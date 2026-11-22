import { useCallback, useEffect, useRef, useState, type ChangeEvent, type FormEvent } from 'react';
import type {
  PrivateRecordBackupV1, PrivateRecordContent, PrivateRecordEntry, PrivateRecordKind,
  PrivateRecordReadOutput, PrivateRecordReceipt11, PrivateRecordTurn,
} from '../../../../contracts/generated/protocol';
import { formatNumber, message, t, useLocale, type Message } from './i18n';
import {
  exportPrivateRecords, maxPrivateBackupBytes, preparePrivateRecord, readPrivateRecords, recordErrorCode,
  resolvePrivateRecord, type PrepareRecord, type RecordApproval, type RecordResolution,
} from './private-record-transport';
import type { LocalWorkspace } from './use-local-workspace';

const kinds = {
  conversation: message('Conversation exchange'),
  memory: message('Long-term memory'),
  strategy: message('Strategy'),
};
const speakerLabels = { user: message('You'), assistant: message('Assistant') };

type Draft = { id: string; revision: number | null; content: PrivateRecordContent };
type ReviewTarget = { title: string; kind: PrivateRecordKind; proposedTitle?: string | undefined };
type PreparedReview = RecordApproval & { target: ReviewTarget | undefined };
type PendingResolution = RecordResolution & { action: RecordApproval['action'] };

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
    <h3>{revision === null ? t('New private record') : t('Correct private record')}</h3>
    {current && <section className="note-approval" aria-label={t('Current saved version')}>
      <p role="alert">{t('Another window changed this record. Compare the current saved version with your draft before reviewing the correction.')}</p>
      <RecordDetail record={current} />
    </section>}
    <fieldset disabled={disabled}>
      {revision === null && <label>{t('Record type')}
        <select value={content.kind} onChange={event => change(emptyContent(event.target.value as PrivateRecordKind, content.title))}>
          {Object.entries(kinds).map(([kind, label]) => <option key={kind} value={kind}>{t(label)}</option>)}
        </select>
      </label>}
      <label>{t('Title')}<input value={content.title} onChange={event => change({ ...content, title: event.target.value })}
        maxLength={120} autoComplete="off" required /></label>
      {content.kind === 'conversation' ? <>
        <p>{t('Enter the typed turns you want to keep, in their original order.')}</p>
        {content.turns.map((turn, index) => <fieldset key={index} className="private-turn">
          <legend>{t('Turn {number}', { number: formatNumber(index + 1) })}</legend>
          <label>{t('Speaker')}
            <select value={turn.speaker} onChange={event => change({
              ...content, turns: updateTurn(content.turns, index, { ...turn, speaker: event.target.value as PrivateRecordTurn['speaker'] }),
            })}>
              <option value="user">{t(speakerLabels.user)}</option><option value="assistant">{t(speakerLabels.assistant)}</option>
            </select>
          </label>
          <label>{t('Text')}<textarea value={turn.text} onChange={event => change({
            ...content, turns: updateTurn(content.turns, index, { ...turn, text: event.target.value }),
          })} rows={3} maxLength={4096} spellCheck={false} required /></label>
          {content.turns.length > 2 && <button type="button" className="secondary-button"
            onClick={() => change({ ...content, turns: content.turns.filter((_, item) => item !== index) })}>
            {t('Remove turn {number}', { number: formatNumber(index + 1) })}
          </button>}
        </fieldset>)}
        <button type="button" className="secondary-button" disabled={content.turns.length === 16}
          onClick={() => change({
            ...content, turns: [...content.turns, { speaker: content.turns.at(-1)?.speaker === 'user' ? 'assistant' : 'user', text: '' }],
          })}>{t('Add turn')}</button>
      </> : <label>{content.kind === 'memory' ? t('Memory') : t(kinds.strategy)}
        <textarea value={content.text} onChange={event => change({ ...content, text: event.target.value })}
          rows={6} maxLength={4096} spellCheck={false} required />
      </label>}
      <div className="private-record-actions">
        <button type="submit" className="primary">{current ? t('Review correction against current version') : t('Review save')}</button>
        <button type="button" className="secondary-button" onClick={onCancel}>{t('Discard draft')}</button>
      </div>
    </fieldset>
  </form>;
}

