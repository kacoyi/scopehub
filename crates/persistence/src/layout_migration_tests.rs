use scopehub_application::{
    FolderManifestBar, FolderManifestLocator, FolderManifestPublication, FolderManifestRequest,
    FolderManifestSource, FolderManifestStore, PRIVATE_RECORD_CHANGE,
};

use super::*;

const LAYOUT_TABLES: [&str; 3] = [
    "workspace_layout_changes",
    "workspace_layouts",
    "workspace_source_preference",
];
const PRIVATE_RECORD_ID: &str = "9014c000-88f0-43e7-9069-000000000001";
const DELETED_RECORD_ID: &str = "9014c000-88f0-43e7-9069-000000000002";

fn populate_v8_private_records(connection: &Connection) {
    connection
        .execute_batch(
            "UPDATE workspaces SET revision = 6;
             INSERT INTO operation_log
               (id, workspace_id, operation, actor, principal_id, paired_client_id, request_id,
                committed_at_ms, revision, undoable, before_timeframe, after_timeframe)
             VALUES
               ('save-memory', 'workspace', 'privateRecord.change', 'user', 'principal', '',
                'save-memory', 400, 4, 0, '1d', '1d'),
               ('save-deleted', 'workspace', 'privateRecord.change', 'user', 'principal', '',
                'save-deleted', 500, 5, 0, '1d', '1d'),
               ('delete-memory', 'workspace', 'privateRecord.change', 'user', 'principal', '',
                'delete-memory', 600, 6, 0, '1d', '1d');
             INSERT INTO idempotency_records VALUES
               ('workspace', 'principal', '', 'privateRecord.change', 'save-memory-retry',
                zeroblob(32), 'save-memory');",
        )
        .unwrap();
    connection
        .execute(
            "INSERT INTO private_records VALUES ('workspace', ?1, 4, ?2)",
            params![
                PRIVATE_RECORD_ID,
                json!({"kind": "memory", "title": "Saved review", "text": "Synthetic body"})
                    .to_string(),
            ],
        )
        .unwrap();
    connection
        .execute(
            "INSERT INTO private_record_deletions VALUES ('workspace', ?1, 6)",
            [DELETED_RECORD_ID],
        )
        .unwrap();
    connection
        .execute(
            "INSERT INTO private_record_receipts VALUES
               ('save-memory', ?1), ('save-deleted', ?2), ('delete-memory', ?2)",
            params![PRIVATE_RECORD_ID, DELETED_RECORD_ID],
        )
        .unwrap();
}

