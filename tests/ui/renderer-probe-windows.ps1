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

function Find-Control([string]$name, $type, $within = $window) {
    $byName = [Windows.Automation.PropertyCondition]::new(
        [Windows.Automation.AutomationElement]::NameProperty, $name)
    $condition = $byName
    if ($null -ne $type) {
        $byType = [Windows.Automation.PropertyCondition]::new(
            [Windows.Automation.AutomationElement]::ControlTypeProperty, $type)
        $condition = [Windows.Automation.AndCondition]::new($byName, $byType)
    }
    $within.FindFirst([Windows.Automation.TreeScope]::Descendants, $condition)
}

function Wait-Name([string]$name) {
    Wait-ForProbe $name { Find-Control $name $null } | Out-Null
}

function Get-ObservedPatterns($control) {
    try {
        $names = @($control.GetSupportedPatterns() | ForEach-Object { $_.ProgrammaticName })
        if ($names.Count) { return ($names -join ', ') }
        return '<none>'
    } catch {
        return "unavailable ($($_.Exception.GetBaseException().Message))"
    }
}

function Invoke-ControlAction([string]$name, $control, [switch]$disclosure) {
    $action = if ($disclosure) { 'ExpandCollapsePattern or InvokePattern' } else { 'InvokePattern or TogglePattern' }
    $pattern = $null
    try {
        if ($disclosure -and $control.TryGetCurrentPattern([Windows.Automation.ExpandCollapsePattern]::Pattern, [ref]$pattern)) {
            $action = 'ExpandCollapsePattern.Expand'
            $pattern.Expand()
        } elseif ($control.TryGetCurrentPattern([Windows.Automation.InvokePattern]::Pattern, [ref]$pattern)) {
            $action = 'InvokePattern.Invoke'
            $pattern.Invoke()
        } elseif (!$disclosure -and $control.TryGetCurrentPattern([Windows.Automation.TogglePattern]::Pattern, [ref]$pattern)) {
            $action = 'TogglePattern.Toggle'
            $pattern.Toggle()
        } else {
            throw 'No applicable action pattern.'
        }
    } catch {
        throw "UI Automation control '$name' failed $action. Observed patterns: [$(Get-ObservedPatterns $control)]. $($_.Exception.GetBaseException().Message)"
    }
    Write-Host "UI Automation control '$name' used $action."
}

function Invoke-Button([string]$name) {
    $button = Wait-ForProbe "enabled button '$name'" {
        $control = Find-Control $name ([Windows.Automation.ControlType]::Button)
        if ($control -and $control.Current.IsEnabled) { $control }
    }
    Invoke-ControlAction $name $button
}

function Draft-Pattern {
    $editor = Wait-ForProbe "edit control 'Composition JSON'" {
        Find-Control 'Composition JSON' ([Windows.Automation.ControlType]::Edit)
    }
    $pattern = $null
    if (!$editor.TryGetCurrentPattern([Windows.Automation.ValuePattern]::Pattern, [ref]$pattern)) {
        throw "UI Automation edit 'Composition JSON' does not expose ValuePattern. Observed patterns: [$(Get-ObservedPatterns $editor)]."
    }
    return $pattern
}

function Open-Disclosure([string]$name) {
    $disclosure = Wait-ForProbe $name { Find-Control $name $null }
    Invoke-ControlAction $name $disclosure -Disclosure
}

