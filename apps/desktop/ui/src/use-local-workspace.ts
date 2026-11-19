import { useCallback, useEffect, useRef, useState } from 'react';
import { invoke, isTauri } from '@tauri-apps/api/core';
import type {
  ChartSetTimeframeCommand11, ChartSetTimeframeReply11, OperationReceipt11, Workspace,
  DecisionNoteAppendOutcomeCommand11, DecisionNoteDeleteCommand11, DecisionNoteReadCommand11, DecisionNoteSaveCommand11,
  DecisionNoteExportBackupCommand11, DecisionNotePreviewRestoreCommand11, DecisionNoteRestoreBackupCommand11,
  ChartInterval11, ReadProjectedChart11, SharedSelectionRequest11, SelectEvidenceReply11, VisibleRange,
  PrivateRecordChangeCommand11, PrivateRecordExportCommand11, PrivateRecordReadCommand11,
  NamedWorkspaceLayout11, WorkspaceApplyLayoutCommand11, WorkspaceApplyLayoutReply11, WorkspaceLayoutEdit11,
  WorkspacePreviewLayoutCommand11, WorkspacePreviewLayoutReply11, WorkspaceUndoCommand11, WorkspaceUndoReply11,
} from '../../../../contracts/generated/protocol';

type Intent = ChartSetTimeframeCommand11['intent'] | WorkspaceUndoCommand11['intent'];
type LayoutIntent = WorkspacePreviewLayoutCommand11['intent'] | WorkspaceApplyLayoutCommand11['intent'];
type Command = Omit<ChartSetTimeframeCommand11, 'intent'> & { intent: Intent | LayoutIntent };
export type WorkspaceSourceChoice = { kind: 'none' } | { kind: 'sample'; instrument_id: string | null }
  | { kind: 'frozen'; revision_id: string };
type WorkspaceSource = Exclude<WorkspaceSourceChoice, { kind: 'frozen' }>
  | { kind: 'frozen'; revision_id: string; status: 'available' | 'revoked' | 'unavailable' };
type SourceRequest = { type: 'workspace_source'; request_id: string; expected_selection_revision: number; source: WorkspaceSourceChoice };
type LocalRequest = SourceRequest | { type: 'snapshot' } | Command | DecisionNoteReadCommand11 | DecisionNoteSaveCommand11
  | DecisionNoteAppendOutcomeCommand11 | DecisionNoteDeleteCommand11 | DecisionNoteExportBackupCommand11
  | DecisionNotePreviewRestoreCommand11 | DecisionNoteRestoreBackupCommand11
  | PrivateRecordChangeCommand11 | PrivateRecordExportCommand11 | PrivateRecordReadCommand11
  | SharedSelectionRequest11 | ReadProjectedChart11
  | { type: 'frozen_chart' | 'frozen_chart_status'; request_id: string; revision_id: string };
type UndoTarget = Pick<OperationReceipt11, 'operation_id' | 'committed_workspace_revision'>;
type ChartEvidence = { revision_id: string; range: VisibleRange } | { unavailable: true };
type Snapshot = {
  workspace: Workspace; selection_revision: number; undo: UndoTarget | null;
  chart_evidence?: ChartEvidence | null;
  source?: WorkspaceSource | null;
  layouts?: { active: NamedWorkspaceLayout11; saved: string[]; undo: UndoTarget | null } | null;
};
type View = {
  snapshot: Snapshot | undefined;
  pending: 'open' | 'save' | 'evidence' | undefined;
  error: string | undefined;
};

const desktop = isTauri();
const nativeWorkspace = desktop && window.__SCOPEHUB_DESKTOP_WORKSPACE__ === true;
const launchCapability = !desktop && import.meta.env.SCOPEHUB_UBUNTU_WORKSPACE ? takeLaunchCapability() : undefined;
const enabled = nativeWorkspace || launchCapability !== undefined;
const chart = { kind: 'chart', object_id: 'main-chart' } as const;
const layoutTarget = { kind: 'layout', object_id: 'workspace-layout' } as const;

function takeLaunchCapability() {
  const { hash, pathname, search } = window.location;
  if (!hash.startsWith('#workspace=')) return undefined;
  // A fragment is local to the browser. Remove it before starting the UI and
  // keep the grant in memory for this page only.
  window.history.replaceState(window.history.state, '', pathname + search);
  return /^#workspace=([a-f0-9]{64})$/.exec(hash)?.[1];
}

function errorCode(error: unknown) {
  return error && typeof error === 'object' && 'code' in error ? error.code : undefined;
}

