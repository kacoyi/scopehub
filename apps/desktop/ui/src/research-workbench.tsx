import { useEffect, useRef, useState } from 'react';
import type { ResearchNote11, ResearchSampleOutput, SavedResearchView11 } from '../../../../contracts/generated/protocol';
import { t } from './i18n';
import { LanguageSettings } from './language-settings';
import { editRegions, fieldValue, queryRows, readProgram, researchTurn, sampleProgram, type Node, type RecordRow } from './research-composition';
import { useResearchSource } from './research-source';
import type { LocalWorkspace } from './use-local-workspace';
import type { WorkbenchAuthoringTurn } from './workbench-composition';
import './workbench-prototype.css';
import './workbench-authoring.css';

type View = { tree: Node; size: number };
type ContentProps = { node: Node; data: ResearchSampleOutput; note: ResearchNote11; row?: RecordRow; open: (record: RecordRow) => void };
const json = (tree: Node) => JSON.stringify(tree);
const display = (value: number | string) => typeof value === 'number' ? new Date(value).toISOString() : value;

function Content({ node, data, note, row, open }: ContentProps) {
  const child = (item: Node, key: string | number, bound?: RecordRow) =>
    <Content key={key} node={item} data={data} note={note} {...(bound && { row: bound })} open={open} />;
  switch (node[0]) {
    case 'row': case 'stack': return <div className={'authored-' + node[0]}>
      {(node.slice(1) as Node[]).map((n, i) => child(n, i, row))}</div>;
    case 'heading': return <h3 dir="auto">{node[1]}</h3>;
    case 'text': return <p dir="auto">{node[1]}</p>;
    case 'field': return row ? <span dir="auto">{display(fieldValue(row, node[1]))}</span> : null;
    case 'open': return row ? <button type="button" className="secondary-button" onClick={() => open(row)}>{node[1]}</button> : null;
    case 'note': return child(node[1], note.id, note);
    case 'list': {
      const rows = queryRows(data.disclosures, note, node[1], node[2]);
      const item = node[3];
      if (item[0] !== 'group') return <div className="authored-series" data-count={rows.length}>
        {rows.map(r => child(item, r.id, r))}</div>;
      const groups = new Map<number | string, typeof rows>();
      for (const record of rows) {
        const key = fieldValue(record, item[1]);
        const members = groups.get(key);
        if (members) members.push(record); else groups.set(key, [record]);
      }
      return <div className="authored-series" data-count={rows.length}>{[...groups].map(([key, records]) =>
        <section key={key}><h4>{display(key)}</h4>{records.map(r => child(item[2], r.id, r))}</section>)}</div>;
    }
    case 'group': return null;
  }
}

