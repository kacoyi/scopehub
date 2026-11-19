use std::{
    collections::{HashSet, VecDeque},
    net::{Ipv4Addr, Ipv6Addr},
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
        mpsc,
    },
    time::{Duration, Instant},
};

use scopehub_domain::protocol::{Error, ErrorCode};
use serde_json::{Value, json};
use url::{Host, Url};
use uuid::Uuid;

use super::{WorkspaceAccess, WorkspaceReply, WorkspaceStore, permits_scope};
use crate::{Actor, Operation, OperationRegistry, failure};

pub const AI_TEXT_CONNECTION: &str = "ai.textConnection";

const PROPOSAL_TTL: Duration = Duration::from_secs(300);
const MAX_HISTORY_PAIRS: usize = 4;
const MAX_HISTORY_WIRE_BYTES: usize = 64 * 1024;
const MAX_TURN_IDS: usize = 512;
const MAX_TEXT_BYTES: usize = 64 * 1024;
const MOCK_MODEL: &str = "ScopeHub offline mock";

#[derive(Default)]
pub(crate) struct TextConnection {
    proposal: Option<Proposal>,
    active: Option<Grant>,
}

struct Proposal {
    id: Uuid,
    route: Route,
    expires: Instant,
}

enum Route {
    Ollama { destination: String, model: String },
    OfflineMock,
}

struct Grant {
    id: Uuid,
    route: Route,
    history: VecDeque<(String, String)>,
    used_requests: HashSet<Uuid>,
    turn: Option<Turn>,
}

struct Turn {
    id: Uuid,
    question: String,
    cancelled: Arc<AtomicBool>,
    work: Option<TextModelWork>,
    completion: mpsc::Receiver<Result<Value, Error>>,
    state: Option<Value>,
}

impl Drop for Turn {
    fn drop(&mut self) {
        self.cancelled.store(true, Ordering::Release);
    }
}

/// One authorized request for the trusted host. The host can cancel, execute
/// and return the response. It cannot create work or widen its disclosure.
pub struct TextModelWork {
    destination: String,
    request: Value,
    cancelled: Arc<AtomicBool>,
    completion: mpsc::SyncSender<Result<Value, Error>>,
}

impl TextModelWork {
    pub fn destination(&self) -> &str {
        &self.destination
    }

    pub fn request(&self) -> &Value {
        &self.request
    }

    pub fn is_cancelled(&self) -> bool {
        self.cancelled.load(Ordering::Acquire)
    }

    pub fn finish(self, result: Result<Value, Error>) {
        let _ = self.completion.try_send(result);
    }
}

impl<S: WorkspaceStore> OperationRegistry<S> {
    fn authorize_text_connection(&self, access: &WorkspaceAccess<'_>) -> Result<(), Error> {
        let binding = self.port.binding();
        if !permits_scope(access, binding)
            || !matches!(access.actor, Actor::User | Actor::LocalUser)
            || access.paired_client_id.is_some()
            || access.principal_id != binding.profile_id
            || !access.operations.contains(&AI_TEXT_CONNECTION)
        {
            return Err(failure(ErrorCode::PERMISSIONDENIED));
        }
        Ok(())
    }

    pub(super) fn execute_text_connection(
        &mut self,
        access: &WorkspaceAccess<'_>,
        op: &Operation,
        request: Value,
    ) -> Result<Value, Error> {
        if !op.wire_validators().0.is_valid(&request) {
            return Err(failure(ErrorCode::INVALIDARGUMENT));
        }
        let request_id = request["request_id"]
            .as_str()
            .expect("validated request ID");
        let reply = WorkspaceReply::new(op, request_id)?;
        let result = op
            .authorize(access.actor)
            .and_then(|()| self.authorize_text_connection(access))
            .and_then(|()| {
                if request["scope"]["workspace_id"] != access.workspace_id {
                    return Err(failure(ErrorCode::PERMISSIONDENIED));
                }
                self.text_connection.execute(
                    &request["intent"]["arguments"],
                    parse_id(&request["request_id"]),
                )
            });
        match result {
            Ok(result) => reply.validated(json!({
                "state": "completed", "operation": AI_TEXT_CONNECTION, "result": result,
            })),
            Err(error) => reply.failed(&error),
        }
    }

    /// Trusted host handoff for a current pending turn. Retrieval consumes
    /// the work item and never opens a workspace storage transaction.
    pub fn take_text_model_work(
        &mut self,
        access: &WorkspaceAccess<'_>,
        turn_id: Uuid,
    ) -> Result<Option<TextModelWork>, Error> {
        self.authorize_text_connection(access)?;
        let turn = self
            .text_connection
            .active
            .as_mut()
            .and_then(|grant| grant.turn.as_mut())
            .filter(|turn| turn.id == turn_id);
        Ok(turn.and_then(|turn| turn.work.take()))
    }
}

