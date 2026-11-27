/// <reference types="vite/client" />

interface Window {
  readonly __SCOPEHUB_DESKTOP_WORKSPACE__?: true;
  readonly __SCOPEHUB_DESKTOP_TEXT_MODEL__?: true;
}

declare module 'virtual:indicator-preview-status' {
  export type ReadyPreviewStatus = {
    status: 'ready';
    task_id: string;
    event: import('../../../../contracts/generated/protocol').PreviewReady11;
    source_snapshot: import('../../../../contracts/generated/protocol').FrozenSnapshot;
    resolved_parameters: Record<string, unknown>;
    warmup_from_ms: number | null;
    calendar_hash: string | null;
  };
  type PreviewStatus = ReadyPreviewStatus | { status: 'running'; task_id: string } | {
    status: 'terminal';
    task_id: string;
    event: import('../../../../contracts/generated/protocol').TaskTerminalEvent11;
  };
  export default function isIndicatorPreviewStatus(value: unknown): value is PreviewStatus;
}
