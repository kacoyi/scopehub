use std::{future::Future, sync::Arc, time::Duration};

use reqwest::{
    Client, ClientBuilder, StatusCode,
    header::{AUTHORIZATION, CONTENT_TYPE, HeaderValue},
};
use rustls::{ClientConfig, ConfigBuilder, WantsVerifier};
use rustls_platform_verifier::BuilderVerifierExt;
use scopehub_application::OPENAI_RESPONSES_DESTINATION;
use scopehub_domain::protocol::{Error, ErrorCode};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

use super::{MAX_FRAME_BYTES, MAX_MODEL_BYTES, MAX_TEXT_BYTES, TURN_DEADLINE, error};

const MAX_INPUT_BYTES: usize = 96 * 1024;
const MAX_MESSAGES: usize = 9;
const MAX_OUTPUT_TOKENS: u16 = 4096;
const MAX_CREDENTIAL_BYTES: usize = 2048;

/// Send the core's conversation projection once over HTTPS. The credential
/// lives in the Authorization header and the receiving account owns usage.
pub async fn complete_openai(
    request: &Value,
    credential: &str,
    cancelled: impl Future<Output = ()>,
) -> Result<Value, Error> {
    let unavailable = || error(ErrorCode::DATAUNAVAILABLE);
    let tls = tls_builder()?
        .with_platform_verifier()
        .map_err(|_| unavailable())?
        .with_no_client_auth();
    let client = client_builder(tls).build().map_err(|_| unavailable())?;
    complete_with(&client, request, credential, cancelled).await
}

fn tls_builder() -> Result<ConfigBuilder<ClientConfig, WantsVerifier>, Error> {
    ClientConfig::builder_with_provider(Arc::new(rustls::crypto::ring::default_provider()))
        .with_safe_default_protocol_versions()
        .map_err(|_| error(ErrorCode::DATAUNAVAILABLE))
}

fn client_builder(tls: ClientConfig) -> ClientBuilder {
    Client::builder()
        .tls_backend_preconfigured(tls)
        .https_only(true)
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .retry(reqwest::retry::never())
        .pool_max_idle_per_host(0)
        .connect_timeout(Duration::from_secs(10))
        .timeout(TURN_DEADLINE)
}

async fn complete_with(
    client: &Client,
    request: &Value,
    credential: &str,
    cancelled: impl Future<Output = ()>,
) -> Result<Value, Error> {
    tokio::select! {
        biased;
        () = cancelled => Err(error(ErrorCode::CANCELLED)),
        result = exchange(client, request, credential) => result,
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Projection<'a> {
    model: &'a str,
    #[serde(borrow)]
    messages: Vec<Message<'a>>,
    stream: bool,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Message<'a> {
    role: Role,
    content: &'a str,
}

#[derive(Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
enum Role {
    User,
    Assistant,
}

fn project(request: &Value) -> Result<Vec<u8>, Error> {
    let invalid = || error(ErrorCode::INVALIDARGUMENT);
    let Projection {
        model,
        messages,
        stream,
    } = Projection::deserialize(request).map_err(|_| invalid())?;
    if stream
        || model.is_empty()
        || model.len() > MAX_MODEL_BYTES
        || !model
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"-_.:/".contains(&byte))
        || messages.len() > MAX_MESSAGES
        || messages.len() % 2 != 1
        || messages.iter().enumerate().any(|(index, message)| {
            let expected = if index % 2 == 0 {
                Role::User
            } else {
                Role::Assistant
            };
            message.role != expected
                || message.content.trim().is_empty()
                || message.content.len() > MAX_TEXT_BYTES
        })
    {
        return Err(invalid());
    }
    let body = serde_json::to_vec(&json!({
        "model": model,
        "input": messages,
        "store": false,
        "stream": false,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
    }))
    .map_err(|_| invalid())?;
    if body.len() > MAX_INPUT_BYTES {
        return Err(error(ErrorCode::RESOURCELIMIT));
    }
    Ok(body)
}

