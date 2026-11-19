use std::{
    sync::LazyLock,
    time::{SystemTime, UNIX_EPOCH},
};

use scopehub_domain::{
    protocol::{
        ChartStyle, DecisionNoteDate, DecisionNoteReadOutput, Error, ErrorCode, EvidenceTarget11,
        MutationScope11, OperationReceipt11, PrivateRecordArchiveEntry, PrivateRecordEntry,
        PrivateRecordSummary, Revision, Workspace, WorkspaceObject11, WorkspaceObject11Kind,
    },
    wire::normalize_integers,
};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use uuid::Uuid;

use crate::{
    Actor, Operation, OperationRegistry, authorized_operation, failure, operation, validator,
};

mod backup;
mod evidence;
mod indicators;
mod layout;
mod notes;
mod private_records;
mod source;
pub use source::{FrozenWorkspaceSourceStatus, WorkspaceSourcePreference, WorkspaceSourceView};
mod text_connection;
pub(crate) use text_connection::TextConnection;
pub use text_connection::{AI_TEXT_CONNECTION, TextModelWork};

pub(crate) use indicators::IndicatorStage;
pub use indicators::{
    ACCEPT_INDICATOR_VERSION, ApprovedPython, CANCEL_INDICATOR_TASK, CREATE_INDICATOR_DRAFT,
    INDICATOR_CONFIRMATION, IndicatorLayerRef, IndicatorLayerUndo, IndicatorVersionRecord,
    MAX_ACTIVE_INDICATOR_LAYERS, READ_INDICATOR_SAMPLE_HISTORY, REMOVE_INDICATOR_LAYER,
    RUN_INDICATOR_PREVIEW, is_sample_indicator_preview,
};
pub(crate) use private_records::PrivateRecordStaging;
pub use private_records::{
    PRIVATE_RECORD_CHANGE, PRIVATE_RECORD_EXPORT, PRIVATE_RECORD_OPERATIONS, PRIVATE_RECORD_READ,
    PrivateRecordPlan,
};

pub(crate) use backup::DecisionNoteRestoreStaging;
pub use backup::{
    DECISION_NOTE_BACKUP_MAX_BYTES, DECISION_NOTE_BACKUP_MAX_DELETIONS,
    DECISION_NOTE_BACKUP_MAX_NOTES, DECISION_NOTE_BACKUP_MAX_OUTCOMES, DECISION_NOTE_EXPORT_BACKUP,
    DECISION_NOTE_PREVIEW_RESTORE, DECISION_NOTE_RESTORE_BACKUP, DecisionNoteBackupData,
    DecisionNoteOutcomeAddition, DecisionNoteRestorePlan,
};
pub use evidence::{
    ChartEvidenceRef, ChartEvidenceStatus, LocalChartAnalysisGrant, READ_CHART_PROJECTION,
    SELECT_EVIDENCE, evidence_operation_name,
};
pub use layout::{
    APPLY_LAYOUT, LAYOUT_ID, PREVIEW_LAYOUT, WorkspaceLayoutChange, WorkspaceLayoutSnapshot,
    WorkspaceLayoutState,
};
pub(crate) use notes::PendingDecisionNoteConfirmation;
pub use notes::{
    DECISION_NOTE_APPEND_OUTCOME, DECISION_NOTE_DELETE, DECISION_NOTE_OPERATIONS,
    DECISION_NOTE_READ, DECISION_NOTE_SAVE, DecisionNoteChange, DecisionNoteMutation,
    DecisionNoteReceipt, DecisionNoteReplay,
};

