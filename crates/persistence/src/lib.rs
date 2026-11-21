//! Profile-bound SQLite workspace storage for the Rust application registry.

mod folder_manifest;
mod local_dataset;
mod note_backup;
mod notes;
mod private_records;
mod reviewed_import;
mod storage;

use std::{fmt, path::Path, time::Duration};

use rusqlite::{
    Connection, OpenFlags, OptionalExtension, Row, TransactionBehavior, config::DbConfig, params,
};
use scopehub_application::{
    Actor, ChartEvidenceRef, ChartEvidenceStatus, DecisionNoteBackupData, DecisionNoteChange,
    DecisionNoteMutation, DecisionNoteReplay, DecisionNoteRestorePlan, PrivateRecordPlan,
    SET_TIMEFRAME, StoredLocalDataset, Timeframe, UNDO, WorkspaceBinding, WorkspaceChange,
    WorkspaceReceipt, WorkspaceReplay, WorkspaceReplayKey, WorkspaceState, WorkspaceStore,
    WorkspaceTransaction, WorkspaceUndoAccess, WorkspaceUndoTarget, failure,
};
use scopehub_domain::protocol::{
    DecisionNoteDate, DecisionNoteReadOutput, Error, ErrorCode, PrivateRecordArchiveEntry,
    PrivateRecordEntry, PrivateRecordSummary, Revision, VisibleRange,
};

const DATABASE_NAME: &str = "workspace.sqlite3";
const APPLICATION_ID: i64 = 0x5348_5542;
const INITIAL_SCHEMA_VERSION: i64 = 1;
const DECISION_NOTES_SCHEMA_VERSION: i64 = 2;
const DECISION_NOTE_RESTORE_SCHEMA_VERSION: i64 = 3;
const REVIEWED_IMPORT_SCHEMA_VERSION: i64 = 4;
const LOCAL_DATASET_SCHEMA_VERSION: i64 = 5;
const CHART_EVIDENCE_SCHEMA_VERSION: i64 = 6;
const FOLDER_MANIFEST_SCHEMA_VERSION: i64 = 7;
const PRIVATE_RECORD_SCHEMA_VERSION: i64 = 8;
const SCHEMA_VERSION: i64 = PRIVATE_RECORD_SCHEMA_VERSION;

#[derive(Debug, PartialEq, Eq)]
pub enum OpenWorkspaceError {
    InvalidBinding,
    UnsupportedFormat,
    ProfileMismatch,
    Busy,
    Database,
}

impl fmt::Display for OpenWorkspaceError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(match self {
            Self::InvalidBinding => {
                "Workspace storage requires an absolute profile directory and valid identities."
            }
            Self::UnsupportedFormat => "Workspace storage has an unsupported format.",
            Self::ProfileMismatch => {
                "Workspace storage belongs to a different profile or workspace."
            }
            Self::Busy => "Workspace storage is busy.",
            Self::Database => "Workspace storage could not be opened.",
        })
    }
}

impl std::error::Error for OpenWorkspaceError {}

impl From<rusqlite::Error> for OpenWorkspaceError {
    fn from(error: rusqlite::Error) -> Self {
        if is_busy(&error) {
            Self::Busy
        } else {
            Self::Database
        }
    }
}

/// An owned connection fixed to one profile and workspace until it is dropped.
pub struct SqliteWorkspace {
    connection: Connection,
    binding: WorkspaceBinding,
}

impl SqliteWorkspace {
    /// Provision a directory for a host-owned profile. Existing directories
    /// must already be private. New storage files must use private creation.
    pub fn prepare_profile_root(profile_root: &Path) -> Result<(), OpenWorkspaceError> {
        storage::prepare_profile_root(profile_root).map_err(|_| OpenWorkspaceError::Database)
    }

