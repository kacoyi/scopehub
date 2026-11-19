use std::{
    process::{self, Command},
    sync::Barrier,
    thread,
};

use scopehub_application::{
    FolderManifestBar, FolderManifestLocator, FolderManifestPublication, FolderManifestRequest,
    FolderManifestSource, FolderManifestStore, LocalDatasetPublication, LocalDatasetStore,
    MappingCommitGuard, ReviewedMappingStore, local_mapping_fingerprint,
};
use scopehub_domain::protocol::Bar;
use serde_json::{Value, json};

use super::{
    reviewed_mapping_tests::{CommitMapping, FIRST_HEADER, SOURCE_KEY, test_mapping},
    *,
};

const FIRST_CSV: &[u8] =
    b"Date,Instrument,Open,High,Low,Close,Unknown\n2026-01-02,000001,4,6,3,5,retained\n";
const ADDED_CSV: &[u8] =
    b"Date,Instrument,Open,High,Low,Close,Unknown\n2026-01-03,000001,6,9,4,8,retained\n";
const FIRST_DIGEST: &str = "ca2c7f20f64aa355407e601d3cfd539f79fc81277cce3786dc2183cf2fab01df";
const ADDED_DIGEST: &str = "03743332f7d58626602c646e00d1ead76f9e5a5a2575b165645dc4c724f1198b";
const FIRST_TIME: i64 = 1_767_346_200_000;
const FIRST_PATH: &str = "daily.csv";
const ALIAS_PATH: &str = "copies/same.csv";
const ADDED_PATH: &str = "daily-next.csv";
const INTERRUPTED_ROOT: &str = "SCOPEHUB_TEST_INTERRUPTED_MANIFEST_ROOT";
const INTERRUPTED_PARENT: &str = "SCOPEHUB_TEST_INTERRUPTED_MANIFEST_PARENT";
const STAGED_EXIT: i32 = 92;

fn first_bar() -> Bar {
    Bar {
        time_ms: FIRST_TIME,
        trading_day: "2026-01-02".into(),
        open: 4.0,
        high: 6.0,
        low: 3.0,
        close: 5.0,
        volume: None,
        is_closed: true,
    }
}

fn manifest_bar(bar: Bar, paths: &[&str]) -> FolderManifestBar {
    FolderManifestBar {
        bar,
        locators: paths
            .iter()
            .map(|path| FolderManifestLocator {
                relative_path: (*path).into(),
                record_number: 1,
                line_start: 2,
                line_end: 2,
            })
            .collect(),
    }
}

fn first_source(relative_path: &'static str) -> FolderManifestSource<'static> {
    FolderManifestSource {
        relative_path,
        sha256: FIRST_DIGEST,
        record_count: 1,
        original: FIRST_CSV,
    }
}

fn complete_sources() -> (Vec<FolderManifestSource<'static>>, Vec<FolderManifestBar>) {
    let added_bar = Bar {
        time_ms: FIRST_TIME + 86_400_000,
        trading_day: "2026-01-03".into(),
        open: 6.0,
        high: 9.0,
        low: 4.0,
        close: 8.0,
        volume: None,
        is_closed: true,
    };
    (
        vec![
            first_source(FIRST_PATH),
            first_source(ALIAS_PATH),
            FolderManifestSource {
                relative_path: ADDED_PATH,
                sha256: ADDED_DIGEST,
                record_count: 1,
                original: ADDED_CSV,
            },
        ],
        vec![
            manifest_bar(first_bar(), &[FIRST_PATH, ALIAS_PATH]),
            manifest_bar(added_bar, &[ADDED_PATH]),
        ],
    )
}

fn seed_frozen_csv(writer: &mut SqliteWorkspace) -> (String, Value) {
    let reviewed = writer
        .save_mapping(
            SOURCE_KEY,
            FIRST_HEADER,
            &test_mapping("v4"),
            &CommitMapping,
        )
        .unwrap();
    let content = json!({
        "file_sha256": FIRST_DIGEST, "mapping_version": 1, "mapping_revision": "v4",
        "header": ["Date", "Instrument", "Open", "High", "Low", "Close", "Unknown"],
        "series": {"instrument_kind": "equity", "venue": "TEST", "timeframe": "1d",
            "calendar_id": "fixture-calendar-1", "session_scope": "regular", "timezone": "UTC",
            "quantity_unit": "shares", "price_adjustment": "raw", "roll_method": null},
        "symbol": "000001", "source_record_count": 1,
        "bars": [{"bar": first_bar(),
            "locators": [{"record_number": 1, "line_start": 2, "line_end": 2}]}]
    });
    let id = writer
        .publish_local_dataset(
            &LocalDatasetPublication {
                source_key: SOURCE_KEY,
                relative_path: FIRST_PATH,
                source_sha256: FIRST_DIGEST,
                mapping_sha256: &local_mapping_fingerprint(&reviewed.mapping).unwrap(),
                reviewed_mapping: &reviewed,
                original: FIRST_CSV,
                content: &content,
            },
            &CommitMapping,
        )
        .unwrap();
    (id, content)
}

fn request<'a>(
    binding: &'a WorkspaceBinding,
    idempotency_key: &'a str,
) -> FolderManifestRequest<'a> {
    FolderManifestRequest {
        principal_id: &binding.profile_id,
        idempotency_key,
    }
}

