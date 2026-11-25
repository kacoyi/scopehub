//! Local human publication and entitled browsing of immutable folder revisions.

use std::collections::BTreeMap;

use scopehub_domain::{
    protocol::{
        Error, ErrorCode, FolderRefreshAlternative, FolderRefreshSource,
        ListLocalFolderRevisionsInput, ListLocalFolderRevisionsOutput, LocalImportRowLocator,
        PrepareLocalFolderPublicationInput, PublishLocalFolderRefreshInput,
        PublishedLocalFolderRevision, PublishedLocalFolderSource, ReadLocalFolderRevisionInput,
        ReadLocalFolderRevisionOutput, ReadLocalFolderSourceRowInput,
        RevokeLocalFolderRevisionInput,
    },
    wire::max_message_bytes,
};
use serde_json::{Value, json};

use crate::{
    Actor, FolderManifestPublication, FolderManifestRequest, FolderManifestStore,
    FrozenLocalSourceRow, LocalDatasetStore, MappingCommitGuard, OperationRegistry,
    StoredFolderManifest, WorkspaceAccess, authorized_operation, failure,
    folder_manifest::FolderManifestAnchor,
    local_mapping_fingerprint,
    local_publication::{bounded_source_row_output, validated_input, validated_source_row_request},
};

pub const PREPARE_LOCAL_FOLDER_PUBLICATION: &str = "market.prepareLocalFolderPublication";
pub const PUBLISH_LOCAL_FOLDER_REFRESH: &str = "market.publishLocalFolderRefresh";
pub const LIST_LOCAL_FOLDER_REVISIONS: &str = "market.listLocalFolderRevisions";
pub const READ_LOCAL_FOLDER_REVISION: &str = "market.readLocalFolderRevision";
pub const READ_LOCAL_FOLDER_SOURCE_ROW: &str = "market.readLocalFolderSourceRow";
pub const REVOKE_LOCAL_FOLDER_REVISION: &str = "market.revokeLocalFolderRevision";

pub fn folder_prepare_input(
    actor: Actor,
    payload: Value,
) -> Result<PrepareLocalFolderPublicationInput, Error> {
    serde_json::from_value(validated_input(
        actor,
        PREPARE_LOCAL_FOLDER_PUBLICATION,
        payload,
    )?)
    .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))
}

pub fn publish_folder_refresh_input(
    actor: Actor,
    payload: Value,
) -> Result<PublishLocalFolderRefreshInput, Error> {
    serde_json::from_value(validated_input(
        actor,
        PUBLISH_LOCAL_FOLDER_REFRESH,
        payload,
    )?)
    .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))
}

pub fn list_local_folder_revisions_input(
    actor: Actor,
    payload: Value,
) -> Result<ListLocalFolderRevisionsInput, Error> {
    serde_json::from_value(validated_input(
        actor,
        LIST_LOCAL_FOLDER_REVISIONS,
        payload,
    )?)
    .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))
}

pub fn read_local_folder_revision_input(
    actor: Actor,
    payload: Value,
) -> Result<ReadLocalFolderRevisionInput, Error> {
    serde_json::from_value(validated_input(actor, READ_LOCAL_FOLDER_REVISION, payload)?)
        .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))
}

pub fn read_local_folder_source_row_input(
    actor: Actor,
    payload: Value,
) -> Result<ReadLocalFolderSourceRowInput, Error> {
    serde_json::from_value(validated_input(
        actor,
        READ_LOCAL_FOLDER_SOURCE_ROW,
        payload,
    )?)
    .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))
}

pub fn read_local_folder_source_row_request(
    actor: Actor,
    payload: Value,
) -> Result<(ReadLocalFolderSourceRowInput, bool), Error> {
    let (raw, review) = validated_source_row_request(actor, READ_LOCAL_FOLDER_SOURCE_ROW, payload)?;
    let input = serde_json::from_value(raw).map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
    Ok((input, review))
}

pub fn revoke_local_folder_revision_input(
    actor: Actor,
    payload: Value,
) -> Result<RevokeLocalFolderRevisionInput, Error> {
    serde_json::from_value(validated_input(
        actor,
        REVOKE_LOCAL_FOLDER_REVISION,
        payload,
    )?)
    .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))
}

impl<S: FolderManifestStore + LocalDatasetStore> OperationRegistry<S> {
    pub fn publish_reviewed_folder(
        &mut self,
        access: &WorkspaceAccess<'_>,
        idempotency_key: &str,
        candidate: &FolderManifestPublication<'_>,
        guard: &impl MappingCommitGuard,
    ) -> Result<Value, Error> {
        self.authorize_local_import(access, PUBLISH_LOCAL_FOLDER_REFRESH)?;
        self.authorize_local_import(access, READ_LOCAL_FOLDER_REVISION)?;
        let request = FolderManifestRequest {
            principal_id: access.principal_id,
            idempotency_key,
        };
        let revision_id = self
            .port
            .publish_folder_manifest(&request, candidate, guard)?;
        let revision = self
            .port
            .folder_manifest_summary(&revision_id)?
            .ok_or_else(|| failure(ErrorCode::SNAPSHOTUNAVAILABLE))?;
        publication_reply(access.actor, &revision)
    }

