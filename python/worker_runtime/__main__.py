import argparse
from collections import OrderedDict
import json
import os
import sys
import time
from typing import Literal, cast
from uuid import uuid4

from contracts.generated.protocol import (
    ErrorCode, ErrorResponse, Heartbeat, Hello, MarketHistoryPageError, MarketHistoryPageRequest,
    MarketHistoryPageResponse, Message, Request, Response, WorkerHello11, WorkerRole,
)
from providers.history import HistoryError, execute
from providers.history_pages import execute_page
from worker_runtime.protocol import MAX_MESSAGE_BYTES, POLICY, ProtocolError, decode_line, encode_message

PageReply = MarketHistoryPageResponse | MarketHistoryPageError
CachedReply = Response | PageReply
WireVersion = Literal['1.0', '1.1']


def emit(message: Message) -> None:
    sys.stdout.buffer.write(encode_message(message))
    sys.stdout.buffer.flush()


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def error_text(code: ErrorCode) -> str:
    return {
        'CANCELLED': 'Request cancelled.',
        'PROVIDER_NOT_CONFIGURED': 'The requested provider is not configured.',
        'DATA_UNAVAILABLE': 'No history is available for this instrument.',
        'INVALID_ARGUMENT': 'The request arguments are invalid.',
        'RESOURCE_LIMIT': 'The requested history exceeds the available budget.',
        'SNAPSHOT_UNAVAILABLE': 'The requested immutable snapshot is unavailable.',
        'BUSY': 'The worker is busy.',
    }.get(code, 'Operation is not implemented in this worker.')


def terminal(request_id: str, code: ErrorCode) -> ErrorResponse:
    return {'protocol_version': '1.0', 'message_id': str(uuid4()), 'timestamp_ms': now_ms(),
            'type': 'response', 'request_id': request_id, 'ok': False, 'result': None,
            'error': {'code': code, 'message': error_text(code), 'retryable': False, 'details': {}}}


def page_failure(request_id: str, code: ErrorCode) -> MarketHistoryPageError:
    return {'protocol_version': '1.1', 'message_id': str(uuid4()), 'timestamp_ms': now_ms(),
            'type': 'response', 'request_id': request_id, 'operation': 'market.readHistoryPage',
            'ok': False, 'result': None,
            'error': {'code': code, 'message': error_text(code), 'retryable': code == 'BUSY', 'details': {}}}


def page_reply(request: MarketHistoryPageRequest, enabled: bool) -> PageReply:
    if not enabled:
        return page_failure(request['request_id'], 'UNSUPPORTED_CAPABILITY')
    try:
        result = execute_page(request['payload'], MAX_MESSAGE_BYTES)
    except HistoryError as error:
        return page_failure(request['request_id'], error.code)
    reply: MarketHistoryPageResponse = {
        'protocol_version': '1.1', 'message_id': str(uuid4()), 'timestamp_ms': now_ms(),
        'type': 'response', 'request_id': request['request_id'], 'operation': 'market.readHistoryPage',
        'ok': True, 'result': result, 'error': None,
    }
    return reply


def legacy_reply(request: Request, enabled: bool) -> Response:
    if not enabled:
        return terminal(request['request_id'], 'UNSUPPORTED_CAPABILITY')
    try:
        result = execute(request)
    except HistoryError as error:
        return terminal(request['request_id'], error.code)
    return {'protocol_version': '1.0', 'message_id': str(uuid4()), 'timestamp_ms': now_ms(),
            'type': 'response', 'request_id': request['request_id'], 'ok': True,
            'result': result, 'error': None}


def advertise(role: WorkerRole, demo: bool, version: WireVersion) -> Hello | WorkerHello11:
    if version == '1.0':
        legacy: Hello = {'protocol_version': '1.0', 'message_id': str(uuid4()), 'timestamp_ms': now_ms(),
                         'type': 'hello', 'role': role, 'worker_version': '0.2.0', 'supported_protocols': ['1.0'],
                         'capabilities': ['instrument.search', 'market.getBars'] if demo and role == 'market' else []}
        return legacy
    current: WorkerHello11 = {
        'protocol_version': '1.1', 'message_id': str(uuid4()), 'timestamp_ms': now_ms(),
        'type': 'hello', 'role': role, 'worker_version': '0.2.0', 'supported_protocols': ['1.0', '1.1'],
        'capabilities': ['instrument.search', 'market.getBars', 'market.readHistoryPage'] if demo and role == 'market' else [],
    }
    return current


