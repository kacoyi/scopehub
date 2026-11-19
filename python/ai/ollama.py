"""Transport for an authorized Ollama text or selected-chart chat request."""

from enum import IntEnum
from http.client import HTTPConnection, HTTPException, HTTPSConnection
import json
import re
from typing import TypedDict
from urllib.parse import SplitResult, urlsplit


CHAT_PATH = '/api/chat'
MAX_RESPONSE_BYTES = 256 * 1024
MAX_TEXT_BYTES = 64 * 1024
MAX_MODEL_BYTES = 256
MAX_MESSAGES = 9
SOCKET_TIMEOUT_SECONDS = 45
CHART_TOOL = 'read_selected_chart'
CHART_TOOL_SCHEMA = [{
    'type': 'function',
    'function': {
        'name': CHART_TOOL,
        'description': 'Read closing prices and timestamps from the currently selected chart interval.',
        'parameters': {'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False},
    },
}]
CHART_TOOL_CALL: dict[str, object] = {'function': {'name': CHART_TOOL, 'arguments': {}}}
CHART_TOOL_CALL_ID = 'call_scopehub'
NATIVE_CALL_ID = re.compile(r'call_[a-z0-9]{8}')


class FailureCode(IntEnum):
    INVALID_REQUEST = 20
    UNAVAILABLE = 21
    INVALID_RESPONSE = 22
    RESOURCE_LIMIT = 23
    TIMEOUT = 24


class TextModelFailure(Exception):
    def __init__(self, code: FailureCode):
        self.code = code
        super().__init__(code.name)


class TextReply(TypedDict):
    model: str
    text: str


class ToolReply(TypedDict):
    model: str
    tool: str


def _validate_endpoint(destination: str) -> SplitResult:
    invalid = TextModelFailure(FailureCode.INVALID_REQUEST)
    if (not destination.isascii() or any(ord(char) <= 32 or ord(char) == 127 for char in destination)
            or any(char in destination for char in '?#\\%')):
        raise invalid
    try:
        endpoint = urlsplit(destination)
        port = endpoint.port
    except ValueError:
        raise invalid from None
    host = endpoint.hostname
    if (not host or endpoint.username is not None or endpoint.password is not None or port == 0
            or endpoint.path != CHAT_PATH or endpoint.scheme not in ('http', 'https')
            or (endpoint.scheme == 'http' and host not in ('127.0.0.1', '::1'))):
        raise invalid
    authority = f'[{host}]' if ':' in host else host
    if port is not None:
        authority += f':{port}'
    if destination != f'{endpoint.scheme}://{authority}{CHAT_PATH}':
        raise invalid
    return endpoint


def _projected_body(request: dict[str, object]) -> tuple[bytes, bool]:
    invalid = TextModelFailure(FailureCode.INVALID_REQUEST)
    model = request.get('model')
    messages = request.get('messages')
    allowed_keys = {'model', 'messages', 'stream'}
    offers_tool = 'tools' in request
    if offers_tool:
        tools = request['tools']
        # Container equality alone also treats a JSON number 0 as false.
        if (not isinstance(tools, list) or tools != CHART_TOOL_SCHEMA
                or tools[0]['function']['parameters']['additionalProperties'] is not False):
            raise invalid
        allowed_keys.add('tools')
    if (request.keys() != allowed_keys or request['stream'] is not False
            or not isinstance(model, str) or not model or len(model.encode('utf-8')) > MAX_MODEL_BYTES
            or not isinstance(messages, list) or not 1 <= len(messages) <= MAX_MESSAGES + 2 or len(messages) % 2 == 0):
        raise invalid
    history = messages
    last = messages[-1]
    if isinstance(last, dict) and last.get('role') == 'tool':
        content = last.get('content')
        call = CHART_TOOL_CALL
        tool_keys = {'role', 'tool_name', 'content'}
        if 'tool_call_id' in last:
            if last['tool_call_id'] != CHART_TOOL_CALL_ID:
                raise invalid
            tool_keys.add('tool_call_id')
            call = {**call, 'id': CHART_TOOL_CALL_ID}
        assistant_call = {'role': 'assistant', 'content': '', 'tool_calls': [call]}
        if (offers_tool or len(messages) < 3 or messages[-2] != assistant_call
                or last.keys() != tool_keys or last['tool_name'] != CHART_TOOL
                or not isinstance(content, str) or not content.strip()):
            raise invalid
        if len(content.encode('utf-8')) > MAX_TEXT_BYTES:
            raise TextModelFailure(FailureCode.RESOURCE_LIMIT)
        history = messages[:-2]
    if len(history) > MAX_MESSAGES:
        raise invalid
    for index, message in enumerate(history):
        role = 'assistant' if index % 2 else 'user'
        if (not isinstance(message, dict) or message.keys() != {'role', 'content'}
                or message['role'] != role or not isinstance(message['content'], str)
                or not message['content'].strip()):
            raise invalid
    body = json.dumps(request, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    return body, offers_tool


def _is_chart_tool_call(calls: object) -> bool:
    if not isinstance(calls, list) or len(calls) != 1:
        return False
    call = calls[0]
    if (not isinstance(call, dict) or call.keys() - {'function', 'type', 'id'}
            or call.get('type', 'function') != 'function'):
        return False
    if 'id' in call and (not isinstance(call['id'], str) or not NATIVE_CALL_ID.fullmatch(call['id'])):
        return False
    function = call.get('function')
    if not isinstance(function, dict) or function.keys() - {'name', 'arguments', 'index'}:
        return False
    index = function.get('index', 0)
    return (type(index) is int and index == 0 and function.get('name') == CHART_TOOL
            and function.get('arguments') == {})


def _decode_reply(body: bytes, offers_tool: bool) -> TextReply | ToolReply:
    invalid = TextModelFailure(FailureCode.INVALID_RESPONSE)
    try:
        value = json.loads(body.decode('utf-8'), object_pairs_hook=_unique_fields)
        if not isinstance(value, dict) or value.get('done') is not True:
            raise invalid
        model = value.get('model')
        message = value.get('message')
        if (not isinstance(model, str) or not model or any(char.isspace() or not char.isprintable() for char in model)
                or len(model.encode('utf-8')) > MAX_MODEL_BYTES
                or not isinstance(message, dict) or message.get('role') != 'assistant'):
            raise invalid
        calls = message.get('tool_calls')
        if calls is not None and calls != []:
            if not offers_tool or not _is_chart_tool_call(calls):
                raise invalid
            # The trusted caller correlates the only admitted tool with its turn.
            return {'model': model, 'tool': CHART_TOOL}
        content = message.get('content')
        if not isinstance(content, str) or not content.strip():
            raise invalid
        if len(content.encode('utf-8')) > MAX_TEXT_BYTES:
            raise TextModelFailure(FailureCode.RESOURCE_LIMIT)
        return {'model': model, 'text': content}
    except (ValueError, RecursionError):
        raise invalid from None


def _unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise ValueError('Duplicate JSON field')
    return fields


def complete(destination: str, request: dict[str, object]) -> TextReply | ToolReply:
    """Perform one direct POST. The caller owns authorization and the total deadline."""
    endpoint = _validate_endpoint(destination)
    try:
        body, offers_tool = _projected_body(request)
    except (ValueError, RecursionError):
        raise TextModelFailure(FailureCode.INVALID_REQUEST) from None
    host = endpoint.hostname
    assert host is not None
    open_connection = HTTPSConnection if endpoint.scheme == 'https' else HTTPConnection
    # http.client uses the selected authority directly and returns redirects as
    # responses. No ambient proxy, cookies or authorization are installed.
    connection = open_connection(host, endpoint.port, timeout=SOCKET_TIMEOUT_SECONDS)
    try:
        connection.request('POST', CHAT_PATH, body=body, headers={
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Connection': 'close',
        })
        with connection.getresponse() as response:
            if response.status != 200:
                raise TextModelFailure(FailureCode.UNAVAILABLE)
            content_type = response.getheader('Content-Type', '').partition(';')[0].strip().lower()
            if content_type != 'application/json':
                raise TextModelFailure(FailureCode.INVALID_RESPONSE)
            if response.length is not None and response.length > MAX_RESPONSE_BYTES:
                raise TextModelFailure(FailureCode.RESOURCE_LIMIT)
            result = response.read(MAX_RESPONSE_BYTES + 1)
            if len(result) > MAX_RESPONSE_BYTES:
                raise TextModelFailure(FailureCode.RESOURCE_LIMIT)
            if response.length not in (None, 0):
                raise TextModelFailure(FailureCode.INVALID_RESPONSE)
            return _decode_reply(result, offers_tool)
    except TimeoutError:
        raise TextModelFailure(FailureCode.TIMEOUT) from None
    except OSError:
        raise TextModelFailure(FailureCode.UNAVAILABLE) from None
    except HTTPException:
        raise TextModelFailure(FailureCode.INVALID_RESPONSE) from None
    finally:
        connection.close()
