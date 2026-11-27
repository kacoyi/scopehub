import { lstat, readFile } from 'node:fs/promises';
import { get } from 'node:http';
import { setTimeout as delay } from 'node:timers/promises';
import { stripVTControlCharacters } from 'node:util';
import { workspaceBridgeNotices } from './workspace_fixture_bridge.mjs';

export const startupMessage = 'workspace-service-startup';
const handoffRejectionNotice = 'The private workspace handoff could not be issued.';
const maxCliLineLength = 256;

export function captureServiceOutput(child, observeCliStdout, port) {
  let output = '';
  let stderr = '';
  let stdout = '';
  let handoffRejected = false;
  let handoffPublished = false;
  const publicationNotice = `Open workspace-access-${port}.html from the private workspace profile directory.`;
  for (const stream of [child.stdout, child.stderr]) {
    stream.setEncoding('utf8');
    stream.on('data', chunk => {
      output = (output + chunk).slice(-6000);
      if (stream === child.stdout) {
        stdout = (stdout + chunk).slice(-8192);
        handoffPublished ||= stdout.includes(publicationNotice);
        observeCliStdout?.(chunk);
      } else {
        stderr = (stderr + chunk).slice(-8193);
        handoffRejected ||= stderr.includes(handoffRejectionNotice);
      }
    });
  }
  return {
    recent: () => output, handoffRejected: () => handoffRejected,
    handoffPublication: () => ({ published: handoffPublished, rejected: handoffRejected }),
    writerNotices: () => workspaceBridgeNotices(stderr),
  };
}

function fileError(error) {
  if (error.code === 'ENOENT') return { file_state: 'missing' };
  const code = ['EACCES', 'EPERM', 'EBUSY', 'EISDIR', 'ENOTDIR', 'EIO'].find(code => code === error.code);
  return { file_state: 'unreadable', code: code ?? 'unknown' };
}

export async function workspaceHandoffFile(path) {
  try {
    const metadata = await lstat(path);
    return { file_state: !metadata.isFile() ? 'non-file' : metadata.size > 4096 ? 'oversize' : 'file' };
  } catch (error) {
    return fileError(error);
  }
}

async function readHandoff(path, url, signal) {
  const metadata = await workspaceHandoffFile(path);
  if (metadata.file_state !== 'file') return metadata;
  try {
    const content = await readFile(path, { encoding: 'utf8', signal });
    const link = /href="(http:\/\/127\.0\.0\.1:\d+\/#workspace=([a-f0-9]{64}))"/.exec(content);
    if (!link) return { file_state: 'invalid-link' };
    const origin = URL.parse(link[1])?.origin;
    if (!origin) return { file_state: 'invalid-link' };
    if (origin !== url) return { file_state: 'wrong-origin' };
    return { file_state: 'ready', capability: link[2] };
  } catch (error) {
    return fileError(error);
  }
}

export async function waitForWorkspaceHandoff(path, url, startup, output, observation) {
  const deadlineMs = 12_000;
  const deadline = performance.now() + deadlineMs;
  const stop = new AbortController();
  const expired = delay(deadlineMs, undefined, { signal: stop.signal }).catch(() => undefined);
  const counts = {};
  Object.assign(observation, {
    stage: 'private-handoff', readiness_ms: startup.elapsed(), deadline_ms: deadlineMs, counts, last_file: null,
  });
  try {
    for (;;) {
      const current = await Promise.race([readHandoff(path, url, stop.signal), expired]);
      if (current) {
        const { capability, ...file } = current;
        observation.last_file = { ...file, elapsed_ms: startup.elapsed() };
        counts[file.file_state] = (counts[file.file_state] ?? 0) + 1;
      }
      observation.publication = output.handoffPublication();
      const terminal = startup.terminal();
      const remaining = deadline - performance.now();
      const outcome = terminal.child_error ? 'child-error' : terminal.exit ? 'child-exited'
        : !current || remaining <= 0 ? 'deadline' : current.capability ? 'ready'
        : observation.publication.rejected ? 'launcher-rejected' : undefined;
      if (outcome) {
        Object.assign(observation, { outcome, terminal, finished_ms: startup.elapsed() });
        return outcome === 'ready' ? current.capability : undefined;
      }
      await delay(Math.min(100, remaining));
    }
  } finally {
    stop.abort();
  }
}

function probeStatus(url, signal) {
  return new Promise((resolve, reject) => {
    // An earlier probe may have reached a different listener on this port.
    const request = get(url, { agent: false, signal }, response => {
      resolve(response.statusCode);
      response.destroy();
    });
    request.once('error', reject);
  });
}

