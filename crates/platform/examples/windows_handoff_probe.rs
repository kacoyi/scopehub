//! Independent, test-only witness for the generated Windows launcher handoff.

#[cfg(windows)]
fn main() -> std::process::ExitCode {
    let result = std::env::var_os("SCOPEHUB_TEST_HANDOFF")
        .ok_or(windows::Stage::FileType)
        .and_then(|path| windows::check(path.as_ref()));
    result.map_or_else(
        |stage| std::process::ExitCode::from(stage as u8),
        |_| std::process::ExitCode::SUCCESS,
    )
}

#[cfg(not(windows))]
fn main() -> std::process::ExitCode {
    std::process::ExitCode::FAILURE
}

#[cfg(any(windows, test))]
#[path = "windows_handoff_acl/leaf_ace.rs"]
mod leaf_ace;

#[cfg(windows)]
mod windows {
    use super::leaf_ace::read_leaf_allow;

    use std::{
        fs::{OpenOptions, symlink_metadata},
        mem::size_of,
        os::windows::{
            ffi::OsStrExt,
            fs::{MetadataExt, OpenOptionsExt},
            io::{AsRawHandle, FromRawHandle, OwnedHandle},
        },
        path::Path,
        ptr::null_mut,
    };

    use windows_sys::Win32::{
        Foundation::{ERROR_SUCCESS, LocalFree},
        Security::{
            ACE_HEADER, ACL,
            Authorization::{GetNamedSecurityInfoW, SE_FILE_OBJECT},
            DACL_SECURITY_INFORMATION, EqualSid, GetAce, GetTokenInformation, INHERITED_ACE,
            IsValidAcl, IsValidSid, IsWellKnownSid, OWNER_SECURITY_INFORMATION,
            PSECURITY_DESCRIPTOR, PSID, SECURITY_MAX_SID_SIZE, TOKEN_QUERY, TOKEN_USER, TokenUser,
            WinBuiltinAdministratorsSid, WinLocalSystemSid,
        },
        Storage::FileSystem::{
            DELETE, FILE_ATTRIBUTE_DIRECTORY, FILE_ATTRIBUTE_REPARSE_POINT,
            FILE_FLAG_OPEN_REPARSE_POINT, FILE_GENERIC_READ, FILE_GENERIC_WRITE, SYNCHRONIZE,
        },
        System::Threading::{GetCurrentProcess, OpenProcessToken},
    };

    // Only these stages leave the process. Never emit paths, SIDs or OS errors.
    #[derive(Clone, Copy)]
    #[repr(u8)]
    pub(super) enum Stage {
        FileType = 51,
        DirectoryOwner = 52,
        FileOwnerAndDacl = 53,
        InheritedAccess = 54,
        EffectiveReadWrite = 55,
        DirectoryDescriptor = 56,
        FileDescriptor = 57,
        UnexpectedTrustee = 58,
        CurrentUser = 59,
    }

    struct Descriptor {
        buffer: PSECURITY_DESCRIPTOR,
        owner: PSID,
        dacl: *mut ACL,
    }

    impl Drop for Descriptor {
        fn drop(&mut self) {
            if !self.buffer.is_null() {
                // SAFETY: this buffer is the LocalAlloc result owned by the witness.
                unsafe { LocalFree(self.buffer) };
            }
        }
    }

    impl Descriptor {
        fn read(path: &Path, failure: Stage) -> Result<Self, Stage> {
            let mut name: Vec<_> = path.as_os_str().encode_wide().collect();
            if name.is_empty() || name.contains(&0) {
                return Err(failure);
            }
            name.push(0);
            let mut descriptor = Self {
                buffer: null_mut(),
                owner: null_mut(),
                dacl: null_mut(),
            };
            // SAFETY: name is NUL-terminated, and all requested outputs are live.
            // Owner and DACL borrow from the returned buffer owned by descriptor.
            let status = unsafe {
                GetNamedSecurityInfoW(
                    name.as_ptr(),
                    SE_FILE_OBJECT,
                    OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
                    &mut descriptor.owner,
                    null_mut(),
                    &mut descriptor.dacl,
                    null_mut(),
                    &mut descriptor.buffer,
                )
            };
            if status != ERROR_SUCCESS || descriptor.buffer.is_null() {
                return Err(failure);
            }
            // SAFETY: a non-null owner or DACL points into the live Win32 descriptor.
            if unsafe {
                (!descriptor.owner.is_null() && IsValidSid(descriptor.owner) == 0)
                    || (!descriptor.dacl.is_null() && IsValidAcl(descriptor.dacl) == 0)
            } {
                return Err(failure);
            }
            Ok(descriptor)
        }

