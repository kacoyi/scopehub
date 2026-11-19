use rusqlite::{Connection, OptionalExtension, TransactionBehavior, params};
use scopehub_application::{MappingCommitGuard, ReviewedImportMapping, ReviewedMappingStore};
use scopehub_domain::protocol::CsvMappingV1;

use super::{Error, ErrorCode, SqliteWorkspace, failure, revision, store_error};

const MAX_REVIEW_REVISION: i64 = 9_007_199_254_740_991;

pub(super) fn is_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

pub(super) fn check_source_key(source_key: &str) -> Result<(), Error> {
    if source_key.is_empty() || source_key.len() > 256 || source_key.contains('\0') {
        return Err(failure(ErrorCode::INVALIDARGUMENT));
    }
    Ok(())
}

pub(super) fn latest_mapping(
    connection: &Connection,
    source_key: &str,
) -> Result<Option<ReviewedImportMapping>, Error> {
    let mapping = connection
        .query_row(
            "SELECT review_revision, header_sha256, mapping_json
                 FROM reviewed_import_mappings
                 WHERE source_key = ?1
                 ORDER BY review_revision DESC LIMIT 1",
            [source_key],
            |row| {
                Ok((
                    revision(row, 0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                ))
            },
        )
        .optional()
        .map_err(store_error)?;
    mapping
        .map(|(review_revision, header_sha256, mapping_json)| {
            if !is_sha256(&header_sha256) {
                return Err(failure(ErrorCode::INTERNALERROR));
            }
            let mapping = serde_json::from_str(&mapping_json)
                .map_err(|_| failure(ErrorCode::INTERNALERROR))?;
            Ok(ReviewedImportMapping {
                review_revision,
                header_sha256,
                mapping,
            })
        })
        .transpose()
}

impl ReviewedMappingStore for SqliteWorkspace {
    fn load_mapping(&self, source_key: &str) -> Result<Option<ReviewedImportMapping>, Error> {
        check_source_key(source_key)?;
        latest_mapping(&self.connection, source_key)
    }

    fn save_mapping(
        &mut self,
        source_key: &str,
        header_sha256: &str,
        mapping: &CsvMappingV1,
        guard: &impl MappingCommitGuard,
    ) -> Result<ReviewedImportMapping, Error> {
        check_source_key(source_key)?;
        if !is_sha256(header_sha256) {
            return Err(failure(ErrorCode::INVALIDARGUMENT));
        }
        let mapping_json =
            serde_json::to_string(mapping).map_err(|_| failure(ErrorCode::INTERNALERROR))?;
        let transaction = self
            .connection
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(store_error)?;
        let previous_revision: i64 = transaction
            .query_row(
                "SELECT coalesce(max(review_revision), 0) FROM reviewed_import_mappings
                 WHERE source_key = ?1",
                [source_key],
                |row| row.get(0),
            )
            .map_err(store_error)?;
        if previous_revision >= MAX_REVIEW_REVISION {
            return Err(failure(ErrorCode::RESOURCELIMIT));
        }
        let review_revision = previous_revision + 1;
        transaction
            .execute(
                "INSERT INTO reviewed_import_mappings
                    (source_key, review_revision, header_sha256, mapping_json)
                 VALUES (?1, ?2, ?3, ?4)",
                params![source_key, review_revision, header_sha256, mapping_json],
            )
            .map_err(store_error)?;
        let review_revision =
            u64::try_from(review_revision).map_err(|_| failure(ErrorCode::INTERNALERROR))?;
        guard.commit(|| transaction.commit().map_err(store_error))?;
        Ok(ReviewedImportMapping {
            review_revision,
            header_sha256: header_sha256.into(),
            mapping: mapping.clone(),
        })
    }
}
