import {
  CandlestickSeries, ColorType, CrosshairMode, HistogramSeries, LineSeries, createChart, createSeriesMarkers,
  type ISeriesApi, type LineWidth, type SeriesMarker, type Time, type UTCTimestamp,
} from 'lightweight-charts';
import type { Bar, DatasetDescriptor, IndicatorResult, Instrument, VisibleRange } from '../../../../../contracts/generated/protocol';
import { clipIndicatorRegions, mountIndicatorRegions } from './indicator-regions';

export type ChartStyle = 'candlestick' | 'line';
export type ChartCursorMode = 'crosshair' | 'pan';

export type ChartAppearance = { theme: 'dark' | 'light'; gain: string; loss: string };
export const defaultChartAppearance: ChartAppearance = { theme: 'dark', gain: '#43d4ac', loss: '#f17d85' };

const chartThemes = {
  dark: { background: '#131722', text: '#b2b5be', line: '#242b38', separatorHover: '#414b60', price: '#73b8f6' },
  light: { background: '#f7f9fc', text: '#34435a', line: '#dce3ec', separatorHover: '#a7b5c7', price: '#245caa' },
};

export type HistoryChartData = {
  bars: readonly Bar[];
  dataset: Pick<DatasetDescriptor, 'instrument_id' | 'timeframe' | 'timezone'>;
};

export type IndicatorChartLayer = { result: IndicatorResult; range: VisibleRange };

export interface HistoryChartHandle {
  dispose(): void;
  fit(): void;
  zoomIn(): void;
  zoomOut(): void;
  setCursor(mode: ChartCursorMode): void;
  setStyle(style: ChartStyle): void;
  setAppearance(appearance: ChartAppearance): void;
  setSelectedBar(time: number | undefined): void;
  replaceBars(bars: readonly Bar[]): void;
  updateLast(bar: Bar): void;
  setIndicatorLayers(preview: IndicatorChartLayer | undefined, accepted: readonly IndicatorChartLayer[]): void;
}

