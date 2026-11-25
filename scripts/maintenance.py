"""Project periodic observations onto the facets they can actually establish."""

from collections import Counter
from datetime import datetime, timedelta, timezone
import re

REVIEW_KIND = 'scopehub-periodic-review-v1'
REVIEW_SOURCES = {
    'weekly-regression-continuity': ('2026-11-23', 'pnpm project:ci'),
    'verification-timing': ('2026-11-21', 'pnpm project:timing --recent 12'),
    'branches-and-pull-requests': ('2026-11-21', 'pnpm project:branches'),
    'worktrees-and-evidence': ('2026-11-21', 'pnpm project:worktree retirement'),
    'compatibility-and-recovery': ('2026-11-21', 'uv run python scripts/project.py observe-maintenance compatibility-and-recovery'),
}
POLICY_COMMAND = 'pnpm project:source project/delivery.json /scaffold_maintenance/periodic_reviews'
COMPATIBILITY_BASELINE = 'contracts/reviews/2026-11-21-repository-maintenance.json'
CONSUMER_MODULES = {
    'domain': ('rust-wire-consumer', ('rust-contracts-workers',)),
    'worker-host': ('rust-wire-consumer', ('rust-contracts-workers',)),
    'worker-runtime': ('python-wire-consumer', ('python-types', 'python-contracts-workers')),
    'persistence': ('workspace-migrations', ('state-migrations',)),
    'development-tooling': ('verification-archive', ('project-state', 'python-contracts-workers')),
}


def in_scope(path, scopes):
    return any(path == scope.rstrip('/') or path.startswith(scope.rstrip('/') + '/') for scope in scopes)


def compatibility_boundaries(paths, development, delivery):
    """Account for actual changed sources under existing module and contract owners."""
    named_data_owners = {row['owner'] for row in development.get('data_ownership', [])
                         if row['owner'] not in {'ui', 'chart-adapter'}}
    modules = [row for row in development.get('modules', [])
               if row['id'] in CONSUMER_MODULES or row['id'] in named_data_owners]
    protocol = next((row['checks'] for row in delivery['change_types'] if row['id'] == 'protocol'), [])
    boundaries = {}
    for path in paths:
        if not path.endswith(('.rs', '.py', '.ts', '.js', '.mjs', '.json', '.sql', '.toml')):
            continue
        if {'tests', 'fixtures', 'examples', 'evidence'}.intersection(path.split('/')[:-1]):
            continue
        if in_scope(path, ('contracts/schemas', 'contracts/generated')) and protocol:
            boundaries[path] = ('published-protocol', tuple(protocol))
            continue
        for module in modules:
            if in_scope(path, module['paths']):
                key = module['id']
                boundaries[path] = CONSUMER_MODULES.get(key, (f'needs-classification:{key}', ()))
                break
    return boundaries


def boundary_summary(boundaries):
    results = {}
    for path, (identity, checks) in sorted(boundaries.items()):
        item = results.setdefault(identity, {'paths': [], 'checks': sorted(checks)})
        item['paths'].append(path)
    return results


def utc(value):
    if not isinstance(value, str):
        raise ValueError('Expected a dated observation.')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('The observation time must include a UTC offset.')
    return parsed.astimezone(timezone.utc)


def git_commit(value):
    return isinstance(value, str) and re.fullmatch('[a-f0-9]{40}', value) is not None


def positive_id(value):
    return type(value) in {int, str} and re.fullmatch('[1-9][0-9]*', str(value)) is not None


def sha256(value):
    return isinstance(value, str) and re.fullmatch('[a-f0-9]{64}', value) is not None


def protective_settings(actual):
    """Keep the inputs used by protection.violations, without actor names or URLs."""
    checks = actual.get('required_status_checks') or {}
    review = actual.get('required_pull_request_reviews')
    if isinstance(review, dict):
        review = {
            'dismiss_stale_reviews': review.get('dismiss_stale_reviews') is True,
            'required_approving_review_count': int(review['required_approving_review_count']),
            'bypass_pull_request_allowances': {'configured': any((review.get('bypass_pull_request_allowances') or {}).values())},
        }
    else:
        review = None
    def enabled(key):
        value = (actual.get(key) or {}).get('enabled')
        return value if type(value) is bool else None

    return {
        'required_status_checks': {
            'strict': checks.get('strict') is True,
            'checks': [{'context': item['context'], 'app_id': item['app_id']} for item in checks.get('checks', [])],
        },
        'required_pull_request_reviews': review,
        **{key: {'enabled': enabled(key)}
           for key in ('enforce_admins', 'allow_force_pushes', 'allow_deletions', 'required_conversation_resolution')},
    }


