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
    $summary=Wait-Text 'Accepted provenance and values'
    $expand=$null
    if ($summary.TryGetCurrentPattern([Windows.Automation.ExpandCollapsePattern]::Pattern,[ref]$expand)) { $expand.Expand() }
    else { $summary.SetFocus(); [Windows.Forms.SendKeys]::SendWait(' ') }
    $null=Wait-Text 'Unknown, 115, 125, 135'
    Write-Output 'Observed the accepted two-period volume mean: Unknown, 115, 125, 135.'
    Review-Step 'Open published CSV'
    Review-Step 'Open series.csv'
    Review-Step 'Use as workspace source'
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
