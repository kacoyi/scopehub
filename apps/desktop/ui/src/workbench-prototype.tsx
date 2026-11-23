import { useEffect, useRef, useState } from 'react';
import fixture from './workbench-synthetic-closes.json';
import { formatDateTime, formatNumber, message, t, type Message } from './i18n';
import { LanguageSettings } from './language-settings';
import './workbench-prototype.css';

type Block = { kind: 'threshold-tiles'; threshold: number; aboveLabel?: string; belowLabel?: string } | { kind: 'value-table' };
type Composition = { version: 1; blocks: Block[] };
const blank: Composition = { version: 1, blocks: [] };
const maxBlocks = 12;
const maxLength = 4096;
const encode = (composition: Composition) => JSON.stringify(composition, null, 2);

function objectWithKeys(value: unknown, required: string[], optional: string[] = []): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    && required.every(key => Object.hasOwn(value, key))
    && Object.keys(value).every(key => required.includes(key) || optional.includes(key));
}

function validLabel(value: unknown): value is string | undefined {
  return value === undefined || (typeof value === 'string' && value.trim().length > 0 && value.length <= 48);
}

function parseDraft(text: string): Composition | Message {
  if (text.length > maxLength) return message('Keep the draft within {limit} characters.', { limit: maxLength });
  let value: unknown;
  try { value = JSON.parse(text); }
  catch { return message('Check the JSON syntax, then preview again.'); }
  if (!objectWithKeys(value, ['version', 'blocks']) || value.version !== 1 || !Array.isArray(value.blocks))
    return message('Use version 1 and a blocks array.');
  if (value.blocks.length > maxBlocks)
    return message('A composition supports up to {limit} blocks.', { limit: maxBlocks });
  const blocks: Block[] = [];
  for (const block of value.blocks) {
    if (objectWithKeys(block, ['kind']) && block.kind === 'value-table') {
      blocks.push({ kind: 'value-table' });
    } else if (objectWithKeys(block, ['kind', 'threshold'], ['aboveLabel', 'belowLabel']) && block.kind === 'threshold-tiles'
        && typeof block.threshold === 'number' && Number.isFinite(block.threshold)
        && validLabel(block.aboveLabel) && validLabel(block.belowLabel)) {
      blocks.push({
        kind: 'threshold-tiles', threshold: block.threshold,
        ...(block.aboveLabel !== undefined && { aboveLabel: block.aboveLabel }),
        ...(block.belowLabel !== undefined && { belowLabel: block.belowLabel }),
      });
    } else {
      return message('Block {index} has an unsupported kind, field or threshold.', { index: blocks.length + 1 });
    }
  }
  return { version: 1, blocks };
}

const timeLabel = (time: number) => formatDateTime(time, {
  timeZone: fixture.dataset.timezone, hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
});
const price = (value: number) => formatNumber(value, { useGrouping: false, maximumFractionDigits: 15 });

function SeriesBlock({ block, selected, select }: {
  block: Block; selected: number | undefined; select: (time: number) => void;
}) {
  if (block.kind === 'value-table') return <section className="probe-block">
    <h3>{t('Value table')}</h3>
    <table>
      <thead><tr><th scope="col">{t('Time ({zone})', { zone: fixture.dataset.timezone })}</th>
        <th scope="col">{t('Close price')}</th></tr></thead>
      <tbody>{fixture.bars.map(bar => <tr key={bar.time_ms} data-selected={selected === bar.time_ms}>
        <td><button type="button" aria-pressed={selected === bar.time_ms}
          onClick={() => select(bar.time_ms)}>{timeLabel(bar.time_ms)}</button></td>
        <td>{price(bar.close)}</td>
      </tr>)}</tbody>
    </table>
  </section>;
  return <section className="probe-block">
    <h3>{t('Threshold tiles')}</h3><p>{t('Threshold {value}', { value: price(block.threshold) })}</p>
    <div className="probe-tiles">{fixture.bars.map(bar => <button type="button" key={bar.time_ms}
      className="probe-tile" data-above={bar.close >= block.threshold} aria-pressed={selected === bar.time_ms}
      onClick={() => select(bar.time_ms)}>
      <span>{timeLabel(bar.time_ms)}</span><strong>{price(bar.close)}</strong>
      <small>{bar.close >= block.threshold ? (block.aboveLabel ?? t('At or above')) : (block.belowLabel ?? t('Below'))}</small>
    </button>)}</div>
  </section>;
}

