"""Validate project history, show a resumable workfront and preserve check evidence."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess

from jsonschema import Draft202012Validator, FormatChecker

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
    if 'plan' in task:
        definition['plan'] = task['plan']
    return digest(json.dumps(definition, sort_keys=True, separators=(',', ':')).encode('utf-8'))


def project_definition_hash(project):
    # Workflow state and receipts change after recording a successful task.
    # Definitions and decisions must not change underneath the observed checks.
    definition = {'tasks': {key: task_definition_hash(task) for key, task in project['tasks'].items()},
                  'decisions': project['decisions']}
    return digest(json.dumps(definition, sort_keys=True, separators=(',', ':')).encode('utf-8'))


def local_path(root, relative):
    path = Path(relative)
    if path.is_absolute() or '..' in path.parts or not path.parts:
        raise ProjectError(f'Expected a repository-relative path: {relative}')
    candidate = root / path
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise ProjectError(f'Path escapes repository: {relative}')
    return candidate


def source_identity(root=ROOT):
    """Hash current non-ignored files without following symlinks or reading .git."""
    result = subprocess.run(
        ['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'],
        cwd=root, capture_output=True, check=True,
    )
    entries = []
    for relative in sorted(set(result.stdout.decode('utf-8').split('\0')) - {''}):
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
    head = subprocess.run(['git', 'rev-parse', '--verify', 'HEAD'], cwd=root, capture_output=True, text=True)
    status = subprocess.run(['git', 'status', '--porcelain'], cwd=root, capture_output=True, check=True, text=True)
    return {
        'snapshot_kind': 'execution-inputs-v1',
        'sha256': digest(json.dumps(entries, separators=(',', ':')).encode('utf-8')),
        'file_count': len(entries), 'git_head': head.stdout.strip() if head.returncode == 0 else None,
        'working_tree_dirty': bool(status.stdout.strip()), 'exclusions': INPUT_EXCLUSIONS,
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


def indexed(items, label):
    result = {item['id']: item for item in items}
    if len(result) != len(items):
        raise ProjectError(f'Duplicate {label} ID')
    return result


def require_refs(values, known, label):
    missing = set(values) - set(known)
    if missing:
        raise ProjectError(f'{label}: unknown references {sorted(missing)}')


def validate_graph(items, field, label):
    visited, visiting = set(), set()

    def visit(key):
        if key in visiting:
            raise ProjectError(f'{label}: cycle at {key}')
        if key in visited:
            return
        visiting.add(key)
        require_refs(items[key][field], items, f'{label} {key}')
        for dependency in items[key][field]:
            visit(dependency)
        visiting.remove(key)
        visited.add(key)

    for key in items:
        visit(key)


def windows_x64(environment):
    return environment.get('system') == 'Windows' and environment.get('machine', '').lower() in {'amd64', 'x86_64'}


def load_project(root=ROOT):
    spec = read_json(root / 'engineering-contract-v1.json')
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
    requirements = [item['requirement'] for item in catalog['acceptance']]
    if len(requirements) != len(set(requirements)) or set(requirements) != set(spec['acceptance']['required']):
        raise ProjectError('Product acceptance bindings differ from the engineering contract')
    for binding in catalog['acceptance']:
        require_refs(binding['checks'], checks, 'Product acceptance')
    decisions = []
    for path in sorted((root / 'contracts/decisions').glob('*.json')):
        decision = read_json(path)
        validate_model(root, 'Decision', decision)
        if not path.name.startswith(decision['id'] + '-'):
            raise ProjectError(f'Decision ID does not match filename: {path.name}')
        decisions.append(decision)
    decisions = indexed(decisions, 'decision')
    validate_graph(decisions, 'supersedes', 'Decision supersession')
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
    validate_graph(tasks, 'depends_on', 'Task dependencies')
    for task in tasks.values():
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
               'decisions': decisions, 'replacements': replacements}
    validate_delivery(root, project)
    return project


def validate_delivery(root, project):
    delivery = read_json(root / 'project/delivery.json')
    feedback = read_json(root / 'project/feedback.json')
    validate_model(root, 'Delivery', delivery)
    validate_model(root, 'Feedback', feedback)
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
        require_refs(plan['change_types'], types, f"Task plan {task['id']}")
        for path in plan['context']:
            local_path(root, path)
        # Historical plans retain their original check bindings when policy evolves.
        if task['status'] in {'done', 'cancelled'}:
            continue
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
        require_refs(item['task_ids'], tasks, f"Feedback {item['id']}")
        require_refs(item['regression_checks'], checks, f"Feedback {item['id']}")
        if item['status'] == 'addressed':
            if not item['resolution'] or any(tasks[key]['status'] != 'done' for key in item['task_ids']):
                raise ProjectError(f"Addressed feedback needs completed corrective work and a resolution: {item['id']}")
            covered = {key for task_id in item['task_ids'] for key in tasks[task_id]['checks']}
            if not set(item['regression_checks']).issubset(covered):
                raise ProjectError(f"Addressed feedback lacks regression evidence: {item['id']}")
        elif item['resolution'] is not None:
            raise ProjectError(f"Unresolved feedback cannot claim a resolution: {item['id']}")
        if (item['status'] == 'deferred') != bool(item['deferral_reason']):
            raise ProjectError(f"Feedback deferral needs an explicit reason: {item['id']}")
    policy = delivery['release_policy']
    require_refs(policy['required_milestones'], {gate['id'] for gate in project['catalog']['milestones']}, 'Release milestones')
    require_refs(policy['required_checks'], checks, 'Release checks')
    project.update(delivery=delivery, feedback=feedback)


def summarize_verification(check_ids, results):
    statuses = [results.get(key, {}).get('status', 'blocked') for key in check_ids]
    return 'failed' if 'failed' in statuses else ('passed' if statuses and all(value == 'passed' for value in statuses) else 'blocked')


def product_progress(project, results):
    by_id = indexed(results, 'check result')
    milestones = []
    for gate in project['catalog']['milestones']:
        tasks = [task for task in project['tasks'].values() if task['milestone'] == gate['id']]
        current_tasks = [task for task in tasks if task['status'] != 'cancelled']
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
                           'cancelled_task_ids': [task['id'] for task in tasks if task['status'] == 'cancelled']})
    acceptance = [
        {**binding, 'status': summarize_verification(binding['checks'], by_id)}
        for binding in project['catalog']['acceptance']
    ]
    return milestones, acceptance


def workfront(root=ROOT):
    project = load_project(root)
    source = source_identity(root)
    report_path = root / '.reports/test-report.json'
    report = read_json(report_path) if report_path.is_file() else None
    report_error = None
    if report is not None:
        try:
            validate_model(root, 'HarnessReport', report)
        except ProjectError as error:
            report_error = str(error)
    if not isinstance(report, dict):
        report = None
    inputs_current = bool(report and report.get('source_unchanged')
                          and report.get('source', {}).get('sha256') == source['sha256'])
    tasks = project['tasks']
    tasks_current = bool(report and report.get('task_definitions') == {
        task['id']: task_definition_hash(task) for task in tasks.values()})
    definitions_current = bool(report and report.get('project_unchanged')
                               and report.get('project_definition_sha256') == project_definition_hash(project))
    current = inputs_current and tasks_current and definitions_current and report_error is None
    return {
        'status': 'passed', 'focus': tasks.get(project['ledger']['focus_task_id']),
        'active_task_ids': [task['id'] for task in tasks.values() if task['status'] == 'active'],
        'ready_task_ids': [task['id'] for task in tasks.values() if task['status'] == 'planned'
                           and all(tasks[key]['status'] == 'done' for key in task['depends_on'])],
        'blocked': [{'id': task['id'], 'reason': task['blocker']} for task in tasks.values() if task['status'] == 'blocked'],
        'completed_task_ids': [task['id'] for task in tasks.values() if task['status'] == 'done'],
        'proposed_decisions': [item['id'] for item in project['decisions'].values() if item['status'] == 'proposed'],
        'decision_replacements': project['replacements'],
        'verification': {'report_available': report_path.is_file(), 'matches_current_execution_inputs': inputs_current,
                         'matches_current_task_definitions': tasks_current,
                         'matches_current_project_definitions': definitions_current, 'current': current,
                         'report_error': report_error,
                         'run_id': report.get('run_id') if report else None,
                         'recorded_status': report.get('status') if report else None},
        'milestones': product_progress(project, report['checks'])[0] if current else None,
        'unresolved_feedback': [item for item in project['feedback']['items'] if item['status'] != 'addressed'],
        'source': source,
    }


def task_context(root=ROOT, task_id=None):
    project = load_project(root)
    key = task_id or project['ledger']['focus_task_id']
    if key not in project['tasks']:
        raise ProjectError(f'Unknown task: {key}')
    task = project['tasks'][key]
    decision_ids = set(task['decision_refs'])
    for decision_id in list(decision_ids):
        while decision_id in project['replacements']:
            decision_id = project['replacements'][decision_id]
            decision_ids.add(decision_id)
    return {
        'task': task,
        'context_paths': list(dict.fromkeys([*project['delivery']['context'], *task.get('plan', {}).get('context', [])])),
        'dependencies': [project['tasks'][item] for item in task['depends_on']],
        'decisions': [project['decisions'][item] for item in sorted(decision_ids)],
        'checks': [project['checks'][item] for item in task['checks']],
        'scenarios': [item for item in project['delivery']['scenarios'] if key in item['task_ids']],
        'feedback': [item for item in project['feedback']['items'] if key in item['task_ids']],
        'iteration_policy': project['delivery']['iteration_policy'],
        'scaffold_maintenance': project['delivery']['scaffold_maintenance'],
        'commands': ['pnpm project:doctor', 'pnpm check:fast', f'pnpm check --task {key}', 'pnpm check',
                     f'pnpm project:recover --task {key} --save',
                     'pnpm project:checkpoint', 'pnpm project:ci',
                     'pnpm project:history --base <reviewed-commit>', f'pnpm project:record {key} --report <run-report>',
                     'pnpm project:readiness'],
    }


def checkpoint(root=ROOT):
    state = workfront(root)
    branch = subprocess.run(['git', 'symbolic-ref', '--quiet', '--short', 'HEAD'],
                            cwd=root, capture_output=True, text=True)
    upstream = subprocess.run(['git', 'rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{upstream}'],
                              cwd=root, capture_output=True, text=True)
    tracking = upstream.stdout.strip() if upstream.returncode == 0 else None
    ahead = behind = None
    if tracking:
        counts = subprocess.run(['git', 'rev-list', '--left-right', '--count', f'HEAD...{tracking}'],
                                cwd=root, capture_output=True, text=True, check=True).stdout.split()
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


def readiness(root=ROOT):
    project = load_project(root)
    state = workfront(root)
    current = state['verification']['current']
    report = read_json(root / '.reports/test-report.json') if current else {}
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
    return {'status': 'blocked' if blockers else 'passed', 'release_ready': not blockers,
            'verification': state['verification'], 'scenarios': scenarios, 'blockers': blockers,
            'release_policy': project['delivery']['release_policy'],
            'claim_scope': 'Readiness derived from recorded executable checks; scenario text alone is not observed product behavior.'}


def review_history(root=ROOT, base=None):
    """Compare accepted history to the actual reviewed revision, not HEAD by guess."""
    if not base or set(base) == {'0'}:
        return {'status': 'not_applicable', 'reason': 'No previous revision supplied; no history comparison was performed.'}
    resolved = subprocess.run(['git', 'rev-parse', '--verify', '--end-of-options', f'{base}^{{commit}}'],
                              cwd=root, capture_output=True, text=True, check=True).stdout.strip()
    project = load_project(root)

    def previous(path):
        result = subprocess.run(['git', 'show', f'{resolved}:{path}'], cwd=root, capture_output=True)
        return json.loads(result.stdout) if result.returncode == 0 else None

    ledger = previous('project/tasks.json')
    compared = []
    for task in ledger['tasks'] if ledger else []:
        if task['status'] in {'done', 'cancelled'}:
            if project['tasks'].get(task['id']) != task:
                raise ProjectError(f"Accepted task history changed or disappeared: {task['id']}; add a successor task")
            compared.append(task['id'])
    paths = subprocess.run(['git', 'ls-tree', '-r', '--name-only', resolved, '--', 'contracts/decisions'],
                           cwd=root, capture_output=True, text=True, check=True).stdout.splitlines()
    for path in paths:
        if not path.endswith('.json'):
            continue
        decision = previous(path)
        if decision and decision['status'] == 'accepted':
            if project['decisions'].get(decision['id']) != decision:
                raise ProjectError(f"Accepted decision history changed or disappeared: {decision['id']}; supersede it")
            compared.append(decision['id'])
    return {'status': 'passed', 'base_commit': resolved, 'preserved_records': compared}


def archive_evidence(root, task_id, report_path, artifact_root=None):
    project = load_project(root)
    if task_id not in project['tasks']:
        raise ProjectError(f'Unknown task: {task_id}')
    task = project['tasks'][task_id]
    report = read_json(report_path)
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
    if ci:
        if ci['head_sha'] != current_source['git_head'] or ci['checkout_sha'] != report['source']['git_head']:
            raise ProjectError('CI evidence belongs to another commit or checkout')
        if report['source']['working_tree_dirty']:
            raise ProjectError('CI evidence must describe a clean checkout')
        remote = subprocess.run(['git', 'remote', 'get-url', 'origin'], cwd=root,
                                capture_output=True, text=True, check=True).stdout.strip()
        if remote not in {f"https://github.com/{ci['repository']}", f"https://github.com/{ci['repository']}.git"}:
            raise ProjectError('CI repository differs from the configured HTTPS origin')
        if ci['run_url'] != f"https://github.com/{ci['repository']}/actions/runs/{ci['run_id']}":
            raise ProjectError('CI run URL differs from its recorded identity')
    if artifact_root is not None:
        if not ci:
            raise ProjectError('Downloaded artifacts require CI provenance')
        artifact_root = artifact_root.resolve()
        reports_root = (root / '.reports').resolve()
        if artifact_root == reports_root or not artifact_root.is_relative_to(reports_root) or not artifact_root.is_dir():
            raise ProjectError('Downloaded artifacts must be isolated inside the repository .reports directory')
        if not report_path.resolve().is_relative_to(artifact_root):
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
    selected = []
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
        log_hash = file_digest(log)
        if result.get('log_sha256') != log_hash:
            raise ProjectError(f'Verification log changed after the run: {key}')
        selected.append({'id': key, 'status': 'passed', 'command': result['command'], 'exit_code': 0,
                         'log_sha256': log_hash})
    receipt = {
        'schema_version': '1.0', 'task_id': task_id, 'task_definition_sha256': task_definition_hash(task),
        'run_id': report['run_id'], 'recorded_at': datetime.now(timezone.utc).isoformat(),
        'source': report['source'], 'report_sha256': digest(report_path.read_bytes()),
        'environment': report['environment'], 'checks': selected,
        'project_definition_sha256': report['project_definition_sha256'],
        'claim_scope': 'Completed task at the recorded execution inputs; not a release or current-worktree guarantee.',
    }
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
    sub.add_parser('status')
    context = sub.add_parser('context')
    context.add_argument('task_id', nargs='?')
    sub.add_parser('readiness')
    sub.add_parser('checkpoint')
    history = sub.add_parser('history')
    history.add_argument('--base', default=os.environ.get('SCOPEHUB_REVIEW_BASE'))
    record = sub.add_parser('record')
    record.add_argument('task_id')
    record.add_argument('--report', type=Path, default=ROOT / '.reports/test-report.json')
    record.add_argument('--artifact-root', type=Path, help='Isolated downloaded artifact directory containing runs/')
    args = parser.parse_args()
    try:
        if args.command == 'check':
            project = load_project()
            result = {'status': 'passed', 'tasks': len(project['tasks']), 'decisions': len(project['decisions']),
                      'checks': len(project['checks'])}
        elif args.command == 'status':
            result = workfront()
        elif args.command == 'context':
            result = task_context(ROOT, args.task_id)
        elif args.command == 'readiness':
            result = readiness()
        elif args.command == 'history':
            result = review_history(ROOT, args.base)
        elif args.command == 'checkpoint':
            result = checkpoint()
        else:
            result = archive_evidence(ROOT, args.task_id, args.report, args.artifact_root)
        print(json.dumps(result, indent=2))
        if args.command == 'readiness' and not result['release_ready']:
            raise SystemExit(1)
    except (ProjectError, subprocess.CalledProcessError, OSError, KeyError) as error:
        print(json.dumps({'status': 'failed', 'error': str(error)}))
        raise SystemExit(1) from error


if __name__ == '__main__':
    main()
