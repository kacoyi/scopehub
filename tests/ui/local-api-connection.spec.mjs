import { createHash, randomUUID } from 'node:crypto';
import { once } from 'node:events';
import { mkdir, mkdtemp, realpath, rm, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test } from '@playwright/test';
import { startLocalImportBridge } from './local-import-bridge.mjs';
import { control } from './workspace-controls.mjs';

async function installDesktopBridge(page, dispatch) {
  await page.exposeFunction('dispatchNativeApi', dispatch);
  await page.addInitScript(() => {
    window.isTauri = true;
    window.__TAURI_INTERNALS__ = {
      async invoke(command, args) {
        const reply = await window.dispatchNativeApi(command, args);
        if (reply.error) throw reply.error;
        return reply.result;
      },
    };
  });
}

async function openApi(page) {
  const panel = page.getByRole('dialog', { name: 'Settings' });
  await control(page, 'Settings').click();
  await control(panel, 'Data sources').click();
  await control(panel, 'My data sources').click();
  await expect(control(panel, 'Choose local CSV folder')).toBeVisible();
  await control(panel, 'Local API connection').click();
  return panel;
}

test('a named local API survives reopen and reads only on explicit trial @profile-local-api', async ({ page, baseURL }, testInfo) => {
  const binary = process.env.SCOPEHUB_LOCAL_IMPORT_BRIDGE;
  expect(binary).toBeTruthy();
  const root = await mkdtemp(join(await realpath(tmpdir()), 'scopehub-local-api-'));
  const traffic = [];
  let mode = 'valid';
  let rejectNextStatus = false;
  let lastTrialReply;
  const held = Promise.withResolvers();
  const validSample = { instrument: 'FUT-TEST', observed_at_ms: 1770076800000, value: 47.25, unit: 'USD' };
  const peer = createServer((request, response) => {
    traffic.push([request.method, request.url, request.headers.authorization, request.headers.cookie]);
    if (mode === 'stalled') return;
    if (mode === 'redirect') {
      response.writeHead(302, { Location: '/unapproved' }).end();
      return;
    }
    response.writeHead(200, { 'Content-Type': 'application/json' });
    if (mode === 'oversized') {
      response.end(' '.repeat(17 * 1024));
      return;
    }
    if (mode === 'held') {
      held.resolve(response);
      return;
    }
    if (mode === 'precise' || mode === 'quoted') {
      const token = mode === 'precise' ? '9007199254740993' : '"9007199254740993"';
      response.end('{"instrument":"FUT-TEST","observed_at_ms":1770076800000,"value":' + token + ',"unit":"USD"}');
      return;
    }
    response.end(JSON.stringify(validSample));
  }).listen(0, '127.0.0.1');
  await once(peer, 'listening');
  const origin = 'http://127.0.0.1:' + peer.address().port;
  let bridge = startLocalImportBridge(binary, join(root, 'profile'), root);
  let otherWindow;
  const unexpected = [];
  page.on('request', request => {
    if (new URL(request.url()).origin !== baseURL) unexpected.push(request.url());
  });
  await installDesktopBridge(page, async (command, args) => {
    const action = args?.operation === 'market.localApiConnection' && args.payload.action;
    if (action === 'status' && rejectNextStatus) {
      rejectNextStatus = false;
      return { error: { code: 'BUSY' } };
    }
    const reply = await bridge.request(command, args);
    if (action === 'trial') lastTrialReply = reply;
    return reply;
  });
  const panel = page.getByRole('dialog', { name: 'Settings' });

  try {
    await page.goto('/');
    await openApi(page);
    await expect(panel.getByText('No local API connection saved.')).toBeVisible();
    await expect(panel.getByLabel('Local API origin')).toHaveAttribute('placeholder', 'http://127.0.0.1:8080');
    await panel.getByLabel('Connection name').fill('My synthetic market API');
    await panel.getByLabel('Local API origin').fill('https://example.invalid:4555');
    await control(panel, 'Save connection').click();
    await expect(panel.getByRole('alert')).toHaveText('Enter a name and a loopback origin such as http://127.0.0.1:8080.');
    expect(traffic).toEqual([]);
    await panel.getByLabel('Local API origin').fill(origin);
    await control(panel, 'Save connection').click();
    await expect(panel.getByText('Saved connection.')).toBeVisible();
    expect(traffic).toEqual([]);

    await bridge.close();
    bridge = startLocalImportBridge(binary, join(root, 'profile'), root);
    await page.reload();
    await openApi(page);
    await expect(panel.getByLabel('Connection name')).toHaveValue('My synthetic market API');
    await expect(panel.getByLabel('Local API origin')).toHaveValue(origin);
    await expect(panel.getByText('GET ' + origin + '/market-sample', { exact: true })).toBeVisible();
    expect(traffic).toEqual([]);
    await control(panel, 'Test read').click();
    const sample = panel.getByRole('region', { name: 'Verified sample' });
    await expect(sample).toContainText('FUT-TEST');
    await expect(sample).toContainText('47.25');
    await expect(sample).toContainText('USD');
    await expect(sample).toContainText('2026-02-03T00:00:00.000Z');
    expect(traffic).toEqual([['GET', '/market-sample', undefined, undefined]]);
    const observation = { at: new Date().toISOString(), displayed: await sample.innerText(), requests: traffic.length };
    console.info('Local API sample observed', JSON.stringify(observation));
    await testInfo.attach('local-api-sample', { body: JSON.stringify(observation), contentType: 'application/json' });
    await testInfo.attach('local-api-settings', { body: await panel.screenshot(), contentType: 'image/png' });

    mode = 'redirect';
    await control(panel, 'Test read').click();
    await expect(panel.getByRole('alert')).toHaveText('The trial did not return a usable market sample.');
    await expect(sample).toHaveCount(0);
    expect(traffic).toEqual(Array.from({ length: 2 }, () => ['GET', '/market-sample', undefined, undefined]));
    mode = 'oversized';
    await control(panel, 'Test read').click();
    await expect(panel.getByRole('alert')).toHaveText('The trial did not return a usable market sample.');
    expect(traffic).toHaveLength(3);

    mode = 'stalled';
    await control(panel, 'Test read').click();
    await expect(panel.getByRole('alert')).toHaveText('The local API did not respond within the trial limit.');
    await expect(sample).toHaveCount(0);
    expect(traffic).toHaveLength(4);

    mode = 'precise';
    await control(panel, 'Test read').click();
    await expect(sample).toBeVisible();
    expect.soft(await sample.innerText()).toContain('9007199254740993');
    expect(traffic).toHaveLength(5);

    mode = 'quoted';
    await control(panel, 'Test read').click();
    await expect(panel.getByRole('alert')).toHaveText('The trial did not return a usable market sample.');
    await expect(sample).toHaveCount(0);
    expect(traffic).toHaveLength(6);

    otherWindow = startLocalImportBridge(binary, join(root, 'profile'), root);
    const operation = 'market.localApiConnection';
    const current = await otherWindow.request('execute_operation', { operation, payload: { action: 'status' } });
    expect(current.result.connection.origin).toBe(origin);
    mode = 'held';
    lastTrialReply = undefined;
    await control(panel, 'Test read').click();
    await expect.poll(() => traffic.length, { timeout: 2000 }).toBe(7);
    const heldResponse = await held.promise;
    try {
      const replacement = await otherWindow.request('execute_operation', { operation, payload: {
        action: 'save', expected_id: current.result.connection.id, name: 'Replacement API', origin,
      } });
      expect(replacement.result.connection.id).not.toBe(current.result.connection.id);
    } finally {
      heldResponse.end(JSON.stringify(validSample));
    }
    await expect.poll(() => lastTrialReply, { timeout: 4000 }).toBeDefined();
    expect.soft(lastTrialReply.error?.code).toBe('STALE_CONTEXT');
    await expect.soft(sample).toHaveCount(0, { timeout: 1000 });
    await expect.soft(panel.getByRole('alert')).toHaveText('The saved connection changed. Reopen the form to load it.', { timeout: 1000 });
    expect(traffic).toHaveLength(7);

    await control(panel, 'Back to my data sources').click();
    await expect(control(panel, 'Local API connection')).toBeFocused();
    await expect(control(panel, 'Choose local CSV folder')).toBeVisible();
    rejectNextStatus = true;
    await control(panel, 'Local API connection').click();
    await expect(panel.getByRole('alert')).toBeVisible();
    await control(panel, 'Retry loading connection').click({ timeout: 2000 });
    await expect(panel.getByLabel('Connection name')).toHaveValue('Replacement API');
    expect(traffic).toHaveLength(7);

    await control(panel, 'Close').click();
    await control(page, 'Language settings').click();
    await page.getByRole('combobox', { name: 'Interface language' }).selectOption('zh-Hans');
    await page.keyboard.press('Escape');
    await expect(page.locator('html')).toHaveAttribute('lang', 'zh-Hans');
    const chinese = page.getByRole('dialog', { name: '设置', exact: true });
    await control(page, '设置').click();
    await control(chinese, '数据源').click();
    await control(chinese, '我的数据源').click();
    await control(chinese, '本地 API 连接').click();
    await expect(chinese.getByLabel('连接名称')).toHaveValue('Replacement API');
    const chineseOrigin = chinese.getByLabel('本地 API 源地址');
    await expect(chineseOrigin).toHaveValue(origin);
    await chineseOrigin.clear();
    await expect(chinese.getByPlaceholder('http://127.0.0.1:8080', { exact: true })).toBeVisible();
    await expect(control(chinese, '试读')).toBeDisabled();
    await chineseOrigin.fill(origin);
    expect(traffic).toHaveLength(7);
    mode = 'valid';
    await control(chinese, '试读').click();
    await expect(chinese.getByRole('region', { name: '已验证样本' })).toContainText('FUT-TEST · 47.25 USD');
    expect(traffic).toHaveLength(8);
    expect(traffic.at(-1)).toEqual(['GET', '/market-sample', undefined, undefined]);
    expect(unexpected).toEqual([]);
  } finally {
    peer.closeAllConnections();
    try { await Promise.all([bridge.close(), otherWindow?.close()]); }
    finally {
      await new Promise((resolve, reject) => peer.close(error => error ? reject(error) : resolve()));
      await rm(root, { recursive: true, force: true });
    }
  }
});

