import { execFile, spawn } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { once } from 'node:events';
import { chmod, mkdir, mkdtemp, readFile, rm, stat, writeFile } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { promisify } from 'node:util';
import { expect, test } from '@playwright/test';
import { control, leaveSample } from '../ui/workspace-controls.mjs';
import { painted } from '../ui/chart-pixels.mjs';
import { startLocalImportBridge } from '../ui/local-import-bridge.mjs';
import { assertUnsuitableWindowsHandoffsRejected, assertWindowsPrivateHandoff } from './workspace_test_handoff_acl.mjs';

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
const service = `
  const [entry, mode, port] = process.argv.slice(1);
  const { createServer } = await import(entry);
  // These journeys restart the service explicitly. Leave file watching to interactive Vite.
  const server = await createServer({ mode, server: { host: '127.0.0.1', port: Number(port), strictPort: true, watch: null } });
  process.once('message', async () => {
    await server.close();
    process.disconnect();
  });
  await server.listen();
`;

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

test('published revocation removes visible and delayed frozen evidence and leaves an authorized clear', async ({ browser }, testInfo) => {
  const fixture = await publishRevocationFixture(testInfo);
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

async function publishRevocationFixture(testInfo) {
  const binary = join(root, 'target/debug/examples', process.platform === 'win32' ? 'local_import_bridge.exe' : 'local_import_bridge');
  await exec('cargo', ['build', '--locked', '-p', 'scopehub-desktop', '--example', 'local_import_bridge',
    '--features', 'portable-test-bridge', '--target-dir', 'target'], { cwd: root, timeout: 90_000 });
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

async function seedFrozenEvidence(profile, id, a, b) {
  const create = exec(workspaceBridge, [profile.root, profile.id, workspaceId], { timeout: 12_000 });
  create.child.stdin.end();
  await create;
  // Independent fixture inputs. The application reopens and verifies the actual
  // SQLite revision. No operation, authorization or chart reply is stubbed.
  const database = new DatabaseSync(join(profile.root, 'workspace.sqlite3'));
  const csv = Buffer.from('Date,Symbol,Open,High,Low,Close,Volume\n2026-02-03,FIX,30,34,29,31.5,120\n2026-02-04,FIX,42,47,40,44.25,\n');
  const digest = createHash('sha256').update(csv).digest('hex');
  const mappingDigest = 'b'.repeat(64);
  const bars = evidenceBars(a, b).map((bar, index) => ({
    bar, locators: [{ record_number: index + 1, line_start: index + 2, line_end: index + 2 }],
  }));
  try {
    database.exec('PRAGMA foreign_keys = ON; BEGIN');
    database.prepare('INSERT INTO reviewed_import_mappings VALUES (?, ?, ?, ?)').run('source-fixture', 1, 'a'.repeat(64), '{}');
    database.prepare('INSERT INTO local_dataset_source_blobs VALUES (?, ?)').run(digest, csv);
    database.prepare('INSERT INTO local_dataset_revisions VALUES (?, ?, ?, ?, ?, ?, ?)').run(
      id, 'source-fixture', 'frozen-fixture.csv', digest, 1, mappingDigest,
      JSON.stringify({ symbol: 'FIX', series: evidenceSeries, source_record_count: 2, bars }),
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

test('the offline sample keeps its own timeframe without replacing the private undo', async ({ browser }, testInfo) => {
  const profile = await makeProfile();
  const port = await freePort();
  const context = await browser.newContext();
  try {
    await inService(port, profile, 'ubuntu-workspace', async server => {
      const page = await context.newPage();
      const launchPath = '/synthetic-private-launch';
      await page.addInitScript(({ launchPath, capability }) => {
        // Simulate the real handoff without exposing its fragment in navigation
        // artifacts or re-injecting it on refresh after the UI scrubs it.
        if (location.pathname === launchPath) history.replaceState(null, '', '/#workspace=' + capability);
      }, { launchPath, capability: server.capability });
      const workspaceRequests = [];
      context.on('request', request => {
        if (new URL(request.url()).pathname !== endpoint) return;
        const body = request.postDataJSON();
        workspaceRequests.push(body.intent?.operation ?? body.type);
      });
      const launch = () => page.goto(server.url + launchPath);
      const shellUrl = url => url.origin === server.url && url.pathname === '/' && url.hash === '';
      const readPrivate = async () => state(await (await post(server, snapshot)).json());
      const assertSample = async (tab, timeframe) => {
        await expect(tab.locator('.connection')).toHaveText('Offline sampleSimulated');
        await expect(control(tab, timeframe)).toHaveAttribute('aria-pressed', 'true');
        await expect(tab.locator('.history')).toHaveAttribute('data-timeframe', timeframe);
      };
      const undo = control(page, 'Undo timeframe change');

      await launch();
      await expect(control(page, '1m')).toHaveAttribute('aria-pressed', 'true');
      await expect(page).toHaveURL(server.url + '/');
      await expect(page.locator('.connection')).toHaveText('No data source');
      expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
      expect(await readPrivate()).toEqual({ revision: 0, timeframe: '1m', undo: null });
      expect((await act(page, '5m', 'chart.setTimeframe')).result.outcome)
        .toMatchObject({ state: 'committed', result: { timeframe: '5m', revision: 1 } });
      const prior = (await act(page, '1m', 'chart.setTimeframe')).result.outcome;
      expect(prior).toMatchObject({ state: 'committed', result: { timeframe: '1m', revision: 2 }, receipt: { undoable: true } });
      const before = {
        revision: 2, timeframe: '1m',
        undo: { operation_id: prior.receipt.operation_id, committed_workspace_revision: 2 },
      };
      expect(await readPrivate()).toEqual(before);
      await expect(undo).toBeEnabled();

      const beforeSample = workspaceRequests.length;
      await control(page, 'Open offline sample').click();
      await assertSample(page, '1m');
      await control(page, '5m').click();
      await assertSample(page, '5m');
      const after = await readPrivate();
      const refreshed = await context.newPage();
      await refreshed.goto(server.url);
      await expect(refreshed.locator('.history')).toBeVisible();
      await refreshed.reload();
      await expect(refreshed.locator('.history')).toBeVisible();
      const refreshedTimeframe = await refreshed.locator('.history').getAttribute('data-timeframe');
      await testInfo.attach('workspace-sample-timeframe', {
        body: JSON.stringify({ before, after, refreshedTimeframe, sampleWorkspaceRequests: workspaceRequests.slice(beforeSample) }),
        contentType: 'application/json',
      });
      expect(after).toEqual(before);
      expect(workspaceRequests.slice(beforeSample)).toEqual([]);
      await expect(undo).toHaveCount(0);
      await assertSample(refreshed, '5m');
      await expect(control(refreshed, 'Undo timeframe change')).toHaveCount(0);
      await assertSampleStorage(refreshed, '5m');
      await refreshed.close();

      await leaveSample(page);
      await expect(page).toHaveURL(shellUrl);
      await expect(page.locator('.connection')).toHaveText('No data source');
      await expect(control(page, '1m')).toHaveAttribute('aria-pressed', 'true');
      await expect(undo).toBeEnabled();
      expect(await readPrivate()).toEqual(before);
      const reply = await duringResponse(page, 'workspace.undo', () => undo.click());
      expect(reply.request().postDataJSON().intent.arguments).toEqual({ operation_id: prior.receipt.operation_id });
      expect((await reply.json()).result.outcome).toMatchObject({
        state: 'committed', result: { timeframe: '5m', revision: 3 }, receipt: { undoable: false },
      });
      await expect(undo).toBeDisabled();
      const afterUndo = { revision: 3, timeframe: '5m', undo: null };
      expect(await readPrivate()).toEqual(afterUndo);
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
      expect(workspaceRequests.slice(beforeReload)).toEqual(['snapshot']);

      const beforeSampleReload = workspaceRequests.length;
      await control(page, 'Open offline sample').click();
      await assertSample(page, '1m');
      await control(page, '1d').click();
      await assertSample(page, '1d');
      await assertSampleStorage(page, '1d');
      await page.reload();
      await assertSample(page, '1d');
      await expect(undo).toHaveCount(0);
      await assertSampleStorage(page, '1d');
      await leaveSample(page);
      await expect(page.locator('.connection')).toHaveText('No data source');
      await expect(control(page, '1m')).toHaveAttribute('aria-pressed', 'true');
      await expect(undo).toHaveCount(0);
      expect(workspaceRequests.slice(beforeSampleReload)).toEqual([]);
      expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
      expect(await readPrivate()).toEqual(afterUndo);
    });
  } finally {
    await context.close();
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
    const events = ['response', 'close', 'crash'];
    const before = events.map(event => page.listenerCount(event));
    await expect(openWorkspaceSnapshot(page, url)).rejects.toThrow(/page\.goto: net::ERR_FAILED/);
    expect(events.map(event => page.listenerCount(event))).toEqual(before);
    expect(navigations).toBe(1);
  } finally {
    await context.close();
  }
});

test('a status-only probe cancels its unread response body', async () => {
  let cancelled = false;
  const body = new ReadableStream({ cancel() { cancelled = true; } });
  expect(await responseStatus(new Response(body, { status: 429 }))).toBe(429);
  expect(cancelled).toBe(true);
});

async function freePort() {
  const server = createServer();
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const port = server.address().port;
  await new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
  return port;
}

async function inService(port, profile, mode, use, { expectHandoff = true, terminalSignal } = {}) {
  const url = 'http://127.0.0.1:' + port;
  const args = terminalSignal
    ? [viteCli, '--mode', mode, '--host', '127.0.0.1', '--port', String(port), '--strictPort']
    : ['--input-type=module', '--eval', service, vite, mode, String(port)];
  const child = spawn(process.execPath, args, {
    cwd: ui,
    env: {
      ...process.env,
      SCOPEHUB_WORKSPACE_PROFILE_ROOT: profile.root,
      SCOPEHUB_WORKSPACE_PROFILE_ID: profile.id,
      SCOPEHUB_WORKSPACE_ID: profile.workspaceId ?? workspaceId,
    },
    stdio: terminalSignal ? ['pipe', 'pipe', 'pipe'] : ['ignore', 'pipe', 'pipe', 'ipc'],
  });
  const closed = once(child, 'close');
  let output = '';
  let handoffRejected = false;
  for (const stream of [child.stdout, child.stderr]) {
    stream.setEncoding('utf8');
    stream.on('data', chunk => {
      const recent = output + chunk;
      handoffRejected ||= recent.includes('The private workspace handoff could not be issued.');
      output = recent.slice(-6000);
    });
  }
  const server = {
    url, pid: child.pid,
    handoff: join(profile.root, 'workspace-access-' + port + '.html'),
    handoffRejected: () => handoffRejected,
  };
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
    if (mode === 'ubuntu-workspace' && expectHandoff) {
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
    await use(server);
  } finally {
    if (terminalSignal) child.kill(terminalSignal);
    else if (child.connected) child.send('close', () => {});
    const grace = setTimeout(() => child.kill('SIGKILL'), 7_000);
    try {
      const [, signal] = await closed;
      expect(signal).not.toBe('SIGKILL');
      if (server.capability) expect(!output.includes(server.capability)).toBe(true);
    } finally {
      clearTimeout(grace);
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
      matchesSample: preference?.source === 'scopehub.synthetic' && preference.timeframe === expected
        && typeof preference.symbol === 'string' && /^SIM\.[.A-Z0-9]+$/.test(preference.symbol)
        && preference.style === 'candlestick' && preference.cursor === 'crosshair'
        && preference.panel === 'watchlist' && preference.chartOnly === false && preference.narrow === false
        && preference.sampleRevision === 'normal' && preference.sampleDate === ''
        && Object.keys(preference).length === 10,
    };
  }, timeframe);
  expect(stored).toEqual({ onlySamplePreference: true, emptySessionStorage: true, matchesSample: true });
}

async function inFreshBrowser(browser, server, use) {
  const context = await browser.newContext();
  try {
    // Read the real launcher handoff but set its fragment locally before page
    // code runs. No capability is put in Playwright navigation URLs or traces.
    await context.addInitScript(capability => {
      window.history.replaceState(null, '', '#workspace=' + capability);
    }, server.capability);
    const page = await context.newPage();
    const requests = observeOperations(page);
    let capabilityInRequestUrl = false;
    page.on('request', request => { capabilityInRequestUrl ||= request.url().includes(server.capability); });
    await use(page, await openWorkspaceSnapshot(page, server.url));
    expect(requests.every(path => path === endpoint)).toBe(true);
    expect(capabilityInRequestUrl).toBe(false);
  } finally {
    await context.close();
  }
}

async function openWorkspaceSnapshot(page, url) {
  const response = await duringPageEvent(page, 'response', response =>
    new URL(response.url()).pathname === endpoint && response.request().postDataJSON()?.type === 'snapshot', async () => {
    await page.goto(url);
    await expect(control(page, 'Open offline sample')).toBeVisible();
    await expect(page.getByText('No data source')).toBeVisible();
    expect(await page.evaluate(() => window.location.hash === '')).toBe(true);
    expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
  });
  return response.json();
}

function duringResponse(page, operation, action, timeout) {
  return duringPageEvent(page, 'response', response => new URL(response.url()).pathname === endpoint
    && response.request().postDataJSON()?.intent?.operation === operation, action, timeout);
}

async function duringPageEvent(page, event, matches, action, timeout) {
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
  page.on(event, onEvent);
  page.on('close', onClose);
  page.on('crash', onCrash);
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

function deleteResolution(payload) {
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
            const resolution = page.waitForRequest(request => deleteResolution(request.postDataJSON()));
            const gone = privateDetailReply(page, id, outcome => outcome?.error?.code === 'DATA_UNAVAILABLE');
            await panel.getByRole('button', { name: 'Review deletion' }).click();
            let initialReceipt;
            if (snapshot) {
              snapshot.exec('BEGIN');
              expect(snapshot.prepare('SELECT revision FROM private_records').get().revision).toBe(1);
              const approval = page.waitForResponse(response => deleteResolution(response.request().postDataJSON()));
              await panel.getByRole('button', { name: 'Delete private record', exact: true }).click();
              expect((await (await approval).json()).result.outcome.error.code).toBe('BUSY');
              expect(inspector.prepare('SELECT revision FROM private_record_cleanup').get().revision).toBe(2);
            } else {
              const dropped = await dropApprovalReply(page, server, 'privateRecord.change', deleteResolution);
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
            const replay = page.waitForResponse(response => deleteResolution(response.request().postDataJSON()));
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

test('private record delete recovery hides its old title after pre-commit busy and peer rename', async ({ browser }) => {
  const profile = await makeProfile();
  try {
    await inService(await freePort(), profile, 'ubuntu-workspace', async server => {
      await inFreshBrowser(browser, server, async pageA => {
        const first = await openPrivateRecords(pageA);
        const title = 'Synthetic old title';
        const renamed = 'Synthetic peer title';
        const text = 'Synthetic original body';
        await saveMemory(first, title, text);
        await first.getByRole('button', { name: 'Review deletion' }).click();
        const writerLock = new DatabaseSync(join(profile.root, 'workspace.sqlite3'));
        try {
          const id = writerLock.prepare('SELECT record_id FROM private_records').get().record_id;
          writerLock.exec('BEGIN IMMEDIATE');
          const response = pageA.waitForResponse(reply => deleteResolution(reply.request().postDataJSON()));
          await first.getByRole('button', { name: 'Delete private record', exact: true }).click();
          const rejected = await response;
          expect((await rejected.json()).result.outcome.error.code).toBe('BUSY');
          expect(writerLock.prepare('SELECT revision FROM workspaces').get().revision).toBe(1);
          writerLock.exec('COMMIT');
          await expect(first.getByRole('button', { name: 'Retry confirmation' })).toBeEnabled();

          const observedRename = privateDetailReply(pageA, id, outcome => outcome?.result?.record?.content?.title === renamed);
          await inFreshBrowser(browser, server, async pageB => {
            const peer = await openPrivateRecords(pageB);
            await peer.getByRole('button', { name: new RegExp(title) }).click();
            await peer.getByRole('button', { name: 'Correct record' }).click();
            await peer.getByRole('textbox', { name: 'Title', exact: true }).fill(renamed);
            await peer.getByRole('button', { name: 'Review save' }).click();
            await peer.getByRole('button', { name: 'Save private record', exact: true }).click();
            await expect(peer.getByRole('heading', { name: renamed })).toBeVisible();
          });

          await focusPrivateRecords(pageA);
          await observedRename;
          await expect(first.getByText(title, { exact: true })).toHaveCount(0);
          await expect(first.getByText(text, { exact: true })).toHaveCount(0);
          await expect(first.getByRole('button', { name: 'Delete private record', exact: true })).toHaveCount(0);
          const replay = pageA.waitForResponse(reply => deleteResolution(reply.request().postDataJSON()));
          await first.getByRole('button', { name: 'Retry confirmation' }).click();
          const retried = await replay;
          assertSameDeleteResolution(rejected.request().postDataJSON(), retried.request().postDataJSON());
          expect((await retried.json()).result.outcome.error.code).toBe('STALE_CONTEXT');
          await expect(first.getByRole('alert')).toContainText('changed');
          await expect(first.getByRole('heading', { name: renamed })).toBeVisible();
          expect(writerLock.prepare('SELECT revision FROM workspaces').get().revision).toBe(2);
          expect(writerLock.prepare('SELECT count(*) AS count FROM private_record_deletions').get().count).toBe(0);
        } finally { writerLock.close(); }
      });
    });
  } finally { await profile.close(); }
});
