"""Real subprocess regressions for diagnostics and bounded check lifetimes."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import errno
from io import BytesIO, StringIO, TextIOWrapper
import json
import os
import hashlib
from pathlib import Path
import select
import signal
import socket
import sys
import subprocess
import tempfile
from threading import Event, Thread, get_ident
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import venv

from scripts import check as harness
from scripts import doctor
from scripts.diagnostic_archive import diagnostic_locator, resolve_diagnostic
from scripts.project import json_bytes, read_json
from tests.project.test_continuity import ProjectFixture
from scripts import prepare_workspace_profile_pool, process_tree, processes


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def execute(self, code, timeout=1):
        with patch.object(harness, 'ROOT', self.root), redirect_stdout(StringIO()):
            return harness.execute('fixture', [sys.executable, '-u', '-c', code], dict(os.environ),
                                   self.root / '.reports/runs/fixture', timeout)

    def read_live_pid(self, marker_path):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                marker = marker_path.read_text(encoding='ascii')
            except FileNotFoundError:
                marker = ''
            if marker.isdecimal():
                return int(marker)
            time.sleep(.01)
        self.fail(f'The synthetic process did not report its PID to {marker_path.name}.')

    def windows_job_contains(self, job, handle):
        import ctypes
        from ctypes import wintypes

        in_job = wintypes.BOOL()
        job.require(job.api.IsProcessInJob(handle, job.handle, ctypes.byref(in_job)))
        return bool(in_job.value)

    def test_timeout_preserves_partial_diagnostics(self):
        result = self.execute("import time; print('diagnostic before timeout', flush=True); time.sleep(30)")
        self.assertEqual('failed', result['status'])
        self.assertEqual('timeout', result['reason_code'])
        self.assertIsNone(result['exit_code'])
        path = self.root / result['log']
        log = path.read_bytes()
        self.assertIn(b'diagnostic before timeout', log)
        self.assertIn(b'Check stopped: timeout', log)
        self.assertEqual(result['log_sha256'], hashlib.sha256(log).hexdigest())
        path.unlink()

    @unittest.skipUnless(sys.platform == 'linux', 'requires replacement of an open log inode')
    def test_unconfirmed_log_caps_output_and_marks_a_short_snapshot(self):
        path = self.root / 'check.log'
        with path.open('w+b', buffering=0) as writer:
            writer.write(b'early output ' + b'z' * 16)
            with patch.object(processes, 'FAILED_LOG_LIMIT', 16):
                processes.seal_unconfirmed_log(path)
            writer.write(b'late output')
            self.assertEqual(b'[Log snapshot omitted 13 earlier bytes.]\n' + b'z' * 16, path.read_bytes())

        original_stat = os.fstat
        path.write_bytes(b'available')
        with patch.object(processes.os, 'fstat',
                          side_effect=lambda fd: SimpleNamespace(st_size=original_stat(fd).st_size + 5)):
            processes.seal_unconfirmed_log(path)
        self.assertEqual(b'available\n[Log snapshot ended before the captured extent.]\n', path.read_bytes())

    @unittest.skipUnless(sys.platform == 'linux', 'requires Linux failure-log publication')
    def test_partial_snapshot_write_preserves_cleanup_failure_and_stable_fallback(self):
        create = processes.NamedTemporaryFile
        writer = None

        def unconfirmed(*args, log_path, **kwargs):
            nonlocal writer
            log_path.parent.mkdir(parents=True)
            writer = self.enterContext(log_path.open('wb', buffering=0))
            writer.write(b'Original output without confirmed cleanup.')
            return None, 'cleanup_failed'

        def fail_once(*args, **kwargs):
            staged = create(*args, **kwargs)
            write = staged.write

            def partial(data):
                write(data[:10])
                raise OSError(errno.ENOSPC, 'synthetic snapshot storage limit')

            staged.write = partial
            return staged

        with (patch.object(harness, 'run_logged', unconfirmed),
              patch.object(processes, 'NamedTemporaryFile', side_effect=fail_once) as failed_copy):
            def create_snapshot(*args, **kwargs):
                factory = fail_once if failed_copy.call_count == 1 else create
                return factory(*args, **kwargs)

            failed_copy.side_effect = create_snapshot
            result = self.execute('')
        self.assertEqual(2, failed_copy.call_count)
        self.assertEqual(('failed', 'cleanup_failed'), (result['status'], result.get('reason_code')))
        writer.write(b'late output')
        log = (self.root / result['log']).read_bytes()
        self.assertEqual(b'Process cleanup failed. Original log snapshot unavailable after an I/O error.\n', log)
        self.assertEqual(result['log_sha256'], hashlib.sha256(log).hexdigest())

    @unittest.skipUnless(sys.platform == 'linux', 'requires Linux sessions and child reaping')
    def test_public_browser_check_owns_detached_listener_on_every_exit(self):
        listener = self.root / 'listener.py'
        listener.write_text('''
import json, os, socket, sys, time
from pathlib import Path

ready, stop, reply = sys.argv[1:]
ready, stop = Path(ready), Path(stop)
with socket.socket() as server:
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('127.0.0.1', 0))
    server.listen()
    server.settimeout(.1)
    ready.with_suffix('.tmp').write_text(json.dumps([os.getpid(), server.getsockname()[1]]))
    ready.with_suffix('.tmp').replace(ready)
    deadline = time.monotonic() + 20
    while not stop.exists() and time.monotonic() < deadline:
        try:
            connection, _ = server.accept()
        except TimeoutError:
            continue
        with connection:
            connection.sendall(reply.encode())
''')
        launcher = '''
import subprocess, sys, time
from pathlib import Path

listener, ready, stop, release, mode = sys.argv[1:]
print('fixture launcher started', flush=True)
child = subprocess.Popen([sys.executable, listener, ready, stop, 'owned'],
                         start_new_session=True)
deadline = time.monotonic() + 15
while not Path(release).exists():
    if time.monotonic() >= deadline or child.poll() is not None:
        raise SystemExit(99)
    time.sleep(.01)
raise SystemExit(int(mode))
'''

        def endpoint(ready):
            deadline = time.monotonic() + 3
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(ready.exists(), f'{ready.name} did not start')
            return json.loads(ready.read_bytes())

        def reply(port):
            with (socket.create_connection(('127.0.0.1', port), timeout=2) as client,
                  client.makefile('rb') as response):
                return response.read()

        cases = [('0', ('passed', 0, None)), ('7', ('failed', 7, None)),
                 ('cancelled', ('failed', None, 'interrupted')), ('timed-out', ('failed', None, 'timeout'))]
        for mode, expected in cases:
            with self.subTest(mode=mode), ExitStack() as cleanup:
                work = self.root / mode
                work.mkdir()
                control_ready, control_stop = work / 'control.json', work / 'control-stop'
                control = subprocess.Popen([sys.executable, str(listener), str(control_ready), str(control_stop),
                                            'unrelated'], start_new_session=True)
                cleanup.callback(control.wait, timeout=5)
                cleanup.callback(control_stop.touch)
                _, control_port = endpoint(control_ready)
                self.assertEqual(b'unrelated', reply(control_port))

                ready, stop, release = work / 'owned.json', work / 'owned-stop', work / 'release'
                cancel = Event()
                command = [sys.executable, '-c', launcher, *map(str, (listener, ready, stop, release)), mode]
                cleanup.enter_context(patch.object(harness, 'ROOT', work))
                cleanup.enter_context(redirect_stdout(StringIO()))
                executor = cleanup.enter_context(ThreadPoolExecutor(max_workers=1))
                cleanup.callback(stop.touch)
                check = executor.submit(harness.execute, 'desktop-localization', command, dict(os.environ),
                                        work / '.reports', 4 if mode == 'timed-out' else 10,
                                        cancel=cancel.is_set if mode == 'cancelled' else None)
                pid, port = endpoint(ready)
                identity = os.pidfd_open(pid)
                cleanup.callback(os.close, identity)

                def stop_owned_fixture():
                    stop.touch()
                    poller = select.poll()
                    poller.register(identity, select.POLLIN)
                    self.assertTrue(poller.poll(5000), 'the fixture listener must stop during teardown')

                cleanup.callback(stop_owned_fixture)
                self.assertEqual(pid, os.getsid(pid), 'fixture listener must be in a detached session')
                self.assertEqual(b'owned', reply(port))
                if mode == 'cancelled':
                    cancel.set()
                elif mode != 'timed-out':
                    release.touch()
                record = check.result(timeout=15)
                self.assertEqual(expected, (record['status'], record['exit_code'], record.get('reason_code')))
                log = (work / record['log']).read_bytes()
                self.assertIn(b'fixture launcher started', log)
                self.assertEqual(hashlib.sha256(log).hexdigest(), record['log_sha256'])
                self.assertIsNone(control.poll(), 'the unrelated process must still be running')
                self.assertEqual(b'unrelated', reply(control_port))
                address = ('127.0.0.1', port)
                with socket.socket() as rebound:
                    rebound.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    rebound.settimeout(2)
                    try:
                        rebound.bind(address)
                    except OSError as error:
                        self.fail(f'{mode} left its detached listener live at return: {error}')
                    rebound.listen()
                    with socket.create_connection(address, timeout=2) as client:
                        connection, _ = rebound.accept()
                        with connection:
                            connection.sendall(b'rebound')
                        with client.makefile('rb') as response:
                            self.assertEqual(b'rebound', response.read())
                fdinfo = Path(f'/proc/self/fdinfo/{identity}').read_text().splitlines()
                self.assertEqual('-1', next(line.split()[1] for line in fdinfo if line.startswith('Pid:')))

    @unittest.skipUnless(sys.platform == 'linux', 'requires Linux subreapers and private file locks')
    def test_linux_execute_requires_cleanup_confirmation_and_records_late_interruption(self):
        import fcntl

        shim = self.root / 'supervisor.py'
        shim.write_text(f'''
import os, resource, sys, time
from pathlib import Path

sys.path.insert(0, {str(Path(process_tree.__file__).resolve().parents[1])!r})
from scripts import process_tree

reap = process_tree.reap_linux_children
def controlled_reap():
    Path('reaping').touch()
    deadline = time.monotonic() + 6
    while not Path('finish-reaping').exists():
        if time.monotonic() >= deadline:
            raise RuntimeError('fixture did not release supervisor')
        time.sleep(.01)
    if os.environ['FIXTURE_MODE'] in ('failure', 'ignored-failure'):
        # Only this fixture supervisor loses the ability to open its child list.
        resource.setrlimit(resource.RLIMIT_NOFILE, (3, resource.getrlimit(resource.RLIMIT_NOFILE)[1]))
    reap()
    if os.environ['FIXTURE_MODE'] == 'fallback-sigint':
        Path('reaped').touch()
        time.sleep(15)
    if os.environ['FIXTURE_MODE'] == 'partial-confirmation':
        os.write(int(sys.argv[1]), b'[0, nul')
        os._exit(0)
    if os.environ['FIXTURE_MODE'] == 'invalid-confirmation':
        os.write(int(sys.argv[1]), b'0\\n')
        os._exit(0)

process_tree.reap_linux_children = controlled_reap
process_tree.supervise_linux_check(int(sys.argv[1]), sys.argv[2:])
''')
        worker = '''
import fcntl, os, sys, time
from pathlib import Path

name = sys.argv[1]
with open(name + '.lock', 'w') as handle:
    fcntl.flock(handle, fcntl.LOCK_EX)
    Path(name + '.pid').write_text(str(os.getpid()))
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and not Path(name + '-stop').exists():
        if name == 'owned' and Path('write-after-return').exists() and not Path('did-write').exists():
            os.lseek(1, 0, os.SEEK_END)
            written = os.write(1, b'late bytes through inherited stdout')
            Path('did-write.tmp').write_text(str(written))
            Path('did-write.tmp').replace('did-write')
        time.sleep(.02)
'''
        command = '''
import subprocess, sys, time
from pathlib import Path

subprocess.Popen([sys.executable, '-c', sys.argv[1], 'owned'], start_new_session=True)
deadline = time.monotonic() + 5
while not Path('owned.pid').exists():
    if time.monotonic() >= deadline:
        raise SystemExit(99)
    time.sleep(.01)
print('command completed before cleanup', flush=True)
raise SystemExit(int(sys.argv[2]))
'''

        def lock_is_free(path):
            with path.open('r+b') as handle:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return False
                return True

        def wait_for_exit(fd):
            readable = select.poll()
            readable.register(fd, select.POLLIN)
            self.assertTrue(readable.poll(5000), 'the private fixture must exit')

        driver_thread = get_ident()
        cases = [
            ('failure', ('failed', None, 'cleanup_failed')),
            ('ignored-failure', ('failed', None, 'cleanup_failed')),
            ('ignored-nonzero', ('failed', 23, None)),
            ('partial-confirmation', ('failed', None, 'cleanup_failed')),
            ('invalid-confirmation', ('failed', None, 'cleanup_failed')),
            ('fallback-sigint', ('failed', None, 'cleanup_failed')),
            ('late-sigint', ('failed', None, 'interrupted')),
            ('late-cancel', ('failed', None, 'interrupted')),
        ]
        for mode, expected in cases:
            with self.subTest(mode=mode), ExitStack() as cleanup:
                work = self.root / mode
                work.mkdir()
                control = subprocess.Popen([sys.executable, '-c', worker, 'unrelated'], cwd=work)
                cleanup.callback(control.wait, timeout=5)
                cleanup.callback((work / 'unrelated-stop').touch)
                self.read_live_pid(work / 'unrelated.pid')
                self.assertFalse(lock_is_free(work / 'unrelated.lock'))
                cleanup.callback((work / 'owned-stop').touch)

                cancel = Event()
                owned_handles = []

                def finish_fixture():
                    (work / 'owned-stop').touch()
                    for fd in owned_handles:
                        try:
                            wait_for_exit(fd)
                        finally:
                            os.close(fd)

                def release_reaping():
                    try:
                        deadline = time.monotonic() + 5
                        while not (work / 'reaping').exists():
                            self.assertLess(time.monotonic(), deadline, 'supervisor did not begin reaping')
                            time.sleep(.01)
                        pid = self.read_live_pid(work / 'owned.pid')
                        owned_handles.append(os.pidfd_open(pid))
                        self.assertEqual(pid, os.getsid(pid))
                        self.assertFalse(lock_is_free(work / 'owned.lock'))
                        if mode == 'late-sigint':
                            while True:
                                frame = sys._current_frames()[driver_thread]
                                while frame is not None and frame.f_code is not subprocess.Popen._wait.__code__:
                                    frame = frame.f_back
                                if frame is not None:
                                    break
                                self.assertLess(time.monotonic(), deadline, 'caller did not enter final wait')
                                time.sleep(.01)
                            os.kill(os.getpid(), signal.SIGINT)
                        elif mode == 'late-cancel':
                            cancel.set()
                    finally:
                        (work / 'finish-reaping').touch()

                stop_tree = processes.stop_tree
                kill = subprocess.Popen.kill
                failed_supervisor = None
                fallback_interrupted = False

                def stop_for_fixture(process, *args, **kwargs):
                    nonlocal failed_supervisor
                    if mode != 'fallback-sigint':
                        return stop_tree(process, *args, **kwargs)
                    deadline = time.monotonic() + 5
                    while not (work / 'reaped').exists():
                        self.assertLess(time.monotonic(), deadline, 'fixture did not finish child reaping')
                        time.sleep(.01)
                    process.stdin.close()
                    failed_supervisor = process
                    raise subprocess.TimeoutExpired(process.args, 0)

                def interrupt_fallback(process):
                    nonlocal fallback_interrupted
                    kill(process)
                    if process is failed_supervisor and not fallback_interrupted:
                        fallback_interrupted = True
                        signal.raise_signal(signal.SIGINT)

                cleanup.callback(finish_fixture)
                controller = cleanup.enter_context(ThreadPoolExecutor(max_workers=1))
                ready = controller.submit(release_reaping)
                digest_log = harness.file_digest

                def digest_with_late_sigint(path):
                    if mode == 'failure':
                        signal.raise_signal(signal.SIGINT)
                    return digest_log(path)

                previous_interrupt = signal.signal(signal.SIGINT, signal.default_int_handler)
                child_disposition = signal.SIG_IGN if mode.startswith('ignored-') else signal.SIG_DFL
                previous_child = signal.signal(signal.SIGCHLD, child_disposition)
                try:
                    with (patch.object(harness, 'ROOT', work),
                          patch.object(process_tree, '__file__', str(shim)),
                          patch.object(processes, 'stop_tree', stop_for_fixture),
                          patch.object(subprocess.Popen, 'kill', interrupt_fallback),
                          patch.object(harness, 'file_digest', digest_with_late_sigint),
                          redirect_stdout(StringIO())):
                        result = harness.execute(
                            'desktop-localization', [sys.executable, '-c', command, worker,
                                                     '23' if mode == 'ignored-nonzero' else '0'],
                            dict(os.environ, FIXTURE_MODE=mode), work / '.reports', 8,
                            cancel=cancel.is_set if mode == 'late-cancel' else None,
                        )
                except KeyboardInterrupt:
                    self.fail('late SIGINT escaped without a check record')
                finally:
                    signal.signal(signal.SIGCHLD, previous_child)
                    signal.signal(signal.SIGINT, previous_interrupt)
                ready.result(timeout=6)
                self.assertIsNone(control.poll())
                self.assertFalse(lock_is_free(work / 'unrelated.lock'))
                self.assertEqual(expected, (result['status'], result['exit_code'], result.get('reason_code')))
                log = (work / result['log']).read_bytes()
                self.assertIn(b'command completed before cleanup', log)
                self.assertEqual(hashlib.sha256(log).hexdigest(), result['log_sha256'])
                if mode.endswith('failure'):
                    self.assertIn(f'[Errno {errno.EMFILE}]'.encode('ascii'), log)
                    self.assertFalse(lock_is_free(work / 'owned.lock'), 'this failure must leave cleanup unconfirmed')
                    if mode == 'failure':
                        self.assertEqual('Interrupted while finalizing check diagnostics.', result.get('reason'))
                    sealed = work / result['log']
                    (work / 'write-after-return').touch()
                    self.assertEqual(len(b'late bytes through inherited stdout'), self.read_live_pid(work / 'did-write'))
                    with patch.object(harness, 'ROOT', work), redirect_stdout(StringIO()):
                        retry = harness.execute('desktop-localization', [sys.executable, '-c', 'print("verified retry")'],
                                                dict(os.environ), work / '.reports/retry', 8)
                    self.assertEqual('passed', retry['status'])
                    self.assertNotEqual(result['log'], retry['log'])
                    self.assertEqual(b'verified retry\n', (work / retry['log']).read_bytes())
                    self.assertEqual(log, sealed.read_bytes(), 'an inherited writer changed the finished log')
                    self.assertEqual(result['log_sha256'], hashlib.sha256(sealed.read_bytes()).hexdigest())
                else:
                    if mode == 'partial-confirmation':
                        self.assertIn(b'did not confirm cleanup', log)
                    if mode == 'invalid-confirmation':
                        self.assertIn(b'Invalid Linux check cleanup confirmation', log)
                    if mode == 'fallback-sigint':
                        self.assertTrue(fallback_interrupted)
                        self.assertIn(b'Process cleanup failed:', log)
                    if mode.startswith('late-'):
                        self.assertIn(b'Check stopped: interrupted.', log)
                    self.assertTrue(lock_is_free(work / 'owned.lock'))
                    self.assertEqual(1, len(owned_handles))
                    fdinfo = Path(f'/proc/self/fdinfo/{owned_handles[0]}').read_text().splitlines()
                    self.assertEqual('-1', next(line.split()[1] for line in fdinfo if line.startswith('Pid:')))

    @unittest.skipUnless(sys.platform in {'linux', 'win32'}, 'requires owned process tree verification')
    def test_workspace_profile_pool_waits_for_grandchild_before_removal(self):
        pool = self.root / 'pool'
        marker = self.root / 'descendant.pid'
        release = self.root / 'release'
        grandchild = '''
from pathlib import Path
import os, sys, time
pool, marker, release = map(Path, sys.argv[1:])
marker.write_text(str(os.getpid()), encoding='ascii')
deadline = time.monotonic() + 30
while time.monotonic() < deadline and not release.exists():
    time.sleep(.01)
if release.exists():
    pool.mkdir(exist_ok=True)
    (pool / 'late').write_text('late write', encoding='ascii')
'''
        bridge = '''
import subprocess, sys, time
subprocess.Popen([sys.executable, '-I', '-S', '-c', *sys.argv[1:]],
                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(30)
'''
        observed = []
        original_wait = processes._wait_for_check
        original_remove = prepare_workspace_profile_pool.shutil.rmtree

        def watch_then_timeout(process, timeout, cancel):
            pid = self.read_live_pid(marker)
            if sys.platform == 'win32':
                import _winapi
                handle = _winapi.OpenProcess(_winapi.SYNCHRONIZE, False, pid)
                self.addCleanup(_winapi.CloseHandle, handle)
                self.assertEqual(_winapi.WAIT_TIMEOUT, _winapi.WaitForSingleObject(handle, 0))
                stopped = lambda: _winapi.WaitForSingleObject(handle, 0) == _winapi.WAIT_OBJECT_0
            else:
                status = Path(f'/proc/{pid}/stat')

                def stopped():
                    try:
                        return status.read_bytes().rsplit(b')', 1)[1].split()[0] in {b'Z', b'X'}
                    except FileNotFoundError:
                        return True

                self.assertFalse(stopped())
            observed.append(stopped)
            return original_wait(process, .1, None)

        def verify_before_removal(path, *args, **kwargs):
            self.assertEqual(pool, path)
            self.assertEqual(1, len(observed))
            self.assertTrue(observed[0](), 'an owned descendant was still live when removing the pool')
            release.write_text('write only if still alive', encoding='ascii')
            return original_remove(path, *args, **kwargs)

        diagnostic = StringIO()
        with (patch.object(processes, '_wait_for_check', watch_then_timeout),
              patch.object(prepare_workspace_profile_pool.shutil, 'rmtree', verify_before_removal),
              redirect_stderr(diagnostic)):
            status = prepare_workspace_profile_pool.prepare(
                [sys._base_executable, '-I', '-S', '-c', bridge, grandchild, str(pool), str(marker), str(release)],
                pool, 12,
            )
        self.assertEqual(124, status, diagnostic.getvalue())
        self.assertTrue(release.exists())
        self.assertFalse(pool.exists())
        self.assertIn('Check stopped: timeout', diagnostic.getvalue())

    def test_workspace_profile_supervisor_refuses_start_when_grant_is_absent_or_expired(self):
        path = Path(prepare_workspace_profile_pool.__file__)
        for grant, deadline, expected in [(b'', 12_000, 125), (b'1', -1, 124)]:
            with self.subTest(grant=grant, deadline=deadline):
                pool = self.root / 'ungranted'
                result = subprocess.run(
                    [sys._base_executable, '-B', str(path), str(self.root / 'unused-bridge'), str(pool), '2',
                     str(time.time_ns() // 1_000_000 + deadline)],
                    input=grant, capture_output=True, timeout=10, check=False,
                )
                self.assertEqual(expected, result.returncode, result.stderr)
                self.assertEqual('scopehub-workspace-profile-ready', result.stdout.decode().strip())
                self.assertFalse(pool.exists())

    def test_workspace_profile_pool_retains_root_if_cleanup_cannot_be_confirmed(self):
        pool = self.root / 'pool'
        diagnostic = StringIO()

        def unconfirmed(*args, log_path, **kwargs):
            log_path.write_text('original refusal\nProcess cleanup failed\n', encoding='utf-8')
            return 31, 'cleanup_failed'

        with patch.object(prepare_workspace_profile_pool, 'run_logged', unconfirmed), redirect_stderr(diagnostic):
            self.assertEqual(31, prepare_workspace_profile_pool.prepare(['fixture'], pool, 12))
        self.assertTrue(pool.is_dir())
        self.assertIn('original refusal', diagnostic.getvalue())
        self.assertIn('process exit was not confirmed', diagnostic.getvalue())

    @unittest.skipUnless(sys.platform == 'win32', 'requires the Windows venv redirector and Job Objects')
    def test_windows_gated_launcher_is_the_assigned_process(self):
        from scripts.windows_job import WindowsJob

        environment = self.root / 'venv'
        venv.EnvBuilder().create(environment)
        redirector = str(environment / 'Scripts' / 'python.exe')
        real_interpreter = sys._base_executable
        ready_path = self.root / 'launcher.pid'
        original_assign = WindowsJob.assign

        def assign_after_gate_starts(job, pid):
            gate_pid = self.read_live_pid(ready_path)
            original_assign(job, pid)
            # PROCESS_QUERY_LIMITED_INFORMATION suffices for IsProcessInJob.
            handle = job.require(job.api.OpenProcess(0x1000, False, gate_pid))
            try:
                gate_in_job = self.windows_job_contains(job, handle)
            finally:
                job.require(job.api.CloseHandle(handle))
            observed.append((pid, gate_pid, gate_in_job))

        launcher = (
            'from pathlib import Path\nimport os\n'
            f'Path({str(ready_path)!r}).write_text(str(os.getpid()), encoding="ascii")\n'
            + processes.WINDOWS_LAUNCHER
        )
        for name, interpreter in [('redirector control', redirector), ('normal gate', real_interpreter)]:
            with self.subTest(launcher=name):
                ready_path.unlink(missing_ok=True)
                observed = []
                configured = SimpleNamespace(**(vars(sys) | {
                    'executable': redirector, '_base_executable': interpreter,
                }))
                with (patch.object(processes, 'sys', configured),
                      patch.object(processes, 'WINDOWS_LAUNCHER', launcher),
                      patch.object(WindowsJob, 'assign', assign_after_gate_starts)):
                    result = self.execute("print('check ran after the gate')", timeout=10)
                path = self.root / result['log']
                self.assertEqual('passed', result['status'], processes.log_tail(path))
                self.assertEqual(1, len(observed))
                assigned_pid, gate_pid, gate_in_job = observed[0]
                print(f'{name}: assigned PID {assigned_pid}, gated PID {gate_pid}, in job: {gate_in_job}', flush=True)
                if name == 'redirector control':
                    self.assertNotEqual(assigned_pid, gate_pid)
                    self.assertFalse(gate_in_job)
                else:
                    self.assertEqual(assigned_pid, gate_pid)
                    self.assertTrue(gate_in_job)
                self.assertIn(b'check ran after the gate', path.read_bytes())
                path.unlink()

    @unittest.skipUnless(sys.platform == 'win32', 'requires native Windows process and file handles')
    def test_windows_timeout_releases_the_owned_log_writer_before_return(self):
        import _winapi

        from scripts.windows_job import WindowsJob

        marker = self.root / 'log-writer.pid'
        writer = (
            'from pathlib import Path\nimport os, time\n'
            "print('owned log writer ready', flush=True)\n"
            f'Path({str(marker)!r}).write_text(str(os.getpid()), encoding="ascii")\n'
            'time.sleep(60)\n'
        )
        observed = {}
        original_wait = processes._wait_for_check
        original_stop = WindowsJob.stop
        writer_handle = None
        timeout_started = None

        def close_writer(handle):
            try:
                if _winapi.WaitForSingleObject(handle, 0) == _winapi.WAIT_TIMEOUT:
                    try:
                        _winapi.TerminateProcess(handle, 1)
                    except OSError as error:
                        # TerminateProcess reports access denied if this exact process already exited.
                        if (error.winerror != _winapi.ERROR_ACCESS_DENIED
                                or _winapi.WaitForSingleObject(handle, 0) != _winapi.WAIT_OBJECT_0):
                            raise
                    self.assertEqual(_winapi.WAIT_OBJECT_0, _winapi.WaitForSingleObject(handle, 10_000))
            finally:
                _winapi.CloseHandle(handle)

        def wait_after_writer_starts(process, timeout, cancel):
            nonlocal writer_handle, timeout_started
            pid = self.read_live_pid(marker)
            # Keep the original process identity for the probe and fixture cleanup.
            writer_handle = _winapi.OpenProcess(_winapi.SYNCHRONIZE | 0x1000 | 0x0001, False, pid)
            self.addCleanup(close_writer, writer_handle)
            observed['writer_pid'] = pid
            self.assertEqual(_winapi.WAIT_TIMEOUT, _winapi.WaitForSingleObject(writer_handle, 0))
            timeout_started = time.monotonic()
            return original_wait(process, timeout, cancel)

        def observe_job_stop(job):
            try:
                if writer_handle is not None:
                    observed['writer_in_job_before_stop'] = self.windows_job_contains(job, writer_handle)
                    observed['writer_wait_before_job_stop'] = _winapi.WaitForSingleObject(writer_handle, 0)
            finally:
                original_stop(job)
            if writer_handle is not None:
                observed['writer_wait_after_job_stop'] = _winapi.WaitForSingleObject(writer_handle, 0)

        def observe_return(argv, *, log_path, **options):
            result = processes.run_logged(argv, log_path=log_path, **options)
            if timeout_started is not None:
                observed['seconds_from_wait'] = round(time.monotonic() - timeout_started, 3)
                observed['writer_wait_status'] = _winapi.WaitForSingleObject(writer_handle, 0)
                try:
                    # Probe DELETE access without changing the bytes before the harness hashes them.
                    handle = _winapi.CreateFile(str(log_path), 0x10000, 0x7, 0, _winapi.OPEN_EXISTING, 0, 0)
                except OSError as error:
                    observed['delete_access_error'] = error.winerror
                else:
                    _winapi.CloseHandle(handle)
                    observed['delete_access_error'] = None
            return result

        with (patch.object(processes, '_wait_for_check', wait_after_writer_starts),
              patch.object(WindowsJob, 'stop', observe_job_stop),
              patch.object(harness, 'run_logged', observe_return)):
            result = self.execute(writer, timeout=.25)
        path = self.root / result['log']
        print(f'Windows timeout observation: {json.dumps(observed)}', flush=True)
        self.assertEqual('timeout', result.get('reason_code'), processes.log_tail(path))
        self.assertTrue(observed['writer_in_job_before_stop'], observed)
        self.assertEqual(_winapi.WAIT_TIMEOUT, observed['writer_wait_before_job_stop'], observed)
        self.assertEqual(_winapi.WAIT_OBJECT_0, observed['writer_wait_after_job_stop'], observed)
        self.assertEqual(_winapi.WAIT_OBJECT_0, observed['writer_wait_status'], observed)
        self.assertIsNone(observed['delete_access_error'], observed)
        self.assertLess(observed['seconds_from_wait'], 25, observed)
        log = path.read_bytes()
        self.assertIn(b'owned log writer ready', log)
        self.assertIn(b'Check stopped: timeout', log)
        self.assertEqual(result['log_sha256'], hashlib.sha256(log).hexdigest())
        path.unlink()

    @unittest.skipUnless(sys.platform == 'win32', 'requires Windows file sharing')
    def test_windows_reader_outside_job_can_block_log_deletion(self):
        result = self.execute("print('synthetic external reader control')")
        self.assertEqual('passed', result['status'], result)
        path = self.root / result['log']
        with path.open('rb') as external_reader:
            self.assertEqual(result['log_sha256'], hashlib.sha256(external_reader.read()).hexdigest())
            with self.assertRaises(PermissionError) as failure:
                path.unlink()
            self.assertEqual(32, failure.exception.winerror)
        path.unlink()

    def test_unicode_failure_on_legacy_console_preserves_log_and_result(self):
        raw = 'Synthetic chart failure \u2502 \u903e\u671f'.encode()
        script = self.root / 'unicode_probe.py'
        script.write_text(f'import os\nos.write(2, {raw!r})\nraise SystemExit(3)\n', encoding='ascii')
        console = BytesIO()
        stream = TextIOWrapper(console, encoding='cp1252', errors='strict', write_through=True)
        with patch.object(harness, 'ROOT', self.root), redirect_stdout(stream):
            result = harness.execute('fixture', [sys.executable, str(script)], dict(os.environ),
                                     self.root / '.reports/runs/fixture')
        self.assertEqual(('failed', 3), (result['status'], result['exit_code']))
        self.assertEqual(raw, (self.root / result['log']).read_bytes())
        self.assertEqual(hashlib.sha256(raw).hexdigest(), result['log_sha256'])
        self.assertEqual(raw.decode(), json.loads(console.getvalue().decode('cp1252').splitlines()[-1])['log_tail'])

    def test_private_checks_preserve_exit_evidence_without_capturing_either_output_stream(self):
        child = self.root / 'probe.py'
        child.write_text("import sys\nprint('synthetic private message')\n"
                         "print('synthetic private error', file=sys.stderr)\nsys.exit(int(sys.argv[1]))\n")
        log_dir = self.root / '.reports/runs/fixture'
        for name, code in [('configured-model-session', 0), ('configured-voice-session', 7),
                           ('physical-windows-workspace', 77), ('configured-external-ai-clients', 0)]:
            printed = StringIO()
            with self.subTest(check=name, code=code), patch.object(harness, 'ROOT', self.root), redirect_stdout(printed):
                result = harness.execute(name, [sys.executable, str(child), str(code)], dict(os.environ), log_dir)
                self.assertEqual(code, result['exit_code'])
                self.assertNotIn('synthetic private', printed.getvalue())
                self.assertEqual(f'{{"exit_code": {code}}}\n'.encode('ascii'), (self.root / result['log']).read_bytes())

    def descendant_command(self, *, exit_code=0, wait=False):
        ready = self.root / 'descendant-ready'
        escaped = self.root / 'escaped-descendant'
        child = (f"from pathlib import Path; import time; Path({str(ready)!r}).touch(); "
                 f"time.sleep(1.2); Path({str(escaped)!r}).touch(); print('late descendant output', flush=True)")
        parent = (f"import subprocess, sys, time; from pathlib import Path; "
                  f"subprocess.Popen([sys.executable, '-c', {child!r}]); "
                  f"deadline=time.monotonic()+5\n"
                  f"while not Path({str(ready)!r}).exists() and time.monotonic()<deadline: time.sleep(.005)\n"
                  + ('time.sleep(30)\n' if wait else f'raise SystemExit({exit_code})\n'))
        return parent, ready, escaped

    def test_normal_and_nonzero_exit_stop_descendants_before_log_digest(self):
        for exit_code in [0, 7]:
            with self.subTest(exit_code=exit_code):
                parent, ready, escaped = self.descendant_command(exit_code=exit_code)
                ready.unlink(missing_ok=True)
                escaped.unlink(missing_ok=True)
                result = self.execute(parent, timeout=10)
                self.assertEqual(exit_code, result['exit_code'])
                self.assertTrue(ready.exists(), 'The child must start before the root exits.')
                time.sleep(1.4)
                self.assertFalse(escaped.exists(), 'A child survived the root exit.')
                self.assertEqual(result['log_sha256'], hashlib.sha256((self.root / result['log']).read_bytes()).hexdigest())

    def test_interruption_stops_descendants_and_preserves_reason(self):
        parent, ready, escaped = self.descendant_command(wait=True)
        def interrupt_wait(*args, **kwargs):
            deadline = time.monotonic() + 5
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(.005)
            raise KeyboardInterrupt

        with patch.object(processes, '_wait_for_check', interrupt_wait):
            result = self.execute(parent, timeout=10)
        self.assertEqual('interrupted', result['reason_code'])
        self.assertTrue(ready.exists())
        time.sleep(1.4)
        self.assertFalse(escaped.exists())

    def test_cancellation_from_another_check_stops_descendants(self):
        parent, ready, escaped = self.descendant_command(wait=True)
        cancel = Event()

        def request_cancellation():
            deadline = time.monotonic() + 5
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(.005)
            cancel.set()

        thread = Thread(target=request_cancellation)
        thread.start()
        self.addCleanup(thread.join)
        log = self.root / 'cancelled.log'
        code, reason = processes.run_logged([sys.executable, '-c', parent], cwd=self.root, env=dict(os.environ),
                                            log_path=log, timeout=10, cancel=cancel.is_set)
        self.assertTrue(ready.exists())
        self.assertEqual((None, 'interrupted'), (code, reason))
        self.assertIn('Check stopped: interrupted.', log.read_text())
        time.sleep(1.4)
        self.assertFalse(escaped.exists())

    def test_cleanup_failure_cannot_become_a_pass(self):
        stop_tree = processes.stop_tree

        def unconfirmed(*args, **kwargs):
            stop_tree(*args, **kwargs)
            raise OSError('cleanup fixture failure')

        with patch.object(processes, 'stop_tree', side_effect=unconfirmed):
            result = self.execute("print('root completed')")
        self.assertEqual('failed', result['status'])
        self.assertEqual('cleanup_failed', result['reason_code'])

    def test_timeout_stops_a_descendant_before_it_can_write(self):
        ready = self.root / 'descendant-ready'
        escaped = self.root / 'escaped-descendant'
        child = f"from pathlib import Path; import time; Path({str(ready)!r}).touch(); time.sleep(2); Path({str(escaped)!r}).touch()"
        parent = f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(30)"
        result = self.execute(parent)
        self.assertEqual('timeout', result['reason_code'])
        self.assertTrue(ready.exists(), 'The descendant must have started for this test to establish cleanup.')
        time.sleep(1.3)
        self.assertFalse(escaped.exists(), 'The descendant survived the check timeout.')

    def test_failed_launch_retains_a_log_instead_of_crashing(self):
        with patch('scripts.processes.subprocess.Popen', side_effect=OSError('fixture launch failure')):
            result = self.execute("print('unreachable')")
        self.assertEqual('failed', result['status'])
        self.assertEqual('launch_error', result['reason_code'])
        self.assertIn('fixture launch failure', (self.root / result['log']).read_text())

    def test_version_probe_failure_is_a_diagnostic(self):
        with patch.object(doctor.subprocess, 'run', side_effect=OSError('fixture unavailable')):
            self.assertEqual('unavailable', doctor.tool_version(sys.executable))

    def test_doctor_rejects_the_vulnerable_uv_executable(self):
        tool_version = doctor.tool_version

        def stale_uv(tool, root):
            return 'uv 0.12.24' if tool == 'uv' else tool_version(tool, root)

        with patch.object(doctor, 'tool_version', side_effect=stale_uv):
            result = doctor.diagnose()
        self.assertEqual('failed', result['status'])
        self.assertTrue(any(error.startswith('uv: expected ') and error.endswith(', observed uv 0.12.24')
                            for error in result['errors']))


class LogFinalizationTests(ProjectFixture):
    @unittest.skipUnless(sys.platform == 'linux', 'requires standard serial POSIX SIGINT delivery')
    def test_serial_sigint_while_digesting_keeps_terminal_report_and_retry(self):
        catalog_path = self.root / 'project/checks.json'
        catalog = read_json(catalog_path)
        by_id = {row['id']: row for row in catalog['checks']}
        by_id['project-state']['command'] = [
            '{python}', '-c', 'import sys; sys.stdout.buffer.write(b"x" * (16 * 1024 * 1024))',
        ]
        by_id['rust-lints']['command'] = ['{python}', '-c', 'print("selected later check")']
        catalog_path.write_bytes(json_bytes(catalog))
        subprocess.run(['git', 'add', '.'], cwd=self.root, check=True, capture_output=True)
        subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.org',
                        'commit', '-m', 'Disposable log finalization controls'],
                       cwd=self.root, check=True, capture_output=True)
        reports = self.root / '.reports'
        interrupt_pending = True
        sent = False
        original_digest = harness.file_digest
        default_handler = signal.signal(signal.SIGINT, signal.default_int_handler)
        self.addCleanup(signal.signal, signal.SIGINT, default_handler)

        def digest_with_test_signal(path):
            if path.name != 'project-state.log' or not interrupt_pending:
                return original_digest(path)

            class LogReader:
                def __init__(self, stream):
                    self.stream = stream

                def readable(self):
                    return True

                def readinto(self, buffer):
                    nonlocal sent
                    count = self.stream.readinto(buffer)
                    if count and not sent:
                        sent = True
                        signal.raise_signal(signal.SIGINT)
                    return count

            self.assertEqual(16 * 1024 * 1024, path.stat().st_size)
            with path.open('rb') as stream:
                return hashlib.file_digest(LogReader(stream), 'sha256').hexdigest()

        def run():
            console = StringIO()
            with (patch.object(harness, 'ROOT', self.root), patch.object(harness, 'REPORTS', reports),
                  patch.object(harness, 'tool_version', return_value='fixture'),
                  patch.object(harness, 'maintain_automatically'),
                  patch.object(harness, 'file_digest', digest_with_test_signal),
                  patch.object(sys, 'argv', ['check.py', '--task', 'M0-001']),
                  redirect_stdout(console)):
                try:
                    with self.assertRaises(SystemExit) as terminal:
                        harness.main()
                except KeyboardInterrupt:
                    self.fail(f'SIGINT escaped serial finalization: {console.getvalue()}')
            return terminal.exception.code, [json.loads(line) for line in console.getvalue().splitlines()]

        code, transcript = run()
        self.assertEqual(1, code)
        self.assertTrue(sent)
        failed_latest = (reports / 'test-report.json').read_bytes()
        failed_report = json.loads(failed_latest)
        rows = {row['id']: row for row in failed_report['checks']}
        interrupted = rows['project-state']
        self.assertEqual(('failed', 'interrupted'), (interrupted['status'], interrupted.get('reason_code')))
        self.assertEqual('earlier_failure', rows['rust-lints']['reason_code'])
        self.assertEqual([interrupted], [row for row in transcript if row.get('id') == 'project-state' and 'log_sha256' in row])
        self.assertEqual(1, sum(row.get('id') == 'project-state' and row.get('status') == 'running' for row in transcript))

        failed_path = reports / 'runs' / failed_report['run_id'] / 'test-report.json'
        self.assertEqual(failed_latest, failed_path.read_bytes())
        interrupted_log = self.root / interrupted['log']
        self.assertEqual(interrupted['log_sha256'], original_digest(interrupted_log))
        interrupt_pending = False
        retry_code, _ = run()
        self.assertEqual(0, retry_code)
        retry = read_json(reports / 'test-report.json')
        self.assertNotEqual(failed_report['run_id'], retry['run_id'])
        self.assertEqual(failed_latest, failed_path.read_bytes())
        self.assertEqual(interrupted['log_sha256'], original_digest(interrupted_log))
        resolved = resolve_diagnostic(diagnostic_locator(failed_path), reports)
        self.assertEqual('failed', resolved['recorded_status'])
        self.assertEqual('failed', resolved['selected_check_statuses']['project-state'])
        self.assertIn('project-state', resolved['verified_logs'])
        retry_path = reports / 'runs' / retry['run_id'] / 'test-report.json'
        verified_retry = resolve_diagnostic(diagnostic_locator(retry_path), reports)
        self.assertEqual('passed', verified_retry['recorded_status'])
        self.assertEqual(resolved['source_commit'], verified_retry['source_commit'])


class TerminalPublicationTests(ProjectFixture):
    @unittest.skipUnless(sys.platform == 'linux', 'requires an owned Linux stdout pipe and POSIX SIGINT')
    def test_single_sigint_releases_blocked_terminal_and_preserves_report(self):
        import fcntl

        reports = self.root / '.reports'
        reports.mkdir()
        catalog_path = self.root / 'project/checks.json'
        catalog = read_json(catalog_path)
        by_id = {row['id']: row for row in catalog['checks']}
        by_id['project-state']['command'] = [
            '{python}', '-c', 'import os, sys, time; print("fixture command output", flush=True); '
            'time.sleep(15) if os.environ.get("FIXTURE_EXIT") == "timeout" else None; '
            'sys.exit(int(os.environ.get("FIXTURE_EXIT", "0")))',
        ]
        by_id['project-state']['timeout_seconds'] = 1
        by_id['rust-lints']['command'] = ['{python}', '-c', 'print("selected later check")']
        catalog['profiles']['fast'] = ['environment', 'project-state', 'rust-lints']
        catalog_path.write_bytes(json_bytes(catalog))
        driver = self.root / 'fixture-driver.py'
        driver.write_text(f'''
import os, signal, sys, time
from pathlib import Path

sys.path.insert(0, {str(Path(harness.__file__).resolve().parents[1])!r})
from scripts import check, processes

check.ROOT = Path.cwd()
check.REPORTS = check.ROOT / '.reports'
signal.signal(signal.SIGINT, signal.default_int_handler)
emit = check.print_check
stop_tree = processes.stop_tree

def stop_fixture(process, *args, **kwargs):
    result = stop_tree(process, *args, **kwargs)
    if os.environ.get('FIXTURE_CLEANUP_FAILED') and process.args[-1].endswith('int(os.environ.get("FIXTURE_EXIT", "0")))'):
        raise OSError('fixture could not confirm cleanup')
    return result

def gated_emit(record):
    stage = os.environ.get('FIXTURE_BLOCK')
    target = record.get('id') == 'project-state'
    at_stage = (stage == 'terminal' and target and 'log_sha256' in record
                or stage == 'tail' and target and 'log_tail' in record
                or stage == 'summary' and 'report' in record
                or stage == 'recovery' and 'failed_report' in record
                or stage == 'running' and target and record.get('status') == 'running')
    if at_stage:
        (check.REPORTS / 'terminal-ready').touch()
        deadline = time.monotonic() + 8
        while not (check.REPORTS / 'write-now').exists():
            if time.monotonic() >= deadline:
                raise AssertionError('fixture did not release console gate')
            time.sleep(.01)
    return emit(record)

check.print_check = gated_emit
processes.stop_tree = stop_fixture
check.main()
''', encoding='utf-8')
        subprocess.run(['git', 'add', '.'], cwd=self.root, check=True, capture_output=True)
        subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.org',
                        'commit', '-m', 'Disposable terminal publication controls'],
                       cwd=self.root, check=True, capture_output=True)
        latest = reports / 'test-report.json'
        ready = reports / 'terminal-ready'
        gate = reports / 'write-now'

        def wait_until(predicate, message):
            deadline = time.monotonic() + 6
            while not predicate():
                self.assertLess(time.monotonic(), deadline, message)
                time.sleep(.01)

        def healthy(argv):
            outcome = subprocess.run(argv, cwd=self.root, capture_output=True, timeout=12, check=False)
            self.assertEqual(0, outcome.returncode, outcome.stderr)
            rows = [json.loads(line) for line in outcome.stdout.splitlines()]
            self.assertIn('project-state', {row['id'] for row in rows if row.get('status') == 'passed' and 'id' in row})
            self.assertIn('report', rows[-1])
            return read_json(latest)

        def original(report):
            return reports / 'runs' / report['run_id'] / 'test-report.json'

        lock = reports / 'unrelated.lock'
        stopped = reports / 'unrelated-stop'
        unrelated = subprocess.Popen([sys.executable, '-c', '''
import fcntl, time
from pathlib import Path
with open('.reports/unrelated.lock', 'w') as handle:
    fcntl.flock(handle, fcntl.LOCK_EX)
    Path('.reports/unrelated-ready').touch()
    deadline = time.monotonic() + 30
    while not Path('.reports/unrelated-stop').exists() and time.monotonic() < deadline:
        time.sleep(.02)
'''], cwd=self.root)
        self.addCleanup(unrelated.wait, timeout=5)
        self.addCleanup(stopped.touch)
        wait_until((reports / 'unrelated-ready').exists, 'the independent fixture did not acquire its lock')

        cases = [
            (('--task', 'M0-001'), '0', 'terminal', False, 'interrupted', 0),
            (('--profile', 'fast'), '23', 'tail', False, 'interrupted', 23),
            (('--task', 'M0-001'), 'timeout', 'terminal', False, 'interrupted', None),
            (('--profile', 'fast'), '0', 'terminal', True, 'cleanup_failed', None),
            (('--task', 'M0-001'), '0', 'summary', False, None, 0),
            (('--profile', 'fast'), '23', 'recovery', False, None, 23),
            (('--task', 'M0-001'), '0', 'running', False, 'interrupted', None),
        ]
        for entry, outcome, stage, cleanup_failed, expected, exit_code in cases:
            with self.subTest(entry=entry, outcome=outcome, stage=stage, cleanup_failed=cleanup_failed):
                ready.unlink(missing_ok=True)
                gate.unlink(missing_ok=True)
                argv = [sys.executable, '-B', str(driver), *entry]
                prior = healthy(argv)
                original_prior = original(prior).read_bytes()
                self.assertEqual(original_prior, latest.read_bytes())
                before = {p.name for p in (reports / 'runs').iterdir()}
                read_fd, write_fd = os.pipe()
                env = dict(os.environ, FIXTURE_EXIT=outcome, FIXTURE_BLOCK=stage)
                if cleanup_failed:
                    env['FIXTURE_CLEANUP_FAILED'] = '1'
                try:
                    capacity = fcntl.fcntl(write_fd, fcntl.F_SETPIPE_SZ, 4096)
                    with subprocess.Popen(argv, cwd=self.root, env=env, stdout=write_fd,
                                          stderr=subprocess.PIPE, start_new_session=True) as caller:
                        try:
                            wait_until(ready.exists, 'completed check did not reach terminal console output')
                            os.set_blocking(read_fd, False)
                            transcript = os.read(read_fd, capacity)
                            self.assertIn(b'"status": "running"', transcript)
                            if stage != 'running':
                                self.assertIn(b'"id": "project-state", "status": "running"', transcript)
                            self.assertEqual(capacity, os.write(write_fd, b'F' * capacity))
                            gate.touch()
                            wait_until(lambda: any(name in Path(f'/proc/{caller.pid}/wchan').read_text()
                                                   for name in ('pipe_write', 'poll', 'wait_for_space')),
                                       'caller did not block in its owned console')
                            if stage in ('summary', 'recovery'):
                                published = latest.read_bytes()
                                self.assertNotEqual(original_prior, published)
                                self.assertEqual(published, original(json.loads(published)).read_bytes())
                            caller.send_signal(signal.SIGINT)
                            try:
                                finished = caller.wait(timeout=4)
                            except subprocess.TimeoutExpired:
                                new_id, = {p.name for p in (reports / 'runs').iterdir()} - before
                                self.fail(f'SIGINT did not release the console. '
                                          f'Original report present: {(reports / "runs" / new_id / "test-report.json").exists()}. '
                                          f'Latest unchanged: {latest.read_bytes() == original_prior}.')
                            self.assertEqual(1, finished, 'one SIGINT must finish without draining stdout')
                            self.assertEqual(b'F' * capacity, os.read(read_fd, capacity))
                        finally:
                            if caller.poll() is None:
                                caller.kill()
                            caller.wait(timeout=5)
                finally:
                    os.close(read_fd)
                    os.close(write_fd)

                self.assertEqual(1, len({p.name for p in (reports / 'runs').iterdir()} - before))
                failed_bytes = latest.read_bytes()
                failed = json.loads(failed_bytes)
                self.assertNotEqual(prior['run_id'], failed['run_id'])
                status = 'passed' if stage == 'summary' else 'failed'
                self.assertEqual(status, failed['status'])
                self.assertEqual(failed_bytes, original(failed).read_bytes())
                if stage in ('summary', 'recovery'):
                    self.assertEqual(published, failed_bytes)
                rows = {row['id']: row for row in failed['checks']}
                row = rows['project-state']
                self.assertEqual((status, expected, exit_code), (row['status'], row.get('reason_code'), row['exit_code']))
                if stage == 'summary':
                    self.assertEqual('passed', rows['rust-lints']['status'])
                else:
                    self.assertEqual('earlier_failure', rows['rust-lints']['reason_code'])
                log = (self.root / row['log']).read_bytes()
                self.assertEqual(hashlib.sha256(log).hexdigest(), row['log_sha256'])
                if outcome == 'timeout':
                    self.assertIn(b'Check stopped: timeout.', log)
                if stage == 'running':
                    self.assertIn(b'Check stopped: interrupted.', log)
                    self.assertNotIn(b'fixture command output', log)
                if cleanup_failed:
                    self.assertIn(b'Process cleanup failed:', log)
                with lock.open('rb') as handle:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.assertIsNone(unrelated.poll())
                retry = healthy(argv)
                self.assertNotIn(retry['run_id'], (prior['run_id'], failed['run_id']))
                self.assertEqual(original_prior, original(prior).read_bytes())
                self.assertEqual(failed_bytes, original(failed).read_bytes())
                for result, status in ((failed, status), (retry, 'passed')):
                    verified = resolve_diagnostic(diagnostic_locator(original(result)), reports)
                    self.assertEqual(status, verified['recorded_status'])
                    self.assertIn('project-state', verified['verified_logs'])


class ParallelPipePublicationTests(ProjectFixture):
    @unittest.skipUnless(sys.platform == 'linux', 'requires an owned Linux pipe and cooperative SIGINT')
    def test_competing_pipe_writer_cannot_block_parallel_report(self):
        import fcntl

        reports = self.root / '.reports'
        reports.mkdir()
        latest = reports / 'test-report.json'
        catalog_path = self.root / 'project/checks.json'
        catalog = read_json(catalog_path)
        template = catalog['checks'][2]
        catalog['checks'][2:2] = [
            {**template, 'id': 'python-contracts-workers'},
            {**template, 'id': 'ui-build', 'command': ['{python}', '-c',
             'import os, sys; print("parallel child already completed"); sys.exit(int(os.getenv("FIXTURE_EXIT", "0")))']},
        ]
        catalog['required_change_checks'].extend(('python-contracts-workers', 'ui-build'))
        # This is a portable stub, not a native Windows check.
        next(row for row in catalog['checks'] if row['id'] == 'windows-native-build')['platforms'] = 'all'
        catalog_path.write_bytes(json_bytes(catalog))
        driver = self.root / 'parallel-driver.py'
        driver.write_text('import sys\nsys.path.insert(0, '
                          + repr(str(Path(harness.__file__).resolve().parents[1])) + ')\n' + '''
import os, select, signal, threading
from pathlib import Path
from scripts import check, processes

check.ROOT = Path.cwd()
check.REPORTS = check.ROOT / '.reports'
signal.signal(signal.SIGINT, signal.default_int_handler)
order = os.getenv('FIXTURE_ORDER')
notify = int(os.getenv('FIXTURE_NOTIFY', '-1'))
release = int(os.getenv('FIXTURE_RELEASE', '-1'))
emit = check.print_check
write_line = check.CheckConsole.write_line
real_poll = select.poll
real_write = os.write
stop_tree = processes.stop_tree
watched_thread = None
first_poll = True
first_write = True
stdout_stat = os.fstat(1)

def watching():
    return watched_thread == threading.get_ident()

def exchange(marker):
    real_write(notify, marker)
    if not select.select([release], [], [], 8)[0] or os.read(release, 1) != b'G':
        raise RuntimeError('fixture did not release the output synchronization point')

def observed_emit(record):
    global watched_thread
    if record.get('id') != 'ui-build' or 'log_sha256' not in record:
        return emit(record)
    watched_thread = threading.get_ident()
    try:
        return emit(record)
    finally:
        watched_thread = None

def observed_line(console, line):
    if watching() and order:
        exchange(b'T')
    return write_line(console, line)

class ObservedPoll:
    def __init__(self):
        self.poller = real_poll()

    def register(self, *args):
        self.poller.register(*args)

    def poll(self, timeout):
        global first_poll
        ready = self.poller.poll(timeout)
        if watching() and order and first_poll:
            first_poll = False
            if order == 'before':
                assert not ready
                real_write(notify, b'P')
            else:
                assert ready
                exchange(b'R')
        return ready

def observed_write(fd, data):
    global first_write
    if watching() and first_write and os.path.samestat(os.fstat(fd), stdout_stat):
        assert os.get_blocking(1), 'the caller stdout flags changed'
        if fd != 1:
            assert not os.get_blocking(fd) and not os.get_inheritable(fd)
        first_write = False
        if order == 'after':
            real_write(notify, b'W')
            try:
                return real_write(fd, data)
            except BlockingIOError:
                real_write(notify, b'E')
                raise
        if not order:
            written = real_write(fd, data[:11])
            Path('.reports/short-write').write_text(str(written))
            return written
    return real_write(fd, data)

def observed_stop(process, *args, **kwargs):
    outcome = stop_tree(process, *args, **kwargs)
    if os.getenv('FIXTURE_CLEANUP_FAILED') and 'parallel child already completed' in process.args[-1]:
        raise OSError('fixture cannot confirm cleanup')
    return outcome

check.print_check = observed_emit
check.CheckConsole.write_line = observed_line
select.poll = ObservedPoll
os.write = observed_write
processes.stop_tree = observed_stop
check.main()
''', encoding='utf-8')
        subprocess.run(['git', 'add', '.'], cwd=self.root, check=True, capture_output=True)
        git = ['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.org']
        subprocess.run([*git, 'commit', '-m', 'Disposable parallel pipe checks'],
                       cwd=self.root, check=True, capture_output=True)
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=self.root, text=True).strip()
        env = dict(os.environ, GITHUB_ACTIONS='true', GITHUB_REPOSITORY='fixture/scopehub',
                   GITHUB_RUN_ID='42', GITHUB_RUN_ATTEMPT='1', GITHUB_EVENT_NAME='push',
                   SCOPEHUB_SOURCE_HEAD=commit, SCOPEHUB_STORAGE='off')
        for name in ('CARGO_TARGET_DIR', 'CARGO_BUILD_TARGET_DIR', 'CARGO_BUILD_BUILD_DIR',
                     'SCOPEHUB_REVIEW_BASE', 'SCOPEHUB_CHECKPOINT_BASE'):
            env.pop(name, None)
        argv = [sys.executable, '-B', str(driver)]

        def original(report):
            return reports / 'runs' / report['run_id'] / 'test-report.json'

        def healthy():
            mode = reports / 'short-write'
            mode.unlink(missing_ok=True)
            result = subprocess.run(argv, cwd=self.root, env=env, capture_output=True, timeout=15, check=False)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual('11', mode.read_text())
            emitted = [json.loads(line) for line in result.stdout.splitlines()]
            report = read_json(latest)
            self.assertEqual('passed', report['status'])
            rows = [row for row in report['checks'] if row['id'] == 'ui-build']
            self.assertEqual(rows, [row for row in emitted if row.get('id') == 'ui-build' and 'log_sha256' in row])
            self.assertEqual('passed', rows[0]['status'])
            self.assertEqual(report['run_id'], Path(emitted[-1]['report']).parent.name)
            return report

        def read_marker(fd, marker):
            self.assertTrue(select.select([fd], [], [], 8)[0], f'fixture did not reach {marker!r}')
            self.assertEqual(marker, os.read(fd, 1))

        def close_fixture(process):
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)

        for order, cleanup_failed in (('before', False), ('after', False), ('after', True)):
            with self.subTest(order=order, cleanup_failed=cleanup_failed), ExitStack() as owned:
                prior = healthy()
                prior_bytes = original(prior).read_bytes()
                prior_originals = set((reports / 'runs').glob('*/test-report.json'))
                self.assertEqual(prior_bytes, latest.read_bytes())

                def pipe():
                    pair = os.pipe()
                    for fd in pair:
                        owned.callback(os.close, fd)
                    return pair

                read_fd, write_fd = pipe()
                notice_read, notice_write = pipe()
                continue_read, continue_write = pipe()
                capacity = fcntl.fcntl(write_fd, fcntl.F_SETPIPE_SZ, 4096)
                original_flags = fcntl.fcntl(write_fd, fcntl.F_GETFL)
                race = dict(env, FIXTURE_ORDER=order, FIXTURE_NOTIFY=str(notice_write),
                            FIXTURE_RELEASE=str(continue_read), FIXTURE_EXIT='23')
                if cleanup_failed:
                    race['FIXTURE_CLEANUP_FAILED'] = '1'
                caller = owned.enter_context(subprocess.Popen(argv, cwd=self.root, env=race, stdout=write_fd,
                                             stderr=subprocess.PIPE, pass_fds=(notice_write, continue_read),
                                             start_new_session=True))
                owned.callback(close_fixture, caller)
                transcript = bytearray()
                deadline = time.monotonic() + 8
                while True:
                    ready, _, _ = select.select([read_fd, notice_read], [], [],
                                                 max(0, deadline - time.monotonic()))
                    self.assertTrue(ready, 'fixture did not reach its terminal write')
                    if read_fd in ready:
                        transcript.extend(os.read(read_fd, capacity))
                    if notice_read in ready:
                        read_marker(notice_read, b'T')
                        break
                # The emitting thread now holds the console lock at its gate.
                while select.select([read_fd], [], [], 0)[0]:
                    transcript.extend(os.read(read_fd, capacity))
                self.assertIn(b'"id": "ui-build", "status": "running"', transcript)
                if order == 'after':
                    os.write(continue_write, b'G')
                    read_marker(notice_read, b'R')

                # The competing writer is a separate process and retains a
                # foreign lock until explicitly released by this fixture.
                lock = reports / 'foreign.lock'
                foreign = owned.enter_context(subprocess.Popen([sys.executable, '-c', '''
import fcntl, os, sys
with open(sys.argv[1], 'w') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    assert os.write(1, b'F' * int(sys.argv[2])) == int(sys.argv[2])
    os.write(int(sys.argv[3]), b'C')
    os.read(0, 1)
''', str(lock), str(capacity), str(notice_write)], stdin=subprocess.PIPE, stdout=write_fd,
                    pass_fds=(notice_write,), start_new_session=True))
                owned.callback(close_fixture, foreign)
                read_marker(notice_read, b'C')
                os.write(continue_write, b'G')
                nonblocking_write_observed = False
                if order == 'before':
                    read_marker(notice_read, b'P')
                else:
                    read_marker(notice_read, b'W')
                    deadline = time.monotonic() + 5
                    while True:
                        if select.select([notice_read], [], [], .01)[0]:
                            read_marker(notice_read, b'E')
                            nonblocking_write_observed = True
                            break
                        if 'pipe_write' in Path(f'/proc/{caller.pid}/wchan').read_text():
                            break  # The red control can enter a real blocking syscall here.
                        self.assertLess(time.monotonic(), deadline, 'actual console write was not observed')

                self.assertEqual(original_flags, fcntl.fcntl(write_fd, fcntl.F_GETFL))
                caller.send_signal(signal.SIGINT)
                try:
                    exit_code = caller.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    self.assertEqual(prior_bytes, latest.read_bytes())
                    self.assertEqual(prior_originals, set((reports / 'runs').glob('*/test-report.json')))
                    self.fail('one SIGINT left the parallel write blocked; no original report or new latest')
                self.assertEqual(1, exit_code, caller.stderr.read())
                if order == 'after':
                    self.assertTrue(nonblocking_write_observed, 'the full pipe must return EAGAIN from the actual write')
                self.assertEqual(original_flags, fcntl.fcntl(write_fd, fcntl.F_GETFL))
                self.assertEqual(b'F' * capacity, os.read(read_fd, capacity), 'the pipe stayed full until exit')
                self.assertIsNone(foreign.poll())
                with lock.open('rb') as handle, self.assertRaises(BlockingIOError):
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                foreign.stdin.close()
                self.assertEqual(0, foreign.wait(timeout=5))

                interrupted_bytes = latest.read_bytes()
                interrupted = json.loads(interrupted_bytes)
                self.assertNotEqual(prior['run_id'], interrupted['run_id'])
                self.assertEqual('failed', interrupted['status'])
                self.assertEqual(interrupted_bytes, original(interrupted).read_bytes())
                row = next(row for row in interrupted['checks'] if row['id'] == 'ui-build')
                self.assertEqual(('failed', 'cleanup_failed' if cleanup_failed else 'interrupted',
                                  None if cleanup_failed else 23),
                                 (row['status'], row.get('reason_code'), row['exit_code']))
                log = (self.root / row['log']).read_bytes()
                self.assertIn(b'parallel child already completed', log)
                self.assertEqual(row['log_sha256'], hashlib.sha256(log).hexdigest())
                retry = healthy()
                self.assertEqual(prior_bytes, original(prior).read_bytes())
                self.assertEqual(interrupted_bytes, original(interrupted).read_bytes())
                self.assertEqual(3, len({prior['run_id'], interrupted['run_id'], retry['run_id']}))
                for report, status in ((prior, 'passed'), (interrupted, 'failed'), (retry, 'passed')):
                    verified = resolve_diagnostic(diagnostic_locator(original(report)), reports)
                    self.assertEqual(status, verified['recorded_status'])
                    self.assertIn('ui-build', verified['verified_logs'])
