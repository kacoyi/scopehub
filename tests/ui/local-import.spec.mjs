import { startLocalImportBridge } from './local-import-bridge.mjs';
import { test as base, expect } from '@playwright/test';
import { createHash, randomUUID } from 'node:crypto';
import { mkdir, mkdtemp, readFile, realpath, rename, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { painted } from './chart-pixels.mjs';
import { control, openPanel, watchlistSymbol } from './workspace-controls.mjs';

const folderId = '95ef05fd-a797-4bb7-9e63-112233440001';
const equity = {
  fileId: '95ef05fd-a797-4bb7-9e63-112233440002',
  path: 'equities/daily.csv',
  header: ['Ticker', 'TradingDate', 'First', 'Top', 'Bottom', 'Native close', 'Shares'],
  rows: [
    ['000731', '2026-04-14', '36', '39', '34', '38', '120'],
    ['000731', '2026-04-15', '38', '41', '37', '40', ''],
  ],
  columns: {
    symbol: 'Ticker', timestamp: 'TradingDate', trading_day: 'TradingDate',
    open: 'First', high: 'Top', low: 'Bottom', close: 'Native close', volume: 'Shares',
  },
  settings: {
    instrument_kind: 'equity', venue: 'TEST-SH', timeframe: '1d', calendar_id: 'synthetic-cash-v1',
    session_scope: 'day', timezone: 'Asia/Shanghai', quantity_unit: 'shares', price_adjustment: 'raw',
    timestamp_format: '%Y-%m-%d', trading_day_format: '%Y-%m-%d', daily_start_time: '09:30:00',
  },
  times: ['2026-04-14T01:30:00Z', '2026-04-15T01:30:00Z'],
};
const future = {
  fileId: '95ef05fd-a797-4bb7-9e63-112233440003',
  path: 'futures/night.csv',
  header: ['Contract', 'Start', 'TradeDate', 'Open', 'High', 'Low', 'Close', 'Contracts', 'PrevSettlementPrice', 'is_closed'],
  rows: [
    ['CU2605', '2026-04-14 21:05', '2026-04-15', '100', '106', '96', '102', '31', '99', 'true'],
    ['CU2605', '2026-04-14 21:10', '2026-04-15', '102', '108', '101', '105', '37', '99', 'true'],
    ['CU2605', '2026-04-14 21:15', '2026-04-15', '105', '107', '104', '106', '6', '99', 'false'],
  ],
  columns: {
    symbol: 'Contract', timestamp: 'Start', trading_day: 'TradeDate',
    open: 'Open', high: 'High', low: 'Low', close: 'Close', volume: 'Contracts',
  },
  settings: {
    instrument_kind: 'dated_future', venue: 'TEST-FU', timeframe: '5m', calendar_id: 'synthetic-futures-v1',
    session_scope: 'night', timezone: 'Asia/Shanghai', quantity_unit: 'contracts', price_adjustment: 'raw',
    timestamp_format: '%Y-%m-%d %H:%M', trading_day_format: '%Y-%m-%d',
  },
  times: ['2026-04-14T13:05:00Z', '2026-04-14T13:10:00Z'],
};

function sourceIdentity(file, invalidPart) {
  const valid = Buffer.from([file.header, ...file.rows].map(row => row.join(',') + '\n').join(''));
  const invalidByte = Buffer.from([0xff]);
  const bytes = invalidPart === 'header' ? Buffer.concat([invalidByte, valid])
    : invalidPart === 'body' ? Buffer.concat([valid, invalidByte]) : valid;
  return {
    version: 1, folder_id: folderId, file_id: file.fileId,
    relative_path: file.path, display_name: file.path.split('/').at(-1),
    byte_count: Buffer.byteLength(bytes), sha256: createHash('sha256').update(bytes).digest('hex'),
  };
}

const files = [equity, future];
const readSourceRow = 'market.readLocalImportSourceRow';
const catalog = {
  folder_id: folderId, display_name: 'Synthetic CSV fixture folder',
  files: files.map(file => {
    const { file_id, relative_path, display_name, byte_count } = sourceIdentity(file);
    return { file_id, relative_path, display_name, byte_count, kind: 'csv' };
  }),
};

const sampleFixtures = JSON.parse(await readFile(new URL('../fixtures/market/series.json', import.meta.url), 'utf8')).fixtures;

function sampleHistory({ series, from_ms, to_ms, warmup_bars }) {
  const { history: { dataset, bars } } = sampleFixtures.find(({ history }) =>
    history.dataset.instrument_id === series.instrument_id && history.dataset.timeframe === series.timeframe);
  const as_of_ms = dataset.provenance.as_of_ms;
  return {
    dataset, bars, total_bars: bars.length, offset: 0, next_cursor: null, stream_position: null,
    snapshot: { series, from_ms, to_ms, warmup_bars, as_of_ms,
      dataset_id: dataset.id, dataset_revision: dataset.revision,
      content_hash: createHash('sha256').update(JSON.stringify(bars)).digest('hex') },
    quality_state: { as_of_ms, last_observed_ms: dataset.provenance.last_observed_ms,
      session_state: 'open', freshness: 'current', connection: 'offline', coverage: 'observed', gaps: [], source_kind: 'synthetic' },
  };
}

function importedFixture({ payload, requestId }, invalidPart) {
  expect(Object.keys(payload).sort()).toEqual(
    ['file_id', 'folder_id', 'mapping', 'version', ...['expected_sha256', 'row_limit'].filter(field => field in payload)].sort(),
  );
  expect(payload.folder_id).toBe(folderId);
  expect(payload.version).toBe(1);
  if ('row_limit' in payload) {
    expect(payload.row_limit).toBeGreaterThan(0);
    expect(payload.row_limit).toBeLessThanOrEqual(500);
  }
  expect(requestId).toMatch(/^[\w-]+$/);
  const file = files.find(file => file.fileId === payload.file_id);
  expect(file).toBeDefined();
  const identity = sourceIdentity(file, invalidPart);
  const { mapping } = payload;
  expect(mapping).toMatchObject({ version: 1, encoding: 'utf-8', delimiter: ',' });
  expect(mapping.revision).toEqual(expect.any(String));
  const isNative = mapping.record_kind === 'native_ohlcv';
  const wrongClose = isNative && mapping.columns.close === 'PrevSettlementPrice';
  if (isNative) {
    expect(mapping).toMatchObject({ ...file.settings, volume_semantics: 'per_bar', timestamp_convention: 'bar_start' });
    expect(mapping.columns).toEqual(wrongClose ? { ...file.columns, close: 'PrevSettlementPrice' } : file.columns);
    expect(payload.expected_sha256).toBe(identity.sha256);
  }
  const issues = invalidPart ? [{ code: 'decode_failed', field: 'encoding', message: 'Invalid UTF-8 in this CSV file.' }]
    : wrongClose ? [{ code: 'auxiliary_column', field: 'close', message: 'Previous settlement price cannot be mapped to OHLC.' }] : [];
  const rows = invalidPart ? [] : file.rows.map((rawFields, index) => {
    const incomplete = isNative && !wrongClose && index >= file.times.length;
    const normalized = isNative && !wrongClose && !incomplete ? {
      time_ms: Date.parse(file.times[index]), trading_day: rawFields[file === future ? 2 : 1],
      open: Number(rawFields[file === future ? 3 : 2]), high: Number(rawFields[file === future ? 4 : 3]),
      low: Number(rawFields[file === future ? 5 : 4]), close: Number(rawFields[file === future ? 6 : 5]),
      volume: rawFields[file === future ? 7 : 6] === '' ? null : Number(rawFields[file === future ? 7 : 6]), is_closed: true,
    } : null;
    return {
      record_number: index + 1, line_start: index + 2, line_end: index + 2,
      raw_fields: rawFields, raw_text: null, symbol: normalized ? rawFields[0] : null, normalized,
      issues: incomplete ? [{ code: 'bar_incomplete', field: 'is_closed', message: 'The source marks this bar incomplete.' }] : [],
    };
  });
  const series = isNative && !wrongClose && !invalidPart ? {
    instrument_kind: file.settings.instrument_kind, venue: file.settings.venue, timeframe: file.settings.timeframe,
    calendar_id: file.settings.calendar_id, session_scope: file.settings.session_scope, timezone: file.settings.timezone,
    quantity_unit: file.settings.quantity_unit, price_adjustment: file.settings.price_adjustment, roll_method: null,
  } : null;
  return {
    ...identity, preview: {
      file_sha256: identity.sha256, mapping_version: mapping.version, mapping_revision: mapping.revision,
      header: invalidPart === 'header' ? [] : file.header, raw_header_text: null, rows, issues,
      raw_only_columns: file === future ? [{ column: 'PrevSettlementPrice', reason: 'Previous settlement price' }] : [],
      series, truncated: false,
    },
  };
}

// The browser substitutes Tauri invocation and the responses from the host.
// Only synthetic fixture bytes contribute to display data and expected hashes.
const test = base.extend({
  pickerLocale: ['en', { option: true }],
  sampleChart: [false, { option: true }],
  mockNative: async ({ page, baseURL, pickerLocale, sampleChart }, use) => {
    const choices = [];
    const calls = [];
    const saved = new Map();
    const changedHeaders = new Set();
    const invalidParts = new Map();
    const cancelSaveAs = [];
    const pendingSaves = new Map();
    const requests = [];
    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(String(error)));
    page.on('request', request => requests.push(request.url()));
    await page.exposeBinding('testNativeInvoke', async (_, command, args) => {
      calls.push({ command, args });
      if (command === 'choose_local_csv_folder') {
        expect(args).toEqual({ locale: pickerLocale });
        expect(choices.length).toBeGreaterThan(0);
        return choices.shift();
      }
      if (command === 'cancel_local_import_preview') {
        expect(args).toEqual({ requestId: expect.any(String) });
        pendingSaves.get(args.requestId)?.();
        return null;
      }
      expect(command).toBe('execute_operation');
      if (args.operation === 'instrument.search') return {
        instruments: sampleChart ? sampleFixtures.filter(item => item.scenario === 'normal' && item.history.dataset.timeframe === '1m')
          .map(item => item.instrument) : [],
      };
      if (sampleChart && args.operation === 'market.readHistoryPage') return sampleHistory(args.payload);
      if (args.operation === 'market.saveLocalImportMapping') {
        const { preview, file_id } = importedFixture(args);
        expect(preview.issues).toEqual([]);
        expect(preview.rows.some(row => row.normalized?.is_closed)).toBe(true);
        const cancelAs = cancelSaveAs.shift();
        if (cancelAs) {
          await new Promise(resolve => pendingSaves.set(args.requestId, resolve));
          pendingSaves.delete(args.requestId);
          if (cancelAs === 'cancelled') return { invokeError: { code: 'CANCELLED' } };
        }
        const record = {
          review_revision: (saved.get(file_id)?.review_revision ?? 0) + 1,
          header_sha256: createHash('sha256').update(JSON.stringify(preview.header)).digest('hex'),
          mapping: args.payload.mapping,
        };
        saved.set(file_id, record);
        return { version: 1, record };
      }
      if (args.operation === 'market.loadLocalImportMapping') {
        const file = files.find(file => file.fileId === args.payload.file_id);
        expect(file).toBeDefined();
        expect(args.payload).toEqual({ version: 1, folder_id: folderId, file_id: file.fileId });
        expect(args.requestId).toMatch(/^[\w-]+$/);
        const record = saved.get(file.fileId);
        if (!record) return { version: 1, status: 'not_saved', record: null, source_sha256: null };
        const invalidPart = invalidParts.get(file.fileId);
        if (invalidPart === 'header') return { invokeError: {
          code: 'DATA_UNAVAILABLE', message: 'The CSV header could not be verified. Check the encoding and inspect the current file.',
          details: { reason: 'csv_header_unavailable', sha256: sourceIdentity(file, invalidPart).sha256 },
        } };
        if (changedHeaders.has(file.fileId)) {
          const changed = { ...file, header: [...file.header, 'New header'] };
          return { version: 1, status: 'review_required', record: null, source_sha256: sourceIdentity(changed).sha256 };
        }
        return { version: 1, status: 'saved', record, source_sha256: sourceIdentity(file, invalidPart).sha256 };
      }
      expect(args.operation).toBe('market.previewLocalImport');
      return importedFixture(args, invalidParts.get(args.payload.file_id));
    });
    await page.addInitScript(() => {
      window.isTauri = true;
      window.__TAURI_INTERNALS__ = {
        async invoke(command, args) {
          const reply = await window.testNativeInvoke(command, args);
          if (reply?.invokeError) throw reply.invokeError;
          return reply;
        },
      };
    });
    await use({ calls, choices, changedHeaders, invalidParts, cancelSaveAs });
    expect(pageErrors).toEqual([]);
    expect(requests.every(url => new URL(url).origin === baseURL && !new URL(url).pathname.startsWith('/__scopehub/'))).toBe(true);
  },
});

async function openPersonalSources(page, labels = { settings: 'Settings', sources: 'Data sources', personal: 'My data sources' }) {
  await control(page, labels.settings).click();
  const dialog = page.getByRole('dialog', { name: labels.settings, exact: true });
  await control(dialog, labels.sources).click();
  await control(dialog, labels.personal).click();
  await expect(dialog.getByRole('heading', { name: labels.personal, exact: true })).toBeVisible();
  return dialog;
}

test.describe('Simplified Chinese desktop source selection', () => {
  test.use({ locale: 'zh-CN', pickerLocale: 'zh-Hans' });

  test('opens My data sources in Chinese and sends the effective language to the chooser', async ({ page, mockNative }, testInfo) => {
    await page.goto('/');
    const dialog = await openPersonalSources(page, { settings: '设置', sources: '数据源', personal: '我的数据源' });
    await expect(dialog.getByRole('status')).toHaveText('尚未选择 CSV 文件夹');
    await expect(control(dialog, '继续审阅 CSV')).toHaveCount(0);
    await page.screenshot({ path: testInfo.outputPath('my-data-sources-zh-Hans.png') });
    mockNative.choices.push(catalog);
    await control(dialog, '选择本地 CSV 文件夹').click();
    await expect(dialog).toBeHidden();
    await expect(page.getByRole('button', { name: '语言设置' }).last()).toBeVisible();
    const file = page.getByRole('combobox', { name: 'CSV 文件', exact: true });
    await file.selectOption(equity.fileId);
    await expect(file.locator('option:checked')).toContainText(equity.path);
    await select(page, '记录类型').selectOption('native_ohlcv');
    await control(page, '查看原始行').click();
    await control(page, '根据表头起草映射').click();
    await expect(select(page, '交易日列')).toHaveValue('TradingDate');
    await expect(page.getByRole('status').filter({ hasText: '请核对所选列名' })).toContainText('没有可识别的列名');
    await control(page, '语言设置').click();
    const settings = page.getByRole('dialog', { name: '语言设置' });
    await settings.getByRole('combobox').selectOption('en');
    await page.getByRole('button', { name: 'Close', exact: true }).click();
    await expect(select(page, 'CSV file')).toHaveValue(equity.fileId);
    await expect(select(page, 'CSV file').locator('option:checked')).toContainText(equity.path);
    expect(mockNative.calls.filter(call => call.command === 'choose_local_csv_folder')).toHaveLength(1);
  });
});

const mappingFields = {
  venue: 'Venue', timeframe: 'Timeframe', calendar_id: 'Calendar ID', session_scope: 'Session scope',
  timezone: 'Timezone', quantity_unit: 'Quantity unit', price_adjustment: 'Price adjustment',
  timestamp_format: 'Timestamp format', trading_day_format: 'Trading day format', daily_start_time: 'Daily start time',
};
const columnFields = {
  symbol: 'Symbol column', timestamp: 'Timestamp column', trading_day: 'Trading day column',
  open: 'Open column', high: 'High column', low: 'Low column', close: 'Close column', volume: 'Volume column',
};

function select(page, name) {
  return page.getByRole('combobox', { name, exact: true });
}

