use std::collections::BTreeMap;

use scopehub_domain::protocol::{
    Error, ErrorCode, EvidenceTarget11, LayoutPanel11, LayoutPanel11Dock, LayoutPanel11Panel,
    NamedWorkspaceLayout11, WorkspaceLayout11, WorkspaceLayout11Theme, WorkspaceLayoutEdit11,
    WorkspaceObject11, WorkspaceObject11Kind,
};

use scopehub_domain::protocol::{MutationScope11, OperationReceipt11};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use uuid::Uuid;

use crate::failure;

use super::{
    MAX_REVISION, UNDO, WorkspaceAccess, WorkspaceChange, WorkspaceReceipt, WorkspaceReplayKey,
    WorkspaceReply, WorkspaceState, WorkspaceTransaction, WorkspaceUndoAccess, WorkspaceUndoTarget,
    workspace_projection,
};

pub const PREVIEW_LAYOUT: &str = "workspace.previewLayout";
pub const APPLY_LAYOUT: &str = "workspace.applyLayout";
pub const LAYOUT_ID: &str = "workspace-layout";
const MAX_SAVED_LAYOUTS: usize = 24;

pub struct WorkspaceLayoutState {
    pub active: NamedWorkspaceLayout11,
    pub saved: BTreeMap<String, WorkspaceLayout11>,
}

impl Default for WorkspaceLayoutState {
    fn default() -> Self {
        Self {
            active: default_layout(),
            saved: BTreeMap::new(),
        }
    }
}

pub struct WorkspaceLayoutChange {
    pub before: NamedWorkspaceLayout11,
    pub after: NamedWorkspaceLayout11,
    pub saved_name: Option<String>,
    pub saved_before: Option<WorkspaceLayout11>,
}

pub struct WorkspaceLayoutSnapshot {
    pub active: NamedWorkspaceLayout11,
    pub saved: Vec<String>,
    pub undo: Option<WorkspaceUndoTarget>,
}

pub(super) fn layout_target() -> EvidenceTarget11 {
    EvidenceTarget11::WorkspaceObject11(Box::new(WorkspaceObject11 {
        kind: WorkspaceObject11Kind::Layout,
        object_id: LAYOUT_ID.into(),
    }))
}

fn default_layout() -> NamedWorkspaceLayout11 {
    NamedWorkspaceLayout11 {
        name: None,
        definition: WorkspaceLayout11 {
            version: 1,
            authored_view: None,
            panels: [
                LayoutPanel11Panel::Watchlist,
                LayoutPanel11Panel::Inspection,
            ]
            .into_iter()
            .map(|panel| LayoutPanel11 {
                panel,
                dock: LayoutPanel11Dock::Right,
                size: 280,
            })
            .collect(),
            theme: WorkspaceLayout11Theme::Dark,
            gain_color: "#43d4ac".into(),
            loss_color: "#f17d85".into(),
            link_selection: true,
            pinned_instrument: None,
        },
    }
}

pub(super) fn resolve_edit(
    state: WorkspaceLayoutState,
    edit: WorkspaceLayoutEdit11,
) -> Result<WorkspaceLayoutChange, Error> {
    let mut saved_name = None;
    let mut saved_before = None;
    let after = match edit {
        WorkspaceLayoutEdit11::SaveWorkspaceLayout11(edit) => {
            let name = edit.name;
            let definition = edit.definition;
            if name.trim() != name || name.chars().any(char::is_control) {
                return Err(failure(ErrorCode::INVALIDARGUMENT));
            }
            let [first, second] = definition.panels.as_slice() else {
                return Err(failure(ErrorCode::INVALIDARGUMENT));
            };
            if first.panel == second.panel
                || (first.dock == second.dock && first.size != second.size)
                || definition
                    .gain_color
                    .eq_ignore_ascii_case(&definition.loss_color)
            {
                return Err(failure(ErrorCode::INVALIDARGUMENT));
            }
            // Only the opt-in synthetic catalog is an implemented independent
            // comparison binding. This value confers no source grant.
            if definition
                .pinned_instrument
                .as_deref()
                .is_some_and(|id| !is_sample_instrument(id))
            {
                return Err(failure(ErrorCode::UNSUPPORTEDCAPABILITY));
            }
            saved_before = state.saved.get(&name).cloned();
            if saved_before.is_none() && state.saved.len() >= MAX_SAVED_LAYOUTS {
                return Err(failure(ErrorCode::RESOURCELIMIT));
            }
            saved_name = Some(name.clone());
            NamedWorkspaceLayout11 {
                name: Some(name),
                definition,
            }
        }
        WorkspaceLayoutEdit11::OpenWorkspaceLayout11(edit) => {
            let definition = state
                .saved
                .get(&edit.name)
                .cloned()
                .ok_or_else(|| failure(ErrorCode::STALECONTEXT))?;
            NamedWorkspaceLayout11 {
                name: Some(edit.name),
                definition,
            }
        }
        WorkspaceLayoutEdit11::ResetWorkspaceLayout11(_) => default_layout(),
    };
    Ok(WorkspaceLayoutChange {
        before: state.active,
        after,
        saved_name,
        saved_before,
    })
}

