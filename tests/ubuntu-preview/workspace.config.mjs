import { randomUUID } from 'node:crypto';
import { resolve } from 'node:path';
import { defineConfig } from '@playwright/test';

const output = process.env.SCOPEHUB_RUN_DIR
  ? resolve(process.env.SCOPEHUB_RUN_DIR, 'workspace-timeframe')
  : (process.env.SCOPEHUB_UBUNTU_WORKSPACE_REPORT_DIR ??= resolve('.reports', 'ubuntu-workspace', `${Date.now()}-${randomUUID()}`));

export default defineConfig({
  testDir: '.',
  testMatch: 'workspace.spec.mjs',
  workers: 1,
  timeout: 60_000,
  outputDir: resolve(output, 'browser'),
  reporter: [['list'], ['json', { outputFile: resolve(output, 'results.json') }]],
  metadata: { scope: 'Opted-in Ubuntu Vite workspace with an isolated synthetic SQLite profile.' },
  use: { viewport: { width: 1366, height: 900 }, screenshot: 'only-on-failure' },
});
