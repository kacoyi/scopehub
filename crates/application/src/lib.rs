//! Shared operation entry for trusted transports.

mod folder_manifest;
mod folder_refresh;
mod history;
mod local_import;
mod local_publication;
mod replay;
mod reviewed_mapping;
mod workspace;

pub use folder_manifest::{
    FolderManifestBar, FolderManifestLocator, FolderManifestPublication, FolderManifestRequest,
    FolderManifestSource, FolderManifestSourceRef, FolderManifestStore, StoredFolderManifest,
};
pub use folder_refresh::{
    FolderRefreshBase, FolderRefreshCheck, FolderRefreshFile, MAX_STAGED_RECORDS,
    PREVIEW_LOCAL_FOLDER_REFRESH, folder_refresh_input, folder_refresh_output,
};
pub use local_import::{
    DEFAULT_PREVIEW_ROWS, LocalImportPort, PREVIEW_LOCAL_IMPORT, preview_local_import,
    validate_csv_preview,
};
pub use local_publication::{
    FrozenLocalSourceRow, LIST_LOCAL_IMPORT_REVISIONS, LocalDatasetPublication, LocalDatasetStore,
    PUBLISH_LOCAL_IMPORT, READ_LOCAL_IMPORT_REVISION, READ_LOCAL_IMPORT_SOURCE_ROW,
    REVOKE_LOCAL_IMPORT_REVISION, StoredLocalDataset, list_local_import_revisions_input,
    local_mapping_fingerprint, publish_local_import_input, read_local_import_revision_input,
    read_local_import_source_row_input, revoke_local_import_revision_input,
};
pub use replay::{SUBSCRIBE, SubscriptionFuture, SubscriptionPort};
pub use reviewed_mapping::{
    LOAD_LOCAL_IMPORT_MAPPING, MappingCommitGuard, ReviewedImportMapping, ReviewedMappingStore,
    SAVE_LOCAL_IMPORT_MAPPING, load_mapping_input, load_mapping_output, mapping_header_digest,
    reviewed_preview_header, save_mapping_input,
};
pub use workspace::{
    ChartEvidenceRef, ChartEvidenceStatus, DECISION_NOTE_APPEND_OUTCOME,
    DECISION_NOTE_BACKUP_MAX_BYTES, DECISION_NOTE_BACKUP_MAX_DELETIONS,
    DECISION_NOTE_BACKUP_MAX_NOTES, DECISION_NOTE_BACKUP_MAX_OUTCOMES, DECISION_NOTE_DELETE,
    DECISION_NOTE_EXPORT_BACKUP, DECISION_NOTE_OPERATIONS, DECISION_NOTE_PREVIEW_RESTORE,
    DECISION_NOTE_READ, DECISION_NOTE_RESTORE_BACKUP, DECISION_NOTE_SAVE, DecisionNoteBackupData,
    DecisionNoteChange, DecisionNoteMutation, DecisionNoteOutcomeAddition, DecisionNoteReceipt,
    DecisionNoteReplay, DecisionNoteRestorePlan, LOCAL_WORKSPACE_OPERATIONS,
    LocalChartAnalysisGrant, MAIN_CHART_ID, READ_CHART_PROJECTION, SELECT_EVIDENCE, SET_TIMEFRAME,
    Timeframe, UNDO, WorkspaceAccess, WorkspaceBinding, WorkspaceChange, WorkspaceReceipt,
    WorkspaceReplay, WorkspaceReplayKey, WorkspaceSnapshot, WorkspaceState, WorkspaceStore,
    WorkspaceTransaction, WorkspaceUndoAccess, WorkspaceUndoTarget, evidence_operation_name,
};

use std::{
    collections::HashSet,
    future::Future,
    pin::Pin,
    sync::{LazyLock, OnceLock},
};

use scopehub_domain::{
    protocol::{
        DatasetDescriptor, Error, ErrorCode, Instrument, InstrumentSearchOutput,
        MarketGetBarsInput, MarketGetBarsOutput, MarketHistoryPageInput, MarketHistoryPageOutput,
    },
    wire::{SCHEMA, normalize_integers},
};
use serde_json::{Value, json};

pub const DEMO_PROVIDER: &str = "scopehub.synthetic";
pub const LEGACY_READ_OPERATIONS: [&str; 2] = ["instrument.search", "market.getBars"];
pub const READ_OPERATIONS: [&str; 3] = [
    "instrument.search",
    "market.getBars",
    "market.readHistoryPage",
];

/// Assigned by the transport, never deserialized from a worker or UI payload.
#[derive(Clone, Copy)]
pub enum Actor {
    User,
    LocalUser,
    Ai,
    System,
}

