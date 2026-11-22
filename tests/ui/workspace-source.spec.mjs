import { randomUUID } from 'node:crypto';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { expect, test } from '@playwright/test';
import { buildPreview, seedDisplayOnlyChart, withProfile, withWorkspace, workspaceEndpoint } from './ai-workflow-fixture.mjs';
import { control, openPanel } from './workspace-controls.mjs';

const bar = {
  time_ms: Date.parse('2026-02-03T00:00:00Z'), trading_day: '2026-02-03',
  open: 73, high: 75, low: 72, close: 74, volume: 13, is_closed: true,
};
const busy = { ok: false, error: { code: 'BUSY', retryable: true } };
const snapshotRequest = { type: 'snapshot' };
const chart = page => page.getByRole('region', { name: 'Price chart' });
const useSource = dialog => control(dialog, 'Use as workspace source');
const alert = (dialog, message) => dialog.getByRole('alert').filter({ hasText: message }).first();

test.beforeAll(async ({}, testInfo) => {
  testInfo.setTimeout(900_000);
  await buildPreview();
});

function persistedSource(profile) {
  const database = new DatabaseSync(join(profile.directory, 'workspace.sqlite3'), { readOnly: true });
  try {
    return { ...database.prepare(`
      SELECT selection_revision, COALESCE(kind, 'none') AS kind, revision_id
      FROM workspaces LEFT JOIN workspace_source_preference ON workspace_id = id WHERE id = 'main'
    `).get() };
  } finally {
    database.close();
  }
}

function requestType(request) {
  if (new URL(request.url()).pathname === workspaceEndpoint) return request.postDataJSON()?.type;
}

async function openPublishedCsv(page, revision, sampleDemo) {
  if (sampleDemo) {
    await control(chart(page), 'Open offline sample').click();
    await expect(page.locator('.connection')).toHaveText('Offline sampleSimulated');
    await page.evaluate(() => { location.hash = '#demo'; });
    await openPanel(page, 'Data sources');
  }
  await control(sampleDemo ? page : chart(page), 'Open published CSV').click({ timeout: 8_000 });
  const dialog = page.getByRole('dialog', { name: 'Open published CSV' });
  await dialog.getByLabel('Revision ID').fill(revision);
  await control(dialog, 'Open frozen chart').click();
  await expect(useSource(dialog)).toBeEnabled();
  return dialog;
}

async function withPublishedSource(browser, testInfo, use, { gateStatus = false, sampleDemo = false } = {}) {
  const stages = [];
  try {
    await withProfile(async profile => {
      const revision = randomUUID();
      await seedDisplayOnlyChart(profile, revision, bar);
      await withWorkspace(browser, profile, async (page, dispatch, _credentials, holdRevisionRead) => {
        const dialog = await openPublishedCsv(page, revision, sampleDemo);
        const persisted = () => persistedSource(profile);
        const before = persisted();
        expect(before).toEqual({
          selection_revision: sampleDemo ? 1 : 0, kind: sampleDemo ? 'sample' : 'none', revision_id: null,
        });
        const record = (stage, http, code) => {
          if (stages.length < 20) stages.push({ stage, http, code, revision: persisted().selection_revision });
        };
        const source = { kind: 'frozen', revision_id: revision };
        await use({ page, dispatch, holdRevisionRead, dialog, persisted, before, record, source });
      }, [], gateStatus);
    });
  } finally {
    const diagnostic = JSON.stringify(stages);
    console.info('Published source stages', diagnostic);
    await testInfo.attach('published-source-stages', { body: diagnostic, contentType: 'application/json' });
  }
}

// Observe the actual browser call and relay the real response unless this case
// controls delivery. Independent dispatches still go straight to the preview.
async function observeWrites(page, record, onWrite, onSnapshot) {
  const writes = [];
  await page.route(url => url.pathname === workspaceEndpoint, async route => {
    const frame = route.request().postDataJSON();
    if (frame.type === 'workspace_source') {
      const response = await route.fetch({ timeout: 6_000 });
      const body = await response.json();
      const delivery = Promise.withResolvers();
      const write = { frame, status: response.status(), body, delivered: delivery.promise };
      writes.push(write);
      record('foreground-write', write.status, body.error?.code);
      try {
        if (await onWrite?.(write, writes.length) === 'drop') return await route.abort('failed');
        return await route.fulfill({ response });
      } finally {
        delivery.resolve();
      }
    }
    if (frame.type === 'snapshot' && writes.length > 0 && onSnapshot) return onSnapshot(route);
    return route.continue();
  });
  return writes;
}

