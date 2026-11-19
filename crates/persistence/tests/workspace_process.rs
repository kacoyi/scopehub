mod support;

use std::{
    fs,
    io::{Read, Write},
    path::PathBuf,
    process::{Child, Command, Stdio},
    thread,
    time::{Duration, Instant},
};

use scopehub_application::{
    OperationRegistry, WorkspaceState, WorkspaceStore, WorkspaceTransaction,
};
use scopehub_domain::protocol::Error;
use serde_json::{Value, json};
use support::{
    PROFILE_ID, Profile, WORKSPACE_ID, access, assert_committed, assert_failed, set, undo,
};
use uuid::Uuid;

const INTERRUPT_CHILD_ROOT: &str = "SCOPEHUB_TEST_INTERRUPTED_WORKSPACE_ROOT";
const INTERRUPT_KEY: Uuid = Uuid::from_u128(0xf1734526_acba_4a59_b0da_2a539cd24df0);
const CHILD_DEADLINE: Duration = Duration::from_secs(15);

#[test]
fn process_round_trip_restores_replays_undoes_and_isolates() {
    let first = Profile::new();
    let second = Profile::new();
    let key = Uuid::new_v4();
    let request = set(0, "5m", key);
    // Let the producer exit normally, then discard its committed reply.
    let original = frames(bridge(&first, PROFILE_ID, &[request]));
    assert_snapshot(&original[0], 0, "1m");
    assert_eq!(original.len(), 2);
    drop(original);

    let reconnect = set(0, "5m", key);
    let replay = frames(bridge(&first, PROFILE_ID, std::slice::from_ref(&reconnect)));
    assert_snapshot(&replay[0], 1, "5m");
    let replay = &replay[1];
    assert_eq!(replay["request_id"], reconnect["request_id"]);
    let id = assert_committed(replay, 1, "5m");
    let persisted_id: String = first
        .sqlite()
        .query_row("SELECT id FROM operation_log", [], |row| row.get(0))
        .unwrap();
    assert_eq!(id, persisted_id);

    let other_profile = frames(bridge(&second, "second-profile", &[undo(0, id, key)]));
    assert_snapshot(&other_profile[0], 0, "1m");
    assert_failed(&other_profile[1], "PERMISSION_DENIED");

    let undo_key = Uuid::new_v4();
    let inverse = frames(bridge(&first, PROFILE_ID, &[undo(1, id, undo_key)]));
    assert_snapshot(&inverse[0], 1, "5m");
    let inverse_id = assert_committed(&inverse[1], 2, "1m");
    assert_ne!(id, inverse_id);
    let completed = frames(bridge(
        &first,
        PROFILE_ID,
        &[undo(1, id, undo_key), set(0, "5m", key)],
    ));
    assert_snapshot(&completed[0], 2, "1m");
    assert_eq!(assert_committed(&completed[1], 2, "1m"), inverse_id);
    assert_eq!(assert_committed(&completed[2], 1, "5m"), id);
    let final_start = frames(bridge(&first, PROFILE_ID, &[]));
    assert_snapshot(&final_start[0], 2, "1m");
    assert_snapshot(&frames(bridge(&second, "second-profile", &[]))[0], 0, "1m");
}

#[test]
fn process_rejects_identity_fields_and_oversized_frames() {
    let profile = Profile::new();
    let mut forged = set(0, "5m", Uuid::new_v4());
    forged["profile_id"] = json!("another-profile");
    let mut input = format!("{forged}\n").into_bytes();
    input.extend(vec![b' '; 16 * 1024]);
    input.push(b'\n');

    let mut child = spawn_bridge(&profile, PROFILE_ID);
    child.stdin.take().unwrap().write_all(&input).unwrap();
    let output = collect(child);
    assert_eq!(output[1]["error"]["code"], "INVALID_ARGUMENT");
    assert_eq!(output[2]["error"]["code"], "RESOURCE_LIMIT");
    assert_eq!(output.len(), 3);
    assert_snapshot(&frames(bridge(&profile, PROFILE_ID, &[]))[0], 0, "1m");
}

