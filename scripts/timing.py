"""Review retained verification timings or explicitly inspect a GitHub Actions run."""

import argparse
from ast import literal_eval
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from functools import cache, lru_cache
from hashlib import sha256
from itertools import chain
import json
import os
from pathlib import Path
import re
import stat
from statistics import median
import subprocess
from tempfile import TemporaryDirectory
from typing import NamedTuple

if __package__:
    from .doctor import tool_version
    from .execution_inputs import committed_source_identity, is_source_input
    from .verification_scope import (
        LEGACY_WINDOWS_LANES, SCOPED_PROFILES, WINDOWS_LANES, ScopeError, committed_windows_assignment, exact_commit,
        git, json_object, oid, require_complete_history, windows_lane_report_matches, windows_published_scope,
        windows_x64,
    )
    from .worktree import WorktreeError, entry_info, exclusive_git_lock, git_path, reparse_point
else:
    from doctor import tool_version
    from execution_inputs import committed_source_identity, is_source_input
    from verification_scope import (
        LEGACY_WINDOWS_LANES, SCOPED_PROFILES, WINDOWS_LANES, ScopeError, committed_windows_assignment, exact_commit,
        git, json_object, oid, require_complete_history, windows_lane_report_matches, windows_published_scope,
        windows_x64,
    )
    from worktree import WorktreeError, entry_info, exclusive_git_lock, git_path, reparse_point

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = '.github/workflows/verify.yml'
AUTOMATIC_PROFILES = SCOPED_PROFILES | {'full'}
SAMPLE_KIND = 'scopehub-verification-timing-v1'
ARCHIVE_KIND = 'scopehub-timing-archive-v1'
TIMING_FILE = 'verification-timing.json'
REVIEW_ID = 'verification-timing'
BUDGET_SCOPE = ('repository', 'event', 'profile', 'platform')
ATTEMPT_IDENTITY = ('repository', 'run_id', 'run_attempt', 'event', 'head_sha')
CANONICAL_CAPTURE = re.compile(r'ci-(pull_request|push)-(\d+)-1\.json\Z')
CI_TIMING_NAME = re.compile(r'ci-(pull_request|push)-(\d+)-1(?:-failure-[0-9a-f]{64})?\.json\Z')
INTAKE_ATTEMPTS = 2
MISSING_COMPACT = 'Compact reports are absent for part of the required matrix.'
MISSING_CLOCK = 'Complete matching attempt job clocks are unavailable.'
TIMING_INPUT_ERRORS = (AttributeError, KeyError, TypeError, ValueError, OverflowError, RecursionError)
ATTEMPT_SECONDS = ('queue', 'setup', 'harness_step', 'tail', 'runner', 'total', 'browser_step')
JOB_INTERVALS = {
    'queue': ('requested', 'start'), 'setup': ('start', 'harness_start'),
    'harness_step': ('harness_start', 'harness_end'), 'tail': ('harness_end', 'end'),
    'runner': ('start', 'end'), 'total': ('requested', 'end'),
    'browser_step': ('browser_start', 'browser_end'),
}
JOB_ENDPOINTS = frozenset(endpoint for pair in JOB_INTERVALS.values() for endpoint in pair)
STEP_KINDS = ('harness', 'browser')
MAX_TIMESTAMP_CHOICES = 8
SPLIT_SECONDS = ('initial_queue', 'aggregate_setup')
WORKFLOW_SECONDS = ('total_elapsed_seconds', 'runner_work_seconds')
CACHE_STATES = {'cold', 'partial', 'warm', 'not_requested', 'not_restored'}
NORMAL_EVENTS = {'pull_request', 'push'}
PLATFORMS = {'ubuntu-latest': 'Linux', 'windows-latest': 'Windows'}
WINDOWS_PRODUCERS = {lane: f'Windows change checks ({lane})' for lane in WINDOWS_LANES}
WINDOWS_PRODUCER_PROFILES = {f'ci-windows-{lane}': name for lane, name in WINDOWS_PRODUCERS.items()}
WINDOWS_AGGREGATE_MARKER = 'Require successful Windows lane jobs'
WINDOWS_RECOVERY_MARKER = 'Require successful Windows lane jobs (four producers)'
# A split stage depends on member endpoints, or comparable interval facts in
# older captures that predate endpoints. Copied metrics retain their source.
WINDOWS_COPIED_STAGES = {'browser_step': ('browser', 'browser_step')}
WINDOWS_DERIVED_STAGES = {
    'initial_queue': (('all', 'queue', ('start',)),),
    'queue': (('all', 'total', ('end',)),),
    'setup': (('all', 'queue', ('start',)), ('active', 'setup', ('start', 'harness_start'))),
    'harness_step': (('all', 'total', ('end',)), ('active', 'queue', ('start',)),
                     ('active', 'setup', ('start', 'harness_start'))),
    'runner': (('all', 'runner', ('start', 'end')),),
}
WINDOWS_PARENT_ENDPOINTS = {
    'initial_queue': {'requested'}, 'queue': {'start'}, 'setup': set(),
    'harness_step': {'start', 'harness_end'}, 'runner': {'start', 'end'},
}
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
TIMED_STEPS = HARNESS_STEPS | BROWSER_STEPS | {f'Verify the Windows {lane} lane' for lane in WINDOWS_LANES}
EXPECTED_FULL_OMISSIONS = {'not_implemented', 'unsupported_platform'}
WINDOW = 3
MIN_GROWTH_SECONDS = 60
MIN_GROWTH_RATIO = 0.15
LOCAL_BUDGET = 5 * 60
HOSTED_BUDGET = 15 * 60
MAX_RECENT = 12
DEFAULT_RECENT = 8
COMPACT_LIMIT = 128 * 1024
TIMING_READ_LIMIT = 2 * 1024 * 1024
MANUAL_ARCHIVE_LIMIT = 16 * 1024 * 1024
REVIEW_READ_LIMIT = 64 * 1024 * 1024
QUOTED_FILENAME = r"""(?:'(?:[^'\\\r\n]|\\.)*'|"(?:[^"\\\r\n]|\\.)*")"""
LEGACY_FILE_ERROR = re.compile(
    rf'(?P<message>\[(?:Errno|WinError) \d+\] [^\r\n]+?): '
    rf'(?P<source>{QUOTED_FILENAME})(?: -> (?P<target>{QUOTED_FILENAME}))?\Z'
)
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
    """Validate the full SHA-1 object names used by timing reports."""
    try:
        return len(oid(value)) == 40
    except ScopeError:
        return False


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
    profile = selection.get('profile')
    if profile is not None and short_label(profile) is None:
        raise TimingError('Invalid verification profile in harness report.')
    selected = list(filter(None, (short_label(key) for key in selection.get('checks', []))))
    if not (profile and profile.startswith('ci-windows-')):
        selected.sort()
    rows = {item['id']: item for item in checks}
    omitted = [rows[key] for key in selected if key in rows and rows[key]['status'] != 'passed']
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


def windows_producer_job_name(sample):
    return WINDOWS_PRODUCER_PROFILES.get(sample['profile'])


def job_summary(sample):
    scope = 'producer' if windows_producer_job_name(sample) else 'platform'
    coverage = f'{scope} coverage complete' if sample['coverage_complete'] else 'incomplete or unavailable coverage'
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
        f" {coverage})",
        f"- Harness elapsed: {'unavailable' if harness is None else f'{harness:.1f} s'}",
        f"- Observed check work (sum; checks may overlap): {'unavailable' if work is None else f'{work:.1f} s'}"
        + (' (partial)' if work is not None and not sample['check_work_complete'] else ''),
        '- Queue, setup, tail and total: inspect completed Actions job timestamps with `pnpm project:timing --run <id>`.',
    ]
    for check in slowest_checks(sample):
        lines.append(f"- `{check['id'] or 'unknown check'}`: {check['duration_seconds']:.1f} s ({check['status']})")
    return '\n'.join(lines) + '\n'


def eligible_step(step):
    return (step.get('conclusion') != 'skipped'
            and step.get('status') not in {'queued', 'pending', 'waiting'})


def step_identity(identities):
    number, number_disputed = known_value(row[0] for row in identities)
    name, name_disputed = known_value(row[1] for row in identities)
    return ([number, name] if name else None), number_disputed or name_disputed


def join_timestamp_choices(observations):
    # Empty means unobserved; None means a dispute with no usable witnesses.
    choices = set()
    for observed in observations:
        if observed is None:
            return None
        choices.update(observed)
        if len(choices) > MAX_TIMESTAMP_CHOICES:
            return None
    return choices


def active_step(steps, names):
    facts = []
    for step in steps:
        if step.get('name') in names and eligible_step(step):
            start, end = timestamp(step.get('started_at')), timestamp(step.get('completed_at'))
            if start is not None or end is not None:
                number = step.get('number')
                facts.append(([number if type(number) is int and number > 0 else None, step['name']], start, end))
    identity, disputed = step_identity([fact[0] for fact in facts])
    return identity, {
        field: None if disputed else join_timestamp_choices({fact[index]} for fact in facts if fact[index] is not None)
        for index, field in enumerate(('start', 'end'), start=1)
    }


def job_endpoint_choices(job):
    disputed = set(job.get('endpoint_disputes', ()))
    alternatives = job.get('endpoint_choices', {})
    choices = {}
    for field, value in job.get('endpoints', {}).items():
        if field in alternatives:
            choices[field] = {timestamp(item) for item in alternatives[field]}
        elif field in disputed:
            choices[field] = None
        else:
            point = timestamp(value)
            choices[field] = {point} if point is not None else set()
    return choices


def unique_endpoint(choices):
    return next(iter(choices)) if choices and len(choices) == 1 else None


def encode_timestamp(point):
    return point.astimezone(timezone.utc).isoformat() if point is not None else None


def retain_endpoint_choices(choices):
    result = {'endpoints': {field: encode_timestamp(unique_endpoint(values)) for field, values in choices.items()}}
    disputed = sorted(field for field, values in choices.items() if values is None or len(values) > 1)
    alternatives = {field: [encode_timestamp(value) for value in sorted(choices[field])]
                    for field in disputed if choices[field]}
    if disputed:
        result['endpoint_disputes'] = disputed
    if alternatives:
        result['endpoint_choices'] = alternatives
    return result


def can_order_endpoints(choices, fields):
    # For a nondecreasing chain the earliest feasible witness leaves every
    # later endpoint the widest possible choice. An absent field adds no bound.
    earliest = None
    for field in fields:
        values = choices.get(field, ())
        if values is None:
            return False
        if values:
            earliest = min((value for value in values if earliest is None or value >= earliest), default=None)
            if earliest is None:
                return False
    return True


def project_job_points(choices):
    points = {field: unique_endpoint(values) for field, values in choices.items()}
    unplaced = set()
    for part in STEP_KINDS:
        start, end = f'{part}_start', f'{part}_end'
        observed = (choices.get(start, ()), choices.get(end, ()))
        if any(values is None or values for values in observed) and not can_order_endpoints(
                choices, ('requested', 'start', start, end, 'end')):
            points[start] = points[end] = None
            unplaced.update((start, end))
    return points, unplaced


def job_seconds(points):
    return {field: elapsed(points.get(left), points.get(right)) for field, (left, right) in JOB_INTERVALS.items()}


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
    choices = {field: {point} if point is not None else set()
               for field, point in (('requested', requested), ('start', start), ('end', end))}
    steps = {}
    for kind, names in zip(STEP_KINDS, (harness_steps, BROWSER_STEPS), strict=True):
        step, observed = active_step(job.get('steps', ()), names)
        if step is not None:
            steps[kind] = step
        choices.update((f'{kind}_{field}', alternatives) for field, alternatives in observed.items())
    points, _ = project_job_points(choices)
    row = {
        'job_id': str(job['id']) if job.get('id') is not None else None,
        'platform': PLATFORMS.get(label), 'runner_label': label,
        'status': job.get('status'), 'conclusion': job.get('conclusion'),
        'identity_issues': {'missing': missing, 'conflicts': conflicts},
        **retain_endpoint_choices(choices),
        'seconds': job_seconds(points),
    }
    if steps:
        row['step_identities'] = steps
    if job.get('checkout_sha') is not None:
        row['checkout_sha'] = job['checkout_sha']
        if not commit_id(row['checkout_sha']):
            conflicts.append('checkout_sha')
    return row


def windows_aggregate_cache(caches):
    if (not isinstance(caches, dict) or caches.keys() != WINDOWS_PRODUCERS.keys()
            or not all(isinstance(state, str) and state in CACHE_STATES | {'unknown'} for state in caches.values())):
        raise ValueError('Expected one cache state per Windows producer.')
    required = {state for lane, state in caches.items() if lane != 'python'}
    if caches['python'] == 'not_requested' and len(required) == 1 and 'not_requested' not in required:
        return next(iter(required))
    return 'unknown'


