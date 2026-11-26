import { createHash, randomUUID } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { expect, test } from '@playwright/test';
import { withNativePublishedProfile } from './published-revision-access-windows.fixture.mjs';
import { expectEmptyRevisionLibrary, expectWithdrawnRevisionDenied, openPublishedRevisionDialog, withdrawPublishedRevision } from './published-revision-controls.mjs';
import { control } from './workspace-controls.mjs';

const sourcePath = 'daily.csv';

function request(operation, revision_id, rest = {}) {
  return { operation: 'market.' + operation, payload: { version: 1, revision_id, ...rest }, requestId: randomUUID() };
}

function statusRequest(kind, revision_id) {
  return kind === 'folder' ? request('readLocalFolderRevision', revision_id, { include_details: false })
    : { operation: null, payload: { type: 'frozen_chart_status', revision_id, request_id: randomUUID() } };
}

async function invoke(page, args) {
  return page.evaluate(async payload => {
    try {
      return { result: await window.__TAURI_INTERNALS__.invoke('execute_operation', payload) };
    } catch (error) {
      return { error };
    }
  }, args);
}

function expectAvailable(kind, id, reply) {
  expect(reply.error).toBeUndefined();
  if (kind === 'folder') {
    expect(reply.result).toMatchObject({ revision: { revision_id: id }, bars: [], files: [] });
  } else {
    expect(reply.result).toEqual({ available: true });
  }
}

function expectMissing(kind, reply) {
  if (kind === 'folder') expect(reply.error?.code).toBe('SNAPSHOT_UNAVAILABLE');
  else expect(reply).toEqual({ result: { available: false } });
}

function expectWithdrawn(kind, reply) {
  if (kind === 'folder') expect(reply.error?.code).toBe('PERMISSION_DENIED');
  else expect(reply).toEqual({ result: { available: false, revoked: true } });
}

async function observeAdmission(page, ids, database, observation) {
  // Keep the desktop's native writer admitted but blocked in SQLite. The
  // transaction here writes no data. The unrelated UUID cannot revoke a fixture.
  const db = new DatabaseSync(database);
  const writeArgs = request('revokeLocalImportRevision', randomUUID());
  try {
    // Establish the application's persistent connection before taking the lock.
    expectAvailable('csv', ids.csv, await invoke(page, statusRequest('csv', ids.csv)));
    db.exec('BEGIN IMMEDIATE');
    const write = invoke(page, writeArgs);
    try {
      for (const kind of ['csv', 'folder']) {
        const probes = [];
        observation[kind] = { probes };
        await expect.poll(async () => {
          const args = statusRequest(kind, ids[kind]);
          const reply = await invoke(page, args);
          probes.push({ args, reply });
          return reply.error?.code;
        }, { timeout: 2000, intervals: [20, 30, 50] }).toBe('BUSY');
        const refusal = probes.at(-1).reply.error;
        expect(refusal.retryable).toBe(true);
        expect(refusal.details).toEqual({});
      }
    } finally {
      // SQLite returns its own BUSY while the native slot remains occupied.
      const reply = await write;
      observation.admittedWrite = { args: writeArgs, reply };
      expect(reply.error).toMatchObject({ code: 'BUSY', retryable: true });
      expect(reply.error.details).toEqual({});
    }
  } finally {
    db.close();
  }
  for (const kind of ['csv', 'folder']) {
    const args = statusRequest(kind, ids[kind]);
    const reply = await invoke(page, args);
    observation[kind].afterRelease = { args, reply };
    expectAvailable(kind, ids[kind], reply);
  }
}

