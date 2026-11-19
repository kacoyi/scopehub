use std::{
    collections::HashSet,
    sync::{Arc, Mutex, MutexGuard, mpsc},
    thread,
    time::Instant,
};

use scopehub_domain::protocol::{
    DecisionNoteBackupDeletion, DecisionNoteBackupEntry, DecisionNoteBackupV1,
    DecisionNoteBackupV1Format, DecisionNoteOriginal, DecisionNoteOutcome,
    DecisionNoteRestoreBackupInput, DecisionNoteRestoreBackupInputDecision,
    DecisionNoteRestoreScope11, DecisionNoteRestoreSummary, Error, ErrorCode, Revision,
};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use uuid::Uuid;

use super::{
    DECISION_NOTE_READ, MAX_REVISION, WorkspaceAccess, WorkspaceBinding, WorkspaceChange,
    WorkspaceReceipt, WorkspaceReplayKey, WorkspaceReply, WorkspaceStore,
    notes::{CONFIRMATION_TTL, canonical_id, has_note_content},
};
use crate::{OperationRegistry, failure};

pub const DECISION_NOTE_EXPORT_BACKUP: &str = "decisionNote.exportBackup";
pub const DECISION_NOTE_PREVIEW_RESTORE: &str = "decisionNote.previewRestore";
pub const DECISION_NOTE_RESTORE_BACKUP: &str = "decisionNote.restoreBackup";
pub const DECISION_NOTE_BACKUP_MAX_BYTES: usize = 512 * 1024;
pub const DECISION_NOTE_BACKUP_MAX_NOTES: usize = 256;
pub const DECISION_NOTE_BACKUP_MAX_DELETIONS: usize = 1_024;
pub const DECISION_NOTE_BACKUP_MAX_OUTCOMES: usize = 128;

/// The logical note projection produced by one SQLite read snapshot.
pub struct DecisionNoteBackupData {
    pub revision: Revision,
    pub notes: Vec<DecisionNoteBackupEntry>,
    pub deletions: Vec<DecisionNoteBackupDeletion>,
}

impl DecisionNoteBackupData {
    fn into_archive(self, binding: &WorkspaceBinding) -> DecisionNoteBackupV1 {
        DecisionNoteBackupV1 {
            format: DecisionNoteBackupV1Format::Value,
            version: 1,
            profile_binding: backup_binding(binding),
            captured_workspace_revision: self.revision,
            notes: self.notes,
            deletions: self.deletions,
        }
    }

    /// Admit only additions whose resulting state can still be exported in full.
    pub fn check_capacity(mut self, binding: &WorkspaceBinding) -> Result<(), Error> {
        if self.notes.len() > DECISION_NOTE_BACKUP_MAX_NOTES
            // Reserve a marker for each live note, including after a series of deletions.
            || self.notes.len() + self.deletions.len() > DECISION_NOTE_BACKUP_MAX_DELETIONS
            || self.notes.iter().any(|entry| entry.outcomes.len() > DECISION_NOTE_BACKUP_MAX_OUTCOMES)
        {
            return Err(failure(ErrorCode::RESOURCELIMIT));
        }
        // A later chart change can increase the encoded revision length without touching notes.
        self.revision = MAX_REVISION;
        check_archive_size(&self.into_archive(binding))
    }
}

/// The exact merge that can be applied at the previewed workspace revision.
/// Marked bodies and already-known deletions are omitted from this plan.
pub struct DecisionNoteRestorePlan {
    pub originals: Vec<DecisionNoteOriginal>,
    pub outcomes: Vec<DecisionNoteOutcomeAddition>,
    pub deletions: Vec<DecisionNoteBackupDeletion>,
    pub summary: DecisionNoteRestoreSummary,
}

pub struct DecisionNoteOutcomeAddition {
    pub note_id: String,
    pub outcomes: Vec<DecisionNoteOutcome>,
}

struct PendingDecisionNoteRestore {
    id: Uuid,
    principal_id: String,
    idempotency_key: String,
    expected_revision: Revision,
    expires: Instant,
    plan: DecisionNoteRestorePlan,
    // Dropping the stage wakes its deadline worker without keeping the plan alive.
    _cancel_deadline: mpsc::Sender<()>,
}

#[derive(Default)]
pub(crate) struct DecisionNoteRestoreStaging(Arc<Mutex<Option<PendingDecisionNoteRestore>>>);

impl DecisionNoteRestoreStaging {
    fn pending(&self) -> MutexGuard<'_, Option<PendingDecisionNoteRestore>> {
        self.0
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    pub(super) fn is_pending(&self) -> bool {
        self.pending().is_some()
    }

    pub(super) fn clear(&self) {
        self.pending().take();
    }

