import { randomUUID } from 'node:crypto';
import { expect, test } from '@playwright/test';
import { control } from './workspace-controls.mjs';

const workspaceId = 'main';
const operationId = 'ce7fd52d-7a62-441f-897f-098098e6fe82';
const chart = { kind: 'chart', object_id: 'main-chart' };
const undo = { operation_id: operationId, committed_workspace_revision: 1 };

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

async function nativeTransport(page, { optedIn, native = true }, use) {
  const calls = [];
  await page.exposeBinding('dispatchNativeWorkspace', async (_, command, args) => {
    calls.push({ command, args });
    expect(command).toBe('execute_operation');
    expect(Object.keys(args).sort()).toEqual(['operation', 'payload']);
    return use(args);
  });
  await page.addInitScript(({ optedIn, native }) => {
    window.isTauri = native;
    if (optedIn) Object.defineProperty(window, '__SCOPEHUB_DESKTOP_WORKSPACE__', { value: true });
    window.__TAURI_INTERNALS__ = {
      async invoke(command, args) {
        const reply = await window.dispatchNativeWorkspace(command, args);
        if (reply.error) throw reply.error;
        return reply.result;
      },
    };
  }, { optedIn, native });
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
