import { useEffect, useRef, useState, type ReactNode } from 'react';
import { formatDateTime, formatNumber, t } from './i18n';
import { LanguageSettings } from './language-settings';
import { sources, type WorkbenchAuthoringTurn } from './workbench-composition';
import { CalculatedReadout } from './drawing-readout';
import type { LocalWorkspace } from './use-local-workspace';
import drawingExample from './drawing-sample.js?raw';
import { allowedValue, drawingPrompt, height, programLimit, readProgram, seriesBounds, viewBounds, viewport, width, type Drawing, type Mark, type ViewBounds, type Target, type Controls } from './drawing-program';
import type { FrozenSnapshot } from '../../../../contracts/generated/protocol';
import type { PublishedRevisionAccess } from './published-revision-access';
import { SeriesIdentity, useAcceptedDrawingSeries, type BoundSeries } from './accepted-drawing-series';
import { evaluateDrawing } from './drawing-evaluation';
import './drawing-workbench.css';

type Origin = 'sample' | 'model' | 'editor';
type Scene = { program: Drawing; marks: Mark[]; targets: Target[]; controls: Controls; origin: Origin; bounds: ViewBounds; expanded: boolean };
type Status = 'idle' | 'running' | 'rejected' | 'timeout';
const inks = ['#60d4bf', '#e8af72', '#a8a4fc', '#e68ca8'];

function DrawingSurface({ scene, weight, select, series }: { series: BoundSeries | undefined; scene: Scene; weight: number; select: (index: number) => void }) {
  const { program, marks } = scene;
  const source = program.source === 'series' ? undefined : sources[program.source];
  const rows = source?.bars ?? series!.points;
  const scale = viewport(scene.bounds);
  const overlay = program.mode === 'overlay';
  const label = overlay ? t('Price overlay') : t('Axis-free drawing');
  const time = (ms: number) => !source ? new Date(ms).toISOString() : formatDateTime(ms, { hour: '2-digit', minute: '2-digit', timeZone: source.timezone, hourCycle: 'h23' });
  return <figure className="drawing-surface">
    <figcaption><strong>{label}</strong><span>{scene.origin === 'sample' ? t('Synthetic drawing sample') : scene.origin === 'model' ? t('Model reply') : t('Local edit')} · {source ? source.instrument_id + ' · ' + source.timeframe : series?.id}</span></figcaption>
    <svg viewBox={`0 0 ${width} ${height}`} role={scene.targets.length ? "group" : "img"} aria-label={label}>
      {overlay && source && <g className="drawing-axis" aria-label={t('Frozen close reference')}>
        {[scene.bounds.high, scene.bounds.low].map(price =>
          <g key={price}><text x="4" y={scale.yValue(price) + 4}>{formatNumber(price)}</text>
            <line x1="60" x2={width - 20} y1={scale.yValue(price)} y2={scale.yValue(price)} /></g>)}
        {[scene.bounds.from, scene.bounds.to].map(ms => <text key={ms} x={scale.xTime(ms)} y={height - 8}
          textAnchor="middle">{time(ms)}</text>)}
        <polyline points={source.bars.map(b => scale.xTime(b.time_ms) + ',' + scale.yValue(b.close)).join(' ')} />
      </g>}
      <g aria-label={t('Program geometry')} strokeWidth={weight}>
        {marks.map((mark, i) => {
          const [kind, x, y, a, b] = mark;
          const stroke = inks[mark.at(-1) as number];
          if (kind === 'circle') return <circle key={i} cx={x} cy={y} r={a} stroke={stroke} fill="none" />;
          if (kind === 'rect') return <rect key={i} x={x} y={y} width={a} height={b} stroke={stroke} fill="none" />;
          return <line key={i} x1={x} y1={y} x2={a} y2={b} stroke={stroke} />;
        })}
      </g>
      {scene.targets.map(([index, x, y, radius]) =>
        <circle key={index} className="drawing-target" cx={x} cy={y} r={radius} fill="transparent"
          tabIndex={0} role="button" aria-pressed={scene.controls.selected === index}
          aria-label={t('Inspect observation at {time}', { time: time(rows[index]!.time_ms) })}
          onClick={() => select(index)} onKeyDown={event => {
            if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); select(index); }
          }} />)}
    </svg>
    {source && <details className="probe-source"><summary>{t('Frozen synthetic source')}</summary>
      <dl><dt>{t('Dataset ID')}</dt><dd>{source.dataset_id}</dd>
        <dt>{t('Dataset revision')}</dt><dd>{source.dataset_revision}</dd>
        <dt>{t('Snapshot hash')}</dt><dd>{source.snapshot_hash}</dd>
        <dt>{t('Time zone')}</dt><dd>{source.timezone}</dd>
      </dl>
    </details>}
  </figure>;
}

