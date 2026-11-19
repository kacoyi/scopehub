//! Reviewed import mappings in the current profile's single durable writer.

use scopehub_domain::{
    protocol::{
        CsvMappingV1, Error, ErrorCode, LoadLocalImportMappingInput, SaveLocalImportMappingInput,
        SavedLocalImportMapping,
    },
    wire::normalize_integers,
};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

use crate::{
    Actor, OperationRegistry, WorkspaceAccess, WorkspaceStore, authorized_operation, failure,
};

pub const SAVE_LOCAL_IMPORT_MAPPING: &str = "market.saveLocalImportMapping";
pub const LOAD_LOCAL_IMPORT_MAPPING: &str = "market.loadLocalImportMapping";

pub type ReviewedImportMapping = SavedLocalImportMapping;

/// Linearize source revocation and cancellation with SQLite commit. The guard
/// runs only after the transaction has staged the candidate row.
pub trait MappingCommitGuard {
    fn commit<T>(&self, apply: impl FnOnce() -> Result<T, Error>) -> Result<T, Error>;
}

pub trait ReviewedMappingStore: WorkspaceStore {
    fn load_mapping(&self, source_key: &str) -> Result<Option<ReviewedImportMapping>, Error>;
    fn save_mapping(
        &mut self,
        source_key: &str,
        header_sha256: &str,
        mapping: &CsvMappingV1,
        guard: &impl MappingCommitGuard,
    ) -> Result<ReviewedImportMapping, Error>;
}

pub fn save_mapping_input(
    actor: Actor,
    mut payload: Value,
) -> Result<SaveLocalImportMappingInput, Error> {
    let op = authorized_operation(actor, SAVE_LOCAL_IMPORT_MAPPING)?;
    if !op.input.is_valid(&payload) {
        return Err(failure(ErrorCode::INVALIDARGUMENT));
    }
    normalize_integers(&mut payload);
    serde_json::from_value(payload).map_err(|_| failure(ErrorCode::INVALIDARGUMENT))
}

pub fn load_mapping_input(
    actor: Actor,
    mut payload: Value,
) -> Result<LoadLocalImportMappingInput, Error> {
    let op = authorized_operation(actor, LOAD_LOCAL_IMPORT_MAPPING)?;
    if !op.input.is_valid(&payload) {
        return Err(failure(ErrorCode::INVALIDARGUMENT));
    }
    normalize_integers(&mut payload);
    serde_json::from_value(payload).map_err(|_| failure(ErrorCode::INVALIDARGUMENT))
}

pub fn mapping_header_digest(preview: &Value, mapping: &CsvMappingV1) -> Option<String> {
    let body = &preview["preview"];
    let header = body["header"].as_array()?;
    if header.is_empty()
        || !body["raw_header_text"].is_null()
        || body["issues"].as_array()?.iter().any(|issue| {
            matches!(
                issue["code"].as_str(),
                Some("invalid_header" | "column_limit" | "decode_failed")
            )
        })
    {
        return None;
    }
    let material = json!([
        "scopehub-local-import-header-v1",
        mapping.encoding,
        mapping.delimiter,
        header
    ]);
    Some(format!(
        "{:x}",
        Sha256::digest(serde_json::to_vec(&material).ok()?)
    ))
}

pub fn verify_mapping_preview(
    input: &SaveLocalImportMappingInput,
    preview: &Value,
) -> Result<String, Error> {
    let body = &preview["preview"];
    if preview["sha256"] != input.expected_sha256
        || body["mapping_revision"] != input.mapping.revision
        || body["issues"]
            .as_array()
            .is_none_or(|issues| !issues.is_empty())
        || body["rows"]
            .as_array()
            .is_none_or(|rows| !rows.iter().any(|row| row["normalized"].is_object()))
    {
        let mut error = failure(ErrorCode::INVALIDARGUMENT);
        error.message = "Correct the mapping and review a normalized bar before saving.".into();
        return Err(error);
    }
    mapping_header_digest(preview, &input.mapping).ok_or_else(|| failure(ErrorCode::OUTPUTINVALID))
}

pub fn load_mapping_output(
    actor: Actor,
    record: Option<ReviewedImportMapping>,
    preview: Option<&Value>,
) -> Result<Value, Error> {
    let op = authorized_operation(actor, LOAD_LOCAL_IMPORT_MAPPING)?;
    let output = match (record, preview) {
        (None, None) => {
            json!({"version":1,"status":"not_saved","record":null,"source_sha256":null})
        }
        (Some(record), Some(preview)) => {
            let matches = mapping_header_digest(preview, &record.mapping).as_deref()
                == Some(&record.header_sha256);
            json!({"version":1,"status":if matches {"saved"} else {"review_required"},
                "record":matches.then_some(record),"source_sha256":preview["sha256"]})
        }
        _ => return Err(failure(ErrorCode::OUTPUTINVALID)),
    };
    if !op.output.is_valid(&output) {
        return Err(failure(ErrorCode::OUTPUTINVALID));
    }
    Ok(output)
}

impl<S: ReviewedMappingStore> OperationRegistry<S> {
    fn authorize_mapping(&self, access: &WorkspaceAccess<'_>, name: &str) -> Result<(), Error> {
        authorized_operation(access.actor, name)?;
        let binding = self.port.binding();
        if access.profile_id != binding.profile_id
            || access.workspace_id != binding.workspace_id
            || access.principal_id != access.profile_id
            || access.paired_client_id.is_some()
            || !access.operations.contains(&name)
        {
            return Err(failure(ErrorCode::PERMISSIONDENIED));
        }
        Ok(())
    }

    pub fn find_reviewed_mapping(
        &self,
        access: &WorkspaceAccess<'_>,
        source_key: &str,
    ) -> Result<Option<ReviewedImportMapping>, Error> {
        self.authorize_mapping(access, LOAD_LOCAL_IMPORT_MAPPING)?;
        self.port.load_mapping(source_key)
    }

    pub fn save_reviewed_mapping(
        &mut self,
        access: &WorkspaceAccess<'_>,
        source_key: &str,
        header_sha256: &str,
        mapping: &CsvMappingV1,
        guard: &impl MappingCommitGuard,
    ) -> Result<Value, Error> {
        self.authorize_mapping(access, SAVE_LOCAL_IMPORT_MAPPING)?;
        let op = authorized_operation(access.actor, SAVE_LOCAL_IMPORT_MAPPING)?;
        if !op.output.is_valid(&json!({"version":1,"record":{
            "review_revision":1,"header_sha256":header_sha256,"mapping":mapping
        }})) {
            return Err(failure(ErrorCode::INVALIDARGUMENT));
        }
        let record = self
            .port
            .save_mapping(source_key, header_sha256, mapping, guard)?;
        Ok(json!({"version":1,"record":record}))
    }
}
