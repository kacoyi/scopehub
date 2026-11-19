#[path = "support/decision_note_restore.rs"]
mod decision_note_restore;
#[path = "support/operations_workflow.rs"]
mod operations_workflow;
#[path = "support/private_record_workflow.rs"]
mod private_record_workflow;
mod support;

use std::{
    fs,
    io::{BufRead, BufReader, Read, Write},
    path::PathBuf,
    process::{Child, ChildStdin, Command, Stdio},
    sync::mpsc::{self, Receiver},
    thread,
    time::{Duration, Instant},
};

use scopehub_application::{
    Actor, ChartEvidenceStatus, DECISION_NOTE_APPEND_OUTCOME, DECISION_NOTE_DELETE,
    DECISION_NOTE_READ, DECISION_NOTE_SAVE, DecisionNoteBackupData, LOCAL_WORKSPACE_OPERATIONS,
    OperationRegistry, WorkspaceAccess, WorkspaceState, WorkspaceStore, WorkspaceTransaction,
    WorkspaceUndoAccess, WorkspaceUndoTarget,
};
use scopehub_domain::protocol::{DecisionNoteReadOutput, Error};
use serde_json::{Value, json};
use support::{
    PROFILE_ID, Profile, WORKSPACE_ID, access, assert_committed, assert_failed, set, store, undo,
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
    assert_eq!(
        frames(bridge(&first, PROFILE_ID, &[]))[0]["undo"],
        json!({
            "operation_id": id, "committed_workspace_revision": 1,
        })
    );

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
    assert_eq!(final_start[0]["undo"], Value::Null);
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
fn decision_note_lifecycle_across_fresh_bridge_processes() {
    const THESIS: &str = "synthetic original thesis 874dcf01";
    const CHOICE: &str = "synthetic decision to wait d33a36d8";
    const LOCATOR: &str = "synthetic evidence row 495e4507";
    const LATER: &str = "synthetic later outcome a12cff97";
    const DECLINED: &str = "synthetic declined body e597c657";
    let first = Profile::new();
    let second = Profile::new();
    let note_id = Uuid::new_v4().to_string();
    let original = json!({
        "decided_on": "2026-01-03", "thesis": THESIS, "choice": CHOICE,
        "evidence_refs": [{"source_id": "synthetic-bars", "source_revision": "bars-v1", "locator": LOCATOR}],
    });
    let mut initial = LiveBridge::open(&first, PROFILE_ID);
    assert_snapshot(&initial.reply(), 0, "1m");
    let timeframe = initial.send(&set(0, "5m", Uuid::new_v4()));
    let timeframe_id = assert_committed(&timeframe, 1, "5m");

    let mut declined = note_write(DECISION_NOTE_SAVE, &note_id, 1, original.clone());
    declined["intent"]["arguments"]["thesis"] = json!(DECLINED);
    let preview = initial.send(&declined);
    let reject = confirm(&declined, &preview, "decline");
    assert_failed(&initial.send(&reject), "CANCELLED");
    assert_failed(
        &initial.send(&confirm(&declined, &preview, "approve")),
        "CONFIRMATION_REQUIRED",
    );
    assert_eq!(
        note_result(&initial.send(&note_read(None)))["notes"],
        json!([])
    );
    assert_profile_absent(&first, &[DECLINED]);

    let save = note_write(DECISION_NOTE_SAVE, &note_id, 1, original.clone());
    let preview = initial.send(&save);
    let approved = confirm(&save, &preview, "approve");
    let mut altered = approved.clone();
    altered["intent"]["arguments"]["evidence_refs"][0]["locator"] = json!("different row");
    assert_failed(&initial.send(&altered), "CONFIRMATION_REQUIRED");
    initial.dispatch(&approved);
    // Drop the delivery before opening a fresh process. The writer drains the
    // admitted request and exits, and the retry has new transport correlation.
    initial.finish();
    let retry = with_new_correlation(save.clone());
    let mut review = LiveBridge::open(&first, PROFILE_ID);
    let snapshot = review.reply();
    assert_snapshot(&snapshot, 2, "5m");
    assert_eq!(snapshot["undo"]["operation_id"], timeframe_id);
    assert_eq!(snapshot["undo"]["committed_workspace_revision"], 1);
    let replay = review.send(&retry);
    assert_eq!(replay["request_id"], retry["request_id"]);
    let save_id = note_receipt(&replay, DECISION_NOTE_SAVE, &note_id, 2);
    let mut different_retry = retry.clone();
    different_retry["intent"]["arguments"]["choice"] = json!("another choice");
    assert_failed(&review.send(&different_retry), "INVALID_ARGUMENT");

    let mut isolated = LiveBridge::open(&second, "other-synthetic-profile");
    assert_snapshot(&isolated.reply(), 0, "1m");
    assert_eq!(
        note_result(&isolated.send(&note_read(None)))["notes"],
        json!([])
    );
    assert_failed(
        &isolated.send(&note_read(Some(&note_id))),
        "DATA_UNAVAILABLE",
    );
    isolated.finish();
    let index = review.send(&note_read(None));
    assert_eq!(
        note_result(&index)["notes"],
        json!([{"note_id":note_id,"decided_on":"2026-01-03"}])
    );
    let mut expected_original = original;
    expected_original["note_id"] = json!(note_id);
    let before = review.send(&note_read(Some(&note_id)));
    assert_eq!(note_result(&before)["note"], expected_original);
    assert_eq!(note_result(&before)["outcomes"], json!([]));

    let same_day = note_write(
        DECISION_NOTE_APPEND_OUTCOME,
        &note_id,
        2,
        json!({
            "observed_on": "2026-01-03", "outcome": LATER,
        }),
    );
    assert_failed(&review.send(&same_day), "INVALID_ARGUMENT");
    let append = note_write(
        DECISION_NOTE_APPEND_OUTCOME,
        &note_id,
        2,
        json!({
            "observed_on": "2026-02-05", "outcome": LATER,
        }),
    );
    let preview = review.send(&append);
    let result = review.send(&confirm(&append, &preview, "approve"));
    let outcome_id = note_receipt(&result, DECISION_NOTE_APPEND_OUTCOME, &note_id, 3);
    review.finish();

    let mut deletion = LiveBridge::open(&first, PROFILE_ID);
    assert_snapshot(&deletion.reply(), 3, "5m");
    let after = deletion.send(&note_read(Some(&note_id)));
    assert_eq!(note_result(&after)["note"], expected_original);
    assert_eq!(
        note_result(&after)["outcomes"],
        json!([
            {"outcome_id":outcome_id,"observed_on":"2026-02-05","outcome":LATER}
        ])
    );
    assert_committed(
        &deletion.send(&undo(3, timeframe_id, Uuid::new_v4())),
        4,
        "1m",
    );
    let remove = note_write(DECISION_NOTE_DELETE, &note_id, 4, json!({}));
    let preview = deletion.send(&remove);
    let removed = deletion.send(&confirm(&remove, &preview, "approve"));
    let delete_id = note_receipt(&removed, DECISION_NOTE_DELETE, &note_id, 5);
    assert_failed(
        &deletion.send(&note_read(Some(&note_id))),
        "DATA_UNAVAILABLE",
    );
    assert_eq!(
        note_result(&deletion.send(&note_read(None)))["notes"],
        json!([])
    );
    assert_profile_absent(&first, &[THESIS, CHOICE, LOCATOR, LATER, DECLINED]);
    deletion.finish();

    let mut reopened = LiveBridge::open(&first, PROFILE_ID);
    assert_snapshot(&reopened.reply(), 5, "1m");
    assert_failed(
        &reopened.send(&note_read(Some(&note_id))),
        "DATA_UNAVAILABLE",
    );
    assert_failed(
        &reopened.send(&with_new_correlation(save)),
        "DATA_UNAVAILABLE",
    );
    assert_failed(
        &reopened.send(&with_new_correlation(append)),
        "DATA_UNAVAILABLE",
    );
    assert_failed(&reopened.send(&note_write(DECISION_NOTE_SAVE, &note_id, 5, json!({
        "decided_on":"2026-01-03","thesis":"another version","evidence_refs":[],"choice":"wait",
    }))), "STALE_CONTEXT");
    let replay = reopened.send(&with_new_correlation(remove));
    assert_eq!(
        note_receipt(&replay, DECISION_NOTE_DELETE, &note_id, 5),
        delete_id
    );
    reopened.finish();
    assert_profile_absent(&first, &[THESIS, CHOICE, LOCATOR, LATER, DECLINED]);
    let rows = first.sqlite();
    let audit: (String, String, i64, i64) = rows.query_row(
        "SELECT log.id, marker.profile_id, marker.revision, marker.cleanup_pending
         FROM decision_note_deletions AS marker JOIN operation_log AS log
           ON log.workspace_id = marker.workspace_id AND log.revision = marker.revision
         WHERE marker.workspace_id = ?1 AND marker.note_id = ?2 AND log.decision_note_id = marker.note_id",
        rusqlite::params![WORKSPACE_ID, note_id],
        |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
    ).unwrap();
    assert_eq!(audit, (delete_id.into(), PROFILE_ID.into(), 5, 0));
    let receipts: Vec<(String, i64)> = rows.prepare(
        "SELECT log.id, replay.digest = zeroblob(32)
         FROM operation_log AS log JOIN idempotency_records AS replay ON replay.operation_id = log.id
         WHERE log.decision_note_id = ?1 ORDER BY log.revision"
    ).unwrap().query_map([note_id], |row| Ok((row.get(0)?, row.get(1)?)))
        .unwrap().collect::<rusqlite::Result<_>>().unwrap();
    assert_eq!(
        receipts,
        vec![
            (save_id.into(), 1),
            (outcome_id.into(), 1),
            (delete_id.into(), 0)
        ]
    );
}

#[test]
fn decision_note_atomic_failure_stale_confirmation_and_current_grants() {
    let first = Profile::new();
    let note_id = Uuid::new_v4().to_string();
    let body = json!({
        "decided_on":"2026-03-02","thesis":"synthetic atomic thesis 1e667efb",
        "evidence_refs":[],"choice":"observe",
    });
    let owner = WorkspaceAccess {
        operations: LOCAL_WORKSPACE_OPERATIONS,
        ..access()
    };
    let mut registry = OperationRegistry::new(store(&first.0));
    let request = note_write(DECISION_NOTE_SAVE, &note_id, 0, body.clone());
    let preview = registry.execute_workspace(&owner, request.clone()).unwrap();
    let approved = confirm(&request, &preview, "approve");
    let mut second = OperationRegistry::new(store(&first.0));
    let wrong_session = second.execute_workspace(&owner, approved.clone()).unwrap();
    assert_failed(&wrong_session, "CONFIRMATION_REQUIRED");
    assert_committed(
        &second
            .execute_workspace(&owner, set(0, "5m", Uuid::new_v4()))
            .unwrap(),
        1,
        "5m",
    );
    let stale = registry.execute_workspace(&owner, approved).unwrap();
    assert_failed(&stale, "STALE_CONTEXT");

    let save = note_write(DECISION_NOTE_SAVE, &note_id, 1, body);
    let preview = registry.execute_workspace(&owner, save.clone()).unwrap();
    let approved = confirm(&save, &preview, "approve");
    let sql = first.sqlite();
    sql.execute_batch(
        "CREATE TRIGGER reject_synthetic_note AFTER INSERT ON decision_notes
         BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END;",
    )
    .unwrap();
    let failed = registry
        .execute_workspace(&owner, approved.clone())
        .unwrap();
    assert_failed(&failed, "INTERNAL_ERROR");
    assert_eq!(
        registry
            .workspace_snapshot(&owner)
            .unwrap()
            .workspace
            .revision,
        1
    );
    let absent = registry
        .execute_workspace(&owner, note_read(Some(&note_id)))
        .unwrap();
    assert_failed(&absent, "DATA_UNAVAILABLE");
    assert_profile_absent(&first, &["synthetic atomic thesis 1e667efb"]);
    sql.execute_batch("DROP TRIGGER reject_synthetic_note")
        .unwrap();
    let committed = registry.execute_workspace(&owner, approved).unwrap();
    let stable_id = note_receipt(&committed, DECISION_NOTE_SAVE, &note_id, 2);

    let replay_only = WorkspaceAccess {
        operations: &[DECISION_NOTE_READ],
        ..owner
    };
    let revoked = WorkspaceAccess {
        operations: &[],
        ..owner
    };
    let model = WorkspaceAccess {
        actor: Actor::Ai,
        ..owner
    };
    let paired = WorkspaceAccess {
        paired_client_id: Some("paired-client"),
        ..owner
    };
    for denied in [&revoked, &model, &paired] {
        for request in [save.clone(), note_read(Some(&note_id))] {
            let reply = registry.execute_workspace(denied, request).unwrap();
            assert_failed(&reply, "PERMISSION_DENIED");
        }
    }
    let retry = with_new_correlation(save);
    let retry_again = with_new_correlation(retry.clone());
    let (a, b) = thread::scope(|scope| {
        let jobs = [(&mut registry, retry), (&mut second, retry_again)].map(|(client, request)| {
            let access = &replay_only;
            scope.spawn(move || client.execute_workspace(access, request).unwrap())
        });
        let [left, right] = jobs;
        (left.join().unwrap(), right.join().unwrap())
    });
    assert_eq!(note_receipt(&a, DECISION_NOTE_SAVE, &note_id, 2), stable_id);
    assert_eq!(note_receipt(&b, DECISION_NOTE_SAVE, &note_id, 2), stable_id);
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

    let child = Command::new(std::env::current_exe().unwrap())
        .args(["--exact", "interrupted_writer_child", "--ignored"])
        .env(INTERRUPT_CHILD_ROOT, &profile.0)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .spawn()
        .unwrap();
    kill_staged_writer(child, &profile.0.join("staged"));

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
        transactions_before_interrupt: 0,
    });
    let result = registry.execute_workspace(&access(), set(1, "1d", INTERRUPT_KEY));
    panic!("writer returned before interruption: {result:?}");
}