def prepare_facts(key, data, source, input_digests, policy, protection=None):
    """Retain only the source observations needed to independently derive the public claim."""
    clock = data['recent_collection']['collected_at'] if key == 'verification-timing' else data['observed_at']
    facts = {'observed_at': utc(clock).isoformat(), 'source_commit': source, 'input_sha256': input_digests}
    if key == 'weekly-regression-continuity':
        weekly = data['weekly_regression']
        run = weekly['run']
        facts.update(repository=data['repository'], status=weekly['status'], run={
            name: run[name] for name in ('id', 'run_attempt', 'event', 'head_sha', 'head_branch', 'created_at',
                                        'status', 'conclusion')
        } if run else None)
    elif key == 'verification-timing':
        fields = ('repository', 'event', 'profile', 'platform', 'sample_count', 'recent_runs', 'recent_objective_breaches')
        facts.update(scopes=[{name: item[name] for name in fields} for item in data['objective_reviews']
                             if item['event'] in {'pull_request', 'push'} and item['profile'] == 'full'
                             and item['sample_count'] >= 3],
                     captured_samples=[{name: row[name] for name in ('run_id', 'platform', 'event')}
                                       for row in data['recent_collection'].get('collected_samples', [])],
                     input_complete=not data['input_issues'],
                     adverse=bool(data['budget_follow_up']['repeated_breaches'] or data['budget_follow_up']['sustained_growth']))
    elif key == 'branches-and-pull-requests':
        branches = data['branches']
        pulls = data['open_pull_requests']
        live = next((branch for branch in branches if branch['status'] == 'default'), {})
        if live.get('sha') != source or live.get('name') != data['default_branch']['name']:
            raise ValueError('Branch inventory is missing the observed default branch row.')
        observed = None
        if protection:
            observed = {name: protection[name] for name in ('repository', 'branch', 'observed_at')}
            observed.update(branch_sha=protection.get('branch_sha'),
                            settings=protective_settings(protection['observed']))
            # A required context comes from the policy. Extra private labels do not leave the input.
            expected_contexts = {check['context'] for check in policy['configuration']['required_status_checks']['checks']}
            checks = observed['settings']['required_status_checks']
            checks['checks'] = [check for check in checks['checks'] if check['context'] in expected_contexts]
        facts.update(repository=data['repository'], branch=data['default_branch']['name'],
                     branch_statuses=sorted(row['status'] for row in branches),
                     pull_statuses=sorted(row['status'] for row in pulls), protected=live.get('protected'),
                     delete_branch_on_merge=data['delete_branch_on_merge'],
                     inventory_complete=not data['errors'], protection=observed)
    elif key == 'worktrees-and-evidence':
        rows = data['worktrees']
        facts.update(integration_ref=data['integration_ref'],
                     row_states=sorted(row['state'] for row in rows),
                     holds=sorted({hold for row in rows for hold in row['holds']}),
                     archive_configured=bool(data['archive_root']), archive_error=bool(data['archive_error']),
                     activity_complete=not data['activity_issues'])
    else:
        raise ValueError('Unknown periodic review.')
    return facts


