import argparse
from collections import OrderedDict
from dataclasses import dataclass
from hashlib import sha256
import json
import os
import sys
from threading import Lock
import time
from typing import Literal, cast
from uuid import uuid4

from contracts.generated.protocol import (
    CancelMemberRequest11, ErrorCode, ErrorResponse, Heartbeat, Hello, MarketHistoryPageError, MarketHistoryPageRequest,
    MarketHistoryPageResponse, MarketSubscriptionError, MarketSubscriptionRequest,
    MarketSubscriptionResponse, Message, Request, Response, WorkerHello11, WorkerRole,
)
from providers.history import HistoryError, execute
from providers.history_pages import execute_page
from providers.replay import plan_replay
from providers.simulated import ReplayObservation
from worker_runtime.protocol import MAX_MESSAGE_BYTES, POLICY, ProtocolError, decode_line, encode_message
from worker_runtime.replay import Replay

PageReply = MarketHistoryPageResponse | MarketHistoryPageError
CachedReply = Response | PageReply | MarketSubscriptionResponse | MarketSubscriptionError
ReadRequest = Request | MarketHistoryPageRequest | MarketSubscriptionRequest
WireVersion = Literal['1.0', '1.1']
Lane = Literal['legacy', 'market.readHistoryPage', 'market.subscribe']
OUTPUT_LOCK = Lock()

@dataclass
class Receipt:
    version: WireVersion
    task_id: str | None
    lane: Lane
    signature: bytes | None
    reply: CachedReply

    def busy(self) -> bool:
        error = self.reply['error']
        return error is not None and error['code'] == 'BUSY'


def emit(message: Message) -> None:
    frame = encode_message(message)
    with OUTPUT_LOCK:
        sys.stdout.buffer.write(frame)
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

def reject(lane: Lane, request_id: str, code: ErrorCode) -> CachedReply:
    if lane == 'legacy':
        return terminal(request_id, code)
    if lane == 'market.readHistoryPage':
        return page_failure(request_id, code)
    return {'protocol_version': '1.1', 'message_id': str(uuid4()), 'timestamp_ms': now_ms(),
            'type': 'response', 'request_id': request_id, 'operation': 'market.subscribe',
            'ok': False, 'result': None,
            'error': {'code': code, 'message': error_text(code), 'retryable': code == 'BUSY', 'details': {}}}

def open_replay(request: MarketSubscriptionRequest, replay: Replay) -> tuple[CachedReply, list[ReplayObservation]]:
    if replay.active_id() is not None:
        return reject('market.subscribe', request['request_id'], 'BUSY'), []
    try:
        result, events = plan_replay(request['payload'])
    except HistoryError as error:
        return reject('market.subscribe', request['request_id'], error.code), []
    reply: MarketSubscriptionResponse = {
        'protocol_version': '1.1', 'message_id': str(uuid4()), 'timestamp_ms': now_ms(),
        'type': 'response', 'request_id': request['request_id'], 'operation': 'market.subscribe',
        'ok': True, 'result': result, 'error': None,
    }
    return reply, events


def page_reply(request: MarketHistoryPageRequest) -> PageReply:
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


def legacy_reply(request: Request) -> Response:
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
        'capabilities': ['instrument.search', 'market.getBars', 'market.readHistoryPage', 'market.subscribe']
        if demo and role == 'market' else [],
    }
    return current


def run(role: WorkerRole, demo: bool = False, version: WireVersion = '1.0') -> int:
    emit(advertise(role, demo, version))
    replay = Replay(emit, lambda: str(uuid4()), now_ms)
    try:
        return serve(demo and role == 'market', version, replay)
    finally:
        replay.close()


def serve(enabled: bool, version: WireVersion, replay: Replay) -> int:
    receipts: OrderedDict[str, Receipt] = OrderedDict()
    while line := sys.stdin.buffer.readline(MAX_MESSAGE_BYTES + 1):
        try:
            if not line.endswith(b'\n'):
                raise ProtocolError('INVALID_ARGUMENT')
            message = decode_line(line)
            if message['type'] == 'heartbeat':
                heartbeat: Heartbeat = {'protocol_version': '1.0', 'message_id': str(uuid4()),
                                        'timestamp_ms': now_ms(), 'type': 'heartbeat'}
                emit(heartbeat)
                continue
            dispatch(message, version, enabled, replay, receipts)
        except ProtocolError:
            print(json.dumps({'level':'error','code':'INVALID_PROTOCOL_MESSAGE'}), file=sys.stderr, flush=True)
            return 2
    return 0