    /// Open the database in the pre-provisioned profile directory. Both
    /// arguments come from the host at composition time.
    pub fn open(
        profile_root: &Path,
        binding: WorkspaceBinding,
    ) -> Result<Self, OpenWorkspaceError> {
        if !profile_root.is_absolute()
            || [&binding.profile_id, &binding.workspace_id]
                .into_iter()
                .any(|id| id.is_empty() || id.chars().count() > 256 || id.contains('\0'))
        {
            return Err(OpenWorkspaceError::InvalidBinding);
        }
        let (path, exists) =
            storage::open_path(profile_root).map_err(|_| OpenWorkspaceError::Database)?;
        if exists {
            // A read-only preflight cannot recover a rollback journal into an
            // unsupported database or checkpoint an unsupported WAL.
            let mut reader = Connection::open_with_flags(
                &path,
                OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NOFOLLOW,
            )?;
            reader.set_db_config(DbConfig::SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, true)?;
            reader.busy_timeout(Duration::from_secs(3))?;
            check_format(&mut reader, &binding)?;
        }
        let mut connection = Connection::open_with_flags(
            &path,
            OpenFlags::SQLITE_OPEN_READ_WRITE
                | OpenFlags::SQLITE_OPEN_CREATE
                | OpenFlags::SQLITE_OPEN_NOFOLLOW,
        )?;
        // Closing an unqualified connection must not checkpoint another format's WAL.
        connection.set_db_config(DbConfig::SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, true)?;
        connection.busy_timeout(Duration::from_secs(3))?;
        connection.pragma_update(None, "foreign_keys", true)?;
        let foreign_keys: bool =
            connection.pragma_query_value(None, "foreign_keys", |row| row.get(0))?;
        if !foreign_keys {
            return Err(OpenWorkspaceError::Database);
        }

        // Recheck under SQLite's own locking before any persistent SQL.
        let version = check_format(&mut connection, &binding)?;
        let mode: String = connection.pragma_query_value(None, "journal_mode", |row| row.get(0))?;
        if mode != "wal" {
            let updated: String =
                connection
                    .pragma_update_and_check(None, "journal_mode", "WAL", |row| row.get(0))?;
            if updated != "wal" {
                return Err(OpenWorkspaceError::Database);
            }
        }
        connection.pragma_update(None, "synchronous", "FULL")?;
        connection.pragma_update(None, "temp_store", "MEMORY")?;
        // A bounded note mutation can keep its uncommitted pages in memory.
        connection.pragma_update(None, "cache_spill", false)?;
        let secure_delete: bool =
            connection.pragma_update_and_check(None, "secure_delete", true, |row| row.get(0))?;
        if !secure_delete {
            return Err(OpenWorkspaceError::Database);
        }
        if version < SCHEMA_VERSION {
            let transaction =
                connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
            // Another host may have migrated the database while we waited.
            migrate(&transaction, &binding)?;
            transaction.commit()?;
        }
        notes::clean_deletions(&connection, &binding)?;
        private_records::clean(&connection, &binding.workspace_id)?;
        connection.set_db_config(DbConfig::SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, false)?;
        Ok(Self {
            connection,
            binding,
        })
    }
}

fn check_format(
    connection: &mut Connection,
    binding: &WorkspaceBinding,
) -> Result<i64, OpenWorkspaceError> {
    let snapshot = connection.transaction()?;
    let version = schema_version(&snapshot, binding)?;
    snapshot.commit()?;
    Ok(version)
}

fn schema_version(
    connection: &Connection,
    binding: &WorkspaceBinding,
) -> Result<i64, OpenWorkspaceError> {
    let application_id: i64 =
        connection.pragma_query_value(None, "application_id", |row| row.get(0))?;
    let version: i64 = connection.pragma_query_value(None, "user_version", |row| row.get(0))?;
    match (application_id, version) {
        (0, 0) => {
            let empty: bool = connection.query_row(
                "SELECT NOT EXISTS(SELECT 1 FROM sqlite_schema)",
                [],
                |row| row.get(0),
            )?;
            if empty {
                Ok(0)
            } else {
                Err(OpenWorkspaceError::UnsupportedFormat)
            }
        }
        (APPLICATION_ID, INITIAL_SCHEMA_VERSION..=SCHEMA_VERSION) => {
            let ledger_matches: bool = connection.query_row(
                "SELECT count(*) = ?1 AND count(DISTINCT version) = ?1
                    AND coalesce(min(version) = ?2 AND max(version) = ?1, 0)
                 FROM schema_migrations",
                params![version, INITIAL_SCHEMA_VERSION],
                |row| row.get(0),
            )?;
            if !ledger_matches {
                return Err(OpenWorkspaceError::UnsupportedFormat);
            }
            let actual_profile: String = connection.query_row(
                "SELECT profile_id FROM profile_binding WHERE singleton = 1",
                [],
                |row| row.get(0),
            )?;
            let workspace_matches: bool = connection.query_row(
                "SELECT count(*) = 1 AND min(id) = ?1 FROM workspaces",
                [&binding.workspace_id],
                |row| row.get(0),
            )?;
            if actual_profile != binding.profile_id || !workspace_matches {
                return Err(OpenWorkspaceError::ProfileMismatch);
            }
            Ok(version)
        }
        _ => Err(OpenWorkspaceError::UnsupportedFormat),
    }
}

