import { expect } from '@playwright/test';
import { control } from './workspace-controls.mjs';

const removalNotice = 'Access removed for this published revision.';

export function openPublishedRevisionDialog(page) {
  return control(page.getByRole('region', { name: 'Price chart', exact: true }), 'Open published CSV').click();
}

export function expectEmptyRevisionLibrary(dialog, kind) {
  const library = dialog.getByRole('region', { name: 'Published revisions', exact: true });
  const empty = kind === 'folder' ? 'No published folder revisions are available in this profile.'
    : 'No published CSV revisions are available in this profile.';
  return expect(library.getByRole('status').and(library.getByText(empty, { exact: true }))).toBeVisible();
}

export async function withdrawPublishedRevision(dialog, kind) {
  await control(dialog, 'Remove frozen chart access').click();
  const confirmation = dialog.getByRole('group', { name: 'Remove access to this revision?', exact: true });
  await expect(confirmation).toContainText(kind === 'folder'
    ? 'The original sources stay in this profile.' : 'The original CSV copy stays in this profile.');
  await control(confirmation, 'Remove access').click();
  await expect(dialog.getByRole('status').and(dialog.getByText(removalNotice, { exact: true }))).toBeVisible();
  await expectEmptyRevisionLibrary(dialog, kind);
}

export function expectWithdrawnRevisionDenied(dialog) {
  return expect(dialog.getByRole('alert')).toHaveText('This profile does not have permission to display this revision.');
}
