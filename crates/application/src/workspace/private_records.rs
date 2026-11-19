use std::{
    collections::{HashMap, HashSet},
    sync::{Arc, Mutex, MutexGuard, mpsc},
    thread,
    time::Instant,
};

use scopehub_domain::{
    protocol::{
        Error, ErrorCode, PrivateRecordArchiveEntry, PrivateRecordBackupV1,
        PrivateRecordBackupV1Format, PrivateRecordChangeInput, PrivateRecordChangeScope11,
        PrivateRecordChangeSummary, PrivateRecordContent, PrivateRecordKind,
        PrivateRecordReadInput, PrivateRecordReadOutput, PrivateRecordResolveInput,
        PrivateRecordResolveInputDecision, PrivateRecordSummary, PrivateRecordTurnSpeaker,
        Revision,
    },
    wire::normalize_integers,
};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use uuid::Uuid;

use super::{
    MAX_REVISION, WorkspaceAccess, WorkspaceBinding, WorkspaceChange, WorkspaceReceipt,
    WorkspaceReplayKey, WorkspaceReply, WorkspaceStore, WorkspaceTransaction,
    notes::{CONFIRMATION_TTL, canonical_id},
    permits_scope, workspace_failure,
};
use crate::{Actor, Operation, OperationRegistry, failure};

pub const PRIVATE_RECORD_READ: &str = "privateRecord.read";
pub const PRIVATE_RECORD_CHANGE: &str = "privateRecord.change";
pub const PRIVATE_RECORD_EXPORT: &str = "privateRecord.exportBackup";
pub const PRIVATE_RECORD_OPERATIONS: [&str; 3] = [
    PRIVATE_RECORD_READ,
    PRIVATE_RECORD_CHANGE,
    PRIVATE_RECORD_EXPORT,
];

const MAX_RECORD_BYTES: usize = 8 * 1024;
const MAX_BACKUP_BYTES: usize = 512 * 1024;
const MAX_RECORDS: usize = 48;
const MAX_IDENTITIES: usize = 1024;
const MAX_PENDING_CHANGES: usize = 8;

pub struct PrivateRecordPlan {
    pub record_id: Option<String>,
    pub added: Vec<PrivateRecordArchiveEntry>,
    pub corrected: Option<PrivateRecordArchiveEntry>,
    pub deletions: Vec<String>,
    summary: PrivateRecordChangeSummary,
}

impl PrivateRecordPlan {
    fn new() -> Self {
        Self {
            record_id: None,
            added: Vec::new(),
            corrected: None,
            deletions: Vec::new(),
            summary: PrivateRecordChangeSummary {
                added: 0,
                corrected: 0,
                removed: 0,
                kept: 0,
                suppressed: 0,
                imported_deletions: 0,
            },
        }
    }

    pub fn can_supersede_content(&self) -> bool {
        self.corrected.is_some() || !self.deletions.is_empty()
    }
}

struct PendingChange {
    id: Uuid,
    principal_id: String,
    scope: PrivateRecordChangeScope11,
    expires: Instant,
    plan: PrivateRecordPlan,
    // Wake the expiry thread when this stage is replaced or dropped.
    _cancel_deadline: mpsc::Sender<()>,
}

#[derive(Default)]
pub(crate) struct PrivateRecordStaging(Arc<Mutex<HashMap<Uuid, PendingChange>>>);

impl PrivateRecordStaging {
    fn pending(&self) -> MutexGuard<'_, HashMap<Uuid, PendingChange>> {
        self.0
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    pub(super) fn observe(&self, revision: Revision) {
        self.pending()
            .retain(|_, stage| stage.scope.expected_workspace_revision >= revision);
    }

    pub(super) fn observe_local_access(&self, access: &WorkspaceAccess<'_>) {
        if access.disclose_workspace_state
            && access.operations.contains(&PRIVATE_RECORD_READ)
            && access.operations.contains(&PRIVATE_RECORD_CHANGE)
        {
            return;
        }
        self.pending()
            .retain(|_, stage| stage.principal_id != access.principal_id);
    }

