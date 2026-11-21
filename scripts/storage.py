"""Report this repository's Cargo storage. Use --collect to reclaim idle debug intermediates."""

from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
import errno
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time


HIGH_BYTES = 64 * 1024**3
LOW_BYTES = 32 * 1024**3
AUTO_SECONDS = 30
COLD_DEPENDENCY_NS = 24 * 60 * 60 * 10**9
CARGO_TAG = b'Signature: 8a477f597d28d172789f06886806bc55'
CARGO_RUSTC_INFO_MAX = 1024 * 1024
# Leave room for reopened directories, Cargo locks, procfs, and Git subprocess pipes.
COLLECTION_FD_HEADROOM = 16
DESCRIPTOR_LIMIT_ERRORS = {errno.EMFILE, errno.ENFILE}
CARGO_LOCKS = {'.cargo-lock', '.cargo-build-lock', '.cargo-artifact-lock'}
CARGO_DIRECTORY_VARS = ('CARGO_TARGET_DIR', 'CARGO_BUILD_TARGET_DIR', 'CARGO_BUILD_BUILD_DIR')
LOCAL_FILESYSTEMS = {'ext4', 'xfs', 'btrfs', 'f2fs', 'ext3', 'tmpfs'}
LEASE_FILESYSTEMS = {'ext4', 'tmpfs'}
INTERMEDIATE_DIRS = {'.fingerprint', 'deps', 'incremental'}
NON_CACHE_CHILDREN = {'debug', 'release', 'doc', 'tmp', 'package', 'cargo-timings', 'dist', 'reports'}
TARGET_TRIPLE = re.compile(r'[A-Za-z0-9_]+-(?:unknown|pc|apple|linux|uwp|w64|none|sun|nvidia)-.+')
HASHED = r'[A-Za-z0-9_-]+-[0-9a-f]{16}'
DEP_FILE = re.compile(HASHED + r'\.(?:d|o|rlib|rmeta)')
TEST_BINARY = re.compile(r'(?P<target>[A-Za-z0-9_-]+)-(?P<digest>[0-9a-f]{16})')
TEST_FINGERPRINT = re.compile(r'test-(?:integration-test|lib|bin|test)-(?P<target>[A-Za-z0-9_-]+)')
FINGERPRINT_FILE = re.compile(
    r'invoked\.timestamp|output-(?:(?:test|lib|bin|example|bench|build|run)-)[A-Za-z0-9_-]+'
    r'|(?:dep-)?(?:test|lib|bin|example|bench|build|run)-[A-Za-z0-9_-]+(?:\.json)?'
)
INCREMENTAL_CRATE = re.compile(r'[A-Za-z0-9_]+-[a-z0-9]{13}')
INCREMENTAL_SESSION = re.compile(r's-[a-z0-9]+-[a-z0-9]+-[a-z0-9]+')
INCREMENTAL_FILE = re.compile(
    r'[a-z0-9]+\.o|metadata\.rmeta|dep-graph\.bin|query-cache\.bin|work-products\.bin'
)
COMPLETED_SESSION_FILES = {'dep-graph.bin', 'query-cache.bin', 'work-products.bin'}
PARTIAL_SCOPE = 'completed_incremental_sessions_and_leased_dependencies'


class StorageError(RuntimeError):
    pass


class TimeLimit(StorageError):
    pass


class DescriptorLimit(StorageError):
    pass


@dataclass(frozen=True)
class LinuxContext:
    mounts: dict[Path, str]
    active_paths: set[Path]
    active_inodes: set[tuple[int, int]] = field(default_factory=set)
    inaccessible_processes: int = 0


@dataclass(frozen=True)
class DirectoryHandle:
    fd: int
    ctime_ns: int


@dataclass(frozen=True)
class CacheSnapshot:
    path: Path
    root: DirectoryHandle
    outer: DirectoryHandle
    directory: DirectoryHandle
    debug: DirectoryHandle
    mount_id: str
    rustc_info: tuple[int, bytes] | None


def git(root, *args):
    # Git can otherwise inherit the caller's alternate worktree, common dir or index.
    env = {name: value for name, value in os.environ.items() if not name.startswith('GIT_')}
    result = subprocess.run(['git', '--no-optional-locks', *args], cwd=root, env=env,
                            capture_output=True, timeout=5, check=True)
    return os.fsdecode(result.stdout)


def identity(root):
    output = git(root, 'rev-parse', '--path-format=absolute', '--show-toplevel', '--git-common-dir')
    paths = output.removesuffix('\n').split('\n')
    if len(paths) != 2 or any(not Path(path).is_absolute() for path in paths):
        raise StorageError('Unrecognized Git worktree identity')
    return tuple(Path(path).resolve(strict=True) for path in paths)


def worktrees(root):
    listing = git(root, 'worktree', 'list', '--porcelain', '-z')
    for record in listing.split('\0\0'):
        fields = record.split('\0')
        if fields[0].startswith('worktree '):
            yield Path(fields[0].removeprefix('worktree ')), {field.split(' ', 1)[0] for field in fields[1:]}


def dedicated_worktree_directory(current, common, registered, directory):
    # Match worktree.py's admission rule for existing roots. Both helpers also run standalone.
    if (not directory.is_absolute() or directory == directory.parent or
            directory.resolve(strict=True) != directory or directory.is_relative_to(common) or
            any(directory.is_relative_to(path) for path, _ in registered)):
        return False
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or reparse(info):
        return False
    for parent in (directory, *directory.parents):
        marker = parent / '.git'
        try:
            marker_info = marker.lstat()
        except FileNotFoundError:
            continue
        try:
            git(current, 'rev-parse', '--resolve-git-dir', str(marker))
        except subprocess.CalledProcessError as error:
            if error.returncode != 128:
                raise
            # Only a readable, empty directory proves that this marker contains no Git metadata.
            if not stat.S_ISDIR(marker_info.st_mode) or reparse(marker_info):
                return False
            with os.scandir(marker) as entries:
                if next(entries, None) is not None:
                    return False
        else:
            return False
    return True


