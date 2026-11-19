import { execFile, spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { once } from 'node:events';
import { mkdtemp, rm, stat } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import { expect, test } from '@playwright/test';
import { control } from '../ui/workspace-controls.mjs';

const exec = promisify(execFile);
const root = fileURLToPath(new URL('../../', import.meta.url));
const ui = join(root, 'apps/desktop/ui');
const vite = join(ui, 'node_modules/vite/bin/vite.js');
const python = join(root, '.venv', ...(process.platform === 'win32' ? ['Scripts', 'python.exe'] : ['bin', 'python']));
const workspaceId = 'synthetic-workspace';
const endpoint = '/__scopehub/workspace';
const snapshot = { type: 'snapshot' };

test.beforeAll(async ({}, testInfo) => {
  testInfo.setTimeout(300_000);
  await exec('cargo', [
    'build', '--locked', '-p', 'scopehub-persistence', '--bin', 'workspace_bridge',
    '-p', 'scopehub-worker-host', '--example', 'history_bridge', '--target-dir', 'target',
  ], { cwd: root, timeout: 295_000 });
});

test('fresh browsers restore committed timeframe and eligible undo after service restarts', async ({ browser }, testInfo) => {
  const profile = await makeProfile();
  const port = await freePort();
  const observation = [];
  try {
    await inService(port, profile, 'ubuntu-workspace', async server => {
      const writer = await writerPid(server);
      await inFreshBrowser(browser, server, async (page, initial) => {
        expect(state(initial)).toEqual({ revision: 0, timeframe: '1m', undo: null });
        await expect(control(page, '1m')).toHaveAttribute('aria-pressed', 'true');
        await expect(control(page, 'Undo timeframe change')).toBeDisabled();
        const reply = await act(page, '5m', 'chart.setTimeframe');
        expect(reply.result.outcome).toMatchObject({
          state: 'committed', result: { timeframe: '5m', revision: 1 }, receipt: { undoable: true },
        });
        await expect(control(page, '5m')).toHaveAttribute('aria-pressed', 'true');
        expect(await writerPid(server)).toBe(writer);
        observation.push({ visit: 'first', vite: server.pid, writer, receipt: reply.result.outcome.receipt.operation_id });
      });
    });
    await expect.poll(() => alive(observation[0].writer)).toBe(false);
    expect((await stat(join(profile.root, 'workspace.sqlite3'))).size).toBeGreaterThan(0);

    await inService(port, profile, 'ubuntu-workspace', async server => {
      const writer = await writerPid(server);
      expect(server.pid).not.toBe(observation[0].vite);
      expect(writer).not.toBe(observation[0].writer);
      await inFreshBrowser(browser, server, async (page, initial) => {
        expect(state(initial)).toEqual({
          revision: 1, timeframe: '5m',
          undo: { operation_id: observation[0].receipt, committed_workspace_revision: 1 },
        });
        await expect(control(page, '5m')).toHaveAttribute('aria-pressed', 'true');
        const undo = control(page, 'Undo timeframe change');
        await expect(undo).toBeEnabled();
        await undo.focus();
        const response = page.waitForResponse(mutationResponse('workspace.undo'));
        await page.keyboard.press('Enter');
        const accepted = await response;
        expect(accepted.request().postDataJSON().intent.arguments).toEqual({ operation_id: observation[0].receipt });
        const inverse = (await accepted.json()).result.outcome;
        expect(inverse).toMatchObject({
          state: 'committed', result: { timeframe: '1m', revision: 2 }, receipt: { undoable: false },
        });
        expect(inverse.receipt.operation_id).not.toBe(observation[0].receipt);
        await expect(control(page, '1m')).toHaveAttribute('aria-pressed', 'true');
        await expect(undo).toBeDisabled();
        expect(await writerPid(server)).toBe(writer);
        observation.push({ visit: 'undo', vite: server.pid, writer, receipt: inverse.receipt.operation_id });
      });
    });
    await expect.poll(() => alive(observation[1].writer)).toBe(false);

    await inService(port, profile, 'ubuntu-workspace', async server => {
      const writer = await writerPid(server);
      expect(server.pid).not.toBe(observation[1].vite);
      expect(writer).not.toBe(observation[1].writer);
      await inFreshBrowser(browser, server, async (page, initial) => {
        expect(state(initial)).toEqual({ revision: 2, timeframe: '1m', undo: null });
        await expect(control(page, '1m')).toHaveAttribute('aria-pressed', 'true');
        await expect(control(page, 'Undo timeframe change')).toBeDisabled();
        observation.push({ visit: 'after-undo', vite: server.pid, writer, restored: state(initial) });
      });
    });
    await expect.poll(() => alive(observation[2].writer)).toBe(false);
    await testInfo.attach('workspace-restarts', { body: JSON.stringify(observation), contentType: 'application/json' });
  } finally {
    await rm(profile.root, { recursive: true, force: true });
  }
});

test('the opted-in route binds identity, limits input, and drains cancellation without replaying after EOF', async () => {
  const profile = await makeProfile();
  const port = await freePort();
  try {
    await inService(port, profile, 'ubuntu-workspace', async server => {
      const writer = await writerPid(server);
      const baseline = await post(server, snapshot);
      expect(state(await baseline.json())).toEqual({ revision: 0, timeframe: '1m', undo: null });
      const foreign = await post(server, snapshot, { Origin: 'http://localhost' });
      expect(foreign.status).toBe(403);
      const oversized = await post(server, { type: 'snapshot', padding: 'x'.repeat(16 * 1024) });
      expect(oversized.status).toBe(413);
      const forged = setTimeframe(0, '5m');
      Object.assign(forged.scope, { principal_id: 'forged', client_id: 'forged', profile_root: profile.root, grant: 'all' });
      expect(await (await post(server, forged)).json()).toMatchObject({ ok: false, error: { code: 'INVALID_ARGUMENT' } });
      forged.scope.workspace_id = 'other-workspace';
      expect((await post(server, forged)).status).toBe(403);
      const file = join(profile.root, 'workspace.sqlite3').replaceAll('\\', '/');
      expect((await stat(file)).isFile()).toBe(true);
      const sqlite = await fetch(server.url + '/@fs/' + file.replace(/^\//, ''));
      expect([403, 404]).toContain(sqlite.status);

      await writerPid(server, 'suspend', writer);
      const abort = new AbortController();
      const cancelled = post(server, setTimeframe(0, '5m'), {}, abort.signal).catch(error => error);
      try {
        await expect.poll(async () => (await post(server, snapshot)).status).toBe(429);
        abort.abort();
        expect((await cancelled).name).toBe('AbortError');
        // The browser stopped waiting. The admitted line still owns the producer slot.
        expect((await post(server, snapshot)).status).toBe(429);
      } finally {
        if (alive(writer)) await writerPid(server, 'resume', writer);
      }
      await expect.poll(async () => {
        const reply = await post(server, snapshot);
        return reply.ok ? state(await reply.json())?.revision : undefined;
      }).toBe(1);
      expect(await writerPid(server)).toBe(writer);

      await writerPid(server, 'suspend', writer);
      const inFlight = post(server, setTimeframe(1, '1d'));
      try {
        await expect.poll(async () => (await post(server, snapshot)).status).toBe(429);
      } finally {
        if (alive(writer)) await writerPid(server, 'kill', writer);
      }
      const disconnected = await inFlight;
      expect(disconnected.status).toBe(503);
      expect(await disconnected.json()).toMatchObject({ ok: false, error: { code: 'WORKER_EXITED' } });
      expect((await post(server, snapshot)).status).toBe(503);
    });
    await inService(port, profile, 'ubuntu-workspace', async server => {
      const restored = state(await (await post(server, snapshot)).json());
      expect(restored).toMatchObject({ revision: 1, timeframe: '5m' });
    });
  } finally {
    await rm(profile.root, { recursive: true, force: true });
  }
});

test('ordinary and Ubuntu history startup do not open a writer or dispatch data operations', async ({ browser }) => {
  const profile = await makeProfile();
  try {
    for (const mode of ['development', 'ubuntu-preview']) {
      await inService(await freePort(), profile, mode, async server => {
        const context = await browser.newContext();
        try {
          const page = await context.newPage();
          const requests = observeOperations(page);
          await page.goto(server.url);
          await expect(control(page, 'Open offline sample')).toBeVisible();
          await expect(control(page, '1m')).toHaveAttribute('aria-pressed', 'true');
          await expect(control(page, 'Undo timeframe change')).toHaveCount(0);
          await control(page, '5m').click();
          await expect(control(page, '5m')).toHaveAttribute('aria-pressed', 'true');
          expect(requests).toEqual([]);
        } finally {
          await context.close();
        }
        if (mode === 'ubuntu-preview') {
          const denied = await fetch(server.url + '/__scopehub/operation', {
            method: 'POST', headers: { Origin: server.url, 'Content-Type': 'application/json' },
            body: JSON.stringify({ operation: 'chart.setTimeframe', payload: setTimeframe(0, '5m') }),
          });
          expect(denied.status).toBe(403);
          expect(await denied.json()).toMatchObject({ ok: false, error: { code: 'PERMISSION_DENIED' } });
        }
        expect((await post(server, setTimeframe(0, '5m'))).status).toBe(404);
        await expect(stat(join(profile.root, 'workspace.sqlite3'))).rejects.toMatchObject({ code: 'ENOENT' });
      });
    }
  } finally {
    await rm(profile.root, { recursive: true, force: true });
  }
});

async function makeProfile() {
  return { root: await mkdtemp(join(tmpdir(), 'scopehub-ubuntu-test-')), id: 'synthetic-' + randomUUID() };
}

async function freePort() {
  const server = createServer();
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const port = server.address().port;
  await new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
  return port;
}

async function inService(port, profile, mode, use) {
  const url = 'http://127.0.0.1:' + port;
  const child = spawn(process.execPath, [vite, '--mode', mode, '--host', '127.0.0.1', '--port', String(port), '--strictPort'], {
    cwd: ui,
    env: {
      ...process.env,
      SCOPEHUB_WORKSPACE_PROFILE_ROOT: profile.root,
      SCOPEHUB_WORKSPACE_PROFILE_ID: profile.id,
      SCOPEHUB_WORKSPACE_ID: workspaceId,
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  const closed = once(child, 'close');
  let output = '';
  for (const stream of [child.stdout, child.stderr]) {
    stream.setEncoding('utf8');
    stream.on('data', chunk => { output = (output + chunk).slice(-6000); });
  }
  const server = { url, pid: child.pid };
  try {
    const deadline = Date.now() + 10_000;
    for (;;) {
      if (child.exitCode !== null || child.signalCode !== null || Date.now() > deadline) {
        throw new Error(`Vite failed to start in ${mode}: ${output}`);
      }
      const response = await fetch(url, { signal: AbortSignal.timeout(500) }).catch(() => null);
      if (response?.ok) {
        await response.body.cancel();
        break;
      }
      await response?.body?.cancel();
      await delay(30);
    }
    await use(server);
  } finally {
    child.kill('SIGTERM');
    const grace = setTimeout(() => child.kill('SIGKILL'), 7_000);
    try {
      const [, signal] = await closed;
      expect(signal).not.toBe('SIGKILL');
    } finally {
      clearTimeout(grace);
    }
  }
}

async function writerPid(server, action, observedPid) {
  const args = ['-I', join(root, 'tests/ubuntu-preview/workspace_test_writer.py'), String(server.pid)];
  if (action) args.push(action, String(observedPid));
  const { stdout } = await exec(python, args, { timeout: 5_000 });
  return Number(stdout.trim());
}

function alive(pid) {
  try { process.kill(pid, 0); return true; }
  catch (error) {
    if (error.code === 'ESRCH') return false;
    throw error;
  }
}

function post(server, body, headers = {}, signal = AbortSignal.timeout(10_000)) {
  return fetch(server.url + endpoint, {
    method: 'POST', headers: { Origin: server.url, 'Content-Type': 'application/json', ...headers },
    body: JSON.stringify(body), signal,
  });
}

function state(frame) {
  if (frame.ok !== true) return undefined;
  const { workspace, undo } = frame.result;
  expect(workspace).toMatchObject({ id: workspaceId, instrument_id: null, dataset: null, layers: [] });
  expect(frame.result.selection_revision).toBe(0);
  return { revision: workspace.revision, timeframe: workspace.timeframe, undo };
}

function observeOperations(page) {
  const requests = [];
  page.on('request', request => {
    const path = new URL(request.url()).pathname;
    if (path.startsWith('/__scopehub/')) requests.push(path);
  });
  return requests;
}

async function inFreshBrowser(browser, server, use) {
  const context = await browser.newContext();
  try {
    const page = await context.newPage();
    const requests = observeOperations(page);
    const initial = page.waitForResponse(response =>
      new URL(response.url()).pathname === endpoint && response.request().postDataJSON()?.type === 'snapshot');
    await page.goto(server.url);
    await expect(control(page, 'Open offline sample')).toBeVisible();
    await expect(page.getByText('No data source')).toBeVisible();
    expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
    await use(page, await (await initial).json());
    expect(requests.every(path => path === endpoint)).toBe(true);
  } finally {
    await context.close();
  }
}

function mutationResponse(operation) {
  return response => new URL(response.url()).pathname === endpoint
    && response.request().postDataJSON()?.intent?.operation === operation;
}

async function act(page, name, operation) {
  const operationResponse = page.waitForResponse(mutationResponse(operation));
  await control(page, name).click();
  const accepted = await operationResponse;
  const json = await accepted.json();
  expect(json.result.request_id).toBe(accepted.request().postDataJSON().request_id);
  return json;
}

function setTimeframe(revision, timeframe) {
  return {
    protocol_version: '1.1', type: 'request', message_id: randomUUID(), timestamp_ms: Date.now(), request_id: randomUUID(),
    task_id: null,
    scope: {
      workspace_id: workspaceId, target: { kind: 'chart', object_id: 'main-chart' }, idempotency_key: randomUUID(),
      expected_workspace_revision: revision, expected_selection_revision: 0,
    },
    intent: { operation: 'chart.setTimeframe', arguments: { timeframe } },
  };
}
