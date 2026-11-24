import { setTimeout as delay } from 'node:timers/promises';

export const startupMessage = 'workspace-service-startup';

export function observeServiceStartup(child, { startedAt, mode, port, requireListen }) {
  const elapsed = () => Math.round(performance.now() - startedAt);
  const phases = [];
  const outcomes = {};
  let lastProbe;
  let childError;
  let listening = false;
  let exit;
  let close;

  child.on('message', message => {
    if (message?.type !== startupMessage) return;
    const { phase, code } = message;
    phases.push({ phase, elapsed_ms: elapsed(), ...(code && { code }) });
    if (phase === 'listening') listening = true;
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
    async wait(url, timeoutMs = 10_000) {
      const deadline = performance.now() + timeoutMs;
      for (;;) {
        const remaining = Math.ceil(deadline - performance.now());
        const reason = childError ? 'child-error' : exited() ? 'child-exited' : remaining <= 0 ? 'readiness-timeout' : undefined;
        if (reason) return { reason, failure_ms: elapsed(), alive_at_failure: Boolean(child.pid) && !childError && !exited() };

        let response;
        try {
          response = await fetch(url, { redirect: 'manual', signal: AbortSignal.timeout(Math.min(500, remaining)) });
        } catch (error) {
          recordProbe(error.cause?.code ?? error.name);
        }
        if (response) {
          await response.body?.cancel();
          if (response.ok && (!requireListen || listening) && !exited() && !childError && performance.now() < deadline) return;
          recordProbe(`HTTP_${response.status}${response.ok && requireListen && !listening ? '_BEFORE_LISTEN' : ''}`);
        }
        await delay(Math.min(30, Math.max(0, deadline - performance.now())));
      }
    },
    failure(observation, cleanup, output) {
      const startup = {
        mode, requested_port: port, pid: child.pid ?? null, ...observation,
        phases, probe: { outcomes, last: lastProbe ?? null }, child_error: childError ?? null,
        ...cleanup, exit: exit ?? null, close: close ?? null,
        // A failing launch may have written diagnostics before the parent could read its handoff.
        output: output.replace(/(#workspace=)[a-f0-9]+|[a-f0-9]{64}/gi, '$1[redacted]'),
      };
      return Object.assign(new Error(`Vite failed to start in ${mode}: ${JSON.stringify(startup)}`), { startup });
    },
  };
}
