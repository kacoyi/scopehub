import { useCallback, useEffect, useRef, useState, type ChangeEvent, type FormEvent } from 'react';
import type {
  DecisionNoteBackupV1, DecisionNoteEvidenceRef, DecisionNoteOriginal, DecisionNoteReadOutput, DecisionNoteSaveInput,
} from '../../../../contracts/generated/protocol';
import {
  exportNoteBackup, noteErrorCode, prepareNote, previewNoteRestore, readNotes, resolveNote, resolveNoteRestore,
  type NoteIntent, type PreparedNote, type PreparedRestore,
} from './decision-note-transport';
import { formatNumber, message, t, useLocale, type Message } from './i18n';
import { withReplyDeadline } from './reply-deadline';
import { readWorkspaceSnapshot, type LocalWorkspace } from './use-local-workspace';
import { WorkspaceIcon } from './workspace-icons';

type Page = { noteId: string | null; offset: number; revision: number; epoch: number; data: DecisionNoteReadOutput };
type Editor = 'note' | 'outcome';
type OutcomeDraft = { noteId: string; observed_on: string; outcome: string };
type PendingAction = { kind: 'note'; note: PreparedNote } | { kind: 'restore'; restore: PreparedRestore };
type NoteFailure = { code: unknown; description: Message };

// Leave room for the transport's four-second admission retry inside the full fallback.
const fallbackReplyDeadlineMs = 5_000;
const listRefreshFailure = message('The decision-note list could not be refreshed.');

const actions = {
  'decisionNote.save': {
    title: message('Save this decision note locally?'), confirm: message('Save decision note'), done: message('Decision note saved.'),
  },
  'decisionNote.appendOutcome': {
    title: message('Save this later outcome?'), confirm: message('Save later outcome'), done: message('Later outcome saved.'),
  },
  'decisionNote.delete': {
    title: message('Delete this decision note and its later outcomes?'), confirm: message('Delete decision note'), done: message('Decision note deleted.'),
  },
};

function failureMessage(code: unknown) {
  switch (code) {
    case 'STALE_CONTEXT': return message('The workspace changed. Reload its current state and review again.');
    case 'CONFIRMATION_REQUIRED': return message('Approval expired. Review the action again.');
    case 'DATA_UNAVAILABLE': return message('This decision note is unavailable.');
    case 'PERMISSION_DENIED': return message('This decision note action is no longer authorized.');
    case 'BUSY': return message('The local workspace is busy. Try again.');
    case 'TIMEOUT': return message('The decision-note request timed out.');
    case 'RESOURCE_LIMIT': return message('This action exceeds the size or record limit for a complete decision-note backup.');
    case 'INVALID_ARGUMENT': return message('Check the dates and text. A later outcome needs a date after the original decision.');
    case 'CANCELLED': return message('The action was cancelled.');
    default: return message('The local workspace could not verify the result.');
  }
}

function backupFailureMessage(code: unknown) {
  switch (code) {
    case 'INVALID_ARGUMENT': return message('Choose a valid ScopeHub decision-note backup file.');
    case 'PERMISSION_DENIED': return message('This backup or action is unavailable for the current local profile.');
    case 'RESOURCE_LIMIT': return message('The selected file or resulting backup exceeds the size or record limit.');
    default: return failureMessage(code);
  }
}

function downloadBackup(backup: DecisionNoteBackupV1) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(backup)], { type: 'application/json' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = `scopehub-decision-notes-${localDate()}.json`;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

function localDate() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
}

function text(form: FormData, name: string) {
  return String(form.get(name) ?? '');
}

