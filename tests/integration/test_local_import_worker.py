import base64
from datetime import date, timedelta
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from providers.local_folder import MAX_PUBLICATION_RECORDS
from worker_runtime.local_import import run
from worker_runtime.protocol import decode_line


ROOT = Path(__file__).resolve().parents[2]
CHUNK_BYTES = 192 * 1024
MAX_BYTES = 4 * 1024 * 1024
REQUEST_ID = 'db056359-1861-433a-ab29-849db8b240b5'
FRAME_ID = {'version': 1, 'request_id': REQUEST_ID}
CSV_PREFIX = (
    'Ticker,Date,Open,High,Low,Close,Shares,Note\r\n'
    '000071,2026-01-06,8,10,7,9,200,样本\r\n'
    '000071,2026-01-07,8,10,7,11,200,超出最高价\r\n'
).encode()
MAPPING = {
    'version': 1,
    'revision': 'sample-v1',
    'encoding': 'utf-8',
    'delimiter': ',',
    'record_kind': 'native_ohlcv',
    'instrument_kind': 'equity',
    'venue': 'SYNTH',
    'timeframe': '1d',
    'timezone': 'Asia/Shanghai',
    'calendar_id': 'sample',
    'session_scope': 'regular',
    'quantity_unit': 'shares',
    'price_adjustment': 'unadjusted',
    'volume_semantics': 'per_bar',
    'timestamp_convention': 'bar_start',
    'timestamp_format': '%Y-%m-%d',
    'trading_day_format': '%Y-%m-%d',
    'daily_start_time': '09:30:00',
    'columns': {
        'symbol': 'Ticker', 'timestamp': 'Date', 'trading_day': 'Date',
        'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'volume': 'Shares',
    },
}


