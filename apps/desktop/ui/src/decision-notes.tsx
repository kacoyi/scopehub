import { useCallback, useEffect, useRef, useState, type ChangeEvent, type FormEvent } from 'react';
import type {
  DecisionNoteBackupV1, DecisionNoteEvidenceRef, DecisionNoteOriginal, DecisionNoteReadOutput, DecisionNoteSaveInput,
} from '../../../../contracts/generated/protocol';
import {
  exportNoteBackup, noteErrorCode, prepareNote, previewNoteRestore, readNotes, resolveNote, resolveNoteRestore,
  type NoteIntent, type PreparedNote, type PreparedRestore,
} from './decision-note-transport';
import { readWorkspaceSnapshot, type LocalWorkspace } from './use-local-workspace';
import { WorkspaceIcon } from './workspace-icons';

type Page = { noteId: string | null; offset: number; revision: number | undefined; epoch: number; data: DecisionNoteReadOutput };
type Editor = 'note' | 'outcome';
type PendingAction = { kind: 'note'; note: PreparedNote } | { kind: 'restore'; restore: PreparedRestore };

const actions = {
  'decisionNote.save': { title: 'Save this decision note locally?', confirm: 'Save decision note', done: 'Decision note saved.' },
  'decisionNote.appendOutcome': { title: 'Save this later outcome?', confirm: 'Save later outcome', done: 'Later outcome saved.' },
  'decisionNote.delete': { title: 'Delete this decision note and its later outcomes?', confirm: 'Delete decision note', done: 'Decision note deleted.' },
} as const;

function failureMessage(error: unknown) {
  switch (noteErrorCode(error)) {
    case 'STALE_CONTEXT': return 'The workspace changed. Reload its current state and review again.';
    case 'CONFIRMATION_REQUIRED': return 'Approval expired. Review the action again.';
    case 'DATA_UNAVAILABLE': return 'This decision note is unavailable.';
    case 'PERMISSION_DENIED': return 'This decision note action is no longer authorized.';
    case 'BUSY': return 'The local workspace is busy. Try again.';
    case 'RESOURCE_LIMIT': return 'The decision note exceeds a workspace limit.';
    case 'INVALID_ARGUMENT': return 'Check the dates and text. A later outcome needs a date after the original decision.';
    case 'CANCELLED': return 'The action was cancelled.';
    default: return 'The local workspace could not verify the result.';
  }
}

function backupFailureMessage(error: unknown) {
  switch (noteErrorCode(error)) {
    case 'INVALID_ARGUMENT': return 'Choose a valid ScopeHub decision-note backup file.';
    case 'PERMISSION_DENIED': return 'This backup or action is unavailable for the current local profile.';
    case 'RESOURCE_LIMIT': return 'The decision-note backup exceeds the size limit.';
    default: return failureMessage(error);
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
      <legend>Original decision</legend>
      <label>Decision date<input name="date" type="date" required defaultValue={localDate()} /></label>
      <label>Thesis<textarea name="thesis" required maxLength={2048} rows={3} spellCheck={false} /></label>
      <fieldset className="note-evidence">
        <legend>Evidence references <small>(optional)</small></legend>
        {Array.from({ length: references }, (_, index) => <div className="note-reference" key={index}>
          <label>Source ID<input name={`source-${index}`} maxLength={96} spellCheck={false} /></label>
          <label>Source revision<input name={`revision-${index}`} maxLength={96} spellCheck={false} /></label>
          <label>Locator<input name={`locator-${index}`} maxLength={256} spellCheck={false} /></label>
        </div>)}
        {references < 4 && <button className="secondary-button" type="button" onClick={() => setReferences(references + 1)}>Add evidence reference</button>}
        {references > 1 && <button className="secondary-button" type="button" onClick={() => setReferences(references - 1)}>Remove last reference</button>}
        {error && <p role="alert">Fill in the source ID, revision and locator for each reference you add.</p>}
      </fieldset>
      <label>Choice<textarea name="choice" required maxLength={1024} rows={2} spellCheck={false} /></label>
      <div className="note-actions">
        <button className="primary" type="submit">Review decision note</button>
        <button className="secondary-button" type="button" onClick={onDiscard}>Discard draft</button>
      </div>
    </fieldset>
  </form>;
}

function OutcomeEditor({ note, disabled, onReview, onDiscard }: {
  note: DecisionNoteOriginal; disabled: boolean; onReview: (intent: NoteIntent) => void; onDiscard: () => void;
}) {
  return <form autoComplete="off" onSubmit={event => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    onReview({ operation: 'decisionNote.appendOutcome', arguments: { observed_on: text(form, 'date'), outcome: text(form, 'body') } });
  }}>
    <fieldset className="note-fields" disabled={disabled}>
      <legend>Later outcome</legend>
      <label>Outcome date<input name="date" type="date" required min={note.decided_on} defaultValue={localDate()} /></label>
      <label>Outcome<textarea name="body" required maxLength={2048} rows={3} spellCheck={false} /></label>
      <div className="note-actions">
        <button className="primary" type="submit">Review later outcome</button>
        <button className="secondary-button" type="button" onClick={onDiscard}>Discard draft</button>
      </div>
    </fieldset>
  </form>;
}

