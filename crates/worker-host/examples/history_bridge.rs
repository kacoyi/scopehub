//! Test-only stdio bridge. Browser tests exercise the production registry/worker
//! without claiming native WebView or packaged desktop execution.
use std::io::{BufRead, Read, Write};

use scopehub_application::{Actor, MarketRegistry, failure};
use scopehub_domain::protocol::ErrorCode;
use scopehub_worker_host::{
    market::{MarketWorker, WorkerConfig},
    max_message_bytes,
};
use serde::Deserialize;
use serde_json::{Value, json};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Input {
    operation: String,
    payload: Value,
}

#[tokio::main]
async fn main() {
    let root = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..");
    let mut registry =
        MarketRegistry::new(MarketWorker::new(Some(WorkerConfig::development(&root))));
    let mut input = std::io::stdin().lock();
    loop {
        let mut line = String::new();
        let read = (&mut input)
            .take(max_message_bytes() as u64 + 1)
            .read_line(&mut line);
        if matches!(read, Ok(0)) {
            break;
        }
        if read.is_err() || line.len() > max_message_bytes() || !line.ends_with('\n') {
            break;
        }
        let result = match serde_json::from_str::<Input>(&line) {
            Ok(request) => {
                registry
                    .execute(Actor::User, &request.operation, request.payload)
                    .await
            }
            Err(_) => Err(failure(ErrorCode::INVALIDARGUMENT)),
        };
        let output = match result {
            Ok(result) => json!({"ok":true, "result":result}),
            Err(error) => json!({"ok":false, "error":error}),
        };
        println!("{output}");
        if std::io::stdout().flush().is_err() {
            break;
        }
    }
    registry.port_mut().shutdown().await;
}
