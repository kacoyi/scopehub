import { formatNumber, message, t } from './i18n';
import { assetLabel, instrumentName, price, priceChange, symbol, unitLabel } from './instrument-display';
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
  choose: (returnFocus: HTMLElement) => Promise<boolean>;
  openRevision: () => void;
  resume: ((returnFocus: HTMLElement) => void) | undefined;
  folderName: string | undefined;
  choosing: boolean;
  error: string | undefined;
};

export function LocalSourceButtons({ actions }: { actions: LocalSourceActions }) {
  return <div className="local-source-actions">
    <button type="button" className="secondary-button" disabled={actions.choosing} onClick={event => { void actions.choose(event.currentTarget); }}>
      {t('Choose local CSV folder')}
    </button>
    <button type="button" className="secondary-button" aria-haspopup="dialog" disabled={actions.choosing} onClick={actions.openRevision}>
      {t('Open published CSV')}
    </button>
    {actions.resume && <button type="button" className="secondary-button" disabled={actions.choosing} onClick={event => actions.resume?.(event.currentTarget)}>
      {t('Back to local CSV preview')}
    </button>}
    {actions.error && <p role="alert">{actions.error}</p>}
  </div>;
}

function marketValue(value: number) {
  return formatNumber(value, { useGrouping: false, maximumSignificantDigits: 21 });
}

function InstrumentDetails({ instrument, history }: { instrument: Instrument; history: ChartHistory }) {
  const last = history.bars.at(-1);
  if (!last) return null;
  const previous = history.bars.at(-2);
  const difference = previous ? last.close - previous.close : undefined;
  const fraction = last.high === last.low ? 0.5 : (last.close - last.low) / (last.high - last.low);
  return <section className="instrument-details" aria-label={t('Selected instrument details')}>
    <div className="detail-symbol"><span className={'asset-mark ' + instrument.asset_class} aria-hidden="true">{instrument.asset_class === 'future' ? t('F') : t('S')}</span>
      <div><strong>{symbol(instrument)}</strong><small>{instrumentName(instrument)} · {assetLabel(instrument)}</small></div></div>
    <div className="quote-label">{t('Snapshot close · {timeframe}', { timeframe: history.dataset.timeframe })}</div>
    <div className="quote">{price(last.close, instrument)} <span>{instrument.currency}</span></div>
    {previous && difference !== undefined && previous.close !== 0 && <div className="quote-difference">
      <b className={difference > 0 ? 'up' : difference < 0 ? 'down' : ''}>
        {difference < 0 ? '−' : '+'}{priceChange(last.close, previous.close, instrument)} ({difference < 0 ? '−' : '+'}{formatNumber(Math.abs(difference / previous.close), {
          style: 'percent', minimumFractionDigits: 2, maximumFractionDigits: 2, useGrouping: false,
        })})</b>
      <small>{t('vs previous bar')}</small>
    </div>}
    <div className="range-title">{t('Last bar range')}</div>
    <div className="bar-range" aria-hidden="true"><i style={{ left: (fraction * 100) + '%' }} /></div>
    <div className="range-values"><span>{price(last.low, instrument)}</span><span>{price(last.high, instrument)}</span></div>
    <dl className="facts"><dt>{t('Trading day')}</dt><dd>{last.trading_day}</dd><dt>{t('Volume')}</dt>
      <dd>{last.volume == null ? t('Unknown') : marketValue(last.volume)} {unitLabel(history.dataset.quantity_unit, 'lowercase')}</dd>
      <dt>{t('Bar status')}</dt><dd>{last.is_closed ? t('Closed') : t('Partial')}</dd></dl>
  </section>;
}

function Watchlist({ instruments, selected, history, choose, sample, choosing }: {
  instruments: Instrument[]; selected: Instrument | undefined; history: ChartHistory | undefined;
  choose: (item: Instrument) => void; sample: boolean; choosing: boolean;
}) {
  return <>
    <div className="watch-columns"><span>{t('Symbol')}</span><span>{t('Currency')}</span></div>
    <div className="watch-rows">{instruments.map(item => <button type="button" key={item.id}
      disabled={choosing} aria-pressed={item.id === selected?.id} className="watch-row" onClick={() => choose(item)}>
      <span className={'asset-mark ' + item.asset_class} aria-hidden="true">{item.asset_class === 'future' ? t('F') : t('S')}</span>
      <span className="watch-name"><strong>{symbol(item)}</strong><small>{instrumentName(item)}</small></span>
      <span className="watch-currency">{item.currency}</span>
    </button>)}</div>
    {!sample && !instruments.length && <p className="panel-message">{t('Symbols appear here when you open a data source.')}</p>}
    {selected && history && <InstrumentDetails instrument={selected} history={history} />}
  </>;
}

type BarSelection = {
  selectedBarTime?: number | undefined;
  selectBar?: ((bar: Bar) => void) | undefined;
  clearBar?: (() => void) | undefined;
  selectionBlocked?: boolean | undefined;
};

const barColumns = [
  message('Start (UTC)'), message('Trading day'), message('Open', undefined, 'price'),
  message('High', undefined, 'price'), message('Low', undefined, 'price'), message('Close', undefined, 'price'),
  message('Volume'), message('Status'),
];