async function attachIpcProbe(page, kind, id, missingId) {
  await page.evaluate(({ kind, id, missingId }) => {
    // Use Tauri's genuine IPC fetch, with its original headers and native
    // response. The test changes only the named UUID inputs and holds only
    // designated replies. No test entrypoint is present in the application.
    const original = window.fetch;
    const endpoint = window.__TAURI_INTERNALS__.convertFileSrc('execute_operation', 'ipc');
    const held = new Map();
    const events = [];
    let interruptedRow;
    let interruptedStatus;
    const probe = {
      phase: 'natural open',
      events,
      release(name) { held.get(name)?.(); },
      restore() {
        for (const release of held.values()) release();
        window.fetch = original;
        delete window.scopehubPublishedRevisionProbe;
      },
    };
    window.scopehubPublishedRevisionProbe = probe;
    window.fetch = async function (resource, options) {
      if (String(resource) !== endpoint || options?.method !== 'POST' || typeof options.body !== 'string') {
        return original.call(window, resource, options);
      }
      const args = JSON.parse(options.body);
      const { operation, payload } = args;
      const statusKind = payload.type === 'frozen_chart_status' ? 'csv'
        : operation === 'market.readLocalFolderRevision' && payload.include_details === false ? 'folder' : undefined;
      const subjectId = payload.revision_id;
      const type = statusKind ? 'status'
        : /SourceRow$/.test(operation) ? 'row'
          : /listLocal.*Revisions$/.test(operation) ? 'list'
            : /readLocal.*Revision$/.test(operation) ? 'chart'
              : /revokeLocal.*Revision$/.test(operation) ? 'withdrawal' : undefined;
      if (!type) return original.call(window, resource, options);
      const event = {
        type, phase: probe.phase, operation: operation ?? payload.type, id: subjectId, kind: statusKind,
        request_id: args.requestId ?? payload.request_id, requested_at_ms: performance.now(),
      };
      events.push(event);
      const interruptRow = type === 'row' && !interruptedRow;
      const interruptStatus = statusKind === kind && interruptedRow?.delivered && !interruptedStatus;
      if (event.phase === 'row interruption' && subjectId === id && (interruptRow || interruptStatus)) {
        if (interruptRow) interruptedRow = event;
        else interruptedStatus = event;
        event.controlled_revision_id = missingId;
        payload.revision_id = missingId;
        options = { ...options, body: JSON.stringify(args) };
      }
      if (type === 'row' && subjectId === id && event.phase === 'natural open') {
        probe.originalRow = args;
      }
      event.native_input = args;
      if (type === 'status' && subjectId === id) {
        if (statusKind !== kind && event.phase === 'other library lookup') event.gate = 'other';
        if (statusKind === kind && ['reopen', 'refresh 1', 'refresh 2'].includes(event.phase)) event.gate = 'own';
      }
      const response = await original.call(window, resource, options);
      const reply = await response.clone().json();
      // Store only facts needed to compare native reply with displayed access.
      event.native = response.headers.get('Tauri-Response') === 'ok'
        ? {
            available: reply.available, revoked: reply.revoked, id: reply.revision?.revision_id ?? reply.revision_id,
            listed: reply.revisions?.map(entry => entry.revision_id), fields: reply.raw_fields,
            closes: reply.bars?.map(entry => entry.bar.close),
          }
        : { error: reply.code, details: reply.details };
      event.native_at_ms = performance.now();
      if (event.gate) {
        await new Promise(resolve => {
          if (held.has(event.gate)) throw new Error('Overlapping same-gate native operation');
          held.set(event.gate, resolve);
        });
        held.delete(event.gate);
      }
      event.delivered = true;
      event.delivered_at_ms = performance.now();
      return response;
    };
  }, { kind, id, missingId });
}

async function readEvents(page) {
  return page.evaluate(() => window.scopehubPublishedRevisionProbe.events);
}

