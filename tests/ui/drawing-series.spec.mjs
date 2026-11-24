import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';
import { buildPreview, persistedRows, withProfile, withWorkspace } from './ai-workflow-fixture.mjs';

const file = name => fileURLToPath(new URL('../fixtures/workbench/' + name + '.js', import.meta.url));
const button = (scope, name) => scope.getByRole('button', { name, exact: true });
test.beforeAll(async ({}, info) => { info.setTimeout(900_000); await buildPreview(); });

test('a directly authored file binds controls, host selection and scoped edits to a current Rust preview', async ({ browser }) => {
  await withProfile(profile => withWorkspace(browser, profile, async page => {
    const calls = [];
    page.on('request', request => {
      if (request.method() === 'POST') calls.push(request.postDataJSON()?.intent?.operation);
    });
    await button(page, 'Open offline sample').click();
    await button(page, 'Open blank AI workbench').click();
    await button(page, 'Drawing studio').click();
    const studio = page.getByRole('dialog', { name: 'Drawing studio' });
    const open = studio.getByLabel('Open local drawing program');
    const geometry = studio.getByLabel('Program geometry');
    const ref = studio.getByRole('slider', { name: 'reference', exact: true });
    const readout = studio.getByRole('region', { name: 'Selected frozen evidence' });
    const left = button(studio, 'Inspect observation at 21:00');
    const right = button(studio, 'Inspect observation at 21:01');
    const before = persistedRows(profile);

    await open.setInputFiles(file('interactive-diamonds'));
    await button(studio, 'Accept view').click();
    await expect(geometry.locator('line')).toHaveCount(11);
    await left.click();
    await expect(readout).toContainText('3,499.2 CNY');
    await expect(readout).toContainText('120 contracts');
    await expect(readout.getByText('-0.2', { exact: true })).toBeVisible();
    const resultHash = readout.getByText(/^[a-f0-9]{64}$/);
    const firstHash = await resultHash.innerText();
    await right.click();
    await expect(readout).toContainText('3,499.8 CNY');
    await expect(readout.getByText('+0.4', { exact: true })).toBeVisible();

    await ref.focus(); await ref.press('ArrowRight');
    await expect(ref).toHaveValue('3499.6');
    await expect(readout.getByText('+0.2', { exact: true })).toBeVisible();
    expect(await resultHash.innerText()).not.toBe(firstHash);
    const oldMarks = await geometry.innerHTML();
    const sameHash = await resultHash.innerText();
    await open.setInputFiles(file('interactive-diamonds-revised'));
    await button(studio, 'Accept view').click();
    await expect(ref).toHaveValue('3499.6');
    await expect(right).toHaveAttribute('aria-pressed', 'true');
    await expect(geometry.locator('line')).toHaveCount(15);
    expect((await geometry.innerHTML()).startsWith(oldMarks)).toBe(true);
    await expect(resultHash).toHaveText(sameHash);
    await left.focus(); await left.press('Enter');
    await expect(left).toHaveAttribute('aria-pressed', 'true');
    await expect(readout.getByText('-0.4', { exact: true })).toBeVisible();
    await expect(geometry.locator('line').nth(6)).toHaveAttribute('x1', '64');

    // Interrupt an established calculated readout, then retry through the
    // real core. A prior result cannot be shown for the new reference.
    await page.route('**/__scopehub/workspace', async route => {
      if (route.request().postDataJSON()?.type === 'indicator_preview_status') {
        return route.fulfill({ status: 503, json: { ok: false, error: { code: 'WORKER_EXITED' } } });
      }
      return route.continue();
    });
    await ref.focus(); await ref.press('ArrowLeft');
    await right.dispatchEvent('click'); await left.dispatchEvent('click'); await right.dispatchEvent('click');
    await expect(right).toHaveAttribute('aria-pressed', 'true');
    await expect(ref).toHaveValue('3499.4');
    await expect(readout).toContainText('Calculated readout unavailable.');
    await expect(readout.getByText('Signed distance')).toHaveCount(0);
    await page.unroute('**/__scopehub/workspace');
    await button(readout, 'Retry calculation').click();
    await expect(readout.getByText('+0.4', { exact: true })).toBeVisible();
    await expect(resultHash).toHaveText(firstHash);

    expect(calls).toContain('indicator.createDraft');
    expect(calls).toContain('indicator.runPreview');
    expect(calls).not.toContain('indicator.acceptVersion');
    expect(calls).not.toContain('confirmation.respond');
    expect(calls).not.toContain('ai.textConnection');
    expect(persistedRows(profile)).toEqual(before);
  }));
});

test('an unconfigured workspace can inspect raw observations and reports unavailable calculated values', async ({ browser }) => {
  await withProfile(profile => withWorkspace(browser, profile, async page => {
    await button(page, 'Open blank AI workbench').click(); await button(page, 'Drawing studio').click();
    const studio = page.getByRole('dialog', { name: 'Drawing studio' });
    await studio.getByLabel('Open local drawing program').setInputFiles(file('interactive-diamonds'));
    await button(studio, 'Inspect observation at 21:00').click();
    await expect(studio.getByText('3,499.2 CNY')).toBeVisible();
    await expect(studio.getByText(/Calculated readout unavailable/)).toBeVisible();
    await expect(studio.getByText('Signed distance')).toHaveCount(0);
    await button(studio, 'Accept view').click();
    for (const invalid of ['// scalar size 0 10 0 4\ncircle(1,1,1,0);', 'hit(2,1,1,4);circle(1,1,1,0);']) {
      await studio.getByLabel('Program source').fill('// future free\n' + invalid);
      await button(studio, 'Preview draft').click();
      await expect(studio.getByRole('alert')).toBeVisible();
      await expect(button(studio, 'Accept view')).toHaveCount(0);
      await button(studio, 'Restore accepted view').click();
      await expect(studio.getByText('3,499.2 CNY')).toBeVisible();
    }
  }));
});
