import { setTimeout as delay } from 'node:timers/promises';
import { expect, test } from '@playwright/test';
import { painted } from '../ui/chart-pixels.mjs';
import { replayStage } from '../ui/replay-stages.mjs';
import { future, openDefaultSample } from '../ui/sample-startup.mjs';
import { control, leaveSample, openPanel, watchlistSymbol } from '../ui/workspace-controls.mjs';
import { observeTimeLabels } from './time-axis.mjs';

const endpoint = '/__scopehub/operation';

async function expectSeries(page, id, timeframe, bars) {
  const history = page.locator('.history');
  await expect(history).toHaveAttribute('data-instrument', id);
  await expect(history).toHaveAttribute('data-timeframe', timeframe);
  await expect(history).toHaveAttribute('data-bars', String(bars));
  await expect(page.locator('.chart[role="img"]')).toHaveAccessibleName(new RegExp(id.replaceAll('.', '\\.')));
}

test('open the chart workspace, find symbols from the keyboard and inspect bars in the side panel', async ({ page, baseURL }, testInfo) => {
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
  const entry = control(page, 'Open offline sample');
  await expect(entry).toBeVisible();
  await expect(page.getByText('No data source', { exact: true }).first()).toBeVisible();
  await expect(control(page, 'Search symbols')).toBeVisible();
  await expect(control(page, 'Watchlist')).toHaveAttribute('aria-expanded', 'true');
  await expect(page.locator('.chart canvas')).toHaveCount(0);
  expect(calls).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath('unconfigured.png'), fullPage: true });
  const stops = await page.locator('button:visible, a[href]:visible, input:visible, [tabindex="0"]:visible').count();
  for (let i = 0; i <= stops && !await entry.evaluate(el => el === document.activeElement); i++) {
    await page.keyboard.press('Tab');
  }
  await expect(entry).toBeFocused();
  await openDefaultSample(page, { trigger: () => page.keyboard.press('Enter') });
  await expectSeries(page, 'SIM.CN.FUTURE.001', '1m', 40);
  await expect(page.getByText('Offline sample', { exact: true }).first()).toBeVisible();
  await expect.poll(() => painted(page, 'candlestick')).toBeGreaterThan(20);
  const [plot, crosshair, symbols, watchlistRow] = await Promise.all([
    page.locator('.chart').boundingBox(), control(page, 'Crosshair').boundingBox(),
    control(page, 'Search symbols').boundingBox(), watchlistSymbol(page, 'SIM.CN.FUTURE.001').boundingBox(),
  ]);
  expect(symbols.y + symbols.height).toBeLessThanOrEqual(plot.y + 1);
  expect(crosshair.x + crosshair.width).toBeLessThanOrEqual(plot.x + 1);
  expect(watchlistRow.x).toBeGreaterThanOrEqual(plot.x + plot.width - 1);
  expect(plot.width).toBeGreaterThan(page.viewportSize().width / 2);
  await page.screenshot({ path: testInfo.outputPath('futures-candles.png'), fullPage: true });

  const searchButton = control(page, 'Search symbols');
  await searchButton.click();
  const dialog = page.getByRole('dialog', { name: 'Search symbols' });
  const input = dialog.getByLabel('Search symbols', { exact: true });
  await expect(input).toBeFocused();
  await input.fill('no such symbol');
  await expect(dialog.getByText('No matching instruments.')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await expect(searchButton).toBeFocused();
  await searchButton.press('Enter');
  await input.fill('stock');
  const options = dialog.getByRole('listbox').getByRole('option');
  await expect(options).toHaveCount(2);
  await expect(options.first()).toContainText('CN.STOCK.001');
  await expect(options.last()).toContainText('US.STOCK.001');
  await input.press('ArrowDown');
  await expect(options.last()).toHaveAttribute('aria-selected', 'true');
  await input.press('ArrowUp');
  await expect(options.first()).toHaveAttribute('aria-selected', 'true');
  await page.screenshot({ path: testInfo.outputPath('symbol-search.png'), fullPage: true });
  await input.press('Enter');
  await expect(dialog).toHaveCount(0);
  await control(page, '5m').click();
  await expectSeries(page, 'SIM.CN.STOCK.001', '5m', 8);
  await control(page, 'Close-price line').click();
  await expect.poll(() => painted(page, 'line')).toBeGreaterThan(20);

  await watchlistSymbol(page, 'SIM.US.STOCK.001').click();
  await control(page, '1d').click();
  await expectSeries(page, 'SIM.US.STOCK.001', '1d', 2);
  await expect.poll(() => painted(page, 'line')).toBeGreaterThan(20);
  const canvas = await page.locator('.chart canvas').first().elementHandle();
  const requestsBeforePanels = calls.length;
  await control(page, 'Data window').click();
  await expect(control(page, 'Watchlist')).toHaveAttribute('aria-expanded', 'false');
  await expect(control(page, 'Data window')).toHaveAttribute('aria-expanded', 'true');
  const rows = page.getByRole('table', { name: 'Historical bars' }).locator('tbody tr');
  await expect(rows).toHaveCount(2);
  await expect(rows.first().getByRole('cell')).toHaveText([
    '2024-01-03T14:30:00.000Z', '2024-01-03', '175', '175.02', '174.8', '174.85', 'Unknown', 'Closed',
  ]);
  await expect(rows.last().getByRole('cell')).toHaveText([
    '2024-01-04T14:30:00.000Z', '2024-01-04', '174.85', '174.91', '174.68', '174.72', '2600', 'Partial',
  ]);
  await page.screenshot({ path: testInfo.outputPath('stock-data-window.png'), fullPage: true });
  await control(page, 'Watchlist').click();
  await expect(watchlistSymbol(page, 'SIM.US.STOCK.001')).toBeVisible();
  expect(await canvas.evaluate(node => node.isConnected)).toBe(true);
  await expectSeries(page, 'SIM.US.STOCK.001', '1d', 2);
  expect(calls).toHaveLength(requestsBeforePanels);
  await leaveSample(page);
  expect(new URL(page.url()).hash).not.toBe('#demo');
  await page.reload();
  await expect(entry).toBeVisible();
  expect(calls).toHaveLength(requestsBeforePanels);

  expect([...new Set(calls.map(call => call.operation))].sort()).toEqual(['instrument.search', 'market.readHistoryPage']);
  const reads = calls.filter(call => call.operation === 'market.readHistoryPage').map(call => call.payload.series);
  for (const [id, timeframe] of [['SIM.CN.FUTURE.001', '1m'], ['SIM.CN.STOCK.001', '5m'], ['SIM.US.STOCK.001', '1d']]) {
    expect(reads).toContainEqual(expect.objectContaining({ provider_id: 'scopehub.synthetic', instrument_id: id, timeframe }));
  }
  expect(errors).toEqual([]);
  expect(outside).toEqual([]);
});

