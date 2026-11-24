"""Review retained verification timings or explicitly inspect a GitHub Actions run."""

import argparse
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from functools import cache
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from statistics import median
import subprocess
from tempfile import TemporaryDirectory

if __package__:
    from .doctor import tool_version
    from .verification_scope import SCOPED_PROFILES, ScopeError, exact_commit, git, require_complete_history
    from .worktree import WorktreeError, exclusive_git_lock, git_path
else:
    from doctor import tool_version
    from verification_scope import SCOPED_PROFILES, ScopeError, exact_commit, git, require_complete_history
    from worktree import WorktreeError, exclusive_git_lock, git_path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = '.github/workflows/verify.yml'
AUTOMATIC_PROFILES = SCOPED_PROFILES | {'full'}
SAMPLE_KIND = 'scopehub-verification-timing-v1'
ARCHIVE_KIND = 'scopehub-timing-archive-v1'
TIMING_FILE = 'verification-timing.json'
REVIEW_ID = 'verification-timing'
BUDGET_SCOPE = ('repository', 'event', 'profile', 'platform')
ATTEMPT_IDENTITY = ('repository', 'run_id', 'run_attempt', 'event', 'head_sha')
CI_TIMING_NAME = re.compile(r'ci-(pull_request|push)-(\d+)-1(?:-failure-[0-9a-f]{64})?\.json\Z')
INTAKE_ATTEMPTS = 2
MISSING_COMPACT = 'Compact reports are absent for part of the required matrix.'
MISSING_CLOCK = 'Complete matching attempt job clocks are unavailable.'
TIMING_INPUT_ERRORS = (AttributeError, KeyError, TypeError, ValueError, OverflowError, RecursionError)
ATTEMPT_SECONDS = ('queue', 'setup', 'harness_step', 'tail', 'runner', 'total', 'browser_step')
SPLIT_SECONDS = ('initial_queue', 'aggregate_setup')
CACHE_STATES = {'cold', 'partial', 'warm', 'not_requested', 'not_restored'}
NORMAL_EVENTS = {'pull_request', 'push'}
PLATFORMS = {'ubuntu-latest': 'Linux', 'windows-latest': 'Windows'}
LEGACY_WINDOWS_LANES = ('core', 'browser', 'python')
WINDOWS_PRODUCERS = {lane: f'Windows change checks ({lane})' for lane in (*LEGACY_WINDOWS_LANES, 'recovery')}
WINDOWS_AGGREGATE_MARKER = 'Require successful Windows lane jobs'
WINDOWS_RECOVERY_MARKER = 'Require successful Windows lane jobs (four producers)'
SCOPED_HARNESS_STEPS = {f'Verify {profile} changes' for profile in SCOPED_PROFILES}
HARNESS_STEPS = {
    'Run pnpm check',
    'Run uv run python scripts/check.py --require-windows',
    'Run uv run python scripts/check.py --profile release --require-windows',
    'Verify the selected task',
    *SCOPED_HARNESS_STEPS,
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
GENERATED_CHECK = 'generated-contracts'
GENERATED_COMMAND = ['{python}', 'scripts/generate_contracts.py', '--check']
GENERATOR_VERSIONS = {
    'python': r'\d+\.\d+\.\d+',
    'rustfmt': r'rustfmt [0-9.]+(?:-[a-z]+)? \([0-9a-f]+ \d{4}-\d{2}-\d{2}\)',
}


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
    return value if type(value) in (int, float) and 0 <= value <= timedelta.max.total_seconds() else None


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


def merge_ref(event, ref):
    return isinstance(ref, str) and (
        (event == 'push' and ref == 'refs/heads/main')
        or (event == 'pull_request' and bool(re.fullmatch(r'refs/pull/[1-9][0-9]*/merge', ref)))
    )


def generator_version(tool, version):
    return isinstance(version, str) and bool(re.fullmatch(GENERATOR_VERSIONS[tool], version))


def generated_contracts_sample(report, root, env):
    """Observe the reviewed generator invocation without retaining machine paths."""
    ci = report.get('ci') or {}
    checkout = ci.get('checkout_sha')
    ref = env.get('GITHUB_REF')
    comparison_base = env.get('SCOPEHUB_SCOPE_BASE')
    if (not commit_id(checkout) or checkout != env.get('GITHUB_SHA') or not merge_ref(ci.get('event'), ref)
            or env.get('SCOPEHUB_SCOPE_OUTCOME') != 'success' or not commit_id(comparison_base)
            or (ci['event'] == 'pull_request' and env.get('GITHUB_BASE_REF') != 'main')):
        return None
    selected = report.get('selection', {}).get('checks', [])
    rows = [row for row in report.get('checks', []) if row.get('id') == GENERATED_CHECK]
    environment = report.get('environment', {})
    python = environment.get('python')
    executable = environment.get('python_executable')
    if (GENERATED_CHECK not in selected or len(rows) != 1 or rows[0].get('status') != 'passed'
            or not executable or rows[0].get('command') != [executable, *GENERATED_COMMAND[1:]]
            or rows[0].get('exit_code') != 0 or not generator_version('python', python)):
        return None
    try:
        # Resolve the actual checkout object. No fetching, replacements or grafts.
        oid, tree, *parents = git(root, 'show', '-s', '--format=%H %T %P', 'HEAD', '--').decode('ascii').split()
    except (ScopeError, OSError, subprocess.SubprocessError, ValueError):
        return None
    if (oid != checkout or not all(map(commit_id, [tree, *parents])) or len(parents) != 2
            or parents[0] != comparison_base):
        return None
    rustfmt = tool_version('rustfmt', root)
    if not generator_version('rustfmt', rustfmt):
        return None
    return {'checkout': checkout, 'ref': ref, 'tree': tree, 'parents': parents, 'comparison_base': comparison_base,
            'command': GENERATED_COMMAND, 'tools': {'python': python, 'rustfmt': rustfmt}}


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


def observe_job(job, *, identity, requested, label=None, harness_steps=HARNESS_STEPS):
    missing, conflicts = job_identity(*identity, job)
    if label and job.get('labels') and label not in job['labels']:
        conflicts.append('runner_label')
    start, end = timestamp(job.get('started_at')), timestamp(job.get('completed_at'))
    harness_start, harness_end = active_step(job.get('steps', []), harness_steps)
    browser_start, browser_end = active_step(job.get('steps', []), BROWSER_STEPS)
    if not (start and harness_start and start <= harness_start and (end is None or harness_start <= end)):
        harness_start = None
    if not (harness_start and harness_end and harness_start <= harness_end and (end is None or harness_end <= end)):
        harness_end = None
    if not (start and browser_start and browser_end and start <= browser_start <= browser_end
            and (end is None or browser_end <= end)):
        browser_start = browser_end = None
    row = {
        'job_id': str(job['id']) if job.get('id') is not None else None,
        'platform': PLATFORMS.get(label), 'runner_label': label,
        'status': job.get('status'), 'conclusion': job.get('conclusion'),
        'identity_issues': {'missing': missing, 'conflicts': conflicts},
        'seconds': {
            'queue': elapsed(requested, start), 'setup': elapsed(start, harness_start),
            'harness_step': elapsed(harness_start, harness_end), 'tail': elapsed(harness_end, end),
            'runner': elapsed(start, end), 'total': elapsed(requested, end),
            'browser_step': elapsed(browser_start, browser_end),
        },
    }
    return row, (start, end, harness_start, harness_end)


def windows_aggregate_cache(caches):
    if (not isinstance(caches, dict) or caches.keys() != WINDOWS_PRODUCERS.keys()
            or not all(isinstance(state, str) and state in CACHE_STATES | {'unknown'} for state in caches.values())):
        raise ValueError('Expected one cache state per Windows producer.')
    required = {state for lane, state in caches.items() if lane != 'python'}
    if caches['python'] == 'not_requested' and len(required) == 1 and 'not_requested' not in required:
        return next(iter(required))
    return 'unknown'


def split_windows_seconds(aggregate, producers, requested, expected):
    windows, (start, end, aggregate_harness_start, aggregate_harness_end), raw = aggregate
    seconds = windows['seconds']
    seconds.update(initial_queue=None, aggregate_setup=seconds['setup'], queue=None,
                   setup=None, harness_step=None, runner=None, browser_step=None)
    if producers.keys() != set(expected):
        return
    scoped = any(step.get('name') in SCOPED_HARNESS_STEPS and step.get('conclusion') != 'skipped'
                 and step.get('status') not in {'queued', 'pending', 'waiting'} for step in raw.get('steps', []))
    starts = [clocks[0] for _, clocks in producers.values()]
    ends = [clocks[1] for _, clocks in producers.values()]
    if all(starts):
        seconds['initial_queue'] = elapsed(requested, min(starts))
    if all(ends):
        seconds['queue'] = elapsed(max(ends), start)
    active = [producers['core']] if scoped else list(producers.values())
    harness_starts = [clocks[2] for _, clocks in active]
    if all(harness_starts):
        first_harness = min(harness_starts)
        seconds['setup'] = elapsed(min(starts), first_harness) if all(starts) else None
        # The protected step must begin after the lane steps, including scoped setup siblings.
        if all(ends) and start and max(ends) <= start and aggregate_harness_start:
            seconds['harness_step'] = elapsed(first_harness, aggregate_harness_end)
    runners = [elapsed(start, end), *(row['seconds']['runner'] for row, _ in producers.values())]
    if all(value is not None for value in runners):
        seconds['runner'] = sum(runners)
    seconds['browser_step'] = producers['browser'][0]['seconds']['browser_step']


def has_windows_producers(siblings, expected):
    labels = [WINDOWS_PRODUCERS[lane] for lane in expected]
    named = [job for job in siblings if job.get('name') in WINDOWS_PRODUCERS.values()]
    return (sorted(job['name'] for job in named) == sorted(labels)
            and all(job['platform'] == 'Windows' and job['runner_label'] == 'windows-latest' for job in named))


def hosted_timing(run, jobs):
    """Derive clock intervals from an exact Actions attempt and its actual job topology."""
    if run.get('path', WORKFLOW) != WORKFLOW or run.get('name', 'Verify delivery') != 'Verify delivery':
        raise TimingError('Expected the ScopeHub verification workflow')
    repository = run.get('repository', {}).get('full_name')
    if not repository:
        match = re.fullmatch(r'https://github\.com/([\w.-]+/[\w.-]+)/actions/runs/\d+', run.get('html_url', ''))
        repository = match[1] if match else None
    attempt = str(run['run_attempt']) if run.get('run_attempt') is not None else None
    run_id = str(run['id'])
    requested = timestamp(run.get('created_at')) if attempt == '1' else None
    identity = (run_id, attempt, run.get('head_sha'), repository)
    jobs = list(jobs)
    aggregate_markers = {step.get('name') for job in jobs if job.get('name') == 'verify (windows-latest)'
                         for step in job.get('steps', [])}
    recovery_layout = (WINDOWS_RECOVERY_MARKER in aggregate_markers
                       or any(job.get('name') == WINDOWS_PRODUCERS['recovery'] for job in jobs))
    expected = WINDOWS_PRODUCERS if recovery_layout else LEGACY_WINDOWS_LANES
    split = run.get('event') in NORMAL_EVENTS and (
        recovery_layout or WINDOWS_AGGREGATE_MARKER in aggregate_markers
        or any(job.get('name') in WINDOWS_PRODUCERS.values() for job in jobs)
    )
    rows, siblings, producers = [], [], {}
    aggregate = None
    for job in jobs:
        name = job.get('name', '')
        protected = re.fullmatch(r'verify(?: task)? \((ubuntu-latest|windows-latest)\)', name)
        if protected:
            row, clocks = observe_job(job, identity=identity, requested=requested, label=protected[1])
            rows.append(row)
            if split and protected[1] == 'windows-latest':
                aggregate = row, clocks, job
            continue
        if not split:
            continue
        lane = next((lane for lane, expected in WINDOWS_PRODUCERS.items() if name == expected), None)
        if lane:
            harness_steps = {f'Verify the Windows {lane} lane'}
            if lane == 'core':
                harness_steps.update(SCOPED_HARNESS_STEPS)
            row, clocks = observe_job(job, identity=identity, requested=requested, label='windows-latest',
                                      harness_steps=harness_steps)
            producers[lane] = row, clocks
        elif job.get('conclusion') == 'skipped':
            continue
        else:
            row, _ = observe_job(job, identity=identity, requested=requested, harness_steps=())
        siblings.append({**row, 'name': name})

    actual = [*rows, *siblings]
    runners = [row['seconds']['runner'] for row in actual]
    if split and aggregate:
        split_windows_seconds(aggregate, producers, requested, expected)
    ids = [row['job_id'] for row in actual if row['job_id'] is not None]
    if len(set(ids)) != len(ids):
        for row in actual:
            if ids.count(row['job_id']) > 1:
                row['identity_issues']['conflicts'].append('duplicate_job_id')
    bound_matrix = (len(rows) == len(PLATFORMS) and {row['platform'] for row in rows} == set(PLATFORMS.values())
                    and (not split or has_windows_producers(siblings, expected))
                    and all(not row['identity_issues']['conflicts'] for row in actual))
    complete = (bound_matrix and run.get('status') == 'completed'
                and all(row['status'] == 'completed' for row in actual))
    identity_complete = (bound_matrix and positive_id(run_id) and positive_id(attempt)
                         and commit_id(run.get('head_sha')) and all(not row['identity_issues']['missing'] for row in actual))
    totals = [row['seconds']['total'] for row in rows]
    complete_clocks = complete and identity_complete
    result = {
        'repository': repository, 'run_id': run_id, 'run_attempt': attempt,
        'head_sha': run.get('head_sha'), 'event': run.get('event'),
        'head_branch': short_label(run.get('head_branch')),
        'status': run.get('status'), 'conclusion': run.get('conclusion'), 'jobs': rows,
        'workflow': {
            'matrix_complete': complete,
            'identity_complete': identity_complete,
            'total_elapsed_seconds': max(totals) if complete_clocks and all(value is not None for value in totals) else None,
            'runner_work_seconds': sum(runners) if complete_clocks and all(value is not None for value in runners) else None,
        },
    }
    if split:
        result['sibling_jobs'] = siblings
        result['workflow']['windows_split'] = True
        if recovery_layout:
            result['workflow']['windows_recovery_producer'] = True
    return result


def ci_key(ci):
    return tuple(ci.get(key) for key in ATTEMPT_IDENTITY)


def actions_attempt_identity(repository, run):
    return repository, str(run['id']), str(run['run_attempt']), run['event'], run['head_sha']


def authenticated_failure(run, identity):
    if run['status'] == 'completed' and run['conclusion'] not in {None, 'success'}:
        # Workflow authority can establish failure without any matching job clock.
        return {**dict(zip(ATTEMPT_IDENTITY, identity)), 'status': 'completed',
                'conclusion': run['conclusion'], 'authenticated_failure': True,
                'head_branch': short_label(run.get('head_branch')), 'jobs': [],
                'workflow': {'matrix_complete': False, 'identity_complete': False}}


def sample_core(sample):
    return {key: value for key, value in sample.items() if key not in {'cache', 'generated_contracts', 'windows_caches'}}


def valid_seconds(seconds, required):
    return isinstance(seconds, dict) and all(
        key in seconds and (seconds[key] is None or duration(seconds[key]) is not None) for key in required
    )


def labels(record, keys):
    return isinstance(record, dict) and all(
        key in record and (record[key] is None or isinstance(record[key], str)) for key in keys
    )


def valid_sample(sample):
    if not labels(sample, ('run_id', 'platform', 'profile', 'arch', 'source_commit', 'recorded_at', 'cache')):
        return False
    checks, selected, ci = sample['checks'], sample['selected'], sample['ci']
    if (sample.get('kind') != SAMPLE_KIND or type(sample['coverage_complete']) is not bool
            or (sample.get('identity_complete') is not None and type(sample['identity_complete']) is not bool)
            or (sample.get('identity_complete') and not commit_id(sample['source_commit']))
            or not valid_seconds(sample['seconds'], ('harness', 'check_work'))
            or not isinstance(selected, list) or not all(isinstance(key, str) for key in selected)
            or not isinstance(checks, list)
            or not all(labels(check, ('id', 'status', 'reason_code'))
                       and (check['exit_code'] is None or type(check['exit_code']) is int)
                       and valid_seconds(check, ('duration_seconds',)) for check in checks)
            or (ci is not None and not labels(ci, ('repository', 'run_id', 'run_attempt', 'event',
                                                  'head_sha', 'checkout_sha')))):
        return False
    if 'windows_caches' in sample and (
            sample['platform'] != 'Windows' or sample['profile'] != 'full'
            or windows_aggregate_cache(sample['windows_caches']) != sample['cache']):
        return False
    return not sample['coverage_complete'] or (
        set(selected) <= {check['id'] for check in checks}
        and all(check['status'] != 'passed' or check['exit_code'] == 0 for check in checks)
    )


def valid_clock(run):
    if not labels(run, ('repository', 'run_id', 'run_attempt', 'event', 'head_sha', 'conclusion')):
        return False
    if run.get('head_branch') is not None and short_label(run['head_branch']) is None:
        return False
    workflow, jobs = run['workflow'], run['jobs']
    siblings = run.get('sibling_jobs', [])
    if 'authenticated_failure' in run and (
            run['authenticated_failure'] is not True or run.get('status') != 'completed'
            or run['conclusion'] in {None, 'success'} or jobs or siblings
            or run['event'] not in NORMAL_EVENTS or run['run_attempt'] != '1'
            or not positive_id(run['run_id']) or not commit_id(run['head_sha'])):
        return False
    return (isinstance(workflow, dict) and type(workflow['matrix_complete']) is bool
            and (workflow.get('identity_complete') is None or type(workflow['identity_complete']) is bool)
            and (workflow.get('windows_split') is None or type(workflow['windows_split']) is bool)
            and ('windows_recovery_producer' not in workflow
                 or (workflow.get('windows_split') is True and workflow['windows_recovery_producer'] is True))
            and isinstance(jobs, list) and isinstance(siblings, list) and all(
                labels(job, ('job_id', 'platform', 'runner_label', 'status', 'conclusion'))
                and all(isinstance(job['identity_issues'][key], list) for key in ('missing', 'conflicts'))
                and valid_seconds(job['seconds'], ATTEMPT_SECONDS) for job in [*jobs, *siblings]
            ))


def admitted_records(records, validate, label, issues):
    if not isinstance(records, list):
        issues.append({'error': f'Expected a list of {label}.'})
        return []
    valid = []
    for index, record in enumerate(records):
        try:
            if validate(record):
                valid.append(record)
                continue
        except TIMING_INPUT_ERRORS:
            pass
        issues.append({'error': f'Invalid {label} record at position {index + 1}.'})
    return valid


def clock_capture_complete(run):
    workflow = run['workflow']
    siblings = run.get('sibling_jobs', [])
    split = workflow.get('windows_split') or 'sibling_jobs' in run
    expected = WINDOWS_PRODUCERS if workflow.get('windows_recovery_producer') else LEGACY_WINDOWS_LANES
    actual = [*run['jobs'], *siblings]
    ids = {job['job_id'] for job in actual if positive_id(job['job_id'])}
    return (run.get('status') == 'completed' and bool(run['conclusion']) and workflow['matrix_complete'] is True
            and workflow.get('identity_complete') is True and len(run['jobs']) == len(PLATFORMS)
            and {job['platform'] for job in run['jobs']} == set(PLATFORMS.values())
            and len(ids) == len(actual)
            and all(duration(workflow.get(key)) is not None for key in ('total_elapsed_seconds', 'runner_work_seconds'))
            and all(job['status'] == 'completed' and job['conclusion'] and not job['identity_issues']['missing']
                    and not job['identity_issues']['conflicts']
                    and job['seconds']['total'] is not None and job['seconds']['runner'] is not None for job in actual)
            and (not split or (
                has_windows_producers(siblings, expected) and all(job['conclusion'] != 'success'
                                                       or job['seconds']['harness_step'] is not None for job in run['jobs'])
            )))


def clock_complete(run):
    return (clock_capture_complete(run) and run['conclusion'] == 'success'
            and all(job['conclusion'] == 'success' for job in [*run['jobs'], *run.get('sibling_jobs', [])]))


def sample_identity_complete(sample):
    return (sample.get('identity_complete') is True
            and short_label(sample['run_id']) is not None and timestamp(sample['recorded_at']) is not None)


def sample_eligible(sample):
    return sample['coverage_complete'] and sample_identity_complete(sample)


def attempt_clocks(hosted, issues):
    failures = defaultdict(dict)
    for run in hosted:
        if run.get('authenticated_failure'):
            failures[ci_key(run)][run['conclusion']] = run

    clocks, conflicting, rejected = {}, set(), set()
    for run in hosted:
        key = ci_key(run)
        if key in conflicting or run.get('authenticated_failure'):
            continue
        if key in failures and set(failures[key]) != {run['conclusion']}:
            if key not in rejected:
                issues.append({'run_id': run['run_id'],
                               'authenticated_conclusions': sorted(failures[key]),
                               'error': 'Attempt clock conflicts with the authenticated workflow conclusion.'})
                rejected.add(key)
            continue
        previous = clocks.get(key)
        if previous is None or not clock_capture_complete(previous):
            clocks[key] = run
        elif clock_capture_complete(run):
            branches = {row.get('head_branch') for row in (previous, run)} - {None}
            if clock_signature(previous) != clock_signature(run) or len(branches) > 1:
                conflicting.add(key)
                del clocks[key]
                issues.append({'run_id': run['run_id'], 'error': 'Conflicting complete attempt clocks.'})
            elif run.get('head_branch') and not previous.get('head_branch'):
                clocks[key] = run
    for key, outcomes in failures.items():
        if len(outcomes) == 1:
            clocks.setdefault(key, next(iter(outcomes.values())))
        elif len(outcomes) > 1:
            issues.append({'run_id': key[1], 'error': 'Conflicting authenticated workflow conclusions.'})
    return clocks


def clock_signature(run):
    jobs = sorted([*run['jobs'], *run.get('sibling_jobs', [])],
                  key=lambda job: tuple(job[key] or '' for key in ('job_id', 'platform', 'runner_label')))
    return (
        run['conclusion'],
        *(duration(run['workflow'].get(key)) for key in ('total_elapsed_seconds', 'runner_work_seconds')),
        tuple((job['job_id'], job['platform'], job['runner_label'], job['conclusion'],
               tuple(duration(job['seconds'].get(key)) for key in (*ATTEMPT_SECONDS, *SPLIT_SECONDS))) for job in jobs),
    )


def coalesce_samples(samples, issues):
    observations = {}
    for sample in samples:
        ci = sample['ci']
        key = (*ci_key(ci), sample['platform']) if ci else ('local', sample['run_id'], sample['platform'])
        group = observations.get(key)
        if group is None:
            observations[key] = [sample]
            continue
        if len(group) > 1:
            continue
        previous = group[0]
        eligible, previous_eligible = sample_eligible(sample), sample_eligible(previous)
        if not eligible or not previous_eligible:
            if eligible:
                group[0] = sample
            continue
        # Compact artifacts can add observations absent from older samples and full reports.
        try:
            previous_proof, proof = (row.get('generated_contracts') for row in (previous, sample))
            previously_observed, observed = (row.get('windows_caches') for row in (previous, sample))
            caches = {previous['cache'], sample['cache']} - {'unknown'}
            if (sample_core(previous) != sample_core(sample) or len(caches) > 1
                    or (previous_proof is not None and proof is not None and previous_proof != proof)
                    or (previously_observed is not None and observed is not None and previously_observed != observed)):
                group.append(sample)
                issues.append({'run_id': observation_id(sample), 'platform': sample['platform'],
                               'error': 'Conflicting complete timing samples for this run and platform.'})
                continue
            sample = {**previous, **sample, 'cache': next(iter(caches), 'unknown')}
            if 'windows_caches' in sample and windows_aggregate_cache(sample['windows_caches']) != sample['cache']:
                group.append(sample)
                issues.append({'run_id': observation_id(sample), 'platform': sample['platform'],
                               'error': 'Conflicting Windows producer and aggregate cache observations.'})
                continue
            if previous_proof is not None and proof is None:
                sample['generated_contracts'] = previous_proof
        except TIMING_INPUT_ERRORS:
            issues.append({'run_id': observation_id(sample), 'platform': sample['platform'],
                           'error': 'Could not compare timing samples for this run and platform.'})
            group.append(sample)
            continue
        group[0] = sample
    return list(observations.values())


def merge_samples(samples, hosted, issues):
    clocks = attempt_clocks(hosted, issues)
    for group in coalesce_samples(samples, issues):
        if len(group) > 1:
            continue
        sample = group[0]
        ci = sample['ci']
        run = clocks.get(ci_key(ci)) if ci else None
        jobs = [job for job in run['jobs'] if job['platform'] == sample['platform']] if run else []
        job = jobs[0] if len(jobs) == 1 and not jobs[0]['identity_issues']['conflicts'] else None
        seconds = {key: sample['seconds'][key] for key in ('harness', 'check_work')}
        seconds.update({key: job['seconds'][key] if job else None for key in ATTEMPT_SECONDS})
        seconds.update({key: duration(job['seconds'].get(key)) if job else None for key in SPLIT_SECONDS})
        yield {**sample, 'seconds': seconds,
               'job': {key: value for key, value in job.items() if key != 'seconds'} if job else None,
               'workflow_success': run['conclusion'] == 'success' if run else None,
               'workflow_conclusion': run['conclusion'] if run else None,
               'hosted_head_branch': run.get('head_branch') if run else None,
               'hosted_identity_complete': clock_capture_complete(run) if run else None,
               'hosted_success_complete': clock_complete(run) if run else None,
               'slowest_checks': slowest_checks(sample)}


def coverage_key(sample):
    rows = {row['id']: row for row in sample['checks']}
    return tuple((key, rows[key]['status'], rows[key]['reason_code']) for key in sample['selected'])


def observation_id(row):
    return row['ci']['run_id'] if row['ci'] else row['run_id']


def generated_contracts_proof(sample, *, eligible_only=True):
    ci = sample['ci']
    proof = sample.get('generated_contracts')
    if not isinstance(proof, dict):
        return None
    parents = proof.get('parents')
    tools = proof.get('tools')
    if ((eligible_only and sample['review_exclusion'] is not None) or sample.get('working_tree_dirty') is not False
            or not sample['arch'] or not sample.get('inputs_sha256')
            or proof.get('checkout') != ci['checkout_sha'] or not commit_id(proof.get('tree'))
            or not isinstance(parents, list) or len(parents) != 2 or not all(map(commit_id, parents))
            or proof.get('comparison_base') != parents[0]
            or not merge_ref(ci['event'], proof.get('ref'))
            or proof.get('command') != GENERATED_COMMAND or not isinstance(tools, dict)
            or not all(generator_version(tool, tools.get(tool)) for tool in GENERATOR_VERSIONS)
            or GENERATED_CHECK not in sample['selected']):
        return None
    if ci['head_sha'] != (ci['checkout_sha'] if ci['event'] == 'push' else parents[1]):
        return None
    return proof


def pr_main_comparisons(samples):
    """Compare only the measured generator check on the same validated merge inputs."""
    pull_requests = defaultdict(list)
    for sample in samples:
        ci = sample['ci']
        if ci and ci['event'] == 'pull_request':
            pull_requests[ci['repository'], sample['platform'], ci['head_sha']].append(sample)
    for main in samples:
        ci = main['ci']
        if not ci or ci['event'] != 'push':
            continue
        proof = generated_contracts_proof(main)
        row = {'repository': ci['repository'], 'main_run_id': ci['run_id'], 'platform': main['platform'],
               'check': GENERATED_CHECK, 'status': 'unknown'}
        candidates = pull_requests.get((ci['repository'], main['platform'], proof['parents'][1])) if proof else None
        if not candidates:
            yield {**row, 'reason': 'No proven main merge and matching PR sample on this platform.'}
        for pr in candidates or ():
            pair = {**row, 'pr_run_id': observation_id(pr)}
            pr_proof = generated_contracts_proof(pr)
            if (not pr_proof or pr['profile'] != main['profile'] or coverage_key(pr) != coverage_key(main)
                    or pr['arch'] != main['arch'] or pr['inputs_sha256'] != main['inputs_sha256']
                    or any(pr_proof[key] != proof[key] for key in ('tree', 'parents', 'tools'))):
                yield {**pair, 'reason': 'Merge, execution inputs or toolchain do not have matching proof.'}
                continue
            timings = [next((duration(check['duration_seconds']) for check in sample['checks']
                             if check['id'] == GENERATED_CHECK and check['status'] == 'passed'
                             and check['exit_code'] == 0), None) for sample in (pr, main)]
            if None in timings:
                yield {**pair, 'reason': 'Passing check durations are unavailable.'}
                continue
            pr_seconds, main_seconds = timings
            yield {**pair, 'status': 'comparable', 'pr_seconds': pr_seconds, 'main_seconds': main_seconds,
                   'main_minus_pr_seconds': round(main_seconds - pr_seconds, 3)}


def metric_review(rows, metric, objective, *, compare_growth=True):
    measured = [row for row in rows if row['seconds'][metric] is not None
              and (metric != 'check_work' or row.get('check_work_complete'))]
    values = [(row['seconds'][metric], observation_id(row)) for row in measured]
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
            'baseline_reports': [harness_identity(row) for row in measured[-2 * WINDOW:-WINDOW]] if len(baseline) == WINDOW else [],
            'recent_reports': [harness_identity(row) for row in measured[-WINDOW:]],
            'breach_reports': [harness_identity(row) for row in measured[-WINDOW:]
                               if objective is not None and row['seconds'][metric] > objective],
            'recent_median_seconds': current, 'baseline_median_seconds': prior, 'growth_seconds': growth,
            'recent_objective_breaches': breaches, 'material_growth': material, 'action': action,
            'missing_measurements': len(rows) - len(values)}


