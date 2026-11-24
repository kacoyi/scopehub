import { randomUUID } from 'node:crypto';
import { resolve } from 'node:path';
import { defineConfig } from '@playwright/test';

const run = process.env.SCOPEHUB_RUN_DIR ?? resolve('.reports', 'renderer-comparison', randomUUID());
const output = resolve(run, 'shared-renderer-workload');
export default defineConfig({
  testDir: '.', testMatch: 'renderer-comparison.spec.mjs', workers: 1, timeout: 90_000,
  outputDir: resolve(output, 'browser'), reporter: [['list'], ['json', { outputFile: resolve(output, 'results.json') }]],
  use: { viewport: { width: 1280, height: 800 }, screenshot: 'only-on-failure' },
});
