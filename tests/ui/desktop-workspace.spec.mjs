import { execFile } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import { randomUUID } from 'node:crypto';
import { expect, test } from '@playwright/test';
import { buildPreview, preAdmissionBusy, seedDisplayOnlyChart, seedPublishedChart, withProfile, withWorkspaceTransport } from './ai-workflow-fixture.mjs';
import { startLocalImportBridge } from './local-import-bridge.mjs';
import { control, leaveSample, openPanel } from './workspace-controls.mjs';

const workspaceId = 'main';
const operationId = 'ce7fd52d-7a62-441f-897f-098098e6fe82';
const chart = { kind: 'chart', object_id: 'main-chart' };
const undo = { operation_id: operationId, committed_workspace_revision: 1 };

function revisionStatus(dispatch, revisionId, requestId = randomUUID()) {
  return dispatch({ type: 'frozen_chart_status', request_id: requestId, revision_id: revisionId });
}

function snapshotOutcome({ status, body }) {
  return {
    status, ok: body?.ok ?? null, code: body?.error?.code ?? null, retryable: body?.error?.retryable ?? null,
    hasResult: Object.hasOwn(body ?? {}, 'result'), hasError: Object.hasOwn(body ?? {}, 'error'),
    selection: body?.result?.selection_revision ?? null, source: body?.result?.source ?? null,
  };
}

async function readProofSnapshot(dispatchResponse, observe) {
  let response;
  let outcome;
  await expect.poll(async () => {
    try {
      response = await dispatchResponse({ type: 'snapshot' });
    } catch (error) {
      observe({ transportError: error.name, code: error.code ?? error.cause?.code ?? null });
      throw error;
    }
    outcome = snapshotOutcome(response);
    const refused = !outcome.hasResult && preAdmissionBusy(response);
    observe({ ...outcome, refused });
    return !refused;
  }, { timeout: 4_000, message: 'The verified state read must reach a terminal snapshot reply' }).toBe(true);
  expect(outcome.hasError && (outcome.ok === true || outcome.hasResult),
    'The independent proof received a contradictory workspace snapshot').toBe(false);
  expect(outcome, 'The independent proof must return a successful workspace snapshot').toMatchObject({
    status: 200, ok: true, hasResult: true, hasError: false, selection: expect.any(Number),
  });
  return response.body.result;
}

function workspace(timeframe, revision) {
  return {
    id: workspaceId, revision, timeframe, style: 'candlestick',
    instrument_id: null, dataset: null, visible_range: null, layers: [],
  };
}

function ready(timeframe, revision, candidate = null) {
  return { workspace: workspace(timeframe, revision), selection_revision: 0, undo: candidate };
}

function response(request, outcome, requestId = request.request_id) {
  return {
    protocol_version: '1.1', type: 'response', message_id: randomUUID(),
    timestamp_ms: Date.now(), request_id: requestId, outcome,
  };
}

function committed(operation, result, id, undoable) {
  return {
    state: 'committed', operation, result,
    receipt: {
      operation_id: id, workspace_id: workspaceId, target: chart,
      committed_workspace_revision: result.revision, undoable,
    },
  };
}

async function nativeTransport(page, { optedIn, native = true, textModel = false, observeFrozenRevision }, use) {
  const calls = [];
  await page.exposeBinding('dispatchNativeWorkspace', async (_, command, args) => {
    calls.push({ command, args });
    expect(command).toBe('execute_operation');
    expect(Object.keys(args).sort()).toEqual(args.operation === 'market.listLocalImportRevisions'
      ? ['operation', 'payload', 'requestId'] : ['operation', 'payload']);
    return use(args);
  });
  await page.addInitScript(({ optedIn, native, textModel, observeFrozenRevision }) => {
    window.isTauri = native;
    if (optedIn) Object.defineProperty(window, '__SCOPEHUB_DESKTOP_WORKSPACE__', { value: true });
    if (textModel) Object.defineProperty(window, '__SCOPEHUB_DESKTOP_TEXT_MODEL__', { value: true });
    window.__TAURI_INTERNALS__ = {
      async invoke(command, args) {
        const reply = await window.dispatchNativeWorkspace(command, args);
        if (reply.error) throw reply.error;
        if (args.payload.type === 'frozen_chart' && args.payload.revision_id === observeFrozenRevision) {
          (window.receivedFrozenReplies ??= []).push(args.payload.request_id);
        }
        return reply.result;
      },
    };
  }, { optedIn, native, textModel, observeFrozenRevision });
  return calls;
}

function observeWorkspaceNetwork(page, baseURL) {
  const unexpected = [];
  page.on('request', request => {
    const url = new URL(request.url());
    if (url.origin !== baseURL || url.pathname.startsWith('/__scopehub/')) unexpected.push(url.href);
  });
  return unexpected;
}

function expectMutation(args, name, revision, values) {
  expect(args.operation).toBe(name);
  expect(args.payload.intent).toEqual({ operation: name, arguments: values });
  expect(args.payload.scope).toEqual({
    workspace_id: workspaceId, target: chart, expected_workspace_revision: revision,
    expected_selection_revision: 0, idempotency_key: expect.stringMatching(/^[\da-f-]{36}$/),
  });
  expect(args.payload).toMatchObject({
    protocol_version: '1.1', type: 'request', request_id: expect.any(String),
    message_id: expect.any(String), task_id: null,
  });
  expect(args.payload.message_id).not.toBe(args.payload.request_id);
}

for (const mode of [
  { name: 'unconfigured native', native: true, optedIn: false },
  { name: 'ordinary browser with an injected marker', native: false, optedIn: true },
]) {
  test(`${mode.name} has no startup or timeframe IPC`, async ({ page, baseURL }) => {
    const calls = await nativeTransport(page, mode, () => { throw new Error('unexpected IPC'); });
    const network = observeWorkspaceNetwork(page, baseURL);
    await page.goto('/');
    await expect(control(page, 'Open offline sample')).toBeVisible();
    await expect(control(page, '1m')).toHaveAttribute('aria-pressed', 'true');
    await expect(control(page, 'Undo timeframe change')).toHaveCount(0);
    await control(page, '5m').click();
    await expect(control(page, '5m')).toHaveAttribute('aria-pressed', 'true');
    expect(calls).toEqual([]);
    expect(network).toEqual([]);
  });
}

test('opted-in desktop uses a distinct bootstrap and commits and undoes through named 1.1 IPC', async ({ page, baseURL }) => {
  let current = ready('1m', 0);
  const admitted = Promise.withResolvers();
  const release = Promise.withResolvers();
  const calls = await nativeTransport(page, { optedIn: true }, async args => {
    if (args.operation === null) {
      expect(args.payload).toEqual({ type: 'snapshot' });
      return { result: current };
    }
    if (args.operation === 'chart.setTimeframe') {
      expectMutation(args, 'chart.setTimeframe', 0, { timeframe: '5m' });
      admitted.resolve();
      await release.promise;
      current = ready('5m', 1, undo);
      return { result: response(args.payload, committed(args.operation, current.workspace, operationId, true)) };
    }
    expectMutation(args, 'workspace.undo', 1, { operation_id: operationId });
    current = ready('1m', 2);
    return { result: response(args.payload, committed(args.operation, current.workspace, randomUUID(), false)) };
  });
  const network = observeWorkspaceNetwork(page, baseURL);
  await page.goto('/');
  await expect(control(page, '1m')).toHaveAttribute('aria-pressed', 'true');
  await expect(control(page, 'Undo timeframe change')).toBeDisabled();
  try {
    await control(page, '5m').click();
    await admitted.promise;
    await expect(control(page, '1m')).toHaveAttribute('aria-pressed', 'true');
    await expect(control(page, '5m')).toBeDisabled();
  } finally {
    release.resolve();
  }
  await expect(control(page, '5m')).toHaveAttribute('aria-pressed', 'true');
  await page.reload();
  await expect(control(page, '5m')).toHaveAttribute('aria-pressed', 'true');
  const undoButton = control(page, 'Undo timeframe change');
  await expect(undoButton).toBeEnabled();
  await undoButton.focus();
  await page.keyboard.press('Enter');
  await expect(control(page, '1m')).toHaveAttribute('aria-pressed', 'true');
  await expect(undoButton).toBeDisabled();
  expect(calls.map(call => call.args.operation)).toEqual([null, 'chart.setTimeframe', null, 'workspace.undo']);
  expect(network).toEqual([]);
});

for (const fault of [
  { name: 'refreshes a denied source', code: 'SNAPSHOT_UNAVAILABLE', revoked: true },
  { name: 'retries a transient worker failure', code: 'WORKER_EXITED', revoked: false },
]) {
  test(`restoring a saved CSV ${fault.name}`, async ({ page }) => {
    const revisionId = randomUUID();
    const from = Date.parse('2026-02-03T00:00:00Z');
    const selection = { revision_id: revisionId, range: { from_ms: from, to_ms: from + 1 } };
    let chartRead = false;
    const calls = await nativeTransport(page, { optedIn: true }, ({ operation, payload }) => {
      if (operation === 'market.listLocalImportRevisions') {
        return { result: { revisions: [], next_after_revision_id: null } };
      }
      expect(operation).toBeNull();
      if (payload.type === 'snapshot') {
        const revoked = fault.revoked && chartRead;
        return { result: {
          ...ready('1m', 0), selection_revision: 2,
          source: { kind: 'frozen', revision_id: revisionId, status: revoked ? 'revoked' : 'available' },
          chart_evidence: revoked ? { unavailable: true } : selection,
        } };
      }
      expect(payload).toMatchObject({ type: 'frozen_chart', revision_id: revisionId });
      chartRead = true;
      return { error: { code: fault.code } };
    });

    await page.goto('/');
    const chart = page.getByRole('region', { name: 'Price chart' });
    const retry = control(chart, 'Retry source');
    if (fault.revoked) {
      await expect(chart.getByText('Access to this published CSV was revoked.')).toBeVisible();
      await expect(retry).toHaveCount(0);
      await control(chart, 'Open published CSV').click();
      const dialog = page.getByRole('dialog', { name: 'Open published CSV' });
      await expect(dialog.getByText('The shared selection is unavailable.')).toBeVisible();
      expect(calls.slice(0, 3).map(({ args }) => args.payload.type)).toEqual(['snapshot', 'frozen_chart', 'snapshot']);
    } else {
      await expect(chart.getByText('The published CSV is unavailable.')).toBeVisible();
      await retry.click();
      await expect(chart.getByText('The published CSV is unavailable.')).toBeVisible();
      expect(calls.map(({ args }) => args.payload.type)).toEqual(['snapshot', 'frozen_chart', 'frozen_chart']);
    }
  });
}

