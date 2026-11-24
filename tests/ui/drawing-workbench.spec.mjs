import { readFile, mkdir } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';
import { buildPreview, persistedRows, withPeer, withProfile, withWorkspace } from './ai-workflow-fixture.mjs';

const fixture = async path => JSON.parse(await readFile(new URL(path, import.meta.url), 'utf8'));
const button = (scope, name) => scope.getByRole('button', { name, exact: true });
const overlay = `// future overlay
for (const b of rows) {
  const x = xTime(b.time_ms);
  circle(x, yValue(b.close), 6, 0);
  line(x, yValue(b.high), x, yValue(b.low), 1);
}`;
const free = `// future free
for (const [i, b] of rows.entries()) {
  rect(10 + i * 180, 40, b.volume, 20 + i * 10, 2);
}`;

async function connectDrawing(page, peer, studio, chat, intent, request) {
  await button(studio, 'Ask AI to author a view').click();
  await intent.fill(request);
  await button(chat, 'Ask').click();
  const settings = page.getByRole('dialog', { name: 'AI settings' });
  await settings.getByLabel('Server origin').fill(peer.origin);
  await settings.getByLabel('Model', { exact: true }).fill('synthetic-drawing:fixture');
  await button(settings, 'Review connection').click();
  await button(settings, 'Connect and enable').click();
}

test.beforeAll(async ({}, info) => { info.setTimeout(900_000); await buildPreview(); });

test('drawing inputs match independent frozen market and indicator-input records', async () => {
  const sources = await fixture('../../apps/desktop/ui/src/workbench-frozen-sources.json');
  const history = await fixture('../fixtures/indicators/confirmation-sample-history.json');
  const market = await fixture('../fixtures/market/series.json');
  expect(sources[0]).toMatchObject({ dataset_id: history.snapshot.dataset_id, dataset_revision: history.snapshot.dataset_revision,
    snapshot_hash: history.snapshot.content_hash, as_of_ms: history.snapshot.as_of_ms, bars: history.bars });
  for (const source of sources) {
    const original = market.fixtures.find(f => f.scenario === 'normal' && f.instrument.id === source.instrument_id).history;
    expect(source.bars).toEqual(original.bars.slice(0, 2));
    expect(source.dataset_revision).toBe(original.dataset.revision);
    expect(source.dataset_id).toBe(original.dataset.id);
  }
});

