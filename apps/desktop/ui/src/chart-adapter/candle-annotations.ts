import {
  CandlestickSeries, ColorType, CrosshairMode, createChart,
  type IPrimitivePaneRenderer, type IPrimitivePaneView, type ISeriesPrimitive, type UTCTimestamp,
} from 'lightweight-charts';
import { defaultChartAppearance } from './history';

export type Candle = Readonly<{ timeMs: number; open: number; high: number; low: number; close: number }>;
export type CandleAnnotation = Readonly<{ timeMs: number; text: string }>;
type ReplayCandle = Readonly<{ sequence: number; candle: Candle }>;

export function mountCandleAnnotations(element: HTMLElement) {
  const chart = createChart(element, {
    autoSize: true,
    layout: { background: { type: ColorType.Solid, color: '#131722' }, textColor: '#b2b5be', attributionLogo: true },
    grid: { vertLines: { color: '#242b38' }, horzLines: { color: '#242b38' } },
    crosshair: { mode: CrosshairMode.Hidden },
    rightPriceScale: { scaleMargins: { top: 0.25, bottom: 0.12 } },
    timeScale: { timeVisible: true, secondsVisible: false, minBarSpacing: 16, shiftVisibleRangeOnNewBar: false },
  });
  try {
    const { gain, loss } = defaultChartAppearance;
    const series = chart.addSeries(CandlestickSeries, {
      upColor: gain, downColor: loss, wickUpColor: gain, wickDownColor: loss, borderVisible: false,
      lastValueVisible: false, priceLineVisible: false,
    });
    const timeScale = chart.timeScale();
    const timestamp = (timeMs: number) => (timeMs / 1000) as UTCTimestamp;
    const datum = ({ timeMs, open, high, low, close }: Candle) => ({ time: timestamp(timeMs), open, high, low, close });
    let candles: readonly Candle[] | undefined;
    let replay: ReplayCandle | undefined;
    let labels: { time: UTCTimestamp; high: number; text: string }[] = [];
    let resultRevision = '';
    let annotationRevision = 0;
    let viewportRevision = 0;
    let viewportKey = '';
    let drawNumber = 0;
    let requestUpdate = () => {};
    const renderer: IPrimitivePaneRenderer = {
      draw(target) {
        target.useMediaCoordinateSpace(({ context, mediaSize }) => {
          // Read both axes during the SDK's pane draw, with the same series and viewport as the candles.
          const drawn = [];
          for (const label of labels) {
            const x = timeScale.timeToCoordinate(label.time);
            const y = series.priceToCoordinate(label.high);
            if (x === null || y === null || x < 0 || x >= mediaSize.width || y < 0 || y >= mediaSize.height) continue;
            if (drawBubble(context, mediaSize.width, mediaSize.height, x, y, label.text)) {
              drawn.push({ timeMs: label.time * 1000, x, y, text: label.text });
            }
          }
          const viewport = {
            width: mediaSize.width, height: mediaSize.height,
            logical: timeScale.getVisibleLogicalRange(), price: series.priceScale().getVisibleRange(),
          };
          const key = JSON.stringify(viewport);
          if (key !== viewportKey) {
            viewportKey = key;
            viewportRevision++;
          }
          // This is a draw callback observation. The platform presents the canvas separately.
          element.dataset.primitiveDraw = JSON.stringify({
            drawNumber: ++drawNumber, atMs: performance.now(), quoteSequence: replay?.sequence ?? 0,
            viewportRevision, annotationRevision, resultRevision, viewport, annotations: drawn,
          });
        });
      },
    };
    const views: IPrimitivePaneView[] = [{ renderer: () => renderer }];
    const primitive: ISeriesPrimitive = {
      attached: params => { requestUpdate = params.requestUpdate; },
      paneViews: () => views,
    };
    series.attachPrimitive(primitive);

    function zoom(factor: number) {
      const range = timeScale.getVisibleLogicalRange();
      if (!range) return;
      const halfSpan = Math.max(0.8, (range.to - range.from) * factor / 2);
      const center = (range.to + range.from) / 2;
      timeScale.setVisibleLogicalRange({ from: center - halfSpan, to: center + halfSpan });
    }

    return {
      display(nextCandles: readonly Candle[], annotations: readonly CandleAnnotation[], revision: string, quote?: ReplayCandle) {
        const visibleCandles = quote ? [...nextCandles, quote.candle] : nextCandles;
        const byTime = new Map(annotations.map(({ timeMs, text }) => [timeMs, text]));
        labels = visibleCandles.flatMap(({ timeMs, high }) => {
          const text = byTime.get(timeMs);
          return text === undefined ? [] : [{ time: timestamp(timeMs), high, text }];
        });
        resultRevision = revision;
        annotationRevision++;
        if (candles !== nextCandles) {
          candles = nextCandles;
          series.setData(candles.map(datum));
          timeScale.setVisibleLogicalRange({ from: -1, to: candles.length });
        } else if (replay && !quote) {
          const range = timeScale.getVisibleLogicalRange();
          series.setData(candles.map(datum));
          if (range) timeScale.setVisibleLogicalRange(range);
        }
        if (quote && quote !== replay) series.update(datum(quote.candle));
        replay = quote;
        requestUpdate();
      },
      setLocale(locale: string) {
        const time = new Intl.DateTimeFormat(locale, {
          timeZone: 'UTC', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
        });
        const format = (stamp: unknown) => time.format(Number(stamp) * 1000);
        chart.applyOptions({ localization: { locale, timeFormatter: format }, timeScale: { tickMarkFormatter: format } });
      },
      zoomIn: () => zoom(0.75),
      zoomOut: () => zoom(1 / 0.75),
      dispose() {
        series.detachPrimitive(primitive);
        chart.remove();
        delete element.dataset.primitiveDraw;
      },
    };
  } catch (error) {
    chart.remove();
    throw error;
  }
}

function drawBubble(context: CanvasRenderingContext2D, width: number, height: number, x: number, y: number, text: string) {
  context.font = '600 12px system-ui, sans-serif';
  const bubbleWidth = Math.ceil(context.measureText(text).width) + 16;
  const bubbleHeight = 24;
  const left = Math.max(1, Math.min(x - bubbleWidth / 2, width - bubbleWidth - 1));
  const above = y >= bubbleHeight + 12;
  const top = above ? y - bubbleHeight - 8 : y + 8;
  if (top + bubbleHeight >= height || bubbleWidth + 2 > width) return false;

  const center = left + bubbleWidth / 2;
  const edge = above ? top + bubbleHeight : top;
  context.strokeStyle = '#c79aff';
  context.lineWidth = 1.5;
  context.beginPath();
  context.moveTo(center, edge);
  context.lineTo(x, y + (above ? -2 : 2));
  context.stroke();

  const shade = context.createLinearGradient(0, top, 0, top + bubbleHeight);
  shade.addColorStop(0, 'rgba(111, 66, 172, 0.92)');
  shade.addColorStop(1, 'rgba(47, 27, 78, 0.92)');
  context.fillStyle = shade;
  context.beginPath();
  context.roundRect(left, top, bubbleWidth, bubbleHeight, 8);
  context.fill();
  context.stroke();
  context.fillStyle = '#fff';
  context.textAlign = 'center';
  context.textBaseline = 'middle';
  context.fillText(text, center, top + bubbleHeight / 2);
  return true;
}
