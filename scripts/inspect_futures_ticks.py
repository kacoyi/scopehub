"""Read-only diagnostics for the supplied futures snapshot CSV dialect.

This is an offline investigation tool, not a market provider or exchange validator.
Its relative clock is explicit: it cannot recover ActionDay, a trading calendar,
unobserved trades, contract multipliers or continuous-contract rollover rules.
"""

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import sys


HEADER = [
    'TradingDay', 'InstrumentID', 'UpdateTime', 'UpdateMillisec', 'LastPrice',
    'Volume', 'BidPrice1', 'BidVolume1', 'AskPrice1', 'AskVolume1', 'AveragePrice',
    'Turnover', 'OpenInterest', 'UpperLimitPrice', 'LowerLimitPrice',
]
NUMERIC_FIELDS = HEADER[4:]
INTEGER_FIELDS = {'Volume', 'BidVolume1', 'AskVolume1'}
PRICE_FIELDS = {'LastPrice', 'BidPrice1', 'AskPrice1', 'AveragePrice',
                'UpperLimitPrice', 'LowerLimitPrice'}


@lru_cache(maxsize=86400)
def clock_seconds(value):
    if len(value) != 8 or value[2] != ':' or value[5] != ':':
        raise ValueError('Expected HH:MM:SS')
    parts = value.split(':')
    if not all(part.isascii() and part.isdigit() for part in parts):
        raise ValueError('Expected numeric clock components')
    hour, minute, second = (int(part) for part in parts)
    if not (0 <= hour < 24 and 0 <= minute < 60 and 0 <= second < 60):
        raise ValueError('Clock is out of range')
    return hour * 3600 + minute * 60 + second


@lru_cache(maxsize=3660)
def trading_day(value):
    if len(value) != 8 or not value.isascii() or not value.isdigit():
        raise ValueError('Expected YYYYMMDD')
    datetime.strptime(value, '%Y%m%d')
    return value


def usable_number(value, field):
    try:
        number = int(value) if field in INTEGER_FIELDS else float(value)
    except ValueError:
        return None, 'malformed'
    # This diagnostic threshold identifies extreme missing-value placeholders;
    # it is not an exchange price limit or an inferred contract specification.
    if isinstance(number, float) and not math.isfinite(number):
        return None, 'nonfinite'
    if abs(number) >= 1e100:
        return None, 'sentinel'
    if number < 0:
        return None, 'negative'
    if number == 0 and field in PRICE_FIELDS:
        return None, 'zero_unavailable'
    return number, None


def new_bar(minute, price):
    return {'minute': minute, 'open': price, 'high': price, 'low': price,
            'close': price, 'known_volume_delta': 0, 'volume': 0,
            'price_observations': 0, 'flags': []}


def aggregate_bars(bars, minutes=None):
    """Aggregate observed bars; None means one relative trading-day bar.

    Time buckets are anchored to the configured relative clock, not an exchange
    session calendar. Unknown volume remains unknown when aggregating.
    """
    grouped = {}
    for source in bars:
        key = source['minute'] // minutes * minutes if minutes else 0
        if key not in grouped:
            grouped[key] = new_bar(key, source['open'])
        bar = grouped[key]
        bar['high'] = max(bar['high'], source['high'])
        bar['low'] = min(bar['low'], source['low'])
        bar['close'] = source['close']
        bar['known_volume_delta'] += source['known_volume_delta']
        bar['volume'] = (None if bar['volume'] is None or source['volume'] is None
                         else bar['volume'] + source['volume'])
        bar['price_observations'] += source['price_observations']
        bar['flags'] = sorted(set(bar['flags']) | set(source['flags']))
    return list(grouped.values())


