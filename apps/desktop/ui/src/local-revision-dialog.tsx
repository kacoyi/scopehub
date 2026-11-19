import { useCallback, useEffect, useId, useRef, useState } from 'react';
import type { ListLocalImportRevisionsOutput } from '../../../../contracts/generated/protocol';
import { listLocalImportRevisions, readLocalImportRevision, revokeLocalImportRevision } from './local-import-transport';
import { FrozenLocalChart } from './local-preview-chart';
import { openAuthenticatedFrozenChart, UnavailableSharedSelection, type FrozenChartResult } from './frozen-evidence';
import { displayAccessDenied, useWatchPublishedRevision, type PublishedRevisionAccess } from './published-revision-access';
import type { LocalWorkspace } from './use-local-workspace';

export function RevisionAccessActions({ disabled, onRevoke }: { disabled: boolean; onRevoke: () => void }) {
  const [confirm, setConfirm] = useState(false);
  return confirm ? <fieldset className="local-import-card local-access-confirmation" disabled={disabled}>
    <legend>Remove access to this revision?</legend>
    <p>The original CSV copy stays in this profile.</p>
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
  | { status: 'ready'; result: ListLocalImportRevisionsOutput };

const PAGE_SIZE = 8;

export function OpenLocalRevisionDialog({ onClose, revisionAccess, workspace, nativeLocalImport }: {
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
  const [cursors, setCursors] = useState<Array<string | null>>([null]);
  const [draft, setDraft] = useState('');
  const [frozen, setFrozen] = useState<FrozenChartResult>();
  const [openingId, setOpeningId] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string>();
  const [notice, setNotice] = useState<string>();
  const revisionId = draft.trim().toLowerCase();
  const validId = /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i.test(revisionId);
  const watchedId = openingId ?? frozen?.revision.revision_id;
  const lostAccess = watchedId !== undefined && revisionAccess.revoked.has(watchedId);
  const visibleFrozen = lostAccess ? undefined : frozen;
  const visibleRevisions = library.status === 'ready'
    ? library.result.revisions.filter(entry => !revisionAccess.revoked.has(entry.revision_id)) : [];
  useWatchPublishedRevision(lostAccess ? undefined : watchedId, revisionAccess.verify, workspace);

  useEffect(() => {
    if (!lostAccess) return;
    setFrozen(undefined);
    setFailure(unavailable);
  }, [lostAccess]);
  const listLoading = nativeLocalImport && library.status === 'loading';
  const working = busy || listLoading;

  const loadPage = useCallback(async (pageCursors: Array<string | null>) => {
    if (active.current) return;
    active.current = true;
    setLibrary({ status: 'loading' });
    setCursors(pageCursors);
    try {
      const result = await listLocalImportRevisions({
        version: 1, after_revision_id: pageCursors.at(-1) ?? null, limit: PAGE_SIZE, include_bar_summary: true,
      }, crypto.randomUUID());
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
    if (nativeLocalImport) void loadPage([null]);
    return () => { node?.close(); };
  }, [loadPage, nativeLocalImport]);

  useEffect(() => {
    if (visibleFrozen) chart.current?.focus();
  }, [visibleFrozen]);

  function browse(pageCursors: Array<string | null>) {
    if (active.current) return;
    libraryTitle.current?.focus();
    setFrozen(undefined);
    setFailure(undefined);
    setNotice(undefined);
    void loadPage(pageCursors);
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
      setOpeningId(id);
      const denied = { code: workspace ? 'SNAPSHOT_UNAVAILABLE' : 'PERMISSION_DENIED' };
      try {
        if (!revisionAccess.canDisplay(id)) throw denied;
        const reply = workspace ? await openAuthenticatedFrozenChart(id, crypto.randomUUID())
          : await readLocalImportRevision({ version: 1, revision_id: id }, crypto.randomUUID());
        if (!revisionAccess.canDisplay(id)) throw denied;
        if (reply.revision.revision_id !== id || reply.bars.length !== reply.revision.bar_count) {
          throw new Error('The frozen chart reply could not be verified.');
        }
        setFrozen(reply);
      } catch (error) {
        if (displayAccessDenied(error)) {
          if (fromLibrary) libraryTitle.current?.focus();
          removeFromLibrary(id);
          revisionAccess.revoke(id);
          workspace?.reload();
        }
        throw error;
      } finally {
        setOpeningId(undefined);
      }
    }, 'Unable to open the frozen chart.');
  }

  async function revoke() {
    if (!visibleFrozen) return;
    await request(async () => {
      const revision_id = visibleFrozen.revision.revision_id;
      const reply = await revokeLocalImportRevision({ version: 1, revision_id }, crypto.randomUUID());
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
        <h3 id={chartHeading}>{visibleFrozen.revision.relative_path}</h3>
        <FrozenLocalChart key={visibleFrozen.revision.revision_id} result={visibleFrozen} workspace={workspace} access={revisionAccess} />
        {nativeLocalImport && <div className="local-import-card">
          <RevisionAccessActions key={visibleFrozen.revision.revision_id} disabled={working} onRevoke={() => void revoke()} />
        </div>}
      </section>}
      {nativeLocalImport && <section className="local-import-card local-revision-library" aria-labelledby={libraryHeading}>
        <div className="local-revision-heading">
          <h3 ref={libraryTitle} id={libraryHeading} tabIndex={-1}>Published revisions</h3>
          <button type="button" className="secondary-button" disabled={working} onClick={() => browse([null])}>Refresh revisions</button>
        </div>
        {listLoading && <p role="status">Loading published revisions…</p>}
        {library.status === 'unconfigured' && <p role="status">Published revisions are unavailable because local profile storage is unconfigured.</p>}
        {library.status === 'error' && <p className="local-import-error" role="alert">Unable to read published revisions. Try refreshing the list.</p>}
        {library.status === 'ready' && <>
          {visibleRevisions.length ? <ul className="local-revision-list">
            {visibleRevisions.map(entry => <li key={entry.revision_id}>
              <button type="button" className="secondary-button" disabled={working} onClick={() => void open(entry.revision_id, true)}>
                <strong>Open {entry.relative_path}</strong>
                <span>{entry.symbol}{entry.bar_summary && ` · ${entry.bar_summary.venue}`} · {entry.timeframe} · {entry.bar_count} {entry.bar_count === 1 ? 'bar' : 'bars'} · {entry.source_record_count} source {entry.source_record_count === 1 ? 'row' : 'rows'}</span>
                {entry.bar_summary && <span className="local-revision-summary">
                  <b>Final close {entry.bar_summary.final_close}</b> · Trading day {entry.bar_summary.final_trading_day}
                  {entry.bar_summary.first_trading_day !== entry.bar_summary.final_trading_day &&
                    ` · First bar trading day ${entry.bar_summary.first_trading_day}`}
                </span>}
                <span>CSV SHA-256 <code>{entry.source_sha256.slice(0, 12)}</code> · Revision <code>{entry.revision_id.slice(0, 8)}</code></span>
              </button>
            </li>)}
          </ul> : <p role="status">{cursors.length === 1 && !library.result.next_after_revision_id
            ? 'No published CSV revisions are available in this profile.' : 'No published revisions on this page.'}</p>}
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
          <button type="submit" className="secondary-button" disabled={!validId}>Open frozen chart</button>
        </fieldset>
      </form>
    </div>
  </dialog>;
}
