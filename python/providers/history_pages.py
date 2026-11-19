"""Read one verifiable page from an immutable, explicit synthetic observation."""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from hashlib import sha256
import json
from struct import pack

from contracts.generated.protocol import (
    Bar, DatasetDescriptor, FrozenSnapshot, HistoryCursor, MarketHistoryPageInput,
    MarketHistoryPageOutput, MarketQualityState, SeriesKey, VisibleRange,
)
from providers.history import HistoryError
from providers.simulated import MINUTE_MS, PROVIDER_ID, TIMEFRAMES, build_fixture, instruments

# The protocol counts framing as well as payload. Account for the longest valid
# timestamp and UUIDs so every emitted response fits at its actual request time.
MAX_BARS = 100_000
PAGE_ENVELOPE = {
    'protocol_version': '1.1', 'message_id': '0' * 36, 'timestamp_ms': -9_007_199_254_740_991,
    'type': 'response', 'request_id': '0' * 36, 'operation': 'market.readHistoryPage',
    'ok': True, 'result': {}, 'error': None,
}


def _text(value: str) -> bytes:
    data = value.encode('utf-8')
    return pack('>I', len(data)) + data


def _float(value: float) -> bytes:
    return pack('>d', value if value else 0.0)


def _bar_bytes(bar: Bar) -> bytes:
    return (pack('>q', bar['time_ms']) + _text(bar['trading_day'])
            + b''.join(_float(value) for value in (bar['open'], bar['high'], bar['low'], bar['close']))
            + (b'\0' if bar['volume'] is None else b'\1' + _float(bar['volume']))
            + bytes((bar['is_closed'],)))


def _seed(series: SeriesKey, dataset: DatasetDescriptor, start: int, end: int, warmup: int, cutoff: int) -> bytes:
    values = (series['instrument_id'], series['provider_id'], series['timeframe'], series['price_adjustment'],
              series['session_scope'], series['calendar_id'], series['quantity_unit'],
              dataset['id'], dataset['revision'], dataset['timezone'])
    return sha256(b'ScopeHub/history-chain-v1\0' + b''.join(_text(value) for value in values)
                  + pack('>qqqq', start, end, warmup, cutoff)).digest()


def _encoded_size(value: object) -> int:
    return len(json.dumps(value, separators=(',', ':'), allow_nan=False, ensure_ascii=True).encode())


@dataclass
class FrozenHistory:
    dataset: DatasetDescriptor
    quality: MarketQualityState
    bars: list[Bar]
    snapshot: FrozenSnapshot
    prefixes: list[bytes]

    @classmethod
    def freeze(cls, dataset: DatasetDescriptor, quality: MarketQualityState, series: SeriesKey,
               all_bars: list[Bar], start: int, end: int, warmup: int) -> FrozenHistory:
        if start >= end or not 0 <= warmup <= MAX_BARS:
            raise HistoryError('INVALID_ARGUMENT')
        times = [bar['time_ms'] for bar in all_bars]
        first = max(0, bisect_left(times, start) - warmup)
        last = bisect_left(times, end)
        if last - first > MAX_BARS:
            raise HistoryError('RESOURCE_LIMIT')
        bars = all_bars[first:last]
        cutoff = quality['as_of_ms']
        prefixes = [_seed(series, dataset, start, end, warmup, cutoff)]
        for bar in bars:
            prefixes.append(sha256(prefixes[-1] + _bar_bytes(bar)).digest())
        snapshot: FrozenSnapshot = {
            'series': series, 'dataset_id': dataset['id'], 'dataset_revision': dataset['revision'],
            'from_ms': start, 'to_ms': end, 'warmup_bars': warmup, 'as_of_ms': cutoff,
            'content_hash': prefixes[-1].hex(),
        }
        return cls(dataset, quality, bars, snapshot, prefixes)

    def cursor(self, index: int) -> HistoryCursor | None:
        if index == len(self.bars):
            return None
        return {'snapshot_hash': self.snapshot['content_hash'], 'next_index': index,
                'prefix_hash': self.prefixes[index].hex(), 'previous_time_ms': self.bars[index-1]['time_ms']}

    def page(self, request: MarketHistoryPageInput, frame_budget: int) -> MarketHistoryPageOutput:
        pinned = request.get('snapshot')
        if pinned is not None and pinned != self.snapshot:
            raise HistoryError('SNAPSHOT_UNAVAILABLE')
        cursor = request.get('cursor')
        offset = 0
        if cursor is not None:
            offset = cursor['next_index']
            if pinned is None or not 0 < offset < len(self.bars) or cursor != self.cursor(offset):
                raise HistoryError('INVALID_ARGUMENT')
        maximum = request.get('page_size', MAX_BARS)
        if not 1 <= maximum <= MAX_BARS:
            raise HistoryError('INVALID_ARGUMENT')
        limit = min(len(self.bars), offset + maximum)
        output: MarketHistoryPageOutput = {
            'dataset': self.dataset, 'bars': [], 'snapshot': self.snapshot, 'quality_state': self.quality,
            'stream_position': None, 'next_cursor': None, 'total_bars': len(self.bars), 'offset': offset,
        }
        base_size = _encoded_size({**PAGE_ENVELOPE, 'result': output})
        sizes = [0]
        for index in range(offset, limit):
            sizes.append(sizes[-1] + _encoded_size(self.bars[index]) + int(index > offset))

        def fits(end: int) -> bool:
            next_cursor = self.cursor(end)
            cursor_bytes = 0 if next_cursor is None else _encoded_size(next_cursor) - len('null')
            return base_size + sizes[end-offset] + cursor_bytes + 1 <= frame_budget

        # A terminal page drops the cursor. Test it before searching partial pages.
        if not fits(limit):
            low, high = offset, limit
            while low + 1 < high:
                mid = (low + high) // 2
                if fits(mid):
                    low = mid
                else:
                    high = mid
            if low == offset:
                raise HistoryError('RESOURCE_LIMIT')
            limit = low
        output['next_cursor'] = self.cursor(limit)
        output['bars'] = self.bars[offset:limit]
        return output


