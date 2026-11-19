use std::time::{Duration, Instant};

use scopehub_domain::protocol::{
    DecisionNoteAppendOutcomeInput, DecisionNoteConfirmation11, DecisionNoteConfirmation11Decision,
    DecisionNoteEvidenceRef, DecisionNoteOriginal, DecisionNoteOutcome, DecisionNoteReadCommand11,
    DecisionNoteSaveInput, DecisionNoteWriteScope11, Error, ErrorCode, Revision,
};
use scopehub_domain::wire::normalize_integers;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use uuid::Uuid;

use super::{
    DECISION_NOTE_EXPORT_BACKUP, DECISION_NOTE_PREVIEW_RESTORE, DECISION_NOTE_RESTORE_BACKUP,
    MAX_REVISION, WorkspaceAccess, WorkspaceReplayKey, WorkspaceReply, WorkspaceStore,
    WorkspaceTransaction, permits_scope, workspace_failure,
};
use crate::{Actor, Operation, OperationRegistry, failure};

pub const DECISION_NOTE_SAVE: &str = "decisionNote.save";
pub const DECISION_NOTE_READ: &str = "decisionNote.read";
pub const DECISION_NOTE_APPEND_OUTCOME: &str = "decisionNote.appendOutcome";
pub const DECISION_NOTE_DELETE: &str = "decisionNote.delete";
pub const DECISION_NOTE_OPERATIONS: [&str; 7] = [
    DECISION_NOTE_SAVE,
    DECISION_NOTE_READ,
    DECISION_NOTE_APPEND_OUTCOME,
    DECISION_NOTE_DELETE,
    DECISION_NOTE_EXPORT_BACKUP,
    DECISION_NOTE_PREVIEW_RESTORE,
    DECISION_NOTE_RESTORE_BACKUP,
];

pub(super) const CONFIRMATION_TTL: Duration = Duration::from_secs(300);

pub struct DecisionNoteReceipt {
    pub operation_id: String,
    pub note_id: String,
    pub revision: Revision,
}

pub struct DecisionNoteReplay {
    /// A missing digest marks a body-bearing request whose note was deleted.
    pub digest: Option<[u8; 32]>,
    pub receipt: DecisionNoteReceipt,
}

pub enum DecisionNoteMutation {
    Save(DecisionNoteOriginal),
    Append(DecisionNoteOutcome),
    Delete,
}

pub struct DecisionNoteChange<'a> {
    pub key: WorkspaceReplayKey<'a>,
    pub digest: [u8; 32],
    pub receipt: DecisionNoteReceipt,
    pub request_id: &'a str,
    pub committed_at_ms: i64,
    pub mutation: DecisionNoteMutation,
}

/// The issuing registry owns the session. Pending approval retains only the
/// scope and fingerprint in memory, with no private draft or durable grant.
pub(crate) struct PendingDecisionNoteConfirmation {
    id: Uuid,
    principal_id: String,
    operation: String,
    idempotency_key: String,
    digest: [u8; 32],
    expires: Instant,
}

enum NoteArguments {
    Save(DecisionNoteSaveInput),
    Append(DecisionNoteAppendOutcomeInput),
    Delete,
}

impl NoteArguments {
    fn from_validated(operation: &str, arguments: Value) -> Result<Self, Error> {
        let invalid = |_| failure(ErrorCode::INVALIDARGUMENT);
        match operation {
            DECISION_NOTE_SAVE => serde_json::from_value(arguments)
                .map(Self::Save)
                .map_err(invalid),
            DECISION_NOTE_APPEND_OUTCOME => serde_json::from_value(arguments)
                .map(Self::Append)
                .map_err(invalid),
            DECISION_NOTE_DELETE => Ok(Self::Delete),
            _ => unreachable!("registered note write"),
        }
    }

