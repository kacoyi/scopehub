//! Profile-bound declarative indicator commands and disposable preview jobs.

mod accepted;
mod sample;
pub(super) use accepted::undo_indicator_layer;
pub use accepted::{
    ACCEPT_INDICATOR_VERSION, INDICATOR_CONFIRMATION, IndicatorLayerRef, IndicatorLayerUndo,
    IndicatorVersionRecord, MAX_ACTIVE_INDICATOR_LAYERS, REMOVE_INDICATOR_LAYER,
};
pub use sample::{READ_INDICATOR_SAMPLE_HISTORY, is_sample_indicator_preview};

use std::{
    collections::VecDeque,
    sync::{
        Arc, LazyLock, Mutex, OnceLock,
        atomic::{AtomicBool, AtomicUsize, Ordering},
    },
    time::{Duration, Instant},
};

use scopehub_domain::{
    indicator::{BucketCalendar, IndicatorError, MAX_BARS, Program},
    protocol::{
        Bar, DeclarativeSource11, Error, ErrorCode, EvidenceTarget11, FrozenSnapshot,
        IndicatorProvenance, MutationScope11, OperationReceipt11, SnapshotPreview11,
    },
    wire::{max_message_bytes, normalize_integers},
};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use uuid::Uuid;

use super::{
    WorkspaceAccess, WorkspaceReply, WorkspaceStore, WorkspaceTransaction, chart_target,
    evidence::FrozenChart, permits_scope, timestamp_ms, workspace_failure,
};
use crate::{Actor, OperationRegistry, failure, history::snapshot_hash, operation, validator};

pub const CREATE_INDICATOR_DRAFT: &str = "indicator.createDraft";
pub const RUN_INDICATOR_PREVIEW: &str = "indicator.runPreview";
pub const CANCEL_INDICATOR_TASK: &str = "task.cancel";

const ENGINE_VERSION: &str = "scopehub-declarative-2";
const MAX_DRAFTS: usize = 16;
const MAX_JOBS: usize = 8;
const MAX_RUNNING: usize = 2;
const JOB_TTL: Duration = Duration::from_secs(300);
const COMPUTE_DEADLINE: Duration = Duration::from_secs(8);
static READY: LazyLock<jsonschema::Validator> = LazyLock::new(|| validator("PreviewReady11"));
static TERMINAL: LazyLock<jsonschema::Validator> =
    LazyLock::new(|| validator("TaskTerminalEvent11"));

struct Draft {
    id: String,
    key: String,
    identity: String,
    code_hash: String,
    source: DeclarativeSource11,
    receipt: Value,
    expires: Instant,
}

struct Job {
    id: String,
    key: String,
    identity: String,
    draft_id: String,
    source: FrozenSnapshot,
    scope: MutationScope11,
    event_id: String,
    started_at: i64,
    receipt: Value,
    expires: Instant,
    state: Arc<Mutex<JobResult>>,
    cancel: Arc<AtomicBool>,
}

enum JobResult {
    Running,
    Ready(Box<Preview>),
    Cancelled,
    Failed(ErrorCode),
}

struct Preview {
    snapshot: FrozenSnapshot,
    resolved_parameters: Value,
    warmup_from_ms: Option<i64>,
    calendar_hash: Option<String>,
    artifact: Value,
    code_hash: String,
    params_hash: String,
    id: String,
    delivered: bool,
}

impl Job {
    fn is_cancelled(&self) -> bool {
        self.cancel.load(Ordering::Acquire)
    }

    fn cancel(&self) {
        self.cancel.store(true, Ordering::Release);
        let mut result = self.state.lock().expect("indicator job state");
        // A computed result is still staged until the core publishes it.
        // Let cancellation win until that publication, even if evaluation ended.
        if matches!(&*result, JobResult::Running)
            || matches!(&*result, JobResult::Ready(preview) if !preview.delivered)
        {
            *result = JobResult::Cancelled;
        }
    }
}

#[derive(Default)]
pub(crate) struct IndicatorStage {
    drafts: VecDeque<Draft>,
    jobs: VecDeque<Job>,
    workers: Arc<AtomicUsize>,
    approval: Option<accepted::Approval>,
    session_id: OnceLock<String>,
}

impl Drop for IndicatorStage {
    fn drop(&mut self) {
        for job in &self.jobs {
            job.cancel();
        }
    }
}

