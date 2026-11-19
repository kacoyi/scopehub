//! Local development stdio composition for an explicitly enabled workspace.
//! Only the trusted launcher supplies the profile directory and identities.

use std::{
    io::{self, BufRead, Read, Write},
    path::Path,
    process::ExitCode,
};

use scopehub_application::{
    Actor, OperationRegistry, SET_TIMEFRAME, UNDO, WorkspaceAccess, WorkspaceBinding, failure,
};
use scopehub_domain::protocol::{Error, ErrorCode};
use scopehub_persistence::SqliteWorkspace;
use serde_json::{Value, json};

const MAX_REQUEST_BYTES: u64 = 16 * 1024;

fn write_frame(output: &mut impl Write, result: Result<Value, Error>) -> io::Result<()> {
    let frame = match result {
        Ok(result) => json!({"ok": true, "result": result}),
        Err(error) => json!({"ok": false, "error": error}),
    };
    writeln!(output, "{frame}")?;
    output.flush()
}

fn serve(
    registry: &mut OperationRegistry<SqliteWorkspace>,
    access: &WorkspaceAccess<'_>,
    mut input: impl BufRead,
    mut output: impl Write,
) -> io::Result<()> {
    let snapshot = registry.workspace_snapshot(access);
    let failed = snapshot.is_err();
    write_frame(
        &mut output,
        snapshot.map(|snapshot| {
            json!({
                "workspace": snapshot.workspace,
                "selection_revision": snapshot.selection_revision,
                "undo": snapshot.undo.map(|target| json!({
                    "operation_id": target.operation_id,
                    "committed_workspace_revision": target.committed_workspace_revision,
                })),
            })
        }),
    )?;
    if failed {
        return Err(io::Error::other("Workspace state could not be restored."));
    }

    let mut line = Vec::new();
    loop {
        line.clear();
        let count = input
            .by_ref()
            .take(MAX_REQUEST_BYTES + 1)
            .read_until(b'\n', &mut line)?;
        if count == 0 {
            return Ok(());
        }
        if count as u64 > MAX_REQUEST_BYTES {
            return write_frame(&mut output, Err(failure(ErrorCode::RESOURCELIMIT)));
        }
        if !line.ends_with(b"\n") {
            return write_frame(&mut output, Err(failure(ErrorCode::INVALIDARGUMENT)));
        }
        let result = serde_json::from_slice(&line)
            .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))
            .and_then(|request| registry.execute_workspace(access, request));
        write_frame(&mut output, result)?;
    }
}

fn main() -> ExitCode {
    let args: Vec<_> = std::env::args_os().skip(1).collect();
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
        operations: &[SET_TIMEFRAME, UNDO],
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
