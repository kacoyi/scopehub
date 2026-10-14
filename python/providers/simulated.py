"""Deterministic synthetic samples, without a wall clock or provider connection.

Regenerate the committed replay with:
uv run python -m python.providers.simulated --write tests/fixtures/market/series.json

Session windows are deliberately short synthetic samples, not exchange calendars.
Only observed minutes contribute prices; missing intervals stay missing and
incomplete volume is unknown, never an observed subtotal presented as a total.
"""

import argparse
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
import json
import math
from pathlib import Path
from random import Random
from typing import Literal, Sequence, TypedDict
from uuid import NAMESPACE_URL, uuid5

from contracts.generated.protocol import Bar, Instrument, MarketGetBarsOutput

MINUTE_MS = 60_000
GENERATOR = 'scopehub-synthetic-v1'
PROVIDER_ID = 'scopehub.synthetic'
TIMEFRAMES = ('1m', '5m', '1d')
Scenario = Literal['normal', 'gapped', 'stale']


@dataclass(frozen=True)
class Session:
    start_ms: int
    minutes: int
    trading_day: str

    @property
    def end_ms(self) -> int:
        return self.start_ms + self.minutes * MINUTE_MS


def _session(start_utc: str, trading_day: str) -> Session:
    return Session(int(datetime.fromisoformat(start_utc).timestamp()) * 1000, 10, trading_day)


@dataclass(frozen=True)
class Sample:
    instrument_id: str
    asset_class: str
    currency: str
    timezone: str
    price_tick: str
    initial_ticks: int
    seed: int
    sessions: tuple[Session, ...]

    def instrument(self) -> Instrument:
        return {
            'id': self.instrument_id, 'display_symbol': self.instrument_id,
            'name': f'Synthetic {self.instrument_id}', 'asset_class': self.asset_class,
            'venue': 'SIMULATED', 'currency': self.currency,
            'calendar_id': f'{self.instrument_id}.sample-calendar.v1',
            'timezone': self.timezone, 'price_tick': self.price_tick,
            'quantity_unit': 'contracts' if self.asset_class == 'future' else 'shares',
            'tradability': 'non_tradable',
            'metadata': {'synthetic': True, 'calendar_scope': 'Fixed sample windows; not an exchange calendar'},
            'provider_mappings': [{'provider_id': PROVIDER_ID, 'provider_symbol': self.instrument_id}],
        }


_SAMPLES = (
    Sample('SIM.CN.FUTURE.001', 'future', 'CNY', 'Asia/Shanghai', '0.2', 17500, 20241013, (
        _session('2024-01-02T13:00:00+00:00', '2024-01-03'),
        _session('2024-01-03T01:00:00+00:00', '2024-01-03'),
        _session('2024-01-03T13:00:00+00:00', '2024-01-04'),
        _session('2024-01-04T01:00:00+00:00', '2024-01-04'),
    )),
    Sample('SIM.CN.STOCK.001', 'stock', 'CNY', 'Asia/Shanghai', '0.01', 1200, 20241014, (
        _session('2024-01-03T01:30:00+00:00', '2024-01-03'),
        _session('2024-01-03T05:00:00+00:00', '2024-01-03'),
        _session('2024-01-04T01:30:00+00:00', '2024-01-04'),
        _session('2024-01-04T05:00:00+00:00', '2024-01-04'),
    )),
    Sample('SIM.US.STOCK.001', 'stock', 'USD', 'America/New_York', '0.01', 17500, 20241015, (
        _session('2024-01-03T14:30:00+00:00', '2024-01-03'),
        _session('2024-01-03T20:00:00+00:00', '2024-01-03'),
        _session('2024-01-04T14:30:00+00:00', '2024-01-04'),
        _session('2024-01-04T20:00:00+00:00', '2024-01-04'),
    )),
)


def _timestamp(value: int) -> bool:
    return type(value) is int and 0 <= value <= 9_007_199_254_740_991


