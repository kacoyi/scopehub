import { execFile } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { once } from 'node:events';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import { expect, test } from '@playwright/test';
import { painted } from './chart-pixels.mjs';
import { readPublishedWorkspaceProfile, startLocalImportBridge } from './local-import-bridge.mjs';
import { control } from './workspace-controls.mjs';
import { closeWorkspaceService, spawnWorkspaceService } from './workspace-service.mjs';

const exec = promisify(execFile);
const root = fileURLToPath(new URL('../../', import.meta.url));
const windows = process.platform === 'win32';
const workspaceBridge = join(root, 'target', 'debug', windows ? 'workspace_bridge.exe' : 'workspace_bridge');
const python = join(root, '.venv', ...(windows ? ['Scripts', 'python.exe'] : ['bin', 'python']));
const endpoint = '/__scopehub/workspace';

test.beforeAll(async ({}, testInfo) => {
  testInfo.setTimeout(900_000);
  await exec('cargo', ['build', '--locked', '--target-dir', 'target',
    '-p', 'scopehub-persistence', '--bin', 'workspace_bridge',
    '-p', 'scopehub-worker-host', '--example', 'history_bridge',
    '-p', 'scopehub-desktop', '--example', 'local_import_bridge', '--features', 'scopehub-desktop/portable-test-bridge',
  ], { cwd: root, env: { ...process.env, CARGO_BUILD_JOBS: '2' }, timeout: 875_000 });
});

async function withProfile(use) {
  const temp = await mkdtemp(join(tmpdir(), 'scopehub-layout-test-'));
  const directory = join(temp, 'profile');
  try {
    await exec(workspaceBridge, ['--prepare-profile-root', directory], { timeout: 12_000 });
    await use({ directory, id: `synthetic-layout-${randomUUID()}`, workspaceId: 'main' });
  } finally {
    await rm(temp, { recursive: true, force: true });
  }
}

async function withService(profile, use) {
  const port = process.env.SCOPEHUB_WORKSPACE_LAYOUT_PORT ?? '0';
  const child = spawnWorkspaceService(profile, { port });
  const closed = once(child, 'close');
  const listening = once(child, 'message', { signal: AbortSignal.timeout(15_000) });
  let diagnostic = '';
  for (const stream of [child.stdout, child.stderr]) {
    stream.setEncoding('utf8');
    stream.on('data', chunk => { diagnostic = (diagnostic + chunk).slice(-4000); });
  }
  try {
    const [actual] = await Promise.race([
      listening,
      closed.then(([code, signal]) => { throw new Error(`Layout service exited (${code ?? signal}). ${diagnostic}`); }),
    ]);
    const url = `http://127.0.0.1:${actual}`;
    const handoff = join(profile.directory, `workspace-access-${actual}.html`);
    let capability;
    await expect.poll(async () => {
      const document = await readFile(handoff, 'utf8').catch(error => {
        if (error.code === 'ENOENT') return '';
        throw error;
      });
      const link = /href="(http:\/\/127\.0\.0\.1:\d+\/#workspace=([a-f0-9]{64}))"/.exec(document);
      if (link && new URL(link[1]).origin === url) capability = link[2];
      return capability !== undefined;
    }, { timeout: 12_000, message: 'The real writer issues a private workspace handoff' }).toBe(true);
    const { stdout } = await exec(python, ['-I', join(root, 'tests/ubuntu-preview/workspace_test_writer.py'),
      String(process.pid), String(child.pid)], { timeout: 5_000 });
    await use({ url, capability, serverPid: child.pid, writerPid: Number(stdout.trim()) });
    expect(diagnostic.includes(capability)).toBe(false);
  } finally {
    const [code] = await closeWorkspaceService(child, closed);
    expect(code).toBe(0);
  }
}

async function withBrowser(browser, server, use, { authorized = true, setup } = {}) {
  const context = await browser.newContext({ viewport: { width: 1366, height: 900 } });
  context.setDefaultTimeout(8_000);
  try {
    if (authorized) {
      // Send the same fragment as the real handoff without recording the token in navigation URLs.
      await context.addInitScript(({ origin, capability }) => {
        if (location.origin === origin && window === window.top) {
          history.replaceState(history.state, '', '#workspace=' + capability);
        }
      }, { origin: server.url, capability: server.capability });
    }
    const page = await context.newPage();
    const sent = [];
    page.on('request', request => sent.push(request.url()));
    await setup?.(page);
    await page.goto(server.url);
    await use(page);
    expect(sent.every(url => new URL(url).origin === server.url && !url.includes(server.capability))).toBe(true);
    if (!authorized) expect(sent.some(url => new URL(url).pathname === endpoint)).toBe(false);
  } finally { await context.close(); }
}

async function frame(server, request) {
  let reply;
  await expect.poll(async () => {
    const response = await fetch(server.url + endpoint, {
      method: 'POST', signal: AbortSignal.timeout(5_000),
      headers: { Origin: server.url, 'Content-Type': 'application/json', Authorization: 'Bearer ' + server.capability },
      body: JSON.stringify(request),
    });
    reply = await response.json();
    return reply.error?.code;
  }, { message: 'The workspace writer admitted the test request' }).not.toBe('BUSY');
  return reply;
}

async function send(server, request) {
  const reply = await frame(server, request);
  expect(reply).toMatchObject({ ok: true });
  return reply.result;
}

async function snapshot(server) {
  return send(server, { type: 'snapshot' });
}

async function keyboardActivate(scope, name) {
  const button = control(scope, name);
  await expect(button).toBeEnabled();
  await button.focus();
  await expect(button).toBeFocused();
  await button.press('Enter');
}

async function operation(page, name, action) {
  const response = page.waitForResponse(reply => new URL(reply.url()).pathname === endpoint
    && reply.request().postDataJSON()?.intent?.operation === name);
  const [accepted] = await Promise.all([response, action()]);
  const frame = await accepted.json();
  expect(frame).toMatchObject({ ok: true });
  const request = accepted.request().postDataJSON();
  expect(frame.result.request_id).toBe(request.request_id);
  return { request, outcome: frame.result.outcome, response: frame.result };
}