def harness_identity(row):
    identity = {'run_id': row['run_id'], 'source_commit': row['source_commit']}
    if row['ci']:
        identity['hosted_run_id'] = row['ci']['run_id']
    return identity


def objective_review(rows, metric, objective):
    result = metric_review(rows, metric, objective, compare_growth=False)
    if result['recent_objective_breaches'] >= 2:
        result['action'] = 'Retain the observed wall times and use comparable cohorts to decide recurrence.'
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


def timing_view(samples, *, hosted=(), issues=None):
    if issues is None:
        issues = []
    samples = admitted_records(list(samples), valid_sample, 'timing sample', issues)
    hosted = admitted_records(list(hosted), valid_clock, 'attempt clock', issues)
    merged = sorted(merge_samples(samples, hosted, issues), key=lambda sample: timestamp(sample['recorded_at'])
                    or datetime.min.replace(tzinfo=timezone.utc))
    cohorts = defaultdict(list)
    objective_scopes = defaultdict(list)
    for sample in merged:
        ci, job = sample['ci'], sample['job']
        if not sample_eligible(sample):
            reason = 'Harness report is missing, failed, or has incomplete coverage or identity.'
        elif ci and not sample['hosted_identity_complete']:
            reason = 'Exact Actions attempt and job identities are unavailable or conflicting.'
        elif ci and (not sample['hosted_success_complete'] or not job or job['conclusion'] != 'success'):
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
            **dict.fromkeys(SPLIT_SECONDS),
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
            'pr_main_comparisons': list(pr_main_comparisons(merged)),
            'next_action': ('Review objective breaches and material growth, then collect missing comparable observations.'
                            if reviews else 'Retain a measured fast run or a hosted change report with matching Actions jobs.')}


