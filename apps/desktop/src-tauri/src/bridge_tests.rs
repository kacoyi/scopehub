use std::{future, path::PathBuf};

use scopehub_application::{MarketFuture, MarketStatus, ShutdownFuture};
use serde_json::json;
use tokio::sync::oneshot::error::TryRecvError;

use super::*;

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
        .execute("main", Some("market.readHistoryPage"), input.clone())
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
        state.read(
            "market.readHistoryPage",
            history("SIM.CN.STOCK.001", "5m", "shares"),
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

async fn snapshot(state: &State) -> Value {
    state
        .execute("main", None, json!({"type": "snapshot"}))
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
        .execute("main", Some(SET_TIMEFRAME), set.clone())
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
            .execute("main", Some(SET_TIMEFRAME), set.clone())
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
        .execute("main", Some(SET_TIMEFRAME), retry)
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
    let inverse = restarted.execute("main", Some(UNDO), undo).await.unwrap();
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
        (None, json!({"type": "snapshot"})),
    ] {
        assert_eq!(
            disabled
                .execute("main", operation, request)
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
        (None, json!({"type": "snapshot"})),
    ] {
        assert_eq!(
            desktop
                .execute("other-window", operation, request)
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
                .execute("main", operation, request)
                .await
                .unwrap_err()
                .code,
            ErrorCode::INVALIDARGUMENT,
        );
    }
    assert_eq!(
        desktop
            .execute("main", Some("workspace.getContext"), json!({}))
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
                .execute("main", Some(SET_TIMEFRAME), forged)
                .await
                .unwrap_err()
                .code,
            ErrorCode::INVALIDARGUMENT,
        );
    }
    let mut other_workspace = command;
    other_workspace["scope"]["workspace_id"] = json!("caller-workspace");
    let rejected = desktop
        .execute("main", Some(SET_TIMEFRAME), other_workspace)
        .await
        .unwrap();
    assert_eq!(rejected["outcome"]["error"]["code"], "PERMISSION_DENIED");
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
        .execute("main", Some(SET_TIMEFRAME), command)
        .await
        .unwrap();
    assert_eq!(reply["outcome"]["result"]["timeframe"], "5m");
    writer.shutdown().await;

    // Normal startup has no workspace grant, even when workspace bytes exist.
    let ordinary = state();
    assert_eq!(
        ordinary
            .execute("main", None, json!({"type": "snapshot"}))
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
            .execute("main", None, json!({"type": "snapshot"}))
            .await
            .unwrap_err()
            .code,
        ErrorCode::INTERNALERROR,
    );
    assert_eq!(std::fs::read(&copy).unwrap(), before);
    foreign.shutdown().await;
}
