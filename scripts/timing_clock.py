"""Exact instants and bounded projections of timing evidence."""

from datetime import datetime, timedelta
from decimal import Context, Decimal, MAX_EMAX, MIN_EMIN, localcontext
from functools import lru_cache
from itertools import product
import re


EPOCH = datetime.min
LAST_SECOND = (datetime.max - EPOCH).days * 86400 + 86399
ISO_INSTANT = re.compile(
    r'(?P<local>[0-9]{4}(?:-?[0-9]{2}-?[0-9]{2}|-?W[0-9]{2}(?:-?[0-9])?)'
    r'.[0-9]{2}(?::?[0-9]{2}){0,2})(?:[.,](?P<fraction>[0-9]+))?'
    r'(?:Z|(?P<sign>[+-])(?P<hour>[0-9]{2})(?::?(?P<minute>[0-9]{2})'
    r'(?::?(?P<second>[0-9]{2}))?)?(?:[.,](?P<offset_fraction>[0-9]+))?)',
    re.DOTALL,
)


def exact_arithmetic(values):
    scale = max((-value.as_tuple().exponent for value in values), default=0)
    # The supported calendar range takes twelve digits. One additional
    # digit accommodates sums across the verification matrix.
    return localcontext(Context(prec=13 + scale, Emin=MIN_EMIN, Emax=MAX_EMAX))


def timestamp(value):
    """Parse an aware ISO calendar instant without microsecond truncation."""
    if not isinstance(value, str):
        return None
    return cached_timestamp(value) if len(value) <= 96 else parse_timestamp(value)


def parse_timestamp(value):
    match = ISO_INSTANT.fullmatch(value)
    if match is None:
        return None
    try:
        # Use the standard parser for calendar and timezone validation, but
        # read the original fractions below before they can be rounded away.
        aware = datetime.fromisoformat(value)
    except ValueError:
        return None
    if aware.tzinfo is None:
        return None
    offset = (int(match['hour'] or 0) * 3600 + int(match['minute'] or 0) * 60
              + int(match['second'] or 0))
    shift = -1 if match['sign'] == '+' else 1
    delta = aware.replace(microsecond=0, tzinfo=None) - EPOCH
    whole = delta.days * 86400 + delta.seconds + shift * offset
    fraction = (match['fraction'] or '').rstrip('0')
    offset_fraction = (match['offset_fraction'] or '').rstrip('0')
    point = Decimal(whole)
    if fraction or offset_fraction:
        local_part = Decimal('0.' + fraction)
        offset_part = Decimal('0.' + offset_fraction)
        with exact_arithmetic((local_part, offset_part)):
            point += local_part + shift * offset_part
    return point if 0 <= point < LAST_SECOND + 1 else None


cached_timestamp = lru_cache(maxsize=4096)(parse_timestamp)


def encode_timestamp(point):
    if point is None:
        return None
    whole, _, fraction = format(point, 'f').partition('.')
    fraction = fraction.rstrip('0')
    text = (EPOCH + timedelta(seconds=int(whole))).isoformat()
    return f'{text}.{fraction.ljust(6, "0")}+00:00' if fraction else f'{text}+00:00'


def elapsed(start, end):
    if start is None or end is None or start > end:
        return None
    with exact_arithmetic((start, end)):
        return end - start


def latest_harness_start(recorded, elapsed_seconds):
    # The verification writer rounds its monotonic elapsed time to milliseconds.
    elapsed = Decimal(str(elapsed_seconds))
    half_millisecond = Decimal('0.0005')
    with exact_arithmetic((recorded, elapsed, half_millisecond)):
        return recorded - max(0, elapsed - half_millisecond)


def seconds(bounds):
    if bounds is None or bounds[0] != bounds[1]:
        return None
    return float(bounds[0])


def can_order(choices, fields):
    # The earliest feasible witness leaves later endpoints the widest choice.
    # A missing or opaque witness cannot bound a different measured endpoint.
    earliest = None
    for field in fields:
        values = choices.get(field, ())
        if values:
            earliest = min((value for value in values if earliest is None or value >= earliest), default=None)
            if earliest is None:
                return False
    return True


def ordered_choices(choices, fields, *, within):
    """Project the requested endpoints from every feasible ordered chain."""
    values = [choices.get(field) for field in fields]
    if not all(values):
        return ()
    return tuple(points for points in product(*values)
                 if can_order({**choices, **{field: (point,) for field, point in zip(fields, points, strict=True)}},
                              within))


def ordered_limit(choices, fields, boundary, *, latest=False):
    """Find the outer feasible limit of a boundary in an ordered chain."""
    if latest:
        fields = reversed(fields)
        edge = Decimal('Infinity')
        select = max
    else:
        edge = Decimal('-Infinity')
        select = min
    limit = None
    for field in fields:
        if values := choices.get(field):
            eligible = (point for point in values if point <= edge) if latest else (
                point for point in values if point >= edge)
            edge = select(eligible, default=None)
            if edge is None:
                return None
        if field == boundary:
            limit = edge
    return limit


def interval_bounds(pairs):
    values = [value for start, end in pairs if (value := elapsed(start, end)) is not None]
    return (min(values), max(values)) if values else None


def sum_intervals(intervals):
    if not all(intervals):
        return None
    with exact_arithmetic(value for interval in intervals for value in interval):
        return tuple(sum(values) for values in zip(*intervals, strict=True))


def extrema(choices, choose):
    """Min or max across independent endpoint choices, at both bounds."""
    return choose(min(values) for values in choices), choose(max(values) for values in choices)


def paired_minima(choices):
    """Combine per-job endpoint pairs without multiplying unrelated choices."""
    result = set(choices[0])
    for pairs in choices[1:]:
        result = {(min(left, start), min(right, harness))
                  for left, right in result for start, harness in pairs}
    return result
