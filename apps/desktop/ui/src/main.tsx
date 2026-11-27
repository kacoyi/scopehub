import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { message, t, useLocale } from './i18n';
import { LanguageSettings } from './language-settings';
import { createBlankWorkbench, WorkbenchPrototype } from './workbench-prototype';
import { ChartWorkspace, type RevealModelChartBar } from './chart-workspace';
import { LocalImportEditor, type LocalFolderCatalog } from './local-import-editor';
import { cancelLocalPreview, chooseLocalCsvFolder, hasLocalCsvPicker, localImportErrorMessage, previewLocalImport, renderLocalImportMessage, type LocalImportMessage } from './local-import-transport';
import { LocalPreviewChart } from './local-preview-chart';
import { OpenLocalRevisionDialog } from './local-revision-dialog';
import { ModelChat, type ModelChartAnchor, type ModelChatView } from './model-chat';
import { matchesModelChartSelection, type ModelChartSelection } from './model-chat-transport';
import { usePublishedRevisionAccess } from './published-revision-access';
import { readSampleView, saveSampleView } from './sample-view';
import { nativeTextWorkspaceId, readWorkspaceSnapshot, useLocalWorkspace, type LocalWorkspace } from './use-local-workspace';
import { useRestoredWorkspaceSource } from './workspace-source';

function initialSource(): boolean {
  if (window.location.hash) return window.location.hash === '#demo';
  return readSampleView() !== undefined;
}

type EvidenceNavigation = { anchor: ModelChartAnchor; restore: () => void }
  | { anchor: ModelChartAnchor; unavailable: boolean; returned: boolean };

