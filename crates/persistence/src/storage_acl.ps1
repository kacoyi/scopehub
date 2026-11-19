$ErrorActionPreference = 'Stop'
function Stop-Storage([int]$code) {
    $script:failure = $code
    throw 'The storage ACL check was refused.'
}

function Get-StorageAttributes($path) {
    $script:failure = 24
    try {
        return [IO.File]::GetAttributes($path)
    } catch {
        $cause = $_.Exception.GetBaseException()
        if ($cause -isnot [IO.FileNotFoundException] -and $cause -isnot [IO.DirectoryNotFoundException]) {
            throw
        }
    }
}

function Confirm-StorageAcl($path, $attributes, $directory, $private) {
    if (($attributes -band [IO.FileAttributes]::ReparsePoint) -or
        ([bool]($attributes -band [IO.FileAttributes]::Directory) -ne $directory)) {
        Stop-Storage 25
    }
    $script:failure = 26
    $acl = if ($directory) { [IO.Directory]::GetAccessControl($path) } else { [IO.File]::GetAccessControl($path) }
    $raw = [Security.AccessControl.RawSecurityDescriptor]::new($acl.GetSecurityDescriptorBinaryForm(), 0)
    if ($null -eq $raw.DiscretionaryAcl -or $null -eq $raw.Owner) {
        Stop-Storage 26
    }
    # A newly created file can take the elevated token's default owner
    # (Administrators). Its effective DACL still has to grant the current user.
    $allowedOwners = if (!$private) { $trustedAncestors } elseif ($directory) { @($user.Value) } else { $privatePrincipals }
    if ($raw.Owner.Value -notin $allowedOwners) {
        if ($private) { Stop-Storage 34 } else { Stop-Storage 28 }
    }
    $ownerInherits = $false
    $ownerAccess = $false
    foreach ($ace in $raw.DiscretionaryAcl) {
        if ($ace -isnot [Security.AccessControl.CommonAce] -or $ace.IsCallback) {
            Stop-Storage 27
        }
        if ($ace.AceQualifier -ne [Security.AccessControl.AceQualifier]::AccessAllowed) {
            continue
        }
        $sid = $ace.SecurityIdentifier.Value
        if ($private -and $sid -notin $privatePrincipals) {
            Stop-Storage 30
        }
        if (!$private -and $sid -notin $trustedAncestors -and
            !($ace.AceFlags -band [Security.AccessControl.AceFlags]::InheritOnly) -and
            ($ace.AccessMask -band ($ancestorMutation -bor $genericAll))) {
            Stop-Storage 29
        }
        if ($sid -eq $user.Value) {
            if (!($ace.AceFlags -band [Security.AccessControl.AceFlags]::InheritOnly)) {
                $ownerAccess = $true
            }
            if (($ace.AccessMask -band $full) -eq $full -and
                ($ace.AceFlags -band [int]$inherits) -eq [int]$inherits) {
                $ownerInherits = $true
            }
        }
    }
    if ($private -and !$ownerAccess) {
        Stop-Storage 31
    }
    if ($private -and $directory -and !$ownerInherits) {
        Stop-Storage 31
    }
}

try {
    $script:failure = 20
    $user = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $system = 'S-1-5-18'
    $administrators = 'S-1-5-32-544'
    $trustedInstaller = 'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464'
    $privatePrincipals = @($user.Value, $system, $administrators)
    $trustedAncestors = $privatePrincipals + $trustedInstaller
    $inherits = [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
    $full = [Security.AccessControl.FileSystemRights]::FullControl
    $ancestorMutation = [int][Security.AccessControl.FileSystemRights]'Delete, DeleteSubdirectoriesAndFiles, ChangePermissions, TakeOwnership'
    $genericAll = 0x10000000

    $script:failure = 21
    $path = [IO.Path]::GetFullPath($env:SCOPEHUB_STORAGE_PATH)
    $root = [IO.Path]::GetPathRoot($path)
    if ($root -notmatch '^[A-Za-z]:\\$' -or $root -eq $path) {
        Stop-Storage 21
    }
    $script:failure = 22
    $volume = [IO.DriveInfo]::new($root)
    if ($volume.DriveType -ne [IO.DriveType]::Fixed -or $volume.DriveFormat -ne 'NTFS') {
        Stop-Storage 22
    }
    $ancestors = [Collections.Generic.List[string]]::new()
    for ($entry = [IO.Directory]::GetParent($path); $null -ne $entry; $entry = $entry.Parent) {
        $ancestors.Add($entry.FullName)
    }

    if ($env:SCOPEHUB_STORAGE_ACTION -notin @('prepare', 'open')) { Stop-Storage 23 }
    $prepare = $env:SCOPEHUB_STORAGE_ACTION -eq 'prepare'
    if ($prepare) {
        $script:failure = 32
        $security = [Security.AccessControl.DirectorySecurity]::new()
        $security.SetOwner($user)
        $security.SetAccessRuleProtection($true, $false)
        $security.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
            $user, $full, $inherits, [Security.AccessControl.PropagationFlags]::None,
            [Security.AccessControl.AccessControlType]::Allow
        ))
    }
    for ($index = $ancestors.Count - 1; $index -ge 0; $index--) {
        $ancestor = $ancestors[$index]
        $attributes = Get-StorageAttributes $ancestor
        if ($null -eq $attributes -and $prepare) {
            $script:failure = 32
            [void][IO.Directory]::CreateDirectory($ancestor, $security)
            $attributes = Get-StorageAttributes $ancestor
        }
        if ($null -eq $attributes) { Stop-Storage 33 }
        Confirm-StorageAcl $ancestor $attributes $true $false
    }
    $attributes = Get-StorageAttributes $path
    if ($null -eq $attributes -and $prepare) {
        $script:failure = 32
        [void][IO.Directory]::CreateDirectory($path, $security)
        $attributes = Get-StorageAttributes $path
    }
    if ($null -eq $attributes) { Stop-Storage 33 }
    Confirm-StorageAcl $path $attributes $true $true

    if (!$prepare) {
        foreach ($name in $env:SCOPEHUB_STORAGE_FILES.Split(';')) {
            $file = [IO.Path]::Combine($path, $name)
            $attributes = Get-StorageAttributes $file
            if ($null -ne $attributes) { Confirm-StorageAcl $file $attributes $false $true }
        }
    }
    exit 0
} catch {
    exit $script:failure
}