async function observeSample(page, instrument, { defaultPalette = true } = {}) {
  const chart = page.getByRole('region', { name: 'Price chart' });
  await expect(page.getByText('Simulated', { exact: true })).toBeVisible();
  await expect(chart.getByRole('img', { name: `Candlestick and volume chart for ${instrument}` })).toBeVisible();
  if (defaultPalette) await expect.poll(() => painted(chart, 'candlestick')).toBeGreaterThan(20);
  return chart;
}

async function expectPaintedColor(chart, rgb) {
  await expect.poll(() => chart.locator('.chart canvas').evaluateAll((canvases, rgb) => canvases.some(canvas => {
    if (!canvas.width || !canvas.height) return false;
    const values = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
    let painted = 0;
    for (let i = 0; i < values.length; i += 4) {
      if (rgb.every((channel, k) => Math.abs(values[i + k] - channel) < 5) && ++painted > 20) return true;
    }
    return false;
  }), rgb)).toBe(true);
}

const future = 'SIM.CN.FUTURE.001';
const stock = 'SIM.CN.STOCK.001';
const editor = page => page.getByRole('dialog', { name: 'Edit workspace layout' });
const panel = (page, name) => page.locator(`[data-layout-panel="${name}"]`);
const expectedDefinition = {
  version: 1,
  panels: [
    { panel: 'inspection', dock: 'bottom', size: 205 },
    { panel: 'watchlist', dock: 'left', size: 310 },
  ],
  theme: 'light', gain_color: '#138a61', loss_color: '#b73348',
  link_selection: true, pinned_instrument: future,
};

async function openEditor(page) {
  await keyboardActivate(page, 'Edit layout');
  const dialog = editor(page);
  await expect(dialog).toBeVisible();
  return dialog;
}

async function enterManualLayout(dialog, name) {
  await dialog.getByLabel('Layout name').fill(name);
  await dialog.getByLabel('Watchlist dock').selectOption('left');
  await dialog.getByLabel('Watchlist size (pixels)').fill('310');
  await dialog.getByLabel('Data window dock').selectOption('bottom');
  await dialog.getByLabel('Data window size (pixels)').fill('205');
  await dialog.getByRole('combobox', { name: 'Theme', exact: true }).selectOption('light');
  await dialog.getByLabel('Gain color').fill('#138a61');
  await dialog.getByLabel('Loss color').fill('#b73348');
  await dialog.getByLabel('Link data window to chart').check();
  await dialog.getByLabel('Pin comparison').selectOption(future);
  await keyboardActivate(dialog, 'Move Data window earlier');
}

async function expectManualArrangement(page, chart) {
  const watchlist = panel(page, 'watchlist');
  const inspection = panel(page, 'inspection');
  await expect(watchlist).toBeVisible();
  await expect(inspection.getByRole('table', { name: 'Historical bars' })).toBeVisible();
  await expect(page.locator('[data-layout-theme="light"]')).toBeVisible();
  await expect(page.locator('[data-layout-dock="left"]').locator('[data-layout-panel="watchlist"]')).toBeVisible();
  await expect(page.locator('[data-layout-dock="bottom"]').locator('[data-layout-panel="inspection"]')).toBeVisible();
  const [watchBox, chartBox, dataBox] = await Promise.all([watchlist.boundingBox(), chart.boundingBox(), inspection.boundingBox()]);
  expect(watchBox.x + watchBox.width).toBeLessThanOrEqual(chartBox.x + 1);
  expect(dataBox.y).toBeGreaterThanOrEqual(chartBox.y + chartBox.height - 1);
  expect(watchBox.width).toBeGreaterThan(280);
  expect(watchBox.width).toBeLessThan(335);
  expect(dataBox.height).toBeGreaterThan(175);
  expect(dataBox.height).toBeLessThan(230);
  await expectPaintedColor(chart, [19, 138, 97]);
  await expectPaintedColor(chart, [183, 51, 72]);
}

async function reviewControls(page) {
  await keyboardActivate(page, 'Data sources');
  await expect(control(page, 'Leave sample')).toBeVisible();
  await page.keyboard.press('Escape');
  await keyboardActivate(page, 'Decision notes');
  const notes = page.getByRole('dialog', { name: 'Decision notes' });
  await keyboardActivate(notes, 'New decision note');
  await notes.getByLabel('Decision date').fill('2026-01-03');
  await notes.getByLabel('Thesis').fill('Synthetic layout accessibility check');
  await notes.getByLabel('Choice').fill('Review the sample only');
  await keyboardActivate(notes, 'Review decision note');
  await expect(notes.getByRole('heading', { name: 'Save this decision note locally?' })).toBeVisible();
  await keyboardActivate(notes, 'Cancel action');
  await expect(notes.getByText('No saved decision notes.')).toBeVisible();
  await keyboardActivate(notes, 'Close decision notes');
}

