import { invoke, isTauri } from '@tauri-apps/api/core';
import type {
  ListLocalImportRevisionsInput, ListLocalImportRevisionsOutput,
  LoadLocalImportMappingInput, LoadLocalImportMappingOutput, LocalImportPreviewInput, LocalImportPreviewOutput,
  ListLocalFolderRevisionsInput, ListLocalFolderRevisionsOutput,
  PrepareLocalFolderPublicationInput, PrepareLocalFolderPublicationOutput,
  PreviewLocalFolderRefreshInput, PreviewLocalFolderRefreshOutput,
  PublishLocalFolderRefreshInput, PublishLocalFolderRefreshOutput,
  ReadLocalFolderRevisionInput, ReadLocalFolderRevisionOutput,
  ReadLocalFolderSourceRowInput, ReadLocalFolderSourceRowOutput,
  RevokeLocalFolderRevisionInput, RevokeLocalFolderRevisionOutput,
  PublishLocalImportInput, PublishLocalImportOutput, ReadLocalImportRevisionInput, ReadLocalImportRevisionOutput,
  ReadLocalImportSourceRowInput, ReadLocalImportSourceRowOutput,
  RevokeLocalImportRevisionInput, RevokeLocalImportRevisionOutput,
  SaveLocalImportMappingInput, SaveLocalImportMappingOutput,
} from '../../../../contracts/generated/protocol';
import type { LocalFolderCatalog } from './local-import-editor';
import { formatNumber, getLocale, message, t } from './i18n';

export type LocalImportMessage = ReturnType<typeof message> | string | { readonly render: () => string };

// The protocol carries English diagnostics. Recognize application-owned
// messages here, leaving other diagnostic text and source values opaque.
const nativeErrorMessages = new Set([
  'No data source is configured for this request.',
  'This operation or timeframe is not implemented.',
  'The request arguments are invalid.',
  'This caller cannot perform the operation.',
  'The operation was cancelled.',
  'The selected source is no longer current.',
  'No data is available for this instrument.',
  'The market worker did not respond before the deadline.',
  'The market worker is unavailable.',
  'The market response failed validation.',
  'The market worker is incompatible.',
  'The market worker is busy.',
  'The requested market snapshot is unavailable.',
  'The market request exceeded a resource limit.',
  'The operation could not be completed.',
  'Workspace storage is busy.',
  'Workspace commands require protocol 1.1.',
  'The workspace revision limit was reached.',
  'The workspace reply failed validation.',
  'Preview this mapping and review a normalized bar before saving.',
  'Load the current saved mapping before publishing this CSV.',
  'Preview the current CSV with its saved mapping before publishing.',
  'Publication supports CSV files up to 4 MiB.',
  'This preview supports source files up to 4 MiB. Select a smaller CSV file.',
  'The preview exceeds the response budget. Request fewer rows or select a smaller CSV file.',
  'Restart ScopeHub to start more CSV writes. Committed requests can still be retried during this session.',
  'This request ID already identifies a different local CSV action.',
  'Review the current folder before publishing.',
  'Choose the directory used for this published revision.',
  'The folder changed during review. Stage the refresh again.',
  'A folder preview can check up to 64 CSV files.',
  'A folder preview can check up to 16 MiB of complete CSV files.',
  'A folder preview can check up to 20,000 complete CSV records, including aliases.',
  'The selected folder has CSV names that cannot be distinguished in the review.',
  'Resolve unavailable or incompatible sources before publishing.',
  'The CSV header changed. Review and save the mapping for this file before publishing.',
  'The CSV header could not be verified with the saved encoding and delimiter. Inspect the current file before reusing the mapping.',
  'Publication supports up to 10,000 source records and 4 MiB per file or result. Select a smaller CSV file.',
  'The local CSV preview timed out twice. Try running the preview again.',
]);

