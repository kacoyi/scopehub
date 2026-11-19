import { expect, test as base } from '@playwright/test';
import { future, openDefaultSample } from '../ui/sample-startup.mjs';
import { painted } from '../ui/chart-pixels.mjs';
import { replayStage, waitForReplayRequest } from '../ui/replay-stages.mjs';
import { control, openPanel } from '../ui/workspace-controls.mjs';

const operationPath = '/__scopehub/operation';
const streamPath = '/__scopehub/market-stream';
const stock = 'SIM.CN.STOCK.001';
const revisionId = suffix => `scopehub-synthetic-v1:20241013:${suffix}`;
const observed = '2024-01-04T01:09:30.000Z';
const closed = '2024-01-04T01:10:00.000Z';
const fullRange = { from_ms: Date.UTC(2024, 0, 1), to_ms: Date.UTC(2024, 0, 6), warmup_bars: 0 };
const normal = {
  revision: 'normal', bars: 40, session: 'Open', freshness: 'Current', coverage: 'Observed', quality: 'Fresh',
  digest: '8c5cccacb89d3cfa0a2f06418caa75cd5be6436e3cdd3ed4fadddd766cdff900',
};
const stale = {
  ...normal, revision: 'stale', session: 'Closed', freshness: 'Stale', quality: 'Stale',
  asOf: '2024-01-04T01:39:30.000Z', digest: '6815dbdd58b4ce45f06bfae29c707260279e533f4b5b8df90a55fc3addf0c3a4',
};
const gapped = {
  ...normal, revision: 'gapped', bars: 34, coverage: 'Gapped', quality: 'Gapped',
  digest: '5fa8715b7a41e79fcc29eb80296e2b32291939ecded22ac75294c3767ee81f89',
};
const empty = { ...normal, bars: 0, digest: '7d5f49b610ab82e45010ec60096472668d754cacd20fc865a43072a153a172d8' };
const closedCurrent = {
  ...normal, session: 'Closed', asOf: closed, last: closed,
  digest: 'f3ef9cc2e1f0167c7606cf52c08988e694f7e42d1323cf8dfcc9c1c38350b3f0',
};
const gaps = [
  ['2024-01-02T13:02:00.000Z', '2024-01-02T13:03:00.000Z'],
  ['2024-01-02T13:05:00.000Z', '2024-01-02T13:10:00.000Z'],
];

const plot = page => page.getByRole('region', { name: 'Price chart' });
const chart = page => plot(page).locator('.history');
const canvases = page => plot(page).locator('.chart canvas');
const sources = page => page.getByRole('complementary', { name: 'Data sources' });
const test = base.extend({
  calls: [async ({ page, baseURL }, use) => {
    const calls = [];
    const outside = [];
    const errors = [];
    page.on('request', request => {
      const url = new URL(request.url());
      if (!['http:', 'https:'].includes(url.protocol)) return;
      if (url.origin !== baseURL) outside.push(url.origin);
      if (url.pathname.startsWith('/__scopehub/')) calls.push(request.postDataJSON());
    });
    page.on('websocket', socket => {
      const url = new URL(socket.url());
      const origin = (url.protocol === 'wss:' ? 'https://' : 'http://') + url.host;
      if (origin !== baseURL) outside.push(url.origin);
    });
    page.on('pageerror', error => errors.push(error.message));
    await use(calls);
    expect(outside, 'external HTTP and WebSocket traffic').toEqual([]);
    expect(errors, 'uncaught browser errors').toEqual([]);
  }, { auto: true }],
});

function fact(page, label) {
  return sources(page).locator('dt').filter({ hasText: new RegExp(`^${RegExp.escape(label)}$`) })
    .locator('xpath=following-sibling::dd[1]');
}

async function expectFacts(page, expected) {
  for (const [label, value] of Object.entries(expected)) await expect(fact(page, label)).toHaveText(value);
}

