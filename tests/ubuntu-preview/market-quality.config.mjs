import { resolve } from 'node:path';
import { defineConfig } from '@playwright/test';
import preview from './playwright.config.mjs';

const port = Number(process.env.SCOPEHUB_MARKET_QUALITY_PORT ?? 1426);
const baseURL = `http://127.0.0.1:${port}`;
const output = resolve(process.env.SCOPEHUB_RUN_DIR ?? '.reports', 'market-quality-states');

export default defineConfig({
  ...preview,
  testMatch: 'market-quality.spec.mjs',
  outputDir: resolve(output, 'browser'),
  reporter: [['list'], ['json', { outputFile: resolve(output, 'results.json') }]],
  metadata: { scope: 'Synthetic market quality through the loopback chart with controlled loading and error responses.' },
  use: { ...preview.use, baseURL, trace: 'off' },
  webServer: { ...preview.webServer, command: `pnpm dev:ubuntu --port ${port}`, url: baseURL },
});
