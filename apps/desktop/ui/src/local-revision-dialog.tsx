import { waitForReply, withReplyDeadline } from './reply-deadline';
import { useEffect, useEffectEvent, useId, useRef, useState } from 'react';
import type { ListLocalFolderRevisionsOutput, ListLocalImportRevisionsOutput, ReadLocalFolderRevisionOutput } from '../../../../contracts/generated/protocol';
import { formatNumber, message, plural, t } from './i18n';
import {
  listLocalFolderRevisions, listLocalImportRevisions, readLocalFolderRevision, readLocalImportRevision,
  revokeLocalFolderRevision, revokeLocalImportRevision, localImportErrorMessage, renderLocalImportMessage,
} from './local-import-transport';
import { formatSourceBarValue, FrozenFolderChart, FrozenLocalChart } from './local-preview-chart';
import { openAuthenticatedFrozenChart, UnavailableSharedSelection, type FrozenChartResult } from './frozen-evidence';
import { displayAccessDenied, useWatchPublishedRevision, type PublishedRevisionAccess } from './published-revision-access';
import { type LocalWorkspace, WorkspaceSourceError } from './use-local-workspace';

export function RevisionAccessActions({ disabled, folder = false, onRevoke }: { disabled: boolean; folder?: boolean; onRevoke: () => void }) {
  const [confirm, setConfirm] = useState(false);
  return confirm ? <fieldset className="local-import-card local-access-confirmation" disabled={disabled}>
    <legend>{t('Remove access to this revision?')}</legend>
    <p>{folder ? t('The original sources stay in this profile.') : t('The original CSV copy stays in this profile.')}</p>
    <div className="local-publication-actions">
      <button type="button" className="secondary-button" onClick={onRevoke}>{t('Remove access')}</button>
      <button type="button" className="secondary-button" onClick={() => setConfirm(false)}>{t('Keep access')}</button>
    </div>
  </fieldset> : <button type="button" className="secondary-button" disabled={disabled}
    onClick={() => setConfirm(true)}>{t('Remove frozen chart access')}</button>;
}

function errorCode(error: unknown) {
  return error && typeof error === 'object' && 'code' in error ? error.code : undefined;
}

function failureMessage(error: unknown, fallback: string) {
  const code = errorCode(error);
  if (code === 'SNAPSHOT_UNAVAILABLE') return message('This frozen chart is unavailable in the current profile.');
  if (code === 'PERMISSION_DENIED') return message('This profile does not have permission to display this revision.');
  if (code === 'STALE_CONTEXT') return message('The workspace changed. Reload its current state.');
  if (error instanceof WorkspaceSourceError) return error.localizedMessage;
  if (error instanceof Error && error.message === 'The frozen chart reply could not be verified.') {
    return message('The frozen chart reply could not be verified.');
  }
  if (error instanceof Error && error.message === 'The access removal reply could not be verified.') {
    return message('The access removal reply could not be verified.');
  }
  return localImportErrorMessage(error) ?? message(fallback);
}

type LibraryPage =
  | { status: 'unconfigured' | 'error' }
  | { status: 'ready'; result: {
    revisions: Array<ListLocalImportRevisionsOutput['revisions'][number] | ListLocalFolderRevisionsOutput['revisions'][number]>;
    next_after_revision_id: string | null;
  } };

type RevisionKind = 'csv' | 'folder';
type LibrarySelection = { kind: RevisionKind; cursors: Array<string | null> };
type OpenedRevision =
  | { kind: 'csv'; result: FrozenChartResult }
  | { kind: 'folder'; result: ReadLocalFolderRevisionOutput };

const PAGE_SIZE = 8;

