use std::{future, path::PathBuf};

use scopehub_application::{MarketFuture, MarketStatus, SET_TIMEFRAME, ShutdownFuture, UNDO};
use serde_json::json;
use tokio::sync::oneshot::error::TryRecvError;

use super::*;

#[cfg(any(target_os = "linux", windows))]
struct ImportFixture(PathBuf);

#[cfg(any(target_os = "linux", windows))]
impl ImportFixture {
    fn new() -> Self {
        use std::time::{SystemTime, UNIX_EPOCH};
        let path = std::env::temp_dir().join(format!(
            "scopehub-launcher-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        #[cfg(target_os = "linux")]
        {
            use std::os::unix::fs::DirBuilderExt;
            std::fs::DirBuilder::new()
                .mode(0o700)
                .create(&path)
                .unwrap();
        }
        #[cfg(windows)]
        std::fs::create_dir(&path).unwrap();
        #[cfg(target_os = "linux")]
        let path = path.canonicalize().unwrap();
        Self(path)
    }
}

#[cfg(any(target_os = "linux", windows))]
impl Drop for ImportFixture {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

#[cfg(any(target_os = "linux", windows))]
fn import_request(
    catalog: &scopehub_platform::local_folder::FolderCatalog,
    name: &str,
    mapping: Value,
) -> Value {
    let file = catalog
        .files
        .iter()
        .find(|file| file.relative_path == name)
        .unwrap();
    json!({"version": 1, "folder_id": catalog.folder_id.to_string(), "file_id": file.file_id.to_string(), "mapping": mapping})
}

#[cfg(any(target_os = "linux", windows))]
#[tokio::test]
async fn picker_cancel_and_catalog_limit_preserve_the_previous_selection() {
    use scopehub_platform::local_folder::FileId;
    use std::fs;

    let fixture = ImportFixture::new();
    let first = fixture.0.join("first");
    let deep = fixture.0.join("deep");
    let second = fixture.0.join("second");
    fs::create_dir_all(first.join("subfolder")).unwrap();
    fs::create_dir(&second).unwrap();
    fs::write(first.join("subfolder").join("bars.CSV"), b"Date,Close\n").unwrap();
    fs::write(first.join("notes.txt"), b"fixture note").unwrap();
    let mut nested = deep.clone();
    for index in 0..17 {
        nested.push(format!("level-{index}"));
    }
    fs::create_dir_all(nested).unwrap();

    let state = state();
    let granted = state
        .choose_local_csv_folder(|| async { Some(first) })
        .await
        .unwrap()
        .unwrap();
    let first_id = granted["folder_id"].as_str().unwrap().parse().unwrap();
    assert_eq!(granted["display_name"], "first");
    assert_eq!(granted.as_object().unwrap().len(), 3);
    let files = granted["files"].as_array().unwrap();
    assert_eq!(files.len(), 2);
    assert_eq!(files[0]["relative_path"], "notes.txt");
    assert_eq!(files[0]["kind"], "unsupported_extension");
    assert_eq!(files[1]["relative_path"], "subfolder/bars.CSV");
    assert_eq!(files[1]["display_name"], "bars.CSV");
    assert_eq!(files[1]["kind"], "csv");
    assert_eq!(files[1]["byte_count"], 11);
    assert_eq!(files[1].as_object().unwrap().len(), 5);
    let _: FileId = files[1]["file_id"].as_str().unwrap().parse().unwrap();

    assert!(
        state
            .choose_local_csv_folder(|| async { None })
            .await
            .unwrap()
            .is_none()
    );
    let error = state
        .choose_local_csv_folder(|| async { Some(deep) })
        .await
        .unwrap_err();
    assert_eq!(error.code, ErrorCode::RESOURCELIMIT);
    state.local_folders().check_current(first_id).unwrap();

    let replacement = state
        .choose_local_csv_folder(|| async { Some(second) })
        .await
        .unwrap()
        .unwrap();
    assert_ne!(replacement["folder_id"], granted["folder_id"]);
    assert!(state.local_folders().check_current(first_id).is_err());
}

#[cfg(any(target_os = "linux", windows))]
#[tokio::test]
async fn picker_admits_one_dialog_and_does_not_grant_after_shutdown() {
    let fixture = ImportFixture::new();
    let state = state();
    let (opened, entered) = oneshot::channel();
    let (confirm, result) = oneshot::channel();

    let choose = state.choose_local_csv_folder(|| async {
        opened.send(()).unwrap();
        result.await.unwrap();
        Some(fixture.0.clone())
    });
    let close = async {
        entered.await.unwrap();
        let error = state
            .choose_local_csv_folder(|| async { panic!("second picker opened") })
            .await
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::BUSY);
        state.shutdown().await;
        confirm.send(()).unwrap();
    };
    let (chosen, ()) = tokio::join!(choose, close);
    assert_eq!(chosen.unwrap_err().code, ErrorCode::CANCELLED);

    let error = state
        .choose_local_csv_folder(|| async { panic!("picker opened after shutdown") })
        .await
        .unwrap_err();
    assert_eq!(error.code, ErrorCode::CANCELLED);
}

#[cfg(any(target_os = "linux", windows))]
#[tokio::test]
async fn shutdown_releases_the_selection_and_closes_native_folder_access() {
    use scopehub_platform::local_folder::{Cancellation, FolderError};
    use std::fs;

    let fixture = ImportFixture::new();
    let selected = fixture.0.join("selected");
    let replacement = fixture.0.join("replacement");
    fs::create_dir(&selected).unwrap();
    fs::create_dir(&replacement).unwrap();
    let source = b"contract,quote\nTEST,7\n";
    fs::write(selected.join("source.csv"), source).unwrap();
    let state = state();
    let folders = state.local_folders();
    let cancel = Cancellation::default();
    let before = folders.adopt_selected(&selected, &cancel).unwrap();
    let file = before.files[0].file_id;
    let frozen = folders.read(before.folder_id, file, None, &cancel).unwrap();

    state.shutdown().await;

    assert_eq!(
        folders.check_current(before.folder_id),
        Err(FolderError::SelectionUnavailable)
    );
    assert!(matches!(
        folders.read(before.folder_id, file, None, &cancel),
        Err(FolderError::SelectionUnavailable)
    ));
    assert!(matches!(
        folders.adopt_selected(&replacement, &cancel),
        Err(FolderError::SelectionUnavailable)
    ));
    // On Windows this also checks that the old directory pin no longer blocks rename.
    fs::rename(selected, fixture.0.join("released")).unwrap();
    assert_eq!(frozen.content.as_deref(), Some(source.as_slice()));
}

#[cfg(any(target_os = "linux", windows))]
#[tokio::test]
async fn native_registry_returns_source_bound_raw_and_normalized_previews() {
    use std::fs;

    let fixture = ImportFixture::new();
    let stock = b"Ticker,Date,Open,High,Low,Close,Shares\n000418,2026-11-19,4,6,3,5,20\n000418,2026-11-20,6,7,4,5,\n";
    let future = b"Contract,LocalStart,TradingDay,Open,High,Low,Close,Contracts,PrevSettlementPrice,is_closed\nTEST2603,2024-07-19 21:05,2024-07-22,100,105,96,102,0,99,true\nTEST2603,2024-07-19 21:10,2024-07-22,83,85,82,settlement,6,84,true\nTEST2603,2024-07-19 21:15,2024-07-22,103,106,98,105,3,102,false\n";
    let stock_path = fixture.0.join("equity.csv");
    fs::write(&stock_path, stock).unwrap();
    fs::write(fixture.0.join("future.csv"), future).unwrap();
    fs::File::create(fixture.0.join("large.csv"))
        .unwrap()
        .set_len(4_194_305)
        .unwrap();

    let state = state();
    let folders = state.local_folders();
    let granted = state
        .choose_local_csv_folder(|| async { Some(fixture.0.clone()) })
        .await
        .unwrap()
        .unwrap();
    let selected = folders
        .catalog(granted["folder_id"].as_str().unwrap().parse().unwrap())
        .unwrap();
    let mut input = import_request(&selected, "equity.csv", equity_mapping());
    input["expected_sha256"] =
        json!("92eb8b04506ac61dd43097b78e5ff15d4aa89b4c525a014b8816e3fa7b21ee1e");
    input["row_limit"] = json!(1);
    let stock_preview = state
        .execute_local_operation(PREVIEW_LOCAL_IMPORT, input.clone(), None)
        .await
        .unwrap();
    assert_eq!(stock_preview["sha256"], input["expected_sha256"]);
    assert_eq!(stock_preview["byte_count"], stock.len());
    assert_eq!(stock_preview["relative_path"], "equity.csv");
    assert_eq!(stock_preview["preview"]["truncated"], true);
    let row = &stock_preview["preview"]["rows"][0];
    assert_eq!(row["symbol"], "000418");
    assert_eq!(row["raw_fields"][0], "000418");
    assert_eq!(row["line_start"], 2);
    assert_eq!(row["normalized"]["volume"], 20.0);

    let mut generic = MarketRegistry::new(worker());
    for actor in [Actor::User, Actor::Ai, Actor::System] {
        let error = generic
            .execute(actor, PREVIEW_LOCAL_IMPORT, input.clone())
            .await
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::PERMISSIONDENIED);
    }
    let mut spoof = input.clone();
    spoof["path"] = json!("equity.csv");
    assert_eq!(
        state
            .execute_local_operation(PREVIEW_LOCAL_IMPORT, spoof, None)
            .await
            .unwrap_err()
            .code,
        ErrorCode::INVALIDARGUMENT
    );

    let import_future = import_request(
        &selected,
        "future.csv",
        json!({
            "version":1,"revision":"future-fixture-1","encoding":"utf-8","delimiter":",","record_kind":"native_ohlcv",
            "columns":{"symbol":"Contract","timestamp":"LocalStart","trading_day":"TradingDay","open":"Open","high":"High","low":"Low","close":"Close","volume":"Contracts"},
            "instrument_kind":"dated_future","venue":"TEST","timezone":"Asia/Shanghai","calendar_id":"fixture-calendar-2","session_scope":"night",
            "timeframe":"5m","quantity_unit":"contracts","price_adjustment":"raw","volume_semantics":"per_bar",
            "timestamp_convention":"bar_start","timestamp_format":"%Y-%m-%d %H:%M","trading_day_format":"%Y-%m-%d"
        }),
    );
    let futures_preview = state
        .execute_local_operation(PREVIEW_LOCAL_IMPORT, import_future.clone(), None)
        .await
        .unwrap();
    assert_eq!(
        futures_preview["preview"]["file_sha256"],
        "19aebe2d5ed0d9fe68bf7414a6fd1a0a60a2154d5979291c37d7f7f546178074"
    );
    let rows = &futures_preview["preview"]["rows"];
    assert_eq!(rows[0]["normalized"]["time_ms"], 1_721_394_300_000_i64);
    assert_eq!(rows[0]["normalized"]["trading_day"], "2024-07-22");
    assert_eq!(rows[0]["normalized"]["volume"], 0.0);
    assert_eq!(rows[0]["normalized"]["close"], 102.0);
    assert_eq!(rows[0]["normalized"]["is_closed"], true);
    assert_eq!(rows[1]["raw_fields"][6], "settlement");
    assert_eq!(rows[1]["issues"][0]["field"], "close");
    assert!(rows[1]["normalized"].is_null());

    let mut mislabelled = import_future;
    mislabelled["mapping"]["columns"]["close"] = json!("PrevSettlementPrice");
    let raw_preview = state
        .execute_local_operation(PREVIEW_LOCAL_IMPORT, mislabelled, None)
        .await
        .unwrap();
    let issue = &raw_preview["preview"]["issues"][0];
    assert_eq!(issue["code"], "auxiliary_column");
    assert_eq!(issue["field"], "close");
    let raw = &raw_preview["preview"]["rows"][0];
    assert!(raw["normalized"].is_null());
    assert_eq!(raw["raw_fields"][6], "102");
    assert_eq!(raw["raw_fields"][8], "99");
    assert_eq!(rows[2]["raw_fields"][9], "false");
    assert_eq!(rows[2]["issues"][0]["code"], "bar_incomplete");
    assert!(rows[2]["normalized"].is_null());

    let too_large = import_request(&selected, "large.csv", input["mapping"].clone());
    let limit = state
        .execute_local_operation(PREVIEW_LOCAL_IMPORT, too_large, None)
        .await
        .unwrap_err();
    assert_eq!(limit.code, ErrorCode::RESOURCELIMIT);
    assert_eq!(limit.details["byte_count"], 4_194_305);
    fs::write(stock_path, b"changed source").unwrap();
    assert_eq!(
        state
            .execute_local_operation(PREVIEW_LOCAL_IMPORT, input.clone(), None)
            .await
            .unwrap_err()
            .code,
        ErrorCode::SNAPSHOTUNAVAILABLE
    );
    folders.revoke(selected.folder_id).unwrap();
    assert_eq!(
        state
            .execute_local_operation(PREVIEW_LOCAL_IMPORT, input, None)
            .await
            .unwrap_err()
            .code,
        ErrorCode::STALECONTEXT
    );
    assert_eq!(row["normalized"]["close"], 5.0);
}

#[cfg(any(target_os = "linux", windows))]
fn equity_mapping() -> Value {
    json!({
        "version":1,"revision":"equity-fixture-2","encoding":"utf-8","delimiter":",","record_kind":"native_ohlcv",
        "columns":{"symbol":"Ticker","timestamp":"Date","trading_day":"Date","open":"Open","high":"High","low":"Low","close":"Close","volume":"Shares"},
        "instrument_kind":"equity","venue":"TEST","timezone":"UTC","calendar_id":"fixture-calendar-1","session_scope":"regular",
        "timeframe":"1d","quantity_unit":"shares","price_adjustment":"raw","volume_semantics":"per_bar",
        "timestamp_convention":"bar_start","timestamp_format":"%Y-%m-%d","trading_day_format":"%Y-%m-%d","daily_start_time":"09:30:00"
    })
}

#[cfg(any(target_os = "linux", windows))]
async fn select_csv(state: &State, folder: &Path) -> Value {
    let catalog = state
        .choose_local_csv_folder(|| async { Some(folder.to_owned()) })
        .await
        .unwrap()
        .unwrap();
    assert_eq!(catalog["files"][0]["kind"], "csv");
    json!({
        "version": 1,
        "folder_id": catalog["folder_id"],
        "file_id": catalog["files"][0]["file_id"],
    })
}

#[cfg(any(target_os = "linux", windows))]
async fn execute_import(state: &State, operation: &str, payload: Value) -> Result<Value, Error> {
    state.execute("main", Some(operation), payload, None).await
}

#[cfg(any(target_os = "linux", windows))]
async fn reviewed_save_input(state: &State, ids: &Value, mapping: Value) -> Value {
    let mut input = ids.clone();
    input["mapping"] = mapping;
    input["row_limit"] = json!(1);
    let shown = execute_import(state, PREVIEW_LOCAL_IMPORT, input.clone())
        .await
        .unwrap();
    input["expected_sha256"] = shown["sha256"].clone();
    input
}

#[cfg(any(target_os = "linux", windows))]
fn publication_input(save: &Value, receipt: &Value) -> Value {
    json!({
        "version": 1,
        "folder_id": save["folder_id"],
        "file_id": save["file_id"],
        "expected_sha256": save["expected_sha256"],
        "review_revision": receipt["record"]["review_revision"],
    })
}

#[cfg(any(target_os = "linux", windows))]
#[tokio::test]
async fn folder_refresh_stages_sources_conflicts_and_withdrawals_without_writing() {
    use scopehub_application::{PREVIEW_LOCAL_FOLDER_REFRESH, REVOKE_LOCAL_IMPORT_REVISION};
    use std::fs;

    let fixture = ImportFixture::new();
    let profile = fixture.0.join("profile");
    let folder = fixture.0.join("source");
    let different_folder = fixture.0.join("different");
    fs::create_dir(&folder).unwrap();
    fs::create_dir(&different_folder).unwrap();
    let header = "Ticker,Date,Open,High,Low,Close,Shares\n";
    let first = "000418,2026-11-19,4,7,3,5,20\n";
    let second = "000418,2026-11-20,4,8,3,6,21\n";
    let accepted = format!("{header}{first}{first}{second}");
    let path = folder.join("source.csv");
    fs::write(&path, &accepted).unwrap();
    fs::write(different_folder.join("source.csv"), &accepted).unwrap();
    let state = state_with_local_profile(Some(&profile));
    let ids = select_csv(&state, &folder).await;
    let save = reviewed_save_input(&state, &ids, equity_mapping()).await;
    let saved = execute_import(&state, SAVE_LOCAL_IMPORT_MAPPING, save.clone())
        .await
        .unwrap();
    let published = execute_import(
        &state,
        PUBLISH_LOCAL_IMPORT,
        publication_input(&save, &saved),
    )
    .await
    .unwrap();
    let revision_id = &published["revision"]["revision_id"];
    let read = json!({"version": 1, "revision_id": revision_id});
    let prior = execute_import(&state, READ_LOCAL_IMPORT_REVISION, read.clone())
        .await
        .unwrap();
    let request = json!({"version": 1, "folder_id": ids["folder_id"], "revision_id": revision_id});
    let extras = [
        ("z-alias.csv", accepted.clone()),
        (
            "a-added.csv",
            format!("{header}{first}000418,2026-11-21,4,8,3,7,22\n"),
        ),
        (
            "correction.csv",
            format!("{header}000418,2026-11-19,4,7,3,6,20\n"),
        ),
        ("incompatible.csv", accepted.replace("Close", "Last")),
        ("other-symbol.csv", accepted.replace("000418", "000419")),
    ];
    for (name, bytes) in &extras {
        fs::write(folder.join(name), bytes).unwrap();
    }
    let staged = execute_import(&state, PREVIEW_LOCAL_FOLDER_REFRESH, request.clone())
        .await
        .unwrap();
    assert_eq!(staged["base"], published["revision"]);
    assert_eq!(staged["files"][0]["relative_path"], "a-added.csv");
    assert_eq!(staged["files"][0]["change"], "added");
    assert_eq!(staged["files"][2]["review"], "header_changed");
    assert_eq!(staged["files"][3]["review"], "series_mismatch");
    assert_eq!(staged["files"][5]["alias_of"], "source.csv");
    let bar = &staged["bars"][0];
    assert_eq!(bar["status"], "conflict");
    assert_eq!(bar["accepted"]["bar"]["close"], 5.0);
    assert_eq!(bar["alternatives"][0]["bar"]["close"], 5.0);
    assert_eq!(bar["alternatives"][1]["bar"]["close"], 6.0);
    let sources = bar["alternatives"][0]["sources"].as_array().unwrap();
    let locators: Vec<_> = sources
        .iter()
        .map(|source| {
            (
                source["relative_path"].as_str().unwrap(),
                source["record_number"].as_u64().unwrap(),
                source["line_start"].as_u64().unwrap(),
                source["line_end"].as_u64().unwrap(),
            )
        })
        .collect();
    assert_eq!(
        locators,
        [
            ("a-added.csv", 1, 2, 2),
            ("source.csv", 1, 2, 2),
            ("source.csv", 2, 3, 3),
            ("z-alias.csv", 1, 2, 2),
            ("z-alias.csv", 2, 3, 3),
        ]
    );
    assert_eq!(sources[1]["source_sha256"], sources[3]["source_sha256"]);
    assert_eq!(staged["bars"][1]["status"], "unchanged");
    assert_eq!(staged["bars"][2]["status"], "addition");

    for (name, _) in &extras {
        fs::remove_file(folder.join(name)).unwrap();
    }
    for (name, bytes) in extras.iter().rev() {
        fs::write(folder.join(name), bytes).unwrap();
    }
    assert_eq!(
        execute_import(&state, PREVIEW_LOCAL_FOLDER_REFRESH, request.clone())
            .await
            .unwrap(),
        staged
    );

    fs::write(&path, accepted.replace(",6,21", ",7,21")).unwrap();
    let replaced = execute_import(&state, PREVIEW_LOCAL_FOLDER_REFRESH, request.clone())
        .await
        .unwrap();
    assert_eq!(replaced["files"][4]["change"], "replaced");
    assert_eq!(replaced["bars"][1]["status"], "conflict");
    assert_eq!(replaced["bars"][1]["accepted"]["bar"]["close"], 6.0);
    fs::remove_file(&path).unwrap();
    let aliased = execute_import(&state, PREVIEW_LOCAL_FOLDER_REFRESH, request.clone())
        .await
        .unwrap();
    assert_eq!(aliased["files"][4]["change"], "removed");
    assert_eq!(aliased["bars"][1]["status"], "unchanged");
    fs::remove_file(folder.join("z-alias.csv")).unwrap();
    let held = execute_import(&state, PREVIEW_LOCAL_FOLDER_REFRESH, request.clone())
        .await
        .unwrap();
    assert_eq!(held["bars"][1]["status"], "unverified");
    assert_eq!(held["bars"][1]["accepted"]["bar"]["close"], 6.0);
    fs::remove_file(folder.join("incompatible.csv")).unwrap();
    let removed = execute_import(&state, PREVIEW_LOCAL_FOLDER_REFRESH, request.clone())
        .await
        .unwrap();
    assert_eq!(removed["files"][3]["change"], "removed");
    assert_eq!(removed["bars"][1]["status"], "withdrawal");
    assert_eq!(removed["bars"][1]["accepted"]["bar"]["close"], 6.0);

    let (cancelled, ()) = tokio::join!(
        state.execute_local_operation(
            PREVIEW_LOCAL_FOLDER_REFRESH,
            request.clone(),
            Some("cancel-folder-review")
        ),
        async {
            tokio::task::yield_now().await;
            state.cancel_local_import("cancel-folder-review");
        },
    );
    assert_eq!(cancelled.unwrap_err().code, ErrorCode::CANCELLED);
    assert_eq!(
        execute_import(&state, READ_LOCAL_IMPORT_REVISION, read.clone())
            .await
            .unwrap(),
        prior
    );
    state.shutdown().await;

    // Pause the real Python checker after it receives and normalizes the
    // complete file, then cancel or change the CSV manifest before it replies.
    let config = development_config().unwrap();
    let worker_root = fixture.0.join("held-worker");
    let module = worker_root.join("python/worker_runtime");
    fs::create_dir_all(&module).unwrap();
    fs::write(
        worker_root.join("repo-root"),
        config.root.canonicalize().unwrap().to_str().unwrap(),
    )
    .unwrap();
    fs::write(
        module.join("local_import.py"),
        r#"
import pathlib, sys, time
root = pathlib.Path('repo-root').read_text(encoding='utf-8')
sys.modules.pop('worker_runtime', None)
sys.path[:0] = [root, str(pathlib.Path(root) / 'python')]
from worker_runtime import local_import

check = local_import.check_complete_csv
def hold(*args):
    result = check(*args)
    pathlib.Path('checked').touch()
    while not pathlib.Path('release').exists():
        time.sleep(0.01)
    return result

local_import.check_complete_csv = hold
raise SystemExit(local_import.run(sys.stdin.buffer, sys.stdout.buffer))
"#,
    )
    .unwrap();
    let state = State {
        local_import: LocalImport::new(Some(WorkerConfig {
            root: worker_root.clone(),
            ..config
        })),
        ..state_with_local_profile(Some(&profile))
    };
    let current = select_csv(&state, &folder).await;
    let live_request =
        json!({"version": 1, "folder_id": current["folder_id"], "revision_id": revision_id});
    let ready = worker_root.join("checked");
    for cancel in [true, false] {
        let id = if cancel {
            "cancel-after-check"
        } else {
            "change-after-check"
        };
        let preview = state.execute_local_operation(
            PREVIEW_LOCAL_FOLDER_REFRESH,
            live_request.clone(),
            Some(id),
        );
        tokio::pin!(preview);
        tokio::select! {
            result = &mut preview => panic!("refresh ended before the complete file check: {result:?}"),
            result = tokio::time::timeout(Duration::from_secs(4), async {
                while !ready.exists() { tokio::time::sleep(Duration::from_millis(10)).await; }
            }) => result.unwrap(),
        }
        if cancel {
            state.cancel_local_import(id);
        } else {
            fs::write(folder.join("appeared-during-check.csv"), &accepted).unwrap();
            fs::write(worker_root.join("release"), "").unwrap();
        }
        let error = preview.await.unwrap_err();
        assert_eq!(
            error.code,
            if cancel {
                ErrorCode::CANCELLED
            } else {
                ErrorCode::SNAPSHOTUNAVAILABLE
            }
        );
        if !cancel {
            assert_eq!(error.details["reason"], "folder_changed_during_review");
        }
        fs::remove_file(&ready).unwrap();
        assert_eq!(
            execute_import(&state, READ_LOCAL_IMPORT_REVISION, read.clone())
                .await
                .unwrap(),
            prior
        );
    }

    let wrong = select_csv(&state, &different_folder).await;
    assert_eq!(
        execute_import(&state, PREVIEW_LOCAL_FOLDER_REFRESH, request)
            .await
            .unwrap_err()
            .code,
        ErrorCode::STALECONTEXT,
    );
    let wrong_request =
        json!({"version": 1, "folder_id": wrong["folder_id"], "revision_id": revision_id});
    assert_eq!(
        execute_import(&state, PREVIEW_LOCAL_FOLDER_REFRESH, wrong_request)
            .await
            .unwrap_err()
            .code,
        ErrorCode::PERMISSIONDENIED,
    );
    let current = select_csv(&state, &folder).await;
    execute_import(&state, REVOKE_LOCAL_IMPORT_REVISION, read)
        .await
        .unwrap();
    let revoked_request =
        json!({"version": 1, "folder_id": current["folder_id"], "revision_id": revision_id});
    assert_eq!(
        execute_import(&state, PREVIEW_LOCAL_FOLDER_REFRESH, revoked_request)
            .await
            .unwrap_err()
            .code,
        ErrorCode::PERMISSIONDENIED,
    );
    state.shutdown().await;
}

#[cfg(any(target_os = "linux", windows))]
#[tokio::test]
async fn local_publication_replays_the_minimal_save_publish_chain() {
    use scopehub_application::REVOKE_LOCAL_IMPORT_REVISION;
    use std::fs;

    let fixture = ImportFixture::new();
    let profile = fixture.0.join("profile");
    let folder = fixture.0.join("source");
    fs::create_dir(&folder).unwrap();
    let csv = folder.join("source.csv");
    let original = b"Ticker,Date,Open,High,Low,Close,Shares\n000418,2026-11-19,4,7,3,5,20\n";
    fs::write(&csv, original).unwrap();
    let state = state_with_local_profile(Some(&profile));
    let ids = select_csv(&state, &folder).await;
    let save = reviewed_save_input(&state, &ids, equity_mapping()).await;
    let request = |operation, body: &Value, key| {
        state.execute_local_operation(operation, body.clone(), Some(key))
    };
    let (first, concurrent) = tokio::join!(
        request(SAVE_LOCAL_IMPORT_MAPPING, &save, "save-a"),
        request(SAVE_LOCAL_IMPORT_MAPPING, &save, "save-a"),
    );
    let saved = first.unwrap();
    assert_eq!(saved["record"]["review_revision"], 1);
    match concurrent {
        Ok(receipt) => assert_eq!(receipt, saved),
        Err(error) => assert_eq!(error.code, ErrorCode::BUSY),
    }

    let publish = publication_input(&save, &saved);
    let (first, concurrent) = tokio::join!(
        request(PUBLISH_LOCAL_IMPORT, &publish, "publish-p"),
        request(PUBLISH_LOCAL_IMPORT, &publish, "publish-p"),
    );
    let published = first.unwrap();
    match concurrent {
        Ok(receipt) => assert_eq!(receipt, published),
        Err(error) => assert_eq!(error.code, ErrorCode::BUSY),
    }
    assert_eq!(published["revision"]["bar_count"], 1);
    assert_eq!(
        request(PUBLISH_LOCAL_IMPORT, &publish, "publish-p")
            .await
            .unwrap(),
        published
    );
    assert_eq!(
        request(SAVE_LOCAL_IMPORT_MAPPING, &save, "save-a")
            .await
            .unwrap(),
        saved
    );
    assert_eq!(
        request(SAVE_LOCAL_IMPORT_MAPPING, &save, "equivalent-new-save")
            .await
            .unwrap(),
        saved
    );
    assert_eq!(
        request(PUBLISH_LOCAL_IMPORT, &publish, "publish-p")
            .await
            .unwrap(),
        published
    );
    assert_eq!(
        request(PUBLISH_LOCAL_IMPORT, &publish, "equivalent-new-publish")
            .await
            .unwrap(),
        published
    );
    let read = json!({"version": 1, "revision_id": published["revision"]["revision_id"]});
    let frozen = execute_import(&state, READ_LOCAL_IMPORT_REVISION, read.clone())
        .await
        .unwrap();
    assert_eq!(frozen["bars"][0]["bar"]["close"], 5.0);
    assert_eq!(
        frozen["bars"][0]["locators"],
        json!([{"record_number":1,"line_start":2,"line_end":2}])
    );

    // A reused write key may never authorize a different request.
    let mut changed_sample = save.clone();
    changed_sample["row_limit"] = json!(2);
    assert_eq!(
        request(SAVE_LOCAL_IMPORT_MAPPING, &changed_sample, "save-a")
            .await
            .unwrap_err()
            .code,
        ErrorCode::INVALIDARGUMENT
    );
    assert_eq!(
        request(PUBLISH_LOCAL_IMPORT, &publish, "save-a")
            .await
            .unwrap_err()
            .code,
        ErrorCode::INVALIDARGUMENT
    );
    let mut invalid_publish = publish.clone();
    invalid_publish["revision_id"] = read["revision_id"].clone();
    assert_eq!(
        request(PUBLISH_LOCAL_IMPORT, &invalid_publish, "publish-p")
            .await
            .unwrap_err()
            .code,
        ErrorCode::INVALIDARGUMENT
    );
    let mut changed_digest = publish.clone();
    changed_digest["expected_sha256"] = json!("0".repeat(64));
    assert_eq!(
        request(PUBLISH_LOCAL_IMPORT, &changed_digest, "publish-p")
            .await
            .unwrap_err()
            .code,
        ErrorCode::INVALIDARGUMENT
    );
    assert_eq!(
        request(REVOKE_LOCAL_IMPORT_REVISION, &read, "publish-p")
            .await
            .unwrap_err()
            .code,
        ErrorCode::INVALIDARGUMENT
    );
    let list = json!({"version":1,"after_revision_id":null,"limit":8});
    let folder_refresh = json!({
        "version": 1, "folder_id": ids["folder_id"], "revision_id": read["revision_id"],
    });
    for write_id in ["save-a", "publish-p"] {
        assert_eq!(
            request(LIST_LOCAL_IMPORT_REVISIONS, &list, write_id)
                .await
                .unwrap_err()
                .code,
            ErrorCode::INVALIDARGUMENT
        );
        assert_eq!(
            request(PREVIEW_LOCAL_FOLDER_REFRESH, &folder_refresh, write_id)
                .await
                .unwrap_err()
                .code,
            ErrorCode::INVALIDARGUMENT
        );
    }
    let listed = request(LIST_LOCAL_IMPORT_REVISIONS, &list, "list-before-revocation")
        .await
        .unwrap();
    let entries = listed["revisions"].as_array().unwrap();
    assert_eq!(entries.len(), 1);
    assert_eq!(entries[0]["revision_id"], read["revision_id"]);
    state.cancel_local_import("publish-p");
    assert_eq!(
        request(PUBLISH_LOCAL_IMPORT, &publish, "publish-p")
            .await
            .unwrap(),
        published
    );
    assert_eq!(
        execute_import(&state, REVOKE_LOCAL_IMPORT_REVISION, read.clone())
            .await
            .unwrap(),
        read
    );
    fs::rename(&csv, folder.join("unavailable")).unwrap();
    assert_eq!(
        request(PUBLISH_LOCAL_IMPORT, &publish, "publish-p")
            .await
            .unwrap_err()
            .code,
        ErrorCode::PERMISSIONDENIED,
        "a committed replay checks current display rights without rereading the source"
    );
    assert_eq!(
        request(LIST_LOCAL_IMPORT_REVISIONS, &list, "list-after-revocation")
            .await
            .unwrap(),
        json!({"version":1,"revisions":[],"next_after_revision_id":null})
    );
    fs::write(&csv, original).unwrap();
    assert_eq!(
        request(
            SAVE_LOCAL_IMPORT_MAPPING,
            &save,
            "new-save-after-revocation"
        )
        .await
        .unwrap(),
        saved
    );
    assert_eq!(
        request(PUBLISH_LOCAL_IMPORT, &publish, "new-id-after-revocation")
            .await
            .unwrap_err()
            .code,
        ErrorCode::PERMISSIONDENIED
    );
    state.shutdown().await;
    let restarted = state_with_local_profile(Some(&profile));
    assert_eq!(
        execute_import(&restarted, READ_LOCAL_IMPORT_REVISION, read)
            .await
            .unwrap_err()
            .code,
        ErrorCode::PERMISSIONDENIED
    );
    restarted.shutdown().await;
}

#[cfg(any(target_os = "linux", windows))]
#[tokio::test]
async fn local_publication_replay_preserves_real_mapping_transitions_and_current_grants() {
    use std::fs;

    let fixture = ImportFixture::new();
    let profile = fixture.0.join("profile");
    let folder = fixture.0.join("source");
    fs::create_dir(&folder).unwrap();
    let csv = folder.join("source.csv");
    let original =
        "Ticker,Date,Open,High,Low,Close,AltClose,Shares\n000418,2026-11-19,4,7,3,5,6,20\n";
    fs::write(&csv, original).unwrap();
    let state = state_with_local_profile(Some(&profile));
    let ids = select_csv(&state, &folder).await;
    let a = reviewed_save_input(&state, &ids, equity_mapping()).await;
    let request = |operation, body: &Value, key| {
        state.execute_local_operation(operation, body.clone(), Some(key))
    };
    let saved_a = request(SAVE_LOCAL_IMPORT_MAPPING, &a, "original-a")
        .await
        .unwrap();
    let publish_a = publication_input(&a, &saved_a);
    let frozen_a = request(PUBLISH_LOCAL_IMPORT, &publish_a, "publish-a")
        .await
        .unwrap();
    let read_a = json!({"version": 1, "revision_id": frozen_a["revision"]["revision_id"]});
    let first_read = execute_import(&state, READ_LOCAL_IMPORT_REVISION, read_a.clone())
        .await
        .unwrap();

    let mut mapping_b = equity_mapping();
    mapping_b["columns"]["close"] = json!("AltClose");
    let b = reviewed_save_input(&state, &ids, mapping_b).await;
    let saved_b = request(SAVE_LOCAL_IMPORT_MAPPING, &b, "review-b")
        .await
        .unwrap();
    assert_eq!(saved_b["record"]["review_revision"], 2);
    assert_eq!(
        request(SAVE_LOCAL_IMPORT_MAPPING, &b, "original-a")
            .await
            .unwrap_err()
            .code,
        ErrorCode::INVALIDARGUMENT
    );
    let unavailable = folder.join("unavailable");
    fs::rename(&csv, &unavailable).unwrap();
    assert_eq!(
        request(SAVE_LOCAL_IMPORT_MAPPING, &a, "original-a")
            .await
            .unwrap(),
        saved_a
    );
    assert_eq!(
        request(PUBLISH_LOCAL_IMPORT, &publish_a, "publish-a")
            .await
            .unwrap(),
        frozen_a
    );
    assert_eq!(
        request(PUBLISH_LOCAL_IMPORT, &publish_a, "fresh-stale-mapping")
            .await
            .unwrap_err()
            .code,
        ErrorCode::STALECONTEXT
    );
    assert_eq!(
        request(
            PUBLISH_LOCAL_IMPORT,
            &publication_input(&b, &saved_b),
            "fresh-missing-source"
        )
        .await
        .unwrap_err()
        .code,
        ErrorCode::SNAPSHOTUNAVAILABLE
    );
    fs::rename(&unavailable, &csv).unwrap();
    let loaded = execute_import(&state, LOAD_LOCAL_IMPORT_MAPPING, ids.clone())
        .await
        .unwrap();
    assert_eq!(loaded["record"], saved_b["record"]);

    assert_eq!(reviewed_save_input(&state, &ids, equity_mapping()).await, a);
    state.cancel_local_import("return-to-a");
    assert_eq!(
        request(SAVE_LOCAL_IMPORT_MAPPING, &a, "return-to-a")
            .await
            .unwrap_err()
            .code,
        ErrorCode::CANCELLED
    );
    let loaded = execute_import(&state, LOAD_LOCAL_IMPORT_MAPPING, ids.clone())
        .await
        .unwrap();
    assert_eq!(loaded["record"], saved_b["record"]);
    let returned = request(SAVE_LOCAL_IMPORT_MAPPING, &a, "return-to-a")
        .await
        .unwrap();
    assert_eq!(returned["record"]["review_revision"], 3);
    assert_eq!(returned["record"]["mapping"], saved_a["record"]["mapping"]);
    assert_eq!(
        request(SAVE_LOCAL_IMPORT_MAPPING, &a, "original-a")
            .await
            .unwrap(),
        saved_a
    );
    let publish_returned = publication_input(&a, &returned);
    fs::write(&csv, original.replace(",5,6,", ",7,6,")).unwrap();
    assert_eq!(
        request(PUBLISH_LOCAL_IMPORT, &publish_returned, "fresh-stale-bytes")
            .await
            .unwrap_err()
            .code,
        ErrorCode::SNAPSHOTUNAVAILABLE
    );
    let edited = reviewed_save_input(&state, &ids, equity_mapping()).await;
    assert_eq!(
        request(SAVE_LOCAL_IMPORT_MAPPING, &edited, "save-edited-source")
            .await
            .unwrap(),
        returned,
        "a fresh review of changed bytes with the same header and mapping keeps the current mapping"
    );
    let edited_publish = publication_input(&edited, &returned);
    let updated = request(PUBLISH_LOCAL_IMPORT, &edited_publish, "publish-edited")
        .await
        .unwrap();
    assert_ne!(
        updated["revision"]["revision_id"],
        frozen_a["revision"]["revision_id"]
    );
    assert_ne!(
        updated["revision"]["source_sha256"],
        frozen_a["revision"]["source_sha256"]
    );
    assert_eq!(
        updated["revision"]["mapping_sha256"],
        frozen_a["revision"]["mapping_sha256"]
    );

    let new_ids = select_csv(&state, &folder).await;
    assert_eq!(
        request(PUBLISH_LOCAL_IMPORT, &publish_a, "publish-a")
            .await
            .unwrap_err()
            .code,
        ErrorCode::STALECONTEXT
    );
    let mut changed_grant = edited_publish;
    changed_grant["folder_id"] = new_ids["folder_id"].clone();
    changed_grant["file_id"] = new_ids["file_id"].clone();
    assert_eq!(
        request(PUBLISH_LOCAL_IMPORT, &changed_grant, "publish-edited")
            .await
            .unwrap_err()
            .code,
        ErrorCode::INVALIDARGUMENT
    );
    state.shutdown().await;
    let restarted = state_with_local_profile(Some(&profile));
    assert_eq!(
        execute_import(&restarted, READ_LOCAL_IMPORT_REVISION, read_a)
            .await
            .unwrap(),
        first_read
    );
    restarted.shutdown().await;
}

#[cfg(any(target_os = "linux", windows))]
#[tokio::test]
async fn published_revision_listing_requires_the_local_window_and_profile() {
    let first_page = json!({"version":1,"after_revision_id":null,"limit":8});
    let unconfigured = state();
    assert_eq!(
        execute_import(
            &unconfigured,
            LIST_LOCAL_IMPORT_REVISIONS,
            first_page.clone()
        )
        .await
        .unwrap_err()
        .code,
        ErrorCode::PROVIDERNOTCONFIGURED
    );

    let fixture = ImportFixture::new();
    let profile = fixture.0.join("profile");
    let configured = state_with_local_profile(Some(&profile));
    assert_eq!(
        configured
            .execute(
                "untrusted",
                Some(LIST_LOCAL_IMPORT_REVISIONS),
                first_page.clone(),
                None
            )
            .await
            .unwrap_err()
            .code,
        ErrorCode::PERMISSIONDENIED
    );
    let mut market = MarketRegistry::new(worker());
    for actor in [Actor::User, Actor::Ai, Actor::System] {
        assert_eq!(
            market
                .execute(actor, LIST_LOCAL_IMPORT_REVISIONS, first_page.clone())
                .await
                .unwrap_err()
                .code,
            ErrorCode::PERMISSIONDENIED
        );
    }
    for payload in [
        json!({"version":1,"after_revision_id":null,"limit":0}),
        json!({"version":1,"after_revision_id":null,"limit":21}),
        json!({"version":1,"after_revision_id":"invalid","limit":1}),
        json!({"version":1,"after_revision_id":null,"limit":1,"profile_id":"other"}),
    ] {
        assert_eq!(
            execute_import(&configured, LIST_LOCAL_IMPORT_REVISIONS, payload)
                .await
                .unwrap_err()
                .code,
            ErrorCode::INVALIDARGUMENT
        );
    }
    assert_eq!(
        execute_import(&configured, LIST_LOCAL_IMPORT_REVISIONS, first_page)
            .await
            .unwrap(),
        json!({"version":1,"revisions":[],"next_after_revision_id":null})
    );
    assert!(!profile.exists());
    configured.shutdown().await;
    unconfigured.shutdown().await;
}

#[cfg(any(target_os = "linux", windows))]
#[tokio::test]
async fn local_publication_keeps_the_prior_chart_after_changed_bytes_and_cancellation() {
    use scopehub_application::REVOKE_LOCAL_IMPORT_REVISION;
    use std::fs;

    let fixture = ImportFixture::new();
    let profile = fixture.0.join("profile");
    let source = fixture.0.join("source");
    fs::create_dir(&source).unwrap();
    let csv = source.join("source.csv");
    let initial = "Ticker,Date,Open,High,Low,Close,Shares\n000418,2026-11-19,4,7,3,5,20\n";
    fs::write(&csv, initial).unwrap();
    let state = state_with_local_profile(Some(&profile));
    let ids = select_csv(&state, &source).await;
    let mut preview_input = ids.clone();
    preview_input["mapping"] = equity_mapping();
    preview_input["row_limit"] = json!(1);
    let shown = execute_import(&state, PREVIEW_LOCAL_IMPORT, preview_input.clone())
        .await
        .unwrap();
    let mut save = preview_input.clone();
    save["expected_sha256"] = shown["sha256"].clone();
    let saved = execute_import(&state, SAVE_LOCAL_IMPORT_MAPPING, save)
        .await
        .unwrap();
    let mut publish = ids;
    publish["expected_sha256"] = shown["sha256"].clone();
    publish["review_revision"] = saved["record"]["review_revision"].clone();
    let first = execute_import(&state, PUBLISH_LOCAL_IMPORT, publish.clone())
        .await
        .unwrap();
    let read = json!({"version":1,"revision_id":first["revision"]["revision_id"]});
    let frozen = execute_import(&state, READ_LOCAL_IMPORT_REVISION, read.clone())
        .await
        .unwrap();

    let added = format!("{initial}000418,2026-11-20,4,8,3,6,21\n");
    fs::write(&csv, &added).unwrap();
    let shown = execute_import(&state, PREVIEW_LOCAL_IMPORT, preview_input)
        .await
        .unwrap();
    publish["expected_sha256"] = shown["sha256"].clone();
    assert_eq!(shown["preview"]["rows"].as_array().unwrap().len(), 1);
    assert_eq!(shown["preview"]["truncated"], true);
    fs::write(&csv, added.replace(",6,21", ",7,21")).unwrap();
    assert_eq!(
        execute_import(&state, PUBLISH_LOCAL_IMPORT, publish.clone())
            .await
            .unwrap_err()
            .code,
        ErrorCode::SNAPSHOTUNAVAILABLE
    );
    fs::write(&csv, &added).unwrap();
    state.cancel_local_import("publish-cancelled");
    assert_eq!(
        state
            .execute(
                "main",
                Some(PUBLISH_LOCAL_IMPORT),
                publish.clone(),
                Some("publish-cancelled")
            )
            .await
            .unwrap_err()
            .code,
        ErrorCode::CANCELLED
    );
    assert_eq!(
        execute_import(&state, READ_LOCAL_IMPORT_REVISION, read.clone())
            .await
            .unwrap(),
        frozen
    );
    let next = execute_import(&state, PUBLISH_LOCAL_IMPORT, publish.clone())
        .await
        .unwrap();
    assert_eq!(next["revision"]["bar_count"], 2);
    assert_ne!(
        next["revision"]["revision_id"],
        first["revision"]["revision_id"]
    );
    assert_eq!(
        execute_import(&state, PUBLISH_LOCAL_IMPORT, publish)
            .await
            .unwrap(),
        next
    );
    state.shutdown().await;

    let restarted = state_with_local_profile(Some(&profile));
    assert_eq!(
        execute_import(&restarted, READ_LOCAL_IMPORT_REVISION, read.clone())
            .await
            .unwrap(),
        frozen
    );
    let other = state_with_local_profile(Some(&fixture.0.join("other-profile")));
    assert_eq!(
        execute_import(&other, READ_LOCAL_IMPORT_REVISION, read.clone())
            .await
            .unwrap_err()
            .code,
        ErrorCode::SNAPSHOTUNAVAILABLE
    );
    assert_eq!(
        execute_import(&restarted, REVOKE_LOCAL_IMPORT_REVISION, read.clone())
            .await
            .unwrap(),
        read
    );
    assert_eq!(
        execute_import(&restarted, READ_LOCAL_IMPORT_REVISION, read)
            .await
            .unwrap_err()
            .code,
        ErrorCode::PERMISSIONDENIED
    );
    restarted.shutdown().await;
    other.shutdown().await;
}

#[cfg(any(target_os = "linux", windows))]
#[tokio::test]
async fn reviewed_mapping_save_requires_the_exact_public_preview() {
    use sha2::{Digest, Sha256};
    use std::fs;

    let fixture = ImportFixture::new();
    let profile = fixture.0.join("profile");
    let source = fixture.0.join("source");
    fs::create_dir(&source).unwrap();
    let csv = source.join("source.csv");
    let content = b"Ticker,Date,Open,High,Low,Close,AltClose,Shares\n000418,2026-11-19,4,7,3,5,6,20\n000418,2026-11-20,6,7,4,5,6,21\n";
    fs::write(&csv, content).unwrap();
    fs::write(source.join("unreviewed.csv"), content).unwrap();

    let state = state_with_local_profile(Some(&profile));
    let ids = select_csv(&state, &source).await;
    let mut request = ids.clone();
    request["mapping"] = equity_mapping();
    request["row_limit"] = json!(1);
    let mut save = request.clone();
    save["expected_sha256"] = json!(format!("{:x}", Sha256::digest(content)));
    assert_eq!(
        execute_import(&state, SAVE_LOCAL_IMPORT_MAPPING, save.clone())
            .await
            .unwrap_err()
            .code,
        ErrorCode::INVALIDARGUMENT,
        "a save cannot establish its own preview",
    );
    assert!(!profile.exists());

    let shown = execute_import(&state, PREVIEW_LOCAL_IMPORT, request)
        .await
        .unwrap();
    assert_eq!(shown["preview"]["rows"][0]["normalized"]["close"], 5.0);
    assert_eq!(shown["sha256"], save["expected_sha256"]);

    let mut unreviewed_mapping = save.clone();
    unreviewed_mapping["mapping"]["columns"]["close"] = json!("AltClose");
    assert_eq!(
        unreviewed_mapping["mapping"]["revision"],
        save["mapping"]["revision"]
    );
    assert_eq!(
        execute_import(&state, SAVE_LOCAL_IMPORT_MAPPING, unreviewed_mapping)
            .await
            .unwrap_err()
            .code,
        ErrorCode::INVALIDARGUMENT,
        "the same revision label cannot authorize another column mapping",
    );

    let folder = state
        .local_folders()
        .catalog(ids["folder_id"].as_str().unwrap().parse().unwrap())
        .unwrap();
    let mut unreviewed_file = import_request(&folder, "unreviewed.csv", equity_mapping());
    unreviewed_file["row_limit"] = save["row_limit"].clone();
    unreviewed_file["expected_sha256"] = save["expected_sha256"].clone();
    assert_eq!(
        execute_import(&state, SAVE_LOCAL_IMPORT_MAPPING, unreviewed_file)
            .await
            .unwrap_err()
            .code,
        ErrorCode::INVALIDARGUMENT,
        "a different file with identical bytes requires its own preview",
    );

    let mut unreviewed_sample = save.clone();
    unreviewed_sample["row_limit"] = json!(2);
    assert_eq!(
        execute_import(&state, SAVE_LOCAL_IMPORT_MAPPING, unreviewed_sample)
            .await
            .unwrap_err()
            .code,
        ErrorCode::INVALIDARGUMENT,
    );
    assert!(!profile.exists());

    // Change a row beyond the sample. The matching receipt cannot replace the live reread.
    let mut changed = content.to_vec();
    changed[content.len() - 2] = b'2';
    fs::write(&csv, &changed).unwrap();
    assert_eq!(
        execute_import(&state, SAVE_LOCAL_IMPORT_MAPPING, save.clone())
            .await
            .unwrap_err()
            .code,
        ErrorCode::SNAPSHOTUNAVAILABLE,
    );
    assert!(!profile.exists());

    // Retry the same reviewed bytes after cancellation before admission.
    fs::write(&csv, content).unwrap();
    state.local_import.cancel("save-before-admission");
    assert_eq!(
        state
            .execute(
                "main",
                Some(SAVE_LOCAL_IMPORT_MAPPING),
                save.clone(),
                Some("save-before-admission")
            )
            .await
            .unwrap_err()
            .code,
        ErrorCode::CANCELLED,
    );
    assert!(!profile.exists());
    let saved = execute_import(&state, SAVE_LOCAL_IMPORT_MAPPING, save.clone())
        .await
        .unwrap();
    assert_eq!(saved["record"]["review_revision"], 1);
    assert_eq!(saved["record"]["mapping"], equity_mapping());

    // Selecting the same path again still creates a new grant.
    let new_ids = select_csv(&state, &source).await;
    assert_ne!(ids["folder_id"], new_ids["folder_id"]);
    assert_eq!(
        execute_import(&state, SAVE_LOCAL_IMPORT_MAPPING, save.clone())
            .await
            .unwrap_err()
            .code,
        ErrorCode::STALECONTEXT,
    );
    save["folder_id"] = new_ids["folder_id"].clone();
    save["file_id"] = new_ids["file_id"].clone();
    let loaded = execute_import(&state, LOAD_LOCAL_IMPORT_MAPPING, new_ids)
        .await
        .unwrap();
    assert_eq!(loaded["status"], "saved");
    assert_eq!(
        execute_import(&state, SAVE_LOCAL_IMPORT_MAPPING, save)
            .await
            .unwrap_err()
            .code,
        ErrorCode::INVALIDARGUMENT,
    );
    state.shutdown().await;
}

#[cfg(any(target_os = "linux", windows))]
#[tokio::test]
async fn reviewed_mapping_reuses_live_source_after_restart_and_requires_review_for_changed_header()
{
    use sha2::{Digest, Sha256};
    use std::fs;

    let fixture = ImportFixture::new();
    let profile = fixture.0.join("profile");
    let other_profile = fixture.0.join("other-profile");
    let source = fixture.0.join("source");
    let other_source = fixture.0.join("other-source");
    fs::create_dir(&source).unwrap();
    fs::create_dir(&other_source).unwrap();
    let csv = source.join("source.csv");
    let content = b"Ticker,Date,Open,High,Low,Close,Shares\n000418,2026-11-19,4,6,3,5,20\n000418,2026-11-20,6,7,4,5,21\n";
    fs::write(&csv, content).unwrap();
    fs::write(other_source.join("source.csv"), content).unwrap();

    let first = state_with_local_profile(Some(&profile));
    let selected = source.canonicalize().unwrap();
    let first_ids = select_csv(&first, &selected).await;
    let mut uncorrected = first_ids.clone();
    uncorrected["row_limit"] = json!(1);
    uncorrected["mapping"] = equity_mapping();
    uncorrected["mapping"]["columns"]["close"] = json!("WrongClose");
    let raw = execute_import(&first, PREVIEW_LOCAL_IMPORT, uncorrected.clone())
        .await
        .unwrap();
    assert!(raw["preview"]["rows"][0]["normalized"].is_null());
    assert!(
        !profile.exists(),
        "opening a preview must not provision a workspace"
    );
    uncorrected["expected_sha256"] = raw["sha256"].clone();
    assert_eq!(
        execute_import(&first, SAVE_LOCAL_IMPORT_MAPPING, uncorrected.clone())
            .await
            .unwrap_err()
            .code,
        ErrorCode::INVALIDARGUMENT
    );
    assert!(
        !profile.exists(),
        "a failed review must not provision a workspace"
    );

    let mut corrected = uncorrected;
    corrected["mapping"] = equity_mapping();
    let corrected_preview = execute_import(&first, PREVIEW_LOCAL_IMPORT, corrected.clone())
        .await
        .unwrap();
    assert_eq!(
        corrected_preview["preview"]["rows"][0]["normalized"]["close"],
        5.0
    );
    let saved = first
        .execute(
            "main",
            Some(SAVE_LOCAL_IMPORT_MAPPING),
            corrected.clone(),
            None,
        )
        .await
        .unwrap();
    assert_eq!(saved["record"]["review_revision"], 1);
    assert_eq!(saved["record"]["mapping"], equity_mapping());
    assert_eq!(saved["record"]["header_sha256"].as_str().unwrap().len(), 64);
    assert!(profile.join("workspace-dev/workspace.sqlite3").is_file());
    for (operation, payload) in [
        (None, json!({"type": "snapshot"})),
        (Some("decisionNote.read"), note_read()),
    ] {
        assert_eq!(
            first
                .execute("main", operation, payload, None)
                .await
                .unwrap_err()
                .code,
            ErrorCode::PERMISSIONDENIED,
            "opening the writer for mapping confers no workspace grant",
        );
    }
    first.shutdown().await;
    drop(first);

    let restarted = state_with_development_workspace(Some(&profile));
    assert_eq!(snapshot(&restarted).await["workspace"]["revision"], 0);
    assert_eq!(
        execute_import(&restarted, LOAD_LOCAL_IMPORT_MAPPING, first_ids)
            .await
            .unwrap_err()
            .code,
        ErrorCode::STALECONTEXT
    );
    let ids = select_csv(&restarted, &source).await;
    // A data row changed beyond the review sample, preserving the decoded header.
    let updated = b"Ticker,Date,Open,High,Low,Close,Shares\n000418,2026-11-19,4,6,3,5,20\n000418,2026-11-20,6,7,4,5,22\n";
    fs::write(&csv, updated).unwrap();
    let loaded = execute_import(&restarted, LOAD_LOCAL_IMPORT_MAPPING, ids.clone())
        .await
        .unwrap();
    assert_eq!(loaded["status"], "saved");
    assert_eq!(loaded["record"], saved["record"]);
    assert_eq!(
        loaded["source_sha256"],
        format!("{:x}", Sha256::digest(updated))
    );
    assert_ne!(loaded["source_sha256"], raw["sha256"]);
    let mut preview = ids.clone();
    preview["expected_sha256"] = loaded["source_sha256"].clone();
    preview["mapping"] = loaded["record"]["mapping"].clone();
    let bar = execute_import(&restarted, PREVIEW_LOCAL_IMPORT, preview.clone())
        .await
        .unwrap();
    assert_eq!(bar["preview"]["rows"][0]["normalized"]["close"], 5.0);
    assert_eq!(bar["preview"]["rows"][0]["raw_fields"][0], "000418");

    let isolated = state_with_local_profile(Some(&other_profile));
    let isolated_ids = select_csv(&isolated, &source).await;
    assert_eq!(
        execute_import(&isolated, LOAD_LOCAL_IMPORT_MAPPING, isolated_ids)
            .await
            .unwrap()["status"],
        "not_saved"
    );
    isolated.shutdown().await;
    assert!(!other_profile.exists());

    let damaged_body =
        b"Ticker,Date,Open,High,Low,Close,Shares\n000418,2026-11-19,4,6,3,5,20\n\xff\n";
    fs::write(&csv, damaged_body).unwrap();
    let damaged_hash = format!("{:x}", Sha256::digest(damaged_body));
    let loaded_with_damage = execute_import(&restarted, LOAD_LOCAL_IMPORT_MAPPING, ids.clone())
        .await
        .unwrap();
    assert_eq!(loaded_with_damage["status"], "saved");
    assert_eq!(loaded_with_damage["record"], saved["record"]);
    assert_eq!(loaded_with_damage["source_sha256"], damaged_hash);

    preview["expected_sha256"] = json!(damaged_hash);
    let damaged_preview = execute_import(&restarted, PREVIEW_LOCAL_IMPORT, preview)
        .await
        .unwrap();
    assert_eq!(damaged_preview["sha256"], damaged_hash);
    assert_eq!(damaged_preview["preview"]["header"][5], "Close");
    assert!(
        damaged_preview["preview"]["issues"]
            .as_array()
            .unwrap()
            .iter()
            .any(|issue| issue["code"] == "decode_failed")
    );
    assert!(
        damaged_preview["preview"]["rows"]
            .as_array()
            .unwrap()
            .iter()
            .all(|row| row["normalized"].is_null())
    );

    let changed_header = b"Ticker,Date,Open,High,Low,Last,Shares\n000418,2026-11-19,4,6,3,5,20\n";
    fs::write(&csv, changed_header).unwrap();
    let changed = execute_import(&restarted, LOAD_LOCAL_IMPORT_MAPPING, ids.clone())
        .await
        .unwrap();
    assert_eq!(changed["status"], "review_required");
    assert!(changed["record"].is_null());
    assert_eq!(
        changed["source_sha256"],
        format!("{:x}", Sha256::digest(changed_header))
    );

    let damaged_header =
        b"Ticker,D\xffate,Open,High,Low,Close,Shares\n000418,2026-11-19,4,6,3,5,20\n";
    fs::write(&csv, damaged_header).unwrap();
    let unavailable = execute_import(&restarted, LOAD_LOCAL_IMPORT_MAPPING, ids)
        .await
        .unwrap_err();
    assert_eq!(unavailable.code, ErrorCode::DATAUNAVAILABLE);
    assert!(unavailable.message.to_lowercase().contains("header"));
    assert_eq!(
        unavailable.details.get("reason"),
        Some(&json!("csv_header_unavailable"))
    );
    assert_eq!(
        unavailable.details.get("sha256"),
        Some(&json!(format!("{:x}", Sha256::digest(damaged_header)))),
    );
    let other_ids = select_csv(&restarted, &other_source).await;
    assert_eq!(
        execute_import(&restarted, LOAD_LOCAL_IMPORT_MAPPING, other_ids)
            .await
            .unwrap()["status"],
        "not_saved"
    );
    restarted.shutdown().await;
}

#[cfg(windows)]
#[tokio::test]
async fn reviewed_mapping_reopens_a_legacy_v4_windows_key() {
    use scopehub_application::{MappingCommitGuard, mapping_header_digest};
    use sha2::{Digest, Sha256};
    use std::{fs, os::windows::ffi::OsStrExt};

    struct FixtureSeed;

    impl MappingCommitGuard for FixtureSeed {
        fn commit<T>(&self, write: impl FnOnce() -> Result<T, Error>) -> Result<T, Error> {
            write()
        }
    }

    // Reproduce the original v4 key framing for a canonical selector whose
    // admitted spelling was already the long on-disk path.
    fn legacy_fingerprint(domain: &[u8], path: &Path) -> [u8; 32] {
        let mut hash = Sha256::new();
        hash.update(domain);
        hash.update([0]);
        for part in path.components() {
            let units: Vec<_> = part.as_os_str().encode_wide().collect();
            hash.update((2 * units.len() as u64).to_be_bytes());
            for unit in units {
                hash.update(unit.to_be_bytes());
            }
        }
        hash.finalize().into()
    }

    let fixture = ImportFixture::new();
    let source = fixture.0.join("Legacy Mapping Source");
    let profile = fixture.0.join("profile");
    fs::create_dir(&source).unwrap();
    fs::write(
        source.join("source.csv"),
        b"Ticker,Date,Open,High,Low,Close,Shares\n000418,2026-11-19,4,6,3,5,20\n",
    )
    .unwrap();
    let canonical = source.canonicalize().unwrap();
    let mut legacy_key = Sha256::new();
    legacy_key.update(b"scopehub-local-mapping-source-v1\0");
    legacy_key.update(legacy_fingerprint(b"scopehub-local-folder-v1", &canonical));
    legacy_key.update(legacy_fingerprint(
        b"scopehub-local-relative-file-v1",
        Path::new("source.csv"),
    ));

    let first = state_with_local_profile(Some(&profile));
    let mut payload = select_csv(&first, &canonical).await;
    payload["mapping"] = equity_mapping();
    let shown = execute_import(&first, PREVIEW_LOCAL_IMPORT, payload)
        .await
        .unwrap();
    let mapping = serde_json::from_value(equity_mapping()).unwrap();
    let header = mapping_header_digest(&shown, &mapping).unwrap();
    // Seed a real SQLite v4 record with the old key, independently of the
    // corrected host association code.
    let seeded = first
        .workspace
        .save(
            format!("{:x}", legacy_key.finalize()),
            header,
            mapping,
            FixtureSeed,
            None,
        )
        .await
        .unwrap();
    first.shutdown().await;

    let reopened = state_with_local_profile(Some(&profile));
    let alternate = source.with_file_name("lEGACY mAPPING sOURCE");
    assert_eq!(alternate.canonicalize().unwrap(), canonical);
    let ids = select_csv(&reopened, &alternate).await;
    let loaded = execute_import(&reopened, LOAD_LOCAL_IMPORT_MAPPING, ids)
        .await
        .unwrap();
    assert_eq!(loaded["status"], "saved");
    assert_eq!(loaded["record"], seeded["record"]);
    assert_eq!(loaded["source_sha256"], shown["sha256"]);
    reopened.shutdown().await;
}

#[cfg(any(target_os = "linux", windows))]
#[tokio::test]
async fn pending_local_import_stops_on_cancellation_selection_change_or_shutdown() {
    use scopehub_platform::local_folder::{Cancellation, FolderError};
    use std::fs;

    #[derive(Clone, Copy)]
    enum Interruption {
        Cancel,
        Revoke,
        Shutdown,
    }

    for interruption in [
        Interruption::Cancel,
        Interruption::Revoke,
        Interruption::Shutdown,
    ] {
        let fixture = ImportFixture::new();
        let selected = fixture.0.join("selected");
        fs::create_dir(&selected).unwrap();
        let content = b"contract,quote\nTEST,7\n";
        fs::write(selected.join("source.csv"), content).unwrap();
        let module = fixture.0.join("python").join("worker_runtime");
        fs::create_dir_all(&module).unwrap();
        fs::write(module.join("local_import.py"), r#"
import json, pathlib, sys, time
print(json.dumps({'protocol_version':'1.1','type':'hello','message_id':'74237caa-395f-4d71-a07f-bb15f86fda88',
    'timestamp_ms':0,'role':'market','worker_version':'fixture','supported_protocols':['1.0','1.1'],
    'capabilities':['market.previewLocalImport']}), flush=True)
sys.stdin.buffer.read()
pathlib.Path('received').touch()
time.sleep(30)
"#).unwrap();
        let config = development_config().expect("development worker");
        let state = State {
            local_import: LocalImport::new(Some(WorkerConfig {
                root: fixture.0.clone(),
                ..config
            })),
            ..state()
        };
        let folders = state.local_folders();
        let catalog = folders
            .adopt_selected(&selected, &Cancellation::default())
            .unwrap();
        let request = import_request(
            &catalog,
            "source.csv",
            json!({"version":1,"revision":"1","encoding":"utf-8","delimiter":",","record_kind":"quote_snapshot"}),
        );
        let preview = state.execute(
            "main",
            Some(PREVIEW_LOCAL_IMPORT),
            request,
            Some("preview-1"),
        );
        tokio::pin!(preview);
        let ready = fixture.0.join("received");
        tokio::select! {
            result = &mut preview => panic!("preview ended before it could be interrupted: {result:?}"),
            _ = tokio::time::timeout(Duration::from_secs(3), async {
                while !ready.exists() { tokio::time::sleep(Duration::from_millis(10)).await; }
            }) => assert!(ready.exists(), "controlled worker did not receive the bytes"),
        }
        let interrupt = async {
            match interruption {
                Interruption::Cancel => state.local_import.cancel("preview-1"),
                Interruption::Revoke => folders.revoke(catalog.folder_id).unwrap(),
                Interruption::Shutdown => state.shutdown().await,
            }
        };
        let (result, ()) = tokio::time::timeout(Duration::from_secs(3), async {
            tokio::join!(preview, interrupt)
        })
        .await
        .unwrap();
        let code = match interruption {
            Interruption::Revoke => ErrorCode::STALECONTEXT,
            Interruption::Cancel | Interruption::Shutdown => ErrorCode::CANCELLED,
        };
        assert_eq!(result.unwrap_err().code, code);
        if matches!(interruption, Interruption::Shutdown) {
            assert_eq!(
                folders.check_current(catalog.folder_id),
                Err(FolderError::SelectionUnavailable)
            );
            assert!(matches!(
                folders.adopt_selected(&selected, &Cancellation::default()),
                Err(FolderError::SelectionUnavailable)
            ));
            fs::rename(&selected, fixture.0.join("released")).unwrap();
        }
        state.shutdown().await;
    }
}

#[cfg(any(target_os = "linux", windows))]
#[test]
fn cancelling_an_admitted_reviewed_save_keeps_the_profile_unwritten() {
    use std::{fs, task::Poll};

    // Occupy the only blocking slot after getting a real public preview so the
    // save can be cancelled deterministically while its live reread is pending.
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .max_blocking_threads(1)
        .build()
        .unwrap();
    runtime.block_on(async {
        let fixture = ImportFixture::new();
        let profile = fixture.0.join("profile");
        let source = fixture.0.join("source");
        fs::create_dir(&source).unwrap();
        fs::write(
            source.join("source.csv"),
            b"Ticker,Date,Open,High,Low,Close,Shares\n000418,2026-11-19,4,6,3,5,20\n",
        )
        .unwrap();

        let state = state_with_local_profile(Some(&profile));
        let ids = select_csv(&state, &source).await;
        let mut save = ids.clone();
        save["mapping"] = equity_mapping();
        let shown = execute_import(&state, PREVIEW_LOCAL_IMPORT, save.clone())
            .await
            .unwrap();
        save["expected_sha256"] = shown["sha256"].clone();

        let (entered, blocking_slot_held) = oneshot::channel();
        let (release, released) = oneshot::channel::<()>();
        let blocker = tokio::task::spawn_blocking(move || {
            entered.send(()).unwrap();
            let _ = released.blocking_recv();
        });
        blocking_slot_held.await.unwrap();
        let save = state.execute(
            "main",
            Some(SAVE_LOCAL_IMPORT_MAPPING),
            save,
            Some("save-with-pending-read"),
        );
        tokio::pin!(save);
        future::poll_fn(|context| {
            assert!(std::future::Future::poll(save.as_mut(), context).is_pending());
            Poll::Ready(())
        })
        .await;
        state.local_import.cancel("save-with-pending-read");
        let result = tokio::time::timeout(Duration::from_secs(3), save).await;
        release.send(()).unwrap();
        blocker.await.unwrap();
        assert_eq!(result.unwrap().unwrap_err().code, ErrorCode::CANCELLED);
        assert_eq!(
            execute_import(&state, LOAD_LOCAL_IMPORT_MAPPING, ids)
                .await
                .unwrap()["status"],
            "not_saved",
        );
        assert!(!profile.exists());
        state.shutdown().await;
    });
}

struct DelayedPage {
    worker: MarketWorker,
    reached_page: Option<oneshot::Sender<u32>>,
    started_cleanup: Option<oneshot::Sender<()>>,
    finish_cleanup: oneshot::Receiver<()>,
}

impl MarketPort for DelayedPage {
    fn request<'a>(&'a mut self, operation: &'a str, payload: &'a Value) -> MarketFuture<'a> {
        Box::pin(async move {
            if operation == "market.readHistoryPage" {
                let pid = self
                    .worker
                    .process_id()
                    .expect("catalog started the worker");
                self.reached_page.take().unwrap().send(pid).unwrap();
                return future::pending().await;
            }
            self.worker.request(operation, payload).await
        })
    }
}

