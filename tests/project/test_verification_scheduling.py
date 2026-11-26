"""Keep full hosted verification overlap bounded to independent checks."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
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
from scripts import ci_artifacts, project, windows_python
from scripts.diagnostic_archive import diagnostic_locator, resolve_diagnostic
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
    recovery_ui_report = 'market-recovery-ui/results.json'

    def setUp(self):
        super().setUp()
        self.downloads = Path(self.enterContext(tempfile.TemporaryDirectory()))
        catalog_path = self.root / 'project/checks.json'
        catalog = read_json(catalog_path)
        for name in ('dependency-review', 'python-contracts-workers', 'workspace-timeframe',
                     'market-recovery', 'market-recovery-ui'):
            catalog['checks'].append(dict(catalog['checks'][0], id=name))
            catalog['required_change_checks'].append(name)
        for check in catalog['checks'][-3:]:
            exit_key = 'SCOPEHUB_FIXTURE_RECOVERY_UI_EXIT' if check['id'] == 'market-recovery-ui' else 'SCOPEHUB_FIXTURE_EXIT'
            check['command'] = ['{python}', '-c', f'import os, sys; sys.exit(int(os.environ.get({exit_key!r}, 0)))']
        catalog['profiles']['records'] = ['environment', 'project-state', 'dependency-review']
        catalog_path.write_bytes(json_bytes(catalog))
        ledger = self.ledger()
        task_by_id(ledger, 'M0-001')['checks'].append('market-recovery-ui')
        self.save_ledger(ledger)

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

    def scope_environment(self, profile):
        base = read_json(self.event)['pull_request']['base']['sha']
        decision = {'profile': profile, 'reason_code': 'reviewed_changes', 'base': base,
                    'checkout': os.environ['GITHUB_SHA'], 'change_count': 1}
        return {'SCOPEHUB_SCOPE_OUTCOME': 'success', 'SCOPEHUB_SCOPE_PROFILE': profile,
                'SCOPEHUB_SCOPE_BASE': base, 'SCOPEHUB_SCOPE_DECISION': json.dumps(decision)}

    def produce(self, profile=None, *, browser_exit=0, recovery_exit=0, recovery_ui_exit=0):
        reports = {}
        attempt = os.environ['GITHUB_RUN_ATTEMPT']
        for lane in ('core',) if profile else harness.WINDOWS_LANES:
            args = ('--profile', profile) if profile else ('--ci-windows-lane', lane)
            fixture_exit = {'browser': browser_exit, 'recovery': recovery_exit}.get(lane, 0)
            ui_exit = recovery_ui_exit if lane == 'recovery' else 0
            with patch.dict(os.environ, {'SCOPEHUB_FIXTURE_EXIT': str(fixture_exit),
                                         'SCOPEHUB_FIXTURE_RECOVERY_UI_EXIT': str(ui_exit)}):
                code, report = self.run_harness(*args)
            self.assertEqual(int(bool(fixture_exit or ui_exit)), code)
            destination = self.downloads / f'verification-windows-{attempt}-{lane}'
            if lane == 'browser':
                run = self.reports / 'runs' / report['run_id'] / 'workspace-timeframe'
                run.mkdir()
                (run / 'results.json').write_bytes(b'{"synthetic": true}\n')
            if lane == 'recovery':
                screenshot = self.reports / 'runs' / report['run_id'] / self.recovery_screenshot
                screenshot.parent.mkdir(parents=True)
                screenshot.write_bytes(b'synthetic screenshot bytes\n')
                (screenshot.parents[2] / 'results.json').write_bytes(b'{"synthetic": true}\n')
                ui_report = self.reports / 'runs' / report['run_id'] / self.recovery_ui_report
                ui_report.parent.mkdir()
                ui_report.write_bytes(b'{"synthetic": "recovery UI"}\n')
            publication_env = {**os.environ, **self.scope_environment(profile)} if profile else os.environ
            self.assertTrue(ci_artifacts.stage_artifacts(self.root, destination, publication_env, lane=not profile))
            reports[lane] = report
            shutil.rmtree(self.reports)
        return reports

    def partition_fixture(self, discovery=None, *, scoped_catalog=False, receipt_scope=False, legacy_runner=False):
        catalog_path = self.root / 'project/checks.json'
        catalog = read_json(catalog_path)
        next(row for row in catalog['checks'] if row['id'] == windows_python.CHECK)['command'] = windows_python.COMMAND
        if scoped_catalog:
            production = read_json(Path(__file__).resolve().parents[2] / 'project/checks.json')
            present = {check['id'] for check in catalog['checks']}
            for profile in ('records', 'tooling', 'localization'):
                catalog['profiles'][profile] = production['profiles'][profile]
                for key in catalog['profiles'][profile]:
                    if key not in present:
                        catalog['checks'].append(dict(catalog['checks'][0], id=key))
                        catalog['required_change_checks'].append(key)
                        present.add(key)
        catalog_path.write_bytes(json_bytes(catalog))
        if receipt_scope:
            ledger = self.ledger()
            task_by_id(ledger, 'M0-001')['checks'].append(windows_python.CHECK)
            task_by_id(ledger, 'M0-004')['plan'] = deepcopy(task_by_id(ledger, 'M0-001')['plan'])
            self.save_ledger(ledger)
            workflow = Path('.github/workflows/verify.yml')
            (self.root / workflow).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(Path(__file__).resolve().parents[2] / workflow, self.root / workflow)
        for directory in ('scripts', 'tests', 'tests/project'):
            (self.root / directory).mkdir()
        runner_source = Path(__file__).resolve().parents[2] / 'scripts/windows_python.py'
        runner = self.root / 'scripts/windows_python.py'
        if legacy_runner:
            source = runner_source.read_text(encoding='utf-8')
            # Exercise the historical output shape even in a shallow test checkout.
            for line in ('CALLBACK_RECORD_VERSION = 1\n',
                         "        'callback_record_version': CALLBACK_RECORD_VERSION,\n",
                         "    write_callback_record(destination.with_suffix('.log'), outcomes)\n"):
                source = source.replace(line, '', 1)
            runner.write_text(source, encoding='utf-8')
        else:
            shutil.copyfile(runner_source, runner)
        for package in ('tests', 'tests/project'):
            (self.root / package / '__init__.py').write_bytes(b'')
        (self.root / 'tests/project/test_first.py').write_text(
            'import unittest\n'
            'class First(unittest.TestCase):\n'
            '    def test_prefix(self): self.assertTrue(True)\n'
            '    @unittest.skip("intentional fixture skip")\n'
            '    def test_skipped(self): pass\n', encoding='utf-8')
        (self.root / 'tests/project/test_verification_scope.py').write_text(
            'import os, time, unittest\n'
            'if "SCOPEHUB_BROKEN_PYTHON_IMPORT" in os.environ:\n'
            '    raise RuntimeError("rejected actual suffix import")\n'
            'class Second(unittest.TestCase):\n'
            '    def test_suffix(self):\n'
            '        time.sleep(0.015)\n'
            '        self.assertNotIn("SCOPEHUB_BAD_PYTHON", os.environ, "rejected actual suffix fixture")\n',
            encoding='utf-8')

        if discovery == 'empty_boundary':
            (self.root / 'tests/project/test_verification_scope.py').write_bytes(b'')
            (self.root / 'tests/project/test_worktree.py').write_text(
                'import unittest\n'
                'class After(unittest.TestCase):\n'
                '    def test_after_boundary(self): self.assertTrue(True)\n', encoding='utf-8')
        elif discovery == 'case_events':
            (self.root / 'tests/project/test_verification_scope.py').write_text(
                'import atexit, os, unittest\n'
                'class Second(unittest.TestCase):\n'
                '    def test_bad(self):\n'
                '        mode = os.environ.get("SCOPEHUB_CASE_EVENTS")\n'
                '        if mode == "inline_stdout":\n'
                '            print("progress", end="", flush=True)\n'
                '            return\n'
                '        if mode == "status_stdout":\n'
                '            print("progress", flush=True)\n'
                '            print("ERROR", flush=True)\n'
                '            return\n'
                '        if mode == "subtest":\n'
                '            with self.subTest(step=1): self.fail("subtest assertion")\n'
                '            with self.subTest(step=2): self.skipTest("subtest skip")\n'
                '            return\n'
                '        if mode == "fixture": return\n'
                '        if mode == "multiple": self.addCleanup(lambda: 1 / 0)\n'
                '        if mode == "header": self.fail("original diagnostic\\nFAIL: second")\n'
                '        if mode: self.fail("original diagnostic")\n'
                '    def test_good(self):\n'
                '        if os.environ.get("SCOPEHUB_CASE_EVENTS") == "":\n'
                '            print("ok")\n'
                '            atexit.register(print, "ordinary unittest shutdown output")\n'
                'class Third(unittest.TestCase):\n'
                '    @classmethod\n'
                '    def setUpClass(cls):\n'
                '        if os.environ.get("SCOPEHUB_CASE_EVENTS") == "fixture": raise RuntimeError("fixture error")\n'
                '    def test_fixture(self): pass\n',
                encoding='utf-8')
        elif discovery in {'package_skip', 'legacy_package_skip'}:
            (self.root / 'tests/project/__init__.py').write_text(
                'import unittest\n'
                'def setUpModule(): raise unittest.SkipTest("package fixtures unavailable")\n'
                'class PackageCase(unittest.TestCase):\n'
                '    def test_package(self): self.fail("package should stay skipped")\n', encoding='utf-8')
            (self.root / 'tests/project/test_skip_submodule.py').write_text(
                'import unittest\n'
                'def setUpModule(): raise unittest.SkipTest("submodule fixtures unavailable")\n'
                'class SubmoduleCase(unittest.TestCase):\n'
                '    def test_submodule(self): self.fail("submodule should stay skipped")\n', encoding='utf-8')
            if discovery == 'legacy_package_skip':
                prefix = self.root / 'tests/project/test_first.py'
                prefix.write_text(prefix.read_text(encoding='utf-8').replace(
                    '    def test_prefix(self): self.assertTrue(True)\n',
                    '    def test_prefix(self):\n'
                    '        """Verbose unittest displays the case description."""\n'
                    '        print("\\n" + "-" * 70 + "\\nRan 1 test in 0.000s\\n\\nOK")\n'
                    '        self.assertTrue(True)\n',
                ), encoding='utf-8')
                (self.root / 'tests/project/test_worktree.py').write_text(
                    'import unittest\n'
                    'def setUpModule(): raise unittest.SkipTest("nested module unavailable")\n'
                    'class Outer:\n'
                    '    class Inner(unittest.TestCase):\n'
                    '        def test_nested(self): self.fail("nested case should stay skipped")\n'
                    'Nested = Outer.Inner\n', encoding='utf-8')
        elif discovery == 'case_exchange':
            (self.root / 'tests/project/test_first.py').write_text(
                'import os, sys, unittest\n'
                'class First(unittest.TestCase):\n'
                '    def test_prefix(self):\n'
                '        if "SCOPEHUB_REVERSE_FIXTURE_SKIP" in os.environ:\n'
                '            self.skipTest("intentional fixture skip")\n'
                '    @classmethod\n'
                '    def tearDownClass(cls):\n'
                '        print("optional cleanup skipped \'fixture\'", file=sys.stderr)\n'
                'class Second(unittest.TestCase):\n'
                '    def test_skipped(self):\n'
                '        if "SCOPEHUB_REVERSE_FIXTURE_SKIP" not in os.environ:\n'
                '            self.skipTest("intentional fixture skip")\n'
                '    @classmethod\n'
                '    def tearDownClass(cls):\n'
                '        print("cleanup ok", file=sys.stderr)\n', encoding='utf-8')
            suffix = self.root / 'tests/project/test_verification_scope.py'
            suffix.write_text(suffix.read_text(encoding='utf-8').replace('test_suffix', 'test_手順'), encoding='utf-8')
            self.enterContext(patch.dict(os.environ, {'PYTHONIOENCODING': 'utf-8'}))
        elif discovery == 'legacy_atexit_suite':
            (self.root / 'tests/project/test_first.py').write_text(
                'import atexit, unittest\n'
                'def cleanup():\n'
                '    unittest.TextTestRunner(verbosity=2).run(unittest.FunctionTestCase(lambda: None))\n'
                'class First(unittest.TestCase):\n'
                '    def test_prefix(self):\n'
                '        atexit.register(cleanup)\n'
                '    @unittest.skip("intentional fixture skip")\n'
                '    def test_skipped(self): pass\n', encoding='utf-8')
        elif discovery == 'stderr_during_digest':
            (self.root / 'tests/project/__init__.py').write_text(
                'from contextlib import contextmanager\n'
                'from pathlib import Path\n'
                'from threading import Event, Thread\n'
                'import os\n'
                'original_open = Path.open\n'
                'ready = Event()\n'
                'def write_final_stderr():\n'
                '    if ready.wait(5): os.write(2, b"ordinary stderr during prefix finalization\\n")\n'
                'writer = Thread(target=write_final_stderr, daemon=True)\n'
                'writer.start()\n'
                'def signal_on_eof(read):\n'
                '    def read_with_signal(*args):\n'
                '        data = read(*args)\n'
                '        if not data:\n'
                '            ready.set()\n'
                '            writer.join(5)\n'
                '        return data\n'
                '    return read_with_signal\n'
                '@contextmanager\n'
                'def open_log(path, mode):\n'
                '    with original_open(path, mode) as raw:\n'
                '        raw.read = signal_on_eof(raw.read)\n'
                '        raw.readinto = signal_on_eof(raw.readinto)\n'
                '        yield raw\n'
                'def fixture_open(path, mode="r", *args, **kwargs):\n'
                '    if mode == "rb" and path.name.startswith("windows-python-") and path.suffix == ".log":\n'
                '        return open_log(path, mode)\n'
                '    return original_open(path, mode, *args, **kwargs)\n'
                'Path.open = fixture_open\n', encoding='utf-8')
        elif discovery == 'destructor':
            module = self.root / 'tests/project/test_first.py'
            with module.open('a', encoding='utf-8') as output:
                output.write('import os\n'
                             'class FinalOutput:\n'
                             '    def __del__(self, write=os.write): write(2, b"ordinary final destructor output\\n")\n'
                             'final_output = FinalOutput()\n')
        elif discovery == 'source_drift':
            module = self.root / 'tests/project/test_first.py'
            with module.open('a', encoding='utf-8') as output:
                output.write('import os\n'
                             'from pathlib import Path\n'
                             'class Drift(unittest.TestCase):\n'
                             '    def test_exercise_source(self):\n'
                             '        if "SCOPEHUB_FIXTURE_SOURCE_DRIFT" in os.environ:\n'
                             '            Path("implementation.py").write_bytes(b"value = 2\\n")\n')
        elif discovery == 'package_error':
            (self.root / 'tests/project/__init__.py').write_text(
                'raise RuntimeError("rejected package import")\n', encoding='utf-8')

        def git(*args):
            return subprocess.check_output(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.org',
                                            *args], cwd=self.root, stderr=subprocess.PIPE, text=True).strip()

        git('add', '.')
        tree = git('write-tree')
        base = read_json(self.event)['pull_request']['base']['sha']
        head = git('commit-tree', tree, '-p', base, '-m', 'Python fixture head')
        merge = git('commit-tree', tree, '-p', base, '-p', head, '-m', 'Python fixture merge')
        git('checkout', '--detach', merge)
        event = {'number': 1, 'pull_request': {'base': {'sha': base}, 'head': {'sha': head}}}
        self.event.write_bytes(json_bytes(event))
        self.enterContext(patch.dict(os.environ, {'GITHUB_SHA': merge, 'SCOPEHUB_SOURCE_HEAD': head}))

    def record_report(self, path, *, artifact=None, error=None, task='M0-001'):
        output = StringIO()
        args = ['project.py', 'record', task, '--report', str(path)]
        if artifact:
            args.extend(('--artifact-root', str(artifact)))
        with patch.object(project, 'ROOT', self.root), patch.object(sys, 'argv', args), redirect_stdout(output):
            if error:
                with self.assertRaises(SystemExit) as failed:
                    project.main()
                self.assertEqual(1, failed.exception.code)
            else:
                project.main()
        result = json.loads(output.getvalue())
        if error:
            self.assertIn(error, result['error'])
        return result

    def checkout_report_head(self, report):
        subprocess.run(['git', 'checkout', '--quiet', '--detach', report['ci']['head_sha']],
                       cwd=self.root, check=True)
        subprocess.run(['git', 'remote', 'add', 'origin', 'https://github.com/fixture/scopehub'],
                       cwd=self.root, check=True)

    @staticmethod
    def seal_legacy_manifest(path, _):
        manifest = read_json(path)
        del manifest['owners']
        path.write_bytes(json_bytes(manifest))

    def test_full_windows_attempt_identity_binds_required_logs_on_import_and_retry(self):
        artifacts = {}
        for attempt in ('1', '2'):
            self.downloads = Path(self.enterContext(tempfile.TemporaryDirectory()))
            with patch.dict(os.environ, {'GITHUB_RUN_ATTEMPT': attempt}):
                self.produce()
                code, report = self.run_harness('--require-windows', '--ci-windows-aggregate', str(self.downloads))
                self.assertEqual((0, '2.0'), (code, report['report_version']))
                artifacts[attempt] = self.downloads / 'published'
                self.assertTrue(ci_artifacts.stage_artifacts(self.root, artifacts[attempt], os.environ))
            shutil.rmtree(self.reports)

        prior = read_json(artifacts['1'] / 'test-report.json')
        expected = read_json(artifacts['2'] / 'test-report.json')
        self.assertEqual(prior['selection'], expected['selection'])
        self.assertEqual([(row['id'], row['status']) for row in prior['checks']],
                         [(row['id'], row['status']) for row in expected['checks']])
        self.checkout_report_head(expected)
        ci = self.reports / 'ci'
        artifact = ci / 'legitimate'
        shutil.copytree(artifacts['2'], artifact)
        receipt = self.record_report(artifact / 'test-report.json', artifact=artifact)
        receipt_path = self.root / receipt['path']
        original_receipt = receipt_path.read_bytes()
        self.assertEqual('2', read_json(receipt_path)['ci']['run_attempt'])

        required = harness.load_project(self.root)['tasks']['M0-001']['checks']
        prior_results = {row['id']: row for row in prior['checks']}
        earlier = ci / 'earlier-required-logs'
        shutil.copytree(artifact, earlier)
        altered = deepcopy(expected)
        for row in altered['checks']:
            if row['id'] in required:
                older = prior_results[row['id']]
                relative = older['log'].removeprefix('.reports/')
                (earlier / relative).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(artifacts['1'] / relative, earlier / relative)
                row.update(log=older['log'], log_sha256=older['log_sha256'])
        earlier_report = earlier / 'test-report.json'
        earlier_report.write_bytes(json_bytes(altered))
        with self.subTest('other attempt paths'):
            self.record_report(earlier_report, artifact=earlier, error='current run and check log')

        swapped = deepcopy(expected)
        by_id = {row['id']: row for row in swapped['checks']}
        environment = by_id['environment']
        by_id['project-state'].update(log=environment['log'], log_sha256=environment['log_sha256'])
        earlier_report.write_bytes(json_bytes(swapped))
        with self.subTest('same attempt wrong check'):
            self.record_report(earlier_report, artifact=earlier, error='current run and check log')

        opened = artifact / f"runs/{expected['run_id']}/project-state.log"
        good_log = opened.read_bytes()
        opened.unlink()
        with self.subTest('reopen with missing original'):
            self.record_report(artifact / 'test-report.json', artifact=artifact, error='Missing verification log')
        opened.write_bytes(good_log)
        self.assertEqual(receipt, self.record_report(artifact / 'test-report.json', artifact=artifact))
        self.assertEqual([receipt_path], list(receipt_path.parent.iterdir()))
        self.assertEqual(original_receipt, receipt_path.read_bytes())

    def test_full_windows_main_push_publication_rejects_a_different_attempt(self):
        pull = read_json(self.event)['pull_request']
        after = os.environ['GITHUB_SHA']
        self.event.write_bytes(json_bytes({'ref': 'refs/heads/main', 'before': pull['base']['sha'], 'after': after}))
        with patch.dict(os.environ, {'GITHUB_EVENT_NAME': 'push', 'GITHUB_REF': 'refs/heads/main',
                                     'SCOPEHUB_SOURCE_HEAD': after, 'GITHUB_RUN_ATTEMPT': '1'}):
            self.produce()
            code, report = self.run_harness('--require-windows', '--ci-windows-aggregate', str(self.downloads))
            self.assertEqual((0, '2.0'), (code, report['report_version']))
            clean = self.downloads / 'clean-push'
            self.assertTrue(ci_artifacts.stage_artifacts(self.root, clean, os.environ))
            raw = {path: path.read_bytes() for path in self.reports.rglob('*.log')}
            latest = self.reports / 'test-report.json'
            named = self.reports / 'runs' / report['run_id'] / 'test-report.json'
            altered = dict(report, ci=dict(report['ci'], run_attempt='2'))
            for path in (latest, named):
                path.write_bytes(json_bytes(altered))
            with patch.dict(os.environ, {'GITHUB_RUN_ATTEMPT': '2'}):
                with self.assertRaisesRegex(ci_artifacts.PublicationError, 'run or attempt'):
                    ci_artifacts.stage_artifacts(self.root, self.downloads / 'wrong-attempt', os.environ)
            self.assertFalse((self.downloads / 'wrong-attempt/test-report.json').exists())
            for path in (latest, named):
                path.write_bytes(json_bytes(report))
            retry = self.downloads / 'clean-push-retry'
            self.assertTrue(ci_artifacts.stage_artifacts(self.root, retry, os.environ))
            self.assertEqual((clean / 'test-report.json').read_bytes(), (retry / 'test-report.json').read_bytes())
            self.assertEqual(raw, {path: path.read_bytes() for path in raw})

    def test_current_split_receipt_binds_the_original_producer_attempt(self):
        self.partition_fixture(receipt_scope=True)
        first = self.downloads / 'first'
        with patch.dict(os.environ, {'GITHUB_RUN_ATTEMPT': '1'}):
            self.produce()
            code, prior = self.run_harness('--require-windows', '--ci-windows-aggregate', str(self.downloads))
            self.assertEqual((0, 'passed'), (code, prior['status']))
            self.assertTrue(ci_artifacts.stage_artifacts(self.root, first, os.environ))
        shutil.rmtree(self.reports)

        self.downloads = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.produce()
        code, current = self.run_harness('--require-windows', '--ci-windows-aggregate', str(self.downloads))
        self.assertEqual((0, 'passed'), (code, current['status']))
        clean = self.reports / 'ci' / 'current'
        clean.parent.mkdir()
        self.assertTrue(ci_artifacts.stage_artifacts(self.root, clean, os.environ))
        self.assertEqual(prior['selection'], current['selection'])
        altered = deepcopy(current)
        paths = {}
        for lane, record in altered['windows_python'].items():
            name = f'windows-python-{lane}.producer.json'
            relative = f"runs/{current['run_id']}/{name}"
            older = (first / 'runs' / prior['run_id'] / name).read_bytes()
            good = (clean / relative).read_bytes()
            self.assertEqual('1', json.loads(older)['ci']['run_attempt'])
            self.assertEqual('2', json.loads(good)['ci']['run_attempt'])
            paths[relative] = (good, older)
            (self.reports / relative).write_bytes(older)
            record['producer_report_sha256'] = project.digest(older)
        report_files = ['test-report.json', f"runs/{current['run_id']}/test-report.json"]
        for path in report_files:
            (self.reports / path).write_bytes(json_bytes(altered))
        rejected = self.downloads / 'another-attempt'
        with self.assertRaisesRegex(ci_artifacts.PublicationError, 'producer'):
            ci_artifacts.stage_artifacts(self.root, rejected, os.environ)
        self.assertFalse((rejected / 'test-report.json').exists())

        self.checkout_report_head(current)
        report_path = clean / 'test-report.json'
        receipt = self.record_report(report_path, artifact=clean)
        for relative, (_, older) in paths.items():
            (clean / relative).write_bytes(older)
        self.record_report(report_path, artifact=clean, error='recorded digest')
        for path in report_files:
            (clean / path).write_bytes(json_bytes(altered))
        self.record_report(report_path, artifact=clean, error='producer')
        for relative, (good, _) in paths.items():
            (clean / relative).write_bytes(good)
        for path in report_files:
            (clean / path).write_bytes(json_bytes(current))
        self.assertEqual(receipt, self.record_report(report_path, artifact=clean))

    def test_legacy_case_outcomes_stay_bound_to_both_original_logs(self):
        self.assert_case_outcomes_stay_bound(legacy=True)

    def test_current_cleanup_cannot_exchange_case_outcomes_or_drop_callback_evidence(self):
        self.assert_case_outcomes_stay_bound(legacy=False)

    def assert_case_outcomes_stay_bound(self, *, legacy):
        self.partition_fixture('case_exchange', receipt_scope=True, legacy_runner=legacy)
        if legacy:
            self.enterContext(patch.object(windows_python, 'seal_manifest', side_effect=self.seal_legacy_manifest))
        serial = subprocess.run([sys.executable, *windows_python.COMMAND[1:]], cwd=self.root,
                                capture_output=True, check=True).stderr.splitlines()
        self.assertIn(b"optional cleanup skipped 'fixture'", serial)
        self.assertIn(b'cleanup ok', serial)
        self.produce()
        code, report = self.run_harness('--require-windows', '--ci-windows-aggregate', str(self.downloads))
        self.assertEqual((0, 'passed'), (code, report['status']))
        destination = Path(self.enterContext(tempfile.TemporaryDirectory()))
        good = destination / 'legitimate'
        self.assertTrue(ci_artifacts.stage_artifacts(self.root, good, os.environ))
        original_parts = ci_artifacts.python_parts(self.reports, report, windows_python.LANES)
        self.assertIn('test_手順'.encode('utf-8'), original_parts[1][1])
        original_row = next(row for row in report['checks'] if row['id'] == windows_python.CHECK)
        original_log = (self.root / original_row['log']).read_bytes()
        self.assertEqual([line for line in serial if b' ... ' in line],
                         [line for line in original_log.splitlines() if b' ... ' in line])

        alternative = destination / 'alternative'
        aggregate = self.reports.rename(destination / 'aggregate-reports')
        try:
            with patch.dict(os.environ, {'SCOPEHUB_REVERSE_FIXTURE_SKIP': '1'}):
                code, alternate_report = self.run_harness('--ci-windows-lane', 'python')
            self.assertEqual((0, 'passed'), (code, alternate_report['status']))
            self.reports.rename(alternative)
        finally:
            shutil.rmtree(self.reports, ignore_errors=True)
            aggregate.rename(self.reports)
        self.assertTrue(ci_artifacts.stage_artifacts(
            self.root, destination / 'alternative-published', os.environ, reports=alternative, lane=True))
        _, alternate_raw, alternate_manifest = ci_artifacts.python_parts(
            alternative, alternate_report, ('python',))[0]
        original_record, original_raw, original_manifest = original_parts[0]
        self.assertEqual(original_manifest['inventory'], alternate_manifest['inventory'])
        self.assertEqual(original_manifest['counts'], alternate_manifest['counts'])
        self.assertEqual([case[:2] for case in original_manifest['cases']],
                         [case[:2] for case in alternate_manifest['cases']])
        self.assertEqual(['ok', 'skipped'], [event[2] for event in original_manifest['events']])
        self.assertEqual(['skipped', 'ok'], [event[2] for event in alternate_manifest['events']])
        self.assertNotEqual(original_raw, alternate_raw)
        self.assertEqual(not legacy, 'callback_record_version' in original_manifest)

        changed = dict(original_manifest, events=alternate_manifest['events'], cases=alternate_manifest['cases'])
        row, log = ci_artifacts.combined_python(
            harness.load_project(self.root), report,
            [(original_record, alternate_raw, alternate_manifest), original_parts[1]])
        variants = {'exchanged': (changed, row, log, 'Original unittest' if legacy else 'callback record differs')}
        if not legacy:
            downgraded = original_manifest.copy()
            del downgraded['callback_record_version']
            variants['downgraded'] = (downgraded, original_row, original_log, 'version differs from the recorded source')
        relative_check = row['log'].removeprefix('.reports/')
        relative_manifest = Path(original_record['log'].removeprefix('.reports/')).with_suffix('.json')
        relative_producer = relative_manifest.with_suffix('.producer.json')
        relative_logs = [part['log'].removeprefix('.reports/') for part, _, _ in original_parts]
        raw_logs = {path: (good / path).read_bytes() for path in relative_logs}
        report_files = ['test-report.json', f"runs/{report['run_id']}/test-report.json"]

        rejected_downloads = {}
        for name, (manifest, derived_row, derived_log, error) in variants.items():
            manifest_bytes = json_bytes(manifest)
            manifest_digest = project.digest(manifest_bytes)
            producer = destination / f'{name}-producer'
            shutil.copytree(self.downloads / 'verification-windows-2-python', producer)
            (producer / relative_manifest).write_bytes(manifest_bytes)
            producer_report = read_json(producer / 'test-report.json')
            producer_report['windows_python']['python']['manifest_sha256'] = manifest_digest
            producer_bytes = json_bytes(producer_report)
            for path in report_files:
                (producer / path).write_bytes(producer_bytes)
            with self.subTest(case=name, entry='producer publication'):
                rejected = destination / f'{name}-rejected-producer'
                with self.assertRaisesRegex(ci_artifacts.PublicationError, error):
                    ci_artifacts.stage_artifacts(self.root, rejected, os.environ, reports=producer, lane=True)
                self.assertFalse((rejected / 'test-report.json').exists())

            downloaded = self.reports / 'ci' / name
            shutil.copytree(good, downloaded)
            altered = deepcopy(report)
            altered['checks'] = [derived_row if check['id'] == derived_row['id'] else check for check in altered['checks']]
            part = altered['windows_python']['python']
            part['manifest_sha256'] = manifest_digest
            if 'producer_report_sha256' in part:
                (downloaded / relative_producer).write_bytes(producer_bytes)
                part['producer_report_sha256'] = project.digest(producer_bytes)
            (downloaded / relative_manifest).write_bytes(manifest_bytes)
            (downloaded / relative_check).write_bytes(derived_log)
            for path in report_files:
                (downloaded / path).write_bytes(json_bytes(altered))
            with self.subTest(case=name, entry='aggregate publication'):
                rejected = destination / f'{name}-rejected-aggregate'
                with self.assertRaisesRegex(ci_artifacts.PublicationError, error):
                    ci_artifacts.stage_artifacts(self.root, rejected, os.environ, reports=downloaded)
                self.assertFalse((rejected / 'test-report.json').exists())
            self.assertEqual(raw_logs, {path: (downloaded / path).read_bytes() for path in relative_logs})
            rejected_downloads[name] = (downloaded, error)

        self.checkout_report_head(report)
        for name, (downloaded, error) in rejected_downloads.items():
            with self.subTest(case=name, entry='ordinary public import'):
                self.record_report(downloaded / 'test-report.json', artifact=downloaded, error=error)
        downloaded = self.reports / 'ci' / 'ordinary'
        shutil.copytree(good, downloaded)
        report_path = downloaded / 'test-report.json'
        receipt = self.record_report(report_path, artifact=downloaded)
        receipt_path = self.root / receipt['path']
        original_receipt = receipt_path.read_bytes()
        for name, (rejected, error) in rejected_downloads.items():
            with self.subTest(case=name, entry='reopen with a changed manifest'):
                shutil.copyfile(rejected / relative_manifest, downloaded / relative_manifest)
                self.record_report(report_path, artifact=downloaded, error='recorded digest')
            with self.subTest(case=name, entry='reopen with a rehashed candidate'):
                self.record_report(rejected / 'test-report.json', artifact=rejected, error=error)
        shutil.copyfile(good / relative_manifest, downloaded / relative_manifest)
        self.assertEqual(receipt, self.record_report(report_path, artifact=downloaded))
        self.assertEqual([receipt_path], list(receipt_path.parent.iterdir()))
        self.assertEqual(original_receipt, receipt_path.read_bytes())

    def test_legacy_atexit_unittest_summary_does_not_replace_the_main_run(self):
        self.partition_fixture('legacy_atexit_suite', legacy_runner=True)
        with patch.object(windows_python, 'seal_manifest', side_effect=self.seal_legacy_manifest):
            self.produce()
        code, report = self.run_harness('--require-windows', '--ci-windows-aggregate', str(self.downloads))
        self.assertEqual((0, 'passed'), (code, report['status']))
        original = self.root / report['windows_python']['python']['log']
        self.assertLess(original.read_bytes().index(b'Ran 2 tests'), original.read_bytes().index(b'Ran 1 test'))
        published = self.downloads / 'published'
        self.assertTrue(ci_artifacts.stage_artifacts(self.root, published, os.environ))
        merged = next(check for check in report['checks'] if check['id'] == windows_python.CHECK)
        combined = (published / merged['log'].removeprefix('.reports/')).read_bytes()
        self.assertIn(b'Ran 3 tests', combined)
        self.assertNotIn(b'FunctionTestCase', combined)

    def test_partitioned_python_keeps_catalog_and_publishes_ordered_raw_evidence(self):
        self.partition_fixture()
        with patch.object(harness, 'execute', wraps=harness.execute) as execute:
            lanes = self.produce()
        self.assertEqual([], lanes['python']['selection']['checks'])
        recovery_checks = ['market-recovery', 'market-recovery-ui']
        self.assertEqual(recovery_checks, lanes['recovery']['selection']['checks'])
        self.assertEqual(recovery_checks, [call.args[0] for call in execute.call_args_list
                                           if call.args[0] in recovery_checks])
        code, report = self.run_harness('--require-windows', '--ci-windows-aggregate', str(self.downloads))
        self.assertEqual(0, code)
        self.assertEqual([row['id'] for row in lanes['core']['checks']], [row['id'] for row in report['checks']])
        self.assertEqual('passed', report['integration_gates']['status'])
        row = next(check for check in report['checks'] if check['id'] == windows_python.CHECK)
        raw = (self.root / row['log']).read_text(encoding='utf-8')
        self.assertLess(raw.index('test_prefix ('), raw.index('test_skipped ('))
        self.assertLess(raw.index('test_skipped ('), raw.index('test_suffix ('))
        self.assertIn('Ran 3 tests', raw)
        self.assertIn('OK (skipped=1)', raw)
        parts = ci_artifacts.python_parts(self.reports, report, windows_python.LANES)
        record, part_raw, manifest = parts[0]
        parts[0] = ({**record, 'started_at': '2000-01-01T00:00:00+00:00'}, part_raw, manifest)
        late, _ = ci_artifacts.combined_python(harness.load_project(self.root), report, parts)
        self.assertEqual(('failed', 'timeout', None), (late['status'], late['reason_code'], late['exit_code']))
        serial = subprocess.run([sys.executable, *windows_python.COMMAND[1:]], cwd=self.root,
                                capture_output=True, text=True, encoding='utf-8', check=True)
        self.assertEqual([line for line in serial.stderr.splitlines() if ' ... ' in line],
                         [line for line in raw.splitlines() if ' ... ' in line])
        archive = self.downloads / 'published'
        self.assertTrue(ci_artifacts.stage_artifacts(self.root, archive, os.environ))
        for lane in windows_python.LANES:
            producer = lanes[lane]['windows_python'][lane]
            path = Path(producer['log'].removeprefix('.reports/'))
            original_lane = self.downloads / f'verification-windows-2-{lane}'
            original = original_lane / path
            self.assertEqual(original.read_bytes(), (archive / path).read_bytes())
            retained_producer = archive / path.with_suffix('.producer.json')
            self.assertEqual((original_lane / 'test-report.json').read_bytes(), retained_producer.read_bytes())
            self.assertEqual(report['windows_python'][lane]['producer_report_sha256'],
                             project.file_digest(retained_producer))

        locator = diagnostic_locator(archive / 'test-report.json')
        archived = resolve_diagnostic(locator, archive)
        self.assertEqual(sorted(f'windows-python-{lane}.{suffix}'
                                for lane in windows_python.LANES for suffix in ('json', 'log', 'producer.json')),
                         sorted(archived['verified_observations']))
        shutil.rmtree(archive)

        shutil.rmtree(self.reports)
        native = self.downloads / 'verification-windows-2-python'
        report_path = native / 'test-report.json'
        report = read_json(report_path)
        run_path = native / 'runs' / report['run_id']
        manifest_path = run_path / 'windows-python-python.json'
        manifest = read_json(manifest_path)
        manifest['cases'].append(manifest['cases'][0])
        manifest_path.write_bytes(json_bytes(manifest))
        report['windows_python']['python']['manifest_sha256'] = harness.file_digest(manifest_path)
        for target in (report_path, run_path / 'test-report.json'):
            target.write_bytes(json_bytes(report))
        with self.assertRaisesRegex(ci_artifacts.PublicationError, 'duplicate or reordered'):
            self.run_harness('--require-windows', '--ci-windows-aggregate', str(self.downloads))

    def test_partitioned_python_rejects_failed_and_interrupted_parts(self):
        self.partition_fixture()
        with patch.dict(os.environ, {'SCOPEHUB_BROKEN_PYTHON_IMPORT': '1'}):
            serial = subprocess.run([sys.executable, *windows_python.COMMAND[1:]], cwd=self.root,
                                    capture_output=True, text=True, encoding='utf-8')
            code, discovery = self.run_harness('--ci-windows-lane', 'recovery')
        self.assertEqual((1, 1), (serial.returncode, code))
        self.assertIn('RuntimeError: rejected actual suffix import', serial.stderr)
        part = discovery['windows_python']['recovery']
        self.assertIn(b'RuntimeError: rejected actual suffix import', (self.root / part['log']).read_bytes())
        shutil.rmtree(self.reports)
        self.produce()
        recovery = self.downloads / 'verification-windows-2-recovery'
        shutil.rmtree(recovery)
        with patch.dict(os.environ, {'SCOPEHUB_BAD_PYTHON': '1'}):
            serial = subprocess.run([sys.executable, *windows_python.COMMAND[1:]], cwd=self.root,
                                    capture_output=True, text=True, encoding='utf-8')
            code, producer = self.run_harness('--ci-windows-lane', 'recovery')
        self.assertEqual(1, serial.returncode)
        self.assertEqual(1, code)
        self.assertTrue(ci_artifacts.stage_artifacts(self.root, recovery, os.environ, lane=True))
        shutil.rmtree(self.reports)
        with patch.dict(os.environ, {'SCOPEHUB_WINDOWS_LANE_RESULT': 'failure'}):
            code, report = self.run_harness('--require-windows', '--ci-windows-aggregate', str(self.downloads))
        self.assertEqual(1, code)
        row = next(check for check in report['checks'] if check['id'] == windows_python.CHECK)
        self.assertEqual(('failed', 1), (row['status'], row['exit_code']))
        merged = (self.root / row['log']).read_text(encoding='utf-8')
        self.assertIn('rejected actual suffix fixture', merged)
        self.assertEqual([line for line in serial.stderr.splitlines() if ' ... ' in line],
                         [line for line in merged.splitlines() if ' ... ' in line])

        # The normal check process tree owns cancellation. Exercise collection of its partial raw log without
        # the completion manifest, and prove that missing part metadata cannot report success either.
        shutil.rmtree(self.reports)
        part = producer['windows_python']['recovery']
        artifact_run = recovery / 'runs' / producer['run_id']
        part_log = artifact_run / 'windows-python-recovery.log'
        part_log.write_bytes(b'part started\nCheck stopped: interrupted.\n')
        part.update(exit_code=None, log_sha256=harness.file_digest(part_log), reason_code='interrupted')
        del part['manifest_sha256']
        (artifact_run / 'windows-python-recovery.json').unlink()
        for target in (recovery / 'test-report.json', artifact_run / 'test-report.json'):
            target.write_bytes(json_bytes(producer))
        with patch.dict(os.environ, {'SCOPEHUB_WINDOWS_LANE_RESULT': 'cancelled'}):
            code, report = self.run_harness('--require-windows', '--ci-windows-aggregate', str(self.downloads))
        self.assertEqual(1, code)
        row = next(check for check in report['checks'] if check['id'] == windows_python.CHECK)
        self.assertEqual(('failed', 'interrupted'), (row['status'], row['reason_code']))
        self.assertIn(b'Check stopped: interrupted.', (self.root / row['log']).read_bytes())
        shutil.rmtree(self.reports)
        del producer['windows_python']
        for target in (recovery / 'test-report.json', artifact_run / 'test-report.json'):
            target.write_bytes(json_bytes(producer))
        with self.assertRaisesRegex(ci_artifacts.PublicationError, 'Missing or unexpected Windows Python parts'):
            self.run_harness('--require-windows', '--ci-windows-aggregate', str(self.downloads))

    def test_partitioned_python_rejects_contradictory_publication(self):
        self.partition_fixture()
        with patch.dict(os.environ, {'SCOPEHUB_BROKEN_PYTHON_IMPORT': '1'}):
            code, failing = self.run_harness('--ci-windows-lane', 'recovery')
        self.assertEqual(1, code)
        failed_raw = (self.root / failing['windows_python']['recovery']['log']).read_bytes()
        self.assertIn(b'FAILED (errors=1)', failed_raw)
        shutil.rmtree(self.reports)

        self.produce()
        rc, original = self.run_harness('--require-windows', '--ci-windows-aggregate', str(self.downloads))
        self.assertEqual(0, rc)
        run = self.reports / 'runs' / original['run_id']
        recovery_log = run / 'windows-python-recovery.log'
        primary_log = run / 'windows-python-python.log'
        raw = {path: path.read_bytes() for path in (recovery_log, primary_log)}
        latest = self.reports / 'test-report.json'
        final = run / 'test-report.json'
        destination = self.downloads / 'publication'
        self.assertTrue(ci_artifacts.stage_artifacts(self.root, destination, os.environ))
        shutil.rmtree(destination)

        def publish(changed):
            for target in (latest, final):
                target.write_bytes(json_bytes(changed))
            return ci_artifacts.stage_artifacts(self.root, destination, os.environ)

        ordered = raw[primary_log].splitlines(keepends=True)
        ordered[:2] = reversed(ordered[:2])
        for label, path, lane, replacement in (
            ('genuine failure with newly bound digest', recovery_log, 'recovery', failed_raw),
            ('case order', primary_log, 'python', b''.join(ordered)),
            ('test total', primary_log, 'python', raw[primary_log].replace(b'Ran 2 tests', b'Ran 8 tests', 1)),
            ('skip total', primary_log, 'python', raw[primary_log].replace(b'OK (skipped=1)', b'OK', 1)),
        ):
            with self.subTest(label=label):
                self.assertNotEqual(raw[path], replacement)
                path.write_bytes(replacement)
                altered = deepcopy(original)
                altered['windows_python'][lane]['log_sha256'] = harness.file_digest(path)
                try:
                    with self.assertRaisesRegex(ci_artifacts.PublicationError, 'unittest'):
                        publish(altered)
                finally:
                    path.write_bytes(raw[path])
                    shutil.rmtree(destination, ignore_errors=True)

        for checks in ([], [key for key in original['selection']['checks'] if key != windows_python.CHECK]):
            with self.subTest(checks=checks):
                altered = deepcopy(original)
                altered['selection']['checks'] = checks
                try:
                    with self.assertRaisesRegex(ci_artifacts.PublicationError, 'selection'):
                        publish(altered)
                finally:
                    shutil.rmtree(destination, ignore_errors=True)

        with self.assertRaisesRegex(ci_artifacts.PublicationError, 'version'):
            publish({**original, 'report_version': '2.0'})
        shutil.rmtree(destination, ignore_errors=True)

        altered = deepcopy(original)
        altered['windows_python']['recovery']['duration_seconds'] = 0
        self.assertIn(b'Ran 1 test in ', raw[recovery_log])
        with self.assertRaisesRegex(ci_artifacts.PublicationError, 'duration'):
            publish(altered)
        shutil.rmtree(destination, ignore_errors=True)

        altered = deepcopy(original)
        altered['windows_python']['python']['started_at'] = '2000-01-01T00:00:00+00:00'
        with self.assertRaisesRegex(ci_artifacts.PublicationError, 'clock'):
            publish(altered)
        shutil.rmtree(destination, ignore_errors=True)

        # A real timeout row must make the aggregate fail, even when both parts passed.
        altered = deepcopy(original)
        altered['windows_python']['python']['started_at'] = '2000-01-01T00:00:00+00:00'
        altered['timing']['elapsed_seconds'] = 2_000_000_000
        parts = ci_artifacts.python_parts(self.reports, altered, windows_python.LANES)
        late, late_log = ci_artifacts.combined_python(harness.load_project(self.root), altered, parts)
        self.assertEqual('timeout', late['reason_code'])
        (self.root / late['log']).write_bytes(late_log)
        altered['checks'] = [late if row['id'] == windows_python.CHECK else row for row in altered['checks']]
        with self.assertRaisesRegex(ci_artifacts.PublicationError, 'verdict'):
            publish(altered)

    def test_partitioned_python_discovery_preserves_missing_boundary_outcomes(self):
        # Each actual suite runs in an independent nested repository with both entrypoints.
        for scenario in ('empty_boundary', 'package_error'):
            with self.subTest(scenario=scenario):
                fixture = WindowsLaneTests('runTest')
                with ExitStack() as stack:
                    stack.callback(fixture.doCleanups)
                    fixture.setUp()
                    fixture.partition_fixture(scenario)
                    serial = subprocess.run([sys.executable, *windows_python.COMMAND[1:]], cwd=fixture.root,
                                            capture_output=True, text=True, encoding='utf-8')
                    expected = 1 if scenario == 'package_error' else 0
                    self.assertEqual(expected, serial.returncode)
                    captured = []
                    for lane in windows_python.LANES:
                        code, report = fixture.run_harness('--ci-windows-lane', lane)
                        self.assertEqual(expected if lane == 'python' else 0, code)
                        part = report['windows_python'][lane]
                        self.assertTrue(ci_artifacts.stage_artifacts(
                            fixture.root, fixture.downloads / lane, os.environ, lane=True))
                        captured.append((fixture.root / part['log']).read_text(encoding='utf-8'))
                        shutil.rmtree(fixture.reports)
                    original_events = [line for line in serial.stderr.splitlines() if ' ... ' in line]
                    partition_events = [line for log in captured for line in log.splitlines() if ' ... ' in line]
                    self.assertEqual(original_events, partition_events)
                    if expected:
                        self.assertIn('RuntimeError: rejected package import', captured[0])

    def test_full_windows_producers_own_disjoint_commands_without_same_runner_overlap(self):
        expected = {
            'core': {'environment', 'project-state', 'dependency-review', 'windows-native-build',
                     'ai-workflow', 'market-fixtures', 'rust-lints'},
            'browser': {'workspace-timeframe'},
            'python': {'python-contracts-workers'},
            'recovery': {'market-recovery', 'market-recovery-ui'},
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
        self.assertEqual(['market-recovery', 'market-recovery-ui'], lanes['recovery']['selection']['checks'])
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
        ui_report = final / 'runs' / report['run_id'] / self.recovery_ui_report
        self.assertEqual(b'{"synthetic": "recovery UI"}\n', ui_report.read_bytes())

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

    def test_scoped_collection_uses_the_profile_available_to_the_workflow_step(self):
        self.partition_fixture(scoped_catalog=True)
        for profile in ('records', 'tooling', 'localization'):
            with self.subTest(profile=profile):
                for key in ('SCOPEHUB_SCOPE_OUTCOME', 'SCOPEHUB_SCOPE_PROFILE', 'SCOPEHUB_SCOPE_DECISION',
                            'SCOPEHUB_SCOPE_BASE'):
                    self.assertNotIn(key, os.environ)
                report = self.produce(profile)['core']
                self.assertEqual('2.0', report['report_version'])
                producer = self.downloads / 'verification-windows-2-core'
                original = (producer / 'test-report.json').read_bytes()
                try:
                    code, imported = self.run_harness('--profile', profile, '--ci-windows-aggregate',
                                                      str(self.downloads))
                    self.assertEqual((0, report), (code, imported))
                    self.assertEqual(original, (self.reports / 'test-report.json').read_bytes())
                    with patch.dict(os.environ, self.scope_environment(profile)):
                        published = self.downloads / 'published'
                        self.assertTrue(ci_artifacts.stage_artifacts(self.root, published, os.environ))
                    self.assertEqual(original, (published / 'test-report.json').read_bytes())
                    shutil.rmtree(published)
                finally:
                    shutil.rmtree(producer)
                    shutil.rmtree(self.reports, ignore_errors=True)

    def test_full_collection_rejects_a_failed_producer_with_passing_part(self):
        self.partition_fixture()
        producer = self.produce()['python']
        self.assertEqual(('passed', 'passed'),
                         (producer['status'], producer['windows_python']['python']['status']))
        producer['status'] = 'failed'
        downloaded = self.downloads / 'verification-windows-2-python'
        for path in (downloaded / 'test-report.json', downloaded / 'runs' / producer['run_id'] / 'test-report.json'):
            path.write_bytes(json_bytes(producer))
        with self.assertRaisesRegex(ci_artifacts.PublicationError, 'verdict'):
            self.run_harness('--require-windows', '--ci-windows-aggregate', str(self.downloads))

    def test_split_runner_retains_original_case_output_and_diagnostic_ownership(self):
        self.partition_fixture('case_events')
        self.produce()
        recovery = self.downloads / 'verification-windows-2-recovery'
        passing = ('', 'inline_stdout', 'status_stdout')
        for mode in (*passing, 'multiple', 'header', 'subtest', 'fixture', 'relabeled'):
            with self.subTest(mode=mode):
                shutil.rmtree(recovery, ignore_errors=True)
                with patch.dict(os.environ, {'SCOPEHUB_CASE_EVENTS': mode}):
                    code, producer = self.run_harness('--ci-windows-lane', 'recovery')
                expected = int(mode not in passing)
                self.assertEqual(expected, code)
                part = producer['windows_python']['recovery']
                raw_path = self.root / part['log']
                raw = raw_path.read_bytes()
                if mode == 'relabeled':
                    wrong = raw.replace(b'FAIL: test_bad (', b'FAIL: test_good (', 1)
                    self.assertNotEqual(raw, wrong)
                    raw_path.write_bytes(wrong)
                    part['log_sha256'] = harness.file_digest(raw_path)
                    for target in (self.reports / 'test-report.json', raw_path.parent / 'test-report.json'):
                        target.write_bytes(json_bytes(producer))
                    with self.assertRaisesRegex(ci_artifacts.PublicationError, 'unittest'):
                        ci_artifacts.stage_artifacts(self.root, recovery, os.environ, lane=True)
                    shutil.rmtree(self.reports)
                    continue
                try:
                    self.assertTrue(ci_artifacts.stage_artifacts(self.root, recovery, os.environ, lane=True))
                    shutil.rmtree(self.reports)
                    code, aggregate = self.run_harness('--require-windows', '--ci-windows-aggregate',
                                                        str(self.downloads))
                    self.assertEqual(expected, code)
                    self.assertEqual('2.1', aggregate['report_version'])
                    row = next(row for row in aggregate['checks'] if row['id'] == windows_python.CHECK)
                    combined = (self.root / row['log']).read_bytes()
                    self.assertIn(b'test_good (tests.project.test_verification_scope.Second.test_good) ... ok', combined)
                    if mode == 'multiple':
                        self.assertIn(b'FAILED (failures=1, errors=1, skipped=1)', combined)
                        self.assertIn(b'FAIL: test_bad (', combined)
                        self.assertIn(b'ERROR: test_bad (', combined)
                    if mode == 'header':
                        self.assertIn(b'FAIL: second', combined)
                        self.assertIn(b'FAIL: test_bad (', combined)
                    if mode == 'subtest':
                        self.assertIn(b'.test_bad) (step=1) ... FAIL', combined)
                        self.assertIn(b'.test_bad) (step=2) ... skipped', combined)
                        self.assertIn(b'FAILED (failures=1, skipped=2)', combined)
                    if mode == 'fixture':
                        self.assertIn(b'ERROR: setUpClass (tests.project.test_verification_scope.Third)', combined)
                        self.assertIn(b'RuntimeError: fixture error', combined)
                        self.assertNotIn(b'test_fixture (', combined)
                    if not mode:
                        self.assertIn(b'... ok', raw)
                        self.assertIn(b'ordinary unittest shutdown output', raw)
                        self.assertIn(b'\nok', raw.replace(b'\r\n', b'\n'))
                    if mode in {'inline_stdout', 'status_stdout'}:
                        raw_progress = raw.replace(b'\r\n', b'\n')
                        self.assertIn(b'... progressok\n' if mode == 'inline_stdout' else
                                      b'... progress\nERROR\nok\n', raw_progress)
                        self.assertIn(b'test_bad (tests.project.test_verification_scope.Second.test_bad) ... ok', combined)
                        self.assertNotIn(b'progress', combined)
                        self.assertNotIn(b'ERROR', combined)
                    latest = self.downloads / 'published'
                    self.assertTrue(ci_artifacts.stage_artifacts(self.root, latest, os.environ))
                    self.assertEqual(raw, (latest / part['log'].removeprefix('.reports/')).read_bytes())
                    shutil.rmtree(latest)
                finally:
                    shutil.rmtree(self.reports, ignore_errors=True)

    def test_moved_recovery_ui_still_runs_once_in_an_unsplit_task(self):
        task = 'M0-001'
        project = harness.load_project(self.root)
        expected = harness.select_checks(project, task_id=task)
        self.assertTrue(harness.ci_requires_build_setup(project, task))
        code, report = self.run_harness('--task', task)
        self.assertEqual(0, code)
        self.assertEqual({'profile': 'task', 'task_id': task, 'checks': sorted(expected)}, report['selection'])
        moved, = [row for row in report['checks'] if row['id'] == 'market-recovery-ui']
        self.assertEqual('passed', moved['status'])
        self.assertEqual(moved['log_sha256'], harness.file_digest(self.root / moved['log']))
        recovery, = [row for row in report['checks'] if row['id'] == 'market-recovery']
        self.assertEqual('not_selected', recovery['reason_code'])

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

    def test_public_record_requires_both_split_originals_and_preserves_legacy_import(self):
        self.partition_fixture(receipt_scope=True)
        self.produce()
        code, report = self.run_harness('--require-windows', '--ci-windows-aggregate', str(self.downloads))
        self.assertEqual((0, '2.1'), (code, report['report_version']))
        downloaded = self.reports / 'ci' / 'ordinary'
        downloaded.parent.mkdir()
        self.assertTrue(ci_artifacts.stage_artifacts(self.root, downloaded, os.environ))
        self.checkout_report_head(report)
        record = self.record_report

        run = downloaded / 'runs' / report['run_id']
        download_report = downloaded / 'test-report.json'
        originals = {run / f'windows-python-{lane}.{ext}': (run / f'windows-python-{lane}.{ext}').read_bytes()
                     for lane in windows_python.LANES for ext in ('log', 'json', 'producer.json')}
        downgraded = deepcopy(report)
        downgraded['report_version'] = '2.0'
        del downgraded['windows_python']
        try:
            for path in originals:
                path.unlink()
            download_report.write_bytes(json_bytes(downgraded))
            record(download_report, artifact=downloaded, error='Missing or unexpected Windows Python parts')
            downgraded['selection']['profile'] = 'tooling'
            download_report.write_bytes(json_bytes(downgraded))
            record(download_report, artifact=downloaded, error='requires a full native Windows pull request')
            downgraded['ci']['event'] = 'push'
            download_report.write_bytes(json_bytes(downgraded))
            record(download_report, artifact=downloaded, error='event differs from its CI checkout')
            self.assertFalse((self.root / 'project/evidence').exists())
        finally:
            for path, raw in originals.items():
                path.write_bytes(raw)

        changed_attempt = deepcopy(report)
        self.assertEqual(('windows-change-10-2', '2'), (report['run_id'], report['ci']['run_attempt']))
        changed_attempt['ci']['run_attempt'] = '3'
        download_report.write_bytes(json_bytes(changed_attempt))
        for task in ('M0-001', 'M0-004', 'M0-001'):
            record(download_report, artifact=downloaded, task=task, error='differs from its CI run or attempt')
            self.assertFalse((self.root / 'project/evidence').exists())
        download_report.write_bytes(json_bytes(report))

        for path, raw in originals.items():
            with self.subTest(action='missing', original=path.name):
                try:
                    path.unlink()
                    record(download_report, artifact=downloaded, error=path.name)
                    self.assertFalse((self.root / 'project/evidence').exists())
                finally:
                    path.write_bytes(raw)
        for path in (run / 'windows-python-recovery.json', run / 'windows-python-python.log'):
            with self.subTest(action='damaged', original=path.name):
                try:
                    path.write_bytes(b'damaged original')
                    record(download_report, artifact=downloaded, error='recorded digest')
                    self.assertFalse((self.root / 'project/evidence').exists())
                finally:
                    path.write_bytes(originals[path])

        missing = deepcopy(report)
        del missing['windows_python']
        wrong_id = deepcopy(report)
        wrong_id['windows_python']['recovery']['id'] = 'windows-python-python'
        wrong_command = deepcopy(report)
        wrong_command['windows_python']['recovery']['command'][-1] = 'python'
        misplaced = deepcopy(report)
        misplaced['windows_python']['python']['log'] = '.reports/runs/another/windows-python-python.log'
        wrong_combined = deepcopy(report)
        row = next(row for row in wrong_combined['checks'] if row['id'] == windows_python.CHECK)
        row['duration_seconds'] += 0.5
        for changed, error in ((missing, 'Missing or unexpected Windows Python parts'),
                               (wrong_id, 'command or identity'), (wrong_command, 'command or identity'),
                               (misplaced, 'log is misplaced'), (wrong_combined, 'original part results')):
            download_report.write_bytes(json_bytes(changed))
            record(download_report, artifact=downloaded, error=error)
            self.assertFalse((self.root / 'project/evidence').exists())

        recovery_manifest = run / 'windows-python-recovery.json'
        wrong_manifest = deepcopy(report)
        try:
            recovery_manifest.write_bytes(originals[run / 'windows-python-python.json'])
            wrong_manifest['windows_python']['recovery']['manifest_sha256'] = harness.file_digest(recovery_manifest)
            download_report.write_bytes(json_bytes(wrong_manifest))
            record(download_report, artifact=downloaded, error='unexpected, duplicate or reordered tests')
            self.assertFalse((self.root / 'project/evidence').exists())
        finally:
            recovery_manifest.write_bytes(originals[recovery_manifest])
            download_report.write_bytes(json_bytes(report))

        receipt = record(download_report, artifact=downloaded)
        self.assertEqual(harness.file_digest(download_report),
                         read_json(self.root / receipt['path'])['report_sha256'])
        self.assertEqual(receipt, record(download_report, artifact=downloaded))
        with patch.dict(os.environ, {'GITHUB_ACTIONS': 'false'}):
            code, legacy = self.run_harness('--task', 'M0-001')
        self.assertEqual((0, '2.0'), (code, legacy['report_version']))
        local = record(self.reports / 'test-report.json')
        self.assertNotEqual(receipt['path'], local['path'])

    def test_real_package_skip_leaves_submodule_cases_in_the_published_order(self):
        self.partition_fixture('package_skip')
        serial = subprocess.run([sys.executable, *windows_python.COMMAND[1:]], cwd=self.root,
                                capture_output=True, check=True)
        self.assertIn(b'Ran 3 tests', serial.stderr)
        self.assertIn(b'OK (skipped=3)', serial.stderr)
        self.produce()
        code, report = self.run_harness('--require-windows', '--ci-windows-aggregate', str(self.downloads))
        self.assertEqual(0, code)
        row = next(row for row in report['checks'] if row['id'] == windows_python.CHECK)
        combined = (self.root / row['log']).read_bytes()
        self.assertEqual([line for line in serial.stderr.splitlines() if b' ... ' in line],
                         [line for line in combined.splitlines() if b' ... ' in line])
        self.assertTrue(ci_artifacts.stage_artifacts(self.root, self.downloads / 'published', os.environ))
        run = self.root / row['log']
        manifest_path = run.parent / 'windows-python-python.json'
        manifest = read_json(manifest_path)
        case = 'tests.project.test_first.First.test_prefix'
        self.assertEqual(['tests.project.test_first', 'tests.project.test_first.First'], manifest['owners'][case])
        manifest['owners'][case][0] = 'tests.project'
        manifest_path.write_bytes(json_bytes(manifest))
        report['windows_python']['python']['manifest_sha256'] = harness.file_digest(manifest_path)
        for path in (self.reports / 'test-report.json', run.parent / 'test-report.json'):
            path.write_bytes(json_bytes(report))
        with self.assertRaisesRegex(ci_artifacts.PublicationError, 'fixture outcomes'):
            ci_artifacts.stage_artifacts(self.root, self.downloads / 'rejected-owner', os.environ)

    def test_legacy_parts_publish_nested_module_skip_and_package_continuation(self):
        self.partition_fixture('legacy_package_skip', legacy_runner=True)
        serial = subprocess.run([sys.executable, *windows_python.COMMAND[1:]], cwd=self.root,
                                capture_output=True, check=True)
        self.assertIn(b'Ran 3 tests', serial.stderr)
        self.assertIn(b'OK (skipped=4)', serial.stderr)

        with patch.object(windows_python, 'seal_manifest', side_effect=self.seal_legacy_manifest):
            self.produce()
        code, report = self.run_harness('--require-windows', '--ci-windows-aggregate', str(self.downloads))
        self.assertEqual((0, '2.1'), (code, report['report_version']))
        row = next(row for row in report['checks'] if row['id'] == windows_python.CHECK)
        combined = (self.root / row['log']).read_bytes()
        self.assertEqual([line for line in serial.stderr.splitlines() if b' ... ' in line],
                         [line for line in combined.splitlines() if b' ... ' in line])
        staging = Path(self.enterContext(tempfile.TemporaryDirectory()))
        published = staging / 'published-legacy'
        self.assertTrue(ci_artifacts.stage_artifacts(self.root, published, os.environ))
        for lane, part in report['windows_python'].items():
            path = Path(part['log'].removeprefix('.reports/')).with_suffix('.json')
            original = self.downloads / f'verification-windows-2-{lane}' / path
            self.assertEqual(original.read_bytes(), (published / path).read_bytes())

        shutil.rmtree(self.reports)
        producer = self.downloads / 'verification-windows-2-python'
        run = producer / 'runs' / report['run_id']
        producer_manifest = run / 'windows-python-python.json'
        raw = producer_manifest.with_suffix('.log').read_bytes()
        self.assertIn(b'Ran 1 test in 0.000s', raw)
        self.assertIn(b'Ran 2 tests in ', raw)
        report_paths = [producer / 'test-report.json', run / 'test-report.json']
        originals = {path: path.read_bytes() for path in [producer_manifest, *report_paths]}
        original_case = 'tests.project.test_first.First.test_prefix'
        for invented_name, error in ((None, 'run count'), ('test_replaced', 'Original unittest'),
                                     ('test_未执行', 'Original unittest')):
            with self.subTest(invented_name=invented_name):
                manifest = json.loads(originals[producer_manifest])
                if invented_name is None:
                    manifest['cases'] = [case for case in manifest['cases'] if case[0] != original_case]
                    manifest['events'] = [event for event in manifest['events'] if event[0] != original_case]
                else:
                    invented_case = f'tests.project.test_first.First.{invented_name}'
                    manifest['inventory'][manifest['inventory'].index(original_case)] = invented_case
                    display = f'{invented_name} ({invented_case})'
                    next(case for case in manifest['cases'] if case[0] == original_case)[:2] = [invented_case, display]
                    next(event for event in manifest['events'] if event[0] == original_case)[:2] = [invented_case, display]
                producer_manifest.write_bytes(json_bytes(manifest))
                producer_report = json.loads(originals[report_paths[0]])
                producer_report['windows_python']['python']['manifest_sha256'] = harness.file_digest(producer_manifest)
                for path in report_paths:
                    path.write_bytes(json_bytes(producer_report))
                try:
                    for attempt in range(2):
                        rejected = staging / f'rejected-{invented_name}-{attempt}'
                        with self.assertRaisesRegex(ci_artifacts.PublicationError, error):
                            ci_artifacts.stage_artifacts(self.root, rejected, os.environ, reports=producer, lane=True)
                        self.assertFalse((rejected / 'test-report.json').exists())
                    with self.assertRaisesRegex(ci_artifacts.PublicationError, error):
                        self.run_harness('--require-windows', '--ci-windows-aggregate', str(self.downloads))
                    self.assertEqual(raw, producer_manifest.with_suffix('.log').read_bytes())
                finally:
                    for path, contents in originals.items():
                        path.write_bytes(contents)
                    shutil.rmtree(self.reports, ignore_errors=True)

        self.assertTrue(ci_artifacts.stage_artifacts(
            self.root, staging / 'retry-python', os.environ, reports=producer, lane=True))
        code, retried = self.run_harness('--require-windows', '--ci-windows-aggregate', str(self.downloads))
        self.assertEqual((0, 'passed'), (code, retried['status']))
        retry_publication = staging / 'retry-published'
        self.assertTrue(ci_artifacts.stage_artifacts(self.root, retry_publication, os.environ))
        relative_log = row['log'].removeprefix('.reports/')
        self.assertEqual(combined, (retry_publication / relative_log).read_bytes())

    def test_main_push_stages_honest_identity_failures_but_collection_rejects_them(self):
        self.partition_fixture('source_drift')
        original_file = self.root / 'implementation.py'
        original = original_file.read_bytes()
        pull = read_json(self.event)['pull_request']
        after = os.environ['GITHUB_SHA']
        event = {'ref': 'refs/heads/main', 'before': pull['base']['sha'], 'after': after}
        self.event.write_bytes(json_bytes(event))
        with patch.dict(os.environ, {'GITHUB_EVENT_NAME': 'push', 'GITHUB_REF': event['ref'],
                                     'SCOPEHUB_SOURCE_HEAD': after}):
            self.produce()
            uploaded = self.downloads / 'verification-windows-2-python'
            retained = Path(self.enterContext(tempfile.TemporaryDirectory()))
            loaded = harness.load_project(self.root)
            for field in ('source_unchanged', 'project_unchanged'):
                with self.subTest(field=field):
                    try:
                        with ExitStack() as stack:
                            if field == 'project_unchanged':
                                stack.enter_context(patch.object(harness, 'load_project',
                                    side_effect=[loaded, project.ProjectError('observed final project read failure')]))
                            else:
                                stack.enter_context(patch.dict(os.environ, {'SCOPEHUB_FIXTURE_SOURCE_DRIFT': '1'}))
                            code, report = self.run_harness('--ci-windows-lane', 'python')
                    finally:
                        original_file.write_bytes(original)
                    self.assertEqual((1, '2.0', 'failed'), (code, report['report_version'], report['status']))
                    self.assertFalse(report[field])
                    other = 'source_unchanged' if field == 'project_unchanged' else 'project_unchanged'
                    self.assertTrue(report[other])
                    python = next(row for row in report['checks'] if row['id'] == windows_python.CHECK)
                    self.assertEqual(('passed', 0), (python['status'], python['exit_code']))
                    self.assertNotIn('windows_python', report)
                    archived = retained / field
                    self.assertTrue(ci_artifacts.stage_artifacts(self.root, archived, os.environ, lane=True))
                    self.assertEqual((self.reports / 'test-report.json').read_bytes(),
                                     (archived / 'test-report.json').read_bytes())
                    self.assertEqual((self.root / python['log']).read_bytes(),
                                     (archived / python['log'].removeprefix('.reports/')).read_bytes())
                    shutil.rmtree(uploaded)
                    shutil.copytree(archived, uploaded)
                    shutil.rmtree(self.reports)
                    with self.assertRaisesRegex(ci_artifacts.PublicationError, 'another checkout'):
                        self.run_harness('--require-windows', '--ci-windows-aggregate', str(self.downloads))
                    self.assertFalse((self.reports / 'test-report.json').exists())
                    shutil.rmtree(self.reports, ignore_errors=True)

    def test_runner_log_prefix_stays_bound_when_stderr_appends_at_eof(self):
        self.partition_fixture('stderr_during_digest')
        self.produce()
        code, aggregate = self.run_harness('--require-windows', '--ci-windows-aggregate', str(self.downloads))
        self.assertEqual(0, code)
        published = self.downloads / 'published'
        self.assertTrue(ci_artifacts.stage_artifacts(self.root, published, os.environ))
        for lane, part in aggregate['windows_python'].items():
            with self.subTest(lane=lane):
                raw = (self.root / part['log']).read_bytes()
                manifest = read_json(self.root / part['log'].replace('.log', '.json'))
                prefix = raw[:manifest['log_size']]
                self.assertEqual(manifest['log_sha256'], windows_python.sha256(prefix).hexdigest())
                self.assertEqual(b'ordinary stderr during prefix finalization\n', raw[len(prefix):])
                self.assertEqual(raw, (published / part['log'].removeprefix('.reports/')).read_bytes())

    def test_closed_producer_log_retains_ordinary_interpreter_destructor_output(self):
        self.partition_fixture('destructor')
        serial = subprocess.run([sys.executable, *windows_python.COMMAND[1:]], cwd=self.root,
                                capture_output=True, check=True)
        tail = b'ordinary final destructor output'
        self.assertIn(tail, serial.stderr)
        self.produce()
        code, aggregate = self.run_harness('--require-windows', '--ci-windows-aggregate', str(self.downloads))
        self.assertEqual(0, code)
        published = self.downloads / 'published'
        self.assertTrue(ci_artifacts.stage_artifacts(self.root, published, os.environ))
        for lane, part in aggregate['windows_python'].items():
            with self.subTest(lane=lane):
                original = self.root / part['log']
                raw = original.read_bytes()
                manifest = read_json(original.with_suffix('.json'))
                self.assertNotIn(tail, raw[:manifest['log_size']])
                self.assertIn(tail, raw[manifest['log_size']:])
                self.assertEqual(part['log_sha256'], manifest['final_log_sha256'])
                self.assertEqual(raw, (published / part['log'].removeprefix('.reports/')).read_bytes())
                try:
                    original.write_bytes(raw + b'forged shutdown bytes\n')
                    changed = deepcopy(aggregate)
                    changed['windows_python'][lane]['log_sha256'] = harness.file_digest(original)
                    for path in (self.reports / 'test-report.json', original.parent / 'test-report.json'):
                        path.write_bytes(json_bytes(changed))
                    with self.assertRaisesRegex(ci_artifacts.PublicationError, 'original|Original'):
                        ci_artifacts.stage_artifacts(self.root, self.downloads / f'rejected-{lane}', os.environ)
                finally:
                    original.write_bytes(raw)
                    for path in (self.reports / 'test-report.json', original.parent / 'test-report.json'):
                        path.write_bytes(json_bytes(aggregate))

    def test_partitioned_producer_sigint_retains_closed_log_manifest_and_reports(self):
        self.partition_fixture()
        label = windows_python.part_id('python')
        finish_digest, write_bytes = harness.file_digest, Path.write_bytes
        self.addCleanup(signal.signal, signal.SIGINT, signal.signal(signal.SIGINT, signal.default_int_handler))

        for attempt, target in enumerate((f'{label}.log', f'{label}.tmp', f'{label}.json'), 2):
            with self.subTest(target=target):
                signaled = []

                def interrupt(path):
                    if path.name == target and not signaled:
                        signaled.append(path)
                        signal.raise_signal(signal.SIGINT)

                def digest_with_signal(path):
                    digest = finish_digest(path)
                    interrupt(path)
                    return digest

                def write_with_signal(path, raw):
                    written = write_bytes(path, raw)
                    interrupt(path)
                    return written

                with (patch.dict(os.environ, {'GITHUB_RUN_ATTEMPT': str(attempt)}),
                      patch.object(harness, 'file_digest', digest_with_signal),
                      patch.object(Path, 'write_bytes', write_with_signal)):
                    code, report = self.run_harness('--ci-windows-lane', 'python')
                    self.assertEqual((1, 'failed'), (code, report['status']))
                    self.assertEqual(1, len(signaled))
                    part = report['windows_python']['python']
                    self.assertEqual(('failed', 'interrupted', 0),
                                     (part['status'], part['reason_code'], part['exit_code']))
                    raw = self.root / part['log']
                    manifest = raw.with_suffix('.json')
                    self.assertIn(b'OK (skipped=1)', raw.read_bytes())
                    self.assertEqual(part['log_sha256'], finish_digest(raw))
                    self.assertEqual(part['log_sha256'], read_json(manifest)['final_log_sha256'])
                    self.assertEqual(part['manifest_sha256'], finish_digest(manifest))
                    self.assertFalse(manifest.with_suffix('.tmp').exists())
                    latest = self.reports / 'test-report.json'
                    self.assertEqual(latest.read_bytes(), (raw.parent / latest.name).read_bytes())
                    archived = self.downloads / f'interrupted-{attempt}'
                    self.assertTrue(ci_artifacts.stage_artifacts(self.root, archived, os.environ, lane=True))
                    for original in (raw, manifest, raw.parent / latest.name):
                        self.assertEqual(original.read_bytes(), (archived / original.relative_to(self.reports)).read_bytes())

        with patch.dict(os.environ, {'GITHUB_RUN_ATTEMPT': '5'}):
            code, report = self.run_harness('--ci-windows-lane', 'python')
            self.assertEqual((0, 'passed'), (code, report['status']))
            self.assertEqual('passed', report['windows_python']['python']['status'])

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
            (dict(report, checks=list(reversed(report['checks']))), 'catalog order'),
            (dict(report, environment=dict(report['environment'], system='Linux')), 'platform'),
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

    def test_aggregate_rejects_missing_or_duplicate_recovery_ui_assignment(self):
        lanes = self.produce()
        for lane, checks in (('recovery', ['market-recovery']),
                             ('browser', ['market-recovery-ui', 'workspace-timeframe'])):
            with self.subTest(lane=lane):
                original = lanes[lane]
                incoming = self.downloads / f'verification-windows-2-{lane}'
                reports = (incoming / 'test-report.json', incoming / 'runs' / original['run_id'] / 'test-report.json')
                changed = dict(original, selection=dict(original['selection'], checks=checks))
                for path in reports:
                    path.write_bytes(json_bytes(changed))
                try:
                    with self.assertRaisesRegex(ci_artifacts.PublicationError, f'Windows {lane} lane has a different selection'):
                        self.run_harness('--require-windows', '--ci-windows-aggregate', str(self.downloads))
                    self.assertFalse((self.reports / 'test-report.json').exists())
                finally:
                    for path in reports:
                        path.write_bytes(json_bytes(original))
                    shutil.rmtree(self.reports, ignore_errors=True)

    def test_failed_moved_recovery_ui_keeps_its_result_and_attachment(self):
        lanes = self.produce(recovery_ui_exit=7)
        with patch.dict(os.environ, {'SCOPEHUB_WINDOWS_LANE_RESULT': 'failure'}):
            code, report = self.run_harness('--require-windows', '--ci-windows-aggregate', str(self.downloads))
        self.assertEqual(1, code)
        self.assertEqual('failed', report['status'])
        self.assertEqual('blocked', report['integration_gates']['status'])
        self.assertIn('market-recovery-ui', report['integration_gates']['unverified'])
        recovery = {row['id']: row for row in lanes['recovery']['checks']}
        self.assertEqual('passed', recovery['market-recovery']['status'])
        failed = recovery['market-recovery-ui']
        self.assertEqual(7, failed['exit_code'])
        self.assertIn(failed, report['checks'])
        self.assertEqual(failed['log_sha256'], harness.file_digest(self.root / failed['log']))
        ui_report = self.reports / 'runs' / report['run_id'] / self.recovery_ui_report
        self.assertEqual(b'{"synthetic": "recovery UI"}\n', ui_report.read_bytes())

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
