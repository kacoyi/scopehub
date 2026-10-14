import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: '.', testMatch: '*.spec.mjs', workers: 1, retries: 0, timeout: 45_000,
  outputDir: process.env.SCOPEHUB_HISTORY_REPORT_DIR ? `${process.env.SCOPEHUB_HISTORY_REPORT_DIR}/browser` : '../../.reports/chart-history/browser',
  reporter: [['list'], ['json', { outputFile: process.env.SCOPEHUB_HISTORY_REPORT_DIR ? `${process.env.SCOPEHUB_HISTORY_REPORT_DIR}/browser-results.json` : '.reports/chart-history/browser-results.json' }]],
  use: { baseURL: 'http://127.0.0.1:1420', viewport: { width: 1366, height: 900 }, screenshot: 'only-on-failure', trace: 'retain-on-failure' },
  webServer: { command: 'pnpm --filter @scopehub/ui exec vite preview --host 127.0.0.1 --port 1420 --strictPort', url: 'http://127.0.0.1:1420', reuseExistingServer: false, timeout: 30_000, cwd: '../..' },
});