const csvIssueMessages = new Set([
  'Select UTF-8, UTF-8 with BOM, or GB18030.',
  'Select a single CSV field separator distinct from the quote and newline.',
  'Use mapping version 1 and supply a mapping revision.',
  'Executed trades are shown in source order with their reported prices and quantities.',
  'Quote snapshots stay raw. Reconstructed bars require verified trade and session rules.',
  'Order book snapshots are shown with their original levels and quantities.',
  'Reference records are shown with their original fields.',
  'Corporate action records are shown with the dates and terms supplied by the source.',
  'Classify this input. Normalization requires closed native OHLCV bars.',
  'Use nonempty, unique CSV column headers.',
  'Check the delimiter and quoting. The field count differs from the header.',
  'The CSV header is present. Supply a file with data records.',
  'This file contains multiple symbols. Review a file with one native bar series.',
  'This row disagrees with another bar at the same start time. Review the competing source records.',
  'Select exact source headers for the native bar fields.',
  'Select the source header for this field.',
  'Select a distinct source header for this field.',
  'Declare the source instrument kind.',
  'Declare the source venue.',
  'Declare the source timeframe.',
  'Declare the source calendar id.',
  'Declare the source session scope.',
  'Declare the source timezone.',
  'Declare the source quantity unit.',
  'Declare the source price adjustment.',
  'Declare the source roll method.',
  'Select equity, dated future or continuous future.',
  'Declare the specific adjustment basis used by the source.',
  'Declare per-bar volume. Cumulative or unresolved quantities stay raw.',
  'Select a timestamp that identifies the bar start. End labels need a supported conversion.',
  'Declare a date format with %Y, %m and %d.',
  'Use each supported directive once (%Y, %m, %d, %H, %M, %S, %f, %z).',
  'Include an explicit four-digit year, month and day.',
  'The trading day format must contain a date only.',
  'Include the hour and minute, and seconds when using fractional seconds.',
  'Select an available IANA source timezone.',
  'Select a civil bar-start timestamp. A trading date alone cannot locate this bar.',
  'Select a civil bar-start timestamp or declare the daily bar-start clock as HH:MM:SS.',
  'Use a finite decimal number in the declared unit.',
  'The chart number would lose source precision. Review this value in the raw record.',
  'The source marks this bar incomplete. Review its raw values or supply a closed bar.',
  'Resolve the missing, unrecognized or conflicting source closure flags. Use explicit true or false values.',
  'Use a nonempty identifier without surrounding whitespace.',
  'Use a valid trading date in the declared format.',
  'Check that low and high bound both open and close.',
  'Use a nonnegative per-bar quantity or leave it empty when unknown.',
]);

const timestampIssueDetails = new Set([
  'Value does not match the declared format.',
  'The timestamp offset disagrees with the source timezone.',
  'The local clock falls in a timezone gap. Select a valid civil timestamp.',
  'The local clock is ambiguous. Supply an explicit UTC offset in the source timestamp.',
  'Supply a bar start with whole millisecond precision.',
]);

const auxiliaryRoles = new Set([
  'settlement price',
  'previous settlement price',
  'bar closure flag',
  'declared quantity unit',
  'declared calendar id',
  'declared daily start time',
  'declared instrument kind',
  'declared record kind',
  'declared roll method',
  'declared session scope',
  'declared timestamp convention',
  'declared timestamp format',
  'declared trading day format',
  'declared price adjustment',
  'declared venue',
  'declared timeframe',
  'declared timezone',
  'declared volume semantics',
  'snapshot last price',
  'average price',
  'open interest',
  'turnover',
  'contract multiplier',
  'cumulative volume',
]);

const folderReviewDetails = new Set([
  'An opaque directory component blocks inspection of the accepted source.',
  'The entry is not a supported regular CSV within the 4 MiB file limit.',
  'The current source could not be read for review.',
  'The decoded CSV header could not be inspected within the preview limit.',
  'The ordered CSV header could not be decoded with the saved encoding and delimiter.',
  'The complete CSV could not be checked within the 10,000-record and 4 MiB result limits.',
  'The completed file describes a different series.',
  'This file has competing values at the same bar start. Review the alternatives below.',
  'Ordered CSV header differs from the saved mapping.',
  'An opaque ancestor prevents checking this accepted source path.',
  'The accepted source path is absent from the current scan.',
]);

const matchesCsvIssue = (code: string | undefined, expected: string) => code === undefined || code === expected;
const formatCsvInteger = (value: string) => formatNumber(Number(value), { useGrouping: false });

