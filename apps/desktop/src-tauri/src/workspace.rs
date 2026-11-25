//! Native profile binding and admission for the existing SQLite workspace writer.

use std::{
    future::Future,
    path::{Path, PathBuf},
    sync::Arc,
};

use scopehub_application::{
    AI_TEXT_CONNECTION, Actor, ApprovedPython, DECISION_NOTE_OPERATIONS,
    DECISION_NOTE_PREVIEW_RESTORE, FolderManifestAnchor, FolderRefreshBase, FrozenLocalSourceRow,
    LIST_LOCAL_FOLDER_REVISIONS, LIST_LOCAL_IMPORT_REVISIONS, LOAD_LOCAL_IMPORT_MAPPING,
    LOCAL_API_CONNECTION, LOCAL_WORKSPACE_OPERATIONS, LocalApiAdmission, LocalDatasetPublication,
    MappingCommitGuard, OperationRegistry, PREPARE_LOCAL_FOLDER_PUBLICATION,
    PREVIEW_LOCAL_FOLDER_REFRESH, PRIVATE_RECORD_CHANGE, PUBLISH_LOCAL_FOLDER_REFRESH,
    PUBLISH_LOCAL_IMPORT, READ_LOCAL_FOLDER_REVISION, READ_LOCAL_FOLDER_SOURCE_ROW,
    READ_LOCAL_IMPORT_REVISION, READ_LOCAL_IMPORT_SOURCE_ROW, REVOKE_LOCAL_FOLDER_REVISION,
    REVOKE_LOCAL_IMPORT_REVISION, ReviewedImportMapping, SAVE_LOCAL_IMPORT_MAPPING, TextModelWork,
    WorkspaceAccess, WorkspaceBinding, evidence_operation_name, failure,
    list_local_folder_revisions_input, list_local_import_revisions_input,
    local_mapping_fingerprint, read_local_folder_revision_input, read_local_import_revision_input,
    revoke_local_folder_revision_input, revoke_local_import_revision_input,
};
use scopehub_domain::protocol::{
    CsvMappingV1, Error, ErrorCode, ReadLocalFolderSourceRowInput, ReadLocalImportSourceRowInput,
};
use scopehub_persistence::SqliteWorkspace;
use scopehub_platform::local_folder::FrozenFile;
#[cfg(any(windows, test))]
use scopehub_platform::model_credentials::CredentialLifetime;
use scopehub_platform::model_credentials::{CredentialError, OpenAiCredentials};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use tokio::sync::{Mutex, OwnedMutexGuard};
use uuid::Uuid;

use crate::local_import::{FolderCommitGuard, ImportReceipt, PendingImport, PreparedFolderReview};

const WORKSPACE_ID: &str = "main";
const WORKSPACE_DIRECTORY: &str = "workspace-dev";
const LOCAL_IMPORT_OPERATIONS: &[&str] = &[
    LOAD_LOCAL_IMPORT_MAPPING,
    PREVIEW_LOCAL_FOLDER_REFRESH,
    SAVE_LOCAL_IMPORT_MAPPING,
    PUBLISH_LOCAL_IMPORT,
    LIST_LOCAL_IMPORT_REVISIONS,
    READ_LOCAL_IMPORT_REVISION,
    READ_LOCAL_IMPORT_SOURCE_ROW,
    REVOKE_LOCAL_IMPORT_REVISION,
    PREPARE_LOCAL_FOLDER_PUBLICATION,
    PUBLISH_LOCAL_FOLDER_REFRESH,
    LIST_LOCAL_FOLDER_REVISIONS,
    READ_LOCAL_FOLDER_REVISION,
    READ_LOCAL_FOLDER_SOURCE_ROW,
    REVOKE_LOCAL_FOLDER_REVISION,
];
const MAX_REQUEST_BYTES: usize = 16 * 1024;
const MAX_NOTE_REQUEST_BYTES: usize = 32 * 1024;
const MAX_BACKUP_REQUEST_BYTES: usize = 1024 * 1024;

#[derive(Clone)]
pub(super) struct WorkspaceBridge {
    lane: Arc<Mutex<Lane>>,
    workspace_enabled: bool,
    openai_credentials: Arc<OpenAiCredentials>,
}

enum Lane {
    Disabled,
    Unavailable,
    Pending(PathBuf),
    Open(Box<BoundWorkspace>),
    Closed,
}

enum Request {
    Snapshot,
    WorkspaceSource(Value),
    FrozenChart(String),
    FrozenChartStatus(String),
    IndicatorPreviewStatus(String),
    IndicatorAcceptedRead(Value),
    SampleIndicatorPreview(Value, Value),
    SampleEvidenceSelection(Value, Value),
    Operation(Value),
}

struct BoundWorkspace {
    registry: OperationRegistry<SqliteWorkspace>,
    profile_id: String,
}

