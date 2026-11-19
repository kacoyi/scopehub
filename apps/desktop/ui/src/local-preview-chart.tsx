import { useMemo, useRef, useState } from 'react';
import type { Bar, LocalImportPreviewOutput } from '../../../../contracts/generated/protocol';
import type { ChartStyle, HistoryChartHandle } from './chart-adapter/history';
import { PriceChart } from './price-chart';

export function LocalPreviewChart({ result }: { result: LocalImportPreviewOutput }) {
  const [pickedSymbol, setPickedSymbol] = useState('');
  const [style, setStyle] = useState<ChartStyle>('candlestick');
  const handle = useRef<HistoryChartHandle | null>(null);
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
  const projection = useMemo(() => {
    if (!series || !bars || symbol === undefined || overlapping) return;
    return {
      instrument: {
        id: symbol, name: symbol, display_symbol: symbol,
        asset_class: series.instrument_kind === 'equity' ? 'stock' : 'future', price_tick: null,
      },
      history: { dataset: { instrument_id: symbol, timeframe: series.timeframe, timezone: series.timezone }, bars },
    };
  }, [bars, overlapping, series, symbol]);

  return <section className="local-chart-preview" aria-label="Local CSV chart preview">
    <div className="local-chart-tools">
      <h2>Chart preview</h2>
      {bySymbol.size > 1 && <label>Chart symbol <select value={symbol} onChange={event => setPickedSymbol(event.currentTarget.value)}>
        {Array.from(bySymbol.keys(), key => <option key={key}>{key}</option>)}
      </select></label>}
      {projection && <div role="group" aria-label="Preview chart style">
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
    </> : <p role="status" className="panel-message">{overlapping
      ? 'Review overlapping bars for this symbol before charting.'
      : 'Apply a closed-bar mapping to see a chart of the normalized rows.'}</p>}
  </section>;
}
