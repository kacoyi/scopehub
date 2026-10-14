import { CandlestickSeries, ColorType, HistogramSeries, LineSeries, createChart, type Time, type UTCTimestamp } from 'lightweight-charts';
import type { Bar, Instrument, MarketGetBarsOutput } from '../market';

export type ChartStyle = 'candlestick' | 'line';

// Chart-library units and lifecycle stay inside this replaceable adapter.
export function mountHistoryChart(
  element: HTMLElement, history: MarketGetBarsOutput, instrument: Instrument,
  style: ChartStyle, select: (bar: Bar) => void,
): () => void {
  const lookup = new Map(history.bars.map(bar => [bar.time_ms / 1000, bar]));
  const localTime = new Intl.DateTimeFormat('en-GB', {
    timeZone: history.dataset.timezone, month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  });
  const formatTime = (time: Time) => {
    const timestamp = Number(time);
    return history.dataset.timeframe === '1d'
      ? (lookup.get(timestamp)?.trading_day ?? '')
      : localTime.format(new Date(timestamp * 1000));
  };
  const chart = createChart(element, {
    autoSize: true,
    layout: { background: { type: ColorType.Solid, color: '#101a23' }, textColor: '#a9b9c7', attributionLogo: true },
    grid: { vertLines: { color: '#1b2a36' }, horzLines: { color: '#1b2a36' } },
    rightPriceScale: { borderColor: '#2a3b49' },
    timeScale: { timeVisible: true, secondsVisible: false, borderColor: '#2a3b49', tickMarkFormatter: formatTime },
    localization: { locale: 'en-GB', timeFormatter: formatTime },
  });
  try {
    const tick = instrument.price_tick ?? '0.01';
    const priceFormat = { type: 'price' as const, precision: tick.split('.')[1]?.length ?? 0, minMove: Number(tick) || 0.01 };
    const timestamp = (bar: Bar) => (bar.time_ms / 1000) as UTCTimestamp;
    if (style === 'candlestick') {
      const series = chart.addSeries(CandlestickSeries, {
        priceFormat, upColor: '#43d4ac', downColor: '#f17d85', borderVisible: false,
        wickUpColor: '#43d4ac', wickDownColor: '#f17d85',
      });
      series.setData(history.bars.map(bar => ({ time: timestamp(bar), open: bar.open, high: bar.high, low: bar.low, close: bar.close })));
    } else {
      const series = chart.addSeries(LineSeries, { priceFormat, color: '#73b8f6', lineWidth: 2 });
      series.setData(history.bars.map(bar => ({ time: timestamp(bar), value: bar.close })));
    }
    const volumes = chart.addSeries(HistogramSeries, { priceFormat: { type: 'volume' }, priceLineVisible: false, lastValueVisible: false }, 1);
    volumes.setData(history.bars.map(bar => bar.volume === null
      ? { time: timestamp(bar) }
      : { time: timestamp(bar), value: bar.volume, color: bar.close >= bar.open ? '#287b69' : '#8d4e59' }));
    chart.panes()[1]?.setHeight(100);
    chart.timeScale().fitContent();
    chart.subscribeCrosshairMove(event => {
      const bar = lookup.get(Number(event.time));
      const selected = bar ?? history.bars.at(-1);
      if (selected) select(selected);
    });
    return () => chart.remove();
  } catch (error) { chart.remove(); throw error; }
}
