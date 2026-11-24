"""Keep full hosted verification overlap bounded to independent checks."""

from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
from threading import Event
import time
import unittest
from unittest.mock import patch

from scripts import check as harness
from scripts import ci_artifacts
from scripts.process_tree import running_group
from scripts.project import json_bytes, read_json
from scripts.verification_scope import ScopeError, preview
from tests.project.fixtures import task_by_id
from tests.project.test_continuity import ProjectFixture


class VerificationSchedulingTests(unittest.TestCase):
    def test_build_window_stops_before_workflows_and_unsupported_native_build(self):
        catalog = json.loads((harness.ROOT / 'project/checks.json').read_text(encoding='utf-8'))['checks']
        selected = {check['id'] for check in catalog}
        index = next(i for i, check in enumerate(catalog) if check['id'] == 'python-contracts-workers')
        portable = ['python-contracts-workers', 'typescript-contracts', 'ui-build', 'rust-format',
                    'rust-contracts-workers', 'rust-lints']
        for system, expected in [('Linux', portable), ('Windows', [*portable, 'windows-native-build'])]:
            with self.subTest(system=system):
                window = harness.build_window(catalog, selected, {'system': system, 'machine': 'AMD64'}, index)
                self.assertEqual(expected, [check['id'] for check in window])

    def test_real_commands_overlap_and_build_outputs_are_consumed_in_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            python_ready = root / 'python-ready'
            ui_ready = root / 'ui-ready'
            rust_ready = root / 'rust-ready'

            def wait_for_file(path):
                return f'while not Path({str(path)!r}).exists(): time.sleep(.01)\n'

            def fixture(check_id, body):
                command = ['{python}', '-c', f'from pathlib import Path; import time\n{body}']
                return {'id': check_id, 'command': command, 'timeout_seconds': 5}

            checks = [
                fixture('python-contracts-workers', f'Path({str(python_ready)!r}).touch()\n' + wait_for_file(rust_ready)),
                fixture('ui-build', wait_for_file(python_ready) + f'Path({str(ui_ready)!r}).touch()'),
                fixture('rust-contracts-workers', f'assert Path({str(ui_ready)!r}).exists()\nPath({str(rust_ready)!r}).touch()'),
            ]
            console = StringIO()
            with patch.object(harness, 'ROOT', root), redirect_stdout(console):
                results = harness.run_overlapped(checks, dict(os.environ), root / '.reports',
                                                 system=harness.platform.system())

            self.assertEqual({check['id'] for check in checks}, results.keys())
            self.assertEqual(['passed'] * len(checks), [results[check['id']]['status'] for check in checks])
            self.assertEqual(6, len([json.loads(line) for line in console.getvalue().splitlines()]))
            for check in checks:
                result = results[check['id']]
                self.assertEqual([sys.executable, *check['command'][1:]], result['command'])
                self.assertEqual(result['log_sha256'], harness.file_digest(root / result['log']))

    def test_cleanup_failure_cancels_the_other_lane_and_stops_later_builds(self):
        checks = [{'id': key, 'command': ['{python}'], 'timeout_seconds': 5} for key in
                  ('python-contracts-workers', 'ui-build', 'rust-contracts-workers')]

        def execute(name, command, env, run_dir, timeout, *, cancel):
            if name == 'ui-build':
                return {'status': 'failed', 'reason_code': 'cleanup_failed'}
            self.assertEqual('python-contracts-workers', name)
            deadline = time.monotonic() + 2
            while not cancel() and time.monotonic() < deadline:
                time.sleep(.005)
            self.assertTrue(cancel())
            return {'status': 'failed', 'reason_code': 'interrupted'}

        with patch.object(harness, 'execute', side_effect=execute):
            self.assertEqual(
                {'python-contracts-workers': {'status': 'failed', 'reason_code': 'interrupted'},
                 'ui-build': {'status': 'failed', 'reason_code': 'cleanup_failed'}},
                harness.run_overlapped(checks, {}, Path('unused'), system=harness.platform.system()),
            )

    @unittest.skipUnless(sys.platform == 'linux', 'Linux checks use independent process sessions.')
    def test_interruption_and_termination_stop_both_sessions_and_restore_handlers(self):
        for signum in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signum=signum):
                self.assert_signal_stops_checks(signum)

    def assert_signal_stops_checks(self, signum):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            names = ('python-contracts-workers', 'ui-build')
            checks = []
            for name in names:
                ready = root / name
                descendant = f'from pathlib import Path; import os, time; Path({str(ready)!r}).write_text(str(os.getppid())); time.sleep(30)'
                body = f'import subprocess, sys, time; subprocess.Popen([sys.executable, "-c", {descendant!r}]); time.sleep(30)'
                checks.append({'id': name, 'command': ['{python}', '-c', body], 'timeout_seconds': 15})
            coordinator = (
                'import os, signal; from pathlib import Path; from scripts import check; '
                f'check.ROOT = Path({str(root)!r}); '
                'signal.signal(signal.SIGINT, signal.SIG_IGN); '
                'signal.signal(signal.SIGTERM, signal.SIG_IGN); '
                f'rows = check.run_overlapped({checks!r}, dict(os.environ), check.ROOT / "logs", system="Linux"); '
                'assert signal.getsignal(signal.SIGINT) == signal.SIG_IGN; '
                'assert signal.getsignal(signal.SIGTERM) == signal.SIG_IGN; '
                f'assert tuple(sorted(rows)) == {names!r}; '
                'assert all(row.get("reason_code") == "interrupted" for row in rows.values())'
            )
            with subprocess.Popen([sys.executable, '-c', coordinator], cwd=harness.ROOT,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT) as process:
                ready_files = [root / name for name in names]
                try:
                    deadline = time.monotonic() + 5
                    while not all(file.exists() and file.stat().st_size for file in ready_files):
                        self.assertLess(time.monotonic(), deadline, 'Both descendants must start before cancellation.')
                        time.sleep(.01)
                    process.send_signal(signum)
                    output, _ = process.communicate(timeout=5)
                    self.assertEqual(0, process.returncode, output.decode(errors='replace'))
                    for name, ready in zip(names, ready_files, strict=True):
                        self.assertFalse(running_group(int(ready.read_text())))
                        self.assertIn('Check stopped: interrupted.', (root / 'logs' / f'{name}.log').read_text())
                finally:
                    if process.poll() is None:
                        process.kill()
                    for ready in ready_files:
                        if ready.exists() and ready.stat().st_size:
                            group = int(ready.read_text())
                            if running_group(group):
                                os.killpg(group, signal.SIGKILL)


