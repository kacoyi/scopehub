import { readFileSync } from 'node:fs';
import { setTimeout as delay } from 'node:timers/promises';
import { expect, test } from '@playwright/test';

const fixture = JSON.parse(readFileSync(new URL('../fixtures/workbench-qualification-v1.json', import.meta.url), 'utf8'));
const numericCase = fixture.cases['unconventional-indicator'];
const annotationBlock = {
  kind: 'candlestick', source: 'qualification-equity',
  annotation: { field: 'turnover', when: { field: 'strength', above: 0.8 } },
};

test('offers an editable candle binding and scopes the source disclosures to their blocks', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Open workbench prototype' }).click();
  const canvas = page.getByRole('region', { name: 'Workbench canvas' });
  const draft = page.getByRole('textbox', { name: 'Composition JSON' });
  const preview = page.getByRole('button', { name: 'Preview draft' });
  const block = canvas.getByRole('region', { name: 'Annotated candlesticks' });
  const legacy = canvas.getByText('SIM.CN.FUTURE.001', { exact: false });
  await expect(legacy).toHaveText('Frozen synthetic closes · SIM.CN.FUTURE.001');

  const chartOnly = { version: 1, blocks: [annotationBlock] };
  await draft.fill(JSON.stringify(chartOnly));
  await preview.click();
  await expect(block).toBeVisible();
  await expect(legacy).toHaveCount(0);
  await block.getByText('Synthetic result attribution').click();
  await expect(block.getByText('qualification-equity', { exact: true })).toBeVisible();
  await expect(block.getByText('qualification-equity-5m-bars-v1', { exact: true })).toBeVisible();

  await page.getByRole('button', { name: 'Restore accepted view' }).click();
  await page.getByRole('button', { name: 'Add candlesticks' }).click();
  expect(JSON.parse(await draft.inputValue())).toEqual(chartOnly);
  await expect(block).toHaveCount(0);
  await page.getByRole('button', { name: 'Add value table' }).click();
  await preview.click();
  await expect(legacy).toHaveText('Frozen synthetic closes for tiles and tables · SIM.CN.FUTURE.001');
  await legacy.click();
  await expect(canvas.getByText('3a1b0032b57711c9563131caf34c8cfda9d91bbf5ef5e9dbc7a4f0f48ab390d1')).toBeVisible();
  await expect(canvas.getByRole('heading', { name: 'Value table' })).toBeVisible();
  await block.getByText('Synthetic result attribution').click();
  await expect(block.getByText('qualification-equity-5m-bars-v1', { exact: true })).toBeVisible();
  await expect(block.getByText('SIM.CN.FUTURE.001', { exact: false })).toHaveCount(0);
});