test('previews a real named workspace, retains its linked and pinned panels, and reopens the profile', async ({ browser }, testInfo) => {
  const name = 'Evening desk';
  const recoveredName = 'Recovery desk';
  await withProfile(async profile => {
    let firstWriter;
    let saved;
    let interrupted;
    await withService(profile, async server => {
      firstWriter = server.writerPid;
      await withBrowser(browser, server, async page => {
        await expect(page.getByText('No data source', { exact: true })).toBeVisible();
        await keyboardActivate(page, 'Open offline sample');
        const chart = await observeSample(page, future);
        const before = await snapshot(server);
        const dialog = await openEditor(page);
        await enterManualLayout(dialog, name);
        const preview = await operation(page, 'workspace.previewLayout', () => keyboardActivate(dialog, 'Preview layout'));
        expect(preview.request.scope.expected_workspace_revision).toBe(before.workspace.revision);
        expect(preview.request.intent.arguments.edit).toEqual({ action: 'save', name, definition: expectedDefinition });
        expect(preview.outcome).toMatchObject({ state: 'previewed', result: { layout: { name, definition: expectedDefinition } } });
        expect((await snapshot(server)).layouts).toEqual(before.layouts);
        await expectManualArrangement(page, chart);
        const accepted = await operation(page, 'workspace.applyLayout', () => keyboardActivate(page, 'Apply layout'));
        expect(accepted.request.intent.arguments.preview_token).toBe(preview.outcome.result.preview_token);
        expect(accepted.outcome).toMatchObject({ state: 'committed', result: { name, definition: expectedDefinition },
          receipt: { undoable: true, committed_workspace_revision: before.workspace.revision + 1 } });
        await expect(control(page, 'Apply layout')).toHaveCount(0);

        const beforeHover = await snapshot(server);
        await chart.getByRole('img', { name: /^Candlestick and volume chart for / }).hover();
        const afterHover = await snapshot(server);
        expect(afterHover.workspace.revision).toBe(beforeHover.workspace.revision);
        expect(afterHover.selection_revision).toBe(beforeHover.selection_revision);
        expect(afterHover.layouts).toEqual(beforeHover.layouts);

        const comparison = page.getByRole('region', { name: 'Pinned comparison' });
        await expect(comparison.getByText('CN.FUTURE.001', { exact: true })).toBeVisible();
        const dailyClose = comparison.getByText(/Sample daily close/);
        await expect(dailyClose).toBeVisible();
        const pinnedValue = await dailyClose.textContent();
        await control(panel(page, 'watchlist'), /CN\.STOCK\.001/).press('Enter');
        await observeSample(page, stock, { defaultPalette: false });
        await expect(panel(page, 'inspection')).toContainText('CN.STOCK.001');
        await expect(dailyClose).toHaveText(pinnedValue);
        await reviewControls(page);
        saved = await snapshot(server);
        expect(saved.layouts).toMatchObject({ active: { name, definition: expectedDefinition }, saved: [name] });

        const edit = await openEditor(page);
        await edit.getByLabel('Layout name').fill(recoveredName);
        await operation(page, 'workspace.previewLayout', () => keyboardActivate(edit, 'Preview layout'));
        const actual = Promise.withResolvers();
        let dropFirstApply = true;
        await page.route(`**${endpoint}`, async route => {
          const request = route.request().postDataJSON();
          if (request?.intent?.operation !== 'workspace.applyLayout' || !dropFirstApply) return route.continue();
          dropFirstApply = false;
          const reply = await route.fetch();
          const { result } = await reply.json();
          await route.abort('connectionreset');
          actual.resolve({ request, outcome: result?.outcome });
        });
        await keyboardActivate(page, 'Apply layout');
        await expect(page.getByRole('alert').filter({ hasText: 'The local layout could not be updated.' })).toBeVisible();
        interrupted = await actual.promise;
        expect(interrupted.outcome).toMatchObject({ state: 'committed', result: { name: recoveredName } });
        expect(interrupted.request.scope.idempotency_key).not.toBe(accepted.request.scope.idempotency_key);
        const committedWithoutReply = await snapshot(server);
        expect(committedWithoutReply.workspace.revision).toBe(saved.workspace.revision + 1);

        const clickedRetry = await operation(page, 'workspace.applyLayout', () => keyboardActivate(page, 'Apply layout'));
        expect(clickedRetry.request.scope).toEqual(interrupted.request.scope);
        expect(clickedRetry.request.intent).toEqual(interrupted.request.intent);
        expect(clickedRetry.request.request_id).not.toBe(interrupted.request.request_id);
        expect(clickedRetry.request.message_id).not.toBe(interrupted.request.message_id);
        expect(clickedRetry.outcome).toEqual(interrupted.outcome);
        await expect(control(page, 'Apply layout')).toHaveCount(0);
        await expect(page.getByRole('status').filter({ hasText: `Layout ${recoveredName}` })).toBeVisible();
        expect(await snapshot(server)).toEqual(committedWithoutReply);
        await testInfo.attach('layout-apply-retry-response', { contentType: 'application/json', body: JSON.stringify(clickedRetry.response) });
      });
      await withBrowser(browser, server, async page => {
        await expect(page.getByText('No data source', { exact: true })).toBeVisible();
        await expect(control(page, 'Edit layout')).toHaveCount(0);
      }, { authorized: false });
    });

    await withService(profile, async server => {
      expect(server.writerPid).not.toBe(firstWriter);
      await withBrowser(browser, server, async page => {
        await expect(control(page, 'Edit layout')).toBeEnabled();
        const restored = await snapshot(server);
        expect(restored.workspace.revision).toBe(saved.workspace.revision + 1);
        expect(restored.layouts).toMatchObject({ active: { name: recoveredName, definition: expectedDefinition }, saved: [name, recoveredName] });
        const retried = await send(server, interrupted.request);
        expect(retried.outcome).toEqual(interrupted.outcome);
        expect(await snapshot(server)).toEqual(restored);
        const chart = await observeSample(page, stock, { defaultPalette: false });
        await expectManualArrangement(page, chart);
        const screenshot = testInfo.outputPath('restored-workspace.png');
        await page.screenshot({ path: screenshot });
        await testInfo.attach('restored-workspace', { path: screenshot, contentType: 'image/png' });
        await page.setViewportSize({ width: 480, height: 740 });
        await expect(control(page, 'Edit layout')).toBeInViewport();
        await reviewControls(page);
        await testInfo.attach('reopened-layout', { contentType: 'application/json', body: JSON.stringify({
          writers: [firstWriter, server.writerPid], workspaceRevision: restored.workspace.revision,
          selectionRevision: restored.selection_revision, layout: restored.layouts.active,
          replayedOperation: retried.outcome.receipt.operation_id,
        }) });
      });
    });
  });
});

