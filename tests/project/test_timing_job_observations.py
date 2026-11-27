"""Exercise job evidence through the ordinary timing and archive entries."""

from contextlib import redirect_stdout
from io import StringIO
from itertools import count
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import timing
from tests.project.fixtures import isolated_git_environment
from tests.project.test_timing import (
    FIXTURE_PR_HEAD, SPLIT_CATALOG, fast_report, hosted_job, hosted_run, lane_sample, split_harness_report, use_catalog,
)


class PublicJobObservationTests(unittest.TestCase):
    def timing_checkout(self):
        self.enterContext(isolated_git_environment())
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        subprocess.run(['git', '-C', str(root), 'init', '-q'], check=True, capture_output=True)
        project = root / 'project'
        project.mkdir()
        for filename, data in (('tasks.json', {'tasks': []}), ('feedback.json', {'items': []})):
            (project / filename).write_bytes(timing.json_bytes(data))
        return root

    def ordinary(self, root, *args):
        output = StringIO()
        with (patch.object(timing, 'ROOT', root), patch.object(timing, 'weekly_review', return_value=None),
              patch.object(sys, 'argv', ['timing.py', *map(str, args)]), redirect_stdout(output)):
            timing.main()
        return json.loads(output.getvalue())

    def test_split_execution_uses_measured_steps_with_partial_job_boundaries(self):
        use_catalog(self, SPLIT_CATALOG, lanes=timing.LEGACY_WINDOWS_LANES)
        root = self.timing_checkout()
        run_path, jobs_path, report_path = (root / name for name in ('run.json', 'jobs.json', 'report.json'))
        run_path.write_bytes(timing.json_bytes(hosted_run()))
        report = split_harness_report(675)
        report.update(generated_at='2026-11-20T00:12:34Z', ci={
            'repository': 'fixture/scopehub', 'run_id': '11', 'run_attempt': '1',
            'event': 'pull_request', 'head_sha': FIXTURE_PR_HEAD, 'checkout_sha': 'a' * 40,
        })
        windows_report = timing.report_sample(report)
        report_path.write_bytes(timing.json_bytes(windows_report))
        linux_harness = fast_report(20, 450)
        linux_harness['selection']['profile'] = 'full'
        linux_harness['ci'] = report['ci']
        linux_report = timing.report_sample(linux_harness)
        linux_path = root / 'linux.json'
        linux_path.write_bytes(timing.json_bytes(linux_report))
        receipts = {row['platform']: timing.platform_report_receipt(row, timing.report_input_sha256(row))
                    for row in (linux_report, windows_report)}
        linux = hosted_job(31, 'ubuntu', '00:10', '08:30', 'Run pnpm check', '00:50', '08:10')
        core = hosted_job(32, 'windows', '00:20', '09:00', 'Verify the Windows core lane', '01:10', '08:40')
        browser = hosted_job(33, 'windows', '00:40', '12:00', 'Verify the Windows browser lane', '02:00', '11:25')
        python = hosted_job(34, 'windows', '00:30', '08:00', 'Verify the Windows python lane', '01:00', '07:50')
        parent = hosted_job(35, 'windows', '12:08', '12:45', timing.WINDOWS_FULL_STEP, '12:27', '12:35')
        parent['steps'].append({'name': timing.WINDOWS_AGGREGATE_MARKER, 'conclusion': 'success'})
        for lane, job in (('core', core), ('browser', browser), ('python', python)):
            job.update(name=timing.WINDOWS_PRODUCERS[lane], labels=['windows-latest'])
        parent['labels'] = ['windows-latest']
        original = [core, browser, python, parent]
        no_parent_start = dict(parent, started_at=None)
        no_producer_end = dict(browser, completed_at=None)
        late_finish = dict(browser, completed_at='2026-11-20T00:12:40Z')
        late_step = dict(browser['steps'][0], completed_at='2026-11-20T00:12:40Z')
        late_without_end = dict(no_producer_end, steps=[late_step])
        setup = dict(parent['steps'][0], name='Set up job', number=1,
                     started_at='2026-11-20T00:12:08Z', completed_at='2026-11-20T00:12:12Z')
        witnessed_parent = dict(no_parent_start, steps=[setup, *parent['steps']])
        setup_finish_only = dict(no_parent_start, steps=[dict(setup, started_at=None), *parent['steps']])
        finish_after_setup = dict(browser, completed_at='2026-11-20T00:12:18Z')
        completion = dict(browser['steps'][0], name='Complete job', number=99,
                          started_at='2026-11-20T00:11:50Z', completed_at='2026-11-20T00:12:00Z')
        witnessed_producer = dict(no_producer_end, steps=[*browser['steps'], completion])
        late_completion = dict(completion, started_at='2026-11-20T00:12:09Z',
                               completed_at='2026-11-20T00:12:18Z')
        late_witnessed_producer = dict(no_producer_end, steps=[*browser['steps'], late_completion])
        cases = (
            ('untouched', original, (695, 8, 1687, 765)),
            ('producer start', [core, browser, dict(python, started_at=None), parent], (695, 8, None, 765)),
            ('producer completion', [core, no_producer_end, python, parent], (695, None, None, 765)),
            ('aggregate start', [core, browser, python, no_parent_start], (695, None, None, 765)),
            ('aggregate completion', [core, browser, python, dict(parent, completed_at=None)], (695, 8, None, None)),
            ('two absent boundaries', [core, no_producer_end, python, no_parent_start], (695, None, None, 765)),
            ('reordered duplicates', [no_parent_start, python, browser, core, no_parent_start], (695, None, None, 765)),
            ('missing measured endpoint', [core, browser, dict(python, steps=[]), no_parent_start],
             (None, None, None, 765)),
            ('impossible job order', [core, late_finish, python, no_parent_start], (None, None, None, 765)),
            ('impossible step order', [core, late_without_end, python, no_parent_start], (None, None, None, 765)),
            ('setup after producer', [core, browser, python, witnessed_parent], (695, None, None, 765)),
            ('setup before producer finishes', [core, finish_after_setup, python, witnessed_parent],
             (None, None, None, 765)),
            ('setup completed before producer finishes', [core, finish_after_setup, python, setup_finish_only],
             (None, None, None, 765)),
            ('both setup and completion support order', [core, witnessed_producer, python, witnessed_parent],
             (695, None, None, 765)),
            ('completion after setup', [witnessed_parent, python, late_witnessed_producer, core, witnessed_parent],
             (None, None, None, 765)),
        )
        for index, (case, jobs, expected) in enumerate(cases):
            with self.subTest(case=case):
                jobs_path.write_bytes(timing.json_bytes({'jobs': [linux, *jobs]}))
                saved = root / f'capture-{index}.json'
                ordinary = self.ordinary(root, '--report', report_path, '--report', linux_path,
                                         '--actions', run_path, jobs_path, '--save', saved)
                reopened = self.ordinary(root, '--report', saved)
                for view in (ordinary, reopened):
                    samples = {row['platform']: row['seconds'] for row in view['samples']}
                    self.assertEqual(expected, tuple(samples['Windows'][field] for field in (
                        'harness_step', 'queue', 'runner', 'total',
                    )))
                    self.assertEqual((510, 500), tuple(samples['Linux'][field] for field in ('total', 'runner')))
                    self.assertEqual({'Linux': 510, 'Windows': expected[-1]}, {
                        row['platform']: row['seconds']['total'] for row in view['hosted'][0]['jobs']
                    })
                    platforms = ('Linux', 'Windows') if expected[-1] is not None else ('Linux',)
                    self.assertCountEqual([receipts[platform] for platform in platforms],
                                          timing.platform_report_receipts(view['samples']))

        cleanup = dict(completion, completed_at='2026-11-20T00:12:44Z',
                       started_at='2026-11-20T00:12:43Z')
        witnessed = dict(parent, steps=[setup, *parent['steps'], cleanup])
        verify = parent['steps'][0]
        variants = (
            ('completion before cleanup', {'completed_at': '2026-11-20T00:12:43Z'}, (765, 10, 19, 1687)),
            ('completion at cleanup', {'completed_at': '2026-11-20T00:12:44Z'}, (None, None, 19, None)),
            ('verification before setup', {
                'steps': [{'number': verify['number'], 'started_at': '2026-11-20T00:12:09Z'}],
            }, (765, 10, 19, 1687)),
            ('verification after setup', {
                'steps': [{'number': verify['number'], 'started_at': '2026-11-20T00:12:28Z'}],
            }, (765, 10, None, 1687)),
        )
        original_jobs = [linux, core, browser, python, witnessed]
        other_path = root / 'additional-jobs.json'
        jobs_path.write_bytes(timing.json_bytes({'jobs': original_jobs}))
        for name, assertions, expected in variants:
            other = {key: witnessed[key] for key in ('id', 'run_id', 'run_attempt', 'head_sha', 'run_url')}
            other.update(assertions)
            other_path.write_bytes(timing.json_bytes({'jobs': [other]}))
            for paths in ((jobs_path, other_path), (other_path, jobs_path)):
                with self.subTest(case=name, first=paths[0].name):
                    saved = root / f'{name}-{paths[0].name}'
                    actions = [arg for path in paths for arg in ('--actions', run_path, path)]
                    observed = self.ordinary(root, '--report', report_path, '--report', linux_path,
                                             *actions, '--save', saved)
                    for view in (observed, self.ordinary(root, '--report', saved)):
                        windows = next(row for row in view['samples'] if row['platform'] == 'Windows')['seconds']
                        self.assertEqual(expected, tuple(windows[field] for field in (
                            'total', 'tail', 'aggregate_setup', 'runner',
                        )))

        for name, boundary, expected_total in (
            ('no end after cleanup', {'completed_at': '2026-11-20T00:12:43Z'}, None),
            ('no start before setup', {'started_at': '2026-11-20T00:12:09Z'}, 765),
        ):
            with self.subTest(case=name):
                jobs_path.write_bytes(timing.json_bytes({'jobs': [*original_jobs[:-1], {**witnessed, **boundary}]}))
                saved = root / f'{name}.json'
                observed = self.ordinary(root, '--report', report_path, '--report', linux_path,
                                         '--actions', run_path, jobs_path, '--save', saved)
                for view in (observed, self.ordinary(root, '--report', saved)):
                    totals = {row['platform']: row['seconds']['total'] for row in view['hosted'][0]['jobs']}
                    self.assertEqual({'Linux': 510, 'Windows': expected_total}, totals)

    def test_ordered_steps_bound_verification_and_job_clocks(self):
        root = self.timing_checkout()
        run, jobs, other, report_path = (root / name for name in ('run.json', 'jobs.json', 'other.json', 'report.json'))
        run.write_bytes(timing.json_bytes(hosted_run()))
        verification = hosted_job(35, 'ubuntu', '00:29', '01:19', 'Run pnpm check', '00:53', '01:09')
        report = fast_report(20, 16)
        report['selection']['profile'] = 'full'
        report['ci'] = {'repository': 'fixture/scopehub', 'run_id': '11', 'run_attempt': '1',
                        'event': 'pull_request', 'head_sha': FIXTURE_PR_HEAD, 'checkout_sha': 'a' * 40}
        report_path.write_bytes(timing.json_bytes(timing.report_sample(report)))

        def step(number, name, start, end):
            return {'number': number, 'name': name, 'conclusion': 'success',
                    'started_at': f'2026-11-20T00:{start}Z', 'completed_at': f'2026-11-20T00:{end}Z'}

        verification['steps'] = [
            step(2, 'Run actions/checkout@reviewed', '00:32', '00:43'),
            step(7, 'Run actions/download-artifact@reviewed', '00:52', '00:53'),
            step(9, 'Run pnpm check', '00:53', '01:09'),
            step(11, 'Stage current verification evidence', '01:09', '01:12'),
            step(27, 'Post Run actions/checkout@reviewed', '01:16', '01:18'),
        ]
        job_identity = {key: verification[key] for key in ('id', 'run_id', 'run_attempt', 'run_url', 'head_sha')}
        fields = ('queue', 'setup', 'harness_step', 'tail', 'runner', 'total')
        original = (29, 24, 16, 10, 50, 79)
        captures = count()

        def check(data, paths, expected):
            jobs.write_bytes(timing.json_bytes({'jobs': [data]}))
            saved = root / f'archive-{next(captures)}.json'
            actions = [arg for path in paths for arg in ('--actions', run, path)]
            observed = self.ordinary(root, '--report', report_path, *actions, '--save', saved)
            for view in (observed, self.ordinary(root, '--report', saved)):
                row, = view['samples']
                self.assertEqual(expected, tuple(row['seconds'][field] for field in fields))

        check(verification, [jobs], original)
        cases = (
            ('before download', 'step', 'started_at', '00:52', original, (29, None, None, 10, 50, 79)),
            ('after download', 'step', 'started_at', '00:54', (29, None, None, 10, 50, 79), None),
            ('after staging began', 'step', 'completed_at', '01:10', original, (29, 24, None, None, 50, 79)),
            ('after checkout began', 'job', 'started_at', '00:33', original, (None, None, 16, 10, None, 79)),
            ('at checkout start', 'job', 'started_at', '00:32', (None, None, 16, 10, None, 79), None),
            ('before post checkout ended', 'job', 'completed_at', '01:17', original, (29, 24, 16, None, None, None)),
            ('at post checkout end', 'job', 'completed_at', '01:18', (29, 24, 16, None, None, None), None),
        )
        for name, scope, edge, clock, conflicting, sole in cases:
            changed = {edge: f'2026-11-20T00:{clock}Z'}
            if scope == 'step':
                claim = {'steps': [{'number': 9, **changed}]}
                replaced = {**verification, 'steps': [{**row, **changed} if row['number'] == 9 else row
                                                       for row in verification['steps']]}
            else:
                claim = changed
                replaced = {**verification, **changed}
            other.write_bytes(timing.json_bytes({'jobs': [{**job_identity, **claim}]}))
            for order in ((jobs, other), (other, jobs)):
                with self.subTest(claim=name, first=order[0].name):
                    check(verification, order, conflicting)
            if sole is not None:
                with self.subTest(claim=name, first='sole'):
                    check(replaced, [jobs], sole)

        # Initialization and cleanup still locate the job on the timeline
        # when Actions supplies endpoints without a conclusion.
        for anchor, edge, impossible, feasible, separate in (
            (step(1, 'Set up job', '00:31', '00:32'), 'started_at', '00:32', '00:31',
             (None, None, 16, 10, None, 79)),
            (step(28, 'Complete job', '01:18', '01:18'), 'completed_at', '01:17', '01:18',
             (29, 24, 16, None, None, None)),
        ):
            anchor.pop('conclusion')
            anchor['status'] = 'completed'
            name = anchor['name']
            other_steps = [row for row in verification['steps'] if row['number'] != 27]
            observed = dict(verification, steps=[*other_steps, anchor])
            with self.subTest(step=name, claim='original'):
                check(observed, [jobs], original)
            for value, expected in ((impossible, original), (feasible, separate)):
                other.write_bytes(timing.json_bytes({'jobs': [
                    {**job_identity, edge: f'2026-11-20T00:{value}Z'},
                ]}))
                for order in ((jobs, other), (other, jobs)):
                    with self.subTest(step=name, claim=value, first=order[0].name):
                        check(observed, order, expected)
            with self.subTest(step=name, claim='only impossible'):
                check({**observed, edge: f'2026-11-20T00:{impossible}Z'}, [jobs], separate)

    def test_step_clocks_use_joined_eligibility_before_selecting_a_phase(self):
        run = hosted_run()
        job = hosted_job(31, 'ubuntu', '00:10', '07:00', 'Run pnpm check', '01:05', '06:40')
        active, = job['steps']
        skipped = dict(active, name='Verify the selected task', number=10, conclusion='skipped',
                       completed_at=active['started_at'])
        browser = dict(active, name='Start the pinned chart-test browser on Linux', number=7,
                       started_at='2026-11-20T00:00:35Z', completed_at='2026-11-20T00:00:49Z')
        times = {key: value for key, value in skipped.items() if key != 'conclusion'}
        outcome = {key: value for key, value in skipped.items() if key not in ('started_at', 'completed_at')}
        conflict = {**outcome, 'conclusion': 'success'}
        contradicted_active = {**active, 'conclusion': 'skipped'}
        conflicting_end = {**active, 'completed_at': '2026-11-20T00:06:39Z'}
        active_start = {key: value for key, value in active.items() if key != 'completed_at'}
        numbered_end = {key: active[key] for key in ('number', 'completed_at')}
        conflicting_numbered_end = dict(numbered_end, completed_at=conflicting_end['completed_at'])
        exact_offset_end = dict(numbered_end, completed_at='2026-11-20T05:51:40.000000001+05:45:00.000000001')
        compact_offset_end = dict(numbered_end, completed_at='2026-11-19T18:21:40-0545')
        submicrosecond_end = dict(numbered_end, completed_at='2026-11-20T00:06:40.000000001Z')
        incompatible_name = dict(numbered_end, name='An unrelated step')
        pending = {**active, 'status': 'queued', 'conclusion': None}
        pending_state = {key: value for key, value in pending.items() if key not in ('started_at', 'completed_at')}
        pending_times = {key: value for key, value in pending.items() if key != 'status'}
        report = fast_report(20, 450)
        report['selection']['profile'] = 'full'
        report['ci'] = {'repository': 'fixture/scopehub', 'run_id': '11', 'run_attempt': '1',
                        'event': 'pull_request', 'head_sha': FIXTURE_PR_HEAD, 'checkout_sha': 'a' * 40}

        root = self.timing_checkout()
        run_path, report_path = root / 'run.json', root / 'report.json'
        run_path.write_bytes(timing.json_bytes(run))
        report_path.write_bytes(timing.json_bytes(timing.report_sample(report)))

        for label, partitions, expected in (
                ('full', ([active, skipped],), (55, 335, 20)),
                ('complementary', ([active, outcome], [times]), (55, 335, 20)),
                ('reversed', ([times], [active, outcome]), (55, 335, 20)),
                ('redundant', ([active, skipped, times],), (55, 335, 20)),
                ('unrelated contradiction', ([active, outcome], [times, conflict]), (55, 335, 20)),
                ('fully contradicted', ([active, outcome], [times, conflict, contradicted_active]), (None, None, None)),
                ('one contradicted endpoint', ([active, skipped], [conflicting_end]), (55, None, None)),
                ('numbered completion in one row', ([active_start, numbered_end],), (55, 335, 20)),
                ('numbered completion in separate rows', ([active_start], [numbered_end]), (55, 335, 20)),
                ('numbered completion reversed', ([numbered_end], [active_start]), (55, 335, 20)),
                ('absent completion', ([active_start],), (55, None, None)),
                ('conflicting numbered completion', ([active], [conflicting_numbered_end]), (55, None, None)),
                ('equivalent fractional offset', ([active], [exact_offset_end]), (55, 335, 20)),
                ('equivalent compact negative offset', ([compact_offset_end], [active]), (55, 335, 20)),
                ('distinct sub-microsecond completion', ([active], [submicrosecond_end]), (55, None, None)),
                ('distinct completion in reverse', ([submicrosecond_end], [active]), (55, None, None)),
                ('distinct completion in one listing', ([active, submicrosecond_end],), (55, None, None)),
                ('contradicted number binding', ([active], [incompatible_name]), (None, None, None)),
                ('two executed candidates', ([active, {**times, 'conclusion': 'success'}],), (None, None, None)),
                ('pending stale times', ([pending],), (None, None, None)),
                ('pending fields split', ([pending_state], [pending_times]), (None, None, None)),
                ('pending fields reversed', ([pending_times], [pending_state]), (None, None, None)),
                ('completed after pending', ([pending_state], [active]), (55, 335, 20))):
            with self.subTest(case=label):
                args = []
                for index, steps in enumerate(partitions):
                    path = root / f'jobs-{index}.json'
                    path.write_bytes(timing.json_bytes({'jobs': [dict(job, steps=[*steps, browser])]}))
                    args.extend(('--actions', run_path, path))
                saved = root / f'{label}.json'
                for view in (self.ordinary(root, '--report', report_path, *args, '--save', saved),
                             self.ordinary(root, '--report', saved)):
                    seconds = view['samples'][0]['seconds']
                    self.assertEqual((*expected, 420, 410, 14), tuple(seconds[key] for key in (
                        'setup', 'harness_step', 'tail', 'total', 'runner', 'browser_step',
                    )))
                    if label == 'contradicted number binding':
                        self.assertTrue(any(issue.get('steps') == [active['name']] for issue in view['input_issues']))

    def test_job_field_conflicts_survive_listing_partitions_and_archive_reopen(self):
        use_catalog(self, SPLIT_CATALOG, lanes=timing.LEGACY_WINDOWS_LANES)
        run = hosted_run()
        linux = hosted_job(31, 'ubuntu', '00:10', '08:30', 'Run pnpm check', '00:50', '08:10')
        core = hosted_job(32, 'windows', '00:20', '09:00', 'Verify the Windows core lane', '01:10', '08:40')
        browser = hosted_job(33, 'windows', '00:40', '12:00', 'Verify the Windows browser lane', '02:00', '11:25')
        python = hosted_job(34, 'windows', '00:30', '08:00', 'Verify the Windows python lane', '01:00', '07:50')
        full_step = 'Run uv run python scripts/check.py --require-windows'
        aggregate = hosted_job(35, 'windows', '12:08', '12:45', full_step, '12:27', '12:35')
        aggregate['steps'].append({'name': 'Require successful Windows lane jobs', 'conclusion': 'success'})
        for role, job in (('core', core), ('browser', browser), ('python', python)):
            job.update(name=f'Windows change checks ({role})', labels=['windows-latest'])
        aggregate['labels'] = ['windows-latest']
        linux['labels'] = ['ubuntu-latest']
        jobs = [linux, core, browser, python, aggregate]
        report = split_harness_report(675)
        report['ci'] = {'repository': 'fixture/scopehub', 'run_id': '11', 'run_attempt': '1',
                        'event': 'pull_request', 'head_sha': FIXTURE_PR_HEAD, 'checkout_sha': 'a' * 40}
        report['generated_at'] = '2026-11-20T00:12:34Z'
        sample = timing.report_sample(report)

        scope = dict(aggregate, steps=[dict(aggregate['steps'][0], name='Verify tooling changes')])
        nameless = {key: value for key, value in browser.items() if key not in ('name', 'steps')}
        opposite_runner = dict(nameless, labels=['ubuntu-latest'])
        same_runner = {key: value for key, value in nameless.items() if key not in ('run_attempt', 'conclusion')}
        other_attempt = dict(opposite_runner, run_attempt=2)
        missing_end = [dict(job, completed_at=None) if job['id'] == browser['id'] else job for job in jobs]
        with isolated_git_environment(), tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(['git', '-C', str(root), 'init', '-q'], check=True, capture_output=True)
            project = root / 'project'
            project.mkdir()
            for filename, data in (('tasks.json', {'tasks': []}), ('feedback.json', {'items': []})):
                (project / filename).write_bytes(timing.json_bytes(data))
            run_path = root / 'run.json'
            run_path.write_bytes(timing.json_bytes(run))
            report_args = []
            for name, row in (('aggregate', sample), ('browser', lane_sample(sample, 'browser'))):
                path = root / f'{name}.json'
                path.write_bytes(timing.json_bytes(row))
                report_args.extend(('--report', str(path)))

            def ordinary(*args):
                output = StringIO()
                with (patch.object(timing, 'ROOT', root), patch.object(timing, 'weekly_review', return_value=None),
                      patch.object(sys, 'argv', ['timing.py', *map(str, args)]), redirect_stdout(output)):
                    timing.main()
                return json.loads(output.getvalue())

            def measured(view):
                rows = {row['profile']: row['seconds'] for row in view['samples']}
                return rows['full']['total'], rows['full']['runner'], rows['ci-windows-browser']['total']

            for name, complete, additional, expected in (
                    ('competing profile', jobs, scope, (None, None, 720)),
                    ('competing runner', missing_end, opposite_runner, (765, None, None)),
                    ('compatible partial endpoint', missing_end, same_runner, (765, 1687, 720)),
                    ('unrelated attempt', jobs, other_attempt, (765, 1687, 720))):
                partitions = ([*complete, additional],), (complete, [additional]), ([additional], complete, [additional])
                for index, partition in enumerate(partitions):
                    with self.subTest(name=name, partition=index):
                        actions = []
                        for part, rows in enumerate(partition):
                            path = root / f'jobs-{part}.json'
                            path.write_bytes(timing.json_bytes({'jobs': rows}))
                            actions.extend(('--actions', str(run_path), str(path)))
                        saved = root / f'{name}-{index}.json'
                        self.assertEqual(expected, measured(ordinary(*report_args, *actions, '--save', saved)))
                        self.assertEqual(expected, measured(ordinary('--report', saved)))

            # Historical compact captures can retain job boundaries after they
            # have lost the publisher step that supported the role assignment.
            published = dict(sample, inputs_sha256='c' * 64, working_tree_dirty=False)
            browser_report = dict(lane_sample(published, 'browser'), recorded_at='2026-11-20T00:11:24Z')
            linux_report = fast_report(20, 450)
            linux_report['selection']['profile'] = 'full'
            linux_report['ci'] = report['ci']
            independent = timing.report_sample(linux_report)
            for name, rows, expected in (
                    ('matched roles', jobs, (765, 1687, 720)),
                    ('misleading names', [linux, core, dict(browser, name=aggregate['name'], steps=[]),
                                          python, dict(aggregate, name=browser['name'], steps=[])],
                     (None, None, None))):
                with self.subTest(archive=name):
                    clock = timing.hosted_timing(run, rows)
                    clock.pop('job_facts', None)
                    clock['workflow'].update(windows_split=True, windows_layout_identified=True, windows_profile='full')
                    for job in (*clock['jobs'], *clock.get('sibling_jobs', ())):
                        job.pop('step_observations')
                        job.pop('runner_labels', None)
                    saved = root / f'{name}.json'
                    saved.write_bytes(timing.json_bytes({'kind': timing.ARCHIVE_KIND,
                        'samples': [published, browser_report, independent], 'hosted': [clock]}))
                    view = ordinary('--report', saved)
                    result = {row['platform']: row['seconds'] for row in view['samples'] if row['profile'] == 'full'}
                    producer, = [row['seconds'] for row in view['samples'] if row['profile'] == 'ci-windows-browser']
                    self.assertEqual(expected, (result['Windows']['total'], result['Windows']['runner'], producer['total']))
                    self.assertEqual(675, result['Windows']['harness'])
                    self.assertEqual(510, result['Linux']['total'])
