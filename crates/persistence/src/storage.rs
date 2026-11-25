//! Verify filesystem ownership and access before SQLite touches a profile.

use std::{io, num::NonZeroU8, path::Path};

const FILES: [&str; 4] = [
    super::DATABASE_NAME,
    "workspace.sqlite3-wal",
    "workspace.sqlite3-shm",
    "workspace.sqlite3-journal",
];

fn denied() -> io::Error {
    io::ErrorKind::PermissionDenied.into()
}

pub(super) fn prepare_profile_root(path: &Path) -> io::Result<()> {
    require_profile_path(path)?;
    platform::prepare(path)
}

fn require_profile_path(path: &Path) -> io::Result<()> {
    if !path.is_absolute() || path.file_name().is_none() {
        return Err(denied());
    }
    Ok(())
}

pub(super) fn prepare_profile_pool(path: &Path, count: NonZeroU8) -> io::Result<()> {
    require_profile_path(path)?;
    #[cfg(windows)]
    {
        platform::prepare_pool(path, count)
    }
    #[cfg(not(windows))]
    {
        platform::prepare(path)?;
        for index in 0..count.get() {
            platform::prepare(&path.join(format!("profile-{index}")))?;
        }
        Ok(())
    }
}

pub(super) use platform::open_path;

#[cfg(unix)]
mod platform {
    use std::{
        fs::{self, DirBuilder, OpenOptions},
        os::unix::fs::{DirBuilderExt, MetadataExt, OpenOptionsExt},
        path::{Component, PathBuf},
        process::Command,
    };

    use super::*;

    fn effective_uid() -> io::Result<u32> {
        // Use the system executable directly. Only its numeric effective UID is needed.
        let output = Command::new("/usr/bin/id").arg("-u").env_clear().output()?;
        if !output.status.success() {
            return Err(denied());
        }
        str::from_utf8(&output.stdout)
            .ok()
            .and_then(|uid| uid.trim().parse().ok())
            .ok_or_else(denied)
    }

    fn create_directory(path: &Path) -> io::Result<()> {
        match DirBuilder::new().mode(0o700).create(path) {
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => Ok(()),
            result => result,
        }
    }

    fn trusted_parent(path: &Path, uid: u32, create: bool) -> io::Result<PathBuf> {
        let mut directory = PathBuf::new();
        // Check one entry at a time from the root, without following aliases.
        // A sticky parent protects its owned child entries (for example /tmp).
        for component in path.components() {
            if !matches!(component, Component::RootDir | Component::Normal(_)) {
                return Err(denied());
            }
            directory.push(component);
            let metadata = match fs::symlink_metadata(&directory) {
                Ok(metadata) => metadata,
                Err(error) if error.kind() == io::ErrorKind::NotFound && create => {
                    create_directory(&directory)?;
                    directory = check_private_directory(directory, uid)?;
                    continue;
                }
                Err(error) => return Err(error),
            };
            if !metadata.is_dir()
                || (metadata.uid() != 0 && metadata.uid() != uid)
                || (metadata.mode() & 0o022 != 0 && metadata.mode() & 0o1000 == 0)
            {
                return Err(denied());
            }
        }
        Ok(directory)
    }

    fn check_private_directory(path: PathBuf, uid: u32) -> io::Result<PathBuf> {
        let metadata = fs::symlink_metadata(&path)?;
        if !metadata.is_dir() || metadata.uid() != uid || metadata.mode() & 0o077 != 0 {
            return Err(denied());
        }
        Ok(path)
    }

    fn profile(path: &Path, uid: u32, create: bool) -> io::Result<PathBuf> {
        let parent = trusted_parent(path.parent().ok_or_else(denied)?, uid, create)?;
        let directory = parent.join(path.file_name().ok_or_else(denied)?);
        if create {
            create_directory(&directory)?;
        }
        check_private_directory(directory, uid)
    }

