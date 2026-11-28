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
    from .execution_inputs import committed_project_definition_claims, committed_source_identity, is_source_input
    from .timing_clock import (
        can_order, encode_timestamp, extrema, interval_bounds, ordered_choices, ordered_limit, paired_minima,
        seconds as interval_seconds, sum_intervals, timestamp,
    )
    from .verification_report import (
        RUN_OBSERVATION_CHECKS, RUN_OBSERVATION_POLICIES, passed_observation_checks,
        WINDOWS_LANE_FAILED, WINDOWS_PYTHON_CHECK, WINDOWS_RECOVERY_INTERRUPTED,
        archived_windows_python_issue, archived_windows_python_lanes, failed_check_log_issue, windows_producer_bindings,
        integration_gates, publisher_sequence_issue, publishing_observation_checks, report_observation_issue,
        windows_publisher_status_issue,
    )
    from .verification_scope import (
        LEGACY_WINDOWS_LANES, SCOPED_PROFILES, WINDOWS_LANES, ScopeError, exact_commit,
        decode_publishing_catalog, git, invalid_number, oid, publishing_check_catalog, require_complete_history,
        unique_object,
        windows_lane_checks, windows_lane_report_matches, windows_native_satisfied, windows_published_scope,
        windows_raw_timing_issue, windows_result_issue, windows_x64,
    )
    from .worktree import WorktreeError, entry_info, exclusive_git_lock, git_path, reparse_point
