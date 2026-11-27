//! Register explicit chart evidence selection and field-scoped reads.

use std::sync::LazyLock;

use scopehub_domain::{
    protocol::{
        Bar, Error, ErrorCode, EvidenceTarget11, ExportField11, FrozenSnapshot,
        ProjectedChartArguments11, SelectEvidenceArguments11, SeriesKey, SharedSelection11,
        VisibleRange,
    },
    wire::normalize_integers,
};
use serde_json::{Value, json};
use uuid::Uuid;

use super::{MAX_REVISION, WorkspaceAccess, WorkspaceStore, WorkspaceTransaction, permits_scope};
use crate::{
    Actor, OperationRegistry, READ_LOCAL_IMPORT_REVISION, StoredLocalDataset, failure,
    history::{add_bar, snapshot_seed},
    local_publication::frozen_output,
    operation, validator,
};

mod sample;
pub(super) use sample::ModelChartBinding;
pub(crate) use sample::SampleChartSelection;
pub use sample::{READ_SAMPLE_EVIDENCE_HISTORY, is_sample_evidence_selection};

pub const SELECT_EVIDENCE: &str = "workspace.selectEvidence";
pub const READ_CHART_PROJECTION: &str = "workspace.readChartProjection";
const LOCAL_CSV_PROVIDER: &str = "scopehub.local-csv";
static SELECT_INTENT: LazyLock<jsonschema::Validator> =
    LazyLock::new(|| validator("SelectEvidenceIntent11"));
static PROJECT_INTENT: LazyLock<jsonschema::Validator> =
    LazyLock::new(|| validator("ProjectedReadIntent11"));

#[derive(Clone, Debug, PartialEq)]
pub struct ChartEvidenceRef {
    pub revision_id: String,
    pub range: VisibleRange,
}

pub enum ChartEvidenceStatus {
    Available(ChartEvidenceRef),
    Unavailable,
}

/// Effective, current user grant and source entitlement for a trusted on-device
/// recipient, resolved for this dispatch. Local display alone gives no AI grant.
pub struct LocalChartAnalysisGrant<'a> {
    pub principal_id: &'a str,
    pub revision_id: &'a str,
    pub range: &'a VisibleRange,
    pub fields: &'a [ExportField11],
}

enum EvidenceAdmission<'a> {
    Workspace,
    LocalAnalysis(Option<&'a LocalChartAnalysisGrant<'a>>),
    Model(&'a ModelChartBinding),
    SampleSelection(&'a FrozenChart),
}

pub fn evidence_operation_name(request: &Value) -> Option<&'static str> {
    match request["operation"].as_str() {
        Some(SELECT_EVIDENCE) => Some(SELECT_EVIDENCE),
        Some(READ_CHART_PROJECTION) => Some(READ_CHART_PROJECTION),
        _ => None,
    }
}

pub(super) fn local_display(access: &WorkspaceAccess<'_>) -> bool {
    matches!(access.actor, Actor::User) && access.paired_client_id.is_none()
}

impl<S: WorkspaceStore> OperationRegistry<S> {
    /// Open the published chart for the local human profile. The snapshot is
    /// derived from the exact validated frozen bars this view receives.
    pub fn open_evidence_chart(
        &self,
        access: &WorkspaceAccess<'_>,
        revision_id: &str,
    ) -> Result<Value, Error> {
        self.authorize_evidence_display(access)?;
        self.port.inspect(|tx| {
            let dataset = entitled_dataset(tx, revision_id)?;
            let mut output =
                frozen_output(access.actor, &dataset.summary(), &dataset.content["bars"])?;
            output["snapshot"] = json!(FrozenChart::new(&dataset)?.snapshot);
            Ok(output)
        })
    }

    /// Check the current local display entitlement without materializing the stored chart.
    pub fn evidence_chart_display_allowed(
        &self,
        access: &WorkspaceAccess<'_>,
        revision_id: &str,
    ) -> Result<bool, Error> {
        self.authorize_evidence_display(access)?;
        self.port
            .inspect(|tx| tx.local_evidence_display_allowed(revision_id))
    }

