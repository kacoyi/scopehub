"""Journal and retire only registered local Herdr assignments."""

from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

if __package__:
    from .worktree import WorktreeError, exclusive_git_lock
else:
    from worktree import WorktreeError, exclusive_git_lock


VERSION = 1
ID = re.compile(r'[a-z0-9][a-z0-9_-]{0,63}')
COMMIT = re.compile(r'[a-f0-9]{40}(?:[a-f0-9]{24})?')
SHA256 = re.compile(r'[a-f0-9]{64}')
RECORD_LIMIT = 64 * 1024
ORIGINAL_LIMIT = 8 * 1024 * 1024
DEFAULT_INVENTORY_LIMIT = 1024


class LifecycleHold(ValueError):
    """A scoped assignment cannot safely proceed; the argument is a public reason code."""


def require_supported_host():
    if sys.platform != 'linux':
        raise LifecycleHold('unsupported_host')


def _matches(pattern, value):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _validate_binding(binding, parent, assignment):
    if (not isinstance(binding, dict)
            or not _matches(ID, assignment)
            or binding.get('id') != assignment
            or binding.get('parent') != parent
            or binding.get('role') not in ('implement', 'review')
            or not _matches(COMMIT, binding.get('source'))
            or not _matches(SHA256, binding.get('prompt_sha256'))
            or any(not isinstance(binding.get(key), str) or not binding[key]
                   for key in ('task', 'checkout', 'model', 'effort'))
            or not Path(binding['checkout']).is_absolute()):
        raise LifecycleHold('invalid_registration')


def _private_dir(path, *, create=False):
    if create:
        if path.parent.resolve(strict=True) != path.parent:
            raise LifecycleHold('unsafe_private_directory')
        path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077
            or path.resolve() != path):
        raise LifecycleHold('unsafe_private_directory')


def _private_read(path, limit):
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    with os.fdopen(os.open(path, flags), 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_nlink != 1):
            raise LifecycleHold('unsafe_private_record')
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise LifecycleHold('oversized_private_record')
    return data


