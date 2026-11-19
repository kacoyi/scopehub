import base64
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

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


def worker(data):
    env = {key: value for key, value in os.environ.items() if key.upper() in {'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP'}}
    env['PYTHONPATH'] = os.pathsep.join([str(ROOT), str(ROOT / 'python')])
    return subprocess.run(
        [sys.executable, '-m', 'worker_runtime.local_import'], cwd=ROOT, env=env,
        input=data, capture_output=True, timeout=10,
    )


class LocalImportWorkerTests(unittest.TestCase):
    def reply_frames(self, stdout):
        hello_line, *replies = stdout.splitlines(keepends=True)
        hello = decode_line(hello_line)
        self.assertEqual((hello['type'], hello['protocol_version'], hello['role']), ('hello', '1.1', 'market'))
        self.assertEqual(hello['capabilities'], ['market.previewLocalImport'])
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
