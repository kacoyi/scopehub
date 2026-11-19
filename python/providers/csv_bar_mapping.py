"""Validate declared native bar mappings and normalize rows for offline preview."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
import math
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from contracts.generated.protocol import Bar, PreviewIssue, PreviewSeries


_FIELDS = ('symbol', 'timestamp', 'trading_day', 'open', 'high', 'low', 'close', 'volume')
_PRICES = ('open', 'high', 'low', 'close')
_SERIES_DECLARATIONS = {
    'quantityunit': 'quantity_unit',
    'priceadjustment': 'price_adjustment',
    'venue': 'venue',
}
_SETTLEMENT_NAMES = {name + suffix for name in ('settle', 'settlement') for suffix in ('', 'price', 'px')}
_CURRENT_SETTLEMENT = _SETTLEMENT_NAMES | {
    prefix + name for prefix in ('current', 'today', 'daily', 'final') for name in _SETTLEMENT_NAMES
} | {'结算', '结算价', '结算价格', '今结算', '今结算价', '今日结算价', '当日结算价'}
_PRIOR_SETTLEMENT = {
    prefix + name for prefix in ('pre', 'prev', 'previous', 'prior', 'last', 'yesterday') for name in _SETTLEMENT_NAMES
} | {'昨结算', '昨结算价', '昨日结算价', '前结算', '前结算价', '前结算价格', '上一结算价', '前一交易日结算价'}
_CLOSURE_NAMES = {'isclosed', 'barclosed', 'isbarclosed', 'barisclosed'}
_AUXILIARY_COLUMNS = {
    'lastprice': 'snapshot last price', 'averageprice': 'average price',
    'openinterest': 'open interest', '持仓量': 'open interest',
    'turnover': 'turnover', '成交额': 'turnover', 'multiplier': 'contract multiplier',
    'cumulativevolume': 'cumulative volume', 'cumvolume': 'cumulative volume',
}
_DATE_DIRECTIVES = {
    '%Y': r'[0-9]{4}', '%m': r'[0-9]{2}', '%d': r'[0-9]{2}', '%H': r'[0-9]{2}',
    '%M': r'[0-9]{2}', '%S': r'[0-9]{2}', '%f': r'[0-9]{1,6}',
    '%z': r'(?:Z|[+-][0-9]{2}:?[0-9]{2})', '%%': '%',
}
_DATE = {'%Y', '%m', '%d'}
_CLOCK = {'%H', '%M'}
_NUMBER = re.compile(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?')
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MILLISECOND = timedelta(milliseconds=1)


def preview_issue(code: str, field: str | None, message: str) -> PreviewIssue:
    return {'code': code, 'field': field, 'message': message}


class BarFieldError(ValueError):
    def __init__(self, issue: PreviewIssue) -> None:
        self.issue = issue
        super().__init__(issue['message'])


@dataclass(frozen=True)
class _DatePattern:
    template: str
    pattern: re.Pattern[str]
    date_only: bool

    def parse(self, value: str) -> datetime:
        if not self.pattern.fullmatch(value):
            raise ValueError('Value does not match the declared format.')
        return datetime.strptime(value, self.template)


def _date_pattern(template: object, *, day_only: bool) -> _DatePattern:
    if not isinstance(template, str) or not 1 <= len(template) <= 128:
        raise ValueError('Declare a date format with %Y, %m and %d.')
    directives = set()
    parts = []
    for part in re.findall(r'%[\s\S]?|[^%]+', template):
        if part.startswith('%'):
            if part not in _DATE_DIRECTIVES or (part != '%%' and part in directives):
                raise ValueError('Use each supported directive once (%Y, %m, %d, %H, %M, %S, %f, %z).')
            if part != '%%':
                directives.add(part)
            parts.append(_DATE_DIRECTIVES[part])
        else:
            parts.append(re.escape(part))
    if not _DATE <= directives:
        raise ValueError('Include an explicit four-digit year, month and day.')
    date_only = directives == _DATE
    if day_only and not date_only:
        raise ValueError('The trading day format must contain a date only.')
    if not date_only and (not _CLOCK <= directives or ('%f' in directives and '%S' not in directives)):
        raise ValueError('Include the hour and minute, and seconds when using fractional seconds.')
    return _DatePattern(template, re.compile(''.join(parts)), date_only)


@dataclass(frozen=True)
class _NativeMapping:
    series: PreviewSeries
    indices: dict[str, int]
    timestamp: _DatePattern
    trading_day: _DatePattern
    zone: ZoneInfo
    daily_start: time | None
    closure_indices: tuple[int, ...]
    declarations: tuple[tuple[int, str, str], ...]


def _column_key(column: str) -> str:
    return ''.join(character for character in column.casefold() if character.isalnum())


def auxiliary_role(column: str) -> str | None:
    key = _column_key(column)
    if key in _CURRENT_SETTLEMENT:
        return 'settlement price'
    if key in _PRIOR_SETTLEMENT:
        return 'previous settlement price'
    if key in _CLOSURE_NAMES:
        return 'bar closure flag'
    if field := _SERIES_DECLARATIONS.get(key):
        return f'declared {field.replace("_", " ")}'
    return _AUXILIARY_COLUMNS.get(key)


def _column_indices(mapping: object, header: list[str], issues: list[PreviewIssue]) -> dict[str, int]:
    if not isinstance(mapping, Mapping):
        issues.append(preview_issue('mapping_required', 'columns', 'Select exact source headers for the native bar fields.'))
        return {}
    positions = {column: index for index, column in enumerate(header)}
    assigned: dict[int, str] = {}
    indices: dict[str, int] = {}
    for field in _FIELDS:
        column = mapping.get(field)
        if field == 'volume' and field in mapping and column is None:
            continue
        if not isinstance(column, str) or not column:
            issues.append(preview_issue('column_required', field, 'Select the source header for this field.'))
        elif (position := positions.get(column)) is None:
            issues.append(preview_issue('column_missing', field, f'The mapped header {column!r} is missing. Review the file layout.'))
        elif (role := auxiliary_role(column)) and (
            field in (*_PRICES, 'volume') or _column_key(column) in _SERIES_DECLARATIONS
        ):
            issues.append(preview_issue('auxiliary_column', field, f'{column!r} is {role}. Select a native bar field.'))
        elif position in assigned and (assigned[position], field) != ('timestamp', 'trading_day'):
            issues.append(preview_issue('column_reused', field, 'Select a distinct source header for this field.'))
        else:
            assigned[position] = field
            indices[field] = position
    return indices


def native_mapping(mapping: Mapping[str, object], header: list[str], issues: list[PreviewIssue]) -> _NativeMapping | None:
    def declared(field: str) -> str:
        value = mapping.get(field)
        if isinstance(value, str) and value and value == value.strip() and value.casefold() not in {
            'unknown', 'ambiguous', 'unspecified',
        }:
            return value
        issues.append(preview_issue('declaration_required', field, f'Declare the source {field.replace("_", " ")}.'))
        return ''

    series: PreviewSeries = {
        'instrument_kind': declared('instrument_kind'), 'venue': declared('venue'),
        'timeframe': declared('timeframe'), 'calendar_id': declared('calendar_id'),
        'session_scope': declared('session_scope'), 'timezone': declared('timezone'),
        'quantity_unit': declared('quantity_unit'), 'price_adjustment': declared('price_adjustment'),
        'roll_method': None,
    }
    kind = series['instrument_kind']
    if kind == 'continuous_future':
        series['roll_method'] = declared('roll_method')
    elif kind and kind not in {'equity', 'dated_future'}:
        issues.append(preview_issue('unsupported_instrument', 'instrument_kind', 'Select equity, dated future or continuous future.'))
    if series['price_adjustment'].casefold() == 'adjusted':
        issues.append(preview_issue('adjustment_ambiguous', 'price_adjustment', 'Declare the specific adjustment basis used by the source.'))
    if mapping.get('volume_semantics') != 'per_bar':
        issues.append(preview_issue('volume_ambiguous', 'volume_semantics', 'Declare per-bar volume. Cumulative or unresolved quantities stay raw.'))
    if mapping.get('timestamp_convention') != 'bar_start':
        issues.append(preview_issue('timestamp_convention', 'timestamp_convention', 'Select a timestamp that identifies the bar start. End labels need a supported conversion.'))

    indices = _column_indices(mapping.get('columns'), header, issues)
    formats = {}
    for field in ('timestamp', 'trading_day'):
        key = f'{field}_format'
        try:
            formats[field] = _date_pattern(mapping.get(key), day_only=field == 'trading_day')
        except ValueError as error:
            issues.append(preview_issue('date_format', key, str(error)))

    zone = None
    if timezone_key := series['timezone']:
        try:
            if timezone_key in {'localtime', 'posixrules'} or timezone_key.startswith(('posix/', 'right/')):
                raise ValueError
            zone = ZoneInfo(timezone_key)
        except (OSError, ValueError, ZoneInfoNotFoundError):
            issues.append(preview_issue('timezone_unavailable', 'timezone', 'Select an available IANA source timezone.'))

    timestamp = formats.get('timestamp')
    daily_start = None
    if timestamp is not None and timestamp.date_only:
        value = mapping.get('daily_start_time')
        same_date_column = indices.get('timestamp') == indices.get('trading_day')
        if series['timeframe'] != '1d' or (kind != 'equity' and same_date_column):
            issues.append(preview_issue('civil_time_required', 'timestamp', 'Select a civil bar-start timestamp. A trading date alone cannot locate this bar.'))
        else:
            try:
                if not isinstance(value, str) or not re.fullmatch(r'[0-9]{2}:[0-9]{2}:[0-9]{2}', value):
                    raise ValueError
                daily_start = time.fromisoformat(value)
            except ValueError:
                issues.append(preview_issue('daily_start_required', 'daily_start_time', 'Select a civil bar-start timestamp or declare the daily bar-start clock as HH:MM:SS.'))

    trading_day = formats.get('trading_day')
    if issues or timestamp is None or trading_day is None or zone is None:
        return None
    closure_indices = tuple(index for index, column in enumerate(header) if _column_key(column) in _CLOSURE_NAMES)
    reviewed = {
        'quantity_unit': series['quantity_unit'],
        'price_adjustment': series['price_adjustment'],
        'venue': series['venue'],
    }
    declarations = tuple(
        (index, declaration, reviewed[declaration])
        for index, column in enumerate(header)
        if (declaration := _SERIES_DECLARATIONS.get(_column_key(column))) is not None
    )
    return _NativeMapping(series, indices, timestamp, trading_day, zone, daily_start, closure_indices, declarations)


def _resolve_time(value: datetime, zone: ZoneInfo) -> datetime:
    if value.tzinfo is not None:
        utc = value.astimezone(UTC)
        if value.utcoffset() != utc.astimezone(zone).utcoffset():
            raise ValueError('The timestamp offset disagrees with the source timezone.')
        return utc

    candidates = set()
    for fold in (0, 1):
        utc = value.replace(tzinfo=zone, fold=fold).astimezone(UTC)
        if utc.astimezone(zone).replace(tzinfo=None) == value:
            candidates.add(utc)
    if not candidates:
        raise ValueError('The local clock falls in a timezone gap. Select a valid civil timestamp.')
    if len(candidates) > 1:
        raise ValueError('The local clock is ambiguous. Supply an explicit UTC offset in the source timestamp.')
    return candidates.pop()


def _timestamp(value: str, mapping: _NativeMapping) -> int:
    source_time = mapping.timestamp.parse(value)
    if mapping.daily_start is not None:
        source_time = datetime.combine(source_time.date(), mapping.daily_start)
    utc = _resolve_time(source_time, mapping.zone)
    if utc.microsecond % 1000:
        raise ValueError('Supply a bar start with whole millisecond precision.')
    return (utc - _EPOCH) // _MILLISECOND


def _number(value: str, field: str) -> Decimal:
    try:
        if not _NUMBER.fullmatch(value.strip()):
            raise ValueError
        number = Decimal(value.strip())
        rounded = float(number)
        if not math.isfinite(rounded):
            raise ValueError
    except (ValueError, InvalidOperation, OverflowError) as error:
        raise BarFieldError(preview_issue('invalid_number', field, 'Use a finite decimal number in the declared unit.')) from error
    if Decimal(str(rounded)) != number:
        raise BarFieldError(preview_issue('numeric_precision', field, 'The chart number would lose source precision. Review this value in the raw record.'))
    return number


def normalize_bar(values: list[str], mapping: _NativeMapping) -> tuple[str, Bar]:
    def raw(field: str) -> str:
        return values[mapping.indices[field]]

    # A native mapping declares closed historical input. Explicit row evidence
    # can override that declaration and remains part of the raw preview.
    if mapping.closure_indices:
        flags = {values[index].strip().casefold() for index in mapping.closure_indices}
        if flags <= {'false', '0'}:
            raise BarFieldError(preview_issue('bar_incomplete', 'is_closed', 'The source marks this bar incomplete. Review its raw values or supply a closed bar.'))
        if not flags <= {'true', '1'}:
            raise BarFieldError(preview_issue('bar_closure_ambiguous', 'is_closed', 'Resolve the missing, unrecognized or conflicting source closure flags. Use explicit true or false values.'))

    for index, field, expected in mapping.declarations:
        declared = values[index].strip()
        if declared and declared.casefold() != expected.casefold():
            label = field.replace('_', ' ')
            raise BarFieldError(preview_issue(
                f'{field}_mismatch', field,
                f'The source {label} is {declared[:80]!r}. The reviewed value is {expected[:80]!r}. Review the mapping.',
            ))

    symbol = raw('symbol')
    if not symbol or symbol != symbol.strip():
        raise BarFieldError(preview_issue('invalid_symbol', 'symbol', 'Use a nonempty identifier without surrounding whitespace.'))
    try:
        timestamp = _timestamp(raw('timestamp'), mapping)
    except (ValueError, OverflowError) as error:
        raise BarFieldError(preview_issue('invalid_timestamp', 'timestamp', f'Check the source bar start. {error}')) from error
    trading_day: date
    try:
        trading_day = mapping.trading_day.parse(raw('trading_day')).date()
    except ValueError as error:
        raise BarFieldError(preview_issue('invalid_trading_day', 'trading_day', 'Use a valid trading date in the declared format.')) from error

    open_, high, low, close = (_number(raw(field), field) for field in _PRICES)
    if low > min(open_, close) or high < max(open_, close):
        raise BarFieldError(preview_issue('ohlc_bounds', None, 'Check that low and high bound both open and close.'))
    volume = None
    if 'volume' in mapping.indices and (quantity := raw('volume').strip()):
        amount = _number(quantity, 'volume')
        if amount < 0:
            raise BarFieldError(preview_issue('negative_volume', 'volume', 'Use a nonnegative per-bar quantity or leave it empty when unknown.'))
        volume = float(amount) or 0.0
    return symbol, {
        'time_ms': timestamp, 'trading_day': trading_day.isoformat(),
        'open': float(open_) or 0.0, 'high': float(high) or 0.0,
        'low': float(low) or 0.0, 'close': float(close) or 0.0,
        'volume': volume, 'is_closed': True,
    }
