import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import type {
  CsvColumnMappingV1, CsvMappingV1, CsvPreview, LocalImportPreviewInput, LocalImportPreviewOutput, PreviewIssue, PreviewRow,
  PrepareLocalFolderPublicationOutput, PreviewLocalFolderRefreshOutput, PublishedLocalFolderRevision, PublishedLocalImportRevision,
  ReadLocalFolderRevisionOutput, SavedLocalImportMapping,
} from '../../../../contracts/generated/protocol';
import {
  loadLocalImportMapping, localCsvAuxiliaryRole, localCsvIssueMessage, localImportErrorMessage, prepareLocalFolderPublication,
  previewLocalFolderRefresh, publishLocalFolderRefresh, publishLocalImport, readLocalFolderRevision, readLocalImportRevision,
  renderLocalImportMessage, revokeLocalFolderRevision, revokeLocalImportRevision, saveLocalImportMapping, type LocalImportMessage,
} from './local-import-transport';
import { openAuthenticatedFrozenChart, UnavailableSharedSelection, type FrozenChartResult } from './frozen-evidence';
import { formatSourceBarTime, FrozenFolderChart, FrozenLocalChart } from './local-preview-chart';
import { FolderRefreshReview, type FolderPublicationChoices } from './folder-refresh-review';
import { formatNumber, message, plural, t } from './i18n';
import { displayAccessDenied, useWatchPublishedRevision, type PublishedRevisionAccess } from './published-revision-access';
import type { LocalWorkspace } from './use-local-workspace';
import { RevisionAccessActions } from './local-revision-dialog';
import './local-import.css';

export type LocalFolderFile = {
  file_id: string;
  relative_path: string;
  display_name: string;
  byte_count: number;
  kind: 'csv' | 'unsupported_extension' | 'unsupported_entry';
};

export type LocalFolderCatalog = {
  folder_id: string;
  display_name: string;
  files: LocalFolderFile[];
};

type Props = {
  initialCatalog: LocalFolderCatalog;
  chooseFolder: () => Promise<LocalFolderCatalog | null>;
  previewImport: (input: LocalImportPreviewInput, requestId: string) => Promise<LocalImportPreviewOutput>;
  cancelPreview: (requestId: string) => Promise<void>;
  renderChart: (preview: LocalImportPreviewOutput) => ReactNode;
  revisionAccess: PublishedRevisionAccess;
  workspace: LocalWorkspace | undefined;
};

type Inspection = { output: LocalImportPreviewOutput; draft: CsvMappingV1; mapping: CsvMappingV1; rowLimit: number; raw: boolean };
type ApprovedMapping = { draft: CsvMappingV1; sha256: string; record: SavedLocalImportMapping };
type ImportAction = 'preview' | 'save' | 'load' | 'publish' | 'read' | 'revoke' | 'refresh'
  | 'prepare-folder' | 'publish-folder' | 'read-folder' | 'revoke-folder';
type Pending = { action: ImportAction; cancelled: boolean; cancel: () => Promise<void> };
type PreparedFolder = { review: PrepareLocalFolderPublicationOutput; key: string; attempted: boolean };
type StagedFolderRefresh = {
  result: PreviewLocalFolderRefreshOutput;
  requestId: string;
  focusGeneration: number | undefined;
  prepared?: PreparedFolder | undefined;
};
type Progress = { action: ImportAction; phase: 'running' | 'cancelling' | 'cancelled' };
type Locator = Pick<PreviewRow, 'line_start' | 'line_end'>;
type Failure = { message: LocalImportMessage; sha256?: string; headerUnavailable?: boolean; locator?: Locator };
type DraftTextField = Exclude<keyof CsvMappingV1, 'version' | 'revision' | 'columns'>;

const actionText = {
  preview: { running: 'Reading local CSV…', label: 'preview', cancelled: 'Preview cancelled.', failure: 'Unable to preview this CSV file.' },
  save: { running: 'Saving reviewed mapping…', label: 'save', cancelled: 'Save cancelled.', failure: 'Unable to save the reviewed mapping.' },
  load: { running: 'Checking for a reviewed mapping…', label: 'load', cancelled: 'Load cancelled.', failure: 'Unable to load a reviewed mapping.' },
  publish: { running: 'Checking the complete CSV and publishing…', label: 'publication', cancelled: 'Publication cancelled.', failure: 'Unable to publish this CSV file.' },
  read: { running: 'Opening frozen chart…', label: 'chart', cancelled: 'Chart opening cancelled.', failure: 'Unable to open the frozen chart.' },
  refresh: { running: 'Checking complete files in the selected folder…', label: 'folder refresh', cancelled: 'Stopped waiting for the folder refresh.', failure: 'Unable to stage the folder refresh.' },
  revoke: { running: 'Removing access to the revision…', label: 'removal', cancelled: 'Removal cancelled.', failure: 'Unable to remove access to this revision.' },
  'prepare-folder': { running: 'Preparing the reviewed folder…', label: 'folder review', cancelled: 'Folder review cancelled.', failure: 'Unable to prepare the folder publication.' },
  'publish-folder': { running: 'Checking current files and publishing the folder…', label: 'folder publication', cancelled: 'Folder publication cancelled.', failure: 'Unable to confirm the folder publication.' },
  'read-folder': { running: 'Opening frozen folder revision…', label: 'folder revision', cancelled: 'Folder opening cancelled.', failure: 'Unable to open the frozen folder revision.' },
  'revoke-folder': { running: 'Removing access to the folder revision…', label: 'folder access removal', cancelled: 'Access removal cancelled.', failure: 'Unable to remove access to this folder revision.' },
};

const columnFields = [
  ['symbol', 'Symbol column', ['symbol', 'ticker', 'contract', '证券代码', '合约代码']],
  ['timestamp', 'Timestamp column', ['timestamp', 'datetime', 'bar_start', 'bar_start_time', 'barstart', 'barstarttime', '时间戳']],
  ['trading_day', 'Trading day column', ['trading_day', 'trading_date', 'trade_date', 'tradingday', 'tradingdate', 'tradedate', '交易日']],
  ['open', 'Open column', ['open', 'open_price', 'openprice', '开盘价']],
  ['high', 'High column', ['high', 'high_price', 'highprice', '最高价']],
  ['low', 'Low column', ['low', 'low_price', 'lowprice', '最低价']],
  ['close', 'Close column', ['close', 'close_price', 'closeprice', '收盘价']],
  ['volume', 'Volume column', ['volume', 'bar_volume', 'barvolume', '成交量']],
] as const;

const unmappedColumnReason = 'No supported bar field is mapped to this column.';

function canBindColumn(column: string) {
  return column.length > 0 && (column.length <= 1_024 || (column.length <= 2_048 && [...column].length <= 1_024));
}

