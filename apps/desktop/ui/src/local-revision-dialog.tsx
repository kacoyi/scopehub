import { useCallback, useEffect, useId, useRef, useState } from 'react';
import type { ListLocalFolderRevisionsOutput, ListLocalImportRevisionsOutput, ReadLocalFolderRevisionOutput } from '../../../../contracts/generated/protocol';
import {
  listLocalFolderRevisions, listLocalImportRevisions, readLocalFolderRevision, readLocalImportRevision,
  revokeLocalFolderRevision, revokeLocalImportRevision,
} from './local-import-transport';
import { FrozenFolderChart, FrozenLocalChart } from './local-preview-chart';
import { openAuthenticatedFrozenChart, UnavailableSharedSelection, type FrozenChartResult } from './frozen-evidence';
import { displayAccessDenied, useWatchPublishedRevision, type PublishedRevisionAccess } from './published-revision-access';
import type { LocalWorkspace } from './use-local-workspace';

export function RevisionAccessActions({ disabled, folder = false, onRevoke }: { disabled: boolean; folder?: boolean; onRevoke: () => void }) {
  const [confirm, setConfirm] = useState(false);
  return confirm ? <fieldset className="local-import-card local-access-confirmation" disabled={disabled}>
    <legend>Remove access to this revision?</legend>
    <p>{folder ? 'The original sources stay in this profile.' : 'The original CSV copy stays in this profile.'}</p>
    <div className="local-publication-actions">
      <button type="button" className="secondary-button" onClick={onRevoke}>Remove access</button>
      <button type="button" className="secondary-button" onClick={() => setConfirm(false)}>Keep access</button>
    </div>
  </fieldset> : <button type="button" className="secondary-button" disabled={disabled}
    onClick={() => setConfirm(true)}>Remove frozen chart access</button>;
}

function errorCode(error: unknown) {
  return error && typeof error === 'object' && 'code' in error ? error.code : undefined;
}

function failureMessage(error: unknown, fallback: string) {
  if (!error || typeof error !== 'object') return fallback;
  if (errorCode(error) === 'SNAPSHOT_UNAVAILABLE') return 'This frozen chart is unavailable in the current profile.';
  if (errorCode(error) === 'PERMISSION_DENIED') return 'This profile does not have permission to display this revision.';
  return 'message' in error && typeof error.message === 'string' ? error.message : fallback;
}

const unavailable = 'This frozen chart is unavailable in the current profile.';

type LibraryPage =
  | { status: 'loading' | 'unconfigured' | 'error' }
  | { status: 'ready'; result: {
    revisions: Array<ListLocalImportRevisionsOutput['revisions'][number] | ListLocalFolderRevisionsOutput['revisions'][number]>;
    next_after_revision_id: string | null;
  } };

type RevisionKind = 'csv' | 'folder';
type OpenedRevision =
  | { kind: 'csv'; result: FrozenChartResult }
  | { kind: 'folder'; result: ReadLocalFolderRevisionOutput };

const PAGE_SIZE = 8;

