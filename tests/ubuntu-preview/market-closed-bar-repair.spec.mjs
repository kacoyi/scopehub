import { readFileSync } from 'node:fs';
import { expect, test } from '@playwright/test';
import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';
import { painted } from '../ui/chart-pixels.mjs';
import { replayStage, waitForReplayRequest } from '../ui/replay-stages.mjs';
import { openDefaultSample, sampleCatalogCall, sampleHistoryCall } from '../ui/sample-startup.mjs';
import { control, openPanel } from '../ui/workspace-controls.mjs';

const stream = '/__scopehub/market-stream';
const sources = page => page.getByRole('complementary', { name: 'Data sources' });
const fact = (page, label) => sources(page).locator('dt').filter({ hasText: new RegExp(`^${RegExp.escape(label)}$`) })
  .locator('xpath=following-sibling::dd[1]');
const chart = page => page.locator('.history');
const legend = page => page.getByLabel('Selected bar');
const gap = page => page.getByRole('group', { name: 'Replay gap details' });
const revision = page => sources(page).getByLabel('Sample revision');
const frozenLast = ['2024-01-04T01:09:00.000Z', '2024-01-04', '3501.6', '3502', '3500.8', '3500.8', '110', 'Partial'];
const missing = '2024-01-04T01:08:00.000Z';

function observeReplay(path) {
  const fetch = window.fetch;
  window.closedBarReplays = [];
  window.fetch = async (...args) => {
    const response = await fetch(...args);
    if (new URL(response.url).pathname !== path || !response.ok) return response;
    window.closedBarReplays.push(response.clone().text());
    if (!response.headers.has('X-Hold-Closed-Bar-Repair')) return response;
    const lines = (await response.text()).trimEnd().split('\n');
    const terminal = lines.pop();
    const repair = lines.pop();
    const encoder = new TextEncoder();
    return new Response(new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(lines.join('\n') + '\n'));
        window.sendClosedBarRepair = () => controller.enqueue(encoder.encode(repair + '\n'));
        window.finishClosedBarRepair = () => {
          controller.enqueue(encoder.encode(terminal + '\n'));
          controller.close();
        };
      },
    }), { headers: { 'Content-Type': 'application/x-ndjson' } });
  };
}

function attachSampleStartup(observation) {
  const body = JSON.stringify(observation);
  if (observation.stage !== 'sample-verified') console.info('Sample startup', body);
  return test.info().attach('sample-startup', { contentType: 'application/json', body });
}

async function openRepairSample(page, selected) {
  await page.addInitScript(observeReplay, stream);
  await openDefaultSample(page, { reportStartup: attachSampleStartup });
  await openPanel(page, 'Data sources');
  await expect(revision(page)).toHaveValue('normal');
  await revision(page).selectOption(selected);
  await expect(chart(page)).toHaveAttribute('data-bars', '39', replayStage);
  await expect(legend(page)).toContainText(/C 3500\.8Volume 110.*Partial bar/);
  await expect(fact(page, 'Coverage')).toHaveText('Gapped');
  await expect(fact(page, 'Connection')).toHaveText('Offline');
  await expect(fact(page, 'Input digest')).toHaveText(/^[0-9a-f]{64}$/);
}

async function readFrozenRows(page) {
  await openPanel(page, 'Data window');
  const rows = page.getByRole('table', { name: 'Historical bars' }).locator('tbody tr');
  await expect(rows).toHaveCount(39);
  await expect(rows.last().getByRole('cell')).toHaveText(frozenLast);
  await expect(rows.filter({ hasText: missing })).toHaveCount(0);
  return rows.allTextContents();
}

async function play(page) {
  await page.mouse.move(1, 1);
  await waitForReplayRequest(page, () => control(page, 'Play sample updates').press('Enter'));
}

async function captureFrames(page) {
  const bodies = await page.evaluate(() => Promise.all(window.closedBarReplays));
  expect(bodies).toHaveLength(1);
  return bodies[0].trimEnd().split('\n').map(line => JSON.parse(line));
}

async function expectGap(page) {
  await expect(gap(page)).toHaveText('Affected range [2024-01-04 01:08:00, 2024-01-04 01:09:00) UTC · 1 lost update', replayStage);
  await expect(chart(page)).toHaveAttribute('data-bars', '39');
  await expect(legend(page)).toContainText(/C 3501\.2Volume 120.*Partial bar/);
}

async function trackProjection(page) {
  await chart(page).evaluate(element => {
    const counts = [element.getAttribute('data-bars')];
    const observer = new MutationObserver(records => {
      counts.push(...records.map(record => record.oldValue), element.getAttribute('data-bars'));
    });
    observer.observe(element, { attributes: true, attributeOldValue: true, attributeFilter: ['data-bars'] });
    window.closedBarProjectionCounts = counts;
  });
}

