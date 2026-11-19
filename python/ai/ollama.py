"""Text-only transport for an already authorized Ollama chat request."""

from enum import IntEnum
from http.client import HTTPConnection, HTTPException, HTTPSConnection
import json
from typing import TypedDict
from urllib.parse import SplitResult, urlsplit


CHAT_PATH = '/api/chat'
MAX_RESPONSE_BYTES = 256 * 1024
MAX_TEXT_BYTES = 64 * 1024
MAX_MODEL_BYTES = 256
MAX_MESSAGES = 9
SOCKET_TIMEOUT_SECONDS = 45


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


def _projected_body(request: dict[str, object]) -> bytes:
    invalid = TextModelFailure(FailureCode.INVALID_REQUEST)
    model = request.get('model')
    messages = request.get('messages')
    if (request.keys() != {'model', 'messages', 'stream'} or request['stream'] is not False
            or not isinstance(model, str) or not model or len(model.encode('utf-8')) > MAX_MODEL_BYTES
            or not isinstance(messages, list) or not 1 <= len(messages) <= MAX_MESSAGES or len(messages) % 2 == 0):
        raise invalid
    for index, message in enumerate(messages):
        role = 'assistant' if index % 2 else 'user'
        if (not isinstance(message, dict) or message.keys() != {'role', 'content'}
                or message['role'] != role or not isinstance(message['content'], str)
                or not message['content'].strip()):
            raise invalid
    return json.dumps(request, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')


def _decode_reply(body: bytes) -> TextReply:
    invalid = TextModelFailure(FailureCode.INVALID_RESPONSE)
    try:
        value = json.loads(body.decode('utf-8'), object_pairs_hook=_unique_fields)
        if not isinstance(value, dict) or value.get('done') is not True:
            raise invalid
        model = value.get('model')
        message = value.get('message')
        if (not isinstance(model, str) or not model or any(char.isspace() or not char.isprintable() for char in model)
                or len(model.encode('utf-8')) > MAX_MODEL_BYTES
                or not isinstance(message, dict) or message.get('role') != 'assistant' or message.get('tool_calls')):
            raise invalid
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


def complete(destination: str, request: dict[str, object]) -> TextReply:
    """Perform one direct POST. The caller owns authorization and the total deadline."""
    endpoint = _validate_endpoint(destination)
    try:
        body = _projected_body(request)
    except UnicodeError:
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
            return _decode_reply(result)
    except TimeoutError:
        raise TextModelFailure(FailureCode.TIMEOUT) from None
    except OSError:
        raise TextModelFailure(FailureCode.UNAVAILABLE) from None
    except HTTPException:
        raise TextModelFailure(FailureCode.INVALID_RESPONSE) from None
    finally:
        connection.close()
