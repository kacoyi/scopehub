import { expect } from '@playwright/test';
import { withPeer, withProfile, withWorkspace } from './ai-workflow-fixture.mjs';
import { control } from './workspace-controls.mjs';

const chat = page => page.getByRole('dialog', { name: 'Ask AI' });
const settings = page => page.getByRole('dialog', { name: 'AI settings' });
const conversation = page => chat(page).getByRole('log', { name: 'Conversation' });
const user = content => ({ role: 'user', content });
const assistant = content => ({ role: 'assistant', content });
const peerReply = turn => `Synthetic peer reply ${turn}`;

export async function checkModelConnectionBoundaries(browser, testInfo, controls) {
  const { openChat, propose, activateRoute, send, observeAction, expectRevoked, expectRequest } = controls;
  await withPeer(first => withPeer(second => withProfile(profile => withProfile(async otherProfile => {
    expect(first.origin).not.toBe(second.origin);
    expect(profile.id).not.toBe(otherProfile.id);
    try {
      await withWorkspace(browser, profile, async (page, dispatch) => {
        await openChat(page);
        await control(chat(page), 'AI settings').click();
        await activateRoute(page, await propose(page, first.origin));

        const questions = Array.from({ length: 6 }, (_, index) => `Synthetic recipient A question ${index + 1}`);
        const firstCommand = await observeAction(page, 'send', () => send(page, questions[0]));
        await expect(conversation(page).getByText(peerReply(1), { exact: true })).toBeVisible();
        expectRequest(first.traffic[0], [user(questions[0])]);
        for (const [index, question] of questions.slice(1).entries()) {
          await send(page, question);
          await expect(conversation(page).getByText(peerReply(index + 2), { exact: true })).toBeVisible();
        }
        expect(first.traffic).toHaveLength(6);
        expectRequest(first.traffic[4], [
          user(questions[0]), assistant(peerReply(1)),
          user(questions[1]), assistant(peerReply(2)),
          user(questions[2]), assistant(peerReply(3)),
          user(questions[3]), assistant(peerReply(4)),
          user(questions[4]),
        ]);
        expectRequest(first.traffic[5], [
          user(questions[1]), assistant(peerReply(2)),
          user(questions[2]), assistant(peerReply(3)),
          user(questions[3]), assistant(peerReply(4)),
          user(questions[4]), assistant(peerReply(5)),
          user(questions[5]),
        ]);

        await control(chat(page), 'AI settings').click();
        await control(settings(page), 'Change connection').click();
        const activateSecond = await propose(page, second.origin);
        expect(first.traffic).toHaveLength(6);
        expect(second.traffic).toEqual([]);
        const secondGrant = await activateRoute(page, activateSecond);
        expect(secondGrant).not.toBe(firstCommand.intent.arguments.connection_id);
        await expectRevoked(dispatch, firstCommand);
        await expect(conversation(page)).toBeEmpty();

        const secondQuestion = 'Synthetic recipient B question in the first profile';
        await send(page, secondQuestion);
        await expect(conversation(page).getByText(peerReply(1), { exact: true })).toBeVisible();
        expect(second.traffic).toHaveLength(1);
        expectRequest(second.traffic[0], [user(secondQuestion)]);

        await withWorkspace(browser, otherProfile, async (otherPage, dispatchOther) => {
          await openChat(otherPage);
          await expect(chat(otherPage).getByText('No model connected', { exact: true })).toBeVisible();
          await expect(conversation(otherPage)).toBeEmpty();
          await expectRevoked(dispatchOther, firstCommand, secondGrant);
          expect(second.traffic).toHaveLength(1);
          await control(chat(otherPage), 'AI settings').click();
          await activateRoute(otherPage, await propose(otherPage, second.origin));
          const isolatedQuestion = 'Synthetic recipient B question in a separate profile';
          await send(otherPage, isolatedQuestion);
          await expect(conversation(otherPage).getByText(peerReply(2), { exact: true })).toBeVisible();
          expect(second.traffic).toHaveLength(2);
          expectRequest(second.traffic[1], [user(isolatedQuestion)]);
        });

        second.respond('hold');
        const cancelledQuestion = 'Synthetic recipient B question cancelled by its profile';
        await send(page, cancelledQuestion);
        await expect.poll(() => second.traffic.length).toBe(3);
        expectRequest(second.traffic[2], [user(secondQuestion), assistant(peerReply(1)), user(cancelledQuestion)]);
        await control(chat(page), 'Cancel turn').click();
        await expect(chat(page).getByText('No model connected', { exact: true })).toBeVisible();
        await expect(chat(page).getByLabel('Question')).toHaveValue('');
        await expect.poll(() => second.traffic[2].closedWithoutReply).toBe(true);
        await expectRevoked(dispatch, firstCommand, secondGrant);

        second.respond('success');
        await control(chat(page), 'AI settings').click();
        const reactivatedGrant = await activateRoute(page, await propose(page, second.origin));
        await expect(control(chat(page), 'Send')).toBeDisabled();
        const newQuestion = 'Synthetic recipient B question after a new approval';
        await send(page, newQuestion);
        await expect(conversation(page).getByText(peerReply(4), { exact: true })).toBeVisible();
        expectRequest(second.traffic[3], [user(newQuestion)]);
        await control(chat(page), 'AI settings').click();
        await control(settings(page), 'Disconnect').click();
        await control(settings(page), 'Back to question').click();
        await expect(chat(page).getByText('No model connected', { exact: true })).toBeVisible();
        await expectRevoked(dispatch, firstCommand, reactivatedGrant);
        expect(first.traffic).toHaveLength(6);
        expect(second.traffic).toHaveLength(4);
      });
    } finally {
      await testInfo.attach('outbound-payloads-at-two-synthetic-recipients', {
        body: JSON.stringify({ first: first.traffic, second: second.traffic }, null, 2),
        contentType: 'application/json',
      });
    }
  }))));
}