// Python repr quotes each mapped value and escapes embedded delimiters.
const quotedCsvValue = /'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*"/u.source;
const missingHeader = new RegExp(
  '^The mapped header (' + quotedCsvValue + ') is missing\\. Review the file layout\\.$', 'u',
);
const auxiliaryHeader = new RegExp(
  '^(' + quotedCsvValue + ') is (.+)\\. Select a native bar field\\.$', 'u',
);
const declarationMessages = new Map([
  ['calendar id', 'The source calendar id is {source}. The reviewed value is {reviewed}. Review the mapping.'],
  ['daily start time', 'The source daily start time is {source}. The reviewed value is {reviewed}. Review the mapping.'],
  ['instrument kind', 'The source instrument kind is {source}. The reviewed value is {reviewed}. Review the mapping.'],
  ['record kind', 'The source record kind is {source}. The reviewed value is {reviewed}. Review the mapping.'],
  ['roll method', 'The source roll method is {source}. The reviewed value is {reviewed}. Review the mapping.'],
  ['session scope', 'The source session scope is {source}. The reviewed value is {reviewed}. Review the mapping.'],
  ['timestamp convention', 'The source timestamp convention is {source}. The reviewed value is {reviewed}. Review the mapping.'],
  ['timestamp format', 'The source timestamp format is {source}. The reviewed value is {reviewed}. Review the mapping.'],
  ['trading day format', 'The source trading day format is {source}. The reviewed value is {reviewed}. Review the mapping.'],
  ['quantity unit', 'The source quantity unit is {source}. The reviewed value is {reviewed}. Review the mapping.'],
  ['price adjustment', 'The source price adjustment is {source}. The reviewed value is {reviewed}. Review the mapping.'],
  ['venue', 'The source venue is {source}. The reviewed value is {reviewed}. Review the mapping.'],
  ['timeframe', 'The source timeframe is {source}. The reviewed value is {reviewed}. Review the mapping.'],
  ['timezone', 'The source timezone is {source}. The reviewed value is {reviewed}. Review the mapping.'],
  ['volume basis', 'The source volume basis is {source}. The reviewed value is {reviewed}. Review the mapping.'],
]);
const declarationMismatch = new RegExp(
  '^The source ([a-z ]+) is (' + quotedCsvValue +
  ')\\. The reviewed value is (' + quotedCsvValue + ')\\. Review the mapping\\.$', 'u',
);

function deferred(render: () => string): LocalImportMessage {
  return { render };
}

export function renderLocalImportMessage(value: LocalImportMessage): string {
  if (typeof value === 'string') return value;
  return 'render' in value ? value.render() : t(value);
}

export function localCsvAuxiliaryRole(role: string): string {
  return auxiliaryRoles.has(role) ? t(role) : role;
}

export function localCsvIssueMessage(text: string, code?: string): LocalImportMessage {
  if (csvIssueMessages.has(text)) return message(text);

  if (matchesCsvIssue(code, 'decode_failed')) {
    const match = /^Cannot decode byte (\d+) with (utf-8(?:-sig)?|gb18030)\. Check the source encoding\.$/u.exec(text);
    const [, offset, encoding] = match ?? [];
    if (offset !== undefined && encoding !== undefined) return deferred(() => t(
      'Cannot decode byte {offset} with {encoding}. Check the source encoding.',
      { offset: formatCsvInteger(offset), encoding },
    ));
  }
  if (matchesCsvIssue(code, 'column_limit')) {
    const [, columns] = /^Review the field separator\. CSV preview supports up to (\d+) columns\.$/u.exec(text) ?? [];
    if (columns !== undefined) return deferred(() => t('Review the field separator. CSV preview supports up to {columns} columns.', {
      columns: formatCsvInteger(columns),
    }));
    const [, fields] = /^Check the separator and quoting\. This record exceeds (\d+) fields\.$/u.exec(text) ?? [];
    if (fields !== undefined) return deferred(() => t('Check the separator and quoting. This record exceeds {fields} fields.', {
      fields: formatCsvInteger(fields),
    }));
  }
  if (matchesCsvIssue(code, 'column_missing')) {
    const [, column] = missingHeader.exec(text) ?? [];
    if (column !== undefined) return message('The mapped header {column} is missing. Review the file layout.', { column });
  }
  if (matchesCsvIssue(code, 'auxiliary_column')) {
    const [, column, role] = auxiliaryHeader.exec(text) ?? [];
    if (column !== undefined && role !== undefined) return deferred(() => t(
      '{column} is {role}. Select a native bar field.', { column, role: localCsvAuxiliaryRole(role) },
    ));
  }
  if (code === undefined || code.endsWith('_mismatch')) {
    const [, field, source, reviewed] = declarationMismatch.exec(text) ?? [];
    if (field && source !== undefined && reviewed !== undefined) {
      const name = field === 'volume basis' ? 'volume_semantics' : field.replaceAll(' ', '_');
      const template = declarationMessages.get(field);
      if (template && matchesCsvIssue(code, name + '_mismatch')) return message(template, { source, reviewed });
    }
  }
  if (matchesCsvIssue(code, 'invalid_header')) {
    const [, detail] = /^Check the header delimiter, quoting or field size\. ([\s\S]+)$/u.exec(text) ?? [];
    if (detail !== undefined) return message('Check the header delimiter, quoting or field size. {detail}', { detail });
  }
  if (matchesCsvIssue(code, 'csv_syntax')) {
    const [, detail] = /^Check the CSV quoting or field size\. ([\s\S]+)$/u.exec(text) ?? [];
    if (detail !== undefined) return message('Check the CSV quoting or field size. {detail}', { detail });
  }
  if (matchesCsvIssue(code, 'invalid_timestamp')) {
    const [, detail] = /^Check the source bar start\. ([\s\S]+)$/u.exec(text) ?? [];
    if (detail !== undefined) return deferred(() => t('Check the source bar start. {detail}', {
      detail: timestampIssueDetails.has(detail) ? t(detail) : detail,
    }));
  }
  return text;
}