test('a real verified repair adds the missing closed minute to the existing chart and keeps its source rows fixed', async ({ page }, testInfo) => {
  const calls = [];
  page.on('request', request => {
    if (new URL(request.url()).pathname === stream) calls.push(request.postDataJSON());
  });
  await openRepairSample(page, 'repairable');
  const digest = await fact(page, 'Input digest').textContent();
  const before = await readFrozenRows(page);
  await openPanel(page, 'Data sources');
  const canvas = await chart(page).locator('.chart canvas').first().elementHandle();
  await play(page);
  await expect(page.getByText('Closed-bar gap repaired in chart', { exact: true })).toBeVisible(replayStage);
  await expect(page.getByText('Frozen history has gaps', { exact: true })).toBeVisible();
  await expect(chart(page)).toHaveAttribute('data-bars', '40');
  await expect(legend(page)).toContainText(/C 3501Volume 130.*Closed bar/);
  await expect(gap(page)).toHaveCount(0);
  await expect(chart(page).getByRole('alert')).toHaveCount(0);
  expect(await canvas.evaluate(element => element.isConnected)).toBe(true);
  await expect.poll(() => painted(page, 'candlestick')).toBeGreaterThan(20);
  await expect(fact(page, 'Coverage')).toHaveText('Gapped');
  await expect(fact(page, 'Input digest')).toHaveText(digest);
  expect(await readFrozenRows(page)).toEqual(before);

  const frames = await captureFrames(page);
  expect(calls).toHaveLength(1);
  const { after, snapshot } = calls[0].payload;
  expect(frames).toEqual([
    { subscription: { status: 'accepted', from_position: after } },
    { event: expect.objectContaining({ event: 'market.bar_update', sequence: 1 }) },
    { event: expect.objectContaining({ event: 'market.stream_gap', sequence: 3 }) },
    { repair: {
      bars: [
        { time_ms: Date.parse(missing), trading_day: '2024-01-04', open: 3501.8, high: 3502, low: 3501.6, close: 3501.6, volume: 90, is_closed: true },
        { time_ms: Date.parse(frozenLast[0]), trading_day: '2024-01-04', open: 3501.6, high: 3502, low: 3500.8, close: 3501, volume: 130, is_closed: true },
      ],
      snapshot: expect.objectContaining({ series: snapshot.series, dataset_id: snapshot.dataset_id, dataset_revision: snapshot.dataset_revision,
        from_ms: Date.parse(missing), to_ms: 1704330600000, as_of_ms: 1704330600000, warmup_bars: 0 }),
      position: { ...after, sequence: 3 },
      quality_state: expect.objectContaining({ coverage: 'observed', gaps: [], connection: 'offline', source_kind: 'synthetic' }),
    } },
    { ok: true, result: { status: 'complete' } },
  ]);
  await page.getByRole('cell', { name: 'Partial', exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath('verified-closed-bar-repair.png'), animations: 'disabled' });

  await control(page, '5m').click();
  await openPanel(page, 'Data sources');
  await expect(revision(page)).toHaveValue('normal');
  await expect(revision(page).locator('option')).toHaveCount(3);
  await expect(chart(page)).toHaveAttribute('data-timeframe', '5m');
  await expect(page.getByText('Closed-bar gap repaired in chart', { exact: true })).toHaveCount(0);
});

test('the real unrepaired sample keeps the missing interval visible and the chart unfilled', async ({ page }, testInfo) => {
  await openRepairSample(page, 'unrepaired');
  await play(page);
  await expect(page.getByText('Replay gap. An update is missing.', { exact: true })).toBeVisible(replayStage);
  await expectGap(page);
  const frames = await captureFrames(page);
  expect(frames.map(frame => Object.keys(frame)[0])).toEqual(['subscription', 'event', 'event', 'ok']);
  expect(frames.at(-1)).toEqual({ ok: true, result: { status: 'gapped', reason: 'sequence_gap' } });
  await expect(fact(page, 'Coverage')).toHaveText('Gapped');
  await page.screenshot({ path: testInfo.outputPath('unresolved-closed-bar-gap.png'), animations: 'disabled' });
  await readFrozenRows(page);
});

test('sample startup reports a terminal document before any committed sample', async ({ page }) => {
  await page.route(url => url.pathname === '/', route => route.abort('connectionreset'));
  try {
    let observation;
    await expect(openDefaultSample(page, { reportStartup: report => {
      observation = report;
      return attachSampleStartup(report);
    } })).rejects.toThrow('Sample document GET failed (net::ERR_CONNECTION_RESET)');
    expect(observation).toMatchObject({ stage: 'document-terminal',
      document: { failure: 'net::ERR_CONNECTION_RESET' }, pending_count: 0 });
    expect(observation.document.committed_ms).toBeUndefined();
    expect(observation.catalog_verified_ms).toBeUndefined();
  } finally {
    await page.unrouteAll({ behavior: 'wait' });
  }
});

const navigationDeadlineMs = 10_000;
const timerRoundingMs = 5;

