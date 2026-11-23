import { randomUUID } from 'node:crypto';
import { once } from 'node:events';
import { createServer } from 'node:net';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig } from '@playwright/test';

const root = fileURLToPath(new URL('../../', import.meta.url));
async function freePort() {
  const server = createServer().listen(0, '127.0.0.1');
  await once(server, 'listening');
  const { port } = server.address();
  await new Promise((done, reject) => server.close(error => error ? reject(error) : done()));
  return String(port);
}

const port = process.env.SCOPEHUB_RENDERER_PROBE_PORT ??= await freePort();
const baseURL = `http://127.0.0.1:${port}`;
const output = process.env.SCOPEHUB_RUN_DIR
  ? resolve(process.env.SCOPEHUB_RUN_DIR, 'renderer-probe')
  : (process.env.SCOPEHUB_RENDERER_PROBE_REPORT_DIR ??= resolve(root, '.reports', 'renderer-probe', randomUUID()));

export default defineConfig({
  testDir: '.', testMatch: 'renderer-probe.spec.mjs', workers: 1,
  metadata: { scope: 'Chromium development-host interaction with the frozen synthetic renderer fixture.' },
  outputDir: resolve(output, 'browser'),
  reporter: [['list'], ['json', { outputFile: resolve(output, 'results.json') }]],
  use: { baseURL, viewport: { width: 1366, height: 900 }, screenshot: 'only-on-failure' },
  webServer: {
    command: `pnpm --filter @scopehub/ui exec vite --host 127.0.0.1 --port ${port} --strictPort`,
    cwd: root, url: baseURL, reuseExistingServer: false, timeout: 60_000,
  },
});
