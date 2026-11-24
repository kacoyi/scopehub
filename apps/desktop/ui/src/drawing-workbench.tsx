import { useEffect, useRef, useState } from 'react';
import { formatDateTime, formatNumber, t } from './i18n';
import { LanguageSettings } from './language-settings';
import { sources, type WorkbenchAuthoringTurn } from './workbench-composition';
import drawingExample from './drawing-sample.js?raw';
import { drawingPrompt, height, programLimit, readProgram, viewBounds, viewport, width, type Drawing, type Mark, type ViewBounds } from './drawing-program';
import { evaluateDrawing } from './drawing-evaluation';
import './drawing-workbench.css';

type Origin = 'sample' | 'model' | 'editor';
type Scene = { program: Drawing; marks: Mark[]; origin: Origin; bounds: ViewBounds; expanded: boolean };
type Status = 'idle' | 'running' | 'rejected' | 'timeout';
const inks = ['#60d4bf', '#e8af72', '#a8a4fc', '#e68ca8'];

function DrawingSurface({ scene, weight }: { scene: Scene; weight: number }) {
  const { program, marks } = scene;
  const source = sources[program.source];
  const scale = viewport(scene.bounds);
  const overlay = program.mode === 'overlay';
  const label = overlay ? t('Price overlay') : t('Axis-free drawing');
  const time = (ms: number) => formatDateTime(ms, { hour: '2-digit', minute: '2-digit', timeZone: source.timezone, hourCycle: 'h23' });
  return <figure className="drawing-surface">
    <figcaption><strong>{label}</strong><span>{scene.origin === 'sample' ? t('Synthetic drawing sample') : scene.origin === 'model' ? t('Model reply') : t('Local edit')} · {source.instrument_id} · {source.timeframe}</span></figcaption>
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label}>
      {overlay && <g className="drawing-axis" aria-label={t('Frozen close reference')}>
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
    </svg>
    <details className="probe-source"><summary>{t('Frozen synthetic source')}</summary>
      <dl><dt>{t('Dataset ID')}</dt><dd>{source.dataset_id}</dd>
        <dt>{t('Dataset revision')}</dt><dd>{source.dataset_revision}</dd>
        <dt>{t('Snapshot hash')}</dt><dd>{source.snapshot_hash}</dd>
        <dt>{t('Time zone')}</dt><dd>{source.timezone}</dd>
      </dl>
    </details>
  </figure>;
}

export function DrawingWorkbench({ ask, close, hasWorkspace }: { hasWorkspace: boolean; ask: (turn: WorkbenchAuthoringTurn) => void; close: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const scope = useRef(Symbol());
  const epoch = useRef(0);
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
    setDraft(undefined);
    setStatus('idle');
    setSource(code);
  }

  function preview(text: string, origin: Origin, expanded = false, reframeAccepted = false) {
    const program = readProgram(text);
    job.current?.abort();
    setDraft(undefined);
    setSource(program?.code ?? text.slice(0, programLimit));
    if (!program) { setStatus('rejected'); return false; }
    const controller = new AbortController();
    job.current = controller;
    setStatus('running');
    const bounds = viewBounds(program.source, expanded);
    void evaluateDrawing(program, bounds, controller.signal).then(result => {
      if (controller.signal.aborted || result.status === 'cancelled') return;
      if (result.status === 'ready') {
        const scene = { program, marks: result.marks, origin, bounds, expanded };
        if (reframeAccepted) setAccepted(scene);
        else setDraft(scene);
        setStatus('idle');
      } else setStatus(result.status);
    });
    return 'evaluating' as const;
  }

  function author() {
    const expected = ++epoch.current;
    ask({ scope: scope.current, prompt: drawingPrompt(accepted?.program), receive(text) {
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
        <h2>{t('Drawing program')}</h2>
        <button type="button" className="primary" disabled={!hasWorkspace} onClick={author}>
          {accepted ? t('Ask AI to revise the view') : t('Ask AI to author a view')}</button>
        {!hasWorkspace && <p className="probe-hint">{t('Open Drawing studio in a connected workspace to use the model chat.')}</p>}
        <label>{t('Program source')}<textarea spellCheck={false} value={source} maxLength={programLimit}
          onChange={event => edit(event.target.value)} /></label>
        <div className="probe-actions"><button type="button" onClick={() => {
          edit(drawingExample); preview(drawingExample, 'sample');
        }}>{t('Try sample program')}</button>
          <button type="button" disabled={!source || status === 'running'} onClick={() => {
            ++epoch.current; preview(source, 'editor');
          }}>{t('Preview draft')}</button></div>
        <details><summary>{t('Drawing API')}</summary><p>{t('Use time and price coordinates in overlay mode, or position shapes freely.')}</p>
          <pre dir="ltr">{'// stock|future free|overlay\n640 × 240\nrows: time_ms, open, high, low, close, volume\nxTime(time_ms), yValue(price)\nline(x1,y1,x2,y2,ink)\ncircle(x,y,r,ink)\nrect(x,y,w,h,ink)\nink: 0–3'}</pre></details>
      </section>
      <section className="probe-canvas" aria-label={t('Workbench canvas')}>
        <div className="probe-canvas-heading"><h2>{t('Synthetic preview')}</h2>
          <span role="status">{status === 'running' ? t('Evaluating program…') : draft ? t('Draft preview')
            : accepted ? t('Accepted view') : t('Blank canvas')}</span></div>
        {status === 'timeout' && <p role="alert">{t('The drawing exceeded its time limit.')}</p>}
        {status === 'rejected' && <p role="alert">{t('The drawing failed validation or exceeded its limits.')}</p>}
        {visible ? <DrawingSurface scene={visible} weight={weight} />
          : <p className="probe-empty">{t('Ask AI to draw from frozen synthetic bars, or try the editable sample.')}</p>}
        <div className="drawing-controls">
          <label>{t('Stroke weight')} <input type="range" min="1" max="10" value={weight}
            onChange={event => setWeight(Number(event.target.value))} /><output>{weight}</output></label>
          <div className="probe-actions">
            {visible?.program.mode === 'overlay' && <button disabled={status === 'running' || source !== visible.program.code}
              onClick={() => { ++epoch.current; preview(visible.program.code, visible.origin, !visible.expanded, !draft); }}>
              {visible.expanded ? t('Fit frozen bars') : t('Expand viewport')}</button>}
            {draft && <button onClick={() => {
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
