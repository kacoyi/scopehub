use std::{
    fs,
    path::{Path, PathBuf},
};

use scopehub_application::{
    Actor, MAIN_CHART_ID, SET_TIMEFRAME, UNDO, WorkspaceAccess, WorkspaceBinding,
};
use scopehub_persistence::SqliteWorkspace;
use serde_json::{Value, json};
use uuid::Uuid;

pub const WORKSPACE_ID: &str = "workspace";
pub const PROFILE_ID: &str = "local-profile";

pub struct Profile(pub PathBuf);

impl Profile {
    pub fn new() -> Self {
        let temporary = std::env::temp_dir();
        #[cfg(unix)]
        let temporary = temporary.canonicalize().unwrap();
        let path = temporary.join(format!("scopehub-workspace-{}", Uuid::new_v4()));
        SqliteWorkspace::prepare_profile_root(&path).unwrap();
        Self(path)
    }

    pub fn sqlite(&self) -> rusqlite::Connection {
        rusqlite::Connection::open(self.0.join("workspace.sqlite3")).unwrap()
    }
}

pub fn store(root: &Path) -> SqliteWorkspace {
    SqliteWorkspace::open(root, binding(PROFILE_ID)).unwrap()
}

impl Drop for Profile {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.0).unwrap();
    }
}

pub fn binding(profile_id: &str) -> WorkspaceBinding {
    WorkspaceBinding {
        profile_id: profile_id.into(),
        workspace_id: WORKSPACE_ID.into(),
    }
}

pub fn access() -> WorkspaceAccess<'static> {
    WorkspaceAccess {
        actor: Actor::User,
        profile_id: PROFILE_ID,
        principal_id: PROFILE_ID,
        paired_client_id: None,
        workspace_id: WORKSPACE_ID,
        operations: &[SET_TIMEFRAME, UNDO],
        disclose_workspace_state: true,
    }
}

pub fn command(operation: &str, revision: u64, key: Uuid, arguments: Value) -> Value {
    json!({
        "protocol_version": "1.1",
        "type": "request",
        "message_id": Uuid::new_v4().to_string(),
        "timestamp_ms": 1234,
        "request_id": Uuid::new_v4().to_string(),
        "task_id": null,
        "scope": {
            "workspace_id": WORKSPACE_ID,
            "target": {"kind": "chart", "object_id": MAIN_CHART_ID},
            "expected_workspace_revision": revision,
            "expected_selection_revision": 0,
            "idempotency_key": key.to_string(),
        },
        "intent": {"operation": operation, "arguments": arguments},
    })
}

pub fn set(revision: u64, next: &str, key: Uuid) -> Value {
    command(SET_TIMEFRAME, revision, key, json!({"timeframe": next}))
}

pub fn undo(revision: u64, operation_id: &str, key: Uuid) -> Value {
    command(UNDO, revision, key, json!({"operation_id": operation_id}))
}

pub fn assert_failed(reply: &Value, code: &str) {
    assert_eq!(reply["outcome"]["state"], "failed", "{reply}");
    assert_eq!(reply["outcome"]["error"]["code"], code, "{reply}");
    assert_eq!(reply["outcome"].as_object().unwrap().len(), 2, "{reply}");
    assert_eq!(
        reply["outcome"]["error"].as_object().unwrap().len(),
        3,
        "{reply}"
    );
}

pub fn assert_committed<'a>(reply: &'a Value, revision: u64, timeframe: &str) -> &'a str {
    assert_eq!(reply["outcome"]["state"], "committed", "{reply}");
    let result = &reply["outcome"]["result"];
    let receipt = &reply["outcome"]["receipt"];
    assert_eq!(result["timeframe"], timeframe);
    assert_eq!(result["revision"], revision);
    assert_eq!(receipt["committed_workspace_revision"], revision);
    assert_eq!(receipt["workspace_id"], WORKSPACE_ID);
    assert_eq!(
        receipt["target"],
        json!({"kind": "chart", "object_id": MAIN_CHART_ID})
    );
    receipt["operation_id"].as_str().unwrap()
}
