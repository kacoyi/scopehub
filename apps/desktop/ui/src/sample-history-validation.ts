import type { FrozenSnapshot, MarketHistoryPageInput, MarketHistoryPageOutput, SeriesKey } from '../../../../contracts/generated/protocol';

function sameSeries(requested: SeriesKey, received: SeriesKey): boolean {
  return Object.entries(requested).every(([field, value]) => received[field as keyof SeriesKey] === value);
}

function matchesFrozenSnapshot(requested: FrozenSnapshot, received: FrozenSnapshot): boolean {
  return sameSeries(requested.series, received.series)
    && requested.dataset_id === received.dataset_id && requested.dataset_revision === received.dataset_revision
    && requested.from_ms === received.from_ms && requested.to_ms === received.to_ms
    && requested.warmup_bars === received.warmup_bars && requested.as_of_ms === received.as_of_ms
    && requested.content_hash === received.content_hash;
}

function pageBoundsMatch(page: MarketHistoryPageOutput, query: MarketHistoryPageInput): boolean {
  const { bars, offset, total_bars, next_cursor, snapshot } = page;
  const { cursor, page_size } = query;
  const end = offset + bars.length;
  if (offset !== (cursor?.next_index ?? 0) || (page_size !== undefined && bars.length > page_size)
    || end > total_bars || (bars.length === 0 && total_bars !== 0)) return false;
  if (cursor && (!query.snapshot || cursor.snapshot_hash !== snapshot.content_hash
    || (bars[0] && bars[0].time_ms <= cursor.previous_time_ms))) return false;
  if (next_cursor === null) return end === total_bars;
  return end < total_bars && next_cursor.next_index === end
    && next_cursor.snapshot_hash === snapshot.content_hash && next_cursor.previous_time_ms === bars.at(-1)?.time_ms;
}

function barsMatchSnapshot(page: MarketHistoryPageOutput): boolean {
  const { bars, offset, total_bars, snapshot } = page;
  return bars.every((bar, index) => {
    const previous = bars[index - 1];
    const position = offset + index;
    return (!previous || previous.time_ms < bar.time_ms)
      && (bar.time_ms >= snapshot.from_ms || position < snapshot.warmup_bars)
      && bar.time_ms < snapshot.to_ms && bar.time_ms <= snapshot.as_of_ms
      && bar.low <= Math.min(bar.open, bar.close) && bar.high >= Math.max(bar.open, bar.close)
      && (bar.volume === null || bar.volume >= 0) && (bar.is_closed || position + 1 === total_bars);
  });
}

// The registry verifies the bar digest. The UI admits the decoded page only
// when its request, frozen identity, pagination and projection agree.
export function sampleHistoryPageMatchesRequest(
  page: MarketHistoryPageOutput,
  query: MarketHistoryPageInput,
  source: { timezone: string },
): boolean {
  const { snapshot, dataset, quality_state: quality, stream_position: position } = page;
  const { series, from_ms, to_ms, warmup_bars, as_of_ms, expected_revision } = query;
  if (from_ms >= to_ms || !sameSeries(series, snapshot.series)
    || snapshot.from_ms !== from_ms || snapshot.to_ms !== to_ms || snapshot.warmup_bars !== warmup_bars
    || (as_of_ms !== undefined && snapshot.as_of_ms !== as_of_ms)
    || (expected_revision !== undefined && snapshot.dataset_revision !== expected_revision)
    || (query.snapshot && !matchesFrozenSnapshot(query.snapshot, snapshot))) return false;

  if (dataset.id !== snapshot.dataset_id || dataset.revision !== snapshot.dataset_revision
    || dataset.instrument_id !== series.instrument_id || dataset.provider_id !== series.provider_id
    || dataset.timeframe !== series.timeframe || dataset.price_adjustment !== series.price_adjustment
    || dataset.calendar_id !== series.calendar_id || dataset.quantity_unit !== series.quantity_unit
    || dataset.timezone !== source.timezone || dataset.mode !== 'simulated' || dataset.provenance.synthetic !== true) return false;

  if (quality.as_of_ms !== snapshot.as_of_ms || (quality.last_observed_ms !== null && quality.last_observed_ms > quality.as_of_ms)
    || quality.connection !== 'offline' || quality.source_kind !== 'synthetic'
    || !quality.gaps.every(gap => gap.from_ms < gap.to_ms)) return false;
  if (position !== null && (query.include_stream_position !== true || position.stream_id !== snapshot.dataset_id
    || quality.last_observed_ms !== snapshot.as_of_ms)) return false;

  return pageBoundsMatch(page, query) && barsMatchSnapshot(page);
}
