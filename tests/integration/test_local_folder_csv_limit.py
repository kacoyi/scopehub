import csv
from hashlib import sha256
import unittest

from providers.local_folder import preview_csv


class LocalFolderCsvLimitTests(unittest.TestCase):
    def test_long_multiline_field_keeps_the_following_native_row(self):
        notes = 'x' * 131_070 + ',\r\n'
        self.assertEqual(len(notes), 131_073)
        content = (
            'Ticker,Date,Open,High,Low,Close,Shares,Notes\r\n'
            f'000417,2024-07-18,4,6,3,5,20,"{notes}"\r\n'
            '000417,2024-07-19,7,8,6,8,10,ordinary\r\n'
        ).encode('utf-8')
        mapping = {
            'version': 1, 'revision': 'long-notes-fixture', 'encoding': 'utf-8',
            'delimiter': ',', 'record_kind': 'native_ohlcv', 'instrument_kind': 'equity',
            'venue': 'TEST', 'timezone': 'UTC', 'calendar_id': 'fixture-calendar',
            'session_scope': 'regular', 'timeframe': '1d', 'quantity_unit': 'shares',
            'price_adjustment': 'raw', 'volume_semantics': 'per_bar',
            'timestamp_convention': 'bar_start', 'timestamp_format': '%Y-%m-%d',
            'trading_day_format': '%Y-%m-%d', 'daily_start_time': '09:30:00',
            'columns': {
                'symbol': 'Ticker', 'timestamp': 'Date', 'trading_day': 'Date',
                'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'volume': 'Shares',
            },
        }
        ambient_limit = csv.field_size_limit()

        preview = preview_csv(content, mapping)

        self.assertEqual(csv.field_size_limit(), ambient_limit)
        self.assertEqual(preview['file_sha256'], sha256(content).hexdigest())
        self.assertEqual(preview['issues'], [])
        self.assertFalse(preview['truncated'])
        first, following = preview['rows']
        self.assertEqual((first['line_start'], first['line_end']), (2, 3))
        self.assertEqual(first['raw_fields'][-1], notes)
        self.assertEqual(first['normalized']['close'], 5.0)
        self.assertEqual((following['record_number'], following['line_start']), (2, 4))
        self.assertEqual(following['raw_fields'][-1], 'ordinary')
        self.assertEqual(following['normalized']['close'], 8.0)
