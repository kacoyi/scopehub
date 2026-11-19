import type { ChartCursorMode, ChartStyle } from './chart-adapter/history';
import type { Panel } from './workspace-panels';

// This versioned presentation preference belongs only to the built-in sample.
// Configured sources, grants and durable user workspaces belong to the core.
const storageKey = 'scopehub.preview.sample.v1';
export const narrowScreen = '(max-width: 760px)';

export interface SampleView {
  symbol: string;
  timeframe: string;
  style: ChartStyle;
  cursor: ChartCursorMode;
  panel: Panel | null;
  narrow: boolean;
  chartOnly: boolean;
}

export function readSampleView(): Partial<SampleView> | undefined {
  try {
    const raw = window.localStorage.getItem(storageKey);
    if (!raw || raw.length > 1024) return undefined;
    const saved = JSON.parse(raw);
    if (!saved || saved.source !== 'scopehub.synthetic') return undefined;
    const view: Partial<SampleView> = {};
    if (typeof saved.symbol === 'string' && /^SIM\.[.A-Z0-9]{1,80}$/.test(saved.symbol)) view.symbol = saved.symbol;
    if (['1m', '5m', '1d'].includes(saved.timeframe)) view.timeframe = saved.timeframe;
    if (['candlestick', 'line'].includes(saved.style)) view.style = saved.style;
    if (['crosshair', 'pan'].includes(saved.cursor)) view.cursor = saved.cursor;
    if ([null, 'watchlist', 'data', 'sources'].includes(saved.panel)) view.panel = saved.panel;
    if (typeof saved.narrow === 'boolean') view.narrow = saved.narrow;
    if (typeof saved.chartOnly === 'boolean') view.chartOnly = saved.chartOnly;
    return view;
  } catch { return undefined; }
}

export function saveSampleView(view: SampleView | null) {
  try {
    if (view) window.localStorage.setItem(storageKey, JSON.stringify({ source: 'scopehub.synthetic', ...view }));
    else window.localStorage.removeItem(storageKey);
  } catch { /* A blocked local store still allows a sample for the current visit. */ }
}