// Chart-library units and lifecycle stay inside this replaceable adapter.
export function mountHistoryChart(
  element: HTMLElement, history: HistoryChartData, instrument: Pick<Instrument, 'price_tick'>,
  style: ChartStyle, select: (bar: Bar) => void, activate?: (bar: Bar) => void,
): HistoryChartHandle {
  let appearance = defaultChartAppearance;
  let bars = [...history.bars];
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
  const colors = (theme: ChartAppearance['theme']) => ({
    layout: {
      background: { type: ColorType.Solid as const, color: chartThemes[theme].background },
      textColor: chartThemes[theme].text, attributionLogo: true,
      panes: { separatorColor: chartThemes[theme].line, separatorHoverColor: chartThemes[theme].separatorHover },
    },
    grid: { vertLines: { color: chartThemes[theme].line }, horzLines: { color: chartThemes[theme].line } },
    rightPriceScale: { borderColor: chartThemes[theme].line },
    timeScale: { borderColor: chartThemes[theme].line },
  });
  const theme = colors(appearance.theme);
  const chart = createChart(element, {
    ...theme,
    autoSize: true,
    timeScale: {
      ...theme.timeScale, timeVisible: true, secondsVisible: false, tickMarkFormatter: formatTime,
      fixLeftEdge: true, fixRightEdge: true,
      tickMarkMaxCharacterLength: history.dataset.timeframe === '1d' ? 10 : 13,
    },
    localization: { locale: 'en-GB', timeFormatter: formatTime },
  });
  try {
    const tick = instrument.price_tick;
    const priceFormat = tick
      ? { type: 'price' as const, precision: tick.split('.')[1]?.length ?? 0, minMove: Number(tick) || 0.01 }
      : { type: 'custom' as const, minMove: observedDisplayStep(bars), formatter: (value: number) => String(value) };
    const timestamp = (bar: Bar) => (bar.time_ms / 1000) as UTCTimestamp;
    const candle = (bar: Bar) => ({ time: timestamp(bar), open: bar.open, high: bar.high, low: bar.low, close: bar.close });
    const linePoint = (bar: Bar) => ({ time: timestamp(bar), value: bar.close });
    const volume = (bar: Bar) => bar.volume === null
      ? { time: timestamp(bar) }
      : { time: timestamp(bar), value: bar.volume, color: (bar.close >= bar.open ? appearance.gain : appearance.loss) + '6b' };
    const candleColors = () => ({
      upColor: appearance.gain, downColor: appearance.loss, wickUpColor: appearance.gain, wickDownColor: appearance.loss,
    });
    function addPriceSeries(nextStyle: ChartStyle) {
      let series: ISeriesApi<'Candlestick'> | ISeriesApi<'Line'> | undefined;
      try {
        if (nextStyle === 'candlestick') {
          series = chart.addSeries(CandlestickSeries, {
            priceFormat, ...candleColors(), borderVisible: false,
          });
          series.setData(bars.map(candle));
        } else {
          series = chart.addSeries(LineSeries, { priceFormat, color: chartThemes[appearance.theme].price, lineWidth: 2 });
          series.setData(bars.map(linePoint));
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
    const markerPositions = { above: 'aboveBar', below: 'belowBar', on: 'inBar' } as const;
    const timeScale = chart.timeScale();
    let markerPlugin = createSeriesMarkers<Time>(priceSeries, []);
    const setRegions = mountIndicatorRegions(priceSeries.getPane(), timeScale);
    let indicatorMarkers: SeriesMarker<Time>[] = [];
    let indicatorSeries: (ISeriesApi<'Line'> | ISeriesApi<'Histogram'>)[] = [];
    let indicatorLayers: (IndicatorChartLayer & { preview: boolean })[] = [];

    function renderIndicatorLayers(layers: typeof indicatorLayers) {
      const nextSeries: typeof indicatorSeries = [];
      const panes = new Map<string, number>();
      const onChart = (time: number, range: VisibleRange) => time >= range.from_ms && time < range.to_ms && lookup.has(time / 1000);
      const markers = layers.flatMap(({ result, range }) => result.markers.filter(marker => onChart(marker.time_ms, range)))
        .sort((a, b) => a.time_ms - b.time_ms).map(marker => ({
          time: (marker.time_ms / 1000) as UTCTimestamp, position: markerPositions[marker.position],
          shape: 'circle' as const, color: marker.color, text: marker.label,
        }));
      const regions = layers.flatMap(({ result, range, preview }) => clipIndicatorRegions(bars, result.regions, range, preview));
      try {
        for (const layer of layers) {
          for (const spec of layer.result.series) {
            const points = spec.points.filter(point => onChart(point.time_ms, layer.range));
            if (!points.length) continue;
            const pane = spec.pane === 'price' ? 0 : panes.get(spec.pane) ?? chart.panes().length;
            if (pane !== 0) panes.set(spec.pane, pane);
            const options = { color: spec.style.color, lastValueVisible: false, priceLineVisible: false };
            const series = spec.type === 'line'
              ? chart.addSeries(LineSeries, { ...options, lineWidth: spec.style.width as LineWidth, lineStyle: layer.preview ? 2 : 0 }, pane)
              : chart.addSeries(HistogramSeries, options, pane);
            nextSeries.push(series);
            series.setData(points.map(point => {
              const time = (point.time_ms / 1000) as UTCTimestamp;
              return point.value === null ? { time } : { time, value: point.value };
            }));
          }
        }
        markerPlugin.setMarkers(markers);
      } catch (error) {
        for (const series of nextSeries) chart.removeSeries(series);
        throw error;
      }
      for (const series of indicatorSeries) chart.removeSeries(series);
      indicatorSeries = nextSeries;
      indicatorMarkers = markers;
      setRegions(regions);
    }

    timeScale.fitContent();

    function zoom(factor: number) {
      const range = timeScale.getVisibleLogicalRange();
      if (!range || bars.length < 2) return;
      const count = Math.min(bars.length, Math.max(2, (range.to - range.from + 1) * factor));
      const center = (range.from + range.to) / 2;
      const from = Math.max(0, Math.min(center - (count - 1) / 2, bars.length - count));
      timeScale.setVisibleLogicalRange({ from, to: from + count - 1 });
    }

    let cursor: ChartCursorMode = 'crosshair';
    let inspectedTime: number | undefined;
    let selectedTime: number | undefined;
    const displayedBar = () => {
      const time = inspectedTime ?? selectedTime;
      return (time === undefined ? undefined : lookup.get(time / 1000)) ?? bars.at(-1);
    };
    const originalCursor = element.style.cursor;
    chart.subscribeCrosshairMove(event => {
      if (cursor === 'pan') return;
      const bar = lookup.get(Number(event.time));
      inspectedTime = bar?.time_ms;
      const selected = displayedBar();
      if (selected) select(selected);
    });
    if (activate) chart.subscribeClick(event => {
      const bar = cursor === 'crosshair' ? lookup.get(Number(event.time)) : undefined;
      if (bar) activate(bar);
    });
    element.style.cursor = 'crosshair';
    return {
      dispose() {
        element.style.cursor = originalCursor;
        setRegions([]);
        markerPlugin.detach();
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
          const selected = displayedBar();
          if (selected) select(selected);
        }
      },
      setStyle(nextStyle) {
        if (nextStyle === style) return;
        const range = timeScale.getVisibleLogicalRange();
        const next = addPriceSeries(nextStyle);
        markerPlugin.detach();
        chart.removeSeries(priceSeries);
        priceSeries = next;
        markerPlugin = createSeriesMarkers(priceSeries, indicatorMarkers);
        style = nextStyle;
        if (range) timeScale.setVisibleLogicalRange(range);
      },
      setAppearance(next) {
        if (next.theme === appearance.theme && next.gain === appearance.gain && next.loss === appearance.loss) return;
        appearance = next;
        chart.applyOptions(colors(next.theme));
        if (style === 'candlestick') (priceSeries as ISeriesApi<'Candlestick'>).applyOptions(candleColors());
        else (priceSeries as ISeriesApi<'Line'>).applyOptions({ color: chartThemes[next.theme].price });
        volumes.setData(bars.map(volume));
      },
      setSelectedBar(time) {
        if (selectedTime === time) return;
        selectedTime = time;
        inspectedTime = undefined;
        const bar = time === undefined ? undefined : lookup.get(time / 1_000);
        if (!bar) chart.clearCrosshairPosition();
        else if (cursor === 'crosshair') {
          const point = timestamp(bar);
          const index = timeScale.timeToIndex(point);
          const range = timeScale.getVisibleLogicalRange();
          if (index !== null && range && (index < range.from || index > range.to)) {
            const offset = index < range.from ? index - range.from : index - range.to;
            timeScale.setVisibleLogicalRange({ from: range.from + offset, to: range.to + offset });
          }
          chart.setCrosshairPosition(bar.close, point, priceSeries);
        }
        const selected = displayedBar();
        if (selected) select(selected);
      },
      replaceBars(nextBars) {
        const range = timeScale.getVisibleLogicalRange();
        const lastIndex = bars.length - 1;
        bars = [...nextBars];
        lookup.clear();
        for (const bar of bars) lookup.set(timestamp(bar), bar);
        priceSeries.setData(style === 'candlestick' ? bars.map(candle) : bars.map(linePoint));
        volumes.setData(bars.map(volume));
        if (indicatorLayers.length) renderIndicatorLayers(indicatorLayers);
        if (!range || (range.from <= 0 && range.to >= lastIndex)) timeScale.fitContent();
        else {
          const shift = range.to >= lastIndex ? bars.length - 1 - lastIndex : 0;
          timeScale.setVisibleLogicalRange({ from: range.from + shift, to: range.to + shift });
        }
        const selected = displayedBar();
        if (selected) select(selected);
      },
      setIndicatorLayers(preview, accepted) {
        const layers = accepted.map(layer => ({ ...layer, preview: false }));
        if (preview) layers.push({ ...preview, preview: true });
        renderIndicatorLayers(layers);
        indicatorLayers = layers;
      },
      updateLast(bar) {
        const previous = bars.at(-1);
        if (bar === previous || bar.time_ms !== previous?.time_ms) return;
        bars[bars.length - 1] = bar;
        lookup.set(timestamp(bar), bar);
        const point = { ...candle(bar), value: bar.close };
        priceSeries.update(point);
        volumes.update(volume(bar));
        if (displayedBar()?.time_ms === bar.time_ms) select(bar);
      },
    };
  } catch (error) { chart.remove(); throw error; }
}

function observedDisplayStep(bars: readonly Bar[]) {
  // Display precision from the supplied prices without declaring an exchange tick.
  let precision = 0;
  for (const bar of bars) {
    for (const price of [bar.open, bar.high, bar.low, bar.close]) {
      const [mantissa, exponent] = String(price).split('e');
      precision = Math.max(precision, (mantissa?.split('.')[1]?.length ?? 0) - Number(exponent ?? 0));
    }
  }
  return 10 ** -Math.min(precision, 12);
}