impl WorkspaceBridge {
    pub(super) fn local(app_local_data: Option<&Path>, workspace_enabled: bool) -> Self {
        // The native launcher owns this path. A sample or preview-only session
        // never opens the database.
        let lane = match app_local_data {
            Some(path) => Lane::Pending(path.to_owned()),
            None if workspace_enabled => Lane::Unavailable,
            None => Lane::Disabled,
        };
        Self {
            lane: Arc::new(Mutex::new(lane)),
            workspace_enabled,
            openai_credentials: Arc::default(),
        }
    }

    fn admit(&self) -> Result<OwnedMutexGuard<Lane>, Error> {
        // The workspace, reviewed mappings and published datasets share one writer slot.
        let lane = Arc::clone(&self.lane)
            .try_lock_owned()
            .map_err(|_| failure(ErrorCode::BUSY))?;
        match &*lane {
            Lane::Disabled => Err(failure(ErrorCode::PERMISSIONDENIED)),
            Lane::Unavailable => Err(failure(ErrorCode::INTERNALERROR)),
            Lane::Closed => Err(failure(ErrorCode::CANCELLED)),
            Lane::Pending(_) | Lane::Open(_) => Ok(lane),
        }
    }

    async fn with_bound<T: Send + 'static>(
        mut lane: OwnedMutexGuard<Lane>,
        create: bool,
        run: impl FnOnce(&mut BoundWorkspace) -> Result<T, Error> + Send + 'static,
    ) -> Result<Option<T>, Error> {
        tokio::task::spawn_blocking(move || {
            if let Lane::Pending(path) = &*lane {
                if !path.is_absolute() {
                    return Err(failure(ErrorCode::INTERNALERROR));
                }
                if !create
                    && !path
                        .join(WORKSPACE_DIRECTORY)
                        .try_exists()
                        .map_err(|_| failure(ErrorCode::INTERNALERROR))?
                {
                    return Ok(None);
                }
                *lane = Lane::Open(Box::new(BoundWorkspace::open(path)?));
            }
            let Lane::Open(bound) = &mut *lane else {
                unreachable!("admission requires an available workspace");
            };
            run(bound).map(Some)
        })
        .await
        .map_err(|_| failure(ErrorCode::INTERNALERROR))?
    }

    pub(super) async fn local_api_connection(
        &self,
        payload: Value,
    ) -> Result<LocalApiAdmission, Error> {
        if payload.to_string().len() >= MAX_REQUEST_BYTES {
            return Err(failure(ErrorCode::RESOURCELIMIT));
        }
        Self::with_bound(self.admit()?, true, move |bound| {
            let access = WorkspaceAccess {
                operations: &[LOCAL_API_CONNECTION],
                ..local_import_access(&bound.profile_id)
            };
            bound.registry.local_api_connection(&access, &payload)
        })
        .await?
        .ok_or_else(|| failure(ErrorCode::INTERNALERROR))
    }

    pub(super) async fn complete_local_api_trial(
        &self,
        trial: scopehub_application::LocalApiTrial,
        sample: Value,
    ) -> Result<Value, Error> {
        Self::with_bound(self.admit()?, false, move |bound| {
            bound.registry.complete_local_api_trial(trial, sample)
        })
        .await?
        .ok_or_else(|| failure(ErrorCode::INTERNALERROR))
    }

    pub(super) async fn dispatch(
        &self,
        operation: Option<&str>,
        payload: Value,
    ) -> Result<Value, Error> {
        let chart_status = operation.is_none() && payload["type"] == "frozen_chart_status";
        if !self.workspace_enabled && !chart_status {
            return Err(failure(ErrorCode::PERMISSIONDENIED));
        }
        let lane = self.admit()?;
        let request = match operation {
            None if payload == json!({"type": "snapshot"}) => Request::Snapshot,
            None if payload["type"] == "workspace_source"
                && payload.to_string().len() <= MAX_REQUEST_BYTES =>
            {
                Request::WorkspaceSource(payload)
            }
            None if payload["type"] == "frozen_chart" || chart_status => {
                let revision_id = payload["revision_id"]
                    .as_str()
                    .filter(|id| {
                        id.len() == 36
                            && payload.as_object().is_some_and(|obj| obj.len() == 3)
                            && payload["request_id"]
                                .as_str()
                                .is_some_and(|request_id| request_id.len() == 36)
                    })
                    .ok_or_else(|| failure(ErrorCode::INVALIDARGUMENT))?;
                if chart_status {
                    Request::FrozenChartStatus(revision_id.to_owned())
                } else {
                    Request::FrozenChart(revision_id.to_owned())
                }
            }
            None if matches!(
                payload["type"].as_str(),
                Some("indicator_accepted_layers" | "indicator_accepted_output")
            ) =>
            {
                Request::IndicatorAcceptedRead(payload)
            }
            None if payload["type"] == "indicator_preview_status" => {
                let task_id = payload["task_id"]
                    .as_str()
                    .filter(|id| {
                        id.len() == 36
                            && payload.as_object().is_some_and(|object| object.len() == 3)
                            && payload["request_id"]
                                .as_str()
                                .is_some_and(|request_id| request_id.len() == 36)
                    })
                    .ok_or_else(|| failure(ErrorCode::INVALIDARGUMENT))?;
                Request::IndicatorPreviewStatus(task_id.into())
            }
            Some(operation)
                if payload["type"] == "request"
                    && (payload["intent"]["operation"] == operation
                        || evidence_operation_name(&payload) == Some(operation)
                        || matches!(
                            operation,
                            scopehub_application::CANCEL_INDICATOR_TASK
                                | scopehub_application::INDICATOR_CONFIRMATION
                        ) && payload["operation"] == operation) =>
            {
                let limit = if matches!(
                    operation,
                    DECISION_NOTE_PREVIEW_RESTORE | PRIVATE_RECORD_CHANGE
                ) {
                    MAX_BACKUP_REQUEST_BYTES
                } else if DECISION_NOTE_OPERATIONS.contains(&operation) {
                    MAX_NOTE_REQUEST_BYTES
                } else {
                    MAX_REQUEST_BYTES
                };
                if payload.to_string().len() >= limit {
                    return Err(failure(ErrorCode::RESOURCELIMIT));
                }
                Request::Operation(payload)
            }
            _ => return Err(failure(ErrorCode::INVALIDARGUMENT)),
        };
        let reply =
            Self::with_bound(lane, !chart_status, move |bound| bound.dispatch(request)).await?;
        match reply {
            Some(reply) => Ok(reply),
            None if chart_status => Ok(json!({"available": false})),
            None => Err(failure(ErrorCode::INTERNALERROR)),
        }
    }

    pub(super) async fn dispatch_indicator_decision(
        &self,
        command: Value,
    ) -> Result<(Value, Option<ApprovedPython>), Error> {
        if !self.workspace_enabled {
            return Err(failure(ErrorCode::PERMISSIONDENIED));
        }
        if command.to_string().len() >= MAX_REQUEST_BYTES {
            return Err(failure(ErrorCode::RESOURCELIMIT));
        }
        Self::with_bound(self.admit()?, true, move |bound| {
            let reply = bound.dispatch(Request::Operation(command))?;
            let work = if let Some(task) = reply["outcome"]["result"]["task_id"].as_str() {
                bound
                    .registry
                    .take_approved_python(&workspace_access(&bound.profile_id), task)?
            } else {
                None
            };
            Ok((reply, work))
        })
        .await?
        .ok_or_else(|| failure(ErrorCode::INTERNALERROR))
    }

    pub(super) async fn dispatch_text_connection(
        &self,
        command: Value,
    ) -> Result<(Value, Option<(String, TextModelWork)>), Error> {
        if command["intent"]["operation"] != AI_TEXT_CONNECTION || command["type"] != "request" {
            return Err(failure(ErrorCode::INVALIDARGUMENT));
        }
        if command.to_string().len() >= MAX_REQUEST_BYTES {
            return Err(failure(ErrorCode::RESOURCELIMIT));
        }
        let credentials = Arc::clone(&self.openai_credentials);
        Self::with_bound(self.admit()?, true, move |bound| {
            let access = text_model_access(&bound.profile_id);
            if command["scope"]["workspace_id"] == access.workspace_id
                && bound
                    .registry
                    .needs_openai_credential_for_review(&command["intent"]["arguments"])
            {
                bound.registry.refresh_openai_credential(
                    &access,
                    openai_revision(&credentials, &bound.profile_id)?,
                )?;
            }
            let reply = bound.registry.execute_workspace(&access, command)?;
            let result = &reply["outcome"]["result"];
            let work = if result["action"] == "send"
                || result["action"] == "poll" && result["turn"]["state"] == "running"
            {
                let id = result["turn_id"]
                    .as_str()
                    .and_then(|id| uuid::Uuid::parse_str(id).ok())
                    .ok_or_else(|| failure(ErrorCode::OUTPUTINVALID))?;
                bound
                    .registry
                    .take_text_model_work(&access, id)?
                    .map(|work| (bound.profile_id.clone(), work))
            } else {
                None
            };
            Ok((reply, work))
        })
        .await?
        .ok_or_else(|| failure(ErrorCode::INTERNALERROR))
    }

    pub(super) fn openai_credentials(&self) -> Arc<OpenAiCredentials> {
        Arc::clone(&self.openai_credentials)
    }

    #[cfg(any(windows, test))]
    pub(super) async fn configure_openai_credential(
        &self,
        action: String,
        secret: Option<String>,
    ) -> Result<Value, Error> {
        let credentials = self.openai_credentials();
        Self::with_bound(self.admit()?, true, move |bound| {
            let revision = match (action.as_str(), secret) {
                ("status", None) => openai_revision(&credentials, &bound.profile_id)?,
                ("replace", Some(secret)) => {
                    let revision = credentials
                        .replace(&bound.profile_id, secret)
                        .map_err(credential_failure)?;
                    Some(parse_credential_revision(&revision)?)
                }
                ("remove", None) => {
                    credentials
                        .remove(&bound.profile_id)
                        .map_err(credential_failure)?;
                    None
                }
                _ => return Err(failure(ErrorCode::INVALIDARGUMENT)),
            };
            bound
                .registry
                .refresh_openai_credential(&text_model_access(&bound.profile_id), revision)?;
            Ok(json!({
                "configured": revision.is_some(),
                "lifetime": match credentials.lifetime() {
                    CredentialLifetime::Session => "session",
                    CredentialLifetime::WindowsCredentialManager => "windows_credential_manager",
                },
            }))
        })
        .await?
        .ok_or_else(|| failure(ErrorCode::INTERNALERROR))
    }

    pub(super) async fn finish_trusted_python(
        &self,
        task: String,
        response: Result<Value, Error>,
    ) -> Result<(), Error> {
        let lane = Arc::clone(&self.lane).lock_owned().await;
        if !matches!(&*lane, Lane::Open(_)) {
            return Ok(());
        }
        Self::with_bound(lane, false, move |bound| {
            bound.registry.finish_trusted_python(
                &workspace_access(&bound.profile_id),
                &task,
                response,
            )
        })
        .await?;
        Ok(())
    }

    pub(super) async fn dispatch_sample_preview(
        &self,
        command: Value,
        load_history: impl Future<Output = Result<Value, Error>>,
    ) -> Result<Value, Error> {
        if !self.workspace_enabled {
            return Err(failure(ErrorCode::PERMISSIONDENIED));
        }
        if command.to_string().len() >= MAX_REQUEST_BYTES {
            return Err(failure(ErrorCode::RESOURCELIMIT));
        }
        let history = load_history.await?;
        Self::with_bound(self.admit()?, true, move |bound| {
            bound.dispatch(Request::SampleIndicatorPreview(command, history))
        })
        .await?
        .ok_or_else(|| failure(ErrorCode::INTERNALERROR))
    }

    pub(super) async fn dispatch_sample_selection(
        &self,
        command: Value,
        load_history: impl Future<Output = Result<Value, Error>>,
    ) -> Result<Value, Error> {
        if !self.workspace_enabled {
            return Err(failure(ErrorCode::PERMISSIONDENIED));
        }
        if command.to_string().len() >= MAX_REQUEST_BYTES {
            return Err(failure(ErrorCode::RESOURCELIMIT));
        }
        let history = load_history.await?;
        Self::with_bound(self.admit()?, true, move |bound| {
            bound.dispatch(Request::SampleEvidenceSelection(command, history))
        })
        .await?
        .ok_or_else(|| failure(ErrorCode::INTERNALERROR))
    }

    pub(super) async fn find(
        &self,
        source_key: String,
    ) -> Result<Option<ReviewedImportMapping>, Error> {
        Self::with_bound(self.admit()?, false, move |bound| {
            bound
                .registry
                .find_reviewed_mapping(&local_import_access(&bound.profile_id), &source_key)
        })
        .await
        .map(Option::flatten)
    }

    pub(super) async fn save(
        &self,
        source_key: String,
        header_sha256: String,
        mapping: CsvMappingV1,
        guard: impl MappingCommitGuard + Send + 'static,
        attempt: Option<PendingImport>,
    ) -> Result<Value, Error> {
        Self::with_bound(self.admit()?, true, move |bound| {
            let record = bound.registry.save_reviewed_mapping(
                &local_import_access(&bound.profile_id),
                &source_key,
                &header_sha256,
                &mapping,
                &guard,
            )?;
            if let Some(attempt) = attempt {
                attempt.committed(ImportReceipt::Mapping(record.review_revision));
            }
            Ok(json!({ "version": 1, "record": record }))
        })
        .await?
        .ok_or_else(|| failure(ErrorCode::INTERNALERROR))
    }

    pub(super) async fn replay_saved_mapping(
        &self,
        source_key: String,
        revision: u64,
    ) -> Result<Value, Error> {
        Self::with_bound(self.admit()?, false, move |bound| {
            let record = bound.registry.replay_reviewed_mapping(
                &local_import_access(&bound.profile_id),
                &source_key,
                revision,
            )?;
            Ok(json!({ "version": 1, "record": record }))
        })
        .await?
        .ok_or_else(|| failure(ErrorCode::SNAPSHOTUNAVAILABLE))
    }

    pub(super) async fn publish(
        &self,
        source_key: String,
        frozen: FrozenFile,
        reviewed: ReviewedImportMapping,
        content: Value,
        guard: impl MappingCommitGuard + Send + 'static,
        attempt: Option<PendingImport>,
    ) -> Result<Value, Error> {
        Self::with_bound(self.admit()?, false, move |bound| {
            let candidate = LocalDatasetPublication {
                source_key: &source_key,
                relative_path: &frozen.file.relative_path,
                source_sha256: &frozen.sha256,
                mapping_sha256: &local_mapping_fingerprint(&reviewed.mapping)?,
                reviewed_mapping: &reviewed,
                original: frozen
                    .content
                    .as_deref()
                    .ok_or_else(|| failure(ErrorCode::RESOURCELIMIT))?,
                content: &content,
            };
            let access = local_import_access(&bound.profile_id);
            let revision_id = bound
                .registry
                .publish_reviewed_local_dataset(&access, &candidate, &guard)?;
            if let Some(attempt) = attempt {
                attempt.committed(ImportReceipt::Publication(revision_id.clone()));
            }
            bound
                .registry
                .local_dataset_publication_receipt(&access, &revision_id)
        })
        .await?
        .ok_or_else(|| failure(ErrorCode::STALECONTEXT))
    }

    pub(super) async fn replay_publication(&self, revision_id: String) -> Result<Value, Error> {
        Self::with_bound(self.admit()?, false, move |bound| {
            bound.registry.local_dataset_publication_receipt(
                &local_import_access(&bound.profile_id),
                &revision_id,
            )
        })
        .await?
        .ok_or_else(|| failure(ErrorCode::SNAPSHOTUNAVAILABLE))
    }

    pub(super) async fn folder_refresh_base(
        &self,
        revision_id: String,
    ) -> Result<FolderRefreshBase, Error> {
        Self::with_bound(self.admit()?, false, move |bound| {
            bound
                .registry
                .load_folder_refresh_base(&local_import_access(&bound.profile_id), &revision_id)
        })
        .await?
        .ok_or_else(|| failure(ErrorCode::SNAPSHOTUNAVAILABLE))
    }

    pub(super) async fn finish_folder_refresh<T: Send + 'static>(
        &self,
        revision_id: String,
        finish: impl FnOnce() -> Result<T, Error> + Send + 'static,
    ) -> Result<T, Error> {
        Self::with_bound(self.admit()?, false, move |bound| {
            // Keep the workspace lane until the native recheck finishes so
            // a display revocation cannot overtake the entitled read.
            bound
                .registry
                .load_folder_refresh_base(&local_import_access(&bound.profile_id), &revision_id)?;
            finish()
        })
        .await?
        .ok_or_else(|| failure(ErrorCode::SNAPSHOTUNAVAILABLE))
    }

    pub(super) async fn publish_folder(
        &self,
        idempotency_key: String,
        prepared: Arc<PreparedFolderReview>,
        guard: FolderCommitGuard,
        attempt: Option<PendingImport>,
    ) -> Result<Value, Error> {
        Self::with_bound(self.admit()?, false, move |bound| {
            let candidate = guard.candidate(&prepared)?;
            let receipt = bound.registry.publish_reviewed_folder(
                &local_import_access(&bound.profile_id),
                &idempotency_key,
                &candidate,
                &guard,
            )?;
            if let Some(attempt) = attempt {
                attempt.committed(ImportReceipt::FolderPublication);
            }
            Ok(receipt)
        })
        .await?
        .ok_or_else(|| failure(ErrorCode::SNAPSHOTUNAVAILABLE))
    }

    pub(super) async fn replay_folder_publication(
        &self,
        idempotency_key: String,
        candidate_sha256: String,
        verify_origin: impl FnOnce(&FolderManifestAnchor) -> Result<(), Error> + Send + 'static,
    ) -> Result<Option<Value>, Error> {
        let reply = Self::with_bound(self.admit()?, false, move |bound| {
            let receipt = bound.registry.replay_reviewed_folder(
                &local_import_access(&bound.profile_id),
                &idempotency_key,
                &candidate_sha256,
            )?;
            receipt
                .map(|(reply, anchor)| {
                    verify_origin(&anchor)?;
                    Ok(reply)
                })
                .transpose()
        })
        .await?;
        Ok(reply.flatten())
    }

    pub(super) async fn list_local_folder_revisions(&self, payload: Value) -> Result<Value, Error> {
        let input = list_local_folder_revisions_input(Actor::LocalUser, payload)?;
        let lane = self.admit().map_err(|error| {
            if error.code == ErrorCode::PERMISSIONDENIED {
                failure(ErrorCode::PROVIDERNOTCONFIGURED)
            } else {
                error
            }
        })?;
        let result = Self::with_bound(lane, false, move |bound| {
            bound
                .registry
                .list_frozen_folder_revisions(&local_import_access(&bound.profile_id), &input)
        })
        .await?;
        Ok(result.unwrap_or_else(
            || json!({"version": 1, "revisions": [], "next_after_revision_id": null}),
        ))
    }

    pub(super) async fn read_local_folder_revision(&self, payload: Value) -> Result<Value, Error> {
        let input = read_local_folder_revision_input(Actor::LocalUser, payload)?;
        Self::with_bound(self.admit()?, false, move |bound| {
            bound
                .registry
                .read_frozen_folder_revision(&local_import_access(&bound.profile_id), &input)
        })
        .await?
        .ok_or_else(|| failure(ErrorCode::SNAPSHOTUNAVAILABLE))
    }

    pub(super) async fn load_local_folder_source_row(
        &self,
        input: ReadLocalFolderSourceRowInput,
    ) -> Result<FrozenLocalSourceRow<ReadLocalFolderSourceRowInput>, Error> {
        Self::with_bound(self.admit()?, false, move |bound| {
            bound
                .registry
                .prepare_frozen_folder_source_row(&local_import_access(&bound.profile_id), input)
        })
        .await?
        .ok_or_else(|| failure(ErrorCode::SNAPSHOTUNAVAILABLE))
    }

    pub(super) async fn finish_local_folder_source_row(
        &self,
        source: FrozenLocalSourceRow<ReadLocalFolderSourceRowInput>,
        row: Value,
    ) -> Result<Value, Error> {
        Self::with_bound(self.admit()?, false, move |bound| {
            bound.registry.finish_frozen_folder_source_row(
                &local_import_access(&bound.profile_id),
                source,
                row,
            )
        })
        .await?
        .ok_or_else(|| failure(ErrorCode::SNAPSHOTUNAVAILABLE))
    }

    pub(super) async fn revoke_local_folder_revision(
        &self,
        payload: Value,
    ) -> Result<Value, Error> {
        let input = revoke_local_folder_revision_input(Actor::LocalUser, payload)?;
        Self::with_bound(self.admit()?, false, move |bound| {
            bound
                .registry
                .revoke_frozen_folder_revision(&local_import_access(&bound.profile_id), &input)
        })
        .await?
        .ok_or_else(|| failure(ErrorCode::SNAPSHOTUNAVAILABLE))
    }

    pub(super) async fn list_local_revisions(&self, payload: Value) -> Result<Value, Error> {
        let input = list_local_import_revisions_input(Actor::LocalUser, payload)?;
        let lane = self.admit().map_err(|error| {
            if error.code == ErrorCode::PERMISSIONDENIED {
                failure(ErrorCode::PROVIDERNOTCONFIGURED)
            } else {
                error
            }
        })?;
        let result = Self::with_bound(lane, false, move |bound| {
            bound
                .registry
                .list_frozen_local_datasets(&local_import_access(&bound.profile_id), &input)
        })
        .await?;
        Ok(result.unwrap_or_else(
            || json!({"version": 1, "revisions": [], "next_after_revision_id": null}),
        ))
    }

    pub(super) async fn read_local_revision(&self, payload: Value) -> Result<Value, Error> {
        let input = read_local_import_revision_input(Actor::LocalUser, payload)?;
        Self::with_bound(self.admit()?, false, move |bound| {
            bound
                .registry
                .read_frozen_local_dataset(&local_import_access(&bound.profile_id), &input)
        })
        .await?
        .ok_or_else(|| failure(ErrorCode::SNAPSHOTUNAVAILABLE))
    }

    pub(super) async fn load_local_source_row(
        &self,
        input: ReadLocalImportSourceRowInput,
    ) -> Result<FrozenLocalSourceRow, Error> {
        Self::with_bound(self.admit()?, false, move |bound| {
            bound
                .registry
                .prepare_frozen_local_source_row(&local_import_access(&bound.profile_id), input)
        })
        .await?
        .ok_or_else(|| failure(ErrorCode::SNAPSHOTUNAVAILABLE))
    }

    pub(super) async fn finish_local_source_row(
        &self,
        source: FrozenLocalSourceRow,
        row: Value,
    ) -> Result<Value, Error> {
        Self::with_bound(self.admit()?, false, move |bound| {
            bound.registry.finish_frozen_local_source_row(
                &local_import_access(&bound.profile_id),
                source,
                row,
            )
        })
        .await?
        .ok_or_else(|| failure(ErrorCode::SNAPSHOTUNAVAILABLE))
    }

    pub(super) async fn revoke_local_revision(&self, payload: Value) -> Result<Value, Error> {
        let input = revoke_local_import_revision_input(Actor::LocalUser, payload)?;
        Self::with_bound(self.admit()?, false, move |bound| {
            bound
                .registry
                .revoke_frozen_local_dataset(&local_import_access(&bound.profile_id), &input)
        })
        .await?
        .ok_or_else(|| failure(ErrorCode::SNAPSHOTUNAVAILABLE))
    }

    pub(super) async fn shutdown(&self) {
        let previous = {
            let mut lane = self.lane.lock().await;
            std::mem::replace(&mut *lane, Lane::Closed)
        };
        if let Lane::Open(bound) = previous {
            // SQLite may checkpoint the WAL while closing the owned connection.
            let _ = tokio::task::spawn_blocking(move || drop(bound)).await;
        }
    }
}

