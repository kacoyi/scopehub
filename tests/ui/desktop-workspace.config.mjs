import { randomUUID } from 'node:crypto';
import { once } from 'node:events';
import { createServer } from 'node:net';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig } from '@playwright/test';

const root = fileURLToPath(new URL('../../', import.meta.url));
async function freePort() {
  const probe = createServer().listen(0, '127.0.0.1');
  await once(probe, 'listening');
  const { port } = probe.address();
  await new Promise((resolve, reject) => probe.close(error => error ? reject(error) : resolve()));
  return String(port);
}

const port = process.env.SCOPEHUB_DESKTOP_WORKSPACE_TEST_PORT ??= await freePort();
const baseURL = `http://127.0.0.1:${port}`;
const output = process.env.SCOPEHUB_RUN_DIR
  ? resolve(process.env.SCOPEHUB_RUN_DIR, 'desktop-workspace-transport')
  : (process.env.SCOPEHUB_DESKTOP_WORKSPACE_REPORT_DIR ??= resolve(root, '.reports', 'desktop-workspace', `${Date.now()}-${randomUUID()}`));

export default defineConfig({
  testDir: '.',
  testMatch: 'desktop-workspace.spec.mjs',
  workers: 1,
  timeout: 45_000,
  metadata: { scope: 'Production UI with substituted Tauri IPC. Native storage and authorization use the portable Rust bridge tests.' },
  outputDir: resolve(output, 'browser'),
  reporter: [['list'], ['json', { outputFile: resolve(output, 'results.json') }]],
  use: { baseURL, viewport: { width: 1366, height: 900 }, screenshot: 'only-on-failure' },
  webServer: {
    command: `pnpm build && pnpm --filter @scopehub/ui exec vite preview --host 127.0.0.1 --port ${port} --strictPort`,
    cwd: root, url: baseURL, reuseExistingServer: false, timeout: 30_000,
  },
});
