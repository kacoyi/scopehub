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

if __package__:
    from .diagnostic_archive import DiagnosticArchiveError, validate_locator
else:
    from diagnostic_archive import DiagnosticArchiveError, validate_locator


HERDR_ROLE_PHASES = {'implement': 'implement', 'review': 'verify', 'coordinate': 'handoff'}
ARCHIVE_CONFIGURATION = ('Use --archive-root, SCOPEHUB_ARCHIVE_ROOT, or git config --local '
                         'scopehub.archiveRoot <absolute-path>, in that order.')


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


def feedback_row_routable(item):
    return (isinstance(item, dict)
            and isinstance(item.get('task_ids'), list)
            and all(isinstance(key, str) for key in item['task_ids'])
            and isinstance(item.get('severity'), str)
            and isinstance(item.get('status'), str))


def feedback_for_task(items, task_id):
    severity = {'critical': 0, 'major': 1, 'minor': 2}
    linked = [(index, item) for index, item in enumerate(items)
              if feedback_row_routable(item) and task_id in item['task_ids']]
    return sorted(linked, key=lambda entry: (entry[1]['status'] != 'open',
                                             severity.get(entry[1]['severity'], 3)))


def read_only_project_command(*arguments, source_commit=None):
    """Render the pinned interpreter form used by the read-only review role."""
    python = './.venv/Scripts/python.exe' if os.name == 'nt' else './.venv/bin/python'
    command = [python, '-B', 'scripts/project.py', *arguments]
    if source_commit is not None:
        command += ['--source', source_commit]
    return subprocess.list2cmdline(command) if os.name == 'nt' else shlex.join(command)


def bound_review_navigation(task_id, source_commit, current_commands):
    context = ('context', task_id, '--phase', 'verify')
    return {
        'commands': [read_only_project_command(*context, '--detail', source_commit=source_commit),
                     read_only_project_command(*context, source_commit=source_commit)],
        'source_command': read_only_project_command('source') + f' "<path>" "<json-pointer>" --source {source_commit}',
        'current_checkout_commands': current_commands,
    }


def feedback_source_command(index, source_commit=None):
    pointer = f'/items/{index}'
    return project_navigation('source', 'project/feedback.json', pointer, source_commit=source_commit)


def diagnostic_commands(records, *, read_only=False):
    for record in records:
        locators = record.get('diagnostic_reports')
        for locator in locators if isinstance(locators, list) else ():
            try:
                validate_locator(locator)
            except DiagnosticArchiveError:
                continue  # Historical rows can predate the current locator validation.
            arguments = (locator['run_id'], locator['source_commit'], locator['report_sha256'])
            yield project_navigation('diagnostic', 'resolve', *arguments, read_only=read_only)


def excerpt(text, limit, *, observation=False):
    if not isinstance(text, str):
        return ''
    line = ' '.join(''.join(char if char.isprintable() else ' ' for char in text).split())
    if len(line) <= limit:
        return line
    if observation:
        # Quote whole sentences so that a qualification can stay with its assertion.
        prefix, stop, _ = line[:limit + 1].rpartition('. ')
        return prefix + stop + '…' if prefix else 'Read the exact observation in the full record.'
    return line[:limit].rstrip() + '…'


def task_launch_excerpt(task, linked_feedback, source_commit=None):
    """Start with recorded facts and a lookup. The full task context stays separate."""
    result = {
        'task_status': excerpt(task.get('status'), 16),
        'outcome': excerpt(task.get('plan', {}).get('outcome') or task.get('title'), 210),
        'next_action': excerpt(task.get('next_action'), 280),
    }
    if linked_feedback:
        index, item = linked_feedback[0]
        finding = {
            'status': excerpt(item.get('status'), 16),
            'observation': excerpt(item.get('observation'), 360, observation=True),
        }
        resolution = item.get('resolution')
        if isinstance(resolution, dict) and resolution.get('cause'):
            finding['recorded_cause'] = excerpt(resolution['cause'], 210, observation=True)
        original = next(diagnostic_commands((item,), read_only=source_commit is not None), None)
        if original:
            finding['original_diagnostic'] = original
            finding['archive_configuration'] = ARCHIVE_CONFIGURATION
        else:
            finding['original_source'] = excerpt(item.get('source'), 240)
            finding['raw_evidence'] = 'This record cites its source. Raw bytes require a separately retained original.'
        finding['full_record'] = feedback_source_command(index, source_commit)
        result['finding'] = finding
        if len(linked_feedback) > 1:
            result['additional_findings_in_task_context'] = len(linked_feedback) - 1
    return {name: value for name, value in result.items() if value}