    fn digest(&self, scope: &DecisionNoteWriteScope11) -> Result<[u8; 32], Error> {
        let arguments = match self {
            Self::Save(input) => serde_json::to_value(input),
            Self::Append(input) => serde_json::to_value(input),
            Self::Delete => Ok(json!({})),
        }
        .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
        let bytes =
            serde_json::to_vec(&(&scope.target, scope.expected_workspace_revision, arguments))
                .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
        Ok(Sha256::digest(bytes).into())
    }

    fn mutation(
        self,
        tx: &dyn WorkspaceTransaction,
        receipt: &DecisionNoteReceipt,
    ) -> Result<DecisionNoteMutation, Error> {
        let note_id = &receipt.note_id;
        let decided_on = tx.note_decision_date(note_id)?;
        match self {
            Self::Save(input) => {
                if decided_on.is_some() || tx.note_is_deleted(note_id)? {
                    return Err(failure(ErrorCode::STALECONTEXT));
                }
                if !has_note_content(&input.thesis, &input.evidence_refs, &input.choice) {
                    return Err(failure(ErrorCode::INVALIDARGUMENT));
                }
                Ok(DecisionNoteMutation::Save(DecisionNoteOriginal {
                    note_id: note_id.clone(),
                    decided_on: input.decided_on,
                    thesis: input.thesis,
                    evidence_refs: input.evidence_refs,
                    choice: input.choice,
                }))
            }
            Self::Append(input) => {
                let decided_on = decided_on.ok_or_else(|| failure(ErrorCode::DATAUNAVAILABLE))?;
                if input.outcome.trim().is_empty() || input.observed_on <= decided_on {
                    return Err(failure(ErrorCode::INVALIDARGUMENT));
                }
                Ok(DecisionNoteMutation::Append(DecisionNoteOutcome {
                    outcome_id: receipt.operation_id.clone(),
                    observed_on: input.observed_on,
                    outcome: input.outcome,
                }))
            }
            Self::Delete => decided_on
                .map(|_| DecisionNoteMutation::Delete)
                .ok_or_else(|| failure(ErrorCode::DATAUNAVAILABLE)),
        }
    }
}

enum WriteResult {
    Preview(PendingDecisionNoteConfirmation, Value),
    Declined(Value),
    Replay(Value),
    Committed(Value),
}

impl<S: WorkspaceStore> OperationRegistry<S> {
    pub(super) fn execute_decision_note(
        &mut self,
        access: &WorkspaceAccess<'_>,
        op: &Operation,
        mut request: Value,
    ) -> Result<Value, Error> {
        if request["protocol_version"]
            .as_str()
            .is_some_and(|version| version != "1.1")
        {
            return Err(workspace_failure(ErrorCode::PROTOCOLMISMATCH));
        }
        if !op.wire_validators().0.is_valid(&request) {
            return Err(failure(ErrorCode::INVALIDARGUMENT));
        }
        normalize_integers(&mut request);
        // Keep correlation independent of any private input or adapter error.
        let request_id = request["request_id"]
            .as_str()
            .expect("validated request id")
            .to_owned();
        let reply = WorkspaceReply::new(op, &request_id)?;
        let result = op.authorize(access.actor).and_then(|()| {
            if !matches!(access.actor, Actor::User)
                || access.paired_client_id.is_some()
                || !permits_scope(access, self.port.binding())
                || request["scope"]["workspace_id"] != access.workspace_id
            {
                return Err(failure(ErrorCode::PERMISSIONDENIED));
            }
            self.note_restore.observe_local_access(access);
            if !access.disclose_workspace_state {
                return Err(failure(ErrorCode::PERMISSIONDENIED));
            }
            match op.name.as_str() {
                DECISION_NOTE_READ => self.read_decision_note(access, request, &reply),
                DECISION_NOTE_EXPORT_BACKUP => self.export_decision_note_backup(access, &reply),
                DECISION_NOTE_PREVIEW_RESTORE => {
                    self.preview_decision_note_restore(access, request, &reply)
                }
                DECISION_NOTE_RESTORE_BACKUP => {
                    self.resolve_decision_note_restore(access, request, &reply)
                }
                _ => self.write_decision_note(access, request, &reply),
            }
        });
        match result {
            Ok(value) => Ok(value),
            Err(error) => reply.failed(&note_failure(error.code)),
        }
    }

