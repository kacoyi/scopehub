use scopehub_domain::protocol::{Error, ErrorCode, Revision};
use serde_json::{Value, json};
use uuid::Uuid;

use crate::{OperationRegistry, failure};

use super::{
    MAX_REVISION, WorkspaceAccess, WorkspaceStore, evidence::local_display,
    layout::is_sample_instrument, permits_scope,
};

#[derive(PartialEq, Eq)]
pub enum WorkspaceSourcePreference {
    Empty,
    Sample(Option<String>),
    Frozen(String),
}

pub enum FrozenWorkspaceSourceStatus {
    Available,
    Revoked,
    Unavailable,
}

pub struct WorkspaceSourceView {
    pub preference: WorkspaceSourcePreference,
    pub frozen_status: Option<FrozenWorkspaceSourceStatus>,
}

impl WorkspaceSourceView {
    pub(super) fn to_json(&self) -> Value {
        match &self.preference {
            WorkspaceSourcePreference::Empty => json!({"kind": "none"}),
            WorkspaceSourcePreference::Sample(id) => json!({"kind": "sample", "instrument_id": id}),
            WorkspaceSourcePreference::Frozen(id) => json!({
                "kind": "frozen",
                "revision_id": id,
                "status": match self.frozen_status {
                    Some(FrozenWorkspaceSourceStatus::Available) => "available",
                    Some(FrozenWorkspaceSourceStatus::Revoked) => "revoked",
                    _ => "unavailable",
                },
            }),
        }
    }
}

impl<S: WorkspaceStore> OperationRegistry<S> {
    /// Designate the persistent main-chart source for the local human profile.
    /// A reference is presentation state, with current frozen display checked independently.
    pub fn select_workspace_source(
        &mut self,
        access: &WorkspaceAccess<'_>,
        request: &Value,
    ) -> Result<Value, Error> {
        if !local_display(access)
            || !access.disclose_workspace_state
            || access.principal_id != access.profile_id
            || !permits_scope(access, self.port.binding())
        {
            return Err(failure(ErrorCode::PERMISSIONDENIED));
        }
        let expected = request["expected_selection_revision"]
            .as_u64()
            .filter(|revision| *revision <= MAX_REVISION)
            .ok_or_else(|| failure(ErrorCode::INVALIDARGUMENT))?;
        if request["type"] != "workspace_source"
            || !request.as_object().is_some_and(|value| value.len() == 4)
            || !request["request_id"].as_str().is_some_and(valid_id)
        {
            return Err(failure(ErrorCode::INVALIDARGUMENT));
        }
        let preference = parse_preference(&request["source"])?;
        let selection_revision: Revision = self.port.transact(|tx| {
            let current = tx.state()?.selection_revision;
            if current != expected {
                return Err(failure(ErrorCode::STALECONTEXT));
            }
            if let WorkspaceSourcePreference::Frozen(id) = &preference
                && !tx.local_evidence_display_allowed(id)?
            {
                return Err(failure(ErrorCode::SNAPSHOTUNAVAILABLE));
            }
            let next = current
                .checked_add(1)
                .filter(|revision| *revision <= MAX_REVISION)
                .ok_or_else(|| failure(ErrorCode::RESOURCELIMIT))?;
            tx.set_source_preference(&preference)?;
            tx.set_chart_evidence(next, None)?;
            Ok(next)
        })?;
        self.indicator_stage.cancel_trusted_source();
        Ok(json!({"selection_revision": selection_revision}))
    }
}

fn valid_id(value: &str) -> bool {
    Uuid::parse_str(value).is_ok_and(|id| id.to_string() == value)
}

fn parse_preference(value: &Value) -> Result<WorkspaceSourcePreference, Error> {
    let len = value.as_object().map(serde_json::Map::len);
    match value["kind"].as_str() {
        Some("none") if len == Some(1) => Ok(WorkspaceSourcePreference::Empty),
        Some("sample") if len == Some(2) => {
            let id = value
                .get("instrument_id")
                .ok_or_else(|| failure(ErrorCode::INVALIDARGUMENT))?;
            if id.is_null() {
                Ok(WorkspaceSourcePreference::Sample(None))
            } else {
                let id = id
                    .as_str()
                    .filter(|id| is_sample_instrument(id))
                    .ok_or_else(|| failure(ErrorCode::UNSUPPORTEDCAPABILITY))?;
                Ok(WorkspaceSourcePreference::Sample(Some(id.into())))
            }
        }
        Some("frozen") if len == Some(2) => {
            let id = value["revision_id"]
                .as_str()
                .filter(|id| valid_id(id))
                .ok_or_else(|| failure(ErrorCode::INVALIDARGUMENT))?;
            Ok(WorkspaceSourcePreference::Frozen(id.into()))
        }
        _ => Err(failure(ErrorCode::INVALIDARGUMENT)),
    }
}
