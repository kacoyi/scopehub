"""Exercise windowed timing with Actions job names and completed publishing steps."""

from contextlib import redirect_stdout
from copy import deepcopy
from io import StringIO
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import timing
from tests.project import test_timing as fixtures


class TimingJobRoleTests(unittest.TestCase):
    def setUp(self):
        fixtures.use_catalog(self, fixtures.SPLIT_CATALOG)
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.run = fixtures.hosted_run()
        self.run_path = self.write('run.json', self.run)
        self.jobs = [
            fixtures.hosted_job(31, 'ubuntu', '00:10', '08:30', 'Run pnpm check', '00:50', '08:10'),
            fixtures.hosted_job(35, 'windows', '12:08', '12:45',
                                'Run uv run python scripts/check.py --require-windows', '12:27', '12:35'),
        ]
        self.jobs[1]['steps'].append({'name': timing.WINDOWS_RECOVERY_MARKER, 'conclusion': 'success'})
        for number, lane, finish in ((32, 'core', '09:00'), (33, 'browser', '12:00'),
                                     (34, 'python', '08:00'), (36, 'recovery', '05:00')):
            job = fixtures.hosted_job(number, 'windows', '00:30', finish,
                                      f'Verify the Windows {lane} lane', '01:00', '04:30')
            self.jobs.append(dict(job, name=timing.WINDOWS_PRODUCERS[lane], labels=['windows-latest']))
        report = fixtures.split_harness_report(675)
        report['ci'] = {'repository': 'fixture/scopehub', 'run_id': '11', 'run_attempt': '1',
                        'event': 'pull_request', 'head_sha': 'a' * 40, 'checkout_sha': 'a' * 40}
        self.aggregate = timing.report_sample(report, cache='warm')
        self.aggregate['windows_caches'] = {
            'core': 'warm', 'browser': 'warm', 'python': 'not_requested', 'recovery': 'warm',
        }
        producer_base = {key: value for key, value in self.aggregate.items() if key != 'windows_caches'}
        self.report_paths = [self.write(f'{index}.json', sample) for index, sample in enumerate(
            (self.aggregate, fixtures.lane_sample(producer_base, 'browser'), fixtures.lane_sample(producer_base, 'recovery'))
        )]

    def write(self, name, data):
        path = self.root / name
        path.write_bytes(timing.json_bytes(data))
        return str(path)

    def cli(self, argv):
        with patch.object(sys, 'argv', ['timing.py', *argv]), redirect_stdout(StringIO()) as stdout:
            timing.main()
        return json.loads(stdout.getvalue())

    def read_modes(self, jobs, label):
        args = [arg for path in self.report_paths for arg in ('--report', path)]
        listing = self.write(f'{label}-jobs.json', {'jobs': jobs})
        ordinary = self.cli([*args, '--actions', self.run_path, listing])
        archive, summary = (str(self.root / f'{label}.{extension}') for extension in ('json', 'md'))
        environment = {'GITHUB_ACTIONS': 'true', 'GITHUB_REPOSITORY': 'fixture/scopehub', 'GH_TOKEN': 'fixture'}
        with (patch.dict(os.environ, environment),
              patch.object(timing, 'collection_source', return_value='b' * 40),
              patch.object(timing, 'recent_runs', return_value=[self.run]),
              patch.object(timing, 'download_timing', return_value=[self.aggregate]),
              patch.object(timing, 'command', return_value=json.dumps({'jobs': jobs}))):
            weekly = self.cli([*args[2:], '--recent', '12', '--github-actions', '--save', archive,
                               '--summary', summary])
        return ordinary, weekly, self.cli(['--report', archive])

    def test_swapped_jobs_cannot_qualify_but_independent_report_and_recovery_survive(self):
        swapped = deepcopy(self.jobs)
        aggregate, browser = swapped[1], swapped[3]
        aggregate['name'], browser['name'] = browser['name'], aggregate['name']
        for label, jobs, expected, receipts in (
                ('genuine', self.jobs, (765, 720, 300), 1),
                ('swapped', swapped, (None, None, 300), 0),
                ('reordered', swapped[::-1], (None, None, 300), 0),
                ('limited', self.jobs[:1], (None, None, None), 0)):
            modes = self.read_modes(jobs, label)
            for view in modes:
                with self.subTest(label=label, mode=view['recent_collection'] is not None):
                    rows = {row['profile']: row for row in view['samples']}
                    profiles = ('full', 'ci-windows-browser', 'ci-windows-recovery')
                    self.assertEqual(expected, tuple(rows[profile]['seconds']['total'] for profile in profiles))
                    self.assertEqual(('warm', 675), (rows['full']['cache'], rows['full']['seconds']['harness']))
                    if label in {'swapped', 'reordered'}:
                        self.assertTrue(view['input_issues'])
                        self.assertIsNone(rows['full']['seconds']['runner'])
                    else:
                        self.assertEqual([], view['input_issues'])
                    if view['recent_collection'] is not None:
                        self.assertEqual(receipts, len(view['recent_collection']['collected_samples']))

    def test_wrong_lane_step_disputes_one_producer_but_skipped_step_has_no_vote(self):
        jobs = deepcopy(self.jobs)
        recovery = jobs[-1]
        cross_lane = {'name': 'Verify the Windows browser lane', 'conclusion': 'success'}
        for step, expected in ((cross_lane, (765, 720, None, None)),
                               (dict(cross_lane, conclusion='skipped'), (765, 720, 300, 1957))):
            recovery['steps'] = [recovery['steps'][0], step]
            view, _, _ = self.read_modes(jobs, step['conclusion'])
            rows = {row['profile']: row['seconds'] for row in view['samples']}
            self.assertEqual(expected, (rows['full']['total'], rows['ci-windows-browser']['total'],
                                        rows['ci-windows-recovery']['total'], rows['full']['runner']))
