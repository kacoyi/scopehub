import { expect, test } from '@playwright/test';
import { painted } from '../ui/chart-pixels.mjs';
import { replayStage, waitForReplayRequest } from '../ui/replay-stages.mjs';
import { openDefaultSample } from '../ui/sample-startup.mjs';
import { expectSelectedHistory, observeSelectedHistory } from '../ui/selected-history.mjs';
import { control, openPanel } from '../ui/workspace-controls.mjs';

test.use({ viewport: { width: 1600, height: 900 } });

const operationPath = '/__scopehub/operation';
const streamPath = '/__scopehub/market-stream';
const tradingDay = '2024-01-04';

// Normal sample fixture values, recorded independently of the preview responses.
const selections = [
  {
    id: 'SIM.CN.FUTURE.001', timeframe: '1m', style: 'Candles', count: 40, unit: 'contracts',
    start: '2024-01-04T01:09:00.000Z', ohl: [3501.6, 3502, 3500.8],
    referencePrices: [3502.2, 3501.6], // 01:07 open and 01:08 close
    observations: [[3500.8, 110], [3501.2, 120], [3501, 130]],
  },
  {
    id: 'SIM.CN.STOCK.001', timeframe: '5m', style: 'Close-price line', count: 8, unit: 'shares',
    start: '2024-01-04T05:05:00.000Z', ohl: [12.1, 12.19, 12.1],
    referencePrices: [12.06, 12.1], // 01:35 and 05:00 closes
    observations: [[12.15, 510], [12.17, 520], [12.16, 530]],
  },
  {
    id: 'SIM.US.STOCK.001', timeframe: '1d', style: 'Candles', count: 2, unit: 'shares',
    start: '2024-01-04T14:30:00.000Z', ohl: [174.85, 174.91, 174.68],
    referencePrices: [175, 174.85], // January 3 open and close
    observations: [[174.72, 2600], [174.74, 2610], [174.73, 2620]],
  },
];

const searchDialog = page => page.getByRole('dialog', { name: 'Search symbols' });
const barLegend = page => page.getByLabel('Selected bar');
const historyRows = page => page.getByRole('table', { name: 'Historical bars' }).locator('tbody tr');
const legendFields = bar => [
  `O ${bar.open}`, `H ${bar.high}`, `L ${bar.low}`, `C ${bar.close}`, `Volume ${bar.volume}`,
  `Trading day ${bar.trading_day}`, bar.is_closed ? 'Closed bar' : 'Partial bar',
];

async function selectSymbol(page, id) {
  await control(page, 'Search symbols').click();
  const dialog = searchDialog(page);
  await dialog.getByRole('combobox').fill(id);
  const option = dialog.getByRole('option');
  await expect(option).toHaveCount(1);
  await expect(option.getByText(id.replace(/^SIM\./, ''), { exact: true })).toBeVisible();
  await option.click();
  await expect(dialog).toHaveCount(0);
}

async function observeBarLegend(legend) {
  await legend.evaluate(element => {
    const observations = [];
    function record() {
      const fields = Array.from(element.children, child => child.textContent);
      if (fields.join('\n') !== observations.at(-1)?.join('\n')) observations.push(fields);
    }
    // Capture the actual legend as the worker progresses at its normal cadence.
    const observer = new MutationObserver(record);
    observer.observe(element, { childList: true, characterData: true, subtree: true });
    record();
    window.readMarketBarObservations = () => {
      observer.disconnect();
      return observations;
    };
  });
}

