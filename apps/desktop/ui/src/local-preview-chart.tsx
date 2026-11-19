import { useMemo, useRef, useState, type ReactNode } from 'react';
import type { Bar, LocalImportPreviewOutput, PreviewSeries, ReadLocalFolderRevisionOutput } from '../../../../contracts/generated/protocol';
import type { ChartStyle, HistoryChartHandle } from './chart-adapter/history';
import { FrozenEvidenceControls, type FrozenChartResult } from './frozen-evidence';
import { PriceChart } from './price-chart';
import type { PublishedRevisionAccess } from './published-revision-access';
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
  const [page, setPage] = useState(0);
  const { revision, bars: events } = result;
  const bars = useMemo(() => events.map(event => event.bar), [events]);
  const rows = useMemo(() => events.flatMap(({ bar, locators }) => locators.map(locator => ({ bar, locator }))), [events]);
  const pageSize = 20;
  const first = page * pageSize;
  const last = Math.min(first + pageSize, rows.length);
  return <>
    <LocalBarChart title="Frozen chart" label="Frozen local CSV chart" styleLabel="Frozen chart style"
      series={revision.series} symbol={revision.symbol} bars={bars} />
    {result.snapshot && workspace && <FrozenEvidenceControls snapshot={result.snapshot} bars={bars} workspace={workspace} access={access} />}
    <section className="local-import-card" aria-label="Published source provenance">
      <h2>Published source provenance</h2>
      <div className="local-import-hash"><strong>Dataset revision</strong><code>{revision.revision_id}</code></div>
      <div className="local-import-hash"><strong>Source SHA-256</strong><code>{revision.source_sha256}</code></div>
      <div className="local-import-hash"><strong>Reviewed mapping {revision.review_revision} SHA-256</strong><code>{revision.mapping_sha256}</code></div>
      <details className="local-import-raw-only">
        <summary>Source rows ({revision.source_record_count.toLocaleString()})</summary>
        <div className="local-import-table table-scroll" tabIndex={0} role="region" aria-label="Scroll published source rows">
          <table aria-label="Published source rows">
            <thead><tr>{['Record', 'Source lines', 'Bar start (UTC)', 'Trading day', 'Close'].map(label =>
              <th scope="col" key={label}>{label}</th>,
            )}</tr></thead>
            <tbody>{rows.slice(first, last).map(({ bar, locator }) => <tr key={locator.record_number}>
              <th scope="row">{locator.record_number}</th>
              <td>{locator.line_start === locator.line_end ? locator.line_start : `${locator.line_start}–${locator.line_end}`}</td>
              <td>{new Date(bar.time_ms).toISOString()}</td><td>{bar.trading_day}</td><td>{bar.close}</td>
            </tr>)}</tbody>
          </table>
        </div>
        {rows.length > pageSize && <div className="local-import-table-nav">
          <p>Source rows {first + 1}–{last} of {rows.length}</p>
          <button type="button" className="secondary-button" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous source rows</button>
          <button type="button" className="secondary-button" disabled={last >= rows.length} onClick={() => setPage(page + 1)}>Next source rows</button>
        </div>}
      </details>
    </section>
  </>;
}

export function FrozenFolderChart({ result }: { result: ReadLocalFolderRevisionOutput }) {
  const [page, setPage] = useState(0);
  const { revision, files, bars: events } = result;
  const bars = useMemo(() => events.map(event => event.bar), [events]);
  const rows = useMemo(() => events.flatMap(({ bar, sources }) => sources.map(source => ({ bar, source }))), [events]);
  const pageSize = 20;
  const first = page * pageSize;
  const last = Math.min(first + pageSize, rows.length);
  return <>
    <LocalBarChart title="Frozen folder chart" label="Frozen local folder chart" styleLabel="Frozen folder chart style"
      series={revision.series} symbol={revision.symbol} bars={bars.length ? bars : undefined}
      emptyMessage="This reviewed folder revision withdrew all accepted bars." />
    <section className="local-import-card" aria-label="Published folder source provenance">
      <h2>Published folder source provenance</h2>
      <div className="local-import-hash"><strong>Folder revision</strong><code>{revision.revision_id}</code></div>
      <div className="local-import-hash"><strong>Prior revision</strong><code>{revision.parent_revision_id ?? revision.base_revision_id}</code></div>
      <details className="local-import-raw-only">
        <summary>Source files ({files.length.toLocaleString()})</summary>
        {files.length ? <ul>{files.map(file => <li key={file.relative_path}>
          <strong>{file.relative_path}</strong> · {file.record_count.toLocaleString()} records · SHA-256 <code>{file.source_sha256}</code>
        </li>)}</ul> : <p>No sources remain in this reviewed revision.</p>}
      </details>
      {rows.length > 0 && <details className="local-import-raw-only">
        <summary>Source rows ({rows.length.toLocaleString()})</summary>
        <div className="local-import-table table-scroll" tabIndex={0} role="region" aria-label="Scroll published folder source rows">
          <table aria-label="Published folder source rows">
            <thead><tr>{['Source', 'Record', 'Source lines', 'Bar start (UTC)', 'Trading day', 'Close'].map(label =>
              <th scope="col" key={label}>{label}</th>,
            )}</tr></thead>
            <tbody>{rows.slice(first, last).map(({ bar, source }) => <tr key={`${source.relative_path}:${source.record_number}`}>
              <th scope="row">{source.relative_path}</th><td>{source.record_number}</td>
              <td>{source.line_start === source.line_end ? source.line_start : `${source.line_start}–${source.line_end}`}</td>
              <td>{new Date(bar.time_ms).toISOString()}</td><td>{bar.trading_day}</td><td>{bar.close}</td>
            </tr>)}</tbody>
          </table>
        </div>
        {rows.length > pageSize && <div className="local-import-table-nav">
          <p>Source rows {first + 1}–{last} of {rows.length}</p>
          <button type="button" className="secondary-button" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous source rows</button>
          <button type="button" className="secondary-button" disabled={last >= rows.length} onClick={() => setPage(page + 1)}>Next source rows</button>
        </div>}
      </details>}
    </section>
  </>;
}