fn counts(connection: &Connection) -> [i64; 7] {
    [
        "local_folder_manifest_revisions",
        "local_folder_manifest_sources",
        "local_folder_manifest_bars",
        "local_folder_manifest_locators",
        "local_folder_manifest_receipts",
        "local_folder_manifest_revocations",
        "local_dataset_source_blobs",
    ]
    .map(|table| {
        connection
            .query_row(&format!("SELECT count(*) FROM {table}"), [], |row| {
                row.get(0)
            })
            .unwrap()
    })
}

struct RejectCommit;

impl MappingCommitGuard for RejectCommit {
    fn commit<T>(&self, _apply: impl FnOnce() -> Result<T, Error>) -> Result<T, Error> {
        Err(failure(ErrorCode::CANCELLED))
    }
}

fn publish(
    store: &mut SqliteWorkspace,
    key: &FolderManifestRequest<'_>,
    review: &FolderManifestPublication<'_>,
) -> Result<String, Error> {
    store.publish_folder_manifest(key, review, &CommitMapping)
}

fn replay(
    store: &SqliteWorkspace,
    key: &FolderManifestRequest<'_>,
    review: &FolderManifestPublication<'_>,
) -> Result<Option<String>, Error> {
    store.replay_folder_manifest(key, review.input_sha256())
}

fn assert_error<T>(result: Result<T, Error>, expected: ErrorCode) {
    assert_eq!(
        result
            .err()
            .expect("the store should refuse this request")
            .code,
        expected
    );
}

fn frozen_legacy(store: &SqliteWorkspace, revision_id: &str) -> Value {
    store
        .read_local_dataset(revision_id)
        .unwrap()
        .unwrap()
        .content
}