function Wait-Annotation($expected, [string]$otherValue) {
    Wait-ForProbe "$($expected.bar_utc) $($expected.value) $($expected.result_revision)" {
        $row = Find-Control $expected.bar_utc $null
        ($row -and (Find-Control $expected.value $null $row) -and
            (Find-Control $expected.series_revision $null) -and (Find-Control $expected.result_revision $null) -and
            (-not (Find-Control $otherValue $null)))
    } | Out-Null
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
    $sourceName = "Frozen synthetic closes $([char]0x00B7) SIM.CN.FUTURE.001"
    Open-Disclosure $sourceName
    $hash = '3a1b0032b57711c9563131caf34c8cfda9d91bbf5ef5e9dbc7a4f0f48ab390d1'
    Wait-Name $hash
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

    # Reopen the ordinary editor and inspect the rendered view's accessible attribution.
    $five = @{
        timeframe = '5m'; bar_utc = '2024-01-04T09:40:00.000Z'; value = '3.2%'
        series_revision = 'qualification-equity-5m-bars-v1'; result_revision = 'qualification-equity-5m-results-v1'
    }
    $fifteen = @{
        timeframe = '15m'; bar_utc = '2024-01-04T09:30:00.000Z'; value = '4.7%'
        series_revision = 'qualification-equity-15m-bars-v1'; result_revision = 'qualification-equity-15m-results-v1'
    }
    Invoke-Button 'Open workbench prototype'
    Wait-Name 'The canvas is empty. Add a block and preview your draft.'
    $annotationDraft = '{"version":1,"blocks":[{"kind":"candlestick","source":"qualification-equity","annotation":{"field":"turnover","when":{"field":"strength","above":0.8}}}]}'
    (Draft-Pattern).SetValue($annotationDraft)
    Invoke-Button 'Preview draft'
    Wait-Name 'Candlestick chart with numeric annotations'
    Wait-ForProbe 'chart-only source scope' { -not (Find-Control $sourceName $null) } | Out-Null
    Open-Disclosure 'Synthetic result attribution'
    Wait-Annotation $five $fifteen.value
    Invoke-Button 'Accept view'
    Wait-Name 'Accepted view'
    Invoke-Button '15 min'
    Wait-Annotation $fifteen $five.value
    Invoke-Button '5 min'
    Wait-Annotation $five $fifteen.value
    (Draft-Pattern).SetValue(($annotationDraft -replace '"field":"turnover"', '"field":"rsi"'))
    Invoke-Button 'Preview draft'
    Wait-Name 'Block 1 must bind granted synthetic fields and a finite threshold.'
    Wait-Annotation $five $fifteen.value
    Invoke-Button 'Restore accepted view'
    Wait-ForProbe 'restored accepted annotation definition' {
        (Draft-Pattern).Current.Value -ceq $annotationDraft
    } | Out-Null
    Wait-Annotation $five $fifteen.value

    Invoke-Button 'Preview draft'
    Invoke-Button '15 min'
    Wait-Annotation $fifteen $five.value
    Invoke-Button 'Accept view'
    Wait-Name 'Accepted view'
    (Draft-Pattern).SetValue('{"version":1,"blocks":[]}')
    Invoke-Button 'Preview draft'
    Wait-Name 'The canvas is empty. Add a block and preview your draft.'
    Wait-ForProbe 'candlestick chart removed by preview' {
        -not (Find-Control 'Candlestick chart with numeric annotations' $null)
    } | Out-Null
    (Draft-Pattern).SetValue($annotationDraft.Replace('qualification-equity', 'configured-market-data'))
    Invoke-Button 'Preview draft'
    Wait-Name 'Block 1 must bind granted synthetic fields and a finite threshold.'
    Open-Disclosure 'Synthetic result attribution'
    Wait-Annotation $fifteen $five.value
    Invoke-Button 'Restore accepted view'
    Wait-ForProbe 'restored fifteen-minute author definition' {
        (Draft-Pattern).Current.Value -ceq $annotationDraft
    } | Out-Null
    Wait-ForProbe 'grant rejection cleared by restore' {
        -not (Find-Control 'Block 1 must bind granted synthetic fields and a finite threshold.' $null)
    } | Out-Null
    Wait-Annotation $fifteen $five.value

    # Edit only the condition's threshold in the same foreground editor.
    $thresholdDraft = $annotationDraft.Replace('"above":0.8', '"above":0.85')
    (Draft-Pattern).SetValue($thresholdDraft)
    Invoke-Button 'Preview draft'
    Wait-Name 'Draft preview'
    Wait-Annotation $fifteen $five.value
    Invoke-Button 'Accept view'
    Wait-Name 'Accepted view'
    Wait-Annotation $fifteen $five.value
    Wait-ForProbe 'accepted threshold-only author definition' {
        (Draft-Pattern).Current.Value -ceq $thresholdDraft
    } | Out-Null
    Invoke-Button 'Back to chart'
    Wait-Name 'Open workbench prototype'
    Wait-ForProbe 'closed annotation editor' { -not (Find-Control 'Composition JSON' $null) } | Out-Null

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
        fixture = $hash
        measurement = 'UI Automation invocation to accessible result, including polling'
        compose_ms = $composeMs; edit_ms = $editMs; recovery_ms = $recoverMs
        annotation_scope = 'UI Automation UTC attribution rows and editor recovery in WebView2'
        annotation_fixture_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath 'tests/fixtures/workbench-qualification-v1.json').Hash.ToLowerInvariant()
        annotation_round_trip = @($five, $fifteen, $five)
        annotation_rejected_draft_restored = $true
        annotation_restored_after_empty_preview = $fifteen
        annotation_accepted_threshold_edit = @{ above = 0.85; result = $fifteen }
    } | ConvertTo-Json -Depth 4 -Compress | Write-Output
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
