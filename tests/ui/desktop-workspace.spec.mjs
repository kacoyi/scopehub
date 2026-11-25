import { randomUUID } from 'node:crypto';
import { expect, test } from '@playwright/test';
import { buildPreview, seedDisplayOnlyChart, withProfile, withWorkspaceTransport } from './ai-workflow-fixture.mjs';
import { control, leaveSample } from './workspace-controls.mjs';

const workspaceId = 'main';
const operationId = 'ce7fd52d-7a62-441f-897f-098098e6fe82';
const chart = { kind: 'chart', object_id: 'main-chart' };
const undo = { operation_id: operationId, committed_workspace_revision: 1 };

function revisionStatus(dispatch, revisionId) {
  return dispatch({ type: 'frozen_chart_status', request_id: randomUUID(), revision_id: revisionId });
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

async function nativeTransport(page, { optedIn, native = true, textModel = false }, use) {
  const calls = [];
  await page.exposeBinding('dispatchNativeWorkspace', async (_, command, args) => {
    calls.push({ command, args });
    expect(command).toBe('execute_operation');
    expect(Object.keys(args).sort()).toEqual(args.operation === 'market.listLocalImportRevisions'
      ? ['operation', 'payload', 'requestId'] : ['operation', 'payload']);
    return use(args);
  });
  await page.addInitScript(({ optedIn, native, textModel }) => {
    window.isTauri = native;
    if (optedIn) Object.defineProperty(window, '__SCOPEHUB_DESKTOP_WORKSPACE__', { value: true });
    if (textModel) Object.defineProperty(window, '__SCOPEHUB_DESKTOP_TEXT_MODEL__', { value: true });
    window.__TAURI_INTERNALS__ = {
      async invoke(command, args) {
        const reply = await window.dispatchNativeWorkspace(command, args);
        if (reply.error) throw reply.error;
        return reply.result;
      },
    };
  }, { optedIn, native, textModel });
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
    await withWorkspaceTransport(profile, async ({ dispatch, holdRevisionRead }) => {
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
        const held = await holdRevisionRead(() => revisionStatus(dispatch, revision));
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

test('a revision gate preserves rejected and thrown errors before admission', async () => {
  test.setTimeout(180_000);
  await buildPreview();
  await withProfile(profile => withWorkspaceTransport(profile, async ({ dispatch, holdRevisionRead }) => {
    await test.step('propagates the original code promptly', async () => {
      await expect(holdRevisionRead(() => revisionStatus(dispatch, 'invalid'))).rejects.toMatchObject({ code: 'INVALID_ARGUMENT' });
    }, { timeout: 3000 });
    await test.step('propagates a thrown read error promptly', async () => {
      const failure = new Error('Synthetic revision dispatch failure');
      await expect(holdRevisionRead(() => { throw failure; })).rejects.toBe(failure);
    }, { timeout: 3000 });
    const held = await holdRevisionRead(() => revisionStatus(dispatch, randomUUID()));
    held.release();
    expect(await held.reply).toEqual({ ok: true, result: { available: false } });
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