test('restores the accepted fifteen-minute binding after preview removal and grant rejection', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Open workbench prototype' }).click();
  const draft = page.getByRole('textbox', { name: 'Composition JSON' });
  const preview = page.getByRole('button', { name: 'Preview draft' });
  const accept = page.getByRole('button', { name: 'Accept view' });
  const restore = page.getByRole('button', { name: 'Restore accepted view' });
  const block = page.getByRole('region', { name: 'Annotated candlesticks' });
  const chart = block.getByRole('img', { name: 'Candlestick chart with numeric annotations' });
  const authored = `{ "version": 1,\n  "blocks": [${JSON.stringify(annotationBlock)}] }`;
  const invalid = authored.replace('qualification-equity', 'configured-market-data');

  await draft.fill(authored);
  await preview.click();
  await block.getByRole('button', { name: '15 min', exact: true }).click();
  await preview.click();
  await expectTurnover(block, '15m');
  await accept.click();

  await draft.fill('{"version":1,"blocks":[]}');
  await preview.click();
  await expect(page.getByText('The canvas is empty. Add a block and preview your draft.')).toBeVisible();
  await expect(chart).toHaveCount(0);
  await draft.fill(invalid);
  await preview.click();
  await expect(page.getByRole('alert')).toHaveText('Block 1 must bind granted synthetic fields and a finite threshold.');
  await expect(accept).toBeDisabled();
  await expectTurnover(block, '15m');
  await restore.click();
  await expect(draft).toHaveValue(authored);
  await expect(page.getByRole('alert')).toHaveCount(0);
  await expectTurnover(block, '15m');
  await expect.poll(async () => {
    const { candles, bubbles } = await paintedGeometry(chart);
    return [candles.length, bubbles.length];
  }).toEqual([1, 1]);

  // Editing another block, including one at the former candle position, cannot overwrite the accepted timeframe.
  const other = { ...annotationBlock, annotation: { ...annotationBlock.annotation, field: 'strength' } };
  await draft.fill(JSON.stringify({ version: 1, blocks: [other, annotationBlock] }));
  await preview.click();
  await expect(block).toHaveCount(2);
  await expect(block.nth(0).getByRole('button', { name: '5 min', exact: true })).toHaveAttribute('aria-pressed', 'true');
  const previewPeriod = block.nth(1).getByRole('button', { name: '15 min', exact: true });
  await expect(previewPeriod).toHaveAttribute('aria-pressed', 'true');
  await block.nth(1).getByRole('button', { name: '5 min', exact: true }).click();
  await expect(previewPeriod).toHaveAttribute('aria-pressed', 'false');
  await block.nth(0).getByRole('button', { name: '15 min', exact: true }).click();
  await expect(previewPeriod).toHaveAttribute('aria-pressed', 'false');
  await draft.fill(invalid);
  await preview.click();
  await expect(page.getByRole('alert')).toBeVisible();
  await restore.click();
  await expect(draft).toHaveValue(authored);
  await expectTurnover(block, '15m');
});

async function expectAttribution(block, period) {
  const minutes = period === '15m' ? 15 : 5;
  const bar = period === '15m' ? '09:30' : '09:40';
  await expect(block.getByRole('button', { name: `${minutes} min`, exact: true })).toHaveAttribute('aria-pressed', 'true');
  if (await block.locator('details[open]').count() === 0) await block.getByText('Synthetic result attribution').click();
  await expect(block.getByText(`qualification-equity-${period}-bars-v1`, { exact: true })).toBeVisible();
  await expect(block.getByText(`qualification-equity-${period}-results-v1`, { exact: true })).toBeVisible();
  return block.getByRole('row', { name: `2024-01-04T${bar}:00.000Z`, exact: true });
}

async function expectTurnover(block, period) {
  const row = await expectAttribution(block, period);
  const value = period === '15m' ? '4.7%' : '3.2%';
  await expect(row.getByRole('cell', { name: value, exact: true })).toBeVisible();
  await expect(block.getByText(period === '15m' ? '3.2%' : '4.7%', { exact: true })).toHaveCount(0);
}

