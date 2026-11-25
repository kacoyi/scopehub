import { useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import type {
  Bar, CsvDeclarationConflict, FolderRefreshSource, LocalImportPreviewOutput, LocalImportRowLocator,
  PreviewSeries, ReadLocalFolderRevisionOutput,
} from '../../../../contracts/generated/protocol';
import type { ChartStyle, HistoryChartHandle, IndicatorChartLayer } from './chart-adapter/history';
import { FrozenEvidenceControls, type FrozenChartResult } from './frozen-evidence';
import { formatDateTime, formatNumber, getLocale, message, t } from './i18n';
import { hasLocalCsvPicker, readLocalFolderSourceRow, readLocalImportSourceRow } from './local-import-transport';
import { IndicatorPreview } from './indicator-preview';
import { PriceChart } from './price-chart';
import { displayAccessDenied, type PublishedRevisionAccess } from './published-revision-access';
import type { LocalWorkspace } from './use-local-workspace';

type ChartProps = {
  title: string;
  label: string;
  styleLabel: string;
  series: PreviewSeries | undefined;
  symbol: string | undefined;
  bars: readonly Bar[] | undefined;
  emptyMessage?: string;
  controls?: ReactNode;
  indicatorPreview?: IndicatorChartLayer | undefined;
  acceptedIndicators?: readonly IndicatorChartLayer[] | undefined;
};

function LocalBarChart({ title, label, styleLabel, series, symbol, bars, emptyMessage, controls, indicatorPreview, acceptedIndicators }: ChartProps) {
  const [style, setStyle] = useState<ChartStyle>('candlestick');
  const handle = useRef<HistoryChartHandle | null>(null);
  const projection = useMemo(() => {
    if (!series || !bars || symbol === undefined) return;
    return {
      instrument: {
        id: symbol, name: symbol, display_symbol: symbol,
        asset_class: series.instrument_kind === 'equity' ? 'stock' : 'future', price_tick: null,
      },
      history: { dataset: { instrument_id: symbol, timeframe: series.timeframe, timezone: series.timezone }, bars },
    };
  }, [bars, series, symbol]);

  return <section className="local-chart-preview" aria-label={label}>
    <div className="local-chart-tools">
      <h2>{title}</h2>
      {controls}
      {projection && <div role="group" aria-label={styleLabel}>
        <button type="button" aria-pressed={style === 'candlestick'} onClick={() => setStyle('candlestick')}>{t('Candles')}</button>
        <button type="button" aria-pressed={style === 'line'} onClick={() => setStyle('line')}>{t('Close-price line')}</button>
      </div>}
    </div>
    {projection && series ? <>
      <div className="local-chart-canvas">
        <PriceChart instrument={projection.instrument} history={projection.history} handle={handle} style={style}
          cursor="crosshair" replayBar={undefined} repairedBars={undefined} indicatorPreview={indicatorPreview} acceptedIndicators={acceptedIndicators} />
      </div>
      <footer className="chart-footer">
        <span>{series.venue} · {series.timezone}</span><span>{t('Volume in {unit}', { unit: series.quantity_unit })}</span><span>{series.price_adjustment}</span>
        {series.instrument_kind === 'dated_future' && <span>{t('Dated futures contract')}</span>}
        {series.instrument_kind === 'continuous_future' && <>
          <span>{t('Continuous futures series')}</span>
          {series.roll_method && <span>{t('Roll method {method}', { method: series.roll_method })}</span>}
        </>}
        <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">Charts by TradingView Lightweight Charts™</a>
      </footer>
    </> : <p role="status" className="panel-message">{emptyMessage}</p>}
  </section>;
}

export function LocalPreviewChart({ result }: { result: LocalImportPreviewOutput }) {
  const [pickedSymbol, setPickedSymbol] = useState('');
  const { series, rows } = result.preview;
  const bySymbol = useMemo(() => {
    const groups = new Map<string, Bar[]>();
    for (const row of rows) {
      if (!row.symbol || !row.normalized) continue;
      const bars = groups.get(row.symbol);
      if (bars) bars.push(row.normalized);
      else groups.set(row.symbol, [row.normalized]);
    }
    for (const bars of groups.values()) bars.sort((a, b) => a.time_ms - b.time_ms);
    return groups;
  }, [rows]);
  const symbol = bySymbol.has(pickedSymbol) ? pickedSymbol : bySymbol.keys().next().value;
  const bars = symbol === undefined ? undefined : bySymbol.get(symbol);
  const overlapping = bars?.some((bar, index) => index > 0 && bars[index - 1]?.time_ms === bar.time_ms);
  return <LocalBarChart title={t('Chart preview')} label={t('Local CSV chart preview')} styleLabel={t('Preview chart style')}
    series={series ?? undefined} symbol={symbol} bars={overlapping ? undefined : bars}
    emptyMessage={overlapping ? t('Review overlapping bars for this symbol before charting.')
      : t('Apply a closed-bar mapping to see a chart of the normalized rows.')}
    controls={bySymbol.size > 1 && <label>{t('Chart symbol')}{' '}<select value={symbol} onChange={event => setPickedSymbol(event.currentTarget.value)}>
      {Array.from(bySymbol.keys(), key => <option key={key}>{key}</option>)}
    </select></label>} />;
}

export function FrozenLocalChart({ result, workspace, access }: {
  result: FrozenChartResult; workspace: LocalWorkspace | undefined; access: PublishedRevisionAccess;
}) {
  const [indicatorPreview, setIndicatorPreview] = useState<IndicatorChartLayer>();
  const [acceptedIndicators, setAcceptedIndicators] = useState<readonly IndicatorChartLayer[]>([]);
  const { revision, bars: events } = result;
  const bars = useMemo(() => events.map(event => event.bar), [events]);
  const showIndicators = result.snapshot && workspace && access.canDisplay(revision.revision_id);
  return <>
    <LocalBarChart title={t('Frozen chart')} label={t('Frozen local CSV chart')} styleLabel={t('Frozen chart style')}
      series={revision.series} symbol={revision.symbol} bars={bars} indicatorPreview={showIndicators ? indicatorPreview : undefined}
      acceptedIndicators={showIndicators ? acceptedIndicators : undefined} />
    {result.snapshot && workspace && <IndicatorPreview snapshot={result.snapshot} bars={bars} workspace={workspace}
      access={access} showResult={setIndicatorPreview} showAccepted={setAcceptedIndicators} />}
    {result.snapshot && workspace && <FrozenEvidenceControls snapshot={result.snapshot} bars={bars} workspace={workspace} access={access} />}
    <section className="local-import-card" aria-label={t('Published source provenance')}>
      <h2>{t('Published source provenance')}</h2>
      <div className="local-import-hash"><strong>{t('Dataset revision')}</strong><code>{revision.revision_id}</code></div>
      <div className="local-import-hash"><strong>{t('Source SHA-256')}</strong><code>{revision.source_sha256}</code></div>
      <div className="local-import-hash"><strong>{t('Reviewed mapping {revision} SHA-256', { revision: revision.review_revision })}</strong><code>{revision.mapping_sha256}</code></div>
      <FrozenSourceRows result={result} workspace={workspace} access={access} />
    </section>
  </>;
}

type OriginalSourceRow = {
  locator: LocalImportRowLocator;
  header: string[];
  raw_fields: string[];
  declaration_conflicts?: CsvDeclarationConflict[] | null | undefined;
  relativePath?: string;
};

type SourceRowRead = { loading: number } | { error: ReturnType<typeof message> } | { row: OriginalSourceRow };

function sourceLines(locator: LocalImportRowLocator) {
  const start = formatNumber(locator.line_start, { useGrouping: false });
  return locator.line_start === locator.line_end ? start : `${start}–${formatNumber(locator.line_end, { useGrouping: false })}`;
}

export function formatSourceBarTime(time: number) {
  const date = new Date(time);
  if (!Number.isFinite(date.valueOf())) return t('{value} ms UTC', { value: String(time) });
  return getLocale() === 'en' ? date.toISOString() : formatDateTime(date, {
    timeZone: 'UTC', timeZoneName: 'short', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', fractionalSecondDigits: 3, hourCycle: 'h23',
  });
}

export function formatSourceBarValue(value: number) {
  return getLocale() === 'en' ? String(value) : formatNumber(value, { useGrouping: false, maximumSignificantDigits: 21 });
}

function sameLocator(left: LocalImportRowLocator, right: LocalImportRowLocator) {
  return left.record_number === right.record_number && left.line_start === right.line_start && left.line_end === right.line_end;
}

function sourceRowErrorCode(error: unknown) {
  return error && typeof error === 'object' && 'code' in error ? error.code : undefined;
}

function useSourceRowRead(revisionId: string, access: PublishedRevisionAccess, workspace?: LocalWorkspace) {
  const [read, setRead] = useState<SourceRowRead>();
  const pending = useRef<string | undefined>(undefined);

  useLayoutEffect(() => () => { pending.current = undefined; }, []);

  function clearRead() {
    pending.current = undefined;
    setRead(undefined);
  }

  async function inspect(
    recordNumber: number,
    load: (requestId: string, review: boolean) => Promise<OriginalSourceRow | undefined>,
  ) {
    if (!access.canDisplay(revisionId)) return;
    let requestId = crypto.randomUUID();
    pending.current = requestId;
    setRead({ loading: recordNumber });
    try {
      let row;
      try {
        row = await load(requestId, true);
      } catch (error) {
        const code = sourceRowErrorCode(error);
        if (code !== 'RESOURCE_LIMIT' && code !== 'UNSUPPORTED_CAPABILITY') throw error;
        if (pending.current !== requestId || !access.canDisplay(revisionId)) return;
        requestId = crypto.randomUUID();
        pending.current = requestId;
        row = await load(requestId, false);
      }
      if (pending.current !== requestId || !access.canDisplay(revisionId)) return;
      if (!row || row.header.length !== row.raw_fields.length) {
        setRead({ error: message('The original CSV row could not be verified.') });
        return;
      }
      setRead({ row });
    } catch (error) {
      if (pending.current !== requestId || !access.canDisplay(revisionId)) return;
      if (displayAccessDenied(error)) {
        clearRead();
        access.revoke(revisionId);
        workspace?.reload();
        return;
      }
      const code = sourceRowErrorCode(error);
      setRead({ error: code === 'RESOURCE_LIMIT' ? message('This original CSV row exceeds the inspection size limit.')
        : code === 'BUSY' ? message('The CSV reader is busy. Try inspecting the record again.') : message('Unable to inspect the original CSV row.') });
    } finally {
      if (pending.current === requestId) pending.current = undefined;
    }
  }

  return { read: access.canDisplay(revisionId) ? read : undefined, clearRead, inspect };
}

function OriginalSourceFields({ read }: { read: SourceRowRead | undefined }) {
  if (!read) return null;
  if ('loading' in read) return <p role="status">{t('Reading source record {record}…', { record: formatNumber(read.loading, { useGrouping: false }) })}</p>;
  if ('error' in read) return <p role="alert" className="local-import-error">{t(read.error)}</p>;
  const { row } = read;
  return <section aria-label={t('Original CSV fields')}>
    <h3>{t('Original CSV fields')}</h3>
    <p>{row.relativePath && `${row.relativePath} · `}{t('Record {record} · Source lines {lines}', {
      record: formatNumber(row.locator.record_number, { useGrouping: false }), lines: sourceLines(row.locator),
    })}</p>
    {row.declaration_conflicts == null ? <p>{t('Declaration review is unavailable for this record.')}</p>
      : row.declaration_conflicts.length === 0 ? <p>{t('No declaration conflicts in this record.')}</p>
        : <div className="local-import-error" role="alert">
          <p>{t('Original declarations conflict with the mapping used for this frozen revision.')}</p>
          <div className="local-import-table local-import-source-fields local-import-declarations table-scroll">
            <table aria-label={t('Conflicting CSV declarations')}>
              <thead><tr>
                <th scope="col">{t('Source column')}</th>
                <th scope="col">{t('Original CSV value')}</th>
                <th scope="col">{t('Frozen mapping value')}</th>
              </tr></thead>
              <tbody>{row.declaration_conflicts.map(({ column_index, reviewed_value }) => <tr key={column_index}>
                <th scope="row">{row.header[column_index]}</th>
                <td>{row.raw_fields[column_index]}</td>
                <td>{reviewed_value ?? t('No applicable value')}</td>
              </tr>)}</tbody>
            </table>
          </div>
        </div>}
    <div className="local-import-table local-import-source-fields table-scroll" tabIndex={0} role="region" aria-label={t('Scroll original CSV fields')}>
      <table aria-label={t('Original CSV fields')}>
        <thead><tr><th scope="col">{t('Source column')}</th><th scope="col">{t('Value')}</th></tr></thead>
        <tbody>{row.header.map((column, index) => <tr key={column}>
          <th scope="row">{column}</th><td>{row.raw_fields[index]}</td>
        </tr>)}</tbody>
      </table>
    </div>
  </section>;
}

function FrozenSourceRows({ result, workspace, access }: {
  result: FrozenChartResult; workspace: LocalWorkspace | undefined; access: PublishedRevisionAccess;
}) {
  const [page, setPage] = useState(0);
  const [record, setRecord] = useState('');
  const { revision, bars: events } = result;
  const revisionId = revision.revision_id;
  const { read, clearRead, inspect } = useSourceRowRead(revisionId, access, workspace);
  const native = hasLocalCsvPicker();
  const rows = useMemo(() => events.flatMap(({ bar, locators }) => locators.map(locator => ({ bar, locator }))), [events]);
  const pageSize = 20;
  const first = page * pageSize;
  const last = Math.min(first + pageSize, rows.length);
  const recordNumber = Number(record);

  function changePage(next: number) {
    clearRead();
    setPage(next);
  }

  function readSourceRow(locator: LocalImportRowLocator) {
    return inspect(locator.record_number, async (requestId, review) => {
      const original = { version: 1, revision_id: revisionId, locator };
      const row = await readLocalImportSourceRow(review ? { ...original, review_declarations: true } : original, requestId);
      if (row.revision_id === revisionId && row.source_sha256 === revision.source_sha256 && sameLocator(row.locator, locator)) return row;
    });
  }

  return <details className="local-import-raw-only" onToggle={event => { if (!event.currentTarget.open) clearRead(); }}>
    <summary onClick={event => { if (event.currentTarget.parentElement?.hasAttribute('open')) clearRead(); }}>
      {t('Source rows ({count})', { count: formatNumber(revision.source_record_count) })}
    </summary>
    {native && <form className="local-import-table-nav local-import-source-lookup" onSubmit={event => {
      event.preventDefault();
      const index = rows.findIndex(({ locator }) => locator.record_number === recordNumber);
      const target = rows[index];
      if (target) {
        setPage(Math.floor(index / pageSize));
        void readSourceRow(target.locator);
      }
    }}>
      <label>{t('Source record')}{' '}<input type="number" min={1} max={revision.source_record_count} step={1} value={record}
        onChange={event => setRecord(event.currentTarget.value)} /></label>
      <button type="submit" className="secondary-button" disabled={!Number.isInteger(recordNumber) || recordNumber < 1
        || recordNumber > revision.source_record_count}>{t('Inspect source record')}</button>
    </form>}
    <div className="local-import-table table-scroll" tabIndex={0} role="region" aria-label={t('Scroll published source rows')}>
      <table aria-label={t('Published source rows')}>
        <thead><tr>{['Record', 'Source lines', 'Bar start (UTC)', 'Trading day', 'Close', ...(native ? ['Action'] : [])].map(label =>
          <th scope="col" key={label}>{label === 'Close' ? t('Close', undefined, 'price') : t(label)}</th>,
        )}</tr></thead>
        <tbody>{rows.slice(first, last).map(({ bar, locator }) => <tr key={locator.record_number}>
          <th scope="row">{formatNumber(locator.record_number, { useGrouping: false })}</th>
          <td>{sourceLines(locator)}</td>
          <td>{formatSourceBarTime(bar.time_ms)}</td><td>{bar.trading_day}</td><td>{formatSourceBarValue(bar.close)}</td>
          {native && <td><button type="button" className="secondary-button" onClick={() => void readSourceRow(locator)}>
            {t('Inspect record {record}', { record: formatNumber(locator.record_number, { useGrouping: false }) })}
          </button></td>}
        </tr>)}</tbody>
      </table>
    </div>
    {rows.length > pageSize && <div className="local-import-table-nav">
      <p>{t('Source rows {first}–{last} of {count}', {
        first: formatNumber(first + 1, { useGrouping: false }), last: formatNumber(last, { useGrouping: false }),
        count: formatNumber(rows.length, { useGrouping: false }),
      })}</p>
      <button type="button" className="secondary-button" disabled={page === 0} onClick={() => changePage(page - 1)}>{t('Previous source rows')}</button>
      <button type="button" className="secondary-button" disabled={last >= rows.length} onClick={() => changePage(page + 1)}>{t('Next source rows')}</button>
    </div>}
    <OriginalSourceFields read={read} />
  </details>;
}

export function FrozenFolderChart({ result, access }: { result: ReadLocalFolderRevisionOutput; access: PublishedRevisionAccess }) {
  const [page, setPage] = useState(0);
  const { revision, files, bars: events } = result;
  const revisionId = revision.revision_id;
  const { read, clearRead, inspect } = useSourceRowRead(revisionId, access);
  const native = hasLocalCsvPicker();
  const bars = useMemo(() => events.map(event => event.bar), [events]);
  const rows = useMemo(() => events.flatMap(({ bar, sources }) => sources.map(source => ({ bar, source }))), [events]);
  const pageSize = 20;
  const first = page * pageSize;
  const last = Math.min(first + pageSize, rows.length);

  function changePage(next: number) {
    clearRead();
    setPage(next);
  }

  function readSourceRow(source: FolderRefreshSource) {
    return inspect(source.record_number, async (requestId, review) => {
      const original = { version: 1, revision_id: revisionId, source };
      const row = await readLocalFolderSourceRow(review ? { ...original, review_declarations: true } : original, requestId);
      if (row.revision_id === revisionId && row.source.relative_path === source.relative_path
        && row.source.source_sha256 === source.source_sha256 && sameLocator(row.source, source)) {
        return {
          locator: row.source, header: row.header, raw_fields: row.raw_fields,
          declaration_conflicts: 'declaration_conflicts' in row ? row.declaration_conflicts : undefined, relativePath: row.source.relative_path,
        };
      }
    });
  }

  return <>
    <LocalBarChart title={t('Frozen folder chart')} label={t('Frozen local folder chart')} styleLabel={t('Frozen folder chart style')}
      series={revision.series} symbol={revision.symbol} bars={bars.length ? bars : undefined}
      emptyMessage={t('This reviewed folder revision withdrew all accepted bars.')} />
    <section className="local-import-card" aria-label={t('Published folder source provenance')}>
      <h2>{t('Published folder source provenance')}</h2>
      <div className="local-import-hash"><strong>{t('Folder revision')}</strong><code>{revision.revision_id}</code></div>
      <div className="local-import-hash"><strong>{t('Prior revision')}</strong><code>{revision.parent_revision_id ?? revision.base_revision_id}</code></div>
      <details className="local-import-raw-only">
        <summary>{t('Source files ({count})', { count: formatNumber(files.length) })}</summary>
        {files.length ? <ul>{files.map(file => <li key={file.relative_path}>
          <strong>{file.relative_path}</strong> · {t('{count} records', { count: formatNumber(file.record_count) })} · SHA-256 <code>{file.source_sha256}</code>
        </li>)}</ul> : <p>{t('No sources remain in this reviewed revision.')}</p>}
      </details>
      {rows.length > 0 && <details className="local-import-raw-only" onToggle={event => { if (!event.currentTarget.open) clearRead(); }}>
        <summary onClick={event => { if (event.currentTarget.parentElement?.hasAttribute('open')) clearRead(); }}>
          {t('Source rows ({count})', { count: formatNumber(rows.length) })}
        </summary>
        <div className="local-import-table table-scroll" tabIndex={0} role="region" aria-label={t('Scroll published folder source rows')}>
          <table aria-label={t('Published folder source rows')}>
            <thead><tr>{['Source', 'Record', 'Source lines', 'Bar start (UTC)', 'Trading day', 'Close', ...(native ? ['Action'] : [])].map(label =>
              <th scope="col" key={label}>{label === 'Close' ? t('Close', undefined, 'price') : t(label)}</th>,
            )}</tr></thead>
            <tbody>{rows.slice(first, last).map(({ bar, source }) => <tr key={`${source.relative_path}:${source.record_number}`}>
              <th scope="row">{source.relative_path}</th><td>{formatNumber(source.record_number, { useGrouping: false })}</td>
              <td>{sourceLines(source)}</td>
              <td>{formatSourceBarTime(bar.time_ms)}</td><td>{bar.trading_day}</td><td>{formatSourceBarValue(bar.close)}</td>
              {native && <td><button type="button" className="secondary-button" onClick={() => void readSourceRow(source)}>
                {t('Inspect record {record} from {path}', {
                  record: formatNumber(source.record_number, { useGrouping: false }), path: source.relative_path,
                })}
              </button></td>}
            </tr>)}</tbody>
          </table>
        </div>
        {rows.length > pageSize && <div className="local-import-table-nav">
          <p>{t('Source rows {first}–{last} of {count}', {
            first: formatNumber(first + 1, { useGrouping: false }), last: formatNumber(last, { useGrouping: false }),
            count: formatNumber(rows.length, { useGrouping: false }),
          })}</p>
          <button type="button" className="secondary-button" disabled={page === 0} onClick={() => changePage(page - 1)}>{t('Previous source rows')}</button>
          <button type="button" className="secondary-button" disabled={last >= rows.length} onClick={() => changePage(page + 1)}>{t('Next source rows')}</button>
        </div>}
        <OriginalSourceFields read={read} />
      </details>}
    </section>
  </>;
}
