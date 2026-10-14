"""Independent counterexamples for local snapshot inspection, not exchange data."""

import contextlib
import csv
import io
from pathlib import Path
import tempfile
import unittest

from scripts.inspect_futures_ticks import (
    HEADER, aggregate_bars, clock_seconds, inspect_directory, inspect_file,
)


def row(time, price, volume, **changes):
    record = dict(zip(HEADER, [
        '20260109', 'TEST2601', time, 0, price, volume, price, 1,
        price, 2, 100, volume * 100, 50, 200, 1,
    ], strict=True))
    record.update(changes)
    return [record[field] for field in HEADER]


class FuturesTickInspectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()

    def write(self, rows, name='sample.csv'):
        path = self.source / name
        with path.open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(HEADER)
            writer.writerows(rows)
        return path

    def test_night_midnight_day_and_independent_ohlcv_oracle(self):
        path = self.write([
            row('20:59:00', 99, 0),  # A previous last price is not a new trade.
            row('21:00:00', 10, 2), row('21:00:30', 12, 5),
            row('21:04:00', 9, 6), row('00:00:00', 11, 8),
            row('09:00:00', 11, 8), row('09:01:00', 13, 10),
            row('15:20:00', 99, 10),  # A late snapshot must not extend trade OHLC.
        ])
        result = inspect_file(path, include_series=True)
        series = result['reconstruction']
        self.assertEqual(series['status'], 'observed_only')
        self.assertNotIn('relative_time_regression', result['issues'])
        bars = series['bars_1m']
        self.assertEqual([bar['minute'] for bar in bars], [180, 184, 360, 901])
        self.assertEqual([(b['open'], b['high'], b['low'], b['close'], b['volume']) for b in bars],
                         [(10, 12, 10, 12, 5), (9, 9, 9, 9, 1), (11, 11, 11, 11, 2), (13, 13, 13, 13, 2)])
        daily = series['bars_1d'][0]
        self.assertEqual([daily[k] for k in ['open', 'high', 'low', 'close', 'volume']], [10, 13, 9, 13, 10])
        self.assertEqual(series['bars_5m'][0]['volume'], 6)
        self.assertEqual(series['bars_5m'][0]['low'], 9)
        self.assertEqual(sum(b['known_volume_delta'] for b in bars), result['known_volume_delta'])
        self.assertEqual(result['last_volume_increase']['time'], '09:01:00')

    def test_initial_counter_is_unallocated_and_unknown_propagates(self):
        result = inspect_file(self.write([row('20:59:00', 100, 40), row('21:00:00', 110, 43)]), include_series=True)
        first, second = result['reconstruction']['bars_1m']
        self.assertIsNone(first['volume'])
        self.assertEqual(first['known_volume_delta'], 0)
        self.assertEqual(second['volume'], 3)
        self.assertIsNone(result['reconstruction']['bars_1d'][0]['volume'])
        self.assertEqual(result['known_volume_delta'], 3)

    def test_same_timestamp_updates_preserve_source_order_and_duplicate_adds_no_volume(self):
        initial = row('09:00:00', 100, 0)
        update = row('09:00:00', 101, 5)
        result = inspect_file(self.write([initial, update, update, row('09:00:00', 102, 7)]), include_series=True)
        self.assertEqual(result['issues']['same_timestamp'], 3)
        self.assertEqual(result['issues']['adjacent_duplicate_row'], 1)
        bar = result['reconstruction']['bars_1m'][0]
        self.assertEqual((bar['open'], bar['close'], bar['volume']), (101, 102, 7))

    def test_regressions_and_mixed_identities_block_reconstruction(self):
        for rows, blocker in [
            ([row('09:01:00', 10, 0), row('09:00:00', 11, 1)], 'relative_time_regression'),
            ([row('09:00:00', 10, 8), row('09:01:00', 11, 3)], 'volume_regression'),
            ([row('09:00:00', 10, 0), row('09:01:00', 11, 1, InstrumentID='OTHER')], 'mixed_instruments'),
            ([row('09:00:00', 10, 0), row('09:01:00', 11, 1, TradingDay='20260110')], 'multiple_source_trading_days'),
        ]:
            with self.subTest(blocker=blocker):
                series = inspect_file(self.write(rows), include_series=True)['reconstruction']
                self.assertEqual(series['status'], 'blocked')
                self.assertIn(blocker, series['blockers'])
                self.assertNotIn('bars_1m', series)

    def test_explicit_day_assignment_preserves_raw_labels_and_boundary_volume(self):
        path = self.write([row('21:00:00', 10, 0, TradingDay='20260108'),
                           row('22:59:59', 11, 3, TradingDay='20260108'),
                           row('23:00:00', 12, 5), row('09:00:00', 13, 9)])
        self.assertEqual(inspect_file(path, include_series=True)['reconstruction']['status'], 'blocked')
        result = inspect_file(path, include_series=True, assigned_trading_day='20260109')
        series = result['reconstruction']
        self.assertEqual(series['status'], 'observed_only')
        self.assertEqual(series['source_trading_days'], ['20260108', '20260109'])
        self.assertEqual(result['first']['source_trading_day'], '20260108')
        self.assertEqual(result['first']['trading_day'], '20260109')
        self.assertEqual(series['bars_1d'][0]['volume'], 9)
        self.assertEqual(result['known_volume_delta'], 9)

    def test_turnover_regression_is_retained_without_corrupting_price_volume_bars(self):
        result = inspect_file(self.write([row('09:00:00', 10, 1, Turnover=5000),
                                          row('09:01:00', 11, 2, Turnover=4000)]), include_series=True)
        self.assertEqual(result['issues']['turnover_regression'], 1)
        self.assertNotIn('turnover_roundoff_regression', result['issues'])
        self.assertEqual(result['reconstruction']['bars_1m'][1]['volume'], 1)

    def test_unavailable_quotes_are_not_prices_and_trade_holes_are_visible(self):
        result = inspect_file(self.write([
            row('09:00:00', 0, 0, BidPrice1=0, AskPrice1='1.7976931348623157e+308'),
            row('09:00:01', 'nan', 2), row('09:00:02', 10, 4),
        ]), include_series=True)
        self.assertEqual(result['numeric_issues']['LastPrice:zero_unavailable'], 1)
        self.assertEqual(result['numeric_issues']['LastPrice:nonfinite'], 1)
        self.assertEqual(result['numeric_issues']['AskPrice1:sentinel'], 1)
        self.assertEqual(result['known_volume_delta'], 4)
        self.assertIn('unobserved_trade_price', result['reconstruction']['blockers'])

    def test_invalid_counters_never_silently_bridge_missing_observations(self):
        result = inspect_file(self.write([
            row('09:00:00', 10, 0), row('09:01:00', 11, 1, Volume='broken'),
            row('09:02:00', 12, 2),
        ]), include_series=True)
        self.assertIn('invalid_volume', result['reconstruction']['blockers'])

    def test_full_file_duplicates_are_identified_without_double_counting_unique_rows(self):
        rows = [row('09:00:00', 10, 0), row('09:01:00', 11, 2)]
        a = self.write(rows, 'actual.csv')
        b = self.write(rows, 'alias.csv')
        before = {p: p.read_bytes() for p in [a, b]}
        with contextlib.redirect_stderr(io.StringIO()):
            result = inspect_directory(self.source, self.root / 'report', ['actual.csv'])
        self.assertEqual(result['summary']['rows'], 4)
        self.assertEqual(result['summary']['unique_content_rows'], 2)
        self.assertEqual(result['duplicate_content_groups'], [['actual.csv', 'alias.csv']])
        self.assertEqual(before, {p: p.read_bytes() for p in [a, b]})
        with self.assertRaises(ValueError):
            inspect_directory(self.source, self.source / 'output')
        with self.assertRaises(FileExistsError):
            inspect_directory(self.source, self.root / 'report')

    def test_malformed_empty_and_invalid_calendar_input_remains_visible(self):
        for rows, issue in [([], 'empty_file'), ([['short']], 'column_count'),
                            ([row('25:00:00', 10, 0)], 'identity_or_time'),
                            ([row('09:00:00', 10, 0, TradingDay='20260230')], 'identity_or_time')]:
            with self.subTest(issue=issue):
                result = inspect_file(self.write(rows), include_series=True)
                self.assertIn(issue, result['reconstruction']['blockers'])
        with self.assertRaises(ValueError):
            clock_seconds('+1:00:00')

    def test_empty_aggregation_does_not_fabricate_a_flat_day(self):
        self.assertEqual(aggregate_bars([]), [])
        result = inspect_file(self.write([row('09:00:00', 99, 0), row('15:00:00', 99, 0)]), include_series=True)
        self.assertEqual(result['reconstruction']['bars_1d'], [])


if __name__ == '__main__':
    unittest.main()
