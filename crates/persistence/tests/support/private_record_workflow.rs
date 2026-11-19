use scopehub_application::{
    PRIVATE_RECORD_CHANGE, PRIVATE_RECORD_EXPORT, PRIVATE_RECORD_OPERATIONS, PRIVATE_RECORD_READ,
};

use super::{
    Actor, LiveBridge, OperationRegistry, PROFILE_ID, Profile, Value, WORKSPACE_ID,
    WorkspaceAccess, assert_failed, json, note_request, store, with_new_correlation,
};
use uuid::Uuid;

fn read(id: Option<&str>) -> Value {
    note_request(
        PRIVATE_RECORD_READ,
        json!({"workspace_id": WORKSPACE_ID}),
        json!({"record_id": id}),
    )
}

fn change(revision: u64, arguments: Value) -> Value {
    note_request(
        PRIVATE_RECORD_CHANGE,
        json!({
            "workspace_id": WORKSPACE_ID, "expected_workspace_revision": revision,
            "idempotency_key": Uuid::new_v4().to_string(),
        }),
        arguments,
    )
}

fn approval(command: &Value, preview: &Value) -> Value {
    assert_eq!(preview["outcome"]["state"], "confirmation_required");
    note_request(
        PRIVATE_RECORD_CHANGE,
        command["scope"].clone(),
        json!({
            "action": "resolve", "challenge_id": preview["outcome"]["challenge_id"], "decision": "approve",
        }),
    )
}

fn archive(bridge: &mut LiveBridge) -> Value {
    let reply = bridge.send(&note_request(
        PRIVATE_RECORD_EXPORT,
        json!({"workspace_id": WORKSPACE_ID}),
        json!({}),
    ));
    assert_eq!(reply["outcome"]["state"], "completed");
    reply["outcome"]["result"].clone()
}

fn commit(
    bridge: &mut LiveBridge,
    command: &Value,
    revision: u64,
    record_id: Option<&str>,
) -> Value {
    let preview = bridge.send(command);
    let reply = bridge.send(&approval(command, &preview));
    assert_eq!(reply["outcome"]["state"], "committed", "{reply}");
    assert_eq!(reply["outcome"].as_object().unwrap().len(), 3);
    let receipt = &reply["outcome"]["receipt"];
    assert_eq!(receipt["record_id"], json!(record_id));
    assert_eq!(receipt["committed_workspace_revision"], revision);
    assert_eq!(receipt["undoable"], false);
    assert_eq!(receipt.as_object().unwrap().len(), 5);
    receipt.clone()
}

fn memory(record_id: &str, expected: Option<u64>, text: &str) -> Value {
    json!({
        "action": "save", "record_id": record_id, "expected_record_revision": expected,
        "content": {"kind": "memory", "title": "Synthetic memory", "text": text},
    })
}

