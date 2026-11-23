import { setTimeout as delay } from 'node:timers/promises';
import { expect, test } from '@playwright/test';
import { openDesktopPreview } from '../ui/preview-startup.mjs';
import { openDefaultSample } from '../ui/sample-startup.mjs';
import { control, openPanel } from '../ui/workspace-controls.mjs';

const preference = 'scopehub.ui.language.v1';

async function chooseLanguage(page, current, target) {
  await control(page, current).click();
  const dialog = page.getByRole('dialog', { name: current, exact: true });
  await dialog.getByRole('combobox').selectOption(target);
  await page.keyboard.press('Escape');
  await expect(page.locator('.language-dialog:modal')).toHaveCount(0);
}

test.describe('language preference and recovery', () => {
  test.use({ locale: 'zh-CN' });

  test('follows the system, persists an override across windows, and restores system selection', async ({ page, context, baseURL }) => {
    await openDesktopPreview(page, baseURL);
    await expect(page.locator('html')).toHaveAttribute('lang', 'zh-Hans');
    await expect(control(page, '打开离线示例')).toBeVisible();
    await control(page, '语言设置').click();
    const settings = page.getByRole('dialog', { name: '语言设置' });
    const language = settings.getByRole('combobox', { name: '界面语言' });
    await expect(language).toHaveValue('system');
    await expect(language.getByRole('option', { name: 'English' })).toHaveCount(1);
    await expect(language.getByRole('option', { name: '简体中文' })).toHaveCount(1);
    await language.selectOption('en');
    await control(page, 'Close').click();
    await expect(control(page, 'Language settings')).toBeFocused();
    await expect(control(page, 'Open offline sample')).toBeVisible();
    await page.close();

    const reopened = await context.newPage();
    await reopened.goto('/');
    await expect(control(reopened, 'Open offline sample')).toBeVisible();
    await expect(reopened.locator('html')).toHaveAttribute('lang', 'en');
    await chooseLanguage(reopened, 'Language settings', 'system');
    await expect(control(reopened, '打开离线示例')).toBeVisible();
    expect(await reopened.evaluate(key => localStorage.getItem(key), preference)).toBeNull();
    await reopened.reload();
    await expect(control(reopened, '语言设置')).toBeVisible();
  });

  test('opens in the system language after a delayed application entry', async ({ page, baseURL }) => {
    await page.route('**/src/main.tsx', async route => {
      // Exceed the old 5 s control assertion within the existing 10 s startup budget.
      await delay(6_200);
      await route.continue();
    }, { times: 1 });
    await openDesktopPreview(page, baseURL);
    await expect(page.locator('html')).toHaveAttribute('lang', 'zh-Hans');
    await expect(control(page, '打开离线示例')).toBeVisible();
  });

  test('keeps a localized reload action when the application entry cannot load', async ({ page, baseURL }) => {
    await page.route('**/src/main.tsx', route => route.abort('connectionfailed'));
    await expect(openDesktopPreview(page, baseURL)).rejects.toThrow(
      /\/src\/main\.tsx failed \(net::ERR_CONNECTION_FAILED\)/,
    );
    const startup = page.getByRole('main', { name: '工作台启动' });
    await expect(startup.getByText('如果工作台一直停在这里，请重新加载此窗口。')).toBeVisible();
    const reload = startup.getByRole('button', { name: '重新加载窗口' });
    await expect(reload).toBeEnabled();
    await page.unroute('**/src/main.tsx');
    await reload.click();
    await expect(control(page, '打开离线示例')).toBeVisible();
  });
});

test('can reload the original URL when the first application module is unavailable', async ({ page }) => {
  await page.route('**/src/workspace-start.ts', route => route.abort('connectionfailed'));
  await page.goto('/?view=main#demo');
  const startup = page.getByRole('main', { name: 'Workspace startup' });
  const reload = startup.getByRole('button', { name: 'Reload window' });
  await expect(reload).toBeVisible();
  const address = page.url();
  await page.unroute('**/src/workspace-start.ts');
  await reload.click();
  await expect(control(page, 'Language settings')).toBeVisible();
  expect(page.url()).toBe(address);
});

test('retranslates the recovery page from another window while the main application is unavailable', async ({ page, context }) => {
  await page.route('**/src/main.tsx', route => route.abort('connectionfailed'));
  await page.goto('/');
  const startup = page.getByRole('main', { name: 'Workspace startup' });
  await expect(startup.getByRole('heading')).toHaveText('The workspace hasn’t opened yet');
  const settings = await context.newPage();
  await settings.goto('/');
  await chooseLanguage(settings, 'Language settings', 'zh-Hans');
  const recovered = page.getByRole('main', { name: '工作台启动' });
  await expect(recovered.getByRole('heading')).toHaveText('工作台尚未打开');
  await expect(recovered.getByRole('button', { name: '重新加载窗口' })).toBeEnabled();
});