def run(role: WorkerRole, demo: bool = False, version: WireVersion = '1.0') -> int:
    emit(advertise(role, demo, version))
    enabled = demo and role == 'market'
    # Replayed results are bounded to this read-only session. Durable mutation
    # idempotency and confirmation consumption are core responsibilities.
    completed: OrderedDict[tuple[str, str], tuple[str | None, str | None, CachedReply]] = OrderedDict()
    while line := sys.stdin.buffer.readline(MAX_MESSAGE_BYTES + 1):
        try:
            if not line.endswith(b'\n'):
                raise ProtocolError('INVALID_ARGUMENT')
            message = decode_line(line)
            signature: str | None = None
            page: MarketHistoryPageRequest | None = None
            legacy: Request | None = None
            task_id: str | None = None
            if message['protocol_version'] == '1.1':
                if version != '1.1':
                    raise ProtocolError('PERMISSION_DENIED')
                if message['type'] == 'cancel':
                    request_id, task_id = message['request_id'], message['task_id']
                elif message['type'] == 'request' and message.get('operation') == 'market.readHistoryPage':
                    page = cast(MarketHistoryPageRequest, message)
                    request_id, task_id = page['request_id'], page['task_id']
                    signature = json.dumps([page['operation'], page['payload']], sort_keys=True)
                else:
                    raise ProtocolError('PERMISSION_DENIED')
            elif message['type'] == 'heartbeat':
                heartbeat: Heartbeat = {'protocol_version': '1.0', 'message_id': str(uuid4()),
                                        'timestamp_ms': now_ms(), 'type': 'heartbeat'}
                emit(heartbeat)
                continue
            elif message['type'] == 'request':
                legacy = message
                request_id = message['request_id']
                signature = json.dumps([message['operation'], message['payload']], sort_keys=True)
            elif message['type'] == 'cancel':
                request_id = message['request_id']
            else:
                raise ProtocolError('PERMISSION_DENIED')
            key = (message['protocol_version'], request_id)
            cached = completed.get(key)
            result: CachedReply
            if cached is not None:
                cached_task, old_signature, result = cached
                if message['protocol_version'] == '1.1' and cached_task != task_id:
                    result = page_failure(request_id, 'PERMISSION_DENIED')
                elif old_signature is not None and signature is not None and old_signature != signature:
                    result = page_failure(request_id, 'INVALID_ARGUMENT') if message['protocol_version'] == '1.1' else terminal(request_id, 'INVALID_ARGUMENT')
            else:
                if page:
                    result = page_reply(page, enabled)
                elif legacy:
                    result = legacy_reply(legacy, enabled)
                elif message['protocol_version'] == '1.1':
                    result = page_failure(request_id, 'CANCELLED')
                else:
                    result = terminal(request_id, 'CANCELLED')
                if len(completed) >= POLICY['max_pending_requests']:
                    completed.popitem(last=False)
                completed[key] = (task_id, signature, result)
            emit(result)
        except ProtocolError:
            print(json.dumps({'level':'error','code':'INVALID_PROTOCOL_MESSAGE'}), file=sys.stderr, flush=True)
            return 2
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--role', choices=['market', 'ai', 'indicator'], required=True)
    parser.add_argument('--demo', action='store_true', help='Enable only synthetic market history.')
    parser.add_argument('--wire-version', choices=['1.0', '1.1'], default=os.environ.get('SCOPEHUB_WORKER_WIRE_VERSION', '1.0'))
    args = parser.parse_args()
    if args.wire_version not in ('1.0', '1.1'):
        parser.error('Unsupported worker wire version.')
    if args.demo and args.role != 'market':
        parser.error('--demo requires --role market')
    raise SystemExit(run(cast(WorkerRole, args.role), args.demo, cast(WireVersion, args.wire_version)))


if __name__ == '__main__':
    main()
