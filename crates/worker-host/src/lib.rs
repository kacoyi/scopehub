use std::process::Stdio;
use std::time::Duration;

use scopehub_domain::protocol::{Hello, Message};
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
pub mod market;
