# Invoke the controls of a synthetic workspace in its own Windows process and profile.
param([Parameter(Mandatory=$true)][string]$OutputDirectory,$Identity)
Add-Type -AssemblyName System.Windows.Forms
$ui = [Windows.Automation.AutomationElement]
$scope = [Windows.Automation.TreeScope]::Descendants
$any = [Windows.Automation.Condition]::TrueCondition
$type = [Windows.Automation.ControlType]
$invoke = [Windows.Automation.InvokePattern]::Pattern
$value = [Windows.Automation.ValuePattern]::Pattern

function Named-Control([string]$label, $kind) {
    $condition = [Windows.Automation.PropertyCondition]::new($ui::ControlTypeProperty,$kind)
    foreach ($item in $window.FindAll($scope,$condition)) {
        if (($item.Current.Name -eq $label -or ([string]$item.Current.Name).StartsWith("$label ")) -and $item.Current.IsEnabled) {
            return $item
        }
    }
}
function Select-NativeOption([string]$label, [int]$index) {
    $select = Wait-ForProbe $label { Named-Control $label $type::ComboBox }
    $select.SetFocus()
    [Windows.Forms.SendKeys]::SendWait('{HOME}')
    if ($index -gt 0) { [Windows.Forms.SendKeys]::SendWait("{DOWN $index}") }
}
function Show-NativeState([string]$stage) {
    $visible=@($window.FindAll($scope,$any) | ForEach-Object {
        if (!$_.Current.IsOffscreen -and $_.Current.Name) { [string]$_.Current.Name }
    })
    Write-Host "Owned workspace at ${stage}: $($visible -join ' | ')"
    Archive-Stage $stage
}
function Select-SeriesChoice([string]$label,[string]$wanted) {
    $combo=Wait-ForProbe "$label in the native studio" { Named-Control $label $type::ComboBox }
    $combo.SetFocus()
    $selection=[Windows.Automation.SelectionPattern]::Pattern
    $before=$null
    if ($combo.TryGetCurrentPattern($selection,[ref]$before)) {
        Write-Host "$label initial selection: $(@($before.Current.GetSelection() | ForEach-Object {$_.Current.Name}) -join ', ')"
    }
    [Windows.Forms.SendKeys]::SendWait('%{DOWN}')
    $item=& {
        $c=Named-Control $label $type::ComboBox
        if (!$c) { return }
        foreach ($i in $c.FindAll($scope,$any)) {
            $name=[string]$i.Current.Name
            if ($i.Current.IsEnabled -and ($name -eq $wanted -or ($wanted -eq 'accepted UUID' -and $name -match '^[a-f0-9-]{36}$'))) { return $i }
        }
        # The expanded select popup can be a sibling of the combo in WebView2.
        foreach ($i in $window.FindAll($scope,[Windows.Automation.PropertyCondition]::new($ui::ControlTypeProperty,$type::ListItem))) {
            $name=[string]$i.Current.Name
            if (!$i.Current.IsOffscreen -and $i.Current.IsEnabled -and ($name -eq $wanted -or ($wanted -eq 'accepted UUID' -and $name -match '^[a-f0-9-]{36}$'))) { return $i }
        }
    }
    $exact=if ($item) { [string]$item.Current.Name } else { $wanted }
    Write-Host "$label requested $wanted; exposed option: $([string]$item.Current.Name)."
    if ($exact -eq 'accepted UUID') {
        [Windows.Forms.SendKeys]::SendWait('{HOME}{DOWN}{ENTER}')
        Wait-Name 'Numeric series'
        return
    }
    # Use the verified item's type-ahead name in the open native select.
    [Windows.Forms.SendKeys]::SendWait($exact.Substring(0,[Math]::Min(8,$exact.Length))+'{ENTER}')
    $null=Wait-ForProbe "$label shows $exact as the current selection" {
        $c=Named-Control $label $type::ComboBox
        if (!$c) { return }
        $v=$null
        if ($c.TryGetCurrentPattern([Windows.Automation.ValuePattern]::Pattern,[ref]$v) -and $v.Current.Value -eq $exact) { return $true }
        $sel=$null
        if ($c.TryGetCurrentPattern($selection,[ref]$sel)) {
            if (@($sel.Current.GetSelection() | ForEach-Object {$_.Current.Name}) -contains $exact) { return $true }
        }
        if (([string]$c.Current.Name).Contains($exact)) { return $true }
        # Subsequent native source disclosure independently checks the full selected identity.
        if ($label -eq 'Accepted version' -and (Named-Control 'Numeric series' $type::ComboBox)) { return $true }
        return $label -eq 'Numeric series' -and (Find-Control 'Accepted series source' $null)
    }
    Write-Host "$label advanced for $exact; full identity is checked in the studio source disclosure."
}
function Open-PreparedSource {
    $null=Wait-ForProbe 'completed first workspace source restoration' {
        (Find-Control 'Candlestick and volume chart for FIX' $type::Image) -or
        (Find-Control 'Retry source' $type::Button) -or (Find-Control 'Reload workspace state' $type::Button) -or
        (Find-Control 'Access to this published CSV was revoked.' $null)
    }
    Show-NativeState 'workspace-initial'
    if (Find-Control 'Candlestick and volume chart for FIX' $type::Image) { return }
    if (Named-Control 'Reload workspace state' $type::Button) { Review-Step 'Reload workspace state' }
    if (!(Named-Control 'Open published CSV' $type::Button)) { Review-Step 'Data sources' }
    Review-Step 'Open published CSV'
    $row=Wait-ForProbe 'published CSV row matching the confirmed revision and fixture' {
        $entry=Named-Control 'Open series.csv' $type::Button
        if ($entry -and ([string]$entry.Current.Name).Contains($Identity.revision.Substring(0,8))) { return $entry }
    }
    $null=Wait-ForProbe 'open this published CSV revision' { Try-Activate $row }
    Wait-Name 'Use as workspace source'
    Show-NativeState 'published-source'
    Review-Step 'Use as workspace source'
    Wait-Name 'Candlestick and volume chart for FIX'
    Show-NativeState 'workspace-bound'
}
function Open-SeriesStudio {
    Review-Step 'Open blank AI workbench'
    Review-Step 'Drawing studio'
    $wanted=if ($Identity) { $Identity.version_id } else { 'accepted UUID' }
    Select-SeriesChoice 'Accepted version' $wanted
    Select-SeriesChoice 'Numeric series' 'mean'
}
function Fill-NativeEdit([string]$label, [string]$text) {
    $edit = Wait-ForProbe $label { Named-Control $label $type::Edit }
    $edit.GetCurrentPattern($value).SetValue($text)
}
function Try-Activate($control) {
    if (!$control -or !$control.Current.IsEnabled) { return $false }
    $pattern=$null
    if ($control.TryGetCurrentPattern($invoke,[ref]$pattern)) {
        $pattern.Invoke()
        return $true
    }
    $bounds=$control.Current.BoundingRectangle
    if ($bounds.IsEmpty -or $bounds.Width -le 0 -or $bounds.Height -le 0) { return $false }
    $x=[int]($bounds.X+$bounds.Width/2); $y=[int]($bounds.Y+$bounds.Height/2)
    $atPoint=[Windows.Automation.AutomationElement]::FromPoint([Windows.Point]::new($x,$y))
    if (!$atPoint -or ([string]$atPoint.Current.Name) -ne ([string]$control.Current.Name)) { return $false }
    [DrawingWindowCapture]::ClickOwned($ownedHandle,$x,$y)
    return $true
}
function Click-WorkspaceControl([string]$label,[switch]$Indicator) {
    $resolve={ if ($Indicator) { Indicator-Control $label } else { Named-Control $label $type::Button } }
    $button=Wait-ForProbe "enabled $label" { $b=& $resolve; if ($b -and $b.Current.IsEnabled) { $b } }
    $scroller=$null
    if ($button.TryGetCurrentPattern([Windows.Automation.ScrollItemPattern]::Pattern,[ref]$scroller)) { $scroller.ScrollIntoView() }
    $button.SetFocus()
    $null=Wait-ForProbe "visible or keyboard-focused owned $label button" {
        $b=& $resolve
        if (!$b -or !$b.Current.IsEnabled) { return $false }
        $r=$b.Current.BoundingRectangle
        if ($r.IsEmpty -or $r.Width -lt 1 -or $r.Height -lt 1) { return $false }
        $x=[int]($r.X+$r.Width/2); $y=[int]($r.Y+$r.Height/2)
        $hit=$ui::FromPoint([Windows.Point]::new($x,$y))
        $walker=[Windows.Automation.TreeWalker]::ControlViewWalker
        for ($i=0; $i -lt 8 -and $hit; $i++) {
            if ([Windows.Automation.Automation]::Compare($b,$hit)) {
                [DrawingWindowCapture]::ClickOwned($ownedHandle,$x,$y)
                Write-Host "Verified owned pointer activation of $label at ($x,$y)."
                return $true
            }
            $hit=$walker.GetParent($hit)
        }
        $focused=$ui::FocusedElement
        if ($focused -and [Windows.Automation.Automation]::Compare($focused,$b) -and $b.Current.HasKeyboardFocus) {
            [Windows.Forms.SendKeys]::SendWait(' ')
            Write-Host "Activated the verified focused $label button with Space."
            return $true
        }
        return $false
    }
}
function Review-Step([string]$label) {
    Write-Output "Native workspace control: $label"
    $null=Wait-ForProbe $label { Try-Activate (Named-Control $label $type::Button) }
}
function Archive-Stage([string]$name) {
    $image=[DrawingWindowCapture]::OwnedClient($ownedHandle)
    try { $image.Save((Join-Path $OutputDirectory "$name.png"),[Drawing.Imaging.ImageFormat]::Png) }
    finally { $image.Dispose() }
}
function Wait-Text([string]$text) {
    Wait-ForProbe $text {
        foreach ($n in $window.FindAll($scope,$any)) {
            if (([string]$n.Current.Name).Contains($text)) { return $n }
        }
    }
}
function Enter-NativeFolder([string]$folder) {
    Review-Step 'Choose local CSV folder'
    $dlg=Wait-ForProbe 'folder picker owned by the ScopeHub window' {
        Find-Control 'Choose local CSV folder' $type::Window
    }
    $pickerHandle=[IntPtr]$dlg.Current.NativeWindowHandle
    [DrawingWindowCapture]::VerifyPicker($ownedHandle,$pickerHandle,$app.Id)
    Write-Output "Confirmed ScopeHub picker: HWND=$pickerHandle class=$($dlg.Current.ClassName) appPID=$($app.Id)"
    # Prefer the common dialog's filename edit. The shell exposes some children as UIA Pane controls.
    if ([DrawingWindowCapture]::SetPickerFolder($pickerHandle,$folder)) {
        Write-Output 'Entered the synthetic folder through the owned dialog edit.'
    } else {
        [DrawingWindowCapture]::FocusPicker($pickerHandle)
        [Windows.Forms.SendKeys]::SendWait('^l')
        [Windows.Forms.Clipboard]::SetText($folder)
        try { [Windows.Forms.SendKeys]::SendWait('^v{ENTER}') }
        finally { [Windows.Forms.Clipboard]::Clear() }
    }
    $name=[Windows.Automation.PropertyCondition]::new($ui::NameProperty,'Select Folder')
    $submit=Wait-ForProbe 'Select Folder in the owned shell picker' { $dlg.FindFirst($scope,$name) }
    Start-Sleep -Milliseconds 300
    $pattern=$null
    if ($submit.TryGetCurrentPattern($invoke,[ref]$pattern)) { $pattern.Invoke() }
    elseif (![DrawingWindowCapture]::PressPickerOk($pickerHandle)) {
        [DrawingWindowCapture]::FocusPicker($pickerHandle)
        $bounds=$submit.Current.BoundingRectangle
        if ($bounds.IsEmpty) { throw 'Owned Select Folder control has no bounds.' }
        [DrawingWindowCapture]::ClickPickerControl($pickerHandle, $bounds.Left+$bounds.Width/2, $bounds.Top+$bounds.Height/2)
    }
    Wait-Name 'Inspect raw rows'
}