        fn check_inherited_access(&self, user: PSID) -> Result<(), Stage> {
            if self.dacl.is_null() || self.owner.is_null() || !private_trustee(self.owner, user) {
                return Err(Stage::FileOwnerAndDacl);
            }
            // The old observer required .NET FileSystemRights Read | Write | Delete.
            const REQUIRED: u32 = (FILE_GENERIC_READ | FILE_GENERIC_WRITE | DELETE) & !SYNCHRONIZE;
            // SAFETY: read() validated the non-null DACL and its buffer is live.
            let acl = unsafe { &*self.dacl };
            let acl_size = usize::from(acl.AclSize);
            let mut inherited_user = false;
            for index in 0..u32::from(acl.AceCount) {
                let mut entry = null_mut();
                // SAFETY: GetAce uses the validated DACL and initializes the output.
                if unsafe { GetAce(self.dacl, index, &mut entry) } == 0 || entry.is_null() {
                    return Err(Stage::InheritedAccess);
                }
                let offset = entry
                    .addr()
                    .checked_sub(self.dacl.addr())
                    .ok_or(Stage::InheritedAccess)?;
                let room = acl_size.checked_sub(offset).ok_or(Stage::InheritedAccess)?;
                if offset < size_of::<ACL>()
                    || room < size_of::<ACE_HEADER>()
                    || !entry.addr().is_multiple_of(4)
                {
                    return Err(Stage::InheritedAccess);
                }
                // SAFETY: the header is aligned and stays within the validated DACL.
                let size = usize::from(unsafe { (*entry.cast::<ACE_HEADER>()).AceSize });
                if !(size_of::<ACE_HEADER>()..=room).contains(&size) {
                    return Err(Stage::InheritedAccess);
                }
                // SAFETY: the complete entry fits within the owned live DACL.
                let bytes = unsafe { std::slice::from_raw_parts(entry.cast::<u8>(), size) };
                let Some(grant) =
                    read_leaf_allow(bytes, acl.AclRevision).map_err(|_| Stage::InheritedAccess)?
                else {
                    continue;
                };
                let sid = grant.sid.as_ptr().cast_mut().cast();
                // SAFETY: the validated SID is aligned and borrows from the live entry.
                if unsafe { IsValidSid(sid) } == 0 {
                    return Err(Stage::InheritedAccess);
                }
                if !private_trustee(sid, user) {
                    return Err(Stage::UnexpectedTrustee);
                }
                // SAFETY: the entry and token supply live, valid SIDs.
                inherited_user |= unsafe { EqualSid(sid, user) != 0 }
                    && u32::from(grant.flags) & INHERITED_ACE != 0
                    && grant.mask & REQUIRED == REQUIRED;
            }
            if !inherited_user {
                return Err(Stage::InheritedAccess);
            }
            Ok(())
        }
    }

    fn private_trustee(sid: PSID, user: PSID) -> bool {
        // SAFETY: the callers supply valid SIDs in the live descriptor and token.
        unsafe {
            EqualSid(sid, user) != 0
                || IsWellKnownSid(sid, WinLocalSystemSid) != 0
                || IsWellKnownSid(sid, WinBuiltinAdministratorsSid) != 0
        }
    }

    pub(super) fn check(path: &Path) -> Result<(), Stage> {
        let attrs = symlink_metadata(path)
            .map_err(|_| Stage::FileType)?
            .file_attributes();
        if attrs & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT) != 0 {
            return Err(Stage::FileType);
        }
        let mut token = null_mut();
        // SAFETY: the only requested token right is query, and the output is live.
        if unsafe { OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut token) } == 0 {
            return Err(Stage::CurrentUser);
        }
        // SAFETY: successful OpenProcessToken returned a uniquely owned handle.
        let token = unsafe { OwnedHandle::from_raw_handle(token) };
        #[repr(C)]
        struct TokenUserBuffer {
            token_user: TOKEN_USER,
            sid: [u8; SECURITY_MAX_SID_SIZE as usize],
        }
        let mut token_user = TokenUserBuffer {
            token_user: TOKEN_USER::default(),
            sid: [0; SECURITY_MAX_SID_SIZE as usize],
        };
        let mut size = 0;
        // SAFETY: the token and an aligned buffer with room for the SID are live.
        if unsafe {
            GetTokenInformation(
                token.as_raw_handle(),
                TokenUser,
                (&raw mut token_user).cast(),
                size_of::<TokenUserBuffer>() as u32,
                &mut size,
            )
        } == 0
        {
            return Err(Stage::CurrentUser);
        }
        let user = token_user.token_user.User.Sid;
        // SAFETY: TokenUser returned a SID backed by the live output buffer.
        if user.is_null() || unsafe { IsValidSid(user) } == 0 {
            return Err(Stage::CurrentUser);
        }
        let parent = path.parent().ok_or(Stage::DirectoryDescriptor)?;
        let directory = Descriptor::read(parent, Stage::DirectoryDescriptor)?;
        if directory.owner.is_null() {
            return Err(Stage::DirectoryDescriptor);
        }
        // SAFETY: the directory owner and process user are live, valid SIDs.
        if unsafe { EqualSid(directory.owner, user) } == 0 {
            return Err(Stage::DirectoryOwner);
        }
        Descriptor::read(path, Stage::FileDescriptor)?.check_inherited_access(user)?;
        // Ask Windows for effective read/write access, then close the handle.
        // No bytes, ACLs or attributes are changed.
        OpenOptions::new()
            .read(true)
            .write(true)
            .share_mode(0)
            .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT)
            .open(path)
            .map_err(|_| Stage::EffectiveReadWrite)?;
        Ok(())
    }
}
