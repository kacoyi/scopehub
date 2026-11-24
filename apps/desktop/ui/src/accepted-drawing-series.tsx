import { useLayoutEffect, useState } from 'react';
import type { FrozenSnapshot, Point, SavedSeriesDrawing11 } from '../../../../contracts/generated/protocol';
import { listAccepted, readAcceptedLayer, type Accepted, type Layer } from './accepted-layer-reader';
import { t } from './i18n';
import type { PublishedRevisionAccess } from './published-revision-access';
import type { LocalWorkspace } from './use-local-workspace';

export type BoundSeries = {
  datasetId: string; revision: string; layer: string; version: string; id: string; resultHash: string;
  sourceHash: string; inputHash: string; sourceVolumeUnit: string; points: Point[];
};
type WorkspaceSnapshot = LocalWorkspace['snapshot'];
type Choices = { verifiedFor?: WorkspaceSnapshot; layers: Layer[]; ids?: string[]; hash?: string; bound?: BoundSeries };

// This projection is the admission boundary for the guest's numeric input.
function project(current: FrozenSnapshot, layer: Layer, doc: NonNullable<Accepted['document']>, id: string): BoundSeries {
  const { snapshot, source_snapshot, artifact } = doc;
  const { provenance, series } = artifact.result;
  const matches = series.filter(item => item.id === id);
  if (matches.length !== 1 || [snapshot, source_snapshot, provenance].some(s =>
    s.dataset_id !== current.dataset_id || s.dataset_revision !== current.dataset_revision)
    || ![artifact.result_hash, source_snapshot.content_hash, snapshot.content_hash].every(h => /^[0-9a-f]{64}$/.test(h))) throw new Error();
  const input = matches[0]!.points;
  if (input.length > 256) throw new RangeError();
  if (input.some(({ time_ms, value }, i) => !Number.isSafeInteger(time_ms)
    || Math.abs(time_ms) > 8.64e15 || time_ms < snapshot.from_ms || time_ms >= snapshot.to_ms
    || i > 0 && time_ms <= input[i - 1]!.time_ms || value !== null && !Number.isFinite(value))) throw new Error();

  return { datasetId: current.dataset_id, revision: current.dataset_revision, layer: layer.layer_id, version: layer.version_id, id, resultHash: artifact.result_hash,
    sourceHash: source_snapshot.content_hash, inputHash: snapshot.content_hash,
    sourceVolumeUnit: source_snapshot.series.quantity_unit,
    points: input.map(({ time_ms, value }) => ({ time_ms, value })) };
}

