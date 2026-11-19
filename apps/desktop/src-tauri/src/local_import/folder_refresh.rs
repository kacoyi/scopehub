use std::{
    collections::{HashMap, hash_map::Entry},
    sync::Arc,
};

use scopehub_application::{
    Actor, FolderRefreshCheck, FolderRefreshFile, MAX_STAGED_RECORDS, ReviewedImportMapping,
    failure, folder_refresh_input, folder_refresh_output, mapping_header_digest,
};
use scopehub_domain::protocol::{Error, ErrorCode};
use scopehub_platform::local_folder::{FileKind, FolderError, FrozenFile, MAX_PREVIEW_BYTES};
use scopehub_worker_host::{local_import, market::WorkerConfig};
use serde_json::{Value, json};

use super::{LocalImport, PreviewPort, folder_failure, folder_id};
use crate::workspace::WorkspaceBridge;

const MAX_FILES: usize = 64;
const MAX_INPUT_BYTES: usize = 16 * 1024 * 1024;

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
        let accepted_key = base.dataset.source_key.clone();
        let scan = tokio::task::spawn_blocking(move || {
            host.scan_for_refresh(folder, &accepted_path, &accepted_key, &cancel.file)
        })
        .await
        .map_err(|_| failure(ErrorCode::INTERNALERROR))?
        .map_err(|error| {
            if error == FolderError::InvalidSelection {
                let mut denied = failure(ErrorCode::PERMISSIONDENIED);
                denied.message = "Choose the directory used for this published revision.".into();
                denied
            } else {
                folder_failure(error)
            }
        })?;
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
            if let FolderRefreshCheck::Complete(content) = &check {
                records += content["source_record_count"].as_u64().unwrap_or_default() as usize;
                if records > MAX_STAGED_RECORDS {
                    return Err(folder_limit(
                        "A folder preview can check up to 20,000 complete CSV records, including aliases.",
                    ));
                }
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
        let output = tokio::task::spawn_blocking(move || {
            folder_refresh_output(Actor::LocalUser, &base, files, || cancel.is_cancelled())
        })
        .await
        .map_err(|_| failure(ErrorCode::INTERNALERROR))??;
        port.check_live(folder)?;
        let host = Arc::clone(&self.folders);
        let cancel = Arc::clone(&port.cancel);
        let output = workspace
            .finish_folder_refresh(input.revision_id, move || {
                host.with_verified_refresh_current(&scan, &digests, &cancel.file, || output)
                    .map_err(refresh_source_failure)
            })
            .await?;
        port.check_live(folder)?;
        Ok(output)
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
    let normalized = local_import::complete(
        config,
        bytes,
        &source.sha256,
        &mapping,
        port.stopped(source.folder_id),
    )
    .await;
    port.check_live(source.folder_id)?;
    match normalized {
        Ok(content) => Ok(FolderRefreshCheck::Complete(Arc::new(content))),
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