function verifyHeldModuleElapsed({ elapsed_ms }, independentElapsedMs) {
  // Timer delivery can run late. Compare the two readings around the same cutoff.
  const samplingToleranceMs = 250;
  const earliestMs = Math.max(navigationDeadlineMs - timerRoundingMs, independentElapsedMs - samplingToleranceMs);
  if (!Number.isInteger(elapsed_ms) || elapsed_ms < earliestMs || elapsed_ms > independentElapsedMs + samplingToleranceMs) {
    throw new Error(`Sample startup attachment elapsed ${elapsed_ms} ms disagrees with the independent ${independentElapsedMs} ms reading`);
  }
}

test('sample startup identifies a committed document with a held required module at the original deadline', async ({ page }) => {
  const release = Promise.withResolvers();
  const reload = page.getByRole('button', { name: 'Reload window' });
  await page.route(url => url.pathname === '/src/workspace-start.ts', async route => {
    await release.promise;
    await route.abort().catch(() => {});
  });
  try {
    let observation;
    let independentElapsedMs;
    const started = process.hrtime.bigint();
    const stopped = expect(openDefaultSample(page, { reportStartup: report => {
      independentElapsedMs = Math.round(Number(process.hrtime.bigint() - started) / 1_000_000);
      observation = report;
      return attachSampleStartup(report);
    } })).rejects.toThrow(`Timeout ${navigationDeadlineMs}ms exceeded`);
    await expect(reload).toBeEnabled();
    await stopped;
    await test.info().attach('sample-startup-clock', {
      contentType: 'application/json', body: JSON.stringify({ elapsed_ms: independentElapsedMs }),
    });
    expect(observation).toMatchObject({ stage: 'document-committed',
      document: { http: 200, committed_ms: expect.any(Number) },
      pending: expect.arrayContaining([expect.objectContaining({ type: 'script', asset: '/src/workspace-start.ts' })]),
    });
    expect(observation.document.dom_content_loaded_ms).toBeUndefined();
    expect(observation.catalog_verified_ms).toBeUndefined();
    expect(independentElapsedMs).toBeGreaterThanOrEqual(navigationDeadlineMs - timerRoundingMs);
    verifyHeldModuleElapsed(observation, independentElapsedMs);
    for (const elapsed_ms of [0, 60_000]) {
      expect(() => verifyHeldModuleElapsed({ ...observation, elapsed_ms }, independentElapsedMs))
        .toThrow(`Sample startup attachment elapsed ${elapsed_ms} ms disagrees`);
    }
    await expect(chart(page)).toHaveCount(0);
  } finally {
    release.resolve();
    await page.unrouteAll({ behavior: 'wait' });
  }
  await openDefaultSample(page, { trigger: () => reload.click() });
  await expect(chart(page)).toHaveAttribute('data-bars', '40');
});

test('DOMContentLoaded with a held dynamic entry still waits for the verified sample', async ({ page }) => {
  const release = Promise.withResolvers();
  await page.route(url => url.pathname === '/src/main.tsx', async route => {
    await release.promise;
    await route.continue().catch(() => {});
  });
  let observation;
  try {
    const dom = page.waitForEvent('domcontentloaded', { timeout: 10_000 });
    const sample = openDefaultSample(page, { reportStartup: report => {
      observation = report;
      return attachSampleStartup(report);
    } });
    void sample.catch(() => {});
    await dom;
    await expect(page.getByRole('button', { name: 'Reload window' })).toBeEnabled();
    await expect(chart(page)).toHaveCount(0);
    expect(observation).toBeUndefined();
    release.resolve();
    await sample;
    expect(observation).toMatchObject({ stage: 'sample-verified',
      document: { http: 200, committed_ms: expect.any(Number), dom_content_loaded_ms: expect.any(Number) },
      catalog_verified_ms: expect.any(Number), history_verified_ms: expect.any(Number), sample_verified_ms: expect.any(Number),
    });
    expect(observation.catalog_verified_ms).toBeGreaterThanOrEqual(observation.document.dom_content_loaded_ms);
    expect(observation.sample_verified_ms).toBeGreaterThanOrEqual(observation.history_verified_ms);
    await expect(chart(page)).toHaveAttribute('data-bars', '40');
  } finally {
    release.resolve();
    await page.unrouteAll({ behavior: 'wait' });
  }
});

