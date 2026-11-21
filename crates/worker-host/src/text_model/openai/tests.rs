use std::{future, net::SocketAddr, sync::Arc, time::Duration};

use reqwest::{
    Client,
    dns::{Addrs, Name, Resolve, Resolving},
};
use rustls::{
    RootCertStore, ServerConfig,
    pki_types::{CertificateDer, PrivateKeyDer, pem::PemObject},
};
use scopehub_application::{
    AI_TEXT_CONNECTION, Actor, DecisionNoteBackupData, OperationRegistry, StoredWorkspaceView,
    TextModelProvider, TextModelWork, WorkspaceAccess, WorkspaceBinding, WorkspaceStore,
    WorkspaceTransaction, WorkspaceUndoAccess,
};
use scopehub_domain::protocol::{DecisionNoteReadOutput, Error, ErrorCode};
use serde_json::{Value, json};
use tokio::{
    io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader},
    net::TcpListener,
    time::timeout,
};
use tokio_rustls::TlsAcceptor;
use uuid::Uuid;

use super::{client_builder, complete_with, tls_builder};

const CA: &[u8] = include_bytes!("tls-fixture/ca.pem");
const SERVER: &[u8] = include_bytes!("tls-fixture/server.pem");
// The matching key is public test data and only the isolated test trusts this CA.
const TEST_KEY: &[u8] = include_bytes!("tls-fixture/server-test-key.pem");
const CREDENTIAL: &str = "scopehub-controlled-openai-credential";
const PROFILE: &str = "controlled-profile";
const WORKSPACE: &str = "main";

