import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';

export function startLocalImportBridge(binary, profile, folder) {
  const child = spawn(binary, [profile, folder], { stdio: ['pipe', 'pipe', 'pipe'] });
  const output = createInterface({ input: child.stdout })[Symbol.asyncIterator]();
  let diagnostic = '';
  child.stderr.on('data', data => { diagnostic = (diagnostic + data.toString()).slice(-2048); });
  child.stdin.on('error', error => { diagnostic = String(error); });
  const ended = new Promise(resolve => {
    child.once('error', error => { diagnostic = String(error); });
    child.once('close', resolve);
  });
  let queue = Promise.resolve();
  return {
    request(command, args) {
      const result = queue.then(async () => {
        child.stdin.write(JSON.stringify({ command, args }) + '\n');
        const reply = await output.next();
        if (reply.done) throw new Error(`Local import bridge stopped: ${diagnostic}`);
        return JSON.parse(reply.value);
      });
      queue = result.catch(() => undefined);
      return result;
    },
    async close() {
      child.stdin.end();
      await ended;
    },
    async interrupt() {
      if (!child.kill('SIGKILL')) throw new Error('The local import bridge was not running when interrupted.');
      await ended;
    },
  };
}
