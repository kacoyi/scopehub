import { spawn } from 'node:child_process';
import { realpathSync, statSync } from 'node:fs';
import { isAbsolute, relative, resolve, sep } from 'node:path';
import { PreviewFailure, assertLocalJsonRequest, failureResponse, maxRequestBytes, readLocalJson } from './ubuntu-preview.mjs';

const route = '/__scopehub/workspace';
const chartId = 'main-chart';
const timeframes = ['1m', '5m', '1d'];
const mutations = ['chart.setTimeframe', 'workspace.undo'];
const startupDeadlineMs = 10_000;
const requestDeadlineMs = 15_000;
const commandDeadlineMs = 8_000;
const uuid = value => typeof value === 'string' && /^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/i.test(value);
const revision = value => Number.isSafeInteger(value) && value >= 0;
const unavailable = () => new PreviewFailure(503, 'WORKER_EXITED');
const invalidReply = () => new PreviewFailure(502, 'OUTPUT_INVALID');
const timeout = () => new PreviewFailure(504, 'TIMEOUT');

function hostProfile(server, root) {
  const {
    SCOPEHUB_WORKSPACE_PROFILE_ROOT: directory,
    SCOPEHUB_WORKSPACE_PROFILE_ID: profileId,
    SCOPEHUB_WORKSPACE_ID: workspaceId,
  } = process.env;
  if (!directory || !isAbsolute(directory) || !profileId || !workspaceId) {
    throw new Error('The Ubuntu workspace requires an absolute profile directory, profile ID and workspace ID in the launcher environment.');
  }
  let path;
  try {
    path = realpathSync(directory);
    if (!statSync(path).isDirectory()) throw new Error();
  } catch {
    throw new Error('The Ubuntu workspace profile directory must already exist.');
  }
  const servedDirectories = [root, server.config.publicDir, ...server.config.server.fs.allow].filter(Boolean);
  for (const served of servedDirectories) {
    const fromServed = relative(served, path);
    if (!fromServed || !isAbsolute(fromServed) && fromServed !== '..' && !fromServed.startsWith('..' + sep)) {
      throw new Error('The Ubuntu workspace profile must be outside the directories served by Vite.');
    }
  }
  return { path, profileId, workspaceId };
}

function validWorkspace(workspace, workspaceId) {
  return workspace?.id === workspaceId && revision(workspace.revision)
    && timeframes.includes(workspace.timeframe) && workspace.instrument_id === null && workspace.dataset === null
    && workspace.visible_range === null && Array.isArray(workspace.layers) && workspace.layers.length === 0;
}

function undoTarget(receipt) {
  return {
    operation_id: receipt.operation_id,
    committed_workspace_revision: receipt.committed_workspace_revision,
  };
}

function validUndo(undo, workspace) {
  return undo === null || !!undo && uuid(undo.operation_id)
    && undo.committed_workspace_revision === workspace.revision;
}

function requestFrame(command, workspaceId) {
  const name = command?.intent?.operation;
  if (!mutations.includes(name)) throw new PreviewFailure(403, 'PERMISSION_DENIED');
  if (command?.scope?.workspace_id !== workspaceId) throw new PreviewFailure(403, 'PERMISSION_DENIED');
  if (command.protocol_version !== '1.1' || command.type !== 'request'
      || !uuid(command.request_id) || !uuid(command.message_id)) {
    throw new PreviewFailure(400, 'INVALID_ARGUMENT');
  }
  const line = JSON.stringify(command) + '\n';
  if (Buffer.byteLength(line) > maxRequestBytes) throw new PreviewFailure(413, 'RESOURCE_LIMIT');
  return line;
}

