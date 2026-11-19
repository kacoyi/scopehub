//! Lazy synthetic worker with correlated reads and a dedicated replay lifecycle.

use std::{
    path::{Path, PathBuf},
    process::Stdio,
    sync::{Arc, LazyLock},
    time::{Duration, SystemTime, UNIX_EPOCH},
};

use scopehub_application::{
    Actor, MarketFuture, MarketLifecycle, MarketPort, MarketRegistry, MarketStatus,
    READ_OPERATIONS, SUBSCRIBE, ShutdownFuture, SubscriptionFuture, SubscriptionPort, failure,
};
use scopehub_domain::protocol::{
    Error, ErrorCode, MarketSubscriptionInput, MarketSubscriptionOutput, Message, Response,
};
use serde_json::{Value, json};
use tokio::{
    io::{AsyncWriteExt, BufReader},
    process::{Child, ChildStdin, ChildStdout, Command},
    sync::{OwnedSemaphorePermit, Semaphore},
};
use uuid::Uuid;

use crate::{ProtocolError, cleanup, decode_line, max_message_bytes, read_frame};

// Retained workers count against the same bound as live workers until reaped.
static WORKER_SLOTS: LazyLock<Arc<Semaphore>> = LazyLock::new(|| {
    Arc::new(Semaphore::new(
        crate::POLICY["max_pending_requests"]
            .as_u64()
            .expect("worker limit") as usize,
    ))
});

pub struct WorkerConfig {
    pub python: PathBuf,
    pub root: PathBuf,
    pub deadline: Duration,
}

impl WorkerConfig {
    /// Repository development only. Packaged interpreters are an M5 deliverable.
    pub fn development(root: &Path) -> Self {
        Self {
            python: root.join(if cfg!(windows) {
                ".venv/Scripts/python.exe"
            } else {
                ".venv/bin/python"
            }),
            root: root.into(),
            deadline: Duration::from_secs(5),
        }
    }

    fn command(&self) -> Command {
        let mut command = Command::new(&self.python);
        command.env_clear().current_dir(&self.root);
        for key in ["SYSTEMROOT", "WINDIR", "TEMP", "TMP"] {
            if let Some(value) = std::env::var_os(key) {
                command.env(key, value);
            }
        }
        command
            .env(
                "PYTHONPATH",
                std::env::join_paths([self.root.clone(), self.root.join("python")])
                    .expect("trusted paths"),
            )
            .env("PYTHONIOENCODING", "utf-8")
            .env("SCOPEHUB_WORKER_WIRE_VERSION", "1.1")
            .args(["-m", "worker_runtime", "--role", "market", "--demo"]);
        command
    }
}

struct Session {
    version: String,
    capabilities: Vec<String>,
    child: Child,
    input: ChildStdin,
    output: BufReader<ChildStdout>,
    permit: OwnedSemaphorePermit,
}

pub struct MarketWorker {
    config: Option<WorkerConfig>,
    session: Option<Session>,
    subscription: Option<(String, String)>,
    cleanup: Option<cleanup::PendingCleanup>,
}

fn protocol_failure(error: ProtocolError) -> Error {
    failure(match error {
        ProtocolError::ResourceLimit => ErrorCode::RESOURCELIMIT,
        ProtocolError::IncompatibleWorker => ErrorCode::PROTOCOLMISMATCH,
        ProtocolError::WorkerExited | ProtocolError::Io => ErrorCode::WORKEREXITED,
        ProtocolError::Timeout => ErrorCode::TIMEOUT,
        ProtocolError::InvalidMessage => ErrorCode::OUTPUTINVALID,
    })
}

impl MarketWorker {
    pub fn new(config: Option<WorkerConfig>) -> Self {
        Self {
            config,
            session: None,
            subscription: None,
            cleanup: None,
        }
    }

    pub fn process_id(&self) -> Option<u32> {
        self.session.as_ref().and_then(|session| session.child.id())
    }

