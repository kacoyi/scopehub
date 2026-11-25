//! Local development stdio composition for an explicitly enabled workspace.
//! Only the trusted launcher supplies the profile directory and identities.

use std::{
    io::{self, BufRead, Read, Write},
    num::NonZeroU8,
    path::Path,
    process::ExitCode,
};

use scopehub_application::{
    AI_TEXT_CONNECTION, Actor, DECISION_NOTE_OPERATIONS, DECISION_NOTE_PREVIEW_RESTORE,
    LOCAL_WORKSPACE_OPERATIONS, OperationRegistry, PRIVATE_RECORD_CHANGE,
    READ_LOCAL_IMPORT_REVISION, TextModelProvider, TextModelWork, WorkspaceAccess,
    WorkspaceBinding, evidence_operation_name, failure,
};
use scopehub_domain::{
    protocol::{Error, ErrorCode},
    wire::max_message_bytes,
};
use scopehub_persistence::SqliteWorkspace;
use serde_json::{Value, json};

const MAX_REQUEST_BYTES: u64 = 16 * 1024;
const MAX_NOTE_REQUEST_BYTES: u64 = 32 * 1024;
const MAX_BACKUP_REQUEST_BYTES: u64 = 1024 * 1024;
const MAX_SAMPLE_REQUEST_BYTES: u64 = 256 * 1024;
// A 64 KiB text result can expand sixfold through JSON escapes, plus the
// responding model identity and the ordinary poll command.
const MAX_TEXT_RESULT_BYTES: u64 = 512 * 1024;

