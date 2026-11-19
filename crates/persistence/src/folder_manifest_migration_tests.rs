use scopehub_application::{
    LocalDatasetPublication, LocalDatasetStore, ReviewedMappingStore, local_mapping_fingerprint,
};
use serde_json::{Value, json};

use super::{
    reviewed_mapping_tests::{
        CommitMapping, FIRST_HEADER, SOURCE_KEY, ledger, test_mapping, v3_workspace,
    },
    *,
};

const LEGACY_CSV: &[u8] =
    b"Date,Instrument,Open,High,Low,Close,Unknown\n2026-01-02,000001,4,6,3,5,retained\n";
const LEGACY_SHA256: &str = "ca2c7f20f64aa355407e601d3cfd539f79fc81277cce3786dc2183cf2fab01df";
const REVOKED_CSV: &[u8] =
    b"Date,Instrument,Open,High,Low,Close,Unknown\n2026-01-02,000001,4,7,3,6,retained\n";
const REVOKED_SHA256: &str = "993232444793f579b2ed3c4a2930938a13b93b6bf2faa32c7145a4acd698d3b8";
const BAR_TIME: i64 = 1_767_346_200_000;
const MANIFEST_TABLES: [&str; 6] = [
    "local_folder_manifest_bars",
    "local_folder_manifest_locators",
    "local_folder_manifest_receipts",
    "local_folder_manifest_revisions",
    "local_folder_manifest_revocations",
    "local_folder_manifest_sources",
];

struct LegacyPublication {
    displayed: ChartEvidenceRef,
    revoked: String,
}

fn legacy_content(digest: &str, close: i64) -> Value {
    json!({
        "file_sha256": digest, "mapping_version": 1, "mapping_revision": "legacy-v6",
        "header": ["Date", "Instrument", "Open", "High", "Low", "Close", "Unknown"],
        "series": {"instrument_kind": "equity", "venue": "TEST", "timeframe": "1d",
            "calendar_id": "fixture-calendar-1", "session_scope": "regular", "timezone": "UTC",
            "quantity_unit": "shares", "price_adjustment": "raw", "roll_method": null},
        "symbol": "000001", "source_record_count": 1,
        "bars": [{"bar": {"time_ms": BAR_TIME, "trading_day": "2026-01-02",
            "open": 4, "high": close + 1, "low": 3, "close": close, "volume": null, "is_closed": true},
            "locators": [{"record_number": 1, "line_start": 2, "line_end": 2}]}]
    })
}

fn populated_v6(profile: &Profile) -> (SqliteWorkspace, LegacyPublication) {
    let mut connection = v3_workspace(profile);
    let transaction = connection
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .unwrap();
    for (version, sql) in [
        (
            REVIEWED_IMPORT_SCHEMA_VERSION,
            include_str!("reviewed_import.sql"),
        ),
        (
            LOCAL_DATASET_SCHEMA_VERSION,
            include_str!("local_dataset.sql"),
        ),
        (
            CHART_EVIDENCE_SCHEMA_VERSION,
            include_str!("chart_evidence.sql"),
        ),
    ] {
        transaction.execute_batch(sql).unwrap();
        record_migration(&transaction, version).unwrap();
    }
    transaction.commit().unwrap();

    let mut store = SqliteWorkspace {
        connection,
        binding: Profile::binding(),
    };
    let mapping = store
        .save_mapping(
            SOURCE_KEY,
            FIRST_HEADER,
            &test_mapping("legacy-v6"),
            &CommitMapping,
        )
        .unwrap();
    let mapping_sha256 = local_mapping_fingerprint(&mapping.mapping).unwrap();
    let [revision_id, revoked] = [
        (LEGACY_SHA256, LEGACY_CSV, 5),
        (REVOKED_SHA256, REVOKED_CSV, 6),
    ]
    .map(|(source_sha256, original, close)| {
        store
            .publish_local_dataset(
                &LocalDatasetPublication {
                    source_key: SOURCE_KEY,
                    relative_path: "history/equity.csv",
                    source_sha256,
                    mapping_sha256: &mapping_sha256,
                    reviewed_mapping: &mapping,
                    original,
                    content: &legacy_content(source_sha256, close),
                },
                &CommitMapping,
            )
            .unwrap()
    });
    assert!(store.revoke_local_dataset(&revoked).unwrap());
    let displayed = ChartEvidenceRef {
        revision_id,
        range: VisibleRange {
            from_ms: BAR_TIME,
            to_ms: BAR_TIME + 1,
        },
    };
    store
        .transact(|tx| {
            tx.record(&WorkspaceChange {
                key: key(SET_TIMEFRAME, "legacy-timeframe-retry"),
                digest: [23; 32],
                receipt: WorkspaceReceipt {
                    operation_id: "legacy-timeframe".into(),
                    revision: 3,
                    undoable: true,
                    before: Timeframe::FiveMinutes,
                    after: Timeframe::Day,
                },
                actor: Actor::User,
                request_id: "legacy-timeframe-request",
                committed_at_ms: 300,
                undoes: None,
            })?;
            tx.set_chart_evidence(7, Some(&displayed))
        })
        .unwrap();
    (store, LegacyPublication { displayed, revoked })
}