async function expectProvenance(page, expected) {
  await expectFacts(page, {
    Source: 'scopehub.synthetic', 'Source mode': 'Simulated', 'Source kind': 'Synthetic',
    Revision: revisionId(expected.revision), 'Time zone': 'Asia/Shanghai',
    'Quality at sample time': expected.quality, Session: expected.session, Connection: 'Offline',
    Freshness: expected.freshness, Coverage: expected.coverage, 'Input digest': expected.digest,
  });
  await expect(fact(page, 'As of (UTC)').locator('time')).toHaveAttribute('datetime', expected.asOf ?? observed);
  await expect(fact(page, 'Last observed (UTC)').locator('time')).toHaveAttribute('datetime', expected.last ?? observed);
  if (expected.coverage === 'Observed') await expect(fact(page, 'Known gaps')).toHaveText('None reported');
}

async function expectPaintedHistory(page, instrument, timeframe, bars) {
  await expect(chart(page)).toHaveAttribute('data-instrument', instrument);
  await expect(chart(page)).toHaveAttribute('data-timeframe', timeframe);
  await expect(chart(page)).toHaveAttribute('data-bars', String(bars));
  await expect(plot(page).getByRole('img', { name: `Candlestick and volume chart for ${instrument}` })).toBeVisible();
  await expect.poll(() => painted(page, 'candlestick')).toBeGreaterThan(20);
}

async function expectReady(page, instrument, timeframe, bars) {
  await expectPaintedHistory(page, instrument, timeframe, bars);
  await expect(plot(page).getByRole('alert')).toHaveCount(0);
}

function historyRequest(request) {
  if (new URL(request.url()).pathname !== operationPath) return;
  const call = request.postDataJSON();
  return call.operation === 'market.readHistoryPage' ? call : undefined;
}

async function selectStock(page) {
  await control(page, 'Search symbols').click();
  const dialog = page.getByRole('dialog', { name: 'Search symbols' });
  await dialog.getByLabel('Search symbols', { exact: true }).fill('CN.STOCK.001');
  await dialog.getByRole('option', { name: /CN\.STOCK\.001/ }).click();
  await expect(control(page, 'Search symbols')).toContainText('CN.STOCK.001');
}

