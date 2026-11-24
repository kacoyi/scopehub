import { markLimit, validMark, validTarget, type Target, type Controls, type Mark, type Drawing, type ViewBounds } from './drawing-program';
import { sources } from './workbench-composition';
import type { RenderRequest } from './drawing.worker';

type Result = { status: 'ready'; marks: Mark[]; targets: Target[] } | { status: 'rejected' | 'timeout' | 'cancelled' };

export function evaluateDrawing(program: Drawing, bounds: ViewBounds, controls: Controls, signal: AbortSignal): Promise<Result> {
  return new Promise(resolve => {
    if (signal.aborted) return resolve({ status: 'cancelled' });
    let worker: Worker;
    try {
      worker = new Worker(new URL('./drawing.worker.ts', import.meta.url), { type: 'module' });
    } catch { return resolve({ status: 'rejected' }); }
    function finish(result: Result) {
      window.clearTimeout(timer);
      signal.removeEventListener('abort', cancel);
      worker.terminate();
      resolve(result);
    }
    const cancel = () => finish({ status: 'cancelled' });
    const timer = window.setTimeout(() => finish({ status: 'timeout' }), 3000);
    signal.addEventListener('abort', cancel, { once: true });
    worker.onerror = () => finish({ status: 'rejected' });
    worker.onmessageerror = () => finish({ status: 'rejected' });
    worker.onmessage = ({ data }: MessageEvent<Partial<Result> | null>) => {
      if (data?.status === 'ready' && Array.isArray(data.marks) && data.marks.length > 0
        && data.marks.length <= markLimit && data.marks.every(validMark) && Array.isArray(data.targets) && data.targets.length <= rows.length
        && data.targets.every(t => validTarget(t, rows.length))) finish({ status: 'ready', marks: data.marks, targets: data.targets });
      else finish({ status: data?.status === 'timeout' ? 'timeout' : 'rejected' });
    };
    const rows = sources[program.source].bars.map(({ time_ms, open, high, low, close, volume }) =>
      ({ time_ms, open, high, low, close, volume }));
    worker.postMessage({ code: program.code, rows, bounds, controls } satisfies RenderRequest);
  });
}