test.describe('initial sample candle presentation', () => {
  const candleColors = ['#43d4ac', '#f17d85'];
  for (const variant of ['rendered history', 'missing candles', 'unrelated price-colored ink']) {
    test(`reports startup with ${variant}`, async ({ page }, testInfo) => {
      const draws = variant === 'rendered history';
      const unrelated = variant === 'unrelated price-colored ink';
      if (!draws) await page.addInitScript(({ candleColors, unrelated }) => {
        const fillRect = CanvasRenderingContext2D.prototype.fillRect;
        CanvasRenderingContext2D.prototype.fillRect = function(...rect) {
          const color = candleColors.indexOf(this.fillStyle);
          if (color < 0 || this.canvas !== document.querySelector('.history .chart canvas')) {
            return fillRect.apply(this, rect);
          }
          // Skip bodies and wicks. The renderer still strokes the last-price line and draws its axis label.
          if (unrelated) fillRect.call(this, 24 + color * 48, 24, 20, 30);
        };
      }, { candleColors, unrelated });

      const pageErrors = [];
      page.on('pageerror', error => pageErrors.push(error.message));
      const calls = [sampleCatalogCall, sampleHistoryCall()];
      const replies = calls.map(call => page.waitForResponse(response =>
        new URL(response.url()).pathname === '/__scopehub/operation' && response.status() === 200
        && response.request().method() === 'POST' && response.request().postDataJSON()?.operation === call.operation));
      let observation;
      const startup = openDefaultSample(page, { reportStartup: report => {
        observation = report;
        return attachSampleStartup(report);
      } }).catch(error => error);
      const responses = await Promise.all(replies);
      expect(responses.map(response => response.request().postDataJSON())).toEqual(calls);
      const [catalog, history] = await Promise.all(responses.map(response => response.json()));
      expect(catalog.result.instruments).toEqual(expect.arrayContaining([
        expect.objectContaining({ id: calls[1].payload.series.instrument_id }),
      ]));
      expect(history.result.bars).toHaveLength(40);
      const { snapshot } = history.result;
      expect(snapshot.content_hash).toBe('8c5cccacb89d3cfa0a2f06418caa75cd5be6436e3cdd3ed4fadddd766cdff900');
      const result = await startup;
      const plot = page.getByRole('region', { name: 'Price chart' });
      await expect(chart(page)).toHaveAttribute('data-bars', '40');
      await expect(plot.getByRole('alert')).toHaveCount(0);
      expect(pageErrors).toEqual([]);

      // Independently retain ink counts by row for the price pane and its axis.
      const ink = await chart(page).locator('.chart canvas').evaluateAll(canvases => [0, 2].map(index => {
        const canvas = canvases[index];
        const { width, height } = canvas;
        const pixels = canvas.getContext('2d').getImageData(0, 0, width, height).data;
        const rows = new Array(height).fill(0);
        for (let i = 0; i < pixels.length; i += 4) {
          const color = pixels[i] * 65536 + pixels[i + 1] * 256 + pixels[i + 2];
          if (color === 0x43d4ac || color === 0xf17d85) rows[Math.floor(i / (4 * width))]++;
        }
        return { pixels: rows.reduce((sum, count) => sum + count, 0), rows: rows.filter(Boolean).length,
          widestRow: Math.max(...rows) };
      }));
      await testInfo.attach('sample-startup-candle-ink', {
        contentType: 'application/json', body: JSON.stringify({ variant, snapshot, ink,
          error: result instanceof Error ? result.message : null }),
      });
      const [pane, axis] = ink;
      expect(pane.widestRow, 'The last-price line remains visible').toBeGreaterThan(20);
      expect(axis.pixels, 'The price-axis label remains visible').toBeGreaterThan(20);
      if (draws || unrelated) expect(pane.rows).toBeGreaterThan(20);
      else expect(pane.rows).toBe(1);
      expect(observation.history_verified_ms).toBeGreaterThanOrEqual(observation.catalog_verified_ms);
      expect(observation.chart_failed_ms).toBeUndefined();
      expect(observation.stage).toBe(draws ? 'sample-verified' : 'history-verified');
      if (draws) {
        expect(result).toEqual(snapshot);
        expect(observation.sample_verified_ms).toBeGreaterThanOrEqual(observation.history_verified_ms);
      } else {
        expect(result).toBeInstanceOf(Error);
        expect(result.message).toContain('Wait for initial sample chart presentation');
        expect(observation.sample_verified_ms).toBeUndefined();
      }
    });
  }
});

for (const [change, stage, rejectedOperation] of [
  ['history start', 'catalog-verified', 'market.readHistoryPage'],
  ['catalog limit', 'dom-content-loaded', 'instrument.search'],
  ['request order', 'dom-content-loaded', 'market.readHistoryPage'],
]) {
  test(`sample startup reports its last verified stage with a changed ${change}`, async ({ page }) => {
    const catalog = structuredClone(sampleCatalogCall);
    const history = sampleHistoryCall();
    if (change === 'history start') history.payload.from_ms += 60_000;
    if (change === 'catalog limit') catalog.payload.limit = 49;
    const reversed = change === 'request order';
    await page.addInitScript(({ change, earlyHistoryCall }) => {
      const fetch = window.fetch;
      let earlyHistory;
      window.fetch = async (url, options) => {
        if (url !== '/__scopehub/operation') return fetch(url, options);
        const call = JSON.parse(options.body);
        const send = body => fetch(url, { ...options, body: JSON.stringify(body) });
        if (call.operation === 'instrument.search' && change === 'request order') {
          earlyHistory = await send(earlyHistoryCall);
          await earlyHistory.clone().arrayBuffer();
        }
        if (call.operation === 'instrument.search' && change === 'catalog limit') call.payload.limit = 49;
        if (call.operation === 'market.readHistoryPage') {
          if (earlyHistory) return earlyHistory;
          if (change === 'history start') call.payload.from_ms += 60_000;
        }
        return send(call);
      };
    }, { change, earlyHistoryCall: history });

    const navigations = [];
    const calls = [];
    const replies = [];
    page.on('request', request => {
      if (request.isNavigationRequest()) navigations.push(request.method());
      if (request.method() === 'POST') calls.push(request.postDataJSON());
    });
    page.on('response', response => {
      if (response.request().method() === 'POST') replies.push(response);
    });
    let observation;
    const rejected = await openDefaultSample(page, { reportStartup: report => {
      observation = report;
      return attachSampleStartup(report);
    } }).catch(error => error);
    await expect.poll(() => replies.length).toBe(2);
    expect(navigations).toEqual(['GET']);
    expect(calls).toEqual(reversed ? [history, catalog] : [catalog, history]);
    for (const reply of replies) {
      expect(reply.status()).toBe(200);
      expect(await reply.json()).toMatchObject({ ok: true });
    }
    expect(observation.stage).toBe(stage);
    expect(rejected).toBeInstanceOf(Error);
    expect(rejected.message).toContain(`POST parameters for ${rejectedOperation}`);
    if (stage === 'catalog-verified') expect(observation.catalog_verified_ms).toEqual(expect.any(Number));
    else expect(observation.catalog_verified_ms).toBeUndefined();
    expect(observation.history_verified_ms).toBeUndefined();
    expect(observation.sample_verified_ms).toBeUndefined();
  });
}

