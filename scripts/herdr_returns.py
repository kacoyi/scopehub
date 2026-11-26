"""Verify one native Codex rollout already resolved to an owned Herdr terminal."""

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat

if __package__:
    from .review_acceptance import ReviewError, TRANSCRIPT_LIMIT, assistant_export, compact, final_response, heading_subject
else:
    from review_acceptance import ReviewError, TRANSCRIPT_LIMIT, assistant_export, compact, final_response, heading_subject


ROLLOUT_LIMIT = 128 * 1024 * 1024
LINE_LIMIT = 8 * 1024 * 1024
ROLL_OUT_NAME = re.compile(r'rollout-[^/]+-([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})\.jsonl')
INTERRUPTIONS = {'task_aborted', 'turn_aborted', 'task_interrupted', 'turn_interrupted'}


class ReturnHold(ValueError):
    """Carry a public reason code without evidence contents or private paths."""


@dataclass(frozen=True)
class Proof:
    original: bytes
    metadata: dict


def _digest(value):
    return hashlib.sha256(value).hexdigest()


def _object(value):
    return value if isinstance(value, dict) else {}


def _events(stream):
    consumed, previous = 0, -1
    while raw := stream.readline(LINE_LIMIT + 1):
        consumed += len(raw)
        if len(raw) > LINE_LIMIT or consumed > ROLLOUT_LIMIT:
            raise ReturnHold('original_limit')
        if not raw.endswith(b'\n'):
            raise ReturnHold('original_incomplete')
        try:
            event = json.loads(raw)
        except (ValueError, RecursionError):
            raise ReturnHold('original_invalid') from None
        event = _object(event)
        ordinal = event.get('ordinal')
        if (type(ordinal) is not int or ordinal <= previous or previous == -1 and ordinal != 0
                or not isinstance(event.get('type'), str) or not isinstance(event.get('payload'), dict)):
            raise ReturnHold('original_invalid')
        previous = ordinal
        yield event, raw


def _retained(event):
    metadata = _object(event.get('metadata'))
    if metadata.get('client_authored') is not False:
        return {}, False
    retained = _object(metadata.get('retained_source'))
    identity = _object(retained.get('id'))
    item = event['payload']
    message = identity.get('message_id')
    if (identity.get('role') != item.get('role') or message != item.get('id')
            or not isinstance(message, str) or not message.startswith('msg_')):
        return {}, False
    return identity, retained.get('complete') is True


def _input_digest(payload):
    parts = payload.get('content')
    if not isinstance(parts, list) or len(parts) != 1:
        raise ReturnHold('assignment_mismatch')
    part = _object(parts[0])
    value = part.get('text')
    if part.get('type') != 'input_text' or not isinstance(value, str):
        raise ReturnHold('assignment_mismatch')
    try:
        return _digest(value.encode('utf-8'))
    except UnicodeError:
        raise ReturnHold('assignment_mismatch') from None


def _timestamp(value):
    try:
        instant = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise ReturnHold('original_invalid') from None
    if instant.utcoffset() is None:
        raise ReturnHold('original_invalid')
    return instant


def _verify_header(events, checkout, source, session):
    event, _ = next(events, ({}, b''))
    if event.get('type') != 'session_meta':
        raise ReturnHold('session_mismatch')
    header = event['payload']
    if (header.get('id') != session or header.get('session_id') != session
            or header.get('source') != 'cli' or header.get('originator') != 'codex-tui'
            or header.get('thread_source') != 'user' or header.get('cwd') != checkout
            or _object(header.get('git')).get('commit_hash') != source):
        raise ReturnHold('session_mismatch')


