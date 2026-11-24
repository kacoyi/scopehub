import { once } from 'node:events';
import { mkdtemp, realpath, rm } from 'node:fs/promises';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test } from '@playwright/test';
import { startLocalImportBridge } from './local-import-bridge.mjs';
import { control } from './workspace-controls.mjs';

test('a named local API survives reopen and reads only on explicit trial @profile-local-api', async ({ page, baseURL }) => {
  const binary = process.env.SCOPEHUB_LOCAL_IMPORT_BRIDGE;
  expect(binary).toBeTruthy();
  const root = await mkdtemp(join(await realpath(tmpdir()), 'scopehub-local-api-'));
  const traffic = [];
  let redirect = false;
  const peer = createServer((request, response) => {
    traffic.push([request.method, request.url, request.headers.authorization, request.headers.cookie]);
    if (redirect) {
      response.writeHead(302, { Location: '/unapproved' }).end();
      return;
    }
    response.writeHead(200, { 'Content-Type': 'application/json' });
    response.end(JSON.stringify({ instrument: 'FUT-TEST', observed_at_ms: 1770076800000, value: 47.25, unit: 'USD' }));
  }).listen(0, '127.0.0.1');
  await once(peer, 'listening');
  const origin = 'http://127.0.0.1:' + peer.address().port;
  let bridge = startLocalImportBridge(binary, join(root, 'profile'), root);
  const unexpected = [];
  page.on('request', request => {
    if (new URL(request.url()).origin !== baseURL) unexpected.push(request.url());
  });
  await page.exposeFunction('dispatchNativeApi', (command, args) => bridge.request(command, args));
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

    redirect = true;
    await control(panel, 'Test read').click();
    await expect(panel.getByRole('alert')).toHaveText('The trial did not return a usable market sample.');
    await expect(sample).toHaveCount(0);
    expect(traffic).toEqual(Array.from({ length: 2 }, () => ['GET', '/market-sample', undefined, undefined]));
    expect(unexpected).toEqual([]);
  } finally {
    try { await bridge.close(); }
    finally {
      peer.closeAllConnections();
      await new Promise((resolve, reject) => peer.close(error => error ? reject(error) : resolve()));
      await rm(root, { recursive: true, force: true });
    }
  }
});
