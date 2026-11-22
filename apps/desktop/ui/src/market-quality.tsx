import { formatNumber, message, plural, t, type Message } from './i18n';
import type { ChartHistory, MarketFailure } from './market';
import type { ReplayRecoveryStatus } from './market-replay';
import type { ReplayGap } from './use-sample-replay';

const unknown = message('Unknown');
const sourceModes = {
  simulated: message('Simulated'), historical: message('Historical'), delayed: message('Delayed'), realtime: message('Real time'),
} satisfies Record<ChartHistory['dataset']['mode'], Message>;
const sourceKinds = {
  synthetic: message('Synthetic'), native_bars: message('Native bars'), snapshot_approximation: message('Snapshot approximation'),
} satisfies Record<ChartHistory['quality_state']['source_kind'], Message>;

const qualityStates = new Map([
  ['fresh', message('Fresh')],
  ['stale', message('Stale')],
  ['gapped', message('Gapped')],
  ['degraded', message('Degraded')],
  ['open', message('Open', undefined, 'session')],
  ['closed', message('Closed', undefined, 'session')],
  ['unknown', unknown],
  ['current', message('Current')],
  ['delayed', message('Delayed')],
  ['offline', message('Offline')],
  ['connected', message('Connected')],
  ['disconnected', message('Disconnected')],
  ['observed', message('Observed')],
  ['reconnecting', message('Reconnecting')],
  ['resumed', message('Resumed')],
]);

const gapReasons = new Map([
  ['epoch_changed', message('The stream restarted.')],
  ['epoch_reset', message('The stream restarted.')],
  ['sequence_gap', message('An update is missing.')],
  ['coverage_unknown', message('The sample has no matching replay boundary.')],
  ['history_evicted', message('The replay boundary is outside retained history.')],
  ['overflow', message('The replay could not keep up with incoming updates.')],
  ['source_gap', message('The source reported missing updates.')],
  ['stale_observation', message('An update predates the current observation.')],
]);

export function replayGapReason(gap: ReplayGap) {
  return t(gapReasons.get(gap.reason) ?? 'Replay continuity could not be verified.');
}

function utcTime(timestamp: number | null | undefined) {
  if (typeof timestamp !== 'number') return t(unknown);
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return t(unknown);
  const iso = date.toISOString();
  return <time dateTime={iso}>{iso.replace('T', ' ').replace(/(?:\.000)?Z$/, '')}</time>;
}

function stateLabel(state: string | undefined) {
  if (typeof state !== 'string' || !state) return t(unknown);
  const label = qualityStates.get(state);
  return label ? t(label) : state;
}

function UtcRange({ range }: { range: NonNullable<ReplayGap['affected']> }) {
  return <>[{utcTime(range.from_ms)}, {utcTime(range.to_ms)}) UTC</>;
}

function KnownGaps({ quality }: { quality: ChartHistory['quality_state'] | undefined }) {
  if (quality?.gaps.length) {
    return <ul className="quality-gaps" aria-label={t('Half-open intervals in UTC, inclusive start and exclusive end')}>
      {quality.gaps.map(range => <li key={`${range.from_ms}:${range.to_ms}`}><UtcRange range={range} /></li>)}
    </ul>;
  }
  return quality?.coverage === 'observed' ? t('None reported') : t(unknown);
}

export function ReplayGapDetails({ gap }: { gap: ReplayGap }) {
  return <span role="group" aria-label={t('Replay gap details')}>
    {gap.affected ? <>{t('Affected range')} <UtcRange range={gap.affected} /></> : t('Affected range unknown')}
    {' · '}{gap.lostCount === null ? t('Lost updates unknown') : plural('{count} lost update', '{count} lost updates', gap.lostCount)}
  </span>;
}

export function ReplayGapFacts({ gap }: { gap: ReplayGap }) {
  return <section aria-label={t('Replay gap')}><h3>{t('Replay gap')}</h3>
    <dl className="source-facts">
      <dt>{t('Reason')}</dt><dd>{replayGapReason(gap)}</dd>
      <dt>{t('Affected range (UTC)')}</dt><dd>{gap.affected ? <UtcRange range={gap.affected} /> : t(unknown)}</dd>
      <dt>{t('Lost updates')}</dt><dd>{gap.lostCount === null ? t(unknown) : formatNumber(gap.lostCount)}</dd>
    </dl>
  </section>;
}

export function MarketQualityFacts({ history, failureKind, workerRecovery }: {
  history: ChartHistory | undefined;
  failureKind: MarketFailure['kind'] | undefined;
  workerRecovery: ReplayRecoveryStatus | undefined;
}) {
  const dataset = history?.dataset;
  const quality = history?.quality_state;
  const workerConnection = workerRecovery ? stateLabel(workerRecovery) : failureKind === 'worker-exited' ? t('Disconnected') : undefined;
  return <dl className="source-facts">
    <dt>{t('Source')}</dt><dd>{dataset?.provider_id ?? t(unknown)}</dd>
    <dt>{t('Source mode')}</dt><dd>{t((dataset && sourceModes[dataset.mode]) ?? unknown)}</dd>
    <dt>{t('Source kind')}</dt><dd>{t((quality && sourceKinds[quality.source_kind]) ?? unknown)}</dd>
    <dt>{t('Revision')}</dt><dd>{dataset?.revision ?? t(unknown)}</dd>
    <dt>{t('Time zone')}</dt><dd>{dataset?.timezone ?? t(unknown)}</dd>
    <dt>{t('Quality at sample time')}</dt><dd>{stateLabel(dataset?.quality)}</dd>
    <dt>{t('As of (UTC)')}</dt><dd>{utcTime(quality?.as_of_ms)}</dd>
    <dt>{t('Last observed (UTC)')}</dt><dd>{utcTime(quality?.last_observed_ms)}</dd>
    <dt>{t('Session')}</dt><dd>{stateLabel(quality?.session_state)}</dd>
    <dt>{t('Connection')}</dt><dd>{stateLabel(quality?.connection)}</dd>
    {workerConnection && <><dt>{t('Worker connection')}</dt><dd>{workerConnection}</dd></>}
    {failureKind === 'transport-unavailable' && <><dt>{t('Local transport')}</dt><dd>{t('Unavailable')}</dd></>}
    <dt>{t('Freshness')}</dt><dd>{stateLabel(quality?.freshness)}</dd>
    <dt>{t('Coverage')}</dt><dd>{stateLabel(quality?.coverage)}</dd>
    <dt>{t('Known gaps')}</dt><dd><KnownGaps quality={quality} /></dd>
    <dt>{t('Input digest')}</dt><dd>{history?.snapshot.content_hash ?? t(unknown)}</dd>
  </dl>;
}
