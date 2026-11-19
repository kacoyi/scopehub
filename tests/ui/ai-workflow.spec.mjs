import { randomUUID } from 'node:crypto';
import { expect, test } from '@playwright/test';
import { buildPreview, withPeer, withProfile, withWorkspace, workspaceEndpoint } from './ai-workflow-fixture.mjs';
import { control } from './workspace-controls.mjs';

const requestedModel = 'synthetic-peer:requested';
const respondingModel = 'synthetic-peer:served';
const firstQuestion = 'Synthetic case: what is the latest simulated close?';
const followUp = 'Synthetic follow-up: which bar did that describe?';
const savedTitle = 'Synthetic private memory';
const savedBody = 'Private synthetic marker 7c942059a2 stays in this profile.';
const unavailable = 'The model could not answer. Check the configured server and try again.';
const cancelled = 'Turn cancelled. Connect and enable again to send another question.';

const chat = page => page.getByRole('dialog', { name: 'Ask AI' });
const settings = page => page.getByRole('dialog', { name: 'AI settings' });
const question = page => chat(page).getByLabel('Question');
const user = content => ({ role: 'user', content });
const assistant = content => ({ role: 'assistant', content });

test.beforeAll(async ({}, testInfo) => {
  testInfo.setTimeout(900_000);
  await buildPreview();
});

async function openChat(page) {
  await control(page, 'Ask AI').click({ timeout: 10_000 });
  await expect(chat(page)).toBeVisible();
}

async function askBeforeSetup(page) {
  await openChat(page);
  await question(page).fill(firstQuestion);
  await control(chat(page), 'Ask').click();
  await expect(settings(page)).toBeVisible();
  await expect(control(settings(page), 'Use offline mock')).toBeVisible();
}

async function propose(page, origin) {
  await settings(page).getByLabel('Server origin').fill(origin);
  await settings(page).getByLabel('Model', { exact: true }).fill(requestedModel);
  await control(settings(page), 'Review connection').click();
  const activation = control(settings(page), 'Connect and enable');
  await expect(activation).toBeVisible();
  await expect(settings(page).getByText(origin + '/api/chat', { exact: true })).toBeVisible();
  await expect(settings(page).getByText(requestedModel, { exact: true })).toBeVisible();
  await expect(settings(page)).toContainText('Typed questions and up to four earlier question and answer pairs in this session.');
  await expect(settings(page)).toContainText('Set by the server operator.');
  return activation;
}

async function activateRoute(page, button) {
  const [reply] = await Promise.all([
    page.waitForResponse(response => isAction(response.request(), 'activate')),
    button.click(),
  ]);
  const frame = await reply.json();
  expect(frame).toMatchObject({ ok: true, result: { outcome: { state: 'completed' } } });
  await expect(chat(page).getByText(/^Ollama connected/)).toBeVisible();
  return frame.result.outcome.result.connection.connection_id;
}

async function send(page, text) {
  if (text !== undefined) await question(page).fill(text);
  await control(chat(page), 'Send').click();
}

function isAction(request, name) {
  if (new URL(request.url()).pathname !== workspaceEndpoint) return false;
  const intent = request.postDataJSON()?.intent;
  return intent?.operation === 'ai.textConnection' && intent.arguments?.action === name;
}

async function observeAction(page, name, action) {
  const [request] = await Promise.all([
    page.waitForRequest(request => isAction(request, name)),
    action(),
  ]);
  return request.postDataJSON();
}

async function expectRevoked(dispatch, command, connectionId = command.intent.arguments.connection_id) {
  const reply = await dispatch({
    ...command,
    request_id: randomUUID(),
    message_id: randomUUID(),
    timestamp_ms: Date.now(),
    intent: { ...command.intent, arguments: {
      action: 'send', connection_id: connectionId, question: 'Synthetic stale-grant probe',
    } },
  });
  expect(reply).toMatchObject({ ok: true, result: { outcome: { state: 'failed' } } });
  expect(['PERMISSION_DENIED', 'STALE_CONTEXT']).toContain(reply.result.outcome.error.code);
}

