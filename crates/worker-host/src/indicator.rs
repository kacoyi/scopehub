//! One-shot native transport for an already admitted and confirmed computation.

use std::{
    collections::HashSet,
    fs, future,
    io::{self, Write},
    path::PathBuf,
    process::{ExitStatus, Stdio},
    time::Duration,
};

use scopehub_domain::protocol::{
    DraftSource11, Error, ErrorCode, IndicatorArtifact11, IndicatorBarsInput11, IndicatorCompute11,
    IndicatorComputed11, IndicatorResult, Message,
};
use serde::{Deserialize, Serialize};
use serde_json::value::RawValue;
use sha2::{Digest, Sha256};
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader},
    process::{Child, ChildStdin, Command},
    runtime::Handle,
    sync::OwnedSemaphorePermit,
    time::{Instant, timeout_at},
};
use uuid::Uuid;

use crate::{
    POLICY, ProtocolError,
    cleanup::PendingCleanup,
    decode_line,
    market::{WorkerConfig, worker_slot},
    max_message_bytes, negotiate_worker, read_frame,
};

mod carrier;
pub use carrier::run_child;

pub const ENGINE: &str = "scopehub-trusted-python-1";
const COMPUTE: &str = "indicator.compute";
const MAX_BARS: usize = 100_000;

/// The caller owns authorization and supplies the immutable approved input.
/// Code runs with the interpreter's normal filesystem and network authority.
/// Dropping this future also starts cleanup. Signal cancellation and await it
/// when the caller needs to observe the terminal outcome after reaping.
pub async fn compute(
    config: &WorkerConfig,
    request: &IndicatorCompute11,
    cancelled: impl Future<Output = ()>,
) -> Result<IndicatorComputed11, Error> {
    let deadline = Instant::now() + config.deadline;
    let mut process = Process::default();
    tokio::pin!(cancelled);
    tokio::select! {
        biased;
        () = &mut cancelled => return Err(error(ErrorCode::CANCELLED)),
        () = future::ready(()) => {}
    }
    let frame = prepare(request)?;
    let outcome = tokio::select! {
        biased;
        () = &mut cancelled => Err(error(ErrorCode::CANCELLED)),
        outcome = timeout_at(deadline, async {
            if Instant::now() >= deadline {
                return Err(error(ErrorCode::TIMEOUT));
            }
            let permit = worker_slot().map_err(|_| error(ErrorCode::BUSY))?;
            let scratch = Scratch::new()?;
            let mut child = command(config, &scratch)?.spawn().map_err(worker_io)?;
            let input = child.stdin.take().expect("piped stdin");
            let run = process.0.insert(Run {
                child,
                input,
                scratch,
                permit,
                runtime: Handle::current(),
            });
            exchange(&mut run.child, &mut run.input, request, &frame).await
        }) => outcome.unwrap_or_else(|_| Err(error(ErrorCode::TIMEOUT))),
    };
    if let Some(mut cleanup) = process.cleanup() {
        // Retain ownership in the reaper if the cleanup budget expires.
        let reaped = cleanup.wait().await;
        if outcome.is_ok() {
            reaped.map_err(|_| error(ErrorCode::WORKEREXITED))?;
        }
    }
    if outcome.is_ok() {
        tokio::select! {
            biased;
            () = &mut cancelled => return Err(error(ErrorCode::CANCELLED)),
            () = future::ready(()) => {}
        }
        if Instant::now() >= deadline {
            return Err(error(ErrorCode::TIMEOUT));
        }
    }
    outcome
}

