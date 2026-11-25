import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { chmod, readFile, rm, writeFile } from 'node:fs/promises';
import { constants } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';

const exec = promisify(execFile);
const witness = fileURLToPath(new URL('../../target/debug/examples/windows_handoff_probe.exe', import.meta.url));
const stages = {
  51: 'file type',
  52: 'directory owner',
  53: 'file owner and DACL',
  54: 'inherited access',
  55: 'effective read/write',
  56: 'directory descriptor read',
  57: 'file descriptor read',
  58: 'unexpected ACL trustee',
  59: 'current user identity',
};

const startupCodes = new Set(['EACCES', 'EAGAIN', 'EINVAL', 'EMFILE', 'ENFILE', 'ENOENT', 'ENOEXEC', 'ENOMEM', 'ENOTDIR', 'EPERM']);

function probeFailure(error, child, exitStages) {
  if (error.code === 'ERR_CHILD_PROCESS_STDIO_MAXBUFFER') return 'subprocess output limit exceeded';
  if (Number.isInteger(error.code)) {
    return exitStages[error.code] ?? `subprocess exited with code ${error.code}`;
  }
  // Node can set error.killed when a deadline only closes inherited pipes.
  // child.killed means a signal was actually sent to the direct child.
  if (child?.killed) return 'subprocess deadline exceeded';
  if (error.signal && Object.hasOwn(constants.signals, error.signal)) {
    return `subprocess terminated by ${error.signal}`;
  }
  if (startupCodes.has(error.code)) return `subprocess startup failed (${error.code})`;
  return 'subprocess startup failed';
}

export async function runHandoffProbe(executable, args, { timeout = 10_000, maxBuffer = 8192, env, exitStages = stages } = {}) {
  let child;
  try {
    // Let execFile close the pipes at its deadline and wait for the direct child.
    const execution = exec(executable, args, { timeout, killSignal: 'SIGKILL', maxBuffer, windowsHide: true, env });
    child = execution.child;
    await execution;
  } catch (error) {
    // execFile errors can carry the command, stdout and stderr. Retain only
    // the enumerated outcome, without chaining the original error.
    throw new Error(probeFailure(error, child, exitStages));
  }
}

function prepareAclFixture(check, handoff, fixture) {
  const systemRoot = process.env.SystemRoot;
  if (!systemRoot) throw new Error('subprocess startup failed (missing system root)');
  const powershell = join(systemRoot, 'System32/WindowsPowerShell/v1.0/powershell.exe');
  return runHandoffProbe(powershell, ['-NoProfile', '-NonInteractive', '-NoLogo', '-Command', check], {
    env: { ...process.env, SCOPEHUB_TEST_HANDOFF: handoff, SCOPEHUB_TEST_ACL_FIXTURE: fixture },
    exitStages: { 1: 'fixture preparation', 2: 'raw ACE readback', 3: 'legacy managed descriptor' },
  });
}

export async function assertWindowsPrivateHandoff(handoff, launch = runHandoffProbe) {
  try {
    await launch(witness, [], { env: { ...process.env, SCOPEHUB_TEST_HANDOFF: handoff } });
  } catch (error) {
    throw new Error(`Private handoff ACL verification failed (${error.message}).`);
  }
}

function assertRejectedHandoff(path, stage) {
  return assert.rejects(assertWindowsPrivateHandoff(path), {
    message: `Private handoff ACL verification failed (${stages[stage]}).`,
  });
}

export async function assertUnsuitableWindowsHandoffsRejected(profile) {
  const prepare = `
    $ErrorActionPreference = 'Stop'
    $path = $env:SCOPEHUB_TEST_HANDOFF
    try {
      if ($env:SCOPEHUB_TEST_ACL_FIXTURE -eq 'broad') {
        $acl = [IO.File]::GetAccessControl($path, [Security.AccessControl.AccessControlSections]::Access)
        $sid = [Security.Principal.SecurityIdentifier]::new('S-1-1-0')
        $rights = [Security.AccessControl.FileSystemRights]::Read
      } elseif ($env:SCOPEHUB_TEST_ACL_FIXTURE -eq 'explicit-only') {
        $acl = [Security.AccessControl.FileSecurity]::new()
        $acl.SetAccessRuleProtection($true, $false)
        $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
        $rights = [Security.AccessControl.FileSystemRights]::FullControl
      } else { exit 1 }
      $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
        $sid, $rights, [Security.AccessControl.AccessControlType]::Allow
      ))
      [IO.File]::SetAccessControl($path, $acl)
      exit 0
    } catch { exit 1 }
  `;
  const cases = [['broad', 58], ['explicit-only', 54]];
  for (const [fixture, stage] of cases) {
    const path = join(profile, `synthetic-acl-${fixture}.txt`);
    await writeFile(path, '', { flag: 'wx', mode: 0o600 });
    try {
      try {
        await prepareAclFixture(prepare, path, fixture);
      } catch (error) {
        throw new Error(`Could not prepare the synthetic ${fixture} ACL fixture (${error.message}).`);
      }
      await assertRejectedHandoff(path, stage);
    } finally {
      await rm(path);
    }
  }

  // Keep the inherited descriptor intact and make the effective write open fail.
  const readOnly = join(profile, 'synthetic-acl-read-only.txt');
  await writeFile(readOnly, '', { flag: 'wx', mode: 0o600 });
  try {
    await chmod(readOnly, 0o400);
    await assertRejectedHandoff(readOnly, 55);
  } finally {
    await chmod(readOnly, 0o600).finally(() => rm(readOnly));
  }
}

export async function assertIneffectiveWindowsHandoffsAccepted(profile) {
  const prepare = await readFile(new URL('./workspace_test_handoff_ineffective_acl.ps1', import.meta.url), 'utf8');
  const controls = ['zero-rights', 'inherit-only'];
  const paths = Object.fromEntries(controls.map(control => [control, join(profile, `synthetic-acl-${control}.txt`)]));
  const owned = [];
  try {
    for (const path of Object.values(paths)) {
      await writeFile(path, '', { flag: 'wx', mode: 0o600 });
      owned.push(path);
    }
    try {
      await prepareAclFixture(prepare, profile, JSON.stringify(paths));
    } catch (error) {
      throw new Error(`Could not verify the synthetic ineffective ACL fixtures (${error.message}).`);
    }
    for (const [control, path] of Object.entries(paths)) {
      try {
        await assertWindowsPrivateHandoff(path);
      } catch (error) {
        throw new Error(`The synthetic ${control} ACE should be ineffective. ${error.message}`);
      }
    }
  } finally {
    await Promise.all(owned.map(path => rm(path)));
  }
}
