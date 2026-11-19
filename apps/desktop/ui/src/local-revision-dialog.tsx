import { useEffect, useId, useRef, useState } from 'react';
import type { ReadLocalImportRevisionOutput } from '../../../../contracts/generated/protocol';
import { readLocalImportRevision, revokeLocalImportRevision } from './local-import-transport';
import { FrozenLocalChart } from './local-preview-chart';

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

function failureMessage(error: unknown, fallback: string) {
  if (!error || typeof error !== 'object') return fallback;
  if ('code' in error && error.code === 'SNAPSHOT_UNAVAILABLE') return 'This frozen chart is unavailable in the current profile.';
  if ('code' in error && error.code === 'PERMISSION_DENIED') return 'This profile does not have permission to display this revision.';
  return 'message' in error && typeof error.message === 'string' ? error.message : fallback;
}

export function OpenLocalRevisionDialog({ onClose, onRevoked }: { onClose: () => void; onRevoked: (revisionId: string) => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const active = useRef(false);
  const heading = useId();
  const [draft, setDraft] = useState('');
  const [frozen, setFrozen] = useState<ReadLocalImportRevisionOutput>();
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string>();
  const [notice, setNotice] = useState<string>();
  const revisionId = draft.trim().toLowerCase();
  const validId = /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i.test(revisionId);

  useEffect(() => {
    const node = dialog.current;
    node?.showModal();
    return () => { node?.close(); };
  }, []);

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

  async function open() {
    if (!validId || active.current) return;
    setFrozen(undefined);
    await request(async () => {
      const reply = await readLocalImportRevision({ version: 1, revision_id: revisionId }, crypto.randomUUID());
      if (reply.revision.revision_id !== revisionId || reply.bars.length !== reply.revision.bar_count) {
        throw new Error('The frozen chart reply could not be verified.');
      }
      setFrozen(reply);
    }, 'Unable to open the frozen chart.');
  }

  async function revoke() {
    if (!frozen) return;
    await request(async () => {
      const revision_id = frozen.revision.revision_id;
      const reply = await revokeLocalImportRevision({ version: 1, revision_id }, crypto.randomUUID());
      if (reply.revision_id !== revision_id) throw new Error('The access removal reply could not be verified.');
      setFrozen(undefined);
      setNotice('Access removed for this published revision.');
      onRevoked(revision_id);
    }, 'Unable to remove access to this revision.');
  }

  return <dialog ref={dialog} className="symbol-dialog local-revision-dialog" aria-labelledby={heading} onCancel={onClose}>
    <div className="dialog-heading local-revision-heading">
      <h2 id={heading}>Open published CSV</h2>
      <button type="button" className="secondary-button" onClick={onClose}>Close</button>
    </div>
    <div className="local-revision-body">
      <form onSubmit={event => { event.preventDefault(); void open(); }}>
        <fieldset className="local-import-card" disabled={busy}>
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
      {busy && <p role="status">Working with the frozen revision…</p>}
      {failure && <p className="local-import-error" role="alert">{failure}</p>}
      {notice && <p role="status">{notice}</p>}
      {frozen && <>
        <FrozenLocalChart key={frozen.revision.revision_id} result={frozen} />
        <div className="local-import-card">
          <p>Source file · {frozen.revision.relative_path}</p>
          <RevisionAccessActions key={frozen.revision.revision_id} disabled={busy} onRevoke={() => void revoke()} />
        </div>
      </>}
    </div>
  </dialog>;
}