pub const SET_TIMEFRAME: &str = "chart.setTimeframe";
pub const UNDO: &str = "workspace.undo";
pub const LOCAL_WORKSPACE_OPERATIONS: &[&str] = &[
    AI_TEXT_CONNECTION,
    SET_TIMEFRAME,
    UNDO,
    PREVIEW_LAYOUT,
    APPLY_LAYOUT,
    SELECT_EVIDENCE,
    READ_CHART_PROJECTION,
    CREATE_INDICATOR_DRAFT,
    RUN_INDICATOR_PREVIEW,
    ACCEPT_INDICATOR_VERSION,
    REMOVE_INDICATOR_LAYER,
    INDICATOR_CONFIRMATION,
    CANCEL_INDICATOR_TASK,
    DECISION_NOTE_SAVE,
    DECISION_NOTE_READ,
    DECISION_NOTE_APPEND_OUTCOME,
    DECISION_NOTE_DELETE,
    DECISION_NOTE_EXPORT_BACKUP,
    DECISION_NOTE_PREVIEW_RESTORE,
    DECISION_NOTE_RESTORE_BACKUP,
    PRIVATE_RECORD_READ,
    PRIVATE_RECORD_CHANGE,
    PRIVATE_RECORD_EXPORT,
];
pub const MAIN_CHART_ID: &str = "main-chart";
const MAX_REVISION: Revision = 9_007_199_254_740_991;
static BOUND_MUTATION_INTENT: LazyLock<jsonschema::Validator> =
    LazyLock::new(|| validator("BoundMutationIntent11"));

/// The identity provisioned by the host when it opens a local profile.
#[derive(Debug, PartialEq, Eq)]
pub struct WorkspaceBinding {
    pub profile_id: String,
    pub workspace_id: String,
}

/// The current authenticated identity and grant, resolved by the trusted
/// transport for each dispatch. This type is deliberately outside the wire.
pub struct WorkspaceAccess<'a> {
    pub actor: Actor,
    pub profile_id: &'a str,
    pub principal_id: &'a str,
    pub paired_client_id: Option<&'a str>,
    pub workspace_id: &'a str,
    pub operations: &'a [&'a str],
    /// Effective disclosure of the complete unconfigured workspace and its
    /// receipts to this recipient. This conveys no market-source rights.
    pub disclose_workspace_state: bool,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Timeframe {
    Minute,
    FiveMinutes,
    Day,
}

impl Timeframe {
    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "1m" => Some(Self::Minute),
            "5m" => Some(Self::FiveMinutes),
            "1d" => Some(Self::Day),
            _ => None,
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Minute => "1m",
            Self::FiveMinutes => "5m",
            Self::Day => "1d",
        }
    }
}

#[derive(Clone, Copy, Debug)]
pub struct WorkspaceState {
    pub revision: Revision,
    pub selection_revision: Revision,
    pub timeframe: Timeframe,
}

pub struct WorkspaceSnapshot {
    pub workspace: Workspace,
    pub selection_revision: Revision,
    pub undo: Option<WorkspaceUndoTarget>,
    pub chart_evidence: Option<Value>,
    pub layouts: Option<WorkspaceLayoutSnapshot>,
    source: Option<WorkspaceSourceView>,
}

impl WorkspaceSnapshot {
    pub fn source_json(&self) -> Value {
        self.source
            .as_ref()
            .map_or(Value::Null, WorkspaceSourceView::to_json)
    }

    pub fn layouts_json(&self) -> Value {
        self.layouts.as_ref().map_or(Value::Null, |layouts| {
            json!({
                "active": layouts.active,
                "saved": layouts.saved,
                "undo": layouts.undo.as_ref().map(|target| json!({
                    "operation_id": target.operation_id,
                    "committed_workspace_revision": target.committed_workspace_revision,
                })),
            })
        })
    }
}

pub struct WorkspaceUndoTarget {
    pub operation_id: String,
    pub committed_workspace_revision: Revision,
}

#[derive(Clone, Copy)]
pub struct WorkspaceUndoAccess<'a> {
    pub actor: Actor,
    pub principal_id: &'a str,
    pub paired_client_id: Option<&'a str>,
}

pub struct WorkspaceReplayKey<'a> {
    pub principal_id: &'a str,
    pub paired_client_id: Option<&'a str>,
    pub operation: &'a str,
    pub idempotency_key: &'a str,
}

pub struct WorkspaceReceipt {
    pub operation_id: String,
    pub revision: Revision,
    pub undoable: bool,
    pub before: Timeframe,
    pub after: Timeframe,
}

