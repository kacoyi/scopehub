import { execFile } from 'node:child_process';
import { lstat, mkdir } from 'node:fs/promises';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';

const exec = promisify(execFile);
const supervisor = fileURLToPath(new URL('../../scripts/prepare_workspace_profile_pool.py', import.meta.url));
const profileCount = 64;
let nextSlot = 0;

export async function prepareProfilePool(python, bridge, pool) {
  // The first cold start is included in the same outer limit as a single profile.
  const started = performance.now();
  try {
    // The supervisor enforces the deadline and waits for all owned descendants.
    // Killing the supervisor here could race its cleanup and leave a live writer.
    const deadline = Date.now() + 12_000;
    await exec(python, ['-B', supervisor, bridge, pool, String(profileCount), String(deadline)], { windowsHide: true });
  } catch (error) {
    console.error(`Workspace profile preparation failed after ${Math.round(performance.now() - started)}ms.`);
    throw error;
  }
  console.info(`Prepared ${profileCount} isolated workspace profiles in ${Math.round(performance.now() - started)}ms.`);
}

export async function claimProfile(pool) {
  if (!pool) throw new Error('The workspace profile pool was not prepared.');
  while (nextSlot < profileCount) {
    const slot = String(nextSlot++);
    try {
      await mkdir(join(pool, 'claims', slot));
    } catch (error) {
      if (error.code === 'EEXIST') continue;
      throw error;
    }
    // Claims are kept until global teardown. A later case never reuses this root.
    const root = join(pool, 'profiles', `profile-${slot}`);
    if (!(await lstat(root)).isDirectory()) throw new Error('The prepared workspace profile is unavailable.');
    return root;
  }
}