function NoteEditor({ disabled, onReview, onDiscard }: {
  disabled: boolean; onReview: (note: DecisionNoteSaveInput) => void; onDiscard: () => void;
}) {
  const [references, setReferences] = useState(1);
  const [error, setError] = useState(false);
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const evidence: DecisionNoteEvidenceRef[] = [];
    for (let index = 0; index < references; index++) {
      const source = {
        source_id: text(form, `source-${index}`), source_revision: text(form, `revision-${index}`), locator: text(form, `locator-${index}`),
      };
      const filled = Object.values(source).filter(value => value.trim()).length;
      if (filled === 3) evidence.push(source);
      else if (filled > 0) { setError(true); return; }
    }
    setError(false);
    onReview({ decided_on: text(form, 'date'), thesis: text(form, 'thesis'), evidence_refs: evidence, choice: text(form, 'choice') });
  };
  return <form autoComplete="off" onSubmit={submit}>
    <fieldset className="note-fields" disabled={disabled}>
      <legend>{t('Original decision')}</legend>
      <label>{t('Decision date')}<input name="date" type="date" required defaultValue={localDate()} /></label>
      <label>{t('Thesis')}<textarea name="thesis" required maxLength={2048} rows={3} spellCheck={false} /></label>
      <fieldset className="note-evidence">
        <legend>{t('Evidence references')} <small>{t('(optional)')}</small></legend>
        {Array.from({ length: references }, (_, index) => <div className="note-reference" key={index}>
          <label>{t('Source ID')}<input name={`source-${index}`} maxLength={96} spellCheck={false} /></label>
          <label>{t('Source revision')}<input name={`revision-${index}`} maxLength={96} spellCheck={false} /></label>
          <label>{t('Locator')}<input name={`locator-${index}`} maxLength={256} spellCheck={false} /></label>
        </div>)}
        {references < 4 && <button className="secondary-button" type="button" onClick={() => setReferences(references + 1)}>{t('Add evidence reference')}</button>}
        {references > 1 && <button className="secondary-button" type="button" onClick={() => setReferences(references - 1)}>{t('Remove last reference')}</button>}
        {error && <p role="alert">{t('Fill in the source ID, revision and locator for each reference you add.')}</p>}
      </fieldset>
      <label>{t('Choice')}<textarea name="choice" required maxLength={1024} rows={2} spellCheck={false} /></label>
      <div className="note-actions">
        <button className="primary" type="submit">{t('Review decision note')}</button>
        <button className="secondary-button" type="button" onClick={onDiscard}>{t('Discard draft')}</button>
      </div>
    </fieldset>
  </form>;
}

function OutcomeEditor({ note, draft, disabled, onChange, onReview, onDiscard }: {
  note: DecisionNoteOriginal; draft: OutcomeDraft; disabled: boolean;
  onChange: (draft: OutcomeDraft) => void; onReview: (intent: NoteIntent) => void; onDiscard: () => void;
}) {
  return <form autoComplete="off" onSubmit={event => {
    event.preventDefault();
    onReview({ operation: 'decisionNote.appendOutcome', arguments: { observed_on: draft.observed_on, outcome: draft.outcome } });
  }}>
    <fieldset className="note-fields" disabled={disabled}>
      <legend>{t('Later outcome')}</legend>
      <label>{t('Outcome date')}<input name="date" type="date" required min={note.decided_on} value={draft.observed_on}
        onChange={event => onChange({ ...draft, observed_on: event.target.value })} /></label>
      <label>{t('Outcome')}<textarea name="body" required maxLength={2048} rows={3} spellCheck={false} value={draft.outcome}
        onChange={event => onChange({ ...draft, outcome: event.target.value })} /></label>
      <div className="note-actions">
        <button className="primary" type="submit">{t('Review later outcome')}</button>
        <button className="secondary-button" type="button" onClick={onDiscard}>{t('Discard draft')}</button>
      </div>
    </fieldset>
  </form>;
}

function OriginalDecision({ note }: { note: DecisionNoteOriginal }) {
  return <section className="note-original" aria-label={t('Original decision')}>
    <h3>{t('Original decision')}</h3>
    <time dateTime={note.decided_on}>{note.decided_on}</time>
    <dl>
      <dt>{t('Thesis')}</dt><dd>{note.thesis}</dd>
      <dt>{t('Evidence references')}</dt><dd>{note.evidence_refs.length ? <ul>{note.evidence_refs.map((item, index) =>
        <li key={index}>{item.source_id} · {item.source_revision} · {item.locator}</li>)}</ul> : t('None recorded')}</dd>
      <dt>{t('Choice')}</dt><dd>{note.choice}</dd>
    </dl>
  </section>;
}

function lostNoteSession(fault: unknown) {
  const code = noteErrorCode(fault);
  return fault instanceof TypeError || code === 'PERMISSION_DENIED' || code === 'WORKER_EXITED' || code === 'CANCELLED';
}

