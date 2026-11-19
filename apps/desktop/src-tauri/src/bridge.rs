use std::{
    future::Future,
    path::{Path, PathBuf},
    sync::Mutex as SyncMutex,
    time::Duration,
};

use scopehub_application::{
    Actor, LOAD_LOCAL_IMPORT_MAPPING, LOCAL_WORKSPACE_OPERATIONS, MarketLifecycle, MarketPort,
    MarketRegistry, PREVIEW_LOCAL_IMPORT, PUBLISH_LOCAL_IMPORT, READ_LOCAL_IMPORT_REVISION,
    REVOKE_LOCAL_IMPORT_REVISION, SAVE_LOCAL_IMPORT_MAPPING, failure,
};
use scopehub_domain::protocol::{Error, ErrorCode};
use scopehub_platform::local_folder::LocalFolderHost;
use scopehub_worker_host::market::{MarketWorker, WorkerConfig};
use serde_json::Value;
#[cfg(windows)]
use tauri::ipc::Channel;
use tokio::sync::{Mutex, MutexGuard, oneshot};
#[cfg(windows)]
use {scopehub_worker_host::market::run_subscription, serde_json::json};

use crate::{local_import::LocalImport, workspace::WorkspaceBridge};

pub struct State {
    reads: Mutex<MarketRegistry<MarketWorker>>,
    local_import: LocalImport,
    workspace: WorkspaceBridge,
    replay: Mutex<()>,
    cancellation: SyncMutex<Cancellation>,
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
    state_with_local_profile(None)
}

/// Only the native desktop launcher supplies the trusted local app-data root.
pub fn state_with_local_profile(app_local_data: Option<&Path>) -> State {
    state_with_profile(app_local_data, false)
}

/// The native launcher explicitly opts this session into private workspace actions.
pub fn state_with_development_workspace(app_local_data: Option<&Path>) -> State {
    state_with_profile(app_local_data, true)
}

fn state_with_profile(app_local_data: Option<&Path>, enable_workspace: bool) -> State {
    State {
        reads: Mutex::new(MarketRegistry::new(MarketWorker::new(development_config()))),
        local_import: LocalImport::new(development_config()),
        workspace: WorkspaceBridge::development(
            app_local_data.filter(|_| cfg!(debug_assertions)),
            enable_workspace && cfg!(debug_assertions),
        ),
        replay: Mutex::new(()),
        cancellation: SyncMutex::default(),
    }
}

impl State {
    async fn execute(
        &self,
        window_label: &str,
        operation: Option<&str>,
        payload: Value,
        request_id: Option<&str>,
    ) -> Result<Value, Error> {
        authorize_window(window_label)?;
        match operation {
            None => self.workspace.dispatch(operation, payload).await,
            Some(name) if LOCAL_WORKSPACE_OPERATIONS.contains(&name) => {
                self.workspace.dispatch(operation, payload).await
            }
            Some(name) => {
                self.execute_local_operation(name, payload, request_id)
                    .await
            }
        }
    }

    /// Native launcher/picker entry. Webview operation payloads have no access
    /// to directory adoption or this host instance.
    pub fn local_folders(&self) -> &LocalFolderHost {
        &self.local_import.folders
    }

    pub fn cancel_local_import(&self, request_id: &str) {
        self.local_import.cancel(request_id);
    }

    /// The Rust host supplies the picker. WebView callers receive catalog metadata.
    pub async fn choose_local_csv_folder<F>(
        &self,
        pick: impl FnOnce() -> F,
    ) -> Result<Option<Value>, Error>
    where
        F: Future<Output = Option<PathBuf>>,
    {
        self.local_import.choose_folder(pick).await
    }

    pub async fn execute_local_operation(
        &self,
        operation: &str,
        payload: Value,
        request_id: Option<&str>,
    ) -> Result<Value, Error> {
        match operation {
            PREVIEW_LOCAL_IMPORT => return self.local_import.preview(payload, request_id).await,
            LOAD_LOCAL_IMPORT_MAPPING => {
                return self
                    .local_import
                    .load_mapping(&self.workspace, payload, request_id)
                    .await;
            }
            SAVE_LOCAL_IMPORT_MAPPING => {
                return self
                    .local_import
                    .save_mapping(&self.workspace, payload, request_id)
                    .await;
            }
            PUBLISH_LOCAL_IMPORT => {
                return self
                    .local_import
                    .publish(&self.workspace, payload, request_id)
                    .await;
            }
            READ_LOCAL_IMPORT_REVISION => {
                self.local_import.reject_reused_request(request_id)?;
                return self.workspace.read_local_revision(payload).await;
            }
            REVOKE_LOCAL_IMPORT_REVISION => {
                self.local_import.reject_reused_request(request_id)?;
                return self.workspace.revoke_local_revision(payload).await;
            }
            _ => {}
        }
        // Do not create an unbounded queue of requests in the native process.
        let mut registry = self
            .reads
            .try_lock()
            .map_err(|_| failure(ErrorCode::BUSY))?;
        registry.execute(Actor::LocalUser, operation, payload).await
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
        self.local_import.shutdown().await;
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
    request_id: Option<String>,
) -> Result<Value, Error> {
    state
        .execute(
            window.label(),
            operation.as_deref(),
            payload,
            request_id.as_deref(),
        )
        .await
}

#[cfg(windows)]
#[tauri::command]
pub fn cancel_local_import_preview(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, State>,
    request_id: String,
) -> Result<(), Error> {
    authorize_window(window.label())?;
    state.cancel_local_import(&request_id);
    Ok(())
}

#[cfg(windows)]
#[tauri::command]
pub async fn choose_local_csv_folder(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, State>,
) -> Result<Option<Value>, Error> {
    authorize_window(window.label())?;
    state
        .choose_local_csv_folder(|| async {
            rfd::AsyncFileDialog::new()
                .set_title("Choose local CSV folder")
                .set_parent(&window)
                .pick_folder()
                .await
                .map(|folder| folder.path().to_path_buf())
        })
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
