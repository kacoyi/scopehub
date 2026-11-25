"""Validate project history, show a resumable workfront and preserve check evidence."""

import argparse
import ast
from datetime import date, datetime, timedelta, timezone
from graphlib import CycleError, TopologicalSorter
import hashlib
from io import BytesIO
import json
import os
import re
from pathlib import Path, PurePosixPath
import subprocess
from statistics import median

from jsonschema import Draft202012Validator, FormatChecker

if __package__:
    from . import maintenance
    from .development import expand_engineering_sources, resolve_source
    from .diagnostic_archive import DiagnosticArchiveError, diagnostic_locator, resolve_diagnostic, validate_locator
    from .retained_failures import retained_failure_view
    from .operational_paths import OPERATIONAL_PATHS, is_operational_path
    from .verification_scope import ScopeError, checkout_git_environment, git, nul_fields, oid
    from .review_tree import CommittedPath
    from .worktree import WorktreeError, git as worktree_git, has_git_metadata
else:
    import maintenance
    from development import expand_engineering_sources, resolve_source
    from diagnostic_archive import DiagnosticArchiveError, diagnostic_locator, resolve_diagnostic, validate_locator
    from retained_failures import retained_failure_view
    from operational_paths import OPERATIONAL_PATHS, is_operational_path
    from verification_scope import ScopeError, checkout_git_environment, git, nul_fields, oid
    from review_tree import CommittedPath
    from worktree import WorktreeError, git as worktree_git, has_git_metadata

ROOT = Path(__file__).resolve().parents[1]
# These records describe work and evidence; they are not executable test inputs.
# Validate them afresh on every status/check command. Excluding them prevents a
# completion receipt from invalidating the source snapshot it records.
INPUT_EXCLUSIONS = [
    'project/tasks.json', 'project/evidence/', 'contracts/decisions/',
    'contracts/reviews/', 'contracts/references.json', 'project/feedback.json',
]
TASK_DEFINITION_FIELDS = [
    'id', 'title', 'milestone', 'depends_on', 'scope', 'decision_refs', 'acceptance', 'checks',
]
PATH_TOKEN = re.compile(r"""[^\s'"\x60<>()\[\]{},;|]+""")
DIAGNOSTIC_FILENAMES = {'test-report.json', 'diagnostic.json'}
LOGICAL_ARCHIVE_PATH = re.compile(
    r'archives/(?:runs/([^/]+)/test-report\.json|recovery/([^/]+)/diagnostic\.json)'
)
# The protected merge that introduced observation-bound feedback receipts. Its immutable
# first parent contains the last dispositions accepted under the previous contract.
FEEDBACK_RECEIPT_INTRODUCTION = '8e4c903c52843d3ac5c0dc7b7d95210d287cac18'


class ProjectError(ValueError):
    """A record is inconsistent or does not support the requested claim."""


def read_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise ProjectError(f'Cannot read JSON record {path}: {error}') from error


def json_bytes(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + '\n').encode('utf-8')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def task_definition_hash(task):
    definition = {key: task[key] for key in TASK_DEFINITION_FIELDS}
    for key in ('delivery', 'plan', 'diagnostic_reports'):
        if key in task:
            definition[key] = task[key]
    return digest(json.dumps(definition, sort_keys=True, separators=(',', ':')).encode('utf-8'))


def feedback_fingerprint(item):
    # Bind a regression run to the actual observation without publishing its text.
    diagnostics = sorted(report['report_sha256'] for report in item.get('diagnostic_reports', []))
    identity = [item['id'], item.get('occurrence', 1), item['observation'], sorted(item['regression_checks']), diagnostics]
    if baseline := item.get('verification_budget', {}).get('growth_baseline'):
        identity.append(sorted((row['run_id'], row['source_commit'], row['report_sha256']) for row in baseline))
    return digest(json_bytes(identity))


def project_definition_hash(project):
    # Workflow state and receipts change after recording a successful task.
    # Definitions and decisions must not change underneath the observed checks.
    definition = {'tasks': {key: task_definition_hash(task) for key, task in project['tasks'].items()},
                  'decisions': project['decisions']}
    if project.get('owner_decisions'):
        definition['owner_decisions'] = project['owner_decisions']
    return digest(json.dumps(definition, sort_keys=True, separators=(',', ':')).encode('utf-8'))


def local_path(root, relative):
    path = Path(relative)
    if path.is_absolute() or '..' in path.parts or not path.parts:
        raise ProjectError(f'Expected a repository-relative path: {relative}')
    candidate = root / path
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise ProjectError(f'Path escapes repository: {relative}')
    return candidate


def checkout_report_path(root, path, *, reports_only=False):
    checkout = root.resolve()
    directory = (checkout / '.reports').resolve() if reports_only else checkout
    candidate = (checkout / path).resolve()
    if ((reports_only and directory == checkout) or not directory.is_relative_to(checkout)
            or not candidate.is_relative_to(directory) or candidate == directory):
        suffix = ' under .reports/' if reports_only else ''
        raise ProjectError(f'Expected a checkout-local report file{suffix}')
    return candidate


def evidence_report_path(root, path):
    checkout = root.resolve()
    candidate = Path(path).resolve()
    # Inspect the supplied location as well as its target. An explicit neutral
    # copy is portable; a path through a different live checkout is a dependency.
    for location in {Path(os.path.abspath(path)), candidate}:
        for directory in location.parents:
            try:
                is_checkout = has_git_metadata(checkout, directory)
            except WorktreeError as error:
                raise ProjectError(f'Cannot verify the report checkout at {directory}: {error}') from error
            if not is_checkout:
                continue
            if directory.resolve() != checkout or not candidate.is_relative_to(checkout):
                raise ProjectError('Report path depends on another checkout or escapes the current checkout')
            break
    if not candidate.is_file():
        raise ProjectError(f'Expected a report file at {candidate}')
    return candidate


def report_text(value):
    if isinstance(value, dict):
        for entry in value.values():
            yield from report_text(entry)
    elif isinstance(value, list):
        for entry in value:
            yield from report_text(entry)
    elif isinstance(value, str):
        yield value


def require_portable_report_paths(root, value, label, run_ids=()):
    """Check literal report paths while leaving ordinary prose and web URLs alone."""
    for line in report_text(value):
        for match in PATH_TOKEN.finditer(line):
            literal = match[0].split('=', 1)[-1].partition('#')[0].rstrip('.!?:')
            token = literal.replace('\\', '/')
            token = re.sub(r':\d+(?::\d+)?$', '', token)
            if token.startswith(('http://', 'https://')):
                continue
            archive_path = LOGICAL_ARCHIVE_PATH.fullmatch(literal)
            if archive_path and (archive_path[1] or archive_path[2]) in run_ids:
                continue
            parts = token.split('/')
            report_directory = any(part.casefold() == '.reports' for part in parts)
            if not report_directory and (len(parts) == 1 or parts[-1].casefold() not in DIAGNOSTIC_FILENAMES):
                continue
            relative = token.removeprefix('./')
            # Explicit checkout-relative files and the conventional report directory
            # are distinct from sibling checkout names and logical archive hints.
            if '..' not in parts and not relative.startswith('archives/'):
                try:
                    local = local_path(root, relative)
                    if (relative == '.reports' or relative.startswith('.reports/')
                            or not report_directory and (token.startswith('./') or local.is_file())):
                        continue
                except ProjectError:
                    pass
            raise ProjectError(f'{label} depends on a nonlocal report path {token!r}; use diagnostic_reports')


def validate_report_references(root, record, label):
    locators = [*record.get('diagnostic_reports', ()), *record.get('verification_budget', {}).get('growth_baseline', ())]
    for locator in locators:
        try:
            validate_locator(locator)
        except DiagnosticArchiveError as error:
            raise ProjectError(f'{label}: {error}') from error
    require_portable_report_paths(root, record, label, {locator['run_id'] for locator in locators})


def review_operational_report_paths(root, base):
    # Check only changed lines and untracked operational files so inherited
    # references, fixtures and generated outputs retain their existing scope.
    env = checkout_git_environment()
    diff = ['git', '--literal-pathspecs', 'diff', '--no-color', '--no-ext-diff', '--no-textconv', '--no-renames']
    changed = subprocess.check_output(
        [*diff, '--name-only', '--diff-filter=AMT', '-z', base, '--', *OPERATIONAL_PATHS], cwd=root, env=env,
    )
    for relative in os.fsdecode(changed).split('\0'):
        if not is_operational_path(relative):
            continue
        patch = subprocess.check_output([*diff, '--unified=0', base, '--', relative], cwd=root, env=env)
        in_hunk = False
        for line in patch.decode(errors='replace').splitlines():
            if line.startswith('@@ '):
                in_hunk = True
            elif in_hunk and line.startswith('+'):
                require_portable_report_paths(root, line[1:], f'Operational file {relative}')
    untracked = subprocess.check_output(
        ['git', 'ls-files', '--others', '--exclude-standard', '-z', '--', *OPERATIONAL_PATHS], cwd=root, env=env,
    )
    for relative in os.fsdecode(untracked).split('\0'):
        if is_operational_path(relative):
            path = local_path(root, relative)
            if path.is_file() and not path.is_symlink():
                require_portable_report_paths(root, path.read_text(encoding='utf-8', errors='replace'),
                                              f'Operational file {relative}')


def checkout_status(root, env):
    git = ['git', '--no-optional-locks', '-c', 'core.fsmonitor=false', '-c', 'core.untrackedCache=false']
    result = subprocess.run(
        [*git, 'ls-files', '-v', '--stage', '--cached', '--others', '--exclude-standard', '-z'],
        cwd=root, env=env, capture_output=True, check=True,
    )
    files, submodules = set(), set()
    index_dirty = False
    for entry in result.stdout.decode('utf-8').split('\0'):
        if not entry:
            continue
        if entry.startswith('? '):
            files.add(entry[2:])
            index_dirty = True
            continue
        stage, separator, relative = entry.partition('\t')
        if not separator or not relative:
            raise ProjectError('Cannot inspect Git index entries')
        files.add(relative)
        index_dirty |= not stage.startswith('H ')
        if stage[2:].startswith('160000 '):
            submodules.add(relative)
    status = subprocess.run(
        [*git, 'status', '--porcelain=v2', '--branch', '--no-ahead-behind',
         '--untracked-files=all', '--ignore-submodules=none', '-z'],
        cwd=root, env=env, capture_output=True, check=True,
    )
    records = status.stdout.split(b'\0')
    prefix = b'# branch.oid '
    if not records[0].startswith(prefix):
        raise ProjectError('Git status did not report the checkout HEAD')
    head = records[0][len(prefix):]
    dirty = index_dirty or any(record and not record.startswith(b'# ') for record in records)
    if not dirty:
        for relative in sorted(submodules):
            path = root / relative
            if path.is_symlink() or path.is_junction() or (path.exists() and not path.is_dir()):
                dirty = True
                break
            if (path / '.git').exists():
                # The parent status cannot see hidden index flags inside a populated submodule.
                _, _, dirty = checkout_status(path, env)
            elif path.is_dir():
                dirty = any(path.iterdir())
            if dirty:
                break
    return files, None if head == b'(initial)' else head.decode('ascii'), dirty


def require_review_source(root, requested):
    """Bind a local review to the named clean checkout, without hashing its evidence."""
    try:
        requested = oid(requested)
    except ScopeError as error:
        raise ProjectError('Pass the full commit ID for --source.') from error
    _, head, dirty = checkout_status(root, checkout_git_environment())
    if head != requested:
        raise ProjectError(f'Review source {requested} does not match checkout HEAD {head}.')
    if dirty:
        raise ProjectError(f'Review source {requested} requires a clean checkout.')


def diagnostic_archive_root(root, explicit=None):
    if explicit is not None:
        return explicit
    if configured := os.environ.get('SCOPEHUB_ARCHIVE_ROOT'):
        return configured
    try:
        setting = worktree_git(root, 'config', '--local', '--null', '--type=path', '--get-all',
                               'scopehub.archiveRoot', allowed=(0, 1))
    except WorktreeError as error:
        raise ProjectError('Could not read the repository-local scopehub.archiveRoot setting.') from error
    if setting.returncode == 1:
        raise ProjectError('Set --archive-root, SCOPEHUB_ARCHIVE_ROOT, or '
                           'git config --local scopehub.archiveRoot <absolute-path> to resolve a diagnostic.')
    paths = os.fsdecode(setting.stdout).removesuffix('\0').split('\0')
    if len(paths) != 1 or not Path(paths[0]).is_absolute():
        raise ProjectError('Set exactly one absolute scopehub.archiveRoot with git config --local.')
    return Path(paths[0])


def is_source_input(relative, exclusions):
    return not any(relative == item or (item.endswith('/') and relative.startswith(item)) for item in exclusions)


def source_snapshot(entries, head, dirty, exclusions):
    if not entries:
        raise ProjectError('No execution inputs found')
    return {
        'snapshot_kind': 'execution-inputs-v1',
        'sha256': digest(json.dumps(sorted(entries), separators=(',', ':')).encode('utf-8')),
        'file_count': len(entries), 'git_head': head,
        'working_tree_dirty': dirty, 'exclusions': exclusions,
    }


def source_identity(root=ROOT):
    """Hash current non-ignored files without following symlinks or reading .git."""
    files, head, dirty = checkout_status(root, checkout_git_environment())
    entries = []
    for relative in files:
        if not is_source_input(relative, INPUT_EXCLUSIONS):
            continue
        path = root / relative
        if path.is_symlink():
            entries.append([relative, 'symlink', path.readlink().as_posix()])
        elif path.is_file():
            entries.append([relative, 'file', file_digest(path)])
    return source_snapshot(entries, head, dirty, INPUT_EXCLUSIONS)


def historical_input_exclusions(root, commit):
    # Read the historical v1 policy as data. A candidate's code never runs here.
    try:
        policy = ast.parse(git(root, 'show', f'{commit}:scripts/project.py'))
        values = [statement.value for statement in policy.body if isinstance(statement, ast.Assign)
                  for target in statement.targets if isinstance(target, ast.Name) and target.id == 'INPUT_EXCLUSIONS']
        value, = values
        exclusions = ast.literal_eval(value)
    except (SyntaxError, ValueError, TypeError) as error:
        raise ProjectError(f'Cannot resolve execution-inputs-v1 rules at {commit}') from error
    if (not isinstance(exclusions, list) or not all(isinstance(path, str) and path for path in exclusions)
            or len(exclusions) != len(set(exclusions))):
        raise ProjectError(f'Invalid execution-inputs-v1 exclusions at {commit}')
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
                raise ProjectError(f'Cannot read execution input {relative} at {commit}')
            size = int(header[2])
            data = contents.read(size)
            if len(data) != size or contents.read(1) != b'\n':
                raise ProjectError(f'Incomplete execution input {relative} at {commit}')
            if mode == b'120000':
                entries.append([relative, 'symlink', PurePosixPath(os.fsdecode(data)).as_posix()])
            else:
                entries.append([relative, 'file', digest(data)])
        if contents.read(1):
            raise ProjectError(f'Unexpected execution input objects at {commit}')
    return source_snapshot(entries, commit, False, exclusions)


