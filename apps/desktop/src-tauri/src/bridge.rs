use scopehub_application::{Actor, MarketRegistry, failure};
use scopehub_domain::protocol::{Error, ErrorCode};
use scopehub_worker_host::market::MarketWorker;
use serde_json::Value;
use tokio::sync::Mutex;

pub type State = Mutex<MarketRegistry<MarketWorker>>;

pub fn state() -> State {
    // Never ship a release executable that searches for development Python.
    // Bundled worker discovery and clean-machine acceptance belong to M5.
    #[cfg(debug_assertions)]
    let config = Some(scopehub_worker_host::market::WorkerConfig::development(
        &std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../.."),
    ));
    #[cfg(not(debug_assertions))]
    let config = None;
    Mutex::new(MarketRegistry::new(MarketWorker::new(config)))
}

#[tauri::command]
pub async fn execute_operation(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, State>,
    operation: String,
    payload: Value,
) -> Result<Value, Error> {
    if window.label() != "main" {
        return Err(failure(ErrorCode::PERMISSIONDENIED));
    }
    // Do not create an unbounded queue of requests in the native process.
    let mut registry = state
        .try_lock()
        .map_err(|_| failure(ErrorCode::RESOURCELIMIT))?;
    registry.execute(Actor::User, &operation, payload).await
}
