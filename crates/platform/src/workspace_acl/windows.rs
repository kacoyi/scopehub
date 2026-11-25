//! Win32 handle and ACL operations for supervised workspace storage.
#![allow(unsafe_code)]

use std::{
    cmp::Ordering,
    ffi::{OsStr, c_void},
    fs::{File, OpenOptions},
    io,
    os::windows::{
        ffi::OsStrExt,
        fs::{MetadataExt, OpenOptionsExt},
        io::{AsRawHandle, FromRawHandle, OwnedHandle},
    },
    path::{Component, Path, Prefix},
    ptr::{null, null_mut},
};

use windows_sys::Win32::{
    Foundation::{ERROR_ALREADY_EXISTS, ERROR_SUCCESS, GENERIC_ALL, LocalFree},
    Security::{
        ACE_HEADER, ACE_INHERITED_OBJECT_TYPE_PRESENT, ACE_OBJECT_TYPE_PRESENT, ACL,
        ACL_REVISION_DS, ACL_SIZE_INFORMATION, AclSizeInformation,
        Authorization::{
            ConvertSidToStringSidW, ConvertStringSecurityDescriptorToSecurityDescriptorW,
            GetSecurityInfo, SDDL_REVISION_1, SE_FILE_OBJECT,
        },
        CONTAINER_INHERIT_ACE, DACL_SECURITY_INFORMATION, FAILED_ACCESS_ACE_FLAG, GetAce,
        GetAclInformation, GetTokenInformation, INHERIT_ONLY_ACE, INHERITED_ACE, IsValidAcl,
        IsValidSid, NO_PROPAGATE_INHERIT_ACE, OBJECT_INHERIT_ACE, OWNER_SECURITY_INFORMATION, PSID,
        SECURITY_ATTRIBUTES, SECURITY_MAX_SID_SIZE, SUCCESSFUL_ACCESS_ACE_FLAG, TOKEN_QUERY,
        TOKEN_USER, TokenUser,
    },
    Storage::FileSystem::{
        CreateDirectoryW, DELETE, FILE_ALL_ACCESS, FILE_ATTRIBUTE_DIRECTORY,
        FILE_ATTRIBUTE_REPARSE_POINT, FILE_DELETE_CHILD, FILE_FLAG_BACKUP_SEMANTICS,
        FILE_FLAG_OPEN_REPARSE_POINT, FILE_READ_ATTRIBUTES, FILE_SHARE_DELETE, FILE_SHARE_READ,
        FILE_SHARE_WRITE, GetDriveTypeW, GetVolumeInformationW, READ_CONTROL, WRITE_DAC,
        WRITE_OWNER,
    },
    System::{
        Memory::LocalSize,
        SystemServices::{
            ACCESS_ALLOWED_ACE_TYPE, ACCESS_ALLOWED_CALLBACK_ACE_TYPE,
            ACCESS_ALLOWED_CALLBACK_OBJECT_ACE_TYPE, ACCESS_ALLOWED_COMPOUND_ACE_TYPE,
            ACCESS_ALLOWED_OBJECT_ACE_TYPE, ACCESS_DENIED_ACE_TYPE,
            ACCESS_DENIED_CALLBACK_ACE_TYPE, ACCESS_DENIED_CALLBACK_OBJECT_ACE_TYPE,
            ACCESS_DENIED_OBJECT_ACE_TYPE, SID_MAX_SUB_AUTHORITIES, SID_REVISION,
            SYSTEM_ALARM_ACE_TYPE, SYSTEM_ALARM_CALLBACK_ACE_TYPE,
            SYSTEM_ALARM_CALLBACK_OBJECT_ACE_TYPE, SYSTEM_ALARM_OBJECT_ACE_TYPE,
            SYSTEM_AUDIT_ACE_TYPE, SYSTEM_AUDIT_CALLBACK_ACE_TYPE,
            SYSTEM_AUDIT_CALLBACK_OBJECT_ACE_TYPE, SYSTEM_AUDIT_OBJECT_ACE_TYPE,
        },
        Threading::{GetCurrentProcess, OpenProcessToken},
        WindowsProgramming::DRIVE_FIXED,
    },
};

use super::Phase;

const SYSTEM: &str = "S-1-5-18";
const ADMINISTRATORS: &str = "S-1-5-32-544";
const TRUSTED_INSTALLER: &str = "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464";
const CHILD_INHERITANCE: u32 = OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE;
const REPLACE_ENTRY: u32 = DELETE | FILE_DELETE_CHILD | WRITE_DAC | WRITE_OWNER | GENERIC_ALL;

