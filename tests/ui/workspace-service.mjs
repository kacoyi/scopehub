import { spawn } from 'node:child_process';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ui = fileURLToPath(new URL('../../apps/desktop/ui/', import.meta.url));
const vite = pathToFileURL(join(ui, 'node_modules/vite/dist/node/index.js')).href;
const writerGate = new URL('./workspace-writer-gate.mjs', import.meta.url).href;

const service = `
  const [entry, mode, port, gate] = process.argv.slice(1);
  const shutdown = Promise.withResolvers();
  let closing = !process.connected;
  function requestClose() {
    closing = true;
    shutdown.resolve();
  }
  process.once('disconnect', requestClose);
  process.on('message', message => {
    if (message === 'close') requestClose();
  });

  let server;
  async function start() {
    if (closing) return;
    if (gate) (await import(gate)).installPublishedRevisionGate();
    const { createServer } = await import(entry);
    if (closing) return;
    server = await createServer({ mode,
      server: { host: '127.0.0.1', port: Number(port), strictPort: true, watch: null, hmr: false, ws: false },
    });
    if (closing) return;
    await server.listen();
    if (!closing && process.connected) {
      process.send(server.httpServer.address().port, error => { if (error) requestClose(); });
    }
  }

  try {
    await start();
    if (!closing) await shutdown.promise;
  } finally {
    try {
      await server?.close();
    } finally {
      if (process.connected) process.disconnect();
    }
  }
`;

export function spawnWorkspaceService(profile, { port = 0, gateStatus = false, entry = vite } = {}) {
  return spawn(process.execPath, ['--input-type=module', '--eval', service, entry, 'ubuntu-workspace',
    String(port), gateStatus ? writerGate : ''], {
    cwd: ui,
    env: {
      ...process.env,
      SCOPEHUB_WORKSPACE_PROFILE_ROOT: profile.directory,
      SCOPEHUB_WORKSPACE_PROFILE_ID: profile.id,
      SCOPEHUB_WORKSPACE_ID: profile.workspaceId ?? 'main',
    },
    stdio: ['ignore', 'pipe', 'pipe', 'ipc'],
  });
}

export async function closeWorkspaceService(child, closed) {
  if (child.connected) child.send('close', () => {});
  const grace = setTimeout(() => child.kill('SIGKILL'), 7_000);
  try { return await closed; }
  finally { clearTimeout(grace); }
}
