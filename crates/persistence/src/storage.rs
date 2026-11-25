//! Verify filesystem ownership and access before SQLite touches a profile.

use std::{
    fs::{self, File},
    io::{self, Write},
    path::{Path, PathBuf},
};

pub(super) const PROFILE_ANCHOR: &str = "desktop-profile.v1";
const FILES: [&str; 5] = [
    super::DATABASE_NAME,
    "workspace.sqlite3-wal",
    "workspace.sqlite3-shm",
    "workspace.sqlite3-journal",
    PROFILE_ANCHOR,
];

#[derive(Debug)]
pub(super) struct ProfileStorage {
    pub database: PathBuf,
    pub exists: bool,
    pub owner: Option<String>,
}

fn denied() -> io::Error {
    io::ErrorKind::PermissionDenied.into()
}

pub(super) fn prepare_profile_root(path: &Path) -> io::Result<()> {
    if !path.is_absolute() || path.file_name().is_none() {
        return Err(denied());
    }
    platform::prepare(path)
}

pub(super) use platform::{create_database, open_path};

pub(super) fn publish_anchor(path: &Path, content: &[u8]) -> io::Result<()> {
    let directory = path.parent().ok_or_else(denied)?;
    let temporary = directory.join(format!(".desktop-profile-{}", uuid::Uuid::new_v4()));
    let mut file = platform::create_private_file(&temporary)?;
    let written = file.write_all(content).and_then(|()| file.sync_all());
    drop(file);
    let installed = written.and_then(|()| fs::rename(&temporary, path));
    if installed.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    installed?;
    #[cfg(unix)]
    File::open(directory)?.sync_all()?;
    Ok(())
}

#[cfg(any(target_os = "linux", windows))]
fn owner_digest(principal: &str) -> String {
    use sha2::{Digest, Sha256};

    let mut digest = Sha256::new();
    digest.update(b"ScopeHub desktop profile owner v1\0");
    digest.update(principal.as_bytes());
    format!("{:x}", digest.finalize())
}

#[cfg(unix)]
mod platform {
    use std::{
        fs::{DirBuilder, OpenOptions},
        os::unix::fs::{DirBuilderExt, MetadataExt, OpenOptionsExt},
        path::Component,
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

    #[cfg(target_os = "linux")]
    fn owner(uid: u32) -> Option<String> {
        let machine = fs::read_to_string("/etc/machine-id").ok()?;
        let machine = machine.trim();
        (machine.len() == 32
            && machine.bytes().all(|byte| byte.is_ascii_hexdigit())
            && machine.bytes().any(|byte| byte != b'0'))
        .then(|| owner_digest(&format!("linux:{machine}:{uid}")))
    }

    #[cfg(not(target_os = "linux"))]
    fn owner(_: u32) -> Option<String> {
        None
    }

    pub(in super::super) fn open_path(root: &Path) -> io::Result<ProfileStorage> {
        let uid = effective_uid()?;
        let directory = profile(root, uid, false)?;
        let database = directory.join(FILES[0]);
        let exists = check_file(&database, uid)?;
        // Refuse unsafe sidecars before reading the anchor or letting SQLite recover.
        for name in &FILES[1..] {
            check_file(&directory.join(name), uid)?;
        }
        Ok(ProfileStorage {
            database,
            exists,
            owner: owner(uid),
        })
    }

    pub(in super::super) fn create_database(path: &Path) -> io::Result<bool> {
        // SQLite derives its WAL, SHM and journal file mode from the database.
        match create_private_file(path) {
            Ok(_) => Ok(false),
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {
                if !check_file(path, effective_uid()?)? {
                    return Err(error);
                }
                Ok(true)
            }
            Err(error) => Err(error),
        }
    }

    pub(super) fn create_private_file(path: &Path) -> io::Result<File> {
        OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(path)
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
        fs::OpenOptions,
        io::Read,
        os::windows::process::CommandExt,
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

    fn check_acl(path: &Path, action: &str) -> io::Result<Option<String>> {
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
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .inspect_err(|_| report_refusal(action, "powershell-spawn"))?;
        let deadline = Instant::now() + Duration::from_secs(10);
        loop {
            match child.try_wait() {
                Ok(Some(status)) if status.success() => break,
                Ok(Some(status)) => {
                    let stage = refusal_stage(status.code());
                    report_refusal(action, stage);
                    return Err(io::Error::new(io::ErrorKind::PermissionDenied, stage));
                }
                Ok(None) if Instant::now() < deadline => thread::sleep(Duration::from_millis(20)),
                Ok(None) => return stop_probe(child, action, io::ErrorKind::TimedOut.into()),
                Err(error) => return stop_probe(child, action, error),
            }
        }
        let mut sid = String::new();
        child
            .stdout
            .take()
            .ok_or_else(denied)?
            .take(256)
            .read_to_string(&mut sid)?;
        let sid = sid.trim();
        if action == "prepare" {
            return Ok(None);
        }
        if sid.len() > 184
            || !sid.strip_prefix("S-1-").is_some_and(|parts| {
                parts
                    .split('-')
                    .all(|part| !part.is_empty() && part.bytes().all(|byte| byte.is_ascii_digit()))
            })
        {
            return Err(denied());
        }
        Ok(Some(owner_digest(&format!("windows:{sid}"))))
    }

    fn stop_probe(
        mut child: std::process::Child,
        action: &str,
        error: io::Error,
    ) -> io::Result<Option<String>> {
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
        check_acl(path, "prepare").map(|_| ())
    }

    pub(in super::super) fn open_path(root: &Path) -> io::Result<ProfileStorage> {
        let owner = check_acl(root, "open")?;
        let database = root.join(FILES[0]);
        let exists = database.try_exists()?;
        Ok(ProfileStorage {
            database,
            exists,
            owner,
        })
    }

    pub(in super::super) fn create_database(path: &Path) -> io::Result<bool> {
        // Hold the same validated directory inheritance for database creation.
        match create_private_file(path) {
            Ok(_) => Ok(false),
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {
                check_acl(path.parent().ok_or_else(denied)?, "open")?;
                Ok(true)
            }
            Err(error) => Err(error),
        }
    }

    pub(super) fn create_private_file(path: &Path) -> io::Result<File> {
        OpenOptions::new().write(true).create_new(true).open(path)
    }
}

#[cfg(not(any(unix, windows)))]
mod platform {
    use super::*;

    pub(super) fn prepare(_: &Path) -> io::Result<()> {
        Err(io::ErrorKind::Unsupported.into())
    }

    pub(in super::super) fn open_path(_: &Path) -> io::Result<ProfileStorage> {
        Err(io::ErrorKind::Unsupported.into())
    }

    pub(in super::super) fn create_database(_: &Path) -> io::Result<bool> {
        Err(io::ErrorKind::Unsupported.into())
    }

    pub(super) fn create_private_file(_: &Path) -> io::Result<File> {
        Err(io::ErrorKind::Unsupported.into())
    }
}
