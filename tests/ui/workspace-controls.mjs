import { expect } from '@playwright/test';

export function control(page, name) {
  return page.getByRole('button', { name, exact: true });
}

export function watchlistSymbol(page, id) {
  const symbol = id.replace(/^SIM\./, '').replaceAll('.', '\\.');
  return page.getByRole('button', { name: new RegExp(symbol) });
}

export async function openPanel(page, name) {
  const toggle = control(page, name);
  if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click();
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
}

export async function leaveSample(page) {
  await openPanel(page, 'Data sources');
  await control(page, 'Leave sample').click();
  await expect(control(page, 'Open offline sample')).toBeVisible();
  await expect(page.locator('.chart canvas')).toHaveCount(0);
}
