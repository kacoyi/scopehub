import { Channel, invoke } from '@tauri-apps/api/core';
import type { InstrumentSearchInput, InstrumentSearchOutput, MarketHistoryPageInput, MarketHistoryPageOutput } from '../../../../contracts/generated/protocol';
import { isAdmissionBusy, marketAdmissionDeadline, retryMarketAdmission } from './market-admission';

export type { Bar, DatasetDescriptor, Instrument, MarketGetBarsOutput } from '../../../../contracts/generated/protocol';
type Inputs = { 'instrument.search': InstrumentSearchInput; 'market.readHistoryPage': MarketHistoryPageInput };
type Outputs = { 'instrument.search': InstrumentSearchOutput; 'market.readHistoryPage': MarketHistoryPageOutput };
type Job = {
  operation: keyof Inputs; payload: Inputs[keyof Inputs]; signal: AbortSignal;
  resolve: (value: unknown) => void; reject: (reason: unknown) => void;
};
const aborted = () => new DOMException('Selection changed', 'AbortError');

async function readOperation(
  operation: keyof Inputs, payload: Inputs[keyof Inputs], admitted: () => void, signal?: AbortSignal,
): Promise<unknown> {
  if (import.meta.env.SCOPEHUB_UBUNTU_PREVIEW) {
    const response = await fetch('/__scopehub/operation', {
      method: 'POST', mode: 'same-origin', credentials: 'omit', redirect: 'error', signal: signal ?? null,
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ operation, payload }),
    });
    if (response.ok) admitted();
    let reply;
    try { reply = await response.json(); } catch (error) {
      if (response.ok && error instanceof SyntaxError) throw invalidMarketData();
      throw error;
    }
    if (reply?.ok === false && operationCode(reply.error)) throw reply.error;
    if (!response.ok) throw new Error('Market preview transport failed');
    if (reply?.ok !== true || !reply.result) throw invalidMarketData();
    return reply.result;
  }
  return invoke('execute_operation', { operation, payload });
}

// One in-flight read plus one latest intent. The core validates every executed operation.
class HistoryReader {
  private running = false;
  private pending: Job | undefined;
  private previewReleaseUntil = 0;

  read<K extends keyof Inputs>(operation: K, payload: Inputs[K], signal: AbortSignal): Promise<Outputs[K]> {
    return new Promise((resolve, reject) => {
      if (signal.aborted) { reject(aborted()); return; }
      this.pending?.reject(aborted());
      const cancel = () => reject(aborted());
      signal.addEventListener('abort', cancel, { once: true });
      this.pending = {
        operation, payload, signal,
        resolve: value => { signal.removeEventListener('abort', cancel); resolve(value as Outputs[K]); },
        reject: reason => { signal.removeEventListener('abort', cancel); reject(reason); },
      };
      void this.drain();
    });
  }

  private async execute(job: Job): Promise<unknown> {
    // Preview disconnect cancels and reaps the read. Native invoke has no cancel handle.
    const transportSignal = import.meta.env.SCOPEHUB_UBUNTU_PREVIEW ? job.signal : undefined;
    return retryMarketAdmission(job.signal, async admitted => {
      try {
        const result = await readOperation(job.operation, job.payload, admitted, transportSignal);
        this.previewReleaseUntil = 0;
        return result;
      } catch (error) {
        // An in-flight abort can settle before the bridge closes the child. An
        // aborted retry delay or an explicit BUSY never started another child.
        if (transportSignal?.aborted && !isAdmissionBusy(error)) this.previewReleaseUntil = marketAdmissionDeadline();
        throw error;
      }
    }, this.previewReleaseUntil);
  }

  private async drain() {
    if (this.running) return;
    this.running = true;
    try {
      while (this.pending) {
        const job = this.pending;
        this.pending = undefined;
        if (job.signal.aborted) { job.reject(aborted()); continue; }
        try {
          const result = await this.execute(job);
          if (job.signal.aborted) job.reject(aborted());
          else job.resolve(result);
        } catch (error) { job.reject(job.signal.aborted ? aborted() : error); }
      }
    } finally { this.running = false; }
  }
}

