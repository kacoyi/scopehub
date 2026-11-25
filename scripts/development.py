"""Validate development ownership and derive navigable views from canonical records.

This module owns repository navigation only. It never grants product runtime
authority or upgrades archived evidence into current verification.
"""

from functools import cache
import json
import os
import shlex
import subprocess
from pathlib import Path


# Budgets apply to the repository navigation view, including repeated subtrees.
MAX_ENGINEERING_DEPTH = 64
MAX_ENGINEERING_VALUES = 65_536


def repository_path(root, relative):
    path = Path(relative)
    if path.is_absolute() or not path.parts or '..' in path.parts:
        raise ValueError(f'Expected a repository-relative path: {relative}')
    result = root / path
    if not result.resolve().is_relative_to(root.resolve()):
        raise ValueError(f'Path escapes repository: {relative}')
    return result


def engineering_operations(manifest):
    # Historical paths name negotiated payloads even when the manifest retains an older binding.
    operations = []
    for operation in manifest['operations']:
        binding = operation.get('wire_bindings', {}).get(manifest['protocol_version'], operation)
        record = {field: operation[field] for field in
                  ('name', 'actors', 'effect', 'confirmation', 'undoable', 'schema_requirement', 'transport')
                  if field in operation}
        operations.append({**record, 'input_spec': binding['input'], 'output_spec': binding['output']})
    return operations


def expand_engineering_sources(root, value):
    """Build a bounded effective agreement from repository-owned source links."""
    index = (root / 'engineering-contract-v1.json').resolve()
    remaining = MAX_ENGINEERING_VALUES

    @cache
    def read_document(path):
        return json.loads(path.read_text(encoding='utf-8'))

    def expand(item, ancestors, depth):
        nonlocal remaining
        if depth > MAX_ENGINEERING_DEPTH or remaining <= 0:
            location = ancestors[-1] if ancestors else (index, '')
            raise ValueError(f'Engineering source expansion limit at {location[0]}#{location[1]}')
        remaining -= 1
        if isinstance(item, list):
            return [expand(child, ancestors, depth + 1) for child in item]
        if not isinstance(item, dict):
            return item
        if '$source' not in item:
            return {key: expand(child, ancestors, depth + 1) for key, child in item.items()}

        link = item['$source']
        if (set(item) != {'$source'} or not isinstance(link, dict) or set(link) != {'path', 'pointer'}
                or not all(isinstance(field, str) for field in link.values())):
            raise ValueError(f'Invalid engineering source link: {item}')
        path = repository_path(root, link['path']).resolve()
        if path == index:
            raise ValueError(f'Invalid engineering source link: {item}')
        target = (path, link['pointer'])
        if target in ancestors:
            raise ValueError(f'Cyclic engineering source link: {link}')
        document = read_document(path)
        if target == (index.parent / 'contracts/contract.json', '/operations'):
            selected = engineering_operations(document)
        else:
            selected = select_source(document, link)
        return expand(selected, (*ancestors, target), depth + 1)

    return expand(value, (), 0)


def select_source(value, reference, engineering_root=None):
    pointer = reference['pointer']
    if pointer and not pointer.startswith('/'):
        raise ValueError(f'Invalid JSON Pointer: {pointer}')
    for token in pointer.split('/')[1:]:
        if engineering_root is not None and isinstance(value, dict) and '$source' in value:
            value = expand_engineering_sources(engineering_root, value)
            engineering_root = None
        # Reject malformed escapes rather than returning the wrong requirement.
        if '~' in token.replace('~0', '').replace('~1', ''):
            raise ValueError(f'Invalid JSON Pointer escape: {pointer}')
        token = token.replace('~1', '/').replace('~0', '~')
        try:
            if isinstance(value, list):
                if not token.isdigit() or (len(token) > 1 and token.startswith('0')):
                    raise ValueError('Invalid array index')
                value = value[int(token)]
            else:
                value = value[token]
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise ValueError(f'Missing source pointer: {reference}') from error
    return expand_engineering_sources(engineering_root, value) if engineering_root is not None else value


def resolve_source(root, reference):
    """Navigate the engineering index, or read a raw JSON Pointer in another repository record."""
    path = repository_path(root, reference['path']).resolve()
    engineering_root = root if path == (root / 'engineering-contract-v1.json').resolve() else None
    return select_source(json.loads(path.read_text(encoding='utf-8')), reference, engineering_root)