    pub async fn shutdown(&mut self) -> Result<(), Error> {
        if let Some(session) = self.session.take() {
            let pending = self.subscription.take();
            let grace = Duration::from_millis(
                crate::POLICY["cancel_grace_ms"]
                    .as_u64()
                    .expect("cancel grace"),
            );
            let budget = if pending.is_some() { grace * 2 } else { grace };
            self.cleanup = Some(cleanup::PendingCleanup::start(
                session.close(pending, grace),
                budget,
            ));
        }
        let Some(cleanup) = &mut self.cleanup else {
            return Ok(());
        };
        let result = cleanup.wait().await;
        if result != Err(ProtocolError::Timeout) {
            self.cleanup = None;
        }
        result.map_err(|error| {
            eprintln!(r#"{{"level":"warning","code":"MARKET_WORKER_CLEANUP_PENDING"}}"#);
            protocol_failure(error)
        })
    }

    async fn start(&mut self) -> Result<(), Error> {
        self.shutdown().await?;
        let config = self
            .config
            .as_ref()
            .ok_or_else(|| failure(ErrorCode::PROVIDERNOTCONFIGURED))?;
        let deadline = config.deadline;
        let permit = Arc::clone(&WORKER_SLOTS)
            .try_acquire_owned()
            .map_err(|_| failure(ErrorCode::BUSY))?;
        let mut child = config
            .command()
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .kill_on_drop(true)
            .spawn()
            .map_err(|_| failure(ErrorCode::WORKEREXITED))?;
        let input = child.stdin.take().expect("piped stdin");
        let output = BufReader::new(child.stdout.take().expect("piped stdout"));
        self.session = Some(Session {
            child,
            input,
            output,
            version: String::new(),
            capabilities: Vec::new(),
            permit,
        });
        let session = self.session.as_mut().expect("started session");
        let result = tokio::time::timeout(deadline, async {
            let frame = read_frame(&mut session.output).await?;
            let hello = crate::negotiate_worker(
                &frame,
                "market",
                &["instrument.search", "market.getBars"],
            )?;
            session.version = hello.version;
            session.capabilities = hello.capabilities;
            Ok(())
        })
        .await
        .unwrap_or(Err(ProtocolError::Timeout));
        if let Err(error) = result {
            let _ = self.shutdown().await;
            return Err(protocol_failure(error));
        }
        Ok(())
    }

    async fn exchange(&mut self, operation: &str, payload: &Value) -> Result<Value, Error> {
        if !READ_OPERATIONS.contains(&operation) {
            return Err(failure(ErrorCode::UNSUPPORTEDCAPABILITY));
        }
        if self.session.is_none() {
            self.start().await?;
        }
        let paged = operation == "market.readHistoryPage";
        let session = self.session.as_ref().expect("started session");
        if !session.supports(operation, paged)
            || paged
                && payload["include_stream_position"] == true
                && !session.supports(SUBSCRIBE, true)
        {
            return Err(failure(ErrorCode::UNSUPPORTEDCAPABILITY));
        }
        let deadline = self.config.as_ref().expect("configured session").deadline;
        let id = Uuid::new_v4().to_string();
        let frame = request_frame(operation, payload, &id, None, paged)?;
        let session = self.session.as_mut().expect("started session");
        let result = tokio::time::timeout(deadline, async {
            session.send(&frame).await?;
            let response = session.receive().await?;
            match response {
                Message::MarketHistoryPageResponse(response)
                    if paged && response.request_id == id =>
                {
                    serde_json::to_value(response.result)
                        .map(Ok)
                        .map_err(|_| ProtocolError::InvalidMessage)
                }
                Message::MarketHistoryPageError(response) if paged && response.request_id == id => {
                    Ok(Err(failure(response.error.code)))
                }
                Message::Response(response) if !paged => match *response {
                    Response::SuccessResponse(response) if response.request_id == id => {
                        serde_json::to_value(response.result)
                            .map(Ok)
                            .map_err(|_| ProtocolError::InvalidMessage)
                    }
                    Response::ErrorResponse(response) if response.request_id == id => {
                        // Provider text/details are untrusted and may include secrets.
                        Ok(Err(failure(response.error.code)))
                    }
                    _ => Err(ProtocolError::InvalidMessage),
                },
                _ => Err(ProtocolError::InvalidMessage),
            }
        })
        .await
        .unwrap_or(Err(ProtocolError::Timeout));
        match result {
            Ok(output) => output,
            Err(error) => {
                let _ = self.shutdown().await;
                Err(protocol_failure(error))
            }
        }
    }

    async fn subscribe(
        &mut self,
        input: &MarketSubscriptionInput,
    ) -> Result<MarketSubscriptionOutput, Error> {
        let session = self
            .session
            .as_mut()
            .ok_or_else(|| failure(ErrorCode::WORKEREXITED))?;
        if !session.supports(SUBSCRIBE, true) || self.subscription.is_some() {
            return Err(failure(ErrorCode::UNSUPPORTEDCAPABILITY));
        }
        let request_id = Uuid::new_v4().to_string();
        let task_id = Uuid::new_v4().to_string();
        let frame = request_frame(SUBSCRIBE, &json!(input), &request_id, Some(&task_id), true)?;
        self.subscription = Some((request_id.clone(), task_id));
        session.send(&frame).await.map_err(protocol_failure)?;
        match session.receive().await.map_err(protocol_failure)? {
            Message::MarketSubscriptionResponse(reply) if reply.request_id == request_id => {
                if matches!(
                    reply.result,
                    MarketSubscriptionOutput::StreamBackfillRequired(_)
                ) {
                    self.subscription = None;
                }
                Ok(reply.result)
            }
            Message::MarketSubscriptionError(reply) if reply.request_id == request_id => {
                self.subscription = None;
                Err(failure(reply.error.code))
            }
            _ => Err(failure(ErrorCode::OUTPUTINVALID)),
        }
    }
}

impl Session {
    async fn close(mut self, pending: Option<(String, String)>, grace: Duration) {
        if let Some((request_id, task_id)) = pending {
            let _ = tokio::time::timeout(grace, self.cancel(request_id, task_id)).await;
        }
        let Self {
            child,
            input,
            output,
            permit,
            ..
        } = self;
        drop(input);
        drop(output);
        cleanup::reclaim(child, grace).await;
        drop(permit);
    }

