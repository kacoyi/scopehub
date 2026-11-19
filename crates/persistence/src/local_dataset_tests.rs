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
    assert_eq!(ledger(&transaction), [1, 2, 3, 4, 5]);
    assert_eq!(integer_pragma(&transaction, "user_version"), 5);
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
    assert_eq!(integer_pragma(&writer.connection, "user_version"), 5);
    assert_eq!(ledger(&writer.connection), [1, 2, 3, 4, 5]);
    let state = writer.snapshot(None).unwrap().0;
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
    assert_eq!(restored.snapshot(None).unwrap().0.revision, 2);
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
