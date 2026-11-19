//! Local development stdio composition for an explicitly enabled workspace.
//! Only the trusted launcher supplies the profile directory and identities.

use std::{
    io::{self, BufRead, Read, Write},
    path::Path,
    process::ExitCode,
};

use scopehub_application::{
    Actor, DECISION_NOTE_OPERATIONS, DECISION_NOTE_PREVIEW_RESTORE, LOCAL_WORKSPACE_OPERATIONS,
    OperationRegistry, PRIVATE_RECORD_CHANGE, READ_LOCAL_IMPORT_REVISION, WorkspaceAccess,
    WorkspaceBinding, evidence_operation_name, failure,
};
use scopehub_domain::protocol::{Error, ErrorCode};
use scopehub_persistence::SqliteWorkspace;
use serde_json::{Value, json};

const MAX_REQUEST_BYTES: u64 = 16 * 1024;
const MAX_NOTE_REQUEST_BYTES: u64 = 32 * 1024;
const MAX_BACKUP_REQUEST_BYTES: u64 = 1024 * 1024;

fn request_limit(operation: Option<&str>) -> u64 {
    match operation {
        Some(DECISION_NOTE_PREVIEW_RESTORE | PRIVATE_RECORD_CHANGE) => MAX_BACKUP_REQUEST_BYTES,
        Some(operation) if DECISION_NOTE_OPERATIONS.contains(&operation) => MAX_NOTE_REQUEST_BYTES,
        _ => MAX_REQUEST_BYTES,
    }
}

fn result_frame(result: Result<Value, Error>) -> Value {
    match result {
        Ok(result) => json!({"ok": true, "result": result}),
        Err(error) => json!({"ok": false, "error": error}),
    }
}

fn write_frame(output: &mut impl Write, frame: Value) -> io::Result<()> {
    writeln!(output, "{frame}")?;
    output.flush()
}

fn snapshot(
    registry: &OperationRegistry<SqliteWorkspace>,
    access: &WorkspaceAccess<'_>,
) -> Result<Value, Error> {
    let state = registry.workspace_snapshot(access)?;
    Ok(json!({
        "workspace": state.workspace,
        "selection_revision": state.selection_revision,
        "chart_evidence": state.chart_evidence,
        "undo": state.undo.map(|target| json!({
            "operation_id": target.operation_id,
            "committed_workspace_revision": target.committed_workspace_revision,
        })),
    }))
}

fn snapshot_request_id(request: &Value) -> Option<u64> {
    let object = request.as_object()?;
    let id = object.get("request_id")?.as_u64()?;
    (object.len() == 2 && (1..=9_007_199_254_740_991).contains(&id)).then_some(id)
}

