//! Authorize immutable publication, discovery and frozen reads of reviewed local CSV bars.

use scopehub_domain::{
    protocol::{
        CsvMappingV1, Error, ErrorCode, ListLocalImportRevisionsInput,
        ListLocalImportRevisionsOutput, LocalImportRevisionListEntry, PublishLocalImportInput,
        ReadLocalImportRevisionInput, RevokeLocalImportRevisionInput,
    },
    wire::{max_message_bytes, normalize_integers},
};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

use crate::{
    Actor, MappingCommitGuard, OperationRegistry, ReviewedImportMapping, ReviewedMappingStore,
    WorkspaceAccess, authorized_operation, failure, local_import::valid_bar,
    reviewed_mapping::ordered_header_digest,
};

pub const PUBLISH_LOCAL_IMPORT: &str = "market.publishLocalImport";
pub const LIST_LOCAL_IMPORT_REVISIONS: &str = "market.listLocalImportRevisions";
pub const READ_LOCAL_IMPORT_REVISION: &str = "market.readLocalImportRevision";
pub const REVOKE_LOCAL_IMPORT_REVISION: &str = "market.revokeLocalImportRevision";
const MAX_RECORDS: u64 = 10_000;

/// Only a trusted transport can materialize this candidate from an authorized file.
pub struct LocalDatasetPublication<'a> {
    pub source_key: &'a str,
    pub relative_path: &'a str,
    pub source_sha256: &'a str,
    pub mapping_sha256: &'a str,
    pub reviewed_mapping: &'a ReviewedImportMapping,
    pub original: &'a [u8],
    pub content: &'a Value,
}

pub struct StoredLocalDataset {
    pub revision_id: String,
    pub relative_path: String,
    pub source_sha256: String,
    pub mapping_sha256: String,
    pub review_revision: u64,
    pub content: Value,
}

pub trait LocalDatasetStore: ReviewedMappingStore {
    fn publish_local_dataset(
        &mut self,
        candidate: &LocalDatasetPublication<'_>,
        guard: &impl MappingCommitGuard,
    ) -> Result<String, Error>;
    fn list_local_datasets(
        &self,
        after_revision_id: Option<&str>,
        limit: usize,
        include_bar_summary: bool,
    ) -> Result<Vec<LocalImportRevisionListEntry>, Error>;
    fn read_local_dataset(&self, revision_id: &str) -> Result<Option<StoredLocalDataset>, Error>;
    fn revoke_local_dataset(&mut self, revision_id: &str) -> Result<bool, Error>;
}

pub fn publish_local_import_input(
    actor: Actor,
    payload: Value,
) -> Result<PublishLocalImportInput, Error> {
    serde_json::from_value(validated_input(actor, PUBLISH_LOCAL_IMPORT, payload)?)
        .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))
}

pub fn read_local_import_revision_input(
    actor: Actor,
    payload: Value,
) -> Result<ReadLocalImportRevisionInput, Error> {
    serde_json::from_value(validated_input(actor, READ_LOCAL_IMPORT_REVISION, payload)?)
        .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))
}

pub fn list_local_import_revisions_input(
    actor: Actor,
    payload: Value,
) -> Result<ListLocalImportRevisionsInput, Error> {
    serde_json::from_value(validated_input(
        actor,
        LIST_LOCAL_IMPORT_REVISIONS,
        payload,
    )?)
    .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))
}

pub fn revoke_local_import_revision_input(
    actor: Actor,
    payload: Value,
) -> Result<RevokeLocalImportRevisionInput, Error> {
    serde_json::from_value(validated_input(
        actor,
        REVOKE_LOCAL_IMPORT_REVISION,
        payload,
    )?)
    .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))
}

fn validated_input(actor: Actor, name: &str, mut payload: Value) -> Result<Value, Error> {
    let operation = authorized_operation(actor, name)?;
    if !operation.input.is_valid(&payload) {
        return Err(failure(ErrorCode::INVALIDARGUMENT));
    }
    normalize_integers(&mut payload);
    Ok(payload)
}