async function expectNarrowPlot(page) {
  const geometry = await page.locator('.chart').evaluate(chart => {
    const rect = chart.getBoundingClientRect();
    const centre = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
    return {
      width: rect.width / innerWidth, top: rect.top / innerHeight,
      visibleHeight: (Math.min(rect.bottom, innerHeight) - Math.max(rect.top, 0)) / innerHeight,
      canUseCentre: chart.contains(centre), documentFits: document.documentElement.scrollWidth <= innerWidth + 1,
    };
  });
  expect(geometry.width).toBeGreaterThan(0.7);
  expect(geometry.top).toBeLessThan(1 / 3);
  expect(geometry.visibleHeight).toBeGreaterThan(0.5);
  expect(geometry.canUseCentre).toBe(true);
  expect(geometry.documentFits).toBe(true);
}

test('an explicit sample link opens a usable plot on a narrow screen', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openDefaultSample(page);
  await expectSeries(page, 'SIM.CN.FUTURE.001', '1m', 40);
  await expect(control(page, 'Watchlist')).toHaveAttribute('aria-expanded', 'false');
  await expect(watchlistSymbol(page, 'SIM.US.STOCK.001')).not.toBeVisible();
  await expectNarrowPlot(page);
  await openPanel(page, 'Watchlist');
  await watchlistSymbol(page, 'SIM.US.STOCK.001').click();
  if (await control(page, 'Watchlist').getAttribute('aria-expanded') === 'true') {
    await control(page, 'Close panel').click();
  }
  await control(page, '1d').click();
  await control(page, 'Close-price line').click();
  await expectSeries(page, 'SIM.US.STOCK.001', '1d', 2);
  await expect.poll(() => painted(page, 'line')).toBeGreaterThan(20);
  await expectNarrowPlot(page);
  await page.screenshot({ path: testInfo.outputPath('stock-line-narrow.png'), fullPage: true });
  await openPanel(page, 'Data window');
  await expect(page.getByRole('table', { name: 'Historical bars' }).getByRole('cell', { name: 'Unknown', exact: true })).toBeVisible();
  await control(page, 'Close panel').click();
  await expectNarrowPlot(page);
});

test('retry the catalog and current history after the browser loses its local connection', async ({ page, context }) => {
  const alert = page.getByRole('alert');
  const chart = page.locator('.chart canvas');
  async function expectRetryGuidance() {
    await expect(alert).toContainText(/local preview.*retry/i);
    await expect(alert).not.toContainText(/restart/i);
  }
  await page.goto('/');
  await expect(control(page, 'Open offline sample')).toBeVisible();
  await context.setOffline(true);
  await control(page, 'Open offline sample').click();
  await expectRetryGuidance();
  await expect(chart).toHaveCount(0);

  await context.setOffline(false);
  await openDefaultSample(page, {
    trigger: () => page.getByRole('button', { name: 'Retry demo' }).click(),
  });
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
  await openPanel(page, 'Data window');
  const rows = page.getByRole('table', { name: 'Historical bars' }).locator('tbody tr');
  await expect(rows).toHaveCount(2);
  await expect(rows.last().getByRole('cell')).toHaveText([
    '2024-01-04T14:30:00.000Z', '2024-01-04', '174.85', '174.91', '174.68', '174.72', '2600', 'Partial',
  ]);
});

