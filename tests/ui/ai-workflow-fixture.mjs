import { execFile, spawn } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { on, once } from 'node:events';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { promisify } from 'node:util';
import { expect } from '@playwright/test';

const exec = promisify(execFile);
const root = fileURLToPath(new URL('../../', import.meta.url));
const ui = join(root, 'apps/desktop/ui');
const vite = pathToFileURL(join(ui, 'node_modules/vite/dist/node/index.js')).href;
const writerGate = new URL('./workspace-writer-gate.mjs', import.meta.url).href;
const workspaceBridge = join(root, 'target', 'debug', process.platform === 'win32' ? 'workspace_bridge.exe' : 'workspace_bridge');
const service = `
  if (process.argv[2]) (await import(process.argv[2])).installPublishedRevisionGate();
  const { createServer } = await import(process.argv[1]);
  const server = await createServer({ mode: 'ubuntu-workspace',
    server: { host: '127.0.0.1', port: 0, strictPort: true, watch: null, hmr: false, ws: false },
  });
  process.on('message', async message => {
    if (message !== 'close') return;
    await server.close();
    process.disconnect();
  });
  await server.listen();
  process.send(server.httpServer.address().port);
`;

export const workspaceEndpoint = '/__scopehub/workspace';
export const credentialEndpoint = '/__scopehub/model-credential';

export async function installBrowserNetworkGuard(context, origin) {
  const browserSends = [];
  await context.route(url => url.origin !== origin, route => {
    browserSends.push(new URL(route.request().url()).origin);
    return route.abort();
  });
  return () => expect(browserSends, 'Browser requests use only the local workspace transport').toEqual([]);
}

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

export async function seedDisplayOnlyChart(profile, revision, bar) {
  const create = exec(workspaceBridge, [profile.directory, profile.id, 'main'], { timeout: 12_000 });
  create.child.stdin.end();
  await create;

  // Independent published CSV fixture. The real registry resolves its display
  // entitlement and verifies its frozen chart. The fixture has no model grant.
  const bars = [{ bar, locators: [{ record_number: 1, line_start: 2, line_end: 2 }] }];
  const series = {
    instrument_kind: 'equity', venue: 'FIXTURE', timeframe: '1d', calendar_id: 'fixture-calendar',
    session_scope: 'regular', timezone: 'UTC', quantity_unit: 'shares', price_adjustment: 'raw', roll_method: null,
  };
  const csv = Buffer.from([
    'Date,Symbol,Open,High,Low,Close,Volume',
    [bar.trading_day, 'FIX', bar.open, bar.high, bar.low, bar.close, bar.volume].join(','),
    '',
  ].join('\n'));
  const source = `display-only-${revision}`;
  const digest = createHash('sha256').update(csv).digest('hex');
  const db = new DatabaseSync(join(profile.directory, 'workspace.sqlite3'));
  try {
    db.exec('PRAGMA foreign_keys = ON; BEGIN');
    db.prepare('INSERT INTO reviewed_import_mappings VALUES (?, ?, ?, ?)').run(source, 1, 'a'.repeat(64), '{}');
    db.prepare('INSERT INTO local_dataset_source_blobs VALUES (?, ?)').run(digest, csv);
    db.prepare('INSERT INTO local_dataset_revisions VALUES (?, ?, ?, ?, ?, ?, ?)').run(
      revision, source, 'display-only.csv', digest, 1, 'b'.repeat(64),
      JSON.stringify({ symbol: 'FIX', series, source_record_count: 1, bars }),
    );
    db.exec('COMMIT');
  } finally {
    db.close();
  }
}

export function persistedRows(profile) {
  const db = new DatabaseSync(join(profile.directory, 'workspace.sqlite3'), { readOnly: true });
  try {
    db.exec('BEGIN');
    const tables = db.prepare("SELECT name FROM sqlite_schema WHERE type = 'table' ORDER BY name").all();
    return Object.fromEntries(tables.map(({ name }) => {
      const identifier = '"' + name.replaceAll('"', '""') + '"';
      const rows = db.prepare(`SELECT * FROM ${identifier}`).all().map(row => JSON.stringify(row)).sort();
      return [name, createHash('sha256').update(JSON.stringify(rows)).digest('hex')];
    }));
  } finally {
    db.close();
  }
}

export async function withWorkspace(browser, profile, use, protectedValues = [], gateStatus = false) {
  const child = spawn(process.execPath, ['--input-type=module', '--eval', service, vite, ...(gateStatus ? [writerGate] : [])], {
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
      const assertNoBrowserSends = await installBrowserNetworkGuard(context, origin);
      const page = await context.newPage();
      page.on('console', message => { keyLogged ||= protectedValues.some(value => message.text().includes(value)); });
      await page.goto(origin);
      const post = (path, frame, authorized = true, padding = 0) => fetch(origin + path, {
        method: 'POST', signal: AbortSignal.timeout(8_000),
        headers: {
          Origin: origin, 'Content-Type': 'application/json',
          ...(authorized ? { Authorization: 'Bearer ' + capability } : {}),
        },
        body: JSON.stringify(frame) + ' '.repeat(padding),
      });
      const dispatch = async (frame, padding = 0) => (await post(workspaceEndpoint, frame, true, padding)).json();
      const dispatchCredential = async (frame, authorized) => {
        const response = await post(credentialEndpoint, frame, authorized);
        return { status: response.status, cacheControl: response.headers.get('cache-control'), body: await response.json() };
      };
      const holdRevisionRead = async () => {
        const waitFor = async message => {
          for await (const [value] of on(child, 'message', { signal: AbortSignal.timeout(8_000) })) {
            if (value === message) return;
          }
        };
        const armed = waitFor('published-revision-gate-armed');
        const held = waitFor('published-revision-reply-held');
        child.send('arm-published-revision-gate');
        await Promise.all([armed, held]);
        return () => child.send('release-published-revision-gate');
      };
      await use(page, dispatch, dispatchCredential, gateStatus ? holdRevisionRead : undefined);
      assertNoBrowserSends();
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
        responseMode: typeof responseMode === 'function' ? 'scripted' : responseMode,
      };
      traffic.push(observed);
      if (responseMode === 'hold') {
        response.once('close', () => { observed.closedWithoutReply = !response.writableEnded; });
        return;
      }
      if (typeof responseMode === 'function') {
        response.once('close', () => { observed.closedWithoutReply = !response.writableEnded; });
        Promise.resolve(responseMode(body)).then(message => {
          if (response.destroyed) return;
          response.writeHead(200, { 'Content-Type': 'application/json' });
          response.end(JSON.stringify({ model: 'synthetic-peer:served', message, done: true }));
        });
        return;
      }
      const json = responseMode === 'unavailable'
        ? { error: 'synthetic-peer-internal-error' }
        : { model: 'synthetic-peer:served', message: { role: 'assistant', content:
          typeof responseMode === 'object' ? responseMode.text : `Synthetic peer reply ${traffic.length}` }, done: true };
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
