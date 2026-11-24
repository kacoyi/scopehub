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
type DrawingResult = {
  viewport: PricePaneViewport;
  program: Drawing;
  marks?: Mark[];
  rejectedAt?: PricePaneFrame;
};
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
  const [results, setResults] = useState<DrawingResult[]>([]);
  const [invalidFile, setInvalidFile] = useState(false);
  const fileEpoch = useRef(0);
  const data = source && sources[source];
  const observing = Boolean(data && (accepted || candidate));

  useLayoutEffect(() => {
    ++fileEpoch.current;
    setCandidate(undefined);
    setAccepted(undefined);
    setInvalidFile(false);
    setResults([]);
    setFrame(undefined);
  }, [source, history]);

  useLayoutEffect(() => {
    setFrame(undefined);
    if (observing && data) return handle.current?.observePricePane(data.bars.map(bar => bar.time_ms), setFrame);
  }, [handle, history, data, observing]);

  useLayoutEffect(() => {
    if (!data || !frame || !observing) return;
    const { viewport } = frame;
    const controller = new AbortController();
    // Coalesce evaluations and cancel superseded work before the new frame paints.
    const timer = window.setTimeout(async () => {
      const selected = data.bars.findIndex(bar => bar.time_ms === frame.crosshair);
      for (const program of [accepted, candidate]) {
        if (!program || controller.signal.aborted) continue;
        const params = Object.fromEntries(program.scalars.map(scalar => [scalar.name, scalar.initial]));
        const next = await evaluateDrawing(program, viewport.projection, { selected, params }, controller.signal);
        if (controller.signal.aborted) return;
        setResults(previous => {
          const current = previous.filter(result => result.viewport === viewport
            && (result.program === accepted || result.program === candidate));
          const existing = current.find(result => result.program === program);
          const result = next.status === 'ready'
            ? { viewport, program, marks: next.marks }
            : { viewport, program, marks: program === accepted ? existing?.marks : undefined, rejectedAt: frame };
          return [...current.filter(entry => entry !== existing), result];
        });
      }
    }, 32);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [accepted, candidate, data, frame, observing]);

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

  const viewport = frame?.viewport;
  // A hover-only change leaves the last verified scene visible while the guest runs.
  // An incompatible viewport has a new identity, even if it later returns to these bounds.
  const acceptedResult = results.find(result => result.viewport === viewport && result.program === accepted);
  const candidateResult = results.find(result => result.viewport === viewport && result.program === candidate);
  const marks = candidateResult?.marks ?? acceptedResult?.marks;
  const crosshair = observing && data && data.bars.find(bar => bar.time_ms === frame?.crosshair);
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
    {candidate && candidateResult?.marks && <button type="button" className="secondary-button" onClick={() => {
      setAccepted(candidate);
      restore();
    }}>{t('Accept chart drawing')}</button>}
    {(candidate || invalidFile) && <button type="button" className="secondary-button" onClick={restore}>
      {accepted ? t('Restore accepted chart drawing') : t('Discard chart drawing draft')}</button>}
    {accepted && <button type="button" className="secondary-button" onClick={() => {
      restore();
      setAccepted(undefined);
      setResults([]);
    }}>{t('Remove chart drawing')}</button>}
    {crosshair && <output aria-label={t('Drawing crosshair observation')}>
      <time dateTime={new Date(crosshair.time_ms).toISOString()}>{new Date(crosshair.time_ms).toISOString()}</time>
      {' · '}{formatNumber(crosshair.close, { useGrouping: false, maximumSignificantDigits: 21 })} {data?.currency}
    </output>}
    {(invalidFile || frame && (candidateResult?.rejectedAt === frame || acceptedResult?.rejectedAt === frame)) &&
      <p role="alert">{t('This chart drawing could not be displayed.')}</p>}
  </div>;
  const overlay = data && viewport && marks && <svg className="chart-drawing-overlay" style={{ width: viewport.width, height: viewport.height }}
    aria-label={t('Projected chart drawing')} role="img">
    <DrawingMarks marks={marks} weight={2} />
  </svg>;
  return { controls, overlay };
}
