//! Open an explicitly selected directory and confine descendant opens to it.

#[cfg(any(target_os = "linux", windows))]
use std::{
    ffi::OsStr,
    io,
    path::{Component, Path},
};

pub(super) use native::{Directory, is_redirect, same_version};

#[cfg(any(target_os = "linux", windows))]
fn require_child(name: &OsStr) -> io::Result<()> {
    let mut components = Path::new(name).components();
    if matches!(components.next(), Some(Component::Normal(part)) if part == name)
        && components.next().is_none()
    {
        Ok(())
    } else {
        Err(io::ErrorKind::InvalidInput.into())
    }
}

#[cfg(target_os = "linux")]
mod native {
    use std::{
        ffi::OsStr,
        fs::{self, File, Metadata, OpenOptions, ReadDir},
        io,
        os::{
            fd::AsRawFd,
            unix::fs::{MetadataExt, OpenOptionsExt},
        },
        path::{Component, Path, PathBuf},
    };

    use super::require_child;

    pub(crate) struct Directory(File);

    impl Directory {
        pub(crate) fn association_path(&self, selected: &Path) -> io::Result<PathBuf> {
            Ok(selected.to_owned())
        }

        pub(crate) fn open_root(path: &Path) -> io::Result<Self> {
            let mut components = path.components();
            if components.next() != Some(Component::RootDir) {
                return Err(io::ErrorKind::InvalidInput.into());
            }
            let mut directory = Self(pin_directory(Path::new("/"))?);
            for component in components {
                let Component::Normal(name) = component else {
                    return Err(io::ErrorKind::InvalidInput.into());
                };
                directory = directory.open_directory(name)?;
            }
            Ok(directory)
        }

        pub(crate) fn read_dir(&self) -> io::Result<ReadDir> {
            fs::read_dir(descriptor_path(&self.0))
        }

        pub(crate) fn open_directory(&self, name: &OsStr) -> io::Result<Self> {
            require_child(name)?;
            Ok(Self(pin_directory(&descriptor_path(&self.0).join(name))?))
        }

        pub(crate) fn open_file(&self, name: &OsStr) -> io::Result<File> {
            require_child(name)?;
            // Pin the entry and check its type without opening special devices,
            // FIFOs or symlink targets for I/O. The descriptor stays live while
            // its proc entry is reopened read-only.
            let entry = OpenOptions::new()
                .read(true)
                .custom_flags(libc::O_PATH | libc::O_NOFOLLOW)
                .open(descriptor_path(&self.0).join(name))?;
            if !entry.metadata()?.is_file() {
                return Err(io::ErrorKind::InvalidData.into());
            }
            File::open(descriptor_path(&entry))
        }
    }

    fn descriptor_path(file: &File) -> PathBuf {
        Path::new("/proc/self/fd").join(file.as_raw_fd().to_string())
    }

    fn pin_directory(path: &Path) -> io::Result<File> {
        OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_PATH | libc::O_NOFOLLOW | libc::O_DIRECTORY)
            .open(path)
    }

    pub(crate) fn is_redirect(metadata: &Metadata) -> bool {
        metadata.is_symlink()
    }

    pub(crate) fn same_version(before: &Metadata, after: &Metadata) -> bool {
        before.dev() == after.dev()
            && before.ino() == after.ino()
            && before.len() == after.len()
            && before.mtime() == after.mtime()
            && before.mtime_nsec() == after.mtime_nsec()
            && before.ctime() == after.ctime()
            && before.ctime_nsec() == after.ctime_nsec()
    }
}

#[cfg(windows)]
mod native {
    use std::{
        ffi::OsStr,
        fs::{self, File, Metadata, OpenOptions, ReadDir},
        io,
        os::windows::{
            ffi::OsStrExt,
            fs::{MetadataExt, OpenOptionsExt},
        },
        path::{Component, Path, PathBuf, Prefix},
        sync::Arc,
    };

    use super::require_child;

    const FILE_SHARE_READ: u32 = 0x0000_0001;
    const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x0000_0400;
    const FILE_FLAG_OPEN_REPARSE_POINT: u32 = 0x0020_0000;
    const FILE_FLAG_BACKUP_SEMANTICS: u32 = 0x0200_0000;

    pub(crate) struct Directory {
        path: PathBuf,
        // Windows paths are used only while their whole ancestor chain is
        // pinned by handles opened without write or delete sharing.
        pins: Vec<Arc<File>>,
    }