function draftColumnsFromHeaders(preview: CsvPreview, bound: CsvColumnMappingV1 | undefined) {
  if (preview.header.length === 0 || preview.issues.some(issue => issue.field === 'header' || issue.code === 'decode_failed')) return;

  // Reuse the reader's classifications for raw-only columns, including source declarations.
  const auxiliary = new Set(preview.raw_only_columns
    .filter(({ reason }) => reason !== unmappedColumnReason).map(({ column }) => column));
  const headers = preview.header.filter(column => !auxiliary.has(column)).map(column => ({
    column, key: column.trim().toLowerCase().replace(/[ _-]+/g, '_'),
  }));
  const assigned = new Set(Object.values(bound ?? {}));
  const fill: CsvColumnMappingV1 = {};
  const unresolved: { label: string; candidates: string[] }[] = [];
  for (const [field, label, aliases] of columnFields) {
    if (bound?.[field] !== undefined) continue;
    const candidates = headers.filter(({ key }) => aliases.some(alias => alias === key)).map(({ column }) => column);
    const [candidate] = candidates;
    // An overlength alias still makes the field ambiguous, even when it cannot be bound.
    if (candidate !== undefined && candidates.length === 1 && canBindColumn(candidate) && !assigned.has(candidate)) {
      fill[field] = candidate;
      assigned.add(candidate);
    } else {
      unresolved.push({ label, candidates });
    }
  }
  return { fill, unresolved };
}

const seriesFields = [
  ['venue', 'Venue', 'Exchange or venue', 256], ['timeframe', 'Timeframe', '1m, 5m or 1d', 256],
  ['calendar_id', 'Calendar ID', 'Calendar used by the source', 256], ['session_scope', 'Session scope', 'Regular or night session', 256],
  ['timezone', 'Timezone', 'Asia/Shanghai or UTC', 256], ['quantity_unit', 'Quantity unit', 'Shares, contracts or lots', 256],
  ['price_adjustment', 'Price adjustment', 'Raw or source adjustment basis', 256],
] as const;

const dateFields = [
  ['timestamp_format', 'Timestamp format', '%Y-%m-%d %H:%M', 128],
  ['trading_day_format', 'Trading day format', '%Y-%m-%d', 128],
  ['daily_start_time', 'Daily start time', '09:30:00 (date-only bars)', 32],
] as const;

function newMapping(): CsvMappingV1 {
  return { version: 1, revision: 'preview', encoding: 'utf-8', delimiter: ',', record_kind: 'unclassified' };
}

function importErrorCode(error: unknown) {
  return error && typeof error === 'object' && 'code' in error ? error.code : undefined;
}

function describeImportFailure(error: unknown, fallback: string, action?: ImportAction): Failure {
  if (!error || typeof error !== 'object') return { message: message(fallback) };
  const code = importErrorCode(error);
  if ((code === 'STALE_CONTEXT' || code === 'SNAPSHOT_UNAVAILABLE') && (action === 'prepare-folder' || action === 'publish-folder')) {
    return { message: message('The folder or accepted revision changed. Stage the folder refresh again.') };
  }
  if (code === 'SNAPSHOT_UNAVAILABLE' && action === 'refresh') return {
    message: message('This frozen revision is unavailable in the current profile.'),
  };
  if (code === 'SNAPSHOT_UNAVAILABLE') return {
    message: action === 'read' || action === 'revoke' || action === 'read-folder' || action === 'revoke-folder'
      ? message('This frozen chart is unavailable in the current profile.')
      : message('The file changed since it was inspected. Inspect raw rows again to review the current file.'),
  };
  if (code === 'STALE_CONTEXT' && action !== 'publish') return { message: message('The folder selection has expired. Choose the folder again.') };
  if (action === 'read' && error instanceof Error && error.message === 'The frozen chart reply could not be verified.') {
    return { message: message('The frozen chart reply could not be verified.') };
  }
  const failureMessage = localImportErrorMessage(error) || message(fallback);
  const details = 'details' in error ? error.details : undefined;
  if (details && typeof details === 'object') {
    const sha256 = 'sha256' in details && typeof details.sha256 === 'string' ? details.sha256 : undefined;
    const headerUnavailable = code === 'DATA_UNAVAILABLE' && 'reason' in details && details.reason === 'csv_header_unavailable';
    const locator = 'reason' in details && details.reason === 'csv_review_required' && 'locator' in details ? details.locator : undefined;
    if (locator && typeof locator === 'object' && 'record_number' in locator && typeof locator.record_number === 'number'
      && 'line_start' in locator && typeof locator.line_start === 'number' && 'line_end' in locator && typeof locator.line_end === 'number') {
      return { message: failureMessage, locator: { line_start: locator.line_start, line_end: locator.line_end } };
    }
    return sha256 === undefined ? { message: failureMessage, headerUnavailable } : { message: failureMessage, sha256, headerUnavailable };
  }
  return { message: failureMessage };
}

function cancelPending(pending: Pending) {
  if (pending.cancelled) return;
  pending.cancelled = true;
  void pending.cancel().catch(() => undefined);
}

function ungrouped(value: number) {
  return formatNumber(value, { useGrouping: false });
}

function sourceLines(row: Locator) {
  return row.line_start === row.line_end ? ungrouped(row.line_start) : `${ungrouped(row.line_start)}–${ungrouped(row.line_end)}`;
}

function failureText(failure: Failure) {
  const text = renderLocalImportMessage(failure.message);
  return failure.locator ? t('{message} Source lines {lines}.', { message: text, lines: sourceLines(failure.locator) }) : text;
}

function ImportAlert({ failure }: { failure: Failure }) {
  return <div className="local-import-error" role="alert">
    {failureText(failure)}
    {failure.sha256 && <div>{t('Complete file SHA-256')} <code>{failure.sha256}</code></div>}
  </div>;
}

function rawOnlyReason(reason: string) {
  const preserved = /^Preserved for review as (.+)\.$/.exec(reason);
  if (preserved?.[1]) return t('Preserved for review as {role}.', { role: localCsvAuxiliaryRole(preserved[1]) });
  if (reason === unmappedColumnReason || reason === 'Previous settlement price') return t(reason);
  return reason;
}

function IssueList({ issues }: { issues: PreviewIssue[] }) {
  return <ul className="local-import-issues">{issues.map((issue, index) =>
    <li key={`${issue.code}-${index}`}>{issue.field && <strong>{issue.field}{' · '}</strong>}
      {renderLocalImportMessage(localCsvIssueMessage(issue.message, issue.code))}
    </li>,
  )}</ul>;
}

function Diagnostics({ preview }: { preview: CsvPreview }) {
  const rejected = preview.rows.filter(row => row.issues.length);
  return <section className="local-import-card" aria-label={t('Diagnostics')}>
    <h2>{t('Diagnostics')}</h2>
    {preview.issues.length > 0 && <IssueList issues={preview.issues} />}
    {rejected.map(row => <div className="local-import-row-issues" key={row.record_number}>
      <h3>{t('Record {record} · Source lines {lines}', { record: ungrouped(row.record_number), lines: sourceLines(row) })}</h3>
      <IssueList issues={row.issues} />
    </div>)}
    {preview.issues.length === 0 && rejected.length === 0 && <p>{t('No issues in this preview.')}</p>}
    {preview.raw_only_columns.length > 0 && <details className="local-import-raw-only">
      <summary>{t('Columns kept in raw data ({count})', { count: ungrouped(preview.raw_only_columns.length) })}</summary>
      <ul>{preview.raw_only_columns.map(({ column, reason }, index) =>
        <li key={index}><strong>{column || t('Column {number}', { number: ungrouped(index + 1) })}</strong> · {rawOnlyReason(reason)}</li>)}</ul>
    </details>}
  </section>;
}