function observePriceCanvas() {
  const marks = new WeakMap();
  const paths = new WeakMap();
  const prototype = CanvasRenderingContext2D.prototype;
  const { fillRect, beginPath, lineTo, stroke } = prototype;
  let watchedCanvas;
  let paints;
  let queued = false;

  function record(canvas) {
    if (canvas !== watchedCanvas || queued) return;
    queued = true;
    // All marks from this synchronous pane draw are available at the microtask.
    queueMicrotask(() => {
      queued = false;
      const tail = marks.get(canvas).slice(-3);
      if (JSON.stringify(tail) !== JSON.stringify(paints.at(-1))) paints.push(tail);
    });
  }

  prototype.fillRect = function (x, y, width, height) {
    fillRect.call(this, x, y, width, height);
    const { canvas } = this;
    if (this.globalCompositeOperation === 'copy' && x === 0 && y === 0
      && width === canvas.width && height === canvas.height) {
      marks.set(canvas, []);
      record(canvas);
    }
    // The last bodies and the reference candles in these fixtures are down candles.
    if (this.fillStyle === '#f17d85' && width > 2 && height > 0) {
      marks.get(canvas)?.push({ x, openY: y, closeY: y + height - 1 });
      record(canvas);
    }
  };
  prototype.beginPath = function () {
    beginPath.call(this);
    paths.set(this, []);
  };
  prototype.lineTo = function (x, y) {
    lineTo.call(this, x, y);
    paths.get(this)?.push({ x, closeY: y });
  };
  prototype.stroke = function (...args) {
    stroke.apply(this, args);
    const path = paths.get(this);
    // The solid price series has several segments. The last-value guide has one.
    if (this.strokeStyle === '#73b8f6' && path?.length >= 3 && this.getLineDash().length === 0) {
      marks.set(this.canvas, path);
      record(this.canvas);
    }
  };

  window.followMarketPrice = canvas => {
    watchedCanvas = canvas;
    paints = [marks.get(canvas)?.slice(-3) ?? []];
  };
  window.readMarketPricePaints = () => paints;
}

async function expectPricePaints(page, selection, bars) {
  await expect.poll(() => page.evaluate(() => window.readMarketPricePaints().length), {
    message: 'price canvas draws the original, intermediate and terminal close',
  }).toBe(bars.length);
  const paints = await page.evaluate(() => window.readMarketPricePaints());
  expect(paints).toHaveLength(bars.length);

  const line = selection.style === 'Close-price line';
  const [referenceA, referenceB] = selection.referencePrices;
  const tolerance = line ? 1 : 3;
  for (const [index, bar] of bars.entries()) {
    const paint = paints[index];
    expect(paint).toHaveLength(Math.min(selection.count, 3));
    expect(paint.map(mark => mark.x)).toEqual(paints[0].map(mark => mark.x));
    const previous = paint.at(-2);
    const a = line ? paint[0].closeY : paint[0].openY;
    const b = previous.closeY;
    const pixelsPerPrice = (a - b) / (referenceA - referenceB);
    expect(pixelsPerPrice).toBeLessThan(0);
    const priceY = price => b + (price - referenceB) * pixelsPerPrice;
    if (index > 0) {
      const changeInPixels = Math.abs(priceY(bar.close) - priceY(bars[index - 1].close));
      expect(changeInPixels, 'successive closes are distinguishable at this scale').toBeGreaterThan(2 * tolerance);
    }
    const last = paint.at(-1);
    expect(paint[0].x).toBeLessThan(last.x);
    expect(Math.abs(last.closeY - priceY(bar.close)), `painted close ${bar.close}`).toBeLessThanOrEqual(tolerance);
    if (!line) {
      expect(Math.abs(last.openY - priceY(bar.open)), `painted open ${bar.open}`).toBeLessThanOrEqual(tolerance);
    }
  }
}

function observeMarketStreams(path) {
  const originalFetch = window.fetch;
  const bodies = [];
  window.fetch = async (...args) => {
    const response = await originalFetch(...args);
    if (response.ok && new URL(response.url).pathname === path) {
      // Keep a copy because Chromium can discard the body from its test protocol.
      bodies.push(response.clone().text().then(body => ({ body }), error => ({ error: error.message })));
    }
    return response;
  };
  window.takeMarketStreamBodies = () => Promise.all(bodies.splice(0));
}

async function admittedResponse(calls, operation) {
  const responses = await Promise.all(calls.map(({ request }) => request.response()));
  const admitted = responses.filter(response => response?.ok());
  expect(admitted, `${operation} admitted response count`).toHaveLength(1);
  return admitted[0];
}

async function expectStreamAdmission(subscriptions) {
  const admitted = [];
  for (const { request } of subscriptions) {
    const response = await request.response();
    expect(response, `market.subscribe response (${request.failure()?.errorText ?? 'no response'})`).not.toBeNull();
    expect(await response.finished(), 'market.subscribe response completion').toBeNull();
    expect(request.failure(), 'market.subscribe transport failure').toBeNull();
    if (response.status() === 429) {
      expect(admitted, 'BUSY responses precede admission').toHaveLength(0);
      expect(await response.json()).toEqual({ ok: false, error: { code: 'BUSY', retryable: true } });
    } else {
      expect(response.status(), 'market.subscribe status').toBe(200);
      admitted.push(response);
    }
  }
  expect(admitted, 'market.subscribe admitted response count').toHaveLength(1);
  return admitted[0];
}

