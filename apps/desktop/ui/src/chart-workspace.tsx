import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import type { ChartCursorMode, ChartStyle, HistoryChartHandle } from './chart-adapter/history';
import { symbol } from './instrument-display';
import type { Instrument } from './market';
import { PriceChart } from './price-chart';
import { narrowScreen, readSampleView, saveSampleView } from './sample-view';
import { SymbolSearch } from './symbol-search';
import { useHistory, useSampleCatalog } from './use-market-data';
import { WorkspaceIcon, type WorkspaceIconName } from './workspace-icons';
import { WorkspacePanel, panelLabels, type Panel } from './workspace-panels';

const panelIcons: Record<Panel, WorkspaceIconName> = { watchlist: 'watchlist', data: 'table', sources: 'database' };
const panelOrder: Panel[] = ['watchlist', 'data', 'sources'];
const noInstruments: Instrument[] = [];

function Tool({ name, icon, onClick, active, disabled }: {
  name: string; icon: WorkspaceIconName; onClick: () => void; active?: boolean; disabled?: boolean;
}) {
  return <button type="button" className="icon-button" title={name} aria-label={name} aria-pressed={active}
    disabled={disabled} onClick={onClick}><WorkspaceIcon name={icon} /></button>;
}

function PlotMessage({ title, children, error = false }: { title: string; children?: ReactNode; error?: boolean }) {
  return <div className="plot-empty"><div className="empty-content">
    <span className="empty-chart-icon" aria-hidden="true"><WorkspaceIcon name="chart" /></span>
    <h1 role={error ? 'alert' : 'status'}>{title}</h1>{children}
  </div></div>;
}

