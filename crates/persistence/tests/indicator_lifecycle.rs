#[allow(dead_code)]
mod support;

use std::time::{Duration, Instant};

use scopehub_application::{
    ACCEPT_INDICATOR_VERSION, Actor, CANCEL_INDICATOR_TASK, CREATE_INDICATOR_DRAFT,
    INDICATOR_CONFIRMATION, OperationRegistry, READ_LOCAL_IMPORT_REVISION, REMOVE_INDICATOR_LAYER,
    RUN_INDICATOR_PREVIEW, SET_TIMEFRAME, UNDO, WorkspaceAccess,
};
use scopehub_domain::protocol::ErrorCode;
use scopehub_persistence::SqliteWorkspace;
use serde_json::{Value, json};
use support::{Profile, assert_failed, command, set, store};
use uuid::Uuid;

type Registry = OperationRegistry<SqliteWorkspace>;

const DAY: i64 = 86_400_000;
const START: i64 = 1_770_076_800_000;

fn access() -> WorkspaceAccess<'static> {
    WorkspaceAccess {
        operations: &[
            CREATE_INDICATOR_DRAFT,
            RUN_INDICATOR_PREVIEW,
            ACCEPT_INDICATOR_VERSION,
            REMOVE_INDICATOR_LAYER,
            INDICATOR_CONFIRMATION,
            CANCEL_INDICATOR_TASK,
            SET_TIMEFRAME,
            UNDO,
        ],
        ..support::access()
    }
}

fn display_access() -> WorkspaceAccess<'static> {
    WorkspaceAccess {
        actor: Actor::LocalUser,
        operations: &[READ_LOCAL_IMPORT_REVISION],
        ..support::access()
    }
}

fn publish_fixture(profile: &Profile, prices: [i32; 3]) -> String {
    let id = Uuid::new_v4().to_string();
    let conn = profile.sqlite();
    let source_hash = "a".repeat(64);
    conn.execute(
        "INSERT INTO reviewed_import_mappings VALUES (?1, 1, ?2, '{}')",
        [&id, &source_hash],
    )
    .unwrap();
    conn.execute(
        "INSERT OR IGNORE INTO local_dataset_source_blobs VALUES (?1, X'01')",
        [&source_hash],
    )
    .unwrap();
    let bars: Vec<_> = prices
        .into_iter()
        .enumerate()
        .map(|(index, close)| {
            json!({
                "bar": {
                    "time_ms": START + index as i64 * DAY, "trading_day": format!("2026-02-0{}", index + 3),
                    "open": close, "high": close, "low": close, "close": close,
                    "volume": null, "is_closed": true,
                },
                "locators": [{"record_number": index + 1, "line_start": index + 2, "line_end": index + 2}],
            })
        })
        .collect();
    let data = json!({
        "symbol": "FIX", "source_record_count": bars.len(), "bars": bars,
        "series": {"instrument_kind": "equity", "venue": "FIXTURE", "timeframe": "1d",
            "calendar_id": "fixture-calendar", "session_scope": "regular", "timezone": "UTC",
            "quantity_unit": "shares", "price_adjustment": "raw", "roll_method": null},
    });
    conn.execute(
        "INSERT INTO local_dataset_revisions VALUES (?1, ?1, 'fixture.csv', ?2, 1, ?2, ?3)",
        [&id, &source_hash, &data.to_string()],
    )
    .unwrap();
    id
}

fn execute(registry: &mut Registry, request: Value) -> Value {
    registry.execute_workspace(&access(), request).unwrap()
}

fn same_intent(request: &Value) -> Value {
    let mut retry = request.clone();
    retry["request_id"] = json!(Uuid::new_v4().to_string());
    retry["message_id"] = json!(Uuid::new_v4().to_string());
    retry
}

fn draft(registry: &mut Registry, revision: u64, selection_revision: u64, source: Value) -> Value {
    let mut request = command(CREATE_INDICATOR_DRAFT, revision, Uuid::new_v4(), source);
    request["scope"]["expected_selection_revision"] = json!(selection_revision);
    let reply = execute(registry, request);
    assert_eq!(reply["outcome"]["state"], "committed", "{reply}");
    reply["outcome"]["result"]["draft_id"].clone()
}

