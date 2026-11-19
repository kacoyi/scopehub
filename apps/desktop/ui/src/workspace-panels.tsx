import { assetLabel, instrumentName, price, symbol, unitLabel } from './instrument-display';
import {
  isClosedBarGapRevision, isSampleRevision, sampleDateStart, sampleRevisionInstrument, sampleRevisions,
  type Bar, type ChartHistory, type Instrument, type MarketFailure, type SampleRevision,
} from './market';
import { MarketQualityFacts, ReplayGapFacts } from './market-quality';
import type { ReplayRecoveryStatus } from './market-replay';
import type { ReplayGap } from './use-sample-replay';
import { WorkspaceIcon } from './workspace-icons';

export type Panel = 'watchlist' | 'data' | 'sources';
export const panelLabels: Record<Panel, string> = { watchlist: 'Watchlist', data: 'Data window', sources: 'Data sources' };

export type LocalSourceActions = {
  choose: () => void;
  openRevision: () => void;
  resume: (() => void) | undefined;
  choosing: boolean;
  error: string | undefined;
};

export function LocalSourceButtons({ actions }: { actions: LocalSourceActions }) {
  return <div className="local-source-actions">
    <button type="button" className="secondary-button" disabled={actions.choosing} onClick={actions.choose}>
      Choose local CSV folder
    </button>
    <button type="button" className="secondary-button" aria-haspopup="dialog" disabled={actions.choosing} onClick={actions.openRevision}>
      Open published CSV
    </button>
    {actions.resume && <button type="button" className="secondary-button" disabled={actions.choosing} onClick={actions.resume}>
      Back to local CSV preview
    </button>}
    {actions.error && <p role="alert">{actions.error}</p>}
  </div>;
}

function InstrumentDetails({ instrument, history }: { instrument: Instrument; history: ChartHistory }) {
  const last = history.bars.at(-1);
  if (!last) return null;
  const previous = history.bars.at(-2);
  const difference = previous ? last.close - previous.close : undefined;
  const fraction = last.high === last.low ? 0.5 : (last.close - last.low) / (last.high - last.low);
  return <section className="instrument-details" aria-label="Selected instrument details">
    <div className="detail-symbol"><span className={'asset-mark ' + instrument.asset_class} aria-hidden="true">{instrument.asset_class === 'future' ? 'F' : 'S'}</span>
      <div><strong>{symbol(instrument)}</strong><small>{instrumentName(instrument)} · {assetLabel(instrument)}</small></div></div>
    <div className="quote-label">Snapshot close · {history.dataset.timeframe}</div>
    <div className="quote">{price(last.close, instrument)} <span>{instrument.currency}</span></div>
    {previous && difference !== undefined && previous.close !== 0 && <div className="quote-difference">
      <b className={difference > 0 ? 'up' : difference < 0 ? 'down' : ''}>
        {difference < 0 ? '−' : '+'}{price(Math.abs(difference), instrument)} ({difference < 0 ? '−' : '+'}{Math.abs(difference / previous.close * 100).toFixed(2)}%)</b>
      <small>vs previous bar</small>
    </div>}
    <div className="range-title">Last bar range</div>
    <div className="bar-range" aria-hidden="true"><i style={{ left: (fraction * 100) + '%' }} /></div>
    <div className="range-values"><span>{price(last.low, instrument)}</span><span>{price(last.high, instrument)}</span></div>
    <dl className="facts"><dt>Trading day</dt><dd>{last.trading_day}</dd><dt>Volume</dt><dd>{last.volume ?? 'Unknown'} {unitLabel(history.dataset.quantity_unit).toLowerCase()}</dd>
      <dt>Bar status</dt><dd>{last.is_closed ? 'Closed' : 'Partial'}</dd></dl>
  </section>;
}

