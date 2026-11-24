import { execFile, spawn } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { EventEmitter, once } from 'node:events';
import { chmod, mkdir, mkdtemp, readFile, rm, stat, writeFile } from 'node:fs/promises';
import { createServer as createHttpServer } from 'node:http';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { PassThrough } from 'node:stream';
import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { promisify } from 'node:util';
import { expect, test } from '@playwright/test';
import { control, leaveSample } from '../ui/workspace-controls.mjs';
import { painted } from '../ui/chart-pixels.mjs';
import { startLocalImportBridge } from '../ui/local-import-bridge.mjs';
import { openWorkspacePreview } from '../ui/preview-startup.mjs';
import { busy, future, observeSampleRequests, sampleCatalogCall, sampleHistoryCall } from '../ui/sample-startup.mjs';
import { observeSelectedHistory, operationPath, readMarketAttemptStages, traceHeader, waitForSelectedHistoryTerminal } from '../ui/selected-history.mjs';
import { assertUnsuitableWindowsHandoffsRejected, assertWindowsPrivateHandoff } from './workspace_test_handoff_acl.mjs';
import { captureServiceOutput, observeServiceStartup, startupMessage } from './workspace_service_readiness.mjs';
import { observeSnapshotGate } from './workspace_snapshot_gate.mjs';

const exec = promisify(execFile);
const root = fileURLToPath(new URL('../../', import.meta.url));
const ui = join(root, 'apps/desktop/ui');
const vite = pathToFileURL(join(ui, 'node_modules/vite/dist/node/index.js')).href;
const viteCli = join(ui, 'node_modules/vite/bin/vite.js');
const workspaceBridge = join(root, 'target/debug', process.platform === 'win32' ? 'workspace_bridge.exe' : 'workspace_bridge');
const python = join(root, '.venv', ...(process.platform === 'win32' ? ['Scripts', 'python.exe'] : ['bin', 'python']));
const workspaceId = 'synthetic-workspace';
const endpoint = '/__scopehub/workspace';
const snapshot = { type: 'snapshot' };
// This malformed snapshot checks admission without becoming a competing
// SQLite read when the actual producer is deliberately suspended.
const admissionProbe = { type: 'snapshot', request_id: 1 };
const snapshotGateModule = new URL('./workspace_snapshot_gate.mjs', import.meta.url).href;
const activeCacheSlots = new Set();
const firstHistoryDisconnectHoldMs = 5_500;
const service = `
  const [entry, mode, port, cacheDir, faultEnabled, snapshotWriter, traceStartup] = process.argv.slice(1);
  const shutdown = Promise.withResolvers();
  let closing = !process.connected;
  let interrupted;
  let heldFirstDisconnect = false;
  function cutHistory() {
    interrupted?.destroy();
    interrupted = undefined;
  }
  function requestClose() {
    closing = true;
    shutdown.resolve();
  }
  process.once('disconnect', requestClose);
  process.on('message', message => {
    if (message === 'close') requestClose();
    else if (message === 'cut-history') cutHistory();
    else if (message?.type === 'history-barrier') console.info('Preview history barrier ' + message.id);
  });
  const plugins = faultEnabled ? [{
    name: 'scopehub-test-history-response-interruption',
    enforce: 'pre',
    configureServer(server) {
      server.middlewares.use((request, response, next) => {
        const fault = request.headers['x-scopehub-test-interrupt'];
        if (request.url !== '${operationPath}' || !request.headers['${traceHeader}'] || !fault) return next();
        const end = response.end;
        response.end = function (...args) {
          if (fault === 'hold-success' && response.statusCode === 200) {
            const start = performance.now();
            const timer = setTimeout(() => {
              console.info('Preview history', JSON.stringify({
                id: request.headers['${traceHeader}'], stage: 'held-success', elapsed_ms: Math.round(performance.now() - start),
              }));
              end.apply(response, args);
            }, 5500);
            response.once('close', () => clearTimeout(timer));
            return response;
          }
          if (fault === 'before-headers' && response.statusCode === 200) {
            if (heldFirstDisconnect) return response.destroy();
            heldFirstDisconnect = true;
            // Keep the real history response pending past the default UI assertion window.
            const start = performance.now();
            const timer = setTimeout(() => {
              console.info('Preview history', JSON.stringify({
                id: request.headers['${traceHeader}'], stage: 'held-disconnect', elapsed_ms: Math.round(performance.now() - start),
              }));
              response.destroy();
            }, ${firstHistoryDisconnectHoldMs});
            response.once('close', () => clearTimeout(timer));
            return response;
          }
          if (fault === 'during-body-' + response.statusCode) {
            interrupted = response;
            response.write('{');
            return response;
          }
          return end.apply(this, args);
        };
        next();
      });
    },
  }] : [];
  const snapshotGate = snapshotWriter
    ? (await import('${snapshotGateModule}')).installSnapshotGate('${endpoint}', snapshotWriter)
    : undefined;
  if (snapshotGate) plugins.push(snapshotGate.plugin);
  let server;
  function reportStartup(phase, code) {
    if (traceStartup && process.connected) process.send({ type: '${startupMessage}', phase, code }, () => {});
  }
  async function start() {
    if (closing) return;
    reportStartup('importing');
    const { createServer } = await import(entry);
    if (closing) return;
    reportStartup('configuring');
    // These journeys restart the service explicitly. Leave file watching to interactive Vite.
    server = await createServer({ mode, plugins, cacheDir, server: { host: '127.0.0.1', port: Number(port), strictPort: true, watch: null } });
    if (closing) return;
    if (traceStartup) {
      server.httpServer.once('listening', () => reportStartup('listening'));
      server.httpServer.once('error', error => reportStartup('bind-error', error.code));
    }
    reportStartup('binding');
    await server.listen();
  }
  try {
    await start();
    if (!closing) await shutdown.promise;
  } finally {
    cutHistory();
    snapshotGate?.close();
    try {
      await server?.close();
    } finally {
      if (process.connected) process.disconnect();
    }
  }
`;

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
    expect(alive(observation[0].vite)).toBe(false);
    expect(await portAvailable(port)).toBe(true);
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
        const accepted = await duringResponse(page, 'workspace.undo', () => page.keyboard.press('Enter'));
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
    await profile.close();
  }
});

test('frozen chart shares two visible bars and clears the independent selection', async ({ browser }, testInfo) => {
  const profile = await makeProfile();
  const port = Number(process.env.SCOPEHUB_EVIDENCE_TEST_PORT ?? await freePort());
  const id = 'e6cbffaf-8822-4d60-8652-b1e406a1eb20';
  const a = Date.parse('2026-02-03T00:00:00Z');
  const b = Date.parse('2026-02-04T00:00:00Z');
  const observation = [];
  try {
    await seedFrozenEvidence(profile, id, a, b);
    await inService(port, profile, 'ubuntu-workspace', async server => {
      const denied = await post(server, { type: 'frozen_chart', request_id: randomUUID(), revision_id: id }, { Authorization: '' });
      expect(denied.status).toBe(403);
      await inFreshBrowser(browser, server, async (page, initial) => {
        expect(initial.result).toMatchObject({ selection_revision: 0, chart_evidence: null, workspace: { revision: 0 } });
        await control(page, 'Open published CSV').click();
        const dialog = page.getByRole('dialog', { name: 'Open published CSV' });
        await dialog.getByLabel('Revision ID').fill(id);
        const opened = page.waitForResponse(response => response.request().postDataJSON()?.type === 'frozen_chart');
        await dialog.getByRole('button', { name: 'Open frozen chart' }).click();
        const source = (await (await opened).json()).result;
        expect(source.snapshot).toMatchObject({
          dataset_id: id, dataset_revision: id, from_ms: a, to_ms: b + 1,
          content_hash: independentEvidenceHash(id, a, b),
        });
        await expect(dialog.getByRole('region', { name: 'Frozen local CSV chart' })).toBeVisible();
        await expect.poll(() => painted(page, 'candlestick')).toBeGreaterThan(20);
        const evidence = dialog.getByRole('region', { name: 'Shared chart evidence' });
        const requests = [];
        page.on('request', request => {
          const body = request.postDataJSON();
          if (body?.operation === 'workspace.selectEvidence') requests.push(body);
        });
        const share = async (time, revision) => {
          await evidence.getByLabel('Bar to share').selectOption(String(time));
          const accepted = page.waitForResponse(evidenceResponse('workspace.selectEvidence'));
          await evidence.getByRole('button', { name: 'Share selected bar' }).click();
          const wire = await accepted;
          const body = wire.request().postDataJSON();
          expect(body.expected_selection_revision).toBe(revision - 1);
          expect(body.target).toEqual({ kind: 'chart_interval', snapshot: source.snapshot, range: { from_ms: time, to_ms: time + 1 } });
          const { result } = await wire.json();
          expect(result.selection).toEqual({ workspace_id: workspaceId, selection_revision: revision, target: body.target });
          observation.push({ revision, time, snapshot: source.snapshot.content_hash });
          return body;
        };
        const inspect = async (time, close) => {
          const projected = page.waitForResponse(evidenceResponse('workspace.readChartProjection'));
          await evidence.getByRole('button', { name: 'Inspect shared bar' }).click();
          const reply = (await (await projected).json()).result;
          expect(reply.result).toEqual({
            source_reference: id, revision: id, fields: ['time_ms', 'close'], rows: [{ time_ms: time, close }],
          });
          await expect(evidence.getByText(`Close ${close}`)).toBeVisible();
        };
        const first = await share(a, 1);
        await inspect(a, 31.5);
        const canvas = dialog.locator('.chart canvas').first();
        await canvas.scrollIntoViewIfNeeded();
        const box = await canvas.boundingBox();
        await page.mouse.move(box.x + box.width * 0.94, box.y + box.height * 0.4);
        await page.mouse.move(box.x + box.width * 0.2, box.y + box.height * 0.4, { steps: 10 });
        await expect(dialog.locator('.bar-detail')).toContainText('C 31.5');
        expect(requests).toHaveLength(1);
        const afterHover = (await (await post(server, snapshot)).json()).result;
        expect(afterHover).toMatchObject({
          selection_revision: 1, chart_evidence: { revision_id: id, range: { from_ms: a, to_ms: a + 1 } },
          workspace: { revision: 0 }, undo: null,
        });
        await share(b, 2);
        await inspect(b, 44.25);
        await inFreshBrowser(browser, server, async (_peer, peer) => {
          expect(peer.result).toMatchObject({
            selection_revision: 2, chart_evidence: { revision_id: id, range: { from_ms: b, to_ms: b + 1 } },
            workspace: { revision: 0 }, undo: null,
          });
        });
        const stale = await post(server, { ...first, request_id: randomUUID(), message_id: randomUUID() });
        expect((await stale.json()).result.error.code).toBe('STALE_CONTEXT');
        const cleared = page.waitForResponse(evidenceResponse('workspace.selectEvidence'));
        await evidence.getByRole('button', { name: 'Clear shared selection' }).click();
        const clearReply = await cleared;
        expect(clearReply.request().postDataJSON()).toMatchObject({ expected_selection_revision: 2, target: null });
        expect((await clearReply.json()).result.selection).toEqual({
          workspace_id: workspaceId, selection_revision: 3, target: null,
        });
        await expect(evidence.getByText('No shared selection.')).toBeVisible();
        await expect(evidence.getByRole('button', { name: 'Inspect shared bar' })).toBeDisabled();
        observation.push({ revision: 3, target: null, hoverPreserved: true, stale: 'STALE_CONTEXT' });
      });
    });
    await inService(port, profile, 'ubuntu-workspace', async server => {
      await inFreshBrowser(browser, server, async (page, initial) => {
        expect(initial.result).toMatchObject({ selection_revision: 3, chart_evidence: null, workspace: { revision: 0 }, undo: null });
        await act(page, '5m', 'chart.setTimeframe');
        const afterTimeframe = (await (await post(server, snapshot)).json()).result;
        expect(afterTimeframe).toMatchObject({
          selection_revision: 3, chart_evidence: null, workspace: { revision: 1, timeframe: '5m' },
        });
      });
    });
    await testInfo.attach('shared-evidence-observations', {
      body: JSON.stringify(observation, null, 2), contentType: 'application/json',
    });
  } finally {
    await profile.close();
  }
});

const revocationTest = test.extend({
  localImportBridge: [async ({}, use, testInfo) => {
    const { stdout, stderr } = await exec('cargo', [
      'build', '--locked', '-p', 'scopehub-desktop', '--example', 'local_import_bridge',
      '--features', 'portable-test-bridge', '--target-dir', 'target',
    ], { cwd: root, timeout: 295_000 });
    await testInfo.attach('local-import-bridge-build', { body: stdout + stderr, contentType: 'text/plain' });
    await use(join(root, 'target/debug/examples', process.platform === 'win32' ? 'local_import_bridge.exe' : 'local_import_bridge'));
  }, { timeout: 300_000 }],
});

revocationTest('published revocation removes visible and delayed frozen evidence and leaves an authorized clear', async ({ browser, localImportBridge }, testInfo) => {
  const fixture = await publishRevocationFixture(testInfo, localImportBridge);
  const port = Number(process.env.SCOPEHUB_EVIDENCE_REVOCATION_PORT ?? await freePort());
  const { revoked, entitled, profile, market } = fixture;
  const held = Promise.withResolvers();
  const deliver = Promise.withResolvers();
  try {
    await inService(port, profile, 'ubuntu-workspace', async server => {
      const revision = revoked.revision_id;
      const open = async (page, id, start = true) => {
        await control(page, 'Open published CSV').click();
        const dialog = page.getByRole('dialog', { name: 'Open published CSV' });
        await dialog.getByLabel('Revision ID').fill(id);
        if (start) await control(dialog, 'Open frozen chart').click();
        return dialog;
      };
      const frozen = dialog => dialog.getByRole('region', { name: 'Frozen local CSV chart' });
      const focus = page => page.evaluate(() => window.dispatchEvent(new Event('focus')));
      const action = async (page, scope, button, operation) => {
        const accepted = page.waitForResponse(evidenceResponse(operation));
        await control(scope, button).click();
        return (await accepted).json();
      };
      await inFreshBrowser(browser, server, async page => {
        const baseline = (await act(page, '5m', 'chart.setTimeframe')).result.outcome;
        const dialog = await open(page, revision);
        await expect(frozen(dialog)).toBeVisible();
        const evidence = dialog.getByRole('region', { name: 'Shared chart evidence' });
        expect((await action(page, evidence, 'Share selected bar', 'workspace.selectEvidence')).result.selection.selection_revision).toBe(1);
        await action(page, evidence, 'Inspect shared bar', 'workspace.readChartProjection');
        await expect(evidence.getByText('Close 44.25')).toBeVisible();
        await dialog.locator('details summary').click();
        await expect(dialog.getByRole('table', { name: 'Published source rows' })).toBeVisible();
        await inFreshBrowser(browser, server, async pending => {
          const lateDialog = await open(pending, revision, false);
          await pending.route(`**${endpoint}`, async route => {
            if (route.request().postDataJSON()?.type !== 'frozen_chart') return route.continue();
            const response = await route.fetch();
            held.resolve(await response.json());
            await deliver.promise;
            await route.fulfill({ response });
          }, { times: 1 });
          await control(lateDialog, 'Open frozen chart').click();
          expect((await held.promise).result.revision.revision_id).toBe(revision);

          const peerRevoke = await market('revokeLocalImportRevision', { version: 1, revision_id: revision });
          expect(peerRevoke.revision_id).toBe(revision);
          const refreshed = page.waitForResponse(response => response.request().postDataJSON()?.type === 'snapshot');
          await focus(page);
          expect((await (await refreshed).json()).result.chart_evidence).toEqual({ unavailable: true });
          await expect.soft(evidence.getByText('Close 44.25')).toHaveCount(0, { timeout: 2500 });
          await expect.soft(frozen(dialog)).toHaveCount(0, { timeout: 2500 });
          await expect.soft(dialog.getByText(revoked.source_sha256, { exact: true })).toHaveCount(0, { timeout: 2500 });
          await expect.soft(dialog.getByRole('table', { name: 'Published source rows' })).toHaveCount(0, { timeout: 2500 });

          await focus(pending);
          // Wait for the visible denied state before delivering the formerly valid body.
          await expect.soft(lateDialog.getByText('This frozen chart is unavailable in the current profile.')).toBeVisible({ timeout: 3500 });
          const lateResponse = pending.waitForResponse(response => response.request().postDataJSON()?.type === 'frozen_chart');
          deliver.resolve();
          await lateResponse;
          await expect.soft(frozen(lateDialog)).toHaveCount(0, { timeout: 2500 });

          await inFreshBrowser(browser, server, async (fresh, initial) => {
            expect(initial.result.chart_evidence).toEqual({ unavailable: true });
            expect(initial.result.workspace.revision).toBe(baseline.result.revision);
            expect(initial.result.undo.operation_id).toBe(baseline.receipt.operation_id);
            const workspaceClear = control(fresh, 'Clear shared selection');
            await expect.soft(workspaceClear).toBeVisible({ timeout: 2500 });
            const denied = await open(fresh, revision);
            await expect(denied.getByText('This frozen chart is unavailable in the current profile.')).toBeVisible();
            const modalClear = control(denied, 'Clear shared selection');
            await expect(modalClear).toBeEnabled();
            const accepted = await action(fresh, denied, 'Clear shared selection', 'workspace.selectEvidence');
            expect(accepted.result.selection).toEqual({ workspace_id: 'main', selection_revision: 2, target: null });
            const cleared = (await (await post(server, snapshot)).json()).result;
            expect(cleared.workspace).toEqual(baseline.result);
            expect(cleared.undo.operation_id).toBe(baseline.receipt.operation_id);
            await control(denied, 'Close').click();
            const other = await open(fresh, entitled.revision_id);
            await expect(frozen(other)).toBeVisible();
            const selected = await action(fresh, other, 'Share selected bar', 'workspace.selectEvidence');
            expect(selected.result.selection.target.snapshot.dataset_id).toBe(entitled.revision_id);
            expect(selected.result.selection.selection_revision).toBe(3);
          });
        });
      });
    });
  } finally {
    deliver.resolve();
    await fixture.close();
  }
});

async function publishRevocationFixture(testInfo, binary) {
  const temp = await mkdtemp(join(tmpdir(), 'scopehub-evidence-revocation-'));
  const app = join(temp, 'app');
  const folder = join(temp, 'folder');
  const path = join(app, 'workspace-dev');
  let bridge;
  const close = async () => {
    await bridge?.close();
    await rm(temp, { recursive: true, force: true });
  };
  try {
    await mkdir(app, { mode: 0o700 });
    await mkdir(folder, { mode: 0o700 });
    const sources = [
      ['revoked.csv', 'FIX,30,34,29,31.5,120', 'FIX,42,47,40,44.25,'],
      ['entitled.csv', 'KEEP,60,64,59,61.5,25', 'KEEP,62,67,60,64.25,8'],
    ].map(([name, a, b]) => [name, `Date,Symbol,Open,High,Low,Close,Volume\n2026-02-03,${a}\n2026-02-04,${b}\n`]);
    for (const [name, csv] of sources) await writeFile(join(folder, name), csv);
    bridge = startLocalImportBridge(binary, app, folder);
    const market = async (name, payload) => {
      const frame = await bridge.request('execute_operation', { operation: `market.${name}`, requestId: randomUUID(), payload });
      expect(frame.error, JSON.stringify(frame.error)).toBeUndefined();
      return frame.result;
    };
    const catalog = (await bridge.request('choose_local_csv_folder', {})).result;
    const mapping = {
      ...evidenceSeries, version: 1, revision: 'revocation-fixture', encoding: 'utf-8', delimiter: ',',
      record_kind: 'native_ohlcv', volume_semantics: 'per_bar', timestamp_convention: 'bar_start',
      timestamp_format: '%Y-%m-%d', trading_day_format: '%Y-%m-%d', daily_start_time: '00:00:00',
      columns: { symbol: 'Symbol', timestamp: 'Date', trading_day: 'Date', open: 'Open', high: 'High', low: 'Low', close: 'Close', volume: 'Volume' },
    };
    delete mapping.roll_method;
    const revisions = [];
    for (const [name, csv] of sources) {
      const input = {
        version: 1, folder_id: catalog.folder_id, file_id: catalog.files.find(file => file.relative_path === name).file_id,
        expected_sha256: createHash('sha256').update(csv).digest('hex'),
      };
      await market('previewLocalImport', { ...input, row_limit: 10, mapping });
      const { record } = await market('saveLocalImportMapping', { ...input, row_limit: 10, mapping });
      revisions.push((await market('publishLocalImport', { ...input, review_revision: record.review_revision })).revision);
    }
    const db = new DatabaseSync(join(path, 'workspace.sqlite3'), { readOnly: true });
    const { profile_id: id } = db.prepare('SELECT profile_id FROM profile_binding WHERE singleton = 1').get();
    db.close();
    await testInfo.attach('published-revisions', { body: JSON.stringify(revisions.map(revision => revision.revision_id)), contentType: 'application/json' });
    return { revoked: revisions[0], entitled: revisions[1], profile: { root: path, id, workspaceId: 'main' }, market, close };
  } catch (error) {
    await close();
    throw error;
  }
}

function evidenceResponse(operation) {
  return response => new URL(response.url()).pathname === endpoint
    && response.request().postDataJSON()?.operation === operation;
}

const evidenceSeries = {
  instrument_kind: 'equity', venue: 'FIXTURE', timeframe: '1d', calendar_id: 'fixture-calendar',
  session_scope: 'regular', timezone: 'UTC', quantity_unit: 'shares', price_adjustment: 'raw', roll_method: null,
};

function evidenceBars(a, b) {
  return [
    { time_ms: a, trading_day: '2026-02-03', open: 30, high: 34, low: 29, close: 31.5, volume: 120, is_closed: true },
    { time_ms: b, trading_day: '2026-02-04', open: 42, high: 47, low: 40, close: 44.25, volume: null, is_closed: true },
  ];
}

async function seedFrozenEvidence(profile, id, a, b, fixture = evidenceBars(a, b)) {
  const create = exec(workspaceBridge, [profile.root, profile.id, workspaceId], { timeout: 12_000 });
  create.child.stdin.end();
  await create;
  // Independent fixture inputs. The application reopens and verifies the actual
  // SQLite revision. No operation, authorization or chart reply is stubbed.
  const database = new DatabaseSync(join(profile.root, 'workspace.sqlite3'));
  const csv = Buffer.from(['Date,Symbol,Open,High,Low,Close,Volume', ...fixture.map(bar =>
    [bar.trading_day, 'FIX', bar.open, bar.high, bar.low, bar.close, bar.volume].join(',')), ''].join('\n'));
  const digest = createHash('sha256').update(csv).digest('hex');
  const mappingDigest = 'b'.repeat(64);
  const bars = fixture.map((bar, index) => ({
    bar, locators: [{ record_number: index + 1, line_start: index + 2, line_end: index + 2 }],
  }));
  const sourceKey = 'source-fixture-' + id;
  try {
    database.exec('PRAGMA foreign_keys = ON; BEGIN');
    database.prepare('INSERT INTO reviewed_import_mappings VALUES (?, ?, ?, ?)').run(sourceKey, 1, 'a'.repeat(64), '{}');
    database.prepare('INSERT INTO local_dataset_source_blobs VALUES (?, ?)').run(digest, csv);
    database.prepare('INSERT INTO local_dataset_revisions VALUES (?, ?, ?, ?, ?, ?, ?)').run(
      id, sourceKey, 'frozen-fixture.csv', digest, 1, mappingDigest,
      JSON.stringify({ symbol: 'FIX', series: evidenceSeries, source_record_count: bars.length, bars }),
    );
    database.exec('COMMIT');
  } finally {
    database.close();
  }
}

function independentEvidenceHash(id, a, b) {
  const hash = parts => createHash('sha256').update(Buffer.concat(parts)).digest();
  const int = value => {
    const bytes = Buffer.alloc(8);
    bytes.writeBigInt64BE(BigInt(value));
    return bytes;
  };
  const number = value => {
    const bytes = Buffer.alloc(8);
    bytes.writeDoubleBE(value);
    return bytes;
  };
  const text = value => {
    const utf8 = Buffer.from(value);
    const length = Buffer.alloc(4);
    length.writeUInt32BE(utf8.length);
    return Buffer.concat([length, utf8]);
  };
  const identity = [id, 'scopehub.local-csv', '1d', 'raw', 'regular', 'fixture-calendar', 'shares', id, id, 'UTC'];
  let result = hash([Buffer.from('ScopeHub/history-chain-v1\0'), ...identity.map(text), ...[a, b + 1, 0, b + 1].map(int)]);
  for (const bar of evidenceBars(a, b)) {
    result = hash([
      result, int(bar.time_ms), text(bar.trading_day), ...[bar.open, bar.high, bar.low, bar.close].map(number),
      ...(bar.volume === null ? [Buffer.of(0)] : [Buffer.of(1), number(bar.volume)]), Buffer.of(1),
    ]);
  }
  return result.toString('hex');
}

test('indicator-preview restores all 256 accepted layer references through the Ubuntu workspace', async () => {
  const profile = await makeProfile();
  const revisionId = randomUUID();
  const first = Date.UTC(2026, 1, 3);
  const last = Date.UTC(2026, 1, 4);
  try {
    await seedFrozenEvidence(profile, revisionId, first, last);
    await inService(await freePort(), profile, 'ubuntu-workspace', async server => {
      const frozen = await (await post(server, {
        type: 'frozen_chart', request_id: randomUUID(), revision_id: revisionId,
      })).json();
      expect(frozen.ok).toBe(true);
      const source = frozen.result.snapshot;
      expect(source.content_hash).toBe(independentEvidenceHash(revisionId, first, last));

      // Preload the maximum legal number of active attachments. The fixture
      // represents separately accepted runs with identical inputs and output.
      const digest = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
      const parameters = { period: 2 };
      // Use the canonical key order when hashing the expected artifact.
      const points = [{ time_ms: first, value: null }, { time_ms: last, value: 37.875 }];
      const result = {
        diagnostics: [], markers: [],
        provenance: {
          code_hash: 'd'.repeat(64), dataset_id: revisionId, dataset_revision: source.dataset_revision,
          engine_version: 'scopehub-declarative-3', params_hash: digest(parameters),
        },
        regions: [],
        series: [{ id: 'mean', pane: 'price', points, style: { color: '#123456', width: 2 }, type: 'line' }],
      };
      const artifact = { result, result_hash: digest(result), storage: 'inline' };
      const common = {
        draft_id: randomUUID(), source_snapshot: source, snapshot: source, warmup_from_ms: null,
        resolved_parameters: parameters, artifact, calendar_hash: null,
      };
      const uuid = index => '00000000-0000-4000-8000-' + index.toString(16).padStart(12, '0');
      const layers = Array.from({ length: 256 }, (_unused, index) => ({
        layer_id: uuid(index + 1), version_id: uuid(index + 257), available: true,
      }));
      const database = new DatabaseSync(join(profile.root, 'workspace.sqlite3'));
      try {
        database.exec('PRAGMA foreign_keys = ON; BEGIN');
        const record = database.prepare(`INSERT INTO operation_log
          (id, workspace_id, operation, actor, principal_id, paired_client_id, request_id, committed_at_ms,
            revision, undoable, before_timeframe, after_timeframe)
          VALUES (?, ?, 'indicator.acceptVersion', 'user', ?, '', ?, ?, ?, 1, '1m', '1m')`);
        const version = database.prepare('INSERT INTO indicator_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?)');
        const activate = database.prepare('INSERT INTO indicator_layer_state VALUES (?, ?, 1)');
        const attachment = database.prepare('INSERT INTO indicator_layer_operations VALUES (?, ?, ?, 0, 1)');
        for (const [index, layer] of layers.entries()) {
          const operation = randomUUID();
          const preview = randomUUID();
          record.run(operation, workspaceId, profile.id, randomUUID(), Date.now(), index + 1);
          version.run(workspaceId, layer.layer_id, layer.version_id, preview, randomUUID(), operation, revisionId,
            JSON.stringify({ ...common, task_id: randomUUID(), preview_id: preview }));
          activate.run(workspaceId, layer.layer_id);
          attachment.run(operation, workspaceId, layer.layer_id);
        }
        database.prepare('UPDATE workspaces SET revision = ? WHERE id = ?').run(layers.length, workspaceId);
        database.exec('COMMIT');
      } finally {
        database.close();
      }

      const representative = layers.at(-1);
      const output = await (await post(server, {
        type: 'indicator_accepted_output', request_id: randomUUID(), layer_id: representative.layer_id,
      })).json();
      expect(output).toMatchObject({ ok: true, result: {
        layer_id: representative.layer_id, version_id: representative.version_id, document: { artifact },
      } });
      const list = async () => {
        const response = await post(server, {
          type: 'indicator_accepted_layers', request_id: randomUUID(), revision_id: revisionId,
        });
        const listing = await response.json();
        expect(response.status, JSON.stringify(listing).slice(0, 250)).toBe(200);
        expect(listing).toMatchObject({ ok: true, result: { dataset_id: revisionId } });
        return listing.result.layers;
      };
      expect(await list()).toEqual(layers);

      // Revocation retains the references and uses the longer false flag.
      const revoked = new DatabaseSync(join(profile.root, 'workspace.sqlite3'));
      try {
        revoked.prepare('INSERT INTO local_dataset_display_revocations (revision_id) VALUES (?)').run(revisionId);
      } finally {
        revoked.close();
      }
      expect(await list()).toEqual(layers.map(layer => ({ ...layer, available: false })));
    });
  } finally {
    await profile.close();
  }
});