    /// Distinguish missing bytes from a deliberately withdrawn revision.
    pub fn evidence_chart_display_status(
        &self,
        access: &WorkspaceAccess<'_>,
        revision_id: &str,
    ) -> Result<Value, Error> {
        use super::FrozenWorkspaceSourceStatus::{Available, Revoked, Unavailable};

        self.authorize_evidence_display(access)?;
        self.port.inspect(|tx| {
            Ok(match tx.local_evidence_display_status(revision_id)? {
                Available => json!({"available": true}),
                Revoked => json!({"available": false, "revoked": true}),
                Unavailable => json!({"available": false}),
            })
        })
    }

    fn authorize_evidence_display(&self, access: &WorkspaceAccess<'_>) -> Result<(), Error> {
        if !matches!(access.actor, Actor::LocalUser)
            || access.principal_id != access.profile_id
            || access.paired_client_id.is_some()
            || !permits_scope(access, self.port.binding())
            || !access.operations.contains(&READ_LOCAL_IMPORT_REVISION)
        {
            return Err(failure(ErrorCode::PERMISSIONDENIED));
        }
        Ok(())
    }

    pub(super) fn execute_evidence_request(
        &mut self,
        access: &WorkspaceAccess<'_>,
        request: Value,
    ) -> Result<Value, Error> {
        self.dispatch_evidence(access, EvidenceAdmission::Workspace, request)
    }

    /// Intent admission is for a trusted, local AI integration only. It accepts
    /// exactly the registered AI intent shapes and dispatches the same request
    /// as the local human client. No model or external transport opens this grant.
    pub fn execute_evidence_intent(
        &mut self,
        access: &WorkspaceAccess<'_>,
        grant: Option<&LocalChartAnalysisGrant<'_>>,
        intent: Value,
    ) -> Result<Value, Error> {
        if !matches!(access.actor, Actor::Ai) {
            return Err(failure(ErrorCode::PERMISSIONDENIED));
        }
        self.dispatch_evidence(
            access,
            EvidenceAdmission::LocalAnalysis(grant),
            evidence_intent_request(intent)?,
        )
    }

