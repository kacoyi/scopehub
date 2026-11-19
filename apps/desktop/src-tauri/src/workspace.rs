//! Native profile binding and admission for the existing SQLite workspace writer.

use std::{
    path::{Path, PathBuf},
    sync::Arc,
};

use scopehub_application::{
    Actor, LOAD_LOCAL_IMPORT_MAPPING, MappingCommitGuard, OperationRegistry, ReviewedImportMapping,
    SAVE_LOCAL_IMPORT_MAPPING, WorkspaceAccess, WorkspaceBinding, failure,
};
use scopehub_domain::protocol::{CsvMappingV1, Error, ErrorCode};
use scopehub_persistence::SqliteWorkspace;
use serde_json::Value;
use sha2::{Digest, Sha256};
use tokio::sync::Mutex;

const WORKSPACE_ID: &str = "main";
const WORKSPACE_DIRECTORY: &str = "workspace-dev";
const MAPPING_OPERATIONS: &[&str] = &[LOAD_LOCAL_IMPORT_MAPPING, SAVE_LOCAL_IMPORT_MAPPING];

pub(super) struct WorkspaceBridge {
    lane: Arc<Mutex<Lane>>,
}

enum Lane {
    Disabled,
    Pending(PathBuf),
    Open(BoundWorkspace),
    Closed,
}

struct BoundWorkspace {
    registry: OperationRegistry<SqliteWorkspace>,
    profile_id: String,
}

impl WorkspaceBridge {
    pub(super) fn development(app_local_data: Option<&Path>) -> Self {
        // The native launcher owns this path. A sample or preview-only session
        // never opens the database.
        let lane = app_local_data.map_or(Lane::Disabled, |path| Lane::Pending(path.to_owned()));
        Self {
            lane: Arc::new(Mutex::new(lane)),
        }
    }

    async fn with_bound<T: Send + 'static>(
        &self,
        create: bool,
        run: impl FnOnce(&mut BoundWorkspace) -> Result<T, Error> + Send + 'static,
    ) -> Result<Option<T>, Error> {
        let mut lane = Arc::clone(&self.lane)
            .try_lock_owned()
            .map_err(|_| failure(ErrorCode::BUSY))?;
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
                *lane = Lane::Open(BoundWorkspace::open(path)?);
            }
            match &mut *lane {
                Lane::Open(bound) => run(bound).map(Some),
                Lane::Disabled => Err(failure(ErrorCode::PERMISSIONDENIED)),
                Lane::Closed => Err(failure(ErrorCode::CANCELLED)),
                Lane::Pending(_) => unreachable!("workspace opening completed"),
            }
        })
        .await
        .map_err(|_| failure(ErrorCode::INTERNALERROR))?
    }

    pub(super) async fn find(
        &self,
        source_key: String,
    ) -> Result<Option<ReviewedImportMapping>, Error> {
        self.with_bound(false, move |bound| {
            bound
                .registry
                .find_reviewed_mapping(&mapping_access(&bound.profile_id), &source_key)
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
    ) -> Result<Value, Error> {
        self.with_bound(true, move |bound| {
            bound.registry.save_reviewed_mapping(
                &mapping_access(&bound.profile_id),
                &source_key,
                &header_sha256,
                &mapping,
                &guard,
            )
        })
        .await?
        .ok_or_else(|| failure(ErrorCode::INTERNALERROR))
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
}

fn mapping_access(profile_id: &str) -> WorkspaceAccess<'_> {
    WorkspaceAccess {
        actor: Actor::LocalUser,
        profile_id,
        principal_id: profile_id,
        paired_client_id: None,
        workspace_id: WORKSPACE_ID,
        operations: MAPPING_OPERATIONS,
        disclose_workspace_state: false,
    }
}