    fn read_decision_note(
        &self,
        access: &WorkspaceAccess<'_>,
        request: Value,
        reply: &WorkspaceReply<'_>,
    ) -> Result<Value, Error> {
        if !access.operations.contains(&DECISION_NOTE_READ) {
            return Err(failure(ErrorCode::PERMISSIONDENIED));
        }
        let request: DecisionNoteReadCommand11 =
            serde_json::from_value(request).map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
        let args = request.intent.arguments;
        let note_id = args.note_id.as_deref().map(canonical_id).transpose()?;
        if self.note_restore.is_pending() {
            let (state, _, _, _, _) = self.port.snapshot(None, false)?;
            self.note_restore.observe(state.revision);
        }
        let result = self.port.read_notes(note_id.as_deref(), args.offset)?;
        reply.validated(json!({
            "state": "completed", "operation": DECISION_NOTE_READ, "result": result,
        }))
    }

    fn write_decision_note(
        &mut self,
        access: &WorkspaceAccess<'_>,
        mut request: Value,
        reply: &WorkspaceReply<'_>,
    ) -> Result<Value, Error> {
        let invalid = |_| failure(ErrorCode::INVALIDARGUMENT);
        let mut scope: DecisionNoteWriteScope11 =
            serde_json::from_value(request["scope"].take()).map_err(invalid)?;
        let confirmation: Option<DecisionNoteConfirmation11> =
            serde_json::from_value(request["confirmation"].take()).map_err(invalid)?;
        scope.target.object_id = canonical_id(&scope.target.object_id)?;
        scope.idempotency_key = canonical_id(&scope.idempotency_key)?;
        let operation = &reply.operation.name;
        let arguments =
            NoteArguments::from_validated(operation, request["intent"]["arguments"].take())?;
        let digest = arguments.digest(&scope)?;
        let key = WorkspaceReplayKey {
            principal_id: access.principal_id,
            paired_client_id: access.paired_client_id,
            operation,
            idempotency_key: &scope.idempotency_key,
        };
        let write = self.port.transact(|tx| {
            if let Some(saved) = tx.note_replay(&key)? {
                if !access.operations.contains(&DECISION_NOTE_READ) {
                    return Err(failure(ErrorCode::PERMISSIONDENIED));
                }
                let saved_digest = saved
                    .digest
                    .ok_or_else(|| failure(ErrorCode::DATAUNAVAILABLE))?;
                if saved_digest != digest {
                    return Err(failure(ErrorCode::INVALIDARGUMENT));
                }
                return Ok(WriteResult::Replay(note_receipt(
                    reply,
                    access,
                    &saved.receipt,
                )?));
            }
            if !access.operations.contains(&operation.as_str()) {
                return Err(failure(ErrorCode::PERMISSIONDENIED));
            }

            if let Some(confirmation) = &confirmation {
                let id = Uuid::parse_str(&confirmation.challenge_id)
                    .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
                if !self.note_confirmation.as_ref().is_some_and(|pending| {
                    pending.id == id
                        && pending.principal_id == key.principal_id
                        && pending.operation == key.operation
                        && pending.idempotency_key == key.idempotency_key
                        && pending.digest == digest
                        && Instant::now() < pending.expires
                }) {
                    return Err(failure(ErrorCode::CONFIRMATIONREQUIRED));
                }
                if confirmation.decision == DecisionNoteConfirmation11Decision::Decline {
                    return Ok(WriteResult::Declined(
                        reply.failed(&note_failure(ErrorCode::CANCELLED))?,
                    ));
                }
            }

            let state = tx.state()?;
            self.note_restore.observe(state.revision);
            if state.revision != scope.expected_workspace_revision {
                return Err(failure(ErrorCode::STALECONTEXT));
            }
            if state.revision >= MAX_REVISION {
                return Err(failure(ErrorCode::RESOURCELIMIT));
            }
            let receipt = DecisionNoteReceipt {
                operation_id: Uuid::new_v4().to_string(),
                note_id: scope.target.object_id,
                revision: state.revision + 1,
            };
            let mutation = arguments.mutation(tx, &receipt)?;
            if confirmation.is_none() {
                tx.check_note_capacity(&receipt.note_id, &mutation)?;
                let pending = PendingDecisionNoteConfirmation {
                    id: Uuid::new_v4(),
                    principal_id: key.principal_id.into(),
                    operation: operation.clone(),
                    idempotency_key: key.idempotency_key.into(),
                    digest,
                    expires: Instant::now() + CONFIRMATION_TTL,
                };
                let value = reply.validated(json!({
                    "state": "confirmation_required", "operation": operation,
                    "challenge_id": pending.id.to_string(),
                    "expires_at_ms": reply.timestamp_ms + CONFIRMATION_TTL.as_millis() as i64,
                }))?;
                return Ok(WriteResult::Preview(pending, value));
            }
            let value = note_receipt(reply, access, &receipt)?;
            tx.record_note(&DecisionNoteChange {
                key,
                digest,
                receipt,
                request_id: reply.request_id,
                committed_at_ms: reply.timestamp_ms,
                mutation,
            })?;
            Ok(WriteResult::Committed(value))
        })?;
        if matches!(&write, WriteResult::Committed(_)) {
            self.note_confirmation = None;
            self.note_restore.clear();
        }
        match write {
            WriteResult::Preview(pending, value) => {
                self.note_confirmation = Some(pending);
                Ok(value)
            }
            WriteResult::Declined(value) => {
                self.note_confirmation = None;
                Ok(value)
            }
            WriteResult::Committed(value) | WriteResult::Replay(value) => {
                if operation == DECISION_NOTE_DELETE {
                    self.port.clean_deleted_note()?;
                }
                Ok(value)
            }
        }
    }
}

