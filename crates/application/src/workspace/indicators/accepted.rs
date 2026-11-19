//! Confirm and restore immutable local indicator artifacts.

use std::time::{Duration, Instant};

use scopehub_domain::protocol::{
    Error, ErrorCode, EvidenceTarget11, MutationScope11, OperationReceipt11, WorkspaceObject11,
    WorkspaceObject11Kind,
};
use scopehub_domain::wire::normalize_integers;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use uuid::Uuid;

use super::{
    JobResult, OperationRegistry, WorkspaceAccess, WorkspaceReply, WorkspaceStore,
    WorkspaceTransaction, check_context, failure, hash, new_id, operation, timestamp_ms, validator,
    workspace_failure,
};
use crate::{Actor, WorkspaceChange, WorkspaceReceipt, WorkspaceReplayKey};

pub const ACCEPT_INDICATOR_VERSION: &str = "indicator.acceptVersion";
pub const REMOVE_INDICATOR_LAYER: &str = "indicator.remove";
pub const INDICATOR_CONFIRMATION: &str = "confirmation.respond";
pub const MAX_ACTIVE_INDICATOR_LAYERS: u64 = 256;
const CONFIRMATION_TTL: Duration = Duration::from_secs(60);

static DECISION: std::sync::LazyLock<jsonschema::Validator> =
    std::sync::LazyLock::new(|| validator("TrustedConfirmationDecision11"));
static ARTIFACT: std::sync::LazyLock<jsonschema::Validator> =
    std::sync::LazyLock::new(|| validator("InlineIndicatorArtifact11"));
static SNAPSHOT: std::sync::LazyLock<jsonschema::Validator> =
    std::sync::LazyLock::new(|| validator("FrozenSnapshot"));

pub struct IndicatorLayerRef {
    pub layer_id: String,
    pub version_id: String,
    pub dataset_id: String,
    pub sample_instrument: Option<String>,
    pub active: bool,
}

pub struct IndicatorLayerUndo {
    pub before: bool,
    pub after: bool,
}

pub struct IndicatorVersionRecord {
    pub layer: IndicatorLayerRef,
    pub preview_id: String,
    pub challenge_id: String,
    pub document: Value,
}

pub(super) struct Approval {
    challenge: Value,
    scope: MutationScope11,
    args: Value,
    deadline: Instant,
}

