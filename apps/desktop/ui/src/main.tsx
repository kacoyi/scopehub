import { StrictMode, useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { mountHistoryChart, type ChartStyle } from './chart-adapter/history';
import { demoProvider, errorText, historyReader, sampleRange, type Bar, type Instrument, type MarketGetBarsOutput } from './market';
import './style.css';

type Load<T> = { state: 'loading' } | { state: 'error'; message: string } | { state: 'ready'; value: T };

function HistoryChart({ history, instrument, style }: { history: MarketGetBarsOutput; instrument: Instrument; style: ChartStyle }) {
  const container = useRef<HTMLDivElement>(null);
  const [selected, setSelected] = useState<Bar | undefined>(history.bars.at(-1));
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!container.current) return;
    setSelected(history.bars.at(-1));
    setFailed(false);
    try { return mountHistoryChart(container.current, history, instrument, style, setSelected); }
    catch { setFailed(true); }
  }, [history, instrument, style]);
  return <>
    {failed && <p role="alert">The chart could not be displayed. The data remains available in the table.</p>}
    <div className="chart" ref={container} role="img" aria-label={`${style === 'line' ? 'Close-price line' : 'Candlestick'} and volume chart for ${instrument.id}`} />
    {selected && <div className="bar-detail" aria-label="Selected bar">
      <span>Trading day <b>{selected.trading_day}</b></span>
      <span>O <b>{selected.open}</b></span><span>H <b>{selected.high}</b></span>
      <span>L <b>{selected.low}</b></span><span>C <b>{selected.close}</b></span>
      <span>Volume <b>{selected.volume ?? 'Unknown'}</b></span>
      <span className={selected.is_closed ? '' : 'accent'}>{selected.is_closed ? 'Closed bar' : 'Partial bar'}</span>
    </div>}
    <details className="data-table"><summary>Inspect {history.bars.length} bars</summary>
      <div className="table-scroll"><table><caption>Bar starts in UTC · trading day is supplied separately · volume in {history.dataset.quantity_unit}</caption>
        <thead><tr>{['Start (UTC)', 'Trading day', 'Open', 'High', 'Low', 'Close', 'Volume', 'Status'].map(name => <th key={name} scope="col">{name}</th>)}</tr></thead>
        <tbody>{history.bars.map(bar => <tr key={bar.time_ms}>
          <td>{new Date(bar.time_ms).toISOString()}</td><td>{bar.trading_day}</td><td>{bar.open}</td><td>{bar.high}</td><td>{bar.low}</td><td>{bar.close}</td>
          <td>{bar.volume ?? 'Unknown'}</td><td>{bar.is_closed ? 'Closed' : 'Partial'}</td>
        </tr>)}</tbody>
      </table></div>
    </details>
  </>;
}

function History({ instrument, timeframe, style }: { instrument: Instrument; timeframe: string; style: ChartStyle }) {
  const [result, setResult] = useState<Load<MarketGetBarsOutput>>({ state: 'loading' });
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setResult({ state: 'loading' });
    historyReader.read('market.getBars', { instrument_id: instrument.id, provider_id: demoProvider, timeframe, ...sampleRange }, controller.signal)
      .then(value => {
        if (controller.signal.aborted) return;
        // Prevent a bridge or presentation defect from labeling a different series.
        if (value.dataset.instrument_id !== instrument.id || value.dataset.timeframe !== timeframe
          || value.dataset.provider_id !== demoProvider || value.dataset.mode !== 'simulated'
          || value.dataset.timezone !== instrument.timezone || value.dataset.calendar_id !== instrument.calendar_id
          || value.dataset.quantity_unit !== instrument.quantity_unit) throw { code: 'OUTPUT_INVALID' };
        setResult({ state: 'ready', value });
      }).catch(error => { if (!controller.signal.aborted) setResult({ state: 'error', message: errorText(error) }); });
    return () => controller.abort();
  }, [instrument, timeframe, retry]);
  if (result.state === 'loading') return <div className="chart-state" role="status">Loading synthetic history…</div>;
  if (result.state === 'error') return <div className="chart-state"><p role="alert">{result.message}</p><button onClick={() => setRetry(value => value + 1)}>Retry history</button></div>;
  const history = result.value;
  return <div className="history" data-instrument={history.dataset.instrument_id} data-timeframe={history.dataset.timeframe} data-bars={history.bars.length}>
    <div className="source-strip"><span>SIMULATED · Historical sample</span><span>{history.dataset.timezone} · {history.dataset.quality} at sample time</span>
      {history.bars.at(-1)?.is_closed === false && <span>Final bar is partial</span>}</div>
    {history.bars.length ? <HistoryChart history={history} instrument={instrument} style={style} /> : <div className="chart-state" role="status">No bars in this sample range.</div>}
    <p className="provenance">Source: {history.dataset.provider_id} · Revision: {history.dataset.revision}<br />
      Fixed sample sessions in January 2024. No live feed. Missing volume stays unknown; final partial bars stay partial.</p>
  </div>;
}

