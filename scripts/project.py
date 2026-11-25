"""Validate project history, show a resumable workfront and preserve check evidence."""

import argparse
from datetime import date, datetime, timedelta, timezone
from graphlib import CycleError, TopologicalSorter
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess

from jsonschema import Draft202012Validator, FormatChecker

if __package__:
    from .development import expand_engineering_sources, resolve_source
    from .diagnostic_archive import DiagnosticArchiveError, diagnostic_locator, resolve_diagnostic, validate_locator
    from .retained_failures import retained_failure_view
    from .operational_paths import OPERATIONAL_PATHS, is_operational_path
    from .verification_scope import checkout_git_environment
    from .worktree import WorktreeError, has_git_metadata
else:
    from development import expand_engineering_sources, resolve_source
    from diagnostic_archive import DiagnosticArchiveError, diagnostic_locator, resolve_diagnostic, validate_locator
    from retained_failures import retained_failure_view
    from operational_paths import OPERATIONAL_PATHS, is_operational_path
    from verification_scope import checkout_git_environment
    from worktree import WorktreeError, has_git_metadata

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


def source_identity(root=ROOT):
    """Hash current non-ignored files without following symlinks or reading .git."""
    files, head, dirty = checkout_status(root, checkout_git_environment())
    entries = []
    for relative in sorted(files):
        if any(relative == item or (item.endswith('/') and relative.startswith(item))
               for item in INPUT_EXCLUSIONS):
            continue
        path = root / relative
        if path.is_symlink():
            entries.append([relative, 'symlink', path.readlink().as_posix()])
        elif path.is_file():
            entries.append([relative, 'file', file_digest(path)])
    if not entries:
        raise ProjectError('No execution inputs found in the Git worktree')
    return {
        'snapshot_kind': 'execution-inputs-v1',
        'sha256': digest(json.dumps(entries, separators=(',', ':')).encode('utf-8')),
        'file_count': len(entries), 'git_head': head,
        'working_tree_dirty': dirty, 'exclusions': INPUT_EXCLUSIONS,
    }


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
    for path in sorted((root / 'contracts/decisions').glob('*.json')):
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


def periodic_reviews(delivery, today=None):
    today = today or datetime.now(timezone.utc).date()
    reviews = []
    for item in delivery['scaffold_maintenance']['periodic_reviews']:
        due = date.fromisoformat(item['reviewed_on']) + timedelta(days=item['interval_days'])
        reviews.append({**item, 'next_review_by': due.isoformat(), 'review_due': today >= due})
    return reviews


def workfront(root=ROOT, *, report_path=None, retained_detail=None):
    if __package__:
        from .development import evidence_view
        from .timing import offline_budget_status
    else:
        from development import evidence_view
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
        'periodic_reviews': periodic_reviews(project['delivery']),
        'verification_budget': offline_budget_status(root, project['feedback']['items'], tasks),
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
            item['id'] for item in unresolved
            if all(tasks[key]['status'] in {'done', 'cancelled'} for key in item['task_ids'])
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
        from .development import effective_decisions, focused_context
    else:
        from development import effective_decisions, focused_context
    project = load_project(root)
    key = task_id or project['ledger']['focus_task_id']
    if key not in project['tasks']:
        raise ProjectError(f'Unknown task: {key}')
    task = project['tasks'][key]
    if phase not in {'implement', 'verify', 'handoff'}:
        raise ProjectError(f'Unknown context phase: {phase}')
    if not detail:
        return focused_context(project, key, phase)
    decision_ids = effective_decisions(project, task)
    return {
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


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('check')
    status = sub.add_parser('status')
    status.add_argument('--detail', action='store_true', help='Expand focus plans and individual evidence receipts')
    context = sub.add_parser('context')
    context.add_argument('task_id', nargs='?')
    context.add_argument('--detail', action='store_true', help='Expand full policies, decisions and historical dependencies')
    context.add_argument('--phase', choices=['implement', 'verify', 'handoff'], default='implement')
    sub.add_parser('brief')
    source = sub.add_parser('source', help='Read one authoritative JSON section without expanding unrelated records')
    source.add_argument('path')
    source.add_argument('pointer', nargs='?', default='')
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
    resolve.add_argument('--archive-root', type=Path, help='Host archive root (defaults to SCOPEHUB_ARCHIVE_ROOT)')
    args = parser.parse_args()
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
            result = status_view(workfront(retained_detail=args.detail), detail=args.detail)
        elif args.command == 'context':
            result = task_context(ROOT, args.task_id, detail=args.detail, phase=args.phase)
        elif args.command == 'brief':
            if __package__:
                from .development import product_brief
            else:
                from development import product_brief
            result = product_brief(load_project())
        elif args.command == 'source':
            if __package__:
                from .development import resolve_source
            else:
                from development import resolve_source
            reference = {'path': args.path, 'pointer': args.pointer}
            try:
                result = {'source': reference, 'value': resolve_source(ROOT, reference)}
            except ValueError as error:
                raise ProjectError(str(error)) from error
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
                    archive_root = args.archive_root or os.environ.get('SCOPEHUB_ARCHIVE_ROOT')
                    result = resolve_diagnostic(locator, archive_root)
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
    except (ProjectError, subprocess.CalledProcessError, OSError, KeyError) as error:
        print(json.dumps({'status': 'failed', 'error': str(error)}))
        raise SystemExit(1) from error


if __name__ == '__main__':
    main()
