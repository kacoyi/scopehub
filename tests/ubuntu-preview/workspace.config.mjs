import { randomUUID } from 'node:crypto';
import { resolve } from 'node:path';
import { defineConfig } from '@playwright/test';

const output = process.env.SCOPEHUB_RUN_DIR
  ? resolve(process.env.SCOPEHUB_RUN_DIR, 'workspace-timeframe')
  : (process.env.SCOPEHUB_UBUNTU_WORKSPACE_REPORT_DIR ??= resolve('.reports', 'ubuntu-workspace', `${Date.now()}-${randomUUID()}`));
// Fixed-port reproductions can reuse a port in another case.
const fixedPort = process.env.SCOPEHUB_EVIDENCE_TEST_PORT !== undefined
  || process.env.SCOPEHUB_EVIDENCE_REVOCATION_PORT !== undefined;
const nativeCsv = /@native-csv(?:\s|$)/;
const prepareNativeCsv = /@prepare-native-csv(?:\s|$)/;

export default defineConfig({
  testDir: '.',
  testMatch: 'workspace.spec.mjs',
  globalSetup: './workspace.setup.mjs',
  fullyParallel: !fixedPort,
  workers: fixedPort ? 1 : 2,
  timeout: 60_000,
  projects: [
    { grepInvert: [nativeCsv, prepareNativeCsv] },
    { name: 'prepare-native-csv', default: false, grep: prepareNativeCsv, timeout: 300_000 },
    { name: 'native-csv', grep: nativeCsv, dependencies: ['prepare-native-csv'] },
  ],
  outputDir: resolve(output, 'browser'),
  reporter: [['list'], ['json', { outputFile: resolve(output, 'results.json') }]],
  metadata: { scope: 'Opted-in Ubuntu Vite workspace with an isolated synthetic SQLite profile.' },
  use: { viewport: { width: 1366, height: 900 }, screenshot: 'only-on-failure' },
});
