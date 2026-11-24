import { useEffect, useId, useRef, useState } from 'react';
import { formatDateTime, formatNumber, t } from './i18n';
import { LanguageSettings } from './language-settings';
import {
  authoringPrompt, readComposition, sources, type Composition, type Element, type NumericField,
  type WorkbenchAuthoringTurn,
} from './workbench-composition';
import './workbench-prototype.css';
import './workbench-authoring.css';

type Binding = { source: keyof typeof sources; index: number };
type Inspect = (binding: Binding) => void;

const number = (value: number) => formatNumber(value, { useGrouping: false, maximumFractionDigits: 15 });
const date = (value: number, timeZone: string) => formatDateTime(value, {
  timeZone, dateStyle: 'medium', timeStyle: 'short',
});

function unit(source: (typeof sources)[keyof typeof sources], field: NumericField) {
  if (field !== 'volume') return source.currency;
  return source.quantity_unit === 'shares' ? t('shares') : t('contracts');
}

function Content({ node, binding, inspect }: { node: Element; binding?: Binding | undefined; inspect: Inspect }) {
  switch (node[0]) {
    case 'row':
    case 'stack':
      return <div className={`authored-${node[0]}`}>{node.slice(1).map((child, index) =>
        <Content key={index} node={child as Element} binding={binding} inspect={inspect} />)}</div>;
    case 'title':
      return <h3 dir="auto">{node[1]}</h3>;
    case 'text':
      return <p dir="auto">{node[1]}</p>;
    case 'each':
      return <div className="authored-series" aria-label={sources[node[1]].instrument_id}>
        {sources[node[1]].bars.map((bar, index) => <Content key={bar.time_ms} node={node[2]}
          binding={{ source: node[1], index }} inspect={inspect} />)}
      </div>;
  }
  if (!binding) return null;
  const source = sources[binding.source];
  const bar = source.bars[binding.index]!;
  if (node[0] === 'inspect') return <button type="button" className="secondary-button"
    onClick={() => inspect(binding)} dir="auto">{node[1]}</button>;
  const field = node[1];
  if (field === 'time') return <time dateTime={new Date(bar.time_ms).toISOString()}>{date(bar.time_ms, source.timezone)}</time>;
  const value = bar[field];
  const suffix = unit(source, field);
  return node[0] === 'bar' ? <span className="authored-mark">
    <meter min={0} max={Math.max(1, ...source.bars.map(row => row[field]))} value={value}
      aria-label={t('Value from zero to the largest value in this series')} />
    <span>{number(value)} {suffix}</span>
  </span> : <span>{number(value)} {suffix}</span>;
}

function Provenance({ sourceIds }: { sourceIds: Composition['sourceIds'] }) {
  return <section aria-label={t('Frozen synthetic sources')} className="authored-provenance">
    {sourceIds.map(id => {
      const source = sources[id];
      return <details key={id} className="probe-source">
        <summary>{t('Frozen synthetic source')} · {source.instrument_id} · {source.timeframe}</summary>
        <dl>
          <dt>{t('Dataset ID')}</dt><dd>{source.dataset_id}</dd>
          <dt>{t('Dataset revision')}</dt><dd>{source.dataset_revision}</dd>
          <dt>{t('Snapshot hash')}</dt><dd>{source.snapshot_hash}</dd>
          <dt>{t('Time zone')}</dt><dd>{source.timezone}</dd>
          <dt>{t('Snapshot as of')}</dt><dd>{date(source.as_of_ms, source.timezone)}</dd>
        </dl>
      </details>;
    })}
  </section>;
}

function Evidence({ binding }: { binding: Binding }) {
  const source = sources[binding.source];
  const bar = source.bars[binding.index]!;
  return <section className="authored-evidence" aria-label={t('Selected frozen evidence')} aria-live="polite">
    <h3>{t('Selected frozen evidence')}</h3>
    <p>{source.instrument_id} · {date(bar.time_ms, source.timezone)} · {source.timezone}</p>
    <dl>
      <dt>{t('Close price')}</dt><dd>{number(bar.close)} {source.currency}</dd>
      <dt>{t('Volume')}</dt><dd>{number(bar.volume)} {unit(source, 'volume')}</dd>
      <dt>{t('Snapshot hash')}</dt><dd>{source.snapshot_hash}</dd>
    </dl>
  </section>;
}

export function WorkbenchAuthoring({ close, ask, openDrawing }: { openDrawing: () => void; close: () => void; ask: (turn: WorkbenchAuthoringTurn) => void }) {
  const host = useRef<HTMLDialogElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const headingId = useId();
  const scope = useRef(Symbol());
  const epoch = useRef(0);
  const [accepted, setAccepted] = useState<Composition>();
  const [draft, setDraft] = useState<Composition>();
  const [rejected, setRejected] = useState(false);
  const [selected, setSelected] = useState<Binding>();

  useEffect(() => {
    const dialog = host.current;
    dialog?.showModal();
    heading.current?.focus();
    return () => dialog?.close();
  }, []);

  function startTurn() {
    const expected = epoch.current;
    ask({
      scope: scope.current,
      prompt: authoringPrompt(accepted),
      receive(text) {
        if (!host.current?.open || epoch.current !== expected) return false;
        const candidate = readComposition(text);
        setDraft(candidate);
        setSelected(undefined);
        setRejected(candidate === undefined);
        return candidate !== undefined;
      },
    });
  }

  function clearDraft() {
    ++epoch.current;
    setDraft(undefined);
    setSelected(undefined);
    setRejected(false);
  }

  const visible = draft ?? accepted;
  return <dialog ref={host} className="probe authored-probe" aria-labelledby={headingId}
    onCancel={event => { event.preventDefault(); close(); }}>
    <header className="probe-header">
      <button type="button" className="secondary-button" onClick={close}>{t('Back to chart')}</button>
      <h1 id={headingId} ref={heading} tabIndex={-1}>{t('AI workbench')}</h1>
      {!visible && <button type="button" className="secondary-button" onClick={openDrawing}>{t('Drawing studio')}</button>}
      <LanguageSettings />
    </header>
    <section className="probe-canvas authored-canvas" aria-label={t('Workbench canvas')}>
      <div className="probe-canvas-heading authored-heading">
        <h2>{t('Workbench canvas')}</h2>
        {visible && <span role="status">{draft ? t('Draft preview') : t('Accepted view')}</span>}
        <div className="probe-actions">
          <button type="button" className="primary" onClick={startTurn}>
            {accepted ? t('Ask AI to revise the view') : t('Ask AI to author a view')}
          </button>
          {draft && <button type="button" onClick={() => {
            setAccepted(draft);
            clearDraft();
          }}>{t('Accept view')}</button>}
          {(draft || rejected) && <button type="button" onClick={clearDraft}>
            {accepted ? t('Restore accepted view') : t('Discard draft')}
          </button>}
        </div>
      </div>
      {rejected && <p role="alert">{t('The model’s draft could not be displayed. Try revising your request.')}</p>}
      {visible ? <>
        <div className="authored-presentation" aria-label={t('Authored presentation')}>
          <Content node={visible.tree} inspect={setSelected} />
        </div>
        {selected && <Evidence binding={selected} />}
        <Provenance sourceIds={visible.sourceIds} />
      </> : <p className="probe-empty">{t('Start with a blank canvas. Describe what you want to see and how you want to explore the frozen synthetic data.')}</p>}
    </section>
  </dialog>;
}