function RawValue({ value }: { value: string | undefined }) {
  if (value === undefined) return <span className="local-import-muted">—</span>;
  if (value === '') return <span className="local-import-muted">{t('Empty')}</span>;
  if (value.length > 240) return <details>
    <summary>{t('{count} characters', { count: formatNumber(value.length) })}</summary><pre>{value}</pre>
  </details>;
  return value;
}

function RawRows({ preview }: { preview: CsvPreview }) {
  const [page, setPage] = useState(0);
  const pageSize = 20;
  const first = page * pageSize;
  const pageRows = preview.rows.slice(first, first + pageSize);
  const width = Math.max(1, preview.header.length, ...preview.rows.map(row => row.raw_fields.length));
  const headers = Array.from({ length: width }, (_, index) => preview.header[index] || t('Column {number}', { number: ungrouped(index + 1) }));
  return <section className="local-import-card" aria-label={t('Raw CSV rows')}>
    <h2>{t('Raw CSV rows')}</h2>
    {preview.raw_header_text !== null && <div className="local-import-raw-header">
      <h3>{t('Raw header')}</h3><RawValue value={preview.raw_header_text} />
    </div>}
    {preview.rows.length === 0 ? <p>{t('No source records are available for this preview.')}</p> :
      <div className="local-import-table table-scroll" tabIndex={0} role="region" aria-label={t('Scroll raw CSV rows')}>
        <table aria-label={t('Raw CSV rows')}>
          <thead><tr><th scope="col">{t('Record')}</th><th scope="col">{t('Source lines')}</th>
            {headers.map((header, index) => <th scope="col" key={index}><RawValue value={header} /></th>)}
          </tr></thead>
          <tbody>{pageRows.map(row => <tr key={row.record_number}>
            <th scope="row">{ungrouped(row.record_number)}</th><td>{sourceLines(row)}</td>
            {row.raw_text === null ? headers.map((_, index) => <td key={index}><RawValue value={row.raw_fields[index]} /></td>)
              : <td colSpan={width}><RawValue value={row.raw_text} /></td>}
          </tr>)}</tbody>
        </table>
      </div>}
    {preview.rows.length > pageSize && <div className="local-import-table-nav">
      <p>{t('Records {first}–{last} of {total}', {
        first: ungrouped(first + 1), last: ungrouped(first + pageRows.length), total: ungrouped(preview.rows.length),
      })}</p>
      <button type="button" className="secondary-button" disabled={page === 0} onClick={() => setPage(page - 1)}>{t('Previous records')}</button>
      <button type="button" className="secondary-button" disabled={first + pageSize >= preview.rows.length} onClick={() => setPage(page + 1)}>{t('Next records')}</button>
    </div>}
  </section>;
}

function NormalizedBars({ preview }: { preview: CsvPreview }) {
  const { series } = preview;
  return <section className="local-import-card" aria-label={t('Normalized bars')}>
    <h2>{t('Normalized bars')}</h2>
    {series && <p>{series.venue} · {series.timeframe} · {series.timezone} · {t('Volume in {unit}', { unit: series.quantity_unit })} · {series.price_adjustment}</p>}
    {preview.rows.some(row => row.normalized !== null) ?
      <div className="local-import-table table-scroll" tabIndex={0} role="region" aria-label={t('Scroll normalized bars')}>
        <table aria-label={t('Normalized bars')}>
          <thead><tr>{['Record', 'Source lines', 'Symbol', 'Start (UTC)', 'Trading day', 'Open', 'High', 'Low', 'Close', 'Volume'].map(label =>
            <th key={label} scope="col">{t(label, undefined, label === 'Close' ? 'price' : undefined)}</th>,
          )}</tr></thead>
          <tbody>{preview.rows.map(({ normalized: bar, ...row }) => bar && <tr key={row.record_number}>
            <th scope="row">{ungrouped(row.record_number)}</th><td>{sourceLines(row)}</td><td>{row.symbol}</td><td>{formatSourceBarTime(bar.time_ms)}</td>
            <td>{bar.trading_day}</td><td>{bar.open}</td><td>{bar.high}</td><td>{bar.low}</td><td>{bar.close}</td><td>{bar.volume ?? t('Unknown')}</td>
          </tr>)}</tbody>
        </table>
      </div> : <p>{t('No rows qualified as closed bars. Review the diagnostics and mapping.')}</p>}
  </section>;
}

