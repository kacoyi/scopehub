"""Capture and verify a quiescent Linux Git primary checkout rename and its rollback."""

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

if __package__:
    from .worktree import WorktreeError, creation_lock, git, has_git_metadata
else:
    from worktree import WorktreeError, creation_lock, git, has_git_metadata


# These build directories move intact with the primary inode. Keep their local
# entry metadata but do not hash their contents on every verification pass.
BUILD_DIRECTORIES = {'target', '.venv', 'node_modules', '__pycache__', '.mypy_cache', '.pytest_cache', '.uv-cache'}


def read_git(root, *args):
    return git(root, '--no-optional-locks', *args).stdout


def identity(path):
    info = path.stat()
    return [info.st_dev, info.st_ino]


def pointer(file, prefix=''):
    if file.is_symlink():
        raise WorktreeError(f'Unexpected symlink for Git worktree pointer: {file}')
    value = os.fsdecode(file.read_bytes()).removesuffix('\n').removesuffix('\r')
    if not value.startswith(prefix) or value == prefix:
        raise WorktreeError(f'Invalid worktree pointer {file}')
    return Path(os.path.abspath(file.parent / value.removeprefix(prefix)))


def registered(root):
    listing = os.fsdecode(read_git(root, 'worktree', 'list', '--porcelain', '-z'))
    records = []
    for record in filter(None, listing.split('\0\0')):
        fields = dict(field.partition(' ')[::2] for field in record.split('\0'))
        path = Path(fields.pop('worktree'))
        records.append((path, fields))
    if not records or records[0][0] != root:
        raise WorktreeError(f'Expected the first Git worktree to be the primary at {root}')
    return records


def content_manifest(root, paths, *, omit_build_dirs=False):
    digest = hashlib.sha256()
    count = 0

    def observe(path):
        nonlocal count
        info = path.lstat()
        kind = stat.S_IFMT(info.st_mode)
        data = None
        if kind == stat.S_IFLNK:
            data = os.readlink(path)
        elif kind == stat.S_IFREG:
            with path.open('rb') as stream:
                data = hashlib.file_digest(stream, 'sha256').hexdigest()
            after = path.lstat()
            if not os.path.samestat(info, after) or info.st_mtime_ns != after.st_mtime_ns:
                raise WorktreeError(f'File changed during capture: {path}')
        elif kind != stat.S_IFDIR:
            raise WorktreeError(f'Unsupported entry in local worktree state: {path}')
        digest.update(json.dumps([str(path.relative_to(root)), info.st_mode, data]).encode() + b'\0')
        count += 1
        if kind == stat.S_IFDIR and (not omit_build_dirs or path.name not in BUILD_DIRECTORIES):
            for child in sorted(path.iterdir()):
                observe(child)

    for path in sorted(paths):
        observe(path)
    return {'entries': count, 'sha256': digest.hexdigest()}


def extras(root, *args):
    output = read_git(root, 'ls-files', '--others', '--exclude-standard', '-z', *args)
    return {root / os.fsdecode(path) for path in output.split(b'\0') if path}


def worktree_state(root):
    def digest(*args):
        return hashlib.sha256(read_git(root, *args)).hexdigest()

    diff = ('diff', '--no-ext-diff', '--no-textconv', '--binary')
    reports = root / '.reports'
    return {
        'inode': identity(root),
        'status': digest('status', '--porcelain=v1', '-z', '--untracked-files=all'),
        'index': digest('ls-files', '--stage', '-z'),
        'unstaged': digest(*diff, '--'),
        'untracked': content_manifest(root, extras(root)),
        'ignored': content_manifest(root, extras(root, '--ignored', '--directory', '--no-empty-directory')
                                    - {reports}, omit_build_dirs=True),
        'reports': content_manifest(root, {reports} if os.path.lexists(reports) else set()),
    }


def capture(root):
    common = root / '.git'
    if common.is_symlink() or not common.is_dir():
        raise WorktreeError(f'Expected primary Git metadata at {common}')
    source = {}
    for path, fields in registered(root):
        if path.is_symlink() or (path.exists() and path.resolve(strict=True) != path):
            raise WorktreeError(f'Unexpected linked checkout path: {path}')
        key = '.' if path == root else str(path)
        entry = {'registration': fields}
        if 'prunable' in fields:
            if path.exists():
                raise WorktreeError(f'Existing prunable checkout requires investigation: {path}')
        else:
            top = os.fsdecode(read_git(path, 'rev-parse', '--show-toplevel')).removesuffix('\n')
            if Path(top) != path:
                raise WorktreeError(f'Git reports a different checkout root for {path}')
            if path != root:
                marker = path / '.git'
                admin = pointer(marker, 'gitdir: ')
                if (admin.parent != common / 'worktrees'
                        or admin.resolve(strict=True) != admin
                        or pointer(admin / 'gitdir') != marker
                        or pointer(admin / 'commondir') != common):
                    raise WorktreeError(f'Git worktree pointers disagree at {path}')
                entry['admin'] = [admin.name, identity(admin)]
            entry['state'] = worktree_state(path)
        source[key] = entry
    return {'root': identity(root), 'common': identity(common), 'worktrees': source,
            'refs': hashlib.sha256(read_git(root, 'show-ref', '--head', '--dereference')).hexdigest()}