async function publishIndependentCsv(bridge, folder) {
  const csv = 'Contract,Start,TradeDate,Open,High,Low,Close,Contracts\n'
    + 'CU2605,2026-04-14 21:05,2026-04-15,100,106,96,102,31\n'
    + 'CU2605,2026-04-14 21:10,2026-04-15,102,108,101,105,37\n';
  await writeFile(join(folder, 'independent.csv'), csv);
  const { result: catalog } = await bridge.request('choose_local_csv_folder', {});
  const file = {
    folder_id: catalog.folder_id, file_id: catalog.files[0].file_id,
    expected_sha256: createHash('sha256').update(csv).digest('hex'),
  };
  const mapping = {
    version: 1, revision: 'night-session', encoding: 'utf-8', delimiter: ',', record_kind: 'native_ohlcv',
    timestamp_convention: 'bar_start', volume_semantics: 'per_bar',
    instrument_kind: 'dated_future', venue: 'TEST-FU', timeframe: '5m', calendar_id: 'synthetic-futures-v1',
    session_scope: 'night', timezone: 'Asia/Shanghai', quantity_unit: 'contracts', price_adjustment: 'raw',
    timestamp_format: '%Y-%m-%d %H:%M', trading_day_format: '%Y-%m-%d',
    columns: { symbol: 'Contract', timestamp: 'Start', trading_day: 'TradeDate',
      open: 'Open', high: 'High', low: 'Low', close: 'Close', volume: 'Contracts' },
  };
  const preview = { ...file, mapping, row_limit: 1 };
  const issue = (operation, payload) => bridge.request('execute_operation', {
    operation, requestId: randomUUID(), payload: { version: 1, ...payload },
  });
  expect((await issue('market.previewLocalImport', preview)).result.preview.rows[0].symbol).toBe('CU2605');
  const { result: { record } } = await issue('market.saveLocalImportMapping', preview);
  const { result: { revision } } = await issue('market.publishLocalImport', { ...file, review_revision: record.review_revision });
  return revision;
}