def managed_worktree_directory(current, common, registered):
    try:
        values = git(current, 'config', '--local', '--null', '--type=path', '--get-all',
                     'scopehub.worktreeRoot').removesuffix('\0').split('\0')
        if len(values) != 1:
            return None
        directory = Path(values[0])
        if dedicated_worktree_directory(current, common, registered, directory):
            return directory
    except (OSError, RuntimeError, subprocess.SubprocessError):
        pass
    return None


def legacy_worktree_directory(current, common, registered):
    directory = registered[0][0].parent
    try:
        if dedicated_worktree_directory(current, common, registered, directory):
            return directory
    except (OSError, RuntimeError, subprocess.SubprocessError):
        pass
    return None


def tracked(root):
    return bool(git(root, 'ls-files', '-z', '--', 'target') or
                git(root, 'ls-tree', '-z', 'HEAD', '--', 'target'))


def configured_directories(current):
    return {(current / os.environ[key]).resolve() for key in CARGO_DIRECTORY_VARS if os.environ.get(key)}


def mount_table():
    mounts = {}
    proc = []
    if sys.platform != 'linux':
        return mounts, proc
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        left, right = line.split(' - ', 1)
        fields = left.split()
        fs = right.split()
        mount = Path(re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), fields[4]))
        mounts[mount] = fs[0] if mount not in mounts else 'stacked'
        if mount == Path('/proc'):
            proc.append((fs[0], fields[3], *fields[5].split(','), *fs[2].split(',')))
    return mounts, proc


def observe_link(link, paths, inodes):
    try:
        value = os.readlink(link)
        if value.startswith('/'):
            paths.add(Path(value.removesuffix(' (deleted)')))
            info = link.stat()
            inodes.add((info.st_dev, info.st_ino))
    except (FileNotFoundError, ProcessLookupError):
        pass
    except PermissionError:
        return False
    return True


def observe_process(process, paths, inodes, deadline):
    visible = observe_link(process / 'cwd', paths, inodes)
    visible = observe_link(process / 'exe', paths, inodes) and visible
    try:
        for argument in (process / 'cmdline').read_bytes().split(b'\0'):
            value = os.fsdecode(argument).split('=', 1)[-1]
            if value.startswith('/'):
                paths.add(Path(os.path.normpath(value)))
    except (FileNotFoundError, ProcessLookupError):
        pass
    except PermissionError:
        visible = False
    try:
        for fd in (process / 'fd').iterdir():
            budget(deadline)
            visible = observe_link(fd, paths, inodes) and visible
    except (FileNotFoundError, ProcessLookupError):
        pass
    except PermissionError:
        visible = False
    try:
        with (process / 'maps').open(errors='surrogateescape') as maps:
            for line in maps:
                budget(deadline)
                fields = line.rstrip('\n').split(None, 5)
                if len(fields) == 6 and fields[5].startswith('/'):
                    paths.add(Path(fields[5].removesuffix(' (deleted)').replace(r'\012', '\n')))
                    major, minor = (int(value, 16) for value in fields[3].split(':'))
                    inodes.add((os.makedev(major, minor), int(fields[4])))
    except (FileNotFoundError, ProcessLookupError):
        pass
    except PermissionError:
        visible = False
    return visible


def linux_context(deadline=None):
    if sys.platform != 'linux':
        raise StorageError('collection requires Linux')
    # Linux reserves these inodes for the initial user and PID namespaces.
    if (Path('/proc/self/ns/user').stat().st_ino != 0xEFFFFFFD or
            Path('/proc/self/ns/pid').stat().st_ino != 0xEFFFFFFC or
            os.readlink('/proc/self') != str(os.getpid())):
        raise StorageError('host process and lock namespaces are unavailable')
    mounts, proc = mount_table()
    if (len(proc) != 1 or proc[0][:2] != ('proc', '/') or any(
            option.startswith(('hidepid=', 'subset=')) and option != 'hidepid=0' for option in proc[0])):
        raise StorageError('procfs does not expose the host process and lock tables')
    for mount in mounts:
        if mount.is_relative_to('/proc'):
            parts = mount.relative_to('/proc').parts
            if parts and (parts[0].isdecimal() or parts[0] in {'self', 'thread-self', 'locks'}):
                raise StorageError('procfs process or lock information is overmounted')
    with Path('/proc/locks').open() as locks:
        locks.read(1)

    active = set()
    inodes = set()
    inaccessible = 0
    for process in Path('/proc').iterdir():
        budget(deadline)
        if not process.name.isdecimal() or process.name == str(os.getpid()):
            continue
        # Any process can hold a cache file. Its owner says nothing about our visibility.
        if not observe_process(process, active, inodes, deadline):
            inaccessible += 1
    return LinuxContext(mounts, active, inodes, inaccessible)


def budget(deadline):
    if deadline is not None and time.monotonic() >= deadline:
        raise TimeLimit('automatic maintenance time limit reached')


