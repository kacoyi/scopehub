use rusqlite::{Connection, OptionalExtension, params, types::Type};
use scopehub_application::{PrivateRecordPlan, WorkspaceChange, failure};
use scopehub_domain::protocol::{
    Error, ErrorCode, PrivateRecordArchiveEntry, PrivateRecordContent, PrivateRecordEntry,
    PrivateRecordKind, PrivateRecordSummary,
};

use super::{checkpoint_private_pages, revision, store_error};

pub(super) fn read(
    connection: &Connection,
    workspace_id: &str,
    id: &str,
) -> Result<Option<PrivateRecordEntry>, Error> {
    connection
        .query_row(
            "SELECT record_id, revision, content FROM private_records
             WHERE workspace_id = ?1 AND record_id = ?2",
            params![workspace_id, id],
            |row| {
                Ok(PrivateRecordEntry {
                    record_id: row.get(0)?,
                    revision: revision(row, 1)?,
                    content: decode(row.get_ref(2)?.as_str()?, 2)?,
                })
            },
        )
        .optional()
        .map_err(store_error)
}

pub(super) fn index(
    connection: &Connection,
    workspace_id: &str,
) -> Result<Vec<PrivateRecordSummary>, Error> {
    let mut statement = connection
        .prepare(
            "SELECT record_id, revision, json_extract(content, '$.kind'),
                    json_extract(content, '$.title')
             FROM private_records WHERE workspace_id = ?1 ORDER BY revision DESC, record_id",
        )
        .map_err(store_error)?;
    statement
        .query_map([workspace_id], |row| {
            let kind = match row.get_ref(2)?.as_str()? {
                "conversation" => PrivateRecordKind::Conversation,
                "memory" => PrivateRecordKind::Memory,
                "strategy" => PrivateRecordKind::Strategy,
                _ => return Err(rusqlite::Error::InvalidQuery),
            };
            Ok(PrivateRecordSummary {
                record_id: row.get(0)?,
                revision: revision(row, 1)?,
                kind,
                title: row.get(3)?,
            })
        })
        .map_err(store_error)?
        .collect::<rusqlite::Result<Vec<_>>>()
        .map_err(store_error)
}

pub(super) fn archive(
    connection: &Connection,
    workspace_id: &str,
) -> Result<Vec<PrivateRecordArchiveEntry>, Error> {
    let mut statement = connection
        .prepare("SELECT record_id, content FROM private_records WHERE workspace_id = ?1 ORDER BY record_id")
        .map_err(store_error)?;
    statement
        .query_map([workspace_id], |row| {
            Ok(PrivateRecordArchiveEntry {
                record_id: row.get(0)?,
                content: decode(row.get_ref(1)?.as_str()?, 1)?,
            })
        })
        .map_err(store_error)?
        .collect::<rusqlite::Result<Vec<_>>>()
        .map_err(store_error)
}

fn decode(content: &str, column: usize) -> rusqlite::Result<PrivateRecordContent> {
    serde_json::from_str(content).map_err(|error| {
        rusqlite::Error::FromSqlConversionFailure(column, Type::Text, Box::new(error))
    })
}

pub(super) fn deletions(connection: &Connection, workspace_id: &str) -> Result<Vec<String>, Error> {
    let mut statement = connection
        .prepare("SELECT record_id FROM private_record_deletions WHERE workspace_id = ?1 ORDER BY record_id")
        .map_err(store_error)?;
    statement
        .query_map([workspace_id], |row| row.get(0))
        .map_err(store_error)?
        .collect::<rusqlite::Result<Vec<_>>>()
        .map_err(store_error)
}

pub(super) fn receipt_target(
    connection: &Connection,
    operation_id: &str,
) -> Result<Option<String>, Error> {
    connection
        .query_row(
            "SELECT record_id FROM private_record_receipts WHERE operation_id = ?1",
            [operation_id],
            |row| row.get(0),
        )
        .map_err(store_error)
}

pub(super) fn apply(
    connection: &Connection,
    workspace_id: &str,
    change: &WorkspaceChange<'_>,
    plan: &PrivateRecordPlan,
) -> Result<(), Error> {
    let receipt = &change.receipt;
    let revision =
        i64::try_from(receipt.revision).map_err(|_| failure(ErrorCode::INTERNALERROR))?;
    connection
        .execute(
            "INSERT INTO private_record_receipts (operation_id, record_id) VALUES (?1, ?2)",
            params![receipt.operation_id, plan.record_id],
        )
        .map_err(store_error)?;
    let mut reclaim = false;
    for id in &plan.deletions {
        reclaim |= connection
            .execute(
                "DELETE FROM private_records WHERE workspace_id = ?1 AND record_id = ?2",
                params![workspace_id, id],
            )
            .map_err(store_error)?
            != 0;
        connection
            .execute(
                "INSERT INTO private_record_deletions (workspace_id, record_id, revision) VALUES (?1, ?2, ?3)",
                params![workspace_id, id, revision],
            )
            .map_err(store_error)?;
    }
    if let Some(entry) = &plan.corrected {
        let changed = connection
            .execute(
                "UPDATE private_records SET revision = ?3, content = ?4
                 WHERE workspace_id = ?1 AND record_id = ?2",
                params![workspace_id, entry.record_id, revision, encode(entry)?],
            )
            .map_err(store_error)?;
        if changed != 1 {
            return Err(failure(ErrorCode::STALECONTEXT));
        }
        reclaim = true;
    }
    for entry in &plan.added {
        connection
            .execute(
                "INSERT INTO private_records (workspace_id, record_id, revision, content)
                 VALUES (?1, ?2, ?3, ?4)",
                params![workspace_id, entry.record_id, revision, encode(entry)?],
            )
            .map_err(store_error)?;
    }
    if reclaim {
        connection
            .execute(
                "INSERT INTO private_record_cleanup (workspace_id, revision) VALUES (?1, ?2)
                 ON CONFLICT(workspace_id) DO UPDATE SET revision = excluded.revision",
                params![workspace_id, revision],
            )
            .map_err(store_error)?;
    }
    Ok(())
}

fn encode(entry: &PrivateRecordArchiveEntry) -> Result<String, Error> {
    serde_json::to_string(&entry.content).map_err(|_| failure(ErrorCode::INTERNALERROR))
}

pub(super) fn clean(connection: &Connection, workspace_id: &str) -> rusqlite::Result<()> {
    let watermark: Option<i64> = connection
        .query_row(
            "SELECT revision FROM private_record_cleanup WHERE workspace_id = ?1",
            [workspace_id],
            |row| row.get(0),
        )
        .optional()?;
    if let Some(watermark) = watermark {
        checkpoint_private_pages(connection)?;
        connection.execute(
            "DELETE FROM private_record_cleanup WHERE workspace_id = ?1 AND revision = ?2",
            params![workspace_id, watermark],
        )?;
    }
    Ok(())
}
