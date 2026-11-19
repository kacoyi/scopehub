//! Session-scoped read-only access to an explicitly selected local directory.

mod platform_fs;

use std::{
    ffi::OsStr,
    fmt,
    fs::{File, Metadata},
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
    observed: Metadata,
}

struct Selection {
    id: FolderId,
    source_namespace: [u8; 32],
    display_name: String,
    directory: Directory,
    files: Vec<FileEntry>,
    revoked: Cancellation,
}

impl Selection {
    fn csv_entry(&self, file_id: FileId) -> Result<&FileEntry, FolderError> {
        let entry = self
            .files
            .iter()
            .find(|entry| entry.metadata.file_id == file_id)
            .ok_or(FolderError::UnknownFile)?;
        if entry.metadata.kind != FileKind::Csv {
            return Err(FolderError::UnsupportedFile);
        }
        Ok(entry)
    }

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

/// Private catalog of the selected directory at the start of refresh review.
pub struct FolderRefreshScan {
    selection: Arc<Selection>,
    accepted_path: PathBuf,
    files: Vec<FileEntry>,
}

impl FolderRefreshScan {
    pub fn files(&self) -> impl Iterator<Item = &FileMetadata> {
        self.files.iter().map(|entry| &entry.metadata)
    }
}

/// Owned by one trusted host session. All file reads resolve through the
/// current native folder selection and its catalog-issued IDs.
#[derive(Default)]
pub struct LocalFolderHost {
    active: Mutex<Option<Arc<Selection>>>,
    closed: Cancellation,
    adopting: Mutex<()>,
    reading: Mutex<()>,
}

impl LocalFolderHost {
    /// Revoke the active selection and prevent any further adoption. This does
    /// not wait for a native operation that already holds a directory handle.
    pub fn begin_shutdown(&self) {
        let mut active = self
            .active
            .lock()
            .unwrap_or_else(|error| error.into_inner());
        // Advance the lifecycle under the same lock that publishes a selection.
        self.closed.cancel();
        if let Some(selection) = active.take() {
            selection.revoked.cancel();
        }
    }

    /// Close the host and wait for native operations to release their handles.
    /// An in-progress filesystem call can only stop after the OS returns.
    pub fn shutdown(&self) {
        self.begin_shutdown();
        drop(self.adopting.lock());
        drop(self.reading.lock());
    }

    fn check_open(&self) -> Result<(), FolderError> {
        if self.closed.is_cancelled() {
            return Err(FolderError::SelectionUnavailable);
        }
        Ok(())
    }