def reparse(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def allocated(info):
    # st_blocks is expressed in 512-byte units, including for sparse files.
    return getattr(info, 'st_blocks', 0) * 512


def inventory(path, mounts, deadline, seen, active_inodes=()):
    """Measure the local target without following links or crossing mount points."""
    device = path.parent.stat().st_dev
    pending = [path]
    total = 0
    newest_debug = 0
    protected = None
    debug = path / 'debug'
    debug_found = False
    while pending:
        budget(deadline)
        entry = pending.pop()
        try:
            info = entry.lstat()
        except FileNotFoundError:
            continue
        if entry in mounts or info.st_dev != device or (not mounts and os.path.ismount(entry)):
            protected = protected or 'mount_boundary'
            continue
        if reparse(info):
            protected = protected or 'symlink'
            continue
        if entry.name in {'.git', '.reports'}:
            protected = protected or 'nested_repository_or_evidence'
        if (info.st_dev, info.st_ino) in active_inodes:
            protected = protected or 'active_process'
        if entry.is_relative_to(debug):
            newest_debug = max(newest_debug, info.st_mtime_ns)
        if stat.S_ISDIR(info.st_mode):
            if entry == debug:
                debug_found = True
            pending.extend(entry.iterdir())
        elif info.st_nlink > 1:
            key = (info.st_dev, info.st_ino)
            if key in seen:
                continue
            seen.add(key)
        total += allocated(info)
    return total, newest_debug, protected, debug_found


def lock_names(debug):
    with os.scandir(debug) as entries:
        locks = {entry.name: entry for entry in entries
                 if entry.name.startswith('.cargo-') and entry.name.endswith('-lock')}
        # A partial modern lock set may mean cargo check has never linked artifacts.
        if locks.keys() not in ({'.cargo-lock'}, CARGO_LOCKS):
            raise StorageError('unrecognized Cargo lock layout')
        if any(not regular_lock(entry.stat(follow_symlinks=False)) for entry in locks.values()):
            raise StorageError('unrecognized Cargo lock file')
    return sorted(locks)


def valid_cache_tag(contents):
    return contents.partition(b'\n')[0].rstrip(b'\r') == CARGO_TAG


def cargo_layout(target):
    tag = target / 'CACHEDIR.TAG'
    info = tag.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
        raise StorageError('invalid Cargo cache tag')
    with tag.open('rb') as handle:
        if not valid_cache_tag(handle.readline(4097)):
            raise StorageError('invalid Cargo cache tag')
    debug = target / 'debug'
    if not stat.S_ISDIR(debug.lstat().st_mode):
        raise StorageError('unrecognized Cargo debug profile')
    return lock_names(debug)


def file_matches(entry, pattern):
    return stat.S_ISREG(entry.stat(follow_symlinks=False).st_mode) and pattern.fullmatch(entry.name)


def regular_lock(info):
    return stat.S_ISREG(info.st_mode) and not info.st_size and info.st_nlink == 1


def anchored_directory(stack, name, parent_fd=None, mount_id=None):
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    stack.callback(os.close, fd)
    if mount_id is not None and mount_number(fd) != mount_id:
        raise StorageError('mount changed while inspecting the cache')
    return fd


def mount_number(fd):
    result = re.search(r'^mnt_id:\s+(\d+)$', Path(f'/proc/self/fdinfo/{fd}').read_text(), re.MULTILINE)
    if result is None:
        raise StorageError('could not verify the cache mount')
    return result[1]


def same_entry(name, parent_fd, info):
    item = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    return (item.st_dev, item.st_ino, item.st_mode) == (info.st_dev, info.st_ino, info.st_mode)


def take_lock(stack, fd, name, mount_id):
    import fcntl

    lock = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    stack.callback(os.close, lock)
    info = os.fstat(lock)
    if not regular_lock(info) or info.st_uid != os.geteuid() or mount_number(lock) != mount_id:
        raise StorageError('Cargo lock changed during inspection')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return name, info


def matching_files(fd, pattern, deadline, *, require_all=False):
    with os.scandir(fd) as entries:
        files = []
        for entry in entries:
            budget(deadline)
            if file_matches(entry, pattern):
                files.append((entry.name, entry.stat(follow_symlinks=False)))
            elif require_all:
                return None
    return files


def unlink_files(fd, files, progress, deadline):
    for name, planned in files:
        budget(deadline)
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if (not os.path.samestat(info, planned) or info.st_mode != planned.st_mode or
                info.st_uid != os.geteuid() or info.st_size != planned.st_size or
                info.st_mtime_ns != planned.st_mtime_ns):
            raise StorageError('an intermediate changed during inspection')
        os.unlink(name, dir_fd=fd)
        progress['files'] += 1
        if info.st_nlink == 1:
            progress['released_bytes'] += allocated(info)


def completed_session(files):
    return (files is not None and COMPLETED_SESSION_FILES <= {name for name, _ in files} and
            all(not info.st_mode & 0o111 and info.st_uid == os.geteuid() for _, info in files))


def dependency_files(fd, deadline, *, cold=False):
    cutoff = time.time_ns() - COLD_DEPENDENCY_NS if cold else None
    return [(name, info) for name, info in matching_files(fd, DEP_FILE, deadline)
            if eligible_dependency(info, cutoff)]


def eligible_dependency(info, cutoff, *, executable=False):
    return (stat.S_ISREG(info.st_mode) and bool(info.st_mode & 0o111) == executable and
            info.st_uid == os.geteuid() and info.st_nlink == 1 and
            (cutoff is None or info.st_mtime_ns <= cutoff))


def open_regular(fd, name, *, for_write=False):
    mode = os.O_RDWR if for_write else os.O_RDONLY
    return os.open(name, mode | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)


def read_marker(fd, name, maximum):
    with ExitStack() as stack:
        marker = open_regular(fd, name)
        stack.callback(os.close, marker)
        return marker_contents(marker, maximum)


def marker_contents(fd, maximum):
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_size > maximum:
        return b''
    contents = os.pread(fd, maximum + 1, 0)
    return contents if len(contents) == info.st_size else b''


def cargo_test_marker(fd, group, name):
    try:
        if not re.fullmatch(rb'[0-9a-f]{16}', read_marker(fd, name, 16)):
            return False
        data = json.loads(read_marker(fd, name + '.json', 256 * 1024))
        dependency = os.stat('dep-' + name, dir_fd=fd, follow_symlinks=False)
    except (OSError, ValueError):
        return False
    dep_path = f'debug/.fingerprint/{group}/dep-{name}'
    return (stat.S_ISREG(dependency.st_mode) and dependency.st_size > 0 and
            isinstance(data, dict) and
            all(type(data.get(key)) is int for key in ('rustc', 'target', 'profile', 'path')) and
            all(isinstance(data.get(key), list) for key in ('deps', 'local')) and
            any(isinstance(item, dict) and isinstance(item.get('CheckDepInfo'), dict) and
                item['CheckDepInfo'].get('dep_info') == dep_path for item in data['local']))


def test_fingerprints(debug_fd, mount_id, requested, deadline):
    proof = set()
    with ExitStack() as stack:
        try:
            fingerprints = anchored_directory(stack, '.fingerprint', debug_fd, mount_id)
        except FileNotFoundError:
            return proof
        with os.scandir(fingerprints) as entries:
            for entry in entries:
                budget(deadline)
                if not entry.is_dir(follow_symlinks=False) or not re.fullmatch(HASHED, entry.name):
                    continue
                digest = entry.name.rsplit('-', 1)[1]
                if digest not in requested:
                    continue
                with ExitStack() as group:
                    fd = anchored_directory(group, entry.name, fingerprints, mount_id)
                    for name in os.listdir(fd):
                        marker = TEST_FINGERPRINT.fullmatch(name)
                        if marker:
                            target = marker['target'].replace('-', '_')
                            if target in requested[digest] and cargo_test_marker(fd, entry.name, name):
                                proof.add((digest, target))
    return proof


def elf_executable(fd):
    header = os.pread(fd, 64, 0)
    if (len(header) < 52 or not header.startswith(b'\x7fELF') or
            header[4] not in (1, 2) or header[5] not in (1, 2) or header[6] != 1):
        return False
    entry_end = 28 if header[4] == 1 else 32
    order = 'little' if header[5] == 1 else 'big'
    return (len(header) >= (52 if header[4] == 1 else 64) and
            int.from_bytes(header[16:18], order) in (2, 3) and
            int.from_bytes(header[24:entry_end], order) != 0)


def test_harnesses(deps_fd, debug_fd, mount_id, deadline):
    cutoff = time.time_ns() - COLD_DEPENDENCY_NS
    possible = []
    requested = {}
    for name, info in matching_files(deps_fd, TEST_BINARY, deadline):
        if not eligible_dependency(info, cutoff, executable=True):
            continue
        artifact = TEST_BINARY.fullmatch(name)
        digest, target = artifact['digest'], artifact['target'].replace('-', '_')
        requested.setdefault(digest, set()).add(target)
        possible.append((name, info, digest, target))
    if not possible:
        return []
    proof = test_fingerprints(debug_fd, mount_id, requested, deadline)
    verified = []
    for name, info, digest, target in possible:
        budget(deadline)
        if (digest, target) not in proof:
            continue
        with ExitStack() as stack:
            try:
                fd = open_regular(deps_fd, name)
            except OSError:
                continue
            stack.callback(os.close, fd)
            if os.path.samestat(info, os.fstat(fd)) and elf_executable(fd):
                verified.append((name, info))
    return verified


@contextmanager
def lease_signals():
    # A notification can fall back to SIGIO if the realtime signal queue is full.
    notification = signal.SIGRTMIN
    signals = {notification, signal.SIGIO}
    if (len(list(Path('/proc/self/task').iterdir())) != 1 or
            any(signal.getsignal(number) != signal.SIG_DFL for number in signals)):
        yield None
        return
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, signals)
    try:
        if previous & signals or signal.sigpending() & signals:
            yield None
            return
        try:
            yield notification
        finally:
            # Callers close every lease before unblocking the notifications.
            while signal.sigtimedwait(signals, 0):
                pass
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