    impl Directory {
        pub(crate) fn association_path(&self, _: &Path) -> io::Result<PathBuf> {
            // All components have passed the reparse-point checks and remain
            // pinned against modification and rename. Resolve the on-disk
            // spelling here so short names and caller casing cannot split
            // the mapping key for the same admitted directory.
            self.path.canonicalize()
        }

        pub(crate) fn open_root(path: &Path) -> io::Result<Self> {
            let mut components = path.components();
            let Some(Component::Prefix(prefix)) = components.next() else {
                return Err(io::ErrorKind::InvalidInput.into());
            };
            let (Prefix::Disk(drive) | Prefix::VerbatimDisk(drive)) = prefix.kind() else {
                return Err(io::ErrorKind::Unsupported.into());
            };
            if components.next() != Some(Component::RootDir) {
                return Err(io::ErrorKind::InvalidInput.into());
            }
            let root = PathBuf::from(format!(r"\\?\{}:\", char::from(drive.to_ascii_uppercase())));
            let mut directory = Self {
                pins: vec![Arc::new(pin_directory(&root)?)],
                path: root,
            };
            for component in components {
                let Component::Normal(name) = component else {
                    return Err(io::ErrorKind::InvalidInput.into());
                };
                directory = directory.open_directory(name)?;
            }
            Ok(directory)
        }

        pub(crate) fn read_dir(&self) -> io::Result<ReadDir> {
            fs::read_dir(&self.path)
        }

        pub(crate) fn open_directory(&self, name: &OsStr) -> io::Result<Self> {
            let path = self.child_path(name)?;
            let mut pins = self.pins.clone();
            pins.push(Arc::new(pin_directory(&path)?));
            Ok(Self { path, pins })
        }

        pub(crate) fn open_file(&self, name: &OsStr) -> io::Result<File> {
            let path = self.child_path(name)?;
            let file = OpenOptions::new()
                .read(true)
                .share_mode(FILE_SHARE_READ)
                .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT)
                .open(path)?;
            let metadata = file.metadata()?;
            if !metadata.is_file() || is_redirect(&metadata) {
                return Err(io::ErrorKind::InvalidData.into());
            }
            Ok(file)
        }

        fn child_path(&self, name: &OsStr) -> io::Result<PathBuf> {
            require_child(name)?;
            if name.encode_wide().any(|unit| unit == u16::from(b':')) {
                return Err(io::ErrorKind::InvalidInput.into());
            }
            Ok(self.path.join(name))
        }
    }

    fn pin_directory(path: &Path) -> io::Result<File> {
        let directory = OpenOptions::new()
            .read(true)
            .share_mode(FILE_SHARE_READ)
            .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS)
            .open(path)?;
        let metadata = directory.metadata()?;
        if !metadata.is_dir() || is_redirect(&metadata) {
            return Err(io::ErrorKind::InvalidData.into());
        }
        Ok(directory)
    }

    pub(crate) fn is_redirect(metadata: &Metadata) -> bool {
        metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
    }

    pub(crate) fn same_version(before: &Metadata, after: &Metadata) -> bool {
        before.len() == after.len()
            && before.creation_time() == after.creation_time()
            && before.last_write_time() == after.last_write_time()
            && before.file_attributes() == after.file_attributes()
    }
}

#[cfg(not(any(target_os = "linux", windows)))]
mod native {
    use std::{
        ffi::OsStr,
        fs::{File, Metadata, ReadDir},
        io,
        path::{Path, PathBuf},
    };

    pub(crate) struct Directory;

    impl Directory {
        pub(crate) fn association_path(&self, _: &Path) -> io::Result<PathBuf> {
            Err(io::ErrorKind::Unsupported.into())
        }

        pub(crate) fn open_root(_: &Path) -> io::Result<Self> {
            Err(io::ErrorKind::Unsupported.into())
        }

        pub(crate) fn read_dir(&self) -> io::Result<ReadDir> {
            Err(io::ErrorKind::Unsupported.into())
        }

        pub(crate) fn open_directory(&self, _: &OsStr) -> io::Result<Self> {
            Err(io::ErrorKind::Unsupported.into())
        }

        pub(crate) fn open_file(&self, _: &OsStr) -> io::Result<File> {
            Err(io::ErrorKind::Unsupported.into())
        }
    }

    pub(crate) fn is_redirect(_: &Metadata) -> bool {
        true
    }

    pub(crate) fn same_version(_: &Metadata, _: &Metadata) -> bool {
        false
    }
}