def split_stage_seconds(parent, members, scoped):
    points, unplaced = project_job_points(job_endpoint_choices(parent))
    stages = dict.fromkeys((*WINDOWS_DERIVED_STAGES, *WINDOWS_COPIED_STAGES))
    stages['aggregate_setup'] = elapsed(points.get('start'), points.get('harness_start'))
    blocked = {stage for stage, endpoints in WINDOWS_PARENT_ENDPOINTS.items() if unplaced.intersection(endpoints)}
    if not members:
        return stages, blocked
    sources = {}
    for lane, member in members.items():
        sources[lane], unplaced = project_job_points(job_endpoint_choices(member))
        if 'harness_start' in unplaced and (not scoped or lane == 'core'):
            blocked.update(('setup', 'harness_step'))
    starts = [source.get('start') for source in sources.values()]
    ends = [source.get('end') for source in sources.values()]
    start = points.get('start')
    if all(starts):
        stages['initial_queue'] = elapsed(points.get('requested'), min(starts))
    if all(ends):
        last_finish = max(ends)
        stages['queue'] = elapsed(last_finish, start)
        if start is not None and last_finish > start:
            blocked.update(('queue', 'harness_step'))
    active = ('core',) if scoped else sources
    harness_starts = [sources[lane].get('harness_start') if sources[lane].get('start') else None for lane in active]
    if all(harness_starts):
        first_harness = min(harness_starts)
        stages['setup'] = elapsed(min(starts), first_harness) if all(starts) else None
        if all(ends) and start and 'harness_step' not in blocked:
            stages['harness_step'] = elapsed(first_harness, points.get('harness_end'))
    runners = [elapsed(source.get('start'), source.get('end')) for source in (points, *sources.values())]
    if all(value is not None for value in runners):
        stages['runner'] = sum(runners)
    for stage, (lane, metric) in WINDOWS_COPIED_STAGES.items():
        stages[stage] = members[lane]['seconds'][metric]
    return stages, blocked


def windows_parent_profile(job):
    full = 'Run uv run python scripts/check.py --require-windows'
    names = {full: 'full', **{f'Verify {profile} changes': profile for profile in SCOPED_PROFILES}}
    observed = {names[step['name']] for step in job.get('steps', ())
                if step.get('name') in names and eligible_step(step)}
    return known_value(observed)


def windows_clock_lanes(run, *, identified=False):
    if not run or not (run['workflow'].get('windows_split') or 'sibling_jobs' in run):
        return ()
    workflow = run['workflow']
    # Older compact captures had no explicit layout flag. Their complete
    # matrix already required an identified layout and all of its members.
    if identified and not workflow.get('windows_layout_identified', workflow['matrix_complete']):
        return ()
    return WINDOWS_LANES if workflow.get('windows_recovery_producer') else LEGACY_WINDOWS_LANES


def split_windows_clock(run):
    return bool(windows_clock_lanes(run))


def has_windows_producers(siblings, expected):
    labels = [WINDOWS_PRODUCERS[lane] for lane in expected]
    named = [job for job in siblings if job.get('name') in WINDOWS_PRODUCERS.values()]
    return (sorted(job['name'] for job in named) == sorted(labels)
            and all(job['platform'] == 'Windows' and job['runner_label'] == 'windows-latest'
                    and has_job_identity(job) for job in named))


def require_verification_workflow(run):
    if run.get('path', WORKFLOW) != WORKFLOW or run.get('name', 'Verify delivery') != 'Verify delivery':
        raise TimingError('Expected the ScopeHub verification workflow')


def hosted_timing(run, jobs):
    """Derive clock intervals from an exact Actions attempt and its actual job topology."""
    require_verification_workflow(run)
    repository = run.get('repository', {}).get('full_name')
    if not repository:
        match = re.fullmatch(r'https://github\.com/([\w.-]+/[\w.-]+)/actions/runs/\d+', run.get('html_url', ''))
        repository = match[1] if match else None
    attempt = str(run['run_attempt']) if run.get('run_attempt') is not None else None
    run_id = str(run['id'])
    requested = timestamp(run.get('created_at')) if attempt == '1' else None
    identity = (run_id, attempt, run.get('head_sha'), repository)
    grounded = [job for job in jobs if not any(job_identity(*identity, job))]
    aggregate_markers = {step.get('name') for job in grounded if job.get('name') == 'verify (windows-latest)'
                         for step in job.get('steps', [])}
    recovery_layout = (WINDOWS_RECOVERY_MARKER in aggregate_markers
                       or any(job.get('name') == WINDOWS_PRODUCERS['recovery'] for job in grounded))
    legacy_layout = WINDOWS_AGGREGATE_MARKER in aggregate_markers
    expected = WINDOWS_PRODUCERS if recovery_layout else LEGACY_WINDOWS_LANES
    identified_layout = legacy_layout != recovery_layout
    split = run.get('event') in NORMAL_EVENTS and (
        recovery_layout or legacy_layout
        or any(job.get('name') in WINDOWS_PRODUCERS.values() for job in grounded)
    )
    rows, siblings = [], []
    aggregate_scopes = defaultdict(list)
    for job in jobs:
        name = job.get('name', '')
        protected = re.fullmatch(r'verify(?: task)? \((ubuntu-latest|windows-latest)\)', name)
        if protected:
            row = observe_job(job, identity=identity, requested=requested, label=protected[1])
            rows.append(row)
            if split and protected[1] == 'windows-latest' and has_job_identity(row):
                aggregate_scopes[job_clock_identity(row)].append(windows_parent_profile(job))
            continue
        if not split:
            continue
        lane = next((lane for lane, expected in WINDOWS_PRODUCERS.items() if name == expected), None)
        if lane:
            harness_steps = {f'Verify the Windows {lane} lane'}
            if lane == 'core':
                harness_steps.update(SCOPED_HARNESS_STEPS)
            row = observe_job(job, identity=identity, requested=requested, label='windows-latest',
                                      harness_steps=harness_steps)
        elif job.get('conclusion') == 'skipped':
            continue
        else:
            row = observe_job(job, identity=identity, requested=requested, harness_steps=())
        siblings.append({**row, 'name': name})

    rows, siblings = coalesce_job_observations(rows), coalesce_job_observations(siblings)
    identified = [row for row in [*rows, *siblings] if has_job_identity(row)]
    roles = defaultdict(set)
    for row in identified:
        roles[row['job_id']].add(job_clock_identity(row))
    for row in identified:
        if len(roles[row['job_id']]) > 1:
            row['identity_issues']['conflicts'].append('duplicate_job_id')
    protected, sibling_members = bound_jobs(rows), bound_jobs(siblings)
    members = [*protected, *sibling_members]
    runners = [row['seconds']['runner'] for row in members]
    parents = [row for row in protected if row['platform'] == 'Windows']
    profile, scope_disputed = None, False
    if split and len(parents) == 1:
        parent, = parents
        observations = aggregate_scopes[job_clock_identity(parent)]
        profile, scope_disputed = known_value(scope for scope, _ in observations)
        scope_disputed = scope_disputed or any(disputed for _, disputed in observations)
        if scope_disputed:
            profile = None
        producers = {lane: row for lane, name in WINDOWS_PRODUCERS.items() for row in sibling_members
                     if row.get('name') == name} if (identified_layout
                                                     and has_windows_producers(sibling_members, expected)) else {}
        stages, _ = split_stage_seconds(parent, producers, profile in SCOPED_PROFILES)
        parent['seconds'].update(stages)
    bound_matrix = (len(protected) == len(PLATFORMS) and
                    {row['platform'] for row in protected} == set(PLATFORMS.values())
                    and (not split or identified_layout and has_windows_producers(sibling_members, expected)))
    complete = (bound_matrix and len(members) == len(identified) and run.get('status') == 'completed'
                and all(row['status'] == 'completed' for row in members))
    identity_complete = (bound_matrix and len(members) == len(identified) and positive_id(run_id)
                         and positive_id(attempt) and commit_id(run.get('head_sha')))
    totals = [row['seconds']['total'] for row in protected]
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
    if run.get('checkout_sha') is not None:
        result['checkout_sha'] = run['checkout_sha']
    if split:
        result['sibling_jobs'] = siblings
        result['workflow']['windows_split'] = True
        result['workflow']['windows_layout_identified'] = identified_layout
        if scope_disputed:
            result['workflow']['windows_profile_disputed'] = True
        if profile:
            result['workflow']['windows_profile'] = profile
        if recovery_layout:
            result['workflow']['windows_recovery_producer'] = True
    return result


def ci_key(ci):
    return tuple(ci.get(key) for key in ATTEMPT_IDENTITY)


def capture_attempt(ci):
    return ci.get('event'), ci.get('run_id'), ci.get('run_attempt')


def capture_filename_attempt(name):
    if isinstance(name, str) and (match := CI_TIMING_NAME.fullmatch(name)):
        return match[1], match[2], '1'
    return None


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
            or sample['cache'] not in CACHE_STATES | {'unknown'}
            or not valid_seconds(sample['seconds'], ('harness', 'check_work'))
            or not isinstance(selected, list) or not all(isinstance(key, str) for key in selected)
            or not isinstance(checks, list)
            or not all(labels(check, ('id', 'status', 'reason_code'))
                       and (check['exit_code'] is None or type(check['exit_code']) is int)
                       and valid_seconds(check, ('duration_seconds',)) for check in checks)
            or (ci is not None and not labels(ci, ('repository', 'run_id', 'run_attempt', 'event',
                                                  'head_sha', 'checkout_sha')))):
        return False
    profile = sample['profile']
    if profile is not None and short_label(profile) is None:
        return False
    if (profile and profile.startswith('ci-windows-')
            and (not windows_producer_job_name(sample) or sample['platform'] != 'Windows'
                 or ci is None or ci['event'] not in NORMAL_EVENTS)):
        return False
    if 'windows_caches' in sample and (
            sample['platform'] != 'Windows' or sample['profile'] != 'full'
            or windows_aggregate_cache(sample['windows_caches']) != sample['cache']):
        return False
    return not sample['coverage_complete'] or (
        set(selected) <= {check['id'] for check in checks}
        and all(check['status'] != 'passed' or check['exit_code'] == 0 for check in checks)
    )


def valid_timestamp_choices(values):
    if not isinstance(values, list) or not 2 <= len(values) <= MAX_TIMESTAMP_CHOICES:
        return False
    parsed = {timestamp(value) for value in values}
    return None not in parsed and len(parsed) == len(values)


def valid_step_facts(job):
    identities = job.get('step_identities', {})
    disputes = job.get('endpoint_disputes', [])
    choices = job.get('endpoint_choices', {})
    return (isinstance(identities, dict) and identities.keys() <= set(STEP_KINDS)
            and all(isinstance(key, list) and len(key) == 2
                    and (key[0] is None or type(key[0]) is int and key[0] > 0)
                    and isinstance(key[1], str) and key[1] in TIMED_STEPS
                    for key in identities.values())
            and isinstance(disputes, list) and len(disputes) <= len(JOB_ENDPOINTS)
            and all(isinstance(field, str) and field in JOB_ENDPOINTS for field in disputes)
            and isinstance(choices, dict) and choices.keys() <= set(disputes)
            and all(valid_timestamp_choices(values) and job['endpoints'][field] is None
                    for field, values in choices.items())
            and (not (identities or disputes) or 'endpoints' in job))


def valid_clock(run):
    if not labels(run, ('repository', 'run_id', 'run_attempt', 'event', 'head_sha', 'conclusion')):
        return False
    if (run.get('head_branch') is not None and short_label(run['head_branch']) is None
            or (run.get('checkout_sha') is not None and not commit_id(run['checkout_sha']))):
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
            and workflow.get('windows_profile') in AUTOMATIC_PROFILES | {None}
            and all(flag not in workflow or type(workflow[flag]) is bool
                    for flag in ('windows_layout_identified', 'windows_profile_disputed'))
            and ('windows_recovery_producer' not in workflow
                 or (workflow.get('windows_split') is True and workflow['windows_recovery_producer'] is True))
            and isinstance(jobs, list) and isinstance(siblings, list) and all(
                labels(job, ('job_id', 'platform', 'runner_label', 'status', 'conclusion'))
                and (job.get('name') is None or isinstance(job['name'], str))
                and all(isinstance(job['identity_issues'][key], list) for key in ('missing', 'conflicts'))
                and (job.get('checkout_sha') is None or commit_id(job['checkout_sha']))
                and ('endpoints' not in job or (isinstance(job['endpoints'], dict)
                     and job['endpoints'].keys() == JOB_ENDPOINTS
                     and all(value is None or timestamp(value) is not None for value in job['endpoints'].values())))
                and valid_step_facts(job)
                and valid_seconds(job['seconds'], ATTEMPT_SECONDS) for job in [*jobs, *siblings]
            ) and all(job.get('name') not in WINDOWS_PRODUCERS.values() for job in jobs))


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


