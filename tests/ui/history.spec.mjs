import { test as base, expect } from '@playwright/test';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { painted } from './chart-pixels.mjs';
import { control, leaveSample, openPanel, watchlistSymbol } from './workspace-controls.mjs';

const fixtures = JSON.parse(await readFile('tests/fixtures/market/series.json', 'utf8')).fixtures;
const instruments = [fixtures[0].instrument, fixtures[3].instrument, fixtures[6].instrument];

function historyBridge(executable) {
  const child = spawn(executable, [], { stdio: ['pipe', 'pipe', 'pipe'] });
  const exited = new Promise(resolve => child.once('close', resolve));
  const lines = createInterface({ input: child.stdout });
  const pending = [];
  let stderr = '';
  child.stderr.on('data', data => { stderr += data; });
  child.on('error', error => { for (const item of pending.splice(0)) item.reject(error); });
  child.on('exit', () => { for (const item of pending.splice(0)) item.reject(new Error(`History bridge exited: ${stderr}`)); });
  lines.on('line', line => pending.shift()?.resolve(JSON.parse(line)));
  return {
    read: args => new Promise((resolve, reject) => {
      pending.push({ resolve, reject });
      child.stdin.write(JSON.stringify(args) + '\n');
    }),
    async close() {
      child.stdin.end();
      const deadline = setTimeout(() => child.kill(), 6000);
      await exited;
      clearTimeout(deadline);
      lines.close();
      expect(child.exitCode, stderr).toBe(0);
      expect(pending).toHaveLength(0);
    },
  };
}

// Only the native WebView transport is replaced. Both read paths use the
// actual Rust registry, child-process host and Python provider.
const test = base.extend({
  backend: async ({ page, baseURL }, use) => {
    const executable = resolve(`target/debug/examples/history_bridge${process.platform === 'win32' ? '.exe' : ''}`);
    const ordinary = historyBridge(executable);
    let preparation;
    const calls = [];
    const commands = [];
    const errors = [];
    const network = [];
    let fault = null;
    let prepare = async (_args, read, started) => { await started(); return read(); };
    let active = 0;
    let maximumActive = 0;
    page.on('pageerror', error => errors.push(String(error)));
    page.on('request', request => { if (new URL(request.url()).origin !== baseURL) network.push(request.url()); });
    await page.exposeBinding('nativeOperation', async (_, command, args) => {
      commands.push({ command, args });
      if (command === 'cancel_market_preparation') return { ok: true };
      if (command === 'prepare_market_subscription') {
        const read = () => {
          preparation ??= historyBridge(executable);
          return preparation.read({ operation: 'market.readHistoryPage', payload: args.payload });
        };
        const started = () => page.evaluate(id => window.startNativePreparation(id), args.progress);
        return prepare(args, read, started);
      }
      expect(command).toBe('execute_operation');
      calls.push(structuredClone(args));
      active++; maximumActive = Math.max(active, maximumActive);
      try { return fault ? await fault(args, ordinary.read) : await ordinary.read(args); }
      finally { active--; }
    });
    await page.addInitScript(() => {
      const callbacks = new Map();
      let nextId = 0;
      window.startNativePreparation = id => callbacks.get(id)({ index: 0, message: { started: true } });
      window.nativePreparationReplies = [];
      window.__TAURI_INTERNALS__ = {
        transformCallback(callback) {
          const id = ++nextId;
          callbacks.set(id, callback);
          return id;
        },
        unregisterCallback: id => callbacks.delete(id),
        async invoke(command, args) {
          const callbackId = args.progress?.id;
          const reply = await window.nativeOperation(command, callbackId === undefined ? args : { ...args, progress: callbackId });
          if (callbackId !== undefined) callbacks.delete(callbackId);
          if (command === 'prepare_market_subscription') window.nativePreparationReplies.push(args.preparationId);
          if (!reply.ok) throw reply.error;
          return reply.result;
        },
      };
    });
    await use({ calls, commands, fault: value => { fault = value; }, prepare: value => { prepare = value; } });
    await Promise.all([ordinary.close(), preparation?.close()]);
    expect(maximumActive).toBeLessThanOrEqual(1);
    expect(errors).toEqual([]);
    expect(network).toEqual([]);
  },
});

async function enter(page) {
  await page.goto('/');
  await control(page, 'Open offline sample').click();
  await expect(page.locator('.history')).toHaveAttribute('data-bars', '40');
}

