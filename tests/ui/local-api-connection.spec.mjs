import { once } from 'node:events';
import { mkdtemp, realpath, rm } from 'node:fs/promises';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test } from '@playwright/test';
import { startLocalImportBridge } from './local-import-bridge.mjs';
import { control } from './workspace-controls.mjs';

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
  await page.exposeFunction('dispatchNativeApi', async (command, args) => {
    const action = args?.operation === 'market.localApiConnection' && args.payload.action;
    if (action === 'status' && rejectNextStatus) {
      rejectNextStatus = false;
      return { error: { code: 'BUSY' } };
    }
    const reply = await bridge.request(command, args);
    if (action === 'trial') lastTrialReply = reply;
    return reply;
  });
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
  const panel = page.getByRole('dialog', { name: 'Settings' });
  async function openApi() {
    await control(page, 'Settings').click();
    await control(panel, 'Data sources').click();
    await control(panel, 'My data sources').click();
    await expect(control(panel, 'Choose local CSV folder')).toBeVisible();
    await control(panel, 'Local API connection').click();
  }

  try {
    await page.goto('/');
    await openApi();
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
    await openApi();
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