function App() {
  useLocale();
  const [prototype, setPrototype] = useState(false);
  const [acceptedPrototype, setAcceptedPrototype] = useState(createBlankWorkbench);
  const [legacySample, setLegacySample] = useState(initialSource);
  const demoLinkHandled = useRef(false);
  const [local, setLocal] = useState(false);
  const chartReturnFocus = useRef<HTMLElement | null>(null);
  const backToChart = useRef<HTMLButtonElement>(null);
  const [folder, setFolder] = useState<LocalFolderCatalog | null>(null);
  const [choosing, setChoosing] = useState(false);
  const [pickerError, setPickerError] = useState<LocalImportMessage>();
  const [openingRevision, setOpeningRevision] = useState(false);
  const [modelChatView, setModelChatViewState] = useState<ModelChatView>();
  const [modelSelection, setModelSelection] = useState<ModelChartSelection>();
  const [observedSelectionSnapshot, setObservedSelectionSnapshot] = useState<LocalWorkspace['snapshot']>();
  const revealModelChartBar = useRef<RevealModelChartBar>(null);
  const evidenceCheck = useRef<AbortController | undefined>(undefined);
  const [checkingEvidence, setCheckingEvidence] = useState(false);
  const [evidenceNavigation, setEvidenceNavigation] = useState<EvidenceNavigation>();
  const modelAnchor = evidenceNavigation && 'restore' in evidenceNavigation ? evidenceNavigation.anchor : undefined;
  const localWorkspace = useLocalWorkspace();
  const revisionAccess = usePublishedRevisionAccess(localWorkspace);
  const snapshot = localWorkspace?.snapshot;
  const modelWorkspaceId = snapshot?.workspace.id ?? nativeTextWorkspaceId;
  const source = snapshot?.source;
  const supportsSourcePreference = source !== undefined;
  const sample = localWorkspace ? Boolean(snapshot && (source?.kind === 'sample' || source == null && legacySample)) : legacySample;
  const { restored, retry } = useRestoredWorkspaceSource(localWorkspace, revisionAccess);
  const target = snapshot?.chart_evidence;
  const targetUnavailable = Boolean(target && 'revision_id' in target
    && (!revisionAccess.canDisplay('csv', target.revision_id) || localWorkspace?.unconfirmedRevisions.has(target.revision_id)));
  const displaySnapshot = useMemo(() => snapshot && targetUnavailable
    ? { ...snapshot, chart_evidence: { unavailable: true as const } } : snapshot, [snapshot, targetUnavailable]);
  const workspace = localWorkspace && { ...localWorkspace, snapshot: displaySnapshot };
  const modelSelectionOutdated = modelSelection && observedSelectionSnapshot?.workspace.id === modelSelection.workspaceId
    && observedSelectionSnapshot.selection_revision >= modelSelection.selectionRevision
    && !matchesModelChartSelection(observedSelectionSnapshot, modelSelection);
  const currentModelSelection = !local && !targetUnavailable && !modelSelectionOutdated
    && modelSelection?.selectionRevision === snapshot?.selection_revision
    ? modelSelection : undefined;
  const returnToAnswer = useCallback((unavailable = false) => {
    if (!evidenceNavigation || !('restore' in evidenceNavigation)) return;
    evidenceNavigation.restore();
    setEvidenceNavigation({ anchor: evidenceNavigation.anchor, unavailable, returned: true });
    setModelChatViewState('question');
  }, [evidenceNavigation]);
  const setModelChatView = (view: ModelChatView | undefined) => {
    evidenceCheck.current?.abort();
    evidenceCheck.current = undefined;
    setCheckingEvidence(false);
    if (evidenceNavigation && 'restore' in evidenceNavigation) evidenceNavigation.restore();
    setEvidenceNavigation(undefined);
    setModelChatViewState(view);
  };
  const revealChartBar = async (anchor: ModelChartAnchor) => {
    if (evidenceCheck.current) return;
    const controller = new AbortController();
    evidenceCheck.current = controller;
    setCheckingEvidence(true);
    try {
      // Recheck the local authority without reloading the chart or rereading any rows.
      const confirmed = await readWorkspaceSnapshot(AbortSignal.any([controller.signal, AbortSignal.timeout(5000)]));
      controller.signal.throwIfAborted();
      setObservedSelectionSnapshot(previous => previous?.workspace.id === confirmed.workspace.id
        && previous.selection_revision > confirmed.selection_revision ? previous : confirmed);
      const restore = revealModelChartBar.current?.(anchor, confirmed);
      if (restore) {
        setEvidenceNavigation({ anchor, restore });
        setModelChatViewState(undefined);
      } else setEvidenceNavigation({ anchor, unavailable: true, returned: false });
    } catch {
      if (!controller.signal.aborted) setEvidenceNavigation({ anchor, unavailable: true, returned: false });
    } finally {
      if (evidenceCheck.current === controller) {
        evidenceCheck.current = undefined;
        setCheckingEvidence(false);
      }
    }
  };
  useEffect(() => () => evidenceCheck.current?.abort(), []);
  useLayoutEffect(() => {
    if (local) backToChart.current?.focus();
    else {
      chartReturnFocus.current?.focus();
      chartReturnFocus.current = null;
    }
  }, [local]);
  useEffect(() => {
    const navigate = () => {
      const chosen = window.location.hash === '#demo';
      demoLinkHandled.current = false;
      if (chosen) setLocal(false);
      else if (!supportsSourcePreference) {
        saveSampleView(null);
      }
      setLegacySample(chosen);
    };
    window.addEventListener('hashchange', navigate);
    return () => window.removeEventListener('hashchange', navigate);
  }, [supportsSourcePreference]);
  useEffect(() => {
    if (!legacySample || window.location.hash !== '#demo' || demoLinkHandled.current
      || !supportsSourcePreference || !localWorkspace || localWorkspace.pending || localWorkspace.sourceRecovery) return;
    demoLinkHandled.current = true;
    if (source?.kind === 'sample') return;
    void localWorkspace.chooseSource({ kind: 'sample', instrument_id: readSampleView()?.symbol ?? null }).catch(() => {});
  }, [legacySample, localWorkspace, source, supportsSourcePreference]);

  const leaveSample = () => {
    const leave = () => {
      saveSampleView(null);
      if (window.location.hash === '#demo') {
        window.history.replaceState(window.history.state, '', window.location.pathname + window.location.search);
      }
      setLegacySample(false);
    };
    if (localWorkspace && supportsSourcePreference) void localWorkspace.chooseSource({ kind: 'none' }).then(leave, () => {});
    else leave();
  };
  const chooseFolder = useCallback(async (returnFocus?: HTMLElement) => {
    const picked = await chooseLocalCsvFolder();
    if (picked) {
      if (returnFocus) chartReturnFocus.current = returnFocus;
      setFolder(picked);
      setLocal(true);
    }
    return picked;
  }, []);
  const chooseFromWorkspace = async (returnFocus: HTMLElement) => {
    setChoosing(true);
    setPickerError(undefined);
    try { return Boolean(await chooseFolder(returnFocus)); }
    catch (error) {
      setPickerError(localImportErrorMessage(error) ?? message('The local CSV folder could not be opened.'));
      return false;
    } finally { setChoosing(false); }
  };
  const openSample = () => {
    setLocal(false);
    if (localWorkspace && supportsSourcePreference) {
      if (source?.kind !== 'sample') {
        void localWorkspace.chooseSource({ kind: 'sample', instrument_id: readSampleView()?.symbol ?? null }).catch(() => {});
      }
    } else {
      setLegacySample(true);
      window.location.hash = 'demo';
    }
  };
  const localActions = hasLocalCsvPicker() ? {
    choose: chooseFromWorkspace,
    openRevision: () => setOpeningRevision(true),
    resume: folder ? (returnFocus: HTMLElement) => {
      chartReturnFocus.current = returnFocus;
      setLocal(true);
    } : undefined,
    folderName: folder?.display_name,
    choosing,
    error: pickerError === undefined ? undefined : renderLocalImportMessage(pickerError),
  } : undefined;

  return <>
    {prototype && <WorkbenchPrototype accepted={acceptedPrototype} setAccepted={setAcceptedPrototype}
      close={() => setPrototype(false)} />}
    <ChartWorkspace key={sample ? 'sample' : 'unconfigured'} sample={sample} active={!local}
      leaveSample={leaveSample} localActions={localActions} workspace={workspace} revisionAccess={revisionAccess}
      revealModelChartBar={revealModelChartBar} modelAnchor={modelAnchor} returnToAnswer={returnToAnswer}
      modelDialogOpen={Boolean(modelChatView)} openModelChat={selection => {
        if (modelAnchor) returnToAnswer();
        else {
          setModelSelection(selection);
          setModelChatView('question');
        }
      }}
      openModelSettings={() => {
        setModelSelection(undefined);
        setModelChatView('settings');
      }}
      openPublished={workspace ? () => setOpeningRevision(true) : undefined}
      restoredSource={restored} restoredSampleInstrument={source?.kind === 'sample' ? source.instrument_id ?? undefined : undefined}
      retryRestoredSource={retry} onOpenSample={openSample} openPrototype={() => setPrototype(true)}
      chooseSampleInstrument={workspace && supportsSourcePreference ? async id => {
        if (source?.kind !== 'sample' || source.instrument_id !== id) {
          await workspace.chooseSource({ kind: 'sample', instrument_id: id });
        }
      } : undefined} />
    {folder && <div hidden={!local}>
      <main className="workstation local-workstation">
        <header className="topbar local-source-bar">
          <div className="brand" aria-label="ScopeHub"><span className="brand-mark" aria-hidden="true">S</span><strong>ScopeHub</strong></div>
          <span className="local-source-label"><i aria-hidden="true" />{t('Local CSV')}</span>
          <div className="local-source-controls">
            <button type="button" ref={backToChart} className="secondary-button" onClick={() => setLocal(false)}>{t('Back to chart')}</button>
            <LanguageSettings />
            <button type="button" className="secondary-button" aria-haspopup="dialog" onClick={() => setOpeningRevision(true)}>{t('Open published CSV')}</button>
            <button type="button" className="secondary-button" onClick={openSample}>{t('Open offline sample')}</button>
            <button type="button" className="secondary-button" aria-haspopup="dialog" onClick={() => setModelChatView('question')}>{t('Ask AI')}</button>
          </div>
        </header>
        {workspace?.sourceNotice && <p role="status">{workspace.sourceNotice}</p>}
        {workspace?.sourceRecovery && <div className="local-publication-actions">
          <p role="alert">{workspace.error}</p>
          <button type="button" className="secondary-button" onClick={workspace.reload} disabled={Boolean(workspace.pending)}>
            {t('Reload workspace state')}
          </button>
        </div>}
        <LocalImportEditor key={folder.folder_id} initialCatalog={folder} chooseFolder={chooseFolder}
          previewImport={previewLocalImport} cancelPreview={cancelLocalPreview} revisionAccess={revisionAccess}
          renderChart={result => <LocalPreviewChart result={result} />} workspace={workspace} />
      </main>
    </div>}
    <ModelChat key={modelWorkspaceId} workspaceId={modelWorkspaceId} theme={snapshot?.layouts?.active.definition.theme}
      view={modelChatView} show={setModelChatView} chartSelection={currentModelSelection}
      evidenceFeedback={evidenceNavigation && 'returned' in evidenceNavigation ? evidenceNavigation : undefined}
      checkingEvidence={checkingEvidence} revealChartBar={revealChartBar} clearEvidenceFeedback={() => setEvidenceNavigation(undefined)}
      invalidChartSelectionRevision={!local && currentModelSelection === undefined ? modelSelection?.selectionRevision : undefined}
      discardChartSelection={() => setModelSelection(undefined)} />
    {openingRevision && <OpenLocalRevisionDialog onClose={() => setOpeningRevision(false)} revisionAccess={revisionAccess}
      workspace={workspace} nativeLocalImport={hasLocalCsvPicker()}
      useWorkspaceSource={workspace && supportsSourcePreference
        ? (revision_id, signal) => workspace.chooseSource({ kind: 'frozen', revision_id }, signal) : undefined}
      onWorkspaceSourceSelected={() => {
        if (window.location.hash === '#demo') {
          window.history.replaceState(window.history.state, '', window.location.pathname + window.location.search);
        }
        setLocal(false);
        setOpeningRevision(false);
      }} />}
  </>;
}

const root = document.getElementById('root');
if (!root) throw new Error('Missing application root');
createRoot(root).render(<App />);
