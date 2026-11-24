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
export type AnnotationBinding = {
  field: NumericField;
  when: { field: NumericField; above: number };
};

export type CandleBlock = { kind: 'candlestick'; source: typeof candleSource.id; annotation: AnnotationBinding };

export function isNumericField(value: unknown): value is NumericField {
  return typeof value === 'string' && Object.hasOwn(candleSource.fields, value);
}