    pub(super) fn observe(&self, revision: Revision) {
        let mut pending = self.pending();
        if pending
            .as_ref()
            .is_some_and(|stage| stage.expected_revision != revision)
        {
            pending.take();
        }
    }

    pub(super) fn observe_commit(&self, revision: Revision) {
        let mut pending = self.pending();
        // A replay can expose an older receipt while a current stage is still valid.
        if pending
            .as_ref()
            .is_some_and(|stage| stage.expected_revision < revision)
        {
            pending.take();
        }
    }

    pub(super) fn observe_local_access(&self, access: &WorkspaceAccess<'_>) {
        if !access.disclose_workspace_state
            || ![DECISION_NOTE_READ, DECISION_NOTE_RESTORE_BACKUP]
                .iter()
                .all(|operation| access.operations.contains(operation))
        {
            let mut pending = self.pending();
            if pending
                .as_ref()
                .is_some_and(|stage| stage.principal_id == access.principal_id)
            {
                pending.take();
            }
        }
    }

    fn install(
        &self,
        id: Uuid,
        principal_id: String,
        scope: DecisionNoteRestoreScope11,
        expires: Instant,
        plan: DecisionNoteRestorePlan,
    ) -> Result<(), Error> {
        let (cancel_deadline, wait) = mpsc::channel::<()>();
        let weak = Arc::downgrade(&self.0);
        let mut pending = self.pending();
        thread::Builder::new()
            .name("decision-note-restore-deadline".into())
            .spawn(move || {
                if matches!(
                    wait.recv_timeout(expires.saturating_duration_since(Instant::now())),
                    Err(mpsc::RecvTimeoutError::Timeout)
                ) && let Some(state) = weak.upgrade()
                {
                    let mut stage = state
                        .lock()
                        .unwrap_or_else(std::sync::PoisonError::into_inner);
                    if stage.as_ref().is_some_and(|stage| stage.id == id) {
                        stage.take();
                    }
                }
            })
            .map_err(|_| failure(ErrorCode::INTERNALERROR))?;
        *pending = Some(PendingDecisionNoteRestore {
            id,
            principal_id,
            idempotency_key: scope.idempotency_key,
            expected_revision: scope.expected_workspace_revision,
            expires,
            plan,
            _cancel_deadline: cancel_deadline,
        });
        Ok(())
    }
}

impl Drop for DecisionNoteRestoreStaging {
    fn drop(&mut self) {
        self.clear();
    }
}

enum RestoreResult {
    Declined(Value),
    Replay(Value),
    Committed(Value),
}

impl<S: WorkspaceStore> OperationRegistry<S> {
    pub(super) fn export_decision_note_backup(
        &self,
        access: &WorkspaceAccess<'_>,
        reply: &WorkspaceReply<'_>,
    ) -> Result<Value, Error> {
        require_operations(access, &[DECISION_NOTE_READ, DECISION_NOTE_EXPORT_BACKUP])?;
        let data = self.port.decision_note_backup()?;
        self.note_restore.observe(data.revision);
        let backup = data.into_archive(self.port.binding());
        check_archive_size(&backup)?;
        reply.validated(json!({
            "state": "completed", "operation": DECISION_NOTE_EXPORT_BACKUP, "result": backup,
        }))
    }

    pub(super) fn preview_decision_note_restore(
        &mut self,
        access: &WorkspaceAccess<'_>,
        mut request: Value,
        reply: &WorkspaceReply<'_>,
    ) -> Result<Value, Error> {
        require_operations(
            access,
            &[
                DECISION_NOTE_READ,
                DECISION_NOTE_PREVIEW_RESTORE,
                DECISION_NOTE_RESTORE_BACKUP,
            ],
        )?;
        let scope = parse_scope(request["scope"].take())?;
        let backup: DecisionNoteBackupV1 =
            serde_json::from_value(request["intent"]["arguments"]["backup"].take())
                .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
        let archive = validated_archive(backup, self.port.binding())?;
        let key = restore_key(access, &scope);
        let plan = self.port.transact(|tx| {
            if tx.replay(&key)?.is_some() {
                return Err(failure(ErrorCode::INVALIDARGUMENT));
            }
            let revision = tx.state()?.revision;
            self.note_restore.observe(revision);
            check_revision(revision, scope.expected_workspace_revision)?;
            tx.plan_note_restore(archive)
        })?;
        let id = Uuid::new_v4();
        let value = reply.validated(json!({
            "state": "confirmation_required", "operation": DECISION_NOTE_PREVIEW_RESTORE,
            "stage_id": id.to_string(),
            "expires_at_ms": reply.timestamp_ms + CONFIRMATION_TTL.as_millis() as i64,
            "summary": plan.summary,
        }))?;
        self.note_restore.install(
            id,
            access.principal_id.into(),
            scope,
            Instant::now() + CONFIRMATION_TTL,
            plan,
        )?;
        Ok(value)
    }

