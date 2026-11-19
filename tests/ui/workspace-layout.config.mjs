import { randomUUID } from 'node:crypto';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig } from '@playwright/test';

const root = fileURLToPath(new URL('../../', import.meta.url));
const report = process.env.SCOPEHUB_RUN_DIR
  ? resolve(process.env.SCOPEHUB_RUN_DIR, 'workspace-layout')
  : (process.env.SCOPEHUB_WORKSPACE_LAYOUT_REPORT_DIR
    ??= resolve(root, '.reports', 'workspace-layout', `${Date.now()}-${randomUUID()}`));

export default defineConfig({
  testDir: '.',
  testMatch: 'workspace-layout.spec.mjs',
  workers: 1,
  timeout: 65_000,
  metadata: { scope: 'Real Ubuntu workspace transport, Rust registry, SQLite profile, and browser chart with synthetic fixtures.' },
  outputDir: resolve(report, 'browser'),
  reporter: [['list'], ['json', { outputFile: resolve(report, 'results.json') }]],
  use: { viewport: { width: 1366, height: 900 }, screenshot: 'only-on-failure' },
});
