import { readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';
import { openDefaultSample } from '../ui/sample-startup.mjs';

const program = fileURLToPath(new URL('../fixtures/workbench/chart-price-anchors.js', import.meta.url));
const first = '2024-01-02T13:00:00.000Z';
const second = '2024-01-02T13:01:00.000Z';

function anchor(circle) {
  return circle.evaluate(element => {
    const x = Number(element.getAttribute('cx'));
    const y = Number(element.getAttribute('cy'));
    const surface = element.ownerSVGElement;
    const bounds = surface.getBoundingClientRect();
    return { x, y, width: bounds.width, height: bounds.height, left: bounds.left, top: bounds.top };
  });
}

async function candlePaint(chart, position, color) {
  return chart.locator('canvas').evaluateAll((canvases, { position, color }) => {
    for (const canvas of canvases) {
      const bounds = canvas.getBoundingClientRect();
      if (Math.abs(bounds.width - position.width) > 1 || Math.abs(bounds.height - position.height) > 1) continue;
      const x = Math.round(position.x * canvas.width / bounds.width);
      const y = Math.round(position.y * canvas.height / bounds.height);
      const left = Math.max(0, x - 2);
      const top = Math.max(0, y - 2);
      const pixels = canvas.getContext('2d').getImageData(left, top, 5, 5).data;
      for (let index = 0; index < pixels.length; index += 4) {
        if (color.every((channel, i) => Math.abs(channel - pixels[index + i]) < 6)) return true;
      }
    }
    return false;
  }, { position, color });
}

test('local author geometry uses the financial chart coordinates and crosshair', async ({ page }, info) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const workspacePosts = [];
  page.on('request', request => {
    if (request.method() === 'POST' && new URL(request.url()).pathname.endsWith('/workspace')) workspacePosts.push(request.url());
  });
  const frozen = await openDefaultSample(page, { trigger: () => page.goto('/?chart-drawing=1#demo') });
  const chart = page.getByRole('img', { name: 'Candlestick and volume chart for SIM.CN.FUTURE.001', exact: true });
  const picker = page.getByLabel('Open chart drawing program');
  const overlay = page.getByRole('img', { name: 'Projected chart drawing' });
  const readout = page.getByLabel('Drawing crosshair observation');
  const circles = overlay.locator('circle');
  const accept = page.getByRole('button', { name: 'Accept chart drawing', exact: true });
  const restore = page.getByRole('button', { name: 'Restore accepted chart drawing', exact: true });
  await picker.setInputFiles(program);
  await expect(circles).toHaveCount(2);
  await accept.click();

  async function hover(index, time, close) {
    const position = await anchor(circles.nth(index));
    await page.mouse.move(position.left + position.x, position.top + position.y);
    await expect(readout).toContainText(time);
    await expect(readout).toContainText(close + ' CNY');
    await expect(page.getByLabel('Selected bar')).toContainText('C ' + close);
    await expect(circles.nth(index)).toHaveAttribute('r', '8');
    return position;
  }
  const before = await hover(1, second, '3499.8');
  await expect.poll(() => candlePaint(chart, before, [67, 212, 172])).toBe(true);
  await page.screenshot({ path: info.outputPath('aligned.png') });

  const resizeSamples = [];
  let after = before;
  for (const viewport of [{ width: 1040, height: 805 }, { width: 1300, height: 940 }, { width: 1040, height: 805 }]) {
    const previous = after;
    const started = performance.now();
    await page.setViewportSize(viewport);
    await expect.poll(async () => Math.abs((await anchor(circles.nth(1))).width - previous.width)).toBeGreaterThan(150);
    const redrawObservedMs = performance.now() - started;
    after = await hover(1, second, '3499.8');
    expect(Math.hypot(after.x - previous.x, after.y - previous.y)).toBeGreaterThan(5);
    await expect.poll(() => candlePaint(chart, after, [67, 212, 172])).toBe(true);
    resizeSamples.push({ viewport, redrawObservedMs, anchor: after });
  }
  await hover(0, first, '3499.2');
  await page.mouse.move(after.left + after.width + 4, after.top + 30);
  await expect(readout).toHaveCount(0);

  const zoom = page.getByRole('button', { name: 'Zoom in', exact: true });
  await zoom.click();
  await expect.poll(async () => (await anchor(circles.first())).x).toBeLessThan(-5);
  await expect(overlay).toHaveCSS('overflow', 'hidden');
  await expect(readout).toHaveCount(0);
  const clipped = await anchor(circles.first());
  await page.screenshot({ path: info.outputPath('clipped.png') });
  await page.getByRole('button', { name: 'Fit chart', exact: true }).click();
  await expect.poll(async () => (await anchor(circles.first())).x).toBeGreaterThanOrEqual(0);

  const original = await readFile(program, 'utf8');
  const open = text => picker.setInputFiles({ name: 'candidate.js', mimeType: 'text/javascript', buffer: Buffer.from(text) });
  await open('// future overlay\nthrow new Error("rejected guest");');
  await expect(page.getByRole('alert')).toContainText('This chart drawing could not be displayed.');
  await expect(circles).toHaveCount(2);
  await expect(circles.first()).toHaveAttribute('r', '5');
  await expect(accept).toHaveCount(0);
  await restore.click();
  await expect(page.getByRole('alert')).toHaveCount(0);

  await open('// future overlay\nwhile (true) {}');
  await expect(circles.first()).toHaveAttribute('r', '5');
  await expect(page.getByRole('alert')).toContainText('This chart drawing could not be displayed.');
  await expect(circles.first()).toHaveAttribute('r', '5');
  await expect(accept).toHaveCount(0);
  await restore.click();
  await expect(page.getByRole('alert')).toHaveCount(0);

  await open(original.replace('? 8 : 5', '? 10 : 7'));
  await expect(circles.first()).toHaveAttribute('r', '7');
  await restore.click();
  await expect(circles.first()).toHaveAttribute('r', '5');
  await open(original.replace('? 8 : 5', '? 10 : 7'));
  await accept.click();
  await expect(circles.first()).toHaveAttribute('r', '7');
  await page.getByRole('button', { name: '5m', exact: true }).click();
  await expect(overlay).toHaveCount(0);
  await expect(picker).toHaveCount(0);
  await expect(readout).toHaveCount(0);
  expect(workspacePosts).toEqual([]);

  const observed = { frozen, before, after, clipped, resizeSamples,
    method: 'Chromium viewport resize invocation to the first observed new SVG pane width with an authored anchor. Includes browser automation and polling; no presented-frame or FPS claim.',
    cases: ['authorized frozen chart', 'two exact crosshair times', 'actual candle paint at the aligned coordinates',
      'price-axis edge cleared', 'zoom clipped offscreen geometry', 'rejected and timed-out guests preserved accepted drawing',
      'valid draft restored and accepted', 'timeframe change removed the bound scene'] };
  await writeFile(info.outputPath('chart-coordinates.json'), JSON.stringify(observed, null, 2) + '\n');
});