test('indicator-preview uses the bound sample calendar through the normal chart', async ({ browser }, testInfo) => {
  const profile = await makeProfile();
  const port = await freePort();
  const afterClosure = 1704243600000;
  const lastExpected = 1704200940000;
  const croppedHistory = sampleHistoryCall();
  const dayStart = Date.UTC(2024, 0, 3);
  Object.assign(croppedHistory.payload, { from_ms: dayStart, to_ms: Date.UTC(2024, 0, 4) });
  const gappedRevision = 'scopehub-synthetic-v1:20241013:gapped';
  const gappedHistory = { ...croppedHistory, payload: { ...croppedHistory.payload, expected_revision: gappedRevision } };
  const observation = [];
  try {
    await inService(port, profile, 'ubuntu-workspace', async server => {
      const injected = await post(server, {
        type: 'indicator_sample_preview', command: {}, history: { bars: [], sessions: [] },
      });
      expect(injected.status).toBe(403);
      await injected.body?.cancel();

      await inFreshBrowser(browser, server, async page => {
        await sampleStep(page, server, testInfo, 'open sample for calendar preview', () => sampleButton(page).click());
        const source = await (await post(server, snapshot)).json();
        expect(source.result.source).toMatchObject({ kind: 'sample' });
        await control(page, 'Data sources').click();
        const sources = page.getByRole('complementary', { name: 'Data sources' });
        // UTC Jan 3 starts after the first session. The preceding bar
        // must come from the same immutable revision's earlier warm-up.
        const releaseRoute = await holdSampleHistory(page, dayStart);
        try {
          const historyRequest = await sampleStep(page, server, testInfo, 'read delayed January 3 sample',
            () => sources.getByLabel('Sample date (UTC)').fill('2024-01-03'),
            { catalog: false, historyCall: croppedHistory, bars: 20 });
          const stages = server.historyStages.get(historyRequest.headers()[traceHeader]);
          const hold = stages.find(stage => stage.stage === 'held-success');
          expect(hold?.elapsed_ms, 'Delay of the successful history response').toBeGreaterThan(5_000);
        } finally {
          await releaseRoute();
        }
        const plot = page.getByRole('region', { name: 'Price chart' });
        await sources.getByRole('button', { name: 'Close panel' }).click();
        const indicatorSwitch = plot.getByText('Indicators', { exact: true });
        await indicatorSwitch.click({ timeout: 8_000 });
        const indicator = plot.getByRole('region', { name: 'Indicator preview' });
        let previewCommand;
        const run = async () => {
          await indicator.getByLabel('First visible bar').selectOption(String(afterClosure));
          await indicator.getByLabel('Last visible bar').selectOption(String(afterClosure));
          const ready = page.waitForResponse(async response =>
            new URL(response.url()).pathname === endpoint
            && response.request().postDataJSON()?.type === 'indicator_preview_status'
            && (await response.json()).result?.status === 'ready');
          const submitted = page.waitForRequest(request => new URL(request.url()).pathname === endpoint
            && request.postDataJSON()?.intent?.operation === 'indicator.runPreview');
          await indicator.getByRole('button', { name: 'Run preview' }).click();
          previewCommand = (await submitted).postDataJSON();
          const terminal = (await (await ready).json()).result;
          await expect(indicator.getByText(/Preview layers/)).toBeVisible();
          return terminal;
        };
        const normal = await run();
        expect(normal.event.artifact.result.series[0].points).toEqual([{ time_ms: afterClosure, value: 3500.5 }]);
        expect(normal.warmup_from_ms).toBe(lastExpected);
        expect(normal.source_snapshot.from_ms).toBe(dayStart);
        expect(normal.event.snapshot.warmup_bars).toBe(1);
        expect(normal.calendar_hash).toMatch(/^[a-f0-9]{64}$/);
        await expect(indicator.getByText("The source's scheduled closures add no indicator buckets.")).toBeVisible();
        await expect(indicator.getByText(/expected market buckets are missing/)).toHaveCount(0);
        await indicator.getByText('Preview provenance and values').click();
        await expect(indicator.getByRole('table', { name: 'Indicator preview values' })).toContainText('3500.5');
        observation.push(normal);
        expect(Object.keys(previewCommand.intent.arguments).sort()).toEqual(['draft_id', 'parameters', 'snapshot']);
        const forged = structuredClone(previewCommand);
        forged.request_id = randomUUID();
        forged.message_id = randomUUID();
        forged.scope.idempotency_key = randomUUID();
        forged.scope.target.snapshot.content_hash = 'f'.repeat(64);
        forged.intent.arguments.snapshot.content_hash = 'f'.repeat(64);
        const denied = await (await post(server, forged)).json();
        expect(denied.result?.outcome).toMatchObject({ state: 'failed', error: { code: 'SNAPSHOT_UNAVAILABLE' } });
        await expect(indicator.getByRole('table', { name: 'Indicator preview values' })).toContainText('3500.5');


        await control(page, 'Data sources').click();
        await sampleStep(page, server, testInfo, 'read gapped January 3 sample',
          () => sources.getByLabel('Sample revision').selectOption('gapped'),
          { catalog: false, historyCall: gappedHistory, bars: 20 });
        await expect(sources.getByText(gappedRevision, { exact: true })).toBeVisible();
        await sources.getByRole('button', { name: 'Close panel' }).click();
        if (await plot.locator('.chart-indicators').getAttribute('open') === null) {
          await indicatorSwitch.click({ timeout: 8_000 });
        }
        // Jan 3 has the same observed bars; switching the revision removes the
        // old result even though the chart looks identical in this UTC slice.
        await expect(indicator.getByText(/Preview layers/)).toHaveCount(0);
        const gapped = await run();
        expect(gapped.source_snapshot.dataset_revision).toMatch(/:gapped$/);
        expect(gapped.calendar_hash).toBe(normal.calendar_hash);
        expect(gapped.event.snapshot.content_hash).not.toBe(normal.event.snapshot.content_hash);
        expect(gapped.event.artifact.result.series[0].points).toEqual([{ time_ms: afterClosure, value: null }]);
        await expect(indicator.getByText(/expected market buckets are missing/)).toBeVisible();
        await expect(indicator.getByText(/no verified session schedule/)).toHaveCount(0);
        observation.push(gapped);

        await control(page, 'Data sources').click();
        await sources.getByRole('button', { name: 'Leave sample' }).click();
        await expect(plot.getByRole('region', { name: 'Indicator preview' })).toHaveCount(0);
        await expect.poll(async () => (await (await post(server, {
          type: 'indicator_preview_status', request_id: randomUUID(), task_id: gapped.task_id,
        })).json()).error?.code).toBe('STALE_CONTEXT');
      }, [endpoint, '/__scopehub/operation'], { traceSample: true });
    }, { historyTrace: true, historyFault: true });
    await testInfo.attach('sample-indicator-calendar', { body: JSON.stringify(observation), contentType: 'application/json' });
  } finally {
    await profile.close();
  }
});

test('indicator-preview shows zero volatility for equal closes in a full or cropped window', async ({ browser }) => {
  const profile = await makeProfile();
  const revision = randomUUID();
  const prices = [133.33, 120.21, 120.21].map((close, index) => {
    const time = Date.UTC(2026, 1, 3 + index);
    return { time_ms: time, trading_day: new Date(time).toISOString().slice(0, 10),
      open: close, high: close, low: close, close, volume: 10, is_closed: true };
  });
  try {
    await seedFrozenEvidence(profile, revision, prices[0].time_ms, prices[2].time_ms, prices);
    await inService(await freePort(), profile, 'ubuntu-workspace', async server => {
      await inFreshBrowser(browser, server, async page => {
        await control(page, 'Open published CSV').click();
        const dialog = page.getByRole('dialog', { name: 'Open published CSV' });
        await dialog.getByLabel('Revision ID').fill(revision);
        await dialog.getByRole('button', { name: 'Open frozen chart' }).click();
        const panel = dialog.getByRole('region', { name: 'Indicator preview' });
        await expect(panel.getByLabel('Rolling period')).toHaveValue('2');
        for (const count of [3, 1]) {
          if (count === 1) await panel.getByLabel('First visible bar').selectOption(String(prices[2].time_ms));
          const [reply] = await Promise.all([
            page.waitForResponse(async r => r.url().endsWith(endpoint)
              && r.request().postDataJSON()?.type === 'indicator_preview_status' && (await r.json()).result?.status === 'ready'),
            panel.getByRole('button', { name: 'Run preview' }).click(),
          ]);
          const ready = (await reply.json()).result;
          const values = ready.event.artifact.result.series[1].points;
          expect(values).toHaveLength(count);
          expect(values.at(-1)).toEqual({ time_ms: prices[2].time_ms, value: 0 });
          if (count === 1) expect(ready.warmup_from_ms).toBe(prices[1].time_ms);
          const table = panel.getByRole('table', { name: 'Indicator preview values' });
          if (!await table.isVisible()) await panel.getByText('Preview provenance and values').click();
          await expect(table.locator('tbody tr')).toHaveCount(count);
          await expect(table.locator('tbody tr').last().getByRole('cell').last()).toHaveText('0');
        }
      });
    });
  } finally {
    await profile.close();
  }
});

test('indicator-preview evaluates real frozen chart bytes and isolates failure and cancellation', async ({ browser }, testInfo) => {
  const profile = await makeProfile();
  const port = await freePort();
  const id = randomUUID();
  const other = randomUUID();
  const day = 86_400_000;
  const a = Date.parse('2026-02-03T00:00:00Z');
  const makeBars = (closes, offsets) => closes.map((close, index) => ({
    time_ms: a + offsets[index] * day, trading_day: new Date(a + offsets[index] * day).toISOString().slice(0, 10),
    open: close, high: close, low: close, close, volume: index === 1 ? null : 10 + index, is_closed: true,
  }));
  const bars = makeBars([1, 3, 5], [0, 1, 2]);
  const revised = makeBars([10, 14, 20], [0, 1, 3]);
  const command = () => ({
    protocol_version: '1.1', type: 'request', request_id: randomUUID(),
    message_id: randomUUID(), timestamp_ms: Date.now(),
  });
  let workspaceRevision = 0;
  const mutation = (operation, args, target = { kind: 'chart', object_id: 'main-chart' }) => ({
    ...command(), task_id: null, scope: {
      workspace_id: workspaceId, target, expected_workspace_revision: workspaceRevision,
      expected_selection_revision: 0, idempotency_key: randomUUID(),
    }, intent: { operation, arguments: args },
  });
  const status = task_id => ({ type: 'indicator_preview_status', request_id: randomUUID(), task_id });
  const resultPoints = artifact => artifact.result.series.map(series => series.points.map(point => point.value));
  const reply = async (server, body) => (await (await post(server, body)).json()).result;
  const observation = {};
  let savedLayer;
  let savedOutput;
  try {
    await seedFrozenEvidence(profile, id, a, a + day, bars);
    await seedFrozenEvidence(profile, other, a, a + day, revised);
    await inService(port, profile, 'ubuntu-workspace', async server => {
      await inFreshBrowser(browser, server, async page => {
        await control(page, 'Open published CSV').click();
        const dialog = page.getByRole('dialog', { name: 'Open published CSV' });
        await dialog.getByLabel('Revision ID').fill(id);
        const opened = page.waitForResponse(response => response.request().postDataJSON()?.type === 'frozen_chart');
        await dialog.getByRole('button', { name: 'Open frozen chart' }).click();
        const source = (await (await opened).json()).result.snapshot;
        const chart = dialog.getByRole('region', { name: 'Frozen local CSV chart' });
        const plot = chart.getByRole('img', { name: 'Candlestick and volume chart for FIX' });
        await expect(plot).toBeVisible();
        const initialCanvasCount = await plot.locator('canvas').count();
        const preview = dialog.getByRole('region', { name: 'Indicator preview' });
        const run = async () => {
          const response = page.waitForResponse(async response => response.request().postDataJSON()?.type === 'indicator_preview_status'
            && (await response.json()).result?.status === 'ready');
          await preview.getByRole('button', { name: 'Run preview' }).click();
          const terminal = (await (await response).json()).result;
          expect(terminal.status).toBe('ready');
          await expect(preview.getByText(/Preview layers/)).toBeVisible();
          return terminal;
        };
        const first = await run();
        expect(resultPoints(first.event.artifact)).toEqual([[null, 2, 4], [null, 1, 1]]);
        expect(first.resolved_parameters).toEqual({ period: 2 });
        await expect(preview.getByText('This source has no verified session schedule. Indicator windows restart after timestamp gaps.')).toBeVisible();
        expect(first.event.snapshot).toMatchObject({ dataset_id: id, from_ms: a, to_ms: a + 2 * day + 1, warmup_bars: 0 });
        await expect.poll(() => plot.locator('canvas').count()).toBeGreaterThan(initialCanvasCount);
        await expect(chart.getByText('The indicator layer could not be displayed.')).toHaveCount(0);
        await preview.getByText('Preview provenance and values').click();
        await expect(preview.getByRole('table', { name: 'Indicator preview values' }).getByRole('row').last())
          .toContainText('2026-02-05T00:00:00.000Z41');

        await preview.getByLabel('Rolling period').fill('3');
        const second = await run();
        expect(resultPoints(second.event.artifact)).toEqual([[null, null, 3], [null, null, Math.sqrt(8 / 3)]]);
        expect(second.event.params_hash).not.toBe(first.event.params_hash);
        expect(second.event.snapshot.content_hash).toBe(first.event.snapshot.content_hash);

        await preview.getByLabel('First visible bar').selectOption(String(a + 2 * day));
        const warmed = await run();
        expect(warmed.event.snapshot).toMatchObject({ from_ms: a + 2 * day, to_ms: a + 2 * day + 1, warmup_bars: 2 });
        expect(warmed.warmup_from_ms).toBe(a);
        expect(warmed.event.snapshot.content_hash).not.toBe(second.event.snapshot.content_hash);
        expect(resultPoints(warmed.event.artifact)).toEqual([[3], [Math.sqrt(8 / 3)]]);
        const replayed = await reply(server, status(warmed.task_id));
        expect(replayed).toEqual(warmed);

        // Run another real revision and cancel a distinct staged job before delivery.
        const alternate = await reply(server, { type: 'frozen_chart', request_id: randomUUID(), revision_id: other });
        const target = frozen => ({
          kind: 'chart_interval', snapshot: frozen, range: { from_ms: frozen.from_ms, to_ms: frozen.to_ms },
        });
        const constant = value => ({ op: 'constant', value });
        const close = { op: 'field', field: 'close' };
        const style = { color: '#001122', width: 2 };
        const series = (id, value) => ({ kind: 'line', id, pane: 'price', style, value });
        const draft = await reply(server, mutation('indicator.createDraft', { language: 'declarative', source: {
          parameters: [{ type: 'number', name: 'threshold', default: 12 }],
          outputs: [
            series('mean', { op: 'rolling_mean', value: close, window: { periods: 2 } }),
            series('volume', { op: 'field', field: 'volume' }),
            { kind: 'marker_when', position: 'above', label: 'Above threshold', color: '#001122',
              when: { op: 'gt', left: close, right: { op: 'number_parameter', name: 'threshold' } } },
          ],
        } }));
        expect(draft.outcome.state).toBe('committed');
        const submit = async frozen => {
          const request = mutation('indicator.runPreview', {
            draft_id: draft.outcome.result.draft_id, snapshot: frozen, parameters: [],
          }, target(frozen));
          return (await reply(server, request)).outcome.result.task_id;
        };
        const cancellable = await submit(source);
        const remaining = await submit(alternate.snapshot);
        const cancelled = await reply(server, { ...command(), operation: 'task.cancel', task_id: cancellable });
        expect(cancelled.outcome).toEqual({ status: 'cancelled' });
        expect((await reply(server, status(cancellable))).event.outcome).toEqual({ status: 'cancelled' });
        await expect.poll(async () => (await reply(server, status(remaining))).status).toBe('ready');
        const otherReady = await reply(server, status(remaining));
        expect(resultPoints(otherReady.event.artifact)).toEqual([[null, 12, null], [10, null, 12]]);
        expect(otherReady.event.artifact.result.markers.map(marker => marker.time_ms)).toEqual([a + day, a + 3 * day]);

        const invalid = await reply(server, mutation('indicator.createDraft', { language: 'declarative', source: {
          parameters: [], outputs: [series('overflow', { op: 'multiply', left: constant(1e308), right: constant(1e308) })],
        } }));
        const failed = await reply(server, mutation('indicator.runPreview', {
          draft_id: invalid.outcome.result.draft_id, snapshot: source, parameters: [],
        }, target(source)));
        const failedId = failed.outcome.result.task_id;
        await expect.poll(async () => (await reply(server, status(failedId))).status).toBe('terminal');
        expect((await reply(server, status(failedId))).event.outcome).toMatchObject({
          status: 'failed', error: { code: 'OUTPUT_INVALID' },
        });
        await expect(preview.getByText(/Period 3 · 2 warm-up bars/)).toBeVisible();
        await expect(plot).toBeVisible();
        expect((await reply(server, status(warmed.task_id))).event.artifact).toEqual(warmed.event.artifact);

        const database = new DatabaseSync(join(profile.root, 'workspace.sqlite3'));
        try {
          database.prepare('INSERT INTO local_dataset_display_revocations (revision_id) VALUES (?)').run(other);
        } finally {
          database.close();
        }
        const withheld = await (await post(server, status(remaining))).json();
        expect(withheld).toMatchObject({ ok: false, error: { code: 'PERMISSION_DENIED' } });
        expect(withheld.result).toBeUndefined();
        const stage = async () => {
          const replyFrame = page.waitForResponse(response => response.request().postDataJSON()?.intent?.operation === 'indicator.acceptVersion');
          await preview.getByRole('button', { name: 'Accept preview' }).click();
          const response = await replyFrame;
          const challenge = (await response.json()).result.outcome.challenge;
          expect(challenge).toMatchObject({ task_id: warmed.task_id, workspace_revision: 0, selection_revision: 0 });
          expect(challenge.target.range).toEqual({ from_ms: a + 2 * day, to_ms: a + 2 * day + 1 });
          expect(challenge.parameters_hash).toMatch(/^[0-9a-f]{64}$/);
          return response.request().postDataJSON();
        };
        await stage();
        await preview.getByRole('button', { name: 'Cancel indicator acceptance' }).click();
        await expect(preview.getByRole('button', { name: 'Accept preview' })).toBeEnabled();
        const acceptRequest = await stage();
        const decision = page.waitForResponse(response => response.request().postDataJSON()?.operation === 'confirmation.respond');
        await preview.getByRole('button', { name: 'Confirm indicator acceptance' }).click();
        const committed = (await (await decision).json()).result.outcome;
        expect(committed).toMatchObject({ state: 'committed', receipt: { committed_workspace_revision: 1, undoable: true } });
        savedLayer = committed.result;
        await expect(preview.getByRole('region', { name: 'Accepted indicators' })).toBeVisible();
        await expect(preview.getByText(/Preview layers/)).toHaveCount(0);
        workspaceRevision = 1;

        const outputRequest = () => ({ type: 'indicator_accepted_output', request_id: randomUUID(), layer_id: savedLayer.layer_id });
        savedOutput = await reply(server, outputRequest());
        expect(savedOutput.document.artifact).toEqual(warmed.event.artifact);
        expect(savedOutput.document.snapshot).toEqual(warmed.event.snapshot);
        expect(savedOutput.document.resolved_parameters).toEqual({ period: 3 });
        expect(savedOutput.document.warmup_from_ms).toBe(a);
        const retry = { ...acceptRequest, message_id: randomUUID(), request_id: randomUUID() };
        expect((await reply(server, retry)).outcome).toEqual(committed);
        expect((await reply(server, { ...retry, scope: { ...retry.scope, idempotency_key: randomUUID() } })).outcome)
          .toMatchObject({ state: 'failed', error: { code: 'STALE_CONTEXT' } });

        const nextFailure = await reply(server, mutation('indicator.runPreview', {
          draft_id: invalid.outcome.result.draft_id, snapshot: source, parameters: [],
        }, target(source)));
        const nextFailureId = nextFailure.outcome.result.task_id;
        await expect.poll(async () => (await reply(server, status(nextFailureId))).status).toBe('terminal');
        expect((await reply(server, status(nextFailureId))).event.outcome.status).toBe('failed');
        expect(await reply(server, outputRequest())).toEqual(savedOutput);
        await expect(plot).toBeVisible();

        const accepted = preview.getByRole('region', { name: 'Accepted indicators' });
        const remove = preview.getByRole('button', { name: 'Remove accepted layer' });
        const undo = preview.getByRole('button', { name: 'Undo indicator removal' });
        const undoStages = {};
        const removalStart = performance.now();
        const removalEvents = [];
        const record = (stage, fields = {}) => removalEvents.push({
          stage, elapsed_ms: Math.round(performance.now() - removalStart), ...fields,
        });
        const isLayerMutation = (payload, operation) => payload?.intent?.operation === operation
          && payload.scope?.target?.kind === 'layer' && payload.scope.target.object_id === savedLayer.layer_id;
        const explicitMutations = [];
        const snapshotAttempts = new Map();
        let observingRemovalReads = true;
        const observeRemovalRequests = request => {
          if (new URL(request.url()).pathname !== endpoint) return;
          const payload = request.postDataJSON();
          if (['indicator.remove', 'workspace.undo'].includes(payload?.intent?.operation)) {
            explicitMutations.push(payload);
            record('mutation-dispatched', { request: payload });
          } else if (payload?.type === 'snapshot' && observingRemovalReads && explicitMutations.length > 0) {
            const attempt = snapshotAttempts.size + 1;
            snapshotAttempts.set(request, attempt);
            record('browser-snapshot-dispatched', { attempt });
          }
        };
        const layerMutation = async (operation, action) => {
          const matches = request => new URL(request.url()).pathname === endpoint
            && isLayerMutation(request.postDataJSON(), operation);
          const request = await duringPageEvent(page, 'requestfinished', matches, action, 8_000, matches);
          const response = await request.response();
          return { request: request.postDataJSON(), http: response.status(), body: await response.json() };
        };
        const removalRead = (type, matches) => page.waitForResponse(async response => {
          if (new URL(response.url()).pathname !== endpoint || response.status() !== 200
            || response.request().postDataJSON()?.type !== type) return false;
          const body = await response.json().catch(() => undefined);
          return body?.ok === true && matches(body.result, response.request());
        }, { timeout: 8_000 });
        const readOutput = async () => {
          const request = outputRequest();
          const response = await post(server, request, {}, AbortSignal.timeout(3_000));
          return { request, http: response.status, body: await response.json() };
        };
        const readSnapshot = async (tag, signal = AbortSignal.timeout(5_000)) => {
          const response = await post(server, snapshot, tag.headers, signal);
          return { http: response.status, body: await response.json() };
        };
        const withHeldSnapshot = async (role, revision, observe) => {
          const tag = server.tagSnapshot();
          const abort = new AbortController();
          const response = readSnapshot(tag, AbortSignal.any([abort.signal, AbortSignal.timeout(5_000)]))
            .catch(error => ({ error: error.name }));
          try {
            const written = await Promise.race([tag.wait('written'), response.then(result => {
              throw new Error(`Snapshot ${role} settled before reaching the writer (${result.http ?? result.error}).`);
            })]);
            record('snapshot-written', { role, ...written });
            await observe({ tag, abort, response });
            tag.release();
            const drained = await tag.wait('drained');
            record('snapshot-drained', { role, ...drained });
            expect(drained).toMatchObject({ id: tag.id, request_id: written.request_id, ok: true, revision });
            return { id: tag.id, ...await response };
          } finally {
            abort.abort();
            tag.release();
            await response;
          }
        };
        const removeWithContendedSnapshot = async () => {
          const routePattern = `**${endpoint}`;
          const firstPermission = Promise.withResolvers();
          const recoveryPermission = Promise.withResolvers();
          const otherReadsPermission = Promise.withResolvers();
          let firstSnapshot;
          const gateFirstSnapshot = async route => {
            const request = route.request();
            const body = request.postDataJSON();
            if (isLayerMutation(body, 'indicator.remove')) return route.fallback();
            if (!firstSnapshot && body?.type === 'snapshot' && explicitMutations.length > 0) {
              firstSnapshot = request;
              if (await firstPermission.promise) return route.continue();
            } else if (await (body?.type === 'snapshot' ? recoveryPermission : otherReadsPermission).promise) {
              return route.fallback();
            }
            return route.abort();
          };
          await page.route(routePattern, gateFirstSnapshot);
          try {
            let removed;
            const browserSnapshot = await duringPageEvent(page, 'request', request => snapshotAttempts.get(request) === 1,
              async () => { removed = await layerMutation('indicator.remove', () => remove.click()); }, 3_000);
            undoStages.removal = removed;
            record('removal-receipt', removed);
            expect(removed).toMatchObject({ http: 200, body: { ok: true, result: {
              request_id: removed.request.request_id,
              outcome: { state: 'committed', receipt: { committed_workspace_revision: 2, undoable: true } },
            } } });
            // Force a different authorized request to hold admission. The old
            // probe says BUSY even though the intended holder is rejected.
            const cancelled = await withHeldSnapshot('legacy-competitor', 2, async ({ tag, abort, response }) => {
              const intended = server.tagSnapshot();
              const [probe, rejected] = await Promise.all([postStatus(server, admissionProbe), readSnapshot(intended)]);
              const closed = await intended.wait('closed');
              record('legacy-probe-false-positive', { probe, actual_holder: tag.id, intended: closed, rejected });
              expect(probe).toBe(429);
              expect(rejected).toMatchObject({ http: 429, body: busy });
              expect(closed).toMatchObject({ http: 429, cancelled: false });

              abort.abort();
              const disconnect = await tag.wait('closed');
              record('snapshot-holder-cancelled', { ...disconnect, client: await response });
              expect(disconnect).toMatchObject({ id: tag.id, cancelled: true });
              const beforeDrain = await readOutput();
              record('competing-output-before-drain', beforeDrain);
              expect(beforeDrain).toMatchObject({ http: 429, body: busy });
            });
            expect(cancelled).toMatchObject({ error: 'AbortError' });

            const held = await withHeldSnapshot('intended-holder', 2, async ({ tag }) => {
              const refused = await duringPageEvent(page, 'response', reply => reply.request() === browserSnapshot,
                () => firstPermission.resolve(true), 3_000, request => request === browserSnapshot);
              const first = {
                attempt: snapshotAttempts.get(browserSnapshot), request: browserSnapshot.postDataJSON(),
                http: refused.status(), body: await refused.json(),
              };
              record('first-browser-snapshot', { holder: tag.id, ...first });
              expect(first).toMatchObject({ attempt: 1, http: 429, body: busy });
            });
            record('snapshot-holder-completed', held);
            expect(held).toMatchObject({ http: 200, body: { ok: true, result: { workspace: { revision: 2 } } } });
            recoveryPermission.resolve(true);
            await browserRecovery;
            otherReadsPermission.resolve(true);
            return removed;
          } finally {
            firstPermission.resolve(false);
            recoveryPermission.resolve(false);
            otherReadsPermission.resolve(false);
            await page.unroute(routePattern, gateFirstSnapshot);
          }
        };
        const durableLayerMutations = () => {
          const database = new DatabaseSync(join(profile.root, 'workspace.sqlite3'), { readOnly: true });
          try {
            return database.prepare(`
              SELECT log.operation, log.id, log.request_id, log.revision, log.undoes, layer.before_active, layer.after_active
              FROM operation_log AS log JOIN indicator_layer_operations AS layer ON layer.operation_id = log.id
              WHERE log.workspace_id = ? AND layer.layer_id = ? AND log.operation IN ('indicator.remove', 'workspace.undo')
              ORDER BY log.revision
            `).all(workspaceId, savedLayer.layer_id);
          } finally {
            database.close();
          }
        };
        page.on('request', observeRemovalRequests);
        const browserRecovery = removalRead('snapshot', (result, request) => {
          const attempt = snapshotAttempts.get(request);
          if (!attempt || result.workspace?.revision !== 2) return false;
          record('browser-authoritative-snapshot', { attempt, revision: result.workspace.revision });
          return true;
        });
        try {
          const [snapshotResult, layersResult, removalResult] = await Promise.allSettled([
            browserRecovery,
            removalRead('indicator_accepted_layers', (result, request) => {
              if (result.dataset_id !== id || result.layers?.length !== 0) return false;
              record('browser-accepted-layers', { request_id: request.postDataJSON().request_id, layers: result.layers, undo: result.undo });
              return true;
            }),
            removeWithContendedSnapshot(),
          ]);
          observingRemovalReads = false;
          if (removalResult.status === 'rejected') throw removalResult.reason;
          if (snapshotResult.status === 'rejected') throw snapshotResult.reason;
          if (layersResult.status === 'rejected') throw layersResult.reason;
          const { value: removalSnapshot } = snapshotResult;
          const { value: removalLayers } = layersResult;
          const { value: removed } = removalResult;
          undoStages.removal_reads = {
            attempt: snapshotAttempts.get(removalSnapshot.request()),
            snapshot: (await removalSnapshot.json()).result.workspace.revision,
            layers: (await removalLayers.json()).result.layers,
          };
          await expect(accepted).toHaveCount(0);
          await expect(undo).toBeEnabled();
          const removalId = removed.body.result.outcome.receipt.operation_id;
          expect((await removalLayers.json()).result.undo).toEqual({
            operation_id: removalId, layer_id: savedLayer.layer_id, committed_workspace_revision: 2,
          });
          const durableRemoval = {
            operation: 'indicator.remove', id: removalId, request_id: removed.request.request_id,
            revision: 2, undoes: null, before_active: 1, after_active: 0,
          };
          const afterRemoval = durableLayerMutations();
          record('durable-removal-before-undo', { operations: afterRemoval });
          expect(afterRemoval).toEqual([durableRemoval]);
          await dialog.getByRole('button', { name: 'Close', exact: true }).click();
          await control(page, 'Open published CSV').click();
          await dialog.getByLabel('Revision ID').fill(id);
          const reopened = removalRead('indicator_accepted_layers', result => result.dataset_id === id);
          await dialog.getByRole('button', { name: 'Open frozen chart' }).click();
          expect((await (await reopened).json()).result.undo?.operation_id).toBe(removalId);
          await expect(undo).toBeEnabled();
          await expect(undo).toHaveCount(1);
          const releaseUndo = Promise.withResolvers();
          const releaseUndoReads = Promise.withResolvers();
          const undoRoute = `**${endpoint}`;
          const forwardedReads = new Set();
          let pauseUndoReads = false;
          let heldUndo;
          const holdUndo = async route => {
            const request = route.request();
            const payload = request.postDataJSON();
            if (!isLayerMutation(payload, 'workspace.undo') || payload.intent.arguments?.operation_id !== removalId) {
              if (pauseUndoReads) {
                if (await releaseUndoReads.promise) return route.fallback();
                return route.abort();
              }
              const completed = request.response().then(response => response?.finished()).catch(() => {});
              forwardedReads.add(completed);
              void completed.then(() => forwardedReads.delete(completed));
              return route.fallback();
            }
            heldUndo = request;
            await releaseUndo.promise;
            await route.continue();
          };
          await page.route(undoRoute, holdUndo);
          try {
            const undone = await layerMutation('workspace.undo', async () => {
              await undo.click();
              await expect.poll(() => Boolean(heldUndo), { timeout: 3_000 }).toBe(true);
              undoStages.held_undo = {
                request_id: heldUndo.postDataJSON().request_id, accepted_visible: await accepted.isVisible(),
              };
              expect(undoStages.held_undo.accepted_visible).toBe(false);
              expect(heldUndo.existingResponse()).toBeNull();
              releaseUndo.resolve();
            });
            undoStages.undo = undone;
            record('undo-receipt', undone);
            expect(undone.request.request_id).toBe(undoStages.held_undo.request_id);
            expect(undone.request.intent.arguments).toEqual({ operation_id: removalId });
            expect(undone).toMatchObject({ http: 200, body: { ok: true, result: {
              request_id: undone.request.request_id,
              outcome: { state: 'committed', receipt: { committed_workspace_revision: 3, undoable: false } },
            } } });
            await expect(preview.getByText('Accepted layer restored.', { exact: true })).toBeVisible();
            await expect(accepted.getByText(savedLayer.version_id, { exact: true })).toBeVisible();

            // Keep new browser reads behind the gate. A cancelled browser read
            // can leave the writer occupied after its client has settled.
            pauseUndoReads = true;
            const forwarded = forwardedReads.size;
            await Promise.all(forwardedReads);
            await expect.poll(async () => {
              const response = await post(server, admissionProbe, {}, AbortSignal.timeout(3_000));
              const http = await responseStatus(response);
              record('post-undo-admission-probe', { forwarded_reads: forwarded, http });
              return http;
            }, { timeout: 3_000, message: 'The workspace rejected the malformed probe after admission became idle' }).toBe(400);
            const restoredSnapshot = await withHeldSnapshot('undo-holder', 3, async () => {
              undoStages.busy_output = await readOutput();
              expect(undoStages.busy_output).toMatchObject({ http: 429, body: busy });
            });
            expect(restoredSnapshot).toMatchObject({ http: 200, body: { ok: true, result: { workspace: { revision: 3 } } } });
            const output = await readOutput();
            undoStages.recovered_output = output;
            expect(output).toMatchObject({ http: 200, body: { ok: true } });
            expect(output.body.result).toEqual(savedOutput);
            releaseUndoReads.resolve(true);
            expect(explicitMutations.map(request => request.intent.operation)).toEqual(['indicator.remove', 'workspace.undo']);
            const durable = durableLayerMutations();
            record('durable-layer-mutations', { operations: durable });
            expect(durable).toEqual([
              durableRemoval,
              { operation: 'workspace.undo', id: undone.body.result.outcome.receipt.operation_id, request_id: undone.request.request_id,
                revision: 3, undoes: removalId, before_active: 0, after_active: 1 },
            ]);
            record('mutation-counts', { remove: 1, undo: 1 });
          } finally {
            releaseUndo.resolve();
            releaseUndoReads.resolve(false);
            await page.unroute(undoRoute, holdUndo);
          }
        } catch (error) {
          record('failure', { message: error.message });
          if (undoStages.removal?.body?.result?.outcome?.state === 'committed') {
            try {
              record('durable-layer-mutations', { operations: durableLayerMutations() });
              const request = { type: 'indicator_accepted_layers', request_id: randomUUID(), revision_id: id };
              const response = await post(server, request, {}, AbortSignal.timeout(3_000));
              record('independent-accepted-layers', { request, http: response.status, body: await response.json() });
            } catch (diagnosticError) {
              record('persistence-diagnostic-error', { message: diagnosticError.message });
            }
            try {
              record('browser-view-at-failure', {
                accepted_visible: await accepted.isVisible(), remove_disabled: await remove.isDisabled({ timeout: 1_000 }).catch(() => null),
                removal_message: await preview.getByText('Accepted layer removed from the chart.', { exact: true }).isVisible(),
                workspace_busy: await page.getByText('The local workspace is busy.', { exact: true }).first().isVisible(),
              });
              await testInfo.attach('indicator-removal-failure', {
                body: await page.screenshot({ timeout: 2_000 }), contentType: 'image/png',
              });
            } catch (diagnosticError) {
              record('browser-diagnostic-error', { message: diagnosticError.message });
            }
          }
          throw error;
        } finally {
          page.off('request', observeRemovalRequests);
          const failed = removalEvents.some(event => event.stage === 'failure');
          try {
            await testInfo.attach('indicator-removal-events', { body: JSON.stringify(removalEvents), contentType: 'application/json' });
            await testInfo.attach('indicator-undo-stages', { body: JSON.stringify(undoStages), contentType: 'application/json' });
          } catch (attachmentError) {
            if (!failed) throw attachmentError;
          }
        }
        observation.preview = { initial: first.event.snapshot, warmed: warmed.event.snapshot, cancelled, withheld, committed };

      });
    }, { snapshotGate: true });
    await inService(port, profile, 'ubuntu-workspace', async server => {
      await inFreshBrowser(browser, server, async page => {
        let reruns = 0;
        page.on('request', request => { if (request.postDataJSON()?.intent?.operation === 'indicator.runPreview') reruns += 1; });
        await control(page, 'Open published CSV').click();
        const dialog = page.getByRole('dialog', { name: 'Open published CSV' });
        await dialog.getByLabel('Revision ID').fill(id);
        await dialog.getByRole('button', { name: 'Open frozen chart' }).click();
        const accepted = dialog.getByRole('region', { name: 'Accepted indicators' });
        await expect(accepted.getByText(savedLayer.version_id)).toBeVisible();
        await expect(dialog.getByRole('button', { name: 'Undo indicator removal' })).toHaveCount(0);
        await accepted.getByText('Accepted provenance and values').click();
        await expect(accepted.getByText('mean · 3')).toBeVisible();
        expect(reruns).toBe(0);
        const restore = { type: 'indicator_accepted_output', request_id: randomUUID(), layer_id: savedLayer.layer_id };
        expect(await reply(server, restore)).toEqual(savedOutput);
        await dialog.getByRole('button', { name: 'Use as workspace source' }).click();
        const mainChart = page.getByRole('region', { name: 'Price chart' });
        await mainChart.getByText('Indicators', { exact: true }).click();
        await expect(mainChart.getByRole('region', { name: 'Accepted indicators' }).getByText(savedLayer.version_id)).toBeVisible();
        expect(reruns).toBe(0);

        const database = new DatabaseSync(join(profile.root, 'workspace.sqlite3'));
        try {
          database.prepare('INSERT INTO local_dataset_display_revocations (revision_id) VALUES (?)').run(id);
          const counts = database.prepare('SELECT count(*) AS count FROM indicator_versions').get();
          expect(counts.count).toBe(1);
        } finally {
          database.close();
        }
        const withheld = await (await post(server, restore)).json();
        expect(withheld).toMatchObject({ ok: false, error: { code: 'PERMISSION_DENIED' } });
        expect(withheld.result).toBeUndefined();
        expect(await reply(server, { type: 'indicator_accepted_layers', request_id: randomUUID(), revision_id: id }))
          .toMatchObject({ layers: [{ ...savedLayer, available: false }] });
        observation.restored = { reruns, withheld, version: savedLayer.version_id };
      });
    });
    await testInfo.attach('indicator-preview', { body: JSON.stringify(observation), contentType: 'application/json' });

  } finally {
    await profile.close();
  }
});

