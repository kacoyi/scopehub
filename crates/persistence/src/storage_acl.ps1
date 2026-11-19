$ErrorActionPreference = 'Stop'
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

function Get-StorageAttributes($path) {
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
        throw 'Unexpected storage path type.'
    }
    $acl = if ($directory) { [IO.Directory]::GetAccessControl($path) } else { [IO.File]::GetAccessControl($path) }
    $raw = [Security.AccessControl.RawSecurityDescriptor]::new($acl.GetSecurityDescriptorBinaryForm(), 0)
    if ($null -eq $raw.DiscretionaryAcl -or $null -eq $raw.Owner) {
        throw 'Storage has no verifiable owner and discretionary ACL.'
    }
    # A newly created file can take the elevated token's default owner
    # (Administrators). Its effective DACL still has to grant the current user.
    $allowedOwners = if (!$private) { $trustedAncestors } elseif ($directory) { @($user.Value) } else { $privatePrincipals }
    if ($raw.Owner.Value -notin $allowedOwners) {
        throw 'Storage ownership is outside the profile.'
    }
    $ownerInherits = $false
    $ownerAccess = $false
    foreach ($ace in $raw.DiscretionaryAcl) {
        if ($ace -isnot [Security.AccessControl.CommonAce] -or $ace.IsCallback) {
            throw 'Unsupported storage ACL entry.'
        }
        if ($ace.AceQualifier -ne [Security.AccessControl.AceQualifier]::AccessAllowed) {
            continue
        }
        $sid = $ace.SecurityIdentifier.Value
        if ($private -and $sid -notin $privatePrincipals) {
            throw 'Storage grants access outside the owner profile.'
        }
        if (!$private -and $sid -notin $trustedAncestors -and
            !($ace.AceFlags -band [Security.AccessControl.AceFlags]::InheritOnly) -and
            ($ace.AccessMask -band ($ancestorMutation -bor $genericAll))) {
            throw 'A storage ancestor can be replaced outside the owner profile.'
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
        throw 'The current user has no storage ACL entry.'
    }
    if ($private -and $directory -and !$ownerInherits) {
        throw 'The storage directory cannot establish a private ACL for new files.'
    }
}

try {
    $path = [IO.Path]::GetFullPath($env:SCOPEHUB_STORAGE_PATH)
    $root = [IO.Path]::GetPathRoot($path)
    if ($root -notmatch '^[A-Za-z]:\\$' -or $root -eq $path) {
        throw 'Storage requires an absolute local drive path.'
    }
    $volume = [IO.DriveInfo]::new($root)
    if ($volume.DriveType -ne [IO.DriveType]::Fixed -or $volume.DriveFormat -ne 'NTFS') {
        throw 'Storage requires a local NTFS volume.'
    }
    $ancestors = [Collections.Generic.List[string]]::new()
    for ($entry = [IO.Directory]::GetParent($path); $null -ne $entry; $entry = $entry.Parent) {
        $ancestors.Add($entry.FullName)
    }

    $prepare = $env:SCOPEHUB_STORAGE_ACTION -eq 'prepare'
    if ($prepare) {
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
            [void][IO.Directory]::CreateDirectory($ancestor, $security)
            $attributes = Get-StorageAttributes $ancestor
        }
        if ($null -eq $attributes) { throw 'A storage ancestor is unavailable.' }
        Confirm-StorageAcl $ancestor $attributes $true $false
    }
    $attributes = Get-StorageAttributes $path
    if ($null -eq $attributes -and $prepare) {
        [void][IO.Directory]::CreateDirectory($path, $security)
        $attributes = Get-StorageAttributes $path
    }
    if ($null -eq $attributes) { throw 'The storage directory is unavailable.' }
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
    exit 1
}
