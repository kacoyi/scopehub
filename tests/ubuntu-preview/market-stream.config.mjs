import { resolve } from 'node:path';
import { defineConfig } from '@playwright/test';
import preview from './playwright.config.mjs';

const port = Number(process.env.SCOPEHUB_MARKET_STREAM_PORT ?? 1425);
const baseURL = `http://127.0.0.1:${port}`;
const output = resolve(process.env.SCOPEHUB_RUN_DIR ?? '.reports', 'market-live-stream');

export default defineConfig({
  ...preview,
  testMatch: 'market-stream.spec.mjs',
  outputDir: resolve(output, 'browser'),
  reporter: [['list'], ['json', { outputFile: resolve(output, 'results.json') }]],
  metadata: { scope: 'Explicit synthetic replay via the ordinary local development chart and worker.' },
  use: { ...preview.use, baseURL },
  webServer: { ...preview.webServer, command: `pnpm dev:ubuntu --port ${port}`, url: baseURL },
});