impl TextConnection {
    fn execute(&mut self, args: &Value, request_id: Uuid) -> Result<Value, Error> {
        match args["action"].as_str().expect("validated action") {
            "status" => {
                let turn_id = self
                    .active
                    .as_ref()
                    .and_then(|grant| grant.turn.as_ref())
                    .map(|turn| turn.id.to_string());
                Ok(json!({
                    "action": "status", "connection": self.active.as_ref().map(Grant::disclosure), "turn_id": turn_id,
                }))
            }
            "propose" => {
                let route = Route::parse(&args["route"])?;
                let id = Uuid::new_v4();
                let disclosure = route.disclosure();
                self.active = None;
                self.proposal = Some(Proposal {
                    id,
                    route,
                    expires: Instant::now() + PROPOSAL_TTL,
                });
                Ok(json!({"action": "propose", "proposal_id": id.to_string(), "route": disclosure}))
            }
            "activate" => {
                let id = parse_id(&args["proposal_id"]);
                if !self
                    .proposal
                    .as_ref()
                    .is_some_and(|proposal| proposal.id == id && proposal.expires > Instant::now())
                {
                    return Err(connection_failure(ErrorCode::CONFIRMATIONREQUIRED));
                }
                let proposal = self.proposal.take().expect("reviewed proposal");
                let grant = Grant {
                    id: Uuid::new_v4(),
                    route: proposal.route,
                    history: VecDeque::new(),
                    used_requests: HashSet::new(),
                    turn: None,
                };
                let connection = grant.disclosure();
                self.active = Some(grant);
                Ok(json!({"action": "activate", "connection": connection}))
            }
            action => {
                let id = parse_id(&args["connection_id"]);
                let grant = self
                    .active
                    .as_mut()
                    .filter(|grant| grant.id == id)
                    .ok_or_else(|| connection_failure(ErrorCode::STALECONTEXT))?;
                match action {
                    "disconnect" => self.active = None,
                    "cancel" => {
                        grant.turn_mut(parse_id(&args["turn_id"]))?;
                        self.active = None;
                    }
                    "send" => {
                        let question = args["question"].as_str().expect("validated question");
                        let turn_id = grant.send(question, request_id)?;
                        return Ok(json!({"action": action, "turn_id": turn_id.to_string()}));
                    }
                    "poll" => {
                        let id = parse_id(&args["turn_id"]);
                        grant.turn_mut(id)?;
                        grant.settle();
                        let turn = grant.turn_mut(id)?;
                        return Ok(json!({
                            "action": action, "turn_id": id.to_string(),
                            "turn": turn.state.as_ref().cloned().unwrap_or_else(|| json!({"state": "running"})),
                        }));
                    }
                    _ => unreachable!("validated connection action"),
                }
                Ok(json!({"action": action}))
            }
        }
    }
}

impl Route {
    fn parse(route: &Value) -> Result<Self, Error> {
        if route["kind"] == "offline_mock" {
            return Ok(Self::OfflineMock);
        }
        let model = route["model"].as_str().expect("validated model");
        if !model
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"-_.:/".contains(&byte))
        {
            return Err(connection_failure(ErrorCode::INVALIDARGUMENT));
        }
        let origin = route["origin"].as_str().expect("validated origin");
        let valid_origin = origin.split_once("://").is_some_and(|(_, authority)| {
            !authority.trim_end_matches('/').contains('/')
                && !authority.is_empty()
                && !authority.contains(['@', '\\', '?', '#', '%'])
                && !origin.chars().any(char::is_whitespace)
                && !origin.chars().any(char::is_control)
        });
        let mut url = Url::parse(origin)
            .ok()
            .filter(|url| valid_origin && url.path() == "/")
            .ok_or_else(|| connection_failure(ErrorCode::INVALIDARGUMENT))?;
        let supported = match (url.scheme(), url.host()) {
            ("https", Some(_)) => true,
            ("http", Some(Host::Ipv4(ip))) => ip == Ipv4Addr::LOCALHOST,
            ("http", Some(Host::Ipv6(ip))) => ip == Ipv6Addr::LOCALHOST,
            _ => false,
        };
        if !supported || url.port() == Some(0) {
            return Err(connection_failure(ErrorCode::INVALIDARGUMENT));
        }
        url.set_path("/api/chat");
        if url.as_str().len() > 528 {
            return Err(connection_failure(ErrorCode::INVALIDARGUMENT));
        }
        Ok(Self::Ollama {
            destination: url.into(),
            model: model.to_string(),
        })
    }

    fn disclosure(&self) -> Value {
        match self {
            Self::Ollama { destination, model } => json!({
                "kind": "ollama", "destination": destination, "model": model,
                "scope": "conversation_text", "charge_basis": "endpoint_operator",
            }),
            Self::OfflineMock => json!({
                "kind": "offline_mock", "destination": "This app (offline)", "model": MOCK_MODEL,
                "scope": "conversation_text", "charge_basis": "none",
            }),
        }
    }
}

impl Grant {
    fn disclosure(&self) -> Value {
        json!({"connection_id": self.id.to_string(), "route": self.route.disclosure()})
    }

    fn turn_mut(&mut self, id: Uuid) -> Result<&mut Turn, Error> {
        self.turn
            .as_mut()
            .filter(|turn| turn.id == id)
            .ok_or_else(|| connection_failure(ErrorCode::STALECONTEXT))
    }