#[test]
fn killing_the_writer_before_commit_keeps_the_last_committed_revision() {
    let profile = Profile::new();
    let completed = frames(bridge(
        &profile,
        PROFILE_ID,
        &[set(0, "5m", Uuid::new_v4())],
    ));
    assert_committed(&completed[1], 1, "5m");

    let mut child = Command::new(std::env::current_exe().unwrap())
        .args(["--exact", "interrupted_writer_child", "--ignored"])
        .env(INTERRUPT_CHILD_ROOT, &profile.0)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .spawn()
        .unwrap();
    let deadline = Instant::now() + CHILD_DEADLINE;
    while !profile.0.join("staged").is_file() {
        if let Some(status) = child.try_wait().unwrap() {
            panic!("interrupted writer exited before staging: {status}");
        }
        if Instant::now() >= deadline {
            child.kill().unwrap();
            child.wait().unwrap();
            panic!("interrupted writer did not stage its transaction");
        }
        thread::sleep(Duration::from_millis(10));
    }
    child.kill().unwrap();
    assert!(!child.wait().unwrap().success());

    let resumed = frames(bridge(&profile, PROFILE_ID, &[set(1, "1d", INTERRUPT_KEY)]));
    assert_snapshot(&resumed[0], 1, "5m");
    assert_committed(&resumed[1], 2, "1d");
    let count: i64 = profile
        .sqlite()
        .query_row("SELECT count(*) FROM idempotency_records", [], |row| {
            row.get(0)
        })
        .unwrap();
    assert_eq!(count, 2);
}

#[test]
#[ignore = "subprocess fixture for the writer interruption test"]
fn interrupted_writer_child() {
    let Some(root) = std::env::var_os(INTERRUPT_CHILD_ROOT).map(PathBuf::from) else {
        return;
    };
    let store = support::store(&root);
    let mut registry = OperationRegistry::new(InterruptingStore {
        store,
        marker: root.join("staged"),
    });
    let result = registry.execute_workspace(&access(), set(1, "1d", INTERRUPT_KEY));
    panic!("writer returned before interruption: {result:?}");
}

struct InterruptingStore {
    store: scopehub_persistence::SqliteWorkspace,
    marker: PathBuf,
}

impl WorkspaceStore for InterruptingStore {
    fn binding(&self) -> &scopehub_application::WorkspaceBinding {
        self.store.binding()
    }

    fn state(&self) -> Result<WorkspaceState, Error> {
        self.store.state()
    }

    fn transact<T>(
        &mut self,
        work: impl FnOnce(&mut dyn WorkspaceTransaction) -> Result<T, Error>,
    ) -> Result<T, Error> {
        self.store.transact(|transaction| {
            work(transaction)?;
            // The state, receipt and replay key are staged on the SQLite
            // connection. The parent kills this process before it can commit.
            fs::write(&self.marker, b"staged").unwrap();
            loop {
                thread::park();
            }
        })
    }
}

fn spawn_bridge(profile: &Profile, profile_id: &str) -> Child {
    Command::new(env!("CARGO_BIN_EXE_workspace_bridge"))
        .arg(&profile.0)
        .args([profile_id, WORKSPACE_ID])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .unwrap()
}

fn bridge(profile: &Profile, profile_id: &str, requests: &[Value]) -> Vec<Value> {
    let mut child = spawn_bridge(profile, profile_id);
    let mut input = child.stdin.take().unwrap();
    for request in requests {
        writeln!(input, "{request}").unwrap();
    }
    drop(input);
    collect(child)
}

fn collect(mut child: Child) -> Vec<Value> {
    let mut stdout = child.stdout.take().unwrap();
    let reader = thread::spawn(move || {
        let mut text = String::new();
        stdout.read_to_string(&mut text).unwrap();
        text
    });
    let deadline = Instant::now() + CHILD_DEADLINE;
    loop {
        if let Some(status) = child.try_wait().unwrap() {
            assert!(status.success(), "workspace bridge exited {status}");
            break;
        }
        if Instant::now() >= deadline {
            child.kill().unwrap();
            child.wait().unwrap();
            panic!("workspace bridge exceeded the process deadline");
        }
        thread::sleep(Duration::from_millis(10));
    }
    reader
        .join()
        .unwrap()
        .lines()
        .map(|line| serde_json::from_str(line).unwrap())
        .collect()
}

fn frames(output: Vec<Value>) -> Vec<Value> {
    output
        .into_iter()
        .map(|frame| {
            assert_eq!(frame["ok"], true, "{frame}");
            frame["result"].clone()
        })
        .collect()
}

fn assert_snapshot(snapshot: &Value, revision: u64, timeframe: &str) {
    assert_eq!(snapshot["selection_revision"], 0);
    assert_eq!(
        snapshot["workspace"],
        json!({
            "id": WORKSPACE_ID,
            "revision": revision,
            "timeframe": timeframe,
            "style": "candlestick",
            "instrument_id": null,
            "dataset": null,
            "visible_range": null,
            "layers": [],
        })
    );
}
