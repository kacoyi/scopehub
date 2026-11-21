import { randomUUID } from 'node:crypto';
import { expect, test } from '@playwright/test';
import { buildPreview, credentialEndpoint, persistedRows, seedDisplayOnlyChart, withPeer, withProfile, withWorkspace, workspaceEndpoint } from './ai-workflow-fixture.mjs';
import { control, openPanel } from './workspace-controls.mjs';
import { checkModelConnectionBoundaries } from './model-connection-boundaries.mjs';

const requestedModel = 'synthetic-peer:requested';
const respondingModel = 'synthetic-peer:served';
const firstQuestion = 'Synthetic case: what is the latest simulated close?';
const followUp = 'Synthetic follow-up: which bar did that describe?';
const savedTitle = 'Synthetic private memory';
const savedBody = 'Private synthetic marker 7c942059a2 stays in this profile.';
const selectedChartTool = 'read_selected_chart';
const chartTools = [{
  type: 'function',
  function: {
    name: selectedChartTool,
    description: 'Read closing prices and timestamps from the currently selected chart interval.',
    parameters: { type: 'object', properties: {}, required: [], additionalProperties: false },
  },
}];
const chartAccessLabel = 'Allow the selected offline sample’s close prices and times';
// Independent expected values from the committed SIM.CN.FUTURE.001 market fixture.
const intervalA = { time_ms: Date.parse('2024-01-02T13:00:00.000Z'), close: 3499.2 };
const intervalB = { time_ms: Date.parse('2024-01-02T13:01:00.000Z'), close: 3499.8 };
const sampleDataset = '836cb0d6-61d3-5d80-8e4d-37da5b308cf1';
const sampleRevision = 'scopehub-synthetic-v1:20241013:normal';
const csvBar = {
  time_ms: Date.parse('2026-02-03T00:00:00.000Z'), trading_day: '2026-02-03',
  open: 7318, high: 7320, low: 7316, close: 7319.125, volume: 13, is_closed: true,
};
const unavailable = 'The model could not answer. Check the configured server and try again.';
const cancelled = 'Turn cancelled. Connect and enable again to send another question.';

const chat = page => page.getByRole('dialog', { name: 'Ask AI' });
const settings = page => page.getByRole('dialog', { name: 'AI settings' });
const question = page => chat(page).getByLabel('Question');
const selectedModel = page => settings(page).getByLabel('Model', { exact: true });
const provider = page => settings(page).getByRole('combobox', { name: 'Provider', exact: true });
const apiKey = page => settings(page).getByLabel('OpenAI API key', { exact: true });
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

async function propose(page, origin, chartAccess = false) {
  await settings(page).getByLabel('Server origin').fill(origin);
  await settings(page).getByLabel('Model', { exact: true }).fill(requestedModel);
  const allowChart = settings(page).getByLabel(chartAccessLabel, { exact: true });
  if (chartAccess) await allowChart.check();
  await control(settings(page), 'Review connection').click();
  const activation = control(settings(page), 'Connect and enable');
  await expect(activation).toBeVisible();
  await expect(settings(page).getByText(origin + '/api/chat', { exact: true })).toBeVisible();
  await expect(settings(page).getByText(requestedModel, { exact: true })).toBeVisible();
  await expect(settings(page)).toContainText('Typed questions and up to four earlier question and answer pairs in this session.');
  await expect(settings(page)).toContainText('Set by the server operator.');
  if (chartAccess) {
    await expect(settings(page)).toContainText('close prices and times');
    await expect(settings(page)).toContainText('even at a loopback address');
  }
  return activation;
}