test.describe('candlestick editor identity', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: 'Open workbench prototype' }).click();
    await page.getByRole('button', { name: 'Add candlesticks' }).click();
    await page.getByRole('button', { name: 'Preview draft' }).click();
    const block = page.getByRole('region', { name: 'Annotated candlesticks' });
    await block.getByRole('button', { name: '15 min', exact: true }).click();
    await expectTurnover(block, '15m');
    await page.getByRole('button', { name: 'Accept view' }).click();
  });

  test('keeps the accepted binding when appending and editing duplicate candles', async ({ page }) => {
    const draft = page.getByRole('textbox', { name: 'Composition JSON' });
    const preview = page.getByRole('button', { name: 'Preview draft' });
    const accept = page.getByRole('button', { name: 'Accept view' });
    const blocks = page.getByRole('region', { name: 'Annotated candlesticks' });
    const chart = block => block.getByRole('img', { name: 'Candlestick chart with numeric annotations' });
    const originalChart = await chart(blocks).elementHandle();
    await page.getByRole('button', { name: 'Add candlesticks' }).click();
    await preview.click();
    await expect(blocks).toHaveCount(2);
    await expectTurnover(blocks.nth(0), '15m');
    await expectTurnover(blocks.nth(1), '5m');
    await accept.click();

    // An unchanged duplicate in its existing occurrence keeps its own timeframe.
    const edited = { ...annotationBlock, annotation: { ...annotationBlock.annotation, when: { field: 'strength', above: 0.85 } } };
    await draft.fill(JSON.stringify({ version: 1, blocks: [edited, annotationBlock] }));
    await preview.click();
    await expectTurnover(blocks.nth(0), '15m');
    await expectTurnover(blocks.nth(1), '5m');
    await accept.click();

    // Once the definitions differ, the two chart instances follow their definitions when reordered.
    const reordered = JSON.stringify({ version: 1, blocks: [annotationBlock, edited] }, null, 2);
    await draft.fill(reordered);
    await preview.click();
    await expectTurnover(blocks.nth(0), '5m');
    await expectTurnover(blocks.nth(1), '15m');
    expect(await chart(blocks.nth(1)).evaluate((element, original) => element === original, originalChart)).toBe(true);
    await accept.click();

    await draft.fill(reordered.replace('qualification-equity', 'configured-market-data'));
    await preview.click();
    await expect(page.getByRole('alert')).toHaveText('Block 1 must bind granted synthetic fields and a finite threshold.');
    await expect(accept).toBeDisabled();
    await page.getByRole('button', { name: 'Restore accepted view' }).click();
    await expect(draft).toHaveValue(reordered);
    await expectTurnover(blocks.nth(0), '5m');
    await expectTurnover(blocks.nth(1), '15m');
  });

  test('keeps fifteen-minute attribution when accepting a threshold-only edit', async ({ page }) => {
    const draft = page.getByRole('textbox', { name: 'Composition JSON' });
    const preview = page.getByRole('button', { name: 'Preview draft' });
    const block = page.getByRole('region', { name: 'Annotated candlesticks' });
    const original = await draft.inputValue();
    const edited = original.replace('"above": 0.8', '"above": 0.85');
    await draft.fill(edited);
    await preview.click();
    await expectTurnover(block, '15m');
    await page.getByRole('button', { name: 'Accept view' }).click();
    await expect(draft).toHaveValue(edited);
    await expectTurnover(block, '15m');
  });

  for (const { field, original, replacement, value } of [
    { field: 'plotted', original: 'turnover', replacement: 'strength', value: '0.9' },
    { field: 'condition', original: 'strength', replacement: 'turnover', value: '—' },
  ]) {
    test(`keeps fifteen-minute attribution when rebinding the ${field} field in place`, async ({ page }) => {
      const draft = page.getByRole('textbox', { name: 'Composition JSON' });
      const block = page.getByRole('region', { name: 'Annotated candlesticks' });
      const edited = (await draft.inputValue()).replace(`"field": "${original}"`, `"field": "${replacement}"`);
      await draft.fill(edited);
      await page.getByRole('button', { name: 'Preview draft' }).click();
      const row = await expectAttribution(block, '15m');
      await expect(row.getByRole('cell')).toHaveText(['0.9', '0.047', value]);
      await page.getByRole('button', { name: 'Accept view' }).click();
      await expect(draft).toHaveValue(edited);
      await expect(row.getByRole('cell')).toHaveText(['0.9', '0.047', value]);
    });
  }
});