export function ResearchWorkbench({ workspace, reopen, close, ask }: {
  workspace: LocalWorkspace | undefined; reopen: boolean; close: () => void; ask: (turn: WorkbenchAuthoringTurn) => void;
}) {
  const layout = workspace?.snapshot?.layouts?.active;
  const saved = useRef(reopen ? layout?.definition.research_view : undefined).current;
  const [accepted, setAccepted] = useState<View | undefined>(() => {
    const tree = saved && readProgram(saved.program);
    return tree && saved ? { tree, size: saved.parameters.size } : undefined;
  });
  const [previous, setPrevious] = useState<View | null>();
  const [draft, setDraft] = useState<View>();
  const [code, setCode] = useState(saved?.program ?? '');
  const [size, setSize] = useState(saved?.parameters.size ?? 16);
  const [noteId, setNoteId] = useState(saved?.note_id ?? 'note-a');
  const [name, setName] = useState(reopen ? layout?.name ?? '' : '');
  const [region, setRegion] = useState('');
  const [invalid, setInvalid] = useState(Boolean(saved && !accepted));
  const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved' | 'failed'>('idle');
  const source = useResearchSource(workspace?.snapshot?.workspace.id, noteId);
  const dialog = useRef<HTMLDialogElement>(null);
  const token = useRef(0);
  const scope = useRef(Symbol());
  const editRevision = useRef(workspace?.snapshot?.workspace.revision);
  const data = source.data;
  const current = data?.access === 'connected' && data.note?.id === noteId ? data.note : undefined;
  const regions = accepted ? editRegions(accepted.tree) : [];
  const turn = researchTurn(accepted?.tree, region ? region.split('.').map(Number) : []);
  const saving = saveState === 'saving';

  useEffect(() => {
    const host = dialog.current;
    host?.showModal();
    return () => { ++token.current; host?.close(); };
  }, []);

  function edit(text: string, fontSize = size) {
    ++token.current;
    setCode(text); setSize(fontSize); setDraft(undefined); setInvalid(false); setSaveState('idle');
    source.reset();
  }
  function preview(text: string) {
    const tree = readProgram(text);
    edit(text);
    if (tree) setDraft({ tree, size }); else setInvalid(true);
    return Boolean(tree);
  }
  function author() {
    if (!turn) return;
    const target = ++token.current;
    ask({ scope: scope.current, prompt: turn.prompt, receive(text) {
      if (target !== token.current || !dialog.current?.open) return false;
      const proposal = turn.decode(text);
      if (proposal) return preview(json(proposal));
      setInvalid(true); setDraft(undefined); source.reset();
      return false;
    } });
  }
  function restore(view: View | undefined) {
    edit(view ? json(view.tree) : '', view?.size ?? 16);
    setRegion('');
  }
  async function save() {
    if (!workspace || !accepted || !layout || editRevision.current === undefined || draft || !current) return;
    const view: SavedResearchView11 = { version: 1, program: json(accepted.tree), parameters: { size: accepted.size },
      note_id: noteId, source_id: 'scopehub.synthetic.research.v1' };
    const definition = { ...layout.definition, research_view: view };
    delete definition.authored_view;
    setSaveState('saving');
    try {
      const prepared = await workspace.previewLayout({ action: 'save', name: name.trim(), definition }, editRevision.current);
      if (!dialog.current?.open) return;
      await workspace.applyLayout(prepared.command);
      editRevision.current += 1;
      setSaveState('saved');
    } catch { setSaveState('failed'); }
  }
  function open(row: RecordRow) {
    void source.run('reviewed_at_ms' in row ? 'read' : 'original', row.id);
  }
  function presentation(view: View, title: string) {
    return <section className="authored-presentation" aria-label={title} style={{ fontSize: view.size }}>
      {current && data && <Content node={view.tree} data={data} note={current} open={open} />}
    </section>;
  }
  return <dialog ref={dialog} className="probe authored-probe" aria-labelledby="research-heading"
    onCancel={event => { event.preventDefault(); close(); }}>
    <header className="probe-header">
      <button onClick={close}>{t('Back to chart')}</button><h1 id="research-heading">{t('Research workbench')}</h1><LanguageSettings />
    </header>
    <div className="probe-body">
      <section className="probe-editor" aria-label={t('Research source')}>
        <h2>{t('Local synthetic research')}</h2>
        <p role="status">{!workspace ? t('Open the private workspace to connect a source.') : source.failed ? t('Research source unavailable')
          : !data ? t('Reading source status…') : data.access === 'revoked' ? t('Research access revoked')
          : data.access === 'disconnected' ? t('Research source disconnected') : data.access === 'unconfigured'
          ? t('Research source unconfigured') : t('Research source connected')}</p>
        <div className="probe-actions">
          {data && data.access !== 'connected' && <button disabled={source.pending} onClick={() => void source.run('connect')}>{t('Connect synthetic research')}</button>}
          {data?.access === 'connected' && <><button disabled={source.pending} onClick={() => void source.run('refresh')}>{t('Refresh research source')}</button>
            <button disabled={source.pending} onClick={() => void source.run('disconnect')}>{t('Disconnect research source')}</button></>}
          {(data?.access === 'connected' || data?.access === 'disconnected') &&
            <button disabled={source.pending} onClick={() => void source.run('revoke')}>{t('Revoke research access')}</button>}
        </div>
        {data?.access === 'connected' && <label>{t('Selected research note')}<select value={noteId} onChange={e => {
          ++token.current; source.reset(); setNoteId(e.currentTarget.value); setSaveState('idle');
        }}>{data.notes.map(note => <option key={note.id} value={note.id}>{note.title}</option>)}</select></label>}
        <label htmlFor="research-program">{t('Composition source')}</label>
        <textarea id="research-program" maxLength={4096} spellCheck={false} value={code} onChange={e => edit(e.currentTarget.value)} />
        <div className="probe-actions">
          <button onClick={() => preview(json(sampleProgram))}>{t('Try research example')}</button>
          <button onClick={() => preview(code)}>{t('Preview draft')}</button>
        </div>
        <label>{t('Text size')}<input type="range" min="12" max="24" value={size} onChange={e => {
          const val = Number(e.currentTarget.value);
          const tree = readProgram(code); edit(code, val); if (tree) setDraft({ tree, size: val });
        }} /></label>
        {regions.length > 0 && <label>{t('AI edit region')}<select value={region} onChange={e => {
          ++token.current; setRegion(e.currentTarget.value);
        }}>{regions.map(({path,kind}) => <option key={path.join('.')} value={path.join('.')}>
          {path.length ? path.join('.') + ' · ' + kind : t('Whole composition')}</option>)}</select></label>}
        {!turn && <p>{t('Choose a smaller region to fit the model turn.')}</p>}
        <button className="primary" disabled={!turn || !workspace || saving} onClick={author}>
          {accepted ? t('Ask AI to revise the view') : t('Ask AI to author a view')}</button>
        <div className="probe-actions">
          <button disabled={!draft || saving} onClick={() => {
            if (draft) { setPrevious(accepted ?? null); setAccepted(draft); restore(draft); }
          }}>{t('Accept view')}</button>
          <button disabled={saving} onClick={() => restore(accepted)}>{t('Restore accepted view')}</button>
          <button disabled={previous === undefined || saving} onClick={() => {
            setAccepted(previous ?? undefined); restore(previous ?? undefined); setPrevious(undefined);
          }}>{t('Undo accepted edit')}</button>
        </div>
        <label>{t('View name')}<input value={name} maxLength={64} onChange={e => { setName(e.currentTarget.value); setSaveState('idle'); }} /></label>
        <button disabled={!current || !accepted || !!draft || code !== json(accepted.tree) || !name.trim() || saving}
          onClick={() => void save()}>{t('Save accepted view')}</button>
        {saveState === 'saved' && <p role="status">{t('Research view saved')}</p>}
        {saveState === 'failed' && <p role="alert">{t('The research view could not be saved. Reopen the current layout and retry.')}</p>}
      </section>
      <section className="probe-canvas" aria-label={t('Workbench canvas')}>
        {invalid && <p role="alert">{t('The research composition could not be displayed.')}</p>}
        {!accepted && !draft && <p>{t('Start with a blank research canvas. Write instructions or ask AI to create a view.')}</p>}
        {draft && <><h2>{t('Draft preview')}</h2>{presentation(draft,t('Draft preview'))}</>}
        {accepted && <><h2>{t('Accepted view')}</h2>{presentation(accepted,t('Accepted view'))}</>}
        {current && source.original && <section aria-label={t('Attributed original')} className="authored-evidence">
          <h3>{source.original.title}</h3><p>{source.original.issuer} · {source.original.id} · {
            display('reviewed_at_ms' in source.original ? source.original.reviewed_at_ms : source.original.published_at_ms)}</p>
          <p>{source.original.body}</p><button onClick={source.reset}>{t('Close original')}</button>
        </section>}
      </section>
    </div>
  </dialog>;
}
