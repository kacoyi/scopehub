use rusqlite::{Connection, OptionalExtension, TransactionBehavior, params};
use scopehub_application::{MappingCommitGuard, ReviewedImportMapping, ReviewedMappingStore};
use scopehub_domain::protocol::CsvMappingV1;

use super::{Error, ErrorCode, SqliteWorkspace, failure, revision, store_error};

const MAX_REVIEW_REVISION: u64 = 9_007_199_254_740_991;

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
    let row = connection
        .query_row(
            "SELECT review_revision, header_sha256, mapping_json
                 FROM reviewed_import_mappings
                 WHERE source_key = ?1
                 ORDER BY review_revision DESC LIMIT 1",
            [source_key],
            read_mapping_row,
        )
        .optional()
        .map_err(store_error)?;
    decode_mapping(row)
}

type MappingRow = (u64, String, String);

fn read_mapping_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<MappingRow> {
    Ok((revision(row, 0)?, row.get(1)?, row.get(2)?))
}

fn decode_mapping(row: Option<MappingRow>) -> Result<Option<ReviewedImportMapping>, Error> {
    row.map(|(review_revision, header_sha256, mapping_json)| {
        if !is_sha256(&header_sha256) {
            return Err(failure(ErrorCode::INTERNALERROR));
        }
        let mapping =
            serde_json::from_str(&mapping_json).map_err(|_| failure(ErrorCode::INTERNALERROR))?;
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

    fn load_mapping_version(
        &self,
        source_key: &str,
        review_revision: u64,
    ) -> Result<Option<ReviewedImportMapping>, Error> {
        check_source_key(source_key)?;
        let review_revision =
            i64::try_from(review_revision).map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
        let row = self
            .connection
            .query_row(
                "SELECT review_revision, header_sha256, mapping_json
                 FROM reviewed_import_mappings WHERE source_key = ?1 AND review_revision = ?2",
                params![source_key, review_revision],
                read_mapping_row,
            )
            .optional()
            .map_err(store_error)?;
        decode_mapping(row)
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
        let previous = latest_mapping(&transaction, source_key)?;
        let previous_revision = previous.as_ref().map_or(0, |record| record.review_revision);
        if let Some(record) = previous
            && record.header_sha256 == header_sha256
            && record.mapping == *mapping
        {
            return guard.commit(|| {
                transaction.commit().map_err(store_error)?;
                Ok(record)
            });
        }
        if previous_revision >= MAX_REVIEW_REVISION {
            return Err(failure(ErrorCode::RESOURCELIMIT));
        }
        let review_revision = previous_revision + 1;
        let sql_revision =
            i64::try_from(review_revision).map_err(|_| failure(ErrorCode::INTERNALERROR))?;
        transaction
            .execute(
                "INSERT INTO reviewed_import_mappings
                    (source_key, review_revision, header_sha256, mapping_json)
                 VALUES (?1, ?2, ?3, ?4)",
                params![source_key, sql_revision, header_sha256, mapping_json],
            )
            .map_err(store_error)?;
        guard.commit(|| transaction.commit().map_err(store_error))?;
        Ok(ReviewedImportMapping {
            review_revision,
            header_sha256: header_sha256.into(),
            mapping: mapping.clone(),
        })
    }
}
