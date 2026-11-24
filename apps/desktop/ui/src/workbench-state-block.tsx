import type { ReactNode } from 'react';
import { formatDateTime, formatNumber, t } from './i18n';
import { candleSource, type CandleTimeframe, type NumericField } from './workbench-candle-source';

const toneLabels = { green: () => t('Green'), amber: () => t('Amber'), red: () => t('Red'), neutral: () => t('Neutral') };
export type StateStyle = { tone: keyof typeof toneLabels; label?: string };
export type StateMapping = {
  ranges: (StateStyle & { below: number })[];
  otherwise: StateStyle;
};

function styleFor(value: number | null, mapping: StateMapping): StateStyle {
  if (value === null) return { tone: 'neutral', label: t('Unknown') };
  return mapping.ranges.find(range => value < range.below) ?? mapping.otherwise;
}

export function WorkbenchStateBlock({ field, source, timeframe, controls, mapping, selectedBar, selectBar }: {
  field: NumericField; source: typeof candleSource.id; timeframe: CandleTimeframe; controls: ReactNode;
  mapping: StateMapping; selectedBar: number | undefined; selectBar: (time: number) => void;
}) {
  const frame = candleSource.frames[timeframe];
  const { label, unit } = candleSource.fields[field];
  const selected = frame.observations.find(row => row.timeMs === selectedBar);
  const selectedUtc = selected && new Date(selected.timeMs).toISOString();
  const value = selected?.[field];

  return <section className="probe-block probe-state-block" aria-label={t('State lights')}>
    <h3>{t('State lights')}</h3>
    <p>{t('Simulated')} · {candleSource.instrument}</p>
    <p>{t(label)} ({t(unit)})</p>
    <div className="probe-actions">{controls}</div>
    <div className="probe-state-rows">{frame.observations.map(row => {
      const style = styleFor(row[field], mapping);
      const time = formatDateTime(row.timeMs, { timeZone: 'UTC', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
      return <button type="button" key={row.timeMs} className="probe-state-bar" aria-pressed={row === selected}
        onClick={() => selectBar(row.timeMs)}>
        <time dateTime={new Date(row.timeMs).toISOString()}>{t('{time} UTC', { time })}</time>
        <span className="probe-state-label">
          <span className="probe-state-light" aria-hidden="true" data-tone={style.tone} />
          {style.label ?? toneLabels[style.tone]()}
        </span>
      </button>;
    })}</div>
    <section className="probe-source probe-state-inspection" aria-label={t('Selected result')} aria-live="polite" aria-atomic="true">
      <h4>{t('Selected result')}</h4>
      {selectedUtc && value !== undefined ? <dl>
        <dt>{t('Bar open (UTC)')}</dt><dd><time dateTime={selectedUtc}>{selectedUtc}</time></dd>
        <dt>{t('Granted source')}</dt><dd><code>{source}</code></dd>
        <dt>{t('Dataset revision')}</dt><dd>{frame.seriesRevision}</dd>
        <dt>{t('Result revision')}</dt><dd>{frame.resultRevision}</dd>
        <dt>{t('Result value')}</dt><dd>{value === null ? t('Unknown') : formatNumber(value, { maximumFractionDigits: 15 })}</dd>
      </dl> : <p>{t('Select a bar to inspect its result.')}</p>}
    </section>
  </section>;
}