export function WorkbenchPrototype({ close }: { close: () => void }) {
  const [accepted, setAccepted] = useState({ composition: blank, text: encode(blank) });
  const [draft, setDraft] = useState(encode(blank));
  const [preview, setPreview] = useState<Composition>();
  const [error, setError] = useState<Message>();
  const [selected, setSelected] = useState<number>();
  const heading = useRef<HTMLHeadingElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const host = dialog.current;
    host?.showModal();
    heading.current?.focus();
    return () => host?.close();
  }, []);

  function edit(text: string) {
    setDraft(text);
    setPreview(undefined);
    setError(undefined);
  }

  function inspect(block?: Block) {
    const parsed = parseDraft(draft);
    if ('id' in parsed) {
      setError(parsed);
      setPreview(undefined);
    } else if (block) {
      if (parsed.blocks.length === maxBlocks) {
        setError(message('A composition supports up to {limit} blocks.', { limit: maxBlocks }));
        setPreview(undefined);
      } else {
        const next = encode({ ...parsed, blocks: [...parsed.blocks, block] });
        if (next.length <= maxLength) edit(next);
        else {
          setError(message('Keep the draft within {limit} characters.', { limit: maxLength }));
          setPreview(undefined);
        }
      }
    } else {
      setError(undefined);
      setPreview(parsed);
    }
  }

  const visible = preview ?? accepted.composition;
  return <dialog className="probe" ref={dialog} aria-labelledby="probe-heading"
    onCancel={event => { event.preventDefault(); close(); }}>
    <header className="probe-header">
      <button type="button" className="secondary-button" onClick={close}>{t('Back to chart')}</button>
      <h1 id="probe-heading" ref={heading} tabIndex={-1}>{t('Workbench prototype')}</h1>
      <LanguageSettings />
    </header>
    <div className="probe-body">
      <div className="probe-editor">
        <h2>{t('Draft composition')}</h2>
        <div className="probe-actions">
          <button type="button" onClick={() => inspect({ kind: 'threshold-tiles', threshold: 3499.5 })}>{t('Add threshold tiles')}</button>
          <button type="button" onClick={() => inspect({ kind: 'value-table' })}>{t('Add value table')}</button>
        </div>
        <label htmlFor="probe-draft">{t('Composition JSON')}</label>
        <textarea id="probe-draft" value={draft} spellCheck={false} maxLength={maxLength} onChange={event => edit(event.currentTarget.value)} />
        <p className="probe-hint">{t('Edit the threshold or set aboveLabel and belowLabel to your own text.')}</p>
        <div className="probe-actions">
          <button type="button" className="primary" onClick={() => inspect()}>{t('Preview draft')}</button>
          <button type="button" disabled={!preview} onClick={() => {
            if (preview) { setAccepted({ composition: preview, text: draft }); setPreview(undefined); }
          }}>{t('Accept view')}</button>
          <button type="button" onClick={() => edit(accepted.text)}>{t('Restore accepted view')}</button>
        </div>
        {error && <p role="alert">{t(error)}</p>}
      </div>
      <section aria-label={t('Workbench canvas')} className="probe-canvas">
        <div className="probe-canvas-heading">
          <h2>{t('Workbench canvas')}</h2>
          <span role="status">{preview ? t('Draft preview') : t('Accepted view')}</span>
        </div>
        <details className="probe-source">
          <summary>{t('Frozen synthetic closes')} · {fixture.dataset.instrument_id}</summary>
          <dl><dt>{t('Dataset revision')}</dt><dd>{fixture.snapshot.dataset_revision}</dd>
            <dt>{t('Snapshot hash')}</dt><dd>{fixture.snapshot.content_hash}</dd>
            <dt>{t('Time zone')}</dt><dd>{fixture.dataset.timezone}</dd></dl>
        </details>
        {visible.blocks.length === 0 ? <p className="probe-empty">{t('The canvas is empty. Add a block and preview your draft.')}</p> :
          <div className="probe-blocks">{visible.blocks.map((block, index) =>
            <SeriesBlock key={index} block={block} selected={selected} select={setSelected} />)}</div>}
      </section>
    </div>
  </dialog>;
}
