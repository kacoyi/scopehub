"""Real subprocess regressions for diagnostics and bounded check lifetimes."""

from contextlib import redirect_stdout
from io import StringIO
import os
import hashlib
from pathlib import Path
import sys
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

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
        log = (self.root / result['log']).read_text()
        self.assertIn('diagnostic before timeout', log)
        self.assertIn('Check stopped: timeout', log)

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
