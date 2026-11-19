import { setTimeout as delay } from 'node:timers/promises';
import { expect, test } from '@playwright/test';
import { painted } from '../ui/chart-pixels.mjs';
import { control, openPanel, watchlistSymbol } from '../ui/workspace-controls.mjs';
import { replayStage, waitForReplayRequest } from '../ui/replay-stages.mjs';

const stream = '/__scopehub/market-stream';
const operation = '/__scopehub/operation';
const chart = page => page.locator('.history');
const legend = page => page.getByLabel('Selected bar');
const status = page => page.locator('.replay-status');
const busy = { ok: false, error: { code: 'BUSY', retryable: true } };
const admissionFailure = { timeout: 3_000 };
const playReplay = page => waitForReplayRequest(page, () => control(page, 'Play sample updates').click());

async function trackSubscriptions(page) {
  const attempts = [];
  page.on('request', request => {
    if (new URL(request.url()).pathname === stream) attempts.push(request);
  });
  await page.addInitScript(path => {
    const fetch = window.fetch;
    const replies = [];
    window.replayReplies = replies;
    window.fetch = async (...args) => {
      const response = await fetch(...args);
      if (new URL(response.url).pathname === path) {
        // Read the stream while the browser still owns its response body.
        replies.push({
          payload: JSON.parse(args[1].body).payload,
          status: response.status,
          contentType: response.headers.get('content-type'),
          body: response.clone().text().then(text => ({ text }), error => ({ failure: String(error) })),
        });
      }
      return response;
    };
  }, stream);
  return attempts;
}

async function expectCompletedSubscription(page, attempts, instrument, timeframe) {
  const replies = await page.evaluate(id => Promise.all(
    window.replayReplies.filter(({ payload }) => payload.series.instrument_id === id)
      .map(async reply => ({ ...reply, body: await reply.body })),
  ), instrument);
  const payloads = attempts.map(request => request.postDataJSON().payload);
  expect(replies.map(reply => reply.payload), 'one browser reply for every stream POST').toEqual(payloads);
  for (const { payload, body } of replies) {
    expect(payload.series).toMatchObject({ instrument_id: instrument, timeframe });
    expect(body.failure, 'the response body can be read to completion').toBeUndefined();
  }
  const admitted = replies.filter(reply => reply.status === 200);
  expect(admitted, 'exactly one request was admitted as a replay stream').toHaveLength(1);
  expect(replies.at(-1)).toBe(admitted[0]);
  for (const { status, body } of replies.slice(0, -1)) {
    expect(status, 'only HTTP pre-admission BUSY can precede the admitted stream').toBe(429);
    expect(JSON.parse(body.text)).toEqual(busy);
  }
  const { body, contentType, payload } = admitted[0];
  expect(contentType).toBe('application/x-ndjson');
  const frames = body.text.trimEnd().split('\n').map(line => JSON.parse(line));
  expect(frames.filter(frame => frame.subscription?.status === 'accepted')).toHaveLength(1);
  expect(frames[0]).toEqual({ subscription: { status: 'accepted', from_position: payload.after } });
  expect(frames.at(-1)).toEqual({ ok: true, result: { status: 'complete' } });
  return { payload, frames };
}
const priceText = (page, close, volume, phase) => expect(legend(page)).toContainText(
  new RegExp(`C ${close}Volume ${volume}Trading day 2024-01-04${phase} bar`),
);

