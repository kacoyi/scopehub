import { expect, test } from '@playwright/test';
import { painted } from '../ui/chart-pixels.mjs';
import { replayStage, waitForReplayRequest } from '../ui/replay-stages.mjs';
import { control, openPanel, watchlistSymbol } from '../ui/workspace-controls.mjs';

const streamPath = '/__scopehub/market-stream';
const future = 'SIM.CN.FUTURE.001';
const stock = 'SIM.CN.STOCK.001';
const chart = page => page.locator('.history');
const legend = page => page.getByLabel('Selected bar');
const status = page => page.locator('.replay-status');
const sources = page => page.getByRole('complementary', { name: 'Data sources' });
const fact = (page, label) => sources(page).locator('dt').filter({ hasText: new RegExp(`^${RegExp.escape(label)}$`) })
  .locator('xpath=following-sibling::dd[1]');

// Only the futures replay body is controlled. History, preparation and any
// replacement stock replay still run through the preview and its workers.
function injectFuturesReplay({ path, instrument }) {
  const fetch = window.fetch;
  const encoder = new TextEncoder();
  window.injectedReplays = [];
  window.fetch = (url, options) => {
    if (url !== path) return fetch(url, options);
    const { operation, payload } = JSON.parse(options.body);
    if (payload.series.instrument_id !== instrument) return fetch(url, options);
    const { signal } = options;
    const call = { operation, payload, signal, closed: false };
    let writer;
    function abort() {
      call.closed = true;
      writer.error(signal.reason);
    }
    const body = new ReadableStream({
      start(controller) {
        writer = controller;
        signal.addEventListener('abort', abort, { once: true });
      },
      cancel() {
        call.closed = true;
        signal.removeEventListener('abort', abort);
      },
    });
    call.write = (frames, end) => {
      if (call.closed) return false;
      for (const frame of frames) writer.enqueue(encoder.encode(JSON.stringify(frame) + '\n'));
      if (end) {
        call.closed = true;
        signal.removeEventListener('abort', abort);
        writer.close();
      }
      return true;
    };
    window.injectedReplays.push(call);
    return Promise.resolve(new Response(body, { headers: { 'Content-Type': 'application/x-ndjson' } }));
  };
}

async function inject(page, frames, end = false) {
  expect(await page.evaluate(({ frames, end }) => window.injectedReplays[0].write(frames, end), { frames, end })).toBe(true);
}

function replayFrames({ after, series, snapshot }) {
  const position = { ...after, sequence: after.sequence + 1 };
  const recovery = (status, cursor = position) => ({ recovery: { status, position: cursor } });
  const update = (index, observed_at_ms, close, volume, is_closed, message_id) => ({ event: {
    protocol_version: '1.1', type: 'event', event: 'market.bar_update', message_id, timestamp_ms: observed_at_ms,
    stream_id: after.stream_id, epoch: after.epoch, sequence: after.sequence + index,
    payload: { series, dataset_revision: snapshot.dataset_revision, observed_at_ms, bar: {
      time_ms: 1704330540000, trading_day: '2024-01-04', open: 3501.6, high: 3502, low: 3500.8, close, volume, is_closed,
    } },
  } });
  return {
    position,
    recovery,
    accepted: { subscription: { status: 'accepted', from_position: after } },
    interim: update(1, 1704330585000, 3501.2, 120, false, 'f0eb3cc1-a278-4198-8f3e-4640dd32e929'),
    final: update(2, 1704330600000, 3501, 130, true, '44dcebdf-6339-4e17-aeb9-dd9f2557e606'),
    reconnecting: recovery('reconnecting'),
    resumed: recovery('resumed'),
    complete: { ok: true, result: { status: 'complete' } },
  };
}

async function start(page) {
  await page.addInitScript(injectFuturesReplay, { path: streamPath, instrument: future });
  await page.goto('/#demo');
  await expect(chart(page)).toHaveAttribute('data-instrument', future);
  await control(page, 'Play sample updates').click();
  await page.mouse.move(1, 1);
  await page.waitForFunction(() => window.injectedReplays.length === 1);
  const { operation, payload } = await page.evaluate(() => {
    const { operation, payload } = window.injectedReplays[0];
    return { operation, payload };
  });
  expect(operation).toBe('market.subscribe');
  expect(payload.after.sequence).toBe(0);
  return replayFrames(payload);
}