test.describe('sample startup reply compatibility', () => {
  const operation = '/__scopehub/operation';
  const historyCall = sampleHistoryCall();
  const catalogName = sampleCatalogCall.operation;
  const historyName = historyCall.operation;
  let catalogReply;
  let historyReply;
  let shiftedReply;
  let legacyReply;
  const readSchema = path => JSON.parse(readFileSync(new URL(`../../contracts/schemas/${path}`, import.meta.url), 'utf8'));
  const ajv = new Ajv2020();
  addFormats(ajv);
  ajv.addSchema(readSchema('protocol.schema.json'));
  const validateCatalog = ajv.compile(readSchema(`operations/${catalogName}.output.schema.json`));
  const validateHistory = ajv.compile(readSchema(`operations/${historyName}.output.schema.json`));
  const validateLegacyBars = ajv.compile(readSchema('operations/market.getBars.output.schema.json'));

  test.beforeAll(async ({ request, baseURL }) => {
    const headers = { Origin: new URL(baseURL).origin };
    const capture = async call => {
      const response = await request.post(operation, { data: call, headers });
      expect(response.status()).toBe(200);
      const reply = await response.json();
      expect(reply.ok).toBe(true);
      return reply;
    };
    catalogReply = await capture(sampleCatalogCall);
    historyReply = await capture(historyCall);
    const shiftedCall = structuredClone(historyCall);
    shiftedCall.payload.from_ms += 60_000;
    shiftedReply = await capture(shiftedCall);
    const { series: { instrument_id, timeframe, provider_id }, from_ms, to_ms } = historyCall.payload;
    legacyReply = await capture({ operation: 'market.getBars', payload: { instrument_id, timeframe, provider_id, from_ms, to_ms } });
    expect(validateLegacyBars(legacyReply.result)).toBe(true);
    expect(legacyReply.result.dataset).toMatchObject({ instrument_id, timeframe, provider_id, price_adjustment: 'raw' });
    expect(legacyReply.result.bars).toHaveLength(40);
    for (const [call, reply] of [[historyCall, historyReply], [shiftedCall, shiftedReply]]) {
      expect(reply.result.snapshot).toMatchObject({
        from_ms: call.payload.from_ms, to_ms: call.payload.to_ms, series: call.payload.series,
        dataset_revision: call.payload.expected_revision,
      });
      expect(reply.result.bars).toHaveLength(40);
      expect(reply.result.stream_position).toBeNull();
      expect(reply.result.next_cursor).toBeNull();
    }
  });

  const invalidData = 'The data response failed validation.';
  const invalidHistory = 'History reply satisfies the operation output schema';
  const contradictory = 'History reply matches the sample request';
  const chartFailure = 'The chart could not be displayed. Review the bars in the data table.';
  for (const [kind, scenario] of [
    ['compatible', { stage: 'sample-verified' }],
    ['equivalent price tick', { equivalentTick: true, stage: 'sample-verified' }],
    ['chart mount failure', { mountFailure: true, stage: 'history-verified', error: 'Initial sample price series is painted' }],
    ['shifted snapshot', { shifted: true, error: contradictory }],
    ['contradictory dataset adjustment', { schemaValid: true, error: contradictory,
      mutate: history => { history.result.dataset.price_adjustment = 'split_adjusted'; } }],
    ['unsolicited stream position', { schemaValid: true, error: contradictory,
      mutate: history => {
        history.result.stream_position = { stream_id: history.result.snapshot.dataset_id, epoch: 'fixture', sequence: 0 };
      } }],
    ['contradictory quality source', { schemaValid: true, error: contradictory,
      mutate: history => { history.result.quality_state.source_kind = 'native_bars'; } }],
    ['contradictory bar prices', { schemaValid: true, error: contradictory,
      mutate: history => { history.result.bars[0].high = history.result.bars[0].low - 1; } }],
    ['omitted snapshot start', { mutate: history => delete history.result.snapshot.from_ms }],
    ['omitted snapshot content hash', { mutate: history => delete history.result.snapshot.content_hash }],
    ['malformed snapshot content hash', { mutate: history => { history.result.snapshot.content_hash = 'X'.repeat(64); } }],
    ['omitted nullable stream position', { mutate: history => delete history.result.stream_position }],
    ['invalid bar trading day', { mutate: history => { history.result.bars[0].trading_day = '2024-02-30'; } }],
    ['omitted history result', { mutate: history => delete history.result }],
    ['legacy bars returned for frozen history', { legacy: true }],
    ['omitted catalog provider mappings', { catalogInvalid: true, stage: 'dom-content-loaded',
      error: 'Catalog reply satisfies the operation output schema' }],
    ['failed history response', { failed: true, error: 'returned HTTP 503 (WORKER_EXITED)' }],
    ['history completed before catalog', { outOfOrder: true,
      error: `Previous response finished before ${historyName} was sent` }],
  ]) {
    test(`public sample startup handles the ${kind} case`, async ({ page }) => {
      const {
        outOfOrder, shifted, legacy, mutate, catalogInvalid, failed, equivalentTick, mountFailure,
        schemaValid = !(legacy || mutate), stage = 'catalog-verified', error = invalidHistory,
      } = scenario;
      const catalog = structuredClone(catalogReply);
      const instrument = catalog.result.instruments.find(item => item.id === historyCall.payload.series.instrument_id);
      if (catalogInvalid) delete instrument.provider_mappings;
      if (equivalentTick) {
        await page.setViewportSize({ width: 720, height: 900 });
        expect(instrument.price_tick).toBe('0.2');
        instrument.price_tick += '0'.repeat(100);
        expect(Number(instrument.price_tick)).toBe(0.2);
      }
      if (mountFailure) {
        // Fail the real chart's first layout subscription, leaving the market response alone.
        await page.addInitScript(() => {
          const observe = ResizeObserver.prototype.observe;
          ResizeObserver.prototype.observe = function(element, options) {
            if (element.classList.contains('chart')) throw new Error('Synthetic chart mount failure');
            return observe.call(this, element, options);
          };
        });
      }
      const history = structuredClone(legacy ? legacyReply : shifted ? shiftedReply : historyReply);
      if (mutate) mutate(history);
      expect(validateCatalog(catalog.result)).toBe(!catalogInvalid);
      expect(validateHistory(history.result)).toBe(schemaValid);
      if (outOfOrder) {
        await page.addInitScript(({ operation, historyCall }) => {
          const fetch = window.fetch;
          let earlyHistory;
          window.fetch = (url, options) => {
            if (url !== operation) return fetch(url, options);
            const call = JSON.parse(options.body);
            if (call.operation === 'instrument.search') {
              const catalog = fetch(url, options);
              earlyHistory = fetch(url, { ...options, body: JSON.stringify(historyCall) });
              return catalog;
            }
            return call.operation === historyCall.operation && earlyHistory ? earlyHistory : fetch(url, options);
          };
        }, { operation, historyCall });
      }

      const calls = [];
      const completed = [];
      const releaseCatalog = Promise.withResolvers();
      const recordCompletion = request => {
        if (new URL(request.url()).pathname !== operation || request.method() !== 'POST') return;
        const { operation: name } = request.postDataJSON();
        completed.push(name);
        if (name === historyName) releaseCatalog.resolve();
      };
      page.on('requestfinished', recordCompletion);
      await page.route(`**${operation}`, async route => {
        const call = route.request().postDataJSON();
        calls.push(call);
        if (call.operation === catalogName) {
          if (outOfOrder) await releaseCatalog.promise;
          return route.fulfill({ json: catalog });
        }
        if (failed) return route.fulfill({ status: 503, json: { ok: false, error: { code: 'WORKER_EXITED', retryable: true } } });
        return route.fulfill({ json: history });
      });

      try {
        const reports = [];
        const startupEvents = ['request', 'response', 'requestfinished', 'requestfailed', 'framenavigated', 'domcontentloaded'];
        const listenerCounts = () => startupEvents.map(event => page.listenerCount(event));
        const before = listenerCounts();
        const countsAtAttachment = [];
        const startup = openDefaultSample(page, { reportStartup: async report => {
          reports.push(report);
          countsAtAttachment.push(listenerCounts());
          await attachSampleStartup(report);
          if (failed) throw new Error('Simulated attachment sink failure');
        } }).catch(error => error);
        const result = await startup;
        const plot = page.getByRole('region', { name: 'Price chart' });
        const alerts = plot.getByRole('alert');
        const readChart = async () => ({
          barCount: await chart(page).evaluateAll(elements => elements.map(element => element.getAttribute('data-bars'))),
          alert: await alerts.allTextContents(),
          pricePixels: await painted(page, 'candlestick'),
        });
        const draws = stage === 'sample-verified' || outOfOrder;
        await expect.poll(async () => {
          const { alert, pricePixels } = await readChart();
          return alert.length > 0 || pricePixels > 20;
        }, { message: 'Wait for the actual browser presentation to settle' }).toBe(true);
        const presentation = await readChart();
        await test.info().attach('sample-startup-product', {
          contentType: 'application/json',
          body: JSON.stringify({ ...presentation, responseSchemaValid: schemaValid, error: result instanceof Error ? result.message : null }),
        });
        expect(presentation.alert, 'Price chart alerts').toEqual(draws ? [] : [
          mountFailure ? chartFailure : failed ? 'The market worker is disconnected.' : invalidData,
        ]);
        if (draws) {
          expect(presentation.barCount).toEqual(['40']);
          expect(presentation.pricePixels).toBeGreaterThan(20);
          if (equivalentTick) {
            await page.setViewportSize({ width: 1366, height: 900 });
            await openPanel(page, 'Watchlist');
            const details = page.getByRole('region', { name: 'Selected instrument details' });
            await expect(details.locator('.quote')).toHaveText('3,500.8 CNY');
          }
        } else {
          expect(presentation.pricePixels).toBe(0);
          await expect(plot.locator('.chart canvas')).toHaveCount(0);
          await expect(plot.getByRole('button', { name: 'Retry history' })).toHaveCount(failed ? 1 : 0);
        }
        expect(calls).toEqual(catalogInvalid ? [sampleCatalogCall] : [sampleCatalogCall, historyCall]);
        expect(completed).toEqual(catalogInvalid ? [catalogName] : outOfOrder ? [historyName, catalogName] : [catalogName, historyName]);
        expect(reports).toHaveLength(1);
        expect(countsAtAttachment).toEqual([before]);
        const [observation] = reports;
        expect(observation.stage).toBe(stage);
        if (catalogInvalid) expect(observation.catalog_verified_ms).toBeUndefined();
        else expect(observation.catalog_verified_ms).toBeGreaterThanOrEqual(observation.document.dom_content_loaded_ms);
        if (stage === 'sample-verified') {
          expect(result).toEqual(history.result.snapshot);
          expect(observation.history_verified_ms).toBeGreaterThanOrEqual(observation.catalog_verified_ms);
          expect(observation.sample_verified_ms).toBeGreaterThanOrEqual(observation.history_verified_ms);
        } else {
          expect(result).toBeInstanceOf(Error);
          expect(result.message).toContain(error);
          if (mountFailure) {
            expect(observation.history_verified_ms).toBeGreaterThanOrEqual(observation.catalog_verified_ms);
            expect(observation.chart_failed_ms).toBeGreaterThanOrEqual(observation.history_verified_ms);
          } else expect(observation.history_verified_ms).toBeUndefined();
          expect(observation.sample_verified_ms).toBeUndefined();
        }
      } finally {
        releaseCatalog.resolve();
        page.off('requestfinished', recordCompletion);
        await page.unrouteAll({ behavior: 'wait' });
      }
    });
  }
});