function describeFailure(error: unknown) {
  switch (errorCode(error)) {
    case 'STALE_CONTEXT': return 'The workspace changed. Reload its current state.';
    case 'BUSY': return 'The local workspace is busy.';
    case 'SNAPSHOT_UNAVAILABLE': return 'This frozen chart is no longer available.';
    case 'PERMISSION_DENIED': return nativeWorkspace ? 'This workspace action is no longer authorized.'
      : 'Open the private workspace link for this session.';
    case 'OUTPUT_INVALID': return 'The workspace reply could not be verified.';
    default: return 'The local workspace is unavailable.';
  }
}

export async function exchangeWorkspace<T>(body: LocalRequest, signal: AbortSignal): Promise<T> {
  if (nativeWorkspace || desktop && body.type === 'frozen_chart_status') {
    signal.throwIfAborted();
    const operation = body.type === 'request' ? 'operation' in body ? body.operation : body.intent.operation : null;
    const value = await invoke<T>('execute_operation', { operation, payload: body });
    signal.throwIfAborted();
    return value;
  }
  const response = await fetch('/__scopehub/workspace', {
    method: 'POST', mode: 'same-origin', credentials: 'omit', redirect: 'error', signal,
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${launchCapability}` }, body: JSON.stringify(body),
  });
  const reply = await response.json();
  signal.throwIfAborted();
  if (reply?.ok === false && errorCode(reply.error)) throw reply.error;
  if (!response.ok || reply?.ok !== true || !reply.result) throw { code: 'OUTPUT_INVALID' };
  return reply.result as T;
}

function validWorkspace(value: Workspace | undefined) {
  return value && typeof value.id === 'string' && Number.isSafeInteger(value.revision)
    && value.revision >= 0 && ['1m', '5m', '1d'].includes(value.timeframe);
}

export async function readWorkspaceSnapshot(signal: AbortSignal): Promise<Snapshot> {
  const snapshot = await exchangeWorkspace<Snapshot>({ type: 'snapshot' }, signal);
  const evidence = snapshot.chart_evidence;
  const validEvidence = evidence == null || ('unavailable' in evidence ? evidence.unavailable === true
    : typeof evidence.revision_id === 'string' && Number.isSafeInteger(evidence.range?.from_ms)
      && Number.isSafeInteger(evidence.range?.to_ms) && evidence.range.from_ms < evidence.range.to_ms);
  if (!validWorkspace(snapshot.workspace) || !Number.isSafeInteger(snapshot.selection_revision)
    || snapshot.selection_revision < 0 || !validEvidence || snapshot.undo !== null && (
      typeof snapshot.undo?.operation_id !== 'string'
      || !Number.isSafeInteger(snapshot.undo.committed_workspace_revision)
      || snapshot.undo.committed_workspace_revision < 1
      || snapshot.undo.committed_workspace_revision > snapshot.workspace.revision
    )) throw { code: 'OUTPUT_INVALID' };
  return snapshot;
}

async function selectEvidence(snapshot: Snapshot, target: ChartInterval11 | null, signal: AbortSignal): Promise<Snapshot> {
  const request: SharedSelectionRequest11 = {
    protocol_version: '1.1', type: 'request', operation: 'workspace.selectEvidence',
    message_id: crypto.randomUUID(), request_id: crypto.randomUUID(), timestamp_ms: Date.now(),
    workspace_id: snapshot.workspace.id, expected_selection_revision: snapshot.selection_revision, target,
  };
  const reply = await exchangeWorkspace<SelectEvidenceReply11>(request, signal);
  if (reply.request_id !== request.request_id || reply.operation !== request.operation
    || reply.type !== 'response' || reply.protocol_version !== '1.1') throw { code: 'OUTPUT_INVALID' };
  if ('error' in reply) throw reply.error;
  const selection = reply.selection;
  if (selection.workspace_id !== snapshot.workspace.id || selection.selection_revision !== snapshot.selection_revision + 1
    || (target === null ? selection.target !== null : selection.target?.kind !== 'chart_interval'
      || selection.target.snapshot.content_hash !== target.snapshot.content_hash
      || selection.target.snapshot.dataset_id !== target.snapshot.dataset_id
      || selection.target.range.from_ms !== target.range.from_ms || selection.target.range.to_ms !== target.range.to_ms)) {
    throw { code: 'OUTPUT_INVALID' };
  }
  return {
    ...snapshot, selection_revision: selection.selection_revision,
    chart_evidence: target && { revision_id: target.snapshot.dataset_id, range: target.range },
  };
}

function workspaceCommand<I extends Intent | WorkspacePreviewLayoutCommand11['intent'] | WorkspaceApplyLayoutCommand11['intent']>(
  snapshot: Snapshot, intent: I, target: typeof chart | typeof layoutTarget, expectedRevision: number,
) {
  return {
    protocol_version: '1.1',
    type: 'request',
    message_id: crypto.randomUUID(),
    timestamp_ms: Date.now(),
    request_id: crypto.randomUUID(),
    task_id: null,
    scope: {
      workspace_id: snapshot.workspace.id,
      target,
      expected_workspace_revision: expectedRevision,
      expected_selection_revision: snapshot.selection_revision,
      idempotency_key: crypto.randomUUID(),
    },
    intent,
  } as const;
}

async function mutate(snapshot: Snapshot, intent: Intent, signal: AbortSignal): Promise<Snapshot> {
  const request = workspaceCommand(snapshot, intent, chart, snapshot.workspace.revision);
  const reply = await exchangeWorkspace<ChartSetTimeframeReply11 | WorkspaceUndoReply11>(request, signal);
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
    ...snapshot,
    workspace: outcome.result,
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
  const reload = useCallback(() => run(readWorkspaceSnapshot, 'open'), [run]);
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
  async function editLayout<I extends WorkspacePreviewLayoutCommand11['intent'] | WorkspaceApplyLayoutCommand11['intent'] | WorkspaceUndoCommand11['intent']>(
    intent: I, revision: number | undefined,
  ) {
    if (!snapshot || revision === undefined) throw { code: 'STALE_CONTEXT' };
    if (active.current) throw { code: 'BUSY' };
    const controller = new AbortController();
    const signal = controller.signal;
    active.current = controller;
    setView(previous => ({ ...previous, pending: 'save', error: undefined }));
    try {
      const request = workspaceCommand(snapshot, intent, layoutTarget, revision);
      const reply = await exchangeWorkspace<WorkspacePreviewLayoutReply11 | WorkspaceApplyLayoutReply11 | WorkspaceUndoReply11>(request, signal);
      const outcome = reply.outcome;
      if (reply.protocol_version !== '1.1' || reply.type !== 'response' || reply.request_id !== request.request_id
        || typeof reply.message_id !== 'string') throw { code: 'OUTPUT_INVALID' };
      if (outcome.state === 'failed') throw outcome.error;
      if (!('operation' in outcome) || outcome.operation !== intent.operation) throw { code: 'OUTPUT_INVALID' };
      if (outcome.state === 'previewed') return outcome.result;
      if (outcome.state !== 'committed' || outcome.receipt.target.kind !== 'layout'
        || outcome.receipt.target.object_id !== layoutTarget.object_id || outcome.receipt.workspace_id !== snapshot.workspace.id
        || outcome.receipt.committed_workspace_revision !== revision + 1) throw { code: 'OUTPUT_INVALID' };
      const current = await readWorkspaceSnapshot(signal);
      if (!signal.aborted) setView({ snapshot: current, pending: undefined, error: undefined });
      return undefined;
    } finally {
      if (active.current === controller) {
        active.current = null;
        setView(previous => ({ ...previous, pending: undefined }));
      }
    }
  }
  return {
    ...view, reload,
    async chooseSource(source: WorkspaceSourceChoice) {
      if (!snapshot) throw { code: 'STALE_CONTEXT' };
      if (active.current) throw { code: 'BUSY' };
      const controller = new AbortController();
      active.current = controller;
      setView(previous => ({ ...previous, pending: 'evidence', error: undefined }));
      try {
        const result = await exchangeWorkspace<{ selection_revision: number }>({
          type: 'workspace_source', request_id: crypto.randomUUID(),
          expected_selection_revision: snapshot.selection_revision, source,
        }, controller.signal);
        if (result?.selection_revision !== snapshot.selection_revision + 1) throw { code: 'OUTPUT_INVALID' };
        const current = await readWorkspaceSnapshot(controller.signal);
        if (!controller.signal.aborted) setView({ snapshot: current, pending: undefined, error: undefined });
      } catch (error) {
        if (!controller.signal.aborted) setView(previous => ({ ...previous, error: describeFailure(error) }));
        throw error;
      } finally {
        if (active.current === controller) {
          active.current = null;
          setView(previous => ({ ...previous, pending: undefined }));
        }
      }
    },
    async previewLayout(edit: WorkspaceLayoutEdit11, revision: number) {
      const result = await editLayout({ operation: 'workspace.previewLayout', arguments: { edit } }, revision);
      if (!result) throw { code: 'OUTPUT_INVALID' };
      return result;
    },
    async applyLayout(edit: WorkspaceLayoutEdit11, preview_token: string, revision: number) {
      await editLayout({ operation: 'workspace.applyLayout', arguments: { edit, preview_token } }, revision);
    },
    async undoLayout() {
      const target = snapshot?.layouts?.undo;
      if (!target) throw { code: 'STALE_CONTEXT' };
      await editLayout({ operation: 'workspace.undo', arguments: { operation_id: target.operation_id } }, snapshot.workspace.revision);
    },
    chooseEvidence(target: ChartInterval11 | null) {
      if (snapshot && !view.error) run(signal => selectEvidence(snapshot, target, signal), 'evidence');
    },
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