export function OpenLocalRevisionDialog({ onClose, revisionAccess, workspace, nativeLocalImport, useWorkspaceSource, onWorkspaceSourceSelected }: {
  useWorkspaceSource?: ((id: string, signal: AbortSignal) => Promise<void>) | undefined;
  onWorkspaceSourceSelected: () => void;
  onClose: () => void; revisionAccess: PublishedRevisionAccess; workspace: LocalWorkspace | undefined; nativeLocalImport: boolean;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const libraryTitle = useRef<HTMLHeadingElement>(null);
  const chart = useRef<HTMLElement>(null);
  const lifetime = useRef<AbortController | null>(null);
  const active = useRef(false);
  const pendingList = useRef(Promise.resolve());
  const heading = useId();
  const libraryHeading = useId();
  const chartHeading = useId();
  const [selection, selectLibrary] = useState<LibrarySelection>({ kind: 'csv', cursors: [null] });
  const [listing, setListing] = useState<{ selection: LibrarySelection; page: LibraryPage }>();
  const { kind, cursors } = selection;
  const library = listing?.selection === selection ? listing.page : { status: 'loading' } as const;
  const [draft, setDraft] = useState('');
  const [frozen, setFrozen] = useState<OpenedRevision>();
  const [opening, setOpening] = useState<{ id: string; kind: RevisionKind }>();
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<ReturnType<typeof failureMessage>>();
  const [notice, setNotice] = useState<ReturnType<typeof message>>();
  const revisionId = draft.trim().toLowerCase();
  const validId = /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i.test(revisionId);
  const watchedId = opening?.id ?? frozen?.result.revision.revision_id;
  const watchedKind = opening?.kind ?? frozen?.kind;
  const lostAccess = watchedId !== undefined && revisionAccess.revoked.has(watchedId);
  const visibleFrozen = frozen && revisionAccess.canDisplay(frozen.result.revision.revision_id) ? frozen : undefined;
  const visibleRevisions = library.status === 'ready'
    ? library.result.revisions.filter(entry => revisionAccess.canDisplay(entry.revision_id)) : [];
  useWatchPublishedRevision(lostAccess ? undefined : watchedId,
    watchedKind === 'folder' ? revisionAccess.verifyFolder : revisionAccess.verify,
    watchedKind === 'folder' ? undefined : workspace, 'checked-by-reader');

  useEffect(() => {
    if (!lostAccess) return;
    setFrozen(undefined);
    setFailure(message('This frozen chart is unavailable in the current profile.'));
  }, [lostAccess]);
  const listLoading = nativeLocalImport && library.status === 'loading';
  const workspacePending = Boolean(workspace?.pending);
  const working = busy || listLoading || workspacePending;

  useEffect(() => {
    // Workspace reads and writes share native admission with revision operations.
    if (!listLoading || workspacePending) return;
    let superseded = false;
    // The desktop admits one workspace operation at a time. Let the user
    // pick a library while the preceding reply is still pending.
    pendingList.current = pendingList.current.then(async () => {
      if (superseded) return;
      const input = { version: 1 as const, after_revision_id: selection.cursors.at(-1) ?? null, limit: PAGE_SIZE };
      let page: LibraryPage;
      try {
        const result = await withReplyDeadline(15_000, async () => selection.kind === 'folder'
          ? listLocalFolderRevisions(input, crypto.randomUUID())
          : listLocalImportRevisions({ ...input, include_bar_summary: true }, crypto.randomUUID()), lifetime.current?.signal);
        page = { status: 'ready', result };
      } catch (error) {
        page = { status: errorCode(error) === 'PROVIDER_NOT_CONFIGURED' ? 'unconfigured' : 'error' };
      }
      if (!superseded) setListing({ selection, page });
    });
    return () => { superseded = true; };
  }, [listLoading, selection, workspacePending]);

  const withheldFolderIds = useEffectEvent((revisions: readonly { revision_id: string }[]) =>
    revisions.map(entry => entry.revision_id).filter(id => revisionAccess.unavailable.has(id) && !revisionAccess.revoked.has(id)));
  const { verifyFolder } = revisionAccess;
  useEffect(() => {
    if (kind !== 'folder' || library.status !== 'ready') return;
    let superseded = false;
    const withheld = withheldFolderIds(library.result.revisions);
    // List membership is not a display grant. Recheck only temporary
    // denials on the current page, in order on the native admission lane.
    void (async () => {
      for (const id of withheld) {
        if (superseded) return;
        await verifyFolder(id);
      }
    })();
    return () => { superseded = true; };
  }, [kind, library, verifyFolder]);

  useEffect(() => {
    const node = dialog.current;
    const controller = new AbortController();
    lifetime.current = controller;
    node?.showModal();
    return () => { controller.abort(); node?.close(); };
  }, []);

  useEffect(() => {
    if (visibleFrozen) chart.current?.focus();
  }, [visibleFrozen]);

  function browse(pageCursors: Array<string | null>, revisionKind = kind) {
    if (active.current) return;
    libraryTitle.current?.focus();
    setFrozen(undefined);
    setFailure(undefined);
    setNotice(undefined);
    selectLibrary({ kind: revisionKind, cursors: pageCursors });
  }

  function removeFromLibrary(id: string) {
    setListing(current => current?.selection === selection && current.page.status === 'ready' ? {
      selection, page: { status: 'ready', result: {
        ...current.page.result, revisions: current.page.result.revisions.filter(entry => entry.revision_id !== id),
      } },
    } : current);
  }

  async function request(work: () => Promise<void>, fallback: string) {
    if (active.current || listLoading || workspacePending) return;
    const signal = lifetime.current?.signal;
    if (!signal || signal.aborted) return;
    active.current = true;
    setBusy(true);
    setFailure(undefined);
    setNotice(undefined);
    try {
      await work();
    } catch (error) {
      const nextFailure = failureMessage(error, fallback);
      if (!signal.aborted) setFailure(() => nextFailure);
    } finally {
      active.current = false;
      if (!signal.aborted) setBusy(false);
    }
  }

  async function open(id: string, fromLibrary = false) {
    const signal = lifetime.current?.signal;
    if (active.current || !signal || signal.aborted) return;
    setFrozen(undefined);
    await request(async () => {
      setOpening({ id, kind });
      const denied = { code: workspace && kind === 'csv' ? 'SNAPSHOT_UNAVAILABLE' : 'PERMISSION_DENIED' };
      const verify = kind === 'folder' ? revisionAccess.verifyFolder : revisionAccess.verify;
      let reading: (() => boolean) | undefined;
      try {
        if (!revisionAccess.canDisplay(id)) {
          await waitForReply(signal, () => verify(id) ?? Promise.resolve());
        }
        if (!revisionAccess.canDisplay(id)) throw denied;
        reading = revisionAccess.beginRead(id);
        const opened: OpenedRevision = kind === 'folder'
          ? { kind, result: await readLocalFolderRevision({ version: 1, revision_id: id }, crypto.randomUUID(), signal) }
          : { kind, result: workspace ? await openAuthenticatedFrozenChart(id, crypto.randomUUID(), signal)
            : await readLocalImportRevision({ version: 1, revision_id: id }, crypto.randomUUID(), signal) };
        if (signal.aborted) return;
        if (!reading()) throw denied;
        const { result } = opened;
        if (result.revision.revision_id !== id || result.bars.length !== result.revision.bar_count
          || (opened.kind === 'folder' && opened.result.files.length !== opened.result.revision.source_count)) {
          throw new Error('The frozen chart reply could not be verified.');
        }
        setFrozen(opened);
      } catch (error) {
        if (signal.aborted) return;
        if (displayAccessDenied(error)) {
          if (fromLibrary) libraryTitle.current?.focus();
          if (reading?.()) {
            revisionAccess.withhold(id);
            void verify(id);
            if (kind === 'csv') workspace?.refreshPublishedAccess(id);
          }
        }
        throw error;
      } finally {
        if (!signal.aborted) setOpening(undefined);
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
      setNotice(message('Access removed for this published revision.'));
      revisionAccess.revoke(revision_id);
      workspace?.reload();
    }, 'Unable to remove access to this revision.');
  }

  function close() {
    lifetime.current?.abort();
    onClose();
  }

  async function selectWorkspaceSource(work: (signal: AbortSignal) => Promise<boolean>) {
    const signal = lifetime.current?.signal;
    if (!signal || signal.aborted) return;
    await request(async () => {
      if (await work(signal) && !signal.aborted) onWorkspaceSourceSelected();
    }, 'Unable to save the workspace source.');
  }

  return <dialog ref={dialog} className="symbol-dialog local-revision-dialog" aria-labelledby={heading} onCancel={close}>
    <div className="dialog-heading local-revision-heading">
      <h2 id={heading}>{t('Open published CSV')}</h2>
      <button type="button" className="secondary-button" onClick={close}>{t('Close')}</button>
    </div>
    <div className="local-revision-body">
      {busy && <p role="status">{t('Working with the frozen revision…')}</p>}
      {(failure || workspace?.sourceRecovery && workspace.error) &&
        <p className="local-import-error" role="alert">{failure !== undefined ? renderLocalImportMessage(failure) : workspace?.error}</p>}
      {workspace?.error && <button type="button" className="secondary-button" disabled={working}
        onClick={() => {
          if (!workspace.sourceRecovery) {
            setFailure(undefined);
            workspace.reload();
            return;
          }
          void selectWorkspaceSource(async signal => {
            const ownsSource = workspace.ownsSourceRecovery(signal);
            await workspace.recoverSource();
            return ownsSource;
          });
        }}>
        {t('Reload workspace state')}
      </button>}
      {notice && <p role="status">{t(notice)}</p>}
      {!visibleFrozen && <UnavailableSharedSelection workspace={workspace} />}
      {visibleFrozen && <section ref={chart} className="local-revision-chart" tabIndex={-1} aria-labelledby={chartHeading}>
        <h3 id={chartHeading}>{visibleFrozen.kind === 'folder' ? visibleFrozen.result.revision.origin_path : visibleFrozen.result.revision.relative_path}</h3>
        {visibleFrozen.kind === 'folder'
          ? <FrozenFolderChart key={visibleFrozen.result.revision.revision_id} result={visibleFrozen.result} access={revisionAccess} />
          : <FrozenLocalChart key={visibleFrozen.result.revision.revision_id} result={visibleFrozen.result} workspace={workspace} access={revisionAccess} />}
        {visibleFrozen.kind === 'csv' && useWorkspaceSource && <button type="button" className="secondary-button"
          disabled={working || workspace?.sourceRecovery} onClick={() => void selectWorkspaceSource(
            async signal => {
              await useWorkspaceSource(visibleFrozen.result.revision.revision_id, signal);
              return true;
            },
          )}>{t('Use as workspace source')}</button>}
        {nativeLocalImport && <div className="local-import-card">
          <RevisionAccessActions key={visibleFrozen.result.revision.revision_id} folder={visibleFrozen.kind === 'folder'}
            disabled={working} onRevoke={() => void revoke()} />
        </div>}
      </section>}
      {nativeLocalImport && <section className="local-import-card local-revision-library" aria-labelledby={libraryHeading}>
        <div className="local-revision-heading">
          <h3 ref={libraryTitle} id={libraryHeading} tabIndex={-1}>{t('Published revisions')}</h3>
          <button type="button" className="secondary-button" disabled={working} onClick={() => browse([null])}>{t('Refresh revisions')}</button>
        </div>
        <div className="local-publication-actions" role="group" aria-label={t('Revision kind')}>
          <button type="button" className="secondary-button" aria-pressed={kind === 'csv'} disabled={busy || (listLoading && kind === 'csv')}
            onClick={() => browse([null], 'csv')}>{t('Single-file revisions')}</button>
          <button type="button" className="secondary-button" aria-pressed={kind === 'folder'} disabled={busy || (listLoading && kind === 'folder')}
            onClick={() => browse([null], 'folder')}>{t('Folder revisions')}</button>
        </div>
        {listLoading && <p role="status">{t('Loading published revisions…')}</p>}
        {library.status === 'unconfigured' && <p role="status">{t('Published revisions are unavailable because local profile storage is unconfigured.')}</p>}
        {library.status === 'error' && <p className="local-import-error" role="alert">{t('Unable to read published revisions. Try refreshing the list.')}</p>}
        {library.status === 'ready' && <>
          {visibleRevisions.length ? <ul className="local-revision-list">
            {visibleRevisions.map(entry => {
              const folder = 'origin_path' in entry;
              const venue = folder ? entry.series.venue : entry.bar_summary?.venue;
              const timeframe = folder ? entry.series.timeframe : entry.timeframe;
              return <li key={entry.revision_id}>
                <button type="button" className="secondary-button" disabled={working} onClick={() => void open(entry.revision_id, true)}>
                  <strong>{folder ? t('Open folder revision from {path}', { path: entry.origin_path }) : t('Open {path}', { path: entry.relative_path })}</strong>
                  <span>{entry.symbol}{venue && ` · ${venue}`} · {timeframe} · {
                    plural('{displayCount} bar', '{displayCount} bars', entry.bar_count, {
                      displayCount: formatNumber(entry.bar_count, { useGrouping: false }),
                    })
                  } · {
                    folder ? plural('{displayCount} source', '{displayCount} sources', entry.source_count, {
                      displayCount: formatNumber(entry.source_count, { useGrouping: false }),
                    })
                      : plural('{displayCount} source row', '{displayCount} source rows', entry.source_record_count, {
                        displayCount: formatNumber(entry.source_record_count, { useGrouping: false }),
                      })
                  }</span>
                  {entry.bar_summary && <span className="local-revision-summary">
                    <b>{t('Final close {price}', { price: formatSourceBarValue(entry.bar_summary.final_close) })}</b> · {t('Trading day {day}', { day: entry.bar_summary.final_trading_day })}
                    {entry.bar_summary.first_trading_day !== entry.bar_summary.final_trading_day &&
                      <> · {t('First bar trading day {day}', { day: entry.bar_summary.first_trading_day })}</>}
                  </span>}
                  <span>{!folder && <>{t('CSV SHA-256')}{' '}<code>{entry.source_sha256.slice(0, 12)}</code> · </>}{t('Revision')}{' '}<code>{entry.revision_id.slice(0, 8)}</code></span>
                </button>
              </li>;
            })}
          </ul> : <p role="status">{cursors.length === 1 && !library.result.next_after_revision_id
            ? kind === 'folder' ? t('No published folder revisions are available in this profile.') : t('No published CSV revisions are available in this profile.')
            : t('No published revisions on this page.')}</p>}
          {(cursors.length > 1 || library.result.next_after_revision_id) && <div className="local-publication-actions">
            <button type="button" className="secondary-button" disabled={working || cursors.length < 2} onClick={() => browse(cursors.slice(0, -1))}>{t('Previous page')}</button>
            <button type="button" className="secondary-button" disabled={working || !library.result.next_after_revision_id}
              onClick={() => browse([...cursors, library.result.next_after_revision_id])}>{t('Next page')}</button>
          </div>}
        </>}
      </section>}
      <form onSubmit={event => { event.preventDefault(); if (validId) void open(revisionId); }}>
        <fieldset className="local-import-card" disabled={working}>
          <div className="local-import-fields">
            <label className="local-import-wide">{t('Revision ID')}
              <input value={draft} maxLength={36} autoComplete="off" spellCheck={false} onChange={event => {
                setDraft(event.currentTarget.value);
                setFrozen(undefined);
                setFailure(undefined);
                setNotice(undefined);
              }} />
            </label>
          </div>
          <button type="submit" className="secondary-button" disabled={!validId}>{kind === 'folder' ? t('Open frozen folder revision') : t('Open frozen chart')}</button>
        </fieldset>
      </form>
    </div>
  </dialog>;
}
