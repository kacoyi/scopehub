import { defineConfig } from '@playwright/test';
import { resolve } from 'node:path';

const port = 17351;
const baseURL = `http://127.0.0.1:${port}`;
const report = resolve(process.env.SCOPEHUB_RUN_DIR ?? '.reports', 'local-import-ui');

export default defineConfig({
  testDir: '.', testMatch: 'local-import.spec.mjs', workers: 1, retries: 0, timeout: 45_000,
  outputDir: `${report}/browser`,
  reporter: [['list'], ['json', { outputFile: `${report}/browser-results.json` }]],
  use: { baseURL, viewport: { width: 1366, height: 900 }, actionTimeout: 10_000, screenshot: 'only-on-failure', trace: 'retain-on-failure' },
  webServer: {
    command: `pnpm build && pnpm --filter @scopehub/ui exec vite preview --host 127.0.0.1 --port ${port} --strictPort`,
    url: baseURL, reuseExistingServer: false, timeout: 30_000, cwd: '../..',
  },
});
