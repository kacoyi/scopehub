import type { CancelTaskReply11, FrozenSnapshot, PreviewReady11, TaskTerminalEvent11 } from '../../../../contracts/generated/protocol';
import { exchangeWorkspace } from './use-local-workspace';

export type Ready = {
  status: 'ready'; task_id: string; event: PreviewReady11; source_snapshot: FrozenSnapshot;
  resolved_parameters: Record<string, unknown>; warmup_from_ms: number | null; calendar_hash: string | null;
};
export type Waiting = { status: 'running'; task_id: string };
export type Terminal = { status: 'terminal'; task_id: string; event: TaskTerminalEvent11 };

const validHash = (value: string) => /^[0-9a-f]{64}$/.test(value);
const command = () => ({
  protocol_version: '1.1', type: 'request', message_id: crypto.randomUUID(),
  request_id: crypto.randomUUID(), timestamp_ms: Date.now(),
} as const);
const code = (error: unknown) => error && typeof error === 'object' && 'code' in error ? error.code : undefined;
const pause = () => new Promise<void>(resolve => setTimeout(resolve, 100));

async function submit<T>(request: Parameters<typeof exchangeWorkspace>[0], signal: AbortSignal): Promise<T> {
  for (;;) {
    try {
      return await exchangeWorkspace<T>(request, signal);
    } catch (error) {
      if (code(error) !== 'BUSY') throw error;
      await pause();
      signal.throwIfAborted();
    }
  }
}

async function cancelTask(taskId: string) {
  const request = { ...command(), operation: 'task.cancel' as const, task_id: taskId };
  const reply = await submit<CancelTaskReply11>(request, AbortSignal.timeout(15_000));
  if (reply.request_id !== request.request_id) throw new Error();
  if ('error' in reply) {
    if (reply.error.code === 'STALE_CONTEXT') return;
    throw reply.error;
  }
  if (reply.task_id !== taskId) throw new Error();
}

export { cancelTask, code, command, pause, submit, validHash };