async function startChartObservation(page) {
  await page.evaluate(() => {
    const frames = [];
    const start = performance.now();
    let sampling;
    function observe() {
      const svg = document.querySelector('.chart-drawing-overlay');
      const readout = document.querySelector('output[aria-label="Drawing crosshair observation"]');
      frames.push({
        elapsedMs: performance.now() - start,
        radii: Array.from(svg?.querySelectorAll('circle') ?? [], circle => Number(circle.getAttribute('r'))),
        time: readout?.querySelector('time')?.dateTime ?? null,
      });
      sampling = requestAnimationFrame(observe);
    }
    sampling = requestAnimationFrame(observe);
    window.stopChartObservation = () => { cancelAnimationFrame(sampling); return frames; };
  });
}

async function stopChartObservation(page) {
  return page.evaluate(async () => {
    await new Promise(requestAnimationFrame);
    return window.stopChartObservation();
  });
}

test('accepted geometry stays visible on every sampled frame of real crosshair motion', async ({ page }, info) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openDefaultSample(page, { trigger: () => page.goto('/?chart-drawing=1#demo') });
  const overlay = page.getByRole('img', { name: 'Projected chart drawing' });
  const circles = overlay.locator('circle');
  const readout = page.getByLabel('Drawing crosshair observation');
  await page.getByLabel('Open chart drawing program').setInputFiles(program);
  await page.getByRole('button', { name: 'Accept chart drawing', exact: true }).click();
  await expect(circles).toHaveCount(2);
  const anchors = await Promise.all([anchor(circles.first()), anchor(circles.last())]);
  const observations = [];

  for (const index of [0, 1, 0, 1, 0, 1]) {
    await startChartObservation(page);
    const target = anchors[index];
    await page.mouse.move(target.left + target.x, target.top + target.y);
    const time = index ? second : first;
    await expect(readout).toContainText(time);
    await expect(page.getByLabel('Selected bar')).toContainText(index ? 'C 3499.8' : 'C 3499.2');
    await expect(circles.nth(index)).toHaveAttribute('r', '8');
    await expect(circles.nth(1 - index)).toHaveAttribute('r', '5');
    const frames = await stopChartObservation(page);
    const blanks = frames.filter(frame => frame.radii.length === 0);
    observations.push({
      time, frames, blankFrames: blanks.length,
      mismatchedFrames: frames.filter(frame => frame.radii.length && frame.radii.join(',') !== '5,5'
        && frame.radii.join(',') !== (frame.time === first ? '8,5' : frame.time === second ? '5,8' : '')).length,
      sampledBlankMs: blanks.length ? frames[frames.lastIndexOf(blanks.at(-1)) + 1].elapsedMs - blanks[0].elapsedMs : 0,
    });
  }
  await writeFile(info.outputPath('crosshair-continuity.json'), JSON.stringify({
    method: '1440x900 Chromium; six alternating real mouse moves, one DOM sample per rAF through settled highlight. Blank duration is the first empty sample to the first following visible sample.',
    observations,
  }, null, 2) + '\n');
  expect(observations.map(move => move.blankFrames)).toEqual([0, 0, 0, 0, 0, 0]);
  expect(observations.map(move => move.mismatchedFrames)).toEqual([0, 0, 0, 0, 0, 0]);
});

