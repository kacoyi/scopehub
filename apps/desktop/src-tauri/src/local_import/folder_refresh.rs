use std::{
    collections::{HashMap, hash_map::Entry},
    sync::{Arc, atomic::Ordering},
};

use scopehub_application::{
    Actor, FolderManifestBar, FolderManifestPublication, FolderManifestSource, FolderRefreshCheck,
    FolderRefreshFile, MAX_STAGED_RECORDS, MappingCommitGuard, ReviewedImportMapping, failure,
    folder_prepare_input, folder_refresh_input, folder_refresh_output, mapping_header_digest,
    prepare_folder_manifest, publish_folder_refresh_input,
};
use scopehub_domain::{
    protocol::{Error, ErrorCode},
    wire::max_message_bytes,
};
use scopehub_platform::local_folder::{
    FileId, FileKind, FolderError, FolderId, FolderRefreshScan, FrozenFile, LocalFolderHost,
    MAX_PREVIEW_BYTES,
};
use scopehub_worker_host::{local_import, market::WorkerConfig};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

use super::{
    ACTIVE, COMMITTING, ImportAttempt, ImportReceipt, LocalImport, MutationInput,
    PreviewCancellation, PreviewPort, folder_failure, folder_id,
};
use crate::workspace::WorkspaceBridge;

const MAX_FILES: usize = 64;
const MAX_INPUT_BYTES: usize = 16 * 1024 * 1024;

pub(super) struct FolderReview {
    stage: Arc<FolderReviewStage>,
    prepared: Option<Arc<PreparedFolderReview>>,
}

impl FolderReview {
    pub(super) fn folder(&self) -> FolderId {
        self.stage.folder
    }
}

struct FolderReviewStage {
    folder: FolderId,
    review_id: String,
    parent_id: String,
    scan: Arc<FolderRefreshScan>,
    digests: Vec<(FileId, String)>,
    sources: Vec<InspectedSource>,
    comparison: Value,
}

struct InspectedSource {
    relative_path: String,
    sha256: String,
    record_count: u64,
    original: Arc<[u8]>,
}

pub(crate) struct PreparedFolderReview {
    candidate_sha256: String,
    bars: Vec<FolderManifestBar>,
}

impl FolderReviewStage {
    fn candidate(
        &self,
        bars: Vec<FolderManifestBar>,
    ) -> Result<FolderManifestPublication<'_>, Error> {
        FolderManifestPublication::new(&self.parent_id, self.retained_sources(), bars)
    }

    fn retained_sources(&self) -> Vec<FolderManifestSource<'_>> {
        self.sources
            .iter()
            .map(|source| FolderManifestSource {
                relative_path: &source.relative_path,
                sha256: &source.sha256,
                record_count: source.record_count,
                original: &source.original,
            })
            .collect()
    }
}

pub(crate) struct FolderCommitGuard {
    cancel: Arc<PreviewCancellation>,
    host: Arc<LocalFolderHost>,
    stage: Arc<FolderReviewStage>,
}

impl FolderCommitGuard {
    pub(crate) fn candidate(
        &self,
        prepared: &PreparedFolderReview,
    ) -> Result<FolderManifestPublication<'_>, Error> {
        if self.cancel.is_cancelled() {
            return Err(failure(ErrorCode::CANCELLED));
        }
        let candidate = self.stage.candidate(prepared.bars.clone())?;
        if candidate.input_sha256() != prepared.candidate_sha256 {
            return Err(failure(ErrorCode::INTERNALERROR));
        }
        Ok(candidate)
    }
}

impl MappingCommitGuard for FolderCommitGuard {
    fn commit<T>(&self, apply: impl FnOnce() -> Result<T, Error>) -> Result<T, Error> {
        self.host
            .with_verified_refresh_current(
                &self.stage.scan,
                &self.stage.digests,
                &self.cancel.file,
                || {
                    self.cancel
                        .phase
                        .compare_exchange(ACTIVE, COMMITTING, Ordering::AcqRel, Ordering::Acquire)
                        .map_err(|_| failure(ErrorCode::CANCELLED))?;
                    apply()
                },
            )
            .map_err(refresh_source_failure)?
    }
}