def bound_jobs(jobs):
    return [job for job in jobs if has_job_identity(job)]


def clock_membership_complete(run):
    workflow = run['workflow']
    protected = bound_jobs(run['jobs'])
    siblings = bound_jobs(run.get('sibling_jobs', ()))
    lanes = windows_clock_lanes(run)
    actual = [*protected, *siblings]
    ids = {job['job_id'] for job in actual}
    return (run.get('status') == 'completed' and bool(run['conclusion']) and workflow['matrix_complete'] is True
            and workflow.get('identity_complete') is True and len(protected) == len(PLATFORMS)
            and {job['platform'] for job in protected} == set(PLATFORMS.values())
            and len(ids) == len(actual) and all(job['status'] == 'completed' and job['conclusion'] for job in actual)
            and (not lanes or has_windows_producers(siblings, lanes)))


def clock_capture_complete(run):
    # A single listing uses the same readiness rule as any retained union.
    evidence = reconcile_timing_clocks([], [run], [])
    return clock_ready_for_capture(evidence, ci_key(run), {}, [])


def successful_clock_jobs(run):
    return run['conclusion'] == 'success' and all(
        job['conclusion'] == 'success' for job in bound_jobs([*run['jobs'], *run.get('sibling_jobs', [])])
    )


def clock_complete(run):
    return clock_capture_complete(run) and successful_clock_jobs(run)


def sample_identity_complete(sample):
    ci = sample['ci']
    return (sample.get('identity_complete') is True and commit_id(sample['source_commit'])
            and short_label(sample['run_id']) is not None and timestamp(sample['recorded_at']) is not None
            and (ci is None or (
                short_label(ci['repository']) and re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', ci['repository'])
                and short_label(ci['event']) and commit_id(ci['head_sha'])
                and positive_id(ci['run_id']) and positive_id(ci['run_attempt'])
                and ci['checkout_sha'] == sample['source_commit']
            )))


def platform_timing_identity(sample):
    ci = sample['ci']
    return (ci is not None and ci['event'] in NORMAL_EVENTS and ci['run_attempt'] == '1'
            and sample['profile'] in AUTOMATIC_PROFILES and sample['platform'] in PLATFORMS.values()
            and sample_identity_complete(sample))


def sample_eligible(sample):
    return sample['coverage_complete'] and sample_identity_complete(sample)


def timing_issue(issues, issue):
    if not any({key: value for key, value in note.items() if key != 'file'} == issue for note in issues):
        issues.append(issue)


def has_job_identity(job):
    return positive_id(job['job_id']) and not any(job['identity_issues'].values())


def has_job_role_claim(job):
    # A duplicate ID cannot supply a clock, but its grounded role claim
    # can still conflict with the role observed in a separate listing.
    issues = job['identity_issues']
    return (positive_id(job['job_id']) and not issues['missing']
            and issues['conflicts'] in ([], ['duplicate_job_id']))


def known_value(values, unknown=None):
    """Join an identified field. Missing evidence has no vote."""
    known = set(values) - {unknown}
    return (next(iter(known), unknown), False) if len(known) <= 1 else (unknown, True)


def timing_checkout_conflicts(samples, hosted, issues):
    observed = defaultdict(set)
    for sample in samples:
        if sample['ci'] and sample_identity_complete(sample) and (
                platform_timing_identity(sample) or windows_producer_job_name(sample)):
            observed[ci_key(sample['ci'])].add(sample['source_commit'])
    for run in hosted:
        checkouts = [run.get('checkout_sha'), *(job.get('checkout_sha')
                    for job in [*run['jobs'], *run.get('sibling_jobs', [])] if has_job_identity(job))]
        observed[ci_key(run)].update(sha for sha in checkouts if commit_id(sha))
    disputes = {attempt for attempt, claims in observed.items() if known_value(claims)[1]}
    for attempt in sorted(disputes, key=repr):
        timing_issue(issues, {'run_id': attempt[1], 'checkouts': sorted(observed[attempt]),
                              'error': 'Conflicting timing checkouts for this attempt.'})
    return disputes


def clock_preference(run):
    return (run['status'] == 'completed', len(run['jobs']) + len(run.get('sibling_jobs', [])),
            json.dumps(run, sort_keys=True))


def job_clock_identity(job):
    return (job.get('name'), job['job_id'], job['platform'], job['runner_label'], job['status'], job['conclusion'])


def attempt_topology(run):
    workflow = run['workflow']
    return (
        run['conclusion'], split_windows_clock(run),
        bool(workflow.get('windows_recovery_producer')),
        *(frozenset(job_clock_identity(job) for job in bound_jobs(run.get(field, ()))) for field in ('jobs', 'sibling_jobs')),
    )


def merge_clock_fields(rows, fields):
    values, disputes = {}, []
    for field in fields:
        values[field], conflict = known_value(duration(row.get(field)) for row in rows)
        if conflict:
            disputes.append(field)
    return values, disputes


def merge_job_clock(jobs, *, projected=False):
    primary = [job_endpoint_choices(job) for job in jobs if 'endpoints' in job]
    legacy = [job['seconds'] for job in jobs if 'endpoints' not in job]
    choices = {field: join_timestamp_choices(row.get(field, ()) for row in primary)
               for field in sorted(JOB_ENDPOINTS)}
    step_identities = {}
    for kind in STEP_KINDS:
        identity, disputed = step_identity([job['step_identities'][kind] for job in jobs
                                            if kind in job.get('step_identities', {})])
        if disputed:
            choices[f'{kind}_start'] = choices[f'{kind}_end'] = None
        elif identity is not None:
            step_identities[kind] = identity
    points, unplaced = project_job_points(choices)
    retained = retain_endpoint_choices(choices)
    endpoint_disputes = frozenset(retained.get('endpoint_disputes', ()))
    derived = job_seconds(points)
    dependencies = dict(JOB_INTERVALS)
    if projected:
        derived = {'total': derived['total'], 'tail': derived['tail'], 'aggregate_setup': derived['setup']}
        dependencies['aggregate_setup'] = dependencies['setup']
    seconds, disputes = {}, []
    for field in (*ATTEMPT_SECONDS, *SPLIT_SECONDS):
        value, disputed = known_value([derived.get(field), *(row.get(field) for row in legacy)])
        if field in derived and unplaced.intersection(dependencies[field]):
            value = None
        elif disputed or (field in derived and endpoint_disputes.intersection(dependencies[field])):
            disputes.append(field)
            value = None
        seconds[field] = value
    checkout, _ = known_value(job.get('checkout_sha') for job in jobs)
    representative = max(jobs, key=lambda job: json.dumps(job, sort_keys=True))
    resolved = {key: value for key, value in representative.items()
                if key not in ('step_identities', 'endpoint_disputes', 'endpoint_choices')}
    resolved.update(retained, seconds=seconds)
    if checkout:
        resolved['checkout_sha'] = checkout
    if step_identities:
        resolved['step_identities'] = step_identities
    return resolved, tuple(disputes), endpoint_disputes, unplaced


def coalesce_job_observations(rows):
    # One identity can carry several partial or contradictory facts in one
    # listing, just as it can across independently retained listings.
    grouped = defaultdict(list)
    diagnostic = []
    for row in distinct_observations(rows):
        if has_job_identity(row):
            grouped[job_clock_identity(row)].append(row)
        else:
            diagnostic.append(row)
    for group in grouped.values():
        if len(group) == 1 or known_value(row.get('checkout_sha') for row in group)[1]:
            diagnostic.extend(group)
        else:
            diagnostic.append(merge_job_clock(group)[0])
    return sorted(diagnostic, key=lambda row: (row.get('name', ''), row['job_id'] or ''))


def complete_attempt_membership(observations, issues):
    representative = max(observations, key=clock_preference)
    run_id = representative['run_id']
    branch, branch_conflict = known_value(row.get('head_branch') for row in observations)
    if branch_conflict or len({attempt_topology(row) for row in observations}) > 1:
        timing_issue(issues, {'run_id': run_id, 'error': 'Conflicting complete attempt clocks.'})
        return None

    resolved = {**representative, 'head_branch': branch}
    checkout, _ = known_value(row.get('checkout_sha') for row in observations)
    if checkout:
        resolved['checkout_sha'] = checkout
    # Endpoint-based workflow totals derive from the jobs, so they do not supply another vote.
    legacy = [row['workflow'] for row in observations if not any(
        'endpoints' in job for job in (*row['jobs'], *row.get('sibling_jobs', ()))
    )]
    workflow, disputed = merge_clock_fields(legacy, WORKFLOW_SECONDS)
    resolved['workflow'] = {**representative['workflow'], **workflow}
    if disputed:
        timing_issue(issues, {'run_id': run_id, 'fields': disputed, 'error': 'Conflicting complete attempt clocks.'})

    return resolved


def attempt_clocks(hosted, issues):
    failures, attempts = defaultdict(dict), defaultdict(list)
    for run in hosted:
        key = ci_key(run)
        if run.get('authenticated_failure'):
            failures[key][run['conclusion']] = run
        else:
            attempts[key].append(run)

    clocks, complete = {}, set()
    for key in sorted(attempts.keys() | failures.keys(), key=repr):
        authority = failures.get(key, {})
        matching = [run for run in attempts[key] if not authority or set(authority) == {run['conclusion']}]
        if len(matching) != len(attempts[key]):
            timing_issue(issues, {'run_id': key[1], 'authenticated_conclusions': sorted(authority),
                                  'error': 'Attempt clock conflicts with the authenticated workflow conclusion.'})
        # A grounded split parent is evidence of required producers even
        # when a particular listing omits some of them.
        split = [row for row in matching if split_windows_clock(row)
                 and any(job['platform'] == 'Windows' and has_job_identity(job) for job in row['jobs'])]
        candidates = [run for run in matching if clock_membership_complete(run)]
        if split:
            split_parents = {job_clock_identity(job) for row in split for job in row['jobs']
                             if job['platform'] == 'Windows' and has_job_identity(job)}
            candidates = [row for row in candidates if split_windows_clock(row)
                          or not any(job_clock_identity(job) in split_parents for job in row['jobs'])]
        if candidates:
            if (clock := complete_attempt_membership(candidates, issues)) is not None:
                clocks[key] = clock
                complete.add(key)
        elif matching:
            clocks[key] = max(split or matching, key=clock_preference)
        if key in clocks and split_windows_clock(clocks[key]):
            chosen = clocks[key]
            parents = {job_clock_identity(job) for job in chosen['jobs'] if job['platform'] == 'Windows'}
            scopes = [row['workflow'] for row in split
                      if any(job_clock_identity(job) in parents and has_job_identity(job) for job in row['jobs'])]
            profile, disputed = known_value(row.get('windows_profile') for row in scopes)
            disputed = disputed or any(row.get('windows_profile_disputed') for row in scopes)
            clocks[key] = {**chosen, 'workflow': {**chosen['workflow'], 'windows_profile': profile,
                                                'windows_profile_disputed': disputed}}
            if disputed:
                timing_issue(issues, {'run_id': key[1], 'platform': 'Windows',
                                      'error': 'Conflicting Windows publishing profiles.'})
        if len(authority) == 1:
            clocks.setdefault(key, next(iter(authority.values())))
        elif len(authority) > 1:
            timing_issue(issues, {'run_id': key[1], 'error': 'Conflicting authenticated workflow conclusions.'})
    return clocks, complete


class JobClockEvidence(NamedTuple):
    job: dict | None
    disputed: tuple
    endpoint_disputes: frozenset = frozenset()


