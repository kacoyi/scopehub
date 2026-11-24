import { execFile } from 'node:child_process';
import { mkdir, mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import { prepareProfilePool } from './workspace_profile_pool.mjs';

export default async function setup() {
  const root = fileURLToPath(new URL('../../', import.meta.url));
  // Build before either worker opens the binaries, including on Windows.
  await promisify(execFile)('cargo', [
    'build', '--locked', '-p', 'scopehub-persistence', '--bin', 'workspace_bridge',
    '-p', 'scopehub-worker-host', '--example', 'history_bridge', '--bin', 'scopehub-indicator-child', '--target-dir', 'target',
  ], { cwd: root, timeout: 295_000 });

  const run = await mkdtemp(join(root, 'apps/desktop/ui/node_modules/.vite-workspace-'));
  let profilePool;
  const remove = path => rm(path, { recursive: true, force: true, maxRetries: 3 });
  const cleanup = async () => {
    const removals = await Promise.allSettled([remove(run), ...(profilePool ? [remove(profilePool)] : [])]);
    const failure = removals.find(result => result.status === 'rejected');
    if (failure) throw failure.reason;
  };
  try {
    await mkdir(join(run, 'ports'));
    if (process.platform === 'win32') {
      profilePool = await mkdtemp(join(tmpdir(), 'scopehub-ubuntu-profiles-'));
      const bridge = join(root, 'target', 'debug', 'workspace_bridge.exe');
      await prepareProfilePool(bridge, profilePool);
      process.env.SCOPEHUB_WORKSPACE_TEST_PROFILE_POOL = profilePool;
    }
    process.env.SCOPEHUB_WORKSPACE_TEST_RUN = run;
    return cleanup;
  } catch (error) {
    await cleanup().catch(cleanupError => console.error('Workspace setup cleanup failed.', cleanupError));
    throw error;
  }
}
