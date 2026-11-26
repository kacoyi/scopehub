import type { InstrumentSearchOutput, MarketHistoryPageInput, MarketHistoryPageOutput } from '../../../../contracts/generated/protocol';

export function sampleHistoryMatchesRequest(
  page: MarketHistoryPageOutput,
  query: MarketHistoryPageInput,
  instrument: InstrumentSearchOutput['instruments'][number],
): boolean {
  const { snapshot, dataset, quality_state } = page;
  const { series, from_ms, to_ms, warmup_bars, expected_revision } = query;
  return quality_state.gaps.every(gap => gap.from_ms < gap.to_ms)
    && Object.entries(series).every(([field, value]) => snapshot.series[field as keyof typeof series] === value)
    && snapshot.from_ms === from_ms && snapshot.to_ms === to_ms && snapshot.warmup_bars === warmup_bars
    && dataset.instrument_id === instrument.id && dataset.timeframe === series.timeframe && dataset.provider_id === series.provider_id
    && dataset.mode === 'simulated' && dataset.timezone === instrument.timezone && dataset.calendar_id === instrument.calendar_id
    && dataset.quantity_unit === instrument.quantity_unit && dataset.revision === snapshot.dataset_revision
    && (expected_revision === undefined || dataset.revision === expected_revision)
    && dataset.id === snapshot.dataset_id && quality_state.as_of_ms === snapshot.as_of_ms;
}

export function sampleHistoryPageFitsSnapshot(
  page: MarketHistoryPageOutput,
  snapshot: MarketHistoryPageOutput['snapshot'],
  totalBars: number,
  bars: MarketHistoryPageOutput['bars'],
): boolean {
  const previous = bars.at(-1);
  const first = page.bars[0];
  const end = bars.length + page.bars.length;
  if (page.offset !== bars.length || page.total_bars !== totalBars || page.snapshot.content_hash !== snapshot.content_hash
    || end > totalBars || (previous && first && first.time_ms <= previous.time_ms)) return false;
  if (page.next_cursor === null) return end === totalBars;
  return page.bars.length > 0 && end < totalBars && page.next_cursor.next_index === end
    && page.next_cursor.snapshot_hash === snapshot.content_hash;
}
