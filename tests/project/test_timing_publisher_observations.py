"""Exercise publisher eligibility and evidence identity at the public timing entry."""

from contextlib import redirect_stdout
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import timing
from tests.project import test_timing as fixtures


class ArchivedSplitPublisherTests(unittest.TestCase):
    def test_python_part_without_ordinary_checks_is_a_source_bound_producer(self):
        catalog = deepcopy(fixtures.SPLIT_CATALOG)
        check = next(row for row in catalog['checks'] if row['id'] == 'python-contracts-workers')
        check['command'] = ['{python}', '-m', 'unittest', 'discover', '-s', 'tests', '-t', '.',
                            '-p', 'test_*.py', '-v', '--durations', '0']
        fixtures.use_catalog(self, catalog)
        report = fixtures.split_harness_report(90)
        report['ci'] = {'repository': 'fixture/scopehub', 'run_id': '11', 'run_attempt': '1',
                        'event': 'pull_request', 'head_sha': 'b' * 40, 'checkout_sha': 'a' * 40}
        report['selection'] = {'profile': 'ci-windows-python', 'task_id': None, 'checks': []}
        report['checks'] = [{'id': row['id'], 'status': 'blocked', 'reason_code': 'not_selected'}
                            for row in catalog['checks']]
        fixtures.native_raw_fixture(self, report, catalog)
        part = 'windows-python-python'
        report.update(report_version='2.1', windows_python={'python': {
            'id': part, 'status': 'passed', 'exit_code': 0, 'duration_seconds': 65,
            'started_at': '2026-11-20T00:11:10Z',
            'command': [report['environment']['python_executable'], 'scripts/windows_python.py', 'python'],
            'log': f".reports/runs/{report['run_id']}/{part}.log",
            'log_sha256': '3' * 64, 'manifest_sha256': '4' * 64,
        }})
        publisher = {'catalogs': {timing.publishing_catalog_digest(catalog)},
                     'split_recovery_checks': ['workspace-timeframe'],
                     'run_observations': timing.RUN_OBSERVATION_CHECKS}
        with patch.object(timing, 'committed_windows_publisher', return_value=publisher):
            sample = timing.input_report_sample(report)
            self.assertEqual([], sample['selected'])
            self.assertFalse(sample['coverage_complete'])
            self.assertFalse(timing.native_report_issue(sample, None, []))
            self.assertTrue(timing.native_report_issue(dict(sample, selected=['python-contracts-workers']), None, []))
            with self.assertRaisesRegex(timing.TimingError, 'publisher status.*its check results and Python parts'):
                timing.input_report_sample(dict(report, status='failed'))
            failed = deepcopy(report)
            failed['status'] = 'failed'
            failed['windows_python']['python'].update(status='failed', exit_code=1)
            failed_sample = timing.input_report_sample(failed)
            self.assertFalse(timing.native_report_issue(failed_sample, None, []))
            with self.assertRaisesRegex(timing.TimingError, 'differ from the publishing lane'):
                timing.input_report_sample(dict(report, windows_python={}))
            stopped = deepcopy(report)
            stopped['status'] = 'failed'
            stopped['windows_python']['python'] = {
                key: stopped['windows_python']['python'][key] for key in ('id', 'command')
            } | {'status': 'failed', 'reason_code': 'earlier_failure'}
            with self.assertRaisesRegex(timing.TimingError, 'without a producer stop'):
                timing.input_report_sample(stopped)
            for key, value, error in (
                ('log', 'unbound.log', 'invalid Windows Python process reference'),
                ('started_at', '2026-11-20T00:10:00Z', 'outside its harness interval'),
                ('exit_code', False, 'invalid Windows Python process reference'),
            ):
                with self.subTest(key=key):
                    changed = deepcopy(report)
                    changed['windows_python']['python'][key] = value
                    with self.assertRaisesRegex(timing.TimingError, error):
                        timing.input_report_sample(changed)
            pushed = dict(report, ci={**report['ci'], 'event': 'push'})
            with self.assertRaisesRegex(timing.TimingError, 'invalid native harness metadata'):
                timing.input_report_sample(pushed)
        with patch.object(timing, 'committed_windows_publisher', return_value=None):
            with self.assertRaisesRegex(timing.TimingError, 'invalid native harness metadata'):
                timing.input_report_sample(report)
            self.assertTrue(timing.native_report_issue(sample, None, []))

    def test_split_aggregate_status_is_derived_from_parts_and_historical_timeout(self):
        catalog = deepcopy(fixtures.SPLIT_CATALOG)
        check = next(row for row in catalog['checks'] if row['id'] == 'python-contracts-workers')
        check.update(command=['{python}', '-m', 'unittest', 'discover', '-s', 'tests', '-t', '.',
                              '-p', 'test_*.py', '-v', '--durations', '0'],
                     timeout_seconds=10, windows_timeout_seconds=70)
        fixtures.use_catalog(self, catalog)
        report = fixtures.split_harness_report(100)
        report['ci'] = {'repository': 'fixture/scopehub', 'run_id': '11', 'run_attempt': '1',
                        'event': 'pull_request', 'head_sha': 'b' * 40, 'checkout_sha': 'a' * 40}
        fixtures.native_raw_fixture(self, report, catalog)
        report.update(report_version='2.1', windows_producers={'browser': '1' * 64}, windows_python={})
        for lane, started in (('python', '00:11:05'), ('recovery', '00:11:20')):
            part = f'windows-python-{lane}'
            report['windows_python'][lane] = {
                'id': part, 'status': 'passed', 'exit_code': 0, 'duration_seconds': 50,
                'started_at': f'2026-11-20T{started}Z',
                'command': [report['environment']['python_executable'], 'scripts/windows_python.py', lane],
                'log': f".reports/runs/{report['run_id']}/{part}.log", 'log_sha256': '3' * 64,
                'manifest_sha256': '4' * 64, 'producer_report_sha256': '5' * 64,
            }
        combined = next(row for row in report['checks'] if row['id'] == check['id'])
        combined['duration_seconds'] = 65
        publisher = {'catalogs': {timing.publishing_catalog_digest(catalog)},
                     'split_recovery_checks': [], 'run_observations': timing.RUN_OBSERVATION_CHECKS}
        with patch.object(timing, 'committed_windows_publisher', return_value=publisher):
            self.assertEqual('passed', timing.input_report_sample(report)['report_status'])
            invalid_gates = deepcopy(report)
            invalid_gates['integration_gates'].update(status='blocked', unverified=[check['id']])
            with self.assertRaisesRegex(timing.TimingError, 'integration gates.*contradict'):
                timing.input_report_sample(invalid_gates)
            report['status'] = 'failed'
            combined.update(status='failed', exit_code=1)
            report['integration_gates'] = timing.integration_gates(
                report['checks'], catalog['required_change_checks'], requested=True
            )
            with self.assertRaisesRegex(timing.TimingError, 'aggregate measurement.*differs from its parts'):
                timing.input_report_sample(report)
            report['windows_python']['recovery'].update(status='failed', exit_code=1)
            self.assertEqual('failed', timing.input_report_sample(report)['report_status'])
            report['windows_python']['recovery'].update(status='passed', exit_code=0, duration_seconds=60)
            combined.update(exit_code=None, reason_code='timeout', duration_seconds=75)
            self.assertEqual('failed', timing.input_report_sample(report)['report_status'])
            combined['exit_code'] = 1
            with self.assertRaisesRegex(timing.TimingError, 'aggregate measurement.*differs from its parts'):
                timing.input_report_sample(report)
            part = report['windows_python']['recovery']
            part.update(status='failed', exit_code=None, reason_code='launch_error')
            part.pop('manifest_sha256')
            combined.update(exit_code=None, reason_code='launch_error')
            launching = timing.input_report_sample(report)
            self.assertFalse(timing.native_report_issue(launching, None, []))
            for field in ('started_at', 'duration_seconds', 'exit_code', 'log', 'log_sha256'):
                part.pop(field)
            part.update(status='blocked', reason_code='executable_unavailable')
            combined.update(exit_code=1, reason_code='executable_unavailable', duration_seconds=50)
            unavailable = timing.input_report_sample(report)
            self.assertFalse(timing.native_report_issue(unavailable, None, []))