function DataWindow({ history, instrument, selectedBarTime, selectBar, clearBar, selectionBlocked }: BarSelection & {
  history: ChartHistory | undefined;
  instrument: Instrument | undefined;
}) {
  if (!history) return <p className="panel-message">{t('Select a series to inspect its bars.')}</p>;
  const display = history.dataset.instrument_id === instrument?.id ? symbol(instrument) : history.dataset.instrument_id.replace(/^SIM\./, '');
  return <>
    <p className="panel-intro"><strong>{display} · {history.dataset.timeframe}</strong>
      <span>{t('{count} bars · Volume in {unit}', {
        count: formatNumber(history.bars.length, { useGrouping: false }), unit: unitLabel(history.dataset.quantity_unit, 'lowercase'),
      })}</span></p>
    {clearBar && <div className="layout-bar-actions">
      <span role="status">{t('Selected bar · {time}', { time: new Date(selectedBarTime ?? 0).toISOString() })}</span>
      <button type="button" className="secondary-button" disabled={selectionBlocked} onClick={clearBar}>{t('Clear chart selection')}</button>
    </div>}
    <div className="table-scroll" tabIndex={0} role="region" aria-label={t('Scroll historical bars')}>
      <table aria-label={t('Historical bars')}>
        <thead><tr>{barColumns.map(column => <th key={column.id} scope="col">{t(column)}</th>)}</tr></thead>
        <tbody>{history.bars.map(b => {
          const time = new Date(b.time_ms).toISOString();
          const active = b.time_ms === selectedBarTime;
          return <tr key={b.time_ms} className={active ? 'layout-selected-bar' : undefined}>
            <td>{selectBar ? <button type="button" aria-label={t('Select bar at {time}', { time })} aria-pressed={active}
              disabled={selectionBlocked} onClick={() => selectBar(b)}>{time}</button> : time}</td>
            <td>{b.trading_day}</td><td>{marketValue(b.open)}</td><td>{marketValue(b.high)}</td>
            <td>{marketValue(b.low)}</td><td>{marketValue(b.close)}</td><td>{b.volume == null ? t('Unknown') : marketValue(b.volume)}</td>
            <td>{b.is_closed ? t('Closed') : t('Partial')}</td>
          </tr>;
        })}</tbody>
      </table>
    </div>
  </>;
}

type SampleFilters = {
  revision: SampleRevision; timeframe: string; date: string; blocked: boolean;
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
    {sample && <section><h3>{t('Historical sample')}</h3><p>{t('Generated prices and volume from fixed sessions in January 2024.')}</p>
      <div className="sample-filters">
        {selected?.id === sampleRevisionInstrument && <>
          <label htmlFor="sample-revision">{t('Sample revision')}</label>
          <select id="sample-revision" value={filters.revision} disabled={filters.blocked} onChange={event => {
            const value = event.currentTarget.value;
            if (isSampleRevision(value)) filters.setRevision(value);
          }}>
            {Object.entries(sampleRevisions).filter(([value]) => filters.timeframe === '1m' || !isClosedBarGapRevision(value))
              .map(([value, { label }]) => <option key={value} value={value}>{t(label)}</option>)}
          </select>
        </>}
        <label htmlFor="sample-date">{t('Sample date (UTC)')}</label>
        <input id="sample-date" type="date" max="9999-12-31" value={filters.date} disabled={filters.blocked}
          aria-describedby="sample-date-help" onChange={event => {
          const value = event.currentTarget.value;
          if (value === '' || sampleDateStart(value) !== undefined) filters.setDate(value);
        }} />
        <small id="sample-date-help">{t('Leave blank for the full sample window.')}</small>
      </div>
      <MarketQualityFacts history={history} failureKind={failureKind} workerRecovery={workerRecovery} />
      <button type="button" className="secondary-button" onClick={leave}>{t('Leave sample')}</button>
    </section>}
    {replayGap && <ReplayGapFacts gap={replayGap} />}
    {localActions && <section><h3>{t('Local CSV')}</h3><LocalSourceButtons actions={localActions} /></section>}
    {openPublished && <section><h3>{t('Local CSV')}</h3><button type="button" className="secondary-button"
      aria-haspopup="dialog" onClick={openPublished}>{t('Open published CSV')}</button></section>}
    {openSample && <section><h3>{t('Historical sample')}</h3><button type="button" className="secondary-button"
      onClick={openSample}>{t('Open offline sample')}</button></section>}
    <section><h3>{t('Your connections')}</h3>
      <dl className="facts"><dt>{t('Market data service')}</dt><dd>{t('Not configured')}</dd><dt>{t('Information service')}</dt><dd>{t('Not configured')}</dd></dl>
      <button type="button" className="secondary-button" aria-haspopup="dialog" onClick={openModelSettings}>{t('AI settings')}</button>
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
  const label = t(panelLabels[panel]);
  return <aside id={layoutPanel ? `workspace-panel-${layoutPanel}` : 'workspace-panel'}
    className={`workspace-panel panel-${panel}${layoutSource ? ' layout-source-panel' : ''}`}
    data-layout-panel={layoutPanel} aria-label={label}>
    <div className="panel-heading"><h2>{label}</h2>
      <button type="button" className="icon-button" aria-label={t('Close panel')} title={t('Close panel')} onClick={close}><WorkspaceIcon name="close" /></button></div>
    {panel === 'watchlist' && <Watchlist instruments={instruments} selected={selected} history={history}
      choose={choose} sample={sample} choosing={choosingInstrument} />}
    {panel === 'data' && <DataWindow history={history} instrument={selected} {...selection} />}
    {panel === 'sources' && <DataSources history={history} sample={sample} selected={selected}
      failureKind={failureKind} workerRecovery={workerRecovery} replayGap={replayGap} filters={filters} leave={leave}
      localActions={localActions} openPublished={openPublished} openSample={openSample} openModelSettings={openModelSettings} />}
  </aside>;
}