pub struct WorkspaceReplay {
    pub digest: [u8; 32],
    pub receipt: WorkspaceReceipt,
}

pub struct WorkspaceChange<'a> {
    pub key: WorkspaceReplayKey<'a>,
    pub digest: [u8; 32],
    pub receipt: WorkspaceReceipt,
    pub actor: Actor,
    pub request_id: &'a str,
    pub committed_at_ms: i64,
    pub undoes: Option<&'a str>,
}

/// A transaction over the single workspace owned by this store.
pub trait WorkspaceTransaction {
    fn state(&self) -> Result<WorkspaceState, Error>;
    fn set_source_preference(&mut self, source: &WorkspaceSourcePreference) -> Result<(), Error>;
    fn layouts(&self) -> Result<WorkspaceLayoutState, Error>;
    fn layout_change(
        &self,
        operation_id: &str,
        actor: Actor,
    ) -> Result<Option<WorkspaceLayoutChange>, Error>;
    fn layout_undo_candidate(
        &self,
        operation_id: &str,
        access: WorkspaceUndoAccess<'_>,
    ) -> Result<Option<WorkspaceReceipt>, Error>;
    fn record_layout(
        &mut self,
        change: &WorkspaceChange<'_>,
        edit: &WorkspaceLayoutChange,
        saved_after: Option<&scopehub_domain::protocol::WorkspaceLayout11>,
    ) -> Result<(), Error>;
    fn private_record(&self, id: &str) -> Result<Option<PrivateRecordEntry>, Error>;
    fn private_record_index(&self) -> Result<Vec<PrivateRecordSummary>, Error>;
    fn private_record_deletions(&self) -> Result<Vec<String>, Error>;
    fn private_record_archive_entries(&self) -> Result<Vec<PrivateRecordArchiveEntry>, Error>;
    fn private_record_receipt_target(&self, operation_id: &str) -> Result<Option<String>, Error>;
    fn record_private_records(
        &mut self,
        change: &WorkspaceChange<'_>,
        plan: &PrivateRecordPlan,
    ) -> Result<(), Error>;
    fn chart_evidence(&self) -> Result<Option<ChartEvidenceRef>, Error>;
    fn set_chart_evidence(
        &mut self,
        selection_revision: Revision,
        target: Option<&ChartEvidenceRef>,
    ) -> Result<(), Error>;
    fn local_evidence_dataset(
        &self,
        revision_id: &str,
    ) -> Result<Option<crate::StoredLocalDataset>, Error>;
    fn local_evidence_display_allowed(&self, revision_id: &str) -> Result<bool, Error>;
    fn indicator_trusted_python_allowed(&self, _instrument_id: &str) -> Result<bool, Error> {
        Ok(false)
    }
    fn indicator_sample_allowed(&self, _instrument_id: &str) -> Result<bool, Error> {
        Ok(false)
    }
    fn indicator_compute_allowed(&self, _revision_id: &str) -> Result<bool, Error> {
        Ok(false)
    }
    fn indicator_retention_allowed(&self, revision_id: &str) -> Result<bool, Error>;
    fn active_indicator_count(&self) -> Result<u64, Error>;
    fn indicator_preview_accepted(&self, preview_id: &str) -> Result<bool, Error>;
    fn indicator_layer(&self, layer_id: &str) -> Result<Option<IndicatorLayerRef>, Error>;
    fn indicator_layers(&self, revision_id: &str) -> Result<Vec<IndicatorLayerRef>, Error>;
    fn indicator_operation_layer(
        &self,
        operation_id: &str,
    ) -> Result<Option<IndicatorLayerRef>, Error>;
    fn indicator_layer_undo(
        &self,
        operation_id: &str,
        principal_id: &str,
        layer_id: &str,
    ) -> Result<Option<IndicatorLayerUndo>, Error>;
    fn indicator_confirmation_receipt(
        &self,
        challenge_id: &str,
        principal_id: &str,
    ) -> Result<Option<(IndicatorLayerRef, WorkspaceReceipt)>, Error>;
    fn indicator_document(&self, layer_id: &str) -> Result<Option<Value>, Error>;
    fn record_indicator_version(
        &mut self,
        change: &WorkspaceChange<'_>,
        version: &IndicatorVersionRecord,
    ) -> Result<(), Error>;
    fn record_indicator_layer(
        &mut self,
        change: &WorkspaceChange<'_>,
        layer: &IndicatorLayerRef,
        active: bool,
    ) -> Result<(), Error>;
    fn chart_revision(&self) -> Result<Revision, Error>;
    fn replay(&self, key: &WorkspaceReplayKey<'_>) -> Result<Option<WorkspaceReplay>, Error>;
    fn undo_candidate(
        &self,
        operation_id: &str,
        principal_id: &str,
        paired_client_id: Option<&str>,
    ) -> Result<Option<WorkspaceReceipt>, Error>;
    fn record(&mut self, change: &WorkspaceChange<'_>) -> Result<(), Error>;
    fn note_decision_date(&self, note_id: &str) -> Result<Option<DecisionNoteDate>, Error>;
    fn note_is_deleted(&self, note_id: &str) -> Result<bool, Error>;
    fn note_replay(
        &self,
        key: &WorkspaceReplayKey<'_>,
    ) -> Result<Option<DecisionNoteReplay>, Error>;
    fn record_note(&mut self, change: &DecisionNoteChange<'_>) -> Result<(), Error>;
    fn check_note_capacity(
        &self,
        note_id: &str,
        mutation: &DecisionNoteMutation,
    ) -> Result<(), Error>;
    fn plan_note_restore(
        &self,
        backup: DecisionNoteBackupData,
    ) -> Result<DecisionNoteRestorePlan, Error>;
    fn record_note_restore(
        &mut self,
        change: &WorkspaceChange<'_>,
        plan: &DecisionNoteRestorePlan,
    ) -> Result<(), Error>;
}

pub struct StoredWorkspaceView {
    pub state: WorkspaceState,
    pub chart_undo: Option<WorkspaceUndoTarget>,
    pub chart_evidence: Option<ChartEvidenceStatus>,
    pub layouts: WorkspaceLayoutSnapshot,
    pub source: Option<WorkspaceSourceView>,
}

pub trait WorkspaceStore: Send {
    fn binding(&self) -> &WorkspaceBinding;
    /// Restore the state and its currently eligible undo in one read snapshot.
    fn snapshot(
        &self,
        undo: Option<WorkspaceUndoAccess<'_>>,
        disclose_chart_evidence: bool,
    ) -> Result<StoredWorkspaceView, Error>;
    /// Read the selection token, target and current source entitlement in a single snapshot.
    fn inspect<T>(
        &self,
        read: impl FnOnce(&dyn WorkspaceTransaction) -> Result<T, Error>,
    ) -> Result<T, Error>;
    fn read_notes(
        &self,
        note_id: Option<&str>,
        offset: u64,
    ) -> Result<DecisionNoteReadOutput, Error>;
    fn decision_note_backup(&self) -> Result<DecisionNoteBackupData, Error>;
    /// Remove superseded note pages from application-controlled database sidecars.
    fn clean_deleted_note(&mut self) -> Result<(), Error>;
    fn clean_private_record_pages(&mut self) -> Result<(), Error>;
    /// Commit only if the application successfully produces an authorized result.
    fn transact<T>(
        &mut self,
        work: impl FnOnce(&mut dyn WorkspaceTransaction) -> Result<T, Error>,
    ) -> Result<T, Error>;
}

fn permits_scope(access: &WorkspaceAccess<'_>, binding: &WorkspaceBinding) -> bool {
    access.profile_id == binding.profile_id
        && access.workspace_id == binding.workspace_id
        && !access.principal_id.is_empty()
        && access.paired_client_id != Some("")
}

fn chart_target() -> EvidenceTarget11 {
    EvidenceTarget11::WorkspaceObject11(Box::new(WorkspaceObject11 {
        kind: WorkspaceObject11Kind::Chart,
        object_id: MAIN_CHART_ID.into(),
    }))
}

fn workspace_projection(id: &str, revision: Revision, timeframe: Timeframe) -> Workspace {
    Workspace {
        id: id.into(),
        revision,
        timeframe: timeframe.as_str().into(),
        style: ChartStyle::Candlestick,
        instrument_id: None,
        dataset: None,
        visible_range: None,
        layers: vec![],
    }
}

impl<S: WorkspaceStore> OperationRegistry<S> {
    /// Restore the local workspace view for an authorized host. This is a
    /// composition entry, independent of any public context wire operation.
    pub fn workspace_snapshot(
        &self,
        access: &WorkspaceAccess<'_>,
    ) -> Result<WorkspaceSnapshot, Error> {
        if !matches!(access.actor, Actor::User | Actor::Ai)
            || !permits_scope(access, self.port.binding())
        {
            return Err(failure(ErrorCode::PERMISSIONDENIED));
        }
        if matches!(access.actor, Actor::User) && access.paired_client_id.is_none() {
            self.note_restore.observe_local_access(access);
            self.private_record_stage.observe_local_access(access);
        }
        if !access.disclose_workspace_state {
            return Err(failure(ErrorCode::PERMISSIONDENIED));
        }
        let undo = (access.operations.contains(&UNDO)
            && authorized_operation(access.actor, UNDO).is_ok())
        .then_some(WorkspaceUndoAccess {
            actor: access.actor,
            principal_id: access.principal_id,
            paired_client_id: access.paired_client_id,
        });
        let StoredWorkspaceView {
            state,
            chart_undo,
            chart_evidence,
            layouts,
            source,
        } = self.port.snapshot(
            undo,
            evidence::local_display(access) && access.operations.contains(&READ_CHART_PROJECTION),
        )?;
        self.note_restore.observe(state.revision);
        self.private_record_stage.observe(state.revision);
        Ok(WorkspaceSnapshot {
            workspace: workspace_projection(access.workspace_id, state.revision, state.timeframe),
            selection_revision: state.selection_revision,
            undo: chart_undo,
            source: source.filter(|_| evidence::local_display(access)),
            layouts: access
                .operations
                .contains(&PREVIEW_LAYOUT)
                .then_some(layouts),
            chart_evidence: chart_evidence.map(|status| match status {
                ChartEvidenceStatus::Available(target) => json!({
                    "revision_id": target.revision_id, "range": target.range,
                }),
                ChartEvidenceStatus::Unavailable => json!({"unavailable": true}),
            }),
        })
    }

    /// Execute a registered 1.1 workspace command from a trusted transport.
    /// A structurally valid command receives a correlated 1.1 outcome.
    pub fn execute_workspace(
        &mut self,
        access: &WorkspaceAccess<'_>,
        request: Value,
    ) -> Result<Value, Error> {
        self.dispatch_workspace(access, request, ChartMutationAccess::Command)
    }

    /// Admit a chart mutation intent from a trusted on-device AI integration.
    /// The host supplies current workspace disclosure and effective local
    /// analysis access independently of the wire intent.
    pub fn execute_chart_mutation_intent(
        &mut self,
        access: &WorkspaceAccess<'_>,
        grant: Option<&LocalChartAnalysisGrant<'_>>,
        mut intent: Value,
    ) -> Result<Value, Error> {
        if !matches!(access.actor, Actor::Ai) || access.paired_client_id.is_some() {
            return Err(failure(ErrorCode::PERMISSIONDENIED));
        }
        if !BOUND_MUTATION_INTENT.is_valid(&intent) {
            return Err(failure(ErrorCode::INVALIDARGUMENT));
        }
        if !matches!(
            intent["intent"]["operation"].as_str(),
            Some(SET_TIMEFRAME | UNDO | PREVIEW_LAYOUT | APPLY_LAYOUT)
        ) {
            return Err(failure(ErrorCode::UNSUPPORTEDCAPABILITY));
        }
        intent["type"] = json!("request");
        intent["protocol_version"] = json!("1.1");
        intent["message_id"] = json!(Uuid::new_v4().to_string());
        intent["request_id"] = json!(Uuid::new_v4().to_string());
        intent["timestamp_ms"] = json!(timestamp_ms()?);
        intent["task_id"] = Value::Null;
        self.dispatch_workspace(access, intent, ChartMutationAccess::LocalAnalysis(grant))
    }

    fn dispatch_workspace(
        &mut self,
        access: &WorkspaceAccess<'_>,
        mut request: Value,
        mutation_access: ChartMutationAccess<'_>,
    ) -> Result<Value, Error> {
        if evidence_operation_name(&request).is_some() {
            return self.execute_evidence_request(access, request);
        }
        if request["operation"] == INDICATOR_CONFIRMATION {
            if self.has_pending_python_confirmation(&request) {
                return self.resolve_python_preview(access, &request);
            }
            return self.resolve_indicator_confirmation(access, &request);
        }
        if request["operation"] == CANCEL_INDICATOR_TASK {
            return self.cancel_indicator_job(access, &request);
        }
        let name = request["intent"]["operation"]
            .as_str()
            .ok_or_else(|| failure(ErrorCode::INVALIDARGUMENT))?;
        match name {
            ACCEPT_INDICATOR_VERSION => {
                return self.execute_indicator_layer(access, ACCEPT_INDICATOR_VERSION, request);
            }
            REMOVE_INDICATOR_LAYER => {
                return self.execute_indicator_layer(access, REMOVE_INDICATOR_LAYER, request);
            }
            CREATE_INDICATOR_DRAFT => {
                return self.execute_indicator(access, CREATE_INDICATOR_DRAFT, request, None);
            }
            RUN_INDICATOR_PREVIEW => {
                return self.execute_indicator(access, RUN_INDICATOR_PREVIEW, request, None);
            }
            _ => {}
        }
        if DECISION_NOTE_OPERATIONS.contains(&name) {
            let result = self.execute_decision_note(access, operation(name)?, request)?;
            if let Some(revision) =
                result["outcome"]["receipt"]["committed_workspace_revision"].as_u64()
            {
                self.private_record_stage.observe(revision);
            }
            return Ok(result);
        }
        if PRIVATE_RECORD_OPERATIONS.contains(&name) {
            return self.execute_private_record(access, operation(name)?, request);
        }
        if name == AI_TEXT_CONNECTION {
            return self.execute_text_connection(access, operation(name)?, request);
        }
        if !matches!(name, SET_TIMEFRAME | UNDO | PREVIEW_LAYOUT | APPLY_LAYOUT) {
            return Err(failure(ErrorCode::UNSUPPORTEDCAPABILITY));
        }
        let op = operation(name)?;
        if request["protocol_version"]
            .as_str()
            .is_some_and(|version| version != "1.1")
        {
            return Err(workspace_failure(ErrorCode::PROTOCOLMISMATCH));
        }
        let (input, _) = op.wire_validators();
        if !input.is_valid(&request) {
            return Err(failure(ErrorCode::INVALIDARGUMENT));
        }
        normalize_integers(&mut request);
        let scope: MutationScope11 = serde_json::from_value(request["scope"].clone())
            .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
        let request_id = request["request_id"]
            .as_str()
            .expect("validated request id");
        let reply = WorkspaceReply::new(op, request_id)?;
        let is_layout = matches!(op.name.as_str(), PREVIEW_LAYOUT | APPLY_LAYOUT)
            || (op.name == UNDO && scope.target == layout::layout_target());
        let result = op.authorize(access.actor).and_then(|()| {
            if !permits_scope(access, self.port.binding())
                || scope.workspace_id != access.workspace_id
            {
                return Err(failure(ErrorCode::PERMISSIONDENIED));
            }
            let arguments = &request["intent"]["arguments"];
            if is_layout {
                if op.name == PREVIEW_LAYOUT {
                    return self.port.inspect(|tx| {
                        layout::preview(
                            tx,
                            access,
                            &scope,
                            arguments,
                            &reply,
                            self.layout_preview_secret,
                        )
                    });
                }
                return self.port.transact(|tx| {
                    layout::commit(
                        tx,
                        access,
                        &scope,
                        arguments,
                        &reply,
                        self.layout_preview_secret,
                    )
                });
            }
            self.port.transact(|tx| {
                if op.name == UNDO && matches!(&scope.target, EvidenceTarget11::WorkspaceObject11(target) if target.kind == WorkspaceObject11Kind::Layer) {
                    return indicators::undo_indicator_layer(tx, access, &scope, arguments, &reply);
                }
                execute_mutation(
                    tx,
                    access,
                    &scope,
                    arguments,
                    &reply,
                    &self.note_restore,
                    mutation_access,
                )
            })
        });
        match result {
            Ok(value) => {
                if let Some(revision) =
                    value["outcome"]["receipt"]["committed_workspace_revision"].as_u64()
                {
                    self.note_restore.observe_commit(revision);
                    self.private_record_stage.observe(revision);
                }
                Ok(value)
            }
            Err(error) if is_layout => reply.failed(&error),
            Err(error) => reply.failed(&workspace_failure(error.code)),
        }
    }
}

fn workspace_failure(code: ErrorCode) -> Error {
    let mut error = failure(code);
    let message = match error.code {
        ErrorCode::BUSY => "Workspace storage is busy.",
        ErrorCode::PROTOCOLMISMATCH => "Workspace commands require protocol 1.1.",
        ErrorCode::RESOURCELIMIT => "The workspace revision limit was reached.",
        ErrorCode::OUTPUTINVALID => "The workspace reply failed validation.",
        _ => return error,
    };
    error.message = message.into();
    error
}

struct WorkspaceReply<'a> {
    operation: &'a Operation,
    request_id: &'a str,
    message_id: Uuid,
    timestamp_ms: i64,
}

fn timestamp_ms() -> Result<i64, Error> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .ok()
        .and_then(|duration| i64::try_from(duration.as_millis()).ok())
        .ok_or_else(|| failure(ErrorCode::INTERNALERROR))
}

impl<'a> WorkspaceReply<'a> {
    fn new(operation: &'a Operation, request_id: &'a str) -> Result<Self, Error> {
        Ok(Self {
            operation,
            request_id,
            message_id: Uuid::new_v4(),
            timestamp_ms: timestamp_ms()?,
        })
    }

