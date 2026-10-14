"""Regression checks for unsupported completion and stale validation claims."""

import json
from contextlib import redirect_stdout
from io import StringIO
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import check as harness
from scripts.project import (
    ProjectError, archive_evidence, digest, json_bytes, load_project, product_progress,
    project_definition_hash, read_json, source_identity, task_definition_hash, workfront,
)

from tests.project.fixtures import records, task_by_id

REPO = Path(__file__).resolve().parents[2]


class ProjectFixture(unittest.TestCase):
    def setUp(self):
        # Synthetic repositories must not inherit provenance from the outer CI job.
        isolated_env = patch.dict(os.environ, {'GITHUB_ACTIONS': 'false'})
        isolated_env.start()
        self.addCleanup(isolated_env.stop)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        subprocess.run(['git', 'init', '--quiet', str(self.root)], check=True, capture_output=True)
        schema = self.root / 'contracts/schemas/project.schema.json'
        schema.parent.mkdir(parents=True)
        shutil.copyfile(REPO / 'contracts/schemas/project.schema.json', schema)
        (self.root / '.gitignore').write_text('.reports/\n__pycache__/\n', encoding='utf-8')
        for relative, value in records().items():
            destination = self.root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(json_bytes(value))
        (self.root / 'implementation.py').write_text('value = 1\n', encoding='utf-8')

    def ledger(self):
        return read_json(self.root / 'project/tasks.json')

    def save_ledger(self, ledger):
        (self.root / 'project/tasks.json').write_bytes(json_bytes(ledger))

    def report(self, task_id='M0-001', status='passed', system='Linux'):
        project = load_project(self.root)
        checks = []
        for key in project['tasks'][task_id]['checks']:
            path = self.root / '.reports/runs/fixture' / f'{key}.log'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('Synthetic test fixture, not product validation.\n', encoding='utf-8')
            command = [sys.executable if arg == '{python}' else arg for arg in project['checks'][key]['command'] or []]
            checks.append({'id': key, 'status': status, 'command': command, 'exit_code': 0,
                           'log': path.relative_to(self.root).as_posix(), 'log_sha256': digest(path.read_bytes())})
        report = {
            'report_version': '2.0', 'run_id': 'fixture', 'status': status,
            'source': source_identity(self.root), 'source_unchanged': True,
            'task_definitions': {key: task_definition_hash(task) for key, task in project['tasks'].items()},
            'project_definition_sha256': project_definition_hash(project), 'project_unchanged': True,
            'environment': {'system': system, 'machine': 'x86_64', 'python_executable': sys.executable}, 'checks': checks,
        }
        path = self.root / '.reports/test-report.json'
        path.write_bytes(json_bytes(report))
        return path

    def finish_first(self, ref):
        ledger = self.ledger()
        task_by_id(ledger, 'M0-001').update(status='done', evidence=[ref], next_action=None)
        ledger['focus_task_id'] = 'M0-002'
        self.save_ledger(ledger)


