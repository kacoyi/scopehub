import { Channel, invoke } from '@tauri-apps/api/core';
import type {
  MarketBarUpdateEvent, MarketStreamGapEvent, MarketSubscriptionInput, MarketSubscriptionOutput, StreamPosition,
} from '../../../../contracts/generated/protocol';
import { invalidMarketData } from './market';
import { retryMarketAdmission } from './market-admission';
import type { RepairProgress } from './market-repair';

export type ReplayRecoveryStatus = 'reconnecting' | 'resumed';
type Recovery = { status: ReplayRecoveryStatus; position: StreamPosition };
type Progress = { subscription: MarketSubscriptionOutput }
  | { event: MarketBarUpdateEvent | MarketStreamGapEvent }
  | { recovery: Recovery }
  | { repair: RepairProgress };
type Outcome = { status: 'complete' } | { status: 'gapped'; reason: string };
type Terminal = { ok: true; result: Outcome } | { ok: false; error: unknown };
type Publish = (frame: Progress) => void;

export const samePosition = (a: StreamPosition, b: StreamPosition) =>
  a.stream_id === b.stream_id && a.epoch === b.epoch && a.sequence === b.sequence;

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function isPosition(value: unknown): value is StreamPosition {
  return isRecord(value) && Object.keys(value).length === 3
    && typeof value.stream_id === 'string' && value.stream_id.length > 0
    && typeof value.epoch === 'string' && value.epoch.length > 0
    && typeof value.sequence === 'number' && Number.isSafeInteger(value.sequence) && value.sequence >= 0;
}

function readReplayFrame(value: unknown): Progress | Terminal {
  if (!isRecord(value)) throw invalidMarketData();
  const size = Object.keys(value).length;
  if ('ok' in value) {
    if (size === 2 && value.ok === true && 'result' in value) return { ok: true, result: value.result as Outcome };
    if (size === 2 && value.ok === false && 'error' in value) return { ok: false, error: value.error };
  } else if (size === 1) {
    if ('subscription' in value) return { subscription: value.subscription as MarketSubscriptionOutput };
    if ('event' in value) return { event: value.event as MarketBarUpdateEvent | MarketStreamGapEvent };
    const recovery = value.recovery;
    if (isRecord(recovery) && Object.keys(recovery).length === 2
      && (recovery.status === 'reconnecting' || recovery.status === 'resumed') && isPosition(recovery.position)) {
      return { recovery: { status: recovery.status, position: recovery.position } };
    }
    const repair = value.repair;
    if (isRecord(repair) && Object.keys(repair).length === 4 && isPosition(repair.position)
      && Array.isArray(repair.bars) && isRecord(repair.snapshot) && isRecord(repair.quality_state)) {
      return { repair: repair as RepairProgress };
    }
  }
  throw invalidMarketData();
}

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
        let value: unknown;
        try { value = JSON.parse(line); } catch { throw invalidMarketData(); }
        const frame = readReplayFrame(value);
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
  const progress = new Channel<unknown>();
  const terminal = new Promise<Outcome>((resolve, reject) => {
    progress.onmessage = value => {
      admitted();
      if (!started && isRecord(value) && value.started === true && Object.keys(value).length === 1) {
        started = true;
        if (signal.aborted) cancel();
        return;
      }
      try {
        if (!started) throw invalidMarketData();
        const frame = readReplayFrame(value);
        if ('ok' in frame) {
          progress.onmessage = () => {};
          if (frame.ok) resolve(frame.result);
          else reject(frame.error);
        } else if (!signal.aborted) publish(frame);
      } catch (error) {
        progress.onmessage = () => {};
        cancel();
        reject(error);
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