def local_paths(root):
    try:
        shared = shared_timing_directory(root)
    except (OSError, WorktreeError, RuntimeError, *TIMING_INPUT_ERRORS):
        shared = None
    return sorted({
        *root.glob('.reports/runs/*/test-report.json'), *root.glob('.reports/ci/*/*/test-report.json'),
        *root.glob('.reports/timing/*.json'), *(path for path in [root / '.reports/test-report.json'] if path.is_file()),
        *(incremental_timing_paths([shared]) if shared else ()),
    })


def report_key(report):
    return report['run_id'], report['source_commit']


def budget_cohort(row, coverage):
    runner = row['job']['runner_label'] if 'job' in row else row['runner_label']
    return runner, row['arch'], row['cache'], tuple(coverage)


def has_hosted_inputs(sample):
    ci = sample['ci']
    digest = sample.get('inputs_sha256')
    return (ci and ci['event'] in NORMAL_EVENTS and ci['run_attempt'] == '1'
            and positive_id(ci['run_id']) and commit_id(ci['head_sha'])
            and commit_id(ci['checkout_sha']) and ci['checkout_sha'] == sample['source_commit']
            and sample.get('identity_complete') is True and timestamp(sample['recorded_at'])
            and sample.get('working_tree_dirty') is False and sample['arch'] and sample['cache'] in CACHE_STATES
            and isinstance(digest, str) and re.fullmatch(r'[0-9a-f]{64}', digest)
            and sample['job'] and sample['job']['runner_label'] and sample['job']['status'] == 'completed'
            and not sample['job']['identity_issues']['missing'])


