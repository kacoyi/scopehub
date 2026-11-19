//! Private one-shot NDJSON exchange for an already authorized CSV snapshot.

use std::{
    future::Future,
    io,
    path::{Path, PathBuf},
    process::Stdio,
    time::Duration,
};

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

#[cfg(windows)]
#[path = "local_import_windows.rs"]
mod windows;

const MAX_SOURCE_BYTES: usize = 4 * 1024 * 1024;
const CHUNK_BYTES: usize = 192 * 1024;
const VERSION: u8 = 1;
const CHECK_LOCAL_IMPORT: &str = "market.checkLocalImport";

#[derive(Clone, Copy, Serialize)]
#[serde(untagged)]
enum Check {
    Preview { row_limit: u32 },
    Complete { complete: bool },
}

impl Check {
    fn capability(self) -> &'static str {
        match self {
            Self::Preview { .. } => PREVIEW_LOCAL_IMPORT,
            Self::Complete { .. } => CHECK_LOCAL_IMPORT,
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
    let (mut python, owns_interpreter) = match windows::direct_interpreter(config, &launcher) {
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

fn development_venv(python: &Path) -> Option<&Path> {
    let scripts = python.parent()?;
    let venv = scripts.parent()?;
    let (bin, name) = if cfg!(windows) {
        ("Scripts", "python.exe")
    } else {
        ("bin", "python")
    };
    (python.is_absolute()
        && python.file_name()?.eq_ignore_ascii_case(name)
        && scripts.file_name()?.eq_ignore_ascii_case(bin)
        && venv.file_name()?.eq_ignore_ascii_case(".venv"))
    .then_some(venv)
}

const MAX_VENV_CONFIGURATION: u64 = 4096;

fn managed_cpython_home(venv: &Path) -> Option<PathBuf> {
    use std::{fs::File, io::Read};

    let mut text = String::new();
    File::open(venv.join("pyvenv.cfg"))
        .ok()?
        .take(MAX_VENV_CONFIGURATION + 1)
        .read_to_string(&mut text)
        .ok()?;
    interpreter_home(&text)?.canonicalize().ok()
}

fn interpreter_home(text: &str) -> Option<&Path> {
    if text.len() as u64 > MAX_VENV_CONFIGURATION {
        return None;
    }
    let mut home = None;
    let mut python_312 = false;
    let mut isolated = false;
    for (key, value) in text
        .trim_start_matches('\u{feff}')
        .lines()
        .filter_map(|line| line.split_once('='))
    {
        let key = key.trim();
        let value = value.trim();
        if key.eq_ignore_ascii_case("home") && home.replace(Path::new(value)).is_some() {
            return None;
        }
        if key.eq_ignore_ascii_case("version") || key.eq_ignore_ascii_case("version_info") {
            let version = value.strip_prefix("3.12")?;
            if !version.is_empty()
                && !version.strip_prefix('.').is_some_and(|patch| {
                    !patch.is_empty() && patch.bytes().all(|byte| byte.is_ascii_digit())
                })
            {
                return None;
            }
            python_312 = true;
        }
        if key.eq_ignore_ascii_case("implementation") && !value.eq_ignore_ascii_case("CPython") {
            return None;
        }
        if key.eq_ignore_ascii_case("include-system-site-packages") {
            if !value.eq_ignore_ascii_case("false") {
                return None;
            }
            isolated = true;
        }
    }
    home.filter(|path| path.is_absolute() && python_312 && isolated)
}

#[cfg(not(windows))]
fn native_development_interpreter(python: &Path) -> bool {
    let Some(venv) = development_venv(python) else {
        return false;
    };
    let Some(home) = managed_cpython_home(venv) else {
        return false;
    };
    // uv and standard POSIX symlinks resolve to the actual interpreter named
    // by pyvenv.cfg. Independently configured copies or wrappers stay one-shot.
    let (Ok(actual), Ok(declared)) = (
        python.canonicalize(),
        home.join("python3.12").canonicalize(),
    ) else {
        return false;
    };
    actual == declared && actual.parent() == Some(home.as_path()) && actual.is_file()
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
        || negotiated
            .capabilities
            .iter()
            .any(|name| !matches!(name.as_str(), PREVIEW_LOCAL_IMPORT | CHECK_LOCAL_IMPORT))
    {
        return Err(failure(ErrorCode::PROTOCOLMISMATCH));
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
        (Check::Complete { .. }, Response::Review(reply))
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

    #[cfg(windows)]
    #[tokio::test]
    async fn windows_venv_reaps_owned_leaf_before_recovery() {
        let fixture = Fixture::new(
            r#"import json, os, pathlib, site, sys, time
from importlib.metadata import version
from zoneinfo import ZoneInfo
def normalize(path):
    return os.path.normcase(os.path.realpath(path))
runtime = dict(version=sys.version, libraries=[version('jsonschema'), version('tzdata')],
               paths=[normalize(path) for path in [sys.executable, sys._base_executable,
                      sys.prefix, sys.base_prefix, *site.getsitepackages(), *sys.path]],
               timezone=ZoneInfo('Asia/Shanghai').key)
pathlib.Path('owned').write_text(json.dumps([os.getpid(), os.getppid(), runtime]), encoding='ascii')
deadline = time.monotonic() + 15
while not pathlib.Path('release').exists() and time.monotonic() < deadline:
    time.sleep(0.02)
"#,
        );
        async fn witness(path: &Path) -> (u32, u32, Value) {
            tokio::time::timeout(Duration::from_secs(5), async {
                loop {
                    if let Ok(data) = fs::read_to_string(path)
                        && let Ok(facts) = serde_json::from_str(&data)
                    {
                        return facts;
                    }
                    tokio::time::sleep(Duration::from_millis(10)).await;
                }
            })
            .await
            .expect("the actual interpreter must report its PID and locked environment")
        }
        let owned = fixture.0.root.join("owned");
        let release = fixture.0.root.join("release");

        let mut launcher = fixture
            .0
            .python_command()
            .env("PYTHONTZPATH", "")
            .args(["-m", "worker_runtime.local_import"])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .kill_on_drop(true)
            .spawn()
            .unwrap();
        let launcher_pid = launcher.id().unwrap();
        let (old_leaf, old_parent, old_runtime) = witness(&owned).await;
        assert_eq!(old_parent, launcher_pid);
        assert_ne!(
            old_leaf, launcher_pid,
            "the old Windows launcher must have a distinct leaf"
        );
        fs::write(&release, "").unwrap();
        assert!(
            tokio::time::timeout(Duration::from_secs(5), launcher.wait())
                .await
                .unwrap()
                .unwrap()
                .success()
        );
        fs::remove_file(&owned).unwrap();
        fs::remove_file(&release).unwrap();

        let (mut command, owns_interpreter) = command(&fixture.0);
        assert!(
            owns_interpreter,
            "the normal pinned Windows interpreter must qualify"
        );
        let child = command.spawn().unwrap();
        let child_pid = child.id().unwrap();
        let mut process = Process {
            runtime: Handle::current(),
            child: Some((child, worker_slot().unwrap())),
            terminal_error: None,
        };
        let (worker, parent, runtime) = witness(&owned).await;
        assert_eq!(
            worker, child_pid,
            "the Rust-owned child must be the actual interpreter"
        );
        assert_eq!(parent, std::process::id());
        assert!(
            runtime == old_runtime,
            "the locked venv runtime and package environment changed"
        );
        process.reclaim().unwrap().wait().await.unwrap();
        assert!(process.child.is_none());
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
