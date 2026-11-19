import { spawn } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const route = '/__scopehub/operation';
const streamRoute = '/__scopehub/market-stream';
export const maxRequestBytes = 16 * 1024;
const requestDeadlineMs = 15_000;

export class PreviewFailure extends Error {
  constructor(status, code) {
    super(code);
    this.status = status;
    this.code = code;
  }
}

export function failureResponse(request, response, failure) {
  if (response.destroyed || response.writableEnded) return;
  response.writeHead(failure.status, {
    'Content-Type': 'application/json', 'Cache-Control': 'no-store', Connection: 'close',
  });
  response.end(JSON.stringify({ ok: false, error: { code: failure.code, retryable: ['BUSY', 'TIMEOUT', 'WORKER_EXITED'].includes(failure.code) } }), () => {
    if (!request.complete) request.destroy();
  });
}

export function assertLocalJsonRequest(request, server, maxBytes = maxRequestBytes) {
  const address = server.address();
  const host = address && `127.0.0.1:${address.port}`;
  if (address?.address !== '127.0.0.1' || request.socket.remoteAddress !== '127.0.0.1'
      || request.headers.host !== host || request.headers.origin !== `http://${host}`) {
    throw new PreviewFailure(403, 'PERMISSION_DENIED');
  }
  if (request.method !== 'POST') throw new PreviewFailure(405, 'INVALID_ARGUMENT');
  if (!/^application\/json(?:\s*;\s*charset=(?:utf-8|"utf-8"))?$/i.test(request.headers['content-type'] ?? '')
      || request.headers['content-encoding']) throw new PreviewFailure(415, 'INVALID_ARGUMENT');
  if (Number(request.headers['content-length']) > maxBytes) throw new PreviewFailure(413, 'RESOURCE_LIMIT');
}

export async function readLocalJson(request, maxBytes = maxRequestBytes, operationLimit) {
  const chunks = [];
  let size = 0;
  for await (const chunk of request.iterator({ destroyOnReturn: false })) {
    size += chunk.length;
    if (size > maxBytes) throw new PreviewFailure(413, 'RESOURCE_LIMIT');
    chunks.push(chunk);
  }
  let body;
  try {
    body = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks)));
  } catch {
    throw new PreviewFailure(400, 'INVALID_ARGUMENT');
  }
  if (operationLimit && size > operationLimit(body?.intent?.operation)) throw new PreviewFailure(413, 'RESOURCE_LIMIT');
  return body;
}

async function readOperation(request, stream) {
  const body = await readLocalJson(request);
  if (!body || typeof body.operation !== 'string' || !body.payload || typeof body.payload !== 'object'
      || Array.isArray(body.payload) || Object.keys(body).length !== 2) {
    throw new PreviewFailure(400, 'INVALID_ARGUMENT');
  }
  const operations = stream ? ['market.subscribe'] : ['instrument.search', 'market.getBars', 'market.readHistoryPage'];
  if (!operations.includes(body.operation)) {
    throw new PreviewFailure(403, 'PERMISSION_DENIED');
  }
  if (body.operation === 'market.getBars' && body.payload.provider_id !== 'scopehub.synthetic') {
    throw new PreviewFailure(403, 'PERMISSION_DENIED');
  }
  if ((stream || body.operation === 'market.readHistoryPage') && body.payload.series?.provider_id !== 'scopehub.synthetic') {
    throw new PreviewFailure(403, 'PERMISSION_DENIED');
  }
  // Encode one frame, including when a caller formatted the HTTP JSON over multiple lines.
  try {
    return JSON.stringify(body) + '\n';
  } catch {
    throw new PreviewFailure(400, 'INVALID_ARGUMENT');
  }
}

