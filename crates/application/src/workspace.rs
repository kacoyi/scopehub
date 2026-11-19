use std::time::{SystemTime, UNIX_EPOCH};

use scopehub_domain::{
    protocol::{
        ChartStyle, Error, ErrorCode, EvidenceTarget11, MutationScope11, OperationReceipt11,
        Revision, Workspace, WorkspaceObject11, WorkspaceObject11Kind,
    },
    wire::normalize_integers,
};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use uuid::Uuid;

use crate::{Actor, Operation, OperationRegistry, authorized_operation, failure, operation};

pub const SET_TIMEFRAME: &str = "chart.setTimeframe";
pub const UNDO: &str = "workspace.undo";
pub const MAIN_CHART_ID: &str = "main-chart";
const MAX_REVISION: Revision = 9_007_199_254_740_991;

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
}

pub struct WorkspaceUndoTarget {
    pub operation_id: String,
    pub committed_workspace_revision: Revision,
}

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
    fn replay(&self, key: &WorkspaceReplayKey<'_>) -> Result<Option<WorkspaceReplay>, Error>;
    fn undo_candidate(
        &self,
        operation_id: &str,
        principal_id: &str,
        paired_client_id: Option<&str>,
    ) -> Result<Option<WorkspaceReceipt>, Error>;
    fn record(&mut self, change: &WorkspaceChange<'_>) -> Result<(), Error>;
}

pub trait WorkspaceStore: Send {
    fn binding(&self) -> &WorkspaceBinding;
    /// Restore the state and its currently eligible undo in one read snapshot.
    fn snapshot(
        &self,
        undo: Option<WorkspaceUndoAccess<'_>>,
    ) -> Result<(WorkspaceState, Option<WorkspaceUndoTarget>), Error>;
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
            || !access.disclose_workspace_state
        {
            return Err(failure(ErrorCode::PERMISSIONDENIED));
        }
        let undo = (access.operations.contains(&UNDO)
            && authorized_operation(access.actor, UNDO).is_ok())
        .then_some(WorkspaceUndoAccess {
            actor: access.actor,
            principal_id: access.principal_id,
            paired_client_id: access.paired_client_id,
        });
        let (state, undo) = self.port.snapshot(undo)?;
        Ok(WorkspaceSnapshot {
            workspace: workspace_projection(access.workspace_id, state.revision, state.timeframe),
            selection_revision: state.selection_revision,
            undo,
        })
    }

    /// Execute a registered 1.1 workspace command from a trusted transport.
    /// A structurally valid command receives a correlated 1.1 outcome.
    pub fn execute_workspace(
        &mut self,
        access: &WorkspaceAccess<'_>,
        mut request: Value,
    ) -> Result<Value, Error> {
        let name = request["intent"]["operation"]
            .as_str()
            .ok_or_else(|| failure(ErrorCode::INVALIDARGUMENT))?;
        if !matches!(name, SET_TIMEFRAME | UNDO) {
            return Err(failure(ErrorCode::UNSUPPORTEDCAPABILITY));
        }
        let op = operation(name)?;
        if request["protocol_version"]
            .as_str()
            .is_some_and(|version| version != "1.1")
        {
            return Err(workspace_failure(ErrorCode::PROTOCOLMISMATCH));
        }
        let (input, output) = op.wire_validators();
        if !input.is_valid(&request) {
            return Err(failure(ErrorCode::INVALIDARGUMENT));
        }
        normalize_integers(&mut request);
        let scope: MutationScope11 = serde_json::from_value(request["scope"].clone())
            .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
        let request_id = request["request_id"]
            .as_str()
            .expect("validated request id");
        let reply = MutationReply {
            operation: op,
            request_id,
            message_id: Uuid::new_v4(),
            timestamp_ms: SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .ok()
                .and_then(|duration| i64::try_from(duration.as_millis()).ok())
                .ok_or_else(|| failure(ErrorCode::INTERNALERROR))?,
        };
        let result = op.authorize(access.actor).and_then(|()| {
            if !permits_scope(access, self.port.binding())
                || scope.workspace_id != access.workspace_id
            {
                return Err(failure(ErrorCode::PERMISSIONDENIED));
            }
            let arguments = &request["intent"]["arguments"];
            self.port
                .transact(|tx| execute_mutation(tx, access, &scope, arguments, &reply))
        });
        match result {
            Ok(value) => Ok(value),
            Err(error) => {
                // Project only the catalog's static fault, never adapter details.
                let fault = workspace_failure(error.code);
                let value = reply.envelope(json!({
                    "state": "failed",
                    "error": {
                        "code": fault.code,
                        "message": fault.message,
                        "retryable": fault.retryable,
                    },
                }));
                if !output.is_valid(&value) {
                    return Err(failure(ErrorCode::OUTPUTINVALID));
                }
                Ok(value)
            }
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

struct MutationReply<'a> {
    operation: &'a Operation,
    request_id: &'a str,
    message_id: Uuid,
    timestamp_ms: i64,
}

impl MutationReply<'_> {
    fn envelope(&self, outcome: Value) -> Value {
        json!({
            "protocol_version": "1.1",
            "type": "response",
            "message_id": self.message_id.to_string(),
            "timestamp_ms": self.timestamp_ms,
            "request_id": self.request_id,
            "outcome": outcome,
        })
    }

    fn committed(&self, workspace_id: &str, receipt: &WorkspaceReceipt) -> Result<Value, Error> {
        let value = self.envelope(json!({
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
        }));
        if !self.operation.wire_validators().1.is_valid(&value) {
            return Err(failure(ErrorCode::OUTPUTINVALID));
        }
        Ok(value)
    }
}

fn execute_mutation(
    tx: &mut dyn WorkspaceTransaction,
    access: &WorkspaceAccess<'_>,
    scope: &MutationScope11,
    arguments: &Value,
    reply: &MutationReply<'_>,
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
        return reply.committed(access.workspace_id, &saved.receipt);
    }
    if !access.operations.contains(&operation.name.as_str()) {
        return Err(failure(ErrorCode::PERMISSIONDENIED));
    }
    if operation.confirmation_required {
        return Err(failure(ErrorCode::CONFIRMATIONREQUIRED));
    }

    let current = tx.state()?;
    if scope.target != chart_target()
        || scope.expected_workspace_revision != current.revision
        || scope.expected_selection_revision != current.selection_revision
    {
        return Err(failure(ErrorCode::STALECONTEXT));
    }

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
        // There is one durable chart field in this slice. Its last mutation
        // must still be the candidate, even if later edits returned to its value.
        if original.revision != current.revision || original.after != current.timeframe {
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
