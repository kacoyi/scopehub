import { randomUUID } from 'node:crypto';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig } from '@playwright/test';

const root = fileURLToPath(new URL('../../', import.meta.url));
const report = process.env.SCOPEHUB_RUN_DIR
  ? resolve(process.env.SCOPEHUB_RUN_DIR, 'ai-workflow')
  : (process.env.SCOPEHUB_AI_WORKFLOW_REPORT_DIR
    ??= resolve(root, '.reports', 'ai-workflow', `${Date.now()}-${randomUUID()}`));

export default defineConfig({
  testDir: '.',
  testMatch: ['ai-workflow.spec.mjs', 'ai-workflow-route-guard.spec.mjs'],
  workers: 1,
  timeout: 60_000,
  metadata: { scope: 'Ordinary text and bounded selected-chart turns through the Rust registry and an owned synthetic HTTP peer.' },
  outputDir: resolve(report, 'browser'),
  reporter: [['list'], ['json', { outputFile: resolve(report, 'results.json') }]],
  use: { viewport: { width: 1366, height: 900 }, screenshot: 'only-on-failure' },
});
