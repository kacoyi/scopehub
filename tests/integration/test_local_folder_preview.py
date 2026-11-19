from codecs import BOM_UTF8
from datetime import datetime, timezone
from hashlib import sha256
import unittest

from providers.local_folder import CsvReviewRequired, check_complete_csv, preview_csv


FUTURES_COLUMNS = {
    'symbol': 'Contract',
    'timestamp': 'LocalStart',
    'trading_day': 'SessionDate',
    'open': 'Open',
    'high': 'High',
    'low': 'Low',
    'close': 'Close',
    'volume': 'Quantity',
}
FUTURES_MAPPING = {
    'version': 1,
    'revision': 'synthetic-futures-2',
    'encoding': 'utf-8',
    'delimiter': ',',
    'record_kind': 'native_ohlcv',
    'columns': FUTURES_COLUMNS,
    'instrument_kind': 'dated_future',
    'venue': 'XSGE',
    'timezone': 'Asia/Shanghai',
    'calendar_id': 'synthetic-futures-calendar-v2',
    'session_scope': 'day-and-night',
    'timeframe': '5m',
    'quantity_unit': 'contracts',
    'price_adjustment': 'raw',
    'volume_semantics': 'per_bar',
    'timestamp_convention': 'bar_start',
    'timestamp_format': '%Y%m%d %H:%M:%S',
    'trading_day_format': '%d-%m-%Y',
}
FUTURES_HEADER = (
    b'Contract,LocalStart,SessionDate,Open,High,Low,Close,Quantity,Settlement,OpenInterest\n'
)