    fn admit_open<'a>(&self, slot: &'a Mutex<()>) -> Result<MutexGuard<'a, ()>, FolderError> {
        self.check_open()?;
        let admission = admit(slot);
        self.check_open()?;
        admission
    }

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
        let _admission = self.admit_open(&self.adopting)?;
        let check = || {
            self.check_open()?;
            cancel.check()
        };
        let directory = Directory::open_root(path).map_err(|_| FolderError::InvalidSelection)?;
        let association = directory
            .association_path(path)
            .map_err(|_| FolderError::InvalidSelection)?;
        let source_namespace = path_fingerprint(b"scopehub-local-folder-v1", &association);
        let mut files = Vec::new();
        let mut visited = 0;
        scan(
            &directory,
            Path::new(""),
            0,
            false,
            &check,
            &mut visited,
            &mut files,
        )?;
        files.sort_unstable_by(|left, right| left.relative.cmp(&right.relative));
        let selection = Arc::new(Selection {
            id: FolderId(Uuid::new_v4()),
            source_namespace,
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
        check()?;
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
        self.with_current(folder_id, || ())
    }

    /// Order native grant revocation against an authorized final commit.
    pub fn with_current<T>(
        &self,
        folder_id: FolderId,
        commit: impl FnOnce() -> T,
    ) -> Result<T, FolderError> {
        let active = self.active.lock().map_err(|_| FolderError::Io)?;
        if active
            .as_ref()
            .is_some_and(|selection| selection.id == folder_id)
        {
            Ok(commit())
        } else {
            Err(FolderError::SelectionUnavailable)
        }
    }

    /// Stable association metadata derived from a currently authorized file.
    pub fn source_key(&self, folder_id: FolderId, file_id: FileId) -> Result<String, FolderError> {
        let active = self.active.lock().map_err(|_| FolderError::Io)?;
        let selection = active
            .as_ref()
            .filter(|selection| selection.id == folder_id)
            .ok_or(FolderError::SelectionUnavailable)?;
        let entry = selection.csv_entry(file_id)?;
        Ok(source_key_for_path(selection, &entry.relative))
    }

    /// Enumerate the live pinned directory for a private, independent review.
    /// The saved identity proves that the accepted relative path belongs to this
    /// directory even when the accepted entry is now missing.
    pub fn scan_for_refresh(
        &self,
        folder_id: FolderId,
        accepted_path: &str,
        accepted_key: &str,
        cancel: &Cancellation,
    ) -> Result<FolderRefreshScan, FolderError> {
        let _admission = self.admit_open(&self.reading)?;
        let selection = self.selected(folder_id)?;
        let accepted = Path::new(accepted_path);
        if accepted.as_os_str().is_empty()
            || !accepted
                .components()
                .all(|part| matches!(part, Component::Normal(_)))
            || source_key_for_path(&selection, accepted) != accepted_key
        {
            return Err(FolderError::InvalidSelection);
        }
        let files = scan_refresh_entries(&selection, accepted, cancel)?;
        Ok(FolderRefreshScan {
            selection,
            accepted_path: accepted.to_owned(),
            files,
        })
    }

    /// Freeze one complete CSV from the staged enumeration without changing
    /// the original picker catalog or accepting a path from the UI.
    pub fn read_for_refresh(
        &self,
        scan: &FolderRefreshScan,
        file_id: FileId,
        cancel: &Cancellation,
    ) -> Result<FrozenFile, FolderError> {
        let _admission = self.admit_open(&self.reading)?;
        let selection = self.selected(scan.selection.id)?;
        if !Arc::ptr_eq(&selection, &scan.selection) {
            return Err(FolderError::SelectionUnavailable);
        }
        let entry = scan
            .files
            .iter()
            .find(|entry| entry.metadata.file_id == file_id)
            .ok_or(FolderError::UnknownFile)?;
        if entry.metadata.kind != FileKind::Csv {
            return Err(FolderError::UnsupportedFile);
        }
        let frozen = read_entry(
            &selection,
            entry,
            None,
            cancel,
            ReadMode::Publication,
            |file, checked| {
                if !same_version(&entry.observed, checked) {
                    return Err(FolderError::ContentChanged);
                }
                Ok(file)
            },
        )?;
        let _active = self.active.lock().map_err(|_| FolderError::Io)?;
        selection.check_live(cancel)?;
        Ok(frozen)
    }

    /// Recheck every staged pathname and each frozen digest against the live
    /// grant before returning a comparison. Keep the verified handles open
    /// through the last enumeration and the reply boundary.
    pub fn with_verified_refresh_current<T>(
        &self,
        scan: &FolderRefreshScan,
        digests: &[(FileId, String)],
        cancel: &Cancellation,
        finish: impl FnOnce() -> T,
    ) -> Result<T, FolderError> {
        cancel.check()?;
        let _admission = self.admit_open(&self.reading)?;
        let selection = self.selected(scan.selection.id)?;
        if !Arc::ptr_eq(&selection, &scan.selection) {
            return Err(FolderError::SelectionUnavailable);
        }
        let mut pinned = Vec::with_capacity(digests.len());
        for (id, digest) in digests {
            let entry = scan
                .files
                .iter()
                .find(|entry| entry.metadata.file_id == *id)
                .ok_or(FolderError::UnknownFile)?;
            let (mut file, pins) = open_entry(&selection, entry, cancel)?;
            let (_, checked) = read_open_entry(
                &selection,
                entry,
                Some(digest),
                cancel,
                ReadMode::Commit,
                &mut file,
            )?;
            if !same_version(&entry.observed, &checked) {
                return Err(FolderError::ContentChanged);
            }
            pinned.push((file, pins, checked));
        }
        let current = scan_refresh_entries(&selection, &scan.accepted_path, cancel)?;
        if current.len() != scan.files.len()
            || current.iter().zip(&scan.files).any(|(current, staged)| {
                current.relative != staged.relative
                    || current.metadata.kind != staged.metadata.kind
                    || !same_version(&current.observed, &staged.observed)
            })
        {
            return Err(FolderError::ContentChanged);
        }
        self.with_current(selection.id, || {
            for (file, _pins, checked) in &pinned {
                if !same_version(checked, &file.metadata().map_err(|_| FolderError::Io)?) {
                    return Err(FolderError::ContentChanged);
                }
            }
            selection.check_live(cancel)?;
            Ok(finish())
        })?
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
        self.read_frozen(
            folder_id,
            file_id,
            expected_sha256,
            cancel,
            ReadMode::Preview,
        )
    }

    /// A publication already has a reviewed digest within the complete-file
    /// budget. Reject a larger replacement before spending I/O on it.
    pub fn read_for_publication(
        &self,
        folder_id: FolderId,
        file_id: FileId,
        expected_sha256: &str,
        cancel: &Cancellation,
    ) -> Result<FrozenFile, FolderError> {
        self.read_frozen(
            folder_id,
            file_id,
            Some(expected_sha256),
            cancel,
            ReadMode::Publication,
        )
    }

    fn read_frozen(
        &self,
        folder_id: FolderId,
        file_id: FileId,
        expected_sha256: Option<&str>,
        cancel: &Cancellation,
        mode: ReadMode,
    ) -> Result<FrozenFile, FolderError> {
        cancel.check()?;
        let _admission = self.admit_open(&self.reading)?;
        let selection = self.selected(folder_id)?;
        let entry = selection.csv_entry(file_id)?;
        let frozen = read_entry(
            &selection,
            entry,
            expected_sha256,
            cancel,
            mode,
            |file, _| Ok(file),
        )?;
        let _active = self.active.lock().map_err(|_| FolderError::Io)?;
        selection.check_live(cancel)?;
        Ok(frozen)
    }

    /// Recheck the selected entry at the end of a staged transaction. Keep
    /// native handles pinned and order the final commit against grant changes.
    pub fn with_verified_current<T>(
        &self,
        folder_id: FolderId,
        file_id: FileId,
        expected_sha256: &str,
        cancel: &Cancellation,
        commit: impl FnOnce() -> T,
    ) -> Result<T, FolderError> {
        cancel.check()?;
        let _admission = self.admit_open(&self.reading)?;
        let selection = self.selected(folder_id)?;
        let entry = selection.csv_entry(file_id)?;
        read_entry(
            &selection,
            entry,
            Some(expected_sha256),
            cancel,
            ReadMode::Commit,
            |_, checked| {
                self.with_current(folder_id, || {
                    let (current, _pins) = open_entry(&selection, entry, cancel)?;
                    if !same_version(checked, &current.metadata().map_err(|_| FolderError::Io)?) {
                        return Err(FolderError::ContentChanged);
                    }
                    cancel.check()?;
                    Ok(commit())
                })?
            },
        )
    }

    fn selected(&self, folder_id: FolderId) -> Result<Arc<Selection>, FolderError> {
        self.active
            .lock()
            .map_err(|_| FolderError::Io)?
            .as_ref()
            .filter(|selection| selection.id == folder_id)
            .cloned()
            .ok_or(FolderError::SelectionUnavailable)
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum ReadMode {
    Preview,
    Publication,
    Commit,
}

fn source_key_for_path(selection: &Selection, path: &Path) -> String {
    let mut digest = Sha256::new();
    digest.update(b"scopehub-local-mapping-source-v1\0");
    digest.update(selection.source_namespace);
    digest.update(path_fingerprint(b"scopehub-local-relative-file-v1", path));
    format!("{:x}", digest.finalize())
}

fn path_fingerprint(domain: &[u8], path: &Path) -> [u8; 32] {
    let mut digest = Sha256::new();
    digest.update(domain);
    digest.update(b"\0");
    for component in path.components() {
        let name = component.as_os_str();
        #[cfg(unix)]
        let bytes = {
            use std::os::unix::ffi::OsStrExt;
            name.as_bytes()
        };
        #[cfg(windows)]
        let bytes: Vec<u8> = {
            use std::os::windows::ffi::OsStrExt;
            name.encode_wide().flat_map(u16::to_be_bytes).collect()
        };
        #[cfg(not(any(unix, windows)))]
        let bytes = name.as_encoded_bytes();
        digest.update((bytes.len() as u64).to_be_bytes());
        digest.update(bytes);
    }
    digest.finalize().into()
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
    include_csv_directories: bool,
    check: &impl Fn() -> Result<(), FolderError>,
    visited: &mut usize,
    files: &mut Vec<FileEntry>,
) -> Result<(), FolderError> {
    check()?;
    for entry in directory.read_dir().map_err(|_| FolderError::Io)? {
        check()?;
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
            scan(
                &child,
                &relative,
                depth + 1,
                include_csv_directories,
                check,
                visited,
                files,
            )?;
            if !include_csv_directories || !is_csv_path(&relative) {
                continue;
            }
            FileKind::UnsupportedEntry
        } else if !metadata.is_file() {
            FileKind::UnsupportedEntry
        } else if is_csv_path(&relative) {
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
            observed: metadata,
        });
    }
    Ok(())
}

