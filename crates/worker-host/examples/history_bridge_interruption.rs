//! Opt-in preview bridge for the owned synthetic interruption journey.

#[path = "common/history_bridge.rs"]
mod bridge;
use scopehub_worker_host::market::{MarketWorker, WorkerConfig};
use uuid::Uuid;

#[tokio::main]
async fn main() {
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    let witness = match args.as_slice() {
        [flag, token] if flag == "--test-market-interruption" => {
            Uuid::parse_str(token.to_str().expect("UTF-8 interruption witness"))
                .expect("interruption witness")
        }
        _ => panic!("Invalid interruption bridge arguments"),
    };
    let root = bridge::workspace_root();
    let script = root.join("tests/ubuntu-preview/market_interruption_worker.py");
    let worker =
        MarketWorker::preview_interruption(WorkerConfig::development(&root), script, witness);
    bridge::run(&root, worker).await;
}
