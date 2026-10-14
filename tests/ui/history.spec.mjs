import { test as base, expect } from '@playwright/test';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const fixtures = JSON.parse(await readFile('tests/fixtures/market/series.json', 'utf8')).fixtures;
const instruments = [fixtures[0].instrument, fixtures[3].instrument, fixtures[6].instrument];

// Only the native WebView transport is replaced. Normal requests run through
// the actual Rust registry, child-process host and Python provider.
const test = base.extend({
  backend: async ({ page }, use) => {
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
    page.on('request', request => { if (!request.url().startsWith('http://127.0.0.1:1420/')) network.push(request.url()); });
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
  await page.getByRole('button', { name: 'Explore offline demo' }).click();
  await expect(page.locator('.history')).toHaveAttribute('data-bars', '40');
}

async function painted(page, style) {
  // Verify actual canvas marks, not just an element or a fixture label.
  return page.locator('.chart canvas').evaluateAll((canvases, style) => {
    let found = 0;
    const colors = style === 'line' ? [[115,184,246]] : [[67,212,172],[241,125,133]];
    for (const canvas of canvases) {
      const context = canvas.getContext('2d');
      if (!context) continue;
      const values = context.getImageData(0,0,canvas.width,canvas.height).data;
      for (let i = 0; i < values.length; i += 4) {
        if (colors.some(rgb => rgb.every((v,k) => Math.abs(values[i+k]-v) < 5))) found++;
      }
    }
    return found;
  }, style);
}

test('startup makes no calls; all nine histories render both chart styles and retain sample semantics', async ({ page, backend }, testInfo) => {
  await page.goto('/');
  await expect(page.getByRole('status')).toHaveText('No services configured');
  await expect(page.locator('canvas')).toHaveCount(0);
  expect(backend.calls).toHaveLength(0);
  await page.screenshot({ path: testInfo.outputPath('unconfigured.png'), fullPage: true });
  await page.getByRole('button', { name: 'Explore offline demo' }).click();
  for (const instrument of instruments) {
    await page.getByRole('button', { name: instrument.id.replace('SIM.', '') }).click();
    for (const timeframe of ['1m','5m','1d']) {
      await page.getByRole('button', { name: timeframe, exact: true }).click();
      const expected = fixtures.find(item => item.scenario === 'normal' && item.instrument.id === instrument.id && item.history.dataset.timeframe === timeframe).history;
      const history = page.locator('.history');
      await expect(history).toHaveAttribute('data-instrument', instrument.id);
      await expect(history).toHaveAttribute('data-timeframe', timeframe);
      await expect(history).toHaveAttribute('data-bars', String(expected.bars.length));
      await expect(page.getByText('Final bar is partial', { exact: true })).toBeVisible();
      for (const style of ['candlestick','line']) {
        await page.getByRole('button', { name: style === 'line' ? 'Close-price line' : 'Candles', exact: true }).click();
        await expect(page.locator('.chart[role="img"]')).toHaveAccessibleName(new RegExp(instrument.id));
        await expect.poll(() => painted(page, style)).toBeGreaterThan(20);
      }
      await page.getByText(`Inspect ${expected.bars.length} bars`, { exact:true }).click();
      const rows = await page.locator('tbody tr').evaluateAll(rows => rows.map(row => [...row.cells].map(cell => cell.textContent)));
      expect(rows).toEqual(expected.bars.map(bar => [new Date(bar.time_ms).toISOString(), bar.trading_day, ...['open','high','low','close'].map(key => String(bar[key])), bar.volume === null ? 'Unknown' : String(bar.volume), bar.is_closed ? 'Closed' : 'Partial']));
    }
  }
  await page.screenshot({ path: testInfo.outputPath('history-line.png'), fullPage: true });
  await page.getByRole('button', { name:'Candles', exact:true }).click();
  await page.getByRole('button', { name:'1m', exact:true }).click();
  await expect.poll(() => painted(page, 'candlestick')).toBeGreaterThan(20);
  await page.screenshot({ path: testInfo.outputPath('history-candles.png'), fullPage: true });
  await page.getByLabel('Find instrument').fill('NO MATCH');
  await expect(page.getByText('No matching instruments.')).toBeVisible();
  await page.getByRole('button', { name:'Leave demo' }).click();
  await expect(page.locator('canvas')).toHaveCount(0);
  await expect(page.getByRole('status')).toHaveText('No services configured');
});

test('late response cannot overwrite a later selection; only the latest pending request runs', async ({ page, backend }) => {
  await enter(page);
  let release;
  const held = new Promise(resolve => { release = resolve; });
  let slow = false;
  backend.fault(async (args, real) => {
    if (args.payload.instrument_id === instruments[1].id) { slow = true; await held; }
    return real(args);
  });
  await page.getByRole('button', { name:'CN.STOCK.001' }).click();
  await expect.poll(() => slow).toBe(true);
  await page.getByRole('button', { name:'5m', exact:true }).click();
  await page.getByRole('button', { name:'1d', exact:true }).click();
  await page.getByRole('button', { name:'US.STOCK.001' }).click();
  await expect(page.locator('.history')).toHaveCount(0);
  await expect(page.getByText('Loading synthetic history…')).toBeVisible();
  release();
  await expect(page.locator('.history')).toHaveAttribute('data-instrument', instruments[2].id);
  await expect(page.locator('.history')).toHaveAttribute('data-timeframe', '1d');
  expect(backend.calls.filter(call => call.payload.instrument_id === instruments[1].id)).toHaveLength(1);
  backend.fault(null);
});

test('empty history, sanitized failures, identity rejection and leaving during a request', async ({ page, backend }) => {
  await enter(page);
  backend.fault(async (args, real) => {
    const reply = await real(args);
    if (args.operation === 'market.getBars') reply.result.bars = [];
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
  await page.getByRole('button', { name:'Retry history' }).click();
  await expect.poll(() => waiting).toBe(true);
  await page.getByRole('button', { name:'Leave demo' }).click();
  release();
  await expect(page.getByRole('status')).toHaveText('No services configured');
  await expect(page.locator('canvas')).toHaveCount(0);
});
