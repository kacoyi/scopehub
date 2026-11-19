use rusqlite::{Connection, OptionalExtension, Row, TransactionBehavior, params, types::Type};
use scopehub_application::{
    FolderManifestAnchor, FolderManifestBar, FolderManifestLocator, FolderManifestPublication,
    FolderManifestRequest, FolderManifestSourceRef, FolderManifestStore, MappingCommitGuard,
    StoredFolderManifest,
};
use scopehub_domain::protocol::{
    LocalImportRevisionBarSummary, PreviewSeries, PublishedLocalFolderRevision,
    PublishedLocalImportRevision,
};
use uuid::Uuid;

use super::{
    Error, ErrorCode, SqliteWorkspace, failure,
    local_dataset::{check_revision_id, read_local_dataset, retain_original},
    reviewed_import::is_sha256,
    store_error,
};

impl FolderManifestStore for SqliteWorkspace {
    fn publish_folder_manifest(
        &mut self,
        request: &FolderManifestRequest<'_>,
        candidate: &FolderManifestPublication<'_>,
        guard: &impl MappingCommitGuard,
    ) -> Result<String, Error> {
        check_request(&self.binding.profile_id, request)?;
        let workspace_id = &self.binding.workspace_id;
        let transaction = self
            .connection
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(store_error)?;
        if let Some(revision_id) = replay(
            &transaction,
            workspace_id,
            request,
            candidate.input_sha256(),
        )? {
            return Ok(revision_id);
        }

        let parent_id = candidate.parent_id();
        let (base_id, parent_folder_id) = match revision(&transaction, parent_id)? {
            Some(parent) => {
                require_display(parent.revoked)?;
                (parent.base_revision_id, Some(parent_id))
            }
            None => {
                read_local_dataset(&transaction, parent_id)?
                    .ok_or_else(|| failure(ErrorCode::SNAPSHOTUNAVAILABLE))?;
                (parent_id.to_owned(), None)
            }
        };
        let revision_id = match existing_revision(&transaction, candidate.input_sha256())? {
            Some(revision_id) => revision_id,
            None => insert_revision(&transaction, &base_id, parent_folder_id, candidate)?,
        };
        transaction
            .execute(
                "INSERT INTO local_folder_manifest_receipts
                    (workspace_id, principal_id, idempotency_key, revision_id)
                 VALUES (?1, ?2, ?3, ?4)",
                params![
                    workspace_id,
                    request.principal_id,
                    request.idempotency_key,
                    revision_id
                ],
            )
            .map_err(store_error)?;
        guard.commit(|| {
            transaction.commit().map_err(store_error)?;
            Ok(revision_id)
        })
    }

    fn replay_folder_manifest(
        &self,
        request: &FolderManifestRequest<'_>,
        input_sha256: &str,
    ) -> Result<Option<String>, Error> {
        check_request(&self.binding.profile_id, request)?;
        if !is_sha256(input_sha256) {
            return Err(failure(ErrorCode::INVALIDARGUMENT));
        }
        replay(
            &self.connection,
            &self.binding.workspace_id,
            request,
            input_sha256,
        )
    }

    fn read_folder_manifest(
        &self,
        revision_id: &str,
    ) -> Result<Option<StoredFolderManifest>, Error> {
        check_revision_id(revision_id)?;
        let transaction = self
            .connection
            .unchecked_transaction()
            .map_err(store_error)?;
        let Some(header) = revision(&transaction, revision_id)? else {
            return Ok(None);
        };
        require_display(header.revoked)?;
        let sources = read_sources(&transaction, revision_id)?;
        let bars = read_bars(&transaction, revision_id)?;
        transaction.commit().map_err(store_error)?;
        Ok(Some(StoredFolderManifest {
            revision_id: revision_id.to_owned(),
            base_revision_id: header.base_revision_id,
            parent_revision_id: header.parent_revision_id,
            sources,
            bars,
        }))
    }

