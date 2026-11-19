import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { rm, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { promisify } from 'node:util';

const exec = promisify(execFile);
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

function runPowerShell(check, handoff, fixture) {
  const powershell = join(process.env.SystemRoot, 'System32/WindowsPowerShell/v1.0/powershell.exe');
  return exec(powershell, ['-NoProfile', '-NonInteractive', '-NoLogo', '-Command', check], {
    timeout: 10_000, maxBuffer: 8192, windowsHide: true,
    env: { ...process.env, SCOPEHUB_TEST_HANDOFF: handoff, SCOPEHUB_TEST_ACL_FIXTURE: fixture ?? '' },
  });
}

export async function assertWindowsPrivateHandoff(handoff) {
  // Read the resulting ACL directly, without PowerShell provider resolution.
  // Keep the current SID, the path and the file contents out of diagnostic output.
  const check = `
    $ErrorActionPreference = 'Stop'
    $failure = 51
    try {
      $path = $env:SCOPEHUB_TEST_HANDOFF
      $attributes = [int]([IO.File]::GetAttributes($path))
      $nonFile = [int][IO.FileAttributes]'Directory, ReparsePoint'
      if (($attributes -band $nonFile) -ne 0) { exit $failure }
      $failure = 59
      $user = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
      if ([string]::IsNullOrEmpty($user)) { exit $failure }
      $allowed = @($user, 'S-1-5-18', 'S-1-5-32-544')
      $failure = 56
      $sections = [Security.AccessControl.AccessControlSections]'Owner, Access'
      $directoryAcl = [IO.Directory]::GetAccessControl([IO.Path]::GetDirectoryName($path), $sections)
      $directory = [Security.AccessControl.RawSecurityDescriptor]::new($directoryAcl.GetSecurityDescriptorBinaryForm(), 0)
      if ($null -eq $directory.Owner) { exit $failure }
      $failure = 52
      if ($directory.Owner.Value -ne $user) { exit $failure }
      $failure = 57
      $fileAcl = [IO.File]::GetAccessControl($path, $sections)
      $descriptor = [Security.AccessControl.RawSecurityDescriptor]::new($fileAcl.GetSecurityDescriptorBinaryForm(), 0)
      $failure = 53
      if ($null -eq $descriptor.DiscretionaryAcl -or $null -eq $descriptor.Owner -or $descriptor.Owner.Value -notin $allowed) { exit $failure }
      $failure = 54
      $required = [int][Security.AccessControl.FileSystemRights]'Read, Write, Delete'
      $inherited = [int][Security.AccessControl.AceFlags]::Inherited
      $inheritOnly = [int][Security.AccessControl.AceFlags]::InheritOnly
      $userInherited = $false
      foreach ($ace in $descriptor.DiscretionaryAcl) {
        if ($ace -isnot [Security.AccessControl.CommonAce] -or $ace.IsCallback) { exit $failure }
        if ($ace.AceQualifier -ne [Security.AccessControl.AceQualifier]::AccessAllowed) { continue }
        if ($ace.SecurityIdentifier.Value -notin $allowed) { exit 58 }
        $flags = [int]$ace.AceFlags
        $access = [int]$ace.AccessMask
        if ($ace.SecurityIdentifier.Value -eq $user -and
            ($flags -band $inherited) -ne 0 -and ($flags -band $inheritOnly) -eq 0 -and
            ($access -band $required) -eq $required) { $userInherited = $true }
      }
      if (!$userInherited) { exit $failure }
      $failure = 55
      $stream = [IO.File]::Open($path, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite)
      $stream.Dispose()
      exit 0
    } catch { exit $failure }
  `;
  try {
    await runPowerShell(check, handoff);
  } catch (error) {
    throw new Error(`Private handoff ACL verification failed (${stages[error.code] ?? 'PowerShell execution'}).`);
  }
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
        await runPowerShell(prepare, path, fixture);
      } catch {
        throw new Error(`Could not prepare the synthetic ${fixture} ACL fixture.`);
      }
      await assert.rejects(assertWindowsPrivateHandoff(path), {
        message: `Private handoff ACL verification failed (${stages[stage]}).`,
      });
    } finally {
      await rm(path);
    }
  }
}
