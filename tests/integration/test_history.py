"""History requests preserve fixture identity and never imply a live feed."""
import json
import os
import subprocess
import sys
import unittest
from uuid import uuid4

from tests.integration.test_worker import ROOT, frame
from providers.history import HistoryError, execute
from providers.simulated import PROVIDER_ID, build_fixture, instruments
from worker_runtime.protocol import decode_line


def request(operation, payload, request_id=None):
    return {'protocol_version': '1.0', 'message_id': str(uuid4()), 'timestamp_ms': 0,
            'type': 'request', 'request_id': request_id or str(uuid4()), 'task_id': None,
            'operation': operation, 'payload': payload}


def params(instrument='SIM.CN.FUTURE.001', timeframe='1m', **overrides):
    return {'instrument_id': instrument, 'timeframe': timeframe, 'provider_id': PROVIDER_ID,
            'from_ms': 0, 'to_ms': 9_007_199_254_740_991, **overrides}


class HistoryTests(unittest.TestCase):
    def test_all_histories_equal_committed_fixtures_and_are_independent(self):
        fixture = json.loads((ROOT / 'tests/fixtures/market/series.json').read_text())
        for item in fixture['fixtures'][:9]:
            expected = item['history']
            data = params(expected['dataset']['instrument_id'], expected['dataset']['timeframe'])
            output = execute(request('market.getBars', data))
            self.assertEqual(expected, output)
            output['bars'].clear()
            self.assertEqual(expected, execute(request('market.getBars', data)))

    def test_half_open_start_range_empty_and_partial_not_finalized(self):
        bars = build_fixture('SIM.CN.FUTURE.001', '1m')['history']['bars']
        output = execute(request('market.getBars', params(from_ms=bars[0]['time_ms'], to_ms=bars[1]['time_ms'])))
        self.assertEqual([bars[0]], output['bars'])
        self.assertEqual([], execute(request('market.getBars', params(from_ms=1, to_ms=2)))['bars'])
        output = execute(request('market.getBars', params()))
        self.assertFalse(output['bars'][-1]['is_closed'])
        self.assertIsNone(output['bars'][7]['volume'])
        self.assertEqual(0, output['bars'][8]['volume'])
        self.assertEqual('2024-01-03', output['bars'][0]['trading_day'])

    def test_no_fallback_for_wrong_source_instrument_timeframe_or_range(self):
        for values, code in [({'provider_id': 'paid.provider'}, 'PROVIDER_NOT_CONFIGURED'),
                             ({'instrument_id': 'IF2601'}, 'DATA_UNAVAILABLE'),
                             ({'timeframe': '1s'}, 'UNSUPPORTED_CAPABILITY'),
                             ({'from_ms': 2, 'to_ms': 1}, 'INVALID_ARGUMENT')]:
            with self.subTest(values=values), self.assertRaises(HistoryError) as raised:
                execute(request('market.getBars', params(**values)))
            self.assertEqual(code, raised.exception.code)

    def test_catalog_search_limit_and_no_mutable_global_catalog(self):
        first = instruments()
        first[0]['metadata']['synthetic'] = False
        self.assertTrue(instruments()[0]['metadata']['synthetic'])
        self.assertEqual(3, len(execute(request('instrument.search', {'query': '', 'limit': 50}))['instruments']))
        self.assertEqual(1, len(execute(request('instrument.search', {'query': ' stock ', 'limit': 1}))['instruments']))
        self.assertEqual([], execute(request('instrument.search', {'query': 'IF2601', 'limit': 10}))['instruments'])

    def test_demo_worker_negotiates_replays_and_rejects_changed_duplicate_input(self):
        env = {key: value for key, value in os.environ.items() if key.upper() in {'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP'}}
        env.update(PYTHONPATH=os.pathsep.join([str(ROOT), str(ROOT / 'python')]), PYTHONIOENCODING='utf-8')
        first = request('market.getBars', params())
        changed = {**first, 'payload': params(timeframe='5m')}
        data = b''.join((json.dumps(item) + '\n').encode() for item in [first, first, changed])
        result = subprocess.run([sys.executable, '-m', 'worker_runtime', '--role', 'market', '--demo'],
                                cwd=ROOT, env=env, input=data + frame(type='heartbeat'), capture_output=True, timeout=15)
        self.assertEqual(0, result.returncode, result.stderr)
        messages = [decode_line(line) for line in result.stdout.splitlines()]
        self.assertEqual(['instrument.search', 'market.getBars'], messages[0]['capabilities'])
        self.assertEqual(messages[1], messages[2])
        self.assertEqual('INVALID_ARGUMENT', messages[3]['error']['code'])
        self.assertEqual('heartbeat', messages[4]['type'])
