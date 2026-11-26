import childProcess from 'node:child_process';
import { EventEmitter, once } from 'node:events';
import { mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { syncBuiltinESMExports } from 'node:module';
import { join } from 'node:path';
import { PassThrough, Writable } from 'node:stream';

const syntheticFailure = JSON.stringify({
  error: { code: 'WORKER_EXITED', retryable: false, message: 'Synthetic delayed cleanup.', details: {} },
});

// Interpose on the carrier pipe only. Vite, the HTTP route and the Rust
// workspace writer remain the ordinary production entry points.
export function installIndicatorGate(carrier, profile) {
  const spawn = childProcess.spawn;
  const launches = [];
  const scratch = join(profile, 'synthetic-indicator-scratch');
  const notify = (index, stage, details) => {
    if (process.connected) process.send({ indicatorGate: { index, stage, ...details } }, () => {});
  };

  function controlledChild(index) {
    const child = new EventEmitter();
    child.exitCode = null;
    child.signalCode = null;
    child.stdout = new PassThrough();
    child.stdin = new Writable({
      write(line, _encoding, done) {
        const { task_id } = JSON.parse(line);
        if (index === 0) {
          mkdirSync(scratch);
          writeFileSync(join(scratch, 'input.json'), 'synthetic input');
        }
        done();
        notify(index, 'started', { task_id });
      },
    });
    child.stdin.once('finish', () => notify(index, 'stdin-ended'));
    // The newline is held separately to prove that an incomplete frame stays
    // pending. A completed failure can be delivered while cleanup is held.
    const frame = complete => {
      child.stdout.write(complete && index === 0 ? '\n' : syntheticFailure + (complete ? '\n' : ''));
      setImmediate(() => notify(index, complete ? 'frame' : 'partial'));
    };
    const release = () => {
      if (child.exitCode !== null) return;
      if (index === 0) rmSync(scratch, { recursive: true, force: true });
      child.stdin.end();
      child.stdout.end();
      child.exitCode = 0;
      child.emit('exit', 0, null);
      child.emit('close', 0, null);
      notify(index, 'closed', {
        child: child.listenerCount('close') + child.listenerCount('error') + child.listenerCount('exit'),
        stdin: child.stdin.listenerCount('error'),
        stdout: child.stdout.listenerCount('data') + child.stdout.listenerCount('error'),
      });
    };
    launches.push({ frame, release });
    return child;
  }

  function holdRealClose(child, index) {
    const emit = child.emit;
    let held;
    let released = false;
    child.emit = function (name, ...args) {
      if (name !== 'close') return emit.call(this, name, ...args);
      child.emit = emit;
      if (released) return emit.call(this, name, ...args);
      held = () => emit.call(this, name, ...args);
      notify(index, 'close-held', { exit: args[0] });
      return true;
    };
    launches.push({ release() {
      released = true;
      held?.();
      held = undefined;
      notify(index, 'closed');
    } });
    return child;
  }

  childProcess.spawn = function (file, ...args) {
    if (file !== carrier) return spawn.call(this, file, ...args);
    const index = launches.length;
    if (index === 1) return holdRealClose(spawn.call(this, file, ...args), index);
    return controlledChild(index);
  };
  syncBuiltinESMExports();

  const releaseAll = () => launches.forEach(run => run.release());
  const onMessage = message => {
    const command = message?.indicatorGate;
    if (!command) return;
    const { index, action } = command;
    if (action === 'release-all') releaseAll();
    else if (action === 'count') notify(index, 'count', { launches: launches.length });
    else if (action === 'release') launches[index].release();
    else launches[index].frame(action === 'complete');
  };
  process.on('message', onMessage);
  return {
    closing() { notify(-1, 'closing'); },
    close() {
      process.off('message', onMessage);
      childProcess.spawn = spawn;
      syncBuiltinESMExports();
      releaseAll();
    },
  };
}

export function observeIndicatorGate(child) {
  const events = new EventEmitter();
  const observed = new Map();
  const onMessage = message => {
    if (!message?.indicatorGate) return;
    const { index, stage, ...details } = message.indicatorGate;
    const key = `${index}:${stage}`;
    observed.set(key, details);
    events.emit(key, details);
  };
  const onDisconnect = () => events.emit('disconnected');
  child.on('message', onMessage);
  child.on('disconnect', onDisconnect);
  return {
    async wait(index, stage) {
      const key = `${index}:${stage}`;
      if (observed.has(key)) return observed.get(key);
      const [details] = await once(events, key, { signal: AbortSignal.timeout(5_000) });
      return details;
    },
    async command(index, action) {
      if (child.connected) await new Promise((resolve, reject) => {
        child.send({ indicatorGate: { index, action } }, error => error ? reject(error) : resolve());
      });
    },
    async disconnectsWithin(ms) {
      if (!child.connected) return true;
      try {
        await once(events, 'disconnected', { signal: AbortSignal.timeout(ms) });
        return true;
      } catch (error) {
        if (error.code !== 'ABORT_ERR') throw error;
        return false;
      }
    },
    close() {
      child.off('message', onMessage);
      child.off('disconnect', onDisconnect);
    },
  };
}
