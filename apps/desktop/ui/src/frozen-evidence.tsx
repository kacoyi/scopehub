import { message, t, type Message } from './i18n';
import { useId, useState } from 'react';
import { withReplyDeadline } from './reply-deadline';
import type {
  Bar, FrozenSnapshot, ProjectedChartReply11, ReadLocalImportRevisionOutput, ReadProjectedChart11,
} from '../../../../contracts/generated/protocol';
import { exchangeWorkspace, readWorkspaceAfterAdmission, type LocalWorkspace } from './use-local-workspace';
import { displayAccessDenied, type PublishedRevisionAccess } from './published-revision-access';

export type FrozenChartResult = ReadLocalImportRevisionOutput & { snapshot?: FrozenSnapshot };

export async function openAuthenticatedFrozenChart(revisionId: string, requestId: string, callerSignal?: AbortSignal) {
  const request = { type: 'frozen_chart', revision_id: revisionId, request_id: requestId } as const;
  const result = await withReplyDeadline(15_000,
    signal => readWorkspaceAfterAdmission<FrozenChartResult>(request, signal), callerSignal);
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
  const [inspection, setInspection] = useState<{ revision: number; message: Message; failed?: boolean }>();
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
      const fields = { time: barLabel(first.time_ms), close: String(first.close) };
      const observation = result.rows.length === 1
        ? message('{time} · Close {close}', fields)
        : message('{count} bars · First close {close} at {time}', { ...fields, count: result.rows.length });
      if (access.canDisplay(snapshot.dataset_id)) setInspection({ revision: state.selection_revision, message: observation });
    } catch (error) {
      if (displayAccessDenied(error)) void access.verify(snapshot.dataset_id);
      if (!access.canDisplay(snapshot.dataset_id)) return;
      const code = error && typeof error === 'object' && 'code' in error ? error.code : undefined;
      setInspection({
        revision: state.selection_revision,
        message: code === 'STALE_CONTEXT' ? message('The shared selection changed. Refresh it and try again.')
          : message('The shared chart data is unavailable.'),
        failed: true,
      });
    } finally {
      setPending(false);
    }
  }

  return <section className="local-import-card" aria-labelledby={heading}>
    <h2 id={heading}>{t('Shared chart evidence')}</h2>
    <p role="status">{current ? current.to_ms === current.from_ms + 1 ? t('Shared bar · {time}', { time: barLabel(current.from_ms) })
      : t('Shared interval · {from} to {to}', { from: barLabel(current.from_ms), to: barLabel(current.to_ms) })
      : available ? t('A different frozen chart is selected.') : selected ? t('The shared selection is unavailable.')
        : t('No shared selection.')}</p>
    <div className="local-publication-actions">
      <label>{t('Bar to share')}{' '}
        <select value={barTime} disabled={blocked} onChange={event => setBarTime(Number(event.currentTarget.value))}>
          {bars.map(bar => <option key={bar.time_ms} value={bar.time_ms}>{barLabel(bar.time_ms)}</option>)}
        </select>
      </label>
      <button type="button" className="secondary-button" disabled={blocked || barTime === undefined}
        onClick={() => barTime !== undefined && workspace.chooseEvidence({
          kind: 'chart_interval', snapshot, range: { from_ms: barTime, to_ms: barTime + 1 },
        })}>{t('Share selected bar')}</button>
      <button type="button" className="secondary-button" disabled={blocked || !selected}
        onClick={() => workspace.chooseEvidence(null)}>{t('Clear shared selection')}</button>
      <button type="button" className="secondary-button" disabled={blocked || !current}
        onClick={() => void inspect()}>{t('Inspect shared bar')}</button>
      <button type="button" className="secondary-button" disabled={pending || Boolean(workspace.pending || workspace.sourceRecovery)}
        onClick={() => { setInspection(undefined); reload(); }}>{t('Refresh shared selection')}</button>
    </div>
    {workspace.error && !workspace.sourceRecovery && <p role="alert" className="workspace-error">{workspace.error}</p>}
    {workspace.selectionNotice && <p role="status">{workspace.selectionNotice}</p>}
    {current && inspection && inspection.revision === state?.selection_revision &&
      <p role={inspection.failed ? 'alert' : 'status'}>{t(inspection.message)}</p>}
  </section>;
}

export function UnavailableSharedSelection({ workspace }: { workspace: LocalWorkspace | undefined }) {
  const target = workspace?.snapshot?.chart_evidence;
  if (!workspace || !target || !('unavailable' in target)) return null;
  return <div className="local-publication-actions">
    <span role="status">{t('The shared selection is unavailable.')}</span>
    <button type="button" className="secondary-button" disabled={Boolean(workspace.pending || workspace.error)}
      onClick={() => workspace.chooseEvidence(null)}>{t('Clear shared selection')}</button>
    {workspace.selectionNotice && <span role="status">{workspace.selectionNotice}</span>}
    {workspace.error && !workspace.sourceRecovery && <span role="alert">{workspace.error}</span>}
    {!workspace.sourceRecovery && (workspace.error || workspace.selectionNotice) &&
      <button type="button" className="secondary-button" onClick={workspace.reload}>{t('Refresh shared selection')}</button>}
  </div>;
}
