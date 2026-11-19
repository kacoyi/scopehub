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


def worker(data, role='market', extra_args=()):
    env = {key: value for key, value in os.environ.items() if key.upper() in {'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP'}}
    env['PYTHONPATH'] = os.pathsep.join([str(ROOT), str(ROOT / 'python')])
    env['PYTHONIOENCODING'] = 'utf-8'
    with subprocess.Popen([sys.executable, '-m', 'worker_runtime', '--role', role, *extra_args], cwd=ROOT,
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

    def test_old_host_and_negotiated_worker_advertise_only_working_operations(self):
        legacy = worker(b'', extra_args=('--demo',))[1][0]
        self.assertEqual(legacy['protocol_version'], '1.0')
        self.assertEqual(legacy['supported_protocols'], ['1.0'])
        self.assertEqual(set(legacy['capabilities']), {'instrument.search', 'market.getBars'})
        current = worker(b'', extra_args=('--wire-version', '1.1', '--demo'))[1][0]
        self.assertEqual(current['protocol_version'], '1.1')
        self.assertEqual(set(current['capabilities']), {'instrument.search', 'market.getBars', 'market.readHistoryPage'})
        for role in ['ai','indicator']:
            hello = worker(b'', role, ('--wire-version', '1.1'))[1][0]
            self.assertEqual(hello['capabilities'], [])

    def test_task_cancel_is_bound_to_the_owned_read_request(self):
        example = next(x for x in json.loads((ROOT/'tests/fixtures/protocol-cases.json').read_text()) if x['name']=='versioned history page')
        request = json.loads(example['wire'])
        task = '00000000-0000-4000-8000-000000000099'
        request['task_id'] = task
        cancel = dict(BASE, protocol_version='1.1', type='cancel', task_id=task,request_id=request['request_id'])
        def line(message):
            return json.dumps(message).encode() + b'\n'
        code, replies, _ = worker(line(cancel)+line(request)+line(dict(cancel,task_id=REQUEST_ID)), extra_args=('--wire-version','1.1','--demo'))
        self.assertEqual(code, 0)
        self.assertEqual(replies[1]['error']['code'], 'CANCELLED')
        self.assertEqual(replies[1], replies[2])
        self.assertEqual(replies[3]['error']['code'], 'PERMISSION_DENIED')

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