impl<S: WorkspaceStore> OperationRegistry<S> {
    pub(crate) fn execute_indicator_layer(
        &mut self,
        access: &WorkspaceAccess<'_>,
        name: &str,
        mut request: Value,
    ) -> Result<Value, Error> {
        let op = operation(name)?;
        if !op.wire_validators().0.is_valid(&request) {
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
            scope.idempotency_key = Uuid::parse_str(&scope.idempotency_key)
                .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?
                .to_string();
            if scope.workspace_id != access.workspace_id {
                return Err(failure(ErrorCode::PERMISSIONDENIED));
            }
            let args = &request["intent"]["arguments"];
            let digest = compute_digest(&scope, args)?;
            let key = replay_key(access, &scope, name);
            if let Some(outcome) = self.port.inspect(|tx| prior(tx, &key, &digest, &scope))? {
                return reply.validated(outcome);
            }
            if name == REMOVE_INDICATOR_LAYER {
                return self.remove_indicator_layer(&reply, &scope, args, digest, key);
            }
            let document = self.acceptable_preview(&scope, args)?;
            self.port.inspect(|tx| {
                check_context(tx, &scope)?;
                check_retention(tx, dataset_id(&document)?, sample_instrument(&document)?)?;
                Ok(())
            })?;
            if let Some(approved) = &self.indicator_stage.approval
                && approved.deadline > Instant::now()
                && approved.scope.idempotency_key == scope.idempotency_key
            {
                if approved.scope != scope || &approved.args != args {
                    return Err(failure(ErrorCode::INVALIDARGUMENT));
                }
                return reply.validated(
                    json!({"state": "awaiting_confirmation", "challenge": approved.challenge}),
                );
            }
            let challenge = json!({
                "challenge_id": new_id(), "request_id": request_id, "task_id": document["task_id"],
                "operation": ACCEPT_INDICATOR_VERSION, "principal_id": access.principal_id,
                "session_id": self.indicator_stage.session_id.get_or_init(new_id), "workspace_id": scope.workspace_id,
                "target": scope.target, "workspace_revision": scope.expected_workspace_revision,
                "selection_revision": scope.expected_selection_revision,
                "parameters_hash": hash(&json!([args, document["snapshot"], document["resolved_parameters"],
                    document["warmup_from_ms"], document["artifact"]["result"]["provenance"]]))?,
                "expires_at_ms": timestamp_ms()? + CONFIRMATION_TTL.as_millis() as i64,
                "python": null,
            });
            let response = reply
                .validated(json!({"state": "awaiting_confirmation", "challenge": challenge}))?;
            self.indicator_stage.approval = Some(Approval {
                challenge,
                scope,
                args: args.clone(),
                deadline: Instant::now() + CONFIRMATION_TTL,
            });
            Ok(response)
        })();
        match result {
            Ok(response) => Ok(response),
            Err(error) => reply.failed(&workspace_failure(error.code)),
        }
    }

    pub(crate) fn resolve_indicator_confirmation(
        &mut self,
        access: &WorkspaceAccess<'_>,
        request: &Value,
    ) -> Result<Value, Error> {
        let op = operation(ACCEPT_INDICATOR_VERSION)?;
        if !DECISION.is_valid(request) {
            return Err(failure(ErrorCode::INVALIDARGUMENT));
        }
        let request_id = request["request_id"]
            .as_str()
            .expect("validated request ID");
        let reply = WorkspaceReply::new(op, request_id)?;
        let result = (|| {
            self.authorize_indicator(access, ACCEPT_INDICATOR_VERSION)?;
            let challenge_id = request["challenge_id"]
                .as_str()
                .expect("validated challenge ID");
            if request["decision"] == "approve"
                && let Some(previous) = self.port.inspect(|tx| {
                    let Some((layer, receipt)) = tx.indicator_confirmation_receipt(challenge_id, access.principal_id)? else {
                        return Ok(None);
                    };
                    check_retention(tx, &layer.dataset_id, layer.sample_instrument.as_deref())?;
                    let document = tx.indicator_document(&layer.layer_id)?
                        .ok_or_else(|| failure(ErrorCode::INTERNALERROR))?;
                    let target = serde_json::from_value(json!({
                        "kind": "chart_interval", "snapshot": document["source_snapshot"], "range": {
                            "from_ms": document["snapshot"]["from_ms"], "to_ms": document["snapshot"]["to_ms"],
                        },
                    })).map_err(|_| failure(ErrorCode::OUTPUTINVALID))?;
                    Ok(Some(committed(ACCEPT_INDICATOR_VERSION, &target, &receipt, access.workspace_id, &layer)))
                })?
            {
                return reply.validated(previous);
            }
            let approval = self
                .indicator_stage
                .approval
                .as_ref()
                .filter(|approval| approval.challenge["challenge_id"] == request["challenge_id"])
                .ok_or_else(|| failure(ErrorCode::STALECONTEXT))?;
            if approval.deadline <= Instant::now() {
                self.indicator_stage.approval = None;
                return Err(failure(ErrorCode::STALECONTEXT));
            }
            if request["decision"] == "deny" {
                self.indicator_stage.approval = None;
                return Err(failure(ErrorCode::CANCELLED));
            }
            let scope = &approval.scope;
            let digest = compute_digest(scope, &approval.args)?;
            let key = replay_key(access, scope, ACCEPT_INDICATOR_VERSION);
            let document = self.acceptable_preview(scope, &approval.args)?;
            let id = dataset_id(&document)?.to_owned();
            validate_document(&document, &id)?;
            let preview_id = approval.args["preview_id"]
                .as_str()
                .ok_or_else(|| failure(ErrorCode::INVALIDARGUMENT))?
                .to_owned();
            let version = IndicatorVersionRecord {
                layer: IndicatorLayerRef {
                    layer_id: new_id(),
                    version_id: new_id(),
                    dataset_id: id,
                    sample_instrument: sample_instrument(&document)?.map(str::to_owned),
                    active: true,
                },
                preview_id,
                challenge_id: challenge_id.to_owned(),
                document,
            };
            let result = self.port.transact(|tx| {
                if let Some(outcome) = prior(tx, &key, &digest, scope)? {
                    return reply.validated(outcome);
                }
                check_context(tx, scope)?;
                check_retention(
                    tx,
                    &version.layer.dataset_id,
                    version.layer.sample_instrument.as_deref(),
                )?;
                if tx.active_indicator_count()? >= MAX_ACTIVE_INDICATOR_LAYERS {
                    return Err(failure(ErrorCode::RESOURCELIMIT));
                }
                if tx.indicator_preview_accepted(&version.preview_id)? {
                    return Err(failure(ErrorCode::STALECONTEXT));
                }
                let change = change(tx, key, digest, request_id, true)?;
                let response = reply.validated(committed(
                    ACCEPT_INDICATOR_VERSION,
                    &scope.target,
                    &change.receipt,
                    access.workspace_id,
                    &version.layer,
                ))?;
                tx.record_indicator_version(&change, &version)?;
                Ok(response)
            })?;
            self.indicator_stage.approval = None;
            Ok(result)
        })();
        match result {
            Ok(response) => Ok(response),
            Err(error) => reply.failed(&workspace_failure(error.code)),
        }
    }

    fn acceptable_preview(&self, scope: &MutationScope11, args: &Value) -> Result<Value, Error> {
        let preview_id = args["preview_id"]
            .as_str()
            .ok_or_else(|| failure(ErrorCode::INVALIDARGUMENT))?;
        let deadline = Instant::now();
        let mut newer = std::collections::HashSet::new();
        for job in self.indicator_stage.jobs.iter().rev() {
            if job.expires <= deadline || job.is_cancelled() {
                continue;
            }
            let status = job.state.lock().expect("indicator job state");
            let JobResult::Ready(preview) = &*status else {
                continue;
            };
            if !preview.delivered {
                continue;
            }
            if preview.id != preview_id {
                newer.insert(&job.source.dataset_id);
                continue;
            }
            if newer.contains(&job.source.dataset_id)
                || preview.artifact["result_hash"] != args["result_hash"]
                || job.scope.target != scope.target
                || job.scope.expected_workspace_revision != scope.expected_workspace_revision
                || job.scope.expected_selection_revision != scope.expected_selection_revision
            {
                return Err(failure(ErrorCode::STALECONTEXT));
            }
            let document = json!({
                "task_id": job.id, "draft_id": job.draft_id, "preview_id": preview.id,
                "source_snapshot": job.source, "snapshot": preview.snapshot,
                "resolved_parameters": preview.resolved_parameters, "warmup_from_ms": preview.warmup_from_ms,
                "artifact": preview.artifact, "calendar_hash": preview.calendar_hash,
            });
            return Ok(document);
        }
        Err(failure(ErrorCode::STALECONTEXT))
    }

    fn remove_indicator_layer(
        &mut self,
        reply: &WorkspaceReply<'_>,
        scope: &MutationScope11,
        args: &Value,
        digest: [u8; 32],
        key: WorkspaceReplayKey<'_>,
    ) -> Result<Value, Error> {
        let layer_id = args["layer_id"]
            .as_str()
            .ok_or_else(|| failure(ErrorCode::INVALIDARGUMENT))?;
        if scope.target != layer_target(layer_id) {
            return Err(failure(ErrorCode::INVALIDARGUMENT));
        }
        self.port.transact(|tx| {
            if let Some(outcome) = prior(tx, &key, &digest, scope)? {
                return reply.validated(outcome);
            }
            check_context(tx, scope)?;
            let layer = tx
                .indicator_layer(layer_id)?
                .filter(|layer| layer.active)
                .ok_or_else(|| failure(ErrorCode::STALECONTEXT))?;
            let mutation = change(tx, key, digest, reply.request_id, true)?;
            let response = reply.validated(committed(
                REMOVE_INDICATOR_LAYER,
                &scope.target,
                &mutation.receipt,
                &scope.workspace_id,
                &layer,
            ))?;
            tx.record_indicator_layer(&mutation, &layer, false)?;
            Ok(response)
        })
    }

    /// Bounded local transport read for a revision's layer references or a single artifact.
    pub fn read_accepted_indicator(
        &self,
        access: &WorkspaceAccess<'_>,
        request: &Value,
    ) -> Result<Value, Error> {
        let valid_id = |field: &str| {
            request[field]
                .as_str()
                .filter(|id| Uuid::parse_str(id).is_ok_and(|uuid| uuid.to_string() == *id))
        };
        if !request.as_object().is_some_and(|object| object.len() == 3)
            || valid_id("request_id").is_none()
        {
            return Err(failure(ErrorCode::INVALIDARGUMENT));
        }
        match request["type"].as_str() {
            Some("indicator_accepted_layers") => self.accepted_indicator_layers(
                access,
                valid_id("revision_id").ok_or_else(|| failure(ErrorCode::INVALIDARGUMENT))?,
            ),
            Some("indicator_accepted_output") => self.accepted_indicator_output(
                access,
                valid_id("layer_id").ok_or_else(|| failure(ErrorCode::INVALIDARGUMENT))?,
            ),
            _ => Err(failure(ErrorCode::INVALIDARGUMENT)),
        }
    }

    /// Select only the accepted references for the revision being inspected.
    pub fn accepted_indicator_layers(
        &self,
        access: &WorkspaceAccess<'_>,
        revision_id: &str,
    ) -> Result<Value, Error> {
        self.authorize_indicator(access, ACCEPT_INDICATOR_VERSION)?;
        self.port.inspect(|tx| {
            let mut layers = Vec::new();
            for layer in tx.indicator_layers(revision_id)? {
                let available = retention_allowed(tx, &layer.dataset_id, layer.sample_instrument.as_deref())?;
                layers.push(json!({"layer_id": layer.layer_id, "version_id": layer.version_id, "available": available}));
            }
            Ok(json!({"dataset_id": revision_id, "layers": layers}))
        })
    }

    /// Restore one validated artifact under current source rights without recomputation.
    pub fn accepted_indicator_output(
        &self,
        access: &WorkspaceAccess<'_>,
        layer_id: &str,
    ) -> Result<Value, Error> {
        self.authorize_indicator(access, ACCEPT_INDICATOR_VERSION)?;
        self.port.inspect(|tx| {
            let layer = tx.indicator_layer(layer_id)?.filter(|layer| layer.active)
                .ok_or_else(|| failure(ErrorCode::STALECONTEXT))?;
            check_retention(tx, &layer.dataset_id, layer.sample_instrument.as_deref())?;
            let document = tx.indicator_document(&layer.layer_id)?
                .ok_or_else(|| failure(ErrorCode::SNAPSHOTUNAVAILABLE))?;
            validate_document(&document, &layer.dataset_id)?;
            Ok(json!({"layer_id": layer.layer_id, "version_id": layer.version_id, "document": document}))
        })
    }
}

