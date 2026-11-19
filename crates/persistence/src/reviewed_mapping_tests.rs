use std::{sync::Barrier, thread};

use scopehub_application::{MappingCommitGuard, ReviewedMappingStore};
use scopehub_domain::protocol::CsvMappingV1;
use serde_json::json;

use super::*;

pub(super) const SOURCE_KEY: &str = "synthetic-equity";
pub(super) const FIRST_HEADER: &str =
    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const SECOND_HEADER: &str = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";

pub(super) fn test_mapping(revision: &str) -> CsvMappingV1 {
    serde_json::from_value(json!({
        "version": 1,
        "revision": revision,
        "encoding": "utf-8",
        "delimiter": ",",
        "record_kind": "native_ohlcv",
        "instrument_kind": "equity",
        "columns": {
            "symbol": "Instrument", "timestamp": "Date", "trading_day": "Date",
            "open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": null
        },
        "venue": "TEST",
        "timezone": "UTC",
        "calendar_id": "fixture-calendar-1",
        "session_scope": "regular",
        "timeframe": "1d",
        "quantity_unit": "shares",
        "price_adjustment": "raw",
        "volume_semantics": "per_bar",
        "timestamp_convention": "bar_start",
        "timestamp_format": "%Y-%m-%d",
        "trading_day_format": "%Y-%m-%d",
        "daily_start_time": "09:30:00"
    }))
    .unwrap()
}

pub(super) struct CommitMapping;

impl MappingCommitGuard for CommitMapping {
    fn commit<T>(&self, apply: impl FnOnce() -> Result<T, Error>) -> Result<T, Error> {
        apply()
    }
}

struct CancelMapping;

impl MappingCommitGuard for CancelMapping {
    fn commit<T>(&self, _apply: impl FnOnce() -> Result<T, Error>) -> Result<T, Error> {
        Err(failure(ErrorCode::CANCELLED))
    }
}

fn mapping_versions(writer: &SqliteWorkspace) -> Vec<i64> {
    let mut statement = writer
        .connection
        .prepare(
            "SELECT review_revision FROM reviewed_import_mappings
             WHERE source_key = ?1 ORDER BY review_revision",
        )
        .unwrap();
    statement
        .query_map([SOURCE_KEY], |row| row.get(0))
        .unwrap()
        .collect::<Result<_, _>>()
        .unwrap()
}

#[test]
fn equivalent_concurrent_saves_reuse_the_latest_version_and_keep_review_history() {
    let profile = Profile::new();
    let mut writer = profile.open().unwrap();
    let mut peer = profile.open().unwrap();
    let original = test_mapping("same");
    let start = Barrier::new(2);
    let current = thread::scope(|scope| {
        let other = scope.spawn(|| {
            start.wait();
            peer.save_mapping(SOURCE_KEY, FIRST_HEADER, &original, &CommitMapping)
                .unwrap()
        });
        start.wait();
        let current = writer
            .save_mapping(SOURCE_KEY, FIRST_HEADER, &original, &CommitMapping)
            .unwrap();
        assert_eq!(other.join().unwrap(), current);
        current
    });
    assert_eq!(current.review_revision, 1);
    assert_eq!(mapping_versions(&writer), [1]);
    assert_eq!(
        writer
            .save_mapping(SOURCE_KEY, FIRST_HEADER, &original, &CancelMapping)
            .unwrap_err()
            .code,
        ErrorCode::CANCELLED,
    );

    // A changed ordered header is a different review even with the same mapping.
    let second = writer
        .save_mapping(SOURCE_KEY, SECOND_HEADER, &original, &CommitMapping)
        .unwrap();
    assert_eq!(second.review_revision, 2);
    let returned = writer
        .save_mapping(SOURCE_KEY, FIRST_HEADER, &original, &CommitMapping)
        .unwrap();
    assert_eq!(returned.review_revision, 3);
    assert_eq!(mapping_versions(&writer), [1, 2, 3]);
    assert_eq!(
        writer.load_mapping_version(SOURCE_KEY, 1).unwrap(),
        Some(current)
    );
}