class ContinuityTests(ProjectFixture):
    def test_reordering_and_prepending_tasks_does_not_change_fixture_identity(self):
        ledger = self.ledger()
        unrelated = {**task_by_id(ledger, 'M0-002'), 'id': 'M0-999', 'title': 'Unrelated synthetic task'}
        ledger['tasks'] = [unrelated, *reversed(ledger['tasks'])]
        self.save_ledger(ledger)
        report = self.report()
        # Reordering alone does not invalidate identity-bound evidence.
        ledger['tasks'].reverse()
        self.save_ledger(ledger)
        self.finish_first(archive_evidence(self.root, 'M0-001', report))
        state = workfront(self.root)
        self.assertEqual(['M0-001'], state['completed_task_ids'])
        self.assertEqual('planned', task_by_id(self.ledger(), 'M0-999')['status'])
        self.assertTrue(state['verification']['current'])

    def test_baseline_records_and_pending_gates_remain_explicit(self):
        project = load_project(self.root)
        milestones, acceptance = product_progress(project, [])
        self.assertTrue(all(item['status'] == 'not_started' for item in milestones))
        self.assertTrue(all(item['status'] == 'blocked' for item in acceptance))

    def test_done_without_evidence_is_rejected(self):
        ledger = self.ledger()
        task_by_id(ledger, 'M0-001').update(status='done', next_action=None)
        ledger['focus_task_id'] = 'M0-002'
        self.save_ledger(ledger)
        with self.assertRaisesRegex(ProjectError, 'lacks passing evidence'):
            load_project(self.root)

    def test_dependency_cycles_and_missing_references_are_rejected(self):
        ledger = self.ledger()
        task_by_id(ledger, 'M0-001')['depends_on'] = ['M0-003']
        self.save_ledger(ledger)
        with self.assertRaisesRegex(ProjectError, 'cycle'):
            load_project(self.root)
        task_by_id(ledger, 'M0-001')['depends_on'] = ['M9-999']
        self.save_ledger(ledger)
        with self.assertRaisesRegex(ProjectError, 'unknown references'):
            load_project(self.root)

    def test_duplicate_task_identity_is_rejected(self):
        ledger = self.ledger()
        ledger['tasks'].append(task_by_id(ledger, 'M0-001'))
        self.save_ledger(ledger)
        with self.assertRaisesRegex(ProjectError, 'Duplicate task'):
            load_project(self.root)

    def test_unfinished_dependency_cannot_be_hidden_by_done_status(self):
        ledger = self.ledger()
        task_by_id(ledger, 'M0-003').update(status='done', next_action=None)
        self.save_ledger(ledger)
        with self.assertRaisesRegex(ProjectError, 'unfinished dependencies'):
            load_project(self.root)

    def test_blocker_and_next_action_are_required(self):
        ledger = self.ledger()
        task_by_id(ledger, 'M0-001').update(status='blocked', blocker=None)
        self.save_ledger(ledger)
        with self.assertRaisesRegex(ProjectError, 'needs a reason'):
            load_project(self.root)
        task_by_id(ledger, 'M0-001').update(blocker='A fixture dependency is absent.', next_action=None)
        self.save_ledger(ledger)
        with self.assertRaisesRegex(ProjectError, 'needs a next action'):
            load_project(self.root)

    def test_passing_receipt_supports_completion_without_self_invalidating(self):
        report = self.report()
        before = source_identity(self.root)['sha256']
        ref = archive_evidence(self.root, 'M0-001', report)
        self.assertEqual(ref, archive_evidence(self.root, 'M0-001', report))
        self.finish_first(ref)
        state = workfront(self.root)
        self.assertIn('M0-001', state['completed_task_ids'])
        self.assertTrue(state['verification']['matches_current_execution_inputs'])
        self.assertEqual(before, source_identity(self.root)['sha256'])

    def test_nonpassing_results_cannot_be_promoted_even_with_zero_exit(self):
        for status in ['blocked', 'failed']:
            with self.subTest(status=status):
                with self.assertRaisesRegex(ProjectError, 'did not pass|failed verification run'):
                    archive_evidence(self.root, 'M0-001', self.report(status=status))

    def test_changed_code_makes_report_stale_but_keeps_historical_completion(self):
        report = self.report()
        self.finish_first(archive_evidence(self.root, 'M0-001', report))
        (self.root / 'implementation.py').write_text('value = 2\n', encoding='utf-8')
        state = workfront(self.root)
        self.assertFalse(state['verification']['matches_current_execution_inputs'])
        self.assertIsNone(state['milestones'])
        self.assertIn('M0-001', state['completed_task_ids'])
        with self.assertRaisesRegex(ProjectError, 'stale report'):
            archive_evidence(self.root, 'M0-001', report)

    def test_changed_task_definition_requires_new_verification(self):
        report = self.report()
        ledger = self.ledger()
        task_by_id(ledger, 'M0-001')['acceptance'].append('An additional outcome that was not evaluated.')
        self.save_ledger(ledger)
        with self.assertRaisesRegex(ProjectError, 'Task definition changed'):
            archive_evidence(self.root, 'M0-001', report)

    def test_completed_definition_cannot_be_rewritten_under_old_receipt(self):
        self.finish_first(archive_evidence(self.root, 'M0-001', self.report()))
        ledger = self.ledger()
        task_by_id(ledger, 'M0-001')['acceptance'].append('A retroactive completion claim.')
        self.save_ledger(ledger)
        with self.assertRaisesRegex(ProjectError, 'lacks passing evidence'):
            load_project(self.root)

    def test_changes_during_verification_and_missing_logs_block_archival(self):
        report_path = self.report()
        report = read_json(report_path)
        report['source_unchanged'] = False
        report_path.write_bytes(json_bytes(report))
        with self.assertRaisesRegex(ProjectError, 'unchanged version-bound'):
            archive_evidence(self.root, 'M0-001', report_path)
        report_path = self.report()
        (self.root / read_json(report_path)['checks'][0]['log']).unlink()
        with self.assertRaisesRegex(ProjectError, 'Missing verification log'):
            archive_evidence(self.root, 'M0-001', report_path)

    def test_linux_cannot_supply_native_windows_evidence(self):
        ledger = self.ledger()
        task_by_id(ledger, 'M0-003')['depends_on'] = []
        self.save_ledger(ledger)
        with self.assertRaisesRegex(ProjectError, 'Native Windows proof requires Windows'):
            archive_evidence(self.root, 'M0-003', self.report('M0-003'))

    def test_wrong_command_or_changed_log_cannot_supply_evidence(self):
        path = self.report()
        report = read_json(path)
        report['checks'][0]['command'] = ['echo', 'passed']
        path.write_bytes(json_bytes(report))
        with self.assertRaisesRegex(ProjectError, 'Executed command differs'):
            archive_evidence(self.root, 'M0-001', path)
        path = self.report()
        log = self.root / read_json(path)['checks'][0]['log']
        log.write_text('Replaced after execution.\n', encoding='utf-8')
        with self.assertRaisesRegex(ProjectError, 'log changed after the run'):
            archive_evidence(self.root, 'M0-001', path)

    def test_receipt_tampering_is_detected(self):
        ref = archive_evidence(self.root, 'M0-001', self.report())
        self.finish_first(ref)
        path = self.root / ref['path']
        receipt = read_json(path)
        receipt['checks'][0]['status'] = 'failed'
        path.write_bytes(json_bytes(receipt))
        with self.assertRaisesRegex(ProjectError, 'Evidence digest mismatch'):
            load_project(self.root)

    def test_check_catalog_is_part_of_execution_inputs(self):
        before = source_identity(self.root)['sha256']
        path = self.root / 'project/checks.json'
        catalog = read_json(path)
        catalog['checks'][0]['command'].append('--changed')
        path.write_bytes(json_bytes(catalog))
        self.assertNotEqual(before, source_identity(self.root)['sha256'])

    def test_acceptance_bindings_cannot_silently_drop_a_requirement(self):
        path = self.root / 'project/checks.json'
        catalog = read_json(path)
        catalog['acceptance'].pop()
        path.write_bytes(json_bytes(catalog))
        with self.assertRaisesRegex(ProjectError, 'acceptance bindings differ'):
            load_project(self.root)

    def test_supersession_preserves_predecessor_and_ignores_unaccepted_proposals(self):
        predecessor = self.root / 'contracts/decisions/0003-repository-harness.json'
        before = predecessor.read_bytes()
        path = self.root / 'contracts/decisions/0004-project-continuity.json'
        decision = read_json(path)
        decision.update(status='proposed', supersedes=['0003'])
        path.write_bytes(json_bytes(decision))
        self.assertEqual({}, load_project(self.root)['replacements'])
        decision['status'] = 'accepted'
        path.write_bytes(json_bytes(decision))
        self.assertEqual({'0003': '0004'}, load_project(self.root)['replacements'])
        self.assertEqual(before, predecessor.read_bytes())

    def test_cyclic_decision_history_is_rejected(self):
        for filename, target in [('0003-repository-harness.json', '0004'), ('0004-project-continuity.json', '0003')]:
            path = self.root / 'contracts/decisions' / filename
            decision = read_json(path)
            decision['supersedes'] = [target]
            path.write_bytes(json_bytes(decision))
        with self.assertRaisesRegex(ProjectError, 'Decision supersession: cycle'):
            load_project(self.root)

    def test_run_logs_are_separate_and_failed_commands_stay_failed(self):
        first = self.root / '.reports/runs/one'
        second = self.root / '.reports/runs/two'
        first.mkdir(parents=True)
        second.mkdir(parents=True)
        with patch.object(harness, 'ROOT', self.root), redirect_stdout(StringIO()):
            good = harness.execute('fixture', [sys.executable, '-c', "print('first result')"],
                                   dict(os.environ), first)
            bad = harness.execute('fixture', [sys.executable, '-c', "print('second result'); raise SystemExit(3)"],
                                  dict(os.environ), second)
        self.assertEqual('passed', good['status'])
        self.assertEqual('failed', bad['status'])
        self.assertEqual(3, bad['exit_code'])
        self.assertNotEqual(good['log'], bad['log'])
        self.assertIn('first result', (self.root / good['log']).read_text())
        self.assertIn('second result', (self.root / bad['log']).read_text())
        self.assertEqual(good['log_sha256'], digest((self.root / good['log']).read_bytes()))

    def test_missing_check_executable_is_blocked(self):
        result = harness.execute('missing', ['scopehub-missing-fixture-executable-385ab'],
                                 dict(os.environ), self.root / '.reports/runs/missing')
        self.assertEqual('blocked', result['status'])
        self.assertNotIn('exit_code', result)

    def test_source_change_during_harness_forces_failure(self):
        identity = source_identity(self.root)
        changed = {**identity, 'sha256': '0' * 64}

        def passed(name, command, env, run_dir, timeout):
            log = run_dir / f'{name}.log'
            log.write_text('Synthetic source-drift fixture, not acceptance.\n')
            return {'id': name, 'status': 'passed', 'command': command, 'exit_code': 0,
                    'log': log.relative_to(self.root).as_posix(), 'log_sha256': digest(log.read_bytes())}

        with (patch.object(harness, 'ROOT', self.root),
              patch.object(harness, 'REPORTS', self.root / '.reports'),
              patch.object(harness, 'source_identity', side_effect=[identity, changed]),
              patch.object(harness, 'execute', side_effect=passed),
              patch.object(harness, 'tool_version', return_value='fixture'),
              patch.object(sys, 'argv', ['check.py']), redirect_stdout(StringIO())):
            with self.assertRaises(SystemExit) as stopped:
                harness.main()
        self.assertEqual(1, stopped.exception.code)
        report = read_json(self.root / '.reports/test-report.json')
        self.assertFalse(report['source_unchanged'])
        self.assertEqual('failed', report['status'])
        self.assertFalse(report['product_complete'])


class FixtureIsolationTests(unittest.TestCase):
    def test_setup_copies_only_the_canonical_schema(self):
        fixture = ProjectFixture()
        try:
            with patch('tests.project.test_continuity.shutil.copyfile', wraps=shutil.copyfile) as copied:
                fixture.setUp()
            self.assertEqual([REPO / 'contracts/schemas/project.schema.json'],
                             [call.args[0] for call in copied.call_args_list])
            self.assertEqual(records()['project/tasks.json'], fixture.ledger())
        finally:
            fixture.doCleanups()

    def test_actual_repository_records_validate_without_resetting_history(self):
        actual = load_project(REPO)
        self.assertEqual(read_json(REPO / 'project/tasks.json'), actual['ledger'])


if __name__ == '__main__':
    unittest.main()
