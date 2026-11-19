use std::{fs, path::PathBuf};

use uuid::Uuid;

use super::*;

#[cfg(unix)]
mod os_privacy {
    use std::os::unix::fs::PermissionsExt;

    use super::*;

    fn set_mode(path: &Path, mode: u32) {
        fs::set_permissions(path, fs::Permissions::from_mode(mode)).unwrap();
    }

    fn mode(path: &Path) -> u32 {
        fs::metadata(path).unwrap().permissions().mode() & 0o777
    }

    #[test]
    fn shared_profile_is_refused_before_sqlite_can_create_any_files() {
        let profile = Profile::new();
        set_mode(&profile.0, 0o755);
        let outcome = profile.open();
        assert!(outcome.is_err(), "a shared profile must be refused");
        assert_eq!(mode(&profile.0), 0o755);
        assert_eq!(fs::read_dir(&profile.0).unwrap().count(), 0);
    }

    #[test]
    fn fresh_store_and_live_sqlite_sidecars_are_owner_only() {
        let profile = Profile::new();
        set_mode(&profile.0, 0o700);
        let writer = profile.open().unwrap();
        for name in [
            DATABASE_NAME,
            "workspace.sqlite3-wal",
            "workspace.sqlite3-shm",
        ] {
            assert_eq!(mode(&profile.0.join(name)), 0o600, "{name}");
        }
        let state = writer.snapshot(None).unwrap().0;
        assert_eq!(state.timeframe, Timeframe::Minute);
        drop(writer);
        assert_eq!(
            profile.open().unwrap().snapshot(None).unwrap().0.revision,
            0
        );
    }

    #[test]
    fn unsafe_existing_storage_is_refused_without_changing_durable_data() {
        let profile = Profile::new();
        let writer = profile.open().unwrap();
        writer
            .connection
            .execute("UPDATE workspaces SET revision = 17", [])
            .unwrap();
        drop(writer);

        let database = profile.db();
        let durable = fs::read(&database).unwrap();
        set_mode(&profile.0, 0o755);
        assert!(matches!(profile.open(), Err(OpenWorkspaceError::Database)));
        assert_eq!(mode(&profile.0), 0o755);
        assert_eq!(fs::read(&database).unwrap(), durable);
        set_mode(&profile.0, 0o700);

        set_mode(&database, 0o644);
        assert!(matches!(profile.open(), Err(OpenWorkspaceError::Database)));
        assert_eq!(mode(&database), 0o644);
        assert_eq!(fs::read(&database).unwrap(), durable);
        set_mode(&database, 0o600);

        for name in [
            "workspace.sqlite3-wal",
            "workspace.sqlite3-shm",
            "workspace.sqlite3-journal",
        ] {
            let sidecar = profile.0.join(name);
            let synthetic = b"prepare no changes to any existing sidecar";
            fs::write(&sidecar, synthetic).unwrap();
            set_mode(&sidecar, 0o644);
            assert!(
                matches!(profile.open(), Err(OpenWorkspaceError::Database)),
                "{name}"
            );
            assert_eq!(mode(&sidecar), 0o644, "{name}");
            assert_eq!(fs::read(&sidecar).unwrap(), synthetic, "{name}");
            assert_eq!(fs::read(&database).unwrap(), durable, "{name}");
            fs::remove_file(sidecar).unwrap();
        }
        assert_eq!(
            profile.open().unwrap().snapshot(None).unwrap().0.revision,
            17
        );
    }

    #[test]
    fn aliases_and_replaceable_ancestors_are_refused_without_opening_storage() {
        let profile = Profile::new();
        let sentinel = profile.0.join("synthetic");
        fs::write(&sentinel, b"preserve this fixture").unwrap();
        set_mode(&sentinel, 0o600);

        std::os::unix::fs::symlink(&sentinel, profile.db()).unwrap();
        assert!(profile.open().is_err());
        assert_eq!(fs::read(&sentinel).unwrap(), b"preserve this fixture");
        fs::remove_file(profile.db()).unwrap();

        fs::hard_link(&sentinel, profile.db()).unwrap();
        assert!(profile.open().is_err());
        assert_eq!(fs::read(&sentinel).unwrap(), b"preserve this fixture");
        fs::remove_file(profile.db()).unwrap();

        let child = profile.0.join("nested");
        SqliteWorkspace::prepare_profile_root(&child).unwrap();
        assert_eq!(mode(&child), 0o700);
        let target = child.join("target");
        SqliteWorkspace::prepare_profile_root(&target).unwrap();
        let alias = profile.0.join("ancestor-alias");
        std::os::unix::fs::symlink(&child, &alias).unwrap();
        assert!(SqliteWorkspace::open(&alias.join("target"), Profile::binding()).is_err());
        assert_eq!(fs::read_dir(&target).unwrap().count(), 0);
        fs::remove_file(alias).unwrap();
        fs::remove_dir(target).unwrap();

        set_mode(&profile.0, 0o777);
        assert!(SqliteWorkspace::open(&child, Profile::binding()).is_err());
        assert!(SqliteWorkspace::prepare_profile_root(&profile.0.join("refused")).is_err());
        assert_eq!(mode(&profile.0), 0o777);
        assert!(!profile.0.join("refused").exists());
        assert_eq!(fs::read_dir(&child).unwrap().count(), 0);
    }
}