struct Allocation(*mut c_void);

impl Drop for Allocation {
    fn drop(&mut self) {
        if !self.0.is_null() {
            // SAFETY: the pointer is a LocalAlloc result owned by this guard.
            unsafe { LocalFree(self.0) };
        }
    }
}

fn wide(text: &OsStr) -> Result<Vec<u16>, u8> {
    let mut bytes: Vec<_> = text.encode_wide().collect();
    if bytes.contains(&0) {
        return Err(21);
    }
    bytes.push(0);
    Ok(bytes)
}

fn allocation_room(base: *const c_void, length: usize, entry: *const c_void) -> usize {
    entry
        .addr()
        .checked_sub(base.addr())
        .and_then(|offset| length.checked_sub(offset))
        .unwrap_or(0)
}

fn sid_fits(revision: u8, count: u8, available: usize) -> bool {
    u32::from(revision) == SID_REVISION
        && u32::from(count) <= SID_MAX_SUB_AUTHORITIES
        && 8 + usize::from(count) * size_of::<u32>() <= available
}

/// The caller must supply the remaining extent of the live token, descriptor or ACE.
unsafe fn sid_text(sid: PSID, available: usize) -> Result<String, u8> {
    if available < 8 || sid.is_null() || !sid.cast::<u32>().is_aligned() {
        return Err(26);
    }
    // SAFETY: the first two bytes of the SID header are inside the supplied extent.
    let (revision, count) = unsafe { (*sid.cast::<u8>(), *sid.cast::<u8>().add(1)) };
    // SAFETY: the fixed header and the full declared sub-authority array fit in the extent.
    if !sid_fits(revision, count, available) || unsafe { IsValidSid(sid) } == 0 {
        return Err(26);
    }
    let mut text = null_mut();
    // SAFETY: callers supply a validated SID backed by a live token or descriptor.
    if unsafe { ConvertSidToStringSidW(sid, &mut text) } == 0 {
        return Err(26);
    }
    let _allocation = Allocation(text.cast());
    // A SID is a fixed prefix, a 48-bit authority and at most 15 u32 parts.
    // SAFETY: the Windows conversion returned a NUL-terminated SID string.
    let length = (0..192)
        .find(|offset| unsafe { *text.add(*offset) } == 0)
        .ok_or(26)?;
    // SAFETY: the scan determined the length of the initialized string.
    String::from_utf16(unsafe { std::slice::from_raw_parts(text, length) }).map_err(|_| 26)
}

pub(super) fn current_user() -> Result<String, u8> {
    let mut token = null_mut();
    // SAFETY: the out-parameter receives an owned handle to this process token.
    if unsafe { OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut token) } == 0 {
        return Err(20);
    }
    // SAFETY: OpenProcessToken returned the handle and no other owner exists.
    let token = unsafe { OwnedHandle::from_raw_handle(token) };
    #[repr(C)]
    struct TokenUserBuffer {
        user: TOKEN_USER,
        sid: [u8; SECURITY_MAX_SID_SIZE as usize],
    }
    let mut data = TokenUserBuffer {
        user: TOKEN_USER::default(),
        sid: [0; SECURITY_MAX_SID_SIZE as usize],
    };
    let mut size = 0;
    // SAFETY: data is a live, suitably aligned token user buffer with space for the SID.
    if unsafe {
        GetTokenInformation(
            token.as_raw_handle(),
            TokenUser,
            (&raw mut data).cast(),
            size_of::<TokenUserBuffer>() as u32,
            &mut size,
        )
    } == 0
    {
        return Err(20);
    }
    let sid = data.user.User.Sid;
    let available = allocation_room(
        (&raw const data).cast(),
        (size as usize).min(size_of::<TokenUserBuffer>()),
        sid,
    );
    // SAFETY: the token buffer is still live after GetTokenInformation.
    unsafe { sid_text(sid, available) }.map_err(|_| 20)
}

fn check_volume(root: &Path) -> Result<(), u8> {
    let mut root = wide(root.as_os_str())?;
    if let Some(separator) = root.get_mut(2) {
        *separator = u16::from(b'\\');
    }
    let mut filesystem = [0u16; 32];
    // SAFETY: root and the filesystem output buffer remain live throughout both calls.
    if unsafe { GetDriveTypeW(root.as_ptr()) } != DRIVE_FIXED
        || unsafe {
            GetVolumeInformationW(
                root.as_ptr(),
                null_mut(),
                0,
                null_mut(),
                null_mut(),
                null_mut(),
                filesystem.as_mut_ptr(),
                filesystem.len() as u32,
            )
        } == 0
        || filesystem[..5] != [b'N' as u16, b'T' as u16, b'F' as u16, b'S' as u16, 0]
    {
        return Err(22);
    }
    Ok(())
}