function Watchlist({ instruments, selected, history, choose, sample, choosing }: {
  instruments: Instrument[]; selected: Instrument | undefined; history: ChartHistory | undefined;
  choose: (item: Instrument) => void; sample: boolean; choosing: boolean;
}) {
  return <>
    <div className="watch-columns"><span>Symbol</span><span>Currency</span></div>
    <div className="watch-rows">{instruments.map(item => <button type="button" key={item.id}
      disabled={choosing} aria-pressed={item.id === selected?.id} className="watch-row" onClick={() => choose(item)}>
      <span className={'asset-mark ' + item.asset_class} aria-hidden="true">{item.asset_class === 'future' ? 'F' : 'S'}</span>
      <span className="watch-name"><strong>{symbol(item)}</strong><small>{instrumentName(item)}</small></span>
      <span className="watch-currency">{item.currency}</span>
    </button>)}</div>
    {!sample && !instruments.length && <p className="panel-message">Symbols appear here when you open a data source.</p>}
    {selected && history && <InstrumentDetails instrument={selected} history={history} />}
  </>;
}

type BarSelection = {
  selectedBarTime?: number | undefined;
  selectBar?: ((bar: Bar) => void) | undefined;
  clearBar?: (() => void) | undefined;
  selectionBlocked?: boolean | undefined;
};

function DataWindow({ history, instrument, selectedBarTime, selectBar, clearBar, selectionBlocked }: BarSelection & {
  history: ChartHistory | undefined;
  instrument: Instrument | undefined;
}) {
  if (!history) return <p className="panel-message">Select a series to inspect its bars.</p>;
  const display = history.dataset.instrument_id === instrument?.id ? symbol(instrument) : history.dataset.instrument_id.replace(/^SIM\./, '');
  return <>
    <p className="panel-intro"><strong>{display} · {history.dataset.timeframe}</strong>
      <span>{history.bars.length} bars · Volume in {history.dataset.quantity_unit}</span></p>
    {clearBar && <div className="layout-bar-actions">
      <span role="status">Selected bar · {new Date(selectedBarTime ?? 0).toISOString()}</span>
      <button type="button" className="secondary-button" disabled={selectionBlocked} onClick={clearBar}>Clear chart selection</button>
    </div>}
    <div className="table-scroll" tabIndex={0} role="region" aria-label="Scroll historical bars">
      <table aria-label="Historical bars">
        <thead><tr>{['Start (UTC)', 'Trading day', 'Open', 'High', 'Low', 'Close', 'Volume', 'Status'].map(name => <th key={name} scope="col">{name}</th>)}</tr></thead>
        <tbody>{history.bars.map(b => {
          const time = new Date(b.time_ms).toISOString();
          const active = b.time_ms === selectedBarTime;
          return <tr key={b.time_ms} className={active ? 'layout-selected-bar' : undefined}>
            <td>{selectBar ? <button type="button" aria-label={`Select bar at ${time}`} aria-pressed={active}
              disabled={selectionBlocked} onClick={() => selectBar(b)}>{time}</button> : time}</td>
            <td>{b.trading_day}</td><td>{b.open}</td><td>{b.high}</td>
            <td>{b.low}</td><td>{b.close}</td><td>{b.volume ?? 'Unknown'}</td><td>{b.is_closed ? 'Closed' : 'Partial'}</td>
          </tr>;
        })}</tbody>
      </table>
    </div>
  </>;
}

type SampleFilters = {
  revision: SampleRevision; timeframe: string; date: string;
  setRevision: (value: SampleRevision) => void; setDate: (value: string) => void;
};

type MarketStatus = {
  failureKind: MarketFailure['kind'] | undefined;
  workerRecovery: ReplayRecoveryStatus | undefined;
  replayGap: ReplayGap | undefined;
};

