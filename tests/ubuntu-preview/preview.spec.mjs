import { expect, test } from '@playwright/test';
import { painted } from '../ui/chart-pixels.mjs';
import { observeTimeLabels } from './time-axis.mjs';

const endpoint = '/__scopehub/operation';

async function expectSeries(page, id, timeframe, bars) {
  await expect(page.getByRole('heading', { name: id, exact: true })).toBeVisible();
  const history = page.locator('.history');
  await expect(history).toHaveAttribute('data-instrument', id);
  await expect(history).toHaveAttribute('data-timeframe', timeframe);
  await expect(history).toHaveAttribute('data-bars', String(bars));
  await expect(page.getByText('Final bar is partial', { exact: true })).toBeVisible();
}

test('open the unconfigured workspace and inspect three synthetic histories', async ({ page, baseURL }, testInfo) => {
  const calls = [];
  const outside = [];
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    const url = new URL(request.url());
    if (url.pathname === endpoint) calls.push(request.postDataJSON());
    if (['http:', 'https:'].includes(url.protocol) && url.origin !== baseURL) outside.push(url.origin);
  });
  page.on('websocket', socket => {
    if (new URL(socket.url()).host !== new URL(baseURL).host) outside.push(socket.url());
  });

  await page.goto('/');
  const status = page.locator('header').getByRole('status');
  const entry = page.getByRole('button', { name: 'Explore offline demo' });
  await expect(status).toHaveText('No services configured');
  await expect(page.locator('canvas')).toHaveCount(0);
  await expect(entry).toBeVisible();
  expect(calls).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath('unconfigured.png'), fullPage: true });
  await page.keyboard.press('Tab');
  await expect(entry).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(status).toHaveText('Offline demo · Simulated');

  await page.getByRole('button', { name: /CN\.FUTURE\.001/ }).click();
  await expectSeries(page, 'SIM.CN.FUTURE.001', '1m', 40);
  await expect.poll(() => painted(page, 'candlestick')).toBeGreaterThan(20);
  await page.screenshot({ path: testInfo.outputPath('futures-candles.png'), fullPage: true });

  const search = page.getByLabel('Find instrument');
  await search.fill('no such symbol');
  await expect(page.getByText('No matching instruments.')).toBeVisible();
  await search.fill('cn.stock');
  await page.getByRole('button', { name: /CN\.STOCK\.001/ }).click();
  await page.getByRole('button', { name: '5m', exact: true }).click();
  await expectSeries(page, 'SIM.CN.STOCK.001', '5m', 8);
  await page.getByRole('button', { name: 'Close-price line' }).click();
  await expect.poll(() => painted(page, 'line')).toBeGreaterThan(20);

  await page.setViewportSize({ width: 390, height: 844 });
  await search.fill('us.stock');
  await search.press('Tab');
  await expect(page.getByRole('button', { name: /US\.STOCK\.001/ })).toBeFocused();
  await page.keyboard.press('Enter');
  await page.getByRole('button', { name: '1d', exact: true }).click();
  await expectSeries(page, 'SIM.US.STOCK.001', '1d', 2);
  await expect.poll(() => painted(page, 'line')).toBeGreaterThan(20);
  const details = page.getByText('Inspect 2 bars', { exact: true });
  await details.focus();
  await page.keyboard.press('Enter');
  const rows = page.getByRole('table').locator('tbody tr');
  await expect(rows).toHaveCount(2);
  await expect(rows.first().getByRole('cell')).toHaveText([
    '2024-01-03T14:30:00.000Z', '2024-01-03', '175', '175.02', '174.8', '174.85', 'Unknown', 'Closed',
  ]);
  await expect(rows.last().getByRole('cell')).toHaveText([
    '2024-01-04T14:30:00.000Z', '2024-01-04', '174.85', '174.91', '174.68', '174.72', '2600', 'Partial',
  ]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('stock-line-narrow.png'), fullPage: true });
  await page.getByRole('button', { name: 'Leave demo' }).click();
  await expect(status).toHaveText('No services configured');
  await expect(entry).toBeVisible();
  await expect(page.locator('canvas')).toHaveCount(0);

  expect([...new Set(calls.map(call => call.operation))].sort()).toEqual(['instrument.search', 'market.getBars']);
  const reads = calls.filter(call => call.operation === 'market.getBars').map(call => call.payload);
  for (const [id, timeframe] of [['SIM.CN.FUTURE.001', '1m'], ['SIM.CN.STOCK.001', '5m'], ['SIM.US.STOCK.001', '1d']]) {
    expect(reads).toContainEqual(expect.objectContaining({ provider_id: 'scopehub.synthetic', instrument_id: id, timeframe }));
  }
  expect(errors).toEqual([]);
  expect(outside).toEqual([]);
});