fn prepare(request: &IndicatorCompute11) -> Result<Vec<u8>, Error> {
    let DraftSource11::PythonDraft11(source) = &request.source else {
        return Err(error(ErrorCode::INVALIDARGUMENT));
    };
    let IndicatorBarsInput11::InlineIndicatorBars11(input) = &request.input else {
        return Err(error(ErrorCode::UNSUPPORTEDCAPABILITY));
    };
    let bars = &input.bars;
    let snapshot = &request.snapshot;
    if bars.len() > MAX_BARS {
        return Err(error(ErrorCode::RESOURCELIMIT));
    }
    let warmup =
        usize::try_from(snapshot.warmup_bars).map_err(|_| error(ErrorCode::INVALIDARGUMENT))?;
    if warmup >= bars.len()
        || snapshot.from_ms >= snapshot.to_ms
        || bars.partition_point(|bar| bar.time_ms < snapshot.from_ms) != warmup
        || bars
            .windows(2)
            .any(|pair| pair[0].time_ms >= pair[1].time_ms)
        || bars
            .last()
            .is_some_and(|bar| bar.time_ms >= snapshot.to_ms || bar.time_ms > snapshot.as_of_ms)
        || bars.iter().any(|bar| {
            [bar.open, bar.high, bar.low, bar.close]
                .into_iter()
                .chain(bar.volume)
                .any(|value| !value.is_finite())
        })
        || digest(source.source.as_bytes()) != request.code_hash
    {
        return Err(error(ErrorCode::INVALIDARGUMENT));
    }
    let frame = encode_frame(request)?;
    if !matches!(decode_line(&frame), Ok(Message::IndicatorCompute11(_)))
        || canonical_hash(&request.resolved_params)? != request.params_hash
    {
        return Err(error(ErrorCode::INVALIDARGUMENT));
    }
    Ok(frame)
}

fn command(config: &WorkerConfig, scratch: &Scratch) -> Result<Command, Error> {
    if !config.python.is_absolute() || !config.root.is_absolute() {
        return Err(error(ErrorCode::INVALIDARGUMENT));
    }
    #[cfg(not(windows))]
    if !crate::python::native_development_interpreter(&config.python) {
        return Err(error(ErrorCode::WORKEREXITED));
    }
    let python = config.python_command();
    #[cfg(windows)]
    let python = crate::python::direct_interpreter(config, &python)
        .ok_or_else(|| error(ErrorCode::WORKEREXITED))?;
    let mut python = python;
    python
        .current_dir(&scratch.0)
        .env("TEMP", &scratch.0)
        .env("TMP", &scratch.0)
        .env("TMPDIR", &scratch.0)
        .args([
            "-P",
            "-s",
            "-B",
            "-X",
            "utf8",
            "-m",
            "worker_runtime.indicator_guard",
        ])
        .arg(max_message_bytes().to_string())
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    Ok(python)
}

async fn exchange(
    child: &mut Child,
    input: &mut ChildStdin,
    request: &IndicatorCompute11,
    frame: &[u8],
) -> Result<IndicatorComputed11, Error> {
    let mut output = BufReader::new(child.stdout.take().expect("piped stdout"));
    let hello = read_frame(&mut output).await.map_err(protocol_failure)?;
    let negotiated = negotiate_worker(&hello, "indicator", &[COMPUTE]).map_err(protocol_failure)?;
    if negotiated.version != "1.1"
        || negotiated.capabilities != [COMPUTE]
        || !matches!(
            decode_line(&hello),
            Ok(Message::WorkerHello11(hello)) if hello.worker_version == ENGINE
        )
    {
        return Err(error(ErrorCode::PROTOCOLMISMATCH));
    }
    input.write_all(frame).await.map_err(worker_io)?;

    let terminal = match read_frame(&mut output).await {
        Ok(frame) => decode_response(&frame, request)?,
        Err(ProtocolError::WorkerExited) => {
            return Err(exit_failure(child.wait().await.map_err(worker_io)?));
        }
        Err(failure) => return Err(protocol_failure(failure)),
    };
    if !output.fill_buf().await.map_err(worker_io)?.is_empty() {
        return Err(error(ErrorCode::OUTPUTINVALID));
    }
    if !child.wait().await.map_err(worker_io)?.success() {
        return Err(error(ErrorCode::WORKEREXITED));
    }
    Ok(terminal)
}

#[derive(Deserialize)]
struct RawResponse<'a> {
    #[serde(borrow)]
    artifact: RawArtifact<'a>,
}