export const historyReader = new HistoryReader();
export const demoProvider = 'scopehub.synthetic';
const sampleRange = { from_ms: Date.UTC(2024, 0, 1), to_ms: Date.UTC(2024, 0, 6) };
const utcDayMs = 86_400_000;
export const sampleRevisionInstrument = 'SIM.CN.FUTURE.001';
export const sampleRevisions = {
  normal: { label: 'Normal', id: 'scopehub-synthetic-v1:20241013:normal' },
  stale: { label: 'Stale observations', id: 'scopehub-synthetic-v1:20241013:stale' },
  gapped: { label: 'Missing bars', id: 'scopehub-synthetic-v1:20241013:gapped' },
} as const;
export type SampleRevision = keyof typeof sampleRevisions;

export function isSampleRevision(value: unknown): value is SampleRevision {
  return typeof value === 'string' && Object.hasOwn(sampleRevisions, value);
}

export function sampleDateStart(date: string): number | undefined {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || date.startsWith('0000-')) return undefined;
  const start = Date.parse(date + 'T00:00:00Z');
  if (Number.isFinite(start) && new Date(start).toISOString().startsWith(date + 'T')) return start;
}

function operationCode(error: unknown) {
  return error && typeof error === 'object' && 'code' in error && typeof error.code === 'string' ? error.code : undefined;
}

export function invalidMarketData() {
  return { code: 'OUTPUT_INVALID', retryable: false };
}

export type MarketFailure = {
  kind: 'worker-exited' | 'transport-unavailable' | 'invalid-data' | 'unavailable' | 'error';
  message: string;
  retryable: boolean;
};

export function describeMarketFailure(error: unknown): MarketFailure {
  const retryable = error && typeof error === 'object' && 'retryable' in error && typeof error.retryable === 'boolean'
    ? error.retryable : true;
  switch (operationCode(error)) {
    case 'WORKER_EXITED': return { kind: 'worker-exited', message: 'The market worker is disconnected.', retryable };
    case 'OUTPUT_INVALID': return { kind: 'invalid-data', message: 'The data response failed validation.', retryable: false };
    case 'DATA_UNAVAILABLE': return { kind: 'unavailable', message: 'No history is available for this instrument.', retryable: false };
    case 'TIMEOUT': return { kind: 'error', message: 'The market worker timed out.', retryable };
    case 'BUSY': return { kind: 'error', message: 'The market worker is busy.', retryable };
    case 'RESOURCE_LIMIT': return { kind: 'error', message: 'This request exceeds the data limit. Select a narrower time range.', retryable: false };
    case 'SNAPSHOT_UNAVAILABLE': return { kind: 'unavailable', message: 'The selected historical revision is unavailable.', retryable: false };
    case 'PROVIDER_NOT_CONFIGURED': return { kind: 'unavailable', message: 'The offline demo runtime is not available in this build.', retryable: false };
    case 'UNSUPPORTED_CAPABILITY': return { kind: 'error', message: 'This worker needs an update to read frozen history.', retryable: false };
    case 'PROTOCOL_MISMATCH': return { kind: 'error', message: 'The market worker uses an incompatible protocol.', retryable: false };
    case 'PERMISSION_DENIED': return { kind: 'error', message: 'Access to this source is denied.', retryable: false };
    case undefined: return { kind: 'transport-unavailable', retryable: true, message: import.meta.env.SCOPEHUB_UBUNTU_PREVIEW
      ? 'Disconnected from the local preview. Retry when the connection is restored.'
      : 'Disconnected from the desktop runtime. Open the desktop development app and retry.' };
    default: return { kind: 'error', message: 'Unable to load market data.', retryable: false };
  }
}

export type ChartHistory = Pick<MarketHistoryPageOutput, 'dataset' | 'bars' | 'snapshot' | 'quality_state'>;

async function prepareNativeReplay(payload: MarketHistoryPageInput, signal: AbortSignal): Promise<MarketHistoryPageOutput> {
  return retryMarketAdmission(signal, async admitted => {
    const preparationId = crypto.randomUUID();
    const progress = new Channel<{ started: true }>();
    let started = false;
    const cancel = () => {
      if (started) void invoke('cancel_market_preparation', { preparationId }).catch(() => {});
    };
    progress.onmessage = () => {
      admitted();
      started = true;
      if (signal.aborted) cancel();
    };
    signal.addEventListener('abort', cancel, { once: true });
    try {
      return await invoke<MarketHistoryPageOutput>('prepare_market_subscription', { preparationId, payload, progress });
    } finally {
      signal.removeEventListener('abort', cancel);
      progress.onmessage = () => {};
    }
  });
}