test('startup makes no calls; all nine histories render both chart styles and retain sample semantics', async ({ page, backend }, testInfo) => {
  await page.goto('/');
  await expect(control(page, 'Open offline sample')).toBeVisible();
  await expect(page.getByText('No data source', { exact: true }).first()).toBeVisible();
  await expect(control(page, 'Search symbols')).toBeVisible();
  await expect(page.locator('.chart canvas')).toHaveCount(0);
  expect(backend.calls).toHaveLength(0);
  await page.screenshot({ path: testInfo.outputPath('unconfigured.png'), fullPage: true });
  await control(page, 'Open offline sample').click();
  for (const instrument of instruments) {
    await openPanel(page, 'Watchlist');
    await watchlistSymbol(page, instrument.id).click();
    for (const timeframe of ['1m','5m','1d']) {
      await page.getByRole('button', { name: timeframe, exact: true }).click();
      const expected = fixtures.find(item => item.scenario === 'normal' && item.instrument.id === instrument.id && item.history.dataset.timeframe === timeframe).history;
      const history = page.locator('.history');
      await expect(history).toHaveAttribute('data-instrument', instrument.id);
      await expect(history).toHaveAttribute('data-timeframe', timeframe);
      await expect(history).toHaveAttribute('data-bars', String(expected.bars.length));
      await expect(page.locator('.bar-detail')).toContainText(/partial/i);
      for (const style of ['candlestick','line']) {
        await page.getByRole('button', { name: style === 'line' ? 'Close-price line' : 'Candles', exact: true }).click();
        await expect(page.locator('.chart[role="img"]')).toHaveAccessibleName(new RegExp(instrument.id));
        await expect.poll(() => painted(page, style)).toBeGreaterThan(20);
      }
      await openPanel(page, 'Data window');
      const rows = await page.getByRole('table', { name: 'Historical bars' }).locator('tbody tr')
        .evaluateAll(rows => rows.map(row => [...row.cells].map(cell => cell.textContent)));
      expect(rows).toEqual(expected.bars.map(bar => [new Date(bar.time_ms).toISOString(), bar.trading_day, ...['open','high','low','close'].map(key => String(bar[key])), bar.volume === null ? 'Unknown' : String(bar.volume), bar.is_closed ? 'Closed' : 'Partial']));
    }
  }
  await openPanel(page, 'Watchlist');
  await page.screenshot({ path: testInfo.outputPath('history-line.png'), fullPage: true });
  await page.getByRole('button', { name:'Candles', exact:true }).click();
  await page.getByRole('button', { name:'1m', exact:true }).click();
  await expect.poll(() => painted(page, 'candlestick')).toBeGreaterThan(20);
  await page.screenshot({ path: testInfo.outputPath('history-candles.png'), fullPage: true });
  await control(page, 'Search symbols').click();
  const dialog = page.getByRole('dialog', { name: 'Search symbols' });
  await dialog.getByLabel('Search symbols', { exact: true }).fill('NO MATCH');
  await expect(dialog.getByText('No matching instruments.')).toBeVisible();
  await page.keyboard.press('Escape');
  await leaveSample(page);
  await expect(page.getByText('No data source', { exact: true }).first()).toBeVisible();
});

test('late response cannot overwrite a later selection; only the latest pending request runs', async ({ page, backend }) => {
  await enter(page);
  let release;
  const held = new Promise(resolve => { release = resolve; });
  let slow = false;
  backend.fault(async (args, real) => {
    if (args.payload.series?.instrument_id === instruments[1].id) { slow = true; await held; }
    return real(args);
  });
  await watchlistSymbol(page, instruments[1].id).click();
  await expect.poll(() => slow).toBe(true);
  await page.getByRole('button', { name:'5m', exact:true }).click();
  await page.getByRole('button', { name:'1d', exact:true }).click();
  await watchlistSymbol(page, instruments[2].id).click();
  await expect(page.locator('.history')).toHaveCount(0);
  await expect(page.getByRole('status').filter({ hasText: /loading.*history/i })).toBeVisible();
  release();
  await expect(page.locator('.history')).toHaveAttribute('data-instrument', instruments[2].id);
  await expect(page.locator('.history')).toHaveAttribute('data-timeframe', '1d');
  expect(backend.calls.filter(call => call.payload.series?.instrument_id === instruments[1].id)).toHaveLength(1);
  backend.fault(null);
});