#[test]
fn private_record_restore_recovers_missing_and_unions_markers_without_overwriting_current_content()
{
    const A: &str = "10000000-aaaa-4aaa-aaaa-000000000001";
    const B: &str = "10000000-aaaa-4aaa-aaaa-000000000002";
    const C: &str = "10000000-aaaa-4aaa-aaaa-000000000003";
    const OLD: &str = "synthetic version before correction 06f00d4a";
    const NEW: &str = "synthetic version after correction 0bf31a9e";

    let original = Profile::new();
    let recovered = Profile::new();
    let mut source = LiveBridge::open(&original, PROFILE_ID);
    let mut dest = LiveBridge::open(&recovered, PROFILE_ID);
    source.reply();
    dest.reply();

    let first = change(0, memory(A, None, OLD));
    let prepared = source.send(&first);
    assert!(!prepared.to_string().contains(OLD));
    let accepted = approval(&first, &prepared);
    let result = source.send(&accepted)["outcome"].clone();
    assert_eq!(result["state"], "committed");
    assert!(!result.to_string().contains(OLD));
    assert_eq!(
        source.send(&with_new_correlation(accepted.clone()))["outcome"],
        result
    );
    assert_failed(&source.send(&first), "INVALID_ARGUMENT");
    let mut substituted = accepted.clone();
    substituted["intent"]["arguments"]["challenge_id"] = json!(Uuid::new_v4().to_string());
    assert_failed(&source.send(&substituted), "INVALID_ARGUMENT");
    commit(
        &mut source,
        &change(1, memory(B, None, "synthetic older deleted body")),
        2,
        Some(B),
    );
    let early = archive(&mut source);
    commit(
        &mut dest,
        &change(0, json!({"action":"restore", "backup":early.clone()})),
        1,
        None,
    );
    assert_eq!(
        dest.send(&read(Some(A)))["outcome"]["result"]["record"]["content"]["text"],
        OLD
    );
    assert_eq!(dest.send(&read(Some(B)))["outcome"]["state"], "completed");
    commit(&mut dest, &change(1, memory(A, Some(1), NEW)), 2, Some(A));

    commit(
        &mut source,
        &change(
            2,
            json!({
                "action":"delete", "record_id": B, "expected_record_revision": 2,
            }),
        ),
        3,
        Some(B),
    );
    commit(
        &mut source,
        &change(3, memory(C, None, "synthetic recoverable record")),
        4,
        Some(C),
    );
    let latest = archive(&mut source);
    let restore = change(2, json!({"action":"restore", "backup":latest}));
    let preview = dest.send(&restore);
    assert_eq!(
        preview["outcome"]["summary"],
        json!({
            "added":1, "corrected":0, "removed":1, "kept":1, "suppressed":0, "imported_deletions":1,
        })
    );
    let restore_approval = approval(&restore, &preview);
    let database = recovered.sqlite();
    database
        .execute_batch(
            "CREATE TRIGGER synthetic_reject_restore BEFORE INSERT ON private_records
         BEGIN SELECT raise(ABORT, 'synthetic restore rollback'); END;",
        )
        .unwrap();
    assert_failed(&dest.send(&restore_approval), "INTERNAL_ERROR");
    assert_eq!(dest.send(&read(Some(B)))["outcome"]["state"], "completed");
    assert_failed(&dest.send(&read(Some(C))), "DATA_UNAVAILABLE");
    assert_eq!(
        database
            .query_row("SELECT count(*) FROM private_record_deletions", [], |row| {
                row.get::<_, i64>(0)
            })
            .unwrap(),
        0
    );
    database
        .execute_batch("DROP TRIGGER synthetic_reject_restore")
        .unwrap();
    drop(database);
    let restored = dest.send(&with_new_correlation(restore_approval.clone()))["outcome"].clone();
    assert_eq!(restored["state"], "committed");
    assert_eq!(restored["receipt"]["committed_workspace_revision"], 3);
    assert_failed(&dest.send(&read(Some(B))), "DATA_UNAVAILABLE");
    assert_eq!(
        dest.send(&read(Some(A)))["outcome"]["result"]["record"]["content"]["text"],
        NEW
    );
    assert_eq!(dest.send(&read(Some(C)))["outcome"]["state"], "completed");

    source.finish();
    dest.finish();
    let mut dest = LiveBridge::open(&recovered, PROFILE_ID);
    assert_eq!(dest.reply()["workspace"]["revision"], 3);
    assert_eq!(
        dest.send(&with_new_correlation(restore_approval))["outcome"],
        restored
    );
    let older = change(3, json!({"action":"restore", "backup":early}));
    let preview = dest.send(&older);
    assert_eq!(preview["outcome"]["summary"]["suppressed"], 1);
    assert_eq!(
        dest.send(&approval(&older, &preview))["outcome"]["state"],
        "committed"
    );
    assert_failed(&dest.send(&read(Some(B))), "DATA_UNAVAILABLE");
    assert_failed(
        &dest.send(&change(4, memory(B, None, "synthetic reused ID"))),
        "STALE_CONTEXT",
    );
    assert_failed(
        &dest.send(&change(4, memory(A, Some(1), "synthetic old correction"))),
        "STALE_CONTEXT",
    );

    for suffix in ["", "-wal", "-journal"] {
        let path = recovered.0.join(format!("workspace.sqlite3{suffix}"));
        if let Ok(bytes) = std::fs::read(path) {
            for obsolete in [OLD, "synthetic older deleted body"] {
                assert!(
                    !bytes
                        .windows(obsolete.len())
                        .any(|window| window == obsolete.as_bytes())
                );
            }
        }
    }
    dest.finish();
}

