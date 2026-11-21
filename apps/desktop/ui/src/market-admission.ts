import runtime from '../../../../contracts/generated/runtime.json';
import { abortableDelay } from './abortable-delay';

// Native cleanup can wait for a cancel acknowledgement and then reap the worker.
// The preview bridge force-terminates after 2 s and releases its slot on close.
const cleanupBudgetMs = Math.max(2 * runtime.cancel_grace_ms, 2_000) + 500;

export function marketAdmissionDeadline() {
  return performance.now() + cleanupBudgetMs;
}

export function isAdmissionBusy(error: unknown) {
  return !!error && typeof error === 'object' && 'code' in error && error.code === 'BUSY';
}

export async function retryMarketAdmission<T>(
  signal: AbortSignal,
  attempt: (admitted: () => void) => Promise<T>,
  deadline = marketAdmissionDeadline(),
): Promise<T> {
  let admitted = false;
  for (let delay = 25; ; delay = Math.min(2 * delay, 100)) {
    signal.throwIfAborted();
    try {
      return await attempt(() => { admitted = true; });
    } catch (error) {
      signal.throwIfAborted();
      const remaining = deadline - performance.now();
      if (admitted || !isAdmissionBusy(error) || remaining <= 0) throw error;
      await abortableDelay(Math.min(delay, remaining), signal);
      if (performance.now() >= deadline) throw error;
    }
  }
}
