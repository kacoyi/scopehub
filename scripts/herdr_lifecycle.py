"""Journal and retire only registered local Herdr assignments."""

from contextlib import ExitStack, contextmanager
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
REGISTRATION_VERSION = 2
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


def _record_data(record):
    try:
        data = json.dumps(record, ensure_ascii=False, allow_nan=False, sort_keys=True).encode('utf-8') + b'\n'
    except (TypeError, ValueError, RecursionError) as error:
        raise LifecycleHold('invalid_private_record') from error
    if len(data) > RECORD_LIMIT:
        raise LifecycleHold('oversized_private_record')
    return data


def _write_record(lane, record):
    _atomic_write(lane / 'assignment.json', _record_data(record))


class Lifecycle:
    """Archive verified returns and keep their recovery independent of terminal state."""

    def __init__(self, state_dir, parent, *, observe, probe):
        require_supported_host()
        if not _matches(ID, parent):
            raise LifecycleHold('invalid_owner')
        self.root = Path(os.path.abspath(state_dir))
        self.parent = parent
        self.owner = self.root / parent
        self.pending = self.owner / 'pending'
        self.archived = self.owner / 'archived'
        self.retired = self.owner / 'retired'
        self.observe = observe
        self.probe = probe

    def _directories(self, *, create=False):
        for path in (self.root, self.owner, self.pending, self.retired):
            # Syncing each newly reachable directory also makes the final archive durable.
            _private_dir(path, create=create)
            if create:
                _sync_dir(path.parent)
        if create or self.archived.exists():
            _private_dir(self.archived, create=create)
            if create:
                _sync_dir(self.owner)

    def _read_record(self, lane, assignment):
        try:
            record = json.loads(_private_read(lane / 'assignment.json', RECORD_LIMIT))
        except LifecycleHold:
            raise
        except (ValueError, RecursionError) as error:
            raise LifecycleHold('invalid_registration') from error
        if not isinstance(record, dict) or type(record.get('version')) is not int:
            raise LifecycleHold('invalid_registration')
        if record['version'] not in (1, REGISTRATION_VERSION):
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
        if self._completed(assignment):
            raise LifecycleHold('already_collected')
        _private_dir(lane, create=True)
        _sync_dir(self.pending)
        try:
            with exclusive_git_lock(lane / 'operation.lock'):
                if self._completed(assignment):
                    raise LifecycleHold('already_collected')
                try:
                    existing = self._read_record(lane, assignment)
                except FileNotFoundError:
                    _write_record(lane, {'version': REGISTRATION_VERSION, 'phase': 'registered', 'binding': binding})
                else:
                    if existing['binding'] != binding:
                        raise LifecycleHold('registration_conflict')
        except BlockingIOError as error:
            raise LifecycleHold('assignment_busy') from error

    def _find(self, assignment):
        return self._completed(assignment) or self._lane(self.pending, assignment) or self._completed(assignment)

    def _completed(self, assignment):
        return self._lane(self.archived, assignment) or self._lane(self.retired, assignment)

    @staticmethod
    def _lane(directory, assignment):
        lane = directory / assignment
        try:
            _private_dir(lane)
        except FileNotFoundError:
            return None
        return lane

    def _pending_assignments(self):
        with os.scandir(self.pending) as entries:
            assignments = []
            for index, entry in enumerate(entries):
                if index >= DEFAULT_INVENTORY_LIMIT:
                    raise LifecycleHold('assignment_inventory_limit')
                if _matches(ID, entry.name):
                    assignments.append(entry.name)
        return sorted(assignments)

    def registered_binding(self, assignment):
        if not _matches(ID, assignment) or not (lane := self._lane(self.pending, assignment)):
            raise LifecycleHold('unregistered_assignment')
        return self._read_record(lane, assignment)['binding']

    @contextmanager
    def _assignment_lock(self, lane, assignment):
        with ExitStack() as lock:
            try:
                lock.enter_context(exclusive_git_lock(lane / 'operation.lock'))
            except BlockingIOError as error:
                raise LifecycleHold('assignment_busy') from error
            except FileNotFoundError:
                if lane.parent == self.pending and self._lane(self.archived, assignment):
                    # Collection published the lane between lookup and lock acquisition.
                    raise LifecycleHold('assignment_busy') from None
                raise
            except WorktreeError as error:
                raise LifecycleHold('assignment_io_unavailable') from error
            yield

    def _required_lane(self, assignment):
        if not _matches(ID, assignment):
            raise LifecycleHold('invalid_assignment')
        lane = self._find(assignment)
        if lane is None:
            raise LifecycleHold('unregistered_assignment')
        return lane

    def _owned_record(self, lane, binding):
        record = self._read_record(lane, binding['id'])
        if (binding.get('mode') != 'one-shot' or binding['role'] != 'implement'
                or _record_data(record['binding']) != _record_data(binding)
                or lane.parent == self.retired or record['phase'] not in ('registered', 'archived')
                or lane.parent == self.archived and record['phase'] != 'archived'):
            raise LifecycleHold('assignment_execution_changed')
        return record

    def _require_start_slot(self, lane, record):
        if lane.parent != self.pending or record['phase'] != 'registered':
            raise LifecycleHold('assignment_execution_changed')
        try:
            (lane / 'return').lstat()
        except FileNotFoundError:
            return
        raise LifecycleHold('assignment_execution_changed')

    def prepare_owned(self, binding):
        """Admit a child only for an exact, still unstarted one-shot."""
        assignment = binding['id']
        lane = self._required_lane(assignment)
        with self._assignment_lock(lane, assignment):
            self._require_start_slot(lane, self._owned_record(lane, binding))
            try:
                (lane / 'execution.json').lstat()
            except FileNotFoundError:
                return
            raise LifecycleHold('assignment_execution_changed')

    def save_execution(self, binding, execution):
        """Persist a captured start once, or recognize the identical committed execution."""
        assignment = binding['id']
        lane = self._required_lane(assignment)
        with self._assignment_lock(lane, assignment):
            record = self._owned_record(lane, binding)
            data = _record_data(execution)
            try:
                previous = self._read_execution(lane)
            except LifecycleHold as error:
                if str(error) != 'one_shot_start_unobserved':
                    raise
                if lane.parent != self.pending or record['phase'] != 'registered':
                    raise
                self._require_start_slot(lane, record)
                _atomic_write(lane / 'execution.json', data)
            else:
                if _record_data(previous) != data:
                    raise LifecycleHold('assignment_execution_changed')

    @staticmethod
    def _read_execution(lane):
        try:
            execution = json.loads(_private_read(lane / 'execution.json', RECORD_LIMIT))
        except FileNotFoundError:
            raise LifecycleHold('one_shot_start_unobserved') from None
        except (ValueError, RecursionError):
            raise LifecycleHold('assignment_execution_changed') from None
        if not isinstance(execution, dict):
            raise LifecycleHold('assignment_execution_changed')
        return execution

    def execution(self, assignment):
        return self._read_execution(self._required_lane(assignment))

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

    def complete_owned(self, binding, execution):
        """Commit or recover the return of the runner that observed this exact execution."""
        self._directories()
        result = self._collect_one(binding['id'], False, owned=(binding, execution))
        if result['status'] != 'archived':
            raise LifecycleHold(result['reason'])
        return result['original_sha256']

    def _collect_one(self, assignment, dry_run, *, owned=None):
        result = {'assignment': assignment}
        try:
            lane = self._find(assignment)
            if lane is None:
                return dict(result, status='held', reason='unregistered_assignment')
            if owned is None and (dry_run or lane.parent != self.pending):
                return dict(result, **self._advance(lane, assignment, True))
            with self._assignment_lock(lane, assignment):
                return dict(result, **self._advance(lane, assignment, False, owned=owned))
        except (OSError, WorktreeError):
            return dict(result, status='held', reason='assignment_io_unavailable')
        except (KeyError, TypeError):
            return dict(result, status='held', reason='invalid_assignment_input')
        except LifecycleHold as error:
            reason = str(error)
            return dict(result, status='held', reason=reason if _matches(ID, reason) else 'assignment_unavailable')

    def _archive(self, lane, record):
        directory = lane / 'return'
        try:
            _private_dir(directory)
        except FileNotFoundError:
            if record['phase'] == 'registered':
                return None, None
            # Version 1 collectors retained the reply and a completion digest in the receipt.
            archive, directory = record.get('archive'), lane
        else:
            try:
                archive = json.loads(_private_read(directory / 'archive.json', RECORD_LIMIT))
            except (FileNotFoundError, ValueError, RecursionError) as error:
                raise LifecycleHold('archive_unavailable') from error
            if record['phase'] != 'registered' and archive != record.get('archive'):
                raise LifecycleHold('archive_changed')
        if (not isinstance(archive, dict) or not isinstance(archive.get('terminal'), dict)
                or not isinstance(archive.get('proof'), dict) or not _matches(SHA256, archive.get('sha256'))
                or type(archive.get('bytes')) is not int or not 0 < archive['bytes'] <= ORIGINAL_LIMIT):
            raise LifecycleHold('archive_unavailable')
        try:
            original = _private_read(directory / 'original.jsonl', ORIGINAL_LIMIT)
            if directory != lane:
                completion = _private_read(directory / 'completion.jsonl', ORIGINAL_LIMIT)
                if (not completion or sha256(completion).hexdigest() != archive['proof'].get('completion_sha256')
                        or archive.get('binding_sha256') != self._binding_digest(record['binding'])):
                    raise LifecycleHold('archive_changed')
        except FileNotFoundError as error:
            raise LifecycleHold('archive_unavailable') from error
        if sha256(original).hexdigest() != archive.get('sha256') or len(original) != archive.get('bytes'):
            raise LifecycleHold('archive_changed')
        return archive, directory

    @staticmethod
    def _binding_digest(binding):
        return sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()

    def _retain(self, lane, binding, terminal, proof, *, dry_run):
        if not proof.original or len(proof.original) > ORIGINAL_LIMIT:
            raise LifecycleHold('invalid_original')
        if (not proof.completion or len(proof.completion) > ORIGINAL_LIMIT
                or sha256(proof.completion).hexdigest() != proof.metadata.get('completion_sha256')):
            raise LifecycleHold('invalid_completion')
        # A previous collector may have written the reply before it updated the journal.
        try:
            previous = _private_read(lane / 'original.jsonl', ORIGINAL_LIMIT)
        except FileNotFoundError:
            pass
        else:
            if previous != proof.original:
                raise LifecycleHold('archive_changed')
        archive = {'sha256': sha256(proof.original).hexdigest(), 'bytes': len(proof.original),
                   'proof': proof.metadata, 'terminal': terminal, 'binding_sha256': self._binding_digest(binding)}
        if not dry_run:
            # Commit both raw events together before observing the native state again.
            with tempfile.TemporaryDirectory(prefix='.return-', dir=lane) as staging:
                directory = Path(staging)
                _atomic_write(directory / 'original.jsonl', proof.original)
                _atomic_write(directory / 'completion.jsonl', proof.completion)
                _atomic_write(directory / 'archive.json', _record_data(archive))
                directory.rename(lane / 'return')
                _sync_dir(lane)
        return archive

    def _advance(self, lane, assignment, dry_run, *, owned=None):
        if owned is None:
            record = self._read_record(lane, assignment)
        else:
            expected_binding, execution = owned
            record = self._owned_record(lane, expected_binding)
            if _record_data(self._read_execution(lane)) != _record_data(execution):
                raise LifecycleHold('assignment_execution_changed')
        binding = record['binding']
        if (record['version'] == 1 and record['phase'] != 'retired'
                and not dry_run and lane.parent == self.pending):
            # Older detached collectors reject this version before considering any terminal operation.
            record = dict(record, version=REGISTRATION_VERSION)
            _write_record(lane, record)
        archive, directory = self._archive(lane, record)
        if archive is None:
            terminal = self.observe(binding, retiring=None)
            if terminal is None:
                raise LifecycleHold('terminal_missing')
            if owned is not None and terminal != execution:
                raise LifecycleHold('assignment_execution_changed')
            archive = self._retain(lane, binding, terminal, self.probe(binding, terminal), dry_run=dry_run)
            if dry_run:
                expected = None if binding.get('mode') == 'one-shot' else terminal
                if self.observe(binding, retiring=expected) != terminal:
                    raise LifecycleHold('terminal_changed')
                return {'status': 'eligible', 'original_sha256': archive['sha256']}
            archive, directory = self._archive(lane, record)
        has_completion = directory != lane
        if owned is not None and (not has_completion or _record_data(archive['terminal']) != _record_data(execution) or any(
                archive['proof'].get(key) != execution[key] for key in ('session_id', 'rollout_path'))):
            raise LifecycleHold('archive_execution_changed')
        if not dry_run and lane.parent == self.pending:
            if record['phase'] in ('registered', 'retiring'):
                _write_record(lane, dict(record, phase='archived', archive=archive))
            _private_dir(self.archived, create=True)
            destination = self.archived / lane.name
            lane.rename(destination)
        if not dry_run and (lane.parent == self.pending or owned is not None):
            # The other collector may have stopped just after publishing the directory.
            _sync_dir(self.pending)
            _sync_dir(self.archived)
            _sync_dir(self.owner)
        result = {'status': 'archived', 'original_sha256': archive['sha256']}
        if owned is None:
            result['reason'] = self._archive_disposition(binding, record, archive)
        if has_completion:
            result['completion_sha256'] = archive['proof']['completion_sha256']
        return result

    def _archive_disposition(self, binding, record, archive):
        if record['phase'] == 'retired':
            return 'legacy_retirement_unverified'
        terminal = archive['terminal']
        try:
            current = self.observe(binding, retiring=terminal)
        except LifecycleHold as error:
            reason = str(error)
            return reason if _matches(ID, reason) else 'assignment_unavailable'
        except (OSError, WorktreeError, KeyError, TypeError):
            return 'terminal_unavailable'
        if current is None:
            return 'terminal_missing'
        return 'reusable_terminal' if current == terminal else 'terminal_changed'

    def archive_paths(self, assignment):
        lane = self._find(assignment)
        record = self._read_record(lane, assignment)
        _, directory = self._archive(lane, record)
        paths = {'receipt': str(lane / 'assignment.json'), 'original': str(directory / 'original.jsonl')}
        if directory != lane:
            paths['completion'] = str(directory / 'completion.jsonl')
        return paths