def _verify_turn(events, binding):
    turn = None
    assigned = context_verified = False
    response = completion = None
    for event, raw in events:
        payload = event['payload']
        kind = event.get('type'), payload.get('type')
        if kind[0] == 'session_meta':
            raise ReturnHold('session_mismatch')
        if kind == ('event_msg', 'task_started'):
            started = payload.get('turn_id')
            attribution = _object(payload.get('turn_attribution'))
            if (turn is not None or not isinstance(started, str) or not started
                    or payload.get('root_turn_id') != started or attribution.get('turn_id') != started
                    or attribution.get('root_turn_id') != started or attribution.get('turn_trigger') != 'user'
                    or attribution.get('parent_turn_id') is not None or attribution.get('initiating_agent_path') is not None):
                raise ReturnHold('turn_changed')
            turn = started
        elif kind == ('turn_context', None):
            if (turn is None or completion is not None or payload.get('turn_id') != turn
                    or payload.get('root_turn_id') != turn):
                raise ReturnHold('turn_changed')
            if any(payload.get(field) != binding[expected]
                   for field, expected in (('cwd', 'checkout'), ('model', 'model'), ('effort', 'effort'))):
                raise ReturnHold('assignment_mismatch')
            context_verified = True
        elif kind == ('response_item', 'message') and payload.get('role') == 'user':
            identity, _ = _retained(event)
            # The original includes injected context as user-role items, with no retained user identity.
            if not identity and not _object(event.get('metadata')).get('retained_source'):
                continue
            if not identity or turn is None or identity.get('turn_id') != turn or response is not None:
                raise ReturnHold('turn_changed')
            if not assigned:
                assigned = _input_digest(payload) == binding['prompt_sha256']
                if not assigned:
                    raise ReturnHold('assignment_mismatch')
        elif kind == ('response_item', 'message') and payload.get('phase') == 'final_answer':
            if response is not None or completion is not None:
                raise ReturnHold('original_ambiguous')
            if turn is None or not assigned or not context_verified:
                raise ReturnHold('assignment_mismatch')
            identity, complete = _retained(event)
            if identity.get('turn_id') != turn or not complete:
                raise ReturnHold('original_invalid')
            if len(raw) > TRANSCRIPT_LIMIT:
                raise ReturnHold('original_limit')
            try:
                body, message = assistant_export(raw.decode('utf-8'))
            except (ReviewError, UnicodeError):
                raise ReturnHold('original_invalid') from None
            if not body.strip():
                raise ReturnHold('original_missing')
            response = body, message, raw
        elif kind[0] == 'event_msg' and isinstance(kind[1], str) and kind[1] in INTERRUPTIONS:
            raise ReturnHold('turn_interrupted')
        elif kind == ('event_msg', 'task_complete'):
            if (turn is None or payload.get('turn_id') != turn or payload.get('root_turn_id') != turn):
                raise ReturnHold('turn_changed')
            if completion is not None:
                raise ReturnHold('original_ambiguous')
            if response is None:
                raise ReturnHold('original_missing')
            if payload.get('last_agent_message') != response[0]:
                raise ReturnHold('original_mismatch')
            stamp = event.get('timestamp')
            if _timestamp(stamp) < _timestamp(response[1]['timestamp']):
                raise ReturnHold('original_mismatch')
            completion = {'completed_at': stamp, 'completion_sha256': _digest(raw)}

    if completion is None:
        raise ReturnHold('turn_incomplete')
    _, message, original = response
    return original, {
        **completion, 'turn_id': turn, 'message_id': message['id'],
        'response_timestamp': message['timestamp'], 'original_sha256': _digest(original),
    }


def _review_verdict(original, binding):
    try:
        heading, _, _, _ = final_response(original.decode('utf-8'))
        verdict, source, tasks = heading_subject(compact(heading))
    except (ReviewError, UnicodeError):
        raise ReturnHold('review_verdict_mismatch') from None
    if verdict is None or source != binding['source'] or tasks != [binding['task']]:
        raise ReturnHold('review_verdict_mismatch')
    return verdict


def _version(file_status):
    return (file_status.st_dev, file_status.st_ino, file_status.st_size, file_status.st_mtime_ns,
            file_status.st_ctime_ns)


def probe_return(binding, terminal):
    """Return the exact completed assistant event and private recovery metadata."""
    location = terminal.get('rollout_path')
    path = Path(location) if isinstance(location, (str, Path)) else None
    if path is None:
        raise ReturnHold('original_missing')
    match = ROLL_OUT_NAME.fullmatch(path.name)
    if not path.is_absolute() or match is None:
        raise ReturnHold('session_mismatch')
    session = match[1]
    if terminal.get('session_id', session) != session:
        raise ReturnHold('session_mismatch')

    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0)
    try:
        if path.is_symlink():
            raise ReturnHold('original_invalid')
        with os.fdopen(os.open(path, flags), 'rb') as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or hasattr(os, 'geteuid') and before.st_uid != os.geteuid():
                raise ReturnHold('original_invalid')
            if before.st_size > ROLLOUT_LIMIT:
                raise ReturnHold('original_limit')
            events = _events(stream)
            _verify_header(events, binding['checkout'], binding['source'], session)
            original, metadata = _verify_turn(events, binding)
            if _version(before) != _version(os.fstat(stream.fileno())) or _version(before) != _version(path.lstat()):
                raise ReturnHold('original_changed')
    except FileNotFoundError:
        raise ReturnHold('original_missing') from None
    except OSError:
        raise ReturnHold('original_unavailable') from None

    if binding['role'] == 'review':
        metadata['review_verdict'] = _review_verdict(original, binding)
    return Proof(original, {**metadata, 'session_id': session, 'rollout_path': str(path)})
