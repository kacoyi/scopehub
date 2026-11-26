//! One-shot native transport for an already admitted and confirmed computation.

use std::{
    collections::HashSet,
    fs, future,
    io::{self, Write},
    path::{Path, PathBuf},
    process::{ExitStatus, Stdio},
    thread,
    time::{Duration, Instant as StdInstant},
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
    task::{JoinHandle, spawn_blocking},
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
/// to observe the terminal outcome with a bounded wait for cleanup.
pub async fn compute(
    config: &WorkerConfig,
    request: &IndicatorCompute11,
    cancelled: impl Future<Output = ()>,
) -> Result<IndicatorComputed11, Error> {
    compute_retaining_cleanup(config, request, cancelled, &mut None).await
}

async fn compute_retaining_cleanup(
    config: &WorkerConfig,
    request: &IndicatorCompute11,
    cancelled: impl Future<Output = ()>,
    pending_cleanup: &mut Option<PendingCleanup<io::Result<()>>>,
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
            let scratch = Scratch::new(permit)?;
            let run = process.0.insert(Run {
                supervisor: None,
                scratch,
                runtime: Handle::current(),
            });
            let mut child = command(config, &run.scratch)?.spawn().map_err(worker_io)?;
            let input = child.stdin.take().expect("piped stdin");
            let (child, input) = run.supervisor.insert((child, input));
            exchange(child, input, request, &frame).await
        }) => outcome.unwrap_or_else(|_| Err(error(ErrorCode::TIMEOUT))),
    };
    let cleaned = if let Some(mut cleanup) = process.cleanup() {
        // Retain ownership in the reaper if the cleanup budget expires.
        match cleanup.wait().await {
            Ok(removed) => removed.is_ok(),
            Err(failure) => {
                if failure == ProtocolError::Timeout {
                    *pending_cleanup = Some(cleanup);
                }
                eprintln!(
                    r#"{{"level":"warning","code":"INDICATOR_CLEANUP_WAIT_FAILED","reason":"{failure:?}"}}"#
                );
                false
            }
        }
    } else {
        true
    };
    if outcome.is_ok() {
        tokio::select! {
            biased;
            () = &mut cancelled => return Err(error(ErrorCode::CANCELLED)),
            () = future::ready(()) => {}
        }
        if Instant::now() >= deadline {
            return Err(error(ErrorCode::TIMEOUT));
        }
        if !cleaned {
            return Err(error(ErrorCode::WORKEREXITED));
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
        .current_dir(scratch.path())
        .env("TEMP", scratch.path())
        .env("TMP", scratch.path())
        .env("TMPDIR", scratch.path())
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
        .arg(
            (POLICY["indicator_memory_limit_mb"]
                .as_u64()
                .expect("configured indicator memory limit")
                * 1024
                * 1024)
                .to_string(),
        )
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
    let hello = receive_frame(&mut output, child).await?;
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

    let terminal = decode_response(&receive_frame(&mut output, child).await?, request)?;
    if !output.fill_buf().await.map_err(worker_io)?.is_empty() {
        return Err(error(ErrorCode::OUTPUTINVALID));
    }
    if !child.wait().await.map_err(worker_io)?.success() {
        return Err(error(ErrorCode::WORKEREXITED));
    }
    Ok(terminal)
}

async fn receive_frame(
    output: &mut BufReader<tokio::process::ChildStdout>,
    child: &mut Child,
) -> Result<Vec<u8>, Error> {
    match read_frame(output).await {
        Ok(frame) => Ok(frame),
        Err(ProtocolError::WorkerExited) => {
            Err(exit_failure(child.wait().await.map_err(worker_io)?))
        }
        Err(failure) => Err(protocol_failure(failure)),
    }
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

struct Scratch {
    path: PathBuf,
    // Keep admission with the directory through the last filesystem call.
    _permit: OwnedSemaphorePermit,
}

impl Scratch {
    fn new(permit: OwnedSemaphorePermit) -> Result<Self, Error> {
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
        Ok(Self {
            path: directory,
            _permit: permit,
        })
    }

    fn path(&self) -> &Path {
        &self.path
    }

    fn remove(
        self,
        deadline: Instant,
        mut remove: impl FnMut(&Path) -> io::Result<()> + Send + 'static,
    ) -> JoinHandle<io::Result<()>> {
        spawn_blocking(move || {
            let result =
                retry_scratch_removal(deadline.into_std(), StdInstant::now, thread::sleep, || {
                    remove(self.path())
                });
            result.inspect_err(report_scratch_failure)
        })
    }
}

fn remove_scratch(path: &Path) -> io::Result<()> {
    match fs::remove_dir_all(path) {
        Err(failure) if failure.kind() == io::ErrorKind::NotFound => {
            match fs::symlink_metadata(path) {
                Err(missing) if missing.kind() == io::ErrorKind::NotFound => Ok(()),
                Err(other) => Err(other),
                Ok(_) => Err(failure),
            }
        }
        result => result,
    }
}

fn retry_scratch_removal(
    deadline: StdInstant,
    now: impl Fn() -> StdInstant,
    mut sleep: impl FnMut(Duration),
    mut remove: impl FnMut() -> io::Result<()>,
) -> io::Result<()> {
    loop {
        // Even a late reaper must attempt the initial removal once.
        let Err(failure) = remove() else {
            return Ok(());
        };
        let transient = failure.kind() == io::ErrorKind::Interrupted
            // ERROR_SHARING_VIOLATION or ERROR_LOCK_VIOLATION.
            || (cfg!(windows) && matches!(failure.raw_os_error(), Some(32 | 33)));
        let remaining = deadline.saturating_duration_since(now());
        if !transient || remaining.is_zero() {
            return Err(failure);
        }
        sleep(remaining.min(Duration::from_millis(10)));
        if now() >= deadline {
            return Err(failure);
        }
    }
}

fn report_scratch_failure(failure: &io::Error) {
    let kind = failure.kind();
    let os_code = failure
        .raw_os_error()
        .map_or("null".to_owned(), |code| code.to_string());
    eprintln!(
        r#"{{"level":"warning","code":"INDICATOR_SCRATCH_REMOVE_FAILED","kind":"{kind:?}","os_code":{os_code}}}"#
    );
}

#[derive(Default)]
struct Process(Option<Run>);

struct Run {
    supervisor: Option<(Child, ChildStdin)>,
    scratch: Scratch,
    runtime: Handle,
}

impl Process {
    fn cleanup(&mut self) -> Option<PendingCleanup<io::Result<()>>> {
        let Run {
            supervisor,
            scratch,
            runtime,
        } = self.0.take()?;
        let child = supervisor.map(|(child, input)| {
            // EOF tells the supervisor to reclaim its process tree.
            drop(input);
            child
        });
        let grace =
            Duration::from_millis(POLICY["cancel_grace_ms"].as_u64().expect("cancel grace"));
        let _context = runtime.enter();
        let deadline = Instant::now() + grace;
        Some(PendingCleanup::start_at(
            async move {
                if let Some(mut child) = child {
                    loop {
                        if child.wait().await.is_ok() {
                            break;
                        }
                        tokio::time::sleep(grace).await;
                    }
                }
                scratch
                    .remove(deadline, remove_scratch)
                    .await
                    .map_err(io::Error::other)
                    .inspect_err(report_scratch_failure)?
            },
            deadline,
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

#[cfg(test)]
mod scratch_tests {
    use std::{
        cell::Cell,
        sync::{Arc, mpsc},
    };

    use tokio::sync::{Semaphore, oneshot};

    use super::*;

    fn acquire_scratch() -> (Scratch, Arc<Semaphore>) {
        let admission = Arc::new(Semaphore::new(1));
        let scratch = Scratch::new(admission.clone().try_acquire_owned().unwrap()).unwrap();
        (scratch, admission)
    }

    #[test]
    fn retries_interrupted_removal_of_owned_scratch() {
        let (scratch, _admission) = acquire_scratch();
        fs::write(scratch.path().join("synthetic-output"), b"temporary").unwrap();
        let mut interrupted = false;
        let start = StdInstant::now();
        let clock = Cell::new(start);
        retry_scratch_removal(
            start + Duration::from_secs(1),
            || clock.get(),
            |delay| clock.set(clock.get() + delay),
            || {
                if std::mem::replace(&mut interrupted, true) {
                    remove_scratch(scratch.path())
                } else {
                    Err(io::ErrorKind::Interrupted.into())
                }
            },
        )
        .unwrap();
        assert_eq!(
            fs::symlink_metadata(scratch.path()).unwrap_err().kind(),
            io::ErrorKind::NotFound
        );
    }

    #[test]
    fn reports_permanent_and_out_of_budget_removal_errors() {
        let now = StdInstant::now();
        for (deadline, kind) in [
            (
                now + Duration::from_secs(1),
                io::ErrorKind::PermissionDenied,
            ),
            (now, io::ErrorKind::Interrupted),
        ] {
            let mut attempts = 0;
            let failure = retry_scratch_removal(
                deadline,
                || now,
                |_| panic!("there is no retry budget or the failure is terminal"),
                || {
                    attempts += 1;
                    Err(kind.into())
                },
            )
            .unwrap_err();
            assert_eq!(failure.kind(), kind);
            assert_eq!(attempts, 1);
        }
    }

    #[test]
    fn checks_the_deadline_after_each_backoff() {
        let start = StdInstant::now();
        for (completions_ms, backoffs_ms) in [
            (&[995][..], &[5][..]),
            (&[900, 995][..], &[10, 5][..]),
            (&[990][..], &[10][..]),
        ] {
            let clock = Cell::new(start);
            let mut completions = completions_ms.iter();
            let mut backoffs = backoffs_ms.iter();
            let failure = retry_scratch_removal(
                start + Duration::from_secs(1),
                || clock.get(),
                |delay| {
                    assert_eq!(delay, Duration::from_millis(*backoffs.next().unwrap()));
                    clock.set(clock.get() + delay);
                },
                || {
                    let finished_ms = *completions.next().expect("removal started at the deadline");
                    clock.set(start + Duration::from_millis(finished_ms));
                    Err(io::ErrorKind::Interrupted.into())
                },
            )
            .unwrap_err();
            assert_eq!(failure.kind(), io::ErrorKind::Interrupted);
            assert!(completions.next().is_none() && backoffs.next().is_none());
        }
    }

    #[test]
    fn slow_removal_keeps_scratch_and_permit_after_the_caller_times_out() {
        for queued in [false, true] {
            let runtime = tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .max_blocking_threads(1)
                .build()
                .unwrap();
            runtime.block_on(async {
                let (scratch, admission) = acquire_scratch();
                let path = scratch.path().to_owned();
                let output = path.join("pending-output");
                fs::write(&output, b"synthetic scratch data").unwrap();
                let (unqueue, queue) = mpsc::channel();
                if queued {
                    // Occupy the only blocking thread before submitting removal.
                    spawn_blocking(move || queue.recv_timeout(Duration::from_secs(5)));
                }
                let (release, held) = mpsc::channel();
                let (started, mut entered) = oneshot::channel();
                let mut started = Some(started);
                let deadline = Instant::now() + Duration::from_millis(150);
                let removal = scratch.remove(deadline, move |path| {
                    started.take().unwrap().send(()).unwrap();
                    held.recv_timeout(Duration::from_secs(5)).unwrap();
                    remove_scratch(path)
                });
                let mut cleanup =
                    PendingCleanup::start_at(async { removal.await.unwrap() }, deadline);
                if !queued {
                    (&mut entered).await.unwrap();
                }
                assert!(matches!(cleanup.wait().await, Err(ProtocolError::Timeout)));
                assert_eq!(fs::read(&output).unwrap(), b"synthetic scratch data");
                assert_eq!(admission.available_permits(), 0);
                if queued {
                    unqueue.send(()).unwrap();
                    entered.await.unwrap();
                    assert_eq!(fs::read(&output).unwrap(), b"synthetic scratch data");
                    assert_eq!(admission.available_permits(), 0);
                }
                release.send(()).unwrap();
                cleanup.finish().await;
                assert_eq!(
                    fs::symlink_metadata(&path).unwrap_err().kind(),
                    io::ErrorKind::NotFound
                );
                assert_eq!(admission.available_permits(), 1);
            });
        }
    }

    #[cfg(windows)]
    #[tokio::test]
    async fn retries_when_a_windows_handle_temporarily_denies_delete_sharing() {
        use std::os::windows::fs::OpenOptionsExt;

        let (scratch, _admission) = acquire_scratch();
        let path = scratch.path().to_owned();
        let mut handle = Some(
            fs::OpenOptions::new()
                .read(true)
                .share_mode(0x0000_0003) // FILE_SHARE_READ | FILE_SHARE_WRITE
                .custom_flags(0x0200_0000) // FILE_FLAG_BACKUP_SEMANTICS
                .open(scratch.path())
                .unwrap(),
        );
        scratch
            .remove(Instant::now() + Duration::from_secs(1), move |path| {
                let removal = remove_scratch(path);
                if let Some(blocking_handle) = handle.take() {
                    assert_eq!(removal.as_ref().unwrap_err().raw_os_error(), Some(32));
                    drop(blocking_handle);
                }
                removal
            })
            .await
            .unwrap()
            .unwrap();
        assert_eq!(
            fs::symlink_metadata(path).unwrap_err().kind(),
            io::ErrorKind::NotFound
        );
    }
}