def repair_in_hosted_inputs(sample, repair, ancestor, checkout_parents):
    ci = sample['ci']
    head, checkout = ci['head_sha'], ci['checkout_sha']
    proof = generated_contracts_proof(sample, eligible_only=False)
    if ci['event'] == 'push':
        if head != checkout or sample['hosted_head_branch'] not in {None, 'main'}:
            return False
        if not sample['hosted_head_branch'] and not proof:
            return None
        return ancestor(repair, checkout)

    # Ordinary PR verification checks out the merge preview. The compact proof retains its actual parent order.
    retained = tuple(proof['parents']) if proof else None
    local = checkout_parents(checkout)
    if local is not None and retained is not None and local != retained:
        return False
    parents = local if local is not None else retained
    if parents is None:
        return None
    if len(parents) != 2 or parents[1] != head or head == checkout:
        return False
    included = [ancestor(repair, parent) for parent in parents]
    return True if True in included else None if None in included else False


def recovery_observation(sample):
    return {**harness_identity(sample), 'hosted_head': sample['ci']['head_sha'],
            'recorded_at': sample['recorded_at'],
            'inputs_sha256': sample['inputs_sha256'], 'job_id': sample['job']['job_id'],
            'conclusion': sample['workflow_conclusion'], 'elapsed_seconds': sample['seconds']['total']}