async function rememberPrivateSeed(page) {
  await control(page, 'Private records').click();
  const records = page.getByRole('dialog', { name: 'Private records' });
  await control(records, 'New private record').click();
  await records.getByLabel('Record type').selectOption('memory');
  await records.getByLabel('Title').fill(savedTitle);
  await records.getByLabel('Memory', { exact: true }).fill(savedBody);
  await control(records, 'Review save').click();
  await control(records, 'Save private record').click();
  await expect(records.getByText(savedBody, { exact: true })).toBeVisible();
  await control(records, 'Close').click();
}

function expectRequest(observed, messages) {
  expect(observed).toMatchObject({
    method: 'POST',
    path: '/api/chat',
    contentType: 'application/json',
    authorizationPresent: false,
  });
  expect(observed.body).toEqual({ model: requestedModel, messages, stream: false });
}

async function retainTraffic(testInfo, traffic) {
  const observation = traffic.map(({ body, ...http }) => ({
    ...http,
    requestFields: Object.keys(body ?? {}).sort(),
    requestedModel: body?.model,
    roles: Array.isArray(body?.messages) ? body.messages.map(message => message.role) : null,
  }));
  await testInfo.attach('synthetic-http-traffic', {
    body: JSON.stringify(observation, null, 2),
    contentType: 'application/json',
  });
}

test('first question offers setup and the explicit offline mock; neither contacts the HTTP peer', async ({ browser }, testInfo) => {
  await withPeer(peer => withProfile(profile => withWorkspace(browser, profile, async page => {
    await askBeforeSetup(page);
    expect(peer.traffic, 'No send occurred before choosing a route').toEqual([]);
    await control(settings(page), 'Back to question').click();
    await expect(question(page)).toHaveValue(firstQuestion);
    await control(chat(page), 'Ask').click();
    await control(settings(page), 'Use offline mock').click();
    await expect(chat(page).getByText('Offline mock enabled', { exact: true })).toBeVisible();
    await expect(question(page)).toHaveValue(firstQuestion);
    await send(page);
    await expect(chat(page).getByText(/^Offline mock response\./)).toBeVisible();
    expect(peer.traffic, 'The offline mock is a deliberate in-process route').toEqual([]);
    await testInfo.attach('offline-mock', {
      body: JSON.stringify({ activatedExplicitly: true, requestsAtSyntheticPeer: peer.traffic.length }),
      contentType: 'application/json',
    });
  })));
});