fn declarative_run(registry: &mut Registry, dataset: &str, revision: u64) -> Value {
    let draft_id = draft(
        registry,
        revision,
        0,
        json!({
            "language": "declarative", "source": {
                "parameters": [{"type": "integer", "name": "period", "default": 2}],
                "outputs": [{"id": "mean", "kind": "line", "pane": "price", "style": {"color": "#123456", "width": 2},
                    "value": {"op": "rolling_mean", "value": {"op": "field", "field": "close"},
                              "window": {"parameter": "period"}}}],
            },
        }),
    );
    let chart = registry
        .open_evidence_chart(&display_access(), dataset)
        .unwrap();
    let snapshot = &chart["snapshot"];
    let mut request = command(
        RUN_INDICATOR_PREVIEW,
        revision,
        Uuid::new_v4(),
        json!({"draft_id": draft_id, "snapshot": snapshot, "parameters": []}),
    );
    request["scope"]["target"] = json!({
        "kind": "chart_interval", "snapshot": snapshot,
        "range": {"from_ms": START + DAY, "to_ms": START + 2 * DAY + 1},
    });
    request
}

fn started(reply: &Value) -> &str {
    assert_eq!(reply["outcome"]["state"], "committed", "{reply}");
    reply["outcome"]["result"]["task_id"].as_str().unwrap()
}

fn ready(registry: &mut Registry, task: &str, values: [f64; 2]) -> Value {
    let deadline = Instant::now() + Duration::from_secs(5);
    let status = loop {
        let status = registry.indicator_preview_status(&access(), task).unwrap();
        if status["status"] != "running" {
            break status;
        }
        assert!(Instant::now() < deadline);
        std::thread::sleep(Duration::from_millis(1));
    };
    assert_eq!(status["status"], "ready", "{status}");
    assert_eq!(
        status["event"]["artifact"]["result"]["series"][0]["points"],
        json!([
            {"time_ms": START + DAY, "value": values[0]},
            {"time_ms": START + 2 * DAY, "value": values[1]},
        ])
    );
    status
}

fn accept(run: &Value, status: &Value, revision: u64) -> Value {
    let mut request = command(
        ACCEPT_INDICATOR_VERSION,
        revision,
        Uuid::new_v4(),
        json!({
            "preview_id": status["event"]["preview_id"],
            "result_hash": status["event"]["artifact"]["result_hash"],
        }),
    );
    request["scope"]["target"] = run["scope"]["target"].clone();
    request
}

fn approve(reply: &Value) -> Value {
    assert_eq!(
        reply["outcome"]["state"], "awaiting_confirmation",
        "{reply}"
    );
    json!({
        "type": "request", "protocol_version": "1.1", "operation": INDICATOR_CONFIRMATION,
        "message_id": Uuid::new_v4().to_string(), "request_id": Uuid::new_v4().to_string(),
        "timestamp_ms": 0, "challenge_id": reply["outcome"]["challenge"]["challenge_id"],
        "decision": "approve",
    })
}

fn cancel(registry: &mut Registry, task: &str, expected: &str) {
    let response = execute(
        registry,
        json!({
            "type": "request", "protocol_version": "1.1", "operation": CANCEL_INDICATOR_TASK,
            "message_id": Uuid::new_v4().to_string(), "request_id": Uuid::new_v4().to_string(),
            "timestamp_ms": 0, "task_id": task,
        }),
    );
    assert_eq!(response["outcome"]["status"], expected, "{response}");
    assert_eq!(response["committed_effects"], json!([]));
}

