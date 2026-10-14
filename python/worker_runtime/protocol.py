import json
import math
from pathlib import Path
from typing import cast

from jsonschema import Draft202012Validator, FormatChecker

from contracts.generated.protocol import Message

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / 'contracts/schemas/protocol.schema.json').read_text())
POLICY = json.loads((ROOT / 'contracts/generated/runtime.json').read_text())
MAX_MESSAGE_BYTES: int = POLICY['max_message_bytes']
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())


class ProtocolError(ValueError):
    pass


def assert_finite(value: object) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ProtocolError('INVALID_ARGUMENT')
    if isinstance(value, dict):
        for child in value.values():
            assert_finite(child)
    elif isinstance(value, list):
        for child in value:
            assert_finite(child)


def decode_line(line: bytes) -> Message:
    if len(line) > MAX_MESSAGE_BYTES:
        raise ProtocolError('RESOURCE_LIMIT')
    body = line.removesuffix(b'\n').removesuffix(b'\r')
    if b'\n' in body or b'\r' in body:
        raise ProtocolError('INVALID_ARGUMENT')
    try:
        value = json.loads(body.decode('utf-8'))
        assert_finite(value)
        if not VALIDATOR.is_valid(value):
            raise ProtocolError('INVALID_ARGUMENT')
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ProtocolError('INVALID_ARGUMENT') from error
    return cast(Message, value)


def encode_message(message: Message) -> bytes:
    try:
        line = (json.dumps(message, allow_nan=False, separators=(',', ':')) + '\n').encode('utf-8')
    except (ValueError, TypeError, RecursionError) as error:
        raise ProtocolError('OUTPUT_INVALID') from error
    decode_line(line)
    return line
