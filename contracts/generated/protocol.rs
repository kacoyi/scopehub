// Generated from contracts/schemas/protocol.schema.json; sha256=7d5aeb935ac213ef7068481ce24b63fdbd75b882c0e6f348025408473fca7584. Do not edit.
// Structural types only: validate JSON Schema before deserializing.
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
pub type ProtocolVersion = String;
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
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ChartStyle {
    #[serde(rename = "candlestick")]
    Candlestick,
    #[serde(rename = "line")]
    Line,
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
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum Tradability {
    #[serde(rename = "non_tradable")]
    NonTradable,
    #[serde(rename = "unknown")]
    Unknown,
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
    pub op: String,
    pub field: String,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ConstantExpression {
    pub op: String,
    pub value: f64,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BinaryExpression {
    pub op: String,
    pub left: Expression,
    pub right: Expression,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AbsExpression {
    pub op: String,
    pub value: Expression,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LagExpression {
    pub op: String,
    pub value: Expression,
    pub periods: u64,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WindowExpression {
    pub op: String,
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
    pub language: String,
    pub source: Expression,
    pub params: Metadata,
    pub expected_workspace_revision: Revision,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PythonDraftInput {
    pub language: String,
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
    pub r#type: String,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub operation: String,
    pub payload: InstrumentSearchInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct InstrumentSearchToolCall {
    pub operation: String,
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
    pub r#type: String,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub operation: String,
    pub payload: MarketGetBarsInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MarketGetBarsToolCall {
    pub operation: String,
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
    pub r#type: String,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub operation: String,
    pub payload: WorkspaceOpenInstrumentInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceOpenInstrumentToolCall {
    pub operation: String,
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
    pub r#type: String,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub operation: String,
    pub payload: ChartSetTimeframeInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChartSetTimeframeToolCall {
    pub operation: String,
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
    pub r#type: String,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub operation: String,
    pub payload: ChartSetStyleInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChartSetStyleToolCall {
    pub operation: String,
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
    pub r#type: String,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub operation: String,
    pub payload: ChartAddAnnotationInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChartAddAnnotationToolCall {
    pub operation: String,
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
    pub r#type: String,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub operation: String,
    pub payload: ChartRemoveLayerInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChartRemoveLayerToolCall {
    pub operation: String,
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
    pub r#type: String,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub operation: String,
    pub payload: IndicatorCreateDraftInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IndicatorCreateDraftToolCall {
    pub operation: String,
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
    pub r#type: String,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub operation: String,
    pub payload: IndicatorRunPreviewInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IndicatorRunPreviewToolCall {
    pub operation: String,
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
    pub r#type: String,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub operation: String,
    pub payload: IndicatorAcceptVersionInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IndicatorAcceptVersionToolCall {
    pub operation: String,
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
    pub r#type: String,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub operation: String,
    pub payload: IndicatorRemoveInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IndicatorRemoveToolCall {
    pub operation: String,
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
    pub r#type: String,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub operation: String,
    pub payload: WorkspaceUndoInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceUndoToolCall {
    pub operation: String,
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
    pub status: String,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TaskCancelRequest {
    pub protocol_version: ProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: String,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub operation: String,
    pub payload: TaskCancelInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TaskCancelToolCall {
    pub operation: String,
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
    pub r#type: String,
    pub request_id: Uuid,
    pub task_id: Option<Uuid>,
    pub operation: String,
    pub payload: WorkspaceGetContextInput,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceGetContextToolCall {
    pub operation: String,
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
    pub r#type: String,
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
    pub r#type: String,
    pub request_id: Uuid,
    pub ok: bool,
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
    pub r#type: String,
    pub request_id: Uuid,
    pub ok: bool,
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
    pub r#type: String,
    pub stream_id: String,
    pub sequence: u64,
    pub event: String,
    pub payload: ToolCall,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Cancel {
    pub protocol_version: ProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: String,
    pub request_id: Uuid,
}
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Heartbeat {
    pub protocol_version: ProtocolVersion,
    pub message_id: Uuid,
    pub timestamp_ms: Timestamp,
    pub r#type: String,
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