test.describe('My data sources in the ordinary chart', () => {
  test.use({ sampleChart: true });

  test('cancels the picker and resumes the same review without changing the chart viewport', async ({ page, mockNative }, testInfo) => {
    await page.goto('/');
    await control(page, 'Open offline sample').click();
    await openPanel(page, 'Watchlist');
    const stock = watchlistSymbol(page, 'SIM.CN.STOCK.001');
    await stock.click();
    await control(page, '5m').click();
    await control(page, 'Close-price line').click();
    const history = page.getByRole('region', { name: 'Price chart' }).locator('.history');
    await expect(history).toHaveAttribute('data-timeframe', '5m');
    await expect(history).toHaveAttribute('data-instrument', 'SIM.CN.STOCK.001');
    await expect.poll(() => painted(page, 'line')).toBeGreaterThan(20);
    // Compare the drawn price series without crosshair or hover labels.
    const priceViewport = () => history.locator('.chart canvas').first().evaluate(canvas => {
      const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
      let signature = 0;
      for (let i = 0; i < pixels.length; i += 4) {
        if (pixels[i] === 115 && pixels[i + 1] === 184 && pixels[i + 2] === 246) signature = (signature * 31 + i) | 0;
      }
      return signature;
    });
    const beforeZoom = await priceViewport();
    await control(page, 'Zoom in').click();
    await expect.poll(priceViewport).not.toEqual(beforeZoom);
    const zoomed = await priceViewport();
    const settings = control(page, 'Settings');
    const chooserCalls = () => mockNative.calls.filter(call => call.command === 'choose_local_csv_folder');
    const expectOriginalChart = async () => {
      await expect(settings).toBeFocused();
      await expect(stock).toHaveAttribute('aria-pressed', 'true');
      await expect(control(page, '5m')).toHaveAttribute('aria-pressed', 'true');
      await expect(control(page, 'Close-price line')).toHaveAttribute('aria-pressed', 'true');
      await expect.poll(priceViewport).toEqual(zoomed);
    };

    const dialog = await openPersonalSources(page);
    const choose = control(dialog, 'Choose local CSV folder');
    const resume = control(dialog, 'Resume CSV review');
    await expect(dialog.getByRole('status')).toHaveText('No CSV folder selected');
    await expect(resume).toHaveCount(0);
    await expect(choose).toBeFocused();
    expect(chooserCalls()).toHaveLength(0);

    const picker = Promise.withResolvers();
    mockNative.choices.push(picker.promise);
    await choose.press('Enter');
    await expect(dialog.getByRole('status')).toHaveText('Waiting for folder selection…');
    await expect(choose).toBeDisabled();
    picker.resolve(null);
    await expect(dialog.getByRole('status')).toHaveText('No CSV folder selected');
    await expect(choose).toBeFocused();
    await expect(resume).toHaveCount(0);
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
    await expectOriginalChart();
    expect(chooserCalls()).toHaveLength(1);

    await openPersonalSources(page);
    mockNative.choices.push(catalog);
    await choose.click();
    await expect(dialog).toBeHidden();
    const back = control(page, 'Back to chart');
    await expect(back).toBeFocused();
    await expect(history).toBeHidden();
    const review = page.getByRole('region', { name: 'Local CSV import' });
    await expect(review).toContainText(catalog.display_name);
    await select(review, 'CSV file').selectOption(future.fileId);
    await expect(control(review, 'Inspect raw rows')).toBeEnabled();
    await page.screenshot({ path: testInfo.outputPath('csv-review.png') });
    await back.click();
    await expectOriginalChart();

    await openPersonalSources(page);
    await expect(dialog.getByRole('status')).toHaveText('Folder available in this session');
    await expect(dialog.getByText(catalog.display_name, { exact: true })).toBeVisible();
    mockNative.choices.push(null);
    await choose.click();
    await expect(dialog.getByRole('status')).toHaveText('Folder available in this session');
    await expect(choose).toBeFocused();
    await page.screenshot({ path: testInfo.outputPath('my-data-sources-session.png') });
    await resume.click();
    await expect(select(review, 'CSV file')).toHaveValue(future.fileId);
    await back.click();
    await expectOriginalChart();
    const importCalls = mockNative.calls.filter(call => !['instrument.search', 'market.readHistoryPage'].includes(call.args?.operation));
    expect(importCalls).toEqual(Array.from({ length: 3 }, () => ({
      command: 'choose_local_csv_folder', args: { locale: 'en' },
    })));
  });
});

async function enterSeriesDeclarations(page, file) {
  await select(page, 'Record kind').selectOption('native_ohlcv');
  await select(page, 'Instrument type').selectOption(file.settings.instrument_kind);
  for (const [key, name] of Object.entries(mappingFields)) {
    if (key in file.settings) await page.getByRole('textbox', { name, exact: true }).fill(file.settings[key]);
  }
  await select(page, 'Volume basis').selectOption('per_bar');
  await select(page, 'Timestamp convention').selectOption('bar_start');
}

async function enterMapping(page, file, close = file.columns.close) {
  await enterSeriesDeclarations(page, file);
  for (const [key, name] of Object.entries(columnFields)) {
    await select(page, name).selectOption(key === 'close' ? close : file.columns[key] ?? { label: 'No volume column' });
  }
}

test('mocked native transport preserves the current source on cancel and supports reviewed equity and futures charts', async ({ page, mockNative }, testInfo) => {
  const choose = () => control(page, 'Choose local CSV folder').last().click();
  const chooserCalls = () => mockNative.calls.filter(call => call.command === 'choose_local_csv_folder');
  const chart = page.getByRole('region', { name: 'Local CSV chart preview' });

  await page.goto('/');
  await expect(control(page, 'Choose local CSV folder').first()).toBeVisible();
  expect(mockNative.calls).toHaveLength(0);
  await expect(page.locator('input[type=file]')).toHaveCount(0);

  await control(page, 'Open offline sample').click();
  await expect(page.getByText('Simulated', { exact: true }).first()).toBeVisible();
  await openPanel(page, 'Data sources');
  mockNative.choices.push(null);
  await choose();
  await expect.poll(() => chooserCalls().length).toBe(1);
  await expect(page.getByText('Simulated', { exact: true }).first()).toBeVisible();

  mockNative.choices.push(catalog);
  await choose();
  await expect(page.getByText('Local CSV', { exact: true }).first()).toBeVisible();
  await expect(page.getByText('Simulated', { exact: true })).toBeHidden();
  const fileSelector = select(page, 'CSV file');
  const rawRows = page.getByRole('table', { name: 'Raw CSV rows' }).locator('tbody tr');
  const normalizedRows = page.getByRole('table', { name: 'Normalized bars' }).locator('tbody tr');
  await fileSelector.selectOption(equity.fileId);
  await control(page, 'Inspect raw rows').click();
  await expect(page.getByRole('heading', { name: 'Raw CSV rows' })).toBeVisible();
  await expect(page.getByText('000731', { exact: true }).first()).toBeVisible();
  await expect(page.getByText(sourceIdentity(equity).sha256, { exact: true }).first()).toBeVisible();
  await expect(chart.locator('canvas')).toHaveCount(0);
  await enterMapping(page, equity);
  await control(page, 'Preview mapping').click();
  await expect(normalizedRows).toHaveCount(2);
  await expect(normalizedRows.last()).toContainText('Unknown');
  await expect(chart.getByRole('img', { name: /Candlestick and volume chart for 000731/ })).toBeVisible();
  await expect.poll(() => painted(page, 'candlestick')).toBeGreaterThan(20);

  mockNative.choices.push(null);
  await choose();
  await expect.poll(() => chooserCalls().length).toBe(3);
  await expect(fileSelector).toHaveValue(equity.fileId);
  await expect(select(page, 'Close column')).toHaveValue('Native close');
  await expect(page.getByText(sourceIdentity(equity).sha256, { exact: true }).first()).toBeVisible();
  await expect(chart.getByRole('img', { name: /Candlestick and volume chart for 000731/ })).toBeVisible();

  await fileSelector.selectOption(future.fileId);
  await control(page, 'Inspect raw rows').click();
  await expect(page.getByText(sourceIdentity(future).sha256, { exact: true }).first()).toBeVisible();
  await expect(page.getByText('PrevSettlementPrice', { exact: true }).first()).toBeVisible();
  await select(page, 'Record kind').selectOption('native_ohlcv');
  const callsBeforeDraft = mockNative.calls.length;
  await control(page, 'Draft mapping from headers').click();
  await expect(select(page, 'Close column')).toHaveValue('Close');
  await expect(select(page, 'Timestamp column')).toHaveValue('');
  await expect(page.getByRole('status').filter({ hasText: 'No recognized header' })).toContainText('Timestamp column');
  expect(mockNative.calls).toHaveLength(callsBeforeDraft);
  await enterSeriesDeclarations(page, future);
  await select(page, 'Timestamp column').selectOption('Start');
  await select(page, 'Volume column').selectOption('Contracts');
  await select(page, 'Close column').selectOption('PrevSettlementPrice');
  await control(page, 'Preview mapping').click();
  await expect(page.getByText('Previous settlement price cannot be mapped to OHLC.', { exact: false }).first()).toBeVisible();
  await expect(chart.locator('canvas')).toHaveCount(0);
  await select(page, 'Close column').selectOption('Close');
  await control(page, 'Preview mapping').click();
  await expect(page.getByText('The source marks this bar incomplete.', { exact: false }).first()).toBeVisible();
  await expect(rawRows).toHaveCount(3);
  await expect(rawRows.last()).toContainText('false');
  await expect(normalizedRows).toHaveCount(2);
  await expect(normalizedRows.first().getByRole('cell')).toHaveText([
    '2', 'CU2605', '2026-04-14T13:05:00.000Z', '2026-04-15', '100', '106', '96', '102', '31',
  ]);
  const futuresChart = chart.getByRole('img', { name: /Candlestick and volume chart for CU2605/ });
  await expect(futuresChart).toBeVisible();
  await expect.poll(() => painted(page, 'candlestick')).toBeGreaterThan(20);
  const previewCalls = mockNative.calls.filter(call => call.args?.operation === 'market.previewLocalImport');
  expect(previewCalls.some(call => call.args.payload.mapping.columns?.close === 'PrevSettlementPrice')).toBe(true);
  expect(previewCalls.at(-1).args.payload.mapping.columns.close).toBe('Close');
  expect(new Set(previewCalls.map(call => call.args.requestId)).size).toBe(previewCalls.length);
  await page.screenshot({ path: testInfo.outputPath('reviewed-local-futures.png'), fullPage: true });
  await control(page, 'Open offline sample').click();
  await expect(page.getByText('Simulated', { exact: true }).first()).toBeVisible();
  await openPanel(page, 'Data sources');
  await control(page, 'Back to local CSV preview').click();
  await expect(fileSelector).toHaveValue(future.fileId);
  await expect(futuresChart).toBeVisible();
});

test('substitute native transport saves the reviewed preview and distinguishes header and body failures', async ({ page, mockNative }) => {
  const matchingCalls = (operation) => mockNative.calls.filter(call => call.args?.operation === operation);
  const saves = () => matchingCalls('market.saveLocalImportMapping');
  const loads = () => matchingCalls('market.loadLocalImportMapping');
  const previews = () => matchingCalls('market.previewLocalImport');
  const save = control(page, 'Save reviewed mapping');
  const load = control(page, 'Load reviewed mapping');
  const close = select(page, 'Close column');
  const venue = page.getByRole('textbox', { name: 'Venue', exact: true });
  const normalized = page.getByRole('table', { name: 'Normalized bars' });
  const hash = sourceIdentity(future).sha256;

  async function chooseFuture() {
    mockNative.choices.push(catalog);
    await control(page, 'Choose local CSV folder').last().click();
    await select(page, 'CSV file').selectOption(future.fileId);
  }

  await page.goto('/');
  await chooseFuture();
  expect(loads()).toHaveLength(0);
  expect(saves()).toHaveLength(0);
  await expect(save).toHaveCount(0);
  await load.click();
  await expect(page.getByText('This profile has no reviewed mapping for this file.')).toBeVisible();

  await control(page, 'Inspect raw rows').click();
  await expect(page.getByText(hash, { exact: true })).toBeVisible();
  await enterMapping(page, future, 'PrevSettlementPrice');
  await control(page, 'Preview mapping').click();
  await expect(page.getByText('Previous settlement price cannot be mapped to OHLC.', { exact: false }).first()).toBeVisible();
  await expect(save).toHaveCount(0);
  await close.selectOption('Close');
  await control(page, 'Preview mapping').click();
  await expect(normalized).toBeVisible();
  await expect(save).toBeVisible();
  expect(saves()).toHaveLength(0);

  const cancel = control(page, 'Cancel save');
  mockNative.cancelSaveAs.push('cancelled');
  await save.click();
  await expect.poll(() => saves().length).toBe(1);
  await cancel.click();
  await expect(page.getByText('Save cancelled.')).toBeVisible();
  expect(mockNative.calls.at(-1)).toEqual({
    command: 'cancel_local_import_preview', args: { requestId: saves()[0].args.requestId },
  });
  await load.click();
  await expect(page.getByText('This profile has no reviewed mapping for this file.')).toBeVisible();
  await expect(normalized).toBeVisible();

  await save.click();
  await expect(page.getByText('Reviewed mapping revision 1 saved for this profile.')).toBeVisible();
  expect(saves()).toHaveLength(2);
  for (const { args } of saves()) {
    expect(args.payload).toEqual(previews().at(-1).args.payload);
    expect(args.payload.expected_sha256).toBe(hash);
    expect(args.requestId).not.toBe(previews().at(-1).args.requestId);
  }
  expect(saves()[0].args.requestId).not.toBe(saves()[1].args.requestId);

  await page.reload();
  await chooseFuture();
  expect(loads()).toHaveLength(2);
  await load.click();
  await expect(page.getByText('Reviewed mapping revision 1 loaded. Preview it against the current file.')).toBeVisible();
  await expect(close).toHaveValue('Close');
  await expect(venue).toHaveValue(future.settings.venue);
  await expect(normalized).toHaveCount(0);
  await expect(save).toHaveCount(0);
  await control(page, 'Preview mapping').click();
  await expect(normalized).toBeVisible();
  expect(previews().at(-1).args.payload.expected_sha256).toBe(hash);
  mockNative.cancelSaveAs.push('committed');
  await save.click();
  await expect.poll(() => saves().length).toBe(3);
  await cancel.click();
  await expect(page.getByText('Reviewed mapping revision 2 saved for this profile.')).toBeVisible();
  expect(mockNative.calls.at(-1)).toEqual({
    command: 'cancel_local_import_preview', args: { requestId: saves()[2].args.requestId },
  });

  await expect(normalized).toBeVisible();
  await expect(save).toBeVisible();
  mockNative.invalidParts.set(future.fileId, 'header');
  await load.click();
  const alert = page.getByRole('alert');
  await expect(alert).toContainText('The CSV header could not be verified.');
  await expect(alert).toContainText(sourceIdentity(future, 'header').sha256);
  await expect(page.getByText(hash, { exact: true })).toHaveCount(0);
  await expect(normalized).toHaveCount(0);
  await expect(save).toHaveCount(0);
  await expect(close).toHaveValue('Close');

  mockNative.invalidParts.set(future.fileId, 'body');
  const bodyHash = sourceIdentity(future, 'body').sha256;
  await load.click();
  await expect(page.getByText('Reviewed mapping revision 2 loaded. Preview it against the current file.')).toBeVisible();
  await expect(page.getByRole('region', { name: 'Current source' })).toContainText(bodyHash);
  await expect(save).toHaveCount(0);
  await control(page, 'Preview mapping').click();
  await expect(page.getByRole('region', { name: 'Diagnostics' })).toContainText('Invalid UTF-8 in this CSV file.');
  await expect(page.getByRole('region', { name: 'Preview source' })).toContainText(bodyHash);
  expect(previews().at(-1).args.payload.expected_sha256).toBe(bodyHash);
  await expect(normalized).toHaveCount(0);
  await expect(save).toHaveCount(0);

  await venue.fill('Draft venue');
  mockNative.invalidParts.delete(future.fileId);
  mockNative.changedHeaders.add(future.fileId);
  await load.click();
  await expect(page.getByText('The CSV header has changed. Inspect the current raw rows and review the mapping.')).toBeVisible();
  await expect(venue).toHaveValue('Draft venue');
  await expect(close).toHaveValue('Close');
  await expect(normalized).toHaveCount(0);
  const changed = { ...future, header: [...future.header, 'New header'] };
  await expect(page.getByRole('region', { name: 'Current source' })).toContainText(sourceIdentity(changed).sha256);
  await expect(page.getByText(bodyHash, { exact: true })).toHaveCount(0);
  await expect(save).toHaveCount(0);
  expect(saves()).toHaveLength(3);
});

base('ordinary browser without native transport has no folder selection action', async ({ page }) => {
  await page.goto('/');
  await expect(control(page, 'Open offline sample')).toBeVisible();
  await expect(control(page, 'Choose local CSV folder')).toHaveCount(0);
  await expect(control(page, 'Open published CSV')).toHaveCount(0);
  await expect(page.locator('input[type=file]')).toHaveCount(0);
  const settings = await openPersonalSources(page);
  await expect(settings.getByText('Local CSV selection is available in the desktop app.')).toBeVisible();
  await expect(control(settings, 'Choose local CSV folder')).toHaveCount(0);
});

