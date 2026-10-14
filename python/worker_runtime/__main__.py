import argparse
from collections import OrderedDict
import json
import sys
import time
from typing import cast
from uuid import uuid4

from contracts.generated.protocol import ErrorCode, ErrorResponse, Heartbeat, Hello, Message, WorkerRole
from worker_runtime.protocol import MAX_MESSAGE_BYTES, POLICY, ProtocolError, decode_line, encode_message


def emit(message: Message) -> None:
    sys.stdout.buffer.write(encode_message(message))
    sys.stdout.buffer.flush()


def terminal(request_id: str, code: ErrorCode) -> ErrorResponse:
    return {'protocol_version': '1.0', 'message_id': str(uuid4()), 'timestamp_ms': time.time_ns() // 1_000_000,
            'type': 'response', 'request_id': request_id, 'ok': False, 'result': None,
            'error': {'code': code, 'message': 'Request cancelled.' if code == 'CANCELLED' else 'Operation is not implemented in this worker.',
                      'retryable': False, 'details': {}}}


def run(role: WorkerRole) -> int:
    hello: Hello = {'protocol_version': '1.0', 'message_id': str(uuid4()), 'timestamp_ms': time.time_ns() // 1_000_000,
                    'type': 'hello', 'role': role, 'worker_version': '0.1.0', 'supported_protocols': ['1.0'], 'capabilities': []}
    emit(hello)
    completed: OrderedDict[str, ErrorResponse] = OrderedDict()
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
                if request_id not in completed:
                    if len(completed) >= POLICY['max_pending_requests']:
                        # Retain terminal outcomes within this bounded, side-effect-free M0 session.
                        # Persistent idempotency belongs to the future Rust operation registry.
                        completed.popitem(last=False)
                    completed[request_id] = terminal(request_id, 'CANCELLED' if message['type'] == 'cancel' else 'UNSUPPORTED_CAPABILITY')
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
    args = parser.parse_args()
    raise SystemExit(run(cast(WorkerRole, args.role)))


if __name__ == '__main__':
    main()
