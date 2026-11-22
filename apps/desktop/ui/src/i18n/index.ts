import { useSyncExternalStore } from 'react';
import { catalogsByLocale, interpolate, type Catalog, type Message, type MessageValues } from './catalog';
import { defaultLocale, getLocale, getLocalePreference, subscribeLocale } from './preference';

export { message, type Message, type MessageValues } from './catalog';
export { getLocale, getLocalePreference, languages, setLocalePreference } from './preference';

const catalogs = catalogsByLocale(import.meta.glob<Catalog>('./locales/*/*.json', { eager: true, import: 'default' }));
const pluralRules = new Map<string, Intl.PluralRules>();
function category(locale: string, count: number) {
  let rules = pluralRules.get(locale);
  if (!rules) { rules = new Intl.PluralRules(locale); pluralRules.set(locale, rules); }
  return rules.select(count);
}

// English source messages are also the per-message fallback. Only pass application-owned copy.
export function t(source: string | Message, values?: MessageValues, context?: string): string {
  const id = typeof source === 'string' ? source : source.id;
  const meaning = typeof source === 'string' ? context : source.context;
  const translated = catalogs.get(getLocale())?.get(meaning ? meaning + '::' + id : id);
  return interpolate(typeof translated === 'string' ? translated : id, typeof source === 'string' ? values : source.values);
}

export function plural(one: string, other: string, count: number, values?: MessageValues): string {
  const locale = getLocale();
  const translated = catalogs.get(locale)?.get(one);
  const template = typeof translated === 'string' ? translated
    : translated?.[category(locale, count)] ?? translated?.other
      ?? (category(defaultLocale, count) === 'one' ? one : other);
  return interpolate(template, { ...values, count: formatNumber(count) });
}

export const useLocale = () => useSyncExternalStore(subscribeLocale, getLocale);
export const useLocalePreference = () => useSyncExternalStore(subscribeLocale, getLocalePreference);

const numbers = new Map<string, Intl.NumberFormat>();
const dates = new Map<string, Intl.DateTimeFormat>();
type DisplayNumber = number | `${bigint}e${number}`;
export function formatNumber(value: DisplayNumber, options?: Intl.NumberFormatOptions): string {
  const locale = getLocale();
  const key = locale + JSON.stringify(options ?? {});
  let formatter = numbers.get(key);
  if (!formatter) { formatter = new Intl.NumberFormat(locale, options); numbers.set(key, formatter); }
  // Intl can format an exact decimal without converting it back to binary floating point.
  return (formatter.format as (input: DisplayNumber) => string)(value);
}
export function formatDateTime(value: number | Date, options: Intl.DateTimeFormatOptions): string {
  const locale = getLocale();
  const key = locale + JSON.stringify(options);
  let formatter = dates.get(key);
  if (!formatter) { formatter = new Intl.DateTimeFormat(locale, options); dates.set(key, formatter); }
  return formatter.format(value);
}