def lease_artifact(fd, name, planned, mount_id, notification, *, executable):
    import fcntl

    with ExitStack() as stack:
        try:
            # An open for writing denies execution and returns ETXTBSY for an already
            # executing inode. O_RDWR also lets us verify ELF on that same descriptor.
            file_fd = open_regular(fd, name, for_write=executable)
        except OSError as error:
            if error.errno == errno.ETXTBSY:
                return None
            raise
        stack.callback(os.close, file_fd)
        info = os.fstat(file_fd)
        cutoff = time.time_ns() - COLD_DEPENDENCY_NS
        if (not os.path.samestat(info, planned) or info.st_size != planned.st_size or
                info.st_mtime_ns != planned.st_mtime_ns or
                not eligible_dependency(info, cutoff, executable=executable) or
                mount_number(file_fd) != mount_id or (executable and not elf_executable(file_fd))):
            return None
        fcntl.fcntl(file_fd, fcntl.F_SETOWN, os.getpid())
        fcntl.fcntl(file_fd, fcntl.F_SETSIG, notification)
        try:
            fcntl.fcntl(file_fd, fcntl.F_SETLEASE, fcntl.F_WRLCK)
        except BlockingIOError:
            return None
        item = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if (not os.path.samestat(item, info) or item.st_size != info.st_size or
                item.st_mtime_ns != info.st_mtime_ns or
                not eligible_dependency(item, cutoff, executable=executable) or
                fcntl.fcntl(file_fd, fcntl.F_GETLEASE) != fcntl.F_WRLCK):
            return None
        os.unlink(name, dir_fd=fd)
        return allocated(item)


def remove_leased_files(fd, candidates, mount_id, progress, deadline, *, executable=False):
    if not candidates:
        return
    unsupported = {errno.EACCES, errno.EBADF, errno.EINVAL, errno.ENOLCK, errno.ENOSYS, errno.EOPNOTSUPP, errno.EPERM}
    with lease_signals() as notification:
        if notification is None:
            progress['lease_note'] = 'lease notifications unavailable'
            return
        for name, info in candidates:
            budget(deadline)
            try:
                released = lease_artifact(fd, name, info, mount_id, notification, executable=executable)
            except FileNotFoundError:
                continue
            except PermissionError:
                continue
            except OSError as error:
                if error.errno not in unsupported:
                    raise
                progress['lease_note'] = 'exclusive leases unavailable'
                break
            if released is not None:
                progress['files'] += 1
                field = 'leased_test_harnesses' if executable else 'leased_dependencies'
                progress[field] = progress.get(field, 0) + 1
                progress['released_bytes'] += released


def remove_group(parent_fd, name, pattern, progress, mount_id, deadline, *,
                 completed_only=False, keep_test_markers=False):
    """Only remove groups for which every entry matches this Cargo layout."""
    with ExitStack() as stack:
        fd = anchored_directory(stack, name, parent_fd, mount_id)
        info = os.fstat(fd)
        files = matching_files(fd, pattern, deadline, require_all=True)
        if (files is None or (completed_only and not completed_session(files)) or
                not same_entry(name, parent_fd, info)):
            return
        if keep_test_markers and any(TEST_FINGERPRINT.fullmatch(file) for file, _ in files):
            return
        # Keep a group intact if the time limit has already elapsed.
        budget(deadline)
        unlink_files(fd, files, progress, None)
        if same_entry(name, parent_fd, info):
            os.rmdir(name, dir_fd=parent_fd)
            progress['released_bytes'] += allocated(info)


