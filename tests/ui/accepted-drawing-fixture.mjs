import { createHash, randomUUID } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { expect } from '@playwright/test';
import { readPublishedWorkspaceProfile, startLocalImportBridge } from './local-import-bridge.mjs';
import { withWorkspace } from './ai-workflow-fixture.mjs';

const csv = 'Date,Symbol,Open,High,Low,Close,Volume\n'
  + '2026-02-03,FIX,10,11,9,10,110\n2026-02-04,FIX,14,15,13,14,120\n'
  + '2026-02-05,FIX,18,19,17,18,130\n2026-02-06,FIX,16,17,15,16,140\n';
const mapping = {
  version: 1, revision: 'accepted-numeric-series', encoding: 'utf-8', delimiter: ',',
  instrument_kind: 'equity', venue: 'FIXTURE', timeframe: '1d', calendar_id: 'fixture-calendar',
  session_scope: 'regular', timezone: 'UTC', quantity_unit: 'shares', price_adjustment: 'raw',
  record_kind: 'native_ohlcv', volume_semantics: 'per_bar', timestamp_convention: 'bar_start',
  timestamp_format: '%Y-%m-%d', trading_day_format: '%Y-%m-%d', daily_start_time: '00:00:00',
  columns: { symbol: 'Symbol', timestamp: 'Date', trading_day: 'Date', open: 'Open', high: 'High',
    low: 'Low', close: 'Close', volume: 'Volume' },
};
const digest = value => createHash('sha256').update(value).digest('hex');
const button = (scope, name) => scope.getByRole('button', { name, exact: true });

async function withImport(root, use) {
  const binary = join(process.cwd(), 'target/debug/examples', process.platform === 'win32' ? 'local_import_bridge.exe' : 'local_import_bridge');
  const bridge = startLocalImportBridge(binary, join(root, 'app'), join(root, 'fixture'));
  const market = async (operation, payload) => {
    const reply = await bridge.request('execute_operation', {
      operation: 'market.' + operation, requestId: randomUUID(), payload,
    });
    expect(reply.error).toBeUndefined();
    return reply.result;
  };
  try { return await use(bridge, market); } finally { await bridge.close(); }
}

export const revoke = (root, revision_id) => withImport(root, (_bridge, market) =>
  market('revokeLocalImportRevision', { version: 1, revision_id }));

export async function prepareSeries(browser, root, period = 2) {
  await mkdir(join(root, 'app'), { mode: 0o700 });
  await mkdir(join(root, 'fixture'), { mode: 0o700 });
  await writeFile(join(root, 'fixture/series.csv'), csv);
  const revision = await withImport(root, async (bridge, market) => {
    const { result } = await bridge.request('choose_local_csv_folder', {});
    const input = { version: 1, folder_id: result.folder_id, file_id: result.files[0].file_id, expected_sha256: digest(csv) };
    await market('previewLocalImport', { ...input, row_limit: 10, mapping });
    const { record } = await market('saveLocalImportMapping', { ...input, row_limit: 10, mapping });
    return (await market('publishLocalImport', { ...input, review_revision: record.review_revision })).revision.revision_id;
  });
  const profile = readPublishedWorkspaceProfile(join(root, 'app'));
  let identity;
  await withWorkspace(browser, profile, async (page, dispatch) => {
    await button(page, 'Open published CSV').click();
    const dialog = page.getByRole('dialog', { name: 'Open published CSV' });
    await dialog.getByLabel('Revision ID').fill(revision);
    await button(dialog, 'Open frozen chart').click();
    const indicator = dialog.getByRole('region', { name: 'Indicator preview' });
    await indicator.getByLabel('Indicator input').selectOption('volume');
    await indicator.getByLabel('Rolling period').fill(String(period));
    await button(indicator, 'Run preview').click();
    await button(indicator, 'Accept preview').click();
    const listing = { type: 'indicator_accepted_layers', request_id: randomUUID(), revision_id: revision };
    await expect(button(indicator, 'Confirm indicator acceptance')).toBeVisible();
    await expect.poll(async () => (await dispatch(listing)).result?.layers).toEqual([]);
    const committed = page.waitForResponse(response => response.request().postDataJSON()?.operation === 'confirmation.respond');
    await button(indicator, 'Confirm indicator acceptance').click();
    const { layer_id, version_id } = (await (await committed).json()).result.outcome.result;
    let output;
    await expect.poll(async () => {
      output = (await dispatch({ type: 'indicator_accepted_output', request_id: randomUUID(), layer_id })).result;
      return output?.version_id;
    }).toBe(version_id);
    expect(output.version_id).toBe(version_id);
    identity = { revision, layer_id, version_id, result_hash: output.document.artifact.result_hash };
    await button(dialog, 'Use as workspace source').click();
    await expect(dialog).toHaveCount(0);
  });
  return { profile, ...identity };
}
