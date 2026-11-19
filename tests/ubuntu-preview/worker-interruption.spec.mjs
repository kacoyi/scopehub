import { execFile, spawn } from 'node:child_process';
import { once } from 'node:events';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import { createInterface } from 'node:readline';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { promisify } from 'node:util';
import { expect, test } from '@playwright/test';
import { replayStage, waitForReplayRequest } from '../ui/replay-stages.mjs';
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

test.beforeAll(async ({}, info) => {
  info.setTimeout(300_000);
  await promisify(execFile)('cargo', ['build', '--locked', '-p', 'scopehub-worker-host', '--example', 'history_bridge', '--target-dir', 'target'], { cwd: root });
  const { createServer } = await import(pathToFileURL(entry).href);
  server = await createServer({ root: ui, mode: 'ubuntu-preview', server: { port } });
  await server.listen();
});
test.afterAll(async () => { await server?.close(); });

function observeRecovery() {
  const fetch = window.fetch;
  window.interruptionBodies = [];
  window.interruptionStates = [];
  window.fetch = async (...args) => {
    const response = await fetch(...args);
    if (new URL(response.url).pathname === '/__scopehub/market-stream' && response.status === 200) {
      window.interruptionBodies.push(response.clone().text());
    }
    return response;
  };
  document.addEventListener('DOMContentLoaded', () => {
    let interrupted = false;
    new MutationObserver(() => {
      const state = document.querySelector('.replay-status')?.textContent;
      if (!state) return;
      const legend = document.querySelector('[aria-label="Selected bar"]')?.textContent;
      const term = [...document.querySelectorAll('dt')].find(node => node.textContent === 'Connection');
      const connection = term?.nextElementSibling?.textContent;
      const last = window.interruptionStates.at(-1);
      if (last?.state !== state || last?.legend !== legend || last?.connection !== connection) {
        window.interruptionStates.push({ state, legend, connection });
      }
      if (!interrupted && /C 3501\.2Volume 120/.test(legend)) {
        interrupted = true;
        void window.interruptOwnedWorker();
      }
    }).observe(document.body, { childList: true, characterData: true, subtree: true });
  });
}

test('resumes one real chart stream after its confirmed worker exits', async ({ page }) => {
  const driver = spawn(python, ['-I', join(root, 'tests/ubuntu-preview/interrupt_owned_worker.py'), String(process.pid)], {
    cwd: root, stdio: ['pipe', 'pipe', 'inherit'],
  });
  const finished = once(driver, 'exit');
  const reader = createInterface({ input: driver.stdout });
  try {
    const [ready] = await Promise.race([once(reader, 'line'), finished]);
    expect(ready).toBe('ready');
    await page.exposeFunction('interruptOwnedWorker', () => { driver.stdin.end('interrupt\n'); });
    await page.addInitScript(observeRecovery);
    const attempts = [];
    page.on('request', request => {
      if (new URL(request.url()).pathname === '/__scopehub/market-stream') attempts.push(request.postDataJSON());
    });
    await page.goto('/#demo');
    await openPanel(page, 'Data sources');
    await page.mouse.move(1, 1);
    const legend = page.getByLabel('Selected bar');
    await expect(legend).toHaveText(barLegend(frozenBar));
    await waitForReplayRequest(page, () => control(page, 'Play sample updates').press('Enter'));
    await expect(page.locator('.replay-status')).toHaveText('Synthetic replay complete', replayStage);
    await expect(legend).toHaveText(barLegend(finalBar));
    const states = await page.evaluate(() => window.interruptionStates);
    for (const state of ['Market worker reconnecting…', 'Market worker resumed']) {
      expect(states).toContainEqual({ state, legend: barLegend(confirmedBar), connection: 'Offline' });
    }
    const legends = states.map(observation => observation.legend);
    expect(legends.filter((value, index) => value !== legends[index - 1])).toEqual([frozenBar, confirmedBar, finalBar].map(barLegend));
    expect(attempts).toHaveLength(1);
    const bodies = await page.evaluate(() => Promise.all(window.interruptionBodies));
    expect(bodies).toHaveLength(1);
    const frames = bodies[0].trimEnd().split('\n').map(line => JSON.parse(line));
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
    await openPanel(page, 'Data window');
    const row = page.getByRole('table', { name: 'Historical bars' }).locator('tbody tr').last();
    await expect(row.getByRole('cell')).toHaveText(['2024-01-04T01:09:00.000Z', '2024-01-04', '3501.6', '3502', '3500.8', '3500.8', '110', 'Partial']);
  } finally {
    reader.close();
    driver.stdin.end();
    await page.close();
    const [exitCode] = await finished;
    expect(exitCode).toBe(0);
  }
});