#[cfg(windows)]
#[test]
fn windows_private_acl_inheritance_rejects_shared_roots_and_sidecars() {
    use std::os::windows::process::CommandExt;

    let profile = Profile::new();
    // A regular file created with default Windows attributes inherits the
    // same owner ACL that a launcher bootstrap file receives.
    let ordinary = profile.db();
    fs::write(&ordinary, b"temporary synthetic launch fixture").unwrap();
    assert!(storage::open_path(&profile.0).unwrap().1);
    fs::remove_file(&ordinary).unwrap();

    let writer = profile.open().unwrap();
    writer
        .connection
        .execute("UPDATE workspaces SET revision = 17", [])
        .unwrap();
    let wal = profile.0.join("workspace.sqlite3-wal");
    let shm = profile.0.join("workspace.sqlite3-shm");
    assert!(wal.is_file());
    assert!(shm.is_file());
    // Verify the ACL on the live files created by SQLite before shutdown.
    storage::open_path(&profile.0).unwrap();
    writer
        .connection
        .set_db_config(DbConfig::SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, true)
        .unwrap();
    drop(writer);

    let before = fs::read(profile.db()).unwrap();
    let wal_bytes = fs::read(&wal).unwrap();
    let icacls = PathBuf::from(std::env::var_os("SystemRoot").unwrap()).join("System32/icacls.exe");
    let status = std::process::Command::new(&icacls)
        .arg(&wal)
        .args(["/grant", "*S-1-1-0:(R)"])
        .creation_flags(0x0800_0000)
        .output()
        .unwrap()
        .status;
    assert!(status.success());
    assert!(matches!(profile.open(), Err(OpenWorkspaceError::Database)));
    assert_eq!(fs::read(profile.db()).unwrap(), before);
    assert_eq!(fs::read(&wal).unwrap(), wal_bytes);

    let status = std::process::Command::new(&icacls)
        .arg(&wal)
        .args(["/remove:g", "*S-1-1-0"])
        .creation_flags(0x0800_0000)
        .output()
        .unwrap()
        .status;
    assert!(status.success());
    assert_eq!(
        profile.open().unwrap().snapshot(None).unwrap().0.revision,
        17
    );

    let before = fs::read(profile.db()).unwrap();
    let status = std::process::Command::new(&icacls)
        .arg(&profile.0)
        .args(["/grant", "*S-1-1-0:(RX)"])
        .creation_flags(0x0800_0000)
        .output()
        .unwrap()
        .status;
    assert!(status.success());
    assert!(SqliteWorkspace::prepare_profile_root(&profile.0).is_err());
    assert!(profile.open().is_err());
    assert_eq!(fs::read(profile.db()).unwrap(), before);
}

#[cfg(windows)]
#[test]
fn windows_ancestor_acl_allows_reading_and_refuses_replaceable_entries() {
    use std::os::windows::process::CommandExt;

    let parent = Profile::new();
    let profile_root = parent.0.join("workspace-dev");
    let icacls = PathBuf::from(std::env::var_os("SystemRoot").unwrap()).join("System32/icacls.exe");
    let grant = |ace| {
        assert!(
            std::process::Command::new(&icacls)
                .arg(&parent.0)
                .args(["/grant:r", ace])
                .creation_flags(0x0800_0000)
                .output()
                .unwrap()
                .status
                .success()
        );
    };

    grant("*S-1-1-0:(RX)");
    SqliteWorkspace::prepare_profile_root(&profile_root).unwrap();
    let writer = SqliteWorkspace::open(&profile_root, Profile::binding()).unwrap();
    writer
        .connection
        .execute("UPDATE workspaces SET revision = 19", [])
        .unwrap();
    drop(writer);
    let file = profile_root.join(DATABASE_NAME);
    let durable = fs::read(&file).unwrap();

    grant("*S-1-1-0:(DC)");
    let refusal = storage::open_path(&profile_root).unwrap_err();
    assert_eq!(refusal.to_string(), "replaceable-ancestor");
    assert_eq!(fs::read(&file).unwrap(), durable);
    grant("*S-1-1-0:(RX)");
    let writer = SqliteWorkspace::open(&profile_root, Profile::binding()).unwrap();
    assert_eq!(writer.snapshot(None).unwrap().0.revision, 19);
}