def job_clock_observations(hosted, clocks, issues):
    # Matrix authority determines which protected jobs exist. A grounded
    # measurement for one of those jobs can come from any matching listing.
    jobs, roles = defaultdict(list), defaultdict(set)
    for run in hosted:
        attempt = ci_key(run)
        authority = clocks.get(attempt)
        if (authority is None or authority.get('authenticated_failure') or run.get('authenticated_failure')
                or run['conclusion'] != authority['conclusion']
                or known_value(row.get('head_branch') for row in (authority, run))[1]):
            continue
        protected = {job_clock_identity(job) for job in authority['jobs']}
        for job in run['jobs']:
            role = ('platform', job['platform'])
            if has_job_role_claim(job):
                roles[attempt, job['job_id']].add(role)
            if has_job_identity(job) and job_clock_identity(job) in protected:
                jobs[attempt, role].append(protected_job_facts(run, authority, job))
        for job in run.get('sibling_jobs', []):
            name = job.get('name')
            role = ('producer', name) if name in WINDOWS_PRODUCERS.values() else ('sibling', job['job_id'])
            if has_job_role_claim(job):
                roles[attempt, job['job_id']].add(role)
            if not has_job_identity(job):
                continue
            if (role[0] == 'producer' and job['platform'] == 'Windows'
                    and job['runner_label'] == 'windows-latest'):
                jobs[attempt, role].append(inherit_job_checkout(run, job))
            elif any(job_clock_identity(row) == job_clock_identity(job) for row in authority.get('sibling_jobs', ())):
                jobs[attempt, ('sibling', job['job_id'])].append(inherit_job_checkout(run, job))
    role_disputes = {(attempt, role) for (attempt, _), claims in roles.items() if len(claims) > 1 for role in claims}
    observed, producer_disputes = {}, set()
    for (attempt, role), candidates in sorted(jobs.items(), key=lambda item: repr(item[0])):
        scope, name = role
        issue = {'run_id': attempt[1], scope: name}
        if len({job_clock_identity(job) for job in candidates}) != 1 or (attempt, role) in role_disputes:
            error = 'Conflicting protected job observations.'
            if scope == 'producer':
                producer_disputes.add(attempt)
                error = 'Conflicting Windows producer job observations.'
            timing_issue(issues, {**issue, 'error': error})
            observed[attempt, role] = JobClockEvidence(None, ())
            continue
        projected = role == ('platform', 'Windows') and split_windows_clock(clocks[attempt])
        job, disputed, endpoint_disputes, unplaced = merge_job_clock(candidates, projected=projected)
        if unplaced:
            timing_issue(issues, {**issue, 'endpoints': sorted(unplaced),
                                  'error': 'Job phase endpoint ordering is unavailable or inconsistent.'})
        if endpoint_disputes:
            issue['endpoints'] = sorted(endpoint_disputes)
        if disputed or endpoint_disputes:
            error = ('Conflicting Windows producer job measurements.' if scope == 'producer'
                     else 'Conflicting protected job measurements.')
            timing_issue(issues, {**issue, 'fields': list(disputed), 'error': error})
        observed[attempt, role] = JobClockEvidence(job, disputed, endpoint_disputes)
    return observed, producer_disputes


def protected_job_facts(run, authority, job):
    job = inherit_job_checkout(run, job)
    if job['platform'] != 'Windows':
        return job
    layout, observed = windows_clock_lanes(authority), windows_clock_lanes(run)
    if not layout:
        return job
    seconds = dict(job['seconds'])
    # V1 split records store the original setup as aggregate_setup. That
    # field marks a projected row, even if the original setup was unknown.
    projected = 'aggregate_setup' in seconds
    if ('endpoints' in job or observed != layout or not projected
            or not has_windows_producers(run.get('sibling_jobs', ()), layout)):
        aggregate_setup = seconds.get('aggregate_setup') if observed else seconds['setup']
        seconds.update(dict.fromkeys((*WINDOWS_DERIVED_STAGES, *WINDOWS_COPIED_STAGES)))
        seconds['aggregate_setup'] = aggregate_setup
    for field in WINDOWS_COPIED_STAGES:
        seconds[field] = None
    return {**job, 'seconds': seconds}


def inherit_job_checkout(run, job):
    checkout = job.get('checkout_sha') or run.get('checkout_sha')
    return {**job, 'checkout_sha': checkout} if checkout else job


class ClockEvidence(NamedTuple):
    attempts: dict
    complete: set
    jobs: dict
    checkout_disputes: set
    producer_disputes: set


def disputed_member_input(source, metric, endpoints):
    return source is not None and (
        source.job is None or not source.endpoint_disputes.isdisjoint(endpoints)
        or (metric in source.disputed and source.endpoint_disputes.isdisjoint(JOB_INTERVALS[metric]))
    )


def project_windows_stages(evidence, attempt, run, fact, profile, issues):
    lanes = windows_clock_lanes(run)
    if not lanes:
        return fact
    profile = run['workflow'].get('windows_profile') or profile
    sources = {lane: evidence.jobs.get((attempt, ('producer', WINDOWS_PRODUCERS[lane]))) for lane in lanes}
    have_members = (run['workflow'].get('windows_layout_identified', True)
                    and all(source is not None and source.job is not None for source in sources.values()))
    members = {lane: source.job for lane, source in sources.items()} if have_members else {}
    scoped = profile in SCOPED_PROFILES
    groups = {'all': lanes, 'active': ('core',) if scoped else lanes}
    seconds = dict(fact.job['seconds'])
    derived, blocked = split_stage_seconds(fact.job, members, scoped)
    disputed = set()
    for stage, inputs in WINDOWS_DERIVED_STAGES.items():
        if stage in blocked:
            seconds[stage] = None
            continue
        competing_member = any(disputed_member_input(sources[lane], metric, endpoints)
                               for group, metric, endpoints in inputs for lane in groups[group])
        value, competing_stage = known_value([seconds[stage], derived[stage]])
        if competing_member or competing_stage or fact.endpoint_disputes & WINDOWS_PARENT_ENDPOINTS[stage]:
            disputed.add(stage)
        seconds[stage] = None if stage in disputed or not have_members else value
    for stage, (lane, metric) in WINDOWS_COPIED_STAGES.items():
        source = sources[lane]
        seconds[stage] = source.job['seconds'][metric] if source and source.job is not None else None
        if source and (source.job is None or metric in source.disputed):
            disputed.add(stage)
    if disputed:
        timing_issue(issues, {'run_id': attempt[1], 'platform': 'Windows',
                              'fields': [stage for stage in (*ATTEMPT_SECONDS, *SPLIT_SECONDS) if stage in disputed],
                              'error': 'Conflicting Windows aggregate stage inputs.'})
    disputes = tuple(field for field in (*fact.disputed, *sorted(disputed)) if field not in blocked)
    return JobClockEvidence({**fact.job, 'seconds': seconds}, disputes, fact.endpoint_disputes)


def protected_job(evidence, attempt, platform, profile, issues):
    fact = evidence.jobs.get((attempt, ('platform', platform)))
    if fact and fact.job and platform == 'Windows' and split_windows_clock(evidence.attempts[attempt]):
        return project_windows_stages(evidence, attempt, evidence.attempts[attempt], fact, profile, issues)
    return fact


def clock_ready_for_capture(evidence, attempt, profiles, issues):
    if attempt not in evidence.complete:
        return False
    run = evidence.attempts[attempt]
    for row in bound_jobs(run['jobs']):
        platform = row['platform']
        fields = ('total', 'runner')
        if split_windows_clock(run) and row['conclusion'] == 'success':
            fields += ('harness_step',)
        if not observed_fields(protected_job(evidence, attempt, platform, profiles.get(platform), issues), fields):
            return False
    for row in bound_jobs(run.get('sibling_jobs', ())):
        name = row.get('name')
        role = ('producer', name) if name in WINDOWS_PRODUCERS.values() else ('sibling', row['job_id'])
        if not observed_fields(evidence.jobs.get((attempt, role)), ('total', 'runner')):
            return False
    return True


def observed_fields(fact, required):
    # A diagnosed dispute is not a missing download. It cannot acquire a
    # numeric value but need not consume an automatic retry.
    return fact is not None and (fact.job is None or all(
        fact.job['seconds'].get(field) is not None or field in fact.disputed for field in required
    ))


def admitted_attempts(samples, hosted, issues):
    observed, _ = attempt_clocks(hosted, [])
    claims = [row for row in samples
              if not native_report_issue(row, observed.get(ci_key(row['ci'] or {})), [])]
    checkouts = timing_checkout_conflicts(claims, hosted, issues)
    return [clock for clock in hosted if ci_key(clock) not in checkouts], checkouts


def reconcile_timing_clocks(samples, hosted, issues):
    consistent, checkouts = admitted_attempts(samples, hosted, issues)
    clocks, complete = attempt_clocks(consistent, issues)
    jobs, producer_disputes = job_clock_observations(consistent, clocks, issues)
    return ClockEvidence(clocks, complete, jobs, checkouts, producer_disputes)


def coalesced_sample_groups(samples, issues, *, clocks=None):
    observations, producers = defaultdict(list), defaultdict(list)
    clock_by_attempt = clocks or {}
    diagnostics = []
    for sample in samples:
        ci = sample['ci']
        attempt = ci_key(ci or {})
        scope = windows_producer_job_name(sample)
        if native_report_issue(sample, clock_by_attempt.get(attempt), diagnostics):
            scope = ('native diagnostic', sample['profile'], sample['run_id'], sample['arch'], tuple(sample['selected']))
        elif ci and scope:
            producers[attempt].append(sample)
        if ci and scope is None and ci['event'] in NORMAL_EVENTS and sample['profile'] not in AUTOMATIC_PROFILES:
            scope = ('diagnostic', sample['profile'])
        key = (*ci_key(ci), sample['platform'], scope) if ci else ('local', sample['run_id'], sample['platform'])
        observations[key].append(sample)
    groups = []
    for group in observations.values():
        attempt = ci_key(group[0]['ci'] or {})
        resolved = coalesce_sample_group(group, producers.get(attempt, ()), clock_by_attempt.get(attempt), diagnostics)
        groups.append((group, resolved))
    for issue in sorted(diagnostics, key=lambda note: json.dumps(note, sort_keys=True)):
        timing_issue(issues, issue)
    return groups


def coalesce_samples(samples, issues, *, clocks=None):
    return [resolved for _, resolved in coalesced_sample_groups(samples, issues, clocks=clocks)]


def coalesce_sample_group(group, producers, run, issues):
    candidates = [row for row in group if sample_eligible(row)]
    if not candidates:
        # A diagnostic can still carry exact supplementary metadata. Compare
        # metadata only between projections of the same retained observation.
        best = max(group, key=lambda row: (sample_identity_complete(row) is True, row['recorded_at'] or '',
                                           json.dumps(sample_core(row), sort_keys=True)))
        candidates = [row for row in group if sample_core(row) == sample_core(best)]
    previous = candidates[0]
    producer = windows_producer_job_name(previous)
    proofs = {}
    for sample in candidates:
        if (proof := sample.get('generated_contracts')) is not None:
            proofs[json.dumps(proof, sort_keys=True)] = sample
        if sample_core(previous) != sample_core(sample) or len(proofs) > 1:
            issue = {'run_id': observation_id(sample), 'platform': sample['platform'],
                     'error': 'Conflicting complete timing samples for this run and platform.'}
            if producer:
                issue.update(producer=producer, error='Conflicting complete timing samples for this Windows producer.')
            timing_issue(issues, issue)
            if sample_core(previous) != sample_core(sample):
                return [previous, sample]
            return list(proofs.values())
    result = reconcile_cache_metadata(previous, candidates, matched_producer_caches(previous, producers, run), issues)
    if proofs:
        result['generated_contracts'] = next(iter(proofs.values()))['generated_contracts']
    return [result]


def cache_conflict_issue(sample, producer, issues):
    error = 'Conflicting cache observations for this timing sample.'
    issue = {'run_id': observation_id(sample), 'platform': sample['platform']}
    if producer:
        issue['producer'] = producer
        error = ('Conflicting Windows producer and aggregate cache observations.' if sample['profile'] == 'full'
                 else 'Conflicting cache observations for this Windows producer.')
    timing_issue(issues, {**issue, 'error': error})


def reconcile_cache_metadata(sample, observations, matched, issues):
    scalar, scalar_disputed = known_value(
        (row['cache'] for row in observations if 'windows_caches' not in row), 'unknown',
    )
    vectors = [row['windows_caches'] for row in observations if 'windows_caches' in row]
    result = dict(sample)
    conflict = scalar_disputed
    if scalar_disputed:
        cache_conflict_issue(sample, windows_producer_job_name(sample), issues)
    resolved = {}
    for lane, role in WINDOWS_PRODUCERS.items():
        claims = [vector[lane] for vector in vectors] + matched.get(lane, [])
        value, disputed = known_value(claims, 'unknown')
        # A legacy scalar class can disagree with a lane, but cannot supply
        # any individual field of a four-producer cache vector.
        expected = 'not_requested' if lane == 'python' else scalar
        if disputed or (value != 'unknown' and expected != 'unknown' and value != expected):
            cache_conflict_issue(sample, role, issues)
            conflict = True
            value = 'unknown'
        resolved[lane] = value
    if vectors:
        result['windows_caches'] = resolved
    cache = windows_aggregate_cache(resolved) if vectors else scalar
    if conflict:
        result['cache_conflict'] = True
    result['cache'] = 'unknown' if conflict else cache
    return result


def split_windows_report(sample):
    return sample['ci'] and windows_lane_report_matches(
        sample['ci'], sample['run_id'], {'system': sample['platform'], 'machine': sample['arch']},
    )


