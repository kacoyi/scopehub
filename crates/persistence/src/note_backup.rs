use std::collections::HashSet;

use rusqlite::{Connection, OptionalExtension, params};
use scopehub_application::{
    DECISION_NOTE_BACKUP_MAX_BYTES, DecisionNoteBackupData, DecisionNoteRestorePlan,
    WorkspaceBinding, failure,
};
use scopehub_domain::protocol::{
    DecisionNoteBackupDeletion, DecisionNoteBackupEntry, DecisionNoteOutcome,
    DecisionNoteRestoreSummary, Error, ErrorCode, Revision,
};

use super::{notes, store_error};

const MAX_BACKUP_NOTES: usize = 256;
const MAX_BACKUP_DELETIONS: usize = 1_024;
const MAX_BACKUP_OUTCOMES_PER_NOTE: usize = 128;

fn account_for_row(
    total_bytes: &mut usize,
    encoded: serde_json::Result<Vec<u8>>,
) -> Result<(), Error> {
    let bytes = encoded.map_err(|_| failure(ErrorCode::INTERNALERROR))?;
    *total_bytes += bytes.len();
    if *total_bytes >= DECISION_NOTE_BACKUP_MAX_BYTES {
        return Err(failure(ErrorCode::RESOURCELIMIT));
    }
    Ok(())
}

pub(super) fn read(
    snapshot: &Connection,
    binding: &WorkspaceBinding,
    revision: Revision,
) -> Result<DecisionNoteBackupData, Error> {
    let mut total_bytes = 0;
    let mut originals = snapshot
        .prepare(
            "SELECT note_id, decided_on, thesis, evidence_refs, choice FROM decision_notes
             WHERE workspace_id = ?1 ORDER BY note_id LIMIT ?2",
        )
        .map_err(store_error)?;
    let mut outcomes = snapshot
        .prepare(
            "SELECT outcome_id, observed_on, outcome FROM decision_note_outcomes
             WHERE workspace_id = ?1 AND note_id = ?2 ORDER BY ordinal LIMIT ?3",
        )
        .map_err(store_error)?;
    let mut entries = Vec::new();
    for original in originals
        .query_map(
            params![binding.workspace_id, MAX_BACKUP_NOTES as i64 + 1],
            notes::decode_original,
        )
        .map_err(store_error)?
    {
        if entries.len() == MAX_BACKUP_NOTES {
            return Err(failure(ErrorCode::RESOURCELIMIT));
        }
        let original = original.map_err(store_error)?;
        account_for_row(&mut total_bytes, serde_json::to_vec(&original))?;
        let mut later = Vec::new();
        for outcome in outcomes
            .query_map(
                params![
                    binding.workspace_id,
                    original.note_id,
                    MAX_BACKUP_OUTCOMES_PER_NOTE as i64 + 1,
                ],
                notes::decode_outcome,
            )
            .map_err(store_error)?
        {
            if later.len() == MAX_BACKUP_OUTCOMES_PER_NOTE {
                return Err(failure(ErrorCode::RESOURCELIMIT));
            }
            let outcome = outcome.map_err(store_error)?;
            account_for_row(&mut total_bytes, serde_json::to_vec(&outcome))?;
            later.push(outcome);
        }
        entries.push(DecisionNoteBackupEntry {
            original,
            outcomes: later,
        });
    }

    let mut markers = snapshot
        .prepare(
            "SELECT note_id, deleted_at_ms FROM decision_note_deletions
             WHERE profile_id = ?1 AND workspace_id = ?2 ORDER BY note_id LIMIT ?3",
        )
        .map_err(store_error)?;
    let mut deletions = Vec::new();
    for marker in markers
        .query_map(
            params![
                binding.profile_id,
                binding.workspace_id,
                MAX_BACKUP_DELETIONS as i64 + 1,
            ],
            |row| {
                Ok(DecisionNoteBackupDeletion {
                    note_id: row.get(0)?,
                    deleted_at_ms: row.get(1)?,
                })
            },
        )
        .map_err(store_error)?
    {
        if deletions.len() == MAX_BACKUP_DELETIONS {
            return Err(failure(ErrorCode::RESOURCELIMIT));
        }
        let marker = marker.map_err(store_error)?;
        account_for_row(&mut total_bytes, serde_json::to_vec(&marker))?;
        deletions.push(marker);
    }
    Ok(DecisionNoteBackupData {
        revision,
        notes: entries,
        deletions,
    })
}