class LocalFolderPreviewTests(unittest.TestCase):
    def test_current_and_previous_settlement_aliases_never_supply_close(self):
        for column in ('PrevSettlementPrice', 'previous_settle_price', 'Prior Settlement', 'CurrentSettlementPrice', 'Today Settle', '昨结算价', '今结算价'):
            with self.subTest(column=column):
                content = (
                    f'Contract,LocalStart,SessionDate,Open,High,Low,Close,Quantity,{column}\n'
                    'DEMO2609,20240719 21:05:00,22-07-2024,101,105,96,102,7,99\n'
                ).encode()
                corrected = preview_csv(content, FUTURES_MAPPING)
                self.assertEqual(corrected['rows'][0]['normalized']['close'], 102)
                self.assertEqual(corrected['rows'][0]['raw_fields'][-1], '99')

                mapping = {**FUTURES_MAPPING, 'columns': {**FUTURES_COLUMNS, 'close': column}}
                reviewed = preview_csv(content, mapping)
                row, = reviewed['rows']
                self.assertIsNone(row['normalized'])
                self.assertEqual(row['raw_fields'][6:], ['102', '7', '99'])
                self.assertEqual(reviewed['issues'][0]['code'], 'auxiliary_column')
                self.assertEqual(reviewed['issues'][0]['field'], 'close')
                self.assertIn('settlement price', reviewed['issues'][0]['message'])

    def test_explicit_bar_closure_keeps_incomplete_and_ambiguous_records_raw(self):
        content = FUTURES_HEADER.rstrip(b'\n') + b',is_closed\n' + (
            b'DEMO2609,20240719 21:05:00,22-07-2024,101,105,96,102,7,99,800,false\n'
            b'DEMO2609,20240719 21:10:00,22-07-2024,101,105,96,102,7,99,800,\n'
            b'DEMO2609,20240719 21:15:00,22-07-2024,101,105,96,102,7,99,800,maybe\n'
            b'DEMO2609,20240719 21:20:00,22-07-2024,101,105,96,102,7,99,800,true\n'
        )
        preview = preview_csv(content, FUTURES_MAPPING)
        first, second, third, fourth = preview['rows']
        for row, original, code in (
            (first, 'false', 'bar_incomplete'), (second, '', 'bar_closure_ambiguous'),
            (third, 'maybe', 'bar_closure_ambiguous'),
        ):
            with self.subTest(original=original):
                self.assertEqual(row['raw_fields'][-1], original)
                self.assertIsNone(row['normalized'])
                self.assertEqual(row['issues'][0]['code'], code)
                self.assertEqual(row['issues'][0]['field'], 'is_closed')
        self.assertTrue(fourth['normalized']['is_closed'])

    def test_conflicting_explicit_closure_flags_do_not_produce_a_closed_bar(self):
        content = FUTURES_HEADER.rstrip(b'\n') + b',is_closed,Bar Closed\n' + (
            b'DEMO2609,20240719 21:05:00,22-07-2024,101,105,96,102,7,99,800,true,0\n'
        )
        row, = preview_csv(content, FUTURES_MAPPING)['rows']
        self.assertIsNone(row['normalized'])
        self.assertEqual(row['raw_fields'][-2:], ['true', '0'])
        self.assertEqual(row['issues'][0]['code'], 'bar_closure_ambiguous')

    def test_explicit_market_declarations_must_match_the_reviewed_mapping(self):
        mapping = {**FUTURES_MAPPING, 'instrument_kind': 'equity', 'quantity_unit': 'shares', 'venue': 'XSHG'}
        header = FUTURES_HEADER.rstrip(b'\n') + b',Quantity Unit,Price_Adjustment,Venue\n'
        first_row = b'DEMO071,20240719 09:35:00,19-07-2024,101,105,96,102,7,99,800,'
        next_row = first_row.replace(b'09:35:00', b'09:40:00')
        accepted = header + first_row + b'shares,raw,XSHG\n'
        shown, = preview_csv(accepted, mapping)['rows']
        self.assertEqual(shown['normalized']['volume'], 7)
        self.assertEqual(check_complete_csv(accepted, mapping)['series']['price_adjustment'], 'raw')
        self.assertIsNotNone(preview_csv(header + first_row + b',,\n', mapping)['rows'][0]['normalized'])

        for unit, adjustment, venue, field in (
            ('lots', 'raw', 'XSHG', 'quantity_unit'),
            ('shares', 'split_adjusted', 'XSHG', 'price_adjustment'),
            ('shares', 'raw', 'XSHE', 'venue'),
        ):
            with self.subTest(field=field):
                declared = {'quantity_unit': unit, 'price_adjustment': adjustment, 'venue': venue}
                declaration = f'{unit},{adjustment},{venue}\n'.encode()
                changed = header + first_row + declaration
                preview = preview_csv(changed, mapping)
                row, = preview['rows']
                self.assertEqual(row['raw_fields'][-3:], [unit, adjustment, venue])
                self.assertIsNone(row['normalized'])
                self.assertEqual(row['issues'][0]['field'], field)
                self.assertIn(f'{field}_mismatch', [issue['code'] for issue in row['issues']])
                self.assertIn(declared[field], row['issues'][0]['message'])
                self.assertEqual(
                    {column['column'] for column in preview['raw_only_columns']},
                    {'Settlement', 'OpenInterest', 'Quantity Unit', 'Price_Adjustment', 'Venue'},
                )

                raw = preview_csv(changed, {**mapping, 'record_kind': 'unclassified'})
                self.assertEqual(raw['rows'][0]['raw_fields'], row['raw_fields'])
                self.assertIsNone(raw['rows'][0]['normalized'])
                self.assertIsNotNone(preview_csv(changed, {**mapping, **declared})['rows'][0]['normalized'])

                later = accepted + next_row + declaration
                sampled, = preview_csv(later, mapping, row_limit=1)['rows']
                self.assertEqual(sampled['normalized']['close'], 102)
                with self.assertRaises(CsvReviewRequired) as review:
                    check_complete_csv(later, mapping)
                self.assertEqual(review.exception.issue['field'], field)
                self.assertEqual(review.exception.issue['code'], f'{field}_mismatch')
                self.assertEqual(review.exception.locator, {
                    'record_number': 2, 'line_start': 3, 'line_end': 3,
                })

    def test_invalid_quantities_and_prices_are_reviewable_in_source_order(self):
        content = FUTURES_HEADER + (
            b'DEMO2609,20240719 21:05:00,22-07-2024,85.25,86.00,85.1,85.5,9007199254740993,90.5,764\n'
            b'DEMO2609,20240719 21:10:00,22-07-2024,85.25,86.00,85.1,87.0,120,90.5,764\n'
            b'DEMO2609,20240719 21:15:00,22-07-2024,85.25,NaN,85.1,85.5,120,90.5,764\n'
        )

        preview = preview_csv(content, FUTURES_MAPPING)

        self.assertEqual(preview['issues'], [])
        self.assertEqual([row['normalized'] for row in preview['rows']], [None, None, None])
        first, second, third = preview['rows']
        self.assertEqual(first['raw_fields'][7], '9007199254740993')
        self.assertEqual(first['issues'][0]['code'], 'numeric_precision')
        self.assertEqual(second['issues'][0]['code'], 'ohlc_bounds')
        self.assertEqual(third['issues'][0]['field'], 'high')

    def test_wide_records_keep_source_text_without_expanding_column_metadata(self):
        oversized_record = (b',' * 600) + b'\r\n'
        ordinary_row = b'DEMO2609,20240719 21:05:00,22-07-2024,85.25,86.00,85.1,85.5,5,90.5,764\n'

        invalid_header = preview_csv(oversized_record + ordinary_row, FUTURES_MAPPING)
        self.assertEqual(invalid_header['raw_header_text'], oversized_record.decode())
        self.assertEqual(invalid_header['raw_only_columns'], [])
        self.assertEqual(invalid_header['issues'][0]['code'], 'column_limit')
        self.assertTrue(invalid_header['truncated'])

        preview = preview_csv(FUTURES_HEADER + oversized_record + ordinary_row, FUTURES_MAPPING)
        first, second = preview['rows']
        self.assertEqual(first['raw_text'], oversized_record.decode())
        self.assertEqual(first['issues'][0]['code'], 'column_limit')
        self.assertEqual((second['record_number'], second['line_start']), (2, 3))
        self.assertIsNotNone(second['normalized'])

    def test_unavailable_timezone_keeps_raw_fields(self):
        content = FUTURES_HEADER + b'DEMO2609,20240719 21:05:00,22-07-2024,85.25,86.0,85.1,85.5,5,90.5,764\n'
        for timezone_key in ('localtime', 'X' * 260):
            with self.subTest(timezone_key=timezone_key):
                preview = preview_csv(content, {**FUTURES_MAPPING, 'timezone': timezone_key})
                row, = preview['rows']
                self.assertEqual(row['raw_fields'][:2], ['DEMO2609', '20240719 21:05:00'])
                self.assertIsNone(row['normalized'])
                self.assertEqual(preview['issues'][0]['code'], 'timezone_unavailable')

    def test_declared_gb18030_equity_daily_mapping_preserves_symbol_and_source(self):
        content = (
            '代码;日期;交易日;开盘;最高;最低;收盘;手数;备注\r\n'
            '009314;18/07/2024;20240718;12.20;12.95;12.10;12.64;375;"前一行\r\n后一行"\r\n'
            '009314;19/07/2024;20240719;12.70;12.72;12.65;12.66;;无成交量记录\r\n'
        ).encode('gb18030')
        mapping = {
            **FUTURES_MAPPING,
            'revision': 'synthetic-equity-7',
            'encoding': 'gb18030',
            'delimiter': ';',
            'columns': {
                'symbol': '代码', 'timestamp': '日期', 'trading_day': '交易日',
                'open': '开盘', 'high': '最高', 'low': '最低', 'close': '收盘', 'volume': '手数',
            },
            'instrument_kind': 'equity',
            'venue': 'XSHG',
            'calendar_id': 'synthetic-equity-calendar-v1',
            'session_scope': 'regular',
            'timeframe': '1d',
            'quantity_unit': 'lots',
            'price_adjustment': 'fixture-forward-adjustment-v2',
            'timestamp_format': '%d/%m/%Y',
            'trading_day_format': '%Y%m%d',
            'daily_start_time': '09:30:00',
        }

        preview = preview_csv(content, mapping)

        self.assertEqual(preview['issues'], [])
        self.assertEqual(preview['file_sha256'], sha256(content).hexdigest())
        self.assertEqual(preview['mapping_version'], 1)
        self.assertEqual(preview['mapping_revision'], 'synthetic-equity-7')
        self.assertEqual(preview['header'], ['代码', '日期', '交易日', '开盘', '最高', '最低', '收盘', '手数', '备注'])
        self.assertIsNotNone(preview['series'])
        self.assertEqual(preview['series']['quantity_unit'], 'lots')
        self.assertEqual(preview['series']['venue'], 'XSHG')
        self.assertEqual(preview['series']['price_adjustment'], 'fixture-forward-adjustment-v2')
        self.assertEqual(preview['series']['timezone'], 'Asia/Shanghai')
        first, second = preview['rows']
        self.assertEqual((first['record_number'], first['line_start'], first['line_end']), (1, 2, 3))
        self.assertEqual((second['record_number'], second['line_start'], second['line_end']), (2, 4, 4))
        self.assertEqual(first['raw_fields'], [
            '009314', '18/07/2024', '20240718', '12.20', '12.95', '12.10', '12.64', '375', '前一行\r\n后一行',
        ])
        self.assertEqual(first['symbol'], '009314')
        self.assertEqual(first['normalized'], {
            'time_ms': int(datetime(2024, 7, 18, 1, 30, tzinfo=timezone.utc).timestamp()) * 1000,
            'trading_day': '2024-07-18',
            'open': 12.20, 'high': 12.95, 'low': 12.10, 'close': 12.64,
            'volume': 375, 'is_closed': True,
        })
        self.assertEqual(second['raw_fields'][7], '')
        self.assertIsNotNone(second['normalized'])
        self.assertIsNone(second['normalized']['volume'])

    def test_native_future_night_bar_keeps_explicit_trading_day_and_literal_zero(self):
        content = FUTURES_HEADER + b'DEMO2609,20240719 21:05:00,22-07-2024,85.25,86.00,85.1,85.5,0,90.5,764\n'

        preview = preview_csv(content, FUTURES_MAPPING)

        self.assertEqual(preview['issues'], [])
        self.assertEqual(preview['series']['instrument_kind'], 'dated_future')
        self.assertEqual(preview['series']['quantity_unit'], 'contracts')
        row, = preview['rows']
        self.assertEqual(row['symbol'], 'DEMO2609')
        self.assertEqual(row['normalized'], {
            'time_ms': int(datetime(2024, 7, 19, 13, 5, tzinfo=timezone.utc).timestamp()) * 1000,
            'trading_day': '2024-07-22',
            'open': 85.25, 'high': 86, 'low': 85.1, 'close': 85.5,
            'volume': 0, 'is_closed': True,
        })
        self.assertEqual(row['raw_fields'][-2:], ['90.5', '764'])
        self.assertEqual({column['column'] for column in preview['raw_only_columns']}, {'Settlement', 'OpenInterest'})

    def test_quote_snapshots_stay_inspectable_in_same_time_source_order(self):
        content = (
            b'Contract,TradingDay,UpdateTime,LastPrice,VolumeTotal,BidPrice\n'
            b'DEMO2609,20240722,21:00:01,85.5,100,85.4\n'
            b'DEMO2609,20240722,21:00:01,85.6,102,85.5\n'
        )
        mapping = {
            'version': 1, 'revision': 'synthetic-quotes-1', 'encoding': 'utf-8',
            'delimiter': ',', 'record_kind': 'quote_snapshot',
        }

        preview = preview_csv(content, mapping)

        self.assertEqual(preview['file_sha256'], sha256(content).hexdigest())
        self.assertIsNone(preview['series'])
        first, second = preview['rows']
        self.assertEqual((first['record_number'], second['record_number']), (1, 2))
        self.assertEqual(first['raw_fields'], ['DEMO2609', '20240722', '21:00:01', '85.5', '100', '85.4'])
        self.assertEqual(second['raw_fields'], ['DEMO2609', '20240722', '21:00:01', '85.6', '102', '85.5'])
        self.assertIsNone(first['normalized'])
        self.assertIsNone(second['normalized'])
        issues = [*preview['issues'], *first['issues'], *second['issues']]
        self.assertTrue(any('snapshot' in issue['message'].lower() for issue in issues), issues)

    def test_settlement_cannot_substitute_for_close_and_cumulative_quantity_requires_review(self):
        values = b'DEMO2609,20240719 21:05:00,22-07-2024,85.25,86.00,85.1,85.5,120,85.7,764\n'
        settlement_only = FUTURES_HEADER.replace(b'Close,', b'') + values.replace(b',85.5,', b',')
        for content, mapping, reason in (
            (
                settlement_only,
                FUTURES_MAPPING,
                'close',
            ),
            (
                settlement_only,
                {**FUTURES_MAPPING, 'columns': {**FUTURES_COLUMNS, 'close': 'Settlement'}},
                'settlement',
            ),
            (
                FUTURES_HEADER + values,
                {**FUTURES_MAPPING, 'volume_semantics': 'cumulative'},
                'volume',
            ),
        ):
            with self.subTest(reason=reason):
                preview = preview_csv(content, mapping)
                row, = preview['rows']
                self.assertIsNone(row['normalized'])
                self.assertEqual(row['raw_fields'][-3:], ['120', '85.7', '764'])
                issues = [*preview['issues'], *row['issues']]
                self.assertTrue(any(
                    reason in str(issue['field']).lower() or reason in issue['message'].lower()
                    for issue in issues
                ), issues)

    def test_futures_date_only_is_inspectable_until_civil_start_is_supplied(self):
        content = FUTURES_HEADER + b'DEMO2609,20240722,22-07-2024,85.25,86.00,85.1,85.5,120,90.5,764\n'
        mapping = {**FUTURES_MAPPING, 'timeframe': '1d', 'timestamp_format': '%Y%m%d'}

        preview = preview_csv(content, mapping)

        row, = preview['rows']
        self.assertIsNone(row['normalized'])
        self.assertEqual(row['raw_fields'][1:3], ['20240722', '22-07-2024'])
        issues = [*preview['issues'], *row['issues']]
        self.assertTrue(any(
            'timestamp' in str(issue['field']).lower() or 'start' in issue['message'].lower()
            for issue in issues
        ), issues)

    def test_timezone_gap_and_ambiguous_fold_leave_raw_rows(self):
        content = FUTURES_HEADER + (
            b'DEMO2512,20240310 02:25:00,11-03-2024,85.25,86.00,85.1,85.5,120,85.7,764\n'
            b'DEMO2512,20241103 01:35:00,04-11-2024,85.25,86.00,85.1,85.5,120,85.7,764\n'
            b'DEMO2512,20241103 02:35:00,04-11-2024,85.25,86.00,85.1,85.5,120,85.7,764\n'
        )

        preview = preview_csv(content, {**FUTURES_MAPPING, 'timezone': 'America/New_York'})

        self.assertEqual(preview['issues'], [])
        gap, fold, ordinary = preview['rows']
        for row, raw_timestamp, reason in (
            (gap, '20240310 02:25:00', 'gap'),
            (fold, '20241103 01:35:00', 'ambiguous'),
        ):
            with self.subTest(reason=reason):
                self.assertEqual(row['raw_fields'][1], raw_timestamp)
                self.assertIsNone(row['normalized'])
                issue, = row['issues']
                self.assertEqual(issue['field'], 'timestamp')
                self.assertIn(reason, issue['message'].lower())
        self.assertEqual(ordinary['issues'], [])
        self.assertIsNotNone(ordinary['normalized'])

    def test_explicit_offset_selects_fall_back_occurrence_in_source_timezone(self):
        content = FUTURES_HEADER + (
            b'DEMO2512,20241103 01:35:00 -0400,04-11-2024,85.25,86.00,85.1,85.5,120,85.7,764\n'
            b'DEMO2512,20241103 01:35:00 -0500,04-11-2024,85.25,86.00,85.1,85.5,120,85.7,764\n'
            b'DEMO2512,20241103 01:35:00 -0600,04-11-2024,85.25,86.00,85.1,85.5,120,85.7,764\n'
        )
        mapping = {
            **FUTURES_MAPPING,
            'timezone': 'America/New_York',
            'timestamp_format': '%Y%m%d %H:%M:%S %z',
        }

        preview = preview_csv(content, mapping)

        early, late, wrong_offset = preview['rows']
        for row, utc_hour in ((early, 5), (late, 6)):
            self.assertEqual(row['issues'], [])
            self.assertIsNotNone(row['normalized'])
            expected = datetime(2024, 11, 3, utc_hour, 35, tzinfo=timezone.utc)
            self.assertEqual(row['normalized']['time_ms'], int(expected.timestamp()) * 1000)
            self.assertEqual(row['normalized']['trading_day'], '2024-11-04')
        self.assertIsNone(wrong_offset['normalized'])
        issue, = wrong_offset['issues']
        self.assertEqual(issue['field'], 'timestamp')
        self.assertIn('offset', issue['message'].lower())

    def test_bom_and_preview_limit_keep_exact_file_identity_and_review_changed_header(self):
        content = BOM_UTF8 + FUTURES_HEADER + (
            b'DEMO2609,20240719 21:05:00,22-07-2024,85.25,86.00,85.1,85.5,120,85.7,764\n'
            b'DEMO2609,20240719 21:10:00,22-07-2024,85.50,86.10,85.2,86.0,150,85.7,769\n'
        )
        mapping = {**FUTURES_MAPPING, 'encoding': 'utf-8-sig'}

        full = preview_csv(content, mapping)
        limited = preview_csv(content, mapping, row_limit=1)

        self.assertEqual(full['header'][0], 'Contract')
        self.assertEqual(full['issues'], [])
        self.assertFalse(full['truncated'])
        self.assertTrue(limited['truncated'])
        self.assertEqual(full['file_sha256'], sha256(content).hexdigest())
        self.assertEqual(limited['file_sha256'], full['file_sha256'])
        self.assertEqual(limited['rows'], full['rows'][:1])

        changed_content = content.replace(b',Close,', b',NativeClose,', 1)
        changed = preview_csv(changed_content, mapping, row_limit=1)
        self.assertNotEqual(changed['file_sha256'], full['file_sha256'])
        self.assertEqual(changed['rows'][0]['raw_fields'], limited['rows'][0]['raw_fields'])
        self.assertIsNone(changed['rows'][0]['normalized'])
        issue, = changed['issues']
        self.assertEqual(issue['field'], 'close')
        self.assertIn('missing', issue['message'].lower())

        corrected_mapping = {
            **mapping,
            'revision': 'synthetic-futures-3',
            'columns': {**FUTURES_COLUMNS, 'close': 'NativeClose'},
        }
        corrected = preview_csv(changed_content, corrected_mapping, row_limit=1)
        self.assertEqual(corrected['issues'], [])
        self.assertEqual(corrected['file_sha256'], changed['file_sha256'])
        self.assertEqual(corrected['mapping_revision'], 'synthetic-futures-3')
        self.assertEqual(corrected['rows'], limited['rows'])

    def test_decode_failure_after_a_complete_logical_header_preserves_the_decoded_columns(self):
        for encoding, delimiter, header, expected in (
            ('utf-8', ',', 'Ticker,"Cl,ose ""official""\nprice",Note\n',
             ['Ticker', 'Cl,ose "official"\nprice', 'Note']),
            ('utf-8-sig', ';', '\ufeff代码;"收;盘\r\n标签";交易日\r\n',
             ['\ufeff代码', '收;盘\r\n标签', '交易日']),
            ('gb18030', ';', '代码;"收;盘\r标签";交易日\r',
             ['代码', '收;盘\r标签', '交易日']),
        ):
            with self.subTest(encoding=encoding):
                valid = (header + delimiter.join(['000071', '5', '2026-11-19']) + '\n').encode(encoding)
                content = valid + b'\xff\n'
                mapping = {**FUTURES_MAPPING, 'encoding': encoding, 'delimiter': delimiter}

                preview = preview_csv(content, mapping, row_limit=1)

                self.assertEqual(preview['file_sha256'], sha256(content).hexdigest())
                self.assertEqual(preview['header'], expected)
                self.assertIsNone(preview['raw_header_text'])
                issue, = preview['issues']
                self.assertEqual(issue['code'], 'decode_failed')
                self.assertIn(f'byte {len(valid)}', issue['message'])
                self.assertEqual(preview['rows'], [])
                self.assertIsNone(preview['series'])

    def test_a_decodable_prefix_does_not_prove_a_complete_valid_header(self):
        for content in (
            b'Ticker,Close\xff\r\n',
            b'Ticker,"Close\n\xff"\r\n',
            b'Ticker,"Close"extra\r\n\xff',
            b'Ticker,Close,Close\r\n\xff',
            b'Ticker,' + b'X,' * 512 + b'Close\r\n\xff',
        ):
            with self.subTest(content=content[:40]):
                preview = preview_csv(content, FUTURES_MAPPING)
                self.assertEqual(preview['file_sha256'], sha256(content).hexdigest())
                self.assertEqual(preview['header'], [])
                self.assertEqual(preview['rows'], [])
                self.assertIn('decode_failed', [issue['code'] for issue in preview['issues']])
