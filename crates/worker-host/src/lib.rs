use std::process::Stdio;
use std::time::Duration;

use scopehub_domain::protocol::{Hello, Message};
use std::collections::HashSet;
use tokio::io::{AsyncBufRead, AsyncBufReadExt, BufReader};
use tokio::process::Command;

async fn read_frame<R: AsyncBufRead + Unpin>(reader: &mut R) -> Result<Vec<u8>, ProtocolError> {
    let mut frame = Vec::new();
    loop {
        let available = reader.fill_buf().await.map_err(|_| ProtocolError::Io)?;
        if available.is_empty() {
            return Err(ProtocolError::WorkerExited);
        }
        let end = available.iter().position(|byte| *byte == b'\n');
        let count = end.map_or(available.len(), |index| index + 1);
        if frame.len() + count > max_message_bytes() {
            return Err(ProtocolError::ResourceLimit);
        }
        frame.extend_from_slice(&available[..count]);
        reader.consume(count);
        if end.is_some() {
            return Ok(frame);
        }
    }
}

/// A hello advertises implementations, including optional future names. Only
/// the intersection with the host's own operation allowlist may be dispatched.
pub struct NegotiatedWorker {
    pub version: String,
    pub capabilities: Vec<String>,
}

pub fn negotiate_worker(
    frame: &[u8],
    expected_role: &str,
    required: &[&str],
) -> Result<NegotiatedWorker, ProtocolError> {
    let message = decode_line(frame).map_err(|error| {
        if error == ProtocolError::InvalidMessage
            && let Ok(value) = serde_json::from_slice::<serde_json::Value>(frame)
            && value["type"] == "hello"
            && value["protocol_version"]
                .as_str()
                .is_some_and(|version| !matches!(version, "1.0" | "1.1"))
        {
            return ProtocolError::IncompatibleWorker;
        }
        error
    })?;
    let (version, role, supported, capabilities) = match message {
        Message::Hello(hello) => (
            hello.protocol_version.as_str(),
            hello.role.as_str(),
            hello.supported_protocols,
            hello
                .capabilities
                .iter()
                .map(|operation| operation.as_str().to_owned())
                .collect(),
        ),
        Message::WorkerHello11(hello) => (
            hello.protocol_version.as_str(),
            hello.role.as_str(),
            hello.supported_protocols,
            hello.capabilities,
        ),
        _ => return Err(ProtocolError::IncompatibleWorker),
    };
    let unique: HashSet<_> = capabilities.iter().map(String::as_str).collect();
    if role != expected_role
        || !supported.iter().any(|item| item == version)
        || !supported.iter().any(|item| item == "1.0")
        || (version == "1.0" && unique.len() != required.len())
        || (role == "market"
            && capabilities.iter().any(|name| {
                [
                    "workspace.",
                    "chart.",
                    "indicator.",
                    "task.",
                    "ai.",
                    "confirmation.",
                ]
                .iter()
                .any(|prefix| name.starts_with(prefix))
            }))
        || unique.len() != capabilities.len()
        || required.iter().any(|name| !unique.contains(name))
    {
        return Err(ProtocolError::IncompatibleWorker);
    }
    Ok(NegotiatedWorker {
        version: version.to_owned(),
        capabilities,
    })
}

/// Development probe only. The trusted caller supplies the executable and a clean environment.
/// Long-lived workers and Windows process-tree restrictions are later milestone work.
pub async fn probe_handshake(
    command: &mut Command,
    expected_role: &str,
    deadline: Duration,
) -> Result<Hello, ProtocolError> {
    let mut child = command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .kill_on_drop(true)
        .spawn()
        .map_err(|_| ProtocolError::Io)?;
    let stdout = child.stdout.take().ok_or(ProtocolError::Io)?;
    let mut reader = BufReader::new(stdout);
    let result = tokio::time::timeout(deadline, async {
        let frame = read_frame(&mut reader).await?;
        let Message::Hello(hello) = decode_line(&frame)? else {
            return Err(ProtocolError::IncompatibleWorker);
        };
        if hello.role.as_str() != expected_role
            || !hello
                .supported_protocols
                .iter()
                .any(|version| version == hello.protocol_version.as_str())
            || !hello.capabilities.is_empty()
        {
            return Err(ProtocolError::IncompatibleWorker);
        }
        Ok(*hello)
    })
    .await
    .unwrap_or(Err(ProtocolError::Timeout));
    // Always kill and reap the probe, including timeout, malformed hello and role mismatch.
    // No application state or data tasks are started by this probe.
    child.kill().await.map_err(|_| ProtocolError::Io)?;
    child.wait().await.map_err(|_| ProtocolError::Io)?;
    result
}

pub use scopehub_domain::wire::{POLICY, ProtocolError, SCHEMA, decode_line, max_message_bytes};
mod cleanup;
pub mod local_import;
pub mod market;