test('ordinary text turns supply drawing code, host projections and atomic recovery', async ({ browser }) => {
  await withPeer(peer => withProfile(profile => withWorkspace(browser, profile, async page => {
    const before = persistedRows(profile);
    await button(page, 'Open blank AI workbench').click();
    await button(page, 'Drawing studio').click();
    const studio = page.getByRole('dialog', { name: 'Drawing studio' });
    const chat = page.getByRole('dialog', { name: 'Author with AI' });
    const intent = chat.getByLabel('What should the workbench show or do?');
    const editor = studio.getByRole('textbox', { name: 'Program source' });
    const geometry = studio.getByLabel('Program geometry');
    const status = studio.getByRole('status').first();

    await expect(status).toHaveText('Blank canvas');
    await connectDrawing(page, peer, studio, chat, intent, 'Place rings at the closes and draw the two high-low spans.');
    async function reply(code, previous) {
      peer.respond({ text: code });
      await button(chat, 'Send').click();
      await expect(intent).toHaveValue('');
      const sent = peer.traffic.at(-1).body;
      expect(sent.tools).toBeUndefined();
      expect(sent.messages.at(-1).content).toContain('Accepted program:\n' + previous);
      expect(sent.messages.at(-1).content).not.toContain('3499.2');
      await button(chat, 'Close').click();
      await expect(status).toHaveText('Draft preview');
      await expect(editor).toHaveValue(code);
    }
    await reply(overlay, 'none');
    await expect(geometry.locator('circle').first()).toHaveAttribute('cx', '64');
    expect(Number(await geometry.locator('circle').first().getAttribute('cy'))).toBeCloseTo(146.666667, 4);
    await expect(geometry.locator('circle').last()).toHaveAttribute('cx', '616');
    const firstLine = geometry.locator('line').first();
    await expect(firstLine).toHaveAttribute('y1', '24');
    await button(studio, 'Accept view').click();
    await button(studio, 'Expand viewport').click();
    await expect(geometry.locator('circle').first()).toHaveAttribute('cx', '202');
    expect(Number(await geometry.locator('circle').first().getAttribute('cy'))).toBeCloseTo(131.333333, 4);
    await expect(firstLine).toHaveAttribute('y1', '70');
    await button(studio, 'Fit frozen bars').click();
    await expect(firstLine).toHaveAttribute('y1', '24');
    await studio.getByRole('slider').focus();
    await studio.getByRole('slider').press('ArrowRight');
    await expect(geometry).toHaveAttribute('stroke-width', '4');
    expect(peer.traffic).toHaveLength(1);
    await button(studio, 'Ask AI to revise the view').click();
    await intent.fill('Use the same rows without a chart; draw offset outline rectangles sized by the volumes.');
    await reply(free, overlay);
    await expect(studio.getByRole('img', { name: 'Axis-free drawing' })).toBeVisible();
    await expect(studio.getByLabel('Frozen close reference')).toHaveCount(0);
    await expect(geometry.locator('rect').first()).toHaveAttribute('width', '120');
    await expect(geometry.locator('rect').last()).toHaveAttribute('width', '80');
    await button(studio, 'Accept view').click();
    const accepted = await geometry.innerHTML();

    async function tryCode(body, error) {
      await editor.fill('// future free\n' + body);
      await button(studio, 'Preview draft').click();
      await expect(studio.getByRole('alert')).toHaveText(error);
      expect(await geometry.innerHTML()).toBe(accepted);
      await expect(button(studio, 'Accept view')).toHaveCount(0);
      await button(studio, 'Restore accepted view').click();
      await expect(editor).toHaveValue(free);
    }
    const rejected = 'The drawing failed validation or exceeded its limits.';
    await tryCode('circle(NaN, 0, 9, 0);', rejected);
    await tryCode('for(let i=0;i<257;i++) circle(1,1,1,0);', rejected);
    // Exceed the 2 MiB guest heap in one allocation. Incrementally building a
    // large array can hit the independent CPU deadline first on a busy engine.
    await tryCode('const s = "x".repeat(4194304); circle(1, 1, s.length / 4194304, 0);', rejected);
    await tryCode('while (true) {}', 'The drawing exceeded its time limit.');

    const globals = ['window','document','fetch','XMLHttpRequest','WebSocket','postMessage','importScripts','Worker',
      'indexedDB','navigator','localStorage','process','require','std','os','__TAURI_INTERNALS__','confirm'];
    await editor.fill('// stock free\nif (' + globals.map(key => `typeof ${key} === "undefined"`).join(' && ')
      + ') circle(30,30,rows[0].volume,0);');
    await button(studio, 'Preview draft').click();
    await expect(geometry.locator('circle')).toHaveAttribute('r', '20');
    await button(studio, 'Restore accepted view').click();
    await button(studio, 'Undo accepted edit').click();
    await expect(editor).toHaveValue(overlay);
    await expect(geometry.locator('circle')).toHaveCount(2);
    expect(peer.traffic).toHaveLength(2);
    expect(persistedRows(profile)).toEqual(before);
  })));
});

