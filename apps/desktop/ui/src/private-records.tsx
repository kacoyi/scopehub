import { useCallback, useEffect, useRef, useState, type ChangeEvent, type FormEvent } from 'react';
import type {
  PrivateRecordBackupV1, PrivateRecordContent, PrivateRecordEntry, PrivateRecordKind,
  PrivateRecordReadOutput, PrivateRecordTurn,
} from '../../../../contracts/generated/protocol';
import {
  exportPrivateRecords, maxPrivateBackupBytes, preparePrivateRecord, readPrivateRecords, recordErrorCode,
  resolvePrivateRecord, type PrepareRecord, type RecordApproval,
} from './private-record-transport';
import type { LocalWorkspace } from './use-local-workspace';

const kinds = { conversation: 'Conversation exchange', memory: 'Long-term memory', strategy: 'Strategy' };
const speakerLabels = { user: 'You', assistant: 'Assistant' };

type Draft = { id: string; revision: number | null; content: PrivateRecordContent };
type ReviewTarget = { title: string; kind: PrivateRecordKind; proposedTitle?: string | undefined };
type PreparedReview = RecordApproval & { target: ReviewTarget | undefined };

function emptyContent(kind: PrivateRecordKind, title = ''): PrivateRecordContent {
  if (kind === 'conversation') {
    return { kind, title, turns: [{ speaker: 'user', text: '' }, { speaker: 'assistant', text: '' }] };
  }
  return { kind, title, text: '' };
}

function RecordEditor({ draft, current, disabled, onChange, onReview, onCancel }: {
  draft: Draft;
  current: PrivateRecordEntry | undefined;
  disabled: boolean;
  onChange: (draft: Draft) => void;
  onReview: (event: FormEvent) => void;
  onCancel: () => void;
}) {
  const { content, revision } = draft;
  const change = (value: PrivateRecordContent) => onChange({ ...draft, content: value });
  const updateTurn = (turns: PrivateRecordTurn[], index: number, turn: PrivateRecordTurn) =>
    turns.map((current, item) => item === index ? turn : current);
  return <form className="private-record-editor" onSubmit={onReview}>
    <h3>{revision === null ? 'New private record' : 'Correct private record'}</h3>
    {current && <section className="note-approval" aria-label="Current saved version">
      <p role="alert">Another window changed this record. Compare the current saved version with your draft before reviewing the correction.</p>
      <RecordDetail record={current} />
    </section>}
    <fieldset disabled={disabled}>
      {revision === null && <label>Record type
        <select value={content.kind} onChange={event => change(emptyContent(event.target.value as PrivateRecordKind, content.title))}>
          {Object.entries(kinds).map(([kind, label]) => <option key={kind} value={kind}>{label}</option>)}
        </select>
      </label>}
      <label>Title<input value={content.title} onChange={event => change({ ...content, title: event.target.value })}
        maxLength={120} autoComplete="off" required /></label>
      {content.kind === 'conversation' ? <>
        <p>Enter the typed turns you want to keep, in their original order.</p>
        {content.turns.map((turn, index) => <fieldset key={index} className="private-turn">
          <legend>Turn {index + 1}</legend>
          <label>Speaker
            <select value={turn.speaker} onChange={event => change({
              ...content, turns: updateTurn(content.turns, index, { ...turn, speaker: event.target.value as PrivateRecordTurn['speaker'] }),
            })}>
              <option value="user">You</option><option value="assistant">Assistant</option>
            </select>
          </label>
          <label>Text<textarea value={turn.text} onChange={event => change({
            ...content, turns: updateTurn(content.turns, index, { ...turn, text: event.target.value }),
          })} rows={3} maxLength={4096} spellCheck={false} required /></label>
          {content.turns.length > 2 && <button type="button" className="secondary-button"
            onClick={() => change({ ...content, turns: content.turns.filter((_, item) => item !== index) })}>Remove turn {index + 1}</button>}
        </fieldset>)}
        <button type="button" className="secondary-button" disabled={content.turns.length === 16}
          onClick={() => change({
            ...content, turns: [...content.turns, { speaker: content.turns.at(-1)?.speaker === 'user' ? 'assistant' : 'user', text: '' }],
          })}>Add turn</button>
      </> : <label>{content.kind === 'memory' ? 'Memory' : 'Strategy'}
        <textarea value={content.text} onChange={event => change({ ...content, text: event.target.value })}
          rows={6} maxLength={4096} spellCheck={false} required />
      </label>}
      <div className="private-record-actions">
        <button type="submit" className="primary">{current ? 'Review correction against current version' : 'Review save'}</button>
        <button type="button" className="secondary-button" onClick={onCancel}>Discard draft</button>
      </div>
    </fieldset>
  </form>;
}

