import { useEffect, useRef, useState } from 'react';
import { t } from './i18n';
import { assetLabel, instrumentName, instrumentSearchTerms, symbol } from './instrument-display';
import type { Instrument } from './market';
import { WorkspaceIcon } from './workspace-icons';

type Props = {
  instruments: Instrument[]; initialQuery: string; sample: boolean;
  onChoose: (instrument: Instrument) => void; onClose: () => void; openSample: () => void;
};

export function SymbolSearch({ instruments, initialQuery, sample, onChoose, onClose, openSample }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState(initialQuery);
  const [active, setActive] = useState(0);
  const term = query.trim().toLowerCase();
  const matches = instruments.filter(item =>
    instrumentSearchTerms(item).some(value => value.toLowerCase().includes(term)));
  useEffect(() => {
    const node = dialog.current;
    if (!node) return;
    node.showModal();
    input.current?.focus();
    return () => node.close();
  }, []);
  const current = Math.min(active, matches.length - 1);
  return <dialog ref={dialog} className="symbol-dialog" aria-labelledby="search-title"
    onCancel={event => { event.preventDefault(); onClose(); }}
    onClick={event => {
      if (event.target !== dialog.current) return;
      const bounds = dialog.current.getBoundingClientRect();
      if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) onClose();
    }}>
    <div className="dialog-heading"><h2 id="search-title">{t('Search symbols')}</h2>
      <button type="button" className="icon-button" aria-label={t('Close search')} onClick={onClose}><WorkspaceIcon name="close" /></button>
    </div>
    <div className="search-field"><WorkspaceIcon name="search" />
      <input ref={input} role="combobox" aria-label={t('Search symbols')} aria-controls="symbol-options" aria-expanded="true"
        aria-autocomplete="list" aria-activedescendant={current >= 0 ? 'symbol-option-' + current : undefined}
        placeholder={t('Symbol or name')} value={query} autoComplete="off" spellCheck={false}
        onChange={event => { setQuery(event.target.value); setActive(0); }}
        onKeyDown={event => {
          if (event.nativeEvent.isComposing || !matches.length) return;
          if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            setActive((current + (event.key === 'ArrowDown' ? 1 : -1) + matches.length) % matches.length);
          } else if (event.key === 'Enter' && matches[current]) {
            event.preventDefault();
            onChoose(matches[current]);
          }
        }} />
      <kbd>Esc</kbd>
    </div>
    <div role="listbox" id="symbol-options" aria-label={t('Matching instruments')}>
      {matches.map((item, index) => <button type="button" role="option" tabIndex={-1} key={item.id}
        id={'symbol-option-' + index} aria-selected={current === index} className="search-option"
        onPointerMove={() => setActive(index)} onClick={() => onChoose(item)}>
        <span><strong>{symbol(item)}</strong><small>{instrumentName(item)}</small></span>
        <span className="search-market">{assetLabel(item)} · {item.currency}</span>
      </button>)}
    </div>
    {!matches.length && (sample ? <p className="dialog-message" role="status">{t('No matching instruments.')}</p>
      : <div className="dialog-message"><p>{t('No data source is connected.')}</p>
        <button type="button" className="primary" onClick={openSample}>{t('Open offline sample')}</button></div>)}
  </dialog>;
}
