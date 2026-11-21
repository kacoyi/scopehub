use std::{
    io::{self, BufRead, Read, Write},
    process::ExitCode,
};

use scopehub_domain::protocol::{Error, ErrorCode};
use serde::Deserialize;
use serde_json::{Value, json};
use tokio::{sync::oneshot, time::timeout};

use super::{MAX_FRAME_BYTES, complete, complete_openai, error, worker_exited};
use crate::market::WorkerConfig;

#[derive(Deserialize)]
#[serde(untagged)]
enum Input {
    Ollama(OllamaInput),
    OpenAi(OpenAiInput),
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct OllamaInput {
    destination: String,
    request: Value,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct OpenAiInput {
    #[serde(rename = "provider")]
    _provider: OpenAiProvider,
    request: Value,
    credential: String,
}

#[derive(Deserialize)]
enum OpenAiProvider {
    #[serde(rename = "openai_responses")]
    Responses,
}

/// The preview host keeps stdin open after writing the frame. Closing it
/// signals cancellation or parent loss, allowing the child to stop transport.
pub fn run_child(config: &WorkerConfig) -> ExitCode {
    let result = run(config).unwrap_or_else(|failure| json!({"error": failure}));
    let mut output = io::stdout().lock();
    if serde_json::to_writer(&mut output, &result).is_ok()
        && output
            .write_all(b"\n")
            .and_then(|()| output.flush())
            .is_ok()
    {
        ExitCode::SUCCESS
    } else {
        ExitCode::FAILURE
    }
}

fn run(config: &WorkerConfig) -> Result<Value, Error> {
    let (input, frame) = oneshot::channel();
    let (cancel, eof) = oneshot::channel();
    std::thread::Builder::new()
        .name("text-model-parent-input".into())
        .spawn(move || {
            let mut stdin = io::stdin().lock();
            if input.send(read_request(&mut stdin)).is_ok() {
                let _ = stdin.read(&mut [0]);
                let _ = cancel.send(());
            }
        })
        .map_err(|_| worker_exited())?;
    tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(|_| worker_exited())?
        .block_on(async {
            let input = timeout(config.deadline, frame)
                .await
                .map_err(|_| error(ErrorCode::TIMEOUT))?
                .map_err(|_| worker_exited())??;
            let cancelled = async {
                let _ = eof.await;
            };
            match input {
                Input::Ollama(input) => {
                    complete(config, &input.destination, &input.request, cancelled).await
                }
                Input::OpenAi(input) => {
                    complete_openai(&input.request, &input.credential, cancelled).await
                }
            }
        })
}

fn read_request(input: &mut impl BufRead) -> Result<Input, Error> {
    let mut frame = Vec::new();
    input
        .take((MAX_FRAME_BYTES + 1) as u64)
        .read_until(b'\n', &mut frame)
        .map_err(|_| error(ErrorCode::INVALIDARGUMENT))?;
    if frame.len() > MAX_FRAME_BYTES {
        return Err(error(ErrorCode::RESOURCELIMIT));
    }
    if !frame.ends_with(b"\n") {
        return Err(error(ErrorCode::CANCELLED));
    }
    serde_json::from_slice(&frame).map_err(|_| error(ErrorCode::INVALIDARGUMENT))
}
