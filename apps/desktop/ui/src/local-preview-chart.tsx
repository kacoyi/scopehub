import { useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import type { Bar, LocalImportPreviewOutput, LocalImportRowLocator, PreviewSeries, ReadLocalImportSourceRowOutput } from '../../../../contracts/generated/protocol';
import type { ChartStyle, HistoryChartHandle } from './chart-adapter/history';
import { FrozenEvidenceControls, type FrozenChartResult } from './frozen-evidence';
import { hasLocalCsvPicker, readLocalImportSourceRow } from './local-import-transport';
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
};

function LocalBarChart({ title, label, styleLabel, series, symbol, bars, emptyMessage, controls }: ChartProps) {
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
        <button type="button" aria-pressed={style === 'candlestick'} onClick={() => setStyle('candlestick')}>Candles</button>
        <button type="button" aria-pressed={style === 'line'} onClick={() => setStyle('line')}>Close-price line</button>
      </div>}
    </div>
    {projection && series ? <>
      <div className="local-chart-canvas">
        <PriceChart instrument={projection.instrument} history={projection.history} handle={handle} style={style}
          cursor="crosshair" replayBar={undefined} repairedBars={undefined} />
      </div>
      <footer className="chart-footer">
        <span>{series.venue} · {series.timezone}</span><span>Volume in {series.quantity_unit}</span><span>{series.price_adjustment}</span>
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
  return <LocalBarChart title="Chart preview" label="Local CSV chart preview" styleLabel="Preview chart style"
    series={series ?? undefined} symbol={symbol} bars={overlapping ? undefined : bars}
    emptyMessage={overlapping ? 'Review overlapping bars for this symbol before charting.'
      : 'Apply a closed-bar mapping to see a chart of the normalized rows.'}
    controls={bySymbol.size > 1 && <label>Chart symbol <select value={symbol} onChange={event => setPickedSymbol(event.currentTarget.value)}>
      {Array.from(bySymbol.keys(), key => <option key={key}>{key}</option>)}
    </select></label>} />;
}

export function FrozenLocalChart({ result, workspace, access }: {
  result: FrozenChartResult; workspace: LocalWorkspace | undefined; access: PublishedRevisionAccess;
}) {
  const { revision, bars: events } = result;
  const bars = useMemo(() => events.map(event => event.bar), [events]);
  return <>
    <LocalBarChart title="Frozen chart" label="Frozen local CSV chart" styleLabel="Frozen chart style"
      series={revision.series} symbol={revision.symbol} bars={bars} />
    {result.snapshot && workspace && <FrozenEvidenceControls snapshot={result.snapshot} bars={bars} workspace={workspace} access={access} />}
    <section className="local-import-card" aria-label="Published source provenance">
      <h2>Published source provenance</h2>
      <div className="local-import-hash"><strong>Dataset revision</strong><code>{revision.revision_id}</code></div>
      <div className="local-import-hash"><strong>Source SHA-256</strong><code>{revision.source_sha256}</code></div>
      <div className="local-import-hash"><strong>Reviewed mapping {revision.review_revision} SHA-256</strong><code>{revision.mapping_sha256}</code></div>
      <FrozenSourceRows result={result} workspace={workspace} access={access} />
    </section>
  </>;
}

type SourceRowRead = { loading: number } | { error: string } | { row: ReadLocalImportSourceRowOutput };

function sourceLines(locator: LocalImportRowLocator) {
  return locator.line_start === locator.line_end ? String(locator.line_start) : `${locator.line_start}–${locator.line_end}`;
}