def validate_model(root, name, value):
    schema = read_json(root / 'contracts/schemas/project.schema.json')
    validator = Draft202012Validator(
        {'$ref': f'#/$defs/{name}', '$defs': schema['$defs']}, format_checker=FormatChecker(),
    )
    errors = list(validator.iter_errors(value))
    if errors:
        first = errors[0]
        location = '/'.join(str(part) for part in first.absolute_path)
        raise ProjectError(f'{name}/{location}: {first.message}')


def indexed(items, label, field='id'):
    result = {item[field]: item for item in items}
    if len(result) != len(items):
        raise ProjectError(f'Duplicate {label} ID')
    return result


def require_refs(values, known, label):
    missing = set(values) - set(known)
    if missing:
        raise ProjectError(f'{label}: unknown references {sorted(missing)}')


def validate_graph(edges, label):
    for key, targets in edges.items():
        require_refs(targets, edges, f'{label} {key}')
    try:
        TopologicalSorter(edges).prepare()
    except CycleError as error:
        raise ProjectError(f'{label}: cycle at {error.args[1][0]}') from error


def windows_x64(environment):
    return environment.get('system') == 'Windows' and environment.get('machine', '').lower() in {'amd64', 'x86_64'}


def load_project(root=ROOT):
    try:
        spec = expand_engineering_sources(root, read_json(root / 'engineering-contract-v1.json'))
    except (OSError, ValueError) as error:
        raise ProjectError(f'Invalid engineering contract: {error}') from error
    ledger = read_json(root / 'project/tasks.json')
    catalog = read_json(root / 'project/checks.json')
    validate_model(root, 'TaskLedger', ledger)
    validate_model(root, 'CheckCatalog', catalog)
    tasks = indexed(ledger['tasks'], 'task')
    checks = indexed(catalog['checks'], 'check')
    gates = indexed(catalog['milestones'], 'milestone')
    if set(gates) != {item['id'] for item in spec['milestones']}:
        raise ProjectError('Milestone catalog differs from the engineering contract')
    for check in checks.values():
        if (check['status'] == 'implemented') != (check['command'] is not None):
            raise ProjectError(f"Check command/status mismatch: {check['id']}")
    required = set(catalog['required_change_checks'])
    require_refs(required, checks, 'Required change checks')
    implemented = {key for key, check in checks.items()
                   if check['status'] == 'implemented' and check.get('cadence', 'change') == 'change'}
    if required != implemented:
        raise ProjectError('Required change checks must equal the implemented change checks')
    changes = indexed(catalog['gate_changes'], 'gate replacement', field='removed_check')
    successors = {key: [] for key in checks}
    for key, change in changes.items():
        require_refs([key], checks, 'Gate replacement')
        successors[key] = change['replacement_checks']
        require_refs([change['task_id']], tasks, 'Gate replacement task')
        task = tasks[change['task_id']]
        if (key in change['replacement_checks'] or 'plan' not in task or task['status'] == 'cancelled'
                or not set(change['replacement_checks']).issubset(task['checks'])):
            raise ProjectError(f'Gate replacement lacks planned corrective coverage: {key}')
    validate_graph(successors, 'Gate replacement')
    for gate in gates.values():
        require_refs(gate['checks'], checks, f"Milestone {gate['id']}")
    for name, check_ids in catalog.get('profiles', {}).items():
        require_refs(check_ids, checks, f'Profile {name}')
    for label, check_ids in [
        *[(f"Milestone {gate['id']}", gate['checks']) for gate in gates.values()],
        *[(f"Acceptance {item['requirement']}", item['checks']) for item in catalog['acceptance']],
    ]:
        if any(checks[key]['status'] == 'retired' for key in check_ids if key in checks):
            raise ProjectError(f'{label} depends on a retired check')
    requirements = [item['id'] for item in catalog['acceptance']]
    required = spec['acceptance']['required']
    if len(set(requirements)) != len(requirements) or sorted(requirements) != sorted(required):
        raise ProjectError('Product acceptance bindings differ from the engineering contract')
    for binding in catalog['acceptance']:
        require_refs(binding['checks'], checks, 'Product acceptance')
    decisions = []
    decision_paths = {}
    for path in sorted((root / 'contracts/decisions').glob('*.json'), key=lambda path: path.name):
        decision = read_json(path)
        validate_model(root, 'Decision', decision)
        if not path.name.startswith(decision['id'] + '-'):
            raise ProjectError(f'Decision ID does not match filename: {path.name}')
        decisions.append(decision)
        decision_paths[decision['id']] = path.relative_to(root).as_posix()
    decisions = indexed(decisions, 'decision')
    validate_graph({key: item['supersedes'] for key, item in decisions.items()}, 'Decision supersession')
    replacements = {}
    for decision in decisions.values():
        if decision['status'] != 'accepted':
            continue
        for predecessor in decision['supersedes']:
            if decisions[predecessor]['status'] != 'accepted':
                raise ProjectError(f'Only accepted decisions can be superseded: {predecessor}')
            if predecessor in replacements:
                raise ProjectError(f'Ambiguous decision replacement: {predecessor}')
            replacements[predecessor] = decision['id']
    validate_graph({key: item['depends_on'] for key, item in tasks.items()}, 'Task dependencies')
    for task in tasks.values():
        if task['status'] not in {'done', 'cancelled'}:
            validate_report_references(root, task, f"Task {task['id']}")
        require_refs([task['milestone']], gates, f"Task {task['id']}")
        require_refs(task['checks'], checks, f"Task {task['id']}")
        require_refs(task['decision_refs'], decisions, f"Task {task['id']}")
        for path in task['scope']:
            local_path(root, path)
        if task['status'] in {'planned', 'active', 'blocked'} and not task['next_action']:
            raise ProjectError(f"Unfinished task needs a next action: {task['id']}")
        if task['status'] == 'blocked' and not task['blocker']:
            raise ProjectError(f"Blocked task needs a reason: {task['id']}")
        if task['status'] != 'blocked' and task['blocker'] is not None:
            raise ProjectError(f"Non-blocked task retains a blocker: {task['id']}")
        if task['status'] == 'cancelled' and not task['closure_reason']:
            raise ProjectError(f"Cancelled task needs a reason: {task['id']}")
        if task['status'] == 'active':
            if 'plan' not in task:
                raise ProjectError(f"Active task needs a delivery plan: {task['id']}")
            if any(tasks[key]['status'] != 'done' for key in task['depends_on']):
                raise ProjectError(f"Active task has unfinished dependencies: {task['id']}")
            if any(decisions[key]['status'] != 'accepted' for key in task['decision_refs']):
                raise ProjectError(f"Active task relies on an unaccepted decision: {task['id']}")
        if task['status'] not in {'done', 'cancelled'} and any(checks[key]['status'] == 'retired' for key in task['checks']):
            raise ProjectError(f"Unfinished task depends on a retired check: {task['id']}")
        receipts = []
        for ref in task['evidence']:
            path = local_path(root, ref['path'])
            if not ref['path'].startswith('project/evidence/') or not path.is_file():
                raise ProjectError(f"Missing archived task evidence: {ref['path']}")
            if digest(path.read_bytes()) != ref['sha256']:
                raise ProjectError(f"Evidence digest mismatch: {ref['path']}")
            receipt = read_json(path)
            validate_model(root, 'Evidence', receipt)
            if receipt['task_id'] != task['id']:
                raise ProjectError(f"Evidence belongs to another task: {task['id']}")
            observed = indexed(receipt['checks'], 'evidence check')
            require_refs(observed, checks, 'Evidence checks')
            for check_id in observed:
                if checks[check_id]['platforms'] == 'windows-x64' and not windows_x64(receipt['environment']):
                    raise ProjectError(f'Windows evidence has a non-Windows environment: {check_id}')
            receipts.append(receipt)
        if task['status'] == 'done':
            if any(tasks[key]['status'] != 'done' for key in task['depends_on']):
                raise ProjectError(f"Done task has unfinished dependencies: {task['id']}")
            definition = task_definition_hash(task)
            supported = any(
                receipt['task_definition_sha256'] == definition
                and set(task['checks']).issubset(check['id'] for check in receipt['checks'])
                for receipt in receipts
            )
            if not supported:
                raise ProjectError(f"Done task lacks passing evidence for its definition: {task['id']}")
            if task['next_action'] is not None:
                raise ProjectError(f"Done task retains a next action: {task['id']}")
    if any(not any(task['milestone'] == gate for task in tasks.values()) for gate in gates):
        raise ProjectError('Every declared milestone needs a tracked task')
    focus = ledger['focus_task_id']
    if focus is not None and (focus not in tasks or tasks[focus]['status'] in {'done', 'cancelled'}):
        raise ProjectError('The focus must name an unfinished task')
    if focus is None and any(task['status'] not in {'done', 'cancelled'} for task in tasks.values()):
        raise ProjectError('Unfinished work needs a focus task')
    active = [task['id'] for task in tasks.values() if task['status'] == 'active']
    if active and focus not in active:
        raise ProjectError('The focus must identify active work when active tasks exist')
    project = {'spec': spec, 'ledger': ledger, 'catalog': catalog, 'tasks': tasks, 'checks': checks,
               'decisions': decisions, 'decision_paths': decision_paths, 'replacements': replacements}
    validate_delivery(root, project)
    primary = spec.get('ai', {}).get('model_connections', {}).get('primary_cloud_route')
    if primary is not None:
        if (not isinstance(primary, dict) or not isinstance(primary.get('task'), str)
                or not isinstance(primary.get('check'), str) or primary['task'] not in tasks
                or primary.get('check') not in tasks[primary['task']]['checks']
                or primary['check'] not in project['delivery']['release_policy']['required_checks']):
            raise ProjectError('The primary cloud route needs a delivery task and a required release check')
    decision_inputs = spec.get('repository', {}).get('owner_decision_inputs', [])
    if decision_inputs:
        project['owner_decisions'] = {}
        for ref in decision_inputs:
            if (not isinstance(ref, dict) or not isinstance(ref.get('path'), str)
                    or not ref['path'].startswith('contracts/reviews/') or not isinstance(ref.get('pointer'), str)
                    or not ref['pointer'].startswith('/')):
                raise ProjectError('Expected a repository owner-decision field reference')
            key = ref['path'] + '#' + ref['pointer']
            if key in project['owner_decisions']:
                raise ProjectError(f'Duplicate owner-decision input: {key}')
            try:
                project['owner_decisions'][key] = resolve_source(root, ref)
            except (OSError, ValueError, TypeError) as error:
                raise ProjectError(f'Invalid owner decision input {key}: {error}') from error
    development_path = spec.get('repository', {}).get('development')
    if development_path:
        if __package__:
            from .development import validate_record
        else:
            from development import validate_record
        record = read_json(local_path(root, development_path))
        validate_model(root, 'Development', record)
        try:
            validate_record(root, project, record)
        except (ValueError, OSError) as error:
            raise ProjectError(str(error)) from error
        project['development'] = record
    manifest_path = root / 'contracts/contract.json'
    project['manifest'] = read_json(manifest_path) if manifest_path.is_file() else {}
    return project


def validate_delivery(root, project):
    delivery = read_json(root / 'project/delivery.json')
    feedback = read_json(root / 'project/feedback.json')
    validate_model(root, 'Delivery', delivery)
    validate_model(root, 'Feedback', feedback)
    indexed(delivery['scaffold_maintenance']['periodic_reviews'], 'periodic review')
    tasks, checks = project['tasks'], project['checks']
    types = indexed(delivery['change_types'], 'change type')
    scenarios = indexed(delivery['scenarios'], 'scenario')
    items = indexed(feedback['items'], 'feedback')
    for path in delivery['context']:
        if not local_path(root, path).exists():
            raise ProjectError(f'Missing delivery context: {path}')
    for change in types.values():
        require_refs(change['checks'], checks, f"Change type {change['id']}")
    for task in tasks.values():
        plan = task.get('plan')
        if not plan:
            continue
        for path in plan['context']:
            local_path(root, path)
        # Closed plans keep the classifications and check bindings used at the time.
        if task['status'] in {'done', 'cancelled'}:
            continue
        require_refs(plan['change_types'], types, f"Task plan {task['id']}")
        required = {key for name in plan['change_types'] for key in types[name]['checks']}
        required.update(key for risk in plan['risks'] for key in risk['checks'])
        if not required.issubset(task['checks']):
            raise ProjectError(f"Task plan has uncovered risks or change types: {task['id']}: {sorted(required - set(task['checks']))}")
    for scenario in scenarios.values():
        require_refs(scenario['task_ids'], tasks, f"Scenario {scenario['id']}")
        require_refs(scenario['checks'], checks, f"Scenario {scenario['id']}")
        owned = {key for task_id in scenario['task_ids'] for key in tasks[task_id]['checks']}
        if not set(scenario['checks']).issubset(owned):
            raise ProjectError(f"Scenario has checks without a delivery task: {scenario['id']}")
        if all(tasks[key]['status'] == 'cancelled' for key in scenario['task_ids']):
            raise ProjectError(f"Scenario only has cancelled delivery tasks: {scenario['id']}")
        if any(checks[key]['status'] == 'retired' for key in scenario['checks']):
            raise ProjectError(f"Scenario uses a retired check: {scenario['id']}")
    for item in items.values():
        if item['status'] != 'addressed':
            validate_report_references(root, item, f"Feedback {item['id']}")
        require_refs(item['task_ids'], tasks, f"Feedback {item['id']}")
        require_refs(item['regression_checks'], checks, f"Feedback {item['id']}")
        if item['status'] == 'addressed':
            if not item['resolution'] or any(tasks[key]['status'] != 'done' for key in item['task_ids']):
                raise ProjectError(f"Addressed feedback needs completed corrective work and a resolution: {item['id']}")
        elif item['resolution'] is not None:
            raise ProjectError(f"Unresolved feedback cannot claim a resolution: {item['id']}")
        regression = set(item['regression_checks'])
        linked = [tasks[key] for key in item['task_ids']]
        missing = regression - {key for task in linked for key in task['checks']}
        if missing:
            if item['status'] == 'addressed':
                raise ProjectError(f"Addressed feedback lacks regression evidence: {item['id']}; missing {sorted(missing)}")
            raise ProjectError(f"Feedback {item['id']} has regression checks without a linked task: {sorted(missing)}")
        if item['status'] != 'addressed' and item['severity'] in {'major', 'critical'}:
            for task in linked:
                if task['status'] == 'active':
                    planned = {key for risk in task['plan']['risks'] for key in risk['checks']}
                    uncovered = regression.intersection(task['checks']) - planned
                    if uncovered:
                        raise ProjectError(f"Active task {task['id']} must cover feedback {item['id']} in its plan risks: {sorted(uncovered)}")
        if (item['status'] == 'deferred') != bool(item['deferral_reason']):
            raise ProjectError(f"Feedback deferral needs an explicit reason: {item['id']}")
    policy = delivery['release_policy']
    require_refs(policy['required_milestones'], {gate['id'] for gate in project['catalog']['milestones']}, 'Release milestones')
    require_refs(policy['required_checks'], checks, 'Release checks')
    if any(checks[key]['status'] == 'retired' for key in policy['required_checks']):
        raise ProjectError('Release policy depends on a retired check')
    project.update(delivery=delivery, feedback=feedback)


def release_checks(project):
    selected = {key for key, check in project['checks'].items()
                if check['status'] != 'retired' and check.get('cadence') != 'task'}
    catalog = project['catalog']
    delivery = project['delivery']
    selected.update(delivery['release_policy']['required_checks'])
    for binding in [*catalog['milestones'], *catalog['acceptance'], *delivery['scenarios']]:
        selected.update(binding['checks'])
    return selected


