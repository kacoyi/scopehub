# Prepare and independently verify the two raw leaf ACL fixtures.
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
        string path, int type, uint information, IntPtr owner, IntPtr group, byte[] dacl, IntPtr sacl);

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
        uint error = SetNamedSecurityInfoW(path, 1, Dacl, IntPtr.Zero, IntPtr.Zero, dacl, IntPtr.Zero);
        if (error != 0)
            throw new Win32Exception((int)error);
    }
}
'@
  $paths = @(ConvertFrom-Json $env:SCOPEHUB_TEST_ACL_FIXTURE)
  if ($paths.Count -ne 2) { exit $failure }
  $world = [Security.Principal.SecurityIdentifier]::new('S-1-1-0')
  $user = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
  $allowed = @($user, 'S-1-5-18', 'S-1-5-32-544')
  $allow = [Security.AccessControl.AceQualifier]::AccessAllowed
  $inherited = [int][Security.AccessControl.AceFlags]::Inherited
  $inheritOnly = [int][Security.AccessControl.AceFlags]::InheritOnly
  $required = [int][Security.AccessControl.FileSystemRights]'Read, Write, Delete'
  $sections = [Security.AccessControl.AccessControlSections]'Owner, Access'
  $controls = @(
    @{ Path = $paths[0]; Flags = [Security.AccessControl.AceFlags]::None; Mask = 0 },
    @{ Path = $paths[1]; Flags = [Security.AccessControl.AceFlags]'ObjectInherit, InheritOnly';
       Mask = [int][Security.AccessControl.FileSystemRights]::Read }
  )
  foreach ($control in $controls) {
    $failure = 1
    $raw = [Security.AccessControl.RawSecurityDescriptor]::new([HandoffFixtureAcl]::Read($control.Path), 0)
    $raw.DiscretionaryAcl.InsertAce(0, [Security.AccessControl.CommonAce]::new(
      $control.Flags, $allow, $control.Mask, $world, $false, $null
    ))
    $bytes = [byte[]]::new($raw.DiscretionaryAcl.BinaryLength)
    $raw.DiscretionaryAcl.GetBinaryForm($bytes, 0)
    [HandoffFixtureAcl]::Write($control.Path, $bytes)

    $failure = 2
    $stored = [Security.AccessControl.RawSecurityDescriptor]::new([HandoffFixtureAcl]::Read($control.Path), 0)
    $same = @($stored.DiscretionaryAcl | Where-Object {
      $_ -is [Security.AccessControl.CommonAce] -and $_.AceQualifier -eq $allow -and
      $_.SecurityIdentifier.Value -eq $world.Value -and $_.AceFlags -eq $control.Flags -and
      $_.AccessMask -eq $control.Mask
    })
    if ($same.Count -ne 1) { exit $failure }

    $failure = 3
    # Use the previous witness's managed read, including its effective open.
    $managedAcl = [IO.File]::GetAccessControl($control.Path, $sections)
    $managed = [Security.AccessControl.RawSecurityDescriptor]::new($managedAcl.GetSecurityDescriptorBinaryForm(), 0)
    if ($null -eq $managed.DiscretionaryAcl -or $null -eq $managed.Owner -or
        $managed.Owner.Value -notin $allowed) { exit $failure }
    $userInherited = $false
    foreach ($ace in $managed.DiscretionaryAcl) {
      if ($ace -isnot [Security.AccessControl.CommonAce] -or $ace.IsCallback) { exit $failure }
      if ($ace.AceQualifier -ne $allow) { continue }
      if ($ace.SecurityIdentifier.Value -notin $allowed) { exit $failure }
      if ($ace.SecurityIdentifier.Value -eq $user -and
          ([int]$ace.AceFlags -band ($inherited -bor $inheritOnly)) -eq $inherited -and
          ($ace.AccessMask -band $required) -eq $required) { $userInherited = $true }
    }
    if (!$userInherited) { exit $failure }
    $stream = [IO.File]::Open($control.Path, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite)
    $stream.Dispose()
  }
  exit 0
} catch { exit $failure }