const tabbedDefinition = {
  ...expectedDefinition,
  panels: [
    { panel: 'inspection', dock: 'right', size: 350 },
    { panel: 'watchlist', dock: 'right', size: 350 },
  ],
};

async function enterDefinition(dialog, name, definition) {
  await dialog.getByLabel('Layout name').fill(name);
  await keyboardActivate(dialog, 'Edit layout definition');
  await dialog.getByRole('textbox', { name: 'Layout definition', exact: true }).fill(JSON.stringify(definition, null, 2));
}

function peerRequest(preview, intent, { target = preview.scope.target, expected = preview.scope.expected_workspace_revision } = {}) {
  return {
    ...preview, request_id: randomUUID(), message_id: randomUUID(), timestamp_ms: Date.now(), intent,
    scope: { ...preview.scope, target, expected_workspace_revision: expected, idempotency_key: randomUUID() },
  };
}

test('keeps chart and layout undo independent after restarting the same profile', async ({ browser }, testInfo) => {
  const definition = { ...expectedDefinition, pinned_instrument: null };
  const chart = { kind: 'chart', object_id: 'main-chart' };
  const layout = { kind: 'layout', object_id: 'workspace-layout' };
  const candidate = receipt => ({
    operation_id: receipt.operation_id, committed_workspace_revision: receipt.committed_workspace_revision,
  });

  async function saveLayout(page, name, revision) {
    const dialog = await openEditor(page);
    await enterDefinition(dialog, name, definition);
    await operation(page, 'workspace.previewLayout', () => keyboardActivate(dialog, 'Preview layout'));
    const { outcome } = await operation(page, 'workspace.applyLayout', () => keyboardActivate(page, 'Apply layout'));
    expect(outcome).toMatchObject({ state: 'committed', result: { name, definition },
      receipt: { target: layout, committed_workspace_revision: revision, undoable: true } });
    await expect(control(page, 'Apply layout')).toHaveCount(0);
    return outcome.receipt;
  }

  async function undoLayout(page, receipt, revision, timeframe) {
    const dialog = await openEditor(page);
    const { request, outcome } = await operation(page, 'workspace.undo', () => keyboardActivate(dialog, 'Undo layout'));
    expect(request.intent.arguments).toEqual({ operation_id: receipt.operation_id });
    expect(request.scope).toMatchObject({ target: layout, expected_workspace_revision: revision - 1, expected_selection_revision: 0 });
    expect(outcome).toMatchObject({ state: 'committed', result: { timeframe },
      receipt: { target: layout, committed_workspace_revision: revision, undoable: false } });
    await expect(dialog).toBeHidden();
  }

  await withProfile(async profile => {
    let chartReceipt;
    let writer;
    await withService(profile, async server => {
      writer = server.writerPid;
      await withBrowser(browser, server, async page => {
        await expect(page.getByText('No data source', { exact: true })).toBeVisible();
        const change = await operation(page, 'chart.setTimeframe', () => keyboardActivate(page, '5m'));
        expect(change.outcome).toMatchObject({ state: 'committed', result: { timeframe: '5m' },
          receipt: { target: chart, committed_workspace_revision: 1, undoable: true } });
        chartReceipt = change.outcome.receipt;
        const firstLayout = await saveLayout(page, 'First layout', 2);
        await undoLayout(page, firstLayout, 3, '5m');
        expect(await snapshot(server)).toMatchObject({ workspace: { revision: 3, timeframe: '5m' },
          layouts: { active: { name: null }, saved: [], undo: null } });
      });
    });

    await withService(profile, async server => {
      expect(server.writerPid).not.toBe(writer);
      await withBrowser(browser, server, async page => {
        await expect(control(page, '5m')).toHaveAttribute('aria-pressed', 'true');
        const restored = await snapshot(server);
        expect(restored).toMatchObject({ workspace: { revision: 3, timeframe: '5m' }, undo: candidate(chartReceipt),
          layouts: { active: { name: null }, undo: null }, selection_revision: 0 });

        const pendingLayout = await saveLayout(page, 'Second layout', 4);
        const before = await snapshot(server);
        expect(before.undo).toEqual(candidate(chartReceipt));
        expect(before.layouts.undo).toEqual(candidate(pendingLayout));
        const undone = await operation(page, 'workspace.undo', () => keyboardActivate(page, 'Undo timeframe change'));
        expect(undone.request.intent.arguments).toEqual({ operation_id: chartReceipt.operation_id });
        expect(undone.request.scope).toMatchObject({ target: chart, expected_workspace_revision: 4, expected_selection_revision: 0 });
        expect(undone.outcome).toMatchObject({ state: 'committed', result: { timeframe: '1m' },
          receipt: { target: chart, committed_workspace_revision: 5, undoable: false } });
        await expect(control(page, '1m')).toHaveAttribute('aria-pressed', 'true');
        const after = await snapshot(server);
        expect(after).toMatchObject({ workspace: { revision: 5, timeframe: '1m' }, undo: null,
          layouts: { active: { name: 'Second layout', definition }, undo: candidate(pendingLayout) } });
        await undoLayout(page, pendingLayout, 6, '1m');
        expect(await snapshot(server)).toMatchObject({ workspace: { revision: 6, timeframe: '1m' }, undo: null,
          layouts: { active: { name: null }, saved: [], undo: null }, selection_revision: 0 });
        await testInfo.attach('independent-undo', { contentType: 'application/json', body: JSON.stringify({
          writers: [writer, server.writerPid], chart: restored.undo, layout: after.layouts.undo,
        }) });
      });
    });
  });
});