#[test]
fn delivered_preview_stays_superseded_across_cancel_and_client_retries() {
    let profile = Profile::new();
    let mut registry = OperationRegistry::new(store(&profile.0));
    let dataset = publish_fixture(&profile, [1, 3, 5]);
    let unrelated = publish_fixture(&profile, [10, 14, 22]);

    let first = declarative_run(&mut registry, &dataset, 0);
    let first_task = execute(&mut registry, first.clone());
    let first_ready = ready(&mut registry, started(&first_task), [2.0, 4.0]);
    let first_accept = accept(&first, &first_ready, 0);
    let staged = execute(&mut registry, first_accept.clone());
    let first_approval = approve(&staged);

    // A second local chart client starts B, but has not read its result.
    let mut second = same_intent(&first);
    second["scope"]["idempotency_key"] = json!(Uuid::new_v4().to_string());
    let second_task = execute(&mut registry, second.clone());
    assert_ne!(started(&first_task), started(&second_task));
    assert_eq!(
        execute(&mut registry, same_intent(&first_accept))["outcome"],
        staged["outcome"]
    );

    let other = declarative_run(&mut registry, &unrelated, 0);
    let other_task = execute(&mut registry, other.clone());
    let other_ready = ready(&mut registry, started(&other_task), [12.0, 18.0]);
    assert_eq!(
        execute(&mut registry, same_intent(&first_accept))["outcome"],
        staged["outcome"]
    );

    ready(&mut registry, started(&second_task), [2.0, 4.0]);
    assert_failed(
        &execute(&mut registry, same_intent(&first_accept)),
        "STALE_CONTEXT",
    );
    assert_failed(
        &execute(&mut registry, same_intent(&first_approval)),
        "STALE_CONTEXT",
    );
    cancel(&mut registry, started(&second_task), "completed");
    assert_eq!(
        registry
            .indicator_preview_status(&access(), started(&second_task))
            .unwrap_err()
            .code,
        ErrorCode::STALECONTEXT
    );
    assert_eq!(
        execute(&mut registry, same_intent(&second))["outcome"],
        second_task["outcome"]
    );
    assert_eq!(
        registry
            .indicator_preview_status(&access(), started(&first_task))
            .unwrap(),
        first_ready
    );
    assert_failed(
        &execute(&mut registry, same_intent(&first_accept)),
        "STALE_CONTEXT",
    );
    assert_failed(
        &execute(&mut registry, same_intent(&first_approval)),
        "STALE_CONTEXT",
    );
    assert_eq!(
        registry
            .workspace_snapshot(&access())
            .unwrap()
            .workspace
            .revision,
        0
    );
    assert_eq!(
        registry
            .accepted_indicator_layers(&access(), &dataset)
            .unwrap()["layers"],
        json!([])
    );

    let unrelated_accept = execute(&mut registry, accept(&other, &other_ready, 0));
    let committed = execute(&mut registry, approve(&unrelated_accept));
    assert_eq!(
        committed["outcome"]["receipt"]["committed_workspace_revision"],
        1
    );
    let layer = committed["outcome"]["result"]["layer_id"].as_str().unwrap();
    let restored = registry
        .accepted_indicator_output(&access(), layer)
        .unwrap();
    assert_eq!(
        restored["document"]["artifact"],
        other_ready["event"]["artifact"]
    );
}

fn select_source(registry: &mut Registry, revision: u64, source: Value) {
    let result = registry
        .select_workspace_source(
            &access(),
            &json!({
                "type": "workspace_source", "request_id": Uuid::new_v4().to_string(),
                "expected_selection_revision": revision, "source": source,
            }),
        )
        .unwrap();
    assert_eq!(result["selection_revision"], revision + 1);
}