function startWriter(root, { path, profileId, workspaceId }) {
  const child = spawn(resolve(root, 'target/debug/workspace_bridge'), [path, profileId, workspaceId], {
    cwd: root, stdio: ['pipe', 'pipe', 'inherit'],
  });
  const startup = Promise.withResolvers();
  // Producer startup can fail before a browser requests its snapshot.
  void startup.promise.catch(() => {});
  const stopped = Promise.withResolvers();
  const kill = () => { if (child.pid && child.exitCode === null && child.signalCode === null) child.kill('SIGKILL'); };
  let startupTimer;
  let killTimer;
  let failure;
  let snapshot;
  let pending;
  let remainder = Buffer.alloc(0);

  function fail(reason) {
    if (failure) return;
    failure = reason;
    clearTimeout(startupTimer);
    startup.reject(reason);
    if (pending) {
      clearTimeout(pending.timer);
      pending.reject(reason);
      pending = undefined;
    }
  }

  function stop(reason) {
    fail(reason);
    if (killTimer || child.exitCode !== null || child.signalCode !== null) return;
    child.stdin.end();
    killTimer = setTimeout(kill, 2_000);
  }

  function receive(frame) {
    if (!snapshot) {
      const state = frame?.result;
      if (frame?.ok !== true || !validWorkspace(state?.workspace, workspaceId)
          || !revision(state.selection_revision) || !validUndo(state.undo, state.workspace)) throw invalidReply();
      snapshot = state;
      clearTimeout(startupTimer);
      startup.resolve();
      return;
    }

    const job = pending;
    if (!job) throw invalidReply();
    if (frame?.ok === true) {
      const reply = frame.result;
      const outcome = reply?.outcome;
      if (reply?.type !== 'response' || reply?.protocol_version !== '1.1'
          || reply?.request_id !== job.requestId || !uuid(reply.message_id)
          || reply.message_id === job.messageId) throw invalidReply();
      if (outcome?.state === 'committed') {
        const { result, receipt } = outcome;
        if (outcome.operation !== job.operation || !validWorkspace(result, workspaceId)
            || receipt?.workspace_id !== workspaceId || receipt?.target?.kind !== 'chart'
            || receipt?.target?.object_id !== chartId || !uuid(receipt.operation_id)
            || result.revision !== receipt.committed_workspace_revision
            || typeof receipt.undoable !== 'boolean') throw invalidReply();
        // Explicit idempotent retries can return earlier committed revisions.
        if (result.revision > snapshot.workspace.revision) {
          snapshot = { ...snapshot, workspace: result, undo: receipt.undoable ? undoTarget(receipt) : null };
        }
      } else if (outcome?.state !== 'failed' || typeof outcome.error?.code !== 'string') throw invalidReply();
    } else if (frame?.ok !== false || typeof frame.error?.code !== 'string') throw invalidReply();
    clearTimeout(job.timer);
    pending = undefined;
    job.resolve(frame);
  }

  process.once('exit', kill);
  child.once('error', () => stop(unavailable()));
  child.stdin.once('error', () => stop(unavailable()));
  child.stdout.once('error', () => stop(unavailable()));
  child.stdout.once('end', () => stop(remainder.length ? invalidReply() : unavailable()));
  child.once('close', () => {
    fail(unavailable());
    clearTimeout(killTimer);
    process.removeListener('exit', kill);
    stopped.resolve();
  });
  child.stdout.on('data', chunk => {
    if (failure) return;
    let bytes = Buffer.concat([remainder, chunk]);
    let end;
    try {
      while ((end = bytes.indexOf(10)) !== -1) {
        if (end >= maxRequestBytes) throw invalidReply();
        receive(JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes.subarray(0, end))));
        bytes = bytes.subarray(end + 1);
      }
      if (bytes.length >= maxRequestBytes) throw invalidReply();
      remainder = bytes;
    } catch {
      stop(invalidReply());
    }
  });
  startupTimer = setTimeout(() => stop(timeout()), startupDeadlineMs);

  return {
    async handle(body, signal) {
      await startup.promise;
      signal.throwIfAborted();
      if (failure) throw failure;
      if (body?.type === 'snapshot') {
        if (Object.keys(body).length !== 1) throw new PreviewFailure(400, 'INVALID_ARGUMENT');
        return { ok: true, result: snapshot };
      }
      if (pending) throw new PreviewFailure(429, 'BUSY');
      const line = requestFrame(body, workspaceId);
      const reply = Promise.withResolvers();
      pending = {
        ...reply, requestId: body.request_id, messageId: body.message_id, operation: body.intent.operation,
        timer: setTimeout(() => stop(timeout()), commandDeadlineMs),
      };
      // Once written, let SQLite decide the outcome. A closed browser request
      // discards delivery, while the producer is drained without resubmission.
      child.stdin.write(line);
      return reply.promise;
    },
    async close() {
      stop(unavailable());
      await stopped.promise;
    },
  };
}

export function ubuntuWorkspace() {
  let writer;
  return {
    name: 'scopehub-ubuntu-workspace',
    configureServer(server) {
      const http = server.httpServer;
      if (!http || server.config.server.host !== '127.0.0.1' || server.config.server.https) {
        throw new Error('The Ubuntu workspace requires HTTP on 127.0.0.1.');
      }
      if (!server.config.server.fs.strict) {
        throw new Error('The Ubuntu workspace requires Vite filesystem isolation.');
      }
      const root = resolve(server.config.root, '../../..');
      const profile = hostProfile(server, root);
      // Vite prepares the next config before closing the current server.
      // Starting on listen keeps the previous producer out of the new session.
      http.once('listening', () => { writer = startWriter(root, profile); });

      let active = false;
      server.middlewares.use(async (request, response, next) => {
        if (request.url !== route) return next();
        try {
          assertLocalJsonRequest(request, http);
          if (active) throw new PreviewFailure(429, 'BUSY');
          if (!writer) throw unavailable();
        } catch (error) {
          return failureResponse(request, response, error);
        }
        active = true;
        const controller = new AbortController();
        const disconnected = () => controller.abort(new PreviewFailure(499, 'CANCELLED'));
        response.once('close', disconnected);
        const timer = setTimeout(() => {
          const reason = timeout();
          controller.abort(reason);
          failureResponse(request, response, reason);
        }, requestDeadlineMs);
        try {
          const body = await readLocalJson(request);
          const reply = await writer.handle(body, controller.signal);
          if (!controller.signal.aborted) {
            response.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
            response.end(JSON.stringify(reply));
          }
        } catch (error) {
          failureResponse(request, response, error instanceof PreviewFailure ? error : unavailable());
        } finally {
          clearTimeout(timer);
          response.removeListener('close', disconnected);
          active = false;
        }
      });
    },
    async closeServer() {
      await writer?.close();
    },
  };
}
