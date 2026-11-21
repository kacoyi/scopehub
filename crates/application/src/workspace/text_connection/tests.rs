use super::*;
use crate::{
    DecisionNoteBackupData, StoredWorkspaceView, WorkspaceBinding, WorkspaceTransaction,
    WorkspaceUndoAccess,
};
use scopehub_domain::protocol::DecisionNoteReadOutput;

const PROFILE: &str = "synthetic-model-profile";
const WORKSPACE: &str = "main";

struct BoundProfile(WorkspaceBinding);

// An ordinary text operation can read its trusted binding. Any attempt to
// inspect or change persisted records invalidates the exercise.
impl WorkspaceStore for BoundProfile {
    fn binding(&self) -> &WorkspaceBinding {
        &self.0
    }

    fn snapshot(
        &self,
        _: Option<WorkspaceUndoAccess<'_>>,
        _: bool,
    ) -> Result<StoredWorkspaceView, Error> {
        panic!("text must not open a workspace snapshot")
    }

    fn inspect<T>(
        &self,
        _: impl FnOnce(&dyn WorkspaceTransaction) -> Result<T, Error>,
    ) -> Result<T, Error> {
        panic!("text must not inspect persisted records")
    }

    fn read_notes(&self, _: Option<&str>, _: u64) -> Result<DecisionNoteReadOutput, Error> {
        panic!("text must not read saved notes")
    }

    fn decision_note_backup(&self) -> Result<DecisionNoteBackupData, Error> {
        panic!("text must not read backups")
    }

    fn clean_deleted_note(&mut self) -> Result<(), Error> {
        panic!("text must not mutate storage")
    }

    fn clean_private_record_pages(&mut self) -> Result<(), Error> {
        panic!("text must not mutate storage")
    }

    fn transact<T>(
        &mut self,
        _: impl FnOnce(&mut dyn WorkspaceTransaction) -> Result<T, Error>,
    ) -> Result<T, Error> {
        panic!("text must not open a storage transaction")
    }
}

fn registry() -> OperationRegistry<BoundProfile> {
    OperationRegistry::new(BoundProfile(WorkspaceBinding {
        profile_id: PROFILE.into(),
        workspace_id: WORKSPACE.into(),
    }))
}

fn access() -> WorkspaceAccess<'static> {
    WorkspaceAccess {
        actor: Actor::User,
        profile_id: PROFILE,
        principal_id: PROFILE,
        paired_client_id: None,
        workspace_id: WORKSPACE,
        operations: &[AI_TEXT_CONNECTION],
        disclose_workspace_state: false,
    }
}

fn command(arguments: Value) -> Value {
    json!({
        "protocol_version": "1.1", "type": "request", "message_id": Uuid::new_v4().to_string(),
        "request_id": Uuid::new_v4().to_string(), "timestamp_ms": 1,
        "scope": {"workspace_id": WORKSPACE},
        "intent": {"operation": AI_TEXT_CONNECTION, "arguments": arguments},
    })
}

fn execute(registry: &mut OperationRegistry<BoundProfile>, arguments: Value) -> Value {
    registry
        .execute_workspace(&access(), command(arguments))
        .unwrap()["outcome"]
        .clone()
}

fn propose(registry: &mut OperationRegistry<BoundProfile>, route: Value) -> Value {
    execute(registry, json!({"action": "propose", "route": route}))
}

fn activate(registry: &mut OperationRegistry<BoundProfile>, proposal: &Value) -> Value {
    execute(
        registry,
        json!({"action": "activate", "proposal_id": proposal["result"]["proposal_id"]}),
    )
}

fn send(connection: &Value, question: &str) -> Value {
    command(json!({"action": "send", "connection_id": connection, "question": question}))
}

fn poll(registry: &mut OperationRegistry<BoundProfile>, connection: &Value, turn: &Value) -> Value {
    execute(
        registry,
        json!({"action": "poll", "connection_id": connection, "turn_id": turn}),
    )
}

fn route() -> Value {
    json!({"kind":"ollama", "origin":"http://127.0.0.1:12345", "model":"synthetic:local"})
}

