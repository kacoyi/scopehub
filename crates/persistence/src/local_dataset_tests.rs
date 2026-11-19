use std::{
    process::{self, Command},
    sync::Barrier,
    thread,
};

use scopehub_application::{
    LocalDatasetPublication, LocalDatasetStore, MappingCommitGuard, ReviewedImportMapping,
    ReviewedMappingStore,
};
use serde_json::{Value, json};

use super::{
    reviewed_mapping_tests::{
        CommitMapping, FIRST_HEADER, SOURCE_KEY, ledger, test_mapping, v3_workspace,
    },
    *,
};

const FIRST_CSV: &[u8] =
    b"Date,Instrument,Open,High,Low,Close,Unknown\n2026-01-02,000001,4,6,3,5,retained\n";
const SECOND_CSV: &[u8] =
    b"Date,Instrument,Open,High,Low,Close,Unknown\n2026-01-02,000001,4,7,3,6,retained\n";
const FIRST_DIGEST: &str = "ca2c7f20f64aa355407e601d3cfd539f79fc81277cce3786dc2183cf2fab01df";
const SECOND_DIGEST: &str = "993232444793f579b2ed3c4a2930938a13b93b6bf2faa32c7145a4acd698d3b8";
const MAPPING_DIGEST: &str = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee";

const INTERRUPTED_PUBLISH_ROOT: &str = "SCOPEHUB_TEST_INTERRUPTED_PUBLISH_ROOT";
const STAGED_EXIT: i32 = 91;

fn v4_workspace(profile: &Profile) -> Connection {
    let mut connection = v3_workspace(profile);
    let transaction = connection
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .unwrap();
    transaction
        .execute_batch(include_str!("reviewed_import.sql"))
        .unwrap();
    record_migration(&transaction, REVIEWED_IMPORT_SCHEMA_VERSION).unwrap();
    transaction
        .execute(
            "INSERT INTO reviewed_import_mappings VALUES (?1, 1, ?2, ?3)",
            params![
                SOURCE_KEY,
                FIRST_HEADER,
                serde_json::to_string(&test_mapping("v4")).unwrap()
            ],
        )
        .unwrap();
    transaction.commit().unwrap();
    connection
}

#[test]
fn populated_v4_dataset_migration_is_atomic_and_preserves_existing_state() {
    let profile = Profile::new();
    let mut database = v4_workspace(&profile);
    let backup = fs::read(profile.db()).unwrap();
    let transaction = database
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .unwrap();
    migrate(&transaction, &Profile::binding()).unwrap();
    assert_eq!(ledger(&transaction), current_ledger());
    assert_eq!(integer_pragma(&transaction, "user_version"), SCHEMA_VERSION);
    assert_eq!(dataset_counts(&transaction), (0, 0));
    drop(transaction);
    assert_eq!(ledger(&database), [1, 2, 3, 4]);
    assert_eq!(integer_pragma(&database, "user_version"), 4);
    assert!(
        database
            .prepare("SELECT * FROM local_dataset_revisions")
            .is_err()
    );
    drop(database);
    assert_eq!(fs::read(profile.db()).unwrap(), backup);

    let writer = profile.open().unwrap();
    assert_eq!(
        integer_pragma(&writer.connection, "user_version"),
        SCHEMA_VERSION
    );
    assert_eq!(ledger(&writer.connection), current_ledger());
    let state = writer.snapshot(None, false).unwrap().state;
    assert_eq!(
        (state.revision, state.timeframe),
        (2, Timeframe::FiveMinutes)
    );
    let notes = writer.read_notes(Some("note"), 0).unwrap();
    assert_eq!(notes.note.unwrap().thesis, "thesis");
    assert_eq!(notes.outcomes[0].outcome_id, "outcome");
    let mapping = writer.load_mapping(SOURCE_KEY).unwrap().unwrap();
    assert_eq!(mapping.review_revision, 1);
    assert_eq!(mapping.header_sha256, FIRST_HEADER);
    assert_eq!(mapping.mapping, test_mapping("v4"));
    assert_eq!(dataset_counts(&writer.connection), (0, 0));

    let restored_profile = Profile::new();
    drop(restored_profile.raw_connection());
    fs::write(restored_profile.db(), backup).unwrap();
    let restored = restored_profile.open().unwrap();
    assert_eq!(restored.load_mapping(SOURCE_KEY).unwrap(), Some(mapping));
    assert_eq!(restored.snapshot(None, false).unwrap().state.revision, 2);
    assert_eq!(
        restored.read_notes(Some("note"), 0).unwrap().outcomes[0].outcome_id,
        "outcome"
    );
}

