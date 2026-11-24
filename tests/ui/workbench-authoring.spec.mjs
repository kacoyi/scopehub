import { expect, test } from '@playwright/test';
import { buildPreview, persistedRows, withPeer, withProfile, withWorkspace, workspaceEndpoint } from './ai-workflow-fixture.mjs';

const fixtureModel = 'synthetic-authoring:fixture';
const futureHash = '3a1b0032b57711c9563131caf34c8cfda9d91bbf5ef5e9dbc7a4f0f48ab390d1';
const stockHash = '1cdd778056aa8e41580bb0c522d7eef112a9f02e2ebe6eac83bdda62c30e500e';
const futureView = ['stack', ['title', 'Futures session closes'], ['each', 'future',
  ['row', ['value', 'time'], ['value', 'close'], ['inspect', 'Inspect this minute']]]];
const revision = ['row',
  ['stack', ['title', 'Futures close comparison'], ['each', 'future', ['bar', 'close']]],
  ['stack', ['title', 'Stock comparison'], ['each', 'stock', ['row', ['value', 'low'], ['inspect', 'Check the low']]]]];
const stockView = ['row', ['title', 'Stock liquidity'], ['each', 'stock',
  ['stack', ['value', 'time'], ['bar', 'volume']]]];

const button = (scope, name) => scope.getByRole('button', { name, exact: true });
const chat = page => page.getByRole('dialog', { name: 'Author with AI' });
const canvas = page => page.getByRole('region', { name: 'Workbench canvas' });
const intent = page => chat(page).getByLabel('What should the workbench show or do?');

async function startBlank(page) {
  await button(page, 'Open blank AI workbench').click();
  await expect(canvas(page)).toContainText('Start with a blank canvas.');
  await expect(canvas(page).getByLabel('Authored presentation')).toHaveCount(0);
  await button(canvas(page), 'Ask AI to author a view').click();
  await expect(chat(page)).toBeVisible();
}

async function connect(page, peer, text) {
  await expect(chat(page)).toContainText('No model connected');
  await intent(page).fill(text);
  await button(chat(page), 'Ask').click();
  const settings = page.getByRole('dialog', { name: 'AI settings' });
  await expect(button(settings, 'Use offline mock')).toHaveCount(0);
  await settings.getByLabel('Server origin').fill(peer.origin);
  await settings.getByLabel('Model', { exact: true }).fill(fixtureModel);
  // The ordinary route may be authorized for chart use, but this authoring turn
  // must never acquire chart tools or attach a chart selection.
  await settings.getByLabel('Allow the selected offline sample’s close prices and times').check();
  await button(settings, 'Review connection').click();
  await expect(settings.getByText(peer.origin + '/api/chat', { exact: true })).toBeVisible();
  await button(settings, 'Connect and enable').click();
  await expect(chat(page)).toContainText('Ollama connected');
  await expect(intent(page)).toHaveValue(text);
}

async function proposal(page, peer, text, tree, previous, ready = true) {
  const count = peer.traffic.length;
  await intent(page).fill(text);
  peer.respond({ text: JSON.stringify(tree) });
  await button(chat(page), 'Send').click();
  await expect(intent(page)).toHaveValue('');
  await expect(chat(page).getByRole('log').getByText(ready
    ? 'Draft ready. Close this conversation to inspect it in the workbench.'
    : 'The workbench did not accept this draft.').last()).toBeVisible();
  expect(peer.traffic).toHaveLength(count + 1);
  const call = peer.traffic[count];
  expect(call).toMatchObject({ method: 'POST', path: '/api/chat', authorizationPresent: false });
  expect(call.body).toMatchObject({ model: fixtureModel, stream: false });
  expect(call.body).not.toHaveProperty('tools');
  const prompt = call.body.messages.at(-1);
  expect(prompt.role).toBe('user');
  expect(prompt.content.endsWith('User intent:\n' + text)).toBe(true);
  expect(prompt.content).toContain('Accepted tree: ' + JSON.stringify(previous));
  expect(prompt.content).not.toContain('3499.2');
  expect(prompt.content).not.toContain(stockHash);
  await button(chat(page), 'Close').click();
}

test.beforeAll(async ({}, testInfo) => {
  testInfo.setTimeout(900_000);
  await buildPreview();
});

