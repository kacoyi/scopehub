"""Drive the market-recovery check boundary with a small detached process tree."""

from contextlib import ExitStack, redirect_stdout
import errno
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
from threading import Event, Thread
import time
import unittest
from unittest.mock import patch

from scripts import check as harness
from scripts.processes import log_tail


LISTENER = """
import json, os, pathlib, socket, subprocess, sys, time

writer = subprocess.Popen([sys.executable, '-u', '-c',
    'import pathlib, time; print("detached writer started"); '
    'pathlib.Path("writer-ready").touch(); time.sleep(60)'], start_new_session=True)
deadline = time.monotonic() + 5
while not pathlib.Path('writer-ready').exists():
    if time.monotonic() >= deadline:
        raise SystemExit('detached writer did not start')
    time.sleep(.01)
with socket.socket() as server:
    server.bind(('127.0.0.1', 0))
    server.listen()
    pids = [os.getpid(), writer.pid]
    ready = pathlib.Path('ready.json')
    ready.with_suffix('.tmp').write_text(json.dumps({
        'port': server.getsockname()[1], 'pids': pids,
        'groups': [os.getpgid(pid) for pid in pids],
        'command_group': os.getpgid(os.getppid()),
    }))
    ready.with_suffix('.tmp').replace(ready)
    time.sleep(60)
"""

COMMAND = """
import pathlib, subprocess, sys, time

subprocess.Popen([sys.executable, '-c', {listener!r}], start_new_session=True)
deadline = time.monotonic() + 20
while time.monotonic() < deadline:
    done = pathlib.Path('done')
    if done.exists():
        result = done.read_text()
        if result in ('signal', 'group-signal'):
            import os, signal
            if result == 'group-signal':
                os.killpg(os.getpgrp(), signal.SIGTERM)
            else:
                os.kill(os.getpid(), signal.SIGTERM)
        raise SystemExit(7 if result == 'ignored-sigchld' else int(result))
    time.sleep(.01)
raise SystemExit('fixture was never released')
"""


@unittest.skipUnless(sys.platform == 'linux', 'requires Linux process identities')
class MarketRecoveryProcessTests(unittest.TestCase):
    def test_normal_exit_and_both_interrupt_paths_release_the_owned_tree(self):
        root = Path(__file__).resolve().parents[2]
        catalog = json.loads((root / 'project/checks.json').read_text())['checks']
        command = next(check['command'] for check in catalog if check['id'] == 'market-recovery')
        for mode, expected in [('0', (0, None)), ('7', (7, None)),
                               ('signal', (-signal.SIGTERM, None)),
                               ('group-signal', (-signal.SIGTERM, None)),
                               ('ignored-sigchld', (7, None)),
                               ('serial', (None, 'interrupted')),
                               ('overlap', (None, 'interrupted'))]:
            with self.subTest(mode=mode), ExitStack() as resources:
                work = Path(resources.enter_context(tempfile.TemporaryDirectory()))
                # Substitute only the expensive payload. Keep the exact command
                # selector and harness entry used for the real named check.
                shim = work / 'pnpm'
                shim.write_text(f'#!{sys.executable}\n' + COMMAND.format(listener=LISTENER))
                shim.chmod(0o700)
                control = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                                           start_new_session=True)
                resources.callback(self.stop_control, control)
                cancel = Event()
                ready = work / 'ready.json'
                observed = {}
                handles = []

                def release():
                    deadline = time.monotonic() + 10
                    while not ready.exists() and time.monotonic() < deadline:
                        time.sleep(.01)
                    if not ready.exists():
                        return
                    observed.update(json.loads(ready.read_text()))
                    for pid in observed['pids']:
                        handles.append(os.pidfd_open(pid))
                    with socket.socket() as probe:
                        try:
                            probe.bind(('127.0.0.1', observed['port']))
                        except OSError as error:
                            observed['busy_before'] = error.errno
                    if mode == 'serial':
                        os.kill(os.getpid(), signal.SIGINT)
                    elif mode == 'overlap':
                        cancel.set()
                    else:
                        marker = work / 'done.tmp'
                        marker.write_text(mode)
                        marker.replace(work / 'done')

                resources.callback(self.stop_fixture, handles)
                thread = Thread(target=release)
                thread.start()
                resources.callback(thread.join)
                run_dir = work / '.reports'
                with (patch.object(harness, 'ROOT', work),
                      patch('scripts.check.shutil.which', return_value=str(shim)),
                      redirect_stdout(StringIO())):
                    previous_sigchld = signal.getsignal(signal.SIGCHLD)
                    if mode == 'ignored-sigchld':
                        signal.signal(signal.SIGCHLD, signal.SIG_IGN)
                    try:
                        result = harness.execute('market-recovery', command, dict(os.environ), run_dir,
                                                 timeout=15, cancel=cancel.is_set if mode == 'overlap' else None)
                    finally:
                        if mode == 'ignored-sigchld':
                            signal.signal(signal.SIGCHLD, previous_sigchld)

                observed['outcome'] = (result['exit_code'], result.get('reason_code'))
                log = (work / result['log']).read_bytes()
                self.assertIn(b'detached writer started', log)
                self.assertEqual(result['log_sha256'], hashlib.sha256(log).hexdigest())
                self.assertEqual(command, result['command'])
                self.assertEqual(2, len(handles), observed)
                self.assertEqual(3, len(set([*observed['groups'], observed['command_group']])), observed)
                self.assertEqual(errno.EADDRINUSE, observed.get('busy_before'), observed)
                with socket.socket() as rebound:
                    try:
                        rebound.bind(('127.0.0.1', observed['port']))
                        observed['rebound'] = True
                    except OSError as error:
                        observed['rebound'] = error.errno
                observed['reaped'] = [
                    next(line.split()[1] for line in Path(f'/proc/self/fdinfo/{fd}').read_text().splitlines()
                         if line.startswith('Pid:')) == '-1'
                    for fd in handles
                ]
                observed['unrelated_alive'] = control.poll() is None
                print(f'{mode}: {json.dumps(observed)}', flush=True)
                self.assertEqual(expected, observed['outcome'], log_tail(work / result['log']))
                self.assertIs(True, observed['rebound'], observed)
                self.assertEqual([True, True], observed['reaped'], observed)
                self.assertTrue(observed['unrelated_alive'], observed)

    @staticmethod
    def stop_control(control):
        control.kill()
        control.wait(timeout=5)

    @staticmethod
    def stop_fixture(handles):
        for fd in handles:
            try:
                signal.pidfd_send_signal(fd, signal.SIGKILL)
            except ProcessLookupError:
                pass
            finally:
                os.close(fd)
