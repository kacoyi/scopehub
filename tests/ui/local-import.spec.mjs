import { test as base, expect } from '@playwright/test';
import { createHash } from 'node:crypto';
import { painted } from './chart-pixels.mjs';
import { control, openPanel } from './workspace-controls.mjs';

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

function sourceIdentity(file) {
  const bytes = [file.header, ...file.rows].map(row => row.join(',') + '\n').join('');
  return {
    version: 1, folder_id: folderId, file_id: file.fileId,
    relative_path: file.path, display_name: file.path.split('/').at(-1),
    byte_count: Buffer.byteLength(bytes), sha256: createHash('sha256').update(bytes).digest('hex'),
  };
}

const files = [equity, future];
const catalog = {
  folder_id: folderId, display_name: 'Synthetic CSV fixture folder',
  files: files.map(file => {
    const { file_id, relative_path, display_name, byte_count } = sourceIdentity(file);
    return { file_id, relative_path, display_name, byte_count, kind: 'csv' };
  }),
};

function importedFixture({ payload, requestId }) {
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
  const identity = sourceIdentity(file);
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
  const issues = wrongClose ? [{ code: 'auxiliary_column', field: 'close', message: 'Previous settlement price cannot be mapped to OHLC.' }] : [];
  const rows = file.rows.map((rawFields, index) => {
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
  const series = isNative && !wrongClose ? {
    instrument_kind: file.settings.instrument_kind, venue: file.settings.venue, timeframe: file.settings.timeframe,
    calendar_id: file.settings.calendar_id, session_scope: file.settings.session_scope, timezone: file.settings.timezone,
    quantity_unit: file.settings.quantity_unit, price_adjustment: file.settings.price_adjustment, roll_method: null,
  } : null;
  return {
    ...identity, preview: {
      file_sha256: identity.sha256, mapping_version: mapping.version, mapping_revision: mapping.revision,
      header: file.header, raw_header_text: null, rows, issues,
      raw_only_columns: file === future ? [{ column: 'PrevSettlementPrice', reason: 'Previous settlement price' }] : [],
      series, truncated: false,
    },
  };
}

// The browser substitutes Tauri invocation and the responses from the host.
// Only synthetic fixture bytes contribute to display data and expected hashes.
const test = base.extend({
  mockNative: async ({ page, baseURL }, use) => {
    const choices = [];
    const calls = [];
    const saved = new Map();
    const changedHeaders = new Set();
    const cancelSaveAs = [];
    const pendingSaves = new Map();
    const requests = [];
    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(String(error)));
    page.on('request', request => requests.push(request.url()));
    await page.exposeBinding('testNativeInvoke', async (_, command, args) => {
      calls.push({ command, args });
      if (command === 'choose_local_csv_folder') {
        expect(args ?? {}).toEqual({});
        expect(choices.length).toBeGreaterThan(0);
        return choices.shift();
      }
      if (command === 'cancel_local_import_preview') {
        expect(args).toEqual({ requestId: expect.any(String) });
        pendingSaves.get(args.requestId)?.();
        return null;
      }
      expect(command).toBe('execute_operation');
      if (args.operation === 'instrument.search') return { instruments: [] };
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
        if (changedHeaders.has(file.fileId)) {
          const changed = { ...file, header: [...file.header, 'New header'] };
          return { version: 1, status: 'review_required', record: null, source_sha256: sourceIdentity(changed).sha256 };
        }
        return { version: 1, status: 'saved', record, source_sha256: sourceIdentity(file).sha256 };
      }
      expect(args.operation).toBe('market.previewLocalImport');
      return importedFixture(args);
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
    await use({ calls, choices, changedHeaders, cancelSaveAs });
    expect(pageErrors).toEqual([]);
    expect(requests.every(url => new URL(url).origin === baseURL && !new URL(url).pathname.startsWith('/__scopehub/'))).toBe(true);
  },
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

async function enterMapping(page, file, close = file.columns.close) {
  await select(page, 'Record kind').selectOption('native_ohlcv');
  await select(page, 'Instrument type').selectOption(file.settings.instrument_kind);
  for (const [key, name] of Object.entries(mappingFields)) {
    if (key in file.settings) await page.getByRole('textbox', { name, exact: true }).fill(file.settings[key]);
  }
  await select(page, 'Volume basis').selectOption('per_bar');
  await select(page, 'Timestamp convention').selectOption('bar_start');
  for (const [key, name] of Object.entries(columnFields)) {
    await select(page, name).selectOption(key === 'close' ? close : file.columns[key]);
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
  await expect(page.getByText('Simulated', { exact: true })).toHaveCount(0);
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
  await enterMapping(page, future, 'PrevSettlementPrice');
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

test('substitute native transport saves the reviewed preview and requests review when the header changes', async ({ page, mockNative }) => {
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

  await venue.fill('Draft venue');
  await expect(save).toHaveCount(0);
  mockNative.changedHeaders.add(future.fileId);
  await load.click();
  await expect(page.getByText('The CSV header has changed. Inspect the current raw rows and review the mapping.')).toBeVisible();
  await expect(venue).toHaveValue('Draft venue');
  await expect(close).toHaveValue('Close');
  await expect(normalized).toHaveCount(0);
  await expect(page.getByText(hash, { exact: true })).toHaveCount(0);
  expect(saves()).toHaveLength(3);
});

base('ordinary browser without native transport has no folder selection action', async ({ page }) => {
  await page.goto('/');
  await expect(control(page, 'Open offline sample')).toBeVisible();
  await expect(control(page, 'Choose local CSV folder')).toHaveCount(0);
  await expect(page.locator('input[type=file]')).toHaveCount(0);
});
