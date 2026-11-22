"""Real subprocess regressions for diagnostics and bounded check lifetimes."""

from contextlib import redirect_stdout
from io import BytesIO, StringIO, TextIOWrapper
import json
import os
import hashlib
from pathlib import Path
import sys
import subprocess
import tempfile
from threading import Event, Thread
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import venv

from scripts import check as harness
from scripts import doctor
from scripts import processes


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def execute(self, code, timeout=1):
        with patch.object(harness, 'ROOT', self.root), redirect_stdout(StringIO()):
            return harness.execute('fixture', [sys.executable, '-u', '-c', code], dict(os.environ),
                                   self.root / '.reports/runs/fixture', timeout)

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

    @unittest.skipUnless(sys.platform == 'win32', 'requires the Windows venv redirector and Job Objects')
    def test_windows_gated_launcher_is_the_assigned_process(self):
        import ctypes
        from ctypes import wintypes

        from scripts.windows_job import WindowsJob

        environment = self.root / 'venv'
        venv.EnvBuilder().create(environment)
        redirector = str(environment / 'Scripts' / 'python.exe')
        real_interpreter = sys._base_executable
        ready_path = self.root / 'launcher.pid'
        original_assign = WindowsJob.assign

        def assign_after_gate_starts(job, pid):
            deadline = time.monotonic() + 10
            while True:
                try:
                    marker = ready_path.read_text(encoding='ascii')
                except FileNotFoundError:
                    marker = ''
                if marker.isdecimal():
                    break
                if time.monotonic() >= deadline:
                    self.fail('The gated interpreter did not report its PID before job assignment.')
                time.sleep(.01)
            gate_pid = int(marker)
            original_assign(job, pid)
            gate_in_job = wintypes.BOOL()
            is_process_in_job = job.api.IsProcessInJob
            is_process_in_job.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
            is_process_in_job.restype = wintypes.BOOL
            # PROCESS_QUERY_LIMITED_INFORMATION suffices for IsProcessInJob.
            handle = job.require(job.api.OpenProcess(0x1000, False, gate_pid))
            try:
                job.require(is_process_in_job(handle, job.handle, ctypes.byref(gate_in_job)))
            finally:
                job.require(job.api.CloseHandle(handle))
            observed.append((pid, gate_pid, bool(gate_in_job.value)))

        launcher = (
            'from pathlib import Path\nimport os\n'
            f'Path({str(ready_path)!r}).write_text(str(os.getpid()), encoding="ascii")\n'
            + processes.WINDOWS_LAUNCHER
        )
        for name, interpreter in [('redirector control', redirector), ('normal gate', real_interpreter)]:
            with self.subTest(launcher=name):
                ready_path.unlink(missing_ok=True)
                observed = []
                configured = SimpleNamespace(executable=redirector, _base_executable=interpreter)
                with (patch.object(processes, 'sys', configured),
                      patch.object(processes, 'WINDOWS_LAUNCHER', launcher),
                      patch.object(WindowsJob, 'assign', assign_after_gate_starts)):
                    result = self.execute("print('check ran after the gate')", timeout=10)
                self.assertEqual('passed', result['status'], result)
                self.assertEqual(1, len(observed))
                assigned_pid, gate_pid, gate_in_job = observed[0]
                print(f'{name}: assigned PID {assigned_pid}, gated PID {gate_pid}, in job: {gate_in_job}', flush=True)
                if name == 'redirector control':
                    self.assertNotEqual(assigned_pid, gate_pid)
                    self.assertFalse(gate_in_job)
                else:
                    self.assertEqual(assigned_pid, gate_pid)
                    self.assertTrue(gate_in_job)
                path = self.root / result['log']
                self.assertIn(b'check ran after the gate', path.read_bytes())
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
        original_wait = subprocess.Popen.wait
        interrupted = False

        def wait(process, *args, **kwargs):
            nonlocal interrupted
            if not interrupted:
                deadline = time.monotonic() + 5
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(.005)
                interrupted = True
                raise KeyboardInterrupt
            return original_wait(process, *args, **kwargs)

        with patch.object(subprocess.Popen, 'wait', wait):
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
        with patch.object(processes, 'stop_tree', side_effect=OSError('cleanup fixture failure')):
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