test('superseded guest work cannot change the current selection or restore obsolete geometry', async ({ page }) => {
  await page.addInitScript(() => {
    const workers = new WeakMap();
    const { postMessage, terminate } = Worker.prototype;
    const gate = window.chartGuestGate = { selection: null, held: [] };
    Worker.prototype.postMessage = function (request, ...args) {
      if (request?.bounds?.times && (gate.selection === 'all' || gate.selection === request.controls.selected)) {
        const record = { selected: request.controls.selected, terminated: false };
        gate.held.push(record);
        workers.set(this, record);
        return;
      }
      return postMessage.call(this, request, ...args);
    };
    Worker.prototype.terminate = function () {
      const record = workers.get(this);
      if (record) record.terminated = true;
      return terminate.call(this);
    };
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await openDefaultSample(page, { trigger: () => page.goto('/?chart-drawing=1#demo') });
  const overlay = page.getByRole('img', { name: 'Projected chart drawing' });
  const circles = overlay.locator('circle');
  const readout = page.getByLabel('Drawing crosshair observation');
  await page.getByLabel('Open chart drawing program').setInputFiles(program);
  await page.getByRole('button', { name: 'Accept chart drawing', exact: true }).click();
  const anchors = await Promise.all([anchor(circles.first()), anchor(circles.last())]);
  const move = index => page.mouse.move(anchors[index].left + anchors[index].x, anchors[index].top + anchors[index].y);
  const setGate = selection => page.evaluate(value => { window.chartGuestGate.selection = value; }, selection);
  const held = () => page.evaluate(() => window.chartGuestGate.held);

  await setGate(0);
  await move(0);
  await expect(readout).toContainText(first);
  await expect.poll(held).toEqual([{ selected: 0, terminated: false }]);
  await expect(circles).toHaveCount(2);
  await expect(circles.first()).toHaveAttribute('r', '5');
  await expect(circles.last()).toHaveAttribute('r', '5');

  await move(1);
  await expect.poll(held).toEqual([{ selected: 0, terminated: true }]);
  await expect(readout).toContainText(second);
  await expect(circles.first()).toHaveAttribute('r', '5');
  await expect(circles.last()).toHaveAttribute('r', '8');

  await setGate('all');
  await page.getByRole('button', { name: 'Zoom in', exact: true }).click();
  await expect.poll(async () => (await held()).filter(request => !request.terminated)).toEqual([{ selected: -1, terminated: false }]);
  await expect(overlay).toHaveCount(0);
  await expect(readout).toHaveCount(0);

  await page.getByRole('button', { name: '5m', exact: true }).click();
  await expect.poll(async () => (await held()).every(request => request.terminated)).toBe(true);
  await expect(overlay).toHaveCount(0);
  await expect(readout).toHaveCount(0);

  await setGate(null);
  await page.getByRole('button', { name: '1m', exact: true }).click();
  await expect(page.getByLabel('Open chart drawing program')).toBeVisible();
  await expect(overlay).toHaveCount(0);
});

test('a guest that rejects the current candle leaves the unselected drawing visible', async ({ page }, info) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openDefaultSample(page, { trigger: () => page.goto('/?chart-drawing=1#demo') });
  const original = await readFile(program, 'utf8');
  const overlay = page.getByRole('img', { name: 'Projected chart drawing' });
  const circles = overlay.locator('circle');
  const readout = page.getByLabel('Drawing crosshair observation');
  await page.getByLabel('Open chart drawing program').setInputFiles({
    name: 'reject-second-candle.js',
    mimeType: 'text/javascript',
    buffer: Buffer.from(original + '\nif (selected === 1) throw new Error("second candle rejected");\n'),
  });
  await page.getByRole('button', { name: 'Accept chart drawing', exact: true }).click();
  const anchors = await Promise.all([anchor(circles.first()), anchor(circles.last())]);
  const move = index => page.mouse.move(anchors[index].left + anchors[index].x, anchors[index].top + anchors[index].y);
  await move(0);
  await expect(readout).toContainText(first);
  await expect(circles.first()).toHaveAttribute('r', '8');
  await expect(overlay.locator('line')).toHaveCount(1);

  await startChartObservation(page);
  await move(1);
  await expect(readout).toContainText(second);
  await expect(page.getByRole('alert')).toContainText('This chart drawing could not be displayed.');
  await expect(circles).toHaveCount(2);
  await expect(circles.first()).toHaveAttribute('r', '5');
  await expect(circles.last()).toHaveAttribute('r', '5');
  await expect(overlay.locator('line')).toHaveCount(0);
  expect(await anchor(circles.last())).toEqual(anchors[1]);
  const rejectedFrames = await stopChartObservation(page);
  await writeFile(info.outputPath('rejected-selection-frames.json'), JSON.stringify(rejectedFrames, null, 2) + '\n');
  expect(rejectedFrames.every(frame => frame.radii.length === 2)).toBe(true);
  const rejectedTime = rejectedFrames.filter(frame => frame.time === second);
  expect(rejectedTime.length).toBeGreaterThan(0);
  expect(rejectedTime.every(frame => frame.radii.join(',') === '5,5')).toBe(true);

  await move(0);
  await expect(readout).toContainText(first);
  await expect(circles.first()).toHaveAttribute('r', '8');
  await expect(page.getByRole('alert')).toHaveCount(0);
});

function drawingLayoutFrames(chart, frames) {
  return chart.evaluate(async (plot, count) => {
    const samples = [];
    const layout = plot.closest('.history');
    const controls = layout.querySelector('.chart-drawing-controls');
    for (let index = 0; index < count; index++) {
      await new Promise(requestAnimationFrame);
      const svg = plot.querySelector('.chart-drawing-overlay');
      const canvas = plot.querySelector('canvas');
      const showing = svg && getComputedStyle(svg).visibility === 'visible';
      samples.push({
        top: plot.getBoundingClientRect().top,
        controls: controls.getBoundingClientRect().height,
        accept: Boolean([...controls.querySelectorAll('button')].find(button => !button.disabled && !button.hidden
          && ['Accept chart drawing', '接受图表绘图'].includes(button.textContent))),
        circles: showing ? svg.querySelectorAll('circle').length : 0,
        overlay: showing ? [svg.clientWidth, svg.clientHeight] : null,
        pane: [canvas.clientWidth, canvas.clientHeight],
      });
    }
    return samples;
  }, frames);
}

async function candleCloseOffset(chart, position, color, rising) {
  return chart.locator('canvas').evaluateAll((canvases, expected) => {
    const { position, color, rising } = expected;
    for (const canvas of canvases) {
      const rect = canvas.getBoundingClientRect();
      if (Math.abs(rect.width - position.width) > 1 || Math.abs(rect.height - position.height) > 1) continue;
      const ratioX = canvas.width / rect.width;
      const ratioY = canvas.height / rect.height;
      const radius = Math.max(2, Math.round(2 * ratioX));
      const left = Math.round(position.x * ratioX) - radius;
      const width = 2 * radius + 1;
      if (left < 0 || left + width > canvas.width) continue;
      const pixels = canvas.getContext('2d').getImageData(left, 0, width, canvas.height).data;
      const bodyRows = [];
      for (let y = 0; y < canvas.height; y++) {
        // A body crosses both sides of the center. A matching wick is too thin.
        if ([0, radius, width - 1].every(x => color.every((channel, i) =>
          Math.abs(channel - pixels[(y * width + x) * 4 + i]) < 6))) bodyRows.push(y);
      }
      if (bodyRows.length) {
        const edge = rising ? bodyRows[0] : bodyRows.at(-1) + 1;
        return { anchorY: position.y, bodyCloseY: edge / ratioY, offset: position.y - edge / ratioY };
      }
    }
    return null;
  }, { position, color, rising });
}

const layoutCases = [
  { language: 'English', width: 1104, panel: 'Watchlist', open: 'Open chart drawing program', accept: 'Accept chart drawing',
    remove: 'Remove chart drawing', restore: 'Restore accepted chart drawing', output: 'Drawing crosshair observation',
    chart: 'Candlestick and volume chart for SIM.CN.FUTURE.001', overlay: 'Projected chart drawing', error: 'This chart drawing could not be displayed.' },
  { language: 'Simplified Chinese', width: 900, panel: '自选列表', open: '打开图表绘图程序', accept: '接受图表绘图',
    remove: '移除图表绘图', restore: '恢复已接受的图表绘图', output: '绘图十字光标观测',
    chart: 'SIM.CN.FUTURE.001 的 K 线和成交量图', overlay: '图表投影绘图', error: '无法显示此图表绘图。' },
];

for (const scenario of layoutCases) {
  test(`chart drawing drafts stay actionable with the watchlist open in ${scenario.language}`, async ({ page }, info) => {
    await page.setViewportSize({ width: scenario.width, height: 721 });
    const frozen = await openDefaultSample(page, { trigger: () => page.goto('/?chart-drawing=1#demo') });
    if (scenario.language === 'Simplified Chinese') {
      await page.getByRole('button', { name: 'Language settings' }).click();
      await page.getByLabel('Interface language').selectOption('zh-Hans');
      await page.keyboard.press('Escape');
    }
    const panel = page.locator('.panel-watchlist');
    await expect(panel).toContainText(scenario.panel);
    const chart = page.getByRole('img', { name: scenario.chart, exact: true });
    const circles = page.getByRole('img', { name: scenario.overlay }).locator('circle');
    const picker = page.getByLabel(scenario.open);
    const accept = page.getByRole('button', { name: scenario.accept, exact: true });
    const remove = page.getByRole('button', { name: scenario.remove, exact: true });
    const readout = page.getByLabel(scenario.output);
    const original = await readFile(program, 'utf8');
    const observations = { frozen, scenario: scenario.language, clientWidth: scenario.width, drafts: [], resizes: [], closes: [] };
    const archive = () => writeFile(info.outputPath('chart-draft-layout.json'), JSON.stringify(observations, null, 2) + '\n');

    async function assertCloseEdges(label) {
      const positions = await Promise.all([anchor(circles.first()), anchor(circles.last())]);
      const closes = await Promise.all([
        candleCloseOffset(chart, positions[0], [247, 99, 114], false),
        candleCloseOffset(chart, positions[1], [67, 212, 172], true),
      ]);
      observations.closes.push({ label, closes });
      await archive();
      for (const close of closes) {
        expect(close, label).not.toBeNull();
        expect(Math.abs(close.offset), label).toBeLessThanOrEqual(2);
      }
      return positions;
    }
    async function acceptDraft(label) {
      await expect(accept).toBeVisible();
      const samples = await drawingLayoutFrames(chart, 42);
      observations.drafts.push({ label, samples });
      await archive();
      expect(samples.every(frame => frame.accept && frame.circles === 2), label).toBe(true);
      expect(new Set(samples.map(frame => frame.top)).size, label).toBe(1);
      await assertCloseEdges(label);
      await accept.click();
      await expect(remove).toBeVisible();
    }

    await picker.setInputFiles(program);
    await acceptDraft('first narrow draft');
    await picker.setInputFiles({ name: 'replacement.js', mimeType: 'text/javascript',
      buffer: Buffer.from(original.replace('? 8 : 5', '? 10 : 7')) });
    await acceptDraft('replacement narrow draft');
    await expect(circles.first()).toHaveAttribute('r', '7');

    const restore = page.getByRole('button', { name: scenario.restore, exact: true });
    await picker.setInputFiles({ name: 'rejected.js', mimeType: 'text/javascript',
      buffer: Buffer.from('// future overlay\nthrow new Error("rejected replacement");') });
    await expect(page.getByRole('alert')).toContainText(scenario.error);
    await expect(accept).toHaveCount(0);
    await expect(circles.first()).toHaveAttribute('r', '7');
    await restore.click();
    await expect(page.getByRole('alert')).toHaveCount(0);

    for (const viewport of [{ width: 1280, height: 800 }, { width: scenario.width, height: 721 }]) {
      const oldWidth = (await anchor(circles.first())).width;
      const sampling = drawingLayoutFrames(chart, 30);
      await page.setViewportSize(viewport);
      const samples = await sampling;
      observations.resizes.push({ viewport, samples });
      await archive();
      expect(samples.filter(frame => frame.overlay && frame.overlay.join(',') !== frame.pane.join(','))).toEqual([]);
      await expect.poll(async () => Math.abs((await anchor(circles.first())).width - oldWidth)).toBeGreaterThan(70);
      await expect(panel).toBeVisible();
      const positions = await assertCloseEdges(`accepted at ${viewport.width}`);
      for (const [index, stamp, value] of [[0, first, '3499.2'], [1, second, '3499.8']]) {
        const point = positions[index];
        await page.mouse.move(point.left + point.x, point.top + point.y);
        await expect(readout).toContainText(stamp);
        await expect(readout).toContainText(value);
        await expect(circles.nth(index)).toHaveAttribute('r', '10');
        await expect(circles.nth(1 - index)).toHaveAttribute('r', '7');
      }
      await page.mouse.move(positions[1].left + positions[1].width + 4, positions[1].top + 30);
      await expect(readout).toHaveCount(0);
    }
  });
}