fn migrate(connection: &Connection, binding: &WorkspaceBinding) -> Result<(), OpenWorkspaceError> {
    let version = schema_version(connection, binding)?;
    if version == 0 {
        migrate_initial(connection, binding)?;
    }
    if version < DECISION_NOTES_SCHEMA_VERSION {
        migrate_decision_notes(connection)?;
    }
    if version < DECISION_NOTE_RESTORE_SCHEMA_VERSION {
        connection.execute_batch(include_str!("decision_note_restore.sql"))?;
        record_migration(connection, DECISION_NOTE_RESTORE_SCHEMA_VERSION)?;
    }
    if version < REVIEWED_IMPORT_SCHEMA_VERSION {
        connection.execute_batch(include_str!("reviewed_import.sql"))?;
        record_migration(connection, REVIEWED_IMPORT_SCHEMA_VERSION)?;
    }
    if version < LOCAL_DATASET_SCHEMA_VERSION {
        connection.execute_batch(include_str!("local_dataset.sql"))?;
        record_migration(connection, LOCAL_DATASET_SCHEMA_VERSION)?;
    }
    if version < CHART_EVIDENCE_SCHEMA_VERSION {
        connection.execute_batch(include_str!("chart_evidence.sql"))?;
        record_migration(connection, CHART_EVIDENCE_SCHEMA_VERSION)?;
    }
    if version < FOLDER_MANIFEST_SCHEMA_VERSION {
        connection.execute_batch(include_str!("folder_manifest.sql"))?;
        record_migration(connection, FOLDER_MANIFEST_SCHEMA_VERSION)?;
    }
    if version < PRIVATE_RECORD_SCHEMA_VERSION {
        connection.execute_batch(include_str!("private_records.sql"))?;
        record_migration(connection, PRIVATE_RECORD_SCHEMA_VERSION)?;
    }
    Ok(())
}

fn migrate_initial(connection: &Connection, binding: &WorkspaceBinding) -> rusqlite::Result<()> {
    connection.execute_batch(include_str!("initial.sql"))?;
    connection.execute(
        "INSERT INTO profile_binding(singleton, profile_id) VALUES (1, ?1)",
        [&binding.profile_id],
    )?;
    connection.execute(
        "INSERT INTO workspaces(id, revision, selection_revision, timeframe) VALUES (?1, 0, 0, ?2)",
        params![binding.workspace_id, Timeframe::Minute.as_str()],
    )?;
    connection.pragma_update(None, "application_id", APPLICATION_ID)?;
    record_migration(connection, INITIAL_SCHEMA_VERSION)
}

fn migrate_decision_notes(connection: &Connection) -> rusqlite::Result<()> {
    connection.execute_batch(include_str!("decision_notes.sql"))?;
    record_migration(connection, DECISION_NOTES_SCHEMA_VERSION)
}

fn record_migration(connection: &Connection, version: i64) -> rusqlite::Result<()> {
    connection.execute(
        "INSERT INTO schema_migrations(version) VALUES (?1)",
        [version],
    )?;
    connection.pragma_update(None, "user_version", version)
}

fn is_busy(error: &rusqlite::Error) -> bool {
    matches!(
        error.sqlite_error_code(),
        Some(rusqlite::ErrorCode::DatabaseBusy | rusqlite::ErrorCode::DatabaseLocked)
    )
}

fn store_error(error: rusqlite::Error) -> Error {
    failure(if is_busy(&error) {
        ErrorCode::BUSY
    } else {
        ErrorCode::INTERNALERROR
    })
}

fn timeframe(row: &Row<'_>, index: usize) -> rusqlite::Result<Timeframe> {
    let value: String = row.get(index)?;
    Timeframe::parse(&value).ok_or(rusqlite::Error::InvalidQuery)
}