def inspect_file(path, session_start=18 * 3600, include_series=False, assigned_trading_day=None):
    if not 0 <= session_start < 86400:
        raise ValueError('Session start is outside the relative clock')
    if assigned_trading_day is not None:
        trading_day(assigned_trading_day)
    before = path.stat()
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    result = {'file': path.name, 'bytes': before.st_size, 'sha256': digest,
              'rows': 0, 'issues': {}, 'numeric_issues': {}, 'examples': {},
              'assigned_trading_day': assigned_trading_day}
    issues, numeric_issues = Counter(), Counter()
    days, instruments, millis = Counter(), Counter(), Counter()
    previous = None
    first = last = None
    last_trade = None
    known_volume_delta = 0
    largest_volume_delta = 0
    unchanged_volume_rows = 0
    volume_jump_rows = 0
    valid_prices = 0
    min_price = max_price = None
    ratio_min = ratio_max = None
    ratio_count = 0
    gaps = []
    bars, intraday = {}, {}

    def issue(name, line):
        issues[name] += 1
        result['examples'].setdefault(name, line)

    try:
        with path.open(encoding='utf-8-sig', newline='') as stream:
            reader = csv.reader(stream, strict=True)
            result['header'] = next(reader, [])
            if result['header'] != HEADER:
                issue('unexpected_header', 1)
            else:
                for row in reader:
                    result['rows'] += 1
                    line = reader.line_num
                    if len(row) != len(HEADER):
                        issue('column_count', line)
                        previous = None
                        continue
                    try:
                        day = trading_day(row[0])
                        seconds = clock_seconds(row[2])
                        ms = int(row[3])
                        if not 0 <= ms < 1000 or not row[1].strip():
                            raise ValueError('Invalid milliseconds or identity')
                    except ValueError:
                        issue('identity_or_time', line)
                        previous = None
                        continue
                    days[day] += 1
                    instruments[row[1]] += 1
                    millis[ms] += 1
                    relative = ((seconds - session_start) % 86400) * 1000 + ms
                    numbers = {}
                    for field, value in zip(NUMERIC_FIELDS, row[4:], strict=True):
                        numbers[field], error = usable_number(value, field)
                        if error:
                            numeric_issues[f'{field}:{error}'] += 1
                    price, volume = numbers['LastPrice'], numbers['Volume']
                    turnover = numbers['Turnover']
                    effective_day = assigned_trading_day or day
                    point = {'trading_day': effective_day, 'source_trading_day': day,
                             'source_row': line, 'instrument': row[1],
                             'time': row[2], 'millisecond': ms,
                             'relative_ms': relative, 'volume': volume,
                             'turnover': turnover, 'last_price': price}
                    if first is None:
                        first = point
                    last = point
                    if price is not None:
                        valid_prices += 1
                        min_price = price if min_price is None else min(min_price, price)
                        max_price = price if max_price is None else max(max_price, price)
                    if price and volume and turnover is not None:
                        ratio = turnover / volume / price
                        ratio_min = ratio if ratio_min is None else min(ratio_min, ratio)
                        ratio_max = ratio if ratio_max is None else max(ratio_max, ratio)
                        ratio_count += 1
                    delta = None
                    same_identity = previous and previous['trading_day'] == effective_day and previous['instrument'] == row[1]
                    if same_identity:
                        elapsed = relative - previous['relative_ms']
                        if elapsed < 0:
                            issue('relative_time_regression', line)
                        elif elapsed == 0:
                            issue('same_timestamp', line)
                        elif elapsed > 60000:
                            gaps.append((elapsed, previous['time'], row[2]))
                            gaps = sorted(gaps, reverse=True)[:5]
                        if row == previous['raw']:
                            issue('adjacent_duplicate_row', line)
                        if volume is not None and previous['volume'] is not None:
                            delta = volume - previous['volume']
                            if delta < 0:
                                issue('volume_regression', line)
                                delta = None
                            else:
                                known_volume_delta += delta
                                largest_volume_delta = max(largest_volume_delta, delta)
                                unchanged_volume_rows += delta == 0
                                volume_jump_rows += delta > 1
                                if delta > 0:
                                    last_trade = point
                        if turnover is not None and previous['turnover'] is not None:
                            if turnover < previous['turnover']:
                                issue('turnover_regression', line)
                                if math.isclose(turnover, previous['turnover'], rel_tol=1e-12, abs_tol=1e-6):
                                    issue('turnover_roundoff_regression', line)
                            elif delta == 0 and turnover != previous['turnover']:
                                issue('turnover_change_without_volume', line)
                        if delta == 0 and price != previous['last_price']:
                            issue('price_change_without_volume', line)
                    elif volume == 0:
                        delta = 0
                    if include_series:
                        minute = relative // 60000
                        intraday[minute] = {**point, 'minute': minute,
                                           'open_interest': numbers['OpenInterest'],
                                           'average_price_raw': numbers['AveragePrice']}
                        # A first nonzero counter is a baseline, not proof that
                        # all those trades happened during this minute.
                        active = (delta is not None and delta > 0) or (previous is None and volume and price)
                        if active or (minute in bars and delta is None):
                            if minute not in bars:
                                bars[minute] = new_bar(minute, price)
                            bar = bars[minute]
                            if price is not None:
                                if bar['open'] is None:
                                    bar.update(open=price, high=price, low=price)
                                bar['high'] = max(bar['high'], price)
                                bar['low'] = min(bar['low'], price)
                                bar['close'] = price
                                bar['price_observations'] += 1
                            else:
                                bar['flags'].append('unobserved_trade_price')
                            if delta is None:
                                bar['volume'] = None
                                bar['flags'].append('unknown_counter_baseline')
                            else:
                                bar['known_volume_delta'] += delta
                                if bar['volume'] is not None:
                                    bar['volume'] += delta
                            bar['flags'] = sorted(set(bar['flags']))
                    previous = {**point, 'raw': row}
    except (UnicodeError, csv.Error) as error:
        issue('unreadable_csv', result['rows'] + 2)
        result['read_error'] = str(error)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        issue('source_changed_during_inspection', 0)
    result.update(issues=dict(issues), numeric_issues=dict(numeric_issues),
                  trading_days=dict(days), instruments=dict(instruments),
                  milliseconds=dict(millis), first=first, last=last,
                  last_volume_increase=last_trade,
                  known_volume_delta=known_volume_delta,
                  largest_volume_delta=largest_volume_delta,
                  unchanged_volume_rows=unchanged_volume_rows,
                  volume_jump_rows=volume_jump_rows, valid_price_rows=valid_prices,
                  observed_price_range=[min_price, max_price],
                  turnover_per_volume_to_last_price={'min': ratio_min, 'max': ratio_max, 'count': ratio_count},
                  largest_clock_gaps=[{'milliseconds': gap, 'from': a, 'to': b} for gap, a, b in gaps])
    if len(days) > 1:
        result['issues']['multiple_source_trading_days'] = 1
    if len(instruments) > 1:
        result['issues']['mixed_instruments'] = 1
    if result['rows'] == 0:
        result['issues']['empty_file'] = 1
    if include_series:
        blockers = [name for name in ['unexpected_header', 'column_count', 'identity_or_time',
                    'relative_time_regression', 'volume_regression', 'mixed_instruments',
                    'unreadable_csv', 'source_changed_during_inspection', 'empty_file'] if name in result['issues']]
        if any(name.startswith('Volume:') for name in numeric_issues):
            blockers.append('invalid_volume')
        if len(days) > 1 and assigned_trading_day is None:
            blockers.append('multiple_source_trading_days')
        if any(bar['open'] is None or 'unobserved_trade_price' in bar['flags'] for bar in bars.values()):
            blockers.append('unobserved_trade_price')
        result['reconstruction'] = {
            'status': 'blocked' if blockers else 'observed_only', 'blockers': blockers,
            'assigned_trading_day': assigned_trading_day,
            'source_trading_days': sorted(days), 'session_start_seconds': session_start,
        }
        if not blockers:
            minute_bars = list(bars.values())
            result['reconstruction'].update(
                intraday=list(intraday.values()), bars_1m=minute_bars,
                bars_5m=aggregate_bars(minute_bars, 5),
                bars_1d=aggregate_bars(minute_bars),
                caveats=['Observed last prices at cumulative-volume increases only; intervening trades are unavailable.',
                         'Nonzero initial volume is not allocated to the first minute.',
                         'Trading-day assignment is an explicit investigation assumption; raw labels are preserved.',
                         'Turnover and AveragePrice remain raw diagnostics, not validated cash amounts or normalized price averages.',
                         'No session filtering, auction folding, absolute dates, empty-minute filling or official OHLC claim.'])
    return result


