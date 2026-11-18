import { invoke } from '@tauri-apps/api/core';
import type { InstrumentSearchInput, InstrumentSearchOutput, MarketGetBarsInput, MarketGetBarsOutput } from '../../../../contracts/generated/protocol';

export type { Bar, DatasetDescriptor, Instrument, MarketGetBarsOutput } from '../../../../contracts/generated/protocol';
type Inputs = { 'instrument.search': InstrumentSearchInput; 'market.getBars': MarketGetBarsInput };
type Outputs = { 'instrument.search': InstrumentSearchOutput; 'market.getBars': MarketGetBarsOutput };
type Job = {
  operation: keyof Inputs; payload: Inputs[keyof Inputs]; signal: AbortSignal;
  resolve: (value: unknown) => void; reject: (reason: unknown) => void;
};
const aborted = () => new DOMException('Selection changed', 'AbortError');

async function readOperation(operation: keyof Inputs, payload: Inputs[keyof Inputs]): Promise<unknown> {
  if (import.meta.env.SCOPEHUB_UBUNTU_PREVIEW) {
    const response = await fetch('/__scopehub/operation', {
      method: 'POST', mode: 'same-origin', credentials: 'omit', redirect: 'error',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ operation, payload }),
    });
    const reply = await response.json();
    if (!response.ok || !reply.ok) throw reply.error;
    return reply.result;
  }
  return invoke('execute_operation', { operation, payload });
}

// One in-flight read plus one latest intent. The core validates every executed operation.
class HistoryReader {
  private running = false;
  private pending: Job | undefined;

  read<K extends keyof Inputs>(operation: K, payload: Inputs[K], signal: AbortSignal): Promise<Outputs[K]> {
    return new Promise((resolve, reject) => {
      if (signal.aborted) { reject(aborted()); return; }
      this.pending?.reject(aborted());
      this.pending = { operation, payload, signal, resolve: value => resolve(value as Outputs[K]), reject };
      void this.drain();
    });
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
          const result = await readOperation(job.operation, job.payload);
          if (job.signal.aborted) job.reject(aborted());
          else job.resolve(result);
        } catch (error) { job.reject(job.signal.aborted ? aborted() : error); }
      }
    } finally { this.running = false; }
  }
}

export const historyReader = new HistoryReader();
export const demoProvider = 'scopehub.synthetic';
export const sampleRange = { from_ms: Date.UTC(2024, 0, 1), to_ms: Date.UTC(2024, 0, 6) };

export function errorText(error: unknown): string {
  const code = typeof error === 'object' && error !== null && 'code' in error ? error.code : undefined;
  switch (code) {
    case 'WORKER_EXITED': return 'The market worker is disconnected. Retry to start a new session.';
    case 'TIMEOUT': return 'The market worker timed out. Retry to start a new session.';
    case 'PROVIDER_NOT_CONFIGURED': return 'The offline demo runtime is not available in this build.';
    case 'OUTPUT_INVALID': case 'PROTOCOL_MISMATCH': return 'The data response failed validation. No chart was loaded.';
    case 'RESOURCE_LIMIT': return 'The market worker is busy. Please retry.';
    case 'DATA_UNAVAILABLE': return 'No history is available for this instrument.';
    default: return import.meta.env.SCOPEHUB_UBUNTU_PREVIEW
      ? 'Cannot reach the local preview. Retry when the connection is restored.'
      : 'Unable to load the preview. Open the desktop development app and retry.';
  }
}