async function observePublicDrawer(page, kind, id, otherId, observations) {
  const isFolder = kind === 'folder';
  const otherKind = isFolder ? 'csv' : 'folder';
  const missingId = randomUUID();
  const result = { kind, id, other_id: otherId, controlled_missing_id: missingId, actions: [] };
  observations.push(result);
  const dialog = page.getByRole('dialog', { name: 'Open published CSV' });
  const draft = dialog.getByLabel('Revision ID');
  const entry = dialog.getByRole('button', { name: isFolder ? /^Open folder revision from daily\.csv/ : /^Open daily\.csv/ });
  const chart = dialog.getByRole('region', { name: 'Frozen local ' + (isFolder ? 'folder' : 'CSV') + ' chart' });
  const provenance = dialog.getByRole('region', { name: 'Published ' + (isFolder ? 'folder ' : '') + 'source provenance' });
  const rows = dialog.getByRole('table', { name: 'Published ' + (isFolder ? 'folder ' : '') + 'source rows' });
  const original = dialog.getByRole('region', { name: 'Original CSV fields' });
  const inspect = control(dialog, isFolder ? 'Inspect record 2 from ' + sourcePath : 'Inspect record 2');
  const click = name => control(dialog, name).click();
  const phase = value => page.evaluate(next => { window.scopehubPublishedRevisionProbe.phase = next; }, value);
  const switchKind = next => click(next === 'folder' ? 'Folder revisions' : 'Single-file revisions');
  const openExplicit = next => click(next === 'folder' ? 'Open frozen folder revision' : 'Open frozen chart');
  const release = gate => page.evaluate(name => window.scopehubPublishedRevisionProbe.release(name), gate);
  const openDrawer = async next => {
    await openPublishedRevisionDialog(page);
    if (next === 'folder') await switchKind(next);
    await expect(draft).toHaveValue('');
  };
  const hidden = async label => {
    for (const node of [entry, chart, provenance, rows, original]) await expect(node).toHaveCount(0);
    result.actions.push(label);
  };
  const showOriginal = async () => {
    await expect(chart.getByLabel('Selected bar')).toContainText('C 40');
    await expect(provenance).toContainText(id);
    await dialog.getByText('Source rows (2)', { exact: true }).click();
    await inspect.click();
    await expect(original.getByRole('row', { name: 'Native close 40', exact: true })).toBeVisible();
  };
  const listOperation = isFolder ? 'market.listLocalFolderRevisions' : 'market.listLocalImportRevisions';
  const revokeOperation = isFolder ? 'market.revokeLocalFolderRevision' : 'market.revokeLocalImportRevision';
  const refreshPhases = ['reopen', 'refresh 1', 'refresh 2'];
  const ownStatus = event => event.id === id && event.kind === kind
    && event.type === 'status' && refreshPhases.includes(event.phase);
  const settledLists = async () => (await readEvents(page))
    .filter(event => refreshPhases.includes(event.phase) && event.operation === listOperation && event.delivered)
    .map(event => [event.phase, event.native.listed]);
  const gatedEvent = async gate => (await readEvents(page)).find(event => event.gate === gate);

  await attachIpcProbe(page, kind, id, missingId);
  try {
    await openDrawer(kind);
    await entry.click();
    await showOriginal();
    result.actions.push('Ordinary native chart and original record displayed close 40.');
    const rowArgs = await page.evaluate(() => window.scopehubPublishedRevisionProbe.originalRow);
    expect(rowArgs.payload.revision_id).toBe(id);
    expect((await readEvents(page)).find(event => event.phase === 'natural open' && event.type === 'row').native.fields)
      .toContain('40');

    await phase('row interruption');
    await inspect.click();
    await hidden('Chart, provenance, list entry and original row were withheld after the native missing-row response.');
    await expect.poll(async () => (await readEvents(page)).some(event => event.phase === 'row interruption'
      && event.kind === kind && event.controlled_revision_id === missingId && event.delivered)).toBe(true);
    const interruption = (await readEvents(page)).filter(event => event.phase === 'row interruption' && event.controlled_revision_id);
    expect(interruption.find(event => event.type === 'row')?.native.error).toBe('SNAPSHOT_UNAVAILABLE');
    expect(interruption.find(event => event.type === 'status')?.native).toMatchObject(isFolder
      ? { error: 'SNAPSHOT_UNAVAILABLE' } : { available: false, revoked: undefined });
    expect(interruption.every(event => event.id === id && event.controlled_revision_id === missingId)).toBe(true);

    await phase('other library lookup');
    await switchKind(otherKind);
    await draft.fill(id);
    await openExplicit(otherKind);
    await expect.poll(async () => (await gatedEvent('other'))?.native).toMatchObject(isFolder
      ? { available: false, revoked: undefined } : { error: 'SNAPSHOT_UNAVAILABLE' });
    const beforeClose = await gatedEvent('other');
    expect(beforeClose).toMatchObject({ id, kind: otherKind });
    expect(beforeClose.controlled_revision_id).toBeUndefined();
    expect(beforeClose.delivered).toBeUndefined();
    await click('Close');
    await expect(dialog).toHaveCount(0);
    result.actions.push('Closed the actual dialog while the other library response was held.');

    await phase('reopen');
    await openDrawer(kind);
    await expect.poll(settledLists).toEqual([['reopen', [id]]]);
    for (const number of [1, 2]) {
      await phase('refresh ' + number);
      await click('Refresh revisions');
      await expect.poll(async () => (await settledLists()).length).toBe(number + 1);
      await expect(draft).toHaveValue('');
      await hidden('Refreshed the current library with the same native list membership and retained withheld content.');
      await expectEmptyRevisionLibrary(dialog, kind);
    }
    expect(await settledLists()).toEqual(refreshPhases.map(name => [name, [id]]));
    expect((await readEvents(page)).filter(ownStatus)).toEqual([]);
    await release('other');
    await expect.poll(async () => (await gatedEvent('own'))?.native).toMatchObject(isFolder
      ? { id, closes: [] } : { available: true });
    expect((await readEvents(page)).filter(ownStatus)).toHaveLength(1);
    expect(await gatedEvent('own')).toMatchObject({ id, kind });
    expect((await gatedEvent('own')).controlled_revision_id).toBeUndefined();
    expect((await gatedEvent('other')).delivered).toBe(true);
    expect((await gatedEvent('own')).delivered).toBeUndefined();
    await hidden('The unmodified matching native status was admitted. The entry remained withheld before delivery.');
    await expectEmptyRevisionLibrary(dialog, kind);
    await release('own');
    await expect(entry).toBeVisible();
    await expect(chart).toHaveCount(0);
    expect((await readEvents(page)).filter(event => event.type === 'chart' && event.id === id && event.phase !== 'other library lookup'))
      .toHaveLength(1);
    result.actions.push('Matching status made the entry discoverable without opening chart details.');

    await phase('explicit restored open');
    await entry.click();
    await showOriginal();
    expect((await readEvents(page)).filter(event => event.phase === 'explicit restored open' && event.type === 'chart'))
      .toMatchObject([{ id, delivered: true, native: { id, closes: [38, 40] } }]);
    result.actions.push('Explicit open displayed the same revision, bars and original close 40 again.');

    await phase('confirmed withdrawal');
    await withdrawPublishedRevision(dialog, kind);
    result.withdrawal_live_status = await dialog.getByRole('status').allTextContents();
    await hidden('The visible withdrawal action removed the entry and original data.');
    expect((await readEvents(page)).filter(event => event.operation === revokeOperation))
      .toMatchObject([{ id, delivered: true, native: { id } }]);
    expectWithdrawn(kind, await invoke(page, statusRequest(kind, id)));
    // Sequential direct reads use the same real Tauri dispatcher as the UI.
    expect((await invoke(page, { ...rowArgs, requestId: randomUUID() })).error?.code).toBe('PERMISSION_DENIED');
    const surviving = await invoke(page, statusRequest(otherKind, otherId));
    if (isFolder) expectAvailable(otherKind, otherId, surviving);
    else expectWithdrawn(otherKind, surviving); // The folder trial withdrew its own revision first.
    await click('Close');
    await expect(dialog).toHaveCount(0);
    await phase('reopen after withdrawal');
    await openDrawer(kind);
    await expectEmptyRevisionLibrary(dialog, kind);
    await click('Refresh revisions');
    await expectEmptyRevisionLibrary(dialog, kind);
    await expect.poll(async () => {
      const lists = (await readEvents(page)).filter(event => event.phase === 'reopen after withdrawal'
        && event.operation === listOperation && event.delivered);
      return lists.map(event => event.native.listed);
    }).toEqual([[], []]);
    await expect(draft).toHaveValue('');
    await hidden('The withdrawn revision remained absent after close, reopen and refresh.');
    await phase('denied explicit reopen');
    await draft.fill(id);
    await openExplicit(kind);
    await expectWithdrawnRevisionDenied(dialog);
    await hidden('An explicit retry reported confirmed withdrawal without displaying the old row or chart.');
    expect((await readEvents(page)).filter(event => ['confirmed withdrawal', 'reopen after withdrawal', 'denied explicit reopen'].includes(event.phase)
      && event.type === 'chart')).toEqual([]);
    await click('Close');
    await expect(dialog).toHaveCount(0);
  } finally {
    try {
      result.calls = await readEvents(page);
    } finally {
      await page.evaluate(() => window.scopehubPublishedRevisionProbe.restore());
    }
  }
}