def _sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write(path, data):
    # Flush file and directory before a completed assignment can be ended.
    fd, temporary = tempfile.mkstemp(prefix='.new-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _sync_dir(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _write_record(lane, record):
    try:
        data = json.dumps(record, ensure_ascii=False, allow_nan=False, sort_keys=True).encode('utf-8') + b'\n'
    except (TypeError, ValueError, RecursionError) as error:
        raise LifecycleHold('invalid_private_record') from error
    if len(data) > RECORD_LIMIT:
        raise LifecycleHold('oversized_private_record')
    _atomic_write(lane / 'assignment.json', data)


class Lifecycle:
    """The launcher supplies exact native observations, result proof and retirement."""

    def __init__(self, state_dir, parent, *, observe, retire, probe):
        require_supported_host()
        if not _matches(ID, parent):
            raise LifecycleHold('invalid_owner')
        self.root = Path(os.path.abspath(state_dir))
        self.parent = parent
        self.owner = self.root / parent
        self.pending = self.owner / 'pending'
        self.retired = self.owner / 'retired'
        self.observe = observe
        self.retire = retire
        self.probe = probe

    def _directories(self, *, create=False):
        for path in (self.root, self.owner, self.pending, self.retired):
            # Syncing each newly reachable directory also makes the final archive durable.
            _private_dir(path, create=create)
            if create:
                _sync_dir(path.parent)

    def _read_record(self, lane, assignment):
        try:
            record = json.loads(_private_read(lane / 'assignment.json', RECORD_LIMIT))
        except LifecycleHold:
            raise
        except (ValueError, RecursionError) as error:
            raise LifecycleHold('invalid_registration') from error
        if not isinstance(record, dict) or type(record.get('version')) is not int:
            raise LifecycleHold('invalid_registration')
        if record['version'] != VERSION:
            raise LifecycleHold('unknown_registration_version')
        _validate_binding(record.get('binding'), self.parent, assignment)
        if record.get('phase') not in ('registered', 'archived', 'retiring', 'retired'):
            raise LifecycleHold('invalid_registration')
        return record

    def register(self, binding):
        """Make an exact assignment visible to a later launcher or collector."""
        assignment = binding.get('id') if isinstance(binding, dict) else None
        _validate_binding(binding, self.parent, assignment)
        self._directories(create=True)
        lane = self.pending / assignment
        if (self.retired / assignment).exists():
            raise LifecycleHold('already_retired')
        _private_dir(lane, create=True)
        _sync_dir(self.pending)
        try:
            with exclusive_git_lock(lane / 'operation.lock'):
                if (self.retired / assignment).exists():
                    raise LifecycleHold('already_retired')
                try:
                    existing = self._read_record(lane, assignment)
                except FileNotFoundError:
                    _write_record(lane, {'version': VERSION, 'phase': 'registered', 'binding': binding})
                else:
                    if existing['binding'] != binding:
                        raise LifecycleHold('registration_conflict')
        except BlockingIOError as error:
            raise LifecycleHold('assignment_busy') from error

    def _find(self, assignment):
        for directory in (self.retired, self.pending, self.retired):
            lane = directory / assignment
            try:
                _private_dir(lane)
            except FileNotFoundError:
                continue
            return lane
        return None

    def _pending_assignments(self):
        with os.scandir(self.pending) as entries:
            assignments = []
            for index, entry in enumerate(entries):
                if index >= DEFAULT_INVENTORY_LIMIT:
                    raise LifecycleHold('assignment_inventory_limit')
                if _matches(ID, entry.name):
                    assignments.append(entry.name)
        return sorted(assignments)

    def collect(self, assignment=None, *, dry_run=False):
        """Inspect one registered lane or the bounded pending lanes of this owner."""
        if assignment is not None and not _matches(ID, assignment):
            raise LifecycleHold('invalid_assignment')
        try:
            self._directories()
        except FileNotFoundError:
            assignments = [assignment] if assignment is not None else []
        else:
            assignments = [assignment] if assignment is not None else self._pending_assignments()
        return {
            'version': VERSION, 'parent': self.parent, 'dry_run': dry_run,
            'assignments': [self._collect_one(item, dry_run) for item in assignments],
        }

    def _collect_one(self, assignment, dry_run):
        result = {'assignment': assignment}
        try:
            lane = self._find(assignment)
            if lane is None:
                return dict(result, status='held', reason='unregistered_assignment')
            if dry_run or lane.parent == self.retired:
                return dict(result, **self._advance(lane, assignment, True))
            with exclusive_git_lock(lane / 'operation.lock'):
                return dict(result, **self._advance(lane, assignment, False))
        except BlockingIOError:
            return dict(result, status='held', reason='assignment_busy')
        except (OSError, WorktreeError):
            return dict(result, status='held', reason='assignment_io_unavailable')
        except (KeyError, TypeError):
            return dict(result, status='held', reason='invalid_assignment_input')
        except LifecycleHold as error:
            reason = str(error)
            return dict(result, status='held', reason=reason if _matches(ID, reason) else 'assignment_unavailable')

    def _archive(self, lane, record):
        archive = record.get('archive')
        if (not isinstance(archive, dict) or not isinstance(archive.get('terminal'), dict)
                or not isinstance(archive.get('proof'), dict) or not _matches(SHA256, archive.get('sha256'))
                or type(archive.get('bytes')) is not int or not 0 < archive['bytes'] <= ORIGINAL_LIMIT):
            raise LifecycleHold('archive_unavailable')
        try:
            original = _private_read(lane / 'original.jsonl', ORIGINAL_LIMIT)
        except FileNotFoundError as error:
            raise LifecycleHold('archive_unavailable') from error
        if sha256(original).hexdigest() != archive.get('sha256') or len(original) != archive.get('bytes'):
            raise LifecycleHold('archive_changed')
        return archive

    def _retain(self, lane, original, *, dry_run):
        path = lane / 'original.jsonl'
        try:
            previous = _private_read(path, ORIGINAL_LIMIT)
        except FileNotFoundError:
            if not dry_run:
                _atomic_write(path, original)
        else:
            if previous != original:
                raise LifecycleHold('archive_changed')

    def _advance(self, lane, assignment, dry_run):
        record = self._read_record(lane, assignment)
        binding = record['binding']
        phase = record['phase']
        archive = self._archive(lane, record) if phase != 'registered' else None
        if phase == 'retired':
            if lane.parent == self.pending and not dry_run:
                self._finish(lane)
            return {'status': 'retired', 'original_sha256': archive['sha256']}
        expected = archive['terminal'] if phase == 'retiring' else None
        terminal = self.observe(binding, retiring=expected)
        if terminal is None:
            if phase != 'retiring':
                raise LifecycleHold('terminal_missing')
            if not dry_run:
                self._mark_retired(lane, record)
            return {'status': 'recoverable_retirement' if dry_run else 'retired',
                    'original_sha256': archive['sha256']}
        if expected is not None and terminal == dict(expected, finished=True):
            if not dry_run:
                self.retire(binding, expected, original_sha256=archive['sha256'])
                self._mark_retired(lane, record)
            return {'status': 'recoverable_retirement' if dry_run else 'retired',
                    'original_sha256': archive['sha256']}
        if archive is not None and terminal != archive.get('terminal'):
            raise LifecycleHold('terminal_changed')
        proof = self.probe(binding, terminal)
        if not proof.original or len(proof.original) > ORIGINAL_LIMIT:
            raise LifecycleHold('invalid_original')
        candidate = {'sha256': sha256(proof.original).hexdigest(), 'bytes': len(proof.original),
                     'proof': proof.metadata, 'terminal': terminal}
        if archive is not None and candidate != archive:
            raise LifecycleHold('original_changed')
        if archive is None:
            self._retain(lane, proof.original, dry_run=dry_run)
        if dry_run:
            if self.observe(binding, retiring=expected) != terminal:
                raise LifecycleHold('terminal_changed')
            return {'status': 'eligible', 'original_sha256': candidate['sha256']}
        if archive is None:
            record = dict(record, phase='archived', archive=candidate)
            _write_record(lane, record)
            self._archive(lane, record)
        if self.observe(binding, retiring=expected) != terminal:
            raise LifecycleHold('terminal_changed')
        if phase != 'retiring':
            record = dict(record, phase='retiring')
            _write_record(lane, record)
        self.retire(binding, terminal, original_sha256=candidate['sha256'])
        self._mark_retired(lane, record)
        return {'status': 'retired', 'original_sha256': candidate['sha256']}

    def _mark_retired(self, lane, record):
        _write_record(lane, dict(record, phase='retired'))
        self._finish(lane)

    def _finish(self, lane):
        lane.rename(self.retired / lane.name)
        _sync_dir(self.pending)
        _sync_dir(self.retired)
