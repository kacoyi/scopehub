import { useEffect, useRef, useState } from 'react';
import fixture from './workbench-synthetic-closes.json';
import { formatDateTime, formatNumber, message, t, type Message } from './i18n';
import { LanguageSettings } from './language-settings';
import { WorkbenchCandleBlock } from './workbench-candle-block';
import { candleSource, isNumericField, type CandleBlock, type CandleTimeframe } from './workbench-candle-source';
import { WorkbenchStateBlock, type StateMapping, type StateStyle } from './workbench-state-block';
import './workbench-prototype.css';

type SeriesBlock = { kind: 'threshold-tiles'; threshold: number; aboveLabel?: string; belowLabel?: string } | { kind: 'value-table' };
type ResultPresentation = StateMapping & { kind: 'candlesticks' | 'state-lights' };
type ResultBlock = CandleBlock & { presentation?: ResultPresentation };
type Block = SeriesBlock | ResultBlock;
type Composition = { version: 1; blocks: Block[] };
type ResultSelection = { scope: string; barOpenMs: number };
type PresentedResult = ResultBlock & { instance: number; timeframe: CandleTimeframe; selection: ResultSelection | undefined };
type PresentedBlock = SeriesBlock | PresentedResult;
const blank: Composition = { version: 1, blocks: [] };
const stateLights: ResultPresentation = {
  kind: 'state-lights',
  ranges: [{ below: 0.4, tone: 'green' }, { below: 0.8, tone: 'amber' }],
  otherwise: { tone: 'red' },
};
const timeframes = ['5m', '15m'] as const;
const maxRanges = 4;
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

function readStateStyle(value: Record<string, unknown>): StateStyle | undefined {
  const { tone, label } = value;
  if ((tone === 'green' || tone === 'amber' || tone === 'red' || tone === 'neutral') && validLabel(label)) {
    return { tone, ...(label !== undefined && { label }) };
  }
}