// The model may edit the public chart layout. Only the local human workspace
// reads or writes its private drawing attachment.
pub(super) fn project_layout(layout: &mut NamedWorkspaceLayout11, access: &WorkspaceAccess<'_>) {
    if !super::evidence::local_display(access) {
        layout.definition.authored_view = None;
    }
}

fn resolve_scoped(
    state: WorkspaceLayoutState,
    mut edit: WorkspaceLayoutEdit11,
    access: &WorkspaceAccess<'_>,
) -> Result<WorkspaceLayoutChange, Error> {
    if !super::evidence::local_display(access)
        && let WorkspaceLayoutEdit11::SaveWorkspaceLayout11(save) = &mut edit
    {
        if save.definition.authored_view.is_some() {
            return Err(failure(ErrorCode::PERMISSIONDENIED));
        }
        save.definition.authored_view = state
            .saved
            .get(&save.name)
            .and_then(|layout| layout.authored_view.clone());
    }
    resolve_edit(state, edit)
}

fn check_access(access: &WorkspaceAccess<'_>, operation: &str) -> Result<(), Error> {
    if !access.disclose_workspace_state || !access.operations.contains(&operation) {
        return Err(failure(ErrorCode::PERMISSIONDENIED));
    }
    Ok(())
}

fn check_revision(
    tx: &dyn WorkspaceTransaction,
    scope: &MutationScope11,
) -> Result<WorkspaceState, Error> {
    let state = tx.state()?;
    if scope.target != layout_target()
        || state.revision != scope.expected_workspace_revision
        || state.selection_revision != scope.expected_selection_revision
    {
        return Err(failure(ErrorCode::STALECONTEXT));
    }
    Ok(state)
}

fn preview_token(
    access: &WorkspaceAccess<'_>,
    scope: &MutationScope11,
    edit: &WorkspaceLayoutEdit11,
    secret: Uuid,
) -> Result<String, Error> {
    let payload = serde_json::to_vec(&(
        access.actor.as_str(),
        access.profile_id,
        access.workspace_id,
        access.principal_id,
        access.paired_client_id,
        scope.expected_workspace_revision,
        scope.expected_selection_revision,
        edit,
    ))
    .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
    let mut digest = Sha256::new();
    digest.update(payload);
    digest.update(secret.as_bytes());
    Ok(hex::encode(digest.finalize()))
}

pub(super) fn preview(
    tx: &dyn WorkspaceTransaction,
    access: &WorkspaceAccess<'_>,
    scope: &MutationScope11,
    arguments: &Value,
    reply: &WorkspaceReply<'_>,
    secret: Uuid,
) -> Result<Value, Error> {
    check_access(access, PREVIEW_LAYOUT)?;
    check_revision(tx, scope)?;
    let edit = serde_json::from_value(arguments["edit"].clone())
        .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
    let token = preview_token(access, scope, &edit, secret)?;
    let mut proposed = resolve_scoped(tx.layouts()?, edit, access)?;
    project_layout(&mut proposed.after, access);
    reply.validated(json!({
        "state": "previewed", "operation": PREVIEW_LAYOUT,
        "result": {"layout": proposed.after, "preview_token": token},
    }))
}