impl LocalImport {
    pub(crate) async fn preview_folder_refresh(
        &self,
        workspace: &WorkspaceBridge,
        payload: Value,
        request_id: Option<&str>,
    ) -> Result<Value, Error> {
        let input = folder_refresh_input(Actor::LocalUser, payload)?;
        let (_slot, port) = self.reserve(request_id)?;
        self.requests.reject_reused(request_id)?;
        let folder = folder_id(&input.folder_id)?;
        port.check_live(folder)?;
        let base = workspace
            .folder_refresh_base(input.revision_id.clone())
            .await?;
        let host = Arc::clone(&self.folders);
        let cancel = Arc::clone(&port.cancel);
        let accepted_path = base.dataset.relative_path.clone();
        let accepted_key = base.source_key.clone();
        let previous_paths = base.previous_paths().map(str::to_owned).collect::<Vec<_>>();
        let scan = tokio::task::spawn_blocking(move || {
            host.scan_for_refresh(
                folder,
                &accepted_path,
                &accepted_key,
                previous_paths.iter().map(String::as_str),
                &cancel.file,
            )
        })
        .await
        .map_err(|_| failure(ErrorCode::INTERNALERROR))?
        .map_err(refresh_origin_failure)?;
        let scan = Arc::new(scan);
        let entries: Vec<_> = scan.files().cloned().collect();
        if entries.len() > MAX_FILES {
            return Err(folder_limit(
                "A folder preview can check up to 64 CSV files.",
            ));
        }
        let config = self
            .config
            .as_ref()
            .ok_or_else(|| failure(ErrorCode::PROVIDERNOTCONFIGURED))?;
        let mut bytes = 0;
        let mut records = 0;
        let mut checks = HashMap::<String, FolderRefreshCheck>::new();
        let mut files = Vec::with_capacity(entries.len());
        let mut digests = Vec::with_capacity(entries.len());
        let mut sources = Vec::with_capacity(entries.len());
        files.extend(scan.blocked_prior_paths().map(|path| FolderRefreshFile {
            relative_path: path.to_owned(),
            sha256: None,
            check: FolderRefreshCheck::Unavailable(
                "An opaque directory component blocks inspection of the accepted source.".into(),
            ),
        }));

        for entry in entries {
            port.check_live(folder)?;
            if entry.kind != FileKind::Csv || entry.byte_count > MAX_PREVIEW_BYTES as u64 {
                files.push(FolderRefreshFile {
                    relative_path: entry.relative_path,
                    sha256: None,
                    check: FolderRefreshCheck::Unavailable(
                        "The entry is not a supported regular CSV within the 4 MiB file limit."
                            .into(),
                    ),
                });
                continue;
            }
            let scan = Arc::clone(&scan);
            let host = Arc::clone(&self.folders);
            let cancel = Arc::clone(&port.cancel);
            let read = tokio::task::spawn_blocking(move || {
                host.read_for_refresh(&scan, entry.file_id, &cancel.file)
            })
            .await
            .map_err(|_| failure(ErrorCode::INTERNALERROR))?;
            let frozen = match read {
                Ok(frozen) => frozen,
                Err(FolderError::Io | FolderError::UnsupportedFile) => {
                    files.push(FolderRefreshFile {
                        relative_path: entry.relative_path,
                        sha256: None,
                        check: FolderRefreshCheck::Unavailable(
                            "The current source could not be read for review.".into(),
                        ),
                    });
                    continue;
                }
                Err(error) => return Err(refresh_source_failure(error)),
            };
            bytes += frozen.file.byte_count as usize;
            if bytes > MAX_INPUT_BYTES {
                return Err(folder_limit(
                    "A folder preview can check up to 16 MiB of complete CSV files.",
                ));
            }
            let check = match checks.entry(frozen.sha256.clone()) {
                Entry::Occupied(entry) => entry.get().clone(),
                Entry::Vacant(entry) => entry
                    .insert(check_file(config, &port, &base.mapping, &frozen).await?)
                    .clone(),
            };
            if let FolderRefreshCheck::Inspected(content) = &check {
                let record_count = content["source_record_count"].as_u64().unwrap_or_default();
                records += record_count as usize;
                if records > MAX_STAGED_RECORDS {
                    return Err(folder_limit(
                        "A folder preview can check up to 20,000 complete CSV records, including aliases.",
                    ));
                }
                sources.push(InspectedSource {
                    relative_path: frozen.file.relative_path.clone(),
                    sha256: frozen.sha256.clone(),
                    record_count,
                    original: frozen
                        .content
                        .ok_or_else(|| failure(ErrorCode::OUTPUTINVALID))?,
                });
            }
            digests.push((frozen.file.file_id, frozen.sha256.clone()));
            files.push(FolderRefreshFile {
                relative_path: frozen.file.relative_path,
                sha256: Some(frozen.sha256),
                check,
            });
        }
        port.check_live(folder)?;
        let cancel = Arc::clone(&port.cancel);
        let parent_id = input.revision_id;
        let stage = tokio::task::spawn_blocking(move || {
            let mut comparison =
                folder_refresh_output(Actor::LocalUser, &base, files, || cancel.is_cancelled())?;
            let review_id = review_id(folder, &parent_id, &comparison)?;
            comparison["review_id"] = json!(review_id);
            if comparison.to_string().len() >= max_message_bytes() {
                return Err(failure(ErrorCode::RESOURCELIMIT));
            }
            Ok(Arc::new(FolderReviewStage {
                folder,
                review_id,
                parent_id,
                scan,
                digests,
                sources,
                comparison,
            }))
        })
        .await
        .map_err(|_| failure(ErrorCode::INTERNALERROR))??;
        port.check_live(folder)?;
        let host = Arc::clone(&self.folders);
        let cancel = Arc::clone(&port.cancel);
        let checked = Arc::clone(&stage);
        let output = workspace
            .finish_folder_refresh(stage.parent_id.clone(), move || {
                host.with_verified_refresh_current(
                    &checked.scan,
                    &checked.digests,
                    &cancel.file,
                    || checked.comparison.clone(),
                )
                .map_err(refresh_source_failure)
            })
            .await?;
        let mut state = self.state.lock().expect("preview state");
        port.check_live(folder)?;
        state.reviewed_folder = Some(FolderReview {
            stage,
            prepared: None,
        });
        Ok(output)
    }