test('rejects unsupported bindings and stale applies, then opens, resets and undoes named layouts', async ({ browser }, testInfo) => {
  const baseline = 'Side-by-side';
  const tabbed = 'Reading tabs';
  await withProfile(async profile => withService(profile, async server => {
    let original;
    await withBrowser(browser, server, async page => {
      await keyboardActivate(page, 'Open offline sample');
      await observeSample(page, future);
      let dialog = await openEditor(page);
      await enterManualLayout(dialog, baseline);
      const accepted = await operation(page, 'workspace.previewLayout', () => keyboardActivate(dialog, 'Preview layout'));
      await operation(page, 'workspace.applyLayout', () => keyboardActivate(page, 'Apply layout'));
      await expect(control(page, 'Apply layout')).toHaveCount(0);
      original = await snapshot(server);

      for (const definition of [
        { ...tabbedDefinition, version: 2 },
        { ...tabbedDefinition, panels: [{ panel: 'unknown', dock: 'right', size: 350 }, tabbedDefinition.panels[1]] },
        { ...tabbedDefinition, bindings: { 'watchlist.click': 'network.request' } },
      ]) {
        const invalid = peerRequest(accepted.request, { operation: 'workspace.previewLayout',
          arguments: { edit: { action: 'save', name: 'Rejected definition', definition } },
        }, { expected: original.workspace.revision });
        expect(await frame(server, invalid)).toMatchObject({ ok: false, error: { code: 'INVALID_ARGUMENT' } });
      }
      const unsupported = peerRequest(accepted.request, { operation: 'workspace.previewLayout', arguments: {
        edit: { action: 'save', name: 'Unsupported comparison', definition: { ...tabbedDefinition, pinned_instrument: 'SIM.UNKNOWN' } },
      } }, { expected: original.workspace.revision });
      expect((await send(server, unsupported)).outcome).toMatchObject({ state: 'failed', error: { code: 'UNSUPPORTED_CAPABILITY' } });
      expect((await snapshot(server)).layouts).toEqual(original.layouts);

      dialog = await openEditor(page);
      await enterDefinition(dialog, tabbed, tabbedDefinition);
      const definitionField = dialog.getByRole('textbox', { name: 'Layout definition', exact: true });
      await definitionField.fill('{');
      await keyboardActivate(dialog, 'Preview layout');
      await expect(dialog.getByRole('alert')).toHaveText('The layout definition must be valid JSON.');
      await definitionField.fill(JSON.stringify(tabbedDefinition, null, 2));
      const preview = await operation(page, 'workspace.previewLayout', () => keyboardActivate(dialog, 'Preview layout'));
      expect(preview.outcome).toMatchObject({ state: 'previewed', result: { layout: { name: tabbed, definition: tabbedDefinition } } });
      const concurrent = peerRequest(preview.request, { operation: 'chart.setTimeframe', arguments: { timeframe: '5m' } },
        { target: { kind: 'chart', object_id: 'main-chart' } });
      expect((await send(server, concurrent)).outcome).toMatchObject({ state: 'committed' });
      const stale = await operation(page, 'workspace.applyLayout', () => keyboardActivate(page, 'Apply layout'));
      expect(stale.outcome).toMatchObject({ state: 'failed', error: { code: 'STALE_CONTEXT' } });
      await expect(page.getByRole('alert').filter({ hasText: /workspace changed|changed since/i })).toBeVisible();
      await expect(control(page, 'Edit layout')).toBeEnabled();
      expect((await snapshot(server)).layouts).toEqual(original.layouts);
    });

    await withBrowser(browser, server, async page => {
      await expect(control(page, 'Edit layout')).toBeEnabled();
      let dialog = await openEditor(page);
      await enterDefinition(dialog, tabbed, tabbedDefinition);
      await operation(page, 'workspace.previewLayout', () => keyboardActivate(dialog, 'Preview layout'));
      const committed = await operation(page, 'workspace.applyLayout', () => keyboardActivate(page, 'Apply layout'));
      expect(committed.outcome).toMatchObject({ state: 'committed', result: { name: tabbed, definition: tabbedDefinition } });
      await expect(control(page, 'Apply layout')).toHaveCount(0);
      const dock = page.locator('[data-layout-dock="right"]');
      const tabs = dock.getByRole('tab');
      await expect(tabs).toHaveText(['Data window', 'Watchlist']);
      const data = dock.getByRole('tab', { name: 'Data window', exact: true });
      await data.focus();
      await data.press('ArrowRight');
      await expect(dock.getByRole('tab', { name: 'Watchlist', exact: true })).toBeFocused();
      await expect(dock.locator('[data-layout-panel="watchlist"]')).toBeVisible();

      dialog = await openEditor(page);
      const reset = await operation(page, 'workspace.previewLayout', () => keyboardActivate(dialog, 'Preview default layout'));
      expect(reset.request.intent.arguments.edit).toEqual({ action: 'reset' });
      expect(reset.outcome).toMatchObject({ state: 'previewed', result: { layout: { name: null } } });
      await operation(page, 'workspace.applyLayout', () => keyboardActivate(page, 'Apply layout'));
      await expect(control(page, 'Apply layout')).toHaveCount(0);
      const resetState = await snapshot(server);
      expect(resetState.layouts).toMatchObject({ active: { name: null }, saved: [tabbed, baseline] });

      dialog = await openEditor(page);
      const undone = await operation(page, 'workspace.undo', () => keyboardActivate(dialog, 'Undo layout'));
      expect(undone.outcome).toMatchObject({ state: 'committed', receipt: { undoable: false } });
      await expect(tabs).toHaveText(['Data window', 'Watchlist']);
      const undoState = await snapshot(server);
      expect(undoState.layouts).toMatchObject({ active: { name: tabbed, definition: tabbedDefinition }, undo: null });

      if (!await editor(page).isVisible()) await openEditor(page);
      dialog = editor(page);
      await dialog.getByRole('combobox', { name: 'Saved layout', exact: true }).selectOption(baseline);
      const open = await operation(page, 'workspace.previewLayout', () => keyboardActivate(dialog, 'Preview saved layout'));
      expect(open.request.intent.arguments.edit).toEqual({ action: 'open', name: baseline });
      await operation(page, 'workspace.applyLayout', () => keyboardActivate(page, 'Apply layout'));
      await expect(control(page, 'Apply layout')).toHaveCount(0);
      const state = await snapshot(server);
      expect(state.layouts).toMatchObject({ active: { name: baseline, definition: expectedDefinition } });
      await testInfo.attach('revision-guard-and-reset', { contentType: 'application/json', body: JSON.stringify({
        stale: 'STALE_CONTEXT', before: original.workspace.revision,
        reset: resetState.workspace.revision, afterUndo: undoState.workspace.revision, final: state.workspace.revision,
      }) });
    });
  }));
});