function DataSources({ sample, selected, history, failureKind, workerRecovery, replayGap, filters, leave, localActions, openPublished, openSample, openModelSettings }: MarketStatus & {
  sample: boolean; selected: Instrument | undefined; history: ChartHistory | undefined;
  filters: SampleFilters; leave: () => void;
  localActions: LocalSourceActions | undefined;
  openPublished?: (() => void) | undefined;
  openSample?: (() => void) | undefined;
  openModelSettings: () => void;
}) {
  return <div className="source-details">
    {sample && <section><h3>Historical sample</h3><p>Generated prices and volume from fixed sessions in January 2024.</p>
      <div className="sample-filters">
        {selected?.id === sampleRevisionInstrument && <>
          <label htmlFor="sample-revision">Sample revision</label>
          <select id="sample-revision" value={filters.revision} onChange={event => {
            const value = event.currentTarget.value;
            if (isSampleRevision(value)) filters.setRevision(value);
          }}>
            {Object.entries(sampleRevisions).filter(([value]) => filters.timeframe === '1m' || !isClosedBarGapRevision(value))
              .map(([value, { label }]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </>}
        <label htmlFor="sample-date">Sample date (UTC)</label>
        <input id="sample-date" type="date" max="9999-12-31" value={filters.date} aria-describedby="sample-date-help" onChange={event => {
          const value = event.currentTarget.value;
          if (value === '' || sampleDateStart(value) !== undefined) filters.setDate(value);
        }} />
        <small id="sample-date-help">Leave blank for the full sample window.</small>
      </div>
      <MarketQualityFacts history={history} failureKind={failureKind} workerRecovery={workerRecovery} />
      <button type="button" className="secondary-button" onClick={leave}>Leave sample</button>
    </section>}
    {replayGap && <ReplayGapFacts gap={replayGap} />}
    {localActions && <section><h3>Local CSV</h3><LocalSourceButtons actions={localActions} /></section>}
    {openPublished && <section><h3>Local CSV</h3><button type="button" className="secondary-button"
      aria-haspopup="dialog" onClick={openPublished}>Open published CSV</button></section>}
    {openSample && <section><h3>Historical sample</h3><button type="button" className="secondary-button"
      onClick={openSample}>Open offline sample</button></section>}
    <section><h3>Your connections</h3>
      <dl className="facts"><dt>Market data service</dt><dd>Not configured</dd><dt>Information service</dt><dd>Not configured</dd></dl>
      <button type="button" className="secondary-button" aria-haspopup="dialog" onClick={openModelSettings}>AI settings</button>
    </section>
  </div>;
}

type Props = MarketStatus & BarSelection & {
  panel: Panel; sample: boolean; instruments: Instrument[]; selected: Instrument | undefined; history: ChartHistory | undefined;
  filters: SampleFilters;
  choose: (item: Instrument) => void; close: () => void; leave: () => void;
  choosingInstrument: boolean;
  localActions: LocalSourceActions | undefined;
  layoutPanel?: 'watchlist' | 'inspection';
  layoutSource?: boolean;
  openPublished?: (() => void) | undefined;
  openSample?: (() => void) | undefined;
  openModelSettings: () => void;
};
export function WorkspacePanel({ panel, sample, instruments, selected, history, failureKind, workerRecovery, replayGap, filters,
  choose, close, leave, choosingInstrument, localActions, layoutPanel, layoutSource, openPublished, openSample, openModelSettings, ...selection }: Props) {
  return <aside id={layoutPanel ? `workspace-panel-${layoutPanel}` : 'workspace-panel'}
    className={`workspace-panel panel-${panel}${layoutSource ? ' layout-source-panel' : ''}`}
    data-layout-panel={layoutPanel} aria-label={panelLabels[panel]}>
    <div className="panel-heading"><h2>{panelLabels[panel]}</h2>
      <button type="button" className="icon-button" aria-label="Close panel" title="Close panel" onClick={close}><WorkspaceIcon name="close" /></button></div>
    {panel === 'watchlist' && <Watchlist instruments={instruments} selected={selected} history={history}
      choose={choose} sample={sample} choosing={choosingInstrument} />}
    {panel === 'data' && <DataWindow history={history} instrument={selected} {...selection} />}
    {panel === 'sources' && <DataSources history={history} sample={sample} selected={selected}
      failureKind={failureKind} workerRecovery={workerRecovery} replayGap={replayGap} filters={filters} leave={leave}
      localActions={localActions} openPublished={openPublished} openSample={openSample} openModelSettings={openModelSettings} />}
  </aside>;
}