export function useAcceptedDrawingSeries(current: FrozenSnapshot | undefined, canDisplay: PublishedRevisionAccess['canDisplay'], workspaceSnapshot: WorkspaceSnapshot, saved?: SavedSeriesDrawing11) {
  const revision = current?.dataset_id;
  const matching = saved && current?.dataset_id === saved.dataset_id && current.dataset_revision === saved.dataset_revision ? saved : undefined;
  const [selectedLayer, setLayer] = useState<Layer | undefined>(() => matching && ({
    layer_id: matching.layer_id, version_id: matching.version_id, available: false,
  }));
  const version = selectedLayer?.version_id ?? '';
  const [choice, setChoice] = useState(() => matching ? JSON.stringify([matching.series_id, matching.result_hash]) : '');
  const [options, setOptions] = useState<Choices>({ layers: [] });
  const [failure, setFailure] = useState<'unavailable' | 'size'>();
  useLayoutEffect(() => {
    if (!current || !revision || !canDisplay(revision) || !workspaceSnapshot) return;
    const snapshot = current;
    const id = revision;
    const lifetime = new AbortController();
    let running = false;
    async function refresh() {
      if (running || lifetime.signal.aborted) return;
      running = true;
      const next: Choices = { layers: [], verifiedFor: workspaceSnapshot };
      const signal = AbortSignal.any([lifetime.signal, AbortSignal.timeout(5000)]);
      try {
        const { layers } = await listAccepted(id, signal);
        next.layers = layers;
        const layer = layers.find(l => l.version_id === version && l.layer_id === selectedLayer?.layer_id && l.available);
        if (layer) {
          const doc = await readAcceptedLayer(id, layer, signal);
          next.hash = doc.artifact.result_hash;
          next.ids = doc.artifact.result.series.map(s => s.id);
          const seriesId = next.ids.find(id => JSON.stringify([id, next.hash]) === choice);
          if (seriesId !== undefined) next.bound = project(snapshot, layer, doc, seriesId);
        }
        signal.throwIfAborted();
        if (!canDisplay(id)) throw new Error();
        setOptions(next);
        setFailure((version && !layer) || (choice && !next.bound) ? 'unavailable' : undefined);
      } catch (error) {
        if (!lifetime.signal.aborted) {
          setOptions(error instanceof RangeError ? next : { layers: next.layers });
          setFailure(error instanceof RangeError ? 'size' : 'unavailable');
        }
      } finally { running = false; }
    }
    void refresh();
    const timer = window.setInterval(refresh, 2000);
    return () => { lifetime.abort(); clearInterval(timer); };
  }, [current, revision, canDisplay, selectedLayer, version, choice, workspaceSnapshot]);

  const picker = revision && <fieldset><legend>{t('Accepted indicator binding')}</legend>
    <label>{t('Accepted version')}<select value={version} onChange={event => {
      setOptions({ layers: options.layers }); setFailure(undefined);
      setLayer(options.layers.find(layer => layer.version_id === event.target.value)); setChoice('');
    }}><option value="">{t('Choose an accepted version')}</option>
      {options.layers.map(l => <option key={l.layer_id} value={l.version_id} disabled={!l.available}>{l.version_id}</option>)}
    </select></label>
    {options.ids && <label>{t('Numeric series')}<select value={choice} onChange={event => {
      setOptions(({ bound: _bound, ...choices }) => choices); setFailure(undefined);
      setChoice(event.target.value);
    }}>
      <option value="">{t('Choose a series')}</option>
      {options.ids.map(id => <option key={id} value={JSON.stringify([id, options.hash])}>{id}</option>)}
    </select></label>}
    {failure && <p role="status">{failure === 'size' ? t('This preview supports up to 256 observations per series.')
      : t('Accepted series unavailable.')}</p>}
  </fieldset>;
  // Keep an accepted scene in memory while its workspace and output are rechecked.
  // A failed read drops the binding, which also disposes the scene and its undo.
  const checking = Boolean(current && (!workspaceSnapshot || (options.bound && options.verifiedFor !== workspaceSnapshot)));
  return { bound: options.bound, picker, checking };
}

export function SeriesIdentity({ data }: { data: BoundSeries }) {
  return <details className="probe-source"><summary>{t('Accepted series source')}</summary>
    <dl><dt>{t('Dataset ID')}</dt><dd>{data.datasetId}</dd><dt>{t('Dataset revision')}</dt><dd>{data.revision}</dd>
      <dt>{t('Layer ID')}</dt><dd>{data.layer}</dd><dt>{t('Accepted version')}</dt><dd>{data.version}</dd>
      <dt>{t('Numeric series')}</dt><dd>{data.id}</dd><dt>{t('Output')}</dt><dd>{data.resultHash}</dd>
      <dt>{t('Source snapshot hash')}</dt><dd>{data.sourceHash}</dd><dt>{t('Input')}</dt><dd>{data.inputHash}</dd>
      <dt>{t('Output unit')}</dt><dd>{t('Unknown')}</dd><dt>{t('Source volume unit')}</dt><dd>{data.sourceVolumeUnit}</dd></dl>
    <details><summary>{t('Accepted observations')}</summary><table>
      <thead><tr><th>{t('Observation time (UTC)')}</th><th>{t('Value')}</th></tr></thead>
      <tbody>{data.points.map(p => <tr key={p.time_ms}><td>{new Date(p.time_ms).toISOString()}</td>
        <td>{p.value === null ? t('Unavailable') : String(p.value)}</td></tr>)}</tbody>
    </table></details>
  </details>;
}
