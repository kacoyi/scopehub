import { test as base, expect } from '@playwright/test';
import { spawn } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { mkdir, mkdtemp, realpath, rename, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createInterface } from 'node:readline';
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
const catalog = {
  folder_id: folderId, display_name: 'Synthetic CSV fixture folder',
  files: files.map(file => {
    const { file_id, relative_path, display_name, byte_count } = sourceIdentity(file);
    return { file_id, relative_path, display_name, byte_count, kind: 'csv' };
  }),
};

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
  mockNative: async ({ page, baseURL }, use) => {
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
});

function startLocalImportBridge(binary, profile, folder) {
  const child = spawn(binary, [profile, folder], { stdio: ['pipe', 'pipe', 'pipe'] });
  const output = createInterface({ input: child.stdout })[Symbol.asyncIterator]();
  let diagnostic = '';
  child.stderr.on('data', data => { diagnostic = (diagnostic + data.toString()).slice(-2048); });
  child.stdin.on('error', error => { diagnostic = String(error); });
  const ended = new Promise(resolve => {
    child.once('error', error => { diagnostic = String(error); });
    child.once('close', resolve);
  });
  let queue = Promise.resolve();
  return {
    request(command, args) {
      const result = queue.then(async () => {
        child.stdin.write(JSON.stringify({ command, args }) + '\n');
        const reply = await output.next();
        if (reply.done) throw new Error(`Local import bridge stopped: ${diagnostic}`);
        return JSON.parse(reply.value);
      });
      queue = result.catch(() => undefined);
      return result;
    },
    async close() {
      child.stdin.end();
      await ended;
    },
  };
}

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
  try {
    await mkdir(profile, { mode: 0o700 });
    await mkdir(folder, { mode: 0o700 });
    const file = join(folder, 'daily.csv');
    const heldFile = join(folder, 'held.csv');
    const header = [...equity.header, 'VendorMemo', 'is_closed'];
    const rows = Array.from({ length: 502 }, (_, index) => {
      const day = new Date(Date.UTC(2024, 0, index + 1)).toISOString().slice(0, 10);
      return index === 501
        ? ['000731', day, '300', '306', '299', '305', '', 'Tail was not sampled', 'true']
        : ['000731', day, '36', '39', '34', '38', '120', 'Original extra field', 'true'];
    });
    const csv = (records) => [header, ...records].map(row => row.join(',')).join('\n') + '\n';
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
    const operations = (name) => calls.filter(call => call.args?.operation === name);
    const publish = control(page, 'Publish complete file');
    const open = control(page, 'Open frozen chart');
    const published = page.getByRole('region', { name: 'Published local CSV' });
    const frozen = page.getByRole('region', { name: 'Frozen local CSV chart' });
    const provenance = page.getByRole('region', { name: 'Published source provenance' });
    const sample = page.getByRole('region', { name: 'Preview source' });

    async function previewAndSave() {
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
    await control(page, 'Choose local CSV folder').last().click();
    await previewAndSave();
    await expect(page.getByText('Original extra field', { exact: true })).toBeVisible();
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
    await expect(sourceRow.getByRole('cell')).toHaveText(['2', '2024-01-01T01:30:00.000Z', '2024-01-01', '38']);

    await writeFile(file, original.replace(',305,', ',304,'));
    await publish.click();
    await expect(page.getByRole('alert')).toContainText('The file changed since it was inspected.');
    expect(await bridge.request('execute_operation', first.args)).toEqual(first.reply);
    await expect(frozen.getByLabel('Selected bar')).toContainText('C 305');
    await select(page, 'CSV file').selectOption({ label: 'held.csv' });
    await previewAndSave();
    await publish.click();
    await expect(page.getByRole('alert')).toContainText('Review CSV record 502.');
    await expect(page.getByRole('alert')).toContainText('Source lines 503.');
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
      bar: { close: 305, volume: null }, locators: [{ record_number: 502, line_start: 503, line_end: 503 }],
    });

    await control(page, 'Remove frozen chart access').click();
    await expect(dialog).toContainText('The original CSV copy stays in this profile.');
    await control(page, 'Keep access').click();
    await expect(frozen).toBeVisible();
    await control(page, 'Remove frozen chart access').click();
    await control(page, 'Remove access').click();
    await expect(page.getByText('Access removed for this published revision.', { exact: true })).toBeVisible();
    await expect(frozen).toHaveCount(0);
    const afterRevocation = await run(bridge, read, { revision_id: revision.revision_id });
    expect(afterRevocation.error?.code).toBe('PERMISSION_DENIED');
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
    await peer?.close();
    await bridge?.close();
    await rm(temporary, { recursive: true, force: true });
  }
});
