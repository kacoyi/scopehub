import { randomUUID } from 'node:crypto';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig } from '@playwright/test';

const root = fileURLToPath(new URL('../../', import.meta.url));
const output = process.env.SCOPEHUB_RUN_DIR
  ? resolve(process.env.SCOPEHUB_RUN_DIR, 'windows-published-revision-access')
  : resolve(root, '.reports', 'windows-published-revision-access', randomUUID());

export default defineConfig({
  testDir: '.',
  testMatch: 'published-revision-access-windows.spec.mjs',
  workers: 1,
  timeout: 36 * 60_000,
  expect: { timeout: 5000 },
  outputDir: resolve(output, 'webview2'),
  reporter: [['list'], ['json', { outputFile: resolve(output, 'results.json') }]],
});