fn validate_document(document: &Value, dataset: &str) -> Result<(), Error> {
    let artifact = &document["artifact"];
    let payload = &artifact["result"];
    let provenance = &payload["provenance"];
    let source = &document["source_snapshot"];
    let snapshot = &document["snapshot"];
    let params = &document["resolved_parameters"];
    let invalid = || failure(ErrorCode::OUTPUTINVALID);
    if !ARTIFACT.is_valid(artifact)
        || !SNAPSHOT.is_valid(source)
        || !SNAPSHOT.is_valid(snapshot)
        || artifact["result_hash"] != hash(payload)?
        || !params.is_object()
        || provenance["params_hash"] != hash(params)?
        || dataset_id(document)? != dataset
        || provenance["dataset_id"] != dataset
        || provenance["dataset_revision"] != snapshot["dataset_revision"]
        || sample_instrument(document)?.is_some()
            && !document["calendar_hash"].as_str().is_some_and(|hash| {
                hash.len() == 64 && hash.bytes().all(|byte| byte.is_ascii_hexdigit())
            })
        || ["dataset_id", "dataset_revision", "series", "as_of_ms"]
            .into_iter()
            .any(|field| source[field] != snapshot[field])
    {
        return Err(invalid());
    }
    let from = snapshot["from_ms"].as_i64().ok_or_else(invalid)?;
    let to = snapshot["to_ms"].as_i64().ok_or_else(invalid)?;
    let source_from = source["from_ms"].as_i64().ok_or_else(invalid)?;
    let source_to = source["to_ms"].as_i64().ok_or_else(invalid)?;
    let warmup = snapshot["warmup_bars"].as_u64().ok_or_else(invalid)?;
    let warmup_start = document.get("warmup_from_ms").ok_or_else(invalid)?;
    if from >= to
        || from < source_from
        || to > source_to
        || (warmup == 0 && !warmup_start.is_null())
        || (warmup > 0
            && !warmup_start
                .as_i64()
                .is_some_and(|start| (source_from..from).contains(&start)))
    {
        return Err(invalid());
    }
    Ok(())
}