impl IndicatorStage {
    fn prune(&mut self, now: Instant) {
        self.drafts.retain(|draft| draft.expires > now);
        self.jobs.retain(|job| {
            if job.expires > now {
                true
            } else {
                job.cancel();
                false
            }
        });
    }

    fn start(
        &self,
        job: &Job,
        input: Input,
        resolved: Program,
        code_hash: String,
    ) -> Result<(), Error> {
        if self.workers.load(Ordering::Acquire) >= MAX_RUNNING {
            return Err(failure(ErrorCode::BUSY));
        }
        let workers = WorkerSlot(self.workers.clone());
        workers.0.fetch_add(1, Ordering::AcqRel);
        let status = job.state.clone();
        let cancel = job.cancel.clone();
        std::thread::Builder::new()
            .name("scopehub-indicator-preview".into())
            .spawn(move || {
                let _workers = workers;
                let deadline = Instant::now() + COMPUTE_DEADLINE;
                let stopped = || cancel.load(Ordering::Relaxed) || Instant::now() >= deadline;
                let result = compute(input, resolved, code_hash, stopped)
                    .map(|preview| JobResult::Ready(Box::new(preview)))
                    .unwrap_or_else(|error| {
                        if Instant::now() >= deadline {
                            JobResult::Failed(ErrorCode::TIMEOUT)
                        } else {
                            JobResult::Failed(error.code)
                        }
                    });
                let mut state = status.lock().expect("indicator job state");
                if matches!(*state, JobResult::Running) {
                    *state = result;
                }
            })
            .map_err(|_| failure(ErrorCode::BUSY))?;
        Ok(())
    }
}

struct WorkerSlot(Arc<AtomicUsize>);
impl Drop for WorkerSlot {
    fn drop(&mut self) {
        self.0.fetch_sub(1, Ordering::AcqRel);
    }
}

impl<S: WorkspaceStore> OperationRegistry<S> {
    pub(super) fn execute_indicator(
        &mut self,
        access: &WorkspaceAccess<'_>,
        name: &'static str,
        mut request: Value,
        sample_history: Option<Value>,
    ) -> Result<Value, Error> {
        let op = operation(name)?;
        let (input, _) = op.wire_validators();
        if !input.is_valid(&request) {
            return Err(failure(ErrorCode::INVALIDARGUMENT));
        }
        normalize_integers(&mut request);
        let request_id = request["request_id"]
            .as_str()
            .expect("validated request ID");
        let reply = WorkspaceReply::new(op, request_id)?;
        let result = (|| {
            op.authorize(access.actor)?;
            self.authorize_indicator(access, name)?;
            let mut scope: MutationScope11 = serde_json::from_value(request["scope"].clone())
                .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
            if scope.workspace_id != access.workspace_id {
                return Err(failure(ErrorCode::PERMISSIONDENIED));
            }
            scope.idempotency_key = Uuid::parse_str(&scope.idempotency_key)
                .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?
                .to_string();
            let args = &request["intent"]["arguments"];
            let now = Instant::now();
            self.indicator_stage.prune(now);
            let identity = hash(&json!([scope, args]))?;
            if name == CREATE_INDICATOR_DRAFT {
                self.create_indicator_draft(&reply, &scope, args, identity, now)
            } else {
                self.run_indicator_preview(&reply, scope, args, identity, now, sample_history)
            }
        })();
        match result {
            Ok(response) => Ok(response),
            Err(error) => reply.failed(&workspace_failure(error.code)),
        }
    }

    /// The trusted composition may attach the page read by the fixed offline adapter.
    /// The public workspace command cannot include source bars or a schedule.
    pub fn execute_sample_indicator_preview(
        &mut self,
        access: &WorkspaceAccess<'_>,
        command: Value,
        page: Value,
    ) -> Result<Value, Error> {
        if !is_sample_indicator_preview(&command) {
            return Err(failure(ErrorCode::INVALIDARGUMENT));
        }
        self.execute_indicator(access, RUN_INDICATOR_PREVIEW, command, Some(page))
    }

    fn authorize_indicator(&self, access: &WorkspaceAccess<'_>, name: &str) -> Result<(), Error> {
        if !matches!(access.actor, Actor::User)
            || access.principal_id != access.profile_id
            || access.paired_client_id.is_some()
            || !permits_scope(access, self.port.binding())
            || !access.operations.contains(&name)
            || !access.disclose_workspace_state
        {
            return Err(failure(ErrorCode::PERMISSIONDENIED));
        }
        Ok(())
    }