export function observeServiceStartup(child, { startedAt, mode, port }) {
  const elapsed = (at = performance.now()) => Math.round(at - startedAt);
  const phases = [];
  const outcomes = {};
  const cliListen = new RegExp(`^\\s*➜\\s+Local:\\s+http://127\\.0\\.0\\.1:${port}/\\s*$`);
  const outputNotices = [
    `Error: Port ${port} is already in use`,
    handoffRejectionNotice,
    'Fixture released its unbound listener during cleanup.',
  ];
  let lastProbe;
  let childError;
  let listening = false;
  let cliLine = '';
  let heldAt;
  let exit;
  let close;

  function recordPhase(phase, code) {
    const at = performance.now();
    if (phase === 'held-before-listen') heldAt ??= at;
    if (phase === 'listening') listening = true;
    phases.push({ phase, elapsed_ms: elapsed(at), ...(code && { code }) });
  }
  child.on('message', message => {
    if (message?.type === startupMessage) recordPhase(message.phase, message.code);
  });
  child.on('error', error => { childError = { code: error.code ?? error.name, elapsed_ms: elapsed() }; });
  child.once('exit', (code, signal) => { exit = { code, signal, elapsed_ms: elapsed() }; });
  // 'close' follows stdio drainage, even when the process failed to spawn.
  const closed = new Promise(resolve => child.once('close', (code, signal) => {
    close = { code, signal, elapsed_ms: elapsed() };
    resolve([code, signal]);
  }));

  const exited = () => child.exitCode !== null || child.signalCode !== null;
  const terminal = () => ({ child_error: childError ?? null, exit: exit ?? null, close: close ?? null });
  function recordProbe(cause) {
    outcomes[cause] = (outcomes[cause] ?? 0) + 1;
    lastProbe = { cause, elapsed_ms: elapsed() };
  }

  return {
    closed,
    elapsed,
    terminal,
    observeCliStdout(chunk) {
      // Vite prints its local URL after listen() completes in the terminal-signal fixture.
      if (listening) return;
      const lines = chunk.split('\n');
      for (const [index, line] of lines.entries()) {
        // Discard overlong lines through their newline so a suffix cannot look like a notice.
        if (cliLine !== undefined) {
          cliLine = cliLine.length + line.length <= maxCliLineLength ? cliLine + line : undefined;
        }
        if (index === lines.length - 1) return;
        const ready = cliLine !== undefined && cliListen.test(stripVTControlCharacters(cliLine));
        cliLine = '';
        if (ready) {
          recordPhase('listening');
          return;
        }
      }
    },
    async wait(url, holdTimeoutMs) {
      const startupDeadline = performance.now() + 10_000;
      for (;;) {
        const observedHold = holdTimeoutMs !== undefined && heldAt !== undefined && heldAt <= startupDeadline;
        const deadline = observedHold ? heldAt + holdTimeoutMs : startupDeadline;
        const remaining = Math.ceil(deadline - performance.now());
        const reason = childError ? 'child-error' : exited() ? 'child-exited' : remaining <= 0 ? 'readiness-timeout' : undefined;
        if (reason) return { reason, failure_ms: elapsed(), alive_at_failure: Boolean(child.pid) && !childError && !exited() };

        const beganAfterListen = listening;
        let status;
        try {
          status = await probeStatus(url, AbortSignal.timeout(Math.min(500, remaining)));
        } catch (error) {
          recordProbe(error.cause?.name === 'TimeoutError' ? 'TimeoutError' : error.code ?? error.name);
        }
        if (status !== undefined) {
          const ok = status >= 200 && status < 300;
          if (ok && beganAfterListen && !exited() && !childError && performance.now() < deadline) return;
          recordProbe(`HTTP_${status}${ok && !beganAfterListen ? '_BEFORE_LISTEN' : ''}`);
        }
        await delay(Math.min(30, Math.max(0, deadline - performance.now())));
      }
    },
    failure(observation, cleanup, recentOutput) {
      const startup = {
        mode, requested_port: port, pid: child.pid ?? null, ...observation,
        phases, probe: { outcomes, last: lastProbe ?? null }, ...terminal(), ...cleanup,
        // Report only known notices. A rolling tail can contain a fragment of a private handoff.
        output: outputNotices.filter(notice => recentOutput.includes(notice)).join('\n'),
      };
      return Object.assign(new Error(`Vite failed to start in ${mode}: ${JSON.stringify(startup)}`), { startup });
    },
  };
}