def matches_budget_scope(sample, scope):
    return all((sample['ci'] if key in ('repository', 'event') else sample)[key] == scope[key] for key in BUDGET_SCOPE)


def linked_timing_window(history, reports, growth_baseline):
    # Use the same attributable window and precedence as the ordinary timing review.
    for end in range(len(history), 1, -1):
        window = history[max(0, end - 2 * WINDOW):end]
        review = metric_review(window, 'total', HOSTED_BUDGET)
        if growth_baseline:
            if (review['material_growth'] and {report_key(row) for row in review['baseline_reports']} == growth_baseline
                    and all(report_key(row) in reports for row in review['recent_reports'])):
                return window[:WINDOW], window[WINDOW:]
        elif review['recent_objective_breaches'] >= 2:
            if all(report_key(row) in reports for row in review['breach_reports']):
                return [row for row in history if report_key(row) in reports
                        and row['seconds']['total'] > HOSTED_BUDGET], []
    return [], []


def measure_budget_recovery(scope, repair, reports, growth_baseline, samples, ancestor, checkout_parents):
    rows = [sample for sample in samples if has_hosted_inputs(sample) and matches_budget_scope(sample, scope)]
    required_reports = reports | growth_baseline
    linked = [row for row in rows if report_key(row) in required_reports]
    unavailable = sorted(required_reports - {report_key(row) for row in linked})
    # All linked observations in this scope bound the correction, even when they belong to another cohort.
    last_time = max((timestamp(row['recorded_at']) for row in linked), default=None)
    last_run = max((int(row['ci']['run_id']) for row in linked), default=0)
    cohorts = defaultdict(list)
    for row in rows:
        if row['review_exclusion'] is None and row['seconds']['total'] is not None:
            cohorts[budget_cohort(row, coverage_key(row))].append(row)

    comparisons, cutoffs = [], {}
    for cohort, history in cohorts.items():
        baseline, degraded = linked_timing_window(history, reports, growth_baseline)
        if not baseline:
            continue
        selected = tuple(key for key, *_ in cohort[3])
        candidates, successful, recovered_window, history_missing = [], [], [], False
        for row in rows if commit_id(repair) else ():
            if (timestamp(row['recorded_at']) <= last_time or int(row['ci']['run_id']) <= last_run
                    or budget_cohort(row, row['selected']) != (*cohort[:3], selected)):
                continue
            elapsed = row['seconds']['total']
            failed = row['workflow_conclusion'] not in {None, 'success'}
            if not failed and (row['review_exclusion'] is not None or elapsed is None or coverage_key(row) != cohort[3]):
                continue
            includes = repair_in_hosted_inputs(row, repair, ancestor, checkout_parents)
            history_missing |= includes is None
            if not includes:
                continue
            candidates.append(row)
            if failed or unavailable or recovered_window:
                continue
            successful.append(row)
            if not degraded and elapsed <= HOSTED_BUDGET:
                recovered_window = [row]
            elif degraded and len(successful) >= WINDOW:
                recent = successful[-WINDOW:]
                restored = metric_review([*baseline, *recent], 'total', HOSTED_BUDGET)
                if not restored['material_growth'] and restored['recent_objective_breaches'] < 2:
                    recovered_window = recent
            if recovered_window:
                cutoffs[cohort] = (int(row['ci']['run_id']), timestamp(row['recorded_at']))
        baseline_median = median(row['seconds']['total'] for row in baseline)
        latest = recovery_observation(candidates[-1]) if candidates else None
        if latest and latest['conclusion'] == 'success':
            latest['elapsed_change_seconds'] = round(latest['elapsed_seconds'] - baseline_median, 3)
        comparisons.append({
            'runner_label': cohort[0], 'arch': cohort[1], 'cache': cohort[2], 'coverage': cohort[3],
            'controls': [recovery_observation(row) for row in baseline],
            'degraded_window': [recovery_observation(row) for row in degraded],
            'control_median_seconds': baseline_median, 'latest_attempt': latest,
            'recovered': recovery_observation(recovered_window[-1]) if recovered_window else None,
            'recovery_window': [recovery_observation(row) for row in recovered_window],
            'history_missing': history_missing,
        })
    recovered = bool(comparisons) and len(comparisons) == len(cutoffs)
    event = 'main push' if scope['event'] == 'push' else 'pull request'
    action = f"Observe a comparable successful ordinary {scope['profile']} {scope['platform']} {event} within {HOSTED_BUDGET} seconds."
    if not commit_id(repair):
        action = 'Record the corrective commit. ' + action
    elif unavailable:
        action = 'Restore comparable retained timing for all linked observations. ' + action
    elif not comparisons:
        action = 'Restore the diagnosed timing controls or link the original sustained-growth baseline reports. ' + action
    elif any(row['history_missing'] for row in comparisons):
        action = 'Make the corrective and hosted input history available locally. ' + action
    elif any(row['degraded_window'] and not row['recovered'] for row in comparisons):
        action = (f"Observe {WINDOW} comparable successful ordinary {scope['profile']} {scope['platform']} {event} "
                  'runs that restore the earlier median within the sustained-growth tolerances.')
    return {'status': 'recovered' if recovered else 'pending', 'repair_commit': repair,
            'unavailable_reports': [{'run_id': run, 'source_commit': source} for run, source in unavailable],
            'comparisons': comparisons, 'next_action': None if recovered else action}, cutoffs


