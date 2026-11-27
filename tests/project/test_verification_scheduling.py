"""Keep full hosted verification overlap bounded to independent checks."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
from threading import Condition, Event, get_ident
import time
import unittest
from unittest.mock import patch

from scripts import check as harness
from scripts import ci_artifacts
from scripts.process_tree import running_group
from scripts.project import json_bytes, read_json
from scripts.verification_scope import WINDOWS_LANES, ScopeError, preview
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

    def test_interrupt_after_first_finisher_retains_both_rows_and_releases_listener(self):
        for first in ('market-recovery', 'python-contracts-workers'):
            with self.subTest(first=first):
                self.assert_interrupt_after_first_finisher(first)

    def assert_interrupt_after_first_finisher(self, first):
        import _thread

        names = ('python-contracts-workers', 'market-recovery')
        interrupted = next(name for name in names if name != first)
        main_thread = get_ident()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gate = root / 'let-the-listener-finish'
            script = root / 'paired-check.py'
            script.write_text(
                'import socket, sys, time\nfrom pathlib import Path\n'
                'root, name, first = Path(sys.argv[1]), sys.argv[2], sys.argv[3]\n'
                'with socket.socket() as listener:\n'
                '    listener.bind(("127.0.0.1", 0))\n'
                '    listener.listen()\n'
                '    marker = root / name\n'
                '    temporary = marker.with_suffix(".ready")\n'
                '    temporary.write_text(str(listener.getsockname()[1]))\n'
                '    temporary.replace(marker)\n'
                '    print(name + " listener ready", flush=True)\n'
                '    if name == first:\n'
                '        while not all((root / peer).is_file() for peer in '
                '("python-contracts-workers", "market-recovery")):\n'
                '            time.sleep(.02)\n'
                '        print(name + " completed normally", flush=True)\n'
                '    else:\n'
                '        while not (root / "let-the-listener-finish").exists():\n'
                '            time.sleep(.02)\n', encoding='utf-8',
            )
            checks = [{'id': name, 'command': ['{python}', str(script), str(root), name, first],
                       'timeout_seconds': 15} for name in names]
            done = Event()

            def completion_order_reached():
                stack = []
                frame = sys._current_frames()[main_thread]
                while frame is not None:
                    stack.append(frame)
                    frame = frame.f_back
                for frame in stack:
                    if frame.f_code is harness.run_overlapped.__code__:
                        if first == 'python-contracts-workers':
                            future = frame.f_locals.get('python')
                            return future is not None and future.done()
                        result = frame.f_locals.get('results', {}).get('market-recovery', {})
                        return result.get('status') == 'passed' and any(
                            item.f_code is Condition.wait.__code__ for item in stack)
                return False

            def interrupt_after_first():
                deadline = time.monotonic() + 6
                try:
                    while not completion_order_reached():
                        self.assertLess(time.monotonic(), deadline, 'The expected first finisher was not observed.')
                        self.assertFalse(done.wait(.01), 'The runner returned before the interrupt.')
                    port = int((root / interrupted).read_text())
                    with socket.create_connection(('127.0.0.1', port), timeout=1):
                        pass
                    _thread.interrupt_main()
                    done.wait(2)
                    return port
                finally:
                    gate.touch()

            console = StringIO()
            with (patch.object(harness, 'ROOT', root), redirect_stdout(console),
                  ThreadPoolExecutor(max_workers=1) as observer):
                interruption = observer.submit(interrupt_after_first)
                try:
                    rows = harness.run_overlapped(checks, dict(os.environ), root / 'logs',
                                                  system=harness.platform.system())
                except KeyboardInterrupt:
                    self.fail('The interrupt discarded the completed and interrupted result rows.')
                finally:
                    done.set()
                port = interruption.result(timeout=8)

            self.assertCountEqual(names, rows.keys())
            self.assertEqual(('passed', 0), (rows[first]['status'], rows[first]['exit_code']))
            self.assertEqual(('failed', 'interrupted'), (rows[interrupted]['status'], rows[interrupted]['reason_code']))
            with socket.socket() as listener_probe:
                listener_probe.settimeout(1)
                self.assertNotEqual(0, listener_probe.connect_ex(('127.0.0.1', port)))
            transcript = [json.loads(line) for line in console.getvalue().splitlines()]
            self.assertCountEqual(list(rows.values()), [row for row in transcript if 'log_sha256' in row])
            for row in rows.values():
                log = root / row['log']
                self.assertEqual(row['log_sha256'], harness.file_digest(log))
                marker = b'completed normally' if row['id'] == first else b'Check stopped: interrupted.'
                self.assertIn(marker, log.read_bytes())

    @unittest.skipUnless(sys.platform == 'win32', 'Requires native Windows Job Objects.')
    def test_windows_python_pair_interrupt_reaps_both_listener_trees(self):
        import _thread
        import _winapi

        names = ('python-contracts-workers', 'market-recovery')
        with tempfile.TemporaryDirectory() as directory, ExitStack() as handles:
            root = Path(directory)
            listener = root / 'listener.py'
            listener.write_text(
                'import json, os, socket, sys, time\n'
                'from pathlib import Path\n'
                'with socket.socket() as server:\n'
                '    server.bind(("127.0.0.1", 0))\n'
                '    server.listen()\n'
                '    marker = Path(sys.argv[1])\n'
                '    temporary = marker.with_suffix(".tmp")\n'
                '    temporary.write_text(json.dumps([os.getppid(), os.getpid(), server.getsockname()[1]]))\n'
                '    temporary.replace(marker)\n'
                '    print("Owned listener ready", flush=True)\n'
                '    time.sleep(60)\n', encoding='utf-8',
            )
            launcher = 'import subprocess, sys; subprocess.call([sys._base_executable, *sys.argv[1:]])'
            checks = [{'id': name, 'command': ['{python}', '-c', launcher, str(listener), str(root / name)],
                       'timeout_seconds': 30} for name in names]
            done = Event()

            def close_owned(handle):
                try:
                    if _winapi.WaitForSingleObject(handle, 0) == _winapi.WAIT_TIMEOUT:
                        _winapi.TerminateProcess(handle, 1)
                        self.assertEqual(_winapi.WAIT_OBJECT_0, _winapi.WaitForSingleObject(handle, 10_000))
                finally:
                    _winapi.CloseHandle(handle)

            def interrupt_live_checks():
                markers = [root / name for name in names]
                deadline = time.monotonic() + 15
                while not all(path.exists() for path in markers):
                    self.assertLess(time.monotonic(), deadline, 'Both native listeners must start.')
                    self.assertFalse(done.wait(.02), 'Both listeners must start before the runner returns.')
                observations = []
                for marker in markers:
                    parent, child, port = json.loads(marker.read_text(encoding='utf-8'))
                    self.assertNotEqual(parent, child)
                    owned = []
                    for pid in (parent, child):
                        handle = _winapi.OpenProcess(_winapi.SYNCHRONIZE | 0x0001, False, pid)
                        handles.callback(close_owned, handle)
                        self.assertEqual(_winapi.WAIT_TIMEOUT, _winapi.WaitForSingleObject(handle, 0))
                        owned.append(handle)
                    with socket.create_connection(('127.0.0.1', port), timeout=1):
                        pass
                    observations.append((owned, port))
                self.assertNotEqual(observations[0][1], observations[1][1])
                # Exercise the runner's SIGINT handler without broadcasting to the CI console.
                _thread.interrupt_main()
                return observations

            with (patch.object(harness, 'ROOT', root), redirect_stdout(StringIO()),
                  ThreadPoolExecutor(max_workers=1) as observer):
                interruption = observer.submit(interrupt_live_checks)
                try:
                    rows = harness.run_overlapped(checks, dict(os.environ), root / 'logs', system='Windows')
                finally:
                    done.set()
                observations = interruption.result(timeout=17)

            self.assertCountEqual(names, rows.keys())
            for name, (owned, port) in zip(names, observations, strict=True):
                row = rows[name]
                self.assertEqual(('failed', 'interrupted'), (row['status'], row['reason_code']))
                self.assertTrue(all(_winapi.WaitForSingleObject(handle, 0) == _winapi.WAIT_OBJECT_0
                                    for handle in owned))
                with socket.socket() as probe:
                    probe.settimeout(1)
                    self.assertNotEqual(0, probe.connect_ex(('127.0.0.1', port)))
                log = root / row['log']
                self.assertIn(b'Check stopped: interrupted.', log.read_bytes())
                self.assertEqual(row['log_sha256'], harness.file_digest(log))
            print('Windows Job Object interruption reaped both command parents and listener descendants.', flush=True)

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
    recovery_screenshot = 'market-recovery/browser/market-closed-bar-repair-fixture/verified-closed-bar-repair.png'

    def setUp(self):
        super().setUp()
        self.downloads = Path(self.enterContext(tempfile.TemporaryDirectory()))
        catalog_path = self.root / 'project/checks.json'
        catalog = read_json(catalog_path)
        for name in ('dependency-review', 'python-contracts-workers', 'workspace-timeframe', 'market-recovery'):
            catalog['checks'].append(dict(catalog['checks'][0], id=name))
            catalog['required_change_checks'].append(name)
        for check in catalog['checks'][-2:]:
            check['command'] = ['{python}', '-c', 'import os, sys; sys.exit(int(os.environ.get("SCOPEHUB_FIXTURE_EXIT", 0)))']
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

    def produce(self, profile=None, *, browser_exit=0, recovery_exit=0):
        reports = {}
        for lane in ('core',) if profile else WINDOWS_LANES:
            args = ('--profile', profile) if profile else ('--ci-windows-lane', lane)
            expected_exit = {'browser': browser_exit, 'recovery': recovery_exit}.get(lane, 0)
            with patch.dict(os.environ, {'SCOPEHUB_FIXTURE_EXIT': str(expected_exit)}):
                code, report = self.run_harness(*args)
            self.assertEqual(int(bool(expected_exit)), code)
            destination = self.downloads / f'verification-windows-2-{lane}'
            if lane == 'browser':
                run = self.reports / 'runs' / report['run_id'] / 'workspace-timeframe'
                run.mkdir()
                (run / 'results.json').write_bytes(b'{"synthetic": true}\n')
            if lane == 'recovery':
                screenshot = self.reports / 'runs' / report['run_id'] / self.recovery_screenshot
                screenshot.parent.mkdir(parents=True)
                screenshot.write_bytes(b'synthetic screenshot bytes\n')
                (screenshot.parents[2] / 'results.json').write_bytes(b'{"synthetic": true}\n')
            self.assertTrue(ci_artifacts.stage_artifacts(self.root, destination, os.environ, lane=not profile))
            reports[lane] = report
            shutil.rmtree(self.reports)
        return reports

    def test_full_windows_producers_own_disjoint_commands_without_same_runner_overlap(self):
        expected = {
            'core': {'environment', 'project-state', 'dependency-review', 'windows-native-build',
                     'ai-workflow', 'market-fixtures', 'rust-lints'},
            'browser': {'workspace-timeframe'},
            'python': {'python-contracts-workers'},
            'recovery': {'market-recovery'},
        }
        for lane, checks in expected.items():
            with (self.subTest(lane=lane),
                  patch.object(harness, 'run_overlapped', wraps=harness.run_overlapped) as overlap):
                code, report = self.run_harness('--ci-windows-lane', lane)
            shutil.rmtree(self.reports)
            self.assertEqual(0, code)
            self.assertEqual(sorted(checks), report['selection']['checks'])
            planned = {'ai-workflow', 'market-fixtures'} if lane == 'core' else set()
            self.assertEqual(checks - planned, {row['id'] for row in report['checks'] if row['status'] == 'passed'})
            for row in report['checks']:
                if row['id'] in planned:
                    self.assertEqual('not_implemented', row['reason_code'])
            overlap.assert_not_called()

    def test_aggregate_keeps_actual_commands_catalog_order_and_evidence_bytes(self):
        lanes = self.produce()
        self.assertEqual(['python-contracts-workers'], lanes['python']['selection']['checks'])
        self.assertEqual(['market-recovery'], lanes['recovery']['selection']['checks'])
        self.assertEqual(['workspace-timeframe'], lanes['browser']['selection']['checks'])
        self.assertNotIn('market-recovery', lanes['core']['selection']['checks'])
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
        screenshot = final / 'runs' / report['run_id'] / self.recovery_screenshot
        self.assertEqual(b'synthetic screenshot bytes\n', screenshot.read_bytes())
        self.assertEqual(b'{"synthetic": true}\n', (screenshot.parents[2] / 'results.json').read_bytes())

    def test_aggregate_records_each_cache_and_ignores_only_an_intentional_python_omission(self):
        lanes = self.produce()
        output = self.downloads.parent / f'{self.downloads.name}-outputs.txt'
        self.addCleanup(output.unlink, missing_ok=True)
        caches = dict(core='warm', browser='warm', python='not_requested', recovery='warm')
        paths = {lane: self.downloads / f'verification-windows-2-{lane}' / ci_artifacts.TIMING_FILE for lane in caches}
        samples = {lane: ci_artifacts.report_sample(lanes[lane], cache=state) for lane, state in caches.items()}
        warm = samples['recovery']
        recovery_check = next(row for row in warm['checks'] if row['id'] == 'market-recovery')
        boolean_exit = [{**row, 'exit_code': False} if row is recovery_check else row for row in warm['checks']]
        missing_task = {key: value for key, value in warm.items() if key != 'task_id'}
        for lane, timing, state, expected in (
            ('recovery', None, 'unknown', 'unknown'),
            ('recovery', b'{"cache":', 'unknown', 'unknown'),
            ('recovery', json_bytes({**warm, 'source_commit': '0' * 40}), 'unknown', 'unknown'),
            ('recovery', json_bytes({**warm, 'cache': ['warm']}), 'unknown', 'unknown'),
            ('recovery', json_bytes({**warm, 'checks': boolean_exit}), 'unknown', 'unknown'),
            ('recovery', json_bytes({**warm, 'coverage_complete': 1}), 'unknown', 'unknown'),
            ('recovery', json_bytes(missing_task), 'unknown', 'unknown'),
            ('recovery', json_bytes({**warm, 'cache': 'partial'}), 'partial', 'unknown'),
            ('recovery', json_bytes({**warm, 'cache': 'not_restored'}), 'not_restored', 'unknown'),
            ('recovery', json_bytes({**warm, 'cache': 'not_requested'}), 'not_requested', 'unknown'),
            ('python', None, 'unknown', 'unknown'),
            ('python', json_bytes({**samples['python'], 'cache': 'warm'}), 'warm', 'unknown'),
            ('recovery', json_bytes(warm), 'warm', 'warm'),
        ):
            with self.subTest(lane=lane, state=state, timing=timing):
                for owner, sample in samples.items():
                    paths[owner].write_bytes(json_bytes(sample))
                if timing is None:
                    paths[lane].unlink()
                else:
                    paths[lane].write_bytes(timing)
                output.write_bytes(b'')
                with patch.dict(os.environ, {'GITHUB_OUTPUT': str(output)}):
                    code, report = self.run_harness('--require-windows', '--ci-windows-aggregate', str(self.downloads))
                self.assertEqual(0, code)
                self.assertEqual('passed', report['status'])
                actual = dict(line.split('=', 1) for line in output.read_text(encoding='utf-8').splitlines())
                self.assertEqual(expected, actual['cache'])
                self.assertEqual({**caches, lane: state}, json.loads(actual['windows_caches']))
                destination = self.reports / 'published'
                self.assertTrue(ci_artifacts.stage_artifacts(self.root, destination, os.environ))
                _, published = ci_artifacts.stage_timing(destination, {
                    'SCOPEHUB_TIMING_CACHE': actual['cache'], 'SCOPEHUB_TIMING_WINDOWS_CACHES': actual['windows_caches'],
                }, has_report=True)
                self.assertEqual(expected, published['cache'])
                self.assertEqual({**caches, lane: state}, published['windows_caches'])
                shutil.rmtree(self.reports)

    def test_scoped_aggregate_republishes_the_original_selection_and_report(self):
        lanes = self.produce('records')
        producer = self.downloads / 'verification-windows-2-core'
        original = producer / 'test-report.json'
        timing = ci_artifacts.report_sample(lanes['core'], cache='not_requested')
        (producer / ci_artifacts.TIMING_FILE).write_bytes(json_bytes(timing))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'outputs.txt'
            with patch.dict(os.environ, {'GITHUB_OUTPUT': str(output)}):
                code, report = self.run_harness('--profile', 'records', '--ci-windows-aggregate', str(self.downloads))
            self.assertIn('cache=not_requested', output.read_text(encoding='utf-8').splitlines())
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
        with self.assertRaisesRegex(ci_artifacts.PublicationError, 'unexpected result for dependency-review'):
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
        lane = self.downloads / 'verification-windows-2-recovery'
        original = lane / 'test-report.json'
        data = original.read_bytes()
        report = read_json(original)
        per_run = lane / 'runs' / report['run_id'] / original.name
        log = next(lane.rglob('market-recovery.log'))
        log_data = log.read_bytes()
        project = harness.load_project(self.root)

        def collect():
            return ci_artifacts.collect_windows_lanes(
                self.root, self.downloads, project, report['source'], report['ci'], harness.select_checks(project), os.environ,
            )

        lane.rename(self.downloads / 'verification-windows-1-recovery')
        with self.assertRaisesRegex(ci_artifacts.PublicationError, 'Missing or unexpected'):
            collect()
        (self.downloads / 'verification-windows-1-recovery').rename(lane)
        for changed, error in (
            (dict(report, ci=dict(report['ci'], run_attempt='1')), 'provenance'),
            (dict(report, selection=dict(report['selection'], checks=['python-contracts-workers'])), 'selection'),
            (dict(report, checks=list(reversed(report['checks']))), 'different check result catalog or order'),
            (dict(report, environment=dict(report['environment'], system='Linux')), 'platform'),
            (dict(report, status='failed'), 'summary differs from its selected check results'),
        ):
            with self.subTest(error=error):
                for path in (original, per_run):
                    path.write_bytes(json_bytes(changed))
                try:
                    with self.assertRaisesRegex(ci_artifacts.PublicationError, error):
                        collect()
                finally:
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

    def test_failed_recovery_keeps_its_evidence_and_blocks_integration(self):
        lanes = self.produce(recovery_exit=7)
        with patch.dict(os.environ, {'SCOPEHUB_WINDOWS_LANE_RESULT': 'failure'}):
            code, report = self.run_harness('--require-windows', '--ci-windows-aggregate', str(self.downloads))
        self.assertEqual(1, code)
        self.assertEqual('failed', report['status'])
        self.assertIn('market-recovery', report['integration_gates']['unverified'])
        python = next(row for row in lanes['python']['checks'] if row['id'] == 'python-contracts-workers')
        self.assertEqual('passed', python['status'])
        recovery = next(row for row in lanes['recovery']['checks'] if row['id'] == 'market-recovery')
        self.assertEqual(7, recovery['exit_code'])
        self.assertIn(recovery, report['checks'])
        self.assertEqual(recovery['log_sha256'], harness.file_digest(self.root / recovery['log']))
