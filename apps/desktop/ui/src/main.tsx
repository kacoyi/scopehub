import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { message, t, useLocale } from './i18n';
import { LanguageSettings } from './language-settings';
import { WorkbenchPrototype } from './workbench-prototype';
import { DrawingWorkbench } from './drawing-workbench';
import { WorkbenchAuthoring } from './workbench-authoring';
import type { WorkbenchAuthoringTurn } from './workbench-composition';
import { ChartWorkspace } from './chart-workspace';
import { LocalImportEditor, type LocalFolderCatalog } from './local-import-editor';
import { cancelLocalPreview, chooseLocalCsvFolder, hasLocalCsvPicker, localImportErrorMessage, previewLocalImport, renderLocalImportMessage, type LocalImportMessage } from './local-import-transport';
import { LocalPreviewChart } from './local-preview-chart';
import { OpenLocalRevisionDialog } from './local-revision-dialog';
import { ModelChat, type ModelChatView } from './model-chat';
import type { ModelChartSelection } from './model-chat-transport';
import { usePublishedRevisionAccess } from './published-revision-access';
import { readSampleView, saveSampleView } from './sample-view';
import { nativeTextWorkspaceId, useLocalWorkspace } from './use-local-workspace';
import { useRestoredWorkspaceSource } from './workspace-source';

function initialSource(): boolean {
  if (window.location.hash) return window.location.hash === '#demo';
  return readSampleView() !== undefined;
}

function App() {
  useLocale();
  const [prototype, setPrototype] = useState(false);
  const [authoring, setAuthoring] = useState<'tree' | 'drawing' | undefined>(() =>
    new URLSearchParams(location.search).get('renderer') === 'drawing' ? 'drawing' : undefined);
  const [authoringTurn, setAuthoringTurn] = useState<WorkbenchAuthoringTurn>();
  const [legacySample, setLegacySample] = useState(initialSource);
  const demoLinkHandled = useRef(false);
  const [local, setLocal] = useState(false);
  const [folder, setFolder] = useState<LocalFolderCatalog | null>(null);
  const [choosing, setChoosing] = useState(false);
  const [pickerError, setPickerError] = useState<LocalImportMessage>();
  const [openingRevision, setOpeningRevision] = useState(false);
  const [modelChatView, setModelChatView] = useState<ModelChatView>();
  const [modelSelection, setModelSelection] = useState<ModelChartSelection>();
  const localWorkspace = useLocalWorkspace();
  const revisionAccess = usePublishedRevisionAccess(localWorkspace);
  const snapshot = localWorkspace?.snapshot;
  const modelWorkspaceId = snapshot?.workspace.id ?? nativeTextWorkspaceId;
  const source = snapshot?.source;
  const supportsSourcePreference = source !== undefined;
  const sample = localWorkspace ? Boolean(snapshot && (source?.kind === 'sample' || source == null && legacySample)) : legacySample;
  const { restored, retry } = useRestoredWorkspaceSource(localWorkspace, revisionAccess);
  const target = snapshot?.chart_evidence;
  const targetRevoked = Boolean(target && 'revision_id' in target && revisionAccess.revoked.has(target.revision_id));
  const displaySnapshot = useMemo(() => snapshot && targetRevoked
    ? { ...snapshot, chart_evidence: { unavailable: true as const } } : snapshot, [snapshot, targetRevoked]);
  const workspace = localWorkspace && { ...localWorkspace, snapshot: displaySnapshot };
  const currentModelSelection = !local && !targetRevoked && modelSelection?.selectionRevision === snapshot?.selection_revision
    ? modelSelection : undefined;
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
  const chooseFolder = useCallback(async () => {
    const picked = await chooseLocalCsvFolder();
    if (picked) {
      setFolder(picked);
      setLocal(true);
    }
    return picked;
  }, []);
  const chooseFromWorkspace = async () => {
    setChoosing(true);
    setPickerError(undefined);
    try { await chooseFolder(); }
    catch (error) {
      setPickerError(localImportErrorMessage(error) ?? message('The local CSV folder could not be opened.'));
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
    choose: () => { void chooseFromWorkspace(); },
    openRevision: () => setOpeningRevision(true),
    resume: folder ? () => setLocal(true) : undefined,
    choosing,
    error: pickerError === undefined ? undefined : renderLocalImportMessage(pickerError),
  } : undefined;

  function closeWorkbench() {
    setAuthoringTurn(undefined);
    setAuthoring(undefined);
  }
  function askWorkbench(turn: WorkbenchAuthoringTurn) {
    setModelSelection(undefined);
    setAuthoringTurn(turn);
    setModelChatView('question');
  }

  return <>
    {prototype && <WorkbenchPrototype close={() => setPrototype(false)} />}
    {authoring === 'drawing' && <DrawingWorkbench hasWorkspace={Boolean(modelWorkspaceId)} close={closeWorkbench} ask={askWorkbench} />}
    {authoring === 'tree' && <WorkbenchAuthoring close={closeWorkbench} ask={askWorkbench}
      openDrawing={() => { setAuthoringTurn(undefined); setAuthoring('drawing'); }} />}
    {!local && <ChartWorkspace key={sample ? 'sample' : 'unconfigured'} sample={sample}
      leaveSample={leaveSample} localActions={localActions} workspace={workspace} revisionAccess={revisionAccess}
      modelDialogOpen={Boolean(modelChatView)} openModelChat={selection => {
        setModelSelection(selection);
        setModelChatView('question');
      }}
      openModelSettings={() => {
        setModelSelection(undefined);
        setModelChatView('settings');
      }}
      openPublished={workspace ? () => setOpeningRevision(true) : undefined}
      restoredSource={restored} restoredSampleInstrument={source?.kind === 'sample' ? source.instrument_id ?? undefined : undefined}
      retryRestoredSource={retry} onOpenSample={openSample} openPrototype={() => setPrototype(true)}
      openAuthoring={() => setAuthoring('tree')}
      chooseSampleInstrument={workspace && supportsSourcePreference ? async id => {
        if (source?.kind !== 'sample' || source.instrument_id !== id) {
          await workspace.chooseSource({ kind: 'sample', instrument_id: id });
        }
      } : undefined} />}
    {folder && <div hidden={!local}>
      <main className="workstation local-workstation">
        <header className="topbar local-source-bar">
          <div className="brand" aria-label="ScopeHub"><span className="brand-mark" aria-hidden="true">S</span><strong>ScopeHub</strong></div>
          <span className="local-source-label"><i aria-hidden="true" />{t('Local CSV')}</span>
          <div className="local-source-controls">
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
      authoring={authoringTurn}
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
