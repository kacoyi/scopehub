import { useEffect, useRef, useState } from 'react';
import type { MarketStreamGap } from '../../../../contracts/generated/protocol';
import { describeMarketFailure, invalidMarketData, readStreamPosition, type Bar, type ChartHistory, type MarketFailure } from './market';
import { samePosition, subscribeSample } from './market-replay';

export type ReplayGap = {
  reason: string;
  affected: MarketStreamGap['affected'] | null;
  lostCount: MarketStreamGap['lost_count'] | null;
};

type ReplayStatus =
  | { state: 'starting' | 'playing' | 'complete' }
  | { state: 'gap'; gap: ReplayGap }
  | { state: 'failed'; failure: MarketFailure };
type Replay = ReplayStatus & { history: ChartHistory; bar?: Bar };

export function useSampleReplay(history: ChartHistory | undefined) {
  const [replay, setReplay] = useState<Replay>();
  const active = useRef<{ history: ChartHistory; controller: AbortController }>(undefined);
  useEffect(() => {
    setReplay(old => old?.history === history ? old : undefined);
    return () => {
      if (active.current?.history === history) active.current?.controller.abort();
    };
  }, [history]);
  const current = replay?.history === history ? replay : undefined;

  async function start() {
    const initial = history?.bars.at(-1);
    if (!history || !initial) return;
    active.current?.controller.abort();
    const controller = new AbortController();
    active.current = { history, controller };
    const { signal } = controller;
    let bar = initial;
    let hasUpdate = false;
    const update = (status: ReplayStatus) => {
      if (!signal.aborted) setReplay({ history, ...status, ...(hasUpdate ? { bar } : {}) });
    };
    update({ state: 'starting' });
    try {
      const snapshot = history.snapshot;
      const after = await readStreamPosition(snapshot, signal);
      if (!after) {
        update({ state: 'gap', gap: { reason: 'coverage_unknown', affected: null, lostCount: null } });
        return;
      }
      const matchesSeries = (series: typeof snapshot.series | undefined) => series
        && Object.entries(snapshot.series).every(([key, value]) => series[key as keyof typeof series] === value);
      let accepted = false;
      let sequence = after.sequence;
      let observed = snapshot.as_of_ms;
      let reportedGap: MarketStreamGap | undefined;
      const result = await subscribeSample({ series: snapshot.series, snapshot, after }, signal, frame => {
        if (signal.aborted) return;
        if (reportedGap) throw invalidMarketData();
        if ('subscription' in frame) {
          if (!frame.subscription) throw invalidMarketData();
          if (frame.subscription.status === 'accepted') {
            const from = frame.subscription.from_position;
            if (accepted || !from || !samePosition(after, from)) throw invalidMarketData();
            accepted = true;
            update({ state: 'playing' });
          }
          return;
        }
        const event = frame.event;
        if (!event || !accepted || event.stream_id !== after.stream_id || event.epoch !== after.epoch) throw invalidMarketData();
        if (event.event === 'market.stream_gap') {
          const gap = event.payload;
          const affected = gap?.affected;
          if (!matchesSeries(gap?.series) || typeof gap.reason !== 'string'
            || !Number.isSafeInteger(event.sequence) || event.sequence < sequence + 1 || gap.expected_sequence !== sequence + 1
            || !Number.isSafeInteger(gap.lost_count) || gap.lost_count < 1
            || !affected || !Number.isSafeInteger(affected.from_ms) || !Number.isSafeInteger(affected.to_ms)
            || affected.from_ms >= affected.to_ms) throw invalidMarketData();
          reportedGap = gap;
          return;
        }
        if (event.event !== 'market.bar_update') throw invalidMarketData();
        const next = event.payload;
        if (!next?.bar || !matchesSeries(next.series)
          || event.sequence !== sequence + 1 || next.dataset_revision !== snapshot.dataset_revision
          || next.observed_at_ms <= observed || next.bar.time_ms !== bar.time_ms || bar.is_closed) {
          throw invalidMarketData();
        }
        sequence = event.sequence;
        observed = next.observed_at_ms;
        bar = next.bar;
        hasUpdate = true;
        update({ state: 'playing' });
      });
      if (result?.status === 'gapped' && typeof result.reason === 'string') {
        if (reportedGap && reportedGap.reason !== result.reason) throw invalidMarketData();
        update({ state: 'gap', gap: {
          reason: reportedGap?.reason ?? result.reason,
          affected: reportedGap?.affected ?? null,
          lostCount: reportedGap?.lost_count ?? null,
        } });
      } else if (result?.status === 'complete' && accepted && bar.is_closed && !reportedGap) {
        update({ state: 'complete' });
      } else throw invalidMarketData();
    } catch (error) {
      update({ state: 'failed', failure: describeMarketFailure(error) });
    } finally {
      if (active.current?.controller === controller) active.current = undefined;
    }
  }

  function stop() {
    active.current?.controller.abort();
    active.current = undefined;
    setReplay(undefined);
  }

  return { replay: current, start, stop };
}