test('indicator-preview rejects obsolete inputs and open confirmations without replacing accepted output', async ({ browser }, testInfo) => {
  const profile = await makeProfile();
  const port = await freePort();
  const id = randomUUID();
  const other = randomUUID();
  const a = Date.parse('2026-02-04T00:00:00Z');
  const day = 86_400_000;
  const bars = [1, 3, 5, 7, 9, 11].map((close, index) => {
    const time_ms = a + day * index;
    return {
      time_ms, trading_day: new Date(time_ms).toISOString().slice(0, 10),
      open: close, high: close, low: close, close, volume: close * 10, is_closed: true,
    };
  });
  const command = () => ({
    protocol_version: '1.1', type: 'request', request_id: randomUUID(),
    message_id: randomUUID(), timestamp_ms: Date.now(),
  });
  const respond = challenge_id => ({ ...command(), operation: 'confirmation.respond', decision: 'approve', challenge_id });
  const status = task_id => ({ type: 'indicator_preview_status', request_id: randomUUID(), task_id });
  const observation = {};
  let saved;
  let readSaved;
  try {
    await seedFrozenEvidence(profile, id, a, a + day, bars);
    const alternative = bars.map(bar => ({ ...bar, close: bar.close + 20, high: bar.high + 20 }));
    await seedFrozenEvidence(profile, other, a, a + day, alternative);
    await inService(port, profile, 'ubuntu-workspace', async server => {
      const send = async request => (await post(server, request)).json();
      await inFreshBrowser(browser, server, async page => {
        const dialog = page.getByRole('dialog', { name: 'Open published CSV' });
        const editor = dialog.getByRole('region', { name: 'Indicator preview' });
        const open = async revisionId => {
          await control(page, 'Open published CSV').click();
          await dialog.getByLabel('Revision ID').fill(revisionId);
          await dialog.getByRole('button', { name: 'Open frozen chart' }).click();
          await expect(editor.getByRole('button', { name: 'Run preview' })).toBeEnabled();
        };
        const run = async () => {
          await editor.getByRole('button', { name: 'Run preview' }).click();
          await expect(editor.getByRole('button', { name: 'Accept preview' })).toBeEnabled();
        };
        const stage = async () => {
          const reply = await duringPageEvent(page, 'response', response =>
            response.request().postDataJSON()?.intent?.operation === 'indicator.acceptVersion',
          () => editor.getByRole('button', { name: 'Accept preview' }).click(), 8_000);
          await expect(editor.getByRole('button', { name: 'Confirm indicator acceptance' })).toBeEnabled();
          return (await reply.json()).result.outcome.challenge;
        };
        const waitInvalidation = async (task, action) => {
          const response = await duringPageEvent(page, 'response', response => {
            const body = response.request().postDataJSON();
            return body?.operation === 'task.cancel' && body.task_id === task && response.ok();
          }, action, 8_000);
          expect((await response.json()).result.task_id).toBe(task);
        };

        await open(id);
        await run();
        await stage();
        const confirmed = await duringPageEvent(page, 'response', response =>
          response.request().postDataJSON()?.operation === 'confirmation.respond',
        () => editor.getByRole('button', { name: 'Confirm indicator acceptance' }).click(), 8_000);
        saved = (await confirmed.json()).result.outcome.result;
        await expect(editor.getByText(saved.version_id)).toBeVisible();
        const request = { type: 'indicator_accepted_output', request_id: randomUUID(), layer_id: saved.layer_id };
        readSaved = (await send(request)).result;

        const held = Promise.withResolvers();
        const release = Promise.withResolvers();
        const routeStatus = async route => {
          if (route.request().postDataJSON()?.type !== 'indicator_preview_status') return route.continue();
          const response = await route.fetch();
          const body = await response.json();
          if (body.result?.status === 'ready') {
            held.resolve(body.result);
            await release.promise;
          }
          await route.fulfill({ response }).catch(() => {});
        };
        await page.route('**' + endpoint, routeStatus);
        try {
          await editor.getByRole('button', { name: 'Run preview' }).click();
          const waiting = await Promise.race([held.promise, delay(8_000).then(() => { throw new Error('Ready reply was not intercepted'); })]);
          expect(waiting.resolved_parameters).toEqual({ period: 2 });
          expect(waiting.event.snapshot.from_ms).toBe(a);
          await waitInvalidation(waiting.task_id, async () => {
            await editor.getByLabel('Rolling period').fill('3');
            await editor.getByLabel('First visible bar').selectOption(String(a + 5 * day));
          });
          release.resolve();
          await expect(editor.getByRole('button', { name: 'Accept preview' })).toHaveCount(0);
          const withheld = await send(status(waiting.task_id));
          expect(withheld).toMatchObject({ ok: false, error: { code: 'STALE_CONTEXT' } });
          const rejected = await send({
            ...command(), task_id: null, scope: {
              workspace_id: workspaceId, expected_workspace_revision: 1, expected_selection_revision: 0,
              idempotency_key: randomUUID(), target: {
                kind: 'chart_interval', snapshot: waiting.source_snapshot,
                range: { from_ms: waiting.event.snapshot.from_ms, to_ms: waiting.event.snapshot.to_ms },
              },
            }, intent: { operation: 'indicator.acceptVersion', arguments: {
              preview_id: waiting.event.preview_id, result_hash: waiting.event.artifact.result_hash,
            } },
          });
          expect(rejected.result.outcome).toMatchObject({ state: 'failed', error: { code: 'STALE_CONTEXT' } });
          observation.lateReady = { task: waiting.task_id, withheld, rejected: rejected.result.outcome };
        } finally {
          release.resolve();
          await page.unroute('**' + endpoint, routeStatus);
        }

        await run();
        await expect(editor.getByText('Period 3 · 2 warm-up bars')).toBeVisible();
        const staged = await stage();
        await waitInvalidation(staged.task_id, async () => {
          await editor.getByLabel('Indicator input').selectOption('volume');
          await editor.getByLabel('Rolling period').fill('2');
          await editor.getByLabel('First visible bar').selectOption(String(a + 4 * day));
        });
        await expect(editor.getByRole('button', { name: 'Confirm indicator acceptance' })).toHaveCount(0);
        const rejectedDecision = (await send(respond(staged.challenge_id))).result.outcome;
        expect(rejectedDecision).toMatchObject({ state: 'failed', error: { code: 'STALE_CONTEXT' } });
        observation.openApproval = rejectedDecision;

        await run();
        const oldSource = await stage();
        await waitInvalidation(oldSource.task_id, () => dialog.getByRole('button', { name: 'Close', exact: true }).click());
        await open(other);
        const rejectedSource = (await send(respond(oldSource.challenge_id))).result.outcome;
        expect(rejectedSource).toMatchObject({ state: 'failed', error: { code: 'STALE_CONTEXT' } });
        observation.sourceChange = rejectedSource;
        expect((await send(request)).result).toEqual(readSaved);
      });
    });
    await inService(port, profile, 'ubuntu-workspace', async server => {
      const list = await (await post(server, {
        type: 'indicator_accepted_layers', request_id: randomUUID(), revision_id: id,
      })).json();
      expect(list.result.layers).toEqual([{ ...saved, available: true }]);
      const restored = await (await post(server, {
        type: 'indicator_accepted_output', request_id: randomUUID(), layer_id: saved.layer_id,
      })).json();
      expect(restored.result).toEqual(readSaved);
      observation.restarted = { version: saved.version_id, input: restored.result.document.snapshot };
    });
    await testInfo.attach('obsolete-indicator-inputs', { body: JSON.stringify(observation), contentType: 'application/json' });
  } finally {
    await profile.close();
  }
});

test('indicator-preview runs trusted Python once after exact local approval and restores the accepted output', async ({ browser }, testInfo) => {
  const profile = await makeProfile();
  const port = await freePort();
  const id = randomUUID();
  const before = 1704200940000;
  const after = 1704243600000;
  const mean = await readFile(join(root, 'tests/fixtures/indicators/two-bar-mean.py'), 'utf8');
  const invalid = mean.replace("bar['time_ms'], 'value'", "bar['time_ms'] + 1, 'value'");
  const slow = "def compute(bars, params):\n    while True:\n        1 + 1\n";
  const frame = () => ({ protocol_version: '1.1', type: 'request', request_id: randomUUID(),
    message_id: randomUUID(), timestamp_ms: Date.now() });
  const call = async (server, body) => {
    let frame;
    await expect.poll(async () => {
      frame = await (await post(server, body)).json();
      return frame.error?.code;
    }).not.toBe('BUSY');
    return frame;
  };
  const status = task_id => ({ type: 'indicator_preview_status', request_id: randomUUID(), task_id });
  const respond = (challenge_id, decision = 'approve') =>
    ({ ...frame(), operation: 'confirmation.respond', challenge_id, decision });
  const sampleEditor = async page => {
    const chart = page.getByRole('region', { name: 'Price chart' });
    const plot = chart.getByRole('img', { name: /Candlestick and volume chart/ });
    await expect(plot).toBeVisible();
    await chart.getByText('Indicators', { exact: true }).click();
    return { editor: chart.getByRole('region', { name: 'Indicator preview' }), plot };
  };
  const rows = [
    { time_ms: before, trading_day: '2024-01-02', open: 10, high: 10, low: 10, close: 10, volume: null, is_closed: true },
    { time_ms: after, trading_day: '2024-01-03', open: 14, high: 14, low: 14, close: 14, volume: null, is_closed: true },
  ];
  const observations = {};
  let layer;
  let saved;
  const output = () => ({ type: 'indicator_accepted_output', request_id: randomUUID(), layer_id: layer.layer_id });
  try {
    await seedFrozenEvidence(profile, id, before, after, rows);
    await inService(port, profile, 'ubuntu-workspace', async server => {
      await inFreshBrowser(browser, server, async page => {
        await sampleStep(page, server, testInfo, 'open sample for trusted Python preview', () => sampleButton(page).click());
        const { editor, plot } = await sampleEditor(page);
        const canvases = await plot.locator('canvas').count();
        await editor.getByLabel('Indicator type').selectOption('python');
        await editor.getByLabel('Python parameters').fill('{"divisor":2}');
        await editor.getByLabel('First visible bar').selectOption(String(before));
        await editor.getByLabel('Last visible bar').selectOption(String(after));

        const review = async source => {
          await editor.getByLabel('Select Python file').setInputFiles({
            name: 'fixture.py', mimeType: 'text/x-python', buffer: Buffer.from(source),
          });
          const result = await duringResponse(page, 'indicator.runPreview', () =>
            editor.getByRole('button', { name: 'Review Python run' }).click());
          const outcome = (await result.json()).result.outcome;
          expect(outcome.state).toBe('awaiting_confirmation');
          const prompt = editor.getByRole('group', { name: 'Confirm trusted Python run' });
          await expect(prompt).toBeVisible();
          expect(await prompt.getByLabel('Exact Python code').textContent()).toBe(source);
          expect(outcome.challenge.python.code_hash).toBe(createHash('sha256').update(source).digest('hex'));
          const beforeApproval = await call(server, status(outcome.challenge.task_id));
          expect(beforeApproval).toMatchObject({ ok: false, error: { code: 'STALE_CONTEXT' } });
          return { prompt, challenge: outcome.challenge };
        };
        const clickApproval = async (prompt, approve) => {
          const response = page.waitForResponse(response => response.request().postDataJSON()?.operation === 'confirmation.respond');
          await prompt.getByRole('button', { name: approve ? 'Run selected Python' : 'Decline Python run' }).click();
          const wire = await (await response).json();
          expect(wire.indicator_compute).toBeUndefined();
          return wire.result.outcome;
        };
        const terminal = async task_id => {
          let outcome;
          await expect.poll(async () => {
            outcome = (await call(server, status(task_id))).result;
            return outcome?.status;
          }).toMatch(/^(ready|terminal)$/);
          return outcome;
        };

        const declined = await review(mean);
        expect(await clickApproval(declined.prompt, false)).toMatchObject({ state: 'failed', error: { code: 'CANCELLED' } });
        expect((await call(server, respond(declined.challenge.challenge_id))).result.outcome)
          .toMatchObject({ state: 'failed', error: { code: 'STALE_CONTEXT' } });
        await expect(editor.getByRole('button', { name: 'Review Python run' })).toBeDisabled();

        const obsolete = await review(mean);
        const cancelledReview = page.waitForResponse(response => {
          const body = response.request().postDataJSON();
          return body?.operation === 'task.cancel' && body.task_id === obsolete.challenge.task_id && response.ok();
        });
        await editor.getByLabel('Python parameters').fill('{"divisor":3}');
        await expect(obsolete.prompt).toHaveCount(0);
        await cancelledReview;
        expect((await call(server, respond(obsolete.challenge.challenge_id))).result?.outcome)
          .toMatchObject({ state: 'failed', error: { code: 'STALE_CONTEXT' } });
        await editor.getByLabel('Python parameters').fill('{"divisor":2}');

        const approved = await review(mean);
        expect(approved.challenge).toMatchObject({
          target: { snapshot: { from_ms: before, to_ms: after + 1, warmup_bars: 0 } },
          python: { input_use: 'trusted_code_permitted',
            parameters_hash: createHash('sha256').update('{"divisor":2}').digest('hex') },
        });
        await expect(approved.prompt).toContainText('Resolved parameters {"divisor":2}');
        await expect(approved.prompt).toContainText('read or change files and access the network with your user permissions');
        await expect(approved.prompt).toContainText('2 bars · 0 warm-up bars');
        await approved.prompt.getByText('Exact frozen input bars').click();
        const shownBars = JSON.parse(await approved.prompt.locator('details pre').textContent());
        expect(shownBars.map(bar => [bar.time_ms, bar.close])).toEqual([[before, 3500.2], [after, 3500.8]]);
        expect(await clickApproval(approved.prompt, true)).toMatchObject({
          state: 'committed', result: { task_id: approved.challenge.task_id },
        });
        const ready = await terminal(approved.challenge.task_id);
        expect(ready.event.snapshot.content_hash).toBe(approved.challenge.python.snapshot_hash);
        expect(ready.event.artifact.result.series[0].points).toEqual([
          { time_ms: before, value: null }, { time_ms: after, value: 3500.5 },
        ]);
        await expect(editor.getByText('Preview layers · trusted Python')).toBeVisible();
        await expect.poll(() => plot.locator('canvas').count()).toBeGreaterThan(canvases);

        await editor.getByRole('button', { name: 'Accept preview' }).click();
        const accepted = page.waitForResponse(response => response.request().postDataJSON()?.operation === 'confirmation.respond');
        await editor.getByRole('button', { name: 'Confirm indicator acceptance' }).click();
        const acceptedOutcome = (await (await accepted).json()).result.outcome;
        expect(acceptedOutcome.state).toBe('committed');
        layer = acceptedOutcome.result;
        saved = (await call(server, output())).result;
        expect(saved.document.artifact).toEqual(ready.event.artifact);
        const layers = editor.getByRole('region', { name: 'Accepted indicators' });
        await expect(layers.getByText(layer.version_id)).toBeVisible();

        const malformed = await review(invalid);
        expect(await clickApproval(malformed.prompt, true)).toMatchObject({ state: 'committed' });
        expect(await terminal(malformed.challenge.task_id)).toMatchObject({
          event: { outcome: { status: 'failed', error: { code: 'OUTPUT_INVALID' } } },
        });
        await expect(layers.getByText(layer.version_id)).toBeVisible();

        const cancellable = await review(slow);
        expect(await clickApproval(cancellable.prompt, true)).toMatchObject({ state: 'committed' });
        expect((await call(server, status(cancellable.challenge.task_id))).result).toMatchObject({ status: 'running' });
        await editor.getByRole('button', { name: 'Cancel preview' }).click();
        expect(await terminal(cancellable.challenge.task_id)).toMatchObject({ event: { outcome: { status: 'cancelled' } } });
        await expect(layers.getByText(layer.version_id)).toBeVisible();
        expect((await call(server, output())).result).toEqual(saved);
        const database = new DatabaseSync(join(profile.root, 'workspace.sqlite3'), { readOnly: true });
        try {
          expect(database.prepare('SELECT count(*) AS count FROM indicator_versions').get().count).toBe(1);
        } finally { database.close(); }

        await control(page, 'Data sources').click();
        await page.getByRole('complementary', { name: 'Data sources' })
          .getByRole('button', { name: 'Open published CSV' }).click();
        const dialog = page.getByRole('dialog', { name: 'Open published CSV' });
        await dialog.getByLabel('Revision ID').fill(id);
        const opened = page.waitForResponse(response => response.request().postDataJSON()?.type === 'frozen_chart');
        await dialog.getByRole('button', { name: 'Open frozen chart' }).click();
        const csv = (await (await opened).json()).result.snapshot;
        const current = (await call(server, snapshot)).result;
        const scope = { workspace_id: workspaceId, target: { kind: 'chart', object_id: 'main-chart' },
          expected_workspace_revision: current.workspace.revision, expected_selection_revision: current.selection_revision,
          idempotency_key: randomUUID() };
        const draft = (await call(server, { ...frame(), scope, task_id: null, intent: {
          operation: 'indicator.createDraft', arguments: { language: 'python', source: mean, params: {} },
        } })).result.outcome.result;
        expect((await call(server, { ...frame(), task_id: null, scope: {
          ...scope, target: { kind: 'chart_interval', snapshot: csv, range: { from_ms: before, to_ms: after + 1 } },
          idempotency_key: randomUUID(),
        }, intent: { operation: 'indicator.runPreview',
          arguments: { draft_id: draft.draft_id, snapshot: csv, python_params: { divisor: 2 } },
        } })).result.outcome).toMatchObject({ state: 'failed', error: { code: 'PERMISSION_DENIED' } });
        observations.first = { accepted: layer, output: ready.event.artifact, declined: true, obsolete: true,
          rejectedOutput: true, cancelled: true, csvPurposeDenied: true };
      }, [endpoint, '/__scopehub/operation'], { traceSample: true });
    }, { historyTrace: true });

    await inService(port, profile, 'ubuntu-workspace', async server => {
      await inFreshBrowser(browser, server, async page => {
        let runs = 0;
        page.on('request', request => { if (request.postDataJSON()?.intent?.operation === 'indicator.runPreview') runs += 1; });
        const { editor } = await sampleEditor(page);
        const accepted = editor.getByRole('region', { name: 'Accepted indicators' });
        await expect(accepted.getByText(layer.version_id)).toBeVisible();
        await accepted.getByText('Accepted provenance and values').click();
        await expect(accepted.getByText('python-mean · Unknown, 3500.5')).toBeVisible();
        expect((await call(server, output())).result).toEqual(saved);
        expect(runs).toBe(0);
        await leaveSample(page);
        const withheld = await call(server, output());
        expect(withheld).toMatchObject({ ok: false, error: { code: 'PERMISSION_DENIED' } });
        expect(withheld.result).toBeUndefined();
        const references = await call(server, { type: 'indicator_accepted_layers', request_id: randomUUID(),
          revision_id: saved.document.snapshot.dataset_id });
        expect(references.result.layers).toEqual([{
          layer_id: layer.layer_id, version_id: layer.version_id, available: false,
        }]);
        observations.reopened = { runs, references: references.result.layers, error: withheld.error.code };
      }, [endpoint, '/__scopehub/operation'], { traceSample: true, restoredSample: { testInfo, label: 'restore trusted Python sample' } });
    }, { historyTrace: true });
    await testInfo.attach('trusted-indicator', { body: JSON.stringify(observations), contentType: 'application/json' });
  } finally { await profile.close(); }
});