    fn read_folder_manifest_anchor(
        &self,
        revision_id: &str,
    ) -> Result<Option<FolderManifestAnchor>, Error> {
        check_revision_id(revision_id)?;
        let anchor = self.connection.query_row(
            "SELECT base.source_key, base.revision_id, base.relative_path, base.source_sha256,
                    base.mapping_sha256, base.review_revision,
                    json_extract(base.content_json, '$.symbol'),
                    json_extract(base.content_json, '$.series'),
                    json_array_length(base.content_json, '$.bars'),
                    json_extract(base.content_json, '$.source_record_count'),
                    revoked.revision_id IS NOT NULL
             FROM local_folder_manifest_revisions AS folder
             JOIN local_dataset_revisions AS base ON base.revision_id = folder.base_revision_id
             LEFT JOIN local_folder_manifest_revocations AS revoked ON revoked.revision_id = folder.revision_id
             WHERE folder.revision_id = ?1",
            [revision_id],
            |row| Ok((FolderManifestAnchor {
                source_key: row.get(0)?,
                revision: PublishedLocalImportRevision {
                    revision_id: row.get(1)?, relative_path: row.get(2)?, source_sha256: row.get(3)?,
                    mapping_sha256: row.get(4)?, review_revision: super::revision(row, 5)?,
                    symbol: row.get(6)?, series: series(row, 7)?,
                    bar_count: u64::from(row.get::<_, u32>(8)?),
                    source_record_count: u64::from(row.get::<_, u32>(9)?),
                },
            }, row.get::<_, bool>(10)?)),
        ).optional().map_err(store_error)?;
        anchor
            .map(|(anchor, revoked)| {
                require_display(revoked)?;
                Ok(anchor)
            })
            .transpose()
    }

    fn folder_manifest_summary(
        &self,
        revision_id: &str,
    ) -> Result<Option<PublishedLocalFolderRevision>, Error> {
        check_revision_id(revision_id)?;
        let result = self
            .connection
            .query_row(
                &format!("{SUMMARY_QUERY} WHERE folder.revision_id = ?1"),
                [revision_id],
                |row| Ok((summary(row)?, row.get::<_, bool>(8)?)),
            )
            .optional()
            .map_err(store_error)?;
        result
            .map(|(revision, revoked)| {
                require_display(revoked)?;
                Ok(revision)
            })
            .transpose()
    }

    fn list_folder_manifest_summaries(
        &self,
        after_revision_id: Option<&str>,
        limit: usize,
    ) -> Result<Vec<PublishedLocalFolderRevision>, Error> {
        if let Some(after) = after_revision_id {
            check_revision_id(after)?;
        }
        let limit = i64::try_from(limit).map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
        let mut statement = self
            .connection
            .prepare(&format!(
                "{SUMMARY_QUERY}
            WHERE folder.revision_id > ?1 AND revoked.revision_id IS NULL
            ORDER BY folder.revision_id LIMIT ?2"
            ))
            .map_err(store_error)?;
        statement
            .query_map(
                params![after_revision_id.unwrap_or_default(), limit],
                summary,
            )
            .map_err(store_error)?
            .collect::<Result<_, _>>()
            .map_err(store_error)
    }

