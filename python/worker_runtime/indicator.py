"""Private, single-computation worker. Authority is supplied by the native caller."""

from hashlib import sha256
import json
import os
import sys
import time
from typing import BinaryIO, cast
from uuid import uuid4

from contracts.generated.protocol import IndicatorCompute11, IndicatorProvenance, WorkerHello11
from indicators.trusted import evaluate
from worker_runtime.protocol import MAX_MESSAGE_BYTES, ProtocolError, decode_line, encode_message

ENGINE = 'scopehub-trusted-python-1'
LIMITS = {'series': 16, 'markers': 2000, 'regions': 500, 'diagnostics': 256}
ENCODER = json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(',', ':'))

# Failure exit statuses carry no code, input, paths, output or exception text.
INVALID_REQUEST = 20
COMPUTATION_FAILED = 21
INVALID_OUTPUT = 22
RESOURCE_LIMIT = 23


def envelope() -> dict[str, str | int]:
    return {'protocol_version': '1.1', 'message_id': str(uuid4()),
            'timestamp_ms': time.time_ns() // 1_000_000}


def read_request(stream: BinaryIO) -> IndicatorCompute11:
    line = stream.readline(MAX_MESSAGE_BYTES + 1)
    if len(line) > MAX_MESSAGE_BYTES:
        raise ProtocolError('RESOURCE_LIMIT')
    if not line.endswith(b'\n') or stream.read(1):
        raise ProtocolError('INVALID_ARGUMENT')
    # Keep the exact numeric types in opaque resolved parameters.
    message = decode_line(line, normalize=False)
    if (message['protocol_version'] != '1.1' or message['type'] != 'request'
            or message.get('operation') != 'indicator.compute'):
        raise ProtocolError('INVALID_ARGUMENT')
    request = cast(IndicatorCompute11, message)
    source = request['source']
    if source['language'] != 'python' or request['input']['mode'] != 'inline':
        raise ProtocolError('INVALID_ARGUMENT')
    if sha256(source['source'].encode('utf-8')).hexdigest() != request['code_hash']:
        raise ProtocolError('INVALID_ARGUMENT')
    return request


def response(request: IndicatorCompute11, output: object) -> bytes:
    snapshot = request['snapshot']
    provenance: IndicatorProvenance = {
        'code_hash': request['code_hash'], 'params_hash': request['params_hash'],
        'dataset_id': snapshot['dataset_id'], 'dataset_revision': snapshot['dataset_revision'],
        'engine_version': ENGINE,
    }
    if not isinstance(output, dict) or set(output) - {'provenance', *LIMITS}:
        raise ProtocolError('OUTPUT_INVALID')
    if output.get('provenance', provenance) != provenance:
        raise ProtocolError('OUTPUT_INVALID')
    for key, limit in LIMITS.items():
        values = output.get(key)
        if not isinstance(values, list):
            raise ProtocolError('OUTPUT_INVALID')
        if len(values) > limit:
            raise ProtocolError('RESOURCE_LIMIT')
    for series in output['series']:
        if isinstance(series, dict) and isinstance(series.get('points'), list):
            if len(series['points']) > 100000:
                raise ProtocolError('RESOURCE_LIMIT')

    header = {**envelope(), 'type': 'response', 'operation': 'indicator.compute',
              'request_id': request['request_id'], 'task_id': request['task_id'],
              'draft_id': request['draft_id'], 'snapshot_hash': snapshot['content_hash']}
    prefix = ENCODER.encode(header).encode('utf-8')[:-1]
    prefix += b',"artifact":{"storage":"inline","result_hash":"'
    separator = b'","result":'
    suffix = b'}}\n'
    available = MAX_MESSAGE_BYTES - len(prefix) - 64 - len(separator) - len(suffix)
    result = bytearray()
    for chunk in ENCODER.iterencode({**output, 'provenance': provenance}):
        encoded = chunk.encode('utf-8')
        if len(result) + len(encoded) > available:
            raise ProtocolError('RESOURCE_LIMIT')
        result.extend(encoded)
    # The native host verifies this digest of the exact transmitted result
    # bytes, then computes the canonical Rust JSON digest for its caller.
    frame = prefix + sha256(result).hexdigest().encode('ascii') + separator + result + suffix
    decode_line(frame, normalize=False)
    return frame


def serve(wire: BinaryIO) -> int:
    hello = cast(WorkerHello11, {
        **envelope(), 'type': 'hello', 'role': 'indicator', 'worker_version': ENGINE,
        'supported_protocols': ['1.0', '1.1'], 'capabilities': ['indicator.compute'],
    })
    wire.write(encode_message(hello))
    wire.flush()
    try:
        request = read_request(sys.stdin.buffer)
    except (ProtocolError, UnicodeError) as error:
        return RESOURCE_LIMIT if str(error) == 'RESOURCE_LIMIT' else INVALID_REQUEST
    source, inputs = request['source'], request['input']
    assert source['language'] == 'python' and inputs['mode'] == 'inline'
    try:
        output = evaluate(source['source'], inputs['bars'], request['resolved_params'])
    except BaseException:
        return COMPUTATION_FAILED
    try:
        frame = response(request, output)
    except ProtocolError as error:
        return RESOURCE_LIMIT if str(error) == 'RESOURCE_LIMIT' else INVALID_OUTPUT
    except BaseException:
        return INVALID_OUTPUT
    wire.write(frame)
    wire.flush()
    return 0


def main() -> int:
    # Keep ordinary Python output and native writes to stdout off the wire.
    # This is framing hygiene for trusted code with normal local authority.
    with os.fdopen(os.dup(sys.stdout.fileno()), 'wb') as wire, open(os.devnull, 'wb') as devnull:
        os.dup2(devnull.fileno(), sys.stdout.fileno())
        return serve(wire)


if __name__ == '__main__':
    raise SystemExit(main())