function Observation({ scene, workspace, series }: { series: BoundSeries | undefined; scene: Scene; workspace: LocalWorkspace | undefined }) {
  if (!scene.targets.some(target => target[0] === scene.controls.selected)) return null;
  if (scene.program.source === 'series') {
    const point = series?.points[scene.controls.selected];
    return point && <p aria-label={t('Accepted observation')}>{new Date(point.time_ms).toISOString()} · {
      point.value === null ? t('Unavailable') : String(point.value)} · {t('Unknown')}</p>;
  }
  const data = sources[scene.program.source];
  const bar = data.bars[scene.controls.selected]!;
  const state = workspace?.snapshot;
  const diff = scene.program.difference;
  const available = state?.source?.kind === 'sample' && (!state.source.instrument_id || state.source.instrument_id === data.instrument_id)
    && !workspace?.sourceRecovery && !workspace?.error;
  const query = diff && available && state && {
    source: scene.program.source, field: diff.field, parameter: scene.controls.params[diff.parameter]!,
    workspaceId: state.workspace.id, revision: state.workspace.revision, selectionRevision: state.selection_revision,
  };
  return <section className="authored-evidence" aria-label={t('Selected frozen evidence')}>
    <h3>{t('Selected frozen evidence')}</h3>
    <p>{data.instrument_id} · <time dateTime={new Date(bar.time_ms).toISOString()}>{formatDateTime(bar.time_ms,
      { timeZone: data.timezone, dateStyle: 'short', timeStyle: 'short' })}</time> · {data.timezone}</p>
    <dl><dt>{t('Close price')}</dt><dd>{formatNumber(bar.close)} {data.currency}</dd>
      <dt>{t('Volume')}</dt><dd>{formatNumber(bar.volume)} {data.quantity_unit === 'contracts' ? t('contracts') : t('shares')}</dd></dl>
    {diff && (query ? <CalculatedReadout key={JSON.stringify(query)} query={query} time={bar.time_ms} />
      : <p>{t('Calculated readout unavailable. Open the matching offline sample in a local workspace.')}</p>)}
  </section>;
}

type StudioProps = { hasWorkspace: boolean; workspace: LocalWorkspace | undefined; ask: (turn: WorkbenchAuthoringTurn) => void; close: () => void };

export function DrawingWorkbench(props: StudioProps & { bindingSnapshot: FrozenSnapshot | undefined; sourceUnavailable: boolean; access: PublishedRevisionAccess }) {
  const { bound, picker } = useAcceptedDrawingSeries(props.bindingSnapshot, props.access.canDisplay);
  // Unmount all scene, undo, model-turn and worker state when a binding leaves scope.
  const key = JSON.stringify([bound?.revision, bound?.layer, bound?.version, bound?.id, bound?.resultHash]);
  return <Studio key={key} {...props} series={bound} picker={props.sourceUnavailable ? <p role="status">{t('Accepted series unavailable.')}</p> : picker} />;
}