function expectBar(page, close, volume, phase) {
  return expect(legend(page).locator('span')).toHaveText([
    'O 3501.6', 'H 3502', 'L 3500.8', `C ${close}`, `Volume ${volume}`, 'Trading day 2024-01-04', `${phase} bar`,
  ]);
}

async function expectFrozenHistory(page) {
  await openPanel(page, 'Data window');
  const rows = page.getByRole('table', { name: 'Historical bars' }).locator('tbody tr');
  await expect(rows).toHaveCount(40);
  await expect(rows.last().getByRole('cell')).toHaveText([
    '2024-01-04T01:09:00.000Z', '2024-01-04', '3501.6', '3502', '3500.8', '3500.8', '110', 'Partial',
  ]);
}

async function expectProvenance(page) {
  for (const [label, value] of Object.entries({
    Source: 'scopehub.synthetic', 'Source mode': 'Simulated', 'Source kind': 'Synthetic',
    Revision: 'scopehub-synthetic-v1:20241013:normal', Connection: 'Offline', Session: 'Open',
    Freshness: 'Current', Coverage: 'Observed', 'Input digest': '8c5cccacb89d3cfa0a2f06418caa75cd5be6436e3cdd3ed4fadddd766cdff900',
  })) await expect(fact(page, label)).toHaveText(value);
  for (const label of ['As of (UTC)', 'Last observed (UTC)']) {
    await expect(fact(page, label).locator('time')).toHaveAttribute('datetime', '2024-01-04T01:09:30.000Z');
  }
}

test('injected recovery holds the confirmed futures bar and closes it once while history stays frozen', async ({ page }, testInfo) => {
  const frames = await start(page);
  await openPanel(page, 'Data sources');
  await expectBar(page, 3500.8, 110, 'Partial');
  await expectProvenance(page);
  const canvas = await chart(page).locator('.chart canvas').first().elementHandle();
  await expect.poll(() => painted(page, 'candlestick')).toBeGreaterThan(20);
  await legend(page).evaluate(element => {
    const observations = [];
    function record() {
      const fields = Array.from(element.children).slice(3).map(child => child.textContent);
      const readout = fields.join(' · ');
      if (readout !== observations.at(-1)) observations.push(readout);
    }
    const observer = new MutationObserver(record);
    observer.observe(element, { subtree: true, characterData: true, childList: true });
    window.readRecoveryBars = () => { observer.disconnect(); return observations; };
    record();
  });

  await inject(page, [frames.accepted, frames.interim]);
  await expectBar(page, 3501.2, 120, 'Partial');
  await inject(page, [frames.reconnecting]);
  await expect(status(page)).toHaveText('Market worker reconnecting…');
  await expect(control(page, 'Stop replay')).toBeVisible();
  await expect(fact(page, 'Worker connection')).toHaveText('Reconnecting');
  await expectProvenance(page);
  await expectBar(page, 3501.2, 120, 'Partial');
  await page.screenshot({ path: testInfo.outputPath('injected-worker-reconnecting.png'), animations: 'disabled' });
  await expectFrozenHistory(page);

  await openPanel(page, 'Data sources');
  await inject(page, [frames.resumed]);
  await expect(status(page)).toHaveText('Market worker resumed');
  await expect(fact(page, 'Worker connection')).toHaveText('Resumed');
  await expectBar(page, 3501.2, 120, 'Partial');
  await inject(page, [frames.final, frames.complete], true);
  await expect(status(page)).toHaveText('Synthetic replay complete');
  await expectBar(page, 3501, 130, 'Closed');
  expect(await page.evaluate(() => window.readRecoveryBars())).toEqual([
    'C 3500.8 · Volume 110 · Trading day 2024-01-04 · Partial bar',
    'C 3501.2 · Volume 120 · Trading day 2024-01-04 · Partial bar',
    'C 3501 · Volume 130 · Trading day 2024-01-04 · Closed bar',
  ]);
  expect(await canvas.evaluate(element => element.isConnected)).toBe(true);
  expect(await page.evaluate(() => window.injectedReplays.length)).toBe(1);
  await expectProvenance(page);
  await expectFrozenHistory(page);
  await page.screenshot({ path: testInfo.outputPath('injected-worker-recovered.png'), animations: 'disabled' });
});