test('replay the independently requested diamonds and a source-only selection edit on the frozen runtime', async ({ browser }) => {
  const program = name => readFile(new URL('../fixtures/workbench/' + name + '.js', import.meta.url), 'utf8');
  const original = await program('reference-diamonds');
  const revised = await program('reference-diamonds-revised');
  const request = 'Use the same two frozen synthetic futures closes. I want to see how far each minute is from a reference price, with time running left to right. Draw a thin horizontal reference at 3499.4, a stem from it to each close, and a hollow diamond at the close. I should see one hanging below and one rising above. Let me adjust the reference directly and select a diamond to read its minute, close and signed distance. Keep this chart-free view bound to those original observations.';
  const followup = 'Change only the selected diamond: give it a second, larger outline. Keep the unselected diamond, stems, ordering, reference control, exact readout and existing source as they are.';
  await withPeer(peer => withProfile(profile => withWorkspace(browser, profile, async page => {
    await button(page, 'Open blank AI workbench').click();
    await button(page, 'Drawing studio').click();
    const studio = page.getByRole('dialog', { name: 'Drawing studio' });
    const chat = page.getByRole('dialog', { name: 'Author with AI' });
    const intent = chat.getByLabel('What should the workbench show or do?');
    const editor = studio.getByRole('textbox', { name: 'Program source' });
    const geometry = studio.getByLabel('Program geometry');
    const lines = () => geometry.locator('line').evaluateAll(nodes => nodes.map(line =>
      ['x1', 'y1', 'x2', 'y2'].map(k => Number(line.getAttribute(k)))));
    async function receive(code) {
      peer.respond({ text: code });
      await button(chat, 'Send').click();
      await expect(intent).toHaveValue('');
      await button(chat, 'Close').click();
      await expect(button(studio, 'Accept view')).toBeVisible();
      await expect(editor).toHaveValue(code.trim());
    }

    await connectDrawing(page, peer, studio, chat, intent, request);
    await receive(original);
    await expect(studio.getByRole('img', { name: 'Axis-free drawing' })).toBeVisible();
    const first = await lines();
    expect(first).toHaveLength(11);
    for (const viewport of [{ width: 1280, height: 800 }, { width: 900, height: 650 }]) {
      await page.setViewportSize(viewport);
      const pixels = await geometry.evaluate(g => {
        const bounds = g.ownerSVGElement.getBoundingClientRect();
        const matrix = g.getScreenCTM();
        const centers = [1, 6].map(index => {
          const line = g.children[index];
          return new DOMPoint(line.x2.baseVal.value, line.y2.baseVal.value).matrixTransform(matrix);
        });
        return { width: bounds.width, height: bounds.height,
          dx: centers[1].x - centers[0].x, dy: centers[0].y - centers[1].y };
      });
      expect(pixels.height).toBeGreaterThan(150);
      expect(pixels.width / pixels.height).toBeCloseTo(640 / 240, 2);
      expect(pixels.dx).toBeGreaterThan(pixels.width * 0.85);
      expect(pixels.dy).toBeGreaterThan(pixels.height * 0.25);
      await test.info().attach('drawing-' + viewport.width + '.json', {
        body: JSON.stringify({ viewport, pixels }), contentType: 'application/json',
      });
    }
    const [reference, leftStem, rightStem] = [first[0], first[1], first[6]];
    expect(reference[1]).toBeCloseTo(126.222222, 4);
    expect(reference[1]).toBe(reference[3]);
    expect(leftStem[0]).toBe(64);
    expect(rightStem[0]).toBe(616);
    expect(leftStem[1]).toBe(reference[1]);
    expect(rightStem[1]).toBe(reference[1]);
    expect(leftStem[3]).toBeCloseTo(146.666667, 4);
    expect(rightStem[3]).toBeCloseTo(85.333333, 4);
    for (const [start, stem] of [[2, leftStem], [7, rightStem]]) {
      const [x, , , y] = stem;
      expect(first.slice(start, start + 4)).toEqual([
        [x, y - 8, x + 8, y], [x + 8, y, x, y + 8], [x, y + 8, x - 8, y], [x - 8, y, x, y - 8],
      ]);
    }
    await button(studio, 'Accept view').click();

    // The frozen ABI exposes source editing and host stroke weight. It offers
    // no scalar parameter control, point selection event or text primitive.
    await editor.fill(original.replace('3499.4', '3499.2'));
    await button(studio, 'Preview draft').click();
    await expect(geometry.locator('line')).toHaveCount(10);
    expect((await lines())[0][1]).toBe(leftStem[3]);
    await button(studio, 'Restore accepted view').click();
    await button(studio, 'Ask AI to revise the view').click();
    await intent.fill(followup);
    await receive(revised);
    expect(peer.traffic[1].body.messages.at(-1).content).toContain(original.trim());
    expect(await lines()).toEqual(first);

    await editor.fill(revised.replace('selectedIndex = -1', 'selectedIndex = 1'));
    await button(studio, 'Preview draft').click();
    await expect(geometry.locator('line')).toHaveCount(15);
    const second = await lines();
    expect(second.slice(0, 11)).toEqual(first);
    expect(second[11]).toEqual([616, rightStem[3] - 13, 629, rightStem[3]]);
    await studio.getByText('Frozen synthetic source', { exact: true }).click();
    await expect(studio).toContainText('3a1b0032b57711c9563131caf34c8cfda9d91bbf5ef5e9dbc7a4f0f48ab390d1');
    expect(peer.traffic).toHaveLength(2);
  })));
});

