import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

const exec = promisify(execFile);
const deadlineMs = 12_000;
const bridgeErrors = new Map([
  ['Workspace storage requires an absolute profile directory and valid identities.', 'invalid-binding'],
  ['Workspace storage has an unsupported format.', 'unsupported-format'],
  ['Workspace storage belongs to a different profile or workspace.', 'profile-mismatch'],
  ['Workspace storage is busy.', 'storage-busy'],
  ['Workspace storage could not be opened.', 'storage-open'],
  ['Workspace bridge could not complete the local exchange.', 'local-exchange'],
]);
const aclRefusal = /^Workspace storage ACL check refused \(open, (account|local-path|local-ntfs|action|metadata|path-type|acl-read|acl-entry|ancestor-owner|replaceable-ancestor|shared-access|owner-or-inheritance|private-creation|missing-directory|profile-or-file-owner|worker-(?:cleanup|control|execution|job|launch|launch-timeout|location|spawn|timeout|wait))\)\.$/;
const aclProgress = /^Workspace storage worker \(open\) spawn-to-ready-ms=(?:None|Some\((\d{1,8})\)) entry-to-ready-ms=(?:None|Some\((\d{1,8})\)) phase=(none|ready|identity|volume|ancestors|profile|children|files|complete) phase-ms=(?:None|Some\((\d{1,8})\)) elapsed-ms=(\d{1,8})$/;
const optionalMs = value => value === undefined ? null : Number(value);

export function workspaceBridgeNotices(stderr) {
  const observation = { error: null, acl_refusal: null, acl_progress: null };
  // Discard a potentially partial first line if the retained tail was clipped.
  const start = stderr.length > 8192 ? stderr.indexOf('\n', stderr.length - 8192) + 1 : 0;
  if (stderr.length > 8192 && !start) return observation;
  for (const line of stderr.slice(start).split(/\r?\n/)) {
    observation.error = bridgeErrors.get(line) ?? observation.error;
    const refused = aclRefusal.exec(line);
    if (refused) observation.acl_refusal = refused[1];
    const progress = aclProgress.exec(line);
    if (progress) {
      const [, spawnToReady, entryToReady, phase, phaseMs, elapsed] = progress;
      observation.acl_progress = {
        phase, spawn_to_ready_ms: optionalMs(spawnToReady), entry_to_ready_ms: optionalMs(entryToReady),
        phase_ms: optionalMs(phaseMs), elapsed_ms: Number(elapsed),
      };
    }
  }
  return observation;
}

export async function initializeFrozenProfile(executable, profile, workspaceId, observation = {}) {
  const started = performance.now();
  const elapsed = () => Math.round(performance.now() - started);
  const execution = exec(executable, [profile.root, profile.id, workspaceId], {
    timeout: deadlineMs, windowsHide: true,
    env: { ...process.env, SCOPEHUB_WORKSPACE_ACL_PHASES: '1' },
  });
  const { child } = execution;
  Object.assign(observation, { stage: 'bridge-open', deadline_ms: deadlineMs, pid: child.pid ?? null, exit: null });
  child.once('exit', (code, signal) => { observation.exit = { code, signal, elapsed_ms: elapsed() }; });
  const closed = new Promise(resolve => child.once('close', (code, signal) => {
    observation.close = { code, signal, elapsed_ms: elapsed() };
    resolve();
  }));
  // EOF gives the bridge the same normal exit after emitting its startup frame.
  child.stdin.once('error', error => { observation.stdin_error = error.code ?? 'unknown'; });
  child.stdin.end();

  let result;
  let processError;
  try {
    result = await execution;
  } catch (error) {
    processError = error;
  } finally {
    await closed;
  }
  if (processError) {
    observation.outcome = processError.code === 'ERR_CHILD_PROCESS_STDIO_MAXBUFFER' ? 'output-limit'
      : processError.killed || child.killed ? 'deadline'
      : observation.exit?.signal ? 'signaled' : observation.exit ? 'nonzero-exit' : child.pid ? 'process-error' : 'spawn-error';
    observation.code = processError.code ?? null;
    observation.stderr = workspaceBridgeNotices(processError.stderr ?? '');
  } else {
    observation.stderr = workspaceBridgeNotices(result.stderr);
    let frame;
    try { frame = JSON.parse(result.stdout); } catch { /* Report the frame category only. */ }
    const workspace = frame?.result?.workspace;
    observation.outcome = frame?.ok === true && frame.private_profile === true
      && workspace?.id === workspaceId && Number.isSafeInteger(workspace.revision) ? 'ready' : 'invalid-startup-frame';
  }
  if (observation.outcome !== 'ready') {
    throw Object.assign(new Error(`Frozen evidence bridge preparation failed: ${JSON.stringify(observation)}`), { preparation: observation });
  }
}
