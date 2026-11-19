import { useEffect, useRef, useState, type RefObject } from 'react';
import { mountHistoryChart, type ChartCursorMode, type ChartStyle, type HistoryChartHandle } from './chart-adapter/history';
import { instrumentName, symbol } from './instrument-display';
import type { Bar, Instrument, MarketGetBarsOutput } from './market';

type Props = {
  instrument: Instrument; history: MarketGetBarsOutput; style: ChartStyle; cursor: ChartCursorMode;
  handle: RefObject<HistoryChartHandle | null>;
  replayBar: Bar | undefined;
};

export function PriceChart({ instrument, history, style, cursor, handle, replayBar }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const firstStyle = useRef(style);
  const [selected, setSelected] = useState<Bar | undefined>(history.bars.at(-1));
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!container.current) return;
    setSelected(history.bars.at(-1));
    setFailed(false);
    try {
      const chart = mountHistoryChart(container.current, history, instrument, firstStyle.current, setSelected);
      handle.current = chart;
      return () => { handle.current = null; chart.dispose(); };
    } catch { setFailed(true); }
  }, [history, instrument, handle]);
  useEffect(() => { handle.current?.setStyle(style); }, [style, history, handle]);
  useEffect(() => { handle.current?.setCursor(cursor); }, [cursor, history, handle]);
  useEffect(() => {
    const last = replayBar ?? history.bars.at(-1);
    if (!last) return;
    try { handle.current?.updateLast(last); } catch { setFailed(true); }
  }, [replayBar, history, handle]);
  return <div className="history" data-instrument={history.dataset.instrument_id} data-timeframe={history.dataset.timeframe} data-bars={history.bars.length}>
    <div className="chart-legend">
      <div className="legend-title"><span className={'asset-mark ' + instrument.asset_class} aria-hidden="true">
        {instrument.asset_class === 'future' ? 'F' : 'S'}</span>
        <h1>{instrumentName(instrument)}</h1><span className="legend-period">· {history.dataset.timeframe}</span>
        <span className="legend-ticker">{symbol(instrument)}</span><span className="currency-tag">{instrument.currency}</span>
      </div>
      {selected && <div className="bar-detail" aria-label="Selected bar">
        <span>O <b>{selected.open}</b></span><span>H <b>{selected.high}</b></span>
        <span>L <b>{selected.low}</b></span><span>C <b>{selected.close}</b></span>
        <span>Volume <b>{selected.volume ?? 'Unknown'}</b></span>
        <span className="legend-day">Trading day <b>{selected.trading_day}</b></span>
        <span className={selected.is_closed ? 'bar-phase' : 'bar-phase partial'}>{selected.is_closed ? 'Closed bar' : 'Partial bar'}</span>
      </div>}
    </div>
    {failed && <p className="chart-notice" role="alert">The chart could not be displayed. The data remains available in the data window.</p>}
    <div className="chart" ref={container} role="img" aria-label={`${style === 'line' ? 'Close-price line' : 'Candlestick'} and volume chart for ${instrument.id}`} />
  </div>;
}
