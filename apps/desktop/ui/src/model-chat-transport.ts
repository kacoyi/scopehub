import type {
  AiTextConnectionCommand11, AiTextConnectionInput, AiTextConnectionOutput, AiTextConnectionReply11,
} from '../../../../contracts/generated/protocol';
import { exchangeWorkspace, nativeTextWorkspaceId } from './use-local-workspace';

export type ModelResult<Action extends AiTextConnectionInput['action']> =
  Extract<AiTextConnectionOutput, { action: Action }>;
export type ModelConnection = NonNullable<ModelResult<'status'>['connection']>;
export type ModelRoute = ModelConnection['route'];

// SQLite allows up to three seconds at startup for a busy database. Leave time
// for the opening snapshot to finish and release the native admission slot.
const nativeControlAdmissionMs = 4000;

export function modelErrorCode(error: unknown) {
  return error && typeof error === 'object' && 'code' in error ? error.code : undefined;
}

function isRetryableBusy(error: unknown): error is { code: 'BUSY'; retryable: true } {
  return error !== null && typeof error === 'object'
    && 'code' in error && error.code === 'BUSY' && 'retryable' in error && error.retryable === true;
}

export function isModelWorkspaceBusy(error: unknown) {
  return isRetryableBusy(error) && 'preAdmission' in error && error.preAdmission === true;
}

function validRoute(route: ModelRoute | null | undefined) {
  return route && typeof route.destination === 'string' && typeof route.model === 'string'
    && route.scope === 'conversation_text'
    && (route.kind === 'ollama' && route.charge_basis === 'endpoint_operator'
      || route.kind === 'offline_mock' && route.charge_basis === 'none');
}

function validConnection(connection: ModelConnection | null | undefined) {
  return connection && typeof connection.connection_id === 'string' && validRoute(connection.route);
}

function validResult(result: AiTextConnectionOutput | undefined) {
  switch (result?.action) {
    case 'status': return result.connection === null ? result.turn_id === null
      : validConnection(result.connection) && (result.turn_id === null || typeof result.turn_id === 'string');
    case 'propose': return typeof result.proposal_id === 'string' && validRoute(result.route);
    case 'activate': return validConnection(result.connection);
    case 'send': return typeof result.turn_id === 'string';
    case 'poll': {
      const turn = result.turn;
      return typeof result.turn_id === 'string' && (turn?.state === 'running'
        || turn?.state === 'completed' && typeof turn.model === 'string' && typeof turn.text === 'string'
        || turn?.state === 'failed' && typeof turn.error?.code === 'string');
    }
    case 'disconnect':
    case 'cancel': return true;
    default: return false;
  }
}

export async function exchangeModel<Input extends AiTextConnectionInput>(
  workspaceId: string, args: Input, signal: AbortSignal, requestId = crypto.randomUUID(),
): Promise<ModelResult<Input['action']>> {
  const command: AiTextConnectionCommand11 = {
    protocol_version: '1.1', type: 'request', message_id: crypto.randomUUID(),
    request_id: requestId, timestamp_ms: Date.now(),
    scope: { workspace_id: workspaceId },
    intent: { operation: 'ai.textConnection', arguments: args },
  };
  const mayWait = nativeTextWorkspaceId !== undefined && (args.action === 'status' || args.action === 'propose');
  const deadline = performance.now() + nativeControlAdmissionMs;
  let reply: AiTextConnectionReply11;
  for (let delay = 25; ; delay = Math.min(2 * delay, 100)) {
    try {
      reply = await exchangeWorkspace<AiTextConnectionReply11>(command, signal);
      break;
    } catch (failure) {
      signal.throwIfAborted();
      if (!isRetryableBusy(failure)) throw failure;
      // An operation outcome is decoded below, outside this admission retry.
      const busy = { code: 'BUSY', retryable: true, preAdmission: true };
      const remaining = deadline - performance.now();
      if (!mayWait || remaining <= 0) throw busy;
      await new Promise<void>(resolve => window.setTimeout(resolve, Math.min(delay, remaining)));
      signal.throwIfAborted();
      if (performance.now() >= deadline) throw busy;
    }
  }
  if (reply.protocol_version !== '1.1' || reply.type !== 'response' || reply.request_id !== command.request_id) {
    throw { code: 'OUTPUT_INVALID' };
  }
  const outcome = reply.outcome;
  if (outcome?.state === 'failed') throw outcome.error;
  if (outcome?.operation !== command.intent.operation || outcome.result?.action !== args.action
    || !validResult(outcome.result)
    || outcome.result.action === 'send' && outcome.result.turn_id !== command.request_id
    || args.action === 'poll' && (outcome.result.action !== 'poll' || outcome.result.turn_id !== args.turn_id)) {
    throw { code: 'OUTPUT_INVALID' };
  }
  return outcome.result as ModelResult<Input['action']>;
}