async function activateRoute(page, button, name = 'Ollama') {
  const [reply] = await Promise.all([
    page.waitForResponse(response => isAction(response.request(), 'activate')),
    button.click(),
  ]);
  const frame = await reply.json();
  expect(frame).toMatchObject({ ok: true, result: { outcome: { state: 'completed' } } });
  await expect(chat(page).getByText(new RegExp(`^${name} connected`))).toBeVisible();
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

function modelCommand(args) {
  return {
    protocol_version: '1.1', type: 'request', request_id: randomUUID(), message_id: randomUUID(),
    timestamp_ms: Date.now(), scope: { workspace_id: 'main' },
    intent: { operation: 'ai.textConnection', arguments: args },
  };
}

async function assertCloudRevoked(dispatch, connectionId) {
  const status = await dispatch(modelCommand({ action: 'status' }));
  expect(status).toMatchObject({ ok: true, result: { outcome: {
    state: 'completed', result: { connection: null, turn_id: null },
  } } });
  // A stale disconnect checks the grant without sending a question to the provider.
  const previous = await dispatch(modelCommand({ action: 'disconnect', connection_id: connectionId }));
  expect(previous).toMatchObject({ ok: true, result: { outcome: { state: 'failed', error: { code: 'STALE_CONTEXT' } } } });
}

function assertKeyAbsent(value, keys) {
  const serialized = JSON.stringify(value);
  expect(keys.some(key => serialized.includes(key)), 'The reply contains no synthetic API key').toBe(false);
}

async function updateCloudKey(page, action, label, keys, secret) {
  if (secret) await apiKey(page).fill(secret);
  const [response] = await Promise.all([
    page.waitForResponse(reply => new URL(reply.url()).pathname === credentialEndpoint
      && reply.request().postDataJSON()?.action === action),
    control(settings(page), label).click(),
  ]);
  expect(response.status()).toBe(200);
  expect(response.headers()['cache-control']).toBe('no-store');
  const reply = await response.json();
  assertKeyAbsent(reply, keys);
  expect(reply).toEqual({ ok: true, result: { configured: action === 'replace', lifetime: 'session' } });
  await expect(apiKey(page)).toBeEnabled();
  expect(await apiKey(page).inputValue() === '', 'The password field is cleared after submission').toBe(true);
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
  const saved = await operation(page, 'privateRecord.change', () => control(records, 'Save private record').click(), 'resolve');
  expect(saved.outcome).toMatchObject({ state: 'committed' });
  await expect(records.getByText(savedBody, { exact: true })).toBeVisible();
  await control(records, 'Close').click();
}

function expectRequest(observed, messages, extra = {}) {
  expect(observed).toMatchObject({
    method: 'POST',
    path: '/api/chat',
    contentType: 'application/json',
    authorizationPresent: false,
  });
  expect(observed.body).toEqual({ model: requestedModel, messages, stream: false, ...extra });
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

test('an accepted 20,480-byte answer reaches chat and leaves the same workspace responsive', async ({ browser }, testInfo) => {
  await withPeer(peer => withProfile(profile => withWorkspace(browser, profile, async (page, dispatch) => {
    await askBeforeSetup(page);
    await activateRoute(page, await propose(page, peer.origin));
    const before = await dispatch({ type: 'snapshot' });
    expect(before).toMatchObject({ ok: true });
    const text = 'a'.repeat(20_480);
    peer.respond({ text });
    await send(page);
    const log = chat(page).getByRole('log');
    await expect.poll(async () => ({
      answers: await log.getByRole('article').count(),
      errors: await chat(page).getByRole('alert').count(),
    })).not.toEqual({ answers: 0, errors: 0 });
    const after = await dispatch({ type: 'snapshot' });
    const answers = await log.locator('.model-answer p').allTextContents();
    await testInfo.attach('ascii-reply-boundary', { body: JSON.stringify({
      answerBytes: Buffer.byteLength(text), outboundRequests: peer.traffic.length,
      renderedAnswerBytes: answers.map(answer => Buffer.byteLength(answer)),
      snapshotAfterPoll: after.ok ? 'ready' : after.error?.code,
      alert: await chat(page).getByRole('alert').allTextContents(),
    }), contentType: 'application/json' });
    expect(after, 'The original writer still serves the same snapshot').toEqual(before);
    expect(answers).toEqual([text]);
    expect(peer.traffic).toHaveLength(1);
  })));
});

test('escaped UTF-8 reaches chat at its byte limit and an oversized answer fails within the same grant', async ({ browser }, testInfo) => {
  await withPeer(peer => withProfile(profile => withWorkspace(browser, profile, async (page, dispatch) => {
    await askBeforeSetup(page);
    const connectionId = await activateRoute(page, await propose(page, peer.origin));
    const before = await dispatch({ type: 'snapshot' });
    expect(before).toMatchObject({ ok: true });
    // Keep the decoded text at 64 KiB and exercise JSON expansion well beyond 16 KiB.
    const prefix = '界😀"\\\n';
    const escaped = prefix + '\x01'.repeat(16_384);
    const text = escaped + 'a'.repeat(65_536 - Buffer.byteLength(escaped));
    peer.respond({ text });
    await send(page);
    const answers = chat(page).getByRole('log').locator('.model-answer p');
    await expect(answers).toHaveCount(1);
    expect(await answers.first().textContent()).toBe(text);

    peer.respond({ text: text + 'x' });
    await send(page, followUp);
    await expect(chat(page).getByRole('alert')).toHaveText('The question or response exceeded the text limit.');
    await expect(chat(page).getByText(/^Ollama connected/)).toBeVisible();
    expect(await dispatch({ type: 'snapshot' })).toEqual(before);

    // Send a real workspace mutation above its existing public frame limit.
    const mutation = {
      protocol_version: '1.1', type: 'request', message_id: randomUUID(),
      request_id: randomUUID(), timestamp_ms: Date.now(), task_id: null,
      scope: { workspace_id: 'main', target: { kind: 'chart', object_id: 'main-chart' },
        expected_workspace_revision: before.result.workspace.revision,
        expected_selection_revision: before.result.selection_revision, idempotency_key: randomUUID() },
      intent: { operation: 'chart.setTimeframe', arguments: { timeframe: '5m' } },
    };
    const padding = 16_384;
    const rejectedMutation = await dispatch(mutation, padding);
    expect(rejectedMutation).toMatchObject({ ok: false, error: { code: 'RESOURCE_LIMIT' } });
    expect(await dispatch({ type: 'snapshot' })).toEqual(before);
    const followingMutation = await dispatch({ ...mutation, message_id: randomUUID(), request_id: randomUUID() });
    expect(followingMutation).toMatchObject({ ok: true, result: { outcome: { state: 'committed',
      result: { timeframe: '5m', revision: before.result.workspace.revision + 1 } } } });

    peer.respond('success');
    const next = await observeAction(page, 'send', () => send(page, 'Synthetic request after oversized reply'));
    await expect(chat(page).getByText('Synthetic peer reply 3', { exact: true })).toBeVisible();
    expect(next.intent.arguments.connection_id).toBe(connectionId);
    expect(peer.traffic).toHaveLength(3);
    expectRequest(peer.traffic[2], [user('Synthetic request after oversized reply')]);
    await testInfo.attach('escaped-and-oversized-boundaries', { body: JSON.stringify({
      legalTextBytes: Buffer.byteLength(text),
      legalEncodedTextBytes: Buffer.byteLength(JSON.stringify(text)),
      oversizedTextBytes: Buffer.byteLength(text + 'x'),
      rejectedMutationBytes: Buffer.byteLength(JSON.stringify(mutation)) + padding,
      rejectedMutationCode: rejectedMutation.error.code,
      outboundRequests: peer.traffic.length, reusedGrant: true, reusedWriter: true,
    }), contentType: 'application/json' });
  })));
});

for (const fault of ['lost', 'mismatched']) {
  test('recovers the second turn after a ' + fault + ' terminal poll reply', async ({ browser }, testInfo) => {
    await withPeer(peer => withProfile(profile => withWorkspace(browser, profile, async page => {
      await askBeforeSetup(page);
      const connectionId = await activateRoute(page, await propose(page, peer.origin));
      await send(page);
      await expect(chat(page).getByText('Synthetic peer reply 1', { exact: true })).toBeVisible();
      const workspaceRoute = '**' + workspaceEndpoint;
      const submissions = [];
      page.on('request', request => {
        if (isAction(request, 'send')) submissions.push(request.postDataJSON().request_id);
      });
      let interrupted;
      let injectFault = true;
      const interruptSecondPoll = async route => {
        if (!injectFault || !isAction(route.request(), 'poll')) return route.fallback();
        const response = await route.fetch({ maxRedirects: 0 });
        const frame = await response.json();
        const result = frame.result?.outcome?.result;
        if (result?.turn?.state !== 'completed') return route.fulfill({ response });
        interrupted = result.turn_id;
        if (fault === 'lost') {
          injectFault = false;
          return route.abort('failed');
        }
        result.turn_id = randomUUID();
        await route.fulfill({ response, json: frame });
      };
      await page.route(workspaceRoute, interruptSecondPoll);
      await send(page, followUp);
      await expect.poll(() => interrupted).toBeTruthy();

      const observe = async () => ({
        connected: await chat(page).getByText(/^Ollama connected/).count() === 1,
        transcript: await chat(page).getByRole('article').allTextContents(),
        alert: await chat(page).getByRole('alert').allTextContents(),
        draft: await question(page).inputValue(),
        sendEnabled: await control(chat(page), 'Send').isEnabled(),
      });
      await expect.poll(async () => {
        const state = await observe();
        return state.transcript.length === 2 || state.alert.length !== 0;
      }).toBe(true);
      const interruptedState = await observe();
      injectFault = false;
      await control(chat(page), 'Close').click();
      await openChat(page);
      await expect(chat(page).getByText('Synthetic peer reply 2', { exact: true })).toBeVisible();
      const recoveredState = await observe();
      await testInfo.attach('second-turn-recovery', { body: JSON.stringify({
        fault, connectionId, interrupted, submissions, interruptedState, recoveredState,
        outbound: peer.traffic.map(entry => entry.body),
      }, null, 2), contentType: 'application/json' });
      expect(interruptedState.connected).toBe(true);
      expect(interruptedState.transcript[0]).toContain(firstQuestion);
      expect(interruptedState.sendEnabled).toBe(false);
      if (fault === 'mismatched') {
        expect(interruptedState.alert).toEqual(['The workspace could not confirm this turn. Check its status to continue.']);
      }
      expect(recoveredState).toMatchObject({ connected: true, alert: [], draft: '', sendEnabled: false });
      const conversation = chat(page).getByRole('log');
      await expect(conversation.locator('.model-question p')).toHaveText([firstQuestion, followUp]);
      await expect(conversation.locator('.model-answer p')).toHaveText(['Synthetic peer reply 1', 'Synthetic peer reply 2']);
      expect(submissions).toEqual([interrupted]);
      expect(peer.traffic).toHaveLength(2);
      expectRequest(peer.traffic[1], [user(firstQuestion), assistant('Synthetic peer reply 1'), user(followUp)]);
    })));
  });
}

test('sixth turn, changed destination, separate profile and cancellation respect the approved route', async ({ browser }, testInfo) => {
  await checkModelConnectionBoundaries(browser, testInfo,
    { openChat, propose, activateRoute, send, observeAction, expectRevoked, expectRequest });
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
        await expect(chat(page).getByRole('log').locator('.model-answer p')).toHaveText([
          'Synthetic peer reply 1', 'Synthetic peer reply 2',
        ]);
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

async function selectChartBar(page, bar) {
  const time = new Date(bar.time_ms).toISOString();
  const panel = page.getByRole('complementary', { name: 'Data window' });
  const table = panel.getByRole('table', { name: 'Historical bars' });
  await expect(table).toBeVisible({ timeout: 12_000 });
  const button = control(panel, `Select bar at ${time}`);
  const receipt = await operation(page, 'workspace.selectEvidence', () => button.click());
  await expect(button).toHaveAttribute('aria-pressed', 'true');
  await expect(table.getByRole('row').filter({ has: button }).getByRole('cell').nth(5)).toHaveText(String(bar.close));
  expect(receipt.selection.target.range).toEqual(range(bar));
  return receipt.selection;
}

const range = ({ time_ms }) => ({ from_ms: time_ms, to_ms: time_ms + 1 });
const providerToolCall = {
  ...assistant(''),
  tool_calls: [{ id: 'call_a8bc123d', function: { name: selectedChartTool, arguments: {}, index: 0 } }],
};
const echoedToolCall = {
  ...assistant(''),
  tool_calls: [{ id: 'call_scopehub', function: { name: selectedChartTool, arguments: {} } }],
};
const closeAnswer = bar => `Close ${bar.close} at ${new Date(bar.time_ms).toISOString()}.`;

function chartReply(body) {
  if (body?.tools) return providerToolCall;
  const last = body?.messages?.at(-1);
  if (last?.role !== 'tool') return assistant('Ordinary text received.');
  return assistant(closeAnswer(JSON.parse(last.content).rows[0]));
}

async function operation(page, name, action, intentAction) {
  const [response] = await Promise.all([page.waitForResponse(response => {
    if (new URL(response.url()).pathname !== workspaceEndpoint) return false;
    const body = response.request().postDataJSON();
    return (body?.operation === name || body?.intent?.operation === name)
      && (intentAction === undefined || body.intent?.arguments?.action === intentAction);
  }, { timeout: 12_000 }), action()]);
  const reply = await response.json();
  expect(reply, name).toMatchObject({ ok: true });
  return reply.result;
}

async function openSampleChart(page) {
  await expect(control(page, 'Edit layout')).toBeEnabled();
  await control(page, 'Open offline sample').click();
  await expect(page.locator('.connection')).toHaveText('Offline sampleSimulated');
  await openPanel(page, 'Data window');
}

async function inspectManualCsv(page, revision) {
  await openPanel(page, 'Data sources');
  await control(page, 'Open published CSV').click();
  const dialog = page.getByRole('dialog', { name: 'Open published CSV' });
  await dialog.getByLabel('Revision ID').fill(revision);
  await control(dialog, 'Open frozen chart').click();
  const manual = dialog.getByRole('region', { name: 'Shared chart evidence' });
  await operation(page, 'workspace.selectEvidence', () => control(manual, 'Share selected bar').click());
  const projected = await operation(page, 'workspace.readChartProjection', () => control(manual, 'Inspect shared bar').click());
  expect(projected.result).toEqual({
    source_reference: revision, revision, fields: ['time_ms', 'close'], rows: [{ time_ms: csvBar.time_ms, close: csvBar.close }],
  });
  await expect(manual.getByText(`Close ${csvBar.close}`, { exact: true })).toBeVisible();
  await control(dialog, 'Use as workspace source').click();
  await expect(dialog).toBeHidden();
  await openPanel(page, 'Data window');
  return selectChartBar(page, csvBar);
}

function expectToolResult(observed, question, bar) {
  const content = observed.body?.messages?.at(-1)?.content;
  expectRequest(observed, [user(question), echoedToolCall, {
    role: 'tool', tool_name: selectedChartTool, tool_call_id: 'call_scopehub', content,
  }]);
  const projection = JSON.parse(content);
  expect(projection).toEqual({
    source_reference: sampleDataset, revision: sampleRevision, range: range(bar), rows: [bar],
  });
  return projection;
}

async function expectChartAnswer(page, bar) {
  await expect(chat(page).getByText(closeAnswer(bar), { exact: true })).toBeVisible();
  const evidence = chat(page).getByRole('region', { name: 'Chart evidence used' }).last();
  await expect(evidence).toContainText(sampleDataset);
  await expect(evidence).toContainText(sampleRevision);
  await expect(evidence).toContainText(`Bar starting ${new Date(bar.time_ms).toISOString()}`);
  const rows = evidence.getByRole('table', { name: 'Selected chart closes' }).getByRole('row');
  await expect(rows).toHaveCount(2);
  await expect(rows.nth(1).getByRole('cell')).toHaveText([new Date(bar.time_ms).toISOString(), String(bar.close)]);
}

test('selected chart conversation negotiates the bounded tool through the owned HTTP peer', async ({ browser }, testInfo) => {
  await withPeer(peer => withProfile(async profile => {
    const csvRevision = randomUUID();
    await seedDisplayOnlyChart(profile, csvRevision, csvBar);
    await withWorkspace(browser, profile, async (page, dispatch) => {
      const activations = [];
      page.on('request', request => {
        if (isAction(request, 'activate')) activations.push(request.postDataJSON().request_id);
      });
      peer.respond(chartReply);
      const observation = { readOnlyTables: [], selections: [], projections: [] };
      try {
        await rememberPrivateSeed(page);
        await openSampleChart(page);
        const first = await selectChartBar(page, intervalA);
        observation.selections.push(first.selection_revision);
        const beforeA = persistedRows(profile);
        const manual = await dispatch({
          type: 'request', protocol_version: '1.1', operation: 'workspace.readChartProjection',
          request_id: randomUUID(), message_id: randomUUID(), timestamp_ms: Date.now(),
          workspace_id: first.workspace_id, selection_revision: first.selection_revision, requested_fields: ['time_ms', 'close'],
        });
        expect(manual).toMatchObject({ ok: true, result: { operation: 'workspace.readChartProjection' } });
        expect(manual.result.result).toEqual({
          source_reference: sampleDataset, revision: sampleRevision, fields: ['time_ms', 'close'], rows: [intervalA],
        });
        observation.manualProjection = manual.result.result;
        await askBeforeSetup(page);
        await activateRoute(page, await propose(page, peer.origin, true));
        await send(page);
        await expect.poll(() => peer.traffic.length, { timeout: 10_000 }).toBe(2);
        expectRequest(peer.traffic[0], [user(firstQuestion)], { tools: chartTools });
        observation.projections.push(expectToolResult(peer.traffic[1], firstQuestion, intervalA));
        await expectChartAnswer(page, intervalA);
        expect(persistedRows(profile), 'Tool turn A leaves all persisted table rows unchanged').toEqual(beforeA);

        await control(chat(page), 'Close').click();
        const corrected = await selectChartBar(page, intervalB);
        expect(corrected.selection_revision).toBe(first.selection_revision + 1);
        observation.selections.push(corrected.selection_revision);
        const beforeB = persistedRows(profile);
        await openChat(page);
        const correction = 'Use the corrected selected interval and tell me its close.';
        // Lose the accepted send and first tool-continuation poll. Subsequent
        // terminal replies carry a valid shape but the wrong frozen revision.
        let lostSend = false;
        let lostPoll = false;
        let corruptEvidence = true;
        const path = '**' + workspaceEndpoint;
        const interrupt = async route => {
          const request = route.request();
          if (isAction(request, 'send') && !lostSend) {
            const admitted = await route.fetch({ maxRedirects: 0 });
            expect((await admitted.json()).result.outcome.result.turn_id).toBe(request.postDataJSON().request_id);
            lostSend = true;
            return route.abort('failed');
          }
          if (!isAction(request, 'poll')) return route.fallback();
          const response = await route.fetch({ maxRedirects: 0 });
          const frame = await response.json();
          const turn = frame.result?.outcome?.result?.turn;
          if (!lostPoll && turn?.state === 'running') {
            lostPoll = true;
            return route.abort('failed');
          }
          if (corruptEvidence && turn?.chart_evidence) {
            turn.chart_evidence.revision = 'wrong-synthetic-revision';
            turn.text = 'Uncorrelated chart answer.';
            return route.fulfill({ response, json: frame });
          }
          return route.fulfill({ response });
        };
        await page.route(path, interrupt);
        await send(page, correction);
        await expect(control(chat(page), 'Check turn status')).toBeVisible();
        await expect(chat(page).getByRole('article')).toHaveCount(1);
        await expect(chat(page)).not.toContainText('Uncorrelated chart answer.');
        await expectChartAnswer(page, intervalA);
        expect(lostSend && lostPoll).toBe(true);
        corruptEvidence = false;
        await control(chat(page), 'Check turn status').click();
        await expect.poll(() => peer.traffic.length, { timeout: 10_000 }).toBe(4);
        expectRequest(peer.traffic[2], [user(correction)], { tools: chartTools });
        observation.projections.push(expectToolResult(peer.traffic[3], correction, intervalB));
        await expectChartAnswer(page, intervalB);
        await page.unroute(path, interrupt);
        await expect(chat(page).getByRole('article')).toHaveCount(2);
        await expect(chat(page).getByText(closeAnswer(intervalA), { exact: true })).toBeVisible();
        expect(persistedRows(profile), 'Tool turn B leaves all persisted table rows unchanged').toEqual(beforeB);
        await testInfo.attach('two-selected-intervals', { body: await page.screenshot(), contentType: 'image/png' });

        await control(chat(page), 'Close').click();
        const csv = await inspectManualCsv(page, csvRevision);
        observation.selections.push(csv.selection_revision);
        const beforeDenied = persistedRows(profile);
        const workspaceBefore = await dispatch({ type: 'snapshot' });
        await openChat(page);
        await send(page, 'What is the selected CSV close?');
        await expect(chat(page).getByText('Selected chart data is unavailable to this model connection.', { exact: true })).toBeVisible();
        expect(peer.traffic, 'The display-only source is denied before any provider request').toHaveLength(4);
        expect(activations, 'Both chart tool turns used one activation').toHaveLength(1);
        expect(persistedRows(profile), 'Denied turns leave all persisted table rows unchanged').toEqual(beforeDenied);
        expect(await dispatch({ type: 'snapshot' })).toEqual(workspaceBefore);
        expect(JSON.stringify(peer.traffic)).not.toContain(savedTitle);
        expect(JSON.stringify(peer.traffic)).not.toContain(savedBody);
        observation.readOnlyTables = Object.keys(beforeDenied);
      } finally {
        await testInfo.attach('selected-chart-observations', {
          body: JSON.stringify({ ...observation, requests: peer.traffic }, null, 2), contentType: 'application/json',
        });
      }
    });
  }));
});

test('rotating a pending chart selection blocks tool data and cancellation discards a pending final reply', async ({ browser }, testInfo) => {
  await withPeer(peer => withProfile(profile => withWorkspace(browser, profile, async page => {
    const initial = Promise.withResolvers();
    const final = Promise.withResolvers();
    const lateAnswer = 'Late synthetic response must not appear.';
    try {
      peer.respond(() => initial.promise);
      await openSampleChart(page);
      await selectChartBar(page, intervalA);
      await askBeforeSetup(page);
      await activateRoute(page, await propose(page, peer.origin, true));
      await send(page);
      await expect.poll(() => peer.traffic.length, { timeout: 10_000 }).toBe(1);
      expectRequest(peer.traffic[0], [user(firstQuestion)], { tools: chartTools });
      await control(chat(page), 'Close').click();
      await selectChartBar(page, intervalB);
      const before = persistedRows(profile);
      initial.resolve(providerToolCall);
      await openChat(page);
      await expect(chat(page).getByText('The chart selection changed. Send the question again for the current selection.', { exact: true })).toBeVisible();
      expect(peer.traffic, 'A stale selection produces no continuation').toHaveLength(1);
      expect(persistedRows(profile)).toEqual(before);
      await expect(chat(page).getByRole('region', { name: 'Chart evidence used' })).toHaveCount(0);

      peer.respond(body => body?.tools ? providerToolCall : final.promise);
      const next = 'Synthetic request held before the final answer.';
      await send(page, next);
      await expect.poll(() => peer.traffic.length, { timeout: 10_000 }).toBe(3);
      expectRequest(peer.traffic[1], [user(next)], { tools: chartTools });
      expectToolResult(peer.traffic[2], next, intervalB);
      await control(chat(page), 'Cancel turn').click();
      await expect(chat(page).getByText(cancelled, { exact: true })).toBeVisible();
      await expect.poll(() => peer.traffic[2].closedWithoutReply).toBe(true);
      final.resolve(assistant(lateAnswer));
      await expect(chat(page).getByText(lateAnswer, { exact: true })).toHaveCount(0);
      await expect(chat(page).getByRole('region', { name: 'Chart evidence used' })).toHaveCount(0);
      expect(persistedRows(profile), 'Rotate and cancel created no writes beyond the manual selection').toEqual(before);
    } finally {
      initial.resolve(providerToolCall);
      final.resolve(assistant(lateAnswer));
      await retainTraffic(testInfo, peer.traffic);
    }
  })));
});

test('OpenAI settings manage a scoped session key and leave the Ollama route usable', async ({ browser }, testInfo) => {
  const cloudModel = 'synthetic-cloud-text-1';
  const keys = [0, 1].map(() => `synthetic-key-${randomUUID()}`);
  const cloudChoice = { kind: 'openai_responses', model: cloudModel };
  let openAiTurnsSubmitted;
  let deniedStatus;
  let unsupportedStatus;
  let restartLifetime;
  await withPeer(peer => withProfile(async profile => {
    await withWorkspace(browser, profile, async (page, dispatch, dispatchCredential) => {
      let sends = 0;
      page.on('request', request => { if (isAction(request, 'send')) sends += 1; });
      await openSampleChart(page);
      const selected = await selectChartBar(page, intervalA);
      await askBeforeSetup(page);
      await provider(page).selectOption('openai_responses');
      await expect(settings(page)).toContainText('This key is available only for this app session.');
      await expect(settings(page)).toContainText('No OpenAI API key configured.');
      await expect(selectedModel(page)).toHaveValue('');
      await expect(settings(page).getByLabel('Server origin')).toHaveCount(0);
      await expect(settings(page).getByLabel(chartAccessLabel)).toHaveCount(0);
      await selectedModel(page).fill(cloudModel);
      await expect(control(settings(page), 'Review connection')).toBeDisabled();

      const denied = await dispatchCredential({ action: 'replace', secret: keys[0] }, false);
      deniedStatus = denied.status;
      assertKeyAbsent(denied, keys);
      expect(denied).toMatchObject({ status: 403, cacheControl: 'no-store', body: { ok: false, error: { code: 'PERMISSION_DENIED' } } });
      const unsupported = await dispatchCredential({ action: 'read' });
      unsupportedStatus = unsupported.status;
      expect(unsupported).toMatchObject({ status: 400, cacheControl: 'no-store', body: { ok: false, error: { code: 'INVALID_ARGUMENT' } } });
      const absent = await dispatch(modelCommand({ action: 'propose', route: cloudChoice }));
      expect(absent).toMatchObject({ ok: true, result: { outcome: {
        state: 'failed', error: { code: 'PROVIDER_NOT_CONFIGURED' },
      } } });

      async function enableCloud() {
        await control(settings(page), 'Review connection').click();
        const review = settings(page).getByRole('region', { name: 'Review model connection' });
        await expect(review.getByText('OpenAI Responses', { exact: true })).toBeVisible();
        await expect(review.getByText('https://api.openai.com/v1/responses', { exact: true })).toBeVisible();
        await expect(review.getByText(cloudModel, { exact: true })).toBeVisible();
        await expect(review).toContainText('Typed questions and up to four earlier question and answer pairs in this session.');
        await expect(review).toContainText('OpenAI meters use under the API account or project associated with your key.');
        return activateRoute(page, control(review, 'Connect and enable'), 'OpenAI Responses');
      }

      await updateCloudKey(page, 'replace', 'Save API key', keys, keys[0]);
      const first = await enableCloud();
      const chartAttempt = await dispatch(modelCommand({ action: 'send', connection_id: first,
        question: 'Synthetic selected close?', selection_revision: selected.selection_revision }));
      expect(chartAttempt).toMatchObject({ ok: true, result: { outcome: { state: 'failed', error: { code: 'PERMISSION_DENIED' } } } });
      const statusAfterDenial = await dispatch(modelCommand({ action: 'status' }));
      expect(statusAfterDenial.result.outcome.result).toMatchObject({ connection: { connection_id: first }, turn_id: null });
      await control(chat(page), 'AI settings').click();
      await updateCloudKey(page, 'replace', 'Replace API key', keys, keys[1]);
      await assertCloudRevoked(dispatch, first);

      const second = await enableCloud();
      await control(chat(page), 'AI settings').click();
      await updateCloudKey(page, 'remove', 'Remove API key', keys);
      await expect(settings(page)).toContainText('No OpenAI API key configured.');
      await assertCloudRevoked(dispatch, second);

      await updateCloudKey(page, 'replace', 'Save API key', keys, keys[1]);
      await enableCloud();
      await control(chat(page), 'AI settings').click();
      await control(settings(page), 'Change connection').click();
      await provider(page).selectOption('ollama');
      await expect(selectedModel(page)).toHaveValue('');
      await activateRoute(page, await propose(page, peer.origin));
      openAiTurnsSubmitted = sends;
      expect(openAiTurnsSubmitted, 'No send was submitted on either route before the ordinary Ollama turn').toBe(0);
      expect(peer.traffic).toEqual([]);
      await send(page);
      await expect(chat(page).getByText('Synthetic peer reply 1', { exact: true })).toBeVisible();
      expect(sends).toBe(1);
      expect(peer.traffic).toHaveLength(1);
      expectRequest(peer.traffic[0], [user(firstQuestion)]);
      assertKeyAbsent(peer.traffic, keys);
    }, keys);

    await withWorkspace(browser, profile, async (page, dispatch, dispatchCredential) => {
      await askBeforeSetup(page);
      await provider(page).selectOption('openai_responses');
      await expect(settings(page)).toContainText('No OpenAI API key configured.');
      await expect(selectedModel(page)).toHaveValue('');
      const restarted = await dispatchCredential({ action: 'status' });
      assertKeyAbsent(restarted, keys);
      expect(restarted).toEqual({ status: 200, cacheControl: 'no-store', body: { ok: true, result: { configured: false, lifetime: 'session' } } });
      restartLifetime = restarted.body.result.lifetime;
      const status = await dispatch(modelCommand({ action: 'status' }));
      expect(status).toMatchObject({ ok: true, result: { outcome: {
        state: 'completed', result: { connection: null, turn_id: null },
      } } });
    }, keys);

    await testInfo.attach('synthetic-cloud-settings', {
      body: JSON.stringify({ deniedStatus, unsupportedStatus, replacedAndRemovedGrantsRevoked: true,
        configuredAfterRestart: false, restartLifetime, openAiTurnsSubmitted,
        ollamaTurns: peer.traffic.length, ollamaAuthorizationPresent: peer.traffic[0].authorizationPresent }),
      contentType: 'application/json',
    });
  }));
});
