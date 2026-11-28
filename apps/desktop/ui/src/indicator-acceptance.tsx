import { message as uiMessage, t, type Message } from './i18n';
import { useLayoutEffect, useRef, useState } from 'react';
import type {
  ConfirmationChallenge11, FrozenSnapshot, IndicatorAcceptVersionCommand11, IndicatorAcceptVersionReply11,
  IndicatorRemoveCommand11, IndicatorRemoveReply11, InlineIndicatorArtifact11,
  MutationScope11, PreviewReady11, TrustedConfirmationDecision11, WorkspaceUndoCommand11, WorkspaceUndoReply11,
} from '../../../../contracts/generated/protocol';
import type { IndicatorChartLayer } from './chart-adapter/history';
import { IndicatorDiagnostics } from './indicator-diagnostics';
import { IndicatorRegionValues } from './indicator-region-values';
import { displayAccessDenied, type PublishedRevisionAccess } from './published-revision-access';
import { withReplyDeadline } from './reply-deadline';
import { exchangeWorkspace, type LocalWorkspace } from './use-local-workspace';

type Layer = { layer_id: string; version_id: string; available: boolean };
type Accepted = Layer & { document?: {
  snapshot: FrozenSnapshot; warmup_from_ms: number | null; resolved_parameters: Record<string, unknown>;
  artifact: InlineIndicatorArtifact11;
} };
type RemovalHint = {
  operation_id: string; layer_id: string; committed_workspace_revision: number;
};
type AcceptedLayers = { layers: Accepted[]; undo: RemovalHint | undefined };
type ActionReply = IndicatorAcceptVersionReply11 | IndicatorRemoveReply11 | WorkspaceUndoReply11;
type ActionRequest = IndicatorAcceptVersionCommand11 | IndicatorRemoveCommand11
  | TrustedConfirmationDecision11 | WorkspaceUndoCommand11;
type WriteScope = {
  workspaceId: string;
  revision: number;
};
type UncertainWrite = WriteScope & { wasRejected: () => boolean };
const replyDeadlineMs = 15_000;
const uuid = (id: unknown) => typeof id === 'string' && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(id);

const command = () => ({
  protocol_version: '1.1', type: 'request', message_id: crypto.randomUUID(),
  request_id: crypto.randomUUID(), timestamp_ms: Date.now(),
} as const);

async function readAccepted<T>(request: Parameters<typeof exchangeWorkspace>[0], signal: AbortSignal): Promise<T> {
  for (;;) {
    try {
      return await exchangeWorkspace<T>(request, signal);
    } catch (error) {
      if (!error || typeof error !== 'object' || !('code' in error) || error.code !== 'BUSY') throw error;
      await new Promise<void>(resolve => setTimeout(resolve, 100));
      signal.throwIfAborted();
    }
  }
}

async function readLayers(snapshot: FrozenSnapshot, signal: AbortSignal): Promise<AcceptedLayers> {
  const listing = await readAccepted<{ dataset_id: string; layers: Layer[]; undo?: RemovalHint | null }>({
    type: 'indicator_accepted_layers', request_id: crypto.randomUUID(), revision_id: snapshot.dataset_id,
  }, signal);
  if (listing.dataset_id !== snapshot.dataset_id) throw new Error();
  const accepted: Accepted[] = [];
  for (const layer of listing.layers) {
    if (!layer.available) {
      accepted.push(layer);
      continue;
    }
    const output = await readAccepted<{ layer_id: string; version_id: string; document: NonNullable<Accepted['document']> }>({
      type: 'indicator_accepted_output', request_id: crypto.randomUUID(), layer_id: layer.layer_id,
    }, signal);
    if (output.layer_id !== layer.layer_id || output.version_id !== layer.version_id
      || output.document.snapshot.dataset_id !== snapshot.dataset_id
      || output.document.artifact.storage !== 'inline') throw new Error();
    accepted.push({ ...layer, document: output.document });
  }
  const undo = listing.undo;
  return { layers: accepted, undo: undo && Object.keys(undo).length === 3
    && uuid(undo.operation_id) && uuid(undo.layer_id)
    && Number.isSafeInteger(undo.committed_workspace_revision) && undo.committed_workspace_revision > 0
    && !accepted.some(layer => layer.layer_id === undo.layer_id) ? undo : undefined };
}