impl Actor {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::User | Self::LocalUser => "user",
            Self::Ai => "ai",
            Self::System => "system",
        }
    }
}

pub fn failure(code: ErrorCode) -> Error {
    let message = match code {
        ErrorCode::PROVIDERNOTCONFIGURED => "No data source is configured for this request.",
        ErrorCode::UNSUPPORTEDCAPABILITY => "This operation or timeframe is not implemented.",
        ErrorCode::INVALIDARGUMENT => "The request arguments are invalid.",
        ErrorCode::PERMISSIONDENIED => "This caller cannot perform the operation.",
        ErrorCode::CANCELLED => "The operation was cancelled.",
        ErrorCode::STALECONTEXT => "The selected source is no longer current.",
        ErrorCode::DATAUNAVAILABLE => "No data is available for this instrument.",
        ErrorCode::TIMEOUT => "The market worker did not respond before the deadline.",
        ErrorCode::WORKEREXITED => "The market worker is unavailable.",
        ErrorCode::OUTPUTINVALID => "The market response failed validation.",
        ErrorCode::PROTOCOLMISMATCH => "The market worker is incompatible.",
        ErrorCode::BUSY => "The market worker is busy.",
        ErrorCode::SNAPSHOTUNAVAILABLE => "The requested market snapshot is unavailable.",
        ErrorCode::RESOURCELIMIT => "The market request exceeded a resource limit.",
        _ => "The operation could not be completed.",
    };
    Error {
        retryable: matches!(
            code,
            ErrorCode::TIMEOUT | ErrorCode::WORKEREXITED | ErrorCode::BUSY
        ),
        code,
        message: message.into(),
        details: Default::default(),
    }
}

type PortFuture<'a, T> = Pin<Box<dyn Future<Output = Result<T, Error>> + Send + 'a>>;
pub type MarketFuture<'a> = PortFuture<'a, Value>;
pub type ShutdownFuture<'a> = PortFuture<'a, ()>;

pub trait MarketPort: Send {
    fn request<'a>(&'a mut self, operation: &'a str, payload: &'a Value) -> MarketFuture<'a>;
}

/// Lifecycle access is explicit; callers cannot mutate the underlying adapter.
pub trait MarketLifecycle: Send {
    fn status(&self) -> MarketStatus;
    fn shutdown(&mut self) -> ShutdownFuture<'_>;
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct MarketStatus {
    pub process_id: Option<u32>,
}

struct Operation {
    name: String,
    actors: Vec<String>,
    input: jsonschema::Validator,
    output: jsonschema::Validator,
    wire: Option<WireBinding>,
    confirmation_required: bool,
    undoable: bool,
}

struct WireBinding {
    input: String,
    output: String,
    validators: OnceLock<(jsonschema::Validator, jsonschema::Validator)>,
}

impl Operation {
    fn authorize(&self, actor: Actor) -> Result<(), Error> {
        if !self.actors.iter().any(|allowed| allowed == actor.as_str())
            || (matches!(
                self.name.as_str(),
                PREVIEW_LOCAL_IMPORT
                    | LOAD_LOCAL_IMPORT_MAPPING
                    | SAVE_LOCAL_IMPORT_MAPPING
                    | PUBLISH_LOCAL_IMPORT
                    | LIST_LOCAL_IMPORT_REVISIONS
                    | READ_LOCAL_IMPORT_REVISION
                    | READ_LOCAL_IMPORT_SOURCE_ROW
                    | PREVIEW_LOCAL_FOLDER_REFRESH
                    | REVOKE_LOCAL_IMPORT_REVISION
            ) && !matches!(actor, Actor::LocalUser))
        {
            return Err(failure(ErrorCode::PERMISSIONDENIED));
        }
        Ok(())
    }

