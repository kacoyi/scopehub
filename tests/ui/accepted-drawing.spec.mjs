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

for (const outcome of ['recover', 'revoke', 'deny', 'switch', 'close', 'expire']) {
  test('focus admission recheck: ' + outcome, async ({ browser }) => {
    const root = await mkdtemp(join(tmpdir(), 'scopehub-focus-series-'));
    try {
      const data = await prepareSeries(browser, root);
      await withWorkspace(browser, data.profile, async (page, dispatch, _credential, holdRevisionRead) => {
        await button(page, 'Open blank AI workbench').click();
        await button(page, 'Drawing studio').click();
        const studio = page.getByRole('dialog', { name: 'Drawing studio' });
        const version = studio.getByRole('combobox', { name: 'Accepted version', exact: true });
        await version.selectOption(data.version_id);
        await studio.getByRole('combobox', { name: 'Numeric series', exact: true }).selectOption({ label: 'mean' });
        await expect(studio.getByText('Accepted series source', { exact: true })).toBeVisible();
        await studio.getByLabel('Open local drawing program').setInputFiles(file('dots'));
        await button(studio, 'Accept view').click();
        const geometry = studio.getByLabel('Program geometry');
        await expect(geometry.locator('circle')).toHaveCount(3);
        const baseline = (await dispatch({ type: 'snapshot' })).result;
        let requests = 0;
        const outputRequested = Promise.withResolvers();
        const releaseOutput = Promise.withResolvers();
        let resume;
        const resumeRead = new Promise(resolve => { resume = resolve; });
        await page.route('**/__scopehub/workspace', async route => {
          const type = route.request().postDataJSON()?.type;
          if (outcome === 'recover' && type === 'indicator_accepted_output' && requests > 1) {
            outputRequested.resolve();
            await releaseOutput.promise;
          }
          if (type === 'snapshot' && ++requests > 1) {
            if (outcome === 'deny') return route.fulfill({ status: 403, json: {
              ok: false, error: { code: 'PERMISSION_DENIED', retryable: false },
            } });
            if (outcome === 'switch') await resumeRead;
          }
          return route.continue();
        });
        let release = await holdRevisionRead();
        try {
          const rejected = page.waitForResponse(r => r.status() === 429 && r.request().postDataJSON()?.type === 'snapshot');
          await page.evaluate(() => window.dispatchEvent(new Event('focus')));
          expect((await (await rejected).json()).error).toEqual({ code: 'BUSY', retryable: true });
          await expect(geometry).toHaveCount(0);
          await expect(studio.locator('tbody')).toHaveCount(0);
          await expect(studio.getByText(data.result_hash, { exact: true })).toHaveCount(0);
          await expect(button(studio, 'Undo accepted edit')).toHaveCount(0);
          if (outcome === 'close') await button(studio, 'Back to chart').click();
          if (outcome === 'revoke') await revoke(root, data.revision);
          if (outcome === 'expire' || outcome === 'deny') {
            await expect(studio.getByText('Accepted series unavailable.', { exact: true })).toBeVisible({ timeout: 6500 });
            expect(requests).toBeGreaterThan(1);
            if (outcome === 'deny') expect(requests).toBe(2);
          }
          release();
          release = undefined;
          if (outcome === 'switch') {
            await expect.poll(async () => (await dispatch({
              type: 'workspace_source', request_id: crypto.randomUUID(),
              expected_selection_revision: baseline.selection_revision, source: { kind: 'sample', instrument_id: null },
            })).ok).toBe(true);
          }
          resume();
          if (outcome === 'recover') {
            await outputRequested.promise;
            await expect(studio.getByText('Checking workspace source…')).toBeVisible();
            await expect(geometry).toHaveCount(0);
            releaseOutput.resolve();
            await expect(geometry.locator('circle')).toHaveCount(3);
            await expect(version).toHaveValue(data.version_id);
            await expect(studio.getByText(data.result_hash, { exact: true })).toHaveCount(1);
            await expect(button(studio, 'Undo accepted edit')).toBeEnabled();
          } else if (outcome === 'close') {
            await expect(studio).toHaveCount(0);
            await button(page, 'Open blank AI workbench').click();
            await button(page, 'Drawing studio').click();
            await expect(version).toHaveValue('');
            await expect(geometry).toHaveCount(0);
          } else {
            if (outcome === 'revoke') {
              await expect(studio.getByText('Accepted series unavailable.', { exact: true })).toBeVisible();
              const read = await dispatch({ type: 'indicator_accepted_output', request_id: crypto.randomUUID(), layer_id: data.layer_id });
              expect(read.error?.code).toBe('PERMISSION_DENIED');
              expect(read.result).toBeUndefined();
            }
            if (outcome === 'switch') await expect(studio.getByText('Synthetic preview', { exact: true })).toBeVisible();
            await expect(version).toHaveCount(0);
            await expect(geometry).toHaveCount(0);
            await expect(studio.getByText(data.result_hash, { exact: true })).toHaveCount(0);
          }
        } finally { release?.(); resume(); releaseOutput.resolve(); }
      }, [], true);
    } finally { await rm(root, { recursive: true, force: true }); }
  });
}

