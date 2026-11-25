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
mod tests {
    use std::{fs, future, path::PathBuf};

    use super::*;

    const DIGEST: &str = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";

    fn development_config() -> WorkerConfig {
        let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../..")
            .canonicalize()
            .unwrap();
        WorkerConfig::development(&repo)
    }

    struct Fixture(WorkerConfig);

    impl Fixture {
        fn new(script: &str) -> Self {
            Self::with_capabilities(script, &[PREVIEW_LOCAL_IMPORT])
        }

        fn with_capabilities(script: &str, capabilities: &[&str]) -> Self {
            let root = std::env::temp_dir().join(format!("scopehub-preview-{}", Uuid::new_v4()));
            let package = root.join("worker_runtime");
            fs::create_dir_all(&package).unwrap();
            fs::write(package.join("__init__.py"), "").unwrap();
            let hello = json!({
                "protocol_version": "1.1", "message_id": Uuid::new_v4().to_string(),
                "timestamp_ms": 0, "type": "hello", "role": "market", "worker_version": "1",
                "supported_protocols": ["1.0", "1.1"], "capabilities": capabilities,
            });
            let program = format!("print({:?}, flush=True)\n{script}", hello.to_string());
            fs::write(package.join("local_import.py"), program).unwrap();
            Self(WorkerConfig {
                root,
                ..development_config()
            })
        }
    }

    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0.root);
        }
    }

    #[tokio::test]
    async fn first_worker_timeout_retries_the_frozen_preview_once() {
        let mut fixture = Fixture::new(
            r#"import base64, hashlib, json, pathlib, sys, time
header = json.loads(sys.stdin.buffer.readline())
chunks = [json.loads(line) for line in sys.stdin.buffer]
data = b''.join(base64.b64decode(chunk['data']) for chunk in chunks)
assert len(chunks) == 1 and chunks[0]['request_id'] == header['request_id']
assert data == b'abc' and hashlib.sha256(data).hexdigest() == header['sha256']
assert header['mapping'] == {'version': 1, 'revision': 'synthetic'} and header['row_limit'] == 3
attempts = pathlib.Path('attempts')
first = not attempts.exists()
with attempts.open('a', encoding='ascii') as out:
    out.write(header['request_id'] + '\n')
if first:
    time.sleep(30)
preview = dict(file_sha256=header['sha256'], mapping_version=1, mapping_revision='synthetic',
               header=['abc'], raw_header_text=None, rows=[], issues=[],
               raw_only_columns=[], series=None, truncated=False)
print(json.dumps(dict(version=1, request_id=header['request_id'], ok=True, preview=preview)),
      flush=True)
"#,
        );
        fixture.0.deadline = Duration::from_secs(3);
        let result = preview(
            &fixture.0,
            b"abc",
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
            &json!({"version": 1, "revision": "synthetic"}),
            3,
            future::pending(),
            || Ok(()),
        )
        .await;
        let attempts = fs::read_to_string(fixture.0.root.join("attempts")).unwrap();
        let attempts: Vec<_> = attempts.lines().collect();
        assert_eq!(result.unwrap()["mapping_revision"], "synthetic");
        assert_eq!(attempts.len(), 2);
        assert_ne!(attempts[0], attempts[1]);
    }

    const STALL_AFTER_RECEIVING: &str = r#"import json, sys, time
header = json.loads(sys.stdin.buffer.readline())
sys.stdin.buffer.read()
with open('attempts', 'a', encoding='ascii') as out:
    out.write(header['request_id'] + '\n')
