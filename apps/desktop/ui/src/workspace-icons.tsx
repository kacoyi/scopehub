const paths = {
  search: <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 4.5 4.5" /></>,
  crosshair: <><path d="M12 2v6m0 8v6M2 12h6m8 0h6" /><circle cx="12" cy="12" r="2.1" /></>,
  pan: <path d="M7.4 12.4V6.8a1.5 1.5 0 0 1 3 0V11 4.8a1.5 1.5 0 0 1 3 0V11 6.1a1.5 1.5 0 0 1 3 0v5.5-3.2a1.5 1.5 0 0 1 3 0v5.5c0 4.8-2.4 7.1-6.2 7.1h-.8c-1.8 0-3.2-.9-4.4-2.5l-4.3-5.4a1.7 1.7 0 0 1 2.3-2.4z" />,
  zoomIn: <><circle cx="10" cy="10" r="6.4" /><path d="m15 15 5.6 5.6M7.5 10h5M10 7.5v5" /></>,
  zoomOut: <><circle cx="10" cy="10" r="6.4" /><path d="m15 15 5.6 5.6M7.5 10h5" /></>,
  fit: <><path d="M3.5 9V4h5m7 0h5v5m0 6v5h-5m-7 0h-5v-5M8 12h8" /></>,
  candles: <><path d="M7 2v4m0 10v5m10-18v7m0 7v5" /><rect x="4.5" y="6" width="5" height="10" rx=".7" /><rect x="14.5" y="10" width="5" height="7" rx=".7" /></>,
  line: <><path d="m3 16 5.5-7 4.2 3.4L21 5" /></>,
  watchlist: <><rect x="4.5" y="3.5" width="15" height="17" rx="1.8" /><path d="M8 8h1m2.5 0h4.7M8 12h1m2.5 0h4.7M8 16h1m2.5 0h4.7" /></>,
  workbench: <><rect x="3" y="4" width="18" height="16" rx="1.5" /><path d="M10 4v16m0-9h11" /></>,
  table: <><rect x="3" y="4" width="18" height="16" rx="1.5" /><path d="M3 9h18M3 14h18M9 4v16" /></>,
  database: <><ellipse cx="12" cy="5.5" rx="8" ry="3.3" /><path d="M4 5.5v11.7c0 1.8 3.6 3.3 8 3.3s8-1.5 8-3.3V5.5M4 11.4c0 1.8 3.6 3.3 8 3.3s8-1.5 8-3.3" /></>,
  close: <path d="m6 6 12 12M18 6 6 18" />,
  chevron: <path d="m8 10 4 4 4-4" />,
  expand: <path d="M8 3H3v5m13-5h5v5m0 8v5h-5M8 21H3v-5" />,
  collapse: <path d="M3 8h5V3m8 0v5h5m-5 13v-5h5M8 21v-5H3" />,
  chart: <><path d="M4 3v17h17M7 15l4-6 4 2 5-6" /></>,
  arrow: <path d="M4 12h15m-6-6 6 6-6 6" />,
};

export type WorkspaceIconName = keyof typeof paths;
export function WorkspaceIcon({ name }: { name: WorkspaceIconName }) {
  return <svg className="workspace-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}