def sample_quality(dataset: DatasetDescriptor, cutoff: int) -> MarketQualityState:
    provenance = dataset['provenance']
    observed = provenance.get('last_observed_ms')
    last = observed if isinstance(observed, int) and not isinstance(observed, bool) else None
    if last is not None and last > cutoff:
        raise HistoryError('OUTPUT_INVALID')
    state: MarketQualityState = {
        'as_of_ms': cutoff, 'last_observed_ms': last, 'session_state': 'unknown',
        'freshness': 'unknown' if last is None else 'stale' if dataset['quality'] == 'stale' else 'current',
        'connection': 'offline', 'coverage': 'unknown', 'gaps': [], 'source_kind': 'synthetic',
    }
    windows = provenance.get('sessions')
    if isinstance(windows, list) and windows:
        schedule: list[tuple[int, int]] = []
        for row in windows:
            if isinstance(row, dict):
                start, end = row.get('start_ms'), row.get('end_ms')
                if type(start) is int and type(end) is int and start < end:
                    schedule.append((start, end))
        if len(schedule) == len(windows):
            state['session_state'] = 'open' if any(start <= cutoff < end for start, end in schedule) else 'closed'
    missing = provenance.get('missing_minute_starts_ms')
    if isinstance(missing, list):
        minutes = [time for time in missing if type(time) is int]
        if len(minutes) != len(missing):
            return state
        gaps: list[VisibleRange] = []
        for time in sorted(set(minutes)):
            if gaps and time == gaps[-1]['to_ms']:
                gaps[-1]['to_ms'] = time + MINUTE_MS
            else:
                gaps.append({'from_ms': time, 'to_ms': time + MINUTE_MS})
        state['gaps'] = gaps
        state['coverage'] = 'gapped' if gaps else 'observed'
    return state


def execute_page(request: MarketHistoryPageInput, frame_budget: int) -> MarketHistoryPageOutput:
    series = request['series']
    if series['provider_id'] != PROVIDER_ID:
        raise HistoryError('PROVIDER_NOT_CONFIGURED')
    instrument = next((item for item in instruments() if item['id'] == series['instrument_id']), None)
    if instrument is None:
        raise HistoryError('DATA_UNAVAILABLE')
    if (series['timeframe'] not in TIMEFRAMES or series['price_adjustment'] != 'raw'
            or series['session_scope'] != 'sample-sessions' or series['calendar_id'] != instrument['calendar_id']
            or series['quantity_unit'] != instrument['quantity_unit']):
        raise HistoryError('UNSUPPORTED_CAPABILITY')
    fixture = build_fixture(series['instrument_id'], series['timeframe'])
    history = fixture['history']
    dataset = history['dataset']
    pinned = request.get('snapshot')
    if request.get('expected_revision', dataset['revision']) != dataset['revision']:
        raise HistoryError('SNAPSHOT_UNAVAILABLE')
    observed = dataset['provenance']['as_of_ms']
    assert isinstance(observed, int)
    cutoff = request.get('as_of_ms', pinned['as_of_ms'] if pinned is not None else observed)
    if cutoff != observed:
        update = next((event for event in fixture['updates'] if event['observed_at_ms'] == cutoff), None)
        if update is None:
            raise HistoryError('SNAPSHOT_UNAVAILABLE')
        history['bars'][-1] = update['bar']
        dataset['provenance']['as_of_ms'] = cutoff
        dataset['provenance']['last_observed_ms'] = cutoff
    quality = sample_quality(dataset, cutoff)
    frozen = FrozenHistory.freeze(dataset, quality, series, history['bars'],
                                  request['from_ms'], request['to_ms'], request['warmup_bars'])
    return frozen.page(request, frame_budget)
