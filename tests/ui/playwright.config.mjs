import { defineConfig } from '@playwright/test';

const port = 1422;
const baseURL = `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: '.', testMatch: 'history.spec.mjs', workers: 1, retries: 0, timeout: 45_000,
  outputDir: process.env.SCOPEHUB_HISTORY_REPORT_DIR ? `${process.env.SCOPEHUB_HISTORY_REPORT_DIR}/browser` : '../../.reports/chart-history/browser',
  reporter: [['list'], ['json', { outputFile: process.env.SCOPEHUB_HISTORY_REPORT_DIR ? `${process.env.SCOPEHUB_HISTORY_REPORT_DIR}/browser-results.json` : '.reports/chart-history/browser-results.json' }]],
  use: { baseURL, viewport: { width: 1366, height: 900 }, screenshot: 'only-on-failure', trace: 'retain-on-failure' },
  webServer: { command: `pnpm --filter @scopehub/ui exec vite preview --host 127.0.0.1 --port ${port} --strictPort`, url: baseURL, reuseExistingServer: false, timeout: 30_000, cwd: '../..' },
});