pub fn local_mapping_fingerprint(mapping: &CsvMappingV1) -> Result<String, Error> {
    let bytes = serde_json::to_vec(mapping).map_err(|_| failure(ErrorCode::INTERNALERROR))?;
    Ok(format!("{:x}", Sha256::digest(bytes)))
}

fn revision_summary(
    id: &str,
    relative_path: &str,
    source_sha256: &str,
    mapping_sha256: &str,
    review_revision: u64,
    content: &Value,
) -> Value {
    json!({
        "revision_id": id,
        "relative_path": relative_path,
        "source_sha256": source_sha256,
        "mapping_sha256": mapping_sha256,
        "review_revision": review_revision,
        "symbol": content["symbol"],
        "series": content["series"],
        "bar_count": content["bars"].as_array().map(Vec::len),
        "source_record_count": content["source_record_count"],
    })
}

fn frozen_output(actor: Actor, revision: &Value, bars: &Value) -> Result<Value, Error> {
    let operation = authorized_operation(actor, READ_LOCAL_IMPORT_REVISION)?;
    let output = json!({ "version": 1, "revision": revision, "bars": bars });
    if output.to_string().len() >= max_message_bytes() {
        return Err(failure(ErrorCode::RESOURCELIMIT));
    }
    if !operation.output.is_valid(&output) || !valid_records(&output) {
        return Err(failure(ErrorCode::OUTPUTINVALID));
    }
    Ok(output)
}

fn valid_records(output: &Value) -> bool {
    let mut previous_time = i64::MIN;
    let mut records = Vec::new();
    let Some(bars) = output["bars"].as_array() else {
        return false;
    };
    let count = output["revision"]["source_record_count"]
        .as_u64()
        .unwrap_or_default();
    if !(1..=MAX_RECORDS).contains(&count) {
        return false;
    }
    for entry in bars {
        let bar = &entry["bar"];
        let Some(time) = bar["time_ms"].as_i64() else {
            return false;
        };
        if time <= previous_time || !valid_bar(bar) {
            return false;
        }
        previous_time = time;
        let Some(locators) = entry["locators"].as_array() else {
            return false;
        };
        for locator in locators {
            if records.len() as u64 == count {
                return false;
            }
            records.push((
                locator["record_number"].as_u64().unwrap_or_default(),
                locator["line_start"].as_u64().unwrap_or_default(),
                locator["line_end"].as_u64().unwrap_or_default(),
            ));
        }
    }
    records.sort_unstable();
    let mut previous_line = 1;
    records.len() as u64 == count
        && records
            .iter()
            .enumerate()
            .all(|(index, &(number, first, last))| {
                let valid = number == index as u64 + 1 && first > previous_line && last >= first;
                previous_line = last;
                valid
            })
}

impl<S: LocalDatasetStore> OperationRegistry<S> {
    pub fn publish_reviewed_local_dataset(
        &mut self,
        access: &WorkspaceAccess<'_>,
        candidate: &LocalDatasetPublication<'_>,
        guard: &impl MappingCommitGuard,
    ) -> Result<String, Error> {
        self.authorize_local_import(access, PUBLISH_LOCAL_IMPORT)?;
        let mapping = &candidate.reviewed_mapping.mapping;
        let content = candidate.content;
        if candidate.source_sha256 != format!("{:x}", Sha256::digest(candidate.original))
            || candidate.mapping_sha256 != local_mapping_fingerprint(mapping)?
            || content["file_sha256"] != candidate.source_sha256
            || content["mapping_version"] != mapping.version
            || content["mapping_revision"] != mapping.revision
        {
            return Err(failure(ErrorCode::OUTPUTINVALID));
        }
        let header = content["header"]
            .as_array()
            .ok_or_else(|| failure(ErrorCode::OUTPUTINVALID))?;
        if ordered_header_digest(header, mapping).as_deref()
            != Some(&candidate.reviewed_mapping.header_sha256)
        {
            let mut error = failure(ErrorCode::STALECONTEXT);
            error.message = "The CSV header changed. Review and save the mapping for this file before publishing.".into();
            return Err(error);
        }
        // Validate the exact frozen response before any storage mutation.
        let candidate_revision = revision_summary(
            "00000000-0000-4000-8000-000000000000",
            candidate.relative_path,
            candidate.source_sha256,
            candidate.mapping_sha256,
            candidate.reviewed_mapping.review_revision,
            content,
        );
        frozen_output(access.actor, &candidate_revision, &content["bars"])?;
        self.port.publish_local_dataset(candidate, guard)
    }