test('indicator-preview runs trusted Python once for a region-only result and restores it', async ({ browser }, testInfo) => {
  const profile = await makeProfile();
  const port = await freePort();
  const previous = 1704200940000;
  const next = 1704243600000;
  const last = next + 240_000;
  const label = '<img src=x onerror=alert(1)>';
  const regions = [
    { from_ms: previous, to_ms: next + 120_000, label, color: '#ff00ff' },
    { from_ms: next + 190_000, to_ms: next + 200_000, label: 'Inside the fourth minute', color: '#ff8000' },
    { from_ms: previous + 70_000, to_ms: next - 10_000, label: 'Between sessions', color: '#ffff00' },
  ];
  const program = `def compute(bars, params):
    return {"series": [], "markers": [], "regions": ${JSON.stringify(regions)}, "diagnostics": []}
`;
  try {
    await inService(port, profile, 'ubuntu-workspace', async server => {
      await inFreshBrowser(browser, server, async page => {
        const history = await sampleStep(page, server, testInfo, 'open sample for a region preview', () => sampleButton(page).click());
        const original = (await history.existingResponse().json()).result.snapshot;
        const chart = page.getByRole('region', { name: 'Price chart' });
        const plot = chart.getByRole('img', { name: /and volume chart/ });
        const fit = control(page, 'Fit chart');
        const openIndicators = async () => {
          const disclosure = chart.locator('details.chart-indicators');
          if (await disclosure.getAttribute('open', { timeout: 5000 }) === null) {
            await disclosure.locator(':scope > summary').click({ timeout: 5000 });
          }
        };
        await expect(plot).toBeVisible();
        await openIndicators();
        const editor = chart.getByRole('region', { name: 'Indicator preview' });
        await editor.getByLabel('Indicator type').selectOption('python');
        await editor.getByLabel('First visible bar').selectOption(String(previous));
        await editor.getByLabel('Last visible bar').selectOption(String(last));
        await editor.getByLabel('Select Python file').setInputFiles({
          name: 'region.py', mimeType: 'text/x-python', buffer: Buffer.from(program),
        });
        await editor.getByRole('button', { name: 'Review Python run' }).click();
        const ready = page.waitForResponse(async response => response.request().postDataJSON()?.type === 'indicator_preview_status'
          && (await response.json()).result?.status === 'ready');
        await editor.getByRole('button', { name: 'Run selected Python' }).click();
        const preview = (await (await ready).json()).result.event.artifact;
        expect(preview.result).toMatchObject({ series: [], markers: [], regions });
        await expect(editor.getByText('Preview layers · trusted Python')).toBeVisible();
        await editor.getByText('Preview provenance and values').click();
        await expect(editor.getByRole('list', { name: 'Indicator regions' })).toContainText(label);
        await expect(editor.locator('img')).toHaveCount(0);
        await fit.click();
        await expect.poll(async () => (await regionBand(plot))?.intensity ?? 0).toBeGreaterThan(25);
        const previewBand = await regionBand(plot);
        await expect.poll(async () => (await regionBand(plot, 'orange'))?.intensity ?? 0).toBeGreaterThan(12);
        const previewMinute = await regionBand(plot, 'orange');
        expect(await regionBand(plot, 'yellow')).toBeNull();

        await editor.getByRole('button', { name: 'Accept preview' }).click();
        const commit = page.waitForResponse(response => response.request().postDataJSON()?.operation === 'confirmation.respond');
        await editor.getByRole('button', { name: 'Confirm indicator acceptance' }).click();
        const outcome = (await (await commit).json()).result.outcome;
        expect(outcome.state).toBe('committed');
        const output = { type: 'indicator_accepted_output', layer_id: outcome.result.layer_id };
        const saved = async () => {
          let body;
          await expect.poll(async () => {
            body = await (await post(server, { ...output, request_id: randomUUID() })).json();
            return body.error?.code;
          }).not.toBe('BUSY');
          return body;
        };
        expect((await saved()).result.document.artifact).toEqual(preview);
        const accepted = editor.getByRole('region', { name: 'Accepted indicators' });
        await expect(accepted.getByText(outcome.result.version_id)).toBeVisible();
        await expect(editor.getByText('Preview layers · trusted Python')).toHaveCount(0);
        await expect.poll(async () => (await regionBand(plot))?.intensity ?? 0).toBeGreaterThan(previewBand.intensity + 15);
        await expect.poll(async () => (await regionBand(plot, 'orange'))?.intensity ?? 0).toBeGreaterThan(previewMinute.intensity + 10);
        expect(await regionBand(plot, 'yellow')).toBeNull();
        let reruns = 0;
        page.on('request', request => {
          if (request.postDataJSON()?.intent?.operation === 'indicator.runPreview') reruns++;
        });

        const sources = page.getByRole('complementary', { name: 'Data sources' });
        const sampleDate = sources.getByLabel('Sample date (UTC)');
        const cropRequest = sampleHistoryCall();
        Object.assign(cropRequest.payload, { from_ms: Date.UTC(2024, 0, 3), to_ms: Date.UTC(2024, 0, 4) });
        await control(page, 'Data sources').click();
        await sampleStep(page, server, testInfo, 'crop across the accepted region', () => sampleDate.fill('2024-01-03'),
          { catalog: false, historyCall: cropRequest, bars: 20 });
        await sources.getByRole('button', { name: 'Close panel' }).click();
        await fit.click();
        // The interval includes the final Jan 2 bar and exactly two Jan 3 bars.
        // In the cropped chart it starts at the left edge and spans 2 of 20 cells.
        await expect.poll(async () => {
          const band = await regionBand(plot);
          return band && { first: Math.round(band.start * 20), span: Math.round((band.end - band.start) * 20) };
        }).toEqual({ first: 0, span: 2 });
        await expect.poll(async () => {
          const band = await regionBand(plot, 'orange');
          return band && { first: Math.round(band.start * 20), span: Math.round((band.end - band.start) * 20) };
        }).toEqual({ first: 3, span: 1 });
        expect(await regionBand(plot, 'yellow')).toBeNull();
        await openIndicators();
        await expect(accepted.getByText(outcome.result.version_id)).toBeVisible();
        await accepted.getByText('Accepted provenance and values').click();
        await expect(accepted.getByRole('list', { name: 'Indicator regions' })).toContainText(label);
        await expect(accepted.locator('img')).toHaveCount(0);
        await control(page, 'Close-price line').click();
        await expect(plot).toHaveAccessibleName(/Close-price line and volume chart/);
        await expect.poll(async () => (await regionBand(plot))?.intensity ?? 0).toBeGreaterThan(previewBand.intensity + 15);

        // Request the first replay observation from the same provider. The
        // dataset identity and the highlighted early bars still match.
        const later = original.as_of_ms + 15_000;
        await page.route(`**${operationPath}`, route => {
          const call = route.request().postDataJSON();
          if (call?.operation !== 'market.readHistoryPage') return route.continue();
          return route.continue({ postData: JSON.stringify({ ...call, payload: { ...call.payload, as_of_ms: later } }) });
        });
        const laterHistory = page.waitForResponse(response => new URL(response.url()).pathname === operationPath
          && response.request().postDataJSON()?.operation === 'market.readHistoryPage' && response.status() !== 429,
        { timeout: 8000 });
        await control(page, 'Data sources').click();
        await sampleDate.fill('');
        const changed = await (await laterHistory).json();
        expect(changed).toMatchObject({ ok: true, result: { snapshot: {
          dataset_id: original.dataset_id, dataset_revision: original.dataset_revision, as_of_ms: later, series: original.series,
        } } });
        await sources.getByRole('button', { name: 'Close panel' }).click();
        await openIndicators();
        await expect(accepted.getByText('Accepted version · different chart observation')).toBeVisible();
        await expect.poll(() => regionBand(plot)).toBeNull();
        expect(await regionBand(plot, 'orange')).toBeNull();
        await accepted.getByText('Accepted provenance and values').click();
        await expect(accepted.getByRole('list', { name: 'Indicator regions' })).toContainText(label);
        expect((await saved()).result.document.artifact).toEqual(preview);

        await page.unroute(`**${operationPath}`);
        await control(page, 'Data sources').click();
        await sampleStep(page, server, testInfo, 'return to the compatible observation', () => sampleDate.fill('2024-01-03'),
          { catalog: false, historyCall: cropRequest, bars: 20 });
        await expect.poll(async () => (await regionBand(plot))?.intensity ?? 0).toBeGreaterThan(previewBand.intensity + 15);
        await sources.getByRole('button', { name: 'Leave sample' }).click();
        await expect(plot).toHaveCount(0);
        expect(await saved()).toMatchObject({ ok: false, error: { code: 'PERMISSION_DENIED' } });
        expect(reruns).toBe(0);
      }, [endpoint, '/__scopehub/operation'], { traceSample: true });
    }, { historyTrace: true });
  } finally {
    await profile.close();
  }
});

async function regionBand(plot, color = 'magenta') {
  return plot.locator('canvas').evaluateAll((canvases, color) => {
    for (const canvas of canvases) {
      if (!canvas.width || !canvas.height) continue;
      const values = canvas.getContext('2d').getImageData(0, Math.floor(canvas.height / 2), canvas.width, 1).data;
      const columns = [];
      for (let x = 0; x < canvas.width; x++) {
        const offset = x * 4;
        const [red, green, blue, alpha] = values.subarray(offset, offset + 4);
        const matches = color === 'orange' ? red > green + 12 && green > blue + 4
          : color === 'yellow' ? red > blue + 25 && green > blue + 25 && Math.abs(red - green) < 10
            : red > green + 25 && blue > green + 25;
        if (alpha > 0 && matches) columns.push(x);
      }
      if (columns.length < 3) continue;
      const middle = 4 * columns[Math.floor(columns.length / 2)];
      return { start: columns[0] / canvas.width, end: (columns.at(-1) + 1) / canvas.width,
        intensity: values[middle] - values[middle + 1] };
    }
    return null;
  }, color);
}

test('indicator-preview clips region overlap to shortened, missing and irregular buckets', async ({ browser }) => {
  const profile = await makeProfile();
  try {
    await inService(await freePort(), profile, 'ubuntu-workspace', async server => {
      await inFreshBrowser(browser, server, async page => {
        const bands = await page.evaluate(async () => {
          const { indicatorBarIntervals, clipIndicatorRegions } = await import('/src/chart-adapter/indicator-regions.ts');
          const start = Date.UTC(2024, 0, 2, 13);
          const reopen = Date.UTC(2024, 0, 3, 1);
          const trading_day = '2024-01-03';
          const minute = 60_000;
          const bar = (time_ms, is_closed = true) => ({
            time_ms, trading_day, open: 10, high: 12, low: 9, close: 11, volume: 3, is_closed,
          });
          const range = { from_ms: start, to_ms: reopen + 2 * minute };
          const sessions = [
            { start_ms: start, end_ms: start + 17 * minute, trading_day },
            { start_ms: reopen, end_ms: reopen + 2 * minute, trading_day },
          ];
          const buckets = indicatorBarIntervals([bar(start), bar(start + 5 * minute), bar(start + 15 * minute),
            bar(reopen, false)], '5m', reopen + 20_000, true, sessions);
          const daily = indicatorBarIntervals([bar(start)], '1d', range.to_ms, true, sessions);
          const irregular = indicatorBarIntervals([bar(start), bar(start + 30_000), bar(reopen)], '1m', undefined, false);
          const atCutoff = indicatorBarIntervals([bar(start), bar(start + minute, false), bar(start + 2 * minute)],
            '1m', start + minute, false);
          const unverified = indicatorBarIntervals([bar(start)], '1m', range.to_ms, true, [sessions[1], sessions[0]]);
          const match = (intervals, from_ms, to_ms, visible = range) => clipIndicatorRegions(intervals,
            [{ from_ms, to_ms, label: 'Fixture interval', color: '#446688' }], visible, false)
            .map(({ first, last }) => [first, last]);
          return {
            missingFiveMinuteBar: match(buckets, start + 10 * minute, start + 11 * minute),
            shortenedFiveMinuteBar: match(buckets, start + 16 * minute, start + 17 * minute),
            justAfterSession: match(buckets, start + 17 * minute, start + 18 * minute),
            openFiveMinuteBar: match(buckets, reopen + 10_000, reopen + 20_000),
            afterObservation: match(buckets, reopen + 20_000, reopen + 30_000),
            outsideFrozenRange: match(buckets, reopen + 10_000, reopen + 20_000,
              { ...range, to_ms: reopen + 10_000 }),
            secondSessionOfDailyBar: match(daily, reopen + 15_000, reopen + 20_000),
            dailyBreak: match(daily, start + 17 * minute, reopen),
            clippedAtNextIrregularStart: match(irregular, start + 30_000, start + 45_000),
            irregularGap: match(irregular, start + 90_000, reopen),
            startsAtObservation: match(atCutoff, start + minute, start + minute + 1),
            startsAfterObservation: match(atCutoff, start + 2 * minute, start + 2 * minute + 1),
            unverifiedBucketInterior: match(unverified, start + 10_000, start + 20_000),
            unverifiedBarStart: match(unverified, start, start + 1),
          };
        });
        expect(bands).toEqual({
          missingFiveMinuteBar: [], shortenedFiveMinuteBar: [[2, 2]], justAfterSession: [],
          openFiveMinuteBar: [[3, 3]], afterObservation: [], outsideFrozenRange: [],
          secondSessionOfDailyBar: [[0, 0]], dailyBreak: [],
          clippedAtNextIrregularStart: [[1, 1]], irregularGap: [],
          startsAtObservation: [[1, 1]], startsAfterObservation: [],
          unverifiedBucketInterior: [], unverifiedBarStart: [[0, 0]],
        });
      });
    });
  } finally {
    await profile.close();
  }
});

test('indicator-preview excludes points and markers outside the displayed chart bars', async ({ browser }) => {
  const profile = await makeProfile();
  try {
    await inService(await freePort(), profile, 'ubuntu-workspace', async server => {
      await inFreshBrowser(browser, server, async page => {
        const painted = await page.evaluate(async () => {
          const { mountHistoryChart } = await import('/src/chart-adapter/history.ts');
          const element = document.body.appendChild(document.createElement('div'));
          Object.assign(element.style, { width: '600px', height: '400px' });
          const point = time_ms => ({
            time_ms, trading_day: '2024-01-02', open: 10, high: 12, low: 9, close: 11, volume: 3, is_closed: true,
          });
          const [before, first, missing, last] = [1704200700000, 1704200760000, 1704200820000, 1704200880000];
          const bars = [point(first), point(last)];
          const labels = [];
          const fillText = CanvasRenderingContext2D.prototype.fillText;
          CanvasRenderingContext2D.prototype.fillText = function (text, ...args) {
            if (element.contains(this.canvas)) labels.push(text);
            return fillText.call(this, text, ...args);
          };
          const chart = mountHistoryChart(element, { bars, dataset: { instrument_id: 'FIX', timeframe: '1m', timezone: 'UTC' } },
            { price_tick: '0.01' }, 'candlestick', () => {});
          try {
            const drawLabels = async () => {
              labels.length = 0;
              chart.fit();
              await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
              return [...labels];
            };
            const result = {
              series: [{ id: 'fixture', pane: 'price', type: 'line', style: { color: '#ffff00', width: 1 },
                points: [{ time_ms: before, value: 900000 }, { time_ms: first, value: 11 }, { time_ms: last, value: 500000 }] }],
              markers: [
                { time_ms: first, position: 'above', label: 'First fixture bar', color: '#ff0000' },
                { time_ms: missing, position: 'above', label: 'Updated fixture bar', color: '#ff0000' },
              ],
              regions: [], diagnostics: [],
            };
            chart.setIndicatorLayers(undefined, [{ range: { from_ms: before, to_ms: last }, result }]);
            const initial = await drawLabels();
            chart.replaceBars([point(missing), point(last)]);
            return { initial, updated: await drawLabels() };
          } finally {
            chart.dispose();
            element.remove();
            CanvasRenderingContext2D.prototype.fillText = fillText;
          }
        });
        expect(painted.initial).toContain('First fixture bar');
        expect(painted.initial).not.toContain('Updated fixture bar');
        expect(painted.initial).not.toContain('02 Jan, 13:05');
        expect(painted.updated).toContain('Updated fixture bar');
        expect(painted.updated).not.toContain('First fixture bar');
        for (const labels of [painted.initial, painted.updated]) {
          expect(labels).toContain('11.00');
          expect(Math.max(...labels.map(Number).filter(Number.isFinite))).toBeLessThan(20);
        }
      });
    });
  } finally {
    await profile.close();
  }
});

test('private decision notes require consent and retain the dated original separately from outcomes', async ({ browser }) => {
  const profile = await makeProfile();
  const other = await makeProfile();
  const port = await freePort();
  const declinedThesis = 'Synthetic draft that should never be saved';
  const thesis = 'Synthetic thesis at decision time';
  const outcome = 'Synthetic later observation';
  let originalWriter;
  try {
    await inService(port, profile, 'ubuntu-workspace', async server => {
      originalWriter = await writerPid(server);
      await inFreshBrowser(browser, server, async page => {
        await act(page, '5m', 'chart.setTimeframe');
        await control(page, 'Decision notes').click();
        const notes = page.getByRole('dialog', { name: 'Decision notes' });
        const newNote = notes.getByRole('button', { name: 'New decision note' });
        await newNote.click();
        await notes.getByLabel('Decision date').fill('2026-01-02');
        await notes.getByLabel('Thesis').fill(declinedThesis);
        await notes.getByLabel('Choice').fill('Synthetic wait decision');
        await notes.getByRole('button', { name: 'Review decision note' }).click();
        await expect(notes.getByRole('heading', { name: 'Save this decision note locally?' })).toBeVisible();
        const declined = await duringResponse(page, 'decisionNote.save', () =>
          notes.getByRole('button', { name: 'Cancel action' }).click());
        expect((await declined.json()).result.outcome).toMatchObject({ state: 'failed', error: { code: 'CANCELLED' } });
        await expect(notes.getByText('No saved decision notes.')).toBeVisible();
        await expect(notes.getByLabel('Thesis')).toHaveCount(0);

        await newNote.click();
        await notes.getByLabel('Decision date').fill('2026-01-03');
        await notes.getByLabel('Thesis').fill(thesis);
        await notes.getByLabel('Choice').fill('Wait for the next synthetic revision');
        await notes.getByLabel('Source ID').fill('synthetic-series');
        await notes.getByLabel('Source revision').fill('frozen-input');
        await notes.getByLabel('Locator').fill('fixture:2026-01-03');
        await notes.getByRole('button', { name: 'Review decision note' }).click();
        await expect(notes.getByRole('heading', { name: 'Save this decision note locally?' })).toBeVisible();

        const lost = await dropApprovalReply(page, server, 'decisionNote.save');
        await notes.getByRole('button', { name: 'Save decision note' }).click();
        await expect(notes.getByRole('button', { name: 'Retry confirmation' })).toBeEnabled();
        const committed = lost.outcome();
        expect(committed).toMatchObject({ state: 'committed', receipt: { committed_workspace_revision: 2, undoable: false } });
        expect(committed).not.toHaveProperty('result');
        await lost.restore();
        const replay = await duringResponse(page, 'decisionNote.save', () =>
          notes.getByRole('button', { name: 'Retry confirmation' }).click());
        expect((await replay.json()).result.outcome).toEqual(committed);
        await expect(notes.getByText(thesis)).toBeVisible();
        await expect(notes.getByText('No later outcomes.')).toBeVisible();
        await notes.getByRole('button', { name: 'Close decision notes' }).click();
        await expect(control(page, '5m')).toHaveAttribute('aria-pressed', 'true');
        await expect(control(page, 'Undo timeframe change')).toBeEnabled();
        expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
      });
    });

    await inService(await freePort(), other, 'ubuntu-workspace', async server => {
      await inFreshBrowser(browser, server, async page => {
        await control(page, 'Decision notes').click();
        const notes = page.getByRole('dialog', { name: 'Decision notes' });
        await expect(notes.getByText('No saved decision notes.')).toBeVisible();
      });
    });

    await inService(port, profile, 'ubuntu-workspace', async server => {
      expect(await writerPid(server)).not.toBe(originalWriter);
      await inFreshBrowser(browser, server, async (page, initial) => {
        expect(state(initial)).toMatchObject({ revision: 2, timeframe: '5m', undo: { committed_workspace_revision: 1 } });
        await control(page, 'Decision notes').click();
        const notes = page.getByRole('dialog', { name: 'Decision notes' });
        await notes.getByRole('button', { name: /2026-01-03/ }).click();
        const original = notes.getByRole('region', { name: 'Original decision' });
        await expect(original.getByText(thesis)).toBeVisible();
        await expect(original.getByText('synthetic-series · frozen-input · fixture:2026-01-03')).toBeVisible();
        await expect(original.getByText('Wait for the next synthetic revision')).toBeVisible();

        await notes.getByRole('button', { name: 'Add later outcome' }).click();
        await notes.getByLabel('Outcome date').fill('2026-02-04');
        await notes.getByLabel('Outcome', { exact: true }).fill(outcome);
        await notes.getByRole('button', { name: 'Review later outcome' }).click();
        await notes.getByRole('button', { name: 'Save later outcome' }).click();
        const outcomes = notes.getByRole('region', { name: 'Later outcomes' });
        await expect(outcomes.getByText(outcome)).toBeVisible();
        await expect(outcomes.getByText('2026-02-04')).toBeVisible();
        await expect(original.getByText('2026-01-03', { exact: true })).toBeVisible();

        await notes.getByRole('button', { name: 'Review deletion' }).click();
        await expect(notes.getByRole('heading', { name: 'Delete this decision note and its later outcomes?' })).toBeVisible();
        const lost = await dropApprovalReply(page, server, 'decisionNote.delete');
        await notes.getByRole('button', { name: 'Delete decision note' }).click();
        await expect(notes.getByRole('button', { name: 'Retry confirmation' })).toBeEnabled();
        expect(lost.outcome()).toMatchObject({ state: 'committed', receipt: { committed_workspace_revision: 4 } });
        await expect(notes.getByText(thesis)).toHaveCount(0);
        await expect(notes.getByText(outcome)).toHaveCount(0);
        await lost.restore();
        await notes.getByRole('button', { name: 'Retry confirmation' }).click();
        await expect(notes.getByText('No saved decision notes.')).toBeVisible();
        await expect(notes.getByText(thesis)).toHaveCount(0);
        await expect(notes.getByText(outcome)).toHaveCount(0);
        await notes.getByRole('button', { name: 'Close decision notes' }).click();
        const undo = await act(page, 'Undo timeframe change', 'workspace.undo');
        expect(undo.result.outcome).toMatchObject({ state: 'committed', result: { revision: 5, timeframe: '1m' } });
      });
    });

    await inService(port, profile, 'ubuntu-workspace', async server => {
      await inFreshBrowser(browser, server, async (page, initial) => {
        expect(state(initial)).toMatchObject({ revision: 5, timeframe: '1m', undo: null });
        await control(page, 'Decision notes').click();
        const notes = page.getByRole('dialog', { name: 'Decision notes' });
        await expect(notes.getByText('No saved decision notes.')).toBeVisible();
      });
    });
  } finally {
    await Promise.all([profile.close(), other.close()]);
  }
});

test('decision-note backup restore previews known deletions and independent copies before apply', async ({ browser }) => {
  const profile = await makeProfile();
  const independent = await makeProfile(profile.id);
  const wrongProfile = await makeProfile();
  const port = await freePort();
  const deleted = { date: '2026-01-03', thesis: 'Synthetic original later deleted', choice: 'Wait',
    outcomes: [{ date: '2026-02-03', body: 'Synthetic outcome later deleted' }] };
  const retained = { date: '2026-01-08', thesis: 'Synthetic retained original', choice: 'Revisit',
    outcomes: [{ date: '2026-02-08', body: 'Synthetic first retained outcome' },
      { date: '2026-02-09', body: 'Synthetic second retained outcome' }] };
  const current = { date: '2026-01-21', thesis: 'Synthetic note saved after backup', choice: 'Observe', outcomes: [] };
  const warning = /An independent older backup may bring back previously deleted decision notes/;
  let untouched;
  let withMarker;
  try {
    await inService(port, profile, 'ubuntu-workspace', async server => {
      await inFreshBrowser(browser, server, async page => {
        await act(page, '5m', 'chart.setTimeframe');
        await control(page, 'Decision notes').click();
        const notes = page.getByRole('dialog', { name: 'Decision notes' });
        await saveBackupTestNote(notes, deleted);
        await saveBackupTestNote(notes, retained);
        untouched = await downloadBackupFile(page, notes);
        const archive = JSON.parse(untouched.toString('utf-8'));
        expect(Object.keys(archive).sort()).toEqual([
          'captured_workspace_revision', 'deletions', 'format', 'notes', 'profile_binding', 'version',
        ]);
        expect(archive).toMatchObject({ format: 'scopehub.decision-notes', version: 1, deletions: [] });
        expect(archive.notes).toHaveLength(2);

        await notes.getByRole('button', { name: new RegExp(deleted.date) }).click();
        await notes.getByRole('button', { name: 'Review deletion' }).click();
        await notes.getByRole('button', { name: 'Delete decision note' }).click();
        await expect(notes.getByText('Decision note deleted.')).toBeVisible();
        await saveBackupTestNote(notes, current);

        const preview = await chooseBackup(notes, untouched);
        await expect(preview.locator('dd').nth(2)).toHaveText('0');
        await expect(preview.locator('dd').nth(3)).toHaveText('1');
        await expect(preview.getByText(warning)).toHaveCount(0);
        await expect(notes.getByText(deleted.thesis)).toHaveCount(0);
        await preview.getByRole('button', { name: 'Cancel restore' }).click();
        await expect(notes.getByText('Restore cancelled.')).toBeVisible();
        await expect(notes.getByRole('button', { name: new RegExp(current.date) })).toBeVisible();

        await (await chooseBackup(notes, untouched)).getByRole('button', { name: 'Apply restore' }).click();
        await expect(notes.getByText('Decision-note backup restored.')).toBeVisible();
        await expect(notes.getByRole('button', { name: new RegExp(deleted.date) })).toHaveCount(0);
        await expect(notes.getByRole('button', { name: new RegExp(current.date) })).toBeVisible();
        await assertBackupTestNote(notes, retained);
        await notes.getByRole('button', { name: 'All decision notes' }).click();
        withMarker = await downloadBackupFile(page, notes);
        const afterDeletion = JSON.parse(withMarker.toString('utf-8'));
        expect(afterDeletion.deletions).toEqual([
          { note_id: archive.notes.find(note => note.original.decided_on === deleted.date).original.note_id,
            deleted_at_ms: expect.any(Number) },
        ]);
        expect(withMarker.includes(deleted.thesis)).toBe(false);
        expect(withMarker.includes(deleted.outcomes[0].body)).toBe(false);
      });
    });

    await inService(port, profile, 'ubuntu-workspace', async server => {
      const writer = await writerPid(server);
      await inFreshBrowser(browser, server, async (page, initial) => {
        expect(state(initial)).toMatchObject({ timeframe: '5m', undo: { committed_workspace_revision: 1 } });
        await control(page, 'Decision notes').click();
        const notes = page.getByRole('dialog', { name: 'Decision notes' });
        await expect(notes.getByRole('button', { name: new RegExp(deleted.date) })).toHaveCount(0);
        await assertBackupTestNote(notes, retained);
        await writerPid(server, 'kill', writer);
        await expect(notes.getByText(retained.thesis)).toHaveCount(0);
        await expect(notes.getByText(retained.outcomes[0].body)).toHaveCount(0);
      });
    });

    await inService(port, independent, 'ubuntu-workspace', async server => {
      await inFreshBrowser(browser, server, async page => {
        await control(page, 'Decision notes').click();
        const notes = page.getByRole('dialog', { name: 'Decision notes' });
        const preview = await chooseBackup(notes, untouched);
        await expect(preview.getByText(warning)).toBeVisible();
        await expect(notes.getByText(deleted.thesis)).toHaveCount(0);
        await preview.getByRole('button', { name: 'Cancel restore' }).click();
        await expect(notes.getByText('No saved decision notes.')).toBeVisible();
        const approval = await chooseBackup(notes, untouched);
        await expect(approval.getByText(warning)).toBeVisible();
        await approval.getByRole('button', { name: 'Apply restore' }).click();
        await expect(notes.getByText('Decision-note backup restored.')).toBeVisible();
        await assertBackupTestNote(notes, deleted);
        await notes.getByRole('button', { name: 'All decision notes' }).click();
        await inFreshBrowser(browser, server, async observer => {
          await control(observer, 'Decision notes').click();
          const observedNotes = observer.getByRole('dialog', { name: 'Decision notes' });
          await assertBackupTestNote(observedNotes, deleted);

          const importMarker = await chooseBackup(notes, withMarker);
          await expect(importMarker.locator('dd').nth(2)).toHaveText('1');
          await expect(importMarker.locator('dd').nth(4)).toHaveText('1');
          await importMarker.getByRole('button', { name: 'Apply restore' }).click();
          await expect(notes.getByRole('button', { name: new RegExp(deleted.date) })).toHaveCount(0);
          await expect(observedNotes.getByText(deleted.thesis)).toHaveCount(0);
          await expect(observedNotes.getByText(deleted.outcomes[0].body)).toHaveCount(0);
          await expect(observedNotes.getByRole('button', { name: new RegExp(retained.date) })).toBeVisible();
        });
        const older = await chooseBackup(notes, untouched);
        await expect(older.locator('dd').nth(3)).toHaveText('1');
        await expect(older.getByText(warning)).toHaveCount(0);
        await older.getByRole('button', { name: 'Cancel restore' }).click();
        await assertBackupTestNote(notes, retained);
      });
    });

    await inService(port, wrongProfile, 'ubuntu-workspace', async server => {
      await inFreshBrowser(browser, server, async page => {
        await control(page, 'Decision notes').click();
        const notes = page.getByRole('dialog', { name: 'Decision notes' });
        const rejected = await duringResponse(page, 'decisionNote.previewRestore', () => selectBackupFile(notes, untouched));
        expect((await rejected.json()).result.outcome).toMatchObject({ state: 'failed', error: { code: 'PERMISSION_DENIED' } });
        await expect(notes.getByText('This backup or action is unavailable for the current local profile.')).toBeVisible();
        await expect(notes.getByRole('region', { name: 'Restore preview' })).toHaveCount(0);
        await expect(notes.getByText('No saved decision notes.')).toBeVisible();
      });
    });
  } finally {
    await Promise.all([profile.close(), independent.close(), wrongProfile.close()]);
  }
});