def validate_record(root, project, record):
    modules = {item['id']: item for item in record['modules']}
    if len(modules) != len(record['modules']):
        raise ValueError('Duplicate development module ID')
    capabilities = {item['id'] for item in record['capabilities']}
    if len(capabilities) != len(record['capabilities']):
        raise ValueError('Duplicate capability ID')
    owned_paths = [path for item in modules.values() for path in item['paths']]
    if len(owned_paths) != len(set(owned_paths)):
        raise ValueError('A module path has multiple owners')
    states = record['data_ownership']
    if len({item['id'] for item in states}) != len(states):
        raise ValueError('A business state has multiple ownership records')
    for state in states:
        if state['owner'] not in modules or state['writer'] not in modules:
            raise ValueError(f'Unknown state owner or writer: {state["id"]}')
        if state['status'] == 'implemented' and any(modules[state[key]]['status'] != 'implemented'
                                                    for key in ['owner', 'writer']):
            raise ValueError(f'Implemented state uses planned modules: {state["id"]}')
    for item in modules.values():
        unknown = set(item['dependencies']) - modules.keys()
        if unknown or item['id'] in item['dependencies']:
            raise ValueError(f'Invalid module dependencies: {item["id"]}: {sorted(unknown)}')
        if not set(item['checks']).issubset(project['checks']):
            raise ValueError(f'Unknown module checks: {item["id"]}')
        for relative in [*item['paths'], *item['public_entries']]:
            path = repository_path(root, relative)
            if item['status'] == 'implemented' and not path.exists():
                raise ValueError(f'Missing implemented module path: {relative}')
        for entry in item['public_entries']:
            if not any(Path(entry).is_relative_to(Path(path)) for path in item['paths']):
                raise ValueError(f'Public entry outside module: {entry}')
    visiting, visited = set(), set()

    def visit(key):
        if key in visiting:
            raise ValueError(f'Development dependency cycle: {key}')
        if key in visited:
            return
        visiting.add(key)
        for dependency in modules[key]['dependencies']:
            visit(dependency)
        visiting.remove(key)
        visited.add(key)

    for key in modules:
        visit(key)
    for item in record['capabilities']:
        resolve_source(root, item['source'])
        if not item['task_ids'] or not set(item['task_ids']).issubset(project['tasks']):
            raise ValueError(f'Capability lacks tracked delivery: {item["id"]}')
        surviving = [project['tasks'][key] for key in item['task_ids']
                     if project['tasks'][key]['status'] != 'cancelled']
        if not surviving:
            raise ValueError(f'Capability has only cancelled tasks: {item["id"]}')
        if item['delivery'] == 'current-contract' and all(task.get('delivery') == 'later' for task in surviving):
            raise ValueError(f'Current capability has only later delivery: {item["id"]}')
    for task in project['tasks'].values():
        if task['status'] in {'done', 'cancelled'}:
            continue  # Historical plans are immutable, not retroactive policy targets.
        plan = task.get('plan', {})
        if task['status'] == 'active' and not {'implementation', 'evolution'}.issubset(plan):
            raise ValueError(f'Active task needs implementation and evolution plans: {task["id"]}')
        if 'implementation' not in plan:
            continue
        navigation = plan['implementation']
        if not set(navigation['modules']).issubset(modules):
            raise ValueError(f'Unknown task modules: {task["id"]}')
        for entry in navigation['entrypoints']:
            if not repository_path(root, entry).is_file():
                raise ValueError(f'Missing task entrypoint: {entry}')
        for reference in navigation['rules']:
            resolve_source(root, reference)


def product_brief(project):
    spec, manifest = project['spec'], project.get('manifest', {})
    product = spec.get('product', {})
    development = project.get('development', {})
    return {
        'purpose': product.get('purpose'),
        'source': {'path': 'engineering-contract-v1.json', 'pointer': '/product'},
        'version_scope': product.get('v1_scope', []),
        'excluded': product.get('excluded', []),
        'first_scenario': manifest.get('first_product_scenario'),
        'implemented_operations': [dict(name=op['name'], scope=op.get('implementation_scope'))
                                   for op in manifest.get('operations', []) if op.get('implementation') == 'implemented'],
        'connections': {key: manifest.get(key) for key in ['real_market_data_configured', 'cloud_models_configured']},
        'collaboration': development.get('collaboration', {}),
        'capabilities': [{**item, 'tasks': [{'id': key, 'status': project['tasks'][key]['status']}
                                         for key in item['task_ids']]}
                         for item in development.get('capabilities', [])],
        'claim_scope': 'Derived product scope and recorded implementation, not observed user acceptance or release readiness.',
    }


