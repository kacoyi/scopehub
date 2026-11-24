// Generated from contracts/schemas/protocol.schema.json; sha256=2508b13a207b8a8b993fe9f1c8a7c8fc5e02707b39f8a732bdfacec59c923c2f. Do not edit.
// Structural types and literal discriminators: validate schema refinements before deserializing.

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct ConstBool<const EXPECTED: bool>;
impl<const EXPECTED: bool> serde::Serialize for ConstBool<EXPECTED> {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        serializer.serialize_bool(EXPECTED)
    }
}
impl<'de, const EXPECTED: bool> serde::Deserialize<'de> for ConstBool<EXPECTED> {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        if <bool as serde::Deserialize>::deserialize(deserializer)? == EXPECTED {
            Ok(Self)
        } else {
            Err(serde::de::Error::custom(
                "Boolean discriminator differs from its schema constant",
            ))
        }
    }
}

fn present_nullable<'de, D, T>(deserializer: D) -> Result<Option<Option<T>>, D::Error>
where
    D: serde::Deserializer<'de>,
    T: serde::Deserialize<'de>,
{
    <Option<T> as serde::Deserialize>::deserialize(deserializer).map(Some)
}
pub type OpaqueId = String;
pub type Uuid = String;
pub type Revision = u64;
pub type Timestamp = i64;
pub type Metadata = serde_json::Map<String, serde_json::Value>;
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum WorkerRole {
    #[serde(rename = "market")]
    Market,
    #[serde(rename = "ai")]
    Ai,
    #[serde(rename = "indicator")]
    Indicator,
}
impl WorkerRole {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Market => "market",
            Self::Ai => "ai",
            Self::Indicator => "indicator",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ProtocolVersion {
    #[serde(rename = "1.0")]
    Value,
}
impl ProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.0",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ErrorCode {
    #[serde(rename = "INVALID_ARGUMENT")]
    INVALIDARGUMENT,
    #[serde(rename = "UNSUPPORTED_CAPABILITY")]
    UNSUPPORTEDCAPABILITY,
    #[serde(rename = "PERMISSION_DENIED")]
    PERMISSIONDENIED,
    #[serde(rename = "CONFIRMATION_REQUIRED")]
    CONFIRMATIONREQUIRED,
    #[serde(rename = "CANCELLED")]
    CANCELLED,
    #[serde(rename = "TIMEOUT")]
    TIMEOUT,
    #[serde(rename = "WORKER_EXITED")]
    WORKEREXITED,
    #[serde(rename = "PROTOCOL_MISMATCH")]
    PROTOCOLMISMATCH,
    #[serde(rename = "DATA_UNAVAILABLE")]
    DATAUNAVAILABLE,
    #[serde(rename = "STALE_CONTEXT")]
    STALECONTEXT,
    #[serde(rename = "RESOURCE_LIMIT")]
    RESOURCELIMIT,
    #[serde(rename = "OUTPUT_INVALID")]
    OUTPUTINVALID,
    #[serde(rename = "PROVIDER_NOT_CONFIGURED")]
    PROVIDERNOTCONFIGURED,
    #[serde(rename = "INTERNAL_ERROR")]
    INTERNALERROR,
    #[serde(rename = "BUSY")]
    BUSY,
    #[serde(rename = "SNAPSHOT_UNAVAILABLE")]
    SNAPSHOTUNAVAILABLE,
}
impl ErrorCode {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::INVALIDARGUMENT => "INVALID_ARGUMENT",
            Self::UNSUPPORTEDCAPABILITY => "UNSUPPORTED_CAPABILITY",
            Self::PERMISSIONDENIED => "PERMISSION_DENIED",
            Self::CONFIRMATIONREQUIRED => "CONFIRMATION_REQUIRED",
            Self::CANCELLED => "CANCELLED",
            Self::TIMEOUT => "TIMEOUT",
            Self::WORKEREXITED => "WORKER_EXITED",
            Self::PROTOCOLMISMATCH => "PROTOCOL_MISMATCH",
            Self::DATAUNAVAILABLE => "DATA_UNAVAILABLE",
            Self::STALECONTEXT => "STALE_CONTEXT",
            Self::RESOURCELIMIT => "RESOURCE_LIMIT",
            Self::OUTPUTINVALID => "OUTPUT_INVALID",
            Self::PROVIDERNOTCONFIGURED => "PROVIDER_NOT_CONFIGURED",
            Self::INTERNALERROR => "INTERNAL_ERROR",
            Self::BUSY => "BUSY",
            Self::SNAPSHOTUNAVAILABLE => "SNAPSHOT_UNAVAILABLE",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum OperationName {
    #[serde(rename = "instrument.search")]
    InstrumentSearch,
    #[serde(rename = "market.getBars")]
    MarketGetBars,
    #[serde(rename = "workspace.openInstrument")]
    WorkspaceOpenInstrument,
    #[serde(rename = "chart.setTimeframe")]
    ChartSetTimeframe,
    #[serde(rename = "chart.setStyle")]
    ChartSetStyle,
    #[serde(rename = "chart.addAnnotation")]
    ChartAddAnnotation,
    #[serde(rename = "chart.removeLayer")]
    ChartRemoveLayer,
    #[serde(rename = "indicator.createDraft")]
    IndicatorCreateDraft,
    #[serde(rename = "indicator.runPreview")]
    IndicatorRunPreview,
    #[serde(rename = "indicator.acceptVersion")]
    IndicatorAcceptVersion,
    #[serde(rename = "indicator.remove")]
    IndicatorRemove,
    #[serde(rename = "workspace.undo")]
    WorkspaceUndo,
    #[serde(rename = "task.cancel")]
    TaskCancel,
    #[serde(rename = "workspace.getContext")]
    WorkspaceGetContext,
    #[serde(rename = "market.readHistoryPage")]
    MarketReadHistoryPage,
    #[serde(rename = "market.subscribe")]
    MarketSubscribe,
    #[serde(rename = "market.previewLocalImport")]
    MarketPreviewLocalImport,
    #[serde(rename = "market.saveLocalImportMapping")]
    MarketSaveLocalImportMapping,
    #[serde(rename = "market.loadLocalImportMapping")]
    MarketLoadLocalImportMapping,
    #[serde(rename = "market.listLocalImportRevisions")]
    MarketListLocalImportRevisions,
    #[serde(rename = "market.readLocalImportRevision")]
    MarketReadLocalImportRevision,
    #[serde(rename = "market.readLocalImportSourceRow")]
    MarketReadLocalImportSourceRow,
    #[serde(rename = "market.previewLocalFolderRefresh")]
    MarketPreviewLocalFolderRefresh,
    #[serde(rename = "market.prepareLocalFolderPublication")]
    MarketPrepareLocalFolderPublication,
    #[serde(rename = "market.publishLocalFolderRefresh")]
    MarketPublishLocalFolderRefresh,
    #[serde(rename = "market.listLocalFolderRevisions")]
    MarketListLocalFolderRevisions,
    #[serde(rename = "market.readLocalFolderRevision")]
    MarketReadLocalFolderRevision,
    #[serde(rename = "market.readLocalFolderSourceRow")]
    MarketReadLocalFolderSourceRow,
    #[serde(rename = "market.revokeLocalFolderRevision")]
    MarketRevokeLocalFolderRevision,
    #[serde(rename = "market.revokeLocalImportRevision")]
    MarketRevokeLocalImportRevision,
    #[serde(rename = "market.publishLocalImport")]
    MarketPublishLocalImport,
    #[serde(rename = "workspace.readChartProjection")]
    WorkspaceReadChartProjection,
    #[serde(rename = "workspace.selectEvidence")]
    WorkspaceSelectEvidence,
    #[serde(rename = "decisionNote.save")]
    DecisionNoteSave,
    #[serde(rename = "decisionNote.read")]
    DecisionNoteRead,
    #[serde(rename = "decisionNote.appendOutcome")]
    DecisionNoteAppendOutcome,
    #[serde(rename = "decisionNote.delete")]
    DecisionNoteDelete,
    #[serde(rename = "decisionNote.exportBackup")]
    DecisionNoteExportBackup,
    #[serde(rename = "decisionNote.previewRestore")]
    DecisionNotePreviewRestore,
    #[serde(rename = "decisionNote.restoreBackup")]
    DecisionNoteRestoreBackup,
    #[serde(rename = "privateRecord.read")]
    PrivateRecordRead,
    #[serde(rename = "privateRecord.change")]
    PrivateRecordChange,
    #[serde(rename = "privateRecord.exportBackup")]
    PrivateRecordExportBackup,
    #[serde(rename = "workspace.previewLayout")]
    WorkspacePreviewLayout,
    #[serde(rename = "workspace.applyLayout")]
    WorkspaceApplyLayout,
    #[serde(rename = "ai.textConnection")]
    AiTextConnection,
}
impl OperationName {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::InstrumentSearch => "instrument.search",
            Self::MarketGetBars => "market.getBars",
            Self::WorkspaceOpenInstrument => "workspace.openInstrument",
            Self::ChartSetTimeframe => "chart.setTimeframe",
            Self::ChartSetStyle => "chart.setStyle",
            Self::ChartAddAnnotation => "chart.addAnnotation",
            Self::ChartRemoveLayer => "chart.removeLayer",
            Self::IndicatorCreateDraft => "indicator.createDraft",
            Self::IndicatorRunPreview => "indicator.runPreview",
            Self::IndicatorAcceptVersion => "indicator.acceptVersion",
            Self::IndicatorRemove => "indicator.remove",
            Self::WorkspaceUndo => "workspace.undo",
            Self::TaskCancel => "task.cancel",
            Self::WorkspaceGetContext => "workspace.getContext",
            Self::MarketReadHistoryPage => "market.readHistoryPage",
            Self::MarketSubscribe => "market.subscribe",
            Self::MarketPreviewLocalImport => "market.previewLocalImport",
            Self::MarketSaveLocalImportMapping => "market.saveLocalImportMapping",
            Self::MarketLoadLocalImportMapping => "market.loadLocalImportMapping",
            Self::MarketListLocalImportRevisions => "market.listLocalImportRevisions",
            Self::MarketReadLocalImportRevision => "market.readLocalImportRevision",
            Self::MarketReadLocalImportSourceRow => "market.readLocalImportSourceRow",
            Self::MarketPreviewLocalFolderRefresh => "market.previewLocalFolderRefresh",
            Self::MarketPrepareLocalFolderPublication => "market.prepareLocalFolderPublication",
            Self::MarketPublishLocalFolderRefresh => "market.publishLocalFolderRefresh",
            Self::MarketListLocalFolderRevisions => "market.listLocalFolderRevisions",
            Self::MarketReadLocalFolderRevision => "market.readLocalFolderRevision",
            Self::MarketReadLocalFolderSourceRow => "market.readLocalFolderSourceRow",
            Self::MarketRevokeLocalFolderRevision => "market.revokeLocalFolderRevision",
            Self::MarketRevokeLocalImportRevision => "market.revokeLocalImportRevision",
            Self::MarketPublishLocalImport => "market.publishLocalImport",
            Self::WorkspaceReadChartProjection => "workspace.readChartProjection",
            Self::WorkspaceSelectEvidence => "workspace.selectEvidence",
            Self::DecisionNoteSave => "decisionNote.save",
            Self::DecisionNoteRead => "decisionNote.read",
            Self::DecisionNoteAppendOutcome => "decisionNote.appendOutcome",
            Self::DecisionNoteDelete => "decisionNote.delete",
            Self::DecisionNoteExportBackup => "decisionNote.exportBackup",
            Self::DecisionNotePreviewRestore => "decisionNote.previewRestore",
            Self::DecisionNoteRestoreBackup => "decisionNote.restoreBackup",
            Self::PrivateRecordRead => "privateRecord.read",
            Self::PrivateRecordChange => "privateRecord.change",
            Self::PrivateRecordExportBackup => "privateRecord.exportBackup",
            Self::WorkspacePreviewLayout => "workspace.previewLayout",
            Self::WorkspaceApplyLayout => "workspace.applyLayout",
            Self::AiTextConnection => "ai.textConnection",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ChartStyle {
    #[serde(rename = "candlestick")]
    Candlestick,
    #[serde(rename = "line")]
    Line,
}
impl ChartStyle {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Candlestick => "candlestick",
            Self::Line => "line",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum TaskStatus {
    #[serde(rename = "queued")]
    Queued,
    #[serde(rename = "running")]
    Running,
    #[serde(rename = "awaiting_confirmation")]
    AwaitingConfirmation,
    #[serde(rename = "completed")]
    Completed,
    #[serde(rename = "failed")]
    Failed,
    #[serde(rename = "cancelled")]
    Cancelled,
}
impl TaskStatus {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Queued => "queued",
            Self::Running => "running",
            Self::AwaitingConfirmation => "awaiting_confirmation",
            Self::Completed => "completed",
            Self::Failed => "failed",
            Self::Cancelled => "cancelled",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum Tradability {
    #[serde(rename = "non_tradable")]
    NonTradable,
    #[serde(rename = "unknown")]
    Unknown,
}
impl Tradability {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::NonTradable => "non_tradable",
            Self::Unknown => "unknown",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DataMode {
    #[serde(rename = "simulated")]
    Simulated,
    #[serde(rename = "historical")]
    Historical,
    #[serde(rename = "delayed")]
    Delayed,
    #[serde(rename = "realtime")]
    Realtime,
}
impl DataMode {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Simulated => "simulated",
            Self::Historical => "historical",
            Self::Delayed => "delayed",
            Self::Realtime => "realtime",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DataQuality {
    #[serde(rename = "fresh")]
    Fresh,
    #[serde(rename = "stale")]
    Stale,
    #[serde(rename = "gapped")]
    Gapped,
    #[serde(rename = "degraded")]
    Degraded,
}
impl DataQuality {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Fresh => "fresh",
            Self::Stale => "stale",
            Self::Gapped => "gapped",
            Self::Degraded => "degraded",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProviderMapping {
    pub provider_id: OpaqueId,
    pub provider_symbol: String,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Instrument {
    pub id: OpaqueId,
    pub display_symbol: String,
    pub name: String,
    pub asset_class: String,
    pub venue: String,
    pub currency: String,
    pub calendar_id: OpaqueId,
    pub timezone: String,
    pub price_tick: Option<String>,
    pub quantity_unit: String,
    pub tradability: Tradability,
    pub metadata: Metadata,
    pub provider_mappings: Vec<ProviderMapping>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DatasetDescriptor {
    pub id: Uuid,
    pub instrument_id: OpaqueId,
    pub provider_id: OpaqueId,
    pub mode: DataMode,
    pub timeframe: String,
    pub price_adjustment: String,
    pub timezone: String,
    pub calendar_id: OpaqueId,
    pub quantity_unit: String,
    pub revision: String,
    pub quality: DataQuality,
    pub provenance: Metadata,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Bar {
    pub time_ms: Timestamp,
    pub trading_day: String,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: Option<f64>,
    pub is_closed: bool,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Error {
    pub code: ErrorCode,
    pub message: String,
    pub retryable: bool,
    pub details: Metadata,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Point {
    pub time_ms: Timestamp,
    pub value: Option<f64>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Style {
    pub color: String,
    pub width: u64,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum MarkerPosition {
    #[serde(rename = "above")]
    Above,
    #[serde(rename = "below")]
    Below,
    #[serde(rename = "on")]
    On,
}
impl MarkerPosition {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Above => "above",
            Self::Below => "below",
            Self::On => "on",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Marker {
    pub time_ms: Timestamp,
    pub position: MarkerPosition,
    pub label: String,
    pub color: String,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Region {
    pub from_ms: Timestamp,
    pub to_ms: Timestamp,
    pub label: String,
    pub color: String,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum Annotation {
    Marker(Box<Marker>),
    Region(Box<Region>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum SeriesKind {
    #[serde(rename = "line")]
    Line,
    #[serde(rename = "histogram")]
    Histogram,
}
impl SeriesKind {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Line => "line",
            Self::Histogram => "histogram",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IndicatorSeries {
    pub id: OpaqueId,
    pub pane: String,
    pub r#type: SeriesKind,
    pub points: Vec<Point>,
    pub style: Style,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum Severity {
    #[serde(rename = "info")]
    Info,
    #[serde(rename = "warning")]
    Warning,
    #[serde(rename = "error")]
    Error,
}
impl Severity {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Info => "info",
            Self::Warning => "warning",
            Self::Error => "error",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Diagnostic {
    pub code: String,
    pub message: String,
    pub severity: Severity,
}
pub type Hash = String;
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IndicatorProvenance {
    pub code_hash: Hash,
    pub params_hash: Hash,
    pub dataset_id: Uuid,
    pub dataset_revision: String,
    pub engine_version: String,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IndicatorResult {
    pub series: Vec<IndicatorSeries>,
    pub markers: Vec<Marker>,
    pub regions: Vec<Region>,
    pub diagnostics: Vec<Diagnostic>,
    pub provenance: IndicatorProvenance,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum LayerState {
    #[serde(rename = "preview")]
    Preview,
    #[serde(rename = "accepted")]
    Accepted,
}
impl LayerState {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Preview => "preview",
            Self::Accepted => "accepted",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Layer {
    pub id: OpaqueId,
    pub state: LayerState,
    pub source_task_id: Option<Uuid>,
    pub indicator_version_id: Option<OpaqueId>,
    pub metadata: Metadata,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct VisibleRange {
    pub from_ms: Timestamp,
    pub to_ms: Timestamp,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Workspace {
    pub id: OpaqueId,
    pub revision: Revision,
    pub instrument_id: Option<OpaqueId>,
    pub timeframe: String,
    pub style: ChartStyle,
    pub dataset: Option<DatasetDescriptor>,
    pub visible_range: Option<VisibleRange>,
    pub layers: Vec<Layer>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Context {
    pub workspace: Workspace,
    pub authorized_operations: Vec<LegacyOperationName>,
    pub bars: Vec<Bar>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FieldExpression {
    pub op: FieldExpressionOp,
    pub field: FieldExpressionField,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ConstantExpression {
    pub op: ConstantExpressionOp,
    pub value: f64,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BinaryExpression {
    pub op: BinaryExpressionOp,
    pub left: Expression,
    pub right: Expression,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AbsExpression {
    pub op: AbsExpressionOp,
    pub value: Expression,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LagExpression {
    pub op: LagExpressionOp,
    pub value: Expression,
    pub periods: u64,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WindowExpression {
    pub op: WindowExpressionOp,
    pub value: Expression,
    pub window: u64,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum Expression {
    FieldExpression(Box<FieldExpression>),
    ConstantExpression(Box<ConstantExpression>),
    BinaryExpression(Box<BinaryExpression>),
    AbsExpression(Box<AbsExpression>),
    LagExpression(Box<LagExpression>),
    WindowExpression(Box<WindowExpression>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DeclarativeDraftInput {
    pub language: DeclarativeDraftInputLanguage,
    pub source: Expression,
    pub params: Metadata,
    pub expected_workspace_revision: Revision,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PythonDraftInput {
    pub language: PythonDraftInputLanguage,
    pub source: String,
    pub params: Metadata,
    pub expected_workspace_revision: Revision,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct InstrumentSearchInput {
    pub query: String,
    pub limit: u64,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct InstrumentSearchOutput {
    pub instruments: Vec<Instrument>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct InstrumentSearchRequest {
    pub protocol_version: ProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: InstrumentSearchRequestType,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub operation: InstrumentSearchRequestOperation,
    pub payload: InstrumentSearchInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct InstrumentSearchToolCall {
    pub operation: InstrumentSearchToolCallOperation,
    pub arguments: InstrumentSearchInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MarketGetBarsInput {
    pub instrument_id: OpaqueId,
    pub timeframe: String,
    pub from_ms: Timestamp,
    pub to_ms: Timestamp,
    pub provider_id: OpaqueId,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MarketGetBarsOutput {
    pub dataset: DatasetDescriptor,
    pub bars: Vec<Bar>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MarketGetBarsRequest {
    pub protocol_version: ProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: MarketGetBarsRequestType,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub operation: MarketGetBarsRequestOperation,
    pub payload: MarketGetBarsInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MarketGetBarsToolCall {
    pub operation: MarketGetBarsToolCallOperation,
    pub arguments: MarketGetBarsInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceOpenInstrumentInput {
    pub instrument_id: OpaqueId,
    pub timeframe: String,
    pub expected_workspace_revision: Revision,
}
pub type WorkspaceOpenInstrumentOutput = Workspace;
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceOpenInstrumentRequest {
    pub protocol_version: ProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: WorkspaceOpenInstrumentRequestType,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub operation: WorkspaceOpenInstrumentRequestOperation,
    pub payload: WorkspaceOpenInstrumentInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceOpenInstrumentToolCall {
    pub operation: WorkspaceOpenInstrumentToolCallOperation,
    pub arguments: WorkspaceOpenInstrumentInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChartSetTimeframeInput {
    pub timeframe: String,
    pub expected_workspace_revision: Revision,
}
pub type ChartSetTimeframeOutput = Workspace;
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChartSetTimeframeRequest {
    pub protocol_version: ProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: ChartSetTimeframeRequestType,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub operation: ChartSetTimeframeRequestOperation,
    pub payload: ChartSetTimeframeInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChartSetTimeframeToolCall {
    pub operation: ChartSetTimeframeToolCallOperation,
    pub arguments: ChartSetTimeframeInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChartSetStyleInput {
    pub style: ChartStyle,
    pub expected_workspace_revision: Revision,
}
pub type ChartSetStyleOutput = Workspace;
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChartSetStyleRequest {
    pub protocol_version: ProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: ChartSetStyleRequestType,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub operation: ChartSetStyleRequestOperation,
    pub payload: ChartSetStyleInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChartSetStyleToolCall {
    pub operation: ChartSetStyleToolCallOperation,
    pub arguments: ChartSetStyleInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChartAddAnnotationInput {
    pub annotation: Annotation,
    pub expected_workspace_revision: Revision,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChartAddAnnotationOutput {
    pub layer_id: OpaqueId,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChartAddAnnotationRequest {
    pub protocol_version: ProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: ChartAddAnnotationRequestType,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub operation: ChartAddAnnotationRequestOperation,
    pub payload: ChartAddAnnotationInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChartAddAnnotationToolCall {
    pub operation: ChartAddAnnotationToolCallOperation,
    pub arguments: ChartAddAnnotationInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChartRemoveLayerInput {
    pub layer_id: OpaqueId,
    pub expected_workspace_revision: Revision,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChartRemoveLayerOutput {
    pub operation_id: OpaqueId,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChartRemoveLayerRequest {
    pub protocol_version: ProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: ChartRemoveLayerRequestType,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub operation: ChartRemoveLayerRequestOperation,
    pub payload: ChartRemoveLayerInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChartRemoveLayerToolCall {
    pub operation: ChartRemoveLayerToolCallOperation,
    pub arguments: ChartRemoveLayerInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum IndicatorCreateDraftInput {
    DeclarativeDraftInput(Box<DeclarativeDraftInput>),
    PythonDraftInput(Box<PythonDraftInput>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IndicatorCreateDraftOutput {
    pub draft_id: OpaqueId,
    pub code_hash: Hash,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IndicatorCreateDraftRequest {
    pub protocol_version: ProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: IndicatorCreateDraftRequestType,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub operation: IndicatorCreateDraftRequestOperation,
    pub payload: IndicatorCreateDraftInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IndicatorCreateDraftToolCall {
    pub operation: IndicatorCreateDraftToolCallOperation,
    pub arguments: IndicatorCreateDraftInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IndicatorRunPreviewInput {
    pub draft_id: OpaqueId,
    pub dataset_id: Uuid,
    pub dataset_revision: String,
    pub params: Metadata,
    pub expected_workspace_revision: Revision,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IndicatorRunPreviewOutput {
    pub task_id: Uuid,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IndicatorRunPreviewRequest {
    pub protocol_version: ProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: IndicatorRunPreviewRequestType,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub operation: IndicatorRunPreviewRequestOperation,
    pub payload: IndicatorRunPreviewInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IndicatorRunPreviewToolCall {
    pub operation: IndicatorRunPreviewToolCallOperation,
    pub arguments: IndicatorRunPreviewInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IndicatorAcceptVersionInput {
    pub preview_id: OpaqueId,
    pub expected_workspace_revision: Revision,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IndicatorAcceptVersionOutput {
    pub version_id: OpaqueId,
    pub layer_id: OpaqueId,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IndicatorAcceptVersionRequest {
    pub protocol_version: ProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: IndicatorAcceptVersionRequestType,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub operation: IndicatorAcceptVersionRequestOperation,
    pub payload: IndicatorAcceptVersionInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IndicatorAcceptVersionToolCall {
    pub operation: IndicatorAcceptVersionToolCallOperation,
    pub arguments: IndicatorAcceptVersionInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IndicatorRemoveInput {
    pub layer_id: OpaqueId,
    pub expected_workspace_revision: Revision,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IndicatorRemoveOutput {
    pub operation_id: OpaqueId,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IndicatorRemoveRequest {
    pub protocol_version: ProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: IndicatorRemoveRequestType,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub operation: IndicatorRemoveRequestOperation,
    pub payload: IndicatorRemoveInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IndicatorRemoveToolCall {
    pub operation: IndicatorRemoveToolCallOperation,
    pub arguments: IndicatorRemoveInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceUndoInput {
    pub operation_id: OpaqueId,
    pub expected_workspace_revision: Revision,
}
pub type WorkspaceUndoOutput = Workspace;
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceUndoRequest {
    pub protocol_version: ProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: WorkspaceUndoRequestType,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub operation: WorkspaceUndoRequestOperation,
    pub payload: WorkspaceUndoInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceUndoToolCall {
    pub operation: WorkspaceUndoToolCallOperation,
    pub arguments: WorkspaceUndoInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TaskCancelInput {
    pub task_id: Uuid,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TaskCancelOutput {
    pub task_id: Uuid,
    pub status: TaskCancelOutputStatus,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TaskCancelRequest {
    pub protocol_version: ProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: TaskCancelRequestType,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub operation: TaskCancelRequestOperation,
    pub payload: TaskCancelInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TaskCancelToolCall {
    pub operation: TaskCancelToolCallOperation,
    pub arguments: TaskCancelInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceGetContextInput {
    pub workspace_id: OpaqueId,
}
pub type WorkspaceGetContextOutput = Context;
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceGetContextRequest {
    pub protocol_version: ProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: WorkspaceGetContextRequestType,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub operation: WorkspaceGetContextRequestOperation,
    pub payload: WorkspaceGetContextInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceGetContextToolCall {
    pub operation: WorkspaceGetContextToolCallOperation,
    pub arguments: WorkspaceGetContextInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum Request {
    InstrumentSearchRequest(Box<InstrumentSearchRequest>),
    MarketGetBarsRequest(Box<MarketGetBarsRequest>),
    WorkspaceOpenInstrumentRequest(Box<WorkspaceOpenInstrumentRequest>),
    ChartSetTimeframeRequest(Box<ChartSetTimeframeRequest>),
    ChartSetStyleRequest(Box<ChartSetStyleRequest>),
    ChartAddAnnotationRequest(Box<ChartAddAnnotationRequest>),
    ChartRemoveLayerRequest(Box<ChartRemoveLayerRequest>),
    IndicatorCreateDraftRequest(Box<IndicatorCreateDraftRequest>),
    IndicatorRunPreviewRequest(Box<IndicatorRunPreviewRequest>),
    IndicatorAcceptVersionRequest(Box<IndicatorAcceptVersionRequest>),
    IndicatorRemoveRequest(Box<IndicatorRemoveRequest>),
    WorkspaceUndoRequest(Box<WorkspaceUndoRequest>),
    TaskCancelRequest(Box<TaskCancelRequest>),
    WorkspaceGetContextRequest(Box<WorkspaceGetContextRequest>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum ToolCall {
    InstrumentSearchToolCall(Box<InstrumentSearchToolCall>),
    MarketGetBarsToolCall(Box<MarketGetBarsToolCall>),
    WorkspaceOpenInstrumentToolCall(Box<WorkspaceOpenInstrumentToolCall>),
    ChartSetTimeframeToolCall(Box<ChartSetTimeframeToolCall>),
    ChartSetStyleToolCall(Box<ChartSetStyleToolCall>),
    ChartAddAnnotationToolCall(Box<ChartAddAnnotationToolCall>),
    ChartRemoveLayerToolCall(Box<ChartRemoveLayerToolCall>),
    IndicatorCreateDraftToolCall(Box<IndicatorCreateDraftToolCall>),
    IndicatorRunPreviewToolCall(Box<IndicatorRunPreviewToolCall>),
    IndicatorAcceptVersionToolCall(Box<IndicatorAcceptVersionToolCall>),
    IndicatorRemoveToolCall(Box<IndicatorRemoveToolCall>),
    WorkspaceUndoToolCall(Box<WorkspaceUndoToolCall>),
    TaskCancelToolCall(Box<TaskCancelToolCall>),
    WorkspaceGetContextToolCall(Box<WorkspaceGetContextToolCall>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Hello {
    pub protocol_version: ProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: HelloType,
    pub role: WorkerRole,
    pub worker_version: String,
    pub supported_protocols: Vec<String>,
    pub capabilities: Vec<LegacyOperationName>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ErrorResponse {
    pub protocol_version: ProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: ErrorResponseType,
    pub request_id: Uuid,
    pub ok: ConstBool<false>,
    pub result: (),
    pub error: Error,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum OperationResult {
    InstrumentSearchOutput(Box<InstrumentSearchOutput>),
    MarketGetBarsOutput(Box<MarketGetBarsOutput>),
    WorkspaceOpenInstrumentOutput(Box<WorkspaceOpenInstrumentOutput>),
    ChartSetTimeframeOutput(Box<ChartSetTimeframeOutput>),
    ChartSetStyleOutput(Box<ChartSetStyleOutput>),
    ChartAddAnnotationOutput(Box<ChartAddAnnotationOutput>),
    ChartRemoveLayerOutput(Box<ChartRemoveLayerOutput>),
    IndicatorCreateDraftOutput(Box<IndicatorCreateDraftOutput>),
    IndicatorRunPreviewOutput(Box<IndicatorRunPreviewOutput>),
    IndicatorAcceptVersionOutput(Box<IndicatorAcceptVersionOutput>),
    IndicatorRemoveOutput(Box<IndicatorRemoveOutput>),
    WorkspaceUndoOutput(Box<WorkspaceUndoOutput>),
    TaskCancelOutput(Box<TaskCancelOutput>),
    WorkspaceGetContextOutput(Box<WorkspaceGetContextOutput>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SuccessResponse {
    pub protocol_version: ProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: SuccessResponseType,
    pub request_id: Uuid,
    pub ok: ConstBool<true>,
    pub result: OperationResult,
    pub error: (),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum Response {
    SuccessResponse(Box<SuccessResponse>),
    ErrorResponse(Box<ErrorResponse>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AiToolRequestEvent {
    pub protocol_version: ProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: AiToolRequestEventType,
    pub stream_id: String,
    pub sequence: u64,
    pub event: AiToolRequestEventEvent,
    pub payload: ToolCall,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Cancel {
    pub protocol_version: ProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: CancelType,
    pub request_id: Uuid,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Heartbeat {
    pub protocol_version: ProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: HeartbeatType,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SeriesKey {
    pub instrument_id: OpaqueId,
    pub provider_id: OpaqueId,
    pub timeframe: OpaqueId,
    pub price_adjustment: OpaqueId,
    pub session_scope: OpaqueId,
    pub calendar_id: OpaqueId,
    pub quantity_unit: OpaqueId,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FrozenSnapshot {
    pub series: SeriesKey,
    pub dataset_id: Uuid,
    pub dataset_revision: OpaqueId,
    pub from_ms: Timestamp,
    pub to_ms: Timestamp,
    pub warmup_bars: u64,
    pub as_of_ms: Timestamp,
    pub content_hash: Hash,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HistoryCursor {
    pub snapshot_hash: Hash,
    pub next_index: u64,
    pub prefix_hash: Hash,
    pub previous_time_ms: Timestamp,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StreamPosition {
    pub stream_id: OpaqueId,
    pub epoch: OpaqueId,
    pub sequence: Revision,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MarketQualityState {
    pub as_of_ms: Timestamp,
    pub last_observed_ms: Option<Timestamp>,
    pub session_state: MarketQualityStateSessionState,
    pub freshness: MarketQualityStateFreshness,
    pub connection: MarketQualityStateConnection,
    pub coverage: MarketQualityStateCoverage,
    pub gaps: Vec<VisibleRange>,
    pub source_kind: MarketQualityStateSourceKind,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MarketHistoryPageInput {
    pub series: SeriesKey,
    pub from_ms: Timestamp,
    pub to_ms: Timestamp,
    pub warmup_bars: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub as_of_ms: Option<Timestamp>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub expected_revision: Option<OpaqueId>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub snapshot: Option<FrozenSnapshot>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cursor: Option<HistoryCursor>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub page_size: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub include_stream_position: Option<bool>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MarketHistoryPageOutput {
    pub dataset: DatasetDescriptor,
    pub bars: Vec<Bar>,
    pub snapshot: FrozenSnapshot,
    pub quality_state: MarketQualityState,
    pub stream_position: Option<StreamPosition>,
    pub next_cursor: Option<HistoryCursor>,
    pub total_bars: u64,
    pub offset: u64,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MarketPageFault {
    pub code: ErrorCode,
    pub message: String,
    pub retryable: bool,
    pub details: Metadata,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MarketHistoryPageRequest {
    pub protocol_version: MarketHistoryPageRequestProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: MarketHistoryPageRequestType,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub operation: MarketHistoryPageRequestOperation,
    pub payload: MarketHistoryPageInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MarketHistoryPageResponse {
    pub protocol_version: MarketHistoryPageResponseProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: MarketHistoryPageResponseType,
    pub request_id: Uuid,
    pub operation: MarketHistoryPageResponseOperation,
    pub ok: ConstBool<true>,
    pub result: MarketHistoryPageOutput,
    pub error: (),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MarketHistoryPageError {
    pub protocol_version: MarketHistoryPageErrorProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: MarketHistoryPageErrorType,
    pub request_id: Uuid,
    pub operation: MarketHistoryPageErrorOperation,
    pub ok: ConstBool<false>,
    pub result: (),
    pub error: MarketPageFault,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MarketSubscriptionInput {
    pub series: SeriesKey,
    pub after: StreamPosition,
    pub snapshot: FrozenSnapshot,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StreamResumeAccepted {
    pub status: StreamResumeAcceptedStatus,
    pub from_position: StreamPosition,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StreamBackfillRequired {
    pub status: StreamBackfillRequiredStatus,
    pub reason: StreamBackfillRequiredReason,
    pub recover_from_ms: Timestamp,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum MarketSubscriptionOutput {
    StreamResumeAccepted(Box<StreamResumeAccepted>),
    StreamBackfillRequired(Box<StreamBackfillRequired>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MarketSubscriptionRequest {
    pub protocol_version: MarketSubscriptionRequestProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: MarketSubscriptionRequestType,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub operation: MarketSubscriptionRequestOperation,
    pub payload: MarketSubscriptionInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MarketSubscriptionResponse {
    pub protocol_version: MarketSubscriptionResponseProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: MarketSubscriptionResponseType,
    pub request_id: Uuid,
    pub operation: MarketSubscriptionResponseOperation,
    pub ok: ConstBool<true>,
    pub result: MarketSubscriptionOutput,
    pub error: (),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MarketSubscriptionError {
    pub protocol_version: MarketSubscriptionErrorProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: MarketSubscriptionErrorType,
    pub request_id: Uuid,
    pub operation: MarketSubscriptionErrorOperation,
    pub ok: ConstBool<false>,
    pub result: (),
    pub error: MarketPageFault,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum MarketSubscriptionReply11 {
    MarketSubscriptionResponse(Box<MarketSubscriptionResponse>),
    MarketSubscriptionError(Box<MarketSubscriptionError>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MarketStreamBarUpdate {
    pub series: SeriesKey,
    pub dataset_revision: OpaqueId,
    pub observed_at_ms: Timestamp,
    pub bar: Bar,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MarketStreamBarCorrection {
    pub series: SeriesKey,
    pub dataset_revision: OpaqueId,
    pub observed_at_ms: Timestamp,
    pub bar: Bar,
    pub supersedes_revision: OpaqueId,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MarketStreamGap {
    pub series: SeriesKey,
    pub reason: MarketStreamGapReason,
    pub expected_sequence: Revision,
    pub lost_count: u64,
    pub affected: VisibleRange,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MarketBarUpdateEvent {
    pub protocol_version: MarketBarUpdateEventProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: MarketBarUpdateEventType,
    pub stream_id: OpaqueId,
    pub epoch: OpaqueId,
    pub sequence: Revision,
    pub event: MarketBarUpdateEventEvent,
    pub payload: MarketStreamBarUpdate,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MarketBarCorrectionEvent {
    pub protocol_version: MarketBarCorrectionEventProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: MarketBarCorrectionEventType,
    pub stream_id: OpaqueId,
    pub epoch: OpaqueId,
    pub sequence: Revision,
    pub event: MarketBarCorrectionEventEvent,
    pub payload: MarketStreamBarCorrection,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MarketStreamGapEvent {
    pub protocol_version: MarketStreamGapEventProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: MarketStreamGapEventType,
    pub stream_id: OpaqueId,
    pub epoch: OpaqueId,
    pub sequence: Revision,
    pub event: MarketStreamGapEventEvent,
    pub payload: MarketStreamGap,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LocalImportPreviewRequest {
    pub protocol_version: LocalImportPreviewRequestProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: LocalImportPreviewRequestType,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub operation: LocalImportPreviewRequestOperation,
    pub payload: LocalImportPreviewInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LocalImportPreviewResponse {
    pub protocol_version: LocalImportPreviewResponseProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: LocalImportPreviewResponseType,
    pub request_id: Uuid,
    pub operation: LocalImportPreviewResponseOperation,
    pub ok: ConstBool<true>,
    pub result: LocalImportPreviewOutput,
    pub error: (),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LocalImportPreviewError {
    pub protocol_version: LocalImportPreviewErrorProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: LocalImportPreviewErrorType,
    pub request_id: Uuid,
    pub operation: LocalImportPreviewErrorOperation,
    pub ok: ConstBool<false>,
    pub result: (),
    pub error: MarketPageFault,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SaveLocalImportMappingRequest {
    pub protocol_version: SaveLocalImportMappingRequestProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: SaveLocalImportMappingRequestType,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub operation: SaveLocalImportMappingRequestOperation,
    pub payload: SaveLocalImportMappingInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SaveLocalImportMappingResponse {
    pub protocol_version: SaveLocalImportMappingResponseProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: SaveLocalImportMappingResponseType,
    pub request_id: Uuid,
    pub operation: SaveLocalImportMappingResponseOperation,
    pub ok: ConstBool<true>,
    pub result: SaveLocalImportMappingOutput,
    pub error: (),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SaveLocalImportMappingError {
    pub protocol_version: SaveLocalImportMappingErrorProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: SaveLocalImportMappingErrorType,
    pub request_id: Uuid,
    pub operation: SaveLocalImportMappingErrorOperation,
    pub ok: ConstBool<false>,
    pub result: (),
    pub error: MarketPageFault,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LoadLocalImportMappingRequest {
    pub protocol_version: LoadLocalImportMappingRequestProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: LoadLocalImportMappingRequestType,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub operation: LoadLocalImportMappingRequestOperation,
    pub payload: LoadLocalImportMappingInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LoadLocalImportMappingResponse {
    pub protocol_version: LoadLocalImportMappingResponseProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: LoadLocalImportMappingResponseType,
    pub request_id: Uuid,
    pub operation: LoadLocalImportMappingResponseOperation,
    pub ok: ConstBool<true>,
    pub result: LoadLocalImportMappingOutput,
    pub error: (),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LoadLocalImportMappingError {
    pub protocol_version: LoadLocalImportMappingErrorProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: LoadLocalImportMappingErrorType,
    pub request_id: Uuid,
    pub operation: LoadLocalImportMappingErrorOperation,
    pub ok: ConstBool<false>,
    pub result: (),
    pub error: MarketPageFault,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ListLocalImportRevisionsRequest {
    pub protocol_version: ListLocalImportRevisionsRequestProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: ListLocalImportRevisionsRequestType,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub operation: ListLocalImportRevisionsRequestOperation,
    pub payload: ListLocalImportRevisionsInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ListLocalImportRevisionsResponse {
    pub protocol_version: ListLocalImportRevisionsResponseProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: ListLocalImportRevisionsResponseType,
    pub request_id: Uuid,
    pub operation: ListLocalImportRevisionsResponseOperation,
    pub ok: ConstBool<true>,
    pub result: ListLocalImportRevisionsOutput,
    pub error: (),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ListLocalImportRevisionsError {
    pub protocol_version: ListLocalImportRevisionsErrorProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: ListLocalImportRevisionsErrorType,
    pub request_id: Uuid,
    pub operation: ListLocalImportRevisionsErrorOperation,
    pub ok: ConstBool<false>,
    pub result: (),
    pub error: MarketPageFault,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReadLocalImportRevisionRequest {
    pub protocol_version: ReadLocalImportRevisionRequestProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: ReadLocalImportRevisionRequestType,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub operation: ReadLocalImportRevisionRequestOperation,
    pub payload: ReadLocalImportRevisionInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReadLocalImportRevisionResponse {
    pub protocol_version: ReadLocalImportRevisionResponseProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: ReadLocalImportRevisionResponseType,
    pub request_id: Uuid,
    pub operation: ReadLocalImportRevisionResponseOperation,
    pub ok: ConstBool<true>,
    pub result: ReadLocalImportRevisionOutput,
    pub error: (),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReadLocalImportRevisionError {
    pub protocol_version: ReadLocalImportRevisionErrorProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: ReadLocalImportRevisionErrorType,
    pub request_id: Uuid,
    pub operation: ReadLocalImportRevisionErrorOperation,
    pub ok: ConstBool<false>,
    pub result: (),
    pub error: MarketPageFault,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReadLocalImportSourceRowRequest {
    pub protocol_version: ReadLocalImportSourceRowRequestProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: ReadLocalImportSourceRowRequestType,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub operation: ReadLocalImportSourceRowRequestOperation,
    pub payload: ReadLocalImportSourceRowInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReadLocalImportSourceRowResponse {
    pub protocol_version: ReadLocalImportSourceRowResponseProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: ReadLocalImportSourceRowResponseType,
    pub request_id: Uuid,
    pub operation: ReadLocalImportSourceRowResponseOperation,
    pub ok: ConstBool<true>,
    pub result: ReadLocalImportSourceRowOutput,
    pub error: (),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReadLocalImportSourceRowError {
    pub protocol_version: ReadLocalImportSourceRowErrorProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: ReadLocalImportSourceRowErrorType,
    pub request_id: Uuid,
    pub operation: ReadLocalImportSourceRowErrorOperation,
    pub ok: ConstBool<false>,
    pub result: (),
    pub error: MarketPageFault,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PreviewLocalFolderRefreshRequest {
    pub protocol_version: PreviewLocalFolderRefreshRequestProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: PreviewLocalFolderRefreshRequestType,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub operation: PreviewLocalFolderRefreshRequestOperation,
    pub payload: PreviewLocalFolderRefreshInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PreviewLocalFolderRefreshResponse {
    pub protocol_version: PreviewLocalFolderRefreshResponseProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: PreviewLocalFolderRefreshResponseType,
    pub request_id: Uuid,
    pub operation: PreviewLocalFolderRefreshResponseOperation,
    pub ok: ConstBool<true>,
    pub result: PreviewLocalFolderRefreshOutput,
    pub error: (),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PreviewLocalFolderRefreshError {
    pub protocol_version: PreviewLocalFolderRefreshErrorProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: PreviewLocalFolderRefreshErrorType,
    pub request_id: Uuid,
    pub operation: PreviewLocalFolderRefreshErrorOperation,
    pub ok: ConstBool<false>,
    pub result: (),
    pub error: MarketPageFault,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrepareLocalFolderPublicationRequest {
    pub protocol_version: PrepareLocalFolderPublicationRequestProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: PrepareLocalFolderPublicationRequestType,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub operation: PrepareLocalFolderPublicationRequestOperation,
    pub payload: PrepareLocalFolderPublicationInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrepareLocalFolderPublicationResponse {
    pub protocol_version: PrepareLocalFolderPublicationResponseProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: PrepareLocalFolderPublicationResponseType,
    pub request_id: Uuid,
    pub operation: PrepareLocalFolderPublicationResponseOperation,
    pub ok: ConstBool<true>,
    pub result: PrepareLocalFolderPublicationOutput,
    pub error: (),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrepareLocalFolderPublicationError {
    pub protocol_version: PrepareLocalFolderPublicationErrorProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: PrepareLocalFolderPublicationErrorType,
    pub request_id: Uuid,
    pub operation: PrepareLocalFolderPublicationErrorOperation,
    pub ok: ConstBool<false>,
    pub result: (),
    pub error: MarketPageFault,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PublishLocalFolderRefreshRequest {
    pub protocol_version: PublishLocalFolderRefreshRequestProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: PublishLocalFolderRefreshRequestType,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub operation: PublishLocalFolderRefreshRequestOperation,
    pub payload: PublishLocalFolderRefreshInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PublishLocalFolderRefreshResponse {
    pub protocol_version: PublishLocalFolderRefreshResponseProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: PublishLocalFolderRefreshResponseType,
    pub request_id: Uuid,
    pub operation: PublishLocalFolderRefreshResponseOperation,
    pub ok: ConstBool<true>,
    pub result: PublishLocalFolderRefreshOutput,
    pub error: (),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PublishLocalFolderRefreshError {
    pub protocol_version: PublishLocalFolderRefreshErrorProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: PublishLocalFolderRefreshErrorType,
    pub request_id: Uuid,
    pub operation: PublishLocalFolderRefreshErrorOperation,
    pub ok: ConstBool<false>,
    pub result: (),
    pub error: MarketPageFault,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ListLocalFolderRevisionsRequest {
    pub protocol_version: ListLocalFolderRevisionsRequestProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: ListLocalFolderRevisionsRequestType,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub operation: ListLocalFolderRevisionsRequestOperation,
    pub payload: ListLocalFolderRevisionsInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ListLocalFolderRevisionsResponse {
    pub protocol_version: ListLocalFolderRevisionsResponseProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: ListLocalFolderRevisionsResponseType,
    pub request_id: Uuid,
    pub operation: ListLocalFolderRevisionsResponseOperation,
    pub ok: ConstBool<true>,
    pub result: ListLocalFolderRevisionsOutput,
    pub error: (),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ListLocalFolderRevisionsError {
    pub protocol_version: ListLocalFolderRevisionsErrorProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: ListLocalFolderRevisionsErrorType,
    pub request_id: Uuid,
    pub operation: ListLocalFolderRevisionsErrorOperation,
    pub ok: ConstBool<false>,
    pub result: (),
    pub error: MarketPageFault,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReadLocalFolderRevisionRequest {
    pub protocol_version: ReadLocalFolderRevisionRequestProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: ReadLocalFolderRevisionRequestType,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub operation: ReadLocalFolderRevisionRequestOperation,
    pub payload: ReadLocalFolderRevisionInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReadLocalFolderRevisionResponse {
    pub protocol_version: ReadLocalFolderRevisionResponseProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: ReadLocalFolderRevisionResponseType,
    pub request_id: Uuid,
    pub operation: ReadLocalFolderRevisionResponseOperation,
    pub ok: ConstBool<true>,
    pub result: ReadLocalFolderRevisionOutput,
    pub error: (),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReadLocalFolderRevisionError {
    pub protocol_version: ReadLocalFolderRevisionErrorProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: ReadLocalFolderRevisionErrorType,
    pub request_id: Uuid,
    pub operation: ReadLocalFolderRevisionErrorOperation,
    pub ok: ConstBool<false>,
    pub result: (),
    pub error: MarketPageFault,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReadLocalFolderSourceRowRequest {
    pub protocol_version: ReadLocalFolderSourceRowRequestProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: ReadLocalFolderSourceRowRequestType,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub operation: ReadLocalFolderSourceRowRequestOperation,
    pub payload: ReadLocalFolderSourceRowInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReadLocalFolderSourceRowResponse {
    pub protocol_version: ReadLocalFolderSourceRowResponseProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: ReadLocalFolderSourceRowResponseType,
    pub request_id: Uuid,
    pub operation: ReadLocalFolderSourceRowResponseOperation,
    pub ok: ConstBool<true>,
    pub result: ReadLocalFolderSourceRowOutput,
    pub error: (),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReadLocalFolderSourceRowError {
    pub protocol_version: ReadLocalFolderSourceRowErrorProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: ReadLocalFolderSourceRowErrorType,
    pub request_id: Uuid,
    pub operation: ReadLocalFolderSourceRowErrorOperation,
    pub ok: ConstBool<false>,
    pub result: (),
    pub error: MarketPageFault,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RevokeLocalFolderRevisionRequest {
    pub protocol_version: RevokeLocalFolderRevisionRequestProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: RevokeLocalFolderRevisionRequestType,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub operation: RevokeLocalFolderRevisionRequestOperation,
    pub payload: RevokeLocalFolderRevisionInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RevokeLocalFolderRevisionResponse {
    pub protocol_version: RevokeLocalFolderRevisionResponseProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: RevokeLocalFolderRevisionResponseType,
    pub request_id: Uuid,
    pub operation: RevokeLocalFolderRevisionResponseOperation,
    pub ok: ConstBool<true>,
    pub result: RevokeLocalFolderRevisionOutput,
    pub error: (),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RevokeLocalFolderRevisionError {
    pub protocol_version: RevokeLocalFolderRevisionErrorProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: RevokeLocalFolderRevisionErrorType,
    pub request_id: Uuid,
    pub operation: RevokeLocalFolderRevisionErrorOperation,
    pub ok: ConstBool<false>,
    pub result: (),
    pub error: MarketPageFault,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RevokeLocalImportRevisionRequest {
    pub protocol_version: RevokeLocalImportRevisionRequestProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: RevokeLocalImportRevisionRequestType,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub operation: RevokeLocalImportRevisionRequestOperation,
    pub payload: RevokeLocalImportRevisionInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RevokeLocalImportRevisionResponse {
    pub protocol_version: RevokeLocalImportRevisionResponseProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: RevokeLocalImportRevisionResponseType,
    pub request_id: Uuid,
    pub operation: RevokeLocalImportRevisionResponseOperation,
    pub ok: ConstBool<true>,
    pub result: RevokeLocalImportRevisionOutput,
    pub error: (),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RevokeLocalImportRevisionError {
    pub protocol_version: RevokeLocalImportRevisionErrorProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: RevokeLocalImportRevisionErrorType,
    pub request_id: Uuid,
    pub operation: RevokeLocalImportRevisionErrorOperation,
    pub ok: ConstBool<false>,
    pub result: (),
    pub error: MarketPageFault,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PublishLocalImportRequest {
    pub protocol_version: PublishLocalImportRequestProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: PublishLocalImportRequestType,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub operation: PublishLocalImportRequestOperation,
    pub payload: PublishLocalImportInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PublishLocalImportResponse {
    pub protocol_version: PublishLocalImportResponseProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: PublishLocalImportResponseType,
    pub request_id: Uuid,
    pub operation: PublishLocalImportResponseOperation,
    pub ok: ConstBool<true>,
    pub result: PublishLocalImportOutput,
    pub error: (),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PublishLocalImportError {
    pub protocol_version: PublishLocalImportErrorProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: PublishLocalImportErrorType,
    pub request_id: Uuid,
    pub operation: PublishLocalImportErrorOperation,
    pub ok: ConstBool<false>,
    pub result: (),
    pub error: MarketPageFault,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkerHello11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: WorkerHello11Type,
    pub role: WorkerRole,
    pub worker_version: String,
    pub supported_protocols: Vec<String>,
    pub capabilities: Vec<String>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ScopedMutationRequest11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: ScopedMutationRequest11Type,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub scope: MutationScope11,
    pub intent: MutationIntent11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ScopedMutationResponse11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: ScopedMutationResponse11Type,
    pub request_id: Uuid,
    pub outcome: MutationOutcome11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TrustedConfirmationDecision11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: TrustedConfirmationDecision11Type,
    pub request_id: Uuid,
    pub operation: TrustedConfirmationDecision11Operation,
    pub challenge_id: Uuid,
    pub decision: TrustedConfirmationDecision11Decision,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PreviewReady11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: PreviewReady11Type,
    pub event: PreviewReady11Event,
    pub task_id: Uuid,
    pub draft_id: OpaqueId,
    pub preview_id: OpaqueId,
    pub stream_id: OpaqueId,
    pub sequence: Revision,
    pub snapshot: FrozenSnapshot,
    pub code_hash: Hash,
    pub params_hash: Hash,
    pub engine_version: OpaqueId,
    pub artifact: IndicatorArtifact11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ArtifactChunk11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: ArtifactChunk11Type,
    pub event: ArtifactChunk11Event,
    pub task_id: Uuid,
    pub artifact_id: OpaqueId,
    pub result_hash: Hash,
    pub stream_id: OpaqueId,
    pub sequence: Revision,
    pub segment_index: u64,
    pub bytes_base64: String,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TaskTerminalEvent11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: TaskTerminalEvent11Type,
    pub event: TaskTerminalEvent11Event,
    pub task_id: Uuid,
    pub stream_id: OpaqueId,
    pub sequence: Revision,
    pub outcome: TerminalState11,
    pub committed_effects: Vec<TaskEffectSummary11>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TaskCancelRequest11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: TaskCancelRequest11Type,
    pub operation: TaskCancelRequest11Operation,
    pub request_id: Uuid,
    pub task_id: Uuid,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CancelMemberRequest11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: CancelMemberRequest11Type,
    pub request_id: Uuid,
    pub task_id: Uuid,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReadProjectedChart11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: ReadProjectedChart11Type,
    pub operation: ReadProjectedChart11Operation,
    pub request_id: Uuid,
    pub workspace_id: OpaqueId,
    pub selection_revision: Revision,
    pub requested_fields: Vec<ExportField11>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProjectedChartResponse11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: ProjectedChartResponse11Type,
    pub operation: ProjectedChartResponse11Operation,
    pub request_id: Uuid,
    pub result: ProjectedChart11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SharedSelectionEvent11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: SharedSelectionEvent11Type,
    pub event: SharedSelectionEvent11Event,
    pub stream_id: OpaqueId,
    pub sequence: Revision,
    pub selection: SharedSelection11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SharedSelectionRequest11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: SharedSelectionRequest11Type,
    pub operation: SharedSelectionRequest11Operation,
    pub request_id: Uuid,
    pub workspace_id: OpaqueId,
    pub expected_selection_revision: Revision,
    pub target: Option<EvidenceTarget11>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AiToolRequest11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: AiToolRequest11Type,
    pub event: AiToolRequest11Event,
    pub stream_id: OpaqueId,
    pub sequence: Revision,
    pub task_id: Uuid,
    pub call_id: Uuid,
    pub payload: AiRequestedAction11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AiToolResultDelivery11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: AiToolResultDelivery11Type,
    pub operation: AiToolResultDelivery11Operation,
    pub request_id: Uuid,
    pub task_id: Uuid,
    pub call_id: Uuid,
    pub outcome: AiToolOutcome11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AiToolResultAck11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: AiToolResultAck11Type,
    pub operation: AiToolResultAck11Operation,
    pub request_id: Uuid,
    pub task_id: Uuid,
    pub call_id: Uuid,
    pub status: AiToolResultAck11Status,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProjectedChartError11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: ProjectedChartError11Type,
    pub operation: ProjectedChartError11Operation,
    pub request_id: Uuid,
    pub error: WorkspaceFault11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IndicatorCompute11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: IndicatorCompute11Type,
    pub operation: IndicatorCompute11Operation,
    pub request_id: Uuid,
    pub task_id: Uuid,
    pub draft_id: OpaqueId,
    pub source: DraftSource11,
    pub resolved_params: Metadata,
    pub code_hash: Hash,
    pub params_hash: Hash,
    pub snapshot: FrozenSnapshot,
    pub input: IndicatorBarsInput11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IndicatorComputed11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: IndicatorComputed11Type,
    pub operation: IndicatorComputed11Operation,
    pub request_id: Uuid,
    pub task_id: Uuid,
    pub draft_id: OpaqueId,
    pub snapshot_hash: Hash,
    pub artifact: IndicatorArtifact11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IndicatorInputSegment11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: IndicatorInputSegment11Type,
    pub event: IndicatorInputSegment11Event,
    pub stream_id: OpaqueId,
    pub sequence: Revision,
    pub task_id: Uuid,
    pub request_id: Uuid,
    pub snapshot_hash: Hash,
    pub offset: Revision,
    pub bars: Vec<Bar>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SharedSelectionResponse11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: SharedSelectionResponse11Type,
    pub operation: SharedSelectionResponse11Operation,
    pub request_id: Uuid,
    pub selection: SharedSelection11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SharedSelectionError11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: SharedSelectionError11Type,
    pub operation: SharedSelectionError11Operation,
    pub request_id: Uuid,
    pub error: WorkspaceFault11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SharedContextRequest11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: SharedContextRequest11Type,
    pub operation: SharedContextRequest11Operation,
    pub request_id: Uuid,
    pub workspace_id: OpaqueId,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SharedContextResponse11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: SharedContextResponse11Type,
    pub operation: SharedContextResponse11Operation,
    pub request_id: Uuid,
    pub context: SharedContext11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SharedContextError11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: SharedContextError11Type,
    pub operation: SharedContextError11Operation,
    pub request_id: Uuid,
    pub error: WorkspaceFault11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CancelTaskResponse11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: CancelTaskResponse11Type,
    pub operation: CancelTaskResponse11Operation,
    pub request_id: Uuid,
    pub task_id: Uuid,
    pub outcome: TerminalState11,
    pub committed_effects: Vec<TaskEffectSummary11>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CancelTaskError11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: CancelTaskError11Type,
    pub operation: CancelTaskError11Operation,
    pub request_id: Uuid,
    pub error: WorkspaceFault11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum Message {
    Hello(Box<Hello>),
    Request(Box<Request>),
    Response(Box<Response>),
    AiToolRequestEvent(Box<AiToolRequestEvent>),
    Cancel(Box<Cancel>),
    Heartbeat(Box<Heartbeat>),
    MarketHistoryPageRequest(Box<MarketHistoryPageRequest>),
    MarketHistoryPageResponse(Box<MarketHistoryPageResponse>),
    MarketHistoryPageError(Box<MarketHistoryPageError>),
    MarketSubscriptionRequest(Box<MarketSubscriptionRequest>),
    MarketSubscriptionResponse(Box<MarketSubscriptionResponse>),
    MarketSubscriptionError(Box<MarketSubscriptionError>),
    LocalImportPreviewRequest(Box<LocalImportPreviewRequest>),
    LocalImportPreviewResponse(Box<LocalImportPreviewResponse>),
    LocalImportPreviewError(Box<LocalImportPreviewError>),
    SaveLocalImportMappingRequest(Box<SaveLocalImportMappingRequest>),
    SaveLocalImportMappingResponse(Box<SaveLocalImportMappingResponse>),
    SaveLocalImportMappingError(Box<SaveLocalImportMappingError>),
    LoadLocalImportMappingRequest(Box<LoadLocalImportMappingRequest>),
    LoadLocalImportMappingResponse(Box<LoadLocalImportMappingResponse>),
    LoadLocalImportMappingError(Box<LoadLocalImportMappingError>),
    ListLocalImportRevisionsRequest(Box<ListLocalImportRevisionsRequest>),
    ListLocalImportRevisionsResponse(Box<ListLocalImportRevisionsResponse>),
    ListLocalImportRevisionsError(Box<ListLocalImportRevisionsError>),
    ReadLocalImportRevisionRequest(Box<ReadLocalImportRevisionRequest>),
    ReadLocalImportRevisionResponse(Box<ReadLocalImportRevisionResponse>),
    ReadLocalImportRevisionError(Box<ReadLocalImportRevisionError>),
    ReadLocalImportSourceRowRequest(Box<ReadLocalImportSourceRowRequest>),
    ReadLocalImportSourceRowResponse(Box<ReadLocalImportSourceRowResponse>),
    ReadLocalImportSourceRowError(Box<ReadLocalImportSourceRowError>),
    PreviewLocalFolderRefreshRequest(Box<PreviewLocalFolderRefreshRequest>),
    PreviewLocalFolderRefreshResponse(Box<PreviewLocalFolderRefreshResponse>),
    PreviewLocalFolderRefreshError(Box<PreviewLocalFolderRefreshError>),
    PrepareLocalFolderPublicationRequest(Box<PrepareLocalFolderPublicationRequest>),
    PrepareLocalFolderPublicationResponse(Box<PrepareLocalFolderPublicationResponse>),
    PrepareLocalFolderPublicationError(Box<PrepareLocalFolderPublicationError>),
    PublishLocalFolderRefreshRequest(Box<PublishLocalFolderRefreshRequest>),
    PublishLocalFolderRefreshResponse(Box<PublishLocalFolderRefreshResponse>),
    PublishLocalFolderRefreshError(Box<PublishLocalFolderRefreshError>),
    ListLocalFolderRevisionsRequest(Box<ListLocalFolderRevisionsRequest>),
    ListLocalFolderRevisionsResponse(Box<ListLocalFolderRevisionsResponse>),
    ListLocalFolderRevisionsError(Box<ListLocalFolderRevisionsError>),
    ReadLocalFolderRevisionRequest(Box<ReadLocalFolderRevisionRequest>),
    ReadLocalFolderRevisionResponse(Box<ReadLocalFolderRevisionResponse>),
    ReadLocalFolderRevisionError(Box<ReadLocalFolderRevisionError>),
    ReadLocalFolderSourceRowRequest(Box<ReadLocalFolderSourceRowRequest>),
    ReadLocalFolderSourceRowResponse(Box<ReadLocalFolderSourceRowResponse>),
    ReadLocalFolderSourceRowError(Box<ReadLocalFolderSourceRowError>),
    RevokeLocalFolderRevisionRequest(Box<RevokeLocalFolderRevisionRequest>),
    RevokeLocalFolderRevisionResponse(Box<RevokeLocalFolderRevisionResponse>),
    RevokeLocalFolderRevisionError(Box<RevokeLocalFolderRevisionError>),
    RevokeLocalImportRevisionRequest(Box<RevokeLocalImportRevisionRequest>),
    RevokeLocalImportRevisionResponse(Box<RevokeLocalImportRevisionResponse>),
    RevokeLocalImportRevisionError(Box<RevokeLocalImportRevisionError>),
    PublishLocalImportRequest(Box<PublishLocalImportRequest>),
    PublishLocalImportResponse(Box<PublishLocalImportResponse>),
    PublishLocalImportError(Box<PublishLocalImportError>),
    MarketBarUpdateEvent(Box<MarketBarUpdateEvent>),
    MarketBarCorrectionEvent(Box<MarketBarCorrectionEvent>),
    MarketStreamGapEvent(Box<MarketStreamGapEvent>),
    WorkerHello11(Box<WorkerHello11>),
    ScopedMutationRequest11(Box<ScopedMutationRequest11>),
    ScopedMutationResponse11(Box<ScopedMutationResponse11>),
    TrustedConfirmationDecision11(Box<TrustedConfirmationDecision11>),
    PreviewReady11(Box<PreviewReady11>),
    ArtifactChunk11(Box<ArtifactChunk11>),
    TaskTerminalEvent11(Box<TaskTerminalEvent11>),
    TaskCancelRequest11(Box<TaskCancelRequest11>),
    CancelMemberRequest11(Box<CancelMemberRequest11>),
    ReadProjectedChart11(Box<ReadProjectedChart11>),
    ProjectedChartResponse11(Box<ProjectedChartResponse11>),
    SharedSelectionEvent11(Box<SharedSelectionEvent11>),
    SharedSelectionRequest11(Box<SharedSelectionRequest11>),
    AiToolRequest11(Box<AiToolRequest11>),
    AiToolResultDelivery11(Box<AiToolResultDelivery11>),
    AiToolResultAck11(Box<AiToolResultAck11>),
    ProjectedChartError11(Box<ProjectedChartError11>),
    IndicatorCompute11(Box<IndicatorCompute11>),
    IndicatorComputed11(Box<IndicatorComputed11>),
    IndicatorInputSegment11(Box<IndicatorInputSegment11>),
    SharedSelectionResponse11(Box<SharedSelectionResponse11>),
    SharedSelectionError11(Box<SharedSelectionError11>),
    SharedContextRequest11(Box<SharedContextRequest11>),
    SharedContextResponse11(Box<SharedContextResponse11>),
    SharedContextError11(Box<SharedContextError11>),
    CancelTaskResponse11(Box<CancelTaskResponse11>),
    CancelTaskError11(Box<CancelTaskError11>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ProtocolVersion11 {
    #[serde(rename = "1.1")]
    Value,
}
impl ProtocolVersion11 {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceObject11 {
    pub kind: WorkspaceObject11Kind,
    pub object_id: OpaqueId,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChartInterval11 {
    pub kind: ChartInterval11Kind,
    pub snapshot: FrozenSnapshot,
    pub range: VisibleRange,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SourceLocation11 {
    pub kind: SourceLocation11Kind,
    pub source_id: OpaqueId,
    pub source_revision: OpaqueId,
    pub locator: OpaqueId,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum EvidenceTarget11 {
    WorkspaceObject11(Box<WorkspaceObject11>),
    ChartInterval11(Box<ChartInterval11>),
    SourceLocation11(Box<SourceLocation11>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SharedSelection11 {
    pub workspace_id: OpaqueId,
    pub selection_revision: Revision,
    pub target: Option<EvidenceTarget11>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MutationScope11 {
    pub workspace_id: OpaqueId,
    pub target: EvidenceTarget11,
    pub expected_workspace_revision: Revision,
    pub expected_selection_revision: Revision,
    pub idempotency_key: Uuid,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OperationReceipt11 {
    pub operation_id: OpaqueId,
    pub workspace_id: OpaqueId,
    pub target: EvidenceTarget11,
    pub committed_workspace_revision: Revision,
    pub undoable: bool,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NumberParameter11 {
    pub r#type: NumberParameter11Type,
    pub name: String,
    pub default: f64,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IntegerParameter11 {
    pub r#type: IntegerParameter11Type,
    pub name: String,
    pub default: i64,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BooleanParameter11 {
    pub r#type: BooleanParameter11Type,
    pub name: String,
    pub default: bool,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum ParameterDeclaration11 {
    NumberParameter11(Box<NumberParameter11>),
    IntegerParameter11(Box<IntegerParameter11>),
    BooleanParameter11(Box<BooleanParameter11>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NumericOverride11 {
    pub name: String,
    pub value: f64,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BooleanOverride11 {
    pub name: String,
    pub value: bool,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum ParameterOverride11 {
    NumericOverride11(Box<NumericOverride11>),
    BooleanOverride11(Box<BooleanOverride11>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NumberReference11 {
    pub op: NumberReference11Op,
    pub name: String,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BooleanReference11 {
    pub op: BooleanReference11Op,
    pub name: String,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WindowLiteral11 {
    pub periods: u64,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WindowParameter11 {
    pub parameter: String,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum WindowLength11 {
    WindowLiteral11(Box<WindowLiteral11>),
    WindowParameter11(Box<WindowParameter11>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Arithmetic11 {
    pub op: Arithmetic11Op,
    pub left: NumberExpression11,
    pub right: NumberExpression11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Absolute11 {
    pub op: Absolute11Op,
    pub value: NumberExpression11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Delay11 {
    pub op: Delay11Op,
    pub value: NumberExpression11,
    pub periods: u64,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Rolling11 {
    pub op: Rolling11Op,
    pub value: NumberExpression11,
    pub window: WindowLength11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum NumberExpression11 {
    FieldExpression(Box<FieldExpression>),
    ConstantExpression(Box<ConstantExpression>),
    NumberReference11(Box<NumberReference11>),
    Arithmetic11(Box<Arithmetic11>),
    Absolute11(Box<Absolute11>),
    Delay11(Box<Delay11>),
    Rolling11(Box<Rolling11>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BooleanConstant11 {
    pub op: BooleanConstant11Op,
    pub value: bool,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Comparison11 {
    pub op: Comparison11Op,
    pub left: NumberExpression11,
    pub right: NumberExpression11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BooleanBinary11 {
    pub op: BooleanBinary11Op,
    pub left: BooleanExpression11,
    pub right: BooleanExpression11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum BooleanExpression11 {
    BooleanConstant11(Box<BooleanConstant11>),
    BooleanReference11(Box<BooleanReference11>),
    Comparison11(Box<Comparison11>),
    BooleanBinary11(Box<BooleanBinary11>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NumericSeries11 {
    pub kind: NumericSeries11Kind,
    pub id: OpaqueId,
    pub pane: OpaqueId,
    pub value: NumberExpression11,
    pub style: Style,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ConditionalMarker11 {
    pub kind: ConditionalMarker11Kind,
    pub when: BooleanExpression11,
    pub position: MarkerPosition,
    pub label: String,
    pub color: String,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum IndicatorOutputSpec11 {
    NumericSeries11(Box<NumericSeries11>),
    ConditionalMarker11(Box<ConditionalMarker11>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DeclarativeSource11 {
    pub parameters: Vec<ParameterDeclaration11>,
    pub outputs: Vec<IndicatorOutputSpec11>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DeclarativeDraft11 {
    pub language: DeclarativeDraft11Language,
    pub source: DeclarativeSource11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PythonDraft11 {
    pub language: PythonDraft11Language,
    pub source: String,
    pub params: Metadata,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum DraftSource11 {
    DeclarativeDraft11(Box<DeclarativeDraft11>),
    PythonDraft11(Box<PythonDraft11>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SnapshotPreview11 {
    pub draft_id: OpaqueId,
    pub snapshot: FrozenSnapshot,
    pub parameters: Vec<ParameterOverride11>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PythonSnapshotPreview11 {
    pub draft_id: OpaqueId,
    pub snapshot: FrozenSnapshot,
    pub python_params: Metadata,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum PreviewArguments11 {
    SnapshotPreview11(Box<SnapshotPreview11>),
    PythonSnapshotPreview11(Box<PythonSnapshotPreview11>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AcceptPreview11 {
    pub preview_id: OpaqueId,
    pub result_hash: Hash,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OpenSeries11 {
    pub series: SeriesKey,
    pub dataset_revision: Option<OpaqueId>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceOpenInstrumentIntent11 {
    pub operation: WorkspaceOpenInstrumentIntent11Operation,
    pub arguments: OpenSeries11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChartSetTimeframeArguments11 {
    pub timeframe: String,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChartSetTimeframeIntent11 {
    pub operation: ChartSetTimeframeIntent11Operation,
    pub arguments: ChartSetTimeframeArguments11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChartSetStyleArguments11 {
    pub style: ChartStyle,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChartSetStyleIntent11 {
    pub operation: ChartSetStyleIntent11Operation,
    pub arguments: ChartSetStyleArguments11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChartAddAnnotationArguments11 {
    pub annotation: Annotation,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChartAddAnnotationIntent11 {
    pub operation: ChartAddAnnotationIntent11Operation,
    pub arguments: ChartAddAnnotationArguments11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChartRemoveLayerArguments11 {
    pub layer_id: OpaqueId,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChartRemoveLayerIntent11 {
    pub operation: ChartRemoveLayerIntent11Operation,
    pub arguments: ChartRemoveLayerArguments11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IndicatorCreateDraftIntent11 {
    pub operation: IndicatorCreateDraftIntent11Operation,
    pub arguments: DraftSource11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IndicatorRunPreviewIntent11 {
    pub operation: IndicatorRunPreviewIntent11Operation,
    pub arguments: PreviewArguments11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IndicatorAcceptVersionIntent11 {
    pub operation: IndicatorAcceptVersionIntent11Operation,
    pub arguments: AcceptPreview11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IndicatorRemoveArguments11 {
    pub layer_id: OpaqueId,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IndicatorRemoveIntent11 {
    pub operation: IndicatorRemoveIntent11Operation,
    pub arguments: IndicatorRemoveArguments11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceUndoArguments11 {
    pub operation_id: OpaqueId,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceUndoIntent11 {
    pub operation: WorkspaceUndoIntent11Operation,
    pub arguments: WorkspaceUndoArguments11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspacePreviewLayoutIntent11 {
    pub operation: WorkspacePreviewLayoutIntent11Operation,
    pub arguments: WorkspacePreviewLayoutInput11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceApplyLayoutIntent11 {
    pub operation: WorkspaceApplyLayoutIntent11Operation,
    pub arguments: WorkspaceApplyLayoutInput11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum MutationIntent11 {
    WorkspaceOpenInstrumentIntent11(Box<WorkspaceOpenInstrumentIntent11>),
    ChartSetTimeframeIntent11(Box<ChartSetTimeframeIntent11>),
    ChartSetStyleIntent11(Box<ChartSetStyleIntent11>),
    ChartAddAnnotationIntent11(Box<ChartAddAnnotationIntent11>),
    ChartRemoveLayerIntent11(Box<ChartRemoveLayerIntent11>),
    IndicatorCreateDraftIntent11(Box<IndicatorCreateDraftIntent11>),
    IndicatorRunPreviewIntent11(Box<IndicatorRunPreviewIntent11>),
    IndicatorAcceptVersionIntent11(Box<IndicatorAcceptVersionIntent11>),
    IndicatorRemoveIntent11(Box<IndicatorRemoveIntent11>),
    WorkspaceUndoIntent11(Box<WorkspaceUndoIntent11>),
    WorkspacePreviewLayoutIntent11(Box<WorkspacePreviewLayoutIntent11>),
    WorkspaceApplyLayoutIntent11(Box<WorkspaceApplyLayoutIntent11>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ScopedFailure11 {
    pub state: ScopedFailure11State,
    pub error: WorkspaceFault11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceFault11 {
    pub code: ErrorCode,
    pub message: String,
    pub retryable: bool,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PythonExecutionScope11 {
    pub code_hash: Hash,
    pub parameters_hash: Hash,
    pub snapshot_hash: Hash,
    pub trust_mode: PythonExecutionScope11TrustMode,
    pub input_use: PythonExecutionScope11InputUse,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ConfirmationChallenge11 {
    pub challenge_id: Uuid,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub operation: OperationName,
    pub principal_id: OpaqueId,
    pub session_id: OpaqueId,
    pub workspace_id: OpaqueId,
    pub target: EvidenceTarget11,
    pub workspace_revision: Revision,
    pub selection_revision: Revision,
    pub parameters_hash: Hash,
    pub expires_at_ms: Timestamp,
    pub python: Option<PythonExecutionScope11>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NeedsHumanDecision11 {
    pub state: NeedsHumanDecision11State,
    pub challenge: ConfirmationChallenge11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceOpenInstrumentCommitted11 {
    pub state: WorkspaceOpenInstrumentCommitted11State,
    pub operation: WorkspaceOpenInstrumentCommitted11Operation,
    pub receipt: OperationReceipt11,
    pub result: WorkspaceOpenInstrumentOutput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChartSetTimeframeCommitted11 {
    pub state: ChartSetTimeframeCommitted11State,
    pub operation: ChartSetTimeframeCommitted11Operation,
    pub receipt: OperationReceipt11,
    pub result: ChartSetTimeframeOutput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChartSetStyleCommitted11 {
    pub state: ChartSetStyleCommitted11State,
    pub operation: ChartSetStyleCommitted11Operation,
    pub receipt: OperationReceipt11,
    pub result: ChartSetStyleOutput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChartAddAnnotationCommitted11 {
    pub state: ChartAddAnnotationCommitted11State,
    pub operation: ChartAddAnnotationCommitted11Operation,
    pub receipt: OperationReceipt11,
    pub result: ChartAddAnnotationOutput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChartRemoveLayerCommitted11 {
    pub state: ChartRemoveLayerCommitted11State,
    pub operation: ChartRemoveLayerCommitted11Operation,
    pub receipt: OperationReceipt11,
    pub result: ChartRemoveLayerOutput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IndicatorCreateDraftCommitted11 {
    pub state: IndicatorCreateDraftCommitted11State,
    pub operation: IndicatorCreateDraftCommitted11Operation,
    pub receipt: OperationReceipt11,
    pub result: IndicatorCreateDraftOutput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IndicatorRunPreviewCommitted11 {
    pub state: IndicatorRunPreviewCommitted11State,
    pub operation: IndicatorRunPreviewCommitted11Operation,
    pub receipt: OperationReceipt11,
    pub result: IndicatorRunPreviewOutput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IndicatorAcceptVersionCommitted11 {
    pub state: IndicatorAcceptVersionCommitted11State,
    pub operation: IndicatorAcceptVersionCommitted11Operation,
    pub receipt: OperationReceipt11,
    pub result: IndicatorAcceptVersionOutput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IndicatorRemoveCommitted11 {
    pub state: IndicatorRemoveCommitted11State,
    pub operation: IndicatorRemoveCommitted11Operation,
    pub receipt: OperationReceipt11,
    pub result: IndicatorRemoveOutput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceUndoCommitted11 {
    pub state: WorkspaceUndoCommitted11State,
    pub operation: WorkspaceUndoCommitted11Operation,
    pub receipt: OperationReceipt11,
    pub result: WorkspaceUndoOutput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspacePreviewLayoutSuccess11 {
    pub state: WorkspacePreviewLayoutSuccess11State,
    pub operation: WorkspacePreviewLayoutSuccess11Operation,
    pub result: WorkspacePreviewLayoutOutput11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceApplyLayoutSuccess11 {
    pub state: WorkspaceApplyLayoutSuccess11State,
    pub operation: WorkspaceApplyLayoutSuccess11Operation,
    pub receipt: OperationReceipt11,
    pub result: NamedWorkspaceLayout11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum MutationOutcome11 {
    WorkspaceOpenInstrumentCommitted11(Box<WorkspaceOpenInstrumentCommitted11>),
    ChartSetTimeframeCommitted11(Box<ChartSetTimeframeCommitted11>),
    ChartSetStyleCommitted11(Box<ChartSetStyleCommitted11>),
    ChartAddAnnotationCommitted11(Box<ChartAddAnnotationCommitted11>),
    ChartRemoveLayerCommitted11(Box<ChartRemoveLayerCommitted11>),
    IndicatorCreateDraftCommitted11(Box<IndicatorCreateDraftCommitted11>),
    IndicatorRunPreviewCommitted11(Box<IndicatorRunPreviewCommitted11>),
    IndicatorAcceptVersionCommitted11(Box<IndicatorAcceptVersionCommitted11>),
    IndicatorRemoveCommitted11(Box<IndicatorRemoveCommitted11>),
    WorkspaceUndoCommitted11(Box<WorkspaceUndoCommitted11>),
    NeedsHumanDecision11(Box<NeedsHumanDecision11>),
    ScopedFailure11(Box<ScopedFailure11>),
    WorkspacePreviewLayoutSuccess11(Box<WorkspacePreviewLayoutSuccess11>),
    WorkspaceApplyLayoutSuccess11(Box<WorkspaceApplyLayoutSuccess11>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct InlineIndicatorArtifact11 {
    pub storage: InlineIndicatorArtifact11Storage,
    pub result_hash: Hash,
    pub result: IndicatorResult,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IndexedIndicatorArtifact11 {
    pub storage: IndexedIndicatorArtifact11Storage,
    pub artifact_id: OpaqueId,
    pub result_hash: Hash,
    pub byte_length: u64,
    pub segment_count: u64,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum IndicatorArtifact11 {
    InlineIndicatorArtifact11(Box<InlineIndicatorArtifact11>),
    IndexedIndicatorArtifact11(Box<IndexedIndicatorArtifact11>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TaskEffectSummary11 {
    pub operation_id: OpaqueId,
    pub workspace_id: OpaqueId,
    pub committed_workspace_revision: Revision,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TaskCompleted11 {
    pub status: TaskCompleted11Status,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TaskCancelled11 {
    pub status: TaskCancelled11Status,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TaskFailed11 {
    pub status: TaskFailed11Status,
    pub error: WorkspaceFault11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum TerminalState11 {
    TaskCompleted11(Box<TaskCompleted11>),
    TaskCancelled11(Box<TaskCancelled11>),
    TaskFailed11(Box<TaskFailed11>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ExportedBar11 {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub time_ms: Option<Timestamp>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub trading_day: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub open: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub high: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub low: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub close: Option<f64>,
    #[serde(
        default,
        skip_serializing_if = "Option::is_none",
        deserialize_with = "present_nullable"
    )]
    pub volume: Option<Option<f64>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub is_closed: Option<bool>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ExportField11 {
    #[serde(rename = "time_ms")]
    TimeMs,
    #[serde(rename = "trading_day")]
    TradingDay,
    #[serde(rename = "open")]
    Open,
    #[serde(rename = "high")]
    High,
    #[serde(rename = "low")]
    Low,
    #[serde(rename = "close")]
    Close,
    #[serde(rename = "volume")]
    Volume,
    #[serde(rename = "is_closed")]
    IsClosed,
}
impl ExportField11 {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::TimeMs => "time_ms",
            Self::TradingDay => "trading_day",
            Self::Open => "open",
            Self::High => "high",
            Self::Low => "low",
            Self::Close => "close",
            Self::Volume => "volume",
            Self::IsClosed => "is_closed",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProjectedChart11 {
    pub source_reference: OpaqueId,
    pub revision: OpaqueId,
    pub fields: Vec<ExportField11>,
    pub rows: Vec<ExportedBar11>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProjectedReadIntent11 {
    pub operation: ProjectedReadIntent11Operation,
    pub workspace_id: OpaqueId,
    pub selection_revision: Revision,
    pub requested_fields: Vec<ExportField11>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BoundMutationIntent11 {
    pub scope: MutationScope11,
    pub intent: MutationIntent11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MarketHistoryPageToolCall11 {
    pub operation: MarketHistoryPageToolCall11Operation,
    pub arguments: MarketHistoryPageInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SelectEvidenceIntent11 {
    pub operation: SelectEvidenceIntent11Operation,
    pub workspace_id: OpaqueId,
    pub expected_selection_revision: Revision,
    pub target: Option<EvidenceTarget11>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum AiRequestedAction11 {
    BoundMutationIntent11(Box<BoundMutationIntent11>),
    ProjectedReadIntent11(Box<ProjectedReadIntent11>),
    InstrumentSearchToolCall(Box<InstrumentSearchToolCall>),
    MarketGetBarsToolCall(Box<MarketGetBarsToolCall>),
    WorkspaceGetContextToolCall(Box<WorkspaceGetContextToolCall>),
    TaskCancelToolCall(Box<TaskCancelToolCall>),
    MarketHistoryPageToolCall11(Box<MarketHistoryPageToolCall11>),
    SelectEvidenceIntent11(Box<SelectEvidenceIntent11>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ToolReadResult11 {
    pub status: ToolReadResult11Status,
    pub view: ProjectedChart11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ToolCommittedResult11 {
    pub status: ToolCommittedResult11Status,
    pub operation_id: OpaqueId,
    pub committed_workspace_revision: Revision,
    pub undoable: bool,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ToolRefResult11 {
    pub status: ToolRefResult11Status,
    pub result_ref: OpaqueId,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ToolDeniedResult11 {
    pub status: ToolDeniedResult11Status,
    pub error: WorkspaceFault11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum AiToolOutcome11 {
    ToolReadResult11(Box<ToolReadResult11>),
    ToolCommittedResult11(Box<ToolCommittedResult11>),
    ToolRefResult11(Box<ToolRefResult11>),
    ToolDeniedResult11(Box<ToolDeniedResult11>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct InlineIndicatorBars11 {
    pub mode: InlineIndicatorBars11Mode,
    pub bars: Vec<Bar>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SegmentedIndicatorBars11 {
    pub mode: SegmentedIndicatorBars11Mode,
    pub total_bars: u64,
    pub segment_count: u64,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum IndicatorBarsInput11 {
    InlineIndicatorBars11(Box<InlineIndicatorBars11>),
    SegmentedIndicatorBars11(Box<SegmentedIndicatorBars11>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum LegacyOperationName {
    #[serde(rename = "instrument.search")]
    InstrumentSearch,
    #[serde(rename = "market.getBars")]
    MarketGetBars,
    #[serde(rename = "workspace.openInstrument")]
    WorkspaceOpenInstrument,
    #[serde(rename = "chart.setTimeframe")]
    ChartSetTimeframe,
    #[serde(rename = "chart.setStyle")]
    ChartSetStyle,
    #[serde(rename = "chart.addAnnotation")]
    ChartAddAnnotation,
    #[serde(rename = "chart.removeLayer")]
    ChartRemoveLayer,
    #[serde(rename = "indicator.createDraft")]
    IndicatorCreateDraft,
    #[serde(rename = "indicator.runPreview")]
    IndicatorRunPreview,
    #[serde(rename = "indicator.acceptVersion")]
    IndicatorAcceptVersion,
    #[serde(rename = "indicator.remove")]
    IndicatorRemove,
    #[serde(rename = "workspace.undo")]
    WorkspaceUndo,
    #[serde(rename = "task.cancel")]
    TaskCancel,
    #[serde(rename = "workspace.getContext")]
    WorkspaceGetContext,
}
impl LegacyOperationName {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::InstrumentSearch => "instrument.search",
            Self::MarketGetBars => "market.getBars",
            Self::WorkspaceOpenInstrument => "workspace.openInstrument",
            Self::ChartSetTimeframe => "chart.setTimeframe",
            Self::ChartSetStyle => "chart.setStyle",
            Self::ChartAddAnnotation => "chart.addAnnotation",
            Self::ChartRemoveLayer => "chart.removeLayer",
            Self::IndicatorCreateDraft => "indicator.createDraft",
            Self::IndicatorRunPreview => "indicator.runPreview",
            Self::IndicatorAcceptVersion => "indicator.acceptVersion",
            Self::IndicatorRemove => "indicator.remove",
            Self::WorkspaceUndo => "workspace.undo",
            Self::TaskCancel => "task.cancel",
            Self::WorkspaceGetContext => "workspace.getContext",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProjectedChartArguments11 {
    pub workspace_id: OpaqueId,
    pub selection_revision: Revision,
    pub requested_fields: Vec<ExportField11>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SelectEvidenceArguments11 {
    pub workspace_id: OpaqueId,
    pub expected_selection_revision: Revision,
    pub target: Option<EvidenceTarget11>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum ProjectedChartReply11 {
    ProjectedChartResponse11(Box<ProjectedChartResponse11>),
    ProjectedChartError11(Box<ProjectedChartError11>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum SelectEvidenceReply11 {
    SharedSelectionResponse11(Box<SharedSelectionResponse11>),
    SharedSelectionError11(Box<SharedSelectionError11>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SharedContext11 {
    pub workspace_id: OpaqueId,
    pub workspace_revision: Revision,
    pub selection: SharedSelection11,
    pub chart: Option<ProjectedChart11>,
    pub authorized_operations: Vec<OperationName>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum SharedContextReply11 {
    SharedContextResponse11(Box<SharedContextResponse11>),
    SharedContextError11(Box<SharedContextError11>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum CancelTaskReply11 {
    CancelTaskResponse11(Box<CancelTaskResponse11>),
    CancelTaskError11(Box<CancelTaskError11>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceOpenInstrumentCommand11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: WorkspaceOpenInstrumentCommand11Type,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub scope: MutationScope11,
    pub intent: WorkspaceOpenInstrumentIntent11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum WorkspaceOpenInstrumentOutcome11 {
    WorkspaceOpenInstrumentCommitted11(Box<WorkspaceOpenInstrumentCommitted11>),
    NeedsHumanDecision11(Box<NeedsHumanDecision11>),
    ScopedFailure11(Box<ScopedFailure11>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceOpenInstrumentReply11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: WorkspaceOpenInstrumentReply11Type,
    pub request_id: Uuid,
    pub outcome: WorkspaceOpenInstrumentOutcome11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChartSetTimeframeCommand11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: ChartSetTimeframeCommand11Type,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub scope: MutationScope11,
    pub intent: ChartSetTimeframeIntent11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum ChartSetTimeframeOutcome11 {
    ChartSetTimeframeCommitted11(Box<ChartSetTimeframeCommitted11>),
    NeedsHumanDecision11(Box<NeedsHumanDecision11>),
    ScopedFailure11(Box<ScopedFailure11>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChartSetTimeframeReply11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: ChartSetTimeframeReply11Type,
    pub request_id: Uuid,
    pub outcome: ChartSetTimeframeOutcome11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChartSetStyleCommand11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: ChartSetStyleCommand11Type,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub scope: MutationScope11,
    pub intent: ChartSetStyleIntent11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum ChartSetStyleOutcome11 {
    ChartSetStyleCommitted11(Box<ChartSetStyleCommitted11>),
    NeedsHumanDecision11(Box<NeedsHumanDecision11>),
    ScopedFailure11(Box<ScopedFailure11>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChartSetStyleReply11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: ChartSetStyleReply11Type,
    pub request_id: Uuid,
    pub outcome: ChartSetStyleOutcome11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChartAddAnnotationCommand11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: ChartAddAnnotationCommand11Type,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub scope: MutationScope11,
    pub intent: ChartAddAnnotationIntent11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum ChartAddAnnotationOutcome11 {
    ChartAddAnnotationCommitted11(Box<ChartAddAnnotationCommitted11>),
    NeedsHumanDecision11(Box<NeedsHumanDecision11>),
    ScopedFailure11(Box<ScopedFailure11>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChartAddAnnotationReply11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: ChartAddAnnotationReply11Type,
    pub request_id: Uuid,
    pub outcome: ChartAddAnnotationOutcome11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChartRemoveLayerCommand11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: ChartRemoveLayerCommand11Type,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub scope: MutationScope11,
    pub intent: ChartRemoveLayerIntent11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum ChartRemoveLayerOutcome11 {
    ChartRemoveLayerCommitted11(Box<ChartRemoveLayerCommitted11>),
    NeedsHumanDecision11(Box<NeedsHumanDecision11>),
    ScopedFailure11(Box<ScopedFailure11>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChartRemoveLayerReply11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: ChartRemoveLayerReply11Type,
    pub request_id: Uuid,
    pub outcome: ChartRemoveLayerOutcome11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IndicatorCreateDraftCommand11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: IndicatorCreateDraftCommand11Type,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub scope: MutationScope11,
    pub intent: IndicatorCreateDraftIntent11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum IndicatorCreateDraftOutcome11 {
    IndicatorCreateDraftCommitted11(Box<IndicatorCreateDraftCommitted11>),
    NeedsHumanDecision11(Box<NeedsHumanDecision11>),
    ScopedFailure11(Box<ScopedFailure11>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IndicatorCreateDraftReply11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: IndicatorCreateDraftReply11Type,
    pub request_id: Uuid,
    pub outcome: IndicatorCreateDraftOutcome11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IndicatorRunPreviewCommand11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: IndicatorRunPreviewCommand11Type,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub scope: MutationScope11,
    pub intent: IndicatorRunPreviewIntent11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum IndicatorRunPreviewOutcome11 {
    IndicatorRunPreviewCommitted11(Box<IndicatorRunPreviewCommitted11>),
    NeedsHumanDecision11(Box<NeedsHumanDecision11>),
    ScopedFailure11(Box<ScopedFailure11>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IndicatorRunPreviewReply11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: IndicatorRunPreviewReply11Type,
    pub request_id: Uuid,
    pub outcome: IndicatorRunPreviewOutcome11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IndicatorAcceptVersionCommand11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: IndicatorAcceptVersionCommand11Type,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub scope: MutationScope11,
    pub intent: IndicatorAcceptVersionIntent11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum IndicatorAcceptVersionOutcome11 {
    IndicatorAcceptVersionCommitted11(Box<IndicatorAcceptVersionCommitted11>),
    NeedsHumanDecision11(Box<NeedsHumanDecision11>),
    ScopedFailure11(Box<ScopedFailure11>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IndicatorAcceptVersionReply11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: IndicatorAcceptVersionReply11Type,
    pub request_id: Uuid,
    pub outcome: IndicatorAcceptVersionOutcome11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IndicatorRemoveCommand11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: IndicatorRemoveCommand11Type,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub scope: MutationScope11,
    pub intent: IndicatorRemoveIntent11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum IndicatorRemoveOutcome11 {
    IndicatorRemoveCommitted11(Box<IndicatorRemoveCommitted11>),
    NeedsHumanDecision11(Box<NeedsHumanDecision11>),
    ScopedFailure11(Box<ScopedFailure11>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IndicatorRemoveReply11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: IndicatorRemoveReply11Type,
    pub request_id: Uuid,
    pub outcome: IndicatorRemoveOutcome11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceUndoCommand11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: WorkspaceUndoCommand11Type,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub scope: MutationScope11,
    pub intent: WorkspaceUndoIntent11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum WorkspaceUndoOutcome11 {
    WorkspaceUndoCommitted11(Box<WorkspaceUndoCommitted11>),
    NeedsHumanDecision11(Box<NeedsHumanDecision11>),
    ScopedFailure11(Box<ScopedFailure11>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceUndoReply11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: WorkspaceUndoReply11Type,
    pub request_id: Uuid,
    pub outcome: WorkspaceUndoOutcome11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum HistoryPageReply11 {
    MarketHistoryPageResponse(Box<MarketHistoryPageResponse>),
    MarketHistoryPageError(Box<MarketHistoryPageError>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CsvColumnMappingV1 {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub symbol: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub timestamp: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub trading_day: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub open: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub high: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub low: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub close: Option<String>,
    #[serde(
        default,
        skip_serializing_if = "Option::is_none",
        deserialize_with = "present_nullable"
    )]
    pub volume: Option<Option<String>>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CsvMappingV1 {
    pub version: u64,
    pub revision: String,
    pub encoding: String,
    pub delimiter: String,
    pub record_kind: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub columns: Option<CsvColumnMappingV1>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub instrument_kind: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub venue: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub timeframe: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub calendar_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub session_scope: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub timezone: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub quantity_unit: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub price_adjustment: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub roll_method: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub volume_semantics: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub timestamp_convention: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub timestamp_format: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub trading_day_format: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub daily_start_time: Option<String>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PreviewIssue {
    pub code: String,
    pub field: Option<String>,
    pub message: String,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RawOnlyColumn {
    pub column: String,
    pub reason: String,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PreviewSeries {
    pub instrument_kind: String,
    pub venue: String,
    pub timeframe: String,
    pub calendar_id: String,
    pub session_scope: String,
    pub timezone: String,
    pub quantity_unit: String,
    pub price_adjustment: String,
    pub roll_method: Option<String>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PreviewRow {
    pub record_number: u64,
    pub line_start: u64,
    pub line_end: u64,
    pub raw_fields: Vec<String>,
    pub raw_text: Option<String>,
    pub symbol: Option<String>,
    pub normalized: Option<Bar>,
    pub issues: Vec<PreviewIssue>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CsvPreview {
    pub file_sha256: Hash,
    pub mapping_version: Option<u64>,
    pub mapping_revision: Option<String>,
    pub header: Vec<String>,
    pub raw_header_text: Option<String>,
    pub rows: Vec<PreviewRow>,
    pub issues: Vec<PreviewIssue>,
    pub raw_only_columns: Vec<RawOnlyColumn>,
    pub series: Option<PreviewSeries>,
    pub truncated: bool,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LocalImportPreviewInput {
    pub version: u64,
    pub folder_id: Uuid,
    pub file_id: Uuid,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub expected_sha256: Option<Hash>,
    pub mapping: CsvMappingV1,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub row_limit: Option<u64>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LocalImportPreviewOutput {
    pub version: u64,
    pub folder_id: Uuid,
    pub file_id: Uuid,
    pub relative_path: String,
    pub display_name: String,
    pub byte_count: u64,
    pub sha256: Hash,
    pub preview: CsvPreview,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum LocalImportPreviewReply11 {
    LocalImportPreviewResponse(Box<LocalImportPreviewResponse>),
    LocalImportPreviewError(Box<LocalImportPreviewError>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SavedLocalImportMapping {
    pub review_revision: u64,
    pub header_sha256: Hash,
    pub mapping: CsvMappingV1,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SaveLocalImportMappingInput {
    pub version: u64,
    pub folder_id: Uuid,
    pub file_id: Uuid,
    pub expected_sha256: Hash,
    pub mapping: CsvMappingV1,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub row_limit: Option<u64>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SaveLocalImportMappingOutput {
    pub version: u64,
    pub record: SavedLocalImportMapping,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LoadLocalImportMappingInput {
    pub version: u64,
    pub folder_id: Uuid,
    pub file_id: Uuid,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LoadLocalImportMappingOutput {
    pub version: u64,
    pub status: LoadLocalImportMappingOutputStatus,
    pub record: Option<SavedLocalImportMapping>,
    pub source_sha256: Option<Hash>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum SaveLocalImportMappingReply11 {
    SaveLocalImportMappingResponse(Box<SaveLocalImportMappingResponse>),
    SaveLocalImportMappingError(Box<SaveLocalImportMappingError>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum LoadLocalImportMappingReply11 {
    LoadLocalImportMappingResponse(Box<LoadLocalImportMappingResponse>),
    LoadLocalImportMappingError(Box<LoadLocalImportMappingError>),
}
pub type DecisionNoteUuid11 = String;
pub type DecisionNoteDate = String;
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteEvidenceRef {
    pub source_id: String,
    pub source_revision: String,
    pub locator: String,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteOriginal {
    pub note_id: DecisionNoteUuid11,
    pub decided_on: DecisionNoteDate,
    pub thesis: String,
    pub evidence_refs: Vec<DecisionNoteEvidenceRef>,
    pub choice: String,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteSummary {
    pub note_id: DecisionNoteUuid11,
    pub decided_on: DecisionNoteDate,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteOutcome {
    pub outcome_id: DecisionNoteUuid11,
    pub observed_on: DecisionNoteDate,
    pub outcome: String,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteReadOutput {
    pub note: Option<DecisionNoteOriginal>,
    pub notes: Vec<DecisionNoteSummary>,
    pub outcomes: Vec<DecisionNoteOutcome>,
    pub next_offset: Option<Revision>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteTarget {
    pub kind: DecisionNoteTargetKind,
    pub object_id: DecisionNoteUuid11,
}
pub type PrivateRecordUuid11 = String;
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrivateRecordReadScope11 {
    pub workspace_id: OpaqueId,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrivateRecordChangeScope11 {
    pub workspace_id: OpaqueId,
    pub expected_workspace_revision: Revision,
    pub idempotency_key: PrivateRecordUuid11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PrivateRecordKind {
    #[serde(rename = "conversation")]
    Conversation,
    #[serde(rename = "memory")]
    Memory,
    #[serde(rename = "strategy")]
    Strategy,
}
impl PrivateRecordKind {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Conversation => "conversation",
            Self::Memory => "memory",
            Self::Strategy => "strategy",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrivateRecordTurn {
    pub speaker: PrivateRecordTurnSpeaker,
    pub text: String,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrivateRecordConversation {
    pub kind: PrivateRecordConversationKind,
    pub title: String,
    pub turns: Vec<PrivateRecordTurn>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrivateRecordMemory {
    pub kind: PrivateRecordMemoryKind,
    pub title: String,
    pub text: String,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrivateRecordStrategy {
    pub kind: PrivateRecordStrategyKind,
    pub title: String,
    pub text: String,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum PrivateRecordContent {
    PrivateRecordConversation(Box<PrivateRecordConversation>),
    PrivateRecordMemory(Box<PrivateRecordMemory>),
    PrivateRecordStrategy(Box<PrivateRecordStrategy>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrivateRecordEntry {
    pub record_id: PrivateRecordUuid11,
    pub revision: Revision,
    pub content: PrivateRecordContent,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrivateRecordSummary {
    pub record_id: PrivateRecordUuid11,
    pub revision: Revision,
    pub kind: PrivateRecordKind,
    pub title: String,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrivateRecordArchiveEntry {
    pub record_id: PrivateRecordUuid11,
    pub content: PrivateRecordContent,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrivateRecordBackupV1 {
    pub format: PrivateRecordBackupV1Format,
    pub version: u64,
    pub profile_binding: String,
    pub records: Vec<PrivateRecordArchiveEntry>,
    pub deletions: Vec<PrivateRecordUuid11>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrivateRecordReadInput {
    pub record_id: Option<PrivateRecordUuid11>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrivateRecordReadOutput {
    pub workspace_revision: Revision,
    pub record: Option<PrivateRecordEntry>,
    pub records: Vec<PrivateRecordSummary>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrivateRecordSaveInput {
    pub action: PrivateRecordSaveInputAction,
    pub record_id: PrivateRecordUuid11,
    pub expected_record_revision: Option<Revision>,
    pub content: PrivateRecordContent,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrivateRecordDeleteInput {
    pub action: PrivateRecordDeleteInputAction,
    pub record_id: PrivateRecordUuid11,
    pub expected_record_revision: Revision,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrivateRecordRestoreInput {
    pub action: PrivateRecordRestoreInputAction,
    pub backup: PrivateRecordBackupV1,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrivateRecordResolveInput {
    pub action: PrivateRecordResolveInputAction,
    pub challenge_id: PrivateRecordUuid11,
    pub decision: PrivateRecordResolveInputDecision,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum PrivateRecordChangeInput {
    PrivateRecordSaveInput(Box<PrivateRecordSaveInput>),
    PrivateRecordDeleteInput(Box<PrivateRecordDeleteInput>),
    PrivateRecordRestoreInput(Box<PrivateRecordRestoreInput>),
    PrivateRecordResolveInput(Box<PrivateRecordResolveInput>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrivateRecordChangeSummary {
    pub added: u64,
    pub corrected: u64,
    pub removed: u64,
    pub kept: u64,
    pub suppressed: u64,
    pub imported_deletions: u64,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrivateRecordReceipt11 {
    pub operation_id: PrivateRecordUuid11,
    pub workspace_id: OpaqueId,
    pub record_id: Option<PrivateRecordUuid11>,
    pub committed_workspace_revision: Revision,
    pub undoable: ConstBool<false>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrivateRecordExportInput {}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrivateRecordReadIntent11 {
    pub operation: PrivateRecordReadIntent11Operation,
    pub arguments: PrivateRecordReadInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrivateRecordReadCommand11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: PrivateRecordUuid11,
    pub timestamp_ms: Timestamp,
    pub r#type: PrivateRecordReadCommand11Type,
    pub request_id: PrivateRecordUuid11,
    pub scope: PrivateRecordReadScope11,
    pub intent: PrivateRecordReadIntent11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrivateRecordReadCompleted11 {
    pub state: PrivateRecordReadCompleted11State,
    pub operation: PrivateRecordReadCompleted11Operation,
    pub result: PrivateRecordReadOutput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum PrivateRecordReadOutcome11 {
    PrivateRecordReadCompleted11(Box<PrivateRecordReadCompleted11>),
    ScopedFailure11(Box<ScopedFailure11>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrivateRecordReadReply11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: PrivateRecordUuid11,
    pub timestamp_ms: Timestamp,
    pub r#type: PrivateRecordReadReply11Type,
    pub request_id: PrivateRecordUuid11,
    pub outcome: PrivateRecordReadOutcome11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrivateRecordChangeIntent11 {
    pub operation: PrivateRecordChangeIntent11Operation,
    pub arguments: PrivateRecordChangeInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrivateRecordChangeCommand11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: PrivateRecordUuid11,
    pub timestamp_ms: Timestamp,
    pub r#type: PrivateRecordChangeCommand11Type,
    pub request_id: PrivateRecordUuid11,
    pub scope: PrivateRecordChangeScope11,
    pub intent: PrivateRecordChangeIntent11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrivateRecordChangeCommitted11 {
    pub state: PrivateRecordChangeCommitted11State,
    pub operation: PrivateRecordChangeCommitted11Operation,
    pub receipt: PrivateRecordReceipt11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrivateRecordChangeConfirmationRequired11 {
    pub state: PrivateRecordChangeConfirmationRequired11State,
    pub operation: PrivateRecordChangeConfirmationRequired11Operation,
    pub challenge_id: PrivateRecordUuid11,
    pub expires_at_ms: Timestamp,
    pub summary: PrivateRecordChangeSummary,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum PrivateRecordChangeOutcome11 {
    PrivateRecordChangeCommitted11(Box<PrivateRecordChangeCommitted11>),
    PrivateRecordChangeConfirmationRequired11(Box<PrivateRecordChangeConfirmationRequired11>),
    ScopedFailure11(Box<ScopedFailure11>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrivateRecordChangeReply11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: PrivateRecordUuid11,
    pub timestamp_ms: Timestamp,
    pub r#type: PrivateRecordChangeReply11Type,
    pub request_id: PrivateRecordUuid11,
    pub outcome: PrivateRecordChangeOutcome11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrivateRecordExportIntent11 {
    pub operation: PrivateRecordExportIntent11Operation,
    pub arguments: PrivateRecordExportInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrivateRecordExportCommand11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: PrivateRecordUuid11,
    pub timestamp_ms: Timestamp,
    pub r#type: PrivateRecordExportCommand11Type,
    pub request_id: PrivateRecordUuid11,
    pub scope: PrivateRecordReadScope11,
    pub intent: PrivateRecordExportIntent11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrivateRecordExportCompleted11 {
    pub state: PrivateRecordExportCompleted11State,
    pub operation: PrivateRecordExportCompleted11Operation,
    pub result: PrivateRecordBackupV1,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum PrivateRecordExportOutcome11 {
    PrivateRecordExportCompleted11(Box<PrivateRecordExportCompleted11>),
    ScopedFailure11(Box<ScopedFailure11>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrivateRecordExportReply11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: PrivateRecordUuid11,
    pub timestamp_ms: Timestamp,
    pub r#type: PrivateRecordExportReply11Type,
    pub request_id: PrivateRecordUuid11,
    pub outcome: PrivateRecordExportOutcome11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteReceipt11 {
    pub operation_id: DecisionNoteUuid11,
    pub workspace_id: OpaqueId,
    pub target: DecisionNoteTarget,
    pub committed_workspace_revision: Revision,
    pub undoable: ConstBool<false>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteReadScope11 {
    pub workspace_id: OpaqueId,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteWriteScope11 {
    pub workspace_id: OpaqueId,
    pub target: DecisionNoteTarget,
    pub expected_workspace_revision: Revision,
    pub idempotency_key: DecisionNoteUuid11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteConfirmation11 {
    pub challenge_id: DecisionNoteUuid11,
    pub decision: DecisionNoteConfirmation11Decision,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteSaveInput {
    pub decided_on: DecisionNoteDate,
    pub thesis: String,
    pub evidence_refs: Vec<DecisionNoteEvidenceRef>,
    pub choice: String,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteSaveIntent11 {
    pub operation: DecisionNoteSaveIntent11Operation,
    pub arguments: DecisionNoteSaveInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteSaveCommand11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: DecisionNoteUuid11,
    pub timestamp_ms: Timestamp,
    pub r#type: DecisionNoteSaveCommand11Type,
    pub request_id: DecisionNoteUuid11,
    pub scope: DecisionNoteWriteScope11,
    pub intent: DecisionNoteSaveIntent11,
    pub confirmation: Option<DecisionNoteConfirmation11>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteSaveCommitted11 {
    pub state: DecisionNoteSaveCommitted11State,
    pub operation: DecisionNoteSaveCommitted11Operation,
    pub receipt: DecisionNoteReceipt11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteSaveConfirmationRequired11 {
    pub state: DecisionNoteSaveConfirmationRequired11State,
    pub operation: DecisionNoteSaveConfirmationRequired11Operation,
    pub challenge_id: DecisionNoteUuid11,
    pub expires_at_ms: Timestamp,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum DecisionNoteSaveOutcome11 {
    DecisionNoteSaveCommitted11(Box<DecisionNoteSaveCommitted11>),
    DecisionNoteSaveConfirmationRequired11(Box<DecisionNoteSaveConfirmationRequired11>),
    ScopedFailure11(Box<ScopedFailure11>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteSaveReply11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: DecisionNoteUuid11,
    pub timestamp_ms: Timestamp,
    pub r#type: DecisionNoteSaveReply11Type,
    pub request_id: DecisionNoteUuid11,
    pub outcome: DecisionNoteSaveOutcome11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteReadInput {
    pub note_id: Option<DecisionNoteUuid11>,
    pub offset: Revision,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteReadIntent11 {
    pub operation: DecisionNoteReadIntent11Operation,
    pub arguments: DecisionNoteReadInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteReadCommand11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: DecisionNoteUuid11,
    pub timestamp_ms: Timestamp,
    pub r#type: DecisionNoteReadCommand11Type,
    pub request_id: DecisionNoteUuid11,
    pub scope: DecisionNoteReadScope11,
    pub intent: DecisionNoteReadIntent11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteReadCompleted11 {
    pub state: DecisionNoteReadCompleted11State,
    pub operation: DecisionNoteReadCompleted11Operation,
    pub result: DecisionNoteReadOutput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum DecisionNoteReadOutcome11 {
    DecisionNoteReadCompleted11(Box<DecisionNoteReadCompleted11>),
    ScopedFailure11(Box<ScopedFailure11>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteReadReply11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: DecisionNoteUuid11,
    pub timestamp_ms: Timestamp,
    pub r#type: DecisionNoteReadReply11Type,
    pub request_id: DecisionNoteUuid11,
    pub outcome: DecisionNoteReadOutcome11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteAppendOutcomeInput {
    pub observed_on: DecisionNoteDate,
    pub outcome: String,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteAppendOutcomeIntent11 {
    pub operation: DecisionNoteAppendOutcomeIntent11Operation,
    pub arguments: DecisionNoteAppendOutcomeInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteAppendOutcomeCommand11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: DecisionNoteUuid11,
    pub timestamp_ms: Timestamp,
    pub r#type: DecisionNoteAppendOutcomeCommand11Type,
    pub request_id: DecisionNoteUuid11,
    pub scope: DecisionNoteWriteScope11,
    pub intent: DecisionNoteAppendOutcomeIntent11,
    pub confirmation: Option<DecisionNoteConfirmation11>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteAppendOutcomeCommitted11 {
    pub state: DecisionNoteAppendOutcomeCommitted11State,
    pub operation: DecisionNoteAppendOutcomeCommitted11Operation,
    pub receipt: DecisionNoteReceipt11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteAppendOutcomeConfirmationRequired11 {
    pub state: DecisionNoteAppendOutcomeConfirmationRequired11State,
    pub operation: DecisionNoteAppendOutcomeConfirmationRequired11Operation,
    pub challenge_id: DecisionNoteUuid11,
    pub expires_at_ms: Timestamp,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum DecisionNoteAppendOutcomeOutcome11 {
    DecisionNoteAppendOutcomeCommitted11(Box<DecisionNoteAppendOutcomeCommitted11>),
    DecisionNoteAppendOutcomeConfirmationRequired11(
        Box<DecisionNoteAppendOutcomeConfirmationRequired11>,
    ),
    ScopedFailure11(Box<ScopedFailure11>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteAppendOutcomeReply11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: DecisionNoteUuid11,
    pub timestamp_ms: Timestamp,
    pub r#type: DecisionNoteAppendOutcomeReply11Type,
    pub request_id: DecisionNoteUuid11,
    pub outcome: DecisionNoteAppendOutcomeOutcome11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteDeleteInput {}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteDeleteIntent11 {
    pub operation: DecisionNoteDeleteIntent11Operation,
    pub arguments: DecisionNoteDeleteInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteDeleteCommand11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: DecisionNoteUuid11,
    pub timestamp_ms: Timestamp,
    pub r#type: DecisionNoteDeleteCommand11Type,
    pub request_id: DecisionNoteUuid11,
    pub scope: DecisionNoteWriteScope11,
    pub intent: DecisionNoteDeleteIntent11,
    pub confirmation: Option<DecisionNoteConfirmation11>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteDeleteCommitted11 {
    pub state: DecisionNoteDeleteCommitted11State,
    pub operation: DecisionNoteDeleteCommitted11Operation,
    pub receipt: DecisionNoteReceipt11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteDeleteConfirmationRequired11 {
    pub state: DecisionNoteDeleteConfirmationRequired11State,
    pub operation: DecisionNoteDeleteConfirmationRequired11Operation,
    pub challenge_id: DecisionNoteUuid11,
    pub expires_at_ms: Timestamp,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum DecisionNoteDeleteOutcome11 {
    DecisionNoteDeleteCommitted11(Box<DecisionNoteDeleteCommitted11>),
    DecisionNoteDeleteConfirmationRequired11(Box<DecisionNoteDeleteConfirmationRequired11>),
    ScopedFailure11(Box<ScopedFailure11>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteDeleteReply11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: DecisionNoteUuid11,
    pub timestamp_ms: Timestamp,
    pub r#type: DecisionNoteDeleteReply11Type,
    pub request_id: DecisionNoteUuid11,
    pub outcome: DecisionNoteDeleteOutcome11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteBackupEntry {
    pub original: DecisionNoteOriginal,
    pub outcomes: Vec<DecisionNoteOutcome>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteBackupDeletion {
    pub note_id: DecisionNoteUuid11,
    pub deleted_at_ms: Timestamp,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteBackupV1 {
    pub format: DecisionNoteBackupV1Format,
    pub version: u64,
    pub profile_binding: String,
    pub captured_workspace_revision: Revision,
    pub notes: Vec<DecisionNoteBackupEntry>,
    pub deletions: Vec<DecisionNoteBackupDeletion>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteRestoreScope11 {
    pub workspace_id: OpaqueId,
    pub expected_workspace_revision: Revision,
    pub idempotency_key: DecisionNoteUuid11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteRestoreSummary {
    pub added_notes: Revision,
    pub added_outcomes: Revision,
    pub removed_notes: Revision,
    pub suppressed_notes: Revision,
    pub imported_deletions: Revision,
    pub independent_copy_warning: bool,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteExportBackupInput {}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNotePreviewRestoreInput {
    pub backup: DecisionNoteBackupV1,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteRestoreBackupInput {
    pub stage_id: DecisionNoteUuid11,
    pub decision: DecisionNoteRestoreBackupInputDecision,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteExportBackupIntent11 {
    pub operation: DecisionNoteExportBackupIntent11Operation,
    pub arguments: DecisionNoteExportBackupInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteExportBackupCommand11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: DecisionNoteUuid11,
    pub timestamp_ms: Timestamp,
    pub r#type: DecisionNoteExportBackupCommand11Type,
    pub request_id: DecisionNoteUuid11,
    pub scope: DecisionNoteReadScope11,
    pub intent: DecisionNoteExportBackupIntent11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteExportBackupCompleted11 {
    pub state: DecisionNoteExportBackupCompleted11State,
    pub operation: DecisionNoteExportBackupCompleted11Operation,
    pub result: DecisionNoteBackupV1,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum DecisionNoteExportBackupOutcome11 {
    DecisionNoteExportBackupCompleted11(Box<DecisionNoteExportBackupCompleted11>),
    ScopedFailure11(Box<ScopedFailure11>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteExportBackupReply11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: DecisionNoteUuid11,
    pub timestamp_ms: Timestamp,
    pub r#type: DecisionNoteExportBackupReply11Type,
    pub request_id: DecisionNoteUuid11,
    pub outcome: DecisionNoteExportBackupOutcome11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNotePreviewRestoreIntent11 {
    pub operation: DecisionNotePreviewRestoreIntent11Operation,
    pub arguments: DecisionNotePreviewRestoreInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNotePreviewRestoreCommand11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: DecisionNoteUuid11,
    pub timestamp_ms: Timestamp,
    pub r#type: DecisionNotePreviewRestoreCommand11Type,
    pub request_id: DecisionNoteUuid11,
    pub scope: DecisionNoteRestoreScope11,
    pub intent: DecisionNotePreviewRestoreIntent11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNotePreviewRestoreConfirmationRequired11 {
    pub state: DecisionNotePreviewRestoreConfirmationRequired11State,
    pub operation: DecisionNotePreviewRestoreConfirmationRequired11Operation,
    pub stage_id: DecisionNoteUuid11,
    pub expires_at_ms: Timestamp,
    pub summary: DecisionNoteRestoreSummary,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum DecisionNotePreviewRestoreOutcome11 {
    DecisionNotePreviewRestoreConfirmationRequired11(
        Box<DecisionNotePreviewRestoreConfirmationRequired11>,
    ),
    ScopedFailure11(Box<ScopedFailure11>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNotePreviewRestoreReply11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: DecisionNoteUuid11,
    pub timestamp_ms: Timestamp,
    pub r#type: DecisionNotePreviewRestoreReply11Type,
    pub request_id: DecisionNoteUuid11,
    pub outcome: DecisionNotePreviewRestoreOutcome11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteRestoreBackupIntent11 {
    pub operation: DecisionNoteRestoreBackupIntent11Operation,
    pub arguments: DecisionNoteRestoreBackupInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteRestoreBackupCommand11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: DecisionNoteUuid11,
    pub timestamp_ms: Timestamp,
    pub r#type: DecisionNoteRestoreBackupCommand11Type,
    pub request_id: DecisionNoteUuid11,
    pub scope: DecisionNoteRestoreScope11,
    pub intent: DecisionNoteRestoreBackupIntent11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteRestoreBackupCommitted11 {
    pub state: DecisionNoteRestoreBackupCommitted11State,
    pub operation: DecisionNoteRestoreBackupCommitted11Operation,
    pub receipt: OperationReceipt11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum DecisionNoteRestoreBackupOutcome11 {
    DecisionNoteRestoreBackupCommitted11(Box<DecisionNoteRestoreBackupCommitted11>),
    ScopedFailure11(Box<ScopedFailure11>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionNoteRestoreBackupReply11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: DecisionNoteUuid11,
    pub timestamp_ms: Timestamp,
    pub r#type: DecisionNoteRestoreBackupReply11Type,
    pub request_id: DecisionNoteUuid11,
    pub outcome: DecisionNoteRestoreBackupOutcome11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LocalImportRowLocator {
    pub record_number: u64,
    pub line_start: u64,
    pub line_end: u64,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PublishedLocalImportBar {
    pub bar: Bar,
    pub locators: Vec<LocalImportRowLocator>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PublishedLocalImportRevision {
    pub revision_id: Uuid,
    pub relative_path: String,
    pub source_sha256: Hash,
    pub mapping_sha256: Hash,
    pub review_revision: u64,
    pub symbol: String,
    pub series: PreviewSeries,
    pub bar_count: u64,
    pub source_record_count: u64,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PublishLocalImportInput {
    pub version: u64,
    pub folder_id: Uuid,
    pub file_id: Uuid,
    pub expected_sha256: Hash,
    pub review_revision: u64,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PublishLocalImportOutput {
    pub version: u64,
    pub revision: PublishedLocalImportRevision,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LocalImportRevisionBarSummary {
    pub venue: String,
    pub first_trading_day: String,
    pub final_trading_day: String,
    pub final_close: f64,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LocalImportRevisionListEntry {
    pub revision_id: CanonicalRevisionCursor,
    pub relative_path: String,
    pub symbol: String,
    pub bar_count: u64,
    pub source_record_count: u64,
    pub source_sha256: Hash,
    pub timeframe: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub bar_summary: Option<LocalImportRevisionBarSummary>,
}
pub type CanonicalRevisionCursor = String;
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ListLocalImportRevisionsInput {
    pub version: u64,
    pub after_revision_id: Option<CanonicalRevisionCursor>,
    pub limit: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub include_bar_summary: Option<bool>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ListLocalImportRevisionsOutput {
    pub version: u64,
    pub revisions: Vec<LocalImportRevisionListEntry>,
    pub next_after_revision_id: Option<CanonicalRevisionCursor>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum ListLocalImportRevisionsReply11 {
    ListLocalImportRevisionsResponse(Box<ListLocalImportRevisionsResponse>),
    ListLocalImportRevisionsError(Box<ListLocalImportRevisionsError>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReadLocalImportRevisionInput {
    pub version: u64,
    pub revision_id: Uuid,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReadLocalImportRevisionOutput {
    pub version: u64,
    pub revision: PublishedLocalImportRevision,
    pub bars: Vec<PublishedLocalImportBar>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum PublishLocalImportReply11 {
    PublishLocalImportResponse(Box<PublishLocalImportResponse>),
    PublishLocalImportError(Box<PublishLocalImportError>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum ReadLocalImportRevisionReply11 {
    ReadLocalImportRevisionResponse(Box<ReadLocalImportRevisionResponse>),
    ReadLocalImportRevisionError(Box<ReadLocalImportRevisionError>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReadLocalImportSourceRowInput {
    pub version: u64,
    pub revision_id: Uuid,
    pub locator: LocalImportRowLocator,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReadLocalImportSourceRowOutput {
    pub version: u64,
    pub revision_id: Uuid,
    pub source_sha256: Hash,
    pub locator: LocalImportRowLocator,
    pub header: Vec<String>,
    pub raw_fields: Vec<String>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum ReadLocalImportSourceRowReply11 {
    ReadLocalImportSourceRowResponse(Box<ReadLocalImportSourceRowResponse>),
    ReadLocalImportSourceRowError(Box<ReadLocalImportSourceRowError>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RevokeLocalImportRevisionInput {
    pub version: u64,
    pub revision_id: Uuid,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum RevokeLocalImportRevisionReply11 {
    RevokeLocalImportRevisionResponse(Box<RevokeLocalImportRevisionResponse>),
    RevokeLocalImportRevisionError(Box<RevokeLocalImportRevisionError>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RevokeLocalImportRevisionOutput {
    pub version: u64,
    pub revision_id: Uuid,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FolderRefreshFile {
    pub relative_path: String,
    pub source_sha256: Option<Hash>,
    pub prior_sha256: Option<Hash>,
    pub change: FolderRefreshFileChange,
    pub alias_of: Option<String>,
    pub review: FolderRefreshFileReview,
    pub detail: Option<String>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FolderRefreshSource {
    pub relative_path: String,
    pub source_sha256: Hash,
    pub record_number: u64,
    pub line_start: u64,
    pub line_end: u64,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FolderRefreshAlternative {
    pub bar: Bar,
    pub sources: Vec<FolderRefreshSource>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FolderRefreshBar {
    pub time_ms: Timestamp,
    pub status: FolderRefreshBarStatus,
    pub accepted: Option<AcceptedFolderRefreshBar>,
    pub alternatives: Vec<FolderRefreshAlternative>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PreviewLocalFolderRefreshInput {
    pub version: u64,
    pub folder_id: Uuid,
    pub revision_id: Uuid,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PreviewLocalFolderRefreshOutput {
    pub version: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub base: Option<PublishedLocalImportRevision>,
    pub files: Vec<FolderRefreshFile>,
    pub bars: Vec<FolderRefreshBar>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub folder_base: Option<PublishedLocalFolderRevision>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub review_id: Option<Hash>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub publication_allowed: Option<bool>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum PreviewLocalFolderRefreshReply11 {
    PreviewLocalFolderRefreshResponse(Box<PreviewLocalFolderRefreshResponse>),
    PreviewLocalFolderRefreshError(Box<PreviewLocalFolderRefreshError>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FolderPublicationConflictChoice {
    pub time_ms: Timestamp,
    pub alternative_index: u64,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PublishedLocalFolderRevision {
    pub revision_id: Uuid,
    pub base_revision_id: Uuid,
    pub parent_revision_id: Option<Uuid>,
    pub origin_path: String,
    pub symbol: String,
    pub series: PreviewSeries,
    pub source_count: u64,
    pub bar_count: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub bar_summary: Option<LocalImportRevisionBarSummary>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PublishedLocalFolderSource {
    pub relative_path: String,
    pub source_sha256: Hash,
    pub record_count: u64,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrepareLocalFolderPublicationInput {
    pub version: u64,
    pub folder_id: Uuid,
    pub review_id: Hash,
    pub conflicts: Vec<FolderPublicationConflictChoice>,
    pub withdraw_bars: Vec<Timestamp>,
    pub withdraw_sources: Vec<String>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrepareLocalFolderPublicationOutput {
    pub version: u64,
    pub review_id: Hash,
    pub candidate_sha256: Hash,
    pub parent_revision_id: Uuid,
    pub source_count: u64,
    pub bar_count: u64,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PublishLocalFolderRefreshInput {
    pub version: u64,
    pub folder_id: Uuid,
    pub review_id: Hash,
    pub candidate_sha256: Hash,
    pub idempotency_key: Uuid,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PublishLocalFolderRefreshOutput {
    pub version: u64,
    pub revision: PublishedLocalFolderRevision,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ListLocalFolderRevisionsInput {
    pub version: u64,
    pub after_revision_id: Option<CanonicalRevisionCursor>,
    pub limit: u64,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ListLocalFolderRevisionsOutput {
    pub version: u64,
    pub revisions: Vec<PublishedLocalFolderRevision>,
    pub next_after_revision_id: Option<CanonicalRevisionCursor>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReadLocalFolderRevisionInput {
    pub version: u64,
    pub revision_id: Uuid,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub include_details: Option<bool>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReadLocalFolderRevisionOutput {
    pub version: u64,
    pub revision: PublishedLocalFolderRevision,
    pub files: Vec<PublishedLocalFolderSource>,
    pub bars: Vec<FolderRefreshAlternative>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RevokeLocalFolderRevisionInput {
    pub version: u64,
    pub revision_id: Uuid,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RevokeLocalFolderRevisionOutput {
    pub version: u64,
    pub revision_id: Uuid,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum PrepareLocalFolderPublicationReply11 {
    PrepareLocalFolderPublicationResponse(Box<PrepareLocalFolderPublicationResponse>),
    PrepareLocalFolderPublicationError(Box<PrepareLocalFolderPublicationError>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum PublishLocalFolderRefreshReply11 {
    PublishLocalFolderRefreshResponse(Box<PublishLocalFolderRefreshResponse>),
    PublishLocalFolderRefreshError(Box<PublishLocalFolderRefreshError>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum ListLocalFolderRevisionsReply11 {
    ListLocalFolderRevisionsResponse(Box<ListLocalFolderRevisionsResponse>),
    ListLocalFolderRevisionsError(Box<ListLocalFolderRevisionsError>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum ReadLocalFolderRevisionReply11 {
    ReadLocalFolderRevisionResponse(Box<ReadLocalFolderRevisionResponse>),
    ReadLocalFolderRevisionError(Box<ReadLocalFolderRevisionError>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReadLocalFolderSourceRowInput {
    pub version: u64,
    pub revision_id: Uuid,
    pub source: FolderRefreshSource,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReadLocalFolderSourceRowOutput {
    pub version: u64,
    pub revision_id: Uuid,
    pub source: FolderRefreshSource,
    pub header: Vec<String>,
    pub raw_fields: Vec<String>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum ReadLocalFolderSourceRowReply11 {
    ReadLocalFolderSourceRowResponse(Box<ReadLocalFolderSourceRowResponse>),
    ReadLocalFolderSourceRowError(Box<ReadLocalFolderSourceRowError>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum RevokeLocalFolderRevisionReply11 {
    RevokeLocalFolderRevisionResponse(Box<RevokeLocalFolderRevisionResponse>),
    RevokeLocalFolderRevisionError(Box<RevokeLocalFolderRevisionError>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum AcceptedFolderRefreshBar {
    PublishedLocalImportBar(Box<PublishedLocalImportBar>),
    FolderRefreshAlternative(Box<FolderRefreshAlternative>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LayoutPanel11 {
    pub panel: LayoutPanel11Panel,
    pub dock: LayoutPanel11Dock,
    pub size: u64,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceLayout11 {
    pub version: u64,
    pub panels: Vec<LayoutPanel11>,
    pub theme: WorkspaceLayout11Theme,
    pub gain_color: String,
    pub loss_color: String,
    pub link_selection: bool,
    pub pinned_instrument: Option<OpaqueId>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub authored_view: Option<SavedSeriesDrawing11>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NamedWorkspaceLayout11 {
    pub name: Option<String>,
    pub definition: WorkspaceLayout11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SaveWorkspaceLayout11 {
    pub action: SaveWorkspaceLayout11Action,
    pub name: String,
    pub definition: WorkspaceLayout11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OpenWorkspaceLayout11 {
    pub action: OpenWorkspaceLayout11Action,
    pub name: String,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ResetWorkspaceLayout11 {
    pub action: ResetWorkspaceLayout11Action,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum WorkspaceLayoutEdit11 {
    SaveWorkspaceLayout11(Box<SaveWorkspaceLayout11>),
    OpenWorkspaceLayout11(Box<OpenWorkspaceLayout11>),
    ResetWorkspaceLayout11(Box<ResetWorkspaceLayout11>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspacePreviewLayoutInput11 {
    pub edit: WorkspaceLayoutEdit11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspacePreviewLayoutOutput11 {
    pub layout: NamedWorkspaceLayout11,
    pub preview_token: String,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceApplyLayoutInput11 {
    pub edit: WorkspaceLayoutEdit11,
    pub preview_token: String,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspacePreviewLayoutCommand11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: WorkspacePreviewLayoutCommand11Type,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub scope: MutationScope11,
    pub intent: WorkspacePreviewLayoutIntent11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum WorkspacePreviewLayoutOutcome11 {
    WorkspacePreviewLayoutSuccess11(Box<WorkspacePreviewLayoutSuccess11>),
    ScopedFailure11(Box<ScopedFailure11>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspacePreviewLayoutReply11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: WorkspacePreviewLayoutReply11Type,
    pub request_id: Uuid,
    pub outcome: WorkspacePreviewLayoutOutcome11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceApplyLayoutCommand11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: WorkspaceApplyLayoutCommand11Type,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub scope: MutationScope11,
    pub intent: WorkspaceApplyLayoutIntent11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum WorkspaceApplyLayoutOutcome11 {
    WorkspaceApplyLayoutSuccess11(Box<WorkspaceApplyLayoutSuccess11>),
    ScopedFailure11(Box<ScopedFailure11>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceApplyLayoutReply11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: WorkspaceApplyLayoutReply11Type,
    pub request_id: Uuid,
    pub outcome: WorkspaceApplyLayoutOutcome11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AiTextOllamaChoice {
    pub kind: AiTextOllamaChoiceKind,
    pub origin: String,
    pub model: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub chart_tools: Option<ConstBool<true>>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AiTextOfflineMockChoice {
    pub kind: AiTextOfflineMockChoiceKind,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AiTextOpenAiChoice {
    pub kind: AiTextOpenAiChoiceKind,
    pub model: String,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum AiTextRouteChoice {
    AiTextOllamaChoice(Box<AiTextOllamaChoice>),
    AiTextOfflineMockChoice(Box<AiTextOfflineMockChoice>),
    AiTextOpenAiChoice(Box<AiTextOpenAiChoice>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AiTextOllamaRoute {
    pub kind: AiTextOllamaRouteKind,
    pub destination: String,
    pub model: String,
    pub scope: AiTextOllamaRouteScope,
    pub charge_basis: AiTextOllamaRouteChargeBasis,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AiTextOfflineMockRoute {
    pub kind: AiTextOfflineMockRouteKind,
    pub destination: AiTextOfflineMockRouteDestination,
    pub model: AiTextOfflineMockRouteModel,
    pub scope: AiTextOfflineMockRouteScope,
    pub charge_basis: AiTextOfflineMockRouteChargeBasis,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AiTextOpenAiRoute {
    pub kind: AiTextOpenAiRouteKind,
    pub destination: AiTextOpenAiRouteDestination,
    pub model: String,
    pub scope: AiTextOpenAiRouteScope,
    pub charge_basis: AiTextOpenAiRouteChargeBasis,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum AiTextRoute {
    AiTextOllamaRoute(Box<AiTextOllamaRoute>),
    AiTextOfflineMockRoute(Box<AiTextOfflineMockRoute>),
    AiTextOpenAiRoute(Box<AiTextOpenAiRoute>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AiTextGrant {
    pub connection_id: Uuid,
    pub route: AiTextRoute,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AiTextRunningTurn {
    pub state: AiTextRunningTurnState,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AiTextCompletedTurn {
    pub state: AiTextCompletedTurnState,
    pub model: String,
    pub text: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub chart_evidence: Option<AiTextChartEvidence>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AiTextFailedTurn {
    pub state: AiTextFailedTurnState,
    pub error: WorkspaceFault11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum AiTextTurn {
    AiTextRunningTurn(Box<AiTextRunningTurn>),
    AiTextCompletedTurn(Box<AiTextCompletedTurn>),
    AiTextFailedTurn(Box<AiTextFailedTurn>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AiTextStatusInput {
    pub action: AiTextStatusInputAction,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AiTextProposeInput {
    pub action: AiTextProposeInputAction,
    pub route: AiTextRouteChoice,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AiTextActivateInput {
    pub action: AiTextActivateInputAction,
    pub proposal_id: Uuid,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AiTextDisconnectInput {
    pub action: AiTextDisconnectInputAction,
    pub connection_id: Uuid,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AiTextSendInput {
    pub action: AiTextSendInputAction,
    pub connection_id: Uuid,
    pub question: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub selection_revision: Option<Revision>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AiTextPollInput {
    pub action: AiTextPollInputAction,
    pub connection_id: Uuid,
    pub turn_id: Uuid,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AiTextCancelInput {
    pub action: AiTextCancelInputAction,
    pub connection_id: Uuid,
    pub turn_id: Uuid,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum AiTextConnectionInput {
    AiTextStatusInput(Box<AiTextStatusInput>),
    AiTextProposeInput(Box<AiTextProposeInput>),
    AiTextActivateInput(Box<AiTextActivateInput>),
    AiTextDisconnectInput(Box<AiTextDisconnectInput>),
    AiTextSendInput(Box<AiTextSendInput>),
    AiTextPollInput(Box<AiTextPollInput>),
    AiTextCancelInput(Box<AiTextCancelInput>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AiTextStatusOutput {
    pub action: AiTextStatusOutputAction,
    pub connection: Option<AiTextGrant>,
    pub turn_id: Option<Uuid>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AiTextProposeOutput {
    pub action: AiTextProposeOutputAction,
    pub proposal_id: Uuid,
    pub route: AiTextRoute,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AiTextActivateOutput {
    pub action: AiTextActivateOutputAction,
    pub connection: AiTextGrant,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AiTextDisconnectOutput {
    pub action: AiTextDisconnectOutputAction,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AiTextSendOutput {
    pub action: AiTextSendOutputAction,
    pub turn_id: Uuid,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AiTextPollOutput {
    pub action: AiTextPollOutputAction,
    pub turn_id: Uuid,
    pub turn: AiTextTurn,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AiTextCancelOutput {
    pub action: AiTextCancelOutputAction,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum AiTextConnectionOutput {
    AiTextStatusOutput(Box<AiTextStatusOutput>),
    AiTextProposeOutput(Box<AiTextProposeOutput>),
    AiTextActivateOutput(Box<AiTextActivateOutput>),
    AiTextDisconnectOutput(Box<AiTextDisconnectOutput>),
    AiTextSendOutput(Box<AiTextSendOutput>),
    AiTextPollOutput(Box<AiTextPollOutput>),
    AiTextCancelOutput(Box<AiTextCancelOutput>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AiTextConnectionCommand11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: AiTextConnectionCommand11Type,
    pub request_id: Uuid,
    pub scope: AiTextConnectionScope11,
    pub intent: AiTextConnectionIntent11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AiTextConnectionReply11 {
    pub protocol_version: ProtocolVersion11,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: AiTextConnectionReply11Type,
    pub request_id: Uuid,
    pub outcome: AiTextConnectionOutcome11,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AiTextChartClose {
    pub time_ms: Timestamp,
    pub close: f64,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AiTextChartEvidence {
    pub source_reference: OpaqueId,
    pub revision: OpaqueId,
    pub range: VisibleRange,
    pub rows: Vec<AiTextChartClose>,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AiTextConnectionScope11 {
    pub workspace_id: OpaqueId,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AiTextConnectionIntent11 {
    pub operation: AiTextConnectionIntent11Operation,
    pub arguments: AiTextConnectionInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AiTextConnectionCompleted11 {
    pub state: AiTextConnectionCompleted11State,
    pub operation: AiTextConnectionCompleted11Operation,
    pub result: AiTextConnectionOutput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum AiTextConnectionOutcome11 {
    AiTextConnectionCompleted11(Box<AiTextConnectionCompleted11>),
    ScopedFailure11(Box<ScopedFailure11>),
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SavedSeriesDrawing11 {
    pub api: SavedSeriesDrawing11Api,
    pub source: String,
    pub stroke: u64,
    pub scalars: Vec<f64>,
    pub dataset_id: Uuid,
    pub dataset_revision: OpaqueId,
    pub layer_id: Uuid,
    pub version_id: Uuid,
    pub series_id: OpaqueId,
    pub result_hash: Hash,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum FieldExpressionOp {
    #[serde(rename = "field")]
    Value,
}
impl FieldExpressionOp {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "field",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum FieldExpressionField {
    #[serde(rename = "open")]
    Open,
    #[serde(rename = "high")]
    High,
    #[serde(rename = "low")]
    Low,
    #[serde(rename = "close")]
    Close,
    #[serde(rename = "volume")]
    Volume,
}
impl FieldExpressionField {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Open => "open",
            Self::High => "high",
            Self::Low => "low",
            Self::Close => "close",
            Self::Volume => "volume",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ConstantExpressionOp {
    #[serde(rename = "constant")]
    Value,
}
impl ConstantExpressionOp {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "constant",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum BinaryExpressionOp {
    #[serde(rename = "add")]
    Add,
    #[serde(rename = "subtract")]
    Subtract,
    #[serde(rename = "multiply")]
    Multiply,
    #[serde(rename = "divide")]
    Divide,
    #[serde(rename = "gt")]
    Gt,
    #[serde(rename = "lt")]
    Lt,
    #[serde(rename = "and")]
    And,
    #[serde(rename = "or")]
    Or,
}
impl BinaryExpressionOp {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Add => "add",
            Self::Subtract => "subtract",
            Self::Multiply => "multiply",
            Self::Divide => "divide",
            Self::Gt => "gt",
            Self::Lt => "lt",
            Self::And => "and",
            Self::Or => "or",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AbsExpressionOp {
    #[serde(rename = "abs")]
    Value,
}
impl AbsExpressionOp {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "abs",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum LagExpressionOp {
    #[serde(rename = "lag")]
    Value,
}
impl LagExpressionOp {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "lag",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum WindowExpressionOp {
    #[serde(rename = "rolling_mean")]
    RollingMean,
    #[serde(rename = "rolling_std")]
    RollingStd,
}
impl WindowExpressionOp {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::RollingMean => "rolling_mean",
            Self::RollingStd => "rolling_std",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DeclarativeDraftInputLanguage {
    #[serde(rename = "declarative")]
    Value,
}
impl DeclarativeDraftInputLanguage {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "declarative",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PythonDraftInputLanguage {
    #[serde(rename = "python")]
    Value,
}
impl PythonDraftInputLanguage {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "python",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum InstrumentSearchRequestType {
    #[serde(rename = "request")]
    Value,
}
impl InstrumentSearchRequestType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum InstrumentSearchRequestOperation {
    #[serde(rename = "instrument.search")]
    Value,
}
impl InstrumentSearchRequestOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "instrument.search",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum InstrumentSearchToolCallOperation {
    #[serde(rename = "instrument.search")]
    Value,
}
impl InstrumentSearchToolCallOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "instrument.search",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum MarketGetBarsRequestType {
    #[serde(rename = "request")]
    Value,
}
impl MarketGetBarsRequestType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum MarketGetBarsRequestOperation {
    #[serde(rename = "market.getBars")]
    Value,
}
impl MarketGetBarsRequestOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.getBars",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum MarketGetBarsToolCallOperation {
    #[serde(rename = "market.getBars")]
    Value,
}
impl MarketGetBarsToolCallOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.getBars",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum WorkspaceOpenInstrumentRequestType {
    #[serde(rename = "request")]
    Value,
}
impl WorkspaceOpenInstrumentRequestType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum WorkspaceOpenInstrumentRequestOperation {
    #[serde(rename = "workspace.openInstrument")]
    Value,
}
impl WorkspaceOpenInstrumentRequestOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "workspace.openInstrument",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum WorkspaceOpenInstrumentToolCallOperation {
    #[serde(rename = "workspace.openInstrument")]
    Value,
}
impl WorkspaceOpenInstrumentToolCallOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "workspace.openInstrument",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ChartSetTimeframeRequestType {
    #[serde(rename = "request")]
    Value,
}
impl ChartSetTimeframeRequestType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ChartSetTimeframeRequestOperation {
    #[serde(rename = "chart.setTimeframe")]
    Value,
}
impl ChartSetTimeframeRequestOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "chart.setTimeframe",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ChartSetTimeframeToolCallOperation {
    #[serde(rename = "chart.setTimeframe")]
    Value,
}
impl ChartSetTimeframeToolCallOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "chart.setTimeframe",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ChartSetStyleRequestType {
    #[serde(rename = "request")]
    Value,
}
impl ChartSetStyleRequestType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ChartSetStyleRequestOperation {
    #[serde(rename = "chart.setStyle")]
    Value,
}
impl ChartSetStyleRequestOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "chart.setStyle",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ChartSetStyleToolCallOperation {
    #[serde(rename = "chart.setStyle")]
    Value,
}
impl ChartSetStyleToolCallOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "chart.setStyle",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ChartAddAnnotationRequestType {
    #[serde(rename = "request")]
    Value,
}
impl ChartAddAnnotationRequestType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ChartAddAnnotationRequestOperation {
    #[serde(rename = "chart.addAnnotation")]
    Value,
}
impl ChartAddAnnotationRequestOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "chart.addAnnotation",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ChartAddAnnotationToolCallOperation {
    #[serde(rename = "chart.addAnnotation")]
    Value,
}
impl ChartAddAnnotationToolCallOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "chart.addAnnotation",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ChartRemoveLayerRequestType {
    #[serde(rename = "request")]
    Value,
}
impl ChartRemoveLayerRequestType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ChartRemoveLayerRequestOperation {
    #[serde(rename = "chart.removeLayer")]
    Value,
}
impl ChartRemoveLayerRequestOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "chart.removeLayer",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ChartRemoveLayerToolCallOperation {
    #[serde(rename = "chart.removeLayer")]
    Value,
}
impl ChartRemoveLayerToolCallOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "chart.removeLayer",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum IndicatorCreateDraftRequestType {
    #[serde(rename = "request")]
    Value,
}
impl IndicatorCreateDraftRequestType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum IndicatorCreateDraftRequestOperation {
    #[serde(rename = "indicator.createDraft")]
    Value,
}
impl IndicatorCreateDraftRequestOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "indicator.createDraft",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum IndicatorCreateDraftToolCallOperation {
    #[serde(rename = "indicator.createDraft")]
    Value,
}
impl IndicatorCreateDraftToolCallOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "indicator.createDraft",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum IndicatorRunPreviewRequestType {
    #[serde(rename = "request")]
    Value,
}
impl IndicatorRunPreviewRequestType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum IndicatorRunPreviewRequestOperation {
    #[serde(rename = "indicator.runPreview")]
    Value,
}
impl IndicatorRunPreviewRequestOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "indicator.runPreview",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum IndicatorRunPreviewToolCallOperation {
    #[serde(rename = "indicator.runPreview")]
    Value,
}
impl IndicatorRunPreviewToolCallOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "indicator.runPreview",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum IndicatorAcceptVersionRequestType {
    #[serde(rename = "request")]
    Value,
}
impl IndicatorAcceptVersionRequestType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum IndicatorAcceptVersionRequestOperation {
    #[serde(rename = "indicator.acceptVersion")]
    Value,
}
impl IndicatorAcceptVersionRequestOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "indicator.acceptVersion",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum IndicatorAcceptVersionToolCallOperation {
    #[serde(rename = "indicator.acceptVersion")]
    Value,
}
impl IndicatorAcceptVersionToolCallOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "indicator.acceptVersion",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum IndicatorRemoveRequestType {
    #[serde(rename = "request")]
    Value,
}
impl IndicatorRemoveRequestType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum IndicatorRemoveRequestOperation {
    #[serde(rename = "indicator.remove")]
    Value,
}
impl IndicatorRemoveRequestOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "indicator.remove",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum IndicatorRemoveToolCallOperation {
    #[serde(rename = "indicator.remove")]
    Value,
}
impl IndicatorRemoveToolCallOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "indicator.remove",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum WorkspaceUndoRequestType {
    #[serde(rename = "request")]
    Value,
}
impl WorkspaceUndoRequestType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum WorkspaceUndoRequestOperation {
    #[serde(rename = "workspace.undo")]
    Value,
}
impl WorkspaceUndoRequestOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "workspace.undo",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum WorkspaceUndoToolCallOperation {
    #[serde(rename = "workspace.undo")]
    Value,
}
impl WorkspaceUndoToolCallOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "workspace.undo",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum TaskCancelOutputStatus {
    #[serde(rename = "completed")]
    Completed,
    #[serde(rename = "failed")]
    Failed,
    #[serde(rename = "cancelled")]
    Cancelled,
}
impl TaskCancelOutputStatus {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Completed => "completed",
            Self::Failed => "failed",
            Self::Cancelled => "cancelled",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum TaskCancelRequestType {
    #[serde(rename = "request")]
    Value,
}
impl TaskCancelRequestType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum TaskCancelRequestOperation {
    #[serde(rename = "task.cancel")]
    Value,
}
impl TaskCancelRequestOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "task.cancel",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum TaskCancelToolCallOperation {
    #[serde(rename = "task.cancel")]
    Value,
}
impl TaskCancelToolCallOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "task.cancel",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum WorkspaceGetContextRequestType {
    #[serde(rename = "request")]
    Value,
}
impl WorkspaceGetContextRequestType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum WorkspaceGetContextRequestOperation {
    #[serde(rename = "workspace.getContext")]
    Value,
}
impl WorkspaceGetContextRequestOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "workspace.getContext",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum WorkspaceGetContextToolCallOperation {
    #[serde(rename = "workspace.getContext")]
    Value,
}
impl WorkspaceGetContextToolCallOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "workspace.getContext",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum HelloType {
    #[serde(rename = "hello")]
    Value,
}
impl HelloType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "hello",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ErrorResponseType {
    #[serde(rename = "response")]
    Value,
}
impl ErrorResponseType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum SuccessResponseType {
    #[serde(rename = "response")]
    Value,
}
impl SuccessResponseType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AiToolRequestEventType {
    #[serde(rename = "event")]
    Value,
}
impl AiToolRequestEventType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "event",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AiToolRequestEventEvent {
    #[serde(rename = "ai.tool_request")]
    Value,
}
impl AiToolRequestEventEvent {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "ai.tool_request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum CancelType {
    #[serde(rename = "cancel")]
    Value,
}
impl CancelType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "cancel",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum HeartbeatType {
    #[serde(rename = "heartbeat")]
    Value,
}
impl HeartbeatType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "heartbeat",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum MarketQualityStateSessionState {
    #[serde(rename = "open")]
    Open,
    #[serde(rename = "closed")]
    Closed,
    #[serde(rename = "unknown")]
    Unknown,
}
impl MarketQualityStateSessionState {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Open => "open",
            Self::Closed => "closed",
            Self::Unknown => "unknown",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum MarketQualityStateFreshness {
    #[serde(rename = "current")]
    Current,
    #[serde(rename = "delayed")]
    Delayed,
    #[serde(rename = "stale")]
    Stale,
    #[serde(rename = "unknown")]
    Unknown,
}
impl MarketQualityStateFreshness {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Current => "current",
            Self::Delayed => "delayed",
            Self::Stale => "stale",
            Self::Unknown => "unknown",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum MarketQualityStateConnection {
    #[serde(rename = "offline")]
    Offline,
    #[serde(rename = "connected")]
    Connected,
    #[serde(rename = "disconnected")]
    Disconnected,
}
impl MarketQualityStateConnection {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Offline => "offline",
            Self::Connected => "connected",
            Self::Disconnected => "disconnected",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum MarketQualityStateCoverage {
    #[serde(rename = "observed")]
    Observed,
    #[serde(rename = "gapped")]
    Gapped,
    #[serde(rename = "unknown")]
    Unknown,
}
impl MarketQualityStateCoverage {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Observed => "observed",
            Self::Gapped => "gapped",
            Self::Unknown => "unknown",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum MarketQualityStateSourceKind {
    #[serde(rename = "native_bars")]
    NativeBars,
    #[serde(rename = "snapshot_approximation")]
    SnapshotApproximation,
    #[serde(rename = "synthetic")]
    Synthetic,
}
impl MarketQualityStateSourceKind {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::NativeBars => "native_bars",
            Self::SnapshotApproximation => "snapshot_approximation",
            Self::Synthetic => "synthetic",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum MarketHistoryPageRequestProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl MarketHistoryPageRequestProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum MarketHistoryPageRequestType {
    #[serde(rename = "request")]
    Value,
}
impl MarketHistoryPageRequestType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum MarketHistoryPageRequestOperation {
    #[serde(rename = "market.readHistoryPage")]
    Value,
}
impl MarketHistoryPageRequestOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.readHistoryPage",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum MarketHistoryPageResponseProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl MarketHistoryPageResponseProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum MarketHistoryPageResponseType {
    #[serde(rename = "response")]
    Value,
}
impl MarketHistoryPageResponseType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum MarketHistoryPageResponseOperation {
    #[serde(rename = "market.readHistoryPage")]
    Value,
}
impl MarketHistoryPageResponseOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.readHistoryPage",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum MarketHistoryPageErrorProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl MarketHistoryPageErrorProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum MarketHistoryPageErrorType {
    #[serde(rename = "response")]
    Value,
}
impl MarketHistoryPageErrorType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum MarketHistoryPageErrorOperation {
    #[serde(rename = "market.readHistoryPage")]
    Value,
}
impl MarketHistoryPageErrorOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.readHistoryPage",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum StreamResumeAcceptedStatus {
    #[serde(rename = "accepted")]
    Value,
}
impl StreamResumeAcceptedStatus {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "accepted",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum StreamBackfillRequiredStatus {
    #[serde(rename = "backfill_required")]
    Value,
}
impl StreamBackfillRequiredStatus {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "backfill_required",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum StreamBackfillRequiredReason {
    #[serde(rename = "epoch_changed")]
    EpochChanged,
    #[serde(rename = "history_evicted")]
    HistoryEvicted,
    #[serde(rename = "coverage_unknown")]
    CoverageUnknown,
    #[serde(rename = "sequence_gap")]
    SequenceGap,
}
impl StreamBackfillRequiredReason {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::EpochChanged => "epoch_changed",
            Self::HistoryEvicted => "history_evicted",
            Self::CoverageUnknown => "coverage_unknown",
            Self::SequenceGap => "sequence_gap",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum MarketSubscriptionRequestProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl MarketSubscriptionRequestProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum MarketSubscriptionRequestType {
    #[serde(rename = "request")]
    Value,
}
impl MarketSubscriptionRequestType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum MarketSubscriptionRequestOperation {
    #[serde(rename = "market.subscribe")]
    Value,
}
impl MarketSubscriptionRequestOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.subscribe",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum MarketSubscriptionResponseProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl MarketSubscriptionResponseProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum MarketSubscriptionResponseType {
    #[serde(rename = "response")]
    Value,
}
impl MarketSubscriptionResponseType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum MarketSubscriptionResponseOperation {
    #[serde(rename = "market.subscribe")]
    Value,
}
impl MarketSubscriptionResponseOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.subscribe",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum MarketSubscriptionErrorProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl MarketSubscriptionErrorProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum MarketSubscriptionErrorType {
    #[serde(rename = "response")]
    Value,
}
impl MarketSubscriptionErrorType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum MarketSubscriptionErrorOperation {
    #[serde(rename = "market.subscribe")]
    Value,
}
impl MarketSubscriptionErrorOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.subscribe",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum MarketStreamGapReason {
    #[serde(rename = "sequence_gap")]
    SequenceGap,
    #[serde(rename = "overflow")]
    Overflow,
    #[serde(rename = "epoch_reset")]
    EpochReset,
    #[serde(rename = "source_gap")]
    SourceGap,
}
impl MarketStreamGapReason {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::SequenceGap => "sequence_gap",
            Self::Overflow => "overflow",
            Self::EpochReset => "epoch_reset",
            Self::SourceGap => "source_gap",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum MarketBarUpdateEventProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl MarketBarUpdateEventProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum MarketBarUpdateEventType {
    #[serde(rename = "event")]
    Value,
}
impl MarketBarUpdateEventType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "event",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum MarketBarUpdateEventEvent {
    #[serde(rename = "market.bar_update")]
    Value,
}
impl MarketBarUpdateEventEvent {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.bar_update",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum MarketBarCorrectionEventProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl MarketBarCorrectionEventProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum MarketBarCorrectionEventType {
    #[serde(rename = "event")]
    Value,
}
impl MarketBarCorrectionEventType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "event",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum MarketBarCorrectionEventEvent {
    #[serde(rename = "market.bar_correction")]
    Value,
}
impl MarketBarCorrectionEventEvent {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.bar_correction",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum MarketStreamGapEventProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl MarketStreamGapEventProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum MarketStreamGapEventType {
    #[serde(rename = "event")]
    Value,
}
impl MarketStreamGapEventType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "event",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum MarketStreamGapEventEvent {
    #[serde(rename = "market.stream_gap")]
    Value,
}
impl MarketStreamGapEventEvent {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.stream_gap",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum LocalImportPreviewRequestProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl LocalImportPreviewRequestProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum LocalImportPreviewRequestType {
    #[serde(rename = "request")]
    Value,
}
impl LocalImportPreviewRequestType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum LocalImportPreviewRequestOperation {
    #[serde(rename = "market.previewLocalImport")]
    Value,
}
impl LocalImportPreviewRequestOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.previewLocalImport",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum LocalImportPreviewResponseProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl LocalImportPreviewResponseProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum LocalImportPreviewResponseType {
    #[serde(rename = "response")]
    Value,
}
impl LocalImportPreviewResponseType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum LocalImportPreviewResponseOperation {
    #[serde(rename = "market.previewLocalImport")]
    Value,
}
impl LocalImportPreviewResponseOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.previewLocalImport",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum LocalImportPreviewErrorProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl LocalImportPreviewErrorProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum LocalImportPreviewErrorType {
    #[serde(rename = "response")]
    Value,
}
impl LocalImportPreviewErrorType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum LocalImportPreviewErrorOperation {
    #[serde(rename = "market.previewLocalImport")]
    Value,
}
impl LocalImportPreviewErrorOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.previewLocalImport",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum SaveLocalImportMappingRequestProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl SaveLocalImportMappingRequestProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum SaveLocalImportMappingRequestType {
    #[serde(rename = "request")]
    Value,
}
impl SaveLocalImportMappingRequestType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum SaveLocalImportMappingRequestOperation {
    #[serde(rename = "market.saveLocalImportMapping")]
    Value,
}
impl SaveLocalImportMappingRequestOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.saveLocalImportMapping",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum SaveLocalImportMappingResponseProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl SaveLocalImportMappingResponseProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum SaveLocalImportMappingResponseType {
    #[serde(rename = "response")]
    Value,
}
impl SaveLocalImportMappingResponseType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum SaveLocalImportMappingResponseOperation {
    #[serde(rename = "market.saveLocalImportMapping")]
    Value,
}
impl SaveLocalImportMappingResponseOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.saveLocalImportMapping",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum SaveLocalImportMappingErrorProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl SaveLocalImportMappingErrorProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum SaveLocalImportMappingErrorType {
    #[serde(rename = "response")]
    Value,
}
impl SaveLocalImportMappingErrorType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum SaveLocalImportMappingErrorOperation {
    #[serde(rename = "market.saveLocalImportMapping")]
    Value,
}
impl SaveLocalImportMappingErrorOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.saveLocalImportMapping",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum LoadLocalImportMappingRequestProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl LoadLocalImportMappingRequestProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum LoadLocalImportMappingRequestType {
    #[serde(rename = "request")]
    Value,
}
impl LoadLocalImportMappingRequestType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum LoadLocalImportMappingRequestOperation {
    #[serde(rename = "market.loadLocalImportMapping")]
    Value,
}
impl LoadLocalImportMappingRequestOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.loadLocalImportMapping",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum LoadLocalImportMappingResponseProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl LoadLocalImportMappingResponseProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum LoadLocalImportMappingResponseType {
    #[serde(rename = "response")]
    Value,
}
impl LoadLocalImportMappingResponseType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum LoadLocalImportMappingResponseOperation {
    #[serde(rename = "market.loadLocalImportMapping")]
    Value,
}
impl LoadLocalImportMappingResponseOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.loadLocalImportMapping",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum LoadLocalImportMappingErrorProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl LoadLocalImportMappingErrorProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum LoadLocalImportMappingErrorType {
    #[serde(rename = "response")]
    Value,
}
impl LoadLocalImportMappingErrorType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum LoadLocalImportMappingErrorOperation {
    #[serde(rename = "market.loadLocalImportMapping")]
    Value,
}
impl LoadLocalImportMappingErrorOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.loadLocalImportMapping",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ListLocalImportRevisionsRequestProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl ListLocalImportRevisionsRequestProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ListLocalImportRevisionsRequestType {
    #[serde(rename = "request")]
    Value,
}
impl ListLocalImportRevisionsRequestType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ListLocalImportRevisionsRequestOperation {
    #[serde(rename = "market.listLocalImportRevisions")]
    Value,
}
impl ListLocalImportRevisionsRequestOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.listLocalImportRevisions",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ListLocalImportRevisionsResponseProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl ListLocalImportRevisionsResponseProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ListLocalImportRevisionsResponseType {
    #[serde(rename = "response")]
    Value,
}
impl ListLocalImportRevisionsResponseType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ListLocalImportRevisionsResponseOperation {
    #[serde(rename = "market.listLocalImportRevisions")]
    Value,
}
impl ListLocalImportRevisionsResponseOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.listLocalImportRevisions",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ListLocalImportRevisionsErrorProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl ListLocalImportRevisionsErrorProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ListLocalImportRevisionsErrorType {
    #[serde(rename = "response")]
    Value,
}
impl ListLocalImportRevisionsErrorType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ListLocalImportRevisionsErrorOperation {
    #[serde(rename = "market.listLocalImportRevisions")]
    Value,
}
impl ListLocalImportRevisionsErrorOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.listLocalImportRevisions",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ReadLocalImportRevisionRequestProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl ReadLocalImportRevisionRequestProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ReadLocalImportRevisionRequestType {
    #[serde(rename = "request")]
    Value,
}
impl ReadLocalImportRevisionRequestType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ReadLocalImportRevisionRequestOperation {
    #[serde(rename = "market.readLocalImportRevision")]
    Value,
}
impl ReadLocalImportRevisionRequestOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.readLocalImportRevision",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ReadLocalImportRevisionResponseProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl ReadLocalImportRevisionResponseProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ReadLocalImportRevisionResponseType {
    #[serde(rename = "response")]
    Value,
}
impl ReadLocalImportRevisionResponseType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ReadLocalImportRevisionResponseOperation {
    #[serde(rename = "market.readLocalImportRevision")]
    Value,
}
impl ReadLocalImportRevisionResponseOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.readLocalImportRevision",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ReadLocalImportRevisionErrorProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl ReadLocalImportRevisionErrorProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ReadLocalImportRevisionErrorType {
    #[serde(rename = "response")]
    Value,
}
impl ReadLocalImportRevisionErrorType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ReadLocalImportRevisionErrorOperation {
    #[serde(rename = "market.readLocalImportRevision")]
    Value,
}
impl ReadLocalImportRevisionErrorOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.readLocalImportRevision",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ReadLocalImportSourceRowRequestProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl ReadLocalImportSourceRowRequestProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ReadLocalImportSourceRowRequestType {
    #[serde(rename = "request")]
    Value,
}
impl ReadLocalImportSourceRowRequestType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ReadLocalImportSourceRowRequestOperation {
    #[serde(rename = "market.readLocalImportSourceRow")]
    Value,
}
impl ReadLocalImportSourceRowRequestOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.readLocalImportSourceRow",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ReadLocalImportSourceRowResponseProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl ReadLocalImportSourceRowResponseProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ReadLocalImportSourceRowResponseType {
    #[serde(rename = "response")]
    Value,
}
impl ReadLocalImportSourceRowResponseType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ReadLocalImportSourceRowResponseOperation {
    #[serde(rename = "market.readLocalImportSourceRow")]
    Value,
}
impl ReadLocalImportSourceRowResponseOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.readLocalImportSourceRow",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ReadLocalImportSourceRowErrorProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl ReadLocalImportSourceRowErrorProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ReadLocalImportSourceRowErrorType {
    #[serde(rename = "response")]
    Value,
}
impl ReadLocalImportSourceRowErrorType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ReadLocalImportSourceRowErrorOperation {
    #[serde(rename = "market.readLocalImportSourceRow")]
    Value,
}
impl ReadLocalImportSourceRowErrorOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.readLocalImportSourceRow",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PreviewLocalFolderRefreshRequestProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl PreviewLocalFolderRefreshRequestProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PreviewLocalFolderRefreshRequestType {
    #[serde(rename = "request")]
    Value,
}
impl PreviewLocalFolderRefreshRequestType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PreviewLocalFolderRefreshRequestOperation {
    #[serde(rename = "market.previewLocalFolderRefresh")]
    Value,
}
impl PreviewLocalFolderRefreshRequestOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.previewLocalFolderRefresh",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PreviewLocalFolderRefreshResponseProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl PreviewLocalFolderRefreshResponseProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PreviewLocalFolderRefreshResponseType {
    #[serde(rename = "response")]
    Value,
}
impl PreviewLocalFolderRefreshResponseType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PreviewLocalFolderRefreshResponseOperation {
    #[serde(rename = "market.previewLocalFolderRefresh")]
    Value,
}
impl PreviewLocalFolderRefreshResponseOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.previewLocalFolderRefresh",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PreviewLocalFolderRefreshErrorProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl PreviewLocalFolderRefreshErrorProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PreviewLocalFolderRefreshErrorType {
    #[serde(rename = "response")]
    Value,
}
impl PreviewLocalFolderRefreshErrorType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PreviewLocalFolderRefreshErrorOperation {
    #[serde(rename = "market.previewLocalFolderRefresh")]
    Value,
}
impl PreviewLocalFolderRefreshErrorOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.previewLocalFolderRefresh",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PrepareLocalFolderPublicationRequestProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl PrepareLocalFolderPublicationRequestProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PrepareLocalFolderPublicationRequestType {
    #[serde(rename = "request")]
    Value,
}
impl PrepareLocalFolderPublicationRequestType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PrepareLocalFolderPublicationRequestOperation {
    #[serde(rename = "market.prepareLocalFolderPublication")]
    Value,
}
impl PrepareLocalFolderPublicationRequestOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.prepareLocalFolderPublication",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PrepareLocalFolderPublicationResponseProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl PrepareLocalFolderPublicationResponseProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PrepareLocalFolderPublicationResponseType {
    #[serde(rename = "response")]
    Value,
}
impl PrepareLocalFolderPublicationResponseType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PrepareLocalFolderPublicationResponseOperation {
    #[serde(rename = "market.prepareLocalFolderPublication")]
    Value,
}
impl PrepareLocalFolderPublicationResponseOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.prepareLocalFolderPublication",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PrepareLocalFolderPublicationErrorProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl PrepareLocalFolderPublicationErrorProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PrepareLocalFolderPublicationErrorType {
    #[serde(rename = "response")]
    Value,
}
impl PrepareLocalFolderPublicationErrorType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PrepareLocalFolderPublicationErrorOperation {
    #[serde(rename = "market.prepareLocalFolderPublication")]
    Value,
}
impl PrepareLocalFolderPublicationErrorOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.prepareLocalFolderPublication",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PublishLocalFolderRefreshRequestProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl PublishLocalFolderRefreshRequestProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PublishLocalFolderRefreshRequestType {
    #[serde(rename = "request")]
    Value,
}
impl PublishLocalFolderRefreshRequestType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PublishLocalFolderRefreshRequestOperation {
    #[serde(rename = "market.publishLocalFolderRefresh")]
    Value,
}
impl PublishLocalFolderRefreshRequestOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.publishLocalFolderRefresh",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PublishLocalFolderRefreshResponseProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl PublishLocalFolderRefreshResponseProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PublishLocalFolderRefreshResponseType {
    #[serde(rename = "response")]
    Value,
}
impl PublishLocalFolderRefreshResponseType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PublishLocalFolderRefreshResponseOperation {
    #[serde(rename = "market.publishLocalFolderRefresh")]
    Value,
}
impl PublishLocalFolderRefreshResponseOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.publishLocalFolderRefresh",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PublishLocalFolderRefreshErrorProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl PublishLocalFolderRefreshErrorProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PublishLocalFolderRefreshErrorType {
    #[serde(rename = "response")]
    Value,
}
impl PublishLocalFolderRefreshErrorType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PublishLocalFolderRefreshErrorOperation {
    #[serde(rename = "market.publishLocalFolderRefresh")]
    Value,
}
impl PublishLocalFolderRefreshErrorOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.publishLocalFolderRefresh",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ListLocalFolderRevisionsRequestProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl ListLocalFolderRevisionsRequestProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ListLocalFolderRevisionsRequestType {
    #[serde(rename = "request")]
    Value,
}
impl ListLocalFolderRevisionsRequestType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ListLocalFolderRevisionsRequestOperation {
    #[serde(rename = "market.listLocalFolderRevisions")]
    Value,
}
impl ListLocalFolderRevisionsRequestOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.listLocalFolderRevisions",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ListLocalFolderRevisionsResponseProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl ListLocalFolderRevisionsResponseProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ListLocalFolderRevisionsResponseType {
    #[serde(rename = "response")]
    Value,
}
impl ListLocalFolderRevisionsResponseType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ListLocalFolderRevisionsResponseOperation {
    #[serde(rename = "market.listLocalFolderRevisions")]
    Value,
}
impl ListLocalFolderRevisionsResponseOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.listLocalFolderRevisions",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ListLocalFolderRevisionsErrorProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl ListLocalFolderRevisionsErrorProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ListLocalFolderRevisionsErrorType {
    #[serde(rename = "response")]
    Value,
}
impl ListLocalFolderRevisionsErrorType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ListLocalFolderRevisionsErrorOperation {
    #[serde(rename = "market.listLocalFolderRevisions")]
    Value,
}
impl ListLocalFolderRevisionsErrorOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.listLocalFolderRevisions",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ReadLocalFolderRevisionRequestProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl ReadLocalFolderRevisionRequestProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ReadLocalFolderRevisionRequestType {
    #[serde(rename = "request")]
    Value,
}
impl ReadLocalFolderRevisionRequestType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ReadLocalFolderRevisionRequestOperation {
    #[serde(rename = "market.readLocalFolderRevision")]
    Value,
}
impl ReadLocalFolderRevisionRequestOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.readLocalFolderRevision",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ReadLocalFolderRevisionResponseProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl ReadLocalFolderRevisionResponseProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ReadLocalFolderRevisionResponseType {
    #[serde(rename = "response")]
    Value,
}
impl ReadLocalFolderRevisionResponseType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ReadLocalFolderRevisionResponseOperation {
    #[serde(rename = "market.readLocalFolderRevision")]
    Value,
}
impl ReadLocalFolderRevisionResponseOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.readLocalFolderRevision",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ReadLocalFolderRevisionErrorProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl ReadLocalFolderRevisionErrorProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ReadLocalFolderRevisionErrorType {
    #[serde(rename = "response")]
    Value,
}
impl ReadLocalFolderRevisionErrorType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ReadLocalFolderRevisionErrorOperation {
    #[serde(rename = "market.readLocalFolderRevision")]
    Value,
}
impl ReadLocalFolderRevisionErrorOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.readLocalFolderRevision",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ReadLocalFolderSourceRowRequestProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl ReadLocalFolderSourceRowRequestProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ReadLocalFolderSourceRowRequestType {
    #[serde(rename = "request")]
    Value,
}
impl ReadLocalFolderSourceRowRequestType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ReadLocalFolderSourceRowRequestOperation {
    #[serde(rename = "market.readLocalFolderSourceRow")]
    Value,
}
impl ReadLocalFolderSourceRowRequestOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.readLocalFolderSourceRow",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ReadLocalFolderSourceRowResponseProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl ReadLocalFolderSourceRowResponseProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ReadLocalFolderSourceRowResponseType {
    #[serde(rename = "response")]
    Value,
}
impl ReadLocalFolderSourceRowResponseType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ReadLocalFolderSourceRowResponseOperation {
    #[serde(rename = "market.readLocalFolderSourceRow")]
    Value,
}
impl ReadLocalFolderSourceRowResponseOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.readLocalFolderSourceRow",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ReadLocalFolderSourceRowErrorProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl ReadLocalFolderSourceRowErrorProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ReadLocalFolderSourceRowErrorType {
    #[serde(rename = "response")]
    Value,
}
impl ReadLocalFolderSourceRowErrorType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ReadLocalFolderSourceRowErrorOperation {
    #[serde(rename = "market.readLocalFolderSourceRow")]
    Value,
}
impl ReadLocalFolderSourceRowErrorOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.readLocalFolderSourceRow",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum RevokeLocalFolderRevisionRequestProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl RevokeLocalFolderRevisionRequestProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum RevokeLocalFolderRevisionRequestType {
    #[serde(rename = "request")]
    Value,
}
impl RevokeLocalFolderRevisionRequestType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum RevokeLocalFolderRevisionRequestOperation {
    #[serde(rename = "market.revokeLocalFolderRevision")]
    Value,
}
impl RevokeLocalFolderRevisionRequestOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.revokeLocalFolderRevision",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum RevokeLocalFolderRevisionResponseProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl RevokeLocalFolderRevisionResponseProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum RevokeLocalFolderRevisionResponseType {
    #[serde(rename = "response")]
    Value,
}
impl RevokeLocalFolderRevisionResponseType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum RevokeLocalFolderRevisionResponseOperation {
    #[serde(rename = "market.revokeLocalFolderRevision")]
    Value,
}
impl RevokeLocalFolderRevisionResponseOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.revokeLocalFolderRevision",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum RevokeLocalFolderRevisionErrorProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl RevokeLocalFolderRevisionErrorProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum RevokeLocalFolderRevisionErrorType {
    #[serde(rename = "response")]
    Value,
}
impl RevokeLocalFolderRevisionErrorType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum RevokeLocalFolderRevisionErrorOperation {
    #[serde(rename = "market.revokeLocalFolderRevision")]
    Value,
}
impl RevokeLocalFolderRevisionErrorOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.revokeLocalFolderRevision",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum RevokeLocalImportRevisionRequestProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl RevokeLocalImportRevisionRequestProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum RevokeLocalImportRevisionRequestType {
    #[serde(rename = "request")]
    Value,
}
impl RevokeLocalImportRevisionRequestType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum RevokeLocalImportRevisionRequestOperation {
    #[serde(rename = "market.revokeLocalImportRevision")]
    Value,
}
impl RevokeLocalImportRevisionRequestOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.revokeLocalImportRevision",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum RevokeLocalImportRevisionResponseProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl RevokeLocalImportRevisionResponseProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum RevokeLocalImportRevisionResponseType {
    #[serde(rename = "response")]
    Value,
}
impl RevokeLocalImportRevisionResponseType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum RevokeLocalImportRevisionResponseOperation {
    #[serde(rename = "market.revokeLocalImportRevision")]
    Value,
}
impl RevokeLocalImportRevisionResponseOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.revokeLocalImportRevision",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum RevokeLocalImportRevisionErrorProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl RevokeLocalImportRevisionErrorProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum RevokeLocalImportRevisionErrorType {
    #[serde(rename = "response")]
    Value,
}
impl RevokeLocalImportRevisionErrorType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum RevokeLocalImportRevisionErrorOperation {
    #[serde(rename = "market.revokeLocalImportRevision")]
    Value,
}
impl RevokeLocalImportRevisionErrorOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.revokeLocalImportRevision",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PublishLocalImportRequestProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl PublishLocalImportRequestProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PublishLocalImportRequestType {
    #[serde(rename = "request")]
    Value,
}
impl PublishLocalImportRequestType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PublishLocalImportRequestOperation {
    #[serde(rename = "market.publishLocalImport")]
    Value,
}
impl PublishLocalImportRequestOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.publishLocalImport",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PublishLocalImportResponseProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl PublishLocalImportResponseProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PublishLocalImportResponseType {
    #[serde(rename = "response")]
    Value,
}
impl PublishLocalImportResponseType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PublishLocalImportResponseOperation {
    #[serde(rename = "market.publishLocalImport")]
    Value,
}
impl PublishLocalImportResponseOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.publishLocalImport",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PublishLocalImportErrorProtocolVersion {
    #[serde(rename = "1.1")]
    Value,
}
impl PublishLocalImportErrorProtocolVersion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "1.1",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PublishLocalImportErrorType {
    #[serde(rename = "response")]
    Value,
}
impl PublishLocalImportErrorType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PublishLocalImportErrorOperation {
    #[serde(rename = "market.publishLocalImport")]
    Value,
}
impl PublishLocalImportErrorOperation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.publishLocalImport",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum WorkerHello11Type {
    #[serde(rename = "hello")]
    Value,
}
impl WorkerHello11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "hello",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ScopedMutationRequest11Type {
    #[serde(rename = "request")]
    Value,
}
impl ScopedMutationRequest11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ScopedMutationResponse11Type {
    #[serde(rename = "response")]
    Value,
}
impl ScopedMutationResponse11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum TrustedConfirmationDecision11Type {
    #[serde(rename = "request")]
    Value,
}
impl TrustedConfirmationDecision11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum TrustedConfirmationDecision11Operation {
    #[serde(rename = "confirmation.respond")]
    Value,
}
impl TrustedConfirmationDecision11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "confirmation.respond",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum TrustedConfirmationDecision11Decision {
    #[serde(rename = "approve")]
    Approve,
    #[serde(rename = "deny")]
    Deny,
}
impl TrustedConfirmationDecision11Decision {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Approve => "approve",
            Self::Deny => "deny",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PreviewReady11Type {
    #[serde(rename = "event")]
    Value,
}
impl PreviewReady11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "event",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PreviewReady11Event {
    #[serde(rename = "indicator.preview_ready")]
    Value,
}
impl PreviewReady11Event {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "indicator.preview_ready",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ArtifactChunk11Type {
    #[serde(rename = "event")]
    Value,
}
impl ArtifactChunk11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "event",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ArtifactChunk11Event {
    #[serde(rename = "indicator.result_segment")]
    Value,
}
impl ArtifactChunk11Event {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "indicator.result_segment",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum TaskTerminalEvent11Type {
    #[serde(rename = "event")]
    Value,
}
impl TaskTerminalEvent11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "event",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum TaskTerminalEvent11Event {
    #[serde(rename = "task.terminal")]
    Value,
}
impl TaskTerminalEvent11Event {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "task.terminal",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum TaskCancelRequest11Type {
    #[serde(rename = "request")]
    Value,
}
impl TaskCancelRequest11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum TaskCancelRequest11Operation {
    #[serde(rename = "task.cancel")]
    Value,
}
impl TaskCancelRequest11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "task.cancel",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum CancelMemberRequest11Type {
    #[serde(rename = "cancel")]
    Value,
}
impl CancelMemberRequest11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "cancel",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ReadProjectedChart11Type {
    #[serde(rename = "request")]
    Value,
}
impl ReadProjectedChart11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ReadProjectedChart11Operation {
    #[serde(rename = "workspace.readChartProjection")]
    Value,
}
impl ReadProjectedChart11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "workspace.readChartProjection",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ProjectedChartResponse11Type {
    #[serde(rename = "response")]
    Value,
}
impl ProjectedChartResponse11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ProjectedChartResponse11Operation {
    #[serde(rename = "workspace.readChartProjection")]
    Value,
}
impl ProjectedChartResponse11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "workspace.readChartProjection",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum SharedSelectionEvent11Type {
    #[serde(rename = "event")]
    Value,
}
impl SharedSelectionEvent11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "event",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum SharedSelectionEvent11Event {
    #[serde(rename = "workspace.selection_changed")]
    Value,
}
impl SharedSelectionEvent11Event {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "workspace.selection_changed",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum SharedSelectionRequest11Type {
    #[serde(rename = "request")]
    Value,
}
impl SharedSelectionRequest11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum SharedSelectionRequest11Operation {
    #[serde(rename = "workspace.selectEvidence")]
    Value,
}
impl SharedSelectionRequest11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "workspace.selectEvidence",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AiToolRequest11Type {
    #[serde(rename = "event")]
    Value,
}
impl AiToolRequest11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "event",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AiToolRequest11Event {
    #[serde(rename = "ai.tool_request")]
    Value,
}
impl AiToolRequest11Event {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "ai.tool_request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AiToolResultDelivery11Type {
    #[serde(rename = "request")]
    Value,
}
impl AiToolResultDelivery11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AiToolResultDelivery11Operation {
    #[serde(rename = "ai.tool_result")]
    Value,
}
impl AiToolResultDelivery11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "ai.tool_result",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AiToolResultAck11Type {
    #[serde(rename = "response")]
    Value,
}
impl AiToolResultAck11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AiToolResultAck11Operation {
    #[serde(rename = "ai.tool_result")]
    Value,
}
impl AiToolResultAck11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "ai.tool_result",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AiToolResultAck11Status {
    #[serde(rename = "received")]
    Received,
    #[serde(rename = "already_received")]
    AlreadyReceived,
    #[serde(rename = "task_terminal")]
    TaskTerminal,
}
impl AiToolResultAck11Status {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Received => "received",
            Self::AlreadyReceived => "already_received",
            Self::TaskTerminal => "task_terminal",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ProjectedChartError11Type {
    #[serde(rename = "response")]
    Value,
}
impl ProjectedChartError11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ProjectedChartError11Operation {
    #[serde(rename = "workspace.readChartProjection")]
    Value,
}
impl ProjectedChartError11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "workspace.readChartProjection",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum IndicatorCompute11Type {
    #[serde(rename = "request")]
    Value,
}
impl IndicatorCompute11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum IndicatorCompute11Operation {
    #[serde(rename = "indicator.compute")]
    Value,
}
impl IndicatorCompute11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "indicator.compute",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum IndicatorComputed11Type {
    #[serde(rename = "response")]
    Value,
}
impl IndicatorComputed11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum IndicatorComputed11Operation {
    #[serde(rename = "indicator.compute")]
    Value,
}
impl IndicatorComputed11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "indicator.compute",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum IndicatorInputSegment11Type {
    #[serde(rename = "event")]
    Value,
}
impl IndicatorInputSegment11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "event",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum IndicatorInputSegment11Event {
    #[serde(rename = "indicator.input_segment")]
    Value,
}
impl IndicatorInputSegment11Event {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "indicator.input_segment",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum SharedSelectionResponse11Type {
    #[serde(rename = "response")]
    Value,
}
impl SharedSelectionResponse11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum SharedSelectionResponse11Operation {
    #[serde(rename = "workspace.selectEvidence")]
    Value,
}
impl SharedSelectionResponse11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "workspace.selectEvidence",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum SharedSelectionError11Type {
    #[serde(rename = "response")]
    Value,
}
impl SharedSelectionError11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum SharedSelectionError11Operation {
    #[serde(rename = "workspace.selectEvidence")]
    Value,
}
impl SharedSelectionError11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "workspace.selectEvidence",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum SharedContextRequest11Type {
    #[serde(rename = "request")]
    Value,
}
impl SharedContextRequest11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum SharedContextRequest11Operation {
    #[serde(rename = "workspace.getContext")]
    Value,
}
impl SharedContextRequest11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "workspace.getContext",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum SharedContextResponse11Type {
    #[serde(rename = "response")]
    Value,
}
impl SharedContextResponse11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum SharedContextResponse11Operation {
    #[serde(rename = "workspace.getContext")]
    Value,
}
impl SharedContextResponse11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "workspace.getContext",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum SharedContextError11Type {
    #[serde(rename = "response")]
    Value,
}
impl SharedContextError11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum SharedContextError11Operation {
    #[serde(rename = "workspace.getContext")]
    Value,
}
impl SharedContextError11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "workspace.getContext",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum CancelTaskResponse11Type {
    #[serde(rename = "response")]
    Value,
}
impl CancelTaskResponse11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum CancelTaskResponse11Operation {
    #[serde(rename = "task.cancel")]
    Value,
}
impl CancelTaskResponse11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "task.cancel",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum CancelTaskError11Type {
    #[serde(rename = "response")]
    Value,
}
impl CancelTaskError11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum CancelTaskError11Operation {
    #[serde(rename = "task.cancel")]
    Value,
}
impl CancelTaskError11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "task.cancel",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum WorkspaceObject11Kind {
    #[serde(rename = "chart")]
    Chart,
    #[serde(rename = "layer")]
    Layer,
    #[serde(rename = "layout")]
    Layout,
    #[serde(rename = "dataset")]
    Dataset,
    #[serde(rename = "workspace")]
    Workspace,
}
impl WorkspaceObject11Kind {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Chart => "chart",
            Self::Layer => "layer",
            Self::Layout => "layout",
            Self::Dataset => "dataset",
            Self::Workspace => "workspace",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ChartInterval11Kind {
    #[serde(rename = "chart_interval")]
    Value,
}
impl ChartInterval11Kind {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "chart_interval",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum SourceLocation11Kind {
    #[serde(rename = "table_row")]
    TableRow,
    #[serde(rename = "document_passage")]
    DocumentPassage,
}
impl SourceLocation11Kind {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::TableRow => "table_row",
            Self::DocumentPassage => "document_passage",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum NumberParameter11Type {
    #[serde(rename = "number")]
    Value,
}
impl NumberParameter11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "number",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum IntegerParameter11Type {
    #[serde(rename = "integer")]
    Value,
}
impl IntegerParameter11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "integer",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum BooleanParameter11Type {
    #[serde(rename = "boolean")]
    Value,
}
impl BooleanParameter11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "boolean",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum NumberReference11Op {
    #[serde(rename = "number_parameter")]
    Value,
}
impl NumberReference11Op {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "number_parameter",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum BooleanReference11Op {
    #[serde(rename = "boolean_parameter")]
    Value,
}
impl BooleanReference11Op {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "boolean_parameter",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum Arithmetic11Op {
    #[serde(rename = "add")]
    Add,
    #[serde(rename = "subtract")]
    Subtract,
    #[serde(rename = "multiply")]
    Multiply,
    #[serde(rename = "divide")]
    Divide,
}
impl Arithmetic11Op {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Add => "add",
            Self::Subtract => "subtract",
            Self::Multiply => "multiply",
            Self::Divide => "divide",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum Absolute11Op {
    #[serde(rename = "abs")]
    Value,
}
impl Absolute11Op {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "abs",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum Delay11Op {
    #[serde(rename = "lag")]
    Value,
}
impl Delay11Op {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "lag",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum Rolling11Op {
    #[serde(rename = "rolling_mean")]
    RollingMean,
    #[serde(rename = "rolling_std")]
    RollingStd,
}
impl Rolling11Op {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::RollingMean => "rolling_mean",
            Self::RollingStd => "rolling_std",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum BooleanConstant11Op {
    #[serde(rename = "boolean")]
    Value,
}
impl BooleanConstant11Op {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "boolean",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum Comparison11Op {
    #[serde(rename = "gt")]
    Gt,
    #[serde(rename = "lt")]
    Lt,
}
impl Comparison11Op {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Gt => "gt",
            Self::Lt => "lt",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum BooleanBinary11Op {
    #[serde(rename = "and")]
    And,
    #[serde(rename = "or")]
    Or,
}
impl BooleanBinary11Op {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::And => "and",
            Self::Or => "or",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum NumericSeries11Kind {
    #[serde(rename = "line")]
    Line,
    #[serde(rename = "histogram")]
    Histogram,
}
impl NumericSeries11Kind {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Line => "line",
            Self::Histogram => "histogram",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ConditionalMarker11Kind {
    #[serde(rename = "marker_when")]
    Value,
}
impl ConditionalMarker11Kind {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "marker_when",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DeclarativeDraft11Language {
    #[serde(rename = "declarative")]
    Value,
}
impl DeclarativeDraft11Language {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "declarative",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PythonDraft11Language {
    #[serde(rename = "python")]
    Value,
}
impl PythonDraft11Language {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "python",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum WorkspaceOpenInstrumentIntent11Operation {
    #[serde(rename = "workspace.openInstrument")]
    Value,
}
impl WorkspaceOpenInstrumentIntent11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "workspace.openInstrument",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ChartSetTimeframeIntent11Operation {
    #[serde(rename = "chart.setTimeframe")]
    Value,
}
impl ChartSetTimeframeIntent11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "chart.setTimeframe",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ChartSetStyleIntent11Operation {
    #[serde(rename = "chart.setStyle")]
    Value,
}
impl ChartSetStyleIntent11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "chart.setStyle",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ChartAddAnnotationIntent11Operation {
    #[serde(rename = "chart.addAnnotation")]
    Value,
}
impl ChartAddAnnotationIntent11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "chart.addAnnotation",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ChartRemoveLayerIntent11Operation {
    #[serde(rename = "chart.removeLayer")]
    Value,
}
impl ChartRemoveLayerIntent11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "chart.removeLayer",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum IndicatorCreateDraftIntent11Operation {
    #[serde(rename = "indicator.createDraft")]
    Value,
}
impl IndicatorCreateDraftIntent11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "indicator.createDraft",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum IndicatorRunPreviewIntent11Operation {
    #[serde(rename = "indicator.runPreview")]
    Value,
}
impl IndicatorRunPreviewIntent11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "indicator.runPreview",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum IndicatorAcceptVersionIntent11Operation {
    #[serde(rename = "indicator.acceptVersion")]
    Value,
}
impl IndicatorAcceptVersionIntent11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "indicator.acceptVersion",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum IndicatorRemoveIntent11Operation {
    #[serde(rename = "indicator.remove")]
    Value,
}
impl IndicatorRemoveIntent11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "indicator.remove",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum WorkspaceUndoIntent11Operation {
    #[serde(rename = "workspace.undo")]
    Value,
}
impl WorkspaceUndoIntent11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "workspace.undo",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum WorkspacePreviewLayoutIntent11Operation {
    #[serde(rename = "workspace.previewLayout")]
    Value,
}
impl WorkspacePreviewLayoutIntent11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "workspace.previewLayout",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum WorkspaceApplyLayoutIntent11Operation {
    #[serde(rename = "workspace.applyLayout")]
    Value,
}
impl WorkspaceApplyLayoutIntent11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "workspace.applyLayout",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ScopedFailure11State {
    #[serde(rename = "failed")]
    Value,
}
impl ScopedFailure11State {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "failed",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PythonExecutionScope11TrustMode {
    #[serde(rename = "trusted_user_code_no_sandbox")]
    Value,
}
impl PythonExecutionScope11TrustMode {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "trusted_user_code_no_sandbox",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PythonExecutionScope11InputUse {
    #[serde(rename = "trusted_code_permitted")]
    Value,
}
impl PythonExecutionScope11InputUse {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "trusted_code_permitted",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum NeedsHumanDecision11State {
    #[serde(rename = "awaiting_confirmation")]
    Value,
}
impl NeedsHumanDecision11State {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "awaiting_confirmation",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum WorkspaceOpenInstrumentCommitted11State {
    #[serde(rename = "committed")]
    Value,
}
impl WorkspaceOpenInstrumentCommitted11State {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "committed",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum WorkspaceOpenInstrumentCommitted11Operation {
    #[serde(rename = "workspace.openInstrument")]
    Value,
}
impl WorkspaceOpenInstrumentCommitted11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "workspace.openInstrument",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ChartSetTimeframeCommitted11State {
    #[serde(rename = "committed")]
    Value,
}
impl ChartSetTimeframeCommitted11State {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "committed",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ChartSetTimeframeCommitted11Operation {
    #[serde(rename = "chart.setTimeframe")]
    Value,
}
impl ChartSetTimeframeCommitted11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "chart.setTimeframe",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ChartSetStyleCommitted11State {
    #[serde(rename = "committed")]
    Value,
}
impl ChartSetStyleCommitted11State {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "committed",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ChartSetStyleCommitted11Operation {
    #[serde(rename = "chart.setStyle")]
    Value,
}
impl ChartSetStyleCommitted11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "chart.setStyle",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ChartAddAnnotationCommitted11State {
    #[serde(rename = "committed")]
    Value,
}
impl ChartAddAnnotationCommitted11State {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "committed",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ChartAddAnnotationCommitted11Operation {
    #[serde(rename = "chart.addAnnotation")]
    Value,
}
impl ChartAddAnnotationCommitted11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "chart.addAnnotation",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ChartRemoveLayerCommitted11State {
    #[serde(rename = "committed")]
    Value,
}
impl ChartRemoveLayerCommitted11State {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "committed",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ChartRemoveLayerCommitted11Operation {
    #[serde(rename = "chart.removeLayer")]
    Value,
}
impl ChartRemoveLayerCommitted11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "chart.removeLayer",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum IndicatorCreateDraftCommitted11State {
    #[serde(rename = "committed")]
    Value,
}
impl IndicatorCreateDraftCommitted11State {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "committed",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum IndicatorCreateDraftCommitted11Operation {
    #[serde(rename = "indicator.createDraft")]
    Value,
}
impl IndicatorCreateDraftCommitted11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "indicator.createDraft",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum IndicatorRunPreviewCommitted11State {
    #[serde(rename = "committed")]
    Value,
}
impl IndicatorRunPreviewCommitted11State {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "committed",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum IndicatorRunPreviewCommitted11Operation {
    #[serde(rename = "indicator.runPreview")]
    Value,
}
impl IndicatorRunPreviewCommitted11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "indicator.runPreview",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum IndicatorAcceptVersionCommitted11State {
    #[serde(rename = "committed")]
    Value,
}
impl IndicatorAcceptVersionCommitted11State {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "committed",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum IndicatorAcceptVersionCommitted11Operation {
    #[serde(rename = "indicator.acceptVersion")]
    Value,
}
impl IndicatorAcceptVersionCommitted11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "indicator.acceptVersion",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum IndicatorRemoveCommitted11State {
    #[serde(rename = "committed")]
    Value,
}
impl IndicatorRemoveCommitted11State {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "committed",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum IndicatorRemoveCommitted11Operation {
    #[serde(rename = "indicator.remove")]
    Value,
}
impl IndicatorRemoveCommitted11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "indicator.remove",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum WorkspaceUndoCommitted11State {
    #[serde(rename = "committed")]
    Value,
}
impl WorkspaceUndoCommitted11State {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "committed",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum WorkspaceUndoCommitted11Operation {
    #[serde(rename = "workspace.undo")]
    Value,
}
impl WorkspaceUndoCommitted11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "workspace.undo",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum WorkspacePreviewLayoutSuccess11State {
    #[serde(rename = "previewed")]
    Value,
}
impl WorkspacePreviewLayoutSuccess11State {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "previewed",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum WorkspacePreviewLayoutSuccess11Operation {
    #[serde(rename = "workspace.previewLayout")]
    Value,
}
impl WorkspacePreviewLayoutSuccess11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "workspace.previewLayout",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum WorkspaceApplyLayoutSuccess11State {
    #[serde(rename = "committed")]
    Value,
}
impl WorkspaceApplyLayoutSuccess11State {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "committed",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum WorkspaceApplyLayoutSuccess11Operation {
    #[serde(rename = "workspace.applyLayout")]
    Value,
}
impl WorkspaceApplyLayoutSuccess11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "workspace.applyLayout",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum InlineIndicatorArtifact11Storage {
    #[serde(rename = "inline")]
    Value,
}
impl InlineIndicatorArtifact11Storage {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "inline",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum IndexedIndicatorArtifact11Storage {
    #[serde(rename = "core_artifact")]
    Value,
}
impl IndexedIndicatorArtifact11Storage {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "core_artifact",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum TaskCompleted11Status {
    #[serde(rename = "completed")]
    Value,
}
impl TaskCompleted11Status {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "completed",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum TaskCancelled11Status {
    #[serde(rename = "cancelled")]
    Value,
}
impl TaskCancelled11Status {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "cancelled",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum TaskFailed11Status {
    #[serde(rename = "failed")]
    Value,
}
impl TaskFailed11Status {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "failed",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ProjectedReadIntent11Operation {
    #[serde(rename = "workspace.readChartProjection")]
    Value,
}
impl ProjectedReadIntent11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "workspace.readChartProjection",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum MarketHistoryPageToolCall11Operation {
    #[serde(rename = "market.readHistoryPage")]
    Value,
}
impl MarketHistoryPageToolCall11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "market.readHistoryPage",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum SelectEvidenceIntent11Operation {
    #[serde(rename = "workspace.selectEvidence")]
    Value,
}
impl SelectEvidenceIntent11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "workspace.selectEvidence",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ToolReadResult11Status {
    #[serde(rename = "read")]
    Value,
}
impl ToolReadResult11Status {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "read",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ToolCommittedResult11Status {
    #[serde(rename = "committed")]
    Value,
}
impl ToolCommittedResult11Status {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "committed",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ToolRefResult11Status {
    #[serde(rename = "available")]
    Value,
}
impl ToolRefResult11Status {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "available",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ToolDeniedResult11Status {
    #[serde(rename = "denied")]
    Denied,
    #[serde(rename = "failed")]
    Failed,
    #[serde(rename = "cancelled")]
    Cancelled,
}
impl ToolDeniedResult11Status {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Denied => "denied",
            Self::Failed => "failed",
            Self::Cancelled => "cancelled",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum InlineIndicatorBars11Mode {
    #[serde(rename = "inline")]
    Value,
}
impl InlineIndicatorBars11Mode {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "inline",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum SegmentedIndicatorBars11Mode {
    #[serde(rename = "segmented")]
    Value,
}
impl SegmentedIndicatorBars11Mode {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "segmented",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum WorkspaceOpenInstrumentCommand11Type {
    #[serde(rename = "request")]
    Value,
}
impl WorkspaceOpenInstrumentCommand11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum WorkspaceOpenInstrumentReply11Type {
    #[serde(rename = "response")]
    Value,
}
impl WorkspaceOpenInstrumentReply11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ChartSetTimeframeCommand11Type {
    #[serde(rename = "request")]
    Value,
}
impl ChartSetTimeframeCommand11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ChartSetTimeframeReply11Type {
    #[serde(rename = "response")]
    Value,
}
impl ChartSetTimeframeReply11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ChartSetStyleCommand11Type {
    #[serde(rename = "request")]
    Value,
}
impl ChartSetStyleCommand11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ChartSetStyleReply11Type {
    #[serde(rename = "response")]
    Value,
}
impl ChartSetStyleReply11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ChartAddAnnotationCommand11Type {
    #[serde(rename = "request")]
    Value,
}
impl ChartAddAnnotationCommand11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ChartAddAnnotationReply11Type {
    #[serde(rename = "response")]
    Value,
}
impl ChartAddAnnotationReply11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ChartRemoveLayerCommand11Type {
    #[serde(rename = "request")]
    Value,
}
impl ChartRemoveLayerCommand11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ChartRemoveLayerReply11Type {
    #[serde(rename = "response")]
    Value,
}
impl ChartRemoveLayerReply11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum IndicatorCreateDraftCommand11Type {
    #[serde(rename = "request")]
    Value,
}
impl IndicatorCreateDraftCommand11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum IndicatorCreateDraftReply11Type {
    #[serde(rename = "response")]
    Value,
}
impl IndicatorCreateDraftReply11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum IndicatorRunPreviewCommand11Type {
    #[serde(rename = "request")]
    Value,
}
impl IndicatorRunPreviewCommand11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum IndicatorRunPreviewReply11Type {
    #[serde(rename = "response")]
    Value,
}
impl IndicatorRunPreviewReply11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum IndicatorAcceptVersionCommand11Type {
    #[serde(rename = "request")]
    Value,
}
impl IndicatorAcceptVersionCommand11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum IndicatorAcceptVersionReply11Type {
    #[serde(rename = "response")]
    Value,
}
impl IndicatorAcceptVersionReply11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum IndicatorRemoveCommand11Type {
    #[serde(rename = "request")]
    Value,
}
impl IndicatorRemoveCommand11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum IndicatorRemoveReply11Type {
    #[serde(rename = "response")]
    Value,
}
impl IndicatorRemoveReply11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum WorkspaceUndoCommand11Type {
    #[serde(rename = "request")]
    Value,
}
impl WorkspaceUndoCommand11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum WorkspaceUndoReply11Type {
    #[serde(rename = "response")]
    Value,
}
impl WorkspaceUndoReply11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum LoadLocalImportMappingOutputStatus {
    #[serde(rename = "saved")]
    Saved,
    #[serde(rename = "not_saved")]
    NotSaved,
    #[serde(rename = "review_required")]
    ReviewRequired,
}
impl LoadLocalImportMappingOutputStatus {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Saved => "saved",
            Self::NotSaved => "not_saved",
            Self::ReviewRequired => "review_required",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DecisionNoteTargetKind {
    #[serde(rename = "decision_note")]
    Value,
}
impl DecisionNoteTargetKind {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "decision_note",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PrivateRecordTurnSpeaker {
    #[serde(rename = "user")]
    User,
    #[serde(rename = "assistant")]
    Assistant,
}
impl PrivateRecordTurnSpeaker {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::User => "user",
            Self::Assistant => "assistant",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PrivateRecordConversationKind {
    #[serde(rename = "conversation")]
    Value,
}
impl PrivateRecordConversationKind {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "conversation",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PrivateRecordMemoryKind {
    #[serde(rename = "memory")]
    Value,
}
impl PrivateRecordMemoryKind {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "memory",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PrivateRecordStrategyKind {
    #[serde(rename = "strategy")]
    Value,
}
impl PrivateRecordStrategyKind {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "strategy",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PrivateRecordBackupV1Format {
    #[serde(rename = "scopehub.private-records")]
    Value,
}
impl PrivateRecordBackupV1Format {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "scopehub.private-records",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PrivateRecordSaveInputAction {
    #[serde(rename = "save")]
    Value,
}
impl PrivateRecordSaveInputAction {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "save",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PrivateRecordDeleteInputAction {
    #[serde(rename = "delete")]
    Value,
}
impl PrivateRecordDeleteInputAction {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "delete",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PrivateRecordRestoreInputAction {
    #[serde(rename = "restore")]
    Value,
}
impl PrivateRecordRestoreInputAction {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "restore",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PrivateRecordResolveInputAction {
    #[serde(rename = "resolve")]
    Value,
}
impl PrivateRecordResolveInputAction {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "resolve",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PrivateRecordResolveInputDecision {
    #[serde(rename = "approve")]
    Approve,
    #[serde(rename = "decline")]
    Decline,
}
impl PrivateRecordResolveInputDecision {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Approve => "approve",
            Self::Decline => "decline",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PrivateRecordReadIntent11Operation {
    #[serde(rename = "privateRecord.read")]
    Value,
}
impl PrivateRecordReadIntent11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "privateRecord.read",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PrivateRecordReadCommand11Type {
    #[serde(rename = "request")]
    Value,
}
impl PrivateRecordReadCommand11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PrivateRecordReadCompleted11State {
    #[serde(rename = "completed")]
    Value,
}
impl PrivateRecordReadCompleted11State {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "completed",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PrivateRecordReadCompleted11Operation {
    #[serde(rename = "privateRecord.read")]
    Value,
}
impl PrivateRecordReadCompleted11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "privateRecord.read",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PrivateRecordReadReply11Type {
    #[serde(rename = "response")]
    Value,
}
impl PrivateRecordReadReply11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PrivateRecordChangeIntent11Operation {
    #[serde(rename = "privateRecord.change")]
    Value,
}
impl PrivateRecordChangeIntent11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "privateRecord.change",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PrivateRecordChangeCommand11Type {
    #[serde(rename = "request")]
    Value,
}
impl PrivateRecordChangeCommand11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PrivateRecordChangeCommitted11State {
    #[serde(rename = "committed")]
    Value,
}
impl PrivateRecordChangeCommitted11State {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "committed",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PrivateRecordChangeCommitted11Operation {
    #[serde(rename = "privateRecord.change")]
    Value,
}
impl PrivateRecordChangeCommitted11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "privateRecord.change",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PrivateRecordChangeConfirmationRequired11State {
    #[serde(rename = "confirmation_required")]
    Value,
}
impl PrivateRecordChangeConfirmationRequired11State {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "confirmation_required",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PrivateRecordChangeConfirmationRequired11Operation {
    #[serde(rename = "privateRecord.change")]
    Value,
}
impl PrivateRecordChangeConfirmationRequired11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "privateRecord.change",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PrivateRecordChangeReply11Type {
    #[serde(rename = "response")]
    Value,
}
impl PrivateRecordChangeReply11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PrivateRecordExportIntent11Operation {
    #[serde(rename = "privateRecord.exportBackup")]
    Value,
}
impl PrivateRecordExportIntent11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "privateRecord.exportBackup",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PrivateRecordExportCommand11Type {
    #[serde(rename = "request")]
    Value,
}
impl PrivateRecordExportCommand11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PrivateRecordExportCompleted11State {
    #[serde(rename = "completed")]
    Value,
}
impl PrivateRecordExportCompleted11State {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "completed",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PrivateRecordExportCompleted11Operation {
    #[serde(rename = "privateRecord.exportBackup")]
    Value,
}
impl PrivateRecordExportCompleted11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "privateRecord.exportBackup",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PrivateRecordExportReply11Type {
    #[serde(rename = "response")]
    Value,
}
impl PrivateRecordExportReply11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DecisionNoteConfirmation11Decision {
    #[serde(rename = "approve")]
    Approve,
    #[serde(rename = "decline")]
    Decline,
}
impl DecisionNoteConfirmation11Decision {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Approve => "approve",
            Self::Decline => "decline",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DecisionNoteSaveIntent11Operation {
    #[serde(rename = "decisionNote.save")]
    Value,
}
impl DecisionNoteSaveIntent11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "decisionNote.save",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DecisionNoteSaveCommand11Type {
    #[serde(rename = "request")]
    Value,
}
impl DecisionNoteSaveCommand11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DecisionNoteSaveCommitted11State {
    #[serde(rename = "committed")]
    Value,
}
impl DecisionNoteSaveCommitted11State {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "committed",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DecisionNoteSaveCommitted11Operation {
    #[serde(rename = "decisionNote.save")]
    Value,
}
impl DecisionNoteSaveCommitted11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "decisionNote.save",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DecisionNoteSaveConfirmationRequired11State {
    #[serde(rename = "confirmation_required")]
    Value,
}
impl DecisionNoteSaveConfirmationRequired11State {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "confirmation_required",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DecisionNoteSaveConfirmationRequired11Operation {
    #[serde(rename = "decisionNote.save")]
    Value,
}
impl DecisionNoteSaveConfirmationRequired11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "decisionNote.save",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DecisionNoteSaveReply11Type {
    #[serde(rename = "response")]
    Value,
}
impl DecisionNoteSaveReply11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DecisionNoteReadIntent11Operation {
    #[serde(rename = "decisionNote.read")]
    Value,
}
impl DecisionNoteReadIntent11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "decisionNote.read",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DecisionNoteReadCommand11Type {
    #[serde(rename = "request")]
    Value,
}
impl DecisionNoteReadCommand11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DecisionNoteReadCompleted11State {
    #[serde(rename = "completed")]
    Value,
}
impl DecisionNoteReadCompleted11State {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "completed",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DecisionNoteReadCompleted11Operation {
    #[serde(rename = "decisionNote.read")]
    Value,
}
impl DecisionNoteReadCompleted11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "decisionNote.read",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DecisionNoteReadReply11Type {
    #[serde(rename = "response")]
    Value,
}
impl DecisionNoteReadReply11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DecisionNoteAppendOutcomeIntent11Operation {
    #[serde(rename = "decisionNote.appendOutcome")]
    Value,
}
impl DecisionNoteAppendOutcomeIntent11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "decisionNote.appendOutcome",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DecisionNoteAppendOutcomeCommand11Type {
    #[serde(rename = "request")]
    Value,
}
impl DecisionNoteAppendOutcomeCommand11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DecisionNoteAppendOutcomeCommitted11State {
    #[serde(rename = "committed")]
    Value,
}
impl DecisionNoteAppendOutcomeCommitted11State {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "committed",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DecisionNoteAppendOutcomeCommitted11Operation {
    #[serde(rename = "decisionNote.appendOutcome")]
    Value,
}
impl DecisionNoteAppendOutcomeCommitted11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "decisionNote.appendOutcome",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DecisionNoteAppendOutcomeConfirmationRequired11State {
    #[serde(rename = "confirmation_required")]
    Value,
}
impl DecisionNoteAppendOutcomeConfirmationRequired11State {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "confirmation_required",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DecisionNoteAppendOutcomeConfirmationRequired11Operation {
    #[serde(rename = "decisionNote.appendOutcome")]
    Value,
}
impl DecisionNoteAppendOutcomeConfirmationRequired11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "decisionNote.appendOutcome",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DecisionNoteAppendOutcomeReply11Type {
    #[serde(rename = "response")]
    Value,
}
impl DecisionNoteAppendOutcomeReply11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DecisionNoteDeleteIntent11Operation {
    #[serde(rename = "decisionNote.delete")]
    Value,
}
impl DecisionNoteDeleteIntent11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "decisionNote.delete",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DecisionNoteDeleteCommand11Type {
    #[serde(rename = "request")]
    Value,
}
impl DecisionNoteDeleteCommand11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DecisionNoteDeleteCommitted11State {
    #[serde(rename = "committed")]
    Value,
}
impl DecisionNoteDeleteCommitted11State {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "committed",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DecisionNoteDeleteCommitted11Operation {
    #[serde(rename = "decisionNote.delete")]
    Value,
}
impl DecisionNoteDeleteCommitted11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "decisionNote.delete",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DecisionNoteDeleteConfirmationRequired11State {
    #[serde(rename = "confirmation_required")]
    Value,
}
impl DecisionNoteDeleteConfirmationRequired11State {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "confirmation_required",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DecisionNoteDeleteConfirmationRequired11Operation {
    #[serde(rename = "decisionNote.delete")]
    Value,
}
impl DecisionNoteDeleteConfirmationRequired11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "decisionNote.delete",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DecisionNoteDeleteReply11Type {
    #[serde(rename = "response")]
    Value,
}
impl DecisionNoteDeleteReply11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DecisionNoteBackupV1Format {
    #[serde(rename = "scopehub.decision-notes")]
    Value,
}
impl DecisionNoteBackupV1Format {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "scopehub.decision-notes",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DecisionNoteRestoreBackupInputDecision {
    #[serde(rename = "approve")]
    Approve,
    #[serde(rename = "decline")]
    Decline,
}
impl DecisionNoteRestoreBackupInputDecision {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Approve => "approve",
            Self::Decline => "decline",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DecisionNoteExportBackupIntent11Operation {
    #[serde(rename = "decisionNote.exportBackup")]
    Value,
}
impl DecisionNoteExportBackupIntent11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "decisionNote.exportBackup",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DecisionNoteExportBackupCommand11Type {
    #[serde(rename = "request")]
    Value,
}
impl DecisionNoteExportBackupCommand11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DecisionNoteExportBackupCompleted11State {
    #[serde(rename = "completed")]
    Value,
}
impl DecisionNoteExportBackupCompleted11State {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "completed",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DecisionNoteExportBackupCompleted11Operation {
    #[serde(rename = "decisionNote.exportBackup")]
    Value,
}
impl DecisionNoteExportBackupCompleted11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "decisionNote.exportBackup",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DecisionNoteExportBackupReply11Type {
    #[serde(rename = "response")]
    Value,
}
impl DecisionNoteExportBackupReply11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DecisionNotePreviewRestoreIntent11Operation {
    #[serde(rename = "decisionNote.previewRestore")]
    Value,
}
impl DecisionNotePreviewRestoreIntent11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "decisionNote.previewRestore",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DecisionNotePreviewRestoreCommand11Type {
    #[serde(rename = "request")]
    Value,
}
impl DecisionNotePreviewRestoreCommand11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DecisionNotePreviewRestoreConfirmationRequired11State {
    #[serde(rename = "confirmation_required")]
    Value,
}
impl DecisionNotePreviewRestoreConfirmationRequired11State {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "confirmation_required",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DecisionNotePreviewRestoreConfirmationRequired11Operation {
    #[serde(rename = "decisionNote.previewRestore")]
    Value,
}
impl DecisionNotePreviewRestoreConfirmationRequired11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "decisionNote.previewRestore",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DecisionNotePreviewRestoreReply11Type {
    #[serde(rename = "response")]
    Value,
}
impl DecisionNotePreviewRestoreReply11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DecisionNoteRestoreBackupIntent11Operation {
    #[serde(rename = "decisionNote.restoreBackup")]
    Value,
}
impl DecisionNoteRestoreBackupIntent11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "decisionNote.restoreBackup",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DecisionNoteRestoreBackupCommand11Type {
    #[serde(rename = "request")]
    Value,
}
impl DecisionNoteRestoreBackupCommand11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DecisionNoteRestoreBackupCommitted11State {
    #[serde(rename = "committed")]
    Value,
}
impl DecisionNoteRestoreBackupCommitted11State {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "committed",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DecisionNoteRestoreBackupCommitted11Operation {
    #[serde(rename = "decisionNote.restoreBackup")]
    Value,
}
impl DecisionNoteRestoreBackupCommitted11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "decisionNote.restoreBackup",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DecisionNoteRestoreBackupReply11Type {
    #[serde(rename = "response")]
    Value,
}
impl DecisionNoteRestoreBackupReply11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum FolderRefreshFileChange {
    #[serde(rename = "unchanged")]
    Unchanged,
    #[serde(rename = "added")]
    Added,
    #[serde(rename = "replaced")]
    Replaced,
    #[serde(rename = "removed")]
    Removed,
    #[serde(rename = "unverified")]
    Unverified,
}
impl FolderRefreshFileChange {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Unchanged => "unchanged",
            Self::Added => "added",
            Self::Replaced => "replaced",
            Self::Removed => "removed",
            Self::Unverified => "unverified",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum FolderRefreshFileReview {
    #[serde(rename = "compatible")]
    Compatible,
    #[serde(rename = "header_changed")]
    HeaderChanged,
    #[serde(rename = "series_mismatch")]
    SeriesMismatch,
    #[serde(rename = "invalid_rows")]
    InvalidRows,
    #[serde(rename = "unavailable")]
    Unavailable,
    #[serde(rename = "removed")]
    Removed,
}
impl FolderRefreshFileReview {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Compatible => "compatible",
            Self::HeaderChanged => "header_changed",
            Self::SeriesMismatch => "series_mismatch",
            Self::InvalidRows => "invalid_rows",
            Self::Unavailable => "unavailable",
            Self::Removed => "removed",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum FolderRefreshBarStatus {
    #[serde(rename = "unchanged")]
    Unchanged,
    #[serde(rename = "addition")]
    Addition,
    #[serde(rename = "conflict")]
    Conflict,
    #[serde(rename = "withdrawal")]
    Withdrawal,
    #[serde(rename = "unverified")]
    Unverified,
}
impl FolderRefreshBarStatus {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Unchanged => "unchanged",
            Self::Addition => "addition",
            Self::Conflict => "conflict",
            Self::Withdrawal => "withdrawal",
            Self::Unverified => "unverified",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum LayoutPanel11Panel {
    #[serde(rename = "watchlist")]
    Watchlist,
    #[serde(rename = "inspection")]
    Inspection,
}
impl LayoutPanel11Panel {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Watchlist => "watchlist",
            Self::Inspection => "inspection",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum LayoutPanel11Dock {
    #[serde(rename = "left")]
    Left,
    #[serde(rename = "right")]
    Right,
    #[serde(rename = "bottom")]
    Bottom,
}
impl LayoutPanel11Dock {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Left => "left",
            Self::Right => "right",
            Self::Bottom => "bottom",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum WorkspaceLayout11Theme {
    #[serde(rename = "dark")]
    Dark,
    #[serde(rename = "light")]
    Light,
}
impl WorkspaceLayout11Theme {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Dark => "dark",
            Self::Light => "light",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum SaveWorkspaceLayout11Action {
    #[serde(rename = "save")]
    Value,
}
impl SaveWorkspaceLayout11Action {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "save",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum OpenWorkspaceLayout11Action {
    #[serde(rename = "open")]
    Value,
}
impl OpenWorkspaceLayout11Action {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "open",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ResetWorkspaceLayout11Action {
    #[serde(rename = "reset")]
    Value,
}
impl ResetWorkspaceLayout11Action {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "reset",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum WorkspacePreviewLayoutCommand11Type {
    #[serde(rename = "request")]
    Value,
}
impl WorkspacePreviewLayoutCommand11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum WorkspacePreviewLayoutReply11Type {
    #[serde(rename = "response")]
    Value,
}
impl WorkspacePreviewLayoutReply11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum WorkspaceApplyLayoutCommand11Type {
    #[serde(rename = "request")]
    Value,
}
impl WorkspaceApplyLayoutCommand11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum WorkspaceApplyLayoutReply11Type {
    #[serde(rename = "response")]
    Value,
}
impl WorkspaceApplyLayoutReply11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AiTextOllamaChoiceKind {
    #[serde(rename = "ollama")]
    Value,
}
impl AiTextOllamaChoiceKind {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "ollama",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AiTextOfflineMockChoiceKind {
    #[serde(rename = "offline_mock")]
    Value,
}
impl AiTextOfflineMockChoiceKind {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "offline_mock",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AiTextOpenAiChoiceKind {
    #[serde(rename = "openai_responses")]
    Value,
}
impl AiTextOpenAiChoiceKind {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "openai_responses",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AiTextOllamaRouteKind {
    #[serde(rename = "ollama")]
    Value,
}
impl AiTextOllamaRouteKind {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "ollama",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AiTextOllamaRouteScope {
    #[serde(rename = "conversation_text")]
    ConversationText,
    #[serde(rename = "conversation_text_and_sample_closes")]
    ConversationTextAndSampleCloses,
}
impl AiTextOllamaRouteScope {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::ConversationText => "conversation_text",
            Self::ConversationTextAndSampleCloses => "conversation_text_and_sample_closes",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AiTextOllamaRouteChargeBasis {
    #[serde(rename = "endpoint_operator")]
    Value,
}
impl AiTextOllamaRouteChargeBasis {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "endpoint_operator",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AiTextOfflineMockRouteKind {
    #[serde(rename = "offline_mock")]
    Value,
}
impl AiTextOfflineMockRouteKind {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "offline_mock",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AiTextOfflineMockRouteDestination {
    #[serde(rename = "This app (offline)")]
    Value,
}
impl AiTextOfflineMockRouteDestination {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "This app (offline)",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AiTextOfflineMockRouteModel {
    #[serde(rename = "ScopeHub offline mock")]
    Value,
}
impl AiTextOfflineMockRouteModel {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "ScopeHub offline mock",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AiTextOfflineMockRouteScope {
    #[serde(rename = "conversation_text")]
    Value,
}
impl AiTextOfflineMockRouteScope {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "conversation_text",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AiTextOfflineMockRouteChargeBasis {
    #[serde(rename = "none")]
    Value,
}
impl AiTextOfflineMockRouteChargeBasis {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "none",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AiTextOpenAiRouteKind {
    #[serde(rename = "openai_responses")]
    Value,
}
impl AiTextOpenAiRouteKind {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "openai_responses",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AiTextOpenAiRouteDestination {
    #[serde(rename = "https://api.openai.com/v1/responses")]
    Value,
}
impl AiTextOpenAiRouteDestination {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "https://api.openai.com/v1/responses",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AiTextOpenAiRouteScope {
    #[serde(rename = "conversation_text")]
    Value,
}
impl AiTextOpenAiRouteScope {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "conversation_text",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AiTextOpenAiRouteChargeBasis {
    #[serde(rename = "openai_api_account")]
    Value,
}
impl AiTextOpenAiRouteChargeBasis {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "openai_api_account",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AiTextRunningTurnState {
    #[serde(rename = "running")]
    Value,
}
impl AiTextRunningTurnState {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "running",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AiTextCompletedTurnState {
    #[serde(rename = "completed")]
    Value,
}
impl AiTextCompletedTurnState {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "completed",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AiTextFailedTurnState {
    #[serde(rename = "failed")]
    Value,
}
impl AiTextFailedTurnState {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "failed",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AiTextStatusInputAction {
    #[serde(rename = "status")]
    Value,
}
impl AiTextStatusInputAction {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "status",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AiTextProposeInputAction {
    #[serde(rename = "propose")]
    Value,
}
impl AiTextProposeInputAction {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "propose",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AiTextActivateInputAction {
    #[serde(rename = "activate")]
    Value,
}
impl AiTextActivateInputAction {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "activate",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AiTextDisconnectInputAction {
    #[serde(rename = "disconnect")]
    Value,
}
impl AiTextDisconnectInputAction {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "disconnect",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AiTextSendInputAction {
    #[serde(rename = "send")]
    Value,
}
impl AiTextSendInputAction {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "send",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AiTextPollInputAction {
    #[serde(rename = "poll")]
    Value,
}
impl AiTextPollInputAction {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "poll",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AiTextCancelInputAction {
    #[serde(rename = "cancel")]
    Value,
}
impl AiTextCancelInputAction {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "cancel",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AiTextStatusOutputAction {
    #[serde(rename = "status")]
    Value,
}
impl AiTextStatusOutputAction {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "status",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AiTextProposeOutputAction {
    #[serde(rename = "propose")]
    Value,
}
impl AiTextProposeOutputAction {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "propose",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AiTextActivateOutputAction {
    #[serde(rename = "activate")]
    Value,
}
impl AiTextActivateOutputAction {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "activate",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AiTextDisconnectOutputAction {
    #[serde(rename = "disconnect")]
    Value,
}
impl AiTextDisconnectOutputAction {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "disconnect",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AiTextSendOutputAction {
    #[serde(rename = "send")]
    Value,
}
impl AiTextSendOutputAction {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "send",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AiTextPollOutputAction {
    #[serde(rename = "poll")]
    Value,
}
impl AiTextPollOutputAction {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "poll",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AiTextCancelOutputAction {
    #[serde(rename = "cancel")]
    Value,
}
impl AiTextCancelOutputAction {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "cancel",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AiTextConnectionCommand11Type {
    #[serde(rename = "request")]
    Value,
}
impl AiTextConnectionCommand11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "request",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AiTextConnectionReply11Type {
    #[serde(rename = "response")]
    Value,
}
impl AiTextConnectionReply11Type {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "response",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AiTextConnectionIntent11Operation {
    #[serde(rename = "ai.textConnection")]
    Value,
}
impl AiTextConnectionIntent11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "ai.textConnection",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AiTextConnectionCompleted11State {
    #[serde(rename = "completed")]
    Value,
}
impl AiTextConnectionCompleted11State {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "completed",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AiTextConnectionCompleted11Operation {
    #[serde(rename = "ai.textConnection")]
    Value,
}
impl AiTextConnectionCompleted11Operation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "ai.textConnection",
        }
    }
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum SavedSeriesDrawing11Api {
    #[serde(rename = "series-drawing-1")]
    Value,
}
impl SavedSeriesDrawing11Api {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Value => "series-drawing-1",
        }
    }
}
