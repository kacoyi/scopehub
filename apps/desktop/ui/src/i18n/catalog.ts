export type MessageValues = Readonly<Record<string, string | number>>;
export type Message = { readonly id: string; readonly values?: MessageValues; readonly context?: string };
type PluralForms = Partial<Record<Intl.LDMLPluralRule, string>> & { other: string };
export type Catalog = Readonly<Record<string, string | PluralForms>>;

export function message(id: string, values?: MessageValues, context?: string): Message {
  return { id, ...(values && { values }), ...(context && { context }) };
}

export function catalogsByLocale(files: Record<string, Catalog>) {
  const locales = new Map<string, Map<string, string | PluralForms>>();
  for (const [path, catalog] of Object.entries(files)) {
    const tag = path.split('/').at(-2)!;
    const entries = locales.get(tag) ?? new Map<string, string | PluralForms>();
    for (const [id, translation] of Object.entries(catalog)) entries.set(id, translation);
    locales.set(tag, entries);
  }
  return locales;
}

export function interpolate(template: string, values?: MessageValues) {
  if (!values) return template;
  return template.replace(/\{(\w+)\}/g, (placeholder, key: string) =>
    Object.hasOwn(values, key) ? String(values[key]) : placeholder);
}