    pub(super) fn resolve_decision_note_restore(
        &mut self,
        access: &WorkspaceAccess<'_>,
        mut request: Value,
        reply: &WorkspaceReply<'_>,
    ) -> Result<Value, Error> {
        let scope = parse_scope(request["scope"].take())?;
        let args: DecisionNoteRestoreBackupInput =
            serde_json::from_value(request["intent"]["arguments"].take())
                .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
        let stage_id =
            Uuid::parse_str(&args.stage_id).map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
        let key = restore_key(access, &scope);
        // Replay has no dependency on the selected file or any private body.
        let mut semantic = Sha256::new();
        semantic.update(stage_id.as_bytes());
        semantic.update(scope.expected_workspace_revision.to_be_bytes());
        semantic.update(args.decision.as_str().as_bytes());
        let digest = semantic.finalize().into();

        let outcome = self.port.transact(|tx| {
            if let Some(saved) = tx.replay(&key)? {
                require_operations(access, &[DECISION_NOTE_READ])?;
                if saved.digest != digest {
                    return Err(failure(ErrorCode::INVALIDARGUMENT));
                }
                return Ok(RestoreResult::Replay(restore_receipt(
                    reply,
                    access,
                    &saved.receipt,
                )?));
            }
            require_operations(access, &[DECISION_NOTE_READ, DECISION_NOTE_RESTORE_BACKUP])?;
            let pending = self.note_restore.pending();
            let stage = pending
                .as_ref()
                .filter(|stage| {
                    stage.id == stage_id
                        && stage.principal_id == access.principal_id
                        && stage.idempotency_key == key.idempotency_key
                        && stage.expected_revision == scope.expected_workspace_revision
                        && Instant::now() < stage.expires
                })
                .ok_or_else(|| failure(ErrorCode::CONFIRMATIONREQUIRED))?;
            if args.decision == DecisionNoteRestoreBackupInputDecision::Decline {
                let mut error = failure(ErrorCode::CANCELLED);
                error.message = "The decision-note restore was declined.".into();
                return Ok(RestoreResult::Declined(reply.failed(&error)?));
            }
            let state = tx.state()?;
            check_revision(state.revision, stage.expected_revision)?;
            let receipt = WorkspaceReceipt {
                operation_id: Uuid::new_v4().to_string(),
                revision: state.revision + 1,
                undoable: false,
                before: state.timeframe,
                after: state.timeframe,
            };
            let value = restore_receipt(reply, access, &receipt)?;
            tx.record_note_restore(
                &WorkspaceChange {
                    key,
                    digest,
                    receipt,
                    actor: access.actor,
                    request_id: reply.request_id,
                    committed_at_ms: reply.timestamp_ms,
                    undoes: None,
                },
                &stage.plan,
            )?;
            Ok(RestoreResult::Committed(value))
        });
        match outcome {
            Ok(RestoreResult::Declined(value)) => {
                self.note_restore.clear();
                Ok(value)
            }
            Ok(RestoreResult::Committed(value)) => {
                self.note_restore.clear();
                self.note_confirmation = None;
                self.port.clean_deleted_note()?;
                Ok(value)
            }
            Ok(RestoreResult::Replay(value)) => {
                self.port.clean_deleted_note()?;
                Ok(value)
            }
            Err(error) => {
                if error.code == ErrorCode::STALECONTEXT {
                    self.note_restore.clear();
                }
                Err(error)
            }
        }
    }
}

fn require_operations(access: &WorkspaceAccess<'_>, required: &[&str]) -> Result<(), Error> {
    if required
        .iter()
        .all(|operation| access.operations.contains(operation))
    {
        return Ok(());
    }
    Err(failure(ErrorCode::PERMISSIONDENIED))
}

fn parse_scope(value: Value) -> Result<DecisionNoteRestoreScope11, Error> {
    let mut scope: DecisionNoteRestoreScope11 =
        serde_json::from_value(value).map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
    scope.idempotency_key = canonical_id(&scope.idempotency_key)?;
    Ok(scope)
}

fn restore_key<'a>(
    access: &'a WorkspaceAccess<'_>,
    scope: &'a DecisionNoteRestoreScope11,
) -> WorkspaceReplayKey<'a> {
    WorkspaceReplayKey {
        principal_id: access.principal_id,
        paired_client_id: access.paired_client_id,
        operation: DECISION_NOTE_RESTORE_BACKUP,
        idempotency_key: &scope.idempotency_key,
    }
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

fn backup_binding(binding: &WorkspaceBinding) -> String {
    let mut digest = Sha256::new();
    digest.update(b"ScopeHub/decision-note-backup-v1\0");
    digest.update(binding.profile_id.as_bytes());
    digest.update([0]);
    digest.update(binding.workspace_id.as_bytes());
    hex::encode(digest.finalize())
}