    fn wire_validators(&self) -> (&jsonschema::Validator, &jsonschema::Validator) {
        let binding = self
            .wire
            .as_ref()
            .expect("registered 1.1 operation binding");
        let (input, output) = binding
            .validators
            .get_or_init(|| (validator(&binding.input), validator(&binding.output)));
        (input, output)
    }
}

fn validator(definition: &str) -> jsonschema::Validator {
    let mut schema = SCHEMA.clone();
    schema["$ref"] = json!(format!("#/$defs/{definition}"));
    schema
        .as_object_mut()
        .expect("schema object")
        .remove("oneOf");
    jsonschema::options()
        .should_validate_formats(true)
        .build(&schema)
        .expect("embedded operation schema")
}

static OPERATIONS: LazyLock<Vec<Operation>> = LazyLock::new(|| {
    let manifest: Value = serde_json::from_str(include_str!("../../../contracts/contract.json"))
        .expect("embedded operation catalog");
    let protocol = manifest["protocol_version"]
        .as_str()
        .expect("catalog protocol version");
    manifest["operations"]
        .as_array()
        .expect("operation array")
        .iter()
        .map(|op| Operation {
            name: op["name"].as_str().expect("operation name").into(),
            actors: serde_json::from_value(op["actors"].clone()).expect("actor list"),
            input: validator(op["input"].as_str().expect("input definition")),
            output: validator(op["output"].as_str().expect("output definition")),
            wire: op["wire_bindings"][protocol]
                .as_object()
                .map(|binding| WireBinding {
                    input: binding["input"]
                        .as_str()
                        .expect("wire input definition")
                        .into(),
                    output: binding["output"]
                        .as_str()
                        .expect("wire output definition")
                        .into(),
                    validators: OnceLock::new(),
                }),
            confirmation_required: op["confirmation"].as_bool() != Some(false),
            undoable: op["undoable"].as_bool().expect("undo policy"),
        })
        .collect()
});

fn operation(name: &str) -> Result<&'static Operation, Error> {
    OPERATIONS
        .iter()
        .find(|op| op.name == name)
        .ok_or_else(|| failure(ErrorCode::UNSUPPORTEDCAPABILITY))
}

fn authorized_operation(actor: Actor, name: &str) -> Result<&'static Operation, Error> {
    let op = operation(name)?;
    op.authorize(actor)?;
    Ok(op)
}

/// Authorized operations and explicit lifecycle, without mutable adapter access.
///
/// ```compile_fail
/// use scopehub_application::{MarketPort, MarketRegistry};
/// fn bypass<P: MarketPort>(registry: &mut MarketRegistry<P>) {
///     registry.port_mut();
/// }
/// ```
pub struct OperationRegistry<P> {
    port: P,
    note_confirmation: Option<workspace::PendingDecisionNoteConfirmation>,
    note_restore: workspace::DecisionNoteRestoreStaging,
}

pub type MarketRegistry<P> = OperationRegistry<P>;

enum ReadFailure {
    Worker(Error),
    Local(Error),
}

impl ReadFailure {
    fn error(&self) -> &Error {
        match self {
            Self::Worker(error) | Self::Local(error) => error,
        }
    }

    fn into_error(self) -> Error {
        match self {
            Self::Worker(error) | Self::Local(error) => error,
        }
    }
}

impl From<Error> for ReadFailure {
    fn from(error: Error) -> Self {
        Self::Local(error)
    }
}

impl<P> OperationRegistry<P> {
    pub fn new(port: P) -> Self {
        Self {
            port,
            note_confirmation: None,
            note_restore: workspace::DecisionNoteRestoreStaging::default(),
        }
    }
}

impl<P: MarketPort> MarketRegistry<P> {
    pub async fn execute(
        &mut self,
        actor: Actor,
        name: &str,
        payload: Value,
    ) -> Result<Value, Error> {
        self.execute_read(actor, name, payload)
            .await
            .map_err(ReadFailure::into_error)
    }