    fn dispatch_evidence(
        &mut self,
        access: &WorkspaceAccess<'_>,
        admission: EvidenceAdmission<'_>,
        mut request: Value,
    ) -> Result<Value, Error> {
        let name = evidence_operation_name(&request)
            .ok_or_else(|| failure(ErrorCode::UNSUPPORTEDCAPABILITY))?;
        let op = operation(name)?;
        let (input, output) = op.wire_validators();
        if !input.is_valid(&request) {
            return Err(failure(ErrorCode::INVALIDARGUMENT));
        }
        normalize_integers(&mut request);

        let reply = |field: &str, body: Value| {
            let mut frame = json!({
                "type": "response", "protocol_version": "1.1", "operation": name,
                "request_id": request["request_id"], "message_id": Uuid::new_v4().to_string(),
                "timestamp_ms": super::timestamp_ms()?,
            });
            frame[field] = body;
            if !output.is_valid(&frame) {
                return Err(failure(ErrorCode::OUTPUTINVALID));
            }
            Ok(frame)
        };

        let result = (|| {
            op.authorize(access.actor)?;
            if !permits_scope(access, self.port.binding())
                || request["workspace_id"] != access.workspace_id
                || !access.operations.contains(&name)
            {
                return Err(failure(ErrorCode::PERMISSIONDENIED));
            }
            let grant = match (&admission, access.actor) {
                (
                    EvidenceAdmission::Workspace | EvidenceAdmission::SampleSelection(_),
                    Actor::User,
                ) if local_display(access) => None,
                (EvidenceAdmission::LocalAnalysis(grant), Actor::Ai)
                    if access.paired_client_id.is_none() =>
                {
                    Some(
                        grant
                            .filter(|grant| grant.principal_id == access.principal_id)
                            .ok_or_else(|| failure(ErrorCode::PERMISSIONDENIED))?,
                    )
                }
                (EvidenceAdmission::Model(binding), Actor::Ai)
                    if name == READ_CHART_PROJECTION
                        && access.paired_client_id.is_none()
                        && access.principal_id == binding.principal =>
                {
                    None
                }
                _ => return Err(failure(ErrorCode::PERMISSIONDENIED)),
            };
            let mut arguments = request.as_object().unwrap().clone();
            for field in [
                "type",
                "protocol_version",
                "operation",
                "request_id",
                "message_id",
                "timestamp_ms",
            ] {
                arguments.remove(field);
            }
            let arguments = Value::Object(arguments);
            if name == SELECT_EVIDENCE {
                let args: SelectEvidenceArguments11 = serde_json::from_value(arguments)
                    .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
                let sample = match admission {
                    EvidenceAdmission::SampleSelection(chart) => Some(chart),
                    _ => None,
                };
                let (frame, selected) = self.port.transact(|tx| {
                    if grant.is_some()
                        && args.target.is_none()
                        && let Some(current) = &self.sample_evidence
                        && tx.state()?.selection_revision == current.selection_revision
                    {
                        return Err(failure(ErrorCode::PERMISSIONDENIED));
                    }
                    let (selection, sample) = select(tx, args, grant, sample)?;
                    Ok((reply("selection", json!(selection))?, sample))
                })?;
                self.sample_evidence = selected;
                self.invalidate_text_chart();
                Ok(frame)
            } else {
                let args: ProjectedChartArguments11 = serde_json::from_value(arguments)
                    .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
                self.port.inspect(|tx| {
                    let sample = match admission {
                        EvidenceAdmission::Model(binding) => {
                            if args.requested_fields != sample::MODEL_FIELDS {
                                return Err(failure(ErrorCode::PERMISSIONDENIED));
                            }
                            let sample = binding.current(tx, self.sample_evidence.as_ref())?;
                            Some(sample)
                        }
                        EvidenceAdmission::Workspace => self.sample_evidence.as_ref(),
                        _ => None,
                    };
                    reply("result", project(tx, args, grant, sample)?)
                })
            }
        })();
        match result {
            Ok(value) => Ok(value),
            Err(error) => {
                let error = super::workspace_failure(error.code);
                reply(
                    "error",
                    json!({
                        "code": error.code, "message": error.message, "retryable": error.retryable,
                    }),
                )
            }
        }
    }
}

fn evidence_intent_request(mut intent: Value) -> Result<Value, Error> {
    let shape = match evidence_operation_name(&intent) {
        Some(SELECT_EVIDENCE) => &*SELECT_INTENT,
        Some(READ_CHART_PROJECTION) => &*PROJECT_INTENT,
        _ => return Err(failure(ErrorCode::UNSUPPORTEDCAPABILITY)),
    };
    if !shape.is_valid(&intent) {
        return Err(failure(ErrorCode::INVALIDARGUMENT));
    }
    intent["type"] = json!("request");
    intent["protocol_version"] = json!("1.1");
    intent["message_id"] = json!(Uuid::new_v4().to_string());
    intent["request_id"] = json!(Uuid::new_v4().to_string());
    intent["timestamp_ms"] = json!(super::timestamp_ms()?);
    Ok(intent)
}

fn entitled_dataset(
    tx: &dyn WorkspaceTransaction,
    revision_id: &str,
) -> Result<StoredLocalDataset, Error> {
    tx.local_evidence_dataset(revision_id)?
        .ok_or_else(|| failure(ErrorCode::SNAPSHOTUNAVAILABLE))
}

fn check_target_grant(
    target: &ChartEvidenceRef,
    grant: Option<&LocalChartAnalysisGrant<'_>>,
) -> Result<(), Error> {
    if grant.is_some_and(|grant| {
        target.revision_id != grant.revision_id
            || target.range.from_ms < grant.range.from_ms
            || target.range.to_ms > grant.range.to_ms
    }) {
        return Err(failure(ErrorCode::PERMISSIONDENIED));
    }
    Ok(())
}