def matched_producer_caches(sample, producers, run):
    if sample['profile'] != 'full' or not sample_identity_complete(sample) or not split_windows_report(sample):
        return {}
    digest = sample.get('inputs_sha256')
    if sample['ci'] is None or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
        return {}
    # A component vector names its cache fields independently of job clocks.
    # A partial listing can lack the recovery job without disproving its report.
    lanes = WINDOWS_LANES if 'windows_caches' in sample else windows_clock_lanes(run)
    if not lanes:
        return {}
    rows = {row['id']: row for row in sample['checks'] if row['id'] in sample['selected']}
    states = defaultdict(list)
    for producer in producers:
        # The caller has already verified the publisher's lane selection.
        # Join its measurements only to the very same aggregate input.
        lane = producer['profile'].removeprefix('ci-windows-')
        if (lane not in lanes or producer['ci'] != sample['ci'] or not sample_eligible(producer)
                or producer.get('inputs_sha256') != digest):
            continue
        claimed = set(producer['selected'])
        measurements = [row for row in producer['checks'] if row['id'] in claimed]
        if (len(measurements) == len(claimed) and {row['id'] for row in measurements} == claimed
                and all(rows.get(row['id']) == row for row in measurements)):
            states[lane].append(producer['cache'])
    return states


@lru_cache(maxsize=128)
def native_publishing_catalog(revision, *, root=ROOT, object_type=b'commit'):
    """Allow a substitute only when Git confirms the requested object is absent."""
    if not commit_id(revision):
        return True, None
    object_name = revision.encode('ascii')
    try:
        observed = git(root, 'cat-file', '--batch-check=%(objectname) %(objecttype)',
                       input=object_name + b'\n').strip()
    except (ScopeError, OSError, subprocess.SubprocessError):
        return True, None
    if observed == object_name + b' missing':
        return False, None
    if observed != object_name + b' ' + object_type:
        return True, None
    try:
        path = f'{revision}:project/checks.json'
        if int(git(root, 'cat-file', '-s', path)) > COMPACT_LIMIT:
            return True, None
        catalog = json_object(git(root, 'cat-file', 'blob', path))
        rows = catalog['checks']
        ids = {row['id'] for row in rows}
        if (not isinstance(rows, list) or not ids or len(rows) != len(ids)
                or not all(short_label(key) for key in ids)
                or not all(row['status'] in {'implemented', 'planned', 'retired'}
                           and row.get('cadence', 'change') in {'change', 'task', 'release'} for row in rows)):
            return True, None
        profiles = catalog.get('profiles', {})
        if not isinstance(profiles, dict) or any(
            not isinstance(keys, list) or len(set(keys)) != len(keys) or not set(keys) <= ids
            for name, keys in profiles.items() if name in SCOPED_PROFILES
        ):
            return True, None
        return True, catalog
    except (ScopeError, OSError, subprocess.SubprocessError, *TIMING_INPUT_ERRORS):
        return True, None


@lru_cache(maxsize=128)
def committed_publishing_inputs(revision, *, root=ROOT):
    try:
        source = committed_source_identity(root, revision)
        publishing_paths = ('project/checks.json', 'scripts/check.py', 'scripts/verification_scope.py')
        if not all(is_source_input(path, source['exclusions']) for path in publishing_paths):
            return None
        return source['sha256']
    except (ScopeError, OSError, subprocess.SubprocessError, *TIMING_INPUT_ERRORS):
        return None


def publishing_catalog_source(sample):
    ci = sample['ci']
    checkout = ci['checkout_sha']
    present, catalog = native_publishing_catalog(checkout)
    if present:
        return (checkout, catalog) if catalog is not None else None
    if ci['event'] != 'pull_request' or not sample.get('inputs_sha256'):
        return None
    # An expired PR merge commit can have the same inputs as its exact head,
    # or a retained merge-tree proof. Bind the catalog to the reported digest.
    head = ci['head_sha']
    _, catalog = native_publishing_catalog(head)
    if catalog is not None and committed_publishing_inputs(head) == sample['inputs_sha256']:
        return head, catalog
    proof = generated_contracts_proof(sample, eligible_only=False)
    if proof:
        tree = proof['tree']
        _, catalog = native_publishing_catalog(tree, object_type=b'tree')
        if catalog is not None and committed_publishing_inputs(tree) == sample['inputs_sha256']:
            return tree, catalog
    return None


def publisher_lane_assignment(sample, revision):
    if (sample.get('working_tree_dirty') is not False or not sample.get('inputs_sha256')
            or committed_publishing_inputs(revision) != sample['inputs_sha256']):
        return None
    return committed_windows_assignment(ROOT, revision)


def native_report_issue(sample, run, issues):
    ci, profile = sample['ci'], sample['profile']
    producer = windows_producer_job_name(sample)
    if (sample['platform'] != 'Windows' or not ci or ci['event'] not in NORMAL_EVENTS
            or not sample_identity_complete(sample)
            or (not producer and profile not in AUTOMATIC_PROFILES)):
        return False
    lanes = windows_clock_lanes(run)
    split_report = 'windows_caches' in sample or split_windows_report(sample)
    workflow = run['workflow'] if run else {}
    parent = workflow.get('windows_profile')
    platform = {'system': sample['platform'], 'machine': sample['arch']}
    split_identity = split_windows_report(sample)
    if producer:
        identity = split_identity
    else:
        identity = (windows_x64(platform) and (not (split_report or lanes) or (
            (profile != 'full' or split_identity) and (run is None or lanes)
            and not workflow.get('windows_profile_disputed') and (parent is None or parent == profile)
        )))
    selected = set(sample['selected'])
    measured = [row['id'] for row in sample['checks'] if row['reason_code'] != 'not_selected'
                and row['id'] in selected]
    extra_result = any(row['id'] not in selected and row['status'] in {'passed', 'failed'}
                       for row in sample['checks'])
    publishing = publishing_catalog_source(sample) if identity and sample['task_id'] is None else None
    assignment = None
    if publishing and producer:
        assignment = publisher_lane_assignment(sample, publishing[0])
    if publishing and not extra_result and len(measured) == len(selected) and set(measured) == selected:
        _, catalog = publishing
        if not producer:
            if windows_published_scope(sample['selected'], profile, lanes or WINDOWS_LANES, catalog):
                return False
        elif assignment:
            expected_lanes = tuple(lane for lane in WINDOWS_LANES if lane == 'core' or lane in assignment)
            # An incomplete listing can still prove the identity of this producer's job.
            # Compare widths only when the Actions capture actually identified the layout.
            observed_lanes = windows_clock_lanes(run, identified=True)
            if ((not observed_lanes or observed_lanes == expected_lanes)
                    and sample['selected'] == sorted(selected)
                    and len({row['id'] for row in sample['checks']}) == len(sample['checks'])
                    and windows_published_scope(sample['selected'], profile, expected_lanes, catalog, assignment=assignment)):
                return False
    issue = {'run_id': observation_id(sample), 'platform': 'Windows'}
    if producer:
        issue['producer'] = producer
    if identity and sample['task_id'] is None and not publishing:
        error = 'Native timing check selection is unknown because the publishing catalog is unavailable.'
    elif publishing and producer and assignment is None:
        error = 'Native timing producer selection is unknown because its publishing assignment is unavailable.'
    else:
        kind = 'producer' if producer else 'aggregate' if (lanes or split_report) else 'report'
        error = f'Timing {kind} does not match its native publisher identity, job layout or check scope.'
    timing_issue(issues, {**issue, 'error': error})
    return True


def merge_samples(samples, hosted, issues):
    # A saved review may retain the observations even when the original
    # needed to reconcile them was unreadable. Only accept clocks whose
    # retained attempt could be checked, while keeping workflow conclusions.
    unverified = {capture_filename_attempt(note.get('file')) for note in issues
                  if note.get('retained_capture_unverified') in ('checkout', 'shared')}
    checked = [clock for clock in hosted if clock.get('authenticated_failure') or capture_attempt(clock) not in unverified]
    evidence = reconcile_timing_clocks(samples, checked, issues)
    for inputs, group in coalesced_sample_groups(samples, issues, clocks=evidence.attempts):
        if len(group) > 1:
            continue
        sample = group[0]
        ci = sample['ci']
        attempt = ci_key(ci) if ci else None
        run = evidence.attempts.get(attempt)
        producer = windows_producer_job_name(sample)
        fact = None
        if run:
            if producer:
                fact = evidence.jobs.get((attempt, ('producer', producer)))
            elif ci['event'] not in NORMAL_EVENTS or sample['profile'] in AUTOMATIC_PROFILES:
                fact = protected_job(evidence, attempt, sample['platform'], sample['profile'], issues)
        job = fact.job if fact else None
        complete = attempt in evidence.complete
        unqualified_native = native_report_issue(sample, run, issues)
        if unqualified_native:
            job = None
        if job and not sample_identity_complete(sample):
            issue = {'run_id': observation_id(sample), 'platform': sample['platform'],
                     'error': 'Timing sample checkout or identity does not match the retained job evidence.'}
            if producer:
                issue['producer'] = producer
            issues.append(issue)
            job = None
        seconds = {key: sample['seconds'][key] for key in ('harness', 'check_work')}
        seconds.update({key: job['seconds'][key] if job else None for key in ATTEMPT_SECONDS})
        seconds.update({key: duration(job['seconds'].get(key)) if job else None for key in SPLIT_SECONDS})
        cache = 'unknown' if unqualified_native else sample['cache']
        if (run and run['workflow'].get('windows_recovery_producer') and sample['platform'] == 'Windows'
                and sample['profile'] == 'full' and 'windows_caches' not in sample):
            cache = 'unknown'
        origins = {}
        if job and sample['profile'] == 'full':
            # Coalescing can enrich a report with compatible cache or proof metadata.
            # Keep the identities of the original inputs in this admitted group.
            origins['report_inputs_sha256'] = sorted({report_input_sha256(row) for row in inputs if sample_eligible(row)})
        yield {**sample, **origins, 'seconds': seconds, 'cache': cache,
               'job': {key: value for key, value in job.items() if key not in {'seconds', 'endpoints', 'step_identities', 'endpoint_disputes', 'endpoint_choices'}} if job else None,
               'workflow_success': run['conclusion'] == 'success' if run else None,
               'workflow_conclusion': run['conclusion'] if run else None,
               'hosted_head_branch': run.get('head_branch') if run else None,
               'hosted_identity_complete': complete if run else None,
               'hosted_success_complete': complete and successful_clock_jobs(run) if run else None,
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
        if windows_producer_job_name(sample):
            reason = 'Windows producer observations do not establish protected platform coverage.'
        elif not sample_eligible(sample):
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
            if not sample.get('cache_conflict'):
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
            'input_issues': current_collection_issues(issues, samples, hosted),
            'next_action': ('Review objective breaches and material growth, then collect missing comparable observations.'
                            if reviews else 'Retain a measured fast run or a hosted change report with matching Actions jobs.')}


def local_paths(root):
    directories = timing_directories(root, [])
    return sorted({
        *root.glob('.reports/runs/*/test-report.json'), *root.glob('.reports/ci/*/*/test-report.json'),
        *(path for path in directories[0].glob('*.json') if not CI_TIMING_NAME.fullmatch(path.name)),
        *(path for path in [root / '.reports/test-report.json'] if path.is_file()),
        *incremental_timing_paths(directories),
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


def measure_budget_recovery(scope, repair, reports, growth_baseline, samples, ancestor, checkout_parents, indexed_conflicts):
    rows = [sample for sample in samples if has_hosted_inputs(sample) and matches_budget_scope(sample, scope)]
    required_reports = reports | growth_baseline
    linked = [row for row in rows if report_key(row) in required_reports]
    attempts = defaultdict(set)
    for row in linked:
        attempts[report_key(row)].add(ci_key(row['ci']))
    conflicts = required_reports & indexed_conflicts
    conflicts.update(key for key, identities in attempts.items() if len(identities) > 1)
    linked = [row for row in linked if report_key(row) not in conflicts]
    unavailable = sorted(required_reports - {report_key(row) for row in linked})
    # All linked observations in this scope bound the correction, even when they belong to another cohort.
    last_time = max((timestamp(row['recorded_at']) for row in linked), default=None)
    last_run = max((int(row['ci']['run_id']) for row in linked), default=0)
    cohorts = defaultdict(list)
    for row in rows if linked else ():
        if report_key(row) not in conflicts and row['review_exclusion'] is None and row['seconds']['total'] is not None:
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
    elif conflicts:
        action = 'Resolve conflicting hosted attempts for the linked timing identities. ' + action
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
            'conflicting_reports': [{'run_id': run, 'source_commit': source} for run, source in sorted(conflicts)],
            'comparisons': comparisons, 'next_action': None if recovered else action}, cutoffs


def budget_follow_up(view, feedback, tasks, *, root=ROOT, indexed_conflicts=frozenset()):
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
                                                        view.get('samples', ()), ancestor, checkout_parents,
                                                        indexed_conflicts)
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


