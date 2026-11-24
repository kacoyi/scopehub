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

test('reopens the accepted chart-free workbench without promoting drafts', async ({ page }, testInfo) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Open offline sample' }).click();
  const timeframe = page.getByRole('button', { name: '5m', exact: true });
  const style = page.getByRole('button', { name: 'Close-price line' });
  await timeframe.click();
  await style.click();

  const open = page.getByRole('button', { name: 'Open workbench prototype' });
  const workbench = page.getByRole('dialog', { name: 'Workbench prototype' });
  const canvas = workbench.getByRole('region', { name: 'Workbench canvas' });
  const draft = workbench.getByRole('textbox', { name: 'Composition JSON' });
  const back = workbench.getByRole('button', { name: 'Back to chart' });
  const preview = workbench.getByRole('button', { name: 'Preview draft' });
  const accept = workbench.getByRole('button', { name: 'Accept view' });
  const table = canvas.getByRole('table');
  const first = table.getByRole('button', { name: '21:00' });
  const second = table.getByRole('button', { name: '21:01' });
  const firstTile = canvas.getByRole('button', { name: /21:00.*3499\.2/ });
  const secondTile = canvas.getByRole('button', { name: /21:01.*3499\.8/ });
  const authored = '{ "version": 1, "blocks": [{"kind":"value-table"}, {"kind":"threshold-tiles","threshold":3499.5}] }';
  const revisedDraft = '{ "version": 1, "blocks": [{"kind":"value-table"}] }';

  await open.click();
  await expect(canvas.getByText('The canvas is empty. Add a block and preview your draft.')).toBeVisible();
  await draft.fill(authored);
  await preview.click();
  await accept.click();
  await second.click();
  await expect(secondTile).toHaveAttribute('aria-pressed', 'true');
  await back.click();
  await expect(workbench).toHaveCount(0);
  await expect(timeframe).toHaveAttribute('aria-pressed', 'true');
  await expect(style).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByRole('status').filter({ hasText: 'Offline sample' })).toContainText('Simulated');

  // Measure the first frame callback with the visible accepted value and an enabled selected row.
  await open.evaluate(button => {
    button.addEventListener('click', () => {
      const started = performance.now();
      window.firstVisibleWorkbenchResult = new Promise(resolve => {
        function observe() {
          const row = document.querySelector('dialog[open] .probe-canvas tr[data-selected="true"]');
          const control = row?.querySelector('button[aria-pressed="true"]');
          if (row?.textContent?.includes('3499.8') && row.checkVisibility() && control && !control.disabled) {
            resolve(performance.now() - started);
          } else {
            requestAnimationFrame(observe);
          }
        }
        requestAnimationFrame(observe);
      });
    }, { capture: true, once: true });
  });
  await open.click();
  await expect(draft).toHaveValue(authored);
  await expect(canvas.getByRole('heading')).toHaveText(['Workbench canvas', 'Value table', 'Threshold tiles']);
  await expect(second).toHaveAttribute('aria-pressed', 'true');
  await expect(secondTile).toHaveAttribute('aria-pressed', 'true');
  const reopenMs = await page.evaluate(() => window.firstVisibleWorkbenchResult);
  await testInfo.attach('first visible accepted result after same-window reopen', {
    body: JSON.stringify({ milliseconds: reopenMs, method: 'click to first Chromium frame callback with visible selected result and enabled control' }),
    contentType: 'application/json',
  });
  await testInfo.attach('reopened accepted view', { body: await page.screenshot(), contentType: 'image/png' });

  await canvas.getByText('Frozen synthetic closes', { exact: false }).click();
  await expect(canvas.getByText('scopehub-synthetic-v1:20241013:normal', { exact: true })).toBeVisible();
  await expect(canvas.getByText('3a1b0032b57711c9563131caf34c8cfda9d91bbf5ef5e9dbc7a4f0f48ab390d1')).toBeVisible();
  await first.click();
  await expect(firstTile).toHaveAttribute('aria-pressed', 'true');

  await draft.fill(revisedDraft);
  await preview.click();
  await expect(secondTile).toHaveCount(0);
  await second.click();
  await expect(second).toHaveAttribute('aria-pressed', 'true');
  await back.click();
  await open.click();
  await expect(draft).toHaveValue(authored);
  await expect(firstTile).toHaveAttribute('aria-pressed', 'true');

  await draft.fill(revisedDraft);
  await preview.click();
  await second.click();
  await draft.fill('{');
  await preview.click();
  await expect(workbench.getByRole('alert')).toBeVisible();
  await expect(accept).toBeDisabled();
  await back.click();
  await open.click();
  await expect(draft).toHaveValue(authored);
  await expect(workbench.getByRole('alert')).toHaveCount(0);
  await expect(first).toHaveAttribute('aria-pressed', 'true');
  await expect(firstTile).toHaveAttribute('aria-pressed', 'true');

  const freshWindow = await page.context().newPage();
  try {
    await freshWindow.goto('/');
    await freshWindow.getByRole('button', { name: 'Open workbench prototype' }).click();
    await expect(freshWindow.getByText('The canvas is empty. Add a block and preview your draft.')).toBeVisible();
  } finally {
    await freshWindow.close();
  }

  await draft.fill('{ "version": 1, "blocks": [] }');
  await preview.click();
  await accept.click();
  await back.click();
  await open.click();
  await expect(canvas.getByText('The canvas is empty. Add a block and preview your draft.')).toBeVisible();
  await workbench.getByRole('button', { name: 'Add value table' }).click();
  await preview.click();
  await expect(first).toHaveAttribute('aria-pressed', 'false');
  await expect(second).toHaveAttribute('aria-pressed', 'false');
});