test('the ordinary model connection authors two views and structurally rewrites an accepted tree', async ({ browser }) => {
  await withPeer(peer => withProfile(profile => withWorkspace(browser, profile, async page => {
    const create = 'Show each futures minute’s close next to its time, with a way to check the underlying observation.';
    const compare = 'Show the same futures closes as bars and place a separate column of stock lows beside them.';
    const stock = 'For the stock alone, show when each minute traded and emphasize the amount with bars.';
    const operations = [];
    page.on('request', request => {
      if (new URL(request.url()).pathname === workspaceEndpoint && request.method() === 'POST') {
        operations.push(request.postDataJSON()?.intent?.operation);
      }
    });
    const before = persistedRows(profile);

    await startBlank(page);
    await connect(page, peer, create);
    await proposal(page, peer, create, futureView, null);
    await expect(canvas(page).getByRole('status')).toHaveText('Draft preview');
    await expect(canvas(page).getByRole('heading', { name: 'Futures session closes' })).toBeVisible();
    await expect(canvas(page).getByText('3499.2 CNY')).toBeVisible();
    await expect(canvas(page).getByText('3499.8 CNY')).toBeVisible();
    await canvas(page).getByText('Frozen synthetic source · SIM.CN.FUTURE.001 · 1m').click();
    await expect(canvas(page).getByText(futureHash)).toBeVisible();
    await button(canvas(page), 'Inspect this minute').nth(1).click();
    const detail = canvas(page).getByRole('region', { name: 'Selected frozen evidence' });
    await expect(detail).toContainText('3499.8 CNY');
    await expect(detail).toContainText('80 contracts');
    await expect(detail).toContainText(futureHash);

    await button(canvas(page), 'Accept view').click();
    await button(canvas(page), 'Ask AI to revise the view').click();
    await proposal(page, peer, compare, revision, futureView);
    const closeComparison = canvas(page).getByRole('heading', { name: 'Futures close comparison' });
    const comparison = canvas(page).getByRole('heading', { name: 'Stock comparison' });
    const [left, right] = await Promise.all([closeComparison.boundingBox(), comparison.boundingBox()]);
    expect(left.y).toEqual(right.y);
    expect(left.x).toBeLessThan(right.x);
    await expect(canvas(page).getByRole('meter')).toHaveCount(2);
    await expect(canvas(page).getByRole('meter').first()).toHaveAttribute('value', '3499.2');
    await expect(canvas(page).getByRole('meter').last()).toHaveAttribute('value', '3499.8');
    await expect(canvas(page).getByText(futureHash)).toBeVisible();
    await expect(canvas(page).getByText('11.94 CNY')).toBeVisible();
    await expect(button(canvas(page), 'Inspect this minute')).toHaveCount(0);
    await button(canvas(page), 'Accept view').click();
    await button(canvas(page), 'Ask AI to revise the view').click();
    await proposal(page, peer, 'Try an unsupported interaction.', ['each', 'stock', ['invoke', 'workspace.undo']], revision, false);
    await expect(canvas(page).getByRole('alert')).toBeVisible();
    await expect(canvas(page).getByRole('status')).toHaveText('Accepted view');
    await expect(closeComparison).toBeVisible();
    await expect(comparison).toBeVisible();
    await button(canvas(page), 'Restore accepted view').click();
    await expect(canvas(page).getByRole('alert')).toHaveCount(0);

    await button(page, 'Back to chart').click();
    await startBlank(page);
    await proposal(page, peer, stock, stockView, null);
    const heading = canvas(page).getByRole('heading', { name: 'Stock liquidity' });
    const [title, firstMark] = await Promise.all([heading.boundingBox(), canvas(page).getByRole('meter').first().boundingBox()]);
    expect(title.x).toBeLessThan(firstMark.x);
    await expect(canvas(page).getByText('20 shares')).toBeVisible();
    await expect(canvas(page).getByText('130 shares')).toBeVisible();
    await canvas(page).getByText('Frozen synthetic source · SIM.CN.STOCK.001 · 1m').click();
    await expect(canvas(page).getByText(stockHash)).toBeVisible();
    await expect(canvas(page).getByText(/SIM.CN.FUTURE.001/)).toHaveCount(0);
    await expect(canvas(page).getByRole('button', { name: /Inspect/ })).toHaveCount(0);

    await button(canvas(page), 'Accept view').click();
    await button(canvas(page), 'Ask AI to revise the view').click();
    const deferred = Promise.withResolvers();
    peer.respond(() => deferred.promise);
    const count = peer.traffic.length;
    await intent(page).fill('Replace it with both instruments.');
    await button(chat(page), 'Send').click();
    await expect.poll(() => peer.traffic.length).toBe(count + 1);
    await button(chat(page), 'Close').click();
    await expect(canvas(page).getByRole('status')).toHaveText('Accepted view');
    await button(page, 'Back to chart').click();
    await startBlank(page);
    const completion = page.waitForResponse(async response => {
      if (new URL(response.url()).pathname !== workspaceEndpoint
        || response.request().postDataJSON()?.intent?.arguments?.action !== 'poll') return false;
      return (await response.json()).result?.outcome?.result?.turn?.state === 'completed';
    });
    deferred.resolve({ role: 'assistant', content: JSON.stringify(revision) });
    await completion;
    await expect(intent(page)).toBeEnabled();
    await expect(chat(page).getByText('Draft ready. Close this conversation to inspect it in the workbench.')).toHaveCount(0);
    await button(chat(page), 'Close').click();
    await expect(canvas(page)).toContainText('Start with a blank canvas.');
    expect(operations.filter(Boolean)).not.toContain('workspace.undo');
    expect(persistedRows(profile)).toEqual(before);

    await button(page, 'Back to chart').click();
    await button(page, 'Open workbench prototype').click();
    await expect(page.getByRole('textbox', { name: 'Composition JSON' })).toBeVisible();
    await button(page, 'Back to chart').click();
    await expect(button(page, 'Open offline sample')).toBeVisible();
  })));
});