time.sleep(30)
"#;

    #[tokio::test]
    async fn permanent_timeout_stops_after_two_previews_and_one_complete() {
        let mut previewer = Fixture::new(STALL_AFTER_RECEIVING);
        let mut publisher =
            Fixture::with_capabilities(STALL_AFTER_RECEIVING, &[CHECK_LOCAL_IMPORT]);
        previewer.0.deadline = Duration::from_secs(2);
        publisher.0.deadline = previewer.0.deadline;
        let mapping = json!({});
        let (preview_result, complete_result) = tokio::join!(
            preview(
                &previewer.0,
                b"",
                DIGEST,
                &mapping,
                1,
                future::pending(),
                || Ok(()),
            ),
            complete(&publisher.0, b"", DIGEST, &mapping, future::pending())
        );
        let err = preview_result.unwrap_err();
        assert_eq!(err.code, ErrorCode::TIMEOUT);
        assert!(err.message.contains("timed out twice"));
        assert_eq!(complete_result.unwrap_err().code, ErrorCode::TIMEOUT);

        let attempts = |fixture: &Fixture| {
            fs::read_to_string(fixture.0.root.join("attempts"))
                .unwrap()
                .lines()
                .count()
        };
        assert_eq!(attempts(&previewer), 2);
        assert_eq!(attempts(&publisher), 1);
    }

    #[tokio::test]
    async fn cancellation_and_reselection_prevent_a_replacement() {
        let mut cancelled = Fixture::new(STALL_AFTER_RECEIVING);
        let mut changed = Fixture::new(STALL_AFTER_RECEIVING);
        cancelled.0.deadline = Duration::from_secs(2);
        changed.0.deadline = cancelled.0.deadline;
        let (stop, stopped) = tokio::sync::oneshot::channel();
        let mapping = json!({});
        let (cancelled_result, changed_result) = tokio::join!(
            preview(
                &cancelled.0,
                b"",
                DIGEST,
                &mapping,
                1,
                async { stopped.await.unwrap() },
                move || {
                    stop.send(()).unwrap();
                    Ok(())
                },
            ),
            preview(
                &changed.0,
                b"",
                DIGEST,
                &mapping,
                1,
                future::pending(),
                || Err(failure(ErrorCode::STALECONTEXT)),
            )
        );
        assert_eq!(cancelled_result.unwrap_err().code, ErrorCode::CANCELLED);
        assert_eq!(changed_result.unwrap_err().code, ErrorCode::STALECONTEXT);
        for fixture in [cancelled, changed] {
            let attempts = fs::read_to_string(fixture.0.root.join("attempts")).unwrap();
            assert_eq!(attempts.lines().count(), 1);
        }
    }

    #[tokio::test]
    async fn resource_limit_survives_stalled_teardown() {
        let mut fixture = Fixture::new(
            r#"import json, sys, time
header = json.loads(sys.stdin.buffer.readline())
sys.stdin.buffer.read()
print(json.dumps(dict(version=1, request_id=header['request_id'], ok=False,
                      error='RESOURCE_LIMIT')), flush=True)
time.sleep(30)
"#,
        );
        fixture.0.deadline = Duration::from_secs(2);
        let result = preview(
            &fixture.0,
            b"",
            DIGEST,
            &json!({}),
            1,
            future::pending(),
            || panic!("a received rejection is ineligible for another attempt"),
        )
        .await;
        let error = result.unwrap_err();
        assert_eq!(error.code, ErrorCode::RESOURCELIMIT);
        assert!(!error.retryable);
    }

    #[tokio::test]
    async fn a_wrong_mapping_revision_cannot_recover_from_stalled_teardown() {
        let mut fixture = Fixture::new(
            r#"import json, sys, time
header = json.loads(sys.stdin.buffer.readline())
sys.stdin.buffer.read()
preview = dict(file_sha256=header['sha256'], mapping_version=1, mapping_revision='wrong',
               header=[], raw_header_text=None, rows=[], issues=[], raw_only_columns=[],
               series=None, truncated=False)
print(json.dumps(dict(version=1, request_id=header['request_id'], ok=True, preview=preview)),
      flush=True)
time.sleep(30)
"#,
        );
        fixture.0.deadline = Duration::from_secs(2);
        let result = preview(
            &fixture.0,
            b"",
            DIGEST,
            &json!({"version":1,"revision":"expected"}),
            1,
            future::pending(),
            || panic!("a mismatched preview is ineligible for another attempt"),
        )
        .await;
        let error = result.unwrap_err();
        assert_eq!(error.code, ErrorCode::OUTPUTINVALID);
        assert!(!error.retryable);
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn native_executable_with_a_copied_venv_config_is_not_the_declared_interpreter() {
        let fixture = Fixture::new("");
        let venv = fixture.0.root.join(".venv");
        fs::create_dir_all(venv.join("bin")).unwrap();
        let installed_venv = development_venv(&fixture.0.python).unwrap();
        fs::copy(installed_venv.join("pyvenv.cfg"), venv.join("pyvenv.cfg")).unwrap();
        let timer = venv.join("bin/python");
        fs::copy("/usr/bin/true", &timer).unwrap();
        assert!(native_development_interpreter(&fixture.0.python));
        assert!(!native_development_interpreter(&timer));
    }

    #[tokio::test]
    async fn an_unqualified_runtime_remains_one_shot() {
        let mut fixture = Fixture::new(STALL_AFTER_RECEIVING);
        fixture.0.deadline = Duration::from_secs(2);
        let (qualified, expected) = command(&fixture.0);
        assert!(
            expected,
            "the normal development interpreter should be qualified"
        );
        fixture.0.python = fs::canonicalize(qualified.as_std().get_program()).unwrap();
        let (_, can_retry) = command(&fixture.0);
        assert!(!can_retry);
        let failure = preview(
            &fixture.0,
            b"",
            DIGEST,
            &json!({}),
            1,
            future::pending(),
            || panic!("a custom executable must not be restarted"),
        )
        .await
        .unwrap_err();
        assert_eq!(failure.code, ErrorCode::TIMEOUT);
        let attempts = fs::read_to_string(fixture.0.root.join("attempts")).unwrap();
        assert_eq!(attempts.lines().count(), 1);
    }

    #[tokio::test]
    async fn failed_or_stalled_cleanup_releases_admission_without_proof() {
        use std::sync::Arc;
        use tokio::sync::Semaphore;

        let runtime = Handle::current();
        let slots = Arc::new(Semaphore::new(1));
        let grace = Duration::from_millis(30);
        let permit = Arc::clone(&slots).try_acquire_owned().unwrap();
        let mut cleanup = PendingCleanup::start(&runtime, grace, permit, async {
            Err(io::ErrorKind::PermissionDenied.into())
        });
        assert_eq!(
            cleanup.wait().await.unwrap_err().kind(),
            io::ErrorKind::PermissionDenied
        );
        let permit = Arc::clone(&slots).try_acquire_owned().unwrap();
        let mut cleanup = PendingCleanup::start(&runtime, grace, permit, future::pending());
        assert_eq!(
            cleanup.wait().await.unwrap_err().kind(),
            io::ErrorKind::TimedOut
        );
        assert!(
            tokio::time::timeout(Duration::from_secs(1), slots.acquire())
                .await
                .unwrap()
                .is_ok()
        );
    }

    const WITNESS_WAIT: Duration = Duration::from_secs(5);
    const WITNESS_RECORD_BYTES: usize = 64 * 1024;
    const RUNTIME_WITNESS: &str = r#"import json, os, site, sys, time
from threading import Thread

def release_on_eof():
    # The legacy launcher inherits this pipe into its interpreter. Closing the
    # Rust-owned input also ends this fixture when its facts cannot be read.
    if os.read(0, 1) == b'':
        os._exit(0)

Thread(target=release_on_eof, daemon=True).start()
print(json.dumps([os.getpid(), os.getppid()]), flush=True)
from importlib.metadata import version
from zoneinfo import ZoneInfo

def normalize(path):
    return os.path.normcase(os.path.realpath(path))

runtime = dict(version=sys.version, libraries=[version('jsonschema'), version('tzdata')],
               paths=[normalize(path) for path in [sys.executable, sys._base_executable,
                      sys.prefix, sys.base_prefix, *site.getsitepackages(), *sys.path]],
               timezone=ZoneInfo('Asia/Shanghai').key)
print(json.dumps(runtime), flush=True)
time.sleep(15)
raise SystemExit(124)
"#;

    #[derive(Debug, Deserialize, PartialEq)]
    #[serde(deny_unknown_fields)]
    struct InterpreterRuntime {
        version: String,
        libraries: [String; 2],
        paths: Vec<String>,
        timezone: String,
    }

    fn spawn_witness(command: &mut Command, caller: &str) -> Result<Child, String> {
        let start = Instant::now();
        let child = command
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .kill_on_drop(true)
            .spawn()
            .map_err(|error| format!("caller={caller} phase=spawn error={error}"))?;
        eprintln!(
            "caller={caller} phase=spawn child_pid={:?} elapsed_ms={}",
            child.id(),
            start.elapsed().as_millis()
        );
        Ok(child)
    }

    async fn witness_record<T: serde::de::DeserializeOwned>(
        output: &mut BufReader<tokio::process::ChildStdout>,
        deadline: Instant,
        phase: &str,
    ) -> Result<T, String> {
        use tokio::io::AsyncReadExt;

        let mut frame = Vec::new();
        let read = timeout_at(
            deadline,
            output
                .take((WITNESS_RECORD_BYTES + 1) as u64)
                .read_until(b'\n', &mut frame),
        )
        .await;
        let bytes = frame.len();
        let terminal = match read {
            Err(_) => "deadline",
            Ok(Err(error)) => {
                return Err(format!(
                    "phase={phase} terminal=pipe-error bytes={bytes} kind={:?} os_error={:?}",
                    error.kind(),
                    error.raw_os_error()
                ));
            }
            Ok(Ok(_)) if bytes > WITNESS_RECORD_BYTES => "oversized-record",
            Ok(Ok(0)) => "stdout-closed",
            Ok(Ok(_)) if !frame.ends_with(b"\n") => "incomplete-at-eof",
            Ok(Ok(_)) => {
                return serde_json::from_slice(&frame)
                    .map_err(|_| format!("phase={phase} terminal=invalid-record bytes={bytes}"));
            }
        };
        Err(format!("phase={phase} terminal={terminal} bytes={bytes}"))
    }

    async fn read_interpreter_witness(
        child: &mut Child,
        caller: &str,
        direct: bool,
    ) -> Result<InterpreterRuntime, String> {
        let child_pid = child.id().expect("owned test child");
        let start = Instant::now();
        let deadline = start + WITNESS_WAIT;
        let mut output = BufReader::new(child.stdout.take().expect("piped witness stdout"));
        let result = async {
            let hello: Value = witness_record(&mut output, deadline, "worker-hello").await?;
            if hello["type"] != "hello" || hello["role"] != "market" {
                return Err("phase=worker-hello terminal=unexpected-hello".into());
            }
            eprintln!("caller={caller} phase=worker-hello elapsed_ms={}", start.elapsed().as_millis());
            let (pid, parent): (u32, u32) =
                witness_record(&mut output, deadline, "interpreter-identity").await?;
            eprintln!(
                "caller={caller} phase=interpreter-identity pid={pid} parent={parent} elapsed_ms={}",
                start.elapsed().as_millis()
            );
            let matches = if direct {
                pid == child_pid && parent == std::process::id()
            } else {
                pid != 0 && pid != child_pid && parent == child_pid
            };
            if !matches {
                return Err(format!("phase=interpreter-identity terminal=unexpected-identity pid={pid} parent={parent}"));
            }
            let runtime: InterpreterRuntime =
                witness_record(&mut output, deadline, "locked-runtime").await?;
            if runtime.version.is_empty()
                || runtime.libraries.iter().any(String::is_empty)
                || runtime.paths.len() < 4
                || runtime.timezone != "Asia/Shanghai"
            {
                return Err("phase=locked-runtime terminal=unexpected-runtime".into());
            }
            eprintln!(
                "caller={caller} phase=locked-runtime paths={} packages={:?} timezone={} elapsed_ms={}",
                runtime.paths.len(),
                runtime.libraries,
                runtime.timezone,
                start.elapsed().as_millis()
            );
            Ok(runtime)
        }
        .await;

        let state = child.try_wait();
        match (result, state) {
            (Ok(runtime), Ok(None)) => Ok(runtime),
            (Ok(_), state) => Err(format!(
                "caller={caller} child_pid={child_pid} phase=qualification terminal=not-observed-alive child_state={state:?}"
            )),
            (Err(error), state) => Err(format!(
                "caller={caller} child_pid={child_pid} {error} child_state={state:?} elapsed_ms={}",
                start.elapsed().as_millis()
            )),
        }
    }

    #[tokio::test]
    async fn interpreter_witness_distinguishes_live_partial_records_exit_and_wrong_identity() {
        for (name, script, expected) in [
            (
                "exit",
                "import sys\nraise SystemExit(9)\n",
                "phase=interpreter-identity terminal=stdout-closed bytes=0",
            ),
            (
                "partial",
                "import os, sys, time\nprint([os.getpid(), os.getppid()], flush=True)\nsys.stdout.write('{')\nsys.stdout.flush()\ntime.sleep(15)\n",
                "phase=locked-runtime terminal=deadline bytes=1",
            ),
            (
                "wrong-identity",
                "import os, time\nprint([os.getpid(), 0], flush=True)\ntime.sleep(15)\n",
                "phase=interpreter-identity terminal=unexpected-identity",
            ),
            ("ordinary", RUNTIME_WITNESS, ""),
        ] {
            let fixture = Fixture::new(script);
            let (mut python, _) = command(&fixture.0);
            let mut child = spawn_witness(&mut python, name).unwrap();
            let observation = read_interpreter_witness(&mut child, name, true).await;
            if name == "exit" {
                let status = tokio::time::timeout(WITNESS_WAIT, child.wait())
                    .await
                    .unwrap()
                    .unwrap();
                assert_eq!(status.code(), Some(9));
                eprintln!("caller={name} phase=cleanup natural_exit_code=9");
            } else {
                drop(child.stdin.take()); // Release the positive witness through its own pipe.
                if name == "ordinary" {
                    let status = tokio::time::timeout(WITNESS_WAIT, child.wait())
                        .await
                        .unwrap()
                        .unwrap();
                    assert!(status.success(), "{name}: {status}");
                } else {
                    tokio::time::timeout(WITNESS_WAIT, reap(&mut child))
                        .await
                        .unwrap()
                        .unwrap();
                }
                assert!(child.try_wait().unwrap().is_some());
                eprintln!("caller={name} phase=cleanup reaped=true");
            }
            if expected.is_empty() {
                assert!(observation.is_ok(), "{name}: {observation:?}");
            } else {
                let error = observation.unwrap_err();
                eprintln!("{error}");
                assert!(error.contains(expected), "{error}");
            }
        }
    }

    async fn observe_legacy_witness(command: &mut Command) -> Result<InterpreterRuntime, String> {
        let mut launcher = spawn_witness(command, "legacy-launcher")?;
        let witness = read_interpreter_witness(&mut launcher, "legacy-launcher", false).await;
        drop(launcher.stdin.take());
        let released = tokio::time::timeout(WITNESS_WAIT, launcher.wait()).await;
        if !matches!(&released, Ok(Ok(status)) if status.success()) {
            // The input pipe is already closed. Reap only the handle we own,
            // even when the leaf did not report its identity or failed to exit.
            let grace =
                Duration::from_millis(POLICY["cancel_grace_ms"].as_u64().expect("cancel grace"));
            let fallback = tokio::time::timeout(grace, reap(&mut launcher)).await;
            return Err(format!(
                "caller=legacy-launcher phase=cleanup release={released:?} fallback={fallback:?} witness_error={:?}",
                witness.as_ref().err()
            ));
        }
        eprintln!("caller=legacy-launcher phase=cleanup released=true");
        witness
    }

    #[cfg(not(windows))]
    #[tokio::test]
    async fn interpreter_witness_releases_a_legacy_fixture_after_success_and_rejection() {
        // Exercise the two-process stdio lifetime portably. The Windows test
        // below exercises the actual CPython redirector and compares runtime facts.
        let malformed = RUNTIME_WITNESS.replace(
            "print(json.dumps(runtime), flush=True)",
            "print('{}', flush=True)",
        );
        for (script, valid) in [(RUNTIME_WITNESS, true), (malformed.as_str(), false)] {
            let fixture = Fixture::new(script);
            let observed =
                observe_legacy_witness(fixture.0.python_command().env("PYTHONTZPATH", "").args([
                    "-c",
                    "import subprocess, sys
raise SystemExit(subprocess.run([sys.executable, '-m', 'worker_runtime.local_import']).returncode)",
                ]))
                .await;
            if valid {
                assert!(observed.is_ok(), "{observed:?}");
            } else {
                let error = observed.unwrap_err();
                eprintln!("{error}");
                assert!(
                    error.contains("phase=locked-runtime terminal=invalid-record"),
                    "{error}"
                );
            }
        }
    }

    #[cfg(windows)]
    #[tokio::test]
    async fn windows_venv_reaps_owned_leaf_before_recovery() {
        let fixture = Fixture::new(RUNTIME_WITNESS);

        let old_runtime = observe_legacy_witness(
            fixture
                .0
                .python_command()
                .env("PYTHONTZPATH", "")
                .args(["-m", "worker_runtime.local_import"]),
        )
        .await
        .unwrap_or_else(|error| panic!("{error}"));

        let (mut command, owns_interpreter) = command(&fixture.0);
        assert!(
            owns_interpreter,
            "caller=direct-interpreter phase=launch-qualification unqualified"
        );
        let permit = worker_slot().expect("caller=direct-interpreter phase=admission");
        let child = spawn_witness(&mut command, "direct-interpreter")
            .unwrap_or_else(|error| panic!("{error}"));
        let mut process = Process {
            runtime: Handle::current(),
            child: Some((child, permit)),
            terminal_error: None,
        };
        let (child, _) = process.child.as_mut().expect("owned test child");
        let direct = read_interpreter_witness(child, "direct-interpreter", true).await;
        // Keep stdin open. The existing recovery cleanup must reap the exact
        // live interpreter handle. It may not infer exit from PID or a pipe.
        let reaped = process.reclaim().unwrap().wait().await;
        assert!(
            reaped.is_ok(),
            "caller=direct-interpreter phase=cleanup reap={reaped:?} witness_error={:?}",
            direct.as_ref().err()
        );
        eprintln!("caller=direct-interpreter phase=cleanup reaped=true");
        assert!(process.child.is_none());
        let direct_runtime = direct.unwrap_or_else(|error| panic!("{error}"));
        assert!(
            direct_runtime == old_runtime,
            "caller=direct-interpreter phase=runtime-equivalence locked runtime changed"
        );
        eprintln!("caller=direct-interpreter phase=runtime-equivalence matched=true");
    }

    #[tokio::test]
    async fn old_csv_worker_accepts_raw_rows_and_is_never_sent_review_data() {
        let fixture = Fixture::with_capabilities(
            r#"import json, pathlib, sys
first = sys.stdin.buffer.readline()
if first:
    header = json.loads(first)
    pathlib.Path('received').write_text(first.decode('utf-8'), encoding='utf-8')
    assert 'review_declarations' not in header
    assert header['record_number'] == 1
    assert sys.stdin.buffer.read() == b''
    print(json.dumps({
        'version': 1, 'request_id': header['request_id'], 'ok': True,
        'source_row': {'source_sha256': header['sha256'], 'locator': {'record_number': 1},
                       'header': ['Close'], 'raw_fields': ['04.00']},
    }), flush=True)
"#,
            &[INSPECT_LOCAL_IMPORT_SOURCE_ROW],
        );
        let mapping = json!({});
        let error = review_source_row(&fixture.0, b"", DIGEST, &mapping, 1, future::pending())
            .await
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::UNSUPPORTEDCAPABILITY);
        assert!(!fixture.0.root.join("received").exists());
        let raw = read_source_row(&fixture.0, b"", DIGEST, &mapping, 1, future::pending())
            .await
            .unwrap();
        assert_eq!(raw["raw_fields"], json!(["04.00"]));
        assert!(raw.get("declaration_conflicts").is_none());
        assert!(fixture.0.root.join("received").is_file());
    }

    #[tokio::test]
    async fn refuses_a_worker_with_other_capabilities_before_sending_data() {
        let fixture = Fixture::with_capabilities(
            "import sys\nif sys.stdin.buffer.read(1):\n    open('unexpected-input', 'w').close()\n",
            &[PREVIEW_LOCAL_IMPORT, "market.getBars"],
        );
        let error = preview(
            &fixture.0,
            b"",
            DIGEST,
            &json!({}),
            1,
            future::pending(),
            || Ok(()),
        )
        .await
        .unwrap_err();
        assert_eq!(error.code, ErrorCode::PROTOCOLMISMATCH);
        assert!(!fixture.0.root.join("unexpected-input").exists());
    }

    #[tokio::test]
    async fn refuses_a_preview_only_worker_before_sending_publication_data() {
        let fixture = Fixture::new(
            "import sys\nif sys.stdin.buffer.read(1):\n    open('unexpected-input', 'w').close()\n",
        );
        let error = complete(&fixture.0, b"", DIGEST, &json!({}), future::pending())
            .await
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::PROTOCOLMISMATCH);
        assert!(!fixture.0.root.join("unexpected-input").exists());
    }

    #[tokio::test]
    async fn retains_no_result_from_exited_and_oversized_children() {
        for (script, code) in [
            (
                "import sys\nsys.stdin.buffer.read()\nraise SystemExit(9)\n",
                ErrorCode::WORKEREXITED,
            ),
            (
                "import sys\nsys.stdin.buffer.read()\nprint('x' * 4194304)\n",
                ErrorCode::RESOURCELIMIT,
            ),
            (
                r#"import json, sys
header = json.loads(sys.stdin.buffer.readline())
sys.stdin.buffer.read()
preview = dict(file_sha256=header['sha256'], mapping_version=1, mapping_revision='sample',
               header=[], raw_header_text=None, rows=[], issues=[], raw_only_columns=[],
               series=None, truncated=False)
print(json.dumps(dict(version=1, request_id=header['request_id'], ok=True, preview=preview)))
print('unexpected extra output')
"#,
                ErrorCode::OUTPUTINVALID,
            ),
        ] {
            let fixture = Fixture::new(script);
            let error = preview(
                &fixture.0,
                b"",
                DIGEST,
                &json!({"version": 1, "revision": "sample"}),
                1,
                future::pending(),
                || Ok(()),
            )
            .await
            .unwrap_err();
            assert_eq!(error.code, code);
        }
    }

    #[tokio::test]
    async fn delivers_the_source_limit_in_separate_frames_to_the_real_worker() {
        const SOURCE_DIGEST: &str =
            "7114aa21f5c057302ceb73e1b9b9fa397a8916a08ada901f5005180db010825d";
        let mut content = b"Symbol,Quote\n0012,5\n".to_vec();
        content.resize(MAX_SOURCE_BYTES, b'a');
        let mapping = json!({
            "version": 1, "revision": "sample", "encoding": "utf-8",
            "delimiter": ",", "record_kind": "quote_snapshot",
        });

        let result = preview(
            &development_config(),
            &content,
            SOURCE_DIGEST,
            &mapping,
            1,
            future::pending(),
            || Ok(()),
        )
        .await
        .unwrap();
        assert_eq!(result["file_sha256"], SOURCE_DIGEST);
        assert_eq!(result["rows"][0]["raw_fields"], json!(["0012", "5"]));
        assert_eq!(result["truncated"], true);
    }

    #[tokio::test]
    async fn complete_checks_the_real_file_and_reports_row_review() {
        const SOURCE: &[u8] = b"Ticker,Date,Open,High,Low,Close,Shares\n000071,2026-01-06,8,10,7,9,\n000071,2026-01-07,8,10,7,10,200\n";
        const SOURCE_DIGEST: &str =
            "0a3afa84fbc09d578380626056f80f90132c6fd048bfcf1698df3c5402b0bd90";
        let config = development_config();
        let mut mapping = json!({
            "version": 1, "revision": "complete-fixture", "encoding": "utf-8",
            "delimiter": ",", "record_kind": "native_ohlcv", "instrument_kind": "equity",
            "venue": "TEST", "timezone": "UTC", "calendar_id": "fixture-calendar",
            "session_scope": "regular", "timeframe": "1d", "quantity_unit": "shares",
            "price_adjustment": "unadjusted", "volume_semantics": "per_bar",
            "timestamp_convention": "bar_start", "timestamp_format": "%Y-%m-%d",
            "trading_day_format": "%Y-%m-%d", "daily_start_time": "09:30:00",
            "columns": {
                "symbol": "Ticker", "timestamp": "Date", "trading_day": "Date",
                "open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Shares"
            }
        });
        let checked = complete(&config, SOURCE, SOURCE_DIGEST, &mapping, future::pending())
            .await
            .unwrap();
        assert_eq!(checked["mapping_revision"], "complete-fixture");
        assert_eq!(checked["source_record_count"], 2);
        assert_eq!(checked["symbol"], "000071");
        assert!(checked["bars"][0]["bar"]["volume"].is_null());
        assert_eq!(
            checked["bars"][1]["locators"],
            json!([{"record_number": 2, "line_start": 3, "line_end": 3}])
        );

        mapping["columns"]["open"] = json!("Shares");
        mapping["columns"]["volume"] = Value::Null;
        let reviewed = complete(&config, SOURCE, SOURCE_DIGEST, &mapping, future::pending())
            .await
            .unwrap_err();
        assert_eq!(reviewed.code, ErrorCode::DATAUNAVAILABLE);
        assert_eq!(reviewed.details["issue_code"], "invalid_number");
        assert_eq!(reviewed.details["locator"]["record_number"], 1);
    }

    #[tokio::test]
    async fn cancelled_in_flight_child_is_reaped_before_result() {
        let fixture = Fixture::new(
            "import os, pathlib, sys, time\nsys.stdin.buffer.read()\npathlib.Path('entered').write_text(str(os.getpid()), encoding='ascii')\ntime.sleep(60)\n",
        );
        let manifest = json!({});
        let (cancel, cancelled) = tokio::sync::oneshot::channel();
        let request = preview(
            &fixture.0,
            b"",
            DIGEST,
            &manifest,
            1,
            async {
                cancelled.await.unwrap();
            },
            || Ok(()),
        );
        tokio::pin!(request);
        let entered = fixture.0.root.join("entered");
        let pid = tokio::select! {
            result = &mut request => panic!("request finished before cancellation: {result:?}"),
            pid = async {
                loop {
                    if let Ok(value) = fs::read_to_string(&entered)
                        && let Ok(pid) = value.parse::<u32>()
                    {
                        break pid;
                    }
                    tokio::time::sleep(Duration::from_millis(10)).await;
                }
            } => pid,
        };

        cancel.send(()).unwrap();
        assert_eq!(request.await.unwrap_err().code, ErrorCode::CANCELLED);
        assert_ne!(pid, 0);
        #[cfg(target_os = "linux")]
        assert!(!PathBuf::from(format!("/proc/{pid}")).exists());
    }

    #[test]
    fn checks_reply_identity_and_digest() {
        let result = json!({
            "version": 1, "request_id": "first", "ok": true,
            "preview": {
                "file_sha256": DIGEST, "mapping_version": 1, "mapping_revision": "sample",
                "header": [], "raw_header_text": null, "rows": [], "issues": [],
                "raw_only_columns": [], "series": null, "truncated": false
            }
        });
        let frame = encode(&result).unwrap();
        let mapping = json!({"version": 1, "revision": "sample"});
        let request = |request_id, sha256| Header {
            version: VERSION,
            request_id,
            byte_count: 0,
            sha256,
            mapping: &mapping,
            check: Check::Preview { row_limit: 1 },
        };
        assert_eq!(
            decode(&frame, &request("first", DIGEST)).unwrap(),
            result["preview"]
        );
        for (id, hash) in [("second", DIGEST), ("first", "bad-digest")] {
            assert_eq!(
                decode(&frame, &request(id, hash)).unwrap_err().code,
                ErrorCode::OUTPUTINVALID
            );
        }
    }
}