#[test]
fn folder_manifest_retry_preserves_alias_locators_and_independent_frozen_revisions() {
    let profile = Profile::new();
    let mut writer = profile.open().unwrap();
    let binding = Profile::binding();
    let (legacy_id, legacy_content) = seed_frozen_csv(&mut writer);
    let (sources, bars) = complete_sources();
    let review = FolderManifestPublication::new(&legacy_id, sources, bars).unwrap();

    let (mut sources, mut bars) = complete_sources();
    sources.reverse();
    bars.reverse();
    for bar in &mut bars {
        bar.locators.reverse();
    }
    let reordered = FolderManifestPublication::new(&legacy_id, sources, bars).unwrap();
    assert_eq!(reordered.input_sha256(), review.input_sha256());

    let key = request(&binding, "stage-reviewed-folder");
    let mut peer = profile.open().unwrap();
    let start = Barrier::new(2);
    let first = thread::scope(|scope| {
        let other = scope.spawn(|| {
            start.wait();
            publish(&mut peer, &key, &reordered).unwrap()
        });
        start.wait();
        let first = publish(&mut writer, &key, &review).unwrap();
        assert_eq!(other.join().unwrap(), first);
        first
    });
    assert_eq!(counts(&writer.connection), [1, 3, 2, 3, 1, 0, 2]);
    drop(peer);
    drop(writer);

    let mut writer = profile.open().unwrap();
    assert_eq!(replay(&writer, &key, &review).unwrap(), Some(first.clone()));
    let snapshot = writer.read_folder_manifest(&first).unwrap().unwrap();
    assert_eq!(snapshot.revision_id, first);
    assert_eq!(snapshot.base_revision_id, legacy_id);
    assert!(snapshot.parent_revision_id.is_none());
    let source_digests: Vec<_> = snapshot
        .sources
        .iter()
        .map(|source| (source.relative_path.as_str(), source.sha256.as_str()))
        .collect();
    assert_eq!(
        source_digests,
        [
            (ALIAS_PATH, FIRST_DIGEST),
            (ADDED_PATH, ADDED_DIGEST),
            (FIRST_PATH, FIRST_DIGEST),
        ]
    );
    assert_eq!(snapshot.bars[0].bar, first_bar());
    assert_eq!(snapshot.bars[1].bar.close, 8.0);
    assert_eq!(
        snapshot.bars[0].locators,
        manifest_bar(first_bar(), &[ALIAS_PATH, FIRST_PATH]).locators
    );
    assert_eq!(
        writer
            .read_folder_manifest_source(&first, ALIAS_PATH)
            .unwrap()
            .as_deref(),
        Some(FIRST_CSV),
    );
    assert_eq!(frozen_legacy(&writer, &legacy_id), legacy_content);

    let wrong_principal = FolderManifestRequest {
        principal_id: "other-profile",
        idempotency_key: key.idempotency_key,
    };
    assert_error(
        replay(&writer, &wrong_principal, &review),
        ErrorCode::PERMISSIONDENIED,
    );
    let other_profile = Profile::new();
    let other = other_profile.open().unwrap();
    assert!(replay(&other, &key, &review).unwrap().is_none());
    assert!(other.read_folder_manifest(&first).unwrap().is_none());

    let duplicate_key = request(&binding, "independent-equivalent-review");
    assert_error(
        writer.publish_folder_manifest(&duplicate_key, &review, &RejectCommit),
        ErrorCode::CANCELLED,
    );
    assert!(replay(&writer, &duplicate_key, &review).unwrap().is_none());
    assert_eq!(
        publish(&mut writer, &duplicate_key, &review).unwrap(),
        first
    );
    assert_eq!(counts(&writer.connection), [1, 3, 2, 3, 2, 0, 2]);

    let withdrawn = FolderManifestPublication::new(&first, Vec::new(), Vec::new()).unwrap();
    assert_error(
        publish(&mut writer, &key, &withdrawn),
        ErrorCode::INVALIDARGUMENT,
    );
    let withdrawal_key = request(&binding, "accept-withdrawal");
    let child = publish(&mut writer, &withdrawal_key, &withdrawn).unwrap();
    assert_ne!(child, first);
    let after = writer.read_folder_manifest(&child).unwrap().unwrap();
    assert_eq!(after.base_revision_id, legacy_id);
    assert_eq!(after.parent_revision_id.as_deref(), Some(first.as_str()));
    assert!(after.sources.is_empty() && after.bars.is_empty());
    assert_eq!(
        writer
            .read_folder_manifest_source(&child, FIRST_PATH)
            .unwrap(),
        None
    );
    assert_eq!(
        writer
            .read_folder_manifest_source(&first, FIRST_PATH)
            .unwrap()
            .as_deref(),
        Some(FIRST_CSV),
    );

    // Replay the original request after the source it was based on was revoked.
    assert!(writer.revoke_local_dataset(&legacy_id).unwrap());
    assert_eq!(
        writer
            .publish_folder_manifest(&key, &reordered, &RejectCommit)
            .unwrap(),
        first,
    );
    let fresh = request(&binding, "new-revoked-base-review");
    assert_error(
        publish(&mut writer, &fresh, &review),
        ErrorCode::PERMISSIONDENIED,
    );

    assert!(writer.revoke_folder_manifest(&first).unwrap());
    assert!(writer.revoke_folder_manifest(&first).unwrap());
    assert_error(replay(&writer, &key, &review), ErrorCode::PERMISSIONDENIED);
    assert_error(
        writer.read_folder_manifest(&first),
        ErrorCode::PERMISSIONDENIED,
    );
    assert_error(
        writer.read_folder_manifest_source(&first, FIRST_PATH),
        ErrorCode::PERMISSIONDENIED,
    );
    assert_error(
        publish(&mut writer, &fresh, &withdrawn),
        ErrorCode::PERMISSIONDENIED,
    );
    assert_eq!(
        replay(&writer, &withdrawal_key, &withdrawn).unwrap(),
        Some(child)
    );
}

