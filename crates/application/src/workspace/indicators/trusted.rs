//! Stage local human approval and validate the separately owned Python result.

use std::{
    collections::HashSet,
    sync::{Arc, LazyLock, Mutex, atomic::AtomicBool},
    time::{Duration, Instant},
};

use scopehub_domain::{
    protocol::{
        Error, ErrorCode, FrozenSnapshot, IndicatorCompute11, MutationScope11,
        PythonSnapshotPreview11,
    },
    wire::max_message_bytes,
};
use serde_json::{Value, json};

use super::{
    COMPUTE_DEADLINE, Draft, Input, JOB_TTL, Job, JobResult, OperationRegistry, Preview,
    RUN_INDICATOR_PREVIEW, WorkspaceAccess, WorkspaceReply, WorkspaceStore, WorkspaceTransaction,
    check_context, failure, freeze, hash, new_id, operation, outcome, timestamp_ms, validator,
    workspace_failure,
};

pub const TRUSTED_PYTHON_ENGINE: &str = "scopehub-trusted-python-1";
const APPROVAL_TTL: Duration = Duration::from_secs(60);
static COMPUTE: LazyLock<jsonschema::Validator> = LazyLock::new(|| validator("IndicatorCompute11"));
static COMPUTED: LazyLock<jsonschema::Validator> =
    LazyLock::new(|| validator("IndicatorComputed11"));
static DECISION: LazyLock<jsonschema::Validator> =
    LazyLock::new(|| validator("TrustedConfirmationDecision11"));

pub struct ApprovedPython {
    pub request: IndicatorCompute11,
    pub cancelled: Arc<AtomicBool>,
}

pub(super) struct PendingPython {
    challenge: Value,
    scope: MutationScope11,
    source: FrozenSnapshot,
    input: Input,
    command_identity: String,
    deadline: Instant,
    draft_id: String,
    code_hash: String,
    code: Value,
    params: Value,
    params_hash: String,
}

pub(super) struct RunningPython {
    dispatch: Option<IndicatorCompute11>,
    request_id: String,
    snapshot: FrozenSnapshot,
    calendar_hash: Option<String>,
    visible_times: HashSet<i64>,
    code_hash: String,
    params_hash: String,
    params: Value,
    deadline: Instant,
}

impl PendingPython {
    pub(super) fn draft_id(&self) -> &str {
        &self.draft_id
    }

    pub(super) fn task_id(&self) -> &str {
        self.challenge["task_id"]
            .as_str()
            .expect("issued task identity")
    }

    pub(super) fn matches(&self, request: &Value) -> bool {
        self.challenge["challenge_id"] == request["challenge_id"]
    }

    pub(super) fn expired(&self, now: Instant) -> bool {
        now > self.deadline
    }
}

impl RunningPython {
    pub(super) fn expired(&self, now: Instant) -> bool {
        now > self.deadline
    }
}

