# Generated from contracts/schemas/protocol.schema.json; sha256=7d5aeb935ac213ef7068481ce24b63fdbd75b882c0e6f348025408473fca7584. Do not edit.
from __future__ import annotations
from typing import Literal, NotRequired, TypedDict
JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
OpaqueId = str
Uuid = str
Revision = int
Timestamp = int
Metadata = dict[str, JsonValue]
WorkerRole = Literal['market', 'ai', 'indicator']
ProtocolVersion = Literal['1.0']
ErrorCode = Literal['INVALID_ARGUMENT', 'UNSUPPORTED_CAPABILITY', 'PERMISSION_DENIED', 'CONFIRMATION_REQUIRED', 'CANCELLED', 'TIMEOUT', 'WORKER_EXITED', 'PROTOCOL_MISMATCH', 'DATA_UNAVAILABLE', 'STALE_CONTEXT', 'RESOURCE_LIMIT', 'OUTPUT_INVALID', 'PROVIDER_NOT_CONFIGURED', 'INTERNAL_ERROR']
OperationName = Literal['instrument.search', 'market.getBars', 'workspace.openInstrument', 'chart.setTimeframe', 'chart.setStyle', 'chart.addAnnotation', 'chart.removeLayer', 'indicator.createDraft', 'indicator.runPreview', 'indicator.acceptVersion', 'indicator.remove', 'workspace.undo', 'task.cancel', 'workspace.getContext']
ChartStyle = Literal['candlestick', 'line']
TaskStatus = Literal['queued', 'running', 'awaiting_confirmation', 'completed', 'failed', 'cancelled']
Tradability = Literal['non_tradable', 'unknown']
DataMode = Literal['simulated', 'historical', 'delayed', 'realtime']
DataQuality = Literal['fresh', 'stale', 'gapped', 'degraded']
class ProviderMapping(TypedDict):
    provider_id: OpaqueId
    provider_symbol: str
class Instrument(TypedDict):
    id: OpaqueId
    display_symbol: str
    name: str
    asset_class: str
    venue: str
    currency: str
    calendar_id: OpaqueId
    timezone: str
    price_tick: str | None
    quantity_unit: str
    tradability: Tradability
    metadata: Metadata
    provider_mappings: list[ProviderMapping]
class DatasetDescriptor(TypedDict):
    id: Uuid
    instrument_id: OpaqueId
    provider_id: OpaqueId
    mode: DataMode
    timeframe: str
    price_adjustment: str
    timezone: str
    calendar_id: OpaqueId
    quantity_unit: str
    revision: str
    quality: DataQuality
    provenance: Metadata
class Bar(TypedDict):
    time_ms: Timestamp
    trading_day: str
    open: float
    high: float
    low: float
    close: float
    volume: float | None
    is_closed: bool
class Error(TypedDict):
    code: ErrorCode
    message: str
    retryable: bool
    details: Metadata
class Point(TypedDict):
    time_ms: Timestamp
    value: float | None
class Style(TypedDict):
    color: str
    width: int
MarkerPosition = Literal['above', 'below', 'on']
class Marker(TypedDict):
    time_ms: Timestamp
    position: MarkerPosition
    label: str
    color: str
class Region(TypedDict):
    from_ms: Timestamp
    to_ms: Timestamp
    label: str
    color: str
Annotation = Marker | Region
SeriesKind = Literal['line', 'histogram']
class IndicatorSeries(TypedDict):
    id: OpaqueId
    pane: str
    type: SeriesKind
    points: list[Point]
    style: Style
Severity = Literal['info', 'warning', 'error']
class Diagnostic(TypedDict):
    code: str
    message: str
    severity: Severity
Hash = str
class IndicatorProvenance(TypedDict):
    code_hash: Hash
    params_hash: Hash
    dataset_id: Uuid
    dataset_revision: str
    engine_version: str
class IndicatorResult(TypedDict):
    series: list[IndicatorSeries]
    markers: list[Marker]
    regions: list[Region]
    diagnostics: list[Diagnostic]
    provenance: IndicatorProvenance
LayerState = Literal['preview', 'accepted']
class Layer(TypedDict):
    id: OpaqueId
    state: LayerState
    source_task_id: Uuid | None
    indicator_version_id: OpaqueId | None
    metadata: Metadata
class VisibleRange(TypedDict):
    from_ms: Timestamp
    to_ms: Timestamp
class Workspace(TypedDict):
    id: OpaqueId
    revision: Revision
    instrument_id: OpaqueId | None
    timeframe: str
    style: ChartStyle
    dataset: DatasetDescriptor | None
    visible_range: VisibleRange | None
    layers: list[Layer]
