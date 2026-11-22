import { useId, useRef } from 'react';
import { languages, setLocalePreference, t, useLocalePreference } from './i18n';

export function LanguageSettings() {
  const dialog = useRef<HTMLDialogElement>(null);
  const heading = useId();
  const preference = useLocalePreference();
  return <>
    <button type="button" className="icon-button language-trigger" aria-haspopup="dialog"
      aria-label={t('Language settings')} title={t('Language settings')} onClick={() => dialog.current?.showModal()}>
      <svg className="workspace-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
        <circle cx="12" cy="12" r="9" /><ellipse cx="12" cy="12" rx="4" ry="9" /><path d="M3 12h18" />
      </svg>
    </button>
    <dialog ref={dialog} className="symbol-dialog language-dialog" aria-labelledby={heading}>
      <div className="language-heading">
        <h2 id={heading}>{t('Language settings')}</h2>
        <button type="button" className="icon-button" aria-label={t('Close')} onClick={() => dialog.current?.close()}>×</button>
      </div>
      <label>{t('Interface language')}
        <select value={preference} onChange={event => setLocalePreference(event.currentTarget.value)}>
          <option value="system">{t('Follow system language')}</option>
          {languages.map(language => <option key={language.tag} value={language.tag} lang={language.tag}>{language.name}</option>)}
        </select>
      </label>
    </dialog>
  </>;
}