async function routeCompletedRepair(page, rewrite, headers) {
  const observed = Promise.withResolvers();
  await page.route(`**${stream}`, async route => {
    const response = await route.fetch(replayStage);
    const status = response.status();
    const contentType = response.headers()['content-type'];
    const text = await response.text();
    let frames = [];
    let parseError;
    try { frames = text.trimEnd().split('\n').map(line => JSON.parse(line)); }
    catch (error) { parseError = error.message; }
    const terminal = frames.at(-1);
    const repair = frames[3]?.repair;
    const complete = status === 200 && contentType === 'application/x-ndjson' && frames.length === 5
      && Boolean(repair?.position && repair.snapshot && Array.isArray(repair.bars))
      && terminal?.ok === true && terminal.result?.status === 'complete';
    const outcome = { status, contentType, terminal: structuredClone(terminal), frameCount: frames.length, complete, parseError };
    if (!complete) {
      await route.fulfill({ response });
      // A genuine bridge admission rejection may be retried by the application.
      if (status === 429 && terminal?.ok === false && terminal.error?.code === 'BUSY' && terminal.error.retryable === true) return;
    } else {
      rewrite(frames);
      await route.fulfill({ status, contentType, headers, body: frames.map(frame => JSON.stringify(frame) + '\n').join('') });
    }
    observed.resolve(outcome);
  });
  return async () => {
    expect(await observed.promise, 'Real bridge reply must contain a complete repair before alteration').toMatchObject({
      status: 200, contentType: 'application/x-ndjson', frameCount: 5,
      terminal: { ok: true, result: { status: 'complete' } }, complete: true,
    });
  };
}

