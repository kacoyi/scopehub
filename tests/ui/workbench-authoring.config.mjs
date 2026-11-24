import { randomUUID } from 'node:crypto';
import { resolve } from 'node:path';
import { defineConfig } from '@playwright/test';

const output = process.env.SCOPEHUB_RUN_DIR
  ? resolve(process.env.SCOPEHUB_RUN_DIR, 'workbench-authoring')
  : (process.env.SCOPEHUB_WORKBENCH_AUTHORING_REPORT_DIR
    ??= resolve('.reports', 'workbench-authoring', randomUUID()));

export default defineConfig({
  testDir: '.',
  testMatch: ['workbench-authoring.spec.mjs', 'drawing-workbench.spec.mjs', 'drawing-series.spec.mjs', 'accepted-drawing.spec.mjs'],
  workers: 1,
  timeout: 90_000,
  metadata: { scope: 'Embedded model turn with a test-owned HTTP peer. No connected-model quality or native renderer acceptance.' },
  outputDir: resolve(output, 'browser'),
  reporter: [['list'], ['json', { outputFile: resolve(output, 'results.json') }]],
  use: { viewport: { width: 1366, height: 900 }, screenshot: 'only-on-failure' },
});
