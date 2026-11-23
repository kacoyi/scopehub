import { expect, test } from '@playwright/test';

test('compose from blank, preview a new arrangement, and recover the accepted frozen series', async ({ page }) => {
  await page.goto('/');
  const open = page.getByRole('button', { name: 'Open workbench prototype' });
  await open.click();
  await expect(page.getByRole('heading', { name: 'Workbench prototype' })).toBeVisible();
  const canvas = page.getByRole('region', { name: 'Workbench canvas' });
  const draft = page.getByRole('textbox', { name: 'Composition JSON' });
  const preview = page.getByRole('button', { name: 'Preview draft' });
  const accept = page.getByRole('button', { name: 'Accept view' });
  const restore = page.getByRole('button', { name: 'Restore accepted view' });
  await canvas.getByText('Frozen synthetic closes', { exact: false }).click();
  await expect(canvas.getByText('3a1b0032b57711c9563131caf34c8cfda9d91bbf5ef5e9dbc7a4f0f48ab390d1')).toBeVisible();
  const table = canvas.getByRole('table');
  const tiles = canvas.getByRole('button', { name: /3499\.[28]/ });
  await expect(tiles).toHaveCount(0);
  await expect(table).toHaveCount(0);

  await page.getByRole('button', { name: 'Add threshold tiles' }).click();
  await page.getByRole('button', { name: 'Add value table' }).click();
  await expect(table).toHaveCount(0);
  await preview.click();
  await expect(tiles).toHaveCount(2);
  await expect(tiles.nth(0)).toHaveAccessibleName(/21:00.*3499\.2.*Below/);
  await expect(tiles.nth(1)).toHaveAccessibleName(/21:01.*3499\.8.*At or above/);
  await expect(table.getByRole('cell', { name: '3499.2', exact: true })).toBeVisible();
  await expect(table.getByRole('cell', { name: '3499.8', exact: true })).toBeVisible();
  const firstTime = table.getByRole('button', { name: '21:00' });
  const secondTime = table.getByRole('button', { name: '21:01' });
  await tiles.nth(1).click();
  await expect(secondTime).toHaveAttribute('aria-pressed', 'true');
  await firstTime.click();
  await expect(tiles.nth(0)).toHaveAttribute('aria-pressed', 'true');
  await expect(secondTime).toHaveAttribute('aria-pressed', 'false');
  await accept.click();
  const accepted = JSON.parse(await draft.inputValue());

  // Reclassify one frozen close by changing only its visual encoding.
  await draft.fill(JSON.stringify({ version: 1, blocks: [
    { kind: 'threshold-tiles', threshold: 3500, belowLabel: 'Under my target', aboveLabel: 'Target reached' },
  ] }));
  await preview.click();
  await expect(tiles.nth(1)).toHaveAccessibleName(/21:01.*3499\.8.*Under my target/);
  await expect(table).toHaveCount(0);

  // Keep a valid, distinct preview on screen before submitting a rejected edit.
  await draft.fill(JSON.stringify({ version: 1, blocks: [{ kind: 'value-table' }] }));
  await preview.click();
  await expect(tiles).toHaveCount(0);
  await expect(table).toBeVisible();
  await draft.fill(JSON.stringify({ version: 1, blocks: [{ kind: 'unsupported' }] }));
  await preview.click();
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(accept).toBeDisabled();
  await expect(tiles).toHaveCount(2);
  await expect(table).toBeVisible();

  await expect(tiles.nth(1)).toHaveAccessibleName(/21:01.*3499\.8.*At or above/);
  await restore.click();
  expect(JSON.parse(await draft.inputValue())).toEqual(accepted);
  await expect(page.getByRole('alert')).toHaveCount(0);
  await page.getByRole('button', { name: 'Back to chart' }).click();
  await expect(open).toBeVisible();
});

test('restore preserves the accepted author text including formatting', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Open workbench prototype' }).click();
  const draft = page.getByRole('textbox', { name: 'Composition JSON' });
  const authored = '{ "version": 1, "blocks": [{"kind":"value-table"}] }';
  await draft.fill(authored);
  await page.getByRole('button', { name: 'Preview draft' }).click();
  await page.getByRole('button', { name: 'Accept view' }).click();
  await draft.fill('{');
  await page.getByRole('button', { name: 'Preview draft' }).click();
  await expect(page.getByRole('alert')).toBeVisible();
  await page.getByRole('button', { name: 'Restore accepted view' }).click();
  await expect(draft).toHaveValue(authored);
});
