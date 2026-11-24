import { test } from '@playwright/test';
import { checkDesktopCatalogs } from './catalog-check.mjs';

test('each registered language has valid metadata, unique messages and matching named arguments', async () => {
  await checkDesktopCatalogs();
});
