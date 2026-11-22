import definitions from './languages.json';

export const languages = definitions;
export const defaultLocale = 'en';
const storageKey = 'scopehub.ui.language.v1';

function languageFor(tag: string) {
  try {
    const requested = new Intl.Locale(tag);
    const exact = languages.find(language => language.tag === requested.baseName);
    if (exact) return exact;
    const script = requested.maximize().script;
    return languages.find(language => {
      const candidate = new Intl.Locale(language.tag);
      return candidate.language === requested.language && candidate.maximize().script === script;
    });
  } catch { return undefined; }
}

export function resolveLocale(preference: string, preferred: readonly string[]): string {
  const explicit = languages.find(language => language.tag === preference);
  if (explicit) return explicit.tag;
  for (const tag of preferred) {
    const language = languageFor(tag);
    if (language) return language.tag;
  }
  return defaultLocale;
}

function storedPreference() {
  try {
    const tag = localStorage.getItem(storageKey);
    if (tag !== null && languages.some(language => language.tag === tag)) return tag;
  } catch { /* This profile can still choose a language for the current session. */ }
  return 'system';
}

let preference = storedPreference();
function effectiveLocale() {
  return resolveLocale(preference, navigator.languages?.length ? navigator.languages : [navigator.language]);
}

let locale = effectiveLocale();
export const getLocale = () => locale;
export const getLocalePreference = () => preference;
const listeners = new Set<() => void>();

function setDocumentLanguage() {
  document.documentElement.lang = locale;
  document.documentElement.dir = languages.find(language => language.tag === locale)?.direction ?? 'ltr';
}

function update(value: string) {
  const previous = preference;
  preference = value;
  const resolved = effectiveLocale();
  if (resolved === locale && previous === value) return;
  locale = resolved;
  setDocumentLanguage();
  for (const notify of listeners) notify();
}

export function setLocalePreference(value: string) {
  if (value !== 'system' && !languages.some(language => language.tag === value)) return;
  try {
    if (value === 'system') localStorage.removeItem(storageKey);
    else localStorage.setItem(storageKey, value);
  } catch { /* The in-memory preference still applies. */ }
  update(value);
}

export function subscribeLocale(notify: () => void) {
  listeners.add(notify);
  return () => { listeners.delete(notify); };
}

const systemChanged = () => update(preference);
const storageChanged = (event: StorageEvent) => {
  if (event.key === storageKey || event.key === null) update(storedPreference());
};
setDocumentLanguage();
window.addEventListener('languagechange', systemChanged);
window.addEventListener('storage', storageChanged);
if (import.meta.hot) import.meta.hot.dispose(() => {
  window.removeEventListener('languagechange', systemChanged);
  window.removeEventListener('storage', storageChanged);
});
