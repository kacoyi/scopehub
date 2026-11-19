import { randomUUID } from 'node:crypto';
import { basename, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig } from '@playwright/test';

const root = fileURLToPath(new URL('../../', import.meta.url));
const baseURL = 'http://127.0.0.1:1421';
const ciRun = process.env.GITHUB_ACTIONS === 'true' && process.env.SCOPEHUB_RUN_DIR;
// Playwright also loads this file in its workers. Keep their artifacts in the same run.
const output = ciRun
  ? join(root, '.reports', 'ubuntu-preview', basename(ciRun))
  : (process.env.SCOPEHUB_UBUNTU_REPORT_DIR ??= join(
    root, '.reports', 'ubuntu-preview', `${Date.now()}-${randomUUID()}`,
  ));

export default defineConfig({
  testDir: '.',
  testMatch: 'preview.spec.mjs',
  workers: 1,
  retries: 0,
  timeout: 60_000,
  metadata: { scope: 'Local browser development preview with the real synthetic history operation endpoint.' },
  outputDir: join(output, 'browser'),
  reporter: [['list'], ['json', { outputFile: join(output, 'results.json') }]],
  use: {
    baseURL,
    viewport: { width: 1366, height: 900 },
    screenshot: 'only-on-failure',
    trace: ciRun ? 'off' : 'retain-on-failure',
  },
  webServer: {
    command: 'pnpm dev:ubuntu --port 1421',
    cwd: root,
    url: baseURL,
    reuseExistingServer: false,
    timeout: 300_000,
  },
});
