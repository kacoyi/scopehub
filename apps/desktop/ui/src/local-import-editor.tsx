import { useEffect, useRef, useState, type ReactNode } from 'react';
import type {
  CsvColumnMappingV1, CsvMappingV1, CsvPreview, LocalImportPreviewInput, LocalImportPreviewOutput, PreviewIssue, PreviewRow,
} from '../../../../contracts/generated/protocol';
import { loadLocalImportMapping, saveLocalImportMapping } from './local-import-transport';
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
};

type Inspection = { output: LocalImportPreviewOutput; draft: CsvMappingV1; mapping: CsvMappingV1; rowLimit: number; raw: boolean };
type ImportAction = 'preview' | 'save' | 'load';
type Pending = { action: ImportAction; cancelled: boolean; cancel: () => Promise<void> };
type Progress = { action: ImportAction; phase: 'running' | 'cancelling' | 'cancelled' };
type Failure = { message: string; sha256?: string };
type DraftTextField = Exclude<keyof CsvMappingV1, 'version' | 'revision' | 'columns'>;

const actionText = {
  preview: { running: 'Reading local CSV…', label: 'preview', cancelled: 'Preview cancelled.', failure: 'Unable to preview this CSV file.' },
  save: { running: 'Saving reviewed mapping…', label: 'save', cancelled: 'Save cancelled.', failure: 'Unable to save the reviewed mapping.' },
  load: { running: 'Checking for a reviewed mapping…', label: 'load', cancelled: 'Load cancelled.', failure: 'Unable to load a reviewed mapping.' },
};

const columnFields = [
  ['symbol', 'Symbol column'], ['timestamp', 'Timestamp column'], ['trading_day', 'Trading day column'],
  ['open', 'Open column'], ['high', 'High column'], ['low', 'Low column'], ['close', 'Close column'], ['volume', 'Volume column'],
] as const;

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

function describeImportFailure(error: unknown, fallback: string): Failure {
  if (!error || typeof error !== 'object') return { message: fallback };
  const code = importErrorCode(error);
  if (code === 'SNAPSHOT_UNAVAILABLE') return { message: 'The file changed since it was inspected. Inspect raw rows again to review the current file.' };
  if (code === 'STALE_CONTEXT') return { message: 'The folder selection has expired. Choose the folder again.' };
  const message = 'message' in error && typeof error.message === 'string' && error.message ? error.message : fallback;
  const details = 'details' in error ? error.details : undefined;
  if (details && typeof details === 'object' && 'sha256' in details && typeof details.sha256 === 'string') {
    return { message, sha256: details.sha256 };
  }
  return { message };
}

function cancelPending(pending: Pending) {
  if (pending.cancelled) return;
  pending.cancelled = true;
  void pending.cancel().catch(() => undefined);
}

function sourceLines(row: Pick<PreviewRow, 'line_start' | 'line_end'>) {
  return row.line_start === row.line_end ? String(row.line_start) : `${row.line_start}–${row.line_end}`;
}

function utcTime(milliseconds: number) {
  const time = new Date(milliseconds);
  return Number.isFinite(time.valueOf()) ? time.toISOString() : `${milliseconds} ms UTC`;
}

function IssueList({ issues }: { issues: PreviewIssue[] }) {
  return <ul className="local-import-issues">{issues.map((issue, index) =>
    <li key={`${issue.code}-${index}`}>{issue.field && <strong>{issue.field}{' · '}</strong>}{issue.message}</li>,
  )}</ul>;
}

function Diagnostics({ preview }: { preview: CsvPreview }) {
  const rejected = preview.rows.filter(row => row.issues.length);
  return <section className="local-import-card" aria-label="Diagnostics">
    <h2>Diagnostics</h2>
    {preview.issues.length > 0 && <IssueList issues={preview.issues} />}
    {rejected.map(row => <div className="local-import-row-issues" key={row.record_number}>
      <h3>Record {row.record_number} · Source lines {sourceLines(row)}</h3>
      <IssueList issues={row.issues} />
    </div>)}
    {preview.issues.length === 0 && rejected.length === 0 && <p>No issues in this preview.</p>}
    {preview.raw_only_columns.length > 0 && <details className="local-import-raw-only">
      <summary>Columns kept in raw data ({preview.raw_only_columns.length})</summary>
      <ul>{preview.raw_only_columns.map(({ column, reason }, index) =>
        <li key={index}><strong>{column || `Column ${index + 1}`}</strong> · {reason}</li>)}</ul>
    </details>}
  </section>;
}

