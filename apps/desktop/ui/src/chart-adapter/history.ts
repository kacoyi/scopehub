import {
  CandlestickSeries, ColorType, CrosshairMode, HistogramSeries, LineSeries, createChart,
  type ISeriesApi, type Time, type UTCTimestamp,
} from 'lightweight-charts';
import type { Bar, Instrument, MarketGetBarsOutput } from '../../../../../contracts/generated/protocol';

export type ChartStyle = 'candlestick' | 'line';
export type ChartCursorMode = 'crosshair' | 'pan';

export interface HistoryChartHandle {
  dispose(): void;
  fit(): void;
  zoomIn(): void;
  zoomOut(): void;
  setCursor(mode: ChartCursorMode): void;
  setStyle(style: ChartStyle): void;
  updateLast(bar: Bar): void;
}

// Chart-library units and lifecycle stay inside this replaceable adapter.
export function mountHistoryChart(
  element: HTMLElement, history: MarketGetBarsOutput, instrument: Instrument,
  style: ChartStyle, select: (bar: Bar) => void,
): HistoryChartHandle {
  const bars = [...history.bars];
  const lookup = new Map(bars.map(bar => [bar.time_ms / 1000, bar]));
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
    layout: {
      background: { type: ColorType.Solid, color: '#131722' }, textColor: '#b2b5be', attributionLogo: true,
      panes: { separatorColor: '#242b38', separatorHoverColor: '#414b60' },
    },
    grid: { vertLines: { color: '#242b38' }, horzLines: { color: '#242b38' } },
    rightPriceScale: { borderColor: '#242b38' },
    timeScale: {
      timeVisible: true, secondsVisible: false, borderColor: '#242b38', tickMarkFormatter: formatTime,
      fixLeftEdge: true, fixRightEdge: true,
      tickMarkMaxCharacterLength: history.dataset.timeframe === '1d' ? 10 : 13,
    },
    localization: { locale: 'en-GB', timeFormatter: formatTime },
  });
  try {
    const tick = instrument.price_tick ?? '0.01';
    const priceFormat = { type: 'price' as const, precision: tick.split('.')[1]?.length ?? 0, minMove: Number(tick) || 0.01 };
    const timestamp = (bar: Bar) => (bar.time_ms / 1000) as UTCTimestamp;
    const candle = (bar: Bar) => ({ time: timestamp(bar), open: bar.open, high: bar.high, low: bar.low, close: bar.close });
    const volume = (bar: Bar) => bar.volume === null
      ? { time: timestamp(bar) }
      : { time: timestamp(bar), value: bar.volume, color: bar.close >= bar.open ? 'rgba(67,212,172,0.42)' : 'rgba(241,125,133,0.42)' };
    function addPriceSeries(nextStyle: ChartStyle) {
      let series: ISeriesApi<'Candlestick'> | ISeriesApi<'Line'> | undefined;
      try {
        if (nextStyle === 'candlestick') {
          series = chart.addSeries(CandlestickSeries, {
            priceFormat, upColor: '#43d4ac', downColor: '#f17d85', borderVisible: false,
            wickUpColor: '#43d4ac', wickDownColor: '#f17d85',
          });
          series.setData(bars.map(candle));
        } else {
          series = chart.addSeries(LineSeries, { priceFormat, color: '#73b8f6', lineWidth: 2 });
          series.setData(bars.map(bar => ({ time: timestamp(bar), value: bar.close })));
        }
        return series;
      } catch (error) {
        if (series) chart.removeSeries(series);
        throw error;
      }
    }
    let priceSeries = addPriceSeries(style);
    const volumes = chart.addSeries(HistogramSeries, { priceFormat: { type: 'volume' }, priceLineVisible: false, lastValueVisible: false }, 1);
    volumes.setData(bars.map(volume));
    chart.panes()[1]?.setHeight(100);
    const timeScale = chart.timeScale();
    timeScale.fitContent();

    function zoom(factor: number) {
      const range = timeScale.getVisibleLogicalRange();
      if (!range || history.bars.length < 2) return;
      const count = Math.min(history.bars.length, Math.max(2, (range.to - range.from + 1) * factor));
      const center = (range.from + range.to) / 2;
      const from = Math.max(0, Math.min(center - (count - 1) / 2, history.bars.length - count));
      timeScale.setVisibleLogicalRange({ from, to: from + count - 1 });
    }

    let cursor: ChartCursorMode = 'crosshair';
    let inspectedTime: number | undefined;
    const originalCursor = element.style.cursor;
    chart.subscribeCrosshairMove(event => {
      if (cursor === 'pan') return;
      const bar = lookup.get(Number(event.time));
      inspectedTime = bar?.time_ms;
      const selected = bar ?? bars.at(-1);
      if (selected) select(selected);
    });
    element.style.cursor = 'crosshair';
    return {
      dispose() {
        element.style.cursor = originalCursor;
        chart.remove();
      },
      fit() {
        chart.priceScale('right', 0).setAutoScale(true);
        volumes.priceScale().setAutoScale(true);
        timeScale.fitContent();
      },
      zoomIn: () => zoom(0.8),
      zoomOut: () => zoom(1.25),
      setCursor(mode) {
        if (mode === cursor) return;
        cursor = mode;
        const pan = mode === 'pan';
        element.style.cursor = pan ? 'grab' : 'crosshair';
        chart.applyOptions({ crosshair: { mode: pan ? CrosshairMode.Hidden : CrosshairMode.Magnet } });
        if (pan) {
          chart.clearCrosshairPosition();
          inspectedTime = undefined;
          const last = bars.at(-1);
          if (last) select(last);
        }
      },
      setStyle(nextStyle) {
        if (nextStyle === style) return;
        const range = timeScale.getVisibleLogicalRange();
        const next = addPriceSeries(nextStyle);
        chart.removeSeries(priceSeries);
        priceSeries = next;
        style = nextStyle;
        if (range) timeScale.setVisibleLogicalRange(range);
      },
      updateLast(bar) {
        const previous = bars.at(-1);
        if (bar === previous || bar.time_ms !== previous?.time_ms) return;
        bars[bars.length - 1] = bar;
        lookup.set(timestamp(bar), bar);
        const point = { ...candle(bar), value: bar.close };
        priceSeries.update(point);
        volumes.update(volume(bar));
        if (inspectedTime === undefined || inspectedTime === bar.time_ms) select(bar);
      },
    };
  } catch (error) { chart.remove(); throw error; }
}