else:
    from doctor import tool_version
    from execution_inputs import committed_project_definition_claims, committed_source_identity, is_source_input
    from timing_clock import (
        can_order, encode_timestamp, extrema, interval_bounds, ordered_choices, ordered_limit, paired_minima,
        seconds as interval_seconds, sum_intervals, timestamp,
    )
    from verification_report import (
        RUN_OBSERVATION_CHECKS, RUN_OBSERVATION_POLICIES, passed_observation_checks,
        WINDOWS_LANE_FAILED, WINDOWS_PYTHON_CHECK, WINDOWS_RECOVERY_INTERRUPTED,
        archived_windows_python_issue, archived_windows_python_lanes, failed_check_log_issue, windows_producer_bindings,
        integration_gates, publisher_sequence_issue, publishing_observation_checks, report_observation_issue,
        windows_publisher_status_issue,
    )
    from verification_scope import (
        LEGACY_WINDOWS_LANES, SCOPED_PROFILES, WINDOWS_LANES, ScopeError, exact_commit,
        decode_publishing_catalog, git, invalid_number, oid, publishing_check_catalog, require_complete_history,
        unique_object,
        windows_lane_checks, windows_lane_report_matches, windows_native_satisfied, windows_published_scope,
        windows_raw_timing_issue, windows_result_issue, windows_x64,
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
LEGACY_RECORDING_SPELLING = re.compile(
    r'[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}:[0-9]{2}(?:[.,]([0-9]+))?(?:Z|[+-][0-9]{2}:[0-9]{2})'
)
SPLIT_SECONDS = ('initial_queue', 'aggregate_setup')
WORKFLOW_SECONDS = ('total_elapsed_seconds', 'runner_work_seconds')
CACHE_STATES = {'cold', 'partial', 'warm', 'not_requested', 'not_restored'}
NORMAL_EVENTS = {'pull_request', 'push'}
PLATFORMS = {'ubuntu-latest': 'Linux', 'windows-latest': 'Windows'}
WINDOWS_PRODUCERS = {lane: f'Windows change checks ({lane})' for lane in WINDOWS_LANES}
WINDOWS_PRODUCER_LANES = {name: lane for lane, name in WINDOWS_PRODUCERS.items()}
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
WINDOWS_FULL_STEP = 'Run uv run python scripts/check.py --require-windows'
WINDOWS_LANE_SELECTOR = 'Select the Windows lane work'
WINDOWS_LANE_STEPS = {f'Verify the Windows {lane} lane': role for lane, role in WINDOWS_PRODUCERS.items()}
WINDOWS_LAYOUT_STEPS = {WINDOWS_AGGREGATE_MARKER: False, WINDOWS_RECOVERY_MARKER: True}
WINDOWS_PROFILE_STEPS = {WINDOWS_FULL_STEP: 'full', **{f'Verify {key} changes': key for key in SCOPED_PROFILES}}
WINDOWS_ROLE_STEPS = {*WINDOWS_LAYOUT_STEPS, *WINDOWS_LANE_STEPS, WINDOWS_LANE_SELECTOR}
WINDOWS_INITIALIZATION_STEP = 'Initialize CI verification reports'
WINDOWS_STAGING_STEP = 'Stage current verification evidence'
HARNESS_STEPS = {
    'Run pnpm check',
    WINDOWS_FULL_STEP,
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
ACTIONS_ORDER_STEPS = {'Set up job': 'setup', 'Complete job': 'cleanup'}
PHASE_FIELDS = {kind: (f'{kind}_start', f'{kind}_end') for kind in STEP_KINDS}
PUBLISHING_BOUNDARY_STEPS = {WINDOWS_INITIALIZATION_STEP, WINDOWS_STAGING_STEP}
OBSERVED_STEPS = (TIMED_STEPS | ACTIONS_ORDER_STEPS.keys() | WINDOWS_ROLE_STEPS
                  | WINDOWS_PROFILE_STEPS.keys() | PUBLISHING_BOUNDARY_STEPS)
PENDING_STEP_STATUSES = {'queued', 'pending', 'waiting'}
EXPECTED_FULL_OMISSIONS = {'not_implemented', 'unsupported_platform'}
WINDOW = 3
MIN_GROWTH_SECONDS = 60
MIN_GROWTH_RATIO = 0.15
LOCAL_BUDGET = 5 * 60
HOSTED_BUDGET = 15 * 60
MAX_RECENT = 12
MAX_COLLECTION_RECEIPTS = 192
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


class UnattributableReport(TimingError):
    """A readable report contradicts its source and cannot enter timing reduction."""


class RawWindowsTiming(dict):
    """A compact projection whose source bytes the timing reader inspected."""

    __slots__ = ('source_digests',)

    def __init__(self, sample, source_digests):
        super().__init__(sample)
        self.source_digests = source_digests


def command(argv):
    result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise TimingError(f'{argv[0]} command failed: {result.stderr.strip()}')
    return result.stdout


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def json_bytes(data):
    return (json.dumps(data, indent=2) + '\n').encode('utf-8')


def decode_timing_input(data):
    return json.loads(data, object_pairs_hook=unique_object, parse_constant=invalid_number)


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


def report_recording_spelling(value):
    if timestamp(value) is None:
        return None
    # Preserve the raw-to-compact spelling used in earlier receipt digests.
    # Reconciliation always compares the exact instant independently.
    match = LEGACY_RECORDING_SPELLING.fullmatch(value)
    return datetime.fromisoformat(value).isoformat() if match and not (match[1] or '')[6:].rstrip('0') else value


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


class GitCommitFacts(NamedTuple):
    tree: str
    parents: tuple[str, ...]


@lru_cache(maxsize=128)
def commit_facts(root, revision):
    """Read the object headers, including parents hidden by Git's shallow traversal."""
    try:
        raw = git(root, 'cat-file', 'commit', revision)
        tree_row, *rows = raw.split(b'\n\n', 1)[0].splitlines()
        if not tree_row.startswith(b'tree '):
            return None
        tree = tree_row[5:].decode('ascii')
        parents = tuple(row[7:].decode('ascii') for row in rows if row.startswith(b'parent '))
        return GitCommitFacts(tree, parents) if all(map(commit_id, (tree, *parents))) else None
    except (ScopeError, OSError, subprocess.SubprocessError, ValueError):
        return None


def event_merge_parents(ci, parents):
    return (len(parents) == 2 and parents[0] != parents[1] and ci['checkout_sha'] not in parents
            and (ci['head_sha'] == parents[1] if ci['event'] == 'pull_request'
                 else ci['event'] == 'push' and ci['head_sha'] == ci['checkout_sha']))


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
        actual = git(root, 'rev-parse', '--verify', 'HEAD').strip().decode('ascii')
    except (ScopeError, OSError, subprocess.SubprocessError, ValueError):
        return None
    facts = commit_facts(root, checkout) if actual == checkout else None
    if not facts or not event_merge_parents(ci, facts.parents) or facts.parents[0] != comparison_base:
        return None
    rustfmt = tool_version('rustfmt', root)
    if not generator_version('rustfmt', rustfmt):
        return None
    return {'checkout': checkout, 'ref': ref, 'tree': facts.tree, 'parents': list(facts.parents),
            'comparison_base': comparison_base,
            'command': GENERATED_COMMAND, 'tools': {'python': python, 'rustfmt': rustfmt}}


def report_coverage(checks, selected, status, profile, platform):
    rows = {item['id']: item for item in checks}
    if (not selected or None in rows or len(rows) != len(checks)
            or len(set(selected)) != len(selected) or not set(selected) <= rows.keys()):
        return False
    omitted = [rows[key] for key in selected if rows[key]['status'] != 'passed']
    expected = profile == 'full' and all(
        item['status'] == 'blocked' and item['reason_code'] in EXPECTED_FULL_OMISSIONS
        and (item['reason_code'] != 'unsupported_platform' or platform != 'Windows')
        for item in omitted
    )
    return (
        any(rows[key]['status'] == 'passed' for key in selected)
        and not any(item['status'] == 'failed'
                    or (item['status'] == 'passed'
                        and (item['id'] not in selected or item['exit_code'] != 0 or item['reason_code'] is not None))
                    for item in checks)
        and (not omitted or expected)
        and (status == 'passed' or (status == 'blocked' and bool(omitted) and expected))
    )


def report_check_work(checks):
    measured = [row['duration_seconds'] for row in checks if row['duration_seconds'] is not None]
    return (round(sum(measured), 3) if measured else None,
            bool(measured) and all(row['duration_seconds'] is not None for row in checks
                                   if row['status'] in {'passed', 'failed'}))


def valid_inputs_digest(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[0-9a-f]{64}', value))


def consistent_hosted_source(initial, final):
    """Check the hosted harness source fence using the fields still present."""
    if not isinstance(final, dict) or any(
            snapshot.get('working_tree_dirty', False) is not False
            or ('sha256' in snapshot and not valid_inputs_digest(snapshot['sha256']))
            for snapshot in (initial, final)):
        return False
    return all(initial[field] == final[field] for field in ('git_head', 'sha256')
               if field in initial and field in final)


def report_sample(report, *, cache=None, original_sha256=None):
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
    platform = short_label(environment.get('system'))
    native_publisher = (platform == 'Windows' and ci and ci['event'] in NORMAL_EVENTS
                        and profile in AUTOMATIC_PROFILES)
    if not (native_publisher or (profile and profile.startswith('ci-windows-'))):
        selected.sort()
    status = short_label(report.get('status'))
    source = report.get('source', {})
    source_unchanged = report.get('source_unchanged')
    if ci and not consistent_hosted_source(source, report.get('source_end', {})):
        # Carry the failed source fence into the compact report's existing
        # identity and coverage fields before it can make a checkout claim.
        source_unchanged = False
    coverage_complete = (
        len(selected) == len(selection.get('checks', []))
        and report_coverage(checks, selected, status, profile, platform)
        and source_unchanged is not False and report.get('project_unchanged') is not False
        and report.get('native_requirement', {}).get('satisfied') is not False
    )
    check_work, check_work_complete = report_check_work(checks)
    recorded_at = report_recording_spelling(report.get('generated_at'))
    source_commit = source.get('git_head')
    inputs_digest = source.get('sha256')
    dirty = source.get('working_tree_dirty')
    identity_complete = (
        commit_id(source_commit) and source_unchanged is True and report.get('project_unchanged') is True
        and (ci is None or (all(ci.values()) and commit_id(ci['head_sha']) and ci['checkout_sha'] == source_commit
                           and positive_id(ci['run_id']) and positive_id(ci['run_attempt'])))
    )
    sample = {
        'kind': SAMPLE_KIND,
        'run_id': short_label(report.get('run_id')),
        'recorded_at': recorded_at,
        'source_commit': source_commit if commit_id(source_commit) else None,
        'inputs_sha256': inputs_digest if valid_inputs_digest(inputs_digest) else None,
        'working_tree_dirty': dirty if isinstance(dirty, bool) else None,
        'profile': profile, 'task_id': short_label(selection.get('task_id')),
        'platform': platform, 'arch': short_label(environment.get('machine')),
        'cache': cache if cache in CACHE_STATES else 'unknown',
        'report_status': status, 'coverage_complete': coverage_complete, 'identity_complete': identity_complete,
        'check_work_complete': check_work_complete,
        'selected': selected, 'checks': checks, 'ci': ci,
        'seconds': {
            'harness': duration(report.get('timing', {}).get('elapsed_seconds')),
            'check_work': check_work,
        },
    }
    split_windows = native_publisher and profile == 'full' and split_windows_report(sample)
    producer = platform == 'Windows' and ci and profile in WINDOWS_PRODUCER_PROFILES
    if split_windows or producer:
        bindings = windows_producer_bindings(report)
        if bindings or original_sha256 is not None:
            lineage = {'checks': {row['id']: report_input_sha256(row) for row in report['checks']
                                  if row['id'] in selected}}
            if bindings:
                lineage['producers'] = bindings
            if producer and original_sha256 is not None:
                lineage['original_sha256'] = original_sha256
            if parts := report.get('windows_python'):
                lineage['parts'] = {lane: report_input_sha256({
                    key: value for key, value in part.items() if key != 'producer_report_sha256'
                }) for lane, part in parts.items()}
            sample['windows_lineage'] = lineage
    return sample


def input_report_sample(report, *, original_sha256=None):
    """Check raw publisher claims before the compact projection can erase them."""
    selection = report.get('selection') or {}
    profile = selection.get('profile')
    platform = report.get('environment', {}).get('system')
    ci = report.get('ci')
    protected = platform in PLATFORMS.values() and ci and ci.get('event') in NORMAL_EVENTS
    automatic = protected and profile in AUTOMATIC_PROFILES
    native = platform == 'Windows' and (automatic or protected and profile in WINDOWS_PRODUCER_PROFILES)
    passed = passed_observation_checks(report['checks']) if automatic or native else set()
    observation_claims = (automatic or native) and (bool(passed) or any(
        'observation' in row or 'field_observation' in row for row in report['checks']
    ))
    publishing, assignment = None, None
    if native or observation_claims:
        source = report.get('source') or {}
        publishing, assignment, _ = native_publisher_contract({
            'ci': ci, 'source_commit': source.get('git_head'), 'inputs_sha256': source.get('sha256'),
            'working_tree_dirty': source.get('working_tree_dirty'),
        }) if commit_id(ci.get('checkout_sha')) and commit_id(ci.get('head_sha')) else (None, None, None)
    revision, catalog = publishing or (None, None)
    publisher = committed_windows_publisher(ROOT, revision) if revision and (native or observation_claims) else None
    if ci and platform in PLATFORMS.values():
        policy = publisher['run_observations'] if publisher else RUN_OBSERVATION_CHECKS
        if issue := report_observation_issue(report, run_observations=policy):
            raise TimingError(f'Timing report has {issue}.')
        if issue := failed_check_log_issue(report):
            raise UnattributableReport(f'Timing report has {issue}.')
        if observation_claims and publisher is None:
            raise TimingError('Timing report observations lack a verified publisher policy.')
        if passed and not passed <= publishing_observation_checks(catalog, policy):
            raise TimingError('Timing report has a passing observation check outside the implemented publishing catalog.')
    if native:
        snapshot = committed_publishing_snapshot(revision, root=ROOT) if revision is not None else None
        expected_source = {**snapshot, 'git_head': ci['checkout_sha']} if snapshot is not None else None
        split_assignment = archived_split_assignment(publisher, assignment, catalog, ci, profile)
        parts = archived_windows_python_lanes(profile) if split_assignment is not None else ()
        if issue := windows_raw_timing_issue(report, catalog=catalog, source=expected_source,
                                            version='2.1' if parts else '2.0'):
            raise TimingError(f'Timing report has {issue}.')
        if (timestamp(report.get('generated_at')) is None
                or duration(report.get('timing', {}).get('elapsed_seconds')) is None):
            raise TimingError('Invalid native harness report timing.')
        if publisher and publisher.get('split_recovery_checks') is not None:
            bindings = ('browser',) if split_assignment is not None else ('browser', 'recovery')
            retained = {lane for lane in bindings if profile == 'full'
                        and assignment[lane].intersection(selection['checks'])}
            if issue := archived_windows_python_issue(report, parts, retained, catalog):
                raise TimingError(f'Timing report has {issue}.')
            bindings = windows_producer_bindings(report)
            if len(bindings) != len(set(bindings.values())):
                raise UnattributableReport('Timing report has Windows producer bindings that reuse an original report across roles.')
        elif 'windows_python' in report:
            raise TimingError('Timing report has unexpected Windows Python part evidence.')
        elif 'windows_producers' in report:
            raise TimingError('Timing report has unexpected Windows producer report bindings.')
        for row in report['checks']:
            reason = row.get('reason_code')
            if reason is not None and not short_label(reason):
                raise TimingError('Invalid native harness result reason code.')
            if ('exit_code' in row and row['exit_code'] is not None and type(row['exit_code']) is not int
                    or 'duration_seconds' in row and duration(row['duration_seconds']) is None):
                raise TimingError('Invalid native harness result measurement.')
            if row['status'] == 'blocked' and reason in {
                'not_selected', 'not_implemented', 'retired', 'earlier_failure', 'executable_unavailable',
                'interrupted_before_start',
            } and {'exit_code', 'log', 'log_sha256'} & row.keys():
                raise TimingError('Unexpected execution fields in a blocked native harness result.')
        definition = (publishing_definition(ROOT, revision)
                      if expected_source is not None and assignment is not None else None)
        if definition is not None:
            selected = selection['checks']
            split_run = windows_lane_report_matches(ci, report['run_id'], report['environment'])
            if profile == 'full' and split_run:
                gates = integration_gates(report['checks'], catalog['required_change_checks'], requested=True)
                if (report.get('integration_gates') != gates
                        or report['integration_gates']['requested'] is not True):
                    raise TimingError('Timing report has integration gates that contradict its check results.')
            lane_failed = profile == 'full' and split_run and WINDOWS_LANE_FAILED in report.get('limitations', ())
            interrupted = WINDOWS_RECOVERY_INTERRUPTED in report.get('limitations', ())
            if interrupted and ('recovery' not in parts or not publisher.get('split_recovery_interruptions')):
                raise TimingError('Timing report has an interruption outside its recovery publishing lane.')
            part_failed = any(report['windows_python'][lane]['status'] != 'passed' for lane in parts)
            scoped = profile in SCOPED_PROFILES
            auxiliary_failure = lane_failed or interrupted or part_failed
            if issue := windows_publisher_status_issue(report['status'], report['checks'], selected, catalog,
                                                       scoped=scoped, auxiliary_failure=auxiliary_failure):
                raise TimingError(f'Timing report has {issue}.')
            if issue := native_publisher_sequence(
                report['checks'], set(selected), profile, split_run, publisher, assignment, split_assignment,
                parts=report.get('windows_python', {}),
            ):
                raise TimingError(f'Timing report has {issue}.')
    sample = report_sample(report, original_sha256=original_sha256)
    if native and (definition is None or any(report[key] != value for key, value in definition.items())):
        # Retained compacts cannot recover fields lost by projection. An
        # unbound raw input must stay diagnostic on archive reopen.
        sample['raw_publisher_unverified'] = True
    if original_sha256 and windows_lineage_role(sample):
        return RawWindowsTiming(sample, {original_sha256})
    return sample


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


def observed_step_rows(steps):
    rows = []
    for step in steps:
        name, number = step.get('name'), step.get('number')
        if name is not None and (not isinstance(name, str) or not name):
            continue
        row = {}
        if type(number) is int and number > 0:
            row['number'] = number
        if name not in OBSERVED_STEPS and not row:
            continue
        if name is not None:
            row['name'] = name
        for field in ('status', 'conclusion'):
            if value := short_label(step.get(field)):
                row[field] = value
        for field in ('started_at', 'completed_at'):
            if (value := timestamp(step.get(field))) is not None:
                row[field] = encode_timestamp(value)
        rows.append(row)
    return distinct_observations(rows)


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


def local_job_order(choices, phase=None):
    order = tuple(choices)
    if can_order(choices, order):
        return order
    # A phase with contradictory endpoints cannot invalidate an independently
    # measured clock. Keep the visible anchors in every phase's projection.
    phase_orders = {kind: tuple(field for field in order if field not in PHASE_FIELDS[other])
                    for kind, other in (('harness', 'browser'), ('browser', 'harness'))}
    if phase:
        return phase_orders[phase]
    viable = [order for order in phase_orders.values() if can_order(choices, order)]
    if len(viable) == 1:
        return viable[0]
    return tuple(field for field in order if all(field not in fields for fields in PHASE_FIELDS.values()))


def scheduled_choices(choices, cutoff, *, prerequisite=False, order=None):
    # At a dependency cut every observed point from job start onward follows
    # its prerequisites, or every point through job finish precedes its dependent.
    order = local_job_order(choices) if order is None else order
    bounded = dict(choices)
    for field in order[1 if prerequisite else 0:]:
        if values := choices.get(field):
            accepted = {point for point in values if point >= cutoff} if prerequisite else {
                point for point in values if point <= cutoff}
            if not accepted:
                return None
            bounded[field] = accepted
    return bounded if can_order(bounded, order) else None


def job_boundary_choices(choices, fields):
    return ordered_choices(choices, fields, within=local_job_order(choices))


def unplaced_phases(choices):
    unplaced = set()
    for part in STEP_KINDS:
        fields = PHASE_FIELDS[part]
        unplaced.update(field for field in fields if choices.get(field, ()) is None)
        if any(choices.get(field) for field in fields) and not can_order(choices, local_job_order(choices, part)):
            unplaced.update(fields)
    return unplaced


def job_intervals(choices):
    intervals = {}
    orders = {phase: local_job_order(choices, phase) for phase in (None, *STEP_KINDS)}
    for metric, endpoints in JOB_INTERVALS.items():
        phase = next((kind for kind in STEP_KINDS if any(field.startswith(kind) for field in endpoints)), None)
        pairs = ordered_choices(choices, endpoints, within=orders[phase])
        intervals[metric] = interval_bounds(pairs)
    return intervals


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
    start, end = timestamp(job.get('started_at')), timestamp(job.get('completed_at'))
    choices = {field: {point} if point is not None else set()
               for field, point in (('requested', requested), ('start', start), ('end', end))}
    rows = observed_step_rows(job.get('steps', ()))
    evidence = reconcile_steps(({'step_observations': rows},))
    # The listed view still exposes its intervals. It applies the same phase
    # selector to its local rows that a reopened review applies to the union.
    choices, chronology, _ = job_time_choices(choices, (), harness_steps, evidence)
    row = {
        'job_id': str(job['id']) if job.get('id') is not None else None,
        'platform': PLATFORMS.get(label), 'runner_label': label,
        'status': job.get('status'), 'conclusion': job.get('conclusion'),
        'identity_issues': {'missing': missing, 'conflicts': conflicts},
        'step_observations': rows,
        **retain_endpoint_choices(choices),
        'seconds': {metric: interval_seconds(bounds) for metric, bounds in
                    job_intervals(chronology).items()},
    }
    if job.get('labels'):
        row['runner_labels'] = sorted(set(job['labels']))
    if job.get('checkout_sha') is not None:
        if commit_id(job['checkout_sha']):
            row['checkout_sha'] = job['checkout_sha']
        else:
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


def split_execution_bounds(parent, sources, active):
    # Every producer must finish before the aggregate starts. For each fixed
    # measured endpoint, the earliest feasible finishes fit together exactly
    # when they all fit the latest feasible aggregate start.
    inactive = [ordered_limit(source, local_job_order(source), 'end')
                for lane, source in sources.items() if lane not in active]
    if None in inactive:
        return None
    running = []
    for lane in active:
        source = sources[lane]
        running.append([
            (harness, ordered_limit({**source, 'harness_start': (harness,)}, local_job_order(source, 'harness'), 'end'))
            for harness in source['harness_start']
        ])

    executions = []
    for end in parent['harness_end']:
        deadline = ordered_limit({**parent, 'harness_end': (end,)}, local_job_order(parent, 'harness'), 'start', latest=True)
        if deadline is None or any(finish > deadline for finish in inactive):
            continue
        eligible = [tuple(harness for harness, finish in lane if finish is not None and finish <= deadline)
                    for lane in running]
        if all(eligible):
            executions.extend((harness, end) for harness in extrema(eligible, min))
    return interval_bounds(executions)


def earliest_job_finish(choices, measured):
    if choices.get('end'):
        return min(measured, default=None)
    return ordered_limit(choices, local_job_order(choices), 'end')


def split_runner_bounds(parent, sources):
    # Condition every producer duration on the same aggregate start. Each
    # producer runs independently once that deadline is fixed.
    pairs = [job_boundary_choices(source, ('start', 'end')) for source in sources.values()]
    parents = job_boundary_choices(parent, ('start', 'end'))
    if not parents or not all(pairs):
        return None
    lanes_by_deadline = {
        deadline: [interval_bounds((start, end) for start, end in lane if end <= deadline) for lane in pairs]
        for deadline in {start for start, _ in parents}
    }
    totals = []
    for start, end in parents:
        parent_duration = interval_bounds(((start, end),))
        bounds = sum_intervals([parent_duration, *lanes_by_deadline[start]])
        if bounds is not None:
            totals.append(bounds)
    return (min(low for low, _ in totals), max(high for _, high in totals)) if totals else None


def split_stage_intervals(parent, members, scoped):
    parent = clock_choices(parent)
    unplaced = unplaced_phases(parent)
    blocked = {stage for stage, endpoints in WINDOWS_PARENT_ENDPOINTS.items() if unplaced.intersection(endpoints)}
    stages = dict.fromkeys(WINDOWS_DERIVED_STAGES)
    if not members:
        return stages, blocked
    sources = {lane: clock_choices(member) for lane, member in members.items()}
    active = ('core',) if scoped else sources
    if any('harness_start' in unplaced_phases(sources[lane]) for lane in active):
        blocked.update(('setup', 'harness_step'))

    starts = {lane: tuple(point for point, in job_boundary_choices(source, ('start',)))
              for lane, source in sources.items()}
    requests = tuple(point for point, in job_boundary_choices(parent, ('requested',)))
    if any(source.get('start') and not starts[lane] for lane, source in sources.items()):
        blocked.update(('initial_queue', 'setup', 'runner'))
    if parent.get('requested') and not requests:
        blocked.add('initial_queue')
    if requests and all(starts.values()):
        queues = []
        for requested in requests:
            eligible = [tuple(value for value in lane if value >= requested) for lane in starts.values()]
            if all(eligible):
                queues.extend((requested, start) for start in extrema(eligible, min))
        stages['initial_queue'] = interval_bounds(queues)
        if not queues:
            blocked.add('initial_queue')

    # Minima may come from the same lane. Preserve start/harness pairings
    # within every lane while combining minima across independent jobs.
    paired = [ordered_choices(sources[lane], ('start', 'harness_start'),
                              within=local_job_order(sources[lane], 'harness'))
              for lane in active]
    inactive_starts = [values for lane, values in starts.items() if lane not in active]
    if all(paired) and all(inactive_starts):
        starts_and_harnesses = paired_minima(paired)
        if inactive_starts:
            starts_and_harnesses = {(min(start, inactive), harness) for start, harness in starts_and_harnesses
                                    for inactive in extrema(inactive_starts, min)}
        stages['setup'] = interval_bounds(starts_and_harnesses)

    ends = {lane: tuple(point for point, in job_boundary_choices(source, ('end',)))
            for lane, source in sources.items()}
    earliest_finishes = [earliest_job_finish(source, ends[lane]) for lane, source in sources.items()]
    parent_starts = {
        point for point, in job_boundary_choices(parent, ('start',))
        if all(finish is not None and finish <= point for finish in earliest_finishes)
    }
    if parent.get('start') and parent.get('harness_start'):
        stages['aggregate_setup'] = interval_bounds(
            (start, harness) for start, harness in ordered_choices(
                parent, ('start', 'harness_start'), within=local_job_order(parent, 'harness')
            ) if start in parent_starts
        )
        if stages['aggregate_setup'] is None:
            blocked.add('aggregate_setup')
    if (parent.get('start') and not parent_starts
            or any(source.get('end') and not ends[lane] for lane, source in sources.items())):
        blocked.update(('queue', 'harness_step', 'runner'))
    if parent_starts and all(ends.values()):
        finish_choices = {start: [tuple(end for end in lane if end <= start) for lane in ends.values()]
                          for start in parent_starts}
        viable = {start: choices for start, choices in finish_choices.items() if all(choices)}
        stages['queue'] = interval_bounds(
            (end, start) for start, choices in viable.items() for end in extrema(choices, max)
        )
        if not viable:
            blocked.update(('queue', 'harness_step', 'runner'))
    if parent.get('harness_end') and all(sources[lane].get('harness_start') for lane in active):
        stages['harness_step'] = split_execution_bounds(parent, sources, active)
        if stages['harness_step'] is None:
            blocked.add('harness_step')
    stages['runner'] = split_runner_bounds(parent, sources)
    return stages, blocked


def windows_clock_lanes(run, *, identified=False):
    if not run or not run['workflow'].get('windows_split', 'sibling_jobs' in run):
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
    """Keep the Actions job assertions and project the same facts used on reopen."""
    require_verification_workflow(run)
    repository = run.get('repository', {}).get('full_name')
    if not repository:
        match = re.fullmatch(r'https://github\.com/([\w.-]+/[\w.-]+)/actions/runs/\d+', run.get('html_url', ''))
        repository = match[1] if match else None
    attempt = str(run['run_attempt']) if run.get('run_attempt') is not None else None
    run_id = str(run['id'])
    requested = timestamp(run.get('created_at')) if attempt == '1' else None
    identity = (run_id, attempt, run.get('head_sha'), repository)
    rows, siblings = [], []
    for job in jobs:
        name = job.get('name')
        protected = re.fullmatch(r'verify(?: task)? \((ubuntu-latest|windows-latest)\)', name or '')
        lane = WINDOWS_PRODUCER_LANES.get(name)
        label = protected[1] if protected else 'windows-latest' if lane else None
        steps = HARNESS_STEPS if protected else TIMED_STEPS - BROWSER_STEPS if not name else ()
        if lane:
            steps = job_harness_steps(('producer', name))
        row = observe_job(job, identity=identity, requested=requested, label=label, harness_steps=steps)
        if protected:
            rows.append(row)
        elif lane and run.get('event') not in NORMAL_EVENTS:
            continue
        else:
            siblings.append({**row, 'name': name})
    workflow = {'matrix_complete': False, 'identity_complete': False,
                'total_elapsed_seconds': None, 'runner_work_seconds': None}
    result = {
        'repository': repository, 'run_id': run_id, 'run_attempt': attempt,
        'head_sha': run.get('head_sha'), 'event': run.get('event'),
        'head_branch': short_label(run.get('head_branch')),
        'status': run.get('status'), 'conclusion': run.get('conclusion'),
        'jobs': sorted(distinct_observations(rows), key=lambda row: row['job_id'] or ''),
        'workflow': workflow, 'job_facts': 1,
    }
    if run.get('checkout_sha') is not None:
        result['checkout_sha'] = run['checkout_sha']
    possible_siblings = [row for row in siblings if can_join_job_claim(row)]
    if possible_siblings:
        result['sibling_jobs'] = sorted(distinct_observations(possible_siblings),
                                        key=lambda row: (row.get('name') or '', row['job_id'] or ''))
    project_listed_clocks(result)
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
    metadata = {'cache', 'generated_contracts', 'windows_caches', 'windows_lineage'}
    core = {key: value for key, value in sample.items() if key not in metadata}
    if (recorded := timestamp(core.get('recorded_at'))) is not None:
        # Use the same instant for reconciliation while keeping original receipt inputs.
        core['recorded_at'] = encode_timestamp(recorded)
    return core


def valid_seconds(seconds, required):
    return isinstance(seconds, dict) and all(
        key in seconds and (seconds[key] is None or duration(seconds[key]) is not None) for key in required
    )


def labels(record, keys):
    return isinstance(record, dict) and all(
        key in record and (record[key] is None or isinstance(record[key], str)) for key in keys
    )


def valid_sample(sample):
    if not labels(sample, ('run_id', 'platform', 'profile', 'arch', 'source_commit', 'recorded_at', 'cache', 'task_id')):
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
    if 'windows_lineage' in sample and not valid_windows_lineage(sample):
        return False
    return not sample['coverage_complete'] or (
        set(selected) <= {check['id'] for check in checks}
        and all(check['status'] != 'passed' or check['exit_code'] == 0 for check in checks)
    )


def valid_windows_lineage(sample):
    lineage = sample['windows_lineage']
    producer = windows_producer_job_name(sample)
    if (not (producer or split_windows_report(sample)) or not isinstance(lineage, dict)
            or not lineage or lineage.keys() - {'original_sha256', 'checks', 'producers', 'parts'}
            or ('original_sha256' in lineage and (not producer or not compact_input_digest(lineage['original_sha256'])))
            or ('producers' in lineage and producer)):
        return False
    for field, keys in (('checks', set(sample['selected'])), ('producers', WINDOWS_PRODUCERS.keys()),
                        ('parts', {'python', 'recovery'})):
        rows = lineage.get(field, {})
        if (not isinstance(rows, dict) or not rows.keys() <= keys
                or not all(map(compact_input_digest, rows.values()))):
            return False
    return True


def valid_timestamp_choices(values):
    if not isinstance(values, list) or not 2 <= len(values) <= MAX_TIMESTAMP_CHOICES:
        return False
    parsed = {timestamp(value) for value in values}
    return None not in parsed and len(parsed) == len(values)


def valid_step_facts(job):
    identities = job.get('step_identities', {})
    disputes = job.get('endpoint_disputes', [])
    choices = job.get('endpoint_choices', {})
    timed = job.get('timed_steps', {})
    legacy_fields = {'step_identities', 'timed_steps', 'step_conclusions', 'step_statuses',
                     'publishing_steps', 'executed_layout_steps'}
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
            and all(valid_step_labels(job.get(field, {})) for field in ('step_conclusions', 'step_statuses'))
            and isinstance(timed, dict) and timed.keys() <= TIMED_STEPS
            and all(valid_timed_step(row) for row in timed.values())
            and ('step_observations' not in job
                 or (isinstance(job['step_observations'], list) and 'endpoints' in job
                     and not job.keys() & legacy_fields
                     and all(valid_step_observation(row) for row in job['step_observations'])))
            and (not (identities or disputes or timed) or 'endpoints' in job))


def valid_step_observation(row):
    if not isinstance(row, dict) or row.keys() - {
            'name', 'number', 'status', 'conclusion', 'started_at', 'completed_at'}:
        return False
    name, number = row.get('name'), row.get('number')
    return (('number' not in row or type(number) is int and number > 0)
            and ('name' not in row or isinstance(name, str) and bool(name))
            and (number is not None or name in OBSERVED_STEPS)
            and all(field not in row or short_label(row[field]) for field in ('status', 'conclusion'))
            and all(field not in row or timestamp(row[field]) is not None for field in ('started_at', 'completed_at')))


def valid_step_labels(labels):
    return (isinstance(labels, dict) and labels.keys() <= OBSERVED_STEPS
            and all(isinstance(values, list) and values and all(short_label(value) is not None for value in values)
                    and len(set(values)) == len(values) for values in labels.values()))


def valid_timed_step(row):
    fields, endpoints = {'start', 'end'}, row['endpoints']
    disputes = row.get('endpoint_disputes', [])
    choices = row.get('endpoint_choices', {})
    number = row['number']
    return ((number is None or type(number) is int and number > 0)
            and isinstance(endpoints, dict) and endpoints.keys() == fields
            and all(value is None or timestamp(value) is not None for value in endpoints.values())
            and isinstance(disputes, list) and len(disputes) <= len(fields) and set(disputes) <= fields
            and isinstance(choices, dict) and choices.keys() <= set(disputes)
            and all(valid_timestamp_choices(values) and endpoints[field] is None for field, values in choices.items()))


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
            and ('job_facts' not in run or type(run['job_facts']) is int and run['job_facts'] == 1)
            and (workflow.get('identity_complete') is None or type(workflow['identity_complete']) is bool)
            and (workflow.get('windows_split') is None or type(workflow['windows_split']) is bool)
            and workflow.get('windows_profile') in AUTOMATIC_PROFILES | {None}
            and all(flag not in workflow or type(workflow[flag]) is bool
                    for flag in ('windows_layout_identified', 'windows_layout_disputed', 'windows_profile_disputed'))
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
                and ('producer_family' not in job or job['producer_family'] is True)
                and (isinstance(job.get('runner_labels', []), list)
                     and all(isinstance(label, str) for label in job.get('runner_labels', ())))
                and (isinstance(job.get('publishing_steps', []), list)
                     and all(isinstance(step, str) and step in WINDOWS_ROLE_STEPS | WINDOWS_PROFILE_STEPS.keys()
                             for step in job.get('publishing_steps', ())))
                and (isinstance(job.get('executed_layout_steps', []), list)
                     and all(isinstance(step, str) and step in WINDOWS_LAYOUT_STEPS
                             and step in job.get('publishing_steps', ())
                             for step in job.get('executed_layout_steps', ())))
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


def attempt_membership(run):
    # This is the normalized matrix. Raw captures are only inputs to it.
    return run['jobs'], run.get('sibling_jobs', ())


def clock_capture_complete(run):
    # A single listing uses the same readiness rule as any retained union.
    evidence = reconcile_timing_clocks([], [run], [])
    return clock_ready_for_capture(evidence, ci_key(run), {}, [])


def successful_clock_jobs(run):
    return run['conclusion'] == 'success' and all(
        job['conclusion'] == 'success' for group in attempt_membership(run) for job in group
    )


def clock_complete(run):
    evidence = reconcile_timing_clocks([], [run], [])
    attempt = ci_key(run)
    return clock_ready_for_capture(evidence, attempt, {}, []) and successful_clock_jobs(evidence.attempts[attempt])


def sample_identity_complete(sample):
    ci = sample['ci']
    return (sample.get('identity_complete') is True and commit_id(sample['source_commit'])
            and short_label(sample['run_id']) is not None and timestamp(sample['recorded_at']) is not None
            and (ci is None or (
                short_label(ci['repository']) and re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', ci['repository'])
                and short_label(ci['event']) and commit_id(ci['head_sha'])
                and positive_id(ci['run_id']) and positive_id(ci['run_attempt'])
                and ci['checkout_sha'] == sample['source_commit']
                and (ci['event'] != 'push' or ci['checkout_sha'] == ci['head_sha'])
            )))


def platform_timing_identity(sample):
    ci = sample['ci']
    return (ci is not None and ci['event'] in NORMAL_EVENTS and ci['run_attempt'] == '1'
            and sample['profile'] in AUTOMATIC_PROFILES and sample['platform'] in PLATFORMS.values()
            and sample_identity_complete(sample))


def sample_eligible(sample):
    return sample['coverage_complete'] and sample_identity_complete(sample) and report_coverage(
        sample['checks'], sample['selected'], sample.get('report_status'), sample['profile'], sample['platform'],
    )


def timing_issue(issues, issue):
    if not any({key: value for key, value in note.items() if key != 'file'} == issue for note in issues):
        issues.append(issue)


def has_job_identity(job):
    return positive_id(job['job_id']) and not any(job['identity_issues'].values())


def has_job_claim(job):
    return not job['identity_issues']['missing'] and can_join_job_claim(job)


def can_join_job_claim(job):
    # These issues concern a fact about an already identified Actions job.
    # Older compact captures used duplicate_job_id for the entire row.
    issues = job['identity_issues']
    return (positive_id(job['job_id'])
            and all(issue in ('duplicate_job_id', 'job_role', 'runner_label', 'checkout_sha')
                    for issue in issues['conflicts']))


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
    runs_by_attempt = defaultdict(list)
    for run in hosted:
        attempt = ci_key(run)
        if commit_id(run.get('checkout_sha')):
            observed[attempt].add(run['checkout_sha'])
        runs_by_attempt[attempt].append(run)
    for attempt, runs in runs_by_attempt.items():
        for claims in bound_job_observations(runs).values():
            outcome, _ = job_claim_outcome(claims)
            protected = any(job['platform'] is not None for _, _, job in claims)
            if outcome != 'skipped' or protected:
                observed[attempt].update(job['checkout_sha'] for _, _, job in claims
                                         if commit_id(job.get('checkout_sha')))
    disputes = {attempt for attempt, claims in observed.items() if known_value(claims)[1]}
    for attempt in sorted(disputes, key=repr):
        timing_issue(issues, {'run_id': attempt[1], 'checkouts': sorted(observed[attempt]),
                              'error': 'Conflicting timing checkouts for this attempt.'})
    return disputes


def named_job_role(name, job_id):
    if name in WINDOWS_PRODUCER_LANES:
        return ('producer', name)
    if name == 'verify (windows-latest)':
        return ('platform', 'Windows')
    return ('sibling', job_id)


def listed_job_role(job, *, protected=False):
    return ('platform', job['platform']) if protected else named_job_role(job.get('name'), job['job_id'])


def merge_clock_fields(rows, fields):
    values, disputes = {}, []
    for field in fields:
        values[field], conflict = known_value(duration(row.get(field)) for row in rows)
        if conflict:
            disputes.append(field)
    return values, disputes


def job_harness_steps(role):
    scope, name = role
    if scope == 'platform':
        return HARNESS_STEPS
    if scope == 'producer':
        lane = WINDOWS_PRODUCER_LANES[name]
        lane_step = {f'Verify the Windows {lane} lane'}
        return lane_step | SCOPED_HARNESS_STEPS if lane == 'core' else lane_step
    return set()


class StepEvidence(NamedTuple):
    conclusions: frozenset[str]
    statuses: frozenset[str]
    identity_disputed: bool
    endpoints: dict
    recorded: bool
    legacy_role: bool
    number: int | None = None

    @property
    def disputed(self):
        return self.identity_disputed or len(self.conclusions) > 1

    @property
    def eligible(self):
        pending = bool(self.statuses) and self.statuses <= PENDING_STEP_STATUSES
        return not self.disputed and 'skipped' not in self.conclusions and not pending

    @property
    def supports_clock(self):
        return self.eligible and any(values is None or values for values in self.endpoints.values())

    @property
    def succeeded(self):
        return self.eligible and self.conclusions == {'success'}


def named_step_clocks(job):
    if 'timed_steps' in job:
        for name, step in job['timed_steps'].items():
            yield [step['number'], name], job_endpoint_choices(step)
        return
    endpoints = job_endpoint_choices(job)
    for kind, identity in job.get('step_identities', {}).items():
        yield identity, {field: endpoints.get(f'{kind}_{field}', ()) for field in ('start', 'end')}


class JobSteps(NamedTuple):
    named: dict[str, StepEvidence]
    visible: dict[int, tuple[str, StepEvidence]]


def step_timestamps(row):
    return {field: {timestamp(row[key])} for field, key in
            (('start', 'started_at'), ('end', 'completed_at')) if key in row}


def joined_step_endpoints(clocks):
    return {field: join_timestamp_choices(row.get(field, ()) for row in clocks) for field in ('start', 'end')}


def visible_step_evidence(rows):
    name, number, observations = rows
    conclusions = frozenset(row['conclusion'] for row in observations if 'conclusion' in row)
    statuses = frozenset(row['status'] for row in observations if 'status' in row)
    endpoints = joined_step_endpoints([step_timestamps(row) for row in observations])
    return name, StepEvidence(conclusions, statuses, False, endpoints, True, False, number)


def reconcile_steps(jobs, *, skipped=False):
    # A job ID has already been established. Join each step's facts before any
    # role, layout or phase can use them. Old captures contribute only the
    # facts that they retained.
    conclusions, statuses = defaultdict(set), defaultdict(set)
    clocks, numbers, names_by_number = defaultdict(list), defaultdict(set), defaultdict(set)
    rows = []
    recorded, legacy_roles = set(), set()
    for job in jobs:
        if 'step_observations' in job:
            rows.extend(job['step_observations'])
            continue
        for name, values in job.get('step_conclusions', {}).items():
            conclusions[name].update(values)
        for name, values in job.get('step_statuses', {}).items():
            statuses[name].update(values)
        for (number, name), endpoints in named_step_clocks(job):
            clocks[name].append(endpoints)
            if number is not None:
                numbers[name].add(number)
                names_by_number[number].add(name)
        if skipped:
            continue
        publishing = set(job.get('publishing_steps', ()))
        executed = set(job.get('executed_layout_steps', ()))
        recorded.update(publishing)
        legacy_success = publishing & (WINDOWS_LANE_STEPS.keys() | {WINDOWS_LANE_SELECTOR})
        for name in executed | legacy_success:
            conclusions[name].add('success')
        if 'step_conclusions' not in job and 'publishing_steps' not in job:
            _, name = job.get('step_identities', {}).get('harness', (None, None))
            if name in WINDOWS_PROFILE_STEPS or name in WINDOWS_LANE_STEPS:
                legacy_roles.add(name)
    for row in rows:
        if 'number' in row and 'name' in row:
            names_by_number[row['number']].add(row['name'])
    visible = defaultdict(list)
    for row in rows:
        number, name = row.get('number'), row.get('name')
        if name is None:
            name, _ = known_value(names_by_number.get(number, ()))
        if number is not None and name is not None and names_by_number[number] == {name}:
            visible[number].append(row)
        if name not in OBSERVED_STEPS:
            continue
        if number is not None:
            numbers[name].add(number)
        if 'conclusion' in row:
            conclusions[name].add(row['conclusion'])
        if 'status' in row:
            statuses[name].add(row['status'])
        if name in WINDOWS_LAYOUT_STEPS:
            recorded.add(name)
        if name in TIMED_STEPS or name in ACTIONS_ORDER_STEPS or name in PUBLISHING_BOUNDARY_STEPS:
            endpoints = step_timestamps(row)
            if endpoints:
                clocks[name].append(endpoints)
    steps = {}
    for name in conclusions.keys() | statuses.keys() | clocks.keys() | numbers.keys() | recorded | legacy_roles:
        disputed = len(numbers[name]) > 1 or any(len(names_by_number[value]) > 1 for value in numbers[name])
        number = next(iter(numbers[name]), None) if not disputed else None
        steps[name] = StepEvidence(frozenset(conclusions[name]), frozenset(statuses[name]), disputed,
                                   joined_step_endpoints(clocks[name]), name in recorded, name in legacy_roles, number)
    return JobSteps(steps, {number: visible_step_evidence((next(iter(names_by_number[number])), number, facts))
                            for number, facts in visible.items()})


def joined_step_clock(primary, kind, names, steps):
    fields = ('start', 'end')
    candidates = [steps[name] for name in names & steps.keys()]
    eligible = [step for step in candidates if step.supports_clock]
    anonymous = [row for job, row in primary
                 if ('step_observations' not in job and 'timed_steps' not in job
                     and kind not in job.get('step_identities', {}))]
    if len(eligible) > 1 or (not eligible and any(step.disputed for step in candidates)):
        return {f'{kind}_{field}': None for field in fields}, None
    selected = eligible[0] if eligible else None
    # An anonymous old endpoint cannot be assigned to a surviving named step
    # if another observation established an ineligible candidate for the phase.
    if any(not step.eligible for step in candidates):
        anonymous = []
    return {
        f'{kind}_{field}': join_timestamp_choices([selected.endpoints[field] if selected else (),
                                                 *(row.get(f'{kind}_{field}', ()) for row in anonymous)])
        for field in fields
    }, selected.number if selected else None


def anchor_limits(order, anchors, *, latest=False):
    """Project the attainable prefix/suffix bound at every position."""
    select = max if latest else min
    edge, limits = None, {}
    for field in reversed(order) if latest else order:
        limits[field] = edge
        if values := anchors.get(field):
            eligible = (point for point in values if edge is None or (point <= edge if latest else point >= edge))
            edge = select(eligible, default=None)
            if edge is None:
                return None
    return limits


def job_time_choices(boundaries, primary, harness_steps, steps):
    choices, groups, anchors = dict(boundaries), {}, {}
    for name, group in ACTIONS_ORDER_STEPS.items():
        groups[group] = fields = (f'{group}_start', f'{group}_end')
        step = steps.named.get(name)
        if step and step.supports_clock:
            anchors.update(zip(fields, step.endpoints.values(), strict=True))

    positions, unpositioned = {}, []
    for kind, names in (('browser', BROWSER_STEPS), ('harness', set(harness_steps))):
        phase, number = joined_step_clock(primary, kind, names, steps.named)
        choices.update(phase)
        if number is not None:
            positions[number] = PHASE_FIELDS[kind]
        elif any(phase.values()):
            unpositioned.extend(PHASE_FIELDS[kind])

    # Numbered observations preserve the order of the actual historical job.
    # Repeated action names (downloads, uploads) have separate positions.
    for number, (name, step) in steps.visible.items():
        if number in positions or name in ACTIONS_ORDER_STEPS or not step.eligible:
            continue
        if name in harness_steps:
            # The publishing step's position remains evidence when its
            # timestamps are missing or its phase measurement is ambiguous.
            positions[number] = ((number, 'start'), (number, 'end'))
            continue
        if name in TIMED_STEPS or not step.supports_clock:
            continue
        positions[number] = fields = ((number, 'start'), (number, 'end'))
        anchors.update(zip(fields, step.endpoints.values(), strict=True))

    ordered = tuple(chain.from_iterable(fields for _, fields in sorted(positions.items())))
    absent = tuple(field for fields in PHASE_FIELDS.values() for field in fields if field not in ordered)
    order = ('requested', 'start', *groups['setup'], *absent, *ordered, *groups['cleanup'], 'end')
    lower, upper = anchor_limits(order, anchors), anchor_limits(order, anchors, latest=True)
    if lower is None or upper is None:
        # A contradictory chain of other steps cannot replace a measured job
        # clock. Retain the independent runner setup/cleanup constraints.
        anchors = {field: time for field, time in anchors.items() if isinstance(field, str)}
        order = tuple(field for field in order if isinstance(field, str))
        lower, upper = anchor_limits(order, anchors) or {}, anchor_limits(order, anchors, latest=True) or {}
    qualified = {}
    for field, values in choices.items():
        if field in unpositioned:
            low, high = unique_endpoint(anchors.get('setup_end')), unique_endpoint(anchors.get('cleanup_start'))
        else:
            low, high = lower.get(field), upper.get(field)
        qualified[field] = ({point for point in values if (low is None or low <= point)
                             and (high is None or point <= high)} or None) if values else values
    chronology = {field: qualified.get(field, anchors.get(field, ())) for field in order}
    if not unpositioned:
        return choices, chronology, None
    # A phase clock without a number can still support an interval. Keep the
    # independent numbered order for publication, which cannot place that phase
    # relative to any other observed steps.
    order = ('requested', 'start', *groups['setup'], *PHASE_FIELDS['browser'], *PHASE_FIELDS['harness'],
             *groups['cleanup'], 'end')
    return (choices, {field: chronology.get(field, ()) for field in order},
            {field: values for field, values in chronology.items() if field not in unpositioned})


def merge_job_clock(jobs, role, steps, *, projected=False):
    primary = [(job, job_endpoint_choices(job)) for job in jobs if 'endpoints' in job]
    legacy = [job['seconds'] for job in jobs if 'endpoints' not in job]
    choices = {field: join_timestamp_choices(row.get(field, ()) for _, row in primary)
               for field in ('requested', 'start', 'end')}
    choices, chronology, positioned = job_time_choices(choices, primary, job_harness_steps(role), steps)
    retained = retain_endpoint_choices(choices)
    endpoint_disputes = frozenset(retained.get('endpoint_disputes', ()))
    seconds, disputes, unplaced = job_clock_measurements(chronology, legacy, endpoint_disputes, projected)
    checkout, _ = known_value(job.get('checkout_sha') for job in jobs)
    representative = max(jobs, key=lambda job: json.dumps(job, sort_keys=True))
    resolved = {key: value for key, value in representative.items()
                if key not in ('step_identities', 'endpoint_disputes', 'endpoint_choices')}
    resolved.update(retained, seconds=seconds)
    if checkout:
        resolved['checkout_sha'] = checkout
    return resolved, disputes, endpoint_disputes, unplaced, chronology, positioned


def job_clock_measurements(choices, legacy, endpoint_disputes, projected):
    unplaced = unplaced_phases(choices)
    derived = job_intervals(choices)
    dependencies = dict(JOB_INTERVALS)
    if projected:
        derived = {'total': derived['total'], 'tail': derived['tail'], 'aggregate_setup': derived['setup']}
        dependencies['aggregate_setup'] = dependencies['setup']
    seconds, disputes = {}, []
    for field in (*ATTEMPT_SECONDS, *SPLIT_SECONDS):
        bounds = derived.get(field)
        measured = interval_seconds(bounds)
        value, disputed = known_value([measured, *(row.get(field) for row in legacy)])
        endpoints = dependencies.get(field, ())
        all_observed = all(choices[endpoint] for endpoint in endpoints)
        if field in derived and (unplaced.intersection(endpoints) or bounds is None and (
                all_observed or any(choices[endpoint] is None for endpoint in endpoints))):
            value = None
        elif disputed or (field in derived and measured is None and (
                bounds is not None or endpoint_disputes.intersection(endpoints))):
            disputes.append(field)
            value = None
        seconds[field] = value
    return seconds, tuple(disputes), unplaced


class JobClockEvidence(NamedTuple):
    job: dict | None
    disputed: tuple
    endpoint_disputes: frozenset = frozenset()
    order_bounds: dict | None = None
    runner_bounds: tuple | None = None
    successful_harness_steps: frozenset[str] = frozenset()
    failed_harness_steps: frozenset[str] = frozenset()
    steps: dict[str, StepEvidence] | None = None
    visible_steps: dict[int, tuple[str, StepEvidence]] | None = None
    positioned_bounds: dict | None = None


def clock_choices(fact):
    return fact.order_bounds if fact.order_bounds is not None else job_endpoint_choices(fact.job)


def windows_dependency_job(observed, attempt, layout):
    if not split_windows_clock(layout) or layout['workflow'].get('windows_layout_disputed'):
        return None
    parent = observed.get((attempt, ('platform', 'Windows')))
    if parent and parent.job:
        # A protected display name identifies a clock candidate. Only an
        # observed dependency check establishes that the job consumes lanes.
        steps = parent.steps or {}
        if any(step.eligible and step.conclusions in ({'success'}, {'failure'})
               for name, step in steps.items() if name in WINDOWS_LAYOUT_STEPS):
            return parent
    return None


def scheduled_job_clocks(observed, attempt, layout):
    parent_key = attempt, ('platform', 'Windows')
    parent = windows_dependency_job(observed, attempt, layout)
    if parent is None:
        return
    choices = clock_choices(parent)
    deadline = ordered_limit(choices, local_job_order(choices), 'start', latest=True)
    if deadline is None:
        return
    finishes = []
    for lane in windows_clock_lanes(layout):
        key = attempt, ('producer', WINDOWS_PRODUCERS[lane])
        fact = observed.get(key)
        if not fact or not fact.job:
            continue
        source = clock_choices(fact)
        finish = ordered_limit(source, local_job_order(source), 'end')
        if finish is not None:
            finishes.append(finish)
        if deadline.is_finite() and (feasible := scheduled_choices(source, deadline)) is not None and feasible != source:
            yield key, feasible
    if finishes and (feasible := scheduled_choices(choices, max(finishes), prerequisite=True)) is not None:
        if feasible != choices:
            yield parent_key, feasible


class JobClaims(NamedTuple):
    observations: list
    roles: set
    producer_family: bool
    steps: set
    role_disputed: bool
    step_evidence: JobSteps
    outcome: tuple[str | None, bool]


def bound_job_observations(runs):
    by_id = defaultdict(list)
    for run in runs:
        for protected, rows in ((True, run['jobs']), (False, run.get('sibling_jobs', ()))):
            for job in rows:
                if can_join_job_claim(job):
                    role = listed_job_role(job, protected=protected) if protected or job.get('name') else None
                    by_id[job['job_id']].append((role, run, job))
    # A sparse row can complete an established job, but cannot establish its
    # own membership without a row bound to this run, attempt and head.
    return {job_id: claims for job_id, claims in by_id.items()
            if any(has_job_claim(job) for _, _, job in claims)}


def job_claim_outcome(claims):
    return known_value(job['conclusion'] for _, _, job in claims)


def publishing_step(name, step):
    if name in WINDOWS_LAYOUT_STEPS:
        # A skipped layout marker can describe the shape after the aggregate
        # was independently identified.
        return step.recorded and not step.disputed
    if not step.eligible:
        return False
    if step.legacy_role and not step.conclusions:
        return True
    if name in WINDOWS_LANE_STEPS or name == WINDOWS_LANE_SELECTOR:
        return step.succeeded
    return name in WINDOWS_PROFILE_STEPS and (step.recorded or bool(step.conclusions))


def job_role_claims(claims, event, issues):
    outcome = job_claim_outcome(claims)
    skipped = outcome[0] == 'skipped'
    joined = reconcile_steps((job for _, _, job in claims), skipped=skipped)
    evidence = joined.named
    disputes = {name: sorted(step.conclusions) for name, step in sorted(evidence.items())
                if len(step.conclusions) > 1}
    if disputes:
        _, run, job = claims[0]
        timing_issue(issues, {'run_id': run['run_id'], 'job_id': job['job_id'], 'step_conclusions': disputes,
                              'error': 'Conflicting conclusions for job steps.'})
    if identities := sorted(name for name, step in evidence.items() if step.identity_disputed):
        _, run, job = claims[0]
        timing_issue(issues, {'run_id': run['run_id'], 'job_id': job['job_id'], 'steps': identities,
                              'error': 'Conflicting number bindings for job steps.'})
    roles = {role for role, _, _ in claims if role}
    if event not in NORMAL_EVENTS or skipped:
        return JobClaims(claims, roles, False, set(), False, joined, outcome)
    steps = {name for name, step in evidence.items() if publishing_step(name, step)}
    executed_layout = any(evidence[name].succeeded for name in steps & WINDOWS_LAYOUT_STEPS.keys())
    labels = {label for _, _, job in claims for label in job.get('runner_labels', ())}
    aggregate_role = ('platform', 'Windows')
    windows = 'windows-latest' in labels or any(role == aggregate_role or role[0] == 'producer'
                                               for role in roles)
    step_roles = {('producer', WINDOWS_LANE_STEPS[step]) for step in steps & WINDOWS_LANE_STEPS.keys()}
    if executed_layout or (windows and WINDOWS_FULL_STEP in steps
                           and evidence[WINDOWS_FULL_STEP].conclusions <= {'success'}):
        step_roles.add(aggregate_role)
    # Older captures retained layout names even when the steps were skipped.
    # Their names describe the layout once other evidence identifies the aggregate.
    if aggregate_role not in roles | step_roles:
        steps.difference_update(WINDOWS_LAYOUT_STEPS)
    producer_family = WINDOWS_LANE_SELECTOR in steps or any(job.get('producer_family') for _, _, job in claims)
    disputed = bool(roles and step_roles - roles)
    return JobClaims(claims, roles or step_roles, producer_family, steps, disputed, joined, outcome)


def disputed_runner_labels(role, claims):
    scope, name = role
    expected = next((label for label, platform in PLATFORMS.items() if platform == name), None)
    if scope == 'producer':
        expected = 'windows-latest'
    observations = [set(job['runner_labels']) for _, _, job in claims if job.get('runner_labels')]
    labels = set().union(*observations) & PLATFORMS.keys()
    return len(labels) > 1 or bool(expected and any(expected not in labels for labels in observations))


def joined_job_claims(by_id, attempt, layout):
    by_role = defaultdict(list)
    for job_id, (claims, roles, producer_family, _, step_conflict, step_evidence, outcome) in by_id.items():
        if not roles:
            if not producer_family:
                continue
            roles = {('sibling', job_id)}
        conclusion, outcome_conflict = outcome
        if all(role[0] == 'sibling' for role in roles) and (not split_windows_clock(layout) or conclusion == 'skipped'):
            continue
        conflicts = {field for _, _, job in claims for field in job['identity_issues']['conflicts']}
        role_conflict = (len(roles) > 1 or step_conflict
                         or bool(conflicts & {'job_role', 'runner_label', 'checkout_sha'}))
        role_conflict |= len({job['name'] for role, _, job in claims if role and role[0] == 'sibling'}) > 1
        role_conflict |= producer_family and not any(role[0] == 'producer' for role in roles)
        status, status_conflict = known_value(job['status'] for _, _, job in claims)
        for role in roles:
            facts = [protected_job_facts(run, layout, job) if role[0] == 'platform' and claim is not None
                     else inherit_job_checkout(run, job) for claim, run, job in claims]
            by_role[attempt, role].append((facts, status, conclusion, role_conflict or status_conflict
                                          or outcome_conflict or disputed_runner_labels(role, claims), step_evidence))
    return by_role


def reconcile_job_clocks(by_id, attempt, layout, issues):
    observed, legacy_measurements = {}, {}
    for key, participants in sorted(joined_job_claims(by_id, attempt, layout).items(), key=lambda item: item[0]):
        _, role = key
        scope, name = role
        issue = {'run_id': attempt[1], scope: name}
        producer = scope == 'producer'
        if len(participants) != 1 or participants[0][3]:
            error = ('Conflicting Windows producer job observations.' if producer
                     else 'Conflicting protected job observations.')
            timing_issue(issues, {**issue, 'error': error})
            observed[key] = JobClockEvidence(None, ())
            continue
        ((jobs, status, conclusion, _, step_facts),) = participants
        projected = role == ('platform', 'Windows') and split_windows_clock(layout)
        job, disputed, endpoint_disputes, unplaced, chronology, positioned = merge_job_clock(
            jobs, role, step_facts, projected=projected)
        steps = step_facts.named
        job.pop('publishing_steps', None)
        job.pop('executed_layout_steps', None)
        job.pop('step_conclusions', None)
        job.pop('step_statuses', None)
        job.pop('timed_steps', None)
        job.pop('step_observations', None)
        job.pop('runner_labels', None)
        job.pop('producer_family', None)
        platform = name if scope == 'platform' else 'Windows' if producer else None
        label = next((label for label, system in PLATFORMS.items() if platform == system), None)
        job.update(platform=platform, runner_label=label)
        if scope == 'platform':
            job.pop('name', None)
        if producer:
            job['name'] = name
        if conclusion == 'skipped':
            job['seconds'] = dict.fromkeys((*ATTEMPT_SECONDS, *SPLIT_SECONDS))
            job.update(endpoints=dict.fromkeys(JOB_ENDPOINTS))
            job.pop('endpoint_choices', None)
            job.pop('endpoint_disputes', None)
            chronology = dict.fromkeys(chronology, ())
            positioned = None
        job.update(status=status, conclusion=conclusion, identity_issues={'missing': [], 'conflicts': []})
        if unplaced:
            timing_issue(issues, {**issue, 'endpoints': sorted(unplaced),
                                  'error': 'Job phase endpoint ordering is unavailable or inconsistent.'})
        if endpoint_disputes:
            issue['endpoints'] = sorted(endpoint_disputes)
        if disputed or endpoint_disputes:
            error = ('Conflicting Windows producer job measurements.' if producer
                     else 'Conflicting protected job measurements.')
            timing_issue(issues, {**issue, 'fields': list(disputed), 'error': error})
        successful_harnesses = frozenset(name for name in job_harness_steps(role) & steps.keys()
                                         if conclusion != 'skipped' and steps[name].succeeded)
        failed_harnesses = frozenset(name for name in job_harness_steps(role) & steps.keys()
                                     if conclusion != 'skipped' and steps[name].eligible
                                     and steps[name].conclusions == {'failure'})
        observed[key] = JobClockEvidence(job, disputed, endpoint_disputes, chronology,
                                         successful_harness_steps=successful_harnesses,
                                         failed_harness_steps=failed_harnesses,
                                         steps=steps, visible_steps=step_facts.visible, positioned_bounds=positioned)
        legacy_measurements[key] = [row['seconds'] for row in jobs if 'endpoints' not in row]
    for key, choices in scheduled_job_clocks(observed, attempt, layout):
        fact = observed[key]
        projected = key[1] == ('platform', 'Windows')
        seconds, disputes, _ = job_clock_measurements(choices, legacy_measurements[key], fact.endpoint_disputes,
                                                     projected)
        observed[key] = fact._replace(job={**fact.job, 'seconds': seconds}, disputed=disputes,
                                      order_bounds=choices)
    return observed


def attempt_layout(runs, by_id, attempt, issues):
    legacy = [row['workflow'] for row in runs if not any(
        'endpoints' in job for job in (*row['jobs'], *row.get('sibling_jobs', ()))
    )]
    workflow, disputed = merge_clock_fields(legacy, WORKFLOW_SECONDS)
    if disputed:
        timing_issue(issues, {'run_id': attempt[1], 'fields': disputed, 'error': 'Conflicting complete attempt clocks.'})
    older = [row for row in runs if not row.get('job_facts') and split_windows_clock(row)]
    roles, widths, scopes = set(), set(), set()
    split = bool(older)
    disputed_profile_step = False
    for claims in by_id.values():
        roles.update(claims.roles)
        split |= claims.producer_family
        widths.update(WINDOWS_LAYOUT_STEPS[step] for step in claims.steps & WINDOWS_LAYOUT_STEPS.keys())
        if ('platform', 'Windows') in claims.roles:
            scopes.update(WINDOWS_PROFILE_STEPS[step] for step in claims.steps & WINDOWS_PROFILE_STEPS.keys())
            named = claims.step_evidence.named
            disputed_profile_step |= any(named[name].identity_disputed
                                         for name in WINDOWS_PROFILE_STEPS.keys() & named.keys())
    has_recovery = ('producer', WINDOWS_PRODUCERS['recovery']) in roles
    split |= bool(widths or any(scope == 'producer' for scope, _ in roles))
    if attempt[3] not in NORMAL_EVENTS:
        return {**workflow, 'windows_split': False}
    old_workflows = [row['workflow'] for row in older]
    widths.update(bool(row.get('windows_recovery_producer')) for row in old_workflows
                  if row.get('windows_layout_identified', row['matrix_complete']))
    if has_recovery:
        widths.add(True)
    recovery, layout_disputed = known_value(widths)
    layout_disputed |= any(row.get('windows_layout_disputed') for row in old_workflows)
    # Early captures retained an observed scope at attempt level. Its explicit
    # value remains a claim alongside newer per-job publishing observations.
    scopes.update(row.get('windows_profile') for row in old_workflows)
    profile, profile_disputed = known_value(scopes)
    profile_disputed |= disputed_profile_step
    profile_disputed |= any(row.get('windows_profile_disputed') for row in old_workflows)
    if layout_disputed:
        timing_issue(issues, {'run_id': attempt[1], 'platform': 'Windows',
                              'error': 'Conflicting Windows job layouts.'})
    if profile_disputed:
        timing_issue(issues, {'run_id': attempt[1], 'platform': 'Windows',
                              'error': 'Conflicting Windows publishing profiles.'})
    return {**workflow, 'windows_split': split, 'windows_layout_identified': recovery is not None and not layout_disputed,
            'windows_recovery_producer': True in widths or any(row.get('windows_recovery_producer')
                                                               for row in old_workflows),
            'windows_profile': profile, 'windows_profile_disputed': profile_disputed,
            'windows_layout_disputed': layout_disputed}


def resolve_attempt(runs, attempt, issues):
    conclusion, outcome_conflict = known_value(row['conclusion'] for row in runs)
    branch, branch_conflict = known_value(row.get('head_branch') for row in runs)
    if outcome_conflict or branch_conflict:
        timing_issue(issues, {'run_id': attempt[1], 'error': 'Conflicting complete attempt clocks.'})
        return None, {}
    status = 'completed' if any(row['status'] == 'completed' for row in runs) else known_value(
        row['status'] for row in runs)[0]
    checkout, _ = known_value(row.get('checkout_sha') for row in runs)
    by_id = {job_id: job_role_claims(claims, attempt[3], issues)
             for job_id, claims in bound_job_observations(runs).items()}
    workflow = attempt_layout(runs, by_id, attempt, issues)
    result = {**dict(zip(ATTEMPT_IDENTITY, attempt, strict=True)), 'head_branch': branch,
              'status': status, 'conclusion': conclusion, 'workflow': workflow}
    if checkout:
        result['checkout_sha'] = checkout
    observed = reconcile_job_clocks(by_id, attempt, result, issues)
    members = {role: fact.job for (_, role), fact in observed.items() if fact.job}
    protected = [job for (scope, _), job in members.items() if scope == 'platform']
    siblings = [job for (scope, _), job in members.items() if scope != 'platform']
    lanes = windows_clock_lanes(result)
    bound = (positive_id(attempt[1]) and positive_id(attempt[2]) and commit_id(attempt[4])
             and len(members) == len(observed)
             and not workflow.get('windows_profile_disputed')
             and {job['platform'] for job in protected} == set(PLATFORMS.values())
             and (not lanes or (workflow['windows_layout_identified'] and has_windows_producers(siblings, lanes))))
    workflow['identity_complete'] = bound
    workflow['matrix_complete'] = bool(bound and conclusion and status == 'completed' and all(
        job['status'] == 'completed' and job['conclusion'] for job in members.values()
    ))
    return {**result, 'jobs': protected, 'sibling_jobs': siblings}, observed


def attempt_clocks(hosted, issues):
    failures, attempts = defaultdict(dict), defaultdict(list)
    for run in hosted:
        key = ci_key(run)
        if run.get('authenticated_failure'):
            failures[key][run['conclusion']] = run
        else:
            attempts[key].append(run)

    clocks, complete, jobs, producer_disputes = {}, set(), {}, set()
    for key in sorted(attempts.keys() | failures.keys(), key=repr):
        authority = failures.get(key, {})
        matching = [run for run in attempts[key] if not authority or set(authority) == {run['conclusion']}]
        if len(matching) != len(attempts[key]):
            timing_issue(issues, {'run_id': key[1], 'authenticated_conclusions': sorted(authority),
                                  'error': 'Attempt clock conflicts with the authenticated workflow conclusion.'})
        if matching:
            clock, observed = resolve_attempt(matching, key, issues)
            if clock:
                clocks[key] = clock
                if clock['workflow']['matrix_complete']:
                    complete.add(key)
                jobs.update(observed)
                if any(role[0] == 'producer' and fact.job is None for (_, role), fact in observed.items()):
                    producer_disputes.add(key)
        if len(authority) == 1:
            clocks.setdefault(key, next(iter(authority.values())))
        elif len(authority) > 1:
            timing_issue(issues, {'run_id': key[1], 'error': 'Conflicting authenticated workflow conclusions.'})
    return ClockEvidence(clocks, complete, jobs, set(), producer_disputes)


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
    if 'endpoints' in job or observed != layout or not projected:
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
    unqualified_reports: frozenset = frozenset()


def project_listed_clocks(run):
    attempt = ci_key(run)
    if timing_checkout_conflicts([], [run], []):
        return
    evidence = attempt_clocks([run], [])
    clock = evidence.attempts[attempt]
    workflow = run['workflow']
    workflow.update({field: value for field, value in clock['workflow'].items() if value is not None})
    if not workflow['windows_split']:
        workflow.pop('windows_recovery_producer', None)
    elif not workflow['windows_recovery_producer']:
        workflow.pop('windows_recovery_producer', None)
    parents = [job for job in run['jobs'] if job['platform'] == 'Windows']
    fact = None
    if split_windows_clock(clock):
        fact = protected_job(evidence, attempt, 'Windows', workflow.get('windows_profile'), [])
        if fact and fact.job and len(parents) == 1:
            parents[0]['seconds'].update(fact.job['seconds'])
    if attempt not in evidence.complete:
        return
    protected, siblings = attempt_membership(clock)
    observed = {source.job['job_id']: clock_choices(source) for source in evidence.jobs.values() if source.job}
    totals = [interval_bounds(job_boundary_choices(observed[job['job_id']], JOB_INTERVALS['total']))
              for job in protected]
    if all(totals):
        workflow['total_elapsed_seconds'] = interval_seconds(extrema(totals, max))
    runners = [interval_bounds(job_boundary_choices(observed[job['job_id']], JOB_INTERVALS['runner']))
               for job in (*protected, *siblings) if fact is None or job['platform'] != 'Windows']
    if fact is not None:
        runners.append(fact.runner_bounds)
    workflow['runner_work_seconds'] = interval_seconds(sum_intervals(runners))


def disputed_stage_input(source, metric, endpoints, bounds):
    return source is not None and (
        source.job is None
        or bool(source.endpoint_disputes.intersection(endpoints)
                - (source.job.get('endpoint_choices', {}).keys() if bounds is not None else set()))
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
    members = sources if have_members else {}
    scoped = profile in SCOPED_PROFILES
    groups = {'all': lanes, 'active': ('core',) if scoped else lanes}
    seconds = dict(fact.job['seconds'])
    derived, blocked = split_stage_intervals(fact, members, scoped)
    disputed = set()
    if 'aggregate_setup' in blocked:
        seconds['aggregate_setup'] = None
    if 'aggregate_setup' in derived:
        bounds = derived['aggregate_setup']
        seconds['aggregate_setup'] = interval_seconds(bounds)
        if bounds is not None and seconds['aggregate_setup'] is None:
            disputed.add('aggregate_setup')
    for stage, inputs in WINDOWS_DERIVED_STAGES.items():
        if stage in blocked:
            seconds[stage] = None
            continue
        bounds = derived[stage]
        measured = interval_seconds(bounds)
        competing_member = any(disputed_stage_input(sources[lane], metric, endpoints, bounds)
                               for group, metric, endpoints in inputs for lane in groups[group])
        value, competing_stage = known_value([seconds[stage], measured])
        parent_dispute = fact.endpoint_disputes & WINDOWS_PARENT_ENDPOINTS[stage]
        if bounds is not None:
            parent_dispute -= fact.job.get('endpoint_choices', {}).keys()
        if (competing_member or competing_stage or parent_dispute or stage in fact.disputed
                or bounds is not None and measured is None):
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
    resolved = blocked | {field for field in ('aggregate_setup',) if field in derived}
    disputes = (*[field for field in fact.disputed if field not in resolved], *sorted(disputed))
    return fact._replace(job={**fact.job, 'seconds': seconds}, disputed=disputes,
                         runner_bounds=derived['runner'] if seconds['runner'] is not None else None)


def protected_profile_matches(run, platform, profile):
    if not run or platform != 'Windows':
        return True
    workflow = run['workflow']
    observed = workflow.get('windows_profile')
    return not workflow.get('windows_profile_disputed') and (profile is None or observed in (None, profile))


def protected_job(evidence, attempt, platform, profile, issues):
    fact = evidence.jobs.get((attempt, ('platform', platform)))
    if fact and fact.job:
        run = evidence.attempts[attempt]
        if not protected_profile_matches(run, platform, profile):
            timing_issue(issues, {'run_id': attempt[1], 'platform': platform,
                                  'error': 'Windows job observations do not match the report publishing profile.'})
            return JobClockEvidence(None, ())
        if platform == 'Windows' and split_windows_clock(run):
            return project_windows_stages(evidence, attempt, run, fact, profile, issues)
    return fact


def reports_split_windows_timing(sample):
    return sample['platform'] == 'Windows' and ('windows_caches' in sample or split_windows_report(sample))


def unconfirmed_split_job(fact):
    # The report identifies a split publisher, but its job listing does not
    # identify all participating lanes. Only aggregate-local facts survive.
    seconds = dict(fact.job['seconds'])
    seconds['aggregate_setup'] = seconds['setup']
    seconds.update(dict.fromkeys((*WINDOWS_DERIVED_STAGES, *WINDOWS_COPIED_STAGES)))
    return {**fact.job, 'seconds': seconds}


def clock_ready_for_capture(evidence, attempt, profiles, issues):
    if attempt not in evidence.complete:
        return False
    run = evidence.attempts[attempt]
    protected, siblings = attempt_membership(run)
    for row in protected:
        platform = row['platform']
        fields = ('total', 'runner')
        if split_windows_clock(run) and row['conclusion'] == 'success':
            fields += ('harness_step',)
        if not observed_fields(protected_job(evidence, attempt, platform, profiles.get(platform), issues), fields):
            return False
    for row in siblings:
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


def missing_independent_job_clock(evidence, attempt):
    run = evidence.attempts.get(attempt)
    roles = [('platform', platform) for platform in PLATFORMS.values()]
    roles.extend(('producer', WINDOWS_PRODUCERS[lane]) for lane in windows_clock_lanes(run))
    return any(not observed_fields(evidence.jobs.get((attempt, role)),
                                  ('total',) if role == ('platform', 'Windows') else ('total', 'runner'))
               for role in roles)


def publishing_phase_bounds(fact, verification, floor, ceiling):
    steps = fact.steps or {}
    initialization = steps.get(WINDOWS_INITIALIZATION_STEP)
    writer = steps.get(verification)
    staging = steps.get(WINDOWS_STAGING_STEP)
    # The historical workflow initializes the report directory, writes in
    # verification, and stages the existing bytes. These relations do not
    # depend on Actions returning a number or a clock for each step.
    before = [steps.get('Set up job'), initialization]
    after = [staging, steps.get('Complete job')]
    if writer and writer.eligible and writer.number is not None:
        first = last = writer.number
    else:
        first = initialization.number if initialization and initialization.eligible else None
        last = staging.number if staging and staging.eligible else None
    for number, (name, step) in (fact.visible_steps or {}).items():
        if name in ACTIONS_ORDER_STEPS or name in PUBLISHING_BOUNDARY_STEPS or name in TIMED_STEPS:
            continue
        if first is not None and number < first:
            before.append(step)
        if (last is not None and number > last
                or name.startswith('Run actions/upload-artifact@')):
            after.append(step)
    lowers = [min(points) for step in before if step and step.eligible
              for points in step.endpoints.values() if points]
    uppers = [max(points) for step in after if step and step.eligible
              for points in step.endpoints.values() if points]
    if writer and writer.eligible:
        def within_job(points):
            return (point for point in points or ()
                    if (floor is None or not floor.is_finite() or not outside_report_bound(floor, point, latest=True))
                    and not outside_report_bound(point, ceiling, latest=True))

        start = min(within_job(writer.endpoints['start']), default=None)
        end = max(within_job(writer.endpoints['end']), default=None)
        # An infeasible writer interval cannot establish either writer bound.
        # An independently placed phase or job endpoint can still constrain
        # publication and supply its own measurement.
        if start is None or end is None or not outside_report_bound(start, end, latest=True):
            lowers.append(start)
            uppers.append(end)
    return lowers, uppers


def outside_report_bound(recorded, bound, *, latest):
    if bound is None or not bound.is_finite():
        return False
    point = int(recorded) if bound == int(bound) else recorded
    return point > bound if latest else point < bound


def job_outside_report(fact, recorded, *, verification=None, dependent=None):
    if verification:
        # Admission uses the raw phase clocks. Interval projection can set a
        # phase clock aside when its precise instant disagrees with a coarse
        # ordering anchor, even though both constrain report creation.
        endpoints = job_endpoint_choices(fact.job)
        choices = {field: endpoints[field] for field in ('requested', 'start', 'end')}
    else:
        measured = clock_choices(fact)
        choices = measured if fact.positioned_bounds is None else {
            field: measured.get(field, values) for field, values in fact.positioned_bounds.items()
        }
    order = local_job_order(choices)
    deadline = None
    if dependent is not None:
        parent = clock_choices(dependent)
        deadline = ordered_limit(parent, local_job_order(parent), 'start', latest=True)
        if deadline is not None and deadline.is_finite():
            choices = scheduled_choices(choices, deadline, order=order)
            if choices is None:
                return True
    if not can_order(choices, order):
        return True

    lower = 'setup_end' if 'setup_end' in order else 'start'
    upper = 'cleanup_start' if 'cleanup_start' in order else 'end'
    lowers = [ordered_limit(choices, order, lower)]
    uppers = [ordered_limit(choices, order, upper, latest=True), deadline]
    if verification:
        phase_lowers, phase_uppers = publishing_phase_bounds(fact, verification, lowers[0], uppers[0])
        lowers.extend(phase_lowers)
        uppers.extend(phase_uppers)
    else:
        uploads = [number for number, (name, step) in (fact.visible_steps or {}).items()
                   if name.startswith('Run actions/upload-artifact@') and step.eligible]
        if uploads:
            uppers.append(ordered_limit(choices, order, (max(uploads), 'end'), latest=True))
    # Actions exposes whole-second bounds while the publisher records
    # fractional seconds. Coarse bounds cover their whole second; precise
    # bounds can be compared directly.
    return (any(outside_report_bound(recorded, bound, latest=False) for bound in lowers)
            or any(outside_report_bound(recorded, bound, latest=True) for bound in uppers))


def report_clock_role(sample):
    ci = sample['ci']
    if not ci:
        return None
    if producer := windows_producer_job_name(sample):
        return ('producer', producer)
    if ci['event'] not in NORMAL_EVENTS or sample['profile'] in AUTOMATIC_PROFILES:
        return ('platform', sample['platform'])
    return None


def report_job_issue(sample, attempt, run, evidence):
    """Check publishing claims against observations of the identified Actions job."""
    if evidence is None:
        return None
    role = report_clock_role(sample)
    if role is None or (role[0] == 'platform'
                        and not protected_profile_matches(run, sample['platform'], sample['profile'])):
        return None
    profile = sample['profile']
    # Scoped split publication forwards the core report. Its original creation
    # predates any work in the job that publishes the protected result.
    forwarded = role == ('platform', 'Windows') and profile in SCOPED_PROFILES and split_windows_clock(run)
    producing_role = ('producer', WINDOWS_PRODUCERS['core']) if forwarded else role
    fact = evidence.jobs.get((attempt, producing_role))
    if not fact or not fact.job or not sample_identity_complete(sample):
        return None
    step = (f'Verify the Windows {WINDOWS_PRODUCER_LANES[role[1]]} lane' if role[0] == 'producer'
            else WINDOWS_FULL_STEP if profile == 'full' else f'Verify {profile} changes')
    windows_publication = (sample['platform'] == 'Windows' and sample['ci']['event'] in NORMAL_EVENTS
                           and (forwarded or role[0] == 'producer'
                                or split_windows_clock(run) and split_windows_report(sample)))
    status = sample.get('report_status')
    parent = (windows_dependency_job(evidence.jobs, attempt, run)
              if producing_role[0] == 'producer'
              and WINDOWS_PRODUCER_LANES[producing_role[1]] in windows_clock_lanes(run) else None)
    if job_outside_report(fact, timestamp(sample['recorded_at']), verification=step if windows_publication else None,
                          dependent=parent):
        error = 'The recorded report creation is outside the publishing job boundaries.'
    elif windows_publication and step in fact.successful_harness_steps and status in ('failed', 'blocked'):
        error = 'The timing report status contradicts the successful verification step.'
    elif windows_publication and step in fact.failed_harness_steps and status == 'passed':
        error = 'The timing report status contradicts the failed verification step.'
    else:
        return None
    return {'run_id': attempt[1], role[0]: role[1], 'job_id': fact.job['job_id'], 'error': error}


def reconcile_timing_clocks(samples, hosted, issues, *, report_issues=None):
    evidence = attempt_clocks(hosted, issues)
    unqualified = unqualified_report_inputs(samples, issues if report_issues is None else report_issues, evidence)
    claims = [row for row in samples if id(row) not in unqualified]
    disputes = timing_checkout_conflicts(claims, hosted, issues)
    return ClockEvidence({key: run for key, run in evidence.attempts.items() if key not in disputes},
                         evidence.complete - disputes,
                         {key: fact for key, fact in evidence.jobs.items() if key[0] not in disputes},
                         disputes, evidence.producer_disputes - disputes,
                         unqualified)


def unqualified_report_inputs(samples, issues, evidence=None):
    diagnostics = []
    unqualified = {id(row) for row in samples if report_attribution_issue(row, evidence, diagnostics)}
    publications = defaultdict(list)
    for row in samples:
        if id(row) not in unqualified and windows_lineage_role(row) and sample_identity_complete(row):
            publications[native_lineage_key(row)].append(row)
    for reports in publications.values():
        for row, issue in windows_lineage_conflicts(reports):
            unqualified.add(id(row))
            diagnostics.append({'run_id': observation_id(row), 'platform': 'Windows', 'error': issue})
    for issue in sorted(diagnostics, key=lambda note: json.dumps(note, sort_keys=True)):
        timing_issue(issues, issue)
    return frozenset(unqualified)


def native_lineage_key(sample):
    # Native split publishing copies results within the shared harness run.
    return (*ci_key(sample['ci']), sample['source_commit'], sample['run_id'])


def windows_lineage_role(sample):
    if producer := windows_producer_job_name(sample):
        return WINDOWS_PRODUCER_LANES[producer]
    if sample['profile'] == 'full' and split_windows_report(sample):
        return 'aggregate'
    return None


def windows_lineage_conflicts(reports):
    originals, compacts = defaultdict(list), defaultdict(list)
    raw_roles = defaultdict(set)
    for row in reports:
        observed = originals if isinstance(row, RawWindowsTiming) else compacts
        role = windows_lineage_role(row)
        observed[role].append(row)
        if isinstance(row, RawWindowsTiming):
            for digest in row.source_digests:
                raw_roles[digest].add(role)
    raw_aggregates = originals.pop('aggregate', ())
    compact_aggregates = compacts.pop('aggregate', ())

    # Only report bytes can establish the original publisher role. Qualify
    # the raw aggregate with those producers before considering compact claims.
    qualified_aggregates = []
    for row in raw_aggregates:
        if issue := windows_report_lineage_issue(row, originals, raw_roles):
            yield row, issue
        else:
            qualified_aggregates.append(row)

    qualified_producers = defaultdict(list, originals)
    for role, rows in compacts.items():
        for row in rows:
            digest = row.get('windows_lineage', {}).get('original_sha256')
            if digest in raw_roles and raw_roles[digest] != {role}:
                yield row, f'The timing report binds the original {role} producer report to a different role.'
                continue
            if role in originals:
                if not any(windows_compact_matches_original(row, original) for original in originals[role]):
                    yield row, f'The timing report differs from the retained original {role} producer results.'
                continue
            if qualified_aggregates:
                compatible = [aggregate for aggregate in qualified_aggregates
                              if windows_aggregate_matches_producer_digest(aggregate, role, digest)]
                if not compatible:
                    yield row, f'The timing report producer digest contradicts the retained original aggregate for {role}.'
                    continue
                if not any(windows_producer_results_match(aggregate, row, role) for aggregate in compatible):
                    yield row, f'The timing report differs from the retained original aggregate results for {role}.'
                    continue
            qualified_producers[role].append(row)

    for row in compact_aggregates:
        sources = originals if qualified_aggregates else qualified_producers
        if issue := windows_report_lineage_issue(row, sources, raw_roles):
            yield row, issue
        elif qualified_aggregates and not any(
                windows_compact_matches_original(row, original) for original in qualified_aggregates):
            yield row, 'The timing report differs from the retained original aggregate results.'


def windows_aggregate_matches_producer_digest(aggregate, role, digest):
    if digest is None:
        return True
    bindings = aggregate.get('windows_lineage', {}).get('producers', {})
    if role in bindings:
        return bindings[role] == digest
    return digest not in bindings.values()


def windows_compact_matches_original(compact, original):
    if sample_core(compact) != sample_core(original):
        return False
    projected = original.get('windows_lineage', {})
    claims = compact.get('windows_lineage', {})
    if 'original_sha256' in claims and claims['original_sha256'] not in original.source_digests:
        return False
    for field in ('checks', 'producers', 'parts'):
        supplied, observed = claims.get(field, {}), projected.get(field, {})
        if any(supplied[key] != observed[key] for key in supplied.keys() & observed.keys()):
            return False
    return True


def windows_report_lineage_issue(sample, producers, raw_roles):
    bindings = sample.get('windows_lineage', {}).get('producers', {})
    for lane, digest in bindings.items():
        if digest in raw_roles and raw_roles[digest] != {lane}:
            return f'The timing report binds the original {lane} producer report to a different role.'
    for lane, records in sorted(producers.items()):
        if not records:
            continue
        expected = bindings.get(lane)
        if expected is not None:
            exact = [row for row in records if isinstance(row, RawWindowsTiming)]
            if exact:
                records = [row for row in exact if expected in row.source_digests]
                if not records:
                    return f'The timing report does not bind the retained original {lane} producer report.'
        if not any(windows_producer_results_match(sample, row, lane) for row in records):
            return f'The timing report differs from the retained original {lane} producer results.'
    return None


def windows_producer_results_match(aggregate, original, lane):
    if aggregate['report_status'] == 'passed' and original['report_status'] in ('failed', 'blocked'):
        return False
    lineage = aggregate.get('windows_lineage', {})
    original_lineage = original.get('windows_lineage', {})
    part = lineage.get('parts', {}).get(lane)
    original_part = original_lineage.get('parts', {}).get(lane)
    if part and original_part and part != original_part:
        return False
    exact, observed = lineage.get('checks', {}), original_lineage.get('checks', {})
    comparable = exact.keys() & observed.keys()
    aggregate_checks = {row['id']: row for row in aggregate['checks']}
    publishing, _, _ = native_publisher_contract(aggregate)
    implemented = {row['id'] for row in publishing[1]['checks'] if row['status'] == 'implemented'} if publishing else set()
    owned = implemented.intersection(original['selected'])
    return all(aggregate_checks.get(row['id']) == row and (
        row['id'] not in comparable or exact[row['id']] == observed[row['id']]
    ) for row in original['checks'] if row['id'] in owned)


def coalesced_sample_groups(samples, issues, *, evidence=None):
    observations, producers = defaultdict(list), defaultdict(list)
    clock_by_attempt = evidence.attempts if evidence is not None else {}
    unqualified = (evidence.unqualified_reports if evidence is not None
                   else unqualified_report_inputs(samples, issues))
    diagnostics = []
    for sample in samples:
        ci = sample['ci']
        attempt = ci_key(ci or {})
        scope = windows_producer_job_name(sample)
        if id(sample) in unqualified:
            scope = ('unqualified report', sample['profile'], sample['run_id'], sample['arch'], tuple(sample['selected']))
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


def coalesce_samples(samples, issues):
    return [resolved for _, resolved in coalesced_sample_groups(samples, issues)]


def coalesce_sample_group(group, producers, run, issues):
    originals = [row for row in group if isinstance(row, RawWindowsTiming) and sample_identity_complete(row)]
    if originals:
        # Failed reports can still provide independent measurements. Keep
        # disagreements between their raw sources visible to the reducer.
        known = sample_core(originals[0])
        candidates = [*originals, *(row for row in group if not isinstance(row, RawWindowsTiming)
                                    and sample_core(row) == known)]
    else:
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
    observed = git_object_types(root, (revision,))
    if observed == (b'missing',):
        return False, None
    if observed != (object_type,):
        return True, None
    try:
        path = f'{revision}:project/checks.json'
        if int(git(root, 'cat-file', '-s', path)) > COMPACT_LIMIT:
            return True, None
        catalog = decode_publishing_catalog(git(root, 'cat-file', 'blob', path))
        publishing_check_catalog(catalog)
        return True, catalog
    except (ScopeError, OSError, subprocess.SubprocessError, *TIMING_INPUT_ERRORS):
        return True, None


# These paths cover the workflow, report launcher, local publishing imports
# and the schema used to read publisher data. The registry itself is reader
# input. Its content is never consulted by the job projecting a compact report.
WINDOWS_PUBLISHING_PATHS = (
    '.github/workflows/verify.yml',
    'contracts/schemas/project.schema.json',
    'contracts/schemas/protocol.schema.json',
    'pyproject.toml',
    'uv.lock',
    'scripts/check.py',
    'scripts/ci_artifacts.py',
    'scripts/development.py',
    'scripts/diagnostic_archive.py',
    'scripts/doctor.py',
    'scripts/execution_inputs.py',
    'scripts/maintenance.py',
    'scripts/operational_paths.py',
    'scripts/process_tree.py',
    'scripts/processes.py',
    'scripts/project.py',
    'scripts/recovery.py',
    'scripts/retained_failures.py',
    'scripts/review_acceptance.py',
    'scripts/review_observation.py',
    'scripts/review_tree.py',
    'scripts/storage.py',
    'scripts/timing.py',
    'scripts/timing_clock.py',
    'scripts/verification_evidence.py',
    'scripts/verification_report.py',
    'scripts/verification_scope.py',
    'scripts/windows_python.py',
    'scripts/worktree.py',
)
PUBLISHING_REGISTRY = ROOT / 'project/timing-publishers.json'


@lru_cache(maxsize=1)
def windows_publishing_registry():
    registry = read_json(PUBLISHING_REGISTRY)
    if registry['version'] != 1:
        raise ValueError('Unknown native timing publisher registry version.')
    assignments = registry['assignments']
    return {
        digest: {'catalogs': program['catalogs'], 'assignment': assignments[program['assignment']],
                 'run_observations': RUN_OBSERVATION_POLICIES[program['run_observations']],
                 'split_recovery_checks': program.get('split_recovery_checks'),
                 'split_recovery_interruptions': program.get('split_recovery_interruptions', False)}
        for digest, program in registry['programs'].items()
    }


def windows_publishing_closure(root, revision):
    # The canonical tree rows include path, object ID and file mode. The listed
    # paths also bind files that are absent in older publisher layouts.
    rows = git(root, 'ls-tree', '-z', revision, '--', *WINDOWS_PUBLISHING_PATHS)
    return sha256(rows).hexdigest()


def publishing_catalog_digest(catalog):
    # The agreed selection rule permits cadence changes with a matching
    # required-change list. Bind all other catalog fields to observed data.
    invariant = {key: value for key, value in catalog.items() if key != 'required_change_checks'}
    invariant['checks'] = [{key: value for key, value in row.items() if key != 'cadence'}
                           for row in catalog['checks']]
    canonical = json.dumps(invariant, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return sha256(canonical.encode('ascii')).hexdigest()


@lru_cache(maxsize=128)
def committed_windows_publisher(root, revision):
    if not commit_id(revision):
        return None
    try:
        program = windows_publishing_closure(root, revision)
        return windows_publishing_registry().get(program)
    except (ScopeError, OSError, subprocess.SubprocessError, *TIMING_INPUT_ERRORS):
        return None


def committed_windows_assignment(root, revision):
    # Both publication paths call the same compact projection.
    program = committed_windows_publisher(root, revision)
    if program is None:
        return None
    assignment = program['assignment']
    return {lane: set(checks) for lane, checks in assignment.items()} if assignment is not None else None


def publishing_catalog_supported(root, revision, catalog):
    publisher = committed_windows_publisher(root, revision)
    return publisher is None or publishing_catalog_digest(catalog) in publisher['catalogs']


@lru_cache(maxsize=128)
def committed_publishing_snapshot(revision, *, root=ROOT):
    try:
        source = committed_source_identity(root, revision)
        publishing_paths = ('project/checks.json', *WINDOWS_PUBLISHING_PATHS)
        if not all(is_source_input(path, source['exclusions']) for path in publishing_paths):
            return None
        return source
    except (ScopeError, OSError, subprocess.SubprocessError, *TIMING_INPUT_ERRORS):
        return None


def committed_publishing_inputs(revision, *, root=ROOT):
    source = committed_publishing_snapshot(revision, root=root)
    return source['sha256'] if source is not None else None


@lru_cache(maxsize=128)
def publishing_definition(root, revision):
    try:
        return committed_project_definition_claims(root, revision)
    except (ScopeError, OSError, subprocess.SubprocessError, *TIMING_INPUT_ERRORS):
        return None


def known_checkout_matches_event(ci):
    if ci['event'] == 'push':
        return ci['checkout_sha'] == ci['head_sha']
    facts = commit_facts(ROOT, ci['checkout_sha'])
    return bool(facts and event_merge_parents(ci, facts.parents)
                and possible_commit_types(git_object_types(ROOT, facts.parents)))


class PublishingSource(NamedTuple):
    publishing: tuple | None = None
    issue: str | None = None


def publishing_catalog_source(sample):
    digest = sample.get('inputs_sha256')
    if sample.get('working_tree_dirty') is not False or not digest:
        return PublishingSource()
    ci = sample['ci']
    checkout = ci['checkout_sha']
    present, catalog = native_publishing_catalog(checkout)
    if present:
        if catalog is not None and known_checkout_matches_event(ci) and committed_publishing_inputs(checkout) == digest:
            return PublishingSource((checkout, catalog))
        return PublishingSource()
    if ci['event'] != 'pull_request':
        return PublishingSource()
    # An expired PR merge commit can have the same inputs as its exact head,
    # or a retained merge-tree proof. Bind the catalog to the reported digest.
    head = ci['head_sha']
    if issue := missing_pr_checkout_issue(ROOT, checkout, head):
        return PublishingSource(issue=issue)
    _, catalog = native_publishing_catalog(head)
    if catalog is not None and committed_publishing_inputs(head) == digest:
        return PublishingSource((head, catalog))
    proof_issues = []
    proof = generated_contracts_proof(sample, eligible_only=False, ancestry_issues=proof_issues)
    if proof:
        tree = proof['tree']
        _, catalog = native_publishing_catalog(tree, object_type=b'tree')
        if catalog is not None and committed_publishing_inputs(tree) == digest:
            return PublishingSource((tree, catalog))
    return PublishingSource(issue=next(iter(proof_issues), None))


def native_publisher_contract(sample):
    publishing, issue = publishing_catalog_source(sample)
    if publishing and publishing_catalog_supported(ROOT, *publishing):
        return publishing, committed_windows_assignment(ROOT, publishing[0]), None
    return None, None, issue


def archived_split_assignment(publisher, assignment, catalog, ci, profile):
    if (not publisher or publisher.get('split_recovery_checks') is None or not assignment
            or ci['event'] != 'pull_request' or profile in SCOPED_PROFILES):
        return None
    # These archived publishers enabled their parts only for the ordinary discovery command.
    command = ['{python}', '-m', 'unittest', 'discover', '-s', 'tests', '-t', '.',
               '-p', 'test_*.py', '-v', '--durations', '0']
    if not any(row['id'] == WINDOWS_PYTHON_CHECK and row['command'] == command
               and row.get('cadence', 'change') == 'change' for row in catalog['checks']):
        return None
    moved = assignment['browser'].intersection(publisher['split_recovery_checks'])
    return {**assignment, 'browser': assignment['browser'] - moved, 'recovery': assignment['recovery'] | moved}


def native_publisher_sequence(rows, selected, profile, split_run, publisher, assignment, split_assignment,
                              *, parts=None):
    # These historical publishers verified each producer's stop boundary.
    if not publisher or publisher.get('split_recovery_checks') is None:
        return None
    ownership = split_assignment if split_assignment is not None else assignment
    lanes = ('core', *ownership)
    if profile == 'full':
        if not split_run:
            return None
        if split_assignment is not None:
            selected = selected - {WINDOWS_PYTHON_CHECK}
        groups = {lane: windows_lane_checks(selected, lane, lanes, assignment=ownership) for lane in lanes}
    else:
        lane = 'core' if profile in SCOPED_PROFILES else profile.removeprefix('ci-windows-')
        groups = {lane: selected}
    recovery_overlap = split_assignment is not None and bool(publisher['split_recovery_checks'])
    for lane, checks in groups.items():
        if issue := publisher_sequence_issue(
            rows, checks, lane, part=(parts or {}).get(lane), recovery_overlap=recovery_overlap,
            compact=parts is None,
        ):
            return issue
    return None


def windows_full_summary_issue(sample, selected, catalog, platform):
    if sample['profile'] != 'full':
        return None
    if not windows_native_satisfied(platform, sample['checks']):
        if sample['coverage_complete'] or sample.get('report_status') == 'passed':
            return 'complete coverage without a passed Windows native build'
        return None
    if sample.get('report_status') != 'blocked':
        return None
    # With the native check passed, an unmet implemented check or integration
    # gate must independently account for the blocked outcome.
    required = {row['id'] for row in catalog['checks'] if row['id'] in selected and row['status'] == 'implemented'}
    required.update(catalog['required_change_checks'])
    if required.issubset(row['id'] for row in sample['checks'] if row['status'] == 'passed'):
        return 'a blocked status that contradicts its completed Windows checks'
    return None


def windows_prerequisite_failure(evidence, attempt, lanes):
    """Use the producer's mandatory verification step and terminal job result."""
    conclusions = set()
    for lane in lanes:
        fact = evidence.jobs.get((attempt, ('producer', WINDOWS_PRODUCERS[lane]))) if evidence else None
        job = fact.job if fact else None
        if fact and f'Verify the Windows {lane} lane' in fact.failed_harness_steps:
            return True
        conclusions.add(job['conclusion'] if job and job['status'] == 'completed' else None)
    if conclusions - {None, 'success'}:
        return True
    return False if conclusions == {'success'} else None


def report_attribution_issue(sample, evidence, issues):
    ci = sample['ci']
    if (ci and ci['event'] == 'pull_request' and sample['platform'] in PLATFORMS.values()
            and report_clock_role(sample) and sample_identity_complete(sample)
            and (issue := pr_checkout_issue(ci))):
        if sample['platform'] == 'Windows':
            issue = ('Native timing check selection is unknown because the publishing catalog is unavailable. ' + issue)
        diagnostic = {'run_id': observation_id(sample), 'platform': sample['platform'], 'error': issue}
        if producer := windows_producer_job_name(sample):
            diagnostic['producer'] = producer
        timing_issue(issues, diagnostic)
        return True
    attempt = ci_key(ci or {})
    run = evidence.attempts.get(attempt) if evidence is not None else None
    if native_report_issue(sample, run, issues, evidence=evidence):
        return True
    if (ci and ci['event'] in NORMAL_EVENTS and sample['platform'] in PLATFORMS.values()
            and report_clock_role(sample) and sample_identity_complete(sample)
            and (passed := passed_observation_checks(sample['checks']))):
        publishing, _, _ = native_publisher_contract(sample)
        publisher = committed_windows_publisher(ROOT, publishing[0]) if publishing else None
        if not publisher or not passed <= publishing_observation_checks(publishing[1], publisher['run_observations']):
            timing_issue(issues, {'run_id': observation_id(sample), 'platform': sample['platform'],
                                  'error': 'Timing report observation checks lack a matching implemented publisher policy.'})
            return True
    if issue := report_job_issue(sample, attempt, run, evidence):
        timing_issue(issues, issue)
        return True
    return False


def native_report_issue(sample, run, issues, *, evidence=None):
    ci, profile = sample['ci'], sample['profile']
    producer = windows_producer_job_name(sample)
    if (sample['platform'] != 'Windows' or not ci or ci['event'] not in NORMAL_EVENTS
            or not sample_identity_complete(sample)
            or (not producer and profile not in AUTOMATIC_PROFILES)):
        return False
    lanes = windows_clock_lanes(run)
    split_report = 'windows_caches' in sample or split_windows_report(sample)
    platform = {'system': sample['platform'], 'machine': sample['arch']}
    split_identity = split_windows_report(sample)
    identity = split_identity if producer else windows_x64(platform) and (
        profile != 'full' or not (split_report or lanes) or split_identity
    )
    selected = set(sample['selected'])
    publishing, assignment, source_issue = (native_publisher_contract(sample)
                                            if identity and sample['task_id'] is None else (None, None, None))
    bindings = sample.get('windows_lineage', {}).get('producers', {}).values()
    result_issue = None
    if len(bindings) != len(set(bindings)):
        result_issue = 'Windows producer bindings that reuse an original report across roles'
    elif sample.get('raw_publisher_unverified'):
        publishing, assignment = None, None
    if assignment is not None and (assignment or not (split_report or lanes)) and not result_issue:
        revision, catalog = publishing
        publisher = committed_windows_publisher(ROOT, revision)
        split_assignment = archived_split_assignment(publisher, assignment, catalog, ci, profile)
        expected_lanes = tuple(lane for lane in WINDOWS_LANES if lane == 'core' or lane in assignment) if assignment else ()
        # An incomplete listing can still prove the identity of a producer's job.
        # Compare widths only when the Actions capture actually identified the layout.
        observed_lanes = windows_clock_lanes(run, identified=True)
        matching_producer = bool(expected_lanes) and (not observed_lanes or observed_lanes == expected_lanes)
        if split_assignment is not None and profile == 'ci-windows-python':
            # The Python process has its own recorded part and owns no ordinary result rows.
            scope_matches = not selected and 'python' in expected_lanes
        else:
            scope_matches = windows_published_scope(sample['selected'], profile, expected_lanes, catalog,
                                                    assignment=split_assignment or assignment)
        if (sample['selected'] == sorted(selected) and (not producer or matching_producer)
                and scope_matches):
            # Compacts omit part outcomes and the aggregate's independent lane
            # verdict. A recorded failure can depend on that omitted evidence.
            auxiliary_failure = False
            if profile == 'full' and split_identity:
                auxiliary_failure = windows_prerequisite_failure(evidence, ci_key(ci), expected_lanes)
            elif split_assignment is not None and archived_windows_python_lanes(profile):
                auxiliary_failure = None
            recovery_interruptions = (split_assignment['recovery'] if split_assignment is not None
                                      and publisher.get('split_recovery_interruptions') else ())
            result_issue = (windows_result_issue(sample['checks'], selected, catalog,
                                                 recovery_interruptions=recovery_interruptions)
                            or native_publisher_sequence(sample['checks'], selected, profile, split_identity,
                                                         publisher, assignment, split_assignment)
                            or windows_full_summary_issue(sample, selected, catalog, platform))
            if not result_issue:
                result_issue = windows_publisher_status_issue(
                    sample.get('report_status'), sample['checks'], selected, catalog,
                    scoped=profile in SCOPED_PROFILES, auxiliary_failure=auxiliary_failure)
            if not result_issue:
                work, work_complete = report_check_work(sample['checks'])
                if sample['seconds']['check_work'] != work or sample.get('check_work_complete') is not work_complete:
                    result_issue = 'a check work summary that contradicts its check results'
            if not result_issue and (not sample['coverage_complete'] or sample_eligible(sample)):
                return False
            if not result_issue:
                result_issue = 'a report summary that contradicts the claimed complete coverage'
    issue = {'run_id': observation_id(sample), 'platform': 'Windows'}
    if producer:
        issue['producer'] = producer
    if source_issue:
        error = ('Native timing check selection is unknown because the publishing catalog is unavailable. '
                 + source_issue)
    elif sample.get('raw_publisher_unverified'):
        error = 'Native raw timing report lacks a verified publisher source or matching project definitions.'
    elif identity and sample['task_id'] is None and not publishing:
        error = ('Native timing check selection is unknown because the publishing catalog is unavailable '
                 'or its execution inputs are unverified.')
    elif publishing and assignment is None:
        error = 'Native timing check selection is unknown because its publishing assignment is unavailable.'
    elif result_issue:
        error = f'Native timing report has {result_issue}.'
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
    for inputs, group in coalesced_sample_groups(samples, issues, evidence=evidence):
        if len(group) > 1:
            continue
        sample = group[0]
        ci = sample['ci']
        attempt = ci_key(ci) if ci else None
        run = evidence.attempts.get(attempt)
        role = report_clock_role(sample)
        producer = role[1] if role and role[0] == 'producer' else None
        fact = None
        if run and role:
            if producer:
                fact = evidence.jobs.get((attempt, role))
            else:
                fact = protected_job(evidence, attempt, sample['platform'], sample['profile'], issues)
        job = fact.job if fact else None
        complete = attempt in evidence.complete
        if run and not producer and reports_split_windows_timing(sample) and not split_windows_clock(run):
            complete = False
            if job:
                job = unconfirmed_split_job(fact)
        unqualified = id(inputs[0]) in evidence.unqualified_reports
        if unqualified:
            job = None
        if job and not sample_identity_complete(sample):
            issue = {'run_id': observation_id(sample), 'platform': sample['platform'],
                     'error': 'Timing sample checkout or identity does not match the retained job evidence.'}
            if producer:
                issue['producer'] = producer
            issues.append(issue)
            job = None
        seconds = {key: sample['seconds'][key] for key in ('harness', 'check_work')}
        if unqualified and seconds['check_work'] != report_check_work(sample['checks'])[0]:
            seconds['check_work'] = None
        seconds.update({key: job['seconds'][key] if job else None for key in ATTEMPT_SECONDS})
        seconds.update({key: duration(job['seconds'].get(key)) if job else None for key in SPLIT_SECONDS})
        cache = 'unknown' if unqualified else sample['cache']
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


@lru_cache(maxsize=128)
def git_object_types(root, revisions):
    names = [revision.encode('ascii') for revision in revisions]
    try:
        types = git(root, 'cat-file', '--batch-check=%(objectname) %(objecttype)',
                    input=b'\n'.join(names) + b'\n').splitlines()
    except (ScopeError, OSError, subprocess.SubprocessError):
        return None
    fields = [row.split() for row in types]
    if len(fields) != len(names) or any(len(parts) != 2 or parts[0] != name for parts, name in zip(fields, names)):
        return None
    return tuple(parts[1] for parts in fields)


def possible_commit_types(types):
    # A locally known noncommit contradicts a parent claim. Shallow or
    # expired objects can be unavailable even when the claim is valid.
    return types is not None and all(kind in {b'commit', b'missing'} for kind in types)


EXPIRED_ANCESTRY_LIMIT = 128


def readable_commit_ancestor(root, descendant, ancestor, *, ancestor_missing):
    """Check readable ancestry, including edges hidden by a shallow checkout."""
    if ancestor_missing:
        missing = missing_commit_parent_edges(root, descendant)
        if missing is not None:
            return ancestor in missing
    # Git before 2.43 cannot enumerate missing commits with rev-list. Raw
    # headers can also follow a local commit hidden behind a shallow edge.
    facts = commit_facts(root, descendant)
    if facts is None:
        return None
    pending, searched = set(facts.parents), set()
    while pending:
        if ancestor in pending:
            return True
        if len(pending) + len(searched) > EXPIRED_ANCESTRY_LIMIT:
            return True if shallow_boundary_names_parent(root, descendant, ancestor) else None
        revisions = tuple(sorted(pending))
        kinds = git_object_types(root, revisions)
        if not possible_commit_types(kinds):
            return None
        searched.update(pending)
        pending.clear()
        for revision, kind in zip(revisions, kinds, strict=True):
            if kind == b'missing':
                continue
            if (commit := commit_facts(root, revision)) is None:
                return None
            pending.update(set(commit.parents) - searched)
    return False


def shallow_boundary_names_parent(root, descendant, ancestor):
    """Prove a positive parent edge without walking all readable commit headers."""
    limit = EXPIRED_ANCESTRY_LIMIT * 43
    try:
        location = git(root, 'rev-parse', '--path-format=absolute', '--git-path', 'shallow', output_limit=4096)
        path = Path(os.fsdecode(location).removesuffix('\n').removesuffix('\r'))
        if not path.is_absolute() or not stat.S_ISREG(path.stat().st_mode):
            return False
        flags = os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0)
        with os.fdopen(os.open(path, flags), 'rb') as contents:
            if not stat.S_ISREG(os.fstat(contents.fileno()).st_mode):
                return False
            raw = contents.read(limit + 1)
        boundaries = raw.decode('ascii').splitlines()
        if (len(raw) > limit or len(boundaries) > EXPIRED_ANCESTRY_LIMIT
                or not all(map(commit_id, boundaries))):
            return False
    except (ScopeError, OSError, subprocess.SubprocessError, ValueError):
        return False
    for boundary in sorted(set(boundaries)):
        facts = commit_facts(root, boundary)
        if facts is None or ancestor not in facts.parents:
            continue
        try:
            # Git can still prove reachability up to its shallow boundary.
            # A raw edge from an unrelated shallow commit proves nothing here.
            merge_base = git(root, 'merge-base', descendant, boundary, output_limit=43)
        except (ScopeError, OSError, subprocess.SubprocessError):
            continue
        if merge_base.splitlines() == [boundary.encode('ascii')]:
            return True
    return False


@lru_cache(maxsize=128)
def missing_commit_parent_edges(root, parent):
    """Walk available history in Git and return the raw edges to missing commits."""
    try:
        # Default Git traversal skips the raw parents of shallow commits.
        # Ask Git to read those edges and print only the missing objects.
        # Each row is '?' and a 40-digit object name, with room for CRLF.
        output = git(root, '-c', 'core.commitGraph=false', '--shallow-file', '', 'rev-list',
                     '--missing=print', '--quiet', parent, '--',
                     output_limit=EXPIRED_ANCESTRY_LIMIT * 43)
        rows = output.splitlines()
        if (len(rows) > EXPIRED_ANCESTRY_LIMIT
                or any(not row.startswith(b'?') or not commit_id(row[1:].decode('ascii')) for row in rows)):
            return None
        missing = tuple(row[1:].decode('ascii') for row in rows)
        if missing and git_object_types(root, missing) != (b'missing',) * len(missing):
            return None
        return frozenset(missing)
    except (ScopeError, OSError, subprocess.SubprocessError, ValueError):
        return None


PR_CHECKOUT_ANCESTOR = 'The claimed PR checkout is a parent or ancestor of the proposed merge.'
PR_CHECKOUT_ANCESTRY_UNVERIFIED = 'Available PR parent ancestry could not be verified within the local Git traversal limits.'


def pr_checkout_issue(ci):
    """Require readable ancestry checks to finish before using a checkout claim."""
    checkout, head = ci['checkout_sha'], ci['head_sha']
    if checkout == head:
        return PR_CHECKOUT_ANCESTOR
    types = git_object_types(ROOT, (checkout, head))
    if types is None:
        return PR_CHECKOUT_ANCESTRY_UNVERIFIED
    checkout_type, head_type = types
    if not possible_commit_types(types):
        return 'The claimed PR checkout or event head identifies a noncommit Git object.'
    if checkout_type == b'missing':
        return missing_pr_checkout_issue(ROOT, checkout, head) if head_type == b'commit' else None
    facts = commit_facts(ROOT, checkout)
    if facts is None:
        return PR_CHECKOUT_ANCESTRY_UNVERIFIED
    if event_merge_parents(ci, facts.parents):
        return None
    head_facts = commit_facts(ROOT, head) if head_type == b'commit' else None
    if head_facts is not None and checkout in head_facts.parents:
        return PR_CHECKOUT_ANCESTOR
    return 'The claimed PR checkout does not have the event head as its second parent.'


def missing_pr_checkout_issue(root, checkout, parent):
    """Contradiction or uncertainty in a claim that an absent object was a merge preview."""
    if checkout == parent:
        return PR_CHECKOUT_ANCESTOR
    parent_type = git_object_types(root, (parent,))
    if parent_type == (b'missing',):
        return None  # A retained proof can supply an independently absent parent.
    if parent_type != (b'commit',):
        return PR_CHECKOUT_ANCESTRY_UNVERIFIED
    ancestry = readable_commit_ancestor(root, parent, checkout, ancestor_missing=True)
    if ancestry is None:
        return PR_CHECKOUT_ANCESTRY_UNVERIFIED
    return PR_CHECKOUT_ANCESTOR if ancestry else None


def expired_pr_merge_tree_issue(root, parents, parent_types, tree):
    incompatible = 'The retained PR merge tree does not agree with its parent evidence.'
    uncertainty = 'Available PR merge-tree ancestry could not be verified within the local Git traversal limits.'
    facts = tuple(commit_facts(root, parent) if kind == b'commit' else None
                  for parent, kind in zip(parents, parent_types))
    if any(kind == b'commit' and fact is None for kind, fact in zip(parent_types, facts)):
        return uncertainty
    # A shallow boundary can hide a direct parent even when its identifier
    # remains available in the descendant's commit header.
    for fact, other in zip(facts, reversed(parents)):
        if fact and other in fact.parents:
            return None if tree == fact.tree else incompatible
    matching_parent_trees = False
    if None not in facts:
        try:
            merge_bases = git(root, 'merge-base', '--all', *parents).split()
            shallow = git(root, 'rev-parse', '--is-shallow-repository').strip()
        except (ScopeError, OSError, subprocess.SubprocessError):
            shallow, merge_bases = None, []
        for fact, other in zip(facts, reversed(parents)):
            if merge_bases == [other.encode('ascii')]:
                return None if tree == fact.tree else incompatible
        matching_parent_trees = len(merge_bases) == 1 and facts[0].tree == facts[1].tree == tree
        if shallow == b'false':
            return None if matching_parent_trees else incompatible
    # Git traversal stops at shallow commits. Their raw headers can still
    # prove ancestry through visible commits and name an unavailable ancestor.
    incomplete = False
    for parent, fact, other, other_type in zip(parents, facts, reversed(parents), reversed(parent_types), strict=True):
        if fact is not None:
            ancestor = readable_commit_ancestor(root, parent, other, ancestor_missing=other_type == b'missing')
            if ancestor is True:
                return None if tree == fact.tree else incompatible
            incomplete |= ancestor is None
    if incomplete:
        return uncertainty
    return None if None in facts or matching_parent_trees else incompatible


def generated_contracts_proof(sample, *, eligible_only=True, ancestry_issues=None):
    ci = sample['ci']
    proof = sample.get('generated_contracts')
    if not ci or not isinstance(proof, dict):
        return None
    parents = proof.get('parents')
    tools = proof.get('tools')
    if ((eligible_only and sample['review_exclusion'] is not None) or sample.get('working_tree_dirty') is not False
            or not sample['arch'] or not sample.get('inputs_sha256')
            or proof.get('checkout') != ci['checkout_sha'] or not commit_id(proof.get('checkout'))
            or not commit_id(proof.get('tree'))
            or not isinstance(parents, list) or not all(map(commit_id, parents))
            or not event_merge_parents(ci, parents)
            or proof.get('comparison_base') != parents[0]
            or not merge_ref(ci['event'], proof.get('ref'))
            or proof.get('command') != GENERATED_COMMAND or not isinstance(tools, dict)
            or not all(generator_version(tool, tools.get(tool)) for tool in GENERATOR_VERSIONS)
            or GENERATED_CHECK not in sample['selected']):
        return None
    types = git_object_types(ROOT, (proof['checkout'], proof['tree'], *parents))
    if (types is None or types[0] not in {b'commit', b'missing'} or types[1] not in {b'tree', b'missing'}
            or not possible_commit_types(types[2:])):
        return None
    if types[0] == b'commit':
        return proof if commit_facts(ROOT, proof['checkout']) == GitCommitFacts(proof['tree'], tuple(parents)) else None
    if ci['event'] == 'pull_request':
        for parent in parents:
            if issue := missing_pr_checkout_issue(ROOT, proof['checkout'], parent):
                if ancestry_issues is not None:
                    ancestry_issues.append(issue)
                return None
        if issue := expired_pr_merge_tree_issue(ROOT, parents, types[2:], proof['tree']):
            if ancestry_issues is not None:
                ancestry_issues.append(issue)
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


def review_exclusion_reason(sample):
    ci, job = sample['ci'], sample['job']
    if windows_producer_job_name(sample):
        return 'Windows producer observations do not establish protected platform coverage.'
    if not sample_eligible(sample):
        return 'Harness report is missing, failed, or has incomplete coverage or identity.'
    if ci and not sample['hosted_identity_complete']:
        return 'Exact Actions attempt and job identities are unavailable or conflicting.'
    if ci and (not sample['hosted_success_complete'] or not job or job['conclusion'] != 'success'):
        return 'A matching successful job and complete successful Actions workflow are required.'
    if ci and (ci['event'] not in NORMAL_EVENTS or ci['run_attempt'] != '1'
               or sample['profile'] not in AUTOMATIC_PROFILES):
        return 'Only first-attempt automatic PR and main profiles enter the hosted review.'
    if not ci and sample['profile'] != 'fast':
        return 'The local timing objective applies to the fast profile.'
    if all(sample['seconds'][key] is None for key in ('harness', 'total', 'runner')):
        return 'Elapsed time is unavailable.'
    return None


def timing_view(samples, *, hosted=(), issues=None):
    if issues is None:
        issues = []
    samples = admitted_records(list(samples), valid_sample, 'timing sample', issues)
    hosted = admitted_records(list(hosted), valid_clock, 'attempt clock', issues)
    merged = sorted(merge_samples(samples, hosted, issues),
                    key=lambda sample: value if (value := timestamp(sample['recorded_at'])) is not None else -1)
    cohorts = defaultdict(list)
    objective_scopes = defaultdict(list)
    for sample in merged:
        ci, job = sample['ci'], sample['job']
        reason = review_exclusion_reason(sample)
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
            and (ci['event'] != 'push' or ci['checkout_sha'] == ci['head_sha'])
            and sample.get('identity_complete') is True and timestamp(sample['recorded_at']) is not None
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
    if not event_merge_parents(ci, parents):
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
            exact_commit(root, checkout)
            facts = commit_facts(root, checkout)
            return facts.parents if facts else None
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


def read_bounded_timing(path, *, manual_archive=False, budget=None, include_raw_digest=False):
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
    data = decode_timing_input(raw)
    if len(raw) > TIMING_READ_LIMIT and (not isinstance(data, dict) or data.get('kind') != ARCHIVE_KIND):
        raise TimingError('Report exceeds the compact timing read limit.')
    if include_raw_digest:
        raw_digest = sha256(raw).hexdigest() if isinstance(data, dict) and 'kind' not in data else None
        return data, raw_digest
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
    # This metadata ties retained projections to previously inspected raw
    # inputs. A standalone compact's publisher claims cannot supply it.
    raw_sources = data.get('windows_raw_sources')
    if isinstance(raw_sources, dict) and raw_sources:
        for index, sample in enumerate(samples):
            role = windows_lineage_role(sample)
            if not role:
                continue
            digests = raw_sources.get(report_input_sha256(sample))
            if isinstance(digests, list) and digests and all(map(compact_input_digest, digests)):
                sources = set(digests)
                if role == 'aggregate' or sources == {sample.get('windows_lineage', {}).get('original_sha256')}:
                    samples[index] = RawWindowsTiming(sample, sources)
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
    evidence = reconcile_timing_clocks(samples, hosted, [], report_issues=issues)
    clocks = evidence.attempts
    # Index ownership needs identity, not derived measurements. Reopening
    # replays the original observations and reports their numeric disputes.
    return [group[0] for inputs, group in coalesced_sample_groups(samples, [], evidence=evidence) if len(group) == 1
            and sample_belongs_to_capture(group[0], path)
            and ci_key(group[0]['ci']) not in evidence.checkout_disputes
            and protected_profile_matches(clocks.get(ci_key(group[0]['ci'])),
                                          group[0]['platform'], group[0]['profile'])
            and id(inputs[0]) not in evidence.unqualified_reports]


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


def add_retained_review_inputs(directories, samples, hosted, reads, issues, budget, *, keys=(), current=()):
    attempts = retained_attempt_paths(directories, samples, chain(hosted, current))
    rows, clocks = linked_capture_inputs(directories, keys, reads, issues, attempts=attempts, budget=budget)
    samples.extend(rows)
    hosted.extend(clocks)
    # An index identifies an attempt, including its originals in other
    # stores and any independently retained workflow conclusion.
    attempts = retained_attempt_paths(directories, samples, chain(hosted, current))
    rows, clocks = retained_capture_inputs(attempts, {}, reads, issues, budget=budget)
    samples.extend(rows)
    hosted.extend(clocks)
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


def retained_budget_inputs(root, feedback=(), *, current=()):
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
                               keys=linked_timing_keys(feedback), current=current)
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
    unique = {}
    for row in rows:
        key = json.dumps(row, sort_keys=True)
        previous = unique.get(key)
        if isinstance(previous, RawWindowsTiming):
            row = (RawWindowsTiming(row, previous.source_digests | row.source_digests)
                   if isinstance(row, RawWindowsTiming) else previous)
        unique[key] = row
    return [unique[key] for key in sorted(unique)]


class CaptureEvidence(NamedTuple):
    samples: list
    clocks: list
    missing: set
    complete_clock: bool
    ambiguous: bool
    checkout_disputed: bool
    producer_disputed: bool
    missing_independent_clock: bool


def capture_observations(identity, samples, hosted, issues, *, conclusion=None):
    samples = [row for row in samples if ci_key(row['ci'] or {}) == identity]
    clocks = distinct_observations(row for row in hosted if ci_key(row) == identity)
    clock_evidence = reconcile_timing_clocks(samples, clocks, issues)
    clock = clock_evidence.attempts.get(identity)
    joined = coalesced_sample_groups(samples, issues, evidence=clock_evidence)
    groups = [group for _, group in joined]
    platforms = [group for inputs, group in joined if platform_timing_identity(group[0])
                 and id(inputs[0]) not in clock_evidence.unqualified_reports]
    disputed_platforms = {group[0]['platform'] for group in platforms if len(group) > 1}
    checkout_disputed = identity in clock_evidence.checkout_disputes
    producer_disputed = identity in clock_evidence.producer_disputes
    ambiguous = checkout_disputed or bool(disputed_platforms) or producer_disputed
    outcome = conclusion or (clock['conclusion'] if clock else None)
    diagnostic = outcome not in {None, 'success'}
    # A failed artifact can still establish its exact diagnostic identity.
    qualify = sample_identity_complete if diagnostic else sample_eligible
    present = {group[0]['platform']: group[0]['profile'] for group in platforms
               if len(group) == 1 and qualify(group[0])}
    missing = set(PLATFORMS.values()) - present.keys() - disputed_platforms
    unconfirmed_split = any(reports_split_windows_timing(group[0]) for group in platforms) and not split_windows_clock(clock)
    complete_clock = (clock_ready_for_capture(clock_evidence, identity, present, issues) and clock['conclusion'] == outcome
                      and not unconfirmed_split and (diagnostic or successful_clock_jobs(clock)))
    # Keep the distinct inputs (including disputed claims). Derived fields
    # are recomputed on read, so archive order cannot suppress a known fact.
    retained = [row for row in samples if sample_identity_complete(row)]
    retained.extend(group[0] for group in groups if not sample_identity_complete(group[0]))
    return CaptureEvidence(distinct_observations(retained), clocks, missing, complete_clock, ambiguous,
                           checkout_disputed, producer_disputed, missing_independent_job_clock(clock_evidence, identity))


def capture_is_complete(identity, samples, hosted):
    evidence = capture_observations(identity, samples, hosted, [])
    return evidence.complete_clock and not evidence.missing and not evidence.ambiguous


def windows_raw_sources(samples):
    digests = defaultdict(set)
    for row in samples:
        if isinstance(row, RawWindowsTiming):
            digests[report_input_sha256(row)].update(row.source_digests)
    return {key: sorted(digests[key]) for key in sorted(digests)}


def write_bounded_archive(path, archive, limit, error, *, sort_keys=False):
    if sources := windows_raw_sources(archive['samples']):
        archive = {**archive, 'windows_raw_sources': sources}
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
    changed = (prior_samples != evidence.samples
               or windows_raw_sources(prior_samples) != windows_raw_sources(evidence.samples)
               or prior_clocks != evidence.clocks)
    resolved_clock = evidence.complete_clock or (evidence.producer_disputed and not evidence.missing_independent_clock)
    if evidence.checkout_disputed or (not evidence.missing and resolved_clock):
        persisted = distinct_observations(notes)
        if changed or prior_issues != persisted:
            save_timing_capture(path, attempts, evidence.samples, evidence.clocks, persisted)
        return ('ambiguous' if evidence.ambiguous else 'retained'), evidence.samples, evidence.clocks, notes
    if attempts >= INTAKE_ATTEMPTS:
        if changed:
            save_timing_capture(path, attempts, evidence.samples, evidence.clocks, notes)
        return 'retry_exhausted', evidence.samples, evidence.clocks, [*notes, {
            'run_id': run_id, 'error': 'Incremental timing retry exhausted. Inspect this run explicitly if more timing is needed.',
        }]

    new_samples, new_clocks, issues = [], [], []
    if evidence.missing:
        try:
            criteria = {'platforms': evidence.missing} if evidence.missing != set(PLATFORMS.values()) else {}
            rows = download_timing(repository, run_id, '1', issues=issues, **criteria)
            new_samples = [
                row for row in admitted_records(rows, valid_sample, 'downloaded timing sample', issues)
                if ci_key(row['ci'] or {}) == identity and row['platform'] in evidence.missing
            ]
        except (OSError, subprocess.SubprocessError, *TIMING_INPUT_ERRORS) as error:
            issues.append({'run_id': run_id, 'error': f'Could not retrieve compact reports: {error}'})
    missing_failure_clock = run['conclusion'] != 'success' and not any(
        row['conclusion'] == run['conclusion'] and not row.get('authenticated_failure') for row in evidence.clocks
    )
    if (not resolved_clock
            and (set(PLATFORMS.values()) - evidence.missing or new_samples or issues or missing_failure_clock)):
        try:
            row = inspect_jobs(run, repository)
            if valid_clock(row) and ci_key(row) == identity:
                new_clocks.append(row)
        except (OSError, subprocess.SubprocessError, *TIMING_INPUT_ERRORS) as error:
            issues.append({'run_id': run_id, 'error': f'Could not retrieve attempt jobs: {error}'})
    captured = capture_observations(
        identity, [*evidence.samples, *new_samples], [*evidence.clocks, *new_clocks], issues, conclusion=run['conclusion'],
    )
    if captured.checkout_disputed or (captured.producer_disputed and not captured.missing):
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
    current = [dict(zip(ATTEMPT_IDENTITY, actions_attempt_identity(repository, run), strict=True))] if run else []
    samples, hosted, issues = retained_budget_inputs(root, feedback, current=current)
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


def download_timing(repository, run_id, attempt, *, platforms=None, issues=None):
    if issues is None:
        issues = []
    endpoint = f'repos/{repository}/actions/runs/{run_id}/artifacts?per_page=100'
    artifacts = decode_timing_input(command(['gh', 'api', endpoint]))['artifacts']
    expected = {f'verification-timing-{label}': platform for label, platform in PLATFORMS.items()
                if platforms is None or platform in platforms}
    names = sorted({item['name'] for item in artifacts if item['name'] in expected and not item['expired']}
                   - {item['name'] for item in artifacts if item['size_in_bytes'] > COMPACT_LIMIT})
    samples = []
    for name in names:
        try:
            # gh extracts a single named artifact directly into its destination.
            with TemporaryDirectory(prefix='scopehub-timing-') as temp:
                command(['gh', 'run', 'download', run_id, '--repo', repository, '--dir', temp, '--name', name])
                path = Path(temp) / TIMING_FILE
                info = entry_info(path)
                if info is None or not stat.S_ISREG(info.st_mode) or reparse_point(info):
                    raise TimingError('Expected the published compact timing file.')
                if info.st_size > COMPACT_LIMIT:
                    raise TimingError('Compact timing artifact exceeds the read limit.')
                sample = read_bounded_timing(path)
                if not valid_sample(sample):
                    raise TimingError('Invalid compact timing sample.')
                if ci_key(sample['ci'] or {})[:3] != (repository, run_id, attempt):
                    raise TimingError('Compact timing report does not match the requested attempt.')
                if sample['platform'] != expected[name]:
                    raise TimingError(f"Timing report platform {sample['platform']} does not match "
                                      f"artifact platform {expected[name]}.")
                samples.append(sample)
        except (OSError, subprocess.SubprocessError, *TIMING_INPUT_ERRORS) as error:
            issues.append({'run_id': run_id, 'file': name, 'error': input_error(error)})
    return samples


def inspect_jobs(run, repository):
    validate_actions_run(run)
    endpoint = f'repos/{repository}/actions/runs/{run["id"]}/attempts/{run["run_attempt"]}/jobs?per_page=100'
    listing = command(['gh', 'api', endpoint])
    if len(listing.encode('utf-8')) > TIMING_READ_LIMIT:
        raise TimingError('Report exceeds the compact timing read limit.')
    return actions_clock(run, decode_timing_input(listing))


def inspect_compact_reports(run, repository, issues):
    run_id = str(run['id'])
    try:
        return download_timing(repository, run_id, str(run['run_attempt']), issues=issues)
    except (OSError, subprocess.SubprocessError, *TIMING_INPUT_ERRORS) as error:
        issues.append({'run_id': run_id, 'source': 'compact_reports', 'error': input_error(error)})
        return []


def inspect_hosted(run_id, attempt, repository, samples, issues):
    endpoint = f'repos/{repository}/actions/runs/{run_id}'
    if attempt:
        endpoint += f'/attempts/{attempt}'
    run = decode_timing_input(command(['gh', 'api', endpoint]))
    if str(run['id']) != str(run_id) or run.get('path') != WORKFLOW:
        raise TimingError('Selected run is outside the repository verification workflow')
    samples.extend(inspect_compact_reports(run, repository, issues))
    return inspect_jobs(run, repository)


def valid_recent_limit(limit):
    return type(limit) is int and 1 <= limit <= MAX_RECENT


def recent_runs(repository, limit):
    if not valid_recent_limit(limit):
        raise TimingError(f'Recent limit must be between 1 and {MAX_RECENT} per event.')
    endpoint = f'repos/{repository}/actions/workflows/verify.yml/runs'
    selected = []
    for event in ('pull_request', 'push'):
        fields = ['-f', 'branch=main'] if event == 'push' else []
        response = decode_timing_input(command(['gh', 'api', '--method', 'GET', endpoint,
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


def timing_input_sha256(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def report_input_sha256(sample):
    """Hash the complete compact input before any projection or reconciliation."""
    return timing_input_sha256(sample)


def platform_report_receipt(sample, input_sha256):
    ci = sample['ci']
    return {**{key: ci[key] for key in (*ATTEMPT_IDENTITY, 'checkout_sha')},
            'platform': sample['platform'], 'report_id': sample['run_id'], 'report_input_sha256': input_sha256}


def compact_input_digest(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[0-9a-f]{64}', value))


def platform_report_receipts(samples):
    for sample in samples:
        if (not isinstance(sample, dict) or sample.get('review_exclusion', False) is not None
                or duration(sample.get('seconds', {}).get('total')) is None
                or sample['profile'] != 'full' or not platform_timing_identity(sample)):
            continue
        for input_sha256 in sample.get('report_inputs_sha256', ()):
            if compact_input_digest(input_sha256):
                yield platform_report_receipt(sample, input_sha256)


def collected_receipts(collection):
    received = collection.get('collected_samples', [])
    if not isinstance(received, list) or len(received) > MAX_COLLECTION_RECEIPTS:
        raise TimingError('Expected a bounded timing collection.')
    return {json.dumps(row, sort_keys=True): row for row in received if isinstance(row, dict)}


def collection_members(collection, field, valid_member):
    values = collection.get(field, [])
    if (isinstance(values, list) and len(values) <= MAX_COLLECTION_RECEIPTS
            and all(isinstance(value, str) and valid_member(value) for value in values)):
        return frozenset(values)
    return None


def qualified_collected_reports(samples, collection):
    """Use only exact acquired inputs still admitted by the ordinary timing review."""
    if not collection or collection.get('collection_order_uncertain') or not commit_id(collection.get('capture_source')):
        return []
    missing = collection_members(collection, 'missing_compact_run_ids', positive_id)
    downloaded = collection_members(collection, 'downloaded_compact_inputs_sha256', compact_input_digest)
    successful = collection.get('runs_with_compact')
    if missing is None or downloaded is None or (
            'runs_with_compact' in collection and (type(successful) is not int or successful <= 0)):
        return []
    keys = collected_receipts(collection).keys()
    if not keys:
        return []
    exact_downloads = 'downloaded_compact_inputs_sha256' in collection
    matches = {}
    for receipt in platform_report_receipts(samples):
        key = json.dumps(receipt, sort_keys=True)
        if (key in keys and receipt['run_id'] not in missing
                and (not exact_downloads or receipt['report_input_sha256'] in downloaded)):
            matches[key] = receipt
    if not exact_downloads and successful is not None and len({row['run_id'] for row in matches.values()}) > successful:
        # A historical count cannot select among competing claimed downloads.
        return []
    return [matches[key] for key in sorted(matches)]


COLLECTION_MEMBERS = {
    'missing_compact_run_ids': positive_id,
    'downloaded_compact_inputs_sha256': compact_input_digest,
    'listed_attempt_jobs_sha256': compact_input_digest,
}
COLLECTION_COUNTS = ('completed_runs_considered', 'runs_with_compact', 'runs_without_compact', 'unsuccessful_runs')


class CollectionObservation(NamedTuple):
    record: dict
    qualified: bool


def collection_facts(record):
    if any(type(record.get(field)) is not int or record[field] < 0 for field in COLLECTION_COUNTS):
        raise TimingError('Timing collection requires nonnegative integer summary counts.')
    if 'limit_per_event' in record and not valid_recent_limit(record['limit_per_event']):
        raise TimingError(f'Timing collection limit must be between 1 and {MAX_RECENT} per event.')
    considered, downloaded, missing, unsuccessful = (record[field] for field in COLLECTION_COUNTS)
    if downloaded + missing != considered or unsuccessful > considered:
        raise TimingError('Timing collection summary counts disagree.')
    point = timestamp(record.get('collected_at'))
    source, empty = record.get('capture_source'), empty_artifact_collection(record)
    if (record.get('collected_at') is not None and point is None or source is not None and not commit_id(source)
            or type(empty) is not bool or empty and downloaded):
        raise TimingError('Invalid timing collection source, time or artifact state.')
    if source is not None:
        source_types = git_object_types(ROOT, (source,))
        if source_types is not None and not possible_commit_types(source_types):
            raise TimingError('Timing collection source is a known noncommit Git object.')
    facts = {field: record[field] for field in COLLECTION_COUNTS}
    if 'limit_per_event' in record:
        facts['limit_per_event'] = record['limit_per_event']
    facts.update(collected_at=encode_timestamp(point), capture_source=source, cold_bootstrap=empty)
    for field, validate in COLLECTION_MEMBERS.items():
        if field in record:
            members = collection_members(record, field, validate)
            if members is None:
                raise TimingError(f'Invalid timing collection membership in {field}.')
            facts[field] = sorted(members)
    if 'missing_compact_run_ids' in facts and len(facts['missing_compact_run_ids']) != missing:
        raise TimingError('Timing collection missing run count disagrees with its recorded membership.')
    return facts


def collection_outcomes(clocks):
    outcomes = defaultdict(set)
    for clock in clocks:
        attempt = ci_key(clock)
        repository, run_id, run_attempt, event, head = attempt
        if (clock.get('status') == 'completed' and clock['conclusion'] is not None
                and repository and event and positive_id(run_id) and positive_id(run_attempt) and commit_id(head)):
            outcomes[attempt].add(clock['conclusion'])
    return outcomes


def collection_acquisitions(facts, samples, clocks, attempt_outcomes):
    report_digests = set(facts.get('downloaded_compact_inputs_sha256', ()))
    listing_digests = set(facts.get('listed_attempt_jobs_sha256', ()))
    reports = {digest: sample for sample in samples if sample['ci'] and report_digests
               and (digest := report_input_sha256(sample)) in report_digests}
    listings = {digest: clock for clock in clocks if listing_digests
                and (digest := timing_input_sha256(clock)) in listing_digests}
    downloaded = {sample['ci']['run_id'] for sample in reports.values()}
    listed = {clock['run_id'] for clock in listings.values()}
    missing = set(facts.get('missing_compact_run_ids', ()))
    missing_reports = len(report_digests - reports.keys())
    downloaded_count = facts['runs_with_compact']
    if (downloaded & missing or len(downloaded | listed | missing) > facts['completed_runs_considered']
            or 'downloaded_compact_inputs_sha256' in facts
            and not len(downloaded) <= downloaded_count <= len(downloaded) + missing_reports):
        raise TimingError('Timing collection counts disagree with its acquisition evidence.')
    attempts = {ci_key(sample['ci']) for sample in reports.values()} | {ci_key(clock) for clock in listings.values()}
    run_events = defaultdict(set)
    outcomes = defaultdict(set)
    for attempt in attempts:
        _, run_id, _, event, _ = attempt
        if event in NORMAL_EVENTS:
            run_events[run_id].add(event)
        outcomes[run_id].update(attempt_outcomes.get(attempt, ()))
    for clock in clocks:
        if clock['run_id'] in missing and clock['event'] in NORMAL_EVENTS:
            run_events[clock['run_id']].add(clock['event'])
    if (limit := facts.get('limit_per_event')) is not None and any(
            sum(events == {event} for events in run_events.values()) > limit for event in NORMAL_EVENTS):
        raise TimingError('Timing collection exceeds its recorded distinct run limit per event.')
    # A downloaded report can bind the same attempt as an independently
    # observed Actions conclusion. Only the collection's own listing can
    # qualify the report for a receipt.
    successful = sum(values == {'success'} for values in outcomes.values())
    unsuccessful = sum(len(values) == 1 and 'success' not in values for values in outcomes.values())
    # Count each run once and leave unavailable or disputed outcomes open.
    if not unsuccessful <= facts['unsuccessful_runs'] <= facts['completed_runs_considered'] - successful:
        raise TimingError('Timing collection unsuccessful run count disagrees with its recorded outcomes.')
    complete = not missing_reports and listing_digests == listings.keys()
    return reports.values(), listings.values(), complete


def observed_collection(record, samples, clocks, attempt_outcomes, issues):
    """Assess the owning acquisition before using a collection or its receipts."""
    facts = collection_facts(record)
    facts['collected_samples'] = []
    if record.get('collection_inputs_unavailable'):
        facts['collection_inputs_unavailable'] = True
    reports, listings, complete = collection_acquisitions(facts, samples, clocks, attempt_outcomes)
    if not commit_id(facts['capture_source']) or not COLLECTION_MEMBERS.keys() <= facts.keys():
        return CollectionObservation(facts, False)
    qualified = complete and not record.get('collection_inputs_unavailable')
    if not qualified:
        # A combined archive also contains other observations' inputs.
        # Their arrival cannot upgrade this collection's earlier acquisition.
        facts['collection_inputs_unavailable'] = True
    claims = collected_receipts(record)
    if facts['collected_at'] is None or not claims:
        return CollectionObservation(facts, qualified)
    report_ids = {claim.get('report_id') for claim in claims.values() if isinstance(claim.get('report_id'), str)}
    acquired, acquired_clocks = defaultdict(list), defaultdict(list)
    for sample in reports:
        if sample['run_id'] in report_ids:
            acquired[ci_key(sample['ci'])].append(sample)
    for clock in listings:
        if (attempt := ci_key(clock)) in acquired:
            acquired_clocks[attempt].append(clock)
    evaluated = []
    for attempt, matching_clocks in acquired_clocks.items():
        try:
            evaluated.extend(dict(sample, review_exclusion=review_exclusion_reason(sample))
                             for sample in merge_samples(acquired[attempt], matching_clocks, []))
        except (OSError, subprocess.SubprocessError, *TIMING_INPUT_ERRORS) as error:
            issues.append({'run_id': attempt[1], 'source': 'timing_qualification', 'error': input_error(error)})
    facts['collected_samples'] = qualified_collected_reports(evaluated, record)
    return CollectionObservation(facts, qualified)


def unresolved_collection(point):
    # The version-one summary reader expects counts even when the latest
    # observation cannot be selected. Zero counts and receipts claim no run.
    return {'collected_at': encode_timestamp(point), 'capture_source': None, 'completed_runs_considered': 0,
            'runs_with_compact': 0, 'runs_without_compact': 0, 'unsuccessful_runs': 0,
            'cold_bootstrap': False, 'collected_samples': []}


def uncertain_collection(point, witnesses=()):
    record = {**unresolved_collection(point), 'collection_order_uncertain': True}
    if witnesses:
        record['collection_order_witnesses'] = list(witnesses)
    return record


def empty_artifact_collection(collection):
    return collection.get('artifact_collection_empty', collection.get('cold_bootstrap', False))


def archive_collections(record, samples, clocks, attempt_outcomes, issues, label):
    if not record:
        return []
    records = [record]
    if record.get('collection_order_uncertain'):
        witnesses = record.get('collection_order_witnesses')
        if witnesses is None:
            # An older summary supplies a visible uncertainty without acquisition evidence.
            return [CollectionObservation(uncertain_collection(timestamp(record.get('collected_at'))), False)]
        if (not isinstance(witnesses, list) or len(witnesses) != 2
                or not all(isinstance(witness, dict) and not witness.get('collection_order_uncertain')
                           for witness in witnesses)):
            issues.append({'file': label, 'source': 'timing_collection', 'error': 'Invalid timing collection witnesses.'})
            return []
        records = witnesses
    observations = []
    for candidate in records:
        try:
            observations.append(observed_collection(candidate, samples, clocks, attempt_outcomes, issues))
        except TimingError as error:
            issues.append({'file': label, 'source': 'timing_collection', 'error': str(error)})
    if record.get('collection_order_uncertain'):
        # A saved disagreement carries independent acquisition witnesses.
        # New attempt evidence can disqualify one without invalidating its peer.
        observations = [item for item in observations if item.qualified]
        if len(observations) != 2:
            return observations
        result = reconcile_collection_order(observations)
        if not result.get('collection_order_uncertain') or result['collected_at'] != record.get('collected_at'):
            issues.append({'file': label, 'source': 'timing_collection',
                           'error': 'Timing collection witnesses do not establish a disagreement.'})
            return []
    return observations


def reconcile_collection_order(observations):
    """Select the latest qualified acquisition and retain the grounds of a tie."""
    qualified = [item.record for item in observations if item.qualified]
    records = qualified or [item.record for item in observations]
    if not records:
        return None
    dated = [point for record in records if (point := timestamp(record['collected_at'])) is not None]
    latest = encode_timestamp(max(dated)) if dated else None
    current = [record for record in records if record['collected_at'] in {latest, None}]
    facts, receipts = {}, defaultdict(dict)
    for record in current:
        selection = {key: value for key, value in record.items() if key != 'collected_samples'}
        key = timing_input_sha256(selection)
        facts[key] = selection
        receipts[key].update(collected_receipts(record))
    if len(facts) == 1:
        key, = facts
        return {**facts[key], 'collected_samples': [row for _, row in sorted(receipts[key].items())]}
    point = timestamp(latest) if all(record['collected_at'] == latest for record in current) else None
    if not qualified:
        return {**unresolved_collection(point), 'collection_inputs_unavailable': True}
    ordered = sorted(facts, key=lambda key: (facts[key]['collected_at'] or '', key))
    witnesses = [
        {**facts[key], 'collected_samples': [row for _, row in sorted(receipts[key].items())]}
        for key in (ordered[0], ordered[-1])
    ]
    return uncertain_collection(point, witnesses)


def qualified_hosted_history(view):
    return any(row['event'] in NORMAL_EVENTS and row['sample_count'] for row in view['objective_reviews'])


def inspect_recent(repository, limit, samples, workflows, issues):
    source = collection_source(ROOT)
    runs = recent_runs(repository, limit)
    starting_issues = len(issues)
    with_compact, without_compact = [], []
    collected_samples, received, listed = [], [], []
    downloaded = set()
    for run in runs:
        run_id = str(run['id'])
        compact = inspect_compact_reports(run, repository, issues)
        (with_compact if compact else without_compact).append(run_id)
        downloaded_inputs = [(sample, report_input_sha256(sample)) for sample in compact]
        downloaded.update(digest for _, digest in downloaded_inputs)
        samples.extend(compact)
        received.extend(compact)
        try:
            clock = inspect_jobs(run, repository)
            workflows.append(clock)
            listed.append(clock)
        except (OSError, subprocess.SubprocessError, *TIMING_INPUT_ERRORS) as error:
            issues.append({'run_id': run_id, 'source': 'attempt_jobs', 'error': input_error(error)})
            continue
        try:
            identity = actions_attempt_identity(repository, run)
            for sample, digest in downloaded_inputs:
                if ci_key(sample['ci']) == identity:
                    collected_samples.append(platform_report_receipt(sample, digest))
        except (OSError, subprocess.SubprocessError, *TIMING_INPUT_ERRORS) as error:
            issues.append({'run_id': run_id, 'source': 'timing_qualification', 'error': input_error(error)})
    record = {'collected_at': datetime.now(timezone.utc).isoformat(),
              'capture_source': source if source and collection_source(ROOT) == source else None,
              'limit_per_event': limit, 'completed_runs_considered': len(runs),
              'runs_with_compact': len(with_compact), 'runs_without_compact': len(without_compact),
              'collected_samples': collected_samples,
              'downloaded_compact_inputs_sha256': sorted(downloaded),
              'listed_attempt_jobs_sha256': sorted({timing_input_sha256(clock) for clock in listed}),
              'missing_compact_run_ids': without_compact,
              'unsuccessful_runs': sum(run.get('conclusion') != 'success' for run in runs),
              'cold_bootstrap': not with_compact and len(issues) == starting_issues}
    return record, received, listed


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
        if recent.get('collection_order_uncertain'):
            lines.append('- Saved captures leave the current collection uncertain.')
        elif recent.get('collection_inputs_unavailable') and recent['capture_source'] is None:
            lines.append('- Acquisition evidence is unavailable for the current collection.')
        elif 'completed_runs_considered' in recent:
            lines.append(f"- Examined {recent['completed_runs_considered']} completed runs; "
                         f"{recent['runs_with_compact']} with compact records, "
                         f"{recent['runs_without_compact']} without them. "
                         f"{recent['unsuccessful_runs']} had unsuccessful workflow outcomes.")
            if not recent['runs_with_compact'] and qualified_hosted_history(view):
                lines.append('- No compact reports were acquired in this collection. '
                             'Retained reports support qualified hosted elapsed measurements.')
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
            isinstance(job, dict) and (job.get('name') is None or isinstance(job['name'], str))
            and (job.get('labels') is None or isinstance(job['labels'], list)
                 and all(isinstance(label, str) for label in job['labels']))
            and isinstance(job.get('steps', []), list)
            and all(isinstance(step, dict) for step in job.get('steps', [])) for job in jobs):
        raise TimingError('Expected a list of Actions job records.')
    return jobs


def actions_clock(run, data):
    clock = hosted_timing(run, actions_jobs(data))
    if not valid_clock(clock):
        raise TimingError('Invalid attempt clock derived from Actions input.')
    return clock


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

    if args.recent is not None and not valid_recent_limit(args.recent):
        parser.error(f'--recent must be between 1 and {MAX_RECENT}')
    if args.github_actions and (args.recent is None or args.run):
        parser.error('--github-actions requires --recent and cannot accompany --run')
    samples, workflows, issues = [], [], []
    collection_inputs = []
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
                data, raw_digest = read_bounded_timing(path, manual_archive=manual, budget=read_budget,
                                                      include_raw_digest=True)
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
                    if collection := data.get('recent_collection'):
                        collection_inputs.append((collection, archive_samples, archive_hosted, path.name))
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
                    sample = input_report_sample(data, original_sha256=raw_digest)
                    if not valid_sample(sample):
                        raise TimingError('Invalid timing sample derived from harness report.')
                    samples.append(sample)
                    if args.report is None and path.name == 'test-report.json' and sample['ci']:
                        path = path.with_name(TIMING_FILE)
                        companion = companion_timing_sample(path, sample, read_budget)
                        if companion is not None:
                            samples.append(companion)
            except UnattributableReport as error:
                issues.append({'file': path.name, 'error': str(error)})
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
                workflows.append(actions_clock(run, read_bounded_timing(jobs_path, budget=read_budget)))
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
                workflows.append(inspect_hosted(run, args.attempt, repository, samples, issues))
        acquisition = (inspect_recent(repository, args.recent, samples, workflows, issues)
                       if args.recent is not None else None)
        if args.report is None:
            add_retained_review_inputs(timing_directories(ROOT, issues), samples, workflows,
                                       read_captures, issues, read_budget)
        attempt_outcomes = collection_outcomes(workflows) if collection_inputs or acquisition else {}
        collection_observations = [
            observation for record, owned_samples, owned_clocks, label in collection_inputs
            for observation in archive_collections(record, owned_samples, owned_clocks, attempt_outcomes, issues, label)
        ]
        recent_observation = (observed_collection(*acquisition, attempt_outcomes, issues).record
                              if acquisition else reconcile_collection_order(collection_observations))
        view = timing_view(samples, hosted=workflows, issues=list(issues))
        recent = None
        if recent_observation is not None:
            recent = {**recent_observation,
                      'collected_samples': qualified_collected_reports(view['samples'], recent_observation)}
        if recent and empty_artifact_collection(recent):
            # Preserve acquisition separately so a future reopen can reevaluate
            # history while older summary readers see the current verdict.
            if qualified_hosted_history(view):
                recent = {**recent, 'cold_bootstrap': False, 'artifact_collection_empty': True}
            else:
                recent = {**recent, 'cold_bootstrap': True}
        tasks = {task['id']: task for task in read_json(ROOT / 'project/tasks.json')['tasks']}
        feedback = read_json(ROOT / 'project/feedback.json')['items']
        view.update(weekly_review=weekly_review(), recent_collection=recent,
                    budget_follow_up=budget_follow_up(view, feedback, tasks))
        summary = review_summary(view) if args.summary else None
        archive_identity = None
        if args.save:
            archive_identity = save_review_archive(
                args.save, {'kind': ARCHIVE_KIND, 'samples': samples, 'hosted': workflows,
                            'recent_collection': recent, 'input_issues': issues})
        if args.summary:
            append_review_summary(args.summary, summary, archive=args.save, archive_identity=archive_identity)
        print(json.dumps(view, indent=2))
    except (OSError, subprocess.SubprocessError, *TIMING_INPUT_ERRORS) as error:
        parser.exit(1, f'Timing inspection failed: {input_error(error)}\n')


if __name__ == '__main__':
    main()