impl MarketLifecycle for DelayedPage {
    fn status(&self) -> MarketStatus {
        self.worker.status()
    }

    fn shutdown(&mut self) -> ShutdownFuture<'_> {
        Box::pin(async {
            self.started_cleanup.take().unwrap().send(()).unwrap();
            (&mut self.finish_cleanup).await.unwrap();
            self.worker.shutdown().await
        })
    }
}

fn worker() -> MarketWorker {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..");
    MarketWorker::new(Some(WorkerConfig::development(&root)))
}

fn history(instrument: &str, timeframe: &str, unit: &str) -> Value {
    json!({
        "series": {"instrument_id":instrument,"provider_id":"scopehub.synthetic","timeframe":timeframe,
                   "price_adjustment":"raw","session_scope":"sample-sessions","quantity_unit":unit,
                   "calendar_id":format!("{instrument}.sample-calendar.v1")},
        "from_ms":1704067200000_i64,"to_ms":1704499200000_i64,"warmup_bars":0,
    })
}

#[tokio::test]
async fn new_history_loads_while_preparation_is_held_and_only_its_owner_can_cancel() {
    const OLD: &str = "b24de982-7af4-4ab0-9120-4d79edb71927";
    const NEW: &str = "d6f81b54-1c2f-45bf-84ac-54adbfa55627";
    let state = State {
        reads: Mutex::new(MarketRegistry::new(worker())),
        ..state()
    };
    let mut input = history("SIM.CN.FUTURE.001", "1m", "contracts");
    let frozen = state
        .execute_local_operation("market.readHistoryPage", input.clone(), None)
        .await
        .unwrap();
    input["snapshot"] = frozen["snapshot"].clone();
    input["include_stream_position"] = json!(true);
    input["page_size"] = json!(1);

    let (reached_page, entered) = oneshot::channel();
    let (started_cleanup, entered_cleanup) = oneshot::channel();
    let (release_cleanup, finish_cleanup) = oneshot::channel();
    let delayed = DelayedPage {
        worker: worker(),
        reached_page: Some(reached_page),
        started_cleanup: Some(started_cleanup),
        finish_cleanup,
    };
    let preparing = state.prepare(
        OLD.into(),
        input.clone(),
        MarketRegistry::new(delayed),
        || Ok(()),
    );
    tokio::pin!(preparing);
    let pid = tokio::select! {
        pid = entered => pid.unwrap(),
        result = &mut preparing => panic!("preparation completed before the gate: {result:?}"),
    };
    #[cfg(target_os = "linux")]
    assert!(PathBuf::from(format!("/proc/{pid}")).exists());
    #[cfg(not(target_os = "linux"))]
    assert_ne!(pid, 0);

    let replacement = tokio::time::timeout(
        Duration::from_secs(4),
        state.execute_local_operation(
            "market.readHistoryPage",
            history("SIM.CN.STOCK.001", "5m", "shares"),
            None,
        ),
    )
    .await
    .unwrap()
    .unwrap();
    assert_eq!(replacement["dataset"]["instrument_id"], "SIM.CN.STOCK.001");
    assert_eq!(replacement["dataset"]["timeframe"], "5m");
    assert_eq!(replacement["bars"].as_array().unwrap().len(), 8);
    assert!(replacement["stream_position"].is_null());

    state.cancel_replay(ReplayTask::Preparation(OLD.into()));
    tokio::select! {
        entered = entered_cleanup => entered.unwrap(),
        result = &mut preparing => panic!("preparation completed before cleanup: {result:?}"),
    }
    assert!(
        matches!(state.begin_replay(ReplayTask::Preparation(NEW.into())), Err(error) if error.code == ErrorCode::BUSY)
    );
    release_cleanup.send(()).unwrap();
    assert_eq!(preparing.await.unwrap_err().code, ErrorCode::CANCELLED);
    #[cfg(target_os = "linux")]
    assert!(!PathBuf::from(format!("/proc/{pid}")).exists());

    let (slot, mut cancelled) = state
        .begin_replay(ReplayTask::Preparation(NEW.into()))
        .unwrap();
    state.cancel_replay(ReplayTask::Preparation(OLD.into()));
    state.cancel_replay(ReplayTask::Subscription(NEW.into()));
    assert_eq!(cancelled.try_recv(), Err(TryRecvError::Empty));
    state.cancel_replay(ReplayTask::Preparation(NEW.into()));
    assert_eq!(cancelled.try_recv(), Ok(()));
    drop(slot);

    let prepared = state
        .prepare(NEW.into(), input, MarketRegistry::new(worker()), || Ok(()))
        .await
        .unwrap();
    assert_eq!(prepared["snapshot"], frozen["snapshot"]);
    assert_eq!(prepared["stream_position"]["sequence"], 0);
    assert_eq!(prepared["bars"].as_array().unwrap().len(), 1);
    state.shutdown().await;
    assert!(
        matches!(state.begin_replay(ReplayTask::Preparation(NEW.into())), Err(error) if error.code == ErrorCode::CANCELLED)
    );
}