test('updates the chart, an open panel and a draft without repeating market operations', async ({ page, context }) => {
  const reads = [];
  page.on('request', request => {
    if (request.method() === 'POST' && new URL(request.url()).pathname === '/__scopehub/operation') {
      reads.push(request.postDataJSON());
    }
  });
  await openDefaultSample(page);
  await control(page, '5m').click();
  const history = page.locator('.history');
  await expect(history).toHaveAttribute('data-timeframe', '5m');
  await control(page, 'Close-price line').click();
  await openPanel(page, 'Data window');
  const row = page.getByRole('table', { name: 'Historical bars' }).locator('tbody tr').first();
  const rawValues = await row.locator('td').allTextContents();
  expect(rawValues[0]).toMatch(/^2024-01-\d\dT/);
  const originalCanvas = await history.locator('canvas').first().elementHandle();
  const initialReads = structuredClone(reads);
  const symbol = 'SIM.CN.FUTURE.001';
  await chooseLanguage(page, 'Language settings', 'zh-Hans');
  await expect(history).toHaveAttribute('data-instrument', symbol);
  await expect(control(page, '5m')).toHaveAttribute('aria-pressed', 'true');
  await expect(control(page, '收盘价线')).toHaveAttribute('aria-pressed', 'true');
  await expect(history.getByRole('img', { name: symbol + ' 的收盘价折线和成交量图' })).toBeVisible();
  const table = page.getByRole('table', { name: '历史 K 线' });
  await expect(table.getByRole('columnheader', { name: '收盘价' })).toBeVisible();
  expect((await table.locator('tbody tr').first().locator('td').allTextContents()).slice(0, -1)).toEqual(rawValues.slice(0, -1));
  expect(await originalCanvas.evaluate(canvas => canvas.isConnected)).toBe(true);

  await control(page, '询问 AI').click();
  const draft = 'Watchlist {symbol} · 我的判断 000731';
  const chat = page.getByRole('dialog', { name: '询问 AI' });
  await expect(chat.getByText('未连接模型')).toBeVisible();
  await chat.getByRole('textbox', { name: '问题' }).fill(draft);
  const settingsWindow = await context.newPage();
  await settingsWindow.goto('/');
  await chooseLanguage(settingsWindow, '语言设置', 'en');
  const originalChat = page.getByRole('dialog', { name: 'Ask AI' });
  await expect(originalChat.getByRole('textbox', { name: 'Question' })).toHaveValue(draft);
  await expect(originalChat.getByText('No model connected')).toBeVisible();
  await originalChat.getByRole('button', { name: 'Close' }).click();
  await expect(control(page, 'Close-price line')).toHaveAttribute('aria-pressed', 'true');
  await expect(history).toHaveAttribute('data-timeframe', '5m');
  expect(await originalCanvas.evaluate(canvas => canvas.isConnected)).toBe(true);
  expect(reads).toEqual(initialReads);
});

test('negotiates locale tags and formats source messages without reinterpreting inserted content', async ({ page }) => {
  await page.goto('/');
  await expect(control(page, 'Language settings')).toBeVisible();
  const result = await page.evaluate(async () => {
    const { resolveLocale } = await import('/src/i18n/preference.ts');
    const { t, plural, message, setLocalePreference } = await import('/src/i18n/index.ts');
    const written = message('{path} (unsupported format)', { path: 'Folder/{count}/Close.csv' });
    const en = plural('{count} bar', '{count} bars', 2);
    const one = plural('{count} bar', '{count} bars', 1);
    setLocalePreference('zh-Hans');
    return {
      system: [
        resolveLocale('system', ['de-DE', 'zh-SG', 'en']),
        resolveLocale('system', ['zh-Hant', 'en-GB']),
        resolveLocale('system', ['not a tag', 'fr']),
        resolveLocale('en', ['zh-CN']),
      ],
      en, one, zh: plural('{count} bar', '{count} bars', 1234),
      formatted: t(written), unknown: t('Untranslated {value}', { value: '{other}' }),
      meanings: [t('Close'), t('Close', undefined, 'price')],
    };
  });
  expect(result).toEqual({
    system: ['zh-Hans', 'en', 'en', 'en'], en: '2 bars', one: '1 bar', zh: '1,234 根 K 线',
    formatted: 'Folder/{count}/Close.csv（格式不受支持）', unknown: 'Untranslated {other}', meanings: ['关闭', '收盘价'],
  });
});
