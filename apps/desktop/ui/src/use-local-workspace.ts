import { withReplyDeadline } from './reply-deadline';
import { useCallback, useEffect, useRef, useState } from 'react';
import { invoke, isTauri } from '@tauri-apps/api/core';
import { abortableDelay } from './abortable-delay';
import { message, t } from './i18n';
import { decodeWorkspaceHttpReply } from './workspace-http-reply';
import type {
  AiTextConnectionCommand11,
  IndicatorAcceptVersionCommand11, IndicatorCreateDraftCommand11, IndicatorRemoveCommand11,
  IndicatorRunPreviewCommand11, TaskCancelRequest11, TrustedConfirmationDecision11,
  ChartSetTimeframeCommand11, ChartSetTimeframeReply11, OperationReceipt11, Workspace,
  DecisionNoteAppendOutcomeCommand11, DecisionNoteDeleteCommand11, DecisionNoteReadCommand11, DecisionNoteSaveCommand11,
  DecisionNoteExportBackupCommand11, DecisionNotePreviewRestoreCommand11, DecisionNoteRestoreBackupCommand11,
  ChartInterval11, ReadProjectedChart11, SharedSelectionRequest11, SelectEvidenceReply11, VisibleRange,
  PrivateRecordChangeCommand11, PrivateRecordExportCommand11, PrivateRecordReadCommand11,
  NamedWorkspaceLayout11, WorkspaceApplyLayoutCommand11, WorkspaceApplyLayoutReply11, WorkspaceLayoutEdit11,
  WorkspacePreviewLayoutCommand11, WorkspacePreviewLayoutReply11, WorkspaceUndoCommand11, WorkspaceUndoReply11,
} from '../../../../contracts/generated/protocol';
import { demoProvider } from './market';

type Intent = ChartSetTimeframeCommand11['intent'] | WorkspaceUndoCommand11['intent'];
type LayoutIntent = WorkspacePreviewLayoutCommand11['intent'] | WorkspaceApplyLayoutCommand11['intent'];
type LayoutCommand = WorkspacePreviewLayoutCommand11 | WorkspaceApplyLayoutCommand11 | WorkspaceUndoCommand11;
type PreparedLayoutCommand = Pick<WorkspaceApplyLayoutCommand11, 'scope' | 'intent'>;
export type PreparedWorkspaceLayout = {
  readonly layout: NamedWorkspaceLayout11;
  readonly command: PreparedLayoutCommand;
};
type Command = Omit<ChartSetTimeframeCommand11, 'intent'> & { intent: Intent | LayoutIntent };
export type WorkspaceSourceChoice = { kind: 'none' } | { kind: 'sample'; instrument_id: string | null }
  | { kind: 'frozen'; revision_id: string };
type WorkspaceSource = Exclude<WorkspaceSourceChoice, { kind: 'frozen' }>
  | { kind: 'frozen'; revision_id: string; status: 'available' | 'revoked' | 'unavailable' };
type SourceRequest = { type: 'workspace_source'; request_id: string; expected_selection_revision: number; source: WorkspaceSourceChoice };
type SnapshotRequest = { type: 'snapshot' };
type AdmissionRead = SnapshotRequest | DecisionNoteReadCommand11 | PrivateRecordReadCommand11
  | { type: 'frozen_chart'; request_id: string; revision_id: string };
type LocalRequest = SourceRequest | AdmissionRead | Command | DecisionNoteSaveCommand11
  | AiTextConnectionCommand11
  | DecisionNoteAppendOutcomeCommand11 | DecisionNoteDeleteCommand11 | DecisionNoteExportBackupCommand11
  | DecisionNotePreviewRestoreCommand11 | DecisionNoteRestoreBackupCommand11
  | PrivateRecordChangeCommand11 | PrivateRecordExportCommand11
  | SharedSelectionRequest11 | ReadProjectedChart11
  | IndicatorCreateDraftCommand11 | IndicatorRunPreviewCommand11 | TaskCancelRequest11
  | IndicatorAcceptVersionCommand11 | IndicatorRemoveCommand11 | TrustedConfirmationDecision11
  | { type: 'indicator_accepted_layers'; request_id: string; revision_id: string }
  | { type: 'indicator_accepted_output'; request_id: string; layer_id: string }
  | { type: 'indicator_preview_status'; request_id: string; task_id: string }
  | { type: 'frozen_chart_status'; request_id: string; revision_id: string };
