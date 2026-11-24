import { test, expect } from '@playwright/test';
import { buildPreview, withWorkspace, withProfile, withPeer } from './ai-workflow-fixture.mjs';

const button = (scope, name) => scope.getByRole('button', { name, exact: true });
const dialog = page => page.getByRole('dialog', { name: 'Research workbench' });
const accepted = page => dialog(page).getByRole('region', { name: 'Accepted view' });
const draft = page => dialog(page).getByRole('region', { name: 'Draft preview' });
const chat = page => page.getByRole('dialog', { name: 'Author with AI' });
const question = page => chat(page).getByLabel('What should the workbench show or do?');
const text = 'Compose a note and disclosures published by its issuer since review.';
const tree = ['row', ['note', ['stack', ['field', 'title'], ['open','Read selected note']]],
  ['list', [['issuer','eq','$issuer'],['published_at','gt','$reviewed_at']], ['published_at','asc'],
    ['stack',['field','title'],['field','published_at'],['open','Read disclosure']]]];
const titles = ['Inventory update','Revised dispatch total'];
const denied = ['Inventory baseline','Dispatch update'];
const privateLabel = 'LOCAL ONLY handwritten portfolio analysis';
const bodies = ['Synthetic issuer A disclosure.', 'Synthetic issuer B disclosure.', 'Synthetic research note.'];

async function send(page, peer, intent, reply) {
  const count = peer.traffic.length;
  await question(page).fill(intent);
  peer.respond({ text: JSON.stringify(reply) });
  await button(chat(page), 'Send').click();
  await expect(chat(page).getByText('Draft ready. Close this conversation to inspect it in the workbench.').last()).toBeVisible();
  await expect(question(page)).toHaveValue('');
  expect(peer.traffic.length).toBe(count + 1);
  const sent = JSON.stringify(peer.traffic.at(-1).body);
  for (const secret of [...bodies,privateLabel,...denied,...titles]) expect(sent).not.toContain(secret);
  await button(chat(page), 'Close').click();
}
async function connectModel(page, peer) {
  await question(page).fill(text);
  await button(chat(page), 'Ask').click();
  const settings = page.getByRole('dialog', { name: 'AI settings' });
  await settings.getByLabel('Server origin').fill(peer.origin);
  await settings.getByLabel('Model', { exact: true }).fill('synthetic-authoring:fixture');
  await button(settings, 'Review connection').click();
  await button(settings, 'Connect and enable').click();
  await expect(chat(page)).toContainText('Ollama connected');
}
async function openBlank(page) {
  await button(page,'Open blank AI workbench').click();
  await button(page,'Research workbench').click();
  await expect(dialog(page)).toBeVisible();
}
test.beforeAll(async ({}, info) => { info.setTimeout(900_000); await buildPreview(); });

