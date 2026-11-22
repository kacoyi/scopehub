import { execFile } from 'node:child_process';
import { once } from 'node:events';
import { readFile, stat } from 'node:fs/promises';
import { createServer } from 'node:net';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import { expect, test } from '@playwright/test';
import { buildPreview, withProfile, workspaceEndpoint } from './ai-workflow-fixture.mjs';
import { spawnWorkspaceService } from './workspace-service.mjs';

const exec = promisify(execFile);
const root = fileURLToPath(new URL('../../', import.meta.url));
const python = join(root, '.venv', ...(process.platform === 'win32' ? ['Scripts', 'python.exe'] : ['bin', 'python']));
const writerProbe = join(root, 'tests/ubuntu-preview/workspace_test_writer.py');
const vite = new URL('../../apps/desktop/ui/node_modules/vite/dist/node/index.js', import.meta.url).href;

// Keep the real listen call in flight from the service's perspective after
// Vite has opened the port and started the real workspace writer.
const startupEntry = 'data:text/javascript,' + encodeURIComponent(`
  import { createServer as configure } from ${JSON.stringify(vite)};
  import { once } from 'node:events';
  export async function createServer(options) {
    const server = await configure(options);
    const listen = server.listen.bind(server);
    server.listen = async () => {
      const result = await listen();
      const disconnected = once(process, 'disconnect');
      process.send(server.httpServer.address().port);
      await disconnected;
      return result;
    };
    return server;
  }
`);

test.beforeAll(async ({}, testInfo) => {
  testInfo.setTimeout(900_000);
  await buildPreview();
});

for (const stop of ['ordinary close', 'disconnect after startup', 'disconnect during listen']) {
  test(`workspace service releases the Vite listener and Rust writer on ${stop}`, async ({}, testInfo) => {
    await withProfile(async profile => {
      const child = spawnWorkspaceService(profile, { entry: stop === 'disconnect during listen' ? startupEntry : undefined });
      // A local IPC disconnect can prevent ChildProcess's close event from firing.
      const exited = once(child, 'exit');
      const observation = { stop, service: child.pid };
      let diagnostic = '';
      for (const stream of [child.stdout, child.stderr]) {
        stream.setEncoding('utf8');
        stream.on('data', chunk => { diagnostic = (diagnostic + chunk).slice(-4000); });
      }
      try {
        const [port] = await Promise.race([
          once(child, 'message', { signal: AbortSignal.timeout(15_000) }),
          exited.then(([code, signal]) => { throw new Error(`Service exited (${code ?? signal}). ${diagnostic}`); }),
        ]);
        observation.port = port;
        const handoff = join(profile.directory, `workspace-access-${port}.html`);
        let capability;
        await expect.poll(async () => {
          const html = await readFile(handoff, 'utf8').catch(error => {
            if (error.code === 'ENOENT') return '';
            throw error;
          });
          capability = html.match(new RegExp(`http://127[.]0[.]0[.]1:${port}/#workspace=([a-f0-9]{64})`))?.[1];
          return Boolean(capability);
        }, { timeout: 12_000 }).toBe(true);
        const origin = `http://127.0.0.1:${port}`;
        const response = await fetch(origin + workspaceEndpoint, {
          method: 'POST',
          headers: { Origin: origin, 'Content-Type': 'application/json', Authorization: `Bearer ${capability}` },
          body: JSON.stringify({ type: 'snapshot' }),
          signal: AbortSignal.timeout(5_000),
        });
        expect(response.status).toBe(200);
        expect(await response.json()).toMatchObject({ ok: true, result: { workspace: { id: 'main', revision: 0 } } });
        const { stdout } = await exec(python, probeArgs(child), { timeout: 5_000 });
        observation.writer = Number(stdout.trim());
        const observe = async () => ({
          service: alive(child.pid),
          writer: alive(observation.writer),
          listening: !(await canBind(port)),
        });
        observation.before = await observe();
        expect(observation.before).toEqual({ service: true, writer: true, listening: true });

        if (stop === 'ordinary close') child.send('close');
        else child.disconnect();

        await expect.poll(async () => {
          observation.after = await observe();
          return observation.after;
        }, { timeout: 5_000 }).toEqual({ service: false, writer: false, listening: false });
        observation.exit = await exited;
        observation.handoffRetained = await stat(handoff).then(() => true, error => {
          if (error.code === 'ENOENT') return false;
          throw error;
        });
        observation.ipcSendFailed = diagnostic.includes('ERR_IPC_CHANNEL_CLOSED');
        expect(observation.exit).toEqual([0, null]);
        expect(observation.handoffRetained).toBe(false);
      } finally {
        try {
          if (child.exitCode === null && child.signalCode === null) {
            if (process.platform === 'win32') {
              if (observation.writer && alive(observation.writer)) {
                await exec(python, [...probeArgs(child), 'kill', String(observation.writer)], { timeout: 5_000 });
              }
              child.kill('SIGKILL');
            } else {
              // Let Vite reap its own writer even on a failing regression.
              child.kill('SIGTERM');
            }
          }
        } finally {
          const grace = setTimeout(() => child.kill('SIGKILL'), 5_000);
          try {
            observation.cleanupExit = await exited;
            if (observation.writer) observation.writerAfterCleanup = alive(observation.writer);
            if (observation.port) observation.portReleasedAfterCleanup = await canBind(observation.port);
          } finally {
            clearTimeout(grace);
            console.info('Workspace service lifecycle:', JSON.stringify(observation));
            await testInfo.attach('workspace-service-lifecycle', {
              body: JSON.stringify(observation), contentType: 'application/json',
            });
          }
        }
      }
    });
  });
}

function probeArgs(child) {
  return ['-I', writerProbe, String(process.pid), String(child.pid)];
}

function alive(pid) {
  try { return process.kill(pid, 0); }
  catch (error) {
    if (error.code === 'ESRCH') return false;
    throw error;
  }
}

async function canBind(port) {
  const socket = createServer().listen(port, '127.0.0.1');
  try {
    await once(socket, 'listening');
  } catch (error) {
    if (error.code === 'EADDRINUSE') return false;
    throw error;
  }
  await new Promise((resolve, reject) => socket.close(error => error ? reject(error) : resolve()));
  return true;
}
