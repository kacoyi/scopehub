import { execFile } from 'node:child_process';
import { mkdir, mkdtemp, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';

export default async function setup() {
  const root = fileURLToPath(new URL('../../', import.meta.url));
  // Build before either worker opens the binaries, including on Windows.
  await promisify(execFile)('cargo', [
    'build', '--locked', '-p', 'scopehub-persistence', '--bin', 'workspace_bridge',
    '-p', 'scopehub-worker-host', '--example', 'history_bridge', '--bin', 'scopehub-indicator-child', '--target-dir', 'target',
  ], { cwd: root, timeout: 295_000 });

  const run = await mkdtemp(join(root, 'apps/desktop/ui/node_modules/.vite-workspace-'));
  const cleanup = () => rm(run, { recursive: true, force: true, maxRetries: 3 });
  try {
    await mkdir(join(run, 'ports'));
    process.env.SCOPEHUB_WORKSPACE_TEST_RUN = run;
    return cleanup;
  } catch (error) {
    await cleanup();
    throw error;
  }
}
