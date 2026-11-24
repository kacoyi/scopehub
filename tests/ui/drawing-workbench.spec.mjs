import { readFile } from 'node:fs/promises';
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
    await button(studio, 'Ask AI to author a view').click();
    await intent.fill('Place rings at the closes and draw the two high-low spans.');
    await button(chat, 'Ask').click();
    const settings = page.getByRole('dialog', { name: 'AI settings' });
    await settings.getByLabel('Server origin').fill(peer.origin);
    await settings.getByLabel('Model', { exact: true }).fill('synthetic-drawing:fixture');
    await button(settings, 'Review connection').click();
    await button(settings, 'Connect and enable').click();
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
    await tryCode('const a=[]; for(let i=0;i<100000;i++) a.push({i, values:[i,i,i,i]}); circle(1,1,1,0);', rejected);
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