async fn exchange(client: &Client, request: &Value, credential: &str) -> Result<Value, Error> {
    let body = project(request)?;
    if credential.is_empty()
        || credential.len() > MAX_CREDENTIAL_BYTES
        || !credential
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"-._~+/=".contains(&byte))
    {
        return Err(error(ErrorCode::PERMISSIONDENIED));
    }
    let mut authorization = HeaderValue::try_from(format!("Bearer {credential}"))
        .map_err(|_| error(ErrorCode::PERMISSIONDENIED))?;
    authorization.set_sensitive(true);
    let mut response = client
        .post(OPENAI_RESPONSES_DESTINATION)
        .header(AUTHORIZATION, authorization)
        .header(CONTENT_TYPE, "application/json")
        .body(body)
        .send()
        .await
        .map_err(exchange_error)?;
    match response.status() {
        StatusCode::OK => {}
        StatusCode::UNAUTHORIZED | StatusCode::FORBIDDEN => {
            return Err(error(ErrorCode::PERMISSIONDENIED));
        }
        StatusCode::BAD_REQUEST => return Err(error(ErrorCode::INVALIDARGUMENT)),
        _ => return Err(error(ErrorCode::DATAUNAVAILABLE)),
    }
    if response
        .content_length()
        .is_some_and(|length| length > MAX_FRAME_BYTES as u64)
    {
        return Err(error(ErrorCode::RESOURCELIMIT));
    }
    let mut bytes = Vec::new();
    while let Some(chunk) = response.chunk().await.map_err(exchange_error)? {
        if chunk.len() > MAX_FRAME_BYTES - bytes.len() {
            return Err(error(ErrorCode::RESOURCELIMIT));
        }
        bytes.extend_from_slice(&chunk);
    }
    let reply: Value =
        serde_json::from_slice(&bytes).map_err(|_| error(ErrorCode::OUTPUTINVALID))?;
    text_reply(reply, credential)
}

fn text_reply(reply: Value, credential: &str) -> Result<Value, Error> {
    let invalid = || error(ErrorCode::OUTPUTINVALID);
    if reply["status"] != "completed" {
        if reply["status"] == "incomplete"
            && reply["incomplete_details"]["reason"] == "max_output_tokens"
        {
            return Err(error(ErrorCode::RESOURCELIMIT));
        }
        return Err(invalid());
    }
    let model = reply["model"]
        .as_str()
        .filter(|model| {
            !model.is_empty()
                && model.len() <= MAX_MODEL_BYTES
                && !model
                    .chars()
                    .any(|char| char.is_whitespace() || char.is_control())
        })
        .ok_or_else(invalid)?;
    let outputs = reply["output"].as_array().ok_or_else(invalid)?;
    let mut text = String::new();
    for output in outputs {
        if output["type"] == "message" && output["role"] == "assistant" {
            let content = output["content"].as_array().ok_or_else(invalid)?;
            for part in content {
                let value = match part["type"].as_str() {
                    Some("output_text") => &part["text"],
                    Some("refusal") => &part["refusal"],
                    _ => continue,
                };
                let segment = value.as_str().ok_or_else(invalid)?;
                if segment.len() > MAX_TEXT_BYTES - text.len() {
                    return Err(error(ErrorCode::RESOURCELIMIT));
                }
                text.push_str(segment);
            }
        }
    }
    if text.trim().is_empty() || text.contains(credential) || model.contains(credential) {
        return Err(invalid());
    }
    Ok(json!({"model": model, "text": text}))
}

fn exchange_error(error_source: reqwest::Error) -> Error {
    if error_source.is_timeout() {
        error(ErrorCode::TIMEOUT)
    } else {
        error(ErrorCode::DATAUNAVAILABLE)
    }
}

#[cfg(test)]
mod tests;
