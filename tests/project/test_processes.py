"""Real subprocess regressions for diagnostics and bounded check lifetimes."""

from contextlib import redirect_stdout
from io import StringIO
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from scripts import check as harness
from scripts import doctor


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