class HostedSchedulingTests(ProjectFixture):
    def test_online_dependency_failure_is_reported_before_expensive_selected_checks(self):
        project = harness.load_project(harness.ROOT)

        def execute(name, command, env, run_dir, timeout):
            failed = name == 'dependency-review'
            log = run_dir / f'{name}.log'
            log.write_text('Synthetic scheduling observation.\n', encoding='utf-8')
            return {'id': name, 'command': command, 'status': 'failed' if failed else 'passed',
                    'exit_code': 1 if failed else 0, 'log': log.relative_to(self.root).as_posix(),
                    'log_sha256': harness.file_digest(log)}

        for args in ([], ['--fail-fast'], ['--profile', 'tooling'], ['--task', 'M0-081']):
            with (self.subTest(args=args), patch.object(harness, 'load_project', return_value=project),
                  patch.object(harness, 'ROOT', self.root), patch.object(harness, 'REPORTS', self.root / '.reports'),
                  patch.object(harness, 'ci_provenance', return_value=None), patch.object(harness, 'maintain_automatically'),
                  patch.object(harness, 'tool_version', return_value='fixture'),
                  patch.object(harness.platform, 'system', return_value='Linux'),
                  patch.object(harness, 'execute', side_effect=execute) as executed,
                  patch.object(sys, 'argv', ['check.py', *args]), redirect_stdout(StringIO()),
                  self.assertRaises(SystemExit) as stopped):
                harness.main()
            self.assertEqual(1, stopped.exception.code)
            calls = [call.args[0] for call in executed.call_args_list]
            first = ['environment', 'project-state', 'dependency-review']
            self.assertEqual(first, calls[:3])
            report = read_json(self.root / '.reports/test-report.json')
            selected = set(report['selection']['checks'])
            applicable = {key for key in selected if project['checks'][key]['status'] == 'implemented'
                          and project['checks'][key]['platforms'] == 'all'}
            if '--fail-fast' in args:
                self.assertEqual(first, calls)
                rows = {row['id']: row for row in report['checks']}
                self.assertEqual('earlier_failure', rows['python-contracts-workers']['reason_code'])
            else:
                self.assertCountEqual(applicable, calls)

    def test_full_and_narrow_runs_keep_catalog_order_and_platform_budgets(self):
        catalog_path = self.root / 'project/checks.json'
        catalog = read_json(catalog_path)
        build_ids = ['python-contracts-workers', 'rust-contracts-workers']
        template = catalog['checks'][2]
        catalog['checks'][2:2] = [
            {**template, 'id': 'python-contracts-workers', 'windows_timeout_seconds': 25},
            {**template, 'id': 'rust-contracts-workers'},
        ]
        catalog['checks'].append({**template, 'id': 'operations-workflow'})
        catalog['required_change_checks'].extend([*build_ids, 'operations-workflow'])
        catalog_path.write_bytes(json_bytes(catalog))
        ledger = self.ledger()
        task_by_id(ledger, 'M0-002')['checks'].extend([*build_ids, 'operations-workflow'])
        self.save_ledger(ledger)
        provenance = {
            'provider': 'github-actions', 'repository': 'example/project', 'run_id': '1', 'run_attempt': '1',
            'event': 'workflow_dispatch', 'head_sha': 'a' * 40, 'checkout_sha': 'a' * 40,
            'run_url': 'https://github.com/example/project/actions/runs/1',
        }

        def execute(name, command, env, run_dir, timeout, *, cancel=None):
            self.assertEqual(expected_timeouts[name], timeout)
            if cancel is not None and name == 'rust-lints':
                build_finished.set()
            if cancel is not None and name == 'python-contracts-workers':
                self.assertTrue(build_finished.wait(2))
            if name == 'operations-workflow':
                reuse_modes.append(env.get('SCOPEHUB_REUSE_OPERATIONS_BUILD'))
            failed = name == 'rust-contracts-workers' and break_rust
            log = run_dir / f'{name}.log'
            log.write_text('Synthetic scheduling fixture.\n', encoding='utf-8')
            return {'id': name, 'command': command, 'status': 'failed' if failed else 'passed',
                    'exit_code': 1 if failed else 0,
                    'log': log.relative_to(self.root).as_posix(), 'log_sha256': harness.file_digest(log)}

        for system, args, break_rust, expected_overlap, expected_reuse in [
            ('Linux', [], False, True, ['1']),
            ('Windows', ['--require-windows'], False, True, ['1']),
            ('Linux', [], True, True, [None]),
            ('Linux', ['--fail-fast'], False, False, [None]),
            ('Linux', ['--profile', 'fast'], False, False, []),
            ('Linux', ['--profile', 'release'], False, False, [None]),
            ('Windows', ['--task', 'M0-002'], False, False, [None]),
        ]:
            expected_timeouts = {check['id']: check['timeout_seconds'] for check in catalog['checks']}
            if system == 'Windows':
                expected_timeouts['python-contracts-workers'] = 25
            build_finished = Event()
            reuse_modes = []
            with (self.subTest(system=system, args=args, break_rust=break_rust),
                  patch.object(harness.platform, 'system', return_value=system),
                  patch.object(harness, 'ROOT', self.root),
                  patch.object(harness, 'REPORTS', self.root / '.reports'),
                  patch.object(harness, 'ci_provenance', return_value=provenance),
                  patch.object(harness, 'tool_version', return_value='fixture'),
                  patch.object(harness, 'maintain_automatically'),
                  patch.object(harness, 'execute', side_effect=execute),
                  patch.object(harness, 'run_overlapped', wraps=harness.run_overlapped) as overlap,
                  patch.dict(os.environ, {'CARGO_TARGET_DIR': '', 'CARGO_BUILD_TARGET_DIR': '', 'CARGO_BUILD_BUILD_DIR': '',
                                          'SCOPEHUB_REUSE_OPERATIONS_BUILD': '1'}),
                  patch.object(sys, 'argv', ['check.py', *args]), redirect_stdout(StringIO()),
                  self.assertRaises(SystemExit)):
                harness.main()
            self.assertEqual(expected_overlap, overlap.called)
            self.assertEqual(expected_reuse, reuse_modes)
            report = read_json(self.root / '.reports/test-report.json')
            self.assertEqual([check['id'] for check in catalog['checks']], [result['id'] for result in report['checks']])