fn sample_instrument(document: &Value) -> Result<Option<&str>, Error> {
    let series = &document["source_snapshot"]["series"];
    if series["provider_id"] != crate::DEMO_PROVIDER {
        return Ok(None);
    }
    if document["artifact"]["result"]["provenance"]["engine_version"]
        != super::trusted::TRUSTED_PYTHON_ENGINE
    {
        return Err(failure(ErrorCode::PERMISSIONDENIED));
    }
    series["instrument_id"]
        .as_str()
        .map(Some)
        .ok_or_else(|| failure(ErrorCode::OUTPUTINVALID))
}

fn retention_allowed(
    tx: &dyn WorkspaceTransaction,
    dataset_id: &str,
    sample_instrument: Option<&str>,
) -> Result<bool, Error> {
    match sample_instrument {
        Some(instrument) => tx.indicator_sample_allowed(instrument),
        None => tx.indicator_retention_allowed(dataset_id),
    }
}

fn check_retention(
    tx: &dyn WorkspaceTransaction,
    dataset_id: &str,
    sample_instrument: Option<&str>,
) -> Result<(), Error> {
    if !retention_allowed(tx, dataset_id, sample_instrument)? {
        return Err(failure(ErrorCode::PERMISSIONDENIED));
    }
    Ok(())
}

