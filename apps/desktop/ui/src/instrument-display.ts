import type { Instrument } from './market';

const sampleNames: Record<string, string> = {
  'SIM.CN.FUTURE.001': 'China futures',
  'SIM.CN.STOCK.001': 'China stock',
  'SIM.US.STOCK.001': 'US stock',
};

export const instrumentName = (instrument: Instrument) => sampleNames[instrument.id] ?? instrument.name;
export const symbol = (instrument: Instrument) => instrument.display_symbol.replace(/^SIM\./, '');
export const assetLabel = (instrument: Instrument) => instrument.asset_class === 'future' ? 'Futures' : 'Stock';
export const unitLabel = (unit: string) => unit === 'contracts' ? 'Contracts' : unit === 'shares' ? 'Shares' : unit;

export function price(value: number, instrument: Instrument) {
  const digits = instrument.price_tick?.split('.')[1]?.length ?? 0;
  return value.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}