function OfflinePreview({ leave }: { leave: () => void }) {
  const [catalog, setCatalog] = useState<Load<Instrument[]>>({ state: 'loading' });
  const [retry, setRetry] = useState(0);
  const [selectedId, setSelectedId] = useState('');
  const [query, setQuery] = useState('');
  const [timeframe, setTimeframe] = useState('1m');
  const [style, setStyle] = useState<ChartStyle>('candlestick');
  useEffect(() => {
    const controller = new AbortController();
    setCatalog({ state: 'loading' });
    historyReader.read('instrument.search', { query: '', limit: 50 }, controller.signal)
      .then(value => { if (!controller.signal.aborted) { setCatalog({ state: 'ready', value: value.instruments }); setSelectedId(value.instruments[0]?.id ?? ''); } })
      .catch(error => { if (!controller.signal.aborted) setCatalog({ state: 'error', message: errorText(error) }); });
    return () => controller.abort();
  }, [retry]);
  const selected = catalog.state === 'ready' ? catalog.value.find(item => item.id === selectedId) : undefined;
  const filtered = catalog.state === 'ready' ? catalog.value.filter(item => (item.id + ' ' + item.name).toLowerCase().includes(query.trim().toLowerCase())) : [];
  return <div className="preview">
    <nav className="instruments" aria-label="Demo instruments">
      <div className="eyebrow">OFFLINE DEMO</div><h2>Synthetic instruments</h2>
      <label className="search-label">Find instrument<input value={query} onChange={event => setQuery(event.target.value)} placeholder="Symbol or name" /></label>
      {catalog.state === 'loading' && <p role="status">Loading instruments…</p>}
      {catalog.state === 'error' && <><p role="alert">{catalog.message}</p><button onClick={() => setRetry(value => value + 1)}>Retry demo</button></>}
      {catalog.state === 'ready' && !filtered.length && <p role="status">No matching instruments.</p>}
      {filtered.map(item => <button className="instrument" key={item.id} aria-pressed={selectedId === item.id} onClick={() => setSelectedId(item.id)}>
        <strong>{item.id.replace('SIM.', '')}</strong><span>{item.asset_class === 'future' ? 'Futures' : 'Stocks'} · {item.currency} · Synthetic</span>
      </button>)}
      <p className="sample-note">Generated examples for exploring the chart. These are not market quotes or exchange calendars.</p>
      <button className="quiet" onClick={leave}>Leave demo</button>
    </nav>
    <section className="chart-workspace" aria-label="Chart preview">
      {selected ? <>
        <div className="chart-heading"><div><div className="eyebrow">HISTORICAL PREVIEW</div><h1>{selected.id}</h1></div><span className="badge">SIMULATED</span></div>
        <div className="toolbar">
          <div role="group" aria-label="Timeframe">{['1m', '5m', '1d'].map(value => <button key={value} aria-pressed={timeframe === value} onClick={() => setTimeframe(value)}>{value}</button>)}</div>
          <div role="group" aria-label="Chart style"><button aria-pressed={style === 'candlestick'} onClick={() => setStyle('candlestick')}>Candles</button><button aria-pressed={style === 'line'} onClick={() => setStyle('line')}>Close-price line</button></div>
          <span className="range">2–4 Jan 2024 · Sample range</span>
        </div>
        <History key={`${selected.id}:${timeframe}`} instrument={selected} timeframe={timeframe} style={style} />
      </> : <div className="chart-state">Choose an instrument to inspect its history.</div>}
    </section>
  </div>;
}

function App() {
  const [demo, setDemo] = useState(false);
  return <main>
    <header><strong><span className="brand-mark" aria-hidden="true">S</span> ScopeHub</strong><span role="status">{demo ? 'Offline demo · Simulated' : 'No services configured'}</span></header>
    {demo ? <OfflinePreview leave={() => setDemo(false)} /> : <section className="welcome" aria-label="Start workspace">
      <div className="eyebrow">YOUR FINANCIAL WORKSPACE</div><h1>Start with your sources.</h1>
      <p>Connect your own market data, AI and information services as integrations become available. Service subscriptions are separate from this software.</p>
      <div className="service-grid"><article><h2>Market data</h2><p>Local files and provider APIs</p><span>Not configured · Integration pending</span></article><article><h2>AI collaboration</h2><p>Your model and API connection</p><span>Not configured · Integration pending</span></article><article><h2>Information</h2><p>Your research and information sources</p><span>Not configured · Integration pending</span></article></div>
      <div className="demo-entry"><div><h2>Explore an offline example</h2><p>Three synthetic instruments, historical candles and volume. No service account required.</p></div><button className="primary" onClick={() => setDemo(true)}>Explore offline demo</button></div>
    </section>}
    <footer><span>ScopeHub · Development preview</span><a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">Charts by TradingView Lightweight Charts™</a></footer>
  </main>;
}

const root = document.getElementById('root');
if (!root) throw new Error('Missing application root');
createRoot(root).render(<StrictMode><App /></StrictMode>);