def budget_follow_up(view, feedback, tasks, *, root=ROOT):
    @cache
    def ancestor(base, head):
        try:
            require_complete_history(root)
            exact_commit(root, base)
            exact_commit(root, head)
            return base.encode('ascii') in git(root, 'merge-base', '--all', base, head).split()
        except (OSError, ScopeError, subprocess.SubprocessError):
            return None

    @cache
    def checkout_parents(checkout):
        try:
            require_complete_history(root)
            exact_commit(root, checkout)
            return tuple(git(root, 'show', '-s', '--format=%P', checkout, '--').decode('ascii').split())
        except (OSError, ScopeError, subprocess.SubprocessError):
            return None

    tracked, recovered_cohorts = {}, {}
    for item in feedback:
        if not (spec := item.get('verification_budget')):
            continue
        scope = {key: spec[key] for key in BUDGET_SCOPE}
        scope_key = tuple(scope.values())
        reports = {report_key(report) for report in item.get('diagnostic_reports', ())}
        corrective = [tasks[key] for key in item['task_ids'] if key in tasks
                      and tasks[key]['status'] not in {'done', 'cancelled'}]
        owned = item['status'] == 'open' and any(
            task['status'] == 'active' or task['status'] == 'planned' and
            all(tasks[key]['status'] == 'done' for key in task.get('depends_on', ()))
            for task in corrective
        )
        recovery = None
        if scope['event'] in NORMAL_EVENTS:
            growth_baseline = {report_key(row) for row in spec.get('growth_baseline', ())}
            recovery, cutoffs = measure_budget_recovery(scope, spec.get('repair_commit'), reports, growth_baseline,
                                                        view.get('samples', ()), ancestor, checkout_parents)
            for cohort, cutoff in cutoffs.items():
                key = scope_key, cohort
                recovered_cohorts[key] = max(recovered_cohorts.get(key, cutoff), cutoff)
        status = 'assigned' if owned else 'assignment_needed'
        if recovery and recovery['status'] == 'recovered':
            status = 'recovered'
        elif item['status'] == 'addressed':
            status = 'recovery_pending' if recovery else 'addressed'
        tracked[item['id']] = (
            scope, reports, owned,
            {'feedback_id': item['id'], 'feedback_status': item['status'], 'status': status,
             'recovery': recovery,
             'scope': scope,
             'corrective_tasks': [
                 {key: task.get(key) for key in ('id', 'status', 'next_action', 'blocker')} for task in corrective
             ]},
        )
    def route(row, observed_reports):
        scope = {key: row[key] for key in BUDGET_SCOPE}
        reports = {report_key(report): report for report in observed_reports}
        related = [(keys, {**entry, 'status': 'assigned'} if owned else entry)
                   for candidate, keys, owned, entry in tracked.values() if candidate == scope
                   and (owned or entry['status'] == 'assignment_needed' or keys & reports.keys())]
        recorded = {key for keys, _ in related for key in keys}
        missing = [report for key, report in reports.items() if key not in recorded]
        links = [entry for _, entry in related]
        statuses = {entry['status'] for entry in links}
        status = 'assignment_needed'
        if 'assigned' in statuses:
            status = 'assigned'
        elif not missing and statuses in ({'addressed'}, {'recovery_pending'}, {'recovered'}):
            status, = statuses
        return {
            **{key: row[key] for key in ('repository', 'event', 'runner_label', 'profile', 'platform', 'arch',
                                        'metric', 'objective_seconds', 'recent_runs', 'recent_objective_breaches')},
            'status': status,
            'feedback': links,
            'unrecorded_reports': missing,
            'next_action': ('Use the original verification reports with pnpm project:diagnostic identify '
                            'to associate the unrecorded reports with the existing corrective feedback.'
                            if status == 'assigned' and missing else None),
        }

    comparable = []
    for group in view['reviews']:
        for metric in group['metrics']:
            if metric['objective_seconds'] is None:
                continue
            scope = {key: group[key] for key in BUDGET_SCOPE}
            cohort = budget_cohort(group, group['coverage'])
            cutoff = recovered_cohorts.get((tuple(scope.values()), cohort))
            if cutoff:
                last_run, last_time = cutoff
                after = [row for row in view['samples'] if row['review_exclusion'] is None and row['ci']
                         and positive_id(row['ci']['run_id']) and int(row['ci']['run_id']) > last_run
                         and timestamp(row['recorded_at']) > last_time
                         and matches_budget_scope(row, scope) and budget_cohort(row, coverage_key(row)) == cohort]
                metric = metric_review(after, metric['metric'], metric['objective_seconds'])
            comparable.append({**group, **metric})
    breaches = [{**route(row, row['breach_reports']), 'cache': row['cache'], 'coverage': row['coverage']}
                for row in comparable if row['recent_objective_breaches'] >= 2]
    growth = [
        {**route(row, row['recent_reports']), 'cache': row['cache'], 'coverage': row['coverage'],
         'growth_seconds': row['growth_seconds'], 'baseline_runs': row['baseline_runs'],
         'baseline_reports': row['baseline_reports']}
        for row in comparable if row['material_growth']
    ]
    opened = [entry for _, _, _, entry in tracked.values() if entry['feedback_status'] != 'addressed']
    recovery = [entry for _, _, _, entry in tracked.values() if entry['recovery']]
    needs_task = any(row['status'] == 'assignment_needed' for row in [*breaches, *growth, *opened])
    routing = ('Link the observed reports to verification-budget feedback and assign an open corrective task.'
               if needs_task else next((row['next_action'] for row in [*breaches, *growth] if row['next_action']), None))
    pending = next((row['recovery']['next_action'] for row in recovery if row['recovery']['next_action']), None)
    return {
        'repeated_breaches': breaches,
        'sustained_growth': growth,
        'open_feedback': opened,
        'budget_recovery': recovery,
        'next_action': ' '.join(action for action in (routing, pending) if action) or None,
    }


def shared_timing_directory(root):
    marker = root / '.git'
    if not marker.exists():
        return None
    if marker.is_dir() and not marker.is_symlink() and not (marker / 'commondir').exists():
        common = marker.resolve()
    else:
        common = git_path(root, '--git-common-dir')
    directory = common / 'scopehub-verification-timing'
    if directory.resolve() != directory:
        raise TimingError('Expected a plain timing directory in the shared Git metadata.')
    return directory


def incremental_timing_paths(directories):
    incremental = defaultdict(lambda: defaultdict(list))
    for directory in directories:
        for path in directory.glob('ci-*.json'):
            if match := CI_TIMING_NAME.fullmatch(path.name):
                incremental[match[1]][int(match[2])].append(path)
    return [path for entries in incremental.values() for run in sorted(entries)[-MAX_RECENT:]
            for path in entries[run]]


def read_bounded_timing(path):
    limit = 2 * 1024 * 1024
    with path.open('rb') as content:
        raw = content.read(limit + 1)
    if len(raw) > limit:
        raise TimingError('Report exceeds the compact timing read limit.')
    return json.loads(raw)


def archive_inputs(data, label, issues, *, collection_issues=True):
    samples = admitted_records(data.get('samples'), valid_sample, f'timing sample in {label}', issues)
    hosted = admitted_records(data.get('hosted'), valid_clock, f'attempt clock in {label}', issues)
    if collection_issues:
        notes = admitted_records(data.get('input_issues', []),
                                 lambda note: labels(note, ('error',)) and isinstance(note['error'], str),
                                 f'collection issue in {label}', issues)
        failed = {clock['run_id'] for clock in hosted if clock['conclusion'] != 'success' and clock_capture_complete(clock)
                  and failed_capture_is_complete(clock, samples, hosted)}
        issues.extend({**note, 'file': label} for note in notes
                      if not (isinstance(note.get('run_id'), str) and note['run_id'] in failed
                              and note['error'] in {MISSING_COMPACT, MISSING_CLOCK}))
    return samples, hosted


def retained_failures_for_clocks(directories, hosted, already_read, issues):
    # An older success can survive in a retained review after its ordinary capture ages out.
    # Read failure authority for those exact attempts without opening unrelated older captures.
    attempts = {ci_key(clock) for clock in hosted if clock['event'] in NORMAL_EVENTS
                and clock['run_attempt'] == '1' and positive_id(clock['run_id']) and clock['conclusion'] == 'success'}
    paths = {path for directory in directories for _, run_id, _, event, _ in attempts
             for path in directory.glob(f'ci-{event}-{run_id}-1-failure-*.json')
             if CI_TIMING_NAME.fullmatch(path.name)}
    for path in sorted(paths - set(already_read)):
        try:
            data = read_bounded_timing(path)
            if data.get('kind') != ARCHIVE_KIND:
                raise TimingError('Expected an authenticated failure timing archive.')
            _, clocks = archive_inputs(data, path.name, issues, collection_issues=False)
            for clock in clocks:
                if clock.get('authenticated_failure') and ci_key(clock) in attempts:
                    yield clock
        except (OSError, *TIMING_INPUT_ERRORS) as error:
            issues.append({'file': path.name, 'error': str(error)})