def check_capture(root, expected):
    actual = capture(root)
    for key in ('root', 'common', 'refs'):
        if actual[key] != expected[key]:
            raise WorktreeError(f'Primary {key} differs from the captured state')
    before, after = expected['worktrees'], actual['worktrees']
    changed = sorted(key for key in before.keys() | after.keys() if before.get(key) != after.get(key))
    if changed:
        raise WorktreeError(f'Worktree state differs from capture: {", ".join(changed)}')


def canonical(path):
    if not path.is_absolute() or path.resolve(strict=False) != path:
        raise WorktreeError(f'Expected an absolute path without symlinks: {path}')
    return path


def state_path(path):
    canonical(path)
    info = path.parent.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise WorktreeError(f'State must be in an owner-private directory: {path.parent}')
    return path


def read_state(path):
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise WorktreeError('Expected an owner-private regular state file')
        return json.load(stream)


def rename_exclusive(source, destination):
    # Linux RENAME_NOREPLACE keeps even an empty destination from being overwritten.
    libc = ctypes.CDLL(None, use_errno=True)
    at_fdcwd, noreplace = -100, 1
    if libc.renameat2(at_fdcwd, os.fsencode(source), at_fdcwd, os.fsencode(destination), noreplace):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(destination))


def compatibility_link(source, destination):
    return source.is_symlink() and Path(os.readlink(source)) == destination


def create_state(source, destination, path):
    canonical(source)
    canonical(destination)
    if source == destination or source in destination.parents or destination in source.parents:
        raise WorktreeError('Primary paths must be distinct and non-nested')
    if any(path.is_relative_to(root) for root in (source, destination)):
        raise WorktreeError('State must be outside both checkout paths')
    ancestor = next(parent for parent in destination.parents if parent.exists())
    if destination.exists() or not ancestor.is_dir() or source.stat().st_dev != ancestor.stat().st_dev:
        raise WorktreeError('The destination must be unused and on the primary filesystem')
    if any(has_git_metadata(source, parent) for parent in destination.parents):
        raise WorktreeError('The destination must be outside existing Git checkouts')
    with creation_lock(source / '.git'):
        baseline = capture(source)
        if any(other != '.' and (destination == Path(other) or destination.is_relative_to(other)
                                or Path(other).is_relative_to(destination))
               for other in baseline['worktrees']):
            raise WorktreeError('The destination overlaps another registered checkout')
        record = {'source': str(source), 'destination': str(destination), 'baseline': baseline}
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
            json.dump(record, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())


def active_primary(source, destination, baseline):
    raw_paths = [path for path in (source, destination) if path.is_dir() and not path.is_symlink()]
    if len(raw_paths) != 1 or any(path.is_symlink() for path in (source, destination)
                                  if path != source or not compatibility_link(source, destination)):
        raise WorktreeError('Expected one physical checkout and at most the matching compatibility link')
    root = canonical(raw_paths[0])
    if identity(root) != baseline['root'] or identity(root / '.git') != baseline['common']:
        raise WorktreeError('Checkout identity differs from the captured primary')
    return root


def operate(action, record, keep_link):
    source, destination = Path(record['source']), Path(record['destination'])
    baseline = record['baseline']
    root = active_primary(source, destination, baseline)
    with creation_lock(root / '.git'):
        if active_primary(source, destination, baseline) != root:
            raise WorktreeError('Primary moved while acquiring the worktree creation lock')
        if action == 'verify':
            check_capture(root, baseline)
            return
        if action == 'retire-link':
            if root != destination or not compatibility_link(source, destination):
                raise WorktreeError('Expected a relocated checkout with the matching compatibility link')
            source.unlink()
            return
        target = destination if action == 'move' else source
        if root != target:
            if action == 'move':
                check_capture(root, baseline)
            elif compatibility_link(source, destination):
                source.unlink()
            rename_exclusive(root, target)
        # A matching compatibility link is created only after Git repair.
        if target == destination and compatibility_link(source, destination):
            check_capture(target, baseline)
            return
        # Repair the primary and explicitly revisit every available linked checkout.
        linked = [path for path, entry in baseline['worktrees'].items() if path != '.' and 'state' in entry]
        read_git(target, 'worktree', 'repair', '--', *linked)
        if keep_link:
            source.symlink_to(destination, target_is_directory=True)
        check_capture(target, baseline)


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--state', type=Path, required=True)
    commands = parser.add_subparsers(dest='action', required=True)
    prepare = commands.add_parser('capture')
    prepare.add_argument('source', type=Path)
    prepare.add_argument('destination', type=Path)
    move = commands.add_parser('move')
    move.add_argument('--keep-link', action='store_true', help='Leave a temporary exact old-to-new symlink')
    for command in ('verify', 'rollback', 'retire-link'):
        commands.add_parser(command)
    args = parser.parse_args()
    try:
        if sys.platform != 'linux':
            raise WorktreeError('A Linux host with renameat2 and native Git is required')
        path = state_path(args.state)
        if args.action == 'capture':
            create_state(args.source, args.destination, path)
        else:
            operate(args.action, read_state(path), getattr(args, 'keep_link', False))
    except (OSError, ValueError, WorktreeError, subprocess.SubprocessError) as error:
        parser.exit(1, f'{parser.prog}: {error}\n')
    print(f'{args.action} passed')


if __name__ == '__main__':
    main()