fn request_limit(request: Option<&Value>) -> u64 {
    if request.is_some_and(|frame| frame["type"] == "ai_text_result") {
        return MAX_TEXT_RESULT_BYTES;
    }
    let operation = request.and_then(|request| {
        evidence_operation_name(request).or_else(|| request["intent"]["operation"].as_str())
    });
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
        "layouts": state.layouts_json(),
        "source": state.source_json(),
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

fn report_cancelled_model(frame: &mut Value, model_work: &mut Option<(uuid::Uuid, TextModelWork)>) {
    if model_work
        .as_ref()
        .is_some_and(|(_, work)| work.is_cancelled())
    {
        *model_work = None;
        frame["text_model_cancelled"] = json!(true);
    }
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
    let mut model_work: Option<(uuid::Uuid, TextModelWork)> = None;
    loop {
        line.clear();
        let count = input
            .by_ref()
            .take(max_message_bytes() as u64 + 1)
            .read_until(b'\n', &mut line)?;
        if count == 0 {
            return Ok(());
        }
        if count > max_message_bytes() {
            if !line.ends_with(b"\n") {
                // Drain the rest without buffering it before reading another frame.
                input.skip_until(b'\n')?;
            }
            write_frame(
                &mut output,
                result_frame(Err(failure(ErrorCode::RESOURCELIMIT))),
            )?;
            continue;
        }
        if !line.ends_with(b"\n") {
            return write_frame(
                &mut output,
                result_frame(Err(failure(ErrorCode::INVALIDARGUMENT))),
            );
        }
        let request =
            serde_json::from_slice::<Value>(&line).map_err(|_| failure(ErrorCode::INVALIDARGUMENT));
        if request.as_ref().is_ok_and(|frame| {
            matches!(
                frame["type"].as_str(),
                Some("indicator_sample_preview" | "sample_evidence_selection")
            )
        }) {
            // The authenticated local preview host assembles this private frame.
            // Browser requests never supply the history or the wrapper.
            let mut request = request.expect("private frame");
            let select_sample = request["type"] == "sample_evidence_selection";
            let result = if count as u64 > MAX_SAMPLE_REQUEST_BYTES {
                Err(failure(ErrorCode::RESOURCELIMIT))
            } else if let Some(frame) = request.as_object_mut()
                && frame.len() == 3
                && frame.contains_key("history")
                && frame.contains_key("command")
            {
                let command = frame.remove("command").expect("command member");
                let history = frame.remove("history").expect("history member");
                if command.to_string().len() as u64 >= MAX_REQUEST_BYTES {
                    Err(failure(ErrorCode::RESOURCELIMIT))
                } else {
                    if select_sample {
                        registry.execute_sample_evidence_selection(access, command, history)
                    } else {
                        registry.execute_sample_indicator_preview(access, command, history)
                    }
                }
            } else {
                Err(failure(ErrorCode::INVALIDARGUMENT))
            };
            let mut frame = result_frame(result);
            report_cancelled_model(&mut frame, &mut model_work);
            write_frame(&mut output, frame)?;
            continue;
        }
        if request
            .as_ref()
            .is_ok_and(|frame| frame["type"] == "indicator_python_result")
        {
            let request = request.expect("private frame");
            let result = (|| {
                let task_id = request["task_id"]
                    .as_str()
                    .filter(|id| uuid::Uuid::parse_str(id).is_ok())
                    .ok_or_else(|| failure(ErrorCode::INVALIDARGUMENT))?;
                if !request.as_object().is_some_and(|frame| frame.len() == 4)
                    || !request["request_id"]
                        .as_str()
                        .is_some_and(|id| uuid::Uuid::parse_str(id).is_ok())
                {
                    return Err(failure(ErrorCode::INVALIDARGUMENT));
                }
                let worker = &request["worker"];
                let finished = if worker.get("error").is_some() {
                    Err(serde_json::from_value(worker["error"].clone())
                        .unwrap_or_else(|_| failure(ErrorCode::OUTPUTINVALID)))
                } else {
                    Ok(worker.clone())
                };
                registry.finish_trusted_python(access, task_id, finished)?;
                registry.indicator_preview_status(access, task_id)
            })();
            let mut frame = result_frame(result);
            frame["request_id"] = request["request_id"].clone();
            write_frame(&mut output, frame)?;
            continue;
        }
        if count as u64 > request_limit(request.as_ref().ok()) {
            write_frame(
                &mut output,
                result_frame(Err(failure(ErrorCode::RESOURCELIMIT))),
            )?;
            continue;
        }
        if let Ok(request) = &request
            && request["type"] == "ai_openai_credential_changed"
        {
            // The authenticated preview owns the session key. The writer
            // receives only its opaque revision on this private pipe.
            let result = (|| {
                let invalid = || failure(ErrorCode::INVALIDARGUMENT);
                let (_, revision) = request
                    .as_object()
                    .filter(|frame| frame.len() == 2)
                    .and_then(|frame| frame.get_key_value("revision"))
                    .ok_or_else(invalid)?;
                let revision = if revision.is_null() {
                    None
                } else {
                    Some(
                        revision
                            .as_str()
                            .and_then(|id| uuid::Uuid::parse_str(id).ok())
                            .ok_or_else(invalid)?,
                    )
                };
                registry.refresh_openai_credential(access, revision)?;
                if model_work
                    .as_ref()
                    .is_some_and(|(_, work)| work.is_cancelled())
                {
                    model_work = None;
                }
                Ok(Value::Null)
            })();
            write_frame(&mut output, result_frame(result))?;
        } else if let Ok(request) = &request
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
                Some("indicator_accepted_layers" | "indicator_accepted_output")
            )
        {
            let mut frame = result_frame(registry.read_accepted_indicator(access, request));
            frame["request_id"] = request["request_id"].clone();
            write_frame(&mut output, frame)?;
        } else if let Ok(request) = &request
            && request["type"] == "indicator_preview_status"
        {
            let result = match (request["request_id"].as_str(), request["task_id"].as_str()) {
                (Some(id), Some(task_id))
                    if request.as_object().is_some_and(|object| object.len() == 3)
                        && uuid::Uuid::parse_str(id).is_ok()
                        && uuid::Uuid::parse_str(task_id).is_ok() =>
                {
                    registry.indicator_preview_status(access, task_id)
                }
                _ => Err(failure(ErrorCode::INVALIDARGUMENT)),
            };
            let mut frame = result_frame(result);
            frame["request_id"] = request["request_id"].clone();
            write_frame(&mut output, frame)?;
        } else if let Ok(request) = &request
            && request["type"] == "workspace_source"
        {
            let mut frame = result_frame(registry.select_workspace_source(access, request));
            report_cancelled_model(&mut frame, &mut model_work);
            frame["request_id"] = request["request_id"].clone();
            write_frame(&mut output, frame)?;
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
                        registry.evidence_chart_display_status(&display, id)
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
            let request = request.and_then(|mut request| {
                if request["type"] != "ai_text_result" {
                    return Ok(request);
                }
                // This carrier is assembled only by the trusted preview host.
                // The browser can poll the turn, but supplies no worker result.
                let frame = request
                    .as_object_mut()
                    .filter(|frame| frame.len() == 3)
                    .ok_or_else(|| failure(ErrorCode::INVALIDARGUMENT))?;
                let command = frame
                    .remove("command")
                    .filter(|command| {
                        command["intent"]["operation"] == AI_TEXT_CONNECTION
                            && command["intent"]["arguments"]["action"] == "poll"
                    })
                    .ok_or_else(|| failure(ErrorCode::INVALIDARGUMENT))?;
                if command.to_string().len() as u64 >= MAX_REQUEST_BYTES {
                    return Err(failure(ErrorCode::RESOURCELIMIT));
                }
                let id = command["intent"]["arguments"]["turn_id"]
                    .as_str()
                    .and_then(|id| uuid::Uuid::parse_str(id).ok())
                    .ok_or_else(|| failure(ErrorCode::INVALIDARGUMENT))?;
                let result = frame
                    .remove("worker")
                    .ok_or_else(|| failure(ErrorCode::INVALIDARGUMENT))?;
                if model_work
                    .as_ref()
                    .is_some_and(|(pending, _)| *pending == id)
                {
                    let (_, work) = model_work.take().expect("matched text worker");
                    let result = match result.get("error") {
                        Some(error) => Err(serde_json::from_value(error.clone())
                            .unwrap_or_else(|_| failure(ErrorCode::OUTPUTINVALID))),
                        None => Ok(result),
                    };
                    work.finish(result);
                }
                Ok(command)
            });
            let result = request.and_then(|request| registry.execute_workspace(access, request));
            let next_model_work = if let Ok(reply) = &result
                && reply["outcome"]["operation"] == AI_TEXT_CONNECTION
                && let result = &reply["outcome"]["result"]
                && (result["action"] == "send"
                    || result["action"] == "poll" && result["turn"]["state"] == "running")
            {
                let turn_id = result["turn_id"]
                    .as_str()
                    .and_then(|id| uuid::Uuid::parse_str(id).ok())
                    .ok_or_else(|| io::Error::other("Invalid text turn identity."))?;
                registry
                    .take_text_model_work(access, turn_id)
                    .map_err(|_| io::Error::other("Text turn handoff failed."))?
                    .map(|work| (turn_id, work))
            } else {
                None
            };
            let work = match &result {
                Ok(reply) => reply["outcome"]["result"]["task_id"].as_str(),
                Err(_) => None,
            }
            .and_then(|task| registry.take_approved_python(access, task).ok().flatten());
            let mut frame = result_frame(result);
            report_cancelled_model(&mut frame, &mut model_work);
            if let Some(work) = work {
                frame["indicator_compute"] =
                    Value::String(serde_json::to_string(&work.request).map_err(io::Error::other)?);
            }
            if let Some((id, work)) = next_model_work {
                let projection = match work.provider() {
                    TextModelProvider::Ollama => json!({
                        "destination": work.destination(), "request": work.request(),
                    }),
                    TextModelProvider::OpenAiResponses {
                        credential_revision,
                    } => json!({
                        "provider": "openai_responses", "credential_revision": credential_revision.to_string(),
                        "request": work.request(),
                    }),
                };
                frame["text_model_work"] = Value::String(projection.to_string());
                model_work = Some((id, work));
            }
            write_frame(&mut output, frame)?;
        }
    }
}

fn main() -> ExitCode {
    #[cfg(windows)]
    if let Some(exit) = scopehub_persistence::run_workspace_acl_worker() {
        std::process::exit(exit);
    }
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    if let [flag, root, count] = args.as_slice()
        && flag == "--prepare-profile-pool"
    {
        let Some(count) = count
            .to_str()
            .and_then(|count| count.parse::<NonZeroU8>().ok())
            .filter(|count| count.get() <= 64)
        else {
            eprintln!("A test profile pool requires between 1 and 64 profiles.");
            return ExitCode::FAILURE;
        };
        return match SqliteWorkspace::prepare_profile_pool(Path::new(root), count) {
            Ok(()) => ExitCode::SUCCESS,
            Err(error) => {
                eprintln!("{error}");
                ExitCode::FAILURE
            }
        };
    }
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
