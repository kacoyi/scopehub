import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';

test('exercise the packaged frontend in an isolated native WebView2 window', async () => {
  test.setTimeout(25 * 60_000);
  const script = fileURLToPath(new URL('./renderer-probe-windows.ps1', import.meta.url));
  // Windows PowerShell supplies the .NET Framework UI Automation assemblies in-box.
  const child = spawn('powershell.exe', ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', script],
    { stdio: 'inherit', windowsHide: true });
  const [code] = await once(child, 'exit');
  expect(code, 'Native build, WebView2 interaction and disposable profile cleanup').toBe(0);
});