for (const [timing, acknowledgeEarly] of [['before', false], ['after', true]]) {
  test(`native selection replaces a held replay preparation ${timing} its start acknowledgement`, async ({ page, backend }) => {
    await enter(page);
    const prepared = Promise.withResolvers();
    const acknowledge = Promise.withResolvers();
    const release = Promise.withResolvers();
    backend.prepare(async (args, read, started) => {
      try {
        if (acknowledgeEarly) await started();
        const reply = await read();
        prepared.resolve({ args, reply });
        await acknowledge.promise;
        if (!acknowledgeEarly) await started();
        await release.promise;
        return reply;
      } catch (error) {
        prepared.reject(error);
        throw error;
      }
    });

    const history = page.locator('.history');
    const cancellations = () => backend.commands.filter(({ command }) => command === 'cancel_market_preparation');
    let preparationId;
    try {
      await control(page, 'Play sample updates').click();
      const [{ args, reply }] = await Promise.all([
        prepared.promise, expect(page.locator('.replay-status')).toHaveText('Preparing synthetic replay…'),
      ]);
      ({ preparationId } = args);
      expect(args.payload).toMatchObject({ include_stream_position: true, series: { instrument_id: instruments[0].id, timeframe: '1m' } });
      expect(reply).toMatchObject({ ok: true, result: { stream_position: { sequence: 0 } } });

      await watchlistSymbol(page, instruments[1].id).click();
      await expect(history).toHaveAttribute('data-instrument', instruments[1].id, { timeout: 4_000 });
      await control(page, '5m').click();
      await expect(history).toHaveAttribute('data-timeframe', '5m', { timeout: 4_000 });
      await expect(history).toHaveAttribute('data-bars', '8');
      await expect(page.locator('.replay-status')).toHaveCount(0);
      if (!acknowledgeEarly) expect(cancellations()).toEqual([]);
      acknowledge.resolve();
      await expect.poll(cancellations).toEqual([{ command: 'cancel_market_preparation', args: { preparationId } }]);
      expect(await page.evaluate(() => window.nativePreparationReplies)).toEqual([]);
    } finally {
      acknowledge.resolve();
      release.resolve();
    }

    // Even if success crosses the cancellation in transit, it must not subscribe or paint.
    await expect.poll(() => page.evaluate(() => window.nativePreparationReplies)).toEqual([preparationId]);
    await expect(history).toHaveAttribute('data-instrument', instruments[1].id);
    await expect(history).toHaveAttribute('data-timeframe', '5m');
    await expect(page.locator('.replay-status')).toHaveCount(0);
    expect(backend.calls.slice(2).map(({ payload }) => [payload.series.instrument_id, payload.series.timeframe, payload.include_stream_position])).toEqual([
      [instruments[1].id, '1m', undefined], [instruments[1].id, '5m', undefined],
    ]);
    expect(backend.commands.filter(({ command }) => command === 'execute_market_subscription')).toEqual([]);
  });
}

test('empty history, sanitized failures, identity rejection and leaving during a request', async ({ page, backend }) => {
  await enter(page);
  backend.fault(async (args, real) => {
    const reply = await real(args);
    if (args.operation === 'market.readHistoryPage') { reply.result.bars = []; reply.result.total_bars = 0; }
    return reply;
  });
  await page.getByRole('button', { name:'5m', exact:true }).click();
  await expect(page.getByText('No bars in this sample range.')).toBeVisible();
  await expect(page.locator('canvas')).toHaveCount(0);
  backend.fault(async () => ({ok:false, error:{code:'WORKER_EXITED', message:'SECRET_NOT_FOR_UI'}}));
  await page.getByRole('button', { name:'1m', exact:true }).click();
  await expect(page.getByRole('alert')).toContainText('disconnected');
  await expect(page.getByText('SECRET_NOT_FOR_UI')).toHaveCount(0);
  backend.fault(null);
  await page.getByRole('button', { name:'Retry history' }).click();
  await expect(page.locator('.history')).toHaveAttribute('data-bars','40');
  backend.fault(async (args, real) => { const reply = await real(args); reply.result.dataset.instrument_id = 'OTHER'; return reply; });
  await page.getByRole('button', { name:'5m', exact:true }).click();
  await expect(page.getByRole('alert')).toContainText('failed validation');
  await expect(page.locator('canvas')).toHaveCount(0);
  let release;
  let waiting = false;
  const held = new Promise(resolve => { release = resolve; });
  backend.fault(async (args, real) => { waiting = true; await held; return real(args); });
  await expect(control(page, 'Retry history')).toHaveCount(0);
  await control(page, '1m').click();
  await expect.poll(() => waiting).toBe(true);
  await leaveSample(page);
  release();
  await expect(page.getByText('No data source', { exact: true }).first()).toBeVisible();
  await expect(page.locator('.chart canvas')).toHaveCount(0);
});

