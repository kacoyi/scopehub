//! Win32 handle and ACL operations for supervised workspace storage.
#![allow(unsafe_code)]

use std::{
    ffi::{OsStr, c_void},
    fs::{File, OpenOptions},
    io,
    os::windows::{
        ffi::OsStrExt,
        fs::{MetadataExt, OpenOptionsExt},
        io::{AsRawHandle, FromRawHandle, OwnedHandle},
    },
    path::{Component, Path, PathBuf, Prefix},
    ptr::{null, null_mut},
};

use windows_sys::Win32::{
    Foundation::{ERROR_ALREADY_EXISTS, ERROR_SUCCESS, GENERIC_ALL, LocalFree},
    Security::{
        ACCESS_ALLOWED_ACE, ACE_HEADER, ACL_SIZE_INFORMATION, AclSizeInformation,
        Authorization::{
            ConvertSidToStringSidW, ConvertStringSecurityDescriptorToSecurityDescriptorW,
            GetSecurityInfo, SDDL_REVISION_1, SE_FILE_OBJECT,
        },
        CONTAINER_INHERIT_ACE, DACL_SECURITY_INFORMATION, GetAce, GetAclInformation, GetLengthSid,
        GetTokenInformation, INHERIT_ONLY_ACE, IsValidAcl, IsValidSid, OBJECT_INHERIT_ACE,
        OWNER_SECURITY_INFORMATION, PSID, SECURITY_ATTRIBUTES, SECURITY_MAX_SID_SIZE, TOKEN_QUERY,
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
        SystemServices::{
            ACCESS_ALLOWED_ACE_TYPE, ACCESS_DENIED_ACE_TYPE, SYSTEM_ALARM_ACE_TYPE,
            SYSTEM_AUDIT_ACE_TYPE,
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

fn sid_text(sid: PSID) -> Result<String, u8> {
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

fn current_user() -> Result<String, u8> {
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
    sid_text(data.user.User.Sid).map_err(|_| 20)
}

fn check_volume(root: &Path) -> Result<(), u8> {
    let root = wide(root.as_os_str())?;
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
    let sddl = wide(OsStr::new(&format!("O:{user}D:P(A;OICI;FA;;;{user})")))?;
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
        // The directory is already pinned. Reading an ACL must not prevent
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

fn private_principal(sid: &str, user: &str) -> bool {
    sid == user || matches!(sid, SYSTEM | ADMINISTRATORS)
}

fn trusted_ancestor(sid: &str, user: &str) -> bool {
    private_principal(sid, user) || sid == TRUSTED_INSTALLER
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
        // SAFETY: GetSecurityInfo returned a DACL in the owned descriptor.
        || unsafe { IsValidAcl(dacl) } == 0
    {
        return Err(26);
    }
    let owner = sid_text(owner)?;
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
    {
        return Err(26);
    }
    let mut user_applies = false;
    let mut user_inherits = false;
    for index in 0..info.AceCount {
        let mut entry = null_mut();
        // SAFETY: dacl remains live and GetAce validates the index.
        if unsafe { GetAce(dacl, index, &mut entry) } == 0 || entry.is_null() {
            return Err(27);
        }
        // SAFETY: a successful GetAce provides an ACE containing at least its header.
        let header = unsafe { &*entry.cast::<ACE_HEADER>() };
        match u32::from(header.AceType) {
            ACCESS_ALLOWED_ACE_TYPE => {}
            ACCESS_DENIED_ACE_TYPE | SYSTEM_AUDIT_ACE_TYPE | SYSTEM_ALARM_ACE_TYPE => continue,
            _ => return Err(27),
        }
        const SID_OFFSET: usize = std::mem::offset_of!(ACCESS_ALLOWED_ACE, SidStart);
        const SID_HEADER: usize = 8;
        if usize::from(header.AceSize) < SID_OFFSET + SID_HEADER {
            return Err(27);
        }
        // SAFETY: the header identified ACCESS_ALLOWED_ACE and bounded its mask and SID header.
        let (mask, sid) = unsafe {
            let ace = &*entry.cast::<ACCESS_ALLOWED_ACE>();
            (
                ace.Mask,
                (&raw const ace.SidStart).cast_mut().cast::<c_void>(),
            )
        };
        // SAFETY: GetLengthSid only reads the SID header. Check the full size before validation.
        if unsafe { GetLengthSid(sid) } as usize + SID_OFFSET > usize::from(header.AceSize)
            || unsafe { IsValidSid(sid) } == 0
        {
            return Err(27);
        }
        let sid = sid_text(sid).map_err(|_| 27)?;
        let flags = u32::from(header.AceFlags);
        let applies_here = flags & INHERIT_ONLY_ACE == 0;
        if kind == Entry::Ancestor {
            if !trusted_ancestor(&sid, user) && applies_here && mask & REPLACE_ENTRY != 0 {
                return Err(29);
            }
        } else if !private_principal(&sid, user) {
            return Err(30);
        }
        if sid == user {
            user_applies |= applies_here;
            user_inherits |= mask & FILE_ALL_ACCESS == FILE_ALL_ACCESS
                && flags & CHILD_INHERITANCE == CHILD_INHERITANCE;
        }
    }
    if kind != Entry::Ancestor && (!user_applies || (kind == Entry::Profile && !user_inherits)) {
        return Err(31);
    }
    Ok(())
}

pub(super) fn tick_ms() -> u64 {
    use windows_sys::Win32::System::SystemInformation::GetTickCount64;

    // SAFETY: this function reads the system's monotonic tick counter.
    unsafe { GetTickCount64() }
}

pub(super) fn validate(
    path: &Path,
    prepare: bool,
    children: u8,
    files: &[&str],
    deadline_ms: u64,
    mut phase: impl FnMut(Phase) -> Result<(), u8>,
) -> Result<(), u8> {
    phase(Phase::Identity)?;
    let user = current_user()?;
    phase(Phase::Volume)?;
    let path = std::path::absolute(path).map_err(|_| 21)?;
    let mut components = path.components();
    let Some(Component::Prefix(prefix)) = components.next() else {
        return Err(21);
    };
    if !matches!(prefix.kind(), Prefix::Disk(_)) || components.next() != Some(Component::RootDir) {
        return Err(21);
    }
    let mut cursor = PathBuf::from(prefix.as_os_str());
    cursor.push(Path::new(r"\"));
    let mut components = components.peekable();
    if components.peek().is_none() {
        return Err(21);
    }
    check_volume(&cursor)?;

    phase(Phase::Ancestors)?;
    // Keep the verified chain pinned while descendants are checked and created.
    let root = open_entry(&cursor, Entry::Ancestor)?.ok_or(33)?;
    check_entry(&root, Entry::Ancestor, &user)?;
    let mut pinned = vec![root];
    let descriptor = prepare.then(|| private_descriptor(&user)).transpose()?;
    while let Some(component) = components.next() {
        let Component::Normal(name) = component else {
            return Err(21);
        };
        cursor.push(name);
        let kind = if components.peek().is_some() {
            Entry::Ancestor
        } else {
            phase(Phase::Profile)?;
            Entry::Profile
        };
        pinned.push(check_directory(
            &cursor,
            kind,
            &user,
            descriptor.as_ref(),
            deadline_ms,
        )?);
    }
    if children != 0 {
        phase(Phase::Children)?;
        for index in 0..children {
            check_directory(
                &cursor.join(format!("profile-{index}")),
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
            if let Some(file) = open_entry(&cursor.join(name), Entry::StorageFile)? {
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