    fn install(&self, stage: PendingChange, wait: mpsc::Receiver<()>) -> Result<(), Error> {
        let weak = Arc::downgrade(&self.0);
        let id = stage.id;
        let expires = stage.expires;
        let mut pending = self.pending();
        let same_request = |other: &PendingChange| {
            other.principal_id == stage.principal_id
                && other.scope.idempotency_key == stage.scope.idempotency_key
        };
        if pending.len() >= MAX_PENDING_CHANGES && !pending.values().any(same_request) {
            return Err(failure(ErrorCode::RESOURCELIMIT));
        }
        thread::Builder::new()
            .name("private-record-deadline".into())
            .spawn(move || {
                if matches!(
                    wait.recv_timeout(expires.saturating_duration_since(Instant::now())),
                    Err(mpsc::RecvTimeoutError::Timeout)
                ) && let Some(state) = weak.upgrade()
                {
                    state
                        .lock()
                        .unwrap_or_else(std::sync::PoisonError::into_inner)
                        .remove(&id);
                }
            })
            .map_err(|_| failure(ErrorCode::INTERNALERROR))?;
        // Only this registry owns the strong reference and the pending draft.
        pending.retain(|_, previous| !same_request(previous));
        pending.insert(id, stage);
        Ok(())
    }
}

impl Drop for PrivateRecordStaging {
    fn drop(&mut self) {
        self.pending().clear();
    }
}