export async function readStreamPosition(snapshot: ChartHistory['snapshot'], signal: AbortSignal) {
  const { series, from_ms, to_ms, warmup_bars } = snapshot;
  const payload = {
    series, from_ms, to_ms, warmup_bars, snapshot, page_size: 1, include_stream_position: true,
  };
  const page = import.meta.env.SCOPEHUB_UBUNTU_PREVIEW
    ? await historyReader.read('market.readHistoryPage', payload, signal)
    : await prepareNativeReplay(payload, signal);
  signal.throwIfAborted();
  if (page?.snapshot?.content_hash !== snapshot.content_hash) throw invalidMarketData();
  const position = page.stream_position;
  if (position !== null && (!position?.stream_id || !position.epoch
    || !Number.isSafeInteger(position.sequence) || position.sequence < 0)) throw invalidMarketData();
  return position;
}

export async function readSampleHistory(
  instrument: InstrumentSearchOutput['instruments'][number], timeframe: string, revision: SampleRevision,
  date: string, signal: AbortSignal,
): Promise<ChartHistory> {
  const series = {
    instrument_id: instrument.id, provider_id: demoProvider, timeframe, price_adjustment: 'raw',
    session_scope: 'sample-sessions', calendar_id: instrument.calendar_id, quantity_unit: instrument.quantity_unit,
  };
  const start = sampleDateStart(date);
  const range = start === undefined ? sampleRange : { from_ms: start, to_ms: start + utcDayMs };
  const expectedRevision = instrument.id === sampleRevisionInstrument ? sampleRevisions[revision].id : undefined;
  const query = { series, ...range, warmup_bars: 0, ...(expectedRevision && { expected_revision: expectedRevision }) };
  let page = await historyReader.read('market.readHistoryPage', query, signal);
  if (!page) throw invalidMarketData();
  const { snapshot, dataset, quality_state, total_bars } = page;
  if (!snapshot?.series || !dataset || !Array.isArray(quality_state?.gaps)
    || quality_state.gaps.some(gap => !gap || !Number.isSafeInteger(gap.from_ms)
      || !Number.isSafeInteger(gap.to_ms) || gap.from_ms >= gap.to_ms)
    || Object.entries(series).some(([field, value]) => snapshot.series[field as keyof typeof series] !== value)
    || snapshot.from_ms !== query.from_ms || snapshot.to_ms !== query.to_ms || snapshot.warmup_bars !== 0
    || dataset.instrument_id !== instrument.id || dataset.timeframe !== timeframe || dataset.provider_id !== demoProvider
    || dataset.mode !== 'simulated' || dataset.timezone !== instrument.timezone || dataset.calendar_id !== instrument.calendar_id
    || dataset.quantity_unit !== instrument.quantity_unit || dataset.revision !== snapshot.dataset_revision
    || (expectedRevision !== undefined && dataset.revision !== expectedRevision)
    || dataset.id !== snapshot.dataset_id || quality_state.as_of_ms !== snapshot.as_of_ms
    || !Number.isSafeInteger(total_bars) || total_bars < 0 || total_bars > 100_000) throw invalidMarketData();
  const bars: ChartHistory['bars'] = [];
  for (;;) {
    if (signal.aborted) throw aborted();
    if (!Array.isArray(page?.bars)) throw invalidMarketData();
    const previous = bars.at(-1);
    const first = page.bars[0];
    if (page.offset !== bars.length || page.total_bars !== total_bars || page.snapshot?.content_hash !== snapshot.content_hash
      || bars.length + page.bars.length > total_bars || (previous && first && first.time_ms <= previous.time_ms)) throw invalidMarketData();
    for (const bar of page.bars) bars.push(bar);
    if (page.next_cursor === null) {
      if (bars.length !== total_bars) throw invalidMarketData();
      return { dataset, bars, snapshot, quality_state };
    }
    if (!page.bars.length || bars.length >= total_bars || page.next_cursor?.next_index !== bars.length
      || page.next_cursor.snapshot_hash !== snapshot.content_hash) throw invalidMarketData();
    page = await historyReader.read('market.readHistoryPage', { ...query, snapshot, cursor: page.next_cursor }, signal);
  }
}
