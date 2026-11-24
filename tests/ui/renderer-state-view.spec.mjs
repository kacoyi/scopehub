import { readFileSync } from 'node:fs';
import { expect, test } from '@playwright/test';

const fixture = JSON.parse(readFileSync(new URL('../fixtures/workbench-qualification-v1.json', import.meta.url), 'utf8'));
const strengths = fixture.cases['unconventional-indicator'].strength_by_bar;
const authored = '{ "version": 1,\n  "blocks": [{ "kind": "candlestick", "source": "qualification-equity",'
  + ' "annotation": { "field": "turnover", "when": { "field": "strength", "above": 0.8 } } }] }';
const [candleBlock] = JSON.parse(authored).blocks;
const defaultLights = {
  ...candleBlock,
  presentation: {
    kind: 'state-lights', ranges: [{ below: 0.4, tone: 'green' }, { below: 0.8, tone: 'amber' }], otherwise: { tone: 'red' },
  },
};

async function openEditor(page) {
  await page.goto('/');
  await page.getByRole('button', { name: 'Open workbench prototype' }).click();
  const draft = page.getByRole('textbox', { name: 'Composition JSON' });
  const preview = page.getByRole('button', { name: 'Preview draft' });
  const accept = page.getByRole('button', { name: 'Accept view' });
  const presentation = page.getByRole('combobox', { name: 'Block 1 presentation' });
  const candles = page.getByRole('region', { name: 'Annotated candlesticks' });
  const lights = page.getByRole('region', { name: 'State lights' });
  const result = lights.getByRole('region', { name: 'Selected result' });
  await draft.fill(authored);
  await preview.click();
  await candles.getByRole('button', { name: '15 min' }).click();
  await accept.click();
  return { draft, preview, accept, presentation, candles, lights, result };
}