    fn create_indicator_draft(
        &mut self,
        reply: &WorkspaceReply<'_>,
        scope: &MutationScope11,
        args: &Value,
        identity: String,
        now: Instant,
    ) -> Result<Value, Error> {
        if let Some(draft) = self
            .indicator_stage
            .drafts
            .iter()
            .find(|draft| draft.key == scope.idempotency_key)
        {
            return replay(reply, &draft.identity, &identity, &draft.receipt);
        }
        if args["language"] != "declarative" {
            return Err(failure(ErrorCode::UNSUPPORTEDCAPABILITY));
        }
        if scope.target != chart_target() {
            return Err(failure(ErrorCode::INVALIDARGUMENT));
        }
        self.port.inspect(|tx| check_context(tx, scope))?;
        let source: DeclarativeSource11 = serde_json::from_value(args["source"].clone())
            .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
        Program::prepare(source.clone(), &[]).map_err(evaluator_error)?;
        let code_hash = hash(args)?;
        let id = new_id();
        let outcome = outcome(
            CREATE_INDICATOR_DRAFT,
            scope,
            json!({"draft_id": id, "code_hash": code_hash}),
        );
        let response = reply.validated(outcome.clone())?;
        if self.indicator_stage.drafts.len() == MAX_DRAFTS {
            self.indicator_stage.drafts.pop_front();
        }
        self.indicator_stage.drafts.push_back(Draft {
            id,
            code_hash,
            key: scope.idempotency_key.clone(),
            identity,
            source,
            receipt: outcome,
            expires: now + JOB_TTL,
        });
        Ok(response)
    }

    fn run_indicator_preview(
        &mut self,
        reply: &WorkspaceReply<'_>,
        scope: MutationScope11,
        args: &Value,
        identity: String,
        now: Instant,
        sample_history: Option<Value>,
    ) -> Result<Value, Error> {
        if let Some(job) = self
            .indicator_stage
            .jobs
            .iter()
            .find(|job| job.key == scope.idempotency_key)
        {
            return replay(reply, &job.identity, &identity, &job.receipt);
        }
        if args.get("python_params").is_some() {
            return Err(failure(ErrorCode::UNSUPPORTEDCAPABILITY));
        }
        let args: SnapshotPreview11 = serde_json::from_value(args.clone())
            .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
        let draft = self
            .indicator_stage
            .drafts
            .iter()
            .find(|draft| draft.id == args.draft_id)
            .ok_or_else(|| failure(ErrorCode::STALECONTEXT))?;
        let resolved =
            Program::prepare(draft.source.clone(), &args.parameters).map_err(evaluator_error)?;
        let input = self.port.inspect(|tx| {
            freeze(
                tx,
                &scope,
                &args.snapshot,
                resolved.warmup_bars(),
                sample_history,
            )
        })?;
        let id = new_id();
        let receipt = outcome(RUN_INDICATOR_PREVIEW, &scope, json!({"task_id": id}));
        let response = reply.validated(receipt.clone())?;
        let job = Job {
            id,
            key: scope.idempotency_key.clone(),
            identity,
            draft_id: args.draft_id,
            source: args.snapshot,
            scope,
            event_id: new_id(),
            started_at: timestamp_ms()?,
            receipt,
            expires: now + JOB_TTL,
            state: Arc::new(Mutex::new(JobResult::Running)),
            cancel: Arc::new(AtomicBool::new(false)),
        };
        self.indicator_stage
            .start(&job, input, resolved, draft.code_hash.clone())?;
        if self.indicator_stage.jobs.len() == MAX_JOBS
            && let Some(oldest) = self.indicator_stage.jobs.pop_front()
        {
            oldest.cancel();
        }
        self.indicator_stage.jobs.push_back(job);
        Ok(response)
    }

