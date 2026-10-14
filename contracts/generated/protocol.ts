// Generated from contracts/schemas/protocol.schema.json; sha256=7d5aeb935ac213ef7068481ce24b63fdbd75b882c0e6f348025408473fca7584. Do not edit.
export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };
export type OpaqueId = string;
export type Uuid = string;
export type Revision = number;
export type Timestamp = number;
export type Metadata = Record<string, JsonValue>;
export type WorkerRole = "market" | "ai" | "indicator";
export type ProtocolVersion = "1.0";
export type ErrorCode = "INVALID_ARGUMENT" | "UNSUPPORTED_CAPABILITY" | "PERMISSION_DENIED" | "CONFIRMATION_REQUIRED" | "CANCELLED" | "TIMEOUT" | "WORKER_EXITED" | "PROTOCOL_MISMATCH" | "DATA_UNAVAILABLE" | "STALE_CONTEXT" | "RESOURCE_LIMIT" | "OUTPUT_INVALID" | "PROVIDER_NOT_CONFIGURED" | "INTERNAL_ERROR";
export type OperationName = "instrument.search" | "market.getBars" | "workspace.openInstrument" | "chart.setTimeframe" | "chart.setStyle" | "chart.addAnnotation" | "chart.removeLayer" | "indicator.createDraft" | "indicator.runPreview" | "indicator.acceptVersion" | "indicator.remove" | "workspace.undo" | "task.cancel" | "workspace.getContext";
export type ChartStyle = "candlestick" | "line";
export type TaskStatus = "queued" | "running" | "awaiting_confirmation" | "completed" | "failed" | "cancelled";
export type Tradability = "non_tradable" | "unknown";
export type DataMode = "simulated" | "historical" | "delayed" | "realtime";
export type DataQuality = "fresh" | "stale" | "gapped" | "degraded";
export type ProviderMapping = {
  provider_id: OpaqueId;
  provider_symbol: string;
};
export type Instrument = {
  id: OpaqueId;
  display_symbol: string;
  name: string;
  asset_class: string;
  venue: string;
  currency: string;
  calendar_id: OpaqueId;
  timezone: string;
  price_tick: (string | null);
  quantity_unit: string;
  tradability: Tradability;
  metadata: Metadata;
  provider_mappings: Array<ProviderMapping>;
};
export type DatasetDescriptor = {
  id: Uuid;
  instrument_id: OpaqueId;
  provider_id: OpaqueId;
  mode: DataMode;
  timeframe: string;
  price_adjustment: string;
  timezone: string;
  calendar_id: OpaqueId;
  quantity_unit: string;
  revision: string;
  quality: DataQuality;
  provenance: Metadata;
};
export type Bar = {
  time_ms: Timestamp;
  trading_day: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: (number | null);
  is_closed: boolean;
};
export type Error = {
  code: ErrorCode;
  message: string;
  retryable: boolean;
  details: Metadata;
};
export type Point = {
  time_ms: Timestamp;
  value: (number | null);
};
export type Style = {
  color: string;
  width: number;
};
export type MarkerPosition = "above" | "below" | "on";
export type Marker = {
  time_ms: Timestamp;
  position: MarkerPosition;
  label: string;
  color: string;
};
export type Region = {
  from_ms: Timestamp;
  to_ms: Timestamp;
  label: string;
  color: string;
};
export type Annotation = Marker | Region;
export type SeriesKind = "line" | "histogram";
export type IndicatorSeries = {
  id: OpaqueId;
  pane: string;
  type: SeriesKind;
  points: Array<Point>;
  style: Style;
};
export type Severity = "info" | "warning" | "error";
export type Diagnostic = {
  code: string;
  message: string;
  severity: Severity;
};
export type Hash = string;
export type IndicatorProvenance = {
  code_hash: Hash;
  params_hash: Hash;
  dataset_id: Uuid;
  dataset_revision: string;
  engine_version: string;
};
export type IndicatorResult = {
  series: Array<IndicatorSeries>;
  markers: Array<Marker>;
  regions: Array<Region>;
  diagnostics: Array<Diagnostic>;
  provenance: IndicatorProvenance;
};
export type LayerState = "preview" | "accepted";
export type Layer = {
  id: OpaqueId;
  state: LayerState;
  source_task_id: (Uuid | null);
  indicator_version_id: (OpaqueId | null);
  metadata: Metadata;
};
export type VisibleRange = {
  from_ms: Timestamp;
  to_ms: Timestamp;
};
export type Workspace = {
  id: OpaqueId;
  revision: Revision;
  instrument_id: (OpaqueId | null);
  timeframe: string;
  style: ChartStyle;
  dataset: (DatasetDescriptor | null);
  visible_range: (VisibleRange | null);
  layers: Array<Layer>;
};
export type Context = {
  workspace: Workspace;
  authorized_operations: Array<OperationName>;
  bars: Array<Bar>;
};
export type FieldExpression = {
  op: "field";
  field: "open" | "high" | "low" | "close" | "volume";
};
export type ConstantExpression = {
  op: "constant";
  value: number;
};
export type BinaryExpression = {
  op: "add" | "subtract" | "multiply" | "divide" | "gt" | "lt" | "and" | "or";
  left: Expression;
  right: Expression;
};
export type AbsExpression = {
  op: "abs";
  value: Expression;
};
export type LagExpression = {
  op: "lag";
  value: Expression;
  periods: number;
};
export type WindowExpression = {
  op: "rolling_mean" | "rolling_std";
  value: Expression;
  window: number;
};
export type Expression = FieldExpression | ConstantExpression | BinaryExpression | AbsExpression | LagExpression | WindowExpression;
export type DeclarativeDraftInput = {
  language: "declarative";
  source: Expression;
  params: Metadata;
  expected_workspace_revision: Revision;
};
export type PythonDraftInput = {
  language: "python";
  source: string;
  params: Metadata;
  expected_workspace_revision: Revision;
};
export type InstrumentSearchInput = {
  query: string;
  limit: number;
};
export type InstrumentSearchOutput = {
  instruments: Array<Instrument>;
};
export type InstrumentSearchRequest = {
  protocol_version: ProtocolVersion;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: Uuid;
  task_id: (Uuid | null);
  operation: "instrument.search";
  payload: InstrumentSearchInput;
};
export type InstrumentSearchToolCall = {
  operation: "instrument.search";
  arguments: InstrumentSearchInput;
};
export type MarketGetBarsInput = {
  instrument_id: OpaqueId;
  timeframe: string;
  from_ms: Timestamp;
  to_ms: Timestamp;
  provider_id: OpaqueId;
};
export type MarketGetBarsOutput = {
  dataset: DatasetDescriptor;
  bars: Array<Bar>;
};
export type MarketGetBarsRequest = {
  protocol_version: ProtocolVersion;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: Uuid;
  task_id: (Uuid | null);
  operation: "market.getBars";
  payload: MarketGetBarsInput;
};
export type MarketGetBarsToolCall = {
  operation: "market.getBars";
  arguments: MarketGetBarsInput;
};
export type WorkspaceOpenInstrumentInput = {
  instrument_id: OpaqueId;
  timeframe: string;
  expected_workspace_revision: Revision;
};
export type WorkspaceOpenInstrumentOutput = Workspace;
export type WorkspaceOpenInstrumentRequest = {
  protocol_version: ProtocolVersion;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: Uuid;
  task_id: (Uuid | null);
  operation: "workspace.openInstrument";
  payload: WorkspaceOpenInstrumentInput;
};
export type WorkspaceOpenInstrumentToolCall = {
  operation: "workspace.openInstrument";
  arguments: WorkspaceOpenInstrumentInput;
};
export type ChartSetTimeframeInput = {
  timeframe: string;
  expected_workspace_revision: Revision;
};
export type ChartSetTimeframeOutput = Workspace;
export type ChartSetTimeframeRequest = {
  protocol_version: ProtocolVersion;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: Uuid;
  task_id: (Uuid | null);
  operation: "chart.setTimeframe";
  payload: ChartSetTimeframeInput;
};
export type ChartSetTimeframeToolCall = {
  operation: "chart.setTimeframe";
  arguments: ChartSetTimeframeInput;
};
export type ChartSetStyleInput = {
  style: ChartStyle;
  expected_workspace_revision: Revision;
};
export type ChartSetStyleOutput = Workspace;
export type ChartSetStyleRequest = {
  protocol_version: ProtocolVersion;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: Uuid;
  task_id: (Uuid | null);
  operation: "chart.setStyle";
  payload: ChartSetStyleInput;
};
export type ChartSetStyleToolCall = {
  operation: "chart.setStyle";
  arguments: ChartSetStyleInput;
};
export type ChartAddAnnotationInput = {
  annotation: Annotation;
  expected_workspace_revision: Revision;
};
export type ChartAddAnnotationOutput = {
  layer_id: OpaqueId;
};
export type ChartAddAnnotationRequest = {
  protocol_version: ProtocolVersion;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: Uuid;
  task_id: (Uuid | null);
  operation: "chart.addAnnotation";
  payload: ChartAddAnnotationInput;
};
export type ChartAddAnnotationToolCall = {
  operation: "chart.addAnnotation";
  arguments: ChartAddAnnotationInput;
};
export type ChartRemoveLayerInput = {
  layer_id: OpaqueId;
  expected_workspace_revision: Revision;
};
export type ChartRemoveLayerOutput = {
  operation_id: OpaqueId;
};
export type ChartRemoveLayerRequest = {
  protocol_version: ProtocolVersion;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: Uuid;
  task_id: (Uuid | null);
  operation: "chart.removeLayer";
  payload: ChartRemoveLayerInput;
};
export type ChartRemoveLayerToolCall = {
  operation: "chart.removeLayer";
  arguments: ChartRemoveLayerInput;
};
export type IndicatorCreateDraftInput = DeclarativeDraftInput | PythonDraftInput;
export type IndicatorCreateDraftOutput = {
  draft_id: OpaqueId;
  code_hash: Hash;
};
export type IndicatorCreateDraftRequest = {
  protocol_version: ProtocolVersion;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: Uuid;
  task_id: (Uuid | null);
  operation: "indicator.createDraft";
  payload: IndicatorCreateDraftInput;
};
export type IndicatorCreateDraftToolCall = {
  operation: "indicator.createDraft";
  arguments: IndicatorCreateDraftInput;
};
export type IndicatorRunPreviewInput = {
  draft_id: OpaqueId;
  dataset_id: Uuid;
  dataset_revision: string;
  params: Metadata;
  expected_workspace_revision: Revision;
};
export type IndicatorRunPreviewOutput = {
  task_id: Uuid;
};
export type IndicatorRunPreviewRequest = {
  protocol_version: ProtocolVersion;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: Uuid;
  task_id: (Uuid | null);
  operation: "indicator.runPreview";
  payload: IndicatorRunPreviewInput;
};
export type IndicatorRunPreviewToolCall = {
  operation: "indicator.runPreview";
  arguments: IndicatorRunPreviewInput;
};
export type IndicatorAcceptVersionInput = {
  preview_id: OpaqueId;
  expected_workspace_revision: Revision;
};
export type IndicatorAcceptVersionOutput = {
  version_id: OpaqueId;
  layer_id: OpaqueId;
};
export type IndicatorAcceptVersionRequest = {
  protocol_version: ProtocolVersion;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: Uuid;
  task_id: (Uuid | null);
  operation: "indicator.acceptVersion";
  payload: IndicatorAcceptVersionInput;
};
export type IndicatorAcceptVersionToolCall = {
  operation: "indicator.acceptVersion";
  arguments: IndicatorAcceptVersionInput;
};
export type IndicatorRemoveInput = {
  layer_id: OpaqueId;
  expected_workspace_revision: Revision;
};
export type IndicatorRemoveOutput = {
  operation_id: OpaqueId;
};
export type IndicatorRemoveRequest = {
  protocol_version: ProtocolVersion;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: Uuid;
  task_id: (Uuid | null);
  operation: "indicator.remove";
  payload: IndicatorRemoveInput;
};
export type IndicatorRemoveToolCall = {
  operation: "indicator.remove";
  arguments: IndicatorRemoveInput;
};
export type WorkspaceUndoInput = {
  operation_id: OpaqueId;
  expected_workspace_revision: Revision;
};
export type WorkspaceUndoOutput = Workspace;
export type WorkspaceUndoRequest = {
  protocol_version: ProtocolVersion;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: Uuid;
  task_id: (Uuid | null);
  operation: "workspace.undo";
  payload: WorkspaceUndoInput;
};
export type WorkspaceUndoToolCall = {
  operation: "workspace.undo";
  arguments: WorkspaceUndoInput;
};
export type TaskCancelInput = {
  task_id: Uuid;
};
export type TaskCancelOutput = {
  task_id: Uuid;
  status: "completed" | "failed" | "cancelled";
};
export type TaskCancelRequest = {
  protocol_version: ProtocolVersion;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: Uuid;
  task_id: (Uuid | null);
  operation: "task.cancel";
  payload: TaskCancelInput;
};
export type TaskCancelToolCall = {
  operation: "task.cancel";
  arguments: TaskCancelInput;
};
export type WorkspaceGetContextInput = {
  workspace_id: OpaqueId;
};
export type WorkspaceGetContextOutput = Context;
export type WorkspaceGetContextRequest = {
  protocol_version: ProtocolVersion;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: Uuid;
  task_id: (Uuid | null);
  operation: "workspace.getContext";
  payload: WorkspaceGetContextInput;
};
export type WorkspaceGetContextToolCall = {
  operation: "workspace.getContext";
  arguments: WorkspaceGetContextInput;
};
export type Request = InstrumentSearchRequest | MarketGetBarsRequest | WorkspaceOpenInstrumentRequest | ChartSetTimeframeRequest | ChartSetStyleRequest | ChartAddAnnotationRequest | ChartRemoveLayerRequest | IndicatorCreateDraftRequest | IndicatorRunPreviewRequest | IndicatorAcceptVersionRequest | IndicatorRemoveRequest | WorkspaceUndoRequest | TaskCancelRequest | WorkspaceGetContextRequest;
export type ToolCall = InstrumentSearchToolCall | MarketGetBarsToolCall | WorkspaceOpenInstrumentToolCall | ChartSetTimeframeToolCall | ChartSetStyleToolCall | ChartAddAnnotationToolCall | ChartRemoveLayerToolCall | IndicatorCreateDraftToolCall | IndicatorRunPreviewToolCall | IndicatorAcceptVersionToolCall | IndicatorRemoveToolCall | WorkspaceUndoToolCall | TaskCancelToolCall | WorkspaceGetContextToolCall;
export type Hello = {
  protocol_version: ProtocolVersion;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "hello";
  role: WorkerRole;
  worker_version: string;
  supported_protocols: Array<string>;
  capabilities: Array<OperationName>;
};
export type ErrorResponse = {
  protocol_version: ProtocolVersion;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  ok: false;
  result: null;
  error: Error;
};
export type OperationResult = InstrumentSearchOutput | MarketGetBarsOutput | WorkspaceOpenInstrumentOutput | ChartSetTimeframeOutput | ChartSetStyleOutput | ChartAddAnnotationOutput | ChartRemoveLayerOutput | IndicatorCreateDraftOutput | IndicatorRunPreviewOutput | IndicatorAcceptVersionOutput | IndicatorRemoveOutput | WorkspaceUndoOutput | TaskCancelOutput | WorkspaceGetContextOutput;
export type SuccessResponse = {
  protocol_version: ProtocolVersion;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  ok: true;
  result: OperationResult;
  error: null;
};
export type Response = SuccessResponse | ErrorResponse;
export type AiToolRequestEvent = {
  protocol_version: ProtocolVersion;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "event";
  stream_id: string;
  sequence: number;
  event: "ai.tool_request";
  payload: ToolCall;
};
export type Cancel = {
  protocol_version: ProtocolVersion;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "cancel";
  request_id: Uuid;
};
export type Heartbeat = {
  protocol_version: ProtocolVersion;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "heartbeat";
};
export type Message = Hello | Request | Response | AiToolRequestEvent | Cancel | Heartbeat;