def project_facts(key, facts, policy, protection_violations):
    fields = {
        'weekly-regression-continuity': {'repository', 'status', 'run'},
        'verification-timing': {'scopes', 'captured_samples', 'input_complete', 'adverse'},
        'branches-and-pull-requests': {'repository', 'branch', 'branch_statuses', 'pull_statuses', 'protected',
                                       'delete_branch_on_merge', 'inventory_complete', 'protection'},
        'worktrees-and-evidence': {'integration_ref', 'row_states', 'holds', 'archive_configured',
                                   'archive_error', 'activity_complete'},
    }
    if set(facts) != fields[key] | {'observed_at', 'source_commit', 'input_sha256'}:
        raise ValueError('Expected the canonical public observation fields.')
    observed = utc(facts['observed_at'])
    source = facts['source_commit']
    digests = facts['input_sha256']
    if not git_commit(source) or len(digests) not in {1, 2} or not all(sha256(value) for value in digests):
        raise ValueError('Expected bounded observation identity and input digests.')
    pending = []
    adverse = False
    identity = [facts['observed_at']]
    if key == 'weekly-regression-continuity':
        run = facts['run']
        if facts['repository'] != policy['repository'] or (run and run['head_branch'] != policy['branch']):
            raise ValueError('Weekly verification belongs to another repository or branch.')
        status = facts['status']
        if status not in {'passed', 'failed', 'blocked', 'missing'}:
            raise ValueError('Scheduled verification is unavailable.')
        if run:
            if (not positive_id(run['id']) or not positive_id(run['run_attempt']) or run['head_sha'] != source
                    or run['event'] != 'schedule'):
                raise ValueError('Expected the exact scheduled protected run.')
            actual = ('blocked' if run['status'] != 'completed' else
                      'passed' if run['conclusion'] == 'success' else 'failed')
            age = observed - utc(run['created_at'])
            if actual != status or age < timedelta(0):
                raise ValueError('Weekly status and clock must match the scheduled run.')
            adverse = status != 'passed' or age > timedelta(days=8)
            identity = [run['id'], run['run_attempt']]
        else:
            if status != 'missing':
                raise ValueError('Expected the exact scheduled run.')
            adverse = True
        evidence = {'weekly_regression': {'status': status, 'run': run}}
    elif key == 'verification-timing':
        scopes = facts['scopes']
        captured = facts['captured_samples']
        if (len(scopes) > 32 or len(captured) > 192 or any(
                not positive_id(row['run_id']) or row['platform'] not in {'Linux', 'Windows'}
                or row['event'] not in {'pull_request', 'push'} for row in captured)):
            raise ValueError('Expected a bounded timing collection.')
        collected = {(str(row['run_id']), row['platform'], row['event']) for row in captured}
        refreshed = set()
        for item in scopes:
            runs = item['recent_runs']
            if (item['repository'] != policy['repository'] or item['profile'] != 'full' or
                    item['platform'] not in {'Linux', 'Windows'} or item['event'] not in {'pull_request', 'push'}
                    or len(runs) != 3 or len(set(runs)) != 3 or not all(positive_id(run) for run in runs)):
                raise ValueError('Timing scope does not match ordinary protected repository verification.')
            scope = item['platform'], item['event']
            if any((str(run), *scope) in collected for run in runs):
                refreshed.add(scope)
        pending = [f'{platform} {event}' for platform in ('Linux', 'Windows') for event in ('pull_request', 'push')
                   if (platform, event) not in refreshed]
        if not facts['input_complete']:
            pending.append('complete timing inputs')
        adverse = facts['adverse'] or any(item['recent_objective_breaches'] >= 2 for item in scopes)
        identity = sorted({str(run) for item in scopes for run in item['recent_runs']})
        evidence = {'objective_reviews': scopes}
    elif key == 'branches-and-pull-requests':
        if facts['repository'] != policy['repository'] or facts['branch'] != policy['branch']:
            raise ValueError('Branch inventory belongs to another repository or branch.')
        branches, pulls = facts['branch_statuses'], facts['pull_statuses']
        if (len(branches) + len(pulls) > 2048 or branches.count('default') != 1 or
                any(value not in {'default', 'protected', 'in_use', 'reachable_unreferenced', 'unique_history', 'uncertain'}
                    for value in branches) or
                any(value not in {'fork_head', 'already_integrated', 'outside_default_history', 'uncertain'}
                    for value in pulls)):
            raise ValueError('Expected bounded canonical inventory classifications.')
        if (not facts['inventory_complete'] or 'uncertain' in branches or 'uncertain' in pulls
                or facts['protected'] is None or facts['delete_branch_on_merge'] is None):
            pending.append('complete branch and pull-request inventory')
        protection = facts['protection']
        if protection and (protection['repository'], protection['branch']) != (policy['repository'], policy['branch']):
            raise ValueError('Protection observation belongs to another repository or branch.')
        protection_bound = bool(protection and protection['branch_sha'] == source and
                                abs(utc(protection['observed_at']) - observed) <= timedelta(days=1))
        if not protection_bound:
            pending.append('effective protection at the inventoried revision')
        adverse = bool(facts['protected'] is False or facts['delete_branch_on_merge'] is False or
                       (protection_bound and protection_violations(protection['settings'], policy['configuration'])))
        evidence = {'default_branch': {'name': facts['branch'], 'sha': source},
                    'summary': {'branches': dict(Counter(branches)), 'open_pull_requests': dict(Counter(pulls))},
                    'protection_observed': protection_bound, 'delete_branch_on_merge': facts['delete_branch_on_merge']}
    elif key == 'worktrees-and-evidence':
        if facts['integration_ref'] != 'refs/remotes/origin/' + policy['branch']:
            raise ValueError('Worktree inventory belongs to another integration ref.')
        states, holds = facts['row_states'], facts['holds']
        if (not states or len(states) > 1024 or set(states) - {'held', 'candidate'} or len(holds) > 64
                or any(not re.fullmatch('[a-z_]{1,64}', hold) for hold in holds)):
            raise ValueError('Expected bounded worktree dispositions.')
        if not facts['activity_complete'] or any(re.search(r'(incomplete|unavailable|unverified|changed)$', hold)
                                                for hold in holds):
            pending.append('complete worktree, process and checkout observations')
        if not facts['archive_configured'] or facts['archive_error']:
            pending.append('available evidence archive')
        # Retirement observes registered worktrees, not the independently owned staging scope.
        pending.append('project-owned staging outside registered worktrees')
        adverse = 'unretained_local_evidence' in holds
        evidence = {'integration_tip': source, 'dispositions': dict(Counter(states)), 'holds': holds}
    else:
        raise ValueError('Unknown periodic review.')
    return {**evidence, 'pending': pending}, identity, adverse


def periodic_records(delivery):
    return {item['id']: item for item in delivery['scaffold_maintenance']['periodic_reviews']}