class Context(TypedDict):
    workspace: Workspace
    authorized_operations: list[OperationName]
    bars: list[Bar]
class FieldExpression(TypedDict):
    op: Literal['field']
    field: Literal['open', 'high', 'low', 'close', 'volume']
class ConstantExpression(TypedDict):
    op: Literal['constant']
    value: float
class BinaryExpression(TypedDict):
    op: Literal['add', 'subtract', 'multiply', 'divide', 'gt', 'lt', 'and', 'or']
    left: Expression
    right: Expression
class AbsExpression(TypedDict):
    op: Literal['abs']
    value: Expression
class LagExpression(TypedDict):
    op: Literal['lag']
    value: Expression
    periods: int
class WindowExpression(TypedDict):
    op: Literal['rolling_mean', 'rolling_std']
    value: Expression
    window: int
Expression = FieldExpression | ConstantExpression | BinaryExpression | AbsExpression | LagExpression | WindowExpression
class DeclarativeDraftInput(TypedDict):
    language: Literal['declarative']
    source: Expression
    params: Metadata
    expected_workspace_revision: Revision
class PythonDraftInput(TypedDict):
    language: Literal['python']
    source: str
    params: Metadata
    expected_workspace_revision: Revision
class InstrumentSearchInput(TypedDict):
    query: str
    limit: int
class InstrumentSearchOutput(TypedDict):
    instruments: list[Instrument]
class InstrumentSearchRequest(TypedDict):
    protocol_version: ProtocolVersion
    message_id: Uuid
    timestamp_ms: Timestamp
    type: Literal['request']
    request_id: Uuid
    task_id: Uuid | None
    operation: Literal['instrument.search']
    payload: InstrumentSearchInput
class InstrumentSearchToolCall(TypedDict):
    operation: Literal['instrument.search']
    arguments: InstrumentSearchInput
class MarketGetBarsInput(TypedDict):
    instrument_id: OpaqueId
    timeframe: str
    from_ms: Timestamp
    to_ms: Timestamp
    provider_id: OpaqueId
class MarketGetBarsOutput(TypedDict):
    dataset: DatasetDescriptor
    bars: list[Bar]
class MarketGetBarsRequest(TypedDict):
    protocol_version: ProtocolVersion
    message_id: Uuid
    timestamp_ms: Timestamp
    type: Literal['request']
    request_id: Uuid
    task_id: Uuid | None
    operation: Literal['market.getBars']
    payload: MarketGetBarsInput
class MarketGetBarsToolCall(TypedDict):
    operation: Literal['market.getBars']
    arguments: MarketGetBarsInput
class WorkspaceOpenInstrumentInput(TypedDict):
    instrument_id: OpaqueId
    timeframe: str
    expected_workspace_revision: Revision
WorkspaceOpenInstrumentOutput = Workspace
class WorkspaceOpenInstrumentRequest(TypedDict):
    protocol_version: ProtocolVersion
    message_id: Uuid
    timestamp_ms: Timestamp
    type: Literal['request']
    request_id: Uuid
    task_id: Uuid | None
    operation: Literal['workspace.openInstrument']
    payload: WorkspaceOpenInstrumentInput
class WorkspaceOpenInstrumentToolCall(TypedDict):
    operation: Literal['workspace.openInstrument']
    arguments: WorkspaceOpenInstrumentInput
class ChartSetTimeframeInput(TypedDict):
    timeframe: str
    expected_workspace_revision: Revision
ChartSetTimeframeOutput = Workspace
class ChartSetTimeframeRequest(TypedDict):
    protocol_version: ProtocolVersion
    message_id: Uuid
    timestamp_ms: Timestamp
    type: Literal['request']
    request_id: Uuid
    task_id: Uuid | None
    operation: Literal['chart.setTimeframe']
    payload: ChartSetTimeframeInput
class ChartSetTimeframeToolCall(TypedDict):
    operation: Literal['chart.setTimeframe']
    arguments: ChartSetTimeframeInput
class ChartSetStyleInput(TypedDict):
    style: ChartStyle
    expected_workspace_revision: Revision
ChartSetStyleOutput = Workspace
class ChartSetStyleRequest(TypedDict):
    protocol_version: ProtocolVersion
    message_id: Uuid
    timestamp_ms: Timestamp
    type: Literal['request']
    request_id: Uuid
    task_id: Uuid | None
    operation: Literal['chart.setStyle']
    payload: ChartSetStyleInput
class ChartSetStyleToolCall(TypedDict):
    operation: Literal['chart.setStyle']
    arguments: ChartSetStyleInput
