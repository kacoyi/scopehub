"""Project periodic observations onto the facets they can actually establish."""

from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import PurePosixPath
import re
import stat

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


BRANCH_TRACE = 'dispositions for unique-history branch tips'
LOCAL_FAST = 'passing comparable local fast timing'
PRODUCT_TIMING = 'comparable product delivery timing'
SCOPE_ACTIONS = {
    BRANCH_TRACE: ('coordinator', 'Trace each exact tip to its task, PR or recoverable copy',
                   'pnpm project:branches'),
    LOCAL_FAST: ('verification owner', 'Inspect a retained passing fast run with matching execution inputs',
                 'pnpm project:timing --report .reports/test-report.json'),
    PRODUCT_TIMING: ('development AI', 'Compare comparable slices after observing their usable interaction and verified main',
                     'uv run python scripts/project.py observe-maintenance verification-timing --help'),
}


def tip_identity(repository, sha):
    if not git_commit(sha):
        raise ValueError('A unique branch needs an exact observed tip.')
    return hashlib.sha256(f'{repository}:{sha}'.encode('ascii')).hexdigest()


def local_fast_observation(data, observed):
    for row in reversed(data.get('samples', [])):
        if (row.get('ci') is not None or row.get('profile') != 'fast' or
                row.get('report_status') != 'passed' or row.get('review_exclusion', True) is not None
                or row.get('working_tree_dirty') is not False or not git_commit(row.get('source_commit'))):
            continue
        elapsed = row['seconds']['harness']
        recorded = utc(row['recorded_at'])
        if type(elapsed) in {float, int} and 0 <= elapsed < 86400 and timedelta(0) <= observed - recorded <= timedelta(days=7):
            if re.fullmatch(r'[a-zA-Z0-9._-]{1,100}', row['run_id']):
                return {name: row[name] for name in ('run_id', 'source_commit', 'recorded_at')} | {'seconds': elapsed}
    return None


def prepare_facts(key, data, source, input_digests, policy, protection=None, staging=None,
                  product_timing=None, traced_tips=()):
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
                     local_fast=local_fast_observation(data, utc(clock)), product_delivery=product_timing,
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
        repository = data['repository']
        unique = sorted({tip_identity(repository, row['sha']) for row in branches if row['status'] == 'unique_history'})
        facts.update(repository=repository, branch=data['default_branch']['name'],
                     unique_tips=unique, traced_tips=sorted({tip_identity(repository, sha) for sha in traced_tips}),
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
                     activity_complete=not data['activity_issues'], staging=staging)
    else:
        raise ValueError('Unknown periodic review.')
    return facts


def project_facts(key, facts, policy, protection_violations):
    fields = {
        'weekly-regression-continuity': {'repository', 'status', 'run'},
        'verification-timing': {'scopes', 'captured_samples', 'input_complete', 'adverse', 'local_fast', 'product_delivery'},
        'branches-and-pull-requests': {'repository', 'branch', 'branch_statuses', 'pull_statuses', 'protected',
                                       'delete_branch_on_merge', 'inventory_complete', 'protection', 'unique_tips', 'traced_tips'},
        'worktrees-and-evidence': {'integration_ref', 'row_states', 'holds', 'archive_configured',
                                   'archive_error', 'activity_complete', 'staging'},
    }
    if set(facts) != fields[key] | {'observed_at', 'source_commit', 'input_sha256'}:
        raise ValueError('Expected the canonical public observation fields.')
    observed = utc(facts['observed_at'])
    source = facts['source_commit']
    digests = facts['input_sha256']
    if ((not git_commit(source) and not (source is None and key == 'verification-timing'))
            or len(digests) not in {1, 2, 3} or not all(sha256(value) for value in digests)):
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
        if not facts['local_fast']:
            pending.append(LOCAL_FAST)
        if not facts['product_delivery']:
            pending.append(PRODUCT_TIMING)
        adverse = facts['adverse'] or any(item['recent_objective_breaches'] >= 2 for item in scopes)
        identity = sorted({str(run) for item in scopes for run in item['recent_runs']})
        evidence = {'objective_reviews': scopes, 'local_fast': facts['local_fast'],
                    'product_delivery': facts['product_delivery']}
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
        unique, traced = facts['unique_tips'], facts['traced_tips']
        if (len(unique) > branches.count('unique_history') or bool(unique) != ('unique_history' in branches)
                or len(unique) + len(traced) > 512 or not all(sha256(value) for value in [*unique, *traced])):
            raise ValueError('Expected bounded identities for unique and traced branch tips.')
        untraced = sorted(set(unique) - set(traced))
        if untraced:
            pending.append(BRANCH_TRACE)
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
                    'protection_observed': protection_bound, 'delete_branch_on_merge': facts['delete_branch_on_merge'],
                    'untraced_tips': untraced}
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
        stage = facts['staging']
        if stage and (set(stage) != {'task_id', 'manifest_sha256', 'accepted_source', 'regular_files', 'observed_at'}
                      or not re.fullmatch(r'M[0-9]+-[0-9]{3}', stage['task_id'])
                      or len(digests) != 2 or stage['manifest_sha256'] != digests[1]
                      or not git_commit(stage['accepted_source']) or not 0 < stage['regular_files'] <= 8192
                      or utc(stage['observed_at']) > observed + timedelta(days=1)):
            raise ValueError('Expected a source-bound named staging observation.')
        pending.append(EXTERNAL_STAGING)
        adverse = 'unretained_local_evidence' in holds
        evidence = {'integration_tip': source, 'dispositions': dict(Counter(states)), 'holds': holds,
                    'verified_external_staging': [stage] if stage else []}
    else:
        raise ValueError('Unknown periodic review.')
    return {**evidence, 'pending': pending}, identity, adverse