function matchesChart(input: FrozenSnapshot, chart: FrozenSnapshot) {
  const { series } = chart;
  return input.dataset_id === chart.dataset_id && input.dataset_revision === chart.dataset_revision
    && input.as_of_ms === chart.as_of_ms && Object.entries(series).every(([key, value]) =>
      input.series[key as keyof FrozenSnapshot['series']] === value);
}

function bodyOf<T extends ActionReply>(
  reply: T, requestId: string,
): T['outcome'] {
  if (reply.request_id !== requestId) throw new Error();
  if (reply.outcome.state === 'failed') throw reply.outcome.error;
  return reply.outcome;
}

export function IndicatorAcceptance({ preview, snapshot, workspace, access, clearPreview, showAccepted }: {
  preview: PreviewReady11 | undefined; snapshot: FrozenSnapshot; workspace: LocalWorkspace;
  access?: PublishedRevisionAccess | undefined; clearPreview: () => void; showAccepted: (results: readonly IndicatorChartLayer[]) => void;
}) {
  const [layers, setLayers] = useState<Accepted[]>([]);
  const [confirmation, setConfirmation] = useState<{ challenge: ConfirmationChallenge11; preview: PreviewReady11 }>();
  const [undo, setUndo] = useState<RemovalHint>();
  const [pending, setPending] = useState(false);
  const [uncertain, setUncertain] = useState<UncertainWrite>();
  const [message, setMessage] = useState<{ text: Message; failed?: boolean }>();
  const lifetime = useRef<AbortController>(null);
  const state = workspace.snapshot;
  const canDisplay = access?.canDisplay;
  const verify = access?.verify;
  const revoked = access?.withheldStatus('csv', snapshot.dataset_id) === 'revoked';

  useLayoutEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    return () => controller.abort();
  }, []);

  useLayoutEffect(() => {
    setLayers([]);
    setUndo(undefined);
    showAccepted([]);
    if (revoked) return;
    const abort = new AbortController();
    void withReplyDeadline(replyDeadlineMs, signal => readLayers(snapshot, signal), abort.signal)
      .then(({ layers: restored, undo: hint }) => {
        if (abort.signal.aborted || canDisplay && !canDisplay('csv', snapshot.dataset_id)) return;
        setLayers(restored);
        setUndo(hint);
        showAccepted(restored.flatMap(({ document }) => document && matchesChart(document.snapshot, snapshot)
          ? [{
            range: {
              from_ms: Math.max(snapshot.from_ms, document.snapshot.from_ms),
              to_ms: Math.min(snapshot.to_ms, document.snapshot.to_ms),
            },
            result: document.artifact.result,
          }] : []));
      }).catch(error => {
        if (abort.signal.aborted) return;
        if (displayAccessDenied(error)) void verify?.('csv', snapshot.dataset_id);
        setMessage(previous => previous ?? { text: uiMessage('Accepted indicator output could not be restored.'), failed: true });
      });
    return () => abort.abort();
  }, [snapshot, state?.workspace.revision, state?.selection_revision, canDisplay, verify, revoked, showAccepted]);

  function scope(target: MutationScope11['target']): MutationScope11 {
    if (!state) throw new Error();
    return {
      target, workspace_id: state.workspace.id, expected_workspace_revision: state.workspace.revision,
      expected_selection_revision: state.selection_revision, idempotency_key: crypto.randomUUID(),
    };
  }

  async function act<T extends ActionReply>(request: ActionRequest, receive: (reply: T) => void, write?: WriteScope) {
    const owner = lifetime.current?.signal;
    if (!owner || pending || uncertain) return;
    setPending(true);
    setMessage(undefined);
    let rejected = false;
    try {
      const reply = await withReplyDeadline(replyDeadlineMs, deadline => {
        if (!write) return exchangeWorkspace<T>(request, deadline);
        // Only a correlated failure can settle a write at its original revision.
        // Keep observing delivery for that proof, without applying the late reply.
        return exchangeWorkspace<T>(request, owner).then(result => {
          rejected = result.request_id === request.request_id && result.outcome?.state === 'failed';
          return result;
        });
      }, owner);
      if (!owner.aborted) receive(reply);
    } catch (error) {
      if (owner.aborted) return;
      if (displayAccessDenied(error)) void verify?.('csv', snapshot.dataset_id);
      if (error instanceof DOMException && error.name === 'TimeoutError') {
        if (write) setUncertain({ ...write, wasRejected: () => rejected });
        setMessage({ text: write
          ? uiMessage('The indicator change could not be confirmed. Reload the workspace state to review accepted indicators.')
          : uiMessage('The indicator confirmation reply did not arrive. You can try accepting the preview again.'), failed: true });
      } else {
        setMessage({ text: uiMessage('The indicator change could not be completed. Refresh the workspace and try again.'), failed: true });
      }
    } finally {
      if (!owner.aborted) setPending(false);
    }
  }

  async function reconcile() {
    const owner = lifetime.current?.signal;
    if (!owner || !uncertain || pending) return;
    const minimumRevision = uncertain.revision + (uncertain.wasRejected() ? 0 : 1);
    setPending(true);
    try {
      const current = await workspace.checkAfterIndicatorReply(uncertain.workspaceId);
      if (!owner.aborted && current && current.workspace.revision >= minimumRevision) {
        setUncertain(undefined);
        setMessage({ text: uiMessage('Workspace state reloaded. Review the accepted indicators.') });
      }
    } finally {
      if (!owner.aborted) setPending(false);
    }
  }

  function requestAcceptance() {
    if (preview?.artifact.storage !== 'inline') return;
    const request: IndicatorAcceptVersionCommand11 = {
      ...command(), task_id: null,
      scope: scope({ kind: 'chart_interval', snapshot, range: {
        from_ms: preview.snapshot.from_ms, to_ms: preview.snapshot.to_ms,
      } }),
      intent: { operation: 'indicator.acceptVersion', arguments: {
        preview_id: preview.preview_id, result_hash: preview.artifact.result_hash,
      } },
    };
    void act<IndicatorAcceptVersionReply11>(request, reply => {
      const outcome = bodyOf(reply, request.request_id);
      if (outcome.state !== 'awaiting_confirmation' || outcome.challenge.task_id !== preview.task_id) throw new Error();
      setConfirmation({ challenge: outcome.challenge, preview });
    });
  }

  const challenge = confirmation?.challenge;
  const openChallenge = challenge && !revoked && preview === confirmation.preview
    && state?.workspace.id === challenge.workspace_id && state.workspace.revision === challenge.workspace_revision
    && state.selection_revision === challenge.selection_revision;

  function respond(decision: 'approve' | 'deny') {
    if (!openChallenge || !challenge) return;
    const request: TrustedConfirmationDecision11 = {
      ...command(), operation: 'confirmation.respond', challenge_id: challenge.challenge_id, decision,
    };
    setConfirmation(undefined);
    void act<IndicatorAcceptVersionReply11>(request, reply => {
      if (decision === 'deny' && reply.request_id === request.request_id
        && reply.outcome.state === 'failed' && reply.outcome.error.code === 'CANCELLED') return;
      const outcome = bodyOf(reply, request.request_id);
      if (outcome.state !== 'committed') throw new Error();
      clearPreview();
      workspace.refreshAfterIndicatorCommit(outcome.receipt);
      setMessage({ text: uiMessage('Indicator version accepted.') });
    }, decision === 'approve' ? { workspaceId: challenge.workspace_id, revision: challenge.workspace_revision } : undefined);
  }

  function remove(layer: Layer) {
    const request: IndicatorRemoveCommand11 = {
      ...command(), task_id: null, scope: scope({ kind: 'layer', object_id: layer.layer_id }),
      intent: { operation: 'indicator.remove', arguments: { layer_id: layer.layer_id } },
    };
    void act<IndicatorRemoveReply11>(request, reply => {
      const outcome = bodyOf(reply, request.request_id);
      if (outcome.state !== 'committed') throw new Error();
      workspace.refreshAfterIndicatorCommit(outcome.receipt);
      setMessage({ text: uiMessage('Accepted layer removed from the chart.') });
    }, { workspaceId: request.scope.workspace_id, revision: request.scope.expected_workspace_revision });
  }

  function undoRemoval() {
    if (!undo || !state || state.workspace.revision < undo.committed_workspace_revision) return;
    const request: WorkspaceUndoCommand11 = {
      ...command(), task_id: null, scope: scope({ kind: 'layer', object_id: undo.layer_id }),
      intent: { operation: 'workspace.undo', arguments: { operation_id: undo.operation_id } },
    };
    void act<WorkspaceUndoReply11>(request, reply => {
      if (reply.request_id === request.request_id && reply.outcome.state === 'failed'
        && reply.outcome.error.code === 'STALE_CONTEXT') {
        setUndo(undefined);
        workspace.reload();
      }
      const outcome = bodyOf(reply, request.request_id);
      if (outcome.state !== 'committed') throw new Error();
      setUndo(undefined);
      workspace.refreshAfterIndicatorCommit(outcome.receipt);
      setMessage({ text: uiMessage('Accepted layer restored.') });
    }, { workspaceId: request.scope.workspace_id, revision: request.scope.expected_workspace_revision });
  }

  const blocked = pending || !!uncertain || !state || !!workspace.pending || !!workspace.error;
  return <>
    {preview && !openChallenge && <button type="button" className="secondary-button" disabled={blocked}
      onClick={requestAcceptance}>{t('Accept preview')}</button>}
    {openChallenge && <div role="group" aria-label={t('Confirm accepted indicator')}>
      <p>{t('Keep this indicator version with its frozen input, resolved parameters and exact output in this profile.')}</p>
      <button type="button" className="secondary-button" disabled={blocked} onClick={() => respond('approve')}>
        {t('Confirm indicator acceptance')}
      </button>
      <button type="button" className="secondary-button" disabled={blocked} onClick={() => respond('deny')}>
        {t('Cancel indicator acceptance')}
      </button>
    </div>}
    {message && <p role={message.failed ? 'alert' : 'status'}>{t(message.text)}</p>}
    {uncertain && <button type="button" className="secondary-button" disabled={pending || !!workspace.pending}
      onClick={() => void reconcile()}>{t('Reload workspace state')}</button>}
    {layers.length > 0 && <section aria-label={t('Accepted indicators')}>
      <h3>{t('Accepted indicators')}</h3>
      <ul>{layers.map(layer => <li key={layer.layer_id}>
        <span>{!layer.available ? t('Accepted version · source unavailable')
          : layer.document && !matchesChart(layer.document.snapshot, snapshot) ? t('Accepted version · different chart observation')
            : t('Accepted version')} </span>
        <code>{layer.version_id}</code>
        {layer.document && <details>
          <summary>{t('Accepted provenance and values')}</summary>
          <p>{t('Input')}{' '}<code>{layer.document.snapshot.content_hash}</code></p>
          <p>{t('Output')}{' '}<code>{layer.document.artifact.result_hash}</code></p>
          <p>{t('Parameters')}{' '}<code>{JSON.stringify(layer.document.resolved_parameters)}</code></p>
          <IndicatorDiagnostics result={layer.document.artifact.result} />
          <ul>{layer.document.artifact.result.series.map(series => <li key={series.id}>
            {series.id} · {series.points.slice(0, 20).map(point => point.value ?? t('Unknown')).join(', ')}
          </li>)}</ul>
          <IndicatorRegionValues regions={layer.document.artifact.result.regions} />
        </details>}
        <button type="button" className="secondary-button" disabled={blocked} onClick={() => remove(layer)}>{t('Remove accepted layer')}</button>
      </li>)}</ul>
    </section>}
    {undo && <button type="button" className="secondary-button" disabled={blocked || !state || state.workspace.revision < undo.committed_workspace_revision}
      onClick={undoRemoval}>{t('Undo indicator removal')}</button>}
  </>;
}
