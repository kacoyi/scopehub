"""Exercise timing distinctions and regression decisions with synthetic observations."""

from contextlib import redirect_stderr, redirect_stdout
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
        report = fast_report(20, 675)
        report['selection']['profile'] = 'full'
        report['environment']['system'] = 'Windows'
        report['ci'] = {'repository': 'fixture/scopehub', 'run_id': '11', 'run_attempt': '1',
                        'event': 'pull_request', 'head_sha': 'a' * 40, 'checkout_sha': 'a' * 40}
        for cache in ('warm', 'unknown'):
            with self.subTest(cache=cache):
                review, = timing.timing_view([timing.report_sample(report, cache=cache)], hosted=[result])['reviews']
                self.assertEqual(cache, review['cache'])
                self.assertEqual({'total': 765, 'queue': 8, 'aggregate_setup': 19, 'harness_step': 695},
                                 {row['metric']: row['recent_median_seconds'] for row in review['metrics']
                                  if row['metric'] in {'total', 'queue', 'aggregate_setup', 'harness_step'}})

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
        return subprocess.run(
            ['git', '-c', 'user.name=Timing Fixture', '-c', 'user.email=timing@example.test',
             *args], cwd=self.root, input=contents, capture_output=True, text=True, check=True,
        ).stdout.strip()

    def commit(self, message, *parents, tree=None):
        return self.git('commit-tree', tree or self.tree,
                        *(arg for parent in parents for arg in ('-p', parent)), '-m', message)

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
            workflows.append(timing.hosted_timing(run, [linux, windows]))

            report = historical_report()
            report.update(run_id=f'202611{day:02}T000000.000000Z-aabbccdd',
                          generated_at=f'2026-11-{day:02}T00:14:30Z', status='passed',
                          environment={'system': 'Windows', 'machine': 'AMD64'}, ci={
                              'repository': 'fixture/scopehub', 'run_id': str(day), 'run_attempt': '1',
                              'event': 'push', 'head_sha': 'a' * 40, 'checkout_sha': 'a' * 40,
                          })
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
        samples[-1] = dict(samples[-1], checks=samples[-2]['checks'], selected=samples[-2]['selected'])
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
        self.assertEqual('addressed', addressed['repeated_breaches'][0]['status'])
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
        def invoke(*args):
            output = StringIO()
            with (patch.object(sys, 'argv', ['timing.py', *args]), redirect_stdout(output),
                  patch.object(timing, 'github_identity', side_effect=AssertionError('Unexpected hosted inspection'))):
                timing.main()
            return json.loads(output.getvalue())

        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / 'test-report.json'
            archive = Path(directory) / 'timing.json'
            report.write_text(json.dumps(fast_report(19, 120)), encoding='utf-8')
            original = invoke('--report', str(report), '--save', str(archive))
            recovered = invoke('--report', str(archive))

        self.assertEqual(original['samples'], recovered['samples'])
        self.assertEqual(original['reviews'], recovered['reviews'])
        self.assertEqual(7, recovered['weekly_review']['interval_days'])
        self.assertEqual([], recovered['input_issues'])


class RecentTimingTests(unittest.TestCase):
    def budget_run(self, day, finish, *, head='a' * 40, cache='warm'):
        repository = 'fixture/scopehub'
        run = dict(hosted_run(run_id=day, day=day), head_sha=head)
        jobs = [
            hosted_job(day * 10, 'ubuntu', '00:10', '04:00', 'Run pnpm check', '00:30', '03:50', run_id=day, day=day),
            hosted_job(day * 10 + 1, 'windows', '00:20', finish,
                       'Run uv run python scripts/check.py --require-windows', '00:50', '08:30',
                       run_id=day, day=day),
        ]
        workflow = timing.hosted_timing(run, [dict(job, head_sha=head) for job in jobs])
        samples = []
        for platform in ('Linux', 'Windows'):
            report = fast_report(day, 180)
            report['selection']['profile'] = 'full'
            report['environment']['system'] = platform
            report['source']['git_head'] = head
            report['ci'] = {'repository': repository, 'run_id': str(day), 'run_attempt': '1', 'event': 'pull_request',
                            'head_sha': head, 'checkout_sha': head}
            samples.append(timing.report_sample(report, cache=cache))
        return run, samples, workflow

    def write_archive(self, path, samples, clocks):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(timing.json_bytes({'kind': timing.ARCHIVE_KIND, 'samples': samples, 'hosted': clocks}))

    def test_malformed_timing_preserves_all_ci_verdicts_and_other_observations(self):
        observations = [self.budget_run(day, '17:00') for day in (14, 15)]
        samples = [sample for _, rows, _ in observations for sample in rows]
        clocks = [clock for _, _, clock in observations]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records = root / 'project'
            records.mkdir()
            for file, content in (('tasks', {'tasks': []}), ('feedback', {'items': []}),
                                  ('branch-protection', {'branch': 'main'})):
                (records / f'{file}.json').write_bytes(timing.json_bytes(content))
            archives = root / '.reports/timing'
            malformed = dict(samples[0], seconds={'harness': [], 'check_work': None})
            self.write_archive(archives / 'mixed.json', [{}, malformed, *samples], [False, *clocks])
            (archives / 'deep.json').write_bytes(b'[' * 12000 + b'0' + b']' * 12000)
            observation = observations[-1][0]
            source = {'git_head': observation['head_sha'], 'working_tree_dirty': False}
            with (patch.object(ci, 'github_identity', return_value='fixture/scopehub'),
                  patch.object(ci, 'source_identity', return_value=source),
                  patch.object(ci, 'checkout_branch', return_value='topic'),
                  patch.object(ci, 'independent_status', return_value={'status': 'missing', 'run': None}),
                  patch.object(timing, 'command', side_effect=AssertionError('Unexpected timing request'))):
                for status, row in (('passed', observation), ('failed', dict(observation, conclusion='failure')),
                                    ('blocked', dict(observation, status='queued'))):
                    with self.subTest(status=status), patch.object(ci, 'workflow_runs', return_value=[row]):
                        result = ci.status(root)
                    self.assertEqual(status, result['status'])
                    budget = result['verification_budget']
                    breach, = budget['repeated_breaches']
                    self.assertEqual(['14', '15'], breach['recent_runs'])
                    self.assertEqual('assignment_needed', breach['status'])
                    self.assertGreaterEqual(len(budget['input_issues']), 4)

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
                    path.write_text('{', encoding='utf-8')
                else:
                    with patch.object(timing, 'download_timing', side_effect=KeyboardInterrupt):
                        with self.assertRaises(KeyboardInterrupt):
                            observe()
                    self.assertEqual(1, timing.read_json(path)['intake_attempts'])

                def download(*_args):
                    self.assertEqual('in_progress', observe())
                    return samples

                with (patch.object(timing, 'download_timing', side_effect=download) as artifact,
                      patch.object(timing, 'inspect_jobs', return_value=clock) as jobs):
                    self.assertEqual('captured', observe())
                    self.assertEqual('retained', observe())
                artifact.assert_called_once()
                jobs.assert_called_once()

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