EXTERNAL_STAGING = 'project-owned staging outside registered worktrees'


def observation_clock(record):
    clocks = [utc(record['observed_at'])]
    if protection := record.get('facts', {}).get('protection'):
        if protection['branch_sha'] == record['source_commit']:
            clocks.append(utc(protection['observed_at']))
    return max(clocks)


def same_day_progress(previous, current, old_evidence, new_evidence, adverse, previously_adverse, inputs, added_finding):
    if (previous['source_commit'] != current['source_commit']
            or utc(previous['observed_at']).date() != utc(current['observed_at']).date()
            or not inputs or observation_clock(current).date() != utc(current['observed_at']).date()):
        return False
    later = observation_clock(current) > observation_clock(previous)
    if adverse and added_finding:
        return later
    pending = set(new_evidence.get('pending') or new_evidence.get('unverified_boundaries') or ())
    previous_pending = set(old_evidence.get('pending') or old_evidence.get('unverified_boundaries') or ())
    retained = set(previous.get('facts', {}).get('input_sha256', ())) <= set(
        current.get('facts', {}).get('input_sha256', ()))
    return bool((pending < previous_pending and (later or retained))
                or (previously_adverse and not adverse and later and pending <= previous_pending))


def staging_file(root, relative):
    name = PurePosixPath(relative)
    if (name.is_absolute() or not name.parts or '..' in name.parts
            or name.as_posix() != relative or '\\' in relative or ':' in relative):
        raise ValueError('The named archive contains a nonportable path.')
    file = root.joinpath(*name.parts)
    if file.resolve(strict=True) != file or not stat.S_ISREG(file.stat().st_mode):
        raise ValueError('Expected a regular nonsymlink archive file.')
    return file


def verify_staging_archive(directory, expected):
    """Verify only explicitly indexed files in a named retained archive."""
    root = directory.resolve(strict=True)
    if not root.is_dir() or not sha256(expected):
        raise ValueError('Expected a named archive and its SHA-256 manifest digest.')
    manifest = staging_file(root, 'SHA256SUMS')
    if manifest.stat().st_size > 64 * 1024:
        raise ValueError('The staging manifest exceeds its size limit.')
    data = manifest.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError('The staging manifest digest does not match.')
    originals = {}
    total = 0
    for line in data.decode('utf-8').splitlines():
        value, relative = line.split('  ', 1)
        path = staging_file(root, relative)
        size = path.stat().st_size
        total += size
        if not sha256(value) or relative in originals or len(originals) >= 256 or total > 16 * 1024 * 1024:
            raise ValueError('The staging manifest is ambiguous or exceeds its resource limits.')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != value:
            raise ValueError('A staging manifest member does not match its digest.')
        originals[relative] = (value, raw)
    index_sha, raw = originals['restoration-index.json']
    indexed = json.loads(raw)
    preparation = json.loads(originals['prepare.json'][1])
    task = json.loads(originals['accepted-task.json'][1])
    if (preparation['index_sha256'] != index_sha or indexed['original'] != preparation['source']
            or not git_commit(task['protected_ancestor']) or not task['evidence']):
        raise ValueError('The named staging archive is missing its matching index and accepted task source.')
    entries = indexed['entries']
    if not isinstance(entries, dict) or len(entries) > 8192 or len(entries) != preparation['original_tree_entries']:
        raise ValueError('The staging restoration index is incomplete or exceeds its entry limit.')
    mirror = root / 'files'
    if not mirror.is_dir() or mirror.is_symlink():
        raise ValueError('The staging archive does not have a regular files directory.')
    total = count = 0
    for relative, entry in entries.items():
        if entry['type'] != 'file':
            continue
        path = staging_file(mirror, relative)
        size = entry['size']
        total += size
        if type(size) is not int or size < 0 or total > 4 * 1024**3 or path.stat().st_size != size:
            raise ValueError('A staging file changed size or exceeded the verification budget.')
        with path.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != entry['sha256']:
                raise ValueError('A retained staging file does not match the restoration index.')
        count += 1
    if not count:
        raise ValueError('The named staging archive has no verified regular files.')
    return {'task_id': task['task'], 'manifest_sha256': expected, 'accepted_source': task['protected_ancestor'],
            'regular_files': count, 'observed_at': utc(preparation['observed_at']).isoformat(),
            'task_evidence': task['evidence']}


def producer_recovery(finding, recovery_rows):
    """Use the existing timing producer's comparable post-fix result at the same scope."""
    scope = finding.get('verification_budget', {})
    keys = ('repository', 'event', 'profile', 'platform')
    if not git_commit(scope.get('repair_commit')) or any(key not in scope for key in keys):
        return None
    for row in recovery_rows:
        if row['feedback_id'] != finding['id'] or row['scope'] != {key: scope[key] for key in keys}:
            continue
        recovery = row['recovery']
        if recovery['status'] != 'recovered' or recovery['repair_commit'] != scope['repair_commit']:
            continue
        confirmed = [cohort['recovered'] for cohort in recovery['comparisons'] if cohort.get('recovered')]
        if not confirmed:
            continue
        latest = max(confirmed, key=lambda sample: utc(sample['recorded_at']))
        return {'repair_commit': recovery['repair_commit'], 'scope': row['scope'],
                'run_id': latest['run_id'], 'source_commit': latest['source_commit'],
                'recorded_at': latest['recorded_at']}
    return None


def periodic_records(delivery):
    return {item['id']: item for item in delivery['scaffold_maintenance']['periodic_reviews']}
