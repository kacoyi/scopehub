"""Exercise job evidence through the ordinary timing and archive entries."""

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import timing
from tests.project.test_timing import (
    SPLIT_CATALOG, fast_report, hosted_job, hosted_run, lane_sample, split_harness_report, use_catalog,
)


class PublicJobObservationTests(unittest.TestCase):
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
                        'event': 'pull_request', 'head_sha': 'a' * 40, 'checkout_sha': 'a' * 40}
        report['generated_at'] = '2026-11-20T00:12:34Z'
        sample = timing.report_sample(report)

        scope = dict(aggregate, steps=[dict(aggregate['steps'][0], name='Verify tooling changes')])
        nameless = {key: value for key, value in browser.items() if key not in ('name', 'steps')}
        opposite_runner = dict(nameless, labels=['ubuntu-latest'])
        same_runner = {key: value for key, value in nameless.items() if key not in ('run_attempt', 'conclusion')}
        other_attempt = dict(opposite_runner, run_attempt=2)
        missing_end = [dict(job, completed_at=None) if job['id'] == browser['id'] else job for job in jobs]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
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
                        job.pop('publishing_steps', None)
                        job.pop('executed_layout_steps', None)
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
