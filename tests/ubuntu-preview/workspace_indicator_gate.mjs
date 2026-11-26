import childProcess from 'node:child_process';
import { EventEmitter, once } from 'node:events';
import { mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { syncBuiltinESMExports } from 'node:module';
import { join } from 'node:path';
import { PassThrough, Writable } from 'node:stream';

const syntheticFailure = JSON.stringify({
  error: { code: 'OUTPUT_INVALID', retryable: false, message: 'Synthetic delayed cleanup.', details: {} },
});

const replayCarrier = `
  import { createInterface } from 'node:readline';
  createInterface({ input: process.stdin }).once('line', line => process.send({ request: JSON.parse(line) }));
  process.on('message', ({ frame, exit }) => {
    if (frame !== undefined) process.stdout.write(frame);
    else process.exitCode = exit;
    if (exit !== undefined) {
      process.stdin.destroy();
      process.disconnect();
    }
  });
`;

// Interpose on the carrier pipe only. Vite, the HTTP route and the Rust
// workspace writer remain the ordinary production entry points.
export function installIndicatorGate(carrier, profile) {
  const spawn = childProcess.spawn;
  const launches = [];
  const scratch = join(profile, 'synthetic-indicator-scratch');
  const notify = (index, stage, details) => {
    if (process.connected) process.send({ indicatorGate: { index, stage, ...details } }, () => {});
  };
  let successfulFrame;

  function correlatedSuccess(request) {
    // Replace only the correlation IDs. Preserve the approved artifact bytes,
    // including the Rust/Python JSON number representation and result hash.
    const original = JSON.parse(successfulFrame);
    let frame = successfulFrame;
    for (const key of ['request_id', 'task_id', 'draft_id']) {
      frame = frame.replace(JSON.stringify(original[key]), JSON.stringify(request[key]));
    }
    return frame;
  }

  function observeFrame(child, received) {
    let pending = '';
    const onData = chunk => {
      pending += chunk;
      if (!pending.endsWith('\n')) return;
      child.stdout.off('data', onData);
      received(pending);
    };
    child.stdout.on('data', onData);
    child.stdout.once('end', () => child.stdout.off('data', onData));
  }

  function controlledChild(index) {
    const ownsScratch = index === 0 || index === 5;
    const child = new EventEmitter();
    child.exitCode = null;
    child.signalCode = null;
    child.stdout = new PassThrough();
    let request;
    child.stdin = new Writable({
      write(line, _encoding, done) {
        request = JSON.parse(line);
        if (ownsScratch) {
          mkdirSync(scratch);
          writeFileSync(join(scratch, 'input.json'), 'synthetic input');
        }
        done();
        notify(index, 'started', { task_id: request.task_id });
      },
    });
    child.stdin.once('finish', () => notify(index, 'stdin-ended'));
    // The newline is held separately to prove that an incomplete frame stays
    // pending. A completed failure can be delivered while cleanup is held.
    const write = action => {
      const data = action === 'success' ? correlatedSuccess(request) : action === 'complete' ? '\n' : syntheticFailure;
      child.stdout.write(data);
      setImmediate(() => notify(index,
        action === 'success' ? 'late-success' : action === 'complete' ? 'frame' : 'partial',
        action === 'success' ? { raw: data } : undefined));
    };
    const release = () => {
      if (child.exitCode !== null) return;
      if (ownsScratch) rmSync(scratch, { recursive: true, force: true });
      child.stdin.end();
      child.stdout.end();
      child.exitCode = 1;
      child.emit('exit', child.exitCode, null);
      child.emit('close', child.exitCode, null);
      notify(index, 'closed', {
        exit: child.exitCode,
        child: child.listenerCount('close') + child.listenerCount('error') + child.listenerCount('exit'),
        stdin: child.stdin.listenerCount('error'),
        stdout: child.stdout.listenerCount('data') + child.stdout.listenerCount('error'),
      });
    };
    launches.push({ write, release });
    return child;
  }

  function holdRealClose(child, index) {
    observeFrame(child, raw => { successfulFrame = raw; });
    const emit = child.emit;
    let held;
    let released = false;
    child.emit = function (name, ...args) {
      if (name !== 'close') return emit.call(this, name, ...args);
      child.emit = emit;
      if (released) return emit.call(this, name, ...args);
      held = () => emit.call(this, name, ...args);
      notify(index, 'close-held', { exit: args[0], raw: successfulFrame });
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

  function replaySuccess(index, terminate) {
    const child = spawn(process.execPath, ['--input-type=module', '--eval', replayCarrier], {
      stdio: ['pipe', 'pipe', 'ignore', 'ipc'],
    });
    let request;
    child.once('message', message => {
      request = message.request;
      notify(index, 'started', { task_id: request.task_id });
    });
    observeFrame(child, raw => setImmediate(() => notify(index, 'frame', { raw })));
    child.once('close', (exit, signal) => setImmediate(() => notify(index, 'closed', { exit, signal })));
    launches.push({
      write() { child.send({ frame: correlatedSuccess(request) }, () => {}); },
      release() {
        if (!child.connected) return;
        if (terminate) child.kill('SIGTERM');
        else child.send({ exit: 1 }, () => {});
      },
    });
    return child;
  }

  childProcess.spawn = function (file, ...args) {
    if (file !== carrier) return spawn.call(this, file, ...args);
    const index = launches.length;
    if (index === 1) return holdRealClose(spawn.call(this, file, ...args), index);
    if (index === 2 || index === 3) return replaySuccess(index, index === 3);
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
    else launches[index].write(action);
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
