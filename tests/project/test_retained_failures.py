"""Exercise the handoff from a full failure through narrower and independent checks."""

from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import patch

from scripts import recovery, retained_failures
from scripts.project import digest, json_bytes, read_json, workfront
from tests.project.test_continuity import ProjectFixture

SCRIPTS = Path(__file__).resolve().parents[2] / 'scripts'


class RetainedFailureTests(ProjectFixture):
    def setUp(self):
        super().setUp()
        self.base = read_json(self.report())
        self.base['source_end'] = self.base['source']
        self.reference = self.base['source']['sha256'], self.base['project_definition_sha256']

    def observed(self, minute, states, **changes):
        run = f'20261123T00{minute:02d}00.000000Z-01234567'
        folder = self.root / '.reports/runs' / run
        folder.mkdir(exist_ok=True)
        report = deepcopy(self.base)
        report.update(run_id=run, generated_at=f'2026-11-23T00:{minute:02d}:30+00:00',
                      status='failed' if 'failed' in states.values() else 'passed',
                      selection={'profile': 'task', 'task_id': 'M0-001', 'checks': list(states)})
        for row in report['checks']:
            key = row['id']
            row['status'] = states.get(key, 'blocked')
            if key not in states:
                row['reason_code'] = 'not_selected'
            if row['status'] == 'blocked':
                for attr in ('log', 'log_sha256', 'exit_code'):
                    row.pop(attr)
                continue
            raw = f'{key} {minute}\n'.encode()
            log = folder / f'{key}.log'
            log.write_bytes(raw)
            row.update(exit_code=0 if row['status'] == 'passed' else 1,
                       log=log.relative_to(self.root).as_posix(), log_sha256=digest(raw))
        report.update(changes)
        path = folder / 'test-report.json'
        path.write_bytes(json_bytes(report))
        (self.root / '.reports/test-report.json').write_bytes(json_bytes(report))
        return path

    def handoff(self):
        return retained_failures.retained_failure_view(self.root, self.reference, basis='current_checkout')

    def test_status_and_standalone_recovery_locate_the_original_after_a_narrow_green(self):
        shutil.copytree(SCRIPTS, self.root / 'scripts', ignore=shutil.ignore_patterns('__pycache__'))
        self.base = read_json(self.report())
        self.base['source_end'] = self.base['source']
        failed = self.observed(1, {'project-state': 'passed', 'rust-lints': 'failed'})
        self.observed(3, {'project-state': 'passed'})
        status = subprocess.run([sys.executable, 'scripts/project.py', 'status'], cwd=self.root,
                                capture_output=True, text=True, check=True)
        state = json.loads(status.stdout)
        self.assertTrue(state['verification']['current'])
        self.assertEqual('passed', state['verification']['recorded_status'])
        expected = state['retained_failures']['matching_inputs']
        self.assertEqual(1, expected['count'])
        self.assertEqual((failed.relative_to(self.root).as_posix(), 'rust-lints'),
                         (expected['items'][0]['report'], expected['items'][0]['check_id']))
        recovered = subprocess.run([sys.executable, '-I', '-S', 'scripts/recovery.py'], cwd=self.root,
                                   capture_output=True, text=True, check=True)
        diagnostic = json.loads(recovered.stdout)
        self.assertEqual('not_evaluated', diagnostic['acceptance'])
        self.assertEqual(expected, diagnostic['retained_failures']['matching_inputs'])
        self.assertIn({'kind': 'unrechecked_retained_failures', 'count': 1}, diagnostic['signals'])
        self.observed(5, {'rust-lints': 'passed'})
        self.assertEqual(0, workfront(self.root, retained_detail=False)['retained_failures']['matching_inputs']['count'])
        self.assertNotIn('unrechecked_retained_failures', {entry['kind'] for entry in recovery.diagnose(self.root)['signals']})
        bootstrap = subprocess.run([sys.executable, '-I', '-S', 'scripts/recovery.py'], cwd=self.root,
                                   capture_output=True, text=True, check=True)
        unavailable = json.loads(bootstrap.stdout)
        self.assertEqual(1, unavailable['retained_failures']['matching_inputs']['count'])
        self.assertTrue(unavailable['retained_failures']['scan']['schema_unavailable'])
        self.assertIn('incomplete_retained_run_coverage', {item['kind'] for item in unavailable['signals']})

    def test_a_schema_invalid_green_cannot_hide_a_failure_from_status_or_recovery(self):
        self.observed(1, {'rust-lints': 'failed'})
        path = self.observed(2, {'rust-lints': 'passed'})
        original = read_json(path)
        missing_profile = deepcopy(original)
        del missing_profile['selection']['profile']
        bad_feedback = dict(original, observed_feedback=['not-a-hash'])
        for report in (missing_profile, bad_feedback, dict(original, timing={'elapsed_seconds': -1})):
            path.write_bytes(json_bytes(report))
            for result in (workfront(self.root, retained_detail=False), recovery.diagnose(self.root)):
                view = result['retained_failures']
                self.assertEqual(1, view['matching_inputs']['count'])
                self.assertEqual(1, view['scan']['invalid_recheck_reports'])
                self.assertEqual('incomplete', view['scan']['coverage'])

    def test_missing_original_and_unobserved_history_are_visible_at_both_entries(self):
        path = self.observed(1, {'rust-lints': 'failed'})
        shutil.rmtree(path.parent)
        for result in (workfront(self.root, retained_detail=False), recovery.diagnose(self.root)):
            scan = result['retained_failures']['scan']
            self.assertEqual(('incomplete', [path.parent.name]), (scan['coverage'], scan['unavailable_run_ids']))
        run = self.observed(2, {'rust-lints': 'passed'})
        first = self.observed(0, {'rust-lints': 'failed'})
        (self.root / '.reports/test-report.json').write_bytes(run.read_bytes())
        with patch('scripts.retained_failures.RUN_LIMIT', 1):
            for result in (workfront(self.root, retained_detail=False), recovery.diagnose(self.root)):
                self.assertEqual('incomplete', result['retained_failures']['scan']['coverage'])
                self.assertEqual(1, result['retained_failures']['scan']['older_runs_omitted'])
            self.assertIn('incomplete_retained_run_coverage', {item['kind'] for item in recovery.diagnose(self.root)['signals']})
        self.assertTrue(first.exists())

    def test_other_platform_inputs_command_and_overlapping_run_supply_no_recheck(self):
        self.observed(1, {'rust-lints': 'failed'}, generated_at='2026-11-23T00:03:30Z')
        self.observed(2, {'rust-lints': 'passed'})
        self.observed(4, {'rust-lints': 'passed'}, environment=dict(self.base['environment'], system='Windows'))
        self.observed(5, {'rust-lints': 'passed'}, environment=dict(self.base['environment'], machine='arm64'))
        foreign = dict(self.base['source'], sha256='a' * 64)
        self.observed(6, {'rust-lints': 'passed'}, source=foreign, source_end=foreign)
        self.observed(7, {'rust-lints': 'passed'}, project_definition_sha256='b' * 64)
        self.observed(8, {'rust-lints': 'passed'}, source_unchanged=False)
        self.observed(9, {'rust-lints': 'passed'}, source_end=foreign)
        path = self.observed(10, {'rust-lints': 'passed'})
        report = read_json(path)
        report['checks'][-1]['command'] = ['different-check']
        path.write_bytes(json_bytes(report))
        self.assertEqual(1, self.handoff()['matching_inputs']['count'])
        self.observed(11, {'rust-lints': 'passed'})
        self.assertEqual(0, self.handoff()['matching_inputs']['count'])

    def test_incomplete_or_unbound_log_never_clears_an_observation(self):
        original = self.observed(1, {'rust-lints': 'failed'})
        narrow = self.observed(2, {'project-state': 'passed', 'rust-lints': 'blocked'}, status='blocked')
        later = self.observed(3, {'rust-lints': 'passed'})
        (later.parent / 'rust-lints.log').write_text('changed', encoding='utf-8')
        bad = self.observed(4, {'rust-lints': 'passed'})
        bad.write_bytes(b'{"unfinished":')
        shifted = self.observed(5, {'rust-lints': 'passed'})
        shifted.write_bytes(later.read_bytes())
        view = self.handoff()
        self.assertEqual((1, 2, 1), (view['matching_inputs']['count'], view['scan']['unavailable'],
                                   view['scan']['unverified_rechecks']))
        self.assertEqual(original.parent.name, view['matching_inputs']['items'][0]['run_id'])
        self.assertTrue(narrow.exists())

    def test_unavailable_field_evidence_needs_its_dedicated_recheck(self):
        first = self.observed(1, {'rust-lints': 'failed'})
        evidence_failure = read_json(first)
        failed = next(row for row in evidence_failure['checks'] if row['id'] == 'rust-lints')
        failed.update(exit_code=0, reason_code='missing_acceptance_evidence')
        first.write_bytes(json_bytes(evidence_failure))
        self.observed(2, {'rust-lints': 'failed'})
        self.observed(3, {'rust-lints': 'passed'})
        view = self.handoff()
        signal, = view['matching_inputs']['items']
        self.assertEqual(first.parent.name, signal['first_failed_run'])
        self.assertTrue(signal['requires_observation_review'])
        self.assertEqual(1, view['scan']['unverified_rechecks'])

    def test_stale_report_evidence_and_scan_limits_are_explicit(self):
        self.observed(1, {'rust-lints': 'failed'})
        path = self.observed(2, {'rust-lints': 'passed'})
        doc = read_json(path)
        doc['task_definitions'] = {'M0-001': 'not-a-digest'}
        path.write_bytes(json_bytes(doc))
        self.assertEqual(1, self.handoff()['matching_inputs']['count'])
        with patch.object(retained_failures, 'SCAN_BYTES', 1):
            limited = self.handoff()
        self.assertEqual('incomplete', limited['scan']['coverage'])
        self.assertTrue(limited['scan']['bytes_limited'])
        self.assertEqual(2, limited['scan']['older_runs_omitted'])

    def test_distinguishes_bounded_history_and_declines_linked_originals(self):
        first = self.observed(1, {'rust-lints': 'failed'}, project_definition_sha256='c' * 64)
        self.observed(2, {'rust-lints': 'failed'}, source_unchanged=False)
        self.observed(3, {'project-state': 'passed'})
        view = self.handoff()
        self.assertEqual(2, view['other_or_unverified_inputs']['count'])
        self.assertEqual(0, view['matching_inputs']['count'])
        with patch.object(retained_failures, 'RUN_LIMIT', 2):
            self.assertEqual(1, self.handoff()['scan']['older_runs_omitted'])
        linked = self.observed(4, {'rust-lints': 'passed'})
        linked.unlink()
        try:
            linked.symlink_to(first)
        except OSError:
            self.skipTest('Creating symlinks requires host support')
        self.assertEqual(1, self.handoff()['scan']['unavailable'])
