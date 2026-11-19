use rusqlite::{Connection, OptionalExtension, Row, params};
use scopehub_application::{
    DecisionNoteChange, DecisionNoteMutation, DecisionNoteReceipt, DecisionNoteReplay,
    WorkspaceBinding, WorkspaceReplayKey, failure,
};
use scopehub_domain::protocol::{
    DecisionNoteOriginal, DecisionNoteOutcome, DecisionNoteReadOutput, DecisionNoteSummary, Error,
    ErrorCode,
};

use super::{revision, store_error};

const INDEX_PAGE_SIZE: usize = 50;
const OUTCOME_PAGE_SIZE: usize = 10;

fn decode_original(row: &Row<'_>) -> rusqlite::Result<DecisionNoteOriginal> {
    let evidence_refs: String = row.get(3)?;
    Ok(DecisionNoteOriginal {
        note_id: row.get(0)?,
        decided_on: row.get(1)?,
        thesis: row.get(2)?,
        evidence_refs: serde_json::from_str(&evidence_refs)
            .map_err(|_| rusqlite::Error::InvalidQuery)?,
        choice: row.get(4)?,
    })
}

fn original(
    connection: &Connection,
    workspace_id: &str,
    note_id: &str,
) -> Result<Option<DecisionNoteOriginal>, Error> {
    connection
        .query_row(
            "SELECT note_id, decided_on, thesis, evidence_refs, choice FROM decision_notes
             WHERE workspace_id = ?1 AND note_id = ?2",
            params![workspace_id, note_id],
            decode_original,
        )
        .optional()
        .map_err(store_error)
}

pub(super) fn read(
    connection: &Connection,
    workspace_id: &str,
    note_id: Option<&str>,
    offset: u64,
) -> Result<DecisionNoteReadOutput, Error> {
    let sql_offset = i64::try_from(offset).map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
    if let Some(note_id) = note_id {
        let note = original(connection, workspace_id, note_id)?
            .ok_or_else(|| failure(ErrorCode::DATAUNAVAILABLE))?;
        let mut statement = connection
            .prepare(
                "SELECT outcome_id, observed_on, outcome FROM decision_note_outcomes
             WHERE workspace_id = ?1 AND note_id = ?2 ORDER BY revision LIMIT ?3 OFFSET ?4",
            )
            .map_err(store_error)?;
        let mut outcomes = statement
            .query_map(
                params![
                    workspace_id,
                    note_id,
                    OUTCOME_PAGE_SIZE as i64 + 1,
                    sql_offset
                ],
                |row| {
                    Ok(DecisionNoteOutcome {
                        outcome_id: row.get(0)?,
                        observed_on: row.get(1)?,
                        outcome: row.get(2)?,
                    })
                },
            )
            .map_err(store_error)?
            .collect::<rusqlite::Result<Vec<_>>>()
            .map_err(store_error)?;
        let next_offset = trim_page(&mut outcomes, offset, OUTCOME_PAGE_SIZE);
        return Ok(DecisionNoteReadOutput {
            note: Some(note),
            notes: vec![],
            outcomes,
            next_offset,
        });
    }

    let mut statement = connection
        .prepare(
            "SELECT note_id, decided_on FROM decision_notes WHERE workspace_id = ?1
         ORDER BY decided_on DESC, note_id LIMIT ?2 OFFSET ?3",
        )
        .map_err(store_error)?;
    let mut notes = statement
        .query_map(
            params![workspace_id, INDEX_PAGE_SIZE as i64 + 1, sql_offset],
            |row| {
                Ok(DecisionNoteSummary {
                    note_id: row.get(0)?,
                    decided_on: row.get(1)?,
                })
            },
        )
        .map_err(store_error)?
        .collect::<rusqlite::Result<Vec<_>>>()
        .map_err(store_error)?;
    let next_offset = trim_page(&mut notes, offset, INDEX_PAGE_SIZE);
    Ok(DecisionNoteReadOutput {
        note: None,
        notes,
        outcomes: vec![],
        next_offset,
    })
}

fn trim_page<T>(items: &mut Vec<T>, offset: u64, page_size: usize) -> Option<u64> {
    let more = items.len() > page_size;
    items.truncate(page_size);
    more.then_some(offset + page_size as u64)
}

pub(super) fn is_deleted(
    connection: &Connection,
    binding: &WorkspaceBinding,
    note_id: &str,
) -> Result<bool, Error> {
    connection
        .query_row(
            "SELECT EXISTS(SELECT 1 FROM decision_note_deletions
             WHERE profile_id = ?1 AND workspace_id = ?2 AND note_id = ?3)",
            params![binding.profile_id, binding.workspace_id, note_id],
            |row| row.get(0),
        )
        .map_err(store_error)
}