struct AppLocalData(PathBuf);

impl AppLocalData {
    fn new() -> Self {
        use std::{
            sync::atomic::{AtomicU64, Ordering},
            time::{SystemTime, UNIX_EPOCH},
        };

        static SEQUENCE: AtomicU64 = AtomicU64::new(0);
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let sequence = SEQUENCE.fetch_add(1, Ordering::Relaxed);
        let temporary = std::env::temp_dir();
        #[cfg(unix)]
        let temporary = temporary.canonicalize().unwrap();
        let path = temporary.join(format!(
            "scopehub-desktop-test-{}-{stamp}-{sequence}",
            std::process::id(),
        ));
        scopehub_persistence::SqliteWorkspace::prepare_profile_root(&path).unwrap();
        Self(path)
    }

    fn database(&self) -> PathBuf {
        self.0.join("workspace-dev").join("workspace.sqlite3")
    }
}

impl Drop for AppLocalData {
    fn drop(&mut self) {
        std::fs::remove_dir_all(&self.0).unwrap();
    }
}

fn uuid(number: u64) -> String {
    format!("00000000-0000-4000-8000-{number:012x}")
}

fn mutation(revision: u64, operation: &str, arguments: Value, id: u64) -> Value {
    json!({
        "protocol_version": "1.1", "type": "request", "task_id": null, "timestamp_ms": 123,
        "message_id": uuid(id + 1), "request_id": uuid(id + 2),
        "scope": {
            "workspace_id": "main", "target": {"kind": "chart", "object_id": "main-chart"},
            "expected_workspace_revision": revision, "expected_selection_revision": 0,
            "idempotency_key": uuid(id + 3),
        },
        "intent": {"operation": operation, "arguments": arguments},
    })
}

