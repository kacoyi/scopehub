"""Review retained verification timings or explicitly inspect a GitHub Actions run."""

import argparse
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
from statistics import median
import subprocess
from tempfile import TemporaryDirectory

if __package__:
    from .verification_scope import SCOPED_PROFILES
else:
    from verification_scope import SCOPED_PROFILES

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = '.github/workflows/verify.yml'
AUTOMATIC_PROFILES = SCOPED_PROFILES | {'full'}
SAMPLE_KIND = 'scopehub-verification-timing-v1'
ARCHIVE_KIND = 'scopehub-timing-archive-v1'
TIMING_FILE = 'verification-timing.json'
REVIEW_ID = 'verification-timing'
CACHE_STATES = {'cold', 'partial', 'warm', 'not_requested', 'not_restored'}
NORMAL_EVENTS = {'pull_request', 'push'}
PLATFORMS = {'ubuntu-latest': 'Linux', 'windows-latest': 'Windows'}
HARNESS_STEPS = {
    'Run pnpm check',
    'Run uv run python scripts/check.py --require-windows',
    'Run uv run python scripts/check.py --profile release --require-windows',
    'Verify the selected task',
    *(f'Verify {profile} changes' for profile in SCOPED_PROFILES),
}
BROWSER_STEPS = {
    'Install the pinned chart-test browser on Windows',
    'Start the pinned chart-test browser on Linux',
    'Install and start the pinned chart-test browser',
}
EXPECTED_FULL_OMISSIONS = {'not_implemented', 'unsupported_platform'}
WINDOW = 3
MIN_GROWTH_SECONDS = 60
MIN_GROWTH_RATIO = 0.15
LOCAL_BUDGET = 5 * 60
HOSTED_BUDGET = 15 * 60
MAX_RECENT = 12
DEFAULT_RECENT = 8
COMPACT_LIMIT = 128 * 1024


class TimingError(ValueError):
    """Timing data is unavailable or inconsistent."""


def command(argv):
    result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise TimingError(f'{argv[0]} command failed: {result.stderr.strip()}')
    return result.stdout


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def json_bytes(data):
    return (json.dumps(data, indent=2) + '\n').encode('utf-8')


def github_identity(root):
    if __package__:
        from .ci import github_identity as pinned_identity
    else:
        from ci import github_identity as pinned_identity
    return pinned_identity(root)


def hosted_repository(github_actions):
    if github_actions:
        repository = os.environ.get('GITHUB_REPOSITORY', '')
        if (os.environ.get('GITHUB_ACTIONS') != 'true' or not os.environ.get('GH_TOKEN')
                or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository)):
            raise TimingError('Hosted discovery requires the current Actions repository and its job token.')
        return repository
    return github_identity(ROOT)


def duration(value):
    return value if type(value) in (int, float) and 0 <= value < float('inf') else None


def timestamp(value):
    try:
        result = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return result if result.tzinfo is not None else None


def elapsed(start, end):
    return (end - start).total_seconds() if start and end and start <= end else None


def short_label(value):
    return value if isinstance(value, str) and re.fullmatch(r'[a-zA-Z0-9._/-]{1,128}', value) else None


