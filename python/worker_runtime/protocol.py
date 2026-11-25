import json
import math
from pathlib import Path
from typing import cast

from jsonschema import Draft202012Validator, FormatChecker

from contracts.generated.protocol import ExtendedMessage

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / 'contracts/schemas/protocol.schema.json').read_text(encoding='utf-8'))
POLICY = json.loads((ROOT / 'contracts/generated/runtime.json').read_text(encoding='utf-8'))
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


def normalize_integers(value: object) -> object:
    """JSON Schema integers include 1.0 and 1e0; expose them as typed integers."""
    if isinstance(value, float) and value.is_integer() and abs(value) <= 9007199254740991:
        return int(value)
    if isinstance(value, dict):
        return {key: normalize_integers(child) for key, child in value.items()}
    if isinstance(value, list):
        return [normalize_integers(child) for child in value]
    return value


def decode_line(line: bytes, *, normalize: bool = True) -> ExtendedMessage:
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
    return cast(ExtendedMessage, normalize_integers(value) if normalize else value)


def encode_message(message: ExtendedMessage) -> bytes:
    try:
        line = (json.dumps(message, allow_nan=False, separators=(',', ':')) + '\n').encode('utf-8')
    except (ValueError, TypeError, RecursionError) as error:
        raise ProtocolError('OUTPUT_INVALID') from error
    decode_line(line)
    return line
