use std::{path::Path, sync::Mutex as SyncMutex, time::Duration};

use scopehub_application::{
    Actor, MarketLifecycle, MarketPort, MarketRegistry, SET_TIMEFRAME, UNDO, failure,
};
use scopehub_domain::protocol::{Error, ErrorCode};
use scopehub_worker_host::market::{MarketWorker, WorkerConfig};
use serde_json::Value;
#[cfg(windows)]
use tauri::ipc::Channel;
use tokio::sync::{Mutex, MutexGuard, oneshot};
#[cfg(windows)]
use {scopehub_worker_host::market::run_subscription, serde_json::json};

use crate::workspace::WorkspaceBridge;

pub struct State {
    reads: Mutex<MarketRegistry<MarketWorker>>,
    replay: Mutex<()>,
    cancellation: SyncMutex<Cancellation>,
    workspace: WorkspaceBridge,
}

#[derive(Default)]
struct Cancellation {
    closing: bool,
    active: Option<(ReplayTask, oneshot::Sender<()>)>,
}

#[derive(PartialEq, Eq)]
enum ReplayTask {
    Preparation(String),
    Subscription(String),
}

struct ReplaySlot<'a> {
    _lock: MutexGuard<'a, ()>,
    cancellation: &'a SyncMutex<Cancellation>,
}

impl Drop for ReplaySlot<'_> {
    fn drop(&mut self) {
        // Clear the sender before releasing admission for the next request.
        self.cancellation.lock().expect("cancellation state").active = None;
    }
}

fn development_config() -> Option<WorkerConfig> {
    // Never ship a release executable that searches for development Python.
    // Bundled worker discovery and clean-machine acceptance belong to M5.
    #[cfg(debug_assertions)]
    return Some(WorkerConfig::development(
        &std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../.."),
    ));
    #[cfg(not(debug_assertions))]
    None
}

pub fn state() -> State {
    State {
        reads: Mutex::new(MarketRegistry::new(MarketWorker::new(development_config()))),
        replay: Mutex::new(()),
        cancellation: SyncMutex::default(),
        workspace: WorkspaceBridge::disabled(),
    }
}

pub fn state_with_development_workspace(app_local_data: Option<&Path>) -> State {
    State {
        workspace: WorkspaceBridge::development(app_local_data),
        ..state()
    }
}

impl State {
    async fn execute(
        &self,
        window_label: &str,
        operation: Option<&str>,
        payload: Value,
    ) -> Result<Value, Error> {
        authorize_window(window_label)?;
        match operation {
            None | Some(SET_TIMEFRAME | UNDO) => self.workspace.dispatch(operation, payload).await,
            Some(operation) => self.read(operation, payload).await,
        }
    }

    async fn read(&self, operation: &str, payload: Value) -> Result<Value, Error> {
        // Do not create an unbounded queue of requests in the native process.
        let mut registry = self
            .reads
            .try_lock()
            .map_err(|_| failure(ErrorCode::BUSY))?;
        registry.execute(Actor::User, operation, payload).await
    }

    fn begin_replay(
        &self,
        task: ReplayTask,
    ) -> Result<(ReplaySlot<'_>, oneshot::Receiver<()>), Error> {
        let id = match &task {
            ReplayTask::Preparation(id) | ReplayTask::Subscription(id) => id,
        };
        if id.len() != 36 {
            return Err(failure(ErrorCode::INVALIDARGUMENT));
        }
        let lock = self
            .replay
            .try_lock()
            .map_err(|_| failure(ErrorCode::BUSY))?;
        let mut cancellation = self.cancellation.lock().expect("cancellation state");
        if cancellation.closing {
            return Err(failure(ErrorCode::CANCELLED));
        }
        let (send, receive) = oneshot::channel();
        cancellation.active = Some((task, send));
        Ok((
            ReplaySlot {
                _lock: lock,
                cancellation: &self.cancellation,
            },
            receive,
        ))
    }

    fn cancel_replay(&self, task: ReplayTask) {
        let mut cancellation = self.cancellation.lock().expect("cancellation state");
        if let Some((_, send)) = cancellation.active.take_if(|(active, _)| *active == task) {
            let _ = send.send(());
        }
    }

