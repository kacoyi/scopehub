import { resolve } from 'node:path';
import { defineConfig } from '@playwright/test';
import preview from './playwright.config.mjs';

export function marketPreviewConfig({ spec, port, artifactDirectory, scope, trace = preview.use.trace }) {
  const baseURL = `http://127.0.0.1:${port}`;
  const output = resolve(process.env.SCOPEHUB_RUN_DIR ?? '.reports', artifactDirectory);
  return defineConfig({
    ...preview,
    testMatch: spec,
    outputDir: resolve(output, 'browser'),
    reporter: [['list'], ['json', { outputFile: resolve(output, 'results.json') }]],
    metadata: { scope },
    use: { ...preview.use, baseURL, trace },
    webServer: { ...preview.webServer, command: `pnpm dev:ubuntu --port ${port}`, url: baseURL },
  });
}
