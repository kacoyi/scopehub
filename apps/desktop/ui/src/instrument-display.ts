import { formatNumber, t } from './i18n';
import type { Instrument } from './market';

const sampleNames = new Map([
  ['SIM.CN.FUTURE.001', 'China futures'],
  ['SIM.CN.STOCK.001', 'China stock'],
  ['SIM.US.STOCK.001', 'US stock'],
]);

const assetName = (instrument: Instrument) => instrument.asset_class === 'future' ? 'Futures' : 'Stock';

export function instrumentName(instrument: Instrument) {
  const sampleName = sampleNames.get(instrument.id);
  return sampleName ? t(sampleName) : instrument.name;
}
export const symbol = (instrument: Instrument) => instrument.display_symbol.replace(/^SIM\./, '');
export const assetLabel = (instrument: Instrument) => t(assetName(instrument));

export function instrumentSearchTerms(instrument: Instrument) {
  const name = sampleNames.get(instrument.id);
  const asset = assetName(instrument);
  return [instrument.id, instrument.name, instrument.currency, asset, t(asset), ...(name ? [name, t(name)] : [])];
}

export function unitLabel(unit: string, appearance?: 'lowercase') {
  if (unit === 'contracts') return appearance === 'lowercase' ? t('contracts') : t('Contracts');
  if (unit === 'shares') return appearance === 'lowercase' ? t('shares') : t('Shares');
  return unit;
}

export function price(value: number, instrument: Instrument) {
  if (instrument.price_tick == null) return formatNumber(value, { maximumSignificantDigits: 21 });
  const digits = instrument.price_tick.split('.')[1]?.length ?? 0;
  return formatNumber(value, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}
