import json
import math
from pathlib import Path
from typing import cast

from jsonschema import Draft202012Validator, FormatChecker

from contracts.generated.protocol import ExtendedMessage, Message as Message

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / 'contracts/schemas/protocol.schema.json').read_text(encoding='utf-8'))
POLICY = json.loads((ROOT / 'contracts/generated/runtime.json').read_text(encoding='utf-8'))
MAX_MESSAGE_BYTES: int = POLICY['max_message_bytes']
VALIDATOR = Draft202012Validator(
    {**SCHEMA, '$ref': '#/$defs/Message'}, format_checker=FormatChecker(),
)
_EXTENDED_VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())


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


def decode_line(line: bytes, *, normalize: bool = True) -> Message:
    return cast(Message, _decode(line, VALIDATOR, normalize=normalize))


def decode_extended_line(line: bytes, *, normalize: bool = True) -> ExtendedMessage:
    """Decode the shared message set including opt-in CSV review envelopes."""
    return cast(ExtendedMessage, _decode(line, _EXTENDED_VALIDATOR, normalize=normalize))


def _decode(line: bytes, validator: Draft202012Validator, *, normalize: bool) -> object:
    if len(line) > MAX_MESSAGE_BYTES:
        raise ProtocolError('RESOURCE_LIMIT')
    body = line.removesuffix(b'\n').removesuffix(b'\r')
    if b'\n' in body or b'\r' in body:
        raise ProtocolError('INVALID_ARGUMENT')
    try:
        value = json.loads(body.decode('utf-8'))
        assert_finite(value)
        if not validator.is_valid(value):
            raise ProtocolError('INVALID_ARGUMENT')
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ProtocolError('INVALID_ARGUMENT') from error
    return normalize_integers(value) if normalize else value


def encode_message(message: Message) -> bytes:
    return _encode(message, VALIDATOR)


def encode_extended_message(message: ExtendedMessage) -> bytes:
    return _encode(message, _EXTENDED_VALIDATOR)


def _encode(message: ExtendedMessage, validator: Draft202012Validator) -> bytes:
    try:
        line = (json.dumps(message, allow_nan=False, separators=(',', ':')) + '\n').encode('utf-8')
    except (ValueError, TypeError, RecursionError) as error:
        raise ProtocolError('OUTPUT_INVALID') from error
    _decode(line, validator, normalize=True)
    return line