fn is_csv_path(path: &Path) -> bool {
    path.extension()
        .and_then(OsStr::to_str)
        .is_some_and(|extension| extension.eq_ignore_ascii_case("csv"))
}

fn scan_refresh_entries(
    selection: &Selection,
    accepted: &Path,
    cancel: &Cancellation,
) -> Result<Vec<FileEntry>, FolderError> {
    let mut files = Vec::new();
    scan(
        &selection.directory,
        Path::new(""),
        0,
        true,
        &|| selection.check_live(cancel),
        &mut 0,
        &mut files,
    )?;
    // Retain any opaque ancestor that blocks inspection of the accepted path.
    files.retain(|entry| is_csv_path(&entry.relative) || accepted.starts_with(&entry.relative));
    files.sort_unstable_by(|a, b| a.relative.cmp(&b.relative));
    selection.check_live(cancel)?;
    Ok(files)
}

fn open_entry(
    selection: &Selection,
    entry: &FileEntry,
    cancel: &Cancellation,
) -> Result<(File, Option<Directory>), FolderError> {
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
    let file = directory.open_file(name).map_err(open_error)?;
    Ok((file, parent))
}

fn read_entry<T>(
    selection: &Selection,
    entry: &FileEntry,
    expected_sha256: Option<&str>,
    cancel: &Cancellation,
    mode: ReadMode,
    finish: impl FnOnce(FrozenFile, &Metadata) -> Result<T, FolderError>,
) -> Result<T, FolderError> {
    let (mut file, _parent_pins) = open_entry(selection, entry, cancel)?;
    let (frozen, checked) =
        read_open_entry(selection, entry, expected_sha256, cancel, mode, &mut file)?;
    finish(frozen, &checked)
}

fn read_open_entry(
    selection: &Selection,
    entry: &FileEntry,
    expected_sha256: Option<&str>,
    cancel: &Cancellation,
    mode: ReadMode,
    file: &mut File,
) -> Result<(FrozenFile, Metadata), FolderError> {
    let before = file.metadata().map_err(|_| FolderError::Io)?;
    if mode != ReadMode::Preview && before.len() > MAX_PREVIEW_BYTES as u64 {
        return Err(FolderError::ContentChanged);
    }
    let mut digest = Sha256::new();
    let mut content = (mode != ReadMode::Commit && before.len() <= MAX_PREVIEW_BYTES as u64)
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
    Ok((
        FrozenFile {
            folder_id: selection.id,
            file: metadata,
            sha256,
            content: content.map(Arc::from),
        },
        after,
    ))
}

fn open_error(error: io::Error) -> FolderError {
    match error.kind() {
        io::ErrorKind::NotFound => FolderError::ContentChanged,
        io::ErrorKind::InvalidData | io::ErrorKind::InvalidInput => FolderError::UnsupportedFile,
        _ => FolderError::Io,
    }
}
