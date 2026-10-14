"""Exercise false completion, stale decisions and delivery feedback with isolated records."""

from contextlib import redirect_stdout
from io import StringIO
import subprocess
import sys
from unittest.mock import patch

from scripts import check as harness
from scripts.project import (
    ProjectError, archive_evidence, digest, json_bytes, load_project, product_progress, readiness,
    read_json, review_history, task_context, workfront,
)
from tests.project.test_continuity import ProjectFixture


class DeliveryTests(ProjectFixture):
    def edit_decision(self):
        path = self.root / 'contracts/decisions/0003-repository-harness.json'
        decision = read_json(path)
        decision['decision'] += ' Changed after validation.'
        path.write_bytes(json_bytes(decision))

    def run_harness(self, args=(), before_check=None):
        def execute(name, command, env, run_dir, timeout):
            if before_check:
                before_check(name)
            log = run_dir / f'{name}.log'
            log.write_text('Synthetic harness fixture, not product acceptance.\n')
            return {'id': name, 'status': 'passed', 'command': command, 'exit_code': 0,
                    'log': log.relative_to(self.root).as_posix(), 'log_sha256': digest(log.read_bytes())}

        with (patch.object(harness, 'ROOT', self.root),
              patch.object(harness, 'REPORTS', self.root / '.reports'),
              patch.object(harness, 'execute', side_effect=execute),
              patch.object(harness, 'tool_version', return_value='fixture'),
              patch.object(harness.platform, 'system', return_value='Linux'),
              patch.object(harness.platform, 'machine', return_value='x86_64'),
              patch.object(sys, 'argv', ['check.py', *args]), redirect_stdout(StringIO())):
            with self.assertRaises(SystemExit) as stopped:
                harness.main()
        return stopped.exception.code, read_json(self.root / '.reports/test-report.json')

    def commit_fixture(self):
        subprocess.run(['git', 'add', '.'], cwd=self.root, capture_output=True, check=True)
        subprocess.run(['git', '-c', 'user.name=Test Fixture', '-c', 'user.email=fixture@example.invalid',
                        '-c', 'commit.gpgsign=false', 'commit', '--quiet', '-m', 'Preserve fixture history'],
                       cwd=self.root, capture_output=True, check=True)
        return subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=self.root,
                              capture_output=True, text=True, check=True).stdout.strip()

    def test_decision_change_invalidates_status_and_archival(self):
        report = self.report()
        self.edit_decision()
        state = workfront(self.root)
        self.assertTrue(state['verification']['matches_current_execution_inputs'])
        self.assertFalse(state['verification']['matches_current_project_definitions'])
        self.assertIsNone(state['milestones'])
        with self.assertRaisesRegex(ProjectError, 'definitions or decisions changed'):
            archive_evidence(self.root, 'M0-001', report)

    def test_task_change_hides_stale_milestone_results(self):
        self.report()
        ledger = self.ledger()
        ledger['tasks'][0]['acceptance'].append('New observable outcome.')
        self.save_ledger(ledger)
        state = workfront(self.root)
        self.assertFalse(state['verification']['current'])
        self.assertIsNone(state['milestones'])

    def test_malformed_report_cannot_appear_current_or_support_completion(self):
        path = self.report()
        report = read_json(path)
        report['source_unchanged'] = 'true'
        path.write_bytes(json_bytes(report))
        state = workfront(self.root)
        self.assertFalse(state['verification']['current'])
        self.assertIsNotNone(state['verification']['report_error'])
        with self.assertRaisesRegex(ProjectError, 'HarnessReport'):
            archive_evidence(self.root, 'M0-001', path)

    def test_commit_after_dirty_verification_does_not_create_release_proof(self):
        self.report()
        self.commit_fixture()
        result = readiness(self.root)
        self.assertTrue(result['verification']['current'])
        self.assertIn('Release verification must name the same clean committed revision.', result['blockers'])

    def test_decision_change_during_run_fails_the_report(self):
        code, report = self.run_harness(before_check=lambda name: self.edit_decision() if name == 'project-state' else None)
        self.assertEqual(1, code)
        self.assertTrue(report['source_unchanged'])
        self.assertFalse(report['project_unchanged'])
        self.assertEqual('failed', report['status'])
        self.assertFalse(report['product_complete'])

    def test_invalid_state_at_end_is_recorded_as_failure(self):
        def invalidate(name):
            if name == 'project-state':
                ledger = self.ledger()
                ledger['focus_task_id'] = None
                self.save_ledger(ledger)

        code, report = self.run_harness(before_check=invalidate)
        self.assertEqual(1, code)
        self.assertIn('focus task', report['project_state_error'])
        self.assertFalse(report['project_unchanged'])

    def test_fast_checks_cannot_complete_unexecuted_task_checks(self):
        code, report = self.run_harness(['--profile', 'fast'])
        self.assertEqual(0, code)
        self.assertEqual('passed', report['status'])
        results = {item['id']: item for item in report['checks']}
        self.assertEqual('not_selected', results['rust-lints']['reason_code'])
        self.assertFalse(report['product_complete'])
        with self.assertRaisesRegex(ProjectError, 'Required check did not pass'):
            archive_evidence(self.root, 'M0-001', self.root / '.reports/test-report.json')

    def test_selecting_planned_work_returns_blocked_and_nonzero(self):
        code, report = self.run_harness(['--task', 'M1-002'])
        self.assertEqual(1, code)
        self.assertEqual('blocked', report['status'])
        self.assertEqual({'environment', 'project-state', 'market-fixtures'}, set(report['selection']['checks']))

    def test_change_runs_exclude_expensive_release_checks_but_task_selection_can_include_them(self):
        project = load_project(self.root)
        self.assertNotIn('runtime-stability', harness.select_checks(project))
        self.assertIn('runtime-stability', harness.select_checks(project, profile='release'))
        self.assertIn('runtime-stability', harness.select_checks(project, task_id='M5-001'))
        code, report = self.run_harness(['--profile', 'release'])
        self.assertEqual(1, code)
        self.assertEqual('blocked', report['status'])

    def test_fast_run_stops_after_failure_and_preserves_the_omissions(self):
        def failed(name, command, env, run_dir, timeout):
            return {'id': name, 'status': 'failed', 'command': command, 'exit_code': 2}

        with (patch.object(harness, 'ROOT', self.root),
              patch.object(harness, 'REPORTS', self.root / '.reports'),
              patch.object(harness, 'execute', side_effect=failed) as executed,
              patch.object(harness, 'tool_version', return_value='fixture'),
              patch.object(sys, 'argv', ['check.py', '--profile', 'fast']), redirect_stdout(StringIO())):
            with self.assertRaises(SystemExit) as stopped:
                harness.main()
        self.assertEqual(1, stopped.exception.code)
        self.assertEqual(1, executed.call_count)
        report = read_json(self.root / '.reports/test-report.json')
        self.assertEqual('failed', report['status'])
        self.assertEqual('earlier_failure', next(item for item in report['checks'] if item['id'] == 'project-state')['reason_code'])

    def test_require_windows_cannot_be_satisfied_by_a_fast_linux_run(self):
        code, report = self.run_harness(['--profile', 'fast', '--require-windows'])
        self.assertEqual(1, code)
        self.assertEqual('blocked', report['status'])
        self.assertFalse(report['native_requirement']['satisfied'])
        self.assertFalse(report['product_complete'])

    def test_cancelled_history_does_not_prevent_a_delivered_replacement_from_passing(self):
        project = load_project(self.root)
        # Synthetic states test aggregation; real task completion is validated separately.
        for task in project['tasks'].values():
            if task['milestone'] == 'M0':
                task['status'] = 'done' if task['id'] == 'M0-004' else 'cancelled'
        results = [{'id': key, 'status': 'passed'} for key in project['checks']]
        milestone = product_progress(project, results)[0][0]
        self.assertEqual('passed', milestone['status'])
        self.assertIn('M0-001', milestone['cancelled_task_ids'])
        project['tasks']['M0-004']['status'] = 'cancelled'
        self.assertEqual('blocked', product_progress(project, results)[0][0]['status'])

    def test_failed_run_cannot_archive_even_when_task_checks_pass(self):
        path = self.report()
        report = read_json(path)
        report['checks'].append({'id': 'environment', 'status': 'failed', 'command': [], 'exit_code': 1})
        path.write_bytes(json_bytes(report))
        with self.assertRaisesRegex(ProjectError, 'failed verification run'):
            archive_evidence(self.root, 'M0-001', path)

    def test_active_work_needs_a_plan_and_completed_dependencies(self):
        ledger = self.ledger()
        ledger['tasks'][0]['status'] = 'active'
        del ledger['tasks'][0]['plan']
        self.save_ledger(ledger)
        with self.assertRaisesRegex(ProjectError, 'needs a delivery plan'):
            load_project(self.root)
        ledger['tasks'][0]['status'] = 'planned'
        ledger['tasks'][2]['status'] = 'active'
        ledger['focus_task_id'] = 'M0-003'
        self.save_ledger(ledger)
        with self.assertRaisesRegex(ProjectError, 'unfinished dependencies'):
            load_project(self.root)

    def test_proposed_decision_cannot_authorize_active_work(self):
        ledger = self.ledger()
        ledger['tasks'][0]['status'] = 'active'
        self.save_ledger(ledger)
        path = self.root / 'contracts/decisions/0003-repository-harness.json'
        decision = read_json(path)
        decision['status'] = 'proposed'
        path.write_bytes(json_bytes(decision))
        with self.assertRaisesRegex(ProjectError, 'unaccepted decision'):
            load_project(self.root)

    def test_uncovered_risks_cannot_be_hidden_in_a_task_plan(self):
        ledger = self.ledger()
        ledger['tasks'][0]['plan']['risks'].append({'failure': 'Unverified installation.', 'checks': ['windows-installation']})
        self.save_ledger(ledger)
        with self.assertRaisesRegex(ProjectError, 'uncovered risks'):
            load_project(self.root)

    def test_context_and_scenarios_identify_native_and_mock_boundaries(self):
        context = task_context(self.root, 'M0-003')
        self.assertTrue(any(check['platforms'] == 'windows-x64' for check in context['checks']))
        context = task_context(self.root, 'M4-001')
        self.assertTrue(any('mock evaluation' in scenario['evidence_boundary'] for scenario in context['scenarios']))

    def test_scenario_cannot_name_checks_without_a_delivery_task(self):
        path = self.root / 'project/delivery.json'
        delivery = read_json(path)
        delivery['scenarios'][0]['checks'].append('release-artifacts')
        path.write_bytes(json_bytes(delivery))
        with self.assertRaisesRegex(ProjectError, 'without a delivery task'):
            load_project(self.root)

    def feedback(self, status='open'):
        item = {'id': 'fixture-defect', 'source': 'Synthetic test observation.', 'observation': 'A previous check missed a defect.',
                'severity': 'major', 'status': status, 'task_ids': ['M0-001'], 'regression_checks': ['project-state'],
                'resolution': None, 'deferral_reason': None}
        if status == 'addressed':
            item['resolution'] = {'cause': 'Missing invariant.', 'correction': 'Validate the invariant.', 'prevention': 'Add regression evidence.'}
        return item

    def save_feedback(self, item):
        (self.root / 'project/feedback.json').write_bytes(json_bytes({'schema_version': '1.0', 'items': [item]}))

    def test_feedback_cannot_be_closed_before_corrective_work(self):
        self.save_feedback(self.feedback('addressed'))
        with self.assertRaisesRegex(ProjectError, 'completed corrective work'):
            load_project(self.root)

    def test_resolved_feedback_has_regression_proof_without_invalidating_receipt(self):
        self.save_feedback(self.feedback())
        self.finish_first(archive_evidence(self.root, 'M0-001', self.report()))
        item = self.feedback('addressed')
        self.save_feedback(item)
        self.assertTrue(workfront(self.root)['verification']['current'])
        item['regression_checks'] = ['environment']
        self.save_feedback(item)
        with self.assertRaisesRegex(ProjectError, 'lacks regression evidence'):
            load_project(self.root)

    def test_deferred_major_feedback_remains_a_release_blocker(self):
        item = self.feedback('deferred')
        self.save_feedback(item)
        with self.assertRaisesRegex(ProjectError, 'explicit reason'):
            load_project(self.root)
        item['deferral_reason'] = 'Needs a native target to investigate.'
        self.save_feedback(item)
        result = readiness(self.root)
        self.assertFalse(result['release_ready'])
        self.assertTrue(any('fixture-defect' in blocker for blocker in result['blockers']))

    def test_history_rejects_rewritten_decisions_and_reopened_completed_tasks(self):
        self.finish_first(archive_evidence(self.root, 'M0-001', self.report()))
        base = self.commit_fixture()
        self.assertEqual('passed', review_history(self.root, base)['status'])
        self.edit_decision()
        with self.assertRaisesRegex(ProjectError, 'Accepted decision history changed'):
            review_history(self.root, base)
        subprocess.run(['git', 'restore', 'contracts/decisions/0003-repository-harness.json'],
                       cwd=self.root, check=True, capture_output=True)
        ledger = self.ledger()
        ledger['tasks'][0].update(status='planned', next_action='Pretend the completed task is new.')
        self.save_ledger(ledger)
        with self.assertRaisesRegex(ProjectError, 'Accepted task history changed'):
            review_history(self.root, base)

    def test_history_json_is_read_as_utf8_under_a_legacy_windows_locale(self):
        base = self.commit_fixture()
        original_run = subprocess.run

        def legacy_locale(argv, **kwargs):
            if argv[:2] == ['git', 'show'] and kwargs.get('text') and not kwargs.get('encoding'):
                kwargs['encoding'] = 'cp1252'
            return original_run(argv, **kwargs)

        with patch('scripts.project.subprocess.run', side_effect=legacy_locale):
            self.assertEqual('passed', review_history(self.root, base)['status'])

    def test_missing_history_base_does_not_claim_comparison(self):
        self.assertEqual('not_applicable', review_history(self.root)['status'])
        with self.assertRaises(subprocess.CalledProcessError):
            review_history(self.root, 'missing-base-385ab')

    def test_unknown_selection_is_an_error(self):
        project = load_project(self.root)
        for args in [{'profile': 'typo'}, {'task_id': 'M0-999'}]:
            with self.subTest(args=args), self.assertRaisesRegex(ProjectError, 'Unknown'):
                harness.select_checks(project, **args)
