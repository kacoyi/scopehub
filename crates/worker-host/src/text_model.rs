//! One-shot native transport for an already authorized text turn.

use std::{future::Future, process::Stdio, time::Duration};

use scopehub_domain::protocol::{Error, ErrorCode};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use tokio::{
    io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader},
    process::Child,
    runtime::Handle,
    sync::OwnedSemaphorePermit,
    time::{Instant, timeout_at},
};

use crate::{
    POLICY,
    cleanup::{PendingCleanup, reclaim},
    market::{WorkerConfig, worker_slot},
};

mod carrier;
mod openai;
pub use carrier::run_child;
pub use openai::complete_openai;

pub const TURN_DEADLINE: Duration = Duration::from_secs(45);
const MAX_FRAME_BYTES: usize = 512 * 1024;
const MAX_TEXT_BYTES: usize = 64 * 1024;
const MAX_MODEL_BYTES: usize = 256;

#[derive(Serialize)]
struct RequestFrame<'a> {
    destination: &'a str,
    request: &'a Value,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct TextReply {
    model: String,
    text: String,
}

/// The application supplies the destination and final projection. The child
/// receives those bytes only and has no workspace handle or operation access.
pub async fn complete(
    config: &WorkerConfig,
    destination: &str,
    request: &Value,
    cancelled: impl Future<Output = ()>,
) -> Result<Value, Error> {
    let deadline = Instant::now() + config.deadline;
    tokio::pin!(cancelled);
    let mut process = Process::default();
    let outcome = tokio::select! {
        biased;
        () = &mut cancelled => Err(error(ErrorCode::CANCELLED)),
        result = timeout_at(deadline, async {
            let mut frame = serde_json::to_vec(&RequestFrame { destination, request })
                .map_err(|_| error(ErrorCode::INVALIDARGUMENT))?;
            if frame.len() >= MAX_FRAME_BYTES {
                return Err(error(ErrorCode::RESOURCELIMIT));
            }
            frame.push(b'\n');
            if Instant::now() >= deadline {
                return Err(error(ErrorCode::TIMEOUT));
            }
            let child = process.start(config)?;
            exchange(child, &frame).await
        }) => result.unwrap_or_else(|_| Err(error(ErrorCode::TIMEOUT))),
    };
    if let Some(mut cleanup) = process.cleanup() {
        // Keep ownership in the background reaper if its grace expires.
        let _ = cleanup.wait().await;
    }
    if outcome.is_ok() {
        tokio::select! {
            biased;
            () = &mut cancelled => return Err(error(ErrorCode::CANCELLED)),
            () = std::future::ready(()) => {}
        }
    }
    outcome
}

async fn exchange(child: &mut Child, frame: &[u8]) -> Result<Value, Error> {
    let mut stdin = child.stdin.take().ok_or_else(worker_exited)?;
    stdin.write_all(frame).await.map_err(|_| worker_exited())?;
    // The private Python worker checks that stdin contains exactly one frame.
    drop(stdin);
    let stdout = child.stdout.take().ok_or_else(worker_exited)?;
    let mut reader = BufReader::new(stdout).take((MAX_FRAME_BYTES + 1) as u64);
    let mut bytes = Vec::new();
    let count = reader
        .read_until(b'\n', &mut bytes)
        .await
        .map_err(|_| worker_exited())?;
    if count > MAX_FRAME_BYTES {
        return Err(error(ErrorCode::RESOURCELIMIT));
    }
    if count > 0
        && (!bytes.ends_with(b"\n")
            || reader.read(&mut [0]).await.map_err(|_| worker_exited())? != 0)
    {
        return Err(error(ErrorCode::OUTPUTINVALID));
    }
    let status = child.wait().await.map_err(|_| worker_exited())?;
    if !status.success() {
        return Err(error(match (status.code(), bytes.is_empty()) {
            (Some(20), true) => ErrorCode::INVALIDARGUMENT,
            (Some(21), true) => ErrorCode::DATAUNAVAILABLE,
            (Some(22), true) => ErrorCode::OUTPUTINVALID,
            (Some(23), true) => ErrorCode::RESOURCELIMIT,
            (Some(24), true) => ErrorCode::TIMEOUT,
            _ => ErrorCode::WORKEREXITED,
        }));
    }
    let reply: TextReply =
        serde_json::from_slice(&bytes).map_err(|_| error(ErrorCode::OUTPUTINVALID))?;
    if reply.model.is_empty()
        || reply.model.len() > MAX_MODEL_BYTES
        || reply
            .model
            .chars()
            .any(|char| char.is_whitespace() || char.is_control())
        || reply.text.trim().is_empty()
        || reply.text.len() > MAX_TEXT_BYTES
    {
        return Err(error(ErrorCode::OUTPUTINVALID));
    }
    Ok(json!({"model": reply.model, "text": reply.text}))
}

#[derive(Default)]
struct Process(Option<Run>);

struct Run {
    child: Child,
    permit: OwnedSemaphorePermit,
    runtime: Handle,
}

impl Process {
    fn start(&mut self, config: &WorkerConfig) -> Result<&mut Child, Error> {
        if !config.python.is_absolute() || !config.root.is_absolute() {
            return Err(error(ErrorCode::INVALIDARGUMENT));
        }
        let permit = worker_slot().map_err(|_| error(ErrorCode::BUSY))?;
        let child = config
            .python_command()
            .args([
                "-P",
                "-s",
                "-B",
                "-X",
                "utf8",
                "-m",
                "worker_runtime.text_model",
            ])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .kill_on_drop(true)
            .spawn()
            .map_err(|_| worker_exited())?;
        Ok(&mut self
            .0
            .insert(Run {
                child,
                permit,
                runtime: Handle::current(),
            })
            .child)
    }

    fn cleanup(&mut self) -> Option<PendingCleanup> {
        let Run {
            child,
            permit,
            runtime,
        } = self.0.take()?;
        child.id()?;
        let grace =
            Duration::from_millis(POLICY["cancel_grace_ms"].as_u64().expect("cancel grace"));
        let _context = runtime.enter();
        Some(PendingCleanup::start(
            async move {
                reclaim(child, grace).await;
                drop(permit);
            },
            grace,
        ))
    }
}

impl Drop for Process {
    fn drop(&mut self) {
        let _ = self.cleanup();
    }
}

fn worker_exited() -> Error {
    error(ErrorCode::WORKEREXITED)
}

fn error(code: ErrorCode) -> Error {
    let message = match code {
        ErrorCode::INVALIDARGUMENT => "The selected text model request is invalid.",
        ErrorCode::DATAUNAVAILABLE => "The selected text model route is unavailable.",
        ErrorCode::PERMISSIONDENIED => "OpenAI rejected the credential or account access.",
        ErrorCode::OUTPUTINVALID => "The selected text model returned an invalid reply.",
        ErrorCode::RESOURCELIMIT => "The text model turn exceeded its size limit.",
        ErrorCode::TIMEOUT => "The text model did not reply before the deadline.",
        ErrorCode::CANCELLED => "The text model turn was cancelled.",
        ErrorCode::BUSY => "The text model connector is busy.",
        _ => "The text model connector is unavailable.",
    };
    Error {
        retryable: matches!(
            code,
            ErrorCode::DATAUNAVAILABLE
                | ErrorCode::TIMEOUT
                | ErrorCode::WORKEREXITED
                | ErrorCode::BUSY
        ),
        code,
        message: message.into(),
        details: Default::default(),
    }
}