#[tokio::test]
async fn captures_two_turns_and_rejects_unauthorized_or_redirected_requests() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let mut roots = RootCertStore::empty();
    roots
        .add(CertificateDer::from_pem_slice(CA).unwrap())
        .unwrap();
    let tls = tls_builder()
        .unwrap()
        .with_root_certificates(roots)
        .with_no_client_auth();
    let client = client_builder(tls)
        .dns_resolver(Arc::new(FixtureDns(listener.local_addr().unwrap())))
        .build()
        .unwrap();
    let acceptor = TlsAcceptor::from(Arc::new(
        ServerConfig::builder_with_provider(Arc::new(rustls::crypto::ring::default_provider()))
            .with_safe_default_protocol_versions()
            .unwrap()
            .with_no_client_auth()
            .with_single_cert(
                vec![CertificateDer::from_pem_slice(SERVER).unwrap()],
                PrivateKeyDer::from_pem_slice(TEST_KEY).unwrap(),
            )
            .unwrap(),
    ));
    let peer = tokio::spawn(async move {
        let mut captured = Vec::new();
        for (status, location, body) in [
            ("200 OK", "", reply("First synthetic reply.")),
            ("200 OK", "", reply("Second synthetic reply.")),
            (
                "401 Unauthorized",
                "",
                json!({"error":{"message": CREDENTIAL,"code":"invalid_api_key"}}),
            ),
            (
                "307 Temporary Redirect",
                "Location: https://api.openai.com/unapproved\r\n",
                json!({}),
            ),
        ] {
            let (socket, _) = timeout(Duration::from_secs(5), listener.accept())
                .await
                .expect("connector reached the resolved test socket")
                .unwrap();
            let stream = acceptor.accept(socket).await.unwrap();
            let mut stream = BufReader::new(stream);
            let (start, authorization, host, body_sent) = capture(&mut stream).await;
            assert_eq!(start, "POST /v1/responses HTTP/1.1\r\n");
            assert_eq!(host, "api.openai.com");
            assert!(authorization == format!("Bearer {CREDENTIAL}"));
            captured.push(body_sent);
            let response = format!(
                "HTTP/1.1 {status}\r\n{location}Content-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.to_string().len()
            );
            stream.write_all(response.as_bytes()).await.unwrap();
            stream.shutdown().await.unwrap();
        }
        assert!(
            timeout(Duration::from_millis(200), listener.accept())
                .await
                .is_err(),
            "a failed request must not be followed or replayed"
        );
        captured
    });

    let first = json!([{"role": "user", "content": "First controlled question"}]);
    let first_request = projection(&first);
    let mut unrelated = first_request.clone();
    unrelated["saved_private_records"] = json!(["private-record-canary"]);
    let refused = complete_with(&client, &unrelated, CREDENTIAL, future::pending())
        .await
        .unwrap_err();
    assert_eq!(refused.code, ErrorCode::INVALIDARGUMENT);
    let cancelled = complete_with(&client, &first_request, CREDENTIAL, future::ready(()))
        .await
        .unwrap_err();
    assert_eq!(cancelled.code, ErrorCode::CANCELLED);
    let mut registry = OperationRegistry::new(BoundProfile(WorkspaceBinding {
        profile_id: PROFILE.into(),
        workspace_id: WORKSPACE.into(),
    }));
    let cloud_route = json!({"kind":"openai_responses", "model":"controlled-selected-model"});
    let proposal = json!({"action":"propose", "route":cloud_route});
    assert_eq!(
        operate(&mut registry, Actor::Ai, proposal.clone())["error"]["code"],
        "PERMISSION_DENIED"
    );
    assert_eq!(
        operate(&mut registry, Actor::User, proposal.clone())["error"]["code"],
        "PROVIDER_NOT_CONFIGURED"
    );
    let credential_revision = Uuid::new_v4();
    registry
        .refresh_openai_credential(&access(Actor::User), Some(credential_revision))
        .unwrap();
    let cloud = activate(&mut registry, proposal);
    let selected = command(json!({
        "action": "send", "connection_id": cloud, "selection_revision": 1,
        "question": "What is the selected close?",
    }));
    let request_id = Uuid::parse_str(selected["request_id"].as_str().unwrap()).unwrap();
    let access = access(Actor::User);
    let denied = registry.execute_workspace(&access, selected).unwrap();
    assert_eq!(denied["outcome"]["error"]["code"], "PERMISSION_DENIED");
    let status = operate(&mut registry, Actor::User, json!({"action": "status"}));
    assert_eq!(status["result"]["connection"]["connection_id"], cloud);
    assert_eq!(status["result"]["turn_id"], Value::Null);
    assert!(
        registry
            .take_text_model_work(&access, request_id)
            .unwrap()
            .is_none()
    );
    let result = cloud_turn(
        &mut registry,
        &client,
        &cloud,
        "First controlled question",
        credential_revision,
    )
    .await;
    assert_eq!(
        result,
        json!({"state": "completed", "model": "controlled-actual-model", "text": "First synthetic reply."})
    );

    let second = json!([
        first[0],
        {"role": "assistant", "content": result["text"]},
        {"role": "user", "content": "Second controlled question"}
    ]);
    let result = cloud_turn(
        &mut registry,
        &client,
        &cloud,
        "Second controlled question",
        credential_revision,
    )
    .await;
    assert_eq!(
        result,
        json!({"state": "completed", "model": "controlled-actual-model", "text": "Second synthetic reply."})
    );
    let rejected = cloud_turn(
        &mut registry,
        &client,
        &cloud,
        "Third controlled question",
        credential_revision,
    )
    .await;
    assert_eq!(rejected["state"], "failed");
    assert_eq!(rejected["error"]["code"], "PERMISSION_DENIED");
    assert_eq!(rejected["error"]["retryable"], false);
    assert!(
        !serde_json::to_string(&rejected)
            .unwrap()
            .contains(CREDENTIAL)
    );
    let abandoned = send(&mut registry, &cloud, "This cloud work should be revoked");
    let work = take_work(&mut registry, &abandoned);
    let local = activate(
        &mut registry,
        json!({"action":"propose", "route":{
            "kind":"ollama", "origin":"http://127.0.0.1:12345", "model":"controlled-local"
        }}),
    );
    assert!(work.is_cancelled());
    assert_eq!(
        poll(&mut registry, &cloud, &abandoned)["error"]["code"],
        "STALE_CONTEXT"
    );
    let local_turn = send(&mut registry, &local, "A new local question");
    let local_work = take_work(&mut registry, &local_turn);
    assert_eq!(local_work.provider(), TextModelProvider::Ollama);
    assert_eq!(local_work.destination(), "http://127.0.0.1:12345/api/chat");
    assert_eq!(
        local_work.request()["messages"],
        json!([{"role":"user", "content":"A new local question"}])
    );
    local_work.finish(Ok(
        json!({"model":"controlled-local", "text":"Local synthetic reply."}),
    ));
    assert_eq!(
        poll(&mut registry, &local, &local_turn)["result"]["turn"]["text"],
        "Local synthetic reply."
    );

    let redirected = complete_with(&client, &first_request, CREDENTIAL, future::pending())
        .await
        .unwrap_err();
    assert_eq!(redirected.code, ErrorCode::DATAUNAVAILABLE);

    let third = json!([
        second[0],
        second[1],
        second[2],
        {"role": "assistant", "content": "Second synthetic reply."},
        {"role": "user", "content": "Third controlled question"}
    ]);
    assert_eq!(
        peer.await.unwrap(),
        [
            outbound(first.clone()),
            outbound(second),
            outbound(third),
            outbound(first)
        ]
    );
}

