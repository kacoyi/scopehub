import { sources, type NumericField } from './workbench-composition';

export type Scalar = { name: string; min: number; max: number; step: number; initial: number };
export type Drawing = {
  source: keyof typeof sources; mode: 'free' | 'overlay'; code: string;
  scalars: Scalar[]; difference?: { field: NumericField; parameter: string };
};
export type Controls = { params: Record<string, number>; selected: number };
export type Target = [index: number, x: number, y: number, radius: number];

export function validTarget(value: unknown, count: number): value is Target {
  return Array.isArray(value) && value.length === 4 && value.every(Number.isFinite)
    && Number.isInteger(value[0]) && value[0] >= 0 && value[0] < count
    && Math.abs(value[1]) <= 4096 && Math.abs(value[2]) <= 4096 && value[3] >= 1 && value[3] <= 40;
}

export function allowedValue(scalar: Scalar, value: number) {
  const ticks = (value - scalar.min) / scalar.step;
  return Number.isFinite(value) && value >= scalar.min && value <= scalar.max && Math.abs(ticks - Math.round(ticks)) < 1e-7;
}
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
    const result: Drawing = { source: header[1] as Drawing['source'], mode: header[2] as Drawing['mode'], code, scalars: [] };
    for (const line of code.split(/\r?\n/).slice(1)) {
      const [comment, directive, ...args] = line.trim().split(/\s+/);
      if (comment !== '//' || !['scalar', 'difference'].includes(directive ?? '')) continue;
      const [name, ...numbers] = args;
      if (directive === 'scalar') {
        if (!name || !/^[a-z][a-zA-Z0-9]{0,19}$/.test(name) || name in Object.prototype
          || numbers.length !== 4 || numbers.some(n => !/^-?(?:0|[1-9]\d*)(?:\.\d+)?$/.test(n))
          || result.scalars.length >= 3 || result.scalars.some(s => s.name === name)) return;
        const [min, max, step, initial] = numbers.map(Number) as [number, number, number, number];
        const scalar = { name, min, max, step, initial };
        if (Math.max(Math.abs(min), Math.abs(max)) > 1e9 || min >= max || step <= 0 || step > max - min
          || (max - min) / step > 10000 || !allowedValue(scalar, initial) || !allowedValue(scalar, max)) return;
        result.scalars.push(scalar);
      } else {
        if (result.difference || args.length !== 2 || !['open','high','low','close','volume'].includes(name ?? '')
          || !result.scalars.some(s => s.name === args[1])) return;
        result.difference = { field: name as NumericField, parameter: args[1]! };
      }
    }
    return result;
  }
}

export function drawingPrompt(accepted?: Drawing) {
  return [
    'Write a complete synthetic drawing in plain JavaScript, max 900 characters. First line: // <stock|future> <free|overlay>.',
    'rows contains two frozen 1m bars with time_ms,open,high,low,close,volume. Use for/if/Math for geometry only.',
    'Viewport 640x240. xTime(time_ms), yValue(price) map to the shared price overlay. Free mode has no axes.',
    'Draw with line(x1,y1,x2,y2,ink), circle(x,y,r,ink), rect(x,y,w,h,ink). ink=0..3. Local stroke weight is adjustable.',
    'Optional // scalar name min max step default; read params.name. hit(rowIndex,x,y,r) binds a selectable area; selected is its index or -1.',
    '// difference field name requests a host-calculated selected field minus that scalar. No guest readout formulas. Accepted program:', accepted?.code ?? 'none', 'User intent:',
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
