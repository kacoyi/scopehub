import type {
  DecisionNoteAppendOutcomeCommand11, DecisionNoteAppendOutcomeReply11, DecisionNoteDeleteCommand11,
  DecisionNoteDeleteReply11, DecisionNoteReadCommand11, DecisionNoteReadOutput, DecisionNoteReadReply11,
  DecisionNoteSaveCommand11, DecisionNoteSaveReply11, DecisionNoteWriteScope11,
  DecisionNoteBackupV1, DecisionNoteExportBackupCommand11, DecisionNoteExportBackupReply11,
  DecisionNotePreviewRestoreCommand11, DecisionNotePreviewRestoreReply11, DecisionNoteRestoreBackupCommand11,
  DecisionNoteRestoreBackupInput, DecisionNoteRestoreBackupReply11, DecisionNoteRestoreScope11, DecisionNoteRestoreSummary,
} from '../../../../contracts/generated/protocol';
import { exchangeWorkspace } from './use-local-workspace';

type WriteCommand = DecisionNoteSaveCommand11 | DecisionNoteAppendOutcomeCommand11 | DecisionNoteDeleteCommand11;
type WriteReply = DecisionNoteSaveReply11 | DecisionNoteAppendOutcomeReply11 | DecisionNoteDeleteReply11;
type BackupCommand = DecisionNoteExportBackupCommand11 | DecisionNotePreviewRestoreCommand11 | DecisionNoteRestoreBackupCommand11;
type BackupReply = DecisionNoteExportBackupReply11 | DecisionNotePreviewRestoreReply11 | DecisionNoteRestoreBackupReply11;
export type NoteIntent = WriteCommand['intent'];
export type PreparedNote = { command: WriteCommand; challengeId: string };
export type PreparedRestore = { scope: DecisionNoteRestoreScope11; stageId: string; summary: DecisionNoteRestoreSummary };
const maxBackupBytes = 512 * 1024;

export function noteErrorCode(error: unknown) {
  return error && typeof error === 'object' && 'code' in error ? error.code : undefined;
}

function envelope() {
  return {
    protocol_version: '1.1', type: 'request', message_id: crypto.randomUUID(),
    request_id: crypto.randomUUID(), timestamp_ms: Date.now(),
  } as const;
}

async function send<T extends DecisionNoteReadReply11 | WriteReply | BackupReply>(
  command: DecisionNoteReadCommand11 | WriteCommand | BackupCommand, signal: AbortSignal,
): Promise<T['outcome']> {
  const reply = await exchangeWorkspace<T>(command, signal);
  if (reply.protocol_version !== '1.1' || reply.type !== 'response' || reply.request_id !== command.request_id) {
    throw { code: 'OUTPUT_INVALID' };
  }
  if (reply.outcome?.state === 'failed') throw reply.outcome.error;
  if (reply.outcome?.operation !== command.intent.operation) throw { code: 'OUTPUT_INVALID' };
  return reply.outcome;
}

export async function readNotes(workspaceId: string, noteId: string | null, offset: number, signal: AbortSignal)
  : Promise<DecisionNoteReadOutput> {
  const response = await send<DecisionNoteReadReply11>({
    ...envelope(), scope: { workspace_id: workspaceId },
    intent: { operation: 'decisionNote.read', arguments: { note_id: noteId, offset } },
  }, signal);
  if (response.state !== 'completed' || !response.result || (response.result.note?.note_id ?? null) !== noteId) {
    throw { code: 'OUTPUT_INVALID' };
  }
  return response.result;
}

function newWrite(scope: DecisionNoteWriteScope11, intent: NoteIntent): WriteCommand {
  const command = { ...envelope(), scope, confirmation: null };
  switch (intent.operation) {
    case 'decisionNote.save': return { ...command, intent };
    case 'decisionNote.appendOutcome': return { ...command, intent };
    case 'decisionNote.delete': return { ...command, intent };
  }
}