class WindowsLaneTests(ProjectFixture):
    def setUp(self):
        super().setUp()
        self.downloads = Path(self.enterContext(tempfile.TemporaryDirectory()))
        catalog_path = self.root / 'project/checks.json'
        catalog = read_json(catalog_path)
        for name in ('dependency-review', 'python-contracts-workers', 'workspace-timeframe'):
            catalog['checks'].append(dict(catalog['checks'][0], id=name))
            catalog['required_change_checks'].append(name)
        catalog['checks'][-1]['command'] = ['{python}', '-c', 'import os, sys; sys.exit(int(os.environ.get("SCOPEHUB_FIXTURE_EXIT", 0)))']
        catalog['profiles']['records'] = ['environment', 'project-state', 'dependency-review']
        catalog_path.write_bytes(json_bytes(catalog))

        def git(*args):
            command = ['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.org', *args]
            return subprocess.check_output(command, cwd=self.root, stderr=subprocess.PIPE, text=True).strip()

        git('add', '.')
        tree = git('write-tree')
        base = git('commit-tree', tree, '-m', 'Fixture base')
        head = git('commit-tree', tree, '-p', base, '-m', 'Fixture head')
        merge = git('commit-tree', tree, '-p', base, '-p', head, '-m', 'Fixture merge')
        git('checkout', '--detach', merge)
        self.reports = self.root / '.reports'
        self.event = self.downloads.parent / f'{self.downloads.name}-event.json'
        self.addCleanup(self.event.unlink, missing_ok=True)
        self.event.write_bytes(json_bytes({'number': 1, 'pull_request': {'base': {'sha': base}, 'head': {'sha': head}}}))
        self.enterContext(patch.dict(os.environ, {
            'GITHUB_ACTIONS': 'true', 'GITHUB_REPOSITORY': 'fixture/scopehub', 'GITHUB_RUN_ID': '10',
            'GITHUB_RUN_ATTEMPT': '2', 'GITHUB_EVENT_NAME': 'pull_request', 'GITHUB_SHA': merge,
            'GITHUB_REF': 'refs/pull/1/merge', 'GITHUB_EVENT_PATH': str(self.event), 'SCOPEHUB_SOURCE_HEAD': head,
            'SCOPEHUB_WINDOWS_LANE_RESULT': 'success', 'SCOPEHUB_REVIEW_BASE': base, 'GITHUB_OUTPUT': '',
        }))
        self.enterContext(patch.object(harness, 'ROOT', self.root))
        self.enterContext(patch.object(harness, 'REPORTS', self.reports))
        self.enterContext(patch.object(harness.platform, 'system', return_value='Windows'))
        self.enterContext(patch.object(harness.platform, 'machine', return_value='AMD64'))
        self.enterContext(patch.object(harness, 'maintain_automatically'))
        self.enterContext(patch.object(harness, 'tool_version', return_value='fixture'))

    def run_harness(self, *args):
        with (patch.object(sys, 'argv', ['check.py', *args]), redirect_stdout(StringIO()),
              self.assertRaises(SystemExit) as exit):
            harness.main()
        return exit.exception.code, read_json(self.reports / 'test-report.json')

    def produce(self, profile=None, *, browser_exit=0):
        reports = {}
        for lane in ('core',) if profile else harness.WINDOWS_LANES:
            args = ('--profile', profile) if profile else ('--ci-windows-lane', lane)
            expected_exit = browser_exit if lane == 'browser' else 0
            with patch.dict(os.environ, {'SCOPEHUB_FIXTURE_EXIT': str(expected_exit)}):
                code, report = self.run_harness(*args)
            self.assertEqual(int(bool(expected_exit)), code)
            destination = self.downloads / f'verification-windows-2-{lane}'
            if lane == 'browser':
                run = self.reports / 'runs' / report['run_id'] / 'workspace-timeframe'
                run.mkdir()
                (run / 'results.json').write_bytes(b'{"synthetic": true}\n')
            self.assertTrue(ci_artifacts.stage_artifacts(self.root, destination, os.environ, lane=not profile))
            reports[lane] = report
            shutil.rmtree(self.reports)
        return reports

    def test_aggregate_keeps_actual_commands_catalog_order_and_evidence_bytes(self):
        lanes = self.produce()
        self.assertEqual(['python-contracts-workers'], lanes['python']['selection']['checks'])
        self.assertEqual(['workspace-timeframe'], lanes['browser']['selection']['checks'])
        with patch.object(harness, 'execute') as execute:
            code, report = self.run_harness('--require-windows', '--ci-windows-aggregate', str(self.downloads))
        execute.assert_not_called()
        self.assertEqual(0, code)
        self.assertEqual('passed', report['status'])
        self.assertEqual({'requested': True, 'satisfied': True}, report['native_requirement'])
        self.assertEqual('passed', report['integration_gates']['status'])
        self.assertEqual([row['id'] for row in lanes['core']['checks']], [row['id'] for row in report['checks']])
        final = self.downloads / 'published'
        self.assertTrue(ci_artifacts.stage_artifacts(self.root, final, os.environ))
        for lane in lanes.values():
            for row in lane['checks']:
                if row['status'] != 'passed':
                    continue
                self.assertIn(row, report['checks'])
                path = final / row['log'].removeprefix('.reports/')
                self.assertEqual(row['log_sha256'], harness.file_digest(path))
        browser = final / 'runs' / report['run_id'] / 'workspace-timeframe/results.json'
        self.assertEqual(b'{"synthetic": true}\n', browser.read_bytes())

    def test_scoped_aggregate_republishes_the_original_selection_and_report(self):
        self.produce('records')
        original = self.downloads / 'verification-windows-2-core/test-report.json'
        code, report = self.run_harness('--profile', 'records', '--ci-windows-aggregate', str(self.downloads))
        self.assertEqual(0, code)
        self.assertEqual(original.read_bytes(), (self.reports / original.name).read_bytes())
        self.assertEqual(['dependency-review', 'environment', 'project-state'], report['selection']['checks'])

    def test_scoped_aggregate_rejects_a_passed_report_omitting_dependency_review(self):
        lanes = self.produce('records')
        report = lanes['core']
        row = next(row for row in report['checks'] if row['id'] == 'dependency-review')
        producer = self.downloads / 'verification-windows-2-core'
        (producer / row['log'].removeprefix('.reports/')).unlink()
        command = row['command']
        row.clear()
        row.update(id='dependency-review', command=command, status='blocked', reason_code='not_selected')
        for path in (producer / 'test-report.json', producer / 'runs' / report['run_id'] / 'test-report.json'):
            path.write_bytes(json_bytes(report))
        with self.assertRaisesRegex(ci_artifacts.PublicationError, 'selected check results'):
            self.run_harness('--profile', 'records', '--ci-windows-aggregate', str(self.downloads))
        self.assertFalse((self.reports / 'test-report.json').exists())

    def test_full_fallback_accepts_verified_pr_parents_despite_a_stale_preview(self):
        event = read_json(self.event)
        event['pull_request']['merge_commit_sha'] = event['pull_request']['base']['sha']
        self.event.write_bytes(json_bytes(event))
        decision = preview(self.root, ci_env=os.environ)
        self.assertEqual(('full', 'comparison_unavailable'), (decision['profile'], decision['reason_code']))
        self.produce()
        code, report = self.run_harness('--require-windows', '--ci-windows-aggregate', str(self.downloads))
        self.assertEqual(0, code)
        self.assertEqual('passed', report['integration_gates']['status'])
        self.assertEqual(sorted(harness.select_checks(harness.load_project(self.root))), report['selection']['checks'])
        self.assertEqual(os.environ['GITHUB_SHA'], report['ci']['checkout_sha'])
        self.assertEqual(event['pull_request']['head']['sha'], report['ci']['head_sha'])

    def test_scoped_aggregate_requires_a_proven_comparison(self):
        self.produce('records')
        event = read_json(self.event)
        event['pull_request']['merge_commit_sha'] = event['pull_request']['base']['sha']
        self.event.write_bytes(json_bytes(event))
        with self.assertRaisesRegex(ScopeError, 'event merge SHA'):
            self.run_harness('--profile', 'records', '--ci-windows-aggregate', str(self.downloads))
        self.assertFalse((self.reports / 'test-report.json').exists())

    def test_full_fallback_authenticates_pr_parents_at_a_shallow_boundary(self):
        (self.root / '.git/shallow').write_bytes((os.environ['GITHUB_SHA'] + '\n').encode('ascii'))
        decision = preview(self.root, ci_env=os.environ)
        self.assertEqual(('full', 'comparison_unavailable'), (decision['profile'], decision['reason_code']))
        self.produce()
        code, report = self.run_harness('--require-windows', '--ci-windows-aggregate', str(self.downloads))
        self.assertEqual(0, code)
        self.assertEqual('passed', report['integration_gates']['status'])

    def test_main_push_binds_the_aggregate_to_before_and_after(self):
        pull = read_json(self.event)['pull_request']
        after = os.environ['GITHUB_SHA']
        event = {'ref': 'refs/heads/main', 'before': pull['base']['sha'], 'after': after}
        self.event.write_bytes(json_bytes(event))
        with patch.dict(os.environ, {'GITHUB_EVENT_NAME': 'push', 'GITHUB_REF': event['ref'],
                                     'SCOPEHUB_SOURCE_HEAD': after}):
            self.produce()
            code, report = self.run_harness('--require-windows', '--ci-windows-aggregate', str(self.downloads))
            self.assertEqual(0, code)
            self.assertEqual('push', report['ci']['event'])
            self.assertEqual(after, report['ci']['head_sha'])
            self.assertEqual(after, report['ci']['checkout_sha'])
            for changed, error in (({**event, 'after': pull['head']['sha']}, 'after SHA'),
                                   ({**event, 'deleted': True}, 'checked out main revision')):
                with self.subTest(error=error):
                    self.event.write_bytes(json_bytes(changed))
                    with self.assertRaisesRegex(ScopeError, error):
                        self.run_harness('--require-windows', '--ci-windows-aggregate', str(self.downloads))
            for changed in ({**event, 'before': '0' * 40}, {**event, 'forced': True}):
                with self.subTest(event=changed):
                    self.event.write_bytes(json_bytes(changed))
                    decision = preview(self.root, ci_env=os.environ)
                    self.assertEqual(('full', 'comparison_unavailable'), (decision['profile'], decision['reason_code']))
                    shutil.rmtree(self.reports)
                    code, fallback = self.run_harness('--require-windows', '--ci-windows-aggregate', str(self.downloads))
                    self.assertEqual(0, code)
                    self.assertEqual(report['ci'], fallback['ci'])
                    self.assertEqual(report['checks'], fallback['checks'])

    def test_aggregate_rejects_missing_stale_and_changed_lane_evidence(self):
        self.produce()
        lane = self.downloads / 'verification-windows-2-python'
        original = lane / 'test-report.json'
        data = original.read_bytes()
        report = read_json(original)
        per_run = lane / 'runs' / report['run_id'] / original.name
        log = next(lane.rglob('python-contracts-workers.log'))
        log_data = log.read_bytes()
        project = harness.load_project(self.root)

        def collect():
            return ci_artifacts.collect_windows_lanes(
                self.root, self.downloads, project, report['source'], report['ci'], harness.select_checks(project), os.environ,
            )

        lane.rename(self.downloads / 'verification-windows-1-python')
        with self.assertRaisesRegex(ci_artifacts.PublicationError, 'Missing or unexpected'):
            collect()
        (self.downloads / 'verification-windows-1-python').rename(lane)
        for changed, error in (
            (dict(report, ci=dict(report['ci'], run_attempt='1')), 'provenance'),
            (dict(report, selection=dict(report['selection'], checks=[])), 'selection'),
            (dict(report, checks=list(reversed(report['checks']))), 'catalog order'),
            (dict(report, environment=dict(report['environment'], system='Linux')), 'platform'),
        ):
            with self.subTest(error=error):
                for path in (original, per_run):
                    path.write_bytes(json_bytes(changed))
                with self.assertRaisesRegex(ci_artifacts.PublicationError, error):
                    collect()
                shutil.rmtree(self.reports, ignore_errors=True)
        for path in (original, per_run):
            path.write_bytes(data)
        log.write_bytes(log_data + b'tampered')
        with self.assertRaisesRegex(ci_artifacts.PublicationError, 'recorded digest'):
            collect()
        payload = read_json(self.event)
        payload['pull_request']['base']['sha'] = payload['pull_request']['head']['sha']
        self.event.write_bytes(json_bytes(payload))
        with self.assertRaisesRegex(ScopeError, 'two parents'):
            collect()

    def test_cancelled_lane_result_cannot_publish_a_passing_aggregate(self):
        self.produce()
        with patch.dict(os.environ, {'SCOPEHUB_WINDOWS_LANE_RESULT': 'cancelled'}):
            code, report = self.run_harness('--require-windows', '--ci-windows-aggregate', str(self.downloads))
        self.assertEqual(1, code)
        self.assertEqual('failed', report['status'])

    def test_failed_command_keeps_its_original_result_log_and_blocks_integration(self):
        lanes = self.produce(browser_exit=7)
        with patch.dict(os.environ, {'SCOPEHUB_WINDOWS_LANE_RESULT': 'failure'}):
            code, report = self.run_harness('--require-windows', '--ci-windows-aggregate', str(self.downloads))
        self.assertEqual(1, code)
        self.assertEqual('failed', report['status'])
        self.assertEqual('blocked', report['integration_gates']['status'])
        browser = next(row for row in lanes['browser']['checks'] if row['id'] == 'workspace-timeframe')
        self.assertEqual(7, browser['exit_code'])
        self.assertIn(browser, report['checks'])
        self.assertEqual(browser['log_sha256'], harness.file_digest(self.root / browser['log']))
