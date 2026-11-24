import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { mountCandleAnnotations } from './chart-adapter/candle-annotations';
import { formatDateTime, formatNumber, t, useLocale } from './i18n';
import {
  advanceCandleReplay, candleReplay, candleSource,
  type CandleBlock, type CandleReplayPosition, type CandleTimeframe, type NumericField,
} from './workbench-candle-source';

const fields = Object.keys(candleSource.fields) as NumericField[];
const timeLabel = (time: number) => formatDateTime(time, {
  timeZone: 'UTC', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
});
export function WorkbenchCandleBlock({ block, timeframe, controls, accepted }: {
  block: CandleBlock; timeframe: CandleTimeframe; controls: ReactNode; accepted: boolean;
}) {
  const element = useRef<HTMLDivElement>(null);
  const handle = useRef<ReturnType<typeof mountCandleAnnotations>>(null);
  const locale = useLocale();
  const frame = candleSource.frames[timeframe];
  const binding = block.annotation;
  const scope = accepted && block.source === candleSource.id && timeframe === '5m'
    ? JSON.stringify([block.source, binding.field, binding.when.field, binding.when.above]) : undefined;
  const [playback, setPlayback] = useState<CandleReplayPosition & { scope: string }>();
  const active = playback?.scope === scope ? playback : undefined;
  const quote = active?.quote;
  const ended = active?.step === candleReplay.steps;
  const replayRevision = `${block.source}-${timeframe}-replay`;
  const resultRevision = quote ? `${replayRevision}-results-${quote.sequence}` : frame.resultRevision;
  const observations = useMemo(() => quote ? [...frame.observations, quote.candle] : frame.observations, [frame, quote]);
  const annotations = useMemo(() => {
    const format = new Intl.NumberFormat(locale, {
      style: candleSource.fields[binding.field].unit === 'Fraction' ? 'percent' : 'decimal',
      maximumFractionDigits: 3,
    });
    return observations.flatMap(row => {
      const gate = row[binding.when.field];
      const value = row[binding.field];
      return gate !== null && gate > binding.when.above && value !== null
        ? [{ timeMs: row.timeMs, text: format.format(value) }] : [];
    });
  }, [binding, observations, locale]);

  useLayoutEffect(() => {
    if (playback && playback.scope !== scope) setPlayback(undefined);
  }, [playback, scope]);
  useEffect(() => {
    if (!active || ended) return;
    const timer = window.setTimeout(() => {
      setPlayback(current => current === active ? { ...advanceCandleReplay(active, block.source), scope: active.scope } : current);
    }, candleReplay.intervalMs);
    return () => window.clearTimeout(timer);
  }, [active, block.source, ended]);

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
    handle.current?.display(frame.observations, annotations, resultRevision, quote);
  }, [frame, annotations, resultRevision, quote]);

  return <section className="probe-block probe-candle-block" aria-label={t('Annotated candlesticks')}>
    <h3>{t('Annotated candlesticks')}</h3>
    <p>{t('Simulated')} · {candleSource.instrument}</p>
    <div className="probe-actions">
      {controls}
      <button type="button" onClick={() => handle.current?.zoomIn()}>{t('Zoom in')}</button>
      <button type="button" onClick={() => handle.current?.zoomOut()}>{t('Zoom out')}</button>
      {scope && <button type="button" disabled={!!active}
        onClick={() => setPlayback({ step: 0, scope })}>{t('Start synthetic quote replay')}</button>}
      {active && <button type="button" onClick={() => setPlayback(undefined)}>{t('Reset synthetic quote replay')}</button>}
    </div>
    <div className="probe-candle-chart" role="img" aria-label={t('Candlestick chart with numeric annotations')} ref={element} />
    {active && <p role="status">{ended ? t('Synthetic quote replay complete')
      : active.rejected === 'stale' ? t('Ignored stale replay quote')
      : active.rejected === 'source' ? t('Ignored replay quote from another source')
      : t('Synthetic replay quote {sequence} of {total}', { sequence: quote?.sequence ?? 0, total: candleReplay.finalSequence })}</p>}
    <details className="probe-source">
      <summary>{t('Synthetic result attribution')}</summary>
      <dl>
        <dt>{t('Granted source')}</dt><dd><code>{block.source}</code></dd>
        <dt>{t('Timeframe')}</dt><dd>{t('{count} min', { count: timeframe === '5m' ? 5 : 15 })}</dd>
        <dt>{t('Dataset revision')}</dt><dd>{frame.seriesRevision}</dd>
        <dt>{t('Result revision')}</dt><dd>{frame.resultRevision}</dd>
        <dt>{t('As of (UTC)')}</dt><dd>{new Date(frame.asOfMs).toISOString()}</dd>
        {quote && <>
          <dt>{t('Replay dataset revision')}</dt><dd>{`${replayRevision}-bars-${quote.sequence}`}</dd>
          <dt>{t('Replay result revision')}</dt><dd>{resultRevision}</dd>
          <dt>{t('Replay as of (UTC)')}</dt><dd>{new Date(quote.asOfMs).toISOString()}</dd>
          <dt>{t('Replay close price')}</dt><dd>{formatNumber(quote.candle.close)}</dd>
        </>}
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
          <tbody>{observations.map(row => {
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