fn note_read() -> Value {
    json!({
        "protocol_version": "1.1", "type": "request", "timestamp_ms": 123,
        "message_id": uuid(60), "request_id": uuid(61),
        "scope": {"workspace_id": "main"},
        "intent": {"operation": "decisionNote.read", "arguments": {"note_id": null, "offset": 0}},
    })
}

async fn snapshot(state: &State) -> Value {
    state
        .execute("main", None, json!({"type": "snapshot"}), None)
        .await
        .unwrap()
}

#[tokio::test]
async fn desktop_dispatch_restores_replays_and_undoes_with_one_owned_connection() {
    let profile = AppLocalData::new();
    let first = state_with_development_workspace(Some(&profile.0));
    let opened = snapshot(&first).await;
    assert_eq!(opened["workspace"]["revision"], 0);
    assert_eq!(opened["workspace"]["timeframe"], "1m");
    assert_eq!(opened["selection_revision"], 0);
    assert!(opened["workspace"]["dataset"].is_null());
    assert!(opened["undo"].is_null());
    assert!(first.reads.lock().await.status().process_id.is_none());

    let set = mutation(0, "chart.setTimeframe", json!({"timeframe": "5m"}), 10);
    let reply = first
        .execute("main", Some(SET_TIMEFRAME), set.clone(), None)
        .await
        .unwrap();
    assert_eq!(reply["request_id"], set["request_id"]);
    assert_eq!(reply["outcome"]["result"]["timeframe"], "5m");
    assert_eq!(reply["outcome"]["result"]["revision"], 1);
    let receipt = &reply["outcome"]["receipt"];
    let operation_id = receipt["operation_id"].as_str().unwrap();
    first.shutdown().await;
    assert!(!profile.database().with_extension("sqlite3-wal").exists());
    assert_eq!(
        first
            .execute("main", Some(SET_TIMEFRAME), set.clone(), None)
            .await
            .unwrap_err()
            .code,
        ErrorCode::CANCELLED,
    );

    let restarted = state_with_development_workspace(Some(&profile.0));
    let restored = snapshot(&restarted).await;
    assert_eq!(restored["workspace"]["timeframe"], "5m");
    assert_eq!(restored["workspace"]["revision"], 1);
    assert_eq!(
        restored["undo"],
        json!({
            "operation_id": operation_id, "committed_workspace_revision": 1,
        })
    );
    let mut retry = set;
    retry["message_id"] = json!(uuid(21));
    retry["request_id"] = json!(uuid(22));
    let repeated = restarted
        .execute("main", Some(SET_TIMEFRAME), retry, None)
        .await
        .unwrap();
    assert_eq!(repeated["request_id"], uuid(22));
    assert_eq!(&repeated["outcome"]["receipt"], receipt);

    let undo = mutation(
        1,
        "workspace.undo",
        json!({"operation_id": operation_id}),
        30,
    );
    let inverse = restarted
        .execute("main", Some(UNDO), undo, None)
        .await
        .unwrap();
    assert_eq!(inverse["outcome"]["result"]["timeframe"], "1m");
    assert_eq!(inverse["outcome"]["result"]["revision"], 2);
    assert_ne!(inverse["outcome"]["receipt"]["operation_id"], operation_id);
    restarted.shutdown().await;

    let third = state_with_development_workspace(Some(&profile.0));
    let restored = snapshot(&third).await;
    assert_eq!(restored["workspace"]["timeframe"], "1m");
    assert_eq!(restored["workspace"]["revision"], 2);
    assert!(restored["undo"].is_null());
    assert!(third.reads.lock().await.status().process_id.is_none());
    third.shutdown().await;
}

