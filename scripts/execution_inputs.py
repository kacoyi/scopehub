"""Identify verification inputs in the checkout or an immutable Git tree."""

import ast
from functools import cache
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import PurePosixPath

if __package__:
    from .development import select_source
    from .verification_scope import git, invalid_number, nul_fields, unique_object
else:
    from development import select_source
    from verification_scope import git, invalid_number, nul_fields, unique_object


class ExecutionInputError(ValueError):
    """A source cannot supply a complete execution-input snapshot."""


TASK_DEFINITION_FIELDS = (
    'id', 'title', 'milestone', 'depends_on', 'scope', 'decision_refs', 'acceptance', 'checks',
)
DEFINITION_READ_LIMIT = 8 * 1024 * 1024


def definition_digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def task_definition_hash(task):
    definition = {key: task[key] for key in TASK_DEFINITION_FIELDS}
    for key in ('delivery', 'plan', 'diagnostic_reports'):
        if key in task:
            definition[key] = task[key]
    return definition_digest(definition)


def project_definition_claims(project):
    # Keep receipt and workflow-state mutations out of the observed definition.
    task_definitions = {key: task_definition_hash(task) for key, task in project['tasks'].items()}
    definition = {'tasks': task_definitions, 'decisions': project['decisions']}
    if project.get('owner_decisions'):
        definition['owner_decisions'] = project['owner_decisions']
    return {'project_definition_sha256': definition_digest(definition), 'task_definitions': task_definitions}


def project_definition_hash(project):
    return project_definition_claims(project)['project_definition_sha256']


def committed_project_definition_claims(root, revision):
    """Read the bounded definition inputs without running historical project code."""
    listing = git(root, 'ls-tree', '-rlz', '--full-tree', revision, '--',
                  'project/tasks.json', 'contracts/decisions', 'contracts/reviews',
                  'engineering-contract-v1.json', 'project/development.json', 'contracts/contract.json')
    entries = {}
    for row in nul_fields(listing):
        metadata, path = row.split(b'\t', 1)
        mode, kind, _, size = metadata.split()
        if kind == b'blob' and mode in {b'100644', b'100755'}:
            entries[path.decode('utf-8')] = int(size)
    remaining = DEFINITION_READ_LIMIT

    @cache
    def read(path):
        nonlocal remaining
        size = entries[path]
        if size > remaining:
            raise ExecutionInputError('Project definition exceeds the committed read limit')
        content = git(root, 'cat-file', 'blob', f'{revision}:{path}')
        if len(content) != size:
            raise ExecutionInputError('Incomplete committed project definition')
        remaining -= size
        return json.loads(content.decode('utf-8'), object_pairs_hook=unique_object, parse_constant=invalid_number)

    def select(reference):
        return select_source(read(reference['path']), reference)

    def indexed(rows):
        values = {row['id']: row for row in rows}
        if len(values) != len(rows):
            raise ExecutionInputError('Duplicate committed project definition ID')
        return values

    tasks = indexed(read('project/tasks.json')['tasks'])
    decisions = indexed([read(path) for path in entries if path.endswith('.json')
                         and PurePosixPath(path).parent.as_posix() == 'contracts/decisions'])
    references = read('engineering-contract-v1.json')['repository'].get('owner_decision_inputs', [])
    if isinstance(references, dict) and set(references) == {'$source'}:
        references = select(references['$source'])
    owner_decisions = {reference['path'] + '#' + reference['pointer']: select(reference) for reference in references}
    if len(owner_decisions) != len(references):
        raise ExecutionInputError('Duplicate committed owner decision input')
    return project_definition_claims({'tasks': tasks, 'decisions': decisions, 'owner_decisions': owner_decisions})


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