test('decision-note draft keeps its text and date across peer timeframe changes', async ({ browser }) => {
  const profile = await makeProfile();
  const fixture = { date: '2026-02-03', thesis: 'Synthetic decision for a pending outcome', choice: 'Wait', outcomes: [] };
  const draft = 'Synthetic unsubmitted reflection';
  const date = '2026-04-09';
  try {
    await inService(await freePort(), profile, 'ubuntu-workspace', async server => {
      await inFreshBrowser(browser, server, async page => {
        await control(page, 'Decision notes').click();
        await saveBackupTestNote(page.getByRole('dialog', { name: 'Decision notes' }), fixture);
      });
      await inFreshBrowser(browser, server, async observer => {
        await control(observer, 'Decision notes').click();
        const notes = observer.locator('dialog.decision-dialog');
        await assertBackupTestNote(notes, fixture);
        await notes.getByRole('button', { name: 'Add later outcome' }).click();
        const outcome = notes.locator('textarea[name="body"]');
        const outcomeDate = notes.locator('input[name="date"]');
        await outcome.fill(draft);
        await outcomeDate.fill(date);

        await inFreshBrowser(browser, server, async peer => {
          await duringResponse(observer, 'decisionNote.read', async () => {
            expect((await act(peer, '5m', 'chart.setTimeframe')).result.outcome.state).toBe('committed');
            await observer.bringToFront();
          }, 10_000);
          await expect(outcome).toHaveValue(draft);
          await expect(outcomeDate).toHaveValue(date);

          await notes.getByRole('button', { name: 'Review later outcome' }).click();
          const approval = notes.getByRole('button', { name: 'Save later outcome' });
          await expect(approval).toBeVisible();
          await peer.bringToFront();
          expect((await act(peer, '1d', 'chart.setTimeframe')).result.outcome.state).toBe('committed');
          await observer.bringToFront();
          await expect(approval).toHaveCount(0);
          await expect(outcome).toHaveValue(draft);
          await expect(outcomeDate).toHaveValue(date);
        });

        await notes.getByRole('button', { name: 'Review later outcome' }).click();
        await notes.getByRole('button', { name: 'Save later outcome' }).click();
        await expect(notes.getByRole('region', { name: 'Later outcomes' }).getByText(draft)).toBeVisible();
        await expect(notes.getByRole('region', { name: 'Later outcomes' }).getByText(date)).toBeVisible();
        await notes.getByRole('button', { name: 'Add later outcome' }).click();
        const deletedDraft = 'Synthetic draft to remove on peer deletion';
        await outcome.fill(deletedDraft);
        await inFreshBrowser(browser, server, async peer => {
          await control(peer, 'Decision notes').click();
          await deleteBackupTestNote(peer.getByRole('dialog', { name: 'Decision notes' }), fixture.date);
          await observer.bringToFront();
          await expect(outcome).toHaveCount(0);
          await expect(notes.getByText(fixture.thesis)).toHaveCount(0);
          await expect(notes.getByText(draft)).toHaveCount(0);
          await expect(notes.getByText('No saved decision notes.')).toBeVisible();
        });
      });
    });
  } finally {
    await profile.close();
  }
});

test('decision-note list removes peer deletions after focus and a rejected detail read', async ({ browser }) => {
  const profile = await makeProfile();
  const first = { date: '2026-02-03', thesis: 'Synthetic note removed while a list stays open', choice: 'Wait', outcomes: [] };
  const second = { date: '2026-02-04', thesis: 'Synthetic note removed before its detail read', choice: 'Wait', outcomes: [] };
  try {
    await inService(await freePort(), profile, 'ubuntu-workspace', async server => {
      await inFreshBrowser(browser, server, async page => {
        await control(page, 'Decision notes').click();
        const notes = page.getByRole('dialog', { name: 'Decision notes' });
        await saveBackupTestNote(notes, first);
        await saveBackupTestNote(notes, second);
      });
      await inFreshBrowser(browser, server, async observer => {
        await control(observer, 'Decision notes').click();
        const notes = observer.getByRole('dialog', { name: 'Decision notes' });
        const firstRow = notes.getByRole('button', { name: new RegExp(first.date) });
        const secondRow = notes.getByRole('button', { name: new RegExp(second.date) });
        await expect(firstRow).toBeVisible();
        await expect(secondRow).toBeVisible();
        // Both contexts share SQLite, with a separate writer lane for each peer.
        await inService(await freePort(), profile, 'ubuntu-workspace', async peerServer => {
          await inFreshBrowser(browser, peerServer, async peer => {
            expect(await writerPid(peerServer)).not.toBe(await writerPid(server));
            await control(peer, 'Decision notes').click();
            const peerNotes = peer.getByRole('dialog', { name: 'Decision notes' });
            await deleteBackupTestNote(peerNotes, first.date);
            await observer.evaluate(() => window.dispatchEvent(new Event('focus')));
            await expect(firstRow).toHaveCount(0);
            await expect(secondRow).toBeVisible();

            let intercepted = false;
            const resume = Promise.withResolvers();
            await observer.route(server.url + endpoint, async route => {
              const intent = route.request().postDataJSON()?.intent;
              if (intent?.operation === 'decisionNote.read' && intent.arguments.note_id !== null) {
                intercepted = true;
                await resume.promise;
              }
              await route.continue();
            });
            try {
              await secondRow.click();
              await expect.poll(() => intercepted, { message: 'The observer detail read was held before peer deletion.' }).toBe(true);
              await deleteBackupTestNote(peerNotes, second.date);
            } finally {
              resume.resolve();
              await observer.unrouteAll({ behavior: 'wait' });
            }
            await expect(notes.getByText('This decision note is unavailable.')).toBeVisible();
            await expect(secondRow).toHaveCount(0);
            await expect(notes.getByText('No saved decision notes.')).toBeVisible();
          });
        });
      });
    });
  } finally {
    await profile.close();
  }
});

const futureHistory = { '1m': 40, '5m': 8, '1d': 2 };
const sampleButton = page => control(page, 'Open offline sample');
const retryHistory = page => control(page, 'Retry history');

async function holdSampleHistory(page, from) {
  const handler = async route => {
    const call = route.request().postDataJSON();
    if (call?.operation !== 'market.readHistoryPage' || call.payload.from_ms !== from) return route.continue();
    const headers = await route.request().allHeaders();
    return route.continue({ headers: { ...headers, 'x-scopehub-test-interrupt': 'hold-success' } });
  };
  await page.route(`**${operationPath}`, handler);
  return () => page.unroute(`**${operationPath}`, handler);
}

async function expectedLayerRead(historyRequest) {
  const reply = await historyRequest.existingResponse().json();
  expect(reply.ok).toBe(true);
  return {
    type: 'indicator_accepted_layers', revision_id: reply.result.snapshot.dataset_id,
    request_id: expect.stringMatching(/^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/),
  };
}

function historyWireAttempts(stages = []) {
  const attempts = new Map();
  for (const { attempt, ...event } of stages) {
    if (event.stage === 'held-disconnect' || event.stage === 'held-success') continue;
    expect(attempt, 'Each HTTP history trace has an attempt ID').toEqual(expect.any(String));
    if (!attempts.has(attempt)) attempts.set(attempt, []);
    attempts.get(attempt).push(event);
  }
  return [...attempts.values()];
}

function historyAttemptsComplete(stages) {
  const attempts = historyWireAttempts(stages);
  let complete = attempts.length > 0;
  for (const events of attempts) {
    expect(events[0].stage, 'An HTTP history attempt starts with receipt').toBe('received');
    const terminals = events.filter(event => event.stage === 'finished' || event.stage === 'closed');
    expect(terminals.length, 'One terminal per HTTP history attempt').toBeLessThanOrEqual(1);
    if (terminals.length === 0) complete = false;
    else expect(events.at(-1), 'The terminal ends the HTTP history attempt').toBe(terminals[0]);
  }
  return complete;
}

function expectDisconnectedHistory(stages) {
  const attempts = historyWireAttempts(stages);
  expect(attempts.length).toBeGreaterThan(0);
  for (const events of attempts) {
    expect(events).toEqual([
      { stage: 'received' }, { stage: 'admitted' }, { stage: 'closed', status: 200 },
    ]);
  }
}

async function sampleStep(page, server, testInfo, label, trigger, {
  timeframe = '1m', catalog = true, error, retryable = false, historyCall = sampleHistoryCall(timeframe),
  bars = futureHistory[timeframe], fixtureHoldMs = 0,
} = {}) {
  const observer = observeSampleRequests(page, { marketOnly: true });
  const selection = { id: future, timeframe };
  const history = page.locator(`.history[data-instrument="${future}"][data-timeframe="${timeframe}"]`);
  let terminal;
  try {
    await trigger();
    await expect(page.locator('.connection')).toHaveText('Offline sampleSimulated');
    await expect(control(page, timeframe)).toHaveAttribute('aria-pressed', 'true');
    if (catalog) await observer.read(sampleCatalogCall.operation);
    await observer.read('market.readHistoryPage', { allowFailure: Boolean(error), fixtureHoldMs });
    terminal = await waitForSelectedHistoryTerminal(page, history.locator('canvas').first(), selection);
    expect(terminal, label).toEqual(error ? { state: 'error', message: error } : { state: 'chart' });
    await expect(retryHistory(page)).toHaveCount(retryable ? 1 : 0);
    if (error) await expect(page.locator('.history')).toHaveCount(0);
    else await expect(history).toHaveAttribute('data-bars', String(bars));

    const terminalRequest = [...observer.attempts.keys()].at(-1);
    const allHistoryAttemptsComplete = () => [...observer.attempts.keys()]
      .map(request => historyAttemptsComplete(server.historyStages.get(request.headers()[traceHeader])))
      .every(Boolean);
    // The browser terminal can precede the arrival of already emitted stdout.
    // Drain those receipts, then wait for each observed response to terminate.
    await server.flushHistoryTrace();
    await expect.poll(allHistoryAttemptsComplete,
      { message: `Waiting for all HTTP history attempts to close or finish: ${label}` }).toBe(true);

    const expected = [...(catalog ? [sampleCatalogCall] : []), historyCall];
    let phase = 0;
    for (const [request, attempt] of observer.attempts) {
      const call = expected[phase];
      expect(attempt.call, label).toEqual(call);
      const response = request.existingResponse();
      if (response?.status() === 429) {
        expect(await response.json()).toEqual(busy);
        continue;
      }
      phase++;
      if (call.operation === 'market.readHistoryPage' && error) continue;
      expect(request.failure()).toBeNull();
      expect(response?.status()).toBe(200);
      expect(await response.finished()).toBeNull();
      const reply = await response.json();
      expect(reply.ok).toBe(true);
      if (call.operation === 'instrument.search') {
        expect(reply.result.instruments.map(instrument => instrument.id)).toEqual([
          future, 'SIM.CN.STOCK.001', 'SIM.US.STOCK.001',
        ]);
      } else {
        const { series, expected_revision: dataset_revision, from_ms, to_ms, warmup_bars } = call.payload;
        expect(reply.result).toMatchObject({
          snapshot: { series, dataset_revision, from_ms, to_ms, warmup_bars },
          dataset: { provider_id: 'scopehub.synthetic', mode: 'simulated' },
          quality_state: { connection: 'offline' }, total_bars: bars, next_cursor: null,
        });
        expect(reply.result.bars).toHaveLength(bars);
        expect(reply.result.snapshot.content_hash).toMatch(/^[a-f0-9]{64}$/);
      }
    }
    expect(phase, label).toBe(expected.length);
    await server.flushHistoryTrace();
    expect(allHistoryAttemptsComplete()).toBe(true);
    return terminalRequest;
  } finally {
    observer.stop();
    const requests = [...observer.attempts.keys()];
    const stages = await readMarketAttemptStages(page, requests);
    const attempts = stages.map((stage, index) => ({
      call: observer.attempts.get(requests[index]).call, ...stage, host: server.historyStages.get(stage.trace),
    }));
    await testInfo.attach(label, { contentType: 'application/json', body: JSON.stringify({ selection, terminal, attempts }) });
  }
}

for (const finishes of [true, false]) {
  const outcome = finishes ? 'observes a late retry terminal' : 'rejects a missing retry terminal';
  test(`reopened history observer ${outcome}`, async ({}, testInfo) => {
    const page = new EventEmitter();
    const request = {
      method: () => 'POST', url: () => `http://127.0.0.1${operationPath}`, postDataJSON: () => sampleHistoryCall(),
      failure: () => ({ errorText: 'net::ERR_EMPTY_RESPONSE' }), isNavigationRequest: () => false,
    };
    const response = { request: () => request, status: () => 200 };
    const holdMs = 5_500;
    const lateMs = holdMs + 15_000 - 1;
    const clock = { probes_ms: [] };
    const poll = (read, { timeout, message }) => ({
      async toBe(expected) {
        clock.deadline_ms = timeout;
        for (const at of new Set([0, holdMs, 18_000, lateMs, timeout].filter(at => at <= timeout))) {
          if (at === 0) page.emit('request', request);
          // The physical retry is hidden from the browser's logical request observer.
          if (at === lateMs) {
            if (finishes) page.emit('requestfailed', request);
            else page.emit('response', response);
          }
          clock.probes_ms.push(at);
          if (await read() === expected) return;
        }
        throw new Error(message);
      },
    });
    const observer = observeSampleRequests(page, { marketOnly: true, poll });
    try {
      const settled = observer.read('market.readHistoryPage', { allowFailure: true, fixtureHoldMs: holdMs });
      if (finishes) {
        await settled;
        expect(observer.attempts.get(request).failure).toBe('net::ERR_EMPTY_RESPONSE');
        expect(clock.probes_ms.at(-1)).toBe(lateMs);
      } else {
        await expect(settled).rejects.toThrow('to settle during sample startup');
        expect(observer.attempts.get(request)).toMatchObject({ response });
        expect(observer.attempts.get(request).finished).toBeUndefined();
        expect(observer.attempts.get(request).failure).toBeUndefined();
        expect(clock.probes_ms.at(-1)).toBe(23_500);
      }
      expect(observer.attempts.size).toBe(1);
      expect(clock.deadline_ms).toBe(23_500);
    } finally {
      observer.stop();
      await testInfo.attach('reopened-history-observer-clock', {
        body: JSON.stringify({ ...clock, hold_ms: holdMs, retry_event_ms: lateMs, terminal_supplied: finishes }),
        contentType: 'application/json',
      });
    }
  });
}

test('the offline sample keeps its own timeframe without replacing the private undo', async ({ browser }, testInfo) => {
  test.setTimeout(90_000);
  const profile = await makeProfile();
  const port = await freePort();
  const context = await browser.newContext();
  try {
    await inService(port, profile, 'ubuntu-workspace', async server => {
      const page = await context.newPage();
      await observeSelectedHistory(page, { includeCatalog: true });
      const launchPath = '/synthetic-private-launch';
      await page.addInitScript(({ launchPath, capability }) => {
        // Simulate the real handoff without exposing its fragment in navigation
        // artifacts or re-injecting it on refresh after the UI scrubs it.
        if (location.pathname === launchPath) history.replaceState(null, '', '/#workspace=' + capability);
      }, { launchPath, capability: server.capability });
      const workspaceRequests = [];
      context.on('request', request => {
        if (new URL(request.url()).pathname !== endpoint) return;
        workspaceRequests.push(request.postDataJSON());
      });
      const launch = () => page.goto(server.url + launchPath);
      const shellUrl = url => url.origin === server.url && url.pathname === '/' && url.hash === '';
      const sampleSource = { kind: 'sample', instrument_id: null };
      const noSource = { kind: 'none' };
      const readPrivate = async (selectionRevision, source) => {
        const frame = await (await post(server, snapshot)).json();
        const current = state(frame, selectionRevision);
        expect(frame.result.source).toEqual(source);
        return current;
      };
      const assertSourceWrite = async (start, expectedSelection, source, layerRead) => {
        await expect.poll(() => workspaceRequests.slice(start)).toEqual([{
          type: 'workspace_source', source, expected_selection_revision: expectedSelection,
          request_id: expect.stringMatching(/^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/),
        }, snapshot, ...(layerRead ? [layerRead] : [])]);
      };
      const undo = control(page, 'Undo timeframe change');

      await launch();
      await expect(control(page, '1m')).toHaveAttribute('aria-pressed', 'true');
      await expect(page).toHaveURL(server.url + '/');
      await expect(page.locator('.connection')).toHaveText('No data source');
      expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
      expect(await readPrivate(0, null)).toEqual({ revision: 0, timeframe: '1m', undo: null });
      expect((await act(page, '5m', 'chart.setTimeframe')).result.outcome)
        .toMatchObject({ state: 'committed', result: { timeframe: '5m', revision: 1 } });
      const prior = (await act(page, '1m', 'chart.setTimeframe')).result.outcome;
      expect(prior).toMatchObject({ state: 'committed', result: { timeframe: '1m', revision: 2 }, receipt: { undoable: true } });
      const before = {
        revision: 2, timeframe: '1m',
        undo: { operation_id: prior.receipt.operation_id, committed_workspace_revision: 2 },
      };
      expect(await readPrivate(0, null)).toEqual(before);
      await expect(undo).toBeEnabled();

      const beforeSample = workspaceRequests.length;
      const firstHistory = await sampleStep(page, server, testInfo, 'first explicit sample open', () => sampleButton(page).click());
      await expect(page).toHaveURL(shellUrl);
      await expect(control(page, 'Edit layout')).toBeEnabled();
      const oneMinuteRead = await expectedLayerRead(firstHistory);
      await assertSourceWrite(beforeSample, 0, sampleSource, oneMinuteRead);
      const duringSample = workspaceRequests.length;
      const fiveMinuteHistory = await sampleStep(page, server, testInfo, 'sample five-minute selection', () => control(page, '5m').click(),
        { timeframe: '5m', catalog: false });
      const fiveMinuteRead = await expectedLayerRead(fiveMinuteHistory);
      const after = await readPrivate(1, sampleSource);
      const refreshed = await context.newPage();
      await observeSelectedHistory(refreshed, { includeCatalog: true });
      await sampleStep(refreshed, server, testInfo, 'shared sample preference open', () => refreshed.goto(server.url), { timeframe: '5m' });
      await sampleStep(refreshed, server, testInfo, 'shared sample preference refresh', () => refreshed.reload(), { timeframe: '5m' });
      const refreshedTimeframe = await refreshed.locator('.history').getAttribute('data-timeframe');
      expect(after).toEqual(before);
      expect(workspaceRequests.slice(duringSample)).toEqual([fiveMinuteRead]);
      await expect(undo).toHaveCount(0);
      await expect(control(refreshed, 'Undo timeframe change')).toHaveCount(0);
      await assertSampleStorage(refreshed, '5m');
      await refreshed.close();

      const beforeLeave = workspaceRequests.length;
      await leaveSample(page);
      await expect(page).toHaveURL(shellUrl);
      await expect(page.locator('.connection')).toHaveText('No data source');
      await expect(control(page, '1m')).toHaveAttribute('aria-pressed', 'true');
      await expect(undo).toBeEnabled();
      await assertSourceWrite(beforeLeave, 1, noSource);
      expect(await readPrivate(2, noSource)).toEqual(before);
      const reply = await duringResponse(page, 'workspace.undo', () => undo.click());
      const undoRequest = reply.request().postDataJSON();
      expect(undoRequest.intent.arguments).toEqual({ operation_id: prior.receipt.operation_id });
      expect(undoRequest.scope).toMatchObject({ expected_workspace_revision: 2, expected_selection_revision: 2 });
      expect((await reply.json()).result.outcome).toMatchObject({
        state: 'committed', result: { timeframe: '5m', revision: 3 }, receipt: { undoable: false },
      });
      await expect(undo).toBeDisabled();
      const afterUndo = { revision: 3, timeframe: '5m', undo: null };
      expect(await readPrivate(2, noSource)).toEqual(afterUndo);
      expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);

      const beforeReload = workspaceRequests.length;
      await page.reload();
      await expect(page).toHaveURL(shellUrl);
      await expect(control(page, 'Open offline sample')).toBeVisible();
      await expect(control(page, '1m')).toHaveAttribute('aria-pressed', 'true');
      await expect(undo).toHaveCount(0);
      expect(workspaceRequests.slice(beforeReload)).toEqual([]);
      await launch();
      await expect(page).toHaveURL(server.url + '/');
      await expect(control(page, '5m')).toHaveAttribute('aria-pressed', 'true');
      await expect(undo).toBeDisabled();
      expect(workspaceRequests.slice(beforeReload)).toEqual([snapshot]);

      const beforeSampleReload = workspaceRequests.length;
      await sampleStep(page, server, testInfo, 'second explicit sample open', () => sampleButton(page).click());
      await assertSourceWrite(beforeSampleReload, 2, sampleSource, oneMinuteRead);
      const duringSampleReload = workspaceRequests.length;
      const dailyHistory = await sampleStep(page, server, testInfo, 'sample daily selection', () => control(page, '1d').click(),
        { timeframe: '1d', catalog: false });
      const dailyRead = await expectedLayerRead(dailyHistory);
      expect(await readPrivate(3, sampleSource)).toEqual(afterUndo);
      await assertSampleStorage(page, '1d');
      await sampleStep(page, server, testInfo, 'sample daily refresh', () => page.reload(), { timeframe: '1d' });
      await expect(undo).toHaveCount(0);
      await assertSampleStorage(page, '1d');
      await leaveSample(page);
      await expect(page.locator('.connection')).toHaveText('No data source');
      await expect(control(page, '1m')).toHaveAttribute('aria-pressed', 'true');
      await expect(undo).toHaveCount(0);
      expect(workspaceRequests.slice(duringSampleReload)).toEqual([dailyRead]);
      expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
      expect(await readPrivate(3, sampleSource)).toEqual(afterUndo);

      // The browser-only leave discarded its presentation preference without a local grant.
      // Relaunching the profile restores its explicit sample at the browser's default period.
      const beforeProfileRestore = workspaceRequests.length;
      await sampleStep(page, server, testInfo, 'restore the profile sample', launch);
      await expect(page).toHaveURL(shellUrl);
      await expect(undo).toHaveCount(0);
      await expect.poll(() => workspaceRequests.slice(beforeProfileRestore)).toEqual([snapshot, oneMinuteRead]);
      expect(await readPrivate(3, sampleSource)).toEqual(afterUndo);
      const beforeProfileLeave = workspaceRequests.length;
      await leaveSample(page);
      await expect(page).toHaveURL(shellUrl);
      await expect(page.locator('.connection')).toHaveText('No data source');
      await expect(control(page, '5m')).toHaveAttribute('aria-pressed', 'true');
      await expect(undo).toBeDisabled();
      await assertSourceWrite(beforeProfileLeave, 3, noSource);
      expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
      expect(await readPrivate(4, noSource)).toEqual(afterUndo);
      await testInfo.attach('workspace-sample-timeframe', {
        body: JSON.stringify({ before, after, afterUndo, refreshedTimeframe,
          sampleWorkspaceRequests: workspaceRequests.slice(beforeSample), finalSelectionRevision: 4 }),
        contentType: 'application/json',
      });
    }, { historyTrace: true });
  } finally {
    await context.close();
    await profile.close();
  }
});

