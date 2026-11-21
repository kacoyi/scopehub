use std::{sync::Barrier, thread};

use scopehub_application::{
    Actor, LOCAL_WORKSPACE_OPERATIONS, LocalChartAnalysisGrant, LocalDatasetPublication,
    LocalDatasetStore, MappingCommitGuard, OperationRegistry, READ_LOCAL_IMPORT_REVISION,
    ReviewedMappingStore, SELECT_EVIDENCE, WorkspaceAccess,
};
use scopehub_domain::protocol::{CsvMappingV1, Error, ErrorCode, VisibleRange};
use scopehub_persistence::SqliteWorkspace;
use serde_json::{Value, json};
use uuid::Uuid;

use super::{
    LiveBridge, assert_snapshot_at_selection,
    support::{
        PROFILE_ID, Profile, WORKSPACE_ID, access, assert_committed, assert_failed, set, store,
        undo,
    },
    with_new_correlation,
};

const A: i64 = 1_767_346_200_000;
const B: i64 = A + 86_400_000;
const SOURCE_DIGEST: &str = "14b9a3172b4da93b3e3578af17ea729c1de617753a3d6859066156fcad0ddf8b";
const CSV: &[u8] =
    b"Date,Instrument,Open,High,Low,Close,Unknown\n2026-01-02,000001,4,6,3,5,retained\n2026-01-03,000001,6,9,4,8,retained\n";

struct CommitFixture;

impl MappingCommitGuard for CommitFixture {
    fn commit<T>(&self, apply: impl FnOnce() -> Result<T, Error>) -> Result<T, Error> {
        apply()
    }
}

fn human() -> WorkspaceAccess<'static> {
    WorkspaceAccess {
        operations: LOCAL_WORKSPACE_OPERATIONS,
        ..access()
    }
}

fn ai() -> WorkspaceAccess<'static> {
    WorkspaceAccess {
        actor: Actor::Ai,
        principal_id: "on-device-analysis",
        ..human()
    }
}

fn fixture(registry: &OperationRegistry<SqliteWorkspace>, id: &str) -> Value {
    let local = WorkspaceAccess {
        actor: Actor::LocalUser,
        operations: &[READ_LOCAL_IMPORT_REVISION],
        ..human()
    };
    let chart = registry.open_evidence_chart(&local, id).unwrap();
    assert_eq!(chart["bars"][0]["bar"]["close"], 5);
    assert_eq!(chart["bars"][1]["bar"]["close"], 8);
    chart["snapshot"].clone()
}

fn publish(profile: &Profile) -> String {
    let mut writer = store(&profile.0);
    let mapping: CsvMappingV1 = serde_json::from_value(json!({
        "version": 1, "revision": "fixture", "encoding": "utf-8", "delimiter": ",",
        "record_kind": "native_ohlcv", "instrument_kind": "equity",
        "columns": {"symbol": "Instrument", "timestamp": "Date", "trading_day": "Date",
                    "open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": null},
        "venue": "TEST", "timezone": "UTC", "calendar_id": "fixture-calendar",
        "session_scope": "regular", "timeframe": "1d", "quantity_unit": "shares",
        "price_adjustment": "raw", "volume_semantics": "per_bar",
        "timestamp_convention": "bar_start", "timestamp_format": "%Y-%m-%d",
        "trading_day_format": "%Y-%m-%d", "daily_start_time": "09:30:00"
    }))
    .unwrap();
    let reviewed = writer
        .save_mapping("synthetic", &"a".repeat(64), &mapping, &CommitFixture)
        .unwrap();
    let content = json!({
        "file_sha256": SOURCE_DIGEST, "mapping_version": 1, "mapping_revision": "fixture",
        "header": ["Date", "Instrument", "Open", "High", "Low", "Close", "Unknown"],
        "series": {"instrument_kind": "equity", "venue": "TEST", "timeframe": "1d",
            "calendar_id": "fixture-calendar", "session_scope": "regular", "timezone": "UTC",
            "quantity_unit": "shares", "price_adjustment": "raw", "roll_method": null},
        "symbol": "000001", "source_record_count": 2,
        "bars": [
            {"bar": {"time_ms": A, "trading_day": "2026-01-02", "open": 4, "high": 6,
                     "low": 3, "close": 5, "volume": null, "is_closed": true},
             "locators": [{"record_number": 1, "line_start": 2, "line_end": 2}]},
            {"bar": {"time_ms": B, "trading_day": "2026-01-03", "open": 6, "high": 9,
                     "low": 4, "close": 8, "volume": null, "is_closed": true},
             "locators": [{"record_number": 2, "line_start": 3, "line_end": 3}]}
        ],
    });
    writer
        .publish_local_dataset(
            &LocalDatasetPublication {
                source_key: "synthetic",
                relative_path: "fixtures/equity.csv",
                source_sha256: SOURCE_DIGEST,
                mapping_sha256: &"b".repeat(64),
                reviewed_mapping: &reviewed,
                original: CSV,
                content: &content,
            },
            &CommitFixture,
        )
        .unwrap()
}