export function DecisionNotes({ workspace, onClose }: { workspace: LocalWorkspace; onClose: () => void }) {
  useLocale();
  const dialog = useRef<HTMLDialogElement>(null);
  const cancelApproval = useRef<HTMLButtonElement>(null);
  const active = useRef<AbortController | null>(null);
  const projectionProbe = useRef<ReturnType<typeof readWorkspaceSnapshot> | null>(null);
  const pageEpoch = useRef(0);
  const [page, setPage] = useState<Page>();
  const [editor, setEditor] = useState<Editor>();
  const [outcomeDraft, setOutcomeDraft] = useState<OutcomeDraft>();
  const [prepared, setPrepared] = useState<PendingAction>();
  const [uncertain, setUncertain] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Message>();
  const [error, setError] = useState<NoteFailure>();
  const snapshot = workspace.snapshot;
  const workspaceId = snapshot?.workspace.id;
  const note = page?.data.note ?? null;

  const discardPage = useCallback(() => {
    pageEpoch.current += 1;
    setPage(undefined);
  }, []);

  const clearSelection = useCallback(() => {
    setEditor(undefined);
    setOutcomeDraft(undefined);
    setPrepared(undefined);
    setUncertain(false);
    // The confirmed deletion belongs to the action, independent of a subsequent list read.
    setNotice(previous => previous === actions['decisionNote.delete'].done ? previous : undefined);
  }, []);

  const run = useCallback((work: (signal: AbortSignal) => Promise<void>, describe: (code: unknown) => Message = failureMessage) => {
    if (active.current) return;
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    setError(undefined);
    void (async () => {
      // A liveness check already admitted to the writer owns its reply slot.
      await projectionProbe.current?.catch(() => undefined);
      controller.signal.throwIfAborted();
      await work(controller.signal);
    })().catch(fault => {
      if (!controller.signal.aborted) {
        const code = fault instanceof DOMException && fault.name === 'TimeoutError' ? 'TIMEOUT' : noteErrorCode(fault);
        setError({ code, description: describe(code) });
      }
    }).finally(() => {
      if (active.current !== controller) return;
      active.current = null;
      setBusy(false);
    });
  }, []);

  const load = useCallback(async (noteId: string | null, offset: number, signal: AbortSignal,
    { atRevision, waitForAdmission = false }: { atRevision?: number; waitForAdmission?: boolean } = {},
  ) => {
    if (!workspaceId) return;
    // Capture the revision first so a concurrent commit cannot label old
    // content or an old list as current.
    try {
      const revision = atRevision ?? (await readWorkspaceSnapshot(signal)).workspace.revision;
      const data = await readNotes(workspaceId, noteId, offset, signal, { waitForAdmission });
      if (!signal.aborted) setPage({ noteId, offset, revision, epoch: ++pageEpoch.current, data });
    } catch (fault) {
      if (signal.aborted) return;
      if (lostNoteSession(fault)) {
        discardPage();
        clearSelection();
      } else if (noteId !== null && noteErrorCode(fault) === 'DATA_UNAVAILABLE') {
        discardPage();
        clearSelection();
        const { revision, data } = await withReplyDeadline(fallbackReplyDeadlineMs, async deadline => {
          const revision = (await readWorkspaceSnapshot(deadline)).workspace.revision;
          const data = await readNotes(workspaceId, null, 0, deadline, { waitForAdmission: true });
          return { revision, data };
        }, signal);
        if (!signal.aborted) setPage({ noteId: null, offset: 0, revision, epoch: ++pageEpoch.current, data });
      }
      throw fault;
    }
  }, [clearSelection, discardPage, workspaceId]);

  useEffect(() => {
    const modal = dialog.current;
    if (!modal) return;
    modal.showModal();
    run(signal => load(null, 0, signal));
    return () => {
      active.current?.abort();
      active.current = null;
      modal.close();
    };
  }, [load, run]);

  useEffect(() => {
    if (!page) return;
    const controller = new AbortController();
    const isCurrent = () => !controller.signal.aborted && pageEpoch.current === page.epoch;
    const check = () => {
      if (!isCurrent() || workspace.pending || active.current || projectionProbe.current) return;
      const probe = readWorkspaceSnapshot(controller.signal);
      projectionProbe.current = probe;
      void probe.then(snapshot => {
        if (!isCurrent() || active.current || snapshot.workspace.revision === page.revision) return;
        run(async signal => {
          if (!isCurrent()) return;
          discardPage();
          // An unsubmitted draft belongs to the editor. A prepared approval belongs to its revision.
          if (!uncertain) setPrepared(undefined);
          await load(page.noteId, page.offset, signal, { atRevision: snapshot.workspace.revision });
        });
      }, fault => {
        if (!isCurrent()) return;
        if (lostNoteSession(fault)) {
          discardPage();
          clearSelection();
          const code = noteErrorCode(fault);
          setError({ code, description: failureMessage(code) });
        }
      }).finally(() => {
        if (projectionProbe.current === probe) projectionProbe.current = null;
      });
    };
    const timer = window.setInterval(check, 1_000);
    window.addEventListener('focus', check);
    document.addEventListener('visibilitychange', check);
    return () => {
      controller.abort();
      window.clearInterval(timer);
      window.removeEventListener('focus', check);
      document.removeEventListener('visibilitychange', check);
    };
  }, [clearSelection, discardPage, load, page, run, uncertain, workspace.pending]);

  useEffect(() => { if (prepared && !busy) cancelApproval.current?.focus(); }, [prepared, busy]);

  function navigate(noteId: string | null, offset = 0) {
    setNotice(undefined);
    run(signal => load(noteId, offset, signal));
  }

  function reloadNotes() {
    const noteId = outcomeDraft?.noteId ?? null;
    const describe = noteId === null && notice === actions['decisionNote.delete'].done
      ? () => listRefreshFailure
      : failureMessage;
    run(signal => load(noteId, 0, signal), describe);
  }

  function review(noteId: string, intent: NoteIntent) {
    if (!workspaceId || !page || workspace.pending || workspace.error) return;
    setNotice(undefined);
    run(async signal => {
      const action = await prepareNote(workspaceId, page.revision, noteId, intent, signal);
      if (!signal.aborted) setPrepared({ kind: 'note', note: action });
    });
  }

  function exportBackup() {
    if (!workspaceId) return;
    setNotice(undefined);
    run(async signal => {
      const backup = await exportNoteBackup(workspaceId, signal);
      signal.throwIfAborted();
      downloadBackup(backup);
      setNotice(message('Decision-note backup download started.'));
    }, backupFailureMessage);
  }

  function selectBackup(event: ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = '';
    if (!file || !workspaceId || !page) return;
    setNotice(undefined);
    run(async signal => {
      const restore = await previewNoteRestore(file, workspaceId, page.revision, signal);
      if (!signal.aborted) setPrepared({ kind: 'restore', restore });
    }, backupFailureMessage);
  }

  function finish(decision: 'approve' | 'decline', close = false) {
    if (!prepared) return;
    const restoring = prepared.kind === 'restore';
    const operation = prepared.kind === 'note' ? prepared.note.command.intent.operation : undefined;
    const noteId = prepared.kind === 'note' ? prepared.note.command.scope.target.object_id : null;
    const deleting = operation === 'decisionNote.delete';
    const clearPage = decision === 'approve' && (restoring || deleting);
    run(async signal => {
      if (clearPage) discardPage();
      let deletionRevision: number | undefined;
      try {
        if (prepared.kind === 'restore') await resolveNoteRestore(prepared.restore, decision, signal);
        else {
          const receipt = await resolveNote(prepared.note, decision, signal);
          if (deleting) deletionRevision = receipt?.committed_workspace_revision;
        }
      } catch (fault) {
        const code = noteErrorCode(fault);
        if (!(decision === 'decline' && code === 'CONFIRMATION_REQUIRED')) {
          if (!signal.aborted && decision === 'approve') {
            switch (code) {
              case 'STALE_CONTEXT': case 'CONFIRMATION_REQUIRED': case 'PERMISSION_DENIED':
              case 'INVALID_ARGUMENT': case 'DATA_UNAVAILABLE': case 'CANCELLED': case 'RESOURCE_LIMIT':
                setPrepared(undefined);
                setUncertain(false);
                if (clearPage) await load(noteId, 0, signal).catch(() => { /* The live selection may already be unavailable. */ });
                break;
              default: setUncertain(true);
            }
          }
          throw fault;
        }
      }
      if (signal.aborted) return;
      setPrepared(undefined);
      setUncertain(false);
      setEditor(undefined);
      setOutcomeDraft(undefined);
      if (close) { onClose(); return; }
      if (decision === 'decline') {
        setNotice(restoring ? message('Restore cancelled.') : message('Action cancelled.'));
        return;
      }
      setNotice(operation ? actions[operation].done : message('Decision-note backup restored.'));
      discardPage();
      try {
        // The verified delete receipt already supplies a safe lower bound for the list.
        await load(deleting ? null : noteId, 0, signal, deletionRevision === undefined ? undefined : {
          atRevision: deletionRevision, waitForAdmission: true,
        });
      } catch (fault) {
        if (!deleting) throw fault;
        signal.throwIfAborted();
        setError({ code: noteErrorCode(fault), description: listRefreshFailure });
      } finally {
        if (!signal.aborted) workspace.reload();
      }
    }, restoring ? backupFailureMessage : failureMessage);
  }

  function close() {
    if (active.current) return;
    if (prepared && !uncertain) finish('decline', true);
    else {
      if (uncertain) workspace.reload();
      onClose();
    }
  }

  const editingBlocked = busy || Boolean(prepared) || Boolean(workspace.pending || workspace.error);
  const action = prepared?.kind === 'note' ? actions[prepared.note.command.intent.operation] : undefined;
  const restoreSummary = prepared?.kind === 'restore' ? prepared.restore.summary : undefined;
  return <dialog ref={dialog} className="symbol-dialog decision-dialog" aria-labelledby="decision-notes-heading"
    onCancel={event => { event.preventDefault(); close(); }}>
    <div className="dialog-heading"><h2 id="decision-notes-heading">{t('Decision notes')}</h2>
      <button type="button" className="icon-button" aria-label={t('Close decision notes')} disabled={busy} onClick={close}>
        <WorkspaceIcon name="close" /></button>
    </div>
    <div className="decision-body" aria-busy={busy || undefined}>
      {notice && <p className="note-status" role="status">{t(notice)}</p>}
      {error && <p className="note-error" role="alert">{t(error.description)}</p>}
      {(workspace.error || error?.code === 'STALE_CONTEXT') && <button type="button" className="secondary-button"
        disabled={busy || Boolean(workspace.pending)} onClick={workspace.reload}>{t('Reload workspace state')}</button>}
      {!page && !prepared && (busy ? <p role="status">{t('Loading decision notes…')}</p>
        : <button type="button" className="secondary-button"
          onClick={reloadNotes}>{t('Reload decision notes')}</button>)}
      {page && !editor && !prepared && (note ? <div className="note-actions">
        <button type="button" className="secondary-button" disabled={busy} onClick={() => navigate(null)}>{t('All decision notes')}</button>
        <button type="button" className="secondary-button" disabled={editingBlocked} onClick={() => {
          setOutcomeDraft({ noteId: note.note_id, observed_on: localDate(), outcome: '' });
          setEditor('outcome');
        }}>{t('Add later outcome')}</button>
        <button type="button" className="secondary-button" disabled={editingBlocked}
          onClick={() => review(note.note_id, { operation: 'decisionNote.delete', arguments: {} })}>{t('Review deletion')}</button>
      </div> : <button type="button" className="primary" disabled={editingBlocked} onClick={() => {
        setNotice(undefined);
        setEditor('note');
      }}>{t('New decision note')}</button>)}
      {editor === 'note' && <NoteEditor disabled={editingBlocked} onDiscard={() => { setEditor(undefined); setError(undefined); }}
        onReview={args => review(crypto.randomUUID(), { operation: 'decisionNote.save', arguments: args })} />}
      {note && <OriginalDecision note={note} />}
      {editor === 'outcome' && note && outcomeDraft?.noteId === note.note_id && <OutcomeEditor note={note} draft={outcomeDraft}
        disabled={editingBlocked} onChange={setOutcomeDraft}
        onDiscard={() => { setEditor(undefined); setOutcomeDraft(undefined); setError(undefined); }}
        onReview={intent => review(note.note_id, intent)} />}
      {action && <section className="note-approval" aria-label={t('Confirm action')}>
        <h3>{t(action.title)}</h3>
        {uncertain && <p>{t('The save or deletion may have completed. Retry to recover its result.')}</p>}
        <div className="note-actions">
          <button ref={cancelApproval} type="button" className="secondary-button" disabled={busy || uncertain}
            onClick={() => finish('decline')}>{t('Cancel action')}</button>
          <button type="button" className="primary" disabled={busy} onClick={() => finish('approve')}>
            {uncertain ? t('Retry confirmation') : t(action.confirm)}</button>
        </div>
      </section>}
      {restoreSummary && <section className="note-approval note-restore-preview" aria-label={t('Restore preview')}>
        <h3>{t('Preview decision-note restore')}</h3>
        <p>{t('Apply adds missing notes and outcomes from the backup and removes notes covered by known deletion markers.')}</p>
        <dl>
          <dt>{t('Notes to add')}</dt><dd>{formatNumber(restoreSummary.added_notes)}</dd>
          <dt>{t('Later outcomes to add')}</dt><dd>{formatNumber(restoreSummary.added_outcomes)}</dd>
          <dt>{t('Current notes to remove')}</dt><dd>{formatNumber(restoreSummary.removed_notes)}</dd>
          <dt>{t('Backup notes blocked by deletion markers')}</dt><dd>{formatNumber(restoreSummary.suppressed_notes)}</dd>
          <dt>{t('Deletion markers to import')}</dt><dd>{formatNumber(restoreSummary.imported_deletions)}</dd>
        </dl>
        {restoreSummary.independent_copy_warning && <p className="note-error" role="alert">
          {t('An independent older backup may bring back previously deleted decision notes whose deletion markers are unavailable.')}
        </p>}
        {uncertain && <p>{t('The restore may have completed. Retry to recover its result.')}</p>}
        <div className="note-actions">
          <button ref={cancelApproval} type="button" className="secondary-button" disabled={busy || uncertain}
            onClick={() => finish('decline')}>{t('Cancel restore')}</button>
          <button type="button" className="primary" disabled={busy} onClick={() => finish('approve')}>
            {uncertain ? t('Retry restore') : t('Apply restore')}</button>
        </div>
      </section>}
      {page && !editor && !prepared && (note ? <section className="note-outcomes" aria-label={t('Later outcomes')}>
        <h3>{t('Later outcomes')}</h3>
        {page.data.outcomes.length ? <ul>{page.data.outcomes.map(outcome => <li key={outcome.outcome_id}>
          <time dateTime={outcome.observed_on}>{outcome.observed_on}</time><p>{outcome.outcome}</p>
        </li>)}</ul> : <p>{t('No later outcomes.')}</p>}
      </section> : page.data.notes.length ? <ul className="note-list" aria-label={t('Saved decisions')}>
        {page.data.notes.map(summary => <li key={summary.note_id}><button type="button" className="secondary-button" disabled={busy}
          onClick={() => navigate(summary.note_id)}><time dateTime={summary.decided_on}>{summary.decided_on}</time>
          <small>{summary.note_id.slice(-8)}</small></button></li>)}
      </ul> : <p>{t('No saved decision notes.')}</p>)}
      {page && !editor && !prepared && (page.offset > 0 || page.data.next_offset !== null) && <div className="note-actions">
        {page.offset > 0 && <button type="button" className="secondary-button" disabled={busy}
          onClick={() => navigate(page.noteId, Math.max(0, page.offset - (note ? 10 : 50)))}>{t('Previous page')}</button>}
        {page.data.next_offset !== null && <button type="button" className="secondary-button" disabled={busy}
          onClick={() => navigate(page.noteId, page.data.next_offset ?? 0)}>{t('Next page')}</button>}
      </div>}
      {page && !note && !editor && !prepared && <section className="note-backup" aria-label={t('Decision-note backup')}>
        <h3>{t('Decision-note backup')}</h3>
        <p>{t('Backup files contain private notes and outcomes. Keep them in a private location.')}</p>
        <div className="note-actions">
          <button type="button" className="secondary-button" disabled={editingBlocked} onClick={exportBackup}>{t('Download backup')}</button>
          <label>{t('Choose backup file')}<input type="file" accept=".json,application/json" disabled={editingBlocked} onChange={selectBackup} /></label>
        </div>
      </section>}
    </div>
  </dialog>;
}