def incremental_sessions(stack, debug_fd, mount_id, deadline, *, completed_only, preview=False):
    incremental = anchored_directory(stack, 'incremental', debug_fd, mount_id)
    sessions = []
    with os.scandir(incremental) as crates:
        for crate in crates:
            budget(deadline)
            if not (crate.is_dir(follow_symlinks=False) and INCREMENTAL_CRATE.fullmatch(crate.name)):
                continue
            fd = anchored_directory(stack, crate.name, incremental, mount_id)
            with os.scandir(fd) as entries:
                names = [entry.name for entry in entries
                         if entry.is_dir(follow_symlinks=False) and INCREMENTAL_SESSION.fullmatch(entry.name)]
            selected = []
            for name in names:
                budget(deadline)
                size = 0
                if completed_only:
                    with ExitStack() as group:
                        session = anchored_directory(group, name, fd, mount_id)
                        files = matching_files(session, INCREMENTAL_FILE, deadline, require_all=True)
                        if not completed_session(files):
                            continue
                        size = allocated(os.fstat(session)) + sum(allocated(info) for _, info in files
                                                                  if info.st_nlink == 1)
                lock_name = name.rsplit('-', 1)[0] + '.lock'
                if preview:
                    try:
                        info = os.stat(lock_name, dir_fd=fd, follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    if not regular_lock(info) or info.st_uid != os.geteuid():
                        continue
                selected.append((name, size, lock_name))
            # Keep rustc lock inodes in place. A compiler outside Cargo may also hold them.
            if not preview:
                for name in sorted({lock for _, _, lock in selected}):
                    take_lock(stack, fd, name, mount_id)
            sessions.extend((fd, name, size) for name, size, _ in selected)
    return sessions


def remove_intermediates(debug_fd, mount_id, progress, deadline, *, completed_only, lease_supported):
    with ExitStack() as stack:
        layout = set(os.listdir(debug_fd))
        sessions = incremental_sessions(stack, debug_fd, mount_id, deadline,
                                        completed_only=completed_only) if 'incremental' in layout else []
        if 'deps' in layout and (not completed_only or lease_supported):
            deps = anchored_directory(stack, 'deps', debug_fd, mount_id)
            if not completed_only:
                unlink_files(deps, dependency_files(deps, deadline), progress, deadline)
        for crate_fd, session, _ in sessions:
            remove_group(crate_fd, session, INCREMENTAL_FILE, progress, mount_id, deadline,
                         completed_only=completed_only)
        if 'deps' in layout and lease_supported:
            harnesses = test_harnesses(deps, debug_fd, mount_id, deadline)
            remove_leased_files(deps, harnesses, mount_id, progress, deadline, executable=True)
            if completed_only:
                cold_dependencies = dependency_files(deps, deadline, cold=True)
                remove_leased_files(deps, cold_dependencies, mount_id, progress, deadline)
        if not completed_only and '.fingerprint' in layout:
            fingerprints = anchored_directory(stack, '.fingerprint', debug_fd, mount_id)
            with os.scandir(fingerprints) as entries:
                groups = [entry.name for entry in entries
                          if entry.is_dir(follow_symlinks=False) and re.fullmatch(HASHED, entry.name)]
            for name in groups:
                remove_group(fingerprints, name, FINGERPRINT_FILE, progress, mount_id, deadline,
                             keep_test_markers=True)


def preview_partial_cache(target, mounts, deadline):
    debug = target / 'debug'
    if set(cargo_layout(target)) != CARGO_LOCKS:
        return {}
    with ExitStack() as stack:
        fd = anchored_directory(stack, debug)
        mount_id = mount_number(fd)
        entries = set(os.listdir(fd))
        sessions = incremental_sessions(
            stack, fd, mount_id, deadline, completed_only=True, preview=True,
        ) if 'incremental' in entries else []
        dependencies = []
        harnesses = []
        if 'deps' in entries and filesystem(target, mounts) in LEASE_FILESYSTEMS:
            deps = anchored_directory(stack, 'deps', fd, mount_id)
            dependencies = dependency_files(deps, deadline, cold=True)
            harnesses = test_harnesses(deps, fd, mount_id, deadline)
        incremental = sum(size for _, _, size in sessions)
        cold = sum(allocated(info) for _, info in dependencies)
        tests = sum(allocated(info) for _, info in harnesses)
        return {'completed_sessions': len(sessions), 'cold_dependency_files': len(dependencies),
                'estimated_incremental_bytes': incremental, 'estimated_cold_dependency_bytes': cold,
                'cold_test_harnesses': len(harnesses), 'estimated_test_harness_bytes': tests,
                'estimated_candidate_bytes': incremental + cold + tests}


def active_worktree(root, context):
    return any(path.is_relative_to(root) for path in context.active_paths)


def filesystem(path, mounts):
    return next((mounts[parent] for parent in (path, *path.parents) if parent in mounts), None)


def collection_filesystem(root, mounts):
    fs = filesystem(root, mounts)
    return fs if fs in LOCAL_FILESYSTEMS and root not in mounts else None


def child_cache_name(name):
    return not (name.startswith('.') or name in NON_CACHE_CHILDREN or TARGET_TRIPLE.fullmatch(name))


def observe_directory(stack, name, parent_fd=None, mount_id=None):
    fd = anchored_directory(stack, name, parent_fd, mount_id)
    info = os.fstat(fd)
    if info.st_uid != os.geteuid():
        raise StorageError('directory has a different owner')
    return DirectoryHandle(fd, info.st_ctime_ns)


def reserve_collection_descriptors(count):
    import resource

    limit, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
    if limit != resource.RLIM_INFINITY and len(os.listdir('/proc/self/fd')) + count + COLLECTION_FD_HEADROOM > limit:
        raise DescriptorLimit('insufficient descriptors for cache revalidation')


def observe_caches(stack, root, target, mounts, deadline):
    if collection_filesystem(root, mounts) is None:
        return {}, False
    try:
        reserve_collection_descriptors(2)
        worktree = observe_directory(stack, root)
        mount_id = mount_number(worktree.fd)
        outer = observe_directory(stack, 'target', worktree.fd, mount_id)
        with os.scandir(outer.fd) as entries:
            children = sorted(entry.name for entry in entries
                              if child_cache_name(entry.name) and entry.is_dir(follow_symlinks=False))
    except DescriptorLimit:
        return {}, True
    except OSError as error:
        return {}, error.errno in DESCRIPTOR_LIMIT_ERRORS
    except StorageError:
        return {}, False

    observed = {}
    for path in (target, *(target / name for name in children)):
        budget(deadline)
        if any(mount.is_relative_to(path) for mount in mounts):
            continue
        with ExitStack() as handles:
            try:
                reserve_collection_descriptors(1 if path == target else 3)
                directory = outer
                rustc_info = None
                if path != target:
                    directory = observe_directory(handles, path.name, outer.fd, mount_id)
                    marker = open_regular(directory.fd, '.rustc_info.json')
                    handles.callback(os.close, marker)
                    contents = marker_contents(marker, CARGO_RUSTC_INFO_MAX)
                    if not contents or mount_number(marker) != mount_id:
                        continue
                    rustc_info = marker, contents
                debug = observe_directory(handles, 'debug', directory.fd, mount_id)
                if not valid_cache_tag(read_marker(directory.fd, 'CACHEDIR.TAG', 4096)):
                    continue
                lock_names(debug.fd)
            except DescriptorLimit:
                return observed, True
            except OSError as error:
                if error.errno in DESCRIPTOR_LIMIT_ERRORS:
                    return observed, True
                continue
            except StorageError:
                continue
            observed[path] = CacheSnapshot(path, worktree, outer, directory, debug, mount_id, rustc_info)
            stack.enter_context(handles.pop_all())
    return observed, False


def cache_is_current(cache, root):
    # The inventory handles keep the original inodes alive. Resolve from the worktree
    # again and compare live descriptors before using the retained debug handle.
    try:
        with ExitStack() as handles:
            root_fd = anchored_directory(handles, root, mount_id=cache.mount_id)
            outer_fd = anchored_directory(handles, 'target', root_fd, cache.mount_id)
            target_fd = outer_fd
            if cache.rustc_info is not None:
                target_fd = anchored_directory(handles, cache.path.name, outer_fd, cache.mount_id)
            debug_fd = anchored_directory(handles, 'debug', target_fd, cache.mount_id)
            for fd, observed in zip((root_fd, outer_fd, target_fd, debug_fd),
                                    (cache.root, cache.outer, cache.directory, cache.debug)):
                current = os.fstat(fd)
                if (not os.path.samestat(current, os.fstat(observed.fd)) or
                        current.st_ctime_ns != observed.ctime_ns or current.st_uid != os.geteuid()):
                    return False
            if not valid_cache_tag(read_marker(target_fd, 'CACHEDIR.TAG', 4096)):
                return False
            if cache.rustc_info is None:
                return True
            original, contents = cache.rustc_info
            marker = open_regular(target_fd, '.rustc_info.json')
            handles.callback(os.close, marker)
            return (os.path.samestat(os.fstat(marker), os.fstat(original)) and
                    mount_number(marker) == cache.mount_id and
                    marker_contents(marker, CARGO_RUSTC_INFO_MAX) == contents)
    except OSError as error:
        if error.errno in DESCRIPTOR_LIMIT_ERRORS:
            raise
        return False
    except StorageError:
        return False


def collect_target(cache, root, common, context, progress, deadline):
    target = cache.path
    if active_worktree(root, context):
        return 'active_process'
    if any(mount.is_relative_to(target) for mount in context.mounts):
        return 'mount_boundary'
    fs = collection_filesystem(root, context.mounts)
    if fs is None:
        return 'unsupported_filesystem_or_mount'
    with ExitStack() as stack:
        mount_id = cache.mount_id
        debug_fd = cache.debug.fd
        if not cache_is_current(cache, root):
            return 'cache_identity_changed'
        locks = [take_lock(stack, debug_fd, name, mount_id) for name in lock_names(debug_fd)]
        # Recheck registration, tracking, process paths, and directory identities under the locks.
        if identity(root) != (root, common) or tracked(root):
            return 'worktree_or_index_changed'
        registered = list(worktrees(root))
        flags = dict(registered).get(root)
        if flags is None or flags & {'locked', 'bare', 'prunable'}:
            return 'worktree_registration_changed'
        if (root.parent != legacy_worktree_directory(root, common, registered) and
                root.parent != managed_worktree_directory(root, common, registered)):
            return 'worktree_directory_changed'
        refreshed = linux_context(deadline)
        if refreshed.mounts != context.mounts:
            return 'mount_table_changed'
        if active_worktree(root, refreshed):
            return 'active_process'
        if refreshed.active_inodes:
            # A build process can see the same files through a different mount path.
            _, _, boundary, _ = inventory(target, refreshed.mounts, deadline, set(), refreshed.active_inodes)
            if boundary:
                return boundary
        if (any(not same_entry(name, debug_fd, info) for name, info in locks) or
                not cache_is_current(cache, root)):
            return 'cache_identity_changed'
        completed_only = bool(context.inaccessible_processes or refreshed.inaccessible_processes)
        if completed_only and {name for name, _ in locks} != CARGO_LOCKS:
            return 'partial_visibility_requires_all_cargo_locks'
        progress['collection_scope'] = PARTIAL_SCOPE if completed_only else 'debug_intermediates'
        remove_intermediates(debug_fd, mount_id, progress, deadline, completed_only=completed_only,
                             lease_supported=fs in LEASE_FILESYSTEMS)
    return 'collected' if progress['files'] or progress['released_bytes'] else 'no_recognized_intermediates'


def inspect(root, worktree_directories, current, common, flags, mounts, deadline, seen, context, configured, handles):
    target = root / 'target'
    row = {'path': str(target), 'allocated_bytes': None, 'reason': None}
    if not root.is_absolute() or root.parent not in worktree_directories or flags & {'bare', 'prunable'}:
        row['reason'] = 'outside_worktree_directories_or_unavailable'
        return row, [], False
    try:
        root_info = root.lstat()
        if root.resolve(strict=True) != root or reparse(root_info):
            row['reason'] = 'outside_worktree_directories_or_unavailable'
            return row, [], False
        if identity(root) != (root, common):
            row['reason'] = 'different_repository'
            return row, [], False
        target_info = target.lstat()
    except FileNotFoundError:
        return None, [], False
    except OSError as error:
        if error.errno in DESCRIPTOR_LIMIT_ERRORS:
            raise
        row['reason'] = 'worktree_unavailable'
        return row, [], False
    except (RuntimeError, subprocess.SubprocessError):
        row['reason'] = 'worktree_unavailable'
        return row, [], False
    try:
        if not hasattr(target_info, 'st_blocks'):
            row['reason'] = 'allocated_blocks_unavailable'
            return row, [], False
        protected = None
        if root == current:
            protected = 'current_worktree'
        elif any(path.is_relative_to(target) or target.is_relative_to(path) for path in configured):
            protected = 'current_configured_target'
        elif 'locked' in flags:
            protected = 'git_worktree_locked'
        active = context and active_worktree(root, context)
        directory = stat.S_ISDIR(target_info.st_mode) and not reparse(target_info)
        tracked_target = None
        snapshots = {}
        descriptor_limit = False
        if handles is not None and directory and not (protected or active):
            tracked_target = tracked(root)
            if not tracked_target:
                snapshots, descriptor_limit = observe_caches(handles, root, target, mounts, deadline)
        row['allocated_bytes'], newest, boundary, debug_found = inventory(
            target, mounts, deadline, seen, context.active_inodes if context else (),
        )
        if protected:
            row['reason'] = protected
        elif boundary:
            row['reason'] = boundary
        elif active:
            row['reason'] = 'active_process'
        elif tracked_target or (tracked_target is None and tracked(root)):
            row['reason'] = 'tracked_target'
        else:
            row['reason'] = 'unknown_cargo_layout'
            if not directory:
                return row, [], False
            caches = list(child_caches(target, snapshots, mounts, deadline))
            if not debug_found:
                return row, caches, descriptor_limit
            try:
                cargo_layout(target)
                row['retained_paths'] = sorted(
                    [entry.name for entry in target.iterdir() if entry.name != 'debug'] +
                    [f'debug/{entry.name}' for entry in (target / 'debug').iterdir()
                     if entry.name not in INTERMEDIATE_DIRS]
                )
            except OSError as error:
                if error.errno in DESCRIPTOR_LIMIT_ERRORS:
                    raise
                return row, caches, descriptor_limit
            except StorageError:
                return row, caches, descriptor_limit
            row['reason'] = 'candidate'
            caches.append((row, newest, snapshots.get(target)))
            return row, caches, descriptor_limit
        return row, [], False
    except (OSError, subprocess.SubprocessError) as error:
        if isinstance(error, OSError) and error.errno in DESCRIPTOR_LIMIT_ERRORS:
            raise
        row['reason'] = 'inspection_failed'
        return row, [], False


def child_caches(target, snapshots, mounts, deadline):
    # An independently configured target directory has its own rustc metadata.
    # Never search inside an unknown child or a cross-compiled target triple.
    try:
        children = sorted(target.iterdir())
    except OSError as error:
        if error.errno in DESCRIPTOR_LIMIT_ERRORS:
            raise
        return
    for child in children:
        budget(deadline)
        if not child_cache_name(child.name):
            continue
        try:
            child_info = child.lstat()
            if not stat.S_ISDIR(child_info.st_mode) or reparse(child_info):
                continue
            rustc_info = (child / '.rustc_info.json').lstat()
            if not stat.S_ISREG(rustc_info.st_mode) or reparse(rustc_info):
                continue
            cargo_layout(child)
            size, newest, boundary, debug_found = inventory(child, mounts, deadline, set())
        except TimeLimit:
            raise
        except OSError as error:
            if error.errno in DESCRIPTOR_LIMIT_ERRORS:
                raise
            continue
        except StorageError:
            continue
        if not boundary and debug_found:
            row = {'path': str(child), 'included_in': str(target), 'allocated_bytes': size, 'reason': 'candidate'}
            yield row, newest, snapshots.get(child)


def run(root, *, collect=False, high_bytes=HIGH_BYTES, low_bytes=LOW_BYTES, deadline=None):
    if not 0 <= low_bytes < high_bytes:
        raise ValueError('expected 0 <= low watermark < high watermark')
    current, common = identity(root)
    configured = configured_directories(current)
    try:
        context = linux_context(deadline)
        blocked = None
    except (StorageError, OSError, ValueError, IndexError) as error:
        context = None
        blocked = str(error)
    try:
        mounts = context.mounts if context else mount_table()[0]
    except (OSError, ValueError, IndexError) as error:
        mounts = {}
        blocked = str(error)
    with ExitStack() as stack:
        if collect and context:
            import fcntl

            try:
                # Serialize snapshots and collections without modifying Git metadata.
                fd = anchored_directory(stack, common)
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                blocked = 'another repository cache collection is running'
            except OSError as error:
                blocked = str(error)
        return inspect_repository(current, common, configured, context, mounts, blocked,
                                  collect, high_bytes, low_bytes, deadline,
                                  stack if collect and context and not blocked else None)


def inspect_repository(current, common, configured, context, mounts, blocked,
                       collect, high_bytes, low_bytes, deadline, pins):
    registered = list(worktrees(current))
    if not registered or current not in dict(registered):
        raise StorageError('current checkout is missing from Git worktree registration')
    managed = managed_worktree_directory(current, common, registered)
    legacy = legacy_worktree_directory(current, common, registered)
    worktree_directories = {managed, legacy} - {None}
    rows = []
    candidates = []
    descriptor_limited = []
    seen = set()
    total = 0
    incomplete_mounts = sys.platform == 'linux' and not mounts
    partial = context is not None and context.inaccessible_processes > 0
    report = {
        'mode': 'collect' if collect else 'report', 'repository': str(common),
        'current_worktree': str(current), 'high_bytes': high_bytes, 'low_bytes': low_bytes,
        'scope': 'Git-registered targets alongside the primary checkout or directly in the managed worktree root.',
        'managed_worktree_root': str(managed) if managed is not None else None,
        'accounting': 'Allocated blocks with per-inode deduplication. Child caches are included in their parent. Released bytes are estimates.',
        'process_visibility': ('partial' if partial else 'full') if context else 'unavailable',
        'inaccessible_processes': context.inaccessible_processes if context else None,
        'collection_scope': (PARTIAL_SCOPE if partial else 'debug_intermediates') if context else None,
        'targets': rows, 'collected': [], 'collection_available': context is not None and not blocked,
        'collection_blocked': blocked,
        'inventory_complete': not incomplete_mounts and (deadline is None or blocked is None),
    }
    try:
        for path, flags in registered if report['inventory_complete'] else ():
            budget(deadline)
            with ExitStack() as handles:
                row, caches, descriptor_limit = inspect(
                    path, worktree_directories, current, common, flags, mounts, deadline, seen, context, configured,
                    handles if pins is not None else None,
                )
                if row is None:
                    continue
                rows.append(row)
                total += row['allocated_bytes'] or 0
                rows.extend(child for child, _, _ in caches if child is not row)
                before = len(candidates)
                for cache, modified, snapshot in caches:
                    if partial:
                        try:
                            preview = preview_partial_cache(Path(cache['path']), mounts, deadline)
                        except TimeLimit:
                            raise
                        except OSError as error:
                            if error.errno in DESCRIPTOR_LIMIT_ERRORS:
                                cache.update(reason='file_descriptor_limit', detail=str(error))
                                descriptor_limited.append(cache['path'])
                                continue
                            preview = {}
                        except StorageError:
                            preview = {}
                        cache.update(preview)
                        if not any(cache.get(key, 0) for key in (
                                'completed_sessions', 'cold_dependency_files', 'cold_test_harnesses')):
                            cache['reason'] = 'no_partial_visibility_candidates'
                            continue
                    if snapshot is not None:
                        candidates.append((modified, cache['path'], path, cache, snapshot))
                    elif descriptor_limit:
                        cache['reason'] = 'file_descriptor_limit'
                        descriptor_limited.append(cache['path'])
                    elif pins is not None:
                        cache['reason'] = ('unsupported_filesystem_or_mount' if collection_filesystem(path, mounts) is None
                                           else 'cache_identity_unavailable')
                if pins is not None and len(candidates) > before:
                    # Keep the observed worktree and its admitted caches until this run ends.
                    pins.enter_context(handles.pop_all())
    except TimeLimit as error:
        report.update(inventory_complete=False, collection_blocked=str(error))
    except OSError as error:
        if error.errno not in DESCRIPTOR_LIMIT_ERRORS:
            raise
        report.update(inventory_complete=False, collection_blocked=f'Inventory file descriptor limit: {error}')
    remaining = total
    if collect and context and total > high_bytes and report['inventory_complete'] and not blocked:
        for _, _, root, row, snapshot in sorted(candidates):
            if remaining <= low_bytes:
                break
            progress = {'path': row['path'], 'released_bytes': 0, 'files': 0}
            try:
                budget(deadline)
                row['reason'] = collect_target(snapshot, root, common, context, progress, deadline)
            except BlockingIOError:
                row['reason'] = 'cargo_locked'
            except TimeLimit as error:
                report['collection_blocked'] = str(error)
            except OSError as error:
                if error.errno in DESCRIPTOR_LIMIT_ERRORS:
                    row.update(reason='file_descriptor_limit', detail=str(error))
                    descriptor_limited.append(row['path'])
                else:
                    row.update(reason='collection_interrupted', detail=str(error))
            except (StorageError, subprocess.SubprocessError) as error:
                row.update(reason='collection_interrupted', detail=str(error))
            if 'lease_note' in progress:
                row['lease_note'] = progress['lease_note']
            if progress['files'] or progress['released_bytes']:
                report['collected'].append(progress)
                remaining = max(0, remaining - progress['released_bytes'])
            if report['collection_blocked']:
                break
    incomplete = not report['inventory_complete'] or bool(descriptor_limited and total > high_bytes)
    report.update(allocated_bytes=total, estimated_remaining_bytes=remaining,
                  low_watermark_met=remaining <= low_bytes if total > high_bytes else None,
                  descriptor_limited_candidates=descriptor_limited,
                  collection_incomplete=collect and incomplete)
    if partial:
        report['estimated_candidate_bytes'] = sum(row.get('estimated_candidate_bytes', 0) for row in rows)
    return report


def maintain_automatically(root):
    if os.environ.get('SCOPEHUB_STORAGE') == 'off' or os.environ.get('CI') or os.environ.get('GITHUB_ACTIONS'):
        return
    try:
        report = run(root, collect=True, deadline=time.monotonic() + AUTO_SECONDS)
        if (report['collected'] or report['collection_blocked'] or report['collection_incomplete'] or
                report['low_watermark_met'] is False):
            print('ScopeHub storage: ' + json.dumps({key: report[key] for key in (
                'allocated_bytes', 'estimated_remaining_bytes', 'low_watermark_met', 'collected',
                'collection_blocked', 'collection_incomplete', 'descriptor_limited_candidates',
            )}), file=sys.stderr)
    except (OSError, ValueError, StorageError, subprocess.SubprocessError) as error:
        print(f'ScopeHub storage: {error}', file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collect', action='store_true',
                        help='Collect eligible debug intermediates at 64 GiB until usage reaches 32 GiB.')
    args = parser.parse_args()
    try:
        report = run(Path.cwd(), collect=args.collect)
    except (OSError, ValueError, StorageError, subprocess.SubprocessError) as error:
        print(f'ScopeHub storage: {error}', file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return int(args.collect and bool(report['collection_blocked']))


if __name__ == '__main__':
    raise SystemExit(main())
