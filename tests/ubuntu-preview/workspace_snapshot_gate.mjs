import { AsyncLocalStorage } from 'node:async_hooks';
import childProcess from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { EventEmitter, once } from 'node:events';
import { syncBuiltinESMExports } from 'node:module';

export const snapshotTag = 'x-scopehub-test-snapshot';
const snapshotStages = ['handled', 'written', 'drained', 'finished', 'closed'];

// Installed only in the Vite child for the indicator journey. Observe the real
// writer's input and defer delivery of its reply at the pipe boundary.
export function installSnapshotGate(endpoint, writerPath) {
  const requestContext = new AsyncLocalStorage();
  const spawn = childProcess.spawn;
  let held;

  function restoreSpawn() {
    childProcess.spawn = spawn;
    syncBuiltinESMExports();
  }

  childProcess.spawn = function (file, ...args) {
    const child = spawn.call(this, file, ...args);
    if (file !== writerPath) return child;
    restoreSpawn();
    const write = child.stdin.write;
    child.stdin.write = function (line, ...args) {
      const id = requestContext.getStore();
      if (!id) return write.call(this, line, ...args);
      const frame = JSON.parse(line);
      if (frame.type !== 'snapshot' || !Number.isSafeInteger(frame.request_id) || held) {
        throw new Error('Expected one tagged snapshot at the workspace writer');
      }

      child.stdout.pause();
      const chunks = [];
      const onReply = chunk => {
        chunks.push(chunk);
        if (!chunk.includes(10)) return;
        detach();
        const bytes = Buffer.concat(chunks);
        let reply;
        try { reply = JSON.parse(bytes.subarray(0, bytes.indexOf(10))); }
        catch { reply = {}; }
        // The product listener processes the same frame. Wait for its promise
        // continuation to release HTTP admission before notifying the test.
        setImmediate(() => notify(id, 'drained', {
          request_id: reply.request_id, ok: reply.ok, revision: reply.result?.workspace?.revision,
        }));
      };
      const detach = () => {
        child.stdout.off('data', onReply);
        child.off('close', detach);
      };
      child.stdout.on('data', onReply);
      held = { id, release: () => child.stdout.resume() };
      child.once('close', detach);
      const accepted = write.call(this, line, ...args);
      notify(id, 'written', { request_id: frame.request_id, writer_pid: child.pid });
      return accepted;
    };
    return child;
  };
  syncBuiltinESMExports();

  function notify(id, stage, details) {
    if (process.connected) process.send({ snapshotGate: { id, stage, ...details } }, () => {});
  }

  function release(id) {
    if (held && (id === undefined || held.id === id)) {
      held.release();
      held = undefined;
    }
  }

  const onMessage = message => {
    if (message?.releaseSnapshot) release(message.releaseSnapshot);
  };
  process.on('message', onMessage);
  return {
    plugin: {
      name: 'scopehub-test-snapshot-gate',
      enforce: 'pre',
      configureServer(server) {
        server.middlewares.use((request, response, next) => {
          const id = request.headers[snapshotTag];
          if (request.url !== endpoint || typeof id !== 'string') return next();
          const delivery = () => ({
            complete: request.complete, headers_sent: response.headersSent, socket_destroyed: request.socket.destroyed,
          });
          // Observe the middleware after its synchronous admission decision.
          setImmediate(() => notify(id, 'handled', delivery()));
          response.once('finish', () => notify(id, 'finished', { ...delivery(), http: response.statusCode }));
          response.once('close', () => notify(id, 'closed', {
            cancelled: !response.writableEnded, http: response.writableEnded ? response.statusCode : undefined,
          }));
          requestContext.run(id, next);
        });
      },
    },
    close() {
      if (childProcess.spawn !== spawn) restoreSpawn();
      process.off('message', onMessage);
      release();
    },
  };
}

export function observeSnapshotGate(child) {
  const events = new EventEmitter();
  const observed = new Map();
  child.on('message', ({ snapshotGate }) => {
    if (!snapshotGate) return;
    const { stage, ...observation } = snapshotGate;
    const key = `${observation.id}:${stage}`;
    observed.set(key, observation);
    events.emit(key, observation);
  });

  return function tagSnapshot() {
    const id = randomUUID();
    return {
      id,
      headers: { [snapshotTag]: id },
      stages() {
        return Object.fromEntries(snapshotStages.map(stage => [stage, observed.get(`${id}:${stage}`)]));
      },
      async wait(stage, signal = AbortSignal.timeout(3_000)) {
        signal.throwIfAborted();
        const key = `${id}:${stage}`;
        if (observed.has(key)) return observed.get(key);
        const [event] = await once(events, key, { signal });
        return event;
      },
      release() {
        if (child.connected) child.send({ releaseSnapshot: id }, () => {});
      },
    };
  };
}