fn dataset_id(document: &Value) -> Result<&str, Error> {
    document["snapshot"]["dataset_id"]
        .as_str()
        .ok_or_else(|| failure(ErrorCode::OUTPUTINVALID))
}

fn layer_target(id: &str) -> EvidenceTarget11 {
    EvidenceTarget11::WorkspaceObject11(Box::new(WorkspaceObject11 {
        kind: WorkspaceObject11Kind::Layer,
        object_id: id.into(),
    }))
}

fn compute_digest(scope: &MutationScope11, args: &Value) -> Result<[u8; 32], Error> {
    let encoded =
        serde_json::to_vec(&(scope, args)).map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
    Ok(Sha256::digest(encoded).into())
}

fn replay_key<'a>(
    access: &'a WorkspaceAccess<'_>,
    scope: &'a MutationScope11,
    name: &'a str,
) -> WorkspaceReplayKey<'a> {
    WorkspaceReplayKey {
        principal_id: access.principal_id,
        paired_client_id: access.paired_client_id,
        operation: name,
        idempotency_key: &scope.idempotency_key,
    }
}

fn prior(
    tx: &dyn WorkspaceTransaction,
    key: &WorkspaceReplayKey<'_>,
    digest: &[u8; 32],
    scope: &MutationScope11,
) -> Result<Option<Value>, Error> {
    let Some(saved) = tx.replay(key)? else {
        return Ok(None);
    };
    if &saved.digest != digest {
        return Err(failure(ErrorCode::INVALIDARGUMENT));
    }
    let layer = tx
        .indicator_operation_layer(&saved.receipt.operation_id)?
        .ok_or_else(|| failure(ErrorCode::INTERNALERROR))?;
    if key.operation == ACCEPT_INDICATOR_VERSION {
        check_retention(tx, &layer.dataset_id, layer.sample_instrument.as_deref())?;
    }
    Ok(Some(committed(
        key.operation,
        &scope.target,
        &saved.receipt,
        &scope.workspace_id,
        &layer,
    )))
}