test('real history keeps provenance and painted bars across normal, stale, missing and empty samples', async ({ page, calls }, testInfo) => {
  await page.goto('/');
  await openPanel(page, 'Data sources');
  const revision = sources(page).getByLabel('Sample revision', { exact: true });
  const date = sources(page).getByLabel('Sample date (UTC)', { exact: true });
  await expect(revision).toHaveCount(0);
  await expect(date).toHaveCount(0);
  await expect(page.getByText('No data source', { exact: true })).toBeVisible();
  await expect(canvases(page)).toHaveCount(0);
  expect(calls).toEqual([]);

  const normalResponse = page.waitForResponse(response => historyRequest(response.request())?.payload.expected_revision === revisionId('normal'));
  await openDefaultSample(page, { trigger: () => control(page, 'Open offline sample').click() });
  await expect(page.getByText('Offline sample', { exact: true })).toBeVisible();
  await openPanel(page, 'Data sources');
  await expect(revision.locator('option')).toHaveText([
    'Normal', 'Stale observations', 'Missing bars', 'Repairable closed-bar gap', 'Unrepairable closed-bar gap',
  ]);
  await expect(revision.locator('option:checked')).toHaveText('Normal');
  await expect(date).toHaveValue('');
  await expectProvenance(page, normal);
  await expectReady(page, future, '1m', normal.bars);
  await expect(plot(page).getByRole('status').filter({ hasText: /^History ready$/ })).toBeVisible();
  const { result: { bars: normalBars } } = await (await normalResponse).json();

  await revision.selectOption({ label: 'Stale observations' });
  await expectProvenance(page, stale);
  await expectReady(page, future, '1m', stale.bars);
  await expect(plot(page).getByRole('status').filter({ hasText: /^Stale data$/ })).toBeVisible();
  await expect(page.getByLabel('Selected bar')).toContainText(/C 3500\.8Volume 110.*Partial bar/);

  const missingResponse = page.waitForResponse(response => historyRequest(response.request())?.payload.expected_revision === revisionId('gapped'));
  await revision.selectOption({ label: 'Missing bars' });
  await expectProvenance(page, gapped);
  await expectReady(page, future, '1m', gapped.bars);
  await expect(plot(page).getByRole('status').filter({ hasText: /^Known gaps$/ })).toBeVisible();
  const intervals = fact(page, 'Known gaps').getByRole('list', { name: 'Half-open intervals in UTC, inclusive start and exclusive end' });
  await expect(intervals.getByRole('listitem')).toHaveText([
    '[2024-01-02 13:02:00, 2024-01-02 13:03:00) UTC',
    '[2024-01-02 13:05:00, 2024-01-02 13:10:00) UTC',
  ]);
  expect(await intervals.locator('time').evaluateAll(times => times.map(time => time.dateTime))).toEqual(gaps.flat());
  const { result } = await (await missingResponse).json();
  const gapBounds = gaps.map(([from, to]) => ({ from_ms: Date.parse(from), to_ms: Date.parse(to) }));
  expect(result.quality_state.gaps).toEqual(gapBounds);
  expect(result.bars).toEqual(normalBars.filter(({ time_ms }) =>
    !gapBounds.some(({ from_ms, to_ms }) => from_ms <= time_ms && time_ms < to_ms),
  ));
  await page.screenshot({ path: testInfo.outputPath('synthetic-market-quality.png'), animations: 'disabled' });

  await revision.selectOption({ label: 'Normal' });
  await expectProvenance(page, normal);
  const emptyResponse = page.waitForResponse(response => historyRequest(response.request())?.payload.to_ms === Date.UTC(2024, 0, 2));
  await date.fill('2024-01-01');
  const response = await emptyResponse;
  expect(response.status()).toBe(200);
  expect(await response.json()).toMatchObject({ ok: true, result: {
    bars: [], total_bars: 0, next_cursor: null,
    snapshot: { ...fullRange, to_ms: Date.UTC(2024, 0, 2), content_hash: empty.digest },
  } });
  await expect(plot(page).getByRole('status').filter({ hasText: 'No bars in this sample range.' })).toBeVisible();
  await expect(chart(page)).toHaveAttribute('data-bars', '0');
  await expect(canvases(page)).toHaveCount(0);
  await expect(plot(page).getByRole('alert')).toHaveCount(0);
  await expectProvenance(page, empty);

  await date.fill('');
  await expectProvenance(page, normal);
  await expectReady(page, future, '1m', normal.bars);
  expect(calls.map(({ operation }) => operation)).toEqual([
    'instrument.search', ...Array(6).fill('market.readHistoryPage'),
  ]);
  expect(calls.slice(1).map(({ payload }) => payload)).toEqual(
    ['normal', 'stale', 'gapped', 'normal', 'normal', 'normal'].map((suffix, index) => ({
      ...fullRange, to_ms: index === 4 ? Date.UTC(2024, 0, 2) : fullRange.to_ms,
      expected_revision: revisionId(suffix),
      series: {
        instrument_id: future, provider_id: 'scopehub.synthetic', timeframe: '1m', price_adjustment: 'raw',
        session_scope: 'sample-sessions', calendar_id: `${future}.sample-calendar.v1`, quantity_unit: 'contracts',
      },
    })),
  );
});

test('a real closed sample remains current when its final observation arrived', async ({ page }) => {
  await page.route(`**${operationPath}`, async route => {
    const call = historyRequest(route.request());
    if (call?.payload.series.instrument_id !== future) return route.continue();
    // Select the real final observation through the ordinary operation. Keep the core reply intact.
    return route.continue({ postData: JSON.stringify({ ...call, payload: { ...call.payload, as_of_ms: Date.parse(closed) } }) });
  });
  const [pinned] = await Promise.all([
    page.waitForResponse(response => response.status() === 200
      && historyRequest(response.request())?.payload.as_of_ms === Date.parse(closed), replayStage),
    page.goto('/#demo'),
  ]);
  expect((await pinned.json()).ok).toBe(true);
  await openPanel(page, 'Data sources');
  await expectProvenance(page, closedCurrent);
  await expectReady(page, future, '1m', closedCurrent.bars);
  await page.mouse.move(1, 1);
  await expect(page.getByLabel('Selected bar')).toContainText(/C 3501Volume 130.*Closed bar/);
});

