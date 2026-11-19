// Generated from contracts/schemas/protocol.schema.json; sha256=165474d03e9c67983014341715fa9f74707123ee613aab05cc5603c42ca35c5c. Do not edit.
export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };
export type OpaqueId = string;
export type Uuid = string;
export type Revision = number;
export type Timestamp = number;
export type Metadata = Record<string, JsonValue>;
export type WorkerRole = "market" | "ai" | "indicator";
export type ProtocolVersion = "1.0";
export type ErrorCode = "INVALID_ARGUMENT" | "UNSUPPORTED_CAPABILITY" | "PERMISSION_DENIED" | "CONFIRMATION_REQUIRED" | "CANCELLED" | "TIMEOUT" | "WORKER_EXITED" | "PROTOCOL_MISMATCH" | "DATA_UNAVAILABLE" | "STALE_CONTEXT" | "RESOURCE_LIMIT" | "OUTPUT_INVALID" | "PROVIDER_NOT_CONFIGURED" | "INTERNAL_ERROR" | "BUSY" | "SNAPSHOT_UNAVAILABLE";
export type OperationName = "instrument.search" | "market.getBars" | "workspace.openInstrument" | "chart.setTimeframe" | "chart.setStyle" | "chart.addAnnotation" | "chart.removeLayer" | "indicator.createDraft" | "indicator.runPreview" | "indicator.acceptVersion" | "indicator.remove" | "workspace.undo" | "task.cancel" | "workspace.getContext" | "market.readHistoryPage" | "market.subscribe" | "market.previewLocalImport" | "market.saveLocalImportMapping" | "market.loadLocalImportMapping" | "market.listLocalImportRevisions" | "market.readLocalImportRevision" | "market.readLocalImportSourceRow" | "market.previewLocalFolderRefresh" | "market.prepareLocalFolderPublication" | "market.publishLocalFolderRefresh" | "market.listLocalFolderRevisions" | "market.readLocalFolderRevision" | "market.readLocalFolderSourceRow" | "market.revokeLocalFolderRevision" | "market.revokeLocalImportRevision" | "market.publishLocalImport" | "workspace.readChartProjection" | "workspace.selectEvidence" | "decisionNote.save" | "decisionNote.read" | "decisionNote.appendOutcome" | "decisionNote.delete" | "decisionNote.exportBackup" | "decisionNote.previewRestore" | "decisionNote.restoreBackup" | "privateRecord.read" | "privateRecord.change" | "privateRecord.exportBackup" | "workspace.previewLayout" | "workspace.applyLayout" | "ai.textConnection";
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
  authorized_operations: Array<LegacyOperationName>;
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
  capabilities: Array<LegacyOperationName>;
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
export type SeriesKey = {
  instrument_id: OpaqueId;
  provider_id: OpaqueId;
  timeframe: OpaqueId;
  price_adjustment: OpaqueId;
  session_scope: OpaqueId;
  calendar_id: OpaqueId;
  quantity_unit: OpaqueId;
};
export type FrozenSnapshot = {
  series: SeriesKey;
  dataset_id: Uuid;
  dataset_revision: OpaqueId;
  from_ms: Timestamp;
  to_ms: Timestamp;
  warmup_bars: number;
  as_of_ms: Timestamp;
  content_hash: Hash;
};
export type HistoryCursor = {
  snapshot_hash: Hash;
  next_index: number;
  prefix_hash: Hash;
  previous_time_ms: Timestamp;
};
export type StreamPosition = {
  stream_id: OpaqueId;
  epoch: OpaqueId;
  sequence: Revision;
};
export type MarketQualityState = {
  as_of_ms: Timestamp;
  last_observed_ms: (Timestamp | null);
  session_state: "open" | "closed" | "unknown";
  freshness: "current" | "delayed" | "stale" | "unknown";
  connection: "offline" | "connected" | "disconnected";
  coverage: "observed" | "gapped" | "unknown";
  gaps: Array<VisibleRange>;
  source_kind: "native_bars" | "snapshot_approximation" | "synthetic";
};
export type MarketHistoryPageInput = {
  series: SeriesKey;
  from_ms: Timestamp;
  to_ms: Timestamp;
  warmup_bars: number;
  as_of_ms?: Timestamp;
  expected_revision?: OpaqueId;
  snapshot?: FrozenSnapshot;
  cursor?: HistoryCursor;
  page_size?: number;
  include_stream_position?: boolean;
};
export type MarketHistoryPageOutput = {
  dataset: DatasetDescriptor;
  bars: Array<Bar>;
  snapshot: FrozenSnapshot;
  quality_state: MarketQualityState;
  stream_position: (StreamPosition | null);
  next_cursor: (HistoryCursor | null);
  total_bars: number;
  offset: number;
};
export type MarketPageFault = {
  code: ErrorCode;
  message: string;
  retryable: boolean;
  details: Metadata;
};
export type MarketHistoryPageRequest = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: Uuid;
  task_id: (Uuid | null);
  operation: "market.readHistoryPage";
  payload: MarketHistoryPageInput;
};
export type MarketHistoryPageResponse = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  operation: "market.readHistoryPage";
  ok: true;
  result: MarketHistoryPageOutput;
  error: null;
};
export type MarketHistoryPageError = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  operation: "market.readHistoryPage";
  ok: false;
  result: null;
  error: MarketPageFault;
};
export type MarketSubscriptionInput = {
  series: SeriesKey;
  after: StreamPosition;
  snapshot: FrozenSnapshot;
};
export type StreamResumeAccepted = {
  status: "accepted";
  from_position: StreamPosition;
};
export type StreamBackfillRequired = {
  status: "backfill_required";
  reason: "epoch_changed" | "history_evicted" | "coverage_unknown" | "sequence_gap";
  recover_from_ms: Timestamp;
};
export type MarketSubscriptionOutput = StreamResumeAccepted | StreamBackfillRequired;
export type MarketSubscriptionRequest = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: Uuid;
  task_id: (Uuid | null);
  operation: "market.subscribe";
  payload: MarketSubscriptionInput;
};
export type MarketSubscriptionResponse = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  operation: "market.subscribe";
  ok: true;
  result: MarketSubscriptionOutput;
  error: null;
};
export type MarketSubscriptionError = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  operation: "market.subscribe";
  ok: false;
  result: null;
  error: MarketPageFault;
};
export type MarketSubscriptionReply11 = MarketSubscriptionResponse | MarketSubscriptionError;
export type MarketStreamBarUpdate = {
  series: SeriesKey;
  dataset_revision: OpaqueId;
  observed_at_ms: Timestamp;
  bar: Bar;
};
export type MarketStreamBarCorrection = {
  series: SeriesKey;
  dataset_revision: OpaqueId;
  observed_at_ms: Timestamp;
  bar: Bar;
  supersedes_revision: OpaqueId;
};
export type MarketStreamGap = {
  series: SeriesKey;
  reason: "sequence_gap" | "overflow" | "epoch_reset" | "source_gap";
  expected_sequence: Revision;
  lost_count: number;
  affected: VisibleRange;
};
export type MarketBarUpdateEvent = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "event";
  stream_id: OpaqueId;
  epoch: OpaqueId;
  sequence: Revision;
  event: "market.bar_update";
  payload: MarketStreamBarUpdate;
};
export type MarketBarCorrectionEvent = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "event";
  stream_id: OpaqueId;
  epoch: OpaqueId;
  sequence: Revision;
  event: "market.bar_correction";
  payload: MarketStreamBarCorrection;
};
export type MarketStreamGapEvent = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "event";
  stream_id: OpaqueId;
  epoch: OpaqueId;
  sequence: Revision;
  event: "market.stream_gap";
  payload: MarketStreamGap;
};
export type LocalImportPreviewRequest = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: Uuid;
  task_id: (Uuid | null);
  operation: "market.previewLocalImport";
  payload: LocalImportPreviewInput;
};
export type LocalImportPreviewResponse = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  operation: "market.previewLocalImport";
  ok: true;
  result: LocalImportPreviewOutput;
  error: null;
};
export type LocalImportPreviewError = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  operation: "market.previewLocalImport";
  ok: false;
  result: null;
  error: MarketPageFault;
};
export type SaveLocalImportMappingRequest = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: Uuid;
  task_id: (Uuid | null);
  operation: "market.saveLocalImportMapping";
  payload: SaveLocalImportMappingInput;
};
export type SaveLocalImportMappingResponse = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  operation: "market.saveLocalImportMapping";
  ok: true;
  result: SaveLocalImportMappingOutput;
  error: null;
};
export type SaveLocalImportMappingError = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  operation: "market.saveLocalImportMapping";
  ok: false;
  result: null;
  error: MarketPageFault;
};
export type LoadLocalImportMappingRequest = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: Uuid;
  task_id: (Uuid | null);
  operation: "market.loadLocalImportMapping";
  payload: LoadLocalImportMappingInput;
};
export type LoadLocalImportMappingResponse = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  operation: "market.loadLocalImportMapping";
  ok: true;
  result: LoadLocalImportMappingOutput;
  error: null;
};
export type LoadLocalImportMappingError = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  operation: "market.loadLocalImportMapping";
  ok: false;
  result: null;
  error: MarketPageFault;
};
export type ListLocalImportRevisionsRequest = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: Uuid;
  task_id: (Uuid | null);
  operation: "market.listLocalImportRevisions";
  payload: ListLocalImportRevisionsInput;
};
export type ListLocalImportRevisionsResponse = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  operation: "market.listLocalImportRevisions";
  ok: true;
  result: ListLocalImportRevisionsOutput;
  error: null;
};
export type ListLocalImportRevisionsError = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  operation: "market.listLocalImportRevisions";
  ok: false;
  result: null;
  error: MarketPageFault;
};
export type ReadLocalImportRevisionRequest = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: Uuid;
  task_id: (Uuid | null);
  operation: "market.readLocalImportRevision";
  payload: ReadLocalImportRevisionInput;
};
export type ReadLocalImportRevisionResponse = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  operation: "market.readLocalImportRevision";
  ok: true;
  result: ReadLocalImportRevisionOutput;
  error: null;
};
export type ReadLocalImportRevisionError = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  operation: "market.readLocalImportRevision";
  ok: false;
  result: null;
  error: MarketPageFault;
};
export type ReadLocalImportSourceRowRequest = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: Uuid;
  task_id: (Uuid | null);
  operation: "market.readLocalImportSourceRow";
  payload: ReadLocalImportSourceRowInput;
};
export type ReadLocalImportSourceRowResponse = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  operation: "market.readLocalImportSourceRow";
  ok: true;
  result: ReadLocalImportSourceRowOutput;
  error: null;
};
export type ReadLocalImportSourceRowError = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  operation: "market.readLocalImportSourceRow";
  ok: false;
  result: null;
  error: MarketPageFault;
};
export type PreviewLocalFolderRefreshRequest = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: Uuid;
  task_id: (Uuid | null);
  operation: "market.previewLocalFolderRefresh";
  payload: PreviewLocalFolderRefreshInput;
};
export type PreviewLocalFolderRefreshResponse = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  operation: "market.previewLocalFolderRefresh";
  ok: true;
  result: PreviewLocalFolderRefreshOutput;
  error: null;
};
export type PreviewLocalFolderRefreshError = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  operation: "market.previewLocalFolderRefresh";
  ok: false;
  result: null;
  error: MarketPageFault;
};
export type PrepareLocalFolderPublicationRequest = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: Uuid;
  task_id: (Uuid | null);
  operation: "market.prepareLocalFolderPublication";
  payload: PrepareLocalFolderPublicationInput;
};
export type PrepareLocalFolderPublicationResponse = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  operation: "market.prepareLocalFolderPublication";
  ok: true;
  result: PrepareLocalFolderPublicationOutput;
  error: null;
};
export type PrepareLocalFolderPublicationError = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  operation: "market.prepareLocalFolderPublication";
  ok: false;
  result: null;
  error: MarketPageFault;
};
export type PublishLocalFolderRefreshRequest = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: Uuid;
  task_id: (Uuid | null);
  operation: "market.publishLocalFolderRefresh";
  payload: PublishLocalFolderRefreshInput;
};
export type PublishLocalFolderRefreshResponse = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  operation: "market.publishLocalFolderRefresh";
  ok: true;
  result: PublishLocalFolderRefreshOutput;
  error: null;
};
export type PublishLocalFolderRefreshError = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  operation: "market.publishLocalFolderRefresh";
  ok: false;
  result: null;
  error: MarketPageFault;
};
export type ListLocalFolderRevisionsRequest = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: Uuid;
  task_id: (Uuid | null);
  operation: "market.listLocalFolderRevisions";
  payload: ListLocalFolderRevisionsInput;
};
export type ListLocalFolderRevisionsResponse = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  operation: "market.listLocalFolderRevisions";
  ok: true;
  result: ListLocalFolderRevisionsOutput;
  error: null;
};
export type ListLocalFolderRevisionsError = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  operation: "market.listLocalFolderRevisions";
  ok: false;
  result: null;
  error: MarketPageFault;
};
export type ReadLocalFolderRevisionRequest = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: Uuid;
  task_id: (Uuid | null);
  operation: "market.readLocalFolderRevision";
  payload: ReadLocalFolderRevisionInput;
};
export type ReadLocalFolderRevisionResponse = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  operation: "market.readLocalFolderRevision";
  ok: true;
  result: ReadLocalFolderRevisionOutput;
  error: null;
};
export type ReadLocalFolderRevisionError = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  operation: "market.readLocalFolderRevision";
  ok: false;
  result: null;
  error: MarketPageFault;
};
export type ReadLocalFolderSourceRowRequest = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: Uuid;
  task_id: (Uuid | null);
  operation: "market.readLocalFolderSourceRow";
  payload: ReadLocalFolderSourceRowInput;
};
export type ReadLocalFolderSourceRowResponse = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  operation: "market.readLocalFolderSourceRow";
  ok: true;
  result: ReadLocalFolderSourceRowOutput;
  error: null;
};
export type ReadLocalFolderSourceRowError = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  operation: "market.readLocalFolderSourceRow";
  ok: false;
  result: null;
  error: MarketPageFault;
};
export type RevokeLocalFolderRevisionRequest = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: Uuid;
  task_id: (Uuid | null);
  operation: "market.revokeLocalFolderRevision";
  payload: RevokeLocalFolderRevisionInput;
};
export type RevokeLocalFolderRevisionResponse = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  operation: "market.revokeLocalFolderRevision";
  ok: true;
  result: RevokeLocalFolderRevisionOutput;
  error: null;
};
export type RevokeLocalFolderRevisionError = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  operation: "market.revokeLocalFolderRevision";
  ok: false;
  result: null;
  error: MarketPageFault;
};
export type RevokeLocalImportRevisionRequest = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: Uuid;
  task_id: (Uuid | null);
  operation: "market.revokeLocalImportRevision";
  payload: RevokeLocalImportRevisionInput;
};
export type RevokeLocalImportRevisionResponse = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  operation: "market.revokeLocalImportRevision";
  ok: true;
  result: RevokeLocalImportRevisionOutput;
  error: null;
};
export type RevokeLocalImportRevisionError = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  operation: "market.revokeLocalImportRevision";
  ok: false;
  result: null;
  error: MarketPageFault;
};
export type PublishLocalImportRequest = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: Uuid;
  task_id: (Uuid | null);
  operation: "market.publishLocalImport";
  payload: PublishLocalImportInput;
};
export type PublishLocalImportResponse = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  operation: "market.publishLocalImport";
  ok: true;
  result: PublishLocalImportOutput;
  error: null;
};
export type PublishLocalImportError = {
  protocol_version: "1.1";
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  operation: "market.publishLocalImport";
  ok: false;
  result: null;
  error: MarketPageFault;
};
export type WorkerHello11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "hello";
  role: WorkerRole;
  worker_version: string;
  supported_protocols: Array<string>;
  capabilities: Array<string>;
};
export type ScopedMutationRequest11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: Uuid;
  task_id: (Uuid | null);
  scope: MutationScope11;
  intent: MutationIntent11;
};
export type ScopedMutationResponse11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  outcome: MutationOutcome11;
};
export type TrustedConfirmationDecision11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: Uuid;
  operation: "confirmation.respond";
  challenge_id: Uuid;
  decision: "approve" | "deny";
};
export type PreviewReady11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "event";
  event: "indicator.preview_ready";
  task_id: Uuid;
  draft_id: OpaqueId;
  preview_id: OpaqueId;
  stream_id: OpaqueId;
  sequence: Revision;
  snapshot: FrozenSnapshot;
  code_hash: Hash;
  params_hash: Hash;
  engine_version: OpaqueId;
  artifact: IndicatorArtifact11;
};
export type ArtifactChunk11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "event";
  event: "indicator.result_segment";
  task_id: Uuid;
  artifact_id: OpaqueId;
  result_hash: Hash;
  stream_id: OpaqueId;
  sequence: Revision;
  segment_index: number;
  bytes_base64: string;
};
export type TaskTerminalEvent11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "event";
  event: "task.terminal";
  task_id: Uuid;
  stream_id: OpaqueId;
  sequence: Revision;
  outcome: TerminalState11;
  committed_effects: Array<TaskEffectSummary11>;
};
export type TaskCancelRequest11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  operation: "task.cancel";
  request_id: Uuid;
  task_id: Uuid;
};
export type CancelMemberRequest11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "cancel";
  request_id: Uuid;
  task_id: Uuid;
};
export type ReadProjectedChart11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  operation: "workspace.readChartProjection";
  request_id: Uuid;
  workspace_id: OpaqueId;
  selection_revision: Revision;
  requested_fields: Array<ExportField11>;
};
export type ProjectedChartResponse11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  operation: "workspace.readChartProjection";
  request_id: Uuid;
  result: ProjectedChart11;
};
export type SharedSelectionEvent11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "event";
  event: "workspace.selection_changed";
  stream_id: OpaqueId;
  sequence: Revision;
  selection: SharedSelection11;
};
export type SharedSelectionRequest11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  operation: "workspace.selectEvidence";
  request_id: Uuid;
  workspace_id: OpaqueId;
  expected_selection_revision: Revision;
  target: (EvidenceTarget11 | null);
};
export type AiToolRequest11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "event";
  event: "ai.tool_request";
  stream_id: OpaqueId;
  sequence: Revision;
  task_id: Uuid;
  call_id: Uuid;
  payload: AiRequestedAction11;
};
export type AiToolResultDelivery11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  operation: "ai.tool_result";
  request_id: Uuid;
  task_id: Uuid;
  call_id: Uuid;
  outcome: AiToolOutcome11;
};
export type AiToolResultAck11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  operation: "ai.tool_result";
  request_id: Uuid;
  task_id: Uuid;
  call_id: Uuid;
  status: "received" | "already_received" | "task_terminal";
};
export type ProjectedChartError11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  operation: "workspace.readChartProjection";
  request_id: Uuid;
  error: WorkspaceFault11;
};
export type IndicatorCompute11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  operation: "indicator.compute";
  request_id: Uuid;
  task_id: Uuid;
  draft_id: OpaqueId;
  source: DraftSource11;
  resolved_params: Metadata;
  code_hash: Hash;
  params_hash: Hash;
  snapshot: FrozenSnapshot;
  input: IndicatorBarsInput11;
};
export type IndicatorComputed11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  operation: "indicator.compute";
  request_id: Uuid;
  task_id: Uuid;
  draft_id: OpaqueId;
  snapshot_hash: Hash;
  artifact: IndicatorArtifact11;
};
export type IndicatorInputSegment11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "event";
  event: "indicator.input_segment";
  stream_id: OpaqueId;
  sequence: Revision;
  task_id: Uuid;
  request_id: Uuid;
  snapshot_hash: Hash;
  offset: Revision;
  bars: Array<Bar>;
};
export type SharedSelectionResponse11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  operation: "workspace.selectEvidence";
  request_id: Uuid;
  selection: SharedSelection11;
};
export type SharedSelectionError11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  operation: "workspace.selectEvidence";
  request_id: Uuid;
  error: WorkspaceFault11;
};
export type SharedContextRequest11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  operation: "workspace.getContext";
  request_id: Uuid;
  workspace_id: OpaqueId;
};
export type SharedContextResponse11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  operation: "workspace.getContext";
  request_id: Uuid;
  context: SharedContext11;
};
export type SharedContextError11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  operation: "workspace.getContext";
  request_id: Uuid;
  error: WorkspaceFault11;
};
export type CancelTaskResponse11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  operation: "task.cancel";
  request_id: Uuid;
  task_id: Uuid;
  outcome: TerminalState11;
  committed_effects: Array<TaskEffectSummary11>;
};
export type CancelTaskError11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  operation: "task.cancel";
  request_id: Uuid;
  error: WorkspaceFault11;
};
export type Message = Hello | Request | Response | AiToolRequestEvent | Cancel | Heartbeat | MarketHistoryPageRequest | MarketHistoryPageResponse | MarketHistoryPageError | MarketSubscriptionRequest | MarketSubscriptionResponse | MarketSubscriptionError | LocalImportPreviewRequest | LocalImportPreviewResponse | LocalImportPreviewError | SaveLocalImportMappingRequest | SaveLocalImportMappingResponse | SaveLocalImportMappingError | LoadLocalImportMappingRequest | LoadLocalImportMappingResponse | LoadLocalImportMappingError | ListLocalImportRevisionsRequest | ListLocalImportRevisionsResponse | ListLocalImportRevisionsError | ReadLocalImportRevisionRequest | ReadLocalImportRevisionResponse | ReadLocalImportRevisionError | ReadLocalImportSourceRowRequest | ReadLocalImportSourceRowResponse | ReadLocalImportSourceRowError | PreviewLocalFolderRefreshRequest | PreviewLocalFolderRefreshResponse | PreviewLocalFolderRefreshError | PrepareLocalFolderPublicationRequest | PrepareLocalFolderPublicationResponse | PrepareLocalFolderPublicationError | PublishLocalFolderRefreshRequest | PublishLocalFolderRefreshResponse | PublishLocalFolderRefreshError | ListLocalFolderRevisionsRequest | ListLocalFolderRevisionsResponse | ListLocalFolderRevisionsError | ReadLocalFolderRevisionRequest | ReadLocalFolderRevisionResponse | ReadLocalFolderRevisionError | ReadLocalFolderSourceRowRequest | ReadLocalFolderSourceRowResponse | ReadLocalFolderSourceRowError | RevokeLocalFolderRevisionRequest | RevokeLocalFolderRevisionResponse | RevokeLocalFolderRevisionError | RevokeLocalImportRevisionRequest | RevokeLocalImportRevisionResponse | RevokeLocalImportRevisionError | PublishLocalImportRequest | PublishLocalImportResponse | PublishLocalImportError | MarketBarUpdateEvent | MarketBarCorrectionEvent | MarketStreamGapEvent | WorkerHello11 | ScopedMutationRequest11 | ScopedMutationResponse11 | TrustedConfirmationDecision11 | PreviewReady11 | ArtifactChunk11 | TaskTerminalEvent11 | TaskCancelRequest11 | CancelMemberRequest11 | ReadProjectedChart11 | ProjectedChartResponse11 | SharedSelectionEvent11 | SharedSelectionRequest11 | AiToolRequest11 | AiToolResultDelivery11 | AiToolResultAck11 | ProjectedChartError11 | IndicatorCompute11 | IndicatorComputed11 | IndicatorInputSegment11 | SharedSelectionResponse11 | SharedSelectionError11 | SharedContextRequest11 | SharedContextResponse11 | SharedContextError11 | CancelTaskResponse11 | CancelTaskError11;
export type ProtocolVersion11 = "1.1";
export type WorkspaceObject11 = {
  kind: "chart" | "layer" | "layout" | "dataset" | "workspace";
  object_id: OpaqueId;
};
export type ChartInterval11 = {
  kind: "chart_interval";
  snapshot: FrozenSnapshot;
  range: VisibleRange;
};
export type SourceLocation11 = {
  kind: "table_row" | "document_passage";
  source_id: OpaqueId;
  source_revision: OpaqueId;
  locator: OpaqueId;
};
export type EvidenceTarget11 = WorkspaceObject11 | ChartInterval11 | SourceLocation11;
export type SharedSelection11 = {
  workspace_id: OpaqueId;
  selection_revision: Revision;
  target: (EvidenceTarget11 | null);
};
export type MutationScope11 = {
  workspace_id: OpaqueId;
  target: EvidenceTarget11;
  expected_workspace_revision: Revision;
  expected_selection_revision: Revision;
  idempotency_key: Uuid;
};
export type OperationReceipt11 = {
  operation_id: OpaqueId;
  workspace_id: OpaqueId;
  target: EvidenceTarget11;
  committed_workspace_revision: Revision;
  undoable: boolean;
};
export type NumberParameter11 = {
  type: "number";
  name: string;
  default: number;
};
export type IntegerParameter11 = {
  type: "integer";
  name: string;
  default: number;
};
export type BooleanParameter11 = {
  type: "boolean";
  name: string;
  default: boolean;
};
export type ParameterDeclaration11 = NumberParameter11 | IntegerParameter11 | BooleanParameter11;
export type NumericOverride11 = {
  name: string;
  value: number;
};
export type BooleanOverride11 = {
  name: string;
  value: boolean;
};
export type ParameterOverride11 = NumericOverride11 | BooleanOverride11;
export type NumberReference11 = {
  op: "number_parameter";
  name: string;
};
export type BooleanReference11 = {
  op: "boolean_parameter";
  name: string;
};
export type WindowLiteral11 = {
  periods: number;
};
export type WindowParameter11 = {
  parameter: string;
};
export type WindowLength11 = WindowLiteral11 | WindowParameter11;
export type Arithmetic11 = {
  op: "add" | "subtract" | "multiply" | "divide";
  left: NumberExpression11;
  right: NumberExpression11;
};
export type Absolute11 = {
  op: "abs";
  value: NumberExpression11;
};
export type Delay11 = {
  op: "lag";
  value: NumberExpression11;
  periods: number;
};
export type Rolling11 = {
  op: "rolling_mean" | "rolling_std";
  value: NumberExpression11;
  window: WindowLength11;
};
export type NumberExpression11 = FieldExpression | ConstantExpression | NumberReference11 | Arithmetic11 | Absolute11 | Delay11 | Rolling11;
export type BooleanConstant11 = {
  op: "boolean";
  value: boolean;
};
export type Comparison11 = {
  op: "gt" | "lt";
  left: NumberExpression11;
  right: NumberExpression11;
};
export type BooleanBinary11 = {
  op: "and" | "or";
  left: BooleanExpression11;
  right: BooleanExpression11;
};
export type BooleanExpression11 = BooleanConstant11 | BooleanReference11 | Comparison11 | BooleanBinary11;
export type NumericSeries11 = {
  kind: "line" | "histogram";
  id: OpaqueId;
  pane: OpaqueId;
  value: NumberExpression11;
  style: Style;
};
export type ConditionalMarker11 = {
  kind: "marker_when";
  when: BooleanExpression11;
  position: MarkerPosition;
  label: string;
  color: string;
};
export type IndicatorOutputSpec11 = NumericSeries11 | ConditionalMarker11;
export type DeclarativeSource11 = {
  parameters: Array<ParameterDeclaration11>;
  outputs: Array<IndicatorOutputSpec11>;
};
export type DeclarativeDraft11 = {
  language: "declarative";
  source: DeclarativeSource11;
};
export type PythonDraft11 = {
  language: "python";
  source: string;
  params: Metadata;
};
export type DraftSource11 = DeclarativeDraft11 | PythonDraft11;
export type SnapshotPreview11 = {
  draft_id: OpaqueId;
  snapshot: FrozenSnapshot;
  parameters: Array<ParameterOverride11>;
};
export type PythonSnapshotPreview11 = {
  draft_id: OpaqueId;
  snapshot: FrozenSnapshot;
  python_params: Metadata;
};
export type PreviewArguments11 = SnapshotPreview11 | PythonSnapshotPreview11;
export type AcceptPreview11 = {
  preview_id: OpaqueId;
  result_hash: Hash;
};
export type OpenSeries11 = {
  series: SeriesKey;
  dataset_revision: (OpaqueId | null);
};
export type WorkspaceOpenInstrumentIntent11 = {
  operation: "workspace.openInstrument";
  arguments: OpenSeries11;
};
export type ChartSetTimeframeArguments11 = {
  timeframe: string;
};
export type ChartSetTimeframeIntent11 = {
  operation: "chart.setTimeframe";
  arguments: ChartSetTimeframeArguments11;
};
export type ChartSetStyleArguments11 = {
  style: ChartStyle;
};
export type ChartSetStyleIntent11 = {
  operation: "chart.setStyle";
  arguments: ChartSetStyleArguments11;
};
export type ChartAddAnnotationArguments11 = {
  annotation: Annotation;
};
export type ChartAddAnnotationIntent11 = {
  operation: "chart.addAnnotation";
  arguments: ChartAddAnnotationArguments11;
};
export type ChartRemoveLayerArguments11 = {
  layer_id: OpaqueId;
};
export type ChartRemoveLayerIntent11 = {
  operation: "chart.removeLayer";
  arguments: ChartRemoveLayerArguments11;
};
export type IndicatorCreateDraftIntent11 = {
  operation: "indicator.createDraft";
  arguments: DraftSource11;
};
export type IndicatorRunPreviewIntent11 = {
  operation: "indicator.runPreview";
  arguments: PreviewArguments11;
};
export type IndicatorAcceptVersionIntent11 = {
  operation: "indicator.acceptVersion";
  arguments: AcceptPreview11;
};
export type IndicatorRemoveArguments11 = {
  layer_id: OpaqueId;
};
export type IndicatorRemoveIntent11 = {
  operation: "indicator.remove";
  arguments: IndicatorRemoveArguments11;
};
export type WorkspaceUndoArguments11 = {
  operation_id: OpaqueId;
};
export type WorkspaceUndoIntent11 = {
  operation: "workspace.undo";
  arguments: WorkspaceUndoArguments11;
};
export type WorkspacePreviewLayoutIntent11 = {
  operation: "workspace.previewLayout";
  arguments: WorkspacePreviewLayoutInput11;
};
export type WorkspaceApplyLayoutIntent11 = {
  operation: "workspace.applyLayout";
  arguments: WorkspaceApplyLayoutInput11;
};
export type MutationIntent11 = WorkspaceOpenInstrumentIntent11 | ChartSetTimeframeIntent11 | ChartSetStyleIntent11 | ChartAddAnnotationIntent11 | ChartRemoveLayerIntent11 | IndicatorCreateDraftIntent11 | IndicatorRunPreviewIntent11 | IndicatorAcceptVersionIntent11 | IndicatorRemoveIntent11 | WorkspaceUndoIntent11 | WorkspacePreviewLayoutIntent11 | WorkspaceApplyLayoutIntent11;
export type ScopedFailure11 = {
  state: "failed";
  error: WorkspaceFault11;
};
export type WorkspaceFault11 = {
  code: ErrorCode;
  message: string;
  retryable: boolean;
};
export type PythonExecutionScope11 = {
  code_hash: Hash;
  parameters_hash: Hash;
  snapshot_hash: Hash;
  trust_mode: "trusted_user_code_no_sandbox";
  input_use: "trusted_code_permitted";
};
export type ConfirmationChallenge11 = {
  challenge_id: Uuid;
  request_id: Uuid;
  task_id: (Uuid | null);
  operation: OperationName;
  principal_id: OpaqueId;
  session_id: OpaqueId;
  workspace_id: OpaqueId;
  target: EvidenceTarget11;
  workspace_revision: Revision;
  selection_revision: Revision;
  parameters_hash: Hash;
  expires_at_ms: Timestamp;
  python: (PythonExecutionScope11 | null);
};
export type NeedsHumanDecision11 = {
  state: "awaiting_confirmation";
  challenge: ConfirmationChallenge11;
};
export type WorkspaceOpenInstrumentCommitted11 = {
  state: "committed";
  operation: "workspace.openInstrument";
  receipt: OperationReceipt11;
  result: WorkspaceOpenInstrumentOutput;
};
export type ChartSetTimeframeCommitted11 = {
  state: "committed";
  operation: "chart.setTimeframe";
  receipt: OperationReceipt11;
  result: ChartSetTimeframeOutput;
};
export type ChartSetStyleCommitted11 = {
  state: "committed";
  operation: "chart.setStyle";
  receipt: OperationReceipt11;
  result: ChartSetStyleOutput;
};
export type ChartAddAnnotationCommitted11 = {
  state: "committed";
  operation: "chart.addAnnotation";
  receipt: OperationReceipt11;
  result: ChartAddAnnotationOutput;
};
export type ChartRemoveLayerCommitted11 = {
  state: "committed";
  operation: "chart.removeLayer";
  receipt: OperationReceipt11;
  result: ChartRemoveLayerOutput;
};
export type IndicatorCreateDraftCommitted11 = {
  state: "committed";
  operation: "indicator.createDraft";
  receipt: OperationReceipt11;
  result: IndicatorCreateDraftOutput;
};
export type IndicatorRunPreviewCommitted11 = {
  state: "committed";
  operation: "indicator.runPreview";
  receipt: OperationReceipt11;
  result: IndicatorRunPreviewOutput;
};
export type IndicatorAcceptVersionCommitted11 = {
  state: "committed";
  operation: "indicator.acceptVersion";
  receipt: OperationReceipt11;
  result: IndicatorAcceptVersionOutput;
};
export type IndicatorRemoveCommitted11 = {
  state: "committed";
  operation: "indicator.remove";
  receipt: OperationReceipt11;
  result: IndicatorRemoveOutput;
};
export type WorkspaceUndoCommitted11 = {
  state: "committed";
  operation: "workspace.undo";
  receipt: OperationReceipt11;
  result: WorkspaceUndoOutput;
};
export type WorkspacePreviewLayoutSuccess11 = {
  state: "previewed";
  operation: "workspace.previewLayout";
  result: WorkspacePreviewLayoutOutput11;
};
export type WorkspaceApplyLayoutSuccess11 = {
  state: "committed";
  operation: "workspace.applyLayout";
  receipt: OperationReceipt11;
  result: NamedWorkspaceLayout11;
};
export type MutationOutcome11 = WorkspaceOpenInstrumentCommitted11 | ChartSetTimeframeCommitted11 | ChartSetStyleCommitted11 | ChartAddAnnotationCommitted11 | ChartRemoveLayerCommitted11 | IndicatorCreateDraftCommitted11 | IndicatorRunPreviewCommitted11 | IndicatorAcceptVersionCommitted11 | IndicatorRemoveCommitted11 | WorkspaceUndoCommitted11 | NeedsHumanDecision11 | ScopedFailure11 | WorkspacePreviewLayoutSuccess11 | WorkspaceApplyLayoutSuccess11;
export type InlineIndicatorArtifact11 = {
  storage: "inline";
  result_hash: Hash;
  result: IndicatorResult;
};
export type IndexedIndicatorArtifact11 = {
  storage: "core_artifact";
  artifact_id: OpaqueId;
  result_hash: Hash;
  byte_length: number;
  segment_count: number;
};
export type IndicatorArtifact11 = InlineIndicatorArtifact11 | IndexedIndicatorArtifact11;
export type TaskEffectSummary11 = {
  operation_id: OpaqueId;
  workspace_id: OpaqueId;
  committed_workspace_revision: Revision;
};
export type TaskCompleted11 = {
  status: "completed";
};
export type TaskCancelled11 = {
  status: "cancelled";
};
export type TaskFailed11 = {
  status: "failed";
  error: WorkspaceFault11;
};
export type TerminalState11 = TaskCompleted11 | TaskCancelled11 | TaskFailed11;
export type ExportedBar11 = {
  time_ms?: Timestamp;
  trading_day?: string;
  open?: number;
  high?: number;
  low?: number;
  close?: number;
  volume?: (number | null);
  is_closed?: boolean;
};
export type ExportField11 = "time_ms" | "trading_day" | "open" | "high" | "low" | "close" | "volume" | "is_closed";
export type ProjectedChart11 = {
  source_reference: OpaqueId;
  revision: OpaqueId;
  fields: Array<ExportField11>;
  rows: Array<ExportedBar11>;
};
export type ProjectedReadIntent11 = {
  operation: "workspace.readChartProjection";
  workspace_id: OpaqueId;
  selection_revision: Revision;
  requested_fields: Array<ExportField11>;
};
export type BoundMutationIntent11 = {
  scope: MutationScope11;
  intent: MutationIntent11;
};
export type MarketHistoryPageToolCall11 = {
  operation: "market.readHistoryPage";
  arguments: MarketHistoryPageInput;
};
export type SelectEvidenceIntent11 = {
  operation: "workspace.selectEvidence";
  workspace_id: OpaqueId;
  expected_selection_revision: Revision;
  target: (EvidenceTarget11 | null);
};
export type AiRequestedAction11 = BoundMutationIntent11 | ProjectedReadIntent11 | InstrumentSearchToolCall | MarketGetBarsToolCall | WorkspaceGetContextToolCall | TaskCancelToolCall | MarketHistoryPageToolCall11 | SelectEvidenceIntent11;
export type ToolReadResult11 = {
  status: "read";
  view: ProjectedChart11;
};
export type ToolCommittedResult11 = {
  status: "committed";
  operation_id: OpaqueId;
  committed_workspace_revision: Revision;
  undoable: boolean;
};
export type ToolRefResult11 = {
  status: "available";
  result_ref: OpaqueId;
};
export type ToolDeniedResult11 = {
  status: "denied" | "failed" | "cancelled";
  error: WorkspaceFault11;
};
export type AiToolOutcome11 = ToolReadResult11 | ToolCommittedResult11 | ToolRefResult11 | ToolDeniedResult11;
export type InlineIndicatorBars11 = {
  mode: "inline";
  bars: Array<Bar>;
};
export type SegmentedIndicatorBars11 = {
  mode: "segmented";
  total_bars: number;
  segment_count: number;
};
export type IndicatorBarsInput11 = InlineIndicatorBars11 | SegmentedIndicatorBars11;
export type LegacyOperationName = "instrument.search" | "market.getBars" | "workspace.openInstrument" | "chart.setTimeframe" | "chart.setStyle" | "chart.addAnnotation" | "chart.removeLayer" | "indicator.createDraft" | "indicator.runPreview" | "indicator.acceptVersion" | "indicator.remove" | "workspace.undo" | "task.cancel" | "workspace.getContext";
export type ProjectedChartArguments11 = {
  workspace_id: OpaqueId;
  selection_revision: Revision;
  requested_fields: Array<ExportField11>;
};
export type SelectEvidenceArguments11 = {
  workspace_id: OpaqueId;
  expected_selection_revision: Revision;
  target: (EvidenceTarget11 | null);
};
export type ProjectedChartReply11 = ProjectedChartResponse11 | ProjectedChartError11;
export type SelectEvidenceReply11 = SharedSelectionResponse11 | SharedSelectionError11;
export type SharedContext11 = {
  workspace_id: OpaqueId;
  workspace_revision: Revision;
  selection: SharedSelection11;
  chart: (ProjectedChart11 | null);
  authorized_operations: Array<OperationName>;
};
export type SharedContextReply11 = SharedContextResponse11 | SharedContextError11;
export type CancelTaskReply11 = CancelTaskResponse11 | CancelTaskError11;
export type WorkspaceOpenInstrumentCommand11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: Uuid;
  task_id: (Uuid | null);
  scope: MutationScope11;
  intent: WorkspaceOpenInstrumentIntent11;
};
export type WorkspaceOpenInstrumentOutcome11 = WorkspaceOpenInstrumentCommitted11 | NeedsHumanDecision11 | ScopedFailure11;
export type WorkspaceOpenInstrumentReply11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  outcome: WorkspaceOpenInstrumentOutcome11;
};
export type ChartSetTimeframeCommand11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: Uuid;
  task_id: (Uuid | null);
  scope: MutationScope11;
  intent: ChartSetTimeframeIntent11;
};
export type ChartSetTimeframeOutcome11 = ChartSetTimeframeCommitted11 | NeedsHumanDecision11 | ScopedFailure11;
export type ChartSetTimeframeReply11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  outcome: ChartSetTimeframeOutcome11;
};
export type ChartSetStyleCommand11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: Uuid;
  task_id: (Uuid | null);
  scope: MutationScope11;
  intent: ChartSetStyleIntent11;
};
export type ChartSetStyleOutcome11 = ChartSetStyleCommitted11 | NeedsHumanDecision11 | ScopedFailure11;
export type ChartSetStyleReply11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  outcome: ChartSetStyleOutcome11;
};
export type ChartAddAnnotationCommand11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: Uuid;
  task_id: (Uuid | null);
  scope: MutationScope11;
  intent: ChartAddAnnotationIntent11;
};
export type ChartAddAnnotationOutcome11 = ChartAddAnnotationCommitted11 | NeedsHumanDecision11 | ScopedFailure11;
export type ChartAddAnnotationReply11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  outcome: ChartAddAnnotationOutcome11;
};
export type ChartRemoveLayerCommand11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: Uuid;
  task_id: (Uuid | null);
  scope: MutationScope11;
  intent: ChartRemoveLayerIntent11;
};
export type ChartRemoveLayerOutcome11 = ChartRemoveLayerCommitted11 | NeedsHumanDecision11 | ScopedFailure11;
export type ChartRemoveLayerReply11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  outcome: ChartRemoveLayerOutcome11;
};
export type IndicatorCreateDraftCommand11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: Uuid;
  task_id: (Uuid | null);
  scope: MutationScope11;
  intent: IndicatorCreateDraftIntent11;
};
export type IndicatorCreateDraftOutcome11 = IndicatorCreateDraftCommitted11 | NeedsHumanDecision11 | ScopedFailure11;
export type IndicatorCreateDraftReply11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  outcome: IndicatorCreateDraftOutcome11;
};
export type IndicatorRunPreviewCommand11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: Uuid;
  task_id: (Uuid | null);
  scope: MutationScope11;
  intent: IndicatorRunPreviewIntent11;
};
export type IndicatorRunPreviewOutcome11 = IndicatorRunPreviewCommitted11 | NeedsHumanDecision11 | ScopedFailure11;
export type IndicatorRunPreviewReply11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  outcome: IndicatorRunPreviewOutcome11;
};
export type IndicatorAcceptVersionCommand11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: Uuid;
  task_id: (Uuid | null);
  scope: MutationScope11;
  intent: IndicatorAcceptVersionIntent11;
};
export type IndicatorAcceptVersionOutcome11 = IndicatorAcceptVersionCommitted11 | NeedsHumanDecision11 | ScopedFailure11;
export type IndicatorAcceptVersionReply11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  outcome: IndicatorAcceptVersionOutcome11;
};
export type IndicatorRemoveCommand11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: Uuid;
  task_id: (Uuid | null);
  scope: MutationScope11;
  intent: IndicatorRemoveIntent11;
};
export type IndicatorRemoveOutcome11 = IndicatorRemoveCommitted11 | NeedsHumanDecision11 | ScopedFailure11;
export type IndicatorRemoveReply11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  outcome: IndicatorRemoveOutcome11;
};
export type WorkspaceUndoCommand11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: Uuid;
  task_id: (Uuid | null);
  scope: MutationScope11;
  intent: WorkspaceUndoIntent11;
};
export type WorkspaceUndoOutcome11 = WorkspaceUndoCommitted11 | NeedsHumanDecision11 | ScopedFailure11;
export type WorkspaceUndoReply11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  outcome: WorkspaceUndoOutcome11;
};
export type HistoryPageReply11 = MarketHistoryPageResponse | MarketHistoryPageError;
export type CsvColumnMappingV1 = {
  symbol?: string;
  timestamp?: string;
  trading_day?: string;
  open?: string;
  high?: string;
  low?: string;
  close?: string;
  volume?: (string | null);
};
export type CsvMappingV1 = {
  version: number;
  revision: string;
  encoding: string;
  delimiter: string;
  record_kind: string;
  columns?: CsvColumnMappingV1;
  instrument_kind?: string;
  venue?: string;
  timeframe?: string;
  calendar_id?: string;
  session_scope?: string;
  timezone?: string;
  quantity_unit?: string;
  price_adjustment?: string;
  roll_method?: string;
  volume_semantics?: string;
  timestamp_convention?: string;
  timestamp_format?: string;
  trading_day_format?: string;
  daily_start_time?: string;
};
export type PreviewIssue = {
  code: string;
  field: (string | null);
  message: string;
};
export type RawOnlyColumn = {
  column: string;
  reason: string;
};
export type PreviewSeries = {
  instrument_kind: string;
  venue: string;
  timeframe: string;
  calendar_id: string;
  session_scope: string;
  timezone: string;
  quantity_unit: string;
  price_adjustment: string;
  roll_method: (string | null);
};
export type PreviewRow = {
  record_number: number;
  line_start: number;
  line_end: number;
  raw_fields: Array<string>;
  raw_text: (string | null);
  symbol: (string | null);
  normalized: (Bar | null);
  issues: Array<PreviewIssue>;
};
export type CsvPreview = {
  file_sha256: Hash;
  mapping_version: (number | null);
  mapping_revision: (string | null);
  header: Array<string>;
  raw_header_text: (string | null);
  rows: Array<PreviewRow>;
  issues: Array<PreviewIssue>;
  raw_only_columns: Array<RawOnlyColumn>;
  series: (PreviewSeries | null);
  truncated: boolean;
};
export type LocalImportPreviewInput = {
  version: number;
  folder_id: Uuid;
  file_id: Uuid;
  expected_sha256?: Hash;
  mapping: CsvMappingV1;
  row_limit?: number;
};
export type LocalImportPreviewOutput = {
  version: number;
  folder_id: Uuid;
  file_id: Uuid;
  relative_path: string;
  display_name: string;
  byte_count: number;
  sha256: Hash;
  preview: CsvPreview;
};
export type LocalImportPreviewReply11 = LocalImportPreviewResponse | LocalImportPreviewError;
export type SavedLocalImportMapping = {
  review_revision: number;
  header_sha256: Hash;
  mapping: CsvMappingV1;
};
export type SaveLocalImportMappingInput = {
  version: number;
  folder_id: Uuid;
  file_id: Uuid;
  expected_sha256: Hash;
  mapping: CsvMappingV1;
  row_limit?: number;
};
export type SaveLocalImportMappingOutput = {
  version: number;
  record: SavedLocalImportMapping;
};
export type LoadLocalImportMappingInput = {
  version: number;
  folder_id: Uuid;
  file_id: Uuid;
};
export type LoadLocalImportMappingOutput = {
  version: number;
  status: "saved" | "not_saved" | "review_required";
  record: (SavedLocalImportMapping | null);
  source_sha256: (Hash | null);
};
export type SaveLocalImportMappingReply11 = SaveLocalImportMappingResponse | SaveLocalImportMappingError;
export type LoadLocalImportMappingReply11 = LoadLocalImportMappingResponse | LoadLocalImportMappingError;
export type DecisionNoteUuid11 = string;
export type DecisionNoteDate = string;
export type DecisionNoteEvidenceRef = {
  source_id: string;
  source_revision: string;
  locator: string;
};
export type DecisionNoteOriginal = {
  note_id: DecisionNoteUuid11;
  decided_on: DecisionNoteDate;
  thesis: string;
  evidence_refs: Array<DecisionNoteEvidenceRef>;
  choice: string;
};
export type DecisionNoteSummary = {
  note_id: DecisionNoteUuid11;
  decided_on: DecisionNoteDate;
};
export type DecisionNoteOutcome = {
  outcome_id: DecisionNoteUuid11;
  observed_on: DecisionNoteDate;
  outcome: string;
};
export type DecisionNoteReadOutput = {
  note: (DecisionNoteOriginal | null);
  notes: Array<DecisionNoteSummary>;
  outcomes: Array<DecisionNoteOutcome>;
  next_offset: (Revision | null);
};
export type DecisionNoteTarget = {
  kind: "decision_note";
  object_id: DecisionNoteUuid11;
};
export type PrivateRecordUuid11 = string;
export type PrivateRecordReadScope11 = {
  workspace_id: OpaqueId;
};
export type PrivateRecordChangeScope11 = {
  workspace_id: OpaqueId;
  expected_workspace_revision: Revision;
  idempotency_key: PrivateRecordUuid11;
};
export type PrivateRecordKind = "conversation" | "memory" | "strategy";
export type PrivateRecordTurn = {
  speaker: "user" | "assistant";
  text: string;
};
export type PrivateRecordConversation = {
  kind: "conversation";
  title: string;
  turns: Array<PrivateRecordTurn>;
};
export type PrivateRecordMemory = {
  kind: "memory";
  title: string;
  text: string;
};
export type PrivateRecordStrategy = {
  kind: "strategy";
  title: string;
  text: string;
};
export type PrivateRecordContent = PrivateRecordConversation | PrivateRecordMemory | PrivateRecordStrategy;
export type PrivateRecordEntry = {
  record_id: PrivateRecordUuid11;
  revision: Revision;
  content: PrivateRecordContent;
};
export type PrivateRecordSummary = {
  record_id: PrivateRecordUuid11;
  revision: Revision;
  kind: PrivateRecordKind;
  title: string;
};
export type PrivateRecordArchiveEntry = {
  record_id: PrivateRecordUuid11;
  content: PrivateRecordContent;
};
export type PrivateRecordBackupV1 = {
  format: "scopehub.private-records";
  version: number;
  profile_binding: string;
  records: Array<PrivateRecordArchiveEntry>;
  deletions: Array<PrivateRecordUuid11>;
};
export type PrivateRecordReadInput = {
  record_id: (PrivateRecordUuid11 | null);
};
export type PrivateRecordReadOutput = {
  workspace_revision: Revision;
  record: (PrivateRecordEntry | null);
  records: Array<PrivateRecordSummary>;
};
export type PrivateRecordSaveInput = {
  action: "save";
  record_id: PrivateRecordUuid11;
  expected_record_revision: (Revision | null);
  content: PrivateRecordContent;
};
export type PrivateRecordDeleteInput = {
  action: "delete";
  record_id: PrivateRecordUuid11;
  expected_record_revision: Revision;
};
export type PrivateRecordRestoreInput = {
  action: "restore";
  backup: PrivateRecordBackupV1;
};
export type PrivateRecordResolveInput = {
  action: "resolve";
  challenge_id: PrivateRecordUuid11;
  decision: "approve" | "decline";
};
export type PrivateRecordChangeInput = PrivateRecordSaveInput | PrivateRecordDeleteInput | PrivateRecordRestoreInput | PrivateRecordResolveInput;
export type PrivateRecordChangeSummary = {
  added: number;
  corrected: number;
  removed: number;
  kept: number;
  suppressed: number;
  imported_deletions: number;
};
export type PrivateRecordReceipt11 = {
  operation_id: PrivateRecordUuid11;
  workspace_id: OpaqueId;
  record_id: (PrivateRecordUuid11 | null);
  committed_workspace_revision: Revision;
  undoable: false;
};
export type PrivateRecordExportInput = {
};
export type PrivateRecordReadIntent11 = {
  operation: "privateRecord.read";
  arguments: PrivateRecordReadInput;
};
export type PrivateRecordReadCommand11 = {
  protocol_version: ProtocolVersion11;
  message_id: PrivateRecordUuid11;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: PrivateRecordUuid11;
  scope: PrivateRecordReadScope11;
  intent: PrivateRecordReadIntent11;
};
export type PrivateRecordReadCompleted11 = {
  state: "completed";
  operation: "privateRecord.read";
  result: PrivateRecordReadOutput;
};
export type PrivateRecordReadOutcome11 = PrivateRecordReadCompleted11 | ScopedFailure11;
export type PrivateRecordReadReply11 = {
  protocol_version: ProtocolVersion11;
  message_id: PrivateRecordUuid11;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: PrivateRecordUuid11;
  outcome: PrivateRecordReadOutcome11;
};
export type PrivateRecordChangeIntent11 = {
  operation: "privateRecord.change";
  arguments: PrivateRecordChangeInput;
};
export type PrivateRecordChangeCommand11 = {
  protocol_version: ProtocolVersion11;
  message_id: PrivateRecordUuid11;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: PrivateRecordUuid11;
  scope: PrivateRecordChangeScope11;
  intent: PrivateRecordChangeIntent11;
};
export type PrivateRecordChangeCommitted11 = {
  state: "committed";
  operation: "privateRecord.change";
  receipt: PrivateRecordReceipt11;
};
export type PrivateRecordChangeConfirmationRequired11 = {
  state: "confirmation_required";
  operation: "privateRecord.change";
  challenge_id: PrivateRecordUuid11;
  expires_at_ms: Timestamp;
  summary: PrivateRecordChangeSummary;
};
export type PrivateRecordChangeOutcome11 = PrivateRecordChangeCommitted11 | PrivateRecordChangeConfirmationRequired11 | ScopedFailure11;
export type PrivateRecordChangeReply11 = {
  protocol_version: ProtocolVersion11;
  message_id: PrivateRecordUuid11;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: PrivateRecordUuid11;
  outcome: PrivateRecordChangeOutcome11;
};
export type PrivateRecordExportIntent11 = {
  operation: "privateRecord.exportBackup";
  arguments: PrivateRecordExportInput;
};
export type PrivateRecordExportCommand11 = {
  protocol_version: ProtocolVersion11;
  message_id: PrivateRecordUuid11;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: PrivateRecordUuid11;
  scope: PrivateRecordReadScope11;
  intent: PrivateRecordExportIntent11;
};
export type PrivateRecordExportCompleted11 = {
  state: "completed";
  operation: "privateRecord.exportBackup";
  result: PrivateRecordBackupV1;
};
export type PrivateRecordExportOutcome11 = PrivateRecordExportCompleted11 | ScopedFailure11;
export type PrivateRecordExportReply11 = {
  protocol_version: ProtocolVersion11;
  message_id: PrivateRecordUuid11;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: PrivateRecordUuid11;
  outcome: PrivateRecordExportOutcome11;
};
export type DecisionNoteReceipt11 = {
  operation_id: DecisionNoteUuid11;
  workspace_id: OpaqueId;
  target: DecisionNoteTarget;
  committed_workspace_revision: Revision;
  undoable: false;
};
export type DecisionNoteReadScope11 = {
  workspace_id: OpaqueId;
};
export type DecisionNoteWriteScope11 = {
  workspace_id: OpaqueId;
  target: DecisionNoteTarget;
  expected_workspace_revision: Revision;
  idempotency_key: DecisionNoteUuid11;
};
export type DecisionNoteConfirmation11 = {
  challenge_id: DecisionNoteUuid11;
  decision: "approve" | "decline";
};
export type DecisionNoteSaveInput = {
  decided_on: DecisionNoteDate;
  thesis: string;
  evidence_refs: Array<DecisionNoteEvidenceRef>;
  choice: string;
};
export type DecisionNoteSaveIntent11 = {
  operation: "decisionNote.save";
  arguments: DecisionNoteSaveInput;
};
export type DecisionNoteSaveCommand11 = {
  protocol_version: ProtocolVersion11;
  message_id: DecisionNoteUuid11;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: DecisionNoteUuid11;
  scope: DecisionNoteWriteScope11;
  intent: DecisionNoteSaveIntent11;
  confirmation: (DecisionNoteConfirmation11 | null);
};
export type DecisionNoteSaveCommitted11 = {
  state: "committed";
  operation: "decisionNote.save";
  receipt: DecisionNoteReceipt11;
};
export type DecisionNoteSaveConfirmationRequired11 = {
  state: "confirmation_required";
  operation: "decisionNote.save";
  challenge_id: DecisionNoteUuid11;
  expires_at_ms: Timestamp;
};
export type DecisionNoteSaveOutcome11 = DecisionNoteSaveCommitted11 | DecisionNoteSaveConfirmationRequired11 | ScopedFailure11;
export type DecisionNoteSaveReply11 = {
  protocol_version: ProtocolVersion11;
  message_id: DecisionNoteUuid11;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: DecisionNoteUuid11;
  outcome: DecisionNoteSaveOutcome11;
};
export type DecisionNoteReadInput = {
  note_id: (DecisionNoteUuid11 | null);
  offset: Revision;
};
export type DecisionNoteReadIntent11 = {
  operation: "decisionNote.read";
  arguments: DecisionNoteReadInput;
};
export type DecisionNoteReadCommand11 = {
  protocol_version: ProtocolVersion11;
  message_id: DecisionNoteUuid11;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: DecisionNoteUuid11;
  scope: DecisionNoteReadScope11;
  intent: DecisionNoteReadIntent11;
};
export type DecisionNoteReadCompleted11 = {
  state: "completed";
  operation: "decisionNote.read";
  result: DecisionNoteReadOutput;
};
export type DecisionNoteReadOutcome11 = DecisionNoteReadCompleted11 | ScopedFailure11;
export type DecisionNoteReadReply11 = {
  protocol_version: ProtocolVersion11;
  message_id: DecisionNoteUuid11;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: DecisionNoteUuid11;
  outcome: DecisionNoteReadOutcome11;
};
export type DecisionNoteAppendOutcomeInput = {
  observed_on: DecisionNoteDate;
  outcome: string;
};
export type DecisionNoteAppendOutcomeIntent11 = {
  operation: "decisionNote.appendOutcome";
  arguments: DecisionNoteAppendOutcomeInput;
};
export type DecisionNoteAppendOutcomeCommand11 = {
  protocol_version: ProtocolVersion11;
  message_id: DecisionNoteUuid11;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: DecisionNoteUuid11;
  scope: DecisionNoteWriteScope11;
  intent: DecisionNoteAppendOutcomeIntent11;
  confirmation: (DecisionNoteConfirmation11 | null);
};
export type DecisionNoteAppendOutcomeCommitted11 = {
  state: "committed";
  operation: "decisionNote.appendOutcome";
  receipt: DecisionNoteReceipt11;
};
export type DecisionNoteAppendOutcomeConfirmationRequired11 = {
  state: "confirmation_required";
  operation: "decisionNote.appendOutcome";
  challenge_id: DecisionNoteUuid11;
  expires_at_ms: Timestamp;
};
export type DecisionNoteAppendOutcomeOutcome11 = DecisionNoteAppendOutcomeCommitted11 | DecisionNoteAppendOutcomeConfirmationRequired11 | ScopedFailure11;
export type DecisionNoteAppendOutcomeReply11 = {
  protocol_version: ProtocolVersion11;
  message_id: DecisionNoteUuid11;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: DecisionNoteUuid11;
  outcome: DecisionNoteAppendOutcomeOutcome11;
};
export type DecisionNoteDeleteInput = {
};
export type DecisionNoteDeleteIntent11 = {
  operation: "decisionNote.delete";
  arguments: DecisionNoteDeleteInput;
};
export type DecisionNoteDeleteCommand11 = {
  protocol_version: ProtocolVersion11;
  message_id: DecisionNoteUuid11;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: DecisionNoteUuid11;
  scope: DecisionNoteWriteScope11;
  intent: DecisionNoteDeleteIntent11;
  confirmation: (DecisionNoteConfirmation11 | null);
};
export type DecisionNoteDeleteCommitted11 = {
  state: "committed";
  operation: "decisionNote.delete";
  receipt: DecisionNoteReceipt11;
};
export type DecisionNoteDeleteConfirmationRequired11 = {
  state: "confirmation_required";
  operation: "decisionNote.delete";
  challenge_id: DecisionNoteUuid11;
  expires_at_ms: Timestamp;
};
export type DecisionNoteDeleteOutcome11 = DecisionNoteDeleteCommitted11 | DecisionNoteDeleteConfirmationRequired11 | ScopedFailure11;
export type DecisionNoteDeleteReply11 = {
  protocol_version: ProtocolVersion11;
  message_id: DecisionNoteUuid11;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: DecisionNoteUuid11;
  outcome: DecisionNoteDeleteOutcome11;
};
export type DecisionNoteBackupEntry = {
  original: DecisionNoteOriginal;
  outcomes: Array<DecisionNoteOutcome>;
};
export type DecisionNoteBackupDeletion = {
  note_id: DecisionNoteUuid11;
  deleted_at_ms: Timestamp;
};
export type DecisionNoteBackupV1 = {
  format: "scopehub.decision-notes";
  version: number;
  profile_binding: string;
  captured_workspace_revision: Revision;
  notes: Array<DecisionNoteBackupEntry>;
  deletions: Array<DecisionNoteBackupDeletion>;
};
export type DecisionNoteRestoreScope11 = {
  workspace_id: OpaqueId;
  expected_workspace_revision: Revision;
  idempotency_key: DecisionNoteUuid11;
};
export type DecisionNoteRestoreSummary = {
  added_notes: Revision;
  added_outcomes: Revision;
  removed_notes: Revision;
  suppressed_notes: Revision;
  imported_deletions: Revision;
  independent_copy_warning: boolean;
};
export type DecisionNoteExportBackupInput = {
};
export type DecisionNotePreviewRestoreInput = {
  backup: DecisionNoteBackupV1;
};
export type DecisionNoteRestoreBackupInput = {
  stage_id: DecisionNoteUuid11;
  decision: "approve" | "decline";
};
export type DecisionNoteExportBackupIntent11 = {
  operation: "decisionNote.exportBackup";
  arguments: DecisionNoteExportBackupInput;
};
export type DecisionNoteExportBackupCommand11 = {
  protocol_version: ProtocolVersion11;
  message_id: DecisionNoteUuid11;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: DecisionNoteUuid11;
  scope: DecisionNoteReadScope11;
  intent: DecisionNoteExportBackupIntent11;
};
export type DecisionNoteExportBackupCompleted11 = {
  state: "completed";
  operation: "decisionNote.exportBackup";
  result: DecisionNoteBackupV1;
};
export type DecisionNoteExportBackupOutcome11 = DecisionNoteExportBackupCompleted11 | ScopedFailure11;
export type DecisionNoteExportBackupReply11 = {
  protocol_version: ProtocolVersion11;
  message_id: DecisionNoteUuid11;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: DecisionNoteUuid11;
  outcome: DecisionNoteExportBackupOutcome11;
};
export type DecisionNotePreviewRestoreIntent11 = {
  operation: "decisionNote.previewRestore";
  arguments: DecisionNotePreviewRestoreInput;
};
export type DecisionNotePreviewRestoreCommand11 = {
  protocol_version: ProtocolVersion11;
  message_id: DecisionNoteUuid11;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: DecisionNoteUuid11;
  scope: DecisionNoteRestoreScope11;
  intent: DecisionNotePreviewRestoreIntent11;
};
export type DecisionNotePreviewRestoreConfirmationRequired11 = {
  state: "confirmation_required";
  operation: "decisionNote.previewRestore";
  stage_id: DecisionNoteUuid11;
  expires_at_ms: Timestamp;
  summary: DecisionNoteRestoreSummary;
};
export type DecisionNotePreviewRestoreOutcome11 = DecisionNotePreviewRestoreConfirmationRequired11 | ScopedFailure11;
export type DecisionNotePreviewRestoreReply11 = {
  protocol_version: ProtocolVersion11;
  message_id: DecisionNoteUuid11;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: DecisionNoteUuid11;
  outcome: DecisionNotePreviewRestoreOutcome11;
};
export type DecisionNoteRestoreBackupIntent11 = {
  operation: "decisionNote.restoreBackup";
  arguments: DecisionNoteRestoreBackupInput;
};
export type DecisionNoteRestoreBackupCommand11 = {
  protocol_version: ProtocolVersion11;
  message_id: DecisionNoteUuid11;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: DecisionNoteUuid11;
  scope: DecisionNoteRestoreScope11;
  intent: DecisionNoteRestoreBackupIntent11;
};
export type DecisionNoteRestoreBackupCommitted11 = {
  state: "committed";
  operation: "decisionNote.restoreBackup";
  receipt: OperationReceipt11;
};
export type DecisionNoteRestoreBackupOutcome11 = DecisionNoteRestoreBackupCommitted11 | ScopedFailure11;
export type DecisionNoteRestoreBackupReply11 = {
  protocol_version: ProtocolVersion11;
  message_id: DecisionNoteUuid11;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: DecisionNoteUuid11;
  outcome: DecisionNoteRestoreBackupOutcome11;
};
export type LocalImportRowLocator = {
  record_number: number;
  line_start: number;
  line_end: number;
};
export type PublishedLocalImportBar = {
  bar: Bar;
  locators: Array<LocalImportRowLocator>;
};
export type PublishedLocalImportRevision = {
  revision_id: Uuid;
  relative_path: string;
  source_sha256: Hash;
  mapping_sha256: Hash;
  review_revision: number;
  symbol: string;
  series: PreviewSeries;
  bar_count: number;
  source_record_count: number;
};
export type PublishLocalImportInput = {
  version: number;
  folder_id: Uuid;
  file_id: Uuid;
  expected_sha256: Hash;
  review_revision: number;
};
export type PublishLocalImportOutput = {
  version: number;
  revision: PublishedLocalImportRevision;
};
export type LocalImportRevisionBarSummary = {
  venue: string;
  first_trading_day: string;
  final_trading_day: string;
  final_close: number;
};
export type LocalImportRevisionListEntry = {
  revision_id: CanonicalRevisionCursor;
  relative_path: string;
  symbol: string;
  bar_count: number;
  source_record_count: number;
  source_sha256: Hash;
  timeframe: string;
  bar_summary?: LocalImportRevisionBarSummary;
};
export type CanonicalRevisionCursor = string;
export type ListLocalImportRevisionsInput = {
  version: number;
  after_revision_id: (CanonicalRevisionCursor | null);
  limit: number;
  include_bar_summary?: boolean;
};
export type ListLocalImportRevisionsOutput = {
  version: number;
  revisions: Array<LocalImportRevisionListEntry>;
  next_after_revision_id: (CanonicalRevisionCursor | null);
};
export type ListLocalImportRevisionsReply11 = ListLocalImportRevisionsResponse | ListLocalImportRevisionsError;
export type ReadLocalImportRevisionInput = {
  version: number;
  revision_id: Uuid;
};
export type ReadLocalImportRevisionOutput = {
  version: number;
  revision: PublishedLocalImportRevision;
  bars: Array<PublishedLocalImportBar>;
};
export type PublishLocalImportReply11 = PublishLocalImportResponse | PublishLocalImportError;
export type ReadLocalImportRevisionReply11 = ReadLocalImportRevisionResponse | ReadLocalImportRevisionError;
export type ReadLocalImportSourceRowInput = {
  version: number;
  revision_id: Uuid;
  locator: LocalImportRowLocator;
};
export type ReadLocalImportSourceRowOutput = {
  version: number;
  revision_id: Uuid;
  source_sha256: Hash;
  locator: LocalImportRowLocator;
  header: Array<string>;
  raw_fields: Array<string>;
};
export type ReadLocalImportSourceRowReply11 = ReadLocalImportSourceRowResponse | ReadLocalImportSourceRowError;
export type RevokeLocalImportRevisionInput = {
  version: number;
  revision_id: Uuid;
};
export type RevokeLocalImportRevisionReply11 = RevokeLocalImportRevisionResponse | RevokeLocalImportRevisionError;
export type RevokeLocalImportRevisionOutput = {
  version: number;
  revision_id: Uuid;
};
export type FolderRefreshFile = {
  relative_path: string;
  source_sha256: (Hash | null);
  prior_sha256: (Hash | null);
  change: "unchanged" | "added" | "replaced" | "removed" | "unverified";
  alias_of: (string | null);
  review: "compatible" | "header_changed" | "series_mismatch" | "invalid_rows" | "unavailable" | "removed";
  detail: (string | null);
};
export type FolderRefreshSource = {
  relative_path: string;
  source_sha256: Hash;
  record_number: number;
  line_start: number;
  line_end: number;
};
export type FolderRefreshAlternative = {
  bar: Bar;
  sources: Array<FolderRefreshSource>;
};
export type FolderRefreshBar = {
  time_ms: Timestamp;
  status: "unchanged" | "addition" | "conflict" | "withdrawal" | "unverified";
  accepted: (AcceptedFolderRefreshBar | null);
  alternatives: Array<FolderRefreshAlternative>;
};
export type PreviewLocalFolderRefreshInput = {
  version: number;
  folder_id: Uuid;
  revision_id: Uuid;
};
export type PreviewLocalFolderRefreshOutput = {
  version: number;
  base?: PublishedLocalImportRevision;
  files: Array<FolderRefreshFile>;
  bars: Array<FolderRefreshBar>;
  folder_base?: PublishedLocalFolderRevision;
  review_id?: Hash;
  publication_allowed?: boolean;
};
export type PreviewLocalFolderRefreshReply11 = PreviewLocalFolderRefreshResponse | PreviewLocalFolderRefreshError;
export type FolderPublicationConflictChoice = {
  time_ms: Timestamp;
  alternative_index: number;
};
export type PublishedLocalFolderRevision = {
  revision_id: Uuid;
  base_revision_id: Uuid;
  parent_revision_id: (Uuid | null);
  origin_path: string;
  symbol: string;
  series: PreviewSeries;
  source_count: number;
  bar_count: number;
  bar_summary?: LocalImportRevisionBarSummary;
};
export type PublishedLocalFolderSource = {
  relative_path: string;
  source_sha256: Hash;
  record_count: number;
};
export type PrepareLocalFolderPublicationInput = {
  version: number;
  folder_id: Uuid;
  review_id: Hash;
  conflicts: Array<FolderPublicationConflictChoice>;
  withdraw_bars: Array<Timestamp>;
  withdraw_sources: Array<string>;
};
export type PrepareLocalFolderPublicationOutput = {
  version: number;
  review_id: Hash;
  candidate_sha256: Hash;
  parent_revision_id: Uuid;
  source_count: number;
  bar_count: number;
};
export type PublishLocalFolderRefreshInput = {
  version: number;
  folder_id: Uuid;
  review_id: Hash;
  candidate_sha256: Hash;
  idempotency_key: Uuid;
};
export type PublishLocalFolderRefreshOutput = {
  version: number;
  revision: PublishedLocalFolderRevision;
};
export type ListLocalFolderRevisionsInput = {
  version: number;
  after_revision_id: (CanonicalRevisionCursor | null);
  limit: number;
};
export type ListLocalFolderRevisionsOutput = {
  version: number;
  revisions: Array<PublishedLocalFolderRevision>;
  next_after_revision_id: (CanonicalRevisionCursor | null);
};
export type ReadLocalFolderRevisionInput = {
  version: number;
  revision_id: Uuid;
  include_details?: boolean;
};
export type ReadLocalFolderRevisionOutput = {
  version: number;
  revision: PublishedLocalFolderRevision;
  files: Array<PublishedLocalFolderSource>;
  bars: Array<FolderRefreshAlternative>;
};
export type RevokeLocalFolderRevisionInput = {
  version: number;
  revision_id: Uuid;
};
export type RevokeLocalFolderRevisionOutput = {
  version: number;
  revision_id: Uuid;
};
export type PrepareLocalFolderPublicationReply11 = PrepareLocalFolderPublicationResponse | PrepareLocalFolderPublicationError;
export type PublishLocalFolderRefreshReply11 = PublishLocalFolderRefreshResponse | PublishLocalFolderRefreshError;
export type ListLocalFolderRevisionsReply11 = ListLocalFolderRevisionsResponse | ListLocalFolderRevisionsError;
export type ReadLocalFolderRevisionReply11 = ReadLocalFolderRevisionResponse | ReadLocalFolderRevisionError;
export type ReadLocalFolderSourceRowInput = {
  version: number;
  revision_id: Uuid;
  source: FolderRefreshSource;
};
export type ReadLocalFolderSourceRowOutput = {
  version: number;
  revision_id: Uuid;
  source: FolderRefreshSource;
  header: Array<string>;
  raw_fields: Array<string>;
};
export type ReadLocalFolderSourceRowReply11 = ReadLocalFolderSourceRowResponse | ReadLocalFolderSourceRowError;
export type RevokeLocalFolderRevisionReply11 = RevokeLocalFolderRevisionResponse | RevokeLocalFolderRevisionError;
export type AcceptedFolderRefreshBar = PublishedLocalImportBar | FolderRefreshAlternative;
export type LayoutPanel11 = {
  panel: "watchlist" | "inspection";
  dock: "left" | "right" | "bottom";
  size: number;
};
export type WorkspaceLayout11 = {
  version: number;
  panels: Array<LayoutPanel11>;
  theme: "dark" | "light";
  gain_color: string;
  loss_color: string;
  link_selection: boolean;
  pinned_instrument: (OpaqueId | null);
};
export type NamedWorkspaceLayout11 = {
  name: (string | null);
  definition: WorkspaceLayout11;
};
export type SaveWorkspaceLayout11 = {
  action: "save";
  name: string;
  definition: WorkspaceLayout11;
};
export type OpenWorkspaceLayout11 = {
  action: "open";
  name: string;
};
export type ResetWorkspaceLayout11 = {
  action: "reset";
};
export type WorkspaceLayoutEdit11 = SaveWorkspaceLayout11 | OpenWorkspaceLayout11 | ResetWorkspaceLayout11;
export type WorkspacePreviewLayoutInput11 = {
  edit: WorkspaceLayoutEdit11;
};
export type WorkspacePreviewLayoutOutput11 = {
  layout: NamedWorkspaceLayout11;
  preview_token: string;
};
export type WorkspaceApplyLayoutInput11 = {
  edit: WorkspaceLayoutEdit11;
  preview_token: string;
};
export type WorkspacePreviewLayoutCommand11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: Uuid;
  task_id: (Uuid | null);
  scope: MutationScope11;
  intent: WorkspacePreviewLayoutIntent11;
};
export type WorkspacePreviewLayoutOutcome11 = WorkspacePreviewLayoutSuccess11 | ScopedFailure11;
export type WorkspacePreviewLayoutReply11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  outcome: WorkspacePreviewLayoutOutcome11;
};
export type WorkspaceApplyLayoutCommand11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: Uuid;
  task_id: (Uuid | null);
  scope: MutationScope11;
  intent: WorkspaceApplyLayoutIntent11;
};
export type WorkspaceApplyLayoutOutcome11 = WorkspaceApplyLayoutSuccess11 | ScopedFailure11;
export type WorkspaceApplyLayoutReply11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  outcome: WorkspaceApplyLayoutOutcome11;
};
export type AiTextOllamaChoice = {
  kind: "ollama";
  origin: string;
  model: string;
  chart_tools?: true;
};
export type AiTextOfflineMockChoice = {
  kind: "offline_mock";
};
export type AiTextRouteChoice = AiTextOllamaChoice | AiTextOfflineMockChoice;
export type AiTextOllamaRoute = {
  kind: "ollama";
  destination: string;
  model: string;
  scope: "conversation_text" | "conversation_text_and_sample_closes";
  charge_basis: "endpoint_operator";
};
export type AiTextOfflineMockRoute = {
  kind: "offline_mock";
  destination: "This app (offline)";
  model: "ScopeHub offline mock";
  scope: "conversation_text";
  charge_basis: "none";
};
export type AiTextRoute = AiTextOllamaRoute | AiTextOfflineMockRoute;
export type AiTextGrant = {
  connection_id: Uuid;
  route: AiTextRoute;
};
export type AiTextRunningTurn = {
  state: "running";
};
export type AiTextCompletedTurn = {
  state: "completed";
  model: string;
  text: string;
  chart_evidence?: AiTextChartEvidence;
};
export type AiTextFailedTurn = {
  state: "failed";
  error: WorkspaceFault11;
};
export type AiTextTurn = AiTextRunningTurn | AiTextCompletedTurn | AiTextFailedTurn;
export type AiTextStatusInput = {
  action: "status";
};
export type AiTextProposeInput = {
  action: "propose";
  route: AiTextRouteChoice;
};
export type AiTextActivateInput = {
  action: "activate";
  proposal_id: Uuid;
};
export type AiTextDisconnectInput = {
  action: "disconnect";
  connection_id: Uuid;
};
export type AiTextSendInput = {
  action: "send";
  connection_id: Uuid;
  question: string;
  selection_revision?: Revision;
};
export type AiTextPollInput = {
  action: "poll";
  connection_id: Uuid;
  turn_id: Uuid;
};
export type AiTextCancelInput = {
  action: "cancel";
  connection_id: Uuid;
  turn_id: Uuid;
};
export type AiTextConnectionInput = AiTextStatusInput | AiTextProposeInput | AiTextActivateInput | AiTextDisconnectInput | AiTextSendInput | AiTextPollInput | AiTextCancelInput;
export type AiTextStatusOutput = {
  action: "status";
  connection: (AiTextGrant | null);
  turn_id: (Uuid | null);
};
export type AiTextProposeOutput = {
  action: "propose";
  proposal_id: Uuid;
  route: AiTextRoute;
};
export type AiTextActivateOutput = {
  action: "activate";
  connection: AiTextGrant;
};
export type AiTextDisconnectOutput = {
  action: "disconnect";
};
export type AiTextSendOutput = {
  action: "send";
  turn_id: Uuid;
};
export type AiTextPollOutput = {
  action: "poll";
  turn_id: Uuid;
  turn: AiTextTurn;
};
export type AiTextCancelOutput = {
  action: "cancel";
};
export type AiTextConnectionOutput = AiTextStatusOutput | AiTextProposeOutput | AiTextActivateOutput | AiTextDisconnectOutput | AiTextSendOutput | AiTextPollOutput | AiTextCancelOutput;
export type AiTextConnectionCommand11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "request";
  request_id: Uuid;
  scope: AiTextConnectionScope11;
  intent: AiTextConnectionIntent11;
};
export type AiTextConnectionReply11 = {
  protocol_version: ProtocolVersion11;
  message_id: Uuid;
  timestamp_ms: Timestamp;
  type: "response";
  request_id: Uuid;
  outcome: AiTextConnectionOutcome11;
};
export type AiTextChartClose = {
  time_ms: Timestamp;
  close: number;
};
export type AiTextChartEvidence = {
  source_reference: OpaqueId;
  revision: OpaqueId;
  range: VisibleRange;
  rows: Array<AiTextChartClose>;
};
export type AiTextConnectionScope11 = {
  workspace_id: OpaqueId;
};
export type AiTextConnectionIntent11 = {
  operation: "ai.textConnection";
  arguments: AiTextConnectionInput;
};
export type AiTextConnectionCompleted11 = {
  state: "completed";
  operation: "ai.textConnection";
  result: AiTextConnectionOutput;
};
export type AiTextConnectionOutcome11 = AiTextConnectionCompleted11 | ScopedFailure11;