test('retry the catalog and current history after the browser loses its local connection', async ({ page, context }) => {
  const alert = page.getByRole('alert');
  const chart = page.locator('.chart canvas');
  async function expectRetryGuidance() {
    await expect(alert).toContainText(/local preview.*retry/i);
    await expect(alert).not.toContainText(/restart/i);
  }
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Explore offline demo' })).toBeVisible();
  await context.setOffline(true);
  await page.getByRole('button', { name: 'Explore offline demo' }).click();
  await expectRetryGuidance();
  await expect(chart).toHaveCount(0);

  await context.setOffline(false);
  await page.getByRole('button', { name: 'Retry demo' }).click();
  await expectSeries(page, 'SIM.CN.FUTURE.001', '1m', 40);
  await context.setOffline(true);
  await page.getByRole('button', { name: '5m', exact: true }).click();
  await expectRetryGuidance();
  await expect(chart).toHaveCount(0);
  await expect(page.locator('.history')).toHaveCount(0);

  await context.setOffline(false);
  await page.getByRole('button', { name: 'Retry history' }).click();
  await expectSeries(page, 'SIM.CN.FUTURE.001', '5m', 8);
  await expect(alert).toHaveCount(0);
  await expect.poll(() => painted(page, 'candlestick')).toBeGreaterThan(20);

  await page.getByRole('button', { name: /CN\.STOCK\.001/ }).click();
  await page.getByRole('button', { name: '1d', exact: true }).click();
  await page.getByRole('button', { name: /US\.STOCK\.001/ }).click();
  await expectSeries(page, 'SIM.US.STOCK.001', '1d', 2);
  await page.getByText('Inspect 2 bars', { exact: true }).click();
  const rows = page.getByRole('table').locator('tbody tr');
  await expect(rows).toHaveCount(2);
  await expect(rows.last().getByRole('cell')).toHaveText([
    '2024-01-04T14:30:00.000Z', '2024-01-04', '174.85', '174.91', '174.68', '174.72', '2600', 'Partial',
  ]);
});

test('time axis labels fit the chart and the intraday plot can zoom and pan', async ({ page }, testInfo) => {
  await page.addInitScript(observeTimeLabels);
  await page.goto('/');
  await page.getByRole('button', { name: 'Explore offline demo' }).click();
  await page.getByRole('button', { name: /CN\.FUTURE\.001/ }).click();
  await expectSeries(page, 'SIM.CN.FUTURE.001', '1m', 40);
  const ticks = () => page.evaluate(() => window.readTimeLabels().ticks);
  async function expectReadable() {
    await expect.poll(async () => (await ticks()).length).toBeGreaterThanOrEqual(2);
    const labels = await ticks();
    expect(labels.filter((label, index) =>
      label.left < -1 || label.right > label.width + 1 || (index > 0 && label.left < labels[index - 1].right - 1),
    )).toEqual([]);
    return labels;
  }

  await page.screenshot({ path: testInfo.outputPath('minute-axis-desktop.png'), fullPage: true });
  const fitted = await expectReadable();
  const chart = page.getByRole('img', { name: /Candlestick and volume chart/ });
  await chart.scrollIntoViewIfNeeded();
  const box = await chart.boundingBox();
  const x = box.x + box.width * 0.5;
  const y = box.y + box.height * 0.25;
  await page.mouse.move(x, y);
  await expect.poll(() => page.evaluate(() => window.readTimeLabels().crosshair)).toContainEqual(
    expect.stringMatching(/^\d{2} Jan, \d{2}:\d{2}$/),
  );
  await page.mouse.wheel(0, -400);
  await expect.poll(ticks).not.toEqual(fitted);
  const zoomed = await ticks();
  await page.mouse.down();
  await page.mouse.move(x + box.width * 0.2, y, { steps: 10 });
  await page.mouse.up();
  await expect.poll(ticks).not.toEqual(zoomed);

  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: '5m', exact: true }).click();
  await expectSeries(page, 'SIM.CN.FUTURE.001', '5m', 8);
  await expectReadable();
  await page.screenshot({ path: testInfo.outputPath('minute-axis-narrow.png'), fullPage: true });
  await page.getByRole('button', { name: '1d', exact: true }).click();
  await expectSeries(page, 'SIM.CN.FUTURE.001', '1d', 2);
  const daily = await expectReadable();
  expect(daily.map(label => label.text)).toEqual(['2024-01-03', '2024-01-04']);
});

test('the loopback endpoint limits callers, operations and providers and reports ordinary read errors', async ({ request, baseURL }) => {
  const headers = { Origin: baseURL };
  const search = { operation: 'instrument.search', payload: { query: 'US.STOCK', limit: 1 } };
  const missing = {
    operation: 'market.getBars',
    payload: {
      instrument_id: 'SIM.US.STOCK.MISSING', provider_id: 'scopehub.synthetic', timeframe: '1d',
      from_ms: Date.UTC(2024, 0, 1), to_ms: Date.UTC(2024, 0, 6),
    },
  };
  for (const [name, options, status] of [
    ['read-only HTTP method', { method: 'GET', headers }, 405],
    ['missing origin', { data: search }, 403],
    ['different loopback hostname', { headers: { ...headers, Host: 'localhost' }, data: search }, 403],
    ['plain text instead of JSON', { headers: { ...headers, 'Content-Type': 'text/plain' }, data: 'preview' }, 415],
    ['request above 16 KiB', { headers, data: { operation: 'instrument.search', payload: { query: 'a'.repeat(16 * 1024), limit: 1 } } }, 413],
    ['another registry read', { headers, data: { operation: 'workspace.getContext', payload: {} } }, 403],
    ['unconfigured provider', { headers, data: { ...missing, payload: { ...missing.payload, provider_id: 'other.local' } } }, 403],
  ]) {
    await test.step(name, async () => {
      const response = await request.fetch(endpoint, { method: 'POST', ...options });
      expect(response.status()).toBe(status);
      expect(await response.json()).toMatchObject({ ok: false, error: { code: expect.any(String) } });
    });
  }
  const unavailable = await request.post(endpoint, { headers, data: missing });
  expect(unavailable.status()).toBe(200);
  expect(await unavailable.json()).toMatchObject({ ok: false, error: { code: 'DATA_UNAVAILABLE' } });
  const found = await request.post(endpoint, { headers, data: search });
  expect(found.status()).toBe(200);
  expect(await found.json()).toMatchObject({ ok: true, result: { instruments: [{ id: 'SIM.US.STOCK.001' }] } });
});
