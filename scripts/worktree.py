"""Create and locate native Git worktrees in ScopeHub's managed worktree root."""

import argparse
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
import os
from pathlib import Path
import re
import stat
import subprocess
import sys


ROOT_CONFIG = 'scopehub.worktreeRoot'
PORTABLE_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*')
RESERVED_NAMES = {'AUX', 'CON', 'NUL', 'PRN', *(f'{prefix}{n}' for prefix in ('COM', 'LPT') for n in range(10))}


class WorktreeError(RuntimeError):
    pass


@dataclass(frozen=True)
class Registration:
    path: Path
    branch: str | None
    flags: frozenset[str]


def git(cwd, *args, allowed=(0,)):
    # In particular, ignore an inherited alternate worktree, index or config.
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    result = subprocess.run(['git', *args], cwd=cwd, env=env, stdin=subprocess.DEVNULL, capture_output=True)
    if result.returncode not in allowed:
        detail = os.fsdecode(result.stderr).strip()
        raise WorktreeError(detail or f'git {args[0]} exited with status {result.returncode}')
    return result


def git_path(cwd, option):
    output = git(cwd, 'rev-parse', '--path-format=absolute', option).stdout
    return Path(os.fsdecode(output).removesuffix('\n')).resolve(strict=True)


def registrations(cwd):
    records = os.fsdecode(git(cwd, 'worktree', 'list', '--porcelain', '-z').stdout)
    for record in records.split('\0\0'):
        fields = dict(field.partition(' ')[::2] for field in record.split('\0') if field)
        if 'worktree' in fields:
            yield Registration(Path(fields['worktree']), fields.get('branch'), frozenset(fields))


def entry_mode(path):
    try:
        return path.lstat().st_mode
    except FileNotFoundError:
        return None


def has_git_metadata(cwd, directory):
    marker = directory / '.git'
    return (entry_mode(marker) is not None
            and git(cwd, 'rev-parse', '--resolve-git-dir', str(marker), allowed=(0, 128)).returncode == 0)


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
    mode = entry_mode(root)
    if mode is not None and not stat.S_ISDIR(mode):
        raise WorktreeError(f'Managed worktree root is not a directory: {root}')
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


def verify_checkout(cwd, common, entry):
    if (entry.flags & {'bare', 'prunable'} or entry.path.resolve() != entry.path
            or not (entry.path / '.git').exists()):
        raise WorktreeError(f'Git worktree is unavailable at {entry.path}. Inspect git worktree list --porcelain.')
    if git_path(entry.path, '--show-toplevel') != entry.path or git_path(entry.path, '--git-common-dir') != common:
        raise WorktreeError(f'Git worktree at {entry.path} does not belong to {cwd}.')


@contextmanager
def creation_lock(common):
    # Git's branch-in-use check and registration can race across two worktree adds.
    lock = common / 'scopehub-worktree.lock'
    try:
        lock.mkdir()
    except FileExistsError as error:
        raise WorktreeError(
            f'Worktree creation is locked at {lock}. Retry once the other creation finishes. '
            'After an interruption, confirm creation has stopped before removing the empty lock directory.'
        ) from error
    try:
        yield
    finally:
        lock.rmdir()


def create(cwd, common, root, registered, path, args):
    branch = args.branch if args.branch is not None else args.new_branch
    if git(cwd, 'check-ref-format', '--branch', branch).stdout != os.fsencode(branch) + b'\n':
        raise WorktreeError(f'Supply a literal local branch name: {branch}')
    ref = f'refs/heads/{branch}'
    base = None
    if args.new_branch is not None:
        base = git(cwd, 'rev-parse', '--verify', '--end-of-options', f'{args.base}^{{commit}}').stdout.decode().strip()
    entry = next((entry for entry in registered if entry.path == path), None)
    if entry:
        verify_checkout(cwd, common, entry)
        if entry.branch != ref:
            raise WorktreeError(f'{path} is already registered for {entry.branch or "a detached HEAD"}; choose another name.')
        if base and git(cwd, 'merge-base', '--is-ancestor', base, ref, allowed=(0, 1)).returncode:
            raise WorktreeError(f'Cannot reuse {path}: {args.base} is not an ancestor of {branch}.')
        return 'Reused'

    if entry_mode(path) is not None or any(entry.path.is_relative_to(path) for entry in registered):
        raise WorktreeError(f'Worktree path is occupied or already registered: {path}. Choose another name.')
    for entry in registered:
        if entry.branch == ref:
            raise WorktreeError(f'Branch {branch} is already registered at {entry.path}. Use that checkout or choose another branch.')

    exists = git(cwd, 'show-ref', '--verify', '--quiet', '--', ref, allowed=(0, 1)).returncode == 0
    if base and exists:
        raise WorktreeError(f'Local branch {branch} already exists. Use --branch {branch} to check it out.')
    if not base and not exists:
        raise WorktreeError(f'Local branch {branch} does not exist. Use --new-branch {branch} --base <commit-ish> to create it.')

    root.mkdir(parents=True, exist_ok=True)
    branch_args = ('-b', branch, '--', str(path), base) if base else ('--', str(path), branch)
    git(cwd, 'worktree', 'add', '--no-guess-remote', *branch_args)
    return 'Created'


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
    args = parser.parse_args()
    if args.action == 'create' and (args.new_branch is None) != (args.base is None):
        create_parser.error('--new-branch and --base must be supplied together.')
    try:
        cwd = git_path(Path.cwd(), '--show-toplevel')
        common = git_path(cwd, '--git-common-dir')
        with (creation_lock(common) if args.action == 'create' else nullcontext()):
            registered = list(registrations(cwd))
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
                    outcome = create(cwd, common, root, registered, path, args)
                    print(f'{outcome} {path}\nUse "use {args.name}" to print its path.')
                else:
                    entry = next((entry for entry in registered if entry.path == path), None)
                    if entry is None:
                        raise WorktreeError(f'No registered checkout at {path}. Use list or create.')
                    verify_checkout(cwd, common, entry)
                    print(path)
    except (OSError, RuntimeError) as error:
        parser.exit(1, f'ScopeHub worktree: {error}\n')


if __name__ == '__main__':
    main()