    /// The trusted workspace transport can retrieve an outcome after the
    /// initiating request has returned. Repeated reads cannot execute code.
    pub fn indicator_preview_status(
        &mut self,
        access: &WorkspaceAccess<'_>,
        task_id: &str,
    ) -> Result<Value, Error> {
        self.authorize_indicator(access, RUN_INDICATOR_PREVIEW)?;
        self.indicator_stage.prune(Instant::now());
        let job = self
            .indicator_stage
            .jobs
            .iter()
            .find(|job| job.id == task_id)
            .ok_or_else(|| failure(ErrorCode::STALECONTEXT))?;
        let mut result = job.state.lock().expect("indicator job state");
        match &mut *result {
            JobResult::Running => Ok(json!({"status": "running", "task_id": task_id})),
            JobResult::Ready(preview) => {
                if job.is_cancelled() {
                    return Err(failure(ErrorCode::STALECONTEXT));
                }
                self.port.inspect(|tx| {
                    check_context(tx, &job.scope)?;
                    require_compute_right(tx, &job.source)
                })?;
                let mut event = event(job, "indicator.preview_ready");
                event["draft_id"] = json!(job.draft_id);
                event["preview_id"] = json!(preview.id);
                event["snapshot"] = json!(preview.snapshot);
                event["code_hash"] = json!(preview.code_hash);
                event["params_hash"] = json!(preview.params_hash);
                event["engine_version"] = json!(ENGINE_VERSION);
                event["artifact"] = preview.artifact.clone();
                if !READY.is_valid(&event) {
                    return Err(failure(ErrorCode::OUTPUTINVALID));
                }
                preview.delivered = true;
                Ok(json!({
                    "status": "ready", "task_id": task_id, "event": event,
                    "source_snapshot": job.source, "resolved_parameters": preview.resolved_parameters,
                    "warmup_from_ms": preview.warmup_from_ms, "calendar_hash": preview.calendar_hash,
                }))
            }
            _ => {
                let mut event = event(job, "task.terminal");
                event["outcome"] = terminal_outcome(&result);
                event["committed_effects"] = json!([]);
                if !TERMINAL.is_valid(&event) {
                    return Err(failure(ErrorCode::OUTPUTINVALID));
                }
                Ok(json!({"status": "terminal", "task_id": task_id, "event": event}))
            }
        }
    }

    pub(super) fn cancel_indicator_job(
        &mut self,
        access: &WorkspaceAccess<'_>,
        request: &Value,
    ) -> Result<Value, Error> {
        let op = operation(CANCEL_INDICATOR_TASK)?;
        let (input, output) = op.wire_validators();
        if !input.is_valid(request) {
            return Err(failure(ErrorCode::INVALIDARGUMENT));
        }
        let mut frame = json!({
            "protocol_version": "1.1", "type": "response", "operation": CANCEL_INDICATOR_TASK,
            "message_id": new_id(), "timestamp_ms": timestamp_ms()?, "request_id": request["request_id"],
        });
        let result: Result<Value, Error> = (|| {
            op.authorize(access.actor)?;
            self.authorize_indicator(access, CANCEL_INDICATOR_TASK)?;
            self.indicator_stage.prune(Instant::now());
            let job = self
                .indicator_stage
                .jobs
                .iter()
                .find(|job| request["task_id"] == job.id)
                .ok_or_else(|| failure(ErrorCode::STALECONTEXT))?;
            job.cancel();
            Ok(terminal_outcome(
                &job.state.lock().expect("indicator job state"),
            ))
        })();
        match result {
            Ok(outcome) => {
                frame["task_id"] = request["task_id"].clone();
                frame["outcome"] = outcome;
                frame["committed_effects"] = json!([]);
            }
            Err(error) => frame["error"] = fault(error.code),
        }
        if !output.is_valid(&frame) {
            return Err(failure(ErrorCode::OUTPUTINVALID));
        }
        Ok(frame)
    }
}

fn fault(code: ErrorCode) -> Value {
    let error = workspace_failure(code);
    json!({"code": error.code, "message": error.message, "retryable": error.retryable})
}

fn terminal_outcome(result: &JobResult) -> Value {
    match result {
        JobResult::Ready(_) => json!({"status": "completed"}),
        JobResult::Running | JobResult::Cancelled => json!({"status": "cancelled"}),
        JobResult::Failed(code) => json!({"status": "failed", "error": fault(code.clone())}),
    }
}

fn event(job: &Job, name: &str) -> Value {
    json!({
        "protocol_version": "1.1", "type": "event", "event": name,
        "message_id": job.event_id, "timestamp_ms": job.started_at,
        "task_id": job.id, "stream_id": job.id, "sequence": 0,
    })
}

fn hash(value: &Value) -> Result<String, Error> {
    serde_json::to_vec(value)
        .map(|bytes| hex::encode(Sha256::digest(bytes)))
        .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))
}

fn new_id() -> String {
    Uuid::new_v4().to_string()
}