test('reviews changed definitions and retires an apply after a real authorization denial', async ({ browser }, testInfo) => {
  const name = 'Edited desk';
  const definition = { ...expectedDefinition, pinned_instrument: null };
  await withProfile(profile => withService(profile, server => withBrowser(browser, server, async page => {
    let dialog = await openEditor(page);
    const original = await snapshot(server);
    await enterDefinition(dialog, 'First draft', original.layouts.active.definition);
    const first = await operation(page, 'workspace.previewLayout', () => keyboardActivate(dialog, 'Preview layout'));
    expect(first.outcome).toMatchObject({ state: 'previewed', result: { layout: { name: 'First draft' } } });

    await keyboardActivate(page, 'Edit preview');
    await expect(dialog).toBeVisible();
    await dialog.getByLabel('Layout name').fill(name);
    await dialog.getByRole('textbox', { name: 'Layout definition', exact: true }).fill(JSON.stringify(definition, null, 2));
    const changed = await operation(page, 'workspace.previewLayout', () => keyboardActivate(dialog, 'Preview layout'));
    expect(changed.outcome).toMatchObject({ state: 'previewed', result: { layout: { name, definition } } });
    expect(changed.outcome.result.preview_token).not.toBe(first.outcome.result.preview_token);
    expect(await snapshot(server)).toEqual(original);

    const isApply = request => request.postDataJSON()?.intent?.operation === 'workspace.applyLayout';
    let denyFirst = true;
    await page.route(`**${endpoint}`, async route => {
      const request = route.request();
      if (!denyFirst || !isApply(request)) return route.continue();
      denyFirst = false;
      const headers = await request.allHeaders();
      return route.continue({ headers: { ...headers, authorization: 'Bearer ' + '0'.repeat(64) } });
    });
    const [denied] = await Promise.all([
      page.waitForResponse(reply => new URL(reply.url()).pathname === endpoint && isApply(reply.request())),
      keyboardActivate(page, 'Apply layout'),
    ]);
    const denial = await denied.json();
    const rejected = denied.request().postDataJSON();
    expect(denied.status()).toBe(403);
    expect(denial).toMatchObject({ ok: false, error: { code: 'PERMISSION_DENIED' } });
    expect(rejected.intent.arguments.preview_token).toBe(changed.outcome.result.preview_token);
    await expect(page.getByRole('alert').filter({ hasText: /layout action.*authorized/i })).toBeVisible();
    await expect(control(page, 'Apply layout')).toHaveCount(0);
    expect(await snapshot(server)).toEqual(original);

    dialog = await openEditor(page);
    await enterDefinition(dialog, name, definition);
    const reviewed = await operation(page, 'workspace.previewLayout', () => keyboardActivate(dialog, 'Preview layout'));
    const fresh = await operation(page, 'workspace.applyLayout', () => keyboardActivate(page, 'Apply layout'));
    expect(fresh.request.scope.idempotency_key).not.toBe(rejected.scope.idempotency_key);
    expect(fresh.request.intent.arguments.preview_token).toBe(reviewed.outcome.result.preview_token);
    expect(fresh.outcome).toMatchObject({ state: 'committed', result: { name, definition },
      receipt: { committed_workspace_revision: original.workspace.revision + 1 } });
    await expect(control(page, 'Apply layout')).toHaveCount(0);
    expect((await snapshot(server)).layouts).toMatchObject({ active: { name, definition }, saved: [name] });
    await testInfo.attach('layout-authorization-denial', { contentType: 'application/json', body: JSON.stringify(denial) });
  })));
});

async function withPublishedCsv(use) {
  const temp = await mkdtemp(join(tmpdir(), 'scopehub-layout-source-'));
  const app = join(temp, 'app');
  const folder = join(temp, 'folder');
  const binary = join(root, 'target/debug/examples', windows ? 'local_import_bridge.exe' : 'local_import_bridge');
  const csv = 'Date,Symbol,Open,High,Low,Close,Volume\n2026-02-03,FIX,30,34,29,31.5,120\n2026-02-04,FIX,47,49,40,44.25,110\n';
  const throughDesktop = async use => {
    const bridge = startLocalImportBridge(binary, app, folder);
    try {
      await use(bridge, async (operation, payload) => {
        const reply = await bridge.request('execute_operation', { operation: `market.${operation}`, requestId: randomUUID(), payload });
        expect(reply.error).toBeUndefined();
        return reply.result;
      });
    } finally { await bridge.close(); }
  };
  try {
    await mkdir(app, { mode: 0o700 });
    await mkdir(folder, { mode: 0o700 });
    await writeFile(join(folder, 'layout-source.csv'), csv);
    let revision;
    await throughDesktop(async (bridge, market) => {
      const { result: selected } = await bridge.request('choose_local_csv_folder', {});
      const input = {
        version: 1, folder_id: selected.folder_id, file_id: selected.files[0].file_id,
        expected_sha256: createHash('sha256').update(csv).digest('hex'),
      };
      const mapping = {
        version: 1, revision: 'layout-source-fixture', encoding: 'utf-8', delimiter: ',',
        instrument_kind: 'equity', venue: 'FIXTURE', timeframe: '1d', calendar_id: 'fixture-calendar',
        session_scope: 'regular', timezone: 'UTC', quantity_unit: 'shares', price_adjustment: 'raw',
        record_kind: 'native_ohlcv', volume_semantics: 'per_bar', timestamp_convention: 'bar_start',
        timestamp_format: '%Y-%m-%d', trading_day_format: '%Y-%m-%d', daily_start_time: '00:00:00',
        columns: { symbol: 'Symbol', timestamp: 'Date', trading_day: 'Date', open: 'Open', high: 'High', low: 'Low', close: 'Close', volume: 'Volume' },
      };
      await market('previewLocalImport', { ...input, row_limit: 10, mapping });
      const { record } = await market('saveLocalImportMapping', { ...input, row_limit: 10, mapping });
      ({ revision } = await market('publishLocalImport', { ...input, review_revision: record.review_revision }));
    });
    const profile = readPublishedWorkspaceProfile(app);
    const revoke = () => throughDesktop(async (_bridge, market) => {
      const result = await market('revokeLocalImportRevision', { version: 1, revision_id: revision.revision_id });
      expect(result.revision_id).toBe(revision.revision_id);
    });
    await use({ profile, revision, revoke });
  } finally { await rm(temp, { recursive: true, force: true }); }
}

