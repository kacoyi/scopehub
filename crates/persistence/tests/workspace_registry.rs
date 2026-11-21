mod support;

use std::sync::Barrier;

use scopehub_application::{Actor, OperationRegistry, READ_OPERATIONS, WorkspaceAccess};
use scopehub_domain::protocol::ErrorCode;
use scopehub_persistence::{OpenWorkspaceError, SqliteWorkspace};
use serde_json::json;
use support::{Profile, access, assert_committed, assert_failed, binding, set, store, undo};
use uuid::Uuid;

#[test]
fn ai_commit_and_human_undo_use_the_current_authenticated_scope() {
    let profile = Profile::new();
    let mut registry = OperationRegistry::new(store(&profile.0));
    let binding = access();
    let ai = WorkspaceAccess {
        actor: Actor::Ai,
        ..access()
    };
    let key = Uuid::new_v4();
    let request = set(0, "5m", key);
    let reply = registry.execute_workspace(&ai, request.clone()).unwrap();
    let id = assert_committed(&reply, 1, "5m");
    assert_eq!(reply["request_id"], request["request_id"]);
    assert_eq!(reply["outcome"]["receipt"]["undoable"], true);

    let replay_only = WorkspaceAccess {
        operations: &[],
        ..access()
    };
    let retry = set(0, "5m", key);
    let replay = registry
        .execute_workspace(&replay_only, retry.clone())
        .unwrap();
    assert_eq!(replay["request_id"], retry["request_id"]);
    assert_ne!(replay["request_id"], reply["request_id"]);
    assert_eq!(assert_committed(&replay, 1, "5m"), id);

    let mut alternate_uuid = retry;
    alternate_uuid["scope"]["idempotency_key"] = json!(key.to_string().to_uppercase());
    alternate_uuid["scope"]["expected_workspace_revision"] = json!(0.0);
    let replay = registry
        .execute_workspace(&replay_only, alternate_uuid)
        .unwrap();
    assert_eq!(assert_committed(&replay, 1, "5m"), id);

    let reply = registry
        .execute_workspace(&binding, set(0, "1d", key))
        .unwrap();
    assert_failed(&reply, "INVALID_ARGUMENT");

    let restricted = WorkspaceAccess {
        disclose_workspace_state: false,
        ..access()
    };
    for attempt in [
        request.clone(),
        set(0, "1d", key),
        set(1, "1d", Uuid::new_v4()),
    ] {
        let reply = registry.execute_workspace(&restricted, attempt).unwrap();
        assert_failed(&reply, "PERMISSION_DENIED");
    }
    assert_eq!(
        registry.workspace_snapshot(&restricted).err().unwrap().code,
        ErrorCode::PERMISSIONDENIED
    );

    let undo_request = undo(1, id, key);
    for foreign in [
        WorkspaceAccess {
            principal_id: "other-principal",
            ..access()
        },
        WorkspaceAccess {
            paired_client_id: Some("other-client"),
            ..access()
        },
    ] {
        let replay = registry
            .execute_workspace(&foreign, request.clone())
            .unwrap();
        assert_failed(&replay, "STALE_CONTEXT");
        let result = registry
            .execute_workspace(&foreign, undo_request.clone())
            .unwrap();
        assert_failed(&result, "PERMISSION_DENIED");
    }

    let foreign_profile = WorkspaceAccess {
        profile_id: "other-profile",
        ..access()
    };
    let reply = registry
        .execute_workspace(&foreign_profile, request)
        .unwrap();
    assert_failed(&reply, "PERMISSION_DENIED");

    // The same key is independent for a different registered operation.
    let undo_reply = registry.execute_workspace(&binding, undo_request).unwrap();
    let undo_id = assert_committed(&undo_reply, 2, "1m");
    assert_ne!(undo_id, id);
    assert_eq!(undo_reply["outcome"]["receipt"]["undoable"], false);
    let activity: (String, String) = profile
        .sqlite()
        .query_row(
            "SELECT earlier.actor, later.actor
         FROM operation_log AS earlier JOIN operation_log AS later ON later.undoes = earlier.id",
            [],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .unwrap();
    assert_eq!(activity, ("ai".into(), "user".into()));
}

#[test]
fn history_grant_and_invalid_scope_cannot_create_a_mutation() {
    let profile = Profile::new();
    let mut registry = OperationRegistry::new(store(&profile.0));
    let history = WorkspaceAccess {
        operations: &READ_OPERATIONS,
        ..access()
    };
    let owner = access();
    let key = Uuid::new_v4();
    let mut request = set(0, "5m", key);
    assert_failed(
        &registry
            .execute_workspace(&history, request.clone())
            .unwrap(),
        "PERMISSION_DENIED",
    );
    let system = WorkspaceAccess {
        actor: Actor::System,
        ..access()
    };
    assert_failed(
        &registry
            .execute_workspace(&system, request.clone())
            .unwrap(),
        "PERMISSION_DENIED",
    );
    request["scope"]["workspace_id"] = json!("other-workspace");
    assert_failed(
        &registry.execute_workspace(&owner, request.clone()).unwrap(),
        "PERMISSION_DENIED",
    );
    request = set(0, "5m", key);
    request["scope"]["target"]["object_id"] = json!("other-chart");
    assert_failed(
        &registry.execute_workspace(&owner, request.clone()).unwrap(),
        "STALE_CONTEXT",
    );
    request = set(0, "5m", key);
    request["scope"]["expected_selection_revision"] = json!(1);
    assert_failed(
        &registry.execute_workspace(&owner, request.clone()).unwrap(),
        "STALE_CONTEXT",
    );

    request = set(0, "5m", key);
    request["scope"]["profile_root"] = json!("/forged");
    request["scope"]["principal_id"] = json!("other-principal");
    assert_eq!(
        registry
            .execute_workspace(&owner, request)
            .unwrap_err()
            .code,
        ErrorCode::INVALIDARGUMENT
    );
    let mut future = set(0, "5m", key);
    future["protocol_version"] = json!("1.2");
    assert_eq!(
        registry.execute_workspace(&owner, future).unwrap_err().code,
        ErrorCode::PROTOCOLMISMATCH
    );
    assert_eq!(
        registry
            .workspace_snapshot(&owner)
            .unwrap()
            .workspace
            .revision,
        0
    );
    assert_committed(
        &registry
            .execute_workspace(&owner, set(0, "5m", key))
            .unwrap(),
        1,
        "5m",
    );

    let old_history = WorkspaceAccess {
        operations: &READ_OPERATIONS,
        disclose_workspace_state: false,
        ..access()
    };
    assert_failed(
        &registry
            .execute_workspace(&old_history, set(0, "5m", key))
            .unwrap(),
        "PERMISSION_DENIED",
    );
}

#[test]
fn intervening_edits_prevent_undo_even_when_the_timeframe_matches_again() {
    let profile = Profile::new();
    let mut registry = OperationRegistry::new(store(&profile.0));
    let owner = access();
    let reply = registry
        .execute_workspace(&owner, set(0, "5m", Uuid::new_v4()))
        .unwrap();
    let first = assert_committed(&reply, 1, "5m");
    for (revision, next) in [(1, "1d"), (2, "5m")] {
        let result = registry
            .execute_workspace(&owner, set(revision, next, Uuid::new_v4()))
            .unwrap();
        assert_committed(&result, revision + 1, next);
    }
    let result = registry
        .execute_workspace(&owner, undo(3, first, Uuid::new_v4()))
        .unwrap();
    assert_failed(&result, "STALE_CONTEXT");
    let current = registry.workspace_snapshot(&owner).unwrap().workspace;
    assert_eq!((current.revision, current.timeframe.as_str()), (3, "5m"));
}

#[test]
fn concurrent_retries_from_two_connections_commit_once() {
    let profile = Profile::new();
    let a = store(&profile.0);
    let b = store(&profile.0);
    let barrier = Barrier::new(2);
    let key = Uuid::new_v4();
    let run = |store| {
        let mut registry = OperationRegistry::new(store);
        let request = set(0, "5m", key);
        barrier.wait();
        registry.execute_workspace(&access(), request).unwrap()
    };
    let (first, second) = std::thread::scope(|scope| {
        let a = scope.spawn(|| run(a));
        let b = scope.spawn(|| run(b));
        (a.join().unwrap(), b.join().unwrap())
    });
    assert_eq!(
        assert_committed(&first, 1, "5m"),
        assert_committed(&second, 1, "5m")
    );
    assert_ne!(first["request_id"], second["request_id"]);
    assert_eq!(counts(&profile), (1, 1));
}

#[test]
fn restored_undo_is_limited_to_the_current_eligible_actor_principal_client_and_grant() {
    let profile = Profile::new();
    let mut registry = OperationRegistry::new(store(&profile.0));
    let owner = WorkspaceAccess {
        paired_client_id: Some("paired"),
        ..access()
    };
    assert!(registry.workspace_snapshot(&owner).unwrap().undo.is_none());
    let first = registry
        .execute_workspace(&owner, set(0, "5m", Uuid::new_v4()))
        .unwrap();
    let first_id = assert_committed(&first, 1, "5m");
    drop(registry);

    let mut registry = OperationRegistry::new(store(&profile.0));
    let candidate = registry.workspace_snapshot(&owner).unwrap().undo.unwrap();
    assert_eq!(candidate.operation_id, first_id);
    assert_eq!(candidate.committed_workspace_revision, 1);
    for other in [
        WorkspaceAccess {
            actor: Actor::Ai,
            paired_client_id: Some("paired"),
            ..access()
        },
        WorkspaceAccess {
            principal_id: "other",
            paired_client_id: Some("paired"),
            ..access()
        },
        WorkspaceAccess {
            paired_client_id: Some("other"),
            ..access()
        },
        WorkspaceAccess {
            operations: &READ_OPERATIONS,
            paired_client_id: Some("paired"),
            ..access()
        },
    ] {
        assert!(registry.workspace_snapshot(&other).unwrap().undo.is_none());
    }

    let ai = WorkspaceAccess {
        actor: Actor::Ai,
        paired_client_id: Some("paired"),
        ..access()
    };
    let recent = registry
        .execute_workspace(&ai, set(1, "1d", Uuid::new_v4()))
        .unwrap();
    let recent_id = assert_committed(&recent, 2, "1d");
    assert!(registry.workspace_snapshot(&owner).unwrap().undo.is_none());
    assert_eq!(
        registry
            .workspace_snapshot(&ai)
            .unwrap()
            .undo
            .unwrap()
            .operation_id,
        recent_id
    );
    let inverse = registry
        .execute_workspace(&ai, undo(2, recent_id, Uuid::new_v4()))
        .unwrap();
    assert_committed(&inverse, 3, "5m");
    drop(registry);
    let restored = OperationRegistry::new(store(&profile.0));
    assert!(restored.workspace_snapshot(&ai).unwrap().undo.is_none());
    assert!(restored.workspace_snapshot(&owner).unwrap().undo.is_none());
}

#[test]
fn log_and_timeframe_rollback_if_the_replay_record_cannot_be_written() {
    let profile = Profile::new();
    let mut registry = OperationRegistry::new(store(&profile.0));
    let probe = profile.sqlite();
    probe
        .execute_batch(
            "CREATE TRIGGER reject_replay BEFORE INSERT ON idempotency_records
         BEGIN SELECT RAISE(ABORT, 'synthetic fault'); END;",
        )
        .unwrap();
    let request = set(0, "5m", Uuid::new_v4());
    let failed = registry
        .execute_workspace(&access(), request.clone())
        .unwrap();
    assert_failed(&failed, "INTERNAL_ERROR");
    assert!(!failed.to_string().contains("synthetic fault"));
    assert_eq!(counts(&profile), (0, 0));
    drop(registry);

    probe.execute_batch("DROP TRIGGER reject_replay").unwrap();
    let mut restarted = OperationRegistry::new(store(&profile.0));
    let before = restarted.workspace_snapshot(&access()).unwrap().workspace;
    assert_eq!((before.revision, before.timeframe.as_str()), (0, "1m"));
    let response = restarted.execute_workspace(&access(), request).unwrap();
    assert_committed(&response, 1, "5m");
    assert_eq!(counts(&profile), (1, 1));
}

#[test]
fn a_database_cannot_be_opened_with_another_profile_or_workspace_binding() {
    let profile = Profile::new();
    drop(store(&profile.0));
    assert!(matches!(
        SqliteWorkspace::open(&profile.0, binding("other-profile")),
        Err(OpenWorkspaceError::ProfileMismatch)
    ));
    let mut wrong = binding(support::PROFILE_ID);
    wrong.workspace_id = "other-workspace".into();
    assert!(matches!(
        SqliteWorkspace::open(&profile.0, wrong),
        Err(OpenWorkspaceError::ProfileMismatch)
    ));
}

fn counts(profile: &Profile) -> (i64, i64) {
    profile.sqlite().query_row(
        "SELECT (SELECT count(*) FROM operation_log), (SELECT count(*) FROM idempotency_records)",
        [], |row| Ok((row.get(0)?, row.get(1)?))
    ).unwrap()
}

#[test]
fn indicator_preview_binds_local_source_grants_and_frozen_input() {
    use scopehub_application::{
        ACCEPT_INDICATOR_VERSION, CREATE_INDICATOR_DRAFT, INDICATOR_CONFIRMATION,
        READ_LOCAL_IMPORT_REVISION, RUN_INDICATOR_PREVIEW,
    };
    use serde_json::Value;

    let profile = Profile::new();
    let mut registry = OperationRegistry::new(store(&profile.0));
    let id = Uuid::new_v4().to_string();
    let source_hash = "a".repeat(64);
    let conn = profile.sqlite();
    conn.execute(
        "INSERT INTO reviewed_import_mappings VALUES ('indicator-test', 1, ?1, '{}')",
        [&source_hash],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO local_dataset_source_blobs VALUES (?1, X'01')",
        [&source_hash],
    )
    .unwrap();
    let day = 86_400_000_i64;
    let start = 1_770_076_800_000_i64;
    let bars: Vec<_> = [1, 3, 5].into_iter().enumerate().map(|(index, close)| {
        json!({
            "bar": {
                "time_ms": start + index as i64 * day, "trading_day": format!("2026-02-0{}", index + 3),
                "open": close, "high": close, "low": close, "close": close,
                "volume": null, "is_closed": true,
            },
            "locators": [{"record_number": index + 1, "line_start": index + 2, "line_end": index + 2}],
        })
    }).collect();
    let data = json!({
        "symbol": "FIX", "source_record_count": bars.len(), "bars": bars,
        "series": {"instrument_kind": "equity", "venue": "FIXTURE", "timeframe": "1d",
          "calendar_id": "fixture-calendar", "session_scope": "regular", "timezone": "UTC",
          "quantity_unit": "shares", "price_adjustment": "raw", "roll_method": null},
    });
    conn.execute(
        "INSERT INTO local_dataset_revisions VALUES (?1, 'indicator-test', 'fixture.csv', ?2, 1, ?2, ?3)",
        [&id, &source_hash, &data.to_string()],
    ).unwrap();
    let access = || WorkspaceAccess {
        operations: &[
            CREATE_INDICATOR_DRAFT,
            RUN_INDICATOR_PREVIEW,
            ACCEPT_INDICATOR_VERSION,
            INDICATOR_CONFIRMATION,
        ],
        ..support::access()
    };
    let local_display = WorkspaceAccess {
        actor: Actor::LocalUser,
        operations: &[READ_LOCAL_IMPORT_REVISION],
        ..access()
    };
    let frozen = registry.open_evidence_chart(&local_display, &id).unwrap()["snapshot"].clone();
    let create = CREATE_INDICATOR_DRAFT;
    let bound = |name: &str, arguments: Value, target: Value| {
        let mut request = support::command(name, 0, Uuid::new_v4(), arguments);
        request["scope"]["target"] = target;
        request
    };
    let draft_request = bound(
        create,
        json!({
            "language": "declarative", "source": {
                "parameters": [{"type": "integer", "name": "period", "default": 2}],
                "outputs": [{"id": "mean", "kind": "line", "pane": "price", "style": {"color": "#123456", "width": 2},
                    "value": {"op": "rolling_mean", "value": {"op": "field", "field": "close"},
                              "window": {"parameter": "period"}}}],
            },
        }),
        json!({"kind": "chart", "object_id": "main-chart"}),
    );
    let draft = registry
        .execute_workspace(&access(), draft_request)
        .unwrap()["outcome"]["result"]["draft_id"]
        .clone();
    assert!(draft.is_string());
    let run = bound(
        RUN_INDICATOR_PREVIEW,
        json!({"draft_id": draft, "snapshot": frozen, "parameters": []}),
        json!({"kind": "chart_interval", "snapshot": frozen, "range": {"from_ms": start + day, "to_ms": start + 2 * day + 1}}),
    );
    assert_failed(
        &registry
            .execute_workspace(
                &WorkspaceAccess {
                    operations: &[READ_LOCAL_IMPORT_REVISION],
                    ..access()
                },
                run.clone(),
            )
            .unwrap(),
        "PERMISSION_DENIED",
    );
    let mut forged = run.clone();
    forged["intent"]["arguments"]["snapshot"]["content_hash"] = json!("0".repeat(64));
    forged["scope"]["target"]["snapshot"]["content_hash"] = json!("0".repeat(64));
    assert_failed(
        &registry.execute_workspace(&access(), forged).unwrap(),
        "SNAPSHOT_UNAVAILABLE",
    );
    let started = registry.execute_workspace(&access(), run.clone()).unwrap();
    let task = started["outcome"]["result"]["task_id"].as_str().unwrap();
    assert_eq!(
        registry.execute_workspace(&access(), run.clone()).unwrap()["outcome"],
        started["outcome"]
    );

    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(2);
    let ready = loop {
        let status = registry.indicator_preview_status(&access(), task).unwrap();
        if status["status"] != "running" {
            break status;
        }
        assert!(std::time::Instant::now() < deadline);
        std::thread::sleep(std::time::Duration::from_millis(1));
    };
    assert_eq!(ready["status"], "ready");
    assert_eq!(ready["warmup_from_ms"], start);
    assert_eq!(ready["event"]["snapshot"]["warmup_bars"], 1);
    assert_ne!(
        ready["event"]["snapshot"]["content_hash"],
        frozen["content_hash"]
    );
    assert_eq!(ready["resolved_parameters"], json!({"period": 2}));
    assert_eq!(
        ready["event"]["artifact"]["result"]["series"][0]["points"],
        json!([
            {"time_ms": start + day, "value": 2.0}, {"time_ms": start + 2 * day, "value": 4.0},
        ])
    );
    assert_eq!(
        registry.indicator_preview_status(&access(), task).unwrap(),
        ready
    );
    let accept = bound(
        ACCEPT_INDICATOR_VERSION,
        json!({
            "preview_id": ready["event"]["preview_id"],
            "result_hash": ready["event"]["artifact"]["result_hash"],
        }),
        run["scope"]["target"].clone(),
    );
    let challenge = registry
        .execute_workspace(&access(), accept.clone())
        .unwrap()["outcome"]["challenge"]
        .clone();
    assert_eq!(challenge["task_id"], task);
    assert!(Uuid::parse_str(challenge["session_id"].as_str().unwrap()).is_ok());
    let approval = json!({
        "type": "request", "protocol_version": "1.1", "operation": INDICATOR_CONFIRMATION,
        "message_id": Uuid::new_v4().to_string(), "request_id": Uuid::new_v4().to_string(),
        "timestamp_ms": 0, "challenge_id": challenge["challenge_id"], "decision": "approve",
    });
    for operations in [
        &[ACCEPT_INDICATOR_VERSION][..],
        &[INDICATOR_CONFIRMATION][..],
    ] {
        let limited = WorkspaceAccess {
            operations,
            ..access()
        };
        for decision in ["deny", "approve"] {
            let mut attempt = approval.clone();
            attempt["decision"] = json!(decision);
            assert_failed(
                &registry.execute_workspace(&limited, attempt).unwrap(),
                "PERMISSION_DENIED",
            );
        }
    }
    conn.execute_batch(
        "CREATE TRIGGER refuse_indicator_artifact BEFORE INSERT ON indicator_versions
         BEGIN SELECT raise(ABORT, 'synthetic indicator write failure'); END;",
    )
    .unwrap();
    assert_failed(
        &registry
            .execute_workspace(&access(), approval.clone())
            .unwrap(),
        "INTERNAL_ERROR",
    );
    let versions: i64 = conn
        .query_row("SELECT count(*) FROM indicator_versions", [], |row| {
            row.get(0)
        })
        .unwrap();
    let writes: i64 = conn
        .query_row("SELECT count(*) FROM operation_log", [], |row| row.get(0))
        .unwrap();
    assert_eq!(
        (
            versions,
            writes,
            registry
                .workspace_snapshot(&access())
                .unwrap()
                .workspace
                .revision
        ),
        (0, 0, 0)
    );
    conn.execute_batch("DROP TRIGGER refuse_indicator_artifact")
        .unwrap();

    let committed = registry
        .execute_workspace(&access(), approval.clone())
        .unwrap()["outcome"]
        .clone();
    assert_eq!(committed["state"], "committed", "{committed}");
    assert_eq!(committed["receipt"]["committed_workspace_revision"], 1);
    let layer = committed["result"]["layer_id"].as_str().unwrap();
    let accepted = registry
        .accepted_indicator_output(&access(), layer)
        .unwrap();
    assert_eq!(accepted["document"]["artifact"], ready["event"]["artifact"]);
    drop(registry);

    let mut registry = OperationRegistry::new(store(&profile.0));
    for operations in [
        &[ACCEPT_INDICATOR_VERSION][..],
        &[INDICATOR_CONFIRMATION][..],
    ] {
        assert_failed(
            &registry
                .execute_workspace(
                    &WorkspaceAccess {
                        operations,
                        ..access()
                    },
                    approval.clone(),
                )
                .unwrap(),
            "PERMISSION_DENIED",
        );
    }
    assert_eq!(
        registry.execute_workspace(&access(), approval).unwrap()["outcome"],
        committed
    );
    assert_eq!(
        registry.execute_workspace(&access(), accept).unwrap()["outcome"],
        committed
    );
    assert_eq!(
        registry
            .accepted_indicator_output(&access(), layer)
            .unwrap(),
        accepted
    );
    assert_eq!(
        registry
            .indicator_preview_status(&access(), task)
            .unwrap_err()
            .code,
        ErrorCode::STALECONTEXT
    );

    conn.execute(
        "INSERT INTO local_dataset_display_revocations (revision_id) VALUES (?1)",
        [&id],
    )
    .unwrap();
    assert_eq!(
        registry
            .accepted_indicator_output(&access(), layer)
            .unwrap_err()
            .code,
        ErrorCode::PERMISSIONDENIED
    );
    assert_eq!(
        registry.accepted_indicator_layers(&access(), &id).unwrap()["layers"][0]["available"],
        false
    );
    assert_eq!(
        registry
            .workspace_snapshot(&access())
            .unwrap()
            .workspace
            .revision,
        1
    );
}

#[test]
fn trusted_python_confirmation_requires_both_current_grants() {
    use scopehub_application::{
        CREATE_INDICATOR_DRAFT, INDICATOR_CONFIRMATION, RUN_INDICATOR_PREVIEW,
    };
    use scopehub_domain::protocol::IndicatorBarsInput11;
    use serde_json::Value;

    let profile = Profile::new();
    let mut registry = OperationRegistry::new(store(&profile.0));
    let owner = WorkspaceAccess {
        operations: &[
            CREATE_INDICATOR_DRAFT,
            RUN_INDICATOR_PREVIEW,
            INDICATOR_CONFIRMATION,
        ],
        ..access()
    };
    let selected = registry
        .select_workspace_source(
            &owner,
            &json!({
                "type": "workspace_source", "request_id": Uuid::new_v4().to_string(),
                "expected_selection_revision": 0,
                "source": {"kind": "sample", "instrument_id": "SIM.CN.FUTURE.001"},
            }),
        )
        .unwrap();
    assert_eq!(selected["selection_revision"], 1);
    let page: Value = serde_json::from_str(include_str!(
        "../../../tests/fixtures/indicators/confirmation-sample-history.json"
    ))
    .unwrap();
    let code = include_str!("../../../tests/fixtures/indicators/two-bar-mean.py");
    let mut create = support::command(
        CREATE_INDICATOR_DRAFT,
        0,
        Uuid::new_v4(),
        json!({"language": "python", "source": code, "params": {}}),
    );
    create["scope"]["expected_selection_revision"] = json!(1);
    let draft = registry.execute_workspace(&owner, create).unwrap();
    let draft_id = draft["outcome"]["result"]["draft_id"].as_str().unwrap();
    let snapshot = &page["snapshot"];
    let mut run = support::command(
        RUN_INDICATOR_PREVIEW,
        0,
        Uuid::new_v4(),
        json!({"draft_id": draft_id, "snapshot": snapshot, "python_params": {"divisor": 2}}),
    );
    run["scope"]["expected_selection_revision"] = json!(1);
    run["scope"]["target"] = json!({
        "kind": "chart_interval", "snapshot": snapshot,
        "range": {"from_ms": 1704200400000_i64, "to_ms": 1704200520000_i64},
    });
    let challenge = registry
        .execute_sample_indicator_preview(&owner, run, page)
        .unwrap()["outcome"]["challenge"]
        .clone();
    let task_id = challenge["task_id"].as_str().unwrap();
    let approval = json!({
        "type": "request", "protocol_version": "1.1", "operation": INDICATOR_CONFIRMATION,
        "message_id": Uuid::new_v4().to_string(), "request_id": Uuid::new_v4().to_string(),
        "timestamp_ms": 0, "challenge_id": challenge["challenge_id"], "decision": "approve",
    });
    for operations in [&[RUN_INDICATOR_PREVIEW][..], &[INDICATOR_CONFIRMATION][..]] {
        let limited = WorkspaceAccess {
            operations,
            ..owner
        };
        for decision in ["deny", "approve"] {
            let mut attempt = approval.clone();
            attempt["decision"] = json!(decision);
            assert_failed(
                &registry.execute_workspace(&limited, attempt).unwrap(),
                "PERMISSION_DENIED",
            );
        }
    }
    assert!(
        registry
            .take_approved_python(&owner, task_id)
            .unwrap()
            .is_none()
    );
    let reply = registry.execute_workspace(&owner, approval).unwrap();
    assert_eq!(reply["outcome"]["result"]["task_id"], task_id);
    let dispatch = registry
        .take_approved_python(&owner, task_id)
        .unwrap()
        .unwrap();
    assert_eq!(dispatch.request.resolved_params["divisor"], 2);
    let IndicatorBarsInput11::InlineIndicatorBars11(input) = dispatch.request.input else {
        panic!("expected the approved frozen bars")
    };
    assert_eq!(
        input.bars.iter().map(|bar| bar.close).collect::<Vec<_>>(),
        [3499.2, 3499.8]
    );
    assert!(
        registry
            .take_approved_python(&owner, task_id)
            .unwrap()
            .is_none()
    );
    registry
        .finish_trusted_python(
            &owner,
            task_id,
            Err(scopehub_application::failure(ErrorCode::RESOURCELIMIT)),
        )
        .unwrap();
    let terminal = registry.indicator_preview_status(&owner, task_id).unwrap();
    assert_eq!(terminal["status"], "terminal");
    assert_eq!(terminal["event"]["outcome"]["status"], "failed");
    assert_eq!(
        terminal["event"]["outcome"]["error"]["code"],
        "RESOURCE_LIMIT"
    );
    assert_eq!(terminal["event"]["committed_effects"], json!([]));
}

#[test]
fn layout_preview_is_actor_bound_and_a_failed_commit_keeps_the_previous_layout() {
    use scopehub_application::{
        APPLY_LAYOUT, LAYOUT_ID, LOCAL_WORKSPACE_OPERATIONS, PREVIEW_LAYOUT,
    };
    use support::command;

    let profile = Profile::new();
    let mut registry = OperationRegistry::new(store(&profile.0));
    let owner = WorkspaceAccess {
        operations: LOCAL_WORKSPACE_OPERATIONS,
        ..access()
    };
    let ai = WorkspaceAccess {
        actor: Actor::Ai,
        principal_id: "layout-agent",
        ..owner
    };
    let mut definition = serde_json::to_value(
        registry
            .workspace_snapshot(&owner)
            .unwrap()
            .layouts
            .unwrap()
            .active
            .definition,
    )
    .unwrap();
    definition["theme"] = json!("light");
    definition["panels"][0]["dock"] = json!("left");
    let edit = json!({"action":"save", "name":"Review", "definition": definition});
    let target = json!({"kind":"layout", "object_id":LAYOUT_ID});

    let mut preview = command(PREVIEW_LAYOUT, 0, Uuid::new_v4(), json!({"edit": edit}));
    preview["scope"]["target"] = target.clone();
    let bound = json!({"scope": preview["scope"], "intent": preview["intent"]});
    let scoped = WorkspaceAccess {
        operations: &[PREVIEW_LAYOUT],
        ..ai
    };
    let proposed = registry
        .execute_chart_mutation_intent(&scoped, None, bound)
        .unwrap();
    assert_eq!(proposed["outcome"]["state"], "previewed");
    let candidate = &proposed["outcome"]["result"];
    assert_eq!(candidate["layout"]["definition"], definition);
    assert_eq!(counts(&profile), (0, 0));

    let mut apply = command(
        APPLY_LAYOUT,
        0,
        Uuid::new_v4(),
        json!({
            "edit": edit, "preview_token": candidate["preview_token"]
        }),
    );
    apply["scope"]["target"] = target.clone();
    let other = registry.execute_workspace(&owner, apply.clone()).unwrap();
    assert_failed(&other, "STALE_CONTEXT");
    let mut substituted = apply.clone();
    substituted["intent"]["arguments"]["edit"]["name"] = json!("Substitute");
    assert_failed(
        &registry.execute_workspace(&ai, substituted).unwrap(),
        "STALE_CONTEXT",
    );
    assert_failed(
        &registry.execute_workspace(&scoped, apply.clone()).unwrap(),
        "PERMISSION_DENIED",
    );

    let probe = profile.sqlite();
    probe
        .execute_batch(
            "CREATE TRIGGER stop_layout BEFORE INSERT ON workspace_layout_changes
         BEGIN SELECT RAISE(ABORT, 'synthetic layout fault'); END;",
        )
        .unwrap();
    assert_failed(
        &registry.execute_workspace(&ai, apply.clone()).unwrap(),
        "INTERNAL_ERROR",
    );
    assert_eq!(counts(&profile), (0, 0));
    let previous = registry.workspace_snapshot(&owner).unwrap();
    assert_eq!(previous.workspace.revision, 0);
    let previous = previous.layouts.unwrap();
    assert!(previous.active.name.is_none() && previous.saved.is_empty());

    probe.execute_batch("DROP TRIGGER stop_layout").unwrap();
    let applied = registry.execute_workspace(&ai, apply.clone()).unwrap();
    let receipt = &applied["outcome"]["receipt"];
    assert_eq!(applied["outcome"]["state"], "committed");
    assert_eq!(applied["outcome"]["result"], candidate["layout"]);
    assert_eq!(receipt["committed_workspace_revision"], 1);
    drop(registry);

    let mut reopened = OperationRegistry::new(store(&profile.0));
    let retried = reopened.execute_workspace(&ai, apply).unwrap();
    assert_eq!(&retried["outcome"]["receipt"], receipt);
    let restored = reopened.workspace_snapshot(&ai).unwrap().layouts.unwrap();
    assert_eq!(restored.active.name.as_deref(), Some("Review"));
    let undo_id = restored.undo.unwrap().operation_id;
    assert!(
        reopened
            .workspace_snapshot(&owner)
            .unwrap()
            .layouts
            .unwrap()
            .undo
            .is_none()
    );
    let mut inverse = undo(1, &undo_id, Uuid::new_v4());
    inverse["scope"]["target"] = target;
    let undone = reopened.execute_workspace(&ai, inverse).unwrap();
    assert_eq!(undone["outcome"]["state"], "committed");
    let restored = reopened.workspace_snapshot(&owner).unwrap();
    assert_eq!(
        (restored.workspace.revision, restored.selection_revision),
        (2, 0)
    );
    let restored = restored.layouts.unwrap();
    assert!(restored.active.name.is_none() && restored.saved.is_empty() && restored.undo.is_none());
}