pub(super) fn replay(
    connection: &Connection,
    binding: &WorkspaceBinding,
    key: &WorkspaceReplayKey<'_>,
) -> Result<Option<DecisionNoteReplay>, Error> {
    connection
        .query_row(
            "SELECT log.id, log.revision, log.decision_note_id,
                    CASE WHEN marker.note_id IS NOT NULL AND log.revision < marker.revision
                         THEN NULL ELSE replay.digest END
             FROM idempotency_records AS replay
             JOIN operation_log AS log ON log.id = replay.operation_id
             LEFT JOIN decision_note_deletions AS marker
               ON marker.profile_id = ?1 AND marker.workspace_id = replay.workspace_id
               AND marker.note_id = log.decision_note_id
             WHERE replay.workspace_id = ?2 AND replay.principal_id = ?3
               AND replay.paired_client_id = ?4 AND replay.operation = ?5 AND replay.idempotency_key = ?6
               AND log.decision_note_id IS NOT NULL",
            params![
                binding.profile_id,
                binding.workspace_id,
                key.principal_id,
                key.paired_client_id.unwrap_or(""),
                key.operation,
                key.idempotency_key
            ],
            |row| {
                Ok(DecisionNoteReplay {
                    receipt: DecisionNoteReceipt {
                        operation_id: row.get(0)?,
                        revision: revision(row, 1)?,
                        note_id: row.get(2)?,
                    },
                    digest: row.get(3)?,
                })
            },
        )
        .optional()
        .map_err(store_error)
}

pub(super) fn apply(
    connection: &Connection,
    binding: &WorkspaceBinding,
    change: &DecisionNoteChange<'_>,
) -> Result<(), Error> {
    let workspace_id = &binding.workspace_id;
    let note_id = &change.receipt.note_id;
    let revision =
        i64::try_from(change.receipt.revision).map_err(|_| failure(ErrorCode::INTERNALERROR))?;
    match &change.mutation {
        DecisionNoteMutation::Save(note) => {
            let evidence = serde_json::to_string(&note.evidence_refs)
                .map_err(|_| failure(ErrorCode::INTERNALERROR))?;
            connection.execute(
                "INSERT INTO decision_notes (workspace_id, note_id, decided_on, thesis, evidence_refs, choice)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                params![workspace_id, note_id, note.decided_on, note.thesis, evidence, note.choice],
            ).map_err(store_error)?;
        }
        DecisionNoteMutation::Append(outcome) => {
            connection.execute(
                "INSERT INTO decision_note_outcomes (workspace_id, note_id, outcome_id, observed_on, outcome, revision)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                params![workspace_id, note_id, outcome.outcome_id, outcome.observed_on, outcome.outcome, revision],
            ).map_err(store_error)?;
        }
        DecisionNoteMutation::Delete => {
            let deleted = connection
                .execute(
                    "DELETE FROM decision_notes WHERE workspace_id = ?1 AND note_id = ?2",
                    params![workspace_id, note_id],
                )
                .map_err(store_error)?;
            if deleted != 1 {
                return Err(failure(ErrorCode::DATAUNAVAILABLE));
            }
            connection
                .execute(
                    "INSERT INTO decision_note_deletions
                   (profile_id, workspace_id, note_id, revision, deleted_at_ms, cleanup_pending)
                 VALUES (?1, ?2, ?3, ?4, ?5, 1)",
                    params![
                        binding.profile_id,
                        workspace_id,
                        note_id,
                        revision,
                        change.committed_at_ms
                    ],
                )
                .map_err(store_error)?;
            // Retain the scoped keys and receipts, but remove their body-derived digests.
            connection
                .execute(
                    "UPDATE idempotency_records SET digest = zeroblob(32)
                 WHERE operation_id IN (SELECT id FROM operation_log
                     WHERE workspace_id = ?1 AND decision_note_id = ?2 AND revision < ?3)",
                    params![workspace_id, note_id, revision],
                )
                .map_err(store_error)?;
        }
    }
    Ok(())
}

pub(super) fn clean_deletions(
    connection: &Connection,
    binding: &WorkspaceBinding,
) -> rusqlite::Result<()> {
    let watermark: Option<i64> = connection.query_row(
        "SELECT max(revision) FROM decision_note_deletions
         WHERE profile_id = ?1 AND workspace_id = ?2 AND cleanup_pending = 1",
        params![binding.profile_id, binding.workspace_id],
        |row| row.get(0),
    )?;
    let Some(watermark) = watermark else {
        return Ok(());
    };
    // secure_delete overwrites the current pages, but earlier WAL frames can
    // still contain bodies. Finish this cleanup on replay or startup if needed.
    let busy: bool =
        connection.query_row("PRAGMA wal_checkpoint(TRUNCATE)", [], |row| row.get(0))?;
    if busy {
        return Err(rusqlite::Error::SqliteFailure(
            rusqlite::ffi::Error::new(rusqlite::ffi::SQLITE_BUSY),
            None,
        ));
    }
    // A deletion committed by another open host after the watermark still
    // needs its own checkpoint, even if this one happened to include it.
    connection.execute(
        "UPDATE decision_note_deletions SET cleanup_pending = 0
         WHERE profile_id = ?1 AND workspace_id = ?2 AND cleanup_pending = 1 AND revision <= ?3",
        params![binding.profile_id, binding.workspace_id, watermark],
    )?;
    Ok(())
}