class PublicPublisherObservationTests(unittest.TestCase):
    def setUp(self):
        fixtures.use_catalog(self, fixtures.OLD_FULL_CATALOG, lanes=())
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.run = fixtures.hosted_run()
        self.jobs = [
            fixtures.hosted_job(31, 'ubuntu', '00:10', '04:00', 'Run pnpm check', '00:30', '03:50'),
            fixtures.hosted_job(32, 'windows', '00:20', '06:00', timing.WINDOWS_FULL_STEP, '00:50', '05:20'),
        ]
        self.clock = timing.actions_clock(self.run, self.jobs)
        ci = dict(zip(timing.ATTEMPT_IDENTITY, timing.ci_key(self.clock)), checkout_sha='a' * 40)
        report = dict(fixtures.historical_report(), ci=ci)
        self.linux = timing.report_sample(report, cache='warm')
        report.update(status='passed', environment={'system': 'Windows', 'machine': 'AMD64'})
        report['checks'][3] = {
            'id': 'windows-native-build', 'status': 'passed', 'exit_code': 0, 'duration_seconds': 12,
        }
        self.windows_report = report
        self.windows = timing.report_sample(report, cache='warm')

    def invoke(self, *args):
        output = StringIO()
        with (patch.object(sys, 'argv', ['timing.py', *map(str, args)]), redirect_stdout(output),
              patch.object(timing, 'local_paths', return_value=[]),
              patch.object(timing, 'timing_directories', return_value=[])):
            timing.main()
        return json.loads(output.getvalue())

    def collect(self, archive, samples):
        with (patch.object(timing, 'hosted_repository', return_value='fixture/scopehub'),
              patch.object(timing, 'collection_source', return_value='c' * 40),
              patch.object(timing, 'recent_runs', return_value=[self.run]),
              patch.object(timing, 'download_timing', return_value=samples),
              patch.object(timing, 'inspect_jobs', return_value=self.clock)):
            return self.invoke('--recent', 1, '--github-actions', '--save', archive)

    def weekly(self, archive, sample):
        return self.collect(archive, [self.linux, sample])

    def write_variant(self, name, sample):
        path = self.directory / f'{name}.json'
        path.write_bytes(timing.json_bytes(sample))
        return path

    def assert_view(self, view, windows, issues):
        receipts = view['recent_collection']['collected_samples']
        origins = {row['platform']: row['report_input_sha256'] for row in receipts}
        self.assertEqual(len(origins), len(receipts))
        self.assertEqual({'Linux': timing.report_input_sha256(self.linux), **windows}, origins)
        admitted = {row['platform']: (row['seconds']['total'], row['seconds']['runner'], row['cache'])
                    for row in view['samples'] if row['review_exclusion'] is None}
        expected = {'Linux': (240, 230, 'warm')}
        if windows:
            expected['Windows'] = (360, 340, 'warm')
        self.assertEqual(expected, admitted)
        self.assertEqual(issues, [issue['error'] for issue in view['input_issues']])

    def test_blocked_windows_summary_requires_an_independent_blocked_result(self):
        archive = self.directory / 'genuine.json'
        original = {'Windows': timing.report_input_sha256(self.windows)}
        issue = 'Native timing report has a blocked status that contradicts its completed Windows checks.'
        self.assert_view(self.weekly(archive, self.windows), original, [])
        contradicted = dict(self.windows, report_status='blocked')
        self.assert_view(self.weekly(self.directory / 'contradicted.json', contradicted), {}, [issue])
        variant = self.write_variant('altered', contradicted)
        for paths in ((archive, variant), (variant, archive)):
            self.assert_view(self.invoke('--report', paths[0], '--report', paths[1]), original, [issue])

        blocked = dict(contradicted, coverage_complete=False, checks=[
            dict(row, status='blocked', exit_code=None, duration_seconds=None, reason_code='executable_unavailable')
            if row['id'] == 'environment' else row for row in contradicted['checks']
        ], seconds=dict(contradicted['seconds'], check_work=102))
        self.assert_view(self.weekly(self.directory / 'genuinely-blocked.json', blocked), {}, [])

    def test_unsplit_raw_reports_reject_split_publisher_fields(self):
        original = deepcopy(self.windows_report)
        original['timing'] = {'elapsed_seconds': 240}
        fixtures.native_raw_fixture(self, original, fixtures.OLD_FULL_CATALOG)
        self.assertEqual('passed', timing.input_report_sample(original)['report_status'])
        for key, value in (('windows_producers', {'browser': 'a' * 64}),
                           ('windows_python', {})):
            with self.subTest(field=key), self.assertRaisesRegex(timing.TimingError, 'unexpected Windows'):
                timing.input_report_sample(dict(original, **{key: value}))

    def test_weekly_reopen_reconciles_equivalent_instants_and_preserves_exact_receipts(self):
        self.windows['recorded_at'] = '2026-11-20T00:02:00.683387+00:00'
        original = {'Windows': timing.report_input_sha256(self.windows)}
        archive = self.directory / 'genuine.json'
        self.assert_view(self.weekly(archive, self.windows), original, [])
        for label, instant in (
                ('colon', '2026-11-20T05:47:00.683387000+05:45'),
                ('compact', '2026-11-20T05:47:00.683387000+0545'),
                ('fractional', '2026-11-20T05:47:00.683387001+05:45:00.000000001'),
                ('negative', '2026-11-19T18:17:00.683386999-054500.000000001')):
            with self.subTest(offset=label):
                equivalent = dict(self.windows, recorded_at=instant)
                alternative = {'Windows': timing.report_input_sha256(equivalent)}
                self.assertNotEqual(original, alternative)
                self.assert_view(self.weekly(self.directory / f'{label}.json', equivalent), alternative, [])
                variant = self.write_variant(f'altered-{label}', equivalent)
                for paths in ((archive, variant), (variant, archive)):
                    view = self.invoke('--report', paths[0], '--report', paths[1])
                    self.assert_view(view, original, [])
                    admitted, = (row for row in view['samples'] if row['platform'] == 'Windows')
                    self.assertCountEqual([original['Windows'], alternative['Windows']],
                                          admitted['report_inputs_sha256'])

        for fraction in ('683388', '683387001'):
            with self.subTest(fraction=fraction):
                different = self.write_variant('different', dict(
                    self.windows, recorded_at=f'2026-11-20T05:47:00.{fraction}+0545',
                ))
                self.assert_view(self.invoke('--report', archive, '--report', different), {},
                                 ['Conflicting complete timing samples for this run and platform.'])

    def test_full_publisher_requires_a_passed_native_build_even_if_catalog_excludes_it(self):
        for check in ('project-state', 'windows-native-build'):
            with self.subTest(omission=check):
                catalog = deepcopy(fixtures.OLD_FULL_CATALOG)
                catalog['required_change_checks'].remove(check)
                next(row for row in catalog['checks'] if row['id'] == check)['cadence'] = 'task'
                variant = dict(self.windows, selected=[key for key in self.windows['selected'] if key != check])
                variant['checks'] = [
                    dict(row, status='blocked', reason_code='not_selected', duration_seconds=None, exit_code=None)
                    if row['id'] == check else row for row in self.windows['checks']
                ]
                work, complete = timing.report_check_work(variant['checks'])
                variant.update(seconds=dict(variant['seconds'], check_work=work), check_work_complete=complete)
                statuses = ('passed',) if check != 'windows-native-build' else ('passed', 'blocked')
                for status in statuses:
                    sample = dict(variant, report_status=status)
                    archive = self.directory / f'{check}-{status}.json'
                    valid = check != 'windows-native-build'
                    expected = {'Windows': timing.report_input_sha256(sample)} if valid else {}
                    issues = [] if valid else ['Native timing report has complete coverage without a passed Windows native build.']
                    publishing = timing.PublishingSource((sample['source_commit'], catalog))
                    with patch.object(timing, 'publishing_catalog_source', return_value=publishing):
                        view = self.weekly(archive, sample)
                        self.assert_view(view, expected, issues)
                        actions, = view['hosted']
                        windows, = (job for job in actions['jobs'] if job['platform'] == 'Windows')
                        self.assertEqual(360, windows['seconds']['total'])

    def test_raw_report_admits_only_publisher_supported_observations_on_both_platforms(self):
        original = deepcopy(self.windows_report)
        original['timing'] = {'elapsed_seconds': 240}
        original['generated_at'] = '2026-11-20T00:02:00.683387001+00:00'
        catalog = deepcopy(fixtures.OLD_FULL_CATALOG)
        catalog['checks'].append({'id': 'configured-model-session', 'status': 'implemented', 'cadence': 'task',
                                  'command': ['{python}', 'check.py']})
        original['checks'].append({'id': 'configured-model-session', 'status': 'blocked', 'reason_code': 'not_selected'})
        fixtures.native_raw_fixture(self, original, catalog)
        summary = {key: {'status': 'not_evaluated', 'observations': []}
                   for key in ('configured_models', 'cloud_models', 'real_market_data')}
        args = ('--actions', self.write_variant('run', self.run), self.write_variant('jobs', self.jobs))
        for platform, total, runner in (('Windows', 360, 340), ('Linux', 240, 230)):
            for variant, issue in (
                ('original', None),
                ('summary', 'an integration summary that contradicts its check results'),
                ('blocked-observation', 'an unexpected observation'),
                ('foreign-field-observation', 'an unexpected observation'),
            ):
                with self.subTest(platform=platform, variant=variant):
                    report = deepcopy(original)
                    report['environment']['system'] = platform
                    report['integration_validation'] = dict(summary, mock_ai_evaluated=variant == 'summary')
                    if platform == 'Linux':
                        report['environment']['machine'] = 'x86_64'
                        report['native_requirement']['requested'] = False
                        row = next(row for row in report['checks'] if row['id'] == 'windows-native-build')
                        row.update(status='blocked', reason_code='unsupported_platform')
                    if variant == 'blocked-observation':
                        report['checks'][-1]['observation'] = {'deployment': 'cloud'}
                    if variant == 'foreign-field-observation':
                        report['checks'][0]['field_observation'] = {}
                    publishing = timing.PublishingSource((report['source']['git_head'], catalog))
                    path = self.write_variant('raw', report)
                    with patch.object(timing, 'publishing_catalog_source', return_value=publishing):
                        view = self.invoke('--report', path, *args)
                    if issue:
                        self.assertEqual([], view['samples'])
                        self.assertEqual([{'file': path.name, 'error': f'Timing report has {issue}.'}],
                                         view['input_issues'])
                    else:
                        sample, = view['samples']
                        self.assertEqual(report['generated_at'], sample['recorded_at'])
                        self.assertEqual((total, runner), (sample['seconds']['total'], sample['seconds']['runner']))
                        self.assertEqual([], view['input_issues'])
                    actions, = view['hosted']
                    job, = (job for job in actions['jobs'] if job['platform'] == platform)
                    self.assertEqual(total, job['seconds']['total'])

    def test_raw_linux_report_uses_its_historical_publisher_observation_policy(self):
        original = dict(fixtures.historical_report(), ci=self.linux['ci'])
        original['timing'] = {'elapsed_seconds': 180}
        catalog = deepcopy(fixtures.OLD_FULL_CATALOG)
        external = 'configured-external-ai-clients'
        catalog['checks'].append({'id': external, 'status': 'planned', 'cadence': 'task', 'command': None})
        original['checks'].append({'id': external, 'status': 'blocked', 'reason_code': 'not_implemented'})
        fixtures.native_raw_fixture(self, original, catalog)
        original['native_requirement']['requested'] = False
        revision = original['source']['git_head']
        catalog_source = timing.PublishingSource((revision, catalog))
        publisher = {'catalogs': [timing.publishing_catalog_digest(catalog)],
                     'run_observations': timing.RUN_OBSERVATION_POLICIES['session']}
        args = ('--actions', self.write_variant('run', self.run), self.write_variant('jobs', self.jobs))
        with (patch.object(timing, 'publishing_catalog_source', return_value=catalog_source),
              patch.object(timing, 'committed_windows_publisher', return_value=publisher)):
            genuine = self.write_variant('genuine', original)
            view = self.invoke('--report', genuine, *args)
            self.assertEqual([240], [row['seconds']['total'] for row in view['samples']])
            altered = deepcopy(original)
            altered['selection']['checks'].append(external)
            altered['checks'][-1] = {'id': external, 'status': 'passed', 'exit_code': 0, 'duration_seconds': 2}
            for observation, error in (
                (None, 'a passing observation check outside the implemented publishing catalog'),
                ({}, 'an unexpected observation'),
            ):
                if observation is not None:
                    altered['checks'][-1]['observation'] = observation
                rejected = self.write_variant('altered', altered)
                for first, second in ((genuine, rejected), (rejected, genuine)):
                    view = self.invoke('--report', first, '--report', second, *args)
                    self.assertEqual([240], [row['seconds']['total'] for row in view['samples']])
                    self.assertEqual([f'Timing report has {error}.'], [row['error'] for row in view['input_issues']])
            # Older readers could compact the altered raw report before
            # checking the historical rules. Reopen that retained projection.
            compact = timing.report_sample(altered)
            for order in ([compact, timing.report_sample(original)], [timing.report_sample(original), compact]):
                legacy = self.write_variant('legacy-archive', {
                    'kind': timing.ARCHIVE_KIND, 'samples': order, 'hosted': [self.clock],
                })
                view = self.invoke('--report', legacy)
                self.assertCountEqual([240, None], [row['seconds']['total'] for row in view['samples']])
                self.assertEqual(1, len(list(timing.platform_report_receipts(view['samples']))))

    def test_outside_publishing_job_report_cannot_withdraw_valid_receipts_or_actions_clocks(self):
        expected = {'Linux': (240, 230), 'Windows': (360, 340)}
        actions = ('--actions', self.write_variant('job-run', self.run), self.write_variant('job-list', self.jobs))
        diagnostic = 'The recorded report creation is outside the publishing job boundaries.'
        for platform, earliest_end in (('Linux', '04:00'), ('Windows', '06:00')):
            actual = self.linux if platform == 'Linux' else self.windows
            peer = self.windows if platform == 'Linux' else self.linux
            edge = f'2026-11-20T00:{earliest_end}.999999999Z'
            late = f'2026-11-20T00:{earliest_end[:-2]}01.000000001Z'
            bound = dict(actual, recorded_at=edge, inputs_sha256='c' * 64)
            digestless = dict(bound)
            del digestless['inputs_sha256']
            pairs = (
                ('complete', bound, dict(bound, recorded_at=late)),
                ('digestless', digestless, dict(digestless, recorded_at=late)),
                ('incomplete', bound, dict(bound, recorded_at=late, coverage_complete=False)),
            )
            for variant, good, bad in pairs:
                for index, rows in enumerate(([good, bad, peer], [peer, bad, good, deepcopy(bad), deepcopy(good)])):
                    with self.subTest(platform=platform, variant=variant, index=index):
                        archive = self.directory / f'{platform}-{variant}-{index}.json'
                        inputs = [arg for number, row in enumerate(rows) for arg in (
                            '--report', self.write_variant(f'job-report-{number}', row),
                        )]
                        ordinary = self.invoke(*inputs, *actions)
                        collected = self.collect(archive, rows)
                        reopened = self.invoke('--report', archive)
                        receipts = {
                            row['platform']: timing.report_input_sha256(row) for row in (good, peer)
                        }
                        for view in (ordinary, collected, reopened):
                            observed = (view['recent_collection']['collected_samples'] if view.get('recent_collection')
                                        else list(timing.platform_report_receipts(view['samples'])))
                            self.assertEqual(len(receipts), len(observed))
                            self.assertEqual(receipts, {row['platform']: row['report_input_sha256'] for row in observed})
                            clocks = {job['platform']: (job['seconds']['total'], job['seconds']['runner'])
                                      for job in view['hosted'][0]['jobs']}
                            self.assertEqual(expected, clocks)
                            excluded = [row for row in view['samples'] if row['recorded_at'] == late]
                            self.assertEqual([None], [row['seconds']['total'] for row in excluded])
                            self.assertEqual([diagnostic], [issue['error'] for issue in view['input_issues']])

    def test_report_job_admission_uses_only_available_bounds(self):
        late_report = dict(self.linux, recorded_at='2026-11-20T00:04:01.000000001Z')
        for missing, recorded_at, rejected in (
            ('started_at', late_report['recorded_at'], True),
            ('completed_at', late_report['recorded_at'], False),
            ('completed_at', '2026-11-20T00:00:09.999999999Z', True),
        ):
            with self.subTest(missing=missing, recorded_at=recorded_at):
                jobs = deepcopy(self.jobs)
                del jobs[0][missing]
                view = self.invoke(
                    '--report', self.write_variant('partial-report', dict(late_report, recorded_at=recorded_at)),
                    '--actions', self.write_variant('partial-run', self.run), self.write_variant('partial-jobs', jobs),
                )
                sample, = view['samples']
                self.assertEqual(rejected, sample['job'] is None)
                if not rejected:
                    self.assertIsNone(sample['seconds']['total'])
                    self.assertEqual(20, sample['seconds']['setup'])
                self.assertEqual(rejected, any('outside the publishing job boundaries' in issue['error']
                                              for issue in view['input_issues']))

    def test_raw_linux_source_contradiction_cannot_withdraw_archived_receipts(self):
        archive = self.directory / 'genuine.json'
        original = self.collect(archive, [self.linux, self.windows])
        receipts = original['recent_collection']['collected_samples']
        self.assertEqual({'Linux', 'Windows'}, {row['platform'] for row in receipts})

        checkout = 'b' * 40
        legacy = dict(fixtures.historical_report(), ci=dict(self.linux['ci'], checkout_sha=checkout),
                      source={'git_head': checkout})
        contradicted = dict(legacy, source_end={'git_head': self.linux['source_commit']})
        for unchanged in (False, True):
            with self.subTest(source_unchanged=unchanged):
                raw = self.write_variant('contradicted', dict(contradicted, source_unchanged=unchanged))
                view = self.invoke('--report', archive, '--report', raw)
                self.assertCountEqual(receipts, timing.platform_report_receipts(view['samples']))
                self.assertEqual(receipts, view['recent_collection']['collected_samples'])
                self.assertEqual({'Linux': (240, 230), 'Windows': (360, 340)}, {
                    sample['platform']: (sample['seconds']['total'], sample['seconds']['runner'])
                    for sample in view['samples'] if sample['job'] is not None
                })

        legacy_path = self.write_variant('legacy', legacy)
        view = self.invoke('--report', archive, '--report', legacy_path)
        self.assertEqual([], list(timing.platform_report_receipts(view['samples'])))
        self.assertIn('Conflicting timing checkouts for this attempt.',
                      [issue['error'] for issue in view['input_issues']])

    def test_disproved_linux_checkout_does_not_displace_windows_receipt(self):
        def git(*args):
            return subprocess.run(['git', '-c', 'user.name=Timing Fixture', '-c', 'user.email=timing@example.test',
                                   *args], cwd=self.directory, stdin=subprocess.DEVNULL, capture_output=True,
                                  check=True, text=True).stdout.strip()

        self.enterContext(fixtures.isolated_git_environment())
        git('init', '-q')
        tree = git('mktree')
        base = git('commit-tree', tree, '-m', 'Base')
        head = git('commit-tree', tree, '-p', base, '-m', 'Topic')
        preview = git('commit-tree', tree, '-p', base, '-p', head, '-m', 'Preview')
        alternative = git('commit-tree', tree, '-p', base, '-p', head, '-m', 'Alternative preview')
        for name in ('tasks.json', 'feedback.json', 'delivery.json'):
            path = self.directory / 'project' / name
            path.parent.mkdir(exist_ok=True)
            shutil.copyfile(timing.ROOT / 'project' / name, path)
        self.enterContext(patch.object(timing, 'ROOT', self.directory))
        self.run['head_sha'] = head
        for job in self.jobs:
            job['head_sha'] = head
        self.clock = timing.actions_clock(self.run, self.jobs)
        for sample in (self.windows, self.linux):
            sample.update(source_commit=preview, ci=dict(sample['ci'], head_sha=head, checkout_sha=preview))
        impostor = dict(self.linux, source_commit=base, ci=dict(self.linux['ci'], checkout_sha=base))
        competing = dict(self.linux, source_commit=alternative, ci=dict(self.linux['ci'], checkout_sha=alternative))
        late = dict(competing, recorded_at='2026-11-20T00:04:01.000000001Z')
        jobs = ('--actions', self.write_variant('run', self.run), self.write_variant('jobs', self.jobs))
        clocks = {'Linux': (240, 230), 'Windows': (360, 340)}
        outside = 'The recorded report creation is outside the publishing job boundaries.'
        conflict = 'Conflicting timing checkouts for this attempt.'
        for case, linux, expected_platforms, issue in (
            ('genuine', self.linux, ('Linux', 'Windows'), None),
            ('ancestor', impostor, ('Windows',), timing.PR_CHECKOUT_ANCESTOR),
            ('late-with-digest', dict(late, inputs_sha256='c' * 64), ('Windows',), outside),
            ('late-without-digest', late, ('Windows',), outside),
            ('competing-checkouts', competing, (), conflict),
        ):
            expected = [timing.platform_report_receipt(row, timing.report_input_sha256(row))
                        for row in (linux, self.windows) if row['platform'] in expected_platforms]
            for order, rows in enumerate(([linux, self.windows], [self.windows, linux, linux])):
                with self.subTest(case=case, order=order):
                    archive = self.directory / f'{case}-{order}.json'
                    weekly = self.collect(archive, rows)
                    reports = [item for number, row in enumerate(rows)
                               for item in ('--report', self.write_variant(f'input-{number}', row))]
                    ordinary = self.invoke(*reports, *jobs)
                    cold = self.invoke('--report', archive)
                    for view in (weekly, ordinary, cold):
                        self.assertCountEqual(expected, timing.platform_report_receipts(view['samples']))
                        self.assertEqual({platform: clocks[platform] if platform in expected_platforms else (None, None)
                                          for platform in clocks}, {
                            row['platform']: (row['seconds']['total'], row['seconds']['runner'])
                            for row in view['samples']
                        })
                        self.assertEqual(clocks, {job['platform']: (job['seconds']['total'], job['seconds']['runner'])
                                                  for job in view['hosted'][0]['jobs']})
                        self.assertEqual([issue] if issue else [], [note['error'] for note in view['input_issues']])
                    for view in (weekly, cold):
                        self.assertCountEqual(expected, view['recent_collection']['collected_samples'])

    def test_an_absent_pr_head_cannot_be_its_own_merge_preview(self):
        head = self.linux['ci']['head_sha']
        impostor = dict(self.linux, source_commit=head, ci=dict(self.linux['ci'], checkout_sha=head))
        archive = self.directory / 'absent-head.json'
        with patch.object(timing, 'git_object_types', return_value=(b'missing', b'missing')) as git_objects:
            view = self.collect(archive, [impostor, self.windows])
            self.assertTrue(all(len(set(call.args[1])) == 2 for call in git_objects.call_args_list))
        receipt, = view['recent_collection']['collected_samples']
        self.assertEqual(('Windows', timing.report_input_sha256(self.windows)),
                         (receipt['platform'], receipt['report_input_sha256']))
        self.assertIn(timing.PR_CHECKOUT_ANCESTOR, [row['error'] for row in view['input_issues']])