pub(super) fn commit(
    tx: &mut dyn WorkspaceTransaction,
    access: &WorkspaceAccess<'_>,
    scope: &MutationScope11,
    arguments: &Value,
    reply: &WorkspaceReply<'_>,
    secret: Uuid,
) -> Result<Value, Error> {
    let operation = reply.operation.name.as_str();
    check_access(access, operation)?;
    let semantic = serde_json::to_vec(&(
        &scope.target,
        scope.expected_workspace_revision,
        scope.expected_selection_revision,
        arguments,
    ))
    .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
    let digest = Sha256::digest(semantic).into();
    let idempotency_key = Uuid::parse_str(&scope.idempotency_key)
        .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?
        .to_string();
    let key = WorkspaceReplayKey {
        principal_id: access.principal_id,
        paired_client_id: access.paired_client_id,
        operation,
        idempotency_key: &idempotency_key,
    };
    if let Some(saved) = tx.replay(&key)? {
        if saved.digest != digest {
            return Err(failure(ErrorCode::INVALIDARGUMENT));
        }
        let payload = tx
            .layout_change(&saved.receipt.operation_id, access.actor)?
            .ok_or_else(|| failure(ErrorCode::PERMISSIONDENIED))?;
        return committed(reply, access, &saved.receipt, &payload.after);
    }
    let state = check_revision(tx, scope)?;
    let layouts = tx.layouts()?;
    let (edit, saved_after, undoes) = if operation == APPLY_LAYOUT {
        let input: WorkspaceLayoutEdit11 = serde_json::from_value(arguments["edit"].clone())
            .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
        let expected_token = preview_token(access, scope, &input, secret)?;
        if arguments["preview_token"].as_str() != Some(expected_token.as_str()) {
            return Err(failure(ErrorCode::STALECONTEXT));
        }
        let edit = resolve_scoped(layouts, input, access)?;
        let saved_after = edit
            .saved_name
            .as_ref()
            .map(|_| edit.after.definition.clone());
        (edit, saved_after, None)
    } else {
        let original_id = arguments["operation_id"]
            .as_str()
            .expect("validated undo id");
        let undo_access = WorkspaceUndoAccess {
            actor: access.actor,
            principal_id: access.principal_id,
            paired_client_id: access.paired_client_id,
        };
        tx.layout_undo_candidate(original_id, undo_access)?
            .ok_or_else(|| failure(ErrorCode::STALECONTEXT))?;
        let original = tx
            .layout_change(original_id, access.actor)?
            .ok_or_else(|| failure(ErrorCode::INTERNALERROR))?;
        if layouts.active != original.after {
            return Err(failure(ErrorCode::STALECONTEXT));
        }
        let saved_before = original
            .saved_name
            .as_ref()
            .and_then(|name| layouts.saved.get(name).cloned());
        (
            WorkspaceLayoutChange {
                before: layouts.active,
                after: original.before,
                saved_name: original.saved_name,
                saved_before,
            },
            original.saved_before,
            Some(original_id),
        )
    };
    let revision = state
        .revision
        .checked_add(1)
        .filter(|revision| *revision <= MAX_REVISION)
        .ok_or_else(|| failure(ErrorCode::RESOURCELIMIT))?;
    let change = WorkspaceChange {
        key,
        digest,
        actor: access.actor,
        request_id: reply.request_id,
        committed_at_ms: reply.timestamp_ms,
        receipt: WorkspaceReceipt {
            operation_id: Uuid::new_v4().to_string(),
            revision,
            undoable: reply.operation.undoable,
            before: state.timeframe,
            after: state.timeframe,
        },
        undoes,
    };
    tx.record_layout(&change, &edit, saved_after.as_ref())?;
    committed(reply, access, &change.receipt, &edit.after)
}

fn committed(
    reply: &WorkspaceReply<'_>,
    access: &WorkspaceAccess<'_>,
    receipt: &WorkspaceReceipt,
    layout: &NamedWorkspaceLayout11,
) -> Result<Value, Error> {
    let result = if reply.operation.name == UNDO {
        json!(workspace_projection(
            access.workspace_id,
            receipt.revision,
            receipt.after
        ))
    } else {
        let mut visible = layout.clone();
        project_layout(&mut visible, access);
        json!(visible)
    };
    reply.validated(json!({
        "state": "committed", "operation": reply.operation.name, "result": result,
        "receipt": OperationReceipt11 {
            operation_id: receipt.operation_id.clone(), workspace_id: access.workspace_id.into(),
            target: layout_target(), committed_workspace_revision: receipt.revision, undoable: receipt.undoable,
        },
    }))
}

pub(super) fn is_sample_instrument(id: &str) -> bool {
    matches!(
        id,
        "SIM.CN.STOCK.001" | "SIM.US.STOCK.001" | "SIM.CN.FUTURE.001"
    )
}