impl<S: WorkspaceStore> OperationRegistry<S> {
    pub(super) fn execute_private_record(
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
        let request_id = request["request_id"]
            .as_str()
            .expect("validated request ID")
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
            self.private_record_stage.observe_local_access(access);
            if !access.disclose_workspace_state || !access.operations.contains(&PRIVATE_RECORD_READ)
            {
                return Err(failure(ErrorCode::PERMISSIONDENIED));
            }

            match op.name.as_str() {
                PRIVATE_RECORD_READ => self.read_private_records(request, &reply),
                PRIVATE_RECORD_EXPORT if access.operations.contains(&PRIVATE_RECORD_EXPORT) => {
                    let backup = self.port.inspect(|tx| {
                        self.private_record_stage.observe(tx.state()?.revision);
                        let backup = PrivateRecordBackupV1 {
                            format: PrivateRecordBackupV1Format::Value,
                            version: 1,
                            profile_binding: profile_binding(self.port.binding()),
                            records: tx.private_record_archive_entries()?,
                            deletions: tx.private_record_deletions()?,
                        };
                        check_backup_bytes(&backup)?;
                        Ok(backup)
                    })?;
                    reply.validated(json!({
                        "state": "completed", "operation": PRIVATE_RECORD_EXPORT, "result": backup,
                    }))
                }
                PRIVATE_RECORD_CHANGE => self.change_private_records(access, request, &reply),
                _ => Err(failure(ErrorCode::PERMISSIONDENIED)),
            }
        });
        result.or_else(|error| reply.failed(&private_record_failure(error.code)))
    }

    fn read_private_records(
        &self,
        mut request: Value,
        reply: &WorkspaceReply<'_>,
    ) -> Result<Value, Error> {
        let args: PrivateRecordReadInput =
            serde_json::from_value(request["intent"]["arguments"].take())
                .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
        let result = self.port.inspect(|tx| {
            let workspace_revision = tx.state()?.revision;
            self.private_record_stage.observe(workspace_revision);
            let (record, records) = match args.record_id {
                Some(id) => {
                    let record = tx
                        .private_record(&canonical_id(&id)?)?
                        .ok_or_else(|| failure(ErrorCode::DATAUNAVAILABLE))?;
                    (Some(record), vec![])
                }
                None => (None, tx.private_record_index()?),
            };
            Ok(PrivateRecordReadOutput {
                workspace_revision,
                record,
                records,
            })
        })?;
        reply.validated(json!({
            "state": "completed", "operation": PRIVATE_RECORD_READ, "result": result,
        }))
    }

    fn change_private_records(
        &mut self,
        access: &WorkspaceAccess<'_>,
        mut request: Value,
        reply: &WorkspaceReply<'_>,
    ) -> Result<Value, Error> {
        let mut scope: PrivateRecordChangeScope11 = serde_json::from_value(request["scope"].take())
            .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
        scope.idempotency_key = canonical_id(&scope.idempotency_key)?;
        let args: PrivateRecordChangeInput =
            serde_json::from_value(request["intent"]["arguments"].take())
                .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
        use PrivateRecordChangeInput::*;

        if let PrivateRecordResolveInput(args) = args {
            return self.resolve_private_record(access, scope, &args, reply);
        }
        if !access.operations.contains(&PRIVATE_RECORD_CHANGE) {
            return Err(failure(ErrorCode::PERMISSIONDENIED));
        }
        let key = replay_key(access, &scope);
        let plan = self.port.inspect(|tx| {
            if tx.replay(&key)?.is_some() {
                return Err(failure(ErrorCode::INVALIDARGUMENT));
            }
            let current = tx.state()?.revision;
            self.private_record_stage.observe(current);
            check_revision(current, scope.expected_workspace_revision)?;
            plan_change(tx, self.port.binding(), args)
        })?;
        let id = Uuid::new_v4();
        let preview = reply.validated(json!({
            "state": "confirmation_required", "operation": PRIVATE_RECORD_CHANGE,
            "challenge_id": id.to_string(),
            "expires_at_ms": reply.timestamp_ms + CONFIRMATION_TTL.as_millis() as i64,
            "summary": plan.summary,
        }))?;
        let (cancel_deadline, wait) = mpsc::channel();
        self.private_record_stage.install(
            PendingChange {
                id,
                principal_id: access.principal_id.into(),
                scope,
                expires: Instant::now() + CONFIRMATION_TTL,
                plan,
                _cancel_deadline: cancel_deadline,
            },
            wait,
        )?;
        Ok(preview)
    }

    fn resolve_private_record(
        &mut self,
        access: &WorkspaceAccess<'_>,
        scope: PrivateRecordChangeScope11,
        args: &PrivateRecordResolveInput,
        reply: &WorkspaceReply<'_>,
    ) -> Result<Value, Error> {
        let challenge =
            Uuid::parse_str(&args.challenge_id).map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
        let key = replay_key(access, &scope);
        let approving = args.decision == PrivateRecordResolveInputDecision::Approve;

        // The accepted invocation contains only the random challenge, revision
        // and decision. No title, body or content fingerprint enters replay.
        let mut semantic = Sha256::new();
        semantic.update(challenge.as_bytes());
        semantic.update(scope.expected_workspace_revision.to_be_bytes());
        semantic.update(args.decision.as_str().as_bytes());
        let digest: [u8; 32] = semantic.finalize().into();

        let (value, clean) = self.port.transact(|tx| {
            if let Some(saved) = tx.replay(&key)? {
                if saved.digest != digest {
                    return Err(failure(ErrorCode::INVALIDARGUMENT));
                }
                let target = tx.private_record_receipt_target(&saved.receipt.operation_id)?;
                return Ok((
                    receipt(reply, access, &saved.receipt, target.as_deref())?,
                    true,
                ));
            }
            if !access.operations.contains(&PRIVATE_RECORD_CHANGE) {
                return Err(failure(ErrorCode::PERMISSIONDENIED));
            }
            let state = tx.state()?;
            self.private_record_stage.observe(state.revision);
            if approving {
                check_revision(state.revision, scope.expected_workspace_revision)?;
            }
            let mut pending = self.private_record_stage.pending();
            let stage = pending
                .get(&challenge)
                .filter(|stage| {
                    stage.principal_id == key.principal_id
                        && stage.scope == scope
                        && Instant::now() < stage.expires
                })
                .ok_or_else(|| failure(ErrorCode::CONFIRMATIONREQUIRED))?;
            if !approving {
                pending.remove(&challenge);
                return Ok((
                    reply.failed(&private_record_failure(ErrorCode::CANCELLED))?,
                    false,
                ));
            }
            let saved = WorkspaceReceipt {
                operation_id: Uuid::new_v4().to_string(),
                revision: state.revision + 1,
                undoable: false,
                before: state.timeframe,
                after: state.timeframe,
            };
            let value = receipt(reply, access, &saved, stage.plan.record_id.as_deref())?;
            tx.record_private_records(
                &WorkspaceChange {
                    key,
                    digest,
                    receipt: saved,
                    actor: access.actor,
                    request_id: reply.request_id,
                    committed_at_ms: reply.timestamp_ms,
                    undoes: None,
                },
                &stage.plan,
            )?;
            Ok((value, stage.plan.can_supersede_content()))
        })?;

        if value["outcome"]["state"] == "committed" {
            self.private_record_stage
                .observe(scope.expected_workspace_revision + 1);
            self.note_restore
                .observe_commit(scope.expected_workspace_revision + 1);
        }
        if clean {
            self.port.clean_private_record_pages()?;
        }
        Ok(value)
    }
}