export function LocalImportEditor({ initialCatalog, chooseFolder, previewImport, cancelPreview, renderChart, revisionAccess, workspace }: Props) {
  const catalog = initialCatalog;
  const [fileId, setFileId] = useState(() => catalog.files.find(file => file.kind === 'csv')?.file_id ?? '');
  const [draft, setDraft] = useState(newMapping);
  const [rowLimit, setRowLimit] = useState(50);
  const [inspection, setInspection] = useState<Inspection>();
  const [suggestionSourceHash, setSuggestionSourceHash] = useState<string>();
  const [approved, setApproved] = useState<ApprovedMapping>();
  const [publishedRecord, setPublished] = useState<PublishedLocalImportRevision>();
  const [frozenChart, setFrozen] = useState<FrozenChartResult>();
  const [stagedRefresh, setFolderRefresh] = useState<StagedFolderRefresh>();
  const [standaloneFocus, setStandaloneFocus] = useState(0);
  const [publishedFolderRecord, setPublishedFolder] = useState<PublishedLocalFolderRevision>();
  const [frozenFolderRecord, setFrozenFolder] = useState<ReadLocalFolderRevisionOutput>();
  const [loadedSourceHash, setLoadedSourceHash] = useState<string>();
  const [failure, setFailure] = useState<Failure>();
  const [mappingFailure, setMappingFailure] = useState<Failure>();
  const [mappingNotice, setMappingNotice] = useState<ReturnType<typeof message>>();
  const [publicationFailure, setPublicationFailure] = useState<Failure>();
  const [publicationNotice, setPublicationNotice] = useState<ReturnType<typeof message>>();
  const [pickerFailure, setPickerFailure] = useState<Failure>();
  const [opening, setOpening] = useState(false);
  const [progress, setProgress] = useState<Progress>();
  const active = useRef<Pending | undefined>(undefined);
  const picker = useRef(false);
  const alive = useRef(true);
  const revision = useRef(0);
  const publishedId = publishedRecord?.revision_id;
  const lostAccess = publishedId !== undefined && revisionAccess.revoked.has(publishedId);
  const revokedSourceHash = lostAccess ? publishedRecord?.source_sha256 : undefined;
  const published = publishedId && revisionAccess.canDisplay(publishedId) ? publishedRecord : undefined;
  const frozen = frozenChart && revisionAccess.canDisplay(frozenChart.revision.revision_id) ? frozenChart : undefined;
  const publishedFolderId = publishedFolderRecord?.revision_id;
  const folderAccessLost = publishedFolderId !== undefined && revisionAccess.revoked.has(publishedFolderId);
  const publishedFolder = publishedFolderId && revisionAccess.canDisplay(publishedFolderId) ? publishedFolderRecord : undefined;
  const frozenFolder = frozenFolderRecord && revisionAccess.canDisplay(frozenFolderRecord.revision.revision_id) ? frozenFolderRecord : undefined;
  const folderRefresh = stagedRefresh?.result;
  const focusGeneration = workspace?.focus.generation ?? standaloneFocus;
  const focusPending = workspace?.focus.pending ?? false;
  const workspaceHandlesFocus = workspace !== undefined;
  const refreshRevisionId = folderRefresh?.folder_base?.revision_id ?? folderRefresh?.base?.revision_id;
  const refreshRevoked = refreshRevisionId !== undefined && revisionAccess.revoked.has(refreshRevisionId);
  const refreshMissing = refreshRevisionId !== undefined && revisionAccess.unavailable.has(refreshRevisionId);
  const refreshNeedsConfirmation = stagedRefresh !== undefined && stagedRefresh.focusGeneration !== focusGeneration;
  const refreshVisible = !focusPending && !refreshNeedsConfirmation && refreshRevisionId !== undefined
    && revisionAccess.canDisplay(refreshRevisionId);
  const refreshKind = folderRefresh?.folder_base ? 'folder' : 'csv';
  const { verify } = revisionAccess;
  useWatchPublishedRevision(lostAccess ? undefined : publishedId, 'csv', verify, workspace);
  useWatchPublishedRevision(folderAccessLost ? undefined : publishedFolderId, 'folder', verify, undefined);
  useWatchPublishedRevision(
    refreshRevoked || (refreshNeedsConfirmation && !refreshMissing)
      || refreshRevisionId === publishedId || refreshRevisionId === publishedFolderId ? undefined : refreshRevisionId,
    refreshKind, verify, refreshKind === 'folder' ? undefined : workspace,
  );

  useLayoutEffect(() => {
    if (workspaceHandlesFocus) return;
    const refocus = () => {
      if (document.visibilityState === 'visible') setStandaloneFocus(generation => generation + 1);
    };
    window.addEventListener('focus', refocus);
    document.addEventListener('visibilitychange', refocus);
    return () => {
      window.removeEventListener('focus', refocus);
      document.removeEventListener('visibilitychange', refocus);
    };
  }, [workspaceHandlesFocus]);

  useEffect(() => {
    if (!refreshNeedsConfirmation || !refreshRevisionId || focusPending || refreshRevoked || refreshMissing) return;
    const controller = new AbortController();
    void verify(refreshKind, refreshRevisionId, controller.signal).then(status => {
      if (controller.signal.aborted) return;
      if (status === 'available') {
        setPublicationFailure(undefined);
        setFolderRefresh(current => current && current.result === folderRefresh ? { ...current, focusGeneration } : current);
      } else if (status === undefined) {
        setPublicationFailure({ message: message('The folder preview could not be verified for this revision.') });
      }
    });
    return () => controller.abort();
  }, [focusGeneration, focusPending, folderRefresh, refreshMissing, refreshNeedsConfirmation, refreshRevisionId, refreshRevoked,
    refreshKind, verify]);

  useLayoutEffect(() => {
    if (refreshRevoked) {
      setFolderRefresh(undefined);
      if (refreshNeedsConfirmation) {
        setPublicationFailure({ message: message('Access removed for this published revision.') });
      }
    }
  }, [refreshNeedsConfirmation, refreshRevoked]);

  useLayoutEffect(() => {
    if (!folderAccessLost) return;
    setPublishedFolder(undefined);
    setFrozenFolder(undefined);
    setPublicationNotice(message('Access removed for this published folder revision.'));
  }, [folderAccessLost]);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      if (active.current) cancelPending(active.current);
      active.current = undefined;
    };
  }, []);

  useLayoutEffect(() => {
    if (!lostAccess) return;
    setPublished(record => record?.revision_id === publishedId ? undefined : record);
    setFrozen(chart => chart?.revision.revision_id === publishedId ? undefined : chart);
    // The editor can reopen the file via its independent folder grant. Discard
    // the preview of the withdrawn bytes that would otherwise reappear here.
    setInspection(value => value?.output.sha256 === revokedSourceHash ? undefined : value);
    setLoadedSourceHash(value => value === revokedSourceHash ? undefined : value);
    setPublicationNotice(message('Access removed for this published revision.'));
  }, [lostAccess, publishedId, revokedSourceHash]);

  const file = catalog.files.find(item => item.file_id === fileId && item.kind === 'csv');
  const native = draft.record_kind === 'native_ohlcv';
  const busy = opening || (progress !== undefined && progress.phase !== 'cancelled');
  const observed = inspection?.output.file_id === fileId ? inspection.output : undefined;
  const sameLayout = inspection?.draft.encoding === draft.encoding && inspection?.draft.delimiter === draft.delimiter;
  const visible = sameLayout ? observed?.preview : undefined;
  const displayedSourceHash = visible ? observed?.sha256 : loadedSourceHash;
  const mapped = !inspection?.raw && inspection?.draft === draft && inspection.rowLimit === rowLimit && !failure ? observed : undefined;
  const reviewed = mapped?.preview.series && mapped.preview.issues.length === 0
    && mapped.preview.rows.some(row => row.normalized?.is_closed) ? inspection : undefined;
  const savedDraft = approved?.draft === draft ? approved : undefined;
  const publishable = savedDraft && reviewed?.output.sha256 === savedDraft.sha256 ? savedDraft : undefined;
  const columns = [...new Set((visible?.header ?? []).filter(canBindColumn))];
  const suggestions = native && visible ? draftColumnsFromHeaders(visible, draft.columns) : undefined;
  let noVolume = 'No volume column';
  while (columns.includes(noVolume)) noVolume += ' ';

  function clearMappingFeedback() {
    setMappingFailure(undefined);
    setMappingNotice(undefined);
  }

  function updateDraft(key: DraftTextField, value: string) {
    setDraft(previous => {
      const next = { ...previous, [key]: value };
      if (!value) delete next[key];
      return next;
    });
    if (key === 'encoding' || key === 'delimiter' || key === 'record_kind') setSuggestionSourceHash(undefined);
    setFailure(undefined);
    setProgress(undefined);
    clearMappingFeedback();
  }

  function selectColumn(key: keyof CsvColumnMappingV1, value: string) {
    setDraft(previous => {
      const columns = { ...previous.columns };
      if (!value) delete columns[key];
      else if (key === 'volume' && value === noVolume) columns.volume = null;
      else columns[key] = value;
      return { ...previous, columns };
    });
    setFailure(undefined);
    setProgress(undefined);
    clearMappingFeedback();
  }

  function applyHeaderSuggestions() {
    if (!suggestions || !visible) return;
    setSuggestionSourceHash(visible.file_sha256);
    if (Object.keys(suggestions.fill).length === 0) return;
    setDraft({ ...draft, columns: { ...draft.columns, ...suggestions.fill } });
    setFailure(undefined);
    setProgress(undefined);
    clearMappingFeedback();
  }

  function selectFile(id: string) {
    setFileId(id);
    setDraft(newMapping());
    setInspection(undefined);
    setSuggestionSourceHash(undefined);
    setApproved(undefined);
    setLoadedSourceHash(undefined);
    setFailure(undefined);
    setProgress(undefined);
    setPublicationFailure(undefined);
    clearMappingFeedback();
  }

  async function replaceFolder() {
    if (picker.current || active.current) return;
    picker.current = true;
    setOpening(true);
    setPickerFailure(undefined);
    try {
      await chooseFolder();
    } catch (error) {
      if (alive.current) setPickerFailure(describeImportFailure(error, 'Unable to choose a local CSV folder.'));
    } finally {
      picker.current = false;
      if (alive.current) setOpening(false);
    }
  }

  async function runRequest<T>(action: ImportAction, invoke: (id: string) => Promise<T>, accept: (value: T, id: string) => void,
    cancelRequest = cancelPreview) {
    if (active.current || picker.current) return;
    const id = crypto.randomUUID();
    const request: Pending = { action, cancelled: false, cancel: () => cancelRequest(id) };
    active.current = request;
    setProgress({ action, phase: 'running' });
    const commits = ['save', 'publish', 'revoke', 'publish-folder', 'revoke-folder'].includes(action);
    const publicationAction = ['publish', 'read', 'revoke', 'refresh', 'prepare-folder', 'publish-folder', 'read-folder', 'revoke-folder'].includes(action);
    if (action === 'preview') setFailure(undefined);
    else if (publicationAction) {
      setPublicationFailure(undefined);
      setPublicationNotice(undefined);
    }
    else clearMappingFeedback();
    let cancelled = false;
    try {
      const reply = await invoke(id);
      cancelled = request.cancelled && !commits;
      // A successful write reply confirms a durable commit even after a cancel request.
      if (active.current === request && !cancelled) accept(reply, id);
    } catch (error) {
      const code = importErrorCode(error);
      cancelled = code === 'CANCELLED' || (request.cancelled && !commits);
      if (active.current === request && action === 'publish-folder' && code === 'CANCELLED') setFolderRefresh(undefined);
      if (active.current === request && !cancelled) {
        const failure = describeImportFailure(error, actionText[action].failure, action);
        if (action === 'preview') {
          setFailure(failure);
        } else if (publicationAction) {
          if (action === 'publish' && (code === 'SNAPSHOT_UNAVAILABLE' || code === 'STALE_CONTEXT')) setApproved(undefined);
          if (action === 'read' && code === 'SNAPSHOT_UNAVAILABLE') setFrozen(undefined);
          if ((action === 'prepare-folder' || action === 'publish-folder')
            && (code === 'STALE_CONTEXT' || code === 'SNAPSHOT_UNAVAILABLE' || code === 'PERMISSION_DENIED')) {
            setFolderRefresh(undefined);
          }
          setPublicationFailure(failure);
        } else {
          if (action === 'load' && failure.headerUnavailable) {
            setInspection(undefined);
            setLoadedSourceHash(undefined);
            setFailure(undefined);
          }
          setMappingFailure(failure);
        }
      }
    } finally {
      if (active.current === request) {
        active.current = undefined;
        setProgress(cancelled ? { action, phase: 'cancelled' } : undefined);
      }
    }
  }

  async function inspect(raw: boolean) {
    if (!file || active.current || picker.current) return;
    const mapping: CsvMappingV1 = (!raw && savedDraft?.record.mapping) || {
      ...draft, revision: `preview-${++revision.current}`,
      record_kind: raw && native ? 'unclassified' : draft.record_kind,
    };
    const sourceHash = loadedSourceHash ?? observed?.sha256;
    const input: LocalImportPreviewInput = {
      version: 1, folder_id: catalog.folder_id, file_id: file.file_id, mapping, row_limit: rowLimit,
      ...(!raw && sourceHash ? { expected_sha256: sourceHash } : {}),
    };
    await runRequest('preview', id => previewImport(input, id), output => {
      if (published && output.sha256 === published.source_sha256 && !revisionAccess.canDisplay(published.revision_id)) return;
      setLoadedSourceHash(undefined);
      if (savedDraft && savedDraft.sha256 !== output.sha256) setApproved(undefined);
      setInspection({ output, draft, mapping, rowLimit, raw });
      clearMappingFeedback();
    });
  }

  async function saveMapping() {
    if (!file || !reviewed) return;
    await runRequest('save', id => saveLocalImportMapping({
      version: 1, folder_id: catalog.folder_id, file_id: file.file_id,
      expected_sha256: reviewed.output.sha256, mapping: reviewed.mapping, row_limit: reviewed.rowLimit,
    }, id), reply => {
      setApproved({ draft, sha256: reviewed.output.sha256, record: reply.record });
      setMappingNotice(message('Reviewed mapping revision {revision} saved for this profile.', { revision: reply.record.review_revision }));
    });
  }

  async function loadMapping() {
    if (!file) return;
    await runRequest('load', id => loadLocalImportMapping({
      version: 1, folder_id: catalog.folder_id, file_id: file.file_id,
    }, id), reply => {
      if (reply.status === 'not_saved') {
        setApproved(undefined);
        setMappingNotice(message('This profile has no reviewed mapping for this file.'));
      } else if (reply.status === 'review_required') {
        setApproved(undefined);
        setInspection(undefined);
        setLoadedSourceHash(reply.source_sha256 ?? undefined);
        setFailure(undefined);
        setMappingNotice(message('The CSV header has changed. Inspect the current raw rows and review the mapping.'));
      } else if (reply.record && reply.source_sha256) {
        setDraft(reply.record.mapping);
        setSuggestionSourceHash(undefined);
        setApproved({ draft: reply.record.mapping, sha256: reply.source_sha256, record: reply.record });
        setLoadedSourceHash(reply.source_sha256);
        if (observed?.sha256 !== reply.source_sha256) setInspection(undefined);
        setFailure(undefined);
        setMappingNotice(message('Reviewed mapping revision {revision} loaded. Preview it against the current file.', { revision: reply.record.review_revision }));
      } else {
        setMappingFailure({ message: message('The saved mapping reply could not be verified.') });
      }
    });
  }

  async function publish() {
    if (!file || !publishable) return;
    await runRequest('publish', id => publishLocalImport({
      version: 1, folder_id: catalog.folder_id, file_id: file.file_id,
      expected_sha256: publishable.sha256, review_revision: publishable.record.review_revision,
    }, id), reply => {
      if (!revisionAccess.canDisplay(reply.revision.revision_id)) {
        setPublicationNotice(message('Access removed for this published revision.'));
        return;
      }
      if (!publishedFolderId && publishedId !== reply.revision.revision_id) setFolderRefresh(undefined);
      setPublished(reply.revision);
      if (frozen?.revision.revision_id !== reply.revision.revision_id) setFrozen(undefined);
    });
  }

  async function stageFolderRefresh(revisionId: string) {
    if (active.current || picker.current) return;
    const focusAtStage = focusPending ? undefined : focusGeneration;
    setFolderRefresh(undefined);
    await runRequest('refresh', id => previewLocalFolderRefresh({
      version: 1, folder_id: catalog.folder_id, revision_id: revisionId,
    }, id), (reply, requestId) => {
      if (!revisionAccess.canDisplay(revisionId)) {
        const accessMessage = revisionAccess.revoked.has(revisionId)
          ? message('Access removed for this published revision.')
          : message('This frozen revision is unavailable in the current profile.');
        setPublicationFailure({ message: accessMessage });
        return;
      }
      if ((reply.folder_base?.revision_id ?? reply.base?.revision_id) !== revisionId) {
        setPublicationFailure({ message: message('The folder preview could not be verified for this revision.') });
        return;
      }
      setFolderRefresh({ result: reply, requestId, focusGeneration: focusAtStage });
    });
  }

  async function prepareFolder(choices: FolderPublicationChoices) {
    const reviewId = folderRefresh?.review_id;
    if (!refreshVisible || !reviewId || !folderRefresh?.publication_allowed || !refreshRevisionId || stagedRefresh?.prepared) return;
    await runRequest('prepare-folder', id => prepareLocalFolderPublication({
      version: 1, folder_id: catalog.folder_id, review_id: reviewId, ...choices,
    }, id), review => {
      if (!revisionAccess.canDisplay(refreshRevisionId)) return;
      if (review.review_id !== reviewId || review.parent_revision_id !== refreshRevisionId || !/^[a-f0-9]{64}$/.test(review.candidate_sha256)) {
        setPublicationFailure({ message: message('The reviewed publication reply could not be verified.') });
        return;
      }
      const prepared = { review, key: crypto.randomUUID(), attempted: false };
      setFolderRefresh(current => current && current.result === folderRefresh ? { ...current, prepared } : current);
    });
  }

  function editFolderChoices() {
    if (!refreshVisible || active.current) return;
    setFolderRefresh(current => current && current.result === folderRefresh ? { ...current, prepared: undefined } : current);
  }

  async function publishFolder() {
    const prepared = stagedRefresh?.prepared;
    if (!refreshVisible || !prepared || !folderRefresh || active.current || picker.current) return;
    const { review, key } = prepared;
    const parent = folderRefresh.folder_base?.revision_id ?? null;
    const anchor = folderRefresh.folder_base?.base_revision_id ?? folderRefresh.base?.revision_id;
    setFolderRefresh(current => current && current.prepared === prepared
      ? { ...current, prepared: { ...prepared, attempted: true } } : current);
    await runRequest('publish-folder', id => publishLocalFolderRefresh({
      version: 1, folder_id: catalog.folder_id, review_id: review.review_id,
      candidate_sha256: review.candidate_sha256, idempotency_key: key,
    }, id), reply => {
      const revision = reply.revision;
      if (revision.base_revision_id !== anchor || revision.parent_revision_id !== parent
        || revision.bar_count !== review.bar_count || revision.source_count !== review.source_count) {
        setPublicationFailure({ message: message('The folder publication receipt could not be verified.') });
        return;
      }
      setFolderRefresh(undefined);
      if (!revisionAccess.canDisplay(revision.revision_id)) {
        setPublicationNotice(message('Access removed for this published folder revision.'));
        return;
      }
      setPublishedFolder(revision);
      setFrozenFolder(undefined);
      setPublicationNotice(message('Reviewed folder published.'));
    });
  }

  async function openFrozenFolder() {
    if (!publishedFolder) return;
    const revisionId = publishedFolder.revision_id;
    const reading = revisionAccess.beginRead(revisionId);
    const controller = new AbortController();
    await runRequest('read-folder', async id => {
      try {
        return await readLocalFolderRevision({ version: 1, revision_id: revisionId }, id, controller.signal);
      } catch (error) {
        if (!controller.signal.aborted && reading() && displayAccessDenied(error)) {
          revisionAccess.withhold(revisionId);
          void verify('folder', revisionId);
        }
        throw error;
      }
    }, reply => {
      if (!reading()) return;
      if (reply.revision.revision_id !== revisionId || reply.bars.length !== publishedFolder.bar_count
        || reply.files.length !== publishedFolder.source_count) {
        setPublicationFailure({ message: message('The frozen folder revision could not be verified.') });
        return;
      }
      setFrozen(undefined);
      setFrozenFolder(reply);
    }, async () => { controller.abort(); });
  }

  async function revokeFrozenFolder() {
    if (!publishedFolder) return;
    await runRequest('revoke-folder', id => revokeLocalFolderRevision({
      version: 1, revision_id: publishedFolder.revision_id,
    }, id), reply => {
      if (reply.revision_id !== publishedFolder.revision_id) {
        setPublicationFailure({ message: message('The folder access removal reply could not be verified.') });
        return;
      }
      revisionAccess.revoke(publishedFolder.revision_id);
    });
  }

  async function openFrozenChart() {
    if (!published) return;
    const revisionId = published.revision_id;
    const reading = revisionAccess.beginRead(revisionId);
    const controller = new AbortController();
    await runRequest('read', async id => {
      try {
        return workspace ? await openAuthenticatedFrozenChart(revisionId, id, controller.signal)
          : await readLocalImportRevision({ version: 1, revision_id: revisionId }, id, controller.signal);
      } catch (error) {
        if (!controller.signal.aborted && reading() && displayAccessDenied(error)) {
          revisionAccess.withhold(revisionId);
          void verify('csv', revisionId);
          workspace?.refreshPublishedAccess(revisionId);
        }
        throw error;
      }
    }, reply => {
      if (!reading()) return;
      if (reply.revision.revision_id !== published.revision_id || reply.revision.source_sha256 !== published.source_sha256
        || reply.revision.mapping_sha256 !== published.mapping_sha256 || reply.bars.length !== published.bar_count) {
        setPublicationFailure({ message: message('The frozen chart reply could not be verified.') });
        return;
      }
      setFrozenFolder(undefined);
      setFrozen(reply);
    }, async () => { controller.abort(); });
  }

  async function revokeFrozenChart() {
    if (!published) return;
    await runRequest('revoke', id => revokeLocalImportRevision({
      version: 1, revision_id: published.revision_id,
    }, id), reply => {
      if (reply.revision_id !== published.revision_id) {
        setPublicationFailure({ message: message('The access removal reply could not be verified.') });
        return;
      }
      revisionAccess.revoke(published.revision_id);
      setPublicationNotice(message('Access removed for this published revision.'));
      workspace?.reload();
    });
  }

  function cancel() {
    const request = active.current;
    if (!request) return;
    cancelPending(request);
    if (request.action === 'refresh') {
      // Staging is read-only. Stop waiting even when the native reply is lost.
      // Native admission still owns an in-flight operation until it drains.
      active.current = undefined;
      setProgress({ action: request.action, phase: 'cancelled' });
    } else {
      setProgress({ action: request.action, phase: 'cancelling' });
    }
  }

  const progressText = opening ? t('Waiting for folder selection…') : progress?.phase === 'running' ? t(actionText[progress.action].running)
    : progress?.phase === 'cancelling' ? t('Cancelling {action}…', { action: t(actionText[progress.action].label) })
      : progress?.phase === 'cancelled' ? t(actionText[progress.action].cancelled) : undefined;

  return <section className="local-import-editor" aria-label={t('Local CSV import')}>
    <div className="local-import-heading">
      <div><h1>{t('Local CSV import')}</h1><p className="local-import-muted">{t('Folder · {name}', { name: catalog.display_name })}</p></div>
      <button type="button" className="secondary-button" disabled={busy} onClick={() => void replaceFolder()}>{t('Choose local CSV folder')}</button>
    </div>
    {pickerFailure && <p className="local-import-error" role="alert">{failureText(pickerFailure)}</p>}
    {failure && <ImportAlert failure={failure} />}
    {mappingFailure && <ImportAlert failure={mappingFailure} />}
    {publicationFailure && <ImportAlert failure={publicationFailure} />}
    {refreshMissing && refreshNeedsConfirmation
      && <ImportAlert failure={{ message: message('This frozen revision is unavailable in the current profile.') }} />}
    <div className="local-import-grid">
      <div className="local-import-controls">
        <fieldset className="local-import-card" disabled={busy}>
          <legend>{t('File and format')}</legend>
          <div className="local-import-fields">
            <label className="local-import-wide">{t('CSV file')} <select value={fileId} onChange={event => selectFile(event.currentTarget.value)}>
              {!file && <option value="">{t('Choose a CSV file')}</option>}
              {catalog.files.map(item => <option key={item.file_id} value={item.file_id} disabled={item.kind !== 'csv'}>
                {item.kind === 'unsupported_extension' ? t('{path} (unsupported format)', { path: item.relative_path })
                  : item.kind === 'unsupported_entry' ? t('{path} (unavailable)', { path: item.relative_path }) : item.relative_path}
              </option>)}
            </select></label>
            {file ? <p className="local-import-wide local-import-muted">{t('{count} bytes', { count: formatNumber(file.byte_count) })}</p>
              : <p className="local-import-wide local-import-muted">{t('This folder contains no supported CSV files.')}</p>}
            <label>{t('Encoding')} <select value={draft.encoding} onChange={event => updateDraft('encoding', event.currentTarget.value)}>
              <option value="utf-8">UTF-8</option><option value="utf-8-sig">{t('UTF-8 with BOM')}</option><option value="gb18030">GB18030</option>
            </select></label>
            <label>{t('Delimiter')} <select value={draft.delimiter} onChange={event => updateDraft('delimiter', event.currentTarget.value)}>
              <option value=",">{t('Comma')}</option><option value=";">{t('Semicolon')}</option><option value={'\t'}>{t('Tab')}</option><option value="|">{t('Pipe')}</option>
            </select></label>
            <label>{t('Record kind')} <select value={draft.record_kind} onChange={event => updateDraft('record_kind', event.currentTarget.value)}>
              <option value="unclassified">{t('Show raw records')}</option>
              <option value="native_ohlcv">{t('Closed OHLCV bars')}</option>
              <option value="trade">{t('Executed trades')}</option>
              <option value="quote_snapshot">{t('Quote snapshots')}</option>
              <option value="depth_snapshot">{t('Order book snapshots')}</option>
              <option value="reference_data">{t('Reference data')}</option>
              <option value="corporate_action">{t('Corporate actions')}</option>
            </select></label>
            <label>{t('Rows per preview')} <select value={rowLimit} onChange={event => {
              setRowLimit(Number(event.currentTarget.value)); setFailure(undefined); setProgress(undefined);
            }}>
              {[1, 25, 50, 100, 250, 500].map(count => <option key={count} value={count}>{formatNumber(count)}</option>)}
            </select></label>
          </div>
          <button type="button" className="secondary-button" disabled={!file} onClick={() => void inspect(true)}>{t('Inspect raw rows')}</button>
          <button type="button" className="secondary-button" disabled={!file} onClick={() => void loadMapping()}>{t('Load reviewed mapping')}</button>
        </fieldset>
        {native && <fieldset className="local-import-card" disabled={busy || !file}>
          <legend>{t('Bar mapping')}</legend>
          {columns.length === 0 && <p className="local-import-muted">{t('Inspect raw rows to load column names.')}</p>}
          <div className="local-import-draft">
            <button type="button" className="secondary-button" disabled={!suggestions} onClick={applyHeaderSuggestions}>{t('Draft mapping from headers')}</button>
            {suggestions && visible?.file_sha256 === suggestionSourceHash && <div role="status">
              <p>{t('Review the column selections and complete the source declarations.')}</p>
              {suggestions.unresolved.length > 0 && <ul className="local-import-issues">
                {suggestions.unresolved.map(({ label, candidates }) => {
                  const [header] = candidates;
                  const overlength = candidates.some(candidate => !canBindColumn(candidate));
                  return <li key={label}>
                    <strong>{t(label)}</strong>{' · '}
                    {header === undefined ? t('No recognized header') : overlength
                      ? t('At least one matching header is too long to bind.') : candidates.length === 1
                        ? t('Header “{header}” is already bound', { header })
                        : t('Choose among {headers}', { headers: candidates.map(header => `“${header}”`).join(', ') })}
                  </li>;
                })}
              </ul>}
            </div>}
          </div>
          <div className="local-import-fields">
            {columnFields.map(([key, label]) => {
              const name = draft.columns?.[key];
              return <label key={key}>{t(label)}<select value={name === null ? noVolume : name ?? ''}
                onChange={event => selectColumn(key, event.currentTarget.value)}>
                <option value="">{t('Select a header')}</option>
                {key === 'volume' && <option value={noVolume}>{t('No volume column')}</option>}
                {name && !columns.includes(name) && <option value={name}>{t('{name} (previous selection)', { name })}</option>}
                {columns.map(column => <option key={column} value={column}>{column}</option>)}
              </select></label>;
            })}
            <label>{t('Instrument type')} <select value={draft.instrument_kind ?? ''} onChange={event => updateDraft('instrument_kind', event.currentTarget.value)}>
              <option value="">{t('Select type')}</option><option value="equity">{t('Stock / equity')}</option>
              <option value="dated_future">{t('Dated futures contract')}</option><option value="continuous_future">{t('Continuous futures series')}</option>
            </select></label>
            <label>{t('Volume basis')} <select value={draft.volume_semantics ?? ''} onChange={event => updateDraft('volume_semantics', event.currentTarget.value)}>
              <option value="">{t('Select basis')}</option><option value="per_bar">{t('Per bar')}</option><option value="cumulative">{t('Cumulative')}</option>
            </select></label>
            {seriesFields.map(([key, label, placeholder, maxLength]) => <label key={key}>{t(label)}
              <input value={draft[key] ?? ''} placeholder={t(placeholder)} maxLength={maxLength} onChange={event => updateDraft(key, event.currentTarget.value)} />
            </label>)}
            {draft.instrument_kind === 'continuous_future' && <label>{t('Roll method')}
              <input value={draft.roll_method ?? ''} maxLength={256} onChange={event => updateDraft('roll_method', event.currentTarget.value)} />
            </label>}
            <label>{t('Timestamp convention')} <select value={draft.timestamp_convention ?? ''} onChange={event => updateDraft('timestamp_convention', event.currentTarget.value)}>
              <option value="">{t('Select convention')}</option><option value="bar_start">{t('Bar start')}</option><option value="bar_end">{t('Bar end')}</option>
            </select></label>
            {dateFields.map(([key, label, placeholder, maxLength]) => <label key={key}>{t(label)}
              <input value={draft[key] ?? ''} placeholder={key === 'daily_start_time' ? t(placeholder) : placeholder} maxLength={maxLength} onChange={event => updateDraft(key, event.currentTarget.value)} />
            </label>)}
          </div>
          <button type="button" className="primary local-import-preview" onClick={() => void inspect(false)}>{t('Preview mapping')}</button>
          {reviewed && <button type="button" className="secondary-button" onClick={() => void saveMapping()}>{t('Save reviewed mapping')}</button>}
        </fieldset>}
        {publishable && <fieldset className="local-import-card" disabled={busy}>
          <legend>{t('Publish reviewed CSV')}</legend>
          <p>{t('ScopeHub checks the complete file and keeps a copy of the original CSV in this profile.')}</p>
          <button type="button" className="primary local-import-preview" onClick={() => void publish()}>{t('Publish complete file')}</button>
        </fieldset>}
      </div>
      <div className="local-import-results">
        {progressText && <div className="local-import-card local-import-progress">
          <p role="status">{progressText}</p>
          {progress && !['revoke', 'revoke-folder', 'prepare-folder'].includes(progress.action)
            && (progress.action !== 'read' || workspace) && progress.phase !== 'cancelled' &&
            <button type="button" className="secondary-button" disabled={progress.phase === 'cancelling'} onClick={cancel}>{t('Cancel {action}', { action: t(actionText[progress.action].label) })}</button>}
        </div>}
        {mappingNotice && <div className="local-import-card" role="status">{t(mappingNotice)}</div>}
        {publicationNotice && <div className="local-import-card" role="status">{t(publicationNotice)}</div>}
        {refreshNeedsConfirmation && !refreshMissing && !refreshRevoked && !publicationFailure
          && <p role="status">{t('Checking access to the compared revision…')}</p>}
        <FolderRefreshReview
          revisionId={publishedFolderId ?? publishedId}
          busy={busy}
          staged={stagedRefresh}
          visible={refreshVisible}
          onRevisionChange={() => setFolderRefresh(undefined)}
          onPreview={revisionId => void stageFolderRefresh(revisionId)}
          prepared={stagedRefresh?.prepared?.review}
          attempted={stagedRefresh?.prepared?.attempted ?? false}
          onPrepare={choices => void prepareFolder(choices)}
          onPublish={() => void publishFolder()}
          onEdit={editFolderChoices}
        />
        {publishedFolder && <section className="local-import-card" aria-label={t('Published local folder')}>
          <h2>{t('Published local folder')}</h2>
          <p role="status">{publishedFolder.symbol} · {plural('{count} bar', '{count} bars', publishedFolder.bar_count)} · {plural('{count} source', '{count} sources', publishedFolder.source_count)}</p>
          <div className="local-import-hash"><strong>{t('Folder revision ID')}</strong><code>{publishedFolder.revision_id}</code></div>
          <div className="local-publication-actions">
            <button type="button" className="secondary-button" disabled={busy} onClick={() => void openFrozenFolder()}>{t('Open frozen folder chart')}</button>
            {frozenFolder && <button type="button" className="secondary-button" disabled={busy} onClick={() => setFrozenFolder(undefined)}>{t('Close frozen folder chart')}</button>}
          </div>
          <RevisionAccessActions key={publishedFolder.revision_id} folder disabled={busy} onRevoke={() => void revokeFrozenFolder()} />
        </section>}
        {published && <section className="local-import-card" aria-label={t('Published local CSV')}>
          <h2>{t('Published local CSV')}</h2>
          <p role="status">{published.symbol} · {t('{count} bars', { count: formatNumber(published.bar_count) })} · {published.relative_path}</p>
          <div className="local-import-hash"><strong>{t('Revision ID')}</strong><code>{published.revision_id}</code></div>
          <div className="local-publication-actions">
            <button type="button" className="secondary-button" disabled={busy} onClick={() => void openFrozenChart()}>{t('Open frozen chart')}</button>
            {frozen && <button type="button" className="secondary-button" disabled={busy} onClick={() => setFrozen(undefined)}>{t('Close frozen chart')}</button>}
          </div>
          <RevisionAccessActions key={published.revision_id} disabled={busy} onRevoke={() => void revokeFrozenChart()} />
        </section>}
        {!frozen && !frozenFolder && <UnavailableSharedSelection workspace={workspace} />}
        {frozen && <FrozenLocalChart key={frozen.revision.revision_id} result={frozen} workspace={workspace} access={revisionAccess} />}
        {frozenFolder && <FrozenFolderChart key={frozenFolder.revision.revision_id} result={frozenFolder} access={revisionAccess} />}
        {file && displayedSourceHash && <section className="local-import-card" aria-label={visible ? t('Preview source') : t('Current source')}>
          <h2>{file.relative_path}</h2>
          {visible && <p>{t('{count} source records', { count: ungrouped(visible.rows.length) })}
            {visible.truncated && <> · {t('Preview omits later records')}</>}
            {mapped && <> · {t('Mapping v{version} · {revision}', {
              version: String(visible.mapping_version), revision: String(visible.mapping_revision),
            })}</>}
          </p>}
          <div className="local-import-hash"><strong>{t('Complete file SHA-256')}</strong><code>{displayedSourceHash}</code></div>
        </section>}
        {mapped && <>{!frozen && !frozenFolder && renderChart(mapped)}<NormalizedBars preview={mapped.preview} /></>}
        {visible ? <><RawRows key={visible.mapping_revision} preview={visible} /><Diagnostics preview={visible} /></> :
          <section className="local-import-card local-import-placeholder"><h2>{t('Raw CSV rows')}</h2>
            <p>{file ? t('Inspect raw rows to check the layout and map the fields.') : t('Choose a folder containing a CSV file to begin.')}</p>
          </section>}
      </div>
    </div>
  </section>;
}