    pub(crate) async fn prepare_folder_publication(
        &self,
        workspace: &WorkspaceBridge,
        payload: Value,
        request_id: Option<&str>,
    ) -> Result<Value, Error> {
        let input = folder_prepare_input(Actor::LocalUser, payload)?;
        let (_slot, port) = self.reserve(request_id)?;
        self.requests.reject_reused(request_id)?;
        let folder = folder_id(&input.folder_id)?;
        port.check_live(folder)?;
        let stage = {
            let state = self.state.lock().expect("preview state");
            Arc::clone(&reviewed_folder(&state.reviewed_folder, folder, &input.review_id)?.stage)
        };
        let checked = Arc::clone(&stage);
        let cancel = Arc::clone(&port.cancel);
        let (prepared, output) = workspace
            .finish_folder_refresh(stage.parent_id.clone(), move || {
                if cancel.is_cancelled() {
                    return Err(failure(ErrorCode::CANCELLED));
                }
                let candidate = prepare_folder_manifest(
                    &checked.parent_id,
                    &checked.comparison,
                    checked.retained_sources(),
                    &input,
                )?;
                let output = json!({
                    "version": 1,
                    "review_id": checked.review_id,
                    "candidate_sha256": candidate.input_sha256(),
                    "parent_revision_id": checked.parent_id,
                    "source_count": candidate.sources().len(),
                    "bar_count": candidate.bars().len(),
                });
                let prepared = Arc::new(PreparedFolderReview {
                    candidate_sha256: candidate.input_sha256().to_owned(),
                    bars: candidate.bars().to_vec(),
                });
                Ok((prepared, output))
            })
            .await?;
        let mut state = self.state.lock().expect("preview state");
        port.check_live(folder)?;
        let review = state
            .reviewed_folder
            .as_mut()
            .filter(|review| Arc::ptr_eq(&review.stage, &stage))
            .ok_or_else(review_unavailable)?;
        review.prepared = Some(prepared);
        Ok(output)
    }

