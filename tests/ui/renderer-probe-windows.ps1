# Exercise the embedded WebView2 through its accessibility tree in an isolated synthetic profile.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$run = [Guid]::NewGuid().ToString('N')
$identifier = "com.scopehub.rendererprobe.$run"
$temp = Join-Path ([IO.Path]::GetTempPath()) "scopehub-renderer-$run"
$appData = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) $identifier
if ((Test-Path -LiteralPath $temp) -or (Test-Path -LiteralPath $appData)) { throw 'Probe profile already exists.' }
$browserData = Join-Path $temp 'webview'
$priorBrowserData = $env:WEBVIEW2_USER_DATA_FOLDER
$priorBrowserArgs = $env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS
$app = $null
$window = $null

function Wait-ForProbe($description, [scriptblock]$observe) {
    $watch = [Diagnostics.Stopwatch]::StartNew()
    do {
        if ($app.HasExited) { throw "Desktop exited before $description (code $($app.ExitCode))." }
        try {
            $value = & $observe
            if ($value) { return $value }
        } catch [System.Windows.Automation.ElementNotAvailableException] {
            # WebView2 can replace accessibility nodes during a React render.
        }
        Start-Sleep -Milliseconds 100
    } while ($watch.Elapsed.TotalSeconds -lt 25)
    throw "Timed out waiting for $description."
}

function Find-Control([string]$name, $type) {
    $byName = [Windows.Automation.PropertyCondition]::new(
        [Windows.Automation.AutomationElement]::NameProperty, $name)
    $condition = $byName
    if ($null -ne $type) {
        $byType = [Windows.Automation.PropertyCondition]::new(
            [Windows.Automation.AutomationElement]::ControlTypeProperty, $type)
        $condition = [Windows.Automation.AndCondition]::new($byName, $byType)
    }
    $window.FindFirst([Windows.Automation.TreeScope]::Descendants, $condition)
}

function Wait-Name([string]$name) {
    Wait-ForProbe $name { Find-Control $name $null } | Out-Null
}

function Invoke-Button([string]$name) {
    $button = Wait-ForProbe $name { Find-Control $name ([Windows.Automation.ControlType]::Button) }
    $button.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke()
}

function Draft-Pattern {
    $editor = Wait-ForProbe 'composition editor' {
        Find-Control 'Composition JSON' ([Windows.Automation.ControlType]::Edit)
    }
    $editor.GetCurrentPattern([Windows.Automation.ValuePattern]::Pattern)
}

Push-Location $root
try {
    New-Item -ItemType Directory -Path $temp | Out-Null
    $config = Join-Path $temp 'tauri.renderer.json'
    [IO.File]::WriteAllText($config, (@{ identifier = $identifier } | ConvertTo-Json))
    $metadata = cargo metadata --no-deps --format-version 1
    if ($LASTEXITCODE) { throw 'Cargo target directory lookup failed.' }
    # Keep the test-identifier binary away from the normal release executable.
    $target = Join-Path ($metadata | ConvertFrom-Json).target_directory 'x86_64-pc-windows-msvc'
    Push-Location (Join-Path $root 'apps/desktop')
    try {
        pnpm exec tauri build --no-bundle --target x86_64-pc-windows-msvc --config $config
        if ($LASTEXITCODE) { throw 'Native WebView2 probe build failed.' }
    } finally { Pop-Location }

    $env:WEBVIEW2_USER_DATA_FOLDER = $browserData
    $env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS = '--lang=en-US'
    $exe = Join-Path $target 'release/scopehub-desktop.exe'
    $app = Start-Process -FilePath $exe -PassThru
    $window = Wait-ForProbe 'native main window' {
        $app.Refresh()
        if ($app.MainWindowHandle -ne [IntPtr]::Zero) {
            [Windows.Automation.AutomationElement]::FromHandle($app.MainWindowHandle)
        }
    }
    if ($window.Current.ProcessId -ne $app.Id) { throw 'Unexpected native window owner.' }
    Invoke-Button 'Open workbench prototype'
    Wait-Name 'The canvas is empty. Add a block and preview your draft.'
    Invoke-Button 'Add threshold tiles'
    Invoke-Button 'Add value table'
    $timer = [Diagnostics.Stopwatch]::StartNew()
    Invoke-Button 'Preview draft'
    Wait-Name 'Threshold tiles'
    Wait-Name 'Value table'
    Wait-Name '21:00 3499.2 Below'
    Wait-Name '21:01 3499.8 At or above'
    $composeMs = $timer.ElapsedMilliseconds
    Invoke-Button 'Accept view'
    Wait-Name 'Accepted view'
    $accepted = (Draft-Pattern).Current.Value

    $custom = '{"version":1,"blocks":[{"kind":"threshold-tiles","threshold":3500,"belowLabel":"Under my target","aboveLabel":"Target reached"}]}'
    (Draft-Pattern).SetValue($custom)
    $timer.Restart()
    Invoke-Button 'Preview draft'
    Wait-Name '21:00 3499.2 Under my target'
    Wait-Name '21:01 3499.8 Under my target'
    $editMs = $timer.ElapsedMilliseconds
    (Draft-Pattern).SetValue('{"version":1,"blocks":[{"kind":"unsupported"}]}')
    $timer.Restart()
    Invoke-Button 'Preview draft'
    Wait-Name 'Block 1 has an unsupported kind, field or threshold.'
    Wait-Name 'Value table'
    Wait-Name '21:01 3499.8 At or above'
    $recoverMs = $timer.ElapsedMilliseconds
    Invoke-Button 'Restore accepted view'
    Wait-ForProbe 'restored accepted definition' { (Draft-Pattern).Current.Value -ceq $accepted } | Out-Null
    Invoke-Button 'Back to chart'
    Wait-Name 'Open workbench prototype'

    $browser = Get-CimInstance Win32_Process -Filter "ParentProcessId = $($app.Id) AND Name = 'msedgewebview2.exe'" |
        Select-Object -First 1
    if (!$browser.ExecutablePath) { throw 'Missing WebView2 child runtime identity.' }
    $commit = git rev-parse HEAD
    if ($LASTEXITCODE) { throw 'Source commit lookup failed.' }
    @{
        host = 'Windows WebView2'; source_commit = $commit
        os_version = [Environment]::OSVersion.VersionString
        runtime_version = (Get-Item -LiteralPath $browser.ExecutablePath).VersionInfo.ProductVersion
        binary_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $exe).Hash.ToLowerInvariant()
        fixture = '3a1b0032b57711c9563131caf34c8cfda9d91bbf5ef5e9dbc7a4f0f48ab390d1'
        measurement = 'UI Automation invocation to accessible result, including polling'
        compose_ms = $composeMs; edit_ms = $editMs; recovery_ms = $recoverMs
    } | ConvertTo-Json -Compress | Write-Output
} finally {
    if ($null -ne $app) {
        if (!$app.HasExited) {
            $null = $app.CloseMainWindow()
            if (!$app.WaitForExit(5000)) { $app.Kill(); $app.WaitForExit() }
        }
        $app.Dispose()
    }
    $env:WEBVIEW2_USER_DATA_FOLDER = $priorBrowserData
    $env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS = $priorBrowserArgs
    Pop-Location
    # The embedded browser can briefly keep its profile files open during shutdown.
    foreach ($path in @($appData, $temp)) {
        for ($i = 0; (Test-Path -LiteralPath $path) -and $i -lt 30; $i++) {
            try { Remove-Item -LiteralPath $path -Recurse -Force } catch {
                if ($i -eq 29) { throw }
                Start-Sleep -Milliseconds 300
            }
        }
    }
}