test('a replacement chart is ready while the cancelled old history reply is still held', async ({ page, calls }) => {
  await openDefaultSample(page);
  await expectReady(page, future, '1m', normal.bars);
  await openPanel(page, 'Data sources');
  await expectProvenance(page, normal);

  const oldReady = Promise.withResolvers();
  const releaseOld = Promise.withResolvers();
  const oldFinished = Promise.withResolvers();
  await page.route(`**${operationPath}`, async route => {
    const request = route.request();
    const series = historyRequest(request)?.payload.series;
    if (series?.timeframe !== '5m' || series.instrument_id !== future) return route.continue();
    try {
      const response = await route.fetch({ timeout: 10_000 });
      oldReady.resolve({ request, reply: await response.json() });
      await releaseOld.promise;
      await route.fulfill({ response });
    } catch (error) {
      oldReady.reject(error);
      if (!request.failure()) throw error;
    } finally {
      oldFinished.resolve();
    }
  });

  try {
    await control(page, '5m').click();
    await expect(plot(page).getByText('Loading synthetic history…', { exact: true })).toBeVisible();
    await expect(canvases(page)).toHaveCount(0);
    await expectFacts(page, { 'Input digest': 'Unknown', 'Last observed (UTC)': 'Unknown' });
    const { request, reply } = await oldReady.promise;
    expect(reply).toMatchObject({ ok: true, result: {
      snapshot: { series: { instrument_id: future, timeframe: '5m' } }, total_bars: 8,
    } });

    await plot(page).evaluate(element => {
      const seen = new Set();
      window.seriesDrawnAfterSwitch = seen;
      const observer = new MutationObserver(() => {
        for (const history of element.querySelectorAll('.history:has(canvas)')) seen.add(history.dataset.instrument);
      });
      observer.observe(element, { childList: true, subtree: true, attributes: true, attributeFilter: ['data-instrument'] });
    });
    const [cancelled] = await Promise.all([
      page.waitForEvent('requestfailed', { predicate: candidate => candidate === request, timeout: 6_000 }),
      (async () => {
        await selectStock(page);
        await expectReady(page, stock, '5m', 8);
      })(),
    ]);
    expect(cancelled.failure()?.errorText).toContain('ERR_ABORTED');

    // The stock chart is already painted before the held futures reply is released.
    releaseOld.resolve();
    await oldFinished.promise;
    await expectReady(page, stock, '5m', 8);
    expect(await page.evaluate(() => [...window.seriesDrawnAfterSwitch])).toEqual([stock]);
    await expect(sources(page).getByLabel('Sample revision', { exact: true })).toHaveCount(0);
    expect(calls.filter(call => call.operation === 'market.readHistoryPage').map(({ payload }) => [
      payload.series.instrument_id, payload.series.timeframe,
    ])).toEqual([[future, '1m'], [future, '5m'], [stock, '5m']]);
  } finally {
    releaseOld.resolve();
  }
});

const connectionFailures = [
  {
    name: 'worker exit', alert: 'The market worker is disconnected.', fact: 'Worker connection', value: 'Disconnected',
    unseen: 'Local transport', inject: route => route.fulfill({ status: 502, json: {
      ok: false, error: { code: 'WORKER_EXITED', retryable: true },
    } }),
  },
  {
    name: 'unreachable preview', alert: 'Disconnected from the local preview. Retry when the connection is restored.',
    fact: 'Local transport', value: 'Unavailable', unseen: 'Worker connection', inject: route => route.abort('connectionfailed'),
  },
];

async function expectNoConnectionFailure(page) {
  await expect(fact(page, 'Worker connection')).toHaveCount(0);
  await expect(fact(page, 'Local transport')).toHaveCount(0);
}