function restoreSnapshot(id, status = 'available') {
  return {
    ...ready('1m', 0), selection_revision: 2,
    source: { kind: 'frozen', revision_id: id, status },
    chart_evidence: status === 'available'
      ? { revision_id: id, range: { from_ms: 1, to_ms: 2 } } : { unavailable: true },
    layouts: {
      active: { name: null, definition: {
        version: 1, panels: [], theme: 'dark', gain_color: '#138a61', loss_color: '#b73348',
        link_selection: true, pinned_instrument: null,
      } },
      saved: [], undo: null,
    },
  };
}

test('frozen access refresh waits for a held layout preview', async ({ page }) => {
  const id = randomUUID();
  const denyChart = Promise.withResolvers();
  const layoutStarted = Promise.withResolvers();
  const releaseLayout = Promise.withResolvers();
  let snapshots = 0;
  await nativeTransport(page, { optedIn: true }, async ({ operation, payload }) => {
    if (operation === 'workspace.previewLayout') {
      layoutStarted.resolve();
      await releaseLayout.promise;
      return { error: { code: 'INVALID_ARGUMENT' } };
    }
    if (payload.type === 'snapshot') return { result: restoreSnapshot(id, ++snapshots === 1 ? 'available' : 'revoked') };
    expect(payload).toMatchObject({ type: 'frozen_chart', revision_id: id });
    await denyChart.promise;
    return { error: { code: 'SNAPSHOT_UNAVAILABLE' } };
  });
  try {
    await page.goto('/');
    await control(page, 'Edit layout').click();
    const dialog = page.getByRole('dialog', { name: 'Edit workspace layout' });
    await control(dialog, 'Preview default layout').click();
    await layoutStarted.promise;
    denyChart.resolve();
    await expect(page.locator('.connection')).toHaveText('Source unavailable');
    await expect(page.getByText('The shared selection is unavailable.')).toBeVisible();
    expect(snapshots).toBe(1);
    releaseLayout.resolve();
    await control(dialog, 'Close').click();
    await expect(page.getByText('Access to this published CSV was revoked.')).toBeVisible();
    expect(snapshots).toBe(2);
    await expect(page.getByText('The shared selection is unavailable.')).toBeVisible();
  } finally {
    denyChart.resolve();
    releaseLayout.resolve();
  }
});

test('frozen access refresh releases a silent native reply and ignores its late result', async ({ page }) => {
  const id = randomUUID();
  const waiting = Promise.withResolvers();
  const late = Promise.withResolvers();
  let snapshots = 0;
  await nativeTransport(page, { optedIn: true }, async ({ payload }) => {
    if (payload.type === 'snapshot') {
      if (++snapshots === 1) return { result: restoreSnapshot(id) };
      if (snapshots === 2) {
        waiting.resolve();
        return late.promise;
      }
      return { result: restoreSnapshot(id, 'revoked') };
    }
    expect(payload).toMatchObject({ type: 'frozen_chart', revision_id: id });
    return { error: { code: 'SNAPSHOT_UNAVAILABLE' } };
  });
  await page.clock.install();
  try {
    await page.goto('/');
    await waiting.promise;
    await page.clock.fastForward(16_000);
    await expect(page.getByText('Loading local workspace…')).toHaveCount(0);
    await expect(page.getByText('The shared selection is unavailable.')).toBeVisible();
    await control(page, 'Reload workspace state').click();
    await expect(page.getByText('Access to this published CSV was revoked.')).toBeVisible();
    late.resolve({ result: restoreSnapshot(id) });
    await page.clock.runFor(50);
    await expect(page.getByText('Access to this published CSV was revoked.')).toBeVisible();
    expect(snapshots).toBe(3);
  } finally {
    late.resolve({ result: restoreSnapshot(id) });
  }
});

test('a briefly missing live frozen revision can recover before a confirmed revocation', async ({ page }) => {
  await buildPreview();
  await withProfile(async profile => {
    const id = randomUUID();
    await seedDisplayOnlyChart(profile, id, {
      time_ms: Date.parse('2026-02-03T00:00:00Z'), trading_day: '2026-02-03', open: 73, high: 75, low: 72, close: 74,
      volume: 13, is_closed: true,
    });
    await withWorkspaceTransport(profile, async ({ dispatch }) => {
      let state = 'available';
      let chartReads = 0;
      await nativeTransport(page, { optedIn: true }, ({ payload }) => {
        if (payload.type === 'snapshot') return { result: restoreSnapshot(id) };
        if (payload.type === 'frozen_chart_status') {
          return { result: state === 'revoked' ? { available: false, revoked: true } : { available: state === 'available' } };
        }
        expect(payload).toMatchObject({ type: 'frozen_chart', revision_id: id });
        chartReads += 1;
        return dispatch(payload);
      });
      await page.clock.install();
      await page.goto('/');
      const chart = page.getByRole('region', { name: 'Price chart' });
      const plotted = chart.getByRole('img', { name: /Candlestick and volume chart/ });
      await expect(plotted).toBeVisible();
      state = 'missing';
      await page.clock.fastForward(2200);
      await expect(chart.getByText('The published CSV is unavailable.')).toBeVisible();
      await expect(plotted).toHaveCount(0);
      state = 'available';
      await page.clock.fastForward(2200);
      await expect(plotted).toBeVisible();
      expect(chartReads).toBeGreaterThan(1);
      state = 'revoked';
      await page.clock.fastForward(2200);
      await expect(chart.getByText('Access to this published CSV was revoked.')).toBeVisible();
      state = 'available';
      await page.clock.fastForward(2200);
      await expect(plotted).toHaveCount(0);
    });
  });
});