    fn validated(&self, outcome: Value) -> Result<Value, Error> {
        let value = json!({
            "protocol_version": "1.1",
            "type": "response",
            "message_id": self.message_id.to_string(),
            "timestamp_ms": self.timestamp_ms,
            "request_id": self.request_id,
            "outcome": outcome,
        });
        if !self.operation.wire_validators().1.is_valid(&value) {
            return Err(failure(ErrorCode::OUTPUTINVALID));
        }
        Ok(value)
    }

    fn failed(&self, fault: &Error) -> Result<Value, Error> {
        self.validated(json!({
            "state": "failed",
            "error": {"code": fault.code, "message": fault.message, "retryable": fault.retryable},
        }))
    }

    fn committed(&self, workspace_id: &str, receipt: &WorkspaceReceipt) -> Result<Value, Error> {
        self.validated(json!({
            "state": "committed",
            "operation": self.operation.name,
            "receipt": OperationReceipt11 {
                operation_id: receipt.operation_id.clone(),
                workspace_id: workspace_id.into(),
                target: chart_target(),
                committed_workspace_revision: receipt.revision,
                undoable: receipt.undoable,
            },
            "result": workspace_projection(workspace_id, receipt.revision, receipt.after),
        }))
    }
}

enum ChartMutationAccess<'a> {
    Command,
    LocalAnalysis(Option<&'a LocalChartAnalysisGrant<'a>>),
}