def effective_decisions(project, task):
    ids = set(task['decision_refs'])
    for key in list(ids):
        while key in project['replacements']:
            key = project['replacements'][key]
            ids.add(key)
    return sorted(ids)


def focused_context(project, key, phase):
    task = project['tasks'][key]
    development = project.get('development', {})
    plan = task.get('plan', {})
    navigation = plan.get('implementation', {})
    modules = [item for item in development.get('modules', []) if item['id'] in navigation.get('modules', [])]
    decision_paths = project.get('decision_paths', {})
    decisions = []
    for decision_id in effective_decisions(project, task):
        decision = project['decisions'][decision_id]
        successor = project['replacements'].get(decision_id)
        entry = {
            'id': decision_id,
            'title': decision['title'],
            'decision': decision['decision'],
            'status': decision['status'],
            'superseded_by': successor,
            'path': decision_paths.get(decision_id),
        }
        if decision['status'] == 'accepted' and successor is None:
            entry['revisit_when'] = decision['revisit_when']
        decisions.append(entry)
    result = {
        'task': task,
        'phase': phase,
        'product': {'purpose': project['spec'].get('product', {}).get('purpose'),
                    'source': {'path': 'engineering-contract-v1.json', 'pointer': '/product'},
                    'brief_command': 'pnpm project:brief'},
        'context_paths': list(dict.fromkeys([*plan.get('context', []), 'project/development.json'
                                           if development else 'project/delivery.json'])),
        'dependencies': [{'id': item, 'title': project['tasks'][item]['title'],
                          'status': project['tasks'][item]['status']} for item in task['depends_on']],
        'decisions': decisions,
        'checks': [project['checks'][item] for item in task['checks']],
        'scenarios': [item for item in project['delivery']['scenarios'] if key in item['task_ids']],
        'feedback': [item for item in project['feedback']['items'] if key in item['task_ids']],
        'modules': modules,
        'data_ownership': [item for item in development.get('data_ownership', [])
                           if item['owner'] in navigation.get('modules', [])
                           or item['writer'] in navigation.get('modules', [])],
        'consultation': development.get('collaboration', {}).get('ask_owner_when', []),
        'policy_sources': [{'path': 'project/development.json', 'pointer': '/quality'}] if development else [],
        'source_command': 'pnpm project:source <path> <json-pointer>',
        'commands': ['pnpm project:impact', 'pnpm check:changed', f'pnpm check --task {key}',
                     'pnpm project:doctor', 'pnpm check:fast',
                     f'pnpm project:context {key} --detail', f'pnpm project:context {key} --phase verify'],
        'limitations': ['Navigation is an index; inspect implementation and referenced rules.',
                        'Development collaboration does not specify embedded-AI interactions.'],
    }
    herdr = development.get('herdr')
    if herdr:
        result['development_host'] = {
            'host': herdr['host'],
            'profiles': herdr['profiles'],
            'policy': {'path': 'project/development.json', 'pointer': '/herdr'},
            'launch': f'pnpm project:herdr {key} <implement|review|coordinate> --assignment "<scope>"',
        }
        if 'reasoning_efforts' in herdr:
            result['development_host']['default_reasoning_effort'] = herdr['reasoning_efforts'][0]
    if phase == 'verify':
        result['quality_review'] = development.get('quality', {}).get('review', [])
        result['commands'] += [f'pnpm project:record {key} --report <run-report>',
                               'pnpm project:history --base <reviewed-commit>']
    elif phase == 'handoff':
        result['quality_review'] = development.get('quality', {}).get('review', [])
        result['handoff'] = ['Report delivered behavior, engineering evidence, actual owner feedback and remaining limits.',
                             'Archive task evidence, preserve a source checkpoint and update focus/next action.',
                             'Inspect live CI and PR integration; merge and release need applicable authorization.']
        result['commands'] += ['pnpm project:checkpoint', 'pnpm project:ci',
                               'pnpm project:integration --pr <number>']
    return result


