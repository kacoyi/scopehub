use crate::protocol::Message;
use serde_json::Value;
use std::sync::LazyLock;

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

pub fn normalize_integers(value: &mut Value) {
    match value {
        Value::Number(number) if number.is_f64() => {
            let number = number.as_f64().expect("JSON numbers are finite");
            // The wire schema bounds integer fields to JavaScript's safe range.
            if number.fract() == 0.0 && number.abs() <= 9_007_199_254_740_991.0 {
                *value = Value::from(number as i64);
            }
        }
        Value::Array(values) => values.iter_mut().for_each(normalize_integers),
        Value::Object(values) => values.values_mut().for_each(normalize_integers),
        _ => {}
    }
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
    let mut value: Value =
        serde_json::from_slice(body).map_err(|_| ProtocolError::InvalidMessage)?;
    if !VALIDATOR.is_valid(&value) {
        return Err(ProtocolError::InvalidMessage);
    }
    normalize_integers(&mut value);
    serde_json::from_value(value).map_err(|_| ProtocolError::InvalidMessage)
}
