"""Normalize CSV previews and complete files over authorized source bytes.

Mapping v1 consists of version, revision, encoding, delimiter and record_kind.
For native_ohlcv, declare closed historical bars with exact header selections
in columns, the fields of PreviewSeries, per_bar volume_semantics, bar_start
timestamp_convention, timestamp_format and trading_day_format. A null volume
selector declares that the data supplies no volume. Date-only daily bar starts
also require daily_start_time and, for futures, a separate civil date column.

The importer owns file selection, authorization, mapping revisions and
publication.
"""

from collections.abc import Iterator, Mapping
import csv
from dataclasses import dataclass, field
from hashlib import sha256
from io import StringIO
from threading import Lock
from typing import TypedDict

from contracts.generated.protocol import (
    CsvPreview, LocalImportRowLocator, PreviewIssue, PreviewRow, PreviewSeries, PublishedLocalImportBar,
)
from providers.csv_bar_mapping import (
    BarFieldError, auxiliary_role, native_mapping, normalize_bar, preview_issue,
)


MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_PREVIEW_ROWS = 500
MAX_PUBLICATION_RECORDS = 10_000
_MAX_COLUMNS = 512
_ENCODINGS = {'utf-8', 'utf-8-sig', 'gb18030'}
_CSV_READ_LOCK = Lock()


class CheckedCsv(TypedDict):
    file_sha256: str
    mapping_version: int
    mapping_revision: str
    header: list[str]
    series: PreviewSeries
    symbol: str
    source_record_count: int
    bars: list[PublishedLocalImportBar]


class CsvReviewRequired(ValueError):
    def __init__(self, issue: PreviewIssue, locator: LocalImportRowLocator | None = None) -> None:
        self.issue = issue
        self.locator = locator
        super().__init__(issue['message'])


class CsvPublicationLimit(ValueError):
    """The file exceeded the complete-file analysis budget."""


@dataclass
class _CsvRead:
    result: CsvPreview
    records: Iterator[PreviewRow] = field(default_factory=lambda: iter(()))
    stream: StringIO | None = None
    decoded_length: int = 0

    def truncated(self) -> bool:
        if self.stream is None:
            return self.result['truncated']
        return self.stream.tell() < self.decoded_length


def _next_csv_record(rows: Iterator[list[str]]) -> list[str]:
    # The file budget bounds every field. Keep the interpreter-wide override
    # scoped to one record and serialize concurrent previews during that read.
    with _CSV_READ_LOCK:
        previous_limit = csv.field_size_limit(MAX_FILE_BYTES)
        try:
            return next(rows)
        finally:
            csv.field_size_limit(previous_limit)