function observeCandleBodies() {
  const frames = new WeakMap();
  const fillRect = CanvasRenderingContext2D.prototype.fillRect;
  let watched;
  CanvasRenderingContext2D.prototype.fillRect = function (...args) {
    fillRect.apply(this, args);
    const [x, open, width, height] = args;
    // The fixture ends with three down candles. Their bodies are wider than the wicks.
    if (this.fillStyle !== '#f17d85' || width <= 2 || height < 1) return;
    const time = document.timeline.currentTime;
    if (frames.get(this.canvas)?.time !== time) frames.set(this.canvas, { time, bodies: new Map() });
    const body = { x, open, width, close: open + height - 1 };
    frames.get(this.canvas).bodies.set(x, body);
    if (watched?.canvas !== this.canvas || x !== watched.x || body.close === watched.close) return;
    watched.close = body.close;
    watched.paints.push({
      ...body, canvasWidth: this.canvas.width, canvasHeight: this.canvas.height,
      readout: document.querySelector('[aria-label="Selected bar"]')?.textContent,
    });
  };

  window.followLastCandle = canvas => {
    const [last, previous, earlier] = [...(frames.get(canvas)?.bodies.values() ?? [])]
      .sort((a, b) => b.x - a.x);
    if (!earlier) return null;
    watched = { canvas, x: last.x, close: last.close, paints: [] };
    return { last, previous, earlier, canvasWidth: canvas.width, canvasHeight: canvas.height };
  };
  window.readCandlePaints = () => watched.paints;
}

test('starts only by choice, streams the interim and final bar onto the canvas, and keeps history frozen', async ({ page }) => {
  await page.addInitScript(observeCandleBodies);
  const subscriptions = await trackSubscriptions(page);
  const calls = [];
  page.on('request', request => {
    const path = new URL(request.url()).pathname;
    if ([stream, operation].includes(path)) calls.push(request.postDataJSON());
  });
  await page.goto('/');
  await expect(control(page, 'Open offline sample')).toBeVisible();
  expect(calls).toEqual([]);
  await control(page, 'Open offline sample').click();
  await expect(chart(page)).toHaveAttribute('data-bars', '40');
  await expect.poll(() => painted(page, 'candlestick')).toBeGreaterThan(20);
  // The entry button occupies chart space until the first history arrives.
  await page.mouse.move(1, 1);
  await priceText(page, '3500\\.8', 110, 'Partial');
  expect(calls.map(call => call.operation)).toEqual(['instrument.search', 'market.readHistoryPage']);
  const canvas = chart(page).locator('.chart canvas').first();
  const originalCanvas = await canvas.elementHandle();
  const initial = await canvas.evaluate(el => window.followLastCandle(el));
  expect(initial).not.toBeNull();
  const { last, previous, earlier, canvasWidth, canvasHeight } = initial;
  // Two unchanged bars supply the scale independently of the replay values.
  // The 01:07 UTC open is 3502.2 and the 01:08 UTC close is 3501.6.
  const priceY = price => previous.close + ((3501.6 - price) / 0.6) * (previous.close - earlier.open);
  expect(last.open).toBe(previous.close);
  expect(Math.abs(last.close - priceY(3500.8))).toBeLessThanOrEqual(2);
  expect(priceY(3501) - priceY(3501.2)).toBeGreaterThan(4);

  // Hold the crosshair over the 01:08 bar while triggering replay by keyboard.
  const box = await canvas.boundingBox();
  const historicalReadout = 'O 3501.8H 3502L 3501.6C 3501.6Volume 90Trading day 2024-01-04Closed bar';
  await control(page, 'Play sample updates').focus();
  await page.mouse.move(box.x + (previous.x + previous.width / 2) * box.width / canvasWidth, box.y + box.height / 4);
  await expect(legend(page)).toHaveText(historicalReadout);
  let preparedReply;
  await page.route(`**${operation}`, async route => {
    if (!route.request().postDataJSON().payload.include_stream_position) return route.continue();
    // Hold the unmodified Rust/Python reply so the old shared 5 s wait
    // must compete with both preparation and the worker's real replay cadence.
    const [response] = await Promise.all([route.fetch(replayStage), delay(3_400)]);
    expect(response.status()).toBe(200);
    preparedReply = await response.json();
    await route.fulfill({ response });
  }, { times: 1 });
  await waitForReplayRequest(page, () => page.keyboard.press('Enter'));
  expect(preparedReply).toMatchObject({ ok: true, result: { stream_position: { sequence: 0 } } });
  await expect(status(page)).toHaveText('Synthetic replay complete', replayStage);
  await expect.poll(() => page.evaluate(() => window.readCandlePaints())).toHaveLength(2);
  const paints = await page.evaluate(() => window.readCandlePaints());
  for (const [index, price] of [3501.2, 3501].entries()) {
    const paint = paints[index];
    expect(paint).toMatchObject({ x: last.x, open: last.open, width: last.width, canvasWidth, canvasHeight, readout: historicalReadout });
    expect(Math.abs(paint.close - priceY(price))).toBeLessThanOrEqual(2);
  }
  await expect(legend(page)).toHaveText(historicalReadout);
  await page.mouse.move(1, 1);
  await priceText(page, '3501', 130, 'Closed');
  expect(await originalCanvas.evaluate(el => el.isConnected)).toBe(true);
  await expect(chart(page)).toHaveAttribute('data-bars', '40');

  const verifiedAttempts = [...subscriptions];
  const { payload, frames } = await expectCompletedSubscription(page, verifiedAttempts, 'SIM.CN.FUTURE.001', '1m');
  const preparation = calls.filter(call => call.payload.include_stream_position);
  expect(preparation).toHaveLength(1);
  expect(preparation[0].payload.snapshot).toEqual(payload.snapshot);
  expect(preparedReply.result.snapshot).toEqual(payload.snapshot);
  expect(preparedReply.result.stream_position).toEqual(payload.after);
  expect(frames).toHaveLength(4);
  expect(frames.slice(1, 3).map(({ event }) => {
    const { time_ms, open, high, low, close, volume, is_closed } = event.payload.bar;
    return [event.sequence, event.payload.observed_at_ms, time_ms, open, high, low, close, volume, is_closed];
  })).toEqual([
    [1, 1704330585000, 1704330540000, 3501.6, 3502, 3500.8, 3501.2, 120, false],
    [2, 1704330600000, 1704330540000, 3501.6, 3502, 3500.8, 3501, 130, true],
  ]);
  await page.screenshot({ path: test.info().outputPath('synthetic-replay-final.png'), animations: 'disabled' });

  await control(page, 'Close-price line').click();
  await expect.poll(() => painted(page, 'line')).toBeGreaterThan(20);
  await priceText(page, '3501', 130, 'Closed');
  await openPanel(page, 'Data window');
  await expect(page.getByRole('table', { name: 'Historical bars' }).locator('tbody tr').last().getByRole('cell')).toHaveText(
    ['2024-01-04T01:09:00.000Z', '2024-01-04', '3501.6', '3502', '3500.8', '3500.8', '110', 'Partial'],
  );
  await page.reload();
  await priceText(page, '3500\\.8', 110, 'Partial');
  await expect(status(page)).toHaveCount(0);
  expect(subscriptions).toHaveLength(verifiedAttempts.length);
});

