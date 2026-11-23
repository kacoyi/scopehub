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
  testDir: '.', workers: 1,
  projects: [
    { name: 'Chromium', testMatch: 'renderer-probe.spec.mjs' },
    ...(process.platform === 'win32' ? [{ name: 'Windows WebView2', testMatch: 'renderer-probe-windows.spec.mjs' }] : []),
  ],
  metadata: { scope: 'Frozen synthetic renderer interaction in Chromium and the native WebView2 host on Windows.' },
  outputDir: resolve(output, 'browser'),
  reporter: [['list'], ['json', { outputFile: resolve(output, 'results.json') }]],
  use: { baseURL, viewport: { width: 1366, height: 900 }, screenshot: 'only-on-failure' },
  webServer: {
    command: `pnpm --filter @scopehub/ui exec vite --host 127.0.0.1 --port ${port} --strictPort`,
    cwd: root, url: baseURL, reuseExistingServer: false, timeout: 60_000,
  },
});