test('an outstanding native source invoke needs revision proof and a late reply stays inert', async ({ page, browser, baseURL }, testInfo) => {
  test.setTimeout(180_000);
  await buildPreview();
  const contextCount = browser.contexts().length;
  await withProfile(async profile => {
    const revision = randomUUID();
    await seedDisplayOnlyChart(profile, revision, {
      time_ms: Date.parse('2026-02-03T00:00:00Z'), trading_day: '2026-02-03', open: 73, high: 75, low: 72, close: 74,
      volume: 13, is_closed: true,
    });
    await withWorkspaceTransport(profile, async ({ dispatch, holdRevisionStatus }) => {
      const snapshots = [];
      const snapshot = async stage => {
        const reply = await dispatch({ type: 'snapshot' });
        snapshots.push({ stage, ok: reply.ok, code: reply.error?.code, selection_revision: reply.result?.selection_revision });
        return reply;
      };
      const busy = { ok: false, error: { code: 'BUSY', retryable: true } };
      const missingRevision = randomUUID();
      const reads = [];
      const deliverPublishedReply = Promise.withResolvers();
      const entered = Promise.withResolvers();
      const allowWrite = Promise.withResolvers();
      const committed = Promise.withResolvers();
      const allowReply = Promise.withResolvers();
      const calls = await nativeTransport(page, { optedIn: true }, async ({ operation, payload }) => {
        if (operation === 'market.listLocalImportRevisions') {
          return { result: { revisions: [], next_after_revision_id: null } };
        }
        if (payload.type === 'frozen_chart_status') return { result: { available: true } };
        expect(operation).toBeNull();
        if (payload.type === 'snapshot') return snapshot('native');
        if (payload.type === 'workspace_source') {
          entered.resolve();
          await allowWrite.promise;
          const reply = await dispatch(payload);
          committed.resolve(reply);
          await allowReply.promise;
          return reply;
        }
        if (payload.type === 'frozen_chart') {
          const read = { payload, reply: dispatch(payload) };
          reads.push(read);
          const reply = await read.reply;
          read.outcome = reply.ok ? 'opened' : reply.error.code;
          if (payload.revision_id === revision) {
            await deliverPublishedReply.promise;
          }
          return reply;
        }
        return dispatch(payload);
      });
      const network = observeWorkspaceNetwork(page, baseURL);
      const original = { ok: true, result: { selection_revision: 0, source: null } };
      const sourceWrites = () => calls.filter(({ args }) => args.payload.type === 'workspace_source');
      const snapshotReads = () => calls.filter(({ args }) => args.payload.type === 'snapshot').length;
      const priceChart = page.getByRole('region', { name: 'Price chart' });
      const dialog = page.getByRole('dialog', { name: 'Open published CSV' });
      const revisionInput = dialog.getByLabel('Revision ID');
      const openFrozen = control(dialog, 'Open frozen chart');
      const useSource = control(dialog, 'Use as workspace source');
      const reload = control(dialog, 'Reload workspace state');
      const alert = dialog.getByRole('alert');
      let scenarioFailed = false;
      try {
        await page.goto('/');
        await control(priceChart, 'Open published CSV').click();
        await revisionInput.fill(missingRevision);
        await openFrozen.click();
        await expect(alert).toHaveText('This frozen chart is unavailable in the current profile.');
        await expect(useSource).toHaveCount(0);
        await expect(openFrozen).toBeEnabled();
        expect(reads.map(read => read.outcome)).toEqual(['SNAPSHOT_UNAVAILABLE']);
        expect(sourceWrites()).toHaveLength(0);
        // This invoke substitute owns the real writer, but never mounts a second workspace UI.
        expect(browser.contexts()).toHaveLength(contextCount);
        expect(await snapshot('after-unavailable')).toMatchObject(original);

        await revisionInput.fill(revision);
        const held = await holdRevisionStatus(revision);
        try {
          await openFrozen.click();
          await expect.poll(() => reads[1]?.payload.revision_id).toBe(revision);
          expect(await reads[1].reply).toEqual(busy);
          await expect(useSource).toHaveCount(0);
          expect(sourceWrites()).toHaveLength(0);
          expect(await snapshot('status-read-held')).toEqual(busy);
          held.release();
          expect(await held.reply).toEqual({ ok: true, result: { available: true } });
          expect(await snapshot('status-read-drained')).toMatchObject(original);
        } finally {
          held.release();
          deliverPublishedReply.resolve();
        }
        await expect(useSource, 'The published chart opens after the held reader releases admission').toBeEnabled();
        expect(reads.map(read => read.outcome)).toEqual(['SNAPSHOT_UNAVAILABLE', 'BUSY', 'opened']);
        expect(reads[2].payload).toEqual(reads[1].payload);
        expect(await snapshot('chart-opened')).toMatchObject(original);
        await page.evaluate(() => {
          const ipc = window.__TAURI_INTERNALS__;
          const invoke = ipc.invoke;
          ipc.invoke = async (command, args) => {
            const reply = await invoke(command, args);
            if (args.payload?.type === 'workspace_source') window.syntheticSourceReplyDelivered = true;
            return reply;
          };
        });
        await page.clock.install();
        await useSource.click();
        await entered.promise;
        await page.clock.fastForward(17_500);
        await expect(reload).toBeEnabled();
        await expect(useSource).toBeDisabled();
        await expect(alert).toContainText('Reload the workspace state before changing the source again.');
        const firstRead = snapshotReads();
        await reload.click();
        await expect.poll(snapshotReads).toBe(firstRead + 1);
        await expect(reload).toBeEnabled();
        await expect(useSource).toBeDisabled();
        expect(await snapshot('source-write-pending')).toMatchObject(original);

        allowWrite.resolve();
        expect(await committed.promise).toMatchObject({ ok: true, result: { selection_revision: 1 } });
        const source = { kind: 'none' };
        const peer = await dispatch({
          type: 'workspace_source', request_id: randomUUID(), expected_selection_revision: 1, source,
        });
        expect(peer).toMatchObject({ ok: true, result: { selection_revision: 2 } });
        await reload.click();
        await expect(useSource).toBeEnabled();
        await expect(alert).toContainText('Review the current workspace source.');
        const lastRead = snapshotReads();

        allowReply.resolve();
        await page.waitForFunction(() => window.syntheticSourceReplyDelivered === true);
        await page.clock.runFor(100);
        await expect(dialog).toBeVisible();
        await expect(alert).toContainText('Review the current workspace source.');
        expect(snapshotReads()).toBe(lastRead);
        expect(sourceWrites()).toHaveLength(1);
        expect(sourceWrites()[0].args.payload).toMatchObject({
          expected_selection_revision: 0, source: { kind: 'frozen', revision_id: revision },
        });
        expect(await snapshot('late-reply-delivered')).toMatchObject({ ok: true, result: { selection_revision: 2, source } });
        expect(network).toEqual([]);
      } catch (error) {
        scenarioFailed = true;
        throw error;
      } finally {
        deliverPublishedReply.resolve();
        allowWrite.resolve();
        allowReply.resolve();
        try {
          await testInfo.attach('native-snapshot-admission', { body: JSON.stringify(snapshots), contentType: 'application/json' });
          await testInfo.attach('native-frozen-chart-admission', {
            body: JSON.stringify(reads.map(({ payload, outcome }) => ({
              revision: payload.revision_id === revision ? 'published' : 'missing', outcome,
            }))),
            contentType: 'application/json',
          });
        } catch (error) {
          if (!scenarioFailed) throw error;
          console.warn('Unable to attach native source diagnostics after a test failure.', error);
        }
      }
    }, { gateStatus: true });
  });
});

test('a revision gate holds the designated Rust reply after an interleaved status for the same revision', async ({}, testInfo) => {
  test.setTimeout(180_000);
  await buildPreview();
  await withProfile(async profile => {
    const revision = randomUUID();
    await seedDisplayOnlyChart(profile, revision, {
      time_ms: Date.parse('2026-02-04T00:00:00Z'), trading_day: '2026-02-04',
      open: 103, high: 109, low: 101, close: 106, volume: 13, is_closed: true,
    });
    await withWorkspaceTransport(profile, async ({ dispatch, dispatchResponse, holdRevisionRead }) => {
      const before = await dispatch({ type: 'snapshot' });
      expect(before).toMatchObject({ ok: true, result: { selection_revision: 0, source: null } });
      const designated = randomUUID();
      const background = randomUUID();
      const phases = [];
      let backgroundReply;
      const held = await holdRevisionRead(async () => {
        backgroundReply = await revisionStatus(dispatch, revision, background);
        return revisionStatus(dispatchResponse, revision, designated);
      }, designated, event => phases.push(event));
      let rejected;
      let reply;
      const backgroundCompletedBeforeHold = backgroundReply?.ok === true;
      try {
        expect(backgroundReply, 'The native holder belongs to the designated request, not the earlier same-revision read')
          .toEqual({ ok: true, result: { available: true } });
        expect(phases).toContainEqual({ phase: 'held', requestId: designated });
        rejected = await revisionStatus(dispatchResponse, revision);
        expect(rejected).toEqual({ status: 429, body: { ok: false, error: { code: 'BUSY', retryable: true } } });
      } finally {
        held.release();
        try {
          reply = await held.reply;
        } finally {
          await testInfo.attach('native-revision-gate-interleaving', {
            body: JSON.stringify({ designated, background, backgroundCompletedBeforeHold, phases, rejected, reply }),
            contentType: 'application/json',
          });
        }
      }
      expect(reply).toEqual({ ok: true, result: { available: true } });
      expect(await dispatch({ type: 'snapshot' })).toEqual(before);
    }, { gateStatus: true });
  });
});

test('a revision gate preserves rejected and thrown errors and releases an unclaimed timeout', async ({}, testInfo) => {
  test.setTimeout(180_000);
  await buildPreview();
  await withProfile(profile => withWorkspaceTransport(profile, async ({ dispatch, holdRevisionRead, holdRevisionStatus }) => {
    const phases = [];
    const record = event => phases.push(event);
    const before = await dispatch({ type: 'snapshot' });
    try {
      await test.step('propagates the original code promptly', async () => {
        await expect(holdRevisionStatus('invalid', record)).rejects.toMatchObject({ code: 'INVALID_ARGUMENT' });
      }, { timeout: 3000 });
      await test.step('propagates a thrown read error promptly', async () => {
        const failure = new Error('Synthetic revision dispatch failure');
        await expect(holdRevisionRead(() => { throw failure; }, randomUUID(), record)).rejects.toBe(failure);
      }, { timeout: 3000 });
      const requestId = randomUUID();
      await test.step('disarms when the designated request never reaches the writer', async () => {
        await expect(holdRevisionRead(() => new Promise(() => {}), requestId, record))
          .rejects.toMatchObject({ name: 'AbortError', cause: { name: 'TimeoutError' } });
        expect(phases.filter(phase => phase.requestId === requestId).map(phase => phase.phase))
          .toEqual(['armed', 'claim-failed', 'released']);
        expect(await dispatch({ type: 'snapshot' })).toEqual(before);
      });
      const held = await holdRevisionStatus(randomUUID(), record);
      held.release();
      expect(await held.reply).toEqual({ ok: true, result: { available: false } });
      const next = await holdRevisionStatus(randomUUID(), record);
      try {
        held.release();
        await expect(holdRevisionStatus(randomUUID(), record)).rejects.toThrow('already owns the gate');
        expect(await dispatch({ type: 'snapshot' })).toMatchObject({ ok: false, error: { code: 'BUSY' } });
      } finally {
        next.release();
      }
      expect(await next.reply).toEqual({ ok: true, result: { available: false } });
      expect(await dispatch({ type: 'snapshot' })).toEqual(before);
    } finally {
      await testInfo.attach('native-revision-gate-failure-phases', {
        body: JSON.stringify(phases), contentType: 'application/json',
      });
    }
  }, { gateStatus: true }));
});

test('opted-in desktop keeps sample timeframe controls separate from durable IPC', async ({ page, baseURL }) => {
  let current = ready('1m', 1, undo);
  const calls = await nativeTransport(page, { optedIn: true }, args => {
    if (args.operation === null) {
      expect(args.payload).toEqual({ type: 'snapshot' });
      return { result: current };
    }
    if (args.operation === 'instrument.search') {
      expect(args.payload).toEqual({ query: '', limit: 50 });
      return { result: { instruments: [] } };
    }
    // Model a valid reply so accidental dispatch fails at the IPC assertion.
    const sampleWrite = args.operation === 'chart.setTimeframe';
    if (!sampleWrite) expectMutation(args, 'workspace.undo', 1, { operation_id: operationId });
    const id = randomUUID();
    current = ready('5m', 2, sampleWrite ? { operation_id: id, committed_workspace_revision: 2 } : null);
    return { result: response(args.payload, committed(args.operation, current.workspace, id, sampleWrite)) };
  });
  const network = observeWorkspaceNetwork(page, baseURL);
  const undoButton = control(page, 'Undo timeframe change');
  const workspaceCalls = () => calls.map(call => call.args.operation).filter(name => name !== 'instrument.search');
  await page.goto('/');
  await expect(undoButton).toBeEnabled();
  await expect(control(page, '1m')).toHaveAttribute('aria-pressed', 'true');
  await control(page, 'Open offline sample').click();
  await expect(page.locator('.connection')).toHaveText('Offline sampleSimulated');
  await control(page, '5m').click();
  await expect(control(page, '5m')).toHaveAttribute('aria-pressed', 'true');
  expect(workspaceCalls()).toEqual([null]);
  await expect(undoButton).toHaveCount(0);

  await page.reload();
  await expect(page.locator('.connection')).toHaveText('Offline sampleSimulated');
  await expect(control(page, '5m')).toHaveAttribute('aria-pressed', 'true');
  await expect(undoButton).toHaveCount(0);
  await leaveSample(page);
  await expect(page.locator('.connection')).toHaveText('No data source');
  await expect(control(page, '1m')).toHaveAttribute('aria-pressed', 'true');
  await expect(undoButton).toBeEnabled();
  await undoButton.click();
  await expect(control(page, '5m')).toHaveAttribute('aria-pressed', 'true');
  await expect(undoButton).toBeDisabled();
  expect(workspaceCalls()).toEqual([null, null, 'workspace.undo']);
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
  expect(network).toEqual([]);
});