async function workerResult(response) {
  const reply = await response.json();
  expect(reply).toMatchObject({ ok: true });
  return reply.result;
}

async function expectReplayProtocol(page, calls, playback, selection, [original, ...updates]) {
  const { id, timeframe, unit, count } = selection;
  const series = {
    instrument_id: id, timeframe, quantity_unit: unit, provider_id: 'scopehub.synthetic',
    price_adjustment: 'raw', session_scope: 'sample-sessions', calendar_id: `${id}.sample-calendar.v1`,
  };
  const reads = calls.filter(call => call.path === operationPath && call.operation === 'market.readHistoryPage');
  const historyResponse = await admittedResponse(reads.filter(({ payload }) =>
    !payload.snapshot && payload.series.instrument_id === id && payload.series.timeframe === timeframe), 'history');
  const query = historyResponse.request().postDataJSON().payload;
  expect(query.series).toEqual(series);
  const history = await workerResult(historyResponse);
  expect(history.dataset).toMatchObject({
    instrument_id: id, provider_id: series.provider_id, timeframe, quantity_unit: unit, mode: 'simulated',
  });
  expect(history.total_bars).toBe(count);
  expect(history.bars.at(-1)).toEqual(original);
  const range = { from_ms: query.from_ms, to_ms: query.to_ms, warmup_bars: query.warmup_bars };
  const { snapshot } = history;
  expect(snapshot).toMatchObject({
    series, ...range, dataset_id: history.dataset.id, dataset_revision: history.dataset.revision,
    content_hash: expect.stringMatching(/^[a-f0-9]{64}$/),
  });

  const handoff = await admittedResponse(playback.filter(call =>
    call.path === operationPath && call.operation === 'market.readHistoryPage' && call.payload.include_stream_position),
  'replay preparation');
  expect(handoff.request().postDataJSON().payload).toMatchObject({ series, ...range, snapshot });
  const prepared = await workerResult(handoff);
  expect(prepared.snapshot).toEqual(snapshot);
  const after = prepared.stream_position;
  expect(after).toMatchObject({ stream_id: expect.any(String), epoch: expect.any(String), sequence: 0 });

  const subscriptions = playback.filter(call => call.path === streamPath);
  for (const { operation, payload } of subscriptions) {
    expect(operation).toBe('market.subscribe');
    expect(payload).toEqual({ series, snapshot, after });
  }
  const stream = await expectStreamAdmission(subscriptions);
  expect(stream.headers()['content-type']).toBe('application/x-ndjson');
  const captured = await page.evaluate(() => window.takeMarketStreamBodies());
  expect(captured).toEqual([{ body: expect.any(String) }]);
  const frames = captured[0].body.trimEnd().split('\n').map(line => JSON.parse(line));
  expect(frames).toHaveLength(updates.length + 2);
  expect(frames[0]).toEqual({ subscription: { status: 'accepted', from_position: after } });
  for (const [index, bar] of updates.entries()) {
    expect(frames[index + 1]).toMatchObject({ event: {
      event: 'market.bar_update', stream_id: after.stream_id, epoch: after.epoch, sequence: after.sequence + index + 1,
      payload: { series, dataset_revision: snapshot.dataset_revision, bar },
    } });
  }
  expect(frames.at(-1)).toEqual({ ok: true, result: { status: 'complete' } });
  return subscriptions.length;
}