    async fn execute_read(
        &mut self,
        actor: Actor,
        name: &str,
        mut payload: Value,
    ) -> Result<Value, ReadFailure> {
        let op = authorized_operation(actor, name)?;
        if !READ_OPERATIONS.contains(&name) {
            return Err(failure(ErrorCode::UNSUPPORTEDCAPABILITY).into());
        }
        if !op.input.is_valid(&payload) {
            return Err(failure(ErrorCode::INVALIDARGUMENT).into());
        }
        normalize_integers(&mut payload);
        if name == "instrument.search" {
            let (value, _) = self.catalog(&payload).await?;
            return Ok(value);
        }
        let series = if name == "market.getBars" {
            &payload
        } else {
            &payload["series"]
        };
        if series["provider_id"] != DEMO_PROVIDER {
            return Err(failure(ErrorCode::PROVIDERNOTCONFIGURED).into());
        }
        if payload["from_ms"].as_i64() >= payload["to_ms"].as_i64() {
            return Err(failure(ErrorCode::INVALIDARGUMENT).into());
        }
        let page_input = if name == "market.readHistoryPage" {
            let input: MarketHistoryPageInput = serde_json::from_value(payload.clone())
                .map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
            history::validate_page_input(&input)?;
            Some(input)
        } else {
            None
        };
        let pinned = page_input
            .as_ref()
            .is_some_and(|i| i.snapshot.is_some() || i.expected_revision.is_some());
        let read_error = |error: ReadFailure| {
            if pinned
                && matches!(
                    error.error().code,
                    ErrorCode::DATAUNAVAILABLE | ErrorCode::PROVIDERNOTCONFIGURED
                )
            {
                failure(ErrorCode::SNAPSHOTUNAVAILABLE).into()
            } else {
                error
            }
        };
        // Every read, including a direct AI call without a preceding search, is
        // checked against a catalog entry from the same authorized source.
        let (_, catalog) = self
            .catalog(&json!({"query":series["instrument_id"],"limit":50}))
            .await
            .map_err(read_error)?;
        let instrument = catalog
            .instruments
            .into_iter()
            .find(|item| series["instrument_id"] == item.id)
            .ok_or_else(|| read_error(failure(ErrorCode::DATAUNAVAILABLE).into()))?;
        if let Some(input) = &page_input {
            history::validate_series(&input.series, &instrument)?;
        }
        let (output, checked) = self
            .checked_request(op, &payload)
            .await
            .map_err(read_error)?;
        if let Some(input) = page_input {
            let page: MarketHistoryPageOutput =
                serde_json::from_value(checked).map_err(|_| failure(ErrorCode::OUTPUTINVALID))?;
            validate_dataset(
                &page.dataset,
                &instrument,
                &input.series.provider_id,
                &input.series.timeframe,
            )?;
            history::validate_page(&input, &page)?;
        } else {
            let input: MarketGetBarsInput =
                serde_json::from_value(payload).map_err(|_| failure(ErrorCode::INVALIDARGUMENT))?;
            let result: MarketGetBarsOutput =
                serde_json::from_value(checked).map_err(|_| failure(ErrorCode::OUTPUTINVALID))?;
            validate_dataset(
                &result.dataset,
                &instrument,
                &input.provider_id,
                &input.timeframe,
            )?;
            history::validate_bars(
                &result.bars,
                input.from_ms,
                input.to_ms,
                0,
                0,
                result.bars.len() as u64,
            )?;
        }
        Ok(output)
    }

    async fn checked_request(
        &mut self,
        op: &Operation,
        payload: &Value,
    ) -> Result<(Value, Value), ReadFailure> {
        let output = self
            .port
            .request(&op.name, payload)
            .await
            .map_err(ReadFailure::Worker)?;
        if !op.output.is_valid(&output) {
            return Err(failure(ErrorCode::OUTPUTINVALID).into());
        }
        let mut checked = output.clone();
        normalize_integers(&mut checked);
        Ok((output, checked))
    }

    async fn catalog(
        &mut self,
        payload: &Value,
    ) -> Result<(Value, InstrumentSearchOutput), ReadFailure> {
        let op = OPERATIONS
            .iter()
            .find(|op| op.name == "instrument.search")
            .expect("catalog operation");
        let (value, checked) = self.checked_request(op, payload).await?;
        let catalog: InstrumentSearchOutput =
            serde_json::from_value(checked).map_err(|_| failure(ErrorCode::OUTPUTINVALID))?;
        let mut ids = HashSet::new();
        if catalog.instruments.len() > payload["limit"].as_u64().unwrap_or(0) as usize
            || catalog.instruments.iter().any(|item| {
                !ids.insert(&item.id)
                    || item.tradability.as_str() != "non_tradable"
                    || item.metadata.get("synthetic") != Some(&json!(true))
                    || !item
                        .provider_mappings
                        .iter()
                        .any(|mapping| mapping.provider_id == DEMO_PROVIDER)
            })
        {
            return Err(failure(ErrorCode::OUTPUTINVALID).into());
        }
        Ok((value, catalog))
    }
}

impl<P: MarketPort + MarketLifecycle> MarketRegistry<P> {
    pub fn status(&self) -> MarketStatus {
        self.port.status()
    }

    pub async fn shutdown(&mut self) -> Result<(), Error> {
        self.port.shutdown().await
    }
}

fn validate_dataset(
    data: &DatasetDescriptor,
    instrument: &Instrument,
    provider: &str,
    timeframe: &str,
) -> Result<(), Error> {
    if data.instrument_id != instrument.id
        || data.provider_id != provider
        || data.timeframe != timeframe
        || data.mode.as_str() != "simulated"
        || data.price_adjustment != "raw"
        || data.provenance.get("synthetic") != Some(&json!(true))
        || data.timezone != instrument.timezone
        || data.calendar_id != instrument.calendar_id
        || data.quantity_unit != instrument.quantity_unit
    {
        return Err(failure(ErrorCode::OUTPUTINVALID));
    }
    Ok(())
}
