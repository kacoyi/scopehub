import { useEffect, useId, useRef, useState } from 'react';
import type {
  LayoutPanel11, NamedWorkspaceLayout11, WorkspaceLayout11, WorkspaceLayoutEdit11,
} from '../../../../contracts/generated/protocol';
import { instrumentName, symbol } from './instrument-display';
import type { Instrument } from './market';
import type { LocalWorkspace, PreparedWorkspaceLayout } from './use-local-workspace';

export const layoutDocks = ['left', 'right', 'bottom'] as const;
export const layoutPanelLabels = { watchlist: 'Watchlist', inspection: 'Data window' } as const;

type Draft = {
  name: string;
  definition: WorkspaceLayout11;
  text?: string;
  revision: number;
};
function layoutFailure(code: unknown) {
  switch (code) {
    case 'INVALID_ARGUMENT': return 'The layout definition has an invalid name, setting, or version.';
    case 'UNSUPPORTED_CAPABILITY': return 'The layout contains an unsupported panel or binding.';
    case 'STALE_CONTEXT': return 'The workspace changed. Reopen the editor to preview against its current revision.';
    case 'BUSY': return 'The workspace is busy. Try again in a moment.';
    case 'RESOURCE_LIMIT': return 'This layout would exceed the profile limit.';
    case 'PERMISSION_DENIED': return 'This layout action is no longer authorized.';
    default: return 'The local layout could not be updated.';
  }
}

export function useWorkspaceLayout(workspace: LocalWorkspace | undefined) {
  const [draft, setDraft] = useState<Draft>();
  const [preview, setPreview] = useState<PreparedWorkspaceLayout>();
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const active = useRef(false);
  const snapshot = workspace?.snapshot;
  const layout = preview?.layout ?? snapshot?.layouts?.active;

  function open(editPreview = false) {
    if (!snapshot || !layout) return;
    setDraft(editPreview && draft && preview?.command.intent.arguments.edit.action === 'save'
      ? { ...draft, revision: snapshot.workspace.revision }
      : { name: layout.name ?? '', definition: layout.definition, revision: snapshot.workspace.revision });
    setPreview(undefined);
    setError(undefined);
    setEditing(true);
  }

  async function run(work: () => Promise<void>, onError?: (code: unknown) => void) {
    if (active.current) return;
    active.current = true;
    setBusy(true);
    setError(undefined);
    try { await work(); }
    catch (failure) {
      const code = failure && typeof failure === 'object' && 'code' in failure ? failure.code : undefined;
      setError(layoutFailure(code));
      onError?.(code);
    } finally {
      active.current = false;
      setBusy(false);
    }
  }

  function requestPreview(edit: WorkspaceLayoutEdit11) {
    if (!workspace || !draft) return;
    const { revision } = draft;
    void run(async () => {
      setPreview(await workspace.previewLayout(edit, revision));
      setEditing(false);
    }, code => {
      if (code === 'STALE_CONTEXT') workspace.reload();
    });
  }

  function apply() {
    if (!workspace || !preview) return;
    void run(async () => {
      await workspace.applyLayout(preview.command);
      setPreview(undefined);
      setDraft(undefined);
    }, code => {
      if (code === 'STALE_CONTEXT' || code === 'PERMISSION_DENIED') {
        setPreview(undefined);
        setDraft(undefined);
        workspace.reload();
      }
    });
  }

  function undo() {
    if (!workspace) return;
    void run(async () => {
      await workspace.undoLayout();
      setDraft(undefined);
      setEditing(false);
    });
  }

  return {
    layout, draft, changeDraft: setDraft, preview, requestPreview, apply, undo, open,
    editing, busy, error,
    close() { setEditing(false); setError(undefined); setDraft(undefined); },
    discard() { setPreview(undefined); setError(undefined); setDraft(undefined); },
  };
}

type LayoutState = ReturnType<typeof useWorkspaceLayout>;