export async function prepareNote(
  workspaceId: string, revision: number, noteId: string, intent: NoteIntent, signal: AbortSignal,
): Promise<PreparedNote> {
  const command = newWrite({
    workspace_id: workspaceId, expected_workspace_revision: revision, idempotency_key: crypto.randomUUID(),
    target: { kind: 'decision_note', object_id: noteId },
  }, intent);
  const outcome = await send<WriteReply>(command, signal);
  if (outcome.state !== 'confirmation_required' || typeof outcome.challenge_id !== 'string') {
    throw { code: 'OUTPUT_INVALID' };
  }
  return { command, challengeId: outcome.challenge_id };
}

export async function resolveNote(prepared: PreparedNote, decision: 'approve' | 'decline', signal: AbortSignal) {
  const { command, challengeId } = prepared;
  try {
    const outcome = await send<WriteReply>({
      ...command, ...envelope(), confirmation: { challenge_id: challengeId, decision },
    }, signal);
    if (decision === 'approve' && outcome.state === 'committed' && outcome.receipt?.undoable === false
        && outcome.receipt.workspace_id === command.scope.workspace_id
        && outcome.receipt.target?.kind === 'decision_note'
        && outcome.receipt.target.object_id === command.scope.target.object_id
        && outcome.receipt.committed_workspace_revision === command.scope.expected_workspace_revision + 1) return;
    throw { code: 'OUTPUT_INVALID' };
  } catch (error) {
    if (decision === 'decline' && noteErrorCode(error) === 'CANCELLED') return;
    throw error;
  }
}

export async function exportNoteBackup(workspaceId: string, signal: AbortSignal): Promise<DecisionNoteBackupV1> {
  const outcome = await send<DecisionNoteExportBackupReply11>({
    ...envelope(), scope: { workspace_id: workspaceId },
    intent: { operation: 'decisionNote.exportBackup', arguments: {} },
  }, signal);
  if (outcome.state !== 'completed' || outcome.result?.format !== 'scopehub.decision-notes' || outcome.result.version !== 1) {
    throw { code: 'OUTPUT_INVALID' };
  }
  return outcome.result;
}

export async function previewNoteRestore(file: File, workspaceId: string, revision: number, signal: AbortSignal): Promise<PreparedRestore> {
  if (file.size > maxBackupBytes) throw { code: 'RESOURCE_LIMIT' };
  let backup: DecisionNoteBackupV1;
  try {
    backup = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(await file.arrayBuffer())) as DecisionNoteBackupV1;
  } catch {
    throw { code: 'INVALID_ARGUMENT' };
  }
  const scope = { workspace_id: workspaceId, expected_workspace_revision: revision, idempotency_key: crypto.randomUUID() };
  const outcome = await send<DecisionNotePreviewRestoreReply11>({
    ...envelope(), scope, intent: { operation: 'decisionNote.previewRestore', arguments: { backup } },
  }, signal);
  if (outcome.state !== 'confirmation_required' || typeof outcome.stage_id !== 'string' || !outcome.summary) {
    throw { code: 'OUTPUT_INVALID' };
  }
  return { scope, stageId: outcome.stage_id, summary: outcome.summary };
}

export async function resolveNoteRestore(prepared: PreparedRestore, decision: DecisionNoteRestoreBackupInput['decision'], signal: AbortSignal) {
  const { scope, stageId } = prepared;
  try {
    const outcome = await send<DecisionNoteRestoreBackupReply11>({
      ...envelope(), scope, intent: { operation: 'decisionNote.restoreBackup', arguments: { stage_id: stageId, decision } },
    }, signal);
    if (decision === 'approve' && outcome.state === 'committed' && outcome.receipt?.undoable === false
        && outcome.receipt.workspace_id === scope.workspace_id && outcome.receipt.target?.kind === 'workspace'
        && outcome.receipt.target.object_id === scope.workspace_id
        && outcome.receipt.committed_workspace_revision === scope.expected_workspace_revision + 1) return;
    throw { code: 'OUTPUT_INVALID' };
  } catch (error) {
    if (decision === 'decline' && noteErrorCode(error) === 'CANCELLED') return;
    throw error;
  }
}