fn interval(snapshot: &Value, start: i64) -> Value {
    json!({"kind": "chart_interval", "snapshot": snapshot, "range": {"from_ms": start, "to_ms": start + 1}})
}

fn select(revision: u64, target: Value) -> Value {
    json!({"operation": SELECT_EVIDENCE, "workspace_id": WORKSPACE_ID,
           "expected_selection_revision": revision, "target": target})
}

fn selection_request(revision: u64, target: Value) -> Value {
    let mut value = select(revision, target);
    value["type"] = json!("request");
    value["protocol_version"] = json!("1.1");
    value["timestamp_ms"] = json!(0);
    with_new_correlation(value)
}

fn change(workspace: u64, selection: u64, timeframe: &str, key: Uuid) -> Value {
    let mut request = set(workspace, timeframe, key);
    request["scope"]["expected_selection_revision"] = json!(selection);
    request
}

#[test]
fn selected_ai_mutation_cannot_bypass_the_local_analysis_grant() {
    let profile = Profile::new();
    let id = publish(&profile);
    let mut registry = OperationRegistry::new(store(&profile.0));
    let a = interval(&fixture(&registry, &id), A);
    let selected = registry
        .execute_workspace(&human(), selection_request(0, a.clone()))
        .unwrap();
    assert_eq!(selected["selection"]["target"], a);
    assert_eq!(selected["selection"]["selection_revision"], 1);
    let denied = registry
        .execute_workspace(&ai(), change(0, 1, "5m", Uuid::new_v4()))
        .unwrap();
    assert_failed(&denied, "PERMISSION_DENIED");
    assert_eq!(
        registry
            .workspace_snapshot(&human())
            .unwrap()
            .workspace
            .revision,
        0
    );
    assert_eq!(counts(&profile), (0, 0));
}

fn counts(profile: &Profile) -> (i64, i64) {
    profile.sqlite().query_row(
        "SELECT (SELECT count(*) FROM operation_log), (SELECT count(*) FROM idempotency_records)",
        [],
        |row| Ok((row.get(0)?, row.get(1)?)),
    ).unwrap()
}

fn intent(command: &Value) -> Value {
    json!({"scope": command["scope"], "intent": command["intent"]})
}

fn scoped_undo(workspace: u64, selection: u64, id: &str) -> Value {
    let mut request = undo(workspace, id, Uuid::new_v4());
    request["scope"]["expected_selection_revision"] = json!(selection);
    request
}

