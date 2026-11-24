import { useLayoutEffect, useRef, useState, type RefObject } from 'react';
import type { HistoryChartHandle, PricePaneFrame, PricePaneViewport } from './chart-adapter/history';
import { DrawingMarks } from './drawing-marks';
import { evaluateDrawing } from './drawing-evaluation';
import { programLimit, readProgram, type Drawing, type Mark } from './drawing-program';
import { formatNumber, t } from './i18n';
import type { ChartHistory } from './market';
import { sources } from './workbench-composition';
import './chart-drawing.css';

type Source = keyof typeof sources;
type DrawingBase = { viewport: PricePaneViewport; program: Drawing; marks: Mark[] | undefined };
type DrawingSelection = { frame: PricePaneFrame; program: Drawing; marks: Mark[] | undefined };

function scheduleDrawing(program: Drawing, viewport: PricePaneViewport, selected: number, update: (marks: Mark[] | undefined) => void) {
  const controller = new AbortController();
  const timer = window.setTimeout(async () => {
    const params = Object.fromEntries(program.scalars.map(scalar => [scalar.name, scalar.initial]));
    const result = await evaluateDrawing(program, viewport.projection, { selected, params }, controller.signal);
    if (!controller.signal.aborted) update(result.status === 'ready' ? result.marks : undefined);
  }, 32);
  return () => { window.clearTimeout(timer); controller.abort(); };
}

export function chartDrawingSource(history: ChartHistory): Source | undefined {
  if (history.snapshot.series.provider_id !== 'scopehub.synthetic') return;
  return (Object.keys(sources) as Source[]).find(id => {
    const source = sources[id];
    const snapshot = history.snapshot;
    return snapshot.dataset_id === source.dataset_id && snapshot.dataset_revision === source.dataset_revision
      && snapshot.series.instrument_id === source.instrument_id && snapshot.series.timeframe === source.timeframe
      && snapshot.as_of_ms === source.as_of_ms
      && source.bars.every(frozen => history.bars.some(bar => bar.time_ms === frozen.time_ms && bar.is_closed
        && bar.open === frozen.open && bar.high === frozen.high && bar.low === frozen.low
        && bar.close === frozen.close && bar.volume === frozen.volume));
  });
}

