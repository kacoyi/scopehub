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
        original = self.directory / 'genuine-ancestry.json'
        receipts = lambda view: {receipt['platform'] for receipt in view['recent_collection']['collected_samples']}
        self.assertEqual({'Linux', 'Windows'}, receipts(self.collect(original, [self.linux, self.windows])))
        impostor = dict(self.linux, source_commit=base, ci=dict(self.linux['ci'], checkout_sha=base))
        for number, rows in enumerate(([impostor, self.windows], [self.windows, impostor])):
            archive = self.directory / f'disproved-{number}.json'
            weekly = self.collect(archive, rows)
            ordinary = self.invoke('--report', self.write_variant('first', rows[0]),
                                   '--report', self.write_variant('second', rows[1]),
                                   '--actions', self.write_variant('run', self.run), self.write_variant('jobs', self.jobs))
            cold = self.invoke('--report', archive)
            self.assertEqual({'Windows'}, receipts(weekly))
            self.assertEqual({'Windows'}, receipts(cold))
            for view in (weekly, ordinary, cold):
                self.assertEqual({'Linux': None, 'Windows': 360},
                                 {row['platform']: row['seconds']['total'] for row in view['samples']})
                self.assertEqual({'Linux': 240, 'Windows': 360},
                                 {job['platform']: job['seconds']['total'] for job in view['hosted'][0]['jobs']})

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
