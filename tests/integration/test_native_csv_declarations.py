import csv
import json
from io import StringIO
from pathlib import Path
import unittest

from providers.local_folder import CsvReviewRequired, check_complete_csv, inspect_folder_csv, preview_csv
from tests.integration.test_local_folder_preview import FUTURES_COLUMNS, FUTURES_HEADER, FUTURES_MAPPING


ROOT = Path(__file__).resolve().parents[2]
HEADER = next(csv.reader([FUTURES_HEADER.decode().strip()]))
ROW = ['DEMO2609', '20240719 21:05:00', '22-07-2024', '101', '105', '96', '102', '150', '99', '800']
LATER = [*ROW[:1], '20240719 21:10:00', *ROW[2:]]
DAILY = {**FUTURES_MAPPING, 'instrument_kind': 'equity', 'timeframe': '1d', 'timestamp_format': '%Y%m%d',
         'daily_start_time': '09:30:00'}
ROLLING = {**FUTURES_MAPPING, 'instrument_kind': 'continuous_future', 'roll_method': 'VolumeRoll-A'}
# Independent cases cover both source economics and the way source timestamps acquire meaning.
CONFLICTS = {
    'instrument_kind': 'equity', 'venue': FUTURES_MAPPING['venue'].swapcase(),
    'timeframe': '5M', 'calendar_id': 'another-calendar', 'session_scope': 'day-only',
    'timezone': 'UTC', 'quantity_unit': 'lots', 'price_adjustment': 'split_adjusted', 'roll_method': 'volumeroll-a',
    'record_kind': 'quote_snapshot', 'volume_semantics': 'cumulative', 'timestamp_convention': 'bar_end',
    'timestamp_format': '%Y%m%d %H:%m:%S', 'trading_day_format': '%m-%d-%Y', 'daily_start_time': '09:30:01',
}


def source(header, *rows):
    output = StringIO(newline='')
    csv.writer(output, lineterminator='\n').writerows([header, *rows])
    return output.getvalue().encode()