function expectedSnapshot(source, selection_revision) {
  return { ok: true, result: { workspace: { id: 'main' }, selection_revision, source } };
}

async function expectIndependentSnapshot(dispatch, source, revision) {
  await expect.poll(async () => (await dispatch(snapshotRequest)), {
    timeout: 3_000, message: 'The independent registry snapshot agrees with the durable selection',
  }).toMatchObject(expectedSnapshot(source, revision));
}

async function expectSelectedChart(page, dialog) {
  await expect(dialog).toBeHidden({ timeout: 9_000 });
  await expect(page.locator('.connection')).toHaveText('display-only.csv');
  await expect(chart(page).locator('.chart canvas').first()).toBeVisible();
}

async function commitPeer(dispatch, expected_selection_revision) {
  const source = { kind: 'sample', instrument_id: null };
  const result = await dispatch({
    type: 'workspace_source', request_id: randomUUID(), expected_selection_revision, source,
  });
  expect(result).toMatchObject({ ok: true, result: { selection_revision: expected_selection_revision + 1 } });
  return source;
}

const contentionCases = [
  ['success', 'one click selects a published CSV during a genuine background revision read'],
  ['peer', 'a peer selection between pre-admission BUSY and retry is preserved'],
  ['cancel', 'closing the CSV dialog cancels a retry of a rejected foreground request'],
];

for (const [scenario, title] of contentionCases) {
  test(title, async ({ browser }, testInfo) => {
    await withPublishedSource(browser, testInfo, async ({ page, dispatch, holdRevisionRead, dialog, persisted, before, record, source }) => {
      let release;
      let peerSource;
      const rejected = Promise.withResolvers();
      const deliver = Promise.withResolvers();
      const writes = await observeWrites(page, record, async (write, attempt) => {
        if (attempt !== 1) return;
        expect(write.status).toBe(429);
        expect(write.body).toEqual(busy);
        expect(persisted()).toEqual(before);
        if (scenario === 'cancel') {
          rejected.resolve();
          await deliver.promise;
        }
        const statusDelivered = page.waitForResponse(response => requestType(response.request()) === 'frozen_chart_status',
          { timeout: 5_000 });
        release();
        const response = await statusDelivered;
        expect(await response.json()).toEqual({ ok: true, result: { available: true } });
        record('background-revision-released', response.status());
        if (scenario === 'peer') peerSource = await commitPeer(dispatch, before.selection_revision);
      });
      release = await holdRevisionRead();
      record('background-revision-held');
      try {
        await useSource(dialog).click();
        if (scenario === 'cancel') {
          await rejected.promise;
          const pending = chart(page).getByText('Checking workspace source…', { exact: true });
          await expect(pending).toBeAttached();
          await control(dialog, 'Close').click();
          await expect(dialog).toBeHidden();
          deliver.resolve();
          await expect(pending).toHaveCount(0);
          expect(writes).toHaveLength(1);
          expect(persisted()).toEqual(before);
          await expectIndependentSnapshot(dispatch, null, 0);
          record('rejected-selection-cancelled');
        } else if (scenario === 'peer') {
          await expect(alert(dialog, 'The workspace changed.')).toBeVisible();
          expect(writes.map(({ status, body }) => [status, body.ok, body.error?.code])).toEqual([
            [429, false, 'BUSY'], [200, false, 'STALE_CONTEXT'],
          ]);
          expect(persisted()).toEqual({ selection_revision: 1, kind: 'sample', revision_id: null });
          await expectIndependentSnapshot(dispatch, peerSource, 1);
          record('peer-selection-preserved');
        } else {
          await expectSelectedChart(page, dialog);
          expect(writes.map(({ status, body }) => [status, body.ok])).toEqual([[429, false], [200, true]]);
          expect(writes[1].body.result.selection_revision).toBe(1);
          expect(persisted()).toEqual({ selection_revision: 1, kind: 'frozen', revision_id: source.revision_id });
          await expectIndependentSnapshot(dispatch, { ...source, status: 'available' }, 1);
          record('own-selection-restored');
        }
        expect(writes[0].frame).toMatchObject({ expected_selection_revision: 0, source });
        if (scenario !== 'cancel') expect(writes[1].frame).toEqual(writes[0].frame);
      } finally {
        deliver.resolve();
        release();
      }
    }, { gateStatus: true });
  });
}