#[test]
fn scoped_ai_commit_uses_current_chart_evidence_and_replays_under_current_disclosure() {
    let profile = Profile::new();
    let dataset_id = publish(&profile);
    let mut registry = OperationRegistry::new(store(&profile.0));
    let frozen = fixture(&registry, &dataset_id);
    let a = interval(&frozen, A);
    let b = interval(&frozen, B);
    let range_a = serde_json::from_value::<VisibleRange>(a["range"].clone()).unwrap();
    let range_b = serde_json::from_value::<VisibleRange>(b["range"].clone()).unwrap();
    let grant_a = LocalChartAnalysisGrant {
        principal_id: ai().principal_id,
        revision_id: &dataset_id,
        range: &range_a,
        fields: &[],
    };
    let grant_b = LocalChartAnalysisGrant {
        range: &range_b,
        ..grant_a
    };
    let first = registry
        .execute_evidence_intent(&ai(), Some(&grant_a), select(0, a.clone()))
        .unwrap();
    assert_eq!(first["selection"]["target"], a);
    assert_eq!(first["selection"]["selection_revision"], 1);
    let under_a = intent(&change(0, 1, "1d", Uuid::new_v4()));
    for grant in [None, Some(&grant_b)] {
        assert_failed(
            &registry
                .execute_chart_mutation_intent(&ai(), grant, under_a.clone())
                .unwrap(),
            "PERMISSION_DENIED",
        );
    }
    let another_principal = LocalChartAnalysisGrant {
        principal_id: "other",
        ..grant_a
    };
    assert_failed(
        &registry
            .execute_chart_mutation_intent(&ai(), Some(&another_principal), under_a.clone())
            .unwrap(),
        "PERMISSION_DENIED",
    );
    let paired = WorkspaceAccess {
        paired_client_id: Some("paired"),
        ..ai()
    };
    assert_eq!(
        registry
            .execute_chart_mutation_intent(&paired, Some(&grant_a), under_a.clone())
            .unwrap_err()
            .code,
        ErrorCode::PERMISSIONDENIED,
    );
    let mut spoofed = under_a.clone();
    spoofed["principal_id"] = json!(ai().principal_id);
    assert_eq!(
        registry
            .execute_chart_mutation_intent(&ai(), Some(&grant_a), spoofed)
            .unwrap_err()
            .code,
        ErrorCode::INVALIDARGUMENT,
    );
    for target in [
        json!({"kind": "chart", "object_id": "another-chart"}),
        a.clone(),
    ] {
        let mut wrong_target = under_a.clone();
        wrong_target["scope"]["target"] = target;
        assert_failed(
            &registry
                .execute_chart_mutation_intent(&ai(), Some(&grant_a), wrong_target)
                .unwrap(),
            "STALE_CONTEXT",
        );
    }
    assert_eq!(counts(&profile), (0, 0));

    let selected = registry
        .execute_workspace(&human(), selection_request(1, b.clone()))
        .unwrap();
    assert_eq!(selected["selection"]["target"], b);
    assert_eq!(selected["selection"]["selection_revision"], 2);
    assert_eq!(
        registry
            .workspace_snapshot(&human())
            .unwrap()
            .workspace
            .revision,
        0
    );
    assert_failed(
        &registry
            .execute_chart_mutation_intent(&ai(), Some(&grant_a), under_a)
            .unwrap(),
        "STALE_CONTEXT",
    );
    let bound = intent(&change(0, 2, "1d", Uuid::new_v4()));
    let without_operation = WorkspaceAccess {
        operations: &[],
        ..ai()
    };
    assert_failed(
        &registry
            .execute_chart_mutation_intent(&without_operation, Some(&grant_b), bound.clone())
            .unwrap(),
        "PERMISSION_DENIED",
    );
    let committed = registry
        .execute_chart_mutation_intent(&ai(), Some(&grant_b), bound.clone())
        .unwrap();
    let id = assert_committed(&committed, 1, "1d");
    assert_eq!(committed["outcome"]["receipt"]["undoable"], true);
    let after = registry.workspace_snapshot(&human()).unwrap();
    assert_eq!(after.selection_revision, 2);
    assert_eq!(after.chart_evidence.unwrap()["range"], b["range"]);
    assert_failed(
        &registry
            .execute_chart_mutation_intent(
                &ai(),
                Some(&grant_b),
                intent(&change(0, 2, "5m", Uuid::new_v4())),
            )
            .unwrap(),
        "STALE_CONTEXT",
    );

    let selected = registry
        .execute_workspace(&human(), selection_request(2, a.clone()))
        .unwrap();
    assert_eq!(selected["selection"]["selection_revision"], 3);
    let undo = registry
        .execute_chart_mutation_intent(&ai(), Some(&grant_a), intent(&scoped_undo(1, 3, id)))
        .unwrap();
    assert_committed(&undo, 2, "1m");
    assert_eq!(undo["outcome"]["receipt"]["undoable"], false);
    let mut writer = store(&profile.0);
    assert!(writer.revoke_local_dataset(&dataset_id).unwrap());
    assert_failed(
        &registry
            .execute_chart_mutation_intent(
                &ai(),
                Some(&grant_a),
                intent(&change(2, 3, "5m", Uuid::new_v4())),
            )
            .unwrap(),
        "SNAPSHOT_UNAVAILABLE",
    );
    drop(registry);
    let mut reconnected = OperationRegistry::new(writer);
    let reply = reconnected
        .execute_chart_mutation_intent(&without_operation, None, bound.clone())
        .unwrap();
    assert_ne!(reply["request_id"], committed["request_id"]);
    assert_eq!(reply["outcome"], committed["outcome"]);
    assert_eq!(assert_committed(&reply, 1, "1d"), id);
    assert!(!reply.to_string().contains(&dataset_id));
    let revoked = WorkspaceAccess {
        disclose_workspace_state: false,
        ..without_operation
    };
    assert_failed(
        &reconnected
            .execute_chart_mutation_intent(&revoked, None, bound.clone())
            .unwrap(),
        "PERMISSION_DENIED",
    );
    for (section, field, replacement) in [
        ("intent", "arguments", json!({"timeframe": "5m"})),
        ("scope", "expected_selection_revision", json!(3)),
        ("scope", "expected_workspace_revision", json!(1)),
        (
            "scope",
            "target",
            json!({"kind": "chart", "object_id": "another-chart"}),
        ),
    ] {
        let mut changed = bound.clone();
        changed[section][field] = replacement;
        assert_failed(
            &reconnected
                .execute_chart_mutation_intent(&ai(), None, changed)
                .unwrap(),
            "INVALID_ARGUMENT",
        );
    }
    let live = reconnected.workspace_snapshot(&human()).unwrap();
    assert_eq!(
        (live.workspace.revision, live.workspace.timeframe.as_str()),
        (2, "1m")
    );
    assert_eq!(live.selection_revision, 3);
    assert_eq!(live.chart_evidence, Some(json!({"unavailable": true})));
    assert_eq!(counts(&profile), (2, 2));
}