class ChartAddAnnotationInput(TypedDict):
    annotation: Annotation
    expected_workspace_revision: Revision
class ChartAddAnnotationOutput(TypedDict):
    layer_id: OpaqueId
class ChartAddAnnotationRequest(TypedDict):
    protocol_version: ProtocolVersion
    message_id: Uuid
    timestamp_ms: Timestamp
    type: Literal['request']
    request_id: Uuid
    task_id: Uuid | None
    operation: Literal['chart.addAnnotation']
    payload: ChartAddAnnotationInput
class ChartAddAnnotationToolCall(TypedDict):
    operation: Literal['chart.addAnnotation']
    arguments: ChartAddAnnotationInput
class ChartRemoveLayerInput(TypedDict):
    layer_id: OpaqueId
    expected_workspace_revision: Revision
class ChartRemoveLayerOutput(TypedDict):
    operation_id: OpaqueId
class ChartRemoveLayerRequest(TypedDict):
    protocol_version: ProtocolVersion
    message_id: Uuid
    timestamp_ms: Timestamp
    type: Literal['request']
    request_id: Uuid
    task_id: Uuid | None
    operation: Literal['chart.removeLayer']
    payload: ChartRemoveLayerInput
class ChartRemoveLayerToolCall(TypedDict):
    operation: Literal['chart.removeLayer']
    arguments: ChartRemoveLayerInput
IndicatorCreateDraftInput = DeclarativeDraftInput | PythonDraftInput
class IndicatorCreateDraftOutput(TypedDict):
    draft_id: OpaqueId
    code_hash: Hash
class IndicatorCreateDraftRequest(TypedDict):
    protocol_version: ProtocolVersion
    message_id: Uuid
    timestamp_ms: Timestamp
    type: Literal['request']
    request_id: Uuid
    task_id: Uuid | None
    operation: Literal['indicator.createDraft']
    payload: IndicatorCreateDraftInput
class IndicatorCreateDraftToolCall(TypedDict):
    operation: Literal['indicator.createDraft']
    arguments: IndicatorCreateDraftInput
class IndicatorRunPreviewInput(TypedDict):
    draft_id: OpaqueId
    dataset_id: Uuid
    dataset_revision: str
    params: Metadata
    expected_workspace_revision: Revision
class IndicatorRunPreviewOutput(TypedDict):
    task_id: Uuid
class IndicatorRunPreviewRequest(TypedDict):
    protocol_version: ProtocolVersion
    message_id: Uuid
    timestamp_ms: Timestamp
    type: Literal['request']
    request_id: Uuid
    task_id: Uuid | None
    operation: Literal['indicator.runPreview']
    payload: IndicatorRunPreviewInput
class IndicatorRunPreviewToolCall(TypedDict):
    operation: Literal['indicator.runPreview']
    arguments: IndicatorRunPreviewInput
class IndicatorAcceptVersionInput(TypedDict):
    preview_id: OpaqueId
    expected_workspace_revision: Revision
class IndicatorAcceptVersionOutput(TypedDict):
    version_id: OpaqueId
    layer_id: OpaqueId
class IndicatorAcceptVersionRequest(TypedDict):
    protocol_version: ProtocolVersion
    message_id: Uuid
    timestamp_ms: Timestamp
    type: Literal['request']
    request_id: Uuid
    task_id: Uuid | None
    operation: Literal['indicator.acceptVersion']
    payload: IndicatorAcceptVersionInput
class IndicatorAcceptVersionToolCall(TypedDict):
    operation: Literal['indicator.acceptVersion']
    arguments: IndicatorAcceptVersionInput
class IndicatorRemoveInput(TypedDict):
    layer_id: OpaqueId
    expected_workspace_revision: Revision
class IndicatorRemoveOutput(TypedDict):
    operation_id: OpaqueId
class IndicatorRemoveRequest(TypedDict):
    protocol_version: ProtocolVersion
    message_id: Uuid
    timestamp_ms: Timestamp
    type: Literal['request']
    request_id: Uuid
    task_id: Uuid | None
    operation: Literal['indicator.remove']
    payload: IndicatorRemoveInput
class IndicatorRemoveToolCall(TypedDict):
    operation: Literal['indicator.remove']
    arguments: IndicatorRemoveInput
class WorkspaceUndoInput(TypedDict):
    operation_id: OpaqueId
    expected_workspace_revision: Revision
WorkspaceUndoOutput = Workspace
class WorkspaceUndoRequest(TypedDict):
    protocol_version: ProtocolVersion
    message_id: Uuid
    timestamp_ms: Timestamp
    type: Literal['request']
    request_id: Uuid
    task_id: Uuid | None
    operation: Literal['workspace.undo']
    payload: WorkspaceUndoInput