function LayoutFields({ definition, instruments, change }: {
  definition: WorkspaceLayout11;
  instruments: readonly Instrument[];
  change: (layout: WorkspaceLayout11) => void;
}) {
  function updatePanel(index: number, panel: LayoutPanel11) {
    const shared = definition.panels.find((value, position) => position !== index && value.dock === panel.dock);
    const moved = definition.panels[index]?.dock !== panel.dock;
    const size = moved && shared ? shared.size : panel.size;
    change({ ...definition, panels: definition.panels.map((value, position) => {
      if (position === index) return { ...panel, size };
      return value === shared ? { ...value, size } : value;
    }) });
  }
  function movePanel(index: number, offset: number) {
    const panels = definition.panels.slice();
    const current = panels[index];
    const adjacent = panels[index + offset];
    if (!current || !adjacent) return;
    panels[index] = adjacent;
    panels[index + offset] = current;
    change({ ...definition, panels });
  }

  return <>
    {definition.panels.map((panel, index) => {
      const label = layoutPanelLabels[panel.panel];
      return <fieldset className="layout-fields" key={panel.panel}>
        <legend>{label}</legend>
        <div className="layout-form-grid">
          <label>{label} dock<select value={panel.dock} onChange={event => {
            const dock = layoutDocks.find(candidate => candidate === event.currentTarget.value);
            if (dock) updatePanel(index, { ...panel, dock });
          }}>{layoutDocks.map(dock => <option value={dock} key={dock}>{dock[0]?.toUpperCase()}{dock.slice(1)}</option>)}</select></label>
          <label>{label} size (pixels)<input type="number" min={180} max={480} step={1} required value={Number.isFinite(panel.size) ? panel.size : ''}
            onChange={event => updatePanel(index, { ...panel, size: event.currentTarget.valueAsNumber })} /></label>
        </div>
        <div className="layout-actions">
          <button type="button" className="secondary-button" aria-label={`Move ${label} earlier`} disabled={index === 0}
            onClick={() => movePanel(index, -1)}>Earlier</button>
          <button type="button" className="secondary-button" aria-label={`Move ${label} later`} disabled={index === definition.panels.length - 1}
            onClick={() => movePanel(index, 1)}>Later</button>
        </div>
      </fieldset>;
    })}
    <fieldset className="layout-fields">
      <legend>Appearance and selection</legend>
      <div className="layout-form-grid">
        <label>Theme<select value={definition.theme} onChange={event => change({ ...definition,
          theme: event.currentTarget.value === 'light' ? 'light' : 'dark',
        })}><option value="dark">Dark</option><option value="light">Light</option></select></label>
        <label>Gain color<input type="color" value={definition.gain_color}
          onChange={event => change({ ...definition, gain_color: event.currentTarget.value })} /></label>
        <label>Loss color<input type="color" value={definition.loss_color}
          onChange={event => change({ ...definition, loss_color: event.currentTarget.value })} /></label>
      </div>
      <label className="layout-check"><input type="checkbox" checked={definition.link_selection}
        onChange={event => change({ ...definition, link_selection: event.currentTarget.checked })} />Link data window to chart</label>
      <label>Pin comparison<select value={definition.pinned_instrument ?? ''}
        onChange={event => change({ ...definition, pinned_instrument: event.currentTarget.value || null })}>
        <option value="">No comparison</option>
        {definition.pinned_instrument && !instruments.some(item => item.id === definition.pinned_instrument)
          && <option value={definition.pinned_instrument}>{definition.pinned_instrument}</option>}
        {instruments.map(item => <option key={item.id} value={item.id}>{symbol(item)} · {instrumentName(item)}</option>)}
      </select></label>
    </fieldset>
  </>;
}

