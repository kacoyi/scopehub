import { execFile, spawn } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { once } from 'node:events';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { promisify } from 'node:util';
import { expect } from '@playwright/test';

const exec = promisify(execFile);
const root = fileURLToPath(new URL('../../', import.meta.url));
const ui = join(root, 'apps/desktop/ui');
const vite = pathToFileURL(join(ui, 'node_modules/vite/dist/node/index.js')).href;
const workspaceBridge = join(root, 'target', 'debug', process.platform === 'win32' ? 'workspace_bridge.exe' : 'workspace_bridge');
const service = `
  const { createServer } = await import(process.argv[1]);
  const server = await createServer({ mode: 'ubuntu-workspace',
    server: { host: '127.0.0.1', port: 0, strictPort: true, watch: null, hmr: false, ws: false },
  });
  process.once('message', async () => {
    await server.close();
    process.disconnect();
  });
  await server.listen();
  process.send(server.httpServer.address().port);
`;

export const workspaceEndpoint = '/__scopehub/workspace';
export const credentialEndpoint = '/__scopehub/model-credential';

export async function buildPreview() {
  await exec('cargo', ['build', '--locked', '--target-dir', 'target',
    '-p', 'scopehub-persistence', '-p', 'scopehub-worker-host', '--bins', '--example', 'history_bridge',
  ], { cwd: root, env: { ...process.env, CARGO_BUILD_JOBS: '2' }, timeout: 875_000 });
}

export async function withProfile(use) {
  const tempRoot = process.platform === 'linux' ? '/var/tmp' : tmpdir();
  const temp = await mkdtemp(join(tempRoot, 'scopehub-ai-text-'));
  const directory = join(temp, 'profile');
  try {
    await exec(workspaceBridge, ['--prepare-profile-root', directory], { timeout: 12_000 });
    await use({ directory, id: `synthetic-ai-${randomUUID()}` });
  } finally {
    await rm(temp, { recursive: true, force: true });
  }
}

export async function withWorkspace(browser, profile, use, protectedValues = []) {
  const child = spawn(process.execPath, ['--input-type=module', '--eval', service, vite], {
    cwd: ui,
    env: {
      ...process.env,
      SCOPEHUB_WORKSPACE_PROFILE_ROOT: profile.directory,
      SCOPEHUB_WORKSPACE_PROFILE_ID: profile.id,
      SCOPEHUB_WORKSPACE_ID: 'main',
    },
    stdio: ['ignore', 'pipe', 'pipe', 'ipc'],
  });
  const closed = once(child, 'close');
  let output = '';
  let keyLogged = false;
  for (const stream of [child.stdout, child.stderr]) {
    stream.setEncoding('utf8');
    stream.on('data', chunk => {
      const next = output + chunk;
      keyLogged ||= protectedValues.some(value => next.includes(value));
      output = next.slice(-4000);
    });
  }
  try {
    const [port] = await Promise.race([
      once(child, 'message', { signal: AbortSignal.timeout(15_000) }),
      closed.then(([code, signal]) => { throw new Error(`Preview exited (${code ?? signal}).`); }),
    ]);
    const origin = `http://127.0.0.1:${port}`;
    const handoff = join(profile.directory, `workspace-access-${port}.html`);
    let capability;
    await expect.poll(async () => {
      const document = await readFile(handoff, 'utf8').catch(error => {
        if (error.code === 'ENOENT') return '';
        throw error;
      });
      const link = /href="(http:\/\/127\.0\.0\.1:\d+\/#workspace=([a-f0-9]{64}))"/.exec(document);
      if (link && new URL(link[1]).origin === origin) capability = link[2];
      return capability !== undefined;
    }, { timeout: 12_000, message: 'The Rust-backed preview issues a private workspace handoff' }).toBe(true);
    const context = await browser.newContext({ viewport: { width: 1366, height: 900 } });
    try {
      await context.addInitScript(({ origin, capability }) => {
        if (location.origin === origin && window === window.top) {
          history.replaceState(history.state, '', '#workspace=' + capability);
        }
      }, { origin, capability });
      const browserSends = [];
      await context.route('**/*', route => {
        if (new URL(route.request().url()).origin === origin) return route.continue();
        browserSends.push(new URL(route.request().url()).origin);
        return route.abort();
      });
      const page = await context.newPage();
      page.on('console', message => { keyLogged ||= protectedValues.some(value => message.text().includes(value)); });
      await page.goto(origin);
      const post = (path, frame, authorized = true) => fetch(origin + path, {
        method: 'POST', signal: AbortSignal.timeout(8_000),
        headers: {
          Origin: origin, 'Content-Type': 'application/json',
          ...(authorized ? { Authorization: 'Bearer ' + capability } : {}),
        },
        body: JSON.stringify(frame),
      });
      const dispatch = async frame => (await post(workspaceEndpoint, frame)).json();
      const dispatchCredential = async (frame, authorized) => {
        const response = await post(credentialEndpoint, frame, authorized);
        return { status: response.status, cacheControl: response.headers.get('cache-control'), body: await response.json() };
      };
      await use(page, dispatch, dispatchCredential);
      expect(browserSends, 'Browser requests use only the local workspace transport').toEqual([]);
      expect(output.includes(capability), 'The preview never logs the workspace capability').toBe(false);
    } finally {
      await context.close();
    }
  } finally {
    if (child.connected) child.send('close', () => {});
    const grace = setTimeout(() => child.kill('SIGKILL'), 7_000);
    try {
      const [code] = await closed;
      expect(code, 'The owned preview exits cleanly').toBe(0);
      expect(keyLogged, 'The preview and browser console never log a synthetic API key').toBe(false);
    } finally {
      clearTimeout(grace);
    }
  }
}

export async function withPeer(use) {
  const traffic = [];
  let responseMode = 'success';
  const server = createServer((request, response) => {
    const chunks = [];
    request.on('data', chunk => chunks.push(chunk));
    request.on('end', () => {
      const bytes = Buffer.concat(chunks);
      let body;
      try { body = JSON.parse(bytes); } catch { body = null; }
      const observed = {
        method: request.method,
        path: request.url,
        contentType: request.headers['content-type'],
        authorizationPresent: request.headers.authorization !== undefined,
        body,
        sha256: createHash('sha256').update(bytes).digest('hex'),
        responseMode,
      };
      traffic.push(observed);
      if (responseMode === 'hold') {
        response.once('close', () => { observed.closedWithoutReply = !response.writableEnded; });
        return;
      }
      const json = responseMode === 'unavailable'
        ? { error: 'synthetic-peer-internal-error' }
        : { model: 'synthetic-peer:served', message: { role: 'assistant', content: `Synthetic peer reply ${traffic.length}` }, done: true };
      response.writeHead(responseMode === 'unavailable' ? 503 : 200, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify(json));
    });
  }).listen(0, '127.0.0.1');
  await once(server, 'listening');
  try {
    await use({
      origin: `http://127.0.0.1:${server.address().port}`,
      traffic,
      respond: mode => { responseMode = mode; },
    });
  } finally {
    server.closeAllConnections();
    await new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
  }
}