struct ExitBeforeCommit;

impl MappingCommitGuard for ExitBeforeCommit {
    fn commit<T>(&self, _apply: impl FnOnce() -> Result<T, Error>) -> Result<T, Error> {
        process::exit(STAGED_EXIT);
    }
}

#[test]
#[ignore = "subprocess fixture for an interrupted folder manifest publication"]
fn interrupted_folder_manifest_child() {
    let root = std::env::var_os(INTERRUPTED_ROOT).unwrap();
    let parent = std::env::var(INTERRUPTED_PARENT).unwrap();
    let mut writer = SqliteWorkspace::open(Path::new(&root), Profile::binding()).unwrap();
    let binding = Profile::binding();
    let (sources, bars) = complete_sources();
    let review = FolderManifestPublication::new(&parent, sources, bars).unwrap();
    let outcome = writer.publish_folder_manifest(
        &request(&binding, "publish-after-rollback"),
        &review,
        &ExitBeforeCommit,
    );
    panic!("writer returned before the staged commit boundary: {outcome:?}");
}

#[test]
fn refused_and_interrupted_manifest_discard_staging_before_retry() {
    let profile = Profile::new();
    let mut writer = profile.open().unwrap();
    let binding = Profile::binding();
    let (legacy_id, legacy_content) = seed_frozen_csv(&mut writer);
    let first = FolderManifestPublication::new(
        &legacy_id,
        vec![first_source(FIRST_PATH)],
        vec![manifest_bar(first_bar(), &[FIRST_PATH])],
    )
    .unwrap();
    let existing = publish(&mut writer, &request(&binding, "publish-seed"), &first).unwrap();
    let before = counts(&writer.connection);
    assert_eq!(before, [1, 1, 1, 1, 1, 0, 1]);
    let (sources, bars) = complete_sources();
    let review = FolderManifestPublication::new(&existing, sources, bars).unwrap();
    let key = request(&binding, "publish-after-rollback");
    assert_error(
        writer.publish_folder_manifest(&key, &review, &RejectCommit),
        ErrorCode::CANCELLED,
    );
    assert_eq!(counts(&writer.connection), before);
    assert!(replay(&writer, &key, &review).unwrap().is_none());
    drop(writer);

    let child = Command::new(std::env::current_exe().unwrap())
        .args([
            "--exact",
            "tests::folder_manifest_tests::interrupted_folder_manifest_child",
            "--ignored",
        ])
        .env(INTERRUPTED_ROOT, &profile.0)
        .env(INTERRUPTED_PARENT, &existing)
        .output()
        .unwrap();
    assert_eq!(
        child.status.code(),
        Some(STAGED_EXIT),
        "{}",
        String::from_utf8_lossy(&child.stdout),
    );

    let mut writer = profile.open().unwrap();
    assert_eq!(counts(&writer.connection), before);
    assert!(replay(&writer, &key, &review).unwrap().is_none());
    let frozen = writer.read_folder_manifest(&existing).unwrap().unwrap();
    assert_eq!(frozen.bars.len(), 1);
    assert_eq!(frozen.bars[0].bar, first_bar());
    assert_eq!(frozen_legacy(&writer, &legacy_id), legacy_content);

    let next = publish(&mut writer, &key, &review).unwrap();
    assert_ne!(next, existing);
    assert_eq!(replay(&writer, &key, &review).unwrap(), Some(next));
    assert_eq!(counts(&writer.connection), [2, 4, 3, 4, 2, 0, 2]);
}
