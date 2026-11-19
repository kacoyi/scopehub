use scopehub_application::{
    DECISION_NOTE_EXPORT_BACKUP, DECISION_NOTE_PREVIEW_RESTORE, DECISION_NOTE_RESTORE_BACKUP,
};

use super::*;

const INTERRUPTED_RESTORE_ROOT: &str = "SCOPEHUB_TEST_INTERRUPTED_NOTE_RESTORE_ROOT";
const RESTORE_STAGED: &str = "restore-staged";

#[test]
fn decision_note_restore_snapshot_discards_stage_after_peer_deletes_note() {
    let source = Profile::new();
    let current = Profile::new();
    let note_id = Uuid::new_v4().to_string();
    let original = thesis("Original shared by synthetic copies");
    let mut archive_writer = LiveBridge::open(&source, PROFILE_ID);
    archive_writer.reply();
    write_note(
        &mut archive_writer,
        DECISION_NOTE_SAVE,
        &note_id,
        0,
        original.clone(),
    );
    write_note(
        &mut archive_writer,
        DECISION_NOTE_APPEND_OUTCOME,
        &note_id,
        1,
        later_outcome("2026-04-09", "Synthetic backup-only stage body"),
    );
    let backup = export(&mut archive_writer);
    archive_writer.finish();

    let mut staging = LiveBridge::open(&current, PROFILE_ID);
    staging.reply();
    write_note(&mut staging, DECISION_NOTE_SAVE, &note_id, 0, original);
    let request = restore_preview_request(&backup, 1);
    let preview = staging.send(&request);
    assert_eq!(summary(&preview)["added_notes"], 0);
    assert_eq!(summary(&preview)["added_outcomes"], 1);
    let mut peer = LiveBridge::open(&current, PROFILE_ID);
    assert_snapshot(&peer.reply(), 1, "1m");
    write_note(&mut peer, DECISION_NOTE_DELETE, &note_id, 1, json!({}));
    peer.finish();
    let snapshot = staging.send(&json!({"type":"snapshot","request_id":1}));
    assert_snapshot(&snapshot, 2, "1m");
    for decision in ["decline", "approve"] {
        assert_failed(
            &staging.send(&resolve_restore(&request, &preview, decision)),
            "CONFIRMATION_REQUIRED",
        );
    }
    assert_failed(
        &staging.send(&note_read(Some(&note_id))),
        "DATA_UNAVAILABLE",
    );
    staging.finish();
}

#[test]
fn decision_note_restore_refuses_an_unexportable_merge_before_approval() {
    let first = Profile::new();
    let second = Profile::new();
    let note_id = Uuid::new_v4().to_string();
    let original = thesis("Identical synthetic original in two archives");
    let mut live = LiveBridge::open(&first, PROFILE_ID);
    let mut copy = LiveBridge::open(&second, PROFILE_ID);
    live.reply();
    copy.reply();
    for (bridge, count, label) in [(&mut live, 64, "current"), (&mut copy, 65, "incoming")] {
        write_note(bridge, DECISION_NOTE_SAVE, &note_id, 0, original.clone());
        for index in 0..count {
            write_note(
                bridge,
                DECISION_NOTE_APPEND_OUTCOME,
                &note_id,
                index + 1,
                later_outcome("2026-04-09", &format!("Synthetic {label} outcome {index}")),
            );
        }
    }
    let before = export(&mut live);
    let incoming = export(&mut copy);
    assert_eq!(before["notes"][0]["outcomes"].as_array().unwrap().len(), 64);
    assert_eq!(
        incoming["notes"][0]["outcomes"].as_array().unwrap().len(),
        65
    );
    assert_failed(
        &live.send(&restore_preview_request(&incoming, 65)),
        "RESOURCE_LIMIT",
    );
    assert_eq!(export(&mut live), before);
    assert_eq!(export(&mut copy), incoming);

    // A valid 128-outcome merge remains exportable, and the next append is blocked in preview.
    let mut fitting = incoming;
    fitting["notes"][0]["outcomes"]
        .as_array_mut()
        .unwrap()
        .truncate(64);
    let request = restore_preview_request(&fitting, 65);
    let preview = live.send(&request);
    assert_eq!(summary(&preview)["added_outcomes"], 64);
    assert_restore_committed(
        &live.send(&resolve_restore(&request, &preview, "approve")),
        66,
    );
    let full = export(&mut live);
    let expected = before["notes"][0]["outcomes"]
        .as_array()
        .unwrap()
        .iter()
        .chain(fitting["notes"][0]["outcomes"].as_array().unwrap())
        .cloned()
        .collect::<Vec<_>>();
    assert_eq!(full["notes"][0]["outcomes"], json!(expected));
    let over = note_write(
        DECISION_NOTE_APPEND_OUTCOME,
        &note_id,
        66,
        later_outcome("2026-04-10", "Synthetic above capacity"),
    );
    assert_failed(&live.send(&over), "RESOURCE_LIMIT");
    assert_eq!(export(&mut live), full);
    live.finish();
    copy.finish();
}

