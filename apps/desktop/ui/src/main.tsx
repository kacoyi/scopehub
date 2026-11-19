import { useCallback, useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ChartWorkspace } from './chart-workspace';
import { LocalImportEditor, type LocalFolderCatalog } from './local-import-editor';
import { cancelLocalPreview, chooseLocalCsvFolder, hasLocalCsvPicker, previewLocalImport } from './local-import-transport';
import { LocalPreviewChart } from './local-preview-chart';
import { OpenLocalRevisionDialog } from './local-revision-dialog';
import { usePublishedRevisionAccess } from './published-revision-access';
import { readSampleView, saveSampleView } from './sample-view';
import { useLocalWorkspace } from './use-local-workspace';
import './style.css';

function initialSource(): boolean {
  if (window.location.hash) return window.location.hash === '#demo';
  return readSampleView() !== undefined;
}

function App() {
  const [sample, setSample] = useState(initialSource);
  const [local, setLocal] = useState(false);
  const [folder, setFolder] = useState<LocalFolderCatalog | null>(null);
  const [choosing, setChoosing] = useState(false);
  const [pickerError, setPickerError] = useState<string>();
  const [openingRevision, setOpeningRevision] = useState(false);
  const localWorkspace = useLocalWorkspace();
  const revisionAccess = usePublishedRevisionAccess(localWorkspace);
  const snapshot = localWorkspace?.snapshot;
  const target = snapshot?.chart_evidence;
  const targetRevoked = Boolean(target && 'revision_id' in target && revisionAccess.revoked.has(target.revision_id));
  const displaySnapshot = useMemo(() => snapshot && targetRevoked
    ? { ...snapshot, chart_evidence: { unavailable: true as const } } : snapshot, [snapshot, targetRevoked]);
  const workspace = localWorkspace && { ...localWorkspace, snapshot: displaySnapshot };
  useEffect(() => {
    const navigate = () => {
      const chosen = window.location.hash === '#demo';
      if (!chosen) saveSampleView(null);
      setSample(chosen);
      if (chosen) setLocal(false);
    };
    window.addEventListener('hashchange', navigate);
    return () => window.removeEventListener('hashchange', navigate);
  }, []);
  const leaveSample = () => {
    saveSampleView(null);
    if (window.location.hash) window.location.hash = '';
    setSample(false);
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
      const message = error && typeof error === 'object' && 'message' in error && typeof error.message === 'string'
        ? error.message : 'The local CSV folder could not be opened.';
      setPickerError(message);
    } finally { setChoosing(false); }
  };
  const openSample = () => {
    setLocal(false);
    setSample(true);
    window.location.hash = 'demo';
  };
  const localActions = hasLocalCsvPicker() ? {
    choose: () => { void chooseFromWorkspace(); },
    openRevision: () => setOpeningRevision(true),
    resume: folder ? () => setLocal(true) : undefined,
    choosing,
    error: pickerError,
  } : undefined;

  return <>
    {!local && <ChartWorkspace key={sample ? 'sample' : 'unconfigured'} sample={sample}
      leaveSample={leaveSample} localActions={localActions} workspace={workspace}
      openPublished={workspace ? () => setOpeningRevision(true) : undefined} />}
    {folder && <div hidden={!local}>
      <main className="workstation local-workstation">
        <header className="topbar local-source-bar">
          <div className="brand" aria-label="ScopeHub"><span className="brand-mark" aria-hidden="true">S</span><strong>ScopeHub</strong></div>
          <span className="local-source-label"><i aria-hidden="true" />Local CSV</span>
          <div className="local-source-controls">
            <button type="button" className="secondary-button" aria-haspopup="dialog" onClick={() => setOpeningRevision(true)}>Open published CSV</button>
            <button type="button" className="secondary-button" onClick={openSample}>Open offline sample</button>
          </div>
        </header>
        <LocalImportEditor key={folder.folder_id} initialCatalog={folder} chooseFolder={chooseFolder}
          previewImport={previewLocalImport} cancelPreview={cancelLocalPreview} revisionAccess={revisionAccess}
          renderChart={result => <LocalPreviewChart result={result} />} workspace={workspace} />
      </main>
    </div>}
    {openingRevision && <OpenLocalRevisionDialog onClose={() => setOpeningRevision(false)} revisionAccess={revisionAccess}
      workspace={workspace} nativeLocalImport={hasLocalCsvPicker()} />}
  </>;
}

const root = document.getElementById('root');
if (!root) throw new Error('Missing application root');
createRoot(root).render(<App />);