#[test]
fn populated_v8_layout_migration_rolls_back_and_restores_all_previous_state() {
    const RELATIVE_PATH: &str = "history/equity.csv";
    let profile = Profile::new();
    let (mut v8, legacy) = populated_v6(&profile);
    {
        let transaction = v8.connection.transaction().unwrap();
        for (version, sql) in [
            (
                FOLDER_MANIFEST_SCHEMA_VERSION,
                include_str!("folder_manifest.sql"),
            ),
            (
                PRIVATE_RECORD_SCHEMA_VERSION,
                include_str!("private_records.sql"),
            ),
        ] {
            transaction.execute_batch(sql).unwrap();
            record_migration(&transaction, version).unwrap();
        }
        populate_v8_private_records(&transaction);
        transaction.commit().unwrap();
    }
    let binding = Profile::binding();
    let request = FolderManifestRequest {
        principal_id: &binding.profile_id,
        idempotency_key: "synthetic-v8-history",
    };
    let publication = FolderManifestPublication::new(
        &legacy.displayed.revision_id,
        vec![FolderManifestSource {
            relative_path: RELATIVE_PATH,
            sha256: LEGACY_SHA256,
            record_count: 1,
            original: LEGACY_CSV,
        }],
        vec![FolderManifestBar {
            bar: serde_json::from_value(legacy_content(LEGACY_SHA256, 5)["bars"][0]["bar"].clone())
                .unwrap(),
            locators: vec![FolderManifestLocator {
                relative_path: RELATIVE_PATH.into(),
                record_number: 1,
                line_start: 2,
                line_end: 2,
            }],
        }],
    )
    .unwrap();
    let manifest_id = v8
        .publish_folder_manifest(&request, &publication, &CommitMapping)
        .unwrap();
    let manifest = v8.read_folder_manifest(&manifest_id).unwrap().unwrap();
    let prior_tables = tables(&v8.connection);
    let assert_legacy_state = |store: &SqliteWorkspace| {
        let (state, undo, selected) = legacy_chart_view(store);
        assert_eq!(
            (state.revision, state.selection_revision, state.timeframe),
            (6, 7, Timeframe::Day)
        );
        assert_eq!(undo.unwrap().operation_id, "legacy-timeframe");
        assert!(matches!(selected, Some((target, false)) if target == legacy.displayed));
        let backup = store.decision_note_backup().unwrap();
        assert_eq!(backup.revision, 6);
        assert_eq!(backup.notes.len(), 1);
        assert_eq!(backup.notes[0].original.thesis, "thesis");
        assert_eq!(backup.notes[0].outcomes[0].outcome_id, "outcome");
        store
            .inspect(|tx| {
                let entry = tx.private_record(PRIVATE_RECORD_ID)?.unwrap();
                assert_eq!(entry.revision, 4);
                assert_eq!(
                    serde_json::to_value(entry.content).unwrap(),
                    json!({
                        "kind": "memory", "title": "Saved review", "text": "Synthetic body"
                    })
                );
                assert_eq!(tx.private_record_deletions()?, [DELETED_RECORD_ID]);
                assert!(tx.private_record(DELETED_RECORD_ID)?.is_none());
                let replay = tx
                    .replay(&key(PRIVATE_RECORD_CHANGE, "save-memory-retry"))?
                    .unwrap();
                assert_eq!(replay.receipt.operation_id, "save-memory");
                assert_eq!(
                    tx.private_record_receipt_target(&replay.receipt.operation_id)?
                        .as_deref(),
                    Some(PRIVATE_RECORD_ID)
                );
                Ok(())
            })
            .unwrap();
        assert_eq!(
            store
                .load_mapping(SOURCE_KEY)
                .unwrap()
                .unwrap()
                .review_revision,
            1
        );
        let frozen = store
            .read_local_dataset(&legacy.displayed.revision_id)
            .unwrap()
            .unwrap();
        assert_eq!(frozen.content, legacy_content(LEGACY_SHA256, 5));
        assert_eq!(
            store
                .read_local_dataset(&legacy.revoked)
                .err()
                .unwrap()
                .code,
            ErrorCode::PERMISSIONDENIED
        );
        assert_eq!(
            store.read_folder_manifest(&manifest_id).unwrap().as_ref(),
            Some(&manifest)
        );
        assert_eq!(
            store
                .read_folder_manifest_source(&manifest_id, RELATIVE_PATH)
                .unwrap()
                .as_deref(),
            Some(LEGACY_CSV)
        );
        assert_eq!(
            store
                .replay_folder_manifest(&request, publication.input_sha256())
                .unwrap()
                .as_deref(),
            Some(manifest_id.as_str())
        );
    };
    assert_legacy_state(&v8);
    assert_eq!(ledger(&v8.connection), [1, 2, 3, 4, 5, 6, 7, 8]);
    v8.connection
        .execute_batch("PRAGMA wal_checkpoint(TRUNCATE)")
        .unwrap();
    let backup = fs::read(profile.db()).unwrap();
    v8.connection
        .execute_batch(
            "CREATE TRIGGER fail_layout_migration
             BEFORE INSERT ON schema_migrations WHEN NEW.version = 9
             BEGIN SELECT raise(ABORT, 'synthetic v9 ledger failure'); END;",
        )
        .unwrap();
    drop(v8);

    assert!(matches!(profile.open(), Err(OpenWorkspaceError::Database)));
    let v8 = SqliteWorkspace {
        connection: profile.raw_connection(),
        binding: Profile::binding(),
    };
    assert_eq!(integer_pragma(&v8.connection, "user_version"), 8);
    assert_eq!(ledger(&v8.connection), [1, 2, 3, 4, 5, 6, 7, 8]);
    assert_eq!(tables(&v8.connection), prior_tables);
    assert_legacy_state(&v8);
    v8.connection
        .execute_batch("DROP TRIGGER fail_layout_migration")
        .unwrap();
    drop(v8);

    let restored = Profile::new();
    drop(restored.raw_connection());
    fs::write(restored.db(), backup).unwrap();
    for recovered in [&profile, &profile, &restored] {
        let v9 = recovered.open().unwrap();
        assert_eq!(integer_pragma(&v9.connection, "user_version"), 9);
        assert_eq!(ledger(&v9.connection), [1, 2, 3, 4, 5, 6, 7, 8, 9]);
        let mut expected_tables = prior_tables.clone();
        expected_tables.extend(LAYOUT_TABLES.map(String::from));
        expected_tables.sort();
        assert_eq!(tables(&v9.connection), expected_tables);
        for table in LAYOUT_TABLES {
            let rows: i64 = v9
                .connection
                .query_row(&format!("SELECT count(*) FROM {table}"), [], |row| {
                    row.get(0)
                })
                .unwrap();
            assert_eq!(rows, 0);
        }
        assert_legacy_state(&v9);
        let StoredWorkspaceView {
            chart_evidence: evidence,
            layouts,
            source,
            ..
        } = v9.snapshot(None, true).unwrap();
        assert!(
            matches!(evidence, Some(ChartEvidenceStatus::Available(target)) if target == legacy.displayed)
        );
        assert!(
            layouts.active.name.is_none() && layouts.saved.is_empty() && layouts.undo.is_none()
        );
        assert!(source.is_none());
        assert!(
            v9.connection
                .prepare("PRAGMA foreign_key_check")
                .unwrap()
                .query([])
                .unwrap()
                .next()
                .unwrap()
                .is_none()
        );
    }
}
