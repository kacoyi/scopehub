"""One-shot local import worker over a private, bounded UTF-8 NDJSON pipe.

The Rust host supplies the already authorized file bytes. This worker owns
neither source locations nor the authority to read them.
"""

import base64
from hashlib import sha256
import json
import re
import sys
from time import time_ns
from typing import BinaryIO
from uuid import UUID, uuid4

from contracts.generated.protocol import WorkerHello11
from providers.local_folder import (
    MAX_FILE_BYTES, MAX_PREVIEW_ROWS, MAX_PUBLICATION_RECORDS,
    CsvPublicationLimit, CsvReviewRequired, CsvSourceRowUnavailable,
    check_complete_csv, inspect_folder_csv, preview_csv, read_csv_source_row,
)
from worker_runtime.protocol import MAX_MESSAGE_BYTES, ProtocolError, assert_finite, encode_message


CHUNK_BYTES = 192 * 1024
_MAX_SOURCE_ROW_FRAME_BYTES = 64 * 1024
_HEADER_FIELDS = {'version', 'request_id', 'byte_count', 'sha256', 'mapping'}
_CHUNK_FIELDS = {'version', 'request_id', 'index', 'data'}
_DIGEST = re.compile(r'[0-9a-f]{64}')
_JSON = json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(',', ':'))


class _InvalidFrame(ValueError):
    """A malformed frame with no external diagnostic detail."""


def _object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _InvalidFrame
        result[key] = value
    return result


def _read_object(source: BinaryIO) -> dict[str, object]:
    line = source.readline(MAX_MESSAGE_BYTES + 1)
    if len(line) > MAX_MESSAGE_BYTES or not line.endswith(b'\n'):
        raise _InvalidFrame
    body = line[:-1].removesuffix(b'\r')
    if b'\r' in body:
        raise _InvalidFrame
    try:
        value = json.loads(body.decode('utf-8'), object_pairs_hook=_object_pairs)
        assert_finite(value)
    except (ValueError, RecursionError) as error:
        raise _InvalidFrame from error
    if not isinstance(value, dict):
        raise _InvalidFrame
    return value


def _request_id(value: object) -> str:
    if isinstance(value, str) and len(value) == 36:
        try:
            if str(UUID(value)) == value:
                return value
        except ValueError:
            pass
    raise _InvalidFrame


