import { useRef, type FormEvent } from 'react';
import { t } from './i18n';
import type { ModelRoute, OpenAiCredential } from './model-chat-transport';

export function providerName(kind: ModelRoute['kind']) {
  switch (kind) {
    case 'ollama': return 'Ollama';
    case 'openai_responses': return 'OpenAI Responses';
    case 'offline_mock': return t('Offline mock');
  }
}

export function RouteDisclosure({ route }: { route: ModelRoute }) {
  const charges = route.charge_basis === 'openai_api_account'
    ? t('OpenAI meters use under the API account or project associated with your key. Check your account’s pricing before enabling.')
    : route.charge_basis === 'endpoint_operator' ? t('Set by the server operator. Check its fees before enabling.') : t('None');
  return <>
    <dl className="model-route">
      <dt>{t('Provider')}</dt><dd>{providerName(route.kind)}</dd>
      <dt>{t('Destination')}</dt><dd><bdi>{route.destination}</bdi></dd>
      <dt>{t('Model')}</dt><dd><bdi>{route.model}</bdi></dd>
      <dt>{t('Scope')}</dt><dd>{t('Typed questions and up to four earlier question and answer pairs in this session.')}{' '}
        {route.scope === 'conversation_text_and_sample_closes'
          ? t('The model can request close prices and times from the selected interval of the offline sample. CSV data and saved private records stay local.')
          : t('Chart data and saved private records stay local.')}</dd>
      <dt>{t('Charges')}</dt><dd>{charges}</dd>
    </dl>
    <p className="model-muted">{t('This connection lasts for this app session. Enable it again after restarting ScopeHub.')}</p>
    {route.kind === 'ollama' && <p className="model-muted">{t('The server operator may process or forward requests, even at a loopback address. Disconnect in AI settings to stop future sends.')}</p>}
    {route.kind === 'openai_responses' && <p className="model-muted">{t('ScopeHub asks OpenAI to disable response storage. Disconnect in AI settings to stop future sends.')}</p>}
  </>;
}

export function CloudCredentialSettings({ credential, working, reload, replace, remove }: {
  credential: OpenAiCredential | undefined;
  working: boolean;
  reload: () => void;
  replace: (secret: string) => void;
  remove: () => void;
}) {
  const field = useRef<HTMLInputElement>(null);

  function submit(event: FormEvent) {
    event.preventDefault();
    const input = field.current;
    if (!input) return;
    const secret = input.value.trim();
    input.value = '';
    if (secret) replace(secret);
  }

  return <form className="model-card model-form" onSubmit={submit} autoComplete="off" aria-label={t('OpenAI credential')}>
    {credential ? <>
      <p className="model-muted">
        {credential.lifetime === 'windows_credential_manager'
          ? t('Windows Credential Manager saves this key for this profile until you remove or replace it.')
          : t('This key is available only for this app session. Enter it again after restarting ScopeHub.')}
      </p>
      <p role="status">{credential.configured ? t('API key configured.') : t('No OpenAI API key configured.')}</p>
      <label>{t('OpenAI API key')}<input ref={field} type="password" autoComplete="off" spellCheck={false}
        maxLength={2048} required disabled={working} /></label>
      <div className="model-actions">
        <button type="submit" className="secondary-button" disabled={working}>
          {credential.configured ? t('Replace API key') : t('Save API key')}
        </button>
        {credential.configured && <button type="button" className="secondary-button" disabled={working} onClick={() => {
          if (field.current) field.current.value = '';
          remove();
        }}>{t('Remove API key')}</button>}
      </div>
    </> : working ? <p role="status">{t('Checking OpenAI key status…')}</p>
      : <button type="button" className="secondary-button" onClick={reload}>{t('Check OpenAI key status')}</button>}
  </form>;
}