type UndoTarget = Pick<OperationReceipt11, 'operation_id' | 'committed_workspace_revision'>;
type CommittedRevision = Pick<OperationReceipt11, 'workspace_id' | 'committed_workspace_revision'>;
type NativeSourceWrite = { pending: boolean };
type SourceReadback = {
  request: SourceRequest;
  workspace: Workspace;
  committedRevision: number | undefined;
  owner: AbortSignal | undefined;
  nativeWrite: NativeSourceWrite | undefined;
};
type ChartEvidence = { revision_id: string; range: VisibleRange; snapshot_hash?: string } | { unavailable: true };
type Snapshot = {
  workspace: Workspace; selection_revision: number; undo: UndoTarget | null;
  chart_evidence?: ChartEvidence | null;
  source?: WorkspaceSource | null;
  layouts?: { active: NamedWorkspaceLayout11; saved: string[]; undo: UndoTarget | null } | null;
};
type View = {
  snapshot: Snapshot | undefined;
  pending: 'open' | 'save' | 'evidence' | 'source' | undefined;
  errorId: string | undefined;
  selectionNotice?: ReturnType<typeof message> | undefined;
  sourceRecovery?: boolean;
  sourceNotice?: ReturnType<typeof message> | undefined;
};

export class WorkspaceSourceError extends Error {
  readonly localizedMessage: ReturnType<typeof message>;

  constructor(english: string) {
    super(english);
    this.localizedMessage = message(english);
  }
}

export class IndicatorStatusDeliveryError extends Error {}

const desktop = isTauri();
const nativeWorkspace = desktop && window.__SCOPEHUB_DESKTOP_WORKSPACE__ === true;
const nativeModel = desktop && window.__SCOPEHUB_DESKTOP_TEXT_MODEL__ === true;
export const nativeTextWorkspaceId = nativeModel ? 'main' : undefined;
const launchCapability = !desktop && import.meta.env.SCOPEHUB_UBUNTU_WORKSPACE ? takeLaunchCapability() : undefined;
const enabled = nativeWorkspace || launchCapability !== undefined;
const chart = { kind: 'chart', object_id: 'main-chart' } as const;
const layoutTarget = { kind: 'layout', object_id: 'workspace-layout' } as const;
const admissionRetryBudgetMs = 4_000;
// The development bridge also bounds its in-flight writer command to eight seconds.
const sourceAdmissionRetryBudgetMs = 8_000;
const sourceReplyDeadlineMs = 15_000;

function takeLaunchCapability() {
  const { hash, pathname, search } = window.location;
  if (!hash.startsWith('#workspace=')) return undefined;
  const capability = /^#workspace=([a-f0-9]{64})$/.exec(hash)?.[1];
  // A fragment is local to the browser. Remove it before starting the UI and
  // keep the grant in memory for this page only.
  window.history.replaceState(window.history.state, '', pathname + search);
  if (capability) {
    // A reload after this point would open a workspace without the grant.
    const reload = document.querySelector<HTMLButtonElement>('#startup-reload');
    const hint = document.getElementById('startup-hint');
    if (reload) {
      reload.disabled = true;
      reload.hidden = true;
    }
    if (hint) hint.textContent = t('Reopen the private workspace access document to try again.');
  }
  return capability;
}

function errorCode(error: unknown) {
  return error && typeof error === 'object' && 'code' in error ? error.code : undefined;
}