def aggregate_minutes(
    bars: Sequence[Bar], timeframe: str, sessions: Sequence[Session], *, as_of_ms: int,
) -> list[Bar]:
    """Aggregate observed minutes using explicit sample sessions and trading days.

    Intraday keys are anchored at session open; daily keys are the first session
    of the assigned trading day, even when that session opens on the prior UTC
    date. Closure describes the interval, not its completeness. Missing minutes
    must also be reported in the enclosing dataset's quality/provenance.
    """
    if timeframe not in TIMEFRAMES or not _timestamp(as_of_ms) or not sessions:
        raise ValueError('Unsupported timeframe, observation time or empty calendar')
    slots: dict[int, Session] = {}
    day_bounds: dict[str, tuple[int, int]] = {}
    previous_end = -1
    previous_day = ''
    for session in sessions:
        if (not _timestamp(session.start_ms) or session.start_ms % MINUTE_MS
                or type(session.minutes) is not int or session.minutes <= 0
                or not _timestamp(session.end_ms) or session.start_ms < previous_end
                or date.fromisoformat(session.trading_day).isoformat() != session.trading_day
                or session.trading_day < previous_day):
            raise ValueError('Invalid or unordered sample sessions')
        previous_end, previous_day = session.end_ms, session.trading_day
        start, _ = day_bounds.get(session.trading_day, (session.start_ms, session.end_ms))
        day_bounds[session.trading_day] = (start, session.end_ms)
        for timestamp in range(session.start_ms, session.end_ms, MINUTE_MS):
            slots[timestamp] = session

    groups: dict[int, list[Bar]] = {}
    ends: dict[int, int] = {}
    observed_times: set[int] = set()

    def interval(timestamp: int, session: Session) -> tuple[int, int]:
        if timeframe == '1d':
            return day_bounds[session.trading_day]
        width = MINUTE_MS * (5 if timeframe == '5m' else 1)
        key = session.start_ms + (timestamp - session.start_ms) // width * width
        return key, min(key + width, session.end_ms)

    previous_time = -1
    for index, bar in enumerate(bars):
        timestamp = bar['time_ms']
        if not _timestamp(timestamp) or timestamp <= previous_time or timestamp > as_of_ms:
            raise ValueError('Minutes must be observed, unique and increasing')
        previous_time = timestamp
        matched_session = slots.get(timestamp)
        if matched_session is None or bar['trading_day'] != matched_session.trading_day:
            raise ValueError('Minute does not belong to its declared sample trading day')
        prices = [bar['open'], bar['high'], bar['low'], bar['close']]
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in prices):
            raise ValueError('Prices must be finite numbers')
        if not (bar['low'] <= min(bar['open'], bar['close']) <= max(bar['open'], bar['close']) <= bar['high']):
            raise ValueError('Invalid OHLC range')
        volume = bar['volume']
        if volume is not None and (type(volume) not in (int, float) or not math.isfinite(volume) or volume < 0):
            raise ValueError('Volume must be nonnegative or unknown')
        if (type(bar['is_closed']) is not bool
                or (bar['is_closed'] and timestamp + MINUTE_MS > as_of_ms)
                or (not bar['is_closed'] and index != len(bars) - 1)):
            raise ValueError('Only the final minute may be partial; closed minutes must have ended')
        key, end = interval(timestamp, matched_session)
        groups.setdefault(key, []).append(bar)
        ends[key] = end
        observed_times.add(timestamp)

    expected_counts: dict[int, int] = {}
    for timestamp, slot_session in slots.items():
        # A minute exactly at as_of has not elapsed; count it only if observed.
        if timestamp < as_of_ms or timestamp in observed_times:
            key, _ = interval(timestamp, slot_session)
            expected_counts[key] = expected_counts.get(key, 0) + 1

    result: list[Bar] = []
    for key, group in groups.items():
        volumes = [bar['volume'] for bar in group]
        result.append({
            'time_ms': key, 'trading_day': group[0]['trading_day'],
            'open': group[0]['open'], 'high': max(bar['high'] for bar in group),
            'low': min(bar['low'] for bar in group), 'close': group[-1]['close'],
            'volume': (None if None in volumes or len(group) != expected_counts[key]
                       else math.fsum(value for value in volumes if value is not None)),
            'is_closed': as_of_ms >= ends[key] and all(bar['is_closed'] for bar in group),
        })
    return result


class BarUpdate(TypedDict):
    observed_at_ms: int
    bar: Bar


class Fixture(TypedDict):
    scenario: Scenario
    instrument: Instrument
    history: MarketGetBarsOutput
    updates: list[BarUpdate]


def _minutes(sample: Sample) -> list[Bar]:
    random = Random(sample.seed)
    tick = Decimal(sample.price_tick)
    price = sample.initial_ticks
    bars: list[Bar] = []
    for session in sample.sessions:
        for timestamp in range(session.start_ms, session.end_ms, MINUTE_MS):
            close = price + random.randint(-4, 4)
            high = max(price, close) + random.randint(0, 3)
            low = min(price, close) - random.randint(0, 3)
            volume = float(random.randint(1, 20) * 10)
            bars.append({
                'time_ms': timestamp, 'trading_day': session.trading_day,
                'open': float(price * tick), 'high': float(high * tick),
                'low': float(low * tick), 'close': float(close * tick),
                'volume': None if len(bars) == 7 else (0.0 if len(bars) == 8 else volume),
                'is_closed': True,
            })
            price = close
    bars[-1]['is_closed'] = False
    return bars


