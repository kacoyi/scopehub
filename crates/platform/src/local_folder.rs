//! Session-scoped read-only access to an explicitly selected local directory.

mod platform_fs;

use std::{
    ffi::OsStr,
    fmt,
    io::{self, Read},
    path::{Component, Path, PathBuf},
    str::FromStr,
    sync::{
        Arc, Mutex, MutexGuard, TryLockError,
        atomic::{AtomicBool, Ordering},
    },
};

use sha2::{Digest, Sha256};
use uuid::Uuid;

use platform_fs::{Directory, is_redirect, same_version};

/// Maximum complete source bytes kept for the current Python CSV preview.
/// Larger files still get a whole-file digest and byte count.
pub const MAX_PREVIEW_BYTES: usize = 4 * 1024 * 1024;
const MAX_CATALOG_ENTRIES: usize = 4096;
const MAX_DIRECTORY_DEPTH: usize = 16;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct FolderId(Uuid);

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct FileId(Uuid);

impl FromStr for FolderId {
    type Err = uuid::Error;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        value.parse().map(Self)
    }
}

impl fmt::Display for FolderId {
    fn fmt(&self, output: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.0.fmt(output)
    }
}

impl FromStr for FileId {
    type Err = uuid::Error;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        value.parse().map(Self)
    }
}

