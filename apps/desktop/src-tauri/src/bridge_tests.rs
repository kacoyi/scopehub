use std::{future, path::PathBuf};

use scopehub_application::{MarketFuture, MarketStatus, ShutdownFuture};
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
        std::fs::create_dir(&path).unwrap();
        Self(path.canonicalize().unwrap())
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
    use scopehub_platform::local_folder::Cancellation;
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
    let selected = folders
        .adopt_selected(&fixture.0, &Cancellation::default())
        .unwrap();
    let equity_mapping = json!({
        "version":1,"revision":"equity-fixture-2","encoding":"utf-8","delimiter":",","record_kind":"native_ohlcv",
        "columns":{"symbol":"Ticker","timestamp":"Date","trading_day":"Date","open":"Open","high":"High","low":"Low","close":"Close","volume":"Shares"},
        "instrument_kind":"equity","venue":"TEST","timezone":"UTC","calendar_id":"fixture-calendar-1","session_scope":"regular",
        "timeframe":"1d","quantity_unit":"shares","price_adjustment":"raw","volume_semantics":"per_bar",
        "timestamp_convention":"bar_start","timestamp_format":"%Y-%m-%d","trading_day_format":"%Y-%m-%d","daily_start_time":"09:30:00"
    });
    let mut input = import_request(&selected, "equity.csv", equity_mapping);
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
#[tokio::test]
async fn pending_local_preview_stops_on_cancellation_selection_change_or_shutdown() {
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
        fs::write(selected.join("source.csv"), b"contract,quote\nTEST,7\n").unwrap();
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
        let preview =
            state.execute_local_operation(PREVIEW_LOCAL_IMPORT, request, Some("preview-1"));
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
            fs::rename(selected, fixture.0.join("released")).unwrap();
        }
        state.shutdown().await;
    }
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
