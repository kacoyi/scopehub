use scopehub_domain::{
    protocol::{
        Bar, Error, ErrorCode, ExportField11, FrozenSnapshot, MarketHistoryPageInput,
        MarketHistoryPageOutput, Revision, VisibleRange,
    },
    wire::normalize_integers,
};
use serde_json::{Value, json};

use super::{
    EvidenceAdmission, FrozenChart, READ_CHART_PROJECTION, SELECT_EVIDENCE, evidence_intent_request,
};
use crate::{
    Actor, DEMO_PROVIDER, MarketPort, MarketRegistry, OperationRegistry, WorkspaceAccess,
    WorkspaceStore, WorkspaceTransaction, failure, history, operation,
};

pub const READ_SAMPLE_EVIDENCE_HISTORY: &str = "workspace.loadSampleEvidenceHistory";
const MAX_SAMPLE_BARS: u64 = 200;
pub(super) const MODEL_FIELDS: [ExportField11; 2] = [ExportField11::TimeMs, ExportField11::Close];

pub fn is_sample_evidence_selection(request: &Value) -> bool {
    request["operation"] == SELECT_EVIDENCE
        && request["target"]["snapshot"]["series"]["provider_id"] == DEMO_PROVIDER
}

fn sample_query(request: &Value) -> Result<MarketHistoryPageInput, Error> {
    if !is_sample_evidence_selection(request)
        || !operation(SELECT_EVIDENCE)?
            .wire_validators()
            .0
            .is_valid(request)
    {
        return Err(failure(ErrorCode::INVALIDARGUMENT));
    }
    let mut snapshot = request["target"]["snapshot"].clone();
    normalize_integers(&mut snapshot);
    let snapshot: FrozenSnapshot =
        serde_json::from_value(snapshot).map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
    Ok(MarketHistoryPageInput {
        series: snapshot.series.clone(),
        from_ms: snapshot.from_ms,
        to_ms: snapshot.to_ms,
        warmup_bars: snapshot.warmup_bars,
        as_of_ms: Some(snapshot.as_of_ms),
        expected_revision: Some(snapshot.dataset_revision.clone()),
        snapshot: Some(snapshot),
        cursor: None,
        page_size: Some(MAX_SAMPLE_BARS),
        include_stream_position: None,
    })
}

impl<P: MarketPort> MarketRegistry<P> {
    /// Only a trusted workspace host uses this read to validate a sample selection.
    pub async fn read_sample_evidence_history(&mut self, request: &Value) -> Result<Value, Error> {
        self.execute(
            Actor::User,
            "market.readHistoryPage",
            json!(sample_query(request)?),
        )
        .await
    }
}

pub(crate) struct SampleChartSelection {
    pub(in crate::workspace) selection_revision: Revision,
    pub(super) snapshot: FrozenSnapshot,
    pub(super) bars: Vec<Bar>,
    range: VisibleRange,
}

impl SampleChartSelection {
    pub(super) fn new(
        selection_revision: Revision,
        chart: &FrozenChart,
        range: VisibleRange,
    ) -> Result<Self, Error> {
        let bars = chart.interval(&range)?.to_vec();
        Ok(Self {
            selection_revision,
            snapshot: chart.snapshot.clone(),
            bars,
            range,
        })
    }

    pub(in crate::workspace) fn local_reference(&self) -> Value {
        json!({"revision_id": self.snapshot.dataset_id, "range": self.range, "snapshot_hash": self.snapshot.content_hash})
    }
}

pub(in crate::workspace) struct ModelChartBinding {
    pub(super) principal: String,
    recipient: String,
    selection_revision: Revision,
}

impl ModelChartBinding {
    pub(super) fn current<'a>(
        &self,
        tx: &dyn WorkspaceTransaction,
        sample: Option<&'a SampleChartSelection>,
    ) -> Result<&'a SampleChartSelection, Error> {
        if tx.state()?.selection_revision != self.selection_revision {
            return Err(failure(ErrorCode::STALECONTEXT));
        }
        let sample = sample
            .filter(|sample| sample.selection_revision == self.selection_revision)
            .ok_or_else(|| failure(ErrorCode::PERMISSIONDENIED))?;
        if sample.bars.iter().any(|bar| !bar.is_closed) {
            return Err(failure(ErrorCode::PERMISSIONDENIED));
        }
        if !tx.sample_source_selected(&sample.snapshot.series.instrument_id)? {
            return Err(failure(ErrorCode::STALECONTEXT));
        }
        Ok(sample)
    }
}