#[test]
fn reviewed_mapping_survives_restart_and_a_cancelled_new_version() {
    let profile = Profile::new();
    let first = test_mapping("reviewed-1");
    let second = test_mapping("reviewed-2");
    let mut writer = profile.open().unwrap();
    let stored = writer
        .save_mapping(SOURCE_KEY, FIRST_HEADER, &first, &CommitMapping)
        .unwrap();
    assert_eq!(stored.review_revision, 1);
    assert_eq!(
        writer
            .save_mapping(SOURCE_KEY, SECOND_HEADER, &second, &CancelMapping)
            .unwrap_err()
            .code,
        ErrorCode::CANCELLED
    );
    assert_eq!(
        writer
            .save_mapping("cancelled-new-source", FIRST_HEADER, &first, &CancelMapping)
            .unwrap_err()
            .code,
        ErrorCode::CANCELLED
    );
    drop(writer);

    let mut reopened = profile.open().unwrap();
    let stored = reopened.load_mapping(SOURCE_KEY).unwrap().unwrap();
    assert_eq!(stored.review_revision, 1);
    assert_eq!(stored.header_sha256, FIRST_HEADER);
    assert_eq!(stored.mapping, first);
    assert_eq!(mapping_versions(&reopened), [1]);
    assert!(
        reopened
            .load_mapping("cancelled-new-source")
            .unwrap()
            .is_none()
    );

    let saved = reopened
        .save_mapping(SOURCE_KEY, SECOND_HEADER, &second, &CommitMapping)
        .unwrap();
    assert_eq!(saved.review_revision, 2);
    assert_eq!(saved.header_sha256, SECOND_HEADER);
    assert_eq!(saved.mapping, second);
    assert_eq!(mapping_versions(&reopened), [1, 2]);
    drop(reopened);

    let unrelated_root = Profile::new();
    let unrelated = SqliteWorkspace::open(
        &unrelated_root.0,
        WorkspaceBinding {
            profile_id: "unrelated-profile".into(),
            ..Profile::binding()
        },
    )
    .unwrap();
    assert!(unrelated.load_mapping(SOURCE_KEY).unwrap().is_none());
}

#[test]
fn concurrent_writers_allocate_distinct_versions_for_the_same_source() {
    let profile = Profile::new();
    let mut first = profile.open().unwrap();
    let mut second = profile.open().unwrap();
    let start = Barrier::new(2);
    let mut revisions = thread::scope(|scope| {
        let a = scope.spawn(|| {
            start.wait();
            first
                .save_mapping(SOURCE_KEY, FIRST_HEADER, &test_mapping("a"), &CommitMapping)
                .unwrap()
                .review_revision
        });
        let b = scope.spawn(|| {
            start.wait();
            second
                .save_mapping(
                    SOURCE_KEY,
                    SECOND_HEADER,
                    &test_mapping("b"),
                    &CommitMapping,
                )
                .unwrap()
                .review_revision
        });
        [a.join().unwrap(), b.join().unwrap()]
    });
    revisions.sort_unstable();
    assert_eq!(revisions, [1, 2]);
    let writer = profile.open().unwrap();
    assert_eq!(mapping_versions(&writer), [1, 2]);
    assert_eq!(
        writer
            .load_mapping(SOURCE_KEY)
            .unwrap()
            .unwrap()
            .review_revision,
        2,
    );
}

