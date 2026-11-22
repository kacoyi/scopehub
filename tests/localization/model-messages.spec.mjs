import { expect, test } from '@playwright/test';
import { control } from '../ui/workspace-controls.mjs';

test.use({ locale: 'en-US' });

const routes = [
  {
    route: { kind: 'offline_mock', destination: 'This app (offline)', model: 'ScopeHub offline mock', charge_basis: 'none' },
    chineseDestination: '此应用（离线）',
  },
  {
    route: { kind: 'ollama', destination: 'https://model.example.invalid/api/chat', model: 'Close', charge_basis: 'endpoint_operator' },
    chineseDestination: 'https://model.example.invalid/api/chat',
  },
];

for (const { route, chineseDestination } of routes) {
  test(`renders the ${route.kind} connection destination and model ID on a live language change`, async ({ page }) => {
    await page.addInitScript(route => {
      window.isTauri = true;
      window.__SCOPEHUB_DESKTOP_TEXT_MODEL__ = true;
      window.__TAURI_INTERNALS__ = {
        async invoke(command, { operation, payload }) {
          if (command !== 'execute_operation' || operation !== 'ai.textConnection' || payload.intent.arguments.action !== 'status') {
            throw new Error('Unexpected native model request');
          }
          return {
            protocol_version: '1.1', type: 'response', request_id: payload.request_id,
            message_id: crypto.randomUUID(), timestamp_ms: Date.now(),
            outcome: { state: 'completed', operation, result: {
              action: 'status', turn_id: null,
              connection: { connection_id: '84c8ad71-780d-40a9-a355-a7d290fa20e6', route: { ...route, scope: 'conversation_text' } },
            } },
          };
        },
      };
    }, route);

    await page.goto('/');
    await control(page, 'Ask AI').click();
    await control(page.getByRole('dialog', { name: 'Ask AI' }), 'AI settings').click();
    const disclosure = page.getByRole('region', { name: 'Active model connection' });
    await expect(disclosure.getByText(route.destination, { exact: true })).toBeVisible();
    await expect(disclosure.getByText(route.model, { exact: true })).toBeVisible();

    await page.evaluate(async () => {
      const { setLocalePreference } = await import('/src/i18n/index.ts');
      setLocalePreference('zh-Hans');
    });
    const chineseDisclosure = page.getByRole('region', { name: '当前模型连接' });
    await expect(chineseDisclosure.getByText(chineseDestination, { exact: true })).toBeVisible();
    await expect(chineseDisclosure.getByText(route.model, { exact: true })).toBeVisible();
  });
}
