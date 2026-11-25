//! Local development stdio bridge for the browser preview and chart checks.
//! Requests use the desktop registry and development market worker.
use std::io::{BufRead, Read, Write};

use scopehub_application::{
    Actor, MarketRegistry, READ_INDICATOR_SAMPLE_HISTORY, READ_SAMPLE_EVIDENCE_HISTORY, SUBSCRIBE,
    failure,
};
use scopehub_domain::protocol::ErrorCode;
use scopehub_worker_host::{
    market::{MarketWorker, WorkerConfig},
    max_message_bytes,
};
use serde::Deserialize;
use serde_json::{Value, json};
use uuid::Uuid;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Input {
    operation: String,
    payload: Value,
}

fn write_frame(value: Value) -> std::io::Result<()> {
    let mut output = std::io::stdout().lock();
    writeln!(output, "{value}")?;
    output.flush()
}

fn reply(result: Result<Value, scopehub_domain::protocol::Error>) -> Value {
    match result {
        Ok(result) => json!({"ok":true, "result":result}),
        Err(error) => json!({"ok":false, "error":error}),
    }
}

#[tokio::main]
async fn main() {
    let root = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..");
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    let witness = match args.as_slice() {
        [] => None,
        [flag, token] if flag == "--test-market-interruption" => Some(
            Uuid::parse_str(token.to_str().expect("UTF-8 interruption witness"))
                .expect("interruption witness"),
        ),
        _ => panic!("Invalid preview bridge arguments"),
    };
    let mut registry =
        MarketRegistry::new(MarketWorker::new(Some(WorkerConfig::development(&root))));
    loop {
        let mut line = String::new();
        let read = std::io::stdin()
            .lock()
            .take(max_message_bytes() as u64 + 1)
            .read_line(&mut line);
        if matches!(read, Ok(0)) {
            break;
        }
        if read.is_err() || line.len() > max_message_bytes() || !line.ends_with('\n') {
            break;
        }
        let result = match serde_json::from_str::<Input>(&line) {
            Ok(request) if request.operation == SUBSCRIBE => {
                // A streaming request owns this bridge until completion. The
                // parent closes stdin on disconnect to cancel the owned worker.
                let (signal, cancelled) = tokio::sync::oneshot::channel();
                std::thread::spawn(move || {
                    let _ = std::io::stdin().read(&mut [0u8; 1]);
                    let _ = signal.send(());
                });
                let config = WorkerConfig::development(&root);
                let worker = match witness {
                    Some(witness) => MarketWorker::preview_interruption(config, witness),
                    None => MarketWorker::new(Some(config)),
                };
                let result = worker
                    .run_subscription(
                        Actor::User,
                        request.payload,
                        async {
                            let _ = cancelled.await;
                        },
                        |frame| write_frame(frame).map_err(|_| failure(ErrorCode::CANCELLED)),
                    )
                    .await;
                let _ = write_frame(reply(result));
                break;
            }
            Ok(request) if request.operation == READ_INDICATOR_SAMPLE_HISTORY => {
                registry
                    .read_sample_indicator_history(request.payload)
                    .await
            }
            Ok(request) if request.operation == READ_SAMPLE_EVIDENCE_HISTORY => {
                registry
                    .read_sample_evidence_history(&request.payload)
                    .await
            }
            Ok(request) => {
                registry
                    .execute(Actor::User, &request.operation, request.payload)
                    .await
            }
            Err(_) => Err(failure(ErrorCode::INVALIDARGUMENT)),
        };
        if write_frame(reply(result)).is_err() {
            break;
        }
    }
    let _ = registry.shutdown().await;
}