def commit_id(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[0-9a-f]{40}', value))


def positive_id(value):
    return bool(re.fullmatch(r'[1-9][0-9]*', str(value)))


def report_sample(report, *, cache=None):
    """Project only coverage, timing and run identity. Commands, paths and logs stay out."""
    environment = report.get('environment', {})
    selection = report.get('selection', {})
    ci = report.get('ci')
    if ci:
        ci = {key: short_label(ci.get(key)) for key in
              ('repository', 'run_id', 'run_attempt', 'event', 'head_sha', 'checkout_sha')}
    checks = [
        {'id': short_label(check.get('id')), 'status': short_label(check.get('status')),
         'exit_code': check.get('exit_code') if type(check.get('exit_code')) is int else None,
         'reason_code': short_label(check.get('reason_code')), 'duration_seconds': duration(check.get('duration_seconds'))}
        for check in report.get('checks', [])
    ]
    selected = sorted(filter(None, (short_label(key) for key in selection.get('checks', []))))
    rows = {item['id']: item for item in checks}
    omitted = [rows[key] for key in selected if key in rows and rows[key]['status'] != 'passed']
    profile = short_label(selection.get('profile'))
    status = short_label(report.get('status'))
    platform = short_label(environment.get('system'))
    expected = (profile == 'full' and all(
        item['status'] == 'blocked' and item['reason_code'] in EXPECTED_FULL_OMISSIONS
        and (item['reason_code'] != 'unsupported_platform' or platform != 'Windows')
        for item in omitted
    ))
    coverage_complete = (
        bool(selected) and len(set(selected)) == len(selection.get('checks', []))
        and set(selected) <= rows.keys() and len(rows) == len(checks)
        and None not in rows and any(rows[key]['status'] == 'passed' for key in selected)
        and not any(item['status'] == 'failed'
                    or (item['status'] == 'passed' and (item['id'] not in selected or item['exit_code'] != 0))
                    for item in checks)
        and (not omitted or expected)
        and (status == 'passed' or (status == 'blocked' and bool(omitted) and expected))
        and report.get('source_unchanged') is not False and report.get('project_unchanged') is not False
        and report.get('native_requirement', {}).get('satisfied') is not False
    )
    measured = [item['duration_seconds'] for item in checks if item['duration_seconds'] is not None]
    recorded_at = timestamp(report.get('generated_at'))
    source = report.get('source', {})
    source_commit = source.get('git_head')
    inputs_digest = source.get('sha256')
    dirty = source.get('working_tree_dirty')
    identity_complete = (
        commit_id(source_commit) and report.get('source_unchanged') is True and report.get('project_unchanged') is True
        and (ci is None or (all(ci.values()) and commit_id(ci['head_sha']) and ci['checkout_sha'] == source_commit
                           and positive_id(ci['run_id']) and positive_id(ci['run_attempt'])))
    )
    return {
        'kind': SAMPLE_KIND,
        'run_id': short_label(report.get('run_id')),
        'recorded_at': recorded_at.isoformat() if recorded_at else None,
        'source_commit': source_commit if commit_id(source_commit) else None,
        'inputs_sha256': inputs_digest if isinstance(inputs_digest, str) and re.fullmatch(r'[0-9a-f]{64}', inputs_digest) else None,
        'working_tree_dirty': dirty if isinstance(dirty, bool) else None,
        'profile': profile, 'task_id': short_label(selection.get('task_id')),
        'platform': platform, 'arch': short_label(environment.get('machine')),
        'cache': cache if cache in CACHE_STATES else 'unknown',
        'report_status': status, 'coverage_complete': coverage_complete, 'identity_complete': identity_complete,
        'check_work_complete': bool(measured) and all(
            item['duration_seconds'] is not None for item in checks if item['status'] in {'passed', 'failed'}
        ),
        'selected': selected, 'checks': checks, 'ci': ci,
        'seconds': {
            'harness': duration(report.get('timing', {}).get('elapsed_seconds')),
            'check_work': round(sum(measured), 3) if measured else None,
        },
    }


def slowest_checks(sample):
    return sorted(
        (row for row in sample['checks'] if row['duration_seconds'] is not None),
        key=lambda row: row['duration_seconds'], reverse=True,
    )[:5]


def job_summary(sample):
    seconds = sample['seconds']
    harness = seconds['harness']
    work = seconds['check_work']
    checks = sample['checks']
    selected = [row for row in checks if row['id'] in sample['selected']]
    passing = sum(row['status'] == 'passed' for row in selected)
    lines = [
        '### Verification timing',
        '',
        f"- Scope: {sample['profile'] or 'unavailable'} / {sample['platform'] or 'unavailable'}"
        f" / Cargo cache {sample['cache']}",
        f"- Harness result: {sample['report_status'] or 'unavailable'}"
        f" ({passing}/{len(sample['selected'])} selected passed;"
        f" {'platform coverage complete' if sample['coverage_complete'] else 'incomplete or unavailable coverage'})",
        f"- Harness elapsed: {'unavailable' if harness is None else f'{harness:.1f} s'}",
        f"- Observed check work (sum; checks may overlap): {'unavailable' if work is None else f'{work:.1f} s'}"
        + (' (partial)' if work is not None and not sample['check_work_complete'] else ''),
        '- Queue, setup, tail and total: inspect completed Actions job timestamps with `pnpm project:timing --run <id>`.',
    ]
    for check in slowest_checks(sample):
        lines.append(f"- `{check['id'] or 'unknown check'}`: {check['duration_seconds']:.1f} s ({check['status']})")
    return '\n'.join(lines) + '\n'


def active_step(steps, names):
    matches = [step for step in steps if step.get('name') in names and step.get('conclusion') != 'skipped'
               and step.get('status') not in {'queued', 'pending', 'waiting'} and timestamp(step.get('started_at'))]
    if len(matches) != 1:
        return None, None
    return timestamp(matches[0].get('started_at')), timestamp(matches[0].get('completed_at'))


def job_identity(run_id, attempt, head_sha, repository, job):
    missing, conflicts = [], []
    repository_field = job.get('repository')
    if isinstance(repository_field, dict):
        repository_field = repository_field.get('full_name')
    run_url = job.get('run_url')
    if run_url:
        url = re.fullmatch(r'https://api\.github\.com/repos/([\w.-]+/[\w.-]+)/actions/runs/(\d+)', run_url)
        if not url or url[2] != run_id or (repository_field and repository_field != url[1]):
            conflicts.append('run_url')
        elif url:
            repository_field = url[1]
    for field, observed, expected in [
        ('run_id', job.get('run_id'), run_id), ('run_attempt', job.get('run_attempt'), attempt),
        ('head_sha', job.get('head_sha'), head_sha), ('repository', repository_field, repository),
    ]:
        if observed is None or expected is None:
            missing.append(field)
        elif str(observed) != str(expected):
            conflicts.append(field)
    if not positive_id(job.get('id')):
        missing.append('job_id')
    return missing, conflicts


def hosted_timing(run, jobs):
    """Derive independent clock intervals from one exact Actions attempt."""
    if run.get('path', WORKFLOW) != WORKFLOW or run.get('name', 'Verify delivery') != 'Verify delivery':
        raise TimingError('Expected the ScopeHub verification workflow')
    repository = run.get('repository', {}).get('full_name')
    if not repository:
        match = re.fullmatch(r'https://github\.com/([\w.-]+/[\w.-]+)/actions/runs/\d+', run.get('html_url', ''))
        repository = match[1] if match else None
    attempt = str(run['run_attempt']) if run.get('run_attempt') is not None else None
    run_id = str(run['id'])
    requested = timestamp(run.get('created_at')) if attempt == '1' else None
    rows = []
    for job in jobs:
        match = re.fullmatch(r'verify(?: task)? \((ubuntu-latest|windows-latest)\)', job.get('name', ''))
        if not match:
            continue
        label = match[1]
        missing, conflicts = job_identity(run_id, attempt, run.get('head_sha'), repository, job)
        if job.get('labels') and label not in job['labels']:
            conflicts.append('runner_label')
        start, end = timestamp(job.get('started_at')), timestamp(job.get('completed_at'))
        harness_start, harness_end = active_step(job.get('steps', []), HARNESS_STEPS)
        browser_start, browser_end = active_step(job.get('steps', []), BROWSER_STEPS)
        if not (start and harness_start and start <= harness_start and (end is None or harness_start <= end)):
            harness_start = None
        if not (harness_start and harness_end and harness_start <= harness_end and (end is None or harness_end <= end)):
            harness_end = None
        rows.append({
            'job_id': str(job['id']) if job.get('id') is not None else None,
            'platform': PLATFORMS[label], 'runner_label': label,
            'status': job.get('status'), 'conclusion': job.get('conclusion'),
            'identity_issues': {'missing': missing, 'conflicts': conflicts},
            'seconds': {
                'queue': elapsed(requested, start), 'setup': elapsed(start, harness_start),
                'harness_step': elapsed(harness_start, harness_end), 'tail': elapsed(harness_end, end),
                'runner': elapsed(start, end), 'total': elapsed(requested, end),
                'browser_step': elapsed(browser_start, browser_end),
            },
        })
    ids = [row['job_id'] for row in rows if row['job_id'] is not None]
    if len(set(ids)) != len(ids):
        for row in rows:
            if ids.count(row['job_id']) > 1:
                row['identity_issues']['conflicts'].append('duplicate_job_id')
    bound_matrix = (len(rows) == len(PLATFORMS) and {row['platform'] for row in rows} == set(PLATFORMS.values())
                    and all(not row['identity_issues']['conflicts'] for row in rows))
    complete = (bound_matrix and run.get('status') == 'completed'
                and all(row['status'] == 'completed' for row in rows))
    totals = [row['seconds']['total'] for row in rows]
    runners = [row['seconds']['runner'] for row in rows]
    return {
        'repository': repository, 'run_id': run_id, 'run_attempt': attempt,
        'head_sha': run.get('head_sha'), 'event': run.get('event'),
        'status': run.get('status'), 'conclusion': run.get('conclusion'), 'jobs': rows,
        'workflow': {
            'matrix_complete': complete,
            'identity_complete': (bound_matrix and positive_id(run_id) and positive_id(attempt)
                                  and commit_id(run.get('head_sha'))
                                  and all(not row['identity_issues']['missing'] for row in rows)),
            'total_elapsed_seconds': max(totals) if complete and all(value is not None for value in totals) else None,
            'runner_work_seconds': sum(runners) if complete and all(value is not None for value in runners) else None,
        },
    }


def ci_key(ci):
    return tuple(ci.get(key) for key in ('repository', 'run_id', 'run_attempt', 'event', 'head_sha'))


def merge_samples(samples, hosted):
    clock = {ci_key(run): run for run in hosted}
    observations = {}
    for sample in samples:
        ci = sample['ci']
        key = (*ci_key(ci), sample['platform']) if ci else ('local', sample['run_id'], sample['platform'])
        previous = observations.get(key)
        if previous and previous != sample:
            # The compact artifact can add a cache observation to an otherwise identical report.
            if dict(previous, cache='unknown') != dict(sample, cache='unknown') or (
                previous['cache'] != 'unknown' and sample['cache'] != 'unknown'
            ):
                raise TimingError(f'Conflicting timing observations for {sample["run_id"]}')
            if sample['cache'] == 'unknown':
                continue
        observations[key] = sample
    for sample in observations.values():
        ci = sample['ci']
        run = clock.get(ci_key(ci)) if ci else None
        jobs = [job for job in run['jobs'] if job['platform'] == sample['platform']] if run else []
        job = jobs[0] if len(jobs) == 1 and not jobs[0]['identity_issues']['conflicts'] else None
        seconds = {**sample['seconds'], **(job['seconds'] if job else {})}
        for key in ('queue', 'setup', 'harness_step', 'tail', 'runner', 'total', 'browser_step'):
            seconds.setdefault(key, None)
        yield {**sample, 'seconds': seconds,
               'job': {key: value for key, value in job.items() if key != 'seconds'} if job else None,
               'workflow_success': (run['conclusion'] == 'success' and run['workflow']['matrix_complete']) if run else None,
               'hosted_identity_complete': run['workflow'].get('identity_complete', False) if run else None,
               'slowest_checks': slowest_checks(sample)}


def coverage_key(sample):
    rows = {row['id']: row for row in sample['checks']}
    return tuple((key, rows[key]['status'], rows[key]['reason_code']) for key in sample['selected'])


def observation_id(row):
    return row['ci']['run_id'] if row['ci'] else row['run_id']


def metric_review(rows, metric, objective, *, compare_growth=True):
    values = [(row['seconds'][metric], observation_id(row)) for row in rows if row['seconds'][metric] is not None
              and (metric != 'check_work' or row.get('check_work_complete'))]
    recent = values[-WINDOW:]
    baseline = values[-2 * WINDOW:-WINDOW] if compare_growth else []
    current = median(value for value, _ in recent) if recent else None
    prior = median(value for value, _ in baseline) if len(baseline) == WINDOW else None
    growth = round(current - prior, 3) if prior is not None else None
    material = growth is not None and growth >= MIN_GROWTH_SECONDS and growth >= prior * MIN_GROWTH_RATIO
    breaches = sum(value > objective for value, _ in recent) if objective is not None else 0
    if breaches >= 2 or material:
        status = 'investigate'
        action = 'Retain these runs, identify the changed stage/check or runner overhead, and open or update corrective work.'
    elif breaches == 1:
        status = 'watch'
        action = 'Inspect the outlier and collect another comparable observation.'
    elif len(values) < (2 * WINDOW if compare_growth else WINDOW):
        status = 'insufficient_samples'
        action = 'Retain more independent comparable observations, or record why coverage or timing is missing.'
    else:
        status = 'within_objective' if objective is not None else 'no_material_growth'
        action = 'Retain representative observations for the next weekly review.'
    return {'metric': metric, 'status': status, 'objective_seconds': objective, 'sample_count': len(values),
            'recent_runs': [run for _, run in recent], 'baseline_runs': [run for _, run in baseline],
            'recent_median_seconds': current, 'baseline_median_seconds': prior, 'growth_seconds': growth,
            'recent_objective_breaches': breaches, 'material_growth': material, 'action': action,
            'missing_measurements': len(rows) - len(values)}


def objective_review(rows, metric, objective):
    result = metric_review(rows, metric, objective, compare_growth=False)
    measured = [row for row in rows if row['seconds'][metric] is not None]
    changes = []
    for before, after in zip(measured, measured[1:]):
        old = {key: (status, reason) for key, status, reason in coverage_key(before)}
        new = {key: (status, reason) for key, status, reason in coverage_key(after)}
        if old != new:
            changes.append({
                'before_run': observation_id(before), 'after_run': observation_id(after),
                'added': sorted(new.keys() - old.keys()), 'removed': sorted(old.keys() - new.keys()),
                'outcome_changes': sorted(key for key in old.keys() & new.keys() if old[key] != new[key]),
                'elapsed_change_seconds': round(after['seconds'][metric] - before['seconds'][metric], 3),
            })
    result['coverage_changes'] = changes
    result['scope_change_note'] = ('Elapsed changes span different check coverage; the wall objective still applies.'
                                   if changes else None)
    if changes:
        first, latest = measured[0]['seconds'][metric], measured[-1]['seconds'][metric]
        result['first_to_latest_elapsed_change_seconds'] = round(latest - first, 3)
        recent = measured[-WINDOW:]
        earlier = measured[-MAX_RECENT:-WINDOW]
        baselines = [earlier[index:index + WINDOW] for index in range(len(earlier) - WINDOW + 1)
                     if coverage_key(earlier[index + WINDOW - 1]) != coverage_key(recent[-1])]
        if baselines:
            before = min(median(row['seconds'][metric] for row in window) for window in baselines)
            growth = round(median(row['seconds'][metric] for row in recent) - before, 3)
            result.update(scope_growth_baseline_median_seconds=before, scope_growth_median_change_seconds=growth)
            if result['status'] == 'within_objective' and growth >= max(MIN_GROWTH_SECONDS, MIN_GROWTH_RATIO * before):
                result.update(status='review_scope_growth',
                              action='Review added or removed checks and decide whether the elapsed increase fits the accepted risk.')
    return result


def timing_view(samples, *, hosted=()):
    hosted = list(hosted)
    merged = sorted(merge_samples(samples, hosted), key=lambda sample: timestamp(sample['recorded_at'])
                    or datetime.min.replace(tzinfo=timezone.utc))
    cohorts = defaultdict(list)
    objective_scopes = defaultdict(list)
    for sample in merged:
        ci, job = sample['ci'], sample['job']
        if not sample['coverage_complete'] or not sample.get('identity_complete') or not sample['recorded_at'] or not sample['run_id']:
            reason = 'Harness report is missing, failed, or has incomplete coverage or identity.'
        elif ci and not sample['hosted_identity_complete']:
            reason = 'Exact Actions attempt and job identities are unavailable or conflicting.'
        elif ci and (not sample['workflow_success'] or not job or job['conclusion'] != 'success'):
            reason = 'A matching successful job and complete successful Actions workflow are required.'
        elif ci and (ci['event'] not in NORMAL_EVENTS or ci['run_attempt'] != '1'
                     or sample['profile'] not in AUTOMATIC_PROFILES):
            reason = 'Only first-attempt automatic PR and main profiles enter the hosted review.'
        elif not ci and sample['profile'] != 'fast':
            reason = 'The local timing objective applies to the fast profile.'
        elif all(sample['seconds'][key] is None for key in ('harness', 'total', 'runner')):
            reason = 'Elapsed time is unavailable.'
        else:
            reason = None
        sample['review_exclusion'] = reason
        if reason is None:
            scope = (ci['repository'], ci['event'], job['runner_label']) if ci else (None, 'local', None)
            key = (*scope, sample['profile'], sample['platform'], sample['arch'])
            objective_scopes[key].append(sample)
            cohorts[(*key, sample['cache'], coverage_key(sample))].append(sample)
    reviews = []
    for (repository, event, runner, profile, platform, arch, cache, coverage), rows in cohorts.items():
        metrics = {'harness': LOCAL_BUDGET} if event == 'local' else {
            'total': HOSTED_BUDGET, 'queue': None, 'setup': None, 'harness_step': None,
            'harness': None, 'tail': None, 'runner': None, 'browser_step': None, 'check_work': None,
        }
        reviews.append({'repository': repository, 'event': event, 'runner_label': runner,
                        'profile': profile, 'platform': platform, 'arch': arch, 'cache': cache, 'coverage': coverage,
                        'metrics': [metric_review(rows, metric, objective) for metric, objective in metrics.items()
                                    if objective is not None or any(row['seconds'][metric] is not None for row in rows)]})
    objectives = []
    for (repository, event, runner, profile, platform, arch), rows in objective_scopes.items():
        metric, budget = ('harness', LOCAL_BUDGET) if event == 'local' else ('total', HOSTED_BUDGET)
        objectives.append({'repository': repository, 'event': event, 'runner_label': runner, 'profile': profile,
                           'platform': platform, 'arch': arch, **objective_review(rows, metric, budget)})
    return {'samples': merged, 'hosted': hosted, 'reviews': reviews, 'objective_reviews': objectives,
            'next_action': ('Review objective breaches and material growth, then collect missing comparable observations.'
                            if reviews else 'Retain a measured fast run or a hosted change report with matching Actions jobs.')}


def local_paths(root):
    return sorted({
        *root.glob('.reports/runs/*/test-report.json'), *root.glob('.reports/ci/*/*/test-report.json'),
        *root.glob('.reports/timing/*.json'), *(path for path in [root / '.reports/test-report.json'] if path.is_file()),
    })


def download_timing(repository, run_id, attempt):
    endpoint = f'repos/{repository}/actions/runs/{run_id}/artifacts?per_page=100'
    artifacts = json.loads(command(['gh', 'api', endpoint]))['artifacts']
    expected = {f'verification-timing-{label}' for label in PLATFORMS}
    names = sorted({item['name'] for item in artifacts if item['name'] in expected and not item['expired']}
                   - {item['name'] for item in artifacts if item['size_in_bytes'] > COMPACT_LIMIT})
    samples = []
    if names:
        with TemporaryDirectory(prefix='scopehub-timing-') as temp:
            args = [arg for name in names for arg in ('--name', name)]
            command(['gh', 'run', 'download', run_id, '--repo', repository, '--dir', temp, *args])
            for path in Path(temp).glob(f'**/{TIMING_FILE}'):
                if path.stat().st_size > COMPACT_LIMIT:
                    raise TimingError('Timing artifact exceeds the compact report limit')
                sample = read_json(path)
                ci = sample.get('ci') or {}
                if sample.get('kind') == SAMPLE_KIND and ci_key(ci)[:3] == (repository, run_id, attempt):
                    samples.append(sample)
    return samples


def inspect_jobs(run, repository):
    endpoint = f'repos/{repository}/actions/runs/{run["id"]}/attempts/{run["run_attempt"]}/jobs?per_page=100'
    jobs = json.loads(command(['gh', 'api', endpoint]))['jobs']
    return hosted_timing(run, jobs)


def inspect_hosted(run_id, attempt, repository, samples):
    endpoint = f'repos/{repository}/actions/runs/{run_id}'
    if attempt:
        endpoint += f'/attempts/{attempt}'
    run = json.loads(command(['gh', 'api', endpoint]))
    if str(run['id']) != str(run_id) or run.get('path') != WORKFLOW:
        raise TimingError('Selected run is outside the repository verification workflow')
    samples.extend(download_timing(repository, str(run_id), str(run['run_attempt'])))
    return inspect_jobs(run, repository)


def recent_runs(repository, limit):
    if not 1 <= limit <= MAX_RECENT:
        raise TimingError(f'Recent limit must be between 1 and {MAX_RECENT} per event.')
    endpoint = f'repos/{repository}/actions/workflows/verify.yml/runs'
    selected = []
    for event in ('pull_request', 'push'):
        fields = ['-f', 'branch=main'] if event == 'push' else []
        response = json.loads(command(['gh', 'api', '--method', 'GET', endpoint,
                                       '-f', f'event={event}', '-f', 'status=completed',
                                       '-f', f'per_page={limit}', *fields]))
        selected.extend(run for run in response['workflow_runs'][:limit]
                        if run.get('path') == WORKFLOW and run.get('event') == event
                        and run.get('status') == 'completed'
                        and (event != 'push' or run.get('head_branch') == 'main'))
    return selected


def inspect_recent(repository, limit, samples, workflows, issues):
    runs = recent_runs(repository, limit)
    starting_issues = len(issues)
    with_compact, without_compact = [], []
    for run in runs:
        run_id, attempt = str(run['id']), str(run['run_attempt'])
        try:
            compact = download_timing(repository, run_id, attempt)
            (with_compact if compact else without_compact).append(run_id)
            has_report = any((sample.get('ci') or {}).get('run_id') == run_id for sample in samples)
            samples.extend(compact)
            if compact or has_report:
                workflows.append(inspect_jobs(run, repository))
        except (OSError, subprocess.SubprocessError, KeyError, TypeError, ValueError) as error:
            issues.append({'run_id': run_id, 'error': str(error)})
    return {'collected_at': datetime.now(timezone.utc).isoformat(),
            'limit_per_event': limit, 'completed_runs_considered': len(runs),
            'runs_with_compact': len(with_compact), 'runs_without_compact': len(without_compact),
            'missing_compact_run_ids': without_compact,
            'unsuccessful_runs': sum(run.get('conclusion') != 'success' for run in runs),
            'cold_bootstrap': not with_compact and len(issues) == starting_issues}


def weekly_review():
    policy = read_json(ROOT / 'project/delivery.json')['scaffold_maintenance']['periodic_reviews']
    for item in policy:
        if item['id'] == REVIEW_ID:
            due = date.fromisoformat(item['reviewed_on']) + timedelta(days=item['interval_days'])
            return {**item, 'next_review_by': due.isoformat(), 'review_due': datetime.now(timezone.utc).date() >= due}
    return None


def review_summary(view):
    lines = ['### Verification timing review', '']
    if review := view.get('weekly_review'):
        lines.append(f"- Repository review due {review['next_review_by']}.")
    recent = view.get('recent_collection')
    if recent:
        lines.append(f"- Examined {recent['completed_runs_considered']} completed runs; "
                     f"{recent['runs_with_compact']} with compact records, "
                     f"{recent['runs_without_compact']} without them. "
                     f"{recent['unsuccessful_runs']} had unsuccessful workflow outcomes.")
        if recent['cold_bootstrap']:
            lines.append('- Compact history is unavailable. Allow normal PR and main verification to accumulate samples.')
    objectives = view['objective_reviews']
    if not objectives:
        lines.append('- No qualified elapsed samples. Allow comparable coverage to accumulate and inspect unavailable data.')
    for row in objectives:
        median_seconds = row['recent_median_seconds']
        value = f'{median_seconds:.0f}s' if median_seconds is not None else 'unavailable'
        lines.append(f"- {row['event']} / {row['profile']} / {row['platform']} — {row['status'].replace('_', ' ')}, "
                     f"recent median {value}, objective {row['objective_seconds']}s, "
                     f"{row['recent_objective_breaches']} breaches. {row['action']}")
        if row['scope_change_note']:
            delta = row.get('scope_growth_median_change_seconds')
            if delta is None:
                delta = row['first_to_latest_elapsed_change_seconds']
            lines.append(f'  - Observed elapsed change across coverage: {delta:+.0f}s. Added and removed checks are in the archive.')
    findings = [(row, metric) for row in view['reviews'] for metric in row['metrics']
                if metric['material_growth'] and metric['metric'] != 'total']
    sparse = sum(metric['status'] == 'insufficient_samples' for row in view['reviews'] for metric in row['metrics']
                 if metric['objective_seconds'] is not None)
    if sparse:
        lines.append(f'- {sparse} timing groups need more observations with the same check coverage.')
    for row, metric in findings[:8]:
        lines.append(f"- {row['event']} / {row['platform']} / {metric['metric']}: "
                     f"investigate {metric['growth_seconds']:+.0f}s median growth.")
    if findings[8:]:
        lines.append(f'- {len(findings) - 8} more stage findings are in the archive.')
    if view['input_issues']:
        lines.append(f"- {len(view['input_issues'])} collection or input issues. Inspect the saved timing review.")
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--report', action='append', type=Path, help='Harness report, timing sample, or saved archive; repeatable')
    parser.add_argument('--actions', nargs=2, action='append', type=Path, metavar=('RUN_JSON', 'JOBS_JSON'),
                        help='Join retained Actions run and attempt-specific jobs JSON offline; repeatable')
    parser.add_argument('--run', action='append', type=int, help='Explicitly inspect a GitHub Actions run using official gh; repeatable')
    parser.add_argument('--attempt', type=int, help='Inspect a specific attempt of a single --run')
    parser.add_argument('--recent', nargs='?', type=int, const=DEFAULT_RECENT,
                        help='Inspect up to N recent normal completed runs per event using official gh')
    parser.add_argument('--github-actions', action='store_true', help='Use the read-only job token for --recent in Actions')
    parser.add_argument('--summary', type=Path, help='Append a Markdown review to a job summary')
    parser.add_argument('--save', type=Path, help='Write a compact archive to a new file for offline review')
    args = parser.parse_args()
    if args.attempt is not None and (args.attempt < 1 or len(args.run or []) != 1):
        parser.error('--attempt requires a positive attempt and exactly one --run')
    if any(run < 1 for run in args.run or []):
        parser.error('--run requires a positive run ID')

    if args.recent is not None and not 1 <= args.recent <= MAX_RECENT:
        parser.error(f'--recent must be between 1 and {MAX_RECENT}')
    if args.github_actions and (args.recent is None or args.run):
        parser.error('--github-actions requires --recent and cannot accompany --run')
    samples, workflows, issues = [], [], []
    recent = None
    try:
        for path in args.report if args.report is not None else local_paths(ROOT):
            try:
                data = read_json(path)
                if data.get('kind') == ARCHIVE_KIND:
                    samples.extend(data['samples'])
                    workflows.extend(data['hosted'])
                    recent = data.get('recent_collection') or recent
                    issues.extend(data.get('input_issues', []))
                else:
                    samples.append(data if data.get('kind') == SAMPLE_KIND else report_sample(data))
            except (OSError, KeyError, AttributeError, TypeError, ValueError) as error:
                issues.append({'path': str(path), 'error': str(error)})
        for run_path, jobs_path in args.actions or []:
            run, jobs = read_json(run_path), read_json(jobs_path)
            workflows.append(hosted_timing(run, jobs if isinstance(jobs, list) else jobs['jobs']))
        if args.run or args.recent is not None:
            repository = hosted_repository(args.github_actions)
        if args.run:
            for run in args.run:
                workflows.append(inspect_hosted(run, args.attempt, repository, samples))
        if args.recent is not None:
            recent = inspect_recent(repository, args.recent, samples, workflows, issues)
        workflows = list({ci_key(run): run for run in workflows}.values())
        view = timing_view(samples, hosted=workflows)
        view.update(weekly_review=weekly_review(), input_issues=issues, recent_collection=recent)
        if args.save:
            args.save.parent.mkdir(parents=True, exist_ok=True)
            with args.save.open('xb') as output:
                output.write(json_bytes({'kind': ARCHIVE_KIND, 'samples': samples, 'hosted': workflows,
                                         'recent_collection': recent, 'input_issues': issues}))
        if args.summary:
            with args.summary.open('a', encoding='utf-8') as output:
                output.write(review_summary(view))
        print(json.dumps(view, indent=2))
    except (OSError, subprocess.SubprocessError, KeyError, TypeError, ValueError) as error:
        parser.exit(1, f'Timing inspection failed: {error}\n')


if __name__ == '__main__':
    main()