test('observe native admission and public CSV and folder access across dialog reopen', async ({}, testInfo) => {
  test.skip(process.platform !== 'win32' || process.arch !== 'x64', 'Requires a native Windows x64 desktop and WebView2.');
  const observation = {
    admission: {}, public_drawer: [],
    controlled_input: 'A single missing revision UUID replaces the subject in the designated source-row read and its first status query. Their error/absence replies come from the compiled app.',
    controlled_delivery: 'The other-library response and its matching successor are held after real native receipt and released without replacement.',
    operator_scope: 'Physical Windows, packaged installer and hands-on owner qualification are separate.',
  };
  let outcome = 'failed';
  try {
    await withNativePublishedProfile(testInfo, observation, async ({ page, ids, database }) => {
      await invoke(page, statusRequest('csv', ids.csv)).then(reply => expectAvailable('csv', ids.csv, reply));
      expectMissing('csv', await invoke(page, statusRequest('csv', randomUUID())));
      expectMissing('folder', await invoke(page, statusRequest('folder', randomUUID())));
      await observeAdmission(page, ids, database, observation.admission);
      await observePublicDrawer(page, 'folder', ids.folder, ids.csv, observation.public_drawer);
      await observePublicDrawer(page, 'csv', ids.csv, ids.folder, observation.public_drawer);
    });
    outcome = 'passed';
  } finally {
    observation.outcome = outcome;
    const bounded = {
      ...observation,
      admission: Object.fromEntries(Object.entries(observation.admission).map(([name, entry]) =>
        [name, entry.probes ? { ...entry, probes: entry.probes.slice(-5) } : entry])),
      public_drawer: observation.public_drawer.map(entry =>
        ({ ...entry, calls: entry.calls?.slice(0, 60), calls_total: entry.calls?.length })),
    };
    const output = dirname(testInfo.project.outputDir);
    const retained = JSON.stringify(bounded, null, 2) + '\n';
    await mkdir(output, { recursive: true });
    await writeFile(join(output, 'observation.json'), retained);
    // Bind the bounded sidecar to the check log, including failed launches.
    console.log('native_observation_sha256=' + createHash('sha256').update(retained).digest('hex'));
    await testInfo.attach('windows-published-revision-access', {
      body: JSON.stringify(observation, null, 2), contentType: 'application/json',
    });
  }
});
