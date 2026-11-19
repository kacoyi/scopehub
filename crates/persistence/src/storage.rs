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

    fn check_acl(path: &Path, action: &str) -> io::Result<()> {
        let system = PathBuf::from(env::var_os("SystemRoot").ok_or_else(denied)?);
        if !system.is_absolute() {
            return Err(denied());
        }
        let powershell = system.join("System32/WindowsPowerShell/v1.0/powershell.exe");
        let mut child = Command::new(powershell)
            .args(["-NoProfile", "-NonInteractive", "-NoLogo", "-Command"])
            .arg(include_str!("storage_acl.ps1"))
            .env("SCOPEHUB_STORAGE_PATH", path)
            .env("SCOPEHUB_STORAGE_ACTION", action)
            .env("SCOPEHUB_STORAGE_FILES", FILES.join(";"))
            .creation_flags(0x0800_0000) // CREATE_NO_WINDOW
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()?;
        let deadline = Instant::now() + Duration::from_secs(10);
        let error = loop {
            match child.try_wait() {
                Ok(Some(status)) if status.success() => return Ok(()),
                Ok(Some(_)) => return Err(denied()),
                Ok(None) if Instant::now() < deadline => thread::sleep(Duration::from_millis(20)),
                Ok(None) => break io::ErrorKind::TimedOut.into(),
                Err(error) => break error,
            }
        };
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
