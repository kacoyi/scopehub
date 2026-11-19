import { Channel, invoke } from '@tauri-apps/api/core';
import type {
  MarketBarUpdateEvent, MarketStreamGapEvent, MarketSubscriptionInput, MarketSubscriptionOutput, StreamPosition,
} from '../../../../contracts/generated/protocol';
import { invalidMarketData } from './market';
import { retryMarketAdmission } from './market-admission';

type Progress = { subscription: MarketSubscriptionOutput } | { event: MarketBarUpdateEvent | MarketStreamGapEvent };
type Outcome = { status: 'complete' } | { status: 'gapped'; reason: string };
type Terminal = { ok: true; result: Outcome } | { ok: false; error: unknown };
type Publish = (frame: Progress) => void;

export const samePosition = (a: StreamPosition, b: StreamPosition) =>
  a.stream_id === b.stream_id && a.epoch === b.epoch && a.sequence === b.sequence;

async function browserReplay(
  payload: MarketSubscriptionInput, signal: AbortSignal, publish: Publish, admitted: () => void,
): Promise<Outcome> {
  const response = await fetch('/__scopehub/market-stream', {
    method: 'POST', mode: 'same-origin', credentials: 'omit', redirect: 'error', signal,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ operation: 'market.subscribe', payload }),
  });
  if (!response.ok) {
    const reply = await response.json().catch(() => undefined);
    throw reply?.error ?? new Error('Replay transport failed');
  }
  if (!response.body || response.headers.get('content-type') !== 'application/x-ndjson') throw invalidMarketData();
  // Streaming headers mean the preview has admitted a child, even when its
  // only frame is a terminal worker error.
  admitted();

  let partial = '';
  let terminal: Terminal | undefined;
  const reader = response.body.pipeThrough(new TextDecoderStream('utf-8', { fatal: true })).getReader();
  try {
    for (;;) {
      const chunk = await reader.read();
      if (chunk.done) break;
      const lines = (partial + chunk.value).split('\n');
      partial = lines.pop() ?? '';
      for (const line of lines) {
        signal.throwIfAborted();
        if (terminal) throw invalidMarketData();
        let frame: Progress | Terminal;
        try { frame = JSON.parse(line); } catch { throw invalidMarketData(); }
        if (!frame || typeof frame !== 'object' || !('ok' in frame || 'subscription' in frame || 'event' in frame)) {
          throw invalidMarketData();
        }
        if ('ok' in frame) terminal = frame;
        else publish(frame);
      }
    }
    if (partial || !terminal) throw invalidMarketData();
    if (!terminal.ok) throw terminal.error;
    return terminal.result;
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

async function nativeReplay(
  payload: MarketSubscriptionInput, signal: AbortSignal, publish: Publish, admitted: () => void,
): Promise<Outcome> {
  const subscriptionId = crypto.randomUUID();
  let started = false;
  const cancel = () => {
    if (started) void invoke('cancel_market_subscription', { subscriptionId }).catch(() => {});
  };
  const progress = new Channel<Progress | Terminal | { started: true }>();
  const terminal = new Promise<Outcome>((resolve, reject) => {
    progress.onmessage = frame => {
      admitted();
      if ('started' in frame) {
        started = true;
        if (signal.aborted) cancel();
      } else if ('ok' in frame) {
        if (frame.ok) resolve(frame.result);
        else reject(frame.error);
      } else if (!signal.aborted) {
        try { publish(frame); } catch (error) { cancel(); reject(error); }
      }
    };
  });
  signal.addEventListener('abort', cancel, { once: true });
  try {
    const [result] = await Promise.all([
      terminal, invoke('execute_market_subscription', { subscriptionId, payload, progress }),
    ]);
    return result;
  } finally {
    signal.removeEventListener('abort', cancel);
    progress.onmessage = () => {};
  }
}

export async function subscribeSample(payload: MarketSubscriptionInput, signal: AbortSignal, publish: Publish) {
  const replay = import.meta.env.SCOPEHUB_UBUNTU_PREVIEW ? browserReplay : nativeReplay;
  return retryMarketAdmission(signal, admitted => replay(payload, signal, publish, admitted));
}