for (const failure of connectionFailures) {
  test(`${failure.name} leaves source connection unknown when selected history is unavailable`, async ({ page, calls }) => {
    await openDefaultSample(page);
    await expectReady(page, future, '1m', normal.bars);
    await openPanel(page, 'Data sources');
    let injected = false;
    await page.route(`**${operationPath}`, async route => {
      const series = historyRequest(route.request())?.payload.series;
      if (injected || series?.instrument_id !== future || series.timeframe !== '5m') return route.continue();
      injected = true;
      await failure.inject(route);
    });

    await control(page, '5m').click();
    await expect(plot(page).getByRole('alert')).toHaveText(failure.alert);
    await expect(canvases(page)).toHaveCount(0);
    await expectFacts(page, {
      Connection: 'Unknown', Session: 'Unknown', Freshness: 'Unknown', Coverage: 'Unknown', 'Input digest': 'Unknown',
      [failure.fact]: failure.value,
    });
    await expect(fact(page, failure.unseen)).toHaveCount(0);
    await expect(control(page, '5m')).toHaveAttribute('aria-pressed', 'true');
    await control(page, 'Retry history').click();
    await expectReady(page, future, '5m', 8);
    await expectFacts(page, { Connection: 'Offline', Freshness: 'Current', Coverage: 'Observed' });
    await expectNoConnectionFailure(page);
    const historyCalls = calls.filter(call => call.operation === 'market.readHistoryPage');
    expect(historyCalls).toHaveLength(3);
    expect(historyCalls[2]).toEqual(historyCalls[1]);
  });

  test(`${failure.name} during replay preserves the frozen source and chart`, async ({ page, calls }) => {
    await openDefaultSample(page);
    await expectReady(page, future, '1m', normal.bars);
    await openPanel(page, 'Data sources');
    await expectProvenance(page, normal);
    await page.route(`**${streamPath}`, failure.inject, { times: 1 });

    await waitForReplayRequest(page, () => control(page, 'Play sample updates').click());
    await expect(plot(page).getByRole('alert')).toHaveText(`Replay failed. ${failure.alert}`);
    await expectPaintedHistory(page, future, '1m', normal.bars);
    await expectProvenance(page, normal);
    await expectFacts(page, { [failure.fact]: failure.value });
    await expect(fact(page, failure.unseen)).toHaveCount(0);

    await waitForReplayRequest(page, () => control(page, 'Retry replay').click());
    await expect(plot(page).getByText('Synthetic replay complete', { exact: true })).toBeVisible(replayStage);
    await page.mouse.move(1, 1);
    await expect(page.getByLabel('Selected bar')).toContainText(/C 3501Volume 130.*Closed bar/);
    await expectReady(page, future, '1m', normal.bars);
    await expectProvenance(page, normal);
    await expectNoConnectionFailure(page);
    expect(calls.filter(call => call.operation === 'market.subscribe')).toHaveLength(2);
  });
}

const readFailures = [
  ['OUTPUT_INVALID', future, 'The data response failed validation.'],
  ['SNAPSHOT_UNAVAILABLE', future, 'The selected historical revision is unavailable.'],
  ['DATA_UNAVAILABLE', stock, 'No history is available for this instrument.'],
];

for (const [code, instrument, label] of readFailures) {
  test(`${code} reports a distinct failure for the selected history`, async ({ page }) => {
    await openDefaultSample(page);
    await expectReady(page, future, '1m', normal.bars);
    await openPanel(page, 'Data sources');
    const isTarget = request => {
      const series = historyRequest(request)?.payload.series;
      return series?.instrument_id === instrument && series.timeframe === (instrument === future ? '5m' : '1m');
    };
    let targetedRequest;
    await page.route(`**${operationPath}`, route => {
      const request = route.request();
      if (!isTarget(request)) return route.continue();
      targetedRequest = request;
      if (code === 'OUTPUT_INVALID') return route.fulfill({ status: 502, json: { ok: false, error: { code, retryable: false } } });
      const call = historyRequest(request);
      const { payload } = call;
      if (code === 'SNAPSHOT_UNAVAILABLE') {
        expect(payload.expected_revision).toBe(revisionId('normal'));
        payload.expected_revision = revisionId('missing');
      } else {
        expect(payload.expected_revision).toBeUndefined();
        expect(payload.snapshot).toBeUndefined();
        payload.series.instrument_id = 'SIM.CN.STOCK.MISSING';
      }
      // Ask the core for an unavailable target and pass its typed rejection through unchanged.
      return route.continue({ postData: JSON.stringify(call) });
    });
    const pending = page.waitForResponse(response => response.request() === targetedRequest, { timeout: 10_000 });
    if (instrument === future) await control(page, '5m').click();
    else await selectStock(page);
    const response = await pending;
    expect(response.status()).toBe(code === 'OUTPUT_INVALID' ? 502 : 200);
    expect(await response.json()).toMatchObject({ ok: false, error: { code, retryable: false } });
    await expect(plot(page).getByRole('alert')).toHaveText(label);
    await expect(plot(page).getByText('No bars in this sample range.', { exact: true })).toHaveCount(0);
    await expect(canvases(page)).toHaveCount(0);
    await expectFacts(page, { Connection: 'Unknown', Session: 'Unknown', Freshness: 'Unknown', Coverage: 'Unknown', 'Input digest': 'Unknown' });
    await expectNoConnectionFailure(page);
    await expect(control(page, 'Retry history')).toHaveCount(0);
  });
}

