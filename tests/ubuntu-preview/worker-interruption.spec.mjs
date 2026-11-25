import { execFile, spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { once } from 'node:events';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import { createInterface } from 'node:readline';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { promisify } from 'node:util';
import { expect, test } from '@playwright/test';
import { replayStage, waitForReplayRequest } from '../ui/replay-stages.mjs';
import { openDefaultSample } from '../ui/sample-startup.mjs';
import { control, openPanel } from '../ui/workspace-controls.mjs';

const root = fileURLToPath(new URL('../../', import.meta.url));
const ui = join(root, 'apps', 'desktop', 'ui');
const python = join(root, '.venv', ...(process.platform === 'win32' ? ['Scripts', 'python.exe'] : ['bin', 'python']));
const port = Number(process.env.SCOPEHUB_INTERRUPTION_PORT ?? 1428);
const entry = createRequire(join(ui, 'package.json')).resolve('vite');
const frozenBar = { time_ms: 1704330540000, trading_day: '2024-01-04', open: 3501.6, high: 3502, low: 3500.8,
  close: 3500.8, volume: 110, is_closed: false };
const confirmedBar = { ...frozenBar, close: 3501.2, volume: 120 };
const finalBar = { ...frozenBar, close: 3501, volume: 130, is_closed: true };
const barLegend = bar => `O ${bar.open}H ${bar.high}L ${bar.low}C ${bar.close}Volume ${bar.volume}Trading day ${bar.trading_day}${bar.is_closed ? 'Closed' : 'Partial'} bar`;
let server;
let witness;

test.beforeAll(async ({}, info) => {
  info.setTimeout(300_000);
  await promisify(execFile)('cargo', ['build', '--locked', '-p', 'scopehub-worker-host', '--example', 'history_bridge', '--target-dir', 'target'], { cwd: root });
});
test.beforeEach(async () => {
  witness = randomUUID();
  const { createServer } = await import(pathToFileURL(entry).href);
  // Vite drops null inline overrides when it merges the project config.
  const previous = process.env.SCOPEHUB_TEST_PREVIEW;
  const previousWitness = process.env.SCOPEHUB_TEST_MARKET_INTERRUPTION;
  try {
    process.env.SCOPEHUB_TEST_PREVIEW = '1';
    process.env.SCOPEHUB_TEST_MARKET_INTERRUPTION = witness;
    server = await createServer({ root: ui, mode: 'ubuntu-preview', server: { port } });
  } finally {
    if (previous === undefined) delete process.env.SCOPEHUB_TEST_PREVIEW;
    else process.env.SCOPEHUB_TEST_PREVIEW = previous;
    if (previousWitness === undefined) delete process.env.SCOPEHUB_TEST_MARKET_INTERRUPTION;
    else process.env.SCOPEHUB_TEST_MARKET_INTERRUPTION = previousWitness;
  }
  expect(server.config.server).toMatchObject({ watch: null, hmr: false, ws: false });
  await server.listen();
});
test.afterEach(async () => {
  await server?.close();
  server = undefined;
});

function observeRecovery(groupRecovery) {
  const fetch = window.fetch;
  window.interruptionBodies = [];
  window.interruptionStates = [];
  window.recoveryReads = [];
  window.recoveryPaints = [];

  function groupRecoveryRead(response) {
    let partial = '';
    let reconnecting = '';
    const encoder = new TextEncoder();
    const body = response.body.pipeThrough(new TextDecoderStream('utf-8', { fatal: true })).pipeThrough(new TransformStream({
      transform(chunk, controller) {
        const lines = (partial + chunk).split('\n');
        partial = lines.pop();
        for (const line of lines) {
          if (JSON.parse(line).recovery?.status === 'reconnecting') {
            reconnecting = line + '\n';
            continue;
          }
          // The confirmed partial has already gone through the ordinary reader.
          // Only the next read combines reconnecting with the following recovery frame.
          const delivery = reconnecting + line + '\n';
          if (reconnecting) window.recoveryReads.push(delivery);
          reconnecting = '';
          controller.enqueue(encoder.encode(delivery));
        }
      },
      flush(controller) {
        if (reconnecting || partial) controller.enqueue(encoder.encode(reconnecting + partial));
      },
    }));
    return new Response(body, { status: response.status, headers: response.headers });
  }
  window.fetch = async (...args) => {
    const response = await fetch(...args);
    if (new URL(response.url).pathname === '/__scopehub/market-stream' && response.status === 200) {
      window.interruptionBodies.push(response.clone().text());
      if (groupRecovery) return groupRecoveryRead(response);
    }
    return response;
  };
  document.addEventListener('DOMContentLoaded', () => {
    let interrupted = false;
    function readState() {
      const state = document.querySelector('.replay-status')?.textContent;
      const legend = document.querySelector('[aria-label="Selected bar"]')?.textContent;
      const term = [...document.querySelectorAll('dt')].find(node => node.textContent === 'Connection');
      return { state, legend, connection: term?.nextElementSibling?.textContent };
    }
    function record(observations, observation) {
      if (JSON.stringify(observation) !== JSON.stringify(observations.at(-1))) observations.push(observation);
    }
    if (groupRecovery) {
      function sample() {
        const observation = readState();
        if (observation.state === 'Market worker reconnecting…' || observation.state === 'Market worker resumed') {
          record(window.recoveryPaints, observation);
        }
        requestAnimationFrame(sample);
      }
      requestAnimationFrame(sample);
    }
    new MutationObserver(() => {
      const observation = readState();
      if (!observation.state) return;
      record(window.interruptionStates, observation);
      if (!interrupted && /C 3501\.2Volume 120/.test(observation.legend)) {
        interrupted = true;
        void window.interruptOwnedWorker();
      }
    }).observe(document.body, { childList: true, characterData: true, subtree: true });
  });
}

async function interruptRealWorker(page, testInfo, groupRecovery) {
  const driver = spawn(python, ['-I', join(root, 'tests/ubuntu-preview/interrupt_owned_worker.py'), String(process.pid), witness], {
    cwd: root, stdio: ['pipe', 'pipe', 'inherit'],
  });
  const finished = once(driver, 'exit');
  const reader = createInterface({ input: driver.stdout });
  try {
    const [ready] = await Promise.race([once(reader, 'line'), finished]);
    expect(ready).toBe('ready');
    await page.exposeFunction('interruptOwnedWorker', () => { driver.stdin.end('interrupt\n'); });
    await page.addInitScript(observeRecovery, groupRecovery);
    const attempts = [];
    page.on('request', request => {
      if (new URL(request.url()).pathname === '/__scopehub/market-stream') attempts.push(request.postDataJSON());
    });
    await openDefaultSample(page);
    await openPanel(page, 'Data sources');
    await page.mouse.move(1, 1);
    const legend = page.getByLabel('Selected bar');
    await expect(legend).toHaveText(barLegend(frozenBar));
    await waitForReplayRequest(page, () => control(page, 'Play sample updates').press('Enter'));
    await expect(page.locator('.replay-status')).toHaveText('Synthetic replay complete', replayStage);
    await expect(legend).toHaveText(barLegend(finalBar));
    const observed = await page.evaluate(async () => ({
      states: window.interruptionStates, bodies: await Promise.all(window.interruptionBodies),
      recoveryReads: window.recoveryReads, recoveryPaints: window.recoveryPaints,
    }));
    await testInfo.attach('Real worker interruption stream and chart states', {
      body: JSON.stringify({ ...observed, attempts }), contentType: 'application/json',
    });
    const { states, bodies } = observed;
    expect(attempts).toHaveLength(1);
    expect(bodies).toHaveLength(1);
    const lines = bodies[0].trimEnd().split('\n');
    const frames = lines.map(line => JSON.parse(line));
    const after = attempts[0].payload.after;
    const confirmed = { ...after, sequence: 1 };
    expect(frames).toHaveLength(6);
    expect(frames[0]).toEqual({ subscription: { status: 'accepted', from_position: after } });
    expect(frames[2]).toEqual({ recovery: { status: 'reconnecting', position: confirmed } });
    expect(frames[3]).toEqual({ recovery: { status: 'resumed', position: confirmed } });
    expect(frames[5]).toEqual({ ok: true, result: { status: 'complete' } });
    for (const [index, sequence, observed_at_ms, bar] of [[1, 1, 1704330585000, confirmedBar], [4, 2, 1704330600000, finalBar]]) {
      const event = frames[index].event;
      expect(event).toMatchObject({
        event: 'market.bar_update', stream_id: after.stream_id, epoch: after.epoch, sequence,
        payload: { observed_at_ms },
      });
      expect(event.payload.bar).toEqual(bar);
    }
    const recovery = ['Market worker reconnecting…', 'Market worker resumed'].map(state => ({
      state, legend: barLegend(confirmedBar), connection: 'Offline',
    }));
    const actual = states.filter(observation => recovery.some(expected => observation.state === expected.state)
      && observation.legend === barLegend(confirmedBar));
    expect(actual).toEqual(recovery);
    if (groupRecovery) {
      expect(observed.recoveryReads).toEqual([lines.slice(2, 4).join('\n') + '\n']);
      expect(observed.recoveryPaints.filter(observation => observation.legend === barLegend(confirmedBar))).toEqual(recovery);
    }
    const legends = states.map(observation => observation.legend);
    expect(legends.filter((value, index) => value !== legends[index - 1])).toEqual([frozenBar, confirmedBar, finalBar].map(barLegend));
    await openPanel(page, 'Data window');
    const row = page.getByRole('table', { name: 'Historical bars' }).locator('tbody tr').last();
    await expect(row.getByRole('cell')).toHaveText(['2024-01-04T01:09:00.000Z', '2024-01-04', '3501.6', '3502', '3500.8', '3500.8', '110', 'Partial']);
    const catalog = await page.evaluate(async () => {
      const response = await fetch('/__scopehub/operation', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ operation: 'instrument.search', payload: { query: '', limit: 10 } }),
      });
      return { status: response.status, body: await response.json() };
    });
    expect(catalog).toMatchObject({ status: 200, body: { ok: true } });
  } finally {
    reader.close();
    driver.stdin.end();
    await page.close();
    const [exitCode] = await finished;
    expect(exitCode).toBe(0);
  }
}

test('resumes one real chart stream after its confirmed worker exits', async ({ page }, testInfo) => {
  await interruptRealWorker(page, testInfo, false);
});

test('shows both real worker recovery states delivered in one browser read', async ({ page }, testInfo) => {
  await interruptRealWorker(page, testInfo, true);
});
