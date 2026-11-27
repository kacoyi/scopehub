import assert from 'node:assert/strict';
import childProcess from 'node:child_process';
import { EventEmitter } from 'node:events';
import { access, mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { syncBuiltinESMExports } from 'node:module';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { PassThrough, Writable } from 'node:stream';
import test from 'node:test';
import timers from 'node:timers/promises';
import { chromium, expect } from '@playwright/test';

const port = 42000;
const desktopPid = 42;
const webviewPid = 43;
const debuggerPath = '/devtools/browser/12345678-9abc';
const websocket = `ws://127.0.0.1:${port}${debuggerPath}`;
const storageRefusal = { error: {
  code: 'INTERNAL_ERROR', retryable: false, details: {}, message: 'The operation could not be completed.',
} };
const missingFolder = { error: {
  code: 'SNAPSHOT_UNAVAILABLE', retryable: false, details: {}, message: 'The requested market snapshot is unavailable.',
} };

function statusReply(args) {
  if (args.operation === null && args.payload.type === 'frozen_chart_status') {
    return { result: { available: args.payload.revision_id === 'synthetic-csv' } };
  }
  assert.equal(args.operation, 'market.readLocalFolderRevision');
  assert.equal(args.payload.include_details, false);
  assert.notEqual(args.payload.revision_id, 'synthetic-folder-revision');
  return missingFolder;
}

function bridgeReply({ command, args }) {
  switch (command === 'execute_operation' ? args.operation : command) {
    case 'choose_local_csv_folder': return { folder_id: 'synthetic-folder', files: [{ file_id: 'synthetic-file' }] };
    case 'market.previewLocalImport': return {};
    case 'market.saveLocalImportMapping': return { record: { review_revision: 'synthetic-review' } };
    case 'market.publishLocalImport': return { revision: { revision_id: 'synthetic-csv', bar_count: 2 } };
    case 'market.previewLocalFolderRefresh': return { review_id: 'synthetic-folder-review' };
    case 'market.prepareLocalFolderPublication': return { candidate_sha256: 'synthetic-candidate' };
    case 'market.publishLocalFolderRefresh': return { revision: { revision_id: 'synthetic-folder-revision', bar_count: 2 } };
    default: throw new Error('Unexpected synthetic bridge request.');
  }
}

async function replay(t, { endpointAt = 59000, ownerAt = Infinity, foreign = false, processDelay = 0,
  ignoreProcessTimeout = false, refusePreparation = false, refuseDesktopStatus = false } = {}) {
  const scratch = await mkdtemp(join(tmpdir(), 'scopehub-launch-control-'));
  let now = 0;
  let profileTemp;
  let overlayProfile;
  let preparedProfile;
  let desktop;
  let desktopClosed = false;
  let browserClosed = false;
  let attaches = 0;
  let journeyAt;
  const ownershipBudgets = [];
  const observation = {};
  const setup = [];
  const seedRequests = [];
  const desktopRequests = [];
  const seedBridges = [];
  const { execFile: originalExecFile, spawn: originalSpawn } = childProcess;
  const target = join(scratch, 'published-revision-access', 'x86_64-pc-windows-msvc', 'debug');
  const page = {
    url: () => 'http://tauri.localhost/index.html', bringToFront() {}, waitForFunction() {},
    evaluate(_callback, args) {
      desktopRequests.push(args);
      setup.push('desktop status');
      return refuseDesktopStatus ? storageRefusal : statusReply(args);
    },
  };
  try {
    await mkdir(target, { recursive: true });
    await writeFile(join(target, 'scopehub-desktop.exe'), 'synthetic fixture executable');
    t.mock.method(performance, 'now', () => now);
    t.mock.method(timers, 'setTimeout', async duration => { now += duration; });
    // Keep the synthetic clock in control of Playwright polling too so the
    // same test can exercise the previous fixture's two separate waits.
    t.mock.method(expect, 'poll', (probe, { timeout }) => ({
      async toBe(wanted) {
        const deadline = now + timeout;
        while (now < deadline) {
          if (await probe() === wanted) return;
          now = Math.min(now + 250, deadline);
        }
        throw new Error('Synthetic polling deadline exceeded.');
      },
    }));
    t.mock.method(globalThis, 'fetch', async url => {
      assert.equal(url, `http://127.0.0.1:${port}/json/version`);
      return now < endpointAt ? new Response('', { status: 503 })
        : Response.json({ webSocketDebuggerUrl: websocket });
    });
    t.mock.method(chromium, 'connectOverCDP', async url => {
      assert.equal(url, websocket);
      attaches++;
      return {
        contexts: () => [{ pages: () => [page] }],
        isConnected: () => !browserClosed,
        close() { browserClosed = true; },
      };
    });
    childProcess.execFile = (file, args, options, callback) => {
      if (file === 'git') return originalExecFile(file, args, options, (error, stdout, stderr) => callback(error, { stdout, stderr }));
      if (file === 'cargo') return callback(null, { stdout: JSON.stringify({ target_directory: scratch }) });
      if (file.endsWith('workspace_bridge.exe')) {
        assert.equal(args[0], '--prepare-profile-root');
        preparedProfile = args[1];
        profileTemp = dirname(preparedProfile);
        assert.equal(preparedProfile, overlayProfile);
        setup.push('prepare');
        if (refusePreparation) {
          const error = new Error('Synthetic preparation refused.');
          error.code = 1;
          error.stderr = 'Workspace storage ACL check refused (prepare, local-path).';
          return callback(error);
        }
        void mkdir(preparedProfile).then(() => callback(null, { stdout: '' }), callback);
        return;
      }
      assert.equal(file, 'powershell.exe');
      if (args.at(-1).includes('CloseMainWindow')) {
        desktopClosed = true;
        desktop.exitCode = 0;
        desktop.emit('exit', 0, null);
        return callback(null, { stdout: '' });
      }
      assert.match(args.at(-1), /Get-NetTCPConnection/u);
      const diagnostic = observation.launch.failure_at_ms !== undefined;
      if (!diagnostic) {
        ownershipBudgets.push(options.timeout);
        now += ignoreProcessTimeout ? processDelay : Math.min(processDelay, options.timeout);
        if (!ignoreProcessTimeout && processDelay > options.timeout) {
          const error = new Error('Synthetic process inspection timed out.');
          error.code = 'ETIMEDOUT';
          return callback(error);
        }
      }
      const ownListener = !foreign && now >= ownerAt;
      const snapshot = {
        desktop_pid: desktopPid, window: 123,
        webview_children: [{ pid: webviewPid, version: '100.0.0.0' }],
        listeners: [{ address: '127.0.0.1', pid: ownListener ? webviewPid : 99 }],
        windows_build: '1000', windows_product_type: 1, windows_edition: 'Synthetic',
      };
      return callback(null, { stdout: JSON.stringify(snapshot) });
    };
    childProcess.spawn = (file, args, options) => {
      const child = new EventEmitter();
      if (file.endsWith('local_import_bridge.exe')) {
        assert.equal(args[0], preparedProfile);
        assert.equal(dirname(args[1]), profileTemp);
        const isPublisher = seedBridges.length === 0;
        const bridge = { closed: false };
        seedBridges.push(bridge);
        setup.push(isPublisher ? 'publish' : 'reopen seed');
        child.stdout = new PassThrough();
        child.stderr = new PassThrough();
        child.stdin = new Writable({
          write(line, _encoding, done) {
            const request = JSON.parse(line);
            if (!isPublisher) seedRequests.push(request.args);
            const reply = isPublisher ? { result: bridgeReply(request) } : statusReply(request.args);
            child.stdout.write(JSON.stringify(reply) + '\n');
            done();
          },
          final(done) {
            const finish = error => {
              bridge.closed = true;
              child.stdout.end();
              child.emit('close', error ? 1 : 0);
              done(error);
            };
            if (!isPublisher) return finish();
            const workspace = join(preparedProfile, 'workspace-dev');
            return mkdir(workspace)
              .then(() => writeFile(join(workspace, 'workspace.sqlite3'), ''))
              .then(() => finish(), finish);
          },
        });
      } else if (file.endsWith('scopehub-desktop.exe')) {
        setup.push('spawn desktop');
        assert.equal(options.cwd, profileTemp);
        assert.deepEqual(seedBridges.map(bridge => bridge.closed), [true, true]);
        desktop = child;
        Object.assign(child, {
          pid: desktopPid, exitCode: null, signalCode: null, stdout: new PassThrough(), stderr: new PassThrough(),
        });
        void (async () => {
          try {
            const webview = join(profileTemp, 'profile', 'EBWebView');
            await mkdir(webview, { recursive: true });
            if (Number.isFinite(endpointAt)) await writeFile(join(webview, 'DevToolsActivePort'), `${port}\n${debuggerPath}\n`);
            child.stdout.write('synthetic native stdout');
            child.stderr.write('synthetic native stderr');
            child.emit('spawn');
          } catch (error) {
            child.emit('error', error);
          }
        })();
      } else if (file === process.execPath) {
        const config = args[args.indexOf('--config') + 1];
        void readFile(config, 'utf8').then(contents => {
          overlayProfile = JSON.parse(contents).app.appDirectoriesOverride;
          assert.equal(overlayProfile, join(dirname(config), 'profile'));
          child.emit('exit', 0, null);
        }).catch(error => child.emit('error', error));
      } else {
        assert.equal(file, 'cargo');
        assert.ok(args.includes('workspace_bridge'));
        queueMicrotask(() => child.emit('exit', 0, null));
      }
      return child;
    };
    syncBuiltinESMExports();
    const { withNativePublishedProfile } = await import(`./published-revision-access-windows.fixture.mjs?${encodeURIComponent(t.name)}`);
    let error;
    try {
      await withNativePublishedProfile({ project: { outputDir: join(scratch, 'output', 'webview2') } }, observation, async entry => {
        assert.equal(entry.page, page);
        assert.deepEqual(entry.ids, { csv: 'synthetic-csv', folder: 'synthetic-folder-revision' });
        setup.push('journey');
        journeyAt = now;
      });
    } catch (failure) {
      error = failure;
    }
    assert.ok(profileTemp, error?.stack);
    await assert.rejects(access(profileTemp), { code: 'ENOENT' });
    assert.equal(desktopClosed, Boolean(desktop));
    assert.equal(browserClosed, Boolean(attaches));
    assert.ok(seedBridges.every(bridge => bridge.closed));
    assert.equal(observation.launch.stdout_tail, desktop ? 'synthetic native stdout' : '');
    assert.equal(observation.launch.stderr_tail, desktop ? 'synthetic native stderr' : '');
    const { stage, endpoint_state, endpoint_ready_ms, identity_ready_ms, failure_at_ms } = observation.launch;
    const result = {
      stage, endpoint_state, endpoint_ready_ms, identity_ready_ms, failure_at_ms, attaches, journeyAt,
      first_ownership_budget: ownershipBudgets[0], last_ownership_budget: ownershipBudgets.at(-1),
      inspected_listener_pid: observation.launch.process?.listeners[0]?.pid,
    };
    t.diagnostic(JSON.stringify(result));
    return {
      ...result, error, runtime: observation.runtime,
      profile_prepared: observation.launch.private_profile_prepared,
      profile_preparation_stderr: observation.launch.profile_preparation_stderr,
      profile_status: observation.profile_status, setup, seedRequests, desktopRequests,
    };
  } finally {
    t.mock.restoreAll();
    childProcess.execFile = originalExecFile;
    childProcess.spawn = originalSpawn;
    syncBuiltinESMExports();
    await rm(scratch, { recursive: true, force: true });
  }
}

test('a timely endpoint and owned listener enter the public journey', async t => {
  const result = await replay(t, { ownerAt: 59750 });
  assert.ifError(result.error);
  assert.equal(result.endpoint_ready_ms, 59000);
  assert.equal(result.stage, 'completed');
  assert.equal(result.journeyAt, 59750);
  assert.equal(result.attaches, 1);
  assert.equal(result.runtime.webview_pid, webviewPid);
  assert.equal(result.profile_prepared, true);
  assert.deepEqual(result.setup, ['prepare', 'publish', 'reopen seed', 'spawn desktop',
    'desktop status', 'desktop status', 'desktop status', 'journey']);
  assert.deepEqual(result.desktopRequests, result.seedRequests);
  assert.deepEqual(result.profile_status.desktop, result.profile_status.seed);
  assert.deepEqual(result.profile_status.desktop.seeded_csv.reply, { result: { available: true } });
  assert.deepEqual(result.profile_status.desktop.missing_csv.reply, { result: { available: false } });
  assert.deepEqual(result.profile_status.desktop.missing_folder.reply, missingFolder);
  t.diagnostic(JSON.stringify(result.profile_status.desktop));
});

test('private profile preparation refusal stops before seeding or spawning the desktop', async t => {
  const result = await replay(t, { refusePreparation: true });
  assert.match(result.error.message, /Synthetic preparation refused/u);
  assert.equal(result.stage, 'prepare_profile');
  assert.equal(result.profile_prepared, undefined);
  assert.equal(result.profile_preparation_stderr, 'Workspace storage ACL check refused (prepare, local-path).');
  assert.equal(result.profile_status, undefined);
  assert.deepEqual(result.setup, ['prepare']);
  assert.equal(result.attaches, 0);
  t.diagnostic(result.profile_preparation_stderr);
});

test('the first desktop refusal is retained alongside seed success before entering the journey', async t => {
  const result = await replay(t, { ownerAt: 59000, refuseDesktopStatus: true });
  assert.ok(result.error);
  assert.equal(result.stage, 'desktop_profile_status');
  assert.equal(result.attaches, 1);
  assert.equal(result.journeyAt, undefined);
  assert.deepEqual(result.desktopRequests, result.seedRequests.slice(0, 1));
  assert.deepEqual(result.profile_status.seed.seeded_csv.reply, { result: { available: true } });
  assert.deepEqual(result.profile_status.desktop, { seeded_csv: { args: result.seedRequests[0], reply: storageRefusal } });
  t.diagnostic(JSON.stringify(result.profile_status.desktop.seeded_csv));
});

test('endpoint discovery at 59 s cannot admit a listener first owned at 64 s', async t => {
  const result = await replay(t, { ownerAt: 64000 });
  assert.equal(result.attaches, 0);
  assert.ok(result.error);
  assert.equal(result.stage, 'native_identity');
  assert.equal(result.endpoint_ready_ms, 59000);
  assert.equal(result.failure_at_ms, 60000);
  assert.equal(result.runtime, undefined);
});

test('endpoint absence fails at the original deadline', async t => {
  const result = await replay(t, { endpointAt: Infinity });
  assert.ok(result.error);
  assert.equal(result.stage, 'cdp_endpoint');
  assert.equal(result.endpoint_state, 'port_file_missing');
  assert.equal(result.failure_at_ms, 60000);
  assert.equal(result.first_ownership_budget, undefined);
  assert.equal(result.attaches, 0);
});

test('a foreign listener cannot enter the public journey', async t => {
  const result = await replay(t, { endpointAt: 500, ownerAt: 500, foreign: true });
  assert.ok(result.error);
  assert.equal(result.stage, 'native_identity');
  assert.equal(result.endpoint_ready_ms, 500);
  assert.equal(result.attaches, 0);
});

test('an in-flight process inspection receives only the remaining launch budget', async t => {
  const result = await replay(t, { ownerAt: 59000, processDelay: 5000 });
  assert.equal(result.attaches, 0);
  assert.ok(result.error);
  assert.equal(result.stage, 'native_identity');
  assert.equal(result.first_ownership_budget, 1000);
  assert.equal(result.failure_at_ms, 60000);
});

test('an owned listener returned after the deadline cannot enter the public journey', async t => {
  const result = await replay(t, { ownerAt: 64000, processDelay: 5000, ignoreProcessTimeout: true });
  assert.equal(result.attaches, 0);
  assert.ok(result.error);
  assert.equal(result.stage, 'native_identity');
  assert.equal(result.failure_at_ms, 64000);
  assert.equal(result.inspected_listener_pid, webviewPid);
  assert.equal(result.identity_ready_ms, undefined);
});