test('switches to chart-free state lights and edits a range over the same frozen result', async ({ page }) => {
  const { draft, preview, accept, presentation, candles, lights, result } = await openEditor(page);
  const chart = candles.getByRole('img', { name: 'Candlestick chart with numeric annotations' });
  await candles.getByText('Synthetic result attribution').click();
  await expect(candles.getByRole('row', { name: '2024-01-04T09:30:00.000Z' }).getByRole('cell').first()).toHaveText('0.9');
  await expect(candles.getByText('qualification-equity-15m-results-v1', { exact: true })).toBeVisible();
  const dataRequests = [];
  page.on('request', request => {
    if (['fetch', 'xhr'].includes(request.resourceType())) dataRequests.push(request.url());
  });

  await presentation.selectOption('state-lights');
  await expect(chart).toBeVisible();
  await preview.click();
  await expect(candles).toHaveCount(0);
  await expect(page.getByRole('region', { name: 'Workbench canvas' }).locator('canvas')).toHaveCount(0);
  await expect(page.getByText('Frozen synthetic closes', { exact: false })).toHaveCount(0);
  await expect(lights.getByRole('button', { name: '15 min' })).toHaveAttribute('aria-pressed', 'true');
  const bars = lights.getByRole('button', { name: /UTC/ });
  await expect(bars).toHaveCount(1);
  await lights.getByRole('button', { name: '09:30 UTC Red' }).press('Space');
  await expect(result.locator('dd')).toHaveText([
    '2024-01-04T09:30:00.000Z', 'qualification-equity', 'qualification-equity-15m-bars-v1',
    'qualification-equity-15m-results-v1', '0.9',
  ]);

  await lights.getByRole('button', { name: '5 min', exact: true }).click();
  await accept.click();
  await expect(bars).toHaveCount(4);
  const labels = ['Green', 'Amber', 'Red', 'Unknown'];
  const colors = ['rgb(98, 214, 163)', 'rgb(245, 195, 112)', 'rgb(255, 147, 147)', 'rgb(169, 178, 193)'];
  for (const [index, strength] of strengths.entries()) {
    const bar = bars.nth(index);
    await expect(bar).toHaveAccessibleName(`09:${30 + index * 5} UTC ${labels[index]}`);
    await expect(bar.locator('[data-tone]')).toHaveCSS('background-color', colors[index]);
    await bar.click();
    await expect(bar).toHaveAttribute('aria-pressed', 'true');
    await expect(result.locator('time')).toHaveAttribute('datetime', `2024-01-04T09:${30 + index * 5}:00.000Z`);
    await expect(result.locator('dd').last()).toHaveText(strength === null ? 'Unknown' : String(strength));
    await expect(result.getByText('qualification-equity-5m-results-v1', { exact: true })).toBeVisible();
  }

  await lights.getByRole('button', { name: '09:35 UTC Amber' }).click();
  const ruleEdit = JSON.parse(await draft.inputValue());
  ruleEdit.blocks[0].presentation.ranges[1].below = 0.5;
  ruleEdit.blocks[0].presentation.otherwise.label = 'Review this bar';
  await draft.fill(JSON.stringify(ruleEdit));
  await preview.click();
  const reclassified = lights.getByRole('button', { name: '09:35 UTC Review this bar' });
  await expect(reclassified).toHaveAttribute('aria-pressed', 'true');
  await expect(result.locator('dd').last()).toHaveText('0.5');
  await expect(lights.getByRole('button', { name: '09:45 UTC Unknown' })).toBeVisible();
  await accept.click();

  await presentation.selectOption('candlesticks');
  await preview.click();
  await expect(chart).toBeVisible();
  await expect(candles.getByRole('button', { name: '5 min', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await accept.click();
  await presentation.selectOption('state-lights');
  await preview.click();
  await expect(reclassified).toHaveAttribute('aria-pressed', 'true');

  // The same mapping surface also accepts another granted field and a different number of ranges.
  const otherField = JSON.parse(await draft.inputValue());
  otherField.blocks[0].annotation.when.field = 'turnover';
  otherField.blocks[0].presentation.ranges = [{ below: 0.011, tone: 'neutral', label: 'Low turnover' }];
  await draft.fill(JSON.stringify(otherField));
  await preview.click();
  await expect(lights.getByRole('button', { name: '09:30 UTC Low turnover' })).toBeVisible();
  await expect(reclassified).toHaveAttribute('aria-pressed', 'false');
  await expect(result.locator('dd')).toHaveCount(0);
  await reclassified.click();
  await expect(reclassified).toHaveAttribute('aria-pressed', 'true');
  await expect(result.locator('dd').last()).toHaveText('0.011');
  expect(dataRequests).toEqual([]);
});

test('rejects invalid state ranges and restores the accepted binding and author text', async ({ page }) => {
  const { draft, preview, accept, presentation, lights, result } = await openEditor(page);
  await presentation.selectOption('state-lights');
  await preview.click();
  await lights.getByRole('button', { name: '09:30 UTC Red' }).click();
  await accept.click();
  const accepted = await draft.inputValue();

  await draft.fill(accepted.replace('"below": 0.8', '"below": 0.92'));
  await preview.click();
  await expect(lights.getByRole('button', { name: '15 min' })).toHaveAttribute('aria-pressed', 'true');
  await expect(lights.getByRole('button', { name: '09:30 UTC Amber' })).toHaveAttribute('aria-pressed', 'true');
  await expect(result.locator('dd').last()).toHaveText('0.9');

  await draft.fill('{"version":1,"blocks":[]}');
  await preview.click();
  await expect(lights).toHaveCount(0);
  await draft.fill(accepted.replace('"below": 0.4', '"below": 0.8'));
  await preview.click();
  await expect(page.getByRole('alert')).toHaveText('Block 1 needs 1 to 4 strictly increasing finite limits and valid states.');
  await expect(accept).toBeDisabled();
  await expect(lights.getByRole('button', { name: '15 min' })).toHaveAttribute('aria-pressed', 'true');
  await expect(lights.getByRole('button', { name: '09:30 UTC Red' })).toHaveAttribute('aria-pressed', 'true');
  await expect(result.getByText('qualification-equity-15m-results-v1', { exact: true })).toBeVisible();
  await expect(result.locator('dd').last()).toHaveText('0.9');
  await page.getByRole('button', { name: 'Restore accepted view' }).click();
  await expect(draft).toHaveValue(accepted);
  await expect(page.getByRole('alert')).toHaveCount(0);

  await page.getByRole('dialog', { name: 'Workbench prototype' }).getByRole('button', { name: 'Language settings' }).click();
  await page.getByRole('dialog', { name: 'Language settings' }).getByRole('combobox').selectOption('zh-Hans');
  await page.getByRole('button', { name: '关闭', exact: true }).click();
  await page.setViewportSize({ width: 380, height: 800 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const localized = page.getByRole('region', { name: '状态灯' });
  await expect(localized.getByRole('button', { name: '09:30 UTC 红色' }).locator('[data-tone]')).toHaveCSS('animation-name', 'none');
  await localized.getByRole('button', { name: '5 分钟', exact: true }).click();
  await localized.getByRole('button', { name: '09:45 UTC 未知' }).click();
  await expect(localized.getByRole('region', { name: '选中的结果' }).getByText('未知', { exact: true })).toBeVisible();
  await expect(page.getByRole('dialog', { name: '工作台原型' })).toHaveJSProperty('scrollWidth', 380);
});

test('keeps the original binding when switching presentation and appending before preview', async ({ page }) => {
  const { draft, preview, accept, presentation, candles, lights, result } = await openEditor(page);
  await presentation.selectOption('state-lights');
  await page.getByRole('button', { name: 'Add candlesticks' }).click();
  await preview.click();
  await expect(lights.getByRole('button', { name: '15 min' })).toHaveAttribute('aria-pressed', 'true');
  await expect(candles.getByRole('button', { name: '5 min', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await lights.getByRole('button', { name: '09:30 UTC Red' }).click();
  await expect(result.locator('dd')).toHaveText([
    '2024-01-04T09:30:00.000Z', 'qualification-equity', 'qualification-equity-15m-bars-v1',
    'qualification-equity-15m-results-v1', '0.9',
  ]);
  await accept.click();

  const reordered = JSON.parse(await draft.inputValue());
  reordered.blocks.reverse();
  await draft.fill(JSON.stringify(reordered));
  await preview.click();
  await expect(page.getByRole('region', { name: 'Workbench canvas' }).getByRole('heading', { level: 3 }))
    .toHaveText(['Annotated candlesticks', 'State lights']);
  await expect(candles.getByRole('button', { name: '5 min', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(lights.getByRole('button', { name: '15 min' })).toHaveAttribute('aria-pressed', 'true');
  await expect(result.locator('dd').last()).toHaveText('0.9');
});

test('scopes inspection to the selected timeframe and result revision at a shared UTC bar', async ({ page }) => {
  const { preview, accept, presentation, lights, result } = await openEditor(page);
  await presentation.selectOption('state-lights');
  await preview.click();
  await lights.getByRole('button', { name: '5 min', exact: true }).click();
  const five = lights.getByRole('button', { name: '09:30 UTC Green' });
  const fifteen = lights.getByRole('button', { name: '09:30 UTC Red' });
  await five.click();
  await accept.click();
  await expect(result.getByText('qualification-equity-5m-results-v1', { exact: true })).toBeVisible();
  await expect(result.locator('dd').last()).toHaveText('0.15');

  await lights.getByRole('button', { name: '15 min' }).click();
  await expect(fifteen).toHaveAttribute('aria-pressed', 'false');
  await expect(result.locator('dd')).toHaveCount(0);
  await lights.getByRole('button', { name: '5 min', exact: true }).click();
  await expect(five).toHaveAttribute('aria-pressed', 'true');
  await expect(result.locator('dd').last()).toHaveText('0.15');

  await lights.getByRole('button', { name: '15 min' }).click();
  await fifteen.click();
  await expect(result.locator('dd')).toHaveText([
    '2024-01-04T09:30:00.000Z', 'qualification-equity', 'qualification-equity-15m-bars-v1',
    'qualification-equity-15m-results-v1', '0.9',
  ]);
  await lights.getByRole('button', { name: '5 min', exact: true }).click();
  await expect(five).toHaveAttribute('aria-pressed', 'false');
  await expect(result.locator('dd')).toHaveCount(0);
});

test('switches presentation at the JSON size limit using compact authoring when needed', async ({ page }) => {
  const { draft, preview, accept, candles, lights } = await openEditor(page);
  const switchLast = index => page.getByRole('combobox', { name: `Block ${index} presentation` }).selectOption('state-lights');
  const restore = page.getByRole('button', { name: 'Restore accepted view' });
  const limit = 4096;
  const mixed = { version: 1, blocks: [...Array(7).fill(defaultLights), candleBlock] };
  const mixedText = JSON.stringify(mixed, null, 2);
  expect(mixedText.length).toBeLessThan(limit);
  expect(JSON.stringify({ version: 1, blocks: Array(8).fill(defaultLights) }, null, 2).length).toBeGreaterThan(limit);
  await draft.fill(mixedText);
  await preview.click();
  await accept.click();
  await switchLast(8);
  await expect(page.getByRole('alert')).toHaveCount(0);
  const compact = await draft.inputValue();
  expect(compact.length).toBeLessThanOrEqual(limit);
  expect(JSON.parse(compact)).toEqual({ version: 1, blocks: Array(8).fill(defaultLights) });
  await preview.click();
  await expect(lights).toHaveCount(8);
  await restore.click();
  await expect(draft).toHaveValue(mixedText);
  await expect(candles).toHaveCount(1);

  const labeled = length => {
    const block = structuredClone(defaultLights);
    for (const style of [...block.presentation.ranges, block.presentation.otherwise]) style.label = 'a'.repeat(length);
    return block;
  };
  const largeStates = [...Array(8).fill(labeled(48)), labeled(18)];
  const largeText = `{ "version": 1,\n "blocks": ${JSON.stringify([...largeStates, candleBlock])}\n}`;
  expect(largeText.length).toBeLessThan(limit);
  expect(JSON.stringify({ version: 1, blocks: [...largeStates, defaultLights] }).length).toBeGreaterThan(limit);
  await draft.fill(largeText);
  await preview.click();
  await accept.click();
  await switchLast(10);
  await expect(page.getByRole('alert')).toHaveText('Keep the draft within 4096 characters.');
  await expect(accept).toBeDisabled();
  await expect(candles).toHaveCount(1);
  await expect(draft).toHaveValue(largeText);
  await restore.click();
  await expect(draft).toHaveValue(largeText);
  await expect(page.getByRole('alert')).toHaveCount(0);
});