#[test]
fn private_record_authorization_requires_current_local_human_disclosure() {
    let profile = Profile::new();
    let mut registry = OperationRegistry::new(store(&profile.0));
    let mut access = WorkspaceAccess {
        actor: Actor::User,
        profile_id: PROFILE_ID,
        principal_id: PROFILE_ID,
        paired_client_id: None,
        workspace_id: WORKSPACE_ID,
        operations: &PRIVATE_RECORD_OPERATIONS,
        disclose_workspace_state: true,
    };
    let id = Uuid::new_v4().to_string();
    let save = change(0, memory(&id, None, "synthetic private only"));
    let prepared = registry.execute_workspace(&access, save.clone()).unwrap();
    let approve = approval(&save, &prepared);
    assert_eq!(
        registry
            .execute_workspace(&access, approve.clone())
            .unwrap()["outcome"]["state"],
        "committed"
    );
    access.actor = Actor::Ai;
    for request in [
        read(Some(&id)),
        approve.clone(),
        note_request(
            PRIVATE_RECORD_EXPORT,
            json!({"workspace_id":WORKSPACE_ID}),
            json!({}),
        ),
    ] {
        assert_failed(
            &registry.execute_workspace(&access, request).unwrap(),
            "PERMISSION_DENIED",
        );
    }
    access.actor = Actor::User;
    access.paired_client_id = Some("synthetic-peer");
    assert_failed(
        &registry
            .execute_workspace(&access, read(Some(&id)))
            .unwrap(),
        "PERMISSION_DENIED",
    );
    access.paired_client_id = None;
    access.profile_id = "synthetic-other-profile";
    assert_failed(
        &registry
            .execute_workspace(&access, read(Some(&id)))
            .unwrap(),
        "PERMISSION_DENIED",
    );
    access.profile_id = PROFILE_ID;
    access.disclose_workspace_state = false;
    assert_failed(
        &registry
            .execute_workspace(&access, approve.clone())
            .unwrap(),
        "PERMISSION_DENIED",
    );
    access.disclose_workspace_state = true;
    access.operations = &[PRIVATE_RECORD_READ];
    let replay = registry.execute_workspace(&access, approve).unwrap();
    assert_eq!(replay["outcome"]["state"], "committed");
    assert!(!replay.to_string().contains("synthetic private only"));
    assert_failed(
        &registry
            .execute_workspace(
                &access,
                change(1, memory(&id, Some(1), "synthetic unsupported write")),
            )
            .unwrap(),
        "PERMISSION_DENIED",
    );
}

#[test]
fn private_record_cleanup_survives_a_blocked_checkpoint_and_restarts_without_old_body() {
    const ID: &str = "10000000-aaaa-4aaa-aaaa-000000000012";
    const OBSOLETE: &str = "synthetic body in prior WAL frames 158c1917";
    let profile = Profile::new();
    let mut original = LiveBridge::open(&profile, PROFILE_ID);
    original.reply();
    commit(
        &mut original,
        &change(0, memory(ID, None, OBSOLETE)),
        1,
        Some(ID),
    );
    let snapshot = profile.sqlite();
    snapshot.execute_batch("BEGIN").unwrap();
    assert_eq!(
        snapshot
            .query_row("SELECT revision FROM private_records", [], |row| row
                .get::<_, i64>(0))
            .unwrap(),
        1,
    );

    let remove = change(
        1,
        json!({
            "action":"delete", "record_id":ID, "expected_record_revision":1,
        }),
    );
    let preview = original.send(&remove);
    let decision = approval(&remove, &preview);
    assert_failed(&original.send(&decision), "BUSY");
    let database = profile.sqlite();
    assert_eq!(
        database
            .query_row("SELECT revision FROM private_record_cleanup", [], |row| row
                .get::<_, i64>(0))
            .unwrap(),
        2
    );
    assert_eq!(
        database
            .query_row("SELECT count(*) FROM private_records", [], |row| row
                .get::<_, i64>(0))
            .unwrap(),
        0
    );
    drop(database);
    original.finish();
    drop(snapshot);

    let mut restored = LiveBridge::open(&profile, PROFILE_ID);
    assert_eq!(restored.reply()["workspace"]["revision"], 2);
    assert_failed(&restored.send(&read(Some(ID))), "DATA_UNAVAILABLE");
    let replay = restored.send(&with_new_correlation(decision));
    assert_eq!(
        replay["outcome"]["receipt"]["committed_workspace_revision"],
        2
    );
    assert!(!replay.to_string().contains(OBSOLETE));
    assert_eq!(
        profile
            .sqlite()
            .query_row("SELECT count(*) FROM private_record_cleanup", [], |row| row
                .get::<_, i64>(0))
            .unwrap(),
        0,
    );
    for suffix in ["", "-wal", "-journal"] {
        let path = profile.0.join(format!("workspace.sqlite3{suffix}"));
        if let Ok(bytes) = std::fs::read(path) {
            assert!(
                !bytes
                    .windows(OBSOLETE.len())
                    .any(|window| window == OBSOLETE.as_bytes())
            );
        }
    }
    restored.finish();
}