    pub(crate) async fn publish_folder_refresh(
        &self,
        workspace: &WorkspaceBridge,
        payload: Value,
        request_id: Option<&str>,
    ) -> Result<Value, Error> {
        let input = publish_folder_refresh_input(Actor::LocalUser, payload)?;
        let (_slot, port) = self.reserve_for_receipt(request_id)?;
        let folder = folder_id(&input.folder_id)?;
        let (pending, previously_committed) = match self
            .requests
            .start(request_id, MutationInput::PublishFolder(input.clone()))?
        {
            ImportAttempt::New(pending) => (pending, false),
            ImportAttempt::Committed(ImportReceipt::FolderPublication) => (None, true),
            ImportAttempt::Committed(_) => return Err(failure(ErrorCode::INTERNALERROR)),
        };
        let host = Arc::clone(&self.folders);
        let receipt = workspace
            .replay_folder_publication(
                input.idempotency_key.clone(),
                input.candidate_sha256.clone(),
                move |anchor| {
                    host.verify_refresh_origin(
                        folder,
                        &anchor.revision.relative_path,
                        &anchor.source_key,
                        &scopehub_platform::local_folder::Cancellation::default(),
                    )
                    .map_err(refresh_origin_failure)
                },
            )
            .await?;
        if let Some(receipt) = receipt {
            if let Some(pending) = pending {
                pending.committed(ImportReceipt::FolderPublication);
            }
            return Ok(receipt);
        }
        if previously_committed {
            return Err(failure(ErrorCode::SNAPSHOTUNAVAILABLE));
        }

        port.check_live(folder)?;
        let (stage, prepared) = {
            let state = self.state.lock().expect("preview state");
            let review = reviewed_folder(&state.reviewed_folder, folder, &input.review_id)?;
            let prepared = review
                .prepared
                .as_ref()
                .filter(|prepared| prepared.candidate_sha256 == input.candidate_sha256)
                .ok_or_else(review_unavailable)?;
            (Arc::clone(&review.stage), Arc::clone(prepared))
        };
        let guard = FolderCommitGuard {
            cancel: Arc::clone(&port.cancel),
            host: Arc::clone(&self.folders),
            stage,
        };
        workspace
            .publish_folder(input.idempotency_key, prepared, guard, pending)
            .await
    }
}

fn review_id(folder: FolderId, parent: &str, comparison: &Value) -> Result<String, Error> {
    let mut digest = Sha256::new();
    serde_json::to_writer(
        &mut digest,
        &(
            "scopehub-folder-review-v1",
            folder.to_string(),
            parent,
            comparison,
        ),
    )
    .map_err(|_| failure(ErrorCode::INTERNALERROR))?;
    Ok(format!("{:x}", digest.finalize()))
}

fn reviewed_folder<'a>(
    review: &'a Option<FolderReview>,
    folder: FolderId,
    review_id: &str,
) -> Result<&'a FolderReview, Error> {
    review
        .as_ref()
        .filter(|review| review.stage.folder == folder && review.stage.review_id == review_id)
        .ok_or_else(review_unavailable)
}