def causal_context(task, linked_feedback, source_commit=None):
    context = {
        'observation': 'Record the failure and impact in feedback.observation. Preserve the original in diagnostic_reports.',
        'mechanism': 'Use plan.hypotheses and risks for the proposed mechanism, its distinguishing test and the verification '
                     'boundary that let the failure through. Use resolution.cause, correction and prevention for supported findings.',
    }
    if task['status'] in {'planned', 'active', 'blocked'}:
        context['next_observation'] = (
            'Follow task.next_action. Keep unproven attribution open. '
            'A passing rerun establishes its own outcome.'
        )
    if linked_feedback:
        context['original_records'] = [
            {'feedback': item['id'], 'command': feedback_source_command(index, source_commit)}
            for index, item in linked_feedback[:3]
        ]
    for key, records in (('diagnostic_commands', (item for _, item in linked_feedback)),
                         ('related_task_diagnostics', (task,))):
        commands = []
        for command in diagnostic_commands(records, read_only=source_commit is not None):
            if command not in commands:
                commands.append(command)
            if len(commands) == 3:
                break
        if commands:
            context[key] = commands
            context['archive_configuration'] = ARCHIVE_CONFIGURATION
    return context


def focused_context(project, key, phase, *, source_commit=None):
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
    items = project['feedback']['items']
    linked_feedback = feedback_for_task(items, key)
    product_source = {'path': 'engineering-contract-v1.json', 'pointer': '/product'}
    product = {'purpose': project['spec'].get('product', {}).get('purpose'), 'source': product_source}
    if source_commit is None:
        product['brief_command'] = 'pnpm project:brief'
    else:
        product['source_command'] = read_only_project_command(
            'source', product_source['path'], product_source['pointer'], source_commit=source_commit,
        )
    result = {
        'task': task,
        'phase': phase,
        'product': product,
        'context_paths': list(dict.fromkeys([*plan.get('context', []), 'project/development.json'
                                           if development else 'project/delivery.json'])),
        'dependencies': [{'id': item, 'title': project['tasks'][item]['title'],
                          'status': project['tasks'][item]['status']} for item in task['depends_on']],
        'decisions': decisions,
        'checks': [project['checks'][item] for item in task['checks']],
        'scenarios': [item for item in project['delivery']['scenarios'] if key in item['task_ids']],
        'feedback': [item for _, item in sorted(linked_feedback)],
        'modules': modules,
        'data_ownership': [item for item in development.get('data_ownership', [])
                           if item['owner'] in navigation.get('modules', [])
                           or item['writer'] in navigation.get('modules', [])],
        'consultation': development.get('collaboration', {}).get('ask_owner_when', []),
        'policy_sources': [{'path': 'project/development.json', 'pointer': '/quality'}] if development else [],
        'source_command': 'pnpm project:source <path> <json-pointer>',
        'commands': ['pnpm project:impact', 'pnpm check:changed', f'pnpm check --task {key}',
                     'pnpm project:doctor', 'pnpm check:fast'],
        'limitations': ['Navigation is an index; inspect implementation and referenced rules.',
                        'Development collaboration does not specify embedded-AI interactions.'],
    }
    if source_commit is None:
        result['commands'] += [f'pnpm project:context {key} --detail', f'pnpm project:context {key} --phase verify']
    herdr = development.get('herdr')
    if herdr:
        result['development_host'] = {
            'host': herdr['host'],
            'role_phases': HERDR_ROLE_PHASES,
            'profiles': herdr['profiles'],
            'policy': {'path': 'project/development.json', 'pointer': '/herdr'},
            'launch': f'pnpm project:herdr {key} <implement|review|coordinate> --assignment "<scope>"',
        }
        if source_commit is not None:
            result['development_host']['launch_scope'] = 'Launches a role on the current checkout.'
        if 'reasoning_efforts' in herdr:
            result['development_host']['default_reasoning_effort'] = herdr['reasoning_efforts'][0]
    if linked_feedback or plan.get('hypotheses') or task.get('diagnostic_reports'):
        result['causal_investigation'] = causal_context(task, linked_feedback, source_commit)
    if phase in {'verify', 'handoff'} and herdr and task['status'] in {'planned', 'active', 'blocked'}:
        review_commit = source_commit or '<full-commit>'
        review_launch = f'pnpm project:herdr {key} review --source {review_commit} --assignment "<scope>"'
        result['review_handoff'] = {
            'launch': review_launch,
            'when': 'For a consequential caller, data or recovery change, send a clean active-task source to '
                    'an independent reviewer before attaching completion evidence or marking the task done.',
            'assignment': 'Name the caller or data invariant, its independent expected outcome, and a real '
                          'control or plausible failure boundary. Include any remaining causal uncertainty and next observation.',
            'follow_up': 'Use the reviewed source and its matching checks for closure. If substantive source '
                         'or scope changes after review, send the changed boundary on its new clean source. '
                         'An additive receipt can cite the same reviewed source when the reviewed behavior '
                         'and task definition are unchanged. Keep the existing history and evidence checks.',
        }
        if source_commit is not None:
            result['review_handoff']['launch_scope'] = 'Launches on the current checkout after clean-source admission.'
        result['commands'].append(review_launch)
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
    if source_commit is not None:
        result.update(bound_review_navigation(key, source_commit, result['commands']))
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


def project_navigation(*arguments, read_only=False, source_commit=None):
    if read_only or source_commit is not None:
        if arguments[0] == 'status':
            arguments += ('--read-only',)
        return read_only_project_command(*arguments, source_commit=source_commit)
    return shlex.join(('pnpm', 'project:' + arguments[0], *arguments[1:]))