#[test]
fn private_record_backup_does_not_restore_approval_or_authorize_outbound_access() {
    const ID: &str = "10000000-aaaa-4aaa-aaaa-000000000021";
    const BODY: &str = "synthetic saved excerpt candidate 06c82c74";
    const DRAFT: &str = "synthetic unapproved correction 846cda75";
    let source = Profile::new();
    let recovered = Profile::new();
    let mut bridge = LiveBridge::open(&source, PROFILE_ID);
    bridge.reply();
    commit(&mut bridge, &change(0, memory(ID, None, BODY)), 1, Some(ID));
    let pending = change(1, memory(ID, Some(1), DRAFT));
    let preview = bridge.send(&pending);
    let old_approval = approval(&pending, &preview);
    let backup = archive(&mut bridge);
    assert_eq!(backup.as_object().unwrap().len(), 5);
    assert_eq!(backup["records"][0]["content"]["text"], BODY);
    assert!(!backup.to_string().contains(DRAFT));
    assert!(
        !backup
            .to_string()
            .contains(preview["outcome"]["challenge_id"].as_str().unwrap())
    );
    bridge.finish();

    let mut host = LiveBridge::open(&recovered, PROFILE_ID);
    host.reply();
    commit(
        &mut host,
        &change(0, json!({"action":"restore", "backup":backup.clone()})),
        1,
        None,
    );
    host.finish();

    let mut registry = OperationRegistry::new(store(&recovered.0));
    let mut local = WorkspaceAccess {
        actor: Actor::User,
        profile_id: PROFILE_ID,
        principal_id: PROFILE_ID,
        paired_client_id: None,
        workspace_id: WORKSPACE_ID,
        operations: &PRIVATE_RECORD_OPERATIONS,
        disclose_workspace_state: true,
    };
    let mut forged = backup;
    forged["excerpt_grant"] = json!({
        "recipient": "synthetic-model", "request_id": Uuid::new_v4().to_string(), "excerpt": BODY,
    });
    let error = registry
        .execute_workspace(
            &local,
            change(1, json!({"action":"restore", "backup":forged})),
        )
        .unwrap_err();
    assert_eq!(
        error.code,
        scopehub_domain::protocol::ErrorCode::INVALIDARGUMENT
    );

    // The original approval refers to the same logical profile and revision.
    // A backup must still carry neither that stage nor any disclosure right.
    assert_failed(
        &registry.execute_workspace(&local, old_approval).unwrap(),
        "CONFIRMATION_REQUIRED",
    );
    let export = note_request(
        PRIVATE_RECORD_EXPORT,
        json!({"workspace_id": WORKSPACE_ID}),
        json!({}),
    );
    local.actor = Actor::Ai;
    for request in [read(Some(ID)), export.clone()] {
        assert_failed(
            &registry.execute_workspace(&local, request).unwrap(),
            "PERMISSION_DENIED",
        );
    }
    local.actor = Actor::User;
    local.paired_client_id = Some("synthetic-external-client");
    for request in [read(Some(ID)), export] {
        assert_failed(
            &registry.execute_workspace(&local, request).unwrap(),
            "PERMISSION_DENIED",
        );
    }
    local.paired_client_id = None;
    assert_eq!(
        registry.execute_workspace(&local, read(Some(ID))).unwrap()["outcome"]["result"]["record"]
            ["content"]["text"],
        BODY,
    );
    assert_eq!(
        registry
            .workspace_snapshot(&local)
            .unwrap()
            .workspace
            .revision,
        1
    );
}
