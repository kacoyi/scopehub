//! Compose the local human's filesystem selection with the private preview worker.

use std::{
    future::Future,
    path::PathBuf,
    sync::{Arc, Mutex as SyncMutex},
    time::Duration,
};

use scopehub_application::{
    Actor, DEFAULT_PREVIEW_ROWS, LocalImportPort, MarketFuture, failure, preview_local_import,
};
use scopehub_domain::protocol::{Error, ErrorCode, LocalImportPreviewInput};
use scopehub_platform::local_folder::{
    Cancellation, FileId, FileKind, FolderCatalog, FolderError, FolderId, LocalFolderHost,
    MAX_PREVIEW_BYTES,
};
use scopehub_worker_host::{local_import, market::WorkerConfig};
use serde_json::{Value, json};
use tokio::sync::{Mutex, MutexGuard, Notify};

pub(super) struct LocalImport {
    pub(super) folders: Arc<LocalFolderHost>,
    config: Option<WorkerConfig>,
    picker: Mutex<()>,
    admission: Mutex<()>,
    state: SyncMutex<PreviewState>,
}

#[derive(Default)]
struct PreviewState {
    closing: bool,
    active: Option<(Option<String>, Arc<PreviewCancellation>)>,
}

#[derive(Default)]
struct PreviewCancellation {
    file: Cancellation,
    notify: Notify,
}

impl PreviewCancellation {
    fn cancel(&self) {
        self.file.cancel();
        self.notify.notify_one();
    }

    async fn cancelled(&self) {
        if !self.file.is_cancelled() {
            self.notify.notified().await;
        }
    }
}

struct PreviewSlot<'a> {
    _admission: MutexGuard<'a, ()>,
    state: &'a SyncMutex<PreviewState>,
}

impl Drop for PreviewSlot<'_> {
    fn drop(&mut self) {
        if let Some((_, cancel)) = self.state.lock().expect("preview state").active.take() {
            // Also interrupt an owned file read if its transport future was dropped.
            cancel.cancel();
        }
    }
}

impl LocalImport {
    pub(super) fn new(config: Option<WorkerConfig>) -> Self {
        Self {
            folders: Arc::default(),
            config,
            picker: Mutex::new(()),
            admission: Mutex::new(()),
            state: SyncMutex::default(),
        }
    }

    pub(super) async fn choose_folder<F>(
        &self,
        pick: impl FnOnce() -> F,
    ) -> Result<Option<Value>, Error>
    where
        F: Future<Output = Option<PathBuf>>,
    {
        let _picker = self
            .picker
            .try_lock()
            .map_err(|_| failure(ErrorCode::BUSY))?;
        self.check_picker_available()?;
        let Some(root) = pick().await else {
            return Ok(None);
        };
        self.check_picker_available()?;
        let folders = Arc::clone(&self.folders);
        let catalog = tokio::task::spawn_blocking(move || {
            folders.adopt_selected(&root, &Cancellation::default())
        })
        .await
        .map_err(|_| failure(ErrorCode::INTERNALERROR))?
        .map_err(folder_failure)?;
        self.folders
            .check_current(catalog.folder_id)
            .map_err(folder_failure)?;
        Ok(Some(catalog_metadata(catalog)))
    }

    fn check_picker_available(&self) -> Result<(), Error> {
        if self.state.lock().expect("preview state").closing {
            return Err(failure(ErrorCode::CANCELLED));
        }
        if self.config.is_none() {
            return Err(failure(ErrorCode::PROVIDERNOTCONFIGURED));
        }
        Ok(())
    }

    pub(super) async fn preview(
        &self,
        payload: Value,
        request_id: Option<&str>,
    ) -> Result<Value, Error> {
        if request_id.is_some_and(|id| {
            id.is_empty()
                || id.len() > 64
                || !id.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-')
        }) {
            return Err(failure(ErrorCode::INVALIDARGUMENT));
        }
        let admission = self
            .admission
            .try_lock()
            .map_err(|_| failure(ErrorCode::BUSY))?;
        let cancel = Arc::new(PreviewCancellation::default());
        {
            let mut state = self.state.lock().expect("preview state");
            if state.closing {
                return Err(failure(ErrorCode::CANCELLED));
            }
            state.active = Some((request_id.map(str::to_owned), Arc::clone(&cancel)));
        }
        let _slot = PreviewSlot {
            _admission: admission,
            state: &self.state,
        };
        let mut port = PreviewPort { host: self, cancel };
        preview_local_import(Actor::LocalUser, payload, &mut port).await
    }

    pub(super) fn cancel(&self, request_id: &str) {
        let state = self.state.lock().expect("preview state");
        if let Some((Some(id), cancel)) = &state.active
            && id == request_id
        {
            cancel.cancel();
        }
    }

    pub(super) async fn shutdown(&self) {
        {
            let mut state = self.state.lock().expect("preview state");
            state.closing = true;
            if let Some((_, cancel)) = &state.active {
                cancel.cancel();
            }
        }
        self.folders.begin_shutdown();
        let folders = Arc::clone(&self.folders);
        let release = tokio::task::spawn_blocking(move || folders.shutdown());
        let _admission = self.admission.lock().await;
        release.await.expect("local folder shutdown");
    }
}