test('disconnects the named local API, rejects stale actions, and reopens with an independent CSV @profile-local-api', async ({ page, context, baseURL }, testInfo) => {
  const binary = process.env.SCOPEHUB_LOCAL_IMPORT_BRIDGE;
  expect(binary).toBeTruthy();
  const root = await mkdtemp(join(await realpath(tmpdir()), 'scopehub-api-disconnect-'));
  const folder = join(root, 'csv');
  const profile = join(root, 'profile');
  const traffic = [];
  const reads = [];
  const unrelatedRequests = [];
  const held = Promise.withResolvers();
  const sample = (instrument, value) => ({ instrument, observed_at_ms: 1770076800000, value, unit: 'USD' });
  const oldSample = sample('OLD-FUT', 47.25);
  const replacement = sample('REPLACEMENT-FUT', 63.5);
  let owner, peer, oldWindow;

  async function fixture(name, handle) {
    const server = createServer((request, response) => {
      traffic.push([name, request.method, request.url, request.headers.authorization ?? null, request.headers.cookie ?? null]);
      response.writeHead(200, { 'Content-Type': 'application/json' });
      handle(response);
    }).listen(0, '127.0.0.1');
    reads.push(server);
    await once(server, 'listening');
    return 'http://127.0.0.1:' + server.address().port;
  }

  function observeTraffic(view) {
    view.on('request', request => {
      if (new URL(request.url()).origin !== baseURL) unrelatedRequests.push(request.url());
    });
  }
  async function save(panel, name, origin) {
    await panel.getByLabel('Connection name').fill(name);
    await panel.getByLabel('Local API origin').fill(origin);
    await control(panel, 'Save connection').click();
    await expect(panel.getByText('Saved connection.')).toBeVisible();
  }

  try {
    await mkdir(folder, { mode: 0o700 });
    const oldOrigin = await fixture('primary', response => held.resolve(response));
    const newOrigin = await fixture('replacement', response => response.end(JSON.stringify(replacement)));
    owner = startLocalImportBridge(binary, profile, folder);
    const csv = await publishIndependentCsv(owner, folder);
    peer = startLocalImportBridge(binary, profile, folder);
    oldWindow = await context.newPage();
    observeTraffic(page);
    observeTraffic(oldWindow);
    await installDesktopBridge(page, (command, args) => owner.request(command, args));
    const rejected = [];
    await installDesktopBridge(oldWindow, async (command, args) => {
      const reply = await peer.request(command, args);
      if (reply.error) rejected.push([args.payload.action, reply.error.code]);
      return reply;
    });

    const started = performance.now();
    await page.goto('/');
    const panel = await openApi(page);
    await expect(panel.getByText('No local API connection saved.')).toBeVisible();
    await save(panel, 'Primary loopback', oldOrigin);
    await oldWindow.goto('/');
    const oldPanel = await openApi(oldWindow);
    await expect(oldPanel.getByLabel('Connection name')).toHaveValue('Primary loopback');
    expect(traffic).toEqual([]);

    await control(oldPanel, 'Test read').click();
    await expect.poll(() => traffic.length).toBe(1);
    const response = await held.promise;
    const actionStarted = performance.now();
    try {
      await control(panel, 'Disconnect Primary loopback').click();
      await expect(panel.getByRole('status')).toHaveText('Local API connection disconnected.');
    } finally {
      response.end(JSON.stringify(oldSample));
    }
    const firstVisible = { at: new Date().toISOString(), since_navigation_ms: Math.round(performance.now() - started),
      since_disconnect_action_ms: Math.round(performance.now() - actionStarted),
      displayed: await panel.getByRole('status').innerText(), traffic: [...traffic],
    };
    console.info('Local API first visible disconnect', JSON.stringify(firstVisible));
    await testInfo.attach('first-visible-disconnect', { body: JSON.stringify(firstVisible), contentType: 'application/json' });
    await expect(panel.getByLabel('Connection name')).toBeFocused();
    await expect(panel.getByLabel('Connection name')).toHaveValue('');
    await expect(panel.getByLabel('Local API origin')).toHaveValue('');
    await expect(control(panel, 'Test read')).toBeDisabled();
    await expect(control(panel, 'Disconnect Primary loopback')).toHaveCount(0);
    await expect(oldPanel.getByRole('alert')).toHaveText('The saved connection changed. Reopen the form to load it.');
    await expect(oldPanel.getByRole('region', { name: 'Verified sample' })).toHaveCount(0);

    await owner.close();
    owner = startLocalImportBridge(binary, profile, folder);
    await page.reload();
    await openApi(page);
    await expect(panel.getByText('No local API connection saved.')).toBeVisible();
    await expect(control(panel, 'Test read')).toBeDisabled();
    expect(traffic).toEqual([['primary', 'GET', '/market-sample', null, null]]);
    await save(panel, 'Replacement loopback', newOrigin);
    await control(oldPanel, 'Disconnect Primary loopback').click();
    await expect.poll(() => rejected).toEqual([['trial', 'STALE_CONTEXT'], ['disconnect', 'STALE_CONTEXT']]);
    await expect(oldPanel.getByRole('alert')).toHaveText('The saved connection changed. Reopen the form to load it.');
    await expect(oldPanel.getByLabel('Connection name')).toHaveValue('Primary loopback');
    expect(traffic).toEqual([['primary', 'GET', '/market-sample', null, null]]);
    const stale = { rejected, displayed: await oldPanel.getByRole('alert').innerText(), traffic: [...traffic] };
    await testInfo.attach('stale-window-rejection', { body: JSON.stringify(stale), contentType: 'application/json' });

    await control(panel, 'Back to my data sources').click();
    await expect(control(panel, 'Local API connection')).toBeFocused();
    await control(panel, 'Local API connection').click();
    await expect(panel.getByLabel('Connection name')).toHaveValue('Replacement loopback');
    await expect(panel.getByLabel('Local API origin')).toHaveValue(newOrigin);
    await control(panel, 'Test read').click();
    await expect(panel.getByRole('region', { name: 'Verified sample' })).toContainText('REPLACEMENT-FUT · 63.5 USD');
    await testInfo.attach('replacement-loopback-sample', { body: await panel.screenshot(), contentType: 'image/png' });

    await control(panel, 'Close').click();
    await control(page, 'Open published CSV').click();
    const library = page.getByRole('dialog', { name: 'Open published CSV' });
    await library.getByRole('button', { name: /Open independent\.csv.*Final close 105/ }).click();
    await expect(library.getByLabel('Selected bar')).toContainText('C 105');
    await expect(library.getByText(csv.revision_id, { exact: true }).first()).toBeVisible();
    await control(library, 'Close').click();
    await control(page, 'Language settings').click();
    await page.getByRole('combobox', { name: 'Interface language' }).selectOption('zh-Hans');
    await page.keyboard.press('Escape');
    const chinese = page.getByRole('dialog', { name: '设置', exact: true });
    await control(page, '设置').click();
    await control(chinese, '数据源').click();
    await control(chinese, '我的数据源').click();
    await control(chinese, '本地 API 连接').click();
    await expect(control(chinese, '断开 Replacement loopback')).toBeVisible();
    expect(traffic).toEqual([['primary', 'GET', '/market-sample', null, null], ['replacement', 'GET', '/market-sample', null, null]]);
    expect(unrelatedRequests).toEqual([]);
    await testInfo.attach('disconnect-journey', { body: JSON.stringify({ firstVisible, stale, csv, traffic }), contentType: 'application/json' });
  } finally {
    for (const server of reads) server.closeAllConnections();
    await oldWindow?.close();
    try { await Promise.all([owner?.close(), peer?.close()]); }
    finally {
      await Promise.all(reads.map(server => new Promise(resolve => server.close(resolve))));
      await rm(root, { recursive: true, force: true });
    }
  }
});

