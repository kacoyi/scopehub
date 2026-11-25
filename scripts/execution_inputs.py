"""Identify verification inputs in the checkout or an immutable Git tree."""

import ast
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import PurePosixPath

if __package__:
    from .verification_scope import git, nul_fields
else:
    from verification_scope import git, nul_fields


class ExecutionInputError(ValueError):
    """A source cannot supply a complete execution-input snapshot."""


def is_source_input(relative, exclusions):
    return not any(relative == item or (item.endswith('/') and relative.startswith(item)) for item in exclusions)


def source_snapshot(entries, head, dirty, exclusions):
    if not entries:
        raise ExecutionInputError('No execution inputs found')
    return {
        'snapshot_kind': 'execution-inputs-v1',
        'sha256': sha256(json.dumps(sorted(entries), separators=(',', ':')).encode('utf-8')).hexdigest(),
        'file_count': len(entries), 'git_head': head,
        'working_tree_dirty': dirty, 'exclusions': exclusions,
    }


def historical_input_exclusions(root, commit):
    # Read the historical v1 policy as data. A candidate's code never runs here.
    try:
        policy = ast.parse(git(root, 'show', f'{commit}:scripts/project.py'))
        values = [statement.value for statement in policy.body if isinstance(statement, ast.Assign)
                  for target in statement.targets if isinstance(target, ast.Name) and target.id == 'INPUT_EXCLUSIONS']
        value, = values
        exclusions = ast.literal_eval(value)
    except (SyntaxError, ValueError, TypeError) as error:
        raise ExecutionInputError(f'Cannot resolve execution-inputs-v1 rules at {commit}') from error
    if (not isinstance(exclusions, list) or not all(isinstance(path, str) and path for path in exclusions)
            or len(exclusions) != len(set(exclusions))):
        raise ExecutionInputError(f'Invalid execution-inputs-v1 exclusions at {commit}')
    return exclusions


def committed_source_identity(root, commit):
    """Reproduce a clean v1 snapshot from a verified Git commit and its own rules."""
    exclusions = historical_input_exclusions(root, commit)
    blobs = []
    for field in nul_fields(git(root, 'ls-tree', '-r', '-z', '--full-tree', commit)):
        info, path = field.split(b'\t', 1)
        mode, kind, object_id = info.split()
        relative = path.decode('utf-8')
        # The worktree snapshot observes files and symlinks, not submodule directories.
        if kind == b'blob' and is_source_input(relative, exclusions):
            blobs.append((object_id, mode, relative))
    requests = b''.join(object_id + b'\n' for object_id, _, _ in blobs)
    entries = []
    with BytesIO(git(root, 'cat-file', '--batch', input=requests)) as contents:
        for object_id, mode, relative in blobs:
            header = contents.readline().split()
            if len(header) != 3 or header[:2] != [object_id, b'blob'] or not header[2].isdigit():
                raise ExecutionInputError(f'Cannot read execution input {relative} at {commit}')
            size = int(header[2])
            data = contents.read(size)
            if len(data) != size or contents.read(1) != b'\n':
                raise ExecutionInputError(f'Incomplete execution input {relative} at {commit}')
            if mode == b'120000':
                entries.append([relative, 'symlink', PurePosixPath(os.fsdecode(data)).as_posix()])
            else:
                entries.append([relative, 'file', sha256(data).hexdigest()])
        if contents.read(1):
            raise ExecutionInputError(f'Unexpected execution input objects at {commit}')
    return source_snapshot(entries, commit, False, exclusions)
