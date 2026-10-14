import argparse
from collections import OrderedDict
import json
import sys
import time
from typing import cast
from uuid import uuid4

from contracts.generated.protocol import ErrorCode, ErrorResponse, Heartbeat, Hello, Message, Response, WorkerRole
from providers.history import HistoryError, execute
from worker_runtime.protocol import MAX_MESSAGE_BYTES, POLICY, ProtocolError, decode_line, encode_message


def emit(message: Message) -> None:
    sys.stdout.buffer.write(encode_message(message))
    sys.stdout.buffer.flush()


def terminal(request_id: str, code: ErrorCode) -> ErrorResponse:
    return {'protocol_version': '1.0', 'message_id': str(uuid4()), 'timestamp_ms': time.time_ns() // 1_000_000,
            'type': 'response', 'request_id': request_id, 'ok': False, 'result': None,
            'error': {'code': code, 'message': {'CANCELLED': 'Request cancelled.',
                                         'PROVIDER_NOT_CONFIGURED': 'The requested provider is not configured.',
                                         'DATA_UNAVAILABLE': 'No history is available for this instrument.',
                                         'INVALID_ARGUMENT': 'The request arguments are invalid.'}.get(code, 'Operation is not implemented in this worker.'),
                      'retryable': False, 'details': {}}}


def run(role: WorkerRole, demo: bool = False) -> int:
    hello: Hello = {'protocol_version': '1.0', 'message_id': str(uuid4()), 'timestamp_ms': time.time_ns() // 1_000_000,
                    'type': 'hello', 'role': role, 'worker_version': '0.1.0', 'supported_protocols': ['1.0'],
                    'capabilities': ['instrument.search', 'market.getBars'] if demo and role == 'market' else []}
    emit(hello)
    completed: OrderedDict[str, Response] = OrderedDict()
    inputs: dict[str, str] = {}
    while line := sys.stdin.buffer.readline(MAX_MESSAGE_BYTES + 1):
        try:
            # A pipe frame must be complete; a partial final line is not a request.
            if not line.endswith(b'\n'):
                raise ProtocolError('INVALID_ARGUMENT')
            message = decode_line(line)
            if message['type'] == 'heartbeat':
                heartbeat: Heartbeat = {'protocol_version': '1.0', 'message_id': str(uuid4()),
                                        'timestamp_ms': time.time_ns() // 1_000_000, 'type': 'heartbeat'}
                emit(heartbeat)
            elif message['type'] in ('request', 'cancel'):
                request_id = message['request_id']
                signature = json.dumps([message['operation'], message['payload']], sort_keys=True) if message['type'] == 'request' else None
                if signature is not None and request_id in inputs and inputs[request_id] != signature:
                    emit(terminal(request_id, 'INVALID_ARGUMENT'))
                    continue
                if request_id not in completed:
                    if len(completed) >= POLICY['max_pending_requests']:
                        # Retain terminal outcomes within this bounded, side-effect-free M0 session.
                        # Persistent idempotency belongs to the future Rust operation registry.
                        evicted, _ = completed.popitem(last=False)
                        inputs.pop(evicted, None)
                    if message['type'] == 'cancel':
                        completed[request_id] = terminal(request_id, 'CANCELLED')
                    elif demo and role == 'market':
                        try:
                            result = execute(message)
                            completed[request_id] = {
                                'protocol_version': '1.0', 'message_id': str(uuid4()),
                                'timestamp_ms': time.time_ns() // 1_000_000, 'type': 'response',
                                'request_id': request_id, 'ok': True, 'result': result, 'error': None,
                            }
                        except HistoryError as error:
                            completed[request_id] = terminal(request_id, error.code)
                    else:
                        completed[request_id] = terminal(request_id, 'UNSUPPORTED_CAPABILITY')
                    if signature is not None:
                        inputs[request_id] = signature
                emit(completed[request_id])
            else:
                raise ProtocolError('PERMISSION_DENIED')
        except ProtocolError:
            # Never echo invalid input, prompts, source code or environment values.
            print(json.dumps({'level': 'error', 'code': 'INVALID_PROTOCOL_MESSAGE'}), file=sys.stderr, flush=True)
            return 2
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--role', choices=['market', 'ai', 'indicator'], required=True)
    parser.add_argument('--demo', action='store_true', help='Enable only the synthetic market history reader.')
    args = parser.parse_args()
    if args.demo and args.role != 'market':
        parser.error('--demo requires --role market')
    raise SystemExit(run(cast(WorkerRole, args.role), args.demo))


if __name__ == '__main__':
    main()