#[cfg(windows)]
#[test]
fn windows_temp_alias_and_full_path_open_the_same_durable_profile() {
    use std::os::windows::process::CommandExt;

    let profile = Profile::new();
    let full = profile.0.canonicalize().unwrap();
    let dos_path = Path::new(full.to_str().unwrap().strip_prefix(r"\\?\").unwrap());
    let powershell = PathBuf::from(std::env::var_os("SystemRoot").unwrap())
        .join("System32/WindowsPowerShell/v1.0/powershell.exe");
    // The filesystem supplies the short alias when 8.3 names are enabled.
    // On hosted Windows the incoming TEMP path can already contain one.
    let alias = std::process::Command::new(powershell)
        .args([
            "-NoProfile",
            "-NonInteractive",
            "-NoLogo",
            "-EncodedCommand",
        ])
        .arg(storage::encoded_powershell_script(
            r"
            $ErrorActionPreference = 'Stop'
            [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
            $filesystem = New-Object -ComObject Scripting.FileSystemObject
            [Console]::WriteLine($filesystem.GetFolder($env:SCOPEHUB_ACL_TEST_PATH).ShortPath)
        ",
        ))
        .env("SCOPEHUB_ACL_TEST_PATH", dos_path)
        .creation_flags(0x0800_0000)
        .output()
        .unwrap();
    assert!(alias.status.success(), "short-path lookup failed");
    let alias = PathBuf::from(std::str::from_utf8(&alias.stdout).unwrap().trim());
    assert!(alias.is_absolute());
    assert!(
        alias.canonicalize().unwrap() == full,
        "temporary path forms must resolve to the same private directory"
    );

    let writer = SqliteWorkspace::open(dos_path, Profile::binding()).unwrap();
    writer
        .connection
        .execute("UPDATE workspaces SET revision = 23", [])
        .unwrap();
    drop(writer);
    for path in [&profile.0, &alias] {
        let writer = SqliteWorkspace::open(path, Profile::binding()).unwrap();
        assert_eq!(writer.snapshot(None).unwrap().0.revision, 23);
    }
}

struct Profile(PathBuf);

impl Profile {
    fn new() -> Self {
        let temporary = std::env::temp_dir();
        #[cfg(unix)]
        let temporary = temporary.canonicalize().unwrap();
        let path = temporary.join(format!("scopehub-migration-{}", Uuid::new_v4()));
        SqliteWorkspace::prepare_profile_root(&path).unwrap();
        Self(path)
    }

    fn db(&self) -> PathBuf {
        self.0.join(DATABASE_NAME)
    }

    fn raw_connection(&self) -> Connection {
        let (path, _) = storage::open_path(&self.0).unwrap();
        Connection::open(path).unwrap()
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
    let mut connection = profile.raw_connection();
    connection
        .pragma_update_and_check(None, "journal_mode", "WAL", |row| row.get::<_, String>(0))
        .unwrap();
    {
        let transaction = connection
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .unwrap();
        migrate(&transaction, &Profile::binding()).unwrap();
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
        let connection = profile.raw_connection();
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

#[test]
fn decision_note_migration_rolls_back_and_preserves_v1_timeframe_and_replay() {
    let profile = Profile::new();
    let mut database = profile.raw_connection();
    let transaction = database.transaction().unwrap();
    migrate_initial(&transaction, &Profile::binding()).unwrap();
    transaction
        .execute_batch(
            "UPDATE workspaces SET revision = 1, timeframe = '5m';
             INSERT INTO operation_log VALUES
               ('chart-op', 'workspace', 'chart.setTimeframe', 'user', 'principal', '',
                'chart-request', 300, 1, 1, '1m', '5m', NULL);
             INSERT INTO idempotency_records VALUES
               ('workspace', 'principal', '', 'chart.setTimeframe', 'chart-key', zeroblob(32), 'chart-op');",
        )
        .unwrap();
    transaction.commit().unwrap();

    // An interrupted upgrade rolls back all staged DDL and ledger entries.
    let transaction = database.transaction().unwrap();
    migrate(&transaction, &Profile::binding()).unwrap();
    assert_eq!(integer_pragma(&transaction, "user_version"), SCHEMA_VERSION);
    drop(transaction);
    assert_eq!(integer_pragma(&database, "user_version"), 1);
    assert!(database.prepare("SELECT * FROM decision_notes").is_err());
    assert!(
        database
            .prepare("SELECT decision_note_id FROM operation_log")
            .is_err()
    );
    drop(database);

    let mut migrated = profile.open().unwrap();
    assert_eq!(
        integer_pragma(&migrated.connection, "user_version"),
        SCHEMA_VERSION
    );
    assert_eq!(integer_pragma(&migrated.connection, "secure_delete"), 1);
    let (chart, undo) = migrated
        .snapshot(Some(WorkspaceUndoAccess {
            actor: Actor::User,
            principal_id: "principal",
            paired_client_id: None,
        }))
        .unwrap();
    assert_eq!(
        (chart.revision, chart.timeframe),
        (1, Timeframe::FiveMinutes)
    );
    assert_eq!(undo.unwrap().operation_id, "chart-op");
    let chart_replay = migrated
        .transact(|tx| tx.replay(&key(SET_TIMEFRAME, "chart-key")))
        .unwrap()
        .unwrap();
    assert_eq!(chart_replay.receipt.operation_id, "chart-op");
    assert_eq!(chart_replay.digest, [0; 32]);
    assert!(migrated.read_notes(None, 0).unwrap().notes.is_empty());
}

#[test]
fn decision_note_restore_migration_keeps_v2_outcome_order_and_rollback() {
    let profile = Profile::new();
    let mut database = profile.raw_connection();
    database.pragma_update(None, "foreign_keys", true).unwrap();
    let transaction = database.transaction().unwrap();
    migrate_initial(&transaction, &Profile::binding()).unwrap();
    migrate_decision_notes(&transaction).unwrap();
    transaction.execute_batch(
        "UPDATE workspaces SET revision = 3;
         INSERT INTO operation_log VALUES
           ('save', 'workspace', 'decisionNote.save', 'user', 'principal', '',
            'save', 100, 1, 0, '1m', '1m', NULL, 'note'),
           ('earlier', 'workspace', 'decisionNote.appendOutcome', 'user', 'principal', '',
            'outcome', 200, 2, 0, '1m', '1m', NULL, 'note'),
           ('later', 'workspace', 'decisionNote.appendOutcome', 'user', 'principal', '',
            'outcome', 300, 3, 0, '1m', '1m', NULL, 'note');
         INSERT INTO decision_notes VALUES ('workspace', 'note', '2026-06-01', 'thesis', '[]', 'choice');
         INSERT INTO decision_note_outcomes VALUES
           ('workspace', 'note', 'later', '2026-06-02', 'entered second', 3),
           ('workspace', 'note', 'earlier', '2026-06-03', 'entered first', 2);",
    ).unwrap();
    transaction.commit().unwrap();

    let transaction = database.transaction().unwrap();
    migrate(&transaction, &Profile::binding()).unwrap();
    assert_eq!(integer_pragma(&transaction, "user_version"), SCHEMA_VERSION);
    drop(transaction);
    assert_eq!(
        integer_pragma(&database, "user_version"),
        DECISION_NOTES_SCHEMA_VERSION
    );
    assert!(
        database
            .prepare("SELECT ordinal FROM decision_note_outcomes")
            .is_err()
    );
    drop(database);

    let migrated = profile.open().unwrap();
    let notes = migrated.read_notes(Some("note"), 0).unwrap();
    assert_eq!(
        notes
            .outcomes
            .iter()
            .map(|outcome| outcome.outcome_id.as_str())
            .collect::<Vec<_>>(),
        ["earlier", "later"]
    );
    let rows = migrated
        .connection
        .prepare("SELECT revision, ordinal FROM decision_note_outcomes ORDER BY ordinal")
        .unwrap()
        .query_map([], |row| Ok((row.get::<_, i64>(0)?, row.get::<_, i64>(1)?)))
        .unwrap()
        .collect::<rusqlite::Result<Vec<_>>>()
        .unwrap();
    assert_eq!(rows, [(2, 1), (3, 2)]);
}

mod decision_note_storage {
    use scopehub_application::{
        DECISION_NOTE_APPEND_OUTCOME, DECISION_NOTE_DELETE, DECISION_NOTE_RESTORE_BACKUP,
        DECISION_NOTE_SAVE, DecisionNoteMutation, DecisionNoteReceipt,
    };
    use scopehub_domain::protocol::{
        DecisionNoteBackupDeletion, DecisionNoteBackupEntry, DecisionNoteEvidenceRef,
        DecisionNoteOriginal, DecisionNoteOutcome,
    };

    use super::*;

    const NOTE_ID: &str = "9ff11b8d-dd93-41a1-8d37-5dd7ece8be41";
    const OUTCOME_ID: &str = "9813b947-cfbf-44da-a8cc-9d98386ecf53";
    const THESIS: &str = "synthetic-thesis-deletion-sentinel-b6a641";
    const LOCATOR: &str = "synthetic-locator-deletion-sentinel-f995e2";
    const CHOICE: &str = "synthetic-choice-deletion-sentinel-c7b8d9";
    const OUTCOME: &str = "synthetic-outcome-deletion-sentinel-42b464";

    fn record_note(
        store: &mut SqliteWorkspace,
        operation: &str,
        mutation: DecisionNoteMutation,
    ) -> Result<(), Error> {
        record_note_for(store, operation, NOTE_ID, "stable-note-key", mutation)
    }

    fn record_note_for(
        store: &mut SqliteWorkspace,
        operation: &str,
        note_id: &str,
        idempotency_key: &str,
        mutation: DecisionNoteMutation,
    ) -> Result<(), Error> {
        store.transact(|tx| {
            tx.record_note(&DecisionNoteChange {
                key: key(operation, idempotency_key),
                digest: [98; 32],
                receipt: DecisionNoteReceipt {
                    operation_id: Uuid::new_v4().to_string(),
                    note_id: note_id.into(),
                    revision: tx.state()?.revision + 1,
                },
                request_id: "storage-tests",
                committed_at_ms: 7_500,
                mutation,
            })
        })
    }

    fn save_note(store: &mut SqliteWorkspace) {
        record_note(
            store,
            DECISION_NOTE_SAVE,
            DecisionNoteMutation::Save(DecisionNoteOriginal {
                note_id: NOTE_ID.into(),
                decided_on: "2024-01-03".into(),
                thesis: THESIS.into(),
                evidence_refs: vec![DecisionNoteEvidenceRef {
                    source_id: "synthetic-source".into(),
                    source_revision: "synthetic-revision".into(),
                    locator: LOCATOR.into(),
                }],
                choice: CHOICE.into(),
            }),
        )
        .unwrap();
    }

    fn append_outcome(store: &mut SqliteWorkspace) {
        record_note(
            store,
            DECISION_NOTE_APPEND_OUTCOME,
            DecisionNoteMutation::Append(DecisionNoteOutcome {
                outcome_id: OUTCOME_ID.into(),
                observed_on: "2024-02-03".into(),
                outcome: OUTCOME.into(),
            }),
        )
        .unwrap();
    }

    fn delete_note(store: &mut SqliteWorkspace) -> Result<(), Error> {
        record_note(store, DECISION_NOTE_DELETE, DecisionNoteMutation::Delete)
    }

    fn assert_live_files_body_free(profile: &Profile) {
        assert_no_body_sentinels(profile, &[THESIS, LOCATOR, CHOICE, OUTCOME]);
    }

    fn assert_no_body_sentinels(profile: &Profile, sentinels: &[&str]) {
        for file in fs::read_dir(&profile.0).unwrap() {
            let file = file.unwrap();
            let bytes = fs::read(file.path()).unwrap();
            for sentinel in sentinels {
                assert!(
                    !bytes
                        .windows(sentinel.len())
                        .any(|candidate| candidate == sentinel.as_bytes()),
                    "synthetic sentinel remained in {}",
                    file.file_name().display()
                );
            }
        }
    }

    fn original(note_id: &str, thesis: &str) -> DecisionNoteOriginal {
        DecisionNoteOriginal {
            note_id: note_id.into(),
            decided_on: "2026-05-01".into(),
            thesis: thesis.into(),
            evidence_refs: vec![],
            choice: "synthetic choice".into(),
        }
    }

    #[test]
    fn decision_note_restore_merges_markers_and_outcomes_with_atomic_rollback() {
        const MARKED_IN_BACKUP: &str = "10000000-aaaa-4aaa-aaaa-000000000001";
        const ONLY_LOCAL: &str = "10000000-aaaa-4aaa-aaaa-000000000002";
        const RETAINED_COPY: &str = "10000000-aaaa-4aaa-aaaa-000000000003";
        const REMOVED_THESIS: &str = "synthetic thesis covered by remote marker 28ca60";
        let profile = Profile::new();
        let mut live = profile.open().unwrap();
        save_note(&mut live);
        append_outcome(&mut live);
        let mut backup = live.decision_note_backup().unwrap();
        delete_note(&mut live).unwrap();
        live.clean_deleted_note().unwrap();
        for (id, thesis) in [
            (MARKED_IN_BACKUP, REMOVED_THESIS),
            (ONLY_LOCAL, "local only"),
        ] {
            record_note_for(
                &mut live,
                DECISION_NOTE_SAVE,
                id,
                id,
                DecisionNoteMutation::Save(original(id, thesis)),
            )
            .unwrap();
        }
        live.connection
            .execute_batch("PRAGMA wal_checkpoint(TRUNCATE)")
            .unwrap();

        backup.deletions.push(DecisionNoteBackupDeletion {
            note_id: MARKED_IN_BACKUP.into(),
            deleted_at_ms: 195_301,
        });
        backup.notes.push(DecisionNoteBackupEntry {
            original: original(
                MARKED_IN_BACKUP,
                "suppressed before reading the live original",
            ),
            outcomes: vec![],
        });
        let retained = DecisionNoteBackupEntry {
            original: original(RETAINED_COPY, "retained copy thesis"),
            outcomes: vec![
                DecisionNoteOutcome {
                    outcome_id: "10000000-aaaa-4aaa-aaaa-000000000004".into(),
                    observed_on: "2026-05-05".into(),
                    outcome: "first recorded outcome".into(),
                },
                DecisionNoteOutcome {
                    outcome_id: "10000000-aaaa-4aaa-aaaa-000000000005".into(),
                    observed_on: "2026-05-07".into(),
                    outcome: "second recorded outcome".into(),
                },
            ],
        };
        backup.notes.push(retained.clone());
        let plan = live.transact(|tx| tx.plan_note_restore(backup)).unwrap();
        assert_eq!(plan.originals, vec![retained.original.clone()]);
        assert_eq!(plan.outcomes[0].note_id, retained.original.note_id);
        assert_eq!(plan.outcomes[0].outcomes, retained.outcomes);
        assert_eq!(
            (
                plan.summary.added_notes,
                plan.summary.added_outcomes,
                plan.summary.removed_notes,
                plan.summary.suppressed_notes,
                plan.summary.imported_deletions,
                plan.summary.independent_copy_warning
            ),
            (1, 2, 1, 2, 1, true)
        );
        assert_eq!(live.snapshot(None).unwrap().0.revision, 5);
        assert!(live.read_notes(Some(MARKED_IN_BACKUP), 0).is_ok());

        let change = WorkspaceChange {
            key: key(DECISION_NOTE_RESTORE_BACKUP, "restore-test-key"),
            digest: [65; 32],
            receipt: WorkspaceReceipt {
                operation_id: "restore".into(),
                revision: 6,
                undoable: false,
                before: Timeframe::Minute,
                after: Timeframe::Minute,
            },
            actor: Actor::User,
            request_id: "storage-tests",
            committed_at_ms: 500_000,
            undoes: None,
        };
        live.connection
            .execute_batch(
                "CREATE TEMP TRIGGER reject_restore_marker BEFORE INSERT ON decision_note_deletions
             BEGIN SELECT raise(ABORT, 'synthetic restore marker failure'); END;",
            )
            .unwrap();
        let failed = live.transact(|tx| tx.record_note_restore(&change, &plan));
        assert_eq!(failed.unwrap_err().code, ErrorCode::INTERNALERROR);
        assert_eq!(live.snapshot(None).unwrap().0.revision, 5);
        assert!(live.read_notes(Some(MARKED_IN_BACKUP), 0).is_ok());
        assert_eq!(
            live.read_notes(Some(RETAINED_COPY), 0).unwrap_err().code,
            ErrorCode::DATAUNAVAILABLE
        );
        assert!(
            live.transact(|tx| tx.replay(&change.key))
                .unwrap()
                .is_none()
        );
        live.connection
            .execute_batch("DROP TRIGGER reject_restore_marker")
            .unwrap();

        live.transact(|tx| tx.record_note_restore(&change, &plan))
            .unwrap();
        live.clean_deleted_note().unwrap();
        let restored = live.read_notes(Some(RETAINED_COPY), 0).unwrap();
        assert_eq!(restored.note, Some(retained.original));
        assert_eq!(restored.outcomes, retained.outcomes);
        assert!(live.read_notes(Some(ONLY_LOCAL), 0).is_ok());
        for removed in [NOTE_ID, MARKED_IN_BACKUP] {
            assert_eq!(
                live.read_notes(Some(removed), 0).unwrap_err().code,
                ErrorCode::DATAUNAVAILABLE
            );
        }
        let row: (i64, i64, bool) = live.connection.query_row(
            "SELECT deleted_at_ms, revision, cleanup_pending FROM decision_note_deletions WHERE note_id = ?1",
            [MARKED_IN_BACKUP], |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
        ).unwrap();
        assert_eq!(row, (195_301, 6, false));
        let old_replay = live
            .transact(|tx| tx.note_replay(&key(DECISION_NOTE_SAVE, MARKED_IN_BACKUP)))
            .unwrap()
            .unwrap();
        assert!(old_replay.digest.is_none());
        assert_no_body_sentinels(
            &profile,
            &[THESIS, LOCATOR, CHOICE, OUTCOME, REMOVED_THESIS],
        );
        drop(live);

        let reopened = profile.open().unwrap();
        let archive = reopened.decision_note_backup().unwrap();
        assert_eq!(archive.revision, 6);
        assert_eq!(
            archive
                .notes
                .iter()
                .map(|note| note.original.note_id.as_str())
                .collect::<Vec<_>>(),
            [ONLY_LOCAL, RETAINED_COPY]
        );
        assert_eq!(archive.deletions.len(), 2);
    }

    #[test]
    fn decision_note_restore_plans_only_missing_outcomes_and_rejects_immutable_conflicts() {
        let profile = Profile::new();
        let mut live = profile.open().unwrap();
        save_note(&mut live);
        append_outcome(&mut live);

        let mut conflicting = live.decision_note_backup().unwrap();
        conflicting.notes[0].original.thesis = "different original".into();
        let error = live
            .transact(|tx| tx.plan_note_restore(conflicting))
            .err()
            .unwrap();
        assert_eq!(error.code, ErrorCode::STALECONTEXT);
        let mut conflicting = live.decision_note_backup().unwrap();
        conflicting.notes[0].outcomes[0].outcome = "different outcome with the same ID".into();
        let error = live
            .transact(|tx| tx.plan_note_restore(conflicting))
            .err()
            .unwrap();
        assert_eq!(error.code, ErrorCode::STALECONTEXT);

        let additional = DecisionNoteOutcome {
            outcome_id: "10000000-aaaa-4aaa-aaaa-000000000006".into(),
            observed_on: "2026-06-07".into(),
            outcome: "an additional dated outcome".into(),
        };
        let mut backup = live.decision_note_backup().unwrap();
        backup.notes[0].outcomes.push(additional.clone());
        let plan = live.transact(|tx| tx.plan_note_restore(backup)).unwrap();
        assert_eq!(plan.summary.added_notes, 0);
        assert!(!plan.summary.independent_copy_warning);
        assert_eq!(plan.summary.added_outcomes, 1);
        assert!(
            plan.originals.is_empty(),
            "an outcome-only stage keeps no copy of the original"
        );
        assert_eq!(plan.outcomes[0].note_id, NOTE_ID);
        assert_eq!(plan.outcomes[0].outcomes, vec![additional]);
        let live_note = live.read_notes(Some(NOTE_ID), 0).unwrap();
        assert_eq!(live_note.note.unwrap().thesis, THESIS);
        assert_eq!(live_note.outcomes[0].outcome, OUTCOME);
        assert_eq!(live.snapshot(None).unwrap().0.revision, 2);
    }

    #[test]
    fn decision_note_deletion_rolls_back_on_marker_failure_and_scrubs_live_database_and_wal() {
        let profile = Profile::new();
        let mut store = profile.open().unwrap();
        save_note(&mut store);
        // Put the original body in the main database, with its later outcome
        // still in older WAL frames when the delete commits.
        store
            .connection
            .execute_batch("PRAGMA wal_checkpoint(TRUNCATE)")
            .unwrap();
        append_outcome(&mut store);
        store
            .connection
            .execute_batch(
                "CREATE TEMP TRIGGER reject_marker BEFORE INSERT ON decision_note_deletions
             BEGIN SELECT raise(ABORT, 'synthetic marker failure'); END;",
            )
            .unwrap();
        assert_eq!(
            delete_note(&mut store).unwrap_err().code,
            ErrorCode::INTERNALERROR
        );
        assert_eq!(store.snapshot(None).unwrap().0.revision, 2);
        let before = store.read_notes(Some(NOTE_ID), 0).unwrap();
        assert_eq!(before.note.unwrap().evidence_refs[0].locator, LOCATOR);
        assert_eq!(before.outcomes[0].outcome, OUTCOME);
        assert!(
            store
                .transact(|tx| tx.note_replay(&key(DECISION_NOTE_DELETE, "stable-note-key")))
                .unwrap()
                .is_none()
        );
        store
            .connection
            .execute_batch("DROP TRIGGER reject_marker")
            .unwrap();

        delete_note(&mut store).unwrap();
        store.clean_deleted_note().unwrap();
        assert_eq!(store.snapshot(None).unwrap().0.revision, 3);
        assert_eq!(
            store.read_notes(Some(NOTE_ID), 0).unwrap_err().code,
            ErrorCode::DATAUNAVAILABLE
        );
        assert!(store.read_notes(None, 0).unwrap().notes.is_empty());
        let marker: (String, i64, bool) = store.connection.query_row(
            "SELECT profile_id, revision, cleanup_pending FROM decision_note_deletions WHERE note_id = ?1",
            [NOTE_ID],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
        ).unwrap();
        assert_eq!(marker, (Profile::binding().profile_id, 3, false));
        for operation in [
            DECISION_NOTE_SAVE,
            DECISION_NOTE_APPEND_OUTCOME,
            DECISION_NOTE_DELETE,
        ] {
            let replay_key = key(operation, "stable-note-key");
            let replay = store
                .transact(|tx| tx.note_replay(&replay_key))
                .unwrap()
                .unwrap();
            assert_eq!(replay.receipt.note_id, NOTE_ID);
            assert_eq!(
                replay.digest,
                (operation == DECISION_NOTE_DELETE).then_some([98; 32])
            );
            let stored_digest: [u8; 32] = store
                .connection
                .query_row(
                    "SELECT digest FROM idempotency_records WHERE operation = ?1",
                    [operation],
                    |row| row.get(0),
                )
                .unwrap();
            assert_eq!(stored_digest, replay.digest.unwrap_or([0; 32]));
        }
        let outcomes: i64 = store
            .connection
            .query_row("SELECT count(*) FROM decision_note_outcomes", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(outcomes, 0);
        assert_live_files_body_free(&profile);
    }

    #[test]
    fn decision_note_reopen_finishes_a_checkpoint_blocked_by_an_older_reader() {
        let profile = Profile::new();
        let mut store = profile.open().unwrap();
        save_note(&mut store);
        let reader = profile.raw_connection();
        let snapshot = reader.unchecked_transaction().unwrap();
        assert_eq!(
            snapshot
                .query_row("SELECT thesis FROM decision_notes", [], |row| row
                    .get::<_, String>(0))
                .unwrap(),
            THESIS
        );

        delete_note(&mut store).unwrap();
        store.connection.busy_timeout(Duration::ZERO).unwrap();
        assert_eq!(
            store.clean_deleted_note().unwrap_err().code,
            ErrorCode::BUSY
        );
        assert!(store.transact(|tx| tx.note_is_deleted(NOTE_ID)).unwrap());
        assert!(store.read_notes(None, 0).unwrap().notes.is_empty());
        store
            .connection
            .set_db_config(DbConfig::SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, true)
            .unwrap();
        reader
            .set_db_config(DbConfig::SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, true)
            .unwrap();
        drop(snapshot);
        drop(reader);
        drop(store);

        let mut reopened = profile.open().unwrap();
        assert!(reopened.transact(|tx| tx.note_is_deleted(NOTE_ID)).unwrap());
        let pending: bool = reopened
            .connection
            .query_row(
                "SELECT cleanup_pending FROM decision_note_deletions",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert!(!pending);
        assert_live_files_body_free(&profile);
    }
}

fn key<'a>(operation: &'a str, idempotency_key: &'a str) -> WorkspaceReplayKey<'a> {
    WorkspaceReplayKey {
        principal_id: "principal",
        paired_client_id: None,
        operation,
        idempotency_key,
    }
}

#[path = "reviewed_mapping_tests.rs"]
mod reviewed_mapping_tests;