fn revision(row: &Row<'_>, index: usize) -> rusqlite::Result<Revision> {
    Revision::try_from(row.get::<_, i64>(index)?).map_err(|_| rusqlite::Error::InvalidQuery)
}

fn state(connection: &Connection, workspace_id: &str) -> Result<WorkspaceState, Error> {
    connection
        .query_row(
            "SELECT revision, selection_revision, timeframe FROM workspaces WHERE id = ?1",
            [workspace_id],
            |row| {
                Ok(WorkspaceState {
                    revision: revision(row, 0)?,
                    selection_revision: revision(row, 1)?,
                    timeframe: timeframe(row, 2)?,
                })
            },
        )
        .map_err(store_error)
}

fn chart_evidence(
    connection: &Connection,
    workspace_id: &str,
) -> Result<Option<(ChartEvidenceRef, bool)>, Error> {
    connection
        .query_row(
            "SELECT evidence.revision_id, from_ms, to_ms, revoked.revision_id IS NOT NULL
             FROM workspace_chart_evidence AS evidence
             LEFT JOIN local_dataset_display_revocations AS revoked USING (revision_id)
             WHERE workspace_id = ?1",
            [workspace_id],
            |row| {
                Ok((
                    ChartEvidenceRef {
                        revision_id: row.get(0)?,
                        range: VisibleRange {
                            from_ms: row.get(1)?,
                            to_ms: row.get(2)?,
                        },
                    },
                    row.get(3)?,
                ))
            },
        )
        .optional()
        .map_err(store_error)
}

fn receipt(row: &Row<'_>) -> rusqlite::Result<WorkspaceReceipt> {
    Ok(WorkspaceReceipt {
        operation_id: row.get(0)?,
        revision: revision(row, 1)?,
        undoable: row.get(2)?,
        before: timeframe(row, 3)?,
        after: timeframe(row, 4)?,
    })
}

fn eligible_undo(
    connection: &Connection,
    workspace_id: &str,
    state: WorkspaceState,
    access: WorkspaceUndoAccess<'_>,
) -> Result<Option<WorkspaceUndoTarget>, Error> {
    let committed_workspace_revision = chart_revision(connection, workspace_id)?;
    let revision = i64::try_from(committed_workspace_revision)
        .map_err(|_| failure(ErrorCode::INTERNALERROR))?;
    connection.query_row(
        "SELECT candidate.id FROM operation_log AS candidate
         WHERE candidate.workspace_id = ?1 AND candidate.revision = ?2
           AND candidate.after_timeframe = ?3 AND candidate.operation = ?4 AND candidate.undoable = 1
           AND candidate.actor = ?5 AND candidate.principal_id = ?6 AND candidate.paired_client_id = ?7
           AND NOT EXISTS (SELECT 1 FROM operation_log AS inverse WHERE inverse.undoes = candidate.id)",
        params![workspace_id, revision, state.timeframe.as_str(), SET_TIMEFRAME, access.actor.as_str(),
                access.principal_id, access.paired_client_id.unwrap_or("")],
        |row| Ok(WorkspaceUndoTarget {
            operation_id: row.get(0)?,
            committed_workspace_revision,
        }),
    ).optional().map_err(store_error)
}

fn chart_revision(connection: &Connection, workspace_id: &str) -> Result<Revision, Error> {
    connection
        .query_row(
            "SELECT coalesce(max(revision), 0) FROM operation_log
             WHERE workspace_id = ?1 AND operation IN (?2, ?3)",
            params![workspace_id, SET_TIMEFRAME, UNDO],
            |row| revision(row, 0),
        )
        .map_err(store_error)
}

impl WorkspaceStore for SqliteWorkspace {
    fn binding(&self) -> &WorkspaceBinding {
        &self.binding
    }

    fn snapshot(
        &self,
        undo: Option<WorkspaceUndoAccess<'_>>,
        disclose_chart_evidence: bool,
    ) -> Result<
        (
            WorkspaceState,
            Option<WorkspaceUndoTarget>,
            Option<ChartEvidenceStatus>,
        ),
        Error,
    > {
        let snapshot = self
            .connection
            .unchecked_transaction()
            .map_err(store_error)?;
        let workspace_id = &self.binding.workspace_id;
        let state = state(&snapshot, workspace_id)?;
        let target = undo
            .map(|undo| eligible_undo(&snapshot, workspace_id, state, undo))
            .transpose()?
            .flatten();
        let evidence = if disclose_chart_evidence {
            chart_evidence(&snapshot, workspace_id)?.map(|(target, revoked)| {
                if revoked {
                    ChartEvidenceStatus::Unavailable
                } else {
                    ChartEvidenceStatus::Available(target)
                }
            })
        } else {
            None
        };
        snapshot.commit().map_err(store_error)?;
        Ok((state, target, evidence))
    }