def build_fixture(instrument_id: str, timeframe: str, scenario: Scenario = 'normal') -> Fixture:
    """Return an independent replay snapshot, with two same-key updates or a stall."""
    sample = next((value for value in _SAMPLES if value.instrument_id == instrument_id), None)
    if sample is None or timeframe not in TIMEFRAMES or scenario not in ('normal', 'gapped', 'stale'):
        raise ValueError('Unknown synthetic instrument, timeframe or scenario')
    minutes = _minutes(sample)
    missing = [bar['time_ms'] for index, bar in enumerate(minutes) if index in (2, 5, 6, 7, 8, 9)] if scenario == 'gapped' else []
    minutes = [bar for bar in minutes if bar['time_ms'] not in missing]
    observed_at = minutes[-1]['time_ms'] + 30_000
    as_of = observed_at + (30 * MINUTE_MS if scenario == 'stale' else 0)
    instrument = sample.instrument()
    revision = f'{GENERATOR}:{sample.seed}:{scenario}'
    identity = f'{sample.instrument_id}|{PROVIDER_ID}|{timeframe}|raw|{revision}'
    history: MarketGetBarsOutput = {
        'dataset': {
            'id': str(uuid5(NAMESPACE_URL, identity)), 'instrument_id': sample.instrument_id,
            'provider_id': PROVIDER_ID, 'mode': 'simulated', 'timeframe': timeframe,
            'price_adjustment': 'raw', 'timezone': sample.timezone,
            'calendar_id': instrument['calendar_id'], 'quantity_unit': instrument['quantity_unit'],
            'revision': revision, 'quality': 'fresh' if scenario == 'normal' else scenario,
            'provenance': {
                'synthetic': True, 'generator': GENERATOR, 'seed': sample.seed,
                'start_ms': sample.sessions[0].start_ms,
                'as_of_ms': as_of, 'last_observed_ms': observed_at,
                'missing_minute_starts_ms': [value for value in missing],
                'calendar_scope': 'Only the declared sample windows are represented; not an exchange calendar',
                'sessions': [
                    {'start_ms': session.start_ms, 'end_ms': session.end_ms, 'trading_day': session.trading_day}
                    for session in sample.sessions
                ],
            },
        },
        'bars': aggregate_minutes(minutes, timeframe, sample.sessions, as_of_ms=as_of),
    }
    updates: list[BarUpdate] = []
    if scenario != 'stale':
        initial = minutes[-1]
        running_high = initial['high']
        for step, ticks in enumerate((2, 1), start=1):
            updated = initial.copy()
            updated['close'] = float(Decimal(str(initial['close'])) + ticks * Decimal(sample.price_tick))
            running_high = max(running_high, updated['close'])
            updated['high'] = running_high
            updated['volume'] = None if initial['volume'] is None else initial['volume'] + step * 10
            updated['is_closed'] = step == 2
            at = observed_at + step * 15_000
            bar = aggregate_minutes([*minutes[:-1], updated], timeframe, sample.sessions, as_of_ms=at)[-1]
            updates.append({'observed_at_ms': at, 'bar': bar})
    return {'scenario': scenario, 'instrument': instrument, 'history': history, 'updates': updates}


def fixture_bytes() -> bytes:
    fixtures = [build_fixture(sample.instrument_id, timeframe) for sample in _SAMPLES for timeframe in TIMEFRAMES]
    edge_scenarios: tuple[Scenario, ...] = ('gapped', 'stale')
    fixtures.extend(build_fixture(_SAMPLES[0].instrument_id, timeframe, scenario)
                    for scenario in edge_scenarios for timeframe in TIMEFRAMES)
    bundle = {
        'schema_version': '1.0', 'generator': GENERATOR,
        'label': 'All data is synthetic; not real market quotes or exchange calendars.',
        'regenerate': 'uv run python -m python.providers.simulated --write tests/fixtures/market/series.json',
        'fixtures': fixtures,
    }
    return (json.dumps(bundle, indent=2, allow_nan=False) + '\n').encode('utf-8')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', type=Path, required=True)
    args = parser.parse_args()
    args.write.parent.mkdir(parents=True, exist_ok=True)
    args.write.write_bytes(fixture_bytes())


if __name__ == '__main__':
    main()