function runOperation(root, line, maxReplyBytes, signal, streamResponse) {
  signal.throwIfAborted();
  const windows = process.platform === 'win32';
  const executable = resolve(root, 'target/debug/examples', windows ? 'history_bridge.exe' : 'history_bridge');
  const command = windows ? resolve(root, '.venv/Scripts/python.exe') : executable;
  const args = windows ? ['-I', '-S', resolve(root, 'scripts/windows_job.py'), executable] : [];
  const child = spawn(command, args, {
    cwd: root, detached: !windows, windowsHide: true, stdio: ['pipe', 'pipe', 'inherit'],
  });

  return new Promise((resolveResult, reject) => {
    let size = 0;
    const chunks = [];
    let failure;
    let killTimer;
    function kill() {
      if (!child.pid) return;
      if (windows) child.kill('SIGKILL'); // The launcher owns its descendants in a Windows Job.
      else {
        try { process.kill(-child.pid, 'SIGKILL'); } catch (error) {
          if (error.code !== 'ESRCH') throw error;
        }
      }
    }
    function stop(reason) {
      if (failure) return;
      failure = reason;
      if (streamResponse) {
        // EOF gives the bridge time to cancel and reap its own worker.
        child.stdin.end();
        child.stdout.resume();
        killTimer = setTimeout(kill, 2_000);
      } else {
        kill();
      }
    }
    const abort = () => stop(signal.reason);
    signal.addEventListener('abort', abort, { once: true });
    child.once('error', () => stop(new PreviewFailure(502, 'WORKER_EXITED')));
    child.stdin.once('error', () => stop(new PreviewFailure(502, 'WORKER_EXITED')));
    child.stdout.on('data', chunk => {
      if (failure) return;
      size += chunk.length;
      if (size > maxReplyBytes) stop(new PreviewFailure(502, 'OUTPUT_INVALID'));
      else if (streamResponse) {
        if (!streamResponse.headersSent) {
          streamResponse.writeHead(200, {
            'Content-Type': 'application/x-ndjson', 'Cache-Control': 'no-store',
          });
        }
        if (!streamResponse.write(chunk)) {
          child.stdout.pause();
          streamResponse.once('drain', () => child.stdout.resume());
        }
      } else chunks.push(chunk);
    });
    child.once('close', code => {
      signal.removeEventListener('abort', abort);
      clearTimeout(killTimer);
      if (failure || code !== 0) {
        reject(failure ?? new PreviewFailure(502, 'WORKER_EXITED'));
        return;
      }
      if (streamResponse) {
        if (size) resolveResult();
        else reject(new PreviewFailure(502, 'OUTPUT_INVALID'));
        return;
      }
      try {
        const bytes = Buffer.concat(chunks);
        const reply = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
        if (reply?.ok === true && Object.hasOwn(reply, 'result')
            || reply?.ok === false && typeof reply.error?.code === 'string') resolveResult(bytes);
        else reject(new PreviewFailure(502, 'OUTPUT_INVALID'));
      } catch {
        reject(new PreviewFailure(502, 'OUTPUT_INVALID'));
      }
    });
    // Keep stdin open for a replay's lifetime. A historical request still
    // uses one response and EOF.
    if (streamResponse) child.stdin.write(line);
    else child.stdin.end(line);
  });
}

export function ubuntuPreview() {
  return {
    name: 'scopehub-ubuntu-preview',
    configureServer(server) {
      if (!server.httpServer || server.config.server.host !== '127.0.0.1' || server.config.server.https) {
        throw new Error('The Ubuntu development preview requires HTTP on 127.0.0.1.');
      }
      const root = resolve(server.config.root, '../../..');
      const limits = JSON.parse(readFileSync(resolve(root, 'contracts/generated/runtime.json'), 'utf8'));
      const active = new Map();
      const shutdown = () => {
        for (const cancel of active.values()) cancel(new PreviewFailure(503, 'WORKER_EXITED'));
      };
      const interrupt = async () => {
        shutdown();
        // Let Vite run its close hooks before exiting, including the workspace
        // writer and private handoff cleanup when that mode is active.
        try {
          await server.close();
        } finally {
          process.exitCode ??= 130;
          process.exit();
        }
      };
      process.once('exit', shutdown);
      process.once('SIGINT', interrupt);
      server.httpServer.once('close', () => {
        shutdown();
        process.removeListener('exit', shutdown);
        process.removeListener('SIGINT', interrupt);
      });
      server.middlewares.use(async (request, response, next) => {
        const stream = request.url === streamRoute;
        if (!stream && request.url !== route) return next();
        const reject = (status, code) => failureResponse(request, response, new PreviewFailure(status, code));
        try { assertLocalJsonRequest(request, server.httpServer); }
        catch (error) { return failureResponse(request, response, error); }
        if (active.has(stream)) return reject(429, 'BUSY');

        const controller = new AbortController();
        const cancel = failure => {
          controller.abort(failure);
          if (response.headersSent) response.destroy();
          else failureResponse(request, response, failure);
        };
        active.set(stream, cancel);
        const disconnected = () => cancel(new PreviewFailure(499, 'CANCELLED'));
        response.once('close', disconnected);
        const deadline = setTimeout(() => cancel(new PreviewFailure(504, 'TIMEOUT')), requestDeadlineMs);
        try {
          const line = await readOperation(request, stream);
          const bytes = await runOperation(root, line, limits.max_message_bytes, controller.signal, stream ? response : undefined);
          if (!response.destroyed && !response.writableEnded) {
            if (!stream) response.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
            response.end(bytes);
          }
        } catch (error) {
          if (response.headersSent) response.destroy();
          else failureResponse(request, response, error instanceof PreviewFailure
            ? error : new PreviewFailure(502, 'WORKER_EXITED'));
        } finally {
          clearTimeout(deadline);
          response.removeListener('close', disconnected);
          active.delete(stream);
        }
      });
    },
  };
}
