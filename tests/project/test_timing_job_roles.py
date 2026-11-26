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

    def read_modes(self, jobs, label, *, additional=(), retained=()):
        args = [arg for path in (*self.report_paths, *retained) for arg in ('--report', path)]
        listing = self.write(f'{label}-jobs.json', {'jobs': jobs})
        imports = [arg for index, rows in enumerate(additional)
                   for arg in ('--actions', self.run_path, self.write(f'{label}-{index}.json', {'jobs': rows}))]
        ordinary = self.cli([*args, '--actions', self.run_path, listing, *imports])
        archive, summary = (str(self.root / f'{label}.{extension}') for extension in ('json', 'md'))
        environment = {'GITHUB_ACTIONS': 'true', 'GITHUB_REPOSITORY': 'fixture/scopehub', 'GH_TOKEN': 'fixture'}
        with (patch.dict(os.environ, environment),
              patch.object(timing, 'collection_source', return_value='b' * 40),
              patch.object(timing, 'recent_runs', return_value=[self.run]),
              patch.object(timing, 'download_timing', return_value=[self.aggregate]),
              patch.object(timing, 'command', return_value=json.dumps({'jobs': jobs}))):
            weekly = self.cli([*args[2:], *imports, '--recent', '12', '--github-actions', '--save', archive,
                               '--summary', summary])
        return ordinary, weekly, self.cli(['--report', archive])

    def assert_public_clocks(self, views, *, wall, runner, receipts, collected=None):
        ordinary, weekly, reopened = views
        for view in (ordinary, weekly, reopened):
            rows = {row['profile']: row for row in view['samples']}
            aggregate = rows['full']
            self.assertEqual((wall, runner), (aggregate['seconds']['total'], aggregate['seconds']['runner']))
            self.assertEqual(('warm', 675), (aggregate['cache'], aggregate['seconds']['harness']))
            self.assertEqual(720, rows['ci-windows-browser']['seconds']['total'])
            self.assertEqual(300, rows['ci-windows-recovery']['seconds']['total'])
            self.assertEqual(receipts, len(list(timing.platform_report_receipts(view['samples']))))
        self.assertEqual(receipts if collected is None else collected,
                         len(weekly['recent_collection']['collected_samples']))

    def test_explicit_legacy_scope_joins_current_publisher_claims(self):
        aggregate = dict(self.jobs[1], steps=[{'name': 'Verify tooling changes', 'conclusion': 'success'}])
        for profile, expected in (('tooling', (None, None, 0)), ('full', (765, 1957, 1)),
                                  (None, (765, 1957, 1))):
            with self.subTest(legacy_profile=profile):
                # V1 captures saved the untimed scope on the attempt, before
                # per-job publishing evidence was retained.
                legacy = timing.hosted_timing(self.run, [aggregate, self.jobs[-1]])
                legacy.pop('job_facts')
                legacy['workflow']['windows_profile'] = profile
                for job in (*legacy['jobs'], *legacy['sibling_jobs']):
                    job.pop('publishing_steps', None)
                archive = self.write(f'legacy-{profile}.json', {
                    'kind': timing.ARCHIVE_KIND, 'samples': [], 'hosted': [legacy],
                })
                wall, runner, receipts = expected
                self.assert_public_clocks(self.read_modes(self.jobs, f'scope-{profile}', retained=(archive,)),
                                          wall=wall, runner=runner, receipts=receipts)

    def test_nameless_aggregate_requires_executed_role_evidence(self):
        source = self.jobs[1]
        for conclusion, expected in ((None, (None, None, 0)), ('skipped', (None, None, 0)),
                                     ('failure', (None, None, 0)),
                                     ('success', (765, 1957, 1))):
            with self.subTest(marker=conclusion):
                steps = [] if conclusion is None else [{'name': timing.WINDOWS_RECOVERY_MARKER,
                                                        'conclusion': conclusion}]
                aggregate = dict(source, name=None, labels=['windows-latest'], steps=steps)
                jobs = [self.jobs[0], aggregate, *self.jobs[2:]]
                wall, runner, receipts = expected
                self.assert_public_clocks(self.read_modes(jobs, f'nameless-{conclusion}'),
                                          wall=wall, runner=runner, receipts=receipts)

        # A pre-correction capture only retained the marker's name. Reopening
        # it cannot establish whether the marker executed.
        legacy = timing.hosted_timing(self.run, jobs)
        for row in legacy['sibling_jobs']:
            row.pop('executed_layout_steps', None)
        archive = self.write('legacy-nameless.json', {
            'kind': timing.ARCHIVE_KIND, 'samples': [], 'hosted': [legacy],
        })
        without_aggregate = [self.jobs[0], *self.jobs[2:]]
        self.assert_public_clocks(self.read_modes(without_aggregate, 'legacy-marker', retained=(archive,)),
                                  wall=None, runner=None, receipts=0)

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

    def test_competing_step_conclusions_cannot_establish_a_job_role(self):
        marker = {'name': timing.WINDOWS_RECOVERY_MARKER, 'number': 14, 'conclusion': 'success'}
        aggregate = dict(self.jobs[1], name=None, labels=['windows-latest'], steps=[marker])
        skipped = dict(marker, conclusion='skipped')
        failed = dict(marker, conclusion='failure')
        for label, steps, other_rows, expected in (
                ('executed', [marker], (), (765, 1957, 1)),
                ('duplicate', [marker, marker], (), (765, 1957, 1)),
                ('skipped', [skipped], (), (None, None, 0)),
                ('mixed-skipped', [marker, skipped], (), (None, None, 0)),
                ('mixed-failed', [failed, marker], (), (None, None, 0)),
                ('partitioned', [marker], ([dict(aggregate, steps=[skipped])],), (None, None, 0)),
                ('reversed', [skipped], ([aggregate],), (None, None, 0))):
            with self.subTest(case=label):
                jobs = [self.jobs[0], dict(aggregate, steps=steps), *self.jobs[2:]]
                wall, runner, receipts = expected
                modes = self.read_modes(jobs, label, additional=other_rows)
                self.assert_public_clocks(modes, wall=wall, runner=runner, receipts=receipts)
                if label in {'mixed-skipped', 'mixed-failed', 'partitioned', 'reversed'}:
                    for view in modes:
                        self.assertTrue(any(issue.get('job_id') == str(aggregate['id']) and
                                            marker['name'] in issue.get('step_conclusions', {})
                                            for issue in view['input_issues']))

        legacy = timing.hosted_timing(self.run, [self.jobs[0], aggregate, *self.jobs[2:]])
        for row in (*legacy['jobs'], *legacy['sibling_jobs']):
            row.pop('step_conclusions', None)
        archive = self.write('legacy-executed-marker.json', {
            'kind': timing.ARCHIVE_KIND, 'samples': [], 'hosted': [legacy],
        })
        for label, steps, expected in (('missing', [], (765, 1957, 1)), ('failure', [failed], (None, None, 0))):
            with self.subTest(legacy_marker=label):
                jobs = [self.jobs[0], dict(aggregate, steps=steps), *self.jobs[2:]]
                wall, runner, receipts = expected
                self.assert_public_clocks(self.read_modes(jobs, f'legacy-executed-{label}', retained=(archive,)),
                                          wall=wall, runner=runner, receipts=receipts, collected=0)

        identified = dict(aggregate, name=self.jobs[1]['name'], steps=[marker, skipped])
        jobs = [self.jobs[0], identified, *self.jobs[2:]]
        self.assert_public_clocks(self.read_modes(jobs, 'independent-aggregate-role'),
                                  wall=765, runner=1957, receipts=1)

    def test_independently_identified_job_keeps_wall_clock_with_disputed_timed_step(self):
        jobs = deepcopy(self.jobs)
        browser = jobs[3]
        setup = dict(browser['steps'][0], name='Install and start the pinned chart-test browser', number=8)
        browser['steps'].append(setup)
        for conflict in (False, True):
            if conflict:
                browser['steps'].append(dict(setup, conclusion='skipped'))
            modes = self.read_modes(jobs, f'browser-step-{conflict}')
            self.assert_public_clocks(modes, wall=765, runner=1957, receipts=1)
            for view in modes:
                rows = {row['profile']: row['seconds'] for row in view['samples']}
                self.assertEqual(None if conflict else 210, rows['ci-windows-browser']['browser_step'])

    def test_partial_role_steps_prevent_clock_swaps(self):
        jobs = deepcopy(self.jobs)
        aggregate, browser = jobs[1], jobs[3]
        aggregate['labels'] = browser['labels']
        browser['steps'] = [{'name': 'Select the Windows lane work', 'conclusion': 'success'}]
        aggregate['steps'].pop()
        for swapped in (False, True):
            if swapped:
                aggregate['name'], browser['name'] = browser['name'], aggregate['name']
            for view in self.read_modes(jobs, str(swapped)):
                rows = {row['profile']: row['seconds']['total'] for row in view['samples']}
                self.assertEqual({'full': None if swapped else 765,
                                  'ci-windows-browser': None if swapped else 720,
                                  'ci-windows-recovery': 300}, rows)
                self.assertEqual(not swapped, bool(list(timing.platform_report_receipts(view['samples']))))

    def test_job_claims_join_before_membership_and_measurement(self):
        browser = self.jobs[3]
        missing = dict(browser, conclusion=None, status=None, name=None, steps=[], labels=None)
        extra = fixtures.hosted_job(41, 'ubuntu', '01:00', '02:00', 'Inspect routing', '01:10', '01:30')
        extra.update(name='Check reviewed task dispatch', conclusion='skipped')
        for case, (alternate, expected) in enumerate((
                (browser, (765, 720, 300, 1957, True)),
                (extra, (765, 720, 300, 1957, True)),
                (dict(extra, id=browser['id']), (765, None, 300, None, False)),
                (dict(extra, id=browser['id'], checkout_sha='b' * 40), (None, None, None, None, False)),
                (dict(browser, id=42), (765, None, 300, None, False)),
                (dict(browser, conclusion='failure'), (765, None, 300, None, False)),
                (dict(browser, started_at='2026-11-20T00:00:31Z'), (765, 720, 300, None, True)))):
            for grouping, (current, additional) in enumerate((
                    ([missing, *self.jobs, alternate], ()),
                    ([alternate, *reversed(self.jobs), missing], ()),
                    (self.jobs, ([missing], [alternate])),
                    (self.jobs, ([alternate, missing],)))):
                with self.subTest(expected=expected, listings=1 + len(additional)):
                    for view in self.read_modes(current, f'joined-{case}-{grouping}', additional=additional):
                        rows = {row['profile']: row['seconds'] for row in view['samples']}
                        receipts = list(timing.platform_report_receipts(view['samples']))
                        self.assertEqual(expected, (rows['full']['total'], rows['ci-windows-browser']['total'],
                                                    rows['ci-windows-recovery']['total'], rows['full']['runner'],
                                                    bool(receipts)))
                        if view['recent_collection'] is not None:
                            self.assertEqual(int(expected[-1]), len(view['recent_collection']['collected_samples']))

    def test_complete_role_evidence_can_be_partitioned(self):
        args = [arg for path in self.report_paths for arg in ('--report', path)]
        complete = self.read_modes(self.jobs, 'complete')[0]
        for pivot in range(1, len(self.jobs)):
            first = self.write(f'part-{pivot}-first.json', {'jobs': self.jobs[:pivot]})
            second = self.write(f'part-{pivot}-second.json', {'jobs': self.jobs[pivot:]})
            for inputs in ((first, second), (second, first)):
                observed = self.cli([*args, *(arg for path in inputs for arg in ('--actions', self.run_path, path))])
                self.assertEqual(complete['samples'], observed['samples'])