class NativeCsvDeclarationTests(unittest.TestCase):
    def test_every_formal_field_has_a_reviewed_role_and_negative_case(self):
        definitions = json.loads((ROOT / 'contracts/schemas/protocol.schema.json').read_text())['$defs']
        policy = json.loads((ROOT / 'contracts/market-data-v1.json').read_text())['onboarding']['native_csv_source_declarations']
        series = set(definitions['PreviewSeries']['properties'])
        rules = set(policy['bar_rules'])
        config = set(policy['mapping_configuration'])
        self.assertEqual(config, {'version', 'revision', 'encoding', 'delimiter', 'columns'})
        self.assertTrue(series.isdisjoint(rules) and config.isdisjoint(series | rules))
        self.assertEqual(series | rules | config, set(definitions['CsvMappingV1']['properties']))
        self.assertEqual(series | rules, set(CONFLICTS))
        self.assertEqual(set(definitions['CsvColumnMappingV1']['properties']),
                         {'symbol', 'timestamp', 'trading_day', 'open', 'high', 'low', 'close', 'volume'})

    def test_late_conflicting_declarations_keep_source_values_and_prevent_publication(self):
        for field, wrong in CONFLICTS.items():
            with self.subTest(field=field):
                mapping = DAILY if field == 'daily_start_time' else ROLLING if field == 'roll_method' else FUTURES_MAPPING
                first, second = list(ROW), list(LATER)
                if field == 'daily_start_time':
                    first[1], second[1] = '20240718', '20240719'
                content = source([*HEADER, field.replace('_', ' ').title()],
                                 [*first, mapping[field]], [*second, wrong])
                sampled, = preview_csv(content, mapping, row_limit=1)['rows']
                self.assertEqual(sampled['normalized']['close'], 102)
                rejected = preview_csv(content, mapping)['rows'][1]
                self.assertEqual(rejected['raw_fields'][-1], wrong)
                self.assertIsNone(rejected['normalized'])
                issue, = rejected['issues']
                self.assertEqual((issue['field'], issue['code']), (field, field + '_mismatch'))
                for verify in (check_complete_csv, inspect_folder_csv):
                    with self.assertRaises(CsvReviewRequired) as failure:
                        verify(content, mapping)
                    self.assertEqual(failure.exception.issue, issue)
                    self.assertEqual(failure.exception.locator, {'record_number': 2, 'line_start': 3, 'line_end': 3})

    def test_exact_identifiers_and_explicitly_supported_equivalent_forms(self):
        header = [*HEADER, 'Record Kind', 'INSTRUMENT_KIND', 'timestamp-convention', 'volume semantics', 'quantity unit', 'price adjustment']
        content = source(header, [*ROW, 'Native OHLCV', 'DATED-FUTURE', 'Bar Start', 'PER-BAR', ' Contracts ', 'RAW'])
        self.assertEqual(check_complete_csv(content, FUTURES_MAPPING)['bars'][0]['bar']['volume'], 150)
        for field, identifier in [('venue', 'VenueX'), ('calendar_id', 'CalendarV2'), ('session_scope', 'NightX'),
                                  ('quantity_unit', 'VendorQuantity'), ('price_adjustment', 'VendorAdjustment')]:
            with self.subTest(field=field):
                mapping = {**FUTURES_MAPPING, field: identifier}
                self.assertEqual(check_complete_csv(source([*HEADER, field], [*ROW, identifier]), mapping)['series'][field], identifier)
                with self.assertRaises(CsvReviewRequired) as failure:
                    check_complete_csv(source([*HEADER, field], [*ROW, identifier.swapcase()]), mapping)
                self.assertEqual(failure.exception.issue['field'], field)

    def test_date_only_civil_clock_and_effective_roll_are_conditional(self):
        row = [*ROW[:1], '20240719', *ROW[2:]]
        for equal in ['09:30', '09:30:00', '09:30:00.000']:
            with self.subTest(clock=equal):
                bar = check_complete_csv(source([*HEADER, 'daily_start_time'], [*row, equal]), DAILY)['bars'][0]['bar']
                self.assertEqual(bar['time_ms'], 1721352600000)
        for different in ['09:31', '09:30:00.001', '09:30+08:00']:
            with self.assertRaises(CsvReviewRequired):
                check_complete_csv(source([*HEADER, 'daily_start_time'], [*row, different]), DAILY)
        mapping = {**FUTURES_MAPPING, 'roll_method': 'unused-draft'}
        for no_method in ['', 'null', 'None']:
            checked = check_complete_csv(source([*HEADER, 'roll_method', 'daily_start_time'], [*ROW, no_method, '09:30']), mapping)
            self.assertIsNone(checked['series']['roll_method'])
            self.assertEqual(checked['bars'][0]['bar']['time_ms'], 1721394300000)
        with self.assertRaises(CsvReviewRequired):
            check_complete_csv(source([*HEADER, 'roll_method'], [*ROW, 'unused-draft']), mapping)

    def test_explicit_row_bindings_win_over_same_named_declarations(self):
        header = list(HEADER)
        columns = dict(FUTURES_COLUMNS)
        for field, name in [('symbol', 'record_kind'), ('timestamp', 'session_scope'), ('close', 'calendar_id'),
                            ('volume', 'daily_start_time')]:
            header[header.index(columns[field])] = name
            columns[field] = name
        mapping = {**FUTURES_MAPPING, 'columns': columns}
        extra = ['Calendar ID', 'vendor.calendar_id', 'revision', 'encoding', 'delimiter']
        content = source([*header, *extra], [*ROW, FUTURES_MAPPING['calendar_id'], 'opaque calendar', '2024', 'VendorLabel', 'semicolon'])
        preview = preview_csv(content, mapping)
        self.assertEqual({item['column'] for item in preview['raw_only_columns']}, {'Settlement', 'OpenInterest', *extra})
        self.assertEqual(preview['rows'][0]['raw_fields'][-4:], ['opaque calendar', '2024', 'VendorLabel', 'semicolon'])
        checked = check_complete_csv(content, mapping)
        self.assertEqual(checked['symbol'], 'DEMO2609')
        self.assertEqual(checked['bars'][0]['bar']['close'], 102)
        with self.assertRaises(CsvReviewRequired) as conflict:
            check_complete_csv(source([*header, *extra[:1]], [*ROW, 'another-calendar']), mapping)
        self.assertEqual(conflict.exception.issue['field'], 'calendar_id')
