import { randomUUID } from 'node:crypto';
import { execFile } from 'node:child_process';
import { copyFile, mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import { prepareProfilePool } from './workspace_profile_pool.mjs';

export default async function setup() {
  const root = fileURLToPath(new URL('../../', import.meta.url));
  const exec = promisify(execFile);
  const windows = process.platform === 'win32';
  const witness = windows ? ['-p', 'scopehub-platform', '--example', 'windows_handoff_probe'] : [];
  // Build before either worker opens the binaries, including on Windows.
  await exec('cargo', [
    'build', '--locked', '-p', 'scopehub-persistence', '--bin', 'workspace_bridge',
    '-p', 'scopehub-worker-host', '--example', 'history_bridge', '--bin', 'scopehub-indicator-child', ...witness, '--target-dir', 'target',
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
    const { stdout, stderr } = await exec('cargo', [
      'build', '--locked', '-p', 'scopehub-desktop', '--example', 'local_import_bridge',
      '--features', 'portable-test-bridge', '--target-dir', 'target',
    ], { cwd: root, timeout: 295_000 });
    const name = windows ? 'local_import_bridge.exe' : 'local_import_bridge';
    const binary = join(run, name);
    // Workers execute a run-owned copy so they never hold Cargo's linker output open.
    await copyFile(join(root, 'target', 'debug', 'examples', name), binary);
    await writeFile(join(run, 'local-import-bridge-build.txt'), `Prepared ${binary}\n${stdout}${stderr}`);
    if (windows) {
      const pendingPool = join(tmpdir(), 'scopehub-ubuntu-profiles-' + randomUUID());
      const bridge = join(root, 'target', 'debug', 'workspace_bridge.exe');
      const python = join(root, '.venv', 'Scripts', 'python.exe');
      // Only accept the directory after its supervisor has finished and reaped
      // its children. The supervisor owns cleanup on a failed preparation.
      await prepareProfilePool(python, bridge, pendingPool);
      profilePool = pendingPool;
      process.env.SCOPEHUB_WORKSPACE_TEST_PROFILE_POOL = profilePool;
    }
    process.env.SCOPEHUB_WORKSPACE_TEST_RUN = run;
    return cleanup;
  } catch (error) {
    await cleanup().catch(cleanupError => console.error('Workspace setup cleanup failed.', cleanupError));
    throw error;
  }
}