test('a fresh entry restores an explicitly selected sample and leaving clears the preference', async ({ page, backend }) => {
  await page.goto('/#demo');
  await expect(page.locator('.history')).toHaveAttribute('data-bars', '40');
  await watchlistSymbol(page, 'SIM.US.STOCK.001').click();
  await control(page, '1d').click();
  await control(page, 'Close-price line').click();
  await openPanel(page, 'Data window');
  await expect(page.locator('.history')).toHaveAttribute('data-bars', '2');
  await page.goto('/');
  await expect(page.locator('.history')).toHaveAttribute('data-instrument', 'SIM.US.STOCK.001');
  await expect(page.locator('.history')).toHaveAttribute('data-timeframe', '1d');
  await expect(control(page, 'Close-price line')).toHaveAttribute('aria-pressed', 'true');
  await expect(control(page, 'Data window')).toHaveAttribute('aria-expanded', 'true');
  await expect(page.getByText('Simulated', { exact: true })).toBeVisible();
  await leaveSample(page);
  const count = backend.calls.length;
  await page.goto('/');
  await expect(control(page, 'Open offline sample')).toBeVisible();
  expect(backend.calls).toHaveLength(count);
});

test('a busy read offers retry and a byte limit requires a changed request', async ({ page, backend }) => {
  await enter(page);
  backend.fault(async () => ({ ok: false, error: { code: 'BUSY', retryable: true } }));
  await control(page, '5m').click();
  await expect(page.getByRole('alert')).toContainText('busy');
  backend.fault(async () => ({ ok: false, error: { code: 'RESOURCE_LIMIT', retryable: false } }));
  await control(page, 'Retry history').click();
  await expect(page.getByRole('alert')).toContainText('narrower');
  await expect(control(page, 'Retry history')).toHaveCount(0);
});

test('a chart assembles every pinned page and discards a crossed continuation', async ({ page, backend }) => {
  let cross = false;
  backend.fault(async (args, real) => {
    if (args.operation !== 'market.readHistoryPage') return real(args);
    const reply = await real({ ...args, payload: { ...args.payload, page_size: 3 } });
    if (cross && args.payload.cursor && reply.ok) reply.result.snapshot.content_hash = '0'.repeat(64);
    return reply;
  });
  await enter(page);
  const pages = backend.calls.filter(call => call.operation === 'market.readHistoryPage');
  expect(pages).toHaveLength(14);
  expect(new Set(pages.slice(1).map(call => call.payload.snapshot.content_hash)).size).toBe(1);
  expect(pages.at(-1).payload.cursor.next_index).toBe(39);
  cross = true;
  await control(page, '5m').click();
  await expect(page.getByRole('alert')).toContainText('failed validation');
  await expect(page.locator('.chart canvas')).toHaveCount(0);
});

for (const [label, alter] of [
  ['dataset adjustment', result => { result.dataset.price_adjustment = 'split_adjusted'; }],
  ['cutoff', result => { result.quality_state.last_observed_ms = result.snapshot.as_of_ms + 1; }],
  ['cursor boundary', result => { result.next_cursor.previous_time_ms += 1_000; }],
  ['bar ordering', result => { [result.bars[1], result.bars[2]] = [result.bars[2], result.bars[1]]; }],
]) {
  test(`the desktop history client rejects a pinned page with a contradictory ${label}`, async ({ page, backend }, testInfo) => {
    backend.fault(async (args, real) => {
      if (args.operation !== 'market.readHistoryPage') return real(args);
      const reply = await real({ ...args, payload: { ...args.payload, page_size: 3 } });
      if (args.payload.cursor?.next_index === 3) {
        expect(reply.ok, 'Registry admitted the original continuation').toBe(true);
        expect(reply.result.offset).toBe(3);
        expect(reply.result.bars).toHaveLength(3);
        expect(reply.result.next_cursor.next_index).toBe(6);
        alter(reply.result);
      }
      return reply;
    });
    await page.goto('/#demo');
    const readOutcome = async () => ({
      alerts: await page.getByRole('alert').allTextContents(),
      historyCount: await page.locator('.history').count(),
      canvasCount: await page.locator('.chart canvas').count(),
      calls: backend.calls.map(({ operation, payload }) => ({ operation, offset: payload.cursor?.next_index ?? 0 })),
    });
    await expect.poll(async () => {
      const { alerts, historyCount } = await readOutcome();
      return alerts.length > 0 || historyCount > 0;
    }).toBe(true);
    const outcome = await readOutcome();
    await testInfo.attach('pinned-history-outcome', { contentType: 'application/json', body: JSON.stringify(outcome) });
    expect(outcome).toEqual({
      alerts: ['The data response failed validation.'], historyCount: 0, canvasCount: 0,
      calls: [
        { operation: 'instrument.search', offset: 0 },
        { operation: 'market.readHistoryPage', offset: 0 },
        { operation: 'market.readHistoryPage', offset: 3 },
      ],
    });
  });
}