fn private_descriptor(user: &str) -> Result<Allocation, u8> {
    // Protected DACL, with owner full control on this directory and its children.
    security_descriptor(&format!("O:{user}D:P(A;OICI;FA;;;{user})"))
}

fn security_descriptor(sddl: &str) -> Result<Allocation, u8> {
    let sddl = wide(OsStr::new(sddl))?;
    let mut descriptor = null_mut();
    // SAFETY: the Win32 API reads the NUL-terminated SDDL and allocates the descriptor.
    if unsafe {
        ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl.as_ptr(),
            SDDL_REVISION_1,
            &mut descriptor,
            null_mut(),
        )
    } == 0
    {
        return Err(32);
    }
    Ok(Allocation(descriptor))
}

fn create_directory(path: &Path, descriptor: &Allocation) -> Result<(), u8> {
    let path = wide(path.as_os_str())?;
    let attributes = SECURITY_ATTRIBUTES {
        nLength: size_of::<SECURITY_ATTRIBUTES>() as u32,
        lpSecurityDescriptor: descriptor.0,
        bInheritHandle: 0,
    };
    // SAFETY: path and descriptor remain live for the duration of the call.
    if unsafe { CreateDirectoryW(path.as_ptr(), &attributes) } == 0
        && io::Error::last_os_error().raw_os_error() != Some(ERROR_ALREADY_EXISTS as i32)
    {
        return Err(32);
    }
    Ok(())
}

fn open_entry(path: &Path, kind: Entry) -> Result<Option<File>, u8> {
    let mut sharing = FILE_SHARE_READ | FILE_SHARE_WRITE;
    if kind == Entry::StorageFile {
        // Reading an ACL must not prevent
        // SQLite's other connection from retiring its transient sidecars.
        sharing |= FILE_SHARE_DELETE;
    }
    match OpenOptions::new()
        .access_mode(READ_CONTROL | FILE_READ_ATTRIBUTES)
        .share_mode(sharing)
        .custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT)
        .open(path)
    {
        Ok(entry) => Ok(Some(entry)),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(_) => Err(24),
    }
}

#[derive(Clone, Copy, PartialEq)]
enum Entry {
    Ancestor,
    Profile,
    StorageFile,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum AceKind {
    Allow,
    Deny,
    Audit,
    UnsupportedAccess,
}

enum AceLayout {
    Common,
    Object,
    Compound,
}

struct DecodedAce<'a> {
    kind: AceKind,
    flags: u32,
    mask: u32,
    sid: &'a [u8],
}

