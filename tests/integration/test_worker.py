import json
import os
from pathlib import Path
from queue import Queue
import subprocess
import sys
from threading import Thread
from time import monotonic, sleep
import unittest

from worker_runtime.protocol import MAX_MESSAGE_BYTES, decode_line
from providers.history_pages import execute_page
from providers.replay import plan_replay
from providers.simulated import GapObservation

ROOT = Path(__file__).resolve().parents[2]
BASE = {'protocol_version': '1.0', 'message_id': '00000000-0000-4000-8000-000000000001', 'timestamp_ms': 1700000000000}
REQUEST_ID = '00000000-0000-4000-8000-000000000002'


def frame(**payload):
    return (json.dumps({**BASE, **payload}) + '\n').encode()


def worker(data, role='market', extra_args=(), *, python_args=(), interleave_replay=False):
    env = {key: value for key, value in os.environ.items() if key.upper() in {'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP'}}
    env['PYTHONPATH'] = os.pathsep.join([str(ROOT), str(ROOT / 'python')])
    env['PYTHONIOENCODING'] = 'utf-8'
    with subprocess.Popen([sys.executable, *python_args, '-m', 'worker_runtime', '--role', role, *extra_args], cwd=ROOT,
                          env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        if interleave_replay:
            replies = interleave_then_cancel(process, data)
            return process.returncode, replies, process.stderr.read()
        try:
            stdout, stderr = process.communicate(data, timeout=10)
        except BaseException:
            process.kill()
            process.communicate()
            raise
        return process.returncode, [decode_line(line) for line in stdout.splitlines()], stderr


def interleave_then_cancel(process, data):
    """Wait for one real publication, then cancel its subscription."""
    incoming = Queue()
    replies = []
    deadline = monotonic() + 10
    requests = data.splitlines(keepends=True)
    prefix, cancel, legacy = requests[:-2], requests[-2], requests[-1]
    request_id = json.loads(cancel)['request_id']

    def read_stdout():
        for line in process.stdout:
            incoming.put(line)
        incoming.put(None)

    def send(*lines):
        process.stdin.write(b''.join(lines))
        process.stdin.flush()

    def receive():
        line = incoming.get(timeout=max(0, deadline - monotonic()))
        if line is None:
            return None
        frame = decode_line(line.rstrip(b'\r\n'))
        replies.append(frame)
        return frame

    def until(predicate):
        while (frame := receive()) is not None:
            if predicate(frame):
                return
        raise AssertionError('Worker exited before the expected replay milestone')

    reader = Thread(target=read_stdout, daemon=True)
    reader.start()
    try:
        send(*prefix)
        until(lambda frame: frame.get('request_id') == request_id
              and (frame.get('result') or {}).get('status') == 'accepted')
        until(lambda frame: frame['type'] == 'event')
        send(cancel)
        until(lambda frame: frame.get('request_id') == request_id)
        # Leave the transport open through another publication interval.
        sleep(0.7)
        send(legacy)
        process.stdin.close()
        while receive() is not None:
            pass
        process.wait(timeout=max(0, deadline - monotonic()))
        return replies
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        reader.join(timeout=10)


class WorkerTests(unittest.TestCase):
    def test_worker_hello_does_not_load_unused_iri_grammar(self):
        code, messages, imports = worker(b'', extra_args=('--demo',), python_args=('-X', 'importtime'))
        self.assertEqual(code, 0)
        self.assertEqual([message['type'] for message in messages], ['hello'])
        imported_modules = {line.rsplit(b'|', 1)[-1].strip() for line in imports.splitlines()}
        self.assertIn(b'worker_runtime.protocol', imported_modules)
        # Import profiling also lists missing optional packages, but only their top-level names.
        iri_grammar = sorted(name for name in imported_modules if name.startswith(b'rfc3987_syntax.'))
        self.assertEqual(iri_grammar, [])

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
        self.assertEqual(set(current['capabilities']),
                         {'instrument.search', 'market.getBars', 'market.readHistoryPage', 'market.subscribe'})
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

    def test_replay_is_opt_in_and_cancellation_matches_the_owned_task(self):
        examples = json.loads((ROOT / 'tests/fixtures/protocol-cases.json').read_text())
        request = json.loads(next(case['wire'] for case in examples if case['name'] == 'frozen market resume request'))
        series = request['payload']['series']
        page = execute_page({'series': series, 'from_ms': 0, 'to_ms': 1_720_000_000_000,
                             'warmup_bars': 0, 'include_stream_position': True}, MAX_MESSAGE_BYTES)
        request['payload'] = {'series': series, 'snapshot': page['snapshot'], 'after': page['stream_position']}
        task = '00000000-0000-4000-8000-000000000099'
        request['task_id'] = task
        line = frame(**request)
        current = ('--wire-version', '1.1')
        for role in ('market', 'ai', 'indicator'):
            code, replies, _ = worker(line, role, current)
            self.assertEqual(code, 0)
            self.assertEqual(replies[1]['error']['code'], 'UNSUPPORTED_CAPABILITY')
        cancel = dict(protocol_version='1.1', type='cancel', request_id=request['request_id'])
        code, replies, stderr = worker(
            line + frame(**cancel, task_id=REQUEST_ID) + frame(**cancel, task_id=task),
            extra_args=(*current, '--demo'),
        )
        self.assertEqual((code, stderr), (0, b''))
        self.assertEqual(replies[1]['result']['from_position'], page['stream_position'])
        self.assertTrue(all(reply['operation'] == 'market.subscribe' for reply in replies[1:]))
        self.assertEqual([reply['error']['code'] for reply in replies[2:]], ['PERMISSION_DENIED', 'CANCELLED'])

    def owned_replay_replies(self, messages, frames, payload):
        replies = []
        accepted = set()
        active = None
        observation = 0
        _, updates = plan_replay(payload)
        after = payload['after']
        for frame in frames:
            if frame['type'] == 'event':
                self.assertIsNotNone(active, 'Event outside an active replay subscription')
                self.assertLess(observation, len(updates))
                self.assertEqual((frame['stream_id'], frame['epoch']), (after['stream_id'], after['epoch']))
                update = updates[observation]
                if isinstance(update, GapObservation):
                    self.assertEqual((frame['event'], frame['sequence']), ('market.stream_gap', update.sequence))
                    self.assertEqual(frame['payload'], {
                        'series': payload['series'], 'reason': 'sequence_gap', 'affected': update.affected,
                        'expected_sequence': update.expected_sequence, 'lost_count': update.lost_count,
                    })
                else:
                    self.assertEqual((frame['event'], frame['sequence']),
                                     ('market.bar_update', after['sequence'] + observation + 1))
                    self.assertEqual(frame['payload'], {
                        'series': payload['series'], 'dataset_revision': payload['snapshot']['dataset_revision'],
                        **update,
                    })
                observation += 1
                if observation == len(updates):
                    active = None
                continue
            self.assertEqual(frame['type'], 'response')
            self.assertLess(len(replies), len(messages))
            request = messages[len(replies)]
            identity = request['request_id']
            self.assertEqual(frame['request_id'], identity)
            replies.append(frame)
            if (frame.get('result') or {}).get('status') == 'accepted' and identity not in accepted:
                self.assertEqual(request['operation'], 'market.subscribe')
                self.assertEqual(frame['result']['from_position'], after)
                self.assertIsNone(active, 'An accepted replay displaced an active subscription')
                accepted.add(identity)
                active, observation = identity, 0
            if active == identity and request['type'] == 'cancel' and (frame.get('error') or {}).get('code') == 'CANCELLED':
                active = None
        self.assertEqual(len(replies), len(messages))
        return replies

    def test_read_and_replay_share_request_ownership_and_terminal_retries(self):
        examples = {case['name']: json.loads(case['wire']) for case in json.loads(
            (ROOT / 'tests/fixtures/protocol-cases.json').read_text()) if case['valid']}
        replay = examples['frozen market resume request']
        history = examples['versioned history page']
        history['payload'].update(from_ms=0, to_ms=1_720_000_000_000, include_stream_position=True)
        page = execute_page(history['payload'], MAX_MESSAGE_BYTES)
        replay['payload'] = {'series': history['payload']['series'],
                             'after': page['stream_position'], 'snapshot': page['snapshot']}

        def identity(index):
            return f'00000000-0000-4000-8000-{index:012x}'

        owner, other = identity(20), identity(21)
        first, second, recent, next_id, legacy_id = (identity(i) for i in range(10, 15))
        request = dict(replay, request_id=first, task_id=owner)
        next_replay = dict(replay, request_id=second, task_id=owner)
        cancel = dict(protocol_version='1.1', type='cancel', request_id=first, task_id=owner)
        historical = dict(history, request_id=recent, task_id=owner)
        legacy = dict(protocol_version='1.0', type='request', operation='market.getBars', task_id=None,
                      payload={'instrument_id': 'SIM.CN.FUTURE.001', 'provider_id': 'scopehub.synthetic',
                               'timeframe': '1m', 'from_ms': 0, 'to_ms': 1_720_000_000_000})
        messages = [
            historical, dict(replay, request_id=recent, task_id=other), historical,
            request, request, dict(history, request_id=first, task_id=other),
            dict(history, request_id=first, task_id=owner), dict(cancel, task_id=other),
            dict(legacy, request_id=first), next_replay, cancel, cancel, request,
            next_replay, dict(cancel, request_id=second), dict(replay, request_id=next_id, task_id=owner),
            dict(cancel, request_id=next_id), dict(legacy, request_id=legacy_id),
        ]
        code, replies, stderr = worker(b''.join(frame(**message) for message in messages),
                                       extra_args=('--wire-version', '1.1', '--demo'), interleave_replay=True)
        self.assertEqual((code, stderr), (0, b''))
        self.assertEqual(replies[0]['type'], 'hello')
        outputs = self.owned_replay_replies(messages, replies[1:], replay['payload'])
        self.assertEqual([item['error']['code'] if not item['ok'] else None for item in outputs], [
            None, 'PERMISSION_DENIED', None, None, None, 'PERMISSION_DENIED',
            'INVALID_ARGUMENT', 'PERMISSION_DENIED', 'PERMISSION_DENIED', 'BUSY',
            'CANCELLED', 'CANCELLED', 'CANCELLED', None, 'CANCELLED', None, 'CANCELLED', None,
        ])
        for left, right in [(0, 2), (3, 4), (10, 11), (10, 12)]:
            self.assertEqual(outputs[left], outputs[right])
        self.assertEqual(outputs[1]['operation'], 'market.subscribe')
        self.assertEqual(outputs[5]['operation'], 'market.readHistoryPage')
        self.assertEqual(outputs[13]['result']['status'], 'accepted')
        self.assertEqual(outputs[15]['result']['status'], 'accepted')
        self.assertEqual(len(outputs[-1]['result']['bars']), 40)
        self.assertEqual(outputs[-1]['protocol_version'], '1.0')

        # The staged final subscription publishes before its cancellation. Keep
        # a negative check for a wrong eligible-stream frame or a late delivery.
        frames = replies[1:]
        accepted_at = frames.index(outputs[15])
        cancelled_at = frames.index(outputs[16])
        event_at = next(i for i in range(accepted_at + 1, cancelled_at) if frames[i]['type'] == 'event')
        event = frames[event_at]
        for wrong in (dict(event, event='market.unexpected'), dict(event, epoch=identity(42))):
            with self.assertRaises(AssertionError):
                self.owned_replay_replies(messages, [*frames[:event_at], wrong, *frames[event_at + 1:]], replay['payload'])
        with self.assertRaisesRegex(AssertionError, 'outside an active replay'):
            self.owned_replay_replies(messages, [*frames[:cancelled_at + 1], event, *frames[cancelled_at + 1:]], replay['payload'])


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