function RecordDetail({ record }: { record: PrivateRecordEntry }) {
  const { content } = record;
  return <article className="private-record-detail">
    <h3>{content.title}</h3>
    <small>{kinds[content.kind]}</small>
    {content.kind === 'conversation' ? <ol>
      {content.turns.map((turn, index) => <li key={index}>
        <strong>{speakerLabels[turn.speaker]}</strong><p>{turn.text}</p>
      </li>)}
    </ol> : <p>{content.text}</p>}
  </article>;
}

function failureMessage(error: unknown) {
  switch (recordErrorCode(error)) {
    case 'STALE_CONTEXT': return 'The workspace or record changed. Review the current version before retrying.';
    case 'CONFIRMATION_REQUIRED': return 'The review expired. Review the action again.';
    case 'INVALID_ARGUMENT': return 'Check the record fields or select a valid private records backup.';
    case 'DATA_UNAVAILABLE': return 'This private record is no longer available.';
    case 'WORKER_EXITED': return 'The local workspace is unavailable. Private records are hidden.';
    case 'PERMISSION_DENIED': return 'This action or backup is unavailable in this local profile.';
    case 'RESOURCE_LIMIT': return 'This action exceeds a local record or backup limit.';
    default: return 'The local workspace could not complete the action.';
  }
}