async function expectFrozenChart(page) {
  const chart = page.getByRole('region', { name: 'Price chart' });
  await expect(chart.getByRole('img', { name: /Candlestick and volume chart/ })).toBeVisible();
  await expect(chart.getByLabel('Selected bar')).toContainText('44.25');
  await expect.poll(() => painted(chart, 'candlestick')).toBeGreaterThan(20);
  await expect(page.getByText('Simulated', { exact: true })).toHaveCount(0);
}

function visibleCandleColumns(canvas) {
  const { width, height } = canvas;
  const pixels = canvas.getContext('2d').getImageData(0, 0, width, height).data;
  const count = new Uint16Array(width);
  for (let i = 0; i < pixels.length; i += 4) {
    const [r, g, b] = [pixels[i], pixels[i + 1], pixels[i + 2]];
    if ((r === 67 && g === 212 && b === 172) || (r === 241 && g === 125 && b === 133)) {
      count[(i / 4) % width]++;
    }
  }
  // The price guide and crosshair add at most a few pixels per column.
  return Array.from(count, (pixels, column) => pixels > 12 ? column : -1).filter(column => column >= 0);
}

test('reopens a frozen local source and retains the named layout through disconnection and revocation', async ({ browser }, testInfo) => {
  const name = 'Frozen research';
  await withPublishedCsv(async ({ profile, revision, revoke }) => {
    let accepted;
    let layer;
    let artifact;
    const output = () => ({ type: 'indicator_accepted_output', request_id: randomUUID(), layer_id: layer.layer_id });
    const layers = () => ({ type: 'indicator_accepted_layers', request_id: randomUUID(), revision_id: revision.revision_id });
    const priceChart = page => page.getByRole('region', { name: 'Price chart' });
    const showIndicators = async page => {
      const chart = priceChart(page);
      await chart.getByText('Indicators', { exact: true }).click();
      return chart.getByRole('region', { name: 'Indicator preview' });
    };
    const writers = [];
    await withService(profile, async server => {
      writers.push(server.writerPid);
      await withBrowser(browser, server, async page => {
        await expect(control(page, 'Edit layout')).toBeEnabled();
        const dialog = await openEditor(page);
        const definition = { ...tabbedDefinition, theme: 'dark', gain_color: '#43d4ac', loss_color: '#f17d85', pinned_instrument: null };
        await enterDefinition(dialog, name, definition);
        await operation(page, 'workspace.previewLayout', () => keyboardActivate(dialog, 'Preview layout'));
        await operation(page, 'workspace.applyLayout', () => keyboardActivate(page, 'Apply layout'));
        await expect(control(page, 'Apply layout')).toHaveCount(0);

        await keyboardActivate(page, 'Open published CSV');
        const open = page.getByRole('dialog', { name: 'Open published CSV' });
        await open.getByLabel('Revision ID').fill(revision.revision_id);
        await keyboardActivate(open, 'Open frozen chart');
        await expect(open.getByRole('region', { name: 'Frozen local CSV chart' })).toBeVisible();
        await keyboardActivate(open, 'Use as workspace source');
        await expect(open).toBeHidden();
        await expectFrozenChart(page);

        const beforeSelection = await snapshot(server);
        const data = panel(page, 'inspection');
        const firstBar = 'Select bar at 2026-02-03T00:00:00.000Z';
        const lastBar = 'Select bar at 2026-02-04T00:00:00.000Z';
        const firstRequest = page.waitForRequest(request => new URL(request.url()).pathname === endpoint
          && request.postDataJSON()?.operation === 'workspace.selectEvidence');
        await keyboardActivate(data, firstBar);
        const queued = (await firstRequest).postDataJSON();
        await expect(control(data, firstBar)).toHaveAttribute('aria-pressed', 'true');
        const chart = page.getByRole('region', { name: 'Price chart' });
        await expect(chart.getByLabel('Selected bar')).toContainText('31.5');
        await chart.getByRole('img', { name: /^Candlestick and volume chart for / }).hover();
        await expect(control(data, firstBar)).toHaveAttribute('aria-pressed', 'true');
        const hovered = await snapshot(server);
        expect(hovered.workspace.revision).toBe(beforeSelection.workspace.revision);
        expect(hovered.selection_revision).toBe(beforeSelection.selection_revision + 1);
        await keyboardActivate(data, lastBar);
        await expect(control(data, lastBar)).toHaveAttribute('aria-pressed', 'true');
        await expect.poll(async () => (await snapshot(server)).selection_revision).toBe(beforeSelection.selection_revision + 2);
        const staleSelection = await send(server, { ...queued, request_id: randomUUID(), message_id: randomUUID() });
        expect(staleSelection.error.code).toBe('STALE_CONTEXT');

        const arrangement = await openEditor(page);
        await arrangement.getByLabel('Layout name').fill('Unapplied comparison');
        await operation(page, 'workspace.previewLayout', () => keyboardActivate(arrangement, 'Preview layout'));
        const canvas = await chart.locator('.chart canvas').first().elementHandle();
        await expect.poll(() => canvas.evaluate(visibleCandleColumns)).not.toHaveLength(0);
        const previousPlot = await canvas.evaluate(visibleCandleColumns);
        await keyboardActivate(page, 'Data sources');
        await keyboardActivate(page, 'Open published CSV');
        await open.getByLabel('Revision ID').fill(revision.revision_id);
        await keyboardActivate(open, 'Open frozen chart');
        await expect(open.getByRole('region', { name: 'Frozen local CSV chart' })).toBeVisible();
        await keyboardActivate(open, 'Close');
        await expect(open).toBeHidden();
        await keyboardActivate(page, 'Data sources');
        await expect(page.getByText('Unapplied comparison', { exact: true })).toBeVisible();
        await expect(control(data, lastBar)).toHaveAttribute('aria-pressed', 'true');
        expect(await canvas.evaluate(element => element.isConnected)).toBe(true);
        await expect.poll(() => canvas.evaluate(visibleCandleColumns)).toEqual(previousPlot);
        await keyboardActivate(page, 'Discard preview');

        const indicator = await showIndicators(page);
        const ready = page.waitForResponse(async response => {
          if (response.request().postDataJSON()?.type !== 'indicator_preview_status') return false;
          return (await response.json()).result?.status === 'ready';
        });
        await keyboardActivate(indicator, 'Run preview');
        const preview = (await (await ready).json()).result.event;
        expect(preview.artifact.result.series[0].points.map(point => point.value)).toEqual([null, 37.875]);
        await keyboardActivate(indicator, 'Accept preview');
        const confirmation = page.waitForResponse(response =>
          response.request().postDataJSON()?.operation === 'confirmation.respond');
        await keyboardActivate(indicator, 'Confirm indicator acceptance');
        const committed = (await (await confirmation).json()).result.outcome;
        expect(committed.state).toBe('committed');
        layer = committed.result;
        await expect(indicator.getByRole('region', { name: 'Accepted indicators' }).getByText(layer.version_id)).toBeVisible();
        artifact = await send(server, output());
        expect(artifact.document.artifact).toEqual(preview.artifact);

        accepted = await snapshot(server);
        expect(accepted.layouts).toMatchObject({ active: { name, definition } });
        expect(accepted.workspace.revision).toBe(beforeSelection.workspace.revision + 1);
        expect(accepted.selection_revision).toBe(beforeSelection.selection_revision + 2);
      });
    });

    await withService(profile, async server => {
      writers.push(server.writerPid);
      let disconnected = 0;
      await withBrowser(browser, server, async page => {
        await expect(page.getByText('Source unavailable', { exact: true })).toBeVisible();
        await expect(page.getByText(revision.revision_id, { exact: true })).toBeVisible();
        await expect(page.getByText(name, { exact: true })).toBeVisible();
        await expect(page.getByRole('region', { name: 'Price chart' }).locator('.chart canvas')).toHaveCount(0);
        await expect(page.getByText('Simulated', { exact: true })).toHaveCount(0);
        await expect(control(page, 'Edit layout')).toBeEnabled();
        expect(disconnected).toBeGreaterThan(0);
        await expect(priceChart(page).getByRole('region', { name: 'Accepted indicators' })).toHaveCount(0);
        expect(await send(server, output())).toEqual(artifact);
        expect((await snapshot(server)).layouts).toEqual(accepted.layouts);
      }, { setup: page => page.route(`**${endpoint}`, route => {
        if (route.request().postDataJSON()?.type !== 'frozen_chart') return route.continue();
        disconnected += 1;
        return route.abort('internetdisconnected');
      }) });

      await withBrowser(browser, server, async page => {
        let reruns = 0;
        page.on('request', request => { if (request.postDataJSON()?.intent?.operation === 'indicator.runPreview') reruns++; });
        await expectFrozenChart(page);
        const indicator = await showIndicators(page);
        const saved = indicator.getByRole('region', { name: 'Accepted indicators' });
        await expect(saved.getByText(layer.version_id)).toBeVisible();
        await saved.getByText('Accepted provenance and values').click();
        await expect(saved.getByText('mean · Unknown, 37.875')).toBeVisible();
        expect(await send(server, output())).toEqual(artifact);
        expect(reruns).toBe(0);
        expect((await snapshot(server)).layouts).toEqual(accepted.layouts);
      });
    });

    await revoke();
    await withService(profile, async server => {
      writers.push(server.writerPid);
      await withBrowser(browser, server, async page => {
        await expect(page.getByText('Access revoked', { exact: true })).toBeVisible();
        await expect(page.getByText(revision.revision_id, { exact: true })).toBeVisible();
        await expect(page.getByText(name, { exact: true })).toBeVisible();
        await expect(page.getByRole('region', { name: 'Price chart' }).locator('.chart canvas')).toHaveCount(0);
        await expect(page.getByText('Simulated', { exact: true })).toHaveCount(0);
        await expect(control(page, 'Edit layout')).toBeEnabled();
        const state = await snapshot(server);
        expect(state.layouts).toEqual(accepted.layouts);
        expect(state.workspace.revision).toBe(accepted.workspace.revision);
        expect(state.selection_revision).toBe(accepted.selection_revision);
        expect(state.chart_evidence).toEqual({ unavailable: true });
        const denied = await frame(server, output());
        expect(denied).toMatchObject({ ok: false, error: { code: 'PERMISSION_DENIED' } });
        expect(denied.result).toBeUndefined();
        const references = (await send(server, layers())).layers;
        expect(references).toEqual([{ ...layer, available: false }]);
        await testInfo.attach('source-restoration', { contentType: 'application/json', body: JSON.stringify({
          writers, revision: revision.revision_id, layout: state.layouts.active.name,
          indicator: { version: layer.version_id, resultHash: artifact.document.artifact.result_hash,
            unavailableReference: references[0], readDenied: denied.error.code },
          observed: ['published', 'disconnected', 'reconnected', 'revoked'],
        }) });
      });
    });
    expect(new Set(writers).size).toBe(writers.length);
  });
});
