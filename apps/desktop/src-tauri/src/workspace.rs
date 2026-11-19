use std::{path::Path, sync::Arc};

use scopehub_application::{
    Actor, OperationRegistry, SET_TIMEFRAME, UNDO, WorkspaceAccess, WorkspaceBinding, failure,
};
use scopehub_domain::protocol::{Error, ErrorCode};
use scopehub_persistence::SqliteWorkspace;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use tokio::sync::Mutex;

const WORKSPACE_ID: &str = "main";
const MAX_REQUEST_BYTES: usize = 16 * 1024;

pub(super) struct WorkspaceBridge {
    lane: Arc<Mutex<Lane>>,
}

enum Lane {
    Disabled,
    Unavailable,
    Open(BoundWorkspace),
    Closed,
}

enum Request {
    Snapshot,
    Mutation(Value),
}

struct BoundWorkspace {
    registry: OperationRegistry<SqliteWorkspace>,
    profile_id: String,
}

impl WorkspaceBridge {
    fn new(lane: Lane) -> Self {
        Self {
            lane: Arc::new(Mutex::new(lane)),
        }
    }

    pub(super) fn disabled() -> Self {
        Self::new(Lane::Disabled)
    }

    pub(super) fn development(app_local_data: Option<&Path>) -> Self {
        // This switch is a host decision for the current run. The database
        // provides workspace state and receipts, never a grant.
        let lane = app_local_data
            .and_then(BoundWorkspace::open)
            .map_or(Lane::Unavailable, Lane::Open);
        Self::new(lane)
    }

    pub(super) async fn dispatch(
        &self,
        operation: Option<&str>,
        payload: Value,
    ) -> Result<Value, Error> {
        // Claim one slot before queueing blocking work. Once admitted, the
        // transaction owns its outcome even if the WebView goes away.
        let mut lane = Arc::clone(&self.lane)
            .try_lock_owned()
            .map_err(|_| failure(ErrorCode::BUSY))?;
        match &*lane {
            Lane::Disabled => return Err(failure(ErrorCode::PERMISSIONDENIED)),
            Lane::Unavailable => return Err(failure(ErrorCode::INTERNALERROR)),
            Lane::Closed => return Err(failure(ErrorCode::CANCELLED)),
            Lane::Open(_) => {}
        }
        let request = match operation {
            None if payload == json!({"type": "snapshot"}) => Request::Snapshot,
            Some(operation)
                if payload["type"] == "request" && payload["intent"]["operation"] == operation =>
            {
                if payload.to_string().len() >= MAX_REQUEST_BYTES {
                    return Err(failure(ErrorCode::RESOURCELIMIT));
                }
                Request::Mutation(payload)
            }
            _ => return Err(failure(ErrorCode::INVALIDARGUMENT)),
        };
        tokio::task::spawn_blocking(move || match &mut *lane {
            Lane::Open(bound) => bound.dispatch(request),
            _ => unreachable!("the admitted slot owns the open workspace"),
        })
        .await
        .map_err(|_| failure(ErrorCode::INTERNALERROR))?
    }

    pub(super) async fn shutdown(&self) {
        let previous = {
            let mut lane = self.lane.lock().await;
            std::mem::replace(&mut *lane, Lane::Closed)
        };
        if let Lane::Open(bound) = previous {
            // SQLite can checkpoint the WAL as its owned connection closes.
            let _ = tokio::task::spawn_blocking(move || drop(bound)).await;
        }
    }
}

impl BoundWorkspace {
    fn open(app_local_data: &Path) -> Option<Self> {
        if !app_local_data.is_absolute() {
            return None;
        }
        let directory = app_local_data.join("workspace-dev");
        SqliteWorkspace::prepare_profile_root(&directory).ok()?;
        let canonical = directory.canonicalize().ok()?;
        // Separate development profiles bound to different native data
        // directories. Keep the filesystem path out of storage and replies.
        let digest = Sha256::digest(canonical.to_str()?.as_bytes());
        let profile_id = format!("desktop-development:{digest:x}");
        let store = SqliteWorkspace::open(
            &directory,
            WorkspaceBinding {
                profile_id: profile_id.clone(),
                workspace_id: WORKSPACE_ID.into(),
            },
        )
        .ok()?;
        Some(Self {
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
            operations: &[SET_TIMEFRAME, UNDO],
            disclose_workspace_state: true,
        };
        match request {
            Request::Snapshot => {
                let snapshot = self.registry.workspace_snapshot(&access)?;
                Ok(json!({
                    "workspace": snapshot.workspace,
                    "selection_revision": snapshot.selection_revision,
                    "undo": snapshot.undo.map(|target| json!({
                        "operation_id": target.operation_id,
                        "committed_workspace_revision": target.committed_workspace_revision,
                    })),
                }))
            }
            Request::Mutation(payload) => self.registry.execute_workspace(&access, payload),
        }
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
            let bridge = WorkspaceBridge::development(Some(&app_data.0));
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
        let bridge = WorkspaceBridge::disabled();
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