def _read_content(source: BinaryIO, request_id: str, byte_count: int) -> bytes:
    content = bytearray()
    for index, offset in enumerate(range(0, byte_count, CHUNK_BYTES)):
        frame = _read_object(source)
        version = frame.get('version')
        chunk_index = frame.get('index')
        data = frame.get('data')
        expected_size = min(CHUNK_BYTES, byte_count - offset)
        if (
            frame.keys() != _CHUNK_FIELDS or type(version) is not int or version != 1
            or frame['request_id'] != request_id or type(chunk_index) is not int or chunk_index != index
            or not isinstance(data, str) or len(data) != 4 * ((expected_size + 2) // 3)
        ):
            raise _InvalidFrame
        try:
            chunk = base64.b64decode(data, validate=True)
        except ValueError as error:
            raise _InvalidFrame from error
        if len(chunk) != expected_size or base64.b64encode(chunk).decode('ascii') != data:
            raise _InvalidFrame
        content.extend(chunk)
    if source.read(1):
        raise _InvalidFrame
    return bytes(content)


def _analyze(source: BinaryIO, header: dict[str, object], request_id: str) -> dict[str, object]:
    version = header.get('version')
    byte_count = header.get('byte_count')
    digest = header.get('sha256')
    mapping = header.get('mapping')
    if (
        type(version) is not int or version != 1
        or type(byte_count) is not int or not 0 <= byte_count <= MAX_FILE_BYTES
        or not isinstance(digest, str) or not _DIGEST.fullmatch(digest)
        or not isinstance(mapping, dict)
    ):
        raise _InvalidFrame
    complete = header.keys() == _HEADER_FIELDS | {'complete'} and header['complete'] is True
    inspect_refresh = header.keys() == _HEADER_FIELDS | {'inspect_refresh'} and header['inspect_refresh'] is True
    record_number = header.get('record_number')
    read_row = (
        header.keys() == _HEADER_FIELDS | {'record_number'}
        and type(record_number) is int and 1 <= record_number <= MAX_PUBLICATION_RECORDS
    )
    row_limit = header.get('row_limit')
    if not (complete or inspect_refresh or read_row) and (
        header.keys() != _HEADER_FIELDS | {'row_limit'}
        or type(row_limit) is not int or not 1 <= row_limit <= MAX_PREVIEW_ROWS
    ):
        raise _InvalidFrame
    content = _read_content(source, request_id, byte_count)
    if sha256(content).hexdigest() != digest:
        raise _InvalidFrame
    if complete:
        return {'publication': check_complete_csv(content, mapping)}
    if inspect_refresh:
        return {'inspection': inspect_folder_csv(content, mapping)}
    if read_row:
        assert isinstance(record_number, int)
        return {'source_row': read_csv_source_row(content, mapping, record_number)}
    assert isinstance(row_limit, int)
    return {'preview': preview_csv(content, mapping, row_limit=row_limit)}


def _encode_frame(payload: dict[str, object], *, limit: int = MAX_MESSAGE_BYTES) -> bytes | None:
    line = bytearray()
    for part in _JSON.iterencode(payload):
        chunk = part.encode('utf-8')
        if len(line) + len(chunk) + 1 > limit:
            return None
        line.extend(chunk)
    line.extend(b'\n')
    return bytes(line)


def _write_frame(target: BinaryIO, line: bytes) -> None:
    remaining = memoryview(line)
    while remaining:
        written = target.write(remaining)
        if written is None or not 0 < written <= len(remaining):
            raise OSError('Unable to complete the local import output frame')
        remaining = remaining[written:]
    target.flush()


def _advertise(target: BinaryIO) -> None:
    hello: WorkerHello11 = {
        'protocol_version': '1.1', 'type': 'hello', 'message_id': str(uuid4()),
        'timestamp_ms': time_ns() // 1_000_000, 'role': 'market', 'worker_version': '0.1.0',
        'supported_protocols': ['1.0', '1.1'],
        'capabilities': [
            'market.previewLocalImport', 'market.checkLocalImport', 'market.inspectLocalFolderSource',
            'market.inspectLocalImportSourceRow',
        ],
    }
    _write_frame(target, encode_message(hello))


def run(source: BinaryIO, target: BinaryIO) -> int:
    try:
        _advertise(target)
        header = _read_object(source)
        request_id = _request_id(header.get('request_id'))
    except (_InvalidFrame, OSError, ProtocolError):
        return 2

    envelope: dict[str, object] = {'version': 1, 'request_id': request_id}
    try:
        analysis = _analyze(source, header, request_id)
        limit = _MAX_SOURCE_ROW_FRAME_BYTES if 'source_row' in analysis else MAX_MESSAGE_BYTES
        line = _encode_frame({**envelope, 'ok': True, **analysis}, limit=limit)
        if line is None:
            line = _encode_frame({**envelope, 'ok': False, 'error': 'RESOURCE_LIMIT'})
    except CsvPublicationLimit:
        line = _encode_frame({**envelope, 'ok': False, 'error': 'RESOURCE_LIMIT'})
    except CsvSourceRowUnavailable:
        line = _encode_frame({**envelope, 'ok': False, 'error': 'DATA_UNAVAILABLE'})
    except CsvReviewRequired as error:
        line = _encode_frame({
            **envelope, 'ok': False, 'error': 'REVIEW_REQUIRED', 'issue': error.issue, 'locator': error.locator,
        })
    except (ValueError, RecursionError):
        line = _encode_frame({**envelope, 'ok': False, 'error': 'INVALID_ARGUMENT'})
    except OSError:
        return 2

    if line is None:
        return 2
    try:
        _write_frame(target, line)
    except OSError:
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(run(sys.stdin.buffer, sys.stdout.buffer))
