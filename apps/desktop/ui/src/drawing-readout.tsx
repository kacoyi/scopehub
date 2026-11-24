import { useEffect, useState } from 'react';
import type { FrozenSnapshot, IndicatorCreateDraftReply11, IndicatorRunPreviewReply11 } from '../../../../contracts/generated/protocol';
import { abortableDelay } from './abortable-delay';
import { formatNumber, t } from './i18n';
import { cancelTask, command, submit, validHash, type Ready, type Waiting, type Terminal } from './indicator-preview-transport';
import { sources } from './workbench-composition';
import type { Drawing } from './drawing-program';

type Query = {
  source: keyof typeof sources; field: NonNullable<Drawing['difference']>['field']; parameter: number;
  workspaceId: string; revision: number; selectionRevision: number;
};
type Readout = { points: { time_ms: number; value: number | null }[]; resultHash: string };

async function calculate(query: Query, signal: AbortSignal): Promise<Readout> {
  const data = sources[query.source];
  const snapshot: FrozenSnapshot = {
    series: { instrument_id: data.instrument_id, provider_id: 'scopehub.synthetic', timeframe: '1m', price_adjustment: 'raw',
      session_scope: 'sample-sessions', calendar_id: data.instrument_id + '.sample-calendar.v1', quantity_unit: data.quantity_unit },
    dataset_id: data.dataset_id, dataset_revision: data.dataset_revision, content_hash: data.snapshot_hash,
    as_of_ms: data.as_of_ms, from_ms: 0, to_ms: data.bars.at(-1)!.time_ms + 60_000, warmup_bars: 0,
  };
  const scope = { workspace_id: query.workspaceId, target: { kind: 'chart' as const, object_id: 'main-chart' },
    expected_workspace_revision: query.revision, expected_selection_revision: query.selectionRevision, idempotency_key: crypto.randomUUID() };
  const create = { ...command(), task_id: null, scope, intent: { operation: 'indicator.createDraft' as const,
    arguments: { language: 'declarative' as const, source: { parameters: [{ type: 'number' as const, name: 'reference', default: 0 }],
      outputs: [{ kind: 'line' as const, id: 'difference', pane: 'difference', style: { color: '#60d4bf', width: 1 },
        value: { op: 'subtract' as const, left: { op: 'field' as const, field: query.field },
          right: { op: 'number_parameter' as const, name: 'reference' } } }] } } } };
  const created = await submit<IndicatorCreateDraftReply11>(create, signal);
  signal.throwIfAborted();
  if (created.request_id !== create.request_id || created.outcome.state !== 'committed') throw new Error();
  const { draft_id, code_hash } = created.outcome.result;
  const range = { from_ms: data.bars[0]!.time_ms, to_ms: data.bars.at(-1)!.time_ms + 1 };
  const run = { ...command(), task_id: null, scope: { ...scope, target: { kind: 'chart_interval' as const, snapshot, range },
    idempotency_key: crypto.randomUUID() }, intent: { operation: 'indicator.runPreview' as const,
    arguments: { draft_id, snapshot, parameters: [{ name: 'reference', value: query.parameter }] } } };
  // Drain submission to obtain the task ID even if this readout is superseded.
  const started = await submit<IndicatorRunPreviewReply11>(run, AbortSignal.timeout(15_000));
  if (started.request_id !== run.request_id || started.outcome.state !== 'committed') throw new Error();
  const id = started.outcome.result.task_id;
  try {
    for (;;) {
      await abortableDelay(80, signal);
      const response = await submit<Ready | Waiting | Terminal>({ type: 'indicator_preview_status',
        request_id: crypto.randomUUID(), task_id: id }, signal);
      if (response.task_id !== id || response.status === 'terminal') throw new Error();
      if (response.status === 'running') continue;
      const { event } = response;
      const artifact = event.artifact;
      if (event.task_id !== id || event.draft_id !== draft_id || event.code_hash !== code_hash
        || !validHash(event.params_hash) || artifact.storage !== 'inline' || !validHash(artifact.result_hash)
        || response.source_snapshot.content_hash !== data.snapshot_hash || event.snapshot.dataset_id !== data.dataset_id
        || event.snapshot.dataset_revision !== data.dataset_revision || event.snapshot.as_of_ms !== data.as_of_ms
        || event.snapshot.from_ms !== range.from_ms || event.snapshot.to_ms !== range.to_ms
        || !response.calendar_hash || !validHash(response.calendar_hash) || response.resolved_parameters.reference !== query.parameter)
        throw new Error();
      const series = artifact.result.series[0];
      const provenance = artifact.result.provenance;
      if (provenance.code_hash !== code_hash || provenance.params_hash !== event.params_hash
        || provenance.dataset_id !== data.dataset_id || provenance.dataset_revision !== data.dataset_revision
        || provenance.engine_version !== event.engine_version || event.engine_version !== 'scopehub-declarative-3'
        || series?.id !== 'difference'
        || series.points.length !== data.bars.length || series.points.some((p, i) =>
          p.time_ms !== data.bars[i]!.time_ms || !Number.isFinite(p.value))) throw new Error();
      return { points: series.points, resultHash: artifact.result_hash };
    }
  } catch (error) { void cancelTask(id).catch(() => {}); throw error; }
}

export function CalculatedReadout({ query, time }: { query: Query; time: number }) {
  const [attempt, retry] = useState(0);
  const [result, setResult] = useState<Readout | 'unavailable'>();
  useEffect(() => {
    setResult(undefined);
    const controller = new AbortController();
    const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(15_000)]);
    void abortableDelay(150, signal).then(() => calculate(query, signal)).then(value => {
      if (!controller.signal.aborted) setResult(value);
    }, () => { if (!controller.signal.aborted) setResult('unavailable'); });
    return () => controller.abort();
  }, [query.source, query.field, query.parameter, query.workspaceId, query.revision, query.selectionRevision, attempt]);
  if (!result) return <p role="status">{t('Calculating in the local workspace…')}</p>;
  const point = result !== 'unavailable' && result.points.find(p => p.time_ms === time);
  if (!point || point.value === null) return <p>{t('Calculated readout unavailable.')} <button onClick={() => retry(value => value + 1)}>{t('Retry calculation')}</button></p>;
  return <><dl><dt>{t('Signed distance')}</dt><dd><span>{formatNumber(point.value, { signDisplay: 'exceptZero', maximumFractionDigits: 8 })}</span>{' '}
    {query.field === 'volume' ? (sources[query.source].quantity_unit === 'contracts' ? t('contracts') : t('shares')) : sources[query.source].currency}</dd>
    <dt>{t('Core preview result hash')}</dt><dd>{result.resultHash}</dd></dl>
    <details><summary>{t('Unrounded core value')}</summary><code>{String(point.value)}</code></details></>;
}