function knownGapFrames({ series, after }) {
  return [
    { subscription: { status: 'accepted', from_position: after } },
    { event: {
      protocol_version: '1.1', type: 'event', event: 'market.stream_gap',
      message_id: 'a24c04c8-2439-40b5-9761-2fd5b858c82b', timestamp_ms: Date.parse(closed),
      stream_id: after.stream_id, epoch: after.epoch, sequence: after.sequence + 1,
      payload: {
        series, reason: 'source_gap', expected_sequence: after.sequence + 1, lost_count: 2,
        affected: { from_ms: Date.parse(observed), to_ms: Date.parse(closed) },
      },
    } },
  ];
}

const ndjson = frames => frames.map(frame => JSON.stringify(frame) + '\n').join('');
const replayGap = page => sources(page).getByRole('region', { name: 'Replay gap' });
const replayGapDetails = page => plot(page).getByRole('group', { name: 'Replay gap details' });

async function expectReplayGap(page, known) {
  const reason = known ? 'The source reported missing updates.' : 'The stream restarted.';
  await expect(plot(page).getByText(`Replay gap. ${reason}`, { exact: true })).toBeVisible(replayStage);
  const interval = '[2024-01-04 01:09:30, 2024-01-04 01:10:00) UTC';
  await expect(replayGapDetails(page)).toHaveText(known ? `Affected range ${interval} · 2 lost updates` : 'Affected range unknown · Lost updates unknown');
  await expect(replayGap(page)).toBeVisible();
  await expectFacts(page, { Reason: reason, 'Affected range (UTC)': known ? interval : 'Unknown', 'Lost updates': known ? '2' : 'Unknown' });
  expect(await replayGap(page).locator('time').evaluateAll(times => times.map(time => time.dateTime))).toEqual(known ? [observed, closed] : []);
}

for (const known of [true, false]) {
  test(`a replay gap preserves ${known ? 'its reported interval and count' : 'unknown interval and count'}`, async ({ page }) => {
    await openDefaultSample(page);
    await expectReady(page, future, '1m', normal.bars);
    await openPanel(page, 'Data sources');
    await page.route(`**${streamPath}`, route => {
      const call = route.request().postDataJSON();
      if (!known) {
        // A prior epoch yields a real core backfill response without a stream-gap event.
        call.payload.after.epoch = 'prior-epoch';
        return route.continue({ postData: JSON.stringify(call) });
      }
      const frames = [...knownGapFrames(call.payload), { ok: true, result: { status: 'gapped', reason: 'source_gap' } }];
      return route.fulfill({ contentType: 'application/x-ndjson', body: ndjson(frames) });
    });

    await waitForReplayRequest(page, () => control(page, 'Play sample updates').click());
    await expectReplayGap(page, known);
    await expectReady(page, future, '1m', normal.bars);
    await expectProvenance(page, normal);
    await page.mouse.move(1, 1);
    await expect(page.getByLabel('Selected bar')).toContainText(/C 3500\.8Volume 110.*Partial bar/);
  });
}