def unassigned_feedback(items, tasks):
    severity = {'critical': 0, 'major': 1, 'minor': 2}
    return sorted((item for item in items if item['status'] == 'open' and
                   all(tasks[key]['status'] in {'done', 'cancelled'} for key in item['task_ids'])),
                  key=lambda item: (severity[item['severity']], item['id']))


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
    scan_coverage = retained.get('scan', {}).get('coverage')
    reviews = [review for review in state['periodic_reviews'] if review.get('state')]
    attention = ['evidence_missing', 'evidence_invalid', 'intake_needed', 'recheck_needed', 'owned_action', 'checks_needed',
                 'policy_needed', 'due']
    first = next((review for status in attention for review in reviews if review['state'] == status), None)
    if first is None:
        first = next((review for review in reviews if review['review_due']), None)

    def navigation(*arguments):
        return project_navigation(*arguments, read_only=read_only)

    def action(review):
        result = {key: review[key] for key in
                  ('id', 'state', 'owner', 'trigger', 'command', 'finding', 'next_review_by',
                   'problem', 'report_sha256', 'scope_follow_through') if key in review}
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
    important = sorted((finding for finding in opened if finding['severity'] == 'critical'),
                       key=lambda finding: (not active.intersection(finding['task_ids']), finding['id']))
    critical = finding_action(important[0]) if important else None
    owned = next((finding_action(finding) for finding in opened if finding['severity'] == 'major'
                  and active.intersection(finding['task_ids'])), None)
    failure = None
    if retained_count:
        original = next(iter(retained['matching_inputs'].get('items', [])), {})
        failure = {'owner': 'development AI', 'state': 'retained_failure',
                   'trigger': 'Retained failure on matching inputs', 'command': navigation('status', '--retained-failures')}
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
            **({'unassigned_lookup': navigation('source', 'project/feedback.json', '--unassigned',
                                                 *(['--read-only'] if read_only else []))}
               if unassigned else {}),
        },
        'verification': {
            'local': local, 'run_id': verification['run_id'], 'outcome': verification['recorded_status'],
            'current_check_counts': counts or None,
            'historical_task_receipts': len(evidence['historical_task_evidence']),
            'retained_failures_on_matching_inputs': retained_count,
            'retained_failure_scan': scan_coverage,
            **({'retained_failures_lookup': navigation('status', '--retained-failures')}
               if retained_count or scan_coverage == 'incomplete' else {}),
        },
        'gates': {'hosted': 'pnpm project:ci',
                  'product_release_and_native': navigation('readiness'),
                  'scope': 'Hosted, release and physical gates require separate evidence.'},
        'maintenance': {'periodic_attention': sum(review['review_due'] or review['state'] in attention
                                                  for review in reviews),
                        'waiting_for_event': sum(review['state'] == 'waiting_for_event' or
                                                 review.get('repair_recovery', {}).get('state') == 'waiting_for_event'
                                                 for review in reviews),
                        'dependency_review_due': (state.get('dependency_maintenance') or {}).get('review_due'),
                        'follow_through': additional},
        'next': next_action,
        'also_urgent': other_urgent,
        'details': {'delivery': navigation('brief'), 'focused_work': context,
                    'status': navigation('status'), 'receipts': navigation('status', '--detail'),
                    'modules': navigation('source', 'project/development.json', '/modules')},
    }


def retained_failure_page(retained, *, after=None, read_only=False):
    matching = retained['matching_inputs']
    items = matching['items']
    start = 0
    if after is not None:
        for index, row in enumerate(items):
            if after == f"{row['run_id']}/{row['check_id']}":
                start = index + 1
                break
        else:
            raise ValueError('That matching failure is no longer pending. Restart the retained failure lookup.')
    page = items[start:start + 5]
    result = {
        'basis': retained['basis'], 'scan_coverage': retained['scan']['coverage'],
        'matching_count': matching['count'], 'other_or_unverified_count': retained['other_or_unverified_inputs']['count'],
        'items': [
            {'check_id': row['check_id'], 'run_id': row['run_id'], 'report': row['report'],
             'command': project_navigation('diagnostic', 'identify', row['report'], read_only=read_only)}
            for row in page
        ],
    }
    if start + len(page) < len(items):
        last = page[-1]
        cursor = f"{last['run_id']}/{last['check_id']}"
        result['more'] = project_navigation('status', '--retained-failures', '--after', cursor, read_only=read_only)
    return result


def status_view(state, *, detail=False, overview=False, read_only=False):
    if detail:
        return state
    summary = project_overview(state, read_only=read_only)
    if overview:
        return summary
    result = {'overview': summary, **state}
    result['periodic_reviews'] = [
        {key: review[key] for key in ('id', 'next_review_by', 'review_due',
                                      'state', 'owner', 'command', 'problem', 'finding', 'repair_recovery', 'overseen_staging', 'scope_follow_through', 'follow_through_task') if key in review}
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