async function paintedGeometry(chart) {
  // Infer geometry from actual ink on the price pane, independently of the chart API and attribution table.
  return chart.locator('canvas').evaluateAll(canvases => {
    function groups(columns, minimum) {
      const ranges = [];
      for (let x = 0; x < columns.length; x++) {
        if (columns[x] < minimum) continue;
        const last = ranges.at(-1);
        if (last && x === last.end + 1) last.end = x;
        else ranges.push({ start: x, end: x });
      }
      return ranges;
    }

    for (const canvas of canvases) {
      const { width, height } = canvas;
      if (!width || !height) continue;
      const bounds = canvas.getBoundingClientRect();
      const scaleX = width / bounds.width;
      const scaleY = height / bounds.height;
      const data = canvas.getContext('2d').getImageData(0, 0, width, height).data;
      const candleColumns = new Uint32Array(width);
      const risingColumns = new Uint32Array(width);
      const candleTop = new Int32Array(width).fill(height);
      const bubbleColumns = new Uint32Array(width);
      const bubbleBottom = new Int32Array(width).fill(-1);
      for (let y = 0, i = 0; y < height; y++) {
        for (let x = 0; x < width; x++, i += 4) {
          const r = data[i], g = data[i + 1], b = data[i + 2];
          if ((r === 67 && g === 212 && b === 172) || (r === 241 && g === 125 && b === 133)) {
            candleColumns[x]++;
            if (r === 67) risingColumns[x]++;
            candleTop[x] = Math.min(candleTop[x], y);
          }
          if (r > 130 && g > 90 && b > r + 33 && b > g + 65) {
            bubbleColumns[x]++;
            bubbleBottom[x] = y;
          }
        }
      }
      const candles = groups(candleColumns, 8 * scaleY).map(({ start, end }) => {
        const x = (start + end) / 2;
        return { x: (x + 0.5) / scaleX, wickTop: candleTop[Math.round(x)] / scaleY,
          direction: risingColumns[Math.round(x)] ? 'up' : 'down' };
      });
      if (!candles.length) continue;
      const bubbles = groups(bubbleColumns, 1).map(({ start, end }) => {
        const columns = bubbleBottom.slice(start, end + 1);
        const y = Math.max(...columns);
        const x = start + (columns.indexOf(y) + columns.lastIndexOf(y)) / 2;
        return { x: (x + 0.5) / scaleX, leaderBottom: (y + 1) / scaleY };
      });
      return { width: bounds.width, candles, bubbles, scale: scaleX };
    }
    return { width: 0, candles: [], bubbles: [], scale: 0 };
  });
}