fn decode_ace(bytes: &[u8], acl_revision: u32) -> Result<DecodedAce<'_>, u8> {
    let [
        ace_type,
        flags,
        size_low,
        size_high,
        mask_0,
        mask_1,
        mask_2,
        mask_3,
        body @ ..,
    ] = bytes
    else {
        return Err(27);
    };
    let size = usize::from(u16::from_le_bytes([*size_low, *size_high]));
    if size != bytes.len() || !size.is_multiple_of(size_of::<u32>()) {
        return Err(27);
    }
    let (kind, layout) = match u32::from(*ace_type) {
        ACCESS_ALLOWED_ACE_TYPE => (AceKind::Allow, AceLayout::Common),
        ACCESS_DENIED_ACE_TYPE => (AceKind::Deny, AceLayout::Common),
        ACCESS_ALLOWED_CALLBACK_ACE_TYPE | ACCESS_DENIED_CALLBACK_ACE_TYPE => {
            (AceKind::UnsupportedAccess, AceLayout::Common)
        }
        SYSTEM_AUDIT_ACE_TYPE
        | SYSTEM_ALARM_ACE_TYPE
        | SYSTEM_AUDIT_CALLBACK_ACE_TYPE
        | SYSTEM_ALARM_CALLBACK_ACE_TYPE => (AceKind::Audit, AceLayout::Common),
        ACCESS_ALLOWED_OBJECT_ACE_TYPE
        | ACCESS_DENIED_OBJECT_ACE_TYPE
        | ACCESS_ALLOWED_CALLBACK_OBJECT_ACE_TYPE
        | ACCESS_DENIED_CALLBACK_OBJECT_ACE_TYPE => (AceKind::UnsupportedAccess, AceLayout::Object),
        SYSTEM_AUDIT_OBJECT_ACE_TYPE
        | SYSTEM_ALARM_OBJECT_ACE_TYPE
        | SYSTEM_AUDIT_CALLBACK_OBJECT_ACE_TYPE
        | SYSTEM_ALARM_CALLBACK_OBJECT_ACE_TYPE => (AceKind::Audit, AceLayout::Object),
        ACCESS_ALLOWED_COMPOUND_ACE_TYPE => (AceKind::UnsupportedAccess, AceLayout::Compound),
        _ => return Err(27),
    };
    let sid_and_data = match layout {
        AceLayout::Common => body,
        AceLayout::Object => object_ace_sid_and_data(body, acl_revision)?,
        AceLayout::Compound => {
            // The legacy impersonation layout has a 16-bit type followed
            // by a reserved zero word.
            let [1, 0, 0, 0, sid @ ..] = body else {
                return Err(27);
            };
            sid
        }
    };
    let [revision, count, ..] = sid_and_data else {
        return Err(27);
    };
    if !sid_fits(*revision, *count, sid_and_data.len()) {
        return Err(27);
    }
    let sid_size = 8 + usize::from(*count) * size_of::<u32>();
    let opaque_size = sid_and_data.len() - sid_size;
    // The managed qualified ACE constructors reserve space for the largest
    // possible SID and GUID fields even when this entry has a shorter layout.
    const COMMON_MAX_OPAQUE: usize = u16::MAX as usize
        - size_of::<ACE_HEADER>()
        - size_of::<u32>()
        - SECURITY_MAX_SID_SIZE as usize;
    const OBJECT_MAX_OPAQUE: usize =
        COMMON_MAX_OPAQUE - size_of::<u32>() - 2 * size_of::<windows_sys::core::GUID>();
    let max_opaque = match layout {
        AceLayout::Common => COMMON_MAX_OPAQUE,
        AceLayout::Object => OBJECT_MAX_OPAQUE,
        AceLayout::Compound => 0,
    };
    if opaque_size > max_opaque || !opaque_size.is_multiple_of(size_of::<u32>()) {
        return Err(27);
    }
    // Common and object ACEs can carry opaque data after the complete SID.
    // No condition or object selector supplies workspace authority.
    Ok(DecodedAce {
        kind,
        flags: u32::from(*flags),
        mask: u32::from_le_bytes([*mask_0, *mask_1, *mask_2, *mask_3]),
        sid: &sid_and_data[..sid_size],
    })
}

fn object_ace_sid_and_data(body: &[u8], acl_revision: u32) -> Result<&[u8], u8> {
    let [low, middle_low, middle_high, high, guids_and_sid @ ..] = body else {
        return Err(27);
    };
    let flags = u32::from_le_bytes([*low, *middle_low, *middle_high, *high]);
    let guid_flags = ACE_OBJECT_TYPE_PRESENT | ACE_INHERITED_OBJECT_TYPE_PRESENT;
    if acl_revision != ACL_REVISION_DS || flags & !guid_flags != 0 {
        return Err(27);
    }
    let guid_bytes = flags.count_ones() as usize * size_of::<windows_sys::core::GUID>();
    guids_and_sid.get(guid_bytes..).ok_or(27)
}

fn normalize_access_flags(kind: Entry, mut flags: u32, mask: u32) -> Option<u32> {
    if mask == 0 {
        return None;
    }
    // The managed descriptor drops inherit-only entries on leaves, and on
    // directories where there is no object or container inheritance.
    let file = kind == Entry::StorageFile;
    if flags & INHERIT_ONLY_ACE != 0 && (file || flags & CHILD_INHERITANCE == 0) {
        return None;
    }
    if file {
        flags &= !(CHILD_INHERITANCE | NO_PROPAGATE_INHERIT_ACE);
    } else if flags & CHILD_INHERITANCE == 0 {
        flags &= !NO_PROPAGATE_INHERIT_ACE;
    }
    Some(flags & !(SUCCESSFUL_ACCESS_ACE_FLAG | FAILED_ACCESS_ACE_FLAG))
}

fn normalized_access(kind: Entry, ace: &DecodedAce<'_>) -> Result<Option<(u32, u32)>, u8> {
    let Some(flags) = normalize_access_flags(kind, ace.flags, ace.mask) else {
        return Ok(None);
    };
    let rank = match ace.kind {
        AceKind::Deny => 0,
        AceKind::Allow => 1,
        AceKind::Audit => return Ok(None),
        // The managed DACL drops inert entries before the workspace policy
        // refuses unsupported access entries that remain.
        AceKind::UnsupportedAccess => return Err(27),
    };
    let rank = if flags & INHERITED_ACE != 0 { 2 } else { rank };
    Ok(Some((flags, rank)))
}

