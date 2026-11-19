use rusqlite::{Connection, OptionalExtension, TransactionBehavior, params};
use scopehub_application::{
    LocalDatasetPublication, LocalDatasetStore, MappingCommitGuard, StoredLocalDataset,
};
use scopehub_domain::protocol::{LocalImportRevisionBarSummary, LocalImportRevisionListEntry};
use uuid::Uuid;

use super::{
    Error, ErrorCode, SqliteWorkspace, failure,
    reviewed_import::{check_source_key, is_sha256, latest_mapping},
    revision, store_error,
};

impl LocalDatasetStore for SqliteWorkspace {
    fn publish_local_dataset(
        &mut self,
        candidate: &LocalDatasetPublication<'_>,
        guard: &impl MappingCommitGuard,
    ) -> Result<String, Error> {
        check_source_key(candidate.source_key)?;
        if !is_sha256(candidate.source_sha256)
            || !is_sha256(candidate.mapping_sha256)
            || candidate.relative_path.is_empty()
            || candidate.relative_path.chars().count() > 32768
            || candidate.relative_path.contains('\0')
            || candidate.original.is_empty()
        {
            return Err(failure(ErrorCode::INVALIDARGUMENT));
        }

        let transaction = self
            .connection
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(store_error)?;
        // The reviewed version may have changed while the complete file was parsed.
        if latest_mapping(&transaction, candidate.source_key)?.as_ref()
            != Some(candidate.reviewed_mapping)
        {
            return Err(failure(ErrorCode::STALECONTEXT));
        }
        let review_revision = i64::try_from(candidate.reviewed_mapping.review_revision)
            .map_err(|_| failure(ErrorCode::INTERNALERROR))?;
        let existing: Option<(String, bool)> = transaction
            .query_row(
                "SELECT revision.revision_id, revocation.revision_id IS NOT NULL
                 FROM local_dataset_revisions AS revision
                 LEFT JOIN local_dataset_display_revocations AS revocation USING (revision_id)
                 WHERE revision.source_key = ?1 AND revision.source_sha256 = ?2
                       AND revision.review_revision = ?3",
                params![
                    candidate.source_key,
                    candidate.source_sha256,
                    review_revision
                ],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .optional()
            .map_err(store_error)?;
        retain_original(&transaction, candidate.source_sha256, candidate.original)?;
        let revision_id = match existing {
            Some((_, true)) => return Err(failure(ErrorCode::PERMISSIONDENIED)),
            Some((id, false)) => id,
            None => {
                let id = Uuid::new_v4().to_string();
                let content = serde_json::to_string(candidate.content)
                    .map_err(|_| failure(ErrorCode::INTERNALERROR))?;
                transaction
                    .execute(
                        "INSERT INTO local_dataset_revisions
                            (revision_id, source_key, relative_path, source_sha256,
                             review_revision, mapping_sha256, content_json)
                         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
                        params![
                            id,
                            candidate.source_key,
                            candidate.relative_path,
                            candidate.source_sha256,
                            review_revision,
                            candidate.mapping_sha256,
                            content,
                        ],
                    )
                    .map_err(store_error)?;
                id
            }
        };
        guard.commit(|| transaction.commit().map_err(store_error))?;
        Ok(revision_id)
    }

    fn list_local_datasets(
        &self,
        after_revision_id: Option<&str>,
        limit: usize,
        include_bar_summary: bool,
    ) -> Result<Vec<LocalImportRevisionListEntry>, Error> {
        let after = after_revision_id.unwrap_or_default();
        if after_revision_id.is_some() {
            check_revision_id(after)?;
        }
        let limit = i64::try_from(limit).map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
        let mut statement = self
            .connection
            .prepare(
                "SELECT revision.revision_id, revision.relative_path, revision.source_sha256,
                        json_extract(revision.content_json, '$.symbol'),
                        json_extract(revision.content_json, '$.series.timeframe'),
                        json_array_length(revision.content_json, '$.bars'),
                        json_extract(revision.content_json, '$.source_record_count'),
                        CASE WHEN ?3 THEN json_extract(revision.content_json, '$.series.venue') END,
                        CASE WHEN ?3 THEN json_extract(revision.content_json, '$.bars[0].bar.trading_day') END,
                        CASE WHEN ?3 THEN json_extract(revision.content_json, '$.bars[#-1].bar.trading_day') END,
                        CASE WHEN ?3 THEN json_extract(revision.content_json, '$.bars[#-1].bar.close') END
                 FROM local_dataset_revisions AS revision
                 WHERE revision.revision_id > ?1
                   AND NOT EXISTS (
                       SELECT 1 FROM local_dataset_display_revocations AS revocation
                       WHERE revocation.revision_id = revision.revision_id
                   )
                 ORDER BY revision.revision_id ASC
                 LIMIT ?2",
            )
            .map_err(store_error)?;
        statement
            .query_map(params![after, limit, include_bar_summary], |row| {
                let bar_summary = if include_bar_summary {
                    Some(LocalImportRevisionBarSummary {
                        venue: row.get(7)?,
                        first_trading_day: row.get(8)?,
                        final_trading_day: row.get(9)?,
                        final_close: row.get(10)?,
                    })
                } else {
                    None
                };
                Ok(LocalImportRevisionListEntry {
                    revision_id: row.get(0)?,
                    relative_path: row.get(1)?,
                    source_sha256: row.get(2)?,
                    symbol: row.get(3)?,
                    timeframe: row.get(4)?,
                    bar_count: u64::from(row.get::<_, u32>(5)?),
                    source_record_count: u64::from(row.get::<_, u32>(6)?),
                    bar_summary,
                })
            })
            .map_err(store_error)?
            .collect::<Result<_, _>>()
            .map_err(store_error)
    }

    fn read_local_dataset(&self, revision_id: &str) -> Result<Option<StoredLocalDataset>, Error> {
        read_local_dataset(&self.connection, revision_id)
    }

    fn read_local_dataset_original(&self, revision_id: &str) -> Result<Option<Vec<u8>>, Error> {
        check_revision_id(revision_id)?;
        self.connection
            .query_row(
                "SELECT source.original_bytes FROM local_dataset_revisions AS revision
                 JOIN local_dataset_source_blobs AS source USING (source_sha256)
                 WHERE revision.revision_id = ?1
                   AND NOT EXISTS (
                       SELECT 1 FROM local_dataset_display_revocations AS revocation
                       WHERE revocation.revision_id = revision.revision_id
                   )",
                [revision_id],
                |row| row.get(0),
            )
            .optional()
            .map_err(store_error)
    }

    fn revoke_local_dataset(&mut self, revision_id: &str) -> Result<bool, Error> {
        check_revision_id(revision_id)?;
        let transaction = self
            .connection
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(store_error)?;
        let added = transaction
            .execute(
                "INSERT INTO local_dataset_display_revocations (revision_id)
                 SELECT revision_id FROM local_dataset_revisions WHERE revision_id = ?1
                 ON CONFLICT (revision_id) DO NOTHING",
                [revision_id],
            )
            .map_err(store_error)?;
        let revoked = added == 1
            || transaction
                .query_row(
                    "SELECT EXISTS(SELECT 1 FROM local_dataset_display_revocations WHERE revision_id = ?1)",
                    [revision_id],
                    |row| row.get(0),
                )
                .map_err(store_error)?;
        transaction.commit().map_err(store_error)?;
        Ok(revoked)
    }
}

pub(super) fn check_revision_id(revision_id: &str) -> Result<(), Error> {
    if !Uuid::parse_str(revision_id).is_ok_and(|id| id.to_string() == revision_id) {
        return Err(failure(ErrorCode::INVALIDARGUMENT));
    }
    Ok(())
}

pub(super) fn display_allowed(connection: &Connection, revision_id: &str) -> Result<bool, Error> {
    check_revision_id(revision_id)?;
    connection
        .query_row(
            "SELECT EXISTS(
                SELECT 1 FROM local_dataset_revisions AS revision
                LEFT JOIN local_dataset_display_revocations AS revoked USING (revision_id)
                WHERE revision.revision_id = ?1 AND revoked.revision_id IS NULL
            )",
            [revision_id],
            |row| row.get(0),
        )
        .map_err(store_error)
}