impl fmt::Display for FileId {
    fn fmt(&self, output: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.0.fmt(output)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum FileKind {
    Csv,
    UnsupportedExtension,
    UnsupportedEntry,
}

#[derive(Clone, Eq, PartialEq)]
pub struct FileMetadata {
    pub file_id: FileId,
    /// Display-only metadata for the authorized local view.
    pub relative_path: String,
    pub display_name: String,
    pub byte_count: u64,
    pub kind: FileKind,
}

#[derive(Clone, Eq, PartialEq)]
pub struct FolderCatalog {
    pub folder_id: FolderId,
    pub display_name: String,
    pub files: Vec<FileMetadata>,
}

#[derive(Clone)]
pub struct FrozenFile {
    pub folder_id: FolderId,
    pub file: FileMetadata,
    pub sha256: String,
    /// Frozen complete source bytes. Above MAX_PREVIEW_BYTES the digest and
    /// byte count are still available, but no normalized preview can be made.
    pub content: Option<Arc<[u8]>>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum FolderError {
    Cancelled,
    SelectionUnavailable,
    UnknownFile,
    UnsupportedFile,
    ContentChanged,
    InvalidSelection,
    CatalogLimit,
    Busy,
    Io,
}

#[derive(Default)]
pub struct Cancellation(AtomicBool);

impl Cancellation {
    pub fn cancel(&self) {
        self.0.store(true, Ordering::Release);
    }

    pub fn is_cancelled(&self) -> bool {
        self.0.load(Ordering::Acquire)
    }

    fn check(&self) -> Result<(), FolderError> {
        if self.is_cancelled() {
            return Err(FolderError::Cancelled);
        }
        Ok(())
    }
}

struct FileEntry {
    relative: PathBuf,
    metadata: FileMetadata,
}

struct Selection {
    id: FolderId,
    display_name: String,
    directory: Directory,
    files: Vec<FileEntry>,
    revoked: Cancellation,
}

impl Selection {
    fn catalog(&self) -> FolderCatalog {
        FolderCatalog {
            folder_id: self.id,
            display_name: self.display_name.clone(),
            files: self
                .files
                .iter()
                .map(|file| file.metadata.clone())
                .collect(),
        }
    }

    fn check_live(&self, cancel: &Cancellation) -> Result<(), FolderError> {
        cancel.check()?;
        if self.revoked.is_cancelled() {
            return Err(FolderError::SelectionUnavailable);
        }
        Ok(())
    }
}

/// Owned by one trusted host session. File reads are identified by a current
/// folder and its catalog-issued IDs. None of the read methods accept a path.
#[derive(Default)]
pub struct LocalFolderHost {
    active: Mutex<Option<Arc<Selection>>>,
    adopting: Mutex<()>,
    reading: Mutex<()>,
}

impl LocalFolderHost {
    /// Adopt only a directory chosen by the trusted native launcher or picker.
    /// Scanning is blocking and must run off the UI thread. A failed or
    /// cancelled adoption leaves the active selection usable.
    /// The catalog is bounded to 4096 entries and 16 nested directory levels.
    pub fn adopt_selected(
        &self,
        path: &Path,
        cancel: &Cancellation,
    ) -> Result<FolderCatalog, FolderError> {
        cancel.check()?;
        let _admission = admit(&self.adopting)?;
        let directory = Directory::open_root(path).map_err(|_| FolderError::InvalidSelection)?;
        let mut files = Vec::new();
        let mut visited = 0;
        scan(
            &directory,
            Path::new(""),
            0,
            cancel,
            &mut visited,
            &mut files,
        )?;
        files.sort_unstable_by(|left, right| left.relative.cmp(&right.relative));
        let selection = Arc::new(Selection {
            id: FolderId(Uuid::new_v4()),
            display_name: path.file_name().map_or_else(
                || "Selected folder".into(),
                |name| name.to_string_lossy().into_owned(),
            ),
            directory,
            files,
            revoked: Cancellation::default(),
        });
        let catalog = selection.catalog();
        let mut active = self.active.lock().map_err(|_| FolderError::Io)?;
        cancel.check()?;
        if let Some(previous) = active.replace(selection) {
            previous.revoked.cancel();
        }
        Ok(catalog)
    }

    pub fn catalog(&self, folder_id: FolderId) -> Result<FolderCatalog, FolderError> {
        let active = self.active.lock().map_err(|_| FolderError::Io)?;
        active
            .as_ref()
            .filter(|selection| selection.id == folder_id)
            .map(|selection| selection.catalog())
            .ok_or(FolderError::SelectionUnavailable)
    }

    pub fn revoke(&self, folder_id: FolderId) -> Result<(), FolderError> {
        let mut active = self.active.lock().map_err(|_| FolderError::Io)?;
        let selection = active
            .take_if(|selection| selection.id == folder_id)
            .ok_or(FolderError::SelectionUnavailable)?;
        selection.revoked.cancel();
        Ok(())
    }

    pub fn check_current(&self, folder_id: FolderId) -> Result<(), FolderError> {
        let active = self.active.lock().map_err(|_| FolderError::Io)?;
        if active
            .as_ref()
            .is_some_and(|selection| selection.id == folder_id)
        {
            Ok(())
        } else {
            Err(FolderError::SelectionUnavailable)
        }
    }

    /// Hash the complete opened file and freeze the very same bytes when within
    /// the preview budget. Bind a preview to previously observed bytes by
    /// supplying their expected digest. Reads are blocking and cancellable
    /// between chunks, and an obsolete selection cannot return a new result.
    pub fn read(
        &self,
        folder_id: FolderId,
        file_id: FileId,
        expected_sha256: Option<&str>,
        cancel: &Cancellation,
    ) -> Result<FrozenFile, FolderError> {
        cancel.check()?;
        let _admission = admit(&self.reading)?;
        let selection = {
            let active = self.active.lock().map_err(|_| FolderError::Io)?;
            active
                .as_ref()
                .filter(|selection| selection.id == folder_id)
                .cloned()
                .ok_or(FolderError::SelectionUnavailable)?
        };
        let entry = selection
            .files
            .iter()
            .find(|entry| entry.metadata.file_id == file_id)
            .ok_or(FolderError::UnknownFile)?;
        if entry.metadata.kind != FileKind::Csv {
            return Err(FolderError::UnsupportedFile);
        }
        let frozen = read_entry(&selection, entry, expected_sha256, cancel)?;
        let _active = self.active.lock().map_err(|_| FolderError::Io)?;
        selection.check_live(cancel)?;
        Ok(frozen)
    }
}

fn admit(slot: &Mutex<()>) -> Result<MutexGuard<'_, ()>, FolderError> {
    slot.try_lock().map_err(|error| match error {
        TryLockError::WouldBlock => FolderError::Busy,
        TryLockError::Poisoned(_) => FolderError::Io,
    })
}

fn scan(
    directory: &Directory,
    parent: &Path,
    depth: usize,
    cancel: &Cancellation,
    visited: &mut usize,
    files: &mut Vec<FileEntry>,
) -> Result<(), FolderError> {
    cancel.check()?;
    for entry in directory.read_dir().map_err(|_| FolderError::Io)? {
        cancel.check()?;
        *visited += 1;
        if *visited > MAX_CATALOG_ENTRIES {
            return Err(FolderError::CatalogLimit);
        }
        let entry = entry.map_err(|_| FolderError::Io)?;
        let name = entry.file_name();
        let relative = parent.join(&name);
        let metadata = entry.metadata().map_err(|_| FolderError::Io)?;
        let redirect = is_redirect(&metadata);
        let kind = if redirect || name.to_str().is_none() {
            FileKind::UnsupportedEntry
        } else if metadata.is_dir() {
            if depth == MAX_DIRECTORY_DEPTH {
                return Err(FolderError::CatalogLimit);
            }
            let child = directory
                .open_directory(&name)
                .map_err(|_| FolderError::Io)?;
            scan(&child, &relative, depth + 1, cancel, visited, files)?;
            continue;
        } else if !metadata.is_file() {
            FileKind::UnsupportedEntry
        } else if relative
            .extension()
            .and_then(OsStr::to_str)
            .is_some_and(|extension| extension.eq_ignore_ascii_case("csv"))
        {
            FileKind::Csv
        } else {
            FileKind::UnsupportedExtension
        };
        files.push(FileEntry {
            metadata: FileMetadata {
                file_id: FileId(Uuid::new_v4()),
                relative_path: relative
                    .iter()
                    .map(|part| part.to_string_lossy())
                    .collect::<Vec<_>>()
                    .join("/"),
                display_name: name.to_string_lossy().into_owned(),
                byte_count: if metadata.is_file() && !redirect {
                    metadata.len()
                } else {
                    0
                },
                kind,
            },
            relative,
        });
    }
    Ok(())
}

fn read_entry(
    selection: &Selection,
    entry: &FileEntry,
    expected_sha256: Option<&str>,
    cancel: &Cancellation,
) -> Result<FrozenFile, FolderError> {
    selection.check_live(cancel)?;
    let mut components = entry.relative.components();
    let Some(Component::Normal(name)) = components.next_back() else {
        return Err(FolderError::UnsupportedFile);
    };
    let mut parent = None;
    for component in components {
        selection.check_live(cancel)?;
        let Component::Normal(part) = component else {
            return Err(FolderError::UnsupportedFile);
        };
        let directory = parent.as_ref().unwrap_or(&selection.directory);
        parent = Some(directory.open_directory(part).map_err(open_error)?);
    }
    let directory = parent.as_ref().unwrap_or(&selection.directory);
    let mut file = directory.open_file(name).map_err(open_error)?;
    let before = file.metadata().map_err(|_| FolderError::Io)?;
    let mut digest = Sha256::new();
    let mut content = (before.len() <= MAX_PREVIEW_BYTES as u64)
        .then(|| Vec::with_capacity(before.len() as usize));
    let mut byte_count = 0;
    let mut buffer = [0; 64 * 1024];
    loop {
        selection.check_live(cancel)?;
        let count = match file.read(&mut buffer) {
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            result => result.map_err(|_| FolderError::Io)?,
        };
        if count == 0 {
            break;
        }
        byte_count += count as u64;
        if byte_count > before.len() {
            return Err(FolderError::ContentChanged);
        }
        digest.update(&buffer[..count]);
        if let Some(content) = &mut content {
            content.extend_from_slice(&buffer[..count]);
        }
    }
    let after = file.metadata().map_err(|_| FolderError::Io)?;
    if byte_count != before.len() || !same_version(&before, &after) {
        return Err(FolderError::ContentChanged);
    }
    let sha256 = format!("{:x}", digest.finalize());
    if expected_sha256.is_some_and(|expected| expected != sha256) {
        return Err(FolderError::ContentChanged);
    }
    let mut metadata = entry.metadata.clone();
    metadata.byte_count = byte_count;
    Ok(FrozenFile {
        folder_id: selection.id,
        file: metadata,
        sha256,
        content: content.map(Arc::from),
    })
}

fn open_error(error: io::Error) -> FolderError {
    match error.kind() {
        io::ErrorKind::NotFound => FolderError::ContentChanged,
        io::ErrorKind::InvalidData | io::ErrorKind::InvalidInput => FolderError::UnsupportedFile,
        _ => FolderError::Io,
    }
}