function csvReviewMessage(text: string, issueMetadata?: { code: string | undefined }): LocalImportMessage {
  const [, record, detail] = /^Review CSV record (\d+)\. ([\s\S]+)$/u.exec(text) ?? [];
  if (record === undefined || detail === undefined) return localCsvIssueMessage(text, issueMetadata?.code);
  const issue = localCsvIssueMessage(detail, issueMetadata?.code);
  // Folder details arrive as text alone, so the record prefix does not establish provenance.
  if (!issueMetadata && typeof issue === 'string') return text;
  return deferred(() => t('Review CSV record {record}. {issue}', {
    record: formatCsvInteger(record), issue: renderLocalImportMessage(issue),
  }));
}

export function localImportErrorMessage(error: unknown): LocalImportMessage | undefined {
  if (!error || typeof error !== 'object' || !('message' in error) || typeof error.message !== 'string') return undefined;
  const text = error.message;
  if (error instanceof NativePickerUnavailable) return message('Local CSV selection is available in the desktop app.');
  if (!('code' in error) || typeof error.code !== 'string') return text;
  if (nativeErrorMessages.has(text)) return message(text);
  const details = 'details' in error ? error.details : undefined;
  if (details && typeof details === 'object' && 'reason' in details && details.reason === 'csv_review_required') {
    const code = 'issue_code' in details && typeof details.issue_code === 'string' ? details.issue_code : undefined;
    return csvReviewMessage(text, { code });
  }
  return text;
}

export function folderRefreshFileDetail(detail: string): string {
  if (folderReviewDetails.has(detail)) return t(detail);
  const [, issue, start, end] = /^(Review CSV record \d+\. [\s\S]+) Source lines (\d+)–(\d+)\.$/u.exec(detail) ?? [];
  const review = csvReviewMessage(issue ?? detail);
  if (typeof review === 'string') return detail;
  const text = renderLocalImportMessage(review);
  if (start === undefined || end === undefined) return text;
  return t('{issue} Source lines {start}–{end}.', {
    issue: text,
    start: formatCsvInteger(start), end: formatCsvInteger(end),
  });
}

export function hasLocalCsvPicker() {
  return isTauri() && !import.meta.env.SCOPEHUB_UBUNTU_PREVIEW;
}

class NativePickerUnavailable extends Error {
  constructor() {
    super('Local CSV selection is available in the desktop app.');
  }
}

function requireNative() {
  if (!hasLocalCsvPicker()) throw new NativePickerUnavailable();
}

export async function chooseLocalCsvFolder() {
  requireNative();
  const locale = getLocale();
  return invoke<LocalFolderCatalog | null>('choose_local_csv_folder', { locale });
}

