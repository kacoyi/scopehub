import childProcess from 'node:child_process';
import { syncBuiltinESMExports } from 'node:module';
import { basename } from 'node:path';
import { Transform } from 'node:stream';

// Withhold one genuine status reply before the preview can release admission.
// The same fixture runs against the Rust writer on Linux and Windows.
export function installPublishedRevisionGate() {
  const spawn = childProcess.spawn;
  let armed = false;
  let release;
  const releaseReply = () => {
    armed = false;
    release?.();
    release = undefined;
  };
  const notify = message => {
    if (process.connected) process.send(message, () => {});
  };
  process.once('disconnect', releaseReply);
  process.on('message', message => {
    if (message === 'arm-published-revision-gate') {
      armed = true;
      notify('published-revision-gate-armed');
    } else if (message === 'release-published-revision-gate' || message === 'close') {
      releaseReply();
    }
  });
  childProcess.spawn = (...args) => {
    const child = spawn(...args);
    if (!['workspace_bridge', 'workspace_bridge.exe'].includes(basename(args[0])) || !child.stdout) return child;
    let buffer = Buffer.alloc(0);
    const gate = new Transform({
      transform(chunk, _, callback) {
        buffer = Buffer.concat([buffer, chunk]);
        drain(callback);
      },
      flush(callback) {
        callback(null, buffer);
      },
    });
    function drain(callback) {
      for (let end = buffer.indexOf(10); end !== -1; end = buffer.indexOf(10)) {
        const line = buffer.subarray(0, end + 1);
        buffer = buffer.subarray(end + 1);
        if (armed && isRevisionStatus(line)) {
          armed = false;
          release = () => {
            gate.push(line);
            drain(callback);
          };
          notify('published-revision-reply-held');
          return;
        }
        gate.push(line);
      }
      callback();
    }
    child.stdout.pipe(gate);
    child.stdout = gate;
    return child;
  };
  syncBuiltinESMExports();
}

function isRevisionStatus(line) {
  const frame = JSON.parse(line);
  return typeof frame.ok === 'boolean' && typeof frame.request_id === 'string'
    && typeof frame.result?.available === 'boolean' && Object.keys(frame.result).length === 1;
}
