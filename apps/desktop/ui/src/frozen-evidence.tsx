import { useId, useState } from 'react';
import type {
  Bar, FrozenSnapshot, ProjectedChartReply11, ReadLocalImportRevisionOutput, ReadProjectedChart11,
} from '../../../../contracts/generated/protocol';
import { exchangeWorkspace, type LocalWorkspace } from './use-local-workspace';
import { displayAccessDenied, type PublishedRevisionAccess } from './published-revision-access';

export type FrozenChartResult = ReadLocalImportRevisionOutput & { snapshot?: FrozenSnapshot };

export async function openAuthenticatedFrozenChart(revisionId: string, requestId: string) {
  const result = await exchangeWorkspace<FrozenChartResult>(
    { type: 'frozen_chart', revision_id: revisionId, request_id: requestId }, AbortSignal.timeout(15_000),
  );
  if (result.snapshot?.dataset_id !== revisionId || result.snapshot.dataset_revision !== revisionId
    || !/^[0-9a-f]{64}$/.test(result.snapshot.content_hash) || result.bars.length !== result.revision.bar_count) {
    throw new Error('The frozen chart reply could not be verified.');
  }
  return result;
}

const barLabel = (time: number) => new Date(time).toISOString().replace('T', ' ').replace('.000Z', ' UTC');
const inspectFields = ['time_ms', 'close'] as const;

export function FrozenEvidenceControls({ snapshot, bars, workspace, access }: {
  snapshot: FrozenSnapshot; bars: readonly Bar[]; workspace: LocalWorkspace; access: PublishedRevisionAccess;
}) {
  const heading = useId();
  const [barTime, setBarTime] = useState(() => bars.at(-1)?.time_ms);
  const [pending, setPending] = useState(false);
  const [inspection, setInspection] = useState<{ revision: number; message: string; failed?: boolean }>();
  const state = workspace.snapshot;
  const selected = state?.chart_evidence;
  const available = selected && 'revision_id' in selected ? selected : undefined;
  const current = available?.revision_id === snapshot.dataset_id ? available.range : undefined;
  const blocked = !state || pending || Boolean(workspace.pending || workspace.error);
  const reload = workspace.reload;

  async function inspect() {
    if (!state || !current || blocked) return;
    setPending(true);
    setInspection(undefined);
    const request: ReadProjectedChart11 = {
      protocol_version: '1.1', type: 'request', operation: 'workspace.readChartProjection',
      request_id: crypto.randomUUID(), message_id: crypto.randomUUID(), timestamp_ms: Date.now(),
      workspace_id: state.workspace.id, selection_revision: state.selection_revision,
      requested_fields: [...inspectFields],
    };
    try {
      const reply = await exchangeWorkspace<ProjectedChartReply11>(request, AbortSignal.timeout(15_000));
      if (reply.request_id !== request.request_id || reply.operation !== request.operation
        || reply.type !== 'response' || reply.protocol_version !== '1.1') throw new Error();
      if ('error' in reply) throw reply.error;
      const { result } = reply;
      if (result.source_reference !== snapshot.dataset_id || result.revision !== snapshot.dataset_revision
        || result.fields.join(',') !== inspectFields.join(',') || result.rows.length === 0) throw new Error();
      const first = result.rows[0];
      if (result.rows.some(row => Object.keys(row).length !== inspectFields.length
        || typeof row.close !== 'number' || typeof row.time_ms !== 'number'
        || row.time_ms < current.from_ms || row.time_ms >= current.to_ms) || first?.time_ms === undefined) throw new Error();
      const message = result.rows.length === 1
        ? `${barLabel(first.time_ms)} · Close ${first.close}`
        : `${result.rows.length} bars · First close ${first.close} at ${barLabel(first.time_ms)}`;
      if (access.canDisplay(snapshot.dataset_id)) setInspection({ revision: state.selection_revision, message });
    } catch (error) {
      if (displayAccessDenied(error)) void access.verify(snapshot.dataset_id);
      if (!access.canDisplay(snapshot.dataset_id)) return;
      const code = error && typeof error === 'object' && 'code' in error ? error.code : undefined;
      setInspection({
        revision: state.selection_revision,
        message: code === 'STALE_CONTEXT' ? 'The shared selection changed. Refresh it and try again.'
          : 'The shared chart data is unavailable.',
        failed: true,
      });
    } finally {
      setPending(false);
    }
  }

  return <section className="local-import-card" aria-labelledby={heading}>
    <h2 id={heading}>Shared chart evidence</h2>
    <p role="status">{current ? current.to_ms === current.from_ms + 1 ? `Shared bar · ${barLabel(current.from_ms)}`
      : `Shared interval · ${barLabel(current.from_ms)} to ${barLabel(current.to_ms)}`
      : available ? 'A different frozen chart is selected.' : selected ? 'The shared selection is unavailable.'
        : 'No shared selection.'}</p>
    <div className="local-publication-actions">
      <label>Bar to share{' '}
        <select value={barTime} disabled={blocked} onChange={event => setBarTime(Number(event.currentTarget.value))}>
          {bars.map(bar => <option key={bar.time_ms} value={bar.time_ms}>{barLabel(bar.time_ms)}</option>)}
        </select>
      </label>
      <button type="button" className="secondary-button" disabled={blocked || barTime === undefined}
        onClick={() => barTime !== undefined && workspace.chooseEvidence({
          kind: 'chart_interval', snapshot, range: { from_ms: barTime, to_ms: barTime + 1 },
        })}>Share selected bar</button>
      <button type="button" className="secondary-button" disabled={blocked || !selected}
        onClick={() => workspace.chooseEvidence(null)}>Clear shared selection</button>
      <button type="button" className="secondary-button" disabled={blocked || !current}
        onClick={() => void inspect()}>Inspect shared bar</button>
      <button type="button" className="secondary-button" disabled={pending || Boolean(workspace.pending)}
        onClick={() => { setInspection(undefined); reload(); }}>Refresh shared selection</button>
    </div>
    {workspace.error && <p role="alert" className="workspace-error">{workspace.error}</p>}
    {workspace.selectionNotice && <p role="status">{workspace.selectionNotice}</p>}
    {current && inspection && inspection.revision === state?.selection_revision &&
      <p role={inspection.failed ? 'alert' : 'status'}>{inspection.message}</p>}
  </section>;
}

export function UnavailableSharedSelection({ workspace }: { workspace: LocalWorkspace | undefined }) {
  const target = workspace?.snapshot?.chart_evidence;
  if (!workspace || !target || !('unavailable' in target)) return null;
  return <div className="local-publication-actions">
    <span role="status">The shared selection is unavailable.</span>
    <button type="button" className="secondary-button" disabled={Boolean(workspace.pending || workspace.error)}
      onClick={() => workspace.chooseEvidence(null)}>Clear shared selection</button>
    {workspace.selectionNotice && <span role="status">{workspace.selectionNotice}</span>}
    {workspace.error && <>
      <span role="alert">{workspace.error}</span>
      <button type="button" className="secondary-button" onClick={workspace.reload}>Refresh shared selection</button>
    </>}
  </div>;
}
