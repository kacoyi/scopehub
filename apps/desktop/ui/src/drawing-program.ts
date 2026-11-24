import { sources } from './workbench-composition';

export type Drawing = { source: keyof typeof sources; mode: 'free' | 'overlay'; code: string };
export type Mark =
  | ['circle', number, number, number, number]
  | ['line' | 'rect', number, number, number, number, number];

export const programLimit = 900;
export const markLimit = 256;
export const width = 640;
export const height = 240;

export function readProgram(text: string): Drawing | undefined {
  if (text.length > programLimit + 32) return;
  const trimmed = text.trim();
  const code = /^\x60\x60\x60(?:javascript|js)?\s*\n([\s\S]+?)\n\x60\x60\x60$/i.exec(trimmed)?.[1]?.trim() ?? trimmed;
  const header = /^\/\/ (stock|future) (free|overlay)\r?\n/.exec(code);
  if (header && code.length <= programLimit) {
    return { source: header[1] as Drawing['source'], mode: header[2] as Drawing['mode'], code };
  }
}

export function drawingPrompt(accepted?: Drawing) {
  return [
    'Write a complete synthetic drawing in plain JavaScript, max 900 characters. First line: // <stock|future> <free|overlay>.',
    'rows contains two frozen 1m bars with time_ms,open,high,low,close,volume. Use for/if/Math for geometry only.',
    'Viewport 640x240. xTime(time_ms), yValue(price) map to the shared price overlay. Free mode has no axes.',
    'Draw with line(x1,y1,x2,y2,ink), circle(x,y,r,ink), rect(x,y,w,h,ink). ink=0..3. Local stroke weight is adjustable.',
    'No imports, tools or indicators. Return source only. Accepted program:', accepted?.code ?? 'none', 'User intent:',
  ].join('\n') + '\n';
}

export type ViewBounds = { from: number; to: number; low: number; high: number };

// This candidate admits only the two non-empty, pinned fixtures. A wider
// host window changes both domains without changing either series or program.
export function viewBounds(source: Drawing['source'], expanded: boolean): ViewBounds {
  const bars = sources[source].bars;
  const from = bars[0]!.time_ms;
  const to = bars[bars.length - 1]!.time_ms;
  const low = Math.min(...bars.map(bar => bar.low));
  const high = Math.max(...bars.map(bar => bar.high));
  const timePad = from === to ? 30_000 : expanded ? (to - from) / 2 : 0;
  const pricePad = low === high ? 1 : expanded ? (high - low) / 2 : 0;
  return { from: from - timePad, to: to + timePad, low: low - pricePad, high: high + pricePad };
}

export function viewport({ from, to, low, high }: ViewBounds) {
  return {
    xTime: (time: number) => 64 + (time - from) / (to - from) * (width - 88),
    yValue: (value: number) => height - 32 - (value - low) / (high - low) * (height - 56),
  };
}

// Only these fixed numeric tuples can cross into the host's SVG tree.
export function validMark(value: unknown): value is Mark {
  if (!Array.isArray(value)) return false;
  const [kind, x, y, a, b] = value;
  if (!value.slice(1).every(n => Number.isFinite(n) && Math.abs(n) <= 4096)) return false;
  const ink = value.at(-1);
  if (!Number.isInteger(ink) || ink < 0 || ink > 3) return false;
  if (kind === 'circle') return value.length === 5 && a > 0;
  if (kind === 'rect') return value.length === 6 && a > 0 && b > 0;
  return kind === 'line' && value.length === 6 && (x !== a || y !== b);
}