#[test]
fn decision_note_restore_merges_deletions_and_rolls_back_an_interrupted_apply() {
    const RETAINED: &str = "Synthetic thesis retained through merge 118b2aad";
    const DELETED: &str = "Synthetic thesis suppressed on restore 2a238c77";
    const DELETED_OUTCOME: &str = "Synthetic outcome suppressed on restore 54263aba";
    let source = Profile::new();
    let current = Profile::new();
    let unrelated = Profile::new();
    let kept_id = Uuid::new_v4().to_string();
    let deleted_id = Uuid::new_v4().to_string();
    let late_id = Uuid::new_v4().to_string();

    let mut source_bridge = LiveBridge::open(&source, PROFILE_ID);
    assert_snapshot(&source_bridge.reply(), 0, "1m");
    write_note(
        &mut source_bridge,
        DECISION_NOTE_SAVE,
        &kept_id,
        0,
        thesis(RETAINED),
    );
    write_note(
        &mut source_bridge,
        DECISION_NOTE_APPEND_OUTCOME,
        &kept_id,
        1,
        later_outcome("2026-02-09", "First retained outcome"),
    );
    write_note(
        &mut source_bridge,
        DECISION_NOTE_APPEND_OUTCOME,
        &kept_id,
        2,
        later_outcome("2026-03-01", "Second retained outcome"),
    );
    write_note(
        &mut source_bridge,
        DECISION_NOTE_SAVE,
        &deleted_id,
        3,
        thesis(DELETED),
    );
    write_note(
        &mut source_bridge,
        DECISION_NOTE_APPEND_OUTCOME,
        &deleted_id,
        4,
        later_outcome("2026-02-10", DELETED_OUTCOME),
    );
    let old = export(&mut source_bridge);
    assert_eq!(old["captured_workspace_revision"], 5);
    assert_eq!(old["notes"].as_array().unwrap().len(), 2);
    assert_eq!(old["deletions"], json!([]));
    assert_eq!(old.as_object().unwrap().len(), 6);
    write_note(
        &mut source_bridge,
        DECISION_NOTE_DELETE,
        &deleted_id,
        5,
        json!({}),
    );
    let newer = export(&mut source_bridge);
    assert_eq!(newer["deletions"][0]["note_id"], deleted_id);
    assert_eq!(newer["notes"].as_array().unwrap().len(), 1);
    assert!(!newer.to_string().contains(DELETED));
    assert!(!newer.to_string().contains(DELETED_OUTCOME));
    let retained = newer["notes"][0].clone();
    source_bridge.finish();

    let backup_path = source.0.join("independent-old-backup.json");
    let old_bytes = serde_json::to_vec(&old).unwrap();
    fs::write(&backup_path, &old_bytes).unwrap();
    let old: Value = serde_json::from_slice(&fs::read(&backup_path).unwrap()).unwrap();

    let mut foreign = LiveBridge::open(&unrelated, "unrelated-backup-profile");
    assert_snapshot(&foreign.reply(), 0, "1m");
    let denied = foreign.send(&restore_preview_request(&old, 0));
    assert_failed(&denied, "PERMISSION_DENIED");
    assert_eq!(
        note_result(&foreign.send(&note_read(None)))["notes"],
        json!([])
    );
    foreign.finish();

    let mut target = LiveBridge::open(&current, PROFILE_ID);
    assert_snapshot(&target.reply(), 0, "1m");
    let chart = target.send(&set(0, "5m", Uuid::new_v4()));
    let chart_id = assert_committed(&chart, 1, "5m");
    write_note(
        &mut target,
        DECISION_NOTE_SAVE,
        &late_id,
        1,
        thesis("Unrelated current decision"),
    );
    let load_old = restore_preview_request(&old, 2);
    let preview = target.send(&load_old);
    assert_eq!(
        *summary(&preview),
        json!({
            "added_notes":2, "added_outcomes":3, "removed_notes":0, "suppressed_notes":0,
            "imported_deletions":0, "independent_copy_warning":true,
        })
    );
    assert_failed(
        &target.send(&note_read(Some(&deleted_id))),
        "DATA_UNAVAILABLE",
    );
    let declined = resolve_restore(&load_old, &preview, "decline");
    assert_failed(&target.send(&declined), "CANCELLED");
    assert_failed(
        &target.send(&resolve_restore(&load_old, &preview, "approve")),
        "CONFIRMATION_REQUIRED",
    );
    let preview = target.send(&load_old);
    let receipt = target.send(&resolve_restore(&load_old, &preview, "approve"));
    assert_restore_committed(&receipt, 3);
    assert_eq!(
        note_result(&target.send(&note_read(Some(&deleted_id))))["note"]["thesis"],
        DELETED
    );
    assert_retained(&mut target, &kept_id, &late_id, &retained);

    let mut duplicate = old.clone();
    let entries = duplicate["notes"].as_array_mut().unwrap();
    entries.push(entries[0].clone());
    assert_failed(
        &target.send(&restore_preview_request(&duplicate, 3)),
        "INVALID_ARGUMENT",
    );
    let mut conflict = old.clone();
    let changed = conflict["notes"]
        .as_array_mut()
        .unwrap()
        .iter_mut()
        .find(|entry| entry["original"]["note_id"] == kept_id)
        .unwrap();
    changed["original"]["thesis"] = json!("Conflicting synthetic original");
    assert_failed(
        &target.send(&restore_preview_request(&conflict, 3)),
        "STALE_CONTEXT",
    );
    assert_retained(&mut target, &kept_id, &late_id, &retained);
    let apply_marker = restore_preview_request(&newer, 3);
    let preview = target.send(&apply_marker);
    assert_eq!(
        *summary(&preview),
        json!({
            "added_notes":0, "added_outcomes":0, "removed_notes":1, "suppressed_notes":0,
            "imported_deletions":1, "independent_copy_warning":false,
        })
    );
    assert_eq!(
        note_result(&target.send(&note_read(Some(&deleted_id))))["outcomes"][0]["outcome"],
        DELETED_OUTCOME
    );
    assert_failed(
        &target.send(&resolve_restore(&apply_marker, &preview, "decline")),
        "CANCELLED",
    );
    target.finish();

    let mut child = Command::new(std::env::current_exe().unwrap())
        .args([
            "--exact",
            "decision_note_restore::interrupted_restore_child",
            "--ignored",
        ])
        .env(INTERRUPTED_RESTORE_ROOT, &current.0)
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .spawn()
        .unwrap();
    serde_json::to_writer(child.stdin.take().unwrap(), &apply_marker).unwrap();
    kill_staged_writer(child, &current.0.join(RESTORE_STAGED));

    let mut recovered = LiveBridge::open(&current, PROFILE_ID);
    let snapshot = recovered.reply();
    assert_snapshot(&snapshot, 3, "5m");
    assert_eq!(snapshot["undo"]["operation_id"], chart_id);
    assert_eq!(export(&mut recovered)["deletions"], json!([]));
    assert_eq!(
        note_result(&recovered.send(&note_read(Some(&deleted_id))))["note"]["thesis"],
        DELETED
    );
    assert_retained(&mut recovered, &kept_id, &late_id, &retained);

    let preview = recovered.send(&apply_marker);
    let apply = resolve_restore(&apply_marker, &preview, "approve");
    let restored = recovered.send(&apply);
    assert_restore_committed(&restored, 4);
    let recover_old = restore_preview_request(&old, 4);
    let preview = recovered.send(&recover_old);
    assert_eq!(
        *summary(&preview),
        json!({
            "added_notes":0, "added_outcomes":0, "removed_notes":0, "suppressed_notes":1,
            "imported_deletions":0, "independent_copy_warning":false,
        })
    );
    assert_restore_committed(
        &recovered.send(&resolve_restore(&recover_old, &preview, "approve")),
        5,
    );
    assert_failed(
        &recovered.send(&note_read(Some(&deleted_id))),
        "DATA_UNAVAILABLE",
    );
    assert_profile_absent(&current, &[DELETED, DELETED_OUTCOME]);
    recovered.finish();

    let mut final_open = LiveBridge::open(&current, PROFILE_ID);
    let snapshot = final_open.reply();
    assert_snapshot(&snapshot, 5, "5m");
    assert_eq!(snapshot["undo"]["operation_id"], chart_id);
    let mut changed_decision = with_new_correlation(apply.clone());
    changed_decision["intent"]["arguments"]["decision"] = json!("decline");
    assert_failed(&final_open.send(&changed_decision), "INVALID_ARGUMENT");
    let replay = final_open.send(&with_new_correlation(apply));
    assert_eq!(replay["outcome"], restored["outcome"]);
    assert_failed(
        &final_open.send(&note_read(Some(&deleted_id))),
        "DATA_UNAVAILABLE",
    );
    assert_retained(&mut final_open, &kept_id, &late_id, &retained);
    let released = export(&mut final_open);
    assert_eq!(released["deletions"], newer["deletions"]);
    assert!(!released.to_string().contains(DELETED));
    assert!(!released.to_string().contains(DELETED_OUTCOME));

    let stale = restore_preview_request(&old, 5);
    let preview = final_open.send(&stale);
    assert_eq!(summary(&preview)["suppressed_notes"], 1);
    let mut other_writer = LiveBridge::open(&current, PROFILE_ID);
    assert_snapshot(&other_writer.reply(), 5, "5m");
    let changed_chart = other_writer.send(&set(5, "1d", Uuid::new_v4()));
    assert_committed(&changed_chart, 6, "1d");
    other_writer.finish();
    assert_failed(
        &final_open.send(&resolve_restore(&stale, &preview, "approve")),
        "STALE_CONTEXT",
    );
    assert_retained(&mut final_open, &kept_id, &late_id, &retained);
    final_open.finish();

    assert_eq!(fs::read(&backup_path).unwrap(), old_bytes);
    assert_profile_absent(&current, &[DELETED, DELETED_OUTCOME]);
    let audit: (i64, i64) = current
        .sqlite()
        .query_row(
            "SELECT count(*), count(decision_note_id) FROM operation_log WHERE operation = ?1",
            [DECISION_NOTE_RESTORE_BACKUP],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .unwrap();
    assert_eq!(audit, (3, 0));
}

#[test]
#[ignore = "subprocess fixture for interrupted decision-note restore before commit"]
fn interrupted_restore_child() {
    let Some(root) = std::env::var_os(INTERRUPTED_RESTORE_ROOT).map(PathBuf::from) else {
        return;
    };
    let request: Value = serde_json::from_reader(std::io::stdin().lock()).unwrap();
    let mut registry = OperationRegistry::new(InterruptingStore {
        store: store(&root),
        marker: root.join(RESTORE_STAGED),
        transactions_before_interrupt: 1,
    });
    let owner = WorkspaceAccess {
        operations: LOCAL_WORKSPACE_OPERATIONS,
        ..access()
    };
    let preview = registry.execute_workspace(&owner, request.clone()).unwrap();
    assert_eq!(summary(&preview)["removed_notes"], 1);
    let result = registry.execute_workspace(&owner, resolve_restore(&request, &preview, "approve"));
    panic!("restore writer returned before interruption: {result:?}");
}

fn thesis(text: &str) -> Value {
    json!({"decided_on":"2026-02-01", "thesis":text, "choice":"observe", "evidence_refs":[{
        "source_id":"synthetic-series", "source_revision":"rev-a", "locator":"row-2",
    }]})
}

fn later_outcome(date: &str, body: &str) -> Value {
    json!({"observed_on":date,"outcome":body})
}

fn write_note(
    bridge: &mut LiveBridge,
    operation: &str,
    note_id: &str,
    revision: u64,
    arguments: Value,
) {
    let command = note_write(operation, note_id, revision, arguments);
    let preview = bridge.send(&command);
    let receipt = bridge.send(&confirm(&command, &preview, "approve"));
    note_receipt(&receipt, operation, note_id, revision + 1);
}

fn export(bridge: &mut LiveBridge) -> Value {
    let result = bridge.send(&note_request(
        DECISION_NOTE_EXPORT_BACKUP,
        json!({"workspace_id": WORKSPACE_ID}),
        json!({}),
    ));
    assert_eq!(result["outcome"]["operation"], DECISION_NOTE_EXPORT_BACKUP);
    note_result(&result).clone()
}

fn restore_preview_request(backup: &Value, revision: u64) -> Value {
    note_request(
        DECISION_NOTE_PREVIEW_RESTORE,
        json!({
            "workspace_id":WORKSPACE_ID, "expected_workspace_revision": revision,
            "idempotency_key":Uuid::new_v4().to_string(),
        }),
        json!({"backup":backup}),
    )
}

fn summary(reply: &Value) -> &Value {
    assert_eq!(
        reply["outcome"]["state"], "confirmation_required",
        "{reply}"
    );
    assert_eq!(reply["outcome"]["operation"], DECISION_NOTE_PREVIEW_RESTORE);
    assert_eq!(reply["outcome"].as_object().unwrap().len(), 5);
    &reply["outcome"]["summary"]
}

fn resolve_restore(request: &Value, preview: &Value, decision: &str) -> Value {
    note_request(
        DECISION_NOTE_RESTORE_BACKUP,
        request["scope"].clone(),
        json!({"stage_id":preview["outcome"]["stage_id"],"decision":decision}),
    )
}

fn assert_restore_committed(reply: &Value, revision: u64) {
    let outcome = &reply["outcome"];
    let id = outcome["receipt"]["operation_id"].as_str().unwrap();
    assert!(Uuid::parse_str(id).is_ok());
    assert_eq!(
        *outcome,
        json!({"state":"committed", "operation":DECISION_NOTE_RESTORE_BACKUP, "receipt":{
            "operation_id":id, "workspace_id":WORKSPACE_ID,
            "target":{"kind":"workspace","object_id":WORKSPACE_ID},
            "committed_workspace_revision":revision, "undoable":false,
        }})
    );
}

fn assert_retained(bridge: &mut LiveBridge, retained_id: &str, newer_id: &str, retained: &Value) {
    let reply = bridge.send(&note_read(Some(retained_id)));
    let note = note_result(&reply);
    assert_eq!(note["note"], retained["original"]);
    assert_eq!(note["outcomes"], retained["outcomes"]);
    let newer = bridge.send(&note_read(Some(newer_id)));
    assert_eq!(
        note_result(&newer)["note"]["thesis"],
        "Unrelated current decision"
    );
}
