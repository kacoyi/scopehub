import { spawn } from 'node:child_process';
import { realpathSync, statSync } from 'node:fs';
import { basename, isAbsolute, relative, resolve, sep } from 'node:path';
import { PreviewFailure, assertLocalJsonRequest, failureResponse, maxRequestBytes, readLocalJson } from './ubuntu-preview.mjs';
import { issueWorkspaceAccess } from './ubuntu-workspace-access.mjs';

const route = '/__scopehub/workspace';
const rawFiles = '/@fs/';
const privateFile = /^(?:workspace\.sqlite3(?:-(?:wal|shm|journal))?|\.?workspace-access-\d+\.html(?:-[\da-f-]+)?)$/i;
const chartId = 'main-chart';
const sourceMutation = 'workspace_source';
const timeframes = ['1m', '5m', '1d'];
const layoutId = 'workspace-layout';
const previewLayout = 'workspace.previewLayout';
const applyLayout = 'workspace.applyLayout';
const mutations = ['chart.setTimeframe', 'workspace.undo', previewLayout, applyLayout];
const selectEvidence = 'workspace.selectEvidence';
const readChartProjection = 'workspace.readChartProjection';
const evidenceOperations = [selectEvidence, readChartProjection];
const frozenChartControls = ['frozen_chart', 'frozen_chart_status'];
const exportBackup = 'decisionNote.exportBackup';
const previewRestore = 'decisionNote.previewRestore';
const restoreBackup = 'decisionNote.restoreBackup';
const noteOperations = ['decisionNote.save', 'decisionNote.read', 'decisionNote.appendOutcome', 'decisionNote.delete',
  exportBackup, previewRestore, restoreBackup];
const readPrivateRecords = 'privateRecord.read';
const changePrivateRecords = 'privateRecord.change';
const exportPrivateRecords = 'privateRecord.exportBackup';
const privateRecordOperations = [readPrivateRecords, changePrivateRecords, exportPrivateRecords];
const maxNoteRequestBytes = 32 * 1024;
const maxResponseBytes = 256 * 1024;
const maxBackupFrameBytes = 1024 * 1024;
const startupDeadlineMs = 10_000;
const requestDeadlineMs = 15_000;
const commandDeadlineMs = 8_000;
const uuid = value => typeof value === 'string' && /^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/i.test(value);
const revision = value => Number.isSafeInteger(value) && value >= 0;
const unavailable = () => new PreviewFailure(503, 'WORKER_EXITED');
const invalidReply = () => new PreviewFailure(502, 'OUTPUT_INVALID');
const timeout = () => new PreviewFailure(504, 'TIMEOUT');

function withinDirectory(directory, path) {
  const fromDirectory = relative(directory, path);
  return !isAbsolute(fromDirectory) && fromDirectory !== '..' && !fromDirectory.startsWith('..' + sep);
}