def evidence_view(root, project, source, latest, current, *, report_error=None):
    """Explain omissions without changing the harness/readiness pass criteria."""
    observations = {item['id']: item for item in (latest or {}).get('checks', [])}
    checks = []
    for check in project['checks'].values():
        result = observations.get(check['id'], {})
        if report_error:
            reason = 'invalid_report'
        elif not current:
            reason = 'stale_report' if latest else 'no_report'
        elif check['status'] != 'implemented':
            reason = 'not_implemented' if check['status'] == 'planned' else 'retired'
        else:
            reason = result.get('reason_code') or result.get('status', 'not_recorded')
        checks.append({'id': check['id'], 'implementation': check['status'],
                       'latest_status': result.get('status'), 'explanation': reason})
    historical = []
    for task in project['tasks'].values():
        if task['status'] != 'done':
            continue
        for reference in task['evidence']:
            receipt = json.loads(repository_path(root, reference['path']).read_text(encoding='utf-8'))
            historical.append({'task_id': task['id'], 'receipt': reference['path'],
                               'run_id': receipt['run_id'], 'system': receipt['environment'].get('system'),
                               'checks': [item['id'] for item in receipt['checks']],
                               'matches_execution_inputs': receipt['source']['sha256'] == source['sha256'],
                               'scope': 'Historical task evidence; not current verification or release acceptance.'})
    return {'latest_selection': (latest or {}).get('selection'), 'checks': checks,
            'historical_task_evidence': historical,
            'claim_scope': 'Explanatory view only. Readiness and current verification still use their original strict gates.'}


def read_only_project_command(*arguments, source_commit=None):
    """Render the pinned interpreter form used by the read-only review role."""
    python = './.venv/Scripts/python.exe' if os.name == 'nt' else './.venv/bin/python'
    command = [python, '-B', 'scripts/project.py', *arguments]
    if source_commit is not None:
        command += ['--source', source_commit]
    return subprocess.list2cmdline(command) if os.name == 'nt' else shlex.join(command)