fn catalog_metadata(catalog: FolderCatalog) -> Value {
    let files: Vec<_> = catalog
        .files
        .into_iter()
        .map(|file| {
            let kind = match file.kind {
                FileKind::Csv => "csv",
                FileKind::UnsupportedExtension => "unsupported_extension",
                FileKind::UnsupportedEntry => "unsupported_entry",
            };
            json!({
                "file_id": file.file_id.to_string(),
                "relative_path": file.relative_path,
                "display_name": file.display_name,
                "byte_count": file.byte_count,
                "kind": kind,
            })
        })
        .collect();
    json!({
        "folder_id": catalog.folder_id.to_string(),
        "display_name": catalog.display_name,
        "files": files,
    })
}

struct PreviewPort<'a> {
    host: &'a LocalImport,
    cancel: Arc<PreviewCancellation>,
}

impl PreviewPort<'_> {
    fn check_live(&self, folder: FolderId) -> Result<(), Error> {
        if self.cancel.file.is_cancelled() {
            return Err(failure(ErrorCode::CANCELLED));
        }
        self.host
            .folders
            .check_current(folder)
            .map_err(folder_failure)
    }

    async fn stopped(&self, folder: FolderId) {
        let selection_changed = async {
            while self.host.folders.check_current(folder).is_ok() {
                tokio::time::sleep(Duration::from_millis(25)).await;
            }
        };
        tokio::select! {
            () = self.cancel.cancelled() => {},
            () = selection_changed => {},
        }
    }

    async fn read_preview(&self, request: &LocalImportPreviewInput) -> Result<Value, Error> {
        let folder = folder_id(request)?;
        let file: FileId = request
            .file_id
            .parse()
            .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
        if file.to_string() != request.file_id {
            return Err(failure(ErrorCode::INVALIDARGUMENT));
        }
        let expected_sha256 = request.expected_sha256.clone();
        let config = self
            .host
            .config
            .as_ref()
            .ok_or_else(|| failure(ErrorCode::PROVIDERNOTCONFIGURED))?;

        let folders = Arc::clone(&self.host.folders);
        let cancel = Arc::clone(&self.cancel);
        let read = tokio::task::spawn_blocking(move || {
            folders.read(folder, file, expected_sha256.as_deref(), &cancel.file)
        });
        let frozen = tokio::select! {
            biased;
            () = self.stopped(folder) => {
                self.check_live(folder)?;
                return Err(failure(ErrorCode::CANCELLED));
            }
            result = read => result.map_err(|_| failure(ErrorCode::INTERNALERROR))?.map_err(folder_failure)?,
        };
        self.check_live(folder)?;
        let bytes = frozen.content.as_deref().ok_or_else(|| {
            let mut error = failure(ErrorCode::RESOURCELIMIT);
            error.message =
                "This preview supports source files up to 4 MiB. Select a smaller CSV file.".into();
            error
                .details
                .insert("max_preview_bytes".into(), json!(MAX_PREVIEW_BYTES));
            error
                .details
                .insert("byte_count".into(), json!(frozen.file.byte_count));
            error.details.insert("sha256".into(), json!(frozen.sha256));
            error
        })?;
        let mapping = serde_json::to_value(&request.mapping)
            .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
        let row_limit = request.row_limit.unwrap_or(u64::from(DEFAULT_PREVIEW_ROWS)) as u32;
        let preview = local_import::preview(
            config,
            bytes,
            &frozen.sha256,
            &mapping,
            row_limit,
            self.stopped(folder),
        )
        .await;
        self.check_live(folder)?;
        let preview = preview.map_err(|mut error| {
            if error.code == ErrorCode::RESOURCELIMIT {
                error.message = "The preview exceeds the response budget. Request fewer rows or select a smaller CSV file.".into();
            }
            error
        })?;
        Ok(json!({
            "version": 1,
            "folder_id": frozen.folder_id.to_string(),
            "file_id": frozen.file.file_id.to_string(),
            "relative_path": frozen.file.relative_path,
            "display_name": frozen.file.display_name,
            "byte_count": frozen.file.byte_count,
            "sha256": frozen.sha256,
            "preview": preview,
        }))
    }
}

impl LocalImportPort for PreviewPort<'_> {
    fn ensure_live(&self, input: &LocalImportPreviewInput) -> Result<(), Error> {
        self.check_live(folder_id(input)?)
    }

    fn preview<'a>(&'a mut self, input: &'a LocalImportPreviewInput) -> MarketFuture<'a> {
        Box::pin(self.read_preview(input))
    }
}

fn folder_id(input: &LocalImportPreviewInput) -> Result<FolderId, Error> {
    let id: FolderId = input
        .folder_id
        .parse()
        .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
    if id.to_string() != input.folder_id {
        return Err(failure(ErrorCode::INVALIDARGUMENT));
    }
    Ok(id)
}

fn folder_failure(error: FolderError) -> Error {
    failure(match error {
        FolderError::Cancelled => ErrorCode::CANCELLED,
        FolderError::SelectionUnavailable => ErrorCode::STALECONTEXT,
        FolderError::UnknownFile => ErrorCode::PERMISSIONDENIED,
        FolderError::UnsupportedFile => ErrorCode::UNSUPPORTEDCAPABILITY,
        FolderError::ContentChanged => ErrorCode::SNAPSHOTUNAVAILABLE,
        FolderError::InvalidSelection | FolderError::Io => ErrorCode::DATAUNAVAILABLE,
        FolderError::CatalogLimit => ErrorCode::RESOURCELIMIT,
        FolderError::Busy => ErrorCode::BUSY,
    })
}
