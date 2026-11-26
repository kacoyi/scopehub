import assert from 'node:assert/strict';
import childProcess from 'node:child_process';
import { EventEmitter } from 'node:events';
import { access, mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { syncBuiltinESMExports } from 'node:module';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { PassThrough, Writable } from 'node:stream';
import test from 'node:test';
import timers from 'node:timers/promises';
import { chromium, expect } from '@playwright/test';

const port = 42000;
const desktopPid = 42;
const webviewPid = 43;
const debuggerPath = '/devtools/browser/12345678-9abc';
const websocket = `ws://127.0.0.1:${port}${debuggerPath}`;

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
  ignoreProcessTimeout = false } = {}) {
  const scratch = await mkdtemp(join(tmpdir(), 'scopehub-launch-control-'));
  let now = 0;
  let profileTemp;
  let desktop;
  let desktopClosed = false;
  let browserClosed = false;
  let attaches = 0;
  let journeyAt;
  const ownershipBudgets = [];
  const observation = {};
  const { execFile: originalExecFile, spawn: originalSpawn } = childProcess;
  const target = join(scratch, 'published-revision-access', 'x86_64-pc-windows-msvc', 'debug');
  const page = { url: () => 'http://tauri.localhost/index.html', bringToFront() {}, waitForFunction() {} };
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
        child.stdout = new PassThrough();
        child.stderr = new PassThrough();
        child.stdin = new Writable({
          write(line, _encoding, done) {
            child.stdout.write(JSON.stringify({ result: bridgeReply(JSON.parse(line)) }) + '\n');
            done();
          },
          final(done) { child.stdout.end(); child.emit('close', 0); done(); },
        });
      } else if (file.endsWith('scopehub-desktop.exe')) {
        desktop = child;
        profileTemp = options.cwd;
        Object.assign(child, {
          pid: desktopPid, exitCode: null, signalCode: null, stdout: new PassThrough(), stderr: new PassThrough(),
        });
        void (async () => {
          try {
            const webview = join(profileTemp, 'profile', 'EBWebView');
            const workspace = join(profileTemp, 'profile', 'workspace-dev');
            await mkdir(webview, { recursive: true });
            await mkdir(workspace, { recursive: true });
            if (Number.isFinite(endpointAt)) await writeFile(join(webview, 'DevToolsActivePort'), `${port}\n${debuggerPath}\n`);
            await writeFile(join(workspace, 'workspace.sqlite3'), '');
            child.stdout.write('synthetic native stdout');
            child.stderr.write('synthetic native stderr');
            child.emit('spawn');
          } catch (error) {
            child.emit('error', error);
          }
        })();
      } else {
        assert.ok(file === 'cargo' || file === process.execPath, `Unexpected fixture subprocess: ${file}`);
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
        journeyAt = now;
      });
    } catch (failure) {
      error = failure;
    }
    assert.ok(profileTemp, error?.stack);
    await assert.rejects(access(profileTemp), { code: 'ENOENT' });
    assert.equal(desktopClosed, true);
    assert.equal(browserClosed, Boolean(attaches));
    assert.equal(observation.launch.stdout_tail, 'synthetic native stdout');
    assert.equal(observation.launch.stderr_tail, 'synthetic native stderr');
    const { stage, endpoint_state, endpoint_ready_ms, identity_ready_ms, failure_at_ms } = observation.launch;
    const result = {
      stage, endpoint_state, endpoint_ready_ms, identity_ready_ms, failure_at_ms, attaches, journeyAt,
      first_ownership_budget: ownershipBudgets[0], last_ownership_budget: ownershipBudgets.at(-1),
      inspected_listener_pid: observation.launch.process?.listeners[0]?.pid,
    };
    t.diagnostic(JSON.stringify(result));
    return { ...result, error, runtime: observation.runtime };
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
