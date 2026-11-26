import type { InstrumentSearchOutput, MarketHistoryPageOutput } from '../../../../contracts/generated/protocol';
import { isInstrumentSearchOutput, isMarketHistoryPageOutput } from '../../../../contracts/generated/market-output-validators.mjs';

export type MarketOutputs = {
  'instrument.search': InstrumentSearchOutput;
  'market.readHistoryPage': MarketHistoryPageOutput;
};

const validators = {
  'instrument.search': isInstrumentSearchOutput,
  'market.readHistoryPage': isMarketHistoryPageOutput,
};

export function isMarketOutput<K extends keyof MarketOutputs>(operation: K, value: unknown): value is MarketOutputs[K] {
  return validators[operation](value);
}
