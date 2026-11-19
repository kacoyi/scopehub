import type { ChartHistory, MarketFailure } from './market';
import type { ReplayGap } from './use-sample-replay';

const unknown = 'Unknown';
const sourceModes = {
  simulated: 'Simulated', historical: 'Historical', delayed: 'Delayed', realtime: 'Real time',
} satisfies Record<ChartHistory['dataset']['mode'], string>;
const sourceKinds = {
  synthetic: 'Synthetic', native_bars: 'Native bars', snapshot_approximation: 'Snapshot approximation',
} satisfies Record<ChartHistory['quality_state']['source_kind'], string>;

const gapReasons = new Map([
  ['epoch_changed', 'The stream restarted.'],
  ['epoch_reset', 'The stream restarted.'],
  ['sequence_gap', 'An update is missing.'],
  ['coverage_unknown', 'The sample has no matching replay boundary.'],
  ['history_evicted', 'The replay boundary is outside retained history.'],
  ['overflow', 'The replay could not keep up with incoming updates.'],
  ['source_gap', 'The source reported missing updates.'],
  ['stale_observation', 'An update predates the current observation.'],
]);

export function replayGapReason(gap: ReplayGap) {
  return gapReasons.get(gap.reason) ?? 'Replay continuity could not be verified.';
}

function utcTime(timestamp: number | null | undefined) {
  if (typeof timestamp !== 'number') return unknown;
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return unknown;
  const iso = date.toISOString();
  return <time dateTime={iso}>{iso.replace('T', ' ').replace(/(?:\.000)?Z$/, '')}</time>;
}

function stateLabel(state: string | undefined) {
  return typeof state === 'string' && state ? state.charAt(0).toUpperCase() + state.slice(1) : unknown;
}

function UtcRange({ range }: { range: NonNullable<ReplayGap['affected']> }) {
  return <>[{utcTime(range.from_ms)}, {utcTime(range.to_ms)}) UTC</>;
}

function KnownGaps({ quality }: { quality: ChartHistory['quality_state'] | undefined }) {
  if (quality?.gaps.length) {
    return <ul className="quality-gaps" aria-label="Half-open intervals in UTC, inclusive start and exclusive end">
      {quality.gaps.map(range => <li key={`${range.from_ms}:${range.to_ms}`}><UtcRange range={range} /></li>)}
    </ul>;
  }
  return quality?.coverage === 'observed' ? 'None reported' : unknown;
}

export function ReplayGapDetails({ gap }: { gap: ReplayGap }) {
  return <span role="group" aria-label="Replay gap details">
    {gap.affected ? <>Affected range <UtcRange range={gap.affected} /></> : 'Affected range unknown'}
    {' · '}{gap.lostCount === null ? 'Lost updates unknown' : `${gap.lostCount} ${gap.lostCount === 1 ? 'lost update' : 'lost updates'}`}
  </span>;
}

export function ReplayGapFacts({ gap }: { gap: ReplayGap }) {
  return <section aria-label="Replay gap"><h3>Replay gap</h3>
    <dl className="source-facts">
      <dt>Reason</dt><dd>{replayGapReason(gap)}</dd>
      <dt>Affected range (UTC)</dt><dd>{gap.affected ? <UtcRange range={gap.affected} /> : unknown}</dd>
      <dt>Lost updates</dt><dd>{gap.lostCount ?? unknown}</dd>
    </dl>
  </section>;
}

export function MarketQualityFacts({ history, failureKind }: {
  history: ChartHistory | undefined;
  failureKind: MarketFailure['kind'] | undefined;
}) {
  const dataset = history?.dataset;
  const quality = history?.quality_state;
  return <dl className="source-facts">
    <dt>Source</dt><dd>{dataset?.provider_id ?? unknown}</dd>
    <dt>Source mode</dt><dd>{(dataset && sourceModes[dataset.mode]) ?? unknown}</dd>
    <dt>Source kind</dt><dd>{(quality && sourceKinds[quality.source_kind]) ?? unknown}</dd>
    <dt>Revision</dt><dd>{dataset?.revision ?? unknown}</dd>
    <dt>Time zone</dt><dd>{dataset?.timezone ?? unknown}</dd>
    <dt>Quality at sample time</dt><dd>{stateLabel(dataset?.quality)}</dd>
    <dt>As of (UTC)</dt><dd>{utcTime(quality?.as_of_ms)}</dd>
    <dt>Last observed (UTC)</dt><dd>{utcTime(quality?.last_observed_ms)}</dd>
    <dt>Session</dt><dd>{stateLabel(quality?.session_state)}</dd>
    <dt>Connection</dt><dd>{stateLabel(quality?.connection)}</dd>
    {failureKind === 'worker-exited' && <><dt>Worker connection</dt><dd>Disconnected</dd></>}
    {failureKind === 'transport-unavailable' && <><dt>Local transport</dt><dd>Unavailable</dd></>}
    <dt>Freshness</dt><dd>{stateLabel(quality?.freshness)}</dd>
    <dt>Coverage</dt><dd>{stateLabel(quality?.coverage)}</dd>
    <dt>Known gaps</dt><dd><KnownGaps quality={quality} /></dd>
    <dt>Input digest</dt><dd>{history?.snapshot.content_hash ?? unknown}</dd>
  </dl>;
}
