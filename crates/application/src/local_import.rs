//! Authorize and validate locally scoped import previews at the registry boundary.

use scopehub_domain::{
    protocol::{Error, ErrorCode, LocalImportPreviewInput},
    wire::{max_message_bytes, normalize_integers},
};
use serde_json::Value;

use crate::{Actor, MarketFuture, authorized_operation, failure};

pub const PREVIEW_LOCAL_IMPORT: &str = "market.previewLocalImport";
pub const DEFAULT_PREVIEW_ROWS: u32 = 100;

/// The local transport supplies the port bound to its live selection and its
/// own cancellation. Shared payloads never materialize a port or a caller.
pub trait LocalImportPort: Send {
    fn ensure_live(&self, input: &LocalImportPreviewInput) -> Result<(), Error>;
    fn preview<'a>(&'a mut self, input: &'a LocalImportPreviewInput) -> MarketFuture<'a>;
}

pub async fn preview_local_import(
    actor: Actor,
    mut payload: Value,
    port: &mut impl LocalImportPort,
) -> Result<Value, Error> {
    let operation = authorized_operation(actor, PREVIEW_LOCAL_IMPORT)?;
    if !operation.input.is_valid(&payload) || payload.to_string().len() >= max_message_bytes() {
        return Err(failure(ErrorCode::INVALIDARGUMENT));
    }
    normalize_integers(&mut payload);
    let input: LocalImportPreviewInput =
        serde_json::from_value(payload).map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
    port.ensure_live(&input)?;
    let output = port.preview(&input).await?;
    if output.to_string().len() >= max_message_bytes() {
        return Err(failure(ErrorCode::RESOURCELIMIT));
    }
    if !operation.output.is_valid(&output) || !matches_source(&input, &output) {
        return Err(failure(ErrorCode::OUTPUTINVALID));
    }
    port.ensure_live(&input)?;
    Ok(output)
}

fn matches_source(input: &LocalImportPreviewInput, output: &Value) -> bool {
    let preview = &output["preview"];
    if output["folder_id"] != input.folder_id
        || output["file_id"] != input.file_id
        || preview["file_sha256"] != output["sha256"]
        || input
            .expected_sha256
            .as_ref()
            .is_some_and(|digest| output["sha256"] != *digest)
        || preview["mapping_version"] != input.mapping.version
        || preview["mapping_revision"] != input.mapping.revision
    {
        return false;
    }
    let rows = preview["rows"].as_array().expect("validated preview rows");
    let columns = preview["header"]
        .as_array()
        .expect("validated preview header")
        .len();
    let mut previous_line = 0;
    rows.len() as u64 <= input.row_limit.unwrap_or(u64::from(DEFAULT_PREVIEW_ROWS))
        && rows.iter().enumerate().all(|(index, row)| {
            let first = row["line_start"].as_u64().unwrap_or_default();
            let last = row["line_end"].as_u64().unwrap_or_default();
            if row["record_number"] != index as u64 + 1 || first <= previous_line || last < first {
                return false;
            }
            previous_line = last;
            let bar = &row["normalized"];
            bar.is_null()
                || (preview["series"].is_object()
                    && row["symbol"]
                        .as_str()
                        .is_some_and(|symbol| !symbol.is_empty())
                    && row["raw_fields"]
                        .as_array()
                        .is_some_and(|fields| fields.len() == columns)
                    && valid_bar(bar))
        })
}

fn valid_bar(bar: &Value) -> bool {
    let price = |field: &str| bar[field].as_f64().unwrap_or(f64::NAN);
    let (low, high, open, close) = (price("low"), price("high"), price("open"), price("close"));
    low <= open
        && open <= high
        && low <= close
        && close <= high
        && bar["is_closed"] == true
        && (bar["volume"].is_null() || price("volume") >= 0.0)
}