#[derive(Deserialize)]
struct RawArtifact<'a> {
    #[serde(borrow)]
    result: &'a RawValue,
}

fn decode_response(
    frame: &[u8],
    request: &IndicatorCompute11,
) -> Result<IndicatorComputed11, Error> {
    let invalid = || error(ErrorCode::OUTPUTINVALID);
    let Message::IndicatorComputed11(mut reply) = decode_line(frame).map_err(|_| invalid())? else {
        return Err(invalid());
    };
    let IndicatorArtifact11::InlineIndicatorArtifact11(artifact) = &mut reply.artifact else {
        return Err(invalid());
    };
    if reply.request_id != request.request_id
        || reply.task_id != request.task_id
        || reply.draft_id != request.draft_id
        || reply.snapshot_hash != request.snapshot.content_hash
        || reply.message_id == request.message_id
    {
        return Err(invalid());
    }
    let raw: RawResponse<'_> = serde_json::from_slice(frame).map_err(|_| invalid())?;
    let result_bytes = raw.artifact.result.get();
    if digest(result_bytes.as_bytes()) != artifact.result_hash {
        return Err(invalid());
    }
    // Prefer exact floating-point values when the frame uses typed integers
    // for timestamps and styles. Shared decoding also accepts 1.0 for those.
    if let Ok(exact) = serde_json::from_str(result_bytes) {
        artifact.result = exact;
    }
    if !valid_result(&artifact.result, request) {
        return Err(invalid());
    }
    let result = serde_json::to_value(&artifact.result).map_err(|_| invalid())?;
    artifact.result_hash = canonical_hash(&result)?;
    // Float spellings and key ordering can differ from the transport result.
    encode_frame(reply.as_ref())?;
    Ok(*reply)
}

fn valid_result(result: &IndicatorResult, request: &IndicatorCompute11) -> bool {
    let IndicatorBarsInput11::InlineIndicatorBars11(input) = &request.input else {
        return false;
    };
    let snapshot = &request.snapshot;
    let visible = &input.bars[snapshot.warmup_bars as usize..];
    let visible_time = |time| {
        visible
            .binary_search_by_key(&time, |bar| bar.time_ms)
            .is_ok()
    };
    let mut ids = HashSet::new();
    let provenance = &result.provenance;
    provenance.code_hash == request.code_hash
        && provenance.params_hash == request.params_hash
        && provenance.dataset_id == snapshot.dataset_id
        && provenance.dataset_revision == snapshot.dataset_revision
        && provenance.engine_version == ENGINE
        && result.series.iter().all(|series| {
            ids.insert(&series.id)
                && series
                    .points
                    .windows(2)
                    .all(|pair| pair[0].time_ms < pair[1].time_ms)
                && series.points.iter().all(|point| {
                    visible_time(point.time_ms) && point.value.is_none_or(f64::is_finite)
                })
        })
        && result
            .markers
            .iter()
            .all(|marker| visible_time(marker.time_ms))
        && result.regions.iter().all(|region| {
            region.from_ms < region.to_ms
                && region.from_ms >= snapshot.from_ms
                && region.to_ms <= snapshot.to_ms
        })
}

struct Scratch(PathBuf);

