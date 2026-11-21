import { useEffect, useState, type KeyboardEvent, type ReactNode } from 'react';
import type { LayoutPanel11, WorkspaceLayout11 } from '../../../../contracts/generated/protocol';
import { price, symbol } from './instrument-display';
import { readSampleHistory, type ChartHistory, type Instrument } from './market';
import { layoutPanelLabels } from './workspace-layout';

type Dock = LayoutPanel11['dock'];
type Panel = LayoutPanel11['panel'];
type OpenDocks = Partial<Record<Dock, Panel | null>>;

export function useLayoutDocks(layout: WorkspaceLayout11 | undefined, narrow: boolean) {
  const [open, setOpen] = useState<OpenDocks>({});
  useEffect(() => { setOpen({}); }, [narrow]);

  function selected(dock: Dock) {
    const panels = layout?.panels.filter(value => value.dock === dock) ?? [];
    const current = open[dock];
    return {
      panels, active: current === null ? undefined : panels.find(value => value.panel === current) ?? (narrow ? undefined : panels[0]),
    };
  }

  function setDock(dock: Dock, panel: Panel | null) {
    setOpen(value => ({ ...(narrow ? { left: null, right: null, bottom: null } : value), [dock]: panel }));
  }

  return { selected, setDock, collapse: () => setOpen({ left: null, right: null, bottom: null }) };
}

export function LayoutDock({ dock, panels, active, select, close, children }: {
  dock: Dock;
  panels: readonly LayoutPanel11[];
  active: Panel;
  select: (panel: Panel) => void;
  close: () => void;
  children: ReactNode;
}) {
  const tabs = panels.length > 1;

  function changeTab(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const keys: Record<string, number> = {
      ArrowLeft: (index + panels.length - 1) % panels.length, ArrowRight: (index + 1) % panels.length,
      Home: 0, End: panels.length - 1,
    };
    const panel = panels[keys[event.key] ?? -1];
    if (!panel) return;
    event.preventDefault();
    select(panel.panel);
    event.currentTarget.parentElement?.querySelector<HTMLButtonElement>(`#layout-tab-${panel.panel}`)?.focus();
  }

  return <div className={`layout-dock layout-dock-${dock}`} data-layout-dock={dock} onKeyDown={event => {
    if (event.key === 'Escape') {
      event.preventDefault();
      close();
    }
  }}>
    {tabs && <div className="layout-tabs" role="tablist" aria-label={`${dock[0]?.toUpperCase()}${dock.slice(1)} panels`}>
      {panels.map((panel, index) => <button type="button" key={panel.panel} id={`layout-tab-${panel.panel}`}
        role="tab" aria-selected={active === panel.panel} aria-controls={`layout-section-${panel.panel}`}
        tabIndex={active === panel.panel ? 0 : -1} onClick={() => select(panel.panel)}
        onKeyDown={event => changeTab(event, index)}>{layoutPanelLabels[panel.panel]}</button>)}
    </div>}
    {tabs ? panels.map(panel => <div key={panel.panel} id={`layout-section-${panel.panel}`} role="tabpanel"
      aria-labelledby={`layout-tab-${panel.panel}`} hidden={active !== panel.panel}>{active === panel.panel && children}</div>) : children}
  </div>;
}

export function PinnedComparison({ id, instrument, chartReady }: {
  id: string;
  instrument: Instrument | undefined;
  chartReady: boolean;
}) {
  const [loaded, setLoaded] = useState<{ id: string; history?: ChartHistory }>();
  const resolved = loaded?.id === id && loaded.history;
  useEffect(() => {
    if (!instrument || !chartReady || resolved) return;
    // The main chart owns market admission. Fetch the pinned daily comparison once its history settles.
    const controller = new AbortController();
    void readSampleHistory(instrument, '1d', 'normal', '', controller.signal).then(history => {
      if (!controller.signal.aborted) setLoaded({ id, history });
    }, () => {
      if (!controller.signal.aborted) setLoaded({ id });
    });
    return () => controller.abort();
  }, [id, instrument, chartReady, resolved]);
  const last = resolved ? resolved.bars.at(-1) : undefined;
  return <section className="layout-comparison" aria-label="Pinned comparison">
    <h2>Pinned comparison</h2>
    <strong>{instrument ? symbol(instrument) : id.replace(/^SIM\./, '')}</strong>
    {instrument ? <span>{last ? `Sample daily close ${price(last.close, instrument)} ${instrument.currency} · ${last.trading_day}`
      : loaded?.id === id ? 'Sample close unavailable' : 'Loading sample comparison…'}</span>
      : <span>Comparison source unavailable</span>}
  </section>;
}
