"""Exercise publisher eligibility and evidence identity at the public timing entry."""

from contextlib import redirect_stdout
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import timing
from tests.project import test_timing as fixtures


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

    def weekly(self, archive, sample):
        with (patch.object(timing, 'hosted_repository', return_value='fixture/scopehub'),
              patch.object(timing, 'collection_source', return_value='c' * 40),
              patch.object(timing, 'recent_runs', return_value=[self.run]),
              patch.object(timing, 'download_timing', return_value=[self.linux, sample]),
              patch.object(timing, 'inspect_jobs', return_value=self.clock)):
            return self.invoke('--recent', 1, '--github-actions', '--save', archive)

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

    def test_weekly_reopen_reconciles_equivalent_instants_and_preserves_exact_receipts(self):
        self.windows['recorded_at'] = '2026-11-20T00:02:00.683387+00:00'
        original = {'Windows': timing.report_input_sha256(self.windows)}
        archive = self.directory / 'genuine.json'
        self.assert_view(self.weekly(archive, self.windows), original, [])
        equivalent = dict(self.windows, recorded_at='2026-11-20T05:47:00.683387000+05:45')
        alternative = {'Windows': timing.report_input_sha256(equivalent)}
        self.assertNotEqual(original, alternative)
        self.assert_view(self.weekly(self.directory / 'equivalent.json', equivalent), alternative, [])
        variant = self.write_variant('altered', equivalent)
        for paths in ((archive, variant), (variant, archive)):
            view = self.invoke('--report', paths[0], '--report', paths[1])
            self.assert_view(view, original, [])
            admitted, = (row for row in view['samples'] if row['platform'] == 'Windows')
            self.assertCountEqual([original['Windows'], alternative['Windows']], admitted['report_inputs_sha256'])

        for fraction in ('683388', '683387001'):
            with self.subTest(fraction=fraction):
                different = self.write_variant('different', dict(
                    equivalent, recorded_at=f'2026-11-20T05:47:00.{fraction}+05:45',
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

    def test_raw_native_report_checks_the_integration_summary_before_projection(self):
        report = deepcopy(self.windows_report)
        report['timing'] = {'elapsed_seconds': 240}
        report['generated_at'] = '2026-11-20T00:02:00.683387001+00:00'
        fixtures.native_raw_fixture(self, report, fixtures.OLD_FULL_CATALOG)
        summary = {key: {'status': 'not_evaluated', 'observations': []}
                   for key in ('configured_models', 'cloud_models', 'real_market_data')}
        args = ('--actions', self.write_variant('run', self.run), self.write_variant('jobs', self.jobs))
        for evaluated in (False, True):
            with self.subTest(mock_ai_evaluated=evaluated):
                report['integration_validation'] = dict(summary, mock_ai_evaluated=evaluated)
                path = self.write_variant('raw', report)
                view = self.invoke('--report', path, *args)
                if evaluated:
                    self.assertEqual([], view['samples'])
                    self.assertEqual([{'file': path.name, 'error':
                                       'Timing report has an integration summary that contradicts its check results.'}],
                                     view['input_issues'])
                else:
                    sample, = view['samples']
                    self.assertEqual(report['generated_at'], sample['recorded_at'])
                    self.assertEqual((360, 340), (sample['seconds']['total'], sample['seconds']['runner']))
                    self.assertEqual([], view['input_issues'])
                actions, = view['hosted']
                windows, = (job for job in actions['jobs'] if job['platform'] == 'Windows')
                self.assertEqual(360, windows['seconds']['total'])