#[test]
fn writer_recovers_lost_selected_reply_and_preserves_intervening_edits() {
    let profile = Profile::new();
    let dataset_id = publish(&profile);
    let mut first = LiveBridge::open(&profile, PROFILE_ID);
    let initial = first.reply();
    assert_snapshot_at_selection(&initial, 0, 0, "1m");
    let frozen = first.send(&json!({
        "type": "frozen_chart", "request_id": Uuid::new_v4().to_string(), "revision_id": dataset_id,
    }));
    let a = interval(&frozen["snapshot"], A);
    let b = interval(&frozen["snapshot"], B);
    let selected = first.send(&selection_request(0, a.clone()));
    assert_eq!(selected["selection"]["target"], a);
    let original = change(0, 1, "5m", Uuid::new_v4());
    first.dispatch(&original);
    // Drain the writer and discard the reply, as if the caller disconnected.
    first.finish();
    let persisted_id: String = profile
        .sqlite()
        .query_row("SELECT id FROM operation_log", [], |row| row.get(0))
        .unwrap();

    let mut peer = LiveBridge::open(&profile, PROFILE_ID);
    let reopened = peer.reply();
    assert_snapshot_at_selection(&reopened, 1, 1, "5m");
    let selected = peer.send(&selection_request(1, b.clone()));
    assert_eq!(selected["selection"]["target"], b);
    assert_eq!(selected["selection"]["selection_revision"], 2);
    peer.finish();

    let mut reconnected = LiveBridge::open(&profile, PROFILE_ID);
    let reopened = reconnected.reply();
    assert_snapshot_at_selection(&reopened, 1, 2, "5m");
    assert_eq!(reopened["chart_evidence"]["range"], b["range"]);
    let retry = with_new_correlation(original.clone());
    let replay = reconnected.send(&retry);
    assert_eq!(replay["request_id"], retry["request_id"]);
    assert_eq!(assert_committed(&replay, 1, "5m"), persisted_id);
    assert_eq!(counts(&profile), (1, 1));
    assert_failed(
        &reconnected.send(&scoped_undo(1, 1, &persisted_id)),
        "STALE_CONTEXT",
    );

    let later = reconnected.send(&change(1, 2, "1d", Uuid::new_v4()));
    assert_committed(&later, 2, "1d");
    let returned = reconnected.send(&change(2, 2, "5m", Uuid::new_v4()));
    let latest_id = assert_committed(&returned, 3, "5m");
    assert_failed(
        &reconnected.send(&scoped_undo(3, 2, &persisted_id)),
        "STALE_CONTEXT",
    );
    let cleared = reconnected.send(&selection_request(2, Value::Null));
    assert_eq!(
        cleared["selection"],
        json!({
            "workspace_id": WORKSPACE_ID, "selection_revision": 3, "target": null,
        })
    );
    let eligible = scoped_undo(3, 3, latest_id);
    let inverse = reconnected.send(&eligible);
    assert_committed(&inverse, 4, "1d");
    assert_eq!(inverse["outcome"]["receipt"]["undoable"], false);
    assert_eq!(
        reconnected.send(&with_new_correlation(eligible))["outcome"],
        inverse["outcome"]
    );
    reconnected.finish();
    let last = LiveBridge::open(&profile, PROFILE_ID);
    let reopened = last.reply();
    assert_snapshot_at_selection(&reopened, 4, 3, "1d");
    assert_eq!(reopened["chart_evidence"], Value::Null);
    assert_eq!(reopened["undo"], Value::Null);
    last.finish();
    assert_eq!(counts(&profile), (4, 4));
}

