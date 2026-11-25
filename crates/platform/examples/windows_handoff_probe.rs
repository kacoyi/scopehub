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
fn grants_to_unexpected_trustee(mask: u32, inherit_only: bool, private_trustee: bool) -> bool {
    // The legacy managed leaf ACL discarded both kinds of ineffective allow.
    mask != 0 && !inherit_only && !private_trustee
}

#[cfg(test)]
mod tests {
    use super::grants_to_unexpected_trustee;

    // FILE_READ_DATA is a single nonzero access right.
    const READ_DATA: u32 = 1;

    #[test]
    fn zero_mask_does_not_grant_access() {
        assert!(!grants_to_unexpected_trustee(0, false, false));
    }

    #[test]
    fn inherit_only_allow_does_not_grant_access_to_the_file() {
        assert!(!grants_to_unexpected_trustee(READ_DATA, true, false));
    }

    #[test]
    fn effective_allow_still_checks_the_trustee() {
        assert!(grants_to_unexpected_trustee(READ_DATA, false, false));
        assert!(!grants_to_unexpected_trustee(READ_DATA, false, true));
    }
}

#[cfg(windows)]
mod windows {
    use super::grants_to_unexpected_trustee;

    use std::{
        fs::{OpenOptions, symlink_metadata},
        mem::{offset_of, size_of},
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
            ACCESS_ALLOWED_ACE, ACE_HEADER, ACL,
            Authorization::{GetNamedSecurityInfoW, SE_FILE_OBJECT},
            DACL_SECURITY_INFORMATION, EqualSid, GetAce, GetTokenInformation, INHERIT_ONLY_ACE,
            INHERITED_ACE, IsValidAcl, IsValidSid, IsWellKnownSid, OWNER_SECURITY_INFORMATION,
            PSECURITY_DESCRIPTOR, PSID, SECURITY_MAX_SID_SIZE, TOKEN_QUERY, TOKEN_USER, TokenUser,
            WinBuiltinAdministratorsSid, WinLocalSystemSid,
        },
        Storage::FileSystem::{
            DELETE, FILE_ATTRIBUTE_DIRECTORY, FILE_ATTRIBUTE_REPARSE_POINT,
            FILE_FLAG_OPEN_REPARSE_POINT, FILE_GENERIC_READ, FILE_GENERIC_WRITE, SYNCHRONIZE,
        },
        System::{
            SystemServices::{
                ACCESS_ALLOWED_ACE_TYPE, ACCESS_DENIED_ACE_TYPE, SYSTEM_ALARM_ACE_TYPE,
                SYSTEM_AUDIT_ACE_TYPE,
            },
            Threading::{GetCurrentProcess, OpenProcessToken},
        },
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
            const SID_OFFSET: usize = offset_of!(ACCESS_ALLOWED_ACE, SidStart);
            // SAFETY: read() validated the non-null DACL and its buffer is live.
            let count = unsafe { u32::from((*self.dacl).AceCount) };
            let mut inherited_user = false;
            for index in 0..count {
                let mut entry = null_mut();
                // SAFETY: GetAce uses the validated DACL and initializes the output.
                if unsafe { GetAce(self.dacl, index, &mut entry) } == 0 || entry.is_null() {
                    return Err(Stage::InheritedAccess);
                }
                // SAFETY: GetAce returns an aligned header in the validated DACL.
                let header = unsafe { &*entry.cast::<ACE_HEADER>() };
                match u32::from(header.AceType) {
                    ACCESS_ALLOWED_ACE_TYPE => {}
                    ACCESS_DENIED_ACE_TYPE | SYSTEM_AUDIT_ACE_TYPE | SYSTEM_ALARM_ACE_TYPE => {
                        continue;
                    }
                    _ => return Err(Stage::InheritedAccess),
                }
                let size = usize::from(header.AceSize);
                if size < SID_OFFSET + 8 {
                    return Err(Stage::InheritedAccess);
                }
                // SAFETY: the allow ACE has room for its fixed fields and the SID header.
                let ace = unsafe { &*entry.cast::<ACCESS_ALLOWED_ACE>() };
                let sid = entry.cast::<u8>().wrapping_add(SID_OFFSET).cast();
                let sub_authorities =
                    unsafe { usize::from(*entry.cast::<u8>().add(SID_OFFSET + 1)) };
                if SID_OFFSET + 8 + sub_authorities * size_of::<u32>() > size
                    // SAFETY: the complete SID extent is inside the live ACE.
                    || unsafe { IsValidSid(sid) } == 0
                {
                    return Err(Stage::InheritedAccess);
                }
                let flags = u32::from(header.AceFlags);
                if grants_to_unexpected_trustee(
                    ace.Mask,
                    flags & INHERIT_ONLY_ACE != 0,
                    private_trustee(sid, user),
                ) {
                    return Err(Stage::UnexpectedTrustee);
                }
                // SAFETY: the ACE and token supply live, valid SIDs.
                inherited_user |= unsafe { EqualSid(sid, user) != 0 }
                    && flags & (INHERITED_ACE | INHERIT_ONLY_ACE) == INHERITED_ACE
                    && ace.Mask & REQUIRED == REQUIRED;
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