def inspect_directory(source, output, samples=(), session_start=18 * 3600, assigned_trading_day=None):
    source, output = source.resolve(), output.resolve()
    if output == source or output.is_relative_to(source):
        raise ValueError('Output must be outside the source directory')
    paths = sorted(source.glob('*.csv'))
    if not paths:
        raise ValueError('No CSV inputs found')
    unknown = set(samples) - {path.name for path in paths}
    if unknown:
        raise ValueError(f'Unknown sample names: {sorted(unknown)}')
    # Never overwrite an earlier diagnostic or follow a link to owner data.
    output.mkdir(parents=True, exist_ok=False)
    inspector_digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    files = []
    for index, path in enumerate(paths, 1):
        if path.is_symlink() or not path.is_file():
            raise ValueError(f'Input is not a regular file: {path.name}')
        report = inspect_file(path, session_start, path.name in samples, assigned_trading_day)
        reconstruction = report.pop('reconstruction', None)
        if reconstruction is not None:
            target = output / f'{path.stem}.reconstruction.json'
            target.write_text(json.dumps(reconstruction, indent=2, allow_nan=False) + '\n', encoding='utf-8')
            report['reconstruction'] = {k: reconstruction[k] for k in ['status', 'blockers']}
            report['reconstruction']['file'] = target.name
        files.append(report)
        if index % 25 == 0 or index == len(paths):
            print(f'Inspected {index}/{len(paths)} files; {sum(item["rows"] for item in files)} rows', file=sys.stderr, flush=True)
    duplicates = defaultdict(list)
    issues, numeric = Counter(), Counter()
    for item in files:
        duplicates[item['sha256']].append(item['file'])
        issues.update(item['issues'])
        numeric.update(item['numeric_issues'])
    result = {
        'format_version': 1, 'scope': 'local_snapshot_structure_not_source_accuracy',
        'inspector_sha256': inspector_digest,
        'session_start_seconds': session_start,
        'assigned_trading_day': assigned_trading_day,
        'clock_basis': 'relative to the configured evening anchor; no absolute ActionDay inference',
        'summary': {'files': len(files), 'bytes': sum(item['bytes'] for item in files),
                    'rows': sum(item['rows'] for item in files),
                    'unique_content_files': len(duplicates),
                    'unique_content_rows': sum(next(item['rows'] for item in files if item['sha256'] == digest)
                                               for digest in duplicates),
                    'issues': dict(issues), 'numeric_issues': dict(numeric)},
        'duplicate_content_groups': [names for names in duplicates.values() if len(names) > 1],
        'files': files,
    }
    (output / 'inspection.json').write_text(json.dumps(result, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--sample', action='append', default=[])
    parser.add_argument('--session-start', default='18:00:00')
    parser.add_argument('--assigned-trading-day', help='Explicit one-day investigation assumption (YYYYMMDD); raw dates remain in the report')
    args = parser.parse_args()
    try:
        result = inspect_directory(args.source, args.output, args.sample, clock_seconds(args.session_start), args.assigned_trading_day)
    except (OSError, ValueError) as error:
        parser.exit(1, f'Inspection failed: {error}\n')
    print(json.dumps(result['summary'], indent=2))


if __name__ == '__main__':
    main()