    pub fn replay_reviewed_folder(
        &self,
        access: &WorkspaceAccess<'_>,
        idempotency_key: &str,
        input_sha256: &str,
    ) -> Result<Option<(Value, FolderManifestAnchor)>, Error> {
        self.authorize_local_import(access, PUBLISH_LOCAL_FOLDER_REFRESH)?;
        self.authorize_local_import(access, READ_LOCAL_FOLDER_REVISION)?;
        let request = FolderManifestRequest {
            principal_id: access.principal_id,
            idempotency_key,
        };
        let Some(revision_id) = self.port.replay_folder_manifest(&request, input_sha256)? else {
            return Ok(None);
        };
        let revision = self
            .port
            .folder_manifest_summary(&revision_id)?
            .ok_or_else(|| failure(ErrorCode::SNAPSHOTUNAVAILABLE))?;
        let anchor = self
            .port
            .read_folder_manifest_anchor(&revision_id)?
            .ok_or_else(|| failure(ErrorCode::SNAPSHOTUNAVAILABLE))?;
        Ok(Some((publication_reply(access.actor, &revision)?, anchor)))
    }

    pub fn list_frozen_folder_revisions(
        &self,
        access: &WorkspaceAccess<'_>,
        input: &ListLocalFolderRevisionsInput,
    ) -> Result<Value, Error> {
        self.authorize_local_import(access, LIST_LOCAL_FOLDER_REVISIONS)?;
        let limit = usize::try_from(input.limit)
            .ok()
            .filter(|limit| (1..=20).contains(limit))
            .ok_or_else(|| failure(ErrorCode::INVALIDARGUMENT))?;
        let mut revisions = self
            .port
            .list_folder_manifest_summaries(input.after_revision_id.as_deref(), limit + 1)?;
        let has_more = revisions.len() > limit;
        revisions.truncate(limit);
        let next_after_revision_id = if has_more {
            revisions
                .last()
                .map(|revision| revision.revision_id.clone())
        } else {
            None
        };
        let output = serde_json::to_value(ListLocalFolderRevisionsOutput {
            version: 1,
            revisions,
            next_after_revision_id,
        })
        .map_err(|_| failure(ErrorCode::INTERNALERROR))?;
        validate_output(access.actor, LIST_LOCAL_FOLDER_REVISIONS, output)
    }

    pub fn read_frozen_folder_revision(
        &self,
        access: &WorkspaceAccess<'_>,
        input: &ReadLocalFolderRevisionInput,
    ) -> Result<Value, Error> {
        self.authorize_local_import(access, READ_LOCAL_FOLDER_REVISION)?;
        let revision = self
            .port
            .folder_manifest_summary(&input.revision_id)?
            .ok_or_else(|| failure(ErrorCode::SNAPSHOTUNAVAILABLE))?;
        let (files, bars) = if input.include_details.unwrap_or(true) {
            let manifest = self
                .port
                .read_folder_manifest(&input.revision_id)?
                .ok_or_else(|| failure(ErrorCode::SNAPSHOTUNAVAILABLE))?;
            let bars = folder_bars(&manifest)?;
            let files = manifest
                .sources
                .into_iter()
                .map(|source| PublishedLocalFolderSource {
                    relative_path: source.relative_path,
                    source_sha256: source.sha256,
                    record_count: source.record_count,
                })
                .collect();
            (files, bars)
        } else {
            (Vec::new(), Vec::new())
        };
        let output = serde_json::to_value(ReadLocalFolderRevisionOutput {
            version: 1,
            revision,
            files,
            bars,
        })
        .map_err(|_| failure(ErrorCode::INTERNALERROR))?;
        validate_output(access.actor, READ_LOCAL_FOLDER_REVISION, output)
    }

