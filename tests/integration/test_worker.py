import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from worker_runtime.protocol import MAX_MESSAGE_BYTES, decode_line

ROOT = Path(__file__).resolve().parents[2]
BASE = {'protocol_version': '1.0', 'message_id': '00000000-0000-4000-8000-000000000001', 'timestamp_ms': 1700000000000}
REQUEST_ID = '00000000-0000-4000-8000-000000000002'


def frame(**payload):
    return (json.dumps({**BASE, **payload}) + '\n').encode()


def worker(data, role='market'):
    env = {key: value for key, value in os.environ.items() if key.upper() in {'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP'}}
    env['PYTHONPATH'] = os.pathsep.join([str(ROOT), str(ROOT / 'python')])
    env['PYTHONIOENCODING'] = 'utf-8'
    with subprocess.Popen([sys.executable, '-m', 'worker_runtime', '--role', role], cwd=ROOT,
                          env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        try:
            stdout, stderr = process.communicate(data, timeout=10)
        except BaseException:
            process.kill()
            process.communicate()
            raise
        return process.returncode, [decode_line(line) for line in stdout.splitlines()], stderr


class WorkerTests(unittest.TestCase):
    def test_each_role_handshakes_and_closes_on_eof(self):
        for role in ['market', 'ai', 'indicator']:
            with self.subTest(role=role):
                code, messages, stderr = worker(b'', role)
                self.assertEqual((code, stderr), (0, b''))
                self.assertEqual(len(messages), 1)
                self.assertEqual(messages[0]['role'], role)
                self.assertEqual(messages[0]['capabilities'], [])

    def test_heartbeat(self):
        code, messages, _ = worker(frame(type='heartbeat'))
        self.assertEqual(code, 0)
        self.assertEqual([m['type'] for m in messages], ['hello', 'heartbeat'])

    def test_unsupported_request_and_duplicate_are_terminal(self):
        request = frame(type='request', request_id=REQUEST_ID, task_id=None,
                        operation='instrument.search', payload={'query': 'SIM', 'limit': 10})
        code, messages, _ = worker(request + request + frame(type='cancel', request_id=REQUEST_ID))
        self.assertEqual(code, 0)
        self.assertEqual(messages[1]['error']['code'], 'UNSUPPORTED_CAPABILITY')
        self.assertEqual(messages[1], messages[2])
        self.assertEqual(messages[1], messages[3])

    def test_cancel_before_request_keeps_cancelled_outcome(self):
        cancel = frame(type='cancel', request_id=REQUEST_ID)
        request = frame(type='request', request_id=REQUEST_ID, task_id=None,
                        operation='instrument.search', payload={'query': 'SIM', 'limit': 10})
        code, messages, _ = worker(cancel + request)
        self.assertEqual(code, 0)
        self.assertEqual(messages[1]['error']['code'], 'CANCELLED')
        self.assertEqual(messages[1], messages[2])

    def test_invalid_or_wrong_direction_frames_exit_without_echoing_input(self):
        data = [b'{"secret":"NEVER_ECHO_THIS"}\n', b'\xff\n', b'x' * (MAX_MESSAGE_BYTES + 1),
                frame(type='heartbeat').rstrip(b'\n'),
                frame(type='hello', role='ai', worker_version='0.1.0', supported_protocols=['1.0'], capabilities=[])]
        for value in data:
            with self.subTest(prefix=value[:30]):
                code, messages, stderr = worker(value)
                self.assertEqual(code, 2)
                self.assertEqual(len(messages), 1)
                self.assertNotIn(b'NEVER_ECHO_THIS', stderr)
                self.assertEqual(json.loads(stderr)['code'], 'INVALID_PROTOCOL_MESSAGE')


if __name__ == '__main__':
    unittest.main()