    fn check_file(path: &Path, uid: u32) -> io::Result<bool> {
        match fs::symlink_metadata(path) {
            Ok(metadata) => {
                if !metadata.is_file()
                    || metadata.uid() != uid
                    || metadata.mode() & 0o077 != 0
                    || metadata.nlink() != 1
                {
                    return Err(denied());
                }
                Ok(true)
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(false),
            Err(error) => Err(error),
        }
    }

    pub(super) fn prepare(path: &Path) -> io::Result<()> {
        profile(path, effective_uid()?, true).map(|_| ())
    }

    pub(in super::super) fn open_path(root: &Path) -> io::Result<(PathBuf, bool)> {
        let uid = effective_uid()?;
        let directory = profile(root, uid, false)?;
        let path = directory.join(FILES[0]);
        let exists = check_file(&path, uid)?;
        // Refuse unsafe sidecars before SQLite can recover, truncate or unlink them.
        for name in &FILES[1..] {
            check_file(&directory.join(name), uid)?;
        }
        if exists {
            return Ok((path, true));
        }
        // SQLite derives its WAL, SHM and journal file mode from the database.
        match OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&path)
        {
            Ok(_) => Ok((path, false)),
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {
                if !check_file(&path, uid)? {
                    return Err(error);
                }
                Ok((path, true))
            }
            Err(error) => Err(error),
        }
    }
}

#[cfg(test)]
pub(super) fn encoded_powershell_script(script: &str) -> String {
    const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let utf16: Vec<u8> = script.encode_utf16().flat_map(u16::to_le_bytes).collect();
    let mut encoded = String::with_capacity(utf16.len().div_ceil(3) * 4);
    for group in utf16.chunks(3) {
        let first = group[0];
        let second = group.get(1).copied().unwrap_or(0);
        let third = group.get(2).copied().unwrap_or(0);
        encoded.push(ALPHABET[usize::from(first >> 2)] as char);
        encoded.push(ALPHABET[usize::from((first & 3) << 4 | second >> 4)] as char);
        encoded.push(if group.len() > 1 {
            ALPHABET[usize::from((second & 15) << 2 | third >> 6)] as char
        } else {
            '='
        });
        encoded.push(if group.len() > 2 {
            ALPHABET[usize::from(third & 63)] as char
        } else {
            '='
        });
    }
    encoded
}

#[cfg(test)]
#[test]
fn powershell_script_uses_utf16le_base64() {
    assert_eq!(encoded_powershell_script("A"), "QQA=");
    assert_eq!(encoded_powershell_script("Aé🌍"), "QQDpADzYDd8=");
}

#[cfg(windows)]
pub(super) fn run_acl_worker() -> Option<i32> {
    scopehub_platform::workspace_acl::dispatch(&FILES)
}

#[cfg(windows)]
pub(super) fn run_acl_test_worker() {
    scopehub_platform::workspace_acl::dispatch_test(&FILES);
}

#[cfg(all(windows, test))]
#[test]
fn workspace_acl_worker() {
    run_acl_test_worker();
}

#[cfg(windows)]
mod platform {
    use std::path::PathBuf;

    use super::*;

    fn check_acl(path: &Path, prepare: bool, children: u8) -> io::Result<()> {
        #[cfg(test)]
        scopehub_platform::workspace_acl::use_test_host("storage::workspace_acl_worker");

        scopehub_platform::workspace_acl::check(path, prepare, children)
    }

    pub(super) fn prepare(path: &Path) -> io::Result<()> {
        check_acl(path, true, 0)
    }

    pub(super) fn prepare_pool(path: &Path, count: NonZeroU8) -> io::Result<()> {
        check_acl(path, true, count.get())
    }

    pub(in super::super) fn open_path(root: &Path) -> io::Result<(PathBuf, bool)> {
        check_acl(root, false, 0)?;
        let path = root.join(FILES[0]);
        let exists = path.try_exists()?;
        Ok((path, exists))
    }
}

#[cfg(not(any(unix, windows)))]
mod platform {
    use std::path::PathBuf;

    use super::*;

    pub(super) fn prepare(_: &Path) -> io::Result<()> {
        Err(io::ErrorKind::Unsupported.into())
    }

    pub(in super::super) fn open_path(_: &Path) -> io::Result<(PathBuf, bool)> {
        Err(io::ErrorKind::Unsupported.into())
    }
}