pub(super) fn canonical_id(value: &str) -> Result<String, Error> {
    Uuid::parse_str(value)
        .map(|id| id.to_string())
        .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))
}

pub(super) fn has_note_content(
    thesis: &str,
    refs: &[DecisionNoteEvidenceRef],
    choice: &str,
) -> bool {
    let filled = |text: &str| !text.trim().is_empty();
    filled(thesis)
        && filled(choice)
        && refs.iter().all(|reference| {
            filled(&reference.source_id)
                && filled(&reference.source_revision)
                && filled(&reference.locator)
        })
}

fn note_receipt(
    reply: &WorkspaceReply<'_>,
    access: &WorkspaceAccess<'_>,
    receipt: &DecisionNoteReceipt,
) -> Result<Value, Error> {
    reply.validated(json!({
        "state": "committed", "operation": reply.operation.name,
        "receipt": {
            "operation_id": receipt.operation_id,
            "workspace_id": access.workspace_id,
            "target": {"kind": "decision_note", "object_id": receipt.note_id},
            "committed_workspace_revision": receipt.revision,
            "undoable": false,
        },
    }))
}

fn note_failure(code: ErrorCode) -> Error {
    let mut error = workspace_failure(code);
    error.message = match error.code {
        ErrorCode::CANCELLED => "The decision-note change was declined.",
        ErrorCode::CONFIRMATIONREQUIRED => "Confirm the current decision-note change first.",
        ErrorCode::DATAUNAVAILABLE => "The decision note is unavailable.",
        ErrorCode::RESOURCELIMIT => "The decision-note operation exceeds a local limit.",
        _ => return error,
    }
    .into();
    error
}