test('the reopened sample recovers its history without consuming the private undo', async ({ browser }, testInfo) => {
  test.setTimeout(90_000);
  await test.step('correlate history terminals across a controlled socket retry interleaving', () => {
    const first = [
      { attempt: 'first', stage: 'received' }, { attempt: 'first', stage: 'admitted' },
      { stage: 'held-disconnect', elapsed_ms: 5507 }, { attempt: 'first', stage: 'closed', status: 200 },
    ];
    const second = [
      { attempt: 'second', stage: 'received' }, { attempt: 'second', stage: 'admitted' },
      { attempt: 'second', stage: 'closed', status: 200 },
    ];
    // PR 77's Windows trace admitted the second request before recording the first close.
    const interleaved = [...first.slice(0, -1), ...second.slice(0, -1), first.at(-1), second.at(-1)];
    for (const trace of [first, interleaved]) {
      expect(historyAttemptsComplete(trace)).toBe(true);
      expectDisconnectedHistory(trace);
    }
    expect(historyAttemptsComplete(first.slice(0, -1))).toBe(false);
    expect(historyAttemptsComplete(interleaved.slice(0, -1))).toBe(false);
    expect(() => historyAttemptsComplete([...interleaved, first.at(-1)])).toThrow('One terminal per HTTP history attempt');
    for (const [status, prior] of [[200, { stage: 'admitted' }], [403, { stage: 'failed', code: 'PERMISSION_DENIED' }]]) {
      const finished = { attempt: 'first', stage: 'finished', status };
      const trace = [first[0], { attempt: 'first', ...prior }, finished];
      expect(historyAttemptsComplete(trace)).toBe(true);
      expect(() => historyAttemptsComplete([...trace, finished])).toThrow('One terminal per HTTP history attempt');
    }
  });
  const profile = await makeProfile();
  const port = await freePort();
  const disconnected = 'Disconnected from the local preview. Retry when the connection is restored.';
  const selectedCall = sampleHistoryCall();
  const isSelected = request => request.method() === 'POST' && new URL(request.url()).pathname === operationPath
    && request.postDataJSON()?.operation === selectedCall.operation;
  try {
    await inService(port, profile, 'ubuntu-workspace', async server => {
      await inFreshBrowser(browser, server, async page => {
        const writer = await writerPid(server);
        const privateState = async (selectionRevision, kind) => {
          const frame = await (await post(server, snapshot)).json();
          expect(frame.result.source).toEqual(kind === 'sample' ? { kind, instrument_id: null } : { kind });
          return state(frame, selectionRevision);
        };
        const committed = (await act(page, '5m', 'chart.setTimeframe')).result.outcome;
        expect(committed).toMatchObject({ state: 'committed', result: { timeframe: '5m', revision: 1 }, receipt: { undoable: true } });
        const undo = control(page, 'Undo timeframe change');
        const before = {
          revision: 1, timeframe: '5m',
          undo: { operation_id: committed.receipt.operation_id, committed_workspace_revision: 1 },
        };
        const sourceCalls = [];
        page.on('request', request => {
          if (new URL(request.url()).pathname === endpoint) sourceCalls.push(request.postDataJSON());
        });
        const open = () => sampleButton(page).click();
        const retry = () => retryHistory(page).click();
        const step = (label, trigger, options) => sampleStep(page, server, testInfo, label, trigger, options);
        const cutBody = (trigger, status) => async () => {
          const [response] = await Promise.all([
            page.waitForResponse(response => isSelected(response.request())), trigger(),
          ]);
          expect(response.status()).toBe(status);
          await server.cutHistoryResponse();
        };
        const inject = async (fault, change, run) => {
          const handler = async route => {
            const request = route.request();
            if (!isSelected(request)) return route.continue();
            const call = request.postDataJSON();
            const headers = await request.allHeaders();
            if (fault) headers['x-scopehub-test-interrupt'] = fault;
            change?.(call.payload);
            return route.continue({ headers, ...(change && { postData: JSON.stringify(call) }) });
          };
          await page.route(`**${operationPath}`, handler);
          try { return await run(); }
          finally { await page.unroute(`**${operationPath}`, handler); }
        };

        const initial = await step('healthy open before interruption', open);
        const initialHistory = (await initial.existingResponse().json()).result;
        await leaveSample(page);
        await expect(undo).toBeEnabled();
        await inject('before-headers', undefined, async () => {
          // The deliberate hold precedes any physical retry and its own preview watchdog.
          const failed = await step('reopen with lost history response', open, {
            error: disconnected, retryable: true, fixtureHoldMs: firstHistoryDisconnectHoldMs,
          });
          const stillLost = await step('retry while still disconnected', retry, { catalog: false, error: disconnected, retryable: true });
          for (const request of [failed, stillLost]) {
            const [attempt] = await readMarketAttemptStages(page, [request]);
            expect(attempt).toMatchObject({
              failure: expect.stringMatching(/^net::ERR_(EMPTY_RESPONSE|CONNECTION_(ABORTED|CLOSED|RESET))$/),
              client: { stage: 'fetch-rejected', error: 'TypeError', selectionAborted: false },
            });
            expect(request.existingResponse()).toBeNull();
            const stages = server.historyStages.get(attempt.trace);
            const holds = stages.filter(stage => stage.stage === 'held-disconnect');
            expect(holds).toHaveLength(request === failed ? 1 : 0);
            if (request === failed) expect(holds[0].elapsed_ms).toBeGreaterThan(5000);
            expectDisconnectedHistory(stages);
          }
          expect(failed.headers()[traceHeader]).not.toBe(stillLost.headers()[traceHeader]);
          expect(await privateState(3, 'sample')).toEqual(before);
        });
        await inject('during-body-200', undefined, async () => {
          const request = await step('retry with interrupted successful response body', cutBody(retry, 200), {
            catalog: false, error: disconnected, retryable: true,
          });
          const [attempt] = await readMarketAttemptStages(page, [request]);
          expect(attempt).toMatchObject({
            status: 200, client: { stage: 'body-rejected', status: 200, error: 'TypeError', selectionAborted: false },
          });
        });
        const restored = await step('explicit history retry after transport is restored', retry, { catalog: false });
        expect(restored.postDataJSON()).toEqual(selectedCall);
        expect((await restored.existingResponse().json()).result).toEqual(initialHistory);
        await expect(undo).toHaveCount(0);
        await assertSampleStorage(page, '1m');
        await leaveSample(page);

        // A response rejected by the preview still has its real HTTP status even when its body is lost.
        const deniedProvider = payload => { payload.series.provider_id = 'synthetic.denied'; };
        await inject(null, deniedProvider, async () => {
          const request = await step('completed preview denial', open, { error: 'Access to this source is denied.' });
          expect(request.postDataJSON().payload.series.provider_id).toBe('synthetic.denied');
          const response = request.existingResponse();
          expect(response.status()).toBe(403);
          expect(await response.json()).toEqual({ ok: false, error: { code: 'PERMISSION_DENIED', retryable: false } });
        });
        await leaveSample(page);
        await inject('during-body-403', deniedProvider, async () => {
          const request = await step('preview denial with interrupted body', cutBody(open, 403),
            { error: 'The local preview returned HTTP 403.' });
          const [attempt] = await readMarketAttemptStages(page, [request]);
          expect(attempt).toMatchObject({
            status: 403, client: { stage: 'body-rejected', status: 403, error: 'TypeError', selectionAborted: false },
          });
          expect(historyWireAttempts(server.historyStages.get(attempt.trace))).toEqual([[
            { stage: 'received' }, { stage: 'failed', code: 'PERMISSION_DENIED' }, { stage: 'closed', status: 403 },
          ]]);
        });
        await leaveSample(page);
        await inject(null, payload => { payload.expected_revision = 'scopehub-synthetic-v1:20241013:missing'; }, async () => {
          const request = await step('provider revision rejection', open, { error: 'The selected historical revision is unavailable.' });
          expect(request.postDataJSON().payload.expected_revision).toBe('scopehub-synthetic-v1:20241013:missing');
          const response = request.existingResponse();
          expect(response.status()).toBe(200);
          expect(await response.json()).toMatchObject({ ok: false, error: { code: 'SNAPSHOT_UNAVAILABLE', retryable: false } });
        });
        await leaveSample(page);

        const layerRead = await expectedLayerRead(initial);
        const expectedScopeChanges = Array.from({ length: 10 }, (_, revision) => [{
          type: 'workspace_source',
          source: revision % 2 === 0 ? { kind: 'sample', instrument_id: null } : { kind: 'none' },
          expected_selection_revision: revision,
          request_id: expect.stringMatching(/^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/),
        }, snapshot, ...([0, 2].includes(revision) ? [layerRead] : [])]).flat();
        expect(sourceCalls).toEqual(expectedScopeChanges);
        expect(await postStatus(server, snapshot, { Authorization: '' })).toBe(403);
        expect(await privateState(10, 'none')).toEqual(before);
        expect(await writerPid(server)).toBe(writer);
        await expect(control(page, '5m')).toHaveAttribute('aria-pressed', 'true');
        await expect(undo).toBeEnabled();
        expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
        const inverted = await duringResponse(page, 'workspace.undo', () => undo.click());
        expect(inverted.request().postDataJSON().intent.arguments).toEqual({ operation_id: committed.receipt.operation_id });
        expect((await inverted.json()).result.outcome).toMatchObject({ state: 'committed', result: { timeframe: '1m', revision: 2 } });
        await expect(undo).toBeDisabled();
        expect(await privateState(10, 'none')).toEqual({ revision: 2, timeframe: '1m', undo: null });
      }, [endpoint, operationPath], { traceSample: true });
    }, { historyTrace: true, historyFault: true });
  } finally {
    await profile.close();
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
      expect(await postStatus(server, snapshot, { Origin: 'http://localhost' })).toBe(403);
      expect(await postStatus(server, { type: 'snapshot', padding: 'x'.repeat(16 * 1024) })).toBe(413);
      const forged = setTimeframe(0, '5m');
      Object.assign(forged.scope, { principal_id: 'forged', client_id: 'forged', profile_root: profile.root, grant: 'all' });
      expect(await (await post(server, forged)).json()).toMatchObject({ ok: false, error: { code: 'INVALID_ARGUMENT' } });
      forged.scope.workspace_id = 'other-workspace';
      expect(await postStatus(server, forged)).toBe(403);
      const fallback = await head(server, '/synthetic-missing-page');
      expect(fallback.status).toBe(200);
      expect(fallback.contentType).toMatch(/^text\/html\b/);
      const sqlite = join(profile.root, 'workspace.sqlite3');
      expect((await stat(sqlite)).isFile()).toBe(true);
      await assertPrivateResource(server, rawFileRoute(sqlite));
      for (const suffix of ['', '-wal', '-shm', '-journal']) {
        await assertPrivateResource(server, '/workspace.sqlite3' + suffix);
      }
      const marker = join(profile.root, 'synthetic fixture.txt');
      await writeFile(marker, 'Synthetic file in the bound profile.', { flag: 'wx', mode: 0o600 });
      await assertPrivateResource(server, rawFileRoute(marker));

      await writerPid(server, 'suspend', writer);
      const abort = new AbortController();
      const cancelled = post(server, setTimeframe(0, '5m'), {}, abort.signal).then(responseStatus).catch(error => error);
      try {
        await expect.poll(() => postStatus(server, admissionProbe)).toBe(429);
        abort.abort();
        expect((await cancelled).name).toBe('AbortError');
        // The browser stopped waiting. The admitted line still owns the producer slot.
        expect(await postStatus(server, admissionProbe)).toBe(429);
      } finally {
        abort.abort();
        if (alive(writer)) await writerPid(server, 'resume', writer);
      }
      await expect.poll(async () => {
        const reply = await post(server, snapshot);
        if (reply.ok) return state(await reply.json())?.revision;
        await responseStatus(reply);
        return undefined;
      }).toBe(1);
      expect(await writerPid(server)).toBe(writer);

      await writerPid(server, 'suspend', writer);
      const inFlight = post(server, setTimeframe(1, '1d')).then(async reply => ({ status: reply.status, body: await reply.json() }));
      const [reply, interruption] = await Promise.allSettled([inFlight, (async () => {
        try {
          await expect.poll(() => postStatus(server, admissionProbe)).toBe(429);
        } finally {
          if (alive(writer)) await writerPid(server, 'kill', writer);
        }
      })()]);
      if (interruption.status === 'rejected') throw interruption.reason;
      if (reply.status === 'rejected') throw reply.reason;
      const disconnected = reply.value;
      expect(disconnected.status).toBe(503);
      expect(disconnected.body).toMatchObject({ ok: false, error: { code: 'WORKER_EXITED' } });
      expect(await postStatus(server, snapshot)).toBe(503);
    });
    await inService(port, profile, 'ubuntu-workspace', async server => {
      const restored = state(await (await post(server, snapshot)).json());
      expect(restored).toMatchObject({ revision: 1, timeframe: '5m' });
    });
  } finally {
    await profile.close();
  }
});

test('the private launcher handoff is required and a previous launch cannot authorize a request', async ({ browser }) => {
  const profile = await makeProfile();
  const port = await freePort();
  let previous;
  let handoff;
  try {
    await inService(port, profile, 'ubuntu-workspace', async server => {
      previous = server.capability;
      handoff = server.handoff;
      await assertPrivateHandoff(handoff);
      if (process.platform === 'win32') await assertUnsuitableWindowsHandoffsRejected(profile.root);

      for (const request of [snapshot, setTimeframe(0, '5m')]) {
        const denied = await post({ url: server.url }, request);
        expect(denied.status).toBe(403);
        expect(await denied.json()).toEqual({ ok: false, error: { code: 'PERMISSION_DENIED', retryable: false } });
      }
      await assertPrivateResource(server, '/workspace-access-' + port + '.html');
      await assertPrivateResource(server, rawFileRoute(handoff));
      await assertPrivateResource(server, '/.workspace-access-' + port + '.html-' + randomUUID());
      const allowed = await post(server, snapshot);
      const payload = await allowed.text();
      expect(!payload.includes(previous)).toBe(true);
      expect(state(JSON.parse(payload))).toEqual({ revision: 0, timeframe: '1m', undo: null });
    });
    await expect(stat(handoff)).rejects.toMatchObject({ code: 'ENOENT' });

    await inService(port, profile, 'ubuntu-workspace', async server => {
      expect(server.capability !== previous).toBe(true);
      expect(await postStatus(server, snapshot, { Authorization: 'Bearer ' + previous })).toBe(403);

      const anonymous = await browser.newContext();
      try {
        const page = await anonymous.newPage();
        const requests = observeOperations(page);
        await page.goto(server.url);
        await expect(control(page, 'Open offline sample')).toBeVisible();
        await expect(control(page, 'Undo timeframe change')).toHaveCount(0);
        await expect(control(page, 'Decision notes')).toHaveCount(0);
        await control(page, '5m').click();
        expect(requests).toEqual([]);
      } finally { await anonymous.close(); }

      await inFreshBrowser(browser, server, async (page, initial) => {
        expect(state(initial)).toEqual({ revision: 0, timeframe: '1m', undo: null });
        await expect(control(page, '1m')).toHaveAttribute('aria-pressed', 'true');
      });
    });

    if (process.getuid) {
      // This profile contains only test state. Reject it before issuing a
      // credential when the host supplies it without owner-only permissions.
      await chmod(profile.root, 0o755);
      await inService(port, profile, 'ubuntu-workspace', async server => {
        await expect.poll(server.handoffRejected).toBe(true);
        await expect(stat(server.handoff)).rejects.toMatchObject({ code: 'ENOENT' });
        expect(await postStatus(server, snapshot)).toBe(403);
      }, { expectHandoff: false });
    }
  } finally {
    await profile.close();
  }
});

test('losing parent IPC releases the listener and writer with a history response held', async ({}, testInfo) => {
  const profile = await makeProfile();
  const port = await freePort();
  const history = new AbortController();
  const observation = { port };
  const observe = async () => ({
    vite: alive(observation.vite),
    writer: alive(observation.writer),
    listening: !(await portAvailable(port)),
  });
  try {
    await inService(port, profile, 'ubuntu-workspace', async server => {
      observation.vite = server.pid;
      observation.writer = await writerPid(server);
      expect(state(await (await post(server, snapshot)).json())).toEqual({ revision: 0, timeframe: '1m', undo: null });
      const signal = AbortSignal.any([history.signal, AbortSignal.timeout(15_000)]);
      const response = await fetch(server.url + operationPath, {
        method: 'POST',
        headers: {
          Origin: server.url, 'Content-Type': 'application/json', [traceHeader]: randomUUID(),
          'x-scopehub-test-interrupt': 'during-body-200',
        },
        body: JSON.stringify(sampleHistoryCall()),
        signal,
      });
      expect(response.status).toBe(200);
      const reader = response.body.getReader();
      expect(await reader.read()).toEqual({ value: new TextEncoder().encode('{'), done: false });
      const responseReleased = reader.read().then(({ done }) => done, () => !signal.aborted);
      observation.beforeDisconnect = await observe();
      expect(observation.beforeDisconnect).toEqual({ vite: true, writer: true, listening: true });
      const exited = server.disconnectOwner(observation.writer);
      await expect.poll(async () => {
        observation.afterDisconnect = await observe();
        return observation.afterDisconnect;
      }, { timeout: 4_000 }).toEqual({ vite: false, writer: false, listening: false });
      expect(await exited).toEqual([0, null]);
      expect(await responseReleased).toBe(true);
      await expect(stat(server.handoff)).rejects.toMatchObject({ code: 'ENOENT' });
    }, { historyTrace: true, historyFault: true });
  } finally {
    history.abort();
    if (observation.writer) observation.afterCleanup = await observe();
    await profile.close();
    await testInfo.attach('workspace-parent-disconnect', { body: JSON.stringify(observation), contentType: 'application/json' });
  }
});

test('workspace readiness rejects the previous listener response and probes the replacement', async () => {
  const child = Object.assign(new EventEmitter(), { pid: process.pid, exitCode: null, signalCode: null });
  const oldProbe = Promise.withResolvers();
  const previous = createHttpServer((_request, response) => oldProbe.resolve(response));
  const replacementSockets = [];
  const replacement = createHttpServer((request, response) => {
    replacementSockets.push(request.socket.remotePort);
    response.writeHead(200, { Connection: 'close' }).end('Current listener');
  });
  const port = await freePort();
  let previousClosed;
  try {
    await once(previous.listen(port, '127.0.0.1'), 'listening');
    const startup = observeServiceStartup(child, { startedAt: performance.now(), mode: 'ubuntu-workspace', port });
    const readiness = startup.wait(`http://127.0.0.1:${port}`);
    const oldResponse = await oldProbe.promise;
    const previousSocket = oldResponse.socket.remotePort;
    previousClosed = once(previous.close(), 'close');
    await once(replacement.listen(port, '127.0.0.1'), 'listening');
    child.emit('message', { type: startupMessage, phase: 'listening' });
    oldResponse.writeHead(200, { Connection: 'keep-alive' }).end('Previous listener');

    expect(await readiness).toBeUndefined();
    expect(replacementSockets).toHaveLength(1);
    expect(replacementSockets[0]).not.toBe(previousSocket);
  } finally {
    if (previous.listening) previousClosed = once(previous.close(), 'close');
    previous.closeAllConnections();
    await previousClosed;
    if (replacement.listening) await once(replacement.close(), 'close');
  }
});

test('workspace readiness omits capability fragments at the drained output boundary', () => {
  const child = new EventEmitter();
  const startup = observeServiceStartup(child, { startedAt: performance.now(), mode: 'ubuntu-workspace', port: 1455 });
  const suffix = '4e2c5a7b8d0f19';
  const chunks = ['#workspace=' + 'a'.repeat(50), suffix, 'x'.repeat(6000 - suffix.length)];
  const tail = chunks.reduce((recent, chunk) => (recent + chunk).slice(-6000), '');
  expect(tail.startsWith(suffix)).toBe(true);
  const failure = startup.failure({ reason: 'readiness-timeout' }, {}, tail);
  expect(failure.message.includes(suffix)).toBe(false);
  expect(JSON.stringify(failure.startup).includes(suffix)).toBe(false);
});

for (const [title, chunks, expected] of [
  ['ignores a stderr notice followed by unrelated stdout', [
    ['stderr', '  ➜  Local:   http://127.0.0.1:1455/\n'], ['stdout', 'Unrelated startup output\n'],
  ], []],
  ['accepts split stdout with an intervening stderr warning', [
    ['stdout', '  ➜  \x1b[1mLocal\x1b[22m:   http://127.0.'],
    ['stderr', 'Unrelated warning\n'], ['stdout', '0.1:1455/\n'],
  ], ['listening']],
]) {
  test(`workspace CLI readiness ${title}`, () => {
    const child = Object.assign(new EventEmitter(), { stdout: new PassThrough(), stderr: new PassThrough() });
    const startup = observeServiceStartup(child, { startedAt: performance.now(), mode: 'ubuntu-workspace', port: 1455 });
    const output = captureServiceOutput(child, startup.observeCliStdout);
    for (const [stream, chunk] of chunks) child[stream].write(chunk);
    child.stdout.end();
    child.stderr.end();
    const { phases, output: notices } = startup.failure({ reason: 'test-observation' }, {}, output.recent()).startup;
    expect(phases.map(({ phase }) => phase)).toEqual(expected);
    expect(notices).toBe('');
  });
}

for (const occupied of [true, false]) {
  const cause = occupied ? 'a claimed port' : 'a live child held before listening';
  test(`workspace service readiness distinguishes ${cause}`, async ({}, testInfo) => {
    const profile = await makeProfile();
    const port = await freePort();
    // Keep the deliberately held fixture short and leave the ordinary deadline in place.
    const holdTimeoutMs = occupied ? undefined : 500;
    const blocker = occupied ? createHttpServer((_request, response) => {
      response.writeHead(200, { Connection: 'close' }).end('Another loopback server');
    }) : undefined;
    const entry = occupied ? undefined : 'data:text/javascript,' + encodeURIComponent(`
      import { createServer as configure } from ${JSON.stringify(vite)};
      import { setTimeout as delay } from 'node:timers/promises';
      export async function createServer(options) {
        await delay(700);
        const server = await configure(options);
        server.listen = async () => {
          await new Promise(resolve => {
            process.on('message', message => { if (message === 'close') resolve(); });
            process.once('disconnect', resolve);
            process.send({ type: ${JSON.stringify(startupMessage)}, phase: 'held-before-listen' });
          });
          console.error('x'.repeat(6200));
          console.error('Fixture released its unbound listener during cleanup.');
          console.error('#workspace=' + 'a'.repeat(64));
          return server;
        };
        return server;
      }
    `);
    try {
      if (blocker) await once(blocker.listen(port, '127.0.0.1'), 'listening');
      const failure = await inService(port, profile, 'ubuntu-workspace', () => {
        throw new Error('The test service must not report readiness');
      }, { entry, holdTimeoutMs }).catch(error => error);
      const startup = failure?.startup;
      await testInfo.attach('workspace-service-startup', {
        body: JSON.stringify(startup ?? { message: failure?.message }), contentType: 'application/json',
      });
      expect(startup).toMatchObject({
        requested_port: port, pid: expect.any(Number),
        reason: occupied ? 'child-exited' : 'readiness-timeout',
        alive_at_failure: !occupied,
        close: { code: occupied ? 1 : 0, signal: null, elapsed_ms: expect.any(Number) },
        forced_kill_requested_ms: null,
      });
      expect(startup.phases.map(({ phase }) => phase)).not.toContain('listening');
      expect(startup.cleanup_started_ms).toBeGreaterThanOrEqual(startup.failure_ms);
      expect(startup.close.elapsed_ms).toBeGreaterThanOrEqual(startup.exit.elapsed_ms);
      if (occupied) {
        expect(startup.exit.elapsed_ms).toBeLessThanOrEqual(startup.failure_ms);
        expect(startup.phases).toContainEqual(expect.objectContaining({ phase: 'bind-error', code: 'EADDRINUSE' }));
        expect(startup.output).toContain(`Port ${port} is already in use`);
      } else {
        const hold = startup.phases.find(({ phase }) => phase === 'held-before-listen');
        expect(hold).toBeDefined();
        expect(hold.elapsed_ms).toBeGreaterThan(holdTimeoutMs);
        expect(startup.failure_ms - hold.elapsed_ms).toBeGreaterThanOrEqual(holdTimeoutMs);
        expect(startup.exit.elapsed_ms).toBeGreaterThanOrEqual(startup.cleanup_started_ms);
        expect(startup.phases).toContainEqual(expect.objectContaining({ phase: 'binding' }));
        expect(startup.probe.outcomes.ECONNREFUSED).toBeGreaterThan(0);
        expect(startup.output).toContain('Fixture released its unbound listener during cleanup.');
        expect(startup.output.includes('#workspace=')).toBe(false);
        expect(failure.message).not.toContain('a'.repeat(64));
        expect(startup.output.length).toBeLessThanOrEqual(6000);
      }
      expect(alive(startup.pid)).toBe(false);
      await expect(stat(join(profile.root, 'workspace.sqlite3'))).rejects.toMatchObject({ code: 'ENOENT' });
      await expect(stat(join(profile.root, `workspace-access-${port}.html`))).rejects.toMatchObject({ code: 'ENOENT' });
    } finally {
      if (blocker?.listening) await once(blocker.close(), 'close');
      await profile.close();
    }
    expect(await portAvailable(port)).toBe(true);
  });
}

for (const phase of ['before configuration', 'during configuration', 'after duplicate close messages']) {
  test(`losing parent IPC ${phase} exits without starting the workspace writer`, async () => {
    const profile = await makeProfile();
    const port = await freePort();
    // Hold the real configured Vite server before it can start listening.
    const entry = phase === 'before configuration' ? vite : 'data:text/javascript,' + encodeURIComponent(`
      import { createServer as configure } from ${JSON.stringify(vite)};
      import { once } from 'node:events';
      export async function createServer(options) {
        const server = await configure(options);
        const disconnected = once(process, 'disconnect');
        process.send('configured');
        await disconnected;
        return server;
      }
    `);
    const child = spawnService(port, profile, 'ubuntu-workspace', { entry });
    const exited = once(child, 'exit');
    child.stdout.resume();
    child.stderr.resume();
    try {
      if (entry !== vite) {
        expect(await once(child, 'message', { signal: AbortSignal.timeout(10_000) })).toEqual(['configured']);
      }
      if (phase === 'after duplicate close messages') {
        child.send('close');
        child.send('close');
      }
      expect(child.connected).toBe(true);
      if (child.connected) child.disconnect();
      await expect.poll(() => child.exitCode, { timeout: 4_000 }).toBe(0);
      expect(await portAvailable(port)).toBe(true);
      await expect(stat(join(profile.root, 'workspace.sqlite3'))).rejects.toMatchObject({ code: 'ENOENT' });
    } finally {
      if (child.exitCode === null && child.signalCode === null) child.kill('SIGTERM');
      try { await reapService(child, exited); }
      finally { await profile.close(); }
    }
  });
}

test('terminal SIGINT and SIGTERM remove the handoff and reap the writer before relaunch', async () => {
  test.skip(process.platform === 'win32', 'The owned CLI uses POSIX signal delivery.');
  const profile = await makeProfile();
  const port = await freePort();
  let handoff;
  let previousWriter;
  let previousCapability;
  try {
    await inService(port, profile, 'ubuntu-workspace', async server => {
      handoff = server.handoff;
      previousWriter = await writerPid(server);
      previousCapability = server.capability;
      expect(state(await (await post(server, snapshot)).json())).toEqual({ revision: 0, timeframe: '1m', undo: null });
    }, { terminalSignal: 'SIGINT' });
    await expect(stat(handoff)).rejects.toMatchObject({ code: 'ENOENT' });
    await expect.poll(() => alive(previousWriter)).toBe(false);

    let nextWriter;
    await inService(port, profile, 'ubuntu-workspace', async server => {
      nextWriter = await writerPid(server);
      expect(nextWriter).not.toBe(previousWriter);
      expect(server.capability !== previousCapability).toBe(true);
      expect(await postStatus(server, snapshot, { Authorization: 'Bearer ' + previousCapability })).toBe(403);
      expect(state(await (await post(server, snapshot)).json())).toEqual({ revision: 0, timeframe: '1m', undo: null });
    }, { terminalSignal: 'SIGTERM' });
    await expect(stat(handoff)).rejects.toMatchObject({ code: 'ENOENT' });
    await expect.poll(() => alive(nextWriter)).toBe(false);
  } finally {
    await profile.close();
  }
});

test('two live writers read current undo and a stale browser reload recovers from Rust', async ({ browser }) => {
  const profile = await makeProfile();
  try {
    await inService(await freePort(), profile, 'ubuntu-workspace', async first => {
      await inService(await freePort(), profile, 'ubuntu-workspace', async second => {
        expect(await writerPid(first)).not.toBe(await writerPid(second));
        expect(await postStatus(second, snapshot, { Authorization: 'Bearer ' + first.capability })).toBe(403);
        await inFreshBrowser(browser, first, async (page, initial) => {
          expect(state(initial)).toEqual({ revision: 0, timeframe: '1m', undo: null });
          const changed = (await (await post(second, setTimeframe(0, '5m'))).json()).result.outcome;
          expect(changed).toMatchObject({ state: 'committed', result: { revision: 1, timeframe: '5m' } });
          const target = { operation_id: changed.receipt.operation_id, committed_workspace_revision: 1 };
          expect(state(await (await post(first, snapshot)).json())).toEqual({ revision: 1, timeframe: '5m', undo: target });

          const rejected = await act(page, '1d', 'chart.setTimeframe');
          expect(rejected.result.outcome).toMatchObject({ state: 'failed', error: { code: 'STALE_CONTEXT' } });
          await expect(page.getByRole('alert')).toHaveText('The workspace changed. Reload its current state.');
          await expect(control(page, '1m')).toHaveAttribute('aria-pressed', 'true');
          await control(page, 'Reload workspace state').click();
          await expect(control(page, '5m')).toHaveAttribute('aria-pressed', 'true');
          const accepted = await duringResponse(page, 'workspace.undo', () => control(page, 'Undo timeframe change').click());
          expect(accepted.request().postDataJSON().intent.arguments).toEqual({ operation_id: target.operation_id });
          expect((await accepted.json()).result.outcome).toMatchObject({
            state: 'committed', result: { revision: 2, timeframe: '1m' },
          });
          expect(state(await (await post(second, snapshot)).json())).toEqual({ revision: 2, timeframe: '1m', undo: null });
        });
      });
    });
  } finally {
    await profile.close();
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
        expect(await postStatus(server, setTimeframe(0, '5m'))).toBe(404);
        await expect(stat(join(profile.root, 'workspace.sqlite3'))).rejects.toMatchObject({ code: 'ENOENT' });
      });
    }
  } finally {
    await profile.close();
  }
});

async function makeProfile(id = 'synthetic-' + randomUUID()) {
  const directory = await mkdtemp(join(tmpdir(), 'scopehub-ubuntu-test-'));
  const profile = join(directory, 'profile');
  const close = () => rm(directory, { recursive: true, force: true });
  try {
    await exec(workspaceBridge, ['--prepare-profile-root', profile], { timeout: 12_000 });
    return { root: profile, id, close };
  } catch (error) {
    await close();
    throw error;
  }
}

async function assertPrivateHandoff(handoff) {
  if (process.platform === 'win32') {
    await assertWindowsPrivateHandoff(handoff);
    return;
  }
  const metadata = await stat(handoff);
  expect(metadata.mode & 0o777).toBe(0o600);
  expect(metadata.uid).toBe(process.getuid());
}

test('a failed initial document releases its workspace snapshot observer before closing the context', async ({ browser }) => {
  const context = await browser.newContext();
  try {
    const page = await context.newPage();
    const url = `http://127.0.0.1:${await freePort()}/`;
    let navigations = 0;
    await page.route(url, route => {
      navigations += 1;
      return route.abort('failed');
    });
    const events = ['request', 'response', 'requestfinished', 'requestfailed', 'pageerror', 'close', 'crash'];
    const before = events.map(event => page.listenerCount(event));
    await expect(openWorkspaceSnapshot(page, url)).rejects.toThrow(
      /document navigation\. document failed \(net::ERR_FAILED\)\. Document net::ERR_FAILED, entry not requested/,
    );
    expect(events.map(event => page.listenerCount(event))).toEqual(before);
    expect(navigations).toBe(1);
  } finally {
    await context.close();
  }
});

test('workspace startup entry can finish after the default control assertion window', async ({ browser }, testInfo) => {
  const profile = await makeProfile();
  try {
    await inService(await freePort(), profile, 'ubuntu-workspace', async server => {
      const context = await newWorkspaceContext(browser, server);
      try {
        const page = await context.newPage();
        const requests = observeOperations(page);
        const entry = server.url + '/src/main.tsx';
        let entryRequests = 0;
        let navigations = 0;
        page.on('request', request => { if (request.isNavigationRequest()) navigations += 1; });
        await page.route(entry, async route => {
          entryRequests += 1;
          const transformed = await route.fetch();
          expect(transformed.status()).toBe(200);
          // Chromium can finish the document before an asynchronous module evaluates.
          // Hold execution across the old five-second assertion, then run Vite's real entry.
          const gate = `await new Promise(resolve => {
            const release = () => window.setTimeout(resolve, 5500);
            if (document.readyState === 'complete') release();
            else window.addEventListener('load', release, { once: true });
          });\n`;
          await route.fulfill({ contentType: 'text/javascript', body: gate + await transformed.text() });
        });
        const observeStaticStartup = async () => {
          await page.waitForEvent('load');
          const startup = page.getByRole('main', { name: 'Workspace startup' });
          await expect(startup).toBeVisible();
          await expect(control(page, 'Open offline sample')).toHaveCount(0);
          expect(requests).toEqual([]);
          await testInfo.attach('static-startup-before-entry', { body: await page.screenshot(), contentType: 'image/png' });
        };
        const [, initial] = await Promise.all([observeStaticStartup(), openWorkspaceSnapshot(page, server.url)]);
        expect(state(initial)).toEqual({ revision: 0, timeframe: '1m', undo: null });
        expect(requests).toEqual([endpoint]);
        expect(entryRequests).toBe(1);
        expect(navigations).toBe(1);
        await expect(page.getByRole('main', { name: 'Workspace startup' })).toHaveCount(0);
      } finally { await context.close(); }
    });
  } finally { await profile.close(); }
});