fn text_model_access(profile_id: &str) -> WorkspaceAccess<'_> {
    WorkspaceAccess {
        operations: &[AI_TEXT_CONNECTION],
        disclose_workspace_state: false,
        ..workspace_access(profile_id)
    }
}

fn openai_revision(
    credentials: &OpenAiCredentials,
    profile_id: &str,
) -> Result<Option<Uuid>, Error> {
    credentials
        .revision(profile_id)
        .map_err(credential_failure)?
        .as_deref()
        .map(parse_credential_revision)
        .transpose()
}

fn parse_credential_revision(revision: &str) -> Result<Uuid, Error> {
    Uuid::parse_str(revision).map_err(|_| failure(ErrorCode::INTERNALERROR))
}

pub(super) fn credential_failure(error: CredentialError) -> Error {
    let mut fault = failure(match error {
        CredentialError::InvalidSecret => ErrorCode::INVALIDARGUMENT,
        CredentialError::StoreUnavailable => ErrorCode::DATAUNAVAILABLE,
    });
    fault.message = error.to_string();
    fault
}

#[cfg(test)]
pub(crate) static NATIVE_TEST_PROFILE_SLOT: std::sync::Mutex<()> = std::sync::Mutex::new(());

#[cfg(test)]
std::thread_local! {
    pub(crate) static PROFILE_PREPARATION_ATTEMPT: std::cell::RefCell<Option<std::sync::mpsc::Sender<usize>>> =
        const { std::cell::RefCell::new(None) };
}