test('the accepted source retries a busy readback without another write', async ({ browser }, testInfo) => {
  await withPublishedSource(browser, testInfo, async ({ page, dispatch, dialog, persisted, record, source }) => {
    const readbacks = [];
    const writes = await observeWrites(page, record, undefined, async route => {
      if (readbacks.length === 0) {
        expect(persisted()).toEqual({ selection_revision: 1, kind: 'frozen', revision_id: source.revision_id });
        readbacks.push(429);
        record('readback-busy', 429, 'BUSY');
        return route.fulfill({ status: 429, json: busy });
      }
      const response = await route.fetch({ timeout: 6_000 });
      const snapshot = await response.json();
      readbacks.push(response.status());
      expect(snapshot).toMatchObject(expectedSnapshot({ ...source, status: 'available' }, 1));
      record('readback-current', response.status());
      return route.fulfill({ response });
    });
    await useSource(dialog).click();
    await expectSelectedChart(page, dialog);
    expect(writes.map(write => write.body.result.selection_revision)).toEqual([1]);
    expect(readbacks).toEqual([429, 200]);
    await expectIndependentSnapshot(dispatch, { ...source, status: 'available' }, 1);
  });
});

for (const lostReply of [false, true]) {
  test(lostReply ? 'a lost mutation reply with unavailable readback requires read-only recovery'
    : 'an acknowledged mutation with unavailable readback requires read-only recovery', async ({ browser }, testInfo) => {
    await withPublishedSource(browser, testInfo, async ({ page, dispatch, dialog, persisted, record, source, before }) => {
      const committed = before.selection_revision + 1;
      let readbacks = 0;
      const writes = await observeWrites(page, record, write => {
        expect(write.body).toMatchObject({ ok: true, result: { selection_revision: committed } });
        if (lostReply) {
          record('foreground-reply-dropped');
          return 'drop';
        }
      }, async route => {
        readbacks += 1;
        if (readbacks !== 1) return route.continue();
        expect(persisted()).toEqual({ selection_revision: committed, kind: 'frozen', revision_id: source.revision_id });
        record('readback-unavailable', 503, 'WORKER_EXITED');
        return route.fulfill({ status: 503, json: { ok: false, error: { code: 'WORKER_EXITED', retryable: true } } });
      });
      await useSource(dialog).click();
      await expect(alert(dialog, lostReply
        ? 'The source request could not be confirmed. Reload the workspace state before changing the source again.'
        : 'The source was saved. Reload the workspace state to confirm the current selection.')).toBeVisible();
      await expect(useSource(dialog)).toBeDisabled();
      expect(writes).toHaveLength(1);
      expect(readbacks).toBe(1);
      await control(dialog, 'Reload workspace state').click();
      if (lostReply) {
        await expect(alert(dialog, 'The source request could not be confirmed. Review the current workspace source.')).toBeVisible();
        await control(dialog, 'Close').click();
      }
      await expectSelectedChart(page, dialog);
      expect(writes).toHaveLength(1);
      expect(readbacks).toBe(2);
      await expectIndependentSnapshot(dispatch, { ...source, status: 'available' }, committed);
      record('read-only-recovery-complete');
    }, { sampleDemo: lostReply });
  });
}