test('denied or uncorrelated desktop replies keep the current timeframe; reload sends only a snapshot', async ({ page, baseURL }) => {
  let first = true;
  const calls = await nativeTransport(page, { optedIn: true }, args => {
    if (args.operation === null) return { result: ready('1m', 0) };
    expectMutation(args, 'chart.setTimeframe', 0, { timeframe: '5m' });
    if (first) {
      first = false;
      return { error: { code: 'PERMISSION_DENIED', message: 'denied', retryable: false } };
    }
    return { result: response(args.payload, committed(args.operation, workspace('5m', 1), operationId, true), randomUUID()) };
  });
  const network = observeWorkspaceNetwork(page, baseURL);
  await page.goto('/');
  await control(page, '5m').click();
  await expect(page.getByRole('alert')).toHaveText('This workspace action is no longer authorized.');
  await expect(control(page, '1m')).toHaveAttribute('aria-pressed', 'true');
  await expect(control(page, '5m')).toBeDisabled();
  await control(page, 'Reload workspace state').click();
  await expect(control(page, '5m')).toBeEnabled();
  await control(page, '5m').click();
  await expect(page.getByRole('alert')).toHaveText('The workspace reply could not be verified.');
  await expect(control(page, '1m')).toHaveAttribute('aria-pressed', 'true');
  await expect(control(page, 'Undo timeframe change')).toBeDisabled();
  expect(calls.map(call => call.args.operation)).toEqual([null, 'chart.setTimeframe', null, 'chart.setTimeframe']);
  expect(network).toEqual([]);
});

test('native text setup retries a held writer and sends only on explicit submission', async ({ page, baseURL }, testInfo) => {
  const snapshotEntered = Promise.withResolvers();
  const releaseSnapshot = Promise.withResolvers();
  const modelOperation = 'ai.textConnection';
  const origin = 'http://127.0.0.1:1';
  const model = 'synthetic:start';
  const route = { kind: 'ollama', destination: origin + '/api/chat', model,
    scope: 'conversation_text', charge_basis: 'endpoint_operator' };
  const proposalId = randomUUID();
  const connection = { connection_id: randomUUID(), route };
  const preAdmissionBusy = { error: { code: 'BUSY', retryable: true } };
  const busyMessage = 'The local workspace is busy. Try again when it is ready.';
  const admitted = [];
  let held = 'status';
  let statusHeldSince;
  let turnId;
  const calls = await nativeTransport(page, { optedIn: true, textModel: true }, async ({ operation, payload }) => {
    if (operation === null) {
      expect(payload).toEqual({ type: 'snapshot' });
      snapshotEntered.resolve();
      await releaseSnapshot.promise;
      return { result: ready('1m', 0) };
    }
    expect(operation).toBe(modelOperation);
    const args = payload.intent.arguments;
    const { action } = args;
    if (action === held) {
      if (action === 'status') statusHeldSince ??= performance.now();
      return preAdmissionBusy;
    }
    admitted.push(action);
    let result;
    switch (action) {
      case 'status': result = { connection: null, turn_id: null }; break;
      case 'propose':
        expect(args.route).toEqual({ kind: 'ollama', origin, model });
        result = { proposal_id: proposalId, route };
        break;
      case 'activate':
        expect(args.proposal_id).toBe(proposalId);
        result = { connection };
        break;
      case 'send':
        expect(args).toEqual({ action, connection_id: connection.connection_id, question: 'Synthetic native question' });
        turnId = payload.request_id;
        result = { turn_id: turnId };
        break;
      case 'poll':
      case 'cancel':
        expect(args).toEqual({ action, connection_id: connection.connection_id, turn_id: turnId });
        result = action === 'poll' ? { turn_id: turnId, turn: { state: 'running' } } : {};
        break;
      default: throw new Error(`Unexpected text action ${action}`);
    }
    return { result: response(payload, { state: 'completed', operation, result: { action, ...result } }) };
  });
  const network = observeWorkspaceNetwork(page, baseURL);
  const chat = page.getByRole('dialog', { name: 'Ask AI' });
  const settings = page.getByRole('dialog', { name: 'AI settings' });
  const commands = action => calls.map(({ args }) => args.payload).filter(payload => payload.intent?.arguments.action === action);

  try {
    await page.goto('/');
    await snapshotEntered.promise;
    await control(page, 'Ask AI').click();
    // The captured Windows initial workspace snapshot took just over three seconds.
    await expect.poll(() => statusHeldSince === undefined ? 0 : performance.now() - statusHeldSince,
      { intervals: [50, 100], timeout: 3800 }).toBeGreaterThanOrEqual(3000);
    const statusHeldMs = Math.round(performance.now() - statusHeldSince);
    expect(commands('status').length).toBeGreaterThan(1);
    expect(admitted).toEqual([]);
    held = undefined;
    releaseSnapshot.resolve();
    await chat.getByLabel('Question').fill('Synthetic native question');
    await control(chat, 'Ask').click();
    expect(admitted).toEqual(['status']);

    const submitReview = async () => {
      await settings.getByLabel('Server origin').fill(origin);
      await settings.getByLabel('Model', { exact: true }).fill(model);
      await control(settings, 'Review connection').click();
    };
    held = 'propose';
    await submitReview();
    await expect.poll(() => commands('propose').length).toBeGreaterThanOrEqual(3);
    expect(admitted).toEqual(['status']);
    held = undefined;
    await expect(settings.getByText(route.destination, { exact: true })).toBeVisible();
    for (const action of ['status', 'propose']) {
      const attempts = commands(action);
      expect(attempts).toEqual(attempts.map(() => attempts[0]));
    }
    expect(admitted).toEqual(['status', 'propose']);
    expect(network).toEqual([]);

    await control(settings, 'Connect and enable').click();
    await expect(chat.getByText(/^Ollama connected/)).toBeVisible();
    held = 'send';
    await control(chat, 'Send').click();
    await expect(chat.getByRole('alert')).toHaveText(busyMessage);
    expect(commands('send')).toHaveLength(1);
    expect(admitted).toEqual(['status', 'propose', 'activate']);
    held = undefined;
    await control(chat, 'Send').click();
    await expect(chat.getByText('Waiting for the model…')).toBeVisible();
    await control(chat, 'Cancel turn').click();
    await expect(chat.getByText('No model connected', { exact: true })).toBeVisible();
    expect(admitted.filter(action => action === 'send')).toHaveLength(1);
    expect(commands('send')).toHaveLength(2);
    expect(commands('cancel')).toHaveLength(1);
    expect(network).toEqual([]);

    const recoveredProposeAttempts = commands('propose').length;
    held = 'propose';
    await control(chat, 'AI settings').click();
    await submitReview();
    await expect(settings.getByRole('alert')).toHaveText(busyMessage, { timeout: 6000 });
    const expiredAttempts = commands('propose').slice(recoveredProposeAttempts);
    expect(expiredAttempts.length).toBeGreaterThan(1);
    expect(expiredAttempts).toEqual(expiredAttempts.map(() => expiredAttempts[0]));
    expect(admitted.filter(action => action === 'propose')).toHaveLength(1);
    expect(network).toEqual([]);

    await testInfo.attach('native-text-admission', {
      body: JSON.stringify({ statusHeldMs, statusAttempts: commands('status').length, recoveredProposeAttempts,
        expiredProposeAttempts: expiredAttempts.length, admitted, unexpectedRequests: network }, null, 2),
      contentType: 'application/json',
    });
  } finally {
    releaseSnapshot.resolve();
  }
});