test('workspace startup entry HTTP failure identifies the resource without retrying', async ({ browser }) => {
  const profile = await makeProfile();
  try {
    await inService(await freePort(), profile, 'ubuntu-workspace', async server => {
      const context = await newWorkspaceContext(browser, server);
      try {
        const page = await context.newPage();
        const requests = observeOperations(page);
        let entryRequests = 0;
        let navigations = 0;
        let capabilityInRequestUrl = false;
        page.on('request', request => {
          if (request.isNavigationRequest()) navigations += 1;
          capabilityInRequestUrl ||= request.url().includes(server.capability);
        });
        await page.route(server.url + '/src/main.tsx', route => {
          entryRequests += 1;
          return route.fulfill({ status: 503, contentType: 'text/javascript', body: '' });
        });
        await expect(openWorkspaceSnapshot(page, server.url)).rejects.toThrow(
          /application mount\. \/src\/main\.tsx returned HTTP 503/,
        );
        const startup = page.getByRole('main', { name: 'Workspace startup' });
        await expect(startup.getByRole('button', { name: 'Reload window' })).toBeEnabled();
        await expect(control(page, 'Open offline sample')).toHaveCount(0);
        expect(requests).toEqual([]);
        expect(entryRequests).toBe(1);
        expect(navigations).toBe(1);
        expect(capabilityInRequestUrl).toBe(false);
        expect(state(await (await post(server, snapshot)).json())).toEqual({ revision: 0, timeframe: '1m', undo: null });
      } finally { await context.close(); }
    });
  } finally { await profile.close(); }
});

for (const [name, isPrivate, consumed] of [
  ['public window', false, false],
  ['private launcher before capability consumption', true, false],
  ['private launcher after capability consumption', true, true],
]) {
  test(`workspace startup recovery for a ${name}`, async ({ browser }) => {
    const profile = await makeProfile();
    try {
      await inService(await freePort(), profile, 'ubuntu-workspace', async server => {
        const context = await browser.newContext();
        try {
          const page = await context.newPage();
          const requests = observeOperations(page);
          const entry = server.url + '/src/main.tsx';
          const message = 'Synthetic exception after the entry imports';
          let navigations = 0;
          let exposed = false;
          page.on('request', request => {
            const url = request.url();
            if (request.isNavigationRequest() && new URL(url).origin === server.url) navigations += 1;
            exposed ||= url.includes(server.capability);
          });
          const breakEntry = async route => {
            if (!consumed) return route.fulfill({ status: 503, contentType: 'text/javascript', body: '' });
            // Static imports still evaluate before this exception, consuming the true launch fragment.
            const transformed = await route.fetch();
            expect(transformed.status()).toBe(200);
            return route.fulfill({ contentType: 'text/javascript', body: `throw new Error(${JSON.stringify(message)});\n` + await transformed.text() });
          };
          const launch = async () => {
            await page.goto(pathToFileURL(server.handoff).href);
            // Keep the fragment out of Playwright navigation arguments and assertion diffs.
            await page.getByRole('link', { name: 'Open ScopeHub workspace' }).evaluate(link => link.click());
          };
          await page.route(entry, breakEntry);
          const failed = consumed
            ? page.waitForEvent('pageerror', error => error.message === message)
            : page.waitForResponse(response => response.url() === entry && response.status() === 503);
          await Promise.all([failed, isPrivate ? launch() : page.goto(server.url)]);
          const startup = page.getByRole('main', { name: 'Workspace startup' });
          await expect(startup).toBeVisible();
          expect(await page.evaluate(() => Boolean(window.location.hash))).toBe(isPrivate && !consumed);
          expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
          expect(requests).toEqual([]);
          expect(navigations).toBe(1);

          const reload = startup.locator('#startup-reload');
          if (consumed) {
            await expect(reload).toBeHidden();
            await expect(reload).toBeDisabled();
            await expect(startup).toContainText('Reopen the private workspace access document to try again.');
          } else {
            await expect(reload).toBeVisible();
            await expect(reload).toBeEnabled();
          }
          await page.unroute(entry, breakEntry);
          const recover = consumed ? launch : () => reload.evaluate(button => button.click());
          if (isPrivate) {
            const [first] = await Promise.all([page.waitForResponse(response =>
              new URL(response.url()).pathname === endpoint && response.request().postDataJSON()?.type === 'snapshot'), recover()]);
            expect(state(await first.json())).toEqual({ revision: 0, timeframe: '1m', undo: null });
            await expect(control(page, 'Private records')).toBeEnabled();
          } else {
            await recover();
            await expect(control(page, 'Open offline sample')).toBeVisible({ timeout: 10_000 });
            await expect(control(page, 'Private records')).toHaveCount(0);
          }
          expect(await page.evaluate(() => window.location.hash === '')).toBe(true);
          expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
          expect(requests).toEqual(isPrivate ? [endpoint] : []);
          expect(navigations).toBe(2);
          expect(exposed).toBe(false);
          await expect(startup).toHaveCount(0);
          if (consumed) {
            // A normally mounted workspace keeps its grant only for this page.
            await page.reload();
            await expect(control(page, 'Open offline sample')).toBeVisible({ timeout: 10_000 });
            await expect(control(page, 'Private records')).toHaveCount(0);
            expect(requests).toEqual([endpoint]);
            expect(navigations).toBe(3);
            expect(exposed).toBe(false);
            expect(await page.evaluate(() => window.location.hash === '' && localStorage.length === 0
              && sessionStorage.length === 0)).toBe(true);
          }
        } finally { await context.close(); }
      });
    } finally { await profile.close(); }
  });
}

test('a status-only probe cancels its unread response body', async () => {
  let cancelled = false;
  const body = new ReadableStream({ cancel() { cancelled = true; } });
  expect(await responseStatus(new Response(body, { status: 429 }))).toBe(429);
  expect(cancelled).toBe(true);
});

for (const [name, reason] of [
  ['a rejected reply', /workspace snapshot\. Snapshot returned HTTP 503\. Snapshot HTTP 503/],
  ['an invalid reply', /workspace snapshot\. Application rejected the initial snapshot\. Snapshot HTTP 200/],
  ['a stalled reply', /workspace snapshot\. Snapshot exceeded 1500 ms\. Snapshot no response/],
  ['a browser exception', /workspace snapshot\. Uncaught browser TypeError before the workspace was ready\. Snapshot no response\.$/],
]) {
  test(`workspace startup reports ${name} during the first snapshot after application mount`, async ({ browser }) => {
    const profile = await makeProfile();
    try {
      await inService(await freePort(), profile, 'ubuntu-workspace', async server => {
        const context = await newWorkspaceContext(browser, server);
        const release = Promise.withResolvers();
        try {
          const page = await context.newPage();
          const requests = observeOperations(page);
          await page.route(server.url + endpoint, async route => {
            if (name === 'a rejected reply') {
              await route.fulfill({ status: 503, json: { ok: false, error: { code: 'WORKER_EXITED' } } });
            } else if (name === 'an invalid reply') {
              await route.fulfill({ json: {
                ok: true, result: { workspace: { id: workspaceId, revision: -1, timeframe: '1m' }, selection_revision: 0, undo: null },
              } });
            } else {
              await release.promise;
              await route.abort();
            }
          });
          const events = ['request', 'response', 'requestfailed', 'pageerror', 'close', 'crash'];
          const before = events.map(event => page.listenerCount(event));
          try {
            const injectException = async () => {
              await page.waitForRequest(server.url + endpoint);
              await control(page, 'Data sources').evaluate(() => {
                setTimeout(() => { throw new TypeError('Synthetic startup-only detail'); }, 0);
              });
            };
            const injection = name === 'a browser exception' ? injectException() : undefined;
            const failed = expect(openWorkspacePreview(page, server.url, endpoint, { snapshotTimeout: 1500 })).rejects.toThrow(reason);
            await Promise.all([failed, injection]);
            expect(events.map(event => page.listenerCount(event))).toEqual(before);
            await expect(control(page, 'Data sources')).toBeVisible();
            await expect(control(page, 'Private records')).toBeDisabled();
            expect(requests).toEqual([endpoint]);
            expect(await page.evaluate(() => window.location.hash === '')).toBe(true);
          } finally {
            release.resolve();
            await page.unrouteAll({ behavior: 'wait' });
          }
        } finally {
          await context.close();
        }
      });
    } finally {
      await profile.close();
    }
  });
}

async function freePort(requestedPort = 0) {
  for (;;) {
    const server = createServer();
    server.listen(requestedPort, '127.0.0.1');
    await once(server, 'listening');
    try {
      const port = server.address().port;
      // Retain first allocations for the run so another case cannot claim a
      // port between a probe and Vite's bind, or between service restarts.
      if (!requestedPort) await mkdir(join(process.env.SCOPEHUB_WORKSPACE_TEST_RUN, 'ports', String(port)));
      return port;
    } catch (error) {
      if (error.code !== 'EEXIST') throw error;
    } finally {
      await new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
    }
  }
}

async function portAvailable(port) {
  try {
    await freePort(port);
    return true;
  } catch (error) {
    if (error.code === 'EADDRINUSE') return false;
    throw error;
  }
}

function spawnService(port, profile, mode, { terminalSignal, historyTrace, historyFault, snapshotGate, entry = vite, traceStartup }) {
  // Vite publishes its dependency cache by renaming directories. Reuse warm
  // caches across launches, but give every live server its own write path.
  let slot = 0;
  while (activeCacheSlots.has(slot)) slot += 1;
  const cacheDir = join(process.env.SCOPEHUB_WORKSPACE_TEST_RUN, process.env.TEST_WORKER_INDEX, mode, String(slot));
  const args = terminalSignal
    ? [viteCli, '--mode', mode, '--host', '127.0.0.1', '--port', String(port), '--strictPort']
    : ['--input-type=module', '--eval', service, entry, mode, String(port), cacheDir,
      historyFault ? '1' : '', snapshotGate ? workspaceBridge : '', traceStartup ? '1' : ''];
  const child = spawn(process.execPath, args, {
    cwd: ui,
    env: {
      ...process.env,
      SCOPEHUB_WORKSPACE_PROFILE_ROOT: profile.root,
      SCOPEHUB_WORKSPACE_PROFILE_ID: profile.id,
      SCOPEHUB_WORKSPACE_ID: profile.workspaceId ?? workspaceId,
      ...(historyTrace && { SCOPEHUB_TEST_PREVIEW: '1' }),
    },
    stdio: terminalSignal ? ['pipe', 'pipe', 'pipe'] : ['ignore', 'pipe', 'pipe', 'ipc'],
  });
  if (!terminalSignal) {
    activeCacheSlots.add(slot);
    child.once('exit', () => activeCacheSlots.delete(slot));
  }
  return child;
}

async function reapService(child, done, onForcedKill) {
  const grace = setTimeout(() => {
    onForcedKill?.();
    child.kill('SIGKILL');
  }, 7_000);
  try { return await done; }
  finally { clearTimeout(grace); }
}

async function inService(port, profile, mode, use, {
  expectHandoff = true, terminalSignal, historyTrace = false, historyFault = false, snapshotGate = false, entry, holdTimeoutMs,
} = {}) {
  const url = 'http://127.0.0.1:' + port;
  const startedAt = performance.now();
  const child = spawnService(port, profile, mode, { terminalSignal, historyTrace, historyFault, snapshotGate, entry, traceStartup: true });
  const startup = observeServiceStartup(child, { startedAt, mode, port });
  const output = captureServiceOutput(child, terminalSignal ? startup.observeCliStdout : undefined);
  let startupFailure;
  let disconnectedExit;
  let disconnectedWriter;
  const historyStages = new Map();
  const historyBarriers = new EventEmitter();
  if (historyTrace) {
    let pending = '';
    child.stdout.on('data', chunk => {
      const lines = (pending + chunk).split('\n');
      pending = lines.pop();
      for (const line of lines) {
        if (line.startsWith('Preview history barrier ')) {
          historyBarriers.emit(line.slice('Preview history barrier '.length));
          continue;
        }
        if (!line.startsWith('Preview history ')) continue;
        const { id, ...stage } = JSON.parse(line.slice('Preview history '.length));
        if (!historyStages.has(id)) historyStages.set(id, []);
        historyStages.get(id).push(stage);
      }
    });
  }
  const send = message => new Promise((resolve, reject) => {
    child.send(message, error => error ? reject(error) : resolve());
  });
  const server = {
    url, pid: child.pid,
    handoff: join(profile.root, 'workspace-access-' + port + '.html'),
    handoffRejected: output.handoffRejected,
    historyStages,
    flushHistoryTrace: async () => {
      const id = randomUUID();
      await Promise.all([
        once(historyBarriers, id, { signal: AbortSignal.timeout(5_000) }),
        send({ type: 'history-barrier', id }),
      ]);
    },
    ...(snapshotGate && { tagSnapshot: observeSnapshotGate(child) }),
    cutHistoryResponse: () => send('cut-history'),
    disconnectOwner: writer => {
      expect(child.connected).toBe(true);
      disconnectedWriter = writer;
      // Node does not reliably emit ChildProcess close after a local IPC disconnect.
      disconnectedExit = once(child, 'exit');
      if (child.connected) child.disconnect();
      return disconnectedExit;
    },
  };
  try {
    startupFailure = await startup.wait(url, holdTimeoutMs);
    if (!startupFailure && mode === 'ubuntu-workspace' && expectHandoff) {
      await expect.poll(async () => {
        const content = await readFile(server.handoff, 'utf8').catch(error => {
          if (error.code === 'ENOENT') return '';
          throw error;
        });
        const link = /href="(http:\/\/127\.0\.0\.1:\d+\/#workspace=([a-f0-9]{64}))"/.exec(content);
        if (!link || new URL(link[1]).origin !== url) return false;
        const capability = link[2];
        server.capability = capability;
        return true;
      }, { message: 'The launcher issued a private access document', timeout: 12_000 }).toBe(true);
    }
    if (!startupFailure) await use(server);
  } finally {
    const cleanupStarted = startup.elapsed();
    try {
      if (terminalSignal) child.kill(terminalSignal);
      else if (child.connected) child.send('close', () => {});
      else if (disconnectedWriter && child.exitCode === null && child.signalCode === null) {
        if (process.platform !== 'win32') child.kill('SIGTERM');
        else if (alive(disconnectedWriter)) {
          try { await writerPid(server, 'kill', disconnectedWriter); }
          catch (error) { if (alive(disconnectedWriter)) throw error; }
        }
      }
    } finally {
      let forcedKillRequested = null;
      const [, signal] = await reapService(child, disconnectedExit ?? startup.closed, () => {
        forcedKillRequested = startup.elapsed();
      });
      const recentOutput = output.recent();
      if (server.capability) expect(!recentOutput.includes(server.capability)).toBe(true);
      if (startupFailure) {
        throw startup.failure(startupFailure, {
          cleanup_started_ms: cleanupStarted, forced_kill_requested_ms: forcedKillRequested,
        }, recentOutput);
      }
      expect(signal).not.toBe('SIGKILL');
    }
  }
}