test('an acknowledged source never replaces a later peer selection during readback', async ({ browser }, testInfo) => {
  await withPublishedSource(browser, testInfo, async ({ page, dispatch, dialog, persisted, record }) => {
    let peerSource;
    const writes = await observeWrites(page, record, async write => {
      expect(write.body).toMatchObject({ ok: true, result: { selection_revision: 1 } });
      peerSource = await commitPeer(dispatch, 1);
      record('peer-selection-committed');
    });
    await useSource(dialog).click();
    await expect(alert(dialog, 'The workspace selection changed after this request was saved.')).toBeVisible();
    expect(writes).toHaveLength(1);
    expect(persisted()).toEqual({ selection_revision: 2, kind: 'sample', revision_id: null });
    await expectIndependentSnapshot(dispatch, peerSource, 2);
    await control(dialog, 'Close').click();
    await expect(page.locator('.connection')).toHaveText('Offline sampleSimulated');
    record('peer-selection-restored');
  });
});

for (const holdWrite of [true, false]) {
  test(holdWrite ? 'closing the CSV dialog leaves read-only recovery when a committed response stalls'
    : 'a stalled snapshot after acknowledgement exposes read-only recovery and fences late state', async ({ browser }, testInfo) => {
    await withPublishedSource(browser, testInfo, async ({ page, dispatch, dialog, persisted, record, source }) => {
      const stalled = Promise.withResolvers();
      const release = Promise.withResolvers();
      const readbackDelivered = Promise.withResolvers();
      let readbacks = 0;
      const writes = await observeWrites(page, record, async write => {
        expect(write.body).toMatchObject({ ok: true, result: { selection_revision: 1 } });
        if (!holdWrite) return;
        record('committed-response-held', write.status);
        stalled.resolve();
        await release.promise;
      }, async route => {
        if (++readbacks !== 1) return route.continue();
        if (holdWrite) {
          record('first-recovery-snapshot-unavailable', 503, 'WORKER_EXITED');
          return route.fulfill({ status: 503, json: { ok: false, error: { code: 'WORKER_EXITED', retryable: true } } });
        }
        const response = await route.fetch({ timeout: 6_000 });
        expect(await response.json()).toMatchObject(expectedSnapshot({ ...source, status: 'available' }, 1));
        record('committed-snapshot-held', response.status());
        stalled.resolve();
        await release.promise;
        try {
          await route.fulfill({ response });
        } finally {
          readbackDelivered.resolve();
        }
      });

      try {
        await useSource(dialog).click();
        await stalled.promise;
        expect(persisted()).toEqual({ selection_revision: 1, kind: 'frozen', revision_id: source.revision_id });
        const pending = chart(page).getByText('Checking workspace source…', { exact: true });
        await expect(pending).toBeAttached();
        if (holdWrite) {
          await control(dialog, 'Close').click();
          await expect(dialog).toBeHidden();
        }

        const view = holdWrite ? chart(page) : dialog;
        await expect(alert(view, holdWrite
          ? 'The source request could not be confirmed. Reload the workspace state before changing the source again.'
          : 'The source was saved. Reload the workspace state to confirm the current selection.')).toBeVisible({ timeout: 18_000 });
        await expect(pending).toHaveCount(0);
        if (!holdWrite) await expect(useSource(dialog)).toBeDisabled();
        const reload = control(view, 'Reload workspace state');
        await expect(reload).toBeEnabled();
        record('stalled-request-recovery-available');
        expect(writes).toHaveLength(1);

        const peerSource = await commitPeer(dispatch, 1);
        await reload.click();
        if (!holdWrite) {
          await expect(alert(dialog, 'The workspace selection changed after this request was saved.')).toBeVisible();
          await control(dialog, 'Close').click();
        }
        await expect(page.locator('.connection')).toHaveText('Offline sampleSimulated');
        await expectIndependentSnapshot(dispatch, peerSource, 2);

        release.resolve();
        await (holdWrite ? writes[0].delivered : readbackDelivered.promise);
        await expect(dialog).toBeHidden();
        await expect(page.locator('.connection')).toHaveText('Offline sampleSimulated');
        expect(readbacks).toBe(2);
        expect(writes).toHaveLength(1);
        expect(persisted()).toEqual({ selection_revision: 2, kind: 'sample', revision_id: null });
        record('late-delivery-fenced');
      } finally {
        release.resolve();
      }
    });
  });
}