pub(super) fn authorize_analysis_target(
    tx: &dyn WorkspaceTransaction,
    target: &ChartEvidenceRef,
    grant: &LocalChartAnalysisGrant<'_>,
) -> Result<(), Error> {
    check_target_grant(target, Some(grant))?;
    entitled_dataset(tx, &target.revision_id)?;
    Ok(())
}

fn select(
    tx: &mut dyn WorkspaceTransaction,
    args: SelectEvidenceArguments11,
    grant: Option<&LocalChartAnalysisGrant<'_>>,
    sample: Option<&FrozenChart>,
) -> Result<(SharedSelection11, Option<SampleChartSelection>), Error> {
    let current = tx.state()?.selection_revision;
    if current != args.expected_selection_revision {
        return Err(failure(ErrorCode::STALECONTEXT));
    }
    let selection_revision = current
        .checked_add(1)
        .filter(|revision| *revision <= MAX_REVISION)
        .ok_or_else(|| failure(ErrorCode::RESOURCELIMIT))?;
    let mut selected_sample = None;
    let target = match &args.target {
        Some(EvidenceTarget11::ChartInterval11(interval)) => {
            let reference = ChartEvidenceRef {
                revision_id: interval.snapshot.dataset_id.clone(),
                range: interval.range.clone(),
            };
            check_target_grant(&reference, grant)?;
            if let Some(chart) = sample {
                if interval.snapshot != chart.snapshot
                    || !tx.sample_source_selected(&chart.snapshot.series.instrument_id)?
                {
                    return Err(failure(ErrorCode::STALECONTEXT));
                }
                selected_sample = Some(SampleChartSelection::new(
                    selection_revision,
                    chart,
                    reference.range,
                )?);
                None
            } else {
                let chart = FrozenChart::new(&entitled_dataset(tx, &reference.revision_id)?)?;
                if interval.snapshot != chart.snapshot {
                    return Err(failure(ErrorCode::SNAPSHOTUNAVAILABLE));
                }
                chart.interval(&reference.range)?;
                Some(reference)
            }
        }
        Some(_) => return Err(failure(ErrorCode::UNSUPPORTEDCAPABILITY)),
        None => {
            // A scoped AI may clear a selection it could access. A local human
            // may also clear a now-unavailable source without disclosing it.
            if let Some(grant) = grant
                && let Some(target) = tx.chart_evidence()?
            {
                authorize_analysis_target(tx, &target, grant)?;
            }
            None
        }
    };
    tx.set_chart_evidence(selection_revision, target.as_ref())?;
    Ok((
        SharedSelection11 {
            workspace_id: args.workspace_id,
            selection_revision,
            target: args.target,
        },
        selected_sample,
    ))
}

fn project(
    tx: &dyn WorkspaceTransaction,
    args: ProjectedChartArguments11,
    grant: Option<&LocalChartAnalysisGrant<'_>>,
    sample: Option<&SampleChartSelection>,
) -> Result<Value, Error> {
    if grant.is_some_and(|grant| {
        args.requested_fields
            .iter()
            .any(|field| !grant.fields.contains(field))
    }) {
        return Err(failure(ErrorCode::PERMISSIONDENIED));
    }
    if tx.state()?.selection_revision != args.selection_revision {
        return Err(failure(ErrorCode::STALECONTEXT));
    }
    if let Some(sample) =
        sample.filter(|sample| sample.selection_revision == args.selection_revision)
    {
        return project_rows(&sample.snapshot, &sample.bars, args.requested_fields);
    }
    let target = tx
        .chart_evidence()?
        .ok_or_else(|| failure(ErrorCode::SNAPSHOTUNAVAILABLE))?;
    check_target_grant(&target, grant)?;
    let chart = FrozenChart::new(&entitled_dataset(tx, &target.revision_id)?)?;
    project_rows(
        &chart.snapshot,
        chart.interval(&target.range)?,
        args.requested_fields,
    )
}

