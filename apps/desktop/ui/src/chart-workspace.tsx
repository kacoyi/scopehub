import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import type { LayoutPanel11 } from '../../../../contracts/generated/protocol';
import type { ChartCursorMode, ChartStyle, HistoryChartHandle, IndicatorChartLayer } from './chart-adapter/history';
import { DecisionNotes } from './decision-notes';
import { PrivateRecords } from './private-records';
import { UnavailableSharedSelection } from './frozen-evidence';
import { formatDateTime, message, t, useLocale } from './i18n';
import { instrumentName, symbol } from './instrument-display';
import { IndicatorPreview } from './indicator-preview';
import { LanguageSettings } from './language-settings';
import type { PublishedRevisionAccess } from './published-revision-access';
import { isClosedBarGapRevision, sampleRevisionInstrument, type Bar, type ChartHistory, type Instrument, type SampleRevision } from './market';
import { ReplayGapDetails, replayGapReason } from './market-quality';
import type { ModelChartSelection } from './model-chat-transport';
import { PriceChart } from './price-chart';
import { chartDrawingSource } from './chart-drawing';
import { narrowScreen, readSampleView, saveSampleView } from './sample-view';
import { SymbolSearch } from './symbol-search';
import type { LocalWorkspace } from './use-local-workspace';
import { useHistory, useSampleCatalog } from './use-market-data';
import { useSampleReplay } from './use-sample-replay';
import { WorkspaceIcon, type WorkspaceIconName } from './workspace-icons';
import { layoutDocks, useWorkspaceLayout, WorkspaceLayoutEditor, WorkspaceLayoutReview } from './workspace-layout';
import { LayoutDock, PinnedComparison, useLayoutDocks } from './workspace-layout-view';
import { LocalSourceButtons, WorkspacePanel, panelLabels, type LocalSourceActions, type Panel } from './workspace-panels';
import './workspace-layout.css';

const panelIcons: Record<Panel, WorkspaceIconName> = { watchlist: 'watchlist', data: 'table', sources: 'database' };
const panelOrder = ['watchlist', 'data'] as const;
const layoutPanel = { watchlist: 'watchlist', data: 'inspection' } as const;
const workspacePanel = { watchlist: 'watchlist', inspection: 'data' } as const;
const noInstruments: Instrument[] = [];
const sampleMonth = Date.UTC(2024, 0, 1);
const replayLabels = {
  starting: message('Preparing synthetic replay…'),
  playing: message('Synthetic replay in progress'),
  reconnecting: message('Market worker reconnecting…'),
  resumed: message('Market worker resumed'),
  complete: message('Synthetic replay complete'),
  gap: null,
};

function Tool({ name, icon, onClick, active, disabled }: {
  name: string; icon: WorkspaceIconName; onClick: () => void; active?: boolean; disabled?: boolean;
}) {
  return <button type="button" className="icon-button" title={name} aria-label={name} aria-pressed={active}
    disabled={disabled} onClick={onClick}><WorkspaceIcon name={icon} /></button>;
}

function PlotMessage({ title, children, error = false }: { title: string; children?: ReactNode; error?: boolean }) {
  return <div className="plot-empty"><div className="empty-content">
    <span className="empty-chart-icon" aria-hidden="true"><WorkspaceIcon name="chart" /></span>
    <h1 role={error ? 'alert' : 'status'}>{title}</h1>{children}
  </div></div>;
}

function HistoryStatus({ history, repaired }: { history: ChartHistory; repaired: boolean }) {
  const { freshness, coverage, connection } = history.quality_state;
  const warnings = [
    freshness === 'stale' && t('Stale data'),
    freshness === 'delayed' && t('Delayed observations'),
    coverage === 'gapped' && (repaired ? t('Frozen history has gaps') : t('Known gaps')),
    connection === 'disconnected' && t('Source disconnected'),
  ].filter(Boolean);
  if (warnings.length) return <span className="history-status warning" role="status">{warnings.join(' · ')}</span>;
  return history.bars.length ? <span className="history-status" role="status">{t('History ready')}</span> : null;
}

export type RestoredChartSource = {
  kind: 'frozen';
  revision_id: string;
  status: 'loading' | 'ready' | 'revoked' | 'unavailable';
  chart?: { instrument: Instrument; history: ChartHistory };
  label?: string;
};