function Indicator-Control([string]$label) {
    $region=Find-Control 'Indicator preview' $type::Group
    if (!$region) { return $null }
    $name=[Windows.Automation.PropertyCondition]::new($ui::NameProperty,$label)
    $region.FindFirst($scope,$name)
}
function Run-IndicatorPreview {
    $runButton=Wait-ForProbe 'enabled Run preview inside Indicator preview' {
        $b=Indicator-Control 'Run preview'
        if ($b -and $b.Current.IsEnabled) { return $b }
    }
    $runButton.SetFocus()
    $null=Wait-ForProbe 'owned indicator control at its visible position' {
        $b=Indicator-Control 'Run preview'
        if (!$b -or !$b.Current.IsEnabled) { return $false }
        $rect=$b.Current.BoundingRectangle
        if ($rect.IsEmpty) { return $false }
        $x=[int]($rect.X+$rect.Width/2); $y=[int]($rect.Y+$rect.Height/2)
        $target=$ui::FromPoint([Windows.Point]::new($x,$y))
        if (([string]$target.Current.Name) -ne 'Run preview') { return $false }
        [DrawingWindowCapture]::ClickOwned($ownedHandle,$x,$y)
        return $true
    }
    $transition=Wait-ForProbe 'indicator computation started or its result displayed' {
        foreach ($label in @('Cancel preview','Clear preview','Accept preview','Indicator preview values')) {
            if (Indicator-Control $label) { return $label }
        }
    }
    Write-Output "Indicator-specific transition after native click: $transition"
    Archive-Stage 'indicator-start'
    foreach ($label in @('Accept preview','Confirm indicator acceptance')) {
        Click-WorkspaceControl $label -Indicator
        if ($label -eq 'Accept preview') { Wait-Name 'Confirm accepted indicator' }
    }
}
function Open-Disclosure([string]$label) {
    $control=Wait-ForProbe $label { Find-Control $label $null }
    $expand=$null
    if ($control.TryGetCurrentPattern([Windows.Automation.ExpandCollapsePattern]::Pattern,[ref]$expand)) { $expand.Expand() }
    elseif (!(Try-Activate $control)) { $control.SetFocus(); [Windows.Forms.SendKeys]::SendWait(' ') }
}
function Read-AcceptedHash {
    Wait-ForProbe 'accepted output digest next to Output' {
        $labels=@($window.FindAll($scope,$any) | ForEach-Object { $_.Current.Name } | Where-Object { $_ })
        $match=[regex]::Match(($labels -join ' '),'(?i)\bOutput\s+(?:Output\s+)?([a-f0-9]{64})(?:\b|$)')
        if ($match.Success) { return $match.Groups[1].Value.ToLowerInvariant() }
    }
}
function Native-Plot {
    $svg=Wait-ForProbe 'Axis-free drawing' {
        foreach ($n in $window.FindAll($scope,$any)) {
            $bounds=$n.Current.BoundingRectangle
            if ($n.Current.Name -eq 'Axis-free drawing' -and $bounds.Width -gt 300 -and
                [Math]::Abs($bounds.Height - $bounds.Width * 240 / 640) -le 3) { return $n }
        }
    }
    $b=$svg.Current.BoundingRectangle
    return [DrawingWindowCapture]::Plot($ownedHandle,$b.Left,$b.Top,$b.Width,$b.Height)
}
function Save-SeriesPlot([string]$kind) {
    $plot=Native-Plot
    try {
        $plot.Save((Join-Path $OutputDirectory "$kind.png"),[Drawing.Imaging.ImageFormat]::Png)
        $scale=$plot.Width / 640
        if ([Math]::Abs($plot.Height - 240*$scale) -gt 3) { throw 'Plot bounds have the wrong aspect ratio.' }
        $rings=$kind -eq 'dots'
        $measurements=@()
        $expected=@(32,124,216)
        for ($i=0;$i -lt 3;$i++) {
            if ($rings) {
                $y=208 - 92*$i
                $ink=[Drawing.Color]::FromArgb(96,212,191)
                $x=248 + 184*$i
            } else {
                $y=68 + 52*$i + 3
                $ink=[Drawing.Color]::FromArgb(232,175,114)
                $x=64
            }
            $m=[DrawingWindowCapture]::InkExtent($plot,$y,$ink,8)
            $measurements+=,@($m)
            if ($m[2] -lt 10) { throw "$kind lacks visible ink at row $i." }
            if ($rings) {
                if ([Math]::Abs(($m[0]+$m[1])/2 - $x*$scale) -gt 3) { throw "Wrong ring center at row $i." }
            } elseif ([Math]::Abs(($m[1]-$m[0]+1) - ($expected[$i]+3)*$scale) -gt 4) {
                throw "Wrong strip width at row $i."
            }
        }
        if (!$rings) {
            $missing=[DrawingWindowCapture]::InkExtent($plot,16,[Drawing.Color]::FromArgb(168,164,252),5)
            if ($missing[2] -lt 5 -or [Math]::Abs(($missing[0]+$missing[1])/2-68*$scale) -gt 3) { throw 'Missing first observation dash.' }
        }
        Write-Output (@{encoding=$kind; width=$plot.Width; height=$plot.Height; ink=$measurements} | ConvertTo-Json -Depth 4 -Compress)
    } finally { $plot.Dispose() }
}
function Use-SeriesProgram([string]$kind) {
    $path=Join-Path $PSScriptRoot "../fixtures/workbench/accepted-series-$kind.js"
    $source=[IO.File]::ReadAllText($path).Replace("`r`n","`n")
    Fill-NativeEdit 'Program source' $source
    Review-Step 'Preview draft'
    Review-Step 'Accept view'
    Wait-Name 'Accepted view'
    Archive-Stage ("painted-"+$kind)
    Save-SeriesPlot $kind
    $day=if ($kind -eq 'dots') { '04' } else { '03' }
    $target="Inspect observation at 2026-02-${day}T00:00:00.000Z"
    $inspect=Wait-ForProbe $target { Find-Control $target $null }
    $inspect.SetFocus()
    [Windows.Forms.SendKeys]::SendWait('{ENTER}')
    $expected=if ($kind -eq 'dots') { '115' } else { 'Unavailable' }
    $null=Wait-ForProbe "chosen $day UTC point and $expected" {
        $readout=Find-Control 'Accepted observation' $null
        if (!$readout) { return $false }
        $texts=@($readout.Current.Name)+@($readout.FindAll($scope,$any) | ForEach-Object { [string]$_.Current.Name })
        $expectedText="2026-02-${day}T00:00:00.000Z $([char]0xb7) $expected $([char]0xb7) Unknown"
        return (($texts -join ' ') -replace '\s+',' ').Contains($expectedText)
    }
    Archive-Stage ("selected-"+$kind)
}
function Save-VisibilityPlot([string]$state) {
    $image=Native-Plot
    try {
        $scale=$image.Width / 640
        $m=[DrawingWindowCapture]::InkExtent($image,73,[Drawing.Color]::FromArgb(96,212,191),14)
        if ([Math]::Abs(($m[0]+$m[1])/2 - 108*$scale) -gt 3 -or
            [Math]::Abs(($m[1]-$m[0]+1)-123*$scale) -gt 4) { throw "Unexpected visible capability mark in $state." }
        $path=Join-Path $OutputDirectory "visibility-$state.png"
        $image.Save($path,[Drawing.Imaging.ImageFormat]::Png)
        $signature=[DrawingWindowCapture]::VisibilityContent($image)
        return @{full=(Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash;content=$signature;size="$($image.Width)x$($image.Height)"}
    } finally { $image.Dispose() }
}
function Inspect-Visibility {
    Review-Step 'Open blank AI workbench'
    Review-Step 'Drawing studio'
    $file=Join-Path $PSScriptRoot '../fixtures/workbench/guest-public-surface.js'
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $file).Hash -ne '4cc37306be26e5f30ec2bcfb7c3eb65b91f8735e4483d297c17c937f88311dc4') { throw 'Visibility fixture identity changed.' }
    $source=[IO.File]::ReadAllText($file)
    Fill-NativeEdit 'Program source' $source
    Review-Step 'Preview draft'
    Review-Step 'Accept view'
    Wait-Name 'Accepted view'
    $before=Save-VisibilityPlot 'accepted'
    Fill-NativeEdit 'Program source' "// future free`nwhile (true) {}"
    $timer=[Diagnostics.Stopwatch]::StartNew()
    Review-Step 'Preview draft'
    Wait-Name 'The drawing exceeded its time limit.'
    $elapsed=$timer.ElapsedMilliseconds
    $failed=Save-VisibilityPlot 'timeout'
    Review-Step 'Restore accepted view'
    Wait-Name 'Accepted view'
    $restored=Save-VisibilityPlot 'restored'
    if ($before.full -ne $restored.full -or $before.content -ne $failed.content -or $before.content -ne $restored.content) { throw 'Accepted capability mark pixels changed during timeout or recovery.' }
    $editor=Named-Control 'Program source' $type::Edit
    if ($editor.GetCurrentPattern($value).Current.Value.Trim().Replace("`r`n","`n") -cne $source.Trim()) { throw 'Earlier accepted guest source was not restored.' }
    Write-Output "Visibility: exact accepted, timed out and restored native plot $($before.full); comparison interior $($before.content); bounds $($before.size), $($failed.size), $($restored.size); input to visible timeout message (UIA polling) $elapsed ms."
    Review-Step 'Back to chart'
}
function Measure-SampleControl([string]$label,[int]$index) {
    $samples=@()
    foreach ($direction in @(1,-1,1,-1)) {
        $slider=Wait-ForProbe $label { Named-Control $label $type::Slider }
        $slider.SetFocus()
        $image=Native-Plot
        try {
            $metric=[DrawingWindowCapture]::InkExtent($image,146.67,[Drawing.Color]::FromArgb(96,212,191),20)
            $baseline=if ($index -eq 2) { $metric[2] } else { $metric[1]-$metric[0] }
            if ($metric[2] -lt 12) { throw 'No baseline rim in the native capture.' }
        } finally { $image.Dispose() }
        $timer=[Diagnostics.Stopwatch]::StartNew()
        $key=if ($direction -gt 0) { '{RIGHT}' } else { '{LEFT}' }
        [Windows.Forms.SendKeys]::SendWait($key)
        do {
            $image=Native-Plot
            try { $m=[DrawingWindowCapture]::InkExtent($image,146.67,[Drawing.Color]::FromArgb(96,212,191),20) }
            finally { $image.Dispose() }
            $current=if ($index -eq 2) { $m[2] } else { $m[1]-$m[0] }
            if (($current-$baseline)*$direction -ge 1 -and $m[2] -gt 12) { break }
            if ($timer.ElapsedMilliseconds -gt 3500) { throw "No visible change from $label." }
            Start-Sleep -Milliseconds 5
        } while ($true)
        $samples+=$timer.ElapsedMilliseconds
    }
    Write-Output (@{control=$label;samples_ms=$samples;method='SendKeys to first changed owned PrintWindow rim samples, including capture and UI Automation lookup'} | ConvertTo-Json -Compress)
}
function Inspect-SampleControls {
    Review-Step 'Open blank AI workbench'
    Review-Step 'Drawing studio'
    $source=[IO.File]::ReadAllText((Join-Path $PSScriptRoot '../fixtures/workbench/adjustable-targets.js'))
    Fill-NativeEdit 'Program source' $source
    Review-Step 'Preview draft'
    Review-Step 'Accept view'
    Wait-Name 'Accepted view'
    $target=Wait-ForProbe 'first frozen sample point' {
        $window.FindAll($scope,$any) | Where-Object { ([string]$_.Current.Name).StartsWith('Inspect observation at ') } | Select-Object -First 1
    }
    $target.SetFocus()
    [Windows.Forms.SendKeys]::SendWait('{ENTER}')
    $null=Wait-Text '3,499.2 CNY'
    Measure-SampleControl 'Stroke weight' 2
    Measure-SampleControl 'rimSize' 1
    Review-Step 'Back to chart'
}
$fixture=Join-Path $temp 'series-fixture'
New-Item -ItemType Directory -Path $fixture | Out-Null
$bytes=[Text.Encoding]::ASCII.GetBytes((@(
    'Date,Symbol,Open,High,Low,Close,Volume',
    '2026-02-03,FIX,10,11,9,10,110',
    '2026-02-04,FIX,14,15,13,14,120',
    '2026-02-05,FIX,18,19,17,18,130',
    '2026-02-06,FIX,16,17,15,16,140'
) -join "`n") + "`n")
$file=Join-Path $fixture 'series.csv'
[IO.File]::WriteAllBytes($file,$bytes)
if ((Get-FileHash $file).Hash -ne 'c3661d5dfc5a6d97269a4e4de6550350bab5d79e6d3268500425c1b75f80aa00') { throw 'Synthetic volume CSV changed.' }
try {
    Add-Type -AssemblyName System.Drawing
    Add-Type -Path (Join-Path $PSScriptRoot 'DrawingWindowCapture.cs') -ReferencedAssemblies System.Drawing
    $null=[DrawingWindowCapture]::SetProcessDPIAware()
    [DrawingWindowCapture]::Resize($ownedHandle,1120,760)
    $visibilityFailure=$null
    if ($Identity) {
        $acceptedHash=$Identity.result_hash
        Write-Output "Started real native workspace on previously confirmed version $($Identity.version_id)."
        Archive-Stage 'native-entry'
        Open-PreparedSource
    } else {
        try { Inspect-Visibility } catch {
            $visibilityFailure=$_
            Write-Output "Visibility subcheck failed: $($_.Exception.Message)"
            if (Named-Control 'Back to chart' $type::Button) { Review-Step 'Back to chart' }
        }
        Enter-NativeFolder $fixture
        Select-NativeOption 'Record kind' 1
        Review-Step 'Inspect raw rows'
        $null=Wait-Text 'Raw CSV rows'
        $null=Wait-Text 'series.csv'
        Archive-Stage 'raw'
        $columns=[ordered]@{'Symbol column'=2; 'Timestamp column'=1; 'Trading day column'=1; 'Open column'=3
            'High column'=4; 'Low column'=5; 'Close column'=6; 'Volume column'=8
            'Instrument type'=1; 'Volume basis'=1; 'Timestamp convention'=1}
        foreach ($field in $columns.Keys) { Select-NativeOption $field $columns[$field] }
        $fields=[ordered]@{Venue='FIXTURE'; Timeframe='1d'; 'Calendar ID'='fixture-calendar'; 'Session scope'='regular'
            Timezone='UTC'; 'Quantity unit'='shares'; 'Price adjustment'='raw'; 'Timestamp format'='%Y-%m-%d'
            'Trading day format'='%Y-%m-%d'; 'Daily start time'='00:00:00'}
        foreach ($field in $fields.Keys) { Fill-NativeEdit $field $fields[$field] }
        Review-Step 'Preview mapping'
        $mapping=Wait-ForProbe 'reviewable mapping or the timestamp convention diagnostic' {
            if (Named-Control 'Save reviewed mapping' $type::Button) { return 'ready' }
            foreach ($e in $window.FindAll($scope,$any)) {
                if (([string]$e.Current.Name).Contains('Select a timestamp that identifies the bar start')) { return 'timestamp' }
            }
        }
        if ($mapping -eq 'timestamp') {
            Select-SeriesChoice 'Timestamp convention' 'Bar start'
            Review-Step 'Preview mapping'
        }
        foreach ($label in @('Save reviewed mapping','Publish complete file','Open frozen chart')) { Click-WorkspaceControl $label }
        Archive-Stage 'published'
        Select-NativeOption 'Indicator input' 1
        $period=Wait-ForProbe 'Rolling period' {
            $control=Named-Control 'Rolling period' $type::Spinner
            if ($control) { return $control }
            Named-Control 'Rolling period' $type::Edit
        }
        $period.SetFocus()
        [Windows.Forms.SendKeys]::SendWait('^a2{TAB}')
        Run-IndicatorPreview
        Open-Disclosure 'Accepted provenance and values'
        $null=Wait-Text 'Unknown, 115, 125, 135'
        Archive-Stage 'confirmed'
        $acceptedHash=Read-AcceptedHash
        Write-Output "Accepted two-period volume mean: Unknown, 115, 125, 135; result SHA256=$acceptedHash"
        Review-Step 'Open published CSV'
        Click-WorkspaceControl 'Open series.csv'
        Review-Step 'Use as workspace source'
    }
    try { Open-SeriesStudio } catch {
        $bindingError=$_.Exception.Message
        Show-NativeState 'binding-interrupted'
        if (!(Named-Control 'Back to chart' $type::Button)) { throw }
        Review-Step 'Back to chart'
        Show-NativeState 'workspace-after-binding'
        if (Named-Control 'Reload workspace state' $type::Button) { Review-Step 'Reload workspace state' }
        elseif (Named-Control 'Retry source' $type::Button) { Review-Step 'Retry source' }
        if ($Identity) { Open-PreparedSource }
        Wait-Name 'Candlestick and volume chart for FIX'
        Show-NativeState 'workspace-reopened'
        Write-Host "Retrying native selection after reopening the frozen chart: $bindingError"
        Open-SeriesStudio
    }
    $observations=@()
    try {
        Open-Disclosure 'Accepted series source'
        if ((Read-AcceptedHash) -ne $acceptedHash) { throw 'Studio and confirmed indicator have different result hashes.' }
        if ($Identity) {
            foreach ($required in @($Identity.version_id,$Identity.revision,$Identity.layer_id)) { $null=Wait-Text $required }
        }
        Open-Disclosure 'Accepted observations'
        foreach ($day in 3..6) { $null=Wait-Text ('2026-02-{0:D2}T00:00:00.000Z' -f $day) }
        foreach ($number in @('Unavailable','115','125','135')) { Wait-Name $number }
        Archive-Stage 'selected-series'
    } catch {
        $observations+="Source metadata: $($_.Exception.Message)"
        Write-Output $observations[-1]
        Archive-Stage 'metadata-observation'
    }
    $sourcePanel=Find-Control 'Accepted series source' $null
    if ($sourcePanel) {
        $expand=$null
        if ($sourcePanel.TryGetCurrentPattern([Windows.Automation.ExpandCollapsePattern]::Pattern,[ref]$expand) -and
            $expand.Current.ExpandCollapseState -eq [Windows.Automation.ExpandCollapseState]::Expanded) { $expand.Collapse() }
    }
    foreach ($encoding in @('dots','strips')) {
        try { Use-SeriesProgram $encoding } catch {
            $observations+="$encoding observation: $($_.Exception.Message)"
            Write-Output $observations[-1]
            Archive-Stage ("diagnostic-"+$encoding)
        }
    }
    (Named-Control 'Numeric series' $type::ComboBox).SetFocus()
    [Windows.Forms.SendKeys]::SendWait('{END}')
    Wait-Name 'Open a drawing program for the selected series.'
    if ((Find-Control 'Accepted observation' $null) -or (Find-Control 'Undo accepted edit' $null) -or
        (Find-Control 'Inspect observation at 2026-02-03T00:00:00.000Z' $null)) { throw 'Previous state survived the series change.' }
    Archive-Stage 'switched'
    Review-Step 'Back to chart'
    Review-Step 'Data sources'
    Review-Step 'Open published CSV'
    Click-WorkspaceControl 'Open series.csv'
    Wait-Name 'Use as workspace source'
    Archive-Stage 'before-revoke'
    Click-WorkspaceControl 'Remove frozen chart access'
    Click-WorkspaceControl 'Remove access'
    $null=Wait-Text 'Access removed for this published revision.'
    Archive-Stage 'revoked'
    if (!$observations.Count) { Write-Output "Windows renderer verified: result $acceptedHash; UTC missing point, both authored shapes, source switch and native revocation." }
    Write-Output 'The private source was removed through the native published-revision library.'
    Review-Step 'Close'
    if (!$Identity) { Inspect-SampleControls }
    if ($visibilityFailure) { throw $visibilityFailure }
    if ($observations.Count) { throw ($observations -join ' / ') }
} catch {
    # The common dialog can briefly overlay the app. Only capture product content after it has closed.
    if (!(Find-Control 'Choose local CSV folder' $type::Window)) {
        try { Archive-Stage 'failure' } catch { Write-Output 'No owned client image available.' }
    }
    $controls=@($window.FindAll($scope,$any) | Where-Object { $_.Current.Name } |
        ForEach-Object { "$($_.Current.ControlType.ProgrammaticName) $($_.Current.Name)" })
    Write-Output ($controls -join "`n")
    throw
}