test('an external client authors pennants and changes only the later tip on the shared viewport', async ({ browser }) => {
  await withProfile(profile => withWorkspace(browser, profile, async page => {
    await button(page, 'Open blank AI workbench').click();
    await button(page, 'Drawing studio').click();
    const studio = page.getByRole('dialog', { name: 'Drawing studio' });
    const editor = studio.getByRole('textbox', { name: 'Program source' });
    const geometry = studio.getByLabel('Program geometry');
    const marks = () => geometry.locator('line').evaluateAll(lines => lines.map(l =>
      ['x1','y1','x2','y2'].map(k => Number(l.getAttribute(k)))));
    async function apply(file) {
      await editor.fill(await readFile(new URL('../fixtures/workbench/' + file + '.js', import.meta.url), 'utf8'));
      await button(studio, 'Preview draft').click();
      await button(studio, 'Accept view').click();
    }
    function verify(lines, anchors) {
      expect(lines).toHaveLength(8);
      for (const [i, [x, expectedY, length]] of anchors.entries()) {
        const [pole, top, bottom, back] = lines.slice(4 * i, 4 * i + 4);
        const y = pole[1];
        expect(y).toBeCloseTo(expectedY, 4);
        expect(pole).toEqual([x, y, x, y + length]);
        expect(top).toEqual([x, y, x - 12, y + 4]);
        expect(bottom).toEqual([x - 12, y + 4, x, y + 8]);
        expect(back).toEqual([x, y + 8, x, y]);
      }
    }
    await apply('volume-pennants');
    const initial = await marks();
    verify(initial, [[64,146.666667,24],[616,85.333333,16]]);
    await studio.getByRole('slider').focus();
    await studio.getByRole('slider').press('ArrowRight');
    await expect(geometry).toHaveAttribute('stroke-width', '4');
    await button(studio, 'Expand viewport').click();
    await expect(geometry.locator('line').first()).toHaveAttribute('x1', '202');
    verify(await marks(), [[202,131.333333,24],[478,100.666667,16]]);
    await button(studio, 'Fit frozen bars').click();
    await expect(geometry.locator('line').first()).toHaveAttribute('x1', '64');
    expect(await marks()).toEqual(initial);
    await apply('volume-pennants-revised');
    expect(await marks()).toEqual(initial);
    const circle = geometry.locator('circle');
    await expect(circle).toHaveCount(1);
    await expect(circle).toHaveAttribute('fill', 'none');
    await expect(circle).toHaveAttribute('cx', '604');
    await expect(circle).toHaveAttribute('cy', String(initial[4][1] + 4));
    await expect(geometry).toHaveAttribute('stroke-width', '4');
    await button(studio, 'Expand viewport').click();
    await expect(circle).toHaveAttribute('cx', '466');
    verify(await marks(), [[202,131.333333,24],[478,100.666667,16]]);
  }));
});


test('observe the held-out pennants in the native Windows WebView2 window', async ({}, info) => {
  test.skip(process.platform !== 'win32', 'Requires Windows UI Automation and WebView2.');
  test.setTimeout(25 * 60_000);
  const output = process.env.SCOPEHUB_RUN_DIR
    ? resolve(process.env.SCOPEHUB_RUN_DIR, 'webview2-drawing')
    : info.outputPath('webview2-drawing');
  await mkdir(output, { recursive: true });
  const script = fileURLToPath(new URL('./renderer-probe-windows.ps1', import.meta.url));
  const child = spawn('powershell.exe', ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
    '-File', script, '-DrawingOutput', output], { stdio: 'inherit', windowsHide: true });
  const [code] = await once(child, 'exit');
  expect(code, 'WebView2 window, painted pennants, recovery and disposable profile cleanup').toBe(0);
});