test('a validated gap survives a held replay timeout and clears on retry or a new selection', async ({ page }) => {
  await page.addInitScript(path => {
    const release = Promise.withResolvers();
    window.releaseReplayTerminal = release.resolve;
    const fetch = window.fetch;
    window.fetch = async (...args) => {
      const response = await fetch(...args);
      if (new URL(response.url).pathname !== path || !response.headers.has('x-hold-replay-terminal')) return response;
      const lines = (await response.text()).trimEnd().split('\n');
      const terminal = lines.pop();
      const encoder = new TextEncoder();
      // Keep the browser's ordinary NDJSON reader, but hold its final line until the test releases it.
      const body = new ReadableStream({
        start(controller) { controller.enqueue(encoder.encode(lines.join('\n') + '\n')); },
        async pull(controller) {
          await release.promise;
          controller.enqueue(encoder.encode(terminal + '\n'));
          controller.close();
        },
      });
      return new Response(body, { headers: { 'Content-Type': 'application/x-ndjson' } });
    };
  }, streamPath);
  await openDefaultSample(page);
  await expectReady(page, future, '1m', normal.bars);
  await openPanel(page, 'Data sources');
  await page.route(`**${streamPath}`, route => {
    const { payload } = route.request().postDataJSON();
    const frames = [...knownGapFrames(payload), { ok: false, error: { code: 'TIMEOUT', retryable: true } }];
    return route.fulfill({ headers: { 'Content-Type': 'application/x-ndjson', 'X-Hold-Replay-Terminal': '1' }, body: ndjson(frames) });
  }, { times: 1 });

  try {
    await waitForReplayRequest(page, () => control(page, 'Play sample updates').click());
    await expectReplayGap(page, true);
    await expect(plot(page).getByRole('alert')).toHaveCount(0);
    await expect(control(page, 'Stop replay')).toBeVisible();
    await expectProvenance(page, normal);
  } finally {
    await page.evaluate(() => window.releaseReplayTerminal());
  }
  await expect(plot(page).getByRole('alert')).toHaveText('Replay failed. The market worker timed out.');
  await expectReplayGap(page, true);
  await expectPaintedHistory(page, future, '1m', normal.bars);
  await expectProvenance(page, normal);
  await expectNoConnectionFailure(page);
  await page.mouse.move(1, 1);
  await expect(page.getByLabel('Selected bar')).toContainText(/C 3500\.8Volume 110.*Partial bar/);

  await waitForReplayRequest(page, async () => {
    await control(page, 'Retry replay').click();
    await expect(replayGap(page)).toHaveCount(0);
    await expect(replayGapDetails(page)).toHaveCount(0);
    await expect(plot(page).getByRole('alert')).toHaveCount(0);
  });
  await expect(plot(page).getByText('Synthetic replay complete', { exact: true })).toBeVisible(replayStage);
  await page.mouse.move(1, 1);
  await expect(page.getByLabel('Selected bar')).toContainText(/C 3501Volume 130.*Closed bar/);
  await expectProvenance(page, normal);

  await page.route(`**${streamPath}`, route => {
    const { payload } = route.request().postDataJSON();
    const { series, snapshot } = payload;
    const frames = knownGapFrames(payload);
    const update = {
      ...frames[1].event, event: 'market.bar_update', message_id: '4d38b382-8db5-4d46-8ab8-849450f7d6fe',
      payload: {
        series, dataset_revision: snapshot.dataset_revision, observed_at_ms: Date.parse(closed),
        bar: {
          time_ms: Date.parse('2024-01-04T01:09:00.000Z'), trading_day: '2024-01-04',
          open: 3501.6, high: 3502, low: 3500.8, close: 3501, volume: 130, is_closed: true,
        },
      },
    };
    const terminal = { ok: true, result: { status: 'gapped', reason: 'source_gap' } };
    return route.fulfill({ contentType: 'application/x-ndjson', body: ndjson([...frames, { event: update }, terminal]) });
  }, { times: 1 });
  await waitForReplayRequest(page, () => control(page, 'Play sample updates').click());
  await expect(plot(page).getByRole('alert')).toHaveText('Replay failed. The data response failed validation.');
  await expectReplayGap(page, true);
  await page.mouse.move(1, 1);
  await expect(page.getByLabel('Selected bar')).toContainText(/C 3500\.8Volume 110.*Partial bar/);
  await expectProvenance(page, normal);

  await selectStock(page);
  await expectReady(page, stock, '1m', normal.bars);
  await expect(replayGap(page)).toHaveCount(0);
  await expect(replayGapDetails(page)).toHaveCount(0);
});