    pub fn prepare_frozen_folder_source_row(
        &self,
        access: &WorkspaceAccess<'_>,
        request: ReadLocalFolderSourceRowInput,
    ) -> Result<FrozenLocalSourceRow<ReadLocalFolderSourceRowInput>, Error> {
        self.authorize_local_import(access, READ_LOCAL_FOLDER_SOURCE_ROW)?;
        self.authorize_local_import(access, READ_LOCAL_FOLDER_REVISION)?;
        let id = &request.revision_id;
        let target = &request.source;
        let manifest = self
            .port
            .read_folder_manifest(id)?
            .ok_or_else(|| failure(ErrorCode::SNAPSHOTUNAVAILABLE))?;
        let member = manifest.sources.iter().any(|file| {
            file.relative_path == target.relative_path && file.sha256 == target.source_sha256
        });
        let chosen = manifest
            .bars
            .iter()
            .flat_map(|entry| &entry.locators)
            .any(|locator| {
                locator.relative_path == target.relative_path
                    && locator.record_number == target.record_number
                    && locator.line_start == target.line_start
                    && locator.line_end == target.line_end
            });
        if !member || !chosen {
            return Err(failure(ErrorCode::STALECONTEXT));
        }
        // Folder revisions own their display right independently of the anchor.
        let anchor = self
            .port
            .read_folder_manifest_anchor(id)?
            .ok_or_else(|| failure(ErrorCode::SNAPSHOTUNAVAILABLE))?;
        let reviewed = self
            .port
            .load_mapping_version(&anchor.source_key, anchor.revision.review_revision)?
            .ok_or_else(|| failure(ErrorCode::SNAPSHOTUNAVAILABLE))?;
        if local_mapping_fingerprint(&reviewed.mapping)? != anchor.revision.mapping_sha256 {
            return Err(failure(ErrorCode::OUTPUTINVALID));
        }
        let original = self
            .port
            .read_folder_manifest_source(id, &target.relative_path)?
            .ok_or_else(|| failure(ErrorCode::SNAPSHOTUNAVAILABLE))?;
        Ok(FrozenLocalSourceRow {
            original,
            source_sha256: target.source_sha256.clone(),
            mapping: reviewed.mapping,
            review_declarations: false,
            request,
            header_sha256: reviewed.header_sha256,
        })
    }

    pub fn finish_frozen_folder_source_row(
        &self,
        access: &WorkspaceAccess<'_>,
        source: FrozenLocalSourceRow<ReadLocalFolderSourceRowInput>,
        row: Value,
    ) -> Result<Value, Error> {
        self.authorize_local_import(access, READ_LOCAL_FOLDER_SOURCE_ROW)?;
        self.authorize_local_import(access, READ_LOCAL_FOLDER_REVISION)?;
        let id = &source.request.revision_id;
        let target = &source.request.source;
        source.verify(
            &row,
            &LocalImportRowLocator {
                record_number: target.record_number,
                line_start: target.line_start,
                line_end: target.line_end,
            },
        )?;
        let output = bounded_source_row_output(
            access.actor,
            READ_LOCAL_FOLDER_SOURCE_ROW,
            json!({"version": 1, "revision_id": id, "source": target}),
            row,
        )?;
        // Source parsing runs outside the SQLite lane, so display may have changed.
        self.port
            .folder_manifest_summary(id)?
            .ok_or_else(|| failure(ErrorCode::SNAPSHOTUNAVAILABLE))?;
        Ok(output)
    }

    pub fn revoke_frozen_folder_revision(
        &mut self,
        access: &WorkspaceAccess<'_>,
        input: &RevokeLocalFolderRevisionInput,
    ) -> Result<Value, Error> {
        self.authorize_local_import(access, REVOKE_LOCAL_FOLDER_REVISION)?;
        if !self.port.revoke_folder_manifest(&input.revision_id)? {
            return Err(failure(ErrorCode::SNAPSHOTUNAVAILABLE));
        }
        Ok(json!({"version":1,"revision_id":input.revision_id}))
    }
}

pub(crate) fn folder_bars(
    manifest: &StoredFolderManifest,
) -> Result<Vec<FolderRefreshAlternative>, Error> {
    let digests: BTreeMap<_, _> = manifest
        .sources
        .iter()
        .map(|source| (source.relative_path.as_str(), &source.sha256))
        .collect();
    manifest
        .bars
        .iter()
        .map(|entry| {
            let sources = entry
                .locators
                .iter()
                .map(|locator| {
                    let digest = digests
                        .get(locator.relative_path.as_str())
                        .ok_or_else(|| failure(ErrorCode::OUTPUTINVALID))?;
                    Ok(FolderRefreshSource {
                        relative_path: locator.relative_path.clone(),
                        source_sha256: (*digest).clone(),
                        record_number: locator.record_number,
                        line_start: locator.line_start,
                        line_end: locator.line_end,
                    })
                })
                .collect::<Result<_, Error>>()?;
            Ok(FolderRefreshAlternative {
                bar: entry.bar.clone(),
                sources,
            })
        })
        .collect()
}

fn publication_reply(
    actor: Actor,
    revision: &PublishedLocalFolderRevision,
) -> Result<Value, Error> {
    validate_output(
        actor,
        PUBLISH_LOCAL_FOLDER_REFRESH,
        json!({"version":1,"revision":revision}),
    )
}

fn validate_output(actor: Actor, operation: &str, output: Value) -> Result<Value, Error> {
    if output.to_string().len() >= max_message_bytes() {
        return Err(failure(ErrorCode::RESOURCELIMIT));
    }
    if !authorized_operation(actor, operation)?
        .output
        .is_valid(&output)
    {
        return Err(failure(ErrorCode::OUTPUTINVALID));
    }
    Ok(output)
}
