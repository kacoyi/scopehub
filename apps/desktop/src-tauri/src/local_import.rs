//! Compose the local human's filesystem selection with the private preview worker.

mod folder_refresh;
mod requests;

use std::{
    collections::VecDeque,
    future::Future,
    path::PathBuf,
    sync::{
        Arc, Mutex as SyncMutex,
        atomic::{AtomicU8, Ordering},
    },
    time::Duration,
};

use scopehub_application::{
    Actor, DEFAULT_PREVIEW_ROWS, FrozenLocalSourceRow, LocalImportPort, MappingCommitGuard,
    MarketFuture, failure, load_mapping_input, load_mapping_output, preview_local_import,
    publish_local_import_input, read_local_folder_source_row_input,
    read_local_import_source_row_input, reviewed_preview_header, save_mapping_input,
};
use scopehub_domain::protocol::{CsvMappingV1, Error, ErrorCode, LocalImportPreviewInput};
use scopehub_platform::local_folder::{
    Cancellation, FileId, FileKind, FolderCatalog, FolderError, FolderId, FrozenFile,
    LocalFolderHost, MAX_PREVIEW_BYTES,
};
use scopehub_worker_host::{local_import, market::WorkerConfig};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use tokio::sync::{Mutex, MutexGuard, Notify};

use crate::workspace::WorkspaceBridge;
pub(crate) use folder_refresh::{FolderCommitGuard, PreparedFolderReview};
use requests::{ImportAttempt, ImportRequests, MutationInput};
pub(super) use requests::{ImportReceipt, PendingImport};

const MAX_EARLY_CANCELLATIONS: usize = 64;
const ACTIVE: u8 = 0;
const CANCELLED: u8 = 1;
const COMMITTING: u8 = 2;

pub(super) struct LocalImport {
    pub(super) folders: Arc<LocalFolderHost>,
    config: Option<WorkerConfig>,
    picker: Mutex<()>,
    admission: Mutex<()>,
    state: SyncMutex<PreviewState>,
    requests: ImportRequests,
}

#[derive(Default)]
struct PreviewState {
    closing: bool,
    active: Option<(Option<String>, Arc<PreviewCancellation>)>,
    early_cancellations: VecDeque<String>,
    reviewed: Option<ReviewedPreview>,
    reviewed_folder: Option<folder_refresh::FolderReview>,
}

struct ReviewedPreview {
    binding: PreviewBinding,
    header_sha256: String,
}

#[derive(Clone, PartialEq, Eq)]
struct PreviewBinding {
    folder: FolderId,
    file: FileId,
    source_sha256: String,
    mapping_sha256: [u8; 32],
    row_limit: u64,
}

impl PreviewBinding {
    fn new(
        folder: FolderId,
        file: FileId,
        source_sha256: String,
        mapping: &CsvMappingV1,
        row_limit: Option<u64>,
    ) -> Result<Self, Error> {
        let bytes = serde_json::to_vec(mapping).map_err(|_| failure(ErrorCode::INTERNALERROR))?;
        Ok(Self {
            folder,
            file,
            source_sha256,
            mapping_sha256: Sha256::digest(bytes).into(),
            row_limit: row_limit.unwrap_or(u64::from(DEFAULT_PREVIEW_ROWS)),
        })
    }
}

#[derive(Default)]
struct PreviewCancellation {
    file: Cancellation,
    notify: Notify,
    phase: AtomicU8,
}

impl PreviewCancellation {
    fn cancel(&self) {
        if self
            .phase
            .compare_exchange(ACTIVE, CANCELLED, Ordering::AcqRel, Ordering::Acquire)
            .is_ok()
        {
            self.file.cancel();
            self.notify.notify_one();
        }
    }

    fn is_cancelled(&self) -> bool {
        self.phase.load(Ordering::Acquire) == CANCELLED
    }

    async fn cancelled(&self) {
        if !self.is_cancelled() {
            self.notify.notified().await;
        }
    }
}