export function OpenLocalRevisionDialog({ onClose, revisionAccess, workspace, nativeLocalImport, useWorkspaceSource }: {
  useWorkspaceSource?: ((id: string) => Promise<void>) | undefined;
  onClose: () => void; revisionAccess: PublishedRevisionAccess; workspace: LocalWorkspace | undefined; nativeLocalImport: boolean;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const libraryTitle = useRef<HTMLHeadingElement>(null);
  const chart = useRef<HTMLElement>(null);
  const active = useRef(false);
  const heading = useId();
  const libraryHeading = useId();
  const chartHeading = useId();
  const [library, setLibrary] = useState<LibraryPage>({ status: 'loading' });
  const [kind, setKind] = useState<RevisionKind>('csv');
  const [cursors, setCursors] = useState<Array<string | null>>([null]);
  const [draft, setDraft] = useState('');
  const [frozen, setFrozen] = useState<OpenedRevision>();
  const [opening, setOpening] = useState<{ id: string; kind: RevisionKind }>();
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string>();
  const [notice, setNotice] = useState<string>();
  const revisionId = draft.trim().toLowerCase();
  const validId = /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i.test(revisionId);
  const watchedId = opening?.id ?? frozen?.result.revision.revision_id;
  const watchedKind = opening?.kind ?? frozen?.kind;
  const lostAccess = watchedId !== undefined && revisionAccess.revoked.has(watchedId);
  const visibleFrozen = lostAccess ? undefined : frozen;
  const visibleRevisions = library.status === 'ready'
    ? library.result.revisions.filter(entry => !revisionAccess.revoked.has(entry.revision_id)) : [];
  useWatchPublishedRevision(lostAccess ? undefined : watchedId,
    watchedKind === 'folder' ? revisionAccess.verifyFolder : revisionAccess.verify, watchedKind === 'folder' ? undefined : workspace);

  useEffect(() => {
    if (!lostAccess) return;
    setFrozen(undefined);
    setFailure(unavailable);
  }, [lostAccess]);
  const listLoading = nativeLocalImport && library.status === 'loading';
  const working = busy || listLoading;

  const loadPage = useCallback(async (pageCursors: Array<string | null>, revisionKind: RevisionKind) => {
    if (active.current) return;
    active.current = true;
    setLibrary({ status: 'loading' });
    setKind(revisionKind);
    setCursors(pageCursors);
    try {
      const input = { version: 1 as const, after_revision_id: pageCursors.at(-1) ?? null, limit: PAGE_SIZE };
      const result = revisionKind === 'folder'
        ? await listLocalFolderRevisions(input, crypto.randomUUID())
        : await listLocalImportRevisions({ ...input, include_bar_summary: true }, crypto.randomUUID());
      setLibrary({ status: 'ready', result });
    } catch (error) {
      setLibrary({ status: errorCode(error) === 'PROVIDER_NOT_CONFIGURED' ? 'unconfigured' : 'error' });
    } finally {
      active.current = false;
    }
  }, []);

  useEffect(() => {
    const node = dialog.current;
    node?.showModal();
    if (nativeLocalImport) void loadPage([null], 'csv');
    return () => { node?.close(); };
  }, [loadPage, nativeLocalImport]);

  useEffect(() => {
    if (visibleFrozen) chart.current?.focus();
  }, [visibleFrozen]);

  function browse(pageCursors: Array<string | null>, revisionKind = kind) {
    if (active.current) return;
    libraryTitle.current?.focus();
    setFrozen(undefined);
    setFailure(undefined);
    setNotice(undefined);
    void loadPage(pageCursors, revisionKind);
  }

  function removeFromLibrary(id: string) {
    setLibrary(current => current.status === 'ready' ? {
      status: 'ready', result: {
        ...current.result, revisions: current.result.revisions.filter(entry => entry.revision_id !== id),
      },
    } : current);
  }

  async function request(work: () => Promise<void>, fallback: string) {
    if (active.current) return;
    active.current = true;
    setBusy(true);
    setFailure(undefined);
    setNotice(undefined);
    try {
      await work();
    } catch (error) {
      setFailure(failureMessage(error, fallback));
    } finally {
      active.current = false;
      setBusy(false);
    }
  }

  async function open(id: string, fromLibrary = false) {
    if (active.current) return;
    setFrozen(undefined);
    await request(async () => {
      setOpening({ id, kind });
      const denied = { code: workspace && kind === 'csv' ? 'SNAPSHOT_UNAVAILABLE' : 'PERMISSION_DENIED' };
      try {
        if (!revisionAccess.canDisplay(id)) throw denied;
        const opened: OpenedRevision = kind === 'folder'
          ? { kind, result: await readLocalFolderRevision({ version: 1, revision_id: id }, crypto.randomUUID()) }
          : { kind, result: workspace ? await openAuthenticatedFrozenChart(id, crypto.randomUUID())
            : await readLocalImportRevision({ version: 1, revision_id: id }, crypto.randomUUID()) };
        if (!revisionAccess.canDisplay(id)) throw denied;
        const { result } = opened;
        if (result.revision.revision_id !== id || result.bars.length !== result.revision.bar_count
          || (opened.kind === 'folder' && opened.result.files.length !== opened.result.revision.source_count)) {
          throw new Error('The frozen chart reply could not be verified.');
        }
        setFrozen(opened);
      } catch (error) {
        if (displayAccessDenied(error)) {
          if (fromLibrary) libraryTitle.current?.focus();
          removeFromLibrary(id);
          revisionAccess.revoke(id);
          workspace?.reload();
        }
        throw error;
      } finally {
        setOpening(undefined);
      }
    }, 'Unable to open the frozen chart.');
  }

  async function revoke() {
    if (!visibleFrozen) return;
    await request(async () => {
      const revision_id = visibleFrozen.result.revision.revision_id;
      const input = { version: 1 as const, revision_id };
      const reply = visibleFrozen.kind === 'folder'
        ? await revokeLocalFolderRevision(input, crypto.randomUUID())
        : await revokeLocalImportRevision(input, crypto.randomUUID());
      if (reply.revision_id !== revision_id) throw new Error('The access removal reply could not be verified.');
      libraryTitle.current?.focus();
      setFrozen(undefined);
      removeFromLibrary(revision_id);
      setNotice('Access removed for this published revision.');
      revisionAccess.revoke(revision_id);
      workspace?.reload();
    }, 'Unable to remove access to this revision.');
  }

  return <dialog ref={dialog} className="symbol-dialog local-revision-dialog" aria-labelledby={heading} onCancel={onClose}>
    <div className="dialog-heading local-revision-heading">
      <h2 id={heading}>Open published CSV</h2>
      <button type="button" className="secondary-button" onClick={onClose}>Close</button>
    </div>
    <div className="local-revision-body">
      {busy && <p role="status">Working with the frozen revision…</p>}
      {failure && <p className="local-import-error" role="alert">{failure}</p>}
      {notice && <p role="status">{notice}</p>}
      {!visibleFrozen && <UnavailableSharedSelection workspace={workspace} />}
      {visibleFrozen && <section ref={chart} className="local-revision-chart" tabIndex={-1} aria-labelledby={chartHeading}>
        <h3 id={chartHeading}>{visibleFrozen.kind === 'folder' ? visibleFrozen.result.revision.origin_path : visibleFrozen.result.revision.relative_path}</h3>
        {visibleFrozen.kind === 'folder'
          ? <FrozenFolderChart key={visibleFrozen.result.revision.revision_id} result={visibleFrozen.result} />
          : <FrozenLocalChart key={visibleFrozen.result.revision.revision_id} result={visibleFrozen.result} workspace={workspace} access={revisionAccess} />}
        {visibleFrozen.kind === 'csv' && useWorkspaceSource && <button type="button" className="secondary-button"
          disabled={working || Boolean(workspace?.pending)} onClick={() => void request(
            () => useWorkspaceSource(visibleFrozen.result.revision.revision_id), 'Unable to save the workspace source.',
          )}>Use as workspace source</button>}
        {nativeLocalImport && <div className="local-import-card">
          <RevisionAccessActions key={visibleFrozen.result.revision.revision_id} folder={visibleFrozen.kind === 'folder'}
            disabled={working} onRevoke={() => void revoke()} />
        </div>}
      </section>}
      {nativeLocalImport && <section className="local-import-card local-revision-library" aria-labelledby={libraryHeading}>
        <div className="local-revision-heading">
          <h3 ref={libraryTitle} id={libraryHeading} tabIndex={-1}>Published revisions</h3>
          <button type="button" className="secondary-button" disabled={working} onClick={() => browse([null])}>Refresh revisions</button>
        </div>
        <div className="local-publication-actions" role="group" aria-label="Revision kind">
          <button type="button" className="secondary-button" aria-pressed={kind === 'csv'} disabled={working}
            onClick={() => browse([null], 'csv')}>Single-file revisions</button>
          <button type="button" className="secondary-button" aria-pressed={kind === 'folder'} disabled={working}
            onClick={() => browse([null], 'folder')}>Folder revisions</button>
        </div>
        {listLoading && <p role="status">Loading published revisions…</p>}
        {library.status === 'unconfigured' && <p role="status">Published revisions are unavailable because local profile storage is unconfigured.</p>}
        {library.status === 'error' && <p className="local-import-error" role="alert">Unable to read published revisions. Try refreshing the list.</p>}
        {library.status === 'ready' && <>
          {visibleRevisions.length ? <ul className="local-revision-list">
            {visibleRevisions.map(entry => {
              const folder = 'origin_path' in entry;
              const venue = folder ? entry.series.venue : entry.bar_summary?.venue;
              const timeframe = folder ? entry.series.timeframe : entry.timeframe;
              return <li key={entry.revision_id}>
                <button type="button" className="secondary-button" disabled={working} onClick={() => void open(entry.revision_id, true)}>
                  <strong>{folder ? `Open folder revision from ${entry.origin_path}` : `Open ${entry.relative_path}`}</strong>
                  <span>{entry.symbol}{venue && ` · ${venue}`} · {timeframe} · {entry.bar_count} {entry.bar_count === 1 ? 'bar' : 'bars'} · {
                    folder ? `${entry.source_count} ${entry.source_count === 1 ? 'source' : 'sources'}`
                      : `${entry.source_record_count} source ${entry.source_record_count === 1 ? 'row' : 'rows'}`
                  }</span>
                  {entry.bar_summary && <span className="local-revision-summary">
                    <b>Final close {entry.bar_summary.final_close}</b> · Trading day {entry.bar_summary.final_trading_day}
                    {entry.bar_summary.first_trading_day !== entry.bar_summary.final_trading_day &&
                      ` · First bar trading day ${entry.bar_summary.first_trading_day}`}
                  </span>}
                  <span>{!folder && <>CSV SHA-256 <code>{entry.source_sha256.slice(0, 12)}</code> · </>}Revision <code>{entry.revision_id.slice(0, 8)}</code></span>
                </button>
              </li>;
            })}
          </ul> : <p role="status">{cursors.length === 1 && !library.result.next_after_revision_id
            ? kind === 'folder' ? 'No published folder revisions are available in this profile.' : 'No published CSV revisions are available in this profile.'
            : 'No published revisions on this page.'}</p>}
          {(cursors.length > 1 || library.result.next_after_revision_id) && <div className="local-publication-actions">
            <button type="button" className="secondary-button" disabled={working || cursors.length < 2} onClick={() => browse(cursors.slice(0, -1))}>Previous page</button>
            <button type="button" className="secondary-button" disabled={working || !library.result.next_after_revision_id}
              onClick={() => browse([...cursors, library.result.next_after_revision_id])}>Next page</button>
          </div>}
        </>}
      </section>}
      <form onSubmit={event => { event.preventDefault(); if (validId) void open(revisionId); }}>
        <fieldset className="local-import-card" disabled={working}>
          <div className="local-import-fields">
            <label className="local-import-wide">Revision ID
              <input value={draft} maxLength={36} autoComplete="off" spellCheck={false} onChange={event => {
                setDraft(event.currentTarget.value);
                setFrozen(undefined);
                setFailure(undefined);
                setNotice(undefined);
              }} />
            </label>
          </div>
          <button type="submit" className="secondary-button" disabled={!validId}>{kind === 'folder' ? 'Open frozen folder revision' : 'Open frozen chart'}</button>
        </fieldset>
      </form>
    </div>
  </dialog>;
}