    fn inspect<T>(
        &self,
        read: impl FnOnce(&dyn WorkspaceTransaction) -> Result<T, Error>,
    ) -> Result<T, Error> {
        let snapshot = self
            .connection
            .unchecked_transaction()
            .map_err(store_error)?;
        let value = read(&SqliteTransaction {
            connection: &snapshot,
            binding: &self.binding,
        })?;
        snapshot.commit().map_err(store_error)?;
        Ok(value)
    }

    fn transact<T>(
        &mut self,
        work: impl FnOnce(&mut dyn WorkspaceTransaction) -> Result<T, Error>,
    ) -> Result<T, Error> {
        let transaction = self
            .connection
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(store_error)?;
        let value = work(&mut SqliteTransaction {
            connection: &transaction,
            binding: &self.binding,
        })?;
        transaction.commit().map_err(store_error)?;
        Ok(value)
    }

    fn read_notes(
        &self,
        note_id: Option<&str>,
        offset: u64,
    ) -> Result<DecisionNoteReadOutput, Error> {
        let snapshot = self
            .connection
            .unchecked_transaction()
            .map_err(store_error)?;
        let output = notes::read(&snapshot, &self.binding.workspace_id, note_id, offset)?;
        snapshot.commit().map_err(store_error)?;
        Ok(output)
    }

    fn decision_note_backup(&self) -> Result<DecisionNoteBackupData, Error> {
        let snapshot = self
            .connection
            .unchecked_transaction()
            .map_err(store_error)?;
        let revision = state(&snapshot, &self.binding.workspace_id)?.revision;
        let data = note_backup::read(&snapshot, &self.binding, revision)?;
        snapshot.commit().map_err(store_error)?;
        Ok(data)
    }

    fn clean_deleted_note(&mut self) -> Result<(), Error> {
        notes::clean_deletions(&self.connection, &self.binding).map_err(store_error)
    }

    fn clean_private_record_pages(&mut self) -> Result<(), Error> {
        private_records::clean(&self.connection, &self.binding.workspace_id).map_err(store_error)
    }
}

fn checkpoint_private_pages(connection: &Connection) -> rusqlite::Result<()> {
    let busy: bool =
        connection.query_row("PRAGMA wal_checkpoint(TRUNCATE)", [], |row| row.get(0))?;
    if busy {
        return Err(rusqlite::Error::SqliteFailure(
            rusqlite::ffi::Error::new(rusqlite::ffi::SQLITE_BUSY),
            None,
        ));
    }
    Ok(())
}

struct SqliteTransaction<'a> {
    connection: &'a Connection,
    binding: &'a WorkspaceBinding,
}

