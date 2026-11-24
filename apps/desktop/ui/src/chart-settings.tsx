import { useId, useLayoutEffect, useRef, useState } from 'react';
import { isTauri } from '@tauri-apps/api/core';
import { LocalApiSettings } from './local-api-settings';
import { t } from './i18n';
import { WorkspaceIcon } from './workspace-icons';
import type { LocalSourceActions } from './workspace-panels';

type Page = 'settings' | 'sources' | 'personal' | 'api';

export function ChartSettings({ localActions }: { localActions: LocalSourceActions | undefined }) {
  const trigger = useRef<HTMLButtonElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const next = useRef<HTMLButtonElement>(null);
  const apiEntry = useRef<HTMLButtonElement>(null);
  const returningFromApi = useRef(false);
  const closeButton = useRef<HTMLButtonElement>(null);
  const heading = useId();
  const [page, setPage] = useState<Page>('settings');
  const choosing = localActions?.choosing ?? false;

  function focusPage() {
    const target = returningFromApi.current ? apiEntry.current : next.current;
    returningFromApi.current = false;
    (target ?? closeButton.current)?.focus();
  }

  useLayoutEffect(() => {
    if (dialog.current?.open) focusPage();
  }, [page]);

  const close = () => dialog.current?.close();

  async function choose() {
    if (!localActions || choosing || !trigger.current) return;
    if (await localActions.choose(trigger.current)) close();
    else next.current?.focus();
  }

  return <>
    <button type="button" ref={trigger} className="icon-button" aria-haspopup="dialog"
      aria-label={t('Settings')} title={t('Settings')} onClick={() => {
        dialog.current?.showModal();
        focusPage();
      }}>
      <WorkspaceIcon name="settings" />
    </button>
    <dialog ref={dialog} className="symbol-dialog chart-settings" aria-labelledby={heading}
      onCancel={event => { if (choosing) event.preventDefault(); }}
      onClose={() => setPage('settings')}>
      <div className="dialog-heading">
        <h2 id={heading}>{t('Settings')}</h2>
        <button type="button" ref={closeButton} className="icon-button" aria-label={t('Close')} disabled={choosing} onClick={close}>
          <WorkspaceIcon name="close" />
        </button>
      </div>
      <div className="chart-settings-body">
        {page !== 'settings' && <button type="button" className="secondary-button" disabled={choosing}
          onClick={() => {
            returningFromApi.current = page === 'api';
            setPage(page === 'api' ? 'personal' : page === 'personal' ? 'sources' : 'settings');
          }}>
          {t(page === 'api' ? 'Back to my data sources' : page === 'personal' ? 'Back to data sources' : 'Back to settings')}
        </button>}
        {page === 'settings' && <button type="button" ref={next} className="secondary-button"
          onClick={() => setPage('sources')}>{t('Data sources')}</button>}
        {page === 'sources' && <>
          <h3>{t('Data sources')}</h3>
          <button type="button" ref={next} className="secondary-button" onClick={() => setPage('personal')}>{t('My data sources')}</button>
        </>}
        {page === 'personal' && <>
          <h3>{t('My data sources')}</h3>
          <h4>{t('Local CSV folder')}</h4>
          {localActions ? <>
            {localActions.folderName !== undefined && <strong className="csv-folder-name">{localActions.folderName}</strong>}
            <p role="status">{choosing ? t('Waiting for folder selection…') : localActions.resume
              ? t('Folder available in this session') : t('No CSV folder selected')}</p>
            <div className="local-source-actions">
              <button type="button" ref={next} className="secondary-button" aria-disabled={choosing} onClick={() => void choose()}>
                {t('Choose local CSV folder')}
              </button>
              {localActions.resume && <button type="button" className="secondary-button" disabled={choosing} onClick={() => {
                if (!trigger.current) return;
                close();
                localActions.resume?.(trigger.current);
              }}>{t('Resume CSV review')}</button>}
              {localActions.error && <p role="alert">{localActions.error}</p>}
            </div>
          </> : <p>{t('Local CSV selection is available in the desktop app.')}</p>}
          {isTauri() && <button type="button" ref={apiEntry} className="secondary-button" disabled={choosing}
            onClick={() => setPage('api')}>{t('Local API connection')}</button>}
        </>}
        {page === 'api' && <LocalApiSettings />}
      </div>
    </dialog>
  </>;
}
