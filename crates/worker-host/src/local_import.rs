//! Private one-shot NDJSON exchange for an already authorized CSV snapshot.

use std::{future::Future, process::Stdio, time::Duration};

use base64::{Engine, engine::general_purpose::STANDARD};
use scopehub_application::{PREVIEW_LOCAL_IMPORT, failure};
use scopehub_domain::protocol::{Error, ErrorCode, LocalImportRowLocator, PreviewIssue};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader},
    process::Child,
    runtime::Handle,
    sync::OwnedSemaphorePermit,
};
use uuid::Uuid;

use crate::{
    POLICY, cleanup,
    market::{WorkerConfig, protocol_failure, worker_slot},
    max_message_bytes, negotiate_worker, read_frame,
};

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
}

impl Process {
    fn reclaim(&mut self) -> Option<cleanup::PendingCleanup> {
        let (mut child, permit) = self.child.take()?;
        let grace =
            Duration::from_millis(POLICY["cancel_grace_ms"].as_u64().expect("cancel grace"));
        let _ = child.start_kill();
        let _runtime = self.runtime.enter();
        Some(cleanup::PendingCleanup::start(
            async move {
                cleanup::reclaim(child, grace).await;
                drop(permit);
            },
            grace,
        ))
    }
}

impl Drop for Process {
    fn drop(&mut self) {
        let _ = self.reclaim();
    }
}

/// Preview immutable bytes already read and verified by Rust.
pub async fn preview(
    config: &WorkerConfig,
    content: &[u8],
    sha256: &str,
    mapping: &Value,
    row_limit: u32,
    cancelled: impl Future<Output = ()>,
) -> Result<Value, Error> {
    analyze(
        config,
        content,
        sha256,
        mapping,
        Check::Preview { row_limit },
        cancelled,
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
    let request_id = Uuid::new_v4().to_string();
    let header = encode(&Header {
        version: VERSION,
        request_id: &request_id,
        byte_count: content.len(),
        sha256,
        mapping,
        check,
    })?;
    let mut process = Process {
        runtime: Handle::current(),
        child: None,
    };
    let result = tokio::select! {
        biased;
        () = cancelled => Err(failure(ErrorCode::CANCELLED)),
        result = tokio::time::timeout(config.deadline, exchange(config, &mut process, &header, content, &request_id, sha256, check)) =>
            result.unwrap_or_else(|_| Err(failure(ErrorCode::TIMEOUT))),
    };
    if let Some(mut cleanup) = process.reclaim() {
        let _ = cleanup.wait().await;
    }
    result
}

async fn exchange(
    config: &WorkerConfig,
    process: &mut Process,
    header: &[u8],
    content: &[u8],
    request_id: &str,
    sha256: &str,
    check: Check,
) -> Result<Value, Error> {
    let permit = worker_slot()?;
    let child = config
        .python_command()
        // Resolve IANA zones from the locked data package in this worker.
        .env("PYTHONTZPATH", "")
        .args(["-m", "worker_runtime.local_import"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .kill_on_drop(true)
        .spawn()
        .map_err(|_| failure(ErrorCode::WORKEREXITED))?;
    let (child, _) = process.child.insert((child, permit));
    let mut input = child.stdin.take().expect("piped stdin");
    let mut output = BufReader::new(child.stdout.take().expect("piped stdout"));
    let hello = read_frame(&mut output).await.map_err(protocol_failure)?;
    let negotiated =
        negotiate_worker(&hello, "market", &[check.capability()]).map_err(protocol_failure)?;
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
            "request_id": request_id,
            "index": index,
            "data": STANDARD.encode(bytes),
        }))?;
        input.write_all(&chunk).await.map_err(worker_io)?;
    }
    drop(input);

    let frame = read_frame(&mut output).await.map_err(protocol_failure)?;
    let result = decode(&frame, request_id, sha256, check);
    if !output.fill_buf().await.map_err(worker_io)?.is_empty() {
        return Err(failure(ErrorCode::OUTPUTINVALID));
    }
    let status = child.wait().await.map_err(worker_io)?;
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

fn decode(frame: &[u8], request_id: &str, sha256: &str, check: Check) -> Result<Value, Error> {
    let body = frame.strip_suffix(b"\n").unwrap_or(frame);
    let body = body.strip_suffix(b"\r").unwrap_or(body);
    if body.contains(&b'\r') || body.contains(&b'\n') {
        return Err(failure(ErrorCode::OUTPUTINVALID));
    }
    let response = serde_json::from_slice(body).map_err(|_| failure(ErrorCode::OUTPUTINVALID))?;
    match (check, response) {
        (Check::Preview { .. }, Response::Preview(reply))
            if reply.version == VERSION
                && reply.request_id == request_id
                && reply.ok
                && reply.preview["file_sha256"].as_str() == Some(sha256) =>
        {
            Ok(reply.preview)
        }
        (Check::Complete { .. }, Response::Complete(reply))
            if reply.version == VERSION
                && reply.request_id == request_id
                && reply.ok
                && reply.publication["file_sha256"].as_str() == Some(sha256) =>
        {
            Ok(reply.publication)
        }
        (Check::Complete { .. }, Response::Review(reply))
            if reply.version == VERSION && reply.request_id == request_id && !reply.ok =>
        {
            Err(csv_review_required(reply))
        }
        (_, Response::Failure(reply))
            if reply.version == VERSION && reply.request_id == request_id && !reply.ok =>
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
    async fn refuses_a_worker_with_other_capabilities_before_sending_data() {
        let fixture = Fixture::with_capabilities(
            "import sys\nif sys.stdin.buffer.read(1):\n    open('unexpected-input', 'w').close()\n",
            &[PREVIEW_LOCAL_IMPORT, "market.getBars"],
        );
        let error = preview(&fixture.0, b"", DIGEST, &json!({}), 1, future::pending())
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
                "import json, sys\nh = json.loads(sys.stdin.readline())\nsys.stdin.buffer.read()\nprint(json.dumps(dict(version=1, request_id=h['request_id'], ok=True, preview={'file_sha256': h['sha256']})))\nprint('unexpected extra output')\n",
                ErrorCode::OUTPUTINVALID,
            ),
        ] {
            let fixture = Fixture::new(script);
            let error = preview(&fixture.0, b"", DIGEST, &json!({}), 1, future::pending())
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
        let request = preview(&fixture.0, b"", DIGEST, &manifest, 1, async {
            cancelled.await.unwrap();
        });
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
            "version": 1, "request_id": "first", "ok": true, "preview": {"file_sha256": DIGEST},
        });
        let frame = encode(&result).unwrap();
        let check = Check::Preview { row_limit: 1 };
        assert_eq!(
            decode(&frame, "first", DIGEST, check).unwrap(),
            result["preview"]
        );
        for (id, hash) in [("second", DIGEST), ("first", "bad-digest")] {
            assert_eq!(
                decode(&frame, id, hash, check).unwrap_err().code,
                ErrorCode::OUTPUTINVALID
            );
        }
    }
}
