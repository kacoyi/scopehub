import { spawn, execFile } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { once } from 'node:events';
import { createReadStream } from 'node:fs';
import { access, mkdir, mkdtemp, realpath, rm, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import { chromium, expect } from '@playwright/test';
import { startLocalImportBridge } from './local-import-bridge.mjs';

const runFile = promisify(execFile);
const require = createRequire(import.meta.url);
const root = fileURLToPath(new URL('../../', import.meta.url));
const targetTriple = 'x86_64-pc-windows-msvc';
const tauriOrigin = /^(https?:\/\/tauri\.localhost|tauri:\/\/localhost)\//u;
const fixture = [
  'Ticker,TradingDate,First,Top,Bottom,Native close,Shares',
  '000731,2026-04-14,36,39,34,38,120',
  '000731,2026-04-15,38,41,37,40,',
].join('\n') + '\n';
const fixtureSha = createHash('sha256').update(fixture).digest('hex');

async function build(file, args, options) {
  const child = spawn(file, args, { stdio: 'inherit', ...options });
  const [code, signal] = await once(child, 'exit');
  if (code !== 0) throw new Error(`Native build failed (${code ?? signal}): ${file} ${args.join(' ')}`);
}

async function buildProbe(temp, identifier, profile) {
  const { stdout } = await runFile('cargo', ['metadata', '--locked', '--no-deps', '--format-version', '1'], { cwd: root });
  // Preserve the normal debug and release executables. Keep this build cache reusable.
  const buildEnv = { ...process.env, CARGO_TARGET_DIR: join(JSON.parse(stdout).target_directory, 'published-revision-access') };
  const configuration = join(temp, 'tauri.revision-access.json');
  await writeFile(configuration, JSON.stringify({ identifier, app: { appDirectoriesOverride: profile } }));
  const tauriCli = require.resolve('@tauri-apps/cli/package.json').replace(/package\.json$/u, 'tauri.js');
  await build(process.execPath, [tauriCli, 'build', '--debug', '--ci', '--no-bundle',
    '--target', targetTriple, '--config', configuration, '--', '--locked'], {
    cwd: join(root, 'apps', 'desktop'), env: buildEnv,
  });
  await build('cargo', ['build', '--locked', '-p', 'scopehub-desktop', '--example', 'local_import_bridge',
    '--features', 'portable-test-bridge', '--target', targetTriple], { cwd: root, env: buildEnv });
  const debug = join(buildEnv.CARGO_TARGET_DIR, targetTriple, 'debug');
  return { exe: join(debug, 'scopehub-desktop.exe'), seedExe: join(debug, 'examples', 'local_import_bridge.exe') };
}

export async function publishFixture(seedExe, profile, folder) {
  await mkdir(folder, { mode: 0o700 });
  await writeFile(join(folder, 'daily.csv'), fixture);
  const bridge = startLocalImportBridge(seedExe, profile, folder);
  try {
    const market = async (operation, payload) => {
      const reply = await bridge.request('execute_operation', {
        operation: 'market.' + operation, payload: { version: 1, ...payload }, requestId: randomUUID(),
      });
      expect(reply.error, operation + ': ' + JSON.stringify(reply.error)).toBeUndefined();
      return reply.result;
    };
    const picked = await bridge.request('choose_local_csv_folder', {});
    expect(picked.error).toBeUndefined();
    const { folder_id, files } = picked.result;
    const source = { folder_id, file_id: files[0].file_id, expected_sha256: fixtureSha };
    const mapping = {
      version: 1, revision: 'native-revision-access', encoding: 'utf-8', delimiter: ',',
      record_kind: 'native_ohlcv', volume_semantics: 'per_bar', timestamp_convention: 'bar_start',
      instrument_kind: 'equity', venue: 'TEST-SH', timeframe: '1d', calendar_id: 'synthetic-cash-v1',
      session_scope: 'day', timezone: 'Asia/Shanghai', quantity_unit: 'shares', price_adjustment: 'raw',
      timestamp_format: '%Y-%m-%d', trading_day_format: '%Y-%m-%d', daily_start_time: '09:30:00',
      columns: { symbol: 'Ticker', timestamp: 'TradingDate', trading_day: 'TradingDate', open: 'First',
        high: 'Top', low: 'Bottom', close: 'Native close', volume: 'Shares' },
    };
    const input = { ...source, mapping, row_limit: 2 };
    await market('previewLocalImport', input);
    const { record } = await market('saveLocalImportMapping', input);
    const { revision: csv } = await market('publishLocalImport', { ...source, review_revision: record.review_revision });
    const staged = await market('previewLocalFolderRefresh', { folder_id, revision_id: csv.revision_id });
    const review = { folder_id, review_id: staged.review_id };
    const prepared = await market('prepareLocalFolderPublication', { ...review, conflicts: [], withdraw_bars: [], withdraw_sources: [] });
    const { revision: folderRevision } = await market('publishLocalFolderRefresh', {
      ...review, candidate_sha256: prepared.candidate_sha256, idempotency_key: randomUUID(),
    });
    expect(csv.revision_id).not.toBe(folderRevision.revision_id);
    expect(csv.bar_count).toBe(2);
    expect(folderRevision.bar_count).toBe(2);
    return { csv: csv.revision_id, folder: folderRevision.revision_id };
  } finally {
    await bridge.close();
  }
}

async function availablePort() {
  const server = createServer().listen(0, '127.0.0.1');
  await once(server, 'listening');
  const { port } = server.address();
  await new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
  return port;
}

function powershell(command) {
  return runFile('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', command], { timeout: 15_000 });
}

