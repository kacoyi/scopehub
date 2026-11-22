// The recovery control must remain available even if the React application fails to load.
import { catalogsByLocale, type Catalog } from './i18n/catalog';
import { getLocale } from './i18n/preference';

const messages = catalogsByLocale(import.meta.glob<Catalog>('./i18n/locales/*/startup.json', {
  eager: true, import: 'default',
})).get(getLocale());
function label(source: string) {
  const value = messages?.get(source);
  return typeof value === 'string' ? value : source;
}
const reload = document.querySelector<HTMLButtonElement>('#startup-reload');
const title = document.getElementById('startup-title');
const hint = document.getElementById('startup-hint');
if (reload && title && hint) {
  title.closest('main')?.setAttribute('aria-label', label('Workspace startup'));
  title.textContent = label('Opening ScopeHub…');
  reload.textContent = label('Reload window');
  if (!reload.disabled) {
    hint.textContent = label('If the workspace stays here, reload this window.');
    reload.addEventListener('click', () => { if (!reload.disabled) window.location.reload(); }, { once: true });
    reload.hidden = false;
  }
  window.setTimeout(() => {
    if (title.isConnected) title.textContent = label('The workspace hasn’t opened yet');
  }, 10_000);
}

void import('./main').catch((error: unknown) => {
  if (title?.isConnected) title.textContent = label('The workspace hasn’t opened yet');
  // A failed module fetch already has its network diagnostic and a visible recovery action.
  // Preserve evaluation exceptions for the host's ordinary error reporting.
  if (error instanceof TypeError && /failed to fetch dynamically imported module|importing a module script failed|error loading dynamically imported module/i.test(error.message)) return;
  throw error;
});