fn change<'a>(
    tx: &dyn WorkspaceTransaction,
    key: WorkspaceReplayKey<'a>,
    digest: [u8; 32],
    request_id: &'a str,
    undoable: bool,
) -> Result<WorkspaceChange<'a>, Error> {
    let state = tx.state()?;
    let revision = state
        .revision
        .checked_add(1)
        .filter(|revision| *revision <= super::super::MAX_REVISION)
        .ok_or_else(|| failure(ErrorCode::RESOURCELIMIT))?;
    Ok(WorkspaceChange {
        key,
        digest,
        receipt: WorkspaceReceipt {
            operation_id: new_id(),
            revision,
            undoable,
            before: state.timeframe,
            after: state.timeframe,
        },
        actor: Actor::User,
        request_id,
        committed_at_ms: timestamp_ms()?,
        undoes: None,
    })
}

fn committed(
    operation: &str,
    target: &EvidenceTarget11,
    receipt: &WorkspaceReceipt,
    workspace_id: &str,
    layer: &IndicatorLayerRef,
) -> Value {
    let result = if operation == ACCEPT_INDICATOR_VERSION {
        json!({"version_id": layer.version_id, "layer_id": layer.layer_id})
    } else {
        json!({"operation_id": receipt.operation_id})
    };
    json!({
        "state": "committed", "operation": operation, "result": result,
        "receipt": OperationReceipt11 {
            operation_id: receipt.operation_id.clone(), workspace_id: workspace_id.into(),
            target: target.clone(), committed_workspace_revision: receipt.revision, undoable: receipt.undoable,
        },
    })
}