struct BoundProfile(WorkspaceBinding);

// Any attempt to read workspace/private data through the core fails the test.
impl WorkspaceStore for BoundProfile {
    fn binding(&self) -> &WorkspaceBinding {
        &self.0
    }

    fn snapshot(
        &self,
        _: Option<WorkspaceUndoAccess<'_>>,
        _: bool,
    ) -> Result<StoredWorkspaceView, Error> {
        panic!("unexpected workspace snapshot")
    }

    fn inspect<T>(
        &self,
        _: impl FnOnce(&dyn WorkspaceTransaction) -> Result<T, Error>,
    ) -> Result<T, Error> {
        panic!("unexpected workspace inspection")
    }

    fn read_notes(&self, _: Option<&str>, _: u64) -> Result<DecisionNoteReadOutput, Error> {
        panic!("unexpected private notes read")
    }

    fn decision_note_backup(&self) -> Result<DecisionNoteBackupData, Error> {
        panic!("unexpected backup read")
    }

    fn clean_deleted_note(&mut self) -> Result<(), Error> {
        panic!("unexpected note cleanup")
    }

    fn clean_private_record_pages(&mut self) -> Result<(), Error> {
        panic!("unexpected private record cleanup")
    }

    fn transact<T>(
        &mut self,
        _: impl FnOnce(&mut dyn WorkspaceTransaction) -> Result<T, Error>,
    ) -> Result<T, Error> {
        panic!("unexpected workspace transaction")
    }
}

fn access(actor: Actor) -> WorkspaceAccess<'static> {
    WorkspaceAccess {
        actor,
        profile_id: PROFILE,
        principal_id: PROFILE,
        paired_client_id: None,
        workspace_id: WORKSPACE,
        operations: &[AI_TEXT_CONNECTION],
        disclose_workspace_state: false,
    }
}

fn command(args: Value) -> Value {
    json!({
        "protocol_version": "1.1", "type": "request", "message_id": Uuid::new_v4().to_string(),
        "request_id": Uuid::new_v4().to_string(), "timestamp_ms": 1,
        "scope": {"workspace_id": WORKSPACE},
        "intent": {"operation": AI_TEXT_CONNECTION, "arguments": args},
    })
}

fn operate(registry: &mut OperationRegistry<BoundProfile>, actor: Actor, args: Value) -> Value {
    registry
        .execute_workspace(&access(actor), command(args))
        .unwrap()["outcome"]
        .clone()
}

