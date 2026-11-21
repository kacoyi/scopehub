"""Offline CSV preview over bytes supplied by the authorized importer.

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
from hashlib import sha256
from io import StringIO
from threading import Lock

from contracts.generated.protocol import CsvPreview, PreviewRow
from providers.csv_bar_mapping import (
    BarFieldError, auxiliary_role, native_mapping, normalize_bar, preview_issue,
)


MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_PREVIEW_ROWS = 500
_MAX_COLUMNS = 512
_ENCODINGS = {'utf-8', 'utf-8-sig', 'gb18030'}
_CSV_READ_LOCK = Lock()


def _next_csv_record(rows: Iterator[list[str]]) -> list[str]:
    # The file budget bounds every field. Keep the interpreter-wide override
    # scoped to one record and serialize concurrent previews during that read.
    with _CSV_READ_LOCK:
        previous_limit = csv.field_size_limit(MAX_FILE_BYTES)
        try:
            return next(rows)
        finally:
            csv.field_size_limit(previous_limit)


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
        return result
    if not isinstance(delimiter, str) or len(delimiter) != 1 or delimiter in '\r\n\x00"':
        issues.append(preview_issue('delimiter_required', 'delimiter', 'Select a single CSV field separator distinct from the quote and newline.'))
        return result
    try:
        decoded = content.decode(encoding)
    except UnicodeDecodeError as error:
        issues.append(preview_issue('decode_failed', 'encoding', f'Cannot decode byte {error.start} with {encoding}. Check the source encoding.'))
        return result

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
        result['raw_header_text'] = decoded[:stream.tell()]
        issues.append(preview_issue('invalid_header', 'header', f'Check the header delimiter, quoting or field size. {error}'))
        result['truncated'] = stream.tell() < len(decoded)
        return result
    if len(header) > _MAX_COLUMNS:
        result['raw_header_text'] = decoded[:stream.tell()]
        issues.append(preview_issue('column_limit', 'header', f'Review the field separator. CSV preview supports up to {_MAX_COLUMNS} columns.'))
        result['truncated'] = stream.tell() < len(decoded)
        return result
    result['header'] = header
    if not header or any(not column.strip() for column in header) or len(set(header)) != len(header):
        issues.append(preview_issue('invalid_header', 'header', 'Use nonempty, unique CSV column headers.'))
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

    for record_number in range(1, row_limit + 1):
        line_start, start = rows.line_num + 1, stream.tell()
        row: PreviewRow = {
            'record_number': record_number, 'line_start': line_start, 'line_end': line_start,
            'raw_fields': [], 'raw_text': None, 'symbol': None, 'normalized': None, 'issues': [],
        }
        try:
            values = _next_csv_record(rows)
        except StopIteration:
            break
        except csv.Error as error:
            row['line_end'] = rows.line_num
            row['raw_text'] = decoded[start:stream.tell()]
            row['issues'].append(preview_issue('csv_syntax', None, f'Check the CSV quoting or field size. {error}'))
            result['rows'].append(row)
            break  # Quoting errors leave subsequent record boundaries unverified.
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
        result['rows'].append(row)
    if not result['rows'] and not issues:
        issues.append(preview_issue('no_records', None, 'The CSV header is present. Supply a file with data records.'))
    result['truncated'] = stream.tell() < len(decoded)
    return result