fn check_archive_size(backup: &DecisionNoteBackupV1) -> Result<(), Error> {
    let bytes = serde_json::to_vec(backup).map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
    if bytes.len() > DECISION_NOTE_BACKUP_MAX_BYTES {
        return Err(failure(ErrorCode::RESOURCELIMIT));
    }
    Ok(())
}

fn validated_archive(
    mut backup: DecisionNoteBackupV1,
    binding: &WorkspaceBinding,
) -> Result<DecisionNoteBackupData, Error> {
    if backup.profile_binding != backup_binding(binding) {
        return Err(failure(ErrorCode::PERMISSIONDENIED));
    }
    check_archive_size(&backup)?;
    let mut notes = HashSet::new();
    let mut outcomes = HashSet::new();
    let mut deletions = HashSet::new();
    for entry in &mut backup.notes {
        let original = &mut entry.original;
        original.note_id = canonical_id(&original.note_id)?;
        if !notes.insert(original.note_id.as_str())
            || !has_note_content(&original.thesis, &original.evidence_refs, &original.choice)
        {
            return Err(failure(ErrorCode::INVALIDARGUMENT));
        }
        for outcome in &mut entry.outcomes {
            outcome.outcome_id = canonical_id(&outcome.outcome_id)?;
            if !outcomes.insert(outcome.outcome_id.as_str())
                || outcome.observed_on <= original.decided_on
                || outcome.outcome.trim().is_empty()
            {
                return Err(failure(ErrorCode::INVALIDARGUMENT));
            }
        }
    }
    for deletion in &mut backup.deletions {
        deletion.note_id = canonical_id(&deletion.note_id)?;
        if !deletions.insert(deletion.note_id.as_str()) {
            return Err(failure(ErrorCode::INVALIDARGUMENT));
        }
    }
    Ok(DecisionNoteBackupData {
        revision: backup.captured_workspace_revision,
        notes: backup.notes,
        deletions: backup.deletions,
    })
}

fn restore_receipt(
    reply: &WorkspaceReply<'_>,
    access: &WorkspaceAccess<'_>,
    receipt: &WorkspaceReceipt,
) -> Result<Value, Error> {
    reply.validated(json!({
        "state": "committed", "operation": DECISION_NOTE_RESTORE_BACKUP,
        "receipt": {
            "operation_id": receipt.operation_id,
            "workspace_id": access.workspace_id,
            "target": {"kind": "workspace", "object_id": access.workspace_id},
            "committed_workspace_revision": receipt.revision,
            "undoable": false,
        },
    }))
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use super::*;

    fn staged_content() -> DecisionNoteRestorePlan {
        DecisionNoteRestorePlan {
            originals: vec![],
            outcomes: vec![DecisionNoteOutcomeAddition {
                note_id: Uuid::new_v4().to_string(),
                outcomes: vec![DecisionNoteOutcome {
                    outcome_id: Uuid::new_v4().to_string(),
                    observed_on: "2026-04-09".into(),
                    outcome: "synthetic pending outcome".into(),
                }],
            }],
            deletions: vec![],
            summary: DecisionNoteRestoreSummary {
                added_notes: 0,
                added_outcomes: 1,
                removed_notes: 0,
                suppressed_notes: 0,
                imported_deletions: 0,
                independent_copy_warning: false,
            },
        }
    }

    fn install(staging: &DecisionNoteRestoreStaging, delay: Duration) {
        staging
            .install(
                Uuid::new_v4(),
                "synthetic-owner".into(),
                DecisionNoteRestoreScope11 {
                    workspace_id: "synthetic-workspace".into(),
                    expected_workspace_revision: 2,
                    idempotency_key: Uuid::new_v4().to_string(),
                },
                Instant::now() + delay,
                staged_content(),
            )
            .unwrap();
    }

    #[test]
    fn decision_note_stage_deadline_reclaims_private_content_without_an_operation() {
        let staging = DecisionNoteRestoreStaging::default();
        install(&staging, Duration::from_millis(35));
        // Inspect the owned slot periodically without dispatching or pruning any operation.
        let deadline = Instant::now() + Duration::from_secs(2);
        while staging.is_pending() && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(5));
        }
        assert!(!staging.is_pending());
    }

    #[test]
    fn decision_note_stage_releases_content_on_observation_and_disposal() {
        let staging = DecisionNoteRestoreStaging::default();
        install(&staging, CONFIRMATION_TTL);
        staging.observe(2);
        staging.observe_commit(1); // An old, body-free replay cannot invalidate a current stage.
        assert!(staging.is_pending());
        staging.observe(3);
        assert!(!staging.is_pending());

        install(&staging, CONFIRMATION_TTL);
        let slot = Arc::clone(&staging.0);
        drop(staging);
        assert!(slot.lock().unwrap().is_none());
    }
}