fn present(connection: &Connection, workspace_id: &str, note_id: &str) -> Result<bool, Error> {
    connection
        .query_row(
            "SELECT EXISTS(SELECT 1 FROM decision_notes WHERE workspace_id = ?1 AND note_id = ?2)",
            params![workspace_id, note_id],
            |row| row.get(0),
        )
        .map_err(store_error)
}

fn existing_outcome(
    connection: &Connection,
    workspace_id: &str,
    outcome_id: &str,
) -> Result<Option<(DecisionNoteOutcome, String)>, Error> {
    connection
        .query_row(
            "SELECT outcome_id, observed_on, outcome, note_id FROM decision_note_outcomes
             WHERE workspace_id = ?1 AND outcome_id = ?2",
            params![workspace_id, outcome_id],
            |row| Ok((notes::decode_outcome(row)?, row.get(3)?)),
        )
        .optional()
        .map_err(store_error)
}

pub(super) fn plan(
    snapshot: &Connection,
    binding: &WorkspaceBinding,
    backup: DecisionNoteBackupData,
) -> Result<DecisionNoteRestorePlan, Error> {
    let workspace_id = &binding.workspace_id;
    let backup_markers: HashSet<&str> = backup
        .deletions
        .iter()
        .map(|marker| marker.note_id.as_str())
        .collect();
    let mut summary = DecisionNoteRestoreSummary {
        added_notes: 0,
        added_outcomes: 0,
        removed_notes: 0,
        suppressed_notes: 0,
        imported_deletions: 0,
        independent_copy_warning: false,
    };
    let mut additions = Vec::new();
    for entry in backup.notes {
        let note_id = &entry.original.note_id;
        // Compare immutable content only when neither profile copy marks this ID deleted.
        if backup_markers.contains(note_id.as_str())
            || notes::is_deleted(snapshot, binding, note_id)?
        {
            summary.suppressed_notes += 1;
            continue;
        }
        let new_original = match notes::original(snapshot, workspace_id, note_id)? {
            None => true,
            Some(current) if current == entry.original => false,
            Some(_) => return Err(failure(ErrorCode::STALECONTEXT)),
        };
        let mut later = Vec::new();
        for outcome in entry.outcomes {
            match existing_outcome(snapshot, workspace_id, &outcome.outcome_id)? {
                None => later.push(outcome),
                Some((current, current_note)) if current_note == *note_id && current == outcome => {
                }
                Some(_) => return Err(failure(ErrorCode::STALECONTEXT)),
            }
        }
        if new_original {
            summary.added_notes += 1;
        }
        summary.added_outcomes += later.len() as Revision;
        if new_original || !later.is_empty() {
            additions.push(DecisionNoteBackupEntry {
                original: entry.original,
                outcomes: later,
            });
        }
    }

    let mut deletions = Vec::new();
    for marker in backup.deletions {
        if !notes::is_deleted(snapshot, binding, &marker.note_id)? {
            summary.removed_notes +=
                Revision::from(present(snapshot, workspace_id, &marker.note_id)?);
            deletions.push(marker);
        }
    }
    summary.imported_deletions = deletions.len() as Revision;
    summary.independent_copy_warning = summary.added_notes > 0;
    Ok(DecisionNoteRestorePlan {
        notes: additions,
        deletions,
        summary,
    })
}

pub(super) fn apply(
    transaction: &Connection,
    binding: &WorkspaceBinding,
    revision: Revision,
    plan: &DecisionNoteRestorePlan,
) -> Result<(), Error> {
    let workspace_id = &binding.workspace_id;
    let revision = i64::try_from(revision).map_err(|_| failure(ErrorCode::INTERNALERROR))?;
    for entry in &plan.notes {
        let note_id = &entry.original.note_id;
        if !present(transaction, workspace_id, note_id)? {
            notes::insert_original(transaction, workspace_id, note_id, &entry.original)?;
        }
        for outcome in &entry.outcomes {
            notes::insert_outcome(transaction, workspace_id, note_id, outcome, revision)?;
        }
    }
    for marker in &plan.deletions {
        notes::apply_deletion(
            transaction,
            binding,
            &marker.note_id,
            revision,
            marker.deleted_at_ms,
        )?;
    }
    Ok(())
}