function holdRepair(page, terminal) {
  return routeCompletedRepair(page, frames => {
    if (terminal) frames[frames.length - 1] = terminal;
  }, { 'X-Hold-Closed-Bar-Repair': '1' });
}

test('a held bridge reply keeps the gap until the valid repair and complete are both received', async ({ page }) => {
  const verifyUpstream = await holdRepair(page);
  await openRepairSample(page, 'repairable');
  // Keep the real snapshot and worker. Delay just the preparation reply so
  // the wait for a gap cannot use a single five-second budget from the click.
  let preparations = 0;
  await page.route('**/__scopehub/operation', async route => {
    const { operation, payload } = route.request().postDataJSON();
    if (operation !== 'market.readHistoryPage' || payload.include_stream_position !== true) return route.continue();
    const response = await route.fetch(replayStage);
    if (response.ok()) {
      preparations++;
      await new Promise(resolve => setTimeout(resolve, 5_200));
    }
    await route.fulfill({ response });
  });
  await play(page);
  await verifyUpstream();
  await expectGap(page);
  expect(preparations).toBe(1);
  await page.evaluate(() => window.sendClosedBarRepair());
  await expectGap(page);
  await page.evaluate(() => window.finishClosedBarRepair());
  await expect(page.getByText('Closed-bar gap repaired in chart', { exact: true })).toBeVisible();
  await expect(chart(page)).toHaveAttribute('data-bars', '40');
});