for (const change of ['revoked source without selection', 'redacted selection without source']) {
  test(`a delayed published CSV cannot display after a ${change}`, async ({ page }) => {
    await buildPreview();
    await withProfile(async profile => {
      const id = randomUUID();
      await seedDisplayOnlyChart(profile, id, {
        time_ms: Date.parse('2026-02-03T00:00:00Z'), trading_day: '2026-02-03', open: 73, high: 75, low: 72,
        close: 74, volume: 13, is_closed: true,
      });
      await withWorkspaceTransport(profile, async ({ dispatch }) => {
        const readEntered = Promise.withResolvers();
        const late = Promise.withResolvers();
        const status = Promise.withResolvers();
        const base = { ...restoreSnapshot(id), source: null };
        let current = change.startsWith('revoked') ? { ...base, chart_evidence: null } : base;
        await nativeTransport(page, { optedIn: true }, async ({ operation, payload }) => {
          if (operation === 'market.listLocalImportRevisions') return { result: { revisions: [], next_after_revision_id: null } };
          if (payload.type === 'snapshot') return { result: current };
          if (payload.type === 'frozen_chart_status') return status.promise;
          const reply = await dispatch(payload);
          expect(reply.ok).toBe(true);
          readEntered.resolve();
          await late.promise;
          return reply;
        });
        try {
          await page.goto('/');
          await control(page.getByRole('region', { name: 'Price chart' }), 'Open published CSV').click();
          const dialog = page.getByRole('dialog', { name: 'Open published CSV' });
          await dialog.getByLabel('Revision ID').fill(id);
          await control(dialog, 'Open frozen chart').click();
          await readEntered.promise;
          current = change.startsWith('revoked')
            ? { ...base, ...restoreSnapshot(id, 'revoked'), chart_evidence: null }
            : { ...base, chart_evidence: { unavailable: true } };
          await page.evaluate(() => window.dispatchEvent(new Event('focus')));
          if (change.startsWith('revoked')) {
            await expect(page.getByText('Access to this published CSV was revoked.')).toBeVisible();
          } else {
            await expect(control(dialog, 'Clear shared selection')).toBeVisible();
          }
          late.resolve();
          await expect(dialog.getByRole('alert')).toHaveText('This frozen chart is unavailable in the current profile.');
          await expect(control(dialog, 'Use as workspace source')).toHaveCount(0);
        } finally {
          late.resolve();
          status.resolve({ result: { available: false } });
        }
      });
    });
  });
}

test('a retryable admission rejection cannot disable authorized selection clear', async ({ page }) => {
  const id = randomUUID();
  const base = { ...restoreSnapshot(id), source: null, chart_evidence: { unavailable: true } };
  let reads = 0;
  let cleared;
  await nativeTransport(page, { optedIn: true }, ({ payload }) => {
    if (payload.type === 'snapshot') {
      if (++reads === 1) return { error: { code: 'BUSY', retryable: true } };
      return { result: base };
    }
    expect(payload).toMatchObject({ operation: 'workspace.selectEvidence', expected_selection_revision: 2, target: null });
    cleared = payload;
    return { result: {
      protocol_version: '1.1', type: 'response', operation: payload.operation, request_id: payload.request_id,
      selection: { workspace_id: base.workspace.id, selection_revision: 3, target: null },
    } };
  });
  await page.goto('/');
  const clear = control(page.getByRole('region', { name: 'Price chart' }), 'Clear shared selection');
  await expect(clear).toBeEnabled();
  expect(reads).toBeGreaterThan(1);
  await clear.click();
  await expect(clear).toHaveCount(0);
  expect(cleared?.workspace_id).toBe(base.workspace.id);
});

test('the independent snapshot proof retries only a typed pre-admission refusal', async ({ page }, testInfo) => {
  const source = { kind: 'frozen', revision_id: randomUUID(), status: 'available' };
  const result = { selection_revision: 2, source };
  const admitted = { status: 200, body: { ok: true, result } };
  const refusal = { status: 429, body: { ok: false, error: { code: 'BUSY', retryable: true } } };
  const permissionError = { code: 'PERMISSION_DENIED', retryable: false };
  const denied = { status: 403, body: { ok: false, error: permissionError } };
  const contradictory = { ...admitted, body: { ...admitted.body, error: permissionError } };
  const frame = { type: 'snapshot' };
  const bridgeResponses = [admitted, denied, contradictory];
  const bridgeReplies = [];
  let bridgeReply;
  const bridgeCalls = await nativeTransport(page, { optedIn: true }, () => bridgeReply);
  const traces = [];
  const run = (name, first) => {
    const calls = [];
    const outcomes = [];
    traces.push({ name, calls, outcomes });
    const attempt = readProofSnapshot(async frame => {
      calls.push(frame);
      if (calls.length > 1) return admitted;
      if (first instanceof Error) throw first;
      return first;
    }, outcome => outcomes.push(outcome));
    return { calls, outcomes, attempt };
  };
  try {
    await page.goto('about:blank');
    for (const response of bridgeResponses) {
      bridgeReply = response.body;
      const outcome = await page.evaluate(async payload => {
        try {
          return { result: await window.__TAURI_INTERNALS__.invoke('execute_operation', { operation: null, payload }) };
        } catch (error) {
          return { error };
        }
      }, frame);
      bridgeReplies.push({ response, outcome });
    }
    expect(bridgeReplies.map(entry => entry.outcome)).toEqual([{ result }, { error: permissionError }, { error: permissionError }]);
    expect(bridgeCalls).toHaveLength(bridgeResponses.length);

    const valid = run('ordinary successful snapshot', admitted);
    await expect(valid.attempt).resolves.toEqual(result);
    expect(valid.calls).toEqual([frame]);
    expect(valid.outcomes).toMatchObject([{
      status: 200, ok: true, hasResult: true, hasError: false, refused: false, selection: 2, source,
    }]);

    const recovered = run('pre-admission refusal followed by an admitted proof', refusal);
    await expect(recovered.attempt).resolves.toEqual(result);
    expect(recovered.calls).toEqual([frame, frame]);
    expect(recovered.outcomes).toMatchObject([
      { status: 429, ok: false, code: 'BUSY', retryable: true, hasResult: false, hasError: true, refused: true },
      { status: 200, ok: true, hasResult: true, hasError: false, refused: false, selection: 2, source },
    ]);
    const rejected = run('ordinary permission denial', denied);
    await expect(rejected.attempt).rejects.toThrow('The independent proof must return a successful workspace snapshot');
    expect(rejected.calls).toEqual([frame]);
    expect(rejected.outcomes).toMatchObject([{
      status: 403, ok: false, code: 'PERMISSION_DENIED', retryable: false, hasError: true, hasResult: false, refused: false,
    }]);
    for (const [name, response] of [
      ['admitted BUSY', { ...refusal, status: 200 }],
      ['missing envelope', { status: 200, body: undefined }],
      ['missing result', { status: 200, body: { ok: true } }],
    ]) {
      const rejected = run(name, response);
      await expect(rejected.attempt).rejects.toThrow('The independent proof must return a successful workspace snapshot');
      expect(rejected.calls).toEqual([frame]);
      expect(rejected.outcomes).toEqual([{ ...snapshotOutcome(response), refused: false }]);
    }
    for (const [name, response] of [
      ['contradictory success', contradictory],
      ['contradictory refusal', { ...refusal, body: { ...refusal.body, result } }],
    ]) {
      const rejected = run(name, response);
      await expect(rejected.attempt).rejects.toThrow('The independent proof received a contradictory workspace snapshot');
      expect(rejected.calls).toEqual([frame]);
      expect(rejected.outcomes).toMatchObject([{
        status: response.status, ok: response.body.ok, code: response.body.error.code,
        retryable: response.body.error.retryable, hasError: true, hasResult: true, refused: false, selection: 2, source,
      }]);
    }
    const error = new Error('Synthetic snapshot transport failure');
    const interrupted = run('transport failure', error);
    await expect(interrupted.attempt).rejects.toBe(error);
    expect(interrupted.calls).toEqual([frame]);
    expect(interrupted.outcomes).toEqual([{ transportError: 'Error', code: null }]);
  } finally {
    await testInfo.attach('independent-snapshot-proof-controls', {
      body: JSON.stringify({ bridgeReplies, proofAttempts: traces }, null, 2), contentType: 'application/json',
    });
  }
});