export function WorkspaceLayoutEditor({ state, layouts, instruments }: {
  state: LayoutState & { draft: Draft };
  layouts: NonNullable<NonNullable<LocalWorkspace['snapshot']>['layouts']>;
  instruments: readonly Instrument[];
}) {
  const heading = useId();
  const dialog = useRef<HTMLDialogElement>(null);
  const [saved, setSaved] = useState(layouts.active.name ?? layouts.saved[0] ?? '');
  const [parseError, setParseError] = useState<string>();
  const { draft } = state;
  const raw = draft.text !== undefined;
  useEffect(() => {
    const node = dialog.current;
    node?.showModal();
    return () => { node?.close(); };
  }, []);

  function previewDraft() {
    const name = draft.name.trim();
    if (!name) {
      setParseError('Enter a name for this layout.');
      return;
    }
    let definition = draft.definition;
    if (draft.text !== undefined) {
      try { definition = JSON.parse(draft.text) as WorkspaceLayout11; }
      catch { setParseError('The layout definition must be valid JSON.'); return; }
    }
    setParseError(undefined);
    state.requestPreview({ action: 'save', name, definition });
  }

  return <dialog className="symbol-dialog layout-dialog" ref={dialog} aria-labelledby={heading} onCancel={event => {
    event.preventDefault();
    if (!state.busy) state.close();
  }}>
    <div className="dialog-heading"><h2 id={heading}>Edit workspace layout</h2>
      <button type="button" className="secondary-button" disabled={state.busy} onClick={state.close}>Close</button></div>
    <div className="layout-editor" aria-busy={state.busy}>
      <form onSubmit={event => { event.preventDefault(); previewDraft(); }}>
        <fieldset className="layout-edit-form" disabled={state.busy}>
          <label>Layout name<input autoComplete="off" required maxLength={48} value={draft.name}
            onChange={event => state.changeDraft({ ...draft, name: event.currentTarget.value })} /></label>
          {raw ? <label>Layout definition<textarea rows={13} maxLength={8192} spellCheck={false} value={draft.text}
            onChange={event => state.changeDraft({ ...draft, text: event.currentTarget.value })} /></label>
            : <LayoutFields definition={draft.definition} instruments={instruments}
              change={definition => state.changeDraft({ ...draft, definition })} />}
          {(parseError || state.error) && <p className="layout-error" role="alert">{parseError ?? state.error}</p>}
          <div className="layout-actions">
            <button type="submit" className="primary">Preview layout</button>
            {!raw && <button type="button" className="secondary-button" onClick={() => {
              setParseError(undefined);
              state.changeDraft({ ...draft, text: JSON.stringify(draft.definition, null, 2) });
            }}>Edit layout definition</button>}
          </div>
        </fieldset>
      </form>
      <fieldset className="layout-fields" disabled={state.busy}>
        <legend>Saved layouts</legend>
        <label>Saved layout<select value={saved} onChange={event => setSaved(event.currentTarget.value)}>
          {!layouts.saved.length && <option value="">No saved layouts</option>}
          {layouts.saved.map(name => <option key={name} value={name}>{name}</option>)}
        </select></label>
        <div className="layout-actions">
          <button type="button" className="secondary-button" disabled={!saved}
            onClick={() => { setParseError(undefined); state.requestPreview({ action: 'open', name: saved }); }}>Preview saved layout</button>
          <button type="button" className="secondary-button"
            onClick={() => { setParseError(undefined); state.requestPreview({ action: 'reset' }); }}>Preview default layout</button>
          <button type="button" className="secondary-button" disabled={!layouts.undo}
            onClick={() => { setParseError(undefined); state.undo(); }}>Undo layout</button>
        </div>
      </fieldset>
    </div>
  </dialog>;
}

export function WorkspaceLayoutReview({ active, state }: { active: NamedWorkspaceLayout11; state: LayoutState }) {
  const apply = useRef<HTMLButtonElement>(null);
  useEffect(() => { if (state.preview) apply.current?.focus(); }, [state.preview]);
  const name = state.preview?.layout.name ?? (state.preview ? null : active.name);
  return <div className="layout-review" aria-label="Workspace layout" aria-busy={state.busy}>
    <p role="status">{state.preview ? 'Previewing' : 'Layout'} <strong>{name ?? 'Default layout'}</strong></p>
    {!state.editing && state.error && <p className="layout-error" role="alert">{state.error}</p>}
    {state.preview && <div className="layout-actions">
      <button ref={apply} type="button" className="primary" disabled={state.busy} onClick={state.apply}>Apply layout</button>
      <button type="button" className="secondary-button" disabled={state.busy} onClick={() => state.open(true)}>Edit preview</button>
      <button type="button" className="secondary-button" disabled={state.busy} onClick={state.discard}>Discard preview</button>
    </div>}
  </div>;
}