def retained_budget_inputs(root):
    # Read a bounded window of shared automatic observations and checkout-local reviews.
    directory = root / '.reports/timing'
    archives, issues = [], []
    try:
        shared = shared_timing_directory(root)
    except (OSError, WorktreeError, RuntimeError, *TIMING_INPUT_ERRORS) as error:
        shared = None
        issues.append({'error': str(error)})
    directories = (directory, shared) if shared else (directory,)
    recent = incremental_timing_paths(directories)
    for path in directory.glob('*.json'):
        if CI_TIMING_NAME.fullmatch(path.name):
            continue
        try:
            archives.append((path.stat().st_mtime_ns, path))
        except OSError as error:
            issues.append({'file': path.relative_to(root).as_posix(), 'error': str(error)})
    archives = [path for _, path in sorted(archives)[-4:]]
    local = sorted(root.glob('.reports/runs/*/test-report.json'), reverse=True)[:MAX_RECENT]
    samples, hosted = [], []
    for path in [*recent, *archives, *local]:
        try:
            data = read_bounded_timing(path)
            if data.get('kind') == ARCHIVE_KIND:
                archive_samples, archive_hosted = archive_inputs(data, path.name, issues)
                samples.extend(archive_samples)
                hosted.extend(archive_hosted)
            elif path in local and not data.get('ci') and (data.get('selection') or {}).get('profile') == 'fast':
                samples.append(report_sample(data))
        except (OSError, *TIMING_INPUT_ERRORS) as error:
            issues.append({'file': path.name, 'error': str(error)})
    hosted.extend(retained_failures_for_clocks(directories, hosted, recent, issues))
    return samples, hosted, issues


def budget_status(samples, hosted, issues, feedback, tasks, *, root=ROOT):
    try:
        view = timing_view(samples, hosted=hosted, issues=issues)
        follow_up = budget_follow_up(view, feedback, tasks, root=root)
    except TIMING_INPUT_ERRORS as error:
        view = {'objective_reviews': [], 'reviews': []}
        follow_up = budget_follow_up(view, feedback, tasks, root=root)
        issues.append({'error': str(error)})
    return {**follow_up, 'timing_available': any(row['sample_count'] for row in view['objective_reviews']),
            'elapsed_observations': [
                {key: row[key] for key in (*BUDGET_SCOPE, 'sample_count', 'status')}
                for row in view['objective_reviews']
            ],
            'comparable_elapsed_observations': [
                {**{key: group[key] for key in (*BUDGET_SCOPE, 'cache')},
                 'sample_count': metric['sample_count'], 'status': metric['status']}
                for group in view['reviews'] for metric in group['metrics']
                if metric['objective_seconds'] is not None
            ],
            'input_issues': issues,
            'timing_command': 'pnpm project:timing'}


def offline_budget_status(root, feedback, tasks):
    return budget_status(*retained_budget_inputs(root), feedback, tasks, root=root)


def capture_observations(identity, samples, hosted, issues, *, conclusion=None):
    groups = coalesce_samples([row for row in samples if ci_key(row['ci'] or {}) == identity], issues)
    ambiguous = any(len(group) > 1 for group in groups)
    # Retain distinct observations for diagnostics and future attempts. Incompatible complete clocks
    # must remain visibly incompatible even if an exact retry agrees with one of them.
    clocks = list({(clock_capture_complete(clock), clock_signature(clock), clock.get('head_branch'),
                    clock.get('authenticated_failure')): clock
                   for clock in hosted if ci_key(clock) == identity}.values())
    clock = attempt_clocks(clocks, issues).get(identity)
    outcome = conclusion or (clock['conclusion'] if clock else None)
    diagnostic = outcome not in {None, 'success'}
    # Failed artifacts can carry complete diagnostic identity despite failed or skipped check outcomes.
    qualify = sample_identity_complete if diagnostic else sample_eligible
    present = {group[0]['platform'] for group in groups if qualify(group[0])}
    missing = set(PLATFORMS.values()) - present
    complete_clock = (clock is not None and clock['conclusion'] == outcome
                      and (clock_capture_complete(clock) if diagnostic else clock_complete(clock)))
    if complete_clock:
        clocks = [clock, *(row for row in clocks if row.get('authenticated_failure'))]
    return [row for group in groups for row in group], clocks, missing, complete_clock, ambiguous


def failed_capture_is_complete(clock, samples, hosted):
    _, _, missing, complete, ambiguous = capture_observations(
        ci_key(clock), samples, hosted, [], conclusion=clock['conclusion'],
    )
    return complete and not missing and not ambiguous


def save_timing_capture(path, attempts, samples, clocks, issues):
    archive = {'kind': ARCHIVE_KIND, 'intake_attempts': attempts, 'samples': samples,
               'hosted': clocks, 'input_issues': issues}
    with TemporaryDirectory(dir=path.parent, prefix='.timing-') as temporary:
        staged = Path(temporary) / path.name
        staged.write_bytes(json_bytes(archive))
        staged.replace(path)


def retain_authenticated_failure(root, failure):
    # This immutable attempt-and-conclusion record is independent of the job capture writer's lock.
    key = sha256(json_bytes([*ci_key(failure), failure['conclusion']])).hexdigest()
    filename = f"ci-{failure['event']}-{failure['run_id']}-1-failure-{key}.json"
    local = root / '.reports/timing'
    try:
        shared = shared_timing_directory(root)
    except (OSError, WorktreeError, RuntimeError, *TIMING_INPUT_ERRORS):
        shared = None
    directories = (shared, local) if shared else (local,)
    for directory in directories:
        try:
            if directory == local and not directory.resolve().is_relative_to(root.resolve()):
                raise TimingError('The local timing directory must be inside this checkout.')
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / filename
            _, samples, clocks, _ = read_timing_capture(path, [])
            if samples or clocks != [failure]:
                save_timing_capture(path, 0, [], [failure], [])
            return
        except (OSError, RuntimeError, *TIMING_INPUT_ERRORS) as error:
            failure_to_write = error
    raise TimingError(f'Could not retain the authenticated workflow failure: {failure_to_write}')


def read_timing_capture(path, issues):
    try:
        data = read_bounded_timing(path)
        if data.get('kind') != ARCHIVE_KIND:
            raise TimingError('Expected a compact capture archive.')
        samples, clocks = archive_inputs(data, path.name, issues, collection_issues=False)
        attempts = data.get('intake_attempts', 1)
        if type(attempts) is not int or not 0 <= attempts <= INTAKE_ATTEMPTS:
            raise TimingError('Invalid timing capture attempt count.')
        return attempts, samples, clocks, data.get('input_issues', [])
    except FileNotFoundError:
        return 0, [], [], []
    except (OSError, *TIMING_INPUT_ERRORS) as error:
        issues.append({'file': path.name, 'error': str(error)})
        return 0, [], [], []


def collect_new_ci_timing(root, repository, run, samples, hosted):
    run_id, event = str(run['id']), run['event']
    if not positive_id(run_id) or event not in NORMAL_EVENTS or str(run['run_attempt']) != '1':
        raise TimingError('Expected a completed automatic first-attempt verification run')
    observed = {**run, 'repository': {'full_name': repository}}
    identity = actions_attempt_identity(repository, observed)
    local_directory = root / '.reports/timing'
    directory = shared_timing_directory(root) or local_directory
    if directory == local_directory and not directory.resolve().is_relative_to(root.resolve()):
        raise TimingError('The timing intake directory must be inside this checkout.')
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f'ci-{event}-{run_id}-1.json'
    try:
        with exclusive_git_lock(path.with_suffix('.lock')):
            # Re-read after locking because another worktree may have captured this attempt.
            return update_timing_capture(path, repository, observed, identity, samples, hosted)
    except BlockingIOError:
        return 'in_progress', [], [], []