fn project_rows(
    snapshot: &FrozenSnapshot,
    bars: &[Bar],
    fields: Vec<ExportField11>,
) -> Result<Value, Error> {
    if bars.len() > 1_000 {
        return Err(failure(ErrorCode::RESOURCELIMIT));
    }

    let mut rows = Vec::with_capacity(bars.len());
    for bar in bars {
        let bar = json!(bar);
        let row: serde_json::Map<_, _> = fields
            .iter()
            .map(|field| (field.as_str().into(), bar[field.as_str()].clone()))
            .collect();
        rows.push(row);
    }
    Ok(json!({
        "source_reference": snapshot.dataset_id, "revision": snapshot.dataset_revision,
        "fields": fields, "rows": rows,
    }))
}

pub(super) struct FrozenChart {
    pub(super) snapshot: FrozenSnapshot,
    pub(super) bars: Vec<Bar>,
}

impl FrozenChart {
    pub(super) fn new(dataset: &StoredLocalDataset) -> Result<Self, Error> {
        let series = &dataset.content["series"];
        let series_text = |field| {
            series[field]
                .as_str()
                .map(str::to_owned)
                .ok_or_else(|| failure(ErrorCode::OUTPUTINVALID))
        };
        let bars: Vec<Bar> = dataset.content["bars"]
            .as_array()
            .ok_or_else(|| failure(ErrorCode::OUTPUTINVALID))?
            .iter()
            .map(|entry| serde_json::from_value(entry["bar"].clone()))
            .collect::<Result<_, _>>()
            .map_err(|_| failure(ErrorCode::OUTPUTINVALID))?;
        let from_ms = bars
            .first()
            .ok_or_else(|| failure(ErrorCode::OUTPUTINVALID))?
            .time_ms;
        let to_ms = bars
            .last()
            .and_then(|bar| bar.time_ms.checked_add(1))
            .filter(|end| *end <= MAX_REVISION as i64)
            .ok_or_else(|| failure(ErrorCode::OUTPUTINVALID))?;
        // Published CSVs carry no observation clock. This immutable, closed-bar
        // snapshot uses the exclusive bar-start coverage boundary as its cutoff.
        let mut snapshot = FrozenSnapshot {
            series: SeriesKey {
                instrument_id: dataset.revision_id.clone(),
                provider_id: LOCAL_CSV_PROVIDER.into(),
                timeframe: series_text("timeframe")?,
                price_adjustment: series_text("price_adjustment")?,
                session_scope: series_text("session_scope")?,
                calendar_id: series_text("calendar_id")?,
                quantity_unit: series_text("quantity_unit")?,
            },
            dataset_id: dataset.revision_id.clone(),
            dataset_revision: dataset.revision_id.clone(),
            from_ms,
            to_ms,
            warmup_bars: 0,
            as_of_ms: to_ms,
            content_hash: String::new(),
        };
        let seed = snapshot_seed(&snapshot, &series_text("timezone")?);
        snapshot.content_hash = hex::encode(bars.iter().fold(seed, add_bar));
        Ok(Self { snapshot, bars })
    }

    pub(super) fn interval(&self, range: &VisibleRange) -> Result<&[Bar], Error> {
        if range.from_ms >= range.to_ms
            || range.from_ms < self.snapshot.from_ms
            || range.to_ms > self.snapshot.to_ms
        {
            return Err(failure(ErrorCode::INVALIDARGUMENT));
        }
        let start = self.bars.partition_point(|bar| bar.time_ms < range.from_ms);
        let end = self.bars.partition_point(|bar| bar.time_ms < range.to_ms);
        if start == end {
            return Err(failure(ErrorCode::SNAPSHOTUNAVAILABLE));
        }
        Ok(&self.bars[start..end])
    }
}
