// Prepare the disposable native test profile before the matching Tauri host starts.
import { isAbsolute } from 'node:path';
import { chromium } from '@playwright/test';
import { prepareSeries } from './accepted-drawing-fixture.mjs';

const [scratch, appData, ...extra] = process.argv.slice(2);
if (!scratch || !appData || extra.length || !isAbsolute(scratch) || !isAbsolute(appData)) {
  throw new Error('Usage: node tests/ui/prepare-native-series-profile.mjs <absolute-scratch-directory> <new-absolute-app-data-directory>');
}

const browser = await chromium.launch();
try {
  const { revision, layer_id, version_id, result_hash } = await prepareSeries(browser, scratch, 2, { appData });
  console.log(JSON.stringify({ revision, layer_id, version_id, series_id: 'mean', result_hash }));
} finally {
  await browser.close();
}
