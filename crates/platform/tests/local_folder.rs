#![cfg(any(target_os = "linux", windows))]

use std::{
    fs::{self, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
};

use scopehub_platform::local_folder::{
    Cancellation, FileKind, FileMetadata, FolderCatalog, FolderError, LocalFolderHost,
    MAX_PREVIEW_BYTES,
};

const EQUITY: &[u8] =
    b"Ticker,Date,Open,High,Low,Close,Shares\n001278,2026-01-06,6.4,6.8,6.3,6.5,218\n";
const EQUITY_SHA256: &str = "197d3743f92569195077b9bb6701c71019777f2c3f3e6b40b21df55d5b267966";
const CORRECTED_EQUITY: &[u8] =
    b"Ticker,Date,Open,High,Low,Close,Shares\n001278,2026-01-06,6.4,6.8,6.3,6.7,218\n";
const CORRECTED_SHA256: &str = "daa286b7fa97e43f393f08db254017f9e6e23119f024a4b46fa84c1b86aadb4f";
const FUTURE: &str = "Contract,LocalStart,TradingDay,Open,High,Low,Close,Contracts,Note\nTEST2603,2026-01-06T21:30,2026-01-07,83,85,82,84,37,夜盘\n";
const FUTURE_SHA256: &str = "607db0e9aba74f4d921d05d334d83687f78656384eaa6fd093f9a4a0a697a704";

struct TempDir(PathBuf);

impl TempDir {
    fn new() -> Self {
        let dir =
            std::env::temp_dir().join(format!("scopehub-local-folder-{}", uuid::Uuid::new_v4()));
        fs::create_dir(&dir).unwrap();
        Self(dir.canonicalize().unwrap())
    }

    fn directory(&self, name: &str) -> PathBuf {
        let path = self.0.join(name);
        fs::create_dir_all(&path).unwrap();
        path
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn write(root: &Path, name: &str, content: &[u8]) -> PathBuf {
    let path = name
        .split('/')
        .fold(root.to_path_buf(), |path, part| path.join(part));
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(&path, content).unwrap();
    path
}

fn file<'a>(catalog: &'a FolderCatalog, relative: &str) -> &'a FileMetadata {
    catalog
        .files
        .iter()
        .find(|entry| entry.relative_path == relative)
        .unwrap()
}

#[cfg(windows)]
#[test]
fn windows_source_association_survives_equivalent_selector_spellings() {
    let temp = TempDir::new();
    let source = temp.directory("Reviewed Source");
    let supplied = std::env::temp_dir()
        .join(temp.0.file_name().unwrap())
        .join("Reviewed Source");
    let differently_cased = supplied.with_file_name("rEVIEWED sOURCE");
    let different = temp.directory("Other Source");
    for directory in [&source, &different] {
        write(directory, "equity.csv", EQUITY);
    }

    let canonical = source.canonicalize().unwrap();
    assert_ne!(differently_cased.file_name(), canonical.file_name());
    assert_eq!(supplied.canonicalize().unwrap(), canonical);
    assert_eq!(differently_cased.canonicalize().unwrap(), canonical);

    let host = LocalFolderHost::default();
    let cancel = Cancellation::default();
    let first = host.adopt_selected(&canonical, &cancel).unwrap();
    let first_file = file(&first, "equity.csv");
    let key = host
        .source_key(first.folder_id, first_file.file_id)
        .unwrap();
    for path in [&supplied, &differently_cased, &different] {
        let selected = host.adopt_selected(path, &cancel).unwrap();
        let csv = file(&selected, "equity.csv");
        assert_ne!(selected.folder_id, first.folder_id);
        assert_ne!(csv.file_id, first_file.file_id);
        let reopened_key = host.source_key(selected.folder_id, csv.file_id).unwrap();
        if path == &different {
            assert_ne!(
                reopened_key, key,
                "identical bytes in another root are a different source"
            );
        } else {
            assert_eq!(
                reopened_key, key,
                "equivalent selector paths share the reviewed source"
            );
        }
        assert_eq!(
            host.read(
                selected.folder_id,
                csv.file_id,
                Some(EQUITY_SHA256),
                &cancel
            )
            .unwrap()
            .content
            .as_deref(),
            Some(EQUITY)
        );
    }
    assert_eq!(
        host.source_key(first.folder_id, first_file.file_id),
        Err(FolderError::SelectionUnavailable)
    );
}

#[cfg(windows)]
#[test]
fn windows_source_association_rejects_a_directory_junction() {
    use std::process::Command;

    let temp = TempDir::new();
    // PowerShell 5.1 expects ordinary drive paths when creating the test junction.
    let root = std::env::temp_dir().join(temp.0.file_name().unwrap());
    let source = root.join("Selected Source");
    write(&source, "equity.csv", EQUITY);
    let junction = root.join("Redirected Source");
    let powershell = PathBuf::from(std::env::var_os("SystemRoot").unwrap())
        .join("System32/WindowsPowerShell/v1.0/powershell.exe");
    let status = Command::new(powershell)
        .args([
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "$ErrorActionPreference = 'Stop'; New-Item -ItemType Junction -Path $env:SCOPEHUB_TEST_JUNCTION -Target $env:SCOPEHUB_TEST_JUNCTION_TARGET | Out-Null",
        ])
        .env("SCOPEHUB_TEST_JUNCTION", &junction)
        .env("SCOPEHUB_TEST_JUNCTION_TARGET", &source)
        .status()
        .unwrap();
    assert!(
        status.success(),
        "creating the synthetic NTFS junction failed"
    );
    assert_eq!(
        junction.canonicalize().unwrap(),
        source.canonicalize().unwrap()
    );

    let host = LocalFolderHost::default();
    let cancel = Cancellation::default();
    let selected = host.adopt_selected(&source, &cancel).unwrap();
    let csv = file(&selected, "equity.csv");
    assert!(matches!(
        host.adopt_selected(&junction, &cancel),
        Err(FolderError::InvalidSelection)
    ));
    assert_eq!(
        host.read(selected.folder_id, csv.file_id, None, &cancel)
            .unwrap()
            .sha256,
        EQUITY_SHA256
    );
    fs::remove_dir(junction).unwrap();
}

#[test]
fn lists_scoped_metadata_and_reads_equity_future_and_byte_identical_alias() {
    let temp = TempDir::new();
    let chosen = temp.directory("selected");
    write(&chosen, "derivatives/night.CSV", FUTURE.as_bytes());
    write(&chosen, "notes.txt", b"Unmapped example\n");
    write(&chosen, "copies/replica.csv", EQUITY);
    write(&chosen, "cash/001278.csv", EQUITY);
    write(&temp.0, "outside.csv", b"Outside this selection\n");

    let host = LocalFolderHost::default();
    let cancellation = Cancellation::default();
    let selected = host.adopt_selected(&chosen, &cancellation).unwrap();
    assert_eq!(selected.display_name, "selected");
    assert_eq!(
        selected
            .files
            .iter()
            .map(|entry| entry.relative_path.as_str())
            .collect::<Vec<_>>(),
        [
            "cash/001278.csv",
            "copies/replica.csv",
            "derivatives/night.CSV",
            "notes.txt"
        ],
    );

    let equity = file(&selected, "cash/001278.csv");
    let alias = file(&selected, "copies/replica.csv");
    let future = file(&selected, "derivatives/night.CSV");
    assert_eq!(equity.display_name, "001278.csv");
    assert_eq!(equity.kind, FileKind::Csv);
    assert_eq!(equity.byte_count, EQUITY.len() as u64);
    assert_ne!(equity.file_id, alias.file_id);
    assert_eq!(
        file(
            &host.catalog(selected.folder_id).unwrap(),
            "cash/001278.csv"
        )
        .file_id,
        equity.file_id
    );

    for (entry, expected_digest, expected_bytes) in [
        (equity, EQUITY_SHA256, EQUITY),
        (alias, EQUITY_SHA256, EQUITY),
        (future, FUTURE_SHA256, FUTURE.as_bytes()),
    ] {
        let read = host
            .read(
                selected.folder_id,
                entry.file_id,
                Some(expected_digest),
                &cancellation,
            )
            .unwrap();
        assert_eq!(read.folder_id, selected.folder_id);
        assert_eq!(read.file.file_id, entry.file_id);
        assert_eq!(read.file.relative_path, entry.relative_path);
        assert_eq!(read.sha256, expected_digest);
        assert_eq!(read.content.as_deref(), Some(expected_bytes));
    }

    let unsupported = file(&selected, "notes.txt");
    assert_eq!(unsupported.kind, FileKind::UnsupportedExtension);
    assert!(matches!(
        host.read(selected.folder_id, unsupported.file_id, None, &cancellation),
        Err(FolderError::UnsupportedFile)
    ));
}

#[test]
fn identifies_same_size_edits_without_rewriting_an_existing_frozen_read() {
    let temp = TempDir::new();
    let source = write(&temp.0, "equity.csv", EQUITY);
    let host = LocalFolderHost::default();
    let cancellation = Cancellation::default();
    let selected = host.adopt_selected(&temp.0, &cancellation).unwrap();
    let file_id = file(&selected, "equity.csv").file_id;
    let original = host
        .read(selected.folder_id, file_id, None, &cancellation)
        .unwrap();

    assert_eq!(original.sha256, EQUITY_SHA256);
    assert_eq!(EQUITY.len(), CORRECTED_EQUITY.len());
    fs::write(&source, CORRECTED_EQUITY).unwrap();
    assert!(matches!(
        host.read(
            selected.folder_id,
            file_id,
            Some(&original.sha256),
            &cancellation
        ),
        Err(FolderError::ContentChanged)
    ));

    let corrected = host
        .read(selected.folder_id, file_id, None, &cancellation)
        .unwrap();
    assert_eq!(corrected.sha256, CORRECTED_SHA256);
    assert_eq!(corrected.content.as_deref(), Some(CORRECTED_EQUITY));
    assert_eq!(original.sha256, EQUITY_SHA256);
    assert_eq!(original.content.as_deref(), Some(EQUITY));
}

#[test]
fn cancelled_requests_leave_the_previous_selection_available() {
    let temp = TempDir::new();
    let first = temp.directory("first");
    let second = temp.directory("second");
    write(&first, "equity.csv", EQUITY);
    write(&second, "future.csv", FUTURE.as_bytes());
    let host = LocalFolderHost::default();
    let cancellation = Cancellation::default();
    let selected = host.adopt_selected(&first, &cancellation).unwrap();
    let file_id = file(&selected, "equity.csv").file_id;

    let cancelled = Cancellation::default();
    cancelled.cancel();
    assert!(matches!(
        host.adopt_selected(&second, &cancelled),
        Err(FolderError::Cancelled)
    ));
    assert!(matches!(
        host.read(selected.folder_id, file_id, None, &cancelled),
        Err(FolderError::Cancelled)
    ));
    assert_eq!(
        host.catalog(selected.folder_id).unwrap().folder_id,
        selected.folder_id
    );
    assert_eq!(
        host.read(selected.folder_id, file_id, None, &cancellation)
            .unwrap()
            .sha256,
        EQUITY_SHA256,
    );
}

#[test]
fn switching_and_revoking_selections_expire_ids_but_keep_frozen_values() {
    let temp = TempDir::new();
    let first = temp.directory("first");
    let second = temp.directory("second");
    write(&first, "same.csv", EQUITY);
    write(&second, "same.csv", FUTURE.as_bytes());
    let host = LocalFolderHost::default();
    let cancellation = Cancellation::default();

    let first_selection = host.adopt_selected(&first, &cancellation).unwrap();
    let first_id = file(&first_selection, "same.csv").file_id;
    let first_frozen = host
        .read(first_selection.folder_id, first_id, None, &cancellation)
        .unwrap();
    let second_selection = host.adopt_selected(&second, &cancellation).unwrap();
    let second_id = file(&second_selection, "same.csv").file_id;
    assert_ne!(first_selection.folder_id, second_selection.folder_id);
    assert_ne!(first_id, second_id);
    assert!(matches!(
        host.catalog(first_selection.folder_id),
        Err(FolderError::SelectionUnavailable)
    ));
    assert!(matches!(
        host.read(first_selection.folder_id, first_id, None, &cancellation),
        Err(FolderError::SelectionUnavailable)
    ));
    assert!(matches!(
        host.read(second_selection.folder_id, first_id, None, &cancellation),
        Err(FolderError::UnknownFile)
    ));

    let second_frozen = host
        .read(second_selection.folder_id, second_id, None, &cancellation)
        .unwrap();
    host.revoke(second_selection.folder_id).unwrap();
    assert!(matches!(
        host.catalog(second_selection.folder_id),
        Err(FolderError::SelectionUnavailable)
    ));
    assert_eq!(second_frozen.sha256, FUTURE_SHA256);
    assert_eq!(first_frozen.content.as_deref(), Some(EQUITY));
}

#[test]
fn shutdown_revokes_the_selection_and_closes_future_adoptions() {
    let temp = TempDir::new();
    let selected_path = temp.directory("selected");
    write(&selected_path, "equity.csv", EQUITY);
    let host = LocalFolderHost::default();
    let cancel = Cancellation::default();
    let catalog = host.adopt_selected(&selected_path, &cancel).unwrap();
    let file_id = file(&catalog, "equity.csv").file_id;
    let frozen = host
        .read(catalog.folder_id, file_id, None, &cancel)
        .unwrap();

    #[cfg(target_os = "linux")]
    assert!(directory_is_pinned(&selected_path));

    host.shutdown();
    host.shutdown();

    assert_eq!(
        host.check_current(catalog.folder_id),
        Err(FolderError::SelectionUnavailable)
    );
    assert!(matches!(
        host.catalog(catalog.folder_id),
        Err(FolderError::SelectionUnavailable)
    ));
    assert!(matches!(
        host.read(catalog.folder_id, file_id, None, &cancel),
        Err(FolderError::SelectionUnavailable)
    ));
    assert!(matches!(
        host.adopt_selected(&selected_path, &cancel),
        Err(FolderError::SelectionUnavailable)
    ));
    assert_eq!(frozen.content.as_deref(), Some(EQUITY));
    #[cfg(target_os = "linux")]
    assert!(!directory_is_pinned(&selected_path));
    fs::rename(&selected_path, temp.0.join("released")).unwrap();
}

#[cfg(target_os = "linux")]
fn directory_is_pinned(path: &Path) -> bool {
    fs::read_dir("/proc/self/fd")
        .unwrap()
        .filter_map(Result::ok)
        .any(|entry| fs::read_link(entry.path()).is_ok_and(|target| target == path))
}

#[test]
fn shutdown_does_not_leave_a_concurrently_adopted_selection_live() {
    let temp = TempDir::new();
    let selected_path = temp.directory("selected");
    write(&selected_path, "equity.csv", EQUITY);
    let host = LocalFolderHost::default();
    let cancel = Cancellation::default();
    let catalog = host.adopt_selected(&selected_path, &cancel).unwrap();
    let start = std::sync::Barrier::new(2);

    let attempted = std::thread::scope(|scope| {
        let adoption = scope.spawn(|| {
            start.wait();
            host.adopt_selected(&selected_path, &cancel)
        });
        start.wait();
        host.shutdown();
        adoption.join().unwrap()
    });
    match attempted {
        Ok(adopted) => assert_eq!(
            host.check_current(adopted.folder_id),
            Err(FolderError::SelectionUnavailable)
        ),
        Err(error) => assert_eq!(error, FolderError::SelectionUnavailable),
    }
    assert_eq!(
        host.check_current(catalog.folder_id),
        Err(FolderError::SelectionUnavailable)
    );
    #[cfg(target_os = "linux")]
    assert!(!directory_is_pinned(&selected_path));
}

#[test]
fn calculates_digest_to_eof_even_when_no_complete_preview_can_be_retained() {
    let temp = TempDir::new();
    let retained = vec![b'A'; MAX_PREVIEW_BYTES];
    let source = write(&temp.0, "bulk.csv", &retained);
    let host = LocalFolderHost::default();
    let cancellation = Cancellation::default();
    let selected = host.adopt_selected(&temp.0, &cancellation).unwrap();
    let file_id = file(&selected, "bulk.csv").file_id;
    let fits = host
        .read(selected.folder_id, file_id, None, &cancellation)
        .unwrap();
    assert_eq!(fits.content.as_deref(), Some(retained.as_slice()));

    let tail = b"final record\n";
    OpenOptions::new()
        .append(true)
        .open(&source)
        .unwrap()
        .write_all(tail)
        .unwrap();
    let full = host
        .read(selected.folder_id, file_id, None, &cancellation)
        .unwrap();
    assert_eq!(
        full.file.byte_count,
        (MAX_PREVIEW_BYTES + tail.len()) as u64
    );
    assert_eq!(
        full.sha256,
        "5dc6df4ecc6a110b1218ae7b2aed723c0a80714eafa1a26f5d4d10d60151d799"
    );
    assert!(full.content.is_none());
    assert_eq!(fits.content.as_deref(), Some(retained.as_slice()));
}

#[cfg(target_os = "linux")]
#[test]
fn never_follows_catalogued_or_late_replacement_symlinks() {
    use std::os::unix::fs::symlink;

    let temp = TempDir::new();
    let chosen = temp.directory("selected");
    let outside = temp.directory("outside");
    let target = write(&outside, "unselected.csv", FUTURE.as_bytes());
    write(&outside, "retarget.csv", FUTURE.as_bytes());
    let ordinary = write(&chosen, "ordinary.csv", EQUITY);
    write(&chosen, "branch/retarget.csv", EQUITY);
    symlink(&outside, chosen.join("outside-dir")).unwrap();
    symlink(&target, chosen.join("outside-file.csv")).unwrap();

    let host = LocalFolderHost::default();
    let cancellation = Cancellation::default();
    let selected = host.adopt_selected(&chosen, &cancellation).unwrap();
    assert_eq!(selected.files.len(), 4);
    for redirect in ["outside-dir", "outside-file.csv"] {
        let entry = file(&selected, redirect);
        assert_eq!(entry.kind, FileKind::UnsupportedEntry);
        assert!(matches!(
            host.read(selected.folder_id, entry.file_id, None, &cancellation),
            Err(FolderError::UnsupportedFile)
        ));
    }

    fs::remove_file(&ordinary).unwrap();
    symlink(&target, &ordinary).unwrap();
    let previous_file = file(&selected, "ordinary.csv");
    assert!(
        host.read(
            selected.folder_id,
            previous_file.file_id,
            None,
            &cancellation
        )
        .is_err()
    );

    let replaced_branch = chosen.join("branch");
    fs::remove_dir_all(&replaced_branch).unwrap();
    symlink(&outside, &replaced_branch).unwrap();
    let previous_child = file(&selected, "branch/retarget.csv");
    assert!(
        host.read(
            selected.folder_id,
            previous_child.file_id,
            None,
            &cancellation,
        )
        .is_err()
    );
    assert!(
        host.adopt_selected(&chosen.join("outside-dir"), &cancellation)
            .is_err()
    );
    assert_eq!(
        host.catalog(selected.folder_id).unwrap().folder_id,
        selected.folder_id
    );
}