impl<S: WorkspaceStore> OperationRegistry<S> {
    pub(super) fn stage_python_preview(
        &mut self,
        access: &WorkspaceAccess<'_>,
        reply: &WorkspaceReply<'_>,
        scope: MutationScope11,
        args: &Value,
        identity: String,
        sample_history: Option<Value>,
    ) -> Result<Value, Error> {
        let args: PythonSnapshotPreview11 = serde_json::from_value(args.clone())
            .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
        self.port
            .inspect(|tx| python_source_allowed(tx, &scope, &args.snapshot))?;

        if let Some(previous) = &self.indicator_stage.python_approval
            && previous.scope.idempotency_key == scope.idempotency_key
        {
            if previous.command_identity != identity {
                return Err(failure(ErrorCode::INVALIDARGUMENT));
            }
            return reply.validated(
                json!({"state": "awaiting_confirmation", "challenge": previous.challenge}),
            );
        }

        let Draft {
            source, code_hash, ..
        } = self
            .indicator_stage
            .drafts
            .iter()
            .find(|draft| draft.id == args.draft_id)
            .ok_or_else(|| failure(ErrorCode::STALECONTEXT))?;
        let code = source
            .as_python()
            .ok_or_else(|| failure(ErrorCode::INVALIDARGUMENT))?;
        let code = serde_json::to_value(code).map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
        let code_hash = code_hash.clone();
        let mut params = code["params"]
            .as_object()
            .ok_or_else(|| failure(ErrorCode::INVALIDARGUMENT))?
            .clone();
        params.extend(args.python_params);
        let params = Value::Object(params);
        let params_hash = hash(&params)?;
        // Python sees exactly the selected visible bars in this first inline slice.
        let input = self
            .port
            .inspect(|tx| freeze(tx, &scope, &args.snapshot, 0, sample_history))?;

        let task_id = new_id();
        let mut target =
            serde_json::to_value(&scope.target).map_err(|_| failure(ErrorCode::INTERNALERROR))?;
        target["snapshot"] = json!(input.snapshot);
        let challenge = json!({
            "challenge_id": new_id(), "request_id": reply.request_id, "task_id": task_id,
            "operation": RUN_INDICATOR_PREVIEW, "principal_id": access.principal_id,
            "session_id": self.indicator_stage.session_id.get_or_init(new_id),
            "workspace_id": scope.workspace_id, "target": target,
            "workspace_revision": scope.expected_workspace_revision,
            "selection_revision": scope.expected_selection_revision,
            "parameters_hash": params_hash,
            "expires_at_ms": timestamp_ms()? + APPROVAL_TTL.as_millis() as i64,
            "python": {
                "code_hash": code_hash, "parameters_hash": params_hash, "snapshot_hash": input.snapshot.content_hash,
                "trust_mode": "trusted_user_code_no_sandbox", "input_use": "trusted_code_permitted",
            },
        });
        let response =
            reply.validated(json!({"state": "awaiting_confirmation", "challenge": challenge}))?;
        self.indicator_stage.python_approval = Some(PendingPython {
            challenge,
            scope,
            source: args.snapshot,
            input,
            command_identity: identity,
            deadline: Instant::now() + APPROVAL_TTL,
            draft_id: args.draft_id,
            code_hash,
            code,
            params,
            params_hash,
        });
        Ok(response)
    }