pub(crate) fn undo_indicator_layer(
    tx: &mut dyn WorkspaceTransaction,
    access: &WorkspaceAccess<'_>,
    scope: &MutationScope11,
    args: &Value,
    reply: &WorkspaceReply<'_>,
) -> Result<Value, Error> {
    use super::super::{UNDO, workspace_projection};
    if access.principal_id != access.profile_id
        || access.paired_client_id.is_some()
        || !matches!(access.actor, Actor::User)
        || !access.disclose_workspace_state
        || !access.operations.contains(&UNDO)
    {
        return Err(failure(ErrorCode::PERMISSIONDENIED));
    }
    let EvidenceTarget11::WorkspaceObject11(target) = &scope.target else {
        return Err(failure(ErrorCode::INVALIDARGUMENT));
    };
    let args_id = args["operation_id"]
        .as_str()
        .ok_or_else(|| failure(ErrorCode::INVALIDARGUMENT))?;
    let digest = compute_digest(scope, args)?;
    let normalized = Uuid::parse_str(&scope.idempotency_key)
        .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?
        .to_string();
    let key = WorkspaceReplayKey {
        idempotency_key: &normalized,
        ..replay_key(access, scope, UNDO)
    };
    let response = |receipt: &WorkspaceReceipt| {
        reply.validated(json!({
            "state": "committed", "operation": UNDO,
            "result": workspace_projection(&scope.workspace_id, receipt.revision, receipt.after),
            "receipt": OperationReceipt11 {
                operation_id: receipt.operation_id.clone(), workspace_id: scope.workspace_id.clone(),
                target: scope.target.clone(), committed_workspace_revision: receipt.revision, undoable: false,
            }
        }))
    };
    if let Some(saved) = tx.replay(&key)? {
        if saved.digest != digest
            || tx
                .indicator_operation_layer(&saved.receipt.operation_id)?
                .is_none()
        {
            return Err(failure(ErrorCode::INVALIDARGUMENT));
        }
        return response(&saved.receipt);
    }
    check_context(tx, scope)?;
    let undo = tx
        .indicator_layer_undo(args_id, access.principal_id, &target.object_id)?
        .ok_or_else(|| failure(ErrorCode::STALECONTEXT))?;
    let layer = tx
        .indicator_layer(&target.object_id)?
        .filter(|layer| layer.active == undo.after)
        .ok_or_else(|| failure(ErrorCode::STALECONTEXT))?;
    if undo.before {
        check_retention(tx, &layer.dataset_id, layer.sample_instrument.as_deref())?;
        if tx.active_indicator_count()? >= MAX_ACTIVE_INDICATOR_LAYERS {
            return Err(failure(ErrorCode::RESOURCELIMIT));
        }
    }
    let mut mutation = change(tx, key, digest, reply.request_id, false)?;
    mutation.undoes = Some(args_id);
    let result = response(&mutation.receipt)?;
    tx.record_indicator_layer(&mutation, &layer, undo.before)?;
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn restores_exact_legacy_csv_output_without_current_engine_or_schedule() {
        let dataset = "00000000-0000-4000-8000-000000000006";
        let snapshot = json!({
            "series": {
                "instrument_id": "FIX", "provider_id": "local.csv", "timeframe": "1d",
                "price_adjustment": "raw", "session_scope": "regular",
                "calendar_id": "unverified", "quantity_unit": "shares",
            },
            "dataset_id": dataset, "dataset_revision": "frozen-csv",
            "from_ms": 100, "to_ms": 300, "as_of_ms": 300, "warmup_bars": 0,
            "content_hash": "ab".repeat(32),
        });
        let parameters = json!({"period": 2});
        let expected = json!([{"time_ms": 100, "value": null}, {"time_ms": 200, "value": 37.875}]);
        let result = json!({
            "series": [{"id": "mean", "pane": "price", "type": "line",
                "style": {"width": 2, "color": "#123456"}, "points": expected}],
            "markers": [], "regions": [], "diagnostics": [],
            "provenance": {
                "code_hash": "cd".repeat(32), "params_hash": hash(&parameters).unwrap(),
                "dataset_id": dataset, "dataset_revision": "frozen-csv",
                "engine_version": "scopehub-declarative-2",
            },
        });
        let document = json!({
            "source_snapshot": snapshot, "snapshot": snapshot,
            "resolved_parameters": parameters, "warmup_from_ms": null,
            "artifact": {"storage": "inline", "result_hash": hash(&result).unwrap(), "result": result},
        });
        let stored = serde_json::to_vec(&document).unwrap();
        let restored = serde_json::from_slice(&stored).unwrap();
        validate_document(&restored, dataset).unwrap();
        assert_eq!(
            restored["artifact"]["result"]["series"][0]["points"],
            expected
        );
        assert_eq!(serde_json::to_vec(&restored).unwrap(), stored);
    }
}