export function ChartWorkspace({ sample, leaveSample, localActions, workspace, openPublished, restoredSource, restoredSampleInstrument, chooseSampleInstrument, onOpenSample, openPrototype, openAuthoring, retryRestoredSource, revisionAccess, modelDialogOpen, openModelChat, openModelSettings }: {
  sample: boolean; leaveSample: () => void; localActions: LocalSourceActions | undefined;
  workspace: LocalWorkspace | undefined;
  revisionAccess: PublishedRevisionAccess;
  openPublished: (() => void) | undefined;
  restoredSource?: RestoredChartSource | undefined;
  restoredSampleInstrument?: string | undefined;
  openPrototype: () => void;
  openAuthoring: () => void;
  onOpenSample?: (() => void) | undefined;
  retryRestoredSource?: (() => void) | undefined;
  chooseSampleInstrument?: ((id: string) => Promise<void>) | undefined;
  modelDialogOpen: boolean;
  openModelChat: (selection: ModelChartSelection | undefined) => void;
  openModelSettings: () => void;
}) {
  const locale = useLocale();
  const [saved] = useState(() => sample ? readSampleView() : undefined);
  const [chosenId, setChosenId] = useState(restoredSampleInstrument ?? saved?.symbol ?? '');
  const [sampleTimeframe, setSampleTimeframe] = useState(saved?.timeframe ?? '1m');
  const frozen = !sample && restoredSource?.status === 'ready' ? restoredSource.chart : undefined;
  const timeframe = sample ? sampleTimeframe : frozen?.history.dataset.timeframe ?? (workspace ? workspace.snapshot?.workspace.timeframe ?? '' : sampleTimeframe);
  const workspaceBusy = Boolean(workspace?.pending);
  const workspaceBlocked = Boolean(workspace && (!workspace.snapshot || workspaceBusy || workspace.error));
  const evidence = workspace?.snapshot?.chart_evidence;
  const integratedSample = sample && workspace?.snapshot?.source?.kind === 'sample';
  const hasSampleSelection = Boolean(evidence && 'snapshot_hash' in evidence && evidence.snapshot_hash);
  const sampleFiltersBlocked = integratedSample && (workspace?.pending === 'evidence' || workspace?.pending === 'source'
    || hasSampleSelection && workspaceBlocked);
  const timeframeBlocked = sample ? sampleFiltersBlocked : workspaceBlocked || restoredSource !== undefined;
  const [revision, setRevision] = useState<SampleRevision>(saved?.sampleRevision ?? 'normal');
  const [date, setDate] = useState(saved?.sampleDate ?? '');
  const [style, setStyle] = useState<ChartStyle>(saved?.style ?? 'candlestick');
  const [cursor, setCursor] = useState<ChartCursorMode>(saved?.cursor ?? 'crosshair');
  const [narrow, setNarrow] = useState(() => window.matchMedia(narrowScreen).matches);
  const [panel, setPanel] = useState<Panel | null>(() => {
    return saved?.narrow === narrow && saved.panel !== undefined ? saved.panel : narrow ? null : 'watchlist';
  });
  const [focusChart, setFocusChart] = useState(saved?.chartOnly ?? false);
  const [searchQuery, setSearchQuery] = useState<string | null>(null);
  const [notesOpen, setNotesOpen] = useState(false);
  const [recordsOpen, setRecordsOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const layoutTrigger = useRef<HTMLButtonElement>(null);
  const sourceTrigger = useRef<HTMLButtonElement>(null);
  const choosingInstrument = useRef(false);
  const chart = useRef<HistoryChartHandle | null>(null);
  const dock = useRef<HTMLElement>(null);
  const layout = useWorkspaceLayout(workspace);
  const definition = layout.layout?.definition;
  const docks = useLayoutDocks(definition, narrow);
  const appearance = definition && { theme: definition.theme, gain: definition.gain_color, loss: definition.loss_color };
  const { catalog, retryCatalog } = useSampleCatalog(sample);
  const sampleInstruments = sample && catalog.state === 'ready' ? catalog.value : noInstruments;
  const instruments = frozen ? [frozen.instrument] : sampleInstruments;
  const selected = frozen?.instrument ?? sampleInstruments.find(item => item.id === chosenId) ?? sampleInstruments[0];
  const chartInstrument = useMemo(() => selected && ({
    ...selected, name: instrumentName(selected), display_symbol: symbol(selected),
  }), [selected, locale]);
  const sampleRevision = selected?.id === sampleRevisionInstrument && (timeframe === '1m' || !isClosedBarGapRevision(revision))
    ? revision : 'normal';
  const { result, retryHistory } = useHistory(sample && timeframe ? selected : undefined, timeframe, sampleRevision, date);
  const history = frozen?.history ?? (sample && result.state === 'ready' ? result.value : undefined);
  const indicatorSource = history?.snapshot.content_hash;
  const [previewLayer, setPreviewLayer] = useState<{ source: string; result: IndicatorChartLayer }>();
  const [acceptedLayers, setAcceptedLayers] = useState<{ source: string; results: readonly IndicatorChartLayer[] }>();
  const showPreview = useCallback((result: IndicatorChartLayer | undefined) => {
    setPreviewLayer(result && indicatorSource ? { source: indicatorSource, result } : undefined);
  }, [indicatorSource]);
  const showAccepted = useCallback((results: readonly IndicatorChartLayer[]) => {
    setAcceptedLayers(indicatorSource ? { source: indicatorSource, results } : undefined);
  }, [indicatorSource]);
  const acceptingIndicators = Boolean(workspace && history?.bars.length
    && (frozen || sample && workspace.snapshot?.source?.kind === 'sample'));
  const [lastInspected, setLastInspected] = useState<ChartHistory>();
  const [sampleBar, setSampleBar] = useState<{ snapshot: string; time: number }>();
  const inspectionHistory = sample && definition?.link_selection === false && lastInspected?.dataset.mode === 'simulated'
    ? lastInspected : history;
  useEffect(() => {
    if (sample && history && (definition?.link_selection !== false || !lastInspected)) setLastInspected(history);
  }, [sample, history, definition?.link_selection, lastInspected]);
  const boundSelection = evidence && 'revision_id' in evidence && history
    && evidence.revision_id === history.snapshot.dataset_id
    && (frozen || integratedSample && evidence.snapshot_hash === history.snapshot.content_hash) ? evidence : undefined;
  const sharedBar = boundSelection && boundSelection.range.to_ms === boundSelection.range.from_ms + 1
    ? boundSelection.range.from_ms : undefined;
  const localSelection = sample && !integratedSample;
  const selectedBarTime = !definition?.link_selection ? undefined : localSelection
    ? (sampleBar?.snapshot === history?.snapshot.content_hash ? sampleBar?.time : undefined) : sharedBar;
  const selectionBlocked = Boolean((frozen || integratedSample) && workspaceBlocked);
  const selectBar = definition?.link_selection && history ? (bar: Bar) => {
    if (localSelection) {
      setSampleBar({ snapshot: history.snapshot.content_hash, time: bar.time_ms });
    } else if (workspace?.snapshot && !selectionBlocked && history.bars.some(item => item.time_ms === bar.time_ms)) {
      void workspace.chooseEvidence({
        kind: 'chart_interval', snapshot: history.snapshot, range: { from_ms: bar.time_ms, to_ms: bar.time_ms + 1 },
      });
    }
  } : undefined;
  const clearBar = selectedBarTime !== undefined ? () => {
    if (localSelection) setSampleBar(undefined);
    else if (!selectionBlocked) void workspace?.chooseEvidence(null);
  } : undefined;
  function changeSampleWindow(change: () => void) {
    if (sampleFiltersBlocked) return;
    if (!integratedSample || !hasSampleSelection) {
      change();
    } else {
      void workspace?.chooseEvidence(null).then(cleared => { if (cleared) change(); });
    }
  }
  const failure = catalog.state === 'error' ? catalog.failure : result.state === 'error' ? result.failure : undefined;
  const { replay, start, stop } = useSampleReplay(sample ? history : undefined);
  const workerRecovery = replay?.state === 'reconnecting' || replay?.state === 'resumed' ? replay.state : undefined;
  const playing = replay?.state === 'starting' || replay?.state === 'playing' || workerRecovery !== undefined;
  const replayFailure = replay?.state === 'failed' ? replay.failure : undefined;
  const replayGap = replay?.gap;
  const repairedBars = replay?.repairedBars;
  const replayStatus = replay && replay.state !== 'failed' ? replayLabels[replay.state] : null;
  const replayLabel = replayFailure ? t('Replay failed. {reason}', { reason: t(replayFailure.message) })
    : repairedBars ? t('Closed-bar gap repaired in chart') : replayStatus && t(replayStatus);
  const visiblePanel = panel === 'sources' || (!definition && !focusChart) ? panel : null;
  const activeDocks = focusChart ? [] : layoutDocks.flatMap(dockName => {
    const { panels, active } = docks.selected(dockName);
    return active ? [{ dockName, panels, active }] : [];
  });
  const plotSize = Object.fromEntries(activeDocks.map(({ dockName, active }) => [`--layout-${dockName}`, `${active.size}px`])) as CSSProperties;
  const lastBar = history?.bars.at(-1);
  const canInspect = Boolean(lastBar);
  const canReplay = sample && lastBar && !lastBar.is_closed && history?.quality_state.freshness !== 'stale'
    && (!replayFailure || replayFailure.retryable);
  const savedSymbol = selected?.id ?? chosenId;
  const savedRevision = selected ? sampleRevision : revision;
  useEffect(() => {
    if (sample && restoredSampleInstrument) setChosenId(restoredSampleInstrument);
  }, [sample, restoredSampleInstrument]);
  useEffect(() => {
    if (sample) saveSampleView({
      symbol: savedSymbol, timeframe: sampleTimeframe, style, cursor, panel, chartOnly: focusChart,
      sampleRevision: savedRevision, sampleDate: date, narrow: window.matchMedia(narrowScreen).matches,
    });
  }, [sample, savedSymbol, sampleTimeframe, style, cursor, panel, focusChart, savedRevision, date]);

  const hadLayoutPreview = useRef(false);
  useEffect(() => {
    if (hadLayoutPreview.current && !layout.preview && !layout.editing) layoutTrigger.current?.focus();
    hadLayoutPreview.current = Boolean(layout.preview);
  }, [layout.preview, layout.editing]);

  const searchWasOpen = useRef(false);
  useEffect(() => {
    if (searchQuery === null && searchWasOpen.current) trigger.current?.focus();
    searchWasOpen.current = searchQuery !== null;
  }, [searchQuery]);
  const closeSearch = () => setSearchQuery(null);
  const closePanel = useCallback(() => {
    if (visiblePanel === 'sources') sourceTrigger.current?.focus();
    else dock.current?.querySelector<HTMLButtonElement>('[aria-expanded="true"]')?.focus();
    setPanel(null);
  }, [visiblePanel]);
  const closeDock = (placement: LayoutPanel11) => {
    dock.current?.querySelector<HTMLButtonElement>(`[data-layout-toggle="${placement.panel}"]`)?.focus();
    docks.setDock(placement.dock, null);
  };
  const choose = (item: Instrument) => {
    if (choosingInstrument.current || workspaceBusy) return;
    if (searchQuery !== null) closeSearch();
    const finish = () => {
      if (sample) {
        setChosenId(item.id);
        if (item.id !== sampleRevisionInstrument) setRevision('normal');
      }
      if (narrow) {
        if (visiblePanel) closePanel();
        else if (definition) docks.collapse();
      }
    };
    if (sample && chooseSampleInstrument) {
      choosingInstrument.current = true;
      void chooseSampleInstrument(item.id).then(finish, () => {}).finally(() => { choosingInstrument.current = false; });
    } else {
      finish();
    }
  };
  const chooseTimeframe = (value: string) => {
    if (!sample && workspace) {
      workspace.chooseTimeframe(value);
      return;
    }
    if (value === timeframe) return;
    changeSampleWindow(() => {
      setSampleTimeframe(value);
      if (value !== '1m' && isClosedBarGapRevision(revision)) setRevision('normal');
    });
  };
  useEffect(() => {
    if (timeframe && timeframe !== '1m' && isClosedBarGapRevision(revision)) setRevision('normal');
  }, [timeframe, revision]);
  useEffect(() => {
    const media = window.matchMedia(narrowScreen);
    const changed = () => {
      setNarrow(media.matches);
      if (media.matches) setPanel(null);
    };
    media.addEventListener('change', changed);
    return () => media.removeEventListener('change', changed);
  }, []);
  useEffect(() => {
    const keyboard = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.isComposing || document.querySelector('dialog:modal')) return;
      if (searchQuery !== null || notesOpen || recordsOpen || layout.editing || modelDialogOpen) return;
      if (event.key === 'Escape') {
        if (visiblePanel === 'sources') closePanel();
        else if (focusChart) setFocusChart(false);
        else if (visiblePanel) closePanel();
        return;
      }
      if (event.target instanceof HTMLElement && (event.target.isContentEditable || event.target.closest('input, textarea, select'))) return;
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault(); setSearchQuery('');
      } else if (!event.ctrlKey && !event.altKey && !event.metaKey && (event.key === '/' || /^[a-zA-Z0-9]$/.test(event.key))) {
        event.preventDefault(); setSearchQuery(event.key === '/' ? '' : event.key);
      }
    };
    window.addEventListener('keydown', keyboard);
    return () => window.removeEventListener('keydown', keyboard);
  }, [searchQuery, notesOpen, recordsOpen, layout.editing, focusChart, visiblePanel, closePanel, modelDialogOpen]);
  const openSample = onOpenSample ?? (() => { window.location.hash = 'demo'; });

  let plot: ReactNode;
  if (!sample && !frozen && restoredSource) {
    const reason = restoredSource.status === 'loading' ? t('Restoring published CSV…') : restoredSource.status === 'revoked'
      ? t('Access to this published CSV was revoked.') : t('The published CSV is unavailable.');
    plot = <PlotMessage title={reason} error={restoredSource.status !== 'loading'}>
      <p>{t('Revision')} <code>{restoredSource.revision_id}</code></p>
      {restoredSource.status === 'unavailable' && retryRestoredSource && <button type="button" className="secondary-button"
        onClick={retryRestoredSource}>{t('Retry source')}</button>}
      {restoredSource.status !== 'loading' && visiblePanel !== 'sources' && (localActions ? <LocalSourceButtons actions={localActions} /> : openPublished &&
        <button type="button" className="secondary-button" aria-haspopup="dialog" onClick={openPublished}>{t('Open published CSV')}</button>)}
    </PlotMessage>;
  } else if (!sample && !frozen) {
    plot = <PlotMessage title={t('Open a chart')}>
      {localActions ? <LocalSourceButtons actions={localActions} /> : openPublished &&
        <button type="button" className="secondary-button" aria-haspopup="dialog" onClick={openPublished}>{t('Open published CSV')}</button>}
      <p>{t('Explore three instruments with offline sample data.')}</p>
      <button type="button" className="primary" onClick={openSample}>{t('Open offline sample')} <WorkspaceIcon name="arrow" /></button>
      <button type="button" className="secondary-button" onClick={openAuthoring}>{t('Open blank AI workbench')}</button>
      <button type="button" className="secondary-button" onClick={openPrototype}>{t('Open workbench prototype')}</button></PlotMessage>;
  } else if (sample && failure) {
    const catalogFailed = catalog.state === 'error';
    plot = <PlotMessage title={t(failure.message)} error>{failure.retryable && <button type="button" className="primary"
      onClick={catalogFailed ? retryCatalog : retryHistory}>{catalogFailed ? t('Retry demo') : t('Retry history')}</button>}</PlotMessage>;
  } else if (sample && catalog.state === 'ready' && !selected) {
    plot = <PlotMessage title={t('No instruments in this sample.')} />;
  } else if (!selected || !history) {
    plot = <PlotMessage title={t('Loading synthetic history…')} />;
  } else if (!history.bars.length) {
    plot = <div className="history" data-instrument={selected.id} data-timeframe={timeframe} data-bars={0}>
      <PlotMessage title={sample ? t('No bars in this sample range.') : t('The published CSV has no bars in this revision.')} /></div>;
  } else {
    plot = <PriceChart key={frozen ? restoredSource?.revision_id : selected.id + ':' + timeframe} instrument={chartInstrument ?? selected}
      history={history} style={style} cursor={cursor} handle={chart} replayBar={sample ? replay?.bar : undefined}
      chartDrawing={sample && !repairedBars && new URLSearchParams(location.search).get('chart-drawing') === '1' ? chartDrawingSource(history) : undefined}
      repairedBars={sample ? repairedBars : undefined} selectedBarTime={selectedBarTime} activateBar={selectBar}
      indicatorPreview={acceptingIndicators && previewLayer?.source === indicatorSource ? previewLayer?.result : undefined}
      acceptedIndicators={acceptingIndicators && acceptedLayers?.source === indicatorSource ? acceptedLayers?.results : undefined}
      {...appearance && { appearance }} />;
  }

  const panelProps = {
    sample, selected, instruments, failureKind: (failure ?? replayFailure)?.kind, workerRecovery, replayGap,
    filters: {
      revision: sampleRevision, timeframe, date, blocked: sampleFiltersBlocked,
      setRevision: (value: SampleRevision) => {
        if (value !== sampleRevision) changeSampleWindow(() => setRevision(value));
      },
      setDate: (value: string) => { if (value !== date) changeSampleWindow(() => setDate(value)); },
    }, choose, leave: leaveSample,
    choosingInstrument: workspaceBusy,
    localActions: sample || restoredSource ? localActions : undefined,
    openPublished: (sample || restoredSource) && !localActions ? openPublished : undefined,
    openSample: !sample && restoredSource ? openSample : undefined,
    openModelSettings,
  };
  const savedLayouts = workspace?.snapshot?.layouts;
  const pinned = definition?.pinned_instrument;
  return <main className={'workstation' + (focusChart ? ' chart-only' : '')} style={appearance && {
    '--layout-gain': appearance.gain, '--layout-loss': appearance.loss,
  } as CSSProperties} data-layout-theme={definition?.theme}>
    <header className="topbar">
      <div className="brand" aria-label="ScopeHub"><span className="brand-mark" aria-hidden="true">S</span><strong>ScopeHub</strong></div>
      <div className="toolbar-group symbol-group"><button type="button" ref={trigger} className="symbol-trigger" aria-label={t('Search symbols')}
        aria-haspopup="dialog" title={t('Search symbols (/)')} onClick={() => setSearchQuery('')}>
        <WorkspaceIcon name="search" /><strong>{selected ? symbol(selected) : t('Symbol search')}</strong><WorkspaceIcon name="chevron" /></button></div>
      <div className="toolbar-group periods" role="group" aria-label={t('Timeframe')} aria-busy={!sample && workspaceBusy || undefined}>
        {['1m', '5m', '1d'].map(value => <button type="button" key={value} className="period"
          aria-pressed={timeframe === value} disabled={timeframeBlocked}
          onClick={() => chooseTimeframe(value)}>{value}</button>)}
        {!sample && workspace && <button type="button" className="period" aria-label={t('Undo timeframe change')}
          disabled={timeframeBlocked || !workspace.snapshot?.undo} onClick={workspace.undoTimeframe}>{t('Undo')}</button>}
      </div>
      <div className="toolbar-group chart-styles" role="group" aria-label={t('Chart style')}>
        <Tool name={t('Candles')} icon="candles" active={style === 'candlestick'} onClick={() => setStyle('candlestick')} />
        <Tool name={t('Close-price line')} icon="line" active={style === 'line'} onClick={() => setStyle('line')} />
      </div>
      {workspace && <div className="toolbar-group"><button type="button" className="period notes-trigger"
        aria-haspopup="dialog" disabled={workspaceBlocked} onClick={() => setNotesOpen(true)}>{t('Decision notes')}</button>
        <button type="button" className="period notes-trigger" aria-haspopup="dialog"
          disabled={workspaceBlocked} onClick={() => setRecordsOpen(true)}>{t('Private records')}</button></div>}
      <button type="button" className="period notes-trigger" aria-haspopup="dialog"
        disabled={Boolean(workspace?.pending === 'evidence' || workspace?.pending === 'source')} onClick={() => {
          const state = workspace?.snapshot;
          openModelChat(state && boundSelection && history && !selectionBlocked ? {
            selectionRevision: state.selection_revision,
            sourceReference: history.snapshot.dataset_id,
            datasetRevision: history.snapshot.dataset_revision,
            range: boundSelection.range,
          } : undefined);
        }}>{t('Ask AI')}</button>
      <span className={'connection' + (sample ? ' sample' : '')} role="status"><i aria-hidden="true" />{sample
        ? <><span>{t('Offline sample')}</span><b>{t('Simulated')}</b></> : restoredSource ? frozen ? restoredSource.label ?? t('Published CSV')
          : restoredSource.status === 'revoked' ? t('Access revoked') : restoredSource.status === 'loading' ? t('Restoring CSV') : t('Source unavailable')
          : t('No data source')}</span>
      <button type="button" ref={sourceTrigger} className="icon-button" title={t('Data sources')} aria-label={t('Data sources')}
        aria-expanded={visiblePanel === 'sources'} aria-controls={visiblePanel === 'sources' ? 'workspace-panel' : undefined}
        onClick={() => setPanel(visiblePanel === 'sources' ? null : 'sources')}><WorkspaceIcon name="database" /></button>
      {workspace && <button type="button" ref={layoutTrigger} className="period notes-trigger" aria-haspopup="dialog"
        disabled={!savedLayouts || Boolean(workspace.pending) || layout.busy || Boolean(layout.preview)}
        onClick={() => layout.open()}>{t('Edit layout')}</button>}
      <LanguageSettings />
      <Tool name={focusChart ? t('Exit chart-only view') : t('Chart-only view')} icon={focusChart ? 'collapse' : 'expand'} active={focusChart} onClick={() => setFocusChart(!focusChart)} />
    </header>
    {savedLayouts && <WorkspaceLayoutReview active={savedLayouts.active} state={layout} />}
    <div className={'workbody' + (visiblePanel ? ' with-' + visiblePanel : '')}>
      {!focusChart && <nav className="chart-tools" aria-label={t('Chart tools')}>
        <Tool name={t('Crosshair')} icon="crosshair" active={cursor === 'crosshair'} onClick={() => setCursor('crosshair')} />
        <Tool name={t('Pan')} icon="pan" active={cursor === 'pan'} onClick={() => setCursor('pan')} />
        <span className="tool-divider" />
        <Tool name={t('Zoom in')} icon="zoomIn" disabled={!canInspect} onClick={() => chart.current?.zoomIn()} />
        <Tool name={t('Zoom out')} icon="zoomOut" disabled={!canInspect} onClick={() => chart.current?.zoomOut()} />
        <Tool name={t('Fit chart')} icon="fit" disabled={!canInspect} onClick={() => chart.current?.fit()} />
        {(sample || restoredSource) && <Tool name={t('Open blank AI workbench')} icon="workbench" onClick={openAuthoring} />}
      </nav>}
      <div className={definition ? 'plot-layout layout-grid' : 'plot-layout'} style={plotSize}>
        <section className="plot-workspace" aria-label={t('Price chart')}>
        {plot}
        {!focusChart && pinned && <PinnedComparison key={pinned} id={pinned} instrument={sampleInstruments.find(item => item.id === pinned)} chartReady={Boolean(history)} />}
        <footer className="chart-footer"><span>{sample ? (date ? t('{date} UTC', { date })
          : formatDateTime(sampleMonth, { month: 'short', year: 'numeric', timeZone: 'UTC' })) : 'ScopeHub'}</span>
          <UnavailableSharedSelection workspace={workspace} />
          {workspace?.pending && <span role="status">{workspace.pending === 'open' ? t('Loading local workspace…')
            : workspace.pending === 'evidence' ? t('Sharing chart evidence…')
              : workspace.pending === 'source' ? t('Checking workspace source…') : t('Saving timeframe…')}</span>}
          {workspace?.error && <><span role="alert" className="workspace-error">{workspace.error}</span>
            <button type="button" className="secondary-button" onClick={workspace.reload}>{t('Reload workspace state')}</button></>}
          {workspace?.selectionNotice && <span role="status">{workspace.selectionNotice}</span>}
          {workspace?.sourceNotice && <span role="status">{workspace.sourceNotice}</span>}
          {selected && <span>{selected.timezone}</span>}
          {history && <HistoryStatus history={history} repaired={Boolean(repairedBars)} />}
          {canReplay && <button type="button" className="secondary-button replay-button" onClick={playing ? stop : start}>
            {playing ? t('Stop replay') : replayFailure ? t('Retry replay') : t('Play sample updates')}
          </button>}
          {sample && replay && replayLabel && (!replayGap || replayFailure) && <span role={replayFailure ? 'alert' : 'status'}
            className={'replay-status ' + replay.state}>{replayLabel}</span>}
          {sample && replayGap && <>
            <span role="status" className="replay-status gap">{t('Replay gap. {reason}', { reason: replayGapReason(replayGap) })}</span>
            <ReplayGapDetails gap={replayGap} />
          </>}
          <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">Charts by TradingView Lightweight Charts™</a></footer>
        {acceptingIndicators && workspace && history && <details className="chart-indicators">
          <summary>{t('Indicators')}</summary>
          <IndicatorPreview key={history.snapshot.content_hash} snapshot={history.snapshot} bars={history.bars}
            workspace={workspace} access={frozen ? revisionAccess : undefined}
            showResult={showPreview} showAccepted={showAccepted} />
        </details>}
        </section>
        {activeDocks.map(({ dockName, panels, active }) => <LayoutDock key={dockName} dock={dockName} panels={panels}
          active={active.panel} select={value => docks.setDock(dockName, value)} close={() => closeDock(active)}>
          <WorkspacePanel {...panelProps} layoutPanel={active.panel} panel={workspacePanel[active.panel]}
            history={active.panel === 'inspection' ? inspectionHistory : history} close={() => closeDock(active)}
            selectedBarTime={selectedBarTime} selectBar={selectBar} clearBar={clearBar}
            selectionBlocked={selectionBlocked} />
        </LayoutDock>)}
      </div>
      {narrow && !visiblePanel && activeDocks.length > 0 && <button type="button" className="panel-scrim" aria-label={t('Dismiss side panel')}
        onClick={() => { const active = activeDocks[0]?.active; if (active) closeDock(active); }} />}
      {visiblePanel && <>
        <button type="button" className={definition ? 'panel-scrim layout-source-scrim' : 'panel-scrim'} aria-label={t('Dismiss side panel')} onClick={closePanel} />
        <WorkspacePanel {...panelProps} panel={visiblePanel} history={history} close={closePanel} layoutSource={Boolean(definition)} />
      </>}
      {!focusChart && <nav className="side-tools" aria-label={t('Workspace panels')} ref={dock}>{panelOrder.map(key => {
        const placement = definition?.panels.find(value => value.panel === layoutPanel[key]);
        const expanded = placement ? docks.selected(placement.dock).active?.panel === placement.panel : visiblePanel === key;
        const label = t(panelLabels[key]);
        return <button type="button" className="icon-button" key={key} title={label} aria-label={label}
          data-layout-toggle={placement?.panel} aria-expanded={expanded}
          aria-controls={expanded ? placement ? `workspace-panel-${placement.panel}` : 'workspace-panel' : undefined}
          onClick={() => {
            if (placement) {
              if (visiblePanel === 'sources') setPanel(null);
              docks.setDock(placement.dock, expanded ? null : placement.panel);
            } else setPanel(expanded ? null : key);
          }}><WorkspaceIcon name={panelIcons[key]} /></button>;
      })}</nav>}
    </div>
    {searchQuery !== null && <SymbolSearch instruments={instruments} initialQuery={searchQuery} sample={sample}
      onChoose={choose} onClose={closeSearch} openSample={openSample} />}
    {recordsOpen && workspace?.snapshot && <PrivateRecords workspace={workspace} onClose={() => setRecordsOpen(false)} />}
    {notesOpen && workspace?.snapshot && <DecisionNotes workspace={workspace} onClose={() => setNotesOpen(false)} />}
    {layout.editing && layout.draft && savedLayouts && <WorkspaceLayoutEditor state={{ ...layout, draft: layout.draft }}
      layouts={savedLayouts} instruments={sampleInstruments} />}
  </main>;
}
