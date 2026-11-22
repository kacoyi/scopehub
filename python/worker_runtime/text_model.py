"""Private one-shot worker. Only the native caller owns route authorization."""

import json
import sys
from typing import BinaryIO

from ai.ollama import FailureCode, TextModelFailure, complete


MAX_FRAME_BYTES = 512 * 1024


def _read_request(source: BinaryIO) -> tuple[str, dict[str, object]]:
    line = source.readline(MAX_FRAME_BYTES + 1)
    if len(line) > MAX_FRAME_BYTES:
        raise TextModelFailure(FailureCode.RESOURCE_LIMIT)
    invalid = TextModelFailure(FailureCode.INVALID_REQUEST)
    if not line.endswith(b'\n') or source.read(1):
        raise invalid
    try:
        frame = json.loads(line.decode('utf-8'), object_pairs_hook=_unique_fields)
    except (ValueError, RecursionError):
        raise invalid from None
    if (not isinstance(frame, dict) or frame.keys() != {'destination', 'request'}
            or not isinstance(frame['destination'], str) or not isinstance(frame['request'], dict)):
        raise invalid
    return frame['destination'], frame['request']


def _unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise ValueError('Duplicate JSON field')
    return fields


def run(source: BinaryIO, target: BinaryIO) -> int:
    try:
        destination, request = _read_request(source)
        result = complete(destination, request)
        reply = json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode('utf-8') + b'\n'
        target.write(reply)
        target.flush()
    except TextModelFailure as failure:
        return failure.code
    except OSError:
        return FailureCode.UNAVAILABLE
    return 0


if __name__ == '__main__':
    raise SystemExit(run(sys.stdin.buffer, sys.stdout.buffer))
