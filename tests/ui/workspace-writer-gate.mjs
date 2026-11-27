import childProcess from 'node:child_process';
import { syncBuiltinESMExports } from 'node:module';
import { basename } from 'node:path';
import { Transform } from 'node:stream';

// Withhold one genuine status reply before the preview can release admission.
// The same fixture runs against the Rust writer on Linux and Windows.
export function installPublishedRevisionGate() {
  const spawn = childProcess.spawn;
  let active;
  const releaseReply = () => {
    const held = active;
    active = undefined;
    held?.release?.();
  };
  const notify = (type, requestId) => {
    if (process.connected) process.send({ type, requestId }, () => {});
  };
  process.once('disconnect', releaseReply);
  process.on('message', message => {
    if (message?.type === 'arm-published-revision-gate' && !active) {
      active = { requestId: message.requestId };
      notify('published-revision-gate-armed', active.requestId);
    } else if (message === 'close'
        || message?.type === 'release-published-revision-gate' && active?.requestId === message.requestId) {
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
        if (active && !active.release && isRevisionStatus(line, active.requestId)) {
          active.release = () => {
            gate.push(line);
            drain(callback);
          };
          notify('published-revision-reply-held', active.requestId);
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

function isRevisionStatus(line, requestId) {
  const frame = JSON.parse(line);
  return frame.request_id === requestId && frame.ok === true && typeof frame.result?.available === 'boolean';
}
