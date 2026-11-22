import { useEffect, useRef, useState } from 'react';
import type { MarketStreamGap, MarketStreamGapEvent, MarketSubscriptionOutput } from '../../../../contracts/generated/protocol';
import { message } from './i18n';
import { describeMarketFailure, invalidMarketData, readStreamPosition, type Bar, type ChartHistory, type MarketFailure } from './market';
import { projectClosedBarRepair, validateClosedBarRepair, type ClosedBarRepair } from './market-repair';
import { samePosition, subscribeSample, type ReplayRecoveryStatus } from './market-replay';

export type ReplayGap = {
  reason: string;
  affected: MarketStreamGap['affected'] | null;
  lostCount: MarketStreamGap['lost_count'] | null;
};

type ReplayStatus =
  | { state: 'starting' | 'playing' | 'complete' | 'gap' | ReplayRecoveryStatus }
  | { state: 'failed'; failure: MarketFailure };
type Replay = ReplayStatus & { history: ChartHistory; bar?: Bar; repairedBars?: readonly Bar[]; gap: ReplayGap | undefined };

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
    let gap: ReplayGap | undefined;
    const update = (status: ReplayStatus, repairedBars?: readonly Bar[]) => {
      if (!signal.aborted) setReplay({ history, ...status, gap,
        ...(repairedBars ? { repairedBars } : hasUpdate ? { bar } : {}),
      });
    };
    update({ state: 'starting' });
    try {
      const snapshot = history.snapshot;
      const after = await readStreamPosition(snapshot, signal);
      if (!after) {
        gap = { reason: 'coverage_unknown', affected: null, lostCount: null };
        update({ state: 'gap' });
        return;
      }
      const matchesSeries = (series: typeof snapshot.series | undefined) => series
        && Object.entries(snapshot.series).every(([key, value]) => series[key as keyof typeof series] === value);
      const stream: {
        subscription?: MarketSubscriptionOutput;
        recovery?: ReplayRecoveryStatus;
        gapEvent?: MarketStreamGapEvent;
        repair?: ClosedBarRepair;
      } = {};
      let sequence = after.sequence;
      let observed = snapshot.as_of_ms;
      const result = await subscribeSample({ series: snapshot.series, snapshot, after }, signal, frame => {
        if (signal.aborted) return;
        if ('repair' in frame) {
          if (!stream.gapEvent || stream.repair) throw invalidMarketData();
          stream.repair = validateClosedBarRepair(frame.repair, history, stream.gapEvent, bar, observed);
          return;
        }
        if (gap) throw invalidMarketData();
        if ('subscription' in frame) {
          const reply = frame.subscription;
          if (stream.subscription || !reply) throw invalidMarketData();
          if (reply.status === 'accepted') {
            if (!reply.from_position || !samePosition(after, reply.from_position)) throw invalidMarketData();
            update({ state: 'playing' });
          } else if (reply.status !== 'backfill_required' || typeof reply.reason !== 'string') {
            throw invalidMarketData();
          }
          stream.subscription = reply;
          return;
        }
        if (stream.subscription?.status !== 'accepted') throw invalidMarketData();
        if ('recovery' in frame) {
          const { status, position } = frame.recovery;
          const expectedStatus = stream.recovery === 'reconnecting' ? 'resumed' : 'reconnecting';
          if (bar.is_closed || status !== expectedStatus || !samePosition({ ...after, sequence }, position)) {
            throw invalidMarketData();
          }
          stream.recovery = status;
          update({ state: status });
          return;
        }
        const event = frame.event;
        if (stream.recovery === 'reconnecting' || !event || event.stream_id !== after.stream_id || event.epoch !== after.epoch) {
          throw invalidMarketData();
        }
        if (event.event === 'market.stream_gap') {
          const reported = event.payload;
          const affected = reported?.affected;
          if (!matchesSeries(reported?.series) || typeof reported.reason !== 'string'
            || !Number.isSafeInteger(event.sequence) || event.sequence < sequence + 1 || reported.expected_sequence !== sequence + 1
            || !Number.isSafeInteger(reported.lost_count) || reported.lost_count < 1
            || !affected || !Number.isSafeInteger(affected.from_ms) || !Number.isSafeInteger(affected.to_ms)
            || affected.from_ms >= affected.to_ms) throw invalidMarketData();
          gap = { reason: reported.reason, affected, lostCount: reported.lost_count };
          stream.gapEvent = event;
          // The stream can still fail while reaching its terminal frame.
          update({ state: 'playing' });
          return;
        }
        if (event.event !== 'market.bar_update') throw invalidMarketData();
        const next = event.payload;
        if (!next?.bar || !matchesSeries(next.series)
          || event.sequence !== sequence + 1 || next.dataset_revision !== snapshot.dataset_revision
          || !Number.isSafeInteger(next.observed_at_ms) || next.observed_at_ms <= observed
          || next.bar.time_ms !== bar.time_ms || bar.is_closed) {
          throw invalidMarketData();
        }
        sequence = event.sequence;
        observed = next.observed_at_ms;
        bar = next.bar;
        hasUpdate = true;
        update({ state: stream.recovery === 'resumed' ? 'resumed' : 'playing' });
      });
      if (stream.repair) {
        if (result?.status !== 'complete') throw invalidMarketData();
        signal.throwIfAborted();
        const repairedBars = await projectClosedBarRepair(stream.repair, history);
        signal.throwIfAborted();
        if (!repairedBars) {
          update({ state: 'failed', failure: {
            kind: 'invalid-data', message: message('The closed-bar repair could not be verified.'), retryable: false,
          } });
          return;
        }
        gap = undefined;
        update({ state: 'complete' }, repairedBars);
      } else if (result?.status === 'gapped' && typeof result.reason === 'string') {
        if ((gap && gap.reason !== result.reason)
          || (stream.subscription?.status === 'backfill_required' && stream.subscription.reason !== result.reason)) {
          throw invalidMarketData();
        }
        gap ??= { reason: result.reason, affected: null, lostCount: null };
        update({ state: 'gap' });
      } else if (result?.status === 'complete' && stream.subscription?.status === 'accepted'
        && stream.recovery !== 'reconnecting' && bar.is_closed && !gap) {
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