def project_overview(state, *, read_only=False):
    focus = state.get('focus')
    verification = state['verification']
    evidence = state['evidence_overview']
    counts = {}
    if verification['current']:
        for check in evidence['checks']:
            reason = check['explanation']
            counts[reason] = counts.get(reason, 0) + 1
    local = 'current' if verification['current'] else (
        'invalid' if verification['report_error'] else 'stale' if verification['report_available'] else 'unobserved')
    retained = state.get('retained_failures', {})
    retained_count = retained.get('matching_inputs', {}).get('count')
    reviews = [review for review in state['periodic_reviews'] if review.get('state')]
    attention = ['evidence_missing', 'evidence_invalid', 'intake_needed', 'recheck_needed', 'owned_action', 'checks_needed',
                 'policy_needed', 'due']
    first = next((review for status in attention for review in reviews if review['state'] == status), None)
    if first is None:
        first = next((review for review in reviews if review['review_due']), None)

    def navigation(*arguments):
        if read_only:
            if arguments[0] == 'status':
                arguments += ('--read-only',)
            return read_only_project_command(*arguments)
        return shlex.join(('pnpm', 'project:' + arguments[0], *arguments[1:]))

    def action(review):
        result = {key: review[key] for key in
                  ('id', 'state', 'owner', 'trigger', 'command', 'finding', 'next_review_by',
                   'problem', 'report_sha256') if key in review}
        if read_only and (command := result.get('command')):
            parts = shlex.split(command)
            if parts[:1] == ['pnpm'] and parts[1] in {'project:source', 'project:context'}:
                result['command'] = navigation(parts[1].split(':', 1)[1], *parts[2:])
            elif parts[:4] == ['uv', 'run', 'python', 'scripts/project.py']:
                result['command'] = navigation(*parts[4:])
        return result

    context = navigation('context', focus['id']) if focus else navigation('brief')
    active = set(state['active_task_ids'])
    ready = set(state['ready_task_ids'])
    completed = set(state['completed_task_ids'])
    unassigned = set(state['feedback_needing_assignment'])

    def finding_action(finding):
        identity = finding['id']
        candidates = [task for task in finding['task_ids'] if task not in completed]
        task = next((task for task in candidates if task in active or task in ready), None)
        status = 'owned_action' if task else 'planned_follow_through'
        if identity in unassigned or not candidates:
            status = 'assignment_needed'
            command = navigation('source', 'project/feedback.json', '--id', identity)
        else:
            phase = ('--phase', 'verify') if task else ()
            command = navigation('context', task or candidates[0], *phase)
        return {'id': identity, 'state': status, 'owner': 'development AI',
                'trigger': 'Open ' + finding['severity'] + ' feedback', 'command': command}

    opened = [finding for finding in state['unresolved_feedback'] if finding['status'] == 'open']
    critical = next((finding_action(finding) for finding in opened if finding['severity'] == 'critical'), None)
    owned = next((finding_action(finding) for finding in opened if finding['severity'] == 'major'
                  and active.intersection(finding['task_ids'])), None)
    failure = None
    if retained_count:
        original = next(iter(retained['matching_inputs'].get('items', [])), {})
        failure = {'owner': 'development AI', 'state': 'retained_failure',
                   'trigger': 'Retained failure on matching inputs', 'command': navigation('status', '--detail')}
        if report := original.get('report'):
            failure.update(id=original['check_id'], command=navigation('diagnostic', 'identify', report))
    missing = action(first) if first and first['state'] in {'evidence_missing', 'evidence_invalid', 'intake_needed'} else None
    needs_assignment = next((finding_action(finding) for finding in opened if finding['id'] in unassigned), None)
    next_action = (critical or failure or missing or owned or (action(first) if first else None)
                   or needs_assignment or {'owner': 'development AI', 'command': context})
    other_urgent = next((candidate for candidate in (critical, failure, missing, owned)
                         if candidate and candidate != next_action), None)
    calendar = next((review for review in reviews if review['review_due']
                     and review['state'] not in {'evidence_missing', 'evidence_invalid'}), None)
    maintenance_action = first if first and first['id'] != next_action.get('id') else calendar
    additional = (action(maintenance_action) if maintenance_action
                  and maintenance_action['id'] != next_action.get('id') else None)
    return {
        'purpose': state.get('purpose'),
        'focus': {key: focus[key] for key in ('id', 'title', 'status')} if focus else None,
        'backlog': {
            'ready': len(state['ready_task_ids']), 'active': len(state['active_task_ids']),
            'blocked': len(state['blocked']), 'open_findings': len(state['unresolved_feedback']),
            'unassigned_findings': len(state['feedback_needing_assignment']),
        },
        'verification': {
            'local': local, 'run_id': verification['run_id'], 'outcome': verification['recorded_status'],
            'current_check_counts': counts or None,
            'historical_task_receipts': len(evidence['historical_task_evidence']),
            'retained_failures_on_matching_inputs': retained_count,
            'retained_failure_scan': retained.get('scan', {}).get('coverage'),
        },
        'gates': {'hosted': 'pnpm project:ci',
                  'product_release_and_native': navigation('readiness'),
                  'scope': 'Hosted, release and physical gates require separate evidence.'},
        'maintenance': {'periodic_attention': sum(review['review_due'] or review['state'] in attention
                                                  for review in reviews),
                        'waiting_for_event': sum(review['state'] == 'waiting_for_event' for review in reviews),
                        'dependency_review_due': (state.get('dependency_maintenance') or {}).get('review_due'),
                        'follow_through': additional},
        'next': next_action,
        'also_urgent': other_urgent,
        'details': {'delivery': navigation('brief'), 'focused_work': context,
                    'status': navigation('status'), 'receipts': navigation('status', '--detail'),
                    'modules': navigation('source', 'project/development.json', '/modules')},
    }


def status_view(state, *, detail=False, overview=False, read_only=False):
    if detail:
        return state
    summary = project_overview(state, read_only=read_only)
    if overview:
        return summary
    result = {'overview': summary, **state}
    result['periodic_reviews'] = [
        {key: review[key] for key in ('id', 'next_review_by', 'review_due',
                                      'state', 'owner', 'command', 'problem', 'finding', 'repair_recovery', 'overseen_staging') if key in review}
        for review in state['periodic_reviews']
    ]
    focus = state.get('focus')
    if focus:
        result['focus'] = {key: focus[key] for key in
                           ['id', 'title', 'milestone', 'status', 'depends_on', 'next_action', 'blocker']}
        result['context_command'] = f'pnpm project:context {focus["id"]}'
    evidence = state['evidence_overview']
    groups = {}
    for item in evidence['checks']:
        groups.setdefault(item['explanation'], []).append(item['id'])
    historical = evidence['historical_task_evidence']
    result['evidence_overview'] = {
        'latest_selection': evidence['latest_selection'],
        'check_groups': groups,
        'historical': {'task_count': len({item['task_id'] for item in historical}),
                       'receipt_count': len(historical),
                       'systems': sorted({item['system'] for item in historical if item['system']}),
                       'scope': 'Historical task evidence, not current or release acceptance.'},
        'detail_command': 'pnpm project:status --detail',
    }
    return result
