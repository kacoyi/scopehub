#[allow(dead_code)]
mod support;

use scopehub_application::{Actor, OperationRegistry, RESEARCH_SAMPLE, WorkspaceAccess};
use serde_json::{Value, json};
use support::{Profile, access, store};
use uuid::Uuid;

#[test]
fn synthetic_research_tracks_current_authority_and_exact_original_across_sessions() {
    let profile = Profile::new();
    let user = WorkspaceAccess {
        operations: &[RESEARCH_SAMPLE],
        ..access()
    };
    let mut registry = OperationRegistry::new(store(&profile.0));
    let run = |registry: &mut OperationRegistry<_>, caller: &WorkspaceAccess<'_>, args: Value| {
        registry.execute_workspace(caller, json!({
            "protocol_version":"1.1","message_id":Uuid::new_v4().to_string(),"request_id":Uuid::new_v4().to_string(),
            "timestamp_ms":0,"type":"request","scope":{"workspace_id":user.workspace_id},
            "intent":{"operation":RESEARCH_SAMPLE,"arguments":args}
        })).unwrap()["outcome"].clone()
    };
    let read = json!({"action":"read","note_id":"note-a"});
    let connect = json!({"action":"connect","expected_revision":0});
    let original =
        json!({"action":"original","expected_revision":1,"note_id":"note-a","record_id":"a-0910"});
    for caller in [
        WorkspaceAccess {
            actor: Actor::Ai,
            ..user
        },
        WorkspaceAccess {
            paired_client_id: Some("external"),
            ..user
        },
        WorkspaceAccess {
            disclose_workspace_state: false,
            ..user
        },
        WorkspaceAccess {
            operations: &[],
            ..user
        },
        WorkspaceAccess {
            workspace_id: "wrong",
            ..user
        },
    ] {
        assert_eq!(
            run(&mut registry, &caller, connect.clone())["error"]["code"],
            "PERMISSION_DENIED"
        );
    }
    let empty = run(&mut registry, &user, read.clone());
    assert_eq!(
        empty["result"],
        json!({"access":"unconfigured","revision":0,"notes":[],"disclosures":[]})
    );
    assert_eq!(
        run(&mut registry, &user, connect.clone())["result"]["revision"],
        1
    );
    let result = run(&mut registry, &user, read.clone())["result"].clone();
    assert_eq!(result["note"]["issuer"], "A");
    assert_eq!(result["note"]["reviewed_at_ms"], 1_704_186_000_000_i64);
    let rows = result["disclosures"].as_array().unwrap();
    assert_eq!(rows.len(), 3);
    assert!(rows.iter().all(|row| row.get("body").is_none()));
    assert_eq!(rows[1]["issuer"], "B");
    assert_eq!(rows[2]["published_at_ms"], 1_704_186_600_000_i64);
    assert_eq!(
        run(&mut registry, &user, original.clone())["result"]["original"]["body"],
        "Synthetic issuer A disclosure. A local inventory recount recorded 125 units."
    );
    let not_yet =
        json!({"record_id":"a-0920","action":"original","expected_revision":1,"note_id":"note-a"});
    assert_eq!(
        run(&mut registry, &user, not_yet)["error"]["code"],
        "DATA_UNAVAILABLE"
    );
    assert_eq!(
        run(
            &mut registry,
            &user,
            json!({"action":"refresh","expected_revision":1})
        )["result"]["revision"],
        2
    );
    assert_eq!(
        run(&mut registry, &user, original.clone())["error"]["code"],
        "STALE_CONTEXT"
    );
    assert_eq!(
        run(&mut registry, &user, read.clone())["result"]["disclosures"][3]["id"],
        "a-0920"
    );

    let mut peer = OperationRegistry::new(store(&profile.0));
    assert_eq!(
        run(
            &mut peer,
            &user,
            json!({"action":"disconnect","expected_revision":2})
        )["result"]["access"],
        "disconnected"
    );
    let disconnected = run(&mut registry, &user, read.clone());
    assert_eq!(
        disconnected["result"],
        json!({"access":"disconnected","revision":3,"notes":[],"disclosures":[]})
    );
    assert_eq!(
        run(
            &mut peer,
            &user,
            json!({"action":"revoke","expected_revision":3})
        )["result"]["access"],
        "revoked"
    );
    drop(registry);
    let mut reopened = OperationRegistry::new(store(&profile.0));
    assert_eq!(
        run(&mut reopened, &user, original)["result"],
        json!({"access":"revoked","revision":4,"notes":[],"disclosures":[]})
    );
    assert_eq!(
        run(&mut reopened, &user, connect)["error"]["code"],
        "STALE_CONTEXT"
    );
}
