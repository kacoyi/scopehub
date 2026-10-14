from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import unittest
from uuid import UUID

from jsonschema import Draft202012Validator, FormatChecker

from providers.simulated import Session, aggregate_minutes, build_fixture, fixture_bytes

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / 'tests/fixtures/market'
FUTURE = 'SIM.CN.FUTURE.001'
INSTRUMENTS = (FUTURE, 'SIM.CN.STOCK.001', 'SIM.US.STOCK.001')
TIMEFRAMES = ('1m', '5m', '1d')


class MarketFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = json.loads((FIXTURES / 'series.json').read_text())
        cls.oracle = json.loads((FIXTURES / 'aggregation-oracle.json').read_text())
        schema = json.loads((ROOT / 'contracts/schemas/protocol.schema.json').read_text())
        cls.validators = {
            name: Draft202012Validator({'$ref': f'#/$defs/{name}', '$defs': schema['$defs']},
                                      format_checker=FormatChecker())
            for name in ('Instrument', 'MarketGetBarsOutput', 'Bar')
        }

    def test_committed_replay_is_reproducible(self):
        self.assertEqual((FIXTURES / 'series.json').read_bytes(), fixture_bytes())
        normal = [value for value in self.bundle['fixtures'] if value['scenario'] == 'normal']
        self.assertEqual({(value['instrument']['id'], value['history']['dataset']['timeframe']) for value in normal},
                         {(instrument, timeframe) for instrument in INSTRUMENTS for timeframe in TIMEFRAMES})
        for fixture in normal:
            timeframe = fixture['history']['dataset']['timeframe']
            self.assertEqual(len(fixture['history']['bars']), {'1m': 40, '5m': 8, '1d': 2}[timeframe])

    def test_all_snapshots_fit_the_existing_wire_models(self):
        identities = set()
        for fixture in self.bundle['fixtures']:
            dataset = fixture['history']['dataset']
            with self.subTest(instrument=dataset['instrument_id'], timeframe=dataset['timeframe'], scenario=fixture['scenario']):
                self.validators['Instrument'].validate(fixture['instrument'])
                self.validators['MarketGetBarsOutput'].validate(fixture['history'])
                self.assertEqual(dataset['mode'], 'simulated')
                self.assertEqual(fixture['instrument']['tradability'], 'non_tradable')
                self.assertTrue(dataset['provenance']['synthetic'])
                self.assertTrue(fixture['instrument']['metadata']['synthetic'])
                self.assertEqual(dataset['calendar_id'], fixture['instrument']['calendar_id'])
                self.assertEqual(dataset['instrument_id'], fixture['instrument']['id'])
                self.assertEqual(UUID(dataset['id']).version, 5)
                self.assertNotIn(dataset['id'], identities)
                identities.add(dataset['id'])
                for update in fixture['updates']:
                    self.validators['Bar'].validate(update['bar'])

    def test_bar_invariants_and_tick_units(self):
        for fixture in self.bundle['fixtures']:
            history = fixture['history']['bars']
            times = [bar['time_ms'] for bar in history]
            self.assertEqual(times, sorted(set(times)))
            self.assertTrue(all(type(value) is int and 0 < value < 2**53 for value in times))
            self.assertTrue(all(bar['is_closed'] for bar in history[:-1]))
            self.assertFalse(history[-1]['is_closed'])
            tick = Decimal(fixture['instrument']['price_tick'])
            for bar in history + [value['bar'] for value in fixture['updates']]:
                self.assertLessEqual(bar['low'], min(bar['open'], bar['close']))
                self.assertGreaterEqual(bar['high'], max(bar['open'], bar['close']))
                for field in ('open', 'high', 'low', 'close'):
                    self.assertTrue(math.isfinite(bar[field]))
                    self.assertEqual(Decimal(str(bar[field])) % tick, 0)
                self.assertTrue(bar['volume'] is None or math.isfinite(bar['volume']) and bar['volume'] >= 0)

    def test_hand_calculated_aggregation_oracle(self):
        sessions = [Session(**value) for value in self.oracle['sessions']]
        for timeframe, expected in self.oracle['expected'].items():
            with self.subTest(timeframe=timeframe):
                actual = aggregate_minutes(self.oracle['minutes'], timeframe, sessions,
                                           as_of_ms=self.oracle['as_of_ms'])
                self.assertEqual(actual, expected)
        self.assertEqual(aggregate_minutes(self.oracle['minutes'], '1m', sessions,
                                           as_of_ms=self.oracle['as_of_ms']), self.oracle['minutes'])

    def test_explicit_trading_day_spans_utc_dates(self):
        fixture = build_fixture(FUTURE, '1m')
        bars = fixture['history']['bars']
        self.assertEqual(bars[0]['time_ms'], 1704200400000)
        self.assertEqual(bars[10]['time_ms'], 1704243600000)
        utc_dates = [datetime.fromtimestamp(bar['time_ms'] / 1000, timezone.utc).date().isoformat() for bar in bars[:20]]
        self.assertEqual(set(utc_dates), {'2024-01-02', '2024-01-03'})
        self.assertEqual({bar['trading_day'] for bar in bars[:20]}, {'2024-01-03'})
        daily = build_fixture(FUTURE, '1d')['history']['bars']
        self.assertEqual([bar['time_ms'] for bar in daily], [1704200400000, 1704286800000])
        self.assertEqual([bar['trading_day'] for bar in daily], ['2024-01-03', '2024-01-04'])
        self.assertTrue(daily[0]['is_closed'])
        self.assertFalse(daily[1]['is_closed'])

    def test_unknown_volume_does_not_become_zero(self):
        for instrument in INSTRUMENTS:
            one = build_fixture(instrument, '1m')['history']['bars']
            five = build_fixture(instrument, '5m')['history']['bars']
            daily = build_fixture(instrument, '1d')['history']['bars']
            self.assertIsNone(one[7]['volume'])
            self.assertEqual(one[8]['volume'], 0)
            self.assertIsNone(five[1]['volume'])
            self.assertIsNone(daily[0]['volume'])
            self.assertEqual(five[0]['volume'], sum(bar['volume'] for bar in one[:5]))

    def test_gap_stays_missing_and_is_declared(self):
        normal = build_fixture(FUTURE, '1m')['history']['bars']
        missing = [1704200520000, 1704200700000, 1704200760000, 1704200820000, 1704200880000, 1704200940000]
        complete = build_fixture(FUTURE, '1m')['history']
        self.assertEqual(complete['dataset']['quality'], 'fresh')
        for timeframe in TIMEFRAMES:
            gap = build_fixture(FUTURE, timeframe, 'gapped')['history']
            self.assertEqual(gap['dataset']['quality'], 'gapped')
            self.assertEqual(gap['dataset']['provenance']['missing_minute_starts_ms'], missing)
            if timeframe == '1m':
                self.assertEqual(gap['bars'], [bar for bar in normal if bar['time_ms'] not in missing])
            elif timeframe == '5m':
                self.assertEqual(len(gap['bars']), 7)
                self.assertNotIn(1704200700000, [bar['time_ms'] for bar in gap['bars']])
                self.assertTrue(gap['bars'][0]['is_closed'])
                self.assertIsNone(gap['bars'][0]['volume'])
            else:
                self.assertIsNone(gap['bars'][0]['volume'])

    def test_complete_zero_volume_and_incomplete_volume_are_distinct(self):
        session = Session(1704200400000, 5, '2024-01-03')
        minutes = [{**self.oracle['minutes'][0], 'time_ms': session.start_ms + index * 60_000, 'volume': 0}
                   for index in range(5)]
        complete = aggregate_minutes(minutes, '5m', [session], as_of_ms=session.end_ms)
        incomplete = aggregate_minutes(minutes[1:], '5m', [session], as_of_ms=session.end_ms)
        self.assertEqual(complete[0]['volume'], 0)
        self.assertIsNone(incomplete[0]['volume'])
        self.assertEqual(incomplete[0]['time_ms'], session.start_ms)
        self.assertTrue(incomplete[0]['is_closed'])

    def test_stale_observation_does_not_finalize_a_stalled_bar(self):
        for timeframe in TIMEFRAMES:
            normal = build_fixture(FUTURE, timeframe)
            stale = build_fixture(FUTURE, timeframe, 'stale')
            self.assertEqual(stale['history']['bars'], normal['history']['bars'])
            self.assertEqual(stale['history']['dataset']['quality'], 'stale')
            provenance = stale['history']['dataset']['provenance']
            self.assertEqual(provenance['as_of_ms'] - provenance['last_observed_ms'], 1_800_000)
            self.assertEqual(stale['updates'], [])
            self.assertFalse(stale['history']['bars'][-1]['is_closed'])

    def test_updates_replace_one_key_and_preserve_ohlc_extrema(self):
        for instrument in INSTRUMENTS:
            for timeframe in TIMEFRAMES:
                fixture = build_fixture(instrument, timeframe)
                original = fixture['history']['bars']
                merged = {bar['time_ms']: deepcopy(bar) for bar in original}
                previous = original[-1]
                for index, update in enumerate(fixture['updates']):
                    bar = update['bar']
                    self.assertEqual((bar['time_ms'], bar['trading_day'], bar['open']),
                                     (previous['time_ms'], previous['trading_day'], previous['open']))
                    self.assertGreaterEqual(bar['high'], previous['high'])
                    self.assertLessEqual(bar['low'], previous['low'])
                    self.assertGreater(bar['volume'], previous['volume'])
                    self.assertEqual(bar['is_closed'], index == 1)
                    merged[bar['time_ms']] = bar
                    previous = bar
                self.assertEqual(len(merged), len(original))
                self.assertEqual(list(merged.values())[:-1], original[:-1])

    def test_replay_aggregation_matches_observed_minute_updates(self):
        for instrument in INSTRUMENTS:
            minute = build_fixture(instrument, '1m')
            for timeframe, count in (('5m', 5), ('1d', 20)):
                aggregate = build_fixture(instrument, timeframe)
                for index, update in enumerate(minute['updates']):
                    group = [*minute['history']['bars'][-count:-1], update['bar']]
                    expected = {
                        'time_ms': group[0]['time_ms'], 'trading_day': group[0]['trading_day'],
                        'open': group[0]['open'], 'high': max(bar['high'] for bar in group),
                        'low': min(bar['low'] for bar in group), 'close': group[-1]['close'],
                        'volume': sum(bar['volume'] for bar in group), 'is_closed': index == 1,
                    }
                    self.assertEqual(aggregate['updates'][index], {'observed_at_ms': update['observed_at_ms'], 'bar': expected})

    def test_partial_bucket_and_session_alignment(self):
        # 13:02 UTC: five-minute keys are session anchored, not UTC multiples of five minutes.
        session = Session(1704200520000, 6, '2024-01-03')
        minute = {**self.oracle['minutes'][0], 'time_ms': session.start_ms}
        result = aggregate_minutes([minute], '5m', [session], as_of_ms=session.start_ms + 60_000)
        self.assertEqual(result, [{**minute, 'is_closed': False}])
        # Even an all-closed source minute does not close its larger interval early.
        daily = aggregate_minutes([minute], '1d', [session], as_of_ms=session.start_ms + 60_000)
        self.assertFalse(daily[0]['is_closed'])
        ended = aggregate_minutes([minute], '5m', [session], as_of_ms=session.end_ms)
        self.assertTrue(ended[0]['is_closed'])

    def test_invalid_minutes_are_rejected_instead_of_repaired(self):
        sessions = [Session(**value) for value in self.oracle['sessions']]
        corruptions = [
            {'open': math.nan}, {'high': math.inf}, {'low': -math.inf}, {'close': True},
            {'high': 5}, {'low': 13}, {'close': 100}, {'volume': -1}, {'volume': math.nan},
            {'volume': True}, {'is_closed': False}, {'is_closed': 1},
            {'time_ms': 1704200400001}, {'time_ms': True}, {'time_ms': 1704200400000.0},
            {'trading_day': '2024-01-02'},
        ]
        for change in corruptions:
            bars = deepcopy(self.oracle['minutes'])
            bars[0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                aggregate_minutes(bars, '5m', sessions, as_of_ms=self.oracle['as_of_ms'])
        for bars in (list(reversed(self.oracle['minutes'])), [self.oracle['minutes'][0]] * 2):
            with self.assertRaises(ValueError):
                aggregate_minutes(bars, '1m', sessions, as_of_ms=self.oracle['as_of_ms'])
        with self.assertRaises(ValueError):
            aggregate_minutes(self.oracle['minutes'], '1m', sessions, as_of_ms=1704200400000)

    def test_invalid_calendar_and_unsupported_selection_fail_explicitly(self):
        session = Session(**self.oracle['sessions'][0])
        for sessions in ([], [session, session], [Session(session.start_ms, 0, session.trading_day)],
                         [Session(session.start_ms, 6, '2024-02-30')]):
            with self.subTest(sessions=sessions), self.assertRaises(ValueError):
                aggregate_minutes([], '1m', sessions, as_of_ms=self.oracle['as_of_ms'])
        self.assertEqual(aggregate_minutes([], '1m', [session], as_of_ms=self.oracle['as_of_ms']), [])
        for arguments in (('UNKNOWN', '1m'), (FUTURE, '1h'), (FUTURE, '1m', 'unknown')):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                build_fixture(*arguments)

    def test_caller_mutation_cannot_change_later_replays(self):
        expected = build_fixture(FUTURE, '1m')
        mutated = build_fixture(FUTURE, '1m')
        mutated['history']['bars'][0]['close'] = -123
        mutated['instrument']['metadata']['synthetic'] = False
        mutated['history']['dataset']['provenance']['sessions'].clear()
        mutated['updates'][0]['bar']['close'] = 999
        self.assertEqual(build_fixture(FUTURE, '1m'), expected)

    def test_host_timezone_and_global_random_state_do_not_change_bytes(self):
        expected = hashlib.sha256((FIXTURES / 'series.json').read_bytes()).hexdigest()
        for zone, seed in (('UTC0', '1'), ('EST5EDT', '98765')):
            env = dict(os.environ, TZ=zone, PYTHONHASHSEED=seed)
            env['PYTHONPATH'] = os.pathsep.join([str(ROOT), str(ROOT / 'python')])
            command = ('import hashlib, random, sys; random.seed(int(sys.argv[1])); '
                       'from providers.simulated import fixture_bytes; '
                       'print(hashlib.sha256(fixture_bytes()).hexdigest())')
            result = subprocess.run([sys.executable, '-c', command, seed], cwd=ROOT, env=env,
                                    capture_output=True, text=True, check=True, timeout=10)
            self.assertEqual(result.stdout.strip(), expected)


if __name__ == '__main__':
    unittest.main()
