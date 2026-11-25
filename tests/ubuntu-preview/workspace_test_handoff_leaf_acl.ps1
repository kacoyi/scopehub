# Prepare the raw leaf ACL matrix and compare each file to the former managed witness.
$ErrorActionPreference = 'Stop'
$failure = 1
try {
  Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;

public static class HandoffFixtureAcl
{
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
    private static extern bool GetFileSecurityW(
        string path, uint information, [Out] byte[] descriptor, uint length, out uint needed);

    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, ExactSpelling = true)]
    private static extern uint SetNamedSecurityInfoW(
        string path, int type, uint information, IntPtr owner, IntPtr group, [In] byte[] dacl, IntPtr sacl);

    private const int FileObject = 1;
    private const uint Dacl = 4;

    public static byte[] Read(string path)
    {
        uint needed;
        if (GetFileSecurityW(path, Dacl, null, 0, out needed) ||
            Marshal.GetLastWin32Error() != 122 || needed == 0)
            throw new InvalidOperationException();
        byte[] bytes = new byte[needed];
        if (!GetFileSecurityW(path, Dacl, bytes, needed, out needed))
            throw new Win32Exception();
        return bytes;
    }

    public static void Write(string path, byte[] dacl)
    {
        uint error = SetNamedSecurityInfoW(path, FileObject, Dacl, IntPtr.Zero, IntPtr.Zero, dacl, IntPtr.Zero);
        if (error != 0)
            throw new Win32Exception((int)error);
    }
}
'@
  $matrix = ConvertFrom-Json $env:SCOPEHUB_TEST_ACL_FIXTURE
  if ($matrix.controls.Count -eq 0) { exit $failure }
  $world = [Security.Principal.SecurityIdentifier]::new('S-1-1-0')
  $user = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
  $allowed = @($user, 'S-1-5-18', 'S-1-5-32-544')
  $allow = [Security.AccessControl.AceQualifier]::AccessAllowed
  $deny = [Security.AccessControl.AceQualifier]::AccessDenied
  $inherited = [int][Security.AccessControl.AceFlags]::Inherited
  $inheritOnly = [int][Security.AccessControl.AceFlags]::InheritOnly
  $required = [int][Security.AccessControl.FileSystemRights]'Read, Write, Delete'
  $sections = [Security.AccessControl.AccessControlSections]'Owner, Access'
  $objectType = [Guid]'ee0570e9-82d5-481f-9529-ecb79d9b9945'
  $inheritedType = [Guid]'204ec986-64f0-4bf8-b9c5-6a4befb47f12'
  # The same synthetic conditional expression used by the native storage matrix.
  [byte[]]$condition = 0x61, 0x72, 0x74, 0x78, 0xf8, 0x0a, 0, 0, 0, 0x54, 0, 0x69, 0, 0x74, 0, 0x6c,
    0, 0x65, 0, 0x10, 4, 0, 0, 0, 0x56, 0, 0x50, 0, 0x80, 0, 0, 0

  function Get-LegacyHandoffStage($path) {
    $acl = [IO.File]::GetAccessControl($path, $sections)
    $managed = [Security.AccessControl.RawSecurityDescriptor]::new($acl.GetSecurityDescriptorBinaryForm(), 0)
    if ($null -eq $managed.DiscretionaryAcl -or $null -eq $managed.Owner -or
        $managed.Owner.Value -notin $allowed) { return 53 }
    $userInherited = $false
    foreach ($ace in $managed.DiscretionaryAcl) {
      if ($ace -isnot [Security.AccessControl.CommonAce] -or $ace.IsCallback) { return 54 }
      if ($ace.AceQualifier -ne $allow) { continue }
      if ($ace.SecurityIdentifier.Value -notin $allowed) { return 58 }
      if ($ace.SecurityIdentifier.Value -eq $user -and
          ([int]$ace.AceFlags -band ($inherited -bor $inheritOnly)) -eq $inherited -and
          ($ace.AccessMask -band $required) -eq $required) { $userInherited = $true }
    }
    if (!$userInherited) { return 54 }
    try {
      $stream = [IO.File]::Open($path, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite)
      $stream.Dispose()
    } catch { return 55 }
    return 0
  }

  foreach ($control in $matrix.controls) {
    $failure = 1
    $flags = [Security.AccessControl.AceFlags]::None
    if ($control.mode -eq 'inherit-only') {
      $flags = [Security.AccessControl.AceFlags]'ObjectInherit, InheritOnly'
    }
    $mask = if ($control.mode -eq 'zero-mask') { 0 } else { 1 } # FILE_READ_DATA
    $qualifier = if ($control.deny) { $deny } else { $allow }
    $callback = [bool]$control.callback
    $opaque = $null
    if ($callback) { $opaque = $condition }
    $raw = [Security.AccessControl.RawSecurityDescriptor]::new([HandoffFixtureAcl]::Read($control.path), 0)
    if ($null -eq $control.objectFlags) {
      $entry = [Security.AccessControl.CommonAce]::new($flags, $qualifier, $mask, $world, $callback, $opaque)
      $dacl = $raw.DiscretionaryAcl
      $dacl.InsertAce(0, $entry)
    } else {
      $entry = [Security.AccessControl.ObjectAce]::new(
        $flags, $qualifier, $mask, $world, [Security.AccessControl.ObjectAceFlags]$control.objectFlags,
        $objectType, $inheritedType, $callback, $opaque
      )
      $dacl = [Security.AccessControl.RawAcl]::new([byte]4, $raw.DiscretionaryAcl.Count + 1)
      $dacl.InsertAce(0, $entry)
      foreach ($original in $raw.DiscretionaryAcl) { $dacl.InsertAce($dacl.Count, $original) }
    }
    $bytes = [byte[]]::new($dacl.BinaryLength)
    $dacl.GetBinaryForm($bytes, 0)
    [HandoffFixtureAcl]::Write($control.path, $bytes)

    $failure = 2
    $stored = [Security.AccessControl.RawSecurityDescriptor]::new([HandoffFixtureAcl]::Read($control.path), 0)
    $same = @($stored.DiscretionaryAcl | Where-Object { $_.Equals($entry) })
    if ($same.Count -ne 1 -or $stored.DiscretionaryAcl.Revision -ne $dacl.Revision) { exit $failure }

    $failure = 3
    if ((Get-LegacyHandoffStage $control.path) -ne $control.expectedStage) { exit $failure }
  }
  exit 0
} catch { exit $failure }