fn replay_key<'a>(
    access: &'a WorkspaceAccess<'_>,
    scope: &'a PrivateRecordChangeScope11,
) -> WorkspaceReplayKey<'a> {
    WorkspaceReplayKey {
        principal_id: access.principal_id,
        paired_client_id: None,
        operation: PRIVATE_RECORD_CHANGE,
        idempotency_key: &scope.idempotency_key,
    }
}

fn receipt(
    reply: &WorkspaceReply<'_>,
    access: &WorkspaceAccess<'_>,
    saved: &WorkspaceReceipt,
    target: Option<&str>,
) -> Result<Value, Error> {
    reply.validated(json!({
        "state": "committed", "operation": PRIVATE_RECORD_CHANGE,
        "receipt": {
            "operation_id": saved.operation_id,
            "workspace_id": access.workspace_id,
            "record_id": target,
            "committed_workspace_revision": saved.revision,
            "undoable": false,
        },
    }))
}

fn check_revision(current: Revision, expected: Revision) -> Result<(), Error> {
    if current != expected {
        return Err(failure(ErrorCode::STALECONTEXT));
    }
    if current >= MAX_REVISION {
        return Err(failure(ErrorCode::RESOURCELIMIT));
    }
    Ok(())
}

fn validate_content(content: &PrivateRecordContent) -> Result<PrivateRecordKind, Error> {
    use PrivateRecordContent::*;
    let (kind, filled) = match content {
        PrivateRecordConversation(exchange) => (
            PrivateRecordKind::Conversation,
            !exchange.title.trim().is_empty()
                && exchange
                    .turns
                    .iter()
                    .all(|turn| !turn.text.trim().is_empty())
                && exchange
                    .turns
                    .iter()
                    .any(|turn| turn.speaker == PrivateRecordTurnSpeaker::User)
                && exchange
                    .turns
                    .iter()
                    .any(|turn| turn.speaker == PrivateRecordTurnSpeaker::Assistant),
        ),
        PrivateRecordMemory(memory) => (
            PrivateRecordKind::Memory,
            !memory.title.trim().is_empty() && !memory.text.trim().is_empty(),
        ),
        PrivateRecordStrategy(strategy) => (
            PrivateRecordKind::Strategy,
            !strategy.title.trim().is_empty() && !strategy.text.trim().is_empty(),
        ),
    };
    if !filled {
        return Err(failure(ErrorCode::INVALIDARGUMENT));
    }
    check_bytes(serde_json::to_vec(content), MAX_RECORD_BYTES)?;
    Ok(kind)
}

fn check_bytes(encoded: serde_json::Result<Vec<u8>>, limit: usize) -> Result<(), Error> {
    let encoded = encoded.map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
    if encoded.len() > limit {
        return Err(failure(ErrorCode::RESOURCELIMIT));
    }
    Ok(())
}

fn check_backup_bytes(backup: &PrivateRecordBackupV1) -> Result<(), Error> {
    check_bytes(serde_json::to_vec(backup), MAX_BACKUP_BYTES)
}

fn profile_binding(binding: &WorkspaceBinding) -> String {
    let mut digest = Sha256::new();
    digest.update(b"ScopeHub/private-record-backup-v1\0");
    digest.update(binding.profile_id.as_bytes());
    digest.update([0]);
    digest.update(binding.workspace_id.as_bytes());
    hex::encode(digest.finalize())
}

fn require_current(record: Option<&PrivateRecordSummary>, expected: Revision) -> Result<(), Error> {
    let record = record.ok_or_else(|| failure(ErrorCode::DATAUNAVAILABLE))?;
    if record.revision != expected {
        return Err(failure(ErrorCode::STALECONTEXT));
    }
    Ok(())
}