function Studio({ ask, close, hasWorkspace, workspace, series, picker }: StudioProps & {
  series: BoundSeries | undefined; picker: ReactNode;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const scope = useRef(Symbol());
  const epoch = useRef(0);
  const pendingControls = useRef<{ code: string; controls: Controls } | undefined>(undefined);
  const job = useRef<AbortController | undefined>(undefined);
  const [source, setSource] = useState('');
  const [draft, setDraft] = useState<Scene>();
  const [accepted, setAccepted] = useState<Scene>();
  const [previous, setPrevious] = useState<Scene | null>();
  const [status, setStatus] = useState<Status>('idle');
  const [weight, setWeight] = useState(3);
  const visible = draft ?? accepted;

  useEffect(() => {
    const host = dialog.current;
    host?.showModal();
    return () => { ++epoch.current; job.current?.abort(); host?.close(); };
  }, []);

  function edit(code: string) {
    ++epoch.current;
    job.current?.abort();
    pendingControls.current = undefined;
    setDraft(undefined);
    setStatus('idle');
    setSource(code);
  }

  function preview(text: string, origin: Origin, expanded = false, reframeAccepted = false, update?: Controls) {
    const program = readProgram(text);
    job.current?.abort();
    pendingControls.current = undefined;
    if (!update) {
      setDraft(undefined);
      setSource(program?.code ?? text.slice(0, programLimit));
    }
    if (!program || (program.source === 'series') !== Boolean(series)) { setStatus('rejected'); return false; }
    const controller = new AbortController();
    job.current = controller;
    setStatus('running');
    const bounds = program.source === 'series' ? seriesBounds(series!.points) : viewBounds(program.source, expanded);
    const base = visible?.program.source === program.source ? visible : undefined;
    const params = Object.fromEntries(program.scalars.map(scalar => {
      const old = base?.program.scalars.find(item => item.name === scalar.name);
      const value = base?.controls.params[scalar.name];
      const unchanged = old && old.min === scalar.min && old.max === scalar.max && old.step === scalar.step && old.initial === scalar.initial;
      return [scalar.name, unchanged && value !== undefined && allowedValue(scalar, value) ? value : scalar.initial];
    }));
    const controls = update ?? { params, selected: base?.controls.selected ?? -1 };
    if (update) pendingControls.current = { code: program.code, controls };
    void evaluateDrawing(program, bounds, controls, controller.signal, series?.points).then(result => {
      if (controller.signal.aborted || result.status === 'cancelled') return;
      pendingControls.current = undefined;
      if (result.status === 'ready') {
        const scene = { program, marks: result.marks, targets: result.targets, controls, origin, bounds, expanded };
        if (reframeAccepted) setAccepted(scene);
        else setDraft(scene);
        setStatus('idle');
      } else setStatus(result.status);
    });
    return 'evaluating' as const;
  }

  function changeControls(update: (current: Controls) => Controls) {
    if (!visible) return;
    ++epoch.current;
    const pending = pendingControls.current;
    const current = pending?.code === visible.program.code ? pending.controls : visible.controls;
    preview(visible.program.code, visible.origin, visible.expanded, !draft, update(current));
  }

  async function openFile(file: File) {
    const expected = ++epoch.current;
    job.current?.abort();
    try {
      if (file.size > programLimit * 4 + 32) throw new Error();
      const text = new TextDecoder('utf-8', { fatal: true }).decode(await file.arrayBuffer());
      if (epoch.current === expected && dialog.current?.open) preview(text, 'editor');
    } catch {
      if (epoch.current === expected) { setStatus('rejected'); setDraft(undefined); }
    }
  }

  function author() {
    const expected = ++epoch.current;
    ask({ scope: scope.current, prompt: drawingPrompt(accepted?.program, Boolean(series)), receive(text) {
      return dialog.current?.open && epoch.current === expected ? preview(text, 'model') : false;
    } });
  }

  return <dialog className="probe" ref={dialog} aria-label={t('Drawing studio')}
    onCancel={event => { event.preventDefault(); close(); }}>
    <header className="probe-header">
      <button type="button" className="secondary-button" onClick={close}>{t('Back to chart')}</button>
      <h1>{t('Drawing studio')}</h1><LanguageSettings />
    </header>
    <div className="probe-body drawing-layout">
      <section className="probe-editor">
        {picker}
        <h2>{t('Drawing program')}</h2>
        <button type="button" className="primary" disabled={!hasWorkspace} onClick={author}>
          {accepted ? t('Ask AI to revise the view') : t('Ask AI to author a view')}</button>
        {!hasWorkspace && <p className="probe-hint">{t('Open Drawing studio in a connected workspace to use the model chat.')}</p>}
        <label>{t('Program source')}<textarea spellCheck={false} value={source} maxLength={programLimit}
          onChange={event => edit(event.target.value)} /></label>
        <div className="probe-actions"><button type="button" disabled={Boolean(series)} onClick={() => {
          edit(drawingExample); preview(drawingExample, 'sample');
        }}>{t('Try sample program')}</button>
          <button type="button" disabled={!source || status === 'running'} onClick={() => {
            ++epoch.current; preview(source, 'editor');
          }}>{t('Preview draft')}</button></div>
        <label>{t('Open local drawing program')}<input type="file" accept=".js,.txt" onChange={event => {
          const file = event.currentTarget.files?.[0];
          event.currentTarget.value = '';
          if (file) void openFile(file);
        }} /></label>
        <details><summary>{t('Drawing API')}</summary><p>{t('Use time and price coordinates in overlay mode, or position shapes freely.')}</p>
          <pre dir="ltr">{'// stock|future free|overlay\n// series free: rows contain time_ms, value (number|null)\n640 × 240\nrows: time_ms, open, high, low, close, volume\nxTime(time_ms), yValue(price)\nline(x1,y1,x2,y2,ink)\ncircle(x,y,r,ink)\nrect(x,y,w,h,ink)\nink: 0–3\n// scalar name min max step default\nparams.name, selected, hit(index,x,y,r)\n// difference field name'}</pre></details>
      </section>
      <section className="probe-canvas" aria-label={t('Workbench canvas')}>
        <div className="probe-canvas-heading"><h2>{series ? t('Accepted series preview') : t('Synthetic preview')}</h2>
          <span role="status">{status === 'running' ? t('Evaluating program…') : draft ? t('Draft preview')
            : accepted ? t('Accepted view') : t('Blank canvas')}</span></div>
        {status === 'timeout' && <p role="alert">{t('The drawing exceeded its time limit.')}</p>}
        {status === 'rejected' && <p role="alert">{t('The drawing failed validation or exceeded its limits.')}</p>}
        {series && <SeriesIdentity data={series} />}
        {visible ? <DrawingSurface series={series} scene={visible} weight={weight}
          select={index => changeControls(current => ({ ...current, selected: index }))} />
          : <p className="probe-empty">{series ? t('Open a drawing program for the selected series.') : t('Ask AI to draw from frozen synthetic bars, or try the editable sample.')}</p>}
        {visible && status !== 'running' && <Observation series={series} scene={visible} workspace={workspace} />}
        <div className="drawing-controls">
          {visible?.program.scalars.map(scalar =>
            <label key={scalar.name}>{scalar.name}<input type="range" min={scalar.min} max={scalar.max} step={scalar.step}
              aria-label={scalar.name} value={visible.controls.params[scalar.name]}
              onChange={event => {
                const value = Number(event.target.value);
                changeControls(current => ({ ...current, params: { ...current.params, [scalar.name]: value } }));
              }} />
              <output>{formatNumber(visible.controls.params[scalar.name]!)}</output></label>)}
          <label>{t('Stroke weight')} <input type="range" min="1" max="10" value={weight}
            onChange={event => setWeight(Number(event.target.value))} /><output>{weight}</output></label>
          <div className="probe-actions">
            {visible?.program.mode === 'overlay' && <button disabled={status === 'running' || source !== visible.program.code}
              onClick={() => { ++epoch.current; preview(visible.program.code, visible.origin, !visible.expanded, !draft); }}>
              {visible.expanded ? t('Fit frozen bars') : t('Expand viewport')}</button>}
            {draft && <button disabled={status !== 'idle'} onClick={() => {
              setPrevious(accepted ?? null); setAccepted(draft); edit(draft.program.code);
            }}>{t('Accept view')}</button>}
            {(draft || status !== 'idle' || source !== (accepted?.program.code ?? '')) &&
              <button onClick={() => edit(accepted?.program.code ?? '')}>
                {accepted ? t('Restore accepted view') : t('Discard draft')}</button>}
            {previous !== undefined && <button onClick={() => {
              setAccepted(previous ?? undefined); setPrevious(undefined); edit(previous?.program.code ?? '');
            }}>{t('Undo accepted edit')}</button>}
          </div>
        </div>
      </section>
    </div>
  </dialog>;
}