function readPresentation(value: unknown): ResultPresentation | undefined {
  if (!objectWithKeys(value, ['kind', 'ranges', 'otherwise'])
      || (value.kind !== 'candlesticks' && value.kind !== 'state-lights')
      || !Array.isArray(value.ranges) || value.ranges.length < 1 || value.ranges.length > maxRanges
      || !objectWithKeys(value.otherwise, ['tone'], ['label'])) return;
  const otherwise = readStateStyle(value.otherwise);
  if (!otherwise) return;

  const ranges: ResultPresentation['ranges'] = [];
  let lower = -Infinity;
  for (const range of value.ranges) {
    if (!objectWithKeys(range, ['below', 'tone'], ['label']) || typeof range.below !== 'number'
        || !Number.isFinite(range.below) || range.below <= lower) return;
    const style = readStateStyle(range);
    if (!style) return;
    ranges.push({ below: range.below, ...style });
    lower = range.below;
  }
  return { kind: value.kind, ranges, otherwise };
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
    } else if (objectWithKeys(block, ['kind', 'source', 'annotation'], ['presentation']) && block.kind === 'candlestick') {
      const { annotation, source } = block;
      if (source !== candleSource.id || !objectWithKeys(annotation, ['field', 'when'])
          || !isNumericField(annotation.field) || !objectWithKeys(annotation.when, ['field', 'above'])
          || !isNumericField(annotation.when.field) || typeof annotation.when.above !== 'number'
          || !Number.isFinite(annotation.when.above)) {
        return message('Block {index} must bind granted synthetic fields and a finite threshold.', { index: blocks.length + 1 });
      }
      const presentation = readPresentation(block.presentation);
      if (Object.hasOwn(block, 'presentation') && !presentation) {
        return message('Block {index} needs 1 to {limit} strictly increasing finite limits and valid states.', {
          index: blocks.length + 1, limit: maxRanges,
        });
      }
      blocks.push({ kind: 'candlestick', source, annotation: {
        field: annotation.field, when: { field: annotation.when.field, above: annotation.when.above },
      }, ...(presentation && { presentation }) });
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

function resultsByBinding<T extends ResultBlock>(results: readonly T[]) {
  const groups = new Map<string, T[]>();
  for (const result of results) {
    const { source, annotation: { field, when } } = result;
    const key = JSON.stringify([source, field, when.field]);
    const group = groups.get(key);
    if (group) group.push(result);
    else groups.set(key, [result]);
  }
  return groups;
}

function previewBlocks(blocks: Block[], reference: PresentedBlock[], lastAcceptedInstance: number): PresentedBlock[] {
  const previousResults = reference.filter(block => block.kind === 'candlestick');
  const draftResults = blocks.filter(block => block.kind === 'candlestick');
  const previousGroups = resultsByBinding(previousResults);
  const draftGroups = resultsByBinding(draftResults);
  const matches = new Map<ResultBlock, PresentedResult>();
  const definition = (block: ResultBlock) => JSON.stringify([block.annotation.when.above, block.presentation]);

  for (const [key, results] of draftGroups) {
    const previous = previousGroups.get(key) ?? [];
    const available: (PresentedResult | undefined)[] = [...previous];
    const sameCount = previous.length === results.length;
    const claim = (result: ResultBlock, index: number) => {
      const match = available[index];
      if (!match) return;
      matches.set(result, match);
      available[index] = undefined;
    };

    const previousDefinitions = previous.map(definition).sort();
    const draftDefinitions = results.map(definition).sort();
    if (sameCount && draftDefinitions.some((value, index) => value !== previousDefinitions[index])) {
      // When a definition changes, an exact sibling at the same occurrence claims its own instance first.
      results.forEach((result, index) => {
        if (definition(result) === definition(previous[index]!)) claim(result, index);
      });
    }
    for (const result of results) {
      if (!matches.has(result)) {
        // Match identical definitions in occurrence order, consuming each previous instance once.
        claim(result, available.findIndex(match => match !== undefined && definition(match) === definition(result)));
      }
    }
    if (sameCount) {
      // Match presentation-only edits by the retained candle threshold before falling back to order.
      for (const result of results) {
        if (!matches.has(result)) claim(result, available.findIndex(match => match !== undefined
          && match.annotation.when.above === result.annotation.when.above));
      }
      const remaining = available.filter(match => match !== undefined);
      results.filter(result => !matches.has(result)).forEach((result, index) => {
        matches.set(result, remaining[index]!);
      });
    }
  }

  // After matching definitions within each binding, carry a field edit only across a one-to-one source match.
  const claimed = new Set(matches.values());
  const unmatchedPrevious = previousResults.filter(result => !claimed.has(result));
  const unmatchedDraft = draftResults.filter(result => !matches.has(result));
  for (const result of unmatchedDraft) {
    const previous = unmatchedPrevious.filter(candidate => candidate.source === result.source);
    const incoming = unmatchedDraft.filter(candidate => candidate.source === result.source);
    if (previous.length === 1 && incoming.length === 1) matches.set(result, previous[0]!);
  }

  let nextInstance = Math.max(lastAcceptedInstance, ...previousResults.map(result => result.instance)) + 1;
  return blocks.map(block => {
    if (block.kind !== 'candlestick') return block;
    const match = matches.get(block);
    return { ...block, instance: match?.instance ?? nextInstance++, timeframe: match?.timeframe ?? '5m',
      selection: match?.selection };
  });
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
  const [draftReference, setDraftReference] = useState<PresentedBlock[]>();
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

  const parsed = parseDraft(draft);
  const lastAcceptedInstance = Math.max(0, ...accepted.blocks.flatMap(block => block.kind === 'candlestick' ? [block.instance] : []));

  function presentDraft(blocks: Block[]) {
    return preview ?? previewBlocks(blocks, draftReference ?? accepted.blocks, lastAcceptedInstance);
  }

  function writeDraft(blocks: PresentedBlock[]) {
    const composition: Composition = { version: 1, blocks: blocks.map(block => {
      if (block.kind !== 'candlestick') return block;
      const { kind, source, annotation, presentation } = block;
      return { kind, source, annotation, ...(presentation && { presentation }) };
    }) };
    const formatted = encode(composition);
    const next = formatted.length <= maxLength ? formatted : JSON.stringify(composition);
    if (next.length <= maxLength) {
      edit(next);
      // Structured controls know which draft block each edit targets. Retain those identities for preview and text edits.
      setDraftReference(blocks);
    } else {
      setError(message('Keep the draft within {limit} characters.', { limit: maxLength }));
      setPreview(undefined);
    }
  }

  function inspect(block?: Block) {
    if ('id' in parsed) {
      setError(parsed);
      setPreview(undefined);
    } else if (block) {
      if (parsed.blocks.length === maxBlocks) {
        setError(message('A composition supports up to {limit} blocks.', { limit: maxBlocks }));
        setPreview(undefined);
      } else {
        const reference = presentDraft(parsed.blocks);
        writeDraft(previewBlocks([...parsed.blocks, block], reference, lastAcceptedInstance));
      }
    } else {
      setError(undefined);
      setPreview(presentDraft(parsed.blocks));
    }
  }

  function updateResult(block: PresentedResult, changes: Partial<Pick<PresentedResult, 'timeframe' | 'selection'>>) {
    const update = (blocks: PresentedBlock[]) => blocks.map(candidate => candidate.kind === 'candlestick'
      && candidate.instance === block.instance ? { ...candidate, ...changes } : candidate);
    if (preview) setPreview(current => current && update(current));
    else {
      setAccepted(current => ({ ...current, blocks: update(current.blocks) }));
      setDraftReference(current => current && update(current));
    }
  }

  const visible = preview ?? accepted.blocks;
  const hasResults = visible.some(block => block.kind === 'candlestick');
  const showCloses = visible.length === 0 || visible.some(block => block.kind !== 'candlestick');
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
        {'blocks' in parsed && parsed.blocks.map((block, index) => {
          if (block.kind !== 'candlestick') return null;
          const { presentation } = block;
          return <div className="probe-presentation" key={index}>
            <label htmlFor={`probe-presentation-${index}`}>{t('Block {index} presentation', { index: index + 1 })}</label>
            <select id={`probe-presentation-${index}`} value={presentation?.kind ?? 'candlesticks'}
              onChange={event => {
                const kind = event.currentTarget.value === 'state-lights' ? 'state-lights' : 'candlesticks';
                writeDraft(presentDraft(parsed.blocks).map((candidate, position) => position === index
                  && candidate.kind === 'candlestick'
                  ? { ...candidate, presentation: { ...(presentation ?? stateLights), kind } } : candidate));
              }}>
              <option value="candlesticks">{t('Candlesticks')}</option>
              <option value="state-lights">{t('State lights')}</option>
            </select>
          </div>;
        })}
        <label htmlFor="probe-draft">{t('Composition JSON')}</label>
        <textarea id="probe-draft" value={draft} spellCheck={false} maxLength={maxLength} onChange={event => edit(event.currentTarget.value)} />
        <p className="probe-hint">{t('Edit the threshold or set aboveLabel and belowLabel to your own text.')}</p>
        <p className="probe-hint">{t('Bind strength or turnover and edit the annotation threshold.')}</p>
        {'blocks' in parsed && parsed.blocks.some(block => block.kind === 'candlestick'
          && block.presentation?.kind === 'state-lights') && <p className="probe-hint">
          {t('State lights use the condition field. In JSON, each below limit excludes its endpoint. Set tone to green, amber, red or neutral and add an optional label.')}
        </p>}
        <div className="probe-actions">
          <button type="button" className="primary" onClick={() => inspect()}>{t('Preview draft')}</button>
          <button type="button" disabled={!preview} onClick={() => {
            if (preview) { setAccepted({ blocks: preview, text: draft }); setPreview(undefined); setDraftReference(undefined); }
          }}>{t('Accept view')}</button>
          <button type="button" onClick={() => { edit(accepted.text); setDraftReference(undefined); }}>{t('Restore accepted view')}</button>
        </div>
        {error && <p role="alert">{t(error)}</p>}
      </div>
      <section aria-label={t('Workbench canvas')} className="probe-canvas">
        <div className="probe-canvas-heading">
          <h2>{t('Workbench canvas')}</h2>
          <span role="status">{preview ? t('Draft preview') : t('Accepted view')}</span>
        </div>
        {showCloses && <details className="probe-source">
          <summary>{hasResults ? t('Frozen synthetic closes for tiles and tables') : t('Frozen synthetic closes')}
            {' · '}{fixture.dataset.instrument_id}</summary>
          <dl><dt>{t('Dataset revision')}</dt><dd>{fixture.snapshot.dataset_revision}</dd>
            <dt>{t('Snapshot hash')}</dt><dd>{fixture.snapshot.content_hash}</dd>
            <dt>{t('Time zone')}</dt><dd>{fixture.dataset.timezone}</dd></dl>
        </details>}
        {visible.length === 0 ? <p className="probe-empty">{t('The canvas is empty. Add a block and preview your draft.')}</p> :
          <div className="probe-blocks">{visible.map((block, index) => {
            if (block.kind !== 'candlestick') return <SeriesBlock key={index} block={block} selected={selected} select={setSelected} />;
            const { instance, source, timeframe, presentation, selection } = block;
            const controls = timeframes.map(period => <button key={period} type="button" aria-pressed={period === timeframe}
              onClick={() => updateResult(block, { timeframe: period })}>{t('{count} min', { count: period === '5m' ? 5 : 15 })}</button>);
            if (presentation?.kind === 'state-lights') {
              const field = block.annotation.when.field;
              const { seriesRevision, resultRevision } = candleSource.frames[timeframe];
              const scope = JSON.stringify([source, timeframe, seriesRevision, resultRevision, field]);
              return <WorkbenchStateBlock key={`result-${instance}`} field={field} source={source}
                timeframe={timeframe} controls={controls} mapping={presentation}
                selectedBar={selection?.scope === scope ? selection.barOpenMs : undefined}
                selectBar={barOpenMs => updateResult(block, { selection: { scope, barOpenMs } })} />;
            }
            return <WorkbenchCandleBlock key={`result-${instance}`} block={block} timeframe={timeframe} controls={controls} />;
          })}</div>}
      </section>
    </div>
  </dialog>;
}