export function ChartWorkspace({ sample, leaveSample }: { sample: boolean; leaveSample: () => void }) {
  const [saved] = useState(() => sample ? readSampleView() : undefined);
  const [chosenId, setChosenId] = useState(saved?.symbol ?? '');
  const [timeframe, setTimeframe] = useState(saved?.timeframe ?? '1m');
  const [style, setStyle] = useState<ChartStyle>(saved?.style ?? 'candlestick');
  const [cursor, setCursor] = useState<ChartCursorMode>(saved?.cursor ?? 'crosshair');
  const [panel, setPanel] = useState<Panel | null>(() => {
    const narrow = window.matchMedia(narrowScreen).matches;
    return saved?.narrow === narrow && saved.panel !== undefined ? saved.panel : narrow ? null : 'watchlist';
  });
  const [focusChart, setFocusChart] = useState(saved?.chartOnly ?? false);
  useEffect(() => {
    if (sample) saveSampleView({
      symbol: chosenId, timeframe, style, cursor, panel, chartOnly: focusChart,
      narrow: window.matchMedia(narrowScreen).matches,
    });
  }, [sample, chosenId, timeframe, style, cursor, panel, focusChart]);
  const [searchQuery, setSearchQuery] = useState<string | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const chart = useRef<HistoryChartHandle | null>(null);
  const dock = useRef<HTMLElement>(null);
  const { catalog, retryCatalog } = useSampleCatalog(sample);
  const instruments = catalog.state === 'ready' ? catalog.value : noInstruments;
  const selected = instruments.find(item => item.id === chosenId) ?? instruments[0];
  const { result, retryHistory } = useHistory(selected, timeframe);
  const history = result.state === 'ready' ? result.value : undefined;
  const visiblePanel = focusChart ? null : panel;
  const canInspect = Boolean(history?.bars.length);

  const searchWasOpen = useRef(false);
  useEffect(() => {
    if (searchQuery === null && searchWasOpen.current) trigger.current?.focus();
    searchWasOpen.current = searchQuery !== null;
  }, [searchQuery]);
  const closeSearch = () => setSearchQuery(null);
  const closePanel = useCallback(() => {
    dock.current?.querySelector<HTMLButtonElement>('[aria-expanded="true"]')?.focus();
    setPanel(null);
  }, []);
  const choose = (item: Instrument) => {
    setChosenId(item.id);
    if (searchQuery !== null) closeSearch();
    if (window.matchMedia(narrowScreen).matches) closePanel();
  };
  useEffect(() => {
    const media = window.matchMedia(narrowScreen);
    const changed = () => { if (media.matches) setPanel(null); };
    media.addEventListener('change', changed);
    return () => media.removeEventListener('change', changed);
  }, []);
  useEffect(() => {
    const keyboard = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.isComposing || searchQuery !== null) return;
      if (event.key === 'Escape') {
        if (focusChart) setFocusChart(false);
        else if (visiblePanel) closePanel();
        return;
      }
      if (event.target instanceof HTMLElement && (event.target.isContentEditable || event.target.closest('input, textarea, select'))) return;
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault(); setSearchQuery('');
      } else if (!event.ctrlKey && !event.altKey && !event.metaKey && (event.key === '/' || /^[a-zA-Z0-9]$/.test(event.key))) {
        event.preventDefault(); setSearchQuery(event.key === '/' ? '' : event.key);
      }
    };
    window.addEventListener('keydown', keyboard);
    return () => window.removeEventListener('keydown', keyboard);
  }, [searchQuery, focusChart, visiblePanel, closePanel]);
  const openSample = () => { window.location.hash = 'demo'; };

  let plot: ReactNode;
  if (!sample) {
    plot = <PlotMessage title="Open a chart"><p>Explore three instruments with offline sample data.</p>
      <button type="button" className="primary" onClick={openSample}>Open offline sample <WorkspaceIcon name="arrow" /></button></PlotMessage>;
  } else if (catalog.state === 'error') {
    plot = <PlotMessage title={catalog.message} error>{catalog.retryable && <button type="button" className="primary" onClick={retryCatalog}>Retry demo</button>}</PlotMessage>;
  } else if (catalog.state === 'ready' && !selected) {
    plot = <PlotMessage title="No instruments in this sample." />;
  } else if (result.state === 'error') {
    plot = <PlotMessage title={result.message} error>{result.retryable && <button type="button" className="primary" onClick={retryHistory}>Retry history</button>}</PlotMessage>;
  } else if (!selected || !history) {
    plot = <PlotMessage title="Loading synthetic history…" />;
  } else if (!history.bars.length) {
    plot = <div className="history" data-instrument={selected.id} data-timeframe={timeframe} data-bars={0}>
      <PlotMessage title="No bars in this sample range." /></div>;
  } else {
    plot = <PriceChart key={selected.id + ':' + timeframe} instrument={selected} history={history} style={style} cursor={cursor} handle={chart} />;
  }

  return <main className={'workstation' + (focusChart ? ' chart-only' : '')}>
    <header className="topbar">
      <div className="brand" aria-label="ScopeHub"><span className="brand-mark" aria-hidden="true">S</span><strong>ScopeHub</strong></div>
      <div className="toolbar-group symbol-group"><button type="button" ref={trigger} className="symbol-trigger" aria-label="Search symbols"
        aria-haspopup="dialog" title="Search symbols (/)" onClick={() => setSearchQuery('')}>
        <WorkspaceIcon name="search" /><strong>{selected ? symbol(selected) : 'Symbol search'}</strong><WorkspaceIcon name="chevron" /></button></div>
      <div className="toolbar-group periods" role="group" aria-label="Timeframe">{['1m', '5m', '1d'].map(value =>
        <button type="button" key={value} className="period" aria-pressed={timeframe === value} onClick={() => setTimeframe(value)}>{value}</button>)}</div>
      <div className="toolbar-group chart-styles" role="group" aria-label="Chart style">
        <Tool name="Candles" icon="candles" active={style === 'candlestick'} onClick={() => setStyle('candlestick')} />
        <Tool name="Close-price line" icon="line" active={style === 'line'} onClick={() => setStyle('line')} />
      </div>
      <span className={'connection' + (sample ? ' sample' : '')} role="status"><i aria-hidden="true" />{sample ? <><span>Offline sample</span><b>Simulated</b></> : 'No data source'}</span>
      <Tool name={focusChart ? 'Exit chart-only view' : 'Chart-only view'} icon={focusChart ? 'collapse' : 'expand'} active={focusChart} onClick={() => setFocusChart(!focusChart)} />
    </header>
    <div className={'workbody' + (visiblePanel ? ' with-' + visiblePanel : '')}>
      {!focusChart && <nav className="chart-tools" aria-label="Chart tools">
        <Tool name="Crosshair" icon="crosshair" active={cursor === 'crosshair'} onClick={() => setCursor('crosshair')} />
        <Tool name="Pan" icon="pan" active={cursor === 'pan'} onClick={() => setCursor('pan')} />
        <span className="tool-divider" />
        <Tool name="Zoom in" icon="zoomIn" disabled={!canInspect} onClick={() => chart.current?.zoomIn()} />
        <Tool name="Zoom out" icon="zoomOut" disabled={!canInspect} onClick={() => chart.current?.zoomOut()} />
        <Tool name="Fit chart" icon="fit" disabled={!canInspect} onClick={() => chart.current?.fit()} />
      </nav>}
      <section className="plot-workspace" aria-label="Price chart">
        {plot}
        <footer className="chart-footer"><span>{sample ? 'Jan 2024' : 'ScopeHub'}</span>
          {selected && <span>{selected.timezone}</span>}
          <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">Charts by TradingView Lightweight Charts™</a></footer>
      </section>
      {visiblePanel && <>
        <button type="button" className="panel-scrim" aria-label="Dismiss side panel" onClick={closePanel} />
        <WorkspacePanel panel={visiblePanel} sample={sample} selected={selected} instruments={instruments} history={history}
          choose={choose} close={closePanel} leave={leaveSample} />
      </>}
      {!focusChart && <nav className="side-tools" aria-label="Workspace panels" ref={dock}>{panelOrder.map(key =>
        <button type="button" className="icon-button" key={key} title={panelLabels[key]} aria-label={panelLabels[key]}
          aria-expanded={visiblePanel === key} aria-controls={visiblePanel === key ? 'workspace-panel' : undefined}
          onClick={() => setPanel(visiblePanel === key ? null : key)}><WorkspaceIcon name={panelIcons[key]} /></button>)}</nav>}
    </div>
    {searchQuery !== null && <SymbolSearch instruments={instruments} initialQuery={searchQuery} sample={sample}
      onChoose={choose} onClose={closeSearch} openSample={openSample} />}
  </main>;
}
