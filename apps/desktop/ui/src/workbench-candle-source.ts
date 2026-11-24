import type { Candle } from './chart-adapter/candle-annotations';

type Fields = { strength: number | null; turnover: number | null };
type Observation = Candle & Fields;

const start = Date.UTC(2024, 0, 4, 9, 30);

// Frozen synthetic projection of the qualification inputs, with illustrative OHLC.
// Revisions are prototype identities for these exact inputs.
export const candleSource = {
  id: 'qualification-equity',
  instrument: 'SIM.EQUITY.QUALIFICATION.001',
  fields: {
    strength: { label: 'Volume anomaly strength', unit: 'Unitless' },
    turnover: { label: 'Turnover', unit: 'Fraction' },
  },
  frames: {
    '5m': {
      seriesRevision: 'qualification-equity-5m-bars-v1',
      resultRevision: 'qualification-equity-5m-results-v1',
      asOfMs: start + 20 * 60_000,
      observations: [
        { timeMs: start, open: 106, high: 107, low: 101, close: 102, strength: 0.15, turnover: 0.004 },
        { timeMs: start + 5 * 60_000, open: 102, high: 109, low: 101, close: 107, strength: 0.50, turnover: 0.011 },
        { timeMs: start + 10 * 60_000, open: 107, high: 116, low: 105, close: 113, strength: 0.95, turnover: 0.032 },
        { timeMs: start + 15 * 60_000, open: 113, high: 115, low: 106, close: 108, strength: null, turnover: null },
      ] satisfies readonly Observation[],
    },
    '15m': {
      seriesRevision: 'qualification-equity-15m-bars-v1',
      resultRevision: 'qualification-equity-15m-results-v1',
      asOfMs: start + 15 * 60_000,
      observations: [
        { timeMs: start, open: 106, high: 116, low: 101, close: 113, strength: 0.90, turnover: 0.047 },
      ] satisfies readonly Observation[],
    },
  },
} as const;

export type NumericField = keyof Fields;
export type CandleTimeframe = keyof typeof candleSource.frames;
export type AnnotationBinding = {
  field: NumericField;
  when: { field: NumericField; above: number };
};

export type CandleBlock = { kind: 'candlestick'; source: typeof candleSource.id; annotation: AnnotationBinding };

type ReplayQuote = Readonly<{
  source: string; sequence: number; asOfMs: number; candle: Observation;
}>;
const replayOpen = start + 20 * 60_000;
const firstQuote: ReplayQuote = {
  source: candleSource.id, sequence: 1, asOfMs: replayOpen + 60_000,
  candle: { timeMs: replayOpen, open: 108, high: 111, low: 107, close: 110, strength: 0.84, turnover: 0.038 },
};
const secondQuote: ReplayQuote = {
  source: candleSource.id, sequence: 2, asOfMs: replayOpen + 180_000,
  candle: { timeMs: replayOpen, open: 108, high: 112, low: 105, close: 106, strength: 0.78, turnover: 0.040 },
};
const finalQuote: ReplayQuote = {
  source: candleSource.id, sequence: 3, asOfMs: replayOpen + 5 * 60_000,
  candle: { timeMs: replayOpen, open: 108, high: 115, low: 105, close: 114, strength: 0.93, turnover: 0.045 },
};

const tape: readonly ReplayQuote[] = [
  firstQuote,
  // A previously seen sequence and an unrelated source would both visibly corrupt the chart if admitted.
  { ...firstQuote, candle: { ...firstQuote.candle, close: 109, turnover: 0.099 } },
  { ...secondQuote, source: 'ungranted-synthetic-source', candle: { ...secondQuote.candle, turnover: 0.088 } },
  secondQuote,
  finalQuote,
];
export const candleReplay = { barOpenMs: replayOpen, intervalMs: 650, steps: tape.length, finalSequence: finalQuote.sequence };
export type CandleReplayPosition = { step: number; quote?: ReplayQuote; rejected?: 'stale' | 'source' };

export function advanceCandleReplay(position: CandleReplayPosition, source: CandleBlock['source']): CandleReplayPosition {
  const incoming = tape[position.step];
  if (!incoming) return position;
  const step = position.step + 1;
  if (incoming.source !== source) return { ...position, step, rejected: 'source' };
  if (incoming.sequence !== (position.quote?.sequence ?? 0) + 1) return { ...position, step, rejected: 'stale' };
  return { step, quote: incoming };
}

export function isNumericField(value: unknown): value is NumericField {
  return typeof value === 'string' && Object.hasOwn(candleSource.fields, value);
}