test('validates the exact provider observation time before accepting a local API sample @profile-local-api', async () => {
  const binary = process.env.SCOPEHUB_LOCAL_IMPORT_BRIDGE;
  expect(binary).toBeTruthy();
  const root = await mkdtemp(join(await realpath(tmpdir()), 'scopehub-api-exact-time-'));
  const price = '9007199254740993.000';
  let token = '0';
  const peer = createServer((_, response) => {
    response.writeHead(200, { 'Content-Type': 'application/json' });
    response.end(`{"instrument":"FUT-TEST","observed_at_ms":${token},"value":${price},"unit":"USD"}`);
  }).listen(0, '127.0.0.1');
  let bridge;
  try {
    await once(peer, 'listening');
    bridge = startLocalImportBridge(binary, join(root, 'profile'), root);
    const call = payload => bridge.request('execute_operation', {
      operation: 'market.localApiConnection', payload, requestId: randomUUID(),
    });
    const saved = await call({
      action: 'save', expected_id: null, name: 'Synthetic timestamp samples',
      origin: 'http://127.0.0.1:' + peer.address().port,
    });
    expect(saved.error).toBeUndefined();
    const id = saved.result.connection.id;
    for (const [sourceTime, acceptedTime] of [
      ['1770076800000', 1770076800000],
      ['1.7700768000000e12', 1770076800000],
      ['8640000000000000.0', 8640000000000000],
      ['-0e+99999', 0],
      ['1770076800000.00001', undefined],
      ['1e-999', undefined],
      ['-1e-999', undefined],
      ['8640000000000000.1', undefined],
      ['8640000000000001', undefined],
      ['"1770076800000"', undefined],
    ]) {
      token = sourceTime;
      const reply = await call({ action: 'trial', id });
      if (acceptedTime === undefined) {
        expect.soft(reply.error?.code, token).toBe('OUTPUT_INVALID');
        expect.soft(reply.result, token).toBeUndefined();
      } else {
        expect.soft(reply.result?.sample, token).toEqual({
          instrument: 'FUT-TEST', observed_at_ms: acceptedTime, value: price, unit: 'USD',
        });
      }
    }
  } finally {
    peer.closeAllConnections();
    try { await bridge?.close(); }
    finally {
      await new Promise((resolve, reject) => peer.close(error => error ? reject(error) : resolve()));
      await rm(root, { recursive: true, force: true });
    }
  }
});
