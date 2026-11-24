import { execFile } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { promisify } from 'node:util';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';
import { prepareSeries, revoke } from './accepted-drawing-fixture.mjs';
import { buildPreview, persistedRows, withPeer, withWorkspace } from './ai-workflow-fixture.mjs';

test.beforeAll(async ({}, info) => {
  info.setTimeout(900_000);
  await buildPreview();
  await promisify(execFile)('cargo', ['build', '--locked', '--target-dir', 'target', '-p', 'scopehub-desktop',
    '--example', 'local_import_bridge', '--features', 'scopehub-desktop/portable-test-bridge', '--jobs', '2']);
});
const button = (scope, name) => scope.getByRole('button', { name, exact: true });
const file = name => fileURLToPath(new URL('../fixtures/workbench/accepted-series-' + name + '.js', import.meta.url));

for (const period of [2, 5]) {
  test('confirmed output is bound after writer restart, period ' + period, async ({ browser }, info) => {
    const root = await mkdtemp(join(tmpdir(), 'scopehub-workbench-series-'));
    try {
      const data = await prepareSeries(browser, root, period);
      await withWorkspace(browser, data.profile, async page => {
        const before = persistedRows(data.profile);
        await button(page, 'Open blank AI workbench').click();
        await button(page, 'Drawing studio').click();
        const studio = page.getByRole('dialog', { name: 'Drawing studio' });
        await studio.getByRole('combobox', { name: 'Accepted version', exact: true }).selectOption(data.version_id);
        await studio.getByRole('combobox', { name: 'Numeric series', exact: true }).selectOption({ label: 'mean' });
        await studio.getByText('Accepted series source', { exact: true }).click();
        await expect(studio.getByText(data.result_hash, { exact: true })).toBeVisible();
        await expect(studio.locator('dt').filter({ hasText: /^Output unit$/ }).locator('+ dd')).toHaveText('Unknown');
        await expect(studio.locator('dt').filter({ hasText: /^Source volume unit$/ }).locator('+ dd')).toHaveText('shares');
        const values = studio.getByText('Accepted observations', { exact: true });
        await values.click();
        const expected = period === 2 ? ['Unavailable', '115', '125', '135'] : Array(4).fill('Unavailable');
        await expect(studio.locator('tbody td:nth-child(2)')).toHaveText(expected);
        await expect(studio.locator('tbody td:first-child')).toHaveText([3, 4, 5, 6].map(day => new Date(Date.UTC(2026, 1, day)).toISOString()));
        const open = studio.getByLabel('Open local drawing program');
        const geometry = studio.getByLabel('Program geometry');
        await open.setInputFiles(file('dots'));
        await button(studio, 'Accept view').click();
        await expect(geometry.locator('circle')).toHaveCount(period === 2 ? 3 : 0);
        if (period === 2) {
          await expect(geometry.locator('circle').first()).toHaveAttribute('cx', '248');
          await expect(geometry.locator('circle').first()).toHaveAttribute('cy', '208');
          await expect(geometry.locator('circle').last()).toHaveAttribute('cy', '24');
        }
        await open.setInputFiles(file('strips'));
        await button(studio, 'Accept view').click();
        await expect(geometry.locator('rect')).toHaveCount(period === 2 ? 3 : 0);
        await expect(geometry.locator('line')).toHaveCount(period === 2 ? 1 : 4);
        if (period === 2) {
          expect(await geometry.locator('rect').evaluateAll(nodes => nodes.map(n => Number(n.getAttribute('width')))))
            .toEqual([32, 124, 216]);
        }
        await button(studio, 'Inspect observation at 2026-02-03T00:00:00.000Z').click();
        await expect(studio.getByLabel('Accepted observation', { exact: true })).toContainText('Unavailable');
        await page.screenshot({ path: info.outputPath('accepted-series.png') });
        await studio.getByRole('combobox', { name: 'Numeric series', exact: true }).selectOption({ label: 'standard-deviation' });
        await expect(geometry).toHaveCount(0);
        await expect(studio.getByLabel('Accepted observation', { exact: true })).toHaveCount(0);
        await expect(studio.getByText('Accepted series source', { exact: true })).toBeVisible();
        await open.setInputFiles(file('dots'));
        await button(studio, 'Accept view').click();
        expect(persistedRows(data.profile)).toEqual(before);
        await revoke(root, data.revision);
        await expect(geometry).toHaveCount(0);
        await expect(studio.getByText(data.result_hash)).toHaveCount(0);
        await expect(studio.locator('tbody')).toHaveCount(0);
        await expect(button(studio, 'Undo accepted edit')).toHaveCount(0);
      });
    } finally { await rm(root, { recursive: true, force: true }); }
  });
}

test('the ordinary model revision receives the complete series program and nullable API', async ({ browser }) => {
  const root = await mkdtemp(join(tmpdir(), 'scopehub-series-turn-'));
  try {
    const data = await prepareSeries(browser, root);
    await withPeer(peer => withWorkspace(browser, data.profile, async page => {
      await button(page, 'Open blank AI workbench').click();
      await button(page, 'Drawing studio').click();
      const studio = page.getByRole('dialog', { name: 'Drawing studio' });
      await studio.getByRole('combobox', { name: 'Accepted version' }).selectOption(data.version_id);
      await studio.getByRole('combobox', { name: 'Numeric series' }).selectOption({ label: 'mean' });
      await expect(studio.getByText('Accepted series source')).toBeVisible();
      const code = '// series free\n// Viewport remains host-owned.\nfor (const p of rows) {\n  if (p.value !== null) circle(xTime(p.time_ms), yValue(p.value), 6, 1);\n}';
      await studio.getByLabel('Program source').fill(code);
      await button(studio, 'Preview draft').click();
      await button(studio, 'Accept view').click();
      await button(studio, 'Ask AI to revise the view').click();
      const chat = page.getByRole('dialog', { name: 'Author with AI' });
      await chat.getByLabel('What should the workbench show or do?').fill('Increase the radius to eight.');
      await button(chat, 'Ask').click();
      const settings = page.getByRole('dialog', { name: 'AI settings' });
      await settings.getByLabel('Server origin').fill(peer.origin);
      await settings.getByLabel('Model', { exact: true }).fill('synthetic-drawing:fixture');
      await button(settings, 'Review connection').click();
      await button(settings, 'Connect and enable').click();
      peer.respond({ text: code.replace('6, 1', '8, 1') });
      await button(chat, 'Send').click();
      await expect.poll(() => peer.traffic.length).toBe(1);
      const prompt = peer.traffic[0].body.messages.at(-1).content;
      expect(prompt).toContain('finite number or null');
      expect(prompt).toContain('Accepted program:\n' + code + '\nUser intent:\nIncrease the radius');
      for (const privateValue of [data.result_hash, data.revision, data.version_id, '1770076800000']) {
        expect(prompt).not.toContain(privateValue);
      }
      await expect(chat.getByLabel('What should the workbench show or do?')).toHaveValue('');
      await button(chat, 'Close').click();
      await expect(studio.getByLabel('Program geometry').locator('circle').first()).toHaveAttribute('r', '8');
    }));
  } finally { await rm(root, { recursive: true, force: true }); }
});