    pub(crate) fn resolve_python_preview(
        &mut self,
        access: &WorkspaceAccess<'_>,
        request: &Value,
    ) -> Result<Value, Error> {
        if !DECISION.is_valid(request) {
            return Err(failure(ErrorCode::INVALIDARGUMENT));
        }
        let op = operation(RUN_INDICATOR_PREVIEW)?;
        let reply = WorkspaceReply::new(
            op,
            request["request_id"]
                .as_str()
                .expect("validated request ID"),
        )?;
        let result = (|| {
            self.authorize_indicator(access, RUN_INDICATOR_PREVIEW)?;
            let pending = self
                .indicator_stage
                .python_approval
                .take()
                .filter(|pending| pending.matches(request) && !pending.expired(Instant::now()))
                .ok_or_else(|| failure(ErrorCode::STALECONTEXT))?;
            // A selection is a one-run affordance. Decline or stale approval
            // consumes it as well as a successful approval.
            self.indicator_stage
                .drafts
                .retain(|draft| draft.id != pending.draft_id);
            if request["decision"] == "deny" {
                return Err(failure(ErrorCode::CANCELLED));
            }
            self.port
                .inspect(|tx| python_source_allowed(tx, &pending.scope, &pending.source))?;
            if self.indicator_stage.jobs.iter().any(|job| {
                job.python.is_some()
                    && matches!(
                        *job.state.lock().expect("indicator job state"),
                        JobResult::Running
                    )
            }) {
                return Err(failure(ErrorCode::BUSY));
            }
            let id = pending.challenge["task_id"]
                .as_str()
                .expect("issued task identity")
                .to_owned();
            let request_id = new_id();
            let now_ms = timestamp_ms()?;
            let compute = json!({
                "protocol_version": "1.1", "type": "request", "operation": "indicator.compute",
                "message_id": new_id(), "timestamp_ms": now_ms, "request_id": request_id, "task_id": id,
                "draft_id": pending.draft_id, "source": pending.code, "resolved_params": pending.params,
                "code_hash": pending.code_hash, "params_hash": pending.params_hash, "snapshot": pending.input.snapshot,
                "input": {"mode": "inline", "bars": pending.input.bars},
            });
            if compute.to_string().len() >= max_message_bytes() {
                return Err(failure(ErrorCode::RESOURCELIMIT));
            }
            if !COMPUTE.is_valid(&compute) {
                return Err(failure(ErrorCode::INVALIDARGUMENT));
            }
            let dispatch =
                serde_json::from_value(compute).map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
            let receipt = outcome(
                RUN_INDICATOR_PREVIEW,
                &pending.scope,
                json!({"task_id": id}),
            );
            let response = reply.validated(receipt.clone())?;
            self.indicator_stage.push_job(Job {
                id,
                key: pending.scope.idempotency_key.clone(),
                identity: pending.command_identity,
                draft_id: pending.draft_id,
                source: pending.source,
                scope: pending.scope,
                event_id: new_id(),
                started_at: now_ms,
                receipt,
                expires: Instant::now() + JOB_TTL,
                state: Arc::new(Mutex::new(JobResult::Running)),
                cancel: Arc::new(AtomicBool::new(false)),
                python: Some(RunningPython {
                    dispatch: Some(dispatch),
                    request_id,
                    snapshot: pending.input.snapshot,
                    calendar_hash: pending.input.calendar_hash,
                    params: pending.params,
                    visible_times: pending.input.bars.iter().map(|bar| bar.time_ms).collect(),
                    code_hash: pending.code_hash,
                    params_hash: pending.params_hash,
                    deadline: Instant::now() + COMPUTE_DEADLINE,
                }),
            });
            Ok(response)
        })();
        result.or_else(|error| reply.failed(&workspace_failure(error.code)))
    }

    /// Transfer exactly one previously approved request to the trusted host.
    pub fn take_approved_python(
        &mut self,
        access: &WorkspaceAccess<'_>,
        task_id: &str,
    ) -> Result<Option<ApprovedPython>, Error> {
        self.authorize_indicator(access, RUN_INDICATOR_PREVIEW)?;
        let Some(job) = self
            .indicator_stage
            .jobs
            .iter_mut()
            .find(|job| job.id == task_id)
        else {
            return Ok(None);
        };
        let Some(python) = &mut job.python else {
            return Ok(None);
        };
        let mut result = job.state.lock().expect("indicator job state");
        if !matches!(*result, JobResult::Running) {
            return Ok(None);
        }
        if let Err(error) = self
            .port
            .inspect(|tx| python_source_allowed(tx, &job.scope, &job.source))
        {
            python.dispatch = None;
            *result = JobResult::Failed(error.code);
            return Ok(None);
        }
        Ok(python.dispatch.take().map(|request| ApprovedPython {
            request,
            cancelled: job.cancel.clone(),
        }))
    }

    /// A host callback can finish only the matching running task. It has no
    /// authority to approve a new computation or publish a durable version.
    pub fn finish_trusted_python(
        &mut self,
        access: &WorkspaceAccess<'_>,
        task_id: &str,
        response: Result<Value, Error>,
    ) -> Result<(), Error> {
        self.authorize_indicator(access, RUN_INDICATOR_PREVIEW)?;
        let job = self
            .indicator_stage
            .jobs
            .iter()
            .find(|job| job.id == task_id)
            .ok_or_else(|| failure(ErrorCode::STALECONTEXT))?;
        let python = job
            .python
            .as_ref()
            .filter(|python| python.dispatch.is_none())
            .ok_or_else(|| failure(ErrorCode::STALECONTEXT))?;
        let mut state = job.state.lock().expect("indicator job state");
        if !matches!(*state, JobResult::Running) || job.is_cancelled() {
            return Ok(());
        }
        let result = (|| {
            self.port
                .inspect(|tx| python_source_allowed(tx, &job.scope, &job.source))?;
            if python.expired(Instant::now()) {
                return Err(failure(ErrorCode::TIMEOUT));
            }
            validate_worker_result(job, python, response?)
        })();
        *state = match result {
            Ok(preview) => JobResult::Ready(Box::new(preview)),
            Err(error) if error.code == ErrorCode::CANCELLED => JobResult::Cancelled,
            Err(error) => JobResult::Failed(error.code),
        };
        Ok(())
    }
}

