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

use scopehub_domain::protocol::{Error, ErrorCode, Revision};
use serde_json::{Value, json};
use url::{Host, Url};
use uuid::Uuid;

use super::{
    WorkspaceAccess, WorkspaceReply, WorkspaceStore, evidence::ModelChartBinding, permits_scope,
};
use crate::{Actor, Operation, OperationRegistry, failure};

pub const AI_TEXT_CONNECTION: &str = "ai.textConnection";

const PROPOSAL_TTL: Duration = Duration::from_secs(300);
const MAX_HISTORY_PAIRS: usize = 4;
const MAX_HISTORY_WIRE_BYTES: usize = 64 * 1024;
const MAX_TURN_IDS: usize = 512;
const MAX_TEXT_BYTES: usize = 64 * 1024;
const CHART_TOOL: &str = "read_selected_chart";
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
    Ollama {
        destination: String,
        model: String,
        chart_tools: bool,
    },
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
    selection_revision: Option<Revision>,
    chart: Option<ModelChartBinding>,
    chart_evidence: Option<Value>,
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
                // The synchronous session may call the same registry to dispatch
                // a scoped chart read. Keep session ownership here across that call.
                let mut connection = std::mem::take(&mut self.text_connection);
                let result = connection.execute(
                    self,
                    &request["intent"]["arguments"],
                    parse_id(&request["request_id"]),
                );
                self.text_connection = connection;
                result
            });
        match result {
            Ok(result) => reply.validated(json!({
                "state": "completed", "operation": AI_TEXT_CONNECTION, "result": result,
            })),
            Err(error) => reply.failed(&error),
        }
    }

    pub(super) fn invalidate_text_chart(&mut self) {
        let mut connection = std::mem::take(&mut self.text_connection);
        if let Some(grant) = &mut connection.active {
            grant.recheck_chart(self);
        }
        self.text_connection = connection;
    }

    /// Trusted host handoff for a pending turn. Consume the work item after
    /// read-only revalidation of any bound chart selection.
    pub fn take_text_model_work(
        &mut self,
        access: &WorkspaceAccess<'_>,
        turn_id: Uuid,
    ) -> Result<Option<TextModelWork>, Error> {
        self.authorize_text_connection(access)?;
        self.invalidate_text_chart();
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
    fn execute<S: WorkspaceStore>(
        &mut self,
        registry: &mut OperationRegistry<S>,
        args: &Value,
        request_id: Uuid,
    ) -> Result<Value, Error> {
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
                        let turn_id = grant.send(
                            registry,
                            question,
                            request_id,
                            args["selection_revision"].as_u64(),
                        )?;
                        return Ok(json!({"action": action, "turn_id": turn_id.to_string()}));
                    }
                    "poll" => {
                        let id = parse_id(&args["turn_id"]);
                        grant.turn_mut(id)?;
                        grant.settle(registry);
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
            chart_tools: route["chart_tools"] == true,
        })
    }

    fn disclosure(&self) -> Value {
        match self {
            Self::Ollama {
                destination,
                model,
                chart_tools,
            } => json!({
                "kind": "ollama", "destination": destination, "model": model,
                "scope": if *chart_tools { "conversation_text_and_sample_closes" } else { "conversation_text" },
                "charge_basis": "endpoint_operator",
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

    fn send<S: WorkspaceStore>(
        &mut self,
        registry: &mut OperationRegistry<S>,
        question: &str,
        request_id: Uuid,
        selection_revision: Option<Revision>,
    ) -> Result<Uuid, Error> {
        if question.trim().is_empty() {
            return Err(connection_failure(ErrorCode::INVALIDARGUMENT));
        }
        if self.used_requests.contains(&request_id) {
            return self
                .turn
                .as_ref()
                .filter(|turn| {
                    turn.id == request_id
                        && turn.question == question
                        && turn.selection_revision == selection_revision
                })
                .map(|turn| turn.id)
                .ok_or_else(|| connection_failure(ErrorCode::STALECONTEXT));
        }
        self.settle(registry);
        if self.turn.as_ref().is_some_and(|turn| turn.state.is_none()) {
            return Err(connection_failure(ErrorCode::BUSY));
        }
        if self.used_requests.len() >= MAX_TURN_IDS {
            return Err(connection_failure(ErrorCode::RESOURCELIMIT));
        }
        let chart = match (&self.route, selection_revision) {
            (
                Route::Ollama {
                    destination,
                    chart_tools: true,
                    ..
                },
                Some(revision),
            ) => Some(registry.bind_model_chart(
                revision,
                format!("embedded-text:{}", self.id),
                destination,
            )?),
            (_, Some(_)) => return Err(connection_failure(ErrorCode::PERMISSIONDENIED)),
            (_, None) => None,
        };
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
            Route::Ollama {
                destination, model, ..
            } => {
                let mut request = outbound(model, &self.history, question);
                if chart.is_some() {
                    request["tools"] = json!([{
                        "type": "function", "function": {
                            "name": CHART_TOOL,
                            "description": "Read closing prices and timestamps from the currently selected chart interval.",
                            "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": false},
                        },
                    }]);
                }
                (
                    Some(TextModelWork {
                        destination: destination.clone(),
                        request,
                        cancelled: Arc::clone(&cancelled),
                        completion: sender,
                    }),
                    None,
                )
            }
        };
        self.used_requests.insert(request_id);
        self.turn = Some(Turn {
            id: request_id,
            question: question.to_string(),
            selection_revision,
            chart,
            chart_evidence: None,
            cancelled,
            work,
            completion,
            state,
        });
        Ok(request_id)
    }

    fn recheck_chart<S: WorkspaceStore>(&mut self, registry: &OperationRegistry<S>) {
        let Some(turn) = self.turn.as_mut().filter(|turn| turn.state.is_none()) else {
            return;
        };
        if let (Some(binding), Route::Ollama { destination, .. }) = (&turn.chart, &self.route)
            && let Err(error) = registry.check_model_chart(binding, destination)
        {
            turn.fail(error.code);
        }
    }

    fn settle<S: WorkspaceStore>(&mut self, registry: &mut OperationRegistry<S>) {
        self.recheck_chart(registry);
        let Some(turn) = self.turn.as_mut().filter(|turn| turn.state.is_none()) else {
            return;
        };
        let response = match turn.completion.try_recv() {
            Ok(result) => result.and_then(validate_response),
            Err(mpsc::TryRecvError::Empty) => return,
            Err(mpsc::TryRecvError::Disconnected) => {
                Err(connection_failure(ErrorCode::WORKEREXITED))
            }
        };
        match response {
            Ok(ModelResponse::Text { model, text }) => {
                let mut state = json!({"state": "completed", "model": model, "text": text});
                if let Some(evidence) = turn.chart_evidence.take() {
                    state["chart_evidence"] = evidence;
                }
                // Chart answers can quote authorized rows from a now-obsolete
                // selection. Keep them in the visible conversation only.
                if turn.chart.is_none() {
                    self.history.push_back((turn.question.clone(), text));
                    if self.history.len() > MAX_HISTORY_PAIRS {
                        self.history.pop_front();
                    }
                }
                turn.state = Some(state);
            }
            Ok(ModelResponse::ChartTool) => {
                if let (
                    Some(binding),
                    Route::Ollama {
                        destination, model, ..
                    },
                ) = (&turn.chart, &self.route)
                    && turn.chart_evidence.is_none()
                {
                    match registry.project_model_chart(binding, destination) {
                        Ok(evidence) => {
                            turn.continue_chart(destination, model, &self.history, evidence);
                        }
                        Err(error) => turn.fail(error.code),
                    }
                } else {
                    turn.fail(ErrorCode::OUTPUTINVALID);
                }
            }
            Err(error) => turn.fail(error.code),
        }
    }
}

impl Turn {
    fn fail(&mut self, code: ErrorCode) {
        self.cancelled.store(true, Ordering::Release);
        self.work = None;
        self.chart_evidence = None;
        let fault = connection_failure(code);
        self.state = Some(json!({
            "state": "failed",
            "error": {"code": fault.code, "message": fault.message, "retryable": fault.retryable},
        }));
    }

    fn continue_chart(
        &mut self,
        destination: &str,
        model: &str,
        history: &VecDeque<(String, String)>,
        evidence: Value,
    ) {
        let mut request = outbound(model, history, &self.question);
        let messages = request["messages"]
            .as_array_mut()
            .expect("projected messages");
        messages.push(json!({
            "role": "assistant", "content": "", "tool_calls": [{
                "id": "call_scopehub", "function": {"name": CHART_TOOL, "arguments": {}},
            }],
        }));
        messages.push(json!({
            "role": "tool", "tool_name": CHART_TOOL, "tool_call_id": "call_scopehub",
            "content": evidence.to_string(),
        }));
        let (sender, completion) = mpsc::sync_channel(1);
        self.completion = completion;
        self.work = Some(TextModelWork {
            destination: destination.into(),
            request,
            cancelled: Arc::clone(&self.cancelled),
            completion: sender,
        });
        self.chart_evidence = Some(evidence);
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

enum ModelResponse {
    Text { model: String, text: String },
    ChartTool,
}

fn validate_response(value: Value) -> Result<ModelResponse, Error> {
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
    if value["tool"] == CHART_TOOL {
        return Ok(ModelResponse::ChartTool);
    }
    let text = value["text"]
        .as_str()
        .filter(|text| !text.trim().is_empty() && text.len() <= MAX_TEXT_BYTES)
        .ok_or_else(invalid)?;
    Ok(ModelResponse::Text {
        model: model.to_owned(),
        text: text.to_owned(),
    })
}

fn parse_id(value: &Value) -> Uuid {
    Uuid::parse_str(value.as_str().expect("validated ID")).expect("validated UUID")
}

fn connection_failure(code: ErrorCode) -> Error {
    let mut error = failure(code);
    error.message = match error.code {
        ErrorCode::INVALIDARGUMENT => "The model connection inputs are invalid.",
        ErrorCode::STALECONTEXT => "The model connection or turn is no longer current.",
        ErrorCode::PERMISSIONDENIED => {
            "The selected chart is not entitled for this model connection."
        }
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