pub(super) fn retain_original(
    connection: &Connection,
    digest: &str,
    original: &[u8],
) -> Result<(), Error> {
    let identical: Option<bool> = connection
        .query_row(
            "SELECT original_bytes = ?2 FROM local_dataset_source_blobs WHERE source_sha256 = ?1",
            params![digest, original],
            |row| row.get(0),
        )
        .optional()
        .map_err(store_error)?;
    match identical {
        Some(true) => Ok(()),
        Some(false) => Err(failure(ErrorCode::INTERNALERROR)),
        None => connection
            .execute(
                "INSERT INTO local_dataset_source_blobs (source_sha256, original_bytes) VALUES (?1, ?2)",
                params![digest, original],
            )
            .map(|_| ())
            .map_err(store_error),
    }
}

pub(super) fn read_local_dataset(
    connection: &Connection,
    revision_id: &str,
) -> Result<Option<StoredLocalDataset>, Error> {
    check_revision_id(revision_id)?;
    let row = connection
        .query_row(
            "SELECT revision.relative_path, revision.source_sha256, revision.mapping_sha256,
                    revision.review_revision,
                    CASE WHEN revocation.revision_id IS NULL THEN revision.content_json END,
                    revision.source_key
             FROM local_dataset_revisions AS revision
             LEFT JOIN local_dataset_display_revocations AS revocation USING (revision_id)
             WHERE revision.revision_id = ?1",
            [revision_id],
            |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    revision(row, 3)?,
                    row.get::<_, Option<String>>(4)?,
                    row.get::<_, String>(5)?,
                ))
            },
        )
        .optional()
        .map_err(store_error)?;
    row.map(
        |(relative_path, source_sha256, mapping_sha256, review_revision, content, source_key)| {
            let content = content.ok_or_else(|| failure(ErrorCode::PERMISSIONDENIED))?;
            let content =
                serde_json::from_str(&content).map_err(|_| failure(ErrorCode::INTERNALERROR))?;
            Ok(StoredLocalDataset {
                source_key,
                revision_id: revision_id.to_owned(),
                relative_path,
                source_sha256,
                mapping_sha256,
                review_revision,
                content,
            })
        },
    )
    .transpose()
}
