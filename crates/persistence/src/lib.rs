//! Profile-bound SQLite workspace storage for the Rust application registry.

mod storage;

use std::{fmt, path::Path, time::Duration};

use rusqlite::{
    Connection, OpenFlags, OptionalExtension, Row, TransactionBehavior, config::DbConfig, params,
};
use scopehub_application::{
    SET_TIMEFRAME, Timeframe, WorkspaceBinding, WorkspaceChange, WorkspaceReceipt, WorkspaceReplay,
    WorkspaceReplayKey, WorkspaceState, WorkspaceStore, WorkspaceTransaction, WorkspaceUndoAccess,
    WorkspaceUndoTarget, failure,
};
use scopehub_domain::protocol::{Error, ErrorCode, Revision};

const DATABASE_NAME: &str = "workspace.sqlite3";
const APPLICATION_ID: i64 = 0x5348_5542;
const SCHEMA_VERSION: i64 = 1;

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
        let initial = check_format(&mut connection, &binding)?;
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
        if initial {
            let transaction =
                connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
            // Another host may have initialized the profile while we waited.
            if requires_initial_migration(&transaction, &binding)? {
                migrate_initial(&transaction, &binding)?;
            }
            transaction.commit()?;
        }
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
) -> Result<bool, OpenWorkspaceError> {
    let snapshot = connection.transaction()?;
    let initial = requires_initial_migration(&snapshot, binding)?;
    snapshot.commit()?;
    Ok(initial)
}

fn requires_initial_migration(
    connection: &Connection,
    binding: &WorkspaceBinding,
) -> Result<bool, OpenWorkspaceError> {
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
                Ok(true)
            } else {
                Err(OpenWorkspaceError::UnsupportedFormat)
            }
        }
        (APPLICATION_ID, SCHEMA_VERSION) => {
            let (count, version): (i64, Option<i64>) = connection.query_row(
                "SELECT count(*), max(version) FROM schema_migrations",
                [],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )?;
            if (count, version) != (1, Some(SCHEMA_VERSION)) {
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
            Ok(false)
        }
        _ => Err(OpenWorkspaceError::UnsupportedFormat),
    }
}

fn migrate_initial(connection: &Connection, binding: &WorkspaceBinding) -> rusqlite::Result<()> {
    connection.execute_batch(include_str!("initial.sql"))?;
    connection.execute(
        "INSERT INTO schema_migrations(version) VALUES (?1)",
        [SCHEMA_VERSION],
    )?;
    connection.execute(
        "INSERT INTO profile_binding(singleton, profile_id) VALUES (1, ?1)",
        [&binding.profile_id],
    )?;
    connection.execute(
        "INSERT INTO workspaces(id, revision, selection_revision, timeframe) VALUES (?1, 0, 0, ?2)",
        params![binding.workspace_id, Timeframe::Minute.as_str()],
    )?;
    connection.pragma_update(None, "application_id", APPLICATION_ID)?;
    connection.pragma_update(None, "user_version", SCHEMA_VERSION)
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
    let revision = i64::try_from(state.revision).map_err(|_| failure(ErrorCode::INTERNALERROR))?;
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
            committed_workspace_revision: state.revision,
        }),
    ).optional().map_err(store_error)
}

impl WorkspaceStore for SqliteWorkspace {
    fn binding(&self) -> &WorkspaceBinding {
        &self.binding
    }

    fn snapshot(
        &self,
        undo: Option<WorkspaceUndoAccess<'_>>,
    ) -> Result<(WorkspaceState, Option<WorkspaceUndoTarget>), Error> {
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
        snapshot.commit().map_err(store_error)?;
        Ok((state, target))
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
            workspace_id: &self.binding.workspace_id,
        })?;
        transaction.commit().map_err(store_error)?;
        Ok(value)
    }
}

struct SqliteTransaction<'a> {
    connection: &'a Connection,
    workspace_id: &'a str,
}

impl WorkspaceTransaction for SqliteTransaction<'_> {
    fn state(&self) -> Result<WorkspaceState, Error> {
        state(self.connection, self.workspace_id)
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
                    self.workspace_id,
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
                    self.workspace_id,
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
                    self.workspace_id,
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
                committed_at_ms, revision, undoable, before_timeframe, after_timeframe, undoes
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13)",
                params![
                    receipt.operation_id,
                    self.workspace_id,
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
                ],
            )
            .map_err(store_error)?;
        self.connection.execute(
            "INSERT INTO idempotency_records (
                workspace_id, principal_id, paired_client_id, operation, idempotency_key, digest, operation_id
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            params![self.workspace_id, key.principal_id, client, key.operation,
                    key.idempotency_key, change.digest, receipt.operation_id],
        ).map_err(store_error)?;
        Ok(())
    }
}

#[cfg(test)]
mod tests;
