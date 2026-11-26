import { spawn, execFile } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { once } from 'node:events';
import { createReadStream } from 'node:fs';
import { access, copyFile, mkdir, mkdtemp, readFile, realpath, rm, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import { chromium, expect } from '@playwright/test';
import { startLocalImportBridge } from './local-import-bridge.mjs';

const runFile = promisify(execFile);
const require = createRequire(import.meta.url);
const root = fileURLToPath(new URL('../../', import.meta.url));
const targetTriple = 'x86_64-pc-windows-msvc';
const tauriOrigin = /^(https?:\/\/tauri\.localhost|tauri:\/\/localhost)\//u;
const exeName = 'scopehub-desktop.exe';
const endpointTimeout = 60_000;
const profileDirectory = 'profile';
// Supplying arguments replaces Wry's defaults, including its autoplay policy.
const browserArguments = '--disable-features=msWebOOUI,msPdfOOUI,msSmartScreenProtection '
  + '--autoplay-policy=no-user-gesture-required --remote-debugging-port=0 --lang=en-US';
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

async function buildProbe(temp) {
  const { stdout } = await runFile('cargo', ['metadata', '--locked', '--no-deps', '--format-version', '1'], { cwd: root });
  // Preserve the normal debug and release executables. Keep this build cache reusable.
  const buildEnv = { ...process.env, CARGO_TARGET_DIR: join(JSON.parse(stdout).target_directory, 'published-revision-access') };
  const configuration = join(temp, 'tauri.revision-access.json');
  const base = JSON.parse(await readFile(join(root, 'apps', 'desktop', 'src-tauri', 'tauri.conf.json'), 'utf8'));
  // WebView2 ignores user-scoped environment overrides for elevated hosts.
  // The test-only overlay passes these options directly to the native builder.
  await writeFile(configuration, JSON.stringify({
    identifier: 'com.scopehub.revisionaccess',
    app: {
      appDirectoriesOverride: `./${profileDirectory}`,
      windows: base.app.windows.map(window => window.label === 'main'
        ? { ...window, additionalBrowserArgs: browserArguments } : window),
    },
  }));
  const tauriCli = require.resolve('@tauri-apps/cli/package.json').replace(/package\.json$/u, 'tauri.js');
  await build(process.execPath, [tauriCli, 'build', '--debug', '--ci', '--no-bundle',
    '--target', targetTriple, '--config', configuration, '--', '--locked'], {
    cwd: join(root, 'apps', 'desktop'), env: buildEnv,
  });
  await build('cargo', ['build', '--locked', '-p', 'scopehub-desktop', '--example', 'local_import_bridge',
    '--features', 'portable-test-bridge', '--target', targetTriple], { cwd: root, env: buildEnv });
  const debug = join(buildEnv.CARGO_TARGET_DIR, targetTriple, 'debug');
  return { exe: join(debug, exeName), seedExe: join(debug, 'examples', 'local_import_bridge.exe') };
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

async function pollLaunch(desktop, launch, deadline, probe) {
  while (performance.now() < deadline) {
    if (desktop.exitCode !== null || desktop.signalCode !== null) {
      launch.stage = 'early_process_exit';
      throw new Error(`Native desktop exited before CDP (${desktop.exitCode ?? desktop.signalCode}).`);
    }
    const remaining = Math.ceil(deadline - performance.now());
    if (remaining <= 0) break;
    const result = await probe(remaining);
    const afterProbe = deadline - performance.now();
    if (afterProbe <= 0) break;
    if (result) return result;
    await sleep(Math.min(250, afterProbe));
  }
  throw new Error(`Native desktop launch reached its ${endpointTimeout} ms deadline at ${launch.stage}.`);
}

async function readEndpoint(portFile, observation, remaining) {
  try {
    const signal = AbortSignal.timeout(Math.min(1000, remaining));
    const [portText, websocketPath] = (await readFile(portFile, { encoding: 'utf8', signal })).trim().split(/\r?\n/u);
    const port = Number(portText);
    if (!/^\d{1,5}$/u.test(portText) || port < 1 || port > 65535
      || !/^\/devtools\/browser\/[0-9a-f-]+$/u.test(websocketPath ?? '')) {
      observation.endpoint_state = 'invalid_port_file';
      return;
    }
    observation.port = port;
    const address = `127.0.0.1:${port}`;
    const response = await fetch(`http://${address}/json/version`, { redirect: 'error', signal });
    observation.http_status = response.status;
    if (!response.ok) {
      observation.endpoint_state = 'http_error';
      await response.body?.cancel();
      return;
    }
    const { webSocketDebuggerUrl } = await response.json();
    const websocket = `ws://${address}${websocketPath}`;
    observation.endpoint_state = webSocketDebuggerUrl === websocket ? 'ready' : 'endpoint_mismatch';
    if (observation.endpoint_state === 'ready') return websocket;
  } catch (error) {
    observation.endpoint_state = error.code === 'ENOENT' ? 'port_file_missing' : 'endpoint_read_failed';
    observation.endpoint_error = error.cause?.code ?? error.code ?? error.name;
  }
}

function powershell(command, timeout = 15_000) {
  return runFile('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', command], { timeout });
}

async function processSnapshot(pid, port, remaining = 15_000) {
  // Record only facts about the owned app, its WebView2 children and the expected port.
  const { stdout } = await powershell(
    String.raw`$ErrorActionPreference = 'Stop'
     $desktop = Get-Process -Id ${pid} -ErrorAction SilentlyContinue
     $token = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
     $runtimes = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = ${pid} AND Name = 'msedgewebview2.exe'" |
       Select-Object -First 8 | ForEach-Object {
         $version = if ($_.ExecutablePath) { (Get-Item -LiteralPath $_.ExecutablePath).VersionInfo.ProductVersion }
         @{ pid = $_.ProcessId; version = $version;
            allocated_debug_port = $_.CommandLine -match '(?:^|\s)--remote-debugging-port=0(?:\s|$)' }
       })
     $listeners = @(if (${port ?? 0}) {
       Get-NetTCPConnection -State Listen -LocalPort ${port ?? 0} -ErrorAction SilentlyContinue |
         Select-Object -First 8 | ForEach-Object { @{ address = $_.LocalAddress; pid = $_.OwningProcess } }
     })
     $hostOS = Get-CimInstance Win32_OperatingSystem
     @{ desktop_pid = ${pid}; process_alive = [bool]$desktop;
        window = $(if ($desktop) { $desktop.MainWindowHandle.ToInt64() }); session_id = $desktop.SessionId;
        launcher_administrator = $token.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator);
        user_interactive = [Environment]::UserInteractive; webview_children = $runtimes; listeners = $listeners;
        windows_build = $hostOS.BuildNumber; windows_product_type = $hostOS.ProductType;
        windows_edition = $hostOS.Caption } | ConvertTo-Json -Compress`,
    Math.min(15_000, remaining),
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
  const profile = join(temp, profileDirectory);
  const webviewDirectory = join(profile, 'EBWebView');
  const launch = {
    stage: 'build', endpoint_timeout_ms: endpointTimeout, endpoint_attempts: 0,
    browser_arguments_source: 'Tauri window configuration', browser_arguments: browserArguments,
  };
  observation.launch = launch;
  let desktop;
  let exited;
  let browser;
  let page;
  let started;
  let stdout = '';
  let stderr = '';
  let failure;
  try {
    await mkdir(profile, { mode: 0o700 });
    Object.assign(observation, {
      scope: 'Automated compiled Windows WebView2 public revision access with synthetic profile data.',
      source_commit: commit.trim(), source_working_tree_dirty: Boolean(gitStatus.trim()),
      fixture_sha256: fixtureSha,
      setup: 'Existing test bridge seeds a disposable profile via the Rust registry. The observed journey uses the compiled app.',
      observed_at: new Date().toISOString(),
    });
    const { exe, seedExe } = await buildProbe(temp);
    const artifact = join(dirname(testInfo.project.outputDir), exeName);
    const launchExe = join(temp, exeName);
    await mkdir(dirname(artifact), { recursive: true });
    await copyFile(exe, artifact);
    await copyFile(artifact, launchExe);
    const hash = createHash('sha256');
    for await (const chunk of createReadStream(launchExe)) hash.update(chunk);
    observation.binary_sha256 = hash.digest('hex');
    launch.stage = 'seed_profile';
    const ids = await publishFixture(seedExe, profile, join(temp, 'source'));
    observation.publications = ids;
    const env = Object.fromEntries(Object.entries(process.env)
      .filter(([name]) => !/^(WEBVIEW2_.*|SCOPEHUB_DESKTOP_WORKSPACE_DEV)$/iu.test(name)));
    launch.stage = 'spawn';
    started = performance.now();
    const deadline = started + endpointTimeout;
    desktop = spawn(launchExe, [], { cwd: temp, env, stdio: ['ignore', 'pipe', 'pipe'] });
    exited = once(desktop, 'exit');
    void exited.catch(() => undefined);
    desktop.stdout.setEncoding('utf8').on('data', chunk => { stdout = (stdout + chunk).slice(-4096); });
    desktop.stderr.setEncoding('utf8').on('data', chunk => { stderr = (stderr + chunk).slice(-4096); });
    await once(desktop, 'spawn');
    launch.desktop_pid = desktop.pid;
    launch.stage = 'cdp_endpoint';
    const websocket = await pollLaunch(desktop, launch, deadline, remaining => {
      launch.endpoint_attempts++;
      return readEndpoint(join(webviewDirectory, 'DevToolsActivePort'), launch, remaining);
    });
    launch.endpoint_ready_ms = Math.round(performance.now() - started);
    launch.stage = 'native_identity';
    const runtime = await pollLaunch(desktop, launch, deadline, async remaining => {
      try {
        const snapshot = await processSnapshot(desktop.pid, launch.port, remaining);
        launch.process = snapshot;
        if (snapshot.window) {
          return snapshot.webview_children.find(child => child.version && snapshot.listeners.some(listener =>
            listener.address === '127.0.0.1' && listener.pid === child.pid));
        }
      } catch (error) {
        launch.process_error = error.code ?? error.name;
      }
    });
    launch.identity_ready_ms = Math.round(performance.now() - started);
    launch.stage = 'cdp_attach';
    browser = await chromium.connectOverCDP(websocket, { timeout: 20_000 });
    launch.stage = 'product_page';
    const productPages = () => browser.contexts().flatMap(context => context.pages()).filter(page => tauriOrigin.test(page.url()));
    await expect.poll(() => productPages().length, { timeout: 30_000 }).toBe(1);
    [page] = productPages();
    observation.runtime = {
      desktop_pid: desktop.pid, window: launch.process.window, webview_pid: runtime.pid, webview_version: runtime.version,
      windows_build: launch.process.windows_build, windows_product_type: launch.process.windows_product_type,
      windows_edition: launch.process.windows_edition, origin: new URL(page.url()).origin,
    };
    launch.stage = 'product_ipc';
    await page.bringToFront();
    await page.waitForFunction(() => typeof window.__TAURI_INTERNALS__?.invoke === 'function', undefined, { timeout: 20_000 });
    const database = join(profile, 'workspace-dev', 'workspace.sqlite3');
    await access(database);
    launch.stage = 'public_journey';
    await run({ page, ids, database });
    launch.stage = 'completed';
  } catch (error) {
    failure = error;
    if (started !== undefined) launch.failure_at_ms = Math.round(performance.now() - started);
    if (desktop?.pid) {
      launch.exit_before_cleanup = desktop.exitCode;
      launch.signal_before_cleanup = desktop.signalCode;
      launch.webview_directory_created = await access(webviewDirectory).then(() => true, () => false);
      try {
        launch.failure_process = await processSnapshot(desktop.pid, launch.port);
      } catch (snapshotError) {
        launch.failure_process_error = snapshotError.code ?? snapshotError.name;
      }
    }
    if (page) {
      try {
        await testInfo.attach('native-failure', { body: await page.screenshot({ timeout: 4000 }), contentType: 'image/png' });
      } catch {}
    }
    throw error;
  } finally {
    try {
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
    } catch (error) {
      launch.cleanup_error = error.code ?? error.name;
      if (!failure) {
        launch.stage = 'cleanup';
        throw error;
      }
    } finally {
      if (desktop?.pid) launch.exit_after_cleanup = desktop.exitCode;
      launch.stdout_tail = stdout.replaceAll(temp, '<fixture>').replaceAll(root, '<checkout>');
      launch.stderr_tail = stderr.replaceAll(temp, '<fixture>').replaceAll(root, '<checkout>');
    }
  }
}