#[test]
fn retry_of_approved_python_run_recovers_only_the_original_authorization() {
    let profile = Profile::new();
    let mut registry = OperationRegistry::new(store(&profile.0));
    let sample = json!({"kind": "sample", "instrument_id": "SIM.CN.FUTURE.001"});
    select_source(&mut registry, 0, sample.clone());
    let page: Value = serde_json::from_str(include_str!(
        "../../../tests/fixtures/indicators/confirmation-sample-history.json"
    ))
    .unwrap();
    let snapshot = &page["snapshot"];
    let source = include_str!("../../../tests/fixtures/indicators/two-bar-mean.py");
    let draft_id = draft(
        &mut registry,
        0,
        1,
        json!({"language": "python", "source": source, "params": {}}),
    );
    let mut run = command(
        RUN_INDICATOR_PREVIEW,
        0,
        Uuid::new_v4(),
        json!({
            "draft_id": draft_id, "snapshot": snapshot, "python_params": {"divisor": 2},
        }),
    );
    run["scope"]["expected_selection_revision"] = json!(1);
    run["scope"]["target"] = json!({
        "kind": "chart_interval", "snapshot": snapshot,
        "range": {"from_ms": 1_704_200_400_000_i64, "to_ms": 1_704_200_520_000_i64},
    });
    let staged = registry
        .execute_sample_indicator_preview(&access(), run.clone(), page.clone())
        .unwrap();
    let task = staged["outcome"]["challenge"]["task_id"].as_str().unwrap();
    assert!(
        registry
            .take_approved_python(&access(), task)
            .unwrap()
            .is_none()
    );
    let approved = execute(&mut registry, approve(&staged));
    assert_eq!(started(&approved), task);
    let work = registry
        .take_approved_python(&access(), task)
        .unwrap()
        .unwrap();
    let dispatch = serde_json::to_value(&work.request).unwrap();
    assert_eq!(dispatch["draft_id"], draft_id);
    assert_eq!(dispatch["resolved_params"], json!({"divisor": 2}));
    assert_eq!(dispatch["input"]["bars"][0]["close"], 3499.2);
    assert_eq!(dispatch["input"]["bars"][1]["close"], 3499.8);

    // The host has taken the original dispatch, but the initiating client lost its approval reply.
    let retry = same_intent(&run);
    let preview_grant = WorkspaceAccess {
        operations: &[RUN_INDICATOR_PREVIEW],
        ..access()
    };
    let recovered = registry
        .execute_workspace(&preview_grant, retry.clone())
        .unwrap();
    assert_eq!(recovered["request_id"], retry["request_id"]);
    assert_eq!(recovered["outcome"], approved["outcome"]);
    assert!(
        registry
            .take_approved_python(&access(), task)
            .unwrap()
            .is_none()
    );

    let mut wrong_params = same_intent(&run);
    wrong_params["intent"]["arguments"]["python_params"]["divisor"] = json!(3);
    assert_failed(&execute(&mut registry, wrong_params), "INVALID_ARGUMENT");
    let mut wrong_kind = same_intent(&run);
    let args = wrong_kind["intent"]["arguments"].as_object_mut().unwrap();
    args.remove("python_params");
    args.insert("parameters".into(), json!([]));
    assert_failed(&execute(&mut registry, wrong_kind), "INVALID_ARGUMENT");
    let mut wrong_key = same_intent(&run);
    wrong_key["scope"]["idempotency_key"] = json!(Uuid::new_v4().to_string());
    assert_failed(&execute(&mut registry, wrong_key), "STALE_CONTEXT");

    cancel(&mut registry, task, "cancelled");
    assert!(work.cancelled.load(std::sync::atomic::Ordering::Acquire));
    registry
        .finish_trusted_python(&access(), task, Ok(json!({})))
        .unwrap();
    let terminal = registry.indicator_preview_status(&access(), task).unwrap();
    assert_eq!(terminal["event"]["outcome"]["status"], "cancelled");
    assert_eq!(terminal["event"]["committed_effects"], json!([]));
    assert_eq!(
        execute(&mut registry, same_intent(&run))["outcome"],
        approved["outcome"]
    );
    assert!(
        registry
            .take_approved_python(&access(), task)
            .unwrap()
            .is_none()
    );
    assert_eq!(
        registry
            .workspace_snapshot(&access())
            .unwrap()
            .workspace
            .revision,
        0
    );

    // Even when the host never took the approved work, cancellation makes
    // recovering its receipt safe through transports that attempt dispatch.
    let mut queued = same_intent(&run);
    queued["scope"]["idempotency_key"] = json!(Uuid::new_v4().to_string());
    queued["intent"]["arguments"]["draft_id"] = draft(
        &mut registry,
        0,
        1,
        json!({"language": "python", "source": source, "params": {}}),
    );
    let queued_stage = registry
        .execute_sample_indicator_preview(&access(), queued.clone(), page.clone())
        .unwrap();
    let queued_receipt = execute(&mut registry, approve(&queued_stage));
    let queued_task = started(&queued_receipt);
    cancel(&mut registry, queued_task, "cancelled");
    assert_eq!(
        execute(&mut registry, same_intent(&queued))["outcome"],
        queued_receipt["outcome"]
    );
    assert!(
        registry
            .take_approved_python(&access(), queued_task)
            .unwrap()
            .is_none()
    );

    let no_run_grant = WorkspaceAccess {
        operations: &[INDICATOR_CONFIRMATION],
        ..access()
    };
    assert_failed(
        &registry
            .execute_workspace(&no_run_grant, same_intent(&run))
            .unwrap(),
        "PERMISSION_DENIED",
    );
    select_source(&mut registry, 1, json!({"kind": "none"}));
    assert_failed(
        &execute(&mut registry, same_intent(&run)),
        "PERMISSION_DENIED",
    );
    select_source(&mut registry, 2, sample);
    assert_eq!(
        execute(&mut registry, same_intent(&run))["outcome"],
        approved["outcome"]
    );
    assert!(
        registry
            .take_approved_python(&access(), task)
            .unwrap()
            .is_none()
    );
    drop(registry);

    let mut reopened = OperationRegistry::new(store(&profile.0));
    let mut current_scope = same_intent(&run);
    current_scope["scope"]["expected_selection_revision"] = json!(3);
    assert_failed(
        &reopened
            .execute_sample_indicator_preview(&access(), current_scope, page)
            .unwrap(),
        "STALE_CONTEXT",
    );
    assert_eq!(
        reopened
            .indicator_preview_status(&access(), task)
            .unwrap_err()
            .code,
        ErrorCode::STALECONTEXT
    );
    assert!(
        reopened
            .take_approved_python(&access(), task)
            .unwrap()
            .is_none()
    );
    let writes: i64 = profile
        .sqlite()
        .query_row("SELECT count(*) FROM operation_log", [], |row| row.get(0))
        .unwrap();
    assert_eq!(writes, 0);
}

