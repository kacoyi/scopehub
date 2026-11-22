import { catalogsByLocale, type Catalog } from './i18n/catalog';
import { getLocale, subscribeLocale } from './i18n/preference';

const catalogs = catalogsByLocale(import.meta.glob<Catalog>('./i18n/locales/*/startup.json', {
  eager: true, import: 'default',
}));
const reload = document.querySelector<HTMLButtonElement>('#startup-reload');
const title = document.getElementById('startup-title');
const hint = document.getElementById('startup-hint');
let stalled = false;

function renderStartup() {
  if (!title?.isConnected || !hint || !reload) return;
  const catalog = catalogs.get(getLocale());
  function label(source: string) {
    const value = catalog?.get(source);
    return typeof value === 'string' ? value : source;
  }
  title.closest('main')?.setAttribute('aria-label', label('Workspace startup'));
  title.textContent = stalled ? label('The workspace hasn’t opened yet') : label('Opening ScopeHub…');
  hint.textContent = reload.disabled ? label('Reopen the private workspace access document to try again.')
    : label('If the workspace stays here, reload this window.');
  reload.textContent = label('Reload window');
}

function showDelay() {
  stalled = true;
  renderStartup();
}

renderStartup();
const unsubscribe = subscribeLocale(renderStartup);
const timer = window.setTimeout(showDelay, 10_000);
void import('./main').then(() => {
  window.clearTimeout(timer);
  unsubscribe();
}, (error: unknown) => {
  window.clearTimeout(timer);
  showDelay();
  // Module fetch failures already have a network diagnostic. Report evaluation exceptions as usual.
  if (error instanceof TypeError && /failed to fetch dynamically imported module|importing a module script failed|error loading dynamically imported module/i.test(error.message)) return;
  throw error;
});