base('opens a CSV folder from Settings, previews a corrected header draft and resumes the same review', {
  tag: ['@profile-local-csv', '@csv-header-draft'],
}, async ({ page, baseURL }) => {
  const binary = process.env.SCOPEHUB_LOCAL_IMPORT_BRIDGE;
  base.skip(!binary, 'Set SCOPEHUB_LOCAL_IMPORT_BRIDGE to the portable Rust test example.');
  const unique = {
    settings: future.settings,
    columns: {
      symbol: 'Ticker', timestamp: 'Bar-Start', trading_day: 'TradeDate', open: 'Open_Price',
      high: '最高价', low: 'Low', close: 'Close', volume: 'Volume',
    },
  };
  const ambiguous = {
    settings: unique.settings,
    columns: { ...unique.columns, symbol: 'Vendor Symbol', low: 'Vendor Floor', close: 'CLOSE PRICE', volume: null },
  };
  const extraHeaders = ['SettlementPrice', 'PrevSettlementPrice', 'OpenInterest', 'Venue', 'Quantity.Unit', 'Price-Adjustment', 'TimeZone'];
  const records = [
    ['CU2605', '2026-04-14 21:05', '2026-04-15', '100', '106', '96', '102', '31', '105', '99', '45', 'TEST-FU', 'contracts', 'raw', 'Asia/Shanghai'],
    ['CU2605', '2026-04-14 21:10', '2026-04-15', '102', '108', '101', '105', '37', '107', '99', '46', 'TEST-FU', 'contracts', 'raw', 'UTC'],
    ['CU2605', '2026-04-14 21:15', '2026-04-15', '105', '109', '104', '106', '', '108', '99', '47', 'TEST-FU', 'contracts', 'raw', 'Asia/Shanghai'],
  ];
  const temporary = await mkdtemp(join(await realpath(tmpdir()), 'scopehub-csv-header-draft-'));
  const profile = join(temporary, 'profile');
  const folder = join(temporary, 'selected-csv');
  const writeCsv = (name, header, rows) => writeFile(join(folder, name), [header, ...rows].map(row => row.join(',')).join('\n') + '\n');
  let bridge;
  try {
    await mkdir(profile, { mode: 0o700 });
    await mkdir(folder, { mode: 0o700 });
    const header = [...Object.values(unique.columns), ...extraHeaders];
    await writeCsv('unique.csv', header, records);
    await writeCsv('ambiguous.csv', [
      ...header.map(column => column === 'Low' ? ambiguous.columns.low : column), ambiguous.columns.close, ambiguous.columns.symbol,
    ], records.map((row, index) => [...row, String(103 + 2 * index), 'CU2605-alt']));
    await writeCsv('invalid.csv', [...header, 'Close'], [[...records[0], '103']]);

    bridge = startLocalImportBridge(binary, profile, folder);
    const calls = [];
    const requests = [];
    let cancelNextPicker = false;
    page.on('request', request => requests.push(request.url()));
    await page.exposeBinding('testNativeInvoke', async (_, command, args) => {
      if (command === 'choose_local_csv_folder' && cancelNextPicker) {
        cancelNextPicker = false;
        const reply = { result: null };
        calls.push({ command, args, reply });
        return reply;
      }
      const reply = await bridge.request(command, args);
      calls.push({ command, args, reply });
      return reply;
    });
    await page.addInitScript(() => {
      window.isTauri = true;
      window.__TAURI_INTERNALS__ = {
        async invoke(command, args) {
          const reply = await window.testNativeInvoke(command, args);
          if (reply.error) throw reply.error;
          return reply.result;
        },
      };
    });

    const file = select(page, 'CSV file');
    const kind = select(page, 'Record kind');
    const field = key => select(page, columnFields[key]);
    const suggest = control(page, 'Draft mapping from headers');
    const preview = control(page, 'Preview mapping');
    const diagnostics = page.getByRole('region', { name: 'Diagnostics' });
    const normalized = page.getByRole('table', { name: 'Normalized bars' }).locator('tbody tr');
    const suggestion = page.getByRole('status').filter({ hasText: 'Review the column selections' });
    const previewCalls = () => calls.filter(call => call.args?.operation === 'market.previewLocalImport');
    async function openCsv(name) {
      await file.selectOption({ label: name });
      await kind.selectOption('native_ohlcv');
      await expect(suggest).toBeDisabled();
      await expect(suggestion).toHaveCount(0);
      await control(page, 'Inspect raw rows').click();
      await expect(diagnostics).toBeVisible();
    }
    async function applyDraft() {
      const count = calls.length;
      const requestCount = requests.length;
      await suggest.click();
      await expect(suggestion).toBeVisible();
      expect(calls).toHaveLength(count);
      expect(requests).toHaveLength(requestCount);
      await expect(control(page, 'Save reviewed mapping')).toHaveCount(0);
      await expect(control(page, 'Publish complete file')).toHaveCount(0);
    }
    async function mappedPreview() {
      const count = previewCalls().length;
      await preview.click();
      await expect.poll(() => previewCalls().length).toBe(count + 1);
      await expect(preview).toBeEnabled();
      const { args, reply } = previewCalls().at(-1);
      const { mapping_revision, ...result } = reply.result.preview;
      expect(mapping_revision).toBe(args.payload.mapping.revision);
      expect(result.file_sha256).toBe(args.payload.expected_sha256);
      return { mapping: args.payload.mapping, result };
    }

    await page.goto('/');
    const settings = await openPersonalSources(page);
    const chooseFolder = control(settings, 'Choose local CSV folder');
    expect(calls).toHaveLength(0);
    await chooseFolder.click();
    await expect(settings).toBeHidden();
    await openCsv('unique.csv');
    await applyDraft();
    for (const [key, column] of Object.entries(unique.columns)) await expect(field(key)).toHaveValue(column);
    await expect(suggestion.getByRole('list')).toHaveCount(0);
    const incomplete = await mappedPreview();
    expect(incomplete.mapping).not.toHaveProperty('timezone');
    expect(incomplete.mapping).not.toHaveProperty('venue');
    expect(incomplete.mapping).not.toHaveProperty('quantity_unit');
    expect(incomplete.mapping).not.toHaveProperty('price_adjustment');
    expect(incomplete.mapping).not.toHaveProperty('volume_semantics');
    expect(incomplete.mapping).not.toHaveProperty('timestamp_convention');
    expect(incomplete.result.issues.map(issue => issue.code)).toEqual(expect.arrayContaining([
      'declaration_required', 'volume_ambiguous', 'timestamp_convention', 'date_format',
    ]));
    await enterSeriesDeclarations(page, unique);
    const selected = await mappedPreview();
    expect(selected.result.rows.map(row => row.normalized?.volume)).toEqual([31, undefined, null]);
    expect(selected.result.rows[1].issues.map(issue => issue.code)).toEqual(['timezone_mismatch']);
    expect(selected.result.raw_only_columns).toEqual(expect.arrayContaining([
      { column: 'SettlementPrice', reason: 'Preserved for review as settlement price.' },
      { column: 'Quantity.Unit', reason: 'Preserved for review as declared quantity unit.' },
    ]));
    await expect(normalized).toHaveCount(2);
    await expect(normalized.last()).toContainText('Unknown');
    await select(page, 'Delimiter').selectOption(';');
    await expect(suggest).toBeDisabled();
    await expect(suggestion).toHaveCount(0);
    await select(page, 'Delimiter').selectOption(',');

    await openCsv('invalid.csv');
    await expect(diagnostics).toContainText('Use nonempty, unique CSV column headers.');
    await expect(suggest).toBeDisabled();

    await openCsv('ambiguous.csv');
    await enterSeriesDeclarations(page, ambiguous);
    await field('symbol').selectOption(ambiguous.columns.symbol);
    await field('volume').selectOption({ label: 'No volume column' });
    await applyDraft();
    await expect(field('symbol')).toHaveValue(ambiguous.columns.symbol);
    await expect(field('volume')).toHaveValue('No volume column');
    await expect(field('close')).toHaveValue('');
    await expect(field('low')).toHaveValue('');
    await expect(suggestion.getByRole('listitem')).toHaveText([
      'Low column · No recognized header', 'Close column · Choose among “Close”, “CLOSE PRICE”',
    ]);
    const unresolved = await mappedPreview();
    expect(unresolved.result.issues.map(issue => [issue.field, issue.code])).toEqual([
      ['low', 'column_required'], ['close', 'column_required'],
    ]);
    await field('low').selectOption(ambiguous.columns.low);
    await field('close').selectOption(ambiguous.columns.close);
    await expect(suggestion.getByRole('list')).toHaveCount(0);
    const corrected = await mappedPreview();
    expect(corrected.mapping.columns).toEqual(ambiguous.columns);
    expect(corrected.result.rows[0]).toMatchObject({ symbol: 'CU2605-alt', normalized: { close: 103, volume: null } });
    expect(corrected.result.rows[1].issues.map(issue => issue.code)).toEqual(['timezone_mismatch']);
    await expect(normalized).toHaveCount(2);
    const correctedRows = await normalized.allTextContents();
    const correctedDiagnostics = await diagnostics.textContent();

    const previewCount = previewCalls().length;
    await control(page, 'Back to chart').click();
    await expect(control(page, 'Settings')).toBeFocused();
    await openPersonalSources(page);
    cancelNextPicker = true;
    await chooseFolder.click();
    await expect(settings.getByRole('status')).toHaveText('Folder available in this session');
    await expect(chooseFolder).toBeFocused();
    await control(settings, 'Resume CSV review').click();
    await expect(file.locator('option:checked')).toHaveText('ambiguous.csv');
    await expect(field('close')).toHaveValue(ambiguous.columns.close);
    await expect(normalized).toHaveText(correctedRows);
    await expect(diagnostics).toHaveText(correctedDiagnostics);
    expect(previewCalls()).toHaveLength(previewCount);

    await file.selectOption({ label: 'unique.csv' });
    await openCsv('ambiguous.csv');
    await enterMapping(page, ambiguous);
    const manual = await mappedPreview();
    expect(manual.mapping.columns).toEqual(corrected.mapping.columns);
    expect(manual.result).toEqual(corrected.result);
    await expect(normalized).toHaveText(correctedRows);
    await expect(diagnostics).toHaveText(correctedDiagnostics);
    const picks = calls.filter(call => call.command === 'choose_local_csv_folder');
    expect(picks.map(call => call.reply.result?.display_name ?? null)).toEqual(['selected-csv', null]);
    expect(calls.filter(call => /saveLocalImportMapping|publishLocalImport/.test(call.args?.operation))).toEqual([]);
    expect(requests.every(url => new URL(url).origin === baseURL)).toBe(true);
  } finally {
    await bridge?.close();
    await rm(temporary, { recursive: true, force: true });
  }
});


