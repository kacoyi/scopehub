// Prepare the disposable native test profile before the matching Tauri host starts.
import { isAbsolute } from 'node:path';
import { chromium } from '@playwright/test';
import { prepareSeries, revoke } from './accepted-drawing-fixture.mjs';

const [scratch, appData, operation, revision, ...extra] = process.argv.slice(2);
if (!scratch || !appData || extra.length || !isAbsolute(scratch) || !isAbsolute(appData)) {
  throw new Error('Expected the disposable scratch and native app directories.');
}
if (operation === '--revoke' && /^[a-f0-9-]{36}$/.test(revision)) {
  // Same selected private profile and synthetic fixture. This calls the public registered operation.
  const reply = await revoke(scratch, revision, appData);
  if (reply.version !== 1 || reply.revision_id !== revision) throw new Error('Unexpected revoked revision.');
  console.log(JSON.stringify(reply));
} else if (!operation && !revision) {
  const browser = await chromium.launch();
  try {
    const { revision, layer_id, version_id, result_hash } = await prepareSeries(browser, scratch, 2, { appData });
    console.log(JSON.stringify({ revision, layer_id, version_id, series_id: 'mean', result_hash }));
  } finally {
    await browser.close();
  }
} else {
  throw new Error('Unknown native test request.');
}