function RecordDetail({ record }: { record: PrivateRecordEntry }) {
  const { content } = record;
  return <article className="private-record-detail">
    <h3>{content.title}</h3>
    <small>{t(kinds[content.kind])}</small>
    {content.kind === 'conversation' ? <ol>
      {content.turns.map((turn, index) => <li key={index}>
        <strong>{t(speakerLabels[turn.speaker])}</strong><p>{turn.text}</p>
      </li>)}
    </ol> : <p>{content.text}</p>}
  </article>;
}

function failureMessage(error: unknown) {
  switch (recordErrorCode(error)) {
    case 'STALE_CONTEXT': return message('The workspace or record changed. Review the current version before retrying.');
    case 'CONFIRMATION_REQUIRED': return message('The review expired. Review the action again.');
    case 'INVALID_ARGUMENT': return message('Check the record fields or select a valid private records backup.');
    case 'DATA_UNAVAILABLE': return message('This private record is no longer available.');
    case 'BUSY': return message('The local workspace is busy. Try again.');
    case 'WORKER_EXITED': return message('The local workspace is unavailable. Private records are hidden.');
    case 'PERMISSION_DENIED': return message('This action or backup is unavailable in this local profile.');
    case 'RESOURCE_LIMIT': return message('This action exceeds a local record or backup limit.');
    default: return message('The local workspace could not complete the action.');
  }
}