def _read_csv(content: bytes, mapping: Mapping[str, object]) -> _CsvRead:
    version, revision = mapping.get('version'), mapping.get('revision')
    result: CsvPreview = {
        'file_sha256': sha256(content).hexdigest(), 'header': [], 'raw_header_text': None,
        'rows': [], 'issues': [], 'raw_only_columns': [], 'series': None, 'truncated': False,
        'mapping_version': version if type(version) is int else None,
        'mapping_revision': revision if isinstance(revision, str) and revision.strip() else None,
    }
    issues = result['issues']
    encoding, delimiter = mapping.get('encoding'), mapping.get('delimiter')
    if not isinstance(encoding, str) or encoding not in _ENCODINGS:
        issues.append(preview_issue('encoding_required', 'encoding', 'Select UTF-8, UTF-8 with BOM, or GB18030.'))
        return _CsvRead(result)
    if not isinstance(delimiter, str) or len(delimiter) != 1 or delimiter in '\r\n\x00"':
        issues.append(preview_issue('delimiter_required', 'delimiter', 'Select a single CSV field separator distinct from the quote and newline.'))
        return _CsvRead(result)
    decode_failed = False
    try:
        decoded = content.decode(encoding)
    except UnicodeDecodeError as error:
        # utf-8-sig reports the error relative to the bytes after the BOM.
        offset = len(content) - len(error.object) + error.start
        issues.append(preview_issue('decode_failed', 'encoding', f'Cannot decode byte {offset} with {encoding}. Check the source encoding.'))
        decoded = content[:offset].decode(encoding)
        decode_failed = True

    if not decode_failed:
        if result['mapping_version'] != 1 or result['mapping_revision'] is None:
            issues.append(preview_issue('mapping_version', 'version', 'Use mapping version 1 and supply a mapping revision.'))
        record_kind = mapping.get('record_kind')
        if record_kind == 'quote_snapshot':
            issues.append(preview_issue('quote_snapshot', 'record_kind', 'Quote snapshots stay raw. Reconstructed bars require verified trade and session rules.'))
        elif record_kind != 'native_ohlcv':
            issues.append(preview_issue('unsupported_record_kind', 'record_kind', 'Classify this input. Normalization requires closed native OHLCV bars.'))

    stream = StringIO(decoded, newline='')
    rows = csv.reader(stream, delimiter=delimiter, strict=True)
    try:
        header = _next_csv_record(rows)
    except StopIteration:
        header = []
    except csv.Error as error:
        if not decode_failed:
            result['raw_header_text'] = decoded[:stream.tell()]
        issues.append(preview_issue('invalid_header', 'header', f'Check the header delimiter, quoting or field size. {error}'))
        result['truncated'] = stream.tell() < len(decoded)
        return _CsvRead(result)
    # csv.reader accepts an unterminated physical line at EOF. When decoding
    # failed, only a logical record terminated in the source verifies the header.
    if decode_failed and not decoded.endswith(('\r', '\n'), 0, stream.tell()):
        return _CsvRead(result)
    if len(header) > _MAX_COLUMNS:
        if not decode_failed:
            result['raw_header_text'] = decoded[:stream.tell()]
        issues.append(preview_issue('column_limit', 'header', f'Review the field separator. CSV preview supports up to {_MAX_COLUMNS} columns.'))
        result['truncated'] = stream.tell() < len(decoded)
        return _CsvRead(result)
    valid_header = bool(header) and all(column.strip() for column in header) and len(set(header)) == len(header)
    if not valid_header:
        issues.append(preview_issue('invalid_header', 'header', 'Use nonempty, unique CSV column headers.'))
    if not decode_failed or valid_header:
        result['header'] = header
    if decode_failed:
        # The header may be known while decoding the source records still failed.
        return _CsvRead(result)
    native = native_mapping(mapping, header, issues) if not issues else None
    selected = set(native.indices.values()) if native is not None else set()
    result['raw_only_columns'] = [
        {
            'column': column,
            'reason': f'Preserved for review as {role}.' if (role := auxiliary_role(column))
            else 'No supported bar field is mapped to this column.',
        }
        for index, column in enumerate(header) if index not in selected
    ]
    if native is not None:
        result['series'] = native.series

    def source_records() -> Iterator[PreviewRow]:
        record_number = 0
        while True:
            record_number += 1
            line_start, start = rows.line_num + 1, stream.tell()
            row: PreviewRow = {
                'record_number': record_number, 'line_start': line_start, 'line_end': line_start,
                'raw_fields': [], 'raw_text': None, 'symbol': None, 'normalized': None, 'issues': [],
            }
            try:
                values = _next_csv_record(rows)
            except StopIteration:
                return
            except csv.Error as error:
                row['line_end'] = rows.line_num
                row['raw_text'] = decoded[start:stream.tell()]
                row['issues'].append(preview_issue('csv_syntax', None, f'Check the CSV quoting or field size. {error}'))
                yield row
                return  # Quoting errors leave subsequent record boundaries unverified.
            row['line_end'] = rows.line_num
            if len(values) > _MAX_COLUMNS:
                row['raw_text'] = decoded[start:stream.tell()]
                row['issues'].append(preview_issue('column_limit', None, f'Check the separator and quoting. This record exceeds {_MAX_COLUMNS} fields.'))
            elif len(values) != len(header):
                row['raw_fields'] = values
                row['issues'].append(preview_issue('row_width', None, 'Check the delimiter and quoting. The field count differs from the header.'))
            else:
                row['raw_fields'] = values
                if native is not None:
                    try:
                        row['symbol'], row['normalized'] = normalize_bar(values, native)
                    except BarFieldError as error:
                        row['issues'].append(error.issue)
            yield row

    return _CsvRead(result, source_records(), stream, len(decoded))


