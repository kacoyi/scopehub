import { useEffect, useRef, useState } from 'react';
import { readStreamPosition, type Bar, type ChartHistory } from './market';
import { samePosition, subscribeSample } from './market-replay';

type Replay = { history: ChartHistory; state: 'starting' | 'playing' | 'complete' | 'gap'; bar?: Bar; detail?: string };

const gapText: Record<string, string> = {
  epoch_changed: 'The stream restarted.',
  epoch_reset: 'The stream restarted.',
  sequence_gap: 'An update is missing.',
  coverage_unknown: 'The sample has no matching replay boundary.',
  source_gap: 'The source reported missing updates.',
  stale_observation: 'An update predates the current observation.',
};

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
    const update = (state: Replay['state'], detail?: string) => {
      if (!signal.aborted) setReplay({ history, state, ...(hasUpdate ? { bar } : {}), ...(detail ? { detail } : {}) });
    };
    update('starting');
    try {
      const snapshot = history.snapshot;
      const after = await readStreamPosition(snapshot, signal);
      if (!after) { update('gap', gapText.coverage_unknown); return; }
      let accepted = false;
      let sequence = after.sequence;
      let observed = snapshot.as_of_ms;
      const result = await subscribeSample({ series: snapshot.series, snapshot, after }, signal, frame => {
        if (signal.aborted) return;
        if ('subscription' in frame) {
          if (frame.subscription.status === 'accepted') {
            if (accepted || !samePosition(after, frame.subscription.from_position)) throw new Error('Invalid replay boundary');
            accepted = true;
            update('playing');
          }
          return;
        }
        const event = frame.event;
        if (event.event === 'market.stream_gap') return;
        const next = event.payload;
        if (!accepted || event.stream_id !== after.stream_id || event.epoch !== after.epoch
          || event.sequence !== sequence + 1 || next.dataset_revision !== snapshot.dataset_revision
          || Object.entries(snapshot.series).some(([key, value]) => next.series[key as keyof typeof snapshot.series] !== value)
          || next.observed_at_ms <= observed || next.bar.time_ms !== bar.time_ms || bar.is_closed) {
          throw new Error('Invalid replay update');
        }
        sequence = event.sequence;
        observed = next.observed_at_ms;
        bar = next.bar;
        hasUpdate = true;
        update('playing');
      });
      if (result.status === 'gapped') update('gap', gapText[result.reason] ?? 'Replay continuity could not be verified.');
      else if (result.status === 'complete' && accepted && bar.is_closed) update('complete');
      else throw new Error('Replay ended before the bar closed');
    } catch {
      update('gap', 'Replay was interrupted.');
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