    async fn cancel(&mut self, request_id: String, task_id: String) -> Result<(), ProtocolError> {
        let cancel = json!({
            "protocol_version": "1.1", "type": "cancel", "message_id": Uuid::new_v4().to_string(),
            "timestamp_ms": now_ms(), "request_id": request_id, "task_id": task_id,
        });
        self.send(&encode(&cancel).map_err(|_| ProtocolError::InvalidMessage)?)
            .await?;
        loop {
            let frame = read_frame(&mut self.output).await?;
            if let Ok(Message::MarketSubscriptionError(reply)) = decode_line(&frame)
                && reply.request_id == request_id
                && reply.error.code == ErrorCode::CANCELLED
            {
                return Ok(());
            }
        }
    }

    fn supports(&self, name: &str, current: bool) -> bool {
        (!current || self.version == "1.1") && self.capabilities.iter().any(|op| op == name)
    }

    async fn send(&mut self, frame: &[u8]) -> Result<(), ProtocolError> {
        self.input
            .write_all(frame)
            .await
            .map_err(|_| ProtocolError::Io)?;
        self.input.flush().await.map_err(|_| ProtocolError::Io)
    }

    async fn receive(&mut self) -> Result<Message, ProtocolError> {
        decode_line(&read_frame(&mut self.output).await?)
    }
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

fn encode(message: &Value) -> Result<Vec<u8>, Error> {
    let mut frame = serde_json::to_vec(message).map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
    frame.push(b'\n');
    if frame.len() > max_message_bytes() {
        return Err(failure(ErrorCode::RESOURCELIMIT));
    }
    decode_line(&frame).map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
    Ok(frame)
}

fn request_frame(
    operation: &str,
    payload: &Value,
    id: &str,
    task_id: Option<&str>,
    current: bool,
) -> Result<Vec<u8>, Error> {
    encode(&json!({
        "protocol_version": if current { "1.1" } else { "1.0" },
        "type": "request", "message_id": Uuid::new_v4().to_string(),
        "request_id": id, "task_id": task_id, "timestamp_ms": now_ms(),
        "operation": operation, "payload": payload,
    }))
}

impl MarketPort for MarketWorker {
    fn request<'a>(&'a mut self, operation: &'a str, payload: &'a Value) -> MarketFuture<'a> {
        Box::pin(self.exchange(operation, payload))
    }
}