def summarize_verification(check_ids, results):
    statuses = [results.get(key, {}).get('status', 'blocked') for key in check_ids]
    return 'failed' if 'failed' in statuses else ('passed' if statuses and all(value == 'passed' for value in statuses) else 'blocked')


def product_progress(project, results):
    by_id = indexed(results, 'check result')
    primary = project['spec'].get('ai', {}).get('model_connections', {}).get('primary_cloud_route')
    if primary and by_id.get(primary['check'], {}).get('status') == 'passed':
        try:
            verify_primary_model(project, by_id)
        except ProjectError:
            key = primary['check']
            by_id[key] = {**by_id[key], 'status': 'blocked'}
    milestones = []
    for gate in project['catalog']['milestones']:
        tasks = [task for task in project['tasks'].values() if task['milestone'] == gate['id']]
        current_tasks = [task for task in tasks if task['status'] != 'cancelled'
                         and task.get('delivery', 'current-contract') == 'current-contract']
        validation = summarize_verification(gate['checks'], by_id)
        if not current_tasks:
            status = 'blocked'
        elif all(task['status'] == 'planned' for task in current_tasks):
            status = 'not_started'
        elif all(task['status'] == 'done' for task in current_tasks) and validation == 'passed':
            status = 'passed'
        elif any(task['status'] == 'blocked' for task in current_tasks):
            status = 'blocked'
        else:
            status = 'in_progress'
        milestones.append({'id': gate['id'], 'status': status, 'verification': validation,
                           'checks': gate['checks'], 'tasks': [task['id'] for task in tasks],
                           'later_task_ids': [task['id'] for task in tasks if task.get('delivery') == 'later'],
                           'cancelled_task_ids': [task['id'] for task in tasks if task['status'] == 'cancelled']})
    acceptance = [
        {'requirement': binding['requirement'], 'checks': binding['checks'],
         'status': summarize_verification(binding['checks'], by_id)}
        for binding in project['catalog']['acceptance']
    ]
    return milestones, acceptance


def maintenance_status(root=ROOT, today=None):
    path = root / 'project/dependencies.json'
    if not path.exists():
        return None
    maintenance = read_json(path)['maintenance']
    today = today or datetime.now(timezone.utc).date()
    return {'reviewed_on': maintenance['reviewed_on'], 'next_review_by': maintenance['next_review_by'],
            'review_due': date.fromisoformat(maintenance['next_review_by']) <= today,
            'next_action': 'Open a bounded maintenance task when due; run fresh dependency-review for integration.'}