for (const order of ['before the focus snapshot', 'after the replacement is visible', 'with the same source confirmed']) {
  test(`first saved-chart reopen fences a held reply ${order}`, async ({ page }, testInfo) => {
    test.setTimeout(180_000);
    await buildPreview();
    await withProfile(async profile => {
      const a = randomUUID();
      const b = randomUUID();
      const bar = (date, open, high, low, close) => ({
        time_ms: Date.parse(date + 'T00:00:00Z'), trading_day: date, open, high, low, close, volume: 13, is_closed: true,
      });
      await seedDisplayOnlyChart(profile, a, bar('2026-02-03', 41, 46, 40, 44));
      await seedDisplayOnlyChart(profile, b, bar('2026-02-04', 102, 109, 101, 106));
      await withWorkspaceTransport(profile, async ({ dispatch, dispatchResponse }) => {
        const initial = await dispatch({ type: 'snapshot' });
        expect(initial).toMatchObject({ ok: true, result: { selection_revision: 0, source: null } });
        const selected = await dispatch({ type: 'workspace_source', request_id: randomUUID(),
          expected_selection_revision: initial.result.selection_revision, source: { kind: 'frozen', revision_id: a } });
        expect(selected).toMatchObject({ ok: true, result: { selection_revision: 1 } });
        const peerChangesSource = !order.startsWith('with the same');
        const expected = {
          selection_revision: selected.result.selection_revision + Number(peerChangesSource),
          source: { kind: 'frozen', revision_id: peerChangesSource ? b : a, status: 'available' },
        };
        const oldReply = Promise.withResolvers();
        const focusSnapshot = Promise.withResolvers();
        const history = [];
        const proofAttempts = [];
        const peerWrites = [];
        let snapshots = 0;
        let capturedRequestId;
        let held = false;
        let focusRequested = false;
        const expectedClose = peerChangesSource ? 106 : 44;
        const ordinary = ({ operation, payload }) => operation === 'market.listLocalImportRevisions'
          ? { result: { version: 1, revisions: [], next_after_revision_id: null } } : dispatch(payload);
        await nativeTransport(page, { optedIn: true, observeFrozenRevision: a }, async args => {
          const { payload } = args;
          if (payload.type === 'snapshot') {
            const afterFocus = focusRequested;
            const response = await dispatchResponse(payload);
            const outcome = snapshotOutcome(response);
            const successful = outcome.status === 200 && outcome.ok === true && outcome.hasResult && !outcome.hasError;
            const number = successful ? ++snapshots : null;
            history.push({ number, afterFocus, snapshotReply: outcome });
            if (number === 2) {
              await focusSnapshot.promise;
              history.push({ releasedSnapshot: number });
            }
            return response.body;
          }
          if (payload.type === 'frozen_chart' && payload.revision_id === a && !held) {
            held = true;
            const captured = await ordinary(args);
            expect(captured.ok).toBe(true);
            expect(captured.result.bars[0].bar.close).toBe(44);
            capturedRequestId = payload.request_id;
            await oldReply.promise;
            history.push({ released: a, close: 44, requestId: capturedRequestId });
            return captured;
          }
          return ordinary(args);
        });
        const price = page.getByRole('region', { name: 'Price chart' });
        const selectedBar = price.getByLabel('Selected bar');
        const releaseOriginalChart = async () => {
          oldReply.resolve();
          await page.waitForFunction(id => window.receivedFrozenReplies?.includes(id), capturedRequestId);
          history.push({ received: a, requestId: capturedRequestId });
        };
        let peer;
        try {
          if (peerChangesSource) {
            peer = await page.context().newPage();
            await nativeTransport(peer, { optedIn: true }, async args => {
              const reply = await ordinary(args);
              if (args.payload.type === 'workspace_source') peerWrites.push({ request: args.payload, reply });
              return reply;
            });
          }
          await page.goto('/');
          await expect.poll(() => capturedRequestId, { timeout: 8000 }).toEqual(expect.any(String));
          expect(history.find(row => row.number === 1)?.snapshotReply).toMatchObject({
            selection: selected.result.selection_revision, source: { kind: 'frozen', revision_id: a, status: 'available' },
          });
          await expect(selectedBar).toHaveCount(0);
          if (peerChangesSource) {
            const window = peer;
            await window.goto('/', { timeout: 8000 });
            const peerChart = window.getByRole('region', { name: 'Price chart' });
            await expect(peerChart.getByLabel('Selected bar')).toContainText('C 44');
            await openPanel(window, 'Data sources');
            await control(window.getByRole('complementary', { name: 'Data sources' }), 'Open published CSV').click({ timeout: 8000 });
            const dialog = window.getByRole('dialog', { name: 'Open published CSV' });
            await dialog.getByLabel('Revision ID').fill(b);
            await control(dialog, 'Open frozen chart').click();
            await expect(dialog.getByLabel('Selected bar')).toContainText('C 106');
            await control(dialog, 'Use as workspace source').click();
            await expect(dialog).toHaveCount(0);
            await expect(peerChart.getByLabel('Selected bar')).toContainText('C 106');
            history.push({ peerRenderedClose: 106 });
            expect(peerWrites.filter(write => write.reply.ok === true)).toMatchObject([{
              request: { request_id: expect.any(String), expected_selection_revision: selected.result.selection_revision,
                source: { kind: 'frozen', revision_id: b } },
              reply: { ok: true, result: { selection_revision: expected.selection_revision } },
            }]);
            const confirmed = await readProofSnapshot(dispatchResponse, outcome => proofAttempts.push(outcome));
            expect(confirmed, 'The independent proof confirms the committed peer selection').toMatchObject(expected);
          }
          await page.evaluate(() => {
            window.observedFirstReopen = [];
            const check = () => {
              const bar = document.querySelector('[aria-label="Selected bar"]');
              if (bar) window.observedFirstReopen.push(bar.textContent);
            };
            const observer = new MutationObserver(check);
            observer.observe(document.body, { subtree: true, childList: true, characterData: true });
            window.addEventListener('pagehide', () => observer.disconnect(), { once: true });
            window.observedFocus = false;
            window.addEventListener('focus', () => { window.observedFocus = true; }, { once: true });
          });
          expect(snapshots).toBe(1);
          focusRequested = true;
          history.push({ focusRequested });
          await page.bringToFront();
          await page.evaluate(() => { if (!window.observedFocus) window.dispatchEvent(new Event('focus')); });
          if (!order.startsWith('after')) await releaseOriginalChart();
          await expect.poll(() => snapshots, { timeout: 2500 }).toBe(2);
          expect(history.find(row => row.number === 2)).toMatchObject({
            afterFocus: true, snapshotReply: { selection: expected.selection_revision, source: expected.source },
          });
          await expect(selectedBar).toHaveCount(0);
          focusSnapshot.resolve();
          await expect(selectedBar).toContainText(`C ${expectedClose}`);
          history.push({ renderedClose: expectedClose });
          if (order.startsWith('after')) await releaseOriginalChart();
          await expect(selectedBar).toContainText(`C ${expectedClose}`);
          expect(snapshots).toBe(2);
          if (peerChangesSource) {
            expect((await page.evaluate(() => window.observedFirstReopen)).some(text => text.includes('C 44'))).toBe(false);
          }
        } finally {
          focusSnapshot.resolve();
          oldReply.resolve();
          const visible = page.isClosed() ? [] : await selectedBar.allTextContents();
          const observed = page.isClosed() ? [] : await page.evaluate(() => window.observedFirstReopen ?? []);
          await testInfo.attach('first-reopen-ordering', {
            body: JSON.stringify({ order, a, b, snapshots, history, proofAttempts, peerWrites, visible, observed }, null, 2),
            contentType: 'application/json',
          });
          await peer?.close();
        }
      });
    });
  });
}

for (const scenario of [
  { name: 'focus before the startup snapshot', heldNumber: 1, changeSource: true, events: 1 },
  { name: 'repeated focus during the first confirmation', heldNumber: 2, changeSource: true, events: 4 },
  { name: 'repeated focus confirms the unchanged source', heldNumber: 2, changeSource: false, events: 4 },
  { name: 'the old startup snapshot reaches its ordinary deadline', heldNumber: 1, changeSource: true, events: 3, timeout: true },
]) {
  test(`latest workspace focus survives ${scenario.name}`, async ({ page }, testInfo) => {
    test.setTimeout(180_000);
    await buildPreview();
    await withProfile(async profile => {
      const a = randomUUID();
      const b = randomUUID();
      const bar = (day, close) => ({
        time_ms: Date.parse(day + 'T00:00:00Z'), trading_day: day,
        open: close - 3, high: close + 3, low: close - 5, close, volume: 13, is_closed: true,
      });
      await seedDisplayOnlyChart(profile, a, bar('2026-02-03', 44));
      await seedDisplayOnlyChart(profile, b, bar('2026-02-04', 106));
      await withWorkspaceTransport(profile, async ({ dispatch, dispatchResponse, holdRevisionStatus }) => {
        const selectA = await dispatch({ type: 'workspace_source', request_id: randomUUID(),
          expected_selection_revision: 0, source: { kind: 'frozen', revision_id: a } });
        expect(selectA).toMatchObject({ ok: true, result: { selection_revision: 1 } });

        const heldSnapshot = Promise.withResolvers();
        const firstChart = Promise.withResolvers();
        const snapshots = [];
        const trace = [];
        const ordinary = ({ operation, payload }) => operation === 'market.listLocalImportRevisions'
          ? { result: { version: 1, revisions: [], next_after_revision_id: null } } : dispatch(payload);
        let snapshotCalls = 0;
        let chartHeld = false;
        let focusRefused = false;
        await nativeTransport(page, { optedIn: true }, async args => {
          const { payload } = args;
          if (payload.type === 'snapshot') {
            let reply;
            if (!focusRefused && snapshotCalls === 1 && scenario.name === 'repeated focus during the first confirmation') {
              focusRefused = true;
              const holder = await holdRevisionStatus(a, event => trace.push({ revisionGate: event }));
              try {
                reply = await ordinary(args);
                expect(reply).toMatchObject({ ok: false, error: { code: 'BUSY', retryable: true } });
                trace.push({ focusSnapshotRefused: reply.error.code });
              } finally {
                holder.release();
                expect(await holder.reply).toMatchObject({ ok: true, result: { available: true } });
              }
            } else {
              reply = await ordinary(args);
            }
            if (reply.ok !== true) {
              trace.push({ snapshotRejected: reply.error?.code });
              return reply;
            }
            const number = ++snapshotCalls;
            snapshots.push({ number, source: reply.result.source, selection: reply.result.selection_revision });
            if (number === scenario.heldNumber) await heldSnapshot.promise;
            trace.push({ deliveredSnapshot: number });
            return reply;
          }
          if (payload.type === 'frozen_chart' && !chartHeld && scenario.heldNumber === 2) {
            chartHeld = true;
            const reply = await ordinary(args);
            expect(reply.result.bars[0].bar.close).toBe(44);
            await firstChart.promise;
            trace.push({ deliveredChart: 44 });
            return reply;
          }
          return ordinary(args);
        });
        const chart = page.getByRole('region', { name: 'Price chart' });
        const selectedBar = chart.getByLabel('Selected bar');
        const focus = count => page.evaluate(count => {
          if (document.visibilityState !== 'visible') throw new Error('The first workspace must be visible at focus.');
          for (let i = 0; i < count; i++) window.dispatchEvent(new Event('focus'));
        }, count);
        let peer;
        try {
          if (scenario.changeSource) {
            peer = await page.context().newPage();
            await nativeTransport(peer, { optedIn: true }, ordinary);
          }
          if (scenario.timeout) await page.clock.install();
          await page.goto('/');
          if (scenario.heldNumber === 2) {
            await expect.poll(() => chartHeld).toBe(true);
            await focus(1);
          }
          await expect.poll(() => snapshots.find(row => row.number === scenario.heldNumber)).toMatchObject({
            source: { revision_id: a }, selection: 1,
          });
          if (peer) {
            await peer.goto('/');
            const peerChart = peer.getByRole('region', { name: 'Price chart' });
            await expect(peerChart.getByLabel('Selected bar')).toContainText('C 44');
            await openPanel(peer, 'Data sources');
            const sources = peer.getByRole('complementary', { name: 'Data sources' });
            await control(sources, 'Open published CSV').click({ timeout: 8000 });
            const dialog = peer.getByRole('dialog', { name: 'Open published CSV' });
            await dialog.getByLabel('Revision ID').fill(b);
            await control(dialog, 'Open frozen chart').click();
            await expect(dialog.getByLabel('Selected bar')).toContainText('C 106');
            await control(dialog, 'Use as workspace source').click();
            await expect(dialog).toHaveCount(0);
            await expect(peerChart.getByLabel('Selected bar')).toContainText('C 106');
            const current = await readProofSnapshot(dispatchResponse, outcome => trace.push({ proofSnapshot: outcome }));
            expect(current).toMatchObject({ source: { revision_id: b }, selection_revision: 2 });
            trace.push({ peerCommitted: b, close: 106, selection: 2 });
          }
          await page.evaluate(() => {
            window.focusGenerationBars = [];
            const observer = new MutationObserver(() => {
              const bar = document.querySelector('[aria-label="Selected bar"]');
              if (bar) window.focusGenerationBars.push(bar.textContent);
            });
            observer.observe(document.body, { subtree: true, childList: true, characterData: true });
            window.addEventListener('pagehide', () => observer.disconnect(), { once: true });
          });
          await focus(scenario.events);
          trace.push({ focusEvents: scenario.events });
          expect(snapshotCalls).toBe(scenario.heldNumber);
          await expect(selectedBar).toHaveCount(0);
          firstChart.resolve();
          if (scenario.timeout) await page.clock.fastForward(16_000);
          else heldSnapshot.resolve();
          await expect.poll(() => snapshotCalls, { timeout: 4500 }).toBe(scenario.heldNumber + 1);
          await expect(selectedBar).toContainText(scenario.changeSource ? 'C 106' : 'C 44');
          heldSnapshot.resolve();
          if (scenario.timeout) {
            await expect.poll(() => trace.some(row => row.deliveredSnapshot === 1)).toBe(true);
            await page.clock.runFor(50);
          }
          const confirmed = snapshots.find(row => row.number === scenario.heldNumber + 1);
          expect(confirmed).toMatchObject({ source: { revision_id: scenario.changeSource ? b : a }, selection: scenario.changeSource ? 2 : 1 });
          if (scenario.changeSource) {
            expect((await page.evaluate(() => window.focusGenerationBars)).some(text => text.includes('C 44'))).toBe(false);
          }
          expect(snapshotCalls).toBe(scenario.heldNumber + 1);
        } finally {
          heldSnapshot.resolve();
          firstChart.resolve();
          const visible = page.isClosed() ? [] : await selectedBar.allTextContents();
          const observed = page.isClosed() ? [] : await page.evaluate(() => window.focusGenerationBars ?? []);
          await testInfo.attach('latest-focus-generation', {
            body: JSON.stringify({ scenario, a, b, snapshots, trace, visible, observed }, null, 2),
            contentType: 'application/json',
          });
          await peer?.close();
        }
      }, { gateStatus: scenario.name === 'repeated focus during the first confirmation' });
    });
  });
}