for (const [name, terminal, label] of [
  ['boundary mismatch', { ok: true, result: { status: 'gapped', reason: 'sequence_gap' } }, 'Replay gap. An update is missing.'],
  ['restart exhaustion', { ok: false, error: { code: 'WORKER_EXITED', retryable: true } }, 'Replay failed. The market worker is disconnected.'],
]) {
  test(`injected ${name} retains the last confirmed bar`, async ({ page }) => {
    const frames = await start(page);
    await inject(page, [frames.accepted, frames.interim, frames.reconnecting]);
    await expect(status(page)).toHaveText('Market worker reconnecting…');
    await inject(page, [terminal], true);
    await expect(status(page)).toHaveText(label);
    await expectBar(page, 3501.2, 120, 'Partial');
    await openPanel(page, 'Data sources');
    await expectProvenance(page);
    if (terminal.ok) {
      await expect(page.getByRole('group', { name: 'Replay gap details' })).toHaveText('Affected range unknown · Lost updates unknown');
    } else {
      await expect(fact(page, 'Worker connection')).toHaveText('Disconnected');
      await expect(control(page, 'Retry replay')).toBeVisible();
    }
    expect(await page.evaluate(() => window.injectedReplays.length)).toBe(1);
  });
}

const invalidProgress = [
  ['recovery before acceptance', f => [f.reconnecting], false],
  ['resumed before reconnecting', f => [f.resumed]],
  ['unknown status', f => [f.recovery('connected')]],
  ['non-numeric cursor', f => [f.recovery('reconnecting', { ...f.position, sequence: '1' })]],
  ['wrong stream', f => [f.recovery('reconnecting', { ...f.position, stream_id: 'another-stream' })]],
  ['wrong epoch', f => [f.reconnecting, f.recovery('resumed', { ...f.position, epoch: 'another-epoch' })]],
  ['wrong sequence', f => [f.recovery('reconnecting', { ...f.position, sequence: 0 })]],
  ['two progress fields', f => [{ ...f.reconnecting, ...f.final }]],
  ['repeated reconnecting', f => [f.reconnecting, f.reconnecting]],
  ['repeated resumed', f => [f.reconnecting, f.resumed, f.resumed]],
  ['second logical acceptance', f => [f.reconnecting, f.accepted]],
  ['bar update before resumed', f => [f.reconnecting, f.final]],
  ['complete before resumed', f => [f.reconnecting, f.complete]],
];

for (const [name, progress, accept = true] of invalidProgress) {
  test(`rejects injected ${name}`, async ({ page }) => {
    const frames = await start(page);
    await inject(page, [...(accept ? [frames.accepted, frames.interim] : []), ...progress(frames)], true);
    await expect(status(page)).toHaveText('Replay failed. The data response failed validation.');
    if (accept) await expectBar(page, 3501.2, 120, 'Partial');
    else await expectBar(page, 3500.8, 110, 'Partial');
    await expect(control(page, 'Stop replay')).toHaveCount(0);
  });
}

for (const action of ['stop', 'reselect']) {
  test(`${action} cancels an injected reconnect with no late update`, async ({ page }) => {
    const frames = await start(page);
    await inject(page, [frames.accepted, frames.interim, frames.reconnecting]);
    await expect(status(page)).toHaveText('Market worker reconnecting…');
    if (action === 'stop') await control(page, 'Stop replay').click();
    else await watchlistSymbol(page, stock).click();
    await expect.poll(() => page.evaluate(() => window.injectedReplays[0].signal.aborted)).toBe(true);
    expect(await page.evaluate(frames => window.injectedReplays[0].write(frames, true), [frames.resumed, frames.final, frames.complete])).toBe(false);
    await expect(status(page)).toHaveCount(0);
    if (action === 'stop') {
      await expectBar(page, 3500.8, 110, 'Partial');
      await expect(control(page, 'Play sample updates')).toBeVisible();
    } else {
      await expect(chart(page)).toHaveAttribute('data-instrument', stock);
      const request = await waitForReplayRequest(page, () => control(page, 'Play sample updates').click());
      expect(request.postDataJSON().payload.series.instrument_id).toBe(stock);
      await expect(status(page)).toHaveText('Synthetic replay complete', replayStage);
      await expect(legend(page).locator('span').nth(3)).toHaveText('C 12.16');
    }
  });
}