fn private_principal(sid: &str, user: &str) -> bool {
    sid == user || matches!(sid, SYSTEM | ADMINISTRATORS)
}

fn trusted_ancestor(sid: &str, user: &str) -> bool {
    private_principal(sid, user) || sid == TRUSTED_INSTALLER
}

const SYSTEM_ORDER: u8 = 1;
const ADMINISTRATORS_ORDER: u8 = 3;

fn user_order(user: &str) -> Option<u8> {
    if user == SYSTEM {
        return Some(SYSTEM_ORDER);
    }
    if user == ADMINISTRATORS {
        return Some(ADMINISTRATORS_ORDER);
    }
    let mut parts = user.strip_prefix("S-1-")?.split('-');
    let authority = parts.next()?;
    let authority = match authority
        .strip_prefix("0x")
        .or_else(|| authority.strip_prefix("0X"))
    {
        Some(hex) => u64::from_str_radix(hex, 16).ok()?,
        None => authority.parse::<u64>().ok()?,
    };
    let parts: Vec<u32> = parts.map(str::parse).collect::<Result<_, _>>().ok()?;
    let compare = |other: &[u32]| {
        authority
            .cmp(&5)
            .then_with(|| parts.len().cmp(&other.len()))
            .then_with(|| {
                parts
                    .iter()
                    .zip(other)
                    .map(|(&left, &right)| (left.wrapping_sub(right) as i32).cmp(&0))
                    .find(|&order| order != Ordering::Equal)
                    .unwrap_or(Ordering::Equal)
            })
    };
    Some(if compare(&[18]).is_lt() {
        0
    } else if compare(&[32, 544]).is_lt() {
        2
    } else {
        4
    })
}

#[derive(Clone, Copy)]
struct Grant {
    principal: u8,
    flags: u32,
    mask: u32,
}

struct UserInheritance {
    user: u8,
    full: bool,
    explicit: Vec<Grant>,
}

impl UserInheritance {
    fn new(user: u8) -> Self {
        Self {
            user,
            full: false,
            explicit: Vec::new(),
        }
    }

    fn record(&mut self, principal: u8, flags: u32, mask: u32) {
        self.full |= principal == self.user && inherits_full(flags, mask);
        if flags & INHERITED_ACE == 0 {
            self.explicit.push(Grant {
                principal,
                flags,
                mask,
            });
        }
    }

    fn sufficient(mut self, canonical: bool) -> bool {
        if self.full {
            return true;
        }
        if !canonical {
            return false;
        }
        sort_managed(&mut self.explicit);
        let mut grants = self
            .explicit
            .into_iter()
            .filter(|grant| grant.principal == self.user)
            .peekable();
        while let Some(Grant {
            mut flags,
            mut mask,
            ..
        }) = grants.next()
        {
            if let Some(next) = grants.peek()
                && let Some(merged) = compact_grants((flags, mask), (next.flags, next.mask))
            {
                (flags, mask) = merged;
                // The managed export removes the next ACE and advances. It
                // never tries to merge the result with a third ACE in this pass.
                grants.next();
            }
            if inherits_full(flags, mask) {
                return true;
            }
        }
        false
    }
}

fn inherits_full(flags: u32, mask: u32) -> bool {
    mask & FILE_ALL_ACCESS == FILE_ALL_ACCESS && flags & CHILD_INHERITANCE == CHILD_INHERITANCE
}

fn sort_managed(grants: &mut [Grant]) {
    // Reproduce the managed first-pivot partition within the explicit allow
    // group. Interleaved SIDs can change the order among equal-SID ACEs.
    let mut pending = vec![grants];
    while let Some(grants) = pending.pop() {
        if grants.is_sorted_by_key(|grant| grant.principal) {
            continue;
        }
        let pivot = grants[0];
        let mut left = 0;
        let mut right = grants.len() - 1;
        while left < right {
            while left < right && grants[right].principal >= pivot.principal {
                right -= 1;
            }
            if left != right {
                grants[left] = grants[right];
                left += 1;
            }
            while left < right && grants[left].principal <= pivot.principal {
                left += 1;
            }
            if left != right {
                grants[right] = grants[left];
                right -= 1;
            }
        }
        grants[left] = pivot;
        let (before, rest) = grants.split_at_mut(left);
        pending.extend([before, &mut rest[1..]]);
    }
}

