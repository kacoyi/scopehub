import { useEffect, useRef, useState } from 'react';
import fixture from './workbench-synthetic-closes.json';
import { formatDateTime, formatNumber, message, t, type Message } from './i18n';
import { LanguageSettings } from './language-settings';
import { WorkbenchCandleBlock } from './workbench-candle-block';
import { candleSource, isNumericField, type CandleBlock, type CandleTimeframe } from './workbench-candle-source';
import './workbench-prototype.css';

type SeriesBlock = { kind: 'threshold-tiles'; threshold: number; aboveLabel?: string; belowLabel?: string } | { kind: 'value-table' };
type Block = SeriesBlock | CandleBlock;
type Composition = { version: 1; blocks: Block[] };
type PresentedCandle = CandleBlock & { timeframe: CandleTimeframe };
type PresentedBlock = SeriesBlock | PresentedCandle;
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
    } else if (objectWithKeys(block, ['kind', 'source', 'annotation']) && block.kind === 'candlestick') {
      const { annotation, source } = block;
      if (source !== candleSource.id || !objectWithKeys(annotation, ['field', 'when'])
          || !isNumericField(annotation.field) || !objectWithKeys(annotation.when, ['field', 'above'])
          || !isNumericField(annotation.when.field) || typeof annotation.when.above !== 'number'
          || !Number.isFinite(annotation.when.above)) {
        return message('Block {index} must bind granted synthetic fields and a finite threshold.', { index: blocks.length + 1 });
      }
      blocks.push({ kind: 'candlestick', source, annotation: {
        field: annotation.field, when: { field: annotation.when.field, above: annotation.when.above },
      } });
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

function previewBlocks(blocks: Block[], accepted: PresentedBlock[]): PresentedBlock[] {
  const acceptedCandles = accepted.filter(block => block.kind === 'candlestick');
  const draftCandles = blocks.filter(block => block.kind === 'candlestick');
  const bindingKey = ({ source, annotation }: CandleBlock) => JSON.stringify([source, annotation]);
  const acceptedKeys = acceptedCandles.map(bindingKey);
  const draftKeys = draftCandles.map(bindingKey);
  const sameOrder = draftKeys.length === acceptedKeys.length && draftKeys.every((key, index) => key === acceptedKeys[index]);
  const periods = new Map(draftCandles.map((block, index) => {
    const key = draftKeys[index]!;
    const first = acceptedKeys.indexOf(key);
    // Duplicate definitions are addressable by order while the candle sequence is unchanged.
    // In a changed sequence, carry a timeframe only for an unambiguous binding.
    const unique = first >= 0 && first === acceptedKeys.lastIndexOf(key)
      && draftKeys.indexOf(key) === draftKeys.lastIndexOf(key);
    const match = sameOrder ? index : unique ? first : -1;
    return [block, acceptedCandles[match]?.timeframe ?? '5m'] as const;
  }));
  return blocks.map(block => block.kind === 'candlestick'
    ? { ...block, timeframe: periods.get(block) ?? '5m' } : block);
}

const timeLabel = (time: number) => formatDateTime(time, {
  timeZone: fixture.dataset.timezone, hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
});
const price = (value: number) => formatNumber(value, { useGrouping: false, maximumFractionDigits: 15 });

function SeriesBlock({ block, selected, select }: {
  block: SeriesBlock; selected: number | undefined; select: (time: number) => void;
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
  const [accepted, setAccepted] = useState<{ blocks: PresentedBlock[]; text: string }>({ blocks: [], text: encode(blank) });
  const [draft, setDraft] = useState(encode(blank));
  const [preview, setPreview] = useState<PresentedBlock[]>();
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
      setPreview(current => current ?? previewBlocks(parsed.blocks, accepted.blocks));
    }
  }

  function selectTimeframe(block: PresentedCandle, timeframe: CandleTimeframe) {
    const update = (blocks: PresentedBlock[]) => blocks.map(candidate => candidate === block ? { ...block, timeframe } : candidate);
    if (preview) setPreview(current => current && update(current));
    else setAccepted(current => ({ ...current, blocks: update(current.blocks) }));
  }

  const visible = preview ?? accepted.blocks;
  const hasCandles = visible.some(block => block.kind === 'candlestick');
  const showCloses = !hasCandles || visible.some(block => block.kind !== 'candlestick');
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
          <button type="button" onClick={() => inspect({
            kind: 'candlestick', source: candleSource.id,
            annotation: { field: 'turnover', when: { field: 'strength', above: 0.8 } },
          })}>{t('Add candlesticks')}</button>
        </div>
        <label htmlFor="probe-draft">{t('Composition JSON')}</label>
        <textarea id="probe-draft" value={draft} spellCheck={false} maxLength={maxLength} onChange={event => edit(event.currentTarget.value)} />
        <p className="probe-hint">{t('Edit the threshold or set aboveLabel and belowLabel to your own text.')}</p>
        <p className="probe-hint">{t('Bind strength or turnover and edit the annotation threshold.')}</p>
        <div className="probe-actions">
          <button type="button" className="primary" onClick={() => inspect()}>{t('Preview draft')}</button>
          <button type="button" disabled={!preview} onClick={() => {
            if (preview) { setAccepted({ blocks: preview, text: draft }); setPreview(undefined); }
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
        {showCloses && <details className="probe-source">
          <summary>{hasCandles ? t('Frozen synthetic closes for tiles and tables') : t('Frozen synthetic closes')}
            {' · '}{fixture.dataset.instrument_id}</summary>
          <dl><dt>{t('Dataset revision')}</dt><dd>{fixture.snapshot.dataset_revision}</dd>
            <dt>{t('Snapshot hash')}</dt><dd>{fixture.snapshot.content_hash}</dd>
            <dt>{t('Time zone')}</dt><dd>{fixture.dataset.timezone}</dd></dl>
        </details>}
        {visible.length === 0 ? <p className="probe-empty">{t('The canvas is empty. Add a block and preview your draft.')}</p> :
          <div className="probe-blocks">{visible.map((block, index) => block.kind === 'candlestick'
            ? <WorkbenchCandleBlock key={index} block={block} timeframe={block.timeframe}
              selectTimeframe={timeframe => selectTimeframe(block, timeframe)} />
            : <SeriesBlock key={index} block={block} selected={selected} select={setSelected} />)}</div>}
      </section>
    </div>
  </dialog>;
}
