import type {
  AiTextChartEvidence, AiTextConnectionCommand11, AiTextConnectionInput, AiTextConnectionOutput, AiTextConnectionReply11, VisibleRange,
} from '../../../../contracts/generated/protocol';
import { exchangeOpenAiCredential, exchangeWorkspace } from './use-local-workspace';

export type ModelResult<Action extends AiTextConnectionInput['action']> =
  Extract<AiTextConnectionOutput, { action: Action }>;
export type ModelConnection = NonNullable<ModelResult<'status'>['connection']>;
export type ModelRoute = ModelConnection['route'];
export type ModelChartSelection = {
  selectionRevision: number;
  sourceReference: string;
  datasetRevision: string;
  range: VisibleRange;
};
export type OpenAiCredential = {
  configured: boolean;
  lifetime: 'session' | 'windows_credential_manager';
};

export const openAiDestination = 'https://api.openai.com/v1/responses';

export function modelErrorCode(error: unknown) {
  return error && typeof error === 'object' && 'code' in error ? error.code : undefined;
}

function validRoute(route: ModelRoute | null | undefined) {
  return route && typeof route.destination === 'string' && typeof route.model === 'string'
    && (route.kind === 'ollama' && route.charge_basis === 'endpoint_operator'
      && (route.scope === 'conversation_text' || route.scope === 'conversation_text_and_sample_closes')
      || route.kind === 'openai_responses' && route.destination === openAiDestination
        && route.charge_basis === 'openai_api_account' && route.scope === 'conversation_text'
      || route.kind === 'offline_mock' && route.charge_basis === 'none' && route.scope === 'conversation_text');
}

export async function openAiCredential(args: Parameters<typeof exchangeOpenAiCredential>[0],
  signal: AbortSignal,
): Promise<OpenAiCredential> {
  const reply = await exchangeOpenAiCredential<OpenAiCredential>(args, signal);
  if (typeof reply?.configured !== 'boolean'
    || reply.lifetime !== 'session' && reply.lifetime !== 'windows_credential_manager') throw { code: 'OUTPUT_INVALID' };
  return { configured: reply.configured, lifetime: reply.lifetime };
}

function validConnection(connection: ModelConnection | null | undefined) {
  return connection && typeof connection.connection_id === 'string' && validRoute(connection.route);
}

function validChartEvidence(evidence: AiTextChartEvidence) {
  const { range, rows } = evidence;
  return typeof evidence.source_reference === 'string' && typeof evidence.revision === 'string'
    && Number.isSafeInteger(range?.from_ms) && Number.isSafeInteger(range?.to_ms)
    && Math.abs(range.from_ms) <= 8.64e15 && Math.abs(range.to_ms) <= 8.64e15 && range.from_ms < range.to_ms
    && Array.isArray(rows) && rows.length >= 1 && rows.length <= 200
    && rows.every((row, index) => row && Number.isSafeInteger(row.time_ms)
      && row.time_ms >= range.from_ms && row.time_ms < range.to_ms && Number.isFinite(row.close)
      && (!index || row.time_ms > rows[index - 1]!.time_ms));
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
          && (turn.chart_evidence === undefined || turn.chart_evidence && validChartEvidence(turn.chart_evidence))
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
  const reply = await exchangeWorkspace<AiTextConnectionReply11>(command, signal);
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