export function useChartDrawing(handle: RefObject<HistoryChartHandle | null>, history: object, source: Source | undefined) {
  const [accepted, setAccepted] = useState<Drawing>();
  const [candidate, setCandidate] = useState<Drawing>();
  const [frame, setFrame] = useState<PricePaneFrame>();
  const [bases, setBases] = useState<DrawingBase[]>([]);
  const [selection, setSelection] = useState<DrawingSelection>();
  const [invalidFile, setInvalidFile] = useState(false);
  const fileEpoch = useRef(0);
  const overlayElement = useRef<SVGSVGElement>(null);
  const data = source && sources[source];
  const observing = Boolean(data && (accepted || candidate));

  useLayoutEffect(() => {
    ++fileEpoch.current;
    setCandidate(undefined);
    setAccepted(undefined);
    setInvalidFile(false);
    setBases([]);
    setSelection(undefined);
    setFrame(undefined);
  }, [source, history]);

  useLayoutEffect(() => {
    setFrame(undefined);
    if (observing && data) {
      return handle.current?.observePricePane(data.bars.map(bar => bar.time_ms), setFrame, () => {
        if (overlayElement.current) overlayElement.current.style.visibility = 'hidden';
      });
    }
  }, [handle, history, data, observing]);

  const viewport = frame?.viewport;
  const acceptedBase = bases.find(base => base.viewport === viewport && base.program === accepted);
  const candidateBase = bases.find(base => base.viewport === viewport && base.program === candidate);
  const pendingBase = (acceptedBase ? undefined : accepted) ?? (candidateBase ? undefined : candidate);
  // The unselected geometry belongs to the viewport, independently of pointer movement.
  useLayoutEffect(() => {
    if (!viewport || !pendingBase) return;
    return scheduleDrawing(pendingBase, viewport, -1, marks => {
      setBases(previous => [...previous.filter(base => base.viewport === viewport
        && base.program !== pendingBase && (base.program === accepted || base.program === candidate)),
      { viewport, program: pendingBase, marks }]);
    });
  }, [viewport, pendingBase, accepted, candidate]);

  const visibleBase = candidateBase?.marks ? candidateBase : acceptedBase;
  const selectedProgram = visibleBase?.marks ? visibleBase.program : undefined;
  const selectedIndex = data?.bars.findIndex(bar => bar.time_ms === frame?.crosshair) ?? -1;
  useLayoutEffect(() => {
    if (!frame || selectedIndex < 0 || !selectedProgram) return;
    return scheduleDrawing(selectedProgram, frame.viewport, selectedIndex, marks => {
      setSelection({ frame, program: selectedProgram, marks });
    });
  }, [frame, selectedIndex, selectedProgram]);

  async function open(file: File) {
    const epoch = ++fileEpoch.current;
    setInvalidFile(false);
    setCandidate(undefined);
    try {
      if (file.size > programLimit * 4 + 32) throw new Error();
      const draft = readProgram(new TextDecoder('utf-8', { fatal: true }).decode(await file.arrayBuffer()));
      if (epoch !== fileEpoch.current) return;
      if (!draft || draft.source !== source || draft.mode !== 'overlay' || draft.difference) throw new Error();
      setCandidate(draft);
    } catch {
      if (epoch === fileEpoch.current) setInvalidFile(true);
    }
  }

  // Selected geometry is a whole guest result. It can only accompany its own
  // trusted crosshair observation; otherwise render the verified neutral base.
  const currentSelection = selection && selection.frame === frame && selection.program === selectedProgram ? selection : undefined;
  const marks = currentSelection?.marks ?? visibleBase?.marks;
  const crosshair = observing && data && data.bars[selectedIndex];
  const restore = () => {
    ++fileEpoch.current;
    setCandidate(undefined);
    setInvalidFile(false);
  };
  const controls = source && <div className="chart-drawing-controls">
    <label>{t('Open chart drawing program')}<input type="file" accept=".js,.txt" onChange={event => {
      const file = event.currentTarget.files?.[0];
      event.currentTarget.value = '';
      if (file) void open(file);
    }} /></label>
    <div className="chart-drawing-actions">
      <button type="button" className="secondary-button" hidden={!candidateBase?.marks} onClick={() => {
        if (!candidate || !candidateBase?.marks) return;
        setAccepted(candidate);
        restore();
      }}>{t('Accept chart drawing')}</button>
      <button type="button" className="secondary-button chart-drawing-recovery" hidden={!accepted || !(candidate || invalidFile)} onClick={restore}>
        {t('Restore accepted chart drawing')}</button>
      <button type="button" className="secondary-button chart-drawing-recovery" hidden={!!accepted || !(candidate || invalidFile)} onClick={restore}>
        {t('Discard chart drawing draft')}</button>
      <button type="button" className="secondary-button" hidden={!accepted} onClick={() => {
        restore();
        setAccepted(undefined);
        setBases([]);
        setSelection(undefined);
      }}>{t('Remove chart drawing')}</button>
    </div>
    <div className="chart-drawing-status">
      {crosshair && <output aria-label={t('Drawing crosshair observation')}>
        <time dateTime={new Date(crosshair.time_ms).toISOString()}>{new Date(crosshair.time_ms).toISOString()}</time>
        {' · '}{formatNumber(crosshair.close, { useGrouping: false, maximumSignificantDigits: 21 })} {data?.currency}
      </output>}
      {(invalidFile || candidateBase && !candidateBase.marks || acceptedBase && !acceptedBase.marks
        || currentSelection && !currentSelection.marks) && <p role="alert">{t('This chart drawing could not be displayed.')}</p>}
    </div>
  </div>;
  const overlay = data && viewport && marks && <svg ref={overlayElement} className="chart-drawing-overlay" style={{ width: viewport.width, height: viewport.height }}
    aria-label={t('Projected chart drawing')} role="img">
    <DrawingMarks marks={marks} weight={2} />
  </svg>;
  return { controls, overlay };
}