test('changing symbol and timeframe during preparation loads the new chart before the old reply is released', async ({ page }) => {
  const prepared = Promise.withResolvers();
  const release = Promise.withResolvers();
  const preparationRouteFinished = Promise.withResolvers();
  const subscriptions = await trackSubscriptions(page);
  let replacementWasBusy = false;
  await page.route(`**${operation}`, async route => {
    const request = route.request();
    const { payload } = request.postDataJSON();
    if (payload.series?.instrument_id === 'SIM.CN.STOCK.001' && !replacementWasBusy) {
      // The old connection can abort before the bridge has reaped its worker.
      replacementWasBusy = true;
      await route.fulfill({ status: 429, json: { ok: false, error: { code: 'BUSY', retryable: true } } });
      return;
    }
    if (!payload.include_stream_position || payload.series.instrument_id !== 'SIM.CN.FUTURE.001') {
      await route.continue();
      return;
    }
    try {
      const response = await route.fetch(replayStage);
      prepared.resolve({ request, reply: await response.json() });
      await release.promise;
      await route.fulfill({ response });
    } catch (error) {
      prepared.reject(error);
      if (!request.failure()) throw error;
    } finally {
      preparationRouteFinished.resolve();
    }
  });

  try {
    await page.goto('/#demo');
    await expect(chart(page)).toHaveAttribute('data-instrument', 'SIM.CN.FUTURE.001');
    await control(page, 'Play sample updates').click();
    const [{ request, reply }] = await Promise.all([
      prepared.promise, expect(status(page)).toHaveText('Preparing synthetic replay…'),
    ]);
    expect(reply).toMatchObject({ ok: true, result: { stream_position: { sequence: 0 } } });
    const [cancelled] = await Promise.all([
      page.waitForEvent('requestfailed', { predicate: candidate => candidate === request, timeout: 5_000 }),
      (async () => {
        await watchlistSymbol(page, 'SIM.CN.STOCK.001').click();
        await expect(chart(page)).toHaveAttribute('data-instrument', 'SIM.CN.STOCK.001', replayStage);
      })(),
    ]);
    expect(cancelled.failure()?.errorText).toContain('ERR_ABORTED');
    expect(replacementWasBusy).toBe(true);
    await control(page, '5m').click();
    await expect(chart(page)).toHaveAttribute('data-timeframe', '5m');
    await expect(chart(page)).toHaveAttribute('data-bars', '8');
    await expect(status(page)).toHaveCount(0);
    expect(subscriptions.length).toBe(0);
  } finally {
    release.resolve();
    await page.unroute(`**${operation}`);
  }

  await preparationRouteFinished.promise;
  await playReplay(page);
  await expect(status(page)).toHaveText('Synthetic replay complete', replayStage);
  await expectCompletedSubscription(page, [...subscriptions], 'SIM.CN.STOCK.001', '5m');
});

