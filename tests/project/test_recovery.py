"""Exercise recovery against independently constructed, deliberately broken inputs."""

from contextlib import redirect_stdout
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import recovery
from scripts.project import ProjectError, archive_evidence

REPO = Path(__file__).resolve().parents[2]


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.write('project/tasks.json', {
            'focus_task_id': 'TEST-001',
            'tasks': [{'id': 'TEST-001', 'checks': ['local']},
                      {'id': 'TEST-002', 'checks': ['future']}],
        })
        self.write('project/checks.json', {'checks': [
            {'id': 'local', 'status': 'implemented'},
            {'id': 'native', 'status': 'implemented'},
            {'id': 'future', 'status': 'planned'},
        ]})
        self.write('project/feedback.json', {'items': []})
        for relative in ['project/delivery.json', 'contracts/schemas/project.schema.json', 'package.json']:
            self.write(relative, {})
        self.write('.reports/test-report.json', {'run_id': 'historical', 'status': 'passed', 'checks': [
            {'id': 'local', 'status': 'passed'},
            {'id': 'native', 'status': 'blocked', 'reason_code': 'unsupported_platform'},
            {'id': 'future', 'status': 'blocked', 'reason_code': 'not_implemented'},
        ]})

    def write(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')

    def scripts(self, *names):
        (self.root / 'scripts').mkdir(exist_ok=True)
        for name in names:
            shutil.copyfile(REPO / 'scripts' / name, self.root / 'scripts' / name)

    def invoke(self, script, *args):
        # -S excludes site packages; a bad project.py cannot disable recovery.py.
        return subprocess.run([sys.executable, '-S', str(self.root / 'scripts' / script), *args],
                              cwd=self.root, env={**os.environ, 'PYTHONPATH': ''}, timeout=20,
                              capture_output=True, text=True, encoding='utf-8')

    def kinds(self, result):
        return [item['kind'] for item in result['signals']]

    def test_expected_omissions_are_not_repair_triggers_or_acceptance(self):
        result = recovery.diagnose(self.root)
        self.assertEqual(result['status'], 'no_trigger_observed')
        self.assertEqual(result['acceptance'], 'not_evaluated')
        self.assertEqual(result['recorded_report_freshness'], 'not_verified')
        self.assertEqual(len(result['recorded_run']['observations']), 3)
        self.assertFalse((self.root / '.reports/recovery').exists())

    def test_only_the_selected_task_gets_a_capability_gap(self):
        result = recovery.diagnose(self.root, task_id='TEST-002')
        self.assertIn({'kind': 'task_capability_gap', 'check_id': 'future',
                       'observed_status': 'planned'}, result['signals'])
        self.assertEqual(result['resume_task_id'], 'TEST-002')
        self.assertEqual(result['recorded_focus_task_id'], 'TEST-001')
        self.assertIn('task_unavailable', self.kinds(recovery.diagnose(self.root, task_id='TEST-999')))

    def test_new_dependency_controls_are_preserved_when_malformed(self):
        path = self.root / 'project/dependencies.json'
        broken = b'{"advisory_dispositions": invalid'
        path.write_bytes(broken)
        result = recovery.diagnose(self.root, save=True)
        directory = (self.root / result['snapshot']).parent
        self.assertEqual(broken, (directory / 'originals/project/dependencies.json').read_bytes())
        self.assertEqual('attention_required', result['status'])

    def test_observed_feedback_survives_passing_checks(self):
        self.write('project/feedback.json', {'items': [
            {'id': 'escape', 'task_ids': ['TEST-001'], 'status': 'open'},
            {'id': 'closed', 'task_ids': ['TEST-001'], 'status': 'addressed'},
            {'id': 'unrelated', 'task_ids': ['TEST-002'], 'status': 'open'},
        ]})
        result = recovery.diagnose(self.root)
        relevant = [item['feedback_id'] for item in result['signals'] if item['kind'] == 'task_feedback']
        self.assertEqual(relevant, ['escape'])
        self.assertEqual(result['status'], 'attention_required')

    def test_failed_run_and_missing_executable_are_visible(self):
        self.write('.reports/test-report.json', {'run_id': 'broken', 'status': 'failed', 'checks': [
            {'id': 'local', 'status': 'blocked', 'reason': 'Executable unavailable: example'},
            {'id': 'native', 'status': 'failed', 'log': '.reports/runs/broken/native.log'},
        ]})
        result = recovery.diagnose(self.root)
        self.assertIn('recorded_run_failure', self.kinds(result))
        self.assertEqual(self.kinds(result).count('recorded_check_failure'), 2)

    def test_malformed_bytes_are_retained_without_overwriting_latest(self):
        path = self.root / 'project/tasks.json'
        broken = b'{"tasks":\r\n  [invalid \xff'
        path.write_bytes(broken)
        latest = (self.root / '.reports/test-report.json').read_bytes()
        first = recovery.diagnose(self.root, save=True)
        second = recovery.diagnose(self.root, save=True)
        self.assertNotEqual(first['snapshot'], second['snapshot'])
        directory = (self.root / first['snapshot']).parent
        self.assertEqual((directory / 'originals/project/tasks.json').read_bytes(), broken)
        self.assertEqual((directory / 'originals/.reports/test-report.json').read_bytes(), latest)
        retained = next(item for item in first['files'] if item['path'] == 'project/tasks.json')
        self.assertEqual(retained['sha256'], hashlib.sha256(broken).hexdigest())
        self.assertEqual((self.root / '.reports/test-report.json').read_bytes(), latest)
        self.assertEqual(path.read_bytes(), broken)

    def test_wrong_shapes_and_duplicate_ids_do_not_crash_diagnostics(self):
        cases = [
            ('project/tasks.json', []),
            ('project/tasks.json', {'focus_task_id': {}, 'tasks': [None]}),
            ('project/tasks.json', {'tasks': [{'id': 'duplicate'}, {'id': 'duplicate'}]}),
            ('project/checks.json', {'checks': {}}),
            ('project/feedback.json', {'items': ['invalid']}),
            ('.reports/test-report.json', {'checks': [None]}),
            ('.reports/test-report.json', {'checks': [{'id': 'local', 'status': 'blocked', 'reason_code': []}]}),
        ]
        for relative, value in cases:
            with self.subTest(relative=relative, value=value):
                path = self.root / relative
                original = path.read_bytes()
                self.write(relative, value)
                result = recovery.diagnose(self.root, save=True)
                self.assertEqual(result['acceptance'], 'not_evaluated')
                self.assertTrue(result['signals'])
                path.write_bytes(original)

    def test_missing_and_oversized_controls_do_not_stop_other_observations(self):
        (self.root / 'project/tasks.json').unlink()
        with patch.object(recovery, 'MAX_FILE_BYTES', 1):
            result = recovery.diagnose(self.root, save=True)
        self.assertEqual(result['status'], 'attention_required')
        self.assertTrue(any('size limit' in item.get('detail', '') for item in result['signals']))
        self.assertTrue((self.root / result['snapshot']).is_file())

    def test_git_unavailable_does_not_disable_preservation(self):
        with patch.object(recovery.subprocess, 'run', side_effect=OSError('git is unavailable')):
            result = recovery.diagnose(self.root, save=True)
        self.assertIn('git is unavailable', result['git']['head_error'])
        self.assertTrue((self.root / result['snapshot']).exists())

    def test_external_input_and_output_symlinks_are_not_followed(self):
        with tempfile.TemporaryDirectory() as external:
            outside = Path(external)
            (outside / 'tasks.json').write_bytes(b'{"secret": "fixture"}')
            task_path = self.root / 'project/tasks.json'
            task_path.unlink()
            try:
                task_path.symlink_to(outside / 'tasks.json')
                (self.root / '.reports/recovery').symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest('This host does not permit creating symlinks')
            result = recovery.diagnose(self.root)
            self.assertNotIn('secret', json.dumps(result))
            self.assertIn('control_file', self.kinds(result))
            with self.assertRaisesRegex(ValueError, 'outside the checkout'):
                recovery.diagnose(self.root, save=True)
            self.assertEqual(list(outside.iterdir()), [outside / 'tasks.json'])

    def test_record_commands_and_log_paths_are_never_executed_or_opened(self):
        marker = self.root / 'untrusted-command-ran'
        command = [sys.executable, '-c', f'open({str(marker)!r}, "w").close()']
        self.write('project/checks.json', {'checks': [{'id': 'local', 'status': 'implemented', 'command': command}]})
        self.write('.reports/test-report.json', {'status': 'failed', 'checks': [
            {'id': 'local', 'status': 'failed', 'command': command, 'log': '../../outside.log'},
        ]})
        result = recovery.diagnose(self.root, save=True)
        self.assertIn('recorded_check_failure', self.kinds(result))
        self.assertFalse(marker.exists())

    def test_standalone_entry_survives_broken_project_and_schemas_without_site_packages(self):
        self.scripts('recovery.py')
        (self.root / 'scripts/project.py').write_text('not valid Python !!!\n', encoding='utf-8')
        (self.root / 'contracts/schemas/project.schema.json').write_text('{broken', encoding='utf-8')
        (self.root / 'project/tasks.json').write_text('{broken', encoding='utf-8')
        process = self.invoke('recovery.py', '--task', 'TEST-001', '--save')
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result['status'], 'attention_required')
        self.assertTrue((self.root / result['snapshot']).exists())
        self.assertEqual(result['acceptance'], 'not_evaluated')

    def test_harness_import_failures_preserve_original_error_and_old_report(self):
        self.scripts('check.py', 'recovery.py', 'doctor.py', 'processes.py')
        latest = (self.root / '.reports/test-report.json').read_bytes()
        for source, expected in [('import jsonschema\n', 'ModuleNotFoundError'),
                                 ('not valid Python !!!\n', 'SyntaxError')]:
            with self.subTest(expected=expected):
                (self.root / 'scripts/project.py').write_text(source, encoding='utf-8')
                process = self.invoke('check.py')
                self.assertEqual(process.returncode, 1, process.stderr)
                result = json.loads(process.stdout)
                self.assertEqual(result['status'], 'failed')
                self.assertIn(expected, result['error'])
                diagnostic = json.loads((self.root / result['recovery_snapshot']).read_text())
                error = next(item for item in diagnostic['signals'] if item['kind'] == 'harness_exception')
                self.assertEqual(error['phase'], 'imports')
                self.assertIn(expected, error['traceback'])
                self.assertEqual((self.root / '.reports/test-report.json').read_bytes(), latest)

    def test_failure_before_run_creation_is_preserved(self):
        self.scripts('check.py', 'recovery.py', 'doctor.py', 'processes.py')
        # A minimal project interface isolates the entry point from the live task ledger.
        (self.root / 'scripts/project.py').write_text(
            'class ProjectError(Exception): pass\n'
            'file_digest = project_definition_hash = product_progress = None\n'
            'source_identity = task_definition_hash = validate_model = windows_x64 = None\n'
            'def load_project(root): raise ProjectError("malformed task ledger")\n', encoding='utf-8')
        process = self.invoke('check.py')
        self.assertEqual(process.returncode, 1, process.stderr)
        result = json.loads(process.stdout)
        self.assertIn('malformed task ledger', result['error'])
        diagnostic = json.loads((self.root / result['recovery_snapshot']).read_text())
        self.assertTrue(any(item.get('phase') == 'verification' for item in diagnostic['signals']))
        self.assertFalse((self.root / '.reports/runs').exists())

    def test_bootstrap_failure_retains_explicit_task_instead_of_unrelated_focus(self):
        self.scripts('check.py', 'recovery.py', 'doctor.py', 'processes.py')
        (self.root / 'scripts/project.py').write_text('import jsonschema\n', encoding='utf-8')
        for arguments in [('--task', 'TEST-002'), ('--task=TEST-002',)]:
            with self.subTest(arguments=arguments):
                process = self.invoke('check.py', *arguments)
                self.assertEqual(process.returncode, 1, process.stderr)
                result = json.loads(process.stdout)
                diagnostic = json.loads((self.root / result['recovery_snapshot']).read_text())
                self.assertEqual(diagnostic['resume_task_id'], 'TEST-002')
                self.assertEqual(diagnostic['recorded_focus_task_id'], 'TEST-001')

    def test_snapshot_failure_does_not_mask_original_failure(self):
        (self.root / '.reports/recovery').write_text('output path is a file', encoding='utf-8')
        output = StringIO()
        with redirect_stdout(output):
            recovery.capture_failure(self.root, ValueError('original bootstrap failure'), 'imports')
        result = json.loads(output.getvalue())
        self.assertEqual(result['status'], 'failed')
        self.assertIn('original bootstrap failure', result['error'])
        self.assertIn('recovery_snapshot_error', result)

    def test_diagnostic_cannot_be_archived_as_task_acceptance(self):
        diagnostic = recovery.diagnose(self.root, save=True)
        # Only load_project is replaced: exercise the real archival admission check.
        with patch('scripts.project.load_project', return_value={'tasks': {'TEST-001': {}}}):
            with self.assertRaisesRegex(ProjectError, 'version-bound execution snapshot'):
                archive_evidence(self.root, 'TEST-001', self.root / diagnostic['snapshot'])


if __name__ == '__main__':
    unittest.main()
