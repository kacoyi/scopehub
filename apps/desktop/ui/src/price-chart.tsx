import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from 'react';
import {
  defaultChartAppearance, mountHistoryChart, type ChartAppearance, type ChartCursorMode,
  type ChartStyle, type HistoryChartData, type HistoryChartHandle, type HistoryChartLocalization, type IndicatorChartLayer,
} from './chart-adapter/history';
import { formatNumber, getLocale, t, useLocale } from './i18n';
import type { Bar, Instrument } from './market';

type ChartInstrument = Pick<Instrument, 'id' | 'name' | 'display_symbol' | 'asset_class' | 'price_tick'> & Partial<Pick<Instrument, 'currency'>>;

type Props = {
  instrument: ChartInstrument; history: HistoryChartData; style: ChartStyle; cursor: ChartCursorMode;
  handle: RefObject<HistoryChartHandle | null>;
  replayBar: Bar | undefined;
  repairedBars: readonly Bar[] | undefined;
  indicatorPreview?: IndicatorChartLayer | undefined;
  acceptedIndicators?: readonly IndicatorChartLayer[] | undefined;
  appearance?: ChartAppearance;
  selectedBarTime?: number | undefined;
  activateBar?: ((bar: Bar) => void) | undefined;
  evidenceRow?: Pick<Bar, 'time_ms' | 'close'> | undefined;
  returnToAnswer?: () => void;
};

function chartLocalization(locale: string): HistoryChartLocalization {
  return { locale, regionLabels: { preview: t('Preview'), accepted: t('Accepted') } };
}

const barNumberOptions: Intl.NumberFormatOptions = { useGrouping: false, maximumSignificantDigits: 21 };

export function PriceChart({ instrument, history, style, cursor, handle, replayBar, repairedBars, appearance = defaultChartAppearance, selectedBarTime, activateBar, indicatorPreview, acceptedIndicators, evidenceRow, returnToAnswer }: Props) {
  const locale = useLocale();
  const container = useRef<HTMLDivElement>(null);
  const firstStyle = useRef(style);
  const answerButton = useRef<HTMLButtonElement>(null);
  const onActivate = useRef(activateBar);
  const lastProjection = useRef<readonly Bar[]>(history.bars);
  const bars = repairedBars ?? history.bars;
  const priceTick = instrument.price_tick;
  const evidenceTime = evidenceRow ? new Date(evidenceRow.time_ms).toISOString() : undefined;
  const ticker = instrument.id.startsWith('SIM.') ? instrument.id : instrument.display_symbol;
  const [selected, setSelected] = useState<Bar | undefined>(history.bars.at(-1));
  const [failed, setFailed] = useState(false);
  const [indicatorFailed, setIndicatorFailed] = useState(false);
  useEffect(() => { onActivate.current = activateBar; }, [activateBar]);
  useEffect(() => { if (evidenceRow) answerButton.current?.focus(); }, [evidenceRow]);
  useLayoutEffect(() => {
    if (!container.current) return;
    setSelected(history.bars.at(-1));
    setFailed(false);
    try {
      const chart = mountHistoryChart(container.current, history, { price_tick: priceTick }, firstStyle.current,
        setSelected, bar => onActivate.current?.(bar), chartLocalization(getLocale()));
      handle.current = chart;
      lastProjection.current = history.bars;
      return () => { handle.current = null; chart.dispose(); };
    } catch { setFailed(true); }
  }, [history, priceTick, handle]);
  useLayoutEffect(() => { handle.current?.setLocale(chartLocalization(locale)); }, [locale, handle]);
  useEffect(() => { handle.current?.setStyle(style); }, [style, history, handle]);
  useEffect(() => { handle.current?.setCursor(cursor); }, [cursor, history, handle]);
  useEffect(() => { handle.current?.setAppearance(appearance); }, [appearance, history, handle]);
  useEffect(() => { handle.current?.setSelectedBar(selectedBarTime); }, [selectedBarTime, history, handle]);
  useLayoutEffect(() => {
    const chart = handle.current;
    if (!chart) return;
    try {
      if (lastProjection.current !== bars) {
        chart.replaceBars(bars);
        lastProjection.current = bars;
      }
      const last = replayBar ?? bars.at(-1);
      if (last) chart.updateLast(last);
    } catch { setFailed(true); }
  }, [replayBar, bars, history, handle]);
  useLayoutEffect(() => {
    try {
      handle.current?.setIndicatorLayers(indicatorPreview, acceptedIndicators ?? []);
      setIndicatorFailed(false);
    } catch { setIndicatorFailed(true); }
  }, [indicatorPreview, acceptedIndicators, history, handle]);
  return <div className="history" data-instrument={history.dataset.instrument_id} data-timeframe={history.dataset.timeframe} data-bars={bars.length}>
    <div className="chart-legend">
      <div className="legend-title"><span className={'asset-mark ' + instrument.asset_class} aria-hidden="true">
        {instrument.asset_class === 'future' ? t('F') : t('S')}</span>
        <h1>{instrument.name}</h1><span className="legend-period">· {history.dataset.timeframe}</span>
        {instrument.name !== instrument.display_symbol && <span className="legend-ticker">{instrument.display_symbol}</span>}
        {instrument.currency && <span className="currency-tag">{instrument.currency}</span>}
      </div>
      {selected && <div className="bar-detail" aria-label={t('Selected bar')}>
        <span>{t('O')} <b>{formatNumber(selected.open, barNumberOptions)}</b></span>
        <span>{t('H')} <b>{formatNumber(selected.high, barNumberOptions)}</b></span>
        <span>{t('L')} <b>{formatNumber(selected.low, barNumberOptions)}</b></span>
        <span>{t('C')} <b>{formatNumber(selected.close, barNumberOptions)}</b></span>
        <span>{t('Volume')} <b>{selected.volume === null ? t('Unknown') : formatNumber(selected.volume, barNumberOptions)}</b></span>
        <span className="legend-day">{t('Trading day')} <b>{selected.trading_day}</b></span>
        <span className={selected.is_closed ? 'bar-phase' : 'bar-phase partial'}>{selected.is_closed ? t('Closed bar') : t('Partial bar')}</span>
      </div>}
      {evidenceRow && <div className="chart-evidence-anchor" role="group" aria-label={t('Cited bar')}>
        <span><strong>{t('Cited bar')}</strong> <time dateTime={evidenceTime}>{evidenceTime}</time></span>
        <span>{t('Close', undefined, 'price')} <b>{formatNumber(evidenceRow.close, barNumberOptions)}</b></span>
        <button ref={answerButton} type="button" className="secondary-button" onClick={() => returnToAnswer?.()}>{t('Back to answer')}</button>
      </div>}
    </div>
    {indicatorFailed && <p className="chart-notice" role="alert">{t('The indicator layer could not be displayed.')}</p>}
    {failed && <p className="chart-notice" role="alert">{t('The chart could not be displayed. Review the bars in the data table.')}</p>}
    <div className="chart" ref={container} role="img" aria-label={style === 'line'
      ? t('Close-price line and volume chart for {instrument}', { instrument: ticker })
      : t('Candlestick and volume chart for {instrument}', { instrument: ticker })} />
  </div>;
}