fn compact_grants(
    (left_flags, left_mask): (u32, u32),
    (right_flags, right_mask): (u32, u32),
) -> Option<(u32, u32)> {
    if left_flags == right_flags {
        return Some((left_flags, left_mask | right_mask));
    }
    if left_mask != right_mask {
        return None;
    }
    // Equal masks can share one ACE only when a single Windows scope can
    // represent the union exactly. A single no-propagate flag applies to both
    // the child-object and child-container permissions.
    let (left_here, left_children, left_descendants) = inheritance_scope(left_flags)?;
    let (right_here, right_children, right_descendants) = inheritance_scope(right_flags)?;
    let children = left_children | right_children;
    let descendants = left_descendants | right_descendants;
    if descendants != 0 && descendants != children {
        return None;
    }
    let mut flags = children;
    if !left_here && !right_here {
        flags |= INHERIT_ONLY_ACE;
    }
    if children != 0 && descendants == 0 {
        flags |= NO_PROPAGATE_INHERIT_ACE;
    }
    Some((flags, left_mask))
}

fn inheritance_scope(flags: u32) -> Option<(bool, u32, u32)> {
    let children = flags & CHILD_INHERITANCE;
    let other = INHERIT_ONLY_ACE | NO_PROPAGATE_INHERIT_ACE;
    if flags & !(CHILD_INHERITANCE | other) != 0 || (children == 0 && flags & other != 0) {
        return None;
    }
    let descendants = if flags & NO_PROPAGATE_INHERIT_ACE == 0 {
        children
    } else {
        0
    };
    Some((flags & INHERIT_ONLY_ACE == 0, children, descendants))
}

fn check_entry(file: &File, kind: Entry, user: &str) -> Result<(), u8> {
    let attributes = file.metadata().map_err(|_| 24)?.file_attributes();
    if attributes & FILE_ATTRIBUTE_REPARSE_POINT != 0
        || (attributes & FILE_ATTRIBUTE_DIRECTORY != 0) != (kind != Entry::StorageFile)
    {
        return Err(25);
    }

    let mut owner = null_mut();
    let mut dacl = null_mut();
    let mut descriptor = Allocation(null_mut());
    // SAFETY: the open handle and output parameters are valid. The returned owner
    // and DACL pointers borrow from the descriptor owned by the allocation guard.
    if unsafe {
        GetSecurityInfo(
            file.as_raw_handle(),
            SE_FILE_OBJECT,
            OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
            &mut owner,
            null_mut(),
            &mut dacl,
            null_mut(),
            &mut descriptor.0,
        )
    } != ERROR_SUCCESS
        || owner.is_null()
        || dacl.is_null()
    {
        return Err(26);
    }
    // SAFETY: GetSecurityInfo returned a LocalAlloc descriptor. Owner and DACL
    // point within it and must stay within the reported allocation.
    let descriptor_size = unsafe { LocalSize(descriptor.0) };
    let acl_room = allocation_room(descriptor.0, descriptor_size, dacl.cast());
    if acl_room < size_of::<ACL>() || !dacl.is_aligned() {
        return Err(26);
    }
    // SAFETY: the aligned ACL header fits in the descriptor's allocation.
    let (acl_size, acl_revision) =
        unsafe { (usize::from((*dacl).AclSize), u32::from((*dacl).AclRevision)) };
    // SAFETY: the complete DACL extent is within the live descriptor.
    if !(size_of::<ACL>()..=acl_room).contains(&acl_size) || unsafe { IsValidAcl(dacl) } == 0 {
        return Err(26);
    }
    let owner_room = allocation_room(descriptor.0, descriptor_size, owner);
    // SAFETY: the descriptor remains live and the owner SID is checked against its extent.
    let owner = unsafe { sid_text(owner, owner_room) }?;
    if kind == Entry::Ancestor {
        if !trusted_ancestor(&owner, user) {
            return Err(28);
        }
    } else if (kind == Entry::Profile && owner != user) || !private_principal(&owner, user) {
        return Err(34);
    }

    let mut info = ACL_SIZE_INFORMATION::default();
    // SAFETY: dacl borrows from the live descriptor and info has the requested layout.
    if unsafe {
        GetAclInformation(
            dacl,
            (&raw mut info).cast(),
            size_of::<ACL_SIZE_INFORMATION>() as u32,
            AclSizeInformation,
        )
    } == 0
        || info.AclBytesInUse as usize > acl_size
    {
        return Err(26);
    }
    let mut user_applies = false;
    let mut inheritance = (kind == Entry::Profile)
        .then(|| user_order(user).map(UserInheritance::new).ok_or(26))
        .transpose()?;
    let mut canonical = true;
    let mut previous_rank = 0;
    for index in 0..info.AceCount {
        let mut entry = null_mut();
        // SAFETY: dacl remains live and GetAce validates the index.
        if unsafe { GetAce(dacl, index, &mut entry) } == 0 || entry.is_null() {
            return Err(27);
        }
        let ace_room = allocation_room(dacl.cast(), info.AclBytesInUse as usize, entry);
        if ace_room < size_of::<ACE_HEADER>() || !entry.cast::<u32>().is_aligned() {
            return Err(27);
        }
        // SAFETY: GetAce returned an aligned header within the ACL's used extent.
        let header = unsafe { &*entry.cast::<ACE_HEADER>() };
        let ace_size = usize::from(header.AceSize);
        if !(size_of::<ACE_HEADER>()..=ace_room).contains(&ace_size) {
            return Err(27);
        }
        // SAFETY: the ACE lies entirely within the live ACL allocation.
        let bytes = unsafe { std::slice::from_raw_parts(entry.cast::<u8>(), ace_size) };
        let ace = decode_ace(bytes, acl_revision)?;
        // SAFETY: the parser limits the SID to its exact extent in the live ACE.
        let sid = unsafe { sid_text(ace.sid.as_ptr().cast_mut().cast(), ace.sid.len()) }
            .map_err(|_| 27)?;
        let Some((flags, rank)) = normalized_access(kind, &ace)? else {
            continue;
        };
        canonical &= rank >= previous_rank;
        previous_rank = rank;
        if ace.kind == AceKind::Deny {
            continue;
        }
        let mask = ace.mask;
        let applies_here = flags & INHERIT_ONLY_ACE == 0;
        if kind == Entry::Ancestor {
            if !trusted_ancestor(&sid, user) && applies_here && mask & REPLACE_ENTRY != 0 {
                return Err(29);
            }
        } else if !private_principal(&sid, user) {
            return Err(30);
        }
        user_applies |= sid == user && applies_here;
        if let Some(inheritance) = &mut inheritance {
            let principal = if sid == user {
                inheritance.user
            } else if sid == SYSTEM {
                SYSTEM_ORDER
            } else {
                ADMINISTRATORS_ORDER
            };
            inheritance.record(principal, flags, mask);
        }
    }
    if kind != Entry::Ancestor
        && (!user_applies || inheritance.is_some_and(|rules| !rules.sufficient(canonical)))
    {
        return Err(31);
    }
    Ok(())
}

