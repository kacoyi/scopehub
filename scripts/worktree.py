"""Create and locate native Git worktrees in ScopeHub's managed worktree root."""

from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager, nullcontext
from dataclasses import dataclass
import errno
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys


ROOT_CONFIG = 'scopehub.worktreeRoot'
CREATION_LOCK = 'scopehub-worktree-create.lock'
DEFAULT_RETIREMENT_ENTRY_LIMIT = 20_000
MAX_RETIREMENT_ENTRY_LIMIT = 200_000
PORTABLE_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*')
RESERVED_NAMES = {'AUX', 'CON', 'NUL', 'PRN', *(f'{prefix}{n}' for prefix in ('COM', 'LPT') for n in range(10))}


class WorktreeError(RuntimeError):
    pass


@dataclass(frozen=True)
class Registration:
    path: Path
    branch: str | None
    flags: frozenset[str]
    lock_reason: str | None
    head: str | None = None


def git_environment():
    # In particular, ignore an inherited alternate worktree, index or config.
    return {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}


def git(cwd, *args, allowed=(0,), lock_fd=None, input=None):
    env = git_environment()
    command = ['git', *args]
    options = dict(cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if lock_fd is None:
        result = subprocess.run(command, input=input, stdin=subprocess.DEVNULL if input is None else None, **options)
    else:
        env['LC_ALL'] = 'C'  # Git records its initialization lock reason in the child's locale.
        # This child retains the lock through Git's exit if the original CLI is killed.
        # Git and its hooks start without the lock handle.
        runner = (
            'import subprocess, sys\n'
            'sys.exit(subprocess.call(sys.argv[1:], stdin=sys.stdin, stdout=sys.stdout, '
            'stderr=sys.stderr, close_fds=True))'
        )
        with inherit_lock(lock_fd) as inheritance:
            # subprocess.run would kill the lock holder on Python-side interruption.
            with subprocess.Popen([sys.executable, '-I', '-S', '-c', runner, *command],
                                  stdin=subprocess.DEVNULL if input is None else subprocess.PIPE,
                                  **inheritance, **options) as process:
                stdout, stderr = process.communicate(input)
                result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    if result.returncode not in allowed:
        detail = os.fsdecode(result.stderr).strip()
        raise WorktreeError(detail or f'git {args[0]} exited with status {result.returncode}')
    return result


def git_path(cwd, option):
    output = git(cwd, 'rev-parse', '--path-format=absolute', option).stdout
    return Path(os.fsdecode(output).removesuffix('\n')).resolve(strict=True)


def checkout_paths(cwd):
    options = ('--show-toplevel', '--git-dir', '--git-common-dir')
    output = git(cwd, 'rev-parse', '--path-format=absolute', *options).stdout
    paths = os.fsdecode(output).removesuffix('\n').split('\n')
    if len(paths) != len(options):
        # POSIX allows newlines in paths. A single result has an unambiguous boundary.
        return tuple(git_path(cwd, option) for option in options)
    return tuple(Path(path).resolve(strict=True) for path in paths)


def registrations(cwd):
    records = os.fsdecode(git(cwd, 'worktree', 'list', '--porcelain', '-z').stdout)
    for record in records.split('\0\0'):
        fields = dict(field.partition(' ')[::2] for field in record.split('\0') if field)
        if 'worktree' in fields:
            yield Registration(Path(fields['worktree']), fields.get('branch'), frozenset(fields), fields.get('locked'),
                               fields.get('HEAD'))


def entry_info(path):
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def reparse_point(info):
    return bool(getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def regular_file(path):
    info = entry_info(path)
    return info is not None and stat.S_ISREG(info.st_mode) and not reparse_point(info)


def has_git_metadata(cwd, directory):
    marker = directory / '.git'
    info = entry_info(marker)
    if info is None:
        return False
    if git(cwd, 'rev-parse', '--resolve-git-dir', str(marker), allowed=(0, 128)).returncode == 0:
        return True
    # Git uses the same exit code for an empty directory and unavailable repository metadata.
    if stat.S_ISDIR(info.st_mode) and not reparse_point(info):
        with os.scandir(marker) as entries:
            if next(entries, None) is None:
                return False
    raise WorktreeError(f'Cannot verify ancestor Git marker {marker}. Inspect its repository before using this root.')


def managed_root(cwd, common, registered):
    result = git(cwd, 'config', '--local', '--null', '--type=path', '--get-all', ROOT_CONFIG, allowed=(0, 1))
    if result.returncode == 1:
        root = Path.home().resolve() / 'worktrees' / 'scopehub'
    else:
        values = os.fsdecode(result.stdout).removesuffix('\0').split('\0')
        if len(values) != 1 or not Path(values[0]).is_absolute():
            raise WorktreeError(f'Set one absolute repository-local {ROOT_CONFIG} with git config --local.')
        root = Path(values[0])
    if root.resolve() != root or root == root.parent:
        raise WorktreeError(f'{ROOT_CONFIG} must name a dedicated absolute directory without symlinks: {root}')
    info = entry_info(root)
    if info is not None and (not stat.S_ISDIR(info.st_mode) or reparse_point(info)):
        raise WorktreeError(f'Managed worktree root is not a plain directory: {root}')
    # storage.py applies the same admission rule to existing roots. Both helpers also run standalone.
    if (root.is_relative_to(common) or any(root.is_relative_to(entry.path) for entry in registered)
            or any(has_git_metadata(cwd, parent) for parent in (root, *root.parents))):
        raise WorktreeError(f'Managed worktree root must be outside every Git checkout and its metadata: {root}')
    return root


def destination(root, name):
    if (not PORTABLE_NAME.fullmatch(name) or name.endswith('.')
            or name.partition('.')[0].upper() in RESERVED_NAMES):
        raise WorktreeError('Supply a single portable worktree name using letters, digits, dots, dashes or underscores.')
    path = root / name
    if path.resolve() != path:
        raise WorktreeError(f'Worktree path must not contain a symlink: {path}')
    return path


def registered_checkout(registered, path):
    matches = [entry for entry in registered if entry.path == path]
    if len(matches) > 1:
        raise WorktreeError(f'Ambiguous Git worktree registration at {path}. Inspect git worktree list --porcelain.')
    return matches[0] if matches else None


def verify_checkout(common, entry):
    if entry.lock_reason == 'initializing':
        raise WorktreeError(
            f'Git has not completed checkout at {entry.path} (locked: initializing). '
            'Wait for Git to exit and inspect git worktree list --porcelain before retrying. '
            'Use another name and branch if the incomplete checkout must be preserved.'
        )
    path = entry.path
    marker = path / '.git'
    if entry.flags & {'bare', 'prunable'} or path.resolve() != path or not regular_file(marker):
        raise WorktreeError(f'Git worktree is unavailable at {entry.path}. Inspect git worktree list --porcelain.')
    top, git_dir, common_dir = checkout_paths(path)
    if top != path or common_dir != common or git_dir.parent != common / 'worktrees':
        raise WorktreeError(f'Git worktree at {path} does not have a linked Git directory in {common}.')
    backlink = git_dir / 'gitdir'
    if not regular_file(backlink):
        raise WorktreeError(f'Git directory {git_dir} is missing its worktree backlink. Inspect git worktree list --porcelain.')
    location = os.fsdecode(backlink.read_bytes()).removesuffix('\n').removesuffix('\r')
    if Path(os.path.abspath(git_dir / location)) != marker:
        raise WorktreeError(f'Git directory {git_dir} does not point back to {marker}. Inspect git worktree list --porcelain.')
    head = git(path, 'symbolic-ref', '--quiet', 'HEAD', allowed=(0, 1))
    branch = os.fsdecode(head.stdout).removesuffix('\n') if head.returncode == 0 else None
    if branch != entry.branch:
        raise WorktreeError(f'Git branch changed at {path}. Inspect git worktree list --porcelain and retry.')
    return git_dir


def open_windows_lock(path, *, read_only=False):
    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create_file = kernel.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    # GENERIC_READ / OPEN_EXISTING or GENERIC_READ | GENERIC_WRITE / OPEN_ALWAYS.
    # Both use exclusive sharing and OPEN_REPARSE_POINT.
    access, disposition = (0x80000000, 3) if read_only else (0xC0000000, 4)
    handle = create_file(str(path), access, 0, None, disposition, 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        error = ctypes.WinError(ctypes.get_last_error())
        if error.winerror == 32:  # ERROR_SHARING_VIOLATION
            raise BlockingIOError(errno.EAGAIN, 'exclusive file is open', path) from error
        raise error
    try:
        mode = os.O_RDONLY if read_only else os.O_RDWR
        return msvcrt.open_osfhandle(handle, mode | os.O_BINARY | os.O_NOINHERIT)
    except BaseException:
        kernel.CloseHandle(handle)
        raise


@contextmanager
def inherit_lock(fd):
    if os.name != 'nt':
        yield {'pass_fds': (fd,)}
        return
    import msvcrt

    handle = msvcrt.get_osfhandle(fd)
    os.set_handle_inheritable(handle, True)
    try:
        yield {'startupinfo': subprocess.STARTUPINFO(lpAttributeList={'handle_list': [handle]})}
    finally:
        os.set_handle_inheritable(handle, False)


def check_lock_file(path, info):
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
        raise WorktreeError(f'Unsafe local lock at {path}. Expected a single-link regular file.')


@contextmanager
def exclusive_git_lock(path, *, read_only=False):
    # Closing the last handle releases the OS lock, including after an interrupted process.
    fd = None
    try:
        if read_only:
            initial = path.lstat()
            check_lock_file(path, initial)
        if os.name == 'nt':
            fd = open_windows_lock(path, read_only=read_only)
        else:
            import fcntl

            if not read_only:
                try:
                    check_lock_file(path, path.lstat())
                except FileNotFoundError:
                    pass
            mode = os.O_RDONLY if read_only else os.O_RDWR | os.O_CREAT
            flags = mode | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0)
            fd = os.open(path, flags, 0o600)
        opened = os.fstat(fd)
        check_lock_file(path, opened)
        if read_only and not os.path.samestat(initial, opened):
            raise WorktreeError(f'Local lock changed at {path}. Inspect the path and retry.')
        if os.name != 'nt':
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if read_only or os.name != 'nt':
            current = path.lstat()
            check_lock_file(path, current)
            if not os.path.samestat(opened, current):
                raise WorktreeError(f'Local lock changed at {path}. Inspect the path and retry.')
        yield fd
    finally:
        if fd is not None:
            os.close(fd)


@contextmanager
def creation_lock(common):
    path = common / CREATION_LOCK
    with ExitStack() as stack:
        try:
            fd = stack.enter_context(exclusive_git_lock(path))
        except BlockingIOError as error:
            raise WorktreeError(
                f'Worktree creation is already running. Retry when it finishes. Lock file: {path}'
            ) from error
        yield fd


def add_worktree(cwd, lock_fd, path, branch, base):
    branch_args = ('-b', branch, '--', str(path), base) if base else ('--', str(path), branch)
    try:
        git(cwd, 'worktree', 'add', '--no-guess-remote', *branch_args, lock_fd=lock_fd)
    except WorktreeError as error:
        raise WorktreeError(
            f'{error}\nInspect git worktree list --porcelain and retry the request if the checkout completed. '
            'If only the branch was created, verify its tip before retrying with --branch.'
        ) from error


def create(cwd, common, root, registered, path, args, lock_fd):
    branch = args.branch if args.branch is not None else args.new_branch
    if git(cwd, 'check-ref-format', '--branch', branch).stdout != os.fsencode(branch) + b'\n':
        raise WorktreeError(f'Supply a literal local branch name: {branch}')
    ref = f'refs/heads/{branch}'
    base = None
    if args.new_branch is not None:
        base = git(cwd, 'rev-parse', '--verify', '--end-of-options', f'{args.base}^{{commit}}').stdout.decode().strip()
    entry = registered_checkout(registered, path)
    if entry:
        verify_checkout(common, entry)
        if entry.branch != ref:
            raise WorktreeError(f'{path} is already registered for {entry.branch or "a detached HEAD"}; choose another name.')
        if base and git(cwd, 'merge-base', '--is-ancestor', base, ref, allowed=(0, 1)).returncode:
            raise WorktreeError(f'Cannot reuse {path}: {args.base} is not an ancestor of {branch}.')
        return 'Reused'

    if entry_info(path) is not None or any(entry.path.is_relative_to(path) for entry in registered):
        raise WorktreeError(f'Worktree path is occupied or already registered: {path}. Choose another name.')
    for entry in registered:
        if entry.branch == ref:
            raise WorktreeError(f'Branch {branch} is already registered at {entry.path}. Use that checkout or choose another branch.')

    exists = git(cwd, 'show-ref', '--verify', '--quiet', '--', ref, allowed=(0, 1)).returncode == 0
    if base and exists:
        raise WorktreeError(
            f'Local branch {branch} already exists. Verify its tip against {args.base}, '
            f'then use --branch {branch} to check it out.'
        )
    if not base and not exists:
        raise WorktreeError(f'Local branch {branch} does not exist. Use --new-branch {branch} --base <commit-ish> to create it.')

    root.mkdir(parents=True, exist_ok=True)
    add_worktree(cwd, lock_fd, path, branch, base)
    entry = registered_checkout(registrations(cwd), path)
    if entry is None:
        raise WorktreeError(f'Git did not register {path}. Inspect git worktree list --porcelain.')
    verify_checkout(common, entry)
    if entry.branch != ref:
        raise WorktreeError(f'Git did not register {path} for {ref}. Inspect git worktree list --porcelain.')
    return 'Created'


def validate_retirement_entry_limit(limit):
    if type(limit) is not int or not 1 <= limit <= MAX_RETIREMENT_ENTRY_LIMIT:
        raise ValueError(f'--entry-limit must be an integer from 1 through {MAX_RETIREMENT_ENTRY_LIMIT}')


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    actions = parser.add_subparsers(dest='action', required=True)
    create_parser = actions.add_parser('create', help='Create or reuse a named checkout.', allow_abbrev=False)
    create_parser.add_argument('name')
    branches = create_parser.add_mutually_exclusive_group(required=True)
    branches.add_argument('--branch', help='Check out this existing local branch.')
    branches.add_argument('--new-branch', help='Create this local branch from --base.')
    create_parser.add_argument('--base', help='Commit-ish for --new-branch.')
    use_parser = actions.add_parser('use', help='Print the absolute path of a named checkout.', allow_abbrev=False)
    use_parser.add_argument('name')
    actions.add_parser('list', help='List registered worktrees and their branches.', allow_abbrev=False)
    retirement = actions.add_parser('retirement', help='Report observed retirement candidates without changing worktrees.',
                                    allow_abbrev=False)
    retirement.add_argument('--archive-root', type=Path, help='Absolute path to a host archive outside the checkouts.')
    retirement.add_argument('--dispositions', metavar='COMMIT:PATH', help='Committed disposition records for exact heads.')
    retirement.add_argument('--entry-limit', type=int, metavar='COUNT', default=DEFAULT_RETIREMENT_ENTRY_LIMIT,
                            help=f'Entries per checkout for each ignored-path inventory and the Python consumer inventory '
                                 f'(1 through {MAX_RETIREMENT_ENTRY_LIMIT}, default: %(default)s).')
    args = parser.parse_args()
    if args.action == 'create' and (args.new_branch is None) != (args.base is None):
        create_parser.error('--new-branch and --base must be supplied together.')
    if args.action == 'retirement':
        try:
            validate_retirement_entry_limit(args.entry_limit)
        except ValueError as error:
            retirement.error(str(error))
    try:
        cwd = git_path(Path.cwd(), '--show-toplevel')
        common = git_path(cwd, '--git-common-dir')
        with (creation_lock(common) if args.action == 'create' else nullcontext()) as lock_fd:
            registered = list(registrations(cwd))
            if args.action == 'retirement':
                sys.dont_write_bytecode = True
                if __package__:
                    from .worktree_retirement import retirement_report
                else:
                    # The offline command uses -I -S. Keep create, use and list usable as a pinned single file.
                    sys.path.append(str(Path(__file__).resolve().parent))
                    from worktree_retirement import retirement_report
                print(json.dumps(retirement_report(cwd, common, registered, archive_root=args.archive_root,
                                                   dispositions=args.dispositions, entry_limit=args.entry_limit),
                                 indent=2))
                return
            root = managed_root(cwd, common, registered)
            if args.action == 'list':
                print(f'Managed root: {root}')
                for entry in registered:
                    location = 'managed' if entry.path.parent == root else 'other'
                    branch = entry.branch.removeprefix('refs/heads/') if entry.branch else '(no branch)'
                    flags = ', '.join(sorted(entry.flags & {'locked', 'bare', 'prunable'}))
                    print(f'{location}\t{branch}\t{entry.path}' + (f' [{flags}]' if flags else ''))
            else:
                path = destination(root, args.name)
                if args.action == 'create':
                    outcome = create(cwd, common, root, registered, path, args, lock_fd)
                    print(f'{outcome} {path}\nUse "use {args.name}" to print its path.')
                else:
                    entry = registered_checkout(registered, path)
                    if entry is None:
                        raise WorktreeError(f'No registered checkout at {path}. Use list or create.')
                    verify_checkout(common, entry)
                    print(path)
    except KeyboardInterrupt:
        parser.exit(130, 'ScopeHub worktree: Interrupted. Wait for Git to exit before retrying.\n')
    except (OSError, RuntimeError) as error:
        parser.exit(1, f'ScopeHub worktree: {error}\n')


if __name__ == '__main__':
    main()