export async function previewLocalImport(payload: LocalImportPreviewInput, requestId: string) {
  requireNative();
  return invoke<LocalImportPreviewOutput>('execute_operation', {
    operation: 'market.previewLocalImport', payload, requestId,
  });
}

export async function saveLocalImportMapping(payload: SaveLocalImportMappingInput, requestId: string) {
  requireNative();
  return invoke<SaveLocalImportMappingOutput>('execute_operation', {
    operation: 'market.saveLocalImportMapping', payload, requestId,
  });
}

export async function loadLocalImportMapping(payload: LoadLocalImportMappingInput, requestId: string) {
  requireNative();
  return invoke<LoadLocalImportMappingOutput>('execute_operation', {
    operation: 'market.loadLocalImportMapping', payload, requestId,
  });
}

export async function publishLocalImport(payload: PublishLocalImportInput, requestId: string) {
  requireNative();
  return invoke<PublishLocalImportOutput>('execute_operation', {
    operation: 'market.publishLocalImport', payload, requestId,
  });
}

export async function readLocalImportRevision(payload: ReadLocalImportRevisionInput, requestId: string) {
  requireNative();
  return invoke<ReadLocalImportRevisionOutput>('execute_operation', {
    operation: 'market.readLocalImportRevision', payload, requestId,
  });
}

export async function readLocalImportSourceRow(payload: ReadLocalImportSourceRowInput, requestId: string) {
  requireNative();
  return invoke<ReadLocalImportSourceRowOutput>('execute_operation', {
    operation: 'market.readLocalImportSourceRow', payload, requestId,
  });
}

export async function listLocalImportRevisions(payload: ListLocalImportRevisionsInput, requestId: string) {
  requireNative();
  return invoke<ListLocalImportRevisionsOutput>('execute_operation', {
    operation: 'market.listLocalImportRevisions', payload, requestId,
  });
}

export async function revokeLocalImportRevision(payload: RevokeLocalImportRevisionInput, requestId: string) {
  requireNative();
  return invoke<RevokeLocalImportRevisionOutput>('execute_operation', {
    operation: 'market.revokeLocalImportRevision', payload, requestId,
  });
}

export async function previewLocalFolderRefresh(payload: PreviewLocalFolderRefreshInput, requestId: string) {
  requireNative();
  return invoke<PreviewLocalFolderRefreshOutput>('execute_operation', {
    operation: 'market.previewLocalFolderRefresh', payload, requestId,
  });
}

export async function prepareLocalFolderPublication(payload: PrepareLocalFolderPublicationInput, requestId: string) {
  requireNative();
  return invoke<PrepareLocalFolderPublicationOutput>('execute_operation', {
    operation: 'market.prepareLocalFolderPublication', payload, requestId,
  });
}

export async function publishLocalFolderRefresh(payload: PublishLocalFolderRefreshInput, requestId: string) {
  requireNative();
  return invoke<PublishLocalFolderRefreshOutput>('execute_operation', {
    operation: 'market.publishLocalFolderRefresh', payload, requestId,
  });
}

export async function listLocalFolderRevisions(payload: ListLocalFolderRevisionsInput, requestId: string) {
  requireNative();
  return invoke<ListLocalFolderRevisionsOutput>('execute_operation', {
    operation: 'market.listLocalFolderRevisions', payload, requestId,
  });
}

export async function readLocalFolderRevision(payload: ReadLocalFolderRevisionInput, requestId: string) {
  requireNative();
  return invoke<ReadLocalFolderRevisionOutput>('execute_operation', {
    operation: 'market.readLocalFolderRevision', payload, requestId,
  });
}

export async function readLocalFolderSourceRow(payload: ReadLocalFolderSourceRowInput, requestId: string) {
  requireNative();
  return invoke<ReadLocalFolderSourceRowOutput>('execute_operation', {
    operation: 'market.readLocalFolderSourceRow', payload, requestId,
  });
}

export async function revokeLocalFolderRevision(payload: RevokeLocalFolderRevisionInput, requestId: string) {
  requireNative();
  return invoke<RevokeLocalFolderRevisionOutput>('execute_operation', {
    operation: 'market.revokeLocalFolderRevision', payload, requestId,
  });
}

export async function cancelLocalPreview(requestId: string) {
  requireNative();
  await invoke('cancel_local_import_preview', { requestId });
}