test('authored result annotations paint on their candles across viewport and timeframe changes', async ({ page }, testInfo) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Open workbench prototype' }).click();
  const draft = page.getByRole('textbox', { name: 'Composition JSON' });
  const preview = page.getByRole('button', { name: 'Preview draft' });
  const accept = page.getByRole('button', { name: 'Accept view' });
  const block = page.getByRole('region', { name: 'Annotated candlesticks' });
  const chart = block.getByRole('img', { name: 'Candlestick chart with numeric annotations' });
  const authored = JSON.stringify({ version: 1, blocks: [{ kind: 'value-table' }, annotationBlock] }, null, 2);
  await expect(chart).toHaveCount(0);
  await draft.fill(authored);
  await preview.click();
  await expect(chart).toBeVisible();

  const observations = {};
  async function observe(name, candleCount, indices, changed = () => true) {
    let frame;
    await expect.poll(async () => {
      frame = await paintedGeometry(chart);
      if (frame.candles.length !== candleCount || frame.bubbles.length !== indices.length) return frame;
      const offsets = indices.map((index, bubble) => {
        const candle = frame.candles[index], mark = frame.bubbles[bubble];
        return { x: Math.abs(mark.x - candle.x), y: candle.wickTop - mark.leaderBottom };
      });
      if (!changed(frame) || offsets.some(({ x, y }) => x > 1 || y < 0 || y > 4)) return { frame, offsets };
      observations[name] = { ...frame, offsets };
      return 'aligned';
    }, { message: name }).toBe('aligned');
    return frame;
  }

  const five = await observe('5m initial', 4, [2]);
  await block.getByText('Synthetic result attribution').click();
  const table = block.getByRole('table');
  const rows = table.locator('tbody tr');
  const fiveTimes = ['09:30', '09:35', '09:40', '09:45'];
  await expect(rows).toHaveCount(fiveTimes.length);
  for (const [i, time] of fiveTimes.entries()) {
    const row = rows.nth(i);
    const value = number => number === null ? 'Unknown' : String(number);
    await expect(row.locator('time')).toHaveAttribute('datetime', `2024-01-04T${time}:00.000Z`);
    await expect(row.getByRole('cell')).toHaveText([
      value(numericCase.strength_by_bar[i]), value(numericCase.turnover_fraction_by_bar[i]), i === 2 ? '3.2%' : '—',
    ]);
  }
  await expect(table.getByRole('columnheader', { name: 'Turnover (Fraction) · turnover' })).toBeVisible();
  await expect(block.getByText('qualification-equity-5m-bars-v1', { exact: true })).toBeVisible();
  await expect(block.getByText('qualification-equity-5m-results-v1', { exact: true })).toBeVisible();
  await expect(table.getByRole('row', { name: '2024-01-04T09:40:00.000Z', exact: true })
    .getByRole('cell', { name: '3.2%', exact: true })).toBeVisible();
  await testInfo.attach('five-minute-chart', { body: await chart.screenshot(), contentType: 'image/png' });
  await accept.click();

  await block.getByRole('button', { name: 'Zoom in' }).click();
  const zoomed = await observe('5m zoom', 4, [2], frame => Math.abs(frame.bubbles[0].x - five.bubbles[0].x) > 2);
  const bounds = await chart.boundingBox();
  await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + 150);
  await page.mouse.down();
  await page.mouse.move(bounds.x + bounds.width / 2 + 24, bounds.y + 150, { steps: 6 });
  await page.mouse.up();
  const panned = await observe('5m pan', 4, [2], frame => Math.abs(frame.bubbles[0].x - zoomed.bubbles[0].x) > 10);

  await page.setViewportSize({ width: 1010, height: 820 });
  // The SDK keeps the right edge anchored when the pane narrows. The first candle is offscreen.
  await observe('5m resize', 3, [1], frame => frame.width < panned.width - 80
    && frame.candles[1].wickTop === Math.min(...frame.candles.map(candle => candle.wickTop)));

  await block.getByRole('button', { name: '15 min' }).click();
  await observe('15m switch', 1, [0]);
  await expect(rows).toHaveCount(1);
  await expect(rows.locator('time')).toHaveAttribute('datetime', '2024-01-04T09:30:00.000Z');
  await expect(rows.getByRole('cell')).toHaveText(['0.9', '0.047', '4.7%']);
  await expect(block.getByText('qualification-equity-15m-bars-v1', { exact: true })).toBeVisible();
  await expect(block.getByText('qualification-equity-15m-results-v1', { exact: true })).toBeVisible();
  await expect(table.getByRole('row', { name: '2024-01-04T09:30:00.000Z', exact: true })
    .getByRole('cell', { name: '4.7%', exact: true })).toBeVisible();
  await testInfo.attach('fifteen-minute-chart', { body: await chart.screenshot(), contentType: 'image/png' });
  await block.getByRole('button', { name: '5 min', exact: true }).click();
  await observe('5m return', 4, [2]);
  await expect(rows.nth(2).getByRole('cell').last()).toHaveText('3.2%');

  const alternate = { ...annotationBlock, annotation: { field: 'strength', when: { field: 'strength', above: 0.2 } } };
  await draft.fill(JSON.stringify({ version: 1, blocks: [{ kind: 'value-table' }, alternate] }));
  await preview.click();
  await observe('5m rebind', 4, [1, 2]);
  await expect(rows.nth(1).getByRole('cell').last()).toHaveText('0.5');
  await expect(rows.nth(2).getByRole('cell').last()).toHaveText('0.95');
  await expect(rows.nth(3).getByRole('cell')).toHaveText(['Unknown', 'Unknown', '—']);
  await expect(block.getByText('qualification-equity-5m-results-v1', { exact: true })).toBeVisible();

  for (const [name, invalid] of [
    ['ungranted source', { ...annotationBlock, source: 'configured-market-data' }],
    ['missing field', { ...annotationBlock, annotation: { ...annotationBlock.annotation, field: 'rsi' } }],
  ]) {
    await draft.fill(JSON.stringify({ version: 1, blocks: [{ kind: 'value-table' }, invalid] }));
    await preview.click();
    await expect(page.getByRole('alert')).toHaveText('Block 2 must bind granted synthetic fields and a finite threshold.');
    await expect(accept).toBeDisabled();
    await observe(`5m after ${name}`, 4, [2]);
    await expect(rows.nth(2).getByRole('cell').last()).toHaveText('3.2%');
  }
  await page.getByRole('button', { name: 'Restore accepted view' }).click();
  await expect(draft).toHaveValue(authored);
  await expect(page.getByRole('alert')).toHaveCount(0);

  await page.getByRole('dialog', { name: 'Workbench prototype' }).getByRole('button', { name: 'Language settings' }).click();
  await page.getByRole('dialog', { name: 'Language settings' }).getByRole('combobox').selectOption('zh-Hans');
  await page.getByRole('button', { name: '关闭', exact: true }).click();
  const localized = page.getByRole('region', { name: '带标注的蜡烛图' });
  await expect(localized.getByText('合成结果溯源')).toBeVisible();
  await expect(localized.getByRole('button', { name: '5 分钟', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(localized.getByRole('row', { name: '2024-01-04T09:45:00.000Z', exact: true })
    .getByRole('cell', { name: '未知' })).toHaveCount(2);
  await testInfo.attach('candle-annotation-geometry', {
    body: JSON.stringify(observations, null, 2), contentType: 'application/json',
  });
  await testInfo.attach('annotated-chart', { body: await localized.screenshot(), contentType: 'image/png' });
});

test('a drag started before the first replay quote keeps its original candle anchor', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Open workbench prototype' }).click();
  await page.getByRole('button', { name: 'Add candlesticks' }).click();
  await page.getByRole('button', { name: 'Preview draft' }).click();
  await page.getByRole('button', { name: 'Accept view' }).click();
  const chart = page.getByRole('img', { name: 'Candlestick chart with numeric annotations' });
  const readDraw = () => chart.evaluate(node => JSON.parse(node.dataset.primitiveDraw ?? 'null'));
  await expect.poll(readDraw).toMatchObject({ quoteSequence: 0 });
  const frozen = await readDraw();
  const oldWick = async () => (await paintedGeometry(chart)).candles[2]?.x;
  const frozenWick = await oldWick();
  await page.getByRole('button', { name: 'Start synthetic quote replay' }).click();
  const rect = await chart.boundingBox();
  const x = rect.x + rect.width / 2;
  const y = rect.y + 130;
  await page.mouse.move(x, y);
  await page.mouse.down();
  try {
    for (let shift = 8; shift <= 48; shift += 8) {
      await page.mouse.move(x + shift, y);
      await delay(20);
    }
    await expect.poll(oldWick, { timeout: 300 }).toBeGreaterThan(frozenWick);
    const held = await readDraw();
    expect(held.quoteSequence).toBe(0);
    expect((await paintedGeometry(chart)).candles).toHaveLength(4);
    expect(held.viewport.logical).not.toEqual(frozen.viewport.logical);
    const beforeAppend = await oldWick();
    await expect.poll(readDraw).toMatchObject({ quoteSequence: 1 });
    expect(await oldWick()).toBe(beforeAppend);
    await page.mouse.move(x + 54, y);
    await expect.poll(async () => Math.abs((await oldWick()) - (beforeAppend + 6))).toBeLessThanOrEqual(1);
  } finally {
    await page.mouse.up();
  }

  await page.getByRole('button', { name: 'Reset synthetic quote replay' }).click();
  await expect.poll(readDraw).toMatchObject({ quoteSequence: 0 });
  await page.mouse.move(rect.x + 62, y);
  await page.mouse.down();
  await page.mouse.move(rect.x + rect.width - 92, y, { steps: 12 });
  await page.mouse.up();
  await expect.poll(async () => (await readDraw()).viewport.logical.from).toBeLessThan(-3.5);
  const leftLimit = (await readDraw()).viewport.logical;
  await page.getByRole('button', { name: 'Start synthetic quote replay' }).click();
  await expect.poll(readDraw).toMatchObject({ quoteSequence: 1 });
  expect((await readDraw()).viewport.logical).toEqual(leftLimit);
});