for (const scenario of [
  { name: 'a saved A is superseded during readback', startsWithA: true },
  { name: 'a first source save is superseded during readback' },
  { name: 'the same source is confirmed after readback', startsWithA: true, unchanged: true },
  { name: 'focus during the issued write settles after its readback', heldWrite: true },
  { name: 'focus survives failed readback and explicit recovery', recover: true, contendProof: true },
  { name: 'focus survives failed readback and ordinary explicit recovery', recover: true },
  { name: 'closing the dialog does not discard focus or repeat the write', closeDialog: true },
]) {
  test(`source-save focus waits for the latest snapshot when ${scenario.name}`, async ({ page }, testInfo) => {
    test.setTimeout(180_000);
    await buildPreview();
    await withProfile(async profile => {
      const a = randomUUID();
      const b = randomUUID();
      const bar = (day, close) => ({
        time_ms: Date.parse(day + 'T00:00:00Z'), trading_day: day,
        open: close - 3, high: close + 3, low: close - 5, close, volume: 13, is_closed: true,
      });
      await seedDisplayOnlyChart(profile, a, bar('2026-02-03', 44));
      await seedDisplayOnlyChart(profile, b, bar('2026-02-04', 106));
      await withWorkspaceTransport(profile, async ({ dispatch, dispatchResponse, holdRevisionStatus }) => {
        const originalRevision = scenario.startsWithA ? 1 : 0;
        const savedRevision = originalRevision + 1;
        const confirmedRevision = scenario.unchanged ? savedRevision : savedRevision + 1;
        const currentId = scenario.unchanged ? a : b;
        if (scenario.startsWithA) {
          expect(await dispatch({ type: 'workspace_source', request_id: randomUUID(), expected_selection_revision: 0,
            source: { kind: 'frozen', revision_id: a } })).toMatchObject({ ok: true, result: { selection_revision: 1 } });
        }
        const reopen = Promise.withResolvers();
        const initialReadback = Promise.withResolvers();
        const operation = Promise.withResolvers();
        const trailing = Promise.withResolvers();
        const readbackNumber = scenario.recover ? 3 : 2;
        const snapshots = [];
        const writes = [];
        const trace = [];
        let reopened = false;
        let reads = 0;
        const transport = windowName => async ({ operation: name, payload }) => {
          if (name === 'market.listLocalImportRevisions') {
            return { result: { version: 1, revisions: [], next_after_revision_id: null } };
          }
          const reply = await dispatch(payload);
          const snapshot = windowName === 'first' && payload.type === 'snapshot';
          const number = snapshot && reply.ok === true ? ++reads : undefined;
          if (snapshot && number === undefined) trace.push({ snapshotRejected: reply.error?.code });
          if (payload.type === 'workspace_source') {
            writes.push({ window: windowName, request: payload, result: reply.result });
            if (windowName === 'first' && scenario.heldWrite) await operation.promise;
          }
          if (number !== undefined) {
            snapshots.push({ number, selection: reply.result.selection_revision, source: reply.result.source });
            if (scenario.recover && number === 2) await initialReadback.promise;
            if (number === readbackNumber && !scenario.heldWrite) await operation.promise;
            if (number === readbackNumber + 1) await trailing.promise;
            trace.push({ deliveredSnapshot: number });
          }
          if (windowName === 'first' && scenario.startsWithA && !reopened && payload.type === 'frozen_chart') {
            reopened = true;
            await reopen.promise;
            trace.push({ deliveredOriginalChart: reply.result?.bars[0]?.bar.close });
          }
          return reply;
        };

        const selectedBar = view => view.locator('.plot-workspace [aria-label="Selected bar"]');
        const dialog = view => view.getByRole('dialog', { name: 'Open published CSV' });
        const prepare = async (view, id, close, sourceFree = false) => {
          if (!sourceFree) await openPanel(view, 'Data sources');
          const entry = sourceFree ? view.getByRole('region', { name: 'Price chart' })
            : view.getByRole('complementary', { name: 'Data sources' });
          await control(entry, 'Open published CSV').click({ timeout: 8000 });
          const drawer = dialog(view);
          await drawer.getByLabel('Revision ID').fill(id);
          await control(drawer, 'Open frozen chart').click();
          await expect(drawer.getByLabel('Selected bar')).toContainText(`C ${close}`);
          return control(drawer, 'Use as workspace source');
        };
        const focus = count => page.evaluate(count => {
          if (document.visibilityState !== 'visible') throw new Error('The first workspace must be visible at focus.');
          for (let i = 0; i < count; i++) window.dispatchEvent(new Event('focus'));
        }, count);
        const captured = number => snapshots.find(row => row.number === number);
        const firstChart = selectedBar(page);
        let peer;
        try {
          if (scenario.recover) await page.clock.install();
          await nativeTransport(page, { optedIn: true }, transport('first'));
          peer = await page.context().newPage();
          await nativeTransport(peer, { optedIn: true }, transport('peer'));
          await page.goto('/');
          if (scenario.startsWithA) await expect.poll(() => reopened).toBe(true);
          await (await prepare(page, a, 44, !scenario.startsWithA)).click();
          await expect.poll(() => writes.find(write => write.window === 'first')).toMatchObject({
            request: { expected_selection_revision: originalRevision, source: { revision_id: a } },
            result: { selection_revision: savedRevision },
          });
          if (scenario.recover) {
            await expect.poll(() => captured(2)).toMatchObject({ selection: savedRevision, source: { revision_id: a } });
            await page.clock.fastForward(16_000);
            const reload = control(dialog(page), 'Reload workspace state');
            await expect(reload).toBeEnabled();
            await expect(dialog(page).getByRole('alert')).toContainText('The source was saved.');
            await focus(1);
            expect(reads).toBe(2);
            await reload.click();
          }
          if (!scenario.heldWrite) {
            await expect.poll(() => captured(readbackNumber)).toMatchObject({
              selection: savedRevision, source: { revision_id: a },
            });
          }

          await peer.goto('/');
          await expect(selectedBar(peer)).toContainText('C 44');
          if (!scenario.unchanged) {
            await (await prepare(peer, b, 106)).click();
            await expect(dialog(peer)).toHaveCount(0);
            await expect(selectedBar(peer)).toContainText('C 106');
          }
          if (scenario.contendProof) {
            const holder = await holdRevisionStatus(currentId, event => trace.push({ revisionGate: event }));
            try {
              const refused = await dispatchResponse({ type: 'snapshot' });
              trace.push({ postReadbackSnapshotRefused: refused.body.error?.code, status: refused.status });
              expect(preAdmissionBusy(refused), 'The proof is refused before admission while the designated reply is held').toBe(true);
            } finally {
              holder.release();
            }
            expect(await holder.reply).toMatchObject({ ok: true, result: { available: true } });
          }
          const proof = await readProofSnapshot(dispatchResponse, outcome => trace.push({ proofSnapshot: outcome }));
          expect(proof).toMatchObject({ selection_revision: confirmedRevision, source: { revision_id: currentId } });

          await page.evaluate(() => {
            window.sourceSaveFocusBars = [];
            const observer = new MutationObserver(() => {
              const bar = document.querySelector('.plot-workspace [aria-label="Selected bar"]');
              if (bar) window.sourceSaveFocusBars.push(bar.textContent);
            });
            observer.observe(document.body, { subtree: true, childList: true, characterData: true });
            window.addEventListener('pagehide', () => observer.disconnect(), { once: true });
          });
          await focus(4);
          trace.push({ focusEvents: 4, inFlightSnapshots: reads });
          expect(reads).toBe(scenario.heldWrite ? 1 : readbackNumber);
          if (scenario.closeDialog) await control(dialog(page), 'Close').click();
          reopen.resolve();
          operation.resolve();
          await expect.poll(() => captured(readbackNumber + 1), { timeout: 4500 }).toMatchObject({
            selection: confirmedRevision, source: { revision_id: currentId },
          });
          await expect(firstChart).toHaveCount(0);
          if (scenario.heldWrite) {
            await expect(dialog(page).getByRole('alert')).toContainText('The workspace selection changed after this request was saved.');
          } else {
            await expect(dialog(page)).toHaveCount(0);
          }

          trailing.resolve();
          if (scenario.heldWrite) await control(dialog(page), 'Close').click();
          await expect(firstChart).toContainText(scenario.unchanged ? 'C 44' : 'C 106');
          if (scenario.recover) {
            initialReadback.resolve();
            await expect.poll(() => trace.some(row => row.deliveredSnapshot === 2)).toBe(true);
            await page.clock.runFor(50);
            await expect(firstChart).toContainText('C 106');
          }
          if (!scenario.unchanged) {
            expect(await page.evaluate(() => window.sourceSaveFocusBars.some(text => text.includes('C 44')))).toBe(false);
          }
          expect(reads).toBe(readbackNumber + 1);
          expect(writes.map(write => [write.window, write.result.selection_revision])).toEqual(
            scenario.unchanged ? [['first', savedRevision]] : [['first', savedRevision], ['peer', confirmedRevision]],
          );
          await expect(dialog(page)).toHaveCount(0);
        } finally {
          reopen.resolve();
          initialReadback.resolve();
          operation.resolve();
          trailing.resolve();
          const visible = page.isClosed() ? [] : await firstChart.allTextContents();
          const observed = page.isClosed() ? [] : await page.evaluate(() => window.sourceSaveFocusBars ?? []);
          const peerVisible = peer && !peer.isClosed() ? await selectedBar(peer).allTextContents() : [];
          await testInfo.attach('source-save-focus-ordering', {
            body: JSON.stringify({ scenario, a, b, reads, snapshots, writes, trace, visible, observed, peerVisible }, null, 2),
            contentType: 'application/json',
          });
          await peer?.close();
        }
      }, { gateStatus: Boolean(scenario.contendProof) });
    });
  });
}