pub(super) fn tick_ms() -> u64 {
    use windows_sys::Win32::System::SystemInformation::GetTickCount64;

    // SAFETY: this function reads the system's monotonic tick counter.
    unsafe { GetTickCount64() }
}

fn ordinary_directory_name(name: &[u16]) -> bool {
    if name
        .last()
        .is_none_or(|last| [u16::from(b' '), u16::from(b'.')].contains(last))
        || name.iter().any(|&unit| {
            u8::try_from(unit).is_ok_and(|byte| byte < b' ' || b"\"*:<>?|".contains(&byte))
        })
    {
        return false;
    }
    // DOS device names are reserved even with an extension.
    let base = name
        .split(|&unit| unit == u16::from(b'.'))
        .next()
        .unwrap_or_default();
    let base = String::from_utf16_lossy(base);
    let base = base.trim_end_matches(' ').to_ascii_uppercase();
    !matches!(
        base.as_str(),
        "CON" | "PRN" | "AUX" | "NUL" | "CONIN$" | "CONOUT$"
    ) && !base
        .strip_prefix("COM")
        .or_else(|| base.strip_prefix("LPT"))
        .is_some_and(|port| {
            matches!(
                port,
                "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9" | "¹" | "²" | "³"
            )
        })
}

fn profile_chain(path: &Path) -> Result<Vec<&Path>, u8> {
    let mut components = path.components();
    let Some(Component::Prefix(prefix)) = components.next() else {
        return Err(21);
    };
    if !matches!(prefix.kind(), Prefix::Disk(_)) || components.next() != Some(Component::RootDir) {
        return Err(21);
    }
    // Inspect the supplied spelling before performing any filesystem operation.
    // Path::components would discard interior dot segments.
    let units: Vec<_> = path.as_os_str().encode_wide().collect();
    let remainder = units.get(3..).ok_or(21)?;
    let mut names = remainder
        .split(|&unit| unit == u16::from(b'\\') || unit == u16::from(b'/'))
        .filter(|name| !name.is_empty());
    if !names.next().is_some_and(ordinary_directory_name) || !names.all(ordinary_directory_name) {
        return Err(21);
    }
    // Each entry comes from the same absolute path. No component is parsed again
    // as an independent path, and the drive prefix alone is not an ancestor.
    let mut chain: Vec<_> = path
        .ancestors()
        .take_while(|entry| entry.has_root())
        .collect();
    chain.reverse();
    Ok(chain)
}