#[cfg(test)]
pub(crate) fn lock_test_profile_preparation(
    slot: &std::sync::Mutex<()>,
) -> std::sync::MutexGuard<'_, ()> {
    if let Some(observer) = PROFILE_PREPARATION_ATTEMPT.with_borrow_mut(Option::take) {
        let _ = observer.send(std::ptr::from_ref(slot).addr());
    }
    slot.lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}

#[cfg(test)]
pub(crate) fn lock_native_test_profile_preparation() -> Option<std::sync::MutexGuard<'static, ()>> {
    #[cfg(windows)]
    scopehub_persistence::use_workspace_acl_test_host("bridge::tests::workspace_acl_worker");

    // Windows test paths share the helper budget before starting either ACL call.
    // The integrated regression also exercises this admission path on other hosts.
    (cfg!(windows) || PROFILE_PREPARATION_ATTEMPT.with_borrow(Option::is_some))
        .then(|| lock_test_profile_preparation(&NATIVE_TEST_PROFILE_SLOT))
}

#[cfg(test)]
pub(crate) fn open_bound_workspace_for_test(path: &Path) -> Result<(), Error> {
    BoundWorkspace::open(path).map(drop)
}

impl BoundWorkspace {
    fn open(app_local_data: &Path) -> Result<Self, Error> {
        #[cfg(test)]
        let _preparation = lock_native_test_profile_preparation();
        let directory = app_local_data.join(WORKSPACE_DIRECTORY);
        SqliteWorkspace::prepare_profile_root(&directory)
            .map_err(|_| failure(ErrorCode::INTERNALERROR))?;
        let canonical = directory
            .canonicalize()
            .map_err(|_| failure(ErrorCode::INTERNALERROR))?;
        let native_path = canonical
            .to_str()
            .ok_or_else(|| failure(ErrorCode::INTERNALERROR))?;
        let digest = Sha256::digest(native_path.as_bytes());
        let profile_id = format!("desktop-development:{digest:x}");
        let store = SqliteWorkspace::open(
            &directory,
            WorkspaceBinding {
                profile_id: profile_id.clone(),
                workspace_id: WORKSPACE_ID.into(),
            },
        )
        .map_err(|_| failure(ErrorCode::INTERNALERROR))?;
        Ok(Self {
            registry: OperationRegistry::new(store),
            profile_id,
        })
    }