function RawValue({ value }: { value: string | undefined }) {
  if (value === undefined) return <span className="local-import-muted">—</span>;
  if (value === '') return <span className="local-import-muted">Empty</span>;
  if (value.length > 240) return <details>
    <summary>{value.length.toLocaleString()} characters</summary><pre>{value}</pre>
  </details>;
  return value;
}

function RawRows({ preview }: { preview: CsvPreview }) {
  const [page, setPage] = useState(0);
  const pageSize = 20;
  const first = page * pageSize;
  const pageRows = preview.rows.slice(first, first + pageSize);
  const width = Math.max(1, preview.header.length, ...preview.rows.map(row => row.raw_fields.length));
  const headers = Array.from({ length: width }, (_, index) => preview.header[index] || `Column ${index + 1}`);
  return <section className="local-import-card" aria-label="Raw CSV rows">
    <h2>Raw CSV rows</h2>
    {preview.raw_header_text !== null && <div className="local-import-raw-header">
      <h3>Raw header</h3><RawValue value={preview.raw_header_text} />
    </div>}
    {preview.rows.length === 0 ? <p>No source records are available for this preview.</p> :
      <div className="local-import-table table-scroll" tabIndex={0} role="region" aria-label="Scroll raw CSV rows">
        <table aria-label="Raw CSV rows">
          <thead><tr><th scope="col">Record</th><th scope="col">Source lines</th>
            {headers.map((header, index) => <th scope="col" key={index}><RawValue value={header} /></th>)}
          </tr></thead>
          <tbody>{pageRows.map(row => <tr key={row.record_number}>
            <th scope="row">{row.record_number}</th><td>{sourceLines(row)}</td>
            {row.raw_text === null ? headers.map((_, index) => <td key={index}><RawValue value={row.raw_fields[index]} /></td>)
              : <td colSpan={width}><RawValue value={row.raw_text} /></td>}
          </tr>)}</tbody>
        </table>
      </div>}
    {preview.rows.length > pageSize && <div className="local-import-table-nav">
      <p>Records {first + 1}–{first + pageRows.length} of {preview.rows.length}</p>
      <button type="button" className="secondary-button" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous records</button>
      <button type="button" className="secondary-button" disabled={first + pageSize >= preview.rows.length} onClick={() => setPage(page + 1)}>Next records</button>
    </div>}
  </section>;
}

function NormalizedBars({ preview }: { preview: CsvPreview }) {
  const { series } = preview;
  return <section className="local-import-card" aria-label="Normalized bars">
    <h2>Normalized bars</h2>
    {series && <p>{series.venue} · {series.timeframe} · {series.timezone} · Volume in {series.quantity_unit} · {series.price_adjustment}</p>}
    {preview.rows.some(row => row.normalized !== null) ?
      <div className="local-import-table table-scroll" tabIndex={0} role="region" aria-label="Scroll normalized bars">
        <table aria-label="Normalized bars">
          <thead><tr>{['Record', 'Source lines', 'Symbol', 'Start (UTC)', 'Trading day', 'Open', 'High', 'Low', 'Close', 'Volume'].map(label =>
            <th key={label} scope="col">{label}</th>,
          )}</tr></thead>
          <tbody>{preview.rows.map(({ normalized: bar, ...row }) => bar && <tr key={row.record_number}>
            <th scope="row">{row.record_number}</th><td>{sourceLines(row)}</td><td>{row.symbol}</td><td>{utcTime(bar.time_ms)}</td>
            <td>{bar.trading_day}</td><td>{bar.open}</td><td>{bar.high}</td><td>{bar.low}</td><td>{bar.close}</td><td>{bar.volume ?? 'Unknown'}</td>
          </tr>)}</tbody>
        </table>
      </div> : <p>No rows qualified as closed bars. Review the diagnostics and mapping.</p>}
  </section>;
}