pub(super) fn validate(
    path: &Path,
    prepare: bool,
    children: u8,
    files: &[&str],
    deadline_ms: u64,
    mut phase: impl FnMut(Phase) -> Result<(), u8>,
) -> Result<(), u8> {
    let chain = profile_chain(path)?;
    let (root, descendants) = chain.split_first().ok_or(21)?;
    let (profile, ancestors) = descendants.split_last().ok_or(21)?;
    phase(Phase::Identity)?;
    let user = current_user()?;
    phase(Phase::Volume)?;
    check_volume(root)?;

    phase(Phase::Ancestors)?;
    // Retain checked directory handles without delete sharing during validation.
    let root = open_entry(root, Entry::Ancestor)?.ok_or(33)?;
    check_entry(&root, Entry::Ancestor, &user)?;
    let mut directories = vec![root];
    let descriptor = prepare.then(|| private_descriptor(&user)).transpose()?;
    for ancestor in ancestors {
        directories.push(check_directory(
            ancestor,
            Entry::Ancestor,
            &user,
            descriptor.as_ref(),
            deadline_ms,
        )?);
    }
    phase(Phase::Profile)?;
    directories.push(check_directory(
        profile,
        Entry::Profile,
        &user,
        descriptor.as_ref(),
        deadline_ms,
    )?);
    if children != 0 {
        phase(Phase::Children)?;
        for index in 0..children {
            check_directory(
                &profile.join(format!("profile-{index}")),
                Entry::Profile,
                &user,
                descriptor.as_ref(),
                deadline_ms,
            )?;
        }
    }
    if !prepare {
        phase(Phase::Files)?;
        for name in files {
            if let Some(file) = open_entry(&profile.join(name), Entry::StorageFile)? {
                check_entry(&file, Entry::StorageFile, &user)?;
            }
        }
    }
    Ok(())
}

fn check_directory(
    path: &Path,
    kind: Entry,
    user: &str,
    descriptor: Option<&Allocation>,
    deadline_ms: u64,
) -> Result<File, u8> {
    let file = match open_entry(path, kind)? {
        Some(file) => file,
        None => {
            if tick_ms() >= deadline_ms {
                return Err(36);
            }
            create_directory(path, descriptor.ok_or(33)?)?;
            open_entry(path, kind)?.ok_or(33)?
        }
    };
    check_entry(&file, kind, user)?;
    Ok(file)
}

pub(super) struct Job(OwnedHandle);

impl Job {
    pub(super) fn new() -> io::Result<Self> {
        use windows_sys::Win32::System::JobObjects::{
            CreateJobObjectW, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
            JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JobObjectExtendedLimitInformation,
            SetInformationJobObject,
        };

        // SAFETY: no name or inheritable security descriptor is supplied.
        let job = unsafe { CreateJobObjectW(null(), null()) };
        if job.is_null() {
            return Err(io::Error::last_os_error());
        }
        // SAFETY: the job handle was just created and has no other owner.
        let job = unsafe { OwnedHandle::from_raw_handle(job) };
        let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        // SAFETY: limits has the requested layout and stays live through the call.
        if unsafe {
            SetInformationJobObject(
                job.as_raw_handle(),
                JobObjectExtendedLimitInformation,
                (&raw const limits).cast(),
                size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            )
        } == 0
        {
            return Err(io::Error::last_os_error());
        }
        Ok(Self(job))
    }

    pub(super) fn contain(&self, child: &std::process::Child) -> io::Result<()> {
        use windows_sys::Win32::System::JobObjects::AssignProcessToJobObject;

        // SAFETY: both handles are live, and this process has yet to grant ACL work.
        if unsafe { AssignProcessToJobObject(self.0.as_raw_handle(), child.as_raw_handle()) } == 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(())
    }
}

pub(super) fn wait_for_exit(child: &impl AsRawHandle, milliseconds: u32) -> bool {
    use windows_sys::Win32::{Foundation::WAIT_OBJECT_0, System::Threading::WaitForSingleObject};

    // SAFETY: the child owns the handle until this wait returns.
    unsafe { WaitForSingleObject(child.as_raw_handle(), milliseconds) == WAIT_OBJECT_0 }
}

#[cfg(test)]
mod tests;
