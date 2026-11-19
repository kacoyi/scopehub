use std::{fs, path::PathBuf};

use uuid::Uuid;

use super::*;

struct Profile(PathBuf);

impl Profile {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!("scopehub-migration-{}", Uuid::new_v4()));
        fs::create_dir(&path).unwrap();
        Self(path)
    }

    fn db(&self) -> PathBuf {
        self.0.join(DATABASE_NAME)
    }

    fn binding() -> WorkspaceBinding {
        WorkspaceBinding {
            profile_id: "migration-profile".into(),
            workspace_id: "workspace".into(),
        }
    }

    fn open(&self) -> Result<SqliteWorkspace, OpenWorkspaceError> {
        SqliteWorkspace::open(&self.0, Self::binding())
    }
}

impl Drop for Profile {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.0).unwrap();
    }
}

fn integer_pragma(conn: &Connection, name: &str) -> i64 {
    conn.pragma_query_value(None, name, |row| row.get(0))
        .unwrap()
}

#[test]
fn initial_ddl_and_version_are_atomic_and_the_reopened_writer_enforces_wal_and_foreign_keys() {
    let profile = Profile::new();
    let mut connection = Connection::open(profile.db()).unwrap();
    connection
        .pragma_update_and_check(None, "journal_mode", "WAL", |row| row.get::<_, String>(0))
        .unwrap();
    {
        let transaction = connection
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .unwrap();
        migrate_initial(&transaction, &Profile::binding()).unwrap();
        assert_eq!(integer_pragma(&transaction, "user_version"), SCHEMA_VERSION);
        assert_eq!(
            integer_pragma(&transaction, "application_id"),
            APPLICATION_ID
        );
        // Drop the staged initial migration before commit.
    }
    assert_eq!(integer_pragma(&connection, "user_version"), 0);
    assert_eq!(integer_pragma(&connection, "application_id"), 0);
    let count: i64 = connection
        .query_row("SELECT count(*) FROM sqlite_schema", [], |row| row.get(0))
        .unwrap();
    assert_eq!(count, 0);
    drop(connection);

    drop(profile.open().unwrap());
    let SqliteWorkspace { connection, .. } = profile.open().unwrap();
    assert_eq!(integer_pragma(&connection, "user_version"), SCHEMA_VERSION);
    assert_eq!(
        integer_pragma(&connection, "application_id"),
        APPLICATION_ID
    );
    assert_eq!(integer_pragma(&connection, "foreign_keys"), 1);
    assert_eq!(integer_pragma(&connection, "synchronous"), 2);
    let mode: String = connection
        .pragma_query_value(None, "journal_mode", |row| row.get(0))
        .unwrap();
    assert_eq!(mode, "wal");
    let state = state(&connection, "workspace").unwrap();
    assert_eq!(
        (state.revision, state.selection_revision, state.timeframe),
        (0, 0, Timeframe::Minute)
    );
    let invalid_reference = connection.execute(
        "INSERT INTO idempotency_records VALUES ('workspace', 'p', '', 'op', 'key', zeroblob(32), 'missing')",
        [],
    ).unwrap_err();
    assert_eq!(
        invalid_reference.sqlite_extended_error_code(),
        Some(rusqlite::ffi::SQLITE_CONSTRAINT_FOREIGNKEY)
    );
}

#[test]
fn unknown_formats_are_rejected_without_changing_database_bytes_or_journal_mode() {
    for (application_id, version, journal_mode) in [
        (APPLICATION_ID, SCHEMA_VERSION + 1, "delete"),
        (APPLICATION_ID, SCHEMA_VERSION + 1, "wal"),
        (0, 0, "delete"),
    ] {
        let profile = Profile::new();
        let connection = Connection::open(profile.db()).unwrap();
        connection
            .execute_batch("CREATE TABLE sentinel(value); INSERT INTO sentinel VALUES (42);")
            .unwrap();
        connection
            .pragma_update(None, "application_id", application_id)
            .unwrap();
        connection
            .pragma_update(None, "user_version", version)
            .unwrap();
        let mode: String = connection
            .pragma_update_and_check(None, "journal_mode", journal_mode, |row| row.get(0))
            .unwrap();
        assert_eq!(mode, journal_mode);
        drop(connection);

        let before = fs::read(profile.db()).unwrap();
        assert_eq!(fs::read_dir(&profile.0).unwrap().count(), 1);
        assert!(matches!(
            profile.open(),
            Err(OpenWorkspaceError::UnsupportedFormat)
        ));
        assert_eq!(fs::read(profile.db()).unwrap(), before);
        if journal_mode == "wal" {
            // SQLite's read-only WAL discovery can create lock sidecars in a
            // writable directory. It must not append any frames to the WAL.
            let wal_path = profile.0.join("workspace.sqlite3-wal");
            if wal_path.exists() {
                assert_eq!(fs::metadata(wal_path).unwrap().len(), 0);
            }
        } else {
            assert_eq!(fs::read_dir(&profile.0).unwrap().count(), 1);
        }
    }
}

#[test]
fn future_version_in_the_wal_is_rejected_before_checkpointing_or_migrating() {
    let profile = Profile::new();
    let SqliteWorkspace { connection, .. } = profile.open().unwrap();
    let before = fs::read(profile.db()).unwrap();
    connection
        .pragma_update(None, "user_version", SCHEMA_VERSION + 1)
        .unwrap();
    connection
        .set_db_config(DbConfig::SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, true)
        .unwrap();
    drop(connection);
    let wal_path = profile.0.join("workspace.sqlite3-wal");
    let wal = fs::read(&wal_path).unwrap();
    assert_eq!(fs::read(profile.db()).unwrap(), before);
    assert!(matches!(
        profile.open(),
        Err(OpenWorkspaceError::UnsupportedFormat)
    ));
    assert_eq!(fs::read(profile.db()).unwrap(), before);
    assert_eq!(fs::read(wal_path).unwrap(), wal);
}
