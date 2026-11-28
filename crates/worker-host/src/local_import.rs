//! Private one-shot NDJSON exchange for an already authorized CSV snapshot.

use std::{future::Future, io, process::Stdio, time::Duration};

use base64::{Engine, engine::general_purpose::STANDARD};
use scopehub_application::{PREVIEW_LOCAL_IMPORT, failure, validate_csv_preview};
use scopehub_domain::protocol::{Error, ErrorCode, LocalImportRowLocator, PreviewIssue};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader},
    process::{Child, Command},
    runtime::Handle,
    sync::OwnedSemaphorePermit,
    task::JoinHandle,
    time::{Instant, timeout_at},
};
use uuid::Uuid;

use crate::{
    POLICY,
    market::{WorkerConfig, protocol_failure, worker_slot},
    max_message_bytes, negotiate_worker, read_frame,
};

#[cfg(all(test, target_os = "linux"))]
use crate::python::development_venv;
#[cfg(windows)]
use crate::python::direct_interpreter;
#[cfg(not(windows))]
use crate::python::native_development_interpreter;

const MAX_SOURCE_BYTES: usize = 4 * 1024 * 1024;
const MAX_SOURCE_RECORDS: u64 = 10_000;
const MAX_SOURCE_ROW_FRAME_BYTES: usize = 64 * 1024;
const CHUNK_BYTES: usize = 192 * 1024;
const VERSION: u8 = 1;
const CHECK_LOCAL_IMPORT: &str = "market.checkLocalImport";
const INSPECT_LOCAL_FOLDER_SOURCE: &str = "market.inspectLocalFolderSource";
const INSPECT_LOCAL_IMPORT_SOURCE_ROW: &str = "market.inspectLocalImportSourceRow";
const REVIEW_LOCAL_IMPORT_SOURCE_ROW: &str = "market.reviewLocalImportSourceRow";
const SOURCE_ROW_REVIEW_ENV: &str = "SCOPEHUB_CSV_SOURCE_ROW_REVIEW";

#[derive(Clone, Copy, Serialize)]
#[serde(untagged)]
enum Check {
    Preview {
        row_limit: u32,
    },
    Complete {
        complete: bool,
    },
    InspectRefresh {
        inspect_refresh: bool,
    },
    ReadSourceRow {
        record_number: u64,
        #[serde(skip_serializing_if = "Option::is_none")]
        review_declarations: Option<bool>,
    },
}

impl Check {
    fn reviews_declarations(self) -> bool {
        matches!(
            self,
            Self::ReadSourceRow {
                review_declarations: Some(true),
                ..
            }
        )
    }

    fn capability(self) -> &'static str {
        match self {
            Self::Preview { .. } => PREVIEW_LOCAL_IMPORT,
            Self::Complete { .. } => CHECK_LOCAL_IMPORT,
            Self::InspectRefresh { .. } => INSPECT_LOCAL_FOLDER_SOURCE,
            Self::ReadSourceRow { .. } => INSPECT_LOCAL_IMPORT_SOURCE_ROW,
        }
    }
}

#[derive(Serialize)]
struct Header<'a> {
    version: u8,
    request_id: &'a str,
    byte_count: usize,
    sha256: &'a str,
    mapping: &'a Value,
    #[serde(flatten)]
    check: Check,
}