impl Scratch {
    fn new() -> Result<Self, Error> {
        let directory = std::path::absolute(std::env::temp_dir())
            .map_err(worker_io)?
            .join(format!("scopehub-indicator-{}", Uuid::new_v4()));
        let builder = fs::DirBuilder::new();
        #[cfg(unix)]
        let builder = {
            use std::os::unix::fs::DirBuilderExt;
            let mut builder = builder;
            builder.mode(0o700);
            builder
        };
        builder.create(&directory).map_err(worker_io)?;
        Ok(Self(directory))
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

#[derive(Default)]
struct Process(Option<Run>);

struct Run {
    child: Child,
    input: ChildStdin,
    scratch: Scratch,
    permit: OwnedSemaphorePermit,
    runtime: Handle,
}

impl Process {
    fn cleanup(&mut self) -> Option<PendingCleanup> {
        let Run {
            mut child,
            input,
            scratch,
            permit,
            runtime,
        } = self.0.take()?;
        // The supervisor owns the process tree and can reclaim it even
        // when the native caller drops the computation or exits.
        drop(input);
        let grace =
            Duration::from_millis(POLICY["cancel_grace_ms"].as_u64().expect("cancel grace"));
        let _context = runtime.enter();
        Some(PendingCleanup::start(
            async move {
                loop {
                    if child.wait().await.is_ok() {
                        break;
                    }
                    tokio::time::sleep(grace).await;
                }
                drop((scratch, permit));
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

fn encode_frame(message: &impl Serialize) -> Result<Vec<u8>, Error> {
    let mut frame = BoundedFrame(Vec::new());
    serde_json::to_writer(&mut frame, message).map_err(|failure| {
        if failure.io_error_kind() == Some(io::ErrorKind::FileTooLarge) {
            error(ErrorCode::RESOURCELIMIT)
        } else {
            error(ErrorCode::INVALIDARGUMENT)
        }
    })?;
    frame.0.push(b'\n');
    Ok(frame.0)
}

struct BoundedFrame(Vec<u8>);

impl Write for BoundedFrame {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        if bytes.len() > max_message_bytes() - 1 - self.0.len() {
            return Err(io::ErrorKind::FileTooLarge.into());
        }
        self.0.extend_from_slice(bytes);
        Ok(bytes.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

fn canonical_hash(value: &impl Serialize) -> Result<String, Error> {
    let value = serde_json::to_value(value).map_err(|_| error(ErrorCode::INVALIDARGUMENT))?;
    serde_json::to_vec(&value)
        .map(|bytes| digest(&bytes))
        .map_err(|_| error(ErrorCode::INVALIDARGUMENT))
}

fn digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn protocol_failure(failure: ProtocolError) -> Error {
    error(match failure {
        ProtocolError::ResourceLimit => ErrorCode::RESOURCELIMIT,
        ProtocolError::InvalidMessage => ErrorCode::OUTPUTINVALID,
        ProtocolError::IncompatibleWorker => ErrorCode::PROTOCOLMISMATCH,
        ProtocolError::WorkerExited | ProtocolError::Io => ErrorCode::WORKEREXITED,
        ProtocolError::Timeout => ErrorCode::TIMEOUT,
    })
}

fn exit_failure(status: ExitStatus) -> Error {
    error(match status.code() {
        Some(20) => ErrorCode::INVALIDARGUMENT,
        Some(21) => ErrorCode::INTERNALERROR,
        Some(22) => ErrorCode::OUTPUTINVALID,
        Some(23) => ErrorCode::RESOURCELIMIT,
        _ => ErrorCode::WORKEREXITED,
    })
}

fn worker_io(_: io::Error) -> Error {
    error(ErrorCode::WORKEREXITED)
}

fn error(code: ErrorCode) -> Error {
    let message = match code {
        ErrorCode::INVALIDARGUMENT => "The indicator computation input is invalid.",
        ErrorCode::UNSUPPORTEDCAPABILITY => "The indicator worker requires inline input.",
        ErrorCode::CANCELLED => "The indicator computation was cancelled.",
        ErrorCode::TIMEOUT => "The indicator computation exceeded its deadline.",
        ErrorCode::WORKEREXITED => "The indicator worker is unavailable.",
        ErrorCode::OUTPUTINVALID => "The indicator result failed validation.",
        ErrorCode::PROTOCOLMISMATCH => "The indicator worker is incompatible.",
        ErrorCode::RESOURCELIMIT => "The indicator computation exceeded its resource budget.",
        ErrorCode::BUSY => "The indicator worker is busy.",
        _ => "The indicator code failed during computation.",
    };
    Error {
        code,
        message: message.into(),
        retryable: false,
        details: Default::default(),
    }
}