test('time axis labels fit the chart and the intraday plot can zoom and pan', async ({ page }, testInfo) => {
  await page.addInitScript(observeTimeLabels);
  await page.goto('/');
  await openDefaultSample(page, { trigger: () => control(page, 'Open offline sample').click() });
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
  await control(page, 'Zoom in').click();
  await expect.poll(ticks).not.toEqual(fitted);
  const zoomedByButton = await ticks();
  const canvas = await chart.locator('canvas').first().elementHandle();
  await control(page, 'Close-price line').click();
  await expect.poll(() => painted(page, 'line')).toBeGreaterThan(20);
  expect(await canvas.evaluate(node => node.isConnected)).toBe(true);
  expect((await ticks()).map(label => label.text)).toEqual(zoomedByButton.map(label => label.text));
  await control(page, 'Candles').click();
  await expect.poll(() => painted(page, 'candlestick')).toBeGreaterThan(20);
  await control(page, 'Zoom out').click();
  await expect.poll(ticks).not.toEqual(zoomedByButton);
  await control(page, 'Fit chart').click();
  await expect.poll(async () => (await ticks()).map(label => label.text))
    .toEqual(fitted.map(label => label.text));
  await control(page, 'Crosshair').click();
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
  await control(page, 'Pan').click();
  await page.mouse.move(x, y);
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

test('the default chart opens after separate delays to catalog and history replies', async ({ page }, testInfo) => {
  await page.goto('/');
  const operations = [];
  let start;
  let beforeHistory;
  await page.route(`**${endpoint}`, async route => {
    const { operation } = route.request().postDataJSON();
    const response = await route.fetch(replayStage);
    // Hold each real reply outside the worker deadlines. Their combined delay
    // alone exceeds the former five-second render wait.
    await delay(2_750);
    operations.push(operation);
    if (operation === 'market.readHistoryPage') {
      beforeHistory = {
        elapsed: performance.now() - start,
        loading: await page.getByText('Loading synthetic history…', { exact: true }).isVisible(),
        charts: await page.locator('.chart canvas').count(),
      };
    }
    await route.fulfill({ response });
  });

  await openDefaultSample(page, { trigger: () => {
    start = performance.now();
    return control(page, 'Open offline sample').click();
  } });
  expect(operations).toEqual(['instrument.search', 'market.readHistoryPage']);
  expect(beforeHistory).toMatchObject({ loading: true, charts: 0 });
  expect(beforeHistory.elapsed).toBeGreaterThan(5_000);
  await testInfo.attach('held-startup-replies', {
    body: JSON.stringify({ operations, ...beforeHistory }),
    contentType: 'application/json',
  });
  await expectSeries(page, future, '1m', 40);
  await expect(page.getByText('Simulated', { exact: true })).toBeVisible();
  await expect.poll(() => painted(page, 'candlestick')).toBeGreaterThan(20);
  await page.mouse.move(1, 1);
  await expect(page.getByLabel('Selected bar')).toHaveText(
    'O 3501.6H 3502L 3500.8C 3500.8Volume 110Trading day 2024-01-04Partial bar',
  );
});

test('a real worker rejection during chart startup remains visible', async ({ page }) => {
  let workerReply;
  await page.route(`**${endpoint}`, async route => {
    const call = route.request().postDataJSON();
    if (call.operation !== 'market.readHistoryPage') return route.continue();
    call.payload.expected_revision = 'scopehub-synthetic-v1:20241013:missing';
    const response = await route.fetch({ ...replayStage, postData: call });
    workerReply = { status: response.status(), body: await response.json() };
    await route.fulfill({ response });
  });

  const [catalog] = await Promise.all([
    page.waitForResponse(response => new URL(response.url()).pathname === endpoint
      && response.request().postDataJSON()?.operation === 'instrument.search', replayStage),
    page.goto('/#demo'),
  ]);
  expect(catalog.status()).toBe(200);
  expect(await catalog.json()).toMatchObject({ ok: true });
  await expect.poll(() => workerReply, replayStage).toBeDefined();
  expect(workerReply).toMatchObject({
    status: 200, body: { ok: false, error: { code: 'SNAPSHOT_UNAVAILABLE', retryable: false } },
  });
  await expect(page.getByRole('alert')).toHaveText('The selected historical revision is unavailable.');
  await expect(page.locator('.chart canvas')).toHaveCount(0);
  await expect(control(page, 'Retry history')).toHaveCount(0);
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