test('searches and replays three simulated instruments in one chart session', async ({ page, context, baseURL }, testInfo) => {
  // Each of the three complete journeys has its own bounded preparation and stream.
  test.slow();
  const calls = [];
  const external = [];
  await page.addInitScript(observePriceCanvas);
  await page.addInitScript(observeMarketStreams, streamPath);
  await observeSelectedHistory(page);
  context.on('request', request => {
    const url = new URL(request.url());
    if (!['http:', 'https:'].includes(url.protocol)) return;
    if (url.origin !== baseURL) external.push(url.origin);
    if (url.origin === baseURL && url.pathname.startsWith('/__scopehub/')) {
      calls.push({ ...request.postDataJSON(), path: url.pathname, request });
    }
  });
  page.on('websocket', socket => {
    const url = new URL(socket.url().replace(/^ws/, 'http'));
    if (url.origin !== baseURL) external.push(url.origin);
  });

  const sourceStatus = page.locator('header').getByRole('status');
  await page.goto('/');
  await expect(sourceStatus).toHaveText('No data source');
  await expect(page.locator('.chart canvas')).toHaveCount(0);
  await control(page, 'Search symbols').click();
  const dialog = searchDialog(page);
  await dialog.getByRole('combobox').fill(selections[0].id);
  await expect(dialog.getByText('No data source is connected.')).toBeVisible();
  await expect(dialog.getByRole('option')).toHaveCount(0);
  expect(calls, 'operations issued while unconfigured').toEqual([]);
  await openDefaultSample(page, { trigger: () => control(dialog, 'Open offline sample').click() });
  await expect(dialog).toHaveCount(0);
  await expect(sourceStatus.getByText('Offline sample', { exact: true })).toBeVisible();
  await expect(sourceStatus.getByText('Simulated', { exact: true })).toBeVisible();
  await expect(control(page, 'Play sample updates')).toBeVisible();

  let verifiedSubscriptions = 0;
  let historyStart = 0;
  for (const selection of selections) {
    const { id, timeframe, style, count, unit, start, ohl: [open, high, low], observations } = selection;
    await test.step(`${id} ${timeframe} ${style}`, async () => {
      await selectSymbol(page, id);
      await control(page, timeframe).click();
      await control(page, style).click();
      await page.mouse.move(1, 1);
      await expect(sourceStatus.getByText('Simulated', { exact: true })).toBeVisible();
      await expect(control(page, timeframe)).toHaveAttribute('aria-pressed', 'true');
      await expect(control(page, style)).toHaveAttribute('aria-pressed', 'true');

      const chartName = style === 'Candles' ? 'Candlestick' : style;
      const plot = page.getByRole('img', { name: `${chartName} and volume chart for ${id}`, exact: true });
      const canvas = plot.locator('canvas').first();
      await expectSelectedHistory(page, canvas, calls, historyStart, selection, testInfo);
      await expect.poll(() => painted(page, style === 'Candles' ? 'candlestick' : 'line')).toBeGreaterThan(20);

      const bars = observations.map(([close, volume], index) => ({
        time_ms: Date.parse(start), trading_day: tradingDay, open, high, low, close, volume,
        is_closed: index === observations.length - 1,
      }));
      const [original, , final] = bars;
      const legend = barLegend(page);
      await expect(legend).toBeVisible();
      await expect(legend.locator('span')).toHaveText(legendFields(original));
      await canvas.evaluate(node => window.followMarketPrice(node));
      await observeBarLegend(legend);

      const playbackStart = calls.length;
      await waitForReplayRequest(page, () => control(page, 'Play sample updates').press('Enter'));
      await expect(page.getByText('Synthetic replay complete', { exact: true })).toBeVisible(replayStage);
      await expect(legend.locator('span')).toHaveText(legendFields(final));
      expect(await page.evaluate(() => window.readMarketBarObservations())).toEqual(bars.map(legendFields));
      await expectPricePaints(page, selection, bars);

      await openPanel(page, 'Data window');
      const history = page.locator('.history');
      await expect(history).toHaveAttribute('data-bars', String(count));
      const dataWindow = page.getByRole('complementary', { name: 'Data window' });
      await expect(dataWindow.getByText(`${count} bars · Volume in ${unit}`, { exact: true })).toBeVisible();
      const rows = historyRows(page);
      await expect(rows).toHaveCount(count);
      await expect(rows.last().getByRole('cell')).toHaveText(
        [start, tradingDay, open, high, low, original.close, original.volume, 'Partial'].map(String),
      );
      verifiedSubscriptions += await expectReplayProtocol(page, calls, calls.slice(playbackStart), selection, bars);
      historyStart = calls.length;
    });
  }

  await expect(historyRows(page).first().getByRole('cell').nth(6)).toHaveText('Unknown');
  expect(calls.filter(call => call.path === streamPath)).toHaveLength(verifiedSubscriptions);
  expect([...new Set(calls.map(call => call.operation))]).toEqual(['instrument.search', 'market.readHistoryPage', 'market.subscribe']);
  expect(external, 'external HTTP and WebSocket traffic').toEqual([]);
  await page.screenshot({ path: testInfo.outputPath('synthetic-market-workflow.png'), animations: 'disabled' });
});