pub(super) fn python_source_allowed(
    tx: &dyn WorkspaceTransaction,
    scope: &MutationScope11,
    source: &FrozenSnapshot,
) -> Result<(), Error> {
    check_context(tx, scope)?;
    if source.series.provider_id != crate::DEMO_PROVIDER
        || !tx.indicator_trusted_python_allowed(&source.series.instrument_id)?
    {
        return Err(failure(ErrorCode::PERMISSIONDENIED));
    }
    Ok(())
}

fn validate_worker_result(
    job: &Job,
    python: &RunningPython,
    frame: Value,
) -> Result<Preview, Error> {
    let invalid = || failure(ErrorCode::OUTPUTINVALID);
    if frame.to_string().len() >= max_message_bytes() - 16 * 1024 {
        return Err(failure(ErrorCode::RESOURCELIMIT));
    }
    if !COMPUTED.is_valid(&frame)
        || frame["task_id"] != job.id
        || frame["draft_id"] != job.draft_id
        || frame["request_id"] != python.request_id
        || frame["snapshot_hash"] != python.snapshot.content_hash
    {
        return Err(invalid());
    }
    let artifact = &frame["artifact"];
    let payload = &artifact["result"];
    let provenance = &payload["provenance"];
    if artifact["storage"] != "inline"
        || artifact["result_hash"] != hash(payload)?
        || provenance["code_hash"] != python.code_hash
        || provenance["params_hash"] != python.params_hash
        || provenance["dataset_id"] != python.snapshot.dataset_id
        || provenance["dataset_revision"] != python.snapshot.dataset_revision
        || provenance["engine_version"] != TRUSTED_PYTHON_ENGINE
    {
        return Err(invalid());
    }
    let mut ids = HashSet::new();
    for series in payload["series"].as_array().ok_or_else(invalid)? {
        if !ids.insert(series["id"].as_str().ok_or_else(invalid)?) {
            return Err(invalid());
        }
        let mut last = None;
        for point in series["points"].as_array().ok_or_else(invalid)? {
            let time = point["time_ms"].as_i64().ok_or_else(invalid)?;
            if last.is_some_and(|previous| previous >= time)
                || !python.visible_times.contains(&time)
            {
                return Err(invalid());
            }
            last = Some(time);
        }
    }
    for marker in payload["markers"].as_array().ok_or_else(invalid)? {
        if !marker["time_ms"]
            .as_i64()
            .is_some_and(|time| python.visible_times.contains(&time))
        {
            return Err(invalid());
        }
    }
    for region in payload["regions"].as_array().ok_or_else(invalid)? {
        let from = region["from_ms"].as_i64().ok_or_else(invalid)?;
        let to = region["to_ms"].as_i64().ok_or_else(invalid)?;
        if from >= to || from < python.snapshot.from_ms || to > python.snapshot.to_ms {
            return Err(invalid());
        }
    }
    Ok(Preview {
        snapshot: python.snapshot.clone(),
        resolved_parameters: python.params.clone(),
        warmup_from_ms: None,
        calendar_hash: python.calendar_hash.clone(),
        artifact: artifact.clone(),
        code_hash: python.code_hash.clone(),
        params_hash: python.params_hash.clone(),
        id: new_id(),
        delivered: false,
    })
}