fn layer_action(name: &str, revision: u64, layer_id: &Value, arguments: Value) -> Value {
    let mut request = command(name, revision, Uuid::new_v4(), arguments);
    request["scope"]["target"] = json!({"kind": "layer", "object_id": layer_id});
    request
}

fn list_request(dataset: &str) -> Value {
    json!({"type": "indicator_accepted_layers", "request_id": Uuid::new_v4().to_string(), "revision_id": dataset})
}

#[test]
fn reopening_restores_only_live_removal_receipts_for_the_listed_dataset() {
    let profile = Profile::new();
    let mut registry = OperationRegistry::new(store(&profile.0));
    let dataset = publish_fixture(&profile, [1, 3, 5]);
    let unrelated = publish_fixture(&profile, [10, 14, 22]);
    let listing = list_request(&dataset);
    let empty = json!({"dataset_id": dataset, "layers": []});
    let read = |registry: &Registry| {
        registry
            .read_accepted_indicator(&access(), &listing)
            .unwrap()
    };
    assert_eq!(read(&registry), empty);
    let mut layers = Vec::new();
    for (revision, dataset, values) in [
        (0, &dataset, [2.0, 4.0]),
        (1, &dataset, [2.0, 4.0]),
        (2, &unrelated, [12.0, 18.0]),
    ] {
        let run = declarative_run(&mut registry, dataset, revision);
        let task = execute(&mut registry, run.clone());
        let preview = ready(&mut registry, started(&task), values);
        let staged = execute(&mut registry, accept(&run, &preview, revision));
        let committed = execute(&mut registry, approve(&staged));
        assert_eq!(
            committed["outcome"]["receipt"]["committed_workspace_revision"],
            revision + 1
        );
        layers.push((
            committed["outcome"]["result"]["layer_id"].clone(),
            preview["event"]["artifact"].clone(),
        ));
    }
    assert!(read(&registry).get("undo").is_none());

    let mut removals = Vec::new();
    for (index, (layer, _)) in layers.iter().enumerate() {
        let revision = index as u64 + 3;
        let remove = layer_action(
            REMOVE_INDICATOR_LAYER,
            revision,
            layer,
            json!({"layer_id": layer}),
        );
        let outcome = execute(&mut registry, remove.clone())["outcome"].clone();
        let receipt = &outcome["receipt"];
        assert_eq!(receipt["committed_workspace_revision"], revision + 1);
        assert_eq!(receipt["undoable"], true);
        let removal = json!({
            "operation_id": receipt["operation_id"], "layer_id": layer,
            "committed_workspace_revision": revision + 1,
        });
        removals.push((removal, remove, outcome));
    }
    assert_eq!(
        execute(&mut registry, set(6, "5m", Uuid::new_v4()))["outcome"]["receipt"]["committed_workspace_revision"],
        7
    );
    drop(registry);

    let mut reopened = OperationRegistry::new(store(&profile.0));
    let latest = &removals[1].0;
    assert_eq!(
        read(&reopened),
        json!({"dataset_id": dataset, "layers": [], "undo": latest})
    );
    assert_eq!(
        reopened
            .read_accepted_indicator(&access(), &list_request(&unrelated))
            .unwrap()["undo"],
        removals[2].0
    );
    let unknown = Uuid::new_v4().to_string();
    assert_eq!(
        reopened
            .read_accepted_indicator(&access(), &list_request(&unknown))
            .unwrap(),
        json!({"dataset_id": unknown, "layers": []})
    );
    let no_undo = WorkspaceAccess {
        operations: &[ACCEPT_INDICATOR_VERSION],
        ..access()
    };
    assert_eq!(
        reopened
            .read_accepted_indicator(&no_undo, &listing)
            .unwrap(),
        empty
    );
    for denied in [
        WorkspaceAccess {
            operations: &[UNDO],
            ..access()
        },
        WorkspaceAccess {
            principal_id: "another-principal",
            ..access()
        },
        WorkspaceAccess {
            workspace_id: "other-workspace",
            ..access()
        },
        WorkspaceAccess {
            paired_client_id: Some("paired"),
            ..access()
        },
    ] {
        assert_eq!(
            reopened
                .read_accepted_indicator(&denied, &listing)
                .unwrap_err()
                .code,
            ErrorCode::PERMISSIONDENIED
        );
    }

    let undo = |revision, removal: &Value, layer: &Value| {
        layer_action(
            UNDO,
            revision,
            layer,
            json!({"operation_id": removal["operation_id"]}),
        )
    };
    assert_failed(
        &execute(&mut reopened, undo(7, latest, &layers[0].0)),
        "STALE_CONTEXT",
    );
    let restore = undo(7, latest, &layers[1].0);
    let restored = execute(&mut reopened, restore.clone());
    assert_eq!(
        restored["outcome"]["receipt"]["committed_workspace_revision"],
        8
    );
    assert_eq!(
        reopened
            .accepted_indicator_output(&access(), layers[1].0.as_str().unwrap())
            .unwrap()["document"]["artifact"],
        layers[1].1
    );
    assert_eq!(
        execute(&mut reopened, same_intent(&restore))["outcome"],
        restored["outcome"]
    );
    assert_eq!(
        execute(&mut reopened, same_intent(&removals[1].1))["outcome"],
        removals[1].2
    );
    assert_failed(
        &execute(&mut reopened, undo(8, latest, &layers[1].0)),
        "STALE_CONTEXT",
    );
    assert_eq!(read(&reopened)["undo"], removals[0].0);

    let layer_id = &layers[1].0;
    let remove_again = layer_action(
        REMOVE_INDICATOR_LAYER,
        8,
        layer_id,
        json!({"layer_id": layer_id}),
    );
    let outcome = execute(&mut reopened, remove_again);
    assert_eq!(
        read(&reopened)["undo"],
        json!({
            "operation_id": outcome["outcome"]["receipt"]["operation_id"],
            "layer_id": layer_id,
            "committed_workspace_revision": 9,
        })
    );
    assert_failed(
        &execute(&mut reopened, undo(9, latest, layer_id)),
        "STALE_CONTEXT",
    );

    // The first removal remains effective, but withdrawing the source hides
    // its recovery handle and blocks the same inverse through the registry.
    profile
        .sqlite()
        .execute(
            "INSERT INTO local_dataset_display_revocations (revision_id) VALUES (?1)",
            [&dataset],
        )
        .unwrap();
    let revoked = read(&reopened);
    assert_eq!(revoked, empty);
    assert_failed(
        &execute(&mut reopened, undo(9, &removals[0].0, &layers[0].0)),
        "PERMISSION_DENIED",
    );
    assert_eq!(
        reopened
            .workspace_snapshot(&access())
            .unwrap()
            .workspace
            .revision,
        9
    );
}