impl SubscriptionPort for MarketWorker {
    fn open_subscription<'a>(
        &'a mut self,
        input: &'a MarketSubscriptionInput,
    ) -> SubscriptionFuture<'a, MarketSubscriptionOutput> {
        Box::pin(self.subscribe(input))
    }

    fn next_event(&mut self) -> SubscriptionFuture<'_, Message> {
        Box::pin(async {
            if self.subscription.is_none() {
                return Err(failure(ErrorCode::UNSUPPORTEDCAPABILITY));
            }
            self.session
                .as_mut()
                .expect("active subscription")
                .receive()
                .await
                .map_err(protocol_failure)
        })
    }

    fn restart_subscription(&mut self, attempt: u32) -> SubscriptionFuture<'_, ()> {
        Box::pin(async move {
            self.shutdown().await?;
            // Attempts are one-based and bounded by the registry's runtime policy.
            tokio::time::sleep(Duration::from_millis(100) * 2_u32.pow(attempt - 1)).await;
            Ok(())
        })
    }

    fn complete_subscription(&mut self) {
        self.subscription = None;
    }
}

impl MarketLifecycle for MarketWorker {
    fn status(&self) -> MarketStatus {
        MarketStatus {
            process_id: self.process_id(),
        }
    }

    fn shutdown(&mut self) -> ShutdownFuture<'_> {
        Box::pin(MarketWorker::shutdown(self))
    }
}

/// A replay owns one worker at a time and one deadline across replacements.
/// Both native and browser supply a bridge lifetime and progress sink.
pub async fn run_subscription(
    config: Option<WorkerConfig>,
    actor: Actor,
    payload: Value,
    cancelled: impl std::future::Future<Output = ()>,
    publish: impl FnMut(Value) -> Result<(), Error> + Send,
) -> Result<Value, Error> {
    let deadline = config
        .as_ref()
        .map_or(Duration::from_secs(5), |config| config.deadline);
    run_owned_subscription(
        MarketWorker::new(config),
        deadline,
        actor,
        payload,
        cancelled,
        publish,
    )
    .await
}

async fn run_owned_subscription(
    worker: impl SubscriptionPort + MarketLifecycle,
    deadline: Duration,
    actor: Actor,
    payload: Value,
    cancelled: impl std::future::Future<Output = ()>,
    publish: impl FnMut(Value) -> Result<(), Error> + Send,
) -> Result<Value, Error> {
    let mut registry = MarketRegistry::new(worker);
    let result = tokio::select! {
        biased;
        () = cancelled => Err(failure(ErrorCode::CANCELLED)),
        result = tokio::time::timeout(deadline, registry.subscribe(actor, payload, publish)) =>
            result.unwrap_or_else(|_| Err(failure(ErrorCode::TIMEOUT))),
    };
    // The dedicated worker is reclaimed on completion as well as on rejection,
    // disconnect, timeout and explicit cancellation.
    let cleanup = registry.shutdown().await;
    result.and_then(|value| cleanup.map(|()| value))
}

#[cfg(test)]
#[path = "market_recovery_tests.rs"]
mod recovery_tests;