class WorkspaceUndoToolCall(TypedDict):
    operation: Literal['workspace.undo']
    arguments: WorkspaceUndoInput
class TaskCancelInput(TypedDict):
    task_id: Uuid
class TaskCancelOutput(TypedDict):
    task_id: Uuid
    status: Literal['completed', 'failed', 'cancelled']
class TaskCancelRequest(TypedDict):
    protocol_version: ProtocolVersion
    message_id: Uuid
    timestamp_ms: Timestamp
    type: Literal['request']
    request_id: Uuid
    task_id: Uuid | None
    operation: Literal['task.cancel']
    payload: TaskCancelInput
class TaskCancelToolCall(TypedDict):
    operation: Literal['task.cancel']
    arguments: TaskCancelInput
class WorkspaceGetContextInput(TypedDict):
    workspace_id: OpaqueId
WorkspaceGetContextOutput = Context
class WorkspaceGetContextRequest(TypedDict):
    protocol_version: ProtocolVersion
    message_id: Uuid
    timestamp_ms: Timestamp
    type: Literal['request']
    request_id: Uuid
    task_id: Uuid | None
    operation: Literal['workspace.getContext']
    payload: WorkspaceGetContextInput
class WorkspaceGetContextToolCall(TypedDict):
    operation: Literal['workspace.getContext']
    arguments: WorkspaceGetContextInput
Request = InstrumentSearchRequest | MarketGetBarsRequest | WorkspaceOpenInstrumentRequest | ChartSetTimeframeRequest | ChartSetStyleRequest | ChartAddAnnotationRequest | ChartRemoveLayerRequest | IndicatorCreateDraftRequest | IndicatorRunPreviewRequest | IndicatorAcceptVersionRequest | IndicatorRemoveRequest | WorkspaceUndoRequest | TaskCancelRequest | WorkspaceGetContextRequest
ToolCall = InstrumentSearchToolCall | MarketGetBarsToolCall | WorkspaceOpenInstrumentToolCall | ChartSetTimeframeToolCall | ChartSetStyleToolCall | ChartAddAnnotationToolCall | ChartRemoveLayerToolCall | IndicatorCreateDraftToolCall | IndicatorRunPreviewToolCall | IndicatorAcceptVersionToolCall | IndicatorRemoveToolCall | WorkspaceUndoToolCall | TaskCancelToolCall | WorkspaceGetContextToolCall
class Hello(TypedDict):
    protocol_version: ProtocolVersion
    message_id: Uuid
    timestamp_ms: Timestamp
    type: Literal['hello']
    role: WorkerRole
    worker_version: str
    supported_protocols: list[str]
    capabilities: list[OperationName]
class ErrorResponse(TypedDict):
    protocol_version: ProtocolVersion
    message_id: Uuid
    timestamp_ms: Timestamp
    type: Literal['response']
    request_id: Uuid
    ok: Literal[False]
    result: None
    error: Error
OperationResult = InstrumentSearchOutput | MarketGetBarsOutput | WorkspaceOpenInstrumentOutput | ChartSetTimeframeOutput | ChartSetStyleOutput | ChartAddAnnotationOutput | ChartRemoveLayerOutput | IndicatorCreateDraftOutput | IndicatorRunPreviewOutput | IndicatorAcceptVersionOutput | IndicatorRemoveOutput | WorkspaceUndoOutput | TaskCancelOutput | WorkspaceGetContextOutput
class SuccessResponse(TypedDict):
    protocol_version: ProtocolVersion
    message_id: Uuid
    timestamp_ms: Timestamp
    type: Literal['response']
    request_id: Uuid
    ok: Literal[True]
    result: OperationResult
    error: None
Response = SuccessResponse | ErrorResponse
class AiToolRequestEvent(TypedDict):
    protocol_version: ProtocolVersion
    message_id: Uuid
    timestamp_ms: Timestamp
    type: Literal['event']
    stream_id: str
    sequence: int
    event: Literal['ai.tool_request']
    payload: ToolCall
class Cancel(TypedDict):
    protocol_version: ProtocolVersion
    message_id: Uuid
    timestamp_ms: Timestamp
    type: Literal['cancel']
    request_id: Uuid
class Heartbeat(TypedDict):
    protocol_version: ProtocolVersion
    message_id: Uuid
    timestamp_ms: Timestamp
    type: Literal['heartbeat']
Message = Hello | Request | Response | AiToolRequestEvent | Cancel | Heartbeat
