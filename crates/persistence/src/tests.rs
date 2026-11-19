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
