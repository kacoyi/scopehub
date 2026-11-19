import type {
  DecisionNoteAppendOutcomeCommand11, DecisionNoteAppendOutcomeReply11, DecisionNoteDeleteCommand11,
  DecisionNoteDeleteReply11, DecisionNoteReadCommand11, DecisionNoteReadOutput, DecisionNoteReadReply11,
  DecisionNoteSaveCommand11, DecisionNoteSaveReply11, DecisionNoteWriteScope11,
} from '../../../../contracts/generated/protocol';
import { exchangeWorkspace } from './use-local-workspace';

type WriteCommand = DecisionNoteSaveCommand11 | DecisionNoteAppendOutcomeCommand11 | DecisionNoteDeleteCommand11;
type WriteReply = DecisionNoteSaveReply11 | DecisionNoteAppendOutcomeReply11 | DecisionNoteDeleteReply11;
export type NoteIntent = WriteCommand['intent'];
export type PreparedNote = { command: WriteCommand; challengeId: string };

export function noteErrorCode(error: unknown) {
  return error && typeof error === 'object' && 'code' in error ? error.code : undefined;
}

function envelope() {
  return {
    protocol_version: '1.1', type: 'request', message_id: crypto.randomUUID(),
    request_id: crypto.randomUUID(), timestamp_ms: Date.now(),
  } as const;
}

async function send<T extends DecisionNoteReadReply11 | WriteReply>(
  command: DecisionNoteReadCommand11 | WriteCommand, signal: AbortSignal,
) {
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
