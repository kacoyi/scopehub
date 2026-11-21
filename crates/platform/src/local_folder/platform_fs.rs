//! Open an explicitly selected directory and confine descendant opens to it.

use std::{
    fs::File,
    io,
    path::{Path, PathBuf},
};

#[cfg(any(target_os = "linux", windows))]
use std::{ffi::OsStr, path::Component};

pub(super) use native::{Directory, FileIdentity, file_identity, is_redirect, same_version};

/// Remembers the selected object between operations without retaining Windows
/// handles that restrict editors. Only a verified, operation-scoped Directory
/// can enumerate or open descendants.
pub(super) struct SelectedDirectory {
    path: PathBuf,
    identity: FileIdentity,
    _handle: File,
}

impl SelectedDirectory {
    pub(super) fn adopt(path: &Path) -> io::Result<(Self, Directory)> {
        let directory = Directory::open_root(path)?;
        let identity = directory.identity()?;
        let handle = directory.selection_handle()?;
        if file_identity(&handle)? != identity {
            return Err(io::ErrorKind::NotFound.into());
        }
        Ok((
            Self {
                path: path.to_owned(),
                identity,
                _handle: handle,
            },
            directory,
        ))
    }

    pub(super) fn pin(&self) -> io::Result<Directory> {
        let directory = Directory::open_root(&self.path)?;
        if directory.identity()? != self.identity {
            return Err(io::ErrorKind::NotFound.into());
        }
        Ok(directory)
    }
}

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

    pub(crate) type FileIdentity = (u64, u64, Option<std::time::SystemTime>);

    pub(crate) fn file_identity(file: &File) -> io::Result<FileIdentity> {
        let metadata = file.metadata()?;
        Ok((metadata.dev(), metadata.ino(), metadata.created().ok()))
    }

    impl Directory {
        pub(crate) fn identity(&self) -> io::Result<FileIdentity> {
            file_identity(&self.0)
        }

        pub(crate) fn selection_handle(&self) -> io::Result<File> {
            self.0.try_clone()
        }

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
    const FILE_SHARE_WRITE: u32 = 0x0000_0002;
    const FILE_SHARE_DELETE: u32 = 0x0000_0004;
    const FILE_READ_ATTRIBUTES: u32 = 0x0000_0080;
    const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x0000_0400;
    const FILE_FLAG_OPEN_REPARSE_POINT: u32 = 0x0020_0000;
    const FILE_FLAG_BACKUP_SEMANTICS: u32 = 0x0200_0000;

    pub(crate) struct Directory {
        path: PathBuf,
        // Windows paths are used only while their whole ancestor chain is
        // pinned by handles opened without write or delete sharing.
        handle: Arc<File>,
        ancestors: Vec<Arc<File>>,
    }

    pub(crate) type FileIdentity = fs_id::FileID;

    pub(crate) fn file_identity(file: &File) -> io::Result<FileIdentity> {
        let identity = FileIdentity::new(file)?;
        if matches!(identity.internal_file_id(), 0 | u128::MAX) {
            return Err(io::ErrorKind::Unsupported.into());
        }
        Ok(identity)
    }

    impl Directory {
        pub(crate) fn identity(&self) -> io::Result<FileIdentity> {
            file_identity(&self.handle)
        }

        pub(crate) fn selection_handle(&self) -> io::Result<File> {
            // The strictly pinned chain still guards this open. Attribute
            // access and full sharing let outside editors use the directory
            // while this handle keeps its identity alive.
            let handle = OpenOptions::new()
                .access_mode(FILE_READ_ATTRIBUTES)
                .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)
                .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS)
                .open(&self.path)?;
            require_directory(&handle)?;
            Ok(handle)
        }

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
                handle: Arc::new(pin_directory(&root)?),
                ancestors: Vec::new(),
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
            let mut ancestors = self.ancestors.clone();
            ancestors.push(Arc::clone(&self.handle));
            let handle = Arc::new(pin_directory(&path)?);
            Ok(Self {
                path,
                handle,
                ancestors,
            })
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
        require_directory(&directory)?;
        Ok(directory)
    }

    fn require_directory(file: &File) -> io::Result<()> {
        let metadata = file.metadata()?;
        if !metadata.is_dir() || is_redirect(&metadata) {
            return Err(io::ErrorKind::InvalidData.into());
        }
        Ok(())
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

    #[derive(Clone, Copy, PartialEq)]
    pub(crate) struct FileIdentity;

    pub(crate) fn file_identity(_: &File) -> io::Result<FileIdentity> {
        Err(io::ErrorKind::Unsupported.into())
    }

    impl Directory {
        pub(crate) fn identity(&self) -> io::Result<FileIdentity> {
            Err(io::ErrorKind::Unsupported.into())
        }

        pub(crate) fn selection_handle(&self) -> io::Result<File> {
            Err(io::ErrorKind::Unsupported.into())
        }

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
