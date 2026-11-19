import type { FrozenSnapshot, MarketQualityState, MarketStreamGapEvent, StreamPosition } from '../../../../contracts/generated/protocol';
import { invalidMarketData, type Bar, type ChartHistory } from './market';

export type RepairProgress = {
  bars: Bar[];
  snapshot: FrozenSnapshot;
  position: StreamPosition;
  quality_state: MarketQualityState;
};

const minuteMs = 60_000;

function isClosedBar(bar: Bar | undefined, time: number, tradingDay: string): bar is Bar {
  return bar?.is_closed === true && bar.time_ms === time && bar.trading_day === tradingDay
    && [bar.open, bar.high, bar.low, bar.close].every(Number.isFinite)
    && bar.low <= Math.min(bar.open, bar.close) && bar.high >= Math.max(bar.open, bar.close)
    && (bar.volume === null || (Number.isFinite(bar.volume) && bar.volume >= 0));
}

export function projectClosedBarRepair(
  repair: RepairProgress, history: ChartHistory, gap: MarketStreamGapEvent, confirmed: Bar, observedAt: number,
): readonly Bar[] {
  const original = history.snapshot;
  const { bars, snapshot, quality_state: quality, position } = repair;
  const { affected, reason, lost_count: lostCount, expected_sequence: expectedSequence } = gap.payload;
  const from = affected.from_ms;
  const close = confirmed.time_ms + minuteMs;
  const [knownGap] = history.quality_state.gaps;
  const previous = history.bars.at(-2);
  if (original.series.timeframe !== '1m' || history.quality_state.coverage !== 'gapped' || history.quality_state.gaps.length !== 1
    || knownGap?.from_ms !== from || knownGap.to_ms !== confirmed.time_ms
    || reason !== 'sequence_gap' || lostCount !== 1 || gap.sequence !== expectedSequence + lostCount
    || position.stream_id !== gap.stream_id || position.epoch !== gap.epoch || position.sequence !== gap.sequence
    || from !== confirmed.time_ms - minuteMs || affected.to_ms !== confirmed.time_ms
    || confirmed.is_closed || observedAt >= close || from < original.from_ms || close > original.to_ms
    || (previous && previous.time_ms >= from)) throw invalidMarketData();

  if (snapshot.dataset_id !== original.dataset_id || snapshot.dataset_revision !== original.dataset_revision
    || !snapshot.series || Object.keys(snapshot.series).length !== Object.keys(original.series).length
    || Object.entries(original.series).some(([field, value]) => snapshot.series[field as keyof typeof snapshot.series] !== value)
    || snapshot.from_ms !== from || snapshot.to_ms !== close || snapshot.as_of_ms !== close || snapshot.warmup_bars !== 0
    || typeof snapshot.content_hash !== 'string' || !/^[a-f0-9]{64}$/.test(snapshot.content_hash)
    || snapshot.content_hash === original.content_hash
    || quality.as_of_ms !== close || quality.last_observed_ms !== close || quality.coverage !== 'observed'
    || !Array.isArray(quality.gaps) || quality.gaps.length !== 0 || quality.source_kind !== 'synthetic'
    || quality.connection !== 'offline' || quality.session_state !== 'closed' || quality.freshness !== 'current') {
    throw invalidMarketData();
  }

  const [missing, finalBar] = bars;
  if (bars.length !== 2 || !isClosedBar(missing, from, confirmed.trading_day)
    || !isClosedBar(finalBar, confirmed.time_ms, confirmed.trading_day)
    || finalBar.open !== confirmed.open || finalBar.high < confirmed.high || finalBar.low > confirmed.low
    || (confirmed.volume === null ? finalBar.volume !== null : finalBar.volume === null || finalBar.volume < confirmed.volume)) {
    throw invalidMarketData();
  }

  // The verified slice replaces the chart tail. The original snapshot and rows still back the data window.
  return [...history.bars.slice(0, -1), missing, finalBar];
}
