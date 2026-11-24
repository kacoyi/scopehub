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
    $dlg = Wait-ForProbe 'native folder chooser for the test process' {
        $windows = $ui::RootElement.FindAll([Windows.Automation.TreeScope]::Children,$any)
        foreach ($candidate in $windows) {
            if ($candidate.Current.ProcessId -eq $app.Id -and $candidate.Current.ClassName -eq '#32770') { return $candidate }
        }
    }
    Write-Output "Folder picker: $($dlg.Current.Name), HWND=$($dlg.Current.NativeWindowHandle)"
    $editCondition=[Windows.Automation.PropertyCondition]::new($ui::ControlTypeProperty,$type::Edit)
    $edits=@($dlg.FindAll($scope,$editCondition))
    $edit=$edits | Where-Object { $_.Current.Name -match '^(Folder|File name)' -or $_.Current.AutomationId -eq '1148' } | Select-Object -First 1
    if (!$edit) { throw "Owned picker folder input unavailable. Fields: $(($edits | ForEach-Object { $_.Current.Name }) -join ', ')." }
    $edit.GetCurrentPattern($value).SetValue($folder)
    $default=[Windows.Automation.PropertyCondition]::new($ui::AutomationIdProperty,'1')
    $submit=$dlg.FindFirst($scope,$default)
    if (!$submit) { throw 'Owned folder picker has no confirmation button.' }
    $submit.GetCurrentPattern($invoke).Invoke()
    Start-Sleep -Milliseconds 600
    if ($null -eq (Find-Control 'Inspect raw rows' $type::Button)) {
        try { $submit.GetCurrentPattern($invoke).Invoke() } catch [Windows.Automation.ElementNotAvailableException] {}
    }
    Wait-Name 'Inspect raw rows'
}

function Open-Disclosure([string]$label) {
    $control=Wait-ForProbe $label { Find-Control $label $null }
    $expand=$null
    if ($control.TryGetCurrentPattern([Windows.Automation.ExpandCollapsePattern]::Pattern,[ref]$expand)) { $expand.Expand() }
    else { $control.SetFocus(); [Windows.Forms.SendKeys]::SendWait(' ') }
}
function Save-SeriesPlot([string]$kind) {
    $svg=Wait-ForProbe 'Axis-free drawing' {
        foreach ($n in $window.FindAll($scope,$any)) {
            if ($n.Current.Name -eq 'Axis-free drawing' -and $n.Current.BoundingRectangle.Width -gt 300) { return $n }
        }
    }
    $b=$svg.Current.BoundingRectangle
    $plot=[DrawingWindowCapture]::Plot($app.MainWindowHandle,$b.Left,$b.Top,$b.Width,$b.Height)
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
    Enter-NativeFolder $fixture
    Select-NativeOption 'Record kind' 1
    Review-Step 'Inspect raw rows'
    $null=Wait-Text 'Raw CSV rows'
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
    Add-Type -AssemblyName System.Drawing
    Add-Type -Path (Join-Path $PSScriptRoot 'DrawingWindowCapture.cs') -ReferencedAssemblies System.Drawing
    $null=[DrawingWindowCapture]::SetProcessDPIAware()
    [DrawingWindowCapture]::Resize($app.MainWindowHandle,1120,760)
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
    Select-NativeOption 'Numeric series' 2
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
} catch {
    # Emit only the named ScopeHub window's controls, without inspecting other windows.
    $controls=@($window.FindAll($scope,$any) | Where-Object { $_.Current.Name } |
        ForEach-Object { "$($_.Current.ControlType.ProgrammaticName) $($_.Current.Name)" })
    Write-Output ($controls -join "`n")
    throw
}