    fn send(&mut self, question: &str, request_id: Uuid) -> Result<Uuid, Error> {
        if question.trim().is_empty() {
            return Err(connection_failure(ErrorCode::INVALIDARGUMENT));
        }
        if self.used_requests.contains(&request_id) {
            return self
                .turn
                .as_ref()
                .filter(|turn| turn.id == request_id && turn.question == question)
                .map(|turn| turn.id)
                .ok_or_else(|| connection_failure(ErrorCode::STALECONTEXT));
        }
        self.settle();
        if self.turn.as_ref().is_some_and(|turn| turn.state.is_none()) {
            return Err(connection_failure(ErrorCode::BUSY));
        }
        if self.used_requests.len() >= MAX_TURN_IDS {
            return Err(connection_failure(ErrorCode::RESOURCELIMIT));
        }
        let (sender, completion) = mpsc::sync_channel(1);
        let cancelled = Arc::new(AtomicBool::new(false));
        let (work, state) = match &self.route {
            Route::OfflineMock => (
                None,
                Some(json!({
                    "state": "completed", "model": MOCK_MODEL,
                    "text": "Offline mock response. A connected text model would answer your question here.",
                })),
            ),
            Route::Ollama { destination, model } => (
                Some(TextModelWork {
                    destination: destination.clone(),
                    request: outbound(model, &self.history, question),
                    cancelled: Arc::clone(&cancelled),
                    completion: sender,
                }),
                None,
            ),
        };
        self.used_requests.insert(request_id);
        self.turn = Some(Turn {
            id: request_id,
            question: question.to_string(),
            cancelled,
            work,
            completion,
            state,
        });
        Ok(request_id)
    }

    fn settle(&mut self) {
        let Some(turn) = self.turn.as_mut().filter(|turn| turn.state.is_none()) else {
            return;
        };
        let result = match turn.completion.try_recv() {
            Ok(result) => result.and_then(validate_response),
            Err(mpsc::TryRecvError::Empty) => return,
            Err(mpsc::TryRecvError::Disconnected) => {
                Err(connection_failure(ErrorCode::WORKEREXITED))
            }
        };
        turn.state = Some(match result {
            Ok((model, text)) => {
                self.history
                    .push_back((turn.question.clone(), text.clone()));
                if self.history.len() > MAX_HISTORY_PAIRS {
                    self.history.pop_front();
                }
                json!({"state": "completed", "model": model, "text": text})
            }
            Err(error) => {
                let fault = connection_failure(error.code);
                json!({
                    "state": "failed",
                    "error": {"code": fault.code, "message": fault.message, "retryable": fault.retryable},
                })
            }
        });
    }
}

fn outbound(model: &str, history: &VecDeque<(String, String)>, question: &str) -> Value {
    let question = json!({"role": "user", "content": question});
    let mut bytes = question.to_string().len();
    let mut messages = VecDeque::from([question]);
    for (question, answer) in history.iter().rev() {
        let user = json!({"role": "user", "content": question});
        let assistant = json!({"role": "assistant", "content": answer});
        bytes += user.to_string().len() + assistant.to_string().len() + 2;
        if bytes > MAX_HISTORY_WIRE_BYTES {
            break;
        }
        messages.push_front(assistant);
        messages.push_front(user);
    }
    json!({"model": model, "messages": messages, "stream": false})
}

fn validate_response(value: Value) -> Result<(String, String), Error> {
    let invalid = || connection_failure(ErrorCode::OUTPUTINVALID);
    if !value.as_object().is_some_and(|object| object.len() == 2) {
        return Err(invalid());
    }
    let model = value["model"]
        .as_str()
        .filter(|model| {
            !model.trim().is_empty() && model.len() <= 256 && !model.chars().any(char::is_control)
        })
        .ok_or_else(invalid)?;
    let text = value["text"]
        .as_str()
        .filter(|text| !text.trim().is_empty() && text.len() <= MAX_TEXT_BYTES)
        .ok_or_else(invalid)?;
    Ok((model.to_owned(), text.to_owned()))
}

fn parse_id(value: &Value) -> Uuid {
    Uuid::parse_str(value.as_str().expect("validated ID")).expect("validated UUID")
}

fn connection_failure(code: ErrorCode) -> Error {
    let mut error = failure(code);
    error.message = match error.code {
        ErrorCode::INVALIDARGUMENT => "The model connection inputs are invalid.",
        ErrorCode::STALECONTEXT => "The model connection or turn is no longer current.",
        ErrorCode::CONFIRMATIONREQUIRED => "Review the model connection again before enabling it.",
        ErrorCode::PROVIDERNOTCONFIGURED | ErrorCode::DATAUNAVAILABLE | ErrorCode::WORKEREXITED => {
            "The selected text model is unavailable."
        }
        ErrorCode::OUTPUTINVALID => "The selected text model returned an invalid response.",
        ErrorCode::TIMEOUT => "The selected text model did not respond before the deadline.",
        ErrorCode::BUSY => "A text turn is already running.",
        ErrorCode::RESOURCELIMIT => "The model connection reached a request or response limit.",
        _ => return error,
    }
    .into();
    error
}

#[cfg(test)]
mod tests;