def update_timing_capture(path, repository, run, identity, samples, hosted):
    run_id = str(run['id'])
    retained_notes = []
    attempts, prior_samples, prior_clocks, prior_issues = read_timing_capture(path, retained_notes)
    known_clocks = [*prior_clocks, *hosted]
    if failure := authenticated_failure(run, identity):
        known_clocks.append(failure)
    combined_samples, combined_clocks, missing, complete_clock, ambiguous = capture_observations(
        identity, [*prior_samples, *samples], known_clocks, retained_notes, conclusion=run['conclusion'],
    )
    if not missing and complete_clock:
        persisted_issues = retained_notes if ambiguous else []
        if (prior_samples, prior_clocks, prior_issues) != (combined_samples, combined_clocks, persisted_issues):
            save_timing_capture(path, attempts, combined_samples, combined_clocks, persisted_issues)
        return ('ambiguous' if ambiguous else 'retained'), combined_samples, combined_clocks, retained_notes
    if attempts >= INTAKE_ATTEMPTS:
        if (prior_samples, prior_clocks) != (combined_samples, combined_clocks):
            save_timing_capture(path, attempts, combined_samples, combined_clocks, retained_notes)
        return 'retry_exhausted', combined_samples, combined_clocks, [*retained_notes, {
            'run_id': run_id, 'error': 'Incremental timing retry exhausted. Inspect this run explicitly if more timing is needed.',
        }]
    attempts += 1
    save_timing_capture(path, attempts, combined_samples, combined_clocks,
                        [{'run_id': run_id, 'error': 'Incremental timing capture is unfinished.'}])
    new_samples, new_clocks, issues = [], [], []
    if missing:
        try:
            criteria = {'platforms': missing} if missing != set(PLATFORMS.values()) else {}
            rows = download_timing(repository, run_id, '1', **criteria)
            new_samples = [
                row for row in admitted_records(rows, valid_sample, 'downloaded timing sample', issues)
                if ci_key(row['ci'] or {}) == identity and row['platform'] in missing
            ]
        except (OSError, subprocess.SubprocessError, *TIMING_INPUT_ERRORS) as error:
            issues.append({'run_id': run_id, 'error': f'Could not retrieve compact reports: {error}'})
    if not complete_clock and (set(PLATFORMS.values()) - missing or new_samples
                               or run['conclusion'] != 'success' and not any(
                                   row['conclusion'] == run['conclusion'] and not row.get('authenticated_failure')
                                   for row in combined_clocks)):
        try:
            row = inspect_jobs(run, repository)
            if valid_clock(row) and ci_key(row) == identity:
                new_clocks.append(row)
        except (OSError, subprocess.SubprocessError, *TIMING_INPUT_ERRORS) as error:
            issues.append({'run_id': run_id, 'error': f'Could not retrieve attempt jobs: {error}'})
    captured_samples, captured_clocks, missing_after, complete_clock, ambiguous = capture_observations(
        identity, [*combined_samples, *new_samples], [*combined_clocks, *new_clocks], issues, conclusion=run['conclusion'],
    )
    if missing_after:
        issues.append({'run_id': run_id, 'error': MISSING_COMPACT})
    if not complete_clock:
        issues.append({'run_id': run_id, 'error': MISSING_CLOCK})
    save_timing_capture(path, attempts, captured_samples, captured_clocks, issues)
    status = 'partial' if missing_after or not complete_clock else 'ambiguous' if ambiguous else 'captured'
    return status, captured_samples, captured_clocks, [*retained_notes, *issues]


def ci_budget_status(root, repository, run):
    samples, hosted, issues = retained_budget_inputs(root)
    intake = None
    if run:
        if failure := authenticated_failure(run, actions_attempt_identity(repository, run)):
            hosted.append(failure)
            try:
                retain_authenticated_failure(root, failure)
            except TimingError as error:
                issues.append({'run_id': failure['run_id'], 'error': str(error)})
        try:
            intake, new_samples, new_clocks, new_issues = collect_new_ci_timing(root, repository, run, samples, hosted)
            if intake in {'captured', 'retained', 'ambiguous'}:
                capture = f'ci-{run["event"]}-{run["id"]}-1.json'
                issues = [issue for issue in issues if issue.get('file') != capture]
            samples.extend(new_samples)
            hosted.extend(new_clocks)
            issues.extend(new_issues)
        except (OSError, WorktreeError, RuntimeError, *TIMING_INPUT_ERRORS) as error:
            intake = 'unavailable'
            issues.append({'run_id': str(run['id']), 'error': str(error)})
    tasks = {task['id']: task for task in read_json(root / 'project/tasks.json')['tasks']}
    feedback = read_json(root / 'project/feedback.json')['items']
    return {**budget_status(samples, hosted, issues, feedback, tasks, root=root), 'intake': intake}


def download_timing(repository, run_id, attempt, *, platforms=None):
    endpoint = f'repos/{repository}/actions/runs/{run_id}/artifacts?per_page=100'
    artifacts = json.loads(command(['gh', 'api', endpoint]))['artifacts']
    expected = {f'verification-timing-{label}' for label, platform in PLATFORMS.items()
                if platforms is None or platform in platforms}
    names = sorted({item['name'] for item in artifacts if item['name'] in expected and not item['expired']}
                   - {item['name'] for item in artifacts if item['size_in_bytes'] > COMPACT_LIMIT})
    samples = []
    if names:
        with TemporaryDirectory(prefix='scopehub-timing-') as temp:
            args = [arg for name in names for arg in ('--name', name)]
            command(['gh', 'run', 'download', run_id, '--repo', repository, '--dir', temp, *args])
            for path in Path(temp).glob(f'**/{TIMING_FILE}'):
                try:
                    if path.stat().st_size > COMPACT_LIMIT:
                        continue
                    sample = read_json(path)
                    if valid_sample(sample) and ci_key(sample['ci'] or {})[:3] == (repository, run_id, attempt):
                        samples.append(sample)
                except (OSError, *TIMING_INPUT_ERRORS):
                    continue
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
        except (OSError, subprocess.SubprocessError, *TIMING_INPUT_ERRORS) as error:
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
    if follow_up := view.get('budget_follow_up'):
        for row in [*follow_up['repeated_breaches'], *follow_up['sustained_growth']]:
            owners = ', '.join(task['id'] for finding in row['feedback'] for task in finding['corrective_tasks'])
            suffix = f' ({owners})' if owners else ''
            lines.append(f"- Budget follow-up for {row['event']} / {row['profile']} / {row['platform']} — "
                         f"{row['status'].replace('_', ' ')}{suffix}.")
        for entry in follow_up['budget_recovery']:
            if action := entry['recovery']['next_action']:
                lines.append(f"- {entry['feedback_id']}: {action}")
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
    comparisons = view.get('pr_main_comparisons', [])
    if comparisons:
        comparable = sum(row['status'] == 'comparable' for row in comparisons)
        lines.append(f'- The generated-contracts timing review found {comparable} comparable PR and main pairs '
                     f'and {len(comparisons) - comparable} observations with insufficient proof.')
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
                data = read_bounded_timing(path)
                if data.get('kind') == ARCHIVE_KIND:
                    archive_samples, archive_hosted = archive_inputs(data, path.name, issues)
                    samples.extend(archive_samples)
                    workflows.extend(archive_hosted)
                    recent = data.get('recent_collection') or recent
                else:
                    samples.append(data if data.get('kind') == SAMPLE_KIND else report_sample(data))
            except (OSError, *TIMING_INPUT_ERRORS) as error:
                issues.append({'path': str(path), 'error': str(error)})
        for run_path, jobs_path in args.actions or []:
            try:
                run, jobs = read_bounded_timing(run_path), read_bounded_timing(jobs_path)
                workflows.append(hosted_timing(run, jobs if isinstance(jobs, list) else jobs['jobs']))
            except (OSError, *TIMING_INPUT_ERRORS) as error:
                issues.append({'path': str(jobs_path), 'error': str(error)})
        if args.run or args.recent is not None:
            repository = hosted_repository(args.github_actions)
        if args.run:
            for run in args.run:
                workflows.append(inspect_hosted(run, args.attempt, repository, samples))
        if args.recent is not None:
            recent = inspect_recent(repository, args.recent, samples, workflows, issues)
        view = timing_view(samples, hosted=workflows, issues=issues)
        tasks = {task['id']: task for task in read_json(ROOT / 'project/tasks.json')['tasks']}
        feedback = read_json(ROOT / 'project/feedback.json')['items']
        view.update(weekly_review=weekly_review(), input_issues=issues, recent_collection=recent,
                    budget_follow_up=budget_follow_up(view, feedback, tasks))
        if args.save:
            args.save.parent.mkdir(parents=True, exist_ok=True)
            with args.save.open('xb') as output:
                output.write(json_bytes({'kind': ARCHIVE_KIND, 'samples': samples, 'hosted': workflows,
                                         'recent_collection': recent, 'input_issues': issues}))
        if args.summary:
            with args.summary.open('a', encoding='utf-8') as output:
                output.write(review_summary(view))
        print(json.dumps(view, indent=2))
    except (OSError, subprocess.SubprocessError, *TIMING_INPUT_ERRORS) as error:
        parser.exit(1, f'Timing inspection failed: {error}\n')


if __name__ == '__main__':
    main()
