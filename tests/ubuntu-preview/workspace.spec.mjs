import { execFile, spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { once } from 'node:events';
import { chmod, mkdtemp, readFile, rm, stat } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { promisify } from 'node:util';
import { expect, test } from '@playwright/test';
import { control } from '../ui/workspace-controls.mjs';

const exec = promisify(execFile);
const root = fileURLToPath(new URL('../../', import.meta.url));
const ui = join(root, 'apps/desktop/ui');
const vite = pathToFileURL(join(ui, 'node_modules/vite/dist/node/index.js')).href;
const viteCli = join(ui, 'node_modules/vite/bin/vite.js');
const workspaceBridge = join(root, 'target/debug', process.platform === 'win32' ? 'workspace_bridge.exe' : 'workspace_bridge');
const python = join(root, '.venv', ...(process.platform === 'win32' ? ['Scripts', 'python.exe'] : ['bin', 'python']));
const workspaceId = 'synthetic-workspace';
const endpoint = '/__scopehub/workspace';
const snapshot = { type: 'snapshot' };
// This malformed snapshot checks admission without becoming a competing
// SQLite read when the actual producer is deliberately suspended.
const admissionProbe = { type: 'snapshot', request_id: 1 };
const service = `
  const [entry, mode, port] = process.argv.slice(1);
  const { createServer } = await import(entry);
  const server = await createServer({ mode, server: { host: '127.0.0.1', port: Number(port), strictPort: true } });
  process.once('message', async () => {
    await server.close();
    process.disconnect();
  });
  await server.listen();
`;

test.beforeAll(async ({}, testInfo) => {
  testInfo.setTimeout(300_000);
  await exec('cargo', [
    'build', '--locked', '-p', 'scopehub-persistence', '--bin', 'workspace_bridge',
    '-p', 'scopehub-worker-host', '--example', 'history_bridge', '--target-dir', 'target',
  ], { cwd: root, timeout: 295_000 });
});

test('fresh browsers restore committed timeframe and eligible undo after service restarts', async ({ browser }, testInfo) => {
  const profile = await makeProfile();
  const port = await freePort();
  const observation = [];
  try {
    await inService(port, profile, 'ubuntu-workspace', async server => {
      const writer = await writerPid(server);
      await inFreshBrowser(browser, server, async (page, initial) => {
        expect(state(initial)).toEqual({ revision: 0, timeframe: '1m', undo: null });
        await expect(control(page, '1m')).toHaveAttribute('aria-pressed', 'true');
        await expect(control(page, 'Undo timeframe change')).toBeDisabled();
        const reply = await act(page, '5m', 'chart.setTimeframe');
        expect(reply.result.outcome).toMatchObject({
          state: 'committed', result: { timeframe: '5m', revision: 1 }, receipt: { undoable: true },
        });
        await expect(control(page, '5m')).toHaveAttribute('aria-pressed', 'true');
        expect(await writerPid(server)).toBe(writer);
        observation.push({ visit: 'first', vite: server.pid, writer, receipt: reply.result.outcome.receipt.operation_id });
      });
    });
    await expect.poll(() => alive(observation[0].writer)).toBe(false);
    expect((await stat(join(profile.root, 'workspace.sqlite3'))).size).toBeGreaterThan(0);

    await inService(port, profile, 'ubuntu-workspace', async server => {
      const writer = await writerPid(server);
      expect(server.pid).not.toBe(observation[0].vite);
      expect(writer).not.toBe(observation[0].writer);
      await inFreshBrowser(browser, server, async (page, initial) => {
        expect(state(initial)).toEqual({
          revision: 1, timeframe: '5m',
          undo: { operation_id: observation[0].receipt, committed_workspace_revision: 1 },
        });
        await expect(control(page, '5m')).toHaveAttribute('aria-pressed', 'true');
        const undo = control(page, 'Undo timeframe change');
        await expect(undo).toBeEnabled();
        await undo.focus();
        const response = page.waitForResponse(mutationResponse('workspace.undo'));
        await page.keyboard.press('Enter');
        const accepted = await response;
        expect(accepted.request().postDataJSON().intent.arguments).toEqual({ operation_id: observation[0].receipt });
        const inverse = (await accepted.json()).result.outcome;
        expect(inverse).toMatchObject({
          state: 'committed', result: { timeframe: '1m', revision: 2 }, receipt: { undoable: false },
        });
        expect(inverse.receipt.operation_id).not.toBe(observation[0].receipt);
        await expect(control(page, '1m')).toHaveAttribute('aria-pressed', 'true');
        await expect(undo).toBeDisabled();
        expect(await writerPid(server)).toBe(writer);
        observation.push({ visit: 'undo', vite: server.pid, writer, receipt: inverse.receipt.operation_id });
      });
    });
    await expect.poll(() => alive(observation[1].writer)).toBe(false);

    await inService(port, profile, 'ubuntu-workspace', async server => {
      const writer = await writerPid(server);
      expect(server.pid).not.toBe(observation[1].vite);
      expect(writer).not.toBe(observation[1].writer);
      await inFreshBrowser(browser, server, async (page, initial) => {
        expect(state(initial)).toEqual({ revision: 2, timeframe: '1m', undo: null });
        await expect(control(page, '1m')).toHaveAttribute('aria-pressed', 'true');
        await expect(control(page, 'Undo timeframe change')).toBeDisabled();
        observation.push({ visit: 'after-undo', vite: server.pid, writer, restored: state(initial) });
      });
    });
    await expect.poll(() => alive(observation[2].writer)).toBe(false);
    await testInfo.attach('workspace-restarts', { body: JSON.stringify(observation), contentType: 'application/json' });
  } finally {
    await profile.close();
  }
});

test('private decision notes require consent and retain the dated original separately from outcomes', async ({ browser }) => {
  const profile = await makeProfile();
  const other = await makeProfile();
  const port = await freePort();
  const declinedThesis = 'Synthetic draft that should never be saved';
  const thesis = 'Synthetic thesis at decision time';
  const outcome = 'Synthetic later observation';
  let originalWriter;
  try {
    await inService(port, profile, 'ubuntu-workspace', async server => {
      originalWriter = await writerPid(server);
      await inFreshBrowser(browser, server, async page => {
        await act(page, '5m', 'chart.setTimeframe');
        await control(page, 'Decision notes').click();
        const notes = page.getByRole('dialog', { name: 'Decision notes' });
        const newNote = notes.getByRole('button', { name: 'New decision note' });
        await newNote.click();
        await notes.getByLabel('Decision date').fill('2026-01-02');
        await notes.getByLabel('Thesis').fill(declinedThesis);
        await notes.getByLabel('Choice').fill('Synthetic wait decision');
        await notes.getByRole('button', { name: 'Review decision note' }).click();
        await expect(notes.getByRole('heading', { name: 'Save this decision note locally?' })).toBeVisible();
        const declined = page.waitForResponse(mutationResponse('decisionNote.save'));
        await notes.getByRole('button', { name: 'Cancel action' }).click();
        expect((await (await declined).json()).result.outcome).toMatchObject({ state: 'failed', error: { code: 'CANCELLED' } });
        await expect(notes.getByText('No saved decision notes.')).toBeVisible();
        await expect(notes.getByLabel('Thesis')).toHaveCount(0);

        await newNote.click();
        await notes.getByLabel('Decision date').fill('2026-01-03');
        await notes.getByLabel('Thesis').fill(thesis);
        await notes.getByLabel('Choice').fill('Wait for the next synthetic revision');
        await notes.getByLabel('Source ID').fill('synthetic-series');
        await notes.getByLabel('Source revision').fill('frozen-input');
        await notes.getByLabel('Locator').fill('fixture:2026-01-03');
        await notes.getByRole('button', { name: 'Review decision note' }).click();
        await expect(notes.getByRole('heading', { name: 'Save this decision note locally?' })).toBeVisible();

        const lost = await dropApprovalReply(page, server, 'decisionNote.save');
        await notes.getByRole('button', { name: 'Save decision note' }).click();
        await expect(notes.getByRole('button', { name: 'Retry confirmation' })).toBeEnabled();
        const committed = lost.outcome();
        expect(committed).toMatchObject({ state: 'committed', receipt: { committed_workspace_revision: 2, undoable: false } });
        expect(committed).not.toHaveProperty('result');
        await lost.restore();
        const replay = page.waitForResponse(mutationResponse('decisionNote.save'));
        await notes.getByRole('button', { name: 'Retry confirmation' }).click();
        expect((await (await replay).json()).result.outcome).toEqual(committed);
        await expect(notes.getByText(thesis)).toBeVisible();
        await expect(notes.getByText('No later outcomes.')).toBeVisible();
        await notes.getByRole('button', { name: 'Close decision notes' }).click();
        await expect(control(page, '5m')).toHaveAttribute('aria-pressed', 'true');
        await expect(control(page, 'Undo timeframe change')).toBeEnabled();
        expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
      });
    });

    await inService(await freePort(), other, 'ubuntu-workspace', async server => {
      await inFreshBrowser(browser, server, async page => {
        await control(page, 'Decision notes').click();
        const notes = page.getByRole('dialog', { name: 'Decision notes' });
        await expect(notes.getByText('No saved decision notes.')).toBeVisible();
      });
    });

    await inService(port, profile, 'ubuntu-workspace', async server => {
      expect(await writerPid(server)).not.toBe(originalWriter);
      await inFreshBrowser(browser, server, async (page, initial) => {
        expect(state(initial)).toMatchObject({ revision: 2, timeframe: '5m', undo: { committed_workspace_revision: 1 } });
        await control(page, 'Decision notes').click();
        const notes = page.getByRole('dialog', { name: 'Decision notes' });
        await notes.getByRole('button', { name: /2026-01-03/ }).click();
        const original = notes.getByRole('region', { name: 'Original decision' });
        await expect(original.getByText(thesis)).toBeVisible();
        await expect(original.getByText('synthetic-series · frozen-input · fixture:2026-01-03')).toBeVisible();
        await expect(original.getByText('Wait for the next synthetic revision')).toBeVisible();

        await notes.getByRole('button', { name: 'Add later outcome' }).click();
        await notes.getByLabel('Outcome date').fill('2026-02-04');
        await notes.getByLabel('Outcome', { exact: true }).fill(outcome);
        await notes.getByRole('button', { name: 'Review later outcome' }).click();
        await notes.getByRole('button', { name: 'Save later outcome' }).click();
        const outcomes = notes.getByRole('region', { name: 'Later outcomes' });
        await expect(outcomes.getByText(outcome)).toBeVisible();
        await expect(outcomes.getByText('2026-02-04')).toBeVisible();
        await expect(original.getByText('2026-01-03', { exact: true })).toBeVisible();

        await notes.getByRole('button', { name: 'Review deletion' }).click();
        await expect(notes.getByRole('heading', { name: 'Delete this decision note and its later outcomes?' })).toBeVisible();
        const lost = await dropApprovalReply(page, server, 'decisionNote.delete');
        await notes.getByRole('button', { name: 'Delete decision note' }).click();
        await expect(notes.getByRole('button', { name: 'Retry confirmation' })).toBeEnabled();
        expect(lost.outcome()).toMatchObject({ state: 'committed', receipt: { committed_workspace_revision: 4 } });
        await expect(notes.getByText(thesis)).toHaveCount(0);
        await expect(notes.getByText(outcome)).toHaveCount(0);
        await lost.restore();
        await notes.getByRole('button', { name: 'Retry confirmation' }).click();
        await expect(notes.getByText('No saved decision notes.')).toBeVisible();
        await expect(notes.getByText(thesis)).toHaveCount(0);
        await expect(notes.getByText(outcome)).toHaveCount(0);
        await notes.getByRole('button', { name: 'Close decision notes' }).click();
        const undo = await act(page, 'Undo timeframe change', 'workspace.undo');
        expect(undo.result.outcome).toMatchObject({ state: 'committed', result: { revision: 5, timeframe: '1m' } });
      });
    });

    await inService(port, profile, 'ubuntu-workspace', async server => {
      await inFreshBrowser(browser, server, async (page, initial) => {
        expect(state(initial)).toMatchObject({ revision: 5, timeframe: '1m', undo: null });
        await control(page, 'Decision notes').click();
        const notes = page.getByRole('dialog', { name: 'Decision notes' });
        await expect(notes.getByText('No saved decision notes.')).toBeVisible();
      });
    });
  } finally {
    await Promise.all([profile.close(), other.close()]);
  }
});

test('decision-note backup restore previews known deletions and independent copies before apply', async ({ browser }) => {
  const profile = await makeProfile();
  const independent = await makeProfile(profile.id);
  const wrongProfile = await makeProfile();
  const port = await freePort();
  const deleted = { date: '2026-01-03', thesis: 'Synthetic original later deleted', choice: 'Wait',
    outcomes: [{ date: '2026-02-03', body: 'Synthetic outcome later deleted' }] };
  const retained = { date: '2026-01-08', thesis: 'Synthetic retained original', choice: 'Revisit',
    outcomes: [{ date: '2026-02-08', body: 'Synthetic first retained outcome' },
      { date: '2026-02-09', body: 'Synthetic second retained outcome' }] };
  const current = { date: '2026-01-21', thesis: 'Synthetic note saved after backup', choice: 'Observe', outcomes: [] };
  const warning = /An independent older backup may bring back previously deleted decision notes/;
  let untouched;
  let withMarker;
  try {
    await inService(port, profile, 'ubuntu-workspace', async server => {
      await inFreshBrowser(browser, server, async page => {
        await act(page, '5m', 'chart.setTimeframe');
        await control(page, 'Decision notes').click();
        const notes = page.getByRole('dialog', { name: 'Decision notes' });
        await saveBackupTestNote(notes, deleted);
        await saveBackupTestNote(notes, retained);
        untouched = await downloadBackupFile(page, notes);
        const archive = JSON.parse(untouched.toString('utf-8'));
        expect(Object.keys(archive).sort()).toEqual([
          'captured_workspace_revision', 'deletions', 'format', 'notes', 'profile_binding', 'version',
        ]);
        expect(archive).toMatchObject({ format: 'scopehub.decision-notes', version: 1, deletions: [] });
        expect(archive.notes).toHaveLength(2);

        await notes.getByRole('button', { name: new RegExp(deleted.date) }).click();
        await notes.getByRole('button', { name: 'Review deletion' }).click();
        await notes.getByRole('button', { name: 'Delete decision note' }).click();
        await expect(notes.getByText('Decision note deleted.')).toBeVisible();
        await saveBackupTestNote(notes, current);

        const preview = await chooseBackup(notes, untouched);
        await expect(preview.locator('dd').nth(2)).toHaveText('0');
        await expect(preview.locator('dd').nth(3)).toHaveText('1');
        await expect(preview.getByText(warning)).toHaveCount(0);
        await expect(notes.getByText(deleted.thesis)).toHaveCount(0);
        await preview.getByRole('button', { name: 'Cancel restore' }).click();
        await expect(notes.getByText('Restore cancelled.')).toBeVisible();
        await expect(notes.getByRole('button', { name: new RegExp(current.date) })).toBeVisible();

        await (await chooseBackup(notes, untouched)).getByRole('button', { name: 'Apply restore' }).click();
        await expect(notes.getByText('Decision-note backup restored.')).toBeVisible();
        await expect(notes.getByRole('button', { name: new RegExp(deleted.date) })).toHaveCount(0);
        await expect(notes.getByRole('button', { name: new RegExp(current.date) })).toBeVisible();
        await assertBackupTestNote(notes, retained);
        await notes.getByRole('button', { name: 'All decision notes' }).click();
        withMarker = await downloadBackupFile(page, notes);
        const afterDeletion = JSON.parse(withMarker.toString('utf-8'));
        expect(afterDeletion.deletions).toEqual([
          { note_id: archive.notes.find(note => note.original.decided_on === deleted.date).original.note_id,
            deleted_at_ms: expect.any(Number) },
        ]);
        expect(withMarker.includes(deleted.thesis)).toBe(false);
        expect(withMarker.includes(deleted.outcomes[0].body)).toBe(false);
      });
    });

    await inService(port, profile, 'ubuntu-workspace', async server => {
      const writer = await writerPid(server);
      await inFreshBrowser(browser, server, async (page, initial) => {
        expect(state(initial)).toMatchObject({ timeframe: '5m', undo: { committed_workspace_revision: 1 } });
        await control(page, 'Decision notes').click();
        const notes = page.getByRole('dialog', { name: 'Decision notes' });
        await expect(notes.getByRole('button', { name: new RegExp(deleted.date) })).toHaveCount(0);
        await assertBackupTestNote(notes, retained);
        await writerPid(server, 'kill', writer);
        await expect(notes.getByText(retained.thesis)).toHaveCount(0);
        await expect(notes.getByText(retained.outcomes[0].body)).toHaveCount(0);
      });
    });

    await inService(port, independent, 'ubuntu-workspace', async server => {
      await inFreshBrowser(browser, server, async page => {
        await control(page, 'Decision notes').click();
        const notes = page.getByRole('dialog', { name: 'Decision notes' });
        const preview = await chooseBackup(notes, untouched);
        await expect(preview.getByText(warning)).toBeVisible();
        await expect(notes.getByText(deleted.thesis)).toHaveCount(0);
        await preview.getByRole('button', { name: 'Cancel restore' }).click();
        await expect(notes.getByText('No saved decision notes.')).toBeVisible();
        const approval = await chooseBackup(notes, untouched);
        await expect(approval.getByText(warning)).toBeVisible();
        await approval.getByRole('button', { name: 'Apply restore' }).click();
        await expect(notes.getByText('Decision-note backup restored.')).toBeVisible();
        await assertBackupTestNote(notes, deleted);
        await notes.getByRole('button', { name: 'All decision notes' }).click();
        await inFreshBrowser(browser, server, async observer => {
          await control(observer, 'Decision notes').click();
          const observedNotes = observer.getByRole('dialog', { name: 'Decision notes' });
          await assertBackupTestNote(observedNotes, deleted);

          const importMarker = await chooseBackup(notes, withMarker);
          await expect(importMarker.locator('dd').nth(2)).toHaveText('1');
          await expect(importMarker.locator('dd').nth(4)).toHaveText('1');
          await importMarker.getByRole('button', { name: 'Apply restore' }).click();
          await expect(notes.getByRole('button', { name: new RegExp(deleted.date) })).toHaveCount(0);
          await expect(observedNotes.getByText(deleted.thesis)).toHaveCount(0);
          await expect(observedNotes.getByText(deleted.outcomes[0].body)).toHaveCount(0);
          await expect(observedNotes.getByRole('button', { name: new RegExp(retained.date) })).toBeVisible();
        });
        const older = await chooseBackup(notes, untouched);
        await expect(older.locator('dd').nth(3)).toHaveText('1');
        await expect(older.getByText(warning)).toHaveCount(0);
        await older.getByRole('button', { name: 'Cancel restore' }).click();
        await assertBackupTestNote(notes, retained);
      });
    });

    await inService(port, wrongProfile, 'ubuntu-workspace', async server => {
      await inFreshBrowser(browser, server, async page => {
        await control(page, 'Decision notes').click();
        const notes = page.getByRole('dialog', { name: 'Decision notes' });
        const rejected = page.waitForResponse(mutationResponse('decisionNote.previewRestore'));
        await selectBackupFile(notes, untouched);
        expect((await (await rejected).json()).result.outcome).toMatchObject({ state: 'failed', error: { code: 'PERMISSION_DENIED' } });
        await expect(notes.getByText('This backup or action is unavailable for the current local profile.')).toBeVisible();
        await expect(notes.getByRole('region', { name: 'Restore preview' })).toHaveCount(0);
        await expect(notes.getByText('No saved decision notes.')).toBeVisible();
      });
    });
  } finally {
    await Promise.all([profile.close(), independent.close(), wrongProfile.close()]);
  }
});

test('decision-note draft keeps its text and date across peer timeframe changes', async ({ browser }) => {
  const profile = await makeProfile();
  const fixture = { date: '2026-02-03', thesis: 'Synthetic decision for a pending outcome', choice: 'Wait', outcomes: [] };
  const draft = 'Synthetic unsubmitted reflection';
  const date = '2026-04-09';
  try {
    await inService(await freePort(), profile, 'ubuntu-workspace', async server => {
      await inFreshBrowser(browser, server, async page => {
        await control(page, 'Decision notes').click();
        await saveBackupTestNote(page.getByRole('dialog', { name: 'Decision notes' }), fixture);
      });
      await inFreshBrowser(browser, server, async observer => {
        await control(observer, 'Decision notes').click();
        const notes = observer.locator('dialog.decision-dialog');
        await assertBackupTestNote(notes, fixture);
        await notes.getByRole('button', { name: 'Add later outcome' }).click();
        const outcome = notes.locator('textarea[name="body"]');
        const outcomeDate = notes.locator('input[name="date"]');
        await outcome.fill(draft);
        await outcomeDate.fill(date);

        await inFreshBrowser(browser, server, async peer => {
          const reloaded = observer.waitForResponse(mutationResponse('decisionNote.read'), { timeout: 10_000 });
          expect((await act(peer, '5m', 'chart.setTimeframe')).result.outcome.state).toBe('committed');
          await observer.bringToFront();
          await reloaded;
          await expect(outcome).toHaveValue(draft);
          await expect(outcomeDate).toHaveValue(date);

          await notes.getByRole('button', { name: 'Review later outcome' }).click();
          const approval = notes.getByRole('button', { name: 'Save later outcome' });
          await expect(approval).toBeVisible();
          await peer.bringToFront();
          expect((await act(peer, '1d', 'chart.setTimeframe')).result.outcome.state).toBe('committed');
          await observer.bringToFront();
          await expect(approval).toHaveCount(0);
          await expect(outcome).toHaveValue(draft);
          await expect(outcomeDate).toHaveValue(date);
        });

        await notes.getByRole('button', { name: 'Review later outcome' }).click();
        await notes.getByRole('button', { name: 'Save later outcome' }).click();
        await expect(notes.getByRole('region', { name: 'Later outcomes' }).getByText(draft)).toBeVisible();
        await expect(notes.getByRole('region', { name: 'Later outcomes' }).getByText(date)).toBeVisible();
        await notes.getByRole('button', { name: 'Add later outcome' }).click();
        const deletedDraft = 'Synthetic draft to remove on peer deletion';
        await outcome.fill(deletedDraft);
        await inFreshBrowser(browser, server, async peer => {
          await control(peer, 'Decision notes').click();
          await deleteBackupTestNote(peer.getByRole('dialog', { name: 'Decision notes' }), fixture.date);
          await observer.bringToFront();
          await expect(outcome).toHaveCount(0);
          await expect(notes.getByText(fixture.thesis)).toHaveCount(0);
          await expect(notes.getByText(draft)).toHaveCount(0);
          await expect(notes.getByText('No saved decision notes.')).toBeVisible();
        });
      });
    });
  } finally {
    await profile.close();
  }
});

test('decision-note list removes peer deletions after focus and a rejected detail read', async ({ browser }) => {
  const profile = await makeProfile();
  const first = { date: '2026-02-03', thesis: 'Synthetic note removed while a list stays open', choice: 'Wait', outcomes: [] };
  const second = { date: '2026-02-04', thesis: 'Synthetic note removed before its detail read', choice: 'Wait', outcomes: [] };
  try {
    await inService(await freePort(), profile, 'ubuntu-workspace', async server => {
      await inFreshBrowser(browser, server, async page => {
        await control(page, 'Decision notes').click();
        const notes = page.getByRole('dialog', { name: 'Decision notes' });
        await saveBackupTestNote(notes, first);
        await saveBackupTestNote(notes, second);
      });
      await inFreshBrowser(browser, server, async observer => {
        await control(observer, 'Decision notes').click();
        const notes = observer.getByRole('dialog', { name: 'Decision notes' });
        const firstRow = notes.getByRole('button', { name: new RegExp(first.date) });
        const secondRow = notes.getByRole('button', { name: new RegExp(second.date) });
        await expect(firstRow).toBeVisible();
        await expect(secondRow).toBeVisible();
        await inFreshBrowser(browser, server, async peer => {
          await control(peer, 'Decision notes').click();
          const peerNotes = peer.getByRole('dialog', { name: 'Decision notes' });
          await deleteBackupTestNote(peerNotes, first.date);
          await observer.evaluate(() => window.dispatchEvent(new Event('focus')));
          await expect(firstRow).toHaveCount(0);
          await expect(secondRow).toBeVisible();

          const intercepted = Promise.withResolvers();
          const resume = Promise.withResolvers();
          await observer.route(server.url + endpoint, async route => {
            const intent = route.request().postDataJSON()?.intent;
            if (intent?.operation === 'decisionNote.read' && intent.arguments.note_id !== null) {
              intercepted.resolve();
              await resume.promise;
            }
            await route.continue();
          });
          await secondRow.click();
          try {
            await intercepted.promise;
            await deleteBackupTestNote(peerNotes, second.date);
          } finally {
            resume.resolve();
          }
          await expect(notes.getByText('This decision note is unavailable.')).toBeVisible();
          await expect(secondRow).toHaveCount(0);
          await expect(notes.getByText('No saved decision notes.')).toBeVisible();
        });
      });
    });
  } finally {
    await profile.close();
  }
});

test('the opted-in route binds identity, limits input, and drains cancellation without replaying after EOF', async () => {
  const profile = await makeProfile();
  const port = await freePort();
  try {
    await inService(port, profile, 'ubuntu-workspace', async server => {
      const writer = await writerPid(server);
      const baseline = await post(server, snapshot);
      expect(state(await baseline.json())).toEqual({ revision: 0, timeframe: '1m', undo: null });
      const foreign = await post(server, snapshot, { Origin: 'http://localhost' });
      expect(foreign.status).toBe(403);
      const oversized = await post(server, { type: 'snapshot', padding: 'x'.repeat(16 * 1024) });
      expect(oversized.status).toBe(413);
      const forged = setTimeframe(0, '5m');
      Object.assign(forged.scope, { principal_id: 'forged', client_id: 'forged', profile_root: profile.root, grant: 'all' });
      expect(await (await post(server, forged)).json()).toMatchObject({ ok: false, error: { code: 'INVALID_ARGUMENT' } });
      forged.scope.workspace_id = 'other-workspace';
      expect((await post(server, forged)).status).toBe(403);
      const file = join(profile.root, 'workspace.sqlite3').replaceAll('\\', '/');
      expect((await stat(file)).isFile()).toBe(true);
      const sqlite = await fetch(server.url + '/@fs/' + file.replace(/^\//, ''));
      expect([403, 404]).toContain(sqlite.status);

      await writerPid(server, 'suspend', writer);
      const abort = new AbortController();
      const cancelled = post(server, setTimeframe(0, '5m'), {}, abort.signal).catch(error => error);
      try {
        await expect.poll(async () => (await post(server, admissionProbe)).status).toBe(429);
        abort.abort();
        expect((await cancelled).name).toBe('AbortError');
        // The browser stopped waiting. The admitted line still owns the producer slot.
        expect((await post(server, admissionProbe)).status).toBe(429);
      } finally {
        if (alive(writer)) await writerPid(server, 'resume', writer);
      }
      await expect.poll(async () => {
        const reply = await post(server, snapshot);
        return reply.ok ? state(await reply.json())?.revision : undefined;
      }).toBe(1);
      expect(await writerPid(server)).toBe(writer);

      await writerPid(server, 'suspend', writer);
      const inFlight = post(server, setTimeframe(1, '1d'));
      try {
        await expect.poll(async () => (await post(server, admissionProbe)).status).toBe(429);
      } finally {
        if (alive(writer)) await writerPid(server, 'kill', writer);
      }
      const disconnected = await inFlight;
      expect(disconnected.status).toBe(503);
      expect(await disconnected.json()).toMatchObject({ ok: false, error: { code: 'WORKER_EXITED' } });
      expect((await post(server, snapshot)).status).toBe(503);
    });
    await inService(port, profile, 'ubuntu-workspace', async server => {
      const restored = state(await (await post(server, snapshot)).json());
      expect(restored).toMatchObject({ revision: 1, timeframe: '5m' });
    });
  } finally {
    await profile.close();
  }
});

test('the private launcher handoff is required and a previous launch cannot authorize a request', async ({ browser }) => {
  const profile = await makeProfile();
  const port = await freePort();
  let previous;
  let handoff;
  try {
    await inService(port, profile, 'ubuntu-workspace', async server => {
      previous = server.capability;
      handoff = server.handoff;
      await assertPrivateHandoff(handoff);

      for (const request of [snapshot, setTimeframe(0, '5m')]) {
        const denied = await post({ url: server.url }, request);
        expect(denied.status).toBe(403);
        expect(await denied.json()).toEqual({ ok: false, error: { code: 'PERMISSION_DENIED', retryable: false } });
      }
      const visible = await fetch(server.url + '/workspace-access-' + port + '.html');
      expect(!(await visible.text()).includes(previous)).toBe(true);
      const allowed = await post(server, snapshot);
      const payload = await allowed.text();
      expect(!payload.includes(previous)).toBe(true);
      expect(state(JSON.parse(payload))).toEqual({ revision: 0, timeframe: '1m', undo: null });
    });
    await expect(stat(handoff)).rejects.toMatchObject({ code: 'ENOENT' });

    await inService(port, profile, 'ubuntu-workspace', async server => {
      expect(server.capability !== previous).toBe(true);
      const expired = await post(server, snapshot, { Authorization: 'Bearer ' + previous });
      expect(expired.status).toBe(403);

      const anonymous = await browser.newContext();
      try {
        const page = await anonymous.newPage();
        const requests = observeOperations(page);
        await page.goto(server.url);
        await expect(control(page, 'Open offline sample')).toBeVisible();
        await expect(control(page, 'Undo timeframe change')).toHaveCount(0);
        await expect(control(page, 'Decision notes')).toHaveCount(0);
        await control(page, '5m').click();
        expect(requests).toEqual([]);
      } finally { await anonymous.close(); }

      await inFreshBrowser(browser, server, async (page, initial) => {
        expect(state(initial)).toEqual({ revision: 0, timeframe: '1m', undo: null });
        await expect(control(page, '1m')).toHaveAttribute('aria-pressed', 'true');
      });
    });

    if (process.getuid) {
      // This profile contains only test state. Reject it before issuing a
      // credential when the host supplies it without owner-only permissions.
      await chmod(profile.root, 0o755);
      await inService(port, profile, 'ubuntu-workspace', async server => {
        await expect.poll(server.handoffRejected).toBe(true);
        await expect(stat(server.handoff)).rejects.toMatchObject({ code: 'ENOENT' });
        expect((await post(server, snapshot)).status).toBe(403);
      }, { expectHandoff: false });
    }
  } finally {
    await profile.close();
  }
});

test('terminal SIGINT and SIGTERM remove the handoff and reap the writer before relaunch', async () => {
  test.skip(process.platform === 'win32', 'The owned CLI uses POSIX signal delivery.');
  const profile = await makeProfile();
  const port = await freePort();
  let handoff;
  let previousWriter;
  let previousCapability;
  try {
    await inService(port, profile, 'ubuntu-workspace', async server => {
      handoff = server.handoff;
      previousWriter = await writerPid(server);
      previousCapability = server.capability;
      expect(state(await (await post(server, snapshot)).json())).toEqual({ revision: 0, timeframe: '1m', undo: null });
    }, { terminalSignal: 'SIGINT' });
    await expect(stat(handoff)).rejects.toMatchObject({ code: 'ENOENT' });
    await expect.poll(() => alive(previousWriter)).toBe(false);

    let nextWriter;
    await inService(port, profile, 'ubuntu-workspace', async server => {
      nextWriter = await writerPid(server);
      expect(nextWriter).not.toBe(previousWriter);
      expect(server.capability !== previousCapability).toBe(true);
      expect((await post(server, snapshot, { Authorization: 'Bearer ' + previousCapability })).status).toBe(403);
      expect(state(await (await post(server, snapshot)).json())).toEqual({ revision: 0, timeframe: '1m', undo: null });
    }, { terminalSignal: 'SIGTERM' });
    await expect(stat(handoff)).rejects.toMatchObject({ code: 'ENOENT' });
    await expect.poll(() => alive(nextWriter)).toBe(false);
  } finally {
    await profile.close();
  }
});

test('two live writers read current undo and a stale browser reload recovers from Rust', async ({ browser }) => {
  const profile = await makeProfile();
  try {
    await inService(await freePort(), profile, 'ubuntu-workspace', async first => {
      await inService(await freePort(), profile, 'ubuntu-workspace', async second => {
        expect(await writerPid(first)).not.toBe(await writerPid(second));
        const crossLaunch = await post(second, snapshot, { Authorization: 'Bearer ' + first.capability });
        expect(crossLaunch.status).toBe(403);
        await inFreshBrowser(browser, first, async (page, initial) => {
          expect(state(initial)).toEqual({ revision: 0, timeframe: '1m', undo: null });
          const changed = (await (await post(second, setTimeframe(0, '5m'))).json()).result.outcome;
          expect(changed).toMatchObject({ state: 'committed', result: { revision: 1, timeframe: '5m' } });
          const target = { operation_id: changed.receipt.operation_id, committed_workspace_revision: 1 };
          expect(state(await (await post(first, snapshot)).json())).toEqual({ revision: 1, timeframe: '5m', undo: target });

          const rejected = await act(page, '1d', 'chart.setTimeframe');
          expect(rejected.result.outcome).toMatchObject({ state: 'failed', error: { code: 'STALE_CONTEXT' } });
          await expect(page.getByRole('alert')).toHaveText('The workspace changed. Reload its current state.');
          await expect(control(page, '1m')).toHaveAttribute('aria-pressed', 'true');
          await control(page, 'Reload workspace state').click();
          await expect(control(page, '5m')).toHaveAttribute('aria-pressed', 'true');
          const undo = page.waitForResponse(mutationResponse('workspace.undo'));
          await control(page, 'Undo timeframe change').click();
          const accepted = await undo;
          expect(accepted.request().postDataJSON().intent.arguments).toEqual({ operation_id: target.operation_id });
          expect((await accepted.json()).result.outcome).toMatchObject({
            state: 'committed', result: { revision: 2, timeframe: '1m' },
          });
          expect(state(await (await post(second, snapshot)).json())).toEqual({ revision: 2, timeframe: '1m', undo: null });
        });
      });
    });
  } finally {
    await profile.close();
  }
});

test('ordinary and Ubuntu history startup do not open a writer or dispatch data operations', async ({ browser }) => {
  const profile = await makeProfile();
  try {
    for (const mode of ['development', 'ubuntu-preview']) {
      await inService(await freePort(), profile, mode, async server => {
        const context = await browser.newContext();
        try {
          const page = await context.newPage();
          const requests = observeOperations(page);
          await page.goto(server.url);
          await expect(control(page, 'Open offline sample')).toBeVisible();
          await expect(control(page, '1m')).toHaveAttribute('aria-pressed', 'true');
          await expect(control(page, 'Undo timeframe change')).toHaveCount(0);
          await control(page, '5m').click();
          await expect(control(page, '5m')).toHaveAttribute('aria-pressed', 'true');
          expect(requests).toEqual([]);
        } finally {
          await context.close();
        }
        if (mode === 'ubuntu-preview') {
          const denied = await fetch(server.url + '/__scopehub/operation', {
            method: 'POST', headers: { Origin: server.url, 'Content-Type': 'application/json' },
            body: JSON.stringify({ operation: 'chart.setTimeframe', payload: setTimeframe(0, '5m') }),
          });
          expect(denied.status).toBe(403);
          expect(await denied.json()).toMatchObject({ ok: false, error: { code: 'PERMISSION_DENIED' } });
        }
        expect((await post(server, setTimeframe(0, '5m'))).status).toBe(404);
        await expect(stat(join(profile.root, 'workspace.sqlite3'))).rejects.toMatchObject({ code: 'ENOENT' });
      });
    }
  } finally {
    await profile.close();
  }
});

async function makeProfile(id = 'synthetic-' + randomUUID()) {
  const directory = await mkdtemp(join(tmpdir(), 'scopehub-ubuntu-test-'));
  const profile = join(directory, 'profile');
  const close = () => rm(directory, { recursive: true, force: true });
  try {
    await exec(workspaceBridge, ['--prepare-profile-root', profile], { timeout: 12_000 });
    return { root: profile, id, close };
  } catch (error) {
    await close();
    throw error;
  }
}

async function assertPrivateHandoff(handoff) {
  if (process.platform !== 'win32') {
    const metadata = await stat(handoff);
    expect(metadata.mode & 0o777).toBe(0o600);
    expect(metadata.uid).toBe(process.getuid());
    return;
  }
  // Verify the resulting file's inherited ACL on Windows without reading or
  // printing its contents or revealing any local identity in the test log.
  const check = `
    $ErrorActionPreference = 'Stop'
    try {
      function Read-Descriptor($path) {
        $acl = Get-Acl -LiteralPath $path
        return [Security.AccessControl.RawSecurityDescriptor]::new($acl.GetSecurityDescriptorBinaryForm(), 0)
      }
      $path = $env:SCOPEHUB_TEST_HANDOFF
      if ([IO.File]::GetAttributes($path) -band [IO.FileAttributes]'Directory, ReparsePoint') { exit 1 }
      $user = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
      $allowed = @($user, 'S-1-5-18', 'S-1-5-32-544')
      $directory = Read-Descriptor ([IO.Path]::GetDirectoryName($path))
      if ($directory.Owner.Value -ne $user) { exit 1 }
      $descriptor = Read-Descriptor $path
      if ($null -eq $descriptor.DiscretionaryAcl -or $descriptor.Owner.Value -notin $allowed) { exit 1 }
      $required = [int][Security.AccessControl.FileSystemRights]'Read, Write, Delete'
      $userInherited = $false
      foreach ($ace in $descriptor.DiscretionaryAcl) {
        if ($ace -isnot [Security.AccessControl.CommonAce] -or $ace.IsCallback) { exit 1 }
        if ($ace.AceQualifier -ne [Security.AccessControl.AceQualifier]::AccessAllowed) { continue }
        if ($ace.SecurityIdentifier.Value -notin $allowed) { exit 1 }
        if ($ace.SecurityIdentifier.Value -eq $user -and $ace.IsInherited -and
            !($ace.AceFlags -band [Security.AccessControl.AceFlags]::InheritOnly) -and
            ($ace.AccessMask -band $required) -eq $required) { $userInherited = $true }
      }
      if (!$userInherited) { exit 1 }
      $stream = [IO.File]::Open($path, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite)
      $stream.Dispose()
      exit 0
    } catch { exit 1 }
  `;
  const powershell = join(process.env.SystemRoot, 'System32/WindowsPowerShell/v1.0/powershell.exe');
  await exec(powershell, ['-NoProfile', '-NonInteractive', '-NoLogo', '-Command', check], {
    timeout: 10_000, windowsHide: true, env: { ...process.env, SCOPEHUB_TEST_HANDOFF: handoff },
  });
}

async function freePort() {
  const server = createServer();
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const port = server.address().port;
  await new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
  return port;
}

async function inService(port, profile, mode, use, { expectHandoff = true, terminalSignal } = {}) {
  const url = 'http://127.0.0.1:' + port;
  const args = terminalSignal
    ? [viteCli, '--mode', mode, '--host', '127.0.0.1', '--port', String(port), '--strictPort']
    : ['--input-type=module', '--eval', service, vite, mode, String(port)];
  const child = spawn(process.execPath, args, {
    cwd: ui,
    env: {
      ...process.env,
      SCOPEHUB_WORKSPACE_PROFILE_ROOT: profile.root,
      SCOPEHUB_WORKSPACE_PROFILE_ID: profile.id,
      SCOPEHUB_WORKSPACE_ID: workspaceId,
    },
    stdio: terminalSignal ? ['pipe', 'pipe', 'pipe'] : ['ignore', 'pipe', 'pipe', 'ipc'],
  });
  const closed = once(child, 'close');
  let output = '';
  for (const stream of [child.stdout, child.stderr]) {
    stream.setEncoding('utf8');
    stream.on('data', chunk => { output = (output + chunk).slice(-6000); });
  }
  const server = {
    url, pid: child.pid,
    handoff: join(profile.root, 'workspace-access-' + port + '.html'),
    handoffRejected: () => output.includes('The private workspace handoff could not be issued.'),
  };
  try {
    const deadline = Date.now() + 10_000;
    for (;;) {
      if (child.exitCode !== null || child.signalCode !== null || Date.now() > deadline) {
        throw new Error(`Vite failed to start in ${mode}: ${output}`);
      }
      const response = await fetch(url, { signal: AbortSignal.timeout(500) }).catch(() => null);
      if (response?.ok) {
        await response.body.cancel();
        break;
      }
      await response?.body?.cancel();
      await delay(30);
    }
    if (mode === 'ubuntu-workspace' && expectHandoff) {
      await expect.poll(async () => {
        const content = await readFile(server.handoff, 'utf8').catch(error => {
          if (error.code === 'ENOENT') return '';
          throw error;
        });
        const link = /href="(http:\/\/127\.0\.0\.1:\d+\/#workspace=([a-f0-9]{64}))"/.exec(content);
        if (!link || new URL(link[1]).origin !== url) return false;
        const capability = link[2];
        server.capability = capability;
        return true;
      }, { message: 'The launcher issued a private access document', timeout: 12_000 }).toBe(true);
    }
    await use(server);
  } finally {
    if (terminalSignal) child.kill(terminalSignal);
    else if (child.connected) child.send('close', () => {});
    const grace = setTimeout(() => child.kill('SIGKILL'), 7_000);
    try {
      const [, signal] = await closed;
      expect(signal).not.toBe('SIGKILL');
      if (server.capability) expect(!output.includes(server.capability)).toBe(true);
    } finally {
      clearTimeout(grace);
    }
  }
}

async function writerPid(server, action, observedPid) {
  const args = ['-I', join(root, 'tests/ubuntu-preview/workspace_test_writer.py'), String(process.pid), String(server.pid)];
  if (action) args.push(action, String(observedPid));
  const { stdout } = await exec(python, args, { timeout: 5_000 });
  return Number(stdout.trim());
}

function alive(pid) {
  try { process.kill(pid, 0); return true; }
  catch (error) {
    if (error.code === 'ESRCH') return false;
    throw error;
  }
}

function post(server, body, headers = {}, signal = AbortSignal.timeout(10_000)) {
  return fetch(server.url + endpoint, {
    method: 'POST', headers: {
      Origin: server.url, 'Content-Type': 'application/json',
      ...(server.capability ? { Authorization: 'Bearer ' + server.capability } : {}), ...headers,
    },
    body: JSON.stringify(body), signal,
  });
}

function state(frame) {
  if (frame.ok !== true) return undefined;
  const { workspace, undo } = frame.result;
  expect(workspace).toMatchObject({ id: workspaceId, instrument_id: null, dataset: null, layers: [] });
  expect(frame.result.selection_revision).toBe(0);
  return { revision: workspace.revision, timeframe: workspace.timeframe, undo };
}

function observeOperations(page) {
  const requests = [];
  page.on('request', request => {
    const path = new URL(request.url()).pathname;
    if (path.startsWith('/__scopehub/')) requests.push(path);
  });
  return requests;
}

async function inFreshBrowser(browser, server, use) {
  const context = await browser.newContext();
  try {
    // Read the real launcher handoff but set its fragment locally before page
    // code runs. No capability is put in Playwright navigation URLs or traces.
    await context.addInitScript(capability => {
      window.history.replaceState(null, '', '#workspace=' + capability);
    }, server.capability);
    const page = await context.newPage();
    const requests = observeOperations(page);
    let capabilityInRequestUrl = false;
    page.on('request', request => { capabilityInRequestUrl ||= request.url().includes(server.capability); });
    const initial = page.waitForResponse(response =>
      new URL(response.url()).pathname === endpoint && response.request().postDataJSON()?.type === 'snapshot');
    await page.goto(server.url);
    await expect(control(page, 'Open offline sample')).toBeVisible();
    await expect(page.getByText('No data source')).toBeVisible();
    expect(await page.evaluate(() => window.location.hash === '')).toBe(true);
    expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
    await use(page, await (await initial).json());
    expect(requests.every(path => path === endpoint)).toBe(true);
    expect(capabilityInRequestUrl).toBe(false);
  } finally {
    await context.close();
  }
}

function mutationResponse(operation) {
  return response => new URL(response.url()).pathname === endpoint
    && response.request().postDataJSON()?.intent?.operation === operation;
}

async function saveBackupTestNote(notes, fixture) {
  await notes.getByRole('button', { name: 'New decision note' }).click();
  await notes.getByLabel('Decision date').fill(fixture.date);
  await notes.getByLabel('Thesis').fill(fixture.thesis);
  await notes.getByLabel('Choice').fill(fixture.choice);
  await notes.getByRole('button', { name: 'Review decision note' }).click();
  await notes.getByRole('button', { name: 'Save decision note' }).click();
  for (const outcome of fixture.outcomes) {
    await notes.getByRole('button', { name: 'Add later outcome' }).click();
    await notes.getByLabel('Outcome date').fill(outcome.date);
    await notes.getByLabel('Outcome', { exact: true }).fill(outcome.body);
    await notes.getByRole('button', { name: 'Review later outcome' }).click();
    await notes.getByRole('button', { name: 'Save later outcome' }).click();
  }
  await notes.getByRole('button', { name: 'All decision notes' }).click();
}

async function deleteBackupTestNote(notes, date) {
  await notes.getByRole('button', { name: new RegExp(date) }).click();
  await notes.getByRole('button', { name: 'Review deletion' }).click();
  await notes.getByRole('button', { name: 'Delete decision note' }).click();
  await expect(notes.getByText('Decision note deleted.')).toBeVisible();
}

async function assertBackupTestNote(notes, fixture) {
  await notes.getByRole('button', { name: new RegExp(fixture.date) }).click();
  const original = notes.getByRole('region', { name: 'Original decision' });
  await expect(original.getByText(fixture.date, { exact: true })).toBeVisible();
  await expect(original.getByText(fixture.thesis)).toBeVisible();
  await expect(original.getByText(fixture.choice)).toBeVisible();
  const outcomes = notes.getByRole('region', { name: 'Later outcomes' });
  for (const outcome of fixture.outcomes) {
    await expect(outcomes.getByText(outcome.date, { exact: true })).toBeVisible();
    await expect(outcomes.getByText(outcome.body)).toBeVisible();
  }
}

async function downloadBackupFile(page, notes) {
  const download = page.waitForEvent('download');
  await notes.getByRole('button', { name: 'Download backup' }).click();
  const file = await download;
  expect(file.suggestedFilename()).toMatch(/^scopehub-decision-notes-.*\.json$/);
  return readFile(await file.path());
}

async function selectBackupFile(notes, bytes) {
  const picker = notes.getByLabel('Choose backup file');
  await expect(picker).toBeEnabled();
  await picker.setInputFiles({ name: 'backup.json', mimeType: 'application/json', buffer: bytes });
}

async function chooseBackup(notes, bytes) {
  await selectBackupFile(notes, bytes);
  const preview = notes.getByRole('region', { name: 'Restore preview' });
  await expect(preview).toBeVisible();
  return preview;
}

async function dropApprovalReply(page, server, operation) {
  const endpointUrl = server.url + endpoint;
  let outcome;
  const handle = async route => {
    const payload = route.request().postDataJSON();
    if (payload?.intent?.operation !== operation || payload.confirmation?.decision !== 'approve') {
      await route.continue();
      return;
    }
    const response = await route.fetch();
    outcome = (await response.json()).result.outcome;
    await response.dispose();
    await route.abort();
  };
  await page.route(endpointUrl, handle);
  return { outcome: () => outcome, restore: () => page.unroute(endpointUrl, handle) };
}

async function act(page, name, operation) {
  const operationResponse = page.waitForResponse(mutationResponse(operation));
  await control(page, name).click();
  const accepted = await operationResponse;
  const json = await accepted.json();
  expect(json.result.request_id).toBe(accepted.request().postDataJSON().request_id);
  return json;
}

function setTimeframe(revision, timeframe) {
  return {
    protocol_version: '1.1', type: 'request', message_id: randomUUID(), timestamp_ms: Date.now(), request_id: randomUUID(),
    task_id: null,
    scope: {
      workspace_id: workspaceId, target: { kind: 'chart', object_id: 'main-chart' }, idempotency_key: randomUUID(),
      expected_workspace_revision: revision, expected_selection_revision: 0,
    },
    intent: { operation: 'chart.setTimeframe', arguments: { timeframe } },
  };
}
