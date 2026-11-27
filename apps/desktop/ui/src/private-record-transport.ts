import type {
  PrivateRecordBackupV1, PrivateRecordChangeCommand11, PrivateRecordChangeInput, PrivateRecordChangeReply11,
  PrivateRecordChangeScope11, PrivateRecordChangeSummary, PrivateRecordExportCommand11, PrivateRecordExportReply11,
  PrivateRecordReadCommand11, PrivateRecordReadReply11,
} from '../../../../contracts/generated/protocol';
import { exchangeWorkspace, readWorkspaceAfterAdmission } from './use-local-workspace';

type Command = PrivateRecordReadCommand11 | PrivateRecordChangeCommand11 | PrivateRecordExportCommand11;
type Reply = PrivateRecordReadReply11 | PrivateRecordChangeReply11 | PrivateRecordExportReply11;
export type PrepareRecord = Exclude<PrivateRecordChangeInput, { action: 'resolve' }>;
export type RecordApproval = {
  scope: PrivateRecordChangeScope11;
  challenge: string;
  recordId: string | null;
  summary: PrivateRecordChangeSummary;
  action: PrepareRecord['action'];
};

export type RecordResolution = Pick<RecordApproval, 'scope' | 'challenge' | 'recordId'>;

export const maxPrivateBackupBytes = 512 * 1024;

function envelope() {
  return {
    protocol_version: '1.1', type: 'request', message_id: crypto.randomUUID(),
    request_id: crypto.randomUUID(), timestamp_ms: Date.now(),
  } as const;
}

function decodeOutcome<T extends Reply>(command: Command, reply: T): T['outcome'] {
  if (reply.protocol_version !== '1.1' || reply.type !== 'response' || reply.request_id !== command.request_id) {
    throw { code: 'OUTPUT_INVALID' };
  }
  const { outcome } = reply;
  if (outcome?.state === 'failed') throw outcome.error;
  if (outcome?.operation !== command.intent.operation) throw { code: 'OUTPUT_INVALID' };
  return outcome;
}

async function send<T extends Reply>(command: Command, signal: AbortSignal): Promise<T['outcome']> {
  return decodeOutcome(command, await exchangeWorkspace<T>(command, signal));
}

export async function readPrivateRecords(workspaceId: string, recordId: string | null, signal: AbortSignal,
  waitForAdmission = false) {
  const command: PrivateRecordReadCommand11 = {
    ...envelope(), scope: { workspace_id: workspaceId },
    intent: { operation: 'privateRecord.read', arguments: { record_id: recordId } },
  };
  const reply = await (waitForAdmission ? readWorkspaceAfterAdmission<PrivateRecordReadReply11>(command, signal)
    : exchangeWorkspace<PrivateRecordReadReply11>(command, signal));
  const outcome = decodeOutcome(command, reply);
  if (outcome.state !== 'completed' || !Number.isSafeInteger(outcome.result?.workspace_revision)
    || !Array.isArray(outcome.result.records)
    || (recordId === null ? outcome.result.record !== null
      : outcome.result.record?.record_id !== recordId || outcome.result.records.length !== 0)) {
    throw { code: 'OUTPUT_INVALID' };
  }
  return outcome.result;
}

export async function preparePrivateRecord(
  workspaceId: string, revision: number, args: PrepareRecord, signal: AbortSignal,
): Promise<RecordApproval> {
  const scope = {
    workspace_id: workspaceId, expected_workspace_revision: revision, idempotency_key: crypto.randomUUID(),
  };
  const outcome = await send<PrivateRecordChangeReply11>({
    ...envelope(), scope, intent: { operation: 'privateRecord.change', arguments: args },
  }, signal);
  if (outcome.state !== 'confirmation_required' || typeof outcome.challenge_id !== 'string' || !outcome.summary) {
    throw { code: 'OUTPUT_INVALID' };
  }
  return {
    scope, challenge: outcome.challenge_id, summary: outcome.summary,
    action: args.action, recordId: 'record_id' in args ? args.record_id : null,
  };
}

export function recordErrorCode(error: unknown) {
  return error && typeof error === 'object' && 'code' in error ? error.code : undefined;
}

export async function resolvePrivateRecord(prepared: RecordResolution, decision: 'approve' | 'decline', signal: AbortSignal) {
  const { scope, challenge, recordId } = prepared;
  try {
    const outcome = await send<PrivateRecordChangeReply11>({
      ...envelope(), scope,
      intent: { operation: 'privateRecord.change', arguments: { action: 'resolve', challenge_id: challenge, decision } },
    }, signal);
    if (decision === 'approve' && outcome.state === 'committed'
      && outcome.receipt?.workspace_id === scope.workspace_id && outcome.receipt.record_id === recordId
      && outcome.receipt.committed_workspace_revision === scope.expected_workspace_revision + 1
      && outcome.receipt.undoable === false && typeof outcome.receipt.operation_id === 'string') return outcome.receipt;
    throw { code: 'OUTPUT_INVALID' };
  } catch (error) {
    if (decision === 'decline' && ['CANCELLED', 'CONFIRMATION_REQUIRED'].includes(String(recordErrorCode(error)))) return;
    throw error;
  }
}

export async function exportPrivateRecords(workspaceId: string, signal: AbortSignal): Promise<PrivateRecordBackupV1> {
  const outcome = await send<PrivateRecordExportReply11>({
    ...envelope(), scope: { workspace_id: workspaceId },
    intent: { operation: 'privateRecord.exportBackup', arguments: {} },
  }, signal);
  if (outcome.state !== 'completed' || outcome.result?.format !== 'scopehub.private-records' || outcome.result.version !== 1) {
    throw { code: 'OUTPUT_INVALID' };
  }
  return outcome.result;
}