#[test]
fn authority_projection_replay_and_revocation() {
    let mut registry = registry();
    let invented = json!(Uuid::new_v4().to_string());
    assert_eq!(
        registry
            .execute_workspace(&access(), send(&invented, "unconfigured"))
            .unwrap()["outcome"]["error"]["code"],
        "STALE_CONTEXT",
    );

    for unauthorized in [
        WorkspaceAccess {
            actor: Actor::Ai,
            ..access()
        },
        WorkspaceAccess {
            principal_id: "external",
            paired_client_id: Some("external"),
            ..access()
        },
        WorkspaceAccess {
            profile_id: "other-profile",
            ..access()
        },
        WorkspaceAccess {
            operations: &[],
            ..access()
        },
    ] {
        let proposal = command(json!({"action":"propose","route":route()}));
        let reply = registry.execute_workspace(&unauthorized, proposal).unwrap();
        assert_eq!(reply["outcome"]["error"]["code"], "PERMISSION_DENIED");
    }

    let proposal = propose(&mut registry, route());
    assert_eq!(
        proposal["result"]["route"]["destination"],
        "http://127.0.0.1:12345/api/chat"
    );
    assert_eq!(
        execute(&mut registry, json!({"action":"status"}))["result"]["connection"],
        Value::Null
    );
    let activated = activate(&mut registry, &proposal);
    let connection = &activated["result"]["connection"]["connection_id"];
    assert_eq!(
        activate(&mut registry, &proposal)["error"]["code"],
        "CONFIRMATION_REQUIRED"
    );

    let first = send(connection, "First synthetic question.");
    let first_turn = registry
        .execute_workspace(&access(), first.clone())
        .unwrap()["outcome"]["result"]["turn_id"]
        .clone();
    let work = registry
        .take_text_model_work(&access(), parse_id(&first_turn))
        .unwrap()
        .unwrap();
    assert_eq!(
        work.request(),
        &json!({
            "model":"synthetic:local", "messages":[{"role":"user","content":"First synthetic question."}], "stream":false,
        })
    );
    assert_eq!(
        registry
            .execute_workspace(&access(), first.clone())
            .unwrap()["outcome"]["result"]["turn_id"],
        first_turn
    );
    assert!(
        registry
            .take_text_model_work(&access(), parse_id(&first_turn))
            .unwrap()
            .is_none()
    );
    assert_eq!(
        poll(&mut registry, connection, &first_turn)["result"]["turn"]["state"],
        "running"
    );
    work.finish(Ok(
        json!({"model":"actual-local", "text":"First synthetic answer."}),
    ));
    let succeeded = poll(&mut registry, connection, &first_turn);
    assert_eq!(succeeded["result"]["turn"]["model"], "actual-local");
    assert_eq!(poll(&mut registry, connection, &first_turn), succeeded);

    let second = send(connection, "Second synthetic question.");
    let second_turn =
        registry.execute_workspace(&access(), second).unwrap()["outcome"]["result"]["turn_id"]
            .clone();
    let work = registry
        .take_text_model_work(&access(), parse_id(&second_turn))
        .unwrap()
        .unwrap();
    assert_eq!(
        work.request()["messages"],
        json!([
            {"role":"user","content":"First synthetic question."},
            {"role":"assistant","content":"First synthetic answer."},
            {"role":"user","content":"Second synthetic question."}
        ])
    );
    assert_eq!(
        registry.execute_workspace(&access(), first).unwrap()["outcome"]["error"]["code"],
        "STALE_CONTEXT"
    );

    let next = propose(
        &mut registry,
        json!({"kind":"ollama", "origin":"https://models.example.invalid", "model":"synthetic:local"}),
    );
    assert_eq!(
        next["result"]["route"]["destination"],
        "https://models.example.invalid/api/chat"
    );
    assert!(work.is_cancelled());
    work.finish(Ok(json!({"model":"old-route", "text":"Obsolete answer."})));
    assert_eq!(
        poll(&mut registry, connection, &second_turn)["error"]["code"],
        "STALE_CONTEXT"
    );
    assert_eq!(
        registry
            .execute_workspace(&access(), send(connection, "Old authority."))
            .unwrap()["outcome"]["error"]["code"],
        "STALE_CONTEXT"
    );

    let new = activate(&mut registry, &next);
    let new_connection = &new["result"]["connection"]["connection_id"];
    let submitted = registry
        .execute_workspace(&access(), send(new_connection, "Only new context."))
        .unwrap();
    let new_turn = &submitted["outcome"]["result"]["turn_id"];
    let work = registry
        .take_text_model_work(&access(), parse_id(new_turn))
        .unwrap()
        .unwrap();
    assert_eq!(
        work.request()["messages"],
        json!([{"role":"user","content":"Only new context."}])
    );
    assert_eq!(
        execute(
            &mut registry,
            json!({
                "action":"cancel", "connection_id":new_connection, "turn_id":new_turn,
            })
        )["result"]["action"],
        "cancel"
    );
    assert!(work.is_cancelled());
    assert_eq!(
        execute(&mut registry, json!({"action":"status"}))["result"]["connection"],
        Value::Null
    );
}

#[test]
fn offline_mock_has_no_worker_and_input_cannot_include_credentials() {
    let mut registry = registry();
    for origin in [
        "http://remote.example.invalid",
        "http://localhost:11434",
        "https://u:p@example.invalid",
        "https://example.invalid/?token=secret",
        "https://example.invalid/api/chat",
        "https://example.invalid#token",
        "https://example.invalid:0",
    ] {
        let reply = propose(
            &mut registry,
            json!({"kind":"ollama", "origin":origin, "model":"synthetic:local"}),
        );
        assert_eq!(reply["error"]["code"], "INVALID_ARGUMENT", "{origin}");
    }

    let proposal = propose(&mut registry, json!({"kind":"offline_mock"}));
    let enabled = activate(&mut registry, &proposal);
    let connection = &enabled["result"]["connection"]["connection_id"];
    let submitted = registry
        .execute_workspace(&access(), send(connection, "Synthetic offline question."))
        .unwrap();
    let turn_id = &submitted["outcome"]["result"]["turn_id"];
    assert!(
        registry
            .take_text_model_work(&access(), parse_id(turn_id))
            .unwrap()
            .is_none()
    );
    assert_eq!(
        poll(&mut registry, connection, turn_id)["result"]["turn"]["model"],
        MOCK_MODEL
    );
    assert_eq!(
        execute(
            &mut registry,
            json!({"action":"disconnect", "connection_id":connection})
        )["result"]["action"],
        "disconnect"
    );
    assert_eq!(
        poll(&mut registry, connection, turn_id)["error"]["code"],
        "STALE_CONTEXT"
    );
}