impl WorkspaceTransaction for SqliteTransaction<'_> {
    fn state(&self) -> Result<WorkspaceState, Error> {
        state(self.connection, &self.binding.workspace_id)
    }

    fn private_record(&self, id: &str) -> Result<Option<PrivateRecordEntry>, Error> {
        private_records::read(self.connection, &self.binding.workspace_id, id)
    }

    fn private_record_index(&self) -> Result<Vec<PrivateRecordSummary>, Error> {
        private_records::index(self.connection, &self.binding.workspace_id)
    }

    fn private_record_deletions(&self) -> Result<Vec<String>, Error> {
        private_records::deletions(self.connection, &self.binding.workspace_id)
    }

    fn private_record_archive_entries(&self) -> Result<Vec<PrivateRecordArchiveEntry>, Error> {
        private_records::archive(self.connection, &self.binding.workspace_id)
    }

    fn private_record_receipt_target(&self, operation_id: &str) -> Result<Option<String>, Error> {
        private_records::receipt_target(self.connection, operation_id)
    }

    fn record_private_records(
        &mut self,
        change: &WorkspaceChange<'_>,
        plan: &PrivateRecordPlan,
    ) -> Result<(), Error> {
        self.record_change(change, None)?;
        private_records::apply(self.connection, &self.binding.workspace_id, change, plan)
    }

    fn chart_evidence(&self) -> Result<Option<ChartEvidenceRef>, Error> {
        Ok(chart_evidence(self.connection, &self.binding.workspace_id)?.map(|(target, _)| target))
    }

    fn set_chart_evidence(
        &mut self,
        selection_revision: Revision,
        target: Option<&ChartEvidenceRef>,
    ) -> Result<(), Error> {
        let workspace_id = &self.binding.workspace_id;
        match target {
            Some(target) => self.connection.execute(
                "INSERT INTO workspace_chart_evidence (workspace_id, revision_id, from_ms, to_ms)
                 VALUES (?1, ?2, ?3, ?4)
                 ON CONFLICT(workspace_id) DO UPDATE SET revision_id = excluded.revision_id,
                     from_ms = excluded.from_ms, to_ms = excluded.to_ms",
                params![
                    workspace_id,
                    target.revision_id,
                    target.range.from_ms,
                    target.range.to_ms
                ],
            ),
            None => self.connection.execute(
                "DELETE FROM workspace_chart_evidence WHERE workspace_id = ?1",
                [workspace_id],
            ),
        }
        .map_err(store_error)?;
        self.connection
            .execute(
                "UPDATE workspaces SET selection_revision = ?2 WHERE id = ?1",
                params![
                    workspace_id,
                    i64::try_from(selection_revision)
                        .map_err(|_| failure(ErrorCode::INTERNALERROR))?
                ],
            )
            .map_err(store_error)?;
        Ok(())
    }

    fn local_evidence_dataset(
        &self,
        revision_id: &str,
    ) -> Result<Option<StoredLocalDataset>, Error> {
        match local_dataset::read_local_dataset(self.connection, revision_id) {
            Err(error) if matches!(error.code, ErrorCode::PERMISSIONDENIED) => Ok(None),
            result => result,
        }
    }

    fn local_evidence_display_allowed(&self, revision_id: &str) -> Result<bool, Error> {
        local_dataset::display_allowed(self.connection, revision_id)
    }

    fn chart_revision(&self) -> Result<Revision, Error> {
        chart_revision(self.connection, &self.binding.workspace_id)
    }

    fn replay(&self, key: &WorkspaceReplayKey<'_>) -> Result<Option<WorkspaceReplay>, Error> {
        self.connection
            .query_row(
                "SELECT log.id, log.revision, log.undoable, log.before_timeframe,
                        log.after_timeframe, replay.digest
                 FROM idempotency_records AS replay
                 JOIN operation_log AS log ON log.id = replay.operation_id
                 WHERE replay.workspace_id = ?1 AND replay.principal_id = ?2
                   AND replay.paired_client_id = ?3 AND replay.operation = ?4
                   AND replay.idempotency_key = ?5",
                params![
                    self.binding.workspace_id,
                    key.principal_id,
                    key.paired_client_id.unwrap_or(""),
                    key.operation,
                    key.idempotency_key
                ],
                |row| {
                    Ok(WorkspaceReplay {
                        receipt: receipt(row)?,
                        digest: row.get(5)?,
                    })
                },
            )
            .optional()
            .map_err(store_error)
    }

    fn undo_candidate(
        &self,
        operation_id: &str,
        principal_id: &str,
        paired_client_id: Option<&str>,
    ) -> Result<Option<WorkspaceReceipt>, Error> {
        self.connection
            .query_row(
                "SELECT id, revision, undoable, before_timeframe, after_timeframe
                 FROM operation_log
                 WHERE id = ?1 AND workspace_id = ?2 AND principal_id = ?3
                   AND paired_client_id = ?4 AND operation = ?5 AND undoable = 1",
                params![
                    operation_id,
                    self.binding.workspace_id,
                    principal_id,
                    paired_client_id.unwrap_or(""),
                    SET_TIMEFRAME
                ],
                receipt,
            )
            .optional()
            .map_err(store_error)
    }

    fn record(&mut self, change: &WorkspaceChange<'_>) -> Result<(), Error> {
        self.record_change(change, None)
    }

    fn note_decision_date(&self, note_id: &str) -> Result<Option<DecisionNoteDate>, Error> {
        self.connection
            .query_row(
                "SELECT decided_on FROM decision_notes WHERE workspace_id = ?1 AND note_id = ?2",
                params![self.binding.workspace_id, note_id],
                |row| row.get(0),
            )
            .optional()
            .map_err(store_error)
    }

    fn note_is_deleted(&self, note_id: &str) -> Result<bool, Error> {
        notes::is_deleted(self.connection, self.binding, note_id)
    }

    fn note_replay(
        &self,
        key: &WorkspaceReplayKey<'_>,
    ) -> Result<Option<DecisionNoteReplay>, Error> {
        notes::replay(self.connection, self.binding, key)
    }

    fn record_note(&mut self, change: &DecisionNoteChange<'_>) -> Result<(), Error> {
        self.check_note_capacity(&change.receipt.note_id, &change.mutation)?;
        let timeframe = self.state()?.timeframe;
        let receipt = &change.receipt;
        let key = &change.key;
        self.record_change(
            &WorkspaceChange {
                key: WorkspaceReplayKey {
                    principal_id: key.principal_id,
                    paired_client_id: key.paired_client_id,
                    operation: key.operation,
                    idempotency_key: key.idempotency_key,
                },
                digest: change.digest,
                receipt: WorkspaceReceipt {
                    operation_id: receipt.operation_id.clone(),
                    revision: receipt.revision,
                    undoable: false,
                    before: timeframe,
                    after: timeframe,
                },
                actor: Actor::User,
                request_id: change.request_id,
                committed_at_ms: change.committed_at_ms,
                undoes: None,
            },
            Some(&receipt.note_id),
        )?;
        notes::apply(self.connection, self.binding, change)
    }

    fn check_note_capacity(
        &self,
        note_id: &str,
        mutation: &DecisionNoteMutation,
    ) -> Result<(), Error> {
        note_backup::check_note_capacity(self.connection, self.binding, note_id, mutation)
    }

    fn plan_note_restore(
        &self,
        backup: DecisionNoteBackupData,
    ) -> Result<DecisionNoteRestorePlan, Error> {
        note_backup::plan(self.connection, self.binding, backup)
    }

    fn record_note_restore(
        &mut self,
        change: &WorkspaceChange<'_>,
        plan: &DecisionNoteRestorePlan,
    ) -> Result<(), Error> {
        self.record_change(change, None)?;
        note_backup::apply(self.connection, self.binding, change.receipt.revision, plan)
    }
}