#[test]
fn selected_write_rolls_back_and_parallel_exact_retries_commit_once() {
    let profile = Profile::new();
    let id = publish(&profile);
    let mut registry = OperationRegistry::new(store(&profile.0));
    let b = interval(&fixture(&registry, &id), B);
    let selected = registry
        .execute_workspace(&human(), selection_request(0, b.clone()))
        .unwrap();
    assert_eq!(selected["selection"]["target"], b);
    let probe = profile.sqlite();
    probe
        .execute_batch(
            "CREATE TRIGGER reject_replay BEFORE INSERT ON idempotency_records
         BEGIN SELECT RAISE(ABORT, 'synthetic rollback'); END;",
        )
        .unwrap();
    let request = change(0, 1, "5m", Uuid::new_v4());
    let failed = registry
        .execute_workspace(&human(), request.clone())
        .unwrap();
    assert_failed(&failed, "INTERNAL_ERROR");
    assert!(!failed.to_string().contains("synthetic rollback"));
    assert_eq!(counts(&profile), (0, 0));
    drop(registry);
    probe.execute_batch("DROP TRIGGER reject_replay").unwrap();
    drop(probe);

    let mut first = OperationRegistry::new(store(&profile.0));
    let mut peer = OperationRegistry::new(store(&profile.0));
    let before = first.workspace_snapshot(&human()).unwrap();
    assert_eq!(
        (
            before.workspace.revision,
            before.workspace.timeframe.as_str()
        ),
        (0, "1m")
    );
    assert_eq!(before.selection_revision, 1);
    assert_eq!(before.chart_evidence.unwrap()["range"], b["range"]);
    let barrier = Barrier::new(2);
    let (left, right) = thread::scope(|scope| {
        let retry = with_new_correlation(request.clone());
        let parallel = scope.spawn(|| {
            barrier.wait();
            peer.execute_workspace(&human(), request).unwrap()
        });
        barrier.wait();
        let right = first.execute_workspace(&human(), retry).unwrap();
        (parallel.join().unwrap(), right)
    });
    assert_ne!(left["request_id"], right["request_id"]);
    assert_committed(&left, 1, "5m");
    assert_eq!(left["outcome"], right["outcome"]);
    assert_eq!(counts(&profile), (1, 1));
    let after = first.workspace_snapshot(&human()).unwrap();
    assert_eq!(after.workspace.revision, 1);
    assert_eq!(after.selection_revision, 1);
    assert_eq!(after.chart_evidence.unwrap()["range"], b["range"]);
}
