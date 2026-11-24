import { invoke } from '@tauri-apps/api/core';
import { useEffect, useId, useRef, useState } from 'react';
import type {
  LocalApiConnectionInput, LocalApiConnectionOutput, LocalApiSample, SavedLocalApiConnection,
} from '../../../../contracts/generated/protocol';
import { formatNumber, t } from './i18n';

const operation = 'market.localApiConnection';

function exchange(payload: LocalApiConnectionInput) {
  return invoke<LocalApiConnectionOutput>('execute_operation', { operation, payload });
}

function errorCode(error: unknown) {
  return error !== null && typeof error === 'object' && 'code' in error ? error.code : undefined;
}

export function LocalApiSettings() {
  const lifetime = useRef<AbortController | null>(null);
  const submitting = useRef(false);
  const heading = useId();
  const [connection, setConnection] = useState<SavedLocalApiConnection | null>();
  const [name, setName] = useState('');
  const [origin, setOrigin] = useState('');
  const [busy, setBusy] = useState(true);
  const [failure, setFailure] = useState<string>();
  const [saved, setSaved] = useState(false);
  const [sample, setSample] = useState<LocalApiSample>();

  function showConnection(value: SavedLocalApiConnection | null) {
    setConnection(value);
    setName(value?.name ?? '');
    setOrigin(value?.origin ?? '');
  }

  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    const { signal } = controller;
    void exchange({ action: 'status' }).then(reply => {
      if (signal.aborted) return;
      if (reply.action !== 'status') throw new Error();
      showConnection(reply.connection);
    }).catch(() => {
      if (!signal.aborted) setFailure('The saved local API connection could not be loaded.');
    }).finally(() => {
      if (!signal.aborted) setBusy(false);
    });
    return () => controller.abort();
  }, []);

  async function submit(request: Exclude<LocalApiConnectionInput, { action: 'status' }>) {
    const signal = lifetime.current?.signal;
    if (!signal || signal.aborted || submitting.current) return;
    submitting.current = true;
    setBusy(true);
    setSample(undefined);
    setFailure(undefined);
    setSaved(false);
    try {
      const reply = await exchange(request);
      if (signal.aborted) return;
      if (reply.action === 'save' && request.action === 'save') {
        showConnection(reply.connection);
        setSaved(true);
      } else if (reply.action === 'trial' && request.action === 'trial' && reply.connection.id === request.id) {
        setSample(reply.sample);
      } else {
        throw new Error();
      }
    } catch (error) {
      if (signal.aborted) return;
      const code = errorCode(error);
      if (code === 'STALE_CONTEXT') setFailure('The saved connection changed. Reopen the form to load it.');
      else if (code === 'BUSY') setFailure('The workspace is busy. Try again.');
      else if (request.action === 'trial') {
        const message = code === 'TIMEOUT' ? 'The local API did not respond within the trial limit.'
          : code === 'PERMISSION_DENIED' ? 'The local API refused access.' : 'The trial did not return a usable market sample.';
        setFailure(message);
      } else setFailure(code === 'INVALID_ARGUMENT' ? 'Enter a name and a loopback origin such as http://127.0.0.1:8080.'
        : 'The local API connection could not be saved.');
    } finally {
      submitting.current = false;
      if (!signal.aborted) setBusy(false);
    }
  }

  const originDraft = origin.trim();
  const nameDraft = name.trim();
  const reviewed = connection && connection.name === nameDraft && connection.origin === originDraft;
  return <>
    <h3>{t('Local API connection')}</h3>
    <form onSubmit={event => {
      event.preventDefault();
      void submit({ action: 'save', expected_id: connection?.id ?? null, name: nameDraft, origin: originDraft });
    }}>
      <fieldset className="local-import-card" disabled={busy || connection === undefined}>
        <legend>{t('Review local read rule')}</legend>
        <div className="local-import-fields">
          <label>{t('Connection name')}<input value={name} maxLength={64}
            onChange={event => { setName(event.target.value); setSample(undefined); setSaved(false); }} /></label>
          <label>{t('Local API origin')}<input value={origin} maxLength={100}
            placeholder="http://127.0.0.1:8080" autoComplete="off" spellCheck={false}
            onChange={event => { setOrigin(event.target.value); setSample(undefined); setSaved(false); }} /></label>
        </div>
        <p>{t('One read of /market-sample on this loopback origin. No authentication.')}</p>
        {reviewed && <code>{'GET ' + connection.origin + '/market-sample'}</code>}
        <div className="local-source-actions">
          <button type="submit" className="secondary-button" disabled={!(nameDraft && originDraft)}>{t('Save connection')}</button>
          <button type="button" className="secondary-button" disabled={!reviewed}
            onClick={() => reviewed && void submit({ action: 'trial', id: connection.id })}>{t('Test read')}</button>
        </div>
      </fieldset>
    </form>
    {busy && <p role="status">{t('Working with the local connection…')}</p>}
    {connection === null && !busy && <p role="status">{t('No local API connection saved.')}</p>}
    {saved && <p role="status">{t('Saved connection.')}</p>}
    {failure && <p role="alert">{t(failure)}</p>}
    {sample && <section className="local-import-card" aria-labelledby={heading}>
      <h4 id={heading}>{t('Verified sample')}</h4>
      <p>{sample.instrument} · {formatNumber(sample.value, { maximumSignificantDigits: 17 })} {sample.unit}</p>
      <time dateTime={new Date(sample.observed_at_ms).toISOString()}>{new Date(sample.observed_at_ms).toISOString()}</time>
    </section>}
  </>;
}
