import { expect, test } from '@playwright/test';
import { painted } from '../ui/chart-pixels.mjs';
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

async function openRepairSample(page, selected) {
  await page.addInitScript(observeReplay, stream);
  await page.goto('/#demo');
  await openPanel(page, 'Data sources');
  await expect(revision(page)).toHaveValue('normal');
  await revision(page).selectOption(selected);
  await expect(chart(page)).toHaveAttribute('data-bars', '39');
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
  await control(page, 'Play sample updates').press('Enter');
}

async function captureFrames(page) {
  const bodies = await page.evaluate(() => Promise.all(window.closedBarReplays));
  expect(bodies).toHaveLength(1);
  return bodies[0].trimEnd().split('\n').map(line => JSON.parse(line));
}

async function expectGap(page) {
  await expect(gap(page)).toHaveText('Affected range [2024-01-04 01:08:00, 2024-01-04 01:09:00) UTC · 1 lost update');
  await expect(chart(page)).toHaveAttribute('data-bars', '39');
  await expect(legend(page)).toContainText(/C 3501\.2Volume 120.*Partial bar/);
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
  await expect(page.getByText('Closed-bar gap repaired in chart', { exact: true })).toBeVisible();
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
  await expect(page.getByText('Replay gap. An update is missing.', { exact: true })).toBeVisible();
  await expectGap(page);
  const frames = await captureFrames(page);
  expect(frames.map(frame => Object.keys(frame)[0])).toEqual(['subscription', 'event', 'event', 'ok']);
  expect(frames.at(-1)).toEqual({ ok: true, result: { status: 'gapped', reason: 'sequence_gap' } });
  await expect(fact(page, 'Coverage')).toHaveText('Gapped');
  await page.screenshot({ path: testInfo.outputPath('unresolved-closed-bar-gap.png'), animations: 'disabled' });
  await readFrozenRows(page);
});

async function holdRepair(page, terminal) {
  await page.route(`**${stream}`, async route => {
    const response = await route.fetch();
    const lines = (await response.text()).trimEnd().split('\n');
    if (terminal) lines[lines.length - 1] = JSON.stringify(terminal);
    await route.fulfill({ contentType: 'application/x-ndjson', headers: { 'X-Hold-Closed-Bar-Repair': '1' }, body: lines.join('\n') + '\n' });
  });
}

test('a held bridge reply keeps the gap until the valid repair and complete are both received', async ({ page }) => {
  await holdRepair(page);
  await openRepairSample(page, 'repairable');
  await play(page);
  await expectGap(page);
  await page.evaluate(() => window.sendClosedBarRepair());
  await expectGap(page);
  await page.evaluate(() => window.finishClosedBarRepair());
  await expect(page.getByText('Closed-bar gap repaired in chart', { exact: true })).toBeVisible();
  await expect(chart(page)).toHaveAttribute('data-bars', '40');
});

test('a worker-exited terminal leaves an already visible gap and discards a staged repair', async ({ page }) => {
  await holdRepair(page, { ok: false, error: { code: 'WORKER_EXITED', retryable: true } });
  await openRepairSample(page, 'repairable');
  await play(page);
  await expectGap(page);
  await page.evaluate(() => window.sendClosedBarRepair());
  await expectGap(page);
  await page.evaluate(() => window.finishClosedBarRepair());
  await expect(page.getByRole('alert')).toHaveText('Replay failed. The market worker is disconnected.');
  await expect(fact(page, 'Worker connection')).toHaveText('Disconnected');
  await expectGap(page);
  await readFrozenRows(page);
});

for (const [name, alter] of [
  ['wrong repair cursor', frames => { frames[3].repair.position.sequence--; }],
  ['wrong repair bounds', frames => { frames[3].repair.snapshot.from_ms--; }],
  ['an update in place of a repair', frames => { frames[3] = frames[1]; }],
  ['complete without a repair', frames => { frames.splice(3, 1); }],
  ['a repair with a gapped terminal', frames => { frames[4].result = { status: 'gapped', reason: 'sequence_gap' }; }],
]) {
  test(`a bridge reply altered to carry ${name} leaves the gap visible`, async ({ page }) => {
    await page.route(`**${stream}`, async route => {
      const response = await route.fetch();
      const frames = (await response.text()).trimEnd().split('\n').map(line => JSON.parse(line));
      alter(frames);
      await route.fulfill({ status: 200, contentType: 'application/x-ndjson', body: frames.map(frame => JSON.stringify(frame) + '\n').join('') });
    });
    await openRepairSample(page, 'repairable');
    await play(page);
    await expect(page.getByRole('alert')).toHaveText('Replay failed. The data response failed validation.');
    await expectGap(page);
    await readFrozenRows(page);
  });
}
