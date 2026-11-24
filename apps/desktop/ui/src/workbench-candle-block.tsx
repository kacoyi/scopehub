import { useLayoutEffect, useMemo, useRef, useState } from 'react';
import { mountCandleAnnotations } from './chart-adapter/candle-annotations';
import { formatDateTime, formatNumber, t, useLocale } from './i18n';
import { candleSource, type CandleBlock, type NumericField } from './workbench-candle-source';

const fields = Object.keys(candleSource.fields) as NumericField[];
const timeframes = ['5m', '15m'] as const;
const timeLabel = (time: number) => formatDateTime(time, {
  timeZone: 'UTC', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
});
type Timeframe = typeof timeframes[number];

export function WorkbenchCandleBlock({ block }: { block: CandleBlock }) {
  const [timeframe, setTimeframe] = useState<Timeframe>('5m');
  const element = useRef<HTMLDivElement>(null);
  const handle = useRef<ReturnType<typeof mountCandleAnnotations>>(null);
  const locale = useLocale();
  const frame = candleSource.frames[timeframe];
  const binding = block.annotation;
  const annotations = useMemo(() => {
    const format = new Intl.NumberFormat(locale, {
      style: candleSource.fields[binding.field].unit === 'Fraction' ? 'percent' : 'decimal',
      maximumFractionDigits: 3,
    });
    return frame.observations.flatMap(row => {
      const gate = row[binding.when.field];
      const value = row[binding.field];
      return gate !== null && gate > binding.when.above && value !== null
        ? [{ timeMs: row.timeMs, text: format.format(value) }] : [];
    });
  }, [binding, frame, locale]);

  useLayoutEffect(() => {
    if (!element.current) return;
    const chart = mountCandleAnnotations(element.current);
    handle.current = chart;
    return () => {
      chart.dispose();
      handle.current = null;
    };
  }, []);
  useLayoutEffect(() => {
    handle.current?.setLocale(locale);
  }, [locale]);
  useLayoutEffect(() => {
    handle.current?.display(frame.observations, annotations);
  }, [frame, annotations]);

  return <section className="probe-block probe-candle-block" aria-label={t('Annotated candlesticks')}>
    <h3>{t('Annotated candlesticks')}</h3>
    <p>{t('Simulated')} · {candleSource.instrument}</p>
    <div className="probe-actions">
      {timeframes.map(period => <button key={period} type="button" aria-pressed={period === timeframe}
        onClick={() => setTimeframe(period)}>{t('{count} min', { count: period === '5m' ? 5 : 15 })}</button>)}
      <button type="button" onClick={() => handle.current?.zoomIn()}>{t('Zoom in')}</button>
      <button type="button" onClick={() => handle.current?.zoomOut()}>{t('Zoom out')}</button>
    </div>
    <div className="probe-candle-chart" role="img" aria-label={t('Candlestick chart with numeric annotations')} ref={element} />
    <details className="probe-source">
      <summary>{t('Synthetic result attribution')}</summary>
      <dl>
        <dt>{t('Granted source')}</dt><dd><code>{block.source}</code></dd>
        <dt>{t('Timeframe')}</dt><dd>{t('{count} min', { count: timeframe === '5m' ? 5 : 15 })}</dd>
        <dt>{t('Dataset revision')}</dt><dd>{frame.seriesRevision}</dd>
        <dt>{t('Result revision')}</dt><dd>{frame.resultRevision}</dd>
        <dt>{t('As of (UTC)')}</dt><dd>{new Date(frame.asOfMs).toISOString()}</dd>
      </dl>
      <div className="probe-candle-attribution">
        <table>
          <thead><tr><th scope="col">{t('Bar open (UTC)')}</th>
            {fields.map(field => {
              const { label, unit } = candleSource.fields[field];
              return <th key={field} scope="col">{t(label)} ({t(unit)}) · <code>{field}</code></th>;
            })}
            <th scope="col">{t('Annotation')}</th>
          </tr></thead>
          <tbody>{frame.observations.map(row => {
            const utc = new Date(row.timeMs).toISOString();
            return <tr key={row.timeMs} aria-label={utc}>
              <th scope="row"><time dateTime={utc}>{timeLabel(row.timeMs)}</time></th>
              {fields.map(field => {
                const value = row[field];
                return <td key={field}>{value === null ? t('Unknown') : formatNumber(value, { maximumFractionDigits: 3 })}</td>;
              })}
              <td>{annotations.find(annotation => annotation.timeMs === row.timeMs)?.text ?? '—'}</td>
            </tr>;
          })}</tbody>
        </table>
      </div>
    </details>
  </section>;
}