export function PrivateRecords({ workspace, onClose }: { workspace: LocalWorkspace; onClose: () => void }) {
  useLocale();
  const modal = useRef<HTMLDialogElement>(null);
  const cancelReview = useRef<HTMLButtonElement>(null);
  const active = useRef<AbortController | null>(null);
  const probe = useRef<{ controller: AbortController; task: Promise<void> } | null>(null);
  const selection = useRef<string | null>(null);
  const projectionEpoch = useRef(0);
  const observedRevision = useRef<number | undefined>(undefined);
  const pendingResolution = useRef<PendingResolution | undefined>(undefined);
  const [view, setView] = useState<PrivateRecordReadOutput>();
  const [draft, setDraft] = useState<Draft>();
  const [prepared, setPrepared] = useState<PreparedReview>();
  const [busy, setBusy] = useState(false);
  const [recovery, setRecovery] = useState<PendingResolution>();
  const [recoveredOperationId, setRecoveredOperationId] = useState<string>();
  const [notice, setNotice] = useState<Message>();
  const [error, setError] = useState<Message>();
  const [readError, setReadError] = useState<Message>();
  const workspaceId = workspace.snapshot?.workspace.id;
  const record = view?.record;
  const changedRecord = record && draft?.id === record.record_id && draft.revision !== record.revision ? record : undefined;
  const expiredReview = prepared && view
    && prepared.scope.expected_workspace_revision !== view.workspace_revision;
  const visibleReview = expiredReview ? undefined : prepared;
  const visibleError = error ?? readError;

  const rememberRecovery = useCallback((resolution: PendingResolution | undefined) => {
    pendingResolution.current = resolution;
    setRecovery(resolution);
  }, []);

  const discardPrivateProjection = useCallback((missingRecord?: string) => {
    projectionEpoch.current += 1;
    observedRevision.current = undefined;
    setView(undefined);
    setDraft(undefined);
    setPrepared(undefined);
    // A verified missing detail can be the effect of our submitted deletion.
    // Keep only the original resolve inputs in that case.
    const pending = pendingResolution.current;
    if (pending?.action !== 'delete' || !missingRecord || pending.recordId !== missingRecord) {
      rememberRecovery(undefined);
    }
    setRecoveredOperationId(undefined);
    setNotice(undefined);
    setError(undefined);
  }, [rememberRecovery]);

  const run = useCallback((
    work: (signal: AbortSignal) => Promise<void>, background = false, retry?: PendingResolution,
  ) => {
    if (active.current || background && probe.current) return;
    const controller = new AbortController();
    const epoch = projectionEpoch.current;
    if (!background) {
      active.current = controller;
      setBusy(true);
      setError(undefined);
    }
    const waiting = probe.current?.task;
    const task = (async () => {
      if (!background) await waiting;
      controller.signal.throwIfAborted();
      // A queued result retry can survive the original detail being removed.
      // Loss of authority erases its exact pending resolution.
      if (retry ? pendingResolution.current === retry : epoch === projectionEpoch.current) {
        await work(controller.signal);
      }
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

  const load = useCallback(async function load(id: string | null, signal: AbortSignal): Promise<void> {
    if (!workspaceId) return;
    selection.current = id;
    try {
      const current = await readPrivateRecords(workspaceId, id, signal);
      observedRevision.current = current.workspace_revision;
      setView(current);
      setReadError(undefined);
    } catch (fault) {
      if (signal.aborted) return;
      const code = recordErrorCode(fault);
      if (code === 'BUSY') throw fault;
      discardPrivateProjection(code === 'DATA_UNAVAILABLE' ? id ?? undefined : undefined);
      if (id && code === 'DATA_UNAVAILABLE') {
        setError(failureMessage(fault));
        await load(null, signal);
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
    setError(message('The workspace changed. Review this change again.'));
  }, [expiredReview, prepared]);

  const review = (args: PrepareRecord) => {
    if (!workspaceId || !view) return;
    const saved = record?.content;
    const target: ReviewTarget | undefined = args.action === 'save' ? {
      title: saved?.title ?? args.content.title,
      kind: args.content.kind,
      proposedTitle: saved && saved.title !== args.content.title ? args.content.title : undefined,
    } : args.action === 'delete' && saved ? { title: saved.title, kind: saved.kind } : undefined;
    setNotice(undefined);
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
      setNotice(message('Private records backup download started.'));
    });
  }

  function selectBackup(event: ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = '';
    if (!file || !workspaceId || !view) return;
    const revision = view.workspace_revision;
    setNotice(undefined);
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
    const retry = decision === 'approve' ? recovery : undefined;
    const approval = retry ?? prepared;
    if (!approval) return;
    const { action, scope, challenge, recordId: targetId } = approval;
    run(async signal => {
      if (decision === 'approve' && !retry && scope.expected_workspace_revision !== observedRevision.current) {
        setPrepared(undefined);
        throw { code: 'STALE_CONTEXT' };
      }
      if (decision === 'approve' && action !== 'save') {
        setPrepared(undefined);
        setView(undefined);
      }
      let receipt: PrivateRecordReceipt11 | undefined;
      try {
        receipt = await resolvePrivateRecord(approval, decision, signal);
      } catch (fault) {
        if (signal.aborted) return;
        if (close) {
          workspace.reload();
          onClose();
          return;
        }
        const code = recordErrorCode(fault);
        if (['STALE_CONTEXT', 'CONFIRMATION_REQUIRED', 'INVALID_ARGUMENT', 'DATA_UNAVAILABLE'].includes(String(code))) {
          setPrepared(undefined);
          rememberRecovery(undefined);
          await load(selection.current, signal);
        } else if (decision === 'approve') {
          setPrepared(undefined);
          rememberRecovery({ action, scope, challenge, recordId: targetId });
        }
        throw fault;
      }
      setPrepared(undefined);
      rememberRecovery(undefined);
      if (close) { onClose(); return; }
      if (decision === 'decline') { setNotice(message('Action cancelled.')); return; }
      setDraft(undefined);
      const messages = {
        save: message('Private record saved.'),
        delete: message('Private record deleted.'),
        restore: message('Private records restored.'),
      };
      setNotice(messages[action]);
      setRecoveredOperationId(retry && action === 'delete' ? receipt?.operation_id : undefined);
      await load(action === 'save' ? targetId : null, signal);
      if (!signal.aborted) workspace.reload();
    }, false, retry);
  }

  function close() {
    if (busy) return;
    if (prepared) finish('decline', true);
    else onClose();
  }

  const approvalLabels = {
    save: t('Save private record'),
    delete: t('Delete private record'),
    restore: t('Restore private records'),
  };

  return <dialog ref={modal} className="symbol-dialog decision-dialog" aria-labelledby="private-records-heading"
    onCancel={event => { event.preventDefault(); close(); }}>
    <div className="dialog-heading">
      <h2 id="private-records-heading">{t('Private records')}</h2>
      <button type="button" className="secondary-button" onClick={close} disabled={busy}>{t('Close')}</button>
    </div>
    <div className="decision-body private-record-body" aria-busy={busy}>
      {visibleError && <p className="note-error" role="alert">{t(visibleError)}</p>}
      {notice && <p role="status">{t(notice)}</p>}
      {recoveredOperationId && <p>{t('Deletion receipt')} <code>{recoveredOperationId}</code></p>}
      {recovery ? <section className="note-approval" aria-label={t('Recover private record result')}>
        <h3>{recovery.action === 'delete' ? t('Recover deletion result') : t('Recover private record result')}</h3>
        <p>{t('Retry confirmation to recover the result of the submitted change.')}</p>
        <div className="private-record-actions">
          <button type="button" className="primary" disabled={busy} onClick={() => finish('approve')}>{t('Retry confirmation')}</button>
        </div>
      </section> : visibleReview ? <section className="note-approval" aria-label={t('Review private record change')}>
        <h3>{visibleReview.action === 'delete' ? t('Delete this private record?')
          : visibleReview.action === 'restore' ? t('Restore from this local backup?') : t('Save this private record locally?')}</h3>
        {visibleReview.target && <dl className="private-review-target">
          <dt>{t(kinds[visibleReview.target.kind])}</dt><dd>{visibleReview.target.title}</dd>
          {visibleReview.target.proposedTitle && <><dt>{t('Corrected title')}</dt><dd>{visibleReview.target.proposedTitle}</dd></>}
        </dl>}
        {visibleReview.action === 'delete' && <p>{t('ScopeHub will remove this record from the profile and remember the deletion during restore. Independent backup files may still contain it.')}</p>}
        {visibleReview.action === 'restore' && <p>
          {t('{added} to add, {kept} current records to keep, {removed} to remove, {suppressed} suppressed by known deletions, {imported} deletion markers to import.', {
            added: formatNumber(visibleReview.summary.added),
            kept: formatNumber(visibleReview.summary.kept),
            removed: formatNumber(visibleReview.summary.removed),
            suppressed: formatNumber(visibleReview.summary.suppressed),
            imported: formatNumber(visibleReview.summary.imported_deletions),
          })}{' '}{t('An independent older backup may contain records whose deletion history is unavailable.')}
        </p>}
        <div className="private-record-actions">
          <button type="button" ref={cancelReview} className="secondary-button" disabled={busy}
            onClick={() => finish('decline')}>{t('Cancel review')}</button>
          <button type="button" className="primary" disabled={busy} onClick={() => finish('approve')}>
            {approvalLabels[visibleReview.action]}
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
          <button type="button" className="secondary-button" disabled={busy}
            onClick={() => { setNotice(undefined); run(signal => load(null, signal)); }}>{t('All private records')}</button>
          <RecordDetail record={record} />
          <div className="private-record-actions">
            <button type="button" className="secondary-button" disabled={busy} onClick={() => {
              setNotice(undefined);
              setDraft({ id: record.record_id, revision: record.revision, content: record.content });
            }}>{t('Correct record')}</button>
            <button type="button" className="secondary-button" disabled={busy} onClick={() => review({
              action: 'delete', record_id: record.record_id, expected_record_revision: record.revision,
            })}>{t('Review deletion')}</button>
          </div>
        </> : view && <>
          <button type="button" className="primary" disabled={busy} onClick={() => {
            setNotice(undefined);
            setDraft({ id: crypto.randomUUID(), revision: null, content: emptyContent('conversation') });
          }}>{t('New private record')}</button>
          {view.records.length ? <ul className="note-list">
            {view.records.map(item => <li key={item.record_id}>
              <button type="button" className="secondary-button" disabled={busy} onClick={() => {
                setNotice(undefined);
                run(signal => load(item.record_id, signal));
              }}>{item.title} <small>{t(kinds[item.kind])}</small></button>
            </li>)}
          </ul> : <p>{t('No private records saved.')}</p>}
          <div className="private-record-actions">
            <button type="button" className="secondary-button" disabled={busy} onClick={downloadBackup}>{t('Download local backup')}</button>
            <label>{t('Restore local backup')}<input type="file" accept=".json,application/json" disabled={busy} onChange={selectBackup} /></label>
          </div>
        </>}
    </div>
  </dialog>;
}
