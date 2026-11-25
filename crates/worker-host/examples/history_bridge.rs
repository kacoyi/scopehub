//! Ordinary local development bridge for browser preview and chart checks.

#[path = "common/history_bridge.rs"]
mod bridge;
use scopehub_worker_host::market::{MarketWorker, WorkerConfig};

#[tokio::main]
async fn main() {
    assert_eq!(
        std::env::args_os().len(),
        1,
        "Invalid preview bridge arguments"
    );
    let root = bridge::workspace_root();
    let worker = MarketWorker::new(Some(WorkerConfig::development(&root)));
    bridge::run(&root, worker).await;
}
