"""Validate development ownership and derive navigable views from canonical records.

This module owns repository navigation only. It never grants product runtime
authority or upgrades archived evidence into current verification.
"""

import json
from pathlib import Path


def repository_path(root, relative):
    path = Path(relative)
    if path.is_absolute() or not path.parts or '..' in path.parts:
        raise ValueError(f'Expected a repository-relative path: {relative}')
    result = root / path
    if not result.resolve().is_relative_to(root.resolve()):
        raise ValueError(f'Path escapes repository: {relative}')
    return result


def resolve_source(root, reference):
    """Resolve a JSON Pointer without evaluating instructions from its contents."""
    path = repository_path(root, reference['path'])
    value = json.loads(path.read_text(encoding='utf-8'))
    pointer = reference['pointer']
    if pointer and not pointer.startswith('/'):
        raise ValueError(f'Invalid JSON Pointer: {pointer}')
    for token in pointer.split('/')[1:]:
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
    return value


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
        'decisions': [{'id': item, 'title': project['decisions'][item]['title'],
                       'decision': project['decisions'][item]['decision'],
                       'superseded_by': project['replacements'].get(item),
                       'path': decision_paths.get(item)} for item in effective_decisions(project, task)],
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
        'commands': ['pnpm project:doctor', 'pnpm check:fast', f'pnpm check --task {key}',
                     f'pnpm project:context {key} --detail', f'pnpm project:context {key} --phase verify'],
        'limitations': ['Navigation is an index; inspect implementation and referenced rules.',
                        'Development collaboration does not specify embedded-AI interactions.'],
    }
    if 'herdr' in development:
        result['development_host'] = {
            'host': development['herdr']['host'],
            'profiles': development['herdr']['profiles'],
            'policy': {'path': 'project/development.json', 'pointer': '/herdr'},
            'launch': f'pnpm project:herdr {key} <implement|review|coordinate> --assignment "<scope>"',
        }
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


def status_view(state, *, detail=False):
    """Keep the opening view short while preserving every omission in detail."""
    if detail:
        return state
    result = dict(state)
    result['periodic_reviews'] = [
        {key: review[key] for key in ('id', 'next_review_by', 'review_due')}
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
