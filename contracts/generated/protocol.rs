// Generated from contracts/schemas/protocol.schema.json; sha256=7d5aeb935ac213ef7068481ce24b63fdbd75b882c0e6f348025408473fca7584. Do not edit.
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
    pub authorized_operations: Vec<OperationName>,
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
    pub capabilities: Vec<OperationName>,
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
#[serde(untagged)]
pub enum Message {
    Hello(Box<Hello>),
    Request(Box<Request>),
    Response(Box<Response>),
    AiToolRequestEvent(Box<AiToolRequestEvent>),
    Cancel(Box<Cancel>),
    Heartbeat(Box<Heartbeat>),
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