pub(super) fn v3_workspace(profile: &Profile) -> Connection {
    let mut connection = profile.raw_connection();
    connection
        .pragma_update(None, "foreign_keys", true)
        .unwrap();
    let transaction = connection
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .unwrap();
    migrate_initial(&transaction, &Profile::binding()).unwrap();
    migrate_decision_notes(&transaction).unwrap();
    transaction
        .execute_batch(include_str!("decision_note_restore.sql"))
        .unwrap();
    record_migration(&transaction, DECISION_NOTE_RESTORE_SCHEMA_VERSION).unwrap();
    transaction
        .execute_batch(
            "UPDATE workspaces SET revision = 2, timeframe = '5m';
             INSERT INTO operation_log VALUES
               ('save', 'workspace', 'decisionNote.save', 'user', 'principal', '',
                'save', 100, 1, 0, '5m', '5m', NULL, 'note'),
               ('outcome', 'workspace', 'decisionNote.appendOutcome', 'user', 'principal', '',
                'outcome', 200, 2, 0, '5m', '5m', NULL, 'note');
             INSERT INTO decision_notes VALUES ('workspace', 'note', '2026-06-01', 'thesis', '[]', 'choice');
             INSERT INTO decision_note_outcomes VALUES
               ('workspace', 'note', 'outcome', '2026-06-02', 'synthetic outcome', 2, 1);",
        )
        .unwrap();
    transaction.commit().unwrap();
    connection
}

pub(super) fn ledger(connection: &Connection) -> Vec<i64> {
    let mut query = connection
        .prepare("SELECT version FROM schema_migrations ORDER BY version")
        .unwrap();
    query
        .query_map([], |row| row.get(0))
        .unwrap()
        .collect::<Result<_, _>>()
        .unwrap()
}

#[test]
fn v3_upgrade_is_atomic_and_preserves_workspace_notes_and_outcomes() {
    let profile = Profile::new();
    let mut database = v3_workspace(&profile);
    let transaction = database
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .unwrap();
    migrate(&transaction, &Profile::binding()).unwrap();
    assert_eq!(ledger(&transaction), current_ledger());
    assert_eq!(integer_pragma(&transaction, "user_version"), SCHEMA_VERSION);
    drop(transaction);
    assert_eq!(ledger(&database), [1, 2, 3]);
    assert_eq!(integer_pragma(&database, "user_version"), 3);
    assert!(
        database
            .prepare("SELECT source_key FROM reviewed_import_mappings")
            .is_err()
    );
    drop(database);

    let v3_bytes = fs::read(profile.db()).unwrap();
    let unrelated = WorkspaceBinding {
        profile_id: "unrelated-profile".into(),
        ..Profile::binding()
    };
    assert!(matches!(
        SqliteWorkspace::open(&profile.0, unrelated),
        Err(OpenWorkspaceError::ProfileMismatch)
    ));
    assert_eq!(fs::read(profile.db()).unwrap(), v3_bytes);

    let writer = profile.open().unwrap();
    assert_eq!(ledger(&writer.connection), current_ledger());
    let state = writer.snapshot(None, false).unwrap().0;
    assert_eq!(
        (state.revision, state.timeframe),
        (2, Timeframe::FiveMinutes)
    );
    let notes = writer.read_notes(Some("note"), 0).unwrap();
    assert_eq!(notes.note.unwrap().thesis, "thesis");
    assert_eq!(notes.outcomes[0].outcome_id, "outcome");
    assert!(writer.load_mapping(SOURCE_KEY).unwrap().is_none());
}

#[test]
fn v3_with_an_invalid_ledger_is_refused_before_any_upgrade() {
    let profile = Profile::new();
    let database = v3_workspace(&profile);
    database
        .execute("DELETE FROM schema_migrations WHERE version = 2", [])
        .unwrap();
    drop(database);
    let before = fs::read(profile.db()).unwrap();
    assert!(matches!(
        profile.open(),
        Err(OpenWorkspaceError::UnsupportedFormat)
    ));
    assert_eq!(fs::read(profile.db()).unwrap(), before);
}

#[test]
fn fresh_workspace_has_the_complete_ledger_and_no_reviewed_mapping() {
    let profile = Profile::new();
    let writer = profile.open().unwrap();
    assert_eq!(ledger(&writer.connection), current_ledger());
    assert_eq!(
        integer_pragma(&writer.connection, "user_version"),
        SCHEMA_VERSION
    );
    assert!(writer.load_mapping(SOURCE_KEY).unwrap().is_none());
}