fn outcome(name: &str, scope: &MutationScope11, result: Value) -> Value {
    json!({
        "state": "committed", "operation": name, "result": result,
        "receipt": OperationReceipt11 {
            operation_id: new_id(), workspace_id: scope.workspace_id.clone(),
            target: scope.target.clone(), committed_workspace_revision: scope.expected_workspace_revision,
            undoable: false,
        },
    })
}

fn replay(
    reply: &WorkspaceReply<'_>,
    recorded: &str,
    supplied: &str,
    outcome: &Value,
) -> Result<Value, Error> {
    if recorded != supplied {
        return Err(failure(ErrorCode::INVALIDARGUMENT));
    }
    reply.validated(outcome.clone())
}

fn check_context(tx: &dyn WorkspaceTransaction, scope: &MutationScope11) -> Result<(), Error> {
    let state = tx.state()?;
    if state.revision != scope.expected_workspace_revision
        || state.selection_revision != scope.expected_selection_revision
    {
        return Err(failure(ErrorCode::STALECONTEXT));
    }
    Ok(())
}

struct Input {
    snapshot: FrozenSnapshot,
    bars: Vec<Bar>,
    first_visible: usize,
    calendar: BucketCalendar,
    calendar_hash: Option<String>,
}

fn require_compute_right(
    tx: &dyn WorkspaceTransaction,
    source: &FrozenSnapshot,
) -> Result<(), Error> {
    let allowed = if source.series.provider_id == crate::DEMO_PROVIDER {
        tx.indicator_sample_allowed(&source.series.instrument_id)?
    } else {
        tx.indicator_compute_allowed(&source.dataset_id)?
    };
    if !allowed {
        return Err(failure(ErrorCode::PERMISSIONDENIED));
    }
    Ok(())
}

fn freeze(
    tx: &dyn WorkspaceTransaction,
    scope: &MutationScope11,
    source: &FrozenSnapshot,
    warmup: usize,
    sample_history: Option<Value>,
) -> Result<Input, Error> {
    check_context(tx, scope)?;
    let EvidenceTarget11::ChartInterval11(target) = &scope.target else {
        return Err(failure(ErrorCode::INVALIDARGUMENT));
    };
    if &target.snapshot != source {
        return Err(failure(ErrorCode::STALECONTEXT));
    }
    require_compute_right(tx, source)?;
    if target.range.from_ms >= target.range.to_ms
        || target.range.from_ms < source.from_ms
        || target.range.to_ms > source.to_ms
    {
        return Err(failure(ErrorCode::INVALIDARGUMENT));
    }
    if source.series.provider_id == crate::DEMO_PROVIDER {
        let page = sample_history.ok_or_else(|| failure(ErrorCode::SNAPSHOTUNAVAILABLE))?;
        return sample::freeze_sample(source, &target.range, page, warmup);
    }
    let dataset = tx
        .local_evidence_dataset(&source.dataset_id)?
        .ok_or_else(|| failure(ErrorCode::SNAPSHOTUNAVAILABLE))?;
    if !crate::local_publication::valid_records(
        &dataset.content["bars"],
        dataset.content["source_record_count"]
            .as_u64()
            .unwrap_or_default(),
        false,
    ) {
        return Err(failure(ErrorCode::OUTPUTINVALID));
    }
    let chart = FrozenChart::new(&dataset)?;
    if &chart.snapshot != source {
        return Err(failure(ErrorCode::SNAPSHOTUNAVAILABLE));
    }
    let visible = chart.interval(&target.range)?;
    let start = chart
        .bars
        .partition_point(|bar| bar.time_ms < target.range.from_ms);
    let first = start.saturating_sub(warmup);
    let bars = &chart.bars[first..start + visible.len()];
    if bars.len() > MAX_BARS
        || serde_json::to_vec(bars)
            .map_err(|_| failure(ErrorCode::OUTPUTINVALID))?
            .len()
            >= max_message_bytes()
    {
        return Err(failure(ErrorCode::RESOURCELIMIT));
    }
    let mut snapshot = FrozenSnapshot {
        from_ms: target.range.from_ms,
        to_ms: target.range.to_ms,
        warmup_bars: (start - first) as u64,
        content_hash: String::new(),
        ..source.clone()
    };
    let timezone = dataset.content["series"]["timezone"]
        .as_str()
        .ok_or_else(|| failure(ErrorCode::OUTPUTINVALID))?;
    snapshot.content_hash = snapshot_hash(&snapshot, timezone, bars);
    let period_ms = match source.series.timeframe.as_str() {
        "1m" => 60_000,
        "5m" => 300_000,
        "1d" => 86_400_000,
        _ => return Err(failure(ErrorCode::UNSUPPORTEDCAPABILITY)),
    };
    Ok(Input {
        snapshot,
        bars: bars.to_vec(),
        first_visible: start - first,
        calendar: BucketCalendar::unverified(period_ms).map_err(evaluator_error)?,
        calendar_hash: None,
    })
}