#[derive(Deserialize)]
#[serde(untagged)]
enum Response {
    Preview(PreviewFrame),
    Complete(CompleteFrame),
    Inspection(InspectionFrame),
    SourceRow(SourceRowFrame),
    Review(ReviewFrame),
    Failure(FailureFrame),
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PreviewFrame {
    version: u8,
    request_id: String,
    ok: bool,
    preview: Value,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CompleteFrame {
    version: u8,
    request_id: String,
    ok: bool,
    publication: Value,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct InspectionFrame {
    version: u8,
    request_id: String,
    ok: bool,
    inspection: Value,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SourceRowFrame {
    version: u8,
    request_id: String,
    ok: bool,
    source_row: Value,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct FailureFrame {
    version: u8,
    request_id: String,
    ok: bool,
    error: WorkerFailure,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ReviewFrame {
    version: u8,
    request_id: String,
    ok: bool,
    #[serde(rename = "error")]
    _error: ReviewRequired,
    issue: PreviewIssue,
    locator: Option<LocalImportRowLocator>,
}

#[derive(Deserialize)]
enum ReviewRequired {
    #[serde(rename = "REVIEW_REQUIRED")]
    Csv,
}

#[derive(Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
enum WorkerFailure {
    ResourceLimit,
    InvalidArgument,
    DataUnavailable,
}

/// The child, including its admission permit, stays owned until reaped even
/// when the caller drops a pending request rather than signalling cancellation.
struct Process {
    runtime: Handle,
    child: Option<(Child, OwnedSemaphorePermit)>,
    terminal_error: Option<Error>,
}

impl Process {
    fn reclaim(&mut self) -> Option<PendingCleanup> {
        let (mut child, permit) = self.child.take()?;
        let grace =
            Duration::from_millis(POLICY["cancel_grace_ms"].as_u64().expect("cancel grace"));
        Some(PendingCleanup::start(
            &self.runtime,
            grace,
            permit,
            async move { reap(&mut child).await },
        ))
    }
}

struct PendingCleanup {
    deadline: Instant,
    task: JoinHandle<io::Result<()>>,
}

impl PendingCleanup {
    fn start(
        runtime: &Handle,
        grace: Duration,
        permit: OwnedSemaphorePermit,
        reap: impl Future<Output = io::Result<()>> + Send + 'static,
    ) -> Self {
        let deadline = Instant::now() + grace;
        let task = runtime.spawn(async move {
            // The timeout drops the child-owning future before returning admission.
            // An unconfirmed exit is an error and cannot authorize replacement.
            let outcome = timeout_at(deadline, reap).await;
            drop(permit);
            outcome.map_err(|_| io::Error::from(io::ErrorKind::TimedOut))?
        });
        Self { deadline, task }
    }

    async fn wait(&mut self) -> io::Result<()> {
        timeout_at(self.deadline, &mut self.task)
            .await
            .map_err(|_| io::Error::from(io::ErrorKind::TimedOut))?
            .map_err(io::Error::other)?
    }
}

impl Drop for Process {
    fn drop(&mut self) {
        let _ = self.reclaim();
    }
}

async fn reap(child: &mut Child) -> io::Result<()> {
    if child.try_wait()?.is_none() {
        child.start_kill()?;
    }
    child.wait().await.map(|_| ())
}

fn command(config: &WorkerConfig) -> (Command, bool) {
    let launcher = config.python_command();
    #[cfg(windows)]
    let (mut python, owns_interpreter) = match direct_interpreter(config, &launcher) {
        Some(command) => (command, true),
        None => (launcher, false),
    };
    #[cfg(not(windows))]
    let (mut python, owns_interpreter) = (launcher, native_development_interpreter(&config.python));

    // Resolve IANA zones from the locked data package in this worker.
    python
        .env("PYTHONTZPATH", "")
        .args(["-m", "worker_runtime.local_import"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .kill_on_drop(true);
    (python, owns_interpreter)
}

/// Preview immutable bytes already read and verified by Rust.
/// The caller revalidates its selection before the single possible replacement.
pub async fn preview(
    config: &WorkerConfig,
    content: &[u8],
    sha256: &str,
    mapping: &Value,
    row_limit: u32,
    cancelled: impl Future<Output = ()>,
    check_live: impl FnOnce() -> Result<(), Error>,
) -> Result<Value, Error> {
    analyze(
        config,
        content,
        sha256,
        mapping,
        Check::Preview { row_limit },
        cancelled,
        Some(check_live),
    )
    .await
}

/// Validate and normalize the complete file using the saved source mapping.
/// The child receives the authorized bytes and has no source or storage path.
pub async fn complete(
    config: &WorkerConfig,
    content: &[u8],
    sha256: &str,
    mapping: &Value,
    cancelled: impl Future<Output = ()>,
) -> Result<Value, Error> {
    analyze(
        config,
        content,
        sha256,
        mapping,
        Check::Complete { complete: true },
        cancelled,
        None::<fn() -> Result<(), Error>>,
    )
    .await
    .map_err(|mut error| {
        if error.code == ErrorCode::RESOURCELIMIT {
            error.message = "Publication supports up to 10,000 source records and 4 MiB per file or result. Select a smaller CSV file.".into();
        }
        error
    })
}

/// Inspect the complete authorized file for folder review, retaining competing values.
pub async fn inspect_folder_source(
    config: &WorkerConfig,
    content: &[u8],
    sha256: &str,
    mapping: &Value,
    cancelled: impl Future<Output = ()>,
) -> Result<Value, Error> {
    analyze(
        config,
        content,
        sha256,
        mapping,
        Check::InspectRefresh {
            inspect_refresh: true,
        },
        cancelled,
        None::<fn() -> Result<(), Error>>,
    )
    .await
}

/// Parse one logical record from already authorized immutable source bytes.
pub async fn read_source_row(
    config: &WorkerConfig,
    content: &[u8],
    sha256: &str,
    mapping: &Value,
    record_number: u64,
    cancelled: impl Future<Output = ()>,
) -> Result<Value, Error> {
    source_row(
        config,
        content,
        sha256,
        mapping,
        record_number,
        None,
        cancelled,
    )
    .await
}

/// Compare declarations using the separately negotiated worker capability.
pub async fn review_source_row(
    config: &WorkerConfig,
    content: &[u8],
    sha256: &str,
    mapping: &Value,
    record_number: u64,
    cancelled: impl Future<Output = ()>,
) -> Result<Value, Error> {
    source_row(
        config,
        content,
        sha256,
        mapping,
        record_number,
        Some(true),
        cancelled,
    )
    .await
}

async fn source_row(
    config: &WorkerConfig,
    content: &[u8],
    sha256: &str,
    mapping: &Value,
    record_number: u64,
    review_declarations: Option<bool>,
    cancelled: impl Future<Output = ()>,
) -> Result<Value, Error> {
    if !(1..=MAX_SOURCE_RECORDS).contains(&record_number) {
        return Err(failure(ErrorCode::INVALIDARGUMENT));
    }
    analyze(
        config,
        content,
        sha256,
        mapping,
        Check::ReadSourceRow {
            record_number,
            review_declarations,
        },
        cancelled,
        None::<fn() -> Result<(), Error>>,
    )
    .await
}

async fn analyze(
    config: &WorkerConfig,
    content: &[u8],
    sha256: &str,
    mapping: &Value,
    check: Check,
    cancelled: impl Future<Output = ()>,
    mut retry_check: Option<impl FnOnce() -> Result<(), Error>>,
) -> Result<Value, Error> {
    if content.len() > MAX_SOURCE_BYTES {
        return Err(failure(ErrorCode::RESOURCELIMIT));
    }
    if sha256.len() != 64
        || !sha256
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
        || !mapping.is_object()
        || matches!(check, Check::Preview { row_limit } if !(1..=500).contains(&row_limit))
    {
        return Err(failure(ErrorCode::INVALIDARGUMENT));
    }
    let (mut python, owns_interpreter) = command(config);
    if check.reviews_declarations() {
        python.env(SOURCE_ROW_REVIEW_ENV, "1");
    }
    if !owns_interpreter {
        retry_check = None;
    }
    tokio::pin!(cancelled);
    let mut retried = false;
    loop {
        let request_id = Uuid::new_v4().to_string();
        let request = Header {
            version: VERSION,
            request_id: &request_id,
            byte_count: content.len(),
            sha256,
            mapping,
            check,
        };
        let header = encode(&request)?;
        let mut process = Process {
            runtime: Handle::current(),
            child: None,
            terminal_error: None,
        };
        let attempt = tokio::select! {
            biased;
            () = &mut cancelled => Ok(Err(failure(ErrorCode::CANCELLED))),
            result = tokio::time::timeout(
                config.deadline,
                exchange(&mut python, &mut process, &request, &header, content),
            ) => result,
        };
        let reaped = match process.reclaim() {
            Some(mut cleanup) => cleanup.wait().await.is_ok(),
            None => false,
        };
        if let Ok(result) = attempt {
            return result;
        }

        // Preserve an authoritative rejection even if the child never closed
        // stdout or exited. Successful results still require a clean teardown.
        if let Some(error) = process.terminal_error.take() {
            return Err(error);
        }
        let mut timeout = failure(ErrorCode::TIMEOUT);
        if retried {
            timeout.message =
                "The local CSV preview timed out twice. Try running the preview again.".into();
        }
        if !reaped {
            return Err(timeout);
        }
        let Some(check_live) = retry_check.take() else {
            return Err(timeout);
        };
        check_live()?;
        retried = true;
    }
}

async fn exchange(
    python: &mut Command,
    process: &mut Process,
    request: &Header<'_>,
    header: &[u8],
    content: &[u8],
) -> Result<Value, Error> {
    let permit = worker_slot()?;
    let child = python.spawn().map_err(worker_io)?;
    let (worker, _) = process.child.insert((child, permit));
    let mut input = worker.stdin.take().expect("piped stdin");
    let mut output = BufReader::new(worker.stdout.take().expect("piped stdout"));
    let hello = read_frame(&mut output).await.map_err(protocol_failure)?;
    let negotiated = negotiate_worker(&hello, "market", &[request.check.capability()])
        .map_err(protocol_failure)?;
    if negotiated.version != "1.1"
        || negotiated.capabilities.iter().any(|name| {
            !matches!(
                name.as_str(),
                PREVIEW_LOCAL_IMPORT
                    | CHECK_LOCAL_IMPORT
                    | INSPECT_LOCAL_FOLDER_SOURCE
                    | INSPECT_LOCAL_IMPORT_SOURCE_ROW
                    | REVIEW_LOCAL_IMPORT_SOURCE_ROW
            )
        })
    {
        return Err(failure(ErrorCode::PROTOCOLMISMATCH));
    }
    if request.check.reviews_declarations()
        && !negotiated
            .capabilities
            .iter()
            .any(|name| name == REVIEW_LOCAL_IMPORT_SOURCE_ROW)
    {
        return Err(failure(ErrorCode::UNSUPPORTEDCAPABILITY));
    }
    input.write_all(header).await.map_err(worker_io)?;
    for (index, bytes) in content.chunks(CHUNK_BYTES).enumerate() {
        let chunk = encode(&json!({
            "version": VERSION,
            "request_id": request.request_id,
            "index": index,
            "data": STANDARD.encode(bytes),
        }))?;
        input.write_all(&chunk).await.map_err(worker_io)?;
    }
    drop(input);

    let frame = read_frame(&mut output).await.map_err(protocol_failure)?;
    let result = decode(&frame, request);
    process.terminal_error = result.as_ref().err().cloned();
    if !output.fill_buf().await.map_err(worker_io)?.is_empty() {
        return Err(failure(ErrorCode::OUTPUTINVALID));
    }
    let status = worker.wait().await.map_err(worker_io)?;
    drop(process.child.take());
    if !status.success() {
        return Err(failure(ErrorCode::WORKEREXITED));
    }
    result
}

fn worker_io(_: std::io::Error) -> Error {
    failure(ErrorCode::WORKEREXITED)
}

fn encode(message: &impl Serialize) -> Result<Vec<u8>, Error> {
    let mut frame = serde_json::to_vec(message).map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
    frame.push(b'\n');
    if frame.len() > max_message_bytes() {
        return Err(failure(ErrorCode::RESOURCELIMIT));
    }
    Ok(frame)
}

fn decode(frame: &[u8], request: &Header<'_>) -> Result<Value, Error> {
    if matches!(request.check, Check::ReadSourceRow { .. })
        && frame.len() > MAX_SOURCE_ROW_FRAME_BYTES
    {
        return Err(failure(ErrorCode::RESOURCELIMIT));
    }
    let body = frame.strip_suffix(b"\n").unwrap_or(frame);
    let body = body.strip_suffix(b"\r").unwrap_or(body);
    if body.contains(&b'\r') || body.contains(&b'\n') {
        return Err(failure(ErrorCode::OUTPUTINVALID));
    }
    let response = serde_json::from_slice(body).map_err(|_| failure(ErrorCode::OUTPUTINVALID))?;
    match (request.check, response) {
        (Check::Preview { row_limit }, Response::Preview(reply))
            if reply.version == VERSION && reply.request_id == request.request_id && reply.ok =>
        {
            let (Some(version), Some(revision)) = (
                request.mapping["version"].as_u64(),
                request.mapping["revision"].as_str(),
            ) else {
                return Err(failure(ErrorCode::OUTPUTINVALID));
            };
            validate_csv_preview(
                &reply.preview,
                request.sha256,
                version,
                revision,
                u64::from(row_limit),
            )?;
            Ok(reply.preview)
        }
        (Check::Complete { .. }, Response::Complete(reply))
            if reply.version == VERSION
                && reply.request_id == request.request_id
                && reply.ok
                && reply.publication["file_sha256"].as_str() == Some(request.sha256) =>
        {
            Ok(reply.publication)
        }
        (Check::InspectRefresh { .. }, Response::Inspection(reply))
            if reply.version == VERSION
                && reply.request_id == request.request_id
                && reply.ok
                && reply.inspection["file_sha256"].as_str() == Some(request.sha256) =>
        {
            Ok(reply.inspection)
        }
        (
            Check::ReadSourceRow {
                record_number,
                review_declarations,
            },
            Response::SourceRow(reply),
        ) if reply.version == VERSION
            && reply.request_id == request.request_id
            && reply.ok
            && reply.source_row["source_sha256"].as_str() == Some(request.sha256)
            && reply.source_row.get("declaration_conflicts").is_some()
                == review_declarations.is_some()
            && reply.source_row["locator"]["record_number"].as_u64() == Some(record_number) =>
        {
            Ok(reply.source_row)
        }
        (Check::Complete { .. } | Check::InspectRefresh { .. }, Response::Review(reply))
            if reply.version == VERSION && reply.request_id == request.request_id && !reply.ok =>
        {
            Err(csv_review_required(reply))
        }
        (_, Response::Failure(reply))
            if reply.version == VERSION && reply.request_id == request.request_id && !reply.ok =>
        {
            Err(failure(match reply.error {
                WorkerFailure::ResourceLimit => ErrorCode::RESOURCELIMIT,
                WorkerFailure::InvalidArgument => ErrorCode::INVALIDARGUMENT,
                WorkerFailure::DataUnavailable
                    if matches!(request.check, Check::ReadSourceRow { .. }) =>
                {
                    ErrorCode::DATAUNAVAILABLE
                }
                WorkerFailure::DataUnavailable => ErrorCode::OUTPUTINVALID,
            }))
        }
        _ => Err(failure(ErrorCode::OUTPUTINVALID)),
    }
}

fn csv_review_required(reply: ReviewFrame) -> Error {
    let mut error = failure(ErrorCode::DATAUNAVAILABLE);
    let message = match &reply.locator {
        Some(locator) => format!(
            "Review CSV record {}. {}",
            locator.record_number, reply.issue.message
        ),
        None => reply.issue.message,
    };
    error.message = message.chars().take(512).collect();
    error
        .details
        .insert("reason".into(), json!("csv_review_required"));
    error
        .details
        .insert("issue_code".into(), json!(reply.issue.code));
    error
        .details
        .insert("field".into(), json!(reply.issue.field));
    if let Some(locator) = reply.locator {
        error.details.insert("locator".into(), json!(locator));
    }
    error
}

#[cfg(test)]
mod tests;
