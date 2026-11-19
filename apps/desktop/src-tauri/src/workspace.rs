//! Native profile binding and admission for the existing SQLite workspace writer.

use std::{
    path::{Path, PathBuf},
    sync::Arc,
};

use scopehub_application::{
    Actor, DECISION_NOTE_OPERATIONS, DECISION_NOTE_PREVIEW_RESTORE, FolderManifestAnchor,
    FolderRefreshBase, LIST_LOCAL_FOLDER_REVISIONS, LIST_LOCAL_IMPORT_REVISIONS,
    LOAD_LOCAL_IMPORT_MAPPING, LOCAL_WORKSPACE_OPERATIONS, LocalDatasetPublication,
    MappingCommitGuard, OperationRegistry, PREPARE_LOCAL_FOLDER_PUBLICATION,
    PREVIEW_LOCAL_FOLDER_REFRESH, PRIVATE_RECORD_CHANGE, PUBLISH_LOCAL_FOLDER_REFRESH,
    PUBLISH_LOCAL_IMPORT, READ_LOCAL_FOLDER_REVISION, READ_LOCAL_IMPORT_REVISION,
    REVOKE_LOCAL_FOLDER_REVISION, REVOKE_LOCAL_IMPORT_REVISION, ReviewedImportMapping,
    SAVE_LOCAL_IMPORT_MAPPING, WorkspaceAccess, WorkspaceBinding, evidence_operation_name, failure,
    list_local_folder_revisions_input, list_local_import_revisions_input,
    local_mapping_fingerprint, read_local_folder_revision_input, read_local_import_revision_input,
    revoke_local_folder_revision_input, revoke_local_import_revision_input,
};
use scopehub_domain::protocol::{CsvMappingV1, Error, ErrorCode};
use scopehub_persistence::SqliteWorkspace;
use scopehub_platform::local_folder::FrozenFile;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use tokio::sync::{Mutex, OwnedMutexGuard};

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
    REVOKE_LOCAL_IMPORT_REVISION,
    PREPARE_LOCAL_FOLDER_PUBLICATION,
    PUBLISH_LOCAL_FOLDER_REFRESH,
    LIST_LOCAL_FOLDER_REVISIONS,
    READ_LOCAL_FOLDER_REVISION,
    REVOKE_LOCAL_FOLDER_REVISION,
];
const MAX_REQUEST_BYTES: usize = 16 * 1024;
const MAX_NOTE_REQUEST_BYTES: usize = 32 * 1024;
const MAX_BACKUP_REQUEST_BYTES: usize = 1024 * 1024;

pub(super) struct WorkspaceBridge {
    lane: Arc<Mutex<Lane>>,
    workspace_enabled: bool,
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
    FrozenChart(String),
    FrozenChartStatus(String),
    IndicatorPreviewStatus(String),
    Operation(Value),
}

struct BoundWorkspace {
    registry: OperationRegistry<SqliteWorkspace>,
    profile_id: String,
}

impl WorkspaceBridge {
    pub(super) fn development(app_local_data: Option<&Path>, workspace_enabled: bool) -> Self {
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
                        || operation == scopehub_application::CANCEL_INDICATOR_TASK
                            && payload["operation"] == operation) =>
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

impl BoundWorkspace {
    fn open(app_local_data: &Path) -> Result<Self, Error> {
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
        let access = WorkspaceAccess {
            actor: Actor::User,
            profile_id: &self.profile_id,
            principal_id: &self.profile_id,
            paired_client_id: None,
            workspace_id: WORKSPACE_ID,
            operations: LOCAL_WORKSPACE_OPERATIONS,
            disclose_workspace_state: true,
        };
        match request {
            Request::Snapshot => {
                let snapshot = self.registry.workspace_snapshot(&access)?;
                Ok(json!({
                    "workspace": snapshot.workspace,
                    "selection_revision": snapshot.selection_revision,
                    "chart_evidence": snapshot.chart_evidence,
                    "undo": snapshot.undo.map(|target| json!({
                        "operation_id": target.operation_id,
                        "committed_workspace_revision": target.committed_workspace_revision,
                    })),
                }))
            }
            Request::FrozenChart(revision_id) => self
                .registry
                .open_evidence_chart(&local_import_access(&self.profile_id), &revision_id),
            Request::FrozenChartStatus(revision_id) => {
                let available = self.registry.evidence_chart_display_allowed(
                    &local_import_access(&self.profile_id),
                    &revision_id,
                )?;
                Ok(json!({"available": available}))
            }
            Request::IndicatorPreviewStatus(task_id) => {
                self.registry.indicator_preview_status(&access, &task_id)
            }
            Request::Operation(payload) => self.registry.execute_workspace(&access, payload),
        }
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
            let bridge = WorkspaceBridge::development(Some(&app_data.0), true);
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

    #[tokio::test]
    async fn shutdown_waits_for_the_admitted_slot_and_closes_the_lane() {
        let bridge = WorkspaceBridge::development(None, true);
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
