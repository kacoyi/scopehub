# Prepare the raw leaf ACL matrix and compare each file to the former managed witness.
$ErrorActionPreference = 'Stop'
$timer = [Diagnostics.Stopwatch]::StartNew()
[Console]::Out.WriteLine('SCOPEHUB_ACL_TIMING script 0')
$caseIndex = -1
try {
  [void][Reflection.Assembly]::LoadFrom($env:SCOPEHUB_TEST_ACL_HELPER)
  [Console]::Out.WriteLine(('SCOPEHUB_ACL_TIMING helper {0}' -f $timer.ElapsedMilliseconds))
  $matrix = ConvertFrom-Json $env:SCOPEHUB_TEST_ACL_FIXTURE
  if ($matrix.controls.Count -ne 24) { exit 1 }
  $world = [Security.Principal.SecurityIdentifier]::new('S-1-1-0')
  $user = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
  $allowed = @($user, 'S-1-5-18', 'S-1-5-32-544')
  $allow = [Security.AccessControl.AceQualifier]::AccessAllowed
  $deny = [Security.AccessControl.AceQualifier]::AccessDenied
  $inherited = [int][Security.AccessControl.AceFlags]::Inherited
  $inheritOnly = [int][Security.AccessControl.AceFlags]::InheritOnly
  $required = [int][Security.AccessControl.FileSystemRights]'Read, Write, Delete'
  $sections = [Security.AccessControl.AccessControlSections]'Owner, Access'
  $descriptorFlags = [Security.AccessControl.ControlFlags]'DiscretionaryAclPresent, DiscretionaryAclProtected'
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

  function Get-RawAceDifference($stored, $entry, $hadFixtureTrustee) {
    # A same-trustee candidate is informative only when the original fixture
    # had no such entry and the readback supplies exactly one.
    $candidates = @($stored | Where-Object {
      $_ -is [Security.AccessControl.KnownAce] -and $_.SecurityIdentifier.Equals($world)
    })
    if ($candidates.Count -eq 0) { return 'raw-absent' }
    if ($hadFixtureTrustee -or $candidates.Count -ne 1) { return 'raw-ambiguous' }
    $observed = $candidates[0]
    if ($observed.AceType -ne $entry.AceType) { return 'raw-type' }
    if ($observed.AceFlags -ne $entry.AceFlags) { return 'raw-flags' }
    if ($observed.AccessMask -ne $entry.AccessMask) { return 'raw-mask' }
    if ($entry -is [Security.AccessControl.ObjectAce]) {
      $flags = [int]$entry.ObjectAceFlags
      if ($observed -isnot [Security.AccessControl.ObjectAce] -or $observed.ObjectAceFlags -ne $entry.ObjectAceFlags -or
          (($flags -band 1) -and $observed.ObjectAceType -ne $entry.ObjectAceType) -or
          (($flags -band 2) -and $observed.InheritedObjectAceType -ne $entry.InheritedObjectAceType)) { return 'raw-object' }
    }
    return 'raw-bytes'
  }

  foreach ($control in $matrix.controls) {
    $caseIndex += 1
    $reason = 'fixture-start'
    $flags = [Security.AccessControl.AceFlags]::None
    if ($control.mode -eq 'inherit-only') {
      $flags = [Security.AccessControl.AceFlags]'ObjectInherit, InheritOnly'
    }
    $mask = if ($control.mode -eq 'zero-mask') { 0 } else { 1 } # FILE_READ_DATA
    $qualifier = if ($control.deny) { $deny } else { $allow }
    $callback = [bool]$control.callback
    $opaque = $null
    if ($callback) { $opaque = $condition }
    $startingBytes = [HandoffFixtureAcl]::Read($control.path)
    $reason = 'fixture-baseline'
    $namedBytes = [HandoffFixtureAcl]::ReadNamed($control.path)
    if ($null -eq $startingBytes -or
        ![Collections.StructuralComparisons]::StructuralEqualityComparer.Equals($startingBytes, $namedBytes)) {
      throw 'Starting native DACL views differ.'
    }
    $reason = 'fixture-start'
    $raw = [Security.AccessControl.RawAcl]::new($startingBytes, 0)
    $hadFixtureTrustee = @($raw | Where-Object {
      $_ -is [Security.AccessControl.KnownAce] -and $_.SecurityIdentifier.Equals($world)
    }).Count -ne 0
    $inheritedIndices = @(for ($i = 0; $i -lt $raw.Count; $i++) {
      $ace = $raw[$i]
      if ($ace -is [Security.AccessControl.CommonAce] -and !$ace.IsCallback -and
          $ace.AceQualifier -eq $allow -and $ace.SecurityIdentifier.Value -eq $user -and
          ([int]$ace.AceFlags -band ($inherited -bor $inheritOnly)) -eq $inherited -and
          ($ace.AccessMask -band $required) -eq $required) { $i }
    })
    $reason = 'fixture-inheritance'
    if ($inheritedIndices.Count -eq 0) { throw 'Starting inherited user grant missing.' }
    $reason = 'fixture-start'
    if ($null -eq $control.objectFlags) {
      $entry = [Security.AccessControl.CommonAce]::new($flags, $qualifier, $mask, $world, $callback, $opaque)
      $dacl = $raw
      $dacl.InsertAce(0, $entry)
    } else {
      $entry = [Security.AccessControl.ObjectAce]::new(
        $flags, $qualifier, $mask, $world, [Security.AccessControl.ObjectAceFlags]$control.objectFlags,
        $objectType, $inheritedType, $callback, $opaque
      )
      $dacl = [Security.AccessControl.RawAcl]::new([byte]4, $raw.Count + 1)
      $dacl.InsertAce(0, $entry)
      foreach ($original in $raw) { $dacl.InsertAce($dacl.Count, $original) }
    }
    # Use the protected regular-file write also exercised by the native
    # storage matrix. Carry forward the actual inherited user ACE.
    $descriptor = [Security.AccessControl.RawSecurityDescriptor]::new($descriptorFlags, $null, $null, $null, $dacl)
    $bytes = [byte[]]::new($descriptor.BinaryLength)
    $descriptor.GetBinaryForm($bytes, 0)
    $reason = 'submitted-ace'
    $entryBytes = [byte[]]::new($entry.BinaryLength)
    $entry.GetBinaryForm($entryBytes, 0)
    if (![HandoffFixtureAcl]::HasAce($bytes, $true, 0, $entryBytes)) { throw 'Submitted fixture ACE differs.' }
    $reason = 'submitted-inheritance'
    $inheritedGrantBytes = @(foreach ($index in $inheritedIndices) {
      $grant = $dacl[$index + 1]
      $grantBytes = [byte[]]::new($grant.BinaryLength)
      $grant.GetBinaryForm($grantBytes, 0)
      if (![HandoffFixtureAcl]::HasAce($startingBytes, $false, $index, $grantBytes) -or
          ![HandoffFixtureAcl]::HasAce($bytes, $true, ($index + 1), $grantBytes)) {
        throw 'Submitted inherited user grant differs.'
      }
      ,$grantBytes
    })
    $reason = 'fixture-write'
    [HandoffFixtureAcl]::Write($control.path, $bytes)

    $reason = 'raw-read'
    $storedBytes = [HandoffFixtureAcl]::Read($control.path)
    $reason = 'raw-dacl'
    if ($null -eq $storedBytes) { throw 'Fixture DACL missing.' }
    $reason = 'raw-parse'
    $stored = [Security.AccessControl.RawAcl]::new($storedBytes, 0)
    $reason = 'raw-revision'
    if ($stored.Revision -ne $dacl.Revision) { throw 'Fixture DACL revision differs.' }
    $reason = 'raw-compare'
    $same = @(for ($i = 0; $i -lt $stored.Count; $i++) {
      if ([HandoffFixtureAcl]::HasAce($storedBytes, $false, $i, $entryBytes)) { $i }
    })
    if ($same.Count -gt 1) {
      $reason = 'raw-duplicate'
      throw 'Fixture ACE duplicated.'
    }
    if ($same.Count -eq 0) {
      $reason = Get-RawAceDifference $stored $entry $hadFixtureTrustee
      throw 'Fixture ACE differs.'
    }

    $reason = 'raw-inheritance'
    foreach ($grantBytes in $inheritedGrantBytes) {
      $matches = @(for ($i = 0; $i -lt $stored.Count; $i++) {
        if ([HandoffFixtureAcl]::HasAce($storedBytes, $false, $i, $grantBytes)) { $i }
      })
      if ($matches.Count -eq 0) {
        throw 'Stored inherited user grant differs.'
      }
    }

    $reason = 'legacy-read'
    $observedStage = Get-LegacyHandoffStage $control.path
    if ($observedStage -ne $control.expectedStage) {
      $reason = "legacy-stage-$observedStage"
      throw 'Legacy fixture verdict differs.'
    }
  }
  [Console]::Out.WriteLine(('SCOPEHUB_ACL_TIMING matrix {0}' -f $timer.ElapsedMilliseconds))
  exit 0
} catch {
  if ($caseIndex -lt 0) { exit 1 }
  [Console]::Error.WriteLine(('SCOPEHUB_ACL_FIXTURE {0} {1}' -f $caseIndex, $reason))
  exit 4
}