async function writerPid(server, action, observedPid) {
  const args = ['-I', join(root, 'tests/ubuntu-preview/workspace_test_writer.py'), String(process.pid), String(server.pid)];
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

function rawFileRoute(path) {
  return '/@fs/' + pathToFileURL(path).pathname.replace(/^\//, '');
}

async function head(server, resource) {
  const response = await fetch(server.url + resource, {
    method: 'HEAD', headers: { Accept: 'text/html' }, redirect: 'error', signal: AbortSignal.timeout(5_000),
  });
  return {
    status: response.status,
    contentType: response.headers.get('content-type'),
    contentLength: response.headers.get('content-length'),
    cacheControl: response.headers.get('cache-control'),
    etag: response.headers.get('etag'),
    lastModified: response.headers.get('last-modified'),
  };
}

async function assertPrivateResource(server, resource) {
  expect(await head(server, resource)).toEqual({
    status: 404, contentType: null, contentLength: '0', cacheControl: 'no-store', etag: null, lastModified: null,
  });
}

function post(server, body, headers = {}, signal = AbortSignal.timeout(10_000)) {
  return fetch(server.url + endpoint, {
    method: 'POST', headers: {
      Origin: server.url, 'Content-Type': 'application/json',
      ...(server.capability ? { Authorization: 'Bearer ' + server.capability } : {}), ...headers,
    },
    body: JSON.stringify(body), signal,
  });
}

async function responseStatus(response) {
  await response.body?.cancel();
  return response.status;
}

async function postStatus(server, body, headers) {
  return responseStatus(await post(server, body, headers));
}

function state(frame, expectedSelectionRevision = 0) {
  if (frame.ok !== true) return undefined;
  const { workspace, undo } = frame.result;
  expect(workspace).toMatchObject({ id: workspaceId, instrument_id: null, dataset: null, layers: [] });
  expect(frame.result.selection_revision).toBe(expectedSelectionRevision);
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

async function assertSampleStorage(page, timeframe) {
  const stored = await page.evaluate(expected => {
    let preference;
    try {
      preference = JSON.parse(localStorage.getItem('scopehub.preview.sample.v1') ?? 'null');
    } catch {
      return null;
    }
    return {
      onlySamplePreference: localStorage.length === 1 && preference !== null,
      emptySessionStorage: sessionStorage.length === 0,
      matchesSample: preference?.source === 'scopehub.synthetic' && preference.timeframe === expected.timeframe
        && preference.symbol === expected.instrument
        && preference.style === 'candlestick' && preference.cursor === 'crosshair'
        && preference.panel === 'watchlist' && preference.chartOnly === false && preference.narrow === false
        && preference.sampleRevision === 'normal' && preference.sampleDate === ''
        && Object.keys(preference).length === 10,
    };
  }, { timeframe, instrument: future });
  expect(stored).toEqual({ onlySamplePreference: true, emptySessionStorage: true, matchesSample: true });
}

async function newWorkspaceContext(browser, server) {
  const context = await browser.newContext();
  try {
    // Read the real launcher handoff but set its fragment locally before page
    // code runs. No capability is put in Playwright navigation URLs or traces.
    await context.addInitScript(capability => {
      window.history.replaceState(null, '', '#workspace=' + capability);
    }, server.capability);
    return context;
  } catch (error) {
    await context.close();
    throw error;
  }
}

async function inFreshBrowser(browser, server, use, permittedOperations = [endpoint], { restoredSample, traceSample = false } = {}) {
  const context = await newWorkspaceContext(browser, server);
  try {
    const page = await context.newPage();
    if (traceSample) await observeSelectedHistory(page, { includeCatalog: true });
    const requests = observeOperations(page);
    let capabilityInRequestUrl = false;
    page.on('request', request => { capabilityInRequestUrl ||= request.url().includes(server.capability); });
    let initial;
    const open = async () => { initial = await openWorkspaceSnapshot(page, server.url, Boolean(restoredSample)); };
    if (restoredSample) await sampleStep(page, server, restoredSample.testInfo, restoredSample.label, open);
    else await open();
    await use(page, initial);
    expect(requests.every(path => permittedOperations.includes(path))).toBe(true);
    expect(capabilityInRequestUrl).toBe(false);
  } finally {
    await context.close();
  }
}

async function openWorkspaceSnapshot(page, url, restoredSample = false) {
  const frame = await openWorkspacePreview(page, url, endpoint);
  if (restoredSample) {
    await expect(page.getByText('Offline sample', { exact: true })).toBeVisible();
  } else {
    await expect(control(page, 'Open offline sample')).toBeVisible();
    await expect(page.getByText('No data source')).toBeVisible();
    expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
  }
  expect(await page.evaluate(() => window.location.hash === '')).toBe(true);
  return frame;
}

function duringResponse(page, operation, action, timeout) {
  return duringPageEvent(page, 'response', response => new URL(response.url()).pathname === endpoint
    && response.request().postDataJSON()?.intent?.operation === operation, action, timeout);
}

async function duringPageEvent(page, event, matches, action, timeout, failedRequestMatches) {
  // A navigation or action can fail before the expected event is possible.
  // Record event errors without rejecting a detached promise, and preserve the action error.
  const { promise, resolve } = Promise.withResolvers();
  const onEvent = value => {
    try {
      if (matches(value)) resolve({ value });
    } catch (error) {
      resolve({ error });
    }
  };
  const onClose = () => resolve({ error: new Error(`Page closed before the expected ${event}.`) });
  const onCrash = () => resolve({ error: new Error(`Page crashed before the expected ${event}.`) });
  const onRequestFailed = request => {
    try {
      if (failedRequestMatches(request)) {
        const response = request.existingResponse();
        const status = response ? ` after HTTP ${response.status()}` : '';
        resolve({ error: new Error(`Workspace request failed${status}: ${request.failure()?.errorText}`) });
      }
    } catch (error) {
      resolve({ error });
    }
  };
  page.on(event, onEvent);
  page.on('close', onClose);
  page.on('crash', onCrash);
  if (failedRequestMatches) page.on('requestfailed', onRequestFailed);
  const timer = timeout && setTimeout(() => resolve({ error: new Error(`Timed out waiting for ${event}.`) }), timeout);
  try {
    await action();
    const result = await promise;
    if ('error' in result) throw result.error;
    return result.value;
  } finally {
    clearTimeout(timer);
    page.off(event, onEvent);
    page.off('close', onClose);
    page.off('crash', onCrash);
    if (failedRequestMatches) page.off('requestfailed', onRequestFailed);
  }
}

async function saveBackupTestNote(notes, fixture) {
  await notes.getByRole('button', { name: 'New decision note' }).click();
  await notes.getByLabel('Decision date').fill(fixture.date);
  await notes.getByLabel('Thesis').fill(fixture.thesis);
  await notes.getByLabel('Choice').fill(fixture.choice);
  await notes.getByRole('button', { name: 'Review decision note' }).click();
  await notes.getByRole('button', { name: 'Save decision note' }).click();
  for (const outcome of fixture.outcomes) {
    await notes.getByRole('button', { name: 'Add later outcome' }).click();
    await notes.getByLabel('Outcome date').fill(outcome.date);
    await notes.getByLabel('Outcome', { exact: true }).fill(outcome.body);
    await notes.getByRole('button', { name: 'Review later outcome' }).click();
    await notes.getByRole('button', { name: 'Save later outcome' }).click();
  }
  await notes.getByRole('button', { name: 'All decision notes' }).click();
}

async function deleteBackupTestNote(notes, date) {
  await notes.getByRole('button', { name: new RegExp(date) }).click();
  await notes.getByRole('button', { name: 'Review deletion' }).click();
  await notes.getByRole('button', { name: 'Delete decision note' }).click();
  await expect(notes.getByText('Decision note deleted.')).toBeVisible();
}

async function assertBackupTestNote(notes, fixture) {
  await notes.getByRole('button', { name: new RegExp(fixture.date) }).click();
  const original = notes.getByRole('region', { name: 'Original decision' });
  await expect(original.getByText(fixture.date, { exact: true })).toBeVisible();
  await expect(original.getByText(fixture.thesis)).toBeVisible();
  await expect(original.getByText(fixture.choice)).toBeVisible();
  const outcomes = notes.getByRole('region', { name: 'Later outcomes' });
  for (const outcome of fixture.outcomes) {
    await expect(outcomes.getByText(outcome.date, { exact: true })).toBeVisible();
    await expect(outcomes.getByText(outcome.body)).toBeVisible();
  }
}

async function downloadBackupFile(page, notes) {
  const file = await duringPageEvent(page, 'download', () => true, () =>
    notes.getByRole('button', { name: 'Download backup' }).click());
  expect(file.suggestedFilename()).toMatch(/^scopehub-decision-notes-.*\.json$/);
  return readFile(await file.path());
}

async function selectBackupFile(notes, bytes) {
  const picker = notes.getByLabel('Choose backup file');
  await expect(picker).toBeEnabled();
  await picker.setInputFiles({ name: 'backup.json', mimeType: 'application/json', buffer: bytes });
}

async function chooseBackup(notes, bytes) {
  await selectBackupFile(notes, bytes);
  const preview = notes.getByRole('region', { name: 'Restore preview' });
  await expect(preview).toBeVisible();
  return preview;
}

async function dropApprovalReply(page, server, operation, isApproval = payload => payload.confirmation?.decision === 'approve') {
  const endpointUrl = server.url + endpoint;
  let outcome;
  const handle = async route => {
    const payload = route.request().postDataJSON();
    if (payload?.intent?.operation !== operation || !isApproval(payload)) {
      await route.continue();
      return;
    }
    try {
      const response = await route.fetch();
      try {
        outcome = (await response.json()).result.outcome;
      } finally {
        await response.dispose();
      }
    } finally {
      await route.abort();
    }
  };
  await page.route(endpointUrl, handle);
  return { outcome: () => outcome, restore: () => page.unroute(endpointUrl, handle) };
}

async function act(page, name, operation) {
  const accepted = await duringResponse(page, operation, () => control(page, name).click());
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

test('private records survive restart, isolate profiles, reject stale approval and preserve deletions', async ({ browser }, testInfo) => {
  const profile = await makeProfile();
  const other = await makeProfile();
  const port = await freePort();
  const originalTitle = 'Synthetic typed exchange';
  const correctedTitle = 'Synthetic corrected exchange';
  const opening = 'Synthetic opening 5d7b40e3';
  const answer = 'Synthetic assistant answer 2d61aa75';
  const final = 'Synthetic third turn 50cdf06c';
  let backup;
  let firstWriter;
  async function openRecords(page) {
    await control(page, 'Private records').click();
    const dialog = page.getByRole('dialog', { name: 'Private records' });
    await expect(dialog.getByRole('button', { name: 'New private record' })).toBeVisible();
    return dialog;
  }
  async function approve(dialog, name = 'Save private record') {
    await dialog.getByRole('button', { name, exact: true }).click();
  }
  async function pickBackup(dialog, bytes) {
    await dialog.getByLabel('Restore local backup').setInputFiles({
      name: 'private-records.json', mimeType: 'application/json', buffer: bytes,
    });
  }
  try {
    await inService(port, profile, 'ubuntu-workspace', async server => {
      firstWriter = await writerPid(server);
      await inFreshBrowser(browser, server, async page => {
        const dialog = await openRecords(page);
        async function start(kind, title) {
          await dialog.getByRole('button', { name: 'New private record' }).click();
          await dialog.getByLabel('Record type').selectOption(kind);
          await dialog.getByLabel('Title').fill(title);
        }
        await start('conversation', originalTitle);
        await dialog.getByRole('group', { name: 'Turn 1' }).getByLabel('Text').fill(opening);
        await dialog.getByRole('group', { name: 'Turn 2' }).getByLabel('Text').fill(answer);
        await dialog.getByRole('button', { name: 'Add turn' }).click();
        await dialog.getByRole('group', { name: 'Turn 3' }).getByLabel('Text').fill(final);
        await dialog.getByRole('button', { name: 'Review save' }).click();
        await approve(dialog, 'Cancel review');
        await expect(dialog.getByText('Action cancelled.')).toBeVisible();
        const declined = new DatabaseSync(join(profile.root, 'workspace.sqlite3'), { readOnly: true });
        try { expect(declined.prepare('SELECT count(*) AS count FROM private_records').get().count).toBe(0); }
        finally { declined.close(); }

        await dialog.getByRole('button', { name: 'Review save' }).click();
        const lost = await dropApprovalReply(page, server, 'privateRecord.change', payload =>
          payload.intent.arguments.action === 'resolve' && payload.intent.arguments.decision === 'approve');
        await approve(dialog);
        await expect(dialog.getByRole('button', { name: 'Retry confirmation' })).toBeEnabled();
        expect(lost.outcome()).toMatchObject({ state: 'committed', receipt: { committed_workspace_revision: 1 } });
        await lost.restore();
        await approve(dialog, 'Retry confirmation');
        await expect(dialog.getByRole('heading', { name: originalTitle })).toBeVisible();
        await dialog.getByRole('button', { name: 'All private records' }).click();
        for (const [kind, label, title] of [
          ['memory', 'Memory', 'Synthetic long-term memory'],
          ['strategy', 'Strategy', 'Synthetic strategy'],
        ]) {
          await start(kind, title);
          await dialog.getByLabel(label, { exact: true }).fill('Synthetic ' + kind + ' body');
          await dialog.getByRole('button', { name: 'Review save' }).click();
          await approve(dialog);
          await expect(dialog.getByRole('heading', { name: title })).toBeVisible();
          await dialog.getByRole('button', { name: 'All private records' }).click();
        }
        const download = page.waitForEvent('download');
        await dialog.getByRole('button', { name: 'Download local backup' }).click();
        backup = await readFile(await (await download).path());
        expect(JSON.parse(backup).records).toHaveLength(3);
      });
    });
    await expect.poll(() => alive(firstWriter)).toBe(false);

    await inService(port, other, 'ubuntu-workspace', async server => {
      await inFreshBrowser(browser, server, async page => {
        const dialog = await openRecords(page);
        await expect(dialog.getByText('No private records saved.')).toBeVisible();
        await pickBackup(dialog, backup);
        await expect(dialog.getByRole('alert')).toContainText('unavailable in this local profile');
        await expect(dialog.getByText(originalTitle)).toHaveCount(0);
      });
    });

    await inService(port, profile, 'ubuntu-workspace', async server => {
      expect(await writerPid(server)).not.toBe(firstWriter);
      await inFreshBrowser(browser, server, async (page, initial) => {
        expect(initial.result.workspace.revision).toBe(3);
        const dialog = await openRecords(page);
        await expect(dialog.getByText('Synthetic long-term memory')).toBeVisible();
        await expect(dialog.getByText('Synthetic strategy', { exact: false })).toBeVisible();
        await dialog.getByRole('button', { name: new RegExp(originalTitle) }).click();
        await expect(dialog.locator('article li')).toHaveText(['You' + opening, 'Assistant' + answer, 'You' + final]);

        await dialog.getByRole('button', { name: 'Correct record' }).click();
        await dialog.getByLabel('Title').fill(correctedTitle);
        await dialog.getByRole('group', { name: 'Turn 2' }).getByLabel('Text').fill('Synthetic corrected answer');
        await dialog.getByRole('button', { name: 'Review save' }).click();

        const database = new DatabaseSync(join(profile.root, 'workspace.sqlite3'));
        const revision = () => database.prepare('SELECT revision FROM workspaces').get().revision;
        try {
          database.exec("CREATE TRIGGER synthetic_reject_correction BEFORE UPDATE ON private_records BEGIN SELECT raise(ABORT, 'synthetic rollback'); END");
          await approve(dialog);
          await expect(dialog.getByRole('button', { name: 'Retry confirmation' })).toBeVisible();
          expect(revision()).toBe(3);
          expect(database.prepare('SELECT count(*) AS count FROM private_record_receipts').get().count).toBe(3);
          database.exec('DROP TRIGGER synthetic_reject_correction');

          const peerChange = setTimeframe(3, '5m');
          await expect.poll(async () => {
            const response = await post(server, peerChange);
            return (await response.json()).result?.outcome?.state;
          }).toBe('committed');
          await approve(dialog, 'Retry confirmation');
          await expect(dialog.getByRole('alert')).toContainText('changed');
          expect(revision()).toBe(4);
          await dialog.getByRole('button', { name: 'Review save' }).click();
          await approve(dialog);
          await expect(dialog.getByRole('heading', { name: correctedTitle })).toBeVisible();
          await expect(dialog.getByText(answer)).toHaveCount(0);
          expect(revision()).toBe(5);

          await dialog.getByRole('button', { name: 'Review deletion' }).click();
          await approve(dialog, 'Delete private record');
          await expect(dialog.getByText('Private record deleted.')).toBeVisible();
          await expect(dialog.getByText(correctedTitle)).toHaveCount(0);
          await pickBackup(dialog, backup);
          await expect(dialog.getByText(/1 suppressed by known deletions/)).toBeVisible();
          await approve(dialog, 'Restore private records');
          await expect(dialog.getByText('Private records restored.')).toBeVisible();
          expect(revision()).toBe(7);
          expect(database.prepare('SELECT count(*) AS count FROM private_record_deletions').get().count).toBe(1);
          expect(database.prepare('SELECT count(*) AS count FROM private_record_cleanup').get().count).toBe(0);
        } finally { database.close(); }
      });
    });

    await inService(port, profile, 'ubuntu-workspace', async server => {
      await inFreshBrowser(browser, server, async (page, initial) => {
        expect(initial.result.workspace.revision).toBe(7);
        const dialog = await openRecords(page);
        await expect(dialog.getByText(originalTitle)).toHaveCount(0);
        await expect(dialog.getByText(correctedTitle)).toHaveCount(0);
        await expect(dialog.getByRole('button', { name: /Synthetic strategy/ })).toBeVisible();
      });
    });
    await testInfo.attach('private-record-journey', {
      body: JSON.stringify({
        typedKinds: 3, orderedSpeakers: true, separatedProfiles: true, initialDecline: true,
        transactionRollback: true, staleRetry: true, lostReplyReplay: true, walCleanup: true, restart: true, olderBackupSuppressed: true,
      }),
      contentType: 'application/json',
    });
  } finally {
    await profile.close();
    await other.close();
  }
});

async function openPrivateRecords(page) {
  await control(page, 'Private records').click();
  const panel = page.getByRole('dialog', { name: 'Private records' });
  await expect(panel.getByRole('button', { name: 'New private record' })).toBeVisible();
  return panel;
}

async function reviewMemory(panel, title, text) {
  await panel.getByRole('button', { name: 'New private record' }).click();
  await panel.getByLabel('Record type').selectOption('memory');
  await panel.getByLabel('Title').fill(title);
  await panel.getByLabel('Memory', { exact: true }).fill(text);
  await panel.getByRole('button', { name: 'Review save' }).click();
}

async function saveMemory(panel, title, text) {
  await reviewMemory(panel, title, text);
  await panel.getByRole('button', { name: 'Save private record', exact: true }).click();
  await expect(panel.getByRole('heading', { name: title })).toBeVisible();
}

async function focusPrivateRecords(page) {
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
}

test('private record review clears disclosed content when a real writer disconnects', async ({ browser }) => {
  const profile = await makeProfile();
  try {
    const portA = await freePort();
    await inService(portA, profile, 'ubuntu-workspace', async a => {
      await inFreshBrowser(browser, a, async pageA => {
        const first = await openPrivateRecords(pageA);
        const title = 'Synthetic disconnected detail';
        const text = 'Synthetic body visible only while the writer is reachable';
        await saveMemory(first, title, text);
        await inService(await freePort(), profile, 'ubuntu-workspace', async b => {
          await inFreshBrowser(browser, b, async pageB => {
            const second = await openPrivateRecords(pageB);
            await second.getByRole('button', { name: new RegExp(title) }).click();
            const producer = await writerPid(a);
            process.kill(producer, 'SIGTERM');
            await expect.poll(() => alive(producer)).toBe(false);
            await second.getByRole('button', { name: 'Review deletion' }).click();
            await second.getByRole('button', { name: 'Delete private record' }).click();
            await expect(second.getByText('Private record deleted.')).toBeVisible();
            for (let attempt = 0; attempt < 2; attempt += 1) {
              const failed = pageA.waitForResponse(response => response.request().postDataJSON()?.intent?.operation === 'privateRecord.read');
              await focusPrivateRecords(pageA);
              expect((await (await failed).json()).error?.code).toBe('WORKER_EXITED');
            }
            await expect(first.getByText(title)).toHaveCount(0);
            await expect(first.getByText(text)).toHaveCount(0);
            await expect(first.getByRole('button', { name: 'Correct record' })).toHaveCount(0);
            await expect(first.getByRole('button', { name: 'Review deletion' })).toHaveCount(0);
            await expect(first.getByRole('alert')).toContainText('unavailable');
          });
        });
      });
    });
  } finally { await profile.close(); }
});

test('private record review preserves a draft against a changed target', async ({ browser }) => {
  const profile = await makeProfile();
  try {
    await inService(await freePort(), profile, 'ubuntu-workspace', async server => {
      await inFreshBrowser(browser, server, async pageA => {
        const first = await openPrivateRecords(pageA);
        const title = 'Synthetic shared memory';
        await saveMemory(first, title, 'Synthetic original body');
        await first.getByRole('button', { name: 'Correct record' }).click();
        await first.getByRole('textbox', { name: 'Memory', exact: true }).fill('Synthetic first window draft');
        await inFreshBrowser(browser, server, async pageB => {
          const second = await openPrivateRecords(pageB);
          await second.getByRole('button', { name: new RegExp(title) }).click();
          await second.getByRole('button', { name: 'Correct record' }).click();
          await second.getByRole('textbox', { name: 'Memory', exact: true }).fill('Synthetic peer correction');
          await second.getByRole('button', { name: 'Review save' }).click();
          await second.getByRole('button', { name: 'Save private record', exact: true }).click();
          await expect(second.getByText('Synthetic peer correction')).toBeVisible();
          await focusPrivateRecords(pageA);
          await expect(first.getByText(/Another window changed this record/)).toBeVisible();
          await expect(first.getByRole('textbox', { name: 'Memory', exact: true })).toHaveValue('Synthetic first window draft');
          await expect(first.getByText('Synthetic peer correction')).toBeVisible();
          await first.getByRole('button', { name: 'Review correction against current version' }).click();
          await expect(first.getByRole('region', { name: 'Review private record change' }).getByText(title)).toBeVisible();
          await first.getByRole('button', { name: 'Save private record', exact: true }).click();
          await expect(first.getByText('Synthetic first window draft', { exact: true })).toBeVisible();

        });
      });
    });
  } finally { await profile.close(); }
});

test('private record review retires an approval after known peer deletion', async ({ browser }) => {
  const profile = await makeProfile();
  try {
    await inService(await freePort(), profile, 'ubuntu-workspace', async server => {
      await inFreshBrowser(browser, server, async pageA => {
        const first = await openPrivateRecords(pageA);
        const title = 'Synthetic pending deletion';
        const body = 'Synthetic private deletion body';
        await saveMemory(first, title, body);
        await first.getByRole('button', { name: 'Review deletion' }).click();
        const review = first.getByRole('region', { name: 'Review private record change' });
        await expect(review.getByRole('heading')).toBeVisible();
        const includesTarget = await review.getByText(title, { exact: true }).isVisible();
        await inFreshBrowser(browser, server, async pageB => {
          const second = await openPrivateRecords(pageB);
          await second.getByRole('button', { name: new RegExp(title) }).click();
          await second.getByRole('button', { name: 'Review deletion' }).click();
          await second.getByRole('button', { name: 'Delete private record' }).click();
          await expect(second.getByText('Private record deleted.')).toBeVisible();
          await focusPrivateRecords(pageA);
          await expect(first.getByRole('alert')).toContainText('no longer available');
          await expect(first.getByRole('button', { name: 'Delete private record' })).toHaveCount(0);
          await expect(first.getByText(title)).toHaveCount(0);
          await expect(first.getByText(body)).toHaveCount(0);
          expect(includesTarget).toBe(true);
        });
      });
    });
  } finally { await profile.close(); }
});

test('private record review cancellation leaves the other window approval intact', async ({ browser }) => {
  const profile = await makeProfile();
  try {
    await inService(await freePort(), profile, 'ubuntu-workspace', async server => {
      await inFreshBrowser(browser, server, async pageA => {
        const first = await openPrivateRecords(pageA);
        await reviewMemory(first, 'Synthetic first review', 'Synthetic first body');
        const review = first.getByRole('region', { name: 'Review private record change' });
        await expect(review.getByRole('heading')).toBeVisible();
        const includesTarget = await review.getByText('Synthetic first review', { exact: true }).isVisible();
        await inFreshBrowser(browser, server, async pageB => {
          const second = await openPrivateRecords(pageB);
          await reviewMemory(second, 'Synthetic second review', 'Synthetic second body');
          await second.getByRole('button', { name: 'Cancel review' }).click();
          await expect(second.getByText('Action cancelled.')).toBeVisible();
          await first.getByRole('button', { name: 'Save private record', exact: true }).click();
          await expect(first.getByRole('heading', { name: 'Synthetic first review' })).toBeVisible();
          expect(includesTarget).toBe(true);
          await second.getByRole('button', { name: 'Discard draft' }).click();
          await expect(second.getByRole('button', { name: /Synthetic first review/ })).toBeVisible();
          await expect(second.getByRole('button', { name: /Synthetic second review/ })).toHaveCount(0);
        });
      });
    });
  } finally { await profile.close(); }
});

test('private record review counts current survivors absent from an empty backup', async ({ browser }) => {
  const profile = await makeProfile();
  try {
    await inService(await freePort(), profile, 'ubuntu-workspace', async server => {
      await inFreshBrowser(browser, server, async page => {
        const panel = await openPrivateRecords(page);
        const downloading = page.waitForEvent('download');
        await panel.getByRole('button', { name: 'Download local backup' }).click();
        const backup = await readFile(await (await downloading).path());
        expect(JSON.parse(backup).records).toHaveLength(0);
        await saveMemory(panel, 'Synthetic current survivor', 'Synthetic current body');
        await panel.getByRole('button', { name: 'All private records' }).click();
        await panel.getByLabel('Restore local backup').setInputFiles({
          name: 'synthetic-empty.json', mimeType: 'application/json', buffer: backup,
        });
        await expect(panel.getByText(/1 current records to keep/)).toBeVisible();
        await panel.getByRole('button', { name: 'Restore private records' }).click();
        await expect(panel.getByRole('button', { name: /Synthetic current survivor/ })).toBeVisible();
      });
    });
  } finally { await profile.close(); }
});

function isPrivateApproval(payload) {
  return payload?.intent?.operation === 'privateRecord.change'
    && payload.intent.arguments.action === 'resolve' && payload.intent.arguments.decision === 'approve';
}

function privateDetailReply(page, id, matches) {
  return page.waitForResponse(async response => {
    const intent = response.request().postDataJSON()?.intent;
    return intent?.operation === 'privateRecord.read' && intent.arguments.record_id === id && response.ok()
      && matches((await response.json()).result?.outcome);
  });
}

function assertSameDeleteResolution(before, retry) {
  expect(retry.scope).toEqual(before.scope);
  expect(retry.intent).toEqual(before.intent);
  expect(retry.request_id).not.toBe(before.request_id);
  expect(Object.keys(retry.intent.arguments).sort()).toEqual(['action', 'challenge_id', 'decision']);
}

function privateRequestMatches(operation, argumentsMatch = () => true) {
  return payload => payload?.intent?.operation === operation && argumentsMatch(payload.intent.arguments, payload);
}

async function privateReplyDuring(page, stages, step, matches, action) {
  return test.step(step, async () => {
    const matchesRequest = request => new URL(request.url()).pathname === endpoint && matches(request.postDataJSON());
    const observed = { step };
    stages.push(observed);
    try {
      const request = await duringPageEvent(page, 'requestfinished', matchesRequest, action, 8_000, matchesRequest);
      const response = await request.response();
      observed.http = response.status();
      const frame = await response.json();
      const payload = request.postDataJSON();
      const outcome = frame.result?.outcome;
      observed.state = outcome?.state;
      observed.code = outcome?.error?.code ?? frame.error?.code;
      if (frame.ok) expect(frame.result.request_id).toBe(payload.request_id);
      return { payload, outcome, observed };
    } catch (error) {
      observed.error = error.message.split('\n')[0];
      throw error;
    }
  });
}

function clickPrivate(panel, name) {
  return panel.getByRole('button', { name, exact: true }).click({ timeout: 5_000 });
}

async function assertPrivateHidden(panel, ...values) {
  for (const value of values) {
    await expect(panel.getByText(value, { exact: true })).toHaveCount(0);
  }
}

test('an authorized peer sees shared producer BUSY and its failed step detaches the observer', async ({ browser }, testInfo) => {
  const profile = await makeProfile();
  const stages = [];
  try {
    await inService(await freePort(), profile, 'ubuntu-workspace', async server => {
      await inFreshBrowser(browser, server, async origin => {
        await inFreshBrowser(browser, server, async (peer, initial) => {
          expect(initial.ok).toBe(true);
          await openPrivateRecords(origin);
          const writer = await writerPid(server);
          const listenerEvents = ['requestfinished', 'requestfailed', 'close', 'crash'];
          const listenerCounts = () => listenerEvents.map(event => origin.listenerCount(event));
          const before = listenerCounts();
          const read = privateRequestMatches('privateRecord.read');
          const observedPeerFailure = privateReplyDuring(origin, stages, 'abandoned origin observer', read, async () => {
            await writerPid(server, 'suspend', writer);
            try {
              await focusPrivateRecords(origin);
              await expect.poll(() => postStatus(server, admissionProbe), { timeout: 3_000 }).toBe(429);
              const reply = await privateReplyDuring(peer, stages, 'peer opens private records during origin poll', read,
                () => control(peer, 'Private records').click({ timeout: 5_000 }));
              expect(reply.observed).toMatchObject({ http: 429, code: 'BUSY' });
              throw new Error(`Peer private record read returned HTTP ${reply.observed.http} ${reply.observed.code}`);
            } finally {
              await writerPid(server, 'resume', writer);
            }
          });
          await expect(observedPeerFailure).rejects.toThrow('Peer private record read returned HTTP 429 BUSY');
          expect(listenerCounts()).toEqual(before);
          stages.push({ step: 'observer cleanup', listenersRestored: true, sharedWriter: writer });
          await expect(peer.getByRole('alert')).toContainText('The local workspace is busy.');
        });
      });
    });
  } finally {
    await testInfo.attach('shared-private-writer', { body: JSON.stringify(stages), contentType: 'application/json' });
    await profile.close();
  }
});

for (const fault of ['lost reply', 'blocked checkpoint']) {
  test(`private record delete recovers after detail disappears with ${fault}`, async ({ browser }) => {
    const profile = await makeProfile();
    try {
      await inService(await freePort(), profile, 'ubuntu-workspace', async server => {
        await inFreshBrowser(browser, server, async page => {
          const panel = await openPrivateRecords(page);
          const title = 'Synthetic deleted record title';
          const text = 'Synthetic deleted record text';
          await saveMemory(panel, title, text);
          const databasePath = join(profile.root, 'workspace.sqlite3');
          const inspector = new DatabaseSync(databasePath, { readOnly: true });
          const snapshot = fault === 'blocked checkpoint' ? new DatabaseSync(databasePath, { readOnly: true }) : null;
          try {
            const id = inspector.prepare('SELECT record_id FROM private_records').get().record_id;
            const resolution = page.waitForRequest(request => isPrivateApproval(request.postDataJSON()));
            const gone = privateDetailReply(page, id, outcome => outcome?.error?.code === 'DATA_UNAVAILABLE');
            await panel.getByRole('button', { name: 'Review deletion' }).click();
            let initialReceipt;
            if (snapshot) {
              snapshot.exec('BEGIN');
              expect(snapshot.prepare('SELECT revision FROM private_records').get().revision).toBe(1);
              const approval = page.waitForResponse(response => isPrivateApproval(response.request().postDataJSON()));
              await panel.getByRole('button', { name: 'Delete private record', exact: true }).click();
              expect((await (await approval).json()).result.outcome.error.code).toBe('BUSY');
              expect(inspector.prepare('SELECT revision FROM private_record_cleanup').get().revision).toBe(2);
            } else {
              const dropped = await dropApprovalReply(page, server, 'privateRecord.change', isPrivateApproval);
              await panel.getByRole('button', { name: 'Delete private record', exact: true }).click();
              await expect.poll(dropped.outcome).toMatchObject({ state: 'committed' });
              initialReceipt = dropped.outcome().receipt;
              await dropped.restore();
            }
            const first = (await resolution).postDataJSON();
            await focusPrivateRecords(page);
            await gone;
            await expect(panel.getByRole('alert')).toContainText('no longer available');
            await expect(panel.getByText(title)).toHaveCount(0);
            await expect(panel.getByText(text)).toHaveCount(0);
            await expect(panel.getByRole('button', { name: 'Delete private record', exact: true })).toHaveCount(0);
            expect(inspector.prepare('SELECT revision FROM workspaces').get().revision).toBe(2);
            const operation = inspector.prepare('SELECT id FROM operation_log WHERE revision = 2').get().id;
            if (snapshot) snapshot.exec('COMMIT');
            const foreign = { ...first, scope: { ...first.scope, workspace_id: 'synthetic-foreign-workspace' } };
            await expect.poll(() => postStatus(server, foreign)).toBe(403);

            const retry = panel.getByRole('button', { name: 'Retry confirmation' });
            await expect(retry).toBeEnabled();
            const replay = page.waitForResponse(response => isPrivateApproval(response.request().postDataJSON()));
            await retry.click();
            const response = await replay;
            assertSameDeleteResolution(first, response.request().postDataJSON());
            const outcome = (await response.json()).result.outcome;
            expect(outcome).toEqual({
              state: 'committed', operation: 'privateRecord.change',
              receipt: {
                operation_id: operation, workspace_id: workspaceId, record_id: id,
                committed_workspace_revision: 2, undoable: false,
              },
            });
            if (initialReceipt) expect(outcome.receipt).toEqual(initialReceipt);
            await expect(panel.getByText('Private record deleted.', { exact: true })).toBeVisible();
            await expect(panel.getByText(operation, { exact: true })).toBeVisible();
            await expect(panel.getByRole('button', { name: 'Retry confirmation' })).toHaveCount(0);
            expect(inspector.prepare('SELECT revision FROM workspaces').get().revision).toBe(2);
            expect(inspector.prepare('SELECT count(*) AS count FROM private_record_deletions').get().count).toBe(1);
            expect(inspector.prepare('SELECT count(*) AS count FROM private_record_cleanup').get().count).toBe(0);
          } finally {
            snapshot?.close();
            inspector.close();
          }
        });
      });
    } finally { await profile.close(); }
  });
}

test('private record delete recovery hides its old title after pre-commit busy and peer rename', async ({ browser }, testInfo) => {
  const profile = await makeProfile();
  const stages = [];
  try {
    await inService(await freePort(), profile, 'ubuntu-workspace', async origin => {
      await inFreshBrowser(browser, origin, async pageA => {
        const first = await openPrivateRecords(pageA);
        const title = 'Synthetic old title';
        const renamed = 'Synthetic peer title';
        const text = 'Synthetic original body';
        const corrected = 'Synthetic corrected body';
        await saveMemory(first, title, text);
        await clickPrivate(first, 'Review deletion');
        const writerLock = new DatabaseSync(join(profile.root, 'workspace.sqlite3'));
        try {
          const id = writerLock.prepare('SELECT record_id FROM private_records').get().record_id;
          const readDetail = privateRequestMatches('privateRecord.read', args => args.record_id === id);
          const retry = first.getByRole('button', { name: 'Retry confirmation' });
          const state = () => {
            const row = writerLock.prepare('SELECT revision, content FROM private_records WHERE record_id = ?').get(id);
            return {
              revision: writerLock.prepare('SELECT revision FROM workspaces').get().revision,
              record: row && { revision: row.revision, content: JSON.parse(row.content) },
              deletions: writerLock.prepare('SELECT count(*) AS count FROM private_record_deletions').get().count,
              pendingCleanup: writerLock.prepare('SELECT count(*) AS count FROM private_record_cleanup').get().count,
              receipts: writerLock.prepare('SELECT count(*) AS count FROM private_record_receipts').get().count,
            };
          };
          const originalContent = { kind: 'memory', title, text };
          const correctedContent = { kind: 'memory', title: renamed, text: corrected };
          const originalState = { revision: 1, record: { revision: 1, content: originalContent },
            deletions: 0, pendingCleanup: 0, receipts: 1 };
          const correctedState = { revision: 2, record: { revision: 2, content: correctedContent },
            deletions: 0, pendingCleanup: 0, receipts: 2 };
          const expectCorrectedRead = reply => {
            expect(reply.observed.http).toBe(200);
            expect(reply.outcome, JSON.stringify(reply.observed)).toEqual({
              state: 'completed', operation: 'privateRecord.read', result: {
                workspace_revision: 2, records: [], record: { record_id: id, revision: 2, content: correctedContent },
              },
            });
          };
          writerLock.exec('BEGIN IMMEDIATE');
          const rejected = await privateReplyDuring(pageA, stages, 'origin deletion under SQLite writer reservation',
            isPrivateApproval, () => clickPrivate(first, 'Delete private record'));
          expect(rejected.observed).toMatchObject({ http: 200, state: 'failed', code: 'BUSY' });
          expect(state()).toEqual(originalState);
          writerLock.exec('ROLLBACK');
          await expect(retry).toBeEnabled();
          await assertPrivateHidden(first, title, text);

          await inService(await freePort(), profile, 'ubuntu-workspace', async peerService => {
            const originWriter = await writerPid(origin);
            const peerWriter = await writerPid(peerService);
            expect(peerWriter).not.toBe(originWriter);
            expect(peerService.capability === origin.capability).toBe(false);
            stages.push({ step: 'independent authorized services', originWriter, peerWriter });
            await inFreshBrowser(browser, peerService, async (pageB, initial) => {
              expect(initial.result?.workspace.revision, initial.error?.code).toBe(1);
              const index = await privateReplyDuring(pageB, stages, 'authorized peer opens the private record index',
                privateRequestMatches('privateRecord.read', args => args.record_id === null),
                () => control(pageB, 'Private records').click({ timeout: 5_000 }));
              expect(index.outcome, JSON.stringify(index.observed)).toMatchObject({
                state: 'completed', result: { workspace_revision: 1, record: null, records: [{ record_id: id, title, revision: 1 }] },
              });
              const peer = pageB.getByRole('dialog', { name: 'Private records' });
              const detail = await privateReplyDuring(pageB, stages, 'authorized peer reads original record', readDetail,
                () => peer.getByRole('button', { name: new RegExp(title) }).click({ timeout: 5_000 }));
              expect(detail.outcome, JSON.stringify(detail.observed)).toMatchObject({
                state: 'completed', result: { workspace_revision: 1, record: { record_id: id, revision: 1, content: originalContent } },
              });
              await clickPrivate(peer, 'Correct record');
              await peer.getByRole('textbox', { name: 'Title', exact: true }).fill(renamed);
              await peer.getByRole('textbox', { name: 'Memory', exact: true }).fill(corrected);
              const prepared = await privateReplyDuring(pageB, stages, 'peer prepares correction',
                privateRequestMatches('privateRecord.change', args => args.action === 'save' && args.record_id === id),
                () => clickPrivate(peer, 'Review save'));
              expect(prepared.observed).toMatchObject({ http: 200, state: 'confirmation_required' });
              expect(prepared.outcome.summary.corrected).toBe(1);

              let committed;
              try {
                committed = await privateReplyDuring(pageB, stages, 'peer approves correction', isPrivateApproval,
                  () => clickPrivate(peer, 'Save private record'));
              } finally {
                const { record, ...stored } = state();
                stages.push({ step: 'stored after peer approval', ...stored, recordRevision: record?.revision });
              }
              expect(committed.observed).toMatchObject({ http: 200, state: 'committed' });
              expect(committed.payload.scope).toEqual(prepared.payload.scope);
              expect(committed.payload.intent.arguments.challenge_id).toBe(prepared.outcome.challenge_id);
              expect(committed.outcome.receipt).toMatchObject({
                workspace_id: workspaceId, record_id: id, committed_workspace_revision: 2, undoable: false,
              });
              expect(state()).toEqual(correctedState);
              await expect(peer.getByRole('heading', { name: renamed })).toBeVisible();
              await expect(peer.getByText(corrected, { exact: true })).toBeVisible();

              const denied = await post(peerService, { ...detail.payload, request_id: randomUUID(), message_id: randomUUID() },
                { Authorization: '' });
              expect(denied.status).toBe(403);
              expect(await denied.json()).toEqual({ ok: false, error: { code: 'PERMISSION_DENIED', retryable: false } });
              stages.push({ step: 'ungranted current read', http: 403, code: 'PERMISSION_DENIED' });
            });
          });

          const focused = await privateReplyDuring(pageA, stages, 'origin reads the corrected revision around focus', readDetail,
            async () => {
              await focusPrivateRecords(pageA);
              stages.push({ step: 'origin focus dispatched' });
            });
          expectCorrectedRead(focused);
          await assertPrivateHidden(first, title, text, renamed, corrected);
          await expect(first.getByRole('button', { name: 'Delete private record', exact: true })).toHaveCount(0);

          let retrySent = false;
          let gatedRead;
          const releaseRead = Promise.withResolvers();
          const endpointUrl = origin.url + endpoint;
          const gateCurrentRead = async route => {
            const payload = route.request().postDataJSON();
            if (isPrivateApproval(payload)) retrySent = true;
            if (retrySent && !gatedRead && readDetail(payload)) {
              gatedRead = payload.request_id;
              await releaseRead.promise;
            }
            await route.continue();
          };
          await pageA.route(endpointUrl, gateCurrentRead);
          try {
            const current = await privateReplyDuring(pageA, stages, 'authorized reread after stale deletion retry',
              payload => readDetail(payload) && payload.request_id === gatedRead, async () => {
                const retried = await privateReplyDuring(pageA, stages, 'origin retries the original deletion resolution',
                  isPrivateApproval, () => clickPrivate(first, 'Retry confirmation'));
                assertSameDeleteResolution(rejected.payload, retried.payload);
                expect(retried.observed).toMatchObject({ http: 200, state: 'failed', code: 'STALE_CONTEXT' });
                await expect.poll(() => Boolean(gatedRead)).toBe(true);
                await assertPrivateHidden(first, title, text, renamed, corrected);
                await expect(first.getByRole('button', { name: 'Delete private record', exact: true })).toHaveCount(0);
                stages.push({ step: 'before corrected detail response', contentHidden: true, sameResolution: true });
                releaseRead.resolve();
              });
            expectCorrectedRead(current);
          } finally {
            releaseRead.resolve();
            await pageA.unrouteAll({ behavior: 'wait' });
          }
          await expect(first.getByRole('alert')).toContainText('changed');
          await expect(first.getByRole('heading', { name: renamed })).toBeVisible();
          await expect(first.getByText(corrected, { exact: true })).toBeVisible();
          await assertPrivateHidden(first, title, text);
          await expect(retry).toHaveCount(0);
          expect(state()).toEqual(correctedState);
          stages.push({ step: 'corrected detail displayed', revision: 2, deletions: 0 });
        } finally { writerLock.close(); }
      });
    });
  } finally {
    await testInfo.attach('private-deletion-peer-recovery', { body: JSON.stringify(stages), contentType: 'application/json' });
    await profile.close();
  }
});