test('authorized research source, model edits, local rules and durable named layout', async ({browser}) => {
  await withPeer(peer => withProfile(profile => withWorkspace(browser,profile,async page => {
    await openBlank(page);
    const studio = dialog(page);
    await expect(studio).toContainText('Start with a blank research canvas.');
    await button(studio,'Connect synthetic research').click();
    await expect(studio.getByLabel('Selected research note')).toHaveValue('note-a');
    await button(studio,'Ask AI to author a view').click();
    await connectModel(page,peer);
    await send(page,peer,text,tree);
    await expect(draft(page)).toContainText('Inventory update');
    await button(studio,'Accept view').click();
    const view = accepted(page);
    for (const title of [...denied,titles[1]]) await expect(view).not.toContainText(title);
    await button(view,'Read selected note').click();
    const original = studio.getByRole('region',{name:'Attributed original'});
    await expect(original).toContainText("Review the next published update on issuer A's local inventory.");
    await button(view,'Read disclosure').click();
    await expect(original).toContainText('A local inventory recount recorded 125 units.');
    await button(studio,'Refresh research source').click();
    await expect(original).toHaveCount(0);
    await expect(view.getByText(titles[1],{exact:true})).toBeVisible();
    // A large valid tree cannot fit blindly into the model request. Keep its
    // unrelated nodes and handwritten label local during a single-list revision.
    const filler = ['stack',...Array.from({length:7}, () => ['stack',...Array.from({length:8}, () => ['text',privateLabel])])];
    const big = ['stack',filler,tree];
    const editor = studio.getByLabel('Composition source');
    await editor.fill(JSON.stringify(big)); await button(studio,'Preview draft').click();
    await button(studio,'Accept view').click();
    await studio.getByLabel('AI edit region').selectOption('2.2');
    await button(view,'Read disclosure').first().click();
    await expect(original).toContainText('125 units.');
    await button(studio,'Ask AI to revise the view').click();
    const recent = structuredClone(tree[2]);
    recent[2][1]='desc';
    await send(page,peer,'Put the latest matching disclosure first.',recent);
    await button(studio,'Accept view').click();
    expect(JSON.parse(await editor.inputValue())[1]).toEqual(filler);
    const ordered = view.getByText(/^(Inventory update|Revised dispatch total)$/);
    await expect(ordered).toHaveText([titles[1],titles[0]]);
    await studio.getByLabel('Text size').fill('20');
    await button(studio,'Accept view').click();
    await expect(view).toHaveCSS('font-size','20px');
    await button(studio,'Undo accepted edit').click();
    await expect(view).toHaveCSS('font-size','16px');
    await editor.fill('{"invalid":true}');
    await button(studio,'Preview draft').click();
    await expect(studio.getByRole('alert')).toBeVisible();
    await expect(ordered).toHaveText([titles[1],titles[0]]);
    await button(studio,'Restore accepted view').click();
    await studio.getByLabel('View name').fill('Saved research');
    await button(studio,'Save accepted view').click();
    await expect(studio).toContainText('Research view saved');
    await button(studio,'Back to chart').click();
    await page.reload();
    await button(page,'Open saved view: Saved research').click();
    await expect(ordered).toHaveText([titles[1],titles[0]]);
    // Same executable, independent order, reversed issuer binding and grouping.
    const second = ['stack',['heading','Other issuers'],['list',[['issuer','ne','$issuer']],['title','asc'],
      ['group','issuer',['row',['open','Inspect'],['field','title']]]]];
    await editor.fill(JSON.stringify(second)); await button(studio,'Preview draft').click();
    await expect(draft(page).getByRole('heading',{name:'B',exact:true})).toBeVisible();
    await expect(draft(page)).toContainText('Dispatch update');
    await button(studio,'Restore accepted view').click();
    await studio.getByLabel('Selected research note').selectOption('note-b');
    await expect(view.locator('[data-count="0"]')).toHaveCount(1);
    await expect(studio).toContainText('Research source connected');
    await button(studio,'Disconnect research source').click();
    await expect(studio).toContainText('Research source disconnected');
    await expect(view.locator('[data-count]')).toHaveCount(0);
    await button(studio,'Revoke research access').click();
    await expect(studio).toContainText('Research access revoked');
    await button(studio,'Back to chart').click(); await page.reload();
    await button(page,'Open saved view: Saved research').click();
    await expect(studio).toContainText('Research access revoked');
    await expect(view).not.toContainText('Issuer A review');
    await expect(original).toHaveCount(0);
  })));
});

test('a held entitlement reply expires and cannot restore a subsequently revoked original', async ({browser}) => {
  await withProfile(profile => withWorkspace(browser, profile, async (page, dispatch) => {
    await openBlank(page);
    const studio = dialog(page);
    await button(studio, 'Connect synthetic research').click();
    await button(studio, 'Try research example').click();
    await button(studio, 'Accept view').click();
    await button(accepted(page), 'Read disclosure').click();
    const original = studio.getByRole('region',{name:'Attributed original'});
    await expect(original).toContainText('125 units.');
    let gotReply, releaseReply;
    const held = new Promise(resolve => { gotReply = resolve; });
    const release = new Promise(resolve => { releaseReply = resolve; });
    const endpoint = '**/__scopehub/workspace';
    async function delay(route) {
      const frame = route.request().postDataJSON();
      if (frame.intent?.operation !== 'research.sample' || frame.intent.arguments.action !== 'status')
        return route.continue();
      const response = await route.fetch();
      gotReply();
      await release;
      // The host may already have cancelled the HTTP request at its deadline.
      await route.fulfill({response}).catch(() => {});
    }
    await page.route(endpoint,delay);
    try {
      await held;
      const request = {protocol_version:'1.1',type:'request',message_id:crypto.randomUUID(),
        request_id:crypto.randomUUID(),timestamp_ms:Date.now(),scope:{workspace_id:'main'},
        intent:{operation:'research.sample',arguments:{action:'revoke',expected_revision:1}}};
      const reply = await dispatch(request);
      expect(reply.result.outcome.result.access).toBe('revoked');
      await expect(original).toHaveCount(0, {timeout:6000});
      await expect(accepted(page)).not.toContainText('Inventory update');
    } finally { releaseReply(); await page.unroute(endpoint,delay); }
    await expect(studio).toContainText('Research access revoked');
    await expect(original).toHaveCount(0);
  }));
});