impl SqliteTransaction<'_> {
    fn record_change(
        &self,
        change: &WorkspaceChange<'_>,
        note_id: Option<&str>,
    ) -> Result<(), Error> {
        let receipt = &change.receipt;
        let revision =
            i64::try_from(receipt.revision).map_err(|_| failure(ErrorCode::INTERNALERROR))?;
        let changed = self
            .connection
            .execute(
                "UPDATE workspaces SET revision = ?1, timeframe = ?2
             WHERE id = ?3 AND revision = ?1 - 1 AND timeframe = ?4",
                params![
                    revision,
                    receipt.after.as_str(),
                    self.binding.workspace_id,
                    receipt.before.as_str()
                ],
            )
            .map_err(store_error)?;
        if changed != 1 {
            return Err(failure(ErrorCode::STALECONTEXT));
        }
        let key = &change.key;
        let client = key.paired_client_id.unwrap_or("");
        self.connection
            .execute(
                "INSERT INTO operation_log (
                id, workspace_id, operation, actor, principal_id, paired_client_id, request_id,
                committed_at_ms, revision, undoable, before_timeframe, after_timeframe, undoes, decision_note_id
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14)",
                params![
                    receipt.operation_id,
                    self.binding.workspace_id,
                    key.operation,
                    change.actor.as_str(),
                    key.principal_id,
                    client,
                    change.request_id,
                    change.committed_at_ms,
                    revision,
                    receipt.undoable,
                    receipt.before.as_str(),
                    receipt.after.as_str(),
                    change.undoes,
                    note_id,
                ],
            )
            .map_err(store_error)?;
        self.connection.execute(
            "INSERT INTO idempotency_records (
                workspace_id, principal_id, paired_client_id, operation, idempotency_key, digest, operation_id
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            params![self.binding.workspace_id, key.principal_id, client, key.operation,
                    key.idempotency_key, change.digest, receipt.operation_id],
        ).map_err(store_error)?;
        Ok(())
    }
}

#[cfg(test)]
mod tests;
