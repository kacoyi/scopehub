"""History requests preserve fixture identity and never imply a live feed."""
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

from tests.integration.test_worker import ROOT, frame, worker
from providers.history import HistoryError, execute
from providers.history_pages import FrozenHistory, PAGE_ENVELOPE, execute_page, sample_quality
from providers.simulated import PROVIDER_ID, build_fixture, instruments
from worker_runtime.protocol import MAX_MESSAGE_BYTES, decode_line


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


class HistoryPageTests(unittest.TestCase):
    @staticmethod
    def selection(**fields):
        instrument = instruments()[0]
        series = {'instrument_id': instrument['id'], 'provider_id': PROVIDER_ID, 'timeframe': '1m',
                  'price_adjustment': 'raw', 'session_scope': 'sample-sessions',
                  'calendar_id': instrument['calendar_id'], 'quantity_unit': instrument['quantity_unit']}
        return {'series': series, 'from_ms': 0, 'to_ms': 2_000_000_000_000, 'warmup_bars': 0, **fields}

    def test_missed_observation_deadline_keeps_known_gaps_visible(self):
        # Keep the existing gapped bars and vary only the synthetic observation evidence.
        read = self.selection(expected_revision='scopehub-synthetic-v1:20241013:gapped')
        last = 1704330570000
        for observed, cutoff, session, freshness in (
            (last, 1704330584999, 'open', 'current'),
            (last, 1704330585000, 'open', 'delayed'),
            (last, 1704330600000, 'closed', 'delayed'),
            (last, 1704330600001, 'closed', 'stale'),
            (1704330540000, 1704330585000, 'open', 'stale'),
        ):
            with self.subTest(observed=observed, cutoff=cutoff):
                fixture = build_fixture('SIM.CN.FUTURE.001', '1m', 'gapped')
                dataset = fixture['history']['dataset']
                dataset['provenance'].update(as_of_ms=cutoff, last_observed_ms=observed)
                with patch('providers.history_pages.build_fixture', return_value=fixture):
                    page = execute_page(read, MAX_MESSAGE_BYTES)
                self.assertEqual('gapped', page['dataset']['quality'])
                self.assertEqual({
                    'session_state': session, 'freshness': freshness, 'coverage': 'gapped',
                    'connection': 'offline', 'source_kind': 'synthetic',
                    'as_of_ms': cutoff, 'last_observed_ms': observed,
                    'gaps': [
                        {'from_ms': 1704200520000, 'to_ms': 1704200580000},
                        {'from_ms': 1704200700000, 'to_ms': 1704201000000},
                    ],
                }, page['quality_state'])

    def test_missing_evidence_stays_unknown_on_its_own_axis(self):
        read = self.selection()
        for missing, session, freshness, coverage, summary, observed in (
            ('sessions', 'unknown', 'unknown', 'observed', 'fresh', 1704330570000),
            ('last_observed_ms', 'open', 'unknown', 'observed', 'fresh', None),
            ('missing_minute_starts_ms', 'open', 'current', 'unknown', 'degraded', 1704330570000),
        ):
            with self.subTest(missing=missing):
                fixture = build_fixture('SIM.CN.FUTURE.001', '1m')
                del fixture['history']['dataset']['provenance'][missing]
                with patch('providers.history_pages.build_fixture', return_value=fixture):
                    page = execute_page(read, MAX_MESSAGE_BYTES)
                self.assertEqual({
                    'session_state': session, 'freshness': freshness, 'coverage': coverage,
                    'connection': 'offline', 'source_kind': 'synthetic',
                    'as_of_ms': 1704330570000, 'last_observed_ms': observed, 'gaps': [],
                }, page['quality_state'])
                self.assertEqual(summary, page['dataset']['quality'])

    def test_valid_empty_slice_preserves_frozen_source_quality(self):
        read = self.selection(from_ms=1704200520000, to_ms=1704200580000,
                              expected_revision='scopehub-synthetic-v1:20241013:gapped')
        page = execute_page(read, MAX_MESSAGE_BYTES)
        self.assertEqual(([], 0, 0, None), (page['bars'], page['total_bars'], page['offset'], page['next_cursor']))
        self.assertEqual(('current', 'gapped'), (page['quality_state']['freshness'], page['quality_state']['coverage']))
        self.assertEqual(page, execute_page(dict(read, snapshot=page['snapshot']), MAX_MESSAGE_BYTES))

    def test_partial_and_closed_observations_have_different_replayable_inputs(self):
        read = self.selection()
        initial = execute_page(read, MAX_MESSAGE_BYTES)
        cutoff = initial['snapshot']['as_of_ms']
        updated = execute_page(dict(read, as_of_ms=cutoff + 30_000), MAX_MESSAGE_BYTES)
        self.assertEqual((3500.8, 110, False), tuple(initial['bars'][-1][key] for key in ('close','volume','is_closed')))
        self.assertEqual((3501, 130, True), tuple(updated['bars'][-1][key] for key in ('close','volume','is_closed')))
        self.assertEqual('a7d14a98552ee57f245c341f63e5c4242ee24613d3c4086f63430c0eec8bb766',
                         initial['snapshot']['content_hash'])
        self.assertEqual('dfe41df98529e7ab90508b88beeaf00cdbc77b967024e8a13f1049ee066b21c3',
                         updated['snapshot']['content_hash'])
        self.assertEqual(initial, execute_page(dict(read, snapshot=initial['snapshot']), MAX_MESSAGE_BYTES))
        self.assertEqual(updated, execute_page(dict(read, snapshot=updated['snapshot']), MAX_MESSAGE_BYTES))
        closed = {
            'as_of_ms': 1704330600000, 'last_observed_ms': 1704330600000, 'session_state': 'closed',
            'freshness': 'current', 'connection': 'offline', 'coverage': 'observed',
            'gaps': [], 'source_kind': 'synthetic',
        }
        self.assertEqual(closed, updated['quality_state'])
        self.assertEqual(dict(closed, as_of_ms=1704332370000),
                         sample_quality(updated['dataset'], 1704332370000))
        self.assertIsNone(updated['stream_position'])
        for changes, code in [({'expected_revision':'gone'}, 'SNAPSHOT_UNAVAILABLE'),
                              ({'as_of_ms':cutoff + 1}, 'SNAPSHOT_UNAVAILABLE'),
                              ({'snapshot':dict(initial['snapshot'],content_hash='0'*64)}, 'SNAPSHOT_UNAVAILABLE'),
                              ({'series':dict(read['series'],price_adjustment='adjusted')}, 'UNSUPPORTED_CAPABILITY')]:
            with self.subTest(changes=changes), self.assertRaises(HistoryError) as error:
                execute_page(dict(read, **changes), MAX_MESSAGE_BYTES)
            self.assertEqual(error.exception.code, code)

    def test_a_known_gap_with_late_or_missing_observation_cannot_start_a_stream(self):
        for observed, freshness in [(1704330545000, 'stale'), (None, 'unknown')]:
            fixture = build_fixture('SIM.CN.FUTURE.001', '1m', 'gapped')
            dataset = fixture['history']['dataset']
            self.assertTrue(fixture['updates'])
            dataset['provenance']['as_of_ms'] = 1704330570001
            if observed is None:
                del dataset['provenance']['last_observed_ms']
            else:
                dataset['provenance']['last_observed_ms'] = observed
            read = self.selection(expected_revision=dataset['revision'], include_stream_position=True)
            with self.subTest(freshness=freshness), patch('providers.history_pages.build_fixture', return_value=fixture):
                page = execute_page(read, MAX_MESSAGE_BYTES)
                self.assertEqual((freshness, 'gapped', 2), (page['quality_state']['freshness'],
                                 page['quality_state']['coverage'], len(page['quality_state']['gaps'])))
                self.assertIsNone(page['stream_position'])

    def test_versioned_worker_honors_known_revisions_and_binds_pinned_pages(self):
        def read_page(payload):
            data = frame(protocol_version='1.1', type='request', request_id=str(uuid4()), task_id=None,
                         operation='market.readHistoryPage', payload=payload)
            code, replies, stderr = worker(data, extra_args=('--wire-version', '1.1', '--demo'))
            self.assertEqual((0, b''), (code, stderr))
            self.assertEqual('market.readHistoryPage', replies[1]['operation'])
            return replies[1]

        read = self.selection()
        for scenario, total, second_time in (('gapped', 34, 1704200580000), ('stale', 40, 1704200520000)):
            with self.subTest(scenario=scenario):
                revision = f'scopehub-synthetic-v1:20241013:{scenario}'
                first = read_page(dict(read, expected_revision=revision, page_size=2))['result']
                self.assertEqual(revision, first['snapshot']['dataset_revision'])
                self.assertEqual(read['series'], first['snapshot']['series'])
                self.assertEqual([1704200400000, 1704200460000], [bar['time_ms'] for bar in first['bars']])
                second = read_page(dict(read, snapshot=first['snapshot'], cursor=first['next_cursor']))['result']
                self.assertEqual(first['snapshot'], second['snapshot'])
                self.assertEqual(first['quality_state'], second['quality_state'])
                self.assertEqual((2, total, total - 2, second_time, None),
                                 (second['offset'], second['total_bars'], len(second['bars']),
                                  second['bars'][0]['time_ms'], second['next_cursor']))

        stalled = read_page(dict(read, expected_revision='scopehub-synthetic-v1:20241013:stale',
                                 include_stream_position=True))['result']
        self.assertIsNone(stalled['stream_position'])
        pinned = stalled['snapshot']
        stock = instruments()[1]
        stock_series = dict(read['series'], instrument_id=stock['id'], calendar_id=stock['calendar_id'],
                            quantity_unit=stock['quantity_unit'])
        for modified in (
            {'snapshot': dict(pinned, dataset_revision='unknown')},
            {'snapshot': pinned, 'expected_revision': 'scopehub-synthetic-v1:20241013:normal'},
            {'snapshot': pinned, 'series': dict(read['series'], timeframe='5m')},
            {'snapshot': pinned, 'series': stock_series},
            {'series': stock_series, 'expected_revision': pinned['dataset_revision']},
            {'series': stock_series, 'expected_revision': 'scopehub-synthetic-v1:20241014:gapped'},
        ):
            with self.subTest(modified=modified):
                response = read_page(dict(read, **modified))
                self.assertFalse(response['ok'])
                self.assertEqual('SNAPSHOT_UNAVAILABLE', response['error']['code'])

    def test_warmup_range_and_continuation_bind_exact_input(self):
        full = execute_page(self.selection(), MAX_MESSAGE_BYTES)
        start = full['bars'][-1]['time_ms']
        read = self.selection(from_ms=start, to_ms=start+60_000, page_size=1)
        one = execute_page(read, MAX_MESSAGE_BYTES)
        with_warmup = dict(read, warmup_bars=1)
        first = execute_page(with_warmup, MAX_MESSAGE_BYTES)
        second = execute_page(dict(with_warmup, snapshot=first['snapshot'], cursor=first['next_cursor']), MAX_MESSAGE_BYTES)
        self.assertEqual([3501.6, 3500.8], [bar['close'] for page in (first,second) for bar in page['bars']])
        self.assertEqual((0,1,2,None), (first['offset'], second['offset'], second['total_bars'], second['next_cursor']))
        self.assertEqual(first['snapshot'], second['snapshot'])
        self.assertNotEqual(one['snapshot']['content_hash'], first['snapshot']['content_hash'])
        # A new, independent read of a later observed bar cannot alter an in-flight pinned page.
        latest = execute_page(dict(with_warmup, as_of_ms=first['snapshot']['as_of_ms']+30_000), MAX_MESSAGE_BYTES)
        self.assertNotEqual(latest['snapshot']['content_hash'], second['snapshot']['content_hash'])
        for modified in [{'cursor':first['next_cursor']},
                         {'snapshot':first['snapshot'], 'cursor':dict(first['next_cursor'],next_index=2)},
                         {'snapshot':first['snapshot'], 'cursor':dict(first['next_cursor'],prefix_hash='f'*64)}]:
            with self.subTest(modified=modified), self.assertRaises(HistoryError) as error:
                execute_page(dict(with_warmup, **modified), MAX_MESSAGE_BYTES)
            self.assertEqual(error.exception.code, 'INVALID_ARGUMENT')

    def test_hundred_thousand_records_respect_wire_bytes_and_exact_terminal_boundary(self):
        read = self.selection(from_ms=0, to_ms=100000)
        template = execute_page(self.selection(), MAX_MESSAGE_BYTES)
        bar = dict(template['bars'][0], is_closed=True)
        bars = [dict(bar,time_ms=i) for i in range(100000)]
        frozen = FrozenHistory.freeze(template['dataset'],template['quality_state'],read['series'],bars,0,100000,0)
        offset = 0
        seen = []
        for _ in range(10):
            page = frozen.page(read, MAX_MESSAGE_BYTES)
            size = len((json.dumps({**PAGE_ENVELOPE,'result':page},separators=(',',':'))+'\n').encode())
            self.assertLessEqual(size,MAX_MESSAGE_BYTES)
            self.assertEqual(offset,page['offset'])
            self.assertGreater(len(page['bars']),0)
            seen.extend(item['time_ms'] for item in page['bars'])
            offset += len(page['bars'])
            if page['next_cursor'] is None:
                break
            self.assertLess(offset,100000)
            read = dict(read,snapshot=page['snapshot'],cursor=page['next_cursor'])
        self.assertEqual(list(range(100000)),seen)
        self.assertIsNone(page['next_cursor'])
        # Exactly one complete bar fits including the response wrapper and LF.
        single=FrozenHistory.freeze(template['dataset'],template['quality_state'],read['series'],[bars[0]],0,1,0)
        request=self.selection(from_ms=0,to_ms=1)
        single_page=single.page(request,MAX_MESSAGE_BYTES)
        exact=len((json.dumps({**PAGE_ENVELOPE,'result':single_page},separators=(',',':'))+'\n').encode())
        self.assertEqual(single_page,single.page(request,exact))
        with self.assertRaises(HistoryError) as error: single.page(request,exact-1)
        self.assertEqual('RESOURCE_LIMIT',error.exception.code)


if __name__ == '__main__':
    unittest.main()