fn activate(registry: &mut OperationRegistry<BoundProfile>, proposal: Value) -> Value {
    let proposed = operate(registry, Actor::User, proposal);
    let activation = operate(
        registry,
        Actor::User,
        json!({"action":"activate", "proposal_id":proposed["result"]["proposal_id"]}),
    );
    assert_eq!(activation["state"], "completed");
    activation["result"]["connection"]["connection_id"].clone()
}

fn send(
    registry: &mut OperationRegistry<BoundProfile>,
    connection: &Value,
    question: &str,
) -> Value {
    let sent = operate(
        registry,
        Actor::User,
        json!({"action":"send", "connection_id":connection, "question":question}),
    );
    assert_eq!(sent["state"], "completed");
    sent["result"]["turn_id"].clone()
}

fn poll(registry: &mut OperationRegistry<BoundProfile>, connection: &Value, turn: &Value) -> Value {
    operate(
        registry,
        Actor::User,
        json!({"action":"poll", "connection_id":connection, "turn_id":turn}),
    )
}

fn take_work(registry: &mut OperationRegistry<BoundProfile>, turn: &Value) -> TextModelWork {
    let turn_id = Uuid::parse_str(turn.as_str().unwrap()).unwrap();
    registry
        .take_text_model_work(&access(Actor::User), turn_id)
        .unwrap()
        .unwrap()
}

async fn cloud_turn(
    registry: &mut OperationRegistry<BoundProfile>,
    client: &Client,
    connection: &Value,
    question: &str,
    credential_revision: Uuid,
) -> Value {
    let turn = send(registry, connection, question);
    let work = take_work(registry, &turn);
    assert_eq!(
        work.provider(),
        TextModelProvider::OpenAiResponses {
            credential_revision
        }
    );
    assert_eq!(work.destination(), "https://api.openai.com/v1/responses");
    let result = complete_with(client, work.request(), CREDENTIAL, future::pending()).await;
    work.finish(result);
    poll(registry, connection, &turn)["result"]["turn"].clone()
}

fn projection(messages: &Value) -> Value {
    json!({"model": "controlled-selected-model", "messages": messages, "stream": false})
}

fn outbound(input: Value) -> Value {
    json!({
        "model": "controlled-selected-model",
        "input": input,
        "store": false,
        "stream": false,
        "max_output_tokens": 4096
    })
}

fn reply(text: &str) -> Value {
    json!({
        "id": "resp_controlled_only",
        "conversation": {"id": "conv_controlled_only"},
        "model": "controlled-actual-model",
        "status": "completed",
        "output": [
            {"type":"reasoning", "id": "rs_controlled", "summary": []},
            {"type": "message", "id": "msg_controlled", "role": "assistant",
             "status": "completed", "content": [{"type": "output_text", "text": text}]}
        ]
    })
}

async fn capture(stream: &mut (impl AsyncBufReadExt + Unpin)) -> (String, String, String, Value) {
    let mut start = String::new();
    stream.read_line(&mut start).await.unwrap();
    let mut authorization = String::new();
    let mut host = String::new();
    let mut content_length = 0;
    loop {
        let mut line = String::new();
        assert_ne!(stream.read_line(&mut line).await.unwrap(), 0);
        if line == "\r\n" {
            break;
        }
        let (header, value) = line.split_once(':').unwrap();
        match header.to_ascii_lowercase().as_str() {
            "authorization" => authorization = value.trim().to_owned(),
            "host" => host = value.trim().to_owned(),
            "content-length" => content_length = value.trim().parse().unwrap(),
            _ => {}
        }
    }
    let mut body = vec![0; content_length];
    stream.read_exact(&mut body).await.unwrap();
    (
        start,
        authorization,
        host,
        serde_json::from_slice(&body).unwrap(),
    )
}

struct FixtureDns(SocketAddr);

impl Resolve for FixtureDns {
    fn resolve(&self, name: Name) -> Resolving {
        let socket = self.0;
        Box::pin(async move {
            if name.as_str() == "api.openai.com" {
                Ok(Box::new(std::iter::once(socket)) as Addrs)
            } else {
                Err("non-fixture destination".into())
            }
        })
    }
}
