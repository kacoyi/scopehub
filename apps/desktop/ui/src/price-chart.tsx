import { useEffect, useRef, useState, type RefObject } from 'react';
import { defaultChartAppearance, mountHistoryChart, type ChartAppearance, type ChartCursorMode, type ChartStyle, type HistoryChartData, type HistoryChartHandle } from './chart-adapter/history';
import type { Bar, Instrument } from './market';

type ChartInstrument = Pick<Instrument, 'id' | 'name' | 'display_symbol' | 'asset_class' | 'price_tick'> & Partial<Pick<Instrument, 'currency'>>;

type Props = {
  instrument: ChartInstrument; history: HistoryChartData; style: ChartStyle; cursor: ChartCursorMode;
  handle: RefObject<HistoryChartHandle | null>;
  replayBar: Bar | undefined;
  repairedBars: readonly Bar[] | undefined;
  appearance?: ChartAppearance;
  selectedBarTime?: number | undefined;
  activateBar?: ((bar: Bar) => void) | undefined;
};

export function PriceChart({ instrument, history, style, cursor, handle, replayBar, repairedBars, appearance = defaultChartAppearance, selectedBarTime, activateBar }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const firstStyle = useRef(style);
  const onActivate = useRef(activateBar);
  const lastProjection = useRef<readonly Bar[]>(history.bars);
  const bars = repairedBars ?? history.bars;
  const [selected, setSelected] = useState<Bar | undefined>(history.bars.at(-1));
  const [failed, setFailed] = useState(false);
  useEffect(() => { onActivate.current = activateBar; }, [activateBar]);
  useEffect(() => {
    if (!container.current) return;
    setSelected(history.bars.at(-1));
    setFailed(false);
    try {
      const chart = mountHistoryChart(container.current, history, instrument, firstStyle.current, setSelected, bar => onActivate.current?.(bar));
      handle.current = chart;
      lastProjection.current = history.bars;
      return () => { handle.current = null; chart.dispose(); };
    } catch { setFailed(true); }
  }, [history, instrument, handle]);
  useEffect(() => { handle.current?.setStyle(style); }, [style, history, handle]);
  useEffect(() => { handle.current?.setCursor(cursor); }, [cursor, history, handle]);
  useEffect(() => { handle.current?.setAppearance(appearance); }, [appearance, history, handle]);
  useEffect(() => { handle.current?.setSelectedBar(selectedBarTime); }, [selectedBarTime, history, handle]);
  useEffect(() => {
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
  return <div className="history" data-instrument={history.dataset.instrument_id} data-timeframe={history.dataset.timeframe} data-bars={bars.length}>
    <div className="chart-legend">
      <div className="legend-title"><span className={'asset-mark ' + instrument.asset_class} aria-hidden="true">
        {instrument.asset_class === 'future' ? 'F' : 'S'}</span>
        <h1>{instrument.name}</h1><span className="legend-period">· {history.dataset.timeframe}</span>
        {instrument.name !== instrument.display_symbol && <span className="legend-ticker">{instrument.display_symbol}</span>}
        {instrument.currency && <span className="currency-tag">{instrument.currency}</span>}
      </div>
      {selected && <div className="bar-detail" aria-label="Selected bar">
        <span>O <b>{selected.open}</b></span><span>H <b>{selected.high}</b></span>
        <span>L <b>{selected.low}</b></span><span>C <b>{selected.close}</b></span>
        <span>Volume <b>{selected.volume ?? 'Unknown'}</b></span>
        <span className="legend-day">Trading day <b>{selected.trading_day}</b></span>
        <span className={selected.is_closed ? 'bar-phase' : 'bar-phase partial'}>{selected.is_closed ? 'Closed bar' : 'Partial bar'}</span>
      </div>}
    </div>
    {failed && <p className="chart-notice" role="alert">The chart could not be displayed. Review the bars in the data table.</p>}
    <div className="chart" ref={container} role="img" aria-label={`${style === 'line' ? 'Close-price line' : 'Candlestick'} and volume chart for ${
      instrument.id.startsWith('SIM.') ? instrument.id : instrument.display_symbol
    }`} />
  </div>;
}