fn plan_change(
    tx: &dyn WorkspaceTransaction,
    binding: &WorkspaceBinding,
    args: PrivateRecordChangeInput,
) -> Result<PrivateRecordPlan, Error> {
    use PrivateRecordChangeInput::*;
    let records = tx.private_record_index()?;
    let deletions: HashSet<_> = tx.private_record_deletions()?.into_iter().collect();
    let mut plan = PrivateRecordPlan::new();
    match args {
        PrivateRecordSaveInput(input) => {
            let kind = validate_content(&input.content)?;
            let id = canonical_id(&input.record_id)?;
            let current = records.iter().find(|record| record.record_id == id);
            let entry = PrivateRecordArchiveEntry {
                record_id: id.clone(),
                content: input.content,
            };
            plan.record_id = Some(id.clone());
            if let Some(revision) = input.expected_record_revision {
                require_current(current, revision)?;
                if current.is_some_and(|record| record.kind != kind) {
                    return Err(failure(ErrorCode::INVALIDARGUMENT));
                }
                plan.corrected = Some(entry);
                plan.summary.corrected = 1;
            } else {
                if current.is_some() || deletions.contains(&id) {
                    return Err(failure(ErrorCode::STALECONTEXT));
                }
                check_capacity(records.len() + 1, deletions.len())?;
                plan.added.push(entry);
                plan.summary.added = 1;
            }
        }
        PrivateRecordDeleteInput(input) => {
            let id = canonical_id(&input.record_id)?;
            let current = records.iter().find(|record| record.record_id == id);
            require_current(current, input.expected_record_revision)?;
            plan.record_id = Some(id.clone());
            plan.deletions.push(id);
            plan.summary.removed = 1;
        }
        PrivateRecordRestoreInput(input) => {
            plan = plan_restore(binding, records, deletions, input.backup)?;
        }
        PrivateRecordResolveInput(_) => unreachable!("resolve is dispatched separately"),
    }
    Ok(plan)
}

fn plan_restore(
    binding: &WorkspaceBinding,
    records: Vec<PrivateRecordSummary>,
    mut deletions: HashSet<String>,
    backup: PrivateRecordBackupV1,
) -> Result<PrivateRecordPlan, Error> {
    if backup.profile_binding != profile_binding(binding) {
        return Err(failure(ErrorCode::PERMISSIONDENIED));
    }
    check_backup_bytes(&backup)?;
    let mut plan = PrivateRecordPlan::new();
    let mut incoming_ids = HashSet::new();
    for id in backup.deletions {
        let id = canonical_id(&id)?;
        if !incoming_ids.insert(id.clone()) {
            return Err(failure(ErrorCode::INVALIDARGUMENT));
        }
        if deletions.insert(id.clone()) {
            plan.deletions.push(id);
        }
    }
    let records: HashSet<_> = records.into_iter().map(|record| record.record_id).collect();
    let removed = records.intersection(&deletions).count();
    incoming_ids.clear();
    for mut entry in backup.records {
        entry.record_id = canonical_id(&entry.record_id)?;
        if !incoming_ids.insert(entry.record_id.clone()) {
            return Err(failure(ErrorCode::INVALIDARGUMENT));
        }
        validate_content(&entry.content)?;
        if deletions.contains(&entry.record_id) {
            plan.summary.suppressed += 1;
        } else if !records.contains(&entry.record_id) {
            plan.added.push(entry);
        }
    }
    let kept = records.len() - removed;
    check_capacity(kept + plan.added.len(), deletions.len())?;
    plan.summary.kept = kept as u64;
    plan.summary.added = plan.added.len() as u64;
    plan.summary.removed = removed as u64;
    plan.summary.imported_deletions = plan.deletions.len() as u64;
    Ok(plan)
}

fn check_capacity(live: usize, deleted: usize) -> Result<(), Error> {
    // Reserve deletion-marker space for every live record.
    if live > MAX_RECORDS || live + deleted > MAX_IDENTITIES {
        return Err(failure(ErrorCode::RESOURCELIMIT));
    }
    Ok(())
}

fn private_record_failure(code: ErrorCode) -> Error {
    let mut error = workspace_failure(code);
    error.message = match error.code {
        ErrorCode::CANCELLED => "The private record change was cancelled.",
        ErrorCode::DATAUNAVAILABLE => "This private record is unavailable.",
        ErrorCode::CONFIRMATIONREQUIRED => "Review the current private record change first.",
        ErrorCode::RESOURCELIMIT => "The private record operation exceeds a local limit.",
        _ => return error,
    }
    .into();
    error
}
