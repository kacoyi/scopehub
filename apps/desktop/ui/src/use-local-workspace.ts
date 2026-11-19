import { useCallback, useEffect, useRef, useState } from 'react';
import type {
  ChartSetTimeframeCommand11, ChartSetTimeframeReply11, OperationReceipt11, Workspace,
  WorkspaceUndoCommand11, WorkspaceUndoReply11,
} from '../../../../contracts/generated/protocol';

type Intent = ChartSetTimeframeCommand11['intent'] | WorkspaceUndoCommand11['intent'];
type UndoTarget = Pick<OperationReceipt11, 'operation_id' | 'committed_workspace_revision'>;
type Snapshot = { workspace: Workspace; selection_revision: number; undo: UndoTarget | null };
type View = {
  snapshot: Snapshot | undefined;
  pending: 'open' | 'save' | undefined;
  error: string | undefined;
};

const enabled = import.meta.env.SCOPEHUB_UBUNTU_WORKSPACE;
const chart = { kind: 'chart', object_id: 'main-chart' } as const;

function errorCode(error: unknown) {
  return error && typeof error === 'object' && 'code' in error ? error.code : undefined;
}

function describeFailure(error: unknown) {
  switch (errorCode(error)) {
    case 'STALE_CONTEXT': return 'The workspace changed. Reload its current state.';
    case 'BUSY': return 'The local workspace is busy.';
    case 'PERMISSION_DENIED': return 'This workspace action is no longer authorized.';
    case 'OUTPUT_INVALID': return 'The workspace reply could not be verified.';
    default: return 'The local workspace is unavailable.';
  }
}

async function exchange<T>(body: object, signal: AbortSignal): Promise<T> {
  const response = await fetch('/__scopehub/workspace', {
    method: 'POST', mode: 'same-origin', credentials: 'omit', redirect: 'error', signal,
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const reply = await response.json();
  if (reply?.ok === false && errorCode(reply.error)) throw reply.error;
  if (!response.ok || reply?.ok !== true || !reply.result) throw { code: 'OUTPUT_INVALID' };
  return reply.result as T;
}

function validWorkspace(value: Workspace | undefined) {
  return value && typeof value.id === 'string' && Number.isSafeInteger(value.revision)
    && value.revision >= 0 && ['1m', '5m', '1d'].includes(value.timeframe);
}

async function restore(signal: AbortSignal): Promise<Snapshot> {
  const snapshot = await exchange<Snapshot>({ type: 'snapshot' }, signal);
  if (!validWorkspace(snapshot.workspace) || !Number.isSafeInteger(snapshot.selection_revision)
    || snapshot.selection_revision < 0 || snapshot.undo !== null && (
      typeof snapshot.undo?.operation_id !== 'string'
      || snapshot.undo.committed_workspace_revision !== snapshot.workspace.revision
    )) throw { code: 'OUTPUT_INVALID' };
  return snapshot;
}

async function mutate(snapshot: Snapshot, intent: Intent, signal: AbortSignal): Promise<Snapshot> {
  const request = {
    protocol_version: '1.1',
    type: 'request',
    message_id: crypto.randomUUID(),
    timestamp_ms: Date.now(),
    request_id: crypto.randomUUID(),
    task_id: null,
    scope: {
      workspace_id: snapshot.workspace.id,
      target: chart,
      expected_workspace_revision: snapshot.workspace.revision,
      expected_selection_revision: snapshot.selection_revision,
      idempotency_key: crypto.randomUUID(),
    },
    intent,
  } as const;
  const reply = await exchange<ChartSetTimeframeReply11 | WorkspaceUndoReply11>(request, signal);
  if (reply.protocol_version !== '1.1' || reply.type !== 'response'
    || reply.request_id !== request.request_id || typeof reply.message_id !== 'string') {
    throw { code: 'OUTPUT_INVALID' };
  }
  const outcome = reply.outcome;
  if (outcome?.state === 'failed') throw outcome.error;
  if (outcome?.state !== 'committed' || outcome.operation !== intent.operation
    || !validWorkspace(outcome.result) || outcome.result.id !== snapshot.workspace.id
    || outcome.result.revision !== snapshot.workspace.revision + 1
    || outcome.receipt?.committed_workspace_revision !== outcome.result.revision
    || typeof outcome.receipt.operation_id !== 'string') throw { code: 'OUTPUT_INVALID' };
  const { receipt } = outcome;
  return {
    workspace: outcome.result,
    selection_revision: snapshot.selection_revision,
    undo: receipt.undoable ? {
      operation_id: receipt.operation_id, committed_workspace_revision: receipt.committed_workspace_revision,
    } : null,
  };
}

export function useLocalWorkspace() {
  const active = useRef<AbortController | null>(null);
  const [view, setView] = useState<View>({ snapshot: undefined, pending: 'open', error: undefined });
  const run = useCallback((work: (signal: AbortSignal) => Promise<Snapshot>, pending: View['pending']) => {
    if (active.current) return;
    const controller = new AbortController();
    active.current = controller;
    setView(previous => ({ ...previous, pending, error: undefined }));
    void work(controller.signal).then(snapshot => {
      if (!controller.signal.aborted) setView({ snapshot, pending: undefined, error: undefined });
    }, error => {
      if (!controller.signal.aborted) {
        setView(previous => ({ ...previous, pending: undefined, error: describeFailure(error) }));
      }
    }).finally(() => {
      if (active.current === controller) active.current = null;
    });
  }, []);
  const reload = useCallback(() => run(restore, 'open'), [run]);
  useEffect(() => {
    if (!enabled) return;
    reload();
    return () => { active.current?.abort(); active.current = null; };
  }, [reload]);

  if (!enabled) return undefined;
  const { snapshot } = view;
  const submit = (intent: Intent) => {
    if (snapshot && !view.error) run(signal => mutate(snapshot, intent, signal), 'save');
  };
  return {
    ...view, reload,
    chooseTimeframe(timeframe: string) {
      if (snapshot?.workspace.timeframe !== timeframe) {
        submit({ operation: 'chart.setTimeframe', arguments: { timeframe } });
      }
    },
    undoTimeframe() {
      if (snapshot?.undo) submit({ operation: 'workspace.undo', arguments: { operation_id: snapshot.undo.operation_id } });
    },
  };
}

export type LocalWorkspace = NonNullable<ReturnType<typeof useLocalWorkspace>>;
