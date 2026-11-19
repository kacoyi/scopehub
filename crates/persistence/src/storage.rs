//! Verify filesystem ownership and access before SQLite touches a profile.

use std::{io, path::Path};

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
    if !path.is_absolute() || path.file_name().is_none() {
        return Err(denied());
    }
    platform::prepare(path)
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

#[cfg(any(windows, test))]
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
    assert!(encoded_powershell_script(include_str!("storage_acl.ps1")).len() < 30_000);
}

#[cfg(windows)]
mod platform {
    use std::{
        env,
        os::windows::process::CommandExt,
        path::PathBuf,
        process::{Command, Stdio},
        thread,
        time::{Duration, Instant},
    };

    use super::*;

    fn refusal_stage(exit: Option<i32>) -> &'static str {
        match exit {
            Some(20) => "account",
            Some(21) => "local-path",
            Some(22) => "local-ntfs",
            Some(23) => "action",
            Some(24) => "metadata",
            Some(25) => "path-type",
            Some(26) => "acl-read",
            Some(27) => "acl-entry",
            Some(28) => "ancestor-owner",
            Some(29) => "replaceable-ancestor",
            Some(30) => "shared-access",
            Some(31) => "owner-or-inheritance",
            Some(32) => "private-creation",
            Some(33) => "missing-directory",
            Some(34) => "profile-or-file-owner",
            _ => "powershell-execution",
        }
    }

    fn report_refusal(action: &str, stage: &str) {
        eprintln!("Workspace storage ACL check refused ({action}, {stage}).");
    }

    fn check_acl(path: &Path, action: &str) -> io::Result<()> {
        let system = env::var_os("SystemRoot").map(PathBuf::from);
        let Some(system) = system.filter(|path| path.is_absolute()) else {
            report_refusal(action, "powershell-location");
            return Err(denied());
        };
        let powershell = system.join("System32/WindowsPowerShell/v1.0/powershell.exe");
        let mut child = Command::new(powershell)
            .args([
                "-NoProfile",
                "-NonInteractive",
                "-NoLogo",
                "-EncodedCommand",
            ])
            .arg(encoded_powershell_script(include_str!("storage_acl.ps1")))
            .env("SCOPEHUB_STORAGE_PATH", path)
            .env("SCOPEHUB_STORAGE_ACTION", action)
            .env("SCOPEHUB_STORAGE_FILES", FILES.join(";"))
            .creation_flags(0x0800_0000) // CREATE_NO_WINDOW
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .inspect_err(|_| report_refusal(action, "powershell-spawn"))?;
        let deadline = Instant::now() + Duration::from_secs(10);
        let error = loop {
            match child.try_wait() {
                Ok(Some(status)) if status.success() => return Ok(()),
                Ok(Some(status)) => {
                    let stage = refusal_stage(status.code());
                    report_refusal(action, stage);
                    return Err(io::Error::new(io::ErrorKind::PermissionDenied, stage));
                }
                Ok(None) if Instant::now() < deadline => thread::sleep(Duration::from_millis(20)),
                Ok(None) => break io::ErrorKind::TimedOut.into(),
                Err(error) => break error,
            }
        };
        let stage = if error.kind() == io::ErrorKind::TimedOut {
            "powershell-timeout"
        } else {
            "powershell-wait"
        };
        report_refusal(action, stage);
        let _ = child.kill();
        let _ = child.wait();
        Err(error)
    }

    pub(super) fn prepare(path: &Path) -> io::Result<()> {
        check_acl(path, "prepare")
    }

    pub(in super::super) fn open_path(root: &Path) -> io::Result<(PathBuf, bool)> {
        check_acl(root, "open")?;
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