function failureMessageId(error: unknown) {
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

function retryableBusy(error: unknown) {
  return error !== null && typeof error === 'object' && 'code' in error && error.code === 'BUSY'
    && 'retryable' in error && error.retryable === true;
}

function nativeWorkspaceAdmissionRefused(error: unknown) {
  if (!error || typeof error !== 'object' || !('details' in error)) return false;
  const details = error.details;
  return details !== null && typeof details === 'object' && 'workspace_admission' in details
    && details.workspace_admission === 'refused';
}

async function exchange<T>(body: LocalRequest, signal: AbortSignal, onAdmissionBusy?: () => void): Promise<T> {
  const operation = body.type === 'request' ? 'operation' in body ? body.operation : body.intent.operation : null;
  if (nativeWorkspace || nativeModel && operation === 'ai.textConnection' || desktop && body.type === 'frozen_chart_status') {
    signal.throwIfAborted();
    const requiresAdmissionProof = body.type === 'snapshot' || body.type === 'indicator_preview_status';
    const value = await invoke<T>('execute_operation', { operation, payload: body }).catch(error => {
      // Snapshot and indicator status reads can return SQLite BUSY after
      // admission, so only an explicit lock refusal permits a retry.
      // A source transaction cannot return BUSY after commit.
      if (onAdmissionBusy && retryableBusy(error)
          && (!requiresAdmissionProof || nativeWorkspaceAdmissionRefused(error))) onAdmissionBusy();
      throw error;
    });
    signal.throwIfAborted();
    return value;
  }
  return postWorkspace<T>('/__scopehub/workspace', body, signal, onAdmissionBusy);
}

export async function exchangeOpenAiCredential<T>(body: {
  action: 'status' | 'remove';
} | { action: 'replace'; secret: string }, signal: AbortSignal): Promise<T> {
  if (nativeModel) {
    signal.throwIfAborted();
    const value = await invoke<T>('configure_openai_credential', body);
    signal.throwIfAborted();
    return value;
  }
  if (!launchCapability) throw { code: 'PERMISSION_DENIED' };
  return postWorkspace<T>('/__scopehub/model-credential', body, signal);
}

async function postWorkspace<T>(path: string, body: object, signal: AbortSignal, admissionBusy?: () => void): Promise<T> {
  const response = await fetch(path, {
    method: 'POST', mode: 'same-origin', credentials: 'omit', redirect: 'error', signal,
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${launchCapability}` }, body: JSON.stringify(body),
  }).catch(error => {
    // Classify only a browser fetch failure before there is an HTTP response.
    // JSON decoding and reported protocol failures keep their own semantics.
    if ('type' in body && body.type === 'indicator_preview_status' && error instanceof TypeError && !signal.aborted) {
      throw new IndicatorStatusDeliveryError('The indicator status response did not arrive.', { cause: error });
    }
    throw error;
  });
  const rawReply: unknown = await response.json();
  signal.throwIfAborted();
  const reply = decodeWorkspaceHttpReply(rawReply, response.ok);
  if (!reply.ok) {
    if (admissionBusy && response.status === 429 && retryableBusy(reply.error)) admissionBusy();
    throw reply.error;
  }
  return reply.result as T;
}

export const exchangeWorkspace: <T>(body: LocalRequest, signal: AbortSignal) => Promise<T> = exchange;

export function readIndicatorPreviewStatus<T>(taskId: string, signal: AbortSignal, onAdmissionBusy: () => void) {
  return exchange<T>({ type: 'indicator_preview_status', request_id: crypto.randomUUID(), task_id: taskId },
    signal, onAdmissionBusy);
}

async function waitForWorkspaceReply<T>(body: LocalRequest, signal: AbortSignal,
  onAdmissionBusy?: () => void, nativeWrite?: NativeSourceWrite): Promise<T> {
  signal.throwIfAborted();
  let stopWaiting: (() => void) | undefined;
  try {
    return await new Promise<T>((resolve, reject) => {
      stopWaiting = () => reject(signal.reason);
      signal.addEventListener('abort', stopWaiting, { once: true });
      if (nativeWrite) nativeWrite.pending = true;
      // Fetch can abort its delivery wait. Native invoke can still settle later,
      // so keep both handlers attached and discard any result after the deadline.
      void exchange<T>(body, signal, () => {
        if (!signal.aborted) onAdmissionBusy?.();
      }).finally(() => {
        if (nativeWrite) nativeWrite.pending = false;
      }).then(resolve, reject);
    });
  } finally {
    if (stopWaiting) signal.removeEventListener('abort', stopWaiting);
  }
}

async function exchangeSourceStage<T>(body: SourceRequest | SnapshotRequest, signal: AbortSignal,
  onAdmissionBusy: () => void, nativeWrite?: NativeSourceWrite): Promise<T> {
  const deadline = new AbortController();
  const timer = window.setTimeout(() => {
    deadline.abort(new DOMException('The workspace reply did not arrive in time.', 'TimeoutError'));
  }, sourceReplyDeadlineMs);
  try {
    return await waitForWorkspaceReply<T>(body, AbortSignal.any([signal, deadline.signal]), onAdmissionBusy, nativeWrite);
  } finally {
    window.clearTimeout(timer);
  }
}

function validWorkspace(value: Workspace | undefined) {
  return value && typeof value.id === 'string' && Number.isSafeInteger(value.revision)
    && value.revision >= 0 && ['1m', '5m', '1d'].includes(value.timeframe);
}

export async function readWorkspaceSnapshot(signal: AbortSignal): Promise<Snapshot> {
  return validateSnapshot(await withReplyDeadline(sourceReplyDeadlineMs, deadline =>
    retryAdmission(onBusy => waitForWorkspaceReply<Snapshot>({ type: 'snapshot' }, deadline, onBusy), deadline), signal));
}

export function readFrozenRevisionStatus(revision_id: string, signal: AbortSignal) {
  return waitForWorkspaceReply<{ available: boolean; revoked?: true }>({
    type: 'frozen_chart_status', request_id: crypto.randomUUID(), revision_id,
  }, signal);
}

function validateSnapshot(snapshot: Snapshot): Snapshot {
  const evidence = snapshot.chart_evidence;
  const validEvidence = evidence == null || ('unavailable' in evidence ? evidence.unavailable === true
    : typeof evidence.revision_id === 'string' && Number.isSafeInteger(evidence.range?.from_ms)
      && Number.isSafeInteger(evidence.range?.to_ms) && evidence.range.from_ms < evidence.range.to_ms
      && (evidence.snapshot_hash === undefined || /^[a-f0-9]{64}$/.test(evidence.snapshot_hash)));
  if (!validWorkspace(snapshot.workspace) || !Number.isSafeInteger(snapshot.selection_revision)
    || snapshot.selection_revision < 0 || !validEvidence || snapshot.undo !== null && (
      typeof snapshot.undo?.operation_id !== 'string'
      || !Number.isSafeInteger(snapshot.undo.committed_workspace_revision)
      || snapshot.undo.committed_workspace_revision < 1
      || snapshot.undo.committed_workspace_revision > snapshot.workspace.revision
    )) throw { code: 'OUTPUT_INVALID' };
  return snapshot;
}

async function retryAdmission<T>(work: (onBusy: () => void) => Promise<T>, signal: AbortSignal,
  budgetMs = admissionRetryBudgetMs): Promise<T> {
  const deadline = performance.now() + budgetMs;
  for (let delayMs = 25; ; delayMs = Math.min(2 * delayMs, 100)) {
    signal.throwIfAborted();
    let admissionBusy = false;
    try {
      return await work(() => { admissionBusy = true; });
    } catch (error) {
      if (!admissionBusy) throw error;
      signal.throwIfAborted();
      const remaining = deadline - performance.now();
      if (remaining <= 0) throw error;
      await abortableDelay(Math.min(delayMs, remaining), signal);
      signal.throwIfAborted();
      if (performance.now() >= deadline) throw error;
    }
  }
}

export async function readWorkspaceSnapshotAfterAdmission(signal: AbortSignal): Promise<Snapshot> {
  return validateSnapshot(await readWorkspaceAfterAdmission<Snapshot>({ type: 'snapshot' }, signal));
}

export function readWorkspaceAfterAdmission<T>(body: AdmissionRead, signal: AbortSignal) {
  return retryAdmission(onBusy => body.type === 'frozen_chart'
    ? waitForWorkspaceReply<T>(body, signal, onBusy)
    : exchange<T>(body, signal, onBusy), signal);
}

async function readCommittedSnapshot(receipt: CommittedRevision, signal: AbortSignal): Promise<Snapshot> {
  const current = await readWorkspaceSnapshotAfterAdmission(signal);
  if (current.workspace.id !== receipt.workspace_id || current.workspace.revision < receipt.committed_workspace_revision) {
    throw { code: 'OUTPUT_INVALID' };
  }
  return current;
}

function sameSource(current: Snapshot['source'], requested: WorkspaceSourceChoice) {
  if (requested.kind === 'frozen') return current?.kind === 'frozen' && current.revision_id === requested.revision_id;
  if (requested.kind === 'sample') return current?.kind === 'sample' && current.instrument_id === requested.instrument_id;
  return current?.kind === 'none';
}

function sourceReadbackNotice(current: Snapshot, readback: SourceReadback) {
  const { request, workspace, committedRevision } = readback;
  const source = current.source;
  const validSource = source === null || source?.kind === 'none'
    || source?.kind === 'sample' && (source.instrument_id === null || typeof source.instrument_id === 'string')
    || source?.kind === 'frozen' && typeof source.revision_id === 'string'
      && ['available', 'revoked', 'unavailable'].includes(source.status);
  if (current.workspace.id !== workspace.id || current.workspace.revision < workspace.revision || !validSource
    || current.selection_revision < (committedRevision ?? request.expected_selection_revision)) throw { code: 'OUTPUT_INVALID' };
  // A matching source and revision could belong to another caller when the
  // original write reply was lost. Only an acknowledged write can close the dialog.
  if (committedRevision === undefined) {
    return new WorkspaceSourceError('The source request could not be confirmed. Review the current workspace source.');
  }
  if (current.selection_revision > committedRevision) {
    return new WorkspaceSourceError('The workspace selection changed after this request was saved. Review the current workspace source.');
  }
  if (!sameSource(source, request.source) || current.chart_evidence !== null) throw { code: 'OUTPUT_INVALID' };
  if (source?.kind === 'frozen' && source.status !== 'available') {
    return new WorkspaceSourceError('The source was selected, but this frozen chart is no longer available.');
  }
}

function sourceRejected(error: unknown) {
  switch (errorCode(error)) {
    case 'BUSY':
    case 'INVALID_ARGUMENT':
    case 'PERMISSION_DENIED':
    case 'RESOURCE_LIMIT':
    case 'SNAPSHOT_UNAVAILABLE':
    case 'STALE_CONTEXT':
    case 'UNSUPPORTED_CAPABILITY':
      return true;
    default:
      return false;
  }
}

async function selectEvidence(snapshot: Snapshot, target: ChartInterval11 | null, signal: AbortSignal): Promise<Snapshot> {
  const request: SharedSelectionRequest11 = {
    protocol_version: '1.1', type: 'request', operation: 'workspace.selectEvidence',
    message_id: crypto.randomUUID(), request_id: crypto.randomUUID(), timestamp_ms: Date.now(),
    workspace_id: snapshot.workspace.id, expected_selection_revision: snapshot.selection_revision, target,
  };
  const exchange = async () => {
    const reply = await withReplyDeadline(sourceReplyDeadlineMs, deadline =>
      waitForWorkspaceReply<SelectEvidenceReply11>(request, deadline), signal);
    if (reply.request_id !== request.request_id || reply.operation !== request.operation
      || reply.type !== 'response' || reply.protocol_version !== '1.1') throw { code: 'OUTPUT_INVALID' };
    if ('error' in reply) throw reply.error;
    return reply.selection;
  };
  const selection = await exchange().catch(async error => {
    // BUSY rejects admission or rolls back before commit. A missing or invalid
    // reply may follow a commit and still requires reloading workspace state.
    if (errorCode(error) !== 'BUSY') throw error;
    await abortableDelay(150, signal);
    signal.throwIfAborted();
    return exchange();
  });
  if (selection.workspace_id !== snapshot.workspace.id || selection.selection_revision !== snapshot.selection_revision + 1
    || (target === null ? selection.target !== null : selection.target?.kind !== 'chart_interval'
      || selection.target.snapshot.content_hash !== target.snapshot.content_hash
      || selection.target.snapshot.dataset_id !== target.snapshot.dataset_id
      || selection.target.snapshot.dataset_revision !== target.snapshot.dataset_revision
      || selection.target.range.from_ms !== target.range.from_ms || selection.target.range.to_ms !== target.range.to_ms)) {
    throw { code: 'OUTPUT_INVALID' };
  }
  return {
    ...snapshot, selection_revision: selection.selection_revision,
    chart_evidence: target && {
      revision_id: target.snapshot.dataset_id, range: target.range,
      ...(target.snapshot.series.provider_id === demoProvider && { snapshot_hash: target.snapshot.content_hash }),
    },
  };
}

function workspaceEnvelope() {
  return {
    protocol_version: '1.1',
    type: 'request',
    message_id: crypto.randomUUID(),
    timestamp_ms: Date.now(),
    request_id: crypto.randomUUID(),
    task_id: null,
  } as const;
}

function workspaceCommand<I extends Intent | LayoutIntent>(
  snapshot: Snapshot, intent: I, target: typeof chart | typeof layoutTarget, expectedRevision: number,
) {
  return {
    ...workspaceEnvelope(),
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
  const sourceActive = useRef(false);
  const sourceReadback = useRef<SourceReadback | null>(null);
  const mounted = useRef(false);
  const focus = useRef({ generation: 0, pending: false });
  const [focusView, setFocusView] = useState(focus.current);
  const updateFocus = useCallback((status: typeof focus.current) => {
    focus.current = status;
    setFocusView(status);
  }, []);
  const hasConfirmedFocus = useCallback((generation: number) =>
    focus.current.generation === generation && !focus.current.pending, []);
  const queuedRecovery = useRef<{ revision: number; start: () => void } | null>(null);
  const queuedAccess = useRef<{ revisions: Set<string>; start?: () => void }>({ revisions: new Set() });
  const unconfirmedAccess = useRef(new Set<string>());
  const [unconfirmedRevisions, setUnconfirmedRevisions] = useState<ReadonlySet<string>>(unconfirmedAccess.current);
  const [view, setView] = useState<View>({ snapshot: undefined, pending: 'open', errorId: undefined });
  const showSnapshot = useCallback((snapshot: Snapshot, readback?: Pick<View, 'sourceNotice'>) => {
    // A write's readback can settle its receipt without settling the focus
    // requirement. Only the latest ordinary snapshot clears that requirement.
    const confirmed = !focus.current.pending;
    setView(previous => ({
      snapshot: confirmed ? snapshot : previous.snapshot,
      pending: undefined, errorId: undefined,
      sourceNotice: readback ? readback.sourceNotice : previous.sourceNotice,
    }));
  }, []);
  const release = useCallback((controller: AbortController) => {
    if (active.current !== controller) return;
    active.current = null;
    if (sourceReadback.current) return;
    const next = queuedRecovery.current;
    queuedRecovery.current = null;
    if (next) next.start();
    else {
      const { start } = queuedAccess.current;
      delete queuedAccess.current.start;
      start?.();
    }
  }, []);
  const run = useCallback((work: (signal: AbortSignal) => Promise<Snapshot>, pending: View['pending']) => {
    if (active.current || sourceReadback.current) return;
    const controller = new AbortController();
    active.current = controller;
    const checkedAccess = pending === 'open' ? [...unconfirmedAccess.current] : [];
    const focusAtRead = work === readWorkspaceSnapshot ? focus.current : undefined;
    const superseded = () => focusAtRead !== undefined && focusAtRead.generation !== focus.current.generation;
    setView(previous => ({ ...previous, pending, errorId: undefined, selectionNotice: undefined }));
    return work(controller.signal).then(snapshot => {
      if (controller.signal.aborted || superseded()) return false;
      if (focusAtRead?.pending) updateFocus({ ...focusAtRead, pending: false });
      if (checkedAccess.length && !focus.current.pending) {
        const next = new Set(unconfirmedAccess.current);
        for (const id of checkedAccess) {
          if (!queuedAccess.current.revisions.has(id)) next.delete(id);
        }
        unconfirmedAccess.current = next;
        setUnconfirmedRevisions(next);
      }
      showSnapshot(snapshot);
      return true;
    }, error => {
      if (!controller.signal.aborted && !superseded()) {
        const selectionBusy = pending === 'evidence' && errorCode(error) === 'BUSY';
        setView(previous => ({
          ...previous, pending: undefined,
          errorId: selectionBusy ? undefined : failureMessageId(error),
          selectionNotice: selectionBusy ? message('The workspace is busy. Try selecting again.') : undefined,
        }));
      }
      return false;
    }).finally(() => release(controller));
  }, [release, showSnapshot, updateFocus]);
  const queueSnapshot = useCallback(() => {
    const entry = queuedAccess.current;
    const start = () => {
      entry.revisions.clear();
      void run(readWorkspaceSnapshot, 'open');
    };
    // Focus and access checks share one trailing slot behind the active
    // operation, including a source write or a readback awaiting recovery.
    if (active.current || sourceReadback.current) entry.start = start;
    else start();
  }, [run]);
  const refreshPublishedAccess = useCallback((id: string) => {
    if (!mounted.current || queuedAccess.current.revisions.has(id)) return;
    queuedAccess.current.revisions.add(id);
    unconfirmedAccess.current = new Set(unconfirmedAccess.current).add(id);
    setUnconfirmedRevisions(unconfirmedAccess.current);
    queueSnapshot();
  }, [queueSnapshot]);
  const canCheckRevision = useCallback(() => !sourceActive.current, []);
  const runSource = useCallback(async (work: (signal: AbortSignal) => Promise<void>) => {
    if (active.current) throw { code: 'BUSY' };
    const controller = new AbortController();
    active.current = controller;
    sourceActive.current = true;
    setView(previous => ({ ...previous, pending: 'source', errorId: undefined, selectionNotice: undefined }));
    try {
      await work(controller.signal);
    } finally {
      if (active.current === controller) {
        sourceActive.current = false;
        setView(previous => ({ ...previous, pending: undefined }));
        release(controller);
      }
    }
  }, [release]);
  const readSource = useCallback(async (signal: AbortSignal) => {
    const readback = sourceReadback.current;
    if (!readback) return;
    let current: Snapshot;
    let notice: WorkspaceSourceError | undefined;
    try {
      let nativeWritePending = false;
      current = validateSnapshot(await retryAdmission(onBusy => {
        nativeWritePending = readback.nativeWrite?.pending === true;
        return exchangeSourceStage<Snapshot>({ type: 'snapshot' }, signal, onBusy);
      }, signal));
      // SQLite may have answered before an outstanding invoke reached the writer.
      // A newer selection fences the old CAS; the original revision does not.
      if (nativeWritePending && current.selection_revision === readback.request.expected_selection_revision) {
        throw { code: 'BUSY' };
      }
      notice = sourceReadbackNotice(current, readback);
    } catch {
      signal.throwIfAborted();
      const error = new WorkspaceSourceError(readback.committedRevision === undefined
        ? 'The source request could not be confirmed. Reload the workspace state before changing the source again.'
        : 'The source was saved. Reload the workspace state to confirm the current selection.');
      setView(previous => ({ ...previous, errorId: error.message, sourceRecovery: true }));
      throw error;
    }
    sourceReadback.current = null;
    showSnapshot(current, { sourceNotice: notice?.localizedMessage });
    if (notice) throw notice;
  }, [showSnapshot]);
  const recoverSource = useCallback(() => runSource(readSource), [readSource, runSource]);
  const reload = useCallback(() => {
    if (sourceReadback.current) void recoverSource().catch(() => {});
    else void run(readWorkspaceSnapshot, 'open');
  }, [recoverSource, run]);
  useEffect(() => {
    if (!enabled) return;
    mounted.current = true;
    const requireFocusConfirmation = () => {
      if (document.visibilityState !== 'visible') return;
      updateFocus({ generation: focus.current.generation + 1, pending: true });
      queueSnapshot();
    };
    window.addEventListener('focus', requireFocusConfirmation);
    document.addEventListener('visibilitychange', requireFocusConfirmation);
    reload();
    return () => {
      window.removeEventListener('focus', requireFocusConfirmation);
      document.removeEventListener('visibilitychange', requireFocusConfirmation);
      mounted.current = false;
      queuedRecovery.current = null;
      queuedAccess.current = { revisions: new Set() };
      unconfirmedAccess.current = new Set();
      sourceActive.current = false;
      sourceReadback.current = null;
      active.current?.abort();
      active.current = null;
    };
  }, [queueSnapshot, reload, updateFocus]);

  if (!enabled) return undefined;
  const { snapshot } = view;
  function refreshAfterIndicatorCommit(receipt: CommittedRevision) {
    if (!mounted.current) return;
    const revision = receipt?.committed_workspace_revision;
    if (!snapshot || receipt?.workspace_id !== snapshot.workspace.id || !Number.isSafeInteger(revision) || revision < 1) {
      throw { code: 'OUTPUT_INVALID' };
    }
    const start = () => run(signal => readCommittedSnapshot(receipt, signal), 'open');
    if (!active.current && !sourceReadback.current) start();
    else if (!queuedRecovery.current || revision > queuedRecovery.current.revision) {
      queuedRecovery.current = { revision, start };
    }
  }

  const submit = (intent: Intent) => {
    if (snapshot && !view.errorId) run(signal => mutate(snapshot, intent, signal), 'save');
  };
  async function editLayout(request: LayoutCommand) {
    if (!snapshot || snapshot.workspace.id !== request.scope.workspace_id) throw { code: 'STALE_CONTEXT' };
    if (active.current || sourceReadback.current) throw { code: 'BUSY' };
    const controller = new AbortController();
    const signal = controller.signal;
    active.current = controller;
    setView(previous => ({ ...previous, pending: 'save', errorId: undefined }));
    try {
      const reply = await exchangeWorkspace<WorkspacePreviewLayoutReply11 | WorkspaceApplyLayoutReply11 | WorkspaceUndoReply11>(request, signal);
      const outcome = reply?.outcome;
      if (reply?.protocol_version !== '1.1' || reply.type !== 'response' || reply.request_id !== request.request_id
        || typeof reply.message_id !== 'string' || !outcome) throw { code: 'OUTPUT_INVALID' };
      if (outcome.state === 'failed') throw outcome.error;
      if (!('operation' in outcome) || outcome.operation !== request.intent.operation) throw { code: 'OUTPUT_INVALID' };
      if (outcome.state === 'previewed') return outcome.result;
      const { workspace_id, expected_workspace_revision } = request.scope;
      if (outcome.state !== 'committed' || typeof outcome.receipt?.operation_id !== 'string'
        || outcome.receipt.target?.kind !== layoutTarget.kind || outcome.receipt.target.object_id !== layoutTarget.object_id
        || outcome.receipt.workspace_id !== workspace_id
        || outcome.receipt.committed_workspace_revision !== expected_workspace_revision + 1) throw { code: 'OUTPUT_INVALID' };
      const current = await readWorkspaceSnapshot(signal);
      if (current.workspace.id !== workspace_id || current.workspace.revision < outcome.receipt.committed_workspace_revision) {
        throw { code: 'OUTPUT_INVALID' };
      }
      if (!signal.aborted) showSnapshot(current);
      return undefined;
    } finally {
      if (active.current === controller) {
        setView(previous => ({ ...previous, pending: undefined }));
        release(controller);
      }
    }
  }
  return {
    ...view, reload, refreshAfterIndicatorCommit, canCheckRevision, recoverSource, refreshPublishedAccess, unconfirmedRevisions,
    focus: focusView, hasConfirmedFocus,
    error: view.errorId && t(view.errorId),
    selectionNotice: view.selectionNotice && t(view.selectionNotice),
    sourceNotice: view.sourceNotice && t(view.sourceNotice),
    ownsSourceRecovery: (signal: AbortSignal) => sourceReadback.current?.owner === signal,
    async chooseSource(source: WorkspaceSourceChoice, cancelAdmission?: AbortSignal) {
      if (!snapshot) throw { code: 'STALE_CONTEXT' };
      if (sourceReadback.current) throw { code: 'STALE_CONTEXT' };
      const request: SourceRequest = {
        type: 'workspace_source', request_id: crypto.randomUUID(),
        expected_selection_revision: snapshot.selection_revision, source,
      };
      await runSource(async signal => {
        let committedRevision: number | undefined;
        const nativeWrite = nativeWorkspace ? { pending: false } : undefined;
        try {
          // Closing the dialog cancels waiting for admission. An issued request
          // still settles so an accepted write can be read back and displayed.
          const retrySignal = cancelAdmission ? AbortSignal.any([signal, cancelAdmission]) : signal;
          const result = await retryAdmission(onBusy =>
            exchangeSourceStage<{ selection_revision: number }>(request, signal, onBusy, nativeWrite),
          retrySignal, sourceAdmissionRetryBudgetMs);
          if (result?.selection_revision === request.expected_selection_revision + 1) {
            committedRevision = result.selection_revision;
          }
        } catch (error) {
          signal.throwIfAborted();
          if (cancelAdmission?.aborted && error === cancelAdmission.reason) throw error;
          if (sourceRejected(error)) {
            setView(previous => ({ ...previous, errorId: failureMessageId(error) }));
            throw error;
          }
        }
        sourceReadback.current = { request, workspace: snapshot.workspace, committedRevision, owner: cancelAdmission, nativeWrite };
        await readSource(signal);
      });
    },
    async previewLayout(edit: WorkspaceLayoutEdit11, revision: number): Promise<PreparedWorkspaceLayout> {
      if (!snapshot) throw { code: 'STALE_CONTEXT' };
      const preparedEdit = structuredClone(edit);
      const request = workspaceCommand(snapshot, {
        operation: 'workspace.previewLayout', arguments: { edit: preparedEdit },
      }, layoutTarget, revision);
      const result = await editLayout(request);
      if (!result?.layout || typeof result.preview_token !== 'string') throw { code: 'OUTPUT_INVALID' };
      // Keep the original scope and idempotency key if Apply has to recover a lost receipt.
      return {
        layout: result.layout,
        command: {
          scope: { ...request.scope, idempotency_key: crypto.randomUUID() },
          intent: {
            operation: 'workspace.applyLayout', arguments: { edit: preparedEdit, preview_token: result.preview_token },
          },
        },
      };
    },
    async applyLayout(command: PreparedLayoutCommand) {
      await editLayout({ ...workspaceEnvelope(), ...command });
    },
    async undoLayout() {
      const target = snapshot?.layouts?.undo;
      if (!target) throw { code: 'STALE_CONTEXT' };
      await editLayout(workspaceCommand(snapshot, {
        operation: 'workspace.undo', arguments: { operation_id: target.operation_id },
      }, layoutTarget, snapshot.workspace.revision));
    },
    async chooseEvidence(target: ChartInterval11 | null) {
      return Boolean(snapshot && !view.errorId && await run(signal => selectEvidence(snapshot, target, signal), 'evidence'));
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