test('cancelling a program file choice preserves the selected observation and dialog Escape closes the studio', async ({ browser }) => {
  const root = await mkdtemp(join(tmpdir(), 'scopehub-studio-file-cancel-'));
  try {
    const data = await prepareSeries(browser, root);
    await withWorkspace(browser, data.profile, async page => {
      await button(page, 'Open blank AI workbench').click();
      await button(page, 'Drawing studio').click();
      const studio = page.getByRole('dialog', { name: 'Drawing studio' });
      const version = studio.getByRole('combobox', { name: 'Accepted version', exact: true });
      await version.selectOption(data.version_id);
      await studio.getByRole('combobox', { name: 'Numeric series', exact: true }).selectOption({ label: 'mean' });
      await expect(studio.getByText('Accepted series source', { exact: true })).toBeVisible();
      const picker = studio.getByLabel('Open local drawing program');
      await picker.setInputFiles(file('dots'));
      await button(studio, 'Accept view').click();
      await button(studio, 'Inspect observation at 2026-02-04T00:00:00.000Z').click();
      const selected = studio.getByLabel('Accepted observation', { exact: true });
      await expect(selected).toHaveText('2026-02-04T00:00:00.000Z · 115 · Unknown');
      const source = await studio.getByLabel('Program source').inputValue();
      // Reproduce the bubbling event observed from the native WebKitGTK chooser.
      await picker.dispatchEvent('cancel', { bubbles: true, cancelable: false });
      await expect(studio).toBeVisible();
      await expect(version).toHaveValue(data.version_id);
      await expect(studio.getByText(data.result_hash, { exact: true })).toHaveCount(1);
      await expect(selected).toHaveText('2026-02-04T00:00:00.000Z · 115 · Unknown');
      await expect(studio.getByLabel('Program source')).toHaveValue(source);
      await expect(studio.getByLabel('Program geometry').locator('circle')).toHaveCount(3);
      await expect(button(studio, 'Undo accepted edit')).toBeEnabled();
      await page.keyboard.press('Escape');
      await expect(studio).toHaveCount(0);
      await expect(button(page, 'Open blank AI workbench')).toBeVisible();
    });
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('named authored view survives writer restart and loses its data after revocation', async ({ browser }) => {
  const root = await mkdtemp(join(tmpdir(), 'scopehub-saved-drawing-'));
  const code = '// series free\n// scalar radius 2 12 1 6\nfor (const p of rows) {\n  if (p.value !== null) circle(xTime(p.time_ms), yValue(p.value), params.radius, 0);\n}';
  const revised = code.replace('params.radius, 0', 'params.radius, 2');
  async function studioFor(page, saved = false) {
    if (saved) {
      await button(page, 'Open saved view: Synthetic morning view').click();
      return page.getByRole('dialog', { name: 'Drawing studio' });
    }
    await button(page, 'Open blank AI workbench').click();
    await button(page, 'Drawing studio').click();
    return page.getByRole('dialog', { name: 'Drawing studio' });
  }
  try {
    const data = await prepareSeries(browser, root);
    const name = 'Synthetic morning view';
    await withWorkspace(browser, data.profile, async (page, dispatch) => {
      const studio = await studioFor(page);
      await studio.getByRole('combobox', { name: 'Accepted version', exact: true }).selectOption(data.version_id);
      await studio.getByRole('combobox', { name: 'Numeric series', exact: true }).selectOption({ label: 'mean' });
      await expect(studio.getByText('Accepted series source', { exact: true })).toBeVisible();
      await studio.getByLabel('Program source').fill(code);
      await button(studio, 'Preview draft').click();
      await button(studio, 'Accept view').click();
      await studio.getByRole('slider', { name: 'radius' }).press('ArrowRight');
      await expect(studio.getByLabel('Program geometry').locator('circle').first()).toHaveAttribute('r', '7');
      await studio.getByRole('slider', { name: 'Stroke weight' }).press('ArrowRight');
      await studio.getByLabel('Layout name').fill(name);
      await button(studio, 'Save accepted view').click();
      await expect(studio.getByText('Saved in workspace layout')).toBeVisible();
      const saved = (await dispatch({ type: 'snapshot' })).result.layouts.active;
      expect(saved.name).toBe(name);
      expect(saved.definition.authored_view).toEqual({
        api: 'series-drawing-1', source: code, stroke: 4, scalars: [7], dataset_id: data.revision,
        dataset_revision: expect.any(String), layer_id: data.layer_id, version_id: data.version_id,
        series_id: 'mean', result_hash: data.result_hash,
      });
      await studio.getByLabel('Program source').fill('// series free\nthrow Error();');
      await button(studio, 'Preview draft').click();
      await expect(studio.getByRole('alert')).toBeVisible();
      await expect(button(studio, 'Save accepted view')).toBeDisabled();
      await button(studio, 'Back to chart').click();
    });
    await withWorkspace(browser, data.profile, async (page, dispatch) => {
      const studio = await studioFor(page, true);
      const geometry = studio.getByLabel('Program geometry');
      await expect(geometry.locator('circle')).toHaveCount(3);
      await expect(geometry).toHaveAttribute('stroke-width', '4');
      await expect(geometry.locator('circle').first()).toHaveAttribute('r', '7');
      await expect(studio.getByLabel('Program source')).toHaveValue(code);
      await studio.getByLabel('Program source').fill(revised);
      await button(studio, 'Preview draft').click();
      await button(studio, 'Accept view').click();
      await button(studio, 'Save accepted view').click();
      await expect(studio.getByLabel('Program geometry').locator('circle').first()).toHaveAttribute('stroke', '#a8a4fc');
      await expect(studio.getByLabel('Program source')).toHaveValue(revised);
      await expect.poll(async () => (await dispatch({ type: 'snapshot' })).result?.layouts.active.definition.authored_view.source).toBe(revised);
    });
    await revoke(root, data.revision);
    await withWorkspace(browser, data.profile, async page => {
      const studio = await studioFor(page, true);
      await expect(studio.getByLabel('Program source')).toHaveValue(revised);
      await expect(studio.getByText('Saved source. Waiting for access to its accepted series.')).toBeVisible();
      await expect(studio.getByLabel('Program geometry')).toHaveCount(0);
      await expect(studio.getByText(data.result_hash)).toHaveCount(0);
      await expect(studio.locator('tbody')).toHaveCount(0);
      await studio.getByLabel('Program source').fill(revised + '\n// editable offline');
      await button(studio, 'Preview draft').click();
      await expect(studio.getByRole('alert')).toBeVisible();
    });
  } finally { await rm(root, { recursive: true, force: true }); }
});
