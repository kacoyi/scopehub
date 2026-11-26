"""Exercise timing distinctions and regression decisions with synthetic observations."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from scripts import ci, ci_artifacts, timing
from tests.project.fixtures import isolated_git_environment


def historical_report():
    return {
        'run_id': '20261120T000000.000000Z-aabbccdd',
        'generated_at': '2026-11-20T00:02:00Z',
        'status': 'blocked',
        'source_unchanged': True,
        'project_unchanged': True,
        'source': {'git_head': 'a' * 40},
        'environment': {'system': 'Linux', 'machine': 'x86_64'},
        'selection': {
            'profile': 'full',
            'task_id': None,
            'checks': ['environment', 'project-state', 'future-check', 'windows-native-build'],
        },
        'checks': [
            {'id': 'environment', 'status': 'passed', 'exit_code': 0, 'duration_seconds': 100},
            {'id': 'project-state', 'status': 'passed', 'exit_code': 0, 'duration_seconds': 90},
            {'id': 'future-check', 'status': 'blocked', 'reason_code': 'not_implemented'},
            {'id': 'windows-native-build', 'status': 'blocked', 'reason_code': 'unsupported_platform'},
            {'id': 'release-artifacts', 'status': 'blocked', 'reason_code': 'not_selected'},
        ],
    }


def hosted_run(*, run_id=11, day=20):
    return {
        'id': run_id,
        'run_attempt': 1,
        'path': '.github/workflows/verify.yml',
        'event': 'pull_request',
        'head_sha': 'a' * 40,
        'repository': {'full_name': 'fixture/scopehub'},
        'created_at': f'2026-11-{day:02}T00:00:00Z',
        'run_started_at': f'2026-11-{day:02}T00:00:10Z',
        'updated_at': f'2026-11-{day:02}T00:06:00Z',
        'status': 'completed',
        'conclusion': 'success',
    }


def hosted_job(job_id, platform, start, finish, step_name, step_start, step_finish, *, run_id=11, day=20):
    return {
        'id': job_id,
        'run_id': run_id,
        'run_attempt': 1,
        'run_url': f'https://api.github.com/repos/fixture/scopehub/actions/runs/{run_id}',
        'name': f'verify ({platform}-latest)',
        'head_sha': 'a' * 40,
        'status': 'completed',
        'conclusion': 'success',
        'started_at': f'2026-11-{day:02}T00:{start}Z',
        'completed_at': f'2026-11-{day:02}T00:{finish}Z',
        'steps': [{
            'name': step_name,
            'number': 12,
            'status': 'completed',
            'conclusion': 'success',
            'started_at': f'2026-11-{day:02}T00:{step_start}Z',
            'completed_at': f'2026-11-{day:02}T00:{step_finish}Z',
        }],
    }


def fast_report(day, elapsed):
    report = historical_report()
    report.update(
        run_id=f'202611{day:02}T000000.000000Z-aabbccdd',
        generated_at=f'2026-11-{day:02}T00:01:00Z',
        status='passed',
        timing={'elapsed_seconds': elapsed},
        selection={'profile': 'fast', 'task_id': None, 'checks': ['environment', 'project-state']},
        checks=[item for item in report['checks'] if item['status'] == 'passed'],
    )
    return report


BASIC_CHECKS = ('environment', 'project-state')
SPLIT_CHECKS = ('windows-native-build', 'workspace-timeframe', 'python-contracts-workers', 'market-recovery')
RECORDS_CHECKS = ('generated-contracts', 'dependency-review')
OLD_FULL_CHECKS = (*BASIC_CHECKS, 'future-check', 'windows-native-build')


def fixture_catalog(full=BASIC_CHECKS, *, records=(), non_change=()):
    extras = (*records, *non_change)
    return {'checks': [
        {'id': key, 'status': 'planned' if key == 'future-check' else 'implemented',
         'cadence': 'task' if key in extras else 'change'} for key in (*full, *extras)
    ], 'profiles': {'records': [*BASIC_CHECKS, *records]} if records else {}}


SPLIT_CATALOG = fixture_catalog((*BASIC_CHECKS, *SPLIT_CHECKS), records=RECORDS_CHECKS)
OLD_FULL_CATALOG = fixture_catalog(OLD_FULL_CHECKS, non_change=('release-artifacts',))


def use_catalog(test, catalog):
    test.enterContext(patch.object(timing, 'native_publishing_catalog', return_value=(True, catalog)))


def split_harness_report(elapsed, *, profile='full'):
    report = fast_report(20, elapsed)
    checks = {
        'full': SPLIT_CHECKS,
        'records': RECORDS_CHECKS,
        'ci-windows-core': ('windows-native-build',),
    }
    for key in checks[profile]:
        report['selection']['checks'].append(key)
        report['checks'].append({'id': key, 'status': 'passed', 'exit_code': 0, 'duration_seconds': 12})
    report['selection']['profile'] = profile
    report['run_id'] = 'windows-change-11-1'
    report['environment'].update(system='Windows', machine='AMD64')
    return report


def lane_sample(sample, lane):
    key = {'browser': 'workspace-timeframe', 'recovery': 'market-recovery'}[lane]
    row = {'id': key, 'status': 'passed', 'exit_code': 0, 'reason_code': None, 'duration_seconds': 12}
    checks = [item for item in sample['checks'] if item['id'] == key] or [row]
    return {**sample, 'profile': f'ci-windows-{lane}', 'selected': [key], 'checks': checks}


class ReportTimingTests(unittest.TestCase):
    def test_historical_durations_are_visible_without_inventing_wall_time(self):
        sample = timing.report_sample(historical_report())
        self.assertEqual('full', sample['profile'])
        self.assertEqual('Linux', sample['platform'])
        self.assertEqual(190, sample['seconds']['check_work'])
        self.assertIsNone(sample['seconds']['harness'])

    def test_local_execution_inputs_and_worktree_state_are_preserved_only_when_recorded(self):
        report = historical_report()
        self.assertIsNone(timing.report_sample(report)['inputs_sha256'])
        self.assertIsNone(timing.report_sample(report)['working_tree_dirty'])
        report['source'].update(sha256='c' * 64, working_tree_dirty=True)
        sample = timing.report_sample(report, cache='not_requested')
        self.assertEqual('c' * 64, sample['inputs_sha256'])
        self.assertTrue(sample['working_tree_dirty'])
        self.assertEqual('not_requested', sample['cache'])
        self.assertEqual('not_restored', timing.report_sample(report, cache='not_restored')['cache'])

    def test_aggregate_cache_observations_preserve_comparison_without_promoting_a_missing_restore(self):
        use_catalog(self, SPLIT_CATALOG)
        report = split_harness_report(600)
        report['selection']['profile'] = 'full'
        report['ci'] = {'repository': 'fixture/scopehub', 'run_id': '11', 'run_attempt': '1',
                        'event': 'pull_request', 'head_sha': 'a' * 40, 'checkout_sha': 'a' * 40}
        warm = timing.report_sample(report, cache='warm')
        states = dict(core='warm', browser='warm', python='not_requested', recovery='warm')
        current = dict(warm, windows_caches=states)
        self.assertTrue(timing.valid_sample(warm))
        self.assertTrue(timing.valid_sample(current))
        self.assertEqual(timing.coverage_key(warm), timing.coverage_key(current))
        unknown = dict(current, cache='unknown', windows_caches=dict(states, recovery='unknown'))
        self.assertTrue(timing.valid_sample(unknown))
        self.assertFalse(timing.valid_sample(dict(unknown, cache='warm')))
        missing = {lane: state for lane, state in states.items() if lane != 'recovery'}
        with self.assertRaisesRegex(ValueError, 'one cache state'):
            timing.windows_aggregate_cache(missing)
        for observations in ([warm, current], [current, warm]):
            issues = []
            self.assertEqual([[current]], timing.coalesce_samples(observations, issues))
            self.assertEqual([], issues)
        identity = timing.ci_key(warm['ci'])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'capture.json'
            for observations in ([warm, unknown], [unknown, warm]):
                with self.subTest(first_cache=observations[0]['cache']):
                    issues = []
                    capture = timing.capture_observations(identity, observations, [], issues)
                    self.assertFalse(capture.ambiguous)
                    timing.save_timing_capture(path, 1, capture.samples, capture.clocks, issues)
                    reload_issues = []
                    _, retained, retained_clocks, _ = timing.read_timing_capture(path)
                    self.assertCountEqual(observations, retained)
                    self.assertEqual([], reload_issues)
                    reopened = timing.capture_observations(
                        identity, retained, retained_clocks, reload_issues,
                    )
                    self.assertFalse(reopened.ambiguous)
                    self.assertEqual([[unknown]], timing.coalesce_samples(retained, reload_issues))
                    self.assertEqual([], reload_issues)

    def test_explicit_harness_clock_is_independent_of_overlapping_check_work(self):
        report = historical_report()
        report['timing'] = {'elapsed_seconds': 120}
        sample = timing.report_sample(report)
        self.assertEqual(120, sample['seconds']['harness'])
        self.assertEqual(190, sample['seconds']['check_work'])
        self.assertTrue(sample['check_work_complete'])

    def test_missing_check_duration_keeps_a_partial_work_observation(self):
        report = historical_report()
        del report['checks'][1]['duration_seconds']
        sample = timing.report_sample(report)
        self.assertTrue(sample['coverage_complete'])
        self.assertFalse(sample['check_work_complete'])
        self.assertEqual(100, sample['seconds']['check_work'])

    def test_platform_and_planned_omissions_preserve_applicable_coverage(self):
        report = historical_report()
        self.assertTrue(timing.report_sample(report)['coverage_complete'])
        for reason in ('earlier_failure', 'not_selected'):
            with self.subTest(reason=reason):
                next(item for item in report['checks'] if item['id'] == 'windows-native-build')['reason_code'] = reason
                self.assertFalse(timing.report_sample(report)['coverage_complete'])
        report['environment']['system'] = 'Windows'
        next(item for item in report['checks'] if item['id'] == 'windows-native-build')['reason_code'] = 'unsupported_platform'
        self.assertFalse(timing.report_sample(report)['coverage_complete'])

    def test_missing_result_or_failed_check_does_not_claim_complete_coverage(self):
        report = historical_report()
        report['checks'] = [item for item in report['checks'] if item['id'] != 'environment']
        self.assertFalse(timing.report_sample(report)['coverage_complete'])
        report = historical_report()
        report.update(status='failed')
        report['checks'][0].update(status='failed', exit_code=1)
        sample = timing.report_sample(report)
        self.assertEqual('failed', sample['report_status'])
        self.assertFalse(sample['coverage_complete'])
        self.assertEqual(190, sample['seconds']['check_work'])

    def test_older_report_without_selection_retains_check_work_only(self):
        report = historical_report()
        del report['selection']
        sample = timing.report_sample(report)
        self.assertFalse(sample['coverage_complete'])
        self.assertEqual(190, sample['seconds']['check_work'])
        self.assertIsNone(sample['seconds']['harness'])

    def test_unverified_source_or_passed_nonzero_exit_remains_diagnostic(self):
        for key in ('source_unchanged', 'project_unchanged', 'source'):
            report = fast_report(19, 120)
            del report[key]
            with self.subTest(missing=key):
                sample = timing.report_sample(report)
                self.assertFalse(sample['identity_complete'])
                self.assertEqual([], timing.timing_view([sample])['reviews'])
        report = fast_report(19, 120)
        del report['source']['git_head']
        self.assertFalse(timing.report_sample(report)['identity_complete'])
        report = fast_report(19, 120)
        report['checks'][0]['exit_code'] = 1
        self.assertFalse(timing.report_sample(report)['coverage_complete'])


class HostedTimingTests(unittest.TestCase):
    def setUp(self):
        use_catalog(self, SPLIT_CATALOG)

    def split_windows_jobs(self, *, scoped=False):
        full = 'Run uv run python scripts/check.py --require-windows'
        core = hosted_job(32, 'windows', '00:20', '02:00' if scoped else '09:00',
                          'Verify records changes' if scoped else 'Verify the Windows core lane',
                          '01:10', '01:50' if scoped else '08:40')
        browser = hosted_job(33, 'windows', '00:40', '00:54' if scoped else '12:00',
                             'Select the Windows lane work' if scoped else 'Verify the Windows browser lane',
                             '00:41' if scoped else '02:00', '00:48' if scoped else '11:25')
        python = hosted_job(34, 'windows', '00:30', '00:58' if scoped else '08:00',
                            'Select the Windows lane work' if scoped else 'Verify the Windows python lane',
                            '00:35' if scoped else '01:00', '00:50' if scoped else '07:50')
        aggregate = hosted_job(35, 'windows', '02:08' if scoped else '12:08', '02:45' if scoped else '12:45',
                               'Verify records changes' if scoped else full,
                               '02:25' if scoped else '12:27', '02:35' if scoped else '12:35')
        for name, row in zip(('core', 'browser', 'python'), (core, browser, python), strict=True):
            row['name'] = f'Windows change checks ({name})'
        if not scoped:
            browser['steps'].append(hosted_job(33, 'windows', '00:40', '12:00',
                                               'Install and start the pinned chart-test browser',
                                               '01:05', '01:35')['steps'][0])
        aggregate['steps'].append({'name': 'Require successful Windows lane jobs', 'conclusion': 'skipped'})
        linux = hosted_job(31, 'ubuntu', '00:10', '08:30', 'Run pnpm check', '00:50', '08:10')
        return linux, core, browser, python, aggregate

    def test_split_windows_distinguishes_actual_runner_work_from_the_protected_wall(self):
        linux, core, browser, python, aggregate = self.split_windows_jobs()
        setup = hosted_job(36, 'ubuntu', '00:05', '00:17', 'Select scope', '00:06', '00:16')
        setup.update(name='Verification scope')
        skipped = dict(setup, id=37, name='Unused job', conclusion='skipped')
        result = timing.hosted_timing(hosted_run(), [linux, core, browser, python, aggregate, setup, skipped])
        self.assertTrue(timing.clock_complete(result))
        self.assertEqual(765, result['workflow']['total_elapsed_seconds'])
        self.assertEqual(2199, result['workflow']['runner_work_seconds'])
        windows, = (row for row in result['jobs'] if row['platform'] == 'Windows')
        self.assertEqual({'queue': 8, 'initial_queue': 20, 'setup': 40, 'aggregate_setup': 19,
                          'harness_step': 695, 'tail': 10, 'runner': 1687, 'total': 765, 'browser_step': 30},
                         windows['seconds'])
        self.assertCountEqual(['Windows change checks (core)', 'Windows change checks (browser)',
                               'Windows change checks (python)', 'Verification scope'],
                              [row['name'] for row in result['sibling_jobs']])
        report = split_harness_report(675)
        report['selection']['profile'] = 'full'
        report['ci'] = {'repository': 'fixture/scopehub', 'run_id': '11', 'run_attempt': '1',
                        'event': 'pull_request', 'head_sha': 'a' * 40, 'checkout_sha': 'a' * 40}
        for cache in ('warm', 'unknown'):
            with self.subTest(cache=cache):
                review, = timing.timing_view([timing.report_sample(report, cache=cache)], hosted=[result])['reviews']
                self.assertEqual(cache, review['cache'])
                self.assertEqual({'total': 765, 'queue': 8, 'aggregate_setup': 19, 'harness_step': 695},
                                 {row['metric']: row['recent_median_seconds'] for row in review['metrics']
                                  if row['metric'] in {'total', 'queue', 'aggregate_setup', 'harness_step'}})

    def test_numbered_step_facts_are_independent_of_order_and_listing_boundaries(self):
        jobs = list(self.split_windows_jobs())
        browser_index = 2
        browser = jobs[browser_index]
        run = hosted_run()
        original = timing.hosted_timing(run, jobs)
        report = split_harness_report(675)
        report['ci'] = {**dict(zip(timing.ATTEMPT_IDENTITY, timing.ci_key(original))), 'checkout_sha': 'a' * 40}
        protected = timing.report_sample(report)
        samples = [protected, lane_sample(protected, 'browser')]
        changed_endpoint = deepcopy(browser)
        changed_endpoint['steps'][1]['completed_at'] = '2026-11-20T00:01:25Z'
        for claim, expected in ((browser, 30), (changed_endpoint, None)):
            reordered = dict(claim, steps=claim['steps'][::-1])
            other = timing.hosted_timing(run, [*jobs[:browser_index], reordered, *jobs[browser_index + 1:]])
            regrouped = timing.hosted_timing(run, [reordered, *reversed(jobs), reordered])
            duplicate_step = dict(browser, steps=[*browser['steps'], *reordered['steps']])
            one_job = timing.hosted_timing(run, [*jobs[:browser_index], duplicate_step, *jobs[browser_index + 1:]])
            for readings in ([original, other], [other, original, other], [regrouped], [one_job]):
                with self.subTest(expected=expected, count=len(readings)), tempfile.TemporaryDirectory() as temporary:
                    path = Path(temporary) / 'capture.json'
                    timing.save_timing_capture(path, 0, samples, readings, [])
                    _, retained, clocks, _ = timing.read_timing_capture(path)
                    view = timing.timing_view(retained, hosted=clocks)
                    rows = {row['profile']: row['seconds'] for row in view['samples']}
                    self.assertEqual((765, 1687), (rows['full']['total'], rows['full']['runner']))
                    self.assertEqual(720, rows['ci-windows-browser']['total'])
                    self.assertEqual([expected, expected], [row['browser_step'] for row in rows.values()])
                    expected_diagnostics = set() if expected is not None else {
                        'Conflicting Windows producer job measurements.', 'Conflicting Windows aggregate stage inputs.',
                    }
                    self.assertEqual(expected_diagnostics, {issue['error'] for issue in view['input_issues']})

    def test_agreed_split_intervals_survive_a_missing_or_contested_step_endpoint(self):
        jobs = list(self.split_windows_jobs())
        run = hosted_run()
        before = timing.hosted_timing(run, jobs)
        report = split_harness_report(675)
        report['ci'] = {**dict(zip(timing.ATTEMPT_IDENTITY, timing.ci_key(before))), 'checkout_sha': 'a' * 40}
        sample = timing.report_sample(report)
        fields = ('aggregate_setup', 'setup', 'harness_step', 'tail', 'queue', 'runner', 'total')
        baseline = (19, 40, 695, 10, 8, 1687, 765)
        cases = (
            (4, 'started_at', '12:28', 'harness_start', (None, 40, 695, 10, 8, 1687, 765)),
            (4, 'completed_at', '12:36', 'harness_end', (19, 40, None, None, 8, 1687, 765)),
            (3, 'started_at', '01:01', 'harness_start', (19, None, None, 10, 8, 1687, 765)),
            (3, 'completed_at', '07:51', 'harness_end', baseline),
        )
        for index, key, alternate, endpoint, expected in cases:
            changed = deepcopy(jobs)
            changed[index]['steps'][0][key] = f'2026-11-20T00:{alternate}Z'
            other = timing.hosted_timing(run, changed)
            grouped = timing.hosted_timing(run, [changed[index], *reversed(jobs), changed[index]])
            missing = deepcopy(jobs)
            missing[index]['steps'][0][key] = None
            incomplete = timing.hosted_timing(run, missing)
            variants = (
                ([before, other], expected, endpoint),
                ([other, before, other], expected, endpoint),
                ([grouped], expected, endpoint),
                ([incomplete], expected, None),
                ([incomplete, before, incomplete], baseline, None),
            )
            for clocks, readings, disputed_endpoint in variants:
                with self.subTest(index=index, endpoint=key, readings=readings), tempfile.TemporaryDirectory() as temporary:
                    path = Path(temporary) / 'capture.json'
                    timing.save_timing_capture(path, 0, [sample], clocks, [])
                    _, samples, retained, _ = timing.read_timing_capture(path)
                    view = timing.timing_view(samples, hosted=retained)
                    result, = view['samples']
                    self.assertEqual(readings, tuple(result['seconds'][key] for key in fields))
                    self.assertEqual({disputed_endpoint} if disputed_endpoint else set(), {
                        item for issue in view['input_issues'] for item in issue.get('endpoints', ())
                    })

        shifted = timing.hosted_timing(dict(run, created_at='2026-11-20T00:00:01Z'), jobs)
        view = timing.timing_view([sample], hosted=[before, shifted])
        result, = view['samples']
        self.assertEqual((19, 40, 695, 10, 8, 1687, None), tuple(result['seconds'][key] for key in fields))
        self.assertIsNone(result['seconds']['initial_queue'])

    def test_phase_ordering_survives_same_job_and_cross_listing_joins(self):
        *lanes, parent = self.split_windows_jobs()
        run = hosted_run()
        official = timing.hosted_timing(run, [*lanes, parent])
        identity = timing.ci_key(official)
        report = split_harness_report(675)
        report['ci'] = {**dict(zip(timing.ATTEMPT_IDENTITY, identity)), 'checkout_sha': 'a' * 40}
        windows = timing.report_sample(report)
        linux = dict(windows, platform='Linux', run_id=fast_report(20, 675)['run_id'])
        samples = [windows, linux]

        def fragment(start, end):
            modified = deepcopy(parent)
            modified['steps'][0].update(
                started_at=f'2026-11-20T00:{start}Z' if start else None,
                completed_at=f'2026-11-20T00:{end}Z' if end else None,
            )
            return modified

        def check(clocks, expected, ready):
            view = timing.timing_view(samples, hosted=clocks)
            row = next(item for item in view['samples'] if item['platform'] == 'Windows')
            seconds = row['seconds']
            self.assertEqual(expected, tuple(seconds[key] for key in ('aggregate_setup', 'harness_step', 'tail')))
            self.assertEqual((765, 1687), (seconds['total'], seconds['runner']))
            self.assertIs(ready, timing.capture_observations(identity, samples, clocks, []).complete_clock)
            return view['input_issues']

        cases = (
            ([(None, None), (None, '12:35')], (None, 695, 10), True),
            ([('12:27', None), (None, '12:26')], (None, None, None), False),
            ([('12:27', None), ('12:28', None), (None, '12:26')], (None, None, None), False),
            ([('12:27', None), ('12:28', None), (None, '12:35')], (None, 695, 10), True),
            ([('12:27', None), ('12:30', None), (None, '12:28')], (None, 688, 17), True),
            ([('12:07', None), ('12:28', None), (None, '12:26')], (None, None, None), False),
        )
        for intervals, expected, ready in cases:
            with self.subTest(intervals=intervals), tempfile.TemporaryDirectory() as temporary:
                rows = [fragment(*pair) for pair in intervals]
                separate = [timing.hosted_timing(run, [*lanes, row]) for row in rows]
                grouped = timing.hosted_timing(run, [*rows, *lanes, rows[0]])
                repeated = dict(parent, steps=[*(row['steps'][0] for row in rows), *parent['steps'][1:]])
                one_job = timing.hosted_timing(run, [*lanes, repeated])
                expected_issues = check(separate, expected, ready)
                self.assertEqual(not ready, any('ordering' in issue['error'] for issue in expected_issues))
                path = Path(temporary) / 'capture.json'
                prefix = timing.hosted_timing(run, [*lanes, *rows[:2]])
                for clocks in ([*reversed(separate), separate[0]], [grouped], [one_job], [prefix, *separate[2:]]):
                    timing.save_timing_capture(path, 0, samples, clocks, [])
                    _, _, reopened, _ = timing.read_timing_capture(path)
                    self.assertEqual(expected_issues, check(reopened, expected, ready))

        # Older grouped captures distinguish a dispute from an absent value,
        # even when they cannot establish which original timestamps competed.
        legacy = timing.hosted_timing(run, [*lanes, fragment('12:27', None), fragment('12:28', '12:35')])
        previous = next(job for job in legacy['jobs'] if job['platform'] == 'Windows')
        previous.pop('endpoint_choices')
        self.assertTrue(timing.valid_clock(legacy))
        check([legacy, official], (None, None, None), False)

        too_many = [fragment(f'12:{second:02d}', None)
                    for second in range(20, 21 + timing.MAX_TIMESTAMP_CHOICES)]
        overflow = timing.hosted_timing(run, [*lanes, *too_many, fragment(None, '12:35')])
        disputed = next(job for job in overflow['jobs'] if job['platform'] == 'Windows')
        self.assertIn('harness_start', disputed['endpoint_disputes'])
        self.assertNotIn('harness_start', disputed.get('endpoint_choices', {}))
        self.assertTrue(timing.valid_clock(overflow))
        check([overflow], (None, None, None), False)

    def test_recent_collection_only_credits_received_qualified_platform_report(self):
        run = hosted_run()
        jobs = self.split_windows_jobs()
        clock = timing.hosted_timing(run, jobs)
        report = split_harness_report(675)
        report['ci'] = {**dict(zip(timing.ATTEMPT_IDENTITY, timing.ci_key(clock))), 'checkout_sha': 'a' * 40}
        full = timing.report_sample(report)
        browser = lane_sample(full, 'browser')
        run_again = dict(run, run_attempt=2)
        again = timing.hosted_timing(run_again, [dict(job, run_attempt=2) for job in jobs])
        second = dict(full, run_id='windows-change-11-2', ci=dict(full['ci'], run_attempt='2'))
        shortened = dict(full, selected=[key for key in full['selected'] if key != 'workspace-timeframe'])
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / 'retained.json'
            timing.save_review_archive(archive, {'kind': timing.ARCHIVE_KIND, 'samples': [full], 'hosted': [clock]})
            for name, listing, observed, added, eligible in (
                ('full', run, clock, [full], True),
                ('browser', run, clock, [browser], False),
                ('missing', run, clock, [], False),
                ('another attempt', run_again, again, [second], False),
                ('incomplete selection', run, clock, [shortened], False),
            ):
                with self.subTest(name=name):
                    out = StringIO()
                    argv = ['timing.py', '--report', str(archive), '--recent', '1', '--github-actions']
                    with (patch.object(sys, 'argv', argv), redirect_stdout(out),
                          patch.object(timing, 'hosted_repository', return_value='fixture/scopehub'),
                          patch.object(timing, 'collection_source', return_value='c' * 40),
                          patch.object(timing, 'recent_runs', return_value=[listing]),
                          patch.object(timing, 'download_timing', return_value=added),
                          patch.object(timing, 'inspect_jobs', return_value=observed)):
                        timing.main()
                    view = json.loads(out.getvalue())
                    objective, = view['objective_reviews']
                    self.assertEqual((1, 765), (objective['sample_count'], objective['recent_median_seconds']))
                    collected = view['recent_collection']['collected_samples']
                    receipt = timing.platform_report_receipt(full, timing.report_input_sha256(full))
                    self.assertEqual([receipt] if eligible else [], collected)

    def test_recent_collection_requires_original_layout_and_publishing_qualification(self):
        run = hosted_run()
        split_clock = timing.hosted_timing(run, self.split_windows_jobs())
        linux = hosted_job(31, 'ubuntu', '00:10', '04:00', 'Run pnpm check', '00:30', '03:50')
        windows = hosted_job(32, 'windows', '00:20', '06:00',
                             'Run uv run python scripts/check.py --require-windows', '00:50', '05:20')
        single_clock = timing.hosted_timing(run, [linux, windows])
        ci = {**dict(zip(timing.ATTEMPT_IDENTITY, timing.ci_key(split_clock))), 'checkout_sha': 'a' * 40}
        single_report = historical_report()
        single_report['ci'] = ci
        single_report['environment'].update(system='Windows', machine='AMD64')
        single_report['checks'][3] = {'id': 'windows-native-build', 'status': 'passed', 'exit_code': 0}
        single = timing.report_sample(single_report, cache='warm')
        split = timing.report_sample(dict(split_harness_report(675), ci=ci))
        with_proof = dict(split, generated_contracts={'tree': 'd' * 40})
        with_wrong_layout = dict(single, windows_caches=dict(
            core='warm', browser='warm', python='not_requested', recovery='warm',
        ))

        # The public replay also exercises the real published Git catalog.
        def catalog(sample):
            if sample['run_id'] == single['run_id']:
                return OLD_FULL_CATALOG
            return SPLIT_CATALOG if sample.get('generated_contracts') else None

        for name, original, invalid, clock, wall in (
            ('single job', single, with_wrong_layout, single_clock, 360),
            ('publishing proof', with_proof, split, split_clock, 765),
        ):
            origin = timing.report_input_sha256(original)
            self.assertNotEqual(origin, timing.report_input_sha256(invalid))
            other_cache = 'unknown' if original['cache'] == 'warm' else 'warm'
            supplemented = dict(original, cache=other_cache)
            for label, received, historical, eligible in (
                ('valid new', original, False, True),
                ('invalid alone', invalid, False, False),
                ('invalid with history', invalid, True, False),
                ('valid with compatible history', supplemented, True, True),
            ):
                acquired = timing.report_input_sha256(received)
                expected = [timing.platform_report_receipt(received, acquired)] if eligible else []
                with self.subTest(name=name, label=label), tempfile.TemporaryDirectory() as folder:
                    archive = Path(folder) / 'retained.json'
                    timing.save_review_archive(archive, {'kind': timing.ARCHIVE_KIND,
                                               'samples': [original] if historical else [], 'hosted': []})
                    out = StringIO()
                    argv = ['timing.py', '--report', str(archive), '--recent', '1', '--github-actions']
                    with (patch.object(sys, 'argv', argv), redirect_stdout(out),
                          patch.object(timing, 'hosted_repository', return_value='fixture/scopehub'),
                          patch.object(timing, 'collection_source', return_value='c' * 40),
                          patch.object(timing, 'recent_runs', return_value=[run]),
                          patch.object(timing, 'download_timing', return_value=[received]),
                          patch.object(timing, 'inspect_jobs', return_value=clock),
                          patch.object(timing, 'published_catalog', side_effect=catalog)):
                        timing.main()
                    view = json.loads(out.getvalue())
                    self.assertEqual(expected, view['recent_collection']['collected_samples'])
                    admitted = [row for row in view['samples'] if row['review_exclusion'] is None]
                    if expected or historical:
                        admitted, = admitted
                        self.assertEqual(wall, admitted['seconds']['total'])
                        inputs = ({origin} if historical else set()) | ({acquired} if eligible else set())
                        self.assertEqual(sorted(inputs), admitted['report_inputs_sha256'])
                    else:
                        self.assertEqual([], admitted)
                    if received is invalid:
                        self.assertTrue(view['input_issues'])

    def test_split_windows_measurement_and_joint_capture_use_the_identified_layout(self):
        linux, core, browser, python, aggregate = self.split_windows_jobs()
        run = hosted_run()
        jobs = [linux, core, browser, python, aggregate]
        full = timing.hosted_timing(run, jobs)
        short_job = dict(aggregate, steps=aggregate['steps'][:1])
        short = timing.hosted_timing(run, [linux, short_job])
        self.assertTrue(timing.clock_complete(short))
        windows_report = split_harness_report(675)
        windows_report['selection']['profile'] = 'full'
        windows_report['ci'] = {**dict(zip(timing.ATTEMPT_IDENTITY, timing.ci_key(full))), 'checkout_sha': 'a' * 40}
        windows_sample = timing.report_sample(windows_report)
        linux_sample = dict(windows_sample, platform='Linux', run_id=fast_report(20, 675)['run_id'])

        def missing(row):
            return dict(row, steps=[dict(step, completed_at=None) for step in row['steps']])

        late_linux = timing.hosted_timing(run, [missing(linux), *jobs[1:]])
        late_windows = timing.hosted_timing(run, [*jobs[:-1], missing(aggregate)])
        missing_core = timing.hosted_timing(run, [linux, dict(core, started_at=None), *jobs[2:]])
        for readings, runner, step, complete in (([short, full], 1687, 695, True),
                                                  ([short, missing_core], None, None, False),
                                                  ([late_linux, late_windows], 1687, 695, True)):
            for clocks in ([*readings, readings[0]], readings[::-1]):
                view = timing.timing_view([linux_sample, windows_sample], hosted=clocks)
                by_platform = {row['platform']: row for row in view['samples']}
                seconds = by_platform['Windows']['seconds']
                self.assertEqual((765, runner, step), (seconds['total'], seconds['runner'], seconds['harness_step']))
                self.assertEqual(440, by_platform['Linux']['seconds']['harness_step'])
                capture = timing.capture_observations(timing.ci_key(full), [linux_sample, windows_sample], clocks, [])
                self.assertIs(complete, capture.complete_clock)

    def test_only_bound_producer_endpoints_can_complete_split_intervals(self):
        from copy import deepcopy

        jobs = self.split_windows_jobs()
        run = hosted_run()
        official = timing.hosted_timing(run, jobs)
        report = split_harness_report(675)
        report['ci'] = {**dict(zip(timing.ATTEMPT_IDENTITY, timing.ci_key(official))), 'checkout_sha': 'a' * 40}
        windows = timing.report_sample(report)
        linux = dict(windows, platform='Linux', run_id=fast_report(20, 675)['run_id'])
        observations = [linux, windows]

        def change_member(lane, *, foreign=False, missing_step=False):
            changed = deepcopy(jobs)
            job = next(row for row in changed if row['name'] == timing.WINDOWS_PRODUCERS[lane])
            if foreign:
                job.update(head_sha='c' * 40, started_at='2026-11-20T00:00:27Z')
            elif missing_step:
                step = next(row for row in job['steps'] if row['name'] == f'Verify the Windows {lane} lane')
                step['started_at'] = None
            else:
                job['started_at'] = None
            return timing.hosted_timing(run, changed)

        foreign = change_member('core', foreign=True)
        missing = change_member('core')
        browser = change_member('browser', missing_step=True)
        python = change_member('python', missing_step=True)
        raw_foreign = dict(next(job for job in jobs if job['name'] == timing.WINDOWS_PRODUCERS['core']),
                           head_sha='c' * 40, started_at='2026-11-20T00:00:27Z')
        co_listed = timing.hosted_timing(run, [*jobs, raw_foreign, jobs[0]])
        for pair, values, ready in (([official, foreign], (1687, 40, 695), True),
                                    ([co_listed, co_listed], (1687, 40, 695), True),
                                    ([missing, foreign], (None, None, None), False),
                                    ([browser, python], (1687, 40, 695), True)):
            for clocks in ([*pair, pair[0]], [*reversed(pair), pair[1]]):
                view = timing.timing_view(observations, hosted=clocks)
                sample = next(row for row in view['samples'] if row['platform'] == 'Windows')
                self.assertEqual(765, sample['seconds']['total'])
                self.assertEqual(values, tuple(sample['seconds'][field]
                                               for field in ('runner', 'setup', 'harness_step')))
                captured = timing.capture_observations(timing.ci_key(official), observations, clocks, [])
                self.assertIs(ready, captured.complete_clock)

    def test_four_producer_clocks_keep_each_current_job_and_read_legacy_three_producer_history(self):
        for scoped in (False, True):
            legacy_jobs = self.split_windows_jobs(scoped=scoped)
            legacy = timing.hosted_timing(hosted_run(), legacy_jobs)
            self.assertTrue(timing.clock_complete(legacy))
            self.assertNotIn('windows_recovery_producer', legacy['workflow'])
            *siblings, aggregate = legacy_jobs
            aggregate = dict(aggregate, steps=[
                aggregate['steps'][0], {'name': timing.WINDOWS_RECOVERY_MARKER, 'conclusion': 'success'},
            ])
            recovery = hosted_job(36, 'windows', '00:35', '00:55' if scoped else '05:05',
                                  'Select the Windows lane work' if scoped else 'Verify the Windows recovery lane',
                                  '00:40' if scoped else '02:00', '00:55' if scoped else '05:00')
            recovery['name'] = 'Windows change checks (recovery)'
            current = timing.hosted_timing(hosted_run(), [*siblings, recovery, aggregate])
            retained = json.loads(timing.json_bytes(current))
            self.assertTrue(timing.valid_clock(retained))
            self.assertTrue(timing.clock_complete(retained))
            self.assertTrue(retained['workflow']['windows_recovery_producer'])
            self.assertEqual(legacy['workflow']['total_elapsed_seconds'], current['workflow']['total_elapsed_seconds'])
            self.assertEqual(legacy['workflow']['runner_work_seconds'] + (20 if scoped else 270),
                             current['workflow']['runner_work_seconds'])
            for changed in ([], [dict(recovery, run_attempt=2)], [recovery, dict(recovery, id=37)]):
                with self.subTest(scoped=scoped, jobs=changed):
                    incomplete = timing.hosted_timing(hosted_run(), [*siblings, *changed, aggregate])
                    self.assertFalse(timing.clock_complete(incomplete))
                    self.assertIsNone(incomplete['workflow']['total_elapsed_seconds'])
            for conclusion in ('failure', 'cancelled'):
                failed = timing.hosted_timing(dict(hosted_run(), conclusion=conclusion),
                                             [*siblings, dict(recovery, conclusion=conclusion), aggregate])
                self.assertTrue(timing.clock_capture_complete(failed))
                self.assertFalse(timing.clock_complete(failed))
            retained['sibling_jobs'].pop()
            self.assertFalse(timing.clock_complete(retained))

    def test_unmarked_split_job_data_cannot_be_recognized_as_complete_legacy(self):
        *producers, aggregate = self.split_windows_jobs()
        unmarked = dict(aggregate, steps=aggregate['steps'][:1])
        result = timing.hosted_timing(hosted_run(), [*producers, unmarked])
        self.assertFalse(timing.clock_capture_complete(result))
        self.assertIsNone(result['workflow']['total_elapsed_seconds'])
        self.assertIsNone(result['workflow']['runner_work_seconds'])

    def test_four_producer_wall_clock_needs_every_cache_state_to_enter_the_warm_cohort(self):
        *producers, aggregate = self.split_windows_jobs()
        legacy = timing.hosted_timing(hosted_run(), [*producers, aggregate])
        aggregate = dict(aggregate, steps=[
            aggregate['steps'][0], {'name': timing.WINDOWS_RECOVERY_MARKER, 'conclusion': 'success'},
        ])
        recovery = hosted_job(36, 'windows', '00:35', '05:05',
                              'Verify the Windows recovery lane', '02:00', '05:00')
        recovery['name'] = 'Windows change checks (recovery)'
        current = timing.hosted_timing(hosted_run(), [*producers, recovery, aggregate])
        report = split_harness_report(675)
        report['selection']['profile'] = 'full'
        report['ci'] = {'repository': 'fixture/scopehub', 'run_id': '11', 'run_attempt': '1',
                        'event': 'pull_request', 'head_sha': 'a' * 40, 'checkout_sha': 'a' * 40}
        scalar = timing.report_sample(report, cache='warm')
        states = dict(core='warm', browser='warm', python='not_requested', recovery='warm')
        for clock, sample, cache in ((current, scalar, 'unknown'),
                                     (current, dict(scalar, windows_caches=states), 'warm'),
                                     (legacy, scalar, 'warm')):
            with self.subTest(cache=cache, layout=clock['workflow'].get('windows_recovery_producer')):
                view = timing.timing_view([sample], hosted=[clock])
                review, = view['reviews']
                observed, = view['samples']
                self.assertEqual(cache, review['cache'])
                self.assertEqual(765, observed['seconds']['total'])
                self.assertIsNone(observed['review_exclusion'])

    def test_windows_producer_clocks_do_not_fill_the_protected_platform_capture(self):
        *siblings, aggregate = self.split_windows_jobs()
        aggregate = dict(aggregate, steps=[
            aggregate['steps'][0], {'name': timing.WINDOWS_RECOVERY_MARKER, 'conclusion': 'success'},
        ])
        recovery = hosted_job(36, 'windows', '00:35', '05:05', 'Verify the Windows recovery lane', '02:00', '05:00')
        recovery['name'] = 'Windows change checks (recovery)'
        recovery_job = recovery
        clock = timing.hosted_timing(hosted_run(), [*siblings, recovery_job, aggregate])
        identity = timing.ci_key(clock)
        report = split_harness_report(675)
        report['ci'] = {**dict(zip(timing.ATTEMPT_IDENTITY, identity)), 'checkout_sha': 'a' * 40}
        report['selection']['profile'] = 'full'
        full = timing.report_sample(report)
        browser = lane_sample(full, 'browser')
        recovery = lane_sample(full, 'recovery')
        producers = [browser, recovery]

        for sample, job, elapsed in [(browser, '33', 720), (recovery, '36', 305)]:
            with self.subTest(profile=sample['profile']):
                view = timing.timing_view([sample], hosted=[clock])
                observed, = view['samples']
                self.assertEqual(job, observed['job']['job_id'])
                self.assertEqual(elapsed, observed['seconds']['total'])
                self.assertIn('protected platform coverage', observed['review_exclusion'])
                self.assertEqual([], view['reviews'])

        unbound = dict(recovery_job, run_attempt=None)
        partial = timing.hosted_timing(hosted_run(), [*siblings, unbound, aggregate])
        observed, = timing.timing_view([recovery], hosted=[partial])['samples']
        self.assertIsNone(observed['job'])
        self.assertIsNone(observed['seconds']['total'])

        for samples, missing in [(producers, {'Linux', 'Windows'}), ([full, *producers], {'Linux'})]:
            with self.subTest(samples=len(samples)):
                issues = []
                capture = timing.capture_observations(identity, samples, [clock], issues, conclusion='success')
                self.assertCountEqual(samples, capture.samples)
                self.assertEqual(missing, capture.missing)
                self.assertFalse(capture.ambiguous)
                self.assertTrue(capture.complete_clock)
                self.assertEqual([], issues)

        clock = timing.hosted_timing(hosted_run(), self.split_windows_jobs(scoped=True))
        scoped_report = split_harness_report(675, profile='records')
        scoped_report['ci'] = report['ci']
        scoped = timing.report_sample(scoped_report)
        view = timing.timing_view([scoped, scoped], hosted=[clock])
        observed, = view['samples']
        self.assertEqual('35', observed['job']['job_id'])
        self.assertIsNone(observed['review_exclusion'])
        self.assertEqual([], view['input_issues'])

    def test_split_producer_evidence_rejects_role_conflicts_and_keeps_complementary_jobs(self):
        from copy import deepcopy

        *siblings, aggregate = self.split_windows_jobs()
        aggregate['steps'].append({'name': timing.WINDOWS_RECOVERY_MARKER, 'conclusion': 'success'})
        aggregate['steps'] = [step for step in aggregate['steps'] if step['name'] != timing.WINDOWS_AGGREGATE_MARKER]
        recovery_job = hosted_job(36, 'windows', '00:35', '05:05', 'Verify the Windows recovery lane', '02:00', '05:00')
        recovery_job['name'] = 'Windows change checks (recovery)'
        jobs = [*siblings, recovery_job, aggregate]
        raw_run = hosted_run()
        clock = timing.hosted_timing(raw_run, jobs)
        identity = timing.ci_key(clock)
        report = split_harness_report(675)
        ci = {**dict(zip(timing.ATTEMPT_IDENTITY, identity)), 'checkout_sha': 'a' * 40}
        report.update(ci=ci, environment={'system': 'Windows', 'machine': 'AMD64'})
        browser = lane_sample(timing.report_sample(report), 'browser')
        recovery = lane_sample(browser, 'recovery')
        samples = [browser, recovery]

        swapped_jobs = deepcopy(jobs)
        for job in swapped_jobs:
            for old, new in [('core', 'recovery'), ('recovery', 'core')]:
                if job['name'] == timing.WINDOWS_PRODUCERS[old]:
                    job['name'] = timing.WINDOWS_PRODUCERS[new]
                    job['steps'][0]['name'] = f'Verify the Windows {new} lane'
                    break
        swapped = timing.hosted_timing(raw_run, swapped_jobs)
        for pair in ([clock, swapped], [swapped, clock]):
            view = timing.timing_view(samples, hosted=pair)
            self.assertTrue(all(row['seconds']['total'] is None for row in view['samples']))
            self.assertEqual(['Conflicting complete attempt clocks.'], [issue['error'] for issue in view['input_issues']])

        partial = [timing.hosted_timing(raw_run, [job for job in jobs if job['id'] != omitted]) for omitted in (33, 36)]
        for pair in (partial, partial[::-1]):
            view = timing.timing_view(samples, hosted=pair)
            self.assertEqual([], view['input_issues'])
            self.assertEqual([720, 305], [row['seconds']['total'] for row in view['samples']])
            self.assertFalse(any(row['hosted_success_complete'] for row in view['samples']))
            capture = timing.capture_observations(identity, samples, pair, [])
            self.assertEqual({'Linux', 'Windows'}, capture.missing)
            self.assertFalse(capture.complete_clock)

        invalid_id = deepcopy(clock)
        next(row for row in invalid_id['sibling_jobs'] if row['job_id'] == '33')['job_id'] = None
        incompatible = dict(browser, ci=dict(ci, checkout_sha='c' * 40))
        for sample, observed in [(browser, invalid_id), (incompatible, clock)]:
            with self.subTest(sample=sample['ci']['checkout_sha']):
                row, = timing.timing_view([sample], hosted=[observed])['samples']
                self.assertIsNone(row['job'])
                self.assertIsNone(row['seconds']['total'])

    def test_shared_capture_preserves_job_and_checkout_evidence_in_both_orders(self):
        jobs = self.split_windows_jobs()
        run = hosted_run()
        clock = timing.hosted_timing(run, jobs)
        browser_job = next(job for job in jobs if job['id'] == 33)
        known = timing.hosted_timing(run, [dict(job, checkout_sha='a' * 40) if job is browser_job else job
                                          for job in jobs])
        wrong_job = dict(browser_job, id=39)
        partial = timing.hosted_timing(run, [jobs[0], wrong_job])
        self.assertTrue(timing.clock_complete(known))
        self.assertFalse(timing.clock_capture_complete(partial))

        report = split_harness_report(675)
        report['selection']['profile'] = 'full'
        report['ci'] = {**dict(zip(timing.ATTEMPT_IDENTITY, timing.ci_key(clock))), 'checkout_sha': 'a' * 40}
        windows = timing.report_sample(report)
        browser = lane_sample(windows, 'browser')
        linux = dict(windows, platform='Linux', run_id=fast_report(21, 675)['run_id'])
        other = dict(browser, source_commit='d' * 40, ci=dict(browser['ci'], checkout_sha='d' * 40))
        samples = [linux, windows, browser]
        self.enterContext(isolated_git_environment())
        for case, extra_clock, extra_samples, expected_status, expected_wall, expected_browser in (
            ('compatible checkout', known, [], 'retained', 765, 720),
            ('incompatible job', partial, [], 'ambiguous', 765, None),
            ('incompatible checkout', known, [other], 'ambiguous', None, None),
        ):
            results = []
            for clocks in ([clock, extra_clock], [extra_clock, clock]):
                with self.subTest(case=case, reversed=clocks[0] is not clock), tempfile.TemporaryDirectory() as path:
                    root = Path(path)
                    subprocess.run(['git', 'init', '-q', str(root)], check=True, capture_output=True)
                    shared = timing.shared_timing_directory(root)
                    capture = shared / 'ci-pull_request-11-1.json'
                    repository = 'fixture/scopehub'
                    with (patch.object(timing, 'download_timing', side_effect=AssertionError('Unexpected artifact request')),
                          patch.object(timing, 'inspect_jobs', side_effect=AssertionError('Unexpected jobs request'))):
                        self.assertEqual('retained', timing.collect_new_ci_timing(root, repository, run, samples, [clock])[0])
                        self.assertEqual(2, len(list(shared.glob('linked-harness/*/ci-*.json'))))
                        for rows, evidence in (([*samples, *extra_samples], clocks), (extra_samples, clocks[::-1])):
                            self.assertEqual(expected_status, timing.collect_new_ci_timing(
                                root, repository, run, rows, evidence,
                            )[0])
                    _, retained, hosted, _ = timing.read_timing_capture(capture)
                    self.assertCountEqual([*samples, *extra_samples], retained)
                    self.assertCountEqual(clocks, hosted)
                    reindex = timing.index_retained_captures(root)
                    self.assertEqual((0 if extra_samples else 1, 0),
                                     (reindex['indexed_captures'], reindex['new_links']))
                    markers = list(shared.glob('linked-harness/*/ci-*.json'))
                    self.assertEqual(0 if extra_samples else 2, len(markers))
                    view = timing.timing_view(retained, hosted=hosted)
                    observed = {(sample['platform'], sample['profile']): sample for sample in view['samples']}
                    self.assertEqual(expected_wall, observed['Windows', 'full']['seconds']['total'])
                    if not extra_samples:
                        self.assertEqual(expected_browser, observed['Windows', 'ci-windows-browser']['seconds']['total'])
                    linked_issues = []
                    linked, linked_clocks = timing.linked_capture_inputs(
                        [shared], {timing.report_key(windows)}, [], linked_issues,
                    )
                    self.assertFalse(linked_issues)
                    if extra_samples:
                        self.assertEqual([], linked)
                        self.assertEqual([], linked_clocks)
                        self.assertTrue(all(row['seconds']['total'] is None for row in view['samples']))
                        self.assertEqual('Conflicting timing checkouts for this attempt.', view['input_issues'][0]['error'])
                    else:
                        linked_view = timing.timing_view(linked, hosted=linked_clocks)
                        self.assertEqual(view, linked_view)
                        if expected_browser is None:
                            self.assertEqual('Conflicting Windows producer job observations.', view['input_issues'][0]['error'])
                        else:
                            self.assertEqual([], view['input_issues'])
                            self.assertEqual('a' * 40, observed['Windows', 'ci-windows-browser']['job']['checkout_sha'])
                    results.append(capture.read_bytes())
            if len(results) == 2:
                self.assertEqual(results[0], results[1])

        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            subprocess.run(['git', 'init', '-q', str(root)], check=True, capture_output=True)
            shared = timing.shared_timing_directory(root)
            capture = shared / 'ci-pull_request-11-1.json'
            with (patch.object(timing, 'download_timing', return_value=[linux, windows]) as fetch,
                  patch.object(timing, 'inspect_jobs', side_effect=AssertionError('Unexpected jobs request'))):
                for clocks in ([clock, partial], [partial, clock, partial]):
                    result = timing.collect_new_ci_timing(root, 'fixture/scopehub', run, [browser], clocks)
                    self.assertEqual('ambiguous', result[0])
            fetch.assert_called_once_with('fixture/scopehub', '11', '1')
            self.assertEqual(2, len(list(shared.glob('linked-harness/*/ci-*.json'))))
            _, rows, clocks, _ = timing.read_timing_capture(capture)
            view = timing.timing_view(rows, hosted=clocks)
            times = {(row['platform'], row['profile']): row['seconds']['total'] for row in view['samples']}
            self.assertEqual({('Linux', 'full'): 510, ('Windows', 'full'): 765,
                              ('Windows', 'ci-windows-browser'): None}, times)

    def test_retained_attempt_disputes_survive_recent_rotation_and_archive_reopen(self):
        clock = timing.hosted_timing(hosted_run(), self.split_windows_jobs())
        browser = next(job for job in clock['sibling_jobs'] if job['job_id'] == '33')
        partial = dict(clock, jobs=[], sibling_jobs=[dict(browser, job_id='39')],
                       workflow=dict(clock['workflow'], matrix_complete=False, identity_complete=False))
        alternate_checkout = dict(partial, sibling_jobs=[], checkout_sha='d' * 40)
        report = split_harness_report(675)
        report['ci'] = {**dict(zip(timing.ATTEMPT_IDENTITY, timing.ci_key(clock))), 'checkout_sha': 'a' * 40}
        aggregate = timing.report_sample(report)
        samples = [aggregate, lane_sample(aggregate, 'browser')]
        unrelated = dict(partial, head_sha='b' * 40, checkout_sha='e' * 40)
        feedback = [{'verification_budget': {'event': 'pull_request'},
                     'diagnostic_reports': [dict(zip(('run_id', 'source_commit'), timing.report_key(aggregate)))]}]

        def measurements(view):
            rows = {row['profile']: row for row in view['samples']}
            full = rows['full']
            return (full['seconds']['total'], full['seconds']['runner'],
                    rows['ci-windows-browser']['seconds']['total'], full['review_exclusion'] is None)

        for case, adverse, expected, qualified in (
            ('valid', None, (765, 1687, 720, True), True),
            ('producer', partial, (765, None, None, True), True),
            ('checkout', alternate_checkout, (None, None, None, False), False),
        ):
            for reverse in ((False, True) if adverse else (False,)):
                with self.subTest(case=case, reverse=reverse), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    shared = root / 'shared'
                    shared.mkdir()
                    path = shared / 'ci-pull_request-11-1.json'
                    clocks = [clock, adverse] if adverse else [clock]
                    if reverse:
                        clocks.reverse()
                    timing.save_timing_capture(path, 0, samples, [*clocks, unrelated], [])
                    self.assertEqual(qualified, timing.index_capture(path, samples, clocks)[0])
                    original = path.read_bytes()
                    saved = root / '.reports/timing/saved.json'
                    timing.save_review_archive(saved, {'kind': timing.ARCHIVE_KIND, 'samples': samples, 'hosted': [clock]})
                    saved_bytes = saved.read_bytes()
                    project = root / 'project'
                    project.mkdir()
                    (project / 'tasks.json').write_bytes(timing.json_bytes({'tasks': []}))
                    (project / 'feedback.json').write_bytes(timing.json_bytes({'items': []}))

                    def ordinary(*argv):
                        out = StringIO()
                        with patch.object(sys, 'argv', ['timing.py', *argv]), redirect_stdout(out):
                            timing.main()
                        return json.loads(out.getvalue())

                    with (patch.object(timing, 'ROOT', root),
                          patch.object(timing, 'shared_timing_directory', return_value=shared),
                          patch.object(timing, 'weekly_review', return_value=None),
                          patch.object(timing, 'command', side_effect=AssertionError('Unexpected hosted request'))):
                        self.assertEqual(expected, measurements(ordinary()))
                        for number in range(12, 12 + timing.MAX_RECENT):
                            newer = shared / f'ci-pull_request-{number}-1.json'
                            timing.save_timing_capture(newer, 0, [], [dict(clock, run_id=str(number))], [])
                        sentinel = shared / 'ci-pull_request-10-1.json'
                        sentinel.write_bytes(b'{')
                        self.assertNotIn(path, timing.incremental_timing_paths([shared]))
                        with patch.object(timing, 'read_timing_capture', wraps=timing.read_timing_capture) as read:
                            reopened = root / 'reopened.json'
                            after = ordinary('--save', str(reopened))
                        self.assertEqual(expected, measurements(after))
                        self.assertEqual(expected, measurements(ordinary('--report', str(reopened))))
                        self.assertIsNone(after['recent_collection'])
                        paths = [call.args[0] for call in read.call_args_list]
                        self.assertEqual(timing.MAX_RECENT + 1, len(paths))
                        self.assertEqual(1, paths.count(path))
                        self.assertNotIn(sentinel, paths)
                        self.assertNotIn(unrelated['head_sha'], {row['head_sha'] for row in after['hosted']})
                        with patch.object(timing, 'read_timing_capture', wraps=timing.read_timing_capture) as read:
                            rows, raw_clocks, issues = timing.retained_budget_inputs(root, feedback)
                        self.assertEqual(expected, measurements(timing.timing_view(rows, hosted=raw_clocks, issues=issues)))
                        self.assertEqual(timing.MAX_RECENT + 1, read.call_count)
                        self.assertEqual(1, sum(call.args[0] == path for call in read.call_args_list))
                    self.assertEqual(original, path.read_bytes())
                    self.assertEqual(saved_bytes, saved.read_bytes())

    def test_retained_attempt_lookup_is_bounded_and_needs_matching_raw_evidence(self):
        clock = timing.hosted_timing(hosted_run(), self.split_windows_jobs())
        report = split_harness_report(675)
        report['ci'] = {**dict(zip(timing.ATTEMPT_IDENTITY, timing.ci_key(clock))), 'checkout_sha': 'a' * 40}
        sample = timing.report_sample(report)
        prior, = timing.timing_view([sample], hosted=[clock])['samples']
        self.assertIsNone(prior['review_exclusion'])
        with tempfile.TemporaryDirectory() as directory:
            retained = Path(directory)
            path = retained / 'ci-pull_request-11-1.json'
            paths = timing.retained_attempt_paths([retained], [prior], [])
            self.assertEqual({path}, set(paths))
            rows, clocks = timing.retained_capture_inputs(paths, {}, [], [], budget=timing.TimingReadBudget())
            portable, = timing.timing_view([prior, *rows], hosted=clocks)['samples']
            self.assertIsNone(portable['seconds']['total'])
            self.assertIsNotNone(portable['review_exclusion'])
            timing.save_timing_capture(path, 0, [sample], [clock], [])
            budget = timing.TimingReadBudget()
            budget.remaining = 64
            issues = []
            self.assertEqual(([], []), timing.retained_capture_inputs(paths, {}, [], issues, budget=budget))
            self.assertIn('combined timing read limit', issues[0]['error'])
            rows, clocks = timing.retained_capture_inputs(paths, {}, [], [], budget=timing.TimingReadBudget())
            measured, = timing.timing_view(rows, hosted=clocks)['samples']
            self.assertEqual(765, measured['seconds']['total'])
            self.assertIsNone(measured['review_exclusion'])

    def test_component_cache_union_preserves_protected_elapsed_observation(self):
        jobs = self.split_windows_jobs()
        clock = timing.hosted_timing(hosted_run(), jobs)
        report = split_harness_report(675)
        report['selection']['profile'] = 'full'
        report['ci'] = {**dict(zip(timing.ATTEMPT_IDENTITY, timing.ci_key(clock))), 'checkout_sha': 'a' * 40}
        states = dict(core='warm', browser='warm', python='not_requested', recovery='warm')
        full = dict(timing.report_sample(report, cache='warm'), windows_caches=states)
        browser_unknown = dict(full, cache='unknown', windows_caches=dict(states, browser='unknown'))
        recovery_unknown = dict(full, cache='unknown', windows_caches=dict(states, recovery='unknown'))
        browser_cold = dict(full, cache='unknown', windows_caches=dict(states, browser='cold'))
        for rows, cache, conflict in (([browser_unknown, recovery_unknown], 'warm', False),
                                      ([full, browser_cold], 'unknown', True)):
            for observations in ([*rows, rows[0]], rows[::-1]):
                view = timing.timing_view(observations, hosted=[clock])
                observed, = view['samples']
                self.assertEqual((765, cache, conflict), (observed['seconds']['total'], observed['cache'],
                                                         observed.get('cache_conflict', False)))
                self.assertEqual('full', view['objective_reviews'][0]['profile'])
                if conflict:
                    self.assertEqual([], view['reviews'])
                    self.assertEqual(timing.WINDOWS_PRODUCERS['browser'], view['input_issues'][0]['producer'])
                else:
                    self.assertEqual(['warm'], [review['cache'] for review in view['reviews']])
                    self.assertEqual([], view['input_issues'])

    def test_job_fields_join_by_identity_without_voting_away_independent_measurements(self):
        jobs = self.split_windows_jobs()
        run = hosted_run()
        first = timing.hosted_timing(run, jobs)
        report = split_harness_report(675)
        report['selection']['profile'] = 'full'
        report['ci'] = {**dict(zip(timing.ATTEMPT_IDENTITY, timing.ci_key(first))), 'checkout_sha': 'a' * 40}
        full = timing.report_sample(report)
        browser = lane_sample(full, 'browser')
        skip_step, shorter_step = [], []
        for job in jobs:
            steps = job['steps']
            if job['name'] == timing.WINDOWS_PRODUCERS['browser']:
                install = next(step for step in steps if step['name'].startswith('Install'))
                skip_step.append(dict(job, steps=[{**step, 'completed_at': None} if step is install else step
                                                  for step in steps]))
                shorter_step.append(dict(job, steps=[{**step, 'completed_at': '2026-11-20T00:01:25Z'}
                                                     if step is install else step for step in steps]))
            else:
                skip_step.append(job)
                shorter_step.append(job)
        missing_core = lambda jobs: [job for job in jobs if job['name'] != timing.WINDOWS_PRODUCERS['core']]
        for variant, expected_step in ((skip_step, 30), (shorter_step, None),
                                       (missing_core(skip_step), 30), (missing_core(shorter_step), None)):
            other = timing.hosted_timing(run, variant[::-1])
            for evidence in ([first, other, first], [other, first, other]):
                with self.subTest(step=expected_step, reverse=evidence[0] is other):
                    view = timing.timing_view([full, browser], hosted=evidence)
                    rows = {row['profile']: row for row in view['samples']}
                    self.assertEqual((765, 720), tuple(row['seconds']['total'] for row in rows.values()))
                    self.assertTrue(all(row['hosted_success_complete'] for row in rows.values()))
                    self.assertEqual([expected_step, expected_step],
                                     [row['seconds']['browser_step'] for row in rows.values()])
                    scopes = {(note.get('platform'), note.get('producer'), tuple(note.get('fields', ())))
                              for note in view['input_issues']}
                    self.assertEqual({('Windows', None, ('browser_step',)),
                                      (None, timing.WINDOWS_PRODUCERS['browser'], ('browser_step',))}
                                     if expected_step is None else set(), scopes)

    def test_protected_job_uses_known_facts_from_partial_membership(self):
        jobs = self.split_windows_jobs()
        run = hosted_run()
        full_clock = timing.hosted_timing(run, jobs)
        report = split_harness_report(675)
        report['selection']['profile'] = 'full'
        report['ci'] = {**dict(zip(timing.ATTEMPT_IDENTITY, timing.ci_key(full_clock))), 'checkout_sha': 'a' * 40}
        windows = timing.report_sample(report)
        linux = dict(windows, platform='Linux', run_id=fast_report(20, 675)['run_id'])
        original_jobs = [job for job in jobs if job['name'] != timing.WINDOWS_PRODUCERS['core']]
        late_jobs = [dict(job, completed_at='2026-11-20T00:16:30Z') if job['id'] == 31 else job
                     for job in original_jobs]
        blank_jobs = [dict(job, completed_at=None) if job['id'] == 31 else job for job in jobs]
        partial = timing.hosted_timing(run, original_jobs)
        late = timing.hosted_timing(run, late_jobs)
        blank = timing.hosted_timing(run, blank_jobs)
        self.assertEqual(2, len(blank['jobs']))
        self.assertEqual(510, partial['jobs'][0]['seconds']['total'])
        self.assertEqual(990, late['jobs'][0]['seconds']['total'])
        for claims, expected in (([full_clock, partial], 510), ([blank, partial], 510),
                                 ([blank, late], 990), ([full_clock, late], None)):
            for evidence in ([*claims, claims[0]], [*reversed(claims), claims[1]]):
                with self.subTest(expected=expected, reversed=evidence[0] is claims[1]):
                    view = timing.timing_view([linux, windows], hosted=evidence)
                    rows = {row['platform']: row for row in view['samples']}
                    self.assertEqual((expected, 765), (rows['Linux']['seconds']['total'], rows['Windows']['seconds']['total']))
                    self.assertEqual((10, 40), (rows['Linux']['seconds']['queue'], rows['Linux']['seconds']['setup']))
                    budget = {row['platform']: row for row in view['objective_reviews']}
                    self.assertEqual((int(expected is not None), int(expected == 990)),
                                     (budget['Linux']['sample_count'], budget['Linux']['recent_objective_breaches']))
                    self.assertEqual(1, budget['Windows']['sample_count'])
                    issues = [note for note in view['input_issues'] if note.get('platform') == 'Linux']
                    self.assertEqual(expected is None, bool(issues))
                    if issues:
                        self.assertIn('total', issues[0]['fields'])

    def test_matched_lane_can_fill_only_its_existing_aggregate_cache_field(self):
        clock = timing.hosted_timing(hosted_run(), self.split_windows_jobs())
        report = split_harness_report(675)
        report['selection']['profile'] = 'full'
        report['source']['sha256'] = 'c' * 64
        report['ci'] = {**dict(zip(timing.ATTEMPT_IDENTITY, timing.ci_key(clock))), 'checkout_sha': 'a' * 40}
        caches = dict(core='partial', browser='unknown', python='not_requested', recovery='partial')
        full = dict(timing.report_sample(report), windows_caches=caches)
        browser = {key: value for key, value in full.items() if key != 'windows_caches'}
        browser = dict(lane_sample(browser, 'browser'), cache='partial')
        variants = (
            ([browser], 'partial', False),
            ([dict(browser, arch='x86_64')], 'partial', False),
            ([dict(browser, arch='ARM64')], 'unknown', False),
            ([dict(browser, run_id=fast_report(19, 675)['run_id'])], 'unknown', False),
            ([dict(browser, inputs_sha256='e' * 64)], 'unknown', False),
            ([dict(browser, checks=[{**row, 'duration_seconds': 17} for row in browser['checks']])], 'unknown', False),
            ([dict(browser, checks=[*browser['checks'], browser['checks'][0]])], 'unknown', False),
            ([browser, dict(browser, cache='cold')], 'unknown', True),
        )
        for producers, cache, conflict in variants:
            for records in ([full, *producers, producers[0]], [*reversed(producers), full]):
                view = timing.timing_view(records, hosted=[clock])
                protected, = [row for row in view['samples'] if row['profile'] == 'full']
                self.assertEqual((765, cache, conflict),
                                 (protected['seconds']['total'], protected['cache'], protected.get('cache_conflict', False)))
                self.assertEqual('full', view['objective_reviews'][0]['profile'])
                self.assertEqual([] if conflict else [cache], [row['cache'] for row in view['reviews']])

    def test_untrusted_split_reports_keep_harness_data_without_hosted_attribution(self):
        clock = timing.hosted_timing(hosted_run(), self.split_windows_jobs())
        report = split_harness_report(675)
        report['selection']['profile'] = 'full'
        report['source']['sha256'] = 'c' * 64
        report['ci'] = {**dict(zip(timing.ATTEMPT_IDENTITY, timing.ci_key(clock))), 'checkout_sha': 'a' * 40}
        full = timing.report_sample(report)
        wrong_lane = dict(lane_sample(full, 'recovery'), profile='ci-windows-browser', cache='warm')
        browser = dict(lane_sample(full, 'browser'), cache='warm')
        for rows, cache, total in (([wrong_lane, full], 'unknown', None),
                                   ([wrong_lane, full, browser], 'unknown', 720)):
            view = timing.timing_view(rows, hosted=[clock])
            aggregate, = [row for row in view['samples'] if row['profile'] == 'full']
            self.assertEqual((765, cache), (aggregate['seconds']['total'], aggregate['cache']))
            self.assertEqual(total, next((row['seconds']['total'] for row in view['samples']
                                          if row['selected'] == ['workspace-timeframe']), None))
            self.assertTrue(any('native publisher identity' in issue['error'] for issue in view['input_issues']))
        for bad in (dict(full, arch='ARM64'), dict(full, run_id=fast_report(19, 675)['run_id'])):
            view = timing.timing_view([bad], hosted=[clock])
            sample, = view['samples']
            self.assertEqual((675, None), (sample['seconds']['harness'], sample['seconds']['total']))
            self.assertFalse(view['objective_reviews'])

    def test_native_child_selection_cannot_displace_the_parent_report(self):
        clock = timing.hosted_timing(hosted_run(), self.split_windows_jobs())
        report = split_harness_report(675)
        report['ci'] = {**dict(zip(timing.ATTEMPT_IDENTITY, timing.ci_key(clock))), 'checkout_sha': 'a' * 40}
        full = timing.report_sample(report)
        linux = dict(full, platform='Linux', run_id=fast_report(20, 675)['run_id'])
        for claim in ('full', 'records'):
            producer = dict(lane_sample(full, 'recovery'), profile=claim, cache='partial')
            for samples in ([linux, full, producer], [producer, full, linux, producer]):
                view = timing.timing_view(samples, hosted=[clock])
                windows = [row for row in view['samples'] if row['platform'] == 'Windows']
                expected = {('full', len(full['selected']), 765, 'unknown'), (claim, 1, None, 'unknown')}
                measured = {(row['profile'], len(row['selected']), row['seconds']['total'], row['cache'])
                            for row in windows}
                self.assertEqual(expected, measured)
                indexable = timing.indexable_capture_samples(Path('ci-pull_request-11-1.json'), samples, [clock], [])
                self.assertCountEqual([linux, full], indexable)
        invalid = [dict(lane_sample(full, 'recovery'), profile=claim) for claim in ('full', 'records')]
        observations = timing.capture_observations(timing.ci_key(clock), [linux, *invalid, full], [clock], [])
        self.assertEqual(set(), observations.missing)
        self.assertTrue(observations.complete_clock)
        self.assertFalse(observations.ambiguous)

    def test_producer_cache_dispute_preserves_wall_objective_without_cache_cohort(self):
        *siblings, aggregate = self.split_windows_jobs()
        clock = timing.hosted_timing(hosted_run(), [*siblings, aggregate])
        report = split_harness_report(675)
        report['selection']['profile'] = 'full'
        report['source']['sha256'] = 'c' * 64
        report['ci'] = {**dict(zip(timing.ATTEMPT_IDENTITY, timing.ci_key(clock))), 'checkout_sha': 'a' * 40}
        full = timing.report_sample(report, cache='warm')
        browser = dict(lane_sample(full, 'browser'), cache='partial')
        for observations in ([full, browser], [browser, full]):
            view = timing.timing_view(observations, hosted=[clock])
            protected, = [row for row in view['samples'] if row['profile'] == 'full']
            self.assertEqual((765, 'unknown', True),
                             (protected['seconds']['total'], protected['cache'], protected['cache_conflict']))
            self.assertEqual([], view['reviews'])
            self.assertEqual('full', view['objective_reviews'][0]['profile'])
            issue, = view['input_issues']
            self.assertEqual(timing.WINDOWS_PRODUCERS['browser'], issue['producer'])

    def test_scoped_windows_counts_launched_setup_siblings_and_uses_only_the_core_harness(self):
        run = dict(hosted_run(), event='push')
        result = timing.hosted_timing(run, self.split_windows_jobs(scoped=True))
        self.assertTrue(timing.clock_complete(result))
        self.assertEqual(510, result['workflow']['total_elapsed_seconds'])
        self.assertEqual(679, result['workflow']['runner_work_seconds'])
        windows, = (row for row in result['jobs'] if row['platform'] == 'Windows')
        self.assertEqual((8, 85, 179, 165, None),
                         tuple(windows['seconds'][key] for key in ('queue', 'harness_step', 'runner', 'total', 'browser_step')))

    def test_split_windows_leaves_missing_job_identity_and_clock_explicit(self):
        linux, core, browser, python, aggregate = self.split_windows_jobs()
        for jobs in ([linux, aggregate], [linux, core, browser, aggregate],
                     [linux, core, dict(browser, run_attempt=2), python, aggregate]):
            with self.subTest(job_count=len(jobs), jobs=[row['name'] for row in jobs]):
                result = timing.hosted_timing(hosted_run(), jobs)
                self.assertFalse(result['workflow']['identity_complete'])
                self.assertIsNone(result['workflow']['total_elapsed_seconds'])
                self.assertIsNone(result['workflow']['runner_work_seconds'])
                self.assertFalse(timing.clock_complete(result))

        result = timing.hosted_timing(hosted_run(), [linux, core, dict(browser, completed_at=None), python, aggregate])
        windows, = (row for row in result['jobs'] if row['platform'] == 'Windows')
        self.assertTrue(result['workflow']['identity_complete'])
        self.assertEqual(765, result['workflow']['total_elapsed_seconds'])
        self.assertIsNone(result['workflow']['runner_work_seconds'])
        self.assertIsNone(windows['seconds']['queue'])
        self.assertIsNone(windows['seconds']['harness_step'])
        self.assertFalse(timing.clock_complete(result))

    def test_retained_split_clocks_require_each_named_producer_once(self):
        for scoped in (False, True):
            valid = timing.hosted_timing(hosted_run(), self.split_windows_jobs(scoped=scoped))
            self.assertTrue(timing.clock_complete(valid))
            core, browser, python = valid['sibling_jobs']
            for case, siblings in (('missing', [core, browser]),
                                   ('duplicate', [core, browser, python, dict(python, job_id='36')]),
                                   ('renamed', [core, browser, dict(python, name='Other Windows job')])):
                with self.subTest(scoped=scoped, case=case):
                    retained = json.loads(timing.json_bytes({**valid, 'sibling_jobs': siblings}))
                    self.assertFalse(timing.clock_complete(retained))

    def test_jobs_use_wall_boundaries_for_queue_setup_harness_tail_and_total(self):
        linux = hosted_job(31, 'ubuntu', '00:10', '04:00', 'Run pnpm check', '00:30', '03:50')
        windows = hosted_job(32, 'windows', '00:20', '06:00',
                             'Run uv run python scripts/check.py --require-windows', '00:50', '05:20')
        result = timing.hosted_timing(hosted_run(), [linux, windows])
        self.assertCountEqual(
            [tuple(job['seconds'][key] for key in ('queue', 'setup', 'harness_step', 'tail', 'runner', 'total'))
             for job in result['jobs']],
            [(10, 20, 200, 10, 230, 240), (20, 30, 270, 40, 340, 360)],
        )
        self.assertTrue(result['workflow']['matrix_complete'])
        self.assertEqual(360, result['workflow']['total_elapsed_seconds'])
        self.assertEqual(570, result['workflow']['runner_work_seconds'])

    def test_skipped_harness_leaves_its_stage_clocks_blank(self):
        linux = hosted_job(31, 'ubuntu', '00:10', '04:00', 'Run pnpm check', '00:30', '03:50')
        linux['steps'][0]['conclusion'] = 'skipped'
        job, = timing.hosted_timing(hosted_run(), [linux])['jobs']
        self.assertEqual(230, job['seconds']['runner'])
        self.assertEqual(240, job['seconds']['total'])
        for segment in ('setup', 'harness_step', 'tail'):
            with self.subTest(segment=segment):
                self.assertIsNone(job['seconds'][segment])

    def test_missing_timestamps_and_rerun_queue_are_explicit(self):
        linux = hosted_job(31, 'ubuntu', '00:10', '04:00', 'Run pnpm check', '00:30', '03:50')
        linux.update(run_attempt=2, completed_at=None, status='in_progress', conclusion=None)
        linux['steps'][0].update(completed_at=None, status='in_progress', conclusion=None)
        linux['steps'].append({
            'name': 'Verify the selected task', 'status': 'queued', 'conclusion': None,
            'started_at': None, 'completed_at': None,
        })
        run = dict(hosted_run(), run_attempt=2, status='in_progress', conclusion=None)
        metrics, = timing.hosted_timing(run, [linux])['jobs']
        for segment in ('queue', 'harness_step', 'tail', 'runner', 'total'):
            with self.subTest(segment=segment):
                self.assertIsNone(metrics['seconds'][segment])
        self.assertEqual(20, metrics['seconds']['setup'])

    def test_inconsistent_job_identity_keeps_clocks_diagnostic(self):
        linux = hosted_job(31, 'ubuntu', '00:10', '04:00', 'Run pnpm check', '00:30', '03:50')
        windows = hosted_job(32, 'windows', '00:20', '06:00',
                             'Run uv run python scripts/check.py --require-windows', '00:50', '05:20')
        for conflicting in (
            {'run_id': 12},
            {'run_attempt': 2},
            {'head_sha': 'b' * 40},
            {'run_url': 'https://api.github.com/repos/another/scopehub/actions/runs/11'},
        ):
            with self.subTest(conflicting=conflicting):
                workflow = timing.hosted_timing(hosted_run(), [dict(linux, **conflicting), windows])
                self.assertFalse(workflow['workflow']['identity_complete'])
                self.assertFalse(workflow['workflow']['matrix_complete'])
                job = next(job for job in workflow['jobs'] if job['platform'] == 'Linux')
                self.assertTrue(job['identity_issues']['conflicts'])
                self.assertEqual(230, job['seconds']['runner'])

        del linux['run_attempt']
        workflow = timing.hosted_timing(hosted_run(), [linux, windows])
        self.assertFalse(workflow['workflow']['identity_complete'])
        self.assertEqual(['run_attempt'], workflow['jobs'][0]['identity_issues']['missing'])
        self.assertEqual(230, workflow['jobs'][0]['seconds']['runner'])

    def test_hosted_review_requires_workflow_success_and_respects_timing_coverage(self):
        report = historical_report()
        report['source']['git_head'] = 'b' * 40
        report['ci'] = {
            'repository': 'fixture/scopehub', 'run_id': '11', 'run_attempt': '1',
            'event': 'pull_request', 'head_sha': 'a' * 40, 'checkout_sha': 'b' * 40,
        }
        run = hosted_run()
        linux = hosted_job(31, 'ubuntu', '00:10', '04:00', 'Run pnpm check', '00:30', '03:50')
        windows = hosted_job(32, 'windows', '00:20', '06:00',
                             'Run uv run python scripts/check.py --require-windows', '00:50', '05:20')

        def observe(raw_run, jobs):
            workflow = timing.hosted_timing(raw_run, jobs)
            return timing.timing_view([timing.report_sample(report)], hosted=[workflow])

        success = observe(run, [linux, windows])
        self.assertEqual(1, len(success['reviews']))
        total = next(metric for metric in success['reviews'][0]['metrics'] if metric['metric'] == 'total')
        self.assertEqual(900, total['objective_seconds'])
        for view in (
            observe(dict(run, conclusion='failure'), [linux, dict(windows, conclusion='failure')]),
            observe(run, [linux]),
        ):
            self.assertEqual([], view['reviews'])
            sample, = view['samples']
            self.assertEqual(240, sample['seconds']['total'])
            self.assertIsNotNone(sample['review_exclusion'])

        del report['checks'][1]['duration_seconds']
        partial = observe(run, [linux, windows])
        metrics = {metric['metric']: metric for metric in partial['reviews'][0]['metrics']}
        self.assertEqual(1, metrics['total']['sample_count'])
        self.assertEqual(0, metrics['check_work']['sample_count'])
        self.assertIsNone(metrics['check_work']['recent_median_seconds'])


class PRMainComparisonTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(isolated_git_environment())
        temp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(temp)
        self.git('init', '-q')
        self.tree = self.git('mktree')
        self.base = self.commit('base')
        self.head = self.commit('PR head', self.base)
        self.preview = self.commit('PR preview', self.base, self.head)
        self.main = self.commit('main merge', self.base, self.head)

    def git(self, *args, contents=''):
        # mktree treats a carriage return before LF as part of the entry name.
        result = subprocess.run(
            ['git', '-c', 'user.name=Timing Fixture', '-c', 'user.email=timing@example.test',
             *args], cwd=self.root, input=contents.encode('utf-8'), capture_output=True, check=True,
        )
        return os.fsdecode(result.stdout).strip()

    def commit(self, message, *parents, tree=None):
        return self.git('commit-tree', tree or self.tree,
                        *(arg for parent in parents for arg in ('-p', parent)), '-m', message)

    def publishing_tree(self, catalog):
        blob = self.git('hash-object', '-w', '--stdin', contents=json.dumps(catalog))
        project = self.git('mktree', contents=f'100644 blob {blob}\tchecks.json\n')
        policy = self.git('hash-object', '-w', '--stdin', contents='INPUT_EXCLUSIONS = []')
        scripts = self.git('mktree', contents=f'100644 blob {policy}\tproject.py\n')
        return self.git('mktree', contents=f'040000 tree {project}\tproject\n040000 tree {scripts}\tscripts\n')

    def test_selection_comes_from_committed_publishing_catalog(self):
        full = (*BASIC_CHECKS, 'generated-contracts', 'windows-native-build')
        catalog = fixture_catalog(full, non_change=('release-artifacts',))
        newer = fixture_catalog((*full, 'architecture'), non_change=('release-artifacts',))

        publisher = self.commit('Publishing checkout', self.head, tree=self.publishing_tree(catalog))
        newer_head = self.commit('Other catalog', self.head, tree=self.publishing_tree(newer))
        self.assertEqual(timing.git(self.root, 'ls-tree', '-r', '-z', '--name-only', publisher),
                         b'project/checks.json\0scripts/project.py\0')
        self.assertEqual(timing.git(self.root, 'cat-file', 'blob', f'{publisher}:project/checks.json'),
                         json.dumps(catalog).encode('utf-8'))
        load = timing.native_publishing_catalog
        self.enterContext(patch.object(timing, 'native_publishing_catalog',
                                       side_effect=lambda revision, **options: load(revision, root=self.root, **options)))
        snapshot = timing.committed_publishing_inputs
        self.enterContext(patch.object(timing, 'committed_publishing_inputs',
                                       side_effect=lambda revision: snapshot(revision, root=self.root)))
        report = fast_report(20, 190)
        report['environment'].update(system='Windows', machine='AMD64')
        # The weekly job runs without the locked project environment. Exercise the
        # script import mode in a fresh interpreter with no site packages.
        weekly = subprocess.run(
            [sys.executable, '-I', '-S', '-c',
             'import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); '
             'from timing import committed_publishing_inputs; '
             'print(committed_publishing_inputs(sys.argv[3], root=Path(sys.argv[2])))',
             str(timing.ROOT / 'scripts'), str(self.root), publisher],
            text=True, capture_output=True,
        )
        self.assertEqual(weekly.returncode, 0, weekly.stderr)
        published_digest = snapshot(publisher, root=self.root)
        self.assertEqual(weekly.stdout.strip(), published_digest)
        newer_digest = snapshot(newer_head, root=self.root)
        self.assertIsNotNone(published_digest)
        self.assertIsNotNone(newer_digest)
        self.assertNotEqual(published_digest, newer_digest)
        report['source'].update(git_head=publisher, sha256=published_digest)
        report['ci'] = {'repository': 'fixture/scopehub', 'run_id': '11', 'run_attempt': '1',
                        'event': 'pull_request', 'head_sha': newer_head, 'checkout_sha': publisher}
        report['selection'].update(profile='full', checks=list(full))
        report['checks'] = [dict(report['checks'][0], id=key) for key in full]
        report['checks'].append({'id': 'release-artifacts', 'status': 'blocked', 'reason_code': 'not_selected'})
        original = timing.report_sample(report)
        self.assertFalse(timing.native_report_issue(original, None, []))
        for omitted in full:
            with self.subTest(omitted=omitted):
                broken = dict(original, selected=[key for key in original['selected'] if key != omitted],
                              checks=[row for row in original['checks'] if row['id'] != omitted])
                self.assertTrue(timing.native_report_issue(broken, None, []))
        surplus = dict(original, selected=[*original['selected'], 'release-artifacts'], checks=[
            dict(row, status='passed', exit_code=0, reason_code=None) if row['id'] == 'release-artifacts' else row
            for row in original['checks']
        ])
        self.assertTrue(timing.native_report_issue(surplus, None, []))
        missing = 'f' * 40
        expired = dict(original, source_commit=missing,
                       ci=dict(original['ci'], checkout_sha=missing, head_sha=publisher))
        self.assertFalse(timing.native_report_issue(expired, None, []))
        self.assertTrue(timing.native_report_issue(dict(expired, inputs_sha256=newer_digest), None, []))
        issues = []
        self.assertTrue(timing.native_report_issue(dict(expired, ci=dict(expired['ci'], head_sha=missing)), None, issues))
        self.assertIn('publishing catalog is unavailable', issues[0]['error'])

    def test_weekly_receipts_require_a_commit_or_genuinely_missing_checkout(self):
        tree = self.publishing_tree(fixture_catalog((*BASIC_CHECKS, 'generated-contracts')))
        head = self.commit('Topic', self.base, tree=tree)
        preview = self.commit('Merge preview', self.base, head, tree=tree)
        missing = 'f' * 40
        missing_head = 'e' * 40
        null = '0' * 40
        self.assertEqual('commit', self.git('cat-file', '-t', preview))
        self.assertEqual('tree', self.git('cat-file', '-t', tree))
        for name in (missing, missing_head, null):
            self.assertEqual(f'{name} missing', self.git('cat-file', '--batch-check', contents=f'{name}\n'))
        inputs = timing.committed_publishing_inputs(head, root=self.root)
        self.assertIsNotNone(inputs)
        jobs = [
            hosted_job(31, 'ubuntu', '00:10', '04:00', 'Run pnpm check', '00:30', '03:50'),
            hosted_job(32, 'windows', '00:20', '06:00', 'Run pnpm check', '00:50', '05:20'),
        ]
        environment = {'GITHUB_ACTIONS': 'true', 'GITHUB_REPOSITORY': 'fixture/scopehub', 'GH_TOKEN': 'fixture'}
        artifact = {'name': 'verification-timing-windows-latest', 'expired': False, 'size_in_bytes': 1024}
        for kind, checkout, topic, proof, seconds in (
            ('commit', preview, head, True, 360), ('missing', missing, head, False, 360),
            ('missing-with-proof', missing, missing_head, True, 360), ('tree', tree, head, True, None),
            ('null-without-proof', null, head, False, None), ('null-with-proof', null, missing_head, True, None),
        ):
            with self.subTest(kind=kind):
                run = dict(hosted_run(), head_sha=topic)
                clock = timing.hosted_timing(run, [dict(job, head_sha=topic) for job in jobs])
                report = fast_report(20, 190)
                report['environment'].update(system='Windows', machine='AMD64')
                report['selection']['profile'] = 'full'
                report['selection']['checks'].append('generated-contracts')
                report['checks'].append(dict(report['checks'][0], id='generated-contracts'))
                report['source'].update(git_head=preview, sha256=inputs, working_tree_dirty=False)
                report['ci'] = {'repository': 'fixture/scopehub', 'run_id': '11', 'run_attempt': '1',
                                'event': 'pull_request', 'head_sha': topic, 'checkout_sha': preview}
                compact = timing.report_sample(report)
                compact['source_commit'] = compact['ci']['checkout_sha'] = checkout
                if proof:
                    compact['generated_contracts'] = {
                        'checkout': checkout, 'tree': tree, 'parents': [self.base, topic],
                        'comparison_base': self.base, 'ref': 'refs/pull/84/merge',
                        'command': timing.GENERATED_COMMAND,
                        'tools': {'python': '3.12.0', 'rustfmt': 'rustfmt 1.8.0 (abcdef 2026-01-01)'},
                    }
                digest = sha256(json.dumps(compact, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
                expected = [{**compact['ci'], 'platform': 'Windows', 'report_id': report['run_id'],
                             'report_input_sha256': digest}] if seconds is not None else []
                archive, summary = (self.root / f'{kind}.{ext}' for ext in ('json', 'md'))
                argv = ['timing.py', '--recent', '12', '--github-actions', '--save', str(archive),
                        '--summary', str(summary)]
                load = timing.native_publishing_catalog
                fingerprint = timing.committed_publishing_inputs

                def actions_command(args):
                    if args[:2] == ['gh', 'api']:
                        return json.dumps({'artifacts': [artifact]})
                    self.assertEqual(['gh', 'run', 'download', '11'], args[:4])
                    downloaded = Path(args[args.index('--dir') + 1]) / timing.TIMING_FILE
                    downloaded.write_text(json.dumps(compact), encoding='utf-8')
                    return ''

                with (patch.object(sys, 'argv', argv), patch.dict(os.environ, environment),
                      redirect_stdout(StringIO()) as stdout,
                      patch.object(timing, 'native_publishing_catalog',
                                   side_effect=lambda rev, **options: load(rev, root=self.root, **options)),
                      patch.object(timing, 'committed_publishing_inputs',
                                   side_effect=lambda rev: fingerprint(rev, root=self.root)),
                      patch.object(timing, 'collection_source', return_value=preview),
                      patch.object(timing, 'local_paths', return_value=[]),
                      patch.object(timing, 'timing_directories', return_value=()),
                      patch.object(timing, 'recent_runs', return_value=[run]),
                      patch.object(timing, 'command', side_effect=actions_command),
                      patch.object(timing, 'inspect_jobs', return_value=clock)):
                    timing.main()

                view = json.loads(stdout.getvalue())
                saved = json.loads(archive.read_bytes())
                self.assertEqual(expected, view['recent_collection']['collected_samples'])
                self.assertEqual(expected, saved['recent_collection']['collected_samples'])
                if checkout == null:
                    self.assertEqual([], view['samples'])
                    self.assertEqual([], view['objective_reviews'])
                    self.assertEqual(['11'], view['recent_collection']['missing_compact_run_ids'])
                    self.assertIn('No qualified elapsed samples', summary.read_text(encoding='utf-8'))
                    continue
                sample, = view['samples']
                self.assertEqual(seconds, sample['seconds']['total'])
                self.assertEqual(1, len(view['hosted']))
                if seconds is None:
                    self.assertIn('publishing catalog is unavailable', view['input_issues'][0]['error'])
                    self.assertNotIn('recent median 360s', summary.read_text(encoding='utf-8'))
                else:
                    self.assertEqual([], view['input_issues'])
                    self.assertIn('recent median 360s', summary.read_text(encoding='utf-8'))

    def observe(self, commit, *, push=False, edit_report=None, rustfmt=None, platform='ubuntu', ref=None):
        run_id = 12 if push else 11
        run = dict(hosted_run(run_id=run_id), event='push' if push else 'pull_request',
                   head_sha=commit if push else self.head)
        jobs = [
            dict(hosted_job(run_id * 10 + index, name, '00:10', '04:00', 'Run pnpm check',
                            '00:30', '03:50', run_id=run_id), head_sha=run['head_sha'])
            for index, name in enumerate(('ubuntu', 'windows'))
        ]
        report = fast_report(20, 190)
        report['source'].update(git_head=commit, sha256='c' * 64, working_tree_dirty=False)
        report['selection']['profile'] = 'full'
        report['selection']['checks'].append('generated-contracts')
        report['checks'].append({'id': 'generated-contracts', 'status': 'passed', 'exit_code': 0,
                                 'command': ['/fixture/python', 'scripts/generate_contracts.py', '--check'],
                                 'duration_seconds': 5 if push else 3})
        report['environment'].update(python='3.12.12', python_executable='/fixture/python')
        if platform == 'windows':
            report['environment'].update(system='Windows', machine='AMD64')
        report['ci'] = {'repository': 'fixture/scopehub', 'run_id': str(run_id), 'run_attempt': '1',
                        'event': run['event'], 'head_sha': run['head_sha'], 'checkout_sha': commit}
        if edit_report:
            edit_report(report)
        self.git('update-ref', '--no-deref', 'HEAD', commit)
        env = {'GITHUB_SHA': commit, 'GITHUB_BASE_REF': 'main',
               'SCOPEHUB_SCOPE_BASE': self.base, 'SCOPEHUB_SCOPE_OUTCOME': 'success',
               'GITHUB_REF': ref or ('refs/heads/main' if push else 'refs/pull/52/merge')}
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / 'verification'
            destination.mkdir()
            (destination / 'test-report.json').write_text(json.dumps(report), encoding='utf-8')
            with (patch.object(ci_artifacts, 'ROOT', self.root),
                  patch.object(timing, 'tool_version',
                               return_value=rustfmt or 'rustfmt 1.10.0-stable (b940084d7e 2026-09-28)')):
                path, _ = ci_artifacts.stage_timing(destination, env, has_report=True)
            sample = json.loads(path.read_text(encoding='utf-8'))
        return sample, timing.hosted_timing(run, jobs)

    def compare(self, *observations):
        samples, workflows = zip(*observations)
        comparison, = timing.timing_view(samples, hosted=workflows)['pr_main_comparisons']
        return comparison

    def test_compares_only_generator_durations_on_distinct_merges_with_same_parents_and_tree(self):
        caller_git = str(self.root / 'outside.git')
        with patch.dict(os.environ, {'GIT_DIR': caller_git, 'GIT_WORK_TREE': str(self.root.parent)}):
            with isolated_git_environment():
                self.assertEqual(Path(self.git('rev-parse', '--absolute-git-dir')).resolve(),
                                 (self.root / '.git').resolve())
            self.assertEqual(os.environ['GIT_DIR'], caller_git)
        pr, main = self.observe(self.preview), self.observe(self.main, push=True)
        self.assertNotEqual(self.preview, self.main)
        proof = pr[0]['generated_contracts']
        self.assertEqual([self.base, self.head], proof['parents'])
        self.assertEqual(self.tree, proof['tree'])
        self.assertNotIn('/fixture/python', json.dumps(proof))
        old_main = {key: value for key, value in main[0].items() if key != 'generated_contracts'}
        for observations in ((pr, main, (old_main, main[1])), (pr, (old_main, main[1]), main)):
            with self.subTest(first_main=observations[1][0].keys()):
                comparison = self.compare(*observations)
                self.assertEqual(
                    {'repository': 'fixture/scopehub', 'check': 'generated-contracts', 'platform': 'Linux',
                     'main_run_id': '12', 'pr_run_id': '11', 'status': 'comparable',
                     'pr_seconds': 3, 'main_seconds': 5, 'main_minus_pr_seconds': 2},
                    comparison,
                )
        broken_main = {**main[0], 'ci': {**main[0]['ci'], 'run_id': '13'},
                       'checks': [dict(row) for row in main[0]['checks']]}
        generator = next(row for row in broken_main['checks'] if row['id'] == 'generated-contracts')
        del generator['exit_code']
        issues = []
        view = timing.timing_view([pr[0], broken_main, main[0]],
                                  hosted=[pr[1], {**main[1], 'run_id': '13'}, main[1]], issues=issues)
        comparison, = view['pr_main_comparisons']
        self.assertEqual('comparable', comparison['status'])
        self.assertEqual(1, len(issues))

    def test_missing_or_changed_proof_keeps_comparison_unknown(self):
        different_tree = self.git('mktree', contents=f'040000 tree {self.tree}\tother\n')
        other_base = self.commit('other base')
        main = self.observe(self.main, push=True)
        for name, pr in (
            ('tree', self.observe(self.commit('different tree', self.base, self.head, tree=different_tree))),
            ('base', self.observe(self.commit('different base', other_base, self.head))),
            ('reversed parents', self.observe(self.commit('reversed', self.head, self.base))),
            ('nonmerge', self.observe(self.head)),
            ('tag', self.observe(self.preview, ref='refs/tags/main')),
            ('platform', self.observe(self.preview, platform='windows')),
            ('dirty checkout', self.observe(self.preview, edit_report=lambda r: r['source'].update(working_tree_dirty=True))),
            ('different inputs', self.observe(self.preview, edit_report=lambda r: r['source'].update(sha256='d' * 64))),
            ('changed Python', self.observe(self.preview, edit_report=lambda r: r['environment'].update(python='3.12.11'))),
            ('changed rustfmt', self.observe(self.preview, rustfmt='rustfmt 1.0.0 (aaaa 2026-01-01)')),
            ('missing rustfmt', self.observe(self.preview, rustfmt='unavailable')),
            ('changed command', self.observe(self.preview, edit_report=lambda r: r['checks'][-1]['command'].pop())),
            ('different scope', self.observe(self.preview, edit_report=lambda r: r['selection'].update(profile='tooling'))),
            ('not selected', self.observe(self.preview, edit_report=lambda r: r['selection']['checks'].pop())),
            ('missing duration', self.observe(self.preview, edit_report=lambda r: r['checks'][-1].pop('duration_seconds'))),
        ):
            with self.subTest(name=name):
                comparison = self.compare(pr, main)
                self.assertEqual('unknown', comparison['status'])
                self.assertNotIn('main_minus_pr_seconds', comparison)

        # An older compact artifact is still usable for the ordinary timing reviews.
        pr_sample, workflow = self.observe(self.preview)
        del pr_sample['generated_contracts']
        self.assertEqual('unknown', self.compare((pr_sample, workflow), main)['status'])
        view = timing.timing_view([pr_sample, main[0]], hosted=[workflow, main[1]])
        self.assertEqual(2, len(view['reviews']))
        tagged = self.observe(self.main, push=True, ref='refs/tags/main')
        self.assertEqual('unknown', self.compare(self.observe(self.preview), tagged)['status'])


class TimingRegressionTests(unittest.TestCase):
    def fast_view(self, *values):
        reports = [fast_report(day, elapsed) for day, elapsed in enumerate(values, start=14)]
        return timing.timing_view([timing.report_sample(report) for report in reports])

    def test_repeated_growth_below_objective_triggers_corrective_action(self):
        view = self.fast_view(110, 115, 120, 175, 180, 185)
        review, = view['reviews']
        metric, = review['metrics']
        self.assertEqual(('fast', 'Linux'), (review['profile'], review['platform']))
        self.assertEqual('harness', metric['metric'])
        self.assertEqual('investigate', metric['status'])
        self.assertEqual(300, metric['objective_seconds'])
        self.assertEqual(0, metric['recent_objective_breaches'])
        self.assertEqual(65, metric['growth_seconds'])
        self.assertTrue(metric['material_growth'])
        self.assertIn('corrective work', metric['action'])
        follow_up = timing.budget_follow_up(view, [], {})
        growth, = follow_up['sustained_growth']
        self.assertEqual([], follow_up['repeated_breaches'])
        self.assertEqual('assignment_needed', growth['status'])
        self.assertEqual(65, growth['growth_seconds'])
        self.assertIsNotNone(follow_up['next_action'])

    def test_cumulative_scope_growth_is_visible_below_the_fast_objective(self):
        additions = ('architecture', 'generated-contracts', 'python-types', 'rust-format', 'typescript-contracts')
        samples = []
        for index, seconds in enumerate((100, 110, 115, 240, 252, 265, 280, 291)):
            report = fast_report(14 + index, seconds)
            report['source']['git_head'] = f'{index + 1:040x}'
            for check in additions[:max(0, index - 2)]:
                report['selection']['checks'].append(check)
                report['checks'].append({'id': check, 'status': 'passed', 'exit_code': 0, 'duration_seconds': 10})
            samples.append(timing.report_sample(report))

        view = timing.timing_view(samples)
        self.assertCountEqual(
            [(metric['sample_count'], metric['status']) for review in view['reviews'] for metric in review['metrics']],
            [(3, 'insufficient_samples'), *[(1, 'insufficient_samples')] * len(additions)],
        )
        objective, = view['objective_reviews']
        self.assertEqual('review_scope_growth', objective['status'])
        self.assertFalse(objective['material_growth'])
        self.assertEqual(0, objective['recent_objective_breaches'])
        self.assertEqual(191, objective['first_to_latest_elapsed_change_seconds'])
        self.assertEqual([[check] for check in additions],
                         [change['added'] for change in objective['coverage_changes']])

    def test_scope_growth_uses_an_optimized_episode_after_a_slower_history(self):
        wall_seconds = (285, 290, 288, 90, 92, 91, 120, 155, 190, 220, 250, 280)
        additions = ('architecture', 'generated-contracts', 'python-types', 'rust-format',
                     'typescript-contracts', 'ui-build')
        samples = []
        for index, seconds in enumerate(wall_seconds):
            report = fast_report(9 + index, seconds)
            report['source']['git_head'] = f'{index + 1:040x}'
            for check in additions[:max(0, index - 5)]:
                report['selection']['checks'].append(check)
                report['checks'].append({'id': check, 'status': 'passed', 'exit_code': 0, 'duration_seconds': 10})
            samples.append(timing.report_sample(report))

        objective, = timing.timing_view(samples)['objective_reviews']
        self.assertEqual('review_scope_growth', objective['status'])
        self.assertFalse(objective['material_growth'])
        self.assertEqual(0, objective['recent_objective_breaches'])
        self.assertEqual(-5, objective['first_to_latest_elapsed_change_seconds'])
        self.assertEqual(91, objective['scope_growth_baseline_median_seconds'])
        self.assertEqual(159, objective['scope_growth_median_change_seconds'])
        self.assertEqual([[check] for check in additions],
                         [change['added'] for change in objective['coverage_changes']])

    def test_single_noisy_sample_is_a_watch_and_repeated_breach_is_actionable(self):
        for values, status, breaches in (
            ((110, 115, 120, 114, 116, 330), 'watch', 1),
            ((110, 115, 120, 110, 315, 320), 'investigate', 2),
        ):
            with self.subTest(values=values):
                review, = self.fast_view(*values)['reviews']
                metric, = review['metrics']
                self.assertEqual(status, metric['status'])
                self.assertEqual(breaches, metric['recent_objective_breaches'])

    def test_offline_status_routes_comparable_hosted_growth_below_budget(self):
        use_catalog(self, fixture_catalog())
        samples, workflows = [], []
        for day, finish in enumerate(('08:50', '09:00', '09:10', '11:00', '11:10', '11:20'), start=14):
            run = hosted_run(run_id=day, day=day)
            linux = hosted_job(day * 10, 'ubuntu', '00:10', '04:00', 'Run pnpm check', '00:30', '03:50',
                               run_id=day, day=day)
            step = 'Run uv run python scripts/check.py --require-windows'
            windows = hosted_job(day * 10 + 1, 'windows', '00:20', finish, step, '00:50', '08:30',
                                 run_id=day, day=day)
            workflows.append(timing.hosted_timing(run, [linux, windows]))
            report = fast_report(day, 460)
            report['selection']['profile'] = 'full'
            report['environment']['system'] = 'Windows'
            report['ci'] = {'repository': 'fixture/scopehub', 'run_id': str(day), 'run_attempt': '1',
                            'event': 'pull_request', 'head_sha': 'a' * 40, 'checkout_sha': 'a' * 40}
            samples.append(timing.report_sample(report, cache='warm'))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / '.reports/timing/weekly.json'
            archive.parent.mkdir(parents=True)
            archive.write_bytes(timing.json_bytes({'kind': timing.ARCHIVE_KIND, 'samples': samples,
                                                  'hosted': workflows}))
            with patch.object(timing, 'command', side_effect=AssertionError('Unexpected hosted inspection')):
                found = timing.offline_budget_status(root, [], {})

        self.assertEqual([], found['repeated_breaches'])
        growth, = found['sustained_growth']
        self.assertEqual(('Windows', 'total', 900, 130), (growth['platform'], growth['metric'],
                                                        growth['objective_seconds'], growth['growth_seconds']))
        self.assertEqual('assignment_needed', growth['status'])
        self.assertIsNotNone(found['next_action'])

    def test_scoped_jobs_keep_their_own_baselines_and_measure_the_executed_harness(self):
        samples, workflows = [], []
        for day, profile in enumerate(('full', 'records', 'tooling', 'localization', 'task'), start=10):
            run = hosted_run(run_id=day, day=day)
            step = f'Verify {profile} changes'
            linux = hosted_job(day * 10, 'ubuntu', '00:10', '04:00',
                               'Run pnpm check' if profile == 'full' else step,
                               '00:30', '03:50', run_id=day, day=day)
            windows = hosted_job(day * 10 + 1, 'windows', '00:10', '06:00',
                                 'Run uv run python scripts/check.py --require-windows' if profile == 'full' else step,
                                 '00:40', '05:20', run_id=day, day=day)
            workflows.append(timing.hosted_timing(run, [linux, windows]))
            report = fast_report(day, 190)
            report['source']['git_head'] = 'b' * 40
            report['selection']['profile'] = profile
            report['ci'] = {'repository': 'fixture/scopehub', 'run_id': str(day), 'run_attempt': '1',
                            'event': 'pull_request', 'head_sha': 'a' * 40, 'checkout_sha': 'b' * 40}
            samples.append(timing.report_sample(report))

        view = timing.timing_view(samples, hosted=workflows)
        self.assertCountEqual(['full', 'records', 'tooling', 'localization'],
                              [row['profile'] for row in view['objective_reviews']])
        self.assertIn('pull_request / records / Linux', timing.review_summary({**view, 'input_issues': []}))
        for sample in view['samples']:
            with self.subTest(profile=sample['profile']):
                if sample['profile'] == 'task':
                    self.assertIsNotNone(sample['review_exclusion'])
                else:
                    self.assertEqual(200, sample['seconds']['harness_step'])

    def test_objective_breaches_remain_visible_across_hosted_coverage_change(self):
        changed_commit = 'b' * 40
        changed_catalog = fixture_catalog((*OLD_FULL_CHECKS, 'chart-history'), non_change=('release-artifacts',))
        self.enterContext(patch.object(timing, 'native_publishing_catalog', side_effect=lambda revision: (
            True, changed_catalog if revision == changed_commit else OLD_FULL_CATALOG,
        )))
        samples, workflows = [], []
        for day, finish in enumerate(('14:50', '15:01', '18:01'), start=14):
            run = hosted_run(run_id=day, day=day)
            run.update(event='push', head_branch='main',
                       updated_at=f'2026-11-{day:02}T00:{finish}Z')
            linux = hosted_job(day * 10, 'ubuntu', '00:10', '04:00', 'Run pnpm check', '00:30', '03:50',
                               run_id=day, day=day)
            windows = hosted_job(day * 10 + 1, 'windows', '00:20', finish,
                                 'Run uv run python scripts/check.py --require-windows', '00:50', '14:30',
                                 run_id=day, day=day)
            revision = changed_commit if day == 16 else 'a' * 40
            workflows.append(timing.hosted_timing(dict(run, head_sha=revision),
                                                 [dict(job, head_sha=revision) for job in (linux, windows)]))

            report = historical_report()
            report.update(run_id=f'202611{day:02}T000000.000000Z-aabbccdd',
                          generated_at=f'2026-11-{day:02}T00:14:30Z', status='passed',
                          environment={'system': 'Windows', 'machine': 'AMD64'}, ci={
                              'repository': 'fixture/scopehub', 'run_id': str(day), 'run_attempt': '1',
                              'event': 'push', 'head_sha': revision, 'checkout_sha': revision,
                          })
            report['source']['git_head'] = revision
            native = next(result for result in report['checks'] if result['id'] == 'windows-native-build')
            native.pop('reason_code')
            native.update(status='passed', exit_code=0, duration_seconds=120)
            if day == 16:
                report['selection']['checks'].append('chart-history')
                report['checks'].append({'id': 'chart-history', 'status': 'passed', 'exit_code': 0,
                                         'duration_seconds': 30})
            samples.append(timing.report_sample(report))

        view = timing.timing_view(samples, hosted=workflows)
        equivalent = [metric for review in view['reviews'] for metric in review['metrics']
                      if metric['metric'] == 'total']
        self.assertCountEqual([(metric['sample_count'], metric['status']) for metric in equivalent],
                              [(2, 'watch'), (1, 'watch')])
        objective, = view['objective_reviews']
        self.assertEqual('total', objective['metric'])
        self.assertEqual('investigate', objective['status'])
        self.assertEqual(2, objective['recent_objective_breaches'])
        change, = objective['coverage_changes']
        self.assertEqual(['chart-history'], change['added'])
        self.assertEqual([], change['removed'])
        self.assertEqual([], change['outcome_changes'])
        self.assertEqual(180, change['elapsed_change_seconds'])
        self.assertEqual([], timing.budget_follow_up(view, [], {})['repeated_breaches'])

        # Only an equivalent cache and coverage cohort can trigger recurrence.
        source = samples[-2]['source_commit']
        samples[-1] = dict(samples[-1], checks=samples[-2]['checks'], selected=samples[-2]['selected'],
                           source_commit=source, ci=dict(samples[-1]['ci'], head_sha=source, checkout_sha=source))
        workflows[-1] = dict(workflows[-1], head_sha=source)
        mixed = timing.timing_view([*samples[:-1], dict(samples[-1], cache='cold')], hosted=workflows)
        self.assertEqual([], timing.budget_follow_up(mixed, [], {})['repeated_breaches'])

        later_reports = [{'run_id': row['run_id'], 'source_commit': 'a' * 40} for row in samples[-2:]]
        corrective = {'id': 'M0-999', 'status': 'active', 'next_action': 'Measure the repaired Windows stage.',
                      'blocker': None}
        scope = {'repository': 'fixture/scopehub', 'event': 'push', 'profile': 'full', 'platform': 'Windows'}
        finding = {'id': 'windows-budget', 'verification_budget': scope, 'status': 'open',
                   'task_ids': [corrective['id']], 'diagnostic_reports': later_reports}
        tasks = {corrective['id']: corrective}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / '.reports/timing/weekly.json'
            archive.parent.mkdir(parents=True)
            archive.write_bytes(timing.json_bytes({'kind': timing.ARCHIVE_KIND, 'samples': samples,
                                                  'hosted': workflows}))
            with patch.object(timing, 'command', side_effect=AssertionError('Unexpected hosted inspection')):
                assigned = timing.offline_budget_status(root, [finding], tasks)
                uncovered = timing.offline_budget_status(root, [], tasks)
                wrong_commit = dict(later_reports[1], source_commit='b' * 40)
                finished = {corrective['id']: dict(corrective, status='done')}
                unassigned = timing.offline_budget_status(root, [finding], finished)
                addressed = timing.offline_budget_status(root, [dict(finding, status='addressed')], finished)
                new_breach = timing.offline_budget_status(
                    root, [dict(finding, status='addressed', diagnostic_reports=[later_reports[0], wrong_commit])],
                    finished,
                )
                existing_owner = timing.offline_budget_status(
                    root, [dict(finding, diagnostic_reports=[later_reports[0], wrong_commit])], tasks,
                )
                archive.unlink()
                durable = timing.offline_budget_status(root, [finding], tasks)

        breach, = assigned['repeated_breaches']
        self.assertEqual(('Windows', 'assigned'), (breach['platform'], breach['status']))
        self.assertEqual([corrective], breach['feedback'][0]['corrective_tasks'])
        self.assertEqual('assignment_needed', uncovered['repeated_breaches'][0]['status'])
        self.assertIsNotNone(uncovered['next_action'])
        self.assertEqual('assignment_needed', unassigned['repeated_breaches'][0]['status'])
        self.assertEqual('recovery_pending', addressed['repeated_breaches'][0]['status'])
        self.assertEqual('addressed', addressed['budget_recovery'][0]['feedback_status'])
        self.assertIn('Record the corrective commit', addressed['next_action'])
        unrecorded = [dict(later_reports[1], hosted_run_id='16')]
        self.assertEqual(unrecorded, new_breach['repeated_breaches'][0]['unrecorded_reports'])
        self.assertEqual('assignment_needed', new_breach['repeated_breaches'][0]['status'])
        self.assertEqual('assigned', existing_owner['repeated_breaches'][0]['status'])
        self.assertIn('existing corrective feedback', existing_owner['next_action'])
        self.assertEqual(unrecorded, existing_owner['repeated_breaches'][0]['unrecorded_reports'])
        self.assertFalse(durable['timing_available'])
        self.assertEqual(assigned['open_feedback'], durable['open_feedback'])

    def test_changed_coverage_failed_and_missing_samples_do_not_inflate_the_baseline(self):
        reports = [fast_report(day, 120) for day in (14, 15, 16)]
        changed = fast_report(17, 280)
        changed['selection']['checks'].append('architecture')
        changed['checks'].append({'id': 'architecture', 'status': 'passed', 'exit_code': 0, 'duration_seconds': 80})
        failed = fast_report(18, 290)
        failed['status'] = 'failed'
        failed['checks'][0].update(status='failed', exit_code=1)
        missing = fast_report(19, 300)
        del missing['timing']
        samples = [timing.report_sample(report) for report in [*reports, changed, failed, missing]]
        view = timing.timing_view([*samples, samples[0]])
        self.assertEqual(6, len(view['samples']))
        self.assertCountEqual(
            [(metric['status'], metric['sample_count']) for review in view['reviews'] for metric in review['metrics']],
            [('insufficient_samples', 3), ('insufficient_samples', 1)],
        )
        self.assertTrue(all(row['review_exclusion'] for row in view['samples'][-2:]))

    def test_cache_state_preserves_comparability(self):
        samples = [
            timing.report_sample(fast_report(day, 120 if day < 17 else 250), cache='warm' if day < 17 else 'cold')
            for day in range(14, 20)
        ]
        reviews = timing.timing_view(samples)['reviews']
        self.assertCountEqual(
            [(review['cache'], metric['status']) for review in reviews for metric in review['metrics']],
            [('warm', 'insufficient_samples'), ('cold', 'insufficient_samples')],
        )

    def test_explicit_local_archive_round_trip_stays_offline(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / 'test-report.json'
            archive = Path(directory) / 'timing.json'
            report.write_text(json.dumps(fast_report(19, 120)), encoding='utf-8')
            original = self.invoke_timing('--report', report, '--save', archive)
            recovered = self.invoke_timing('--report', archive)

        self.assertEqual(original['samples'], recovered['samples'])
        self.assertEqual(original['reviews'], recovered['reviews'])
        self.assertEqual(7, recovered['weekly_review']['interval_days'])
        self.assertEqual([], recovered['input_issues'])

    def invoke_timing(self, *args, expected_exit=None):
        output, errors = StringIO(), StringIO()
        with (patch.object(sys, 'argv', ['timing.py', *map(str, args)]), redirect_stdout(output), redirect_stderr(errors),
              patch.object(timing, 'github_identity', side_effect=AssertionError('Unexpected hosted inspection'))):
            if expected_exit is None:
                timing.main()
            else:
                with self.assertRaises(SystemExit) as raised:
                    timing.main()
                self.assertEqual(expected_exit, raised.exception.code)
        return json.loads(output.getvalue()) if output.getvalue() else errors.getvalue()

    def test_cli_preserves_distinct_windows_producer_reports_and_reports_real_conflicts(self):
        use_catalog(self, SPLIT_CATALOG)
        report = split_harness_report(500)
        report['ci'] = {
            'repository': 'fixture/scopehub', 'run_id': '11', 'run_attempt': '1', 'event': 'pull_request',
            'head_sha': 'b' * 40, 'checkout_sha': 'a' * 40,
        }
        report['selection']['profile'] = 'full'

        def producer(lane, check_id):
            selection = {'profile': f'ci-windows-{lane}', 'task_id': None, 'checks': [check_id]}
            row = {'id': check_id, 'status': 'passed', 'exit_code': 0, 'duration_seconds': 12}
            return {**report, 'selection': selection, 'checks': [row], 'timing': {'elapsed_seconds': 14}}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            aggregate, browser, recovery, conflict, malformed, archive = (
                root / f'{name}.json' for name in ('aggregate', 'browser', 'recovery', 'conflict', 'malformed', 'saved')
            )
            raw = producer('browser', 'workspace-timeframe')
            compact = timing.report_sample(producer('recovery', 'market-recovery'), cache='partial')
            aggregate.write_bytes(timing.json_bytes(report))
            browser.write_bytes(timing.json_bytes(raw))
            recovery.write_bytes(timing.json_bytes(compact))
            conflict.write_bytes(timing.json_bytes({**report, 'timing': {'elapsed_seconds': 501}}))
            malformed.write_bytes(timing.json_bytes({**raw, 'environment': {'system': 'Linux'}}))

            mixed = self.invoke_timing('--report', browser, '--report', recovery, '--report', aggregate, '--save', archive)
            self.assertCountEqual(['ci-windows-browser', 'ci-windows-recovery', 'full'],
                                  [row['profile'] for row in mixed['samples']])
            self.assertEqual([], mixed['input_issues'])
            self.assertEqual(mixed['samples'], self.invoke_timing('--report', archive)['samples'])

            disputed = self.invoke_timing('--report', aggregate, '--report', conflict, '--report', browser)
            remaining, = disputed['samples']
            self.assertEqual('ci-windows-browser', remaining['profile'])
            issue, = disputed['input_issues']
            self.assertIn('Conflicting complete timing samples for this run and platform', issue['error'])

            invalid = self.invoke_timing('--report', aggregate, '--report', malformed)
            retained, = invalid['samples']
            self.assertEqual('full', retained['profile'])
            issue, = invalid['input_issues']
            self.assertEqual(malformed.name, issue['file'])
            bad_archive = root / 'bad.json'
            self.invoke_timing('--report', malformed, '--save', bad_archive, expected_exit=1)
            self.assertFalse(bad_archive.exists())

    def test_cli_scope_validation_and_matching_compact_siblings(self):
        use_catalog(self, fixture_catalog((*BASIC_CHECKS, *SPLIT_CHECKS, 'future-check')))
        report = split_harness_report(500, profile='ci-windows-core')
        report['ci'] = {
            'repository': 'fixture/scopehub', 'run_id': '11', 'run_attempt': '1', 'event': 'pull_request',
            'head_sha': 'b' * 40, 'checkout_sha': 'a' * 40,
        }
        report['selection']['checks'].append('future-check')
        report['checks'].append({'id': 'future-check', 'status': 'blocked', 'reason_code': 'not_implemented'})
        sample = timing.report_sample(report, cache='partial')
        self.assertFalse(sample['coverage_complete'])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw, compact = (root / name for name in ('test-report.json', timing.TIMING_FILE))
            raw.write_bytes(timing.json_bytes(report))
            compact.write_bytes(timing.json_bytes(sample))
            for files in ((raw, compact), (compact, raw)):
                view = self.invoke_timing('--report', files[0], '--report', files[1])
                self.assertEqual(['partial'], [row['cache'] for row in view['samples']])
                self.assertEqual([], view['input_issues'])
            with patch.object(timing, 'local_paths', return_value=[raw]):
                view = self.invoke_timing()
                self.assertEqual(['partial'], [row['cache'] for row in view['samples']])
                compact.write_bytes(timing.json_bytes(dict(sample, recorded_at='2026-11-21T00:10:00Z')))
                view = self.invoke_timing()
                self.assertEqual(['unknown'], [row['cache'] for row in view['samples']])
                self.assertIn('does not match', view['input_issues'][0]['error'])

            for profile in ('ci-windows-core ', 'ci-windows-unsupported'):
                with self.subTest(profile=profile):
                    report['selection']['profile'] = profile
                    raw.write_bytes(timing.json_bytes(report))
                    view = self.invoke_timing('--report', raw)
                    self.assertEqual([], view['samples'])
                    self.assertEqual(raw.name, view['input_issues'][0]['file'])
            archive = Path('ci-pull_request-11-1.json')
            self.assertFalse(timing.sample_belongs_to_capture(dict(sample, profile=None), archive))
            self.assertTrue(timing.sample_belongs_to_capture(dict(sample, profile='records'), archive))

    def test_large_v1_manual_archive_preserves_observations_and_redacts_legacy_paths(self):
        sample = timing.report_sample(fast_report(19, 120))
        samples = [sample] * 2400
        run = hosted_run()
        clock = timing.hosted_timing(run, [])
        clocks = [clock]
        private = '/private/review/missing-timing.json'
        data = {'kind': timing.ARCHIVE_KIND, 'samples': samples, 'hosted': clocks,
                'input_issues': [{'run_id': '42', 'path': private, 'error': f'Missing original {private}.'}]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / 'old-review.json'
            archive = root / 'new-review.json'
            report.write_bytes(timing.json_bytes(data))
            self.assertGreater(report.stat().st_size, 2 * 1024 * 1024)
            with self.assertRaisesRegex(timing.TimingError, 'compact timing read limit'):
                timing.read_bounded_timing(report)
            original = self.invoke_timing('--report', report, '--save', archive)
            reopened = self.invoke_timing('--report', archive)
            stored = json.loads(archive.read_bytes())
        self.assertEqual(samples, stored['samples'])
        self.assertEqual(clocks, stored['hosted'])
        self.assertEqual(original['samples'], reopened['samples'])
        self.assertEqual(original['hosted'], reopened['hosted'])
        first_issue, = reopened['input_issues']
        self.assertEqual(('42', 'missing-timing.json', 'Missing original missing-timing.json.'),
                         (first_issue['run_id'], first_issue['path'], first_issue['error']))
        self.assertNotIn(private, json.dumps(stored))

    def test_bad_explicit_input_cannot_publish_an_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / 'input.json'
            archive = root / 'output.json'
            with report.open('wb') as stream:
                stream.truncate(timing.MANUAL_ARCHIVE_LIMIT + 1)
            description = self.invoke_timing('--report', report, '--save', archive, expected_exit=1)
            self.assertEqual([{'file': report.name, 'error': 'Report exceeds the manual timing archive limit.'}],
                             description['input_issues'])
            self.assertFalse(archive.exists())
            report.write_bytes(b'{"kind":')
            description = self.invoke_timing('--report', report, '--save', archive, expected_exit=1)
            self.assertNotIn(str(root), json.dumps(description))
            self.assertFalse(archive.exists())
            report.write_bytes(timing.json_bytes({'kind': timing.ARCHIVE_KIND, 'samples': {}, 'hosted': []}))
            description = self.invoke_timing('--report', report, '--save', archive, expected_exit=1)
            self.assertIn('sample and clock lists', description['input_issues'][0]['error'])
            self.assertFalse(archive.exists())
            report.write_bytes(timing.json_bytes(fast_report(19, 120)))
            with patch.object(timing, 'REVIEW_READ_LIMIT', report.stat().st_size):
                description = self.invoke_timing('--report', report, '--report', report, '--save', archive,
                                                 expected_exit=1)
            self.assertIn('combined timing read limit', description['input_issues'][0]['error'])
            self.assertFalse(archive.exists())
            report.write_bytes(timing.json_bytes({**fast_report(19, 120), 'padding': 'x' * timing.TIMING_READ_LIMIT}))
            description = self.invoke_timing('--report', report, '--save', archive, expected_exit=1)
            self.assertIn('compact timing read limit', description['input_issues'][0]['error'])
            self.assertFalse(archive.exists())

    def test_archive_save_publishes_only_a_complete_new_file(self):
        data = {'kind': timing.ARCHIVE_KIND, 'samples': [], 'hosted': []}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / 'timing.json'
            with patch.object(timing, 'MANUAL_ARCHIVE_LIMIT', 30):
                with self.assertRaisesRegex(timing.TimingError, 'manual timing archive limit'):
                    timing.save_review_archive(archive, data)
            self.assertEqual([], list(root.iterdir()))
            timing.save_review_archive(archive, data)
            snapshot = archive.read_bytes()
            with self.assertRaises(FileExistsError):
                timing.save_review_archive(archive, {**data, 'input_issues': [{'error': 'different'}]})
            self.assertEqual(snapshot, archive.read_bytes())
            self.assertEqual([archive], list(root.iterdir()))

    def test_explicit_save_rejects_incomplete_batches_and_retains_original_diagnostics(self):
        sample = timing.report_sample(fast_report(19, 120))
        clock = timing.hosted_timing(hosted_run(), [])
        saved_note = {'run_id': '42', 'error': 'Original job clock was unavailable.'}
        valid = {'kind': timing.ARCHIVE_KIND, 'samples': [sample], 'hosted': [clock], 'input_issues': [saved_note]}
        cases = (
            ({**valid, 'samples': [{**sample, 'checks': None}]}, 'Invalid timing sample'),
            ({**valid, 'hosted': [{**clock, 'workflow': []}]}, 'Invalid attempt clock'),
            ({**valid, 'input_issues': [saved_note, {}]}, 'Invalid collection issue'),
            ({**valid, 'recent_collection': []}, 'timing archive collection record'),
            ({**sample, 'checks': None}, 'Invalid timing sample'),
            ({**fast_report(19, 120), 'kind': 'unexpected'}, 'timing input kind'),
            ({**fast_report(19, 120), 'checks': [{}]}, 'harness report with run identity and checks'),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            control, faulty, destination = (root / name for name in ('valid.json', 'faulty.json', 'review.json'))
            control.write_bytes(timing.json_bytes(valid))
            for index, (data, message) in enumerate(cases):
                with self.subTest(message=message):
                    faulty.write_bytes(timing.json_bytes(data))
                    rejected = root / f'rejected-{index}.json'
                    result = self.invoke_timing('--report', control, '--report', faulty, '--save', rejected,
                                                expected_exit=1)
                    self.assertTrue(any(message in note['error'] for note in result['input_issues']))
                    self.assertTrue(any(note.get('run_id') == '42' for note in result['input_issues']))
                    self.assertFalse(rejected.exists())
            self.invoke_timing('--report', control, '--save', destination)
            stored = json.loads(destination.read_bytes())
            self.assertEqual([sample], stored['samples'])
            self.assertEqual([clock], stored['hosted'])
            self.assertEqual([{**saved_note, 'file': control.name}], stored['input_issues'])

    def test_actions_diagnostics_name_the_failing_input_and_sanitize_legacy_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, jobs, old, output = (root / name for name in ('run.json', 'jobs.json', 'legacy.json', 'review.json'))
            jobs.write_text('[]', encoding='utf-8')
            first = self.invoke_timing('--report', old, '--actions', run, jobs, '--save', output, expected_exit=1)
            self.assertEqual([old.name, run.name], [issue['file'] for issue in first['input_issues']])
            self.assertNotIn(str(root), json.dumps(first))
            self.assertFalse(output.exists())
            run.write_bytes(timing.json_bytes(hosted_run()))
            jobs.write_text('{', encoding='utf-8')
            history = root / '.reports/timing/earlier.json'
            archived_issue = {'run_id': '41', 'error': 'The earlier job clock was unavailable.'}
            with (patch.object(timing, 'ROOT', root),
                  patch.object(timing, 'shared_timing_directory', return_value=None)):
                empty = self.invoke_timing('--actions', run, jobs, '--save', output, expected_exit=1)
                jobs_issue, = empty['input_issues']
                self.assertEqual(jobs.name, jobs_issue['file'])
                self.assertNotIn(str(root), json.dumps(empty))
                self.assertFalse(output.exists())

                history.parent.mkdir(parents=True)
                history.write_bytes(timing.json_bytes({
                    'kind': timing.ARCHIVE_KIND, 'samples': [], 'hosted': [], 'input_issues': [archived_issue],
                }))
                populated = self.invoke_timing('--actions', run, jobs, '--save', output, expected_exit=1)
                self.assertCountEqual([jobs_issue, {**archived_issue, 'file': history.name}],
                                      populated['input_issues'])
                self.assertNotIn(str(root), json.dumps(populated))
                self.assertFalse(output.exists())

            legacy_error = str(FileNotFoundError(2, 'Missing original input', str(run)))
            windows_run, windows_jobs = r'C:\private\run-details.json', r'D:\private\job-details.json'
            windows_error = f'[WinError 2] Missing original input: {windows_run!r}'
            legacy = {'kind': timing.ARCHIVE_KIND, 'samples': [], 'hosted': [],
                      'input_issues': [{'path': str(jobs), 'run_id': '42', 'error': legacy_error},
                                       {'path': windows_jobs, 'run_id': '43', 'error': windows_error}]}
            old.write_bytes(timing.json_bytes(legacy))
            jobs.write_bytes(timing.json_bytes({'jobs': []}))
            self.invoke_timing('--report', old, '--actions', run, jobs, '--save', output)
            stored = json.loads(output.read_bytes())
            retained, windows_retained = stored['input_issues']
        self.assertEqual(('42', jobs.name, old.name), (retained['run_id'], retained['path'], retained['file']))
        self.assertIn(run.name, retained['error'])
        self.assertNotIn(str(root), json.dumps(retained))
        self.assertEqual('job-details.json', windows_retained['path'])
        self.assertEqual("[WinError 2] Missing original input: 'run-details.json'", windows_retained['error'])
        self.assertEqual([timing.hosted_timing(hosted_run(), [])], stored['hosted'])

    def test_actions_save_validates_repository_and_generated_clock_before_publication(self):
        run = hosted_run()
        linux = hosted_job(31, 'ubuntu', '00:10', '04:00', 'Run pnpm check', '00:30', '03:50')
        windows = hosted_job(32, 'windows', '00:20', '06:00',
                             'Run uv run python scripts/check.py --require-windows', '00:50', '05:20')
        with (tempfile.TemporaryDirectory() as directory,
              patch.object(timing, 'local_paths', return_value=[])):
            root = Path(directory)
            run_path, jobs_path, archive, resaved = (
                root / name for name in ('run.json', 'jobs.json', 'timing.json', 'resaved.json')
            )
            run_path.write_bytes(timing.json_bytes(run))
            jobs_path.write_bytes(timing.json_bytes({'jobs': [linux, windows]}))
            self.invoke_timing('--actions', run_path, jobs_path, '--save', archive)
            original = archive.read_bytes()
            self.invoke_timing('--report', archive, '--save', resaved)
            self.assertEqual(original, resaved.read_bytes())
            clock, = json.loads(original)['hosted']
            self.assertTrue(timing.clock_capture_complete(clock))
            for label, bad_run, bad_jobs, filename in (
                ('object-valued name', dict(run, repository={'full_name': {'wrong': 'shape'}}),
                 [linux, windows], run_path),
                ('malformed name', dict(run, repository={'full_name': 'missing-owner'}), [linux, windows], run_path),
                ('invalid derived job', run, [linux, dict(windows, conclusion={})], jobs_path),
            ):
                with self.subTest(case=label, input=filename.name):
                    run_path.write_bytes(timing.json_bytes(bad_run))
                    jobs_path.write_bytes(timing.json_bytes(bad_jobs))
                    rejected = root / 'rejected.json'
                    for destination in (rejected, archive):
                        result = self.invoke_timing('--actions', run_path, jobs_path, '--save', destination,
                                                    expected_exit=1)
                        self.assertEqual([filename.name], [note['file'] for note in result['input_issues']])
                    self.assertFalse(rejected.exists())
                    self.assertEqual(original, archive.read_bytes())

    def test_resolved_collection_history_survives_save_load_save(self):
        use_catalog(self, OLD_FULL_CATALOG)
        run_id = 45438134397
        run = dict(hosted_run(run_id=run_id), conclusion='failure')
        linux = hosted_job(31, 'ubuntu', '00:10', '04:00', 'Run pnpm check', '00:30', '03:50', run_id=run_id)
        windows = hosted_job(32, 'windows', '00:20', '06:00',
                             'Run uv run python scripts/check.py --require-windows', '00:50', '05:20', run_id=run_id)
        windows['conclusion'] = 'failure'
        clock = timing.hosted_timing(run, [linux, windows])
        samples = []
        for platform in ['Linux', 'Windows']:
            report = historical_report()
            report.update(status='failed', environment={'system': platform, 'machine': 'x86_64'})
            report['ci'] = {'repository': 'fixture/scopehub', 'run_id': str(run_id), 'run_attempt': '1',
                            'event': 'pull_request', 'head_sha': 'a' * 40, 'checkout_sha': 'a' * 40}
            samples.append(timing.report_sample(report))
        self.assertTrue(timing.capture_is_complete(timing.ci_key(clock), samples, [clock]))
        private = '/private/review/old-jobs.json'
        notes = [
            {'run_id': str(run_id), 'error': timing.MISSING_CLOCK},
            {'run_id': str(run_id), 'error': timing.MISSING_COMPACT},
            {'run_id': str(run_id), 'path': private, 'file': private,
             'error': f'Independent collection issue in {private}.'},
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            earlier, recovered, archive, again = (
                root / name for name in ('earlier.json', 'recovered.json', 'saved.json', 'again.json')
            )
            earlier.write_bytes(timing.json_bytes({'kind': timing.ARCHIVE_KIND, 'samples': [], 'hosted': [],
                                                   'input_issues': notes}))
            recovered.write_bytes(timing.json_bytes({'kind': timing.ARCHIVE_KIND, 'samples': samples,
                                                     'hosted': [clock]}))
            before = self.invoke_timing('--report', earlier)
            self.assertEqual(3, len(before['input_issues']))
            current = self.invoke_timing('--report', earlier, '--report', recovered, '--save', archive)
            reopened = self.invoke_timing('--report', archive, '--save', again)
            saved = json.loads(archive.read_bytes())
            self.assertEqual(samples, saved['samples'])
            self.assertEqual([clock], saved['hosted'])
            self.assertEqual(archive.read_bytes(), again.read_bytes())
            self.assertEqual(before['input_issues'], saved['input_issues'])
            self.assertEqual([saved['input_issues'][2]], current['input_issues'])
            self.assertEqual(current, reopened)
            self.assertNotIn(private, archive.read_text(encoding='utf-8'))

    def test_failed_save_does_not_append_to_a_summary_alias_or_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report, archive, summary = (root / name for name in ('input.json', 'review.json', 'summary.md'))
            report.write_bytes(timing.json_bytes(fast_report(19, 120)))
            original = b'{"original":true}\n'
            archive.write_bytes(original)
            os.link(archive, summary)
            self.invoke_timing('--report', report, '--save', archive, '--summary', summary, expected_exit=1)
            self.assertEqual(original, archive.read_bytes())
            self.assertEqual(original, summary.read_bytes())

            archive.unlink()
            link = os.link

            def occupy_destination(stage, destination):
                destination.write_bytes(original)
                link(stage, destination)

            with patch.object(timing.os, 'link', side_effect=occupy_destination):
                self.invoke_timing('--report', report, '--save', archive, '--summary', summary, expected_exit=1)
            self.assertEqual(original, archive.read_bytes())
            self.assertEqual(original, summary.read_bytes())

    def test_summary_aliased_after_save_uses_open_file_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report, archive, summary = (root / name for name in ('input.json', 'review.json', 'summary.md'))
            report.write_bytes(timing.json_bytes(fast_report(19, 120)))
            link = os.link

            def publish_and_alias(stage, destination):
                link(stage, destination)
                if summary.exists():
                    summary.unlink()
                link(destination, summary)

            with patch.object(timing.os, 'link', side_effect=publish_and_alias):
                self.invoke_timing('--report', report, '--save', archive, '--summary', summary, expected_exit=1)
            kept = json.loads(archive.read_bytes())
            self.assertEqual(timing.ARCHIVE_KIND, kept['kind'])
            self.assertEqual(1, len(kept['samples']))


class RecentTimingTests(unittest.TestCase):
    def setUp(self):
        default_catalog = fixture_catalog()
        self.catalogs = {}
        self.missing_catalogs = set()
        self.enterContext(patch.object(timing, 'native_publishing_catalog', side_effect=lambda revision: (
            (False, None) if revision in self.missing_catalogs else (True, self.catalogs.get(revision, default_catalog))
        )))

    def budget_run(self, day, finish, *, head='a' * 40, cache='warm', event='pull_request',
                   checkout=None, outcome='success', extra_check=False):
        repository = 'fixture/scopehub'
        checkout = checkout or head
        if extra_check:
            self.catalogs[checkout] = fixture_catalog((*BASIC_CHECKS, 'extra-check'))
        run = dict(hosted_run(run_id=day, day=day), head_sha=head, event=event, conclusion=outcome,
                   head_branch='main' if event == 'push' else 'repair')
        jobs = [
            hosted_job(day * 10, 'ubuntu', '00:10', '04:00', 'Run pnpm check', '00:30', '03:50', run_id=day, day=day),
            hosted_job(day * 10 + 1, 'windows', '00:20', finish,
                       'Run uv run python scripts/check.py --require-windows', '00:50', '08:30',
                       run_id=day, day=day),
        ]
        jobs[-1]['conclusion'] = outcome
        workflow = timing.hosted_timing(run, [dict(job, head_sha=head) for job in jobs])
        samples = []
        for platform in ('Linux', 'Windows'):
            report = fast_report(day, 180)
            report['selection']['profile'] = 'full'
            report['environment']['system'] = platform
            report['source'].update(git_head=checkout, sha256='c' * 64, working_tree_dirty=False)
            report['ci'] = {'repository': repository, 'run_id': str(day), 'run_attempt': '1', 'event': event,
                            'head_sha': head, 'checkout_sha': checkout}
            if extra_check:
                report['selection']['checks'].append('extra-check')
                report['checks'].append(dict(report['checks'][0], id='extra-check'))
            if outcome == 'failure' and platform == 'Windows':
                report['status'] = 'failed'
                report['checks'][0].update(status='failed', exit_code=1)
            samples.append(timing.report_sample(report, cache=cache))
        return run, samples, workflow

    def write_archive(self, path, samples, clocks):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(timing.json_bytes({'kind': timing.ARCHIVE_KIND, 'samples': samples, 'hosted': clocks}))

    def recovery_fixture(self):
        self.enterContext(isolated_git_environment())
        root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.fixture_git(root, 'init', '-q')
        commits = []
        for subject in ('Before repair', 'Repair Windows verification', 'Follow-up on repaired verification'):
            self.fixture_git(root, 'commit', '-qm', subject, '--allow-empty')
            commits.append(self.fixture_git(root, 'rev-parse', 'HEAD'))
        return root, commits

    def fixture_git(self, root, *args):
        return subprocess.run(['git', '-C', str(root), '-c', 'user.name=Fixture',
                               '-c', 'user.email=fixture@example.invalid', '-c', 'commit.gpgsign=false', *args],
                              capture_output=True, check=True, text=True).stdout.strip()

    def preview_checkout(self, root, base, head, *, subject='Test PR merge'):
        tree = self.fixture_git(root, 'rev-parse', f'{base}^{{tree}}')
        preview = self.fixture_git(root, 'commit-tree', tree, '-p', base, '-p', head, '-m', subject)
        self.fixture_git(root, 'update-ref', f'refs/tags/fixture-{preview[:10]}', preview)
        return preview

    def budget_finding(self, event, repair, *observations, growth_baseline=()):
        finding = {'id': f'budget-{event}', 'verification_budget': {
            'repository': 'fixture/scopehub', 'event': event, 'profile': 'full', 'platform': 'Windows',
            'repair_commit': repair,
        }, 'status': 'addressed', 'task_ids': ['M0-999'], 'diagnostic_reports': [
            timing.harness_identity(samples[-1]) for _, samples, _ in observations
        ]}
        if growth_baseline:
            finding['verification_budget']['growth_baseline'] = [
                timing.harness_identity(samples[-1]) for _, samples, _ in growth_baseline
            ]
        return finding

    def offline_observations(self, root, finding, *observations, tasks=None):
        self.write_archive(root / '.reports/timing/offline.json',
                           [sample for _, samples, _ in observations for sample in samples],
                           [clock for _, _, clock in observations])
        with patch.object(timing, 'command', side_effect=AssertionError('Unexpected hosted inspection')):
            return timing.offline_budget_status(root, [finding], tasks or {})

    def test_missing_linked_observation_cannot_make_an_earlier_run_the_recovery(self):
        root, (before, repair, after) = self.recovery_fixture()
        checkout = self.preview_checkout(root, before, after)
        early, late = (self.budget_run(day, '20:00', head=before) for day in (11, 18))
        success = self.budget_run(16, '12:00', head=after, checkout=checkout)
        finding = self.budget_finding('pull_request', repair, early, late)
        full = self.offline_observations(root, finding, early, late, success)
        self.assertEqual('pending', full['budget_recovery'][0]['recovery']['status'])
        incomplete = self.offline_observations(root, finding, early, success)
        recovery = incomplete['budget_recovery'][0]['recovery']
        self.assertEqual('pending', recovery['status'])
        self.assertEqual([timing.harness_identity(late[1][-1])['run_id']],
                         [row['run_id'] for row in recovery['unavailable_reports']])
        self.assertIn('linked', incomplete['next_action'])
        final = self.budget_run(19, '12:15', head=after, checkout=checkout)
        recovered = self.offline_observations(root, finding, early, late, success, final)
        self.assertEqual('19', recovered['budget_recovery'][0]['recovery']['comparisons'][0]['recovered']['hosted_run_id'])

    def test_pr_recovery_proves_the_tested_merge_and_can_find_repair_on_either_parent(self):
        root, (before, repair, after) = self.recovery_fixture()
        older = self.budget_run(10, '20:00', head=before)
        baseline = self.budget_run(11, '20:00', head=before)
        finding = self.budget_finding('pull_request', repair, older, baseline)
        descendant = self.budget_run(16, '14:55', head=repair, checkout=after)
        rejected = self.offline_observations(root, finding, older, baseline, descendant)
        self.assertEqual('pending', rejected['budget_recovery'][0]['recovery']['status'])

        tree = self.fixture_git(root, 'rev-parse', f'{before}^{{tree}}')
        topic = self.fixture_git(root, 'commit-tree', tree, '-p', before, '-m', 'Independent PR topic')
        checkout = self.preview_checkout(root, repair, topic)
        authentic = self.budget_run(17, '14:40', head=topic, checkout=checkout)
        result = self.offline_observations(root, finding, older, baseline, authentic)
        self.assertEqual('recovered', result['budget_recovery'][0]['recovery']['status'])
        wrong_parent = self.budget_run(17, '14:40', head=repair, checkout=checkout)
        self.assertEqual('pending', self.offline_observations(root, finding, older, baseline, wrong_parent)
                         ['budget_recovery'][0]['recovery']['status'])

        unavailable = 'f' * 40
        historical = self.budget_run(18, '14:40', head=topic, checkout=unavailable)
        self.missing_catalogs.add(unavailable)
        self.catalogs.update(dict.fromkeys((before, topic), fixture_catalog((*BASIC_CHECKS, timing.GENERATED_CHECK))))
        self.enterContext(patch.object(timing, 'committed_publishing_inputs', return_value='c' * 64))
        for observation in (older, baseline, historical):
            for sample in observation[1]:
                sample['selected'].append(timing.GENERATED_CHECK)
                sample['selected'].sort()
                sample['checks'].append(dict(sample['checks'][0], id=timing.GENERATED_CHECK))
        proof = {'checkout': unavailable, 'tree': tree, 'parents': [repair, topic], 'comparison_base': repair,
                 'ref': 'refs/pull/77/merge', 'command': timing.GENERATED_COMMAND,
                 'tools': {'python': '3.12.0', 'rustfmt': 'rustfmt 1.8.0 (abcdef 2026-01-01)'}}
        historical[1][-1]['generated_contracts'] = proof
        retained = self.offline_observations(root, finding, older, baseline, historical)
        self.assertEqual('recovered', retained['budget_recovery'][0]['recovery']['status'])
        proof['parents'] = [topic, repair]
        mismatched = self.offline_observations(root, finding, older, baseline, historical)
        self.assertEqual('pending', mismatched['budget_recovery'][0]['recovery']['status'])

    def test_within_budget_growth_recovery_requires_a_restored_three_run_window(self):
        root, (before, repair, after) = self.recovery_fixture()
        base = [self.budget_run(day, '10:00', head=before, event='push') for day in (11, 12, 13)]
        degraded = [self.budget_run(day, '12:10', head=before, event='push') for day in (14, 15, 16)]
        corrected = [self.budget_run(day, '10:00', head=after, event='push') for day in (17, 18, 19)]
        finding = self.budget_finding('push', repair, *degraded, growth_baseline=base)
        old = self.offline_observations(root, finding, *base, *degraded)
        self.assertEqual(130, old['sustained_growth'][0]['growth_seconds'])
        pending = self.offline_observations(root, finding, *base, *degraded, *corrected[:2])
        self.assertEqual('pending', pending['budget_recovery'][0]['recovery']['status'])
        green = self.offline_observations(root, finding, *base, *degraded, *corrected)
        comparison, = green['budget_recovery'][0]['recovery']['comparisons']
        self.assertEqual(('19', 600), (comparison['recovered']['hosted_run_id'], comparison['control_median_seconds']))
        self.assertEqual([], green['sustained_growth'])
        self.assertIsNone(green['next_action'])

    def test_growth_recovery_keeps_the_original_trigger_despite_an_isolated_overrun(self):
        root, (before, repair, after) = self.recovery_fixture()
        for original, degraded in ((('10:00', '10:00', '10:00'), ('12:10', '12:10', '15:30')),
                                   (('10:00', '10:00', '15:30'), ('12:10', '12:10', '12:10'))):
            with self.subTest(original=original, degraded=degraded):
                base = [self.budget_run(day, finish, head=before, event='push')
                        for day, finish in enumerate(original, 11)]
                slow = [self.budget_run(day, finish, head=before, event='push')
                        for day, finish in enumerate(degraded, 14)]
                improved = [self.budget_run(day, finish, head=after, event='push')
                            for day, finish in enumerate(('12:10', '10:00', '10:00'), 17)]
                finding = self.budget_finding('push', repair, *slow, growth_baseline=base)
                triggered = self.offline_observations(root, finding, *base, *slow)
                self.assertEqual([], triggered['repeated_breaches'])
                self.assertEqual(130, triggered['sustained_growth'][0]['growth_seconds'])
                pending = self.offline_observations(root, finding, *base, *slow, improved[0])
                self.assertEqual('pending', pending['budget_recovery'][0]['recovery']['status'])
                self.assertEqual([], pending['budget_recovery'][0]['recovery']['comparisons'][0]['recovery_window'])
                recovered = self.offline_observations(root, finding, *base, *slow, *improved)
                proof, = recovered['budget_recovery'][0]['recovery']['comparisons']
                self.assertEqual(['17', '18', '19'], [row['hosted_run_id'] for row in proof['recovery_window']])
                missing = self.offline_observations(root, finding, *base[1:], *slow, *improved)
                self.assertEqual('pending', missing['budget_recovery'][0]['recovery']['status'])
                self.assertIn('linked observations', missing['next_action'])

        over = [self.budget_run(day, '20:00', head=before, event='push') for day in (21, 22)]
        corrected = self.budget_run(23, '14:55', head=after, event='push')
        budget = self.budget_finding('push', repair, *over)
        recovery = self.offline_observations(root, budget, *over, corrected)['budget_recovery'][0]['recovery']
        self.assertEqual('recovered', recovery['status'])
        self.assertEqual(['23'], [row['hosted_run_id'] for row in recovery['comparisons'][0]['recovery_window']])

    def test_missing_original_growth_baseline_cannot_be_replaced_by_an_older_sample(self):
        root, (before, repair, after) = self.recovery_fixture()
        older = self.budget_run(10, '10:30', head=before, event='push')
        baseline = [self.budget_run(day, finish, head=before, event='push')
                    for day, finish in enumerate(('10:00', '10:00', '15:30'), 11)]
        degraded = [self.budget_run(day, '12:10', head=before, event='push') for day in (14, 15, 16)]
        improved = [self.budget_run(day, '11:40', head=after, event='push') for day in (17, 18, 19)]
        finding = self.budget_finding('push', repair, *degraded, growth_baseline=baseline)
        full = self.offline_observations(root, finding, older, *baseline, *degraded, *improved)
        comparison, = full['budget_recovery'][0]['recovery']['comparisons']
        self.assertEqual(600, comparison['control_median_seconds'])
        self.assertEqual(['11', '12', '13'], [row['hosted_run_id'] for row in comparison['controls']])
        self.assertEqual('pending', full['budget_recovery'][0]['recovery']['status'])
        missing = self.offline_observations(root, finding, older, *baseline[1:], *degraded, *improved)
        recovery = missing['budget_recovery'][0]['recovery']
        self.assertEqual('pending', recovery['status'])
        self.assertEqual([baseline[0][1][-1]['run_id']], [row['run_id'] for row in recovery['unavailable_reports']])
        self.assertIn('linked observations', missing['next_action'])
        corrected = [self.budget_run(day, '10:00', head=after, event='push') for day in (17, 18, 19)]
        complete = self.offline_observations(root, finding, older, *baseline, *degraded, *corrected)
        self.assertEqual('recovered', complete['budget_recovery'][0]['recovery']['status'])

    def test_authenticated_failure_blocks_the_stale_success_clock_live_and_offline(self):
        root, (before, repair, after) = self.recovery_fixture()
        checkout = self.preview_checkout(root, before, after)
        baseline = [self.budget_run(day, '20:00', head=before) for day in (11, 12)]
        run, stale_samples, stale_clock = self.budget_run(16, '12:00', head=after, checkout=checkout)
        finding = self.budget_finding('pull_request', repair, *baseline)
        records = root / 'project'
        records.mkdir()
        for filename, data in [('tasks', {'tasks': []}), ('feedback', {'items': [finding]}),
                               ('branch-protection', {'branch': 'main'})]:
            (records / f'{filename}.json').write_bytes(timing.json_bytes(data))
        self.write_archive(root / '.reports/timing/stale.json',
                           [*baseline[0][1], *baseline[1][1], *stale_samples],
                           [baseline[0][2], baseline[1][2], stale_clock])
        prior = timing.offline_budget_status(root, [finding], {})
        self.assertEqual('recovered', prior['budget_recovery'][0]['recovery']['status'])
        self.retain_newer_cold_attempts(root, 'pull_request', after, checkout=checkout)
        with (patch.object(ci, 'github_identity', return_value='fixture/scopehub'),
              patch.object(ci, 'source_identity', return_value={'git_head': after, 'working_tree_dirty': False}),
              patch.object(ci, 'checkout_branch', return_value=run['head_branch']),
              patch.object(ci, 'workflow_runs', return_value=[dict(run, conclusion='failure')]),
              patch.object(ci, 'independent_status', return_value={'status': 'missing', 'run': None}),
              patch.object(timing, 'download_timing', return_value=[]),
              patch.object(timing, 'inspect_jobs', side_effect=OSError('Job endpoint unavailable')) as jobs):
            with patch.object(timing, 'collect_new_ci_timing', side_effect=OSError('Capture is unavailable')):
                uncaptured = ci.status(root)
            immediate = timing.offline_budget_status(root, [finding], {})
            live = ci.status(root)
        self.assertEqual('failed', live['status'])
        jobs.assert_called_once()
        with patch.object(timing, 'command', side_effect=AssertionError('Unexpected hosted inspection')):
            offline = timing.offline_budget_status(root, [finding], {})
        for budget in (uncaptured['verification_budget'], immediate, live['verification_budget'], offline):
            recovery = budget['budget_recovery'][0]['recovery']
            self.assertEqual('pending', recovery['status'])
            self.assertIsNone(recovery['comparisons'][0]['recovered'])
            self.assertIsNotNone(budget['next_action'])
            self.assertTrue(any(note.get('run_id') == '16' and note.get('authenticated_conclusions') == ['failure']
                                for note in budget['input_issues']))
        samples, clocks, issues = timing.retained_budget_inputs(root)
        merged = timing.timing_view(samples, hosted=clocks, issues=issues)
        observed, = [sample for sample in merged['samples']
                     if sample['platform'] == 'Windows' and sample['ci']['run_id'] == '16']
        self.assertEqual(('failure', None), (observed['workflow_conclusion'], observed['seconds']['total']))
        _, distinct_samples, distinct_clock = self.budget_run(29, '14:55', head=after, checkout=checkout)
        self.write_archive(root / '.reports/timing/distinct-success.json', distinct_samples, [distinct_clock])
        unrelated = timing.offline_budget_status(root, [finding], {})
        distinct = unrelated['budget_recovery'][0]['recovery']['comparisons'][0]['recovered']
        self.assertEqual(('29', 895.0), (distinct['hosted_run_id'], distinct['elapsed_seconds']))

    def test_busy_timing_writer_cannot_block_ordinary_offline_failure_retention(self):
        root, (before, repair, after) = self.recovery_fixture()
        baseline = [self.budget_run(day, '20:00', head=before, event='push') for day in (11, 12)]
        run, samples, clock = self.budget_run(16, '12:00', head=after, event='push')
        finding = self.budget_finding('push', repair, *baseline)
        records = root / 'project'
        records.mkdir()
        (records / 'tasks.json').write_bytes(timing.json_bytes({'tasks': []}))
        (records / 'feedback.json').write_bytes(timing.json_bytes({'items': [finding]}))
        initial = self.offline_observations(root, finding, *baseline, (run, samples, clock))
        self.assertEqual('recovered', initial['budget_recovery'][0]['recovery']['status'])
        shared = self.retain_newer_cold_attempts(root, 'push', after)
        with (timing.exclusive_git_lock(shared / 'ci-push-16-1.lock'),
              patch.object(timing, 'command', side_effect=AssertionError('Unexpected hosted request'))):
            live = timing.ci_budget_status(root, run['repository']['full_name'], dict(run, conclusion='failure'))
            offline = timing.offline_budget_status(root, [finding], {})
        self.assertEqual('in_progress', live['intake'])
        for observation in (live, offline):
            self.assertEqual('pending', observation['budget_recovery'][0]['recovery']['status'])
            self.assertTrue(any(note.get('authenticated_conclusions') == ['failure'] for note in observation['input_issues']))

    def retain_newer_cold_attempts(self, root, event, head, *, checkout=None):
        shared = timing.shared_timing_directory(root)
        for day in range(17, 17 + timing.MAX_RECENT):
            _, rows, clock = self.budget_run(day, '10:00', head=head, checkout=checkout, event=event, cache='cold')
            self.write_archive(shared / f'ci-{event}-{day}-1.json', rows, [clock])
        return shared

    def test_post_recovery_uses_fresh_trend_windows_and_keeps_an_active_owner(self):
        root, (before, repair, after) = self.recovery_fixture()
        base = [self.budget_run(day, '10:00', head=before, event='push') for day in (11, 12, 13)]
        overruns = [self.budget_run(day, '20:00', head=before, event='push') for day in (14, 15)]
        green = self.budget_run(16, '14:59', head=after, event='push')
        fine = self.budget_run(17, '10:00', head=after, event='push')
        task = {'id': 'M0-999', 'status': 'active', 'next_action': 'Continue the timing correction.'}
        finding = dict(self.budget_finding('push', repair, *overruns), status='open')
        clean = self.offline_observations(root, finding, *base, *overruns, green, fine, tasks={task['id']: task})
        self.assertEqual([], clean['sustained_growth'])
        self.assertIsNone(clean['next_action'])
        slow = [self.budget_run(day, '20:00', head=after, event='push') for day in (18, 19)]
        recurrence = self.offline_observations(root, finding, *base, *overruns, green, fine, *slow, tasks={task['id']: task})
        breach, = recurrence['repeated_breaches']
        self.assertEqual(('assigned', 'M0-999'), (breach['status'], breach['feedback'][0]['corrective_tasks'][0]['id']))
        self.assertEqual(['18', '19'], [report['hosted_run_id'] for report in breach['unrecorded_reports']])

    def test_offline_handoff_requires_comparable_recovery_and_routes_a_later_recurrence(self):
        root, (before, repair, after) = self.recovery_fixture()
        checkout = self.preview_checkout(root, before, after)
        pr = [self.budget_run(day, '28:22', head=before) for day in (11, 12, 13, 14)]
        older_push = self.budget_run(9, '28:40', head=before, event='push')
        push = self.budget_run(15, '28:40', head=before, event='push')
        finding = self.budget_finding('pull_request', repair, *pr)
        main = dict(self.budget_finding('push', repair, older_push, push), status='open')
        task = {'id': 'M0-999', 'status': 'active', 'next_action': 'Measure the main push.'}
        path = root / '.reports/timing/offline-budget.json'

        def offline(*observations, items=(finding, main), checkout=root):
            observations = (older_push, *observations)
            self.write_archive(checkout / path.relative_to(root),
                               [sample for _, samples, _ in observations for sample in samples],
                               [clock for _, _, clock in observations])
            with patch.object(timing, 'command', side_effect=AssertionError('Offline verification used a remote command')):
                return timing.offline_budget_status(checkout, items, {task['id']: task})

        post = [self.budget_run(10, '12:00', head=after, checkout=checkout), # An out-of-order earlier run.
                self.budget_run(16, '12:00', head=before),                 # Earlier code, later clock.
                self.budget_run(17, '12:00', head=after, checkout=checkout, cache='cold'),
                self.budget_run(18, '12:00', head=after,
                                checkout=self.preview_checkout(root, before, after, subject='Additional check'),
                                extra_check=True),
                self.budget_run(19, '12:00', head=after, event='workflow_dispatch'),
                self.budget_run(20, '18:00', head=after, checkout=checkout), # Faster but still over budget.
                self.budget_run(21, '12:00', head=after, checkout=checkout, outcome='failure')]
        local_pass = timing.report_sample(fast_report(22, 60))
        self.write_archive(root / '.reports/timing/local-fast.json', [local_pass], [])
        improved = offline(*pr, push, *post[:-1])['budget_recovery'][0]
        improved_attempt = improved['recovery']['comparisons'][0]['latest_attempt']
        self.assertEqual(('recovery_pending', '20', 1080, -622),
                         (improved['status'], improved_attempt['hosted_run_id'], improved_attempt['elapsed_seconds'],
                          improved_attempt['elapsed_change_seconds']))
        pending = offline(*pr, push, *post)
        self.assertEqual('addressed', pending['budget_recovery'][0]['feedback_status'])
        self.assertEqual('recovery_pending', pending['budget_recovery'][0]['status'])
        comparison, = pending['budget_recovery'][0]['recovery']['comparisons']
        self.assertEqual(('21', 'failure'), (comparison['latest_attempt']['hosted_run_id'],
                                             comparison['latest_attempt']['conclusion']))
        self.assertIsNone(comparison['recovered'])
        self.assertIn('pull request within 900 seconds', pending['next_action'])

        success = self.budget_run(22, '14:59', head=after, checkout=checkout)
        wrong_checkout = self.budget_run(22, '14:59', head=after, checkout=before)
        self.assertEqual('recovery_pending', offline(*pr, push, wrong_checkout)['budget_recovery'][0]['status'])
        cleared = offline(*pr, push, *post, success)
        pr_state, main_state = cleared['budget_recovery']
        proof = pr_state['recovery']['comparisons'][0]['recovered']
        self.assertEqual(('recovered', '22', 899.0, checkout),
                         (pr_state['status'], proof['hosted_run_id'], proof['elapsed_seconds'], proof['source_commit']))
        self.assertEqual('pending', main_state['recovery']['status'])
        self.assertIn('main push within 900 seconds', cleared['next_action'])
        pending_push, = cleared['repeated_breaches']
        self.assertEqual(('push', 'assigned'), (pending_push['event'], pending_push['status']))
        open_pr = offline(*pr, push, success, items=(dict(finding, status='open'), main))
        self.assertEqual(('open', 'recovered'), (open_pr['budget_recovery'][0]['feedback_status'],
                                                open_pr['budget_recovery'][0]['status']))
        self.assertIn('main push', open_pr['next_action'])

        empty = Path(self.enterContext(tempfile.TemporaryDirectory()))
        unknown = offline(*pr, push, success, checkout=empty)
        self.assertIsNone(unknown['budget_recovery'][0]['recovery']['comparisons'][0]['recovered'])
        self.assertIn('history available locally', unknown['next_action'])
        fresh = empty / 'fresh'
        subprocess.run(['git', 'clone', '--quiet', '--no-local', str(root), str(fresh)],
                       capture_output=True, check=True)
        fresh_view = offline(*pr, push, success, checkout=fresh)
        self.assertEqual(['recovered', 'assigned'], [entry['status'] for entry in fresh_view['budget_recovery']])

        push_success = self.budget_run(23, '14:50', head=after, event='push')
        run, samples, clock = push_success
        other_branch = (run, samples, dict(clock, head_branch='unprotected'))
        self.assertEqual('pending', offline(*pr, push, success, other_branch)['budget_recovery'][1]['recovery']['status'])
        both = offline(*pr, push, *post, success, push_success)
        self.assertIsNone(both['next_action'])
        self.assertEqual(['recovered', 'recovered'], [entry['status'] for entry in both['budget_recovery']])

        slow = [self.budget_run(day, '23:30', head=after, checkout=checkout) for day in (24, 25)]
        self.assertEqual([], offline(*pr, push, success, push_success, slow[0])['repeated_breaches'])
        relapse = offline(*pr, push, success, push_success, *slow)
        breach, = relapse['repeated_breaches']
        self.assertEqual(('assignment_needed', ['24', '25']),
                         (breach['status'], [report['hosted_run_id'] for report in breach['unrecorded_reports']]))
        live = dict(self.budget_finding('pull_request', after, *slow), id='new-budget', status='open')
        assigned = offline(*pr, push, success, push_success, *slow, items=(finding, main, live))
        self.assertEqual('assigned', assigned['repeated_breaches'][0]['status'])
        self.assertEqual('M0-999', assigned['repeated_breaches'][0]['feedback'][0]['corrective_tasks'][0]['id'])

    def test_regular_ci_observes_the_matching_ordinary_success_and_failure(self):
        for event, outcome in (('pull_request', 'success'), ('push', 'failure')):
            with self.subTest(event=event, outcome=outcome):
                root, (before, repair, after) = self.recovery_fixture()
                older = self.budget_run(13, '28:22', head=before, event=event)
                baseline = self.budget_run(14, '28:22', head=before, event=event)
                checkout = self.preview_checkout(root, before, after) if event == 'pull_request' else after
                run, samples, clock = self.budget_run(15, '14:55', head=after, checkout=checkout, event=event, outcome=outcome)
                finding = self.budget_finding(event, repair, older, baseline)
                records = root / 'project'
                records.mkdir()
                for filename, data in [('tasks', {'tasks': []}), ('feedback', {'items': [finding]}),
                                       ('branch-protection', {'branch': 'main'})]:
                    (records / f'{filename}.json').write_bytes(timing.json_bytes(data))
                self.write_archive(root / '.reports/timing/control.json', [*older[1], *baseline[1]], [older[2], baseline[2]])
                with (patch.object(ci, 'github_identity', return_value='fixture/scopehub'),
                      patch.object(ci, 'source_identity', return_value={'git_head': after, 'working_tree_dirty': False}),
                      patch.object(ci, 'checkout_branch', return_value=run['head_branch']),
                      patch.object(ci, 'workflow_runs', return_value=[run]),
                      patch.object(ci, 'independent_status', return_value={'status': 'missing', 'run': None}),
                      patch.object(timing, 'download_timing', return_value=samples),
                      patch.object(timing, 'inspect_jobs', return_value=clock) as jobs):
                    result = ci.status(root)
                    again = ci.status(root)
                jobs.assert_called_once()
                self.assertEqual('retained', again['verification_budget']['intake'])
                self.assertEqual([], again['verification_budget']['input_issues'])
                self.assertEqual((15, event, after), tuple(jobs.call_args.args[0][key] for key in
                                                          ('id', 'event', 'head_sha')))
                recovery, = result['verification_budget']['budget_recovery']
                attempt = recovery['recovery']['comparisons'][0]['latest_attempt']
                self.assertEqual((outcome, '15'), (attempt['conclusion'], attempt['hosted_run_id']))
                if outcome == 'success':
                    self.assertEqual(('passed', 'recovered'), (result['status'], recovery['status']))
                else:
                    self.assertEqual(('failed', 'recovery_pending'), (result['status'], recovery['status']))
                    self.assertIn('main push', result['verification_budget']['next_action'])

    def test_malformed_timing_preserves_all_ci_verdicts_and_other_observations(self):
        observations = [self.budget_run(day, '17:00') for day in (14, 15, 16)]
        samples = [sample for _, rows, _ in observations for sample in rows]
        clocks = [clock for _, _, clock in observations]
        failed, failed_samples, failed_clock = self.budget_run(17, '17:00', outcome='failure')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records = root / 'project'
            records.mkdir()
            for file, content in (('tasks', {'tasks': []}), ('feedback', {'items': []}),
                                  ('branch-protection', {'branch': 'main'})):
                (records / f'{file}.json').write_bytes(timing.json_bytes(content))
            archives = root / '.reports/timing'
            malformed = dict(samples[0], seconds={'harness': [], 'check_work': None})
            missing_exit = dict(samples[-1], checks=[dict(row) for row in samples[-1]['checks']])
            del missing_exit['checks'][0]['exit_code']
            self.write_archive(archives / 'mixed.json', [{}, malformed, *samples[:-1], missing_exit, *failed_samples],
                               [False, *clocks, failed_clock])
            (archives / 'deep.json').write_bytes(b'[' * 12000 + b'0' + b']' * 12000)
            observation = observations[1][0]
            source = {'git_head': observation['head_sha'], 'working_tree_dirty': False}
            with (patch.object(ci, 'github_identity', return_value='fixture/scopehub'),
                  patch.object(ci, 'source_identity', return_value=source),
                  patch.object(ci, 'checkout_branch', return_value='topic'),
                  patch.object(ci, 'independent_status', return_value={'status': 'missing', 'run': None}),
                  patch.object(timing, 'command', side_effect=AssertionError('Unexpected timing request'))):
                for status, row in (('passed', observation), ('failed', failed),
                                    ('blocked', dict(observation, status='queued'))):
                    with self.subTest(status=status), patch.object(ci, 'workflow_runs', return_value=[row]):
                        result = ci.status(root)
                    self.assertEqual(status, result['status'])
                    budget = result['verification_budget']
                    breach, = budget['repeated_breaches']
                    self.assertEqual(['14', '15'], breach['recent_runs'])
                    self.assertEqual('assignment_needed', breach['status'])
                    self.assertGreaterEqual(len(budget['input_issues']), 5)

    def test_incomplete_and_conflicting_clocks_cannot_supply_a_recurrence(self):
        first, second, third = [self.budget_run(day, '17:00') for day in (14, 15, 16)]
        samples = [row for _, rows, _ in (first, second, third) for row in rows]
        a, b, c = [clock for _, _, clock in (first, second, third)]
        self.assertEqual(1, len(timing.budget_status(samples, [a, b], [], [], {})['repeated_breaches']))
        incomplete = dict(b, jobs=[row for row in b['jobs'] if row['platform'] == 'Windows'])
        partial = timing.budget_status(samples, [a, incomplete], [], [], {})
        self.assertTrue(partial['timing_available'])
        self.assertEqual([], partial['repeated_breaches'])

        conflicting_jobs = []
        for row in b['jobs']:
            if row['platform'] == 'Windows':
                finish = timing.timestamp(row['endpoints']['requested']) + timedelta(seconds=840)
                row = dict(row, endpoints={**row['endpoints'], 'end': finish.isoformat()})
            conflicting_jobs.append(row)
        conflict = dict(b, jobs=conflicting_jobs, workflow={**b['workflow'], 'total_elapsed_seconds': 840})
        for duplicates in ((b, conflict), (conflict, b)):
            with self.subTest(duplicates=duplicates[0]['workflow']):
                view = timing.budget_status(samples, [a, *duplicates, c], [], [], {})
                breach, = view['repeated_breaches']
                self.assertEqual(['14', '16'], breach['recent_runs'])
                self.assertTrue(any(issue.get('run_id') == '15' and issue.get('platform') == 'Windows'
                                    and 'total' in issue.get('fields', ())
                                    and issue['error'] == 'Conflicting protected job measurements.'
                                    for issue in view['input_issues']))

    def test_current_capture_backfills_and_merges_local_and_shared_observations(self):
        run, rows, clock = self.budget_run(14, '17:00')
        linux, windows = rows
        repository = 'fixture/scopehub'
        existing_error = {'run_id': '14', 'error': 'Compact reports are absent for part of the required matrix.'}
        for kind, local_rows, shared_rows, expected_downloads in (
            ('complete local', rows, [], 0),
            ('partial local', [linux], [], 1),
            ('complementary archives', [windows], [linux], 0),
            ('retry partial shared', [], [linux], 1),
        ):
            with self.subTest(case=kind), tempfile.TemporaryDirectory() as directory:
                base = Path(directory).resolve()
                root = base / 'checkout'
                # Use a real alternate spelling of the same plain directory on both hosts.
                shared = root / '..' / 'common'
                (root / 'project').mkdir(parents=True)
                for name, content in (('tasks', {'tasks': []}), ('feedback', {'items': []})):
                    (root / 'project' / f'{name}.json').write_bytes(timing.json_bytes(content))
                if local_rows:
                    local_clocks = [] if shared_rows else [clock]
                    self.write_archive(root / '.reports/timing/local.json', local_rows, local_clocks)
                path = shared / 'ci-pull_request-14-1.json'
                if shared_rows:
                    shared.mkdir()
                    timing.save_timing_capture(path, 1, shared_rows, [clock], [existing_error])
                with (patch.object(timing, 'shared_timing_directory', return_value=shared),
                      patch.object(timing, 'download_timing', return_value=[windows]) as download,
                      patch.object(timing, 'inspect_jobs', side_effect=AssertionError('Unexpected jobs request'))):
                    current = timing.ci_budget_status(root, repository, run)
                    repeated = timing.ci_budget_status(root, repository, run)
                self.assertEqual('captured' if expected_downloads else 'retained', current['intake'])
                self.assertEqual('retained', repeated['intake'])
                self.assertEqual([], current['input_issues'])
                self.assertEqual([], repeated['input_issues'])
                persisted = timing.read_json(path)
                self.assertCountEqual(['Linux', 'Windows'], [row['platform'] for row in persisted['samples']])
                self.assertEqual([clock], persisted['hosted'])
                self.assertEqual([], persisted['input_issues'])
                self.assertEqual(expected_downloads, download.call_count)
                if expected_downloads:
                    download.assert_called_once_with(repository, '14', '1', platforms={'Windows'})

    def test_linked_timing_index_rejects_redirected_directories(self):
        run, rows, clock = self.budget_run(14, '17:00')
        key = timing.report_key(rows[-1])
        finding = self.budget_finding('pull_request', 'b' * 40, (run, rows, clock))
        for redirect in ('index', 'identity'):
            with self.subTest(redirect=redirect), tempfile.TemporaryDirectory() as directory:
                base = Path(directory).resolve()
                root, shared, outside = (base / name for name in ('checkout', 'common', 'outside'))
                outside.mkdir()
                identity = timing.link_directory(shared, key)
                link = identity.parent if redirect == 'index' else identity
                link.parent.mkdir(parents=True)
                if os.name == 'nt':
                    from _winapi import CreateJunction
                    CreateJunction(str(outside), str(link))
                else:
                    link.symlink_to(outside, target_is_directory=True)
                records = root / 'project'
                records.mkdir(parents=True)
                (records / 'tasks.json').write_bytes(timing.json_bytes({'tasks': []}))
                (records / 'feedback.json').write_bytes(timing.json_bytes({'items': [finding]}))
                self.write_archive(root / '.reports/timing/local.json', rows, [clock])
                with (patch.object(timing, 'shared_timing_directory', return_value=shared),
                      patch.object(timing, 'command', side_effect=AssertionError('Unexpected hosted request'))):
                    current = timing.ci_budget_status(root, 'fixture/scopehub', run)
                    offline = timing.offline_budget_status(root, [finding], {})
                self.assertEqual('retained', current['intake'])
                self.assertTrue(any('Could not index the retained capture' in note['error']
                                    for note in current['input_issues']))
                self.assertTrue(any(note.get('report') == key and 'plain' in note['error']
                                    for note in offline['input_issues']))
                self.assertEqual([], list(outside.iterdir()))

    def test_capture_retries_only_platform_without_harness_identity_or_valid_recorded_time(self):
        run, rows, clock = self.budget_run(14, '17:00')
        linux, windows = rows
        for field, value in (('run_id', None), ('recorded_at', None), ('recorded_at', 'invalid timestamp'),
                             ('recorded_at', '2026-11-14T00:01:00')):
            with self.subTest(field=field, value=value), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                invalid = {**windows, field: value}
                view = timing.timing_view([linux, invalid], hosted=[clock])
                self.assertEqual(['Linux'], [row['platform'] for row in view['reviews']])
                with (patch.object(timing, 'download_timing', return_value=[windows]) as download,
                      patch.object(timing, 'inspect_jobs', side_effect=AssertionError('Unexpected jobs request'))):
                    status, captured, _, _ = timing.collect_new_ci_timing(
                        root, 'fixture/scopehub', run, [linux, invalid], [clock],
                    )
                self.assertEqual('captured', status)
                self.assertCountEqual([linux, windows], captured)
                download.assert_called_once_with('fixture/scopehub', '14', '1', platforms={'Windows'})

    def test_conflicting_complete_sample_remains_ambiguous_after_shared_handoff_in_both_orders(self):
        run, rows, clock = self.budget_run(14, '17:00')
        linux, warm = rows
        cold = dict(warm, cache='cold', seconds={**warm['seconds'], 'harness': warm['seconds']['harness'] + 1})
        _, control_rows, control_clock = self.budget_run(15, '17:00')
        _, unrelated_rows, unrelated_clock = self.budget_run(16, '17:00')
        for first, second in ((warm, cold), (cold, warm)):
            with self.subTest(first=first['cache']), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                shared = base / 'common'
                with (patch.object(timing, 'shared_timing_directory', return_value=shared),
                      patch.object(timing, 'download_timing', side_effect=AssertionError('Unexpected artifact request')),
                      patch.object(timing, 'inspect_jobs', side_effect=AssertionError('Unexpected jobs request'))):
                    self.assertEqual('retained', timing.collect_new_ci_timing(
                        base / 'first', 'fixture/scopehub', run, [linux, first], [clock],
                    )[0])
                    status, _, _, _ = timing.collect_new_ci_timing(
                        base / 'second', 'fixture/scopehub', run, [second], [],
                    )
                    self.assertEqual('ambiguous', status)
                    samples, clocks, issues = timing.retained_budget_inputs(base / 'fresh')
                    self.assertEqual('ambiguous', timing.collect_new_ci_timing(
                        base / 'fresh', 'fixture/scopehub', run, samples, clocks,
                    )[0])
                self.assertCountEqual(['warm', 'cold'], [row['cache'] for row in samples if row['platform'] == 'Windows'])
                self.assertTrue(any('Conflicting' in issue['error'] for issue in issues))
                view = timing.budget_status([*samples, *control_rows, *unrelated_rows],
                                            [*clocks, control_clock, unrelated_clock], issues, [], {})
                breach, = view['repeated_breaches']
                self.assertEqual(['15', '16'], breach['recent_runs'])
                self.assertTrue(any('Conflicting' in issue['error'] for issue in view['input_issues']))
                linux_review, = (row for row in view['elapsed_observations'] if row['platform'] == 'Linux')
                self.assertEqual(3, linux_review['sample_count'])

    def test_recent_cold_successes_do_not_hide_warm_recurrence(self):
        observations = [self.budget_run(day, '17:00', cache='warm') for day in (14, 15)]
        observations.extend(self.budget_run(day, '12:00', cache='cold') for day in (16, 17, 18))
        samples = [sample for _, rows, _ in observations for sample in rows]
        clocks = [clock for _, _, clock in observations]
        view = timing.timing_view(samples, hosted=clocks)
        objective = next(row for row in view['objective_reviews'] if row['platform'] == 'Windows')
        self.assertEqual(0, objective['recent_objective_breaches'])
        breach, = timing.budget_follow_up(view, [], {})['repeated_breaches']
        self.assertEqual(('warm', ['14', '15']), (breach['cache'], breach['recent_runs']))

    def test_linked_timing_survives_recent_rotation_and_is_shared_with_fresh_worktrees(self):
        root, (before, repair, after) = self.recovery_fixture()
        shared = timing.shared_timing_directory(root)
        controls = [self.budget_run(day, '20:00', head=before, event='push') for day in (11, 12)]
        success = self.budget_run(27, '14:40', head=after, event='push')
        newer = [self.budget_run(day, '10:00', head=after, event='push', cache='cold')
                 for day in range(13, 13 + timing.MAX_RECENT)]
        for run, samples, clock in [*controls, *newer, success]:
            self.write_archive(shared / f"ci-push-{run['id']}-1.json", samples, [clock])
        finding = self.budget_finding('push', repair, *controls)
        peer = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve() / 'fresh'
        self.fixture_git(root, 'worktree', 'add', '--detach', str(peer), 'HEAD')
        manual = peer / '.reports/timing'
        old = manual / 'old-review.json'
        self.write_archive(old, [sample for _, rows, _ in controls for sample in rows], [c for _, _, c in controls])
        os.utime(old, (1, 1))
        for index in range(4):
            self.write_archive(manual / f'new-review-{index}.json', [], [])
        with patch.object(timing, 'command', side_effect=AssertionError('Unexpected hosted request')):
            absent = timing.offline_budget_status(peer, [finding], {})
            self.assertEqual(2, len(absent['budget_recovery'][0]['recovery']['unavailable_reports']))
            first = timing.index_retained_captures(root)
            repeated = timing.index_retained_captures(root)
            self.assertEqual([], first['input_issues'])
            self.assertGreater(first['new_links'], 0)
            self.assertEqual(0, repeated['new_links'])
            alias = shared.parent / '..' / shared.parent.name / shared.name
            with patch.object(timing, 'shared_timing_directory', return_value=alias):
                recovered = timing.offline_budget_status(peer, [finding], {})['budget_recovery'][0]['recovery']
            self.assertEqual('recovered', recovered['status'])
            self.assertEqual('27', recovered['comparisons'][0]['recovered']['hosted_run_id'])
            self.assertEqual([], recovered['unavailable_reports'])
            self.assertEqual(timing.MAX_RECENT, len(timing.incremental_timing_paths((manual, shared))))

            # A failure retained outside the recent clock window still governs an exact linked attempt.
            failed = dict(controls[0][0], conclusion='failure')
            authority = timing.authenticated_failure(failed, timing.actions_attempt_identity('fixture/scopehub', failed))
            timing.retain_authenticated_failure(root, authority)
            rejected = timing.offline_budget_status(peer, [finding], {})
            self.assertEqual('pending', rejected['budget_recovery'][0]['recovery']['status'])
            self.assertTrue(any(note.get('authenticated_conclusions') == ['failure'] for note in rejected['input_issues']))

    def test_new_collects_index_exact_linked_captures_and_preserve_missing_and_conflicting_inputs(self):
        root, (before, repair, after) = self.recovery_fixture()
        shared = timing.shared_timing_directory(root)
        controls = [self.budget_run(day, '20:00', head=before, event='push') for day in (11, 12)]
        finding = self.budget_finding('push', repair, *controls)
        for run, samples, clock in controls:
            with (patch.object(timing, 'download_timing', return_value=samples),
                  patch.object(timing, 'inspect_jobs', return_value=clock)):
                intake, _, _, issues = timing.collect_new_ci_timing(root, 'fixture/scopehub', run, [], [])
            self.assertEqual(('captured', []), (intake, issues))
        self.retain_newer_cold_attempts(root, 'push', after)
        run, samples, clock = self.budget_run(29, '14:40', head=after, event='push')
        self.write_archive(shared / 'ci-push-29-1.json', samples, [clock])
        records = root / 'project'
        records.mkdir()
        (records / 'feedback.json').write_bytes(timing.json_bytes({'items': [finding]}))
        (records / 'tasks.json').write_bytes(timing.json_bytes({'tasks': []}))
        with patch.object(timing, 'command', side_effect=AssertionError('Unexpected hosted request')):
            ordinary = timing.ci_budget_status(root, 'fixture/scopehub', None)
        self.assertEqual('recovered', ordinary['budget_recovery'][0]['recovery']['status'])
        original_path = shared / 'ci-push-11-1.json'
        original = original_path.read_bytes()
        original_path.unlink()
        missing = timing.offline_budget_status(root, [finding], {})
        missing_key = {key: controls[0][1][-1][key] for key in ('run_id', 'source_commit')}
        self.assertIn(missing_key, missing['budget_recovery'][0]['recovery']['unavailable_reports'])
        self.assertTrue(any(note.get('file') == original_path.name for note in missing['input_issues']))
        original_path.write_bytes(original)
        _, duplicate, duplicate_clock = self.budget_run(10, '20:00', head=before, event='push')
        duplicate[-1].update(missing_key)
        another = shared / 'ci-push-10-1.json'
        self.write_archive(another, duplicate, [duplicate_clock])
        timing.index_capture(another, duplicate)
        ambiguous = timing.offline_budget_status(root, [finding], {})['budget_recovery'][0]['recovery']
        self.assertEqual('pending', ambiguous['status'])
        self.assertEqual([missing_key], ambiguous['conflicting_reports'])
        for absent in ('clock', 'capture'):
            with self.subTest(absent=absent):
                if absent == 'clock':
                    self.write_archive(another, duplicate, [])
                else:
                    another.unlink()
                views = [timing.offline_budget_status(root, [finding], {}),
                         timing.ci_budget_status(root, 'fixture/scopehub', None)]
                for view in views:
                    recovery = view['budget_recovery'][0]['recovery']
                    self.assertEqual('pending', recovery['status'])
                    self.assertEqual([missing_key], recovery['conflicting_reports'])
                    self.assertIn(missing_key, recovery['unavailable_reports'])

    def test_first_ordinary_ci_query_rejects_a_new_linked_claim_without_a_clock(self):
        root, (before, repair, after) = self.recovery_fixture()
        shared = timing.shared_timing_directory(root)
        controls = [self.budget_run(day, '20:00', head=before) for day in (11, 12)]
        success = self.budget_run(14, '14:40', head=after, checkout=self.preview_checkout(root, repair, after))
        for run, rows, clock in [*controls, success]:
            path = shared / f"ci-pull_request-{run['id']}-1.json"
            self.write_archive(path, rows, [clock])
            timing.index_capture(path, rows)
        finding = self.budget_finding('pull_request', repair, *controls)
        records = root / 'project'
        records.mkdir()
        for name, value in (('feedback', {'items': [finding]}), ('tasks', {'tasks': []}),
                            ('branch-protection', {'branch': 'main'})):
            (records / f'{name}.json').write_bytes(timing.json_bytes(value))
        valid = timing.offline_budget_status(root, [finding], {})
        self.assertEqual('recovered', valid['budget_recovery'][0]['recovery']['status'])
        run, claimant, _ = self.budget_run(15, '17:00', head=after, checkout=before)
        claimed = {key: controls[0][1][-1][key] for key in ('run_id', 'source_commit')}
        claimant[-1].update(run_id=claimed['run_id'])
        untouched = timing.report_key(controls[1][1][-1])

        with (patch.object(ci, 'github_identity', return_value='fixture/scopehub'),
              patch.object(ci, 'integration_status', return_value={'status': 'passed', 'run': run}),
              patch.object(ci, 'independent_status', return_value={'status': 'missing', 'run': None}),
              patch.object(timing, 'download_timing', return_value=claimant) as download,
              patch.object(timing, 'inspect_jobs', side_effect=timing.TimingError('Clock unavailable')),
              patch.object(timing, 'link_directory', wraps=timing.link_directory) as lookup):
            first = ci.status(root)['verification_budget']
            self.assertEqual(2, sum(args[1] == untouched for args, _ in lookup.call_args_list))
            repeated = ci.status(root)['verification_budget']
        download.assert_called_once_with('fixture/scopehub', '15', '1')
        self.assertEqual('partial', first['intake'])
        for view in (first, repeated):
            recovery = view['budget_recovery'][0]['recovery']
            self.assertEqual('pending', recovery['status'])
            self.assertEqual([claimed], recovery['conflicting_reports'])

    def test_all_conflicting_pr_controls_leave_valid_push_recovery_visible(self):
        root, (before, repair, after) = self.recovery_fixture()
        shared = timing.shared_timing_directory(root)
        pr_controls = [self.budget_run(day, '20:00', head=before) for day in (11, 12)]
        pr_success = self.budget_run(14, '14:40', head=after, checkout=self.preview_checkout(root, repair, after))
        push_controls = [self.budget_run(day, '20:00', head=before, event='push') for day in (20, 21)]
        push_success = self.budget_run(24, '14:40', head=after, event='push')
        findings = [self.budget_finding(event, repair, *controls) for event, controls in
                    (('pull_request', pr_controls), ('push', push_controls))]
        for run, rows, clock in [*pr_controls, pr_success, *push_controls, push_success]:
            path = shared / f"ci-{run['event']}-{run['id']}-1.json"
            self.write_archive(path, rows, [clock])
            timing.index_capture(path, rows)
        valid = timing.offline_budget_status(root, findings, {})
        self.assertEqual(['recovered', 'recovered'], [row['recovery']['status'] for row in valid['budget_recovery']])
        for day, (_, rows, _) in enumerate(pr_controls, 15):
            _, claimant, _ = self.budget_run(day, '17:00', head=before)
            claimant[-1].update(run_id=rows[-1]['run_id'])
            path = shared / f'ci-pull_request-{day}-1.json'
            self.write_archive(path, claimant, [])
            timing.index_capture(path, claimant)
        records = root / 'project'
        records.mkdir()
        (records / 'feedback.json').write_bytes(timing.json_bytes({'items': findings}))
        (records / 'tasks.json').write_bytes(timing.json_bytes({'tasks': []}))
        for view in (timing.offline_budget_status(root, findings, {}),
                     timing.ci_budget_status(root, 'fixture/scopehub', None)):
            pr, push = (row['recovery'] for row in view['budget_recovery'])
            self.assertEqual('pending', pr['status'])
            self.assertCountEqual([timing.report_key(row) for row in findings[0]['diagnostic_reports']],
                                  [timing.report_key(row) for row in pr['conflicting_reports']])
            self.assertEqual('recovered', push['status'])
            self.assertEqual([], push['unavailable_reports'])
            self.assertEqual('24', push['comparisons'][0]['recovered']['hosted_run_id'])

    def test_two_fresh_worktrees_share_timing_intake_and_reuse_live_assignment(self):
        self.enterContext(isolated_git_environment())
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            repo, first, second = (base / name for name in ('main', 'topic one', 'topic two'))
            author = ['-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', '-c', 'commit.gpgsign=false']

            def git(root, *args):
                return subprocess.run(['git', '-C', str(root), *args], capture_output=True, check=True).stdout

            repo.mkdir()
            git(repo, 'init', '-q')
            records = repo / 'project'
            records.mkdir()
            scope = {'repository': 'fixture/scopehub', 'event': 'pull_request', 'profile': 'full', 'platform': 'Windows'}
            existing = {'id': 'earlier-budget', 'verification_budget': scope, 'status': 'open', 'task_ids': ['M0-999'],
                        'diagnostic_reports': [{'run_id': 'earlier-run', 'source_commit': 'f' * 40}]}
            task = {'id': 'M0-999', 'status': 'active', 'next_action': 'Measure the Windows candidate.'}
            for file, content in (('tasks', {'tasks': [task]}), ('feedback', {'items': [existing]}),
                                  ('branch-protection', {'branch': 'main'})):
                (records / f'{file}.json').write_bytes(timing.json_bytes(content))
            git(repo, 'add', '.')
            git(repo, *author, 'commit', '-qm', 'Fixture records')
            observations = {}
            for day, root in ((14, first), (15, second)):
                git(repo, 'worktree', 'add', '--detach', str(root), 'HEAD')
                git(root, *author, 'commit', '-qm', f'PR {day}', '--allow-empty')
                head = git(root, 'rev-parse', 'HEAD').decode().strip()
                observations[root] = self.budget_run(day, '17:00', head=head)

            answers = {str(run['id']): (samples, clock) for run, samples, clock in observations.values()}
            with (patch.object(ci, 'github_identity', return_value=scope['repository']),
                  patch.object(ci, 'checkout_branch', return_value='topic'),
                  patch.object(ci, 'independent_status', return_value={'status': 'missing', 'run': None}),
                  patch.object(ci, 'workflow_runs', side_effect=lambda root, *_args, **_kw: [observations[root][0]]),
                  patch.object(timing, 'download_timing', side_effect=lambda _, run_id, __: answers[run_id][0]) as download,
                  patch.object(timing, 'inspect_jobs', side_effect=lambda run, _: answers[str(run['id'])][1]) as jobs):
                first_view = ci.status(first)['verification_budget']
                second_view = ci.status(second)['verification_budget']
                self.assertEqual('retained', ci.status(first)['verification_budget']['intake'])
                offline = timing.offline_budget_status(second, [existing], {task['id']: task})

            self.assertEqual([], first_view['repeated_breaches'])
            breach, = second_view['repeated_breaches']
            self.assertEqual(('assigned', ['14', '15']), (breach['status'], breach['recent_runs']))
            self.assertEqual(['earlier-budget'], [finding['feedback_id'] for finding in breach['feedback']])
            self.assertEqual(2, len(breach['unrecorded_reports']))
            self.assertIn('existing corrective feedback', second_view['next_action'])
            self.assertEqual(second_view['repeated_breaches'], offline['repeated_breaches'])
            self.assertEqual((2, 2), (download.call_count, jobs.call_count))
            self.assertEqual(timing.shared_timing_directory(first), timing.shared_timing_directory(second))

    def test_invalid_or_interrupted_capture_can_retry_and_overlapping_poll_does_not_fetch(self):
        run, samples, clock = self.budget_run(14, '17:00')
        repository = 'fixture/scopehub'
        for mode in ('invalid', 'interrupted'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                path = root / '.reports/timing/ci-pull_request-14-1.json'
                path.parent.mkdir(parents=True)

                def observe():
                    retained, clocks, _ = timing.retained_budget_inputs(root)
                    return timing.collect_new_ci_timing(root, repository, run, retained, clocks)[0]

                if mode == 'invalid':
                    bad = b'{'
                    path.write_bytes(bad)
                    with (patch.object(timing, 'download_timing', side_effect=AssertionError('Unexpected artifact request')),
                          self.assertRaisesRegex(timing.TimingError, 'Could not read retained timing capture')):
                        observe()
                    self.assertEqual(bad, path.read_bytes())
                    path.rename(path.with_name('retained-invalid.json.bak'))
                else:
                    with patch.object(timing, 'download_timing', side_effect=KeyboardInterrupt):
                        with self.assertRaises(KeyboardInterrupt):
                            observe()
                    self.assertFalse(path.exists())

                def download(*_args):
                    self.assertEqual('in_progress', observe())
                    return samples

                with (patch.object(timing, 'download_timing', side_effect=download) as artifact,
                      patch.object(timing, 'inspect_jobs', return_value=clock) as jobs):
                    self.assertEqual('captured', observe())
                    self.assertEqual('retained', observe())
                artifact.assert_called_once()
                jobs.assert_called_once()

    def test_shared_capture_size_validation_and_retry_preserve_the_original(self):
        run, samples, clock = self.budget_run(14, '17:00')
        linux, windows = samples
        windows = dict(windows, run_id=fast_report(15, 180)['run_id'])
        samples = [linux, windows]
        repository = 'fixture/scopehub'
        with isolated_git_environment(), tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture_git(root, 'init', '-q')
            shared = timing.shared_timing_directory(root)
            shared.mkdir(parents=True)
            path = shared / 'ci-pull_request-14-1.json'
            padded = dict(linux, padding='')
            initial = {'kind': timing.ARCHIVE_KIND, 'intake_attempts': 0, 'samples': [padded],
                       'hosted': [clock], 'input_issues': []}
            empty_size = len(json.dumps(initial, separators=(',', ':')).encode('ascii')) + 1
            padded['padding'] = 'x' * (timing.TIMING_READ_LIMIT - empty_size)
            timing.save_timing_capture(path, 0, [padded], [clock], [])
            at_limit = path.read_bytes()
            self.assertEqual(timing.TIMING_READ_LIMIT, len(at_limit))
            self.assertEqual([padded], timing.read_timing_capture(path)[1])
            self.assertEqual(1, timing.index_retained_captures(root)['new_links'])
            with self.assertRaisesRegex(timing.TimingError, 'exceeds the compact read limit'):
                timing.save_timing_capture(path, 0, [dict(padded, padding=padded['padding'] + 'x')], [clock], [])
            with self.assertRaisesRegex(timing.TimingError, 'Invalid timing sample'):
                timing.save_timing_capture(path, 0, [dict(padded, cache='fail')], [clock], [])
            self.assertEqual(at_limit, path.read_bytes())

            for invalid in (at_limit + b' ', b'{"kind":'):
                path.write_bytes(invalid)
                with (patch.object(timing, 'download_timing', side_effect=AssertionError('Unexpected artifact request')),
                      self.assertRaisesRegex(timing.TimingError, 'Could not read retained timing capture')):
                    timing.collect_new_ci_timing(root, repository, run, samples, [clock])
                self.assertEqual(invalid, path.read_bytes())
                self.assertTrue(timing.index_retained_captures(root)['input_issues'])
                rows, clocks, issues = timing.retained_budget_inputs(root)
                self.assertEqual(([], []), (rows, clocks))
                self.assertIn('Could not read retained timing capture', issues[0]['error'])
                self.assertEqual(1, len(list(shared.glob('linked-harness/*/ci-*.json'))))

            padded['padding'] = padded['padding'][:-8192]
            timing.save_timing_capture(path, 0, [padded], [clock], [])
            before = path.read_bytes()
            large = dict(windows, padding='x' * 16384)
            with (patch.object(timing, 'download_timing', side_effect=iter((KeyboardInterrupt, [large], [windows]))) as get,
                  patch.object(timing, 'inspect_jobs', side_effect=AssertionError('Unexpected jobs request'))):
                with self.assertRaises(KeyboardInterrupt):
                    timing.collect_new_ci_timing(root, repository, run, [], [])
                self.assertEqual(before, path.read_bytes())
                with self.assertRaisesRegex(timing.TimingError, 'exceeds the compact read limit'):
                    timing.collect_new_ci_timing(root, repository, run, [], [])
                self.assertEqual(before, path.read_bytes())
                self.assertEqual(0, timing.read_timing_capture(path)[0])
                self.assertEqual(1, len(list(shared.glob('linked-harness/*/ci-*.json'))))
                self.assertEqual('captured', timing.collect_new_ci_timing(root, repository, run, [], [clock])[0])
                after = path.read_bytes()
                self.assertEqual('retained', timing.collect_new_ci_timing(root, repository, run, [], [clock])[0])
            self.assertEqual(3, get.call_count)
            self.assertEqual(after, path.read_bytes())
            self.assertEqual(1, timing.read_timing_capture(path)[0])
            self.assertEqual(2, len(list(shared.glob('linked-harness/*/ci-*.json'))))

    def test_ci_status_collects_one_new_attempt_and_reuses_partial_and_complete_clocks(self):
        repository = 'fixture/scopehub'
        source = {'git_head': 'a' * 40, 'working_tree_dirty': False}
        run = hosted_run()
        linux = hosted_job(21, 'ubuntu', '00:10', '04:00', 'Run pnpm check', '00:30', '03:50')
        windows = hosted_job(22, 'windows', '00:20', '17:00',
                             'Run uv run python scripts/check.py --require-windows', '00:50', '16:40')
        workflow = timing.hosted_timing(run, [linux, windows])
        samples = []
        for platform in ('Linux', 'Windows'):
            report = fast_report(20, 180)
            report['selection']['profile'] = 'full'
            report['environment']['system'] = platform
            report['ci'] = {'repository': repository, 'run_id': '11', 'run_attempt': '1', 'event': 'pull_request',
                            'head_sha': source['git_head'], 'checkout_sha': source['git_head']}
            samples.append(timing.report_sample(report))
        for selection, expected in ((samples, 'captured'), (samples[1:], 'partial')):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                for name, content in [('branch-protection', {'branch': 'main'}), ('tasks', {'tasks': []}),
                                      ('feedback', {'items': []})]:
                    path = root / 'project' / f'{name}.json'
                    path.parent.mkdir(exist_ok=True)
                    path.write_bytes(timing.json_bytes(content))
                with (patch.object(ci, 'github_identity', return_value=repository),
                      patch.object(ci, 'source_identity', return_value=source),
                      patch.object(ci, 'checkout_branch', return_value='topic'),
                      patch.object(ci, 'independent_status', return_value={'status': 'missing', 'run': None}),
                      patch.object(ci, 'workflow_runs', return_value=[run]),
                      patch.object(timing, 'download_timing', side_effect=[selection, []]) as download,
                      patch.object(timing, 'inspect_jobs', return_value=workflow) as jobs):
                    first = ci.status(root)
                    repeat = ci.status(root)
                    third = ci.status(root)
                    ordinary = timing.offline_budget_status(root, [], {})
                self.assertEqual(('passed', 'passed'), (first['status'], repeat['status']))
                self.assertEqual(expected, first['verification_budget']['intake'])
                self.assertEqual('retained' if expected == 'captured' else 'partial',
                                 repeat['verification_budget']['intake'])
                self.assertEqual('retained' if expected == 'captured' else 'retry_exhausted',
                                 third['verification_budget']['intake'])
                self.assertEqual(ordinary['elapsed_observations'], repeat['verification_budget']['elapsed_observations'])
                self.assertEqual(len(selection), len(ordinary['elapsed_observations']))
                self.assertTrue(all(row['sample_count'] == 1 for row in ordinary['elapsed_observations']))
                self.assertEqual(expected == 'partial', bool(ordinary['input_issues']))
                self.assertEqual(1 if expected == 'captured' else 2, download.call_count)
                self.assertEqual((repository, '11', '1'), download.call_args_list[0].args)
                if expected == 'partial':
                    self.assertEqual({'platforms': {'Linux'}}, download.call_args_list[1].kwargs)
                jobs.assert_called_once()

    def test_recent_queries_are_bounded_to_completed_pr_and_main_workflow_runs(self):
        pr = dict(hosted_run(run_id=11), conclusion='failure')
        main = dict(hosted_run(run_id=12), event='push', head_branch='main')
        with patch.object(timing, 'command', side_effect=[
            json.dumps({'workflow_runs': [pr]}),
            json.dumps({'workflow_runs': [main]}),
        ]) as gh:
            self.assertCountEqual([pr, main], timing.recent_runs('fixture/scopehub', 8))

        requests = []
        for call in gh.call_args_list:
            argv = call.args[0]
            self.assertEqual(['gh', 'api'], argv[:2])
            self.assertNotIn('--paginate', argv)
            endpoint = next(arg for arg in argv if arg.startswith('repos/'))
            parsed = urlsplit(endpoint)
            self.assertEqual('repos/fixture/scopehub/actions/workflows/verify.yml/runs', parsed.path)
            fields = {key: values[0] for key, values in parse_qs(parsed.query).items()}
            for index, arg in enumerate(argv[:-1]):
                if arg in {'-f', '-F', '--raw-field', '--field'}:
                    key, value = argv[index + 1].split('=', 1)
                    fields[key] = value
            requests.append(fields)
        self.assertCountEqual(requests, [
            {'event': 'pull_request', 'status': 'completed', 'per_page': '8'},
            {'event': 'push', 'branch': 'main', 'status': 'completed', 'per_page': '8'},
        ])

    def test_recent_empty_result_uses_pinned_account_and_skips_per_run_inspection(self):
        output = StringIO()
        with (patch.object(sys, 'argv', ['timing.py', '--recent']), redirect_stdout(output),
              patch.object(timing, 'local_paths', return_value=[]),
              patch.object(timing, 'github_identity', return_value='fixture/scopehub') as identity,
              patch.object(timing, 'recent_runs', return_value=[]) as recent,
              patch.object(timing, 'inspect_hosted') as inspect):
            timing.main()

        identity.assert_called_once_with(timing.ROOT)
        recent.assert_called_once_with('fixture/scopehub', 8)
        inspect.assert_not_called()
        view = json.loads(output.getvalue())
        self.assertEqual([], view['hosted'])
        self.assertEqual([], view['objective_reviews'])
        collection = view['recent_collection']
        self.assertEqual(8, collection['limit_per_event'])
        for key in ('completed_runs_considered', 'runs_with_compact', 'runs_without_compact'):
            with self.subTest(key=key):
                self.assertEqual(0, collection[key])
        self.assertTrue(view['next_action'])

    def test_recent_limit_is_rejected_before_authentication(self):
        with (patch.object(sys, 'argv', ['timing.py', '--recent', '13']), redirect_stderr(StringIO()),
              patch.object(timing, 'github_identity') as identity, self.assertRaises(SystemExit) as stopped):
            timing.main()
        self.assertEqual(2, stopped.exception.code)
        identity.assert_not_called()

    def test_weekly_job_token_path_publishes_summary_and_compact_archive(self):
        run = dict(hosted_run(), repository={'full_name': 'fixture/scopehub'})
        linux = hosted_job(31, 'ubuntu', '00:10', '04:00', 'Run pnpm check', '00:30', '03:50')
        windows = hosted_job(32, 'windows', '00:20', '06:00',
                             'Run uv run python scripts/check.py --require-windows', '00:50', '05:20')
        report = historical_report()
        report['ci'] = {
            'repository': 'fixture/scopehub', 'run_id': '11', 'run_attempt': '1',
            'event': 'pull_request', 'head_sha': 'a' * 40, 'checkout_sha': report['source']['git_head'],
        }
        compact = timing.report_sample(report)
        with tempfile.TemporaryDirectory() as temp:
            archive, summary = (Path(temp) / name for name in ('timing.json', 'summary.md'))
            argv = ['timing.py', '--recent', '2', '--github-actions', '--save', str(archive), '--summary', str(summary)]
            with (patch.object(sys, 'argv', argv), redirect_stdout(StringIO()),
                  patch.dict(os.environ, {'GITHUB_ACTIONS': 'true', 'GITHUB_REPOSITORY': 'fixture/scopehub',
                                          'GH_TOKEN': 'synthetic'}),
                  patch.object(timing, 'local_paths', return_value=[]),
                  patch.object(timing, 'github_identity') as identity,
                  patch.object(timing, 'recent_runs', return_value=[run]),
                  patch.object(timing, 'download_timing', return_value=[compact]),
                  patch.object(timing, 'inspect_jobs', return_value=timing.hosted_timing(run, [linux, windows]))):
                timing.main()
            identity.assert_not_called()
            saved = json.loads(archive.read_text(encoding='utf-8'))
            self.assertEqual([compact], saved['samples'])
            self.assertEqual(1, saved['recent_collection']['runs_with_compact'])
            self.assertFalse(saved['recent_collection']['cold_bootstrap'])
            self.assertIn('recent median 240s, objective 900s', summary.read_text(encoding='utf-8'))

    def test_hosted_inspection_only_downloads_small_timing_artifacts(self):
        artifacts = [
            {'name': 'verification-windows-latest', 'expired': False, 'size_in_bytes': 10},
            {'name': 'verification-timing-windows-latest', 'expired': False, 'size_in_bytes': 10},
            {'name': 'verification-timing-ubuntu-latest', 'expired': False, 'size_in_bytes': 128 * 1024 + 1},
        ]
        with patch.object(timing, 'command', side_effect=[json.dumps({'artifacts': artifacts}), '']) as gh:
            timing.download_timing('fixture/scopehub', '11', '1')
        self.assertEqual(2, gh.call_count)
        argv = gh.call_args_list[1].args[0]
        self.assertEqual(['gh', 'run', 'download', '11'], argv[:4])
        self.assertEqual(['verification-timing-windows-latest'],
                         [argv[index + 1] for index, arg in enumerate(argv[:-1]) if arg == '--name'])
