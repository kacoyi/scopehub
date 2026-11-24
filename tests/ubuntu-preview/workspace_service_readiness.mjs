import { get } from 'node:http';
import { setTimeout as delay } from 'node:timers/promises';
import { stripVTControlCharacters } from 'node:util';

export const startupMessage = 'workspace-service-startup';

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
  const cliListen = new RegExp(`\\bLocal:\\s+http://127\\.0\\.0\\.1:${port}/(?:\\s|$)`);
  const outputNotices = [
    `Error: Port ${port} is already in use`,
    'The private workspace handoff could not be issued.',
    'Fixture released its unbound listener during cleanup.',
  ];
  let lastProbe;
  let childError;
  let listening = false;
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
  function recordProbe(cause) {
    outcomes[cause] = (outcomes[cause] ?? 0) + 1;
    lastProbe = { cause, elapsed_ms: elapsed() };
  }

  return {
    closed,
    elapsed,
    observeCliOutput(output) {
      // Vite prints its local URL after listen() completes in the terminal-signal fixture.
      if (!listening && cliListen.test(stripVTControlCharacters(output))) recordPhase('listening');
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
        phases, probe: { outcomes, last: lastProbe ?? null }, child_error: childError ?? null,
        ...cleanup, exit: exit ?? null, close: close ?? null,
        // Report only known notices. A rolling tail can contain a fragment of a private handoff.
        output: outputNotices.filter(notice => recentOutput.includes(notice)).join('\n'),
      };
      return Object.assign(new Error(`Vite failed to start in ${mode}: ${JSON.stringify(startup)}`), { startup });
    },
  };
}
