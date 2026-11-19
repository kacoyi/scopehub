// Generated from contracts/schemas/protocol.schema.json; sha256=00d5588197a5635a218a3f20f558086ee5336b4d0663fe40557e3abe3a6b950a. Do not edit.
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
    #[serde(rename = "workspace.readChartProjection")]
    WorkspaceReadChartProjection,
    #[serde(rename = "workspace.selectEvidence")]
    WorkspaceSelectEvidence,
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
            Self::WorkspaceReadChartProjection => "workspace.readChartProjection",
            Self::WorkspaceSelectEvidence => "workspace.selectEvidence",
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
