export function priceTickFormat(tick: string | null): Intl.NumberFormatOptions {
  const digits = tick?.split('.')[1]?.replace(/0+$/, '').length ?? 0;
  // Decimal spelling does not change the tick. Use the portable Intl fraction limit.
  return tick === null || digits > 20
    ? { maximumSignificantDigits: 21 }
    : { minimumFractionDigits: digits, maximumFractionDigits: digits };
}
