import { expect, test } from '@playwright/test';
import { buildPreview, withPeer, withProfile, withWorkspace } from './ai-workflow-fixture.mjs';

const button = (scope, name) => scope.getByRole('button', { name, exact: true });
const intent = 'Show a horizontal length for the volume of each frozen futures minute in order. Let me inspect its observation.';
const revision = 'Change the lengths to represent closing prices. Keep both minutes in order and available for inspection.';
const pin = '3a1b0032b57711c9563131caf34c8cfda9d91bbf5ef5e9dbc7a4f0f48ab390d1';

function program(field) {
  return `// future free
const maximum = Math.max(...rows.map(row => row.${field}));
for (const [i, row] of rows.entries()) {
  const y = 45 + i * 90;
  rect(64, y, 480 * row.${field} / maximum, 12, 0);
  hit(i, 75, y + 6, 12);
}`;
}
function tree(field) {
  return JSON.stringify(['each', 'future', ['row', ['value', 'time'], ['bar', field], ['inspect', 'Inspect minute']]]);
}

test.beforeAll(async ({}, info) => { info.setTimeout(900_000); await buildPreview(); });

for (const renderer of ['tree', 'drawing']) {
  test(renderer + ' authors and revises the shared two-minute workload, then restores its accepted view', async ({ browser }, info) => {
    await withPeer(peer => withProfile(profile => withWorkspace(browser, profile, async page => {
      const drawing = renderer === 'drawing';
      await button(page, 'Open blank AI workbench').click();
      if (drawing) await button(page, 'Drawing studio').click();
      const host = page.getByRole('dialog', { name: drawing ? 'Drawing studio' : 'AI workbench', exact: true });
      const chat = page.getByRole('dialog', { name: 'Author with AI' });
      const question = chat.getByLabel('What should the workbench show or do?');
      const observed = [];
      for (const [i, field] of ['volume', 'close', 'invalid'].entries()) {
        const source = field === 'invalid'
          ? (drawing ? '// future free\nrect(5,5,-1,5,0);' : '["each","future",["unsupported","close"]]')
          : drawing ? program(field) : tree(field);
        await button(host, i ? 'Ask AI to revise the view' : 'Ask AI to author a view').click();
        await question.fill([intent, revision, 'Try this invalid change.'][i]);
        if (!i) {
          await button(chat, 'Ask').click();
          const settings = page.getByRole('dialog', { name: 'AI settings' });
          await settings.getByLabel('Server origin').fill(peer.origin);
          await settings.getByLabel('Model', { exact: true }).fill('synthetic-renderer:comparison');
          await button(settings, 'Review connection').click();
          await button(settings, 'Connect and enable').click();
        }
        peer.respond({ text: source });
        await button(chat, 'Send').click();
        await expect(question).toHaveValue('');
        if (i === 1) {
          expect(peer.traffic.at(-1).body.messages.at(-1).content).toContain(drawing ? program('volume') : tree('volume'));
        }
        await button(chat, 'Close').click();
        if (field === 'invalid') {
          await expect(host.getByRole('alert')).toBeVisible();
          await expect(button(host, 'Accept view')).toHaveCount(0);
          await button(host, 'Restore accepted view').click();
        } else {
          await button(host, 'Accept view').click();
        }
        const marks = drawing ? host.getByLabel('Program geometry').locator('rect') : host.getByRole('meter');
        await expect(marks).toHaveCount(2);
        const lengths = await marks.evaluateAll((nodes, drawing) => nodes.map(node =>
          drawing ? Number(node.getAttribute('width')) / 480 : node.value / node.max), drawing);
        const expected = field === 'volume' ? [1, 80 / 120] : [3499.2 / 3499.8, 1];
        lengths.forEach((length, index) => expect(length).toBeCloseTo(expected[index], 8));
        const later = drawing ? button(host, 'Inspect observation at 21:01') : button(host, 'Inspect minute').nth(1);
        await later.click();
        const detail = host.getByRole('region', { name: 'Selected frozen evidence' });
        await expect(detail).toContainText(/3,?499\.8 CNY/);
        await expect(detail).toContainText('80 contracts');
        const snapshot = host.getByText(pin, { exact: true });
        await expect(snapshot.first()).toBeAttached();
        observed.push({ field, lengths, laterClose: 3499.8, laterVolume: 80, snapshot: pin });
      }
      expect(observed[2].lengths).toEqual(observed[1].lengths);
      await info.attach('shared-workload', { body: JSON.stringify({ renderer, transport: 'controlled loopback', observed }), contentType: 'application/json' });
    })));
  });
}