#[test]
fn openai_is_bound_to_its_disclosed_route_and_current_profile_key() {
    let mut registry = registry();
    let cloud = json!({"kind": "openai_responses", "model": "synthetic-selected"});
    assert_eq!(
        propose(&mut registry, cloud.clone())["error"]["code"],
        "PROVIDER_NOT_CONFIGURED"
    );
    let revision = Uuid::new_v4();
    let other_profile = WorkspaceAccess {
        profile_id: "other-profile",
        ..access()
    };
    assert_eq!(
        registry
            .refresh_openai_credential(&other_profile, Some(revision))
            .unwrap_err()
            .code,
        ErrorCode::PERMISSIONDENIED,
    );
    assert_eq!(
        propose(&mut registry, cloud.clone())["error"]["code"],
        "PROVIDER_NOT_CONFIGURED"
    );
    registry
        .refresh_openai_credential(&access(), Some(revision))
        .unwrap();
    let reviewed = propose(&mut registry, cloud.clone());
    assert_eq!(
        reviewed["result"]["route"],
        json!({
            "kind": "openai_responses", "model": "synthetic-selected",
            "destination": "https://api.openai.com/v1/responses", "scope": "conversation_text",
            "charge_basis": "openai_api_account",
        })
    );
    let replacement = Uuid::new_v4();
    registry
        .refresh_openai_credential(&access(), Some(replacement))
        .unwrap();
    assert_eq!(
        activate(&mut registry, &reviewed)["error"]["code"],
        "CONFIRMATION_REQUIRED"
    );
    let reviewed = propose(&mut registry, cloud.clone());
    let enabled = activate(&mut registry, &reviewed);
    let connection = &enabled["result"]["connection"]["connection_id"];
    let submitted = registry
        .execute_workspace(
            &access(),
            send(connection, "Synthetic first cloud question."),
        )
        .unwrap();
    let turn = &submitted["outcome"]["result"]["turn_id"];
    let work = registry
        .take_text_model_work(&access(), parse_id(turn))
        .unwrap()
        .unwrap();
    assert_eq!(
        work.provider(),
        TextModelProvider::OpenAiResponses {
            credential_revision: replacement
        }
    );
    assert_eq!(work.destination(), "https://api.openai.com/v1/responses");
    work.finish(Ok(
        json!({"model": "synthetic-answer", "text": "Synthetic cloud answer."}),
    ));
    assert_eq!(
        poll(&mut registry, connection, turn)["result"]["turn"]["state"],
        "completed"
    );

    let submitted = registry
        .execute_workspace(
            &access(),
            send(connection, "Synthetic next cloud question."),
        )
        .unwrap();
    let turn = &submitted["outcome"]["result"]["turn_id"];
    let work = registry
        .take_text_model_work(&access(), parse_id(turn))
        .unwrap()
        .unwrap();
    assert_eq!(
        work.request(),
        &json!({
            "model": "synthetic-selected", "stream": false,
            "messages": [
                {"role": "user", "content": "Synthetic first cloud question."},
                {"role": "assistant", "content": "Synthetic cloud answer."},
                {"role": "user", "content": "Synthetic next cloud question."}
            ],
        })
    );
    let reviewed_local = propose(&mut registry, route());
    assert!(work.is_cancelled());
    assert_eq!(
        poll(&mut registry, connection, turn)["error"]["code"],
        "STALE_CONTEXT"
    );
    registry.refresh_openai_credential(&access(), None).unwrap();
    let connected_local = activate(&mut registry, &reviewed_local);
    let local_id = &connected_local["result"]["connection"]["connection_id"];
    let submitted = registry
        .execute_workspace(&access(), send(local_id, "Synthetic local question."))
        .unwrap();
    let work = registry
        .take_text_model_work(
            &access(),
            parse_id(&submitted["outcome"]["result"]["turn_id"]),
        )
        .unwrap()
        .unwrap();
    assert_eq!(work.provider(), TextModelProvider::Ollama);
    assert_eq!(
        work.request()["messages"],
        json!([{"role": "user", "content": "Synthetic local question."}])
    );
    assert_eq!(
        propose(&mut registry, cloud)["error"]["code"],
        "PROVIDER_NOT_CONFIGURED"
    );
    assert!(!work.is_cancelled());
}