function isPrivateResource(url, roots) {
  let pathname;
  try { pathname = decodeURIComponent(url.split(/[?#]/, 1)[0]); } catch { return false; }
  if (privateFile.test(basename(pathname))) return true;
  if (!pathname.startsWith(rawFiles)) return false;
  // Preserve the requested drive before Vite's raw-file middleware resolves it.
  const file = pathname.slice(rawFiles.length);
  const absolute = isAbsolute(file) ? file : '/' + file;
  return roots.some(root => withinDirectory(root, absolute));
}

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
    let physical = served;
    try { physical = realpathSync(served); } catch (error) {
      if (error.code !== 'ENOENT') throw new Error('The Ubuntu workspace could not verify Vite filesystem isolation.');
    }
    if (withinDirectory(physical, path)) {
      throw new Error('The Ubuntu workspace profile must be outside the directories served by Vite.');
    }
  }
  return { path, profileId, workspaceId, privateRoots: [resolve(directory), path] };
}

function validWorkspace(workspace, workspaceId) {
  return workspace?.id === workspaceId && revision(workspace.revision)
    && timeframes.includes(workspace.timeframe) && workspace.instrument_id === null && workspace.dataset === null
    && workspace.visible_range === null && Array.isArray(workspace.layers) && workspace.layers.length === 0;
}

function validUndo(undo, workspace) {
  return undo === null || !!undo && uuid(undo.operation_id)
    && revision(undo.committed_workspace_revision) && undo.committed_workspace_revision > 0
    && undo.committed_workspace_revision <= workspace.revision;
}

function validNamedLayout(layout) {
  const plan = layout?.definition;
  return (layout?.name === null || typeof layout?.name === 'string' && layout.name.length <= 96)
    && plan?.version === 1 && Array.isArray(plan.panels) && plan.panels.length === 2
    && new Set(plan.panels.map(panel => panel?.panel)).size === 2
    && plan.panels.every(panel => ['watchlist', 'inspection'].includes(panel?.panel)
      && ['left', 'right', 'bottom'].includes(panel.dock) && Number.isInteger(panel.size)
      && panel.size >= 180 && panel.size <= 480)
    && ['dark', 'light'].includes(plan.theme) && ['gain_color', 'loss_color'].every(key => /^#[0-9a-f]{6}$/i.test(plan[key]))
    && typeof plan.link_selection === 'boolean' && (plan.pinned_instrument === null || typeof plan.pinned_instrument === 'string');
}

function validSnapshot(state, workspaceId) {
  const source = state?.source;
  const validSource = source == null || source.kind === 'none' || source.kind === 'sample'
    && (source.instrument_id === null || typeof source.instrument_id === 'string')
    || source.kind === 'frozen' && uuid(source.revision_id) && ['available', 'revoked', 'unavailable'].includes(source.status);
  const evidence = state?.chart_evidence;
  const validEvidence = evidence === null || evidence?.unavailable === true || uuid(evidence?.revision_id)
    && Number.isSafeInteger(evidence.range?.from_ms) && Number.isSafeInteger(evidence.range?.to_ms)
    && evidence.range.from_ms < evidence.range.to_ms;
  return validWorkspace(state?.workspace, workspaceId) && revision(state.selection_revision)
    && validUndo(state.undo, state.workspace) && validEvidence && validSource && (state.layouts == null
      || validNamedLayout(state.layouts.active) && Array.isArray(state.layouts.saved)
      && state.layouts.saved.length <= 24 && state.layouts.saved.every(name => typeof name === 'string')
      && validUndo(state.layouts.undo, state.workspace));
}

function requestLimit(operation) {
  if (operation === previewRestore || operation === changePrivateRecords) return maxBackupFrameBytes;
  return noteOperations.includes(operation) ? maxNoteRequestBytes : maxRequestBytes;
}

function responseLimit(operation) {
  if (operation === 'frozen_chart') return 4 * 1024 * 1024 + maxRequestBytes;
  if (operation === readChartProjection) return maxResponseBytes;
  if (operation === exportBackup || operation === exportPrivateRecords) return maxBackupFrameBytes;
  return noteOperations.includes(operation) || privateRecordOperations.includes(operation) ? maxResponseBytes : maxRequestBytes;
}

function requestFrame(command, workspaceId) {
  if (frozenChartControls.includes(command?.type)) {
    if (Object.keys(command).length !== 3 || !uuid(command.request_id) || !uuid(command.revision_id)) {
      throw new PreviewFailure(400, 'INVALID_ARGUMENT');
    }
    return JSON.stringify(command) + '\n';
  }
  if (command?.type === sourceMutation) {
    if (!uuid(command.request_id) || !revision(command.expected_selection_revision)) throw new PreviewFailure(400, 'INVALID_ARGUMENT');
    const line = JSON.stringify(command) + '\n';
    if (Buffer.byteLength(line) > maxRequestBytes) throw new PreviewFailure(413, 'RESOURCE_LIMIT');
    return line;
  }
  const evidence = evidenceOperations.includes(command?.operation);
  const name = evidence ? command.operation : command?.intent?.operation;
  if (!evidence && !mutations.includes(name) && !noteOperations.includes(name) && !privateRecordOperations.includes(name)) throw new PreviewFailure(403, 'PERMISSION_DENIED');
  if ((evidence ? command.workspace_id : command?.scope?.workspace_id) !== workspaceId) throw new PreviewFailure(403, 'PERMISSION_DENIED');
  if (command.protocol_version !== '1.1' || command.type !== 'request'
      || !uuid(command.request_id) || !uuid(command.message_id)) {
    throw new PreviewFailure(400, 'INVALID_ARGUMENT');
  }
  const line = JSON.stringify(command) + '\n';
  if (Buffer.byteLength(line) > requestLimit(name)) {
    throw new PreviewFailure(413, 'RESOURCE_LIMIT');
  }
  return line;
}

function validEvidenceReply(reply, job, workspaceId) {
  if (reply.operation !== job.operation) return false;
  if (reply.error) return typeof reply.error.code === 'string' && !reply.selection && !reply.result;
  if (job.operation === selectEvidence) {
    const selection = reply.selection;
    return selection?.workspace_id === workspaceId && selection.selection_revision === job.selectionRevision + 1
      && (selection.target === null || selection.target?.kind === 'chart_interval');
  }
  const result = reply.result;
  return uuid(result?.source_reference) && uuid(result.revision) && Array.isArray(result.fields)
    && Array.isArray(result.rows) && result.rows.length <= 1000;
}

function validNoteOutcome(outcome, job, workspaceId) {
  if (outcome?.operation !== job.operation) return false;
  if (job.operation === 'decisionNote.read') {
    const { result } = outcome;
    return outcome.state === 'completed' && Array.isArray(result?.notes) && Array.isArray(result?.outcomes)
      && (job.noteId === null ? result.note === null && result.outcomes.length === 0
        : result.note?.note_id === job.noteId && result.notes.length === 0);
  }
  if (job.operation === exportBackup) {
    const { result } = outcome;
    return outcome.state === 'completed' && result?.format === 'scopehub.decision-notes' && result.version === 1
      && revision(result.captured_workspace_revision) && Array.isArray(result.notes) && Array.isArray(result.deletions);
  }
  if (job.operation === previewRestore) {
    const { summary } = outcome;
    return outcome.state === 'confirmation_required' && uuid(outcome.stage_id) && revision(outcome.expires_at_ms)
      && typeof summary?.independent_copy_warning === 'boolean'
      && ['added_notes', 'added_outcomes', 'removed_notes', 'suppressed_notes', 'imported_deletions']
        .every(field => revision(summary[field]));
  }
  if (outcome.state === 'confirmation_required') return uuid(outcome.challenge_id) && revision(outcome.expires_at_ms);
  const { receipt } = outcome;
  const isRestore = job.operation === restoreBackup;
  return outcome.state === 'committed' && !Object.hasOwn(outcome, 'result') && receipt?.workspace_id === workspaceId
    && receipt.target?.kind === (isRestore ? 'workspace' : 'decision_note')
    && receipt.target.object_id === (isRestore ? workspaceId : job.noteId)
    && uuid(receipt.operation_id) && revision(receipt.committed_workspace_revision) && receipt.undoable === false;
}

function validPrivateRecordOutcome(outcome, job, workspaceId) {
  if (outcome?.operation !== job.operation) return false;
  if (job.operation === readPrivateRecords) {
    const { result } = outcome;
    return outcome.state === 'completed' && revision(result?.workspace_revision) && Array.isArray(result.records)
      && (job.recordId === null ? result.record === null
        : result.record?.record_id === job.recordId && result.records.length === 0);
  }
  if (job.operation === exportPrivateRecords) {
    const { result } = outcome;
    return outcome.state === 'completed' && result?.format === 'scopehub.private-records' && result.version === 1
      && Array.isArray(result.records) && Array.isArray(result.deletions);
  }
  if (outcome.state === 'confirmation_required') {
    return job.recordAction !== 'resolve' && uuid(outcome.challenge_id) && revision(outcome.expires_at_ms)
      && ['added', 'corrected', 'removed', 'kept', 'suppressed', 'imported_deletions']
        .every(field => revision(outcome.summary?.[field]));
  }
  const { receipt } = outcome;
  return job.recordAction === 'resolve' && outcome.state === 'committed' && receipt?.workspace_id === workspaceId
    && !Object.hasOwn(outcome, 'result') && uuid(receipt.operation_id) && receipt.undoable === false
    && (receipt.record_id === null || uuid(receipt.record_id))
    && receipt.committed_workspace_revision === job.workspaceRevision + 1;
}

function startWriter(root, { path, profileId, workspaceId }) {
  const executable = process.platform === 'win32' ? 'workspace_bridge.exe' : 'workspace_bridge';
  const child = spawn(resolve(root, 'target/debug', executable), [path, profileId, workspaceId], {
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
  let ready = false;
  let snapshotRequestId = 0;
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
    if (!ready) {
      if (frame?.ok !== true || frame.private_profile !== true || !validSnapshot(frame.result, workspaceId)) {
        throw invalidReply();
      }
      ready = true;
      clearTimeout(startupTimer);
      startup.resolve();
      return;
    }

    const job = pending;
    if (!job) throw invalidReply();
    const validFailure = frame?.ok === false && typeof frame.error?.code === 'string';
    if (job.operation === 'snapshot') {
      if (frame?.request_id !== job.requestId
          || !(validFailure || frame.ok === true && validSnapshot(frame.result, workspaceId))) throw invalidReply();
      // Keep correlation internal to the trusted stdio transport.
      delete frame.request_id;
    } else if (job.operation === sourceMutation) {
      if (frame?.request_id !== job.requestId || !(validFailure || frame?.ok === true
        && frame.result?.selection_revision === job.selectionRevision + 1)) throw invalidReply();
      delete frame.request_id;
    } else if (job.operation === 'frozen_chart_status') {
      if (frame?.request_id !== job.requestId || !(validFailure || frame?.ok === true
        && typeof frame.result?.available === 'boolean' && Object.keys(frame.result).length === 1)) throw invalidReply();
      delete frame.request_id;
    } else if (job.operation === 'frozen_chart') {
      const chart = frame?.result;
      if (frame?.request_id !== job.requestId || !(validFailure || frame?.ok === true
          && chart?.snapshot?.dataset_id === job.revisionId
          && chart?.revision?.revision_id === job.revisionId && Array.isArray(chart.bars)
          && chart.bars.length === chart.revision.bar_count)) throw invalidReply();
      delete frame.request_id;
    } else if (frame?.ok === true) {
      const reply = frame.result;
      const outcome = reply?.outcome;
      if (reply?.type !== 'response' || reply?.protocol_version !== '1.1'
          || reply?.request_id !== job.requestId || !uuid(reply.message_id)
          || reply.message_id === job.messageId) throw invalidReply();
      if (evidenceOperations.includes(job.operation)) {
        if (!validEvidenceReply(reply, job, workspaceId)) throw invalidReply();
      } else if (outcome?.state === 'failed') {
        if (typeof outcome.error?.code !== 'string') throw invalidReply();
      } else if (noteOperations.includes(job.operation)) {
        if (!validNoteOutcome(outcome, job, workspaceId)) throw invalidReply();
      } else if (privateRecordOperations.includes(job.operation)) {
        if (!validPrivateRecordOutcome(outcome, job, workspaceId)) throw invalidReply();
      } else if (job.operation === previewLayout) {
        if (outcome?.state !== 'previewed' || outcome.operation !== job.operation
            || !validNamedLayout(outcome.result?.layout)
            || !/^[a-f0-9]{64}$/.test(outcome.result?.preview_token)) throw invalidReply();
      } else if (outcome?.state === 'committed') {
        const { result, receipt } = outcome;
        const layout = job.operation === applyLayout || job.operation === 'workspace.undo' && job.layoutTarget;
        if (outcome.operation !== job.operation
            || !(job.operation === applyLayout ? validNamedLayout(result) : validWorkspace(result, workspaceId))
            || receipt?.workspace_id !== workspaceId || receipt?.target?.kind !== (layout ? 'layout' : 'chart')
            || receipt?.target?.object_id !== (layout ? layoutId : chartId) || !uuid(receipt.operation_id)
            || receipt.committed_workspace_revision !== (job.operation === applyLayout ? job.workspaceRevision + 1 : result.revision)
            || typeof receipt.undoable !== 'boolean') throw invalidReply();
      } else throw invalidReply();
    } else if (!validFailure) throw invalidReply();
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
        const limit = responseLimit(pending?.operation);
        if (end >= limit) throw invalidReply();
        receive(JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes.subarray(0, end))));
        bytes = bytes.subarray(end + 1);
      }
      const limit = responseLimit(pending?.operation);
      if (bytes.length >= limit) throw invalidReply();
      remainder = bytes;
    } catch {
      stop(invalidReply());
    }
  });
  startupTimer = setTimeout(() => stop(timeout()), startupDeadlineMs);

  return {
    ready: startup.promise,
    async handle(body, signal) {
      await startup.promise;
      signal.throwIfAborted();
      if (failure) throw failure;
      if (pending) throw new PreviewFailure(429, 'BUSY');
      const snapshot = body?.type === 'snapshot';
      if (snapshot && Object.keys(body).length !== 1) throw new PreviewFailure(400, 'INVALID_ARGUMENT');
      const requestId = snapshot ? ++snapshotRequestId : body?.request_id;
      const line = snapshot ? JSON.stringify({ type: 'snapshot', request_id: requestId }) + '\n'
        : requestFrame(body, workspaceId);
      const reply = Promise.withResolvers();
      const noteId = body?.scope?.target?.object_id ?? body?.intent?.arguments?.note_id;
      const operation = snapshot || body.type === sourceMutation || frozenChartControls.includes(body.type)
        ? body.type : body.operation ?? body.intent?.operation;
      pending = {
        ...reply, requestId, messageId: body.message_id, operation,
        revisionId: body.revision_id, selectionRevision: body.expected_selection_revision,
        recordId: typeof body.intent?.arguments?.record_id === 'string'
          ? body.intent.arguments.record_id.toLowerCase() : body.intent?.arguments?.record_id,
        recordAction: body.intent?.arguments?.action, workspaceRevision: body.scope?.expected_workspace_revision,
        layoutTarget: body.scope?.target?.kind === 'layout' && body.scope.target.object_id === layoutId,
        noteId: typeof noteId === 'string' ? noteId.toLowerCase() : noteId,
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
  let access;
  let closing = false;
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
      http.once('listening', () => {
        const port = http.address().port;
        writer = startWriter(root, profile);
        void writer.ready.then(() => {
          if (closing) return;
          access = issueWorkspaceAccess(profile.path, port);
          server.config.logger.info(`Open ${access.name} from the private workspace profile directory.`);
        }).catch(() => {
          if (!closing) server.config.logger.error('The private workspace handoff could not be issued.');
          return writer.close();
        });
      });

      let active = false;
      server.middlewares.use(async (request, response, next) => {
        if (isPrivateResource(request.url, profile.privateRoots)) {
          response.writeHead(404, { 'Cache-Control': 'no-store', 'Content-Length': '0' }).end();
          return;
        }
        if (request.url !== route) return next();
        try {
          assertLocalJsonRequest(request, http, maxBackupFrameBytes);
          if (!access?.permits(request.headers.authorization)) throw new PreviewFailure(403, 'PERMISSION_DENIED');
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
          const body = await readLocalJson(request, maxBackupFrameBytes, requestLimit);
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
      closing = true;
      const issued = access;
      access = undefined;
      try { issued?.close(); } catch {
        // The removed grant cannot be reused even if its local file remains.
      }
      await writer?.close();
    },
  };
}
