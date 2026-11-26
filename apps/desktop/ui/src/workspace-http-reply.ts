import type { WorkspaceFault11 } from '../../../../contracts/generated/protocol';

// The gateway can return a compact fault with only a code and retry flag.
type WorkspaceHttpError = Pick<WorkspaceFault11, 'retryable'> & { code: string };
type WorkspaceHttpReply = { ok: true; result: unknown } | { ok: false; error: WorkspaceHttpError };

function isHttpError(value: unknown): value is WorkspaceHttpError {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    && 'code' in value && typeof value.code === 'string' && value.code.length > 0
    && 'retryable' in value && typeof value.retryable === 'boolean';
}

export function decodeWorkspaceHttpReply(reply: unknown, responseOk: boolean): WorkspaceHttpReply {
  if (reply !== null && typeof reply === 'object' && !Array.isArray(reply) && 'ok' in reply) {
    if (responseOk && reply.ok === true && 'result' in reply && reply.result && !('error' in reply)) {
      return { ok: true, result: reply.result };
    }
    if (reply.ok === false && 'error' in reply && !('result' in reply) && isHttpError(reply.error)) {
      return { ok: false, error: reply.error };
    }
  }
  throw { code: 'OUTPUT_INVALID' };
}