test('selection change cancels the owned replay and releases the slot for the new selection', async ({ page }) => {
  const subscriptions = await trackSubscriptions(page);
  await page.goto('/#demo');
  await expect(chart(page)).toHaveAttribute('data-instrument', 'SIM.CN.FUTURE.001');
  await playReplay(page);
  await expect(status(page)).toHaveText('Synthetic replay in progress', replayStage);
  const cancelled = page.waitForEvent('requestfailed', {
    predicate: request => new URL(request.url()).pathname === stream, timeout: 5_000,
  });
  await watchlistSymbol(page, 'SIM.CN.STOCK.001').click();
  const previousStream = await cancelled;
  expect(previousStream.failure()?.errorText).toContain('ERR_ABORTED');
  await expect(chart(page)).toHaveAttribute('data-instrument', 'SIM.CN.STOCK.001', replayStage);
  await expect(status(page)).toHaveCount(0);
  await priceText(page, '12\\.15', 110, 'Partial');
  await playReplay(page);
  await expect(status(page)).toHaveText('Synthetic replay complete', replayStage);
  await expectCompletedSubscription(page, [...subscriptions].filter(request => request !== previousStream), 'SIM.CN.STOCK.001', '1m');
  await priceText(page, '12\\.16', 130, 'Closed');
});

test('replacement history waits for a cancelled preparation past the old cleanup bound', async ({ page }) => {
  const preparation = Promise.withResolvers();
  const firstBusy = Promise.withResolvers();
  const release = Promise.withResolvers();
  let released = false;
  let admitted = 0;
  await page.route(`**${operation}`, async route => {
    const request = route.request();
    const { payload } = request.postDataJSON();
    if (payload.include_stream_position) {
      preparation.resolve(request);
      await release.promise;
      await route.abort().catch(error => { if (!request.failure()) throw error; });
      return;
    }
    if (payload.series?.instrument_id === 'SIM.CN.STOCK.001') {
      if (!released) {
        await route.fulfill({ status: 429, json: busy });
        firstBusy.resolve();
        return;
      }
      admitted++;
    }
    await route.continue();
  });

  try {
    await page.goto('/#demo');
    await expect(chart(page)).toHaveAttribute('data-instrument', 'SIM.CN.FUTURE.001');
    await control(page, 'Play sample updates').click();
    const previous = await preparation.promise;
    const cancelled = page.waitForEvent('requestfailed', { predicate: request => request === previous });
    await watchlistSymbol(page, 'SIM.CN.STOCK.001').click();
    expect((await cancelled).failure()?.errorText).toContain('ERR_ABORTED');
    await firstBusy.promise;
    // Model the bridge holding its slot after cancellation, beyond the former 1 s budget.
    await delay(1_250);
    released = true;
    release.resolve();
    await expect(chart(page)).toHaveAttribute('data-instrument', 'SIM.CN.STOCK.001', replayStage);
    await expect(status(page)).toHaveCount(0);
    expect(admitted).toBe(1);
  } finally {
    release.resolve();
  }
});