impl ChartMutationAccess<'_> {
    fn authorize_new_work(
        self,
        tx: &dyn WorkspaceTransaction,
        access: &WorkspaceAccess<'_>,
    ) -> Result<(), Error> {
        if !matches!(access.actor, Actor::Ai) {
            return Ok(());
        }
        match (self, tx.chart_evidence()?) {
            (Self::Command, None) => Ok(()),
            (Self::LocalAnalysis(Some(grant)), Some(target))
                if grant.principal_id == access.principal_id =>
            {
                evidence::authorize_analysis_target(tx, &target, grant)
            }
            _ => Err(failure(ErrorCode::PERMISSIONDENIED)),
        }
    }
}

fn execute_mutation(
    tx: &mut dyn WorkspaceTransaction,
    access: &WorkspaceAccess<'_>,
    scope: &MutationScope11,
    arguments: &Value,
    reply: &WorkspaceReply<'_>,
    staging: &DecisionNoteRestoreStaging,
    mutation_access: ChartMutationAccess<'_>,
) -> Result<Value, Error> {
    let operation = reply.operation;
    // Compute semantic identity independently of transport correlation.
    // v1.1 gives these two operations one named scalar argument each.
    let semantic_input = serde_json::to_vec(&(
        &scope.target,
        scope.expected_workspace_revision,
        scope.expected_selection_revision,
        arguments,
    ))
    .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
    let digest = Sha256::digest(semantic_input).into();
    let idempotency_key = Uuid::parse_str(&scope.idempotency_key)
        .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?
        .to_string();
    let key = WorkspaceReplayKey {
        principal_id: access.principal_id,
        paired_client_id: access.paired_client_id,
        operation: &operation.name,
        idempotency_key: &idempotency_key,
    };
    let replay = tx.replay(&key)?;
    if !access.disclose_workspace_state {
        return Err(failure(ErrorCode::PERMISSIONDENIED));
    }
    if let Some(saved) = replay {
        if saved.digest != digest {
            return Err(failure(ErrorCode::INVALIDARGUMENT));
        }
        // This reply describes the main chart effect and carries no selected
        // source fields. A later selection or loss of its analysis grant cannot
        // change that historical effect. Current workspace disclosure still applies.
        return reply.committed(access.workspace_id, &saved.receipt);
    }
    if !access.operations.contains(&operation.name.as_str()) {
        return Err(failure(ErrorCode::PERMISSIONDENIED));
    }
    if operation.confirmation_required {
        return Err(failure(ErrorCode::CONFIRMATIONREQUIRED));
    }

    let current = tx.state()?;
    staging.observe(current.revision);
    if scope.target != chart_target()
        || scope.expected_workspace_revision != current.revision
        || scope.expected_selection_revision != current.selection_revision
    {
        return Err(failure(ErrorCode::STALECONTEXT));
    }

    mutation_access.authorize_new_work(tx, access)?;

    let (after, undoes) = if operation.name == SET_TIMEFRAME {
        let timeframe = arguments["timeframe"]
            .as_str()
            .and_then(Timeframe::parse)
            .ok_or_else(|| failure(ErrorCode::UNSUPPORTEDCAPABILITY))?;
        (timeframe, None)
    } else {
        let operation_id = arguments["operation_id"]
            .as_str()
            .expect("validated undo id");
        let original = tx
            .undo_candidate(operation_id, access.principal_id, access.paired_client_id)?
            .ok_or_else(|| failure(ErrorCode::PERMISSIONDENIED))?;
        // The chart's last mutation must still be the candidate, even if later
        // chart edits returned to its value. Private notes have their own target.
        if original.revision != tx.chart_revision()? || original.after != current.timeframe {
            return Err(failure(ErrorCode::STALECONTEXT));
        }
        (original.before, Some(operation_id))
    };

    if current.revision >= MAX_REVISION {
        return Err(failure(ErrorCode::RESOURCELIMIT));
    }
    let receipt = WorkspaceReceipt {
        operation_id: Uuid::new_v4().to_string(),
        revision: current.revision + 1,
        undoable: operation.undoable,
        before: current.timeframe,
        after,
    };
    let value = reply.committed(access.workspace_id, &receipt)?;
    tx.record(&WorkspaceChange {
        key,
        digest,
        receipt,
        actor: access.actor,
        request_id: reply.request_id,
        committed_at_ms: reply.timestamp_ms,
        undoes,
    })?;
    Ok(value)
}
