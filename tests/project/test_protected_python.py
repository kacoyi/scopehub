from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.diagnostic_archive import DiagnosticArchiveError
from scripts.protected_python import read_native, serial_inventory, serial_result


CHECK = 'python-contracts-workers'
RUN = 'windows-change-100-1'
COMMIT = 'a1' * 20
PASSING_CASE = 'test_runs (tests.project.test_example.ExampleTests.test_runs)'
NOISY_CASE = 'test_reports (tests.project.test_example.ExampleTests.test_reports)'
SKIPPED_CASE = 'test_posix (tests.project.test_example.ExampleTests.test_posix)'
SKIPPED_OUTCOME = "skipped 'Requires POSIX'"
COMMAND = ['{python}', '-m', 'unittest', 'discover', '-s', 'tests', '-t', '.', '-p', 'test_*.py',
           '-v', '--durations', '0']
PYTHON = r'D:\a\scopehub\.venv\Scripts\python.exe'
LOG = '\r\n'.join([
    f'{PASSING_CASE} ... ok',
    f'{NOISY_CASE} ... diagnostic output',
    'continuation of diagnostic output',
    'ok',
    f'{SKIPPED_CASE} ... {SKIPPED_OUTCOME}',
    '',
    'Slowest test durations',
    '----------------------------------------------------------------------',
    f'0.030s     {NOISY_CASE}',
    '',
    '----------------------------------------------------------------------',
    'Ran 3 tests in 0.050s',
    '',
    'OK (skipped=1)',
    '',
]).encode()
OUTCOMES = ((PASSING_CASE, 'ok'), (NOISY_CASE, 'ok'), (SKIPPED_CASE, SKIPPED_OUTCOME))


class NativeSerialInventoryTests(unittest.TestCase):
    def test_ordered_outcomes_follow_actual_verbose_discovery_output(self):
        self.assertEqual(OUTCOMES, serial_inventory(LOG))
        variants = {
            'different test': LOG.replace(b'test_posix', b'test_other'),
            'different skip reason': LOG.replace(b'Requires POSIX', b'Requires Linux'),
            'different order': LOG.replace(PASSING_CASE.encode(), b'placeholder', 1).replace(
                NOISY_CASE.encode(), PASSING_CASE.encode(), 1).replace(b'placeholder', NOISY_CASE.encode(), 1),
        }
        for condition, log in variants.items():
            with self.subTest(condition=condition):
                self.assertNotEqual(OUTCOMES, serial_inventory(log))

    def test_incomplete_or_unsuccessful_output_cannot_establish_coverage(self):
        variants = {
            'absent': b'',
            'stopped after last started case': LOG.split(b'continuation')[0],
            'missing suite verdict': LOG.replace(b'OK (skipped=1)', b''),
            'terminal verdict without summary': LOG.replace(b'Ran 3 tests in 0.050s', b''),
            'missing case outcome': LOG.replace(b'\r\nok\r\n', b'\r\n'),
            'unsuccessful case': LOG.replace(b' ... ok', b' ... FAIL', 1),
            'failed case followed by unrelated output': LOG.replace(b' ... ok', b' ... FAIL\r\nok', 1),
            'unsuccessful suite': LOG.replace(b'OK (skipped=1)', b'FAILED (failures=1)'),
            'unexpected success': LOG.replace(b' ... ok', b' ... unexpected success', 1),
            'missing case': LOG.replace(f'{PASSING_CASE} ... ok\r\n'.encode(), b''),
            'inconsistent skipped count': LOG.replace(b'skipped=1', b'skipped=2'),
            'duplicate identity': LOG.replace(NOISY_CASE.encode(), PASSING_CASE.encode(), 1),
        }
        for condition, log in variants.items():
            with self.subTest(condition=condition), self.assertRaises(ValueError):
                serial_inventory(log)


class NativeSerialResultTests(unittest.TestCase):
    def setUp(self):
        self.check = {'id': CHECK, 'status': 'implemented', 'command': COMMAND, 'windows_timeout_seconds': 1200}
        source = {'git_head': COMMIT, 'working_tree_dirty': False}
        self.report = {
            'report_version': '2.0',
            'run_id': RUN,
            'status': 'passed',
            'source': source,
            'source_end': deepcopy(source),
            'source_unchanged': True,
            'project_unchanged': True,
            'environment': {'system': 'Windows', 'machine': 'AMD64', 'python_executable': PYTHON},
            'native_requirement': {'requested': False, 'satisfied': True},
            'selection': {'profile': 'task', 'task_id': 'M0-153', 'checks': [CHECK]},
            'checks': [{
                'id': CHECK, 'status': 'passed', 'exit_code': 0, 'duration_seconds': 1200,
                'command': [PYTHON, *COMMAND[1:]], 'log': f'.reports/runs/{RUN}/{CHECK}.log',
                'log_sha256': sha256(LOG).hexdigest(),
            }],
        }

    def test_result_uses_the_serial_deadline_and_exact_discovery_command(self):
        self.assertEqual(OUTCOMES, serial_result(self.report, LOG, self.check))
        for fields in ({'duration_seconds': 1200.001}, {'duration_seconds': -1},
                       {'duration_seconds': float('nan')}, {'duration_seconds': True},
                       {'exit_code': 1}, {'status': 'failed'},
                       {'command': [PYTHON, '-m', 'unittest', 'tests.project.test_example']}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                changed = deepcopy(self.report)
                changed['checks'][0].update(fields)
                serial_result(changed, LOG, self.check)
        for fields in ({'windows_timeout_seconds': 1800}, {'status': 'retired'}):
            with self.subTest(catalog=fields), self.assertRaises(ValueError):
                serial_result(self.report, LOG, {**self.check, **fields})

    def test_non_native_or_unstable_reports_cannot_support_the_handoff(self):
        for fields in ({'environment': {**self.report['environment'], 'machine': 'ARM64'}},
                       {'environment': {**self.report['environment'], 'system': 'Linux'}},
                       {'selection': {**self.report['selection'], 'checks': []}},
                       {'source': {**self.report['source'], 'working_tree_dirty': True}},
                       {'source_unchanged': False}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                serial_result({**self.report, **fields}, LOG, self.check)

    def test_original_binds_report_and_case_log_digests(self):
        with TemporaryDirectory() as directory:
            archive = Path(directory)
            run = archive / 'runs' / RUN
            run.mkdir(parents=True)
            raw = json.dumps(self.report).encode()
            original = run / 'test-report.json'
            original.write_bytes(raw)
            log = run / f'{CHECK}.log'
            log.write_bytes(LOG)
            locator = {'run_id': RUN, 'source_commit': COMMIT, 'report_sha256': sha256(raw).hexdigest()}
            self.assertEqual((self.report, LOG), read_native(archive, locator))

            for change in ({'source_commit': 'a2' * 20}, {'report_sha256': '0' * 64}):
                with self.subTest(locator=change), self.assertRaises((DiagnosticArchiveError, ValueError)):
                    read_native(archive, {**locator, **change})

            log.write_bytes(LOG.replace(b'test_posix', b'test_other'))
            with self.assertRaises((DiagnosticArchiveError, ValueError)):
                read_native(archive, locator)
            log.write_bytes(LOG)
            original.write_bytes(raw + b'\n')
            with self.assertRaises((DiagnosticArchiveError, ValueError)):
                read_native(archive, locator)
