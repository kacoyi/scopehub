use std::process::Stdio;
use std::sync::LazyLock;
use std::time::Duration;

use scopehub_domain::protocol::{Hello, Message};
use serde_json::Value;
use tokio::io::{AsyncBufRead, AsyncBufReadExt, BufReader};
use tokio::process::Command;

pub static SCHEMA: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!(
        "../../../contracts/schemas/protocol.schema.json"
    ))
    .expect("embedded schema is valid JSON")
});

pub static POLICY: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("../../../contracts/generated/runtime.json"))
        .expect("embedded policy is valid JSON")
});

static VALIDATOR: LazyLock<jsonschema::Validator> = LazyLock::new(|| {
    jsonschema::options()
        .should_validate_formats(true)
        .build(&SCHEMA)
        .expect("embedded schema compiles")
});

#[derive(Debug, PartialEq)]
pub enum ProtocolError {
    InvalidMessage,
    ResourceLimit,
    IncompatibleWorker,
    WorkerExited,
    Timeout,
    Io,
}

pub fn max_message_bytes() -> usize {
    POLICY["max_message_bytes"]
        .as_u64()
        .expect("configured limit") as usize
}

pub fn decode_line(line: &[u8]) -> Result<Message, ProtocolError> {
    if line.len() > max_message_bytes() {
        return Err(ProtocolError::ResourceLimit);
    }
    let body = line.strip_suffix(b"\n").unwrap_or(line);
    let body = body.strip_suffix(b"\r").unwrap_or(body);
    if body.contains(&b'\n') || body.contains(&b'\r') {
        return Err(ProtocolError::InvalidMessage);
    }
    let value: Value = serde_json::from_slice(body).map_err(|_| ProtocolError::InvalidMessage)?;
    if !VALIDATOR.is_valid(&value) {
        return Err(ProtocolError::InvalidMessage);
    }
    serde_json::from_value(value).map_err(|_| ProtocolError::InvalidMessage)
}

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
        if serde_json::to_value(&hello.role)
            .ok()
            .and_then(|v| v.as_str().map(str::to_owned))
            != Some(expected_role.to_owned())
            || !hello.supported_protocols.contains(&hello.protocol_version)
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