    fn dispatch(&mut self, request: Request) -> Result<Value, Error> {
        let access = workspace_access(&self.profile_id);
        match request {
            Request::Snapshot => {
                let snapshot = self.registry.workspace_snapshot(&access)?;
                Ok(json!({
                    "workspace": snapshot.workspace,
                    "selection_revision": snapshot.selection_revision,
                    "chart_evidence": snapshot.chart_evidence,
                    "layouts": snapshot.layouts_json(),
                    "source": snapshot.source_json(),
                    "undo": snapshot.undo.map(|target| json!({
                        "operation_id": target.operation_id,
                        "committed_workspace_revision": target.committed_workspace_revision,
                    })),
                }))
            }
            Request::WorkspaceSource(request) => {
                self.registry.select_workspace_source(&access, &request)
            }
            Request::FrozenChart(revision_id) => self
                .registry
                .open_evidence_chart(&local_import_access(&self.profile_id), &revision_id),
            Request::FrozenChartStatus(revision_id) => self.registry.evidence_chart_display_status(
                &local_import_access(&self.profile_id),
                &revision_id,
            ),
            Request::IndicatorPreviewStatus(task_id) => {
                self.registry.indicator_preview_status(&access, &task_id)
            }
            Request::IndicatorAcceptedRead(payload) => {
                self.registry.read_accepted_indicator(&access, &payload)
            }
            Request::SampleIndicatorPreview(command, page) => self
                .registry
                .execute_sample_indicator_preview(&access, command, page),
            Request::SampleEvidenceSelection(command, page) => self
                .registry
                .execute_sample_evidence_selection(&access, command, page),
            Request::Operation(payload) => self.registry.execute_workspace(&access, payload),
        }
    }
}