def read_review_report(path, limit):
    if not path.is_file():
        raise FileNotFoundError('Expected a regular review observation file.')
    with path.open('rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError('The review input exceeds its size bound.')
    return json.loads(data), digest(data)


def compatibility_changes(root, before, after):
    if not maintenance.git_commit(before) or not maintenance.git_commit(after):
        raise ValueError('The compatibility comparison needs exact source revisions.')
    result = feedback_git(root, 'diff', '--no-renames', '--name-only', '-z', before, after, '--')
    if result.returncode:
        raise ValueError('The compatibility comparison source is unavailable locally.')
    return [os.fsdecode(path) for path in nul_fields(result.stdout)]


def compatibility_boundaries(project, paths):
    return maintenance.compatibility_boundaries(paths, project.get('development') or {}, project['delivery'])


def compatibility_initial_base(root, revision):
    captured = feedback_git(root, 'show', f'{revision}:{maintenance.COMPATIBILITY_BASELINE}')
    if captured.returncode:
        raise ValueError('The accepted compatibility baseline is unavailable.')
    return json.loads(captured.stdout)['baseline']['main']


def compatibility_base(root, item, revision='HEAD'):
    if item.get('review_record'):
        record, _ = read_review_report(local_path(root, item['review_record']), 64 * 1024)
        if record['evidence'].get('unverified_boundaries'):
            return record['evidence']['compared_from']
        return record['source_commit']
    return compatibility_initial_base(root, revision)


def compatibility_claim(root, project, receipts, before, through, observed_at):
    if (not maintenance.git_commit(before) or not maintenance.git_commit(through)
            or feedback_git(root, 'merge-base', '--is-ancestor', before, through).returncode):
        raise ValueError('No comparable accepted compatibility history is available.')
    if not isinstance(receipts, list) or len(receipts) > 16 or len(set(receipts)) != len(receipts):
        raise ValueError('Use up to sixteen distinct scoped task receipts.')
    pending = compatibility_boundaries(project, compatibility_changes(root, before, through))
    clocks = []
    originals = []
    observations = []
    for receipt in receipts:
        clock, sha, tested, covered = compatibility_observation(root, project, receipt)
        if feedback_git(root, 'merge-base', '--is-ancestor', before, tested).returncode:
            raise ValueError('The consumer receipt predates the compatibility comparison.')
        changed_since_check = set(compatibility_changes(root, tested, through))
        for path in covered.keys() - changed_since_check:
            pending.pop(path, None)
        clocks.append(clock)
        originals.append(sha)
        observations.append({'receipt': receipt, 'tested_source': tested,
                             'verified_boundaries': maintenance.boundary_summary(covered)})
    evidence = {'compared_from': before, 'observations': observations,
                'unverified_boundaries': maintenance.boundary_summary(pending)}
    if not receipts:
        if pending:
            return evidence, None, None
        evidence['observed_at'] = observed_at
        return evidence, maintenance.utc(observed_at), digest(json_bytes([before, through, observed_at]))
    return evidence, max(clocks), originals[0] if len(originals) == 1 else digest(json_bytes(originals))


def compatibility_observation(root, project, relative):
    if not re.fullmatch(r'project/evidence/[a-zA-Z0-9_-]+\.json', relative):
        raise ValueError('Use an existing task receipt from project/evidence.')
    receipt, sha = read_review_report(local_path(root, relative), 64 * 1024)
    task = project['tasks'].get(receipt['task_id'])
    if (not task or {'path': relative, 'sha256': sha} not in task['evidence']
            or receipt['task_definition_sha256'] != task_definition_hash(task)):
        raise ValueError('The receipt does not match the scoped accepted task.')
    passed = {item['id'] for item in receipt['checks'] if item['status'] == 'passed' and item['exit_code'] == 0}
    tested = receipt['source']
    source = tested['git_head']
    ci = receipt.get('ci')
    if ci and feedback_git(root, 'merge-base', '--is-ancestor', source, 'HEAD').returncode:
        # The archived Actions checkout can disappear; the accepted source ref
        # identifies the candidate whose consumer checks the receipt binds.
        source = ci['head_sha']
    if (tested['working_tree_dirty'] or not maintenance.git_commit(source)
            or feedback_git(root, 'merge-base', '--is-ancestor', source, 'HEAD').returncode):
        raise ValueError('The receipt needs a clean, locally comparable accepted source.')
    parents = feedback_git(root, 'show', '-s', '--format=%P', source).stdout.decode('ascii').split()
    if not parents:
        raise ValueError('The receipt source has no comparable parent.')
    changes = sorted({path for parent in parents for path in compatibility_changes(root, parent, source)
                      if maintenance.in_scope(path, task['scope'])})
    scopes = compatibility_boundaries(project, changes)
    supported = {path: scope for path, scope in scopes.items() if scope[1] and set(scope[1]).issubset(passed)}
    if not supported:
        raise ValueError('The receipt has no verified consumer check for a changed boundary in the task scope.')
    candidates = [receipt]
    if ci:
        for ref in task['evidence']:
            candidate = read_json(local_path(root, ref['path']))
            if candidate.get('ci') == ci and candidate['task_definition_sha256'] == receipt['task_definition_sha256']:
                candidates.append(candidate)
    observed = []
    for candidate in candidates:
        run = re.fullmatch(r'(\d{8}T\d{6}\.\d{6}Z)-[a-f0-9]{8}', candidate['run_id'])
        if run:
            observed.append(datetime.strptime(run[1], '%Y%m%dT%H%M%S.%fZ').replace(tzinfo=timezone.utc))
    if not observed:
        raise ValueError('No dated harness observation was retained for this receipt.')
    return min(observed), sha, source, supported


def periodic_policy(root, source):
    if source is None:
        source = feedback_commit(root, 'HEAD')
    response = feedback_git(root, 'show', f'{source}:project/branch-protection.json')
    if response.returncode:
        raise ValueError('The observation source has no protected repository policy.')
    return json.loads(response.stdout)


def project_periodic_facts(root, key, facts, source):
    if __package__:
        from .protection import violations
    else:
        from protection import violations
    return maintenance.project_facts(key, facts, periodic_policy(root, source), violations)


def periodic_record_facts(root, key, record):
    if key == 'compatibility-and-recovery':
        evidence = record['evidence']
        return evidence, sorted(row['receipt'] for row in evidence['observations']) or [
            evidence['compared_from'], evidence['observed_at']], False, [record['report_sha256']]
    facts = record['facts']
    if (digest(json_bytes(facts)) != record['report_sha256'] or record['source_commit'] != facts['source_commit']
            or record['observed_at'] != facts['observed_at']):
        raise ValueError('The record does not match its hash-bound observation facts.')
    evidence, identity, adverse = project_periodic_facts(root, key, facts, record['source_commit'])
    return evidence, identity, adverse, facts['input_sha256']


class ObservationIntakePending(ValueError):
    """A proposed projection has not yet arrived through protected review."""


def require_protected_observation(root, relative, record_sha):
    head = feedback_commit(root, 'HEAD')
    protected = protected_feedback_base(root, head).get('commit')
    if protected:
        accepted = feedback_git(root, 'show', f'{protected}:{relative}')
        if accepted.returncode == 0 and digest(accepted.stdout) == record_sha:
            return protected
    raise ObservationIntakePending(
        'Verify the supplied originals with verify-maintenance and retain the exact record through protected intake before attaching it.')


def traced_branch_tips(root, data, disposition_reference):
    """Use the inventory's PR links and independently recoverable exact-head dispositions."""
    if __package__:
        from .worktree_retirement import read_dispositions
    else:
        from worktree_retirement import read_dispositions
    traced = {row['sha'] for row in data['branches'] if row['status'] == 'in_use'
              and (row.get('open_pr_heads') or row.get('open_pr_bases'))}
    unique = {row['sha'] for row in data['branches'] if row['status'] == 'unique_history'}
    target = data['default_branch']['sha']
    if not maintenance.git_commit(target):
        raise ValueError('The branch inventory needs an exact default tip.')
    ledger = feedback_git(root, 'show', f'{target}:project/tasks.json') if unique else None
    if ledger is not None and not ledger.returncode:
        for task in json.loads(ledger.stdout)['tasks']:
            if task['status'] not in {'active', 'planned', 'blocked'}:
                continue
            for ref in task['evidence']:
                captured = feedback_git(root, 'show', f"{target}:{ref['path']}")
                if captured.returncode or digest(captured.stdout) != ref['sha256']:
                    continue
                receipt = json.loads(captured.stdout)
                tested = (receipt.get('ci') or {}).get('head_sha') or receipt['source']['git_head']
                if tested in unique and receipt['task_definition_sha256'] == task_definition_hash(task):
                    traced.add(tested)
    if not disposition_reference:
        return traced, None
    try:
        commit, entries = read_dispositions(root, disposition_reference)
    except (WorktreeError, ValueError, subprocess.SubprocessError) as error:
        raise ValueError('Expected an existing exact-head recovery disposition reference.') from error
    raw = feedback_git(root, 'show', disposition_reference)
    for head, entry in entries.items():
        ref = entry['recoverable_ref']
        if any(head == row['sha'] and ref in {f"refs/heads/{row['name']}", f"refs/remotes/origin/{row['name']}"}
               for row in data['branches']):
            continue
        tip = feedback_git(root, 'rev-parse', '--verify', '--quiet', ref)
        if (not tip.returncode and not feedback_git(root, 'merge-base', '--is-ancestor', head, ref).returncode
                and (not feedback_git(root, 'merge-base', '--is-ancestor', commit, ref).returncode
                     or not feedback_git(root, 'merge-base', '--is-ancestor', commit, target).returncode)):
            traced.add(head)
    return traced, digest(raw.stdout)


def product_delivery_observation(root, project, record, observed_at):
    """Compare independently recorded product clocks with their accepted verification scope."""
    rows = record['slices']
    if record['kind'] != 'scopehub-product-delivery-timing-v1' or not 2 <= len(rows) <= 32:
        raise ValueError('Supply two or more comparable product slices from retained observations.')
    scenarios = indexed(project['delivery']['scenarios'], 'scenario')
    scenario = scenarios[record['scenario']]
    tasks = set()
    approved_repository = periodic_policy(root, None)['repository']
    usable_seconds, main_seconds = [], []
    for row in rows:
        task_id = row['task_id']
        if task_id not in scenario['task_ids'] or task_id in tasks:
            raise ValueError('Product timing needs distinct slices in the named delivery scenario.')
        tasks.add(task_id)
        task = project['tasks'][task_id]
        receipts = {}
        for field in ('usable_receipt', 'verified_main_receipt'):
            relative = row[field]
            if not any(ref['path'] == relative for ref in task['evidence']):
                raise ValueError('Product timing requires accepted receipts from the same slice.')
            receipts[field] = read_json(local_path(root, relative))
        usable = receipts['usable_receipt']
        main = receipts['verified_main_receipt']
        passed = [{check['id'] for check in receipt['checks'] if check['status'] == 'passed'}
                  for receipt in (usable, main)]
        ci = main.get('ci') or {}
        if (not set(scenario['checks']).intersection(*passed)
                or (ci.get('repository'), ci.get('event'), ci.get('head_sha')) !=
                (approved_repository, 'push', main['source']['git_head'])
                or not observation_source_available(root, 'compatibility-and-recovery', main['source']['git_head'])
                or any(receipt['source']['working_tree_dirty'] for receipt in receipts.values())):
            raise ValueError('The receipts must cover the usable product check and its verified main push.')
        began, interaction, opened, integrated = (maintenance.utc(row[field]) for field in
                                                   ('work_started_at', 'usable_at', 'pr_created_at', 'verified_main_at'))
        if not (began <= interaction <= observed_at and began <= opened <= integrated <= observed_at):
            raise ValueError('Product timing clocks are missing or out of order.')
        for clock, receipt in ((interaction, usable), (integrated, main)):
            if not (clock <= maintenance.utc(receipt['recorded_at'])):
                raise ValueError('A product timing observation follows its verification receipt.')
        usable_seconds.append((interaction - began).total_seconds())
        main_seconds.append((integrated - opened).total_seconds())
    return {'scenario': record['scenario'], 'slices': len(tasks),
            'work_to_usable_median_seconds': median(usable_seconds),
            'pr_to_verified_main_median_seconds': median(main_seconds)}


def verify_maintenance(root, project, path, reports, staging, staging_digest, dispositions=None):
    try:
        record, sha = read_review_report(path, 64 * 1024)
        key = record['review_id']
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise ValueError('Expected a valid proposed maintenance record.') from error
    if key == 'compatibility-and-recovery':
        raise ValueError('Compatibility observations already use committed source and task receipts.')
    regenerated = observe_maintenance(root, project, key, reports, [row['id'] for row in record['findings']],
                                      staging=staging, staging_digest=staging_digest, dispositions=dispositions)
    if regenerated != record:
        raise ValueError('The proposed observation differs from the projection of the supplied originals.')
    return {'status': 'originals_verified', 'review_id': key, 'record_sha256': sha,
            'input_sha256': regenerated['facts']['input_sha256'],
            'source_commit': regenerated['source_commit'],
            'source_scope': ('No producer capture checkout was recorded; verification covers the supplied observations.'
                             if regenerated['source_commit'] is None else 'Capture identity from the supplied original.'),
            'next_action': 'Independently review and retain these exact record bytes through protected intake before raising the claimed date.'}


def review_record(root, project, item):
    relative = item.get('review_record')
    if not relative:
        if item['reviewed_on'] == maintenance.REVIEW_SOURCES[item['id']][0]:
            return None
        raise ValueError('The date has no matching observation.')
    if not re.fullmatch(r'contracts/reviews/[a-zA-Z0-9_-][a-zA-Z0-9._-]*\.json', relative):
        raise ValueError('Expected a tracked periodic review path.')
    path = local_path(root, relative)
    if (root / relative).is_symlink():
        raise ValueError('Expected a regular periodic review record.')
    record, record_sha = read_review_report(path, 64 * 1024)
    key = item['id']
    if (record['kind'] != maintenance.REVIEW_KIND or record['review_id'] != key
            or not re.fullmatch('[a-f0-9]{64}', record['report_sha256'])
            or not observation_source_available(root, key, record['source_commit'])):
        raise ValueError('The observation category, source or original digest does not match.')
    observed = maintenance.utc(record['observed_at'])
    if observed.date().isoformat() != item['reviewed_on'] or observed > datetime.now(timezone.utc):
        raise ValueError('The review date must match the actual observation time.')
    evidence, identity, adverse, original_digests = periodic_record_facts(root, key, record)
    if evidence != record['evidence']:
        raise ValueError('The claim does not match the observation facts.')
    if key != 'compatibility-and-recovery':
        require_protected_observation(root, relative, record_sha)
    if key == 'compatibility-and-recovery':
        receipts = [entry['receipt'] for entry in evidence['observations']]
        scoped, clock, sha = compatibility_claim(root, project, receipts, evidence['compared_from'],
                                                  record['source_commit'], record['observed_at'])
        if evidence != scoped or clock != observed or sha != record['report_sha256']:
            raise ValueError('The compatibility observation does not match the compared history and scoped receipts.')
    links = record['findings']
    if not isinstance(links, list) or len(links) > 32 or (adverse and not links):
        raise ValueError('An adverse observation requires linked feedback.')
    feedback = {entry['id']: entry for entry in project['feedback']['items']}
    found = {}
    for link in links:
        entry = feedback.get(link['id'])
        if (not entry or link['id'] in found or not re.fullmatch('[a-f0-9]{64}', link['fingerprint'])):
            raise ValueError('Expected unique existing feedback occurrences.')
        found[link['id']] = entry
    if adverse and not any(observation_linked(sha, entry) for sha in original_digests for entry in found.values()):
        raise ValueError('Bind the adverse observation digest to its canonical feedback.')
    return record, identity, adverse, found


REVIEW_ERRORS = (OSError, ValueError, KeyError, TypeError, OverflowError, AttributeError, RecursionError, ProjectError)


def retirement_successor(project, item, pending):
    if not (task_id := item.get('follow_through_task')):
        return None
    task = project['tasks'].get(task_id)
    states = {'active', 'planned', 'blocked'} if pending else {'active', 'planned', 'blocked', 'done'}
    if (not item.get('retired_reason') or not task or task['status'] not in states
            or not task.get('plan') or 'project/delivery.json' not in task['scope']):
        raise ValueError('Pending review scope needs an unfinished task with a maintenance plan and policy scope.')
    return task_id


def outstanding_branch_tips(root, item, record):
    pending = set()
    for path in [*item.get('review_history', []), item['review_record']]:
        observation = record if path == item['review_record'] else read_review_report(local_path(root, path), 64 * 1024)[0]
        facts = observation['facts']
        pending.update(facts['unique_tips'])
        pending.difference_update(facts['traced_tips'])
    return pending


def review_obligations(root, key, item, result):
    if not result:
        pending = [f'{key} scheduled observation']
        if key == 'worktrees-and-evidence':
            pending.append(maintenance.EXTERNAL_STAGING)
        return pending
    record, _, adverse, found = result
    pending = list(record['evidence'].get('pending') or record['evidence'].get('unverified_boundaries') or ())
    if key == 'branches-and-pull-requests' and outstanding_branch_tips(root, item, record) and maintenance.BRANCH_TRACE not in pending:
        pending.append(maintenance.BRANCH_TRACE)
    if adverse:
        pending.append('matching follow-up for the adverse observation')
    pending.extend(link['id'] for link in record['findings']
                   if found[link['id']]['status'] != 'addressed'
                   or feedback_fingerprint(found[link['id']]) != link['fingerprint'])
    return pending


def accepted_review_paths(root, old, candidate, base):
    previous = old.get('review_record')
    prior_paths = old.get('review_history', [])
    paths = candidate.get('review_history', [])
    current = candidate.get('review_record')
    replaced = previous is not None and current != previous
    required = [*prior_paths, *([previous] if replaced else [])]
    if paths[:len(required)] != required or (previous and not replaced and paths != required):
        raise ValueError('Keep every admitted predecessor in order in review_history.')
    if current in paths or len(paths) != len(set(paths)) or (paths and not current):
        raise ValueError('Keep one current observation separate from its predecessors.')
    for path in dict.fromkeys([*prior_paths, *([previous] if previous else [])]):
        original = feedback_git(root, 'show', f'{base}:{path}')
        _, sha = read_review_report(local_path(root, path), 64 * 1024)
        if original.returncode or sha != digest(original.stdout):
            raise ValueError('Every admitted predecessor must remain unchanged.')
    return paths[len(prior_paths):]


def validate_review_progress(root, key, previous, current, feedback):
    old_evidence, old_identity, old_adverse, old_inputs = periodic_record_facts(root, key, previous)
    new_evidence, new_identity, new_adverse, new_inputs = periodic_record_facts(root, key, current)
    old_clock = maintenance.utc(previous['observed_at'])
    new_clock = maintenance.utc(current['observed_at'])
    new_links = {link['id']: link['fingerprint'] for link in current['findings']}
    required = {link['id'] for link in previous['findings'] if feedback[link['id']]['status'] != 'addressed' or old_adverse}
    if not required.issubset(new_links):
        raise ValueError('Follow through on the previously observed findings.')
    if new_clock.date() > old_clock.date():
        if new_identity == old_identity or current['report_sha256'] == previous['report_sha256']:
            raise ValueError('The new date needs a fresh comparable observation.')
        return
    inputs = set(new_inputs) - set(old_inputs)
    previous_links = {link['id']: link['fingerprint'] for link in previous['findings']}
    added_finding = any(
        previous_links.get(identity) != fingerprint and fingerprint == feedback_fingerprint(feedback[identity])
        and any(observation_linked(sha, feedback[identity]) for sha in inputs)
        for identity, fingerprint in new_links.items())
    if not maintenance.same_day_progress(previous, current, old_evidence, new_evidence,
                                         new_adverse, old_adverse, inputs, added_finding):
        raise ValueError('Keep the accepted observation or add a later same-day improvement or bound adverse finding.')


def historical_review(root, project, key, path):
    record, _ = read_review_report(local_path(root, path), 64 * 1024)
    day = maintenance.utc(record['observed_at']).date().isoformat()
    return review_record(root, project, {'id': key, 'review_record': path, 'reviewed_on': day})[0]


def require_periodic_transition(root, project, base, only=None):
    prior = feedback_git(root, 'show', f'{base}:project/delivery.json')
    if prior.returncode:
        return
    current = maintenance.periodic_records(project['delivery'])
    previous = maintenance.periodic_records(json.loads(prior.stdout))
    today = datetime.now(timezone.utc).date()
    feedback = indexed(project['feedback']['items'], 'feedback')
    for key in sorted(previous.keys() if only is None else previous.keys() & {only}):
        old, candidate = previous[key], current.get(key, {})
        if candidate.get('reviewed_on', '') < old['reviewed_on']:
            raise ProjectError(f'Periodic review {key} lost its accepted date. Retain the history when retiring it.')
        try:
            date_advanced = candidate['reviewed_on'] > old['reviewed_on']
            if key not in maintenance.REVIEW_SOURCES:
                if date_advanced or candidate.get('retired_reason'):
                    raise ValueError('Define how the new rule verifies and retains its obligations before changing its disposition.')
                continue
            result = review_record(root, project, candidate)
            extra_paths = accepted_review_paths(root, old, candidate, base)
            prior_path = old.get('review_record')
            pending = review_obligations(root, key, candidate, result)
            changed = prior_path != candidate.get('review_record')
            if changed and result:
                chain = [historical_review(root, project, key, path) for path in extra_paths]
                chain.append(result[0])
                for before, after in zip(chain, chain[1:]):
                    validate_review_progress(root, key, before, after, feedback)
            if date_advanced and key == 'compatibility-and-recovery':
                accepted_source = compatibility_initial_base(root, base)
                if prior_path:
                    original = json.loads(feedback_git(root, 'show', f'{base}:{prior_path}').stdout)
                    evidence = original['evidence']
                    accepted_source = evidence['compared_from'] if evidence['unverified_boundaries'] else original['source_commit']
                if result[0]['evidence']['compared_from'] != accepted_source:
                    raise ValueError('Compare compatibility from the last fully reviewed source.')
                relevant = compatibility_boundaries(project, compatibility_changes(
                    root, result[0]['source_commit'], feedback_commit(root, 'HEAD')))
                if relevant:
                    raise ValueError('The compatibility observation predates relevant source changes.')
            successor = retirement_successor(project, candidate, pending)
            if old.get('retired_reason') and (not candidate.get('retired_reason') or old.get('follow_through_task') != successor):
                raise ValueError('Keep the accepted retirement and its successor identity.')
            if old.get('retired_reason') and changed and prior_path:
                prior_record = historical_review(root, project, key, prior_path)
                old_result = (prior_record, *periodic_record_facts(root, key, prior_record)[1:3],
                              {link['id']: feedback[link['id']] for link in prior_record['findings']})
                if not review_obligations(root, key, old, old_result):
                    raise ValueError('The transferred review was already discharged at its accepted observation.')
            if not date_advanced and not old.get('retired_reason'):
                previous_due = date.fromisoformat(old['reviewed_on']) + timedelta(days=old['interval_days'])
                postpones = candidate['interval_days'] > old['interval_days'] or candidate.get('retired_reason')
                if postpones and previous_due <= today and not successor:
                    raise ValueError('A policy change cannot postpone an overdue review.')
                if candidate['interval_days'] > old['interval_days'] and result and pending and not successor:
                    raise ValueError('A policy change cannot postpone an unresolved review.')
            if candidate.get('retired_reason') and not old.get('retired_reason') and not successor:
                if key == 'worktrees-and-evidence' or (result and pending):
                    raise ValueError('Resolve the recorded obligations or transfer them to a scoped successor task, including external staging.')
                if key == 'compatibility-and-recovery' and compatibility_boundaries(
                        project, compatibility_changes(root, compatibility_base(root, candidate, base),
                                                       feedback_commit(root, 'HEAD'))):
                    raise ValueError('Retain the compatibility review until changed consumers are assessed.')
        except REVIEW_ERRORS as error:
            raise ProjectError(f'Periodic review {key}: {error}') from error


def observed_periodic_reviews(root, project, *, compare=False, timing_recovery=()):
    reviews = periodic_reviews(project['delivery'])
    head = feedback_commit(root, 'HEAD') if compare and (root / '.git').exists() else None
    bases = dict.fromkeys([head, protected_feedback_base(root, head).get('commit') if head else None])
    present = {item['id'] for item in reviews}
    for base in filter(None, bases):
        original = feedback_git(root, 'show', f'{base}:project/delivery.json')
        if not original.returncode:
            missing = maintenance.periodic_records(json.loads(original.stdout)).keys() - present
            for key in sorted(missing):
                reviews.append({'id': key, 'state': 'evidence_invalid', 'review_due': True,
                                'next_review_by': None, 'owner': 'coordinator', 'command': maintenance.POLICY_COMMAND,
                                'problem': 'Restore the retained periodic rule and its obligations.'})
            present.update(missing)
    for item in reviews:
        key = item['id']
        if item.get('state') == 'evidence_invalid':
            continue
        if key not in maintenance.REVIEW_SOURCES:
            item.update(state='policy_needed', owner='coordinator', command=maintenance.POLICY_COMMAND,
                        trigger='New periodic rule needs an observation and follow-through policy')
            continue
        item.update(owner='coordinator', trigger='Next comparable observation or scheduled review',
                    command=maintenance.REVIEW_SOURCES[key][1])
        try:
            result = review_record(root, project, item)
            obligations = review_obligations(root, key, item, result)
            successor = retirement_successor(project, item, obligations)
            if item.get('retired_reason') and key == 'worktrees-and-evidence' and not successor:
                raise ValueError('External staging requires an explicit successor task.')
            for base in filter(None, bases):
                require_periodic_transition(root, project, base, only=key)
        except ObservationIntakePending as error:
            item.update(state='intake_needed', review_due=True, next_review_by=None, problem=str(error),
                        command='uv run python scripts/project.py verify-maintenance --help')
            continue
        except REVIEW_ERRORS as error:
            item.update(state='evidence_missing' if not item.get('review_record') or isinstance(error, FileNotFoundError)
                        else 'evidence_invalid', review_due=True, next_review_by=None,
                        problem='The referenced observation file is missing.' if isinstance(error, FileNotFoundError)
                        else str(error))
            continue
        if item.get('retired_reason'):
            if result:
                item.update(observed_at=result[0]['observed_at'], report_sha256=result[0]['report_sha256'])
            if successor and obligations:
                item.update(state='owned_action', review_due=True, owner='development AI', next_review_by=None,
                            problem='Transferred obligations: ' + ', '.join(obligations),
                            trigger='Assess the inherited review scope at the next matching observation',
                            command=f'pnpm project:context {successor} --phase verify')
            else:
                item.update(state='retired', review_due=False, next_review_by=None)
            continue
        if not result:
            item['state'] = 'due' if item['review_due'] else 'historical_schedule'
            continue
        record, _, adverse, found = result
        item.update(observed_at=record['observed_at'], report_sha256=record['report_sha256'],
                    provenance='protected_intake' if key != 'compatibility-and-recovery' else 'committed_receipts',
                    state='due' if item['review_due'] else 'observed')
        pending = record['evidence'].get('unverified_boundaries') or record['evidence'].get('pending')
        if key == 'worktrees-and-evidence' and record['evidence'].get('verified_external_staging'):
            item['overseen_staging'] = record['evidence']['verified_external_staging']
        if key == 'compatibility-and-recovery':
            new_paths = compatibility_boundaries(project, compatibility_changes(
                root, record['source_commit'], feedback_commit(root, 'HEAD')))
            if new_paths:
                pending = sorted(set(pending or ()) | set(maintenance.boundary_summary(new_paths)))
        if key == 'branches-and-pull-requests' and outstanding_branch_tips(root, item, record):
            pending = list(dict.fromkeys([*(pending or ()), maintenance.BRANCH_TRACE]))
        if pending:
            item['scope_follow_through'] = [
                {'scope': scope, 'owner': route[0], 'trigger': route[1], 'command': route[2]}
                for scope in pending if (route := maintenance.SCOPE_ACTIONS.get(scope))]
            item.update(state='checks_needed', review_due=True, next_review_by=None,
                        problem='Unverified observations: ' + ', '.join(pending))
            if key == 'branches-and-pull-requests' and 'effective protection at the inventoried revision' in pending:
                item['command'] = 'pnpm project:protection'
            elif key == 'worktrees-and-evidence' and pending == [maintenance.EXTERNAL_STAGING]:
                item['command'] = 'uv run python scripts/project.py observe-maintenance worktrees-and-evidence --help'
        for link in record['findings']:
            entry = found[link['id']]
            active = next((task_id for task_id in entry['task_ids'] if project['tasks'][task_id]['status'] in
                           {'active', 'planned'}), None)
            if feedback_fingerprint(entry) != link['fingerprint']:
                item.update(state='recheck_needed', finding=entry['id'])
                break
            if entry['status'] == 'open' and active:
                item.update(state='owned_action', owner='development AI', finding=entry['id'],
                            command=f'pnpm project:context {active} --phase verify')
                break
            if entry['status'] == 'open':
                item.update(state='recheck_needed', finding=entry['id'])
                break
            if key == 'verification-timing' and entry['status'] == 'addressed' and entry.get('verification_budget'):
                recovered = maintenance.producer_recovery(entry, timing_recovery)
                item['repair_recovery'] = ({'state': 'repair_observed', **recovered} if recovered
                                           else {'state': 'waiting_for_event', 'finding': entry['id']})
                if recovered and not adverse:
                    item['finding'] = entry['id']
                    if not pending and not item['review_due']:
                        item['state'] = 'repair_observed'
                    continue
            if entry['status'] in {'deferred', 'addressed'}:
                task = next(iter(entry['task_ids']), None)
                disposition = 'checks_needed' if pending else 'waiting_for_event'
                if adverse and entry['status'] == 'addressed':
                    disposition = 'recheck_needed'
                item.update(state=disposition, finding=entry['id'],
                            trigger='Verify the matching follow-up through its original producer')
                if key != 'verification-timing' and task:
                    item['command'] = f'pnpm project:context {task} --phase verify'
    return reviews


def require_periodic_reviews(root, project):
    for item in observed_periodic_reviews(root, project):
        if item.get('state') in {'evidence_missing', 'evidence_invalid', 'intake_needed'}:
            raise ProjectError(f"Periodic review {item['id']}: {item['problem']}")


def observation_linked(sha, entry):
    return sha in entry['source'] or any(row['report_sha256'] == sha for row in entry.get('diagnostic_reports', []))


def observation_source_available(root, key, source):
    if source is None:
        return key == 'verification-timing'
    return maintenance.git_commit(source) and not feedback_git(
        root, 'merge-base', '--is-ancestor', source, 'HEAD').returncode


def observe_maintenance(root, project, key, reports, finding_ids, *, staging=None, staging_digest=None,
                        dispositions=None):
    try:
        if key not in maintenance.REVIEW_SOURCES:
            raise ValueError('Unknown periodic review.')
        source = feedback_commit(root, 'HEAD')
        if not source:
            raise ValueError('A review needs a local source checkpoint.')
        if bool(staging) != bool(staging_digest) or (staging and key != 'worktrees-and-evidence'):
            raise ValueError('Named staging needs both an archive and its manifest digest for the worktree review.')
        if dispositions and key != 'branches-and-pull-requests':
            raise ValueError('Use --dispositions only with a branch inventory.')
        facts = None
        if key == 'compatibility-and-recovery':
            item = maintenance.periodic_records(project['delivery'])[key]
            before = compatibility_base(root, item)
            receipts = [path.resolve().relative_to(root.resolve()).as_posix() for path in reports]
            data, observed, sha = compatibility_claim(root, project, receipts, before, source,
                                                       datetime.now(timezone.utc).isoformat())
            if observed is None:
                return {'status': 'checks_needed', 'compared_sources': [before, source],
                        'changed_boundaries': data['unverified_boundaries'],
                        'next_action': 'Run the checks for changed consumers under the responsible task and use --report with its receipt.'}
            evidence, adverse, original_digests = data, False, [sha]
        else:
            accepts_pair = key in {'branches-and-pull-requests', 'verification-timing'}
            if len(reports) != 1 and not (accepts_pair and len(reports) == 2):
                raise ValueError('Supply the producer observation and its applicable supplemental observation.')
            data, sha = read_review_report(reports[0], 16 * 1024 * 1024)
            protection = product_timing = None
            traced_tips = set()
            original_digests = [sha]
            if len(reports) == 2:
                supplement, supplement_sha = read_review_report(reports[1], 1024 * 1024)
                original_digests.append(supplement_sha)
                if key == 'branches-and-pull-requests':
                    protection = supplement
                else:
                    product_timing = product_delivery_observation(root, project, supplement,
                        maintenance.timing_observation_clock(data))
            if key == 'branches-and-pull-requests':
                source = data['default_branch']['sha']
                traced_tips, disposition_sha = traced_branch_tips(root, data, dispositions)
                if disposition_sha:
                    original_digests.append(disposition_sha)
            elif key == 'worktrees-and-evidence':
                source = data['integration_tip']
            elif key == 'weekly-regression-continuity':
                source = (data['weekly_regression']['run'] or {}).get('head_sha') or data['head_sha']
            elif key == 'verification-timing':
                source = (data.get('recent_collection') or {}).get('capture_source')
            if not observation_source_available(root, key, source):
                raise ValueError('The capture source from the original is unavailable in local accepted history.')
            stage = None
            if staging:
                try:
                    stage = maintenance.verify_staging_archive(staging, staging_digest)
                except OSError as error:
                    raise ValueError('A required file in the named staging archive is unavailable.') from error
                owner = project['tasks'].get(stage['task_id'], {})
                if (owner.get('status') != 'done' or not all(ref in owner['evidence'] for ref in stage['task_evidence'])
                        or feedback_git(root, 'merge-base', '--is-ancestor', stage['accepted_source'], 'HEAD').returncode):
                    raise ValueError('The named stage is missing its matching accepted task and source.')
                del stage['task_evidence']
                original_digests.append(staging_digest)
            facts = maintenance.prepare_facts(key, data, source, original_digests, periodic_policy(root, source),
                                              protection=protection, staging=stage, product_timing=product_timing,
                                              traced_tips=traced_tips)
            evidence, _, adverse = project_periodic_facts(root, key, facts, source)
            observed = maintenance.utc(facts['observed_at'])
            sha = digest(json_bytes(facts))
        if not observation_source_available(root, key, source):
            raise ValueError('The capture source from the original is unavailable in local accepted history.')
        if observed > datetime.now(timezone.utc):
            raise ValueError('An observation cannot be in the future.')
        feedback = {entry['id']: entry for entry in project['feedback']['items']}
        if len(set(finding_ids)) != len(finding_ids) or len(finding_ids) > 32:
            raise ValueError('Use up to 32 distinct feedback IDs.')
        findings = [{'id': key, 'fingerprint': feedback_fingerprint(feedback[key])} for key in finding_ids]
        if adverse and not any(observation_linked(digest, feedback[finding])
                               for digest in original_digests for finding in finding_ids):
            raise ValueError('Bind the adverse observation digest to its canonical feedback.')
        record = {'kind': maintenance.REVIEW_KIND, 'review_id': key, 'observed_at': observed.isoformat(),
                  'source_commit': source, 'report_sha256': sha, 'evidence': evidence, 'findings': findings}
        if facts is not None:
            record['facts'] = facts
        return record
    except REVIEW_ERRORS as error:
        raise ProjectError(f'Cannot record periodic review {key}: {error}') from error


def periodic_reviews(delivery, today=None):
    today = today or datetime.now(timezone.utc).date()
    reviews = []
    for item in delivery['scaffold_maintenance']['periodic_reviews']:
        due = date.fromisoformat(item['reviewed_on']) + timedelta(days=item['interval_days'])
        reviews.append({**item, 'next_review_by': due.isoformat(), 'review_due': today >= due})
    return reviews


def workfront(root=ROOT, *, report_path=None, retained_detail=None):
    if __package__:
        from .development import evidence_view, unassigned_feedback
        from .timing import offline_budget_status
    else:
        from development import evidence_view, unassigned_feedback
        from timing import offline_budget_status
    project = load_project(root)
    source = source_identity(root)
    report_path = report_path or '.reports/test-report.json'
    report_available = False
    report_error = None
    report = None
    try:
        report_path = checkout_report_path(root, report_path)
        report_available = report_path.is_file()
        if report_available:
            report = read_json(report_path)
            validate_model(root, 'HarnessReport', report)
    except ProjectError as error:
        report_error = str(error)
        report = None
    inputs_current = bool(report and report.get('source_unchanged')
                          and report.get('source', {}).get('sha256') == source['sha256'])
    tasks = project['tasks']
    tasks_current = bool(report and report.get('task_definitions') == {
        task['id']: task_definition_hash(task) for task in tasks.values()})
    project_hash = project_definition_hash(project)
    definitions_current = bool(report and report.get('project_unchanged')
                               and report.get('project_definition_sha256') == project_hash)
    current = inputs_current and tasks_current and definitions_current and report_error is None
    unresolved = [item for item in project['feedback']['items'] if item['status'] != 'addressed']
    budget = offline_budget_status(root, project['feedback']['items'], tasks)
    state = {
        'status': 'passed', 'focus': tasks.get(project['ledger']['focus_task_id']),
        'status_scope': 'Project-record consistency only; inspect verification, evidence_overview and release readiness separately.',
        'active_task_ids': [task['id'] for task in tasks.values() if task['status'] == 'active'],
        'ready_task_ids': [task['id'] for task in tasks.values() if task['status'] == 'planned'
                           and all(tasks[key]['status'] == 'done' for key in task['depends_on'])],
        'blocked': [{'id': task['id'], 'reason': task['blocker']} for task in tasks.values() if task['status'] == 'blocked'],
        'completed_task_ids': [task['id'] for task in tasks.values() if task['status'] == 'done'],
        'proposed_decisions': [item['id'] for item in project['decisions'].values() if item['status'] == 'proposed'],
        'decision_replacements': project['replacements'],
        'dependency_maintenance': maintenance_status(root),
        'periodic_reviews': observed_periodic_reviews(root, project, compare=True,
                                                      timing_recovery=budget.get('budget_recovery', ())),
        'purpose': project['spec'].get('product', {}).get('purpose'),
        'verification_budget': budget,
        'verification': {'report_available': report_available, 'matches_current_execution_inputs': inputs_current,
                         'matches_current_task_definitions': tasks_current,
                         'matches_current_project_definitions': definitions_current, 'current': current,
                         'report_error': report_error,
                         'run_id': report.get('run_id') if report else None,
                         'recorded_status': report.get('status') if report else None},
        'milestones': product_progress(project, report['checks'])[0] if current else None,
        'evidence_overview': evidence_view(root, project, source, report, current, report_error=report_error),
        'unresolved_feedback': unresolved,
        'feedback_needing_assignment': [
            item['id'] for item in unassigned_feedback(unresolved, tasks)
        ],
        'source': source,
    }
    if retained_detail is not None:
        state['retained_failures'] = retained_failure_view(
            root, (source['sha256'], project_hash), basis='current_checkout', detail=retained_detail,
            latest_run=report.get('run_id') if report else None,
        )
    return state


def task_context(root=ROOT, task_id=None, *, detail=False, phase='implement'):
    if __package__:
        from .development import (
            bound_review_navigation, causal_context, effective_decisions, feedback_for_task, focused_context,
            project_navigation,
        )
    else:
        from development import (
            bound_review_navigation, causal_context, effective_decisions, feedback_for_task, focused_context,
            project_navigation,
        )
    project = load_project(root)
    key = task_id or project['ledger']['focus_task_id']
    if key not in project['tasks']:
        raise ProjectError(f'Unknown task: {key}')
    task = project['tasks'][key]
    if phase not in {'implement', 'verify', 'handoff'}:
        raise ProjectError(f'Unknown context phase: {phase}')
    source_commit = getattr(root, 'commit', None)
    if not detail:
        context = focused_context(project, key, phase, source_commit=source_commit)
        reviews = project['delivery']['scaffold_maintenance']['periodic_reviews']
        transfers = [(index, row) for index, row in enumerate(reviews) if row.get('follow_through_task') == key]
        if transfers and source_commit is not None:
            context['transferred_reviews'] = [
                {'id': row['id'], 'scope': 'recorded transfer policy',
                 'policy_command': project_navigation('source', 'project/delivery.json',
                    f'/scaffold_maintenance/periodic_reviews/{index}', source_commit=source_commit)}
                for index, row in transfers
            ]
        elif transfers:
            context['transferred_reviews'] = [
                {'id': row['id'], 'obligations': row.get('problem'), 'trigger': row.get('trigger')}
                for row in observed_periodic_reviews(root, project, compare=True)
                if row.get('follow_through_task') == key and row['state'] == 'owned_action'
            ]
        return context
    decision_ids = effective_decisions(project, task)
    result = {
        'task': task,
        'development': project.get('development'),
        'context_paths': list(dict.fromkeys([*project['delivery']['context'], *task.get('plan', {}).get('context', [])])),
        'dependencies': [project['tasks'][item] for item in task['depends_on']],
        'decisions': [project['decisions'][item] for item in sorted(decision_ids)],
        'checks': [project['checks'][item] for item in task['checks']],
        'scenarios': [item for item in project['delivery']['scenarios'] if key in item['task_ids']],
        'feedback': [item for item in project['feedback']['items'] if key in item['task_ids']],
        'iteration_policy': project['delivery']['iteration_policy'],
        'scaffold_maintenance': project['delivery']['scaffold_maintenance'],
        'dependency_maintenance': maintenance_status(root),
        'delivery_decisions': {'integration': project['delivery']['source_control']['integration'],
                               'authorization': project['delivery']['source_control']['authorization'],
                               'release': project['delivery']['release_policy']},
        'commands': ['pnpm project:impact', 'pnpm check:changed', f'pnpm check --task {key}',
                     'pnpm project:doctor', 'pnpm check:fast', 'pnpm check',
                     f'pnpm project:recover --task {key} --save',
                     'pnpm project:checkpoint', 'pnpm project:ci', 'pnpm project:integration --pr <number>',
                     'pnpm project:history --base <reviewed-commit>', f'pnpm project:record {key} --report <run-report>',
                     'pnpm project:readiness'],
    }
    if source_commit is not None:
        result['phase'] = phase
        result.update(bound_review_navigation(key, source_commit, result['commands']))
        linked_feedback = feedback_for_task(project['feedback']['items'], key)
        if linked_feedback or task.get('plan', {}).get('hypotheses') or task.get('diagnostic_reports'):
            result['causal_investigation'] = causal_context(task, linked_feedback, source_commit)
    return result


def checkpoint(root=ROOT):
    state = workfront(root)
    env = checkout_git_environment()
    branch = subprocess.run(['git', 'symbolic-ref', '--quiet', '--short', 'HEAD'],
                            cwd=root, env=env, capture_output=True, text=True)
    upstream = subprocess.run(['git', 'rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{upstream}'],
                              cwd=root, env=env, capture_output=True, text=True)
    tracking = upstream.stdout.strip() if upstream.returncode == 0 else None
    ahead = behind = None
    if tracking:
        counts = subprocess.run(['git', 'rev-list', '--left-right', '--count', f'HEAD...{tracking}'],
                                cwd=root, env=env, capture_output=True, text=True, check=True).stdout.split()
        ahead, behind = map(int, counts)
    project = load_project(root)
    report = read_json(root / '.reports/test-report.json') if state['verification']['current'] else {}
    results = indexed(report.get('checks', []), 'check result')
    fast = project['catalog']['profiles']['fast']
    local_ready = all(results.get(key, {}).get('status') == 'passed' for key in fast)
    return {
        'head_sha': state['source']['git_head'], 'branch': branch.stdout.strip() or None,
        'worktree_dirty': state['source']['working_tree_dirty'], 'upstream': tracking,
        'ahead': ahead, 'behind': behind,
        'tracking_scope': 'Local tracking refs only; no network fetch or remote synchronization is implied.',
        'local_preflight': 'passed' if local_ready else 'blocked',
        'checkpoint_state': 'uncommitted' if state['source']['working_tree_dirty'] or not state['source']['git_head'] else 'committed',
        'hosted_verification': 'Use pnpm project:ci to inspect the current commit; pushing alone is not verification.',
        'slowest_checks': sorted(
            [{'id': key, 'duration_seconds': item['duration_seconds']} for key, item in results.items() if 'duration_seconds' in item],
            key=lambda item: item['duration_seconds'], reverse=True)[:5],
    }


def verified_main(root, candidate, ci):
    if __package__:
        from .ci import WORKFLOW, classify_runs, command, github_identity
    else:
        from ci import WORKFLOW, classify_runs, command, github_identity
    repository = github_identity(root)
    policy = read_json(root / 'project/branch-protection.json')
    if repository != policy['repository'] or (ci and ci['repository'] != repository):
        raise ProjectError('Release evidence belongs to another repository')
    branch = policy['branch']
    endpoint = f'repos/{repository}'

    def main_head():
        return command(['gh', 'api', f'{endpoint}/git/ref/heads/{branch}', '--jq', '.object.sha'], root)

    if main_head() != candidate:
        raise ProjectError('The candidate is not the current main commit')
    response = json.loads(
        command(
            [
                'gh',
                'api',
                '--method',
                'GET',
                f'{endpoint}/actions/runs',
                '-f',
                f'branch={branch}',
                '-f',
                'event=push',
                '-f',
                f'head_sha={candidate}',
                '-f',
                'per_page=100',
            ],
            root,
        )
    )
    runs = [
        run
        for run in response['workflow_runs']
        if run.get('head_branch') == branch and run.get('event') == 'push'
    ]
    observed = classify_runs(runs, candidate)
    if observed['status'] != 'passed':
        raise ProjectError('Main push verification for the candidate has not passed')
    if ci:
        run = json.loads(
            command(
                ['gh', 'api', f'{endpoint}/actions/runs/{ci["run_id"]}/attempts/{ci["run_attempt"]}'], root
            )
        )
        if (
            str(run.get('id')) != ci['run_id']
            or str(run.get('run_attempt')) != ci['run_attempt']
            or run.get('path') != WORKFLOW
            or run.get('event') != 'workflow_dispatch'
            or run.get('head_sha') != candidate
            or run.get('head_branch') != branch
            or run.get('status') != 'completed'
            or run.get('conclusion') != 'success'
        ):
            raise ProjectError('The downloaded run does not match successful release CI for this candidate')
    if main_head() != candidate:
        raise ProjectError('Main changed during candidate verification')
    return observed['run']


def verify_primary_model(project, results, task_id=None, *, check_id=None):
    primary = project['spec'].get('ai', {}).get('model_connections', {}).get('primary_cloud_route')
    if (primary is None or (task_id is not None and task_id != primary['task'])
            or (check_id is not None and check_id != primary['check'])):
        return
    observation = results.get(primary['check'], {}).get('observation', {})
    if observation.get('deployment') != 'cloud' or observation.get('modality') != 'text':
        raise ProjectError('The primary model route requires an observed cloud text session')


def verified_candidate_files(root, project, report, results, source, report_path, artifact_root, candidate):
    if __package__:
        from .verification_evidence import (
            release_artifacts,
            collect_run_observation,
            integration_validation,
            RUN_OBSERVATION_CHECKS,
        )
    else:
        from verification_evidence import (
            release_artifacts,
            collect_run_observation,
            integration_validation,
            RUN_OBSERVATION_CHECKS,
        )
    reports_root = (root / '.reports').resolve()
    report_path = (root / report_path).resolve()
    origin = reports_root
    protected_download = report_path.is_relative_to(reports_root / 'ci')
    if artifact_root is not None:
        if not report.get('ci'):
            raise ProjectError('A downloaded release report requires CI provenance')
        origin = (root / artifact_root).resolve()
        expected_dir = reports_root / 'ci' / report['ci']['run_id'] / 'windows'
        if origin != expected_dir or report_path != origin / 'test-report.json':
            raise ProjectError('Expected the isolated Windows artifact for the observed workflow run')
        if (
            report['ci']['event'] != 'workflow_dispatch'
            or report['ci']['head_sha'] != candidate
            or report['ci']['checkout_sha'] != candidate
        ):
            raise ProjectError(
                'Hosted release evidence must describe this candidate and its release workflow'
            )
    elif protected_download or report.get('ci') and report_path != reports_root / 'test-report.json':
        raise ProjectError('Specify the downloaded CI artifact root')
    run_id = report['run_id']
    if not re.fullmatch(r'[a-zA-Z0-9_-][a-zA-Z0-9._-]*', run_id):
        raise ProjectError('Invalid release run identifier')
    run_dir = local_path(origin, 'runs/' + run_id)
    if artifact_root is None and report_path not in {
        reports_root / 'test-report.json',
        run_dir / 'test-report.json',
    }:
        raise ProjectError('Expected the retained local report or its original per-run copy')
    applicable = release_checks(project)
    if set(report['selection'].get('checks', [])) != applicable:
        raise ProjectError('The release report did not select all applicable candidate checks')
    for key in applicable:
        check = project['checks'][key]
        result = results.get(key, {})
        command = [
            report['environment']['python_executable'] if arg == '{python}' else arg
            for arg in check['command'] or []
        ]
        if (
            check['status'] != 'implemented'
            or result.get('status') != 'passed'
            or result.get('command') != command
            or result.get('exit_code') != 0
            or result.get('log') != f'.reports/runs/{run_id}/{key}.log'
            or file_digest(local_path(run_dir, key + '.log')) != result.get('log_sha256')
        ):
            raise ProjectError(f'Missing or changed release check evidence: {key}')
        if key in RUN_OBSERVATION_CHECKS and result.get('observation') != collect_run_observation(root, run_dir, key, source):
            raise ProjectError(f'Changed external-session evidence: {key}')
    verify_primary_model(project, results)
    if report.get('integration_validation') != integration_validation(results.values()):
        raise ProjectError('The integration summary does not match the observed sessions')
    if not isinstance(report.get('candidate_artifacts'), dict):
        raise ProjectError('Release report lacks bound installer and manifest digests')
    return release_artifacts(
        run_dir, source, project['catalog']['desktop_performance'], expected=report['candidate_artifacts'],
        hosted=artifact_root is not None,
    )


def readiness(root=ROOT, *, report_path=None, artifact_root=None, candidate=None):
    project = load_project(root)
    report_path = checkout_report_path(root, report_path or '.reports/test-report.json', reports_only=True)
    state = workfront(root, report_path=report_path)
    current = state['verification']['current']
    report = read_json(report_path) if current else {}
    results = indexed(report.get('checks', []), 'check result')
    scenarios = [{**item, 'verification': summarize_verification(item['checks'], results)}
                 for item in project['delivery']['scenarios']]
    milestones, acceptance = product_progress(project, list(results.values()))
    blockers = []
    if not current:
        blockers.append('Current execution inputs and project definitions have no matching verification report.')
    if state['source']['working_tree_dirty'] or not state['source']['git_head']:
        blockers.append('Release source must be preserved in a clean committed revision.')
    if report and (report['source']['working_tree_dirty'] or report['source']['git_head'] != state['source']['git_head']):
        blockers.append('Release verification must name the same clean committed revision.')
    if report.get('status') != 'passed':
        blockers.append('The release verification report must pass.')
    if (report.get('selection', {}).get('profile') != 'release'
            or report.get('selection', {}).get('task_id') is not None
            or not windows_x64(report.get('environment', {}))
            or report.get('native_requirement') != {'requested': True, 'satisfied': True}):
        blockers.append('Run the release profile with --require-windows on native Windows x64 for the candidate.')
    blockers.extend(f"Milestone {item['id']}: {item['status']}" for item in milestones if item['status'] != 'passed')
    blockers.extend(f"Scenario {item['id']}: {item['verification']}" for item in scenarios if item['verification'] != 'passed')
    blockers.extend(f"Release check {key}: {results.get(key, {}).get('status', 'blocked')}"
                    for key in project['delivery']['release_policy']['required_checks']
                    if results.get(key, {}).get('status') != 'passed')
    blockers.extend(f"Unresolved {item['severity']} feedback: {item['id']}" for item in project['feedback']['items']
                    if item['status'] != 'addressed' and item['severity'] in {'critical', 'major'})
    if any(item['status'] != 'passed' for item in acceptance):
        blockers.append('Contract acceptance is incomplete.')
    if any(item['status'] == 'failed' for item in results.values()):
        blockers.append('The current report contains failed checks.')
    candidate = candidate or state['source']['git_head']
    if (not isinstance(candidate, str) or not re.fullmatch(r'(?:[a-f0-9]{40}|[a-f0-9]{64})', candidate)
            or candidate != state['source']['git_head']):
        blockers.append('Expected the exact candidate commit checked out locally.')
    verified_candidate = None
    if not blockers:
        try:
            verified_candidate = verified_candidate_files(root, project, report, results, state['source'],
                                                         report_path, artifact_root, candidate)
            verified_candidate['main_ci'] = verified_main(root, candidate, report.get('ci'))
        except (ProjectError, OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError) as error:
            blockers.append(str(error))
    return {'status': 'blocked' if blockers else 'passed', 'release_ready': not blockers,
            'candidate_commit': candidate, 'candidate_evidence': verified_candidate if not blockers else None,
            'technical_ready': not blockers, 'authorization': 'not_evaluated',
            'recommendation': 'complete_release_gates' if blockers else 'present_release_proposal',
            'verification': state['verification'], 'scenarios': scenarios, 'blockers': blockers,
            'release_policy': project['delivery']['release_policy'],
            'claim_scope': 'Technical readiness from recorded release checks; publication authorization and published-artifact verification are separate. Scenario text alone is not observed product behavior.'}


def recover_hosted_checkpoint(root, revision, repository):
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ProjectError('Hosted history requires a full previous checkpoint SHA')
    if revision == '0' * 40:
        return
    if not re.fullmatch(r'[A-Za-z0-9-]+/[A-Za-z0-9_.-]+', repository):
        raise ProjectError('Hosted history requires the GitHub verification repository')

    env = checkout_git_environment()
    def object_type():
        return subprocess.run(['git', '--no-replace-objects', 'cat-file', '-t', '--', revision],
                              cwd=root, env=env, capture_output=True)

    found = object_type()
    if found.returncode:
        # A force-pushed PR checkpoint can remain on GitHub after its ref moves.
        # Request just the event's object from the base repository without credentials.
        origin = f'https://github.com/{repository}.git'
        try:
            fetched = subprocess.run(['git', '-c', 'credential.helper=', '-c', 'http.followRedirects=false',
                                      'fetch', '--no-tags',
                                      '--no-write-fetch-head', '--no-recurse-submodules', '--', origin, revision],
                                     cwd=root, capture_output=True, timeout=60,
                                     env={**env, 'GIT_TERMINAL_PROMPT': '0'})
        except subprocess.TimeoutExpired:
            raise ProjectError(f'Timed out retrieving previous checkpoint {revision} from {repository}') from None
        if fetched.returncode:
            raise ProjectError(f'Cannot retrieve previous checkpoint {revision} from {repository}')
        found = object_type()
    if found.returncode or found.stdout.strip() != b'commit':
        raise ProjectError(f'Previous checkpoint {revision} is not an available Git commit')


def feedback_git(root, *args):
    # Keep local checks offline even in a partial clone with promised objects.
    return subprocess.run(['git', *args], cwd=root, capture_output=True,
                          env={**checkout_git_environment(), 'GIT_NO_LAZY_FETCH': '1', 'GIT_ALLOW_PROTOCOL': '',
                               'GIT_NO_REPLACE_OBJECTS': '1', 'GIT_GRAFT_FILE': os.devnull})


def tasks_at_feedback_checkpoint(root, commit):
    path = 'project/tasks.json'
    ledger = feedback_git(root, 'show', f'{commit}:{path}')
    if ledger.returncode:
        raise ProjectError(f'Cannot read {path} from local feedback checkpoint {commit}')
    try:
        return indexed(json.loads(ledger.stdout)['tasks'], 'previous task')
    except (KeyError, TypeError, UnicodeError, json.JSONDecodeError) as error:
        raise ProjectError(f'Invalid {path} at local feedback checkpoint {commit}: {error}') from error


def verify_accepted_tasks(root, project, previous_tasks):
    compared = []
    for task in previous_tasks:
        if task['status'] in {'done', 'cancelled'}:
            current_task = project['tasks'].get(task['id'])
            # Supplementary CI acceptance may arrive after local completion.
            # Preserve every accepted field and the complete ordered evidence
            # prefix; only done tasks may append independently validated receipts.
            same_record = current_task == task
            append_only = (
                task['status'] == 'done' and current_task is not None
                and {key: value for key, value in current_task.items() if key != 'evidence'}
                == {key: value for key, value in task.items() if key != 'evidence'}
                and current_task['evidence'][:len(task['evidence'])] == task['evidence']
            )
            if not same_record and not append_only:
                raise ProjectError(f"Accepted task history changed or disappeared: {task['id']}; add a successor task")
            for ref in current_task['evidence'][len(task['evidence']):]:
                # load_project has already checked file digest, schema, task ID,
                # passing check status and native platform declarations.
                receipt = read_json(local_path(root, ref['path']))
                if (receipt['task_definition_sha256'] != task_definition_hash(task)
                        or not set(task['checks']).issubset(check['id'] for check in receipt['checks'])):
                    raise ProjectError(f"Appended evidence does not validate the accepted task: {task['id']}")
            compared.append(task['id'])
    return compared


def verify_feedback_handoff(root, project, commit, old_items, items):
    """Keep an open finding with live corrective work when its last owner finishes or is removed."""
    terminal = {'done', 'cancelled'}
    at_risk = [
        item for key, item in items.items()
        if key in old_items and old_items[key]['status'] != 'addressed' and item['status'] == 'open'
        and (item['severity'] != 'minor' or old_items[key]['severity'] != 'minor')
        and all(project['tasks'][owner]['status'] in terminal for owner in item['task_ids'])
    ]
    if not at_risk:
        return
    previous = tasks_at_feedback_checkpoint(root, commit)
    try:
        for item in at_risk:
            key = item['id']
            if any(previous[owner]['status'] not in terminal for owner in old_items[key]['task_ids']):
                raise ProjectError(f'Open feedback {key} needs an unfinished corrective task before its last owner retires, '
                                   'or a recorded resolution or deferral.')
    except (KeyError, TypeError) as error:
        raise ProjectError(f'Invalid project/tasks.json at local feedback checkpoint {commit}: {error}') from error


def feedback_at(root, commit):
    path = 'project/feedback.json'
    original = feedback_git(root, 'show', f'{commit}:{path}')
    if original.returncode:
        entry = feedback_git(root, 'ls-tree', '--name-only', commit, '--', path)
        if entry.returncode or entry.stdout:
            raise ProjectError(f'Cannot read {path} from local feedback checkpoint {commit}')
        return {}
    try:
        records = json.loads(original.stdout)['items']
        if not isinstance(records, list):
            raise ValueError('Expected a list of feedback items')
        return indexed(records, 'previous feedback')
    except (TypeError, ValueError, KeyError) as error:
        raise ProjectError(f'Invalid {path} at local feedback checkpoint {commit}: {error}') from error


def retained_legacy_disposition(item, accepted):
    if item == accepted:
        return True
    if not accepted or {**accepted, 'source': item['source']} != item:
        return False
    # A recorded locator binds the same run, source commit and original bytes after
    # its move to the durable archive. Only its complete path token may change.
    moves = {}
    for locator in accepted.get('diagnostic_reports', []):
        for directory, filename in (('runs', 'test-report.json'), ('recovery', 'diagnostic.json')):
            path = f"{directory}/{locator['run_id']}/{filename}"
            for prefix in ('.reports/', './.reports/'):
                moves[prefix + path] = 'archives/' + path
    if not moves:
        return False
    pieces = re.compile(f'({PATH_TOKEN.pattern})')
    original, current = pieces.split(accepted['source']), pieces.split(item['source'])
    if len(original) != len(current):
        return False
    for old, new in zip(original, current):
        if old != new:
            path = old.rstrip('.!?:')
            if path not in moves or new != moves[path] + old[len(path):]:
                return False
    return True


def validate_feedback_changes(root, project, commit, old_items, items, *, inherited=None, legacy=None):
    verify_feedback_handoff(root, project, commit, old_items, items)
    for key in old_items.keys() - items.keys():
        raise ProjectError(f'Feedback disappeared: {key}; retain its disposition')
    for key, item in items.items():
        old_item = old_items.get(key, {})
        if item != old_item:
            validate_report_references(root, item, f'Feedback {key}')
        occurrence = item.get('occurrence', 1)
        prior_occurrence = old_item.get('occurrence', 1)
        if occurrence < prior_occurrence:
            raise ProjectError(f'Feedback {key} occurrence cannot go backwards')
        if old_item.get('status') == 'addressed' and item['status'] != 'addressed' and occurrence == prior_occurrence:
            raise ProjectError(f'Reopening feedback {key} requires a new occurrence')
        if (item['status'] != 'addressed' or item == old_item
                or (item['severity'] == 'minor' and old_item.get('severity', 'minor') == 'minor')):
            continue
        if key not in old_items and inherited and item == inherited.get(key):
            continue
        if legacy and retained_legacy_disposition(item, legacy.get(key)):
            continue
        finding = feedback_fingerprint(item)
        if (old_item.get('status') == 'addressed' and finding == feedback_fingerprint(old_item)
                and set(item['task_ids']) == set(old_item['task_ids'])):
            continue
        covered = set()
        for task_id in item['task_ids']:
            for ref in project['tasks'][task_id]['evidence']:
                receipt = read_json(local_path(root, ref['path']))
                if finding in receipt.get('observed_feedback', []):
                    covered.update(check['id'] for check in receipt['checks'])
        missing = set(item['regression_checks']) - covered
        if missing:
            raise ProjectError(f'Closing feedback {key} requires new regression evidence for {sorted(missing)}')


def validate_feedback_checkpoint(root, project, commit, inherited=None):
    """Recognize an older closure only across the actual protected rule transition."""
    old_items = feedback_at(root, commit)
    items = indexed(project['feedback']['items'], 'feedback')
    last_legacy = f'{FEEDBACK_RECEIPT_INTRODUCTION}^1'
    # A shallow or unrelated checkout may not prove the crossing. Its ordinary
    # comparison still requires full current evidence for every changed closure.
    def ancestor(earlier, later):
        return feedback_git(root, 'merge-base', '--is-ancestor', earlier, later).returncode == 0

    legacy = None
    if ancestor(commit, last_legacy) and ancestor(FEEDBACK_RECEIPT_INTRODUCTION, 'HEAD'):
        legacy = feedback_at(root, last_legacy)
        verify_accepted_tasks(root, project, tasks_at_feedback_checkpoint(root, last_legacy).values())
        # Compare this endpoint too: the caller's older base cannot see a deletion,
        # edit or recurrence of a finding introduced before the rules changed.
        validate_feedback_changes(root, project, last_legacy, legacy, items)
        # A working-tree edit cannot use an old disposition to close an observation
        # that is open on the checked-out candidate, even with only a long review base.
        head_items = feedback_at(root, 'HEAD')
        if head_items != items:
            validate_feedback_changes(root, project, 'HEAD', head_items, items)
    elif inherited and any(key not in old_items and item == items.get(key) for key, item in inherited.items()):
        if ancestor(FEEDBACK_RECEIPT_INTRODUCTION, commit):
            # A descendant already inherited these records through the cutover. Its
            # missing row is not an independent branch importing a new finding.
            pinned = feedback_at(root, last_legacy)
            inherited = {key: item for key, item in inherited.items() if key not in pinned}
        elif feedback_git(root, 'rev-parse', '--is-shallow-repository').stdout.strip() == b'true':
            # Missing parents cannot establish whether this was a legitimate new import.
            inherited = {}
    validate_feedback_changes(root, project, commit, old_items, items, inherited=inherited, legacy=legacy)
    return old_items


def feedback_commit(root, ref):
    if not ref or set(ref) == {'0'}:
        return None
    commit = feedback_git(root, 'rev-parse', '--verify', '--quiet', '--end-of-options', f'{ref}^{{commit}}')
    if not commit.returncode:
        return commit.stdout.decode('ascii').strip()
    if ref == 'HEAD':
        branch = feedback_git(root, 'symbolic-ref', '--quiet', 'HEAD')
        if (not branch.returncode and feedback_git(root, 'show-ref', '--verify', '--quiet',
                                                   os.fsdecode(branch.stdout).strip()).returncode == 1):
            return None
    raise ProjectError(f'Feedback checkpoint is unavailable locally: {ref}')


def protected_feedback_base(root, head):
    path = root / 'project/branch-protection.json'
    if not head or not path.is_file():
        return {'source': 'none', 'reason': 'No committed HEAD or protected branch configuration.'}
    policy = read_json(path)
    branch = policy.get('branch') if isinstance(policy, dict) else None
    if not isinstance(branch, str) or not branch or feedback_git(root, 'check-ref-format', f'refs/heads/{branch}').returncode:
        raise ProjectError('Invalid configured protected branch for local feedback comparison')
    ref = f'refs/remotes/origin/{branch}'
    if feedback_git(root, 'show-ref', '--verify', '--quiet', ref).returncode == 1:
        return {'source': 'none', 'ref': ref, 'reason': 'No local tracking ref for the protected branch.'}
    tip = feedback_commit(root, ref)
    result = feedback_git(root, 'merge-base', '--all', head, tip)
    commits = result.stdout.decode('ascii').splitlines()
    observation = {'source': 'local_protected_merge_base', 'ref': ref, 'tracking_commit': tip}
    if result.returncode == 1 or len(commits) > 1:
        return {**observation, 'reason': 'The local histories have no unique common ancestor.'}
    if result.returncode or len(commits) != 1:
        raise ProjectError(f'Cannot determine the local common ancestor of HEAD and {ref}')
    return {**observation, 'commit': commits[0]}


def reviewed_feedback_imports(items, reviewed, head):
    # A candidate's own checkout or advertised source head cannot confirm an older disposition.
    if head and reviewed and reviewed not in {head, os.environ.get('SCOPEHUB_SOURCE_HEAD')}:
        return items
    return {}


def local_feedback_history(root, project):
    """Report the local comparison inputs. A cached tracking ref only supplies its common history."""
    require_periodic_reviews(root, project)
    if not (root / '.git').exists():
        return {'status': 'not_applicable', 'reason': 'No Git checkout available.'}
    try:
        head = feedback_commit(root, 'HEAD')
    except FileNotFoundError:
        return {'status': 'not_applicable', 'reason': 'The Git executable is unavailable.'}
    explicit = os.environ.get('SCOPEHUB_REVIEW_BASE')
    basis = ({'source': 'explicit', 'commit': feedback_commit(root, explicit)} if explicit
             else protected_feedback_base(root, head))
    reviewed = basis.get('commit')
    checkpoint = feedback_commit(root, os.environ.get('SCOPEHUB_CHECKPOINT_BASE'))
    compared = []
    imports = {}
    for sha in dict.fromkeys([reviewed, head, checkpoint]):
        if sha:
            require_periodic_transition(root, project, sha)
            records = validate_feedback_checkpoint(root, project, sha, imports)
            if explicit and sha == reviewed:
                imports = reviewed_feedback_imports(records, reviewed, head)
            compared.append(sha)
    if not compared:
        return {'status': 'not_applicable', 'reason': 'The checkout has no committed checkpoint.'}
    return {'status': 'passed', 'compared_commits': compared, 'review_basis': basis}


def review_history(root=ROOT, base=None, *, checkpoint_base=None, fetch_missing_checkpoint=False):
    result, records = compare_history(root, base)
    if checkpoint_base and checkpoint_base != base:
        if fetch_missing_checkpoint:
            if os.environ.get('GITHUB_ACTIONS') != 'true':
                raise ProjectError('Checkpoint recovery requires the GitHub Actions job context')
            recover_hosted_checkpoint(root, checkpoint_base, os.environ.get('GITHUB_REPOSITORY', ''))
        imports = reviewed_feedback_imports(records, result.get('base_commit'), feedback_commit(root, 'HEAD'))
        result['previous_checkpoint'], _ = compare_history(root, checkpoint_base, imports)
    return result


def compare_history(root, base, inherited=None):
    """Return the reviewed base ledger only after the complete history comparison passes."""
    if not base or set(base) == {'0'}:
        return {'status': 'not_applicable', 'reason': 'No previous revision supplied; no history comparison was performed.'}, {}
    env = checkout_git_environment()
    resolved = subprocess.run(['git', 'rev-parse', '--verify', '--end-of-options', f'{base}^{{commit}}'],
                              cwd=root, env=env, capture_output=True, text=True, check=True).stdout.strip()
    project = load_project(root)

    def previous(path):
        result = subprocess.run(['git', 'show', f'{resolved}:{path}'], cwd=root, env=env, capture_output=True)
        return json.loads(result.stdout) if result.returncode == 0 else None

    require_periodic_reviews(root, project)
    require_periodic_transition(root, project, resolved)
    ledger = previous('project/tasks.json')
    old_tasks = indexed(ledger['tasks'], 'previous task') if ledger else {}
    for key, task in project['tasks'].items():
        old = old_tasks.get(key, {})
        if old.get('status') not in {'done', 'cancelled'}:
            validate_report_references(root, task, f'Task {key}')
    feedback = validate_feedback_checkpoint(root, project, resolved, inherited)
    review_operational_report_paths(root, resolved)
    catalog = previous('project/checks.json')
    previous_commands = {check['id']: check.get('command') for check in catalog['checks']} if catalog else {}
    for key, check in project['checks'].items():
        command = check['command']
        if command and command != previous_commands.get(key):
            require_portable_report_paths(root, command, f'Check {key} in project/checks.json')
    replaced = {}
    if catalog:
        established = set(catalog.get('required_change_checks', [
            check['id'] for check in catalog['checks']
            if check['status'] == 'implemented' and check.get('cadence', 'change') == 'change'
        ]))
        current = set(project['catalog']['required_change_checks'])
        changes = {item['removed_check']: item for item in project['catalog']['gate_changes']}
        for entry in catalog.get('gate_changes', []):
            if changes.get(entry['removed_check']) != entry:
                raise ProjectError(f"Accepted gate replacement changed or disappeared: {entry['removed_check']}")
        # The graph is acyclic. Follow every branch until it reaches a current required check.
        pending = sorted(established - current, reverse=True)
        while pending:
            key = pending.pop()
            if key in current or key in replaced:
                continue
            replacement = changes.get(key)
            if not replacement or project['tasks'][replacement['task_id']]['status'] not in {'active', 'done'}:
                raise ProjectError(f'Established integration gate removed without a reviewed replacement: {key}')
            replaced[key] = replacement
            pending.extend(reversed(replacement['replacement_checks']))
    compared = verify_accepted_tasks(root, project, old_tasks.values())
    paths = subprocess.run(['git', 'ls-tree', '-r', '--name-only', resolved, '--', 'contracts/decisions'],
                           cwd=root, env=env, capture_output=True, text=True, check=True).stdout.splitlines()
    for path in paths:
        if not path.endswith('.json'):
            continue
        decision = previous(path)
        if decision and decision['status'] == 'accepted':
            if project['decisions'].get(decision['id']) != decision:
                raise ProjectError(f"Accepted decision history changed or disappeared: {decision['id']}; supersede it")
            compared.append(decision['id'])
    return {'status': 'passed', 'base_commit': resolved, 'preserved_records': compared,
            'gate_replacements': list(replaced.values())}, feedback


def archive_evidence(root, task_id, report_path, artifact_root=None):
    project = load_project(root)
    if task_id not in project['tasks']:
        raise ProjectError(f'Unknown task: {task_id}')
    task = project['tasks'][task_id]
    report_path = evidence_report_path(root, report_path)
    report_bytes = report_path.read_bytes()
    try:
        report = json.loads(report_bytes.decode('utf-8'))
    except ValueError as error:
        raise ProjectError(f'Cannot read JSON record {report_path}: {error}') from error
    if not isinstance(report, dict):
        raise ProjectError('Expected a structured verification report')
    if report.get('report_version') != '2.0' or not report.get('source_unchanged'):
        raise ProjectError('Report lacks an unchanged version-bound execution snapshot')
    validate_model(root, 'HarnessReport', report)
    if report.get('status') == 'failed' or any(item.get('status') == 'failed' for item in report.get('checks', [])):
        raise ProjectError('Cannot archive a failed verification run; repair it and rerun the affected checks')
    current_source = source_identity(root)
    if report['source']['sha256'] != current_source['sha256']:
        raise ProjectError('Cannot archive a stale report as current completion evidence')
    ci = report.get('ci')
    checkout = root.resolve()
    reports_root = (checkout / '.reports').resolve()
    if artifact_root is None and report_path.is_relative_to((reports_root / 'ci').resolve()):
        raise ProjectError('Specify the downloaded CI artifact root')
    if ci:
        if not report_path.is_relative_to(checkout):
            raise ProjectError('CI evidence requires a checkout-local report file')
        if ci['head_sha'] != current_source['git_head'] or ci['checkout_sha'] != report['source']['git_head']:
            raise ProjectError('CI evidence belongs to another commit or checkout')
        if report['source']['working_tree_dirty']:
            raise ProjectError('CI evidence must describe a clean checkout')
        remote = subprocess.run(['git', 'remote', 'get-url', 'origin'], cwd=root, env=checkout_git_environment(),
                                capture_output=True, text=True, check=True).stdout.strip()
        if remote not in {f"https://github.com/{ci['repository']}", f"https://github.com/{ci['repository']}.git"}:
            raise ProjectError('CI repository differs from the configured HTTPS origin')
        if ci['run_url'] != f"https://github.com/{ci['repository']}/actions/runs/{ci['run_id']}":
            raise ProjectError('CI run URL differs from its recorded identity')
    if artifact_root is not None:
        if not ci:
            raise ProjectError('Downloaded artifacts require CI provenance')
        artifact_root = artifact_root.resolve()
        if (reports_root == checkout or not reports_root.is_relative_to(checkout)
                or artifact_root == reports_root or not artifact_root.is_relative_to(reports_root)
                or not artifact_root.is_dir()):
            raise ProjectError('Downloaded artifacts must be isolated inside the repository .reports directory')
        if not report_path.is_relative_to(artifact_root):
            raise ProjectError('Downloaded report must belong to its isolated artifact directory')
    if report.get('task_definitions', {}).get(task_id) != task_definition_hash(task):
        raise ProjectError('Task definition changed after verification')
    if not report.get('project_unchanged') or report.get('project_definition_sha256') != project_definition_hash(project):
        raise ProjectError('Project definitions or decisions changed after verification')
    if task['status'] == 'cancelled':
        raise ProjectError('Cannot record completion of a cancelled task')
    if task['status'] != 'done' and 'plan' not in task:
        raise ProjectError('Task completion needs a delivery plan')
    if any(project['tasks'][key]['status'] != 'done' for key in task['depends_on']):
        raise ProjectError('Task completion has unfinished dependencies')
    results = indexed(report['checks'], 'report check')
    if __package__:
        from .verification_evidence import (
            EXTERNAL_CLIENT_CHECK, FIELD_ACTIONS, RUN_OBSERVATION_CHECKS,
            collect_run_observation, field_observation, redacted_check_log,
        )
    else:
        from verification_evidence import (
            EXTERNAL_CLIENT_CHECK, FIELD_ACTIONS, RUN_OBSERVATION_CHECKS,
            collect_run_observation, field_observation, redacted_check_log,
        )
    selected = []
    field_records = {}
    external_client_observation = None
    for key in task['checks']:
        result = results.get(key, {})
        if project['checks'][key]['status'] != 'implemented' or result.get('status') != 'passed' or result.get('exit_code') != 0:
            raise ProjectError(f'Required check did not pass: {key}')
        expected_command = [report['environment'].get('python_executable') if arg == '{python}' else arg
                            for arg in project['checks'][key]['command']]
        if result.get('command') != expected_command:
            raise ProjectError(f'Executed command differs from the check catalog: {key}')
        if project['checks'][key]['platforms'] == 'windows-x64' and not windows_x64(report['environment']):
            raise ProjectError(f'Native Windows proof requires Windows x64: {key}')
        if not result.get('log', '').startswith('.reports/runs/'):
            raise ProjectError(f'Expected an isolated verification log: {key}')
        log = (local_path(artifact_root, result['log'].removeprefix('.reports/'))
               if artifact_root is not None else local_path(root, result['log']))
        if not log.is_file():
            raise ProjectError(f'Missing verification log: {key}')
        if key in RUN_OBSERVATION_CHECKS:
            if result['log'] != f".reports/runs/{report['run_id']}/{key}.log":
                raise ProjectError(f'External-session log belongs to another run: {key}')
            observation = collect_run_observation(root, log.parent, key, report['source'])
            if result.get('observation') != observation:
                raise ProjectError(f'Changed external-session evidence: {key}')
            if key == EXTERNAL_CLIENT_CHECK:
                if log.read_bytes() != redacted_check_log(result['exit_code']):
                    raise ProjectError('External-client log contains more than its redacted exit status.')
                external_client_observation = observation
        if key in FIELD_ACTIONS:
            observed = field_observation(root, key, artifact_root, hosted=artifact_root is not None)
            if result.get('field_observation') != observed:
                raise ProjectError(f'Field observation changed after verification: {key}')
            field_records[key] = observed
        log_hash = file_digest(log)
        if result.get('log_sha256') != log_hash:
            raise ProjectError(f'Verification log changed after the run: {key}')
        selected.append({'id': key, 'status': 'passed', 'command': result['command'], 'exit_code': 0,
                         'log_sha256': log_hash})
    verify_primary_model(project, results, task_id)
    receipt = {
        'schema_version': '1.0', 'task_id': task_id, 'task_definition_sha256': task_definition_hash(task),
        'run_id': report['run_id'], 'recorded_at': datetime.now(timezone.utc).isoformat(),
        'source': report['source'], 'report_sha256': digest(report_bytes),
        'environment': report['environment'], 'checks': selected,
        'project_definition_sha256': report['project_definition_sha256'],
        'claim_scope': 'Completed task at the recorded execution inputs; not a release or current-worktree guarantee.',
    }
    if 'observed_feedback' in report:
        receipt['observed_feedback'] = report['observed_feedback']
    if external_client_observation is not None:
        receipt['external_client_observation'] = external_client_observation
    if field_records:
        receipt['field_observations'] = field_records
    if ci:
        receipt['ci'] = ci
    validate_model(root, 'Evidence', receipt)
    # Derive the filename from trusted content, never from an arbitrary report path/ID.
    name = f"{task_id}-{receipt['report_sha256'][:16]}.json"
    path = root / 'project/evidence' / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = read_json(path)
        if {key: value for key, value in existing.items() if key != 'recorded_at'} != {
                key: value for key, value in receipt.items() if key != 'recorded_at'}:
            raise ProjectError('Refusing to overwrite different archived evidence')
    else:
        with path.open('xb') as output:
            output.write(json_bytes(receipt))
    return {'path': path.relative_to(root).as_posix(), 'sha256': digest(path.read_bytes())}


def unassigned_source(root, *, after=None, read_only=False):
    if __package__:
        from .development import project_navigation, unassigned_feedback
    else:
        from development import project_navigation, unassigned_feedback
    ledger = read_json(root / 'project/tasks.json')
    items = read_json(root / 'project/feedback.json')['items']
    unassigned = unassigned_feedback(items, indexed(ledger['tasks'], 'task'))
    identities = [row['id'] for row in unassigned]
    if after is not None and after not in identities:
        raise ValueError('That finding is no longer waiting for assignment. Rerun the unassigned lookup.')
    start = identities.index(after) + 1 if after else 0
    rows = unassigned[start:start + 5]
    prefix = ('source', 'project/feedback.json')
    source_commit = getattr(root, 'commit', None)
    result = {'total': len(unassigned), 'items': [
        {'id': row['id'], 'severity': row['severity'],
         'command': project_navigation(*prefix, '--id', row['id'], read_only=read_only, source_commit=source_commit)}
        for row in rows]}
    if rows and start + len(rows) < len(unassigned):
        result['more'] = project_navigation(*prefix, '--unassigned', '--after', rows[-1]['id'],
                                            *(['--read-only'] if read_only else []), read_only=read_only,
                                            source_commit=source_commit)
    return result



def source_navigation(root, args):
    records = CommittedPath.at(root, args.source) if args.source is not None else root
    reference = {'path': args.path, 'pointer': args.pointer}
    if args.after and not args.unassigned:
        raise ValueError('Use --after only with --unassigned.')
    if (args.unassigned or args.id is not None) and (args.path != 'project/feedback.json' or args.pointer):
        flag = '--unassigned' if args.unassigned else '--id'
        raise ValueError(f'Use {flag} with the canonical feedback file and no JSON pointer.')
    if args.unassigned:
        result = unassigned_source(records, after=args.after, read_only=args.read_only)
    elif args.id is not None:
        matches = [row for row in resolve_source(records, {**reference, 'pointer': '/items'}) if row['id'] == args.id]
        if len(matches) != 1:
            raise ValueError('Expected one canonical feedback item with that ID.')
        result = {'source': {'path': args.path, 'id': args.id}, 'value': matches[0]}
        if matches[0]['status'] == 'open':
            result['follow_through'] = 'Link this existing finding to an unfinished corrective task and verify its assigned checks.'
    else:
        result = {'source': reference, 'value': resolve_source(records, reference)}
    if args.source is not None:
        result['source_commit'] = records.commit
    return result


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('check')
    status = sub.add_parser('status')
    status_format = status.add_mutually_exclusive_group()
    status_format.add_argument('--detail', action='store_true', help='Expand focus plans and individual evidence receipts')
    status_format.add_argument('--overview', action='store_true', help='Show a bounded project outlook and next follow-through')
    status.add_argument('--read-only', action='store_true', help='Render local navigation using pinned Python')
    observe = sub.add_parser('observe-maintenance', help='Project an existing review into a compact record')
    observe.add_argument('review')
    observe.add_argument('--report', type=Path, action='append', default=[],
                         help='Producer observation, then optional protection or product slice timing original, or compatibility receipts')
    observe.add_argument('--finding', action='append', default=[])
    verify_observation = sub.add_parser('verify-maintenance', help='Recompute a proposed review from private originals')
    verify_observation.add_argument('record', type=Path)
    verify_observation.add_argument('--report', type=Path, action='append', required=True)
    for command in (observe, verify_observation):
        command.add_argument('--dispositions', help='Existing commit:path exact-head recovery dispositions')
        command.add_argument('--staging', type=Path, help='One explicitly named retained staging archive')
        command.add_argument('--staging-digest', help='Expected SHA-256 of the retained staging manifest')
    context = sub.add_parser('context')
    context.add_argument('task_id', nargs='?')
    context.add_argument('--detail', action='store_true', help='Expand full policies, decisions and historical dependencies')
    context.add_argument('--phase', choices=['implement', 'verify', 'handoff'], default='implement')
    context.add_argument('--source', metavar='COMMIT', help='Require this clean commit in verification context')
    sub.add_parser('brief')
    source = sub.add_parser('source', help='Read one authoritative JSON section without expanding unrelated records')
    source.add_argument('path')
    source.add_argument('pointer', nargs='?', default='')
    source_lookup = source.add_mutually_exclusive_group()
    source_lookup.add_argument('--id', help='Read a single canonical feedback item by stable ID')
    source_lookup.add_argument('--unassigned', action='store_true', help='List open feedback requiring assignment')
    source.add_argument('--after', help='Continue an unassigned list after its last stable feedback ID')
    source.add_argument('--read-only', action='store_true', help='Render continuation commands using pinned Python')
    source.add_argument('--source', metavar='COMMIT', help='Read a record from this exact local Git commit')
    ready = sub.add_parser('readiness')
    ready.add_argument('--candidate', help='Exact release candidate commit')
    ready.add_argument('--report', type=Path, help='Retained Windows release report')
    ready.add_argument('--artifact-root', type=Path, help='Isolated downloaded Windows CI artifact directory')
    sub.add_parser('checkpoint')
    history = sub.add_parser('history')
    history.add_argument('--base', default=os.environ.get('SCOPEHUB_REVIEW_BASE'))
    history.add_argument('--fetch-missing-checkpoint', action='store_true',
                         help='Recover the exact previous checkpoint from the public GitHub verification repository')
    record = sub.add_parser('record')
    record.add_argument('task_id')
    record.add_argument('--report', type=Path, default=ROOT / '.reports/test-report.json')
    record.add_argument('--artifact-root', type=Path, help='Isolated downloaded artifact directory containing runs/')
    diagnostic = sub.add_parser('diagnostic', help='Identify or explicitly verify a retained diagnostic')
    diagnostic_commands = diagnostic.add_subparsers(dest='diagnostic_command', required=True)
    identify = diagnostic_commands.add_parser('identify', help='Derive a portable locator from a report')
    identify.add_argument('report', type=Path)
    resolve = diagnostic_commands.add_parser('resolve', help='Verify an original diagnostic and its bound files')
    resolve.add_argument('run_id')
    resolve.add_argument('source_commit')
    resolve.add_argument('report_sha256')
    resolve.add_argument('--archive-root', type=Path, help='Host archive root (then SCOPEHUB_ARCHIVE_ROOT, then local Git scopehub.archiveRoot)')
    args = parser.parse_args()
    if args.command == 'context' and args.source is not None and args.phase != 'verify':
        context.error('--source requires --phase verify.')
    try:
        if args.command == 'check':
            project = load_project(ROOT)
            feedback_history = local_feedback_history(ROOT, project)
            result = {'status': 'passed', 'tasks': len(project['tasks']), 'decisions': len(project['decisions']),
                      'checks': len(project['checks']), 'feedback_history': feedback_history}
        elif args.command == 'status':
            if __package__:
                from .development import status_view
            else:
                from development import status_view
            result = status_view(workfront(ROOT, retained_detail=args.detail), detail=args.detail,
                                 overview=args.overview, read_only=args.read_only)
        elif args.command == 'observe-maintenance':
            result = observe_maintenance(ROOT, load_project(ROOT), args.review, args.report, args.finding,
                                         staging=args.staging, staging_digest=args.staging_digest,
                                         dispositions=args.dispositions)
        elif args.command == 'verify-maintenance':
            result = verify_maintenance(ROOT, load_project(ROOT), args.record, args.report,
                                        args.staging, args.staging_digest, args.dispositions)
        elif args.command == 'context':
            records = ROOT
            if args.source is not None:
                require_review_source(ROOT, args.source)
                records = CommittedPath.at(ROOT, args.source)
            result = task_context(records, args.task_id, detail=args.detail, phase=args.phase)
            if args.source is not None:
                require_review_source(ROOT, args.source)
                result['review_source'] = records.commit
        elif args.command == 'brief':
            if __package__:
                from .development import product_brief
            else:
                from development import product_brief
            result = product_brief(load_project())
        elif args.command == 'source':
            result = source_navigation(ROOT, args)
        elif args.command == 'readiness':
            result = readiness(report_path=args.report, artifact_root=args.artifact_root, candidate=args.candidate)
        elif args.command == 'history':
            result = review_history(ROOT, args.base, checkpoint_base=os.environ.get('SCOPEHUB_CHECKPOINT_BASE'),
                                    fetch_missing_checkpoint=args.fetch_missing_checkpoint)
        elif args.command == 'checkpoint':
            result = checkpoint()
        elif args.command == 'diagnostic':
            try:
                if args.diagnostic_command == 'identify':
                    result = diagnostic_locator(args.report)
                else:
                    locator = {key: getattr(args, key) for key in ('run_id', 'source_commit', 'report_sha256')}
                    validate_locator(locator)
                    result = resolve_diagnostic(locator, diagnostic_archive_root(ROOT, args.archive_root))
            except DiagnosticArchiveError as error:
                raise ProjectError(str(error)) from error
        else:
            # pnpm executes the script from the package root and supplies the
            # directory from which the user invoked it.
            original = os.environ.get('INIT_CWD') if os.environ.get('npm_lifecycle_event') == 'project:record' else None
            caller = Path(original) if original else Path.cwd()
            artifact_root = caller / args.artifact_root if args.artifact_root is not None else None
            result = archive_evidence(ROOT, args.task_id, caller / args.report, artifact_root)
        print(json.dumps(result, indent=2))
        if args.command == 'readiness' and not result['release_ready']:
            raise SystemExit(1)
    except (ProjectError, ScopeError, subprocess.CalledProcessError, OSError, KeyError, ValueError) as error:
        print(json.dumps({'status': 'failed', 'error': str(error)}))
        raise SystemExit(1) from error


if __name__ == '__main__':
    main()