fn serve(
    registry: &mut OperationRegistry<SqliteWorkspace>,
    access: &WorkspaceAccess<'_>,
    mut input: impl BufRead,
    mut output: impl Write,
) -> io::Result<()> {
    let opened = snapshot(registry, access);
    let failed = opened.is_err();
    let mut startup = result_frame(opened);
    if !failed {
        // SqliteWorkspace::open has verified the profile and private file
        // inheritance before the launcher may issue a local capability.
        startup["private_profile"] = json!(true);
    }
    write_frame(&mut output, startup)?;
    if failed {
        return Err(io::Error::other("Workspace state could not be restored."));
    }

    let mut line = Vec::new();
    loop {
        line.clear();
        let count = input
            .by_ref()
            .take(MAX_BACKUP_REQUEST_BYTES + 1)
            .read_until(b'\n', &mut line)?;
        if count == 0 {
            return Ok(());
        }
        if count as u64 > MAX_BACKUP_REQUEST_BYTES {
            return write_frame(
                &mut output,
                result_frame(Err(failure(ErrorCode::RESOURCELIMIT))),
            );
        }
        if !line.ends_with(b"\n") {
            return write_frame(
                &mut output,
                result_frame(Err(failure(ErrorCode::INVALIDARGUMENT))),
            );
        }
        let request =
            serde_json::from_slice::<Value>(&line).map_err(|_| failure(ErrorCode::INVALIDARGUMENT));
        let operation = request.as_ref().ok().and_then(|request| {
            evidence_operation_name(request).or_else(|| request["intent"]["operation"].as_str())
        });
        if count as u64 > request_limit(operation) {
            return write_frame(
                &mut output,
                result_frame(Err(failure(ErrorCode::RESOURCELIMIT))),
            );
        }
        if let Ok(request) = &request
            && request["type"] == "snapshot"
        {
            // Private stdio control frame, separate from registered 1.1 commands.
            if let Some(id) = snapshot_request_id(request) {
                let mut frame = result_frame(snapshot(registry, access));
                frame["request_id"] = json!(id);
                write_frame(&mut output, frame)?;
            } else {
                write_frame(
                    &mut output,
                    result_frame(Err(failure(ErrorCode::INVALIDARGUMENT))),
                )?;
            }
        } else if let Ok(request) = &request
            && matches!(
                request["type"].as_str(),
                Some("frozen_chart" | "frozen_chart_status")
            )
        {
            let result = match request["revision_id"].as_str() {
                Some(id)
                    if request.as_object().is_some_and(|obj| obj.len() == 3)
                        && request["request_id"].as_str().is_some() =>
                {
                    let display = WorkspaceAccess {
                        actor: Actor::LocalUser,
                        operations: &[READ_LOCAL_IMPORT_REVISION],
                        ..*access
                    };
                    if request["type"] == "frozen_chart_status" {
                        registry
                            .evidence_chart_display_allowed(&display, id)
                            .map(|available| json!({"available": available}))
                    } else {
                        registry.open_evidence_chart(&display, id)
                    }
                }
                _ => Err(failure(ErrorCode::INVALIDARGUMENT)),
            };
            let mut frame = result_frame(result);
            frame["request_id"] = request["request_id"].clone();
            write_frame(&mut output, frame)?;
        } else {
            let result = request.and_then(|request| registry.execute_workspace(access, request));
            write_frame(&mut output, result_frame(result))?;
        }
    }
}

fn main() -> ExitCode {
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    if let [flag, root] = args.as_slice()
        && flag == "--prepare-profile-root"
    {
        return match SqliteWorkspace::prepare_profile_root(Path::new(root)) {
            Ok(()) => ExitCode::SUCCESS,
            Err(error) => {
                eprintln!("{error}");
                ExitCode::FAILURE
            }
        };
    }
    let [root, profile, workspace] = args.as_slice() else {
        eprintln!("Usage: workspace_bridge <absolute-profile-root> <profile-id> <workspace-id>");
        return ExitCode::FAILURE;
    };
    let (Some(profile_id), Some(workspace_id)) = (profile.to_str(), workspace.to_str()) else {
        eprintln!("Profile and workspace identities must be UTF-8.");
        return ExitCode::FAILURE;
    };
    let binding = WorkspaceBinding {
        profile_id: profile_id.into(),
        workspace_id: workspace_id.into(),
    };
    let store = match SqliteWorkspace::open(Path::new(root), binding) {
        Ok(store) => store,
        Err(error) => {
            eprintln!("{error}");
            return ExitCode::FAILURE;
        }
    };
    let mut registry = OperationRegistry::new(store);
    let access = WorkspaceAccess {
        actor: Actor::User,
        profile_id,
        principal_id: profile_id,
        paired_client_id: None,
        workspace_id,
        operations: LOCAL_WORKSPACE_OPERATIONS,
        disclose_workspace_state: true,
    };
    let io = serve(
        &mut registry,
        &access,
        io::stdin().lock(),
        io::stdout().lock(),
    );
    if io.is_err() {
        eprintln!("Workspace bridge could not complete the local exchange.");
        return ExitCode::FAILURE;
    }
    ExitCode::SUCCESS
}