test('one scoped activation delivers two ordinary turns to the owned peer and handles denial, failure and cancellation', async ({ browser }, testInfo) => {
  await withPeer(peer => withPeer(otherPeer => withProfile(async profile => {
    try {
      let firstCommand;
      let restartGrant;
      await withWorkspace(browser, profile, async (page, dispatch) => {
        await rememberPrivateSeed(page);
        const before = await dispatch({ type: 'snapshot' });
        expect(before).toMatchObject({ ok: true });
        const activations = [];
        page.on('request', request => {
          if (isAction(request, 'activate')) activations.push(request.postDataJSON().request_id);
        });
        await askBeforeSetup(page);
        const activate = await propose(page, peer.origin);
        expect(peer.traffic, 'Reviewing a route does not test or contact it').toEqual([]);
        await activateRoute(page, activate);
        await expect(question(page)).toHaveValue(firstQuestion);
        expect(peer.traffic, 'Activation does not submit the draft question').toEqual([]);

        const workspaceRoute = `**${workspaceEndpoint}`;
        let lostReply;
        const dropFirstSendReply = async route => {
          if (lostReply || !isAction(route.request(), 'send')) return route.fallback();
          const admitted = await route.fetch({ maxRedirects: 0 });
          lostReply = await admitted.json();
          await route.abort('failed');
        };
        await page.route(workspaceRoute, dropFirstSendReply);
        firstCommand = await observeAction(page, 'send', () => send(page));
        await expect(chat(page).getByText('Synthetic peer reply 1', { exact: true })).toBeVisible();
        await page.unroute(workspaceRoute, dropFirstSendReply);
        expect(lostReply).toMatchObject({ ok: true, result: { outcome: {
          state: 'completed', result: { action: 'send', turn_id: firstCommand.request_id },
        } } });
        await expect(chat(page).getByRole('heading', { name: `Model ${respondingModel}`, exact: true })).toBeVisible();
        await expect(settings(page)).toHaveCount(0);
        await send(page, followUp);
        await expect(chat(page).getByText('Synthetic peer reply 2', { exact: true })).toBeVisible();
        await expect(settings(page)).toHaveCount(0);
        expect(peer.traffic).toHaveLength(2);
        expect(activations, 'Both ordinary turns use the one explicit activation').toHaveLength(1);
        expectRequest(peer.traffic[0], [user(firstQuestion)]);
        const completed = [user(firstQuestion), assistant('Synthetic peer reply 1'),
          user(followUp), assistant('Synthetic peer reply 2')];
        expectRequest(peer.traffic[1], completed.slice(0, -1));
        expect(JSON.stringify(peer.traffic)).not.toContain(savedBody);
        expect(JSON.stringify(peer.traffic)).not.toContain(savedTitle);
        const after = await dispatch({ type: 'snapshot' });
        expect(after).toEqual(before);
        await testInfo.attach('two-ordinary-turns', { body: await page.screenshot(), contentType: 'image/png' });

        peer.respond('unavailable');
        const failingQuestion = 'Synthetic request to an unavailable route';
        await send(page, failingQuestion);
        await expect(chat(page).getByText(unavailable, { exact: true })).toBeVisible();
        await expect(chat(page).getByText(/^Offline mock response\./)).toHaveCount(0);
        await expect(chat(page)).not.toContainText('synthetic-peer-internal-error');
        expect(peer.traffic).toHaveLength(3);
        expectRequest(peer.traffic[2], [...completed, user(failingQuestion)]);

        peer.respond('hold');
        const cancelledQuestion = 'Synthetic request held until cancellation';
        await send(page, cancelledQuestion);
        await expect.poll(() => peer.traffic.length).toBe(4);
        expectRequest(peer.traffic[3], [...completed, user(cancelledQuestion)]);
        await control(chat(page), 'Cancel turn').click();
        await expect(chat(page).getByText(cancelled, { exact: true })).toBeVisible();
        await expect(control(chat(page), 'Send')).toHaveCount(0);
        await expect.poll(() => peer.traffic[3].closedWithoutReply).toBe(true);
        await expectRevoked(dispatch, firstCommand);
        await question(page).fill('Synthetic question after revoke');
        await control(chat(page), 'Ask').click();
        const reactivate = await propose(page, peer.origin);
        expect(peer.traffic).toHaveLength(4);
        const reactivated = await activateRoute(page, reactivate);

        await control(chat(page), 'AI settings').click();
        await control(settings(page), 'Change connection').click();
        await propose(page, otherPeer.origin);
        await control(settings(page), 'Back to question').click();
        await expect(control(chat(page), 'Send')).toHaveCount(0);
        await expect(control(chat(page), 'Ask')).toBeVisible();
        await expectRevoked(dispatch, firstCommand, reactivated);
        expect(peer.traffic).toHaveLength(4);
        expect(otherPeer.traffic).toEqual([]);

        await control(chat(page), 'Ask').click();
        const activateOther = await propose(page, otherPeer.origin);
        const disconnected = await activateRoute(page, activateOther);
        await control(chat(page), 'AI settings').click();
        await control(settings(page), 'Disconnect').click();
        await control(settings(page), 'Back to question').click();
        await expect(control(chat(page), 'Ask')).toBeVisible();
        await expectRevoked(dispatch, firstCommand, disconnected);
        await question(page).fill('Synthetic question after disconnect');
        await control(chat(page), 'Ask').click();
        await expect(settings(page)).toBeVisible();
        expect(otherPeer.traffic).toEqual([]);

        const activateBeforeRestart = await propose(page, otherPeer.origin);
        restartGrant = await activateRoute(page, activateBeforeRestart);
      });
      await withWorkspace(browser, profile, async (page, dispatch) => {
        await askBeforeSetup(page);
        await expectRevoked(dispatch, firstCommand, restartGrant);
        expect(peer.traffic).toHaveLength(4);
        expect(otherPeer.traffic).toEqual([]);
      });
    } finally {
      await retainTraffic(testInfo, peer.traffic);
    }
  })));
});
