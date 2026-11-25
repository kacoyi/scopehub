"""Bound periodic review on the observations available to ordinary project commands."""

from datetime import date, datetime, timedelta, timezone
import re

REVIEW_KIND = 'scopehub-periodic-review-v1'
REVIEW_SOURCES = {
    'weekly-regression-continuity': ('2026-11-23', 'pnpm project:ci'),
    'verification-timing': ('2026-11-21', 'pnpm project:timing --recent 12'),
    'branches-and-pull-requests': ('2026-11-21', 'pnpm project:branches'),
    'worktrees-and-evidence': ('2026-11-21', 'pnpm project:worktree retirement'),
    'compatibility-and-recovery': ('2026-11-21', 'pnpm check --task M0-111'),
}
COMPATIBILITY_PATHS = {'scripts/timing.py', 'scripts/recovery.py', 'scripts/diagnostic_archive.py',
                       'contracts/schemas/protocol.schema.json'}


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


def inspect_evidence(review_id, data):
    """Return the bounded projection of an existing command result and its observed outcome."""
    if review_id == 'weekly-regression-continuity':
        weekly = data['weekly_regression']
        status, run = weekly['status'], weekly['run']
        if status not in {'passed', 'failed', 'missing', 'blocked'}:
            raise ValueError('Expected an observed scheduled verification status.')
        if not run and status != 'missing':
            raise ValueError('Expected the exact scheduled workflow run.')
        if run:
            if (not positive_id(run['id']) or not positive_id(run['run_attempt'])
                    or run['event'] != 'schedule' or not git_commit(run['head_sha'])):
                raise ValueError('Expected an exact scheduled workflow identity.')
            run = {key: run[key] for key in
                   ('id', 'run_attempt', 'head_sha', 'event', 'created_at', 'status', 'conclusion')}
            if status == 'passed' and (run['status'], run['conclusion']) != ('completed', 'success'):
                raise ValueError('A passed scheduled review needs a successful run.')
        timestamp = utc(data['observed_at'])
        age = timestamp - utc(run['created_at']) if run else None
        if age is not None and age < timedelta(0):
            raise ValueError('A scheduled run cannot occur after its observation.')
        attention = status != 'passed' or age > timedelta(days=8)
        identity = [run['id'], run['run_attempt']] if run else ['missing', data['observed_at']]
        return {'observed_at': data['observed_at'], 'weekly_regression': {'status': status, 'run': run}}, identity, attention
    if review_id == 'verification-timing':
        # A timed window, unlike an empty or unusable collection, can support a disposition.
        scopes = []
        for item in data['objective_reviews']:
            if item['event'] not in {'pull_request', 'push'} or item['profile'] != 'full' or item['sample_count'] < 3:
                continue
            runs = item['recent_runs']
            if (len(runs) != 3 or len(set(runs)) != 3 or not all(positive_id(run) for run in runs)
                    or item['platform'] not in {'Linux', 'Windows'}):
                raise ValueError('Expected three distinct ordinary hosted run identities.')
            scopes.append({key: item[key] for key in
                           ('event', 'profile', 'platform', 'sample_count', 'recent_runs', 'recent_objective_breaches')})
        if not scopes or len(scopes) > 8 or data['input_issues']:
            raise ValueError('A timing review requires usable comparable observations.')
        budget = data['budget_follow_up']
        attention = bool(budget['repeated_breaches'] or budget['sustained_growth']
                         or any(scope['recent_objective_breaches'] >= 2 for scope in scopes))
        # Compact scope findings are retained as flags. Run identities remain exact.
        return {'observed_at': data['observed_at'], 'objective_reviews': scopes, 'input_issues': [],
                'budget_follow_up': {key: bool(budget[key]) for key in ('repeated_breaches', 'sustained_growth')},
                }, sorted({str(run) for item in scopes for run in item['recent_runs']}), attention
    if review_id == 'branches-and-pull-requests':
        default = data['default_branch']
        summary = data['summary']
        if (not git_commit(default['sha']) or not summary['branches']
                or not isinstance(summary['open_pull_requests'], dict)
                or data['status'] not in {'passed', 'failed'}
                or data['delete_branch_on_merge'] not in {True, False}):
            raise ValueError('Expected an observed branch and pull-request inventory.')
        for counts in summary.values():
            if not isinstance(counts, dict) or any(type(n) is not int or n < 0 for n in counts.values()):
                raise ValueError('Invalid branch inventory counts.')
        attention = data['status'] != 'passed' or not data['delete_branch_on_merge']
        return {key: data[key] for key in ('observed_at', 'status', 'default_branch', 'summary',
                                          'delete_branch_on_merge')}, [data['observed_at']], attention
    if review_id == 'worktrees-and-evidence':
        if not git_commit(data['integration_tip']) or not isinstance(data['worktrees'], list) or not data['worktrees']:
            raise ValueError('Expected a registered worktree and evidence inventory.')
        if len(data['worktrees']) > 1024:
            raise ValueError('Too many worktree rows in a review.')
        rows = []
        serious = False
        for row in data['worktrees']:
            if row['state'] not in {'held', 'candidate'} or not isinstance(row['holds'], list):
                raise ValueError('Invalid worktree disposition.')
            holds = row['holds']
            serious |= any('evidence' in hold or 'archive' in hold for hold in holds)
            rows.append({'state': row['state'], 'holds': holds})
        error = bool(data['archive_error'] or data['activity_issues'])
        return {'observed_at': data['observed_at'], 'integration_tip': data['integration_tip'],
                'worktrees': rows, 'archive_error': error, 'activity_issues': []}, [data['observed_at']], error or serious
    if review_id == 'compatibility-and-recovery':
        return {'receipt': data['receipt']}, [data['receipt']], False
    raise ValueError('Unknown periodic review.')


def periodic_records(delivery):
    return {item['id']: item for item in delivery['scaffold_maintenance']['periodic_reviews']}
