# Invoke the controls of a synthetic workspace in its own Windows process and profile.
param([Parameter(Mandatory=$true)][string]$OutputDirectory)
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
        if (($item.Current.Name -eq $label -or $item.Current.Name.StartsWith("$label ")) -and $item.Current.IsEnabled) {
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
function Fill-NativeEdit([string]$label, [string]$text) {
    $edit = Wait-ForProbe $label { Named-Control $label $type::Edit }
    $edit.GetCurrentPattern($value).SetValue($text)
}
function Review-Step([string]$label) {
    Write-Output "Native workspace control: $label"
    $control = Wait-ForProbe $label { Named-Control $label $type::Button }
    $control.GetCurrentPattern($invoke).Invoke()
}
function Wait-Text([string]$text) {
    Wait-ForProbe $text {
        foreach ($n in $window.FindAll($scope,$any)) {
            if ($n.Current.Name.Contains($text)) { return $n }
        }
    }
}
function Enter-NativeFolder([string]$folder) {
    Review-Step 'Choose local CSV folder'
    $dlg=Wait-ForProbe 'folder picker owned by the ScopeHub window' {
        Find-Control 'Choose local CSV folder' $type::Window
    }
    $pickerHandle=[IntPtr]$dlg.Current.NativeWindowHandle
    [DrawingWindowCapture]::VerifyPicker($app.MainWindowHandle,$pickerHandle,$app.Id)
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

function Open-Disclosure([string]$label) {
    $control=Wait-ForProbe $label { Find-Control $label $null }
    $expand=$null
    if ($control.TryGetCurrentPattern([Windows.Automation.ExpandCollapsePattern]::Pattern,[ref]$expand)) { $expand.Expand() }
    else { $control.SetFocus(); [Windows.Forms.SendKeys]::SendWait(' ') }
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
    return [DrawingWindowCapture]::Plot($app.MainWindowHandle,$b.Left,$b.Top,$b.Width,$b.Height)
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
    $day=if ($kind -eq 'dots') { '04' } else { '03' }
    $target="Inspect observation at 2026-02-${day}T00:00:00.000Z"
    $inspect=Wait-ForProbe $target { Find-Control $target $null }
    $inspect.SetFocus()
    [Windows.Forms.SendKeys]::SendWait('{ENTER}')
    $expected=if ($kind -eq 'dots') { '115' } else { 'Unavailable' }
    $null=Wait-Text ("2026-02-${day}T00:00:00.000Z $([char]0xb7) $expected $([char]0xb7) Unknown")
    Save-SeriesPlot $kind
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
        return (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash
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
    if ($before -ne $failed -or $before -ne $restored) { throw 'Accepted capability mark pixels changed during timeout or recovery.' }
    $editor=Named-Control 'Program source' $type::Edit
    if ($editor.GetCurrentPattern($value).Current.Value.Trim().Replace("`r`n","`n") -cne $source.Trim()) { throw 'Earlier accepted guest source was not restored.' }
    Write-Output "Visibility: exact accepted, timed out and restored native plot $before; input to visible timeout message (UIA polling) $elapsed ms."
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
        $window.FindAll($scope,$any) | Where-Object { $_.Current.Name.StartsWith('Inspect observation at ') } | Select-Object -First 1
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
    [DrawingWindowCapture]::Resize($app.MainWindowHandle,1120,760)
    Inspect-Visibility
    Enter-NativeFolder $fixture
    Select-NativeOption 'Record kind' 1
    Review-Step 'Inspect raw rows'
    $null=Wait-Text 'Raw CSV rows'
    $null=Wait-Text 'series.csv'
    $columns=[ordered]@{'Symbol column'=2; 'Timestamp column'=1; 'Trading day column'=1; 'Open column'=3
        'High column'=4; 'Low column'=5; 'Close column'=6; 'Volume column'=8
        'Instrument type'=1; 'Volume basis'=1; 'Timestamp convention'=1}
    foreach ($field in $columns.Keys) { Select-NativeOption $field $columns[$field] }
    $fields=[ordered]@{Venue='FIXTURE'; Timeframe='1d'; 'Calendar ID'='fixture-calendar'; 'Session scope'='regular'
        Timezone='UTC'; 'Quantity unit'='shares'; 'Price adjustment'='raw'; 'Timestamp format'='%Y-%m-%d'
        'Trading day format'='%Y-%m-%d'; 'Daily start time'='00:00:00'}
    foreach ($field in $fields.Keys) { Fill-NativeEdit $field $fields[$field] }
    foreach ($label in @('Preview mapping','Save reviewed mapping','Publish complete file','Open frozen chart')) { Review-Step $label }
    Select-NativeOption 'Indicator input' 1
    $period=Wait-ForProbe 'Rolling period' {
        $control=Named-Control 'Rolling period' $type::Spinner
        if ($control) { return $control }
        Named-Control 'Rolling period' $type::Edit
    }
    $period.SetFocus()
    [Windows.Forms.SendKeys]::SendWait('^a2{TAB}')
    foreach ($label in @('Run preview','Accept preview','Confirm indicator acceptance')) { Review-Step $label }
    Open-Disclosure 'Accepted provenance and values'
    $null=Wait-Text 'Unknown, 115, 125, 135'
    Write-Output 'Observed the accepted two-period volume mean: Unknown, 115, 125, 135.'
    Review-Step 'Open published CSV'
    Review-Step 'Open series.csv'
    Review-Step 'Use as workspace source'
    Review-Step 'Open blank AI workbench'
    Review-Step 'Drawing studio'
    Select-NativeOption 'Accepted version' 1
    Select-NativeOption 'Numeric series' 1
    Open-Disclosure 'Accepted series source'
    Open-Disclosure 'Accepted observations'
    foreach ($day in 3..6) { $null=Wait-Text ('2026-02-{0:D2}T00:00:00.000Z' -f $day) }
    foreach ($number in @('Unavailable','115','125','135')) { Wait-Name $number }
    $sourcePanel=Wait-ForProbe 'source disclosure' { Find-Control 'Accepted series source' $null }
    $sourcePanel.SetFocus(); [Windows.Forms.SendKeys]::SendWait(' ')
    Use-SeriesProgram 'dots'
    Use-SeriesProgram 'strips'
    (Named-Control 'Numeric series' $type::ComboBox).SetFocus()
    [Windows.Forms.SendKeys]::SendWait('{END}')
    Wait-Name 'Open a drawing program for the selected series.'
    if ((Find-Control 'Accepted observation' $null) -or (Find-Control 'Undo accepted edit' $null) -or
        (Find-Control 'Inspect observation at 2026-02-03T00:00:00.000Z' $null)) { throw 'Previous state survived the series change.' }
    Review-Step 'Back to chart'
    Review-Step 'Data sources'
    Review-Step 'Open published CSV'
    Review-Step 'Open series.csv'
    Review-Step 'Remove frozen chart access'
    Review-Step 'Remove access'
    $null=Wait-Text 'Access removed for this published revision.'
    Write-Output 'The private source was removed through the native published-revision library.'
    Review-Step 'Close'
    Inspect-SampleControls
} catch {
    # Emit only the named ScopeHub window's controls, without inspecting other windows.
    $controls=@($window.FindAll($scope,$any) | Where-Object { $_.Current.Name } |
        ForEach-Object { "$($_.Current.ControlType.ProgrammaticName) $($_.Current.Name)" })
    Write-Output ($controls -join "`n")
    throw
}