def line(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode() + b'\n'


def request(content, **header_fields):
    frames = [{
        **FRAME_ID, 'byte_count': len(content), 'sha256': sha256(content).hexdigest(),
        'mapping': MAPPING, 'row_limit': 2, **header_fields,
    }]
    for index, offset in enumerate(range(0, len(content), CHUNK_BYTES)):
        frames.append({
            **FRAME_ID, 'index': index,
            'data': base64.b64encode(content[offset:offset + CHUNK_BYTES]).decode(),
        })
    return frames


def complete_request(content, **header_fields):
    frames = request(content, complete=True, **header_fields)
    del frames[0]['row_limit']
    return frames


def source_row_request(content, record_number, **header_fields):
    frames = request(content, record_number=record_number, **header_fields)
    del frames[0]['row_limit']
    return frames


def worker(data):
    env = {key: value for key, value in os.environ.items() if key.upper() in {'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP'}}
    # Exercise the locked IANA data with no operating-system zone database.
    env['PYTHONTZPATH'] = ''
    env['PYTHONPATH'] = os.pathsep.join([str(ROOT), str(ROOT / 'python')])
    return subprocess.run(
        [sys.executable, '-m', 'worker_runtime.local_import'], cwd=ROOT, env=env,
        input=data, capture_output=True, timeout=10,
    )


class ShortWritingOutput(BytesIO):
    def __init__(self, *, stall_after=None, stalled_result=0):
        super().__init__()
        self.stall_after = stall_after
        self.stalled_result = stalled_result
        self.writes = 0

    def write(self, value):
        self.writes += 1
        if self.stall_after is not None and self.writes > self.stall_after:
            if self.writes > self.stall_after + 1:
                raise AssertionError('retried a write that made no progress')
            return self.stalled_result
        return super().write(value[:32_768])


class LocalImportWorkerTests(unittest.TestCase):
    def reply_frames(self, stdout):
        hello_line, *replies = stdout.splitlines(keepends=True)
        hello = decode_line(hello_line)
        self.assertEqual((hello['type'], hello['protocol_version'], hello['role']), ('hello', '1.1', 'market'))
        self.assertEqual(hello['capabilities'], [
            'market.previewLocalImport', 'market.checkLocalImport', 'market.inspectLocalFolderSource',
            'market.inspectLocalImportSourceRow',
        ])
        return replies

    def test_child_previews_normalized_and_rejected_rows_using_hash_of_all_chunks(self):
        content = CSV_PREFIX + b'\n' * (MAX_BYTES - len(CSV_PREFIX))
        frames = request(content)
        self.assertEqual(len(frames), 23)
        self.assertTrue(all(len(line(frame)) <= MAX_BYTES for frame in frames))
        process = worker(b''.join(map(line, frames)))
        self.assertEqual((process.returncode, process.stderr), (0, b''))
        reply, = self.reply_frames(process.stdout)
        self.assertIn('样本'.encode(), reply)
        result = json.loads(reply)
        self.assertEqual({key: result[key] for key in FRAME_ID}, FRAME_ID)
        self.assertTrue(result['ok'])
        preview = result['preview']
        self.assertEqual(preview['file_sha256'], sha256(content).hexdigest())
        self.assertTrue(preview['truncated'])
        normalized, rejected = preview['rows']
        self.assertEqual(normalized['symbol'], '000071')
        self.assertEqual(normalized['normalized']['close'], 9)
        self.assertEqual(normalized['normalized']['trading_day'], '2026-01-06')
        self.assertEqual(normalized['raw_fields'][-1], '样本')
        self.assertEqual((rejected['record_number'], rejected['line_start'], rejected['line_end']), (2, 3, 3))
        self.assertIsNone(rejected['normalized'])
        self.assertEqual(rejected['issues'][0]['code'], 'ohlc_bounds')

    def test_complete_series_includes_rows_beyond_the_preview_and_duplicate_locators(self):
        header = b'Ticker,Date,Open,High,Low,Close,Shares,"Note\r\nfrom source"\r\n'
        dates = [date(2024, 1, 1) + timedelta(days=index) for index in range(503)]
        rows = [f'000071,{day},8,10,7,9,,raw\r\n'.encode() for day in reversed(dates)]
        rows += [b'000071,2024-01-01,8,10,7,9,,"retained\r\nunknown"\r\n']
        content = header + b''.join(rows)
        preview_process = worker(b''.join(map(line, request(content, row_limit=500))))
        self.assertEqual((preview_process.returncode, preview_process.stderr), (0, b''))
        preview_reply, = self.reply_frames(preview_process.stdout)
        preview = json.loads(preview_reply)['preview']
        self.assertTrue(preview['truncated'])
        self.assertEqual(len(preview['rows']), 500)
        self.assertEqual(preview['rows'][-1]['record_number'], 500)

        process = worker(b''.join(map(line, complete_request(content))))
        self.assertEqual((process.returncode, process.stderr), (0, b''))
        reply, = self.reply_frames(process.stdout)
        checked = json.loads(reply)['publication']
        self.assertEqual(checked['file_sha256'], sha256(content).hexdigest())
        self.assertEqual(checked['file_sha256'], preview['file_sha256'])
        self.assertEqual(checked['mapping_revision'], MAPPING['revision'])
        self.assertEqual(checked['header'][-1], 'Note\r\nfrom source')
        self.assertEqual(checked['symbol'], '000071')
        self.assertEqual(checked['source_record_count'], 504)
        self.assertEqual(len(checked['bars']), 503)
        first, last = checked['bars'][0], checked['bars'][-1]
        self.assertEqual(first['bar']['trading_day'], str(dates[0]))
        self.assertIsNone(first['bar']['volume'])
        self.assertEqual(first['locators'], [
            {'record_number': 503, 'line_start': 505, 'line_end': 505},
            {'record_number': 504, 'line_start': 506, 'line_end': 507},
        ])
        self.assertEqual(last['bar']['trading_day'], str(dates[-1]))
        self.assertEqual(last['locators'], [{'record_number': 1, 'line_start': 3, 'line_end': 3}])

    def test_source_row_uses_reviewed_encoding_and_counts_quoted_records(self):
        content = (
            'Ticker,Date,Open,High,Low,Close,Shares,"备注\r\n原始单位"\r\n'
            '000071,2026-01-06,8,10,7,9,200,"前一行\r\n不应被返回"\r\n'
            '000071,2026-01-07,8,10,7,09.00,,"手, 带有""引号""\r\n下一行"\r\n'
        ).encode('gb18030')
        frames = source_row_request(content, 2, mapping={**MAPPING, 'encoding': 'gb18030'})
        process = worker(b''.join(map(line, frames)))
        self.assertEqual((process.returncode, process.stderr), (0, b''))
        reply, = self.reply_frames(process.stdout)
        self.assertEqual(json.loads(reply), {
            **FRAME_ID, 'ok': True, 'source_row': {
                'source_sha256': sha256(content).hexdigest(),
                'locator': {'record_number': 2, 'line_start': 5, 'line_end': 6},
                'header': ['Ticker', 'Date', 'Open', 'High', 'Low', 'Close', 'Shares', '备注\r\n原始单位'],
                'raw_fields': ['000071', '2026-01-07', '8', '10', '7', '09.00', '', '手, 带有"引号"\r\n下一行'],
            },
        })

    def test_source_row_has_a_separate_frame_limit_and_fails_closed(self):
        header = b'Ticker,Date,Open,High,Low,Close,Shares,Note\r\n'
        row = b'000071,2026-01-06,8,10,7,9,200,valid\r\n'
        oversized = b'000071,2026-01-06,8,10,7,9,200,' + b'x' * (64 * 1024) + b'\r\n'
        for content, number, expected in (
            (header + oversized, 1, 'RESOURCE_LIMIT'),
            (header + row, 2, 'DATA_UNAVAILABLE'),
            (header + b'"unterminated', 1, 'DATA_UNAVAILABLE'),
            (header + row, 10_001, 'INVALID_ARGUMENT'),
        ):
            with self.subTest(number=number, expected=expected):
                output = BytesIO()
                self.assertEqual(run(BytesIO(b''.join(map(line, source_row_request(content, number)))), output), 0)
                reply, = self.reply_frames(output.getvalue())
                self.assertEqual(json.loads(reply), {**FRAME_ID, 'ok': False, 'error': expected})

    def test_frozen_row_reports_every_conflicting_declaration_without_reinterpreting_bytes(self):
        content = (
            'Ticker,Date,Open,High,Low,Close,Shares,timeframe,Time Zone,volume-semantics,Vendor\r\n'
            '000071,2026-01-06,8,10,7,9,200,1d,Asia/Shanghai, Per Bar ,original\r\n'
            '000071,2026-01-07,8,10,7,9,350,5m,UTC,cumulative,"first\r\nsecond"\r\n'
        ).encode()
        for record, expected in (
            (1, []),
            (2, [
                {'column_index': 7, 'field': 'timeframe', 'reviewed_value': '1d'},
                {'column_index': 8, 'field': 'timezone', 'reviewed_value': 'Asia/Shanghai'},
                {'column_index': 9, 'field': 'volume_semantics', 'reviewed_value': 'per_bar'},
            ]),
        ):
            with self.subTest(record=record):
                frames = source_row_request(content, record, review_declarations=True)
                output = BytesIO()
                self.assertEqual(run(BytesIO(b''.join(map(line, frames))), output), 0)
                reply, = self.reply_frames(output.getvalue())
                result = json.loads(reply)
                self.assertIs(result['ok'], True, result)
                row = result['source_row']
                self.assertEqual(row['declaration_conflicts'], expected)
                self.assertEqual(row['source_sha256'], sha256(content).hexdigest())
                if record == 2:
                    self.assertEqual(row['locator'], {'record_number': 2, 'line_start': 3, 'line_end': 4})
                    self.assertEqual(row['raw_fields'][7:], ['5m', 'UTC', 'cumulative', 'first\r\nsecond'])

        frames = source_row_request(content, 2, review_declarations=True,
                                    mapping={**MAPPING, 'timezone': 'Legacy/RemovedZone'})
        output = BytesIO()
        self.assertEqual(run(BytesIO(b''.join(map(line, frames))), output), 0)
        reply, = self.reply_frames(output.getvalue())
        row = json.loads(reply)['source_row']
        self.assertIsNone(row['declaration_conflicts'])
        self.assertEqual(row['raw_fields'][8], 'UTC')

    def test_complete_series_holds_late_invalid_or_ambiguous_rows(self):
        content = b'Ticker,Date,Open,High,Low,Close,Shares,Note\n' + b'000071,2026-01-06,8,10,7,9,200,test\n' * 501
        cases = {
            'ohlc_bounds': b'000071,2026-01-07,8,10,7,11,200,test\n',
            'conflicting_bar': b'000071,2026-01-06,8,10,7,10,200,test\n',
            'multiple_symbols': b'000072,2026-01-07,8,10,7,9,200,test\n',
            'csv_syntax': b'000071,2026-01-07,8,10,7,9,200,"unterminated',
            'row_width': b'000071,2026-01-07,8,10,7,9,200\n',
        }
        for code, later_row in cases.items():
            with self.subTest(issue=code):
                result = BytesIO()
                self.assertEqual(run(BytesIO(b''.join(map(line, complete_request(content + later_row)))), result), 0)
                reply, = self.reply_frames(result.getvalue())
                held = json.loads(reply)
                self.assertFalse(held['ok'])
                self.assertEqual(held['error'], 'REVIEW_REQUIRED')
                self.assertEqual(held['issue']['code'], code)
                self.assertEqual(held['locator'], {'record_number': 502, 'line_start': 503, 'line_end': 503})
                self.assertNotIn('publication', held)

    def test_folder_inspection_retains_competing_values_and_complete_publication_refuses_them(self):
        content = (
            b'Ticker,Date,Open,High,Low,Close,Shares,Note\r\n'
            b'000071,2026-01-06,5,8,3,6,200,first\r\n'
            b'000071,2026-01-06,5,8,3,7,200,"competing\r\nvalue"\r\n'
            b'000071,2026-01-06,5,8,3,6,200,same\r\n'
        )
        frames = request(content, inspect_refresh=True)
        del frames[0]['row_limit']
        process = worker(b''.join(map(line, frames)))
        self.assertEqual((process.returncode, process.stderr), (0, b''))
        reply, = self.reply_frames(process.stdout)
        result = json.loads(reply)
        self.assertTrue(result['ok'])
        self.assertNotIn('publication', result)
        checked = result['inspection']
        self.assertEqual(checked['file_sha256'], sha256(content).hexdigest())
        self.assertEqual(checked['source_record_count'], 3)
        self.assertEqual([entry['bar']['close'] for entry in checked['bars']], [6, 7])
        self.assertEqual([entry['locators'] for entry in checked['bars']], [
            [
                {'record_number': 1, 'line_start': 2, 'line_end': 2},
                {'record_number': 3, 'line_start': 5, 'line_end': 5},
            ],
            [{'record_number': 2, 'line_start': 3, 'line_end': 4}],
        ])

        process = worker(b''.join(map(line, complete_request(content))))
        self.assertEqual((process.returncode, process.stderr), (0, b''))
        reply, = self.reply_frames(process.stdout)
        held = json.loads(reply)
        self.assertFalse(held['ok'])
        self.assertEqual(held['error'], 'REVIEW_REQUIRED')
        self.assertEqual(held['issue']['code'], 'conflicting_bar')
        self.assertEqual(held['locator'], {'record_number': 2, 'line_start': 3, 'line_end': 4})
        self.assertNotIn('publication', held)
        self.assertNotIn('inspection', held)

    def test_complete_series_fails_closed_at_the_source_record_budget(self):
        content = b'Ticker,Date,Open,High,Low,Close,Shares,Note\n' + b'000071,2026-01-06,8,10,7,9,200,test\n' * (MAX_PUBLICATION_RECORDS + 1)
        result = BytesIO()
        self.assertEqual(run(BytesIO(b''.join(map(line, complete_request(content)))), result), 0)
        reply, = self.reply_frames(result.getvalue())
        self.assertEqual(json.loads(reply), {**FRAME_ID, 'ok': False, 'error': 'RESOURCE_LIMIT'})

    def test_complete_series_requires_the_exact_source_digest_and_no_preview_limit(self):
        header, chunk = complete_request(CSV_PREFIX, sha256=sha256(CSV_PREFIX[:-1] + b' ').hexdigest())
        invalid_headers = [header, {
            **header, 'sha256': sha256(CSV_PREFIX).hexdigest(), 'row_limit': 1,
        }]
        for invalid in invalid_headers:
            result = BytesIO()
            self.assertEqual(run(BytesIO(line(invalid) + line(chunk)), result), 0)
            reply, = self.reply_frames(result.getvalue())
            self.assertEqual(json.loads(reply), {**FRAME_ID, 'ok': False, 'error': 'INVALID_ARGUMENT'})

    def test_short_writes_drain_the_entire_reply(self):
        note = b'X' * 90_000
        content = b'Ticker,Note\nSYNTH,' + note + b'\n'
        mapping = {**MAPPING, 'record_kind': 'quote_snapshot'}
        output = ShortWritingOutput()
        status = run(BytesIO(b''.join(map(line, request(content, mapping=mapping)))), output)
        self.assertEqual(status, 0)
        reply, = self.reply_frames(output.getvalue())
        self.assertTrue(reply.endswith(b'\n'))
        self.assertGreater(output.writes, 2)
        result = json.loads(reply)
        self.assertEqual(result['preview']['file_sha256'], sha256(content).hexdigest())
        self.assertEqual(result['preview']['rows'][0]['raw_fields'], ['SYNTH', note.decode()])

    def test_later_decode_failure_returns_header_and_whole_file_digest(self):
        content = CSV_PREFIX + b'\xff\n'
        process = worker(b''.join(map(line, request(content, row_limit=1))))
        self.assertEqual((process.returncode, process.stderr), (0, b''))
        reply, = self.reply_frames(process.stdout)
        result = json.loads(reply)
        self.assertTrue(result['ok'])
        preview = result['preview']
        self.assertEqual(preview['header'], ['Ticker', 'Date', 'Open', 'High', 'Low', 'Close', 'Shares', 'Note'])
        self.assertEqual(preview['file_sha256'], sha256(content).hexdigest())
        self.assertEqual(preview['rows'], [])
        self.assertEqual(preview['issues'][0]['code'], 'decode_failed')

    def test_zero_progress_is_terminal_for_hello_and_partial_reply(self):
        content = b'Ticker,Note\nSYNTH,' + b'X' * 90_000 + b'\n'
        mapping = {**MAPPING, 'record_kind': 'quote_snapshot'}
        data = b''.join(map(line, request(content, mapping=mapping)))
        for stall_after in (0, 2):
            for stalled_result in (0, None):
                with self.subTest(stall_after=stall_after, stalled_result=stalled_result):
                    output = ShortWritingOutput(stall_after=stall_after, stalled_result=stalled_result)
                    self.assertEqual(run(BytesIO(data), output), 2)
                    self.assertEqual(output.writes, stall_after + 1)
                    if stall_after:
                        partial, = self.reply_frames(output.getvalue())
                        self.assertEqual(len(partial), 32_768)
                        self.assertFalse(partial.endswith(b'\n'))
                    else:
                        self.assertEqual(output.getvalue(), b'')

    def test_child_can_emit_an_exact_limit_reply_including_newline(self):
        count = 40
        mapping = {**MAPPING, 'record_kind': 'quote_snapshot'}

        def payload(notes):
            content = b'Ticker,Note\n' + b''.join(b'SYNTH,' + note + b'\n' for note in notes)
            self.assertLessEqual(len(content), MAX_BYTES)
            return b''.join(map(line, request(content, mapping=mapping, row_limit=count)))

        small = BytesIO()
        self.assertEqual(run(BytesIO(payload([b''] * count)), small), 0)
        reply, = self.reply_frames(small.getvalue())
        note_size, extra = divmod(MAX_BYTES - len(reply), count)
        notes = [b'X' * (note_size + (index < extra)) for index in range(count)]
        process = worker(payload(notes))
        self.assertEqual((process.returncode, process.stderr), (0, b''))
        reply, = self.reply_frames(process.stdout)
        self.assertEqual(len(reply), MAX_BYTES)
        self.assertTrue(reply.endswith(b'\n'))
        preview = json.loads(reply)['preview']
        self.assertEqual([row['raw_fields'][1] for row in preview['rows']], [note.decode() for note in notes])
        self.assertFalse(preview['truncated'])

    def test_wrong_digest_unordered_or_noncanonical_chunks_and_extra_frames_are_rejected(self):
        header, chunk = request(b'a')
        cases = {
            'wrong digest': [{**header, 'sha256': '1' * 64}, chunk],
            'other request': [header, {**chunk, 'request_id': '7abcea86-3664-4310-ab0d-df48fb758020'}],
            'unordered': [header, {**chunk, 'index': 1}],
            'noncanonical padding bits': [header, {**chunk, 'data': 'YR=='}],
            'extra frame': [header, chunk, chunk],
            'missing chunk': [header],
            'oversized source': [{**header, 'byte_count': MAX_BYTES + 1}],
            'untrusted path': [{**header, 'path': 'unused'}, chunk],
        }
        for reason, frames in cases.items():
            with self.subTest(reason=reason):
                result = BytesIO()
                self.assertEqual(run(BytesIO(b''.join(map(line, frames))), result), 0)
                reply, = self.reply_frames(result.getvalue())
                self.assertEqual(json.loads(reply), {
                    **FRAME_ID, 'ok': False, 'error': 'INVALID_ARGUMENT',
                })

    def test_worker_never_returns_an_overflowing_response_or_a_partial_preview(self):
        count = 40
        content = b'Ticker,Note\n' + (b'SYNTH,' + b'\x01' * 25_000 + b'\n') * count
        self.assertLess(len(content), MAX_BYTES)
        mapping = {**MAPPING, 'record_kind': 'quote_snapshot'}
        process = worker(b''.join(map(line, request(content, mapping=mapping, row_limit=count))))
        self.assertEqual((process.returncode, process.stderr), (0, b''))
        reply, = self.reply_frames(process.stdout)
        self.assertLessEqual(len(reply), MAX_BYTES)
        self.assertEqual(json.loads(reply), {**FRAME_ID, 'ok': False, 'error': 'RESOURCE_LIMIT'})

    def test_unparseable_or_uncorrelated_headers_produce_no_reply(self):
        header, _ = request(b'a')
        for data in (
            line({**header, 'request_id': 'not-a-request-id'}),
            line(header)[:-1],
            b'\xff\n',
            b'{' + b' ' * MAX_BYTES + b'}\n',
            b'{"request_id":' + json.dumps(REQUEST_ID).encode() + b',"request_id":"duplicate"}\n',
        ):
            result = BytesIO()
            self.assertEqual(run(BytesIO(data), result), 2)
            self.assertEqual(self.reply_frames(result.getvalue()), [])
