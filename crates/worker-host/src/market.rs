//! One lazy, reusable history worker. No implicit retry or external provider.

use std::{
    path::{Path, PathBuf},
    process::Stdio,
    time::{Duration, SystemTime, UNIX_EPOCH},
};

use scopehub_application::{
    MarketFuture, MarketLifecycle, MarketPort, MarketStatus, READ_OPERATIONS, ShutdownFuture,
    failure,
};
use scopehub_domain::protocol::{Error, ErrorCode, Message, Response};
use serde_json::{Value, json};
use tokio::{
    io::{AsyncWriteExt, BufReader},
    process::{Child, ChildStdin, ChildStdout, Command},
};
use uuid::Uuid;

use crate::{ProtocolError, decode_line, max_message_bytes, read_frame};

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
}

pub struct MarketWorker {
    config: Option<WorkerConfig>,
    session: Option<Session>,
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
        }
    }

    pub fn process_id(&self) -> Option<u32> {
        self.session.as_ref().and_then(|session| session.child.id())
    }

    pub async fn shutdown(&mut self) {
        if let Some(mut session) = self.session.take() {
            // This fixed worker does not spawn descendants. Packaged process-tree
            // containment is separate Windows acceptance, not established here.
            let _ = session.child.kill().await;
            let _ = session.child.wait().await;
        }
    }

    async fn start(&mut self) -> Result<(), Error> {
        let config = self
            .config
            .as_ref()
            .ok_or_else(|| failure(ErrorCode::PROVIDERNOTCONFIGURED))?;
        let deadline = config.deadline;
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
            self.shutdown().await;
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
        if !session.capabilities.iter().any(|name| name == operation)
            || paged && session.version != "1.1"
        {
            return Err(failure(ErrorCode::UNSUPPORTEDCAPABILITY));
        }
        let deadline = self.config.as_ref().expect("configured session").deadline;
        let id = Uuid::new_v4().to_string();
        let request = json!({
            "protocol_version": if paged { "1.1" } else { "1.0" }, "type": "request", "message_id": Uuid::new_v4().to_string(),
            "request_id": id, "task_id": null, "timestamp_ms": SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis() as u64,
            "operation": operation, "payload": payload,
        });
        let mut frame =
            serde_json::to_vec(&request).map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
        frame.push(b'\n');
        if frame.len() > max_message_bytes() {
            return Err(failure(ErrorCode::RESOURCELIMIT));
        }
        decode_line(&frame).map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
        let session = self.session.as_mut().expect("started session");
        let result = tokio::time::timeout(deadline, async {
            session
                .input
                .write_all(&frame)
                .await
                .map_err(|_| ProtocolError::Io)?;
            session.input.flush().await.map_err(|_| ProtocolError::Io)?;
            let response = decode_line(&read_frame(&mut session.output).await?)?;
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
                self.shutdown().await;
                Err(protocol_failure(error))
            }
        }
    }
}

impl MarketPort for MarketWorker {
    fn request<'a>(&'a mut self, operation: &'a str, payload: &'a Value) -> MarketFuture<'a> {
        Box::pin(self.exchange(operation, payload))
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
