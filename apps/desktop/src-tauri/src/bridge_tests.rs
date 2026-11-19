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
        .read("market.readHistoryPage", input.clone())
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