#[tokio::test]
async fn desktop_rejects_other_windows_forged_identity_and_unconfigured_mutations() {
    let profile = AppLocalData::new();
    let command = mutation(0, "chart.setTimeframe", json!({"timeframe": "5m"}), 50);
    let disabled = state();
    for (operation, request) in [
        (Some(SET_TIMEFRAME), command.clone()),
        (Some("decisionNote.read"), note_read()),
        (None, json!({"type": "snapshot"})),
    ] {
        assert_eq!(
            disabled
                .execute("main", operation, request, None)
                .await
                .unwrap_err()
                .code,
            ErrorCode::PERMISSIONDENIED,
        );
    }
    assert!(!profile.database().exists());

    let desktop = state_with_development_workspace(Some(&profile.0));
    for (operation, request) in [
        (Some(SET_TIMEFRAME), command.clone()),
        (Some("decisionNote.read"), note_read()),
        (None, json!({"type": "snapshot"})),
    ] {
        assert_eq!(
            desktop
                .execute("other-window", operation, request, None)
                .await
                .unwrap_err()
                .code,
            ErrorCode::PERMISSIONDENIED,
        );
    }
    for (operation, request) in [
        (Some(UNDO), command.clone()),
        (Some(SET_TIMEFRAME), json!({"type": "snapshot"})),
        (None, command.clone()),
        (None, Value::Null),
        (Some(SET_TIMEFRAME), Value::Null),
        (Some("instrument.search"), Value::Null),
        (None, json!({"type": "snapshot", "profile_root": "/caller"})),
    ] {
        assert_eq!(
            desktop
                .execute("main", operation, request, None)
                .await
                .unwrap_err()
                .code,
            ErrorCode::INVALIDARGUMENT,
        );
    }
    assert_eq!(
        desktop
            .execute("main", Some("workspace.getContext"), json!({}), None)
            .await
            .unwrap_err()
            .code,
        ErrorCode::UNSUPPORTEDCAPABILITY,
    );
    for (field, value) in [
        ("profile_root", "/caller"),
        ("principal_id", "other"),
        ("client_id", "client"),
        ("grant", "all"),
    ] {
        let mut forged = command.clone();
        forged["scope"][field] = json!(value);
        assert_eq!(
            desktop
                .execute("main", Some(SET_TIMEFRAME), forged, None)
                .await
                .unwrap_err()
                .code,
            ErrorCode::INVALIDARGUMENT,
        );
    }
    let mut other_workspace = command;
    other_workspace["scope"]["workspace_id"] = json!("caller-workspace");
    let rejected = desktop
        .execute("main", Some(SET_TIMEFRAME), other_workspace, None)
        .await
        .unwrap();
    assert_eq!(rejected["outcome"]["error"]["code"], "PERMISSION_DENIED");
    let notes = desktop
        .execute("main", Some("decisionNote.read"), note_read(), None)
        .await
        .unwrap();
    assert_eq!(notes["outcome"]["state"], "completed");
    assert_eq!(notes["outcome"]["result"]["notes"], json!([]));
    assert_eq!(snapshot(&desktop).await["workspace"]["revision"], 0);
    assert!(desktop.reads.lock().await.status().process_id.is_none());
    desktop.shutdown().await;
}