    pub fn local_dataset_publication_receipt(
        &self,
        access: &WorkspaceAccess<'_>,
        id: &str,
    ) -> Result<Value, Error> {
        self.authorize_local_import(access, PUBLISH_LOCAL_IMPORT)?;
        let operation = authorized_operation(access.actor, PUBLISH_LOCAL_IMPORT)?;
        let stored = self
            .port
            .read_local_dataset(id)?
            .ok_or_else(|| failure(ErrorCode::SNAPSHOTUNAVAILABLE))?;
        let revision = stored.summary();
        let output = json!({ "version": 1, "revision": revision });
        if !operation.output.is_valid(&output) {
            return Err(failure(ErrorCode::OUTPUTINVALID));
        }
        Ok(output)
    }

    pub fn read_frozen_local_dataset(
        &self,
        access: &WorkspaceAccess<'_>,
        input: &ReadLocalImportRevisionInput,
    ) -> Result<Value, Error> {
        self.authorize_local_import(access, READ_LOCAL_IMPORT_REVISION)?;
        let stored = self
            .port
            .read_local_dataset(&input.revision_id)?
            .ok_or_else(|| failure(ErrorCode::SNAPSHOTUNAVAILABLE))?;
        let revision = stored.summary();
        frozen_output(access.actor, &revision, &stored.content["bars"])
    }

    pub fn list_frozen_local_datasets(
        &self,
        access: &WorkspaceAccess<'_>,
        input: &ListLocalImportRevisionsInput,
    ) -> Result<Value, Error> {
        self.authorize_local_import(access, LIST_LOCAL_IMPORT_REVISIONS)?;
        let operation = authorized_operation(access.actor, LIST_LOCAL_IMPORT_REVISIONS)?;
        // Bound work even for trusted Rust callers that bypass input decoding.
        let limit = usize::try_from(input.limit)
            .ok()
            .filter(|limit| (1..=20).contains(limit))
            .ok_or_else(|| failure(ErrorCode::INVALIDARGUMENT))?;
        let mut revisions = self.port.list_local_datasets(
            input.after_revision_id.as_deref(),
            limit + 1,
            input.include_bar_summary.unwrap_or_default(),
        )?;
        let has_more = revisions.len() > limit;
        revisions.truncate(limit);
        let next_after_revision_id = if has_more {
            revisions.last().map(|entry| entry.revision_id.clone())
        } else {
            None
        };
        let output = serde_json::to_value(ListLocalImportRevisionsOutput {
            version: 1,
            revisions,
            next_after_revision_id,
        })
        .map_err(|_| failure(ErrorCode::INTERNALERROR))?;
        if !operation.output.is_valid(&output) {
            return Err(failure(ErrorCode::OUTPUTINVALID));
        }
        if output.to_string().len() >= max_message_bytes() {
            return Err(failure(ErrorCode::RESOURCELIMIT));
        }
        Ok(output)
    }

    pub fn revoke_frozen_local_dataset(
        &mut self,
        access: &WorkspaceAccess<'_>,
        input: &RevokeLocalImportRevisionInput,
    ) -> Result<Value, Error> {
        self.authorize_local_import(access, REVOKE_LOCAL_IMPORT_REVISION)?;
        if !self.port.revoke_local_dataset(&input.revision_id)? {
            return Err(failure(ErrorCode::SNAPSHOTUNAVAILABLE));
        }
        Ok(json!({ "version": 1, "revision_id": input.revision_id }))
    }
}

impl StoredLocalDataset {
    fn summary(&self) -> Value {
        revision_summary(
            &self.revision_id,
            &self.relative_path,
            &self.source_sha256,
            &self.mapping_sha256,
            self.review_revision,
            &self.content,
        )
    }
}
