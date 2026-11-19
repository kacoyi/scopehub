import { test as base, expect } from '@playwright/test';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { painted } from './chart-pixels.mjs';
import { control, leaveSample, openPanel, watchlistSymbol } from './workspace-controls.mjs';

const fixtures = JSON.parse(await readFile('tests/fixtures/market/series.json', 'utf8')).fixtures;
const instruments = [fixtures[0].instrument, fixtures[3].instrument, fixtures[6].instrument];

// Only the native WebView transport is replaced. Normal requests run through
// the actual Rust registry, child-process host and Python provider.
const test = base.extend({
  backend: async ({ page, baseURL }, use) => {
    const executable = resolve(`target/debug/examples/history_bridge${process.platform === 'win32' ? '.exe' : ''}`);
    const child = spawn(executable, [], { stdio: ['pipe', 'pipe', 'pipe'] });
    const exited = new Promise(resolve => child.once('close', resolve));
    const lines = createInterface({ input: child.stdout });
    const pending = [];
    const calls = [];
    const errors = [];
    const network = [];
    let fault = null;
    let stderr = '';
    let active = 0;
    let maximumActive = 0;
    child.stderr.on('data', data => { stderr += data; });
    child.on('error', error => { for (const item of pending.splice(0)) item.reject(error); });
    child.on('exit', () => { for (const item of pending.splice(0)) item.reject(new Error(`History bridge exited: ${stderr}`)); });
    lines.on('line', line => pending.shift()?.resolve(JSON.parse(line)));
    const real = args => new Promise((resolve, reject) => {
      pending.push({ resolve, reject });
      child.stdin.write(JSON.stringify(args) + '\n');
    });
    page.on('pageerror', error => errors.push(String(error)));
    page.on('request', request => { if (new URL(request.url()).origin !== baseURL) network.push(request.url()); });
    await page.exposeBinding('nativeOperation', async (_, command, args) => {
      expect(command).toBe('execute_operation');
      calls.push(structuredClone(args));
      active++; maximumActive = Math.max(active, maximumActive);
      try { return fault ? await fault(args, real) : await real(args); }
      finally { active--; }
    });
    await page.addInitScript(() => {
      window.__TAURI_INTERNALS__ = { invoke: async (command, args) => {
        const reply = await window.nativeOperation(command, args);
        if (!reply.ok) throw reply.error;
        return reply.result;
      } };
    });
    await use({ calls, fault: value => { fault = value; }, real });
    child.stdin.end();
    const deadline = setTimeout(() => child.kill(), 6000);
    await exited;
    clearTimeout(deadline);
    lines.close();
    expect(child.exitCode, stderr).toBe(0);
    expect(pending).toHaveLength(0);
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