async function runtimeIdentity(pid) {
  // The loopback listener alone cannot identify WebView2. Confirm the native
  // window and the versioned WebView2 child owned by the launched executable.
  const { stdout } = await powershell(
    `$ErrorActionPreference = 'Stop'
     $desktop = Get-Process -Id ${pid}
     if ($desktop.MainWindowHandle -eq 0) { throw 'No native window for the owned process.' }
     $runtime = Get-CimInstance Win32_Process -Filter "ParentProcessId = ${pid} AND Name = 'msedgewebview2.exe'" |
       Select-Object -First 1
     if (!$runtime.ExecutablePath) { throw 'No native WebView2 child for the owned process.' }
     $hostOS = Get-CimInstance Win32_OperatingSystem
     @{ window = $desktop.MainWindowHandle.ToInt64(); webview_pid = $runtime.ProcessId;
        webview_version = (Get-Item -LiteralPath $runtime.ExecutablePath).VersionInfo.ProductVersion;
        windows_build = $hostOS.BuildNumber; windows_product_type = $hostOS.ProductType;
        windows_edition = $hostOS.Caption } | ConvertTo-Json -Compress`,
  );
  return JSON.parse(stdout);
}

async function closeDesktop(child, exited) {
  if (child.pid && child.exitCode === null && child.signalCode === null) {
    try {
      await powershell(`$desktop = Get-Process -Id ${child.pid} -ErrorAction Stop
        if (!$desktop.CloseMainWindow() -or !$desktop.WaitForExit(10000)) { throw 'Native window did not close.' }`);
    } catch (error) {
      if (child.exitCode === null && child.signalCode === null) {
        child.kill();
        await exited;
        throw error;
      }
    }
  }
  await exited;
}

export async function withNativePublishedProfile(testInfo, observation, run) {
  const { stdout: commit } = await runFile('git', ['rev-parse', 'HEAD'], { cwd: root });
  const { stdout: gitStatus } = await runFile('git', ['status', '--porcelain=v1'], { cwd: root });
  const temp = await mkdtemp(join(await realpath(tmpdir()), 'scopehub-revision-access-'));
  const identifier = 'com.scopehub.revisionaccess.' + randomUUID().replaceAll('-', '');
  const profile = join(temp, 'profile');
  let desktop;
  let exited;
  let browser;
  try {
    await mkdir(profile, { mode: 0o700 });
    Object.assign(observation, {
      scope: 'Automated compiled Windows WebView2 public revision access with synthetic profile data.',
      source_commit: commit.trim(), source_working_tree_dirty: Boolean(gitStatus.trim()),
      fixture_sha256: fixtureSha,
      setup: 'Existing test bridge seeds a disposable profile via the Rust registry. The observed journey uses the compiled app.',
      observed_at: new Date().toISOString(),
    });
    const { exe, seedExe } = await buildProbe(temp, identifier, profile);
    const ids = await publishFixture(seedExe, profile, join(temp, 'source'));
    const hash = createHash('sha256');
    for await (const chunk of createReadStream(exe)) hash.update(chunk);
    observation.binary_sha256 = hash.digest('hex');
    observation.publications = ids;
    const port = await availablePort();
    const env = {
      ...process.env,
      WEBVIEW2_USER_DATA_FOLDER: join(temp, 'webview'),
      WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS:
        `--remote-debugging-address=127.0.0.1 --remote-debugging-port=${port} --lang=en-US`,
    };
    delete env.SCOPEHUB_DESKTOP_WORKSPACE_DEV;
    desktop = spawn(exe, [], { cwd: root, env, stdio: 'inherit' });
    exited = once(desktop, 'exit');
    void exited.catch(() => undefined);
    // Establish the process or propagate a spawn error before starting UI polling.
    await once(desktop, 'spawn');
    const endpoint = `http://127.0.0.1:${port}`;
    await expect.poll(async () => {
      if (desktop.exitCode !== null) throw new Error(`Native desktop exited early: ${desktop.exitCode}`);
      try {
        const response = await fetch(endpoint + '/json/version', { signal: AbortSignal.timeout(1000) });
        return response.ok;
      } catch {
        return false;
      }
    }, { timeout: 60_000 }).toBe(true);
    browser = await chromium.connectOverCDP(endpoint, { timeout: 20_000 });
    const productPages = () => browser.contexts().flatMap(context => context.pages()).filter(page => tauriOrigin.test(page.url()));
    await expect.poll(() => productPages().length, { timeout: 30_000 }).toBe(1);
    const [page] = productPages();
    await expect.poll(async () => {
      try {
        observation.runtime = { desktop_pid: desktop.pid, ...await runtimeIdentity(desktop.pid), origin: page.url() };
        return Boolean(observation.runtime.window && observation.runtime.webview_version);
      } catch {
        return false;
      }
    }, { timeout: 30_000 }).toBe(true);
    await page.bringToFront();
    await page.waitForFunction(() => typeof window.__TAURI_INTERNALS__?.invoke === 'function', undefined, { timeout: 20_000 });
    const database = join(profile, 'workspace-dev', 'workspace.sqlite3');
    await access(database);
    try {
      await run({ page, ids, database });
    } catch (error) {
      try {
        await testInfo.attach('native-failure', { body: await page.screenshot({ timeout: 4000 }), contentType: 'image/png' });
      } catch {}
      throw error;
    }
  } finally {
    try {
      if (desktop?.pid) await closeDesktop(desktop, exited);
    } finally {
      try {
        if (browser?.isConnected()) await browser.close();
      } finally {
        // WebView2 may hold profile files briefly after its native parent exits.
        await rm(temp, { recursive: true, force: true, maxRetries: 20, retryDelay: 300 });
      }
    }
  }
}