class TimingReadBudget:
    def __init__(self):
        self.remaining = REVIEW_READ_LIMIT


def read_bounded_timing(path, *, manual_archive=False, budget=None):
    file_limit = MANUAL_ARCHIVE_LIMIT if manual_archive else TIMING_READ_LIMIT
    limit = min(file_limit, budget.remaining) if budget is not None else file_limit
    if limit < file_limit:
        limit_error = 'Review inputs exceed the combined timing read limit.'
    elif manual_archive:
        limit_error = 'Report exceeds the manual timing archive limit.'
    else:
        limit_error = 'Report exceeds the compact timing read limit.'
    if not limit:
        raise TimingError(limit_error)
    # Opening a FIFO must not wait for a writer, including if the path changes before this open.
    flags = os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0)
    if not stat.S_ISREG(path.stat().st_mode):
        raise TimingError('Expected a regular timing input file.')
    with os.fdopen(os.open(path, flags), 'rb') as content:
        info = os.fstat(content.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise TimingError('Expected a regular timing input file.')
        if info.st_size > limit:
            raise TimingError(limit_error)
        raw = content.read(limit + 1)
    if budget is not None:
        budget.remaining = max(0, budget.remaining - len(raw))
    if len(raw) > limit:
        raise TimingError(limit_error)
    data = json.loads(raw)
    if len(raw) > TIMING_READ_LIMIT and (not isinstance(data, dict) or data.get('kind') != ARCHIVE_KIND):
        raise TimingError('Report exceeds the compact timing read limit.')
    return data


def companion_timing_sample(compact, sample, budget):
    try:
        data = read_bounded_timing(compact, budget=budget)
    except FileNotFoundError:
        return None
    if not valid_sample(data) or sample_core(data) != sample_core(sample):
        raise TimingError('Companion timing sample does not match its harness report.')
    return data


def input_error(error):
    # OSError's formatted representation includes the full file path.
    return (error.strerror or type(error).__name__) if isinstance(error, OSError) else str(error)


def timing_filename(path):
    return path.replace('\\', '/').rsplit('/', 1)[-1]


def sanitize_legacy_file_error(error):
    # Old --actions diagnostics could name the jobs input but quote the run input in the exception.
    match = LEGACY_FILE_ERROR.fullmatch(error)
    if match is None:
        return error
    try:
        names = [repr(timing_filename(literal_eval(path))) for path in (match['source'], match['target']) if path]
    except (SyntaxError, ValueError):
        return match['message']
    return f"{match['message']}: {' -> '.join(names)}"


def collection_issue(note, label):
    issue = {**note, 'error': sanitize_legacy_file_error(note['error'])}
    # Keep the original source label after sanitizing it, including on resave.
    for field in ('path', 'file'):
        if isinstance(location := note.get(field), str) and location:
            name = timing_filename(location)
            issue['error'] = issue['error'].replace(location, name)
            issue[field] = name
    if not isinstance(issue.get('file'), str) or not issue['file']:
        issue['file'] = label
    return issue


def archive_inputs(data, label, issues, *, collection_issues=True, validation_issues=None):
    invalid = issues if validation_issues is None else validation_issues
    samples = admitted_records(data.get('samples'), valid_sample, f'timing sample in {label}', invalid)
    hosted = admitted_records(data.get('hosted'), valid_clock, f'attempt clock in {label}', invalid)
    if collection_issues:
        notes = admitted_records(data.get('input_issues', []),
                                 lambda note: labels(note, ('error',)) and isinstance(note['error'], str),
                                 f'collection issue in {label}', invalid)
        issues.extend(collection_issue(note, label) for note in notes)
    return samples, hosted


def current_collection_issues(issues, samples, hosted):
    def missing_capture(note):
        return isinstance(note.get('run_id'), str) and note['error'] in {MISSING_COMPACT, MISSING_CLOCK}

    incomplete = {note['run_id'] for note in issues if missing_capture(note)}
    attempts = defaultdict(set)
    for clock in hosted:
        if clock['run_id'] in incomplete:
            attempts[clock['run_id']].add(ci_key(clock))
    recovered = {run_id for run_id, keys in attempts.items() if len(keys) == 1
                 and capture_is_complete(next(iter(keys)), samples, hosted)}
    return [note for note in issues if not (missing_capture(note) and note['run_id'] in recovered)]


def retained_attempt_paths(directories, samples, hosted):
    # A saved review supplies attempt identities, not a current verdict. The
    # canonical capture keeps its original observations even when rotation or
    # a dispute removes its qualifying platform link.
    attempts = defaultdict(set)
    for ci in chain((row['ci'] for row in samples if row.get('ci')), hosted):
        attempt = ci_key(ci)
        _, run_id, number, event, head = attempt
        if event in NORMAL_EVENTS and number == '1' and positive_id(run_id) and commit_id(head):
            attempts[f'ci-{event}-{run_id}-1.json'].add(attempt)
    return {directory / name: identities for name, identities in attempts.items() for directory in directories}


def retained_failure_paths(attempt_paths):
    failures = {}
    for directory in {path.parent for path in attempt_paths}:
        for path in directory.glob('ci-*-failure-*.json'):
            if identity := capture_filename_attempt(path.name):
                event, run, attempt = identity
                canonical = directory / f'ci-{event}-{run}-{attempt}.json'
                if canonical in attempt_paths:
                    failures[path] = attempt_paths[canonical]
    return failures


def linked_timing_keys(feedback):
    keys = set()
    for item in feedback:
        scope = item.get('verification_budget', {})
        if scope.get('event') in NORMAL_EVENTS:
            keys.update(report_key(row) for row in item.get('diagnostic_reports', ()))
            keys.update(report_key(row) for row in scope.get('growth_baseline', ()))
    return keys


def link_directory(directory, key):
    return directory / 'linked-harness' / sha256(json_bytes(key)).hexdigest()


def require_plain_index_directory(directory):
    # A different canonical spelling can name the same plain directory on Windows.
    # Inspect each existing component before accessing its children.
    for part in reversed((directory, *directory.parents)):
        if part == part.parent:
            continue
        info = entry_info(part)
        if info is not None and (not stat.S_ISDIR(info.st_mode) or reparse_point(info)):
            raise TimingError('Expected a plain directory for the linked timing index.')


def sample_belongs_to_capture(sample, path):
    ci = sample['ci']
    return (platform_timing_identity(sample)
            and path.name == f"ci-{ci['event']}-{ci['run_id']}-1.json")


def indexable_capture_samples(path, samples, hosted, issues):
    consistent, disputes = admitted_attempts(samples, hosted, issues)
    clocks, _ = attempt_clocks(consistent, [])
    # Index ownership needs identity, not derived measurements. Reopening
    # replays the original observations and reports their numeric disputes.
    return [group[0] for group in coalesce_samples(samples, [], clocks=clocks) if len(group) == 1
            and sample_belongs_to_capture(group[0], path)
            and ci_key(group[0]['ci']) not in disputes
            and not native_report_issue(group[0], clocks.get(ci_key(group[0]['ci'])), issues)]


def index_capture(path, samples, hosted=(), issues=None):
    # The empty markers refer to original files beside this index. Existing capture bytes are kept in place.
    indexable = {report_key(row) for row in indexable_capture_samples(path, samples, hosted,
                                                                   [] if issues is None else issues)}
    keys = {report_key(row) for row in samples if sample_belongs_to_capture(row, path)}
    count = 0
    for key in sorted(keys):
        directory = link_directory(path.parent, key)
        require_plain_index_directory(directory)
        marker = directory / path.name
        if key not in indexable:
            marker.unlink(missing_ok=True)
            continue
        directory.mkdir(parents=True, exist_ok=True)
        try:
            with marker.open('xb'):
                count += 1
        except FileExistsError:
            pass
    return bool(indexable), count


def index_retained_captures(root):
    directory = shared_timing_directory(root)
    if directory is None:
        raise TimingError('Expected a checkout with shared retained timing storage.')
    captures = links = 0
    issues = []
    for path in sorted(directory.glob('ci-*.json')):
        if not CANONICAL_CAPTURE.fullmatch(path.name):
            continue
        try:
            with exclusive_git_lock(path.with_suffix('.lock')):
                _, samples, hosted, _ = read_timing_capture(path)
                indexed, new_links = index_capture(path, samples, hosted, issues)
                captures += indexed
                links += new_links
        except BlockingIOError:
            issues.append({'file': path.name, 'error': 'Capture is being updated. Retry indexing when it finishes.'})
        except (OSError, RuntimeError, *TIMING_INPUT_ERRORS) as error:
            issues.append({'file': path.name, 'error': str(error)})
    return {'indexed_captures': captures, 'new_links': links, 'input_issues': issues}


def linked_capture_claims(directories, keys, issues, *, observed_capture=None):
    captures = defaultdict(set)
    claimants = defaultdict(set)
    for directory in directories:
        for key in keys:
            links = link_directory(directory, key)
            try:
                require_plain_index_directory(links)
                for marker in links.glob('ci-*.json'):
                    if CANONICAL_CAPTURE.fullmatch(marker.name):
                        captures[directory / marker.name].add(key)
                        claimants[key].add(marker.name)
            except (OSError, RuntimeError, TimingError) as error:
                issues.append({'report': key, 'error': str(error)})
    if observed_capture:
        for key in keys:
            claimants[key].add(observed_capture)
    # A second claimant remains ambiguous even if its capture or clock is unavailable.
    for (run, source), names in claimants.items():
        if len(names) > 1:
            note = {'run_id': run, 'source_commit': source, 'linked_timing_conflict': True,
                    'capture_files': sorted(names), 'error': 'Multiple indexed captures claim the timing identity.'}
            if note not in issues:
                issues.append(note)
    return captures


class RetainedCaptureReads:
    def __init__(self, checkout_directory):
        self.checkout_directory = checkout_directory
        self.observed = {}

    def location(self, path):
        return ('checkout' if path.parent == self.checkout_directory else 'shared', path.name)

    def read(self, path, issues, *, budget=None, required=True, keys=(), include_notes=False):
        try:
            if not required and entry_info(path) is None:
                return None
            _, rows, clocks, notes = read_timing_capture(path, budget=budget, missing_ok=False)
        except (OSError, RuntimeError, *TIMING_INPUT_ERRORS) as error:
            self.observed[path] = set()
            location, name = self.location(path)
            issue = {'file': name, 'error': input_error(error), 'retained_capture_unverified': location}
            if keys:
                issue['reports'] = sorted(keys)
            issues.append(issue)
            return None
        self.observed[path] = {ci_key(ci) for ci in chain((row['ci'] for row in rows), clocks)}
        if include_notes:
            issues.extend(collection_issue(note, path.name) for note in notes)
        return rows, clocks

    def clear_verified_issues(self, issues, attempts):
        # A cold reopen keeps the hold. A later ordinary read must verify
        # the corresponding original and attempt before clearing it.
        resolved = {self.location(path) for path, identities in self.observed.items()
                    if (expected := attempts.get(path)) and expected <= identities}
        issues[:] = [note for note in issues
                     if note.get('retained_capture_unverified') not in ('checkout', 'shared')
                     or (note['retained_capture_unverified'], note.get('file')) not in resolved]


def retained_capture_inputs(attempt_paths, claims, reads, issues, *, budget=None):
    # Prefer exact feedback links. An exhausted budget still leaves each
    # remaining original identifiable so older saved clocks cannot pass it.
    paths = [*sorted(claims.keys() - reads.observed.keys()),
             *sorted(attempt_paths.keys() - claims.keys() - reads.observed.keys())]
    samples, hosted = [], []
    for path in paths:
        keys = claims.get(path, set())
        capture = reads.read(path, issues, budget=budget, required=bool(keys), keys=keys)
        if capture is None:
            continue
        rows, clocks = capture
        attempts = set(attempt_paths.get(path, ()))
        if keys:
            qualified = indexable_capture_samples(path, rows, clocks, issues)
            missing = keys - {report_key(row) for row in qualified}
            if missing:
                issues.append({'file': path.name, 'reports': sorted(missing),
                               'error': 'Indexed timing identities are absent.'})
            attempts.update(ci_key(row['ci']) for row in rows
                            if report_key(row) in keys and sample_belongs_to_capture(row, path))
        samples.extend(row for row in rows if row['ci'] and ci_key(row['ci']) in attempts)
        hosted.extend(clock for clock in clocks if ci_key(clock) in attempts)
    return samples, hosted


def linked_capture_inputs(directories, keys, reads, issues, *, attempts=None, budget=None):
    claims = linked_capture_claims(directories, keys, issues)
    return retained_capture_inputs(attempts or {}, claims, reads, issues, budget=budget)


def add_retained_review_inputs(directories, samples, hosted, reads, issues, budget, *, keys=()):
    attempts = retained_attempt_paths(directories, samples, hosted)
    rows, clocks = linked_capture_inputs(directories, keys, reads, issues, attempts=attempts, budget=budget)
    samples.extend(rows)
    hosted.extend(clocks)
    # Linked captures can introduce a previously unknown attempt. Its
    # independently retained conclusion must govern the same review.
    attempts = retained_attempt_paths(directories, samples, hosted)
    failures = retained_failure_paths(attempts)
    _, clocks = retained_capture_inputs(failures, {}, reads, issues, budget=budget)
    hosted.extend(clocks)
    reads.clear_verified_issues(issues, attempts | failures)


def timing_directories(root, issues):
    directory = root / '.reports/timing'
    try:
        shared = shared_timing_directory(root)
    except (OSError, WorktreeError, RuntimeError, *TIMING_INPUT_ERRORS) as error:
        shared = None
        issues.append({'error': str(error)})
    return (directory, shared) if shared else (directory,)


def retained_budget_inputs(root, feedback=()):
    # Read a bounded window of shared automatic observations and checkout-local reviews.
    archives, issues = [], []
    directories = timing_directories(root, issues)
    directory = directories[0]
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
    review_budget = TimingReadBudget()
    reads = RetainedCaptureReads(directory)
    for path in [*recent, *archives, *local]:
        try:
            if path in recent:
                if capture := reads.read(path, issues, include_notes=True):
                    rows, clocks = capture
                    samples.extend(rows)
                    hosted.extend(clocks)
                continue
            if path in archives:
                data = read_bounded_timing(path, manual_archive=True, budget=review_budget)
            else:
                data = read_bounded_timing(path)
            if data.get('kind') == ARCHIVE_KIND:
                archive_samples, archive_hosted = archive_inputs(data, path.name, issues)
                samples.extend(archive_samples)
                hosted.extend(archive_hosted)
            elif path in local and not data.get('ci') and (data.get('selection') or {}).get('profile') == 'fast':
                samples.append(report_sample(data))
        except (OSError, *TIMING_INPUT_ERRORS) as error:
            issues.append({'file': path.name, 'error': input_error(error)})
    add_retained_review_inputs(directories, samples, hosted, reads, issues, review_budget,
                               keys=linked_timing_keys(feedback))
    return samples, hosted, issues


def budget_status(samples, hosted, issues, feedback, tasks, *, root=ROOT):
    indexed_conflicts = {report_key(note) for note in issues if isinstance(note, dict)
                         and note.get('linked_timing_conflict') is True
                         and short_label(note.get('run_id')) and commit_id(note.get('source_commit'))}
    try:
        view = timing_view(samples, hosted=hosted, issues=issues)
        follow_up = budget_follow_up(view, feedback, tasks, root=root, indexed_conflicts=indexed_conflicts)
    except TIMING_INPUT_ERRORS as error:
        view = {'objective_reviews': [], 'reviews': []}
        follow_up = budget_follow_up(view, feedback, tasks, root=root, indexed_conflicts=indexed_conflicts)
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
            'input_issues': view.get('input_issues', issues),
            'timing_command': 'pnpm project:timing'}