test('replacement replay admits exactly one stream after the old cleanup bound', async ({ page }) => {
  const subscriptions = await trackSubscriptions(page);
  const firstBusy = Promise.withResolvers();
  let released = false;
  await page.route(`**${stream}`, async route => {
    if (route.request().postDataJSON().payload.series.instrument_id === 'SIM.CN.STOCK.001') {
      if (!released) {
        await route.fulfill({ status: 429, json: busy });
        firstBusy.resolve();
        return;
      }
    }
    await route.continue();
  });

  await page.goto('/#demo');
  await playReplay(page);
  await expect(status(page)).toHaveText('Synthetic replay in progress', replayStage);
  const cancelled = page.waitForEvent('requestfailed', {
    predicate: request => new URL(request.url()).pathname === stream, timeout: 5_000,
  });
  await watchlistSymbol(page, 'SIM.CN.STOCK.001').click();
  const previousStream = await cancelled;
  expect(previousStream.failure()?.errorText).toContain('ERR_ABORTED');
  await expect(chart(page)).toHaveAttribute('data-instrument', 'SIM.CN.STOCK.001', replayStage);
  await playReplay(page);
  await firstBusy.promise;
  // Release independently of the retry count and after the former 1.55 s budget.
  await delay(1_950);
  released = true;
  await expect(status(page)).toHaveText('Synthetic replay complete', replayStage);
  const replacementAttempts = [...subscriptions].filter(request => request !== previousStream);
  expect(replacementAttempts.length).toBeGreaterThan(1);
  await expectCompletedSubscription(page, replacementAttempts, 'SIM.CN.STOCK.001', '1m');
});

test('persistent admission contention stops and offers explicit replay retry', async ({ page }) => {
  let attempts = 0;
  await page.route(`**${stream}`, route => {
    attempts++;
    return route.fulfill({ status: 429, json: busy });
  });
  await page.goto('/#demo');
  await playReplay(page);
  await expect(status(page)).toContainText('The market worker is busy.', admissionFailure);
  await expect(control(page, 'Retry replay')).toBeVisible();
  expect(attempts).toBeGreaterThan(1);
  await expect(chart(page)).toHaveAttribute('data-bars', '40');
});

test('a BUSY terminal frame does not admit another replay', async ({ page }) => {
  let admitted = 0;
  await page.route(`**${stream}`, route => {
    admitted++;
    return route.fulfill({ contentType: 'application/x-ndjson', body: JSON.stringify(busy) + '\n' });
  });
  await page.goto('/#demo');
  await playReplay(page);
  await expect(status(page)).toContainText('The market worker is busy.', replayStage);
  expect(admitted).toBe(1);
  await expect(chart(page)).toHaveAttribute('data-bars', '40');
});

test('a crossed epoch produces a visible gap and leaves the chart at its frozen value', async ({ page }) => {
  await page.route(`**${stream}`, async route => {
    const body = route.request().postDataJSON();
    body.payload.after.epoch = 'prior-epoch';
    await route.continue({ postData: JSON.stringify(body) });
  });
  await page.goto('/#demo');
  await playReplay(page);
  await expect(status(page)).toHaveText('Replay gap. The stream restarted.', replayStage);
  await priceText(page, '3500\\.8', 110, 'Partial');
  await expect(chart(page)).toHaveAttribute('data-bars', '40');
  await page.unroute(`**${stream}`);
  await playReplay(page);
  await expect(status(page)).toHaveText('Synthetic replay complete', replayStage);
  await priceText(page, '3501', 130, 'Closed');
});