fn workspace_access(profile_id: &str) -> WorkspaceAccess<'_> {
    WorkspaceAccess {
        actor: Actor::User,
        profile_id,
        principal_id: profile_id,
        paired_client_id: None,
        workspace_id: WORKSPACE_ID,
        operations: LOCAL_WORKSPACE_OPERATIONS,
        disclose_workspace_state: true,
    }
}

fn local_import_access(profile_id: &str) -> WorkspaceAccess<'_> {
    WorkspaceAccess {
        actor: Actor::LocalUser,
        profile_id,
        principal_id: profile_id,
        paired_client_id: None,
        workspace_id: WORKSPACE_ID,
        operations: LOCAL_IMPORT_OPERATIONS,
        disclose_workspace_state: false,
    }
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use super::*;

    #[cfg(windows)]
    #[tokio::test]
    async fn native_workspace_provisions_an_absent_local_app_data_directory() {
        use std::{
            fs,
            path::PathBuf,
            time::{SystemTime, UNIX_EPOCH},
        };

        struct SyntheticAppData(PathBuf);
        impl Drop for SyntheticAppData {
            fn drop(&mut self) {
                if self.0.is_dir() {
                    fs::remove_dir_all(&self.0).unwrap();
                }
            }
        }

        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let app_data = SyntheticAppData(
            PathBuf::from(std::env::var_os("LOCALAPPDATA").unwrap()).join(format!(
                "ScopeHub ACL fixture {}-{stamp}",
                std::process::id()
            )),
        );
        assert!(!app_data.0.exists());
        for _ in 0..2 {
            let bridge = WorkspaceBridge::local(Some(&app_data.0), true);
            let snapshot = bridge
                .dispatch(None, json!({"type": "snapshot"}))
                .await
                .unwrap();
            assert_eq!(snapshot["workspace"]["timeframe"], "1m");
            assert!(
                app_data
                    .0
                    .join("workspace-dev")
                    .join("workspace.sqlite3")
                    .is_file()
            );
            bridge.shutdown().await;
        }
    }

    #[cfg(any(target_os = "linux", windows))]
    #[tokio::test]
    async fn native_text_setup_contention_is_retryable_before_registry_admission() {
        let profile = std::env::temp_dir().join(format!("scopehub-text-{}", uuid::Uuid::new_v4()));
        let bridge = WorkspaceBridge::local(Some(&profile), true);
        let command = |arguments| {
            json!({
                "protocol_version": "1.1", "type": "request", "timestamp_ms": 1763600000000_u64,
                "request_id": uuid::Uuid::new_v4().to_string(), "message_id": uuid::Uuid::new_v4().to_string(),
                "scope": {"workspace_id": WORKSPACE_ID},
                "intent": {"operation": AI_TEXT_CONNECTION, "arguments": arguments},
            })
        };
        let status = command(json!({"action": "status"}));
        let proposal = command(json!({"action": "propose", "route": {
            "kind": "ollama", "origin": "http://127.0.0.1:1", "model": "synthetic:start"
        }}));

        let startup = bridge.admit().unwrap();
        for request in [&status, &proposal] {
            let error = bridge
                .dispatch_text_connection(request.clone())
                .await
                .err()
                .expect("the held writer rejects before a registry outcome");
            assert_eq!(error.code, ErrorCode::BUSY);
            assert!(error.retryable);
        }
        WorkspaceBridge::with_bound(startup, true, |bound| bound.dispatch(Request::Snapshot))
            .await
            .unwrap()
            .expect("the initial workspace snapshot opens the writer");

        let (reply, work) = bridge.dispatch_text_connection(status).await.unwrap();
        assert_eq!(reply["outcome"]["result"]["action"], "status");
        assert!(reply["outcome"]["result"]["connection"].is_null());
        assert!(work.is_none());
        let (reply, work) = bridge.dispatch_text_connection(proposal).await.unwrap();
        assert_eq!(reply["outcome"]["result"]["action"], "propose");
        assert_eq!(
            reply["outcome"]["result"]["route"]["destination"],
            "http://127.0.0.1:1/api/chat"
        );
        assert!(work.is_none());
        bridge.shutdown().await;
        std::fs::remove_dir_all(profile).unwrap();
    }

    #[tokio::test]
    async fn shutdown_waits_for_the_admitted_slot_and_closes_the_lane() {
        let bridge = WorkspaceBridge::local(None, true);
        let admitted = bridge.lane.lock().await;
        let snapshot = json!({"type": "snapshot"});
        assert_eq!(
            bridge
                .dispatch(None, snapshot.clone())
                .await
                .unwrap_err()
                .code,
            ErrorCode::BUSY,
        );

        let shutdown = bridge.shutdown();
        tokio::pin!(shutdown);
        assert!(
            tokio::time::timeout(Duration::from_millis(20), &mut shutdown)
                .await
                .is_err()
        );
        drop(admitted);
        shutdown.await;
        assert_eq!(
            bridge.dispatch(None, snapshot).await.unwrap_err().code,
            ErrorCode::CANCELLED,
        );
    }
}