    fn read_folder_manifest_source(
        &self,
        revision_id: &str,
        relative_path: &str,
    ) -> Result<Option<Vec<u8>>, Error> {
        check_revision_id(revision_id)?;
        let row: Option<(bool, Option<Vec<u8>>)> = self
            .connection
            .query_row(
                "SELECT revoked.revision_id IS NOT NULL,
                    CASE WHEN revoked.revision_id IS NULL THEN blob.original_bytes END
             FROM local_folder_manifest_revisions AS revision
             LEFT JOIN local_folder_manifest_revocations AS revoked USING (revision_id)
             LEFT JOIN local_folder_manifest_sources AS source
               ON source.revision_id = revision.revision_id AND source.relative_path = ?2
             LEFT JOIN local_dataset_source_blobs AS blob USING (source_sha256)
             WHERE revision.revision_id = ?1",
                params![revision_id, relative_path],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .optional()
            .map_err(store_error)?;
        match row {
            Some((true, _)) => Err(failure(ErrorCode::PERMISSIONDENIED)),
            Some((false, bytes)) => Ok(bytes),
            None => Ok(None),
        }
    }

    fn revoke_folder_manifest(&mut self, revision_id: &str) -> Result<bool, Error> {
        check_revision_id(revision_id)?;
        let transaction = self
            .connection
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(store_error)?;
        if revision(&transaction, revision_id)?.is_none() {
            return Ok(false);
        }
        transaction
            .execute(
                "INSERT INTO local_folder_manifest_revocations (revision_id) VALUES (?1)
             ON CONFLICT (revision_id) DO NOTHING",
                [revision_id],
            )
            .map_err(store_error)?;
        transaction.commit().map_err(store_error)?;
        Ok(true)
    }
}

const SUMMARY_QUERY: &str = "SELECT folder.revision_id, folder.base_revision_id, folder.parent_revision_id,
        base.relative_path, json_extract(base.content_json, '$.symbol'),
        json_extract(base.content_json, '$.series'),
        (SELECT count(*) FROM local_folder_manifest_sources AS source WHERE source.revision_id = folder.revision_id),
        (SELECT count(*) FROM local_folder_manifest_bars AS bar WHERE bar.revision_id = folder.revision_id),
        revoked.revision_id IS NOT NULL,
        (SELECT json_extract(bar.bar_json, '$.trading_day') FROM local_folder_manifest_bars AS bar
            WHERE bar.revision_id = folder.revision_id ORDER BY bar.time_ms LIMIT 1),
        (SELECT json_extract(bar.bar_json, '$.trading_day') FROM local_folder_manifest_bars AS bar
            WHERE bar.revision_id = folder.revision_id ORDER BY bar.time_ms DESC LIMIT 1),
        (SELECT json_extract(bar.bar_json, '$.close') FROM local_folder_manifest_bars AS bar
            WHERE bar.revision_id = folder.revision_id ORDER BY bar.time_ms DESC LIMIT 1)
    FROM local_folder_manifest_revisions AS folder
    JOIN local_dataset_revisions AS base ON base.revision_id = folder.base_revision_id
    LEFT JOIN local_folder_manifest_revocations AS revoked ON revoked.revision_id = folder.revision_id";

fn summary(row: &Row<'_>) -> rusqlite::Result<PublishedLocalFolderRevision> {
    let series = series(row, 5)?;
    let bar_count = u64::from(row.get::<_, u32>(7)?);
    let bar_summary = if bar_count == 0 {
        None
    } else {
        Some(LocalImportRevisionBarSummary {
            venue: series.venue.clone(),
            first_trading_day: row.get(9)?,
            final_trading_day: row.get(10)?,
            final_close: row.get(11)?,
        })
    };
    Ok(PublishedLocalFolderRevision {
        revision_id: row.get(0)?,
        base_revision_id: row.get(1)?,
        parent_revision_id: row.get(2)?,
        origin_path: row.get(3)?,
        symbol: row.get(4)?,
        series,
        source_count: u64::from(row.get::<_, u32>(6)?),
        bar_count,
        bar_summary,
    })
}

fn series(row: &Row<'_>, column: usize) -> rusqlite::Result<PreviewSeries> {
    let json: String = row.get(column)?;
    serde_json::from_str(&json).map_err(|error| {
        rusqlite::Error::FromSqlConversionFailure(column, Type::Text, Box::new(error))
    })
}

struct ManifestRevision {
    base_revision_id: String,
    parent_revision_id: Option<String>,
    revoked: bool,
}

fn require_display(revoked: bool) -> Result<(), Error> {
    if revoked {
        Err(failure(ErrorCode::PERMISSIONDENIED))
    } else {
        Ok(())
    }
}

fn revision(connection: &Connection, revision_id: &str) -> Result<Option<ManifestRevision>, Error> {
    connection
        .query_row(
            "SELECT base_revision_id, parent_revision_id, revoked.revision_id IS NOT NULL
             FROM local_folder_manifest_revisions AS revision
             LEFT JOIN local_folder_manifest_revocations AS revoked USING (revision_id)
             WHERE revision.revision_id = ?1",
            [revision_id],
            |row| {
                Ok(ManifestRevision {
                    base_revision_id: row.get(0)?,
                    parent_revision_id: row.get(1)?,
                    revoked: row.get(2)?,
                })
            },
        )
        .optional()
        .map_err(store_error)
}

fn check_request(profile_id: &str, request: &FolderManifestRequest<'_>) -> Result<(), Error> {
    if request.principal_id != profile_id {
        return Err(failure(ErrorCode::PERMISSIONDENIED));
    }
    let key = request.idempotency_key;
    if key.is_empty() || key.len() > 128 || key.contains('\0') {
        return Err(failure(ErrorCode::INVALIDARGUMENT));
    }
    Ok(())
}

fn replay(
    connection: &Connection,
    workspace_id: &str,
    request: &FolderManifestRequest<'_>,
    input_sha256: &str,
) -> Result<Option<String>, Error> {
    let row: Option<(String, String, bool)> = connection.query_row(
        "SELECT revision.revision_id, revision.input_sha256, revoked.revision_id IS NOT NULL
         FROM local_folder_manifest_receipts AS receipt
         JOIN local_folder_manifest_revisions AS revision USING (revision_id)
         LEFT JOIN local_folder_manifest_revocations AS revoked USING (revision_id)
         WHERE receipt.workspace_id = ?1 AND receipt.principal_id = ?2 AND receipt.idempotency_key = ?3",
        params![workspace_id, request.principal_id, request.idempotency_key],
        |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
    ).optional().map_err(store_error)?;
    row.map(|(revision_id, recorded_sha256, revoked)| {
        if recorded_sha256 != input_sha256 {
            return Err(failure(ErrorCode::INVALIDARGUMENT));
        }
        require_display(revoked)?;
        Ok(revision_id)
    })
    .transpose()
}

fn existing_revision(connection: &Connection, input_sha256: &str) -> Result<Option<String>, Error> {
    let row: Option<(String, bool)> = connection
        .query_row(
            "SELECT revision.revision_id, revoked.revision_id IS NOT NULL
         FROM local_folder_manifest_revisions AS revision
         LEFT JOIN local_folder_manifest_revocations AS revoked USING (revision_id)
         WHERE revision.input_sha256 = ?1",
            [input_sha256],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .optional()
        .map_err(store_error)?;
    row.map(|(revision_id, revoked)| {
        require_display(revoked)?;
        Ok(revision_id)
    })
    .transpose()
}

fn insert_revision(
    connection: &Connection,
    base_revision_id: &str,
    parent_revision_id: Option<&str>,
    candidate: &FolderManifestPublication<'_>,
) -> Result<String, Error> {
    let revision_id = Uuid::new_v4().to_string();
    connection
        .execute(
            "INSERT INTO local_folder_manifest_revisions
            (revision_id, base_revision_id, parent_revision_id, input_sha256)
         VALUES (?1, ?2, ?3, ?4)",
            params![
                revision_id,
                base_revision_id,
                parent_revision_id,
                candidate.input_sha256()
            ],
        )
        .map_err(store_error)?;
    let mut source_insert = connection
        .prepare(
            "INSERT INTO local_folder_manifest_sources
            (revision_id, relative_path, source_sha256, record_count) VALUES (?1, ?2, ?3, ?4)",
        )
        .map_err(store_error)?;
    for source in candidate.sources() {
        retain_original(connection, source.sha256, source.original)?;
        source_insert
            .execute(params![
                revision_id,
                source.relative_path,
                source.sha256,
                source.record_count as i64
            ])
            .map_err(store_error)?;
    }
    let mut bar_insert = connection.prepare(
        "INSERT INTO local_folder_manifest_bars (revision_id, time_ms, bar_json) VALUES (?1, ?2, ?3)",
    ).map_err(store_error)?;
    let mut locator_insert = connection
        .prepare(
            "INSERT INTO local_folder_manifest_locators
            (revision_id, relative_path, record_number, time_ms, line_start, line_end)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
        )
        .map_err(store_error)?;
    for entry in candidate.bars() {
        let time_ms = entry.bar.time_ms;
        let bar =
            serde_json::to_string(&entry.bar).map_err(|_| failure(ErrorCode::INTERNALERROR))?;
        bar_insert
            .execute(params![revision_id, time_ms, bar])
            .map_err(store_error)?;
        for locator in &entry.locators {
            locator_insert
                .execute(params![
                    revision_id,
                    locator.relative_path,
                    locator.record_number as i64,
                    time_ms,
                    locator.line_start as i64,
                    locator.line_end as i64,
                ])
                .map_err(store_error)?;
        }
    }
    Ok(revision_id)
}

fn read_sources(
    connection: &Connection,
    revision_id: &str,
) -> Result<Vec<FolderManifestSourceRef>, Error> {
    let mut statement = connection
        .prepare(
            "SELECT relative_path, source_sha256, record_count FROM local_folder_manifest_sources
         WHERE revision_id = ?1 ORDER BY relative_path",
        )
        .map_err(store_error)?;
    statement
        .query_map([revision_id], |row| {
            Ok(FolderManifestSourceRef {
                relative_path: row.get(0)?,
                sha256: row.get(1)?,
                record_count: u64::from(row.get::<_, u32>(2)?),
            })
        })
        .map_err(store_error)?
        .collect::<Result<_, _>>()
        .map_err(store_error)
}

fn read_bars(connection: &Connection, revision_id: &str) -> Result<Vec<FolderManifestBar>, Error> {
    let mut statement = connection
        .prepare(
            "SELECT bar.time_ms, bar.bar_json, locator.relative_path, locator.record_number,
                locator.line_start, locator.line_end
         FROM local_folder_manifest_bars AS bar
         LEFT JOIN local_folder_manifest_locators AS locator USING (revision_id, time_ms)
         WHERE bar.revision_id = ?1
         ORDER BY bar.time_ms, locator.relative_path, locator.record_number",
        )
        .map_err(store_error)?;
    let mut rows = statement.query([revision_id]).map_err(store_error)?;
    let mut bars = Vec::<FolderManifestBar>::new();
    while let Some(row) = rows.next().map_err(store_error)? {
        let time_ms: i64 = row.get(0).map_err(store_error)?;
        if bars.last().is_none_or(|entry| entry.bar.time_ms != time_ms) {
            let bar_json: String = row.get(1).map_err(store_error)?;
            let bar =
                serde_json::from_str(&bar_json).map_err(|_| failure(ErrorCode::INTERNALERROR))?;
            bars.push(FolderManifestBar {
                bar,
                locators: Vec::new(),
            });
        }
        let entry = bars.last_mut().expect("current folder manifest bar");
        entry.locators.push(FolderManifestLocator {
            relative_path: row.get(2).map_err(store_error)?,
            record_number: u64::from(row.get::<_, u32>(3).map_err(store_error)?),
            line_start: u64::from(row.get::<_, u32>(4).map_err(store_error)?),
            line_end: u64::from(row.get::<_, u32>(5).map_err(store_error)?),
        });
    }
    Ok(bars)
}
