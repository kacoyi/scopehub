import { message as uiMessage, t, type Message } from './i18n';
import { useLayoutEffect, useState } from 'react';
import type {
  ConfirmationChallenge11, FrozenSnapshot, IndicatorAcceptVersionCommand11, IndicatorAcceptVersionReply11,
  IndicatorRemoveCommand11, IndicatorRemoveReply11, InlineIndicatorArtifact11,
  MutationScope11, PreviewReady11, TrustedConfirmationDecision11, WorkspaceUndoCommand11, WorkspaceUndoReply11,
} from '../../../../contracts/generated/protocol';
import type { IndicatorChartLayer } from './chart-adapter/history';
import { IndicatorDiagnostics } from './indicator-diagnostics';
import { IndicatorRegionValues } from './indicator-region-values';
import { displayAccessDenied, type PublishedRevisionAccess } from './published-revision-access';
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

function bodyOf<T extends IndicatorAcceptVersionReply11 | IndicatorRemoveReply11 | WorkspaceUndoReply11>(
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
  const [challenge, setChallenge] = useState<ConfirmationChallenge11>();
  const [undo, setUndo] = useState<RemovalHint>();
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState<{ text: Message; failed?: boolean }>();
  const state = workspace.snapshot;
  const canDisplay = access?.canDisplay;
  const verify = access?.verify;
  const revoked = access?.withheldStatus('csv', snapshot.dataset_id) === 'revoked';

  useLayoutEffect(() => {
    setLayers([]);
    setUndo(undefined);
    showAccepted([]);
    if (revoked) return;
    const abort = new AbortController();
    const signal = AbortSignal.any([abort.signal, AbortSignal.timeout(15_000)]);
    void readLayers(snapshot, signal).then(({ layers: restored, undo: hint }) => {
      if (signal.aborted || canDisplay && !canDisplay('csv', snapshot.dataset_id)) return;
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
      if (displayAccessDenied(error)) {
        void verify?.('csv', snapshot.dataset_id);
      }
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

  async function act(action: (signal: AbortSignal) => Promise<void>) {
    if (pending) return;
    setPending(true);
    setMessage(undefined);
    try { await action(AbortSignal.timeout(15_000)); }
    catch (error) {
      if (displayAccessDenied(error)) void verify?.('csv', snapshot.dataset_id);
      setMessage({ text: uiMessage('The indicator change could not be completed. Refresh the workspace and try again.'), failed: true });
    } finally { setPending(false); }
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
    void act(async signal => {
      const outcome = bodyOf(await exchangeWorkspace<IndicatorAcceptVersionReply11>(request, signal), request.request_id);
      if (outcome.state !== 'awaiting_confirmation' || outcome.challenge.task_id !== preview.task_id) throw new Error();
      setChallenge(outcome.challenge);
    });
  }

  function respond(decision: 'approve' | 'deny') {
    if (!challenge) return;
    const request: TrustedConfirmationDecision11 = {
      ...command(), operation: 'confirmation.respond', challenge_id: challenge.challenge_id, decision,
    };
    void act(async signal => {
      setChallenge(undefined);
      const reply = await exchangeWorkspace<IndicatorAcceptVersionReply11>(request, signal);
      if (decision === 'deny' && reply.request_id === request.request_id
        && reply.outcome.state === 'failed' && reply.outcome.error.code === 'CANCELLED') return;
      const outcome = bodyOf(reply, request.request_id);
      if (outcome.state !== 'committed') throw new Error();
      clearPreview();
      workspace.refreshAfterIndicatorCommit(outcome.receipt);
      setMessage({ text: uiMessage('Indicator version accepted.') });
    });
  }

  function remove(layer: Layer) {
    const request: IndicatorRemoveCommand11 = {
      ...command(), task_id: null, scope: scope({ kind: 'layer', object_id: layer.layer_id }),
      intent: { operation: 'indicator.remove', arguments: { layer_id: layer.layer_id } },
    };
    void act(async signal => {
      const outcome = bodyOf(await exchangeWorkspace<IndicatorRemoveReply11>(request, signal), request.request_id);
      if (outcome.state !== 'committed') throw new Error();
      workspace.refreshAfterIndicatorCommit(outcome.receipt);
      setMessage({ text: uiMessage('Accepted layer removed from the chart.') });
    });
  }

  function undoRemoval() {
    if (!undo || !state || state.workspace.revision < undo.committed_workspace_revision) return;
    const request: WorkspaceUndoCommand11 = {
      ...command(), task_id: null, scope: scope({ kind: 'layer', object_id: undo.layer_id }),
      intent: { operation: 'workspace.undo', arguments: { operation_id: undo.operation_id } },
    };
    void act(async signal => {
      const reply = await exchangeWorkspace<WorkspaceUndoReply11>(request, signal);
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
    });
  }

  const blocked = pending || !state || !!workspace.pending || !!workspace.error;
  const openChallenge = challenge && preview?.task_id === challenge.task_id;
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