fn kill_staged_writer(mut child: Child, marker: &std::path::Path) {
    let deadline = Instant::now() + CHILD_DEADLINE;
    while !marker.is_file() {
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
}

struct InterruptingStore {
    store: scopehub_persistence::SqliteWorkspace,
    marker: PathBuf,
    transactions_before_interrupt: u8,
}

impl WorkspaceStore for InterruptingStore {
    fn binding(&self) -> &scopehub_application::WorkspaceBinding {
        self.store.binding()
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
            scopehub_application::WorkspaceLayoutSnapshot,
            Option<scopehub_application::WorkspaceSourceView>,
        ),
        Error,
    > {
        self.store.snapshot(undo, disclose_chart_evidence)
    }

    fn inspect<T>(
        &self,
        read: impl FnOnce(&dyn WorkspaceTransaction) -> Result<T, Error>,
    ) -> Result<T, Error> {
        self.store.inspect(read)
    }

    fn read_notes(
        &self,
        note_id: Option<&str>,
        offset: u64,
    ) -> Result<DecisionNoteReadOutput, Error> {
        self.store.read_notes(note_id, offset)
    }

    fn clean_deleted_note(&mut self) -> Result<(), Error> {
        self.store.clean_deleted_note()
    }

    fn clean_private_record_pages(&mut self) -> Result<(), Error> {
        self.store.clean_private_record_pages()
    }

    fn decision_note_backup(&self) -> Result<DecisionNoteBackupData, Error> {
        self.store.decision_note_backup()
    }

    fn transact<T>(
        &mut self,
        work: impl FnOnce(&mut dyn WorkspaceTransaction) -> Result<T, Error>,
    ) -> Result<T, Error> {
        if self.transactions_before_interrupt > 0 {
            self.transactions_before_interrupt -= 1;
            return self.store.transact(work);
        }
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

struct LiveBridge {
    child: Child,
    input: Option<ChildStdin>,
    replies: Receiver<Value>,
}

impl LiveBridge {
    fn open(profile: &Profile, profile_id: &str) -> Self {
        let mut child = spawn_bridge(profile, profile_id);
        let input = child.stdin.take();
        let stdout = child.stdout.take().unwrap();
        let (sender, replies) = mpsc::channel();
        thread::spawn(move || {
            for line in BufReader::new(stdout).lines() {
                let frame = serde_json::from_str(&line.unwrap()).unwrap();
                if sender.send(frame).is_err() {
                    break;
                }
            }
        });
        Self {
            child,
            input,
            replies,
        }
    }

    fn reply(&self) -> Value {
        let frame = self.replies.recv_timeout(CHILD_DEADLINE).unwrap();
        assert_eq!(frame["ok"], true, "{frame}");
        frame["result"].clone()
    }

    fn dispatch(&mut self, request: &Value) {
        writeln!(self.input.as_mut().unwrap(), "{request}").unwrap();
    }

    fn send(&mut self, request: &Value) -> Value {
        self.dispatch(request);
        self.reply()
    }

    fn finish(mut self) {
        self.input.take();
        wait_for_exit(&mut self.child);
    }
}

impl Drop for LiveBridge {
    fn drop(&mut self) {
        if self.child.try_wait().is_ok_and(|status| status.is_none()) {
            let _ = self.child.kill();
            let _ = self.child.wait();
        }
    }
}

fn note_request(operation: &str, scope: Value, arguments: Value) -> Value {
    with_new_correlation(json!({
        "protocol_version": "1.1", "type": "request", "timestamp_ms": 1234,
        "scope": scope, "intent": {"operation": operation, "arguments": arguments},
    }))
}

fn note_read(note_id: Option<&str>) -> Value {
    note_request(
        DECISION_NOTE_READ,
        json!({"workspace_id": WORKSPACE_ID}),
        json!({"note_id": note_id, "offset": 0}),
    )
}

fn note_write(operation: &str, note_id: &str, revision: u64, arguments: Value) -> Value {
    let mut request = note_request(
        operation,
        json!({
            "workspace_id": WORKSPACE_ID,
            "target": {"kind":"decision_note", "object_id":note_id},
            "expected_workspace_revision": revision,
            "idempotency_key": Uuid::new_v4().to_string(),
        }),
        arguments,
    );
    request["confirmation"] = Value::Null;
    request
}

fn with_new_correlation(mut request: Value) -> Value {
    request["request_id"] = json!(Uuid::new_v4().to_string());
    request["message_id"] = json!(Uuid::new_v4().to_string());
    request
}

fn confirm(request: &Value, preview: &Value, decision: &str) -> Value {
    assert_eq!(
        preview["outcome"]["state"], "confirmation_required",
        "{preview}"
    );
    let mut confirmation = with_new_correlation(request.clone());
    confirmation["confirmation"] = json!({
        "challenge_id": preview["outcome"]["challenge_id"], "decision": decision,
    });
    confirmation
}

fn note_result(reply: &Value) -> &Value {
    assert_eq!(reply["outcome"]["state"], "completed", "{reply}");
    &reply["outcome"]["result"]
}

fn note_receipt<'a>(reply: &'a Value, operation: &str, note_id: &str, revision: u64) -> &'a str {
    let outcome = &reply["outcome"];
    assert_eq!(outcome["state"], "committed", "{reply}");
    let id = outcome["receipt"]["operation_id"].as_str().unwrap();
    assert_eq!(
        *outcome,
        json!({"state":"committed", "operation": operation, "receipt": {
            "operation_id": id, "workspace_id": WORKSPACE_ID,
            "target": {"kind":"decision_note", "object_id":note_id},
            "committed_workspace_revision": revision, "undoable": false,
        }})
    );
    id
}

fn assert_profile_absent(profile: &Profile, phrases: &[&str]) {
    for name in [
        "workspace.sqlite3",
        "workspace.sqlite3-wal",
        "workspace.sqlite3-shm",
        "workspace.sqlite3-journal",
    ] {
        let bytes = match fs::read(profile.0.join(name)) {
            Ok(bytes) => bytes,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => continue,
            Err(error) => panic!("cannot inspect live SQLite file {name}: {error}"),
        };
        for phrase in phrases {
            assert!(
                !bytes
                    .windows(phrase.len())
                    .any(|window| window == phrase.as_bytes()),
                "synthetic note text remains in {name}"
            );
        }
    }
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
    wait_for_exit(&mut child);
    reader
        .join()
        .unwrap()
        .lines()
        .map(|line| serde_json::from_str(line).unwrap())
        .collect()
}

fn wait_for_exit(child: &mut Child) {
    let deadline = Instant::now() + CHILD_DEADLINE;
    loop {
        if let Some(status) = child.try_wait().unwrap() {
            assert!(status.success(), "workspace bridge exited {status}");
            return;
        }
        if Instant::now() >= deadline {
            child.kill().unwrap();
            child.wait().unwrap();
            panic!("workspace bridge exceeded the process deadline");
        }
        thread::sleep(Duration::from_millis(10));
    }
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
    assert_snapshot_at_selection(snapshot, revision, 0, timeframe);
}

fn assert_snapshot_at_selection(snapshot: &Value, revision: u64, selection: u64, timeframe: &str) {
    assert_eq!(snapshot["selection_revision"], selection);
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