    async fn prepare<P: MarketPort + MarketLifecycle>(
        &self,
        id: String,
        payload: Value,
        mut registry: MarketRegistry<P>,
        started: impl FnOnce() -> Result<(), Error>,
    ) -> Result<Value, Error> {
        if payload["include_stream_position"] != true
            || payload["page_size"] != 1
            || !payload["snapshot"].is_object()
            || payload.get("cursor").is_some()
        {
            return Err(failure(ErrorCode::INVALIDARGUMENT));
        }
        let (_slot, cancelled) = self.begin_replay(ReplayTask::Preparation(id))?;
        started()?;
        let result = tokio::select! {
            biased;
            _ = cancelled => Err(failure(ErrorCode::CANCELLED)),
            result = tokio::time::timeout(Duration::from_secs(5), registry.execute(Actor::User, "market.readHistoryPage", payload)) =>
                result.unwrap_or_else(|_| Err(failure(ErrorCode::TIMEOUT))),
        };
        // A cancelled partial exchange is never reused. Keep admission through cleanup.
        let cleanup = registry.shutdown().await;
        result.and_then(|value| cleanup.map(|()| value))
    }

    pub async fn shutdown(&self) {
        {
            let mut cancellation = self.cancellation.lock().expect("cancellation state");
            cancellation.closing = true;
            if let Some((_, sender)) = cancellation.active.take() {
                let _ = sender.send(());
            }
        }
        self.workspace.shutdown().await;
        let _replay = self.replay.lock().await;
        let _ = self.reads.lock().await.shutdown().await;
    }
}

fn authorize_window(label: &str) -> Result<(), Error> {
    if label == "main" {
        Ok(())
    } else {
        Err(failure(ErrorCode::PERMISSIONDENIED))
    }
}

#[cfg(windows)]
#[tauri::command]
pub async fn execute_operation(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, State>,
    operation: Option<String>,
    payload: Value,
) -> Result<Value, Error> {
    state
        .execute(window.label(), operation.as_deref(), payload)
        .await
}

#[cfg(windows)]
#[tauri::command]
pub async fn prepare_market_subscription(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, State>,
    preparation_id: String,
    payload: Value,
    progress: Channel<Value>,
) -> Result<Value, Error> {
    authorize_window(window.label())?;
    state
        .prepare(
            preparation_id,
            payload,
            MarketRegistry::new(MarketWorker::new(development_config())),
            || {
                progress
                    .send(json!({"started": true}))
                    .map_err(|_| failure(ErrorCode::CANCELLED))
            },
        )
        .await
}

#[cfg(windows)]
#[tauri::command]
pub fn cancel_market_preparation(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, State>,
    preparation_id: String,
) -> Result<(), Error> {
    authorize_window(window.label())?;
    state.cancel_replay(ReplayTask::Preparation(preparation_id));
    Ok(())
}

#[cfg(windows)]
#[tauri::command]
pub async fn execute_market_subscription(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, State>,
    subscription_id: String,
    payload: Value,
    progress: Channel<Value>,
) -> Result<(), Error> {
    authorize_window(window.label())?;
    let (_slot, receive) = state.begin_replay(ReplayTask::Subscription(subscription_id))?;
    let publish = |frame| {
        progress
            .send(frame)
            .map_err(|_| failure(ErrorCode::CANCELLED))
    };
    publish(json!({"started": true}))?;
    let result = run_subscription(
        development_config(),
        Actor::User,
        payload,
        async {
            let _ = receive.await;
        },
        &publish,
    )
    .await;
    // Send the terminal frame on the progress channel as well, so the
    // WebView observes it after the final bar even if invoke resolves earlier.
    publish(match result {
        Ok(result) => json!({"ok": true, "result": result}),
        Err(error) => json!({"ok": false, "error": error}),
    })
}

#[cfg(windows)]
#[tauri::command]
pub fn cancel_market_subscription(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, State>,
    subscription_id: String,
) -> Result<(), Error> {
    authorize_window(window.label())?;
    state.cancel_replay(ReplayTask::Subscription(subscription_id));
    Ok(())
}

#[cfg(test)]
#[path = "bridge_tests.rs"]
mod tests;