def dispatch(message: Message, version: WireVersion, enabled: bool, replay: Replay,
             receipts: OrderedDict[str, Receipt]) -> None:
    wire = message['protocol_version']
    request: ReadRequest | None = None
    lane: Lane = 'legacy'
    task_id: str | None = None
    if message['protocol_version'] == '1.1':
        if version != '1.1':
            raise ProtocolError('PERMISSION_DENIED')
        lane = 'market.readHistoryPage'
        if message['type'] == 'request' and message.get('operation') == 'market.subscribe':
            lane = 'market.subscribe'
            request = cast(MarketSubscriptionRequest, message)
        elif message['type'] == 'request' and message.get('operation') == 'market.readHistoryPage':
            request = cast(MarketHistoryPageRequest, message)
        elif message['type'] != 'cancel':
            raise ProtocolError('PERMISSION_DENIED')
        member = request if request is not None else cast(CancelMemberRequest11, message)
        request_id, task_id = member['request_id'], member['task_id']
    elif message['type'] == 'request' or message['type'] == 'cancel':
        request_id = message['request_id']
        if message['type'] == 'request':
            request = message
    else:
        raise ProtocolError('PERMISSION_DENIED')

    receipt = receipts.get(request_id)
    if receipt is not None:
        if request is None and wire == receipt.version:
            lane = receipt.lane
        if receipt.version != wire or receipt.task_id != task_id:
            emit(reject(lane, request_id, 'PERMISSION_DENIED'))
            return
    signature = None if request is None else sha256(json.dumps(
        [request['operation'], request['payload']], sort_keys=True).encode()).digest()
    if receipt is not None and receipt.signature is not None and signature is not None and signature != receipt.signature:
        emit(reject(lane, request_id, 'INVALID_ARGUMENT'))
        return

    events: list[ReplayObservation] = []
    if request is None:
        active = replay.cancel(request_id)
        if receipt is not None and not active and not receipt.busy():
            emit(receipt.reply)
            return
        result = reject(lane, request_id, 'CANCELLED')
        if receipt is not None:
            signature = receipt.signature
    elif receipt is not None and not receipt.busy():
        # A cancel may arrive before the request and has no operation field.
        # Adopt the request's lane once, preserving the cancelled outcome.
        if receipt.signature is not None:
            emit(receipt.reply)
            return
        result = receipt.reply if receipt.lane == lane else reject(lane, request_id, 'CANCELLED')
    elif not enabled:
        result = reject(lane, request_id, 'UNSUPPORTED_CAPABILITY')
    elif lane == 'market.subscribe':
        result, events = open_replay(cast(MarketSubscriptionRequest, request), replay)
    elif lane == 'market.readHistoryPage':
        result = page_reply(cast(MarketHistoryPageRequest, request))
    else:
        result = legacy_reply(cast(Request, request))

    if receipt is None and len(receipts) >= POLICY['max_pending_requests']:
        active_id = replay.active_id()
        oldest = next(key for key in receipts if key != active_id)
        del receipts[oldest]
    receipts[request_id] = Receipt(wire, task_id, lane, signature, result)
    emit(result)
    if events:
        replay.start(cast(MarketSubscriptionRequest, request), events)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--role', choices=['market', 'ai', 'indicator'], required=True)
    parser.add_argument('--demo', action='store_true', help='Enable synthetic market history and negotiated replay.')
    parser.add_argument('--wire-version', choices=['1.0', '1.1'], default=os.environ.get('SCOPEHUB_WORKER_WIRE_VERSION', '1.0'))
    args = parser.parse_args()
    if args.wire_version not in ('1.0', '1.1'):
        parser.error('Unsupported worker wire version.')
    if args.demo and args.role != 'market':
        parser.error('--demo requires --role market')
    raise SystemExit(run(cast(WorkerRole, args.role), args.demo, cast(WireVersion, args.wire_version)))


if __name__ == '__main__':
    main()