fn assert_legacy_workspace(store: &SqliteWorkspace, publication: &LegacyPublication) {
    let (state, undo, selected) = store
        .snapshot(
            Some(WorkspaceUndoAccess {
                actor: Actor::User,
                principal_id: "principal",
                paired_client_id: None,
            }),
            true,
        )
        .unwrap();
    assert_eq!(
        (state.revision, state.selection_revision, state.timeframe),
        (3, 7, Timeframe::Day)
    );
    assert_eq!(undo.unwrap().operation_id, "legacy-timeframe");
    assert!(
        matches!(selected, Some(ChartEvidenceStatus::Available(target)) if target == publication.displayed)
    );
    let replay = store
        .inspect(|tx| tx.replay(&key(SET_TIMEFRAME, "legacy-timeframe-retry")))
        .unwrap()
        .unwrap();
    assert_eq!(replay.receipt.operation_id, "legacy-timeframe");
    assert_eq!(replay.receipt.revision, 3);
    assert_eq!(replay.digest, [23; 32]);

    let mapping = store.load_mapping(SOURCE_KEY).unwrap().unwrap();
    assert_eq!(mapping.review_revision, 1);
    assert_eq!(mapping.header_sha256, FIRST_HEADER);
    assert_eq!(mapping.mapping, test_mapping("legacy-v6"));
    let frozen = store
        .read_local_dataset(&publication.displayed.revision_id)
        .unwrap()
        .unwrap();
    assert_eq!(frozen.source_key, SOURCE_KEY);
    assert_eq!(frozen.source_sha256, LEGACY_SHA256);
    assert_eq!(
        frozen.mapping_sha256,
        local_mapping_fingerprint(&mapping.mapping).unwrap()
    );
    assert_eq!(frozen.content, legacy_content(LEGACY_SHA256, 5));
    assert_eq!(
        store
            .read_local_dataset(&publication.revoked)
            .err()
            .unwrap()
            .code,
        ErrorCode::PERMISSIONDENIED
    );
    for (digest, original) in [(LEGACY_SHA256, LEGACY_CSV), (REVOKED_SHA256, REVOKED_CSV)] {
        let actual: Vec<u8> = store
            .connection
            .query_row(
                "SELECT original_bytes FROM local_dataset_source_blobs WHERE source_sha256 = ?1",
                [digest],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(actual, original);
    }

    let backup = store.decision_note_backup().unwrap();
    assert_eq!(backup.revision, 3);
    assert_eq!(backup.notes.len(), 1);
    assert_eq!(backup.notes[0].original.thesis, "thesis");
    assert_eq!(backup.notes[0].outcomes[0].outcome_id, "outcome");
    assert!(backup.deletions.is_empty());
}

fn tables(connection: &Connection) -> Vec<String> {
    connection
        .prepare("SELECT name FROM sqlite_schema WHERE type = 'table' ORDER BY name")
        .unwrap()
        .query_map([], |row| row.get(0))
        .unwrap()
        .collect::<rusqlite::Result<_>>()
        .unwrap()
}

fn assert_migrated_manifest(store: &SqliteWorkspace, legacy_tables: &[String]) {
    assert_eq!(
        integer_pragma(&store.connection, "user_version"),
        SCHEMA_VERSION
    );
    assert_eq!(ledger(&store.connection), current_ledger());
    let migrated_tables = tables(&store.connection);
    let new_tables: Vec<_> = migrated_tables
        .iter()
        .filter(|table| table.starts_with("local_folder_manifest_"))
        .map(String::as_str)
        .collect();
    assert_eq!(new_tables, MANIFEST_TABLES);
    assert!(
        legacy_tables
            .iter()
            .all(|table| migrated_tables.contains(table))
    );
    for table in MANIFEST_TABLES {
        let count: i64 = store
            .connection
            .query_row(&format!("SELECT count(*) FROM {table}"), [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(
            count, 0,
            "legacy sources must remain single-file publications"
        );
    }
    assert!(
        store
            .connection
            .prepare("PRAGMA foreign_key_check")
            .unwrap()
            .query([])
            .unwrap()
            .next()
            .unwrap()
            .is_none()
    );
}

#[test]
fn populated_v6_folder_manifest_migration_rolls_back_and_reopens_without_rewriting_legacy_data() {
    let profile = Profile::new();
    let (v6, legacy) = populated_v6(&profile);
    assert_legacy_workspace(&v6, &legacy);
    let legacy_tables = tables(&v6.connection);
    let backup = fs::read(profile.db()).unwrap();
    v6.connection
        .execute_batch(
            "CREATE TRIGGER fail_folder_manifest_migration
         BEFORE INSERT ON schema_migrations WHEN NEW.version = 7
         BEGIN SELECT raise(ABORT, 'synthetic v7 ledger failure'); END;",
        )
        .unwrap();
    drop(v6);

    // Force a failure after v7 DDL and before its ledger row commits via the real writer open.
    assert!(matches!(profile.open(), Err(OpenWorkspaceError::Database)));
    let v6 = SqliteWorkspace {
        connection: profile.raw_connection(),
        binding: Profile::binding(),
    };
    assert_eq!(
        integer_pragma(&v6.connection, "user_version"),
        CHART_EVIDENCE_SCHEMA_VERSION
    );
    assert_eq!(ledger(&v6.connection), [1, 2, 3, 4, 5, 6]);
    assert_eq!(tables(&v6.connection), legacy_tables);
    assert_legacy_workspace(&v6, &legacy);
    v6.connection
        .execute_batch("DROP TRIGGER fail_folder_manifest_migration")
        .unwrap();
    drop(v6);

    for _ in 0..2 {
        let v7 = profile.open().unwrap();
        assert_migrated_manifest(&v7, &legacy_tables);
        assert_legacy_workspace(&v7, &legacy);
    }

    let restored = Profile::new();
    drop(restored.raw_connection());
    fs::write(restored.db(), backup).unwrap();
    let v7 = restored.open().unwrap();
    assert_migrated_manifest(&v7, &legacy_tables);
    assert_legacy_workspace(&v7, &legacy);
}
