//! Execute the application-approved local sample read without redirects or credentials.

use std::{sync::Arc, time::Duration};

use scopehub_application::{LocalApiTrial, failure};
use scopehub_domain::protocol::{Error, ErrorCode};
use serde::Deserialize;
use serde_json::{Value, json, value::RawValue};

const MAX_SAMPLE_BYTES: usize = 16 * 1024;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SampleReply<'a> {
    instrument: String,
    observed_at_ms: Value,
    #[serde(borrow)]
    value: &'a RawValue,
    unit: String,
}

pub async fn read_local_api_sample(trial: &LocalApiTrial) -> Result<Value, Error> {
    let tls = rustls::ClientConfig::builder_with_provider(Arc::new(
        rustls::crypto::ring::default_provider(),
    ))
    .with_safe_default_protocol_versions()
    .map_err(|_| failure(ErrorCode::INTERNALERROR))?
    .with_root_certificates(rustls::RootCertStore::empty())
    .with_no_client_auth();
    let client = reqwest::Client::builder()
        .tls_backend_preconfigured(tls)
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .retry(reqwest::retry::never())
        .timeout(Duration::from_secs(3))
        .build()
        .map_err(|_| failure(ErrorCode::INTERNALERROR))?;
    let mut response = client
        .get(trial.destination())
        .header(reqwest::header::ACCEPT, "application/json")
        .send()
        .await
        .map_err(read_error)?;
    match response.status() {
        reqwest::StatusCode::OK => {}
        reqwest::StatusCode::UNAUTHORIZED | reqwest::StatusCode::FORBIDDEN => {
            return Err(failure(ErrorCode::PERMISSIONDENIED));
        }
        _ => return Err(failure(ErrorCode::DATAUNAVAILABLE)),
    }
    if !response
        .headers()
        .get(reqwest::header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .is_some_and(|value| {
            value
                .split(';')
                .next()
                .is_some_and(|mime| mime.trim().eq_ignore_ascii_case("application/json"))
        })
    {
        return Err(failure(ErrorCode::OUTPUTINVALID));
    }
    if response
        .content_length()
        .is_some_and(|length| length > MAX_SAMPLE_BYTES as u64)
    {
        return Err(failure(ErrorCode::RESOURCELIMIT));
    }
    let mut bytes = Vec::new();
    while let Some(chunk) = response.chunk().await.map_err(read_error)? {
        if chunk.len() > MAX_SAMPLE_BYTES - bytes.len() {
            return Err(failure(ErrorCode::RESOURCELIMIT));
        }
        bytes.extend_from_slice(&chunk);
    }
    let reply: SampleReply<'_> =
        serde_json::from_slice(&bytes).map_err(|_| failure(ErrorCode::OUTPUTINVALID))?;
    let value = reply.value.get();
    if !matches!(value.as_bytes().first(), Some(b'-' | b'0'..=b'9')) {
        return Err(failure(ErrorCode::OUTPUTINVALID));
    }
    Ok(json!({
        "instrument": reply.instrument,
        "observed_at_ms": reply.observed_at_ms,
        "value": value,
        "unit": reply.unit,
    }))
}

fn read_error(error: reqwest::Error) -> Error {
    failure(if error.is_timeout() {
        ErrorCode::TIMEOUT
    } else {
        ErrorCode::DATAUNAVAILABLE
    })
}