fn review_unavailable() -> Error {
    let mut error = failure(ErrorCode::STALECONTEXT);
    error.message = "Review the current folder before publishing.".into();
    error
}

fn refresh_origin_failure(error: FolderError) -> Error {
    if error == FolderError::InvalidSelection {
        let mut denied = failure(ErrorCode::PERMISSIONDENIED);
        denied.message = "Choose the directory used for this published revision.".into();
        denied
    } else {
        folder_failure(error)
    }
}

async fn check_file(
    config: &WorkerConfig,
    port: &PreviewPort<'_>,
    reviewed: &ReviewedImportMapping,
    source: &FrozenFile,
) -> Result<FolderRefreshCheck, Error> {
    let bytes = source
        .content
        .as_deref()
        .ok_or_else(|| failure(ErrorCode::RESOURCELIMIT))?;
    let mut header_mapping = reviewed.mapping.clone();
    header_mapping.record_kind = "unclassified".into();
    let header_mapping =
        serde_json::to_value(header_mapping).map_err(|_| failure(ErrorCode::INTERNALERROR))?;
    let header = local_import::preview(
        config,
        bytes,
        &source.sha256,
        &header_mapping,
        1,
        port.stopped(source.folder_id),
        || port.check_live(source.folder_id),
    )
    .await;
    port.check_live(source.folder_id)?;
    let header = match header {
        Ok(header) => header,
        Err(error) if error.code == ErrorCode::RESOURCELIMIT => {
            return Ok(FolderRefreshCheck::Unavailable(
                "The decoded CSV header could not be inspected within the preview limit.".into(),
            ));
        }
        Err(error) => return Err(error),
    };
    let Some(header_sha256) = mapping_header_digest(&json!({"preview": header}), &reviewed.mapping)
    else {
        return Ok(FolderRefreshCheck::Unavailable(
            "The ordered CSV header could not be decoded with the saved encoding and delimiter."
                .into(),
        ));
    };
    if header_sha256 != reviewed.header_sha256 {
        return Ok(FolderRefreshCheck::HeaderChanged);
    }

    let mapping =
        serde_json::to_value(&reviewed.mapping).map_err(|_| failure(ErrorCode::INTERNALERROR))?;
    let inspected = local_import::inspect_folder_source(
        config,
        bytes,
        &source.sha256,
        &mapping,
        port.stopped(source.folder_id),
    )
    .await;
    port.check_live(source.folder_id)?;
    match inspected {
        Ok(content) => Ok(FolderRefreshCheck::Inspected(Arc::new(content))),
        Err(error) if error.code == ErrorCode::RESOURCELIMIT => Ok(FolderRefreshCheck::Unavailable(
            "The complete CSV could not be checked within the 10,000-record and 4 MiB result limits.".into(),
        )),
        Err(error) if error.code == ErrorCode::DATAUNAVAILABLE => {
            let locator = error.details.get("locator");
            let detail = match locator {
                Some(locator) => format!(
                    "{} Source lines {}–{}.",
                    error.message, locator["line_start"], locator["line_end"]
                ),
                None => error.message.to_string(),
            };
            Ok(FolderRefreshCheck::InvalidRows(
                detail.chars().take(1024).collect(),
            ))
        }
        Err(error) => Err(error),
    }
}

fn refresh_source_failure(error: FolderError) -> Error {
    let mut failure = folder_failure(error);
    if error == FolderError::ContentChanged {
        failure.message = "The folder changed during review. Stage the refresh again.".into();
        failure
            .details
            .insert("reason".into(), json!("folder_changed_during_review"));
    }
    failure
}

fn folder_limit(message: &str) -> Error {
    let mut error = failure(ErrorCode::RESOURCELIMIT);
    error.message = message.into();
    error
}