fn content(digest: &str, close: u32, high: u32) -> Value {
    json!({
        "file_sha256": digest, "mapping_version": 1, "mapping_revision": "v4",
        "header": ["Date", "Instrument", "Open", "High", "Low", "Close", "Unknown"],
        "series": {"instrument_kind": "equity", "venue": "TEST", "timeframe": "1d",
            "calendar_id": "fixture-calendar-1", "session_scope": "regular", "timezone": "UTC",
            "quantity_unit": "shares", "price_adjustment": "raw", "roll_method": null},
        "symbol": "000001", "source_record_count": 1,
        "bars": [{"bar": {"time_ms": 1_767_346_200_000_i64, "trading_day": "2026-01-02",
            "open": 4, "high": high, "low": 3, "close": close, "volume": null, "is_closed": true},
            "locators": [{ "record_number": 1, "line_start": 2, "line_end": 2}]}]
    })
}

fn dataset_counts(connection: &Connection) -> (i64, i64) {
    connection
        .query_row(
            "SELECT (SELECT count(*) FROM local_dataset_revisions),
                    (SELECT count(*) FROM local_dataset_source_blobs)",
            [],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .unwrap()
}

struct RejectCommit(ErrorCode);

impl MappingCommitGuard for RejectCommit {
    fn commit<T>(&self, _apply: impl FnOnce() -> Result<T, Error>) -> Result<T, Error> {
        Err(failure(self.0.clone()))
    }
}

#[test]
fn dataset_retry_and_refused_commit_preserve_original_identity_and_row_locators() {
    let profile = Profile::new();
    drop(v4_workspace(&profile));
    let mut writer = profile.open().unwrap();
    let reviewed = writer.load_mapping(SOURCE_KEY).unwrap().unwrap();
    let first = content(FIRST_DIGEST, 5, 6);
    let candidate = LocalDatasetPublication {
        source_key: SOURCE_KEY,
        relative_path: "fixtures/equity.csv",
        source_sha256: FIRST_DIGEST,
        mapping_sha256: MAPPING_DIGEST,
        reviewed_mapping: &reviewed,
        original: FIRST_CSV,
        content: &first,
    };
    let mut peer = profile.open().unwrap();
    let start = Barrier::new(2);
    let revision_id = thread::scope(|scope| {
        let other = scope.spawn(|| {
            start.wait();
            peer.publish_local_dataset(&candidate, &CommitMapping)
                .unwrap()
        });
        start.wait();
        let first = writer
            .publish_local_dataset(&candidate, &CommitMapping)
            .unwrap();
        assert_eq!(other.join().unwrap(), first);
        first
    });
    drop(peer);
    let first_retry = writer
        .publish_local_dataset(&candidate, &CommitMapping)
        .unwrap();
    assert_eq!(first_retry, revision_id);
    assert_eq!(dataset_counts(&writer.connection), (1, 1));

    let second_content = content(SECOND_DIGEST, 6, 7);
    let second = LocalDatasetPublication {
        source_sha256: SECOND_DIGEST,
        original: SECOND_CSV,
        content: &second_content,
        ..candidate
    };
    for code in [ErrorCode::CANCELLED, ErrorCode::STALECONTEXT] {
        let error = writer
            .publish_local_dataset(&second, &RejectCommit(code.clone()))
            .unwrap_err();
        assert_eq!(error.code, code);
        assert_eq!(dataset_counts(&writer.connection), (1, 1));
    }
    drop(writer);

    let writer = profile.open().unwrap();
    let frozen = writer.read_local_dataset(&revision_id).unwrap().unwrap();
    assert_eq!(frozen.revision_id, revision_id);
    assert_eq!(frozen.review_revision, 1);
    assert_eq!(frozen.relative_path, "fixtures/equity.csv");
    assert_eq!(frozen.mapping_sha256, MAPPING_DIGEST);
    assert_eq!(frozen.source_sha256, FIRST_DIGEST);
    assert_eq!(frozen.content, first);
    assert_eq!(frozen.content["bars"][0]["locators"][0]["line_start"], 2);
    let raw: Vec<u8> = writer
        .connection
        .query_row(
            "SELECT original_bytes FROM local_dataset_source_blobs WHERE source_sha256 = ?1",
            [FIRST_DIGEST],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(raw, FIRST_CSV);
    let other_profile = Profile::new();
    let other = other_profile.open().unwrap();
    assert!(other.read_local_dataset(&revision_id).unwrap().is_none());
}

#[test]
fn shared_original_and_revocation_do_not_overwrite_other_revisions() {
    let profile = Profile::new();
    let mut writer = profile.open().unwrap();
    let reviewed = writer
        .save_mapping(
            SOURCE_KEY,
            FIRST_HEADER,
            &test_mapping("v4"),
            &CommitMapping,
        )
        .unwrap();
    let alias = writer
        .save_mapping("alias", FIRST_HEADER, &reviewed.mapping, &CommitMapping)
        .unwrap();
    let normalized = content(FIRST_DIGEST, 5, 6);
    let original = LocalDatasetPublication {
        source_key: SOURCE_KEY,
        relative_path: "equity.csv",
        source_sha256: FIRST_DIGEST,
        mapping_sha256: MAPPING_DIGEST,
        reviewed_mapping: &reviewed,
        original: FIRST_CSV,
        content: &normalized,
    };
    let first_revision = writer
        .publish_local_dataset(&original, &CommitMapping)
        .unwrap();
    let second_revision = writer
        .publish_local_dataset(
            &LocalDatasetPublication {
                source_key: "alias",
                relative_path: "alias.csv",
                reviewed_mapping: &alias,
                ..original
            },
            &CommitMapping,
        )
        .unwrap();
    assert_ne!(first_revision, second_revision);
    assert_eq!(dataset_counts(&writer.connection), (2, 1));
    assert_eq!(
        writer
            .publish_local_dataset(
                &LocalDatasetPublication {
                    original: SECOND_CSV,
                    ..original
                },
                &CommitMapping
            )
            .unwrap_err()
            .code,
        ErrorCode::INTERNALERROR,
    );

    assert!(writer.revoke_local_dataset(&first_revision).unwrap());
    assert!(writer.revoke_local_dataset(&first_revision).unwrap());
    assert!(
        !writer
            .revoke_local_dataset(&Uuid::new_v4().to_string())
            .unwrap()
    );
    assert_eq!(
        writer
            .publish_local_dataset(&original, &CommitMapping)
            .unwrap_err()
            .code,
        ErrorCode::PERMISSIONDENIED,
    );
    drop(writer);
    let writer = profile.open().unwrap();
    assert_eq!(
        writer
            .read_local_dataset(&first_revision)
            .err()
            .unwrap()
            .code,
        ErrorCode::PERMISSIONDENIED,
    );
    assert_eq!(
        writer
            .read_local_dataset(&second_revision)
            .unwrap()
            .unwrap()
            .content,
        normalized
    );
    assert_eq!(dataset_counts(&writer.connection), (2, 1));
}

#[test]
fn publication_checks_current_reviewed_mapping_inside_the_transaction() {
    let profile = Profile::new();
    let mut writer = profile.open().unwrap();
    let mut peer = profile.open().unwrap();
    let earlier: ReviewedImportMapping = writer
        .save_mapping(
            SOURCE_KEY,
            FIRST_HEADER,
            &test_mapping("v4"),
            &CommitMapping,
        )
        .unwrap();
    peer.save_mapping(
        SOURCE_KEY,
        FIRST_HEADER,
        &test_mapping("new"),
        &CommitMapping,
    )
    .unwrap();
    let normalized = content(FIRST_DIGEST, 5, 6);
    let candidate = LocalDatasetPublication {
        source_key: SOURCE_KEY,
        relative_path: "equity.csv",
        source_sha256: FIRST_DIGEST,
        mapping_sha256: MAPPING_DIGEST,
        reviewed_mapping: &earlier,
        original: FIRST_CSV,
        content: &normalized,
    };
    assert_eq!(
        writer
            .publish_local_dataset(&candidate, &CommitMapping)
            .unwrap_err()
            .code,
        ErrorCode::STALECONTEXT
    );
    assert_eq!(dataset_counts(&writer.connection), (0, 0));
}

struct ExitBeforeCommit;

impl MappingCommitGuard for ExitBeforeCommit {
    fn commit<T>(&self, _apply: impl FnOnce() -> Result<T, Error>) -> Result<T, Error> {
        process::exit(STAGED_EXIT);
    }
}

#[test]
#[ignore = "subprocess fixture for an interrupted dataset publication"]
fn interrupted_publication_child() {
    let root = std::env::var_os(INTERRUPTED_PUBLISH_ROOT).unwrap();
    let mut writer = SqliteWorkspace::open(Path::new(&root), Profile::binding()).unwrap();
    let reviewed = writer.load_mapping(SOURCE_KEY).unwrap().unwrap();
    let normalized = content(SECOND_DIGEST, 6, 7);
    let candidate = LocalDatasetPublication {
        source_key: SOURCE_KEY,
        relative_path: "equity.csv",
        source_sha256: SECOND_DIGEST,
        mapping_sha256: MAPPING_DIGEST,
        reviewed_mapping: &reviewed,
        original: SECOND_CSV,
        content: &normalized,
    };
    let outcome = writer.publish_local_dataset(&candidate, &ExitBeforeCommit);
    panic!("writer returned before the staged commit boundary: {outcome:?}");
}

#[test]
fn interrupted_publication_discards_staging_and_keeps_the_frozen_revision() {
    let profile = Profile::new();
    let mut writer = profile.open().unwrap();
    let reviewed = writer
        .save_mapping(
            SOURCE_KEY,
            FIRST_HEADER,
            &test_mapping("v4"),
            &CommitMapping,
        )
        .unwrap();
    let normalized = content(FIRST_DIGEST, 5, 6);
    let candidate = LocalDatasetPublication {
        source_key: SOURCE_KEY,
        relative_path: "equity.csv",
        source_sha256: FIRST_DIGEST,
        mapping_sha256: MAPPING_DIGEST,
        reviewed_mapping: &reviewed,
        original: FIRST_CSV,
        content: &normalized,
    };
    let first = writer
        .publish_local_dataset(&candidate, &CommitMapping)
        .unwrap();
    drop(writer);
    let child = Command::new(std::env::current_exe().unwrap())
        .args([
            "--exact",
            "tests::local_dataset_tests::interrupted_publication_child",
            "--ignored",
        ])
        .env(INTERRUPTED_PUBLISH_ROOT, &profile.0)
        .output()
        .unwrap();
    assert_eq!(
        child.status.code(),
        Some(STAGED_EXIT),
        "{}",
        String::from_utf8_lossy(&child.stdout)
    );

    let mut reopened = profile.open().unwrap();
    assert_eq!(dataset_counts(&reopened.connection), (1, 1));
    let newer = content(SECOND_DIGEST, 6, 7);
    let next = reopened
        .publish_local_dataset(
            &LocalDatasetPublication {
                source_sha256: SECOND_DIGEST,
                original: SECOND_CSV,
                content: &newer,
                ..candidate
            },
            &CommitMapping,
        )
        .unwrap();
    assert_ne!(first, next);
    assert_eq!(
        reopened
            .read_local_dataset(&first)
            .unwrap()
            .unwrap()
            .content,
        normalized
    );
    assert_eq!(
        reopened.read_local_dataset(&next).unwrap().unwrap().content,
        newer
    );
    assert_eq!(dataset_counts(&reopened.connection), (2, 2));
}

#[test]
fn chart_evidence_migrates_populated_v5_and_rechecks_scope_grants_and_frozen_targets() {
    use scopehub_application::{
        LOCAL_WORKSPACE_OPERATIONS, LocalChartAnalysisGrant, OperationRegistry,
        READ_CHART_PROJECTION, READ_LOCAL_IMPORT_REVISION, SELECT_EVIDENCE, WorkspaceAccess,
    };
    use scopehub_domain::protocol::ExportField11;

    let profile = Profile::new();
    let connection = v4_workspace(&profile);
    connection
        .execute_batch(include_str!("local_dataset.sql"))
        .unwrap();
    record_migration(&connection, LOCAL_DATASET_SCHEMA_VERSION).unwrap();
    connection
        .execute_batch("UPDATE workspaces SET revision = 3, selection_revision = 7;
             INSERT INTO operation_log VALUES
               ('ee23de30-c329-463b-85cd-bbe1459223d8', 'workspace', 'chart.setTimeframe', 'user', 'principal', '',
                'timeframe', 300, 3, 1, '1m', '5m', NULL, NULL);")
        .unwrap();
    let v5 = SqliteWorkspace {
        connection,
        binding: Profile::binding(),
    };
    let mut v5 = v5;
    let reviewed = v5.load_mapping(SOURCE_KEY).unwrap().unwrap();
    let mut frozen = content(FIRST_DIGEST, 5, 6);
    let start = 1_767_346_200_000_i64;
    let second = start + 86_400_000;
    let original = b"Date,Instrument,Open,High,Low,Close,Unknown\n2026-01-02,000001,4,6,3,5,retained\n2026-01-03,000001,6,9,4,8,retained\n";
    let digest = "14b9a3172b4da93b3e3578af17ea729c1de617753a3d6859066156fcad0ddf8b";
    frozen["file_sha256"] = json!(digest);
    frozen["source_record_count"] = json!(2);
    frozen["bars"].as_array_mut().unwrap().push(json!({
        "bar": {"time_ms": second, "trading_day": "2026-01-03", "open": 6,
                "high": 9, "low": 4, "close": 8, "volume": null, "is_closed": true},
        "locators": [{"record_number": 2, "line_start": 3, "line_end": 3}],
    }));
    let candidate = LocalDatasetPublication {
        source_key: SOURCE_KEY,
        relative_path: "private/synthetic.csv",
        source_sha256: digest,
        mapping_sha256: MAPPING_DIGEST,
        reviewed_mapping: &reviewed,
        original,
        content: &frozen,
    };
    let id = v5
        .publish_local_dataset(&candidate, &CommitMapping)
        .unwrap();
    let revoked = v5
        .publish_local_dataset(
            &LocalDatasetPublication {
                original: SECOND_CSV,
                source_sha256: SECOND_DIGEST,
                content: &content(SECOND_DIGEST, 6, 7),
                ..candidate
            },
            &CommitMapping,
        )
        .unwrap();
    assert!(v5.revoke_local_dataset(&revoked).unwrap());

    let transaction = v5
        .connection
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .unwrap();
    migrate(&transaction, &Profile::binding()).unwrap();
    assert_eq!(ledger(&transaction), current_ledger());
    drop(transaction);
    assert_eq!(ledger(&v5.connection), [1, 2, 3, 4, 5]);
    assert_eq!(dataset_counts(&v5.connection), (2, 2));
    assert!(
        v5.connection
            .prepare("SELECT * FROM workspace_chart_evidence")
            .is_err()
    );
    drop(v5);

    let mut store = profile.open().unwrap();
    assert_eq!(ledger(&store.connection), current_ledger());
    assert_eq!(store.load_mapping(SOURCE_KEY).unwrap().unwrap(), reviewed);
    assert_eq!(
        store.read_local_dataset(&id).unwrap().unwrap().content,
        frozen
    );
    assert_eq!(
        store.read_local_dataset(&revoked).err().unwrap().code,
        ErrorCode::PERMISSIONDENIED
    );
    let notes = store.read_notes(Some("note"), 0).unwrap();
    assert_eq!(notes.note.unwrap().thesis, "thesis");
    assert_eq!(notes.outcomes[0].outcome_id, "outcome");

    let binding = Profile::binding();
    let user = WorkspaceAccess {
        actor: Actor::User,
        principal_id: "principal",
        paired_client_id: None,
        profile_id: &binding.profile_id,
        workspace_id: &binding.workspace_id,
        operations: LOCAL_WORKSPACE_OPERATIONS,
        disclose_workspace_state: true,
    };
    let ai = WorkspaceAccess {
        actor: Actor::Ai,
        principal_id: "local-analysis-fixture",
        ..user
    };
    let mut first = OperationRegistry::new(profile.open().unwrap());
    let mut peer = OperationRegistry::new(profile.open().unwrap());
    let local_import = WorkspaceAccess {
        actor: Actor::LocalUser,
        principal_id: user.profile_id,
        operations: &[READ_LOCAL_IMPORT_REVISION],
        ..user
    };
    assert_eq!(
        first.open_evidence_chart(&user, &id).unwrap_err().code,
        ErrorCode::PERMISSIONDENIED
    );
    assert_eq!(
        first
            .evidence_chart_display_allowed(&ai, &id)
            .unwrap_err()
            .code,
        ErrorCode::PERMISSIONDENIED
    );
    assert!(
        first
            .evidence_chart_display_allowed(&local_import, &id)
            .unwrap()
    );
    assert!(
        !first
            .evidence_chart_display_allowed(&local_import, &revoked)
            .unwrap()
    );
    let chart = first.open_evidence_chart(&local_import, &id).unwrap();
    assert_eq!(chart["snapshot"]["dataset_id"], id);
    assert_eq!(chart["bars"][1]["bar"]["close"], 8);
    let interval = |time: i64| {
        json!({
            "kind": "chart_interval", "snapshot": chart["snapshot"],
            "range": {"from_ms": time, "to_ms": time + 1},
        })
    };
    let select = |revision, target: Value| {
        json!({
            "operation": SELECT_EVIDENCE, "workspace_id": user.workspace_id,
            "expected_selection_revision": revision, "target": target,
        })
    };
    let read = |revision, fields: Value| {
        json!({
            "operation": READ_CHART_PROJECTION, "workspace_id": user.workspace_id,
            "selection_revision": revision, "requested_fields": fields,
        })
    };
    let envelope = |mut args: Value| {
        args["protocol_version"] = json!("1.1");
        args["type"] = json!("request");
        args["timestamp_ms"] = json!(0);
        args["request_id"] = json!(Uuid::new_v4().to_string());
        args["message_id"] = json!(Uuid::new_v4().to_string());
        args
    };
    let assert_fault = |reply: Value, code: &str| {
        assert_eq!(reply["error"]["code"], code, "{reply}");
        assert_eq!(reply["error"].as_object().unwrap().len(), 3);
        assert!(reply.get("result").is_none() && reply.get("selection").is_none());
    };

    let before = first.workspace_snapshot(&user).unwrap();
    assert_eq!(
        (before.workspace.revision, before.selection_revision),
        (3, 7)
    );
    let a = first
        .execute_workspace(&user, envelope(select(7, interval(start))))
        .unwrap();
    assert_eq!(a["selection"]["selection_revision"], 8);
    let lost = peer
        .execute_workspace(&user, envelope(select(7, interval(second))))
        .unwrap();
    assert_fault(lost, "STALE_CONTEXT");
    let response = peer
        .execute_workspace(&user, envelope(read(8, json!(["time_ms", "close"]))))
        .unwrap();
    assert_eq!(
        response["result"],
        json!({
            "source_reference": id, "revision": id, "fields": ["time_ms", "close"],
            "rows": [{"time_ms": start, "close": 5.0}],
        })
    );
    let mut forged = interval(second);
    forged["snapshot"]["content_hash"] = json!("0".repeat(64));
    assert_fault(
        peer.execute_workspace(&user, envelope(select(8, forged)))
            .unwrap(),
        "SNAPSHOT_UNAVAILABLE",
    );
    assert_fault(
        peer.execute_workspace(&user, envelope(select(8, interval(start + 1))))
            .unwrap(),
        "SNAPSHOT_UNAVAILABLE",
    );
    let mut missing_target = select(8, Value::Null);
    missing_target.as_object_mut().unwrap().remove("target");
    assert_eq!(
        peer.execute_workspace(&user, envelope(missing_target.clone()))
            .unwrap_err()
            .code,
        ErrorCode::INVALIDARGUMENT,
    );
    assert_eq!(
        peer.execute_evidence_intent(&ai, None, missing_target)
            .unwrap_err()
            .code,
        ErrorCode::INVALIDARGUMENT,
    );
    assert_fault(
        peer.execute_workspace(
            &user,
            envelope(select(
                8,
                json!({"kind": "chart", "object_id": "main-chart"}),
            )),
        )
        .unwrap(),
        "UNSUPPORTED_CAPABILITY",
    );
    for other in [
        WorkspaceAccess {
            profile_id: "different-profile",
            ..user
        },
        WorkspaceAccess {
            workspace_id: "different-workspace",
            ..user
        },
        WorkspaceAccess {
            operations: &[],
            ..user
        },
        WorkspaceAccess {
            paired_client_id: Some("external-client"),
            ..user
        },
    ] {
        assert_fault(
            peer.execute_workspace(&other, envelope(read(8, json!(["close"]))))
                .unwrap(),
            "PERMISSION_DENIED",
        );
        assert_fault(
            peer.execute_workspace(&other, envelope(select(8, interval(second))))
                .unwrap(),
            "PERMISSION_DENIED",
        );
    }

    let approved_range = VisibleRange {
        from_ms: start,
        to_ms: second + 1,
    };
    let grant = LocalChartAnalysisGrant {
        principal_id: ai.principal_id,
        revision_id: &id,
        range: &approved_range,
        fields: &[ExportField11::Close, ExportField11::Volume],
    };
    assert_fault(
        peer.execute_evidence_intent(&ai, None, select(8, interval(second)))
            .unwrap(),
        "PERMISSION_DENIED",
    );
    assert_fault(
        peer.execute_evidence_intent(&ai, None, read(8, json!(["close"])))
            .unwrap(),
        "PERMISSION_DENIED",
    );
    let b = peer
        .execute_evidence_intent(&ai, Some(&grant), select(8, interval(second)))
        .unwrap();
    assert_eq!(b["selection"]["target"], interval(second));
    assert_eq!(b["selection"]["selection_revision"], 9);
    assert_fault(
        first
            .execute_workspace(&user, envelope(read(8, json!(["close"]))))
            .unwrap(),
        "STALE_CONTEXT",
    );
    assert_fault(
        peer.execute_evidence_intent(&ai, Some(&grant), read(9, json!(["high", "close"])))
            .unwrap(),
        "PERMISSION_DENIED",
    );
    let response = peer
        .execute_evidence_intent(&ai, Some(&grant), read(9, json!(["close", "volume"])))
        .unwrap();
    assert_eq!(
        response["result"]["rows"],
        json!([{"close": 8.0, "volume": null}])
    );

    let rollback: Result<(), _> = store.transact(|tx| {
        tx.set_chart_evidence(10, None)?;
        Err(failure(ErrorCode::CANCELLED))
    });
    assert_eq!(rollback.unwrap_err().code, ErrorCode::CANCELLED);
    drop(first);
    drop(peer);
    let mut reopened = OperationRegistry::new(profile.open().unwrap());
    let restored = reopened.workspace_snapshot(&user).unwrap();
    assert_eq!(
        (restored.workspace.revision, restored.selection_revision),
        (3, 9)
    );
    assert_eq!(
        restored.chart_evidence.unwrap()["range"],
        interval(second)["range"]
    );
    assert_eq!(
        restored.undo.unwrap().operation_id,
        before.undo.unwrap().operation_id
    );
    assert!(store.revoke_local_dataset(&id).unwrap());
    assert!(
        !reopened
            .evidence_chart_display_allowed(&local_import, &id)
            .unwrap()
    );
    assert_fault(
        reopened
            .execute_evidence_intent(&ai, Some(&grant), read(9, json!(["close"])))
            .unwrap(),
        "SNAPSHOT_UNAVAILABLE",
    );
    assert_fault(
        reopened
            .execute_workspace(&user, envelope(select(9, interval(start))))
            .unwrap(),
        "SNAPSHOT_UNAVAILABLE",
    );
    assert_eq!(
        reopened.workspace_snapshot(&user).unwrap().chart_evidence,
        Some(json!({"unavailable": true}))
    );
    let cleared = reopened
        .execute_workspace(&user, envelope(select(9, Value::Null)))
        .unwrap();
    assert_eq!(
        cleared["selection"],
        json!({
            "workspace_id": user.workspace_id, "selection_revision": 10, "target": null,
        })
    );
    assert_fault(
        reopened
            .execute_workspace(&user, envelope(read(10, json!(["close"]))))
            .unwrap(),
        "SNAPSHOT_UNAVAILABLE",
    );
    assert_eq!(
        (
            store.snapshot(None, false).unwrap().state.revision,
            store
                .snapshot(None, false)
                .unwrap()
                .state
                .selection_revision
        ),
        (3, 10)
    );
    assert_eq!(dataset_counts(&store.connection), (2, 2));
}