fn compute(
    input: Input,
    program: Program,
    code_hash: String,
    stopped: impl Fn() -> bool,
) -> Result<Preview, Error> {
    let resolved_parameters = program.resolved_parameters();
    let params_hash = hash(&resolved_parameters)?;
    let provenance = IndicatorProvenance {
        code_hash: code_hash.clone(),
        params_hash: params_hash.clone(),
        engine_version: ENGINE_VERSION.into(),
        dataset_id: input.snapshot.dataset_id.clone(),
        dataset_revision: input.snapshot.dataset_revision.clone(),
    };
    let result = program
        .evaluate_with_calendar(
            &input.bars,
            input.first_visible,
            &input.calendar,
            &stopped,
            provenance,
        )
        .map_err(evaluator_error)?;
    let result = serde_json::to_value(result).map_err(|_| failure(ErrorCode::OUTPUTINVALID))?;
    let artifact = json!({"storage": "inline", "result_hash": hash(&result)?, "result": result});
    // Reserve room for the event, the local metadata and its transport envelope.
    if artifact.to_string().len() > max_message_bytes() - 16 * 1024 {
        return Err(failure(ErrorCode::RESOURCELIMIT));
    }
    if stopped() {
        return Err(failure(ErrorCode::CANCELLED));
    }
    Ok(Preview {
        snapshot: input.snapshot,
        resolved_parameters,
        artifact,
        code_hash,
        params_hash,
        warmup_from_ms: (input.first_visible > 0).then(|| input.bars[0].time_ms),
        calendar_hash: input.calendar_hash,
        id: new_id(),
        delivered: false,
    })
}

fn evaluator_error(error: IndicatorError) -> Error {
    failure(match error {
        IndicatorError::Invalid => ErrorCode::INVALIDARGUMENT,
        IndicatorError::Limit => ErrorCode::RESOURCELIMIT,
        IndicatorError::Nonfinite => ErrorCode::OUTPUTINVALID,
        IndicatorError::Cancelled => ErrorCode::CANCELLED,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_aggregate_output_that_cannot_fit_the_negotiated_frame() {
        let snapshot: FrozenSnapshot = serde_json::from_value(json!({
            "series": {"instrument_id": "fixture", "provider_id": "scopehub.local-csv", "timeframe": "1m",
                "price_adjustment": "raw", "session_scope": "regular", "calendar_id": "fixture", "quantity_unit": "shares"},
            "dataset_id": new_id(), "dataset_revision": "fixture", "from_ms": 1_000_000_000_000_i64,
            "to_ms": 1_000_600_000_000_i64, "warmup_bars": 0, "as_of_ms": 1_000_600_000_000_i64,
            "content_hash": "0".repeat(64),
        })).unwrap();
        let bars = (0..10_000)
            .map(|i| Bar {
                time_ms: snapshot.from_ms + i * 60_000,
                trading_day: "2001-09-09".into(),
                open: 0.0,
                high: 0.0,
                low: 0.0,
                close: 0.0,
                volume: None,
                is_closed: true,
            })
            .collect();
        let outputs: Vec<_> = (0..16).map(|i| json!({
            "id": format!("series-{i}"), "kind": "line", "pane": "price", "style": {"color": "#123456", "width": 1},
            "value": {"op": "field", "field": "close"},
        })).collect();
        let source = serde_json::from_value(json!({"outputs": outputs, "parameters": []})).unwrap();
        let result = compute(
            Input {
                snapshot,
                bars,
                first_visible: 0,
                calendar: BucketCalendar::unverified(60_000).unwrap(),
                calendar_hash: None,
            },
            Program::prepare(source, &[]).unwrap(),
            "1".repeat(64),
            || false,
        );
        assert!(matches!(
            result,
            Err(Error {
                code: ErrorCode::RESOURCELIMIT,
                ..
            })
        ));
    }
}