export function PrivateRecords({ workspace, onClose }: { workspace: LocalWorkspace; onClose: () => void }) {
  const modal = useRef<HTMLDialogElement>(null);
  const cancelReview = useRef<HTMLButtonElement>(null);
  const active = useRef<AbortController | null>(null);
  const probe = useRef<{ controller: AbortController; task: Promise<void> } | null>(null);
  const selection = useRef<string | null>(null);
  const projectionEpoch = useRef(0);
  const observedRevision = useRef<number | undefined>(undefined);
  const [view, setView] = useState<PrivateRecordReadOutput>();
  const [draft, setDraft] = useState<Draft>();
  const [prepared, setPrepared] = useState<PreparedReview>();
  const [busy, setBusy] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [readError, setReadError] = useState('');
  const workspaceId = workspace.snapshot?.workspace.id;
  const record = view?.record;
  const recordId = record?.record_id ?? null;
  const changedRecord = record && draft?.id === record.record_id && draft.revision !== record.revision ? record : undefined;
  const expiredReview = prepared && !retrying && view
    && prepared.scope.expected_workspace_revision !== view.workspace_revision;
  const visibleReview = expiredReview ? undefined : prepared;

  const discardPrivateProjection = useCallback(() => {
    projectionEpoch.current += 1;
    observedRevision.current = undefined;
    setView(undefined);
    setDraft(undefined);
    setPrepared(undefined);
    setRetrying(false);
    setNotice('');
    setError('');
  }, []);

  const run = useCallback((work: (signal: AbortSignal) => Promise<void>, background = false) => {
    if (active.current || background && probe.current) return;
    const controller = new AbortController();
    const epoch = projectionEpoch.current;
    if (!background) {
      active.current = controller;
      setBusy(true);
      setError('');
    }
    const waiting = probe.current?.task;
    const task = (async () => {
      if (!background) await waiting;
      controller.signal.throwIfAborted();
      // A queued click must not use a selection invalidated by the active read.
      if (epoch === projectionEpoch.current) await work(controller.signal);
    })().catch(fault => {
      if (controller.signal.aborted) return;
      if (!background) {
        const code = recordErrorCode(fault);
        if (code === 'PERMISSION_DENIED' || code === 'WORKER_EXITED') discardPrivateProjection();
        setError(failureMessage(fault));
      }
    }).finally(() => {
      if (background && probe.current?.controller === controller) probe.current = null;
      if (active.current === controller) {
        active.current = null;
        setBusy(false);
      }
    });
    if (background) probe.current = { controller, task };
  }, [discardPrivateProjection]);

  const load = useCallback(async (id: string | null, signal: AbortSignal) => {
    if (!workspaceId) return;
    selection.current = id;
    try {
      const current = await readPrivateRecords(workspaceId, id, signal);
      observedRevision.current = current.workspace_revision;
      setView(current);
      setReadError('');
    } catch (fault) {
      if (signal.aborted) return;
      const code = recordErrorCode(fault);
      if (code === 'BUSY') throw fault;
      discardPrivateProjection();
      if (id && code === 'DATA_UNAVAILABLE') {
        selection.current = null;
        setError(failureMessage(fault));
        const current = await readPrivateRecords(workspaceId, null, signal);
        observedRevision.current = current.workspace_revision;
        setView(current);
        return;
      }
      setReadError(failureMessage(fault));
      throw fault;
    }
  }, [discardPrivateProjection, workspaceId]);

  useEffect(() => {
    const dialog = modal.current;
    dialog?.showModal();
    run(signal => load(null, signal));
    return () => {
      active.current?.abort();
      active.current = null;
      probe.current?.controller.abort();
      probe.current = null;
      dialog?.close();
    };
  }, [load, run]);

  useEffect(() => {
    const refresh = () => {
      if (!workspace.pending) run(signal => load(selection.current, signal), true);
    };
    const timer = window.setInterval(refresh, 1200);
    window.addEventListener('focus', refresh);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener('focus', refresh);
    };
  }, [load, run, workspace.pending]);

  useEffect(() => { if (visibleReview && !busy) cancelReview.current?.focus(); }, [visibleReview, busy]);

  useEffect(() => {
    if (!expiredReview) return;
    setPrepared(existing => existing === prepared ? undefined : existing);
    setError('The workspace changed. Review this change again.');
  }, [expiredReview, prepared]);

  const review = (args: PrepareRecord) => {
    if (!workspaceId || !view) return;
    const saved = record?.content;
    const target: ReviewTarget | undefined = args.action === 'save' ? {
      title: saved?.title ?? args.content.title,
      kind: args.content.kind,
      proposedTitle: saved && saved.title !== args.content.title ? args.content.title : undefined,
    } : args.action === 'delete' && saved ? { title: saved.title, kind: saved.kind } : undefined;
    setNotice('');
    run(async signal => {
      if (view.workspace_revision !== observedRevision.current) throw { code: 'STALE_CONTEXT' };
      const approval = await preparePrivateRecord(workspaceId, view.workspace_revision, args, signal);
      setPrepared({ ...approval, target });
    });
  };

  function downloadBackup() {
    if (!workspaceId) return;
    run(async signal => {
      const backup = await exportPrivateRecords(workspaceId, signal);
      const url = URL.createObjectURL(new Blob([JSON.stringify(backup)], { type: 'application/json' }));
      const link = document.createElement('a');
      link.href = url;
      link.download = 'scopehub-private-records.json';
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 0);
      setNotice('Private records backup download started.');
    });
  }

  function selectBackup(event: ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = '';
    if (!file || !workspaceId || !view) return;
    const revision = view.workspace_revision;
    setNotice('');
    run(async signal => {
      if (file.size > maxPrivateBackupBytes) throw { code: 'RESOURCE_LIMIT' };
      let backup: PrivateRecordBackupV1;
      try {
        backup = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(await file.arrayBuffer())) as PrivateRecordBackupV1;
      } catch {
        throw { code: 'INVALID_ARGUMENT' };
      }
      signal.throwIfAborted();
      const approval = await preparePrivateRecord(workspaceId, revision, { action: 'restore', backup }, signal);
      setPrepared({ ...approval, target: undefined });
    });
  }

  function finish(decision: 'approve' | 'decline', close = false) {
    if (!prepared) return;
    run(async signal => {
      if (decision === 'approve' && !retrying && prepared.scope.expected_workspace_revision !== observedRevision.current) {
        setPrepared(undefined);
        throw { code: 'STALE_CONTEXT' };
      }
      const replacesView = decision === 'approve' && prepared.action !== 'save';
      if (replacesView) setView(undefined);
      try {
        await resolvePrivateRecord(prepared, decision, signal);
      } catch (fault) {
        if (close && !signal.aborted) {
          workspace.reload();
          onClose();
          return;
        }
        const code = recordErrorCode(fault);
        if (!signal.aborted && ['STALE_CONTEXT', 'CONFIRMATION_REQUIRED', 'INVALID_ARGUMENT', 'DATA_UNAVAILABLE'].includes(String(code))) {
          setPrepared(undefined);
          setRetrying(false);
          await load(recordId, signal);
        } else if (!signal.aborted && decision === 'approve') {
          setRetrying(true);
        }
        throw fault;
      }
      setPrepared(undefined);
      setRetrying(false);
      if (close) { onClose(); return; }
      if (decision === 'decline') { setNotice('Action cancelled.'); return; }
      setDraft(undefined);
      const messages = { save: 'Private record saved.', delete: 'Private record deleted.', restore: 'Private records restored.' };
      setNotice(messages[prepared.action]);
      await load(prepared.action === 'save' ? prepared.recordId : null, signal);
      if (!signal.aborted) workspace.reload();
    });
  }

  function close() {
    if (busy) return;
    if (prepared) finish('decline', true);
    else onClose();
  }

  return <dialog ref={modal} className="symbol-dialog decision-dialog" aria-labelledby="private-records-heading"
    onCancel={event => { event.preventDefault(); close(); }}>
    <div className="dialog-heading">
      <h2 id="private-records-heading">Private records</h2>
      <button type="button" className="secondary-button" onClick={close} disabled={busy}>Close</button>
    </div>
    <div className="decision-body private-record-body" aria-busy={busy}>
      {(error || readError) && <p className="note-error" role="alert">{error || readError}</p>}
      {notice && <p role="status">{notice}</p>}
      {visibleReview ? <section className="note-approval" aria-label="Review private record change">
        <h3>{visibleReview.action === 'delete' ? 'Delete this private record?'
          : visibleReview.action === 'restore' ? 'Restore from this local backup?' : 'Save this private record locally?'}</h3>
        {visibleReview.target && <dl className="private-review-target">
          <dt>{kinds[visibleReview.target.kind]}</dt><dd>{visibleReview.target.title}</dd>
          {visibleReview.target.proposedTitle && <><dt>Corrected title</dt><dd>{visibleReview.target.proposedTitle}</dd></>}
        </dl>}
        {visibleReview.action === 'delete' && <p>ScopeHub will remove this record from the profile and remember the deletion during restore. Independent backup files may still contain it.</p>}
        {visibleReview.action === 'restore' && <p>
          {visibleReview.summary.added} to add, {visibleReview.summary.kept} current records to keep, {visibleReview.summary.removed} to remove,
          {' '}{visibleReview.summary.suppressed} suppressed by known deletions, {visibleReview.summary.imported_deletions} deletion markers to import.
          An independent older backup may contain records whose deletion history is unavailable.
        </p>}
        {retrying && <p>Retry confirmation to recover the result of this review.</p>}
        <div className="private-record-actions">
          {!retrying && <button type="button" ref={cancelReview} className="secondary-button" disabled={busy}
            onClick={() => finish('decline')}>Cancel review</button>}
          <button type="button" className="primary" disabled={busy} onClick={() => finish('approve')}>
            {retrying ? 'Retry confirmation' : { save: 'Save private record', delete: 'Delete private record', restore: 'Restore private records' }[visibleReview.action]}
          </button>
        </div>
      </section> : draft ? <RecordEditor draft={draft} current={changedRecord} disabled={busy || !view} onChange={setDraft} onCancel={() => setDraft(undefined)}
        onReview={event => {
          event.preventDefault();
          review({
            action: 'save', record_id: draft.id,
            expected_record_revision: changedRecord?.revision ?? draft.revision, content: draft.content,
          });
        }} />
        : record ? <>
          <button type="button" className="secondary-button" disabled={busy} onClick={() => { setNotice(''); run(signal => load(null, signal)); }}>All private records</button>
          <RecordDetail record={record} />
          <div className="private-record-actions">
            <button type="button" className="secondary-button" disabled={busy} onClick={() => {
              setNotice('');
              setDraft({ id: record.record_id, revision: record.revision, content: record.content });
            }}>Correct record</button>
            <button type="button" className="secondary-button" disabled={busy} onClick={() => review({
              action: 'delete', record_id: record.record_id, expected_record_revision: record.revision,
            })}>Review deletion</button>
          </div>
        </> : view && <>
          <button type="button" className="primary" disabled={busy} onClick={() => {
            setNotice('');
            setDraft({ id: crypto.randomUUID(), revision: null, content: emptyContent('conversation') });
          }}>New private record</button>
          {view.records.length ? <ul className="note-list">
            {view.records.map(item => <li key={item.record_id}>
              <button type="button" className="secondary-button" disabled={busy} onClick={() => {
                setNotice('');
                run(signal => load(item.record_id, signal));
              }}>{item.title} <small>{kinds[item.kind]}</small></button>
            </li>)}
          </ul> : <p>No private records saved.</p>}
          <div className="private-record-actions">
            <button type="button" className="secondary-button" disabled={busy} onClick={downloadBackup}>Download local backup</button>
            <label>Restore local backup<input type="file" accept=".json,application/json" disabled={busy} onChange={selectBackup} /></label>
          </div>
        </>}
    </div>
  </dialog>;
}