function FrozenSourceRows({ result, workspace, access }: {
  result: FrozenChartResult; workspace: LocalWorkspace | undefined; access: PublishedRevisionAccess;
}) {
  const [page, setPage] = useState(0);
  const [record, setRecord] = useState('');
  const [read, setRead] = useState<SourceRowRead>();
  const pending = useRef<string | undefined>(undefined);
  const { revision, bars: events } = result;
  const revisionId = revision.revision_id;
  const native = hasLocalCsvPicker();
  const rows = useMemo(() => events.flatMap(({ bar, locators }) => locators.map(locator => ({ bar, locator }))), [events]);
  const pageSize = 20;
  const first = page * pageSize;
  const last = Math.min(first + pageSize, rows.length);
  const recordNumber = Number(record);

  useLayoutEffect(() => () => { pending.current = undefined; }, []);

  function clearRead() {
    pending.current = undefined;
    setRead(undefined);
  }

  function changePage(next: number) {
    clearRead();
    setPage(next);
  }

  async function inspect(locator: LocalImportRowLocator) {
    if (!access.canDisplay(revisionId)) return;
    const requestId = crypto.randomUUID();
    pending.current = requestId;
    setRead({ loading: locator.record_number });
    try {
      const row = await readLocalImportSourceRow({ version: 1, revision_id: revisionId, locator }, requestId);
      if (pending.current !== requestId || !access.canDisplay(revisionId)) return;
      if (row.revision_id !== revisionId || row.source_sha256 !== revision.source_sha256
        || row.locator.record_number !== locator.record_number || row.locator.line_start !== locator.line_start
        || row.locator.line_end !== locator.line_end || row.header.length !== row.raw_fields.length) {
        setRead({ error: 'The original CSV row could not be verified.' });
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
      const code = error && typeof error === 'object' && 'code' in error ? error.code : undefined;
      setRead({ error: code === 'RESOURCE_LIMIT' ? 'This original CSV row exceeds the inspection size limit.'
        : code === 'BUSY' ? 'The CSV reader is busy. Try inspecting the record again.' : 'Unable to inspect the original CSV row.' });
    } finally {
      if (pending.current === requestId) pending.current = undefined;
    }
  }

  const raw = read && 'row' in read && access.canDisplay(revisionId) ? read.row : undefined;
  return <details className="local-import-raw-only" onToggle={event => { if (!event.currentTarget.open) clearRead(); }}>
    <summary onClick={event => { if (event.currentTarget.parentElement?.hasAttribute('open')) clearRead(); }}>
      Source rows ({revision.source_record_count.toLocaleString()})
    </summary>
    {native && <form className="local-import-table-nav local-import-source-lookup" onSubmit={event => {
      event.preventDefault();
      const index = rows.findIndex(({ locator }) => locator.record_number === recordNumber);
      const target = rows[index];
      if (target) {
        setPage(Math.floor(index / pageSize));
        void inspect(target.locator);
      }
    }}>
      <label>Source record <input type="number" min={1} max={revision.source_record_count} step={1} value={record}
        onChange={event => setRecord(event.currentTarget.value)} /></label>
      <button type="submit" className="secondary-button" disabled={!Number.isInteger(recordNumber) || recordNumber < 1
        || recordNumber > revision.source_record_count}>Inspect source record</button>
    </form>}
    <div className="local-import-table table-scroll" tabIndex={0} role="region" aria-label="Scroll published source rows">
      <table aria-label="Published source rows">
        <thead><tr>{['Record', 'Source lines', 'Bar start (UTC)', 'Trading day', 'Close', ...(native ? ['Action'] : [])].map(label =>
          <th scope="col" key={label}>{label}</th>,
        )}</tr></thead>
        <tbody>{rows.slice(first, last).map(({ bar, locator }) => <tr key={locator.record_number}>
          <th scope="row">{locator.record_number}</th>
          <td>{sourceLines(locator)}</td>
          <td>{new Date(bar.time_ms).toISOString()}</td><td>{bar.trading_day}</td><td>{bar.close}</td>
          {native && <td><button type="button" className="secondary-button" onClick={() => void inspect(locator)}>
            Inspect record {locator.record_number}
          </button></td>}
        </tr>)}</tbody>
      </table>
    </div>
    {rows.length > pageSize && <div className="local-import-table-nav">
      <p>Source rows {first + 1}–{last} of {rows.length}</p>
      <button type="button" className="secondary-button" disabled={page === 0} onClick={() => changePage(page - 1)}>Previous source rows</button>
      <button type="button" className="secondary-button" disabled={last >= rows.length} onClick={() => changePage(page + 1)}>Next source rows</button>
    </div>}
    {read && 'loading' in read && <p role="status">Reading source record {read.loading}…</p>}
    {read && 'error' in read && <p role="alert" className="local-import-error">{read.error}</p>}
    {raw && <section aria-label="Original CSV fields">
      <h3>Original CSV fields</h3>
      <p>Record {raw.locator.record_number} · Source lines {sourceLines(raw.locator)}</p>
      <div className="local-import-table local-import-source-fields table-scroll" tabIndex={0} role="region" aria-label="Scroll original CSV fields">
        <table aria-label="Original CSV fields">
          <thead><tr><th scope="col">Source column</th><th scope="col">Value</th></tr></thead>
          <tbody>{raw.header.map((column, index) => <tr key={column}>
            <th scope="row">{column}</th><td>{raw.raw_fields[index]}</td>
          </tr>)}</tbody>
        </table>
      </div>
    </section>}
  </details>;
}
