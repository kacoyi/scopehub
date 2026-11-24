import { spawn } from 'node:child_process';
import { lstat, mkdir, mkdtemp, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { createInterface } from 'node:readline';
import { fileURLToPath } from 'node:url';

const supervisor = fileURLToPath(new URL('../../scripts/prepare_workspace_profile_pool.py', import.meta.url));
const profileCount = 64;
const preparationTimeoutMs = 12_000;
const readyLine = 'scopehub-workspace-profile-ready';
const nextSlots = new Map();

function deadlineError() {
  return Object.assign(new Error('Workspace profile preparation reached its deadline.'), { code: 'ETIMEDOUT' });
}

function runPreparation(python, bridge, pool, count, deadline, remaining) {
  const child = spawn(python, ['-B', supervisor, bridge, pool, String(count), String(deadline)], {
    windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'],
  });
  return new Promise((resolve, reject) => {
    let granted = false;
    let denied = false;
    let launchError;
    let stderr = '';
    const read = createInterface({ input: child.stdout, crlfDelay: Infinity });
    const denyStartup = error => {
      if (denied) return;
      denied = true;
      read.close();
      // No authorization has been sent. Even a surviving redirector descendant
      // sees EOF and cannot start work or acquire the pool.
      child.stdin.destroy();
      child.stdout.destroy();
      child.stderr.destroy();
      child.kill();
      reject(error);
    };
    const startup = setTimeout(() => denyStartup(deadlineError()), Math.max(0, remaining()));
    child.stderr.setEncoding('utf8').on('data', chunk => {
      stderr = (stderr + chunk).slice(-8192);
    });
    child.on('error', error => { launchError = error; });
    child.stdin.on('error', error => { launchError ??= error; });
    read.once('line', line => {
      if (denied) return;
      clearTimeout(startup);
      if (remaining() <= 0) {
        denyStartup(deadlineError());
      } else if (line !== readyLine) {
        denyStartup(new Error('Workspace profile supervisor returned invalid readiness.'));
      } else {
        granted = true;
        child.stdin.end('1');
      }
    });
    child.once('close', (code, signal) => {
      clearTimeout(startup);
      read.close();
      if (granted && code === 0) {
        resolve();
      } else {
        reject(Object.assign(launchError ?? new Error(stderr.trim() || 'Workspace profile preparation failed.'), {
          code: launchError?.code ?? code, signal, stderr,
        }));
      }
    });
  });
}

export async function prepareProfilePool(python, bridge, pool, count = profileCount) {
  const started = performance.now();
  const remaining = () => preparationTimeoutMs - (performance.now() - started);
  try {
    await runPreparation(python, bridge, pool, count, Date.now() + preparationTimeoutMs, remaining);
    if (remaining() <= 0) {
      // A successful supervisor exit confirms the tree is stopped. An expired
      // result cannot enter the test run and this finished pool can be removed.
      await rm(pool, { recursive: true, force: true, maxRetries: 3 })
        .catch(error => console.error('Expired workspace profile pool cleanup failed.', error));
      throw deadlineError();
    }
  } catch (error) {
    console.error(`Workspace profile preparation failed after ${Math.round(performance.now() - started)}ms.`);
    throw error;
  }
  console.info(`Prepared ${count} isolated workspace profiles in ${Math.round(performance.now() - started)}ms.`);
}

export async function claimProfile(pool, count = profileCount) {
  if (!pool) throw new Error('The workspace profile pool was not prepared.');
  for (let index = nextSlots.get(pool) ?? 0; index < count; index = nextSlots.get(pool)) {
    nextSlots.set(pool, index + 1);
    const slot = String(index);
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


export async function withProfilePoolAclControl(parent, use) {
  const control = await mkdtemp(join(parent, 'scopehub-workspace-outer-acl-'));
  const pending = new Set();
  const prepare = async (python, bridge, pool) => {
    pending.add(pool);
    await prepareProfilePool(python, bridge, pool, 1);
    pending.delete(pool);
  };
  let failed = false;
  try {
    await use(control, prepare);
  } catch (error) {
    failed = true;
    throw error;
  } finally {
    try {
      for (const pool of pending) {
        try {
          await lstat(pool);
          throw new Error(`Retaining ACL control with an unconfirmed pool cleanup: ${pool}`);
        } catch (error) {
          if (error.code !== 'ENOENT') throw error;
        }
      }
      await rm(control, { recursive: true, force: true, maxRetries: 3 });
    } catch (error) {
      if (!failed) throw error;
      console.error('Workspace outer ACL control cleanup failed.', error);
    }
  }
}