test('replays source-scoped quotes on the accepted candle pane while it is panned', async ({ page }, testInfo) => {
  await page.goto('/');
  const open = page.getByRole('button', { name: 'Open workbench prototype' });
  await open.click();
  await page.getByRole('button', { name: 'Add candlesticks' }).click();
  await page.getByRole('button', { name: 'Preview draft' }).click();
  const block = page.getByRole('region', { name: 'Annotated candlesticks' });
  const chart = block.getByRole('img', { name: 'Candlestick chart with numeric annotations' });
  const start = block.getByRole('button', { name: 'Start synthetic quote replay' });
  const reset = block.getByRole('button', { name: 'Reset synthetic quote replay' });
  await expect(start).toHaveCount(0);
  await page.getByRole('button', { name: 'Accept view' }).click();
  await block.getByText('Synthetic result attribution').click();
  const frozen = await expectAttribution(block, '5m');
  await expect(frozen.getByRole('cell')).toHaveText(['0.95', '0.032', '3.2%']);
  const acceptedText = await page.getByRole('textbox', { name: 'Composition JSON' }).inputValue();
  const element = await chart.elementHandle();
  const readDraw = () => chart.evaluate(node => JSON.parse(node.dataset.primitiveDraw ?? 'null'));
  await expect.poll(readDraw).toMatchObject({ quoteSequence: 0, resultRevision: 'qualification-equity-5m-results-v1' });
  const initial = await readDraw();
  await chart.evaluate(node => {
    window.primitiveDraws = [];
    new MutationObserver(() => {
      if (node.dataset.primitiveDraw) window.primitiveDraws.push(JSON.parse(node.dataset.primitiveDraw));
    }).observe(node, { attributes: true, attributeFilter: ['data-primitive-draw'] });
  });

  const newBarTime = Date.UTC(2024, 0, 4, 9, 50);
  const row = block.getByRole('row', { name: '2024-01-04T09:50:00.000Z', exact: true });
  const status = block.getByRole('status');
  async function painted(direction, count) {
    await expect.poll(async () => {
      const { candles, bubbles } = await paintedGeometry(chart);
      return [candles.length, candles.at(-1)?.direction, bubbles.length];
    }).toEqual([5, direction, count]);
  }
  await start.click();
  await expect(status).toHaveText('Synthetic replay quote 1 of 3');
  await expect(row.getByRole('cell')).toHaveText(['0.84', '0.038', '3.8%']);
  await expect.poll(readDraw).toMatchObject({
    quoteSequence: 1, resultRevision: 'qualification-equity-5m-replay-results-1',
    viewport: { logical: initial.viewport.logical },
    annotations: [{ text: '3.2%' }, { timeMs: newBarTime, text: '3.8%' }],
  });
  const first = await readDraw();
  await painted('up', 2);

  await expect(status).toHaveText('Ignored stale replay quote');
  await expect.poll(readDraw).toMatchObject({ quoteSequence: 1, annotationRevision: first.annotationRevision });
  await expect(row.getByRole('cell')).toHaveText(['0.84', '0.038', '3.8%']);
  await expect(status).toHaveText('Ignored replay quote from another source');
  await expect.poll(readDraw).toMatchObject({ quoteSequence: 1, annotationRevision: first.annotationRevision });
  await expect(row.getByRole('cell')).toHaveText(['0.84', '0.038', '3.8%']);

  const bounds = await chart.boundingBox();
  const x = bounds.x + bounds.width * 0.68;
  const y = bounds.y + 140;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await Promise.all([
    (async () => {
      try {
        for (let step = 1; step <= 12; step++) {
          await page.mouse.move(x - step * 6, y);
          await delay(75);
        }
      } finally {
        await page.mouse.up();
      }
    })(),
    (async () => {
      await expect(status).toHaveText('Synthetic replay quote 2 of 3');
      await expect(row.getByRole('cell')).toHaveText(['0.78', '0.04', '—']);
      await expect.poll(readDraw).toMatchObject({
        quoteSequence: 2, resultRevision: 'qualification-equity-5m-replay-results-2',
        annotations: [{ text: '3.2%' }],
      });
      await painted('down', 1);
    })(),
  ]);

  await expect(status).toHaveText('Synthetic quote replay complete');
  await expect(row.getByRole('cell')).toHaveText(['0.93', '0.045', '4.5%']);
  await expect(block.getByText('qualification-equity-5m-bars-v1', { exact: true })).toBeVisible();
  await expect(block.getByText('qualification-equity-5m-replay-bars-3', { exact: true })).toBeVisible();
  await expect(block.getByText('2024-01-04T09:55:00.000Z', { exact: true })).toBeVisible();
  await expect(frozen.getByRole('cell')).toHaveText(['0.95', '0.032', '3.2%']);
  await expect.poll(readDraw).toMatchObject({
    quoteSequence: 3, resultRevision: 'qualification-equity-5m-replay-results-3',
    annotations: [{ text: '3.2%' }, { timeMs: newBarTime, text: '4.5%' }],
  });
  await painted('up', 2);
  const last = await readDraw();
  expect(last.viewport.logical).not.toEqual(first.viewport.logical);
  const draws = await page.evaluate(() => window.primitiveDraws);
  expect([...new Set(draws.map(draw => draw.quoteSequence).filter(Boolean))]).toEqual([1, 2, 3]);
  expect(new Set(draws.filter(draw => draw.quoteSequence === 2).map(draw => draw.viewport.logical.from)).size).toBeGreaterThan(1);
  const leftEdges = draws.map(draw => draw.viewport.logical.from);
  expect(leftEdges.every((edge, index) => index === 0 || edge >= leftEdges[index - 1])).toBe(true);
  expect(draws.find(draw => draw.quoteSequence === 2).annotationRevision).toBeGreaterThan(first.annotationRevision);
  expect(last.annotationRevision).toBeGreaterThan(draws.find(draw => draw.quoteSequence === 2).annotationRevision);
  await testInfo.attach('quote-replay-primitive-draws', { body: JSON.stringify(draws, null, 2), contentType: 'application/json' });
  await testInfo.attach('quote-replay-final', { body: await block.screenshot(), contentType: 'image/png' });
  await delay(700);
  await expect(status).toHaveText('Synthetic quote replay complete');
  await expect.poll(readDraw).toMatchObject({ quoteSequence: 3, annotationRevision: last.annotationRevision });

  const draft = page.getByRole('textbox', { name: 'Composition JSON' });
  await draft.fill(acceptedText.replace('qualification-equity', 'ungranted-synthetic-source'));
  await page.getByRole('button', { name: 'Preview draft' }).click();
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(row.getByRole('cell')).toHaveText(['0.93', '0.045', '4.5%']);
  await page.getByRole('button', { name: 'Restore accepted view' }).click();
  await expect(draft).toHaveValue(acceptedText);

  await reset.click();
  await expect(row).toHaveCount(0);
  await expect(start).toBeEnabled();
  await expect.poll(readDraw).toMatchObject({ quoteSequence: 0, resultRevision: 'qualification-equity-5m-results-v1',
    viewport: { logical: last.viewport.logical } });
  expect(await chart.evaluate((node, original) => node === original, element)).toBe(true);
  await expect(page.getByRole('textbox', { name: 'Composition JSON' })).toHaveValue(acceptedText);
  await start.click();
  await expect(status).toHaveText('Synthetic replay quote 1 of 3');
  await block.getByRole('button', { name: '15 min', exact: true }).click();
  await expect(start).toHaveCount(0);
  await expectTurnover(block, '15m');
  await expect.poll(readDraw).toMatchObject({ quoteSequence: 0 });
  await block.getByRole('button', { name: '5 min', exact: true }).click();
  await expect(start).toBeEnabled();
  await delay(700);
  await expect.poll(readDraw).toMatchObject({ quoteSequence: 0, resultRevision: 'qualification-equity-5m-results-v1' });
  await expect(row).toHaveCount(0);
  await start.click();
  await expect(status).toHaveText('Synthetic replay quote 1 of 3');
  await page.getByRole('button', { name: 'Back to chart' }).click();
  await open.click();
  await expect(draft).toHaveValue(acceptedText);
  await expect(chart).toBeVisible();
  await expect(start).toBeEnabled();
  await expectTurnover(block, '5m');
  await delay(700);
  await expect(reset).toHaveCount(0);
  await expect(status).toHaveCount(0);
  await expect(row).toHaveCount(0);
  await expect.poll(readDraw).toMatchObject({
    quoteSequence: 0, resultRevision: 'qualification-equity-5m-results-v1',
    annotations: [{ text: '3.2%' }],
  });
});
