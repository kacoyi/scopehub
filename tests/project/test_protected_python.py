from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import integration
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

    def test_protected_push_distinguishes_verified_failure_and_incomplete_evidence(self):
        repository = 'owner/scopehub'
        ci = {'provider': 'github-actions', 'repository': repository, 'event': 'push',
              'run_id': '100', 'run_attempt': '1', 'head_sha': COMMIT, 'checkout_sha': COMMIT}
        source = {**self.report['source'], 'sha256': '3' * 64}
        original = {**self.report, 'ci': ci, 'source': source, 'source_end': source,
                    'project_definition_sha256': '4' * 64}
        receipt = {'ci': {**ci, 'event': 'workflow_dispatch'}, 'source': source,
                   'environment': original['environment']}
        binding = {'repair_commit': COMMIT, 'repair_task_id': 'M0-153', 'feedback_id': 'deadline'}
        project = {'checks': {CHECK: self.check}, 'feedback': {'items': [{'id': 'deadline'}]}}
        policy = {'repository': repository, 'branch': 'main'}
        job = {'name': 'Windows change checks (python)', 'head_sha': COMMIT,
               'status': 'completed', 'conclusion': 'success'}
        attempt = {'path': integration.WORKFLOW, 'id': 100, 'run_attempt': 1, 'head_sha': COMMIT,
                   'event': 'push', 'status': 'completed', 'head_branch': 'main'}
        ledger = {'tasks': [{'id': 'M0-153', 'status': 'done'}]}

        def git(_root, operation, *args):
            if operation == 'show':
                self.assertEqual((f'{COMMIT}:project/tasks.json',), args)
                return json.dumps(ledger).encode()
            self.assertEqual('rev-list', operation)
            return COMMIT.encode()

        with TemporaryDirectory() as directory:
            archive = Path(directory)
            run = archive / 'runs' / RUN
            run.mkdir(parents=True)
            with (patch.object(integration, 'require_complete_history'),
                  patch.object(integration, 'exact_commit'),
                  patch.object(integration, 'require_ancestor'),
                  patch.object(integration, 'git', side_effect=git),
                  patch.object(integration, 'committed_source_identity', return_value=source),
                  patch.object(integration, 'diagnostic_archive_root', return_value=archive),
                  patch.object(integration, 'budget_regression_receipts', return_value=[('', receipt)]),
                  patch.object(integration, 'command') as command,
                  patch.object(integration, 'python_receipt_original') as guard):
                def observe(*, profile='full', failed=False, report_event='push', branch='main',
                            jobs=(job,), guard_log=LOG, report_log=LOG, other_check_failure=False):
                    report = deepcopy(original)
                    report['selection'] = {'profile': profile, 'task_id': None, 'checks': [CHECK]}
                    report['native_requirement']['requested'] = profile == 'full'
                    report['ci']['event'] = report_event
                    row, = report['checks']
                    row['log_sha256'] = sha256(report_log).hexdigest()
                    if failed:
                        report['status'] = 'failed'
                        row.update(status='failed', exit_code=None, duration_seconds=1200.047, reason_code='timeout')
                    if other_check_failure:
                        report['status'] = 'failed'
                        report['selection']['checks'].append('workspace-timeframe')
                        report['checks'].append({'id': 'workspace-timeframe', 'status': 'failed', 'exit_code': 1,
                                                 'log': f'.reports/runs/{RUN}/workspace-timeframe.log',
                                                 'log_sha256': sha256(b'Browser failure').hexdigest()})
                        (run / 'workspace-timeframe.log').write_bytes(b'Browser failure')
                    raw = json.dumps(report).encode()
                    (run / 'test-report.json').write_bytes(raw)
                    (run / f'{CHECK}.log').write_bytes(report_log)
                    locator = {'run_id': RUN, 'source_commit': COMMIT, 'report_sha256': sha256(raw).hexdigest()}
                    endpoint = f'repos/{repository}/actions/runs/100/attempts/1'
                    responses = {endpoint: {**attempt, 'head_branch': branch},
                                 endpoint + '/jobs?per_page=100': {'jobs': jobs}}
                    command.side_effect = lambda args, _: json.dumps(responses[args[2]])
                    guard.side_effect = FileNotFoundError() if guard_log is None else None
                    guard_report = deepcopy(original)
                    if guard_log is not None:
                        guard_report['checks'][0]['log_sha256'] = sha256(guard_log).hexdigest()
                    guard.return_value = (guard_report, guard_log)
                    return integration.python_push_observation(
                        archive, project, COMMIT, binding, {'diagnostic_reports': [locator]}, policy)

                timeout_log = LOG.split(b'continuation')[0]
                failed_job = {**job, 'conclusion': 'failure'}
                for profile in ('full', 'ci-windows-python'):
                    with self.subTest(profile=profile):
                        failed = observe(profile=profile, failed=True, report_log=timeout_log, jobs=(failed_job,))
                        self.assertEqual(('failed', 'timeout'), (failed['status'], failed['reason_code']))
                        self.assertEqual(COMMIT, failed['diagnostic']['source_commit'])
                self.assertEqual('passed', observe()['status'])
                self.assertEqual('passed', observe(other_check_failure=True)['status'])
                mismatch = observe(guard_log=LOG.replace(b'test_posix', b'test_other'))
                self.assertEqual(('failed', 'inventory_mismatch'), (mismatch['status'], mismatch['reason_code']))
                for case in ({'profile': 'ci-windows-python'}, {'guard_log': None}, {'branch': 'other'},
                             {'report_event': 'pull_request'}, {'jobs': (job, job)},
                             {'jobs': ({**job, 'status': 'in_progress'},)},
                             {'jobs': ({**job, 'conclusion': 'cancelled'},), 'failed': True},
                             {'jobs': ({**job, 'head_sha': 'a2' * 20},)}, {'report_log': timeout_log},
                             {'jobs': (failed_job,)}):
                    with self.subTest(case=case):
                        missing = observe(**case)
                        self.assertEqual(('missing', COMMIT), (missing['status'], missing['first_push_commit']))
                        self.assertNotIn('diagnostic', missing)
