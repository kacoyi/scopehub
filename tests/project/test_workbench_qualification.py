"""Guard the effective workbench specification without scoring runtime observations."""

from copy import deepcopy
import unittest

from scripts.check import select_checks
from scripts.development import expand_engineering_sources, resolve_source
from scripts.project import ROOT, load_project, product_progress


def source(path, pointer):
    return resolve_source(ROOT, {'path': path, 'pointer': pointer})


class WorkbenchQualificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = source('engineering-contract-v1.json', '/ui/customization/qualification/cases')
        cls.containment = cls.cases['fault-and-permission-containment']

    def assert_denied_paths(self, paths):
        v1 = source('tests/fixtures/workbench-qualification-v1.json', '/cases/fault-and-permission-containment')
        self.assertEqual(v1['given'], paths['synthetic_control'])
        self.assertEqual(v1['observe'][1], paths['baseline_verdict'])
        self.assertCountEqual(
            paths['paths'],
            ['local-files', 'direct-application-sqlite', 'saved-credentials', 'outbound-to-unapproved-destination'],
        )
        per_path = paths['per_path']
        self.assertLessEqual({'host_decision', 'independent_host_observation'}, set(per_path['required_observations']))
        self.assertEqual({
            'protected_reads': 0, 'protected_bytes_returned': 0, 'unauthorized_mutations': 0,
            'protected_markers_at_unapproved_receiver': 0,
        }, per_path['limits'])

    def test_denied_paths_require_independent_evidence_and_zero_outcomes(self):
        paths = self.containment['permissions']['denied_data_paths']
        self.assert_denied_paths(paths)
        for denied_path in paths['paths']:
            with self.subTest(denied_path=denied_path), self.assertRaises(AssertionError):
                self.assert_denied_paths({**paths, 'paths': [path for path in paths['paths'] if path != denied_path]})
        per_path = paths['per_path']
        for change in ({'required_observations': ['host_decision']},
                       {'limits': {**per_path['limits'], 'unauthorized_mutations': 1}}):
            with self.subTest(change=change), self.assertRaises(AssertionError):
                self.assert_denied_paths({**paths, 'per_path': {**per_path, **change}})

    def assert_native_loss(self, loss):
        common_observations = set(loss['required_observations'])
        self.assertLessEqual({
            'rust_supervisor_loss', 'independent_old_pid_exit', 'fresh_renderer_identity',
            'first_interactive_frame_of_accepted_revision', 'shell_paint_and_input', 'live_quote_service',
        }, common_observations)
        webview = loss['webview2']
        webview_observations = set(webview['required_observations'])
        self.assertEqual('canvas_uses_webview2', webview['applies_when'])
        self.assertLessEqual({
            'user_data_folder_sharing', 'core_webview2_environment_sharing', 'actual_live_renderer_reuse',
            'ProcessFailed', 'failure_kind', 'affected_control',
        }, webview_observations)
        other = loss['other_native_runtime']
        other_observations = set(other['required_observations'])
        self.assertEqual('canvas_demonstrably_uses_a_different_native_runtime', other['applies_when'])
        self.assertEqual(source('contracts/decisions/0027-v1-user-composed-workbench.json', '/decision'), other['authority'])
        self.assertLessEqual({'different_runtime_identity', 'native_renderer_loss_signal', 'affected_canvas_instance'},
                             other_observations)
        misplaced = webview_observations & (common_observations | other_observations)
        self.assertFalse(misplaced, f'WebView2-specific observations outside the WebView2 branch: {sorted(misplaced)}')

    def test_native_loss_requires_runtime_specific_and_common_evidence(self):
        loss = self.containment['native_process_failure']
        self.assertEqual(3000, self.containment['watchdog']['max_stall_to_host_intervention_ms'])
        self.assert_native_loss(loss)
        for branch, observation in (
            ('webview2', 'actual_live_renderer_reuse'), ('webview2', 'ProcessFailed'),
            ('other_native_runtime', 'native_renderer_loss_signal'),
            ('common', 'rust_supervisor_loss'), ('common', 'independent_old_pid_exit'),
            ('common', 'first_interactive_frame_of_accepted_revision'),
        ):
            rejected = deepcopy(loss)
            requirements = rejected if branch == 'common' else rejected[branch]
            requirements['required_observations'].remove(observation)
            with self.subTest(branch=branch, observation=observation), self.assertRaises(AssertionError):
                self.assert_native_loss(rejected)

    def test_webview2_observations_are_exclusive_to_their_runtime(self):
        loss = self.containment['native_process_failure']
        expanded = deepcopy(loss)
        expanded['required_observations'].append('renderer_version_at_recovery')
        self.assert_native_loss(expanded)
        for branch in ('common', 'other_native_runtime'):
            rejected = deepcopy(loss)
            requirements = rejected if branch == 'common' else rejected[branch]
            requirements['required_observations'].append('ProcessFailed')
            with self.subTest(branch=branch), self.assertRaisesRegex(AssertionError, 'WebView2-specific.*ProcessFailed'):
                self.assert_native_loss(rejected)

    def assert_cold_reopen(self, reopen):
        fixture = reopen['source']
        selection = reopen['saved_selection']
        self.assertEqual({
            'kind': 'table_row', 'source_id': 'sim-events', 'source_revision': 'events-1', 'locator': 'event-0910',
        }, selection)
        for key in ('source_id', 'source_revision'):
            self.assertEqual(selection[key], fixture[key])
        rows = [row for row in fixture['rows'] if row['locator'] == selection['locator']]
        self.assertEqual(1, len(rows))
        granted = reopen['granted']
        self.assertEqual(('announced_change_bp', 25, 'basis points'),
                         (granted['field'], granted['expected_value'], granted['unit']))
        self.assertEqual(granted['expected_value'], rows[0][granted['field']])
        self.assertLessEqual({'first_restored_data_frame', 'value_with_source_revision_and_locator', 'restored_saved_selection'},
                             set(granted['required_observations']))
        revoked = reopen['revoked']
        self.assertEqual((0, 0), (revoked['protected_reads'], revoked['protected_data_frames']))
        self.assertLessEqual({'trusted_revoked_status', 'original_binding_identity', 'protected_reads_and_frames_from_startup'},
                             set(revoked['required_observations']))

    def test_cold_reopen_requires_granted_fixture_value_and_revoked_zeroes(self):
        reopen = self.cases['data-quality-and-cold-restore']['cold_reopen']
        self.assert_cold_reopen(reopen)
        for branch, key, value in (
            ('granted', 'expected_value', None), ('saved_selection', 'locator', 'event-0920'),
            ('revoked', 'protected_reads', 1), ('revoked', 'protected_data_frames', 1),
        ):
            rejected = deepcopy(reopen)
            rejected[branch][key] = value
            with self.subTest(branch=branch, key=key), self.assertRaises(AssertionError):
                self.assert_cold_reopen(rejected)

    def test_stale_cold_reopen_source_reference_is_rejected(self):
        raw = source('tests/fixtures/workbench-qualification-v2.json', '/cases/data-quality-and-cold-restore/cold_reopen')
        raw['saved_selection']['$source']['pointer'] += '/missing'
        with self.assertRaisesRegex(ValueError, 'Missing source pointer'):
            expand_engineering_sources(ROOT, raw)

    def test_missing_or_failed_package_qualification_keeps_its_gate_closed(self):
        project = load_project(ROOT)
        gate = 'workbench-qualification'
        self.assertIn(gate, select_checks(project, profile='release'))
        self.assertIn(gate, select_checks(project, task_id='M5-001'))
        self.assertIn(gate, project['delivery']['release_policy']['required_checks'])

        other_checks = [{'id': key, 'status': 'passed'} for key in project['checks'] if key != gate]
        for result, expected in (([], 'blocked'), ([{'id': gate, 'status': 'failed'}], 'failed')):
            with self.subTest(result=result):
                _, acceptance = product_progress(project, other_checks + result)
                qualification, = (row for row in acceptance if gate in row['checks'])
                self.assertEqual(expected, qualification['status'])