base('built UI publishes all 502 native bars through Rust and discovers, reopens and revokes frozen revisions', {
  tag: '@profile-local-csv',
}, async ({ page, baseURL }, testInfo) => {
  const binary = process.env.SCOPEHUB_LOCAL_IMPORT_BRIDGE;
  base.skip(!binary, 'Set SCOPEHUB_LOCAL_IMPORT_BRIDGE to the portable Rust test example.');
  const temporary = await mkdtemp(join(await realpath(tmpdir()), 'scopehub-local-import-ui-'));
  const profile = join(temporary, 'profile');
  const folder = join(temporary, 'synthetic-csv');
  let bridge;
  let peer;
  const lateSelection = Promise.withResolvers();
  let holdPrevious = false;
  try {
    await mkdir(profile, { mode: 0o700 });
    await mkdir(folder, { mode: 0o700 });
    const file = join(folder, 'daily.csv');
    const heldFile = join(folder, 'held.csv');
    const header = [...equity.header, 'SettlementPrice', 'PrevSettlementPrice', 'OpenInterest', 'QuantityUnit', 'VendorMemo', 'is_closed'];
    const tailMemo = '尾盘 "official", checked\nSecond line from the source';
    const tailRecord = ['000731', '2025-05-16', '300', '306', '299', '305.00', '', '307.125', '299.25', '001234', ' Shares ', tailMemo, 'true'];
    const tailLocator = { record_number: 502, line_start: 504, line_end: 505 };
    const specialMemos = new Map([[2, 'Earlier memo\nContinued'], [499, 'x'.repeat(70_000)]]);
    const rows = Array.from({ length: 501 }, (_, index) => {
      const day = new Date(Date.UTC(2024, 0, index + 1)).toISOString().slice(0, 10);
      return ['000731', day, '36', '39', '34', '38', '120', '39', '37', '100', 'shares',
        specialMemos.get(index) ?? 'Original extra field', 'true'];
    });
    rows.push(tailRecord);
    const csv = records => '\uFEFF' + [header, ...records].map(row => row.map(field =>
      /[",\r\n]/.test(field) ? `"${field.replaceAll('"', '""')}"` : field,
    ).join(',')).join('\n') + '\n';
    const original = csv(rows);
    const hash = createHash('sha256').update(original).digest('hex');
    await writeFile(file, original);
    await writeFile(heldFile, csv([...rows.slice(0, -1), [...rows.at(-1).slice(0, -1), 'false']]));
    bridge = startLocalImportBridge(binary, profile, folder);
    const calls = [];
    const requestUrls = [];
    page.on('request', request => requestUrls.push(request.url()));
    await page.exposeBinding('testNativeInvoke', async (_, command, args) => {
      const reply = await bridge.request(command, args);
      calls.push({ command, args, reply });
      if (holdPrevious && args?.operation === readSourceRow && args.payload.locator.record_number === 501) {
        await lateSelection.promise;
      }
      return reply;
    });
    await page.addInitScript(operation => {
      window.isTauri = true;
      window.deliveredSourceRecords = [];
      window.__TAURI_INTERNALS__ = {
        async invoke(command, args) {
          const reply = await window.testNativeInvoke(command, args);
          if (reply.error) throw reply.error;
          if (args?.operation === operation) window.deliveredSourceRecords.push(args.payload.locator.record_number);
          return reply.result;
        },
      };
    }, readSourceRow);
    const operations = (name) => calls.filter(call => call.args?.operation === name);
    const publish = control(page, 'Publish complete file');
    const open = control(page, 'Open frozen chart');
    const published = page.getByRole('region', { name: 'Published local CSV' });
    const frozen = page.getByRole('region', { name: 'Frozen local CSV chart' });
    const provenance = page.getByRole('region', { name: 'Published source provenance' });
    const sample = page.getByRole('region', { name: 'Preview source' });
    const originalFields = page.getByRole('region', { name: 'Original CSV fields', exact: true });
    const inspectTail = control(provenance, 'Inspect record 502');

    async function readLastSourceRecord() {
      await provenance.getByText('Source rows (502)', { exact: true }).click();
      await provenance.getByRole('spinbutton', { name: 'Source record' }).fill('502');
      await control(provenance, 'Inspect source record').click();
    }

    async function previewAndSave() {
      await select(page, 'Encoding').selectOption('utf-8-sig');
      await select(page, 'Rows per preview').selectOption('1');
      await control(page, 'Inspect raw rows').click();
      await expect(sample).toContainText('Preview omits later records');
      await enterMapping(page, equity);
      await control(page, 'Preview mapping').click();
      await expect(page.getByRole('table', { name: 'Normalized bars' }).locator('tbody tr')).toHaveCount(1);
      await expect(publish).toHaveCount(0);
      await control(page, 'Save reviewed mapping').click();
      await expect(publish).toBeEnabled();
    }

    await page.goto('/');
    const settings = await openPersonalSources(page);
    await control(settings, 'Choose local CSV folder').click();
    await previewAndSave();
    await expect(page.getByText('Original extra field', { exact: true })).toBeVisible();
    await expect(page.getByText('Second line from the source', { exact: false })).toHaveCount(0);
    await publish.click();
    await expect(published).toContainText('000731 · 502 bars · daily.csv');
    const [first] = operations('market.publishLocalImport');
    const revision = first.reply.result.revision;
    const [mappingSave] = operations('market.saveLocalImportMapping');
    expect(await bridge.request('execute_operation', mappingSave.args)).toEqual(mappingSave.reply);
    expect(first.args.payload).toEqual({
      version: 1, folder_id: expect.any(String), file_id: expect.any(String), expected_sha256: hash, review_revision: 1,
    });
    expect(revision).toMatchObject({ source_sha256: hash, bar_count: 502, source_record_count: 502 });

    await publish.click();
    await expect.poll(() => operations('market.publishLocalImport').length).toBe(2);
    expect(operations('market.publishLocalImport')[1].reply.result.revision).toEqual(revision);
    await open.click();
    await expect(frozen.locator('.history')).toHaveAttribute('data-bars', '502');
    await expect(frozen.getByLabel('Selected bar')).toContainText('C 305');
    await expect(frozen.getByLabel('Selected bar')).toContainText('Volume Unknown');
    await expect.poll(() => painted(page, 'candlestick')).toBeGreaterThan(20);
    await expect(provenance).toContainText(revision.revision_id);
    await expect(provenance).toContainText(hash);
    await provenance.getByText('Source rows (502)', { exact: true }).click();
    const sourceRow = provenance.getByRole('table', { name: 'Published source rows' }).locator('tbody tr').first();
    await expect(sourceRow.getByRole('rowheader')).toHaveText('1');
    expect((await sourceRow.getByRole('cell').allTextContents()).slice(0, 4))
      .toEqual(['2', '2024-01-01T01:30:00.000Z', '2024-01-01', '38']);

    await writeFile(file, original.replace(',305.00,', ',304.00,').replace('307.125', '308.875'));
    await publish.click();
    await expect(page.getByRole('alert')).toContainText('The file changed since it was inspected.');
    expect(await bridge.request('execute_operation', first.args)).toEqual(first.reply);
    await expect(frozen.getByLabel('Selected bar')).toContainText('C 305');
    await select(page, 'CSV file').selectOption({ label: 'held.csv' });
    await previewAndSave();
    await publish.click();
    await expect(page.getByRole('alert')).toContainText('Review CSV record 502.');
    await expect(page.getByRole('alert')).toContainText('Source lines 504–505.');
    await expect(frozen.getByLabel('Selected bar')).toContainText('C 305');

    await select(page, 'CSV file').selectOption({ label: 'daily.csv' });
    await control(page, 'Load reviewed mapping').click();
    await expect(page.getByText('Reviewed mapping revision 1 loaded. Preview it against the current file.')).toBeVisible();
    await expect(publish).toHaveCount(0);
    await control(page, 'Preview mapping').click();
    await expect(publish).toBeEnabled();
    await publish.click();
    await expect(published.locator('code')).not.toHaveText(revision.revision_id);
    const replacement = operations('market.publishLocalImport').at(-1).reply.result.revision;
    expect(replacement.source_sha256).not.toBe(hash);
    expect(replacement.mapping_sha256).toBe(revision.mapping_sha256);
    expect(replacement.revision_id).not.toBe(revision.revision_id);
    await open.click();
    await expect(frozen.getByLabel('Selected bar')).toContainText('C 304');

    const run = (connection, operation, payload) => connection.request('execute_operation', {
      operation, requestId: randomUUID(), payload: { version: 1, ...payload },
    });
    const list = 'market.listLocalImportRevisions';
    const read = 'market.readLocalImportRevision';
    const revoke = 'market.revokeLocalImportRevision';
    const otherFolder = join(temporary, 'other-source');
    await mkdir(otherFolder);
    const otherPath = join(otherFolder, 'other.csv');
    const otherCsv = [future.header, ...future.rows.slice(0, 2).reverse()].map(row => row.join(',')).join('\n') + '\n';
    await writeFile(otherPath, otherCsv);
    peer = startLocalImportBridge(binary, join(temporary, 'other-profile'), otherFolder);
    const { result: catalog } = await peer.request('choose_local_csv_folder', {});
    const otherFile = {
      folder_id: catalog.folder_id, file_id: catalog.files[0].file_id,
      expected_sha256: createHash('sha256').update(otherCsv).digest('hex'),
    };
    const mapping = {
      version: 1, revision: 'night-session', encoding: 'utf-8', delimiter: ',', record_kind: 'native_ohlcv',
      timestamp_convention: 'bar_start', volume_semantics: 'per_bar', ...future.settings, columns: future.columns,
    };
    const otherPreview = { ...otherFile, mapping, row_limit: 1 };
    expect((await run(peer, 'market.previewLocalImport', otherPreview)).result.preview.rows[0].symbol).toBe('CU2605');
    const otherMapping = (await run(peer, 'market.saveLocalImportMapping', otherPreview)).result.record;
    const other = (await run(peer, 'market.publishLocalImport', { ...otherFile, review_revision: otherMapping.review_revision })).result.revision;
    const editedSource = otherCsv.replace(',99,true', ',98,true');
    await writeFile(otherPath, editedSource);
    const editedFile = { ...otherFile, expected_sha256: createHash('sha256').update(editedSource).digest('hex') };
    expect((await run(peer, 'market.previewLocalImport', { ...otherPreview, ...editedFile })).result.sha256).toBe(editedFile.expected_sha256);
    const sameBars = (await run(peer, 'market.publishLocalImport', { ...editedFile, review_revision: otherMapping.review_revision })).result.revision;
    expect(sameBars.revision_id).not.toBe(other.revision_id);
    expect(sameBars.source_sha256).not.toBe(other.source_sha256);
    const otherPage = (await run(peer, list, { after_revision_id: null, limit: 8, include_bar_summary: true })).result;
    expect(otherPage.revisions.map(entry => entry.revision_id).sort()).toEqual([other.revision_id, sameBars.revision_id].sort());
    for (const entry of otherPage.revisions) {
      expect(entry).toMatchObject({
        symbol: 'CU2605', timeframe: '5m', bar_count: 2,
        // The final bar timestamp is on April 14 UTC and its trading day is April 15.
        bar_summary: { venue: 'TEST-FU', first_trading_day: '2026-04-15', final_trading_day: '2026-04-15', final_close: 105 },
      });
    }
    expect((await run(peer, read, { revision_id: revision.revision_id })).error?.code).toBe('SNAPSHOT_UNAVAILABLE');
    await peer.close();
    peer = undefined;

    await bridge.close();
    await rename(folder, join(temporary, 'moved-source'));
    bridge = startLocalImportBridge(binary, profile, folder);
    const folderSelections = calls.filter(call => call.command === 'choose_local_csv_folder').length;
    await page.reload();
    const openDialog = control(page, 'Open published CSV');
    await expect(openDialog).toBeVisible();
    expect(operations(list)).toHaveLength(0);
    expect(operations(read)).toHaveLength(2);
    await page.setViewportSize({ width: 390, height: 800 });
    await openDialog.focus();
    await openDialog.press('Enter');
    const dialog = page.getByRole('dialog', { name: 'Open published CSV' });
    const library = dialog.getByRole('region', { name: 'Published revisions' });
    const rowsForFile = library.getByRole('button', { name: /Open daily\.csv/ });
    const firstEntry = library.getByRole('button', { name: /Open daily\.csv.*Final close 305 · Trading day 2025-05-16/ });
    const nextEntry = library.getByRole('button', { name: /Open daily\.csv.*Final close 304 · Trading day 2025-05-16/ });
    const refresh = library.getByRole('button', { name: 'Refresh revisions' });
    const idInput = dialog.getByRole('textbox', { name: 'Revision ID' });
    await expect(rowsForFile).toHaveCount(2);
    for (const [entry, digest] of [[firstEntry, hash], [nextEntry, replacement.source_sha256]]) {
      await expect(entry).toContainText('000731 · TEST-SH · 1d · 502 bars · 502 source rows');
      await expect(entry).toContainText('First bar trading day 2024-01-01');
      await expect(entry).toContainText(digest.slice(0, 12));
    }
    await expect(idInput).toHaveValue('');
    await expect(frozen).toHaveCount(0);
    expect(operations(read)).toHaveLength(2);
    expect(await dialog.evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
    await dialog.screenshot({ path: testInfo.outputPath('same-name-revision-labels.png') });

    const legacySummary = item => ({
      revision_id: item.revision_id, relative_path: 'daily.csv', symbol: '000731', timeframe: '1d',
      source_sha256: item.source_sha256, bar_count: 502, source_record_count: 502,
    });
    const legacy = [legacySummary(revision), legacySummary(replacement)].sort((left, right) =>
      left.revision_id < right.revision_id ? -1 : 1);
    const expected = legacy.map(entry => ({ ...entry, bar_summary: {
      venue: 'TEST-SH', first_trading_day: '2024-01-01', final_trading_day: '2025-05-16',
      final_close: entry.revision_id === revision.revision_id ? 305 : 304,
    } }));
    expect(operations(list)[0].reply.result).toEqual({ version: 1, revisions: expected, next_after_revision_id: null });
    const legacyQuery = { after_revision_id: null, limit: 8 };
    const legacyPage = { version: 1, revisions: legacy, next_after_revision_id: null };
    expect((await run(bridge, list, legacyQuery)).result).toEqual(legacyPage);
    expect((await run(bridge, list, { ...legacyQuery, include_bar_summary: false })).result).toEqual(legacyPage);
    const firstPageQuery = { after_revision_id: null, limit: 1, include_bar_summary: true };
    const firstPage = (await run(bridge, list, firstPageQuery)).result;
    expect(firstPage).toEqual({ version: 1, revisions: expected.slice(0, 1), next_after_revision_id: expected[0].revision_id });
    expect((await run(bridge, list, firstPageQuery)).result).toEqual(firstPage);
    expect((await run(bridge, list, { ...firstPageQuery, after_revision_id: firstPage.next_after_revision_id })).result).toEqual({
      version: 1, revisions: expected.slice(1), next_after_revision_id: null,
    });
    expect((await run(bridge, read, { revision_id: other.revision_id })).error?.code).toBe('SNAPSHOT_UNAVAILABLE');

    await firstEntry.focus();
    await expect(firstEntry).toBeFocused();
    await firstEntry.press('Enter');
    await expect(frozen.getByLabel('Selected bar')).toContainText('C 305');
    await expect(dialog.getByRole('region', { name: 'daily.csv' })).toBeFocused();
    await expect(provenance).toContainText(revision.revision_id);
    expect(calls.filter(call => call.command === 'choose_local_csv_folder')).toHaveLength(folderSelections);
    const reads = operations(read);
    expect(reads).toHaveLength(3);
    const replyDigest = reply => createHash('sha256').update(JSON.stringify(reply)).digest('hex');
    expect(replyDigest(reads[2].reply)).toBe(replyDigest(reads[0].reply));
    expect(reads[2].reply.result.bars.at(-1)).toMatchObject({
      bar: { close: 305, volume: null }, locators: [tailLocator],
    });
    expect(JSON.stringify(reads[2].reply)).not.toContain('307.125');

    await readLastSourceRecord();
    await expect(originalFields).toBeVisible();
    await expect(originalFields).toContainText('Record 502 · Source lines 504–505');
    const rawSelection = operations(readSourceRow).at(-1);
    expect(rawSelection.args.payload).toEqual({ version: 1, revision_id: revision.revision_id, locator: tailLocator });
    expect(rawSelection.reply.result).toEqual({
      version: 1, revision_id: revision.revision_id, source_sha256: hash, locator: tailLocator, header, raw_fields: tailRecord,
    });
    for (const value of ['SettlementPrice', 'PrevSettlementPrice', 'OpenInterest', 'QuantityUnit', '305.00', '307.125', '299.25', '001234']) {
      await expect(originalFields.getByText(value, { exact: true })).toBeVisible();
    }
    expect(await originalFields.getByText('Shares', { exact: true }).allTextContents()).toContain(' Shares ');
    const memo = originalFields.getByText('Second line from the source', { exact: false });
    await expect(memo).toBeVisible();
    expect(await memo.textContent()).toBe(tailMemo);
    await expect(frozen.getByLabel('Selected bar')).toContainText('C 305');
    expect(calls.filter(call => call.command === 'choose_local_csv_folder')).toHaveLength(folderSelections);

    // Rust authorizes record 501 before the browser switches back to record 502.
    holdPrevious = true;
    await control(provenance, 'Inspect record 501').click();
    await expect.poll(() => operations(readSourceRow).at(-1).reply.result?.locator.record_number).toBe(501);
    await expect(memo).toHaveCount(0);
    await inspectTail.click();
    await expect(memo).toBeVisible();
    lateSelection.resolve();
    await page.waitForFunction(() => window.deliveredSourceRecords.includes(501));
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await expect(memo).toBeVisible();
    await expect(originalFields.getByText('Original extra field', { exact: true })).toHaveCount(0);

    const readPayload = { revision_id: revision.revision_id, locator: tailLocator };
    const stale = await run(bridge, readSourceRow, {
      ...readPayload, locator: { ...tailLocator, line_end: tailLocator.line_start },
    });
    expect(stale.error?.code).toBe('STALE_CONTEXT');
    expect(stale.result).toBeUndefined();
    const oversized = await run(bridge, readSourceRow, {
      ...readPayload, locator: { record_number: 500, line_start: 502, line_end: 502 },
    });
    expect(oversized.error?.code).toBe('RESOURCE_LIMIT');
    expect(oversized.result).toBeUndefined();
    peer = startLocalImportBridge(binary, join(temporary, 'other-profile'), otherFolder);
    const outOfScope = await run(peer, readSourceRow, readPayload);
    expect(outOfScope.error?.code).toBe('SNAPSHOT_UNAVAILABLE');
    expect(outOfScope.result).toBeUndefined();
    await peer.close();
    peer = undefined;

    await control(page, 'Remove frozen chart access').click();
    await expect(dialog).toContainText('The original CSV copy stays in this profile.');
    await control(page, 'Keep access').click();
    await expect(frozen).toBeVisible();
    await control(page, 'Remove frozen chart access').click();
    await control(page, 'Remove access').click();
    await expect(page.getByText('Access removed for this published revision.', { exact: true })).toBeVisible();
    await expect(frozen).toHaveCount(0);
    await expect(originalFields).toHaveCount(0);
    await expect(memo).toHaveCount(0);
    const afterRevocation = await run(bridge, read, { revision_id: revision.revision_id });
    expect(afterRevocation.error?.code).toBe('PERMISSION_DENIED');
    const revokedRow = await run(bridge, readSourceRow, readPayload);
    expect(revokedRow.error?.code).toBe('PERMISSION_DENIED');
    expect(revokedRow.result).toBeUndefined();
    await refresh.click();
    await expect(rowsForFile).toHaveCount(1);
    await expect(firstEntry).toHaveCount(0);
    await expect(nextEntry).toBeVisible();
    await idInput.fill(revision.revision_id);
    await open.click();
    await expect(dialog.getByRole('alert')).toContainText('This profile does not have permission to display this revision.');
    await idInput.fill(other.revision_id);
    await open.click();
    await expect(dialog.getByRole('alert')).toContainText('This frozen chart is unavailable in the current profile.');
    await expect(nextEntry).toBeEnabled();
    await nextEntry.focus();
    await expect(nextEntry).toBeFocused();
    await nextEntry.press('Enter');
    await expect(frozen.getByLabel('Selected bar')).toContainText('C 304');
    await expect(provenance).toContainText(replacement.revision_id);
    await expect(originalFields).toHaveCount(0);
    await readLastSourceRecord();
    await expect(originalFields.getByText('304.00', { exact: true })).toBeVisible();
    await expect(originalFields.getByText('308.875', { exact: true })).toBeVisible();
    await expect(originalFields.getByText('307.125', { exact: true })).toHaveCount(0);
    await idInput.fill(replacement.revision_id);
    await open.click();
    await expect(frozen.getByLabel('Selected bar')).toContainText('C 304');
    await refresh.click();
    await expect(nextEntry).toBeVisible();
    await expect(frozen).toHaveCount(0);
    peer = startLocalImportBridge(binary, profile, folder);
    expect((await run(peer, revoke, { revision_id: replacement.revision_id })).result.revision_id).toBe(replacement.revision_id);
    await nextEntry.focus();
    await nextEntry.press('Enter');
    await expect(dialog.getByRole('alert')).toContainText('This profile does not have permission to display this revision.');
    expect(operations(read).at(-1).reply).toMatchObject({ error: { code: 'PERMISSION_DENIED', details: {} } });
    expect(operations(read).at(-1).reply.result).toBeUndefined();
    await expect(frozen).toHaveCount(0);
    await expect(nextEntry).toHaveCount(0);
    await refresh.click();
    await expect(library.getByRole('status')).toHaveText('No published CSV revisions are available in this profile.');
    expect((await run(bridge, list, legacyQuery)).result).toEqual({ ...legacyPage, revisions: [] });

    await bridge.close();
    const brokenProfile = join(temporary, 'read-error-profile');
    await mkdir(brokenProfile);
    const workspacePath = join(brokenProfile, 'workspace-dev');
    await writeFile(workspacePath, 'This fixture prevents the profile store from opening.');
    bridge = startLocalImportBridge(binary, brokenProfile, folder);
    await page.reload();
    await openDialog.click();
    await expect(library.getByRole('alert')).toHaveText('Unable to read published revisions. Try refreshing the list.');
    expect(operations(list).at(-1).reply.error?.code).toBe('INTERNAL_ERROR');
    await expect(idInput).toBeEnabled();
    await rm(workspacePath);
    await refresh.click();
    await expect(library.getByRole('status')).toHaveText('No published CSV revisions are available in this profile.');
    expect(calls.filter(call => call.command === 'choose_local_csv_folder')).toHaveLength(folderSelections);
    expect(requestUrls.every(url => new URL(url).origin === baseURL && !new URL(url).pathname.startsWith('/__scopehub/'))).toBe(true);
  } finally {
    lateSelection.resolve();
    await peer?.close();
    await bridge?.close();
    await rm(temporary, { recursive: true, force: true });
  }
});

base('peer revocation removes frozen editor content and an in-flight library revision', { tag: '@profile-local-csv' }, async ({ page, context }) => {
  const binary = process.env.SCOPEHUB_LOCAL_IMPORT_BRIDGE;
  base.skip(!binary, 'Set SCOPEHUB_LOCAL_IMPORT_BRIDGE to the portable Rust test example.');
  const temporary = await mkdtemp(join(await realpath(tmpdir()), 'scopehub-revoked-editor-'));
  const profile = join(temporary, 'profile');
  const folder = join(temporary, 'synthetic-csv');
  let bridge;
  let peer;
  const libraryPage = await context.newPage();
  const lateBody = Promise.withResolvers();
  const lateRow = Promise.withResolvers();
  let heldReply;
  let heldRow;
  try {
    await mkdir(profile, { mode: 0o700 });
    await mkdir(folder, { mode: 0o700 });
    const rawMemos = ['Revoked late CSV field', 'Previously visible CSV field'];
    const csv = [[...equity.header, 'Memo'], ...equity.rows.map((row, index) => [...row, rawMemos[index]])]
      .map(row => row.join(',')).join('\n') + '\n';
    const hash = createHash('sha256').update(csv).digest('hex');
    await writeFile(join(folder, 'daily.csv'), csv);
    bridge = startLocalImportBridge(binary, profile, folder);
    peer = startLocalImportBridge(binary, profile, folder);
    let revision;
    await page.exposeBinding('testNativeInvoke', async (_, command, args) => {
      const reply = await bridge.request(command, args);
      if (args?.operation === 'market.publishLocalImport') revision = reply.result?.revision;
      if (args?.operation === readSourceRow && args.payload.locator.record_number === 1) {
        heldRow = reply;
        await lateRow.promise;
      }
      return reply;
    });
    await libraryPage.exposeBinding('testNativeInvoke', async (_, command, args) => {
      const reply = await bridge.request(command, args);
      if (args?.operation === 'market.readLocalImportRevision') {
        heldReply = reply;
        await lateBody.promise;
      }
      return reply;
    });
    await context.addInitScript(operation => {
      window.isTauri = true;
      window.__TAURI_INTERNALS__ = {
        async invoke(command, args) {
          const reply = await window.testNativeInvoke(command, args);
          if (reply.error) throw reply.error;
          if (args?.operation === operation && args.payload.locator.record_number === 1) window.lateRowDelivered = true;
          return reply.result;
        },
      };
    }, readSourceRow);

    await page.goto('/');
    await control(page, 'Choose local CSV folder').last().click();
    await control(page, 'Inspect raw rows').click();
    await enterMapping(page, equity);
    await control(page, 'Preview mapping').click();
    await expect(page.getByRole('table', { name: 'Normalized bars' })).toBeVisible();
    await control(page, 'Save reviewed mapping').click();
    await control(page, 'Publish complete file').click();
    await expect(page.getByRole('region', { name: 'Published local CSV' })).toBeVisible();
    expect(revision.source_sha256).toBe(hash);
    await control(page, 'Open frozen chart').click();
    const chart = page.getByRole('region', { name: 'Frozen local CSV chart' });
    await expect(chart.locator('.history')).toHaveAttribute('data-bars', '2');
    await page.getByText('Source rows (2)', { exact: true }).click();
    await expect(page.getByRole('table', { name: 'Published source rows' })).toBeVisible();
    const rawFields = page.getByRole('region', { name: 'Original CSV fields', exact: true });
    await control(page, 'Inspect record 2').click();
    await expect(rawFields.getByText(rawMemos[1], { exact: true })).toBeVisible();
    await control(page, 'Inspect record 1').click();
    await expect.poll(() => heldRow?.result?.raw_fields.at(-1)).toBe(rawMemos[0]);
    await expect(rawFields.getByText(rawMemos[1], { exact: true })).toHaveCount(0);

    await libraryPage.goto('/');
    await control(libraryPage, 'Open published CSV').last().click();
    const dialog = libraryPage.getByRole('dialog', { name: 'Open published CSV' });
    const entry = dialog.getByRole('button', { name: /Open daily\.csv.*Final close/ });
    await entry.click();
    await expect.poll(() => heldReply?.result?.revision.revision_id).toBe(revision.revision_id);

    const revoked = await peer.request('execute_operation', {
      operation: 'market.revokeLocalImportRevision', requestId: randomUUID(),
      payload: { version: 1, revision_id: revision.revision_id },
    });
    expect(revoked.result.revision_id).toBe(revision.revision_id);
    await libraryPage.bringToFront();
    await libraryPage.evaluate(() => window.dispatchEvent(new Event('focus')));
    await expect(entry).toHaveCount(0, { timeout: 5000 });
    lateBody.resolve();
    await expect(dialog.getByRole('alert')).toContainText('This profile does not have permission to display this revision.');
    await expect(dialog.getByRole('region', { name: 'Frozen local CSV chart' })).toHaveCount(0);
    await expect(dialog.getByText(hash, { exact: true })).toHaveCount(0);
    await page.bringToFront();
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    await expect(chart).toHaveCount(0, { timeout: 3500 });
    lateRow.resolve();
    await page.waitForFunction(() => window.lateRowDelivered === true);
    await expect(rawFields).toHaveCount(0);
    await expect(page.getByRole('table', { name: 'Published source rows' })).toHaveCount(0);
    await expect(page.getByRole('table', { name: 'Normalized bars' })).toHaveCount(0);
    await expect(page.getByText(hash, { exact: true })).toHaveCount(0);
    await expect(control(page, 'Open frozen chart')).toHaveCount(0);

    await control(page, 'Inspect raw rows').click();
    await expect(page.getByRole('region', { name: 'Preview source' })).toContainText(hash);
    await expect(chart).toHaveCount(0);
  } finally {
    lateBody.resolve();
    lateRow.resolve();
    await libraryPage.close();
    await peer?.close();
    await bridge?.close();
    await rm(temporary, { recursive: true, force: true });
  }
});

base('stages additions, aliases, conflicts and withdrawals against a frozen local CSV without publishing', {
  tag: '@profile-folder-refresh',
}, async ({ page }) => {
  const binary = process.env.SCOPEHUB_LOCAL_IMPORT_BRIDGE;
  base.skip(!binary, 'Set SCOPEHUB_LOCAL_IMPORT_BRIDGE to the portable Rust test example.');
  const temporary = await mkdtemp(join(await realpath(tmpdir()), 'scopehub-folder-refresh-ui-'));
  const profile = join(temporary, 'profile');
  const folder = join(temporary, 'synthetic-csv');
  let bridge;
  let peer;
  let holdRefresh = false;
  const releaseRefresh = Promise.withResolvers();
  try {
    await mkdir(profile, { mode: 0o700 });
    await mkdir(folder, { mode: 0o700 });
    const csv = (rows, header = equity.header) => [header, ...rows].map(row => row.join(',')).join('\n') + '\n';
    const originalPath = join(folder, 'daily.csv');
    const original = csv([equity.rows[0], ...equity.rows]);
    await writeFile(originalPath, original);

    bridge = startLocalImportBridge(binary, profile, folder);
    const calls = [];
    const refreshes = () => calls.filter(call => call.args?.operation === 'market.previewLocalFolderRefresh');
    await page.exposeBinding('testNativeInvoke', async (_, command, args) => {
      const reply = command === 'cancel_local_import_preview' ? { result: null } : await bridge.request(command, args);
      calls.push({ command, args, reply });
      if (holdRefresh && args?.operation === 'market.previewLocalFolderRefresh') await releaseRefresh.promise;
      return reply;
    });
    await page.addInitScript(() => {
      window.isTauri = true;
      window.__TAURI_INTERNALS__ = {
        async invoke(command, args) {
          const reply = await window.testNativeInvoke(command, args);
          if (reply.error) throw reply.error;
          return reply.result;
        },
      };
    });

    await page.goto('/');
    await control(page, 'Choose local CSV folder').last().click();
    await control(page, 'Inspect raw rows').click();
    await enterMapping(page, equity);
    await control(page, 'Preview mapping').click();
    await control(page, 'Save reviewed mapping').click();
    await control(page, 'Publish complete file').click();
    await control(page, 'Open frozen chart').click();
    const frozen = page.getByRole('region', { name: 'Frozen local CSV chart' });
    await expect(frozen.getByLabel('Selected bar')).toContainText('C 40');
    const publication = calls.find(call => call.args?.operation === 'market.publishLocalImport').reply.result.revision;

    // These files did not exist when the authorized native picker ran.
    const newBar = ['000731', '2026-04-16', '40', '43', '39', '42', '200'];
    const correction = equity.rows[1].map((value, index) => index === 5 ? '41' : value);
    await writeFile(join(folder, 'alias.csv'), original);
    await writeFile(join(folder, 'added.csv'), csv([equity.rows[0], newBar]));
    await writeFile(join(folder, 'correction.csv'), csv([correction]));
    await writeFile(join(folder, 'held.csv'), csv(equity.rows, equity.header.map(name => name === 'Native close' ? 'Settled' : name)));

    const review = page.getByRole('region', { name: 'Folder refresh review' });
    const acceptedId = review.getByLabel('Accepted revision ID');
    const stage = control(page, 'Stage folder refresh');
    const changes = review.getByRole('table', { name: 'Folder source changes' });
    const file = name => changes.getByRole('rowheader', { name, exact: true }).locator('..');
    await expect(acceptedId).toHaveValue(publication.revision_id);
    await stage.click();
    await expect(file('added.csv')).toContainText('Added');
    await expect(file('alias.csv')).toContainText('Alias of daily.csv');
    await expect(file('held.csv')).toContainText('Header needs review');
    await review.getByText(/Competing values · Review required/).click();
    await expect(review.getByText(/Accepted.+C 40/)).toBeVisible();
    await expect(review.getByText(/Current.+C 41/)).toBeVisible();
    await expect(review.getByText('correction.csv · Record 1 · Lines 2–2')).toBeVisible();
    await review.getByText(/2026-04-16.+New bar/).click();
    await expect(review.getByText(/Current.+C 42/)).toBeVisible();

    await review.getByLabel('Include unchanged overlaps').check();
    await review.getByText(/2026-04-14.+Same bar/).click();
    for (const path of ['daily.csv', 'alias.csv']) {
      await expect(review.getByText(`${path} · Record 1 · Lines 2–2`)).toBeVisible();
      await expect(review.getByText(`${path} · Record 2 · Lines 3–3`)).toBeVisible();
    }
    await expect(review.getByText('added.csv · Record 1 · Lines 2–2')).toBeVisible();

    await acceptedId.fill(randomUUID());
    await expect(changes).toHaveCount(0);
    await acceptedId.fill(publication.revision_id);

    // Hold a successful native reply at the browser boundary until after cancelling the UI request.
    holdRefresh = true;
    await stage.click();
    await expect.poll(() => refreshes().length).toBe(2);
    expect(refreshes()[1].reply).toEqual(refreshes()[0].reply);
    await control(page, 'Cancel folder refresh').click();
    releaseRefresh.resolve();
    holdRefresh = false;
    await expect(page.getByText('Folder refresh cancelled.', { exact: true })).toBeVisible();
    await expect(changes).toHaveCount(0);
    await expect.poll(() => calls.find(call => call.command === 'cancel_local_import_preview')?.args)
      .toEqual({ requestId: refreshes()[1].args.requestId });

    await rm(originalPath);
    await rm(join(folder, 'correction.csv'));
    await stage.click();
    await expect(file('daily.csv')).toContainText('Removal candidate');
    await expect(file('daily.csv')).toContainText('Source absent · Review required');
    await expect(file('alias.csv')).toContainText('Alias of daily.csv (accepted source)');
    await expect(review.getByText(/2026-04-15.+Same bar/)).toBeVisible();

    await rm(join(folder, 'alias.csv'));
    await stage.click();
    await expect(review.getByText(/2026-04-15.+Coverage unverified · Review required/)).toBeVisible();

    await rm(join(folder, 'held.csv'));
    await stage.click();
    await review.getByText(/Withdrawal candidate/).click();
    await expect(review.getByText(/Accepted.+C 40/)).toBeVisible();
    await expect(frozen.getByLabel('Selected bar')).toContainText('C 40');
    expect(calls.filter(call => call.command === 'choose_local_csv_folder')).toHaveLength(1);
    expect(calls.filter(call => call.args?.operation === 'market.publishLocalImport')).toHaveLength(1);
    expect(refreshes()).toHaveLength(5);

    // A preview reopened by revision ID also reacts to a peer revoking access.
    await page.reload();
    await control(page, 'Choose local CSV folder').last().click();
    await expect(acceptedId).toHaveValue('');
    await acceptedId.fill(publication.revision_id);
    await stage.click();
    await expect(changes).toBeVisible();
    peer = startLocalImportBridge(binary, profile, folder);
    const revoked = await peer.request('execute_operation', {
      operation: 'market.revokeLocalImportRevision', requestId: randomUUID(),
      payload: { version: 1, revision_id: publication.revision_id },
    });
    expect(revoked.result.revision_id).toBe(publication.revision_id);
    await expect(changes).toHaveCount(0);
  } finally {
    releaseRefresh.resolve();
    await peer?.close();
    await bridge?.close();
    await rm(temporary, { recursive: true, force: true });
  }
});

base('rejects a late source declaration conflict before publishing a native CSV revision', {
  tag: '@profile-local-csv',
}, async () => {
  const binary = process.env.SCOPEHUB_LOCAL_IMPORT_BRIDGE;
  base.skip(!binary, 'Set SCOPEHUB_LOCAL_IMPORT_BRIDGE to the portable Rust test example.');
  const temporary = await mkdtemp(join(await realpath(tmpdir()), 'scopehub-csv-series-'));
  const folder = join(temporary, 'source');
  const profile = join(temporary, 'profile');
  let bridge;
  try {
    await mkdir(folder, { mode: 0o700 });
    await mkdir(profile, { mode: 0o700 });
    const cases = [
      ['timeframe', '5m', '1m'], ['timezone', 'Asia/Shanghai', 'UTC'], ['volume_semantics', 'per_bar', 'cumulative'],
      ['timestamp_convention', 'bar_start', 'bar_end'], ['calendar_id', future.settings.calendar_id, 'cash-only'],
      ['session_scope', future.settings.session_scope, 'day'], ['instrument_kind', 'dated_future', 'equity'],
      ['record_kind', 'native_ohlcv', 'quote_snapshot'], ['roll_method', '', 'front-month'],
      ['timestamp_format', future.settings.timestamp_format, '%Y-%m-%d %H:%S'],
      ['trading_day_format', future.settings.trading_day_format, '%Y-%d-%m'],
    ];
    const header = [...future.header, ...cases.map(([field]) => field)];
    const reviewed = cases.map(([, expected]) => expected);
    const writeSource = async declarations => {
      const rows = [header, [...future.rows[0], ...reviewed], [...future.rows[1], ...declarations]];
      const csv = rows.map(fields => fields.join(',')).join('\n') + '\n';
      await writeFile(join(folder, 'night.csv'), csv);
      return createHash('sha256').update(csv).digest('hex');
    };
    const hash = await writeSource(reviewed);
    bridge = startLocalImportBridge(binary, profile, folder);
    const run = (operation, payload) => bridge.request('execute_operation', {
      operation, requestId: randomUUID(), payload: { version: 1, ...payload },
    });
    const { result: catalog } = await bridge.request('choose_local_csv_folder', {});
    const file = { folder_id: catalog.folder_id, file_id: catalog.files[0].file_id, expected_sha256: hash };
    const mapping = {
      version: 1, revision: 'night-session', encoding: 'utf-8', delimiter: ',', record_kind: 'native_ohlcv',
      timestamp_convention: 'bar_start', volume_semantics: 'per_bar', ...future.settings, columns: future.columns,
    };
    const initialPreview = { ...file, mapping, row_limit: 1 };
    expect((await run('market.previewLocalImport', initialPreview)).result.preview.rows[0].symbol).toBe('CU2605');
    const saved = await run('market.saveLocalImportMapping', initialPreview);
    expect(saved.error).toBeUndefined();
    const publish = source => run('market.publishLocalImport', {
      ...source, review_revision: saved.result.record.review_revision,
    });
    const { result: { revision } } = await publish(file);
    const read = () => run('market.readLocalImportRevision', { revision_id: revision.revision_id });
    const accepted = await read();
    expect(accepted.result.bars[1].bar).toMatchObject({ time_ms: Date.parse(future.times[1]), volume: 37 });
    for (const [index, [field, , value]] of cases.entries()) {
      const declarations = [...reviewed];
      declarations[index] = value;
      const amended = { ...file, expected_sha256: await writeSource(declarations) };
      const { result: { preview } } = await run('market.previewLocalImport', { ...amended, mapping, row_limit: 1 });
      expect(preview.rows).toHaveLength(1);
      expect(preview.rows[0].normalized).toMatchObject({ time_ms: Date.parse(future.times[0]), volume: 31 });
      const rejected = await publish(amended);
      expect(rejected.error).toMatchObject({
        code: 'DATA_UNAVAILABLE',
        details: { reason: 'csv_review_required', issue_code: field + '_mismatch', field,
          locator: { record_number: 2, line_start: 3, line_end: 3 } },
      });
      expect(rejected.error.message).toContain("'" + value + "'");
      const full = await run('market.previewLocalImport', { ...amended, mapping, row_limit: 2 });
      const last = full.result.preview.rows[1];
      expect(last).toMatchObject({ normalized: null, record_number: 2, issues: [{ field, code: field + '_mismatch' }] });
      expect(last.raw_fields.at(future.header.length + index)).toBe(value);
    }
    const { result: listed } = await run('market.listLocalImportRevisions', { after_revision_id: null, limit: 8 });
    expect(listed.revisions.map(item => item.revision_id)).toEqual([revision.revision_id]);
    expect(await read()).toEqual(accepted);
  } finally {
    await bridge?.close();
    await rm(temporary, { recursive: true, force: true });
  }
});

base('reviews explicit CSV declarations and competing bars with source provenance', {
  tag: '@profile-folder-refresh',
}, async ({ page }) => {
  const binary = process.env.SCOPEHUB_LOCAL_IMPORT_BRIDGE;
  base.skip(!binary, 'Set SCOPEHUB_LOCAL_IMPORT_BRIDGE to the portable Rust test example.');
  const temporary = await mkdtemp(join(await realpath(tmpdir()), 'scopehub-folder-economics-ui-'));
  const profile = join(temporary, 'profile');
  const folder = join(temporary, 'synthetic-csv');
  let bridge;
  try {
    await mkdir(profile, { mode: 0o700 });
    await mkdir(folder, { mode: 0o700 });
    const declarations = {
      quantity_unit: 'shares', price_adjustment: 'raw', venue: equity.settings.venue, timeframe: '1d', timezone: 'Asia/Shanghai', volume_semantics: 'per_bar',
      timestamp_convention: 'bar_start', calendar_id: equity.settings.calendar_id, session_scope: 'day', instrument_kind: 'equity',
      daily_start_time: '09:30', record_kind: 'native_ohlcv', roll_method: '',
    };
    const row = (close, changes = {}) => [
      '000731', '2026-04-14', '4', '8', '3', String(close), '20', ...Object.values({ ...declarations, ...changes }),
    ];
    const legacyHeaders = { quantity_unit: 'Quantity.Unit', price_adjustment: 'Price.Adjustment', venue: 'Ve:nue' };
    const header = [...equity.header, ...Object.keys(declarations).map(field => legacyHeaders[field] ?? field)];
    const csv = (...rows) => [header, ...rows].map(fields => fields.join(',')).join('\n') + '\n';
    await writeFile(join(folder, 'daily.csv'), csv(row(5)));

    bridge = startLocalImportBridge(binary, profile, folder);
    const calls = [];
    await page.exposeBinding('testNativeInvoke', async (_, command, args) => {
      const reply = await bridge.request(command, args);
      calls.push({ args, reply });
      return reply;
    });
    await page.addInitScript(() => {
      window.isTauri = true;
      window.__TAURI_INTERNALS__ = {
        async invoke(command, args) {
          const reply = await window.testNativeInvoke(command, args);
          if (reply.error) throw reply.error;
          return reply.result;
        },
      };
    });
    const resultOf = operation => calls.filter(call => call.args?.operation === operation).at(-1)?.reply.result;

    await page.goto('/');
    await control(page, 'Choose local CSV folder').last().click();
    await control(page, 'Inspect raw rows').click();
    await enterMapping(page, equity);
    await control(page, 'Preview mapping').click();
    await control(page, 'Save reviewed mapping').click();
    await control(page, 'Publish complete file').click();
    await control(page, 'Open frozen chart').click();
    const frozen = page.getByRole('region', { name: 'Frozen local CSV chart' });
    await expect(frozen.getByLabel('Selected bar')).toContainText('C 5');

    const conflictingFiles = [
      ['lots.csv', { quantity_unit: 'lots' }], ['adjusted.csv', { price_adjustment: 'split_adjusted' }], ['venue.csv', { venue: 'X-OTHER' }],
      ['minute.csv', { timeframe: '1m' }], ['utc.csv', { timezone: 'UTC' }],
      ['cumulative.csv', { volume_semantics: 'cumulative' }], ['bar-end.csv', { timestamp_convention: 'bar_end' }],
      ['calendar.csv', { calendar_id: 'futures-calendar' }], ['session.csv', { session_scope: 'night' }],
      ['kind.csv', { instrument_kind: 'dated_future' }], ['clock.csv', { daily_start_time: '09:31' }],
      ['snapshot.csv', { record_kind: 'quote_snapshot' }], ['roll.csv', { roll_method: 'back-adjusted' }],
    ];
    for (const [name, declarations] of conflictingFiles) {
      await writeFile(join(folder, name), csv(row(5, declarations)));
    }
    await writeFile(join(folder, 'competing.csv'), csv(row(6), row(7)));

    const review = page.getByRole('region', { name: 'Folder refresh review' });
    await control(page, 'Stage folder refresh').click();
    await expect(review.getByRole('table', { name: 'Folder source changes' })).toBeVisible();

    const staged = resultOf('market.previewLocalFolderRefresh');
    expect(staged.review_id).toMatch(/^[0-9a-f]{64}$/);
    expect(staged.publication_allowed).toBe(false);
    await expect(review.getByRole('button', { name: 'Review folder publication' })).toHaveCount(0);
    for (const [path, declarations] of conflictingFiles) {
      const file = staged.files.find(source => source.relative_path === path);
      expect.soft(file, `${path} requires an explicit declaration review`).toMatchObject({ review: 'invalid_rows' });
      expect.soft(file?.detail, `${path} identifies its contradictory declaration`).toContain(Object.values(declarations)[0]);
    }
    const bar = staged.bars.find(item => item.time_ms === Date.parse(equity.times[0]));
    expect.soft(bar.status, 'same-file corrected prices require review').toBe('conflict');
    expect.soft(bar.accepted.bar.close).toBe(5);
    expect.soft(bar.accepted.locators).toEqual([{ record_number: 1, line_start: 2, line_end: 2 }]);
    const alternatives = Object.fromEntries(bar.alternatives.map(entry => [entry.bar.close,
      entry.sources.map(source => [source.relative_path, source.record_number, source.line_start, source.line_end])]));
    expect.soft(alternatives, 'all prices have attributable rows, with contradictory files excluded').toEqual({
      5: [['daily.csv', 1, 2, 2]],
      6: [['competing.csv', 1, 2, 2]],
      7: [['competing.csv', 2, 3, 3]],
    });

    await review.getByLabel('Include unchanged overlaps').check();
    await review.getByText(/2026-04-14T01:30:00/).click();
    const shown = await review.innerText();
    expect.soft(shown).toContain('Competing values · Review required');
    expect.soft(shown).toMatch(/Accepted.+C 5.+Volume 20 shares/);
    expect.soft(shown).toMatch(/Current.+C 6.+Volume 20 shares/);
    expect.soft(shown).toMatch(/Current.+C 7.+Volume 20 shares/);
    expect.soft(shown).toContain('competing.csv · Record 1 · Lines 2–2');
    expect.soft(shown).toContain('competing.csv · Record 2 · Lines 3–3');
    expect.soft(shown).toContain('Venue TEST-SH');
    expect.soft(shown).toContain('Price adjustment raw');

    const published = resultOf('market.publishLocalImport').revision;
    const after = await bridge.request('execute_operation', {
      operation: 'market.readLocalImportRevision', requestId: randomUUID(),
      payload: { version: 1, revision_id: published.revision_id },
    });
    expect(after.result.bars.map(entry => entry.bar.close)).toEqual([5]);
    await expect(frozen.getByLabel('Selected bar')).toContainText('C 5');
  } finally {
    await bridge?.close();
    await rm(temporary, { recursive: true, force: true });
  }
});

base('edits and reuses a futures mapping, while quotes and unsupported sources stay out of the chart', {
  tag: '@profile-folder-import',
}, async ({ page, baseURL }) => {
  const binary = process.env.SCOPEHUB_LOCAL_IMPORT_BRIDGE;
  base.skip(!binary, 'Set SCOPEHUB_LOCAL_IMPORT_BRIDGE to the portable Rust test example.');
  const temporary = await mkdtemp(join(await realpath(tmpdir()), 'scopehub-folder-mapping-ui-'));
  const profile = join(temporary, 'profile');
  const folder = join(temporary, 'synthetic-csv');
  let bridge;
  try {
    await mkdir(profile, { mode: 0o700 });
    await mkdir(folder, { mode: 0o700 });
    const futurePath = join(folder, 'night.csv');
    const datedRows = future.rows.slice(0, 2);
    const bomCsv = (header, rows = datedRows) => '\uFEFF' + [header, ...rows].map(row => row.join(';')).join('\r\n') + '\r\n';
    const original = bomCsv(future.header);
    const digest = createHash('sha256').update(original).digest('hex');
    const continuous = { ...future, settings: { ...future.settings, instrument_kind: 'continuous_future' } };
    const continuousRows = datedRows.map(([, ...fields]) => ['CU-CONT', ...fields]);
    const roll = 'Synthetic source roll by session';
    const quotes = [
      ['Contract', 'TradingDay', 'UpdateTime', 'LastPrice', 'VolumeTotal', 'BidPrice'],
      ['CU2605', '20260415', '21:00:01', '85.5', '100', '85.4'],
      ['CU2605', '20260415', '21:00:01', '85.6', '102', '85.5'],
    ];
    await writeFile(futurePath, original);
    await writeFile(join(folder, 'continuous.csv'), bomCsv(continuous.header, continuousRows));
    await writeFile(join(folder, 'quotes.csv'), quotes.map(row => row.join(',')).join('\n') + '\n');
    const trades = [
      ['Contract', 'TradingDay', 'Time', 'TradePrice', 'TradeQuantity'],
      ['CU2605', '20260415', '21:00:01', '85.60', '02'],
      ['CU2605', '20260415', '21:00:01', '85.65', '03'],
    ];
    await writeFile(join(folder, 'trade-style.csv'), trades.map(row => row.join(',')).join('\n') + '\n');
    await writeFile(join(folder, 'unsupported.tsv'), 'Raw trade values\t123\n');
    await writeFile(join(temporary, 'outside.csv'), 'Private sibling\n12\n');

    bridge = startLocalImportBridge(binary, profile, folder);
    const calls = [];
    const urls = [];
    const last = operation => calls.findLast(call => call.args?.operation === `market.${operation}`);
    page.on('request', request => urls.push(request.url()));
    await page.exposeBinding('testNativeInvoke', async (_, command, args) => {
      const reply = await bridge.request(command, args);
      calls.push({ args, reply });
      return reply;
    });
    await page.addInitScript(() => {
      window.isTauri = true;
      window.__TAURI_INTERNALS__ = {
        async invoke(command, args) {
          const reply = await window.testNativeInvoke(command, args);
          if (reply.error) throw reply.error;
          return reply.result;
        },
      };
    });

    const file = select(page, 'CSV file');
    const raw = page.getByRole('table', { name: 'Raw CSV rows' });
    const normalized = page.getByRole('table', { name: 'Normalized bars' });
    const preview = page.getByRole('region', { name: 'Local CSV chart preview' });
    const publish = control(page, 'Publish complete file');
    const runPreview = control(page, 'Preview mapping');
    const diagnostics = page.getByRole('region', { name: 'Diagnostics' });
    async function inspectBomSource(label) {
      await file.selectOption({ label });
      await select(page, 'Encoding').selectOption('utf-8-sig');
      await select(page, 'Delimiter').selectOption(';');
      await control(page, 'Inspect raw rows').click();
      await expect(raw.getByRole('columnheader', { name: 'Contract', exact: true })).toBeVisible();
    }

    await page.goto('/');
    await control(page, 'Choose local CSV folder').last().click();
    await expect(file.getByRole('option', { name: 'unsupported.tsv (unsupported format)' })).toHaveJSProperty('disabled', true);
    await expect(file.getByRole('option', { name: 'outside.csv' })).toHaveCount(0);
    await inspectBomSource('night.csv');
    const admitted = last('previewLocalImport');
    expect(admitted.reply.result.sha256).toBe(digest);
    const denied = await bridge.request('execute_operation', {
      operation: 'market.previewLocalImport', requestId: randomUUID(),
      payload: { ...admitted.args.payload, file_id: randomUUID() },
    });
    expect(denied).toMatchObject({ error: { code: 'PERMISSION_DENIED' } });
    expect(denied.result).toBeUndefined();

    await enterMapping(page, future, 'PrevSettlementPrice');
    await runPreview.click();
    await expect(diagnostics).toContainText('is previous settlement price');
    await expect(normalized).toHaveCount(0);
    await expect(preview.locator('canvas')).toHaveCount(0);
    await select(page, 'Close column').selectOption('Close');
    await runPreview.click();
    await expect(normalized.locator('tbody tr').first().getByRole('cell')).toHaveText([
      '2', 'CU2605', '2026-04-14T13:05:00.000Z', '2026-04-15', '100', '106', '96', '102', '31',
    ]);
    await expect(preview.getByRole('img', { name: /Candlestick and volume chart for CU2605/ })).toBeVisible();
    await expect(preview).toContainText('Dated futures contract');
    await expect(preview).toContainText('Volume in contracts');
    await expect.poll(() => painted(page, 'candlestick')).toBeGreaterThan(20);

    await inspectBomSource('continuous.csv');
    await enterMapping(page, continuous);
    await runPreview.click();
    await expect(diagnostics).toContainText('Declare the source roll method.');
    await expect(normalized).toHaveCount(0);
    await expect(preview.locator('canvas')).toHaveCount(0);
    await page.getByRole('textbox', { name: 'Roll method', exact: true }).fill(roll);
    await runPreview.click();
    await expect(preview.getByRole('img', { name: /Candlestick and volume chart for CU-CONT/ })).toBeVisible();
    await expect(preview).toContainText('Continuous futures series');
    await expect(preview).toContainText(`Roll method ${roll}`);
    expect(last('previewLocalImport').reply.result.preview.series).toMatchObject({
      instrument_kind: 'continuous_future', roll_method: roll,
    });

    await inspectBomSource('night.csv');
    await enterMapping(page, future);
    await runPreview.click();
    await expect(preview).toContainText('Dated futures contract');
    await control(page, 'Save reviewed mapping').click();
    await expect(publish).toBeEnabled();
    expect(last('saveLocalImportMapping').reply.result.record).toMatchObject({
      review_revision: 1, mapping: { encoding: 'utf-8-sig', delimiter: ';', columns: { close: 'Close' } },
    });
    await publish.click();
    await expect(page.getByRole('region', { name: 'Published local CSV' })).toContainText('CU2605 · 2 bars · night.csv');
    await control(page, 'Load reviewed mapping').click();
    await expect(page.getByText('Reviewed mapping revision 1 loaded. Preview it against the current file.')).toBeVisible();

    const changedHeader = future.header.map(column => column === 'Close' ? 'Final bar price' : column);
    await writeFile(futurePath, bomCsv(changedHeader));
    await control(page, 'Load reviewed mapping').click();
    await expect(page.getByText('The CSV header has changed. Inspect the current raw rows and review the mapping.')).toBeVisible();
    expect(last('loadLocalImportMapping').reply.result.status).toBe('review_required');
    await expect(publish).toHaveCount(0);
    await control(page, 'Inspect raw rows').click();
    await expect(raw.getByRole('columnheader', { name: 'Final bar price' })).toBeVisible();
    await select(page, 'Close column').selectOption('Final bar price');
    await runPreview.click();
    await expect(normalized.locator('tbody tr')).toHaveCount(2);
    await control(page, 'Save reviewed mapping').click();
    await expect(page.getByText('Reviewed mapping revision 2 saved for this profile.')).toBeVisible();
    await control(page, 'Load reviewed mapping').click();
    await expect(page.getByText('Reviewed mapping revision 2 loaded. Preview it against the current file.')).toBeVisible();
    await expect(select(page, 'Close column')).toHaveValue('Final bar price');

    await control(page, 'Open frozen chart').click();
    const frozen = page.getByRole('region', { name: 'Frozen local CSV chart' });
    const provenance = page.getByRole('region', { name: 'Published source provenance' });
    await expect(frozen.getByLabel('Selected bar')).toContainText('C 105');
    await expect(provenance).toContainText(digest);
    await provenance.getByText('Source rows (2)', { exact: true }).click();
    await control(provenance, 'Inspect record 1').click();
    const originalFields = page.getByRole('region', { name: 'Original CSV fields', exact: true });
    await expect(originalFields.getByRole('row', { name: 'PrevSettlementPrice 99' })).toBeVisible();
    await expect(originalFields.getByRole('row', { name: 'Close 102', exact: true })).toBeVisible();
    await expect(originalFields.getByRole('rowheader', { name: 'Final bar price' })).toHaveCount(0);
    await control(page, 'Close frozen chart').click();

    await file.selectOption({ label: 'quotes.csv' });
    await select(page, 'Record kind').selectOption('quote_snapshot');
    await control(page, 'Inspect raw rows').click();
    await expect(raw.getByRole('columnheader', { name: 'BidPrice' })).toBeVisible();
    await expect(diagnostics).toContainText('Quote snapshots stay raw.');
    const quoteRows = raw.locator('tbody tr');
    for (const [index, fields] of quotes.slice(1).entries()) {
      await expect(quoteRows.nth(index).getByRole('cell')).toHaveText([String(index + 2), ...fields]);
    }
    const quotePreview = last('previewLocalImport');
    expect(quotePreview.args.payload.mapping.record_kind).toBe('quote_snapshot');
    expect(quotePreview.reply.result.preview.rows.map(row => row.normalized)).toEqual([null, null]);
    await expect(normalized).toHaveCount(0);
    await expect(preview.locator('canvas')).toHaveCount(0);
    await expect(publish).toHaveCount(0);
    await file.selectOption({ label: 'trade-style.csv' });
    await expect(select(page, 'Record kind')).toHaveValue('unclassified');
    await control(page, 'Inspect raw rows').click();
    await expect(raw.getByRole('columnheader', { name: 'TradeQuantity' })).toBeVisible();
    await expect(raw.getByRole('columnheader', { name: 'BidPrice' })).toHaveCount(0);
    await expect(diagnostics).toContainText('Classify this input.');
    await select(page, 'Record kind').selectOption({ label: 'Executed trades' });
    await control(page, 'Inspect raw rows').click();
    await expect(diagnostics).toContainText('Executed trades are shown in source order');
    const tradeRows = raw.locator('tbody tr');
    for (const [index, fields] of trades.slice(1).entries()) {
      await expect(tradeRows.nth(index).getByRole('cell')).toHaveText([String(index + 2), ...fields]);
    }
    const tradePreview = last('previewLocalImport');
    expect(tradePreview.args.payload.mapping.record_kind).toBe('trade');
    expect(tradePreview.reply.result.preview.issues.map(issue => issue.code)).toEqual(['trade']);
    expect(tradePreview.reply.result.preview.rows.map(row => row.normalized)).toEqual([null, null]);
    await expect(publish).toHaveCount(0);
    await expect(normalized).toHaveCount(0);
    await expect(preview.locator('canvas')).toHaveCount(0);
    await expect(page.getByText('Simulated', { exact: true })).toHaveCount(0);
    expect(urls.every(url => new URL(url).origin === baseURL)).toBe(true);
  } finally {
    await bridge?.close();
    await rm(temporary, { recursive: true, force: true });
  }
});

base('keeps the selected folder library independent of an admitted CSV listing', {
  tag: '@profile-folder-refresh',
}, async ({ page }) => {
  const releaseCsv = Promise.withResolvers();
  const calls = [];
  const csvList = 'market.listLocalImportRevisions';
  const folderList = 'market.listLocalFolderRevisions';
  const emptyPage = { version: 1, revisions: [], next_after_revision_id: null };
  const revision = {
    revision_id: randomUUID(), base_revision_id: randomUUID(), parent_revision_id: null,
    origin_path: 'daily.csv', symbol: '000731', source_count: 2, bar_count: 1,
    series: { venue: 'TEST-SH', timeframe: '1d' },
    bar_summary: { final_close: 39, final_trading_day: '2026-04-14', first_trading_day: '2026-04-14' },
  };
  let holdCsv = true;
  let rejectFolder = false;
  await page.exposeBinding('testNativeInvoke', async (_, command, args) => {
    if (command !== 'execute_operation' || ![csvList, folderList].includes(args?.operation)) {
      return { error: { code: 'PROVIDER_NOT_CONFIGURED' } };
    }
    calls.push(args.operation);
    if (args.operation === csvList && holdCsv) {
      holdCsv = false;
      await releaseCsv.promise;
      return { error: { code: 'INTERNAL_ERROR' } };
    }
    if (args.operation === csvList) return { result: emptyPage };
    if (rejectFolder) return { error: { code: 'PERMISSION_DENIED' } };
    return { result: { ...emptyPage, revisions: [revision] } };
  });
  await page.addInitScript(() => {
    window.isTauri = true;
    window.__TAURI_INTERNALS__ = {
      async invoke(command, args) {
        const reply = await window.testNativeInvoke(command, args);
        if (reply.error) throw reply.error;
        return reply.result;
      },
    };
  });

  try {
    await page.goto('/');
    await control(page, 'Open published CSV').last().click();
    const dialog = page.getByRole('dialog', { name: 'Open published CSV' });
    const folder = dialog.getByRole('button', { name: 'Folder revisions' });
    await expect.poll(() => calls).toEqual([csvList]);
    await folder.click({ timeout: 2500 });
    await expect(folder).toHaveAttribute('aria-pressed', 'true');
    await expect(dialog.getByText('Loading published revisions…')).toBeVisible();
    expect(calls).toEqual([csvList]);

    releaseCsv.resolve();
    const entry = dialog.getByRole('button', { name: /Open folder revision from daily.csv/ });
    await expect(entry).toContainText('Final close 39');
    await expect(dialog.getByRole('alert')).toHaveCount(0);
    expect(calls).toEqual([csvList, folderList]);

    rejectFolder = true;
    await dialog.getByRole('button', { name: 'Refresh revisions' }).click();
    await expect(dialog.getByRole('alert')).toHaveText('Unable to read published revisions. Try refreshing the list.');
    await expect(entry).toHaveCount(0);
    await expect(folder).toHaveAttribute('aria-pressed', 'true');
    await dialog.getByRole('button', { name: 'Single-file revisions' }).click();
    await expect(dialog.getByText('No published CSV revisions are available in this profile.')).toBeVisible();
    await expect(dialog.getByRole('alert')).toHaveCount(0);
    expect(calls).toEqual([csvList, folderList, folderList, csvList]);
  } finally {
    releaseCsv.resolve();
  }
});

base('keeps the selected folder library available during workspace startup and refresh', {
  tag: '@profile-folder-refresh',
}, async ({ page }) => {
  const releaseSnapshot = Promise.withResolvers();
  const releaseRefresh = Promise.withResolvers();
  const releaseBusyRetry = Promise.withResolvers();
  const workspaceId = randomUUID();
  const calls = [];
  const clearRequests = [];
  let openingWorkspace = true;
  let writerBusy = true;
  await page.exposeBinding('testNativeInvoke', async (_, command, args) => {
    expect(command).toBe('execute_operation');
    if (args?.payload?.type === 'snapshot') {
      const refresh = calls.includes('snapshot');
      openingWorkspace = true;
      calls.push('snapshot');
      await (refresh ? releaseRefresh : releaseSnapshot).promise;
      openingWorkspace = false;
      return { result: { workspace: { id: workspaceId, revision: 0, timeframe: '1d' },
        selection_revision: refresh ? 4 : 3, undo: null, chart_evidence: { unavailable: true } } };
    }
    calls.push(args?.operation);
    const busy = { error: { code: 'BUSY', message: 'The market worker is busy.', retryable: true } };
    if (args.operation === 'workspace.selectEvidence') {
      const request = args.payload;
      clearRequests.push(request);
      if (clearRequests.length === 2) await releaseBusyRetry.promise;
      if (writerBusy) return busy;
      return { result: { protocol_version: '1.1', type: 'response', operation: args.operation,
        request_id: request.request_id, message_id: randomUUID(), timestamp_ms: Date.now(),
        selection: { workspace_id: workspaceId, selection_revision: 5, target: null } } };
    }
    if (openingWorkspace) return busy;
    return { result: { version: 1, revisions: [], next_after_revision_id: null } };
  });
  await page.addInitScript(() => {
    window.isTauri = true;
    window.__SCOPEHUB_DESKTOP_WORKSPACE__ = true;
    window.__TAURI_INTERNALS__ = {
      async invoke(command, args) {
        const reply = await window.testNativeInvoke(command, args);
        if (reply.error) throw reply.error;
        return reply.result;
      },
    };
  });

  try {
    await page.goto('/');
    await expect.poll(() => calls).toEqual(['snapshot']);
    await control(page, 'Open published CSV').last().click();
    const dialog = page.getByRole('dialog', { name: 'Open published CSV' });
    const folder = dialog.getByRole('button', { name: 'Folder revisions' });
    await folder.click({ timeout: 2500 });
    await expect(folder).toHaveAttribute('aria-pressed', 'true');
    expect(calls).toEqual(['snapshot']);
    await expect(dialog.getByText('Loading published revisions…')).toBeVisible();
    releaseSnapshot.resolve();
    await expect(dialog.getByText('No published folder revisions are available in this profile.')).toBeVisible();
    await expect(dialog.getByRole('alert')).toHaveCount(0);
    expect(calls).toEqual(['snapshot', 'market.listLocalFolderRevisions']);

    const clear = dialog.getByRole('button', { name: 'Clear shared selection' });
    await clear.click();
    await expect.poll(() => clearRequests.length).toBe(2);
    expect(clearRequests[0]).toMatchObject({ workspace_id: workspaceId, expected_selection_revision: 3, target: null });
    expect(clearRequests[1]).toEqual(clearRequests[0]);
    await expect(clear).toBeDisabled();
    releaseBusyRetry.resolve();
    await expect(dialog.getByText('The workspace is busy. Try selecting again.')).toBeVisible();
    await expect(clear).toBeEnabled();
    await expect(folder).toHaveAttribute('aria-pressed', 'true');
    await dialog.getByRole('button', { name: 'Refresh shared selection' }).click();
    const before = ['snapshot', 'market.listLocalFolderRevisions', 'workspace.selectEvidence', 'workspace.selectEvidence', 'snapshot'];
    await expect.poll(() => calls).toEqual(before);
    await dialog.getByRole('button', { name: 'Single-file revisions' }).click();
    await expect(dialog.getByText('Loading published revisions…')).toBeVisible();
    expect(calls).toEqual(before);

    releaseRefresh.resolve();
    await expect(dialog.getByText('No published CSV revisions are available in this profile.')).toBeVisible();
    await expect(dialog.getByRole('alert')).toHaveCount(0);
    expect(calls).toEqual([...before, 'market.listLocalImportRevisions']);

    writerBusy = false;
    await clear.click();
    await expect(clear).toHaveCount(0);
    await expect(dialog.getByText('No published CSV revisions are available in this profile.')).toBeVisible();
    expect(clearRequests).toHaveLength(3);
    expect(clearRequests[2]).toMatchObject({ workspace_id: workspaceId, expected_selection_revision: 4, target: null });
    expect(clearRequests[2].request_id).not.toBe(clearRequests[0].request_id);
    expect(calls).toEqual([...before, 'market.listLocalImportRevisions', 'workspace.selectEvidence']);
  } finally {
    releaseSnapshot.resolve();
    releaseRefresh.resolve();
    releaseBusyRetry.resolve();
  }
});

for (const kind of ['single-file', 'folder']) {
  base(`protects the ${kind} foreground open from its own initial entitlement probe`, {
    tag: '@profile-folder-refresh',
  }, async ({ page }) => {
    const id = randomUUID();
    const clock = new Date('2026-04-16T08:00:00Z');
    await page.clock.install({ time: clock });
    await page.clock.pauseAt(new Date(clock.getTime() + 1000));
    const foreground = Promise.withResolvers();
    const release = Promise.withResolvers();
    let awaitingAdmission = false;
    let competing = false;
    let reads = 0;
    await page.exposeFunction('testRevisionAdmission', async (command, { operation, payload }) => {
      expect(command).toBe('execute_operation');
      if (payload.type === 'snapshot') {
        return { result: { workspace: { id: 'main', revision: 1, timeframe: '1d' }, selection_revision: 0, undo: null } };
      }
      if (operation?.startsWith('market.list')) {
        return { result: { version: 1, revisions: [], next_after_revision_id: null } };
      }
      if (payload.type === 'frozen_chart_status' || payload.include_details === false) {
        if (awaitingAdmission) competing = true;
        return { result: { available: true, revision: { revision_id: id } } };
      }
      expect(payload.revision_id).toBe(id);
      expect(operation ?? payload.type).toBe(kind === 'folder' ? 'market.readLocalFolderRevision' : 'frozen_chart');
      reads++;
      awaitingAdmission = true;
      foreground.resolve();
      await release.promise;
      awaitingAdmission = false;
      return { error: competing
        ? { code: 'BUSY', message: 'The market worker is busy.', retryable: true }
        : { code: 'SNAPSHOT_UNAVAILABLE' } };
    });
    await page.addInitScript(() => {
      window.isTauri = true;
      window.__SCOPEHUB_DESKTOP_WORKSPACE__ = true;
      window.__TAURI_INTERNALS__ = {
        async invoke(command, args) {
          const reply = await window.testRevisionAdmission(command, args);
          if (reply.error) throw reply.error;
          return reply.result;
        },
      };
    });
    try {
      await page.goto('/');
      await control(page, 'Open published CSV').last().click();
      const dialog = page.getByRole('dialog', { name: 'Open published CSV' });
      if (kind === 'folder') await dialog.getByRole('button', { name: 'Folder revisions' }).click();
      await dialog.getByRole('textbox', { name: 'Revision ID' }).fill(id);
      const submit = kind === 'folder' ? 'Open frozen folder revision' : 'Open frozen chart';
      await dialog.getByRole('button', { name: submit, exact: true }).click();
      await foreground.promise;
      await page.clock.runFor(80);
      release.resolve();
      await expect(dialog.getByRole('alert')).toHaveText('This frozen chart is unavailable in the current profile.');
      expect(reads).toBe(1);
      expect(competing).toBe(false);
    } finally {
      release.resolve();
    }
  });
}

for (const entry of ['published CSV dialog', 'local import editor']) {
  base(`cancels a retryable frozen chart read from the ${entry}`, async ({ page }) => {
    const revisionId = randomUUID();
    const releaseBusy = Promise.withResolvers();
    const calls = [];
    const reads = [];
    const isDialog = entry === 'published CSV dialog';
    await page.exposeBinding('testNativeInvoke', async (_, command, args) => {
      if (command === 'choose_local_csv_folder') return { result: catalog };
      if (command === 'cancel_local_import_preview') {
        calls.push(command);
        return { result: null };
      }
      expect(command).toBe('execute_operation');
      const { operation, payload } = args;
      calls.push(operation ?? payload.type);
      if (payload.type === 'snapshot') {
        return { result: { workspace: { id: 'main', revision: 0, timeframe: '1d' }, selection_revision: 0, undo: null, source: null } };
      }
      if (operation === 'market.listLocalImportRevisions') return { result: { revisions: [], next_after_revision_id: null } };
      if (payload.type === 'frozen_chart_status') return { result: { available: true } };
      if (payload.type === 'frozen_chart') {
        reads.push(payload.revision_id);
        if (reads.length > 1) return { error: { code: 'SNAPSHOT_UNAVAILABLE' } };
        await releaseBusy.promise;
        return { error: { code: 'BUSY', retryable: true } };
      }
      if (operation === 'market.previewLocalImport') return { result: importedFixture(args) };
      if (operation === 'market.saveLocalImportMapping') {
        return { result: { version: 1, record: { review_revision: 1, mapping: payload.mapping } } };
      }
      expect(operation).toBe('market.publishLocalImport');
      return { result: { revision: { revision_id: revisionId, symbol: '000731', bar_count: 2,
        relative_path: equity.path, source_sha256: sourceIdentity(equity).sha256 } } };
    });
    await page.addInitScript(() => {
      window.isTauri = true;
      window.__SCOPEHUB_DESKTOP_WORKSPACE__ = true;
      window.frozenChartReplies = 0;
      window.__TAURI_INTERNALS__ = {
        async invoke(command, args) {
          const reply = await window.testNativeInvoke(command, args);
          if (args?.payload?.type === 'frozen_chart') window.frozenChartReplies++;
          if (reply.error) throw reply.error;
          return reply.result;
        },
      };
    });

    const dialog = page.getByRole('dialog', { name: 'Open published CSV' });
    const published = page.getByRole('region', { name: 'Published local CSV' });
    const panel = isDialog ? dialog : published;
    const showDialog = control(page, 'Open published CSV').last();
    try {
      await page.goto('/');
      if (isDialog) {
        await showDialog.click();
        await dialog.getByLabel('Revision ID').fill(revisionId);
      } else {
        await control(page, 'Choose local CSV folder').last().click();
        await control(page, 'Inspect raw rows').click();
        await expect(page.getByRole('table', { name: 'Raw CSV rows' })).toBeVisible();
        await enterMapping(page, equity);
        await control(page, 'Preview mapping').click();
        await control(page, 'Save reviewed mapping').click();
        await control(page, 'Publish complete file').click();
        await expect(published).toContainText(revisionId);
      }
      const openFrozen = control(panel, 'Open frozen chart');
      await expect(openFrozen).toBeEnabled();
      const beforeRead = calls.length;
      const now = new Date();
      await page.clock.install({ time: now });
      await page.clock.pauseAt(new Date(now.getTime() + 1000));
      await openFrozen.click();
      await expect.poll(() => reads).toEqual([revisionId]);
      await (isDialog ? control(dialog, 'Close') : control(page, 'Cancel chart')).click({ timeout: 2500 });
      if (isDialog) await expect(dialog).toBeHidden();
      releaseBusy.resolve();
      await expect.poll(() => page.evaluate(() => window.frozenChartReplies)).toBeGreaterThan(0);
      await page.clock.runFor(150);
      expect(reads).toEqual([revisionId]);
      expect(calls.slice(beforeRead).filter(call => !['snapshot', 'frozen_chart', 'frozen_chart_status'].includes(call))).toEqual([]);
      await expect(page.getByRole('region', { name: 'Frozen local CSV chart' })).toHaveCount(0);
      if (isDialog) {
        await showDialog.click();
        await expect(dialog.getByLabel('Revision ID')).toHaveValue('');
        await expect(control(dialog, 'Use as workspace source')).toHaveCount(0);
      } else {
        await expect(page.getByText('Chart opening cancelled.')).toBeVisible();
        await expect(published).toContainText(revisionId);
        await expect(control(published, 'Open frozen chart')).toBeEnabled();
      }
      await expect(page.getByRole('alert')).toHaveCount(0);
    } finally {
      releaseBusy.resolve();
    }
  });
}

base('publishes a reviewed folder once and reopens its frozen provenance without a folder grant', {
  tag: '@profile-folder-import',
}, async ({ page }) => {
  const binary = process.env.SCOPEHUB_LOCAL_IMPORT_BRIDGE;
  base.skip(!binary, 'Set SCOPEHUB_LOCAL_IMPORT_BRIDGE to the portable Rust test example.');
  const temporary = await mkdtemp(join(await realpath(tmpdir()), 'scopehub-folder-publication-ui-'));
  const profile = join(temporary, 'profile');
  const folder = join(temporary, 'synthetic-csv');
  let bridge;
  let loseReceipt = true;
  const calls = [];
  const operations = name => calls.filter(call => call.args?.operation === `market.${name}`);
  const run = (operation, payload) => bridge.request('execute_operation', {
    operation: `market.${operation}`, requestId: randomUUID(), payload: { version: 1, ...payload },
  });
  try {
    await mkdir(profile, { mode: 0o700 });
    await mkdir(folder, { mode: 0o700 });
    const header = [...equity.header, 'OpenInterest', 'VendorMemo'];
    const csv = (rows, columns = header) => [columns, ...rows].map(row => row.map(field =>
      /[,"\r\n]/.test(field) ? `"${field.replaceAll('"', '""')}"` : field).join(',')).join('\n') + '\n';
    const originalPath = join(folder, 'daily.csv');
    await writeFile(originalPath, csv(equity.rows.map(row => [...row, '00018', 'Original bar'])));

    bridge = startLocalImportBridge(binary, profile, folder);
    await page.exposeBinding('testNativeInvoke', async (_, command, args) => {
      const reply = await bridge.request(command, args);
      calls.push({ command, args, reply });
      if (args?.operation === 'market.publishLocalFolderRefresh' && reply.result && loseReceipt) {
        loseReceipt = false;
        return { error: { code: 'INTERNAL_ERROR', message: 'Publication reply was interrupted.' } };
      }
      return reply;
    });
    await page.addInitScript(() => {
      window.isTauri = true;
      window.__TAURI_INTERNALS__ = {
        async invoke(command, args) {
          const reply = await window.testNativeInvoke(command, args);
          if (reply.error) throw reply.error;
          return reply.result;
        },
      };
    });

    await page.goto('/');
    await control(page, 'Choose local CSV folder').last().click();
    await control(page, 'Inspect raw rows').click();
    const raw = page.getByRole('table', { name: 'Raw CSV rows' });
    await expect(raw.locator('tbody tr').first()).toContainText('000731');
    await enterMapping(page, equity);
    const adjustment = page.getByRole('textbox', { name: 'Price adjustment', exact: true });
    await adjustment.fill('adjusted');
    await control(page, 'Preview mapping').click();
    const normalized = page.getByRole('table', { name: 'Normalized bars' });
    await expect(page.getByRole('region', { name: 'Diagnostics' })).toContainText('Declare the specific adjustment basis used by the source.');
    await expect(normalized).toHaveCount(0);
    await adjustment.fill('raw');
    await control(page, 'Preview mapping').click();
    await expect(normalized.locator('tbody tr').last().getByRole('cell')).toHaveText([
      '3', '000731', '2026-04-15T01:30:00.000Z', '2026-04-15', '38', '41', '37', '40', 'Unknown',
    ]);
    await expect(page.getByRole('region', { name: 'Local CSV chart preview' })).toContainText('Volume in shares');
    await control(page, 'Save reviewed mapping').click();
    await control(page, 'Publish complete file').click();
    const acceptedId = page.getByLabel('Accepted revision ID');
    await expect(acceptedId).not.toHaveValue('');
    const baseId = operations('publishLocalImport')[0].reply.result.revision.revision_id;
    await expect(acceptedId).toHaveValue(baseId);
    await control(page, 'Open frozen chart').click();
    const anchorChart = page.getByRole('region', { name: 'Frozen local CSV chart' });
    await expect(anchorChart.getByLabel('Selected bar')).toContainText('C 40');

    await rm(originalPath);
    const correctionMemo = 'Vendor says "corrected", keep raw\nSecond physical line';
    const corrected = (close, openInterest, memo) => [
      ...equity.rows[0].map((value, index) => index === 5 ? String(close) : value), openInterest, memo,
    ];
    const selectedRow = corrected('39.00', '00120', correctionMemo);
    const addedRow = ['000731', '2026-04-16', '40', '44', '39', '42', '125', '00200', 'New CSV event'];
    const current = csv([corrected('37', '00110', 'Alternate vendor value'), selectedRow, addedRow]);
    const aliasPath = join(folder, 'alias.csv');
    const currentPath = join(folder, 'current.csv');
    await writeFile(currentPath, current);
    await writeFile(aliasPath, current);
    const hash = createHash('sha256').update(current).digest('hex');

    const review = page.getByRole('region', { name: 'Folder refresh review' });
    const stage = control(page, 'Stage folder refresh');
    const reviewPublication = review.getByRole('button', { name: 'Review folder publication' });
    const conflictTime = Date.parse(equity.times[0]);
    const withdrawalTime = Date.parse(equity.times[1]);
    const additionTime = Date.parse('2026-04-16T01:30:00Z');
    const changedPath = join(folder, 'changed-header.csv');
    await writeFile(changedPath, csv([addedRow], header.map(column => column === 'Native close' ? 'Changed column' : column)));
    await stage.click();
    await expect(review.getByRole('row', { name: /changed-header\.csv/ })).toContainText('Header needs review');
    await expect(reviewPublication).toHaveCount(0);
    expect(operations('previewLocalFolderRefresh').at(-1).reply.result.publication_allowed).toBe(false);
    await expect(anchorChart.getByLabel('Selected bar')).toContainText('C 40');
    await rm(changedPath);

    async function prepareCorrection() {
      await stage.click();
      await expect(review.getByText(/2026-04-16.+New bar/)).toBeVisible();
      const staged = operations('previewLocalFolderRefresh').at(-1).reply.result;
      const conflict = staged.bars.find(bar => bar.time_ms === conflictTime);
      expect(conflict.status).toBe('conflict');
      expect(conflict.accepted.bar.close).toBe(38);
      expect(conflict.alternatives.map(item => item.bar.close).sort()).toEqual([37, 39]);
      const addition = staged.bars.find(bar => bar.time_ms === additionTime);
      expect(addition.alternatives[0].bar).toMatchObject({ close: 42, volume: 125 });
      expect(addition.alternatives[0].sources.map(source => source.relative_path)).toEqual(['alias.csv', 'current.csv']);
      const alternativeIndex = conflict.alternatives.findIndex(item => item.bar.close === 39);
      await expect(reviewPublication).toBeDisabled();
      await review.getByText(/2026-04-14.+Competing values/).click();
      await review.getByRole('radio', { name: `Use alternative ${alternativeIndex + 1} for ${new Date(conflictTime).toISOString()}` }).check();
      await expect(reviewPublication).toBeDisabled();
      await review.getByRole('checkbox', { name: 'Withdraw source daily.csv' }).check();
      await expect(reviewPublication).toBeDisabled();
      await review.getByText(/2026-04-15.+Withdrawal candidate/).click();
      await review.getByRole('checkbox', { name: `Withdraw bar at ${new Date(withdrawalTime).toISOString()}` }).check();
      await reviewPublication.click();
      await expect(review.getByRole('group', { name: 'Publish reviewed folder?' })).toContainText('2 bars from 2 sources');
      expect(operations('prepareLocalFolderPublication').at(-1).args.payload).toMatchObject({
        review_id: staged.review_id,
        conflicts: [{ time_ms: conflictTime, alternative_index: alternativeIndex }],
        withdraw_bars: [withdrawalTime],
        withdraw_sources: ['daily.csv'],
      });
    }

    await prepareCorrection();
    // Simulate an external save between preview/approval and the final SQLite commit guard.
    await writeFile(currentPath, current.replace('New CSV event', 'Changed after review'));
    const publishFolder = review.getByRole('button', { name: 'Publish reviewed folder' });
    await publishFolder.click();
    await expect(page.getByRole('alert')).toContainText('The folder or accepted revision changed. Stage the folder refresh again.');
    expect(operations('publishLocalFolderRefresh').at(-1).reply).toMatchObject({
      error: { code: 'SNAPSHOT_UNAVAILABLE', details: { reason: 'folder_changed_during_review' } },
    });
    const listing = await run('listLocalFolderRevisions', { limit: 8, after_revision_id: null });
    expect(listing.result.revisions).toEqual([]);
    const original = await run('readLocalImportRevision', { revision_id: baseId });
    expect(original.result.bars.map(item => item.bar.close)).toEqual([38, 40]);
    await expect(anchorChart.getByLabel('Selected bar')).toContainText('C 40');
    await writeFile(currentPath, current);
    await prepareCorrection();

    // An interrupted Rust process loses its prepared review before any commit is requested.
    await bridge.interrupt();
    bridge = startLocalImportBridge(binary, profile, folder);
    const recovered = await run('listLocalFolderRevisions', { limit: 8, after_revision_id: null });
    expect(recovered.result.revisions).toEqual([]);
    expect(await run('readLocalImportRevision', { revision_id: baseId })).toEqual(original);
    await publishFolder.click();
    await expect(page.getByRole('alert')).toBeVisible();
    expect(operations('publishLocalFolderRefresh').at(-1).reply).toMatchObject({ error: { code: 'STALE_CONTEXT' } });
    await page.reload();
    await control(page, 'Choose local CSV folder').last().click();
    await acceptedId.fill(baseId);
    await prepareCorrection();

    await publishFolder.click();
    await expect(page.getByRole('alert')).toHaveText('Publication reply was interrupted.');
    const first = operations('publishLocalFolderRefresh').at(-1);
    expect(first.reply.result.revision).toMatchObject({
      base_revision_id: baseId, parent_revision_id: null, bar_count: 2, source_count: 2,
    });
    const revisionId = first.reply.result.revision.revision_id;
    await rm(currentPath);
    await rm(aliasPath);
    await review.getByRole('button', { name: 'Retry folder publication' }).click();
    const published = page.getByRole('region', { name: 'Published local folder' });
    await expect(published).toContainText(revisionId);
    const retry = operations('publishLocalFolderRefresh').at(-1);
    expect(retry.args.payload).toEqual(first.args.payload);
    expect(retry.args.requestId).not.toBe(first.args.requestId);
    expect(retry.reply.result).toEqual(first.reply.result);

    await published.getByRole('button', { name: 'Open frozen folder chart' }).click();
    const chart = page.getByRole('region', { name: 'Frozen local folder chart' });
    const provenance = page.getByRole('region', { name: 'Published folder source provenance' });
    await expect(chart.getByLabel('Selected bar')).toContainText('C 42');
    await provenance.getByText('Source files (2)', { exact: true }).click();
    await expect(provenance.getByText(hash, { exact: true })).toHaveCount(2);
    await provenance.getByText('Source rows (4)', { exact: true }).click();
    const sourceRows = provenance.getByRole('table', { name: 'Published folder source rows' });
    for (const relativePath of ['alias.csv', 'current.csv']) {
      const row = sourceRows.getByRole('row', { name: new RegExp(relativePath.replace('.', '\\.')) })
        .filter({ has: page.getByRole('cell', { name: '3–4', exact: true }) });
      await expect(row.getByRole('cell', { name: '39', exact: true })).toBeVisible();
    }

    await expect(acceptedId).toHaveValue(revisionId);
    // Recreate both published sources, then replace only the former representative.
    const copyPath = join(folder, 'retained-copy.csv');
    await writeFile(currentPath, current);
    await writeFile(copyPath, current);
    await writeFile(aliasPath, csv([corrected('38', '00121', 'New revision'), addedRow]));
    await stage.click();
    const changes = review.getByRole('table', { name: 'Folder source changes' });
    const file = name => changes.getByRole('rowheader', { name, exact: true }).locator('..');
    await expect(file('alias.csv')).toContainText('Replaced');
    await expect(file('current.csv')).toContainText('Alias of alias.csv (accepted source)');
    await expect(file('retained-copy.csv')).toContainText('Alias of current.csv (current source)');
    await expect(file('retained-copy.csv')).toContainText('Alias of alias.csv (accepted source)');
    const replaced = operations('previewLocalFolderRefresh').at(-1).reply.result;
    expect(replaced.files).toEqual([
      expect.objectContaining({ relative_path: 'alias.csv', prior_sha256: hash, change: 'replaced', alias_of: null }),
      expect.objectContaining({ relative_path: 'current.csv', source_sha256: hash, alias_of: null }),
      expect.objectContaining({ relative_path: 'retained-copy.csv', source_sha256: hash, alias_of: 'current.csv' }),
    ]);
    expect(replaced.files[0].source_sha256).not.toBe(hash);
    const afterReplacement = replaced.bars.find(bar => bar.time_ms === conflictTime);
    expect(afterReplacement.status).toBe('conflict');
    expect(afterReplacement.alternatives.map(item => item.bar.close).sort()).toEqual([37, 38, 39]);
    await rm(aliasPath);
    await rm(currentPath);
    await rm(copyPath);

    await stage.click();
    await review.getByText(/2026-04-14.+Withdrawal candidate/).click();
    await expect(review.getByText('Accepted current.csv · Record 2 · Lines 3–4')).toBeVisible();
    const childReview = operations('previewLocalFolderRefresh').at(-1).reply.result;
    expect(childReview.base).toBeUndefined();
    expect(childReview.folder_base.revision_id).toBe(revisionId);
    await review.getByRole('checkbox', { name: 'Withdraw source alias.csv' }).check();
    await review.getByRole('checkbox', { name: 'Withdraw source current.csv' }).check();
    await review.getByRole('button', { name: 'Confirm all bar withdrawals' }).click();
    await reviewPublication.click();
    await expect(review.getByRole('group', { name: 'Publish reviewed folder?' })).toContainText('0 bars from 0 sources');
    await review.getByRole('button', { name: 'Publish reviewed folder' }).click();
    await expect(published.getByRole('status')).toContainText('0 bars · 0 sources');
    const child = operations('publishLocalFolderRefresh').at(-1).reply.result.revision;
    expect(child).toMatchObject({ base_revision_id: baseId, parent_revision_id: revisionId, bar_count: 0, source_count: 0 });
    await published.getByRole('button', { name: 'Open frozen folder chart' }).click();
    await expect(chart).toContainText('This reviewed folder revision withdrew all accepted bars.');

    await bridge.close();
    await rename(folder, join(temporary, 'moved-sources'));
    bridge = startLocalImportBridge(binary, profile, folder);
    const folderSelections = calls.filter(call => call.command === 'choose_local_csv_folder').length;
    expect(folderSelections).toBe(2);
    await page.reload();
    await control(page, 'Open published CSV').last().click();
    const dialog = page.getByRole('dialog', { name: 'Open published CSV' });
    await dialog.getByRole('button', { name: 'Folder revisions' }).click();
    const entries = dialog.getByRole('button', { name: /Open folder revision from/ });
    const originalEntry = entries.filter({ hasText: revisionId.slice(0, 8) });
    const withdrawalEntry = entries.filter({ hasText: child.revision_id.slice(0, 8) });
    await expect(entries).toHaveCount(2);
    await expect(originalEntry).toContainText('Final close 42 · Trading day 2026-04-16');
    await withdrawalEntry.click();
    await expect(dialog.getByRole('region', { name: 'Frozen local folder chart' })).toContainText('This reviewed folder revision withdrew all accepted bars.');
    expect(await run('readLocalImportRevision', { revision_id: baseId })).toEqual(original);
    expect((await run('revokeLocalImportRevision', { revision_id: baseId })).result.revision_id).toBe(baseId);
    expect((await run('readLocalImportRevision', { revision_id: baseId })).error.code).toBe('PERMISSION_DENIED');
    await originalEntry.click();
    await expect(dialog.getByRole('region', { name: 'Frozen local folder chart' }).getByLabel('Selected bar')).toContainText('C 42');
    const reopened = dialog.getByRole('region', { name: 'Published folder source provenance' });
    await expect(reopened).toContainText(revisionId);
    await reopened.getByText('Source rows (4)', { exact: true }).click();
    await reopened.getByRole('button', { name: 'Inspect record 2 from alias.csv', exact: true }).click();
    const originalFields = reopened.getByRole('region', { name: 'Original CSV fields', exact: true });
    await expect(originalFields).toContainText('alias.csv · Record 2 · Source lines 3–4');
    await expect(originalFields.getByRole('row', { name: 'Native close 39.00' })).toBeVisible();
    await expect(originalFields.getByRole('row', { name: 'OpenInterest 00120' })).toBeVisible();
    expect(await originalFields.getByText('Second physical line', { exact: false }).textContent()).toBe(correctionMemo);
    const source = { relative_path: 'alias.csv', source_sha256: hash, record_number: 2, line_start: 3, line_end: 4 };
    const locator = { version: 1, revision_id: revisionId, source };
    const inspected = operations('readLocalFolderSourceRow').at(-1);
    expect(inspected.args.payload).toEqual(locator);
    expect(inspected.reply.result).toEqual({ ...locator, header, raw_fields: selectedRow });
    const mismatch = await run('readLocalFolderSourceRow', { ...locator, source: { ...source, line_end: 3 } });
    expect(mismatch.error.code).toBe('STALE_CONTEXT');
    expect(mismatch.result).toBeUndefined();
    expect(calls.filter(call => call.command === 'choose_local_csv_folder')).toHaveLength(folderSelections);

    await dialog.getByRole('button', { name: 'Remove frozen chart access' }).click();
    await dialog.getByRole('button', { name: 'Remove access', exact: true }).click();
    await expect(dialog.getByRole('region', { name: 'Frozen local folder chart' })).toHaveCount(0);
    await expect(originalEntry).toHaveCount(0);
    await expect(withdrawalEntry).toBeVisible();
    const revoked = await bridge.request('execute_operation', {
      operation: 'market.readLocalFolderRevision', requestId: randomUUID(),
      payload: { version: 1, revision_id: revisionId },
    });
    expect(revoked.error.code).toMatch(/PERMISSION_DENIED|SNAPSHOT_UNAVAILABLE/);
    await expect(originalFields).toHaveCount(0);
    const revokedRow = await run('readLocalFolderSourceRow', locator);
    expect(revokedRow.error.code).toMatch(/PERMISSION_DENIED|SNAPSHOT_UNAVAILABLE/);
    expect(revokedRow.result).toBeUndefined();
  } finally {
    await bridge?.close();
    await rm(temporary, { recursive: true, force: true });
  }
});