def offline_budget_status(root, feedback, tasks):
    return budget_status(*retained_budget_inputs(root, feedback), feedback, tasks, root=root)


def distinct_observations(rows):
    unique = {json.dumps(row, sort_keys=True): row for row in rows}
    return [unique[key] for key in sorted(unique)]


class CaptureEvidence(NamedTuple):
    samples: list
    clocks: list
    missing: set
    complete_clock: bool
    ambiguous: bool
    checkout_disputed: bool


def capture_observations(identity, samples, hosted, issues, *, conclusion=None):
    samples = [row for row in samples if ci_key(row['ci'] or {}) == identity]
    clocks = distinct_observations(row for row in hosted if ci_key(row) == identity)
    clock_evidence = reconcile_timing_clocks(samples, clocks, issues)
    clock = clock_evidence.attempts.get(identity)
    groups = coalesce_samples(samples, issues, clocks=clock_evidence.attempts)
    platforms = [group for group in groups if platform_timing_identity(group[0])
                 and not native_report_issue(group[0], clock, issues)]
    disputed_platforms = {group[0]['platform'] for group in platforms if len(group) > 1}
    checkout_disputed = identity in clock_evidence.checkout_disputes
    ambiguous = checkout_disputed or bool(disputed_platforms) or identity in clock_evidence.producer_disputes
    outcome = conclusion or (clock['conclusion'] if clock else None)
    diagnostic = outcome not in {None, 'success'}
    # A failed artifact can still establish its exact diagnostic identity.
    qualify = sample_identity_complete if diagnostic else sample_eligible
    present = {group[0]['platform']: group[0]['profile'] for group in platforms
               if len(group) == 1 and qualify(group[0])}
    missing = set(PLATFORMS.values()) - present.keys() - disputed_platforms
    complete_clock = (clock_ready_for_capture(clock_evidence, identity, present, issues) and clock['conclusion'] == outcome
                      and (diagnostic or successful_clock_jobs(clock)))
    # Keep the distinct inputs (including disputed claims). Derived fields
    # are recomputed on read, so archive order cannot suppress a known fact.
    retained = [row for row in samples if sample_identity_complete(row)]
    retained.extend(group[0] for group in groups if not sample_identity_complete(group[0]))
    return CaptureEvidence(distinct_observations(retained), clocks, missing, complete_clock, ambiguous, checkout_disputed)


def capture_is_complete(identity, samples, hosted):
    evidence = capture_observations(identity, samples, hosted, [])
    return evidence.complete_clock and not evidence.missing and not evidence.ambiguous


def write_bounded_archive(path, archive, limit, error, *, sort_keys=False):
    encoder = json.JSONEncoder(separators=(',', ':'), allow_nan=False, sort_keys=sort_keys)
    remaining = limit - 1  # Include the trailing newline in the published size.
    with path.open('xb') as output:
        for part in encoder.iterencode(archive):
            encoded = part.encode('ascii')
            remaining -= len(encoded)
            if remaining < 0:
                raise TimingError(error)
            output.write(encoded)
        output.write(b'\n')


def save_timing_capture(path, attempts, samples, clocks, issues):
    archive = {'kind': ARCHIVE_KIND, 'intake_attempts': attempts, 'samples': samples,
               'hosted': clocks, 'input_issues': issues}
    with TemporaryDirectory(dir=path.parent, prefix='.timing-') as temporary:
        staged = Path(temporary) / path.name
        write_bounded_archive(staged, archive, TIMING_READ_LIMIT,
                              'Shared timing capture exceeds the compact read limit.', sort_keys=True)
        read_timing_capture(staged)
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
            with exclusive_git_lock(path.with_suffix('.lock')):
                _, samples, clocks, _ = read_timing_capture(path)
                if samples or clocks != [failure]:
                    save_timing_capture(path, 0, [], [failure], [])
            return
        except (OSError, RuntimeError, *TIMING_INPUT_ERRORS) as error:
            failure_to_write = error
    raise TimingError(f'Could not retain the authenticated workflow failure: {failure_to_write}')


def read_timing_capture(path, *, budget=None, missing_ok=True):
    try:
        info = entry_info(path)
        if info is None:
            if missing_ok:
                return 0, [], [], []
            raise TimingError('The original timing capture is absent.')
        if not stat.S_ISREG(info.st_mode) or reparse_point(info):
            raise TimingError('Expected a plain original compact timing capture.')
        data = read_bounded_timing(path, budget=budget)
        if not isinstance(data, dict) or data.get('kind') != ARCHIVE_KIND:
            raise TimingError('Expected a compact capture archive.')
        require_review_archive_shape(data)
        attempts = data.get('intake_attempts', 1)
        if type(attempts) is not int or not 0 <= attempts <= INTAKE_ATTEMPTS:
            raise TimingError('Invalid timing capture attempt count.')
        issues = []
        samples, clocks = archive_inputs(data, path.name, [], validation_issues=issues)
        if issues:
            raise TimingError(issues[0]['error'])
        if (identity := capture_filename_attempt(path.name)) and any(
            capture_attempt(ci) != identity for ci in chain((row['ci'] or {} for row in samples), clocks)
        ):
            raise TimingError('Timing capture records do not match the event, run and attempt in its filename.')
        if identity and not CANONICAL_CAPTURE.fullmatch(path.name):
            if samples or len(clocks) != 1 or not clocks[0].get('authenticated_failure'):
                raise TimingError('Expected a retained authenticated workflow failure.')
            clock = clocks[0]
            key = sha256(json_bytes([*ci_key(clock), clock['conclusion']])).hexdigest()
            if not path.name.endswith(f'-failure-{key}.json'):
                raise TimingError('Retained workflow failure does not match its filename digest.')
        return attempts, samples, clocks, data.get('input_issues', [])
    except (OSError, *TIMING_INPUT_ERRORS) as error:
        raise TimingError(f'Could not read retained timing capture {path.name}: {input_error(error)}') from error


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
            result = update_timing_capture(path, repository, observed, identity, samples, hosted)
            try:
                index_capture(path, result[1], result[2], result[3])
            except (OSError, RuntimeError, *TIMING_INPUT_ERRORS) as error:
                result[3].append({'run_id': run_id, 'error': f'Could not index the retained capture: {error}'})
            return result
    except BlockingIOError:
        return 'in_progress', [], [], []


def update_timing_capture(path, repository, run, identity, samples, hosted):
    run_id = str(run['id'])
    attempts, prior_samples, prior_clocks, prior_issues = read_timing_capture(path)
    notes = []
    known_clocks = [*prior_clocks, *hosted]
    if failure := authenticated_failure(run, identity):
        known_clocks.append(failure)
    evidence = capture_observations(
        identity, [*prior_samples, *samples], known_clocks, notes, conclusion=run['conclusion'],
    )
    if evidence.checkout_disputed or (not evidence.missing and evidence.complete_clock):
        persisted = distinct_observations(notes)
        if (prior_samples, prior_clocks, prior_issues) != (evidence.samples, evidence.clocks, persisted):
            save_timing_capture(path, attempts, evidence.samples, evidence.clocks, persisted)
        return ('ambiguous' if evidence.ambiguous else 'retained'), evidence.samples, evidence.clocks, notes
    if attempts >= INTAKE_ATTEMPTS:
        if (prior_samples, prior_clocks) != (evidence.samples, evidence.clocks):
            save_timing_capture(path, attempts, evidence.samples, evidence.clocks, notes)
        return 'retry_exhausted', evidence.samples, evidence.clocks, [*notes, {
            'run_id': run_id, 'error': 'Incremental timing retry exhausted. Inspect this run explicitly if more timing is needed.',
        }]

    new_samples, new_clocks, issues = [], [], []
    if evidence.missing:
        try:
            criteria = {'platforms': evidence.missing} if evidence.missing != set(PLATFORMS.values()) else {}
            rows = download_timing(repository, run_id, '1', **criteria)
            new_samples = [
                row for row in admitted_records(rows, valid_sample, 'downloaded timing sample', issues)
                if ci_key(row['ci'] or {}) == identity and row['platform'] in evidence.missing
            ]
        except (OSError, subprocess.SubprocessError, *TIMING_INPUT_ERRORS) as error:
            issues.append({'run_id': run_id, 'error': f'Could not retrieve compact reports: {error}'})
    if not evidence.complete_clock and (set(PLATFORMS.values()) - evidence.missing or new_samples
                                        or run['conclusion'] != 'success' and not any(
                                            row['conclusion'] == run['conclusion'] and not row.get('authenticated_failure')
                                            for row in evidence.clocks)):
        try:
            row = inspect_jobs(run, repository)
            if valid_clock(row) and ci_key(row) == identity:
                new_clocks.append(row)
        except (OSError, subprocess.SubprocessError, *TIMING_INPUT_ERRORS) as error:
            issues.append({'run_id': run_id, 'error': f'Could not retrieve attempt jobs: {error}'})
    captured = capture_observations(
        identity, [*evidence.samples, *new_samples], [*evidence.clocks, *new_clocks], issues, conclusion=run['conclusion'],
    )
    if captured.checkout_disputed:
        status = 'ambiguous'
    elif captured.missing or not captured.complete_clock:
        status = 'partial'
        if captured.missing:
            issues.append({'run_id': run_id, 'error': MISSING_COMPACT})
        if not captured.complete_clock:
            issues.append({'run_id': run_id, 'error': MISSING_CLOCK})
    else:
        status = 'ambiguous' if captured.ambiguous else 'captured'
    # Only publish the final reader-validated candidate. An interruption,
    # overflow or invalid candidate leaves both the original and its retry count.
    issues = distinct_observations(issues)
    save_timing_capture(path, attempts + 1, captured.samples, captured.clocks, issues)
    return status, captured.samples, captured.clocks, issues