impl<S: WorkspaceStore> OperationRegistry<S> {
    /// The host provides a frozen response from the first-party market registry.
    /// The selection request itself can never supply market rows.
    pub fn execute_sample_evidence_selection(
        &mut self,
        access: &WorkspaceAccess<'_>,
        request: Value,
        page: Value,
    ) -> Result<Value, Error> {
        let query = sample_query(&request)?;
        let page: MarketHistoryPageOutput =
            serde_json::from_value(page).map_err(|_| failure(ErrorCode::OUTPUTINVALID))?;
        history::validate_page(&query, &page)?;
        if page.next_cursor.is_some() || page.total_bars > MAX_SAMPLE_BARS {
            return Err(failure(ErrorCode::RESOURCELIMIT));
        }
        if page.dataset.provider_id != DEMO_PROVIDER
            || page.dataset.mode.as_str() != "simulated"
            || page.dataset.provenance.get("synthetic") != Some(&json!(true))
            || page.dataset.provenance.get("generator") != Some(&json!("scopehub-synthetic-v1"))
        {
            return Err(failure(ErrorCode::SNAPSHOTUNAVAILABLE));
        }
        let chart = FrozenChart {
            snapshot: page.snapshot,
            bars: page.bars,
        };
        self.dispatch_evidence(access, EvidenceAdmission::SampleSelection(&chart), request)
    }

    pub(in crate::workspace) fn bind_model_chart(
        &self,
        selection_revision: Revision,
        principal: String,
        recipient: &str,
    ) -> Result<ModelChartBinding, Error> {
        // The first-party fixture grants these two fields for chart analysis by the
        // disclosed HTTP recipient. Local CSV and external sources have no such grant.
        // A loopback origin is still an independently operated HTTP recipient.
        let binding = ModelChartBinding {
            principal,
            recipient: recipient.into(),
            selection_revision,
        };
        self.check_model_chart(&binding, recipient)?;
        Ok(binding)
    }

    pub(in crate::workspace) fn check_model_chart(
        &self,
        binding: &ModelChartBinding,
        recipient: &str,
    ) -> Result<(), Error> {
        if binding.recipient != recipient {
            return Err(failure(ErrorCode::PERMISSIONDENIED));
        }
        self.port.inspect(|tx| {
            binding
                .current(tx, self.sample_evidence.as_ref())
                .map(|_| ())
        })
    }

    pub(in crate::workspace) fn project_model_chart(
        &mut self,
        binding: &ModelChartBinding,
        recipient: &str,
    ) -> Result<Value, Error> {
        if binding.recipient != recipient {
            return Err(failure(ErrorCode::PERMISSIONDENIED));
        }
        let profile_id = self.port.binding().profile_id.clone();
        let workspace_id = self.port.binding().workspace_id.clone();
        let access = WorkspaceAccess {
            actor: Actor::Ai,
            profile_id: &profile_id,
            workspace_id: &workspace_id,
            principal_id: &binding.principal,
            paired_client_id: None,
            operations: &[READ_CHART_PROJECTION],
            disclose_workspace_state: false,
        };
        let intent = evidence_intent_request(json!({
            "operation": READ_CHART_PROJECTION, "workspace_id": workspace_id,
            "selection_revision": binding.selection_revision, "requested_fields": MODEL_FIELDS,
        }))?;
        let reply = self.dispatch_evidence(&access, EvidenceAdmission::Model(binding), intent)?;
        let result = reply.get("result").ok_or_else(|| {
            let code = serde_json::from_value(reply["error"]["code"].clone())
                .unwrap_or(ErrorCode::OUTPUTINVALID);
            failure(code)
        })?;
        let sample = self
            .sample_evidence
            .as_ref()
            .expect("projected current sample");
        Ok(
            json!({"source_reference": result["source_reference"], "revision": result["revision"],
            "range": sample.range, "rows": result["rows"]}),
        )
    }
}