#[tokio::test]
async fn copied_workspace_bytes_cannot_enable_a_grant_or_cross_native_profiles() {
    let first = AppLocalData::new();
    let writer = state_with_development_workspace(Some(&first.0));
    let command = mutation(0, "chart.setTimeframe", json!({"timeframe": "5m"}), 100);
    let reply = writer
        .execute("main", Some(SET_TIMEFRAME), command, None)
        .await
        .unwrap();
    assert_eq!(reply["outcome"]["result"]["timeframe"], "5m");
    writer.shutdown().await;

    // Normal startup has no workspace grant, even when workspace bytes exist.
    let ordinary = state();
    assert_eq!(
        ordinary
            .execute("main", None, json!({"type": "snapshot"}), None)
            .await
            .unwrap_err()
            .code,
        ErrorCode::PERMISSIONDENIED,
    );
    assert!(first.database().is_file());
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let database = first.database();
        let mode =
            |path: &std::path::Path| std::fs::metadata(path).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode(database.parent().unwrap()), 0o700);
        assert_eq!(mode(&database), 0o600);
    }

    let other = AppLocalData::new();
    let copy = other.database();
    scopehub_persistence::SqliteWorkspace::prepare_profile_root(copy.parent().unwrap()).unwrap();
    std::fs::copy(first.database(), &copy).unwrap();
    let before = std::fs::read(&copy).unwrap();
    let foreign = state_with_development_workspace(Some(&other.0));
    assert_eq!(
        foreign
            .execute("main", None, json!({"type": "snapshot"}), None)
            .await
            .unwrap_err()
            .code,
        ErrorCode::INTERNALERROR,
    );
    assert_eq!(std::fs::read(&copy).unwrap(), before);
    foreign.shutdown().await;
}