function OriginalDecision({ note }: { note: DecisionNoteOriginal }) {
  return <section className="note-original" aria-label="Original decision">
    <h3>Original decision</h3>
    <time dateTime={note.decided_on}>{note.decided_on}</time>
    <dl>
      <dt>Thesis</dt><dd>{note.thesis}</dd>
      <dt>Evidence references</dt><dd>{note.evidence_refs.length ? <ul>{note.evidence_refs.map((item, index) =>
        <li key={index}>{item.source_id} · {item.source_revision} · {item.locator}</li>)}</ul> : 'None recorded'}</dd>
      <dt>Choice</dt><dd>{note.choice}</dd>
    </dl>
  </section>;
}

export function DecisionNotes({ workspace, onClose }: { workspace: LocalWorkspace; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const cancelApproval = useRef<HTMLButtonElement>(null);
  const active = useRef<AbortController | null>(null);
  const detailProbe = useRef<ReturnType<typeof readWorkspaceSnapshot> | null>(null);
  const pageEpoch = useRef(0);
  const [page, setPage] = useState<Page>();
  const [editor, setEditor] = useState<Editor>();
  const [prepared, setPrepared] = useState<PendingAction>();
  const [uncertain, setUncertain] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const snapshot = workspace.snapshot;
  const workspaceId = snapshot?.workspace.id;
  const note = page?.data.note ?? null;

  const discardPage = useCallback(() => {
    pageEpoch.current += 1;
    setPage(undefined);
  }, []);

  const run = useCallback((work: (signal: AbortSignal) => Promise<void>, formatError: (fault: unknown) => string = failureMessage) => {
    if (active.current) return;
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    setError('');
    void (async () => {
      // A liveness check already admitted to the writer owns its reply slot.
      await detailProbe.current?.catch(() => undefined);
      controller.signal.throwIfAborted();
      await work(controller.signal);
    })().catch(fault => {
      if (!controller.signal.aborted) setError(formatError(fault));
    }).finally(() => {
      if (active.current !== controller) return;
      active.current = null;
      setBusy(false);
    });
  }, []);

  const load = useCallback(async (noteId: string | null, offset: number, signal: AbortSignal, atRevision?: number) => {
    if (!workspaceId) return;
    // Capture the revision first so a concurrent commit cannot label an old
    // detail body with a newer revision.
    const revision = noteId === null ? undefined : atRevision ?? (await readWorkspaceSnapshot(signal)).workspace.revision;
    const data = await readNotes(workspaceId, noteId, offset, signal);
    if (!signal.aborted) setPage({ noteId, offset, revision, epoch: ++pageEpoch.current, data });
  }, [workspaceId]);

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
    if (!page?.noteId || page.revision === undefined) return;
    const controller = new AbortController();
    const isCurrent = () => !controller.signal.aborted && pageEpoch.current === page.epoch;
    const clearSelection = () => {
      setEditor(undefined);
      setPrepared(undefined);
      setUncertain(false);
      setNotice('');
    };
    const check = () => {
      if (!isCurrent() || workspace.pending || active.current || detailProbe.current) return;
      const probe = readWorkspaceSnapshot(controller.signal);
      detailProbe.current = probe;
      void probe.then(snapshot => {
        if (!isCurrent() || active.current || snapshot.workspace.revision === page.revision) return;
        run(async signal => {
          if (!isCurrent()) return;
          discardPage();
          try {
            await load(page.noteId, page.offset, signal, snapshot.workspace.revision);
          } catch (fault) {
            if (signal.aborted || noteErrorCode(fault) !== 'DATA_UNAVAILABLE') throw fault;
            clearSelection();
            await load(null, 0, signal);
          }
        });
      }, fault => {
        if (!isCurrent()) return;
        const code = noteErrorCode(fault);
        if (fault instanceof TypeError || code === 'PERMISSION_DENIED' || code === 'WORKER_EXITED' || code === 'CANCELLED') {
          discardPage();
          clearSelection();
          setError(failureMessage(fault));
        }
      }).finally(() => {
        if (detailProbe.current === probe) detailProbe.current = null;
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
  }, [discardPage, load, page, run, workspace.pending]);

  useEffect(() => { if (prepared && !busy) cancelApproval.current?.focus(); }, [prepared, busy]);

  function navigate(noteId: string | null, offset = 0) {
    setNotice('');
    run(signal => load(noteId, offset, signal));
  }

  function review(noteId: string, intent: NoteIntent) {
    if (!snapshot || workspace.pending || workspace.error) return;
    setNotice('');
    run(async signal => {
      const action = await prepareNote(snapshot.workspace.id, snapshot.workspace.revision, noteId, intent, signal);
      if (!signal.aborted) setPrepared({ kind: 'note', note: action });
    });
  }

  function exportBackup() {
    if (!workspaceId) return;
    setNotice('');
    run(async signal => {
      const backup = await exportNoteBackup(workspaceId, signal);
      signal.throwIfAborted();
      downloadBackup(backup);
      setNotice('Decision-note backup download started.');
    }, backupFailureMessage);
  }

  function selectBackup(event: ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = '';
    if (!file || !snapshot) return;
    setNotice('');
    run(async signal => {
      const restore = await previewNoteRestore(file, snapshot.workspace.id, snapshot.workspace.revision, signal);
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
      try {
        if (prepared.kind === 'restore') await resolveNoteRestore(prepared.restore, decision, signal);
        else await resolveNote(prepared.note, decision, signal);
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
      if (close) { onClose(); return; }
      if (decision === 'decline') { setNotice(restoring ? 'Restore cancelled.' : 'Action cancelled.'); return; }
      setNotice(operation ? actions[operation].done : 'Decision-note backup restored.');
      discardPage();
      try {
        await load(deleting ? null : noteId, 0, signal);
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
    <div className="dialog-heading"><h2 id="decision-notes-heading">Decision notes</h2>
      <button type="button" className="icon-button" aria-label="Close decision notes" disabled={busy} onClick={close}>
        <WorkspaceIcon name="close" /></button>
    </div>
    <div className="decision-body" aria-busy={busy || undefined}>
      {notice && <p className="note-status" role="status">{notice}</p>}
      {error && <p className="note-error" role="alert">{error}</p>}
      {(workspace.error || error.startsWith('The workspace changed.')) && <button type="button" className="secondary-button"
        disabled={busy || Boolean(workspace.pending)} onClick={workspace.reload}>Reload workspace state</button>}
      {!page && !editor && !prepared && (busy ? <p role="status">Loading decision notes…</p>
        : <button type="button" className="secondary-button" onClick={() => navigate(null)}>Reload decision notes</button>)}
      {page && !editor && !prepared && (note ? <div className="note-actions">
        <button type="button" className="secondary-button" disabled={busy} onClick={() => navigate(null)}>All decision notes</button>
        <button type="button" className="secondary-button" disabled={editingBlocked} onClick={() => setEditor('outcome')}>Add later outcome</button>
        <button type="button" className="secondary-button" disabled={editingBlocked}
          onClick={() => review(note.note_id, { operation: 'decisionNote.delete', arguments: {} })}>Review deletion</button>
      </div> : <button type="button" className="primary" disabled={editingBlocked} onClick={() => setEditor('note')}>New decision note</button>)}
      {editor === 'note' && <NoteEditor disabled={editingBlocked} onDiscard={() => { setEditor(undefined); setError(''); }}
        onReview={args => review(crypto.randomUUID(), { operation: 'decisionNote.save', arguments: args })} />}
      {note && <OriginalDecision note={note} />}
      {editor === 'outcome' && note && <OutcomeEditor note={note} disabled={editingBlocked}
        onDiscard={() => { setEditor(undefined); setError(''); }} onReview={intent => review(note.note_id, intent)} />}
      {action && <section className="note-approval" aria-label="Confirm action">
        <h3>{action.title}</h3>
        {uncertain && <p>The save or deletion may have completed. Retry to recover its result.</p>}
        <div className="note-actions">
          <button ref={cancelApproval} type="button" className="secondary-button" disabled={busy || uncertain}
            onClick={() => finish('decline')}>Cancel action</button>
          <button type="button" className="primary" disabled={busy} onClick={() => finish('approve')}>
            {uncertain ? 'Retry confirmation' : action.confirm}</button>
        </div>
      </section>}
      {restoreSummary && <section className="note-approval note-restore-preview" aria-label="Restore preview">
        <h3>Preview decision-note restore</h3>
        <p>Apply adds missing notes and outcomes from the backup and removes notes covered by known deletion markers.</p>
        <dl>
          <dt>Notes to add</dt><dd>{restoreSummary.added_notes}</dd>
          <dt>Later outcomes to add</dt><dd>{restoreSummary.added_outcomes}</dd>
          <dt>Current notes to remove</dt><dd>{restoreSummary.removed_notes}</dd>
          <dt>Backup notes blocked by deletion markers</dt><dd>{restoreSummary.suppressed_notes}</dd>
          <dt>Deletion markers to import</dt><dd>{restoreSummary.imported_deletions}</dd>
        </dl>
        {restoreSummary.independent_copy_warning && <p className="note-error" role="alert">
          An independent older backup may bring back previously deleted decision notes whose deletion markers are unavailable.
        </p>}
        {uncertain && <p>The restore may have completed. Retry to recover its result.</p>}
        <div className="note-actions">
          <button ref={cancelApproval} type="button" className="secondary-button" disabled={busy || uncertain}
            onClick={() => finish('decline')}>Cancel restore</button>
          <button type="button" className="primary" disabled={busy} onClick={() => finish('approve')}>
            {uncertain ? 'Retry restore' : 'Apply restore'}</button>
        </div>
      </section>}
      {page && !editor && !prepared && (note ? <section className="note-outcomes" aria-label="Later outcomes">
        <h3>Later outcomes</h3>
        {page.data.outcomes.length ? <ul>{page.data.outcomes.map(outcome => <li key={outcome.outcome_id}>
          <time dateTime={outcome.observed_on}>{outcome.observed_on}</time><p>{outcome.outcome}</p>
        </li>)}</ul> : <p>No later outcomes.</p>}
      </section> : page.data.notes.length ? <ul className="note-list" aria-label="Saved decisions">
        {page.data.notes.map(summary => <li key={summary.note_id}><button type="button" className="secondary-button" disabled={busy}
          onClick={() => navigate(summary.note_id)}><time dateTime={summary.decided_on}>{summary.decided_on}</time>
          <small>{summary.note_id.slice(-8)}</small></button></li>)}
      </ul> : <p>No saved decision notes.</p>)}
      {page && !editor && !prepared && (page.offset > 0 || page.data.next_offset !== null) && <div className="note-actions">
        {page.offset > 0 && <button type="button" className="secondary-button" disabled={busy}
          onClick={() => navigate(page.noteId, Math.max(0, page.offset - (note ? 10 : 50)))}>Previous page</button>}
        {page.data.next_offset !== null && <button type="button" className="secondary-button" disabled={busy}
          onClick={() => navigate(page.noteId, page.data.next_offset ?? 0)}>Next page</button>}
      </div>}
      {page && !note && !editor && !prepared && <section className="note-backup" aria-label="Decision-note backup">
        <h3>Decision-note backup</h3>
        <p>Backup files contain private notes and outcomes. Keep them in a private location.</p>
        <div className="note-actions">
          <button type="button" className="secondary-button" disabled={editingBlocked} onClick={exportBackup}>Download backup</button>
          <label>Choose backup file<input type="file" accept=".json,application/json" disabled={editingBlocked} onChange={selectBackup} /></label>
        </div>
      </section>}
    </div>
  </dialog>;
}