def ci_budget_status(root, repository, run):
    feedback = read_json(root / 'project/feedback.json')['items']
    samples, hosted, issues = retained_budget_inputs(root, feedback)
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
            capture = Path(f'ci-{run["event"]}-{run["id"]}-1.json')
            if intake in {'captured', 'retained', 'ambiguous'}:
                issues = [issue for issue in issues
                          if issue.get('file') != capture.name or 'retained_capture_unverified' in issue]
            samples.extend(new_samples)
            hosted.extend(new_clocks)
            issues.extend(new_issues)
            affected = {report_key(row) for row in indexable_capture_samples(capture, new_samples, new_clocks, issues)}
            affected.intersection_update(linked_timing_keys(feedback))
            if affected:
                # Collection may have published a second claimant after the first index read.
                linked_capture_claims(timing_directories(root, issues), affected, issues, observed_capture=capture.name)
        except (OSError, WorktreeError, RuntimeError, *TIMING_INPUT_ERRORS) as error:
            intake = 'unavailable'
            issues.append({'run_id': str(run['id']), 'error': str(error)})
    tasks = {task['id']: task for task in read_json(root / 'project/tasks.json')['tasks']}
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


def collection_source(root):
    """Record the producer checkout only when its clean Git revision is observable."""
    try:
        head = git(root, 'rev-parse', 'HEAD').decode('ascii').strip()
        if (commit_id(head) and not git(root, 'status', '--porcelain', '--untracked-files=normal')
                and git(root, 'rev-parse', 'HEAD').decode('ascii').strip() == head):
            return head
    except (OSError, ScopeError, subprocess.SubprocessError):
        pass
    return None


def report_input_sha256(sample):
    """Hash the complete compact input before any projection or reconciliation."""
    return sha256(json.dumps(sample, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def platform_report_receipt(sample, input_sha256):
    ci = sample['ci']
    return {**{key: ci[key] for key in (*ATTEMPT_IDENTITY, 'checkout_sha')},
            'platform': sample['platform'], 'report_id': sample['run_id'], 'report_input_sha256': input_sha256}


def platform_report_receipts(samples):
    for sample in samples:
        if (not isinstance(sample, dict) or sample.get('review_exclusion', False) is not None
                or duration(sample.get('seconds', {}).get('total')) is None
                or sample['profile'] != 'full' or not platform_timing_identity(sample)):
            continue
        for input_sha256 in sample.get('report_inputs_sha256', ()):
            if isinstance(input_sha256, str) and re.fullmatch(r'[0-9a-f]{64}', input_sha256):
                yield platform_report_receipt(sample, input_sha256)


def qualified_collected_reports(samples, collection):
    """Use only exact acquired inputs still admitted by the ordinary timing review."""
    if not collection or not commit_id(collection.get('capture_source')):
        return []
    received = collection.get('collected_samples', [])
    if not isinstance(received, list) or len(received) > 192:
        raise TimingError('Expected a bounded timing collection.')
    keys = {json.dumps(row, sort_keys=True) for row in received if isinstance(row, dict)}
    if not keys:
        return []
    matches = {}
    for receipt in platform_report_receipts(samples):
        key = json.dumps(receipt, sort_keys=True)
        if key in keys:
            matches[key] = receipt
    return [matches[key] for key in sorted(matches)]


def inspect_recent(repository, limit, samples, workflows, issues):
    source = collection_source(ROOT)
    runs = recent_runs(repository, limit)
    starting_issues = len(issues)
    with_compact, without_compact = [], []
    collected_samples = []
    for run in runs:
        run_id, attempt = str(run['id']), str(run['run_attempt'])
        try:
            compact = download_timing(repository, run_id, attempt)
            (with_compact if compact else without_compact).append(run_id)
            has_report = any((sample.get('ci') or {}).get('run_id') == run_id for sample in samples)
            samples.extend(compact)
            if compact or has_report:
                clock = inspect_jobs(run, repository)
                workflows.append(clock)
                identity = actions_attempt_identity(repository, run)
                acquired = [sample for sample in compact if ci_key(sample['ci']) == identity]
                if acquired:
                    # Qualify the original acquisition using only this collection's job evidence.
                    current = timing_view(acquired, hosted=[clock])
                    collected_samples.extend(platform_report_receipts(current['samples']))
        except (OSError, subprocess.SubprocessError, *TIMING_INPUT_ERRORS) as error:
            issues.append({'run_id': run_id, 'error': str(error)})
    return {'collected_at': datetime.now(timezone.utc).isoformat(),
            'capture_source': source if source and collection_source(ROOT) == source else None,
            'limit_per_event': limit, 'completed_runs_considered': len(runs),
            'runs_with_compact': len(with_compact), 'runs_without_compact': len(without_compact),
            'collected_samples': collected_samples,
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


def require_review_archive_shape(archive):
    if not isinstance(archive.get('samples'), list) or not isinstance(archive.get('hosted'), list):
        raise TimingError('Expected a timing archive with sample and clock lists.')
    if archive.get('recent_collection') is not None and not isinstance(archive['recent_collection'], dict):
        raise TimingError('Expected a timing archive collection record.')


def save_review_archive(path, archive):
    require_review_archive_shape(archive)
    invalid, notes = [], []
    archive_inputs(archive, path.name, notes, validation_issues=invalid)
    if invalid:
        raise TimingError(invalid[0]['error'])
    archive = {**archive, 'input_issues': notes}
    path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='.scopehub-timing-', dir=path.parent) as folder:
        staged = Path(folder) / 'archive.json'
        write_bounded_archive(staged, archive, MANUAL_ARCHIVE_LIMIT,
                              'Review archive exceeds the manual timing archive limit.')
        identity = staged.stat()
        # Publish a complete file with create-only semantics, even if another writer raced us.
        os.link(staged, path)
        return identity


def append_review_summary(path, contents, *, archive=None, archive_identity=None):
    with path.open('a', encoding='utf-8') as output:
        if archive_identity is not None:
            summary_identity = os.fstat(output.fileno())
            if (os.path.samestat(summary_identity, archive_identity)
                    or os.path.samestat(summary_identity, archive.stat())):
                raise TimingError('The review archive and job summary must use different files.')
        output.write(contents)


def validate_actions_run(run):
    if (not isinstance(run, dict) or not positive_id(run.get('id'))
            or not isinstance(run.get('repository', {}), dict)
            or any(run.get(key) is not None and not isinstance(run[key], str)
                   for key in ('event', 'head_sha', 'html_url', 'status', 'conclusion'))):
        raise TimingError('Expected an Actions run record.')
    repository = run.get('repository', {}).get('full_name')
    if repository is not None and (not isinstance(repository, str)
                                   or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository)):
        raise TimingError('Expected an Actions repository full_name in owner/name form.')
    require_verification_workflow(run)


def actions_jobs(data):
    jobs = data.get('jobs') if isinstance(data, dict) else data
    if not isinstance(jobs, list) or not all(
            isinstance(job, dict) and isinstance(job.get('name'), str)
            and isinstance(job.get('steps', []), list)
            and all(isinstance(step, dict) for step in job.get('steps', [])) for job in jobs):
        raise TimingError('Expected a list of Actions job records.')
    return jobs


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
    parser.add_argument('--index-retained', action='store_true', help='Index existing shared compact captures locally')
    args = parser.parse_args()
    if args.index_retained:
        if any((args.report, args.actions, args.run, args.attempt, args.recent, args.github_actions, args.summary, args.save)):
            parser.error('--index-retained is a standalone offline operation')
        try:
            result = index_retained_captures(ROOT)
        except (OSError, WorktreeError, RuntimeError, *TIMING_INPUT_ERRORS) as error:
            parser.exit(1, f'Timing indexing failed: {error}\n')
        print(json.dumps(result, indent=2))
        raise SystemExit(bool(result['input_issues']))
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
    explicit_input_failed = False
    read_budget = TimingReadBudget()
    read_captures = RetainedCaptureReads(ROOT / '.reports/timing')
    try:
        if args.save and args.summary and args.save.resolve() == args.summary.resolve():
            raise TimingError('The review archive and job summary must use different files.')
        for path in args.report if args.report is not None else local_paths(ROOT):
            try:
                if args.report is None and CI_TIMING_NAME.fullmatch(path.name):
                    if capture := read_captures.read(path, issues, budget=read_budget, include_notes=True):
                        rows, clocks = capture
                        samples.extend(rows)
                        workflows.extend(clocks)
                    continue
                manual = args.report is not None or (
                    path.parent == ROOT / '.reports/timing' and not CI_TIMING_NAME.fullmatch(path.name))
                data = read_bounded_timing(path, manual_archive=manual, budget=read_budget)
                if not isinstance(data, dict):
                    raise TimingError('Expected a timing report, sample or saved archive.')
                if data.get('kind') == ARCHIVE_KIND:
                    require_review_archive_shape(data)
                    invalid = []
                    archive_samples, archive_hosted = archive_inputs(data, path.name, issues, validation_issues=invalid)
                    if invalid:
                        issues.extend(invalid)
                        explicit_input_failed |= args.report is not None
                    samples.extend(archive_samples)
                    workflows.extend(archive_hosted)
                    recent = data.get('recent_collection') or recent
                elif data.get('kind') == SAMPLE_KIND:
                    if not valid_sample(data):
                        raise TimingError('Invalid timing sample.')
                    samples.append(data)
                elif 'kind' in data:
                    raise TimingError('Unexpected timing input kind.')
                else:
                    checks = data.get('checks')
                    if (not short_label(data.get('run_id')) or not isinstance(checks, list)
                            or not all(isinstance(check, dict) and short_label(check.get('id'))
                                       and short_label(check.get('status')) for check in checks)):
                        raise TimingError('Expected a harness report with run identity and checks.')
                    sample = report_sample(data)
                    if not valid_sample(sample):
                        raise TimingError('Invalid timing sample derived from harness report.')
                    samples.append(sample)
                    if args.report is None and path.name == 'test-report.json' and sample['ci']:
                        path = path.with_name(TIMING_FILE)
                        companion = companion_timing_sample(path, sample, read_budget)
                        if companion is not None:
                            samples.append(companion)
            except (OSError, *TIMING_INPUT_ERRORS) as error:
                explicit_input_failed |= args.report is not None
                issues.append({'file': path.name, 'error': input_error(error)})
                if not read_budget.remaining:
                    break
        for run_path, jobs_path in args.actions or []:
            path = run_path
            try:
                run = read_bounded_timing(run_path, budget=read_budget)
                validate_actions_run(run)
                path = jobs_path
                jobs = actions_jobs(read_bounded_timing(jobs_path, budget=read_budget))
                clock = hosted_timing(run, jobs)
                if not valid_clock(clock):
                    raise TimingError('Invalid attempt clock derived from Actions input.')
                workflows.append(clock)
            except (OSError, *TIMING_INPUT_ERRORS) as error:
                explicit_input_failed = True
                issues.append({'file': path.name, 'error': input_error(error)})
                if not read_budget.remaining:
                    break
        if args.save and explicit_input_failed:
            print(json.dumps({'input_issues': issues}, indent=2))
            parser.exit(1, 'Could not save the review because an explicit timing input was incomplete or invalid.\n')
        if args.run or args.recent is not None:
            repository = hosted_repository(args.github_actions)
        if args.run:
            for run in args.run:
                workflows.append(inspect_hosted(run, args.attempt, repository, samples))
        if args.recent is not None:
            recent = inspect_recent(repository, args.recent, samples, workflows, issues)
        if args.report is None:
            add_retained_review_inputs(timing_directories(ROOT, issues), samples, workflows,
                                       read_captures, issues, read_budget)
        view = timing_view(samples, hosted=workflows, issues=list(issues))
        if recent is not None:
            recent = {**recent, 'collected_samples': qualified_collected_reports(view['samples'], recent)}
        tasks = {task['id']: task for task in read_json(ROOT / 'project/tasks.json')['tasks']}
        feedback = read_json(ROOT / 'project/feedback.json')['items']
        view.update(weekly_review=weekly_review(), recent_collection=recent,
                    budget_follow_up=budget_follow_up(view, feedback, tasks))
        archive_identity = None
        if args.save:
            archive_identity = save_review_archive(
                args.save, {'kind': ARCHIVE_KIND, 'samples': samples, 'hosted': workflows,
                            'recent_collection': recent, 'input_issues': issues})
        if args.summary:
            append_review_summary(args.summary, review_summary(view), archive=args.save, archive_identity=archive_identity)
        print(json.dumps(view, indent=2))
    except (OSError, subprocess.SubprocessError, *TIMING_INPUT_ERRORS) as error:
        parser.exit(1, f'Timing inspection failed: {input_error(error)}\n')


if __name__ == '__main__':
    main()