export function LocalImportEditor({ initialCatalog, chooseFolder, previewImport, cancelPreview, renderChart }: Props) {
  const catalog = initialCatalog;
  const [fileId, setFileId] = useState(() => catalog.files.find(file => file.kind === 'csv')?.file_id ?? '');
  const [draft, setDraft] = useState(newMapping);
  const [rowLimit, setRowLimit] = useState(50);
  const [inspection, setInspection] = useState<Inspection>();
  const [loadedSourceHash, setLoadedSourceHash] = useState<string>();
  const [failure, setFailure] = useState<Failure>();
  const [mappingFailure, setMappingFailure] = useState<Failure>();
  const [mappingNotice, setMappingNotice] = useState<string>();
  const [pickerFailure, setPickerFailure] = useState<string>();
  const [opening, setOpening] = useState(false);
  const [progress, setProgress] = useState<Progress>();
  const active = useRef<Pending | undefined>(undefined);
  const picker = useRef(false);
  const alive = useRef(true);
  const revision = useRef(0);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      if (active.current) cancelPending(active.current);
      active.current = undefined;
    };
  }, []);

  const file = catalog.files.find(item => item.file_id === fileId && item.kind === 'csv');
  const native = draft.record_kind === 'native_ohlcv';
  const busy = opening || (progress !== undefined && progress.phase !== 'cancelled');
  const observed = inspection?.output.file_id === fileId ? inspection.output : undefined;
  const sameLayout = inspection?.draft.encoding === draft.encoding && inspection?.draft.delimiter === draft.delimiter;
  const visible = sameLayout ? observed?.preview : undefined;
  const mapped = !inspection?.raw && inspection?.draft === draft && inspection.rowLimit === rowLimit && !failure ? observed : undefined;
  const reviewed = mapped?.preview.series && mapped.preview.issues.length === 0
    && mapped.preview.rows.some(row => row.normalized?.is_closed) ? inspection : undefined;
  const columns = [...new Set((visible?.header ?? []).filter(column => column.length > 0
    && (column.length <= 1_024 || (column.length <= 2_048 && [...column].length <= 1_024))))];
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

  function selectFile(id: string) {
    setFileId(id);
    setDraft(newMapping());
    setInspection(undefined);
    setLoadedSourceHash(undefined);
    setFailure(undefined);
    setProgress(undefined);
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
      if (alive.current) setPickerFailure(describeImportFailure(error, 'Unable to choose a local CSV folder.').message);
    } finally {
      picker.current = false;
      if (alive.current) setOpening(false);
    }
  }

  async function runRequest<T>(action: ImportAction, invoke: (id: string) => Promise<T>, accept: (value: T) => void) {
    if (active.current || picker.current) return;
    const id = crypto.randomUUID();
    const request: Pending = { action, cancelled: false, cancel: () => cancelPreview(id) };
    active.current = request;
    setProgress({ action, phase: 'running' });
    if (action === 'preview') setFailure(undefined);
    else clearMappingFeedback();
    let cancelled = false;
    try {
      const reply = await invoke(id);
      cancelled = request.cancelled && action !== 'save';
      // A successful save reply confirms a durable commit even after a cancel request.
      if (active.current === request && !cancelled) accept(reply);
    } catch (error) {
      cancelled = importErrorCode(error) === 'CANCELLED' || (request.cancelled && action !== 'save');
      if (active.current === request && !cancelled) {
        const failure = describeImportFailure(error, actionText[action].failure);
        if (action === 'preview') setFailure(failure);
        else setMappingFailure(failure);
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
    const mapping: CsvMappingV1 = {
      ...draft, revision: `preview-${++revision.current}`,
      record_kind: raw && native ? 'unclassified' : draft.record_kind,
    };
    const sourceHash = loadedSourceHash ?? observed?.sha256;
    const input: LocalImportPreviewInput = {
      version: 1, folder_id: catalog.folder_id, file_id: file.file_id, mapping, row_limit: rowLimit,
      ...(!raw && sourceHash ? { expected_sha256: sourceHash } : {}),
    };
    await runRequest('preview', id => previewImport(input, id), output => {
      setLoadedSourceHash(undefined);
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
      setMappingNotice(`Reviewed mapping revision ${reply.record.review_revision} saved for this profile.`);
    });
  }

  async function loadMapping() {
    if (!file) return;
    await runRequest('load', id => loadLocalImportMapping({
      version: 1, folder_id: catalog.folder_id, file_id: file.file_id,
    }, id), reply => {
      if (reply.status === 'not_saved') {
        setMappingNotice('This profile has no reviewed mapping for this file.');
      } else if (reply.status === 'review_required') {
        setInspection(undefined);
        setLoadedSourceHash(undefined);
        setFailure(undefined);
        setMappingNotice('The CSV header has changed. Inspect the current raw rows and review the mapping.');
      } else if (reply.record && reply.source_sha256) {
        setDraft(reply.record.mapping);
        setLoadedSourceHash(reply.source_sha256);
        if (observed?.sha256 !== reply.source_sha256) setInspection(undefined);
        setFailure(undefined);
        setMappingNotice(`Reviewed mapping revision ${reply.record.review_revision} loaded. Preview it against the current file.`);
      } else {
        setMappingFailure({ message: 'The saved mapping reply could not be verified.' });
      }
    });
  }

  function cancel() {
    const request = active.current;
    if (!request) return;
    setProgress({ action: request.action, phase: 'cancelling' });
    cancelPending(request);
  }

  const progressText = opening ? 'Waiting for folder selection…' : progress?.phase === 'running' ? actionText[progress.action].running
    : progress?.phase === 'cancelling' ? `Cancelling ${actionText[progress.action].label}…`
      : progress?.phase === 'cancelled' ? actionText[progress.action].cancelled : undefined;

  return <section className="local-import-editor" aria-label="Local CSV import">
    <div className="local-import-heading">
      <div><h1>Local CSV preview</h1><p className="local-import-muted">Folder · {catalog.display_name}</p></div>
      <button type="button" className="secondary-button" disabled={busy} onClick={() => void replaceFolder()}>Choose local CSV folder</button>
    </div>
    {pickerFailure && <p className="local-import-error" role="alert">{pickerFailure}</p>}
    {failure && <div className="local-import-error" role="alert">{failure.message}
      {failure.sha256 && <div>Complete file SHA-256 <code>{failure.sha256}</code></div>}
    </div>}
    {mappingFailure && <div className="local-import-error" role="alert">{mappingFailure.message}
      {mappingFailure.sha256 && <div>Complete file SHA-256 <code>{mappingFailure.sha256}</code></div>}
    </div>}
    <div className="local-import-grid">
      <div className="local-import-controls">
        <fieldset className="local-import-card" disabled={busy}>
          <legend>File and format</legend>
          <div className="local-import-fields">
            <label className="local-import-wide">CSV file <select value={fileId} onChange={event => selectFile(event.currentTarget.value)}>
              {!file && <option value="">Choose a CSV file</option>}
              {catalog.files.map(item => <option key={item.file_id} value={item.file_id} disabled={item.kind !== 'csv'}>
                {item.relative_path}{item.kind === 'unsupported_extension' ? ' (unsupported format)' : item.kind === 'unsupported_entry' ? ' (unavailable)' : ''}
              </option>)}
            </select></label>
            {file ? <p className="local-import-wide local-import-muted">{file.byte_count.toLocaleString()} bytes</p>
              : <p className="local-import-wide local-import-muted">This folder contains no supported CSV files.</p>}
            <label>Encoding <select value={draft.encoding} onChange={event => updateDraft('encoding', event.currentTarget.value)}>
              <option value="utf-8">UTF-8</option><option value="utf-8-sig">UTF-8 with BOM</option><option value="gb18030">GB18030</option>
            </select></label>
            <label>Delimiter <select value={draft.delimiter} onChange={event => updateDraft('delimiter', event.currentTarget.value)}>
              <option value=",">Comma</option><option value=";">Semicolon</option><option value={'\t'}>Tab</option><option value="|">Pipe</option>
            </select></label>
            <label>Record kind <select value={draft.record_kind} onChange={event => updateDraft('record_kind', event.currentTarget.value)}>
              <option value="unclassified">Show raw records</option><option value="native_ohlcv">Closed OHLCV bars</option><option value="quote_snapshot">Quote snapshots</option>
            </select></label>
            <label>Rows per preview <select value={rowLimit} onChange={event => {
              setRowLimit(Number(event.currentTarget.value)); setFailure(undefined); setProgress(undefined);
            }}>
              {[1, 25, 50, 100, 250, 500].map(count => <option key={count} value={count}>{count}</option>)}
            </select></label>
          </div>
          <button type="button" className="secondary-button" disabled={!file} onClick={() => void inspect(true)}>Inspect raw rows</button>
          <button type="button" className="secondary-button" disabled={!file} onClick={() => void loadMapping()}>Load reviewed mapping</button>
        </fieldset>
        {native && <fieldset className="local-import-card" disabled={busy || !file}>
          <legend>Bar mapping</legend>
          {columns.length === 0 && <p className="local-import-muted">Inspect raw rows to load column names.</p>}
          <div className="local-import-fields">
            {columnFields.map(([key, label]) => {
              const name = draft.columns?.[key];
              return <label key={key}>{label}<select value={name === null ? noVolume : name ?? ''}
                onChange={event => selectColumn(key, event.currentTarget.value)}>
                <option value="">Select a header</option>
                {key === 'volume' && <option value={noVolume}>No volume column</option>}
                {name && !columns.includes(name) && <option value={name}>{name} (previous selection)</option>}
                {columns.map(column => <option key={column} value={column}>{column}</option>)}
              </select></label>;
            })}
            <label>Instrument type <select value={draft.instrument_kind ?? ''} onChange={event => updateDraft('instrument_kind', event.currentTarget.value)}>
              <option value="">Select type</option><option value="equity">Stock / equity</option>
              <option value="dated_future">Dated futures contract</option><option value="continuous_future">Continuous futures series</option>
            </select></label>
            <label>Volume basis <select value={draft.volume_semantics ?? ''} onChange={event => updateDraft('volume_semantics', event.currentTarget.value)}>
              <option value="">Select basis</option><option value="per_bar">Per bar</option><option value="cumulative">Cumulative</option>
            </select></label>
            {seriesFields.map(([key, label, placeholder, maxLength]) => <label key={key}>{label}
              <input value={draft[key] ?? ''} placeholder={placeholder} maxLength={maxLength} onChange={event => updateDraft(key, event.currentTarget.value)} />
            </label>)}
            {draft.instrument_kind === 'continuous_future' && <label>Roll method
              <input value={draft.roll_method ?? ''} maxLength={256} onChange={event => updateDraft('roll_method', event.currentTarget.value)} />
            </label>}
            <label>Timestamp convention <select value={draft.timestamp_convention ?? ''} onChange={event => updateDraft('timestamp_convention', event.currentTarget.value)}>
              <option value="">Select convention</option><option value="bar_start">Bar start</option><option value="bar_end">Bar end</option>
            </select></label>
            {dateFields.map(([key, label, placeholder, maxLength]) => <label key={key}>{label}
              <input value={draft[key] ?? ''} placeholder={placeholder} maxLength={maxLength} onChange={event => updateDraft(key, event.currentTarget.value)} />
            </label>)}
          </div>
          <button type="button" className="primary local-import-preview" onClick={() => void inspect(false)}>Preview mapping</button>
          {reviewed && <button type="button" className="secondary-button" onClick={() => void saveMapping()}>Save reviewed mapping</button>}
        </fieldset>}
      </div>
      <div className="local-import-results">
        {progressText && <div className="local-import-card local-import-progress">
          <p role="status">{progressText}</p>
          {progress && progress.phase !== 'cancelled' &&
            <button type="button" className="secondary-button" disabled={progress.phase === 'cancelling'} onClick={cancel}>Cancel {actionText[progress.action].label}</button>}
        </div>}
        {mappingNotice && <div className="local-import-card" role="status">{mappingNotice}</div>}
        {observed && visible && <section className="local-import-card" aria-label="Preview source">
          <h2>{observed.relative_path}</h2>
          <p>{visible.rows.length} source records{visible.truncated ? ' · Preview omits later records' : ''}{mapped && ` · Mapping v${visible.mapping_version} · ${visible.mapping_revision}`}</p>
          <div className="local-import-hash"><strong>Complete file SHA-256</strong><code>{observed.sha256}</code></div>
        </section>}
        {mapped && <>{renderChart(mapped)}<NormalizedBars preview={mapped.preview} /></>}
        {visible ? <><RawRows key={visible.mapping_revision} preview={visible} /><Diagnostics preview={visible} /></> :
          <section className="local-import-card local-import-placeholder"><h2>Raw CSV rows</h2>
            <p>{file ? 'Inspect raw rows to check the layout and map the fields.' : 'Choose a folder containing a CSV file to begin.'}</p>
          </section>}
      </div>
    </div>
  </section>;
}