struct MappingGuard {
    cancel: Arc<PreviewCancellation>,
    folders: Arc<LocalFolderHost>,
    folder: FolderId,
    file: FileId,
    source_sha256: String,
}

impl MappingCommitGuard for MappingGuard {
    fn commit<T>(&self, apply: impl FnOnce() -> Result<T, Error>) -> Result<T, Error> {
        let apply = || {
            self.cancel
                .phase
                .compare_exchange(ACTIVE, COMMITTING, Ordering::AcqRel, Ordering::Acquire)
                .map_err(|_| failure(ErrorCode::CANCELLED))?;
            apply()
        };
        self.folders
            .with_verified_current(
                self.folder,
                self.file,
                &self.source_sha256,
                &self.cancel.file,
                apply,
            )
            .map_err(folder_failure)?
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
            requests: ImportRequests::default(),
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
        let mut state = self.state.lock().expect("preview state");
        self.folders
            .check_current(catalog.folder_id)
            .map_err(folder_failure)?;
        state
            .reviewed
            .take_if(|review| review.binding.folder != catalog.folder_id);
        state
            .reviewed_folder
            .take_if(|review| review.folder() != catalog.folder_id);
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

    fn reserve(
        &self,
        request_id: Option<&str>,
    ) -> Result<(PreviewSlot<'_>, PreviewPort<'_>), Error> {
        self.reserve_inner(request_id, false)
    }

    fn reserve_for_receipt(
        &self,
        request_id: Option<&str>,
    ) -> Result<(PreviewSlot<'_>, PreviewPort<'_>), Error> {
        self.reserve_inner(request_id, true)
    }

    fn reserve_inner(
        &self,
        request_id: Option<&str>,
        check_durable_receipt: bool,
    ) -> Result<(PreviewSlot<'_>, PreviewPort<'_>), Error> {
        if request_id.is_some_and(|id| !valid_request_id(id)) {
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
            if let Some(index) = state
                .early_cancellations
                .iter()
                .position(|id| Some(id.as_str()) == request_id)
            {
                state.early_cancellations.remove(index);
                // Cancellation can arrive after a dropped transport future and
                // before its blocking writer records the successful commit.
                if !request_id.is_some_and(|id| self.requests.committed(id)) {
                    if !check_durable_receipt {
                        return Err(failure(ErrorCode::CANCELLED));
                    }
                    // A persistent receipt may already exist after a host restart.
                    // Keep cancellation effective for the new-work path.
                    cancel.cancel();
                }
            }
            state.active = Some((request_id.map(str::to_owned), Arc::clone(&cancel)));
        }
        let slot = PreviewSlot {
            _admission: admission,
            state: &self.state,
        };
        Ok((slot, PreviewPort { host: self, cancel }))
    }

    pub(super) async fn preview(
        &self,
        payload: Value,
        request_id: Option<&str>,
    ) -> Result<Value, Error> {
        let (_slot, mut port) = self.reserve(request_id)?;
        self.requests.reject_reused(request_id)?;
        let (input, output) = preview_local_import(Actor::LocalUser, payload, &mut port).await?;
        if let Some(header_sha256) = reviewed_preview_header(&input, &output) {
            let folder = folder_id(&input.folder_id)?;
            let binding = PreviewBinding::new(
                folder,
                file_id(&input.file_id)?,
                output["sha256"]
                    .as_str()
                    .expect("validated source hash")
                    .to_owned(),
                &input.mapping,
                input.row_limit,
            )?;
            let mut state = self.state.lock().expect("preview state");
            port.check_live(folder)?;
            // A failed or cancelled attempt leaves the previous completed review available.
            state.reviewed = Some(ReviewedPreview {
                binding,
                header_sha256,
            });
        }
        Ok(output)
    }

    pub(super) async fn save_mapping(
        &self,
        workspace: &WorkspaceBridge,
        payload: Value,
        request_id: Option<&str>,
    ) -> Result<Value, Error> {
        let input = save_mapping_input(Actor::LocalUser, payload)?;
        let (_slot, port) = self.reserve(request_id)?;
        let folder = folder_id(&input.folder_id)?;
        let file = file_id(&input.file_id)?;
        let source_key = self
            .folders
            .source_key(folder, file)
            .map_err(folder_failure)?;
        let binding = PreviewBinding::new(
            folder,
            file,
            input.expected_sha256,
            &input.mapping,
            input.row_limit,
        )?;
        let attempt = match self
            .requests
            .start(request_id, MutationInput::Save(binding.clone()))?
        {
            ImportAttempt::New(attempt) => attempt,
            ImportAttempt::Committed(ImportReceipt::Mapping(revision)) => {
                let result = workspace.replay_saved_mapping(source_key, revision).await;
                port.check_live(folder)?;
                return result;
            }
            ImportAttempt::Committed(_) => return Err(failure(ErrorCode::INTERNALERROR)),
        };
        let header = self
            .state
            .lock()
            .expect("preview state")
            .reviewed
            .as_ref()
            .filter(|review| review.binding == binding)
            .map(|review| review.header_sha256.clone())
            .ok_or_else(|| {
                let mut error = failure(ErrorCode::INVALIDARGUMENT);
                error.message =
                    "Preview this mapping and review a normalized bar before saving.".into();
                error
            })?;
        port.read_file(
            folder,
            file,
            FileRead::Checked(binding.source_sha256.clone()),
        )
        .await?;
        let guard = port.commit_guard(folder, file, binding.source_sha256);
        workspace
            .save(source_key, header, input.mapping, guard, attempt)
            .await
    }

    pub(super) async fn load_mapping(
        &self,
        workspace: &WorkspaceBridge,
        payload: Value,
        request_id: Option<&str>,
    ) -> Result<Value, Error> {
        let input = load_mapping_input(Actor::LocalUser, payload)?;
        let (_slot, mut port) = self.reserve(request_id)?;
        self.requests.reject_reused(request_id)?;
        let folder = folder_id(&input.folder_id)?;
        let file = file_id(&input.file_id)?;
        let source_key = self
            .folders
            .source_key(folder, file)
            .map_err(folder_failure)?;
        let record = workspace.find(source_key).await?;
        port.check_live(folder)?;
        let Some(record) = record else {
            return load_mapping_output(Actor::LocalUser, None, None);
        };
        let mut mapping = record.mapping.clone();
        // Inspect the current header without applying any of the saved field roles.
        mapping.record_kind = "unclassified".into();
        let (_, preview) = preview_local_import(
            Actor::LocalUser,
            json!({
                "version": 1,
                "folder_id": input.folder_id,
                "file_id": input.file_id,
                "mapping": mapping,
                "row_limit": 1,
            }),
            &mut port,
        )
        .await?;
        let output = load_mapping_output(Actor::LocalUser, Some(record), Some(&preview));
        port.check_live(folder)?;
        output
    }

    pub(super) async fn publish(
        &self,
        workspace: &WorkspaceBridge,
        payload: Value,
        request_id: Option<&str>,
    ) -> Result<Value, Error> {
        let input = publish_local_import_input(Actor::LocalUser, payload)?;
        let (_slot, port) = self.reserve(request_id)?;
        let folder = folder_id(&input.folder_id)?;
        let file = file_id(&input.file_id)?;
        let source_key = self
            .folders
            .source_key(folder, file)
            .map_err(folder_failure)?;
        let attempt = match self
            .requests
            .start(request_id, MutationInput::Publish(input.clone()))?
        {
            ImportAttempt::New(attempt) => attempt,
            ImportAttempt::Committed(ImportReceipt::Publication(revision_id)) => {
                let result = workspace.replay_publication(revision_id).await;
                port.check_live(folder)?;
                return result;
            }
            ImportAttempt::Committed(_) => return Err(failure(ErrorCode::INTERNALERROR)),
        };
        let reviewed = workspace
            .find(source_key.clone())
            .await?
            .filter(|record| record.review_revision == input.review_revision)
            .ok_or_else(|| {
                let mut error = failure(ErrorCode::STALECONTEXT);
                error.message = "Load the current saved mapping before publishing this CSV.".into();
                error
            })?;
        let binding = PreviewBinding::new(
            folder,
            file,
            input.expected_sha256.clone(),
            &reviewed.mapping,
            None,
        )?;
        let previewed = self
            .state
            .lock()
            .expect("preview state")
            .reviewed
            .as_ref()
            .is_some_and(|review| {
                let accepted = &review.binding;
                accepted.folder == binding.folder
                    && accepted.file == binding.file
                    && accepted.source_sha256 == binding.source_sha256
                    && accepted.mapping_sha256 == binding.mapping_sha256
                    && review.header_sha256 == reviewed.header_sha256
            });
        if !previewed {
            let mut error = failure(ErrorCode::STALECONTEXT);
            error.message =
                "Preview the current CSV with its saved mapping before publishing.".into();
            return Err(error);
        }
        let frozen = port
            .read_file(folder, file, FileRead::Checked(input.expected_sha256))
            .await?;
        let original = frozen.content.as_deref().ok_or_else(|| {
            let mut error = failure(ErrorCode::RESOURCELIMIT);
            error.message = "Publication supports CSV files up to 4 MiB.".into();
            error
        })?;
        let config = self
            .config
            .as_ref()
            .ok_or_else(|| failure(ErrorCode::PROVIDERNOTCONFIGURED))?;
        let mapping = serde_json::to_value(&reviewed.mapping)
            .map_err(|_| failure(ErrorCode::INTERNALERROR))?;
        let content = local_import::complete(
            config,
            original,
            &frozen.sha256,
            &mapping,
            port.stopped(folder),
        )
        .await;
        port.check_live(folder)?;
        let guard = port.commit_guard(folder, file, frozen.sha256.clone());
        workspace
            .publish(source_key, frozen, reviewed, content?, guard, attempt)
            .await
    }

    pub(super) fn reject_reused_request(&self, id: Option<&str>) -> Result<(), Error> {
        self.requests.reject_reused(id)
    }

    pub(super) async fn read_frozen_source_row(
        &self,
        workspace: &WorkspaceBridge,
        payload: Value,
        request_id: Option<&str>,
    ) -> Result<Value, Error> {
        let input = read_local_import_source_row_input(Actor::LocalUser, payload)?;
        let record_number = input.locator.record_number;
        let (_slot, port) = self.reserve(request_id)?;
        self.reject_reused_request(request_id)?;
        let source = workspace.load_local_source_row(input).await?;
        let row = self
            .parse_frozen_source_row(&source, record_number, &port.cancel)
            .await?;
        let output = workspace.finish_local_source_row(source, row).await?;
        if port.cancel.is_cancelled() {
            return Err(failure(ErrorCode::CANCELLED));
        }
        Ok(output)
    }

    pub(super) async fn read_frozen_folder_source_row(
        &self,
        workspace: &WorkspaceBridge,
        payload: Value,
        request_id: Option<&str>,
    ) -> Result<Value, Error> {
        let input = read_local_folder_source_row_input(Actor::LocalUser, payload)?;
        let record_number = input.source.record_number;
        let (_slot, port) = self.reserve(request_id)?;
        self.reject_reused_request(request_id)?;
        let source = workspace.load_local_folder_source_row(input).await?;
        let row = self
            .parse_frozen_source_row(&source, record_number, &port.cancel)
            .await?;
        let output = workspace
            .finish_local_folder_source_row(source, row)
            .await?;
        if port.cancel.is_cancelled() {
            return Err(failure(ErrorCode::CANCELLED));
        }
        Ok(output)
    }

    async fn parse_frozen_source_row<R>(
        &self,
        source: &FrozenLocalSourceRow<R>,
        record_number: u64,
        cancel: &PreviewCancellation,
    ) -> Result<Value, Error> {
        let config = self
            .config
            .as_ref()
            .ok_or_else(|| failure(ErrorCode::PROVIDERNOTCONFIGURED))?;
        let mapping =
            serde_json::to_value(&source.mapping).map_err(|_| failure(ErrorCode::INTERNALERROR))?;
        local_import::read_source_row(
            config,
            &source.original,
            &source.source_sha256,
            &mapping,
            record_number,
            source.review_declarations,
            cancel.cancelled(),
        )
        .await
    }

    pub(super) fn cancel(&self, request_id: &str) {
        if !valid_request_id(request_id) || self.requests.committed(request_id) {
            return;
        }
        let mut state = self.state.lock().expect("preview state");
        if let Some((Some(id), cancel)) = &state.active
            && id == request_id
        {
            cancel.cancel();
        } else if !state.closing && !state.early_cancellations.iter().any(|id| id == request_id) {
            // A native cancel can arrive before the asynchronous execute call.
            if state.early_cancellations.len() == MAX_EARLY_CANCELLATIONS {
                state.early_cancellations.pop_front();
            }
            state.early_cancellations.push_back(request_id.to_owned());
        }
    }

    pub(super) async fn shutdown(&self) {
        {
            let mut state = self.state.lock().expect("preview state");
            state.closing = true;
            state.reviewed = None;
            state.reviewed_folder = None;
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

fn valid_request_id(id: &str) -> bool {
    !id.is_empty() && id.len() <= 64 && id.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-')
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

enum FileRead {
    Preview(Option<String>),
    Checked(String),
}

impl PreviewPort<'_> {
    fn commit_guard(&self, folder: FolderId, file: FileId, source_sha256: String) -> MappingGuard {
        MappingGuard {
            cancel: Arc::clone(&self.cancel),
            folders: Arc::clone(&self.host.folders),
            folder,
            file,
            source_sha256,
        }
    }

    fn check_live(&self, folder: FolderId) -> Result<(), Error> {
        if self.cancel.is_cancelled() {
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

    async fn read_file(
        &self,
        folder: FolderId,
        file: FileId,
        mode: FileRead,
    ) -> Result<FrozenFile, Error> {
        self.check_live(folder)?;
        let folders = Arc::clone(&self.host.folders);
        let cancel = Arc::clone(&self.cancel);
        let read = tokio::task::spawn_blocking(move || match mode {
            FileRead::Preview(digest) => {
                folders.read(folder, file, digest.as_deref(), &cancel.file)
            }
            FileRead::Checked(digest) => {
                folders.read_for_publication(folder, file, &digest, &cancel.file)
            }
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
        Ok(frozen)
    }

    async fn read_preview(&self, request: &LocalImportPreviewInput) -> Result<Value, Error> {
        let folder = folder_id(&request.folder_id)?;
        let file = file_id(&request.file_id)?;
        let config = self
            .host
            .config
            .as_ref()
            .ok_or_else(|| failure(ErrorCode::PROVIDERNOTCONFIGURED))?;
        let frozen = self
            .read_file(
                folder,
                file,
                FileRead::Preview(request.expected_sha256.clone()),
            )
            .await?;
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
            || self.check_live(folder),
        )
        .await;
        self.check_live(folder)?;
        let preview = preview.map_err(|mut error| {
            if error.code == ErrorCode::RESOURCELIMIT {
                error.message = "The preview exceeds the response budget. Request fewer rows or select a smaller CSV file.".into();
                error.details.insert("sha256".into(), json!(frozen.sha256));
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
        self.check_live(folder_id(&input.folder_id)?)
    }

    fn preview<'a>(&'a mut self, input: &'a LocalImportPreviewInput) -> MarketFuture<'a> {
        Box::pin(self.read_preview(input))
    }
}

fn folder_id(input: &str) -> Result<FolderId, Error> {
    let id: FolderId = input
        .parse()
        .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
    if id.to_string() != input {
        return Err(failure(ErrorCode::INVALIDARGUMENT));
    }
    Ok(id)
}

fn file_id(input: &str) -> Result<FileId, Error> {
    let id: FileId = input
        .parse()
        .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
    if id.to_string() != input {
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