for (const action of ['stop', 'reselect']) {
  test(`${action} cancels the chart projection while the final repair digest is pending`, async ({ page }) => {
    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    await page.addInitScript(() => {
      const subtle = crypto.subtle;
      const digest = subtle.digest.bind(subtle);
      const held = Promise.withResolvers();
      window.releaseClosedBarDigest = held.resolve;
      let calls = 0;
      subtle.digest = async (...args) => {
        const call = ++calls;
        const result = await digest(...args);
        // The seed and two bars use one SHA-256 call each.
        if (call === 3) {
          window.closedBarDigestHeld = true;
          await held.promise;
        }
        return result;
      };
    });
    await openRepairSample(page, 'repairable');
    await trackProjection(page);
    await play(page);
    await page.waitForFunction(() => window.closedBarDigestHeld);
    expect((await captureFrames(page)).at(-1)).toEqual({ ok: true, result: { status: 'complete' } });
    await expectGap(page);

    if (action === 'stop') {
      await control(page, 'Stop replay').click();
      await expect(chart(page)).toHaveAttribute('data-bars', '39');
      await expect(legend(page)).toContainText(/C 3500\.8Volume 110.*Partial bar/);
    } else {
      await control(page, '5m').click();
      await expect(chart(page)).toHaveAttribute('data-timeframe', '5m');
      await expect(chart(page)).toHaveAttribute('data-bars', '8');
    }
    await page.evaluate(async () => {
      window.releaseClosedBarDigest();
      // Allow the completed digest and resulting React updates to reach the view.
      await new Promise(requestAnimationFrame);
      await new Promise(requestAnimationFrame);
    });
    expect(await page.evaluate(() => window.closedBarProjectionCounts)).not.toContain('40');
    await expect(page.getByText('Closed-bar gap repaired in chart', { exact: true })).toHaveCount(0);
    await expect(page.locator('.replay-status')).toHaveCount(0);
    expect(pageErrors).toEqual([]);
  });
}

test('a worker-exited terminal leaves an already visible gap and discards a staged repair', async ({ page }) => {
  const verifyUpstream = await holdRepair(page, { ok: false, error: { code: 'WORKER_EXITED', retryable: true } });
  await openRepairSample(page, 'repairable');
  await play(page);
  await verifyUpstream();
  await expectGap(page);
  await page.evaluate(() => window.sendClosedBarRepair());
  await expectGap(page);
  await page.evaluate(() => window.finishClosedBarRepair());
  await expect(page.getByRole('alert')).toHaveText('Replay failed. The market worker is disconnected.');
  await expect(fact(page, 'Worker connection')).toHaveText('Disconnected');
  await expectGap(page);
  await readFrozenRows(page);
});

for (const [name, alter, failure = 'The data response failed validation.'] of [
  ['wrong repair cursor', frames => { frames[3].repair.position.sequence--; }],
  ['wrong repair bounds', frames => { frames[3].repair.snapshot.from_ms--; }],
  ['an update in place of a repair', frames => { frames[3] = frames[1]; }],
  ['complete without a repair', frames => { frames.splice(3, 1); }],
  ['a repair with a gapped terminal', frames => { frames[4].result = { status: 'gapped', reason: 'sequence_gap' }; }],
  ['a missing-minute volume changed from 90 to 91 with the original repair digest', frames => {
    expect(frames[3].repair.bars[0].volume).toBe(90);
    expect(frames[3].repair.snapshot.content_hash).toMatch(/^[0-9a-f]{64}$/);
    frames[3].repair.bars[0].volume = 91;
  }, 'The closed-bar repair could not be verified.'],
]) {
  test(`a bridge reply altered to carry ${name} leaves the gap visible`, async ({ page }) => {
    const verifyUpstream = await routeCompletedRepair(page, alter);
    await openRepairSample(page, 'repairable');
    await trackProjection(page);
    await play(page);
    await verifyUpstream();
    await expect(page.getByRole('alert')).toHaveText(`Replay failed. ${failure}`, replayStage);
    await expectGap(page);
    expect(await page.evaluate(() => window.closedBarProjectionCounts)).not.toContain('40');
    await readFrozenRows(page);
  });
}