test('native indicator status retries only a proven writer refusal', async ({ browser, baseURL }, testInfo) => {
  test.setTimeout(180_000);
  const repository = fileURLToPath(new URL('../../', import.meta.url));
  const binary = join(repository, 'target/debug/examples',
    process.platform === 'win32' ? 'local_import_bridge.exe' : 'local_import_bridge');
  await promisify(execFile)('cargo', ['build', '--locked', '-p', 'scopehub-desktop', '--example', 'local_import_bridge',
    '--features', 'portable-test-bridge', '--target-dir', 'target'], {
    cwd: repository, env: { ...process.env, CARGO_BUILD_JOBS: '2' }, timeout: 120_000,
  });
  const temp = await mkdtemp(join(tmpdir(), 'scopehub-native-indicator-'));
  const app = join(temp, 'app');
  const revision = randomUUID();
  const prices = [80, 80, 80].map((close, index) => {
    const time = Date.UTC(2026, 1, 3 + index);
    return { time_ms: time, trading_day: new Date(time).toISOString().slice(0, 10),
      open: close, high: close, low: close, close, volume: 10, is_closed: true };
  });
  const experiments = [];
  let storageBusy;
  let bridge;
  try {
    bridge = startLocalImportBridge(binary, app, temp, { workspace: true });
    expect((await bridge.request('execute_operation', { operation: null, payload: { type: 'snapshot' } })).result)
      .toMatchObject({ workspace: { id: 'main', revision: 0 } });
    await bridge.close();
    bridge = undefined;
    seedPublishedChart(join(app, 'workspace-dev', 'workspace.sqlite3'), revision, prices);
    bridge = startLocalImportBridge(binary, app, temp, { workspace: true });
    const snapshot = { operation: null, payload: { type: 'snapshot' } };
    const opened = await bridge.request('execute_operation', snapshot);
    expect(opened.result.workspace.revision).toBe(0);
    // A distinct SQLite writer is held after the native lane has opened.
    // Observe the actual application error, then reuse its bytes at the
    // admitted status response boundary without changing SQLite's mode.
    const lock = new DatabaseSync(join(app, 'workspace-dev', 'workspace.sqlite3'));
    try {
      lock.exec('BEGIN IMMEDIATE');
      const contention = await bridge.request('execute_operation', {
        operation: null,
        payload: { type: 'workspace_source', request_id: randomUUID(),
          expected_selection_revision: 0, source: { kind: 'none' } },
      });
      expect(contention).toEqual({ error: {
        code: 'BUSY', retryable: true, message: 'The market worker is busy.', details: {},
      } });
      storageBusy = contention.error;
    } finally {
      lock.close();
    }
    expect(await bridge.request('execute_operation', snapshot)).toEqual(opened);
    const { details, ...legacyBusy } = storageBusy;

    for (const trial of [
      { name: 'proven refusal', details: { workspace_admission: 'refused' }, retry: true },
      { name: 'admitted BUSY', details: {} },
      { name: 'older unclassified BUSY' },
      { name: 'nonretryable refusal shape', details: { workspace_admission: 'refused' }, retryable: false },
    ]) {
      const page = await browser.newPage();
      const observation = { trial: trial.name, statuses: [], commands: [] };
      experiments.push(observation);
      try {
        const calls = await nativeTransport(page, { optedIn: true }, async args => {
          const { operation, payload } = args;
          if (payload.type === 'indicator_preview_status') {
            const entry = { payload };
            observation.statuses.push(entry);
            if (observation.statuses.length === 1 && trial.retry) {
              entry.refusedBeforeForwarding = true;
              return { error: { ...storageBusy, details: trial.details } };
            }
            const reply = await bridge.request('execute_operation', args);
            entry.admitted = reply;
            if (reply.result?.status === 'ready' && !trial.retry
              && !observation.statuses.some(status => status.substituted)) {
              entry.substituted = { ...legacyBusy,
                ...(trial.details && { details: trial.details }),
                ...(trial.retryable === false && { retryable: false }) };
              return { error: entry.substituted };
            }
            return reply;
          }
          const reply = await bridge.request('execute_operation', args);
          if (['indicator.createDraft', 'indicator.runPreview', 'task.cancel'].includes(operation)) {
            observation.commands.push({ operation, payload, reply });
          }
          return reply;
        });
        await page.goto(baseURL);
        await control(page, 'Open published CSV').click();
        const dialog = page.getByRole('dialog', { name: 'Open published CSV' });
        await dialog.getByLabel('Revision ID').fill(revision);
        await control(dialog, 'Open frozen chart').click();
        const panel = dialog.getByRole('region', { name: 'Indicator preview' });
        const run = control(panel, 'Run preview');
        await run.click();
        await expect(panel.getByText(/Preview layers/).or(panel.getByRole('alert'))).toBeVisible();
        const result = observation.statuses.find(status => status.admitted?.result?.status === 'ready');
        expect(result.admitted.result.event.artifact.result.series[1].points.at(-1))
          .toEqual({ time_ms: prices.at(-1).time_ms, value: 0 });
        const task = observation.commands.find(command => command.operation === 'indicator.runPreview')
          .reply.result.outcome.result.task_id;
        const statuses = observation.statuses.map(status => status.payload);
        expect(statuses.every(status => status.task_id === task)).toBe(true);
        expect(new Set(statuses.map(status => status.request_id)).size).toBe(statuses.length);
        if (trial.retry) {
          expect(observation.statuses[0].refusedBeforeForwarding).toBe(true);
          expect(observation.statuses.at(-1)).toBe(result);
          expect(calls.filter(call => call.args.operation === 'task.cancel')).toHaveLength(0);
          if (!await panel.getByRole('table').isVisible()) await panel.getByText('Preview provenance and values').click();
          await expect(panel.getByRole('table').locator('tbody tr').last().getByRole('cell').last()).toHaveText('0');
          expect(observation.commands.map(command => command.operation)).toEqual(['indicator.createDraft', 'indicator.runPreview']);
        } else {
          await expect(panel.getByRole('alert')).toHaveText('The workspace is busy. Try the preview again.', { timeout: 1_500 });
          await expect(panel.getByText(/Preview layers/)).toHaveCount(0);
          await expect.poll(() => observation.commands.at(-1)?.operation).toBe('task.cancel');
          expect(observation.statuses.at(-1)).toBe(result);
          expect(result.substituted).toBeDefined();
          expect(observation.commands.map(command => command.operation))
            .toEqual(['indicator.createDraft', 'indicator.runPreview', 'task.cancel']);
          expect(observation.commands.at(-1)).toMatchObject({
            payload: { task_id: task }, reply: { result: { task_id: task, outcome: { status: 'completed' } } },
          });
        }
      } finally {
        await page.close();
      }
    }
  } finally {
    await bridge?.close();
    await rm(temp, { recursive: true, force: true });
    await testInfo.attach('native-indicator-journal', { body: JSON.stringify({ storageBusy, experiments }), contentType: 'application/json' });
  }
});