def _no_records() -> PreviewIssue:
    return preview_issue('no_records', None, 'The CSV header is present. Supply a file with data records.')


def preview_csv(content: bytes, mapping: Mapping[str, object], *, row_limit: int = 100) -> CsvPreview:
    """Review at most row_limit source records in order. The digest covers all bytes.

    The caller supplies the complete file. Oversized inputs or invalid limits
    raise ValueError. Unsupported meanings remain inspectable where the declared
    encoding and CSV syntax permit decoding. Eligible rows are preview candidates.
    """
    if len(content) > MAX_FILE_BYTES:
        raise ValueError(f'CSV preview supports up to {MAX_FILE_BYTES} bytes per file.')
    if type(row_limit) is not int or not 1 <= row_limit <= MAX_PREVIEW_ROWS:
        raise ValueError(f'CSV preview row limit must be between 1 and {MAX_PREVIEW_ROWS}.')
    read = _read_csv(content, mapping)
    result = read.result
    for _ in range(row_limit):
        row = next(read.records, None)
        if row is None:
            break
        result['rows'].append(row)
    if not result['rows'] and not result['issues']:
        result['issues'].append(_no_records())
    result['truncated'] = read.truncated()
    return result


def check_complete_csv(content: bytes, mapping: Mapping[str, object]) -> CheckedCsv:
    """Return a fully parsed, single native series or hold the entire file for review."""
    return _check_complete_csv(content, mapping, retain_conflicts=False)


def inspect_folder_csv(content: bytes, mapping: Mapping[str, object]) -> CheckedCsv:
    """Report every eligible value and locator for read-only folder review."""
    return _check_complete_csv(content, mapping, retain_conflicts=True)


def _check_complete_csv(
    content: bytes, mapping: Mapping[str, object], *, retain_conflicts: bool,
) -> CheckedCsv:
    if len(content) > MAX_FILE_BYTES:
        raise CsvPublicationLimit
    read = _read_csv(content, mapping)
    meta = read.result
    if meta['issues']:
        raise CsvReviewRequired(meta['issues'][0])
    series, version, revision = meta['series'], meta['mapping_version'], meta['mapping_revision']
    assert series is not None and version is not None and revision is not None

    bars: dict[
        int, dict[tuple[str, float, float, float, float, float | None, bool], PublishedLocalImportBar],
    ] = {}
    first_symbol = None
    record_count = 0
    for row in read.records:
        record_count = row['record_number']
        if record_count > MAX_PUBLICATION_RECORDS:
            raise CsvPublicationLimit
        locator: LocalImportRowLocator = {
            'record_number': record_count, 'line_start': row['line_start'], 'line_end': row['line_end'],
        }
        if row['issues']:
            raise CsvReviewRequired(row['issues'][0], locator)
        symbol, bar = row['symbol'], row['normalized']
        assert symbol is not None and bar is not None
        if first_symbol is None:
            first_symbol = symbol
        elif symbol != first_symbol:
            issue = preview_issue('multiple_symbols', 'symbol', 'This file contains multiple symbols. Review a file with one native bar series.')
            raise CsvReviewRequired(issue, locator)
        at_time = bars.setdefault(bar['time_ms'], {})
        values = (
            bar['trading_day'], bar['open'], bar['high'], bar['low'], bar['close'],
            bar['volume'], bar['is_closed'],
        )
        if prior := at_time.get(values):
            prior['locators'].append(locator)
        else:
            if at_time and not retain_conflicts:
                issue = preview_issue('conflicting_bar', 'timestamp', 'This row disagrees with another bar at the same start time. Review the competing source records.')
                raise CsvReviewRequired(issue, locator)
            at_time[values] = {'bar': bar, 'locators': [locator]}
    if first_symbol is None:
        raise CsvReviewRequired(_no_records())
    return {
        'file_sha256': meta['file_sha256'], 'mapping_version': version, 'mapping_revision': revision,
        'header': meta['header'], 'series': series, 'symbol': first_symbol, 'source_record_count': record_count,
        'bars': [entry for timestamp in sorted(bars) for entry in bars[timestamp].values()],
    }
