import { spawn } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const route = '/__scopehub/operation';
const maxRequestBytes = 16 * 1024;
const requestDeadlineMs = 15_000;

class PreviewFailure extends Error {
  constructor(status, code) {
    super(code);
    this.status = status;
    this.code = code;
  }
}

function failureResponse(request, response, failure) {
  if (response.destroyed || response.writableEnded) return;
  response.writeHead(failure.status, {
    'Content-Type': 'application/json', 'Cache-Control': 'no-store', Connection: 'close',
  });
  response.end(JSON.stringify({ ok: false, error: { code: failure.code, retryable: ['BUSY', 'TIMEOUT', 'WORKER_EXITED'].includes(failure.code) } }), () => {
    if (!request.complete) request.destroy();
  });
}

async function readOperation(request) {
  const chunks = [];
  let size = 0;
  for await (const chunk of request.iterator({ destroyOnReturn: false })) {
    size += chunk.length;
    if (size > maxRequestBytes) throw new PreviewFailure(413, 'RESOURCE_LIMIT');
    chunks.push(chunk);
  }
  let body;
  try {
    body = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks)));
  } catch {
    throw new PreviewFailure(400, 'INVALID_ARGUMENT');
  }
  if (!body || typeof body.operation !== 'string' || !body.payload || typeof body.payload !== 'object'
      || Array.isArray(body.payload) || Object.keys(body).length !== 2) {
    throw new PreviewFailure(400, 'INVALID_ARGUMENT');
  }
  if (!['instrument.search', 'market.getBars', 'market.readHistoryPage'].includes(body.operation)) {
    throw new PreviewFailure(403, 'PERMISSION_DENIED');
  }
  if (body.operation === 'market.getBars' && body.payload.provider_id !== 'scopehub.synthetic') {
    throw new PreviewFailure(403, 'PERMISSION_DENIED');
  }
  if (body.operation === 'market.readHistoryPage' && body.payload.series?.provider_id !== 'scopehub.synthetic') {
    throw new PreviewFailure(403, 'PERMISSION_DENIED');
  }
  // Encode one frame, including when a caller formatted the HTTP JSON over multiple lines.
  try {
    return JSON.stringify(body) + '\n';
  } catch {
    throw new PreviewFailure(400, 'INVALID_ARGUMENT');
  }
}

function runOperation(root, line, maxReplyBytes, signal) {
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
    function stop(reason) {
      if (failure) return;
      failure = reason;
      if (!child.pid) return;
      if (windows) child.kill('SIGKILL'); // The launcher owns its descendants in a Windows Job.
      else {
        try { process.kill(-child.pid, 'SIGKILL'); } catch (error) {
          if (error.code !== 'ESRCH') throw error;
        }
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
      else chunks.push(chunk);
    });
    child.once('close', code => {
      signal.removeEventListener('abort', abort);
      if (failure || code !== 0) {
        reject(failure ?? new PreviewFailure(502, 'WORKER_EXITED'));
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
    // One request per process. EOF makes the registry shut down and reap its worker.
    child.stdin.end(line);
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
      let cancelActive;
      const shutdown = () => cancelActive?.(new PreviewFailure(503, 'WORKER_EXITED'));
      const interrupt = () => {
        shutdown();
        if (!process.listenerCount('SIGINT')) process.kill(process.pid, 'SIGINT');
      };
      process.once('exit', shutdown);
      process.once('SIGINT', interrupt);
      server.httpServer.once('close', () => {
        shutdown();
        process.removeListener('exit', shutdown);
        process.removeListener('SIGINT', interrupt);
      });
      server.middlewares.use(async (request, response, next) => {
        if (request.url !== route) return next();
        const address = server.httpServer.address();
        const host = address && `127.0.0.1:${address.port}`;
        const reject = (status, code) => failureResponse(request, response, new PreviewFailure(status, code));
        if (address?.address !== '127.0.0.1' || request.socket.remoteAddress !== '127.0.0.1'
            || request.headers.host !== host || request.headers.origin !== `http://${host}`) {
          return reject(403, 'PERMISSION_DENIED');
        }
        if (request.method !== 'POST') return reject(405, 'INVALID_ARGUMENT');
        if (!/^application\/json(?:\s*;\s*charset=(?:utf-8|"utf-8"))?$/i.test(request.headers['content-type'] ?? '')
            || request.headers['content-encoding']) return reject(415, 'INVALID_ARGUMENT');
        if (Number(request.headers['content-length']) > maxRequestBytes) return reject(413, 'RESOURCE_LIMIT');
        if (cancelActive) return reject(429, 'BUSY');

        const controller = new AbortController();
        const cancel = failure => {
          controller.abort(failure);
          failureResponse(request, response, failure);
        };
        cancelActive = cancel;
        const disconnected = () => cancel(new PreviewFailure(499, 'CANCELLED'));
        response.once('close', disconnected);
        const deadline = setTimeout(() => cancel(new PreviewFailure(504, 'TIMEOUT')), requestDeadlineMs);
        try {
          const line = await readOperation(request);
          const bytes = await runOperation(root, line, limits.max_message_bytes, controller.signal);
          if (!response.destroyed && !response.writableEnded) {
            response.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
            response.end(bytes);
          }
        } catch (error) {
          failureResponse(request, response, error instanceof PreviewFailure
            ? error : new PreviewFailure(502, 'WORKER_EXITED'));
        } finally {
          clearTimeout(deadline);
          response.removeListener('close', disconnected);
          cancelActive = undefined;
        }
      });
    },
  };
}
