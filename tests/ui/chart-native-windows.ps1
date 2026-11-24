# The caller owns this synthetic ScopeHub process, WebView2 data directory, and local app profile.
param([Parameter(Mandatory=$true)][string]$OutputDirectory)
Add-Type -AssemblyName System.Drawing,System.Windows.Forms
Add-Type -Path (Join-Path $PSScriptRoot 'ChartNativeCapture.cs') -ReferencedAssemblies System.Drawing
$null=[ChartNativeCapture]::SetProcessDPIAware()
$handle=$app.MainWindowHandle
$scope=[Windows.Automation.TreeScope]::Descendants
$any=[Windows.Automation.Condition]::TrueCondition
$kind=[Windows.Automation.ControlType]
$file=(Resolve-Path (Join-Path $PSScriptRoot '../fixtures/workbench/chart-price-anchors.js')).Path
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $file).Hash -ne 'ac20d9a0cbd4624a36913c75d08df015b2e26602357f62a4e71600df83ded47e') { throw 'Unexpected chart overlay program.' }

function Find-ChartButton([string]$prefix) {
    $condition=[Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::ControlTypeProperty,$kind::Button)
    foreach ($b in $window.FindAll($scope,$condition)) {
        if ($b.Current.IsEnabled -and $b.Current.Name.StartsWith($prefix)) { return $b }
    }
}
function Open-ChartFile {
    $open=Wait-ForProbe 'chart program file button' {
        $button=Find-ChartButton 'Open chart drawing program'
        if ($button) { return $button }
        Find-ChartButton 'Choose File'
    }
    $open.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke()
    $dlg=Wait-ForProbe 'file chooser owned by the chart host' {
        foreach ($c in $window.FindAll($scope,[Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::ControlTypeProperty,$kind::Window))) {
            if ($c.Current.NativeWindowHandle -and $c.Current.Name -match 'Open|File|Select') { return $c }
        }
        foreach ($c in [Windows.Automation.AutomationElement]::RootElement.FindAll([Windows.Automation.TreeScope]::Children,$any)) {
            if ($c.Current.ClassName -ne '#32770' -or !$c.Current.NativeWindowHandle) { continue }
            try { [ChartNativeCapture]::VerifyOwnedDialog($handle,[IntPtr]$c.Current.NativeWindowHandle); return $c } catch {}
        }
    }
    $dialog=[IntPtr]$dlg.Current.NativeWindowHandle
    [ChartNativeCapture]::VerifyOwnedDialog($handle,$dialog)
    Write-Output "Chart file chooser HWND=$dialog owner=$handle"
    if ([ChartNativeCapture]::SetPickerFolder($dialog,$file)) {
        if (![ChartNativeCapture]::PressPickerOk($dialog)) { throw 'Owned native picker lacks a file confirmation control.' }
    } else {
        $edit=$dlg.FindAll($scope,$any) | Where-Object { $_.Current.ControlType -eq $kind::Edit -and $_.Current.Name -match '^File name' } | Select-Object -First 1
        if ($edit) {
            $edit.GetCurrentPattern([Windows.Automation.ValuePattern]::Pattern).SetValue($file)
            if (![ChartNativeCapture]::PressPickerOk($dialog)) { throw 'Owned file confirmation unavailable.' }
        } else {
            [ChartNativeCapture]::FocusPicker($dialog)
            [Windows.Forms.SendKeys]::SendWait('^l')
            [Windows.Forms.Clipboard]::SetText($file)
            try { [Windows.Forms.SendKeys]::SendWait('^v{ENTER}') }
            finally { [Windows.Forms.Clipboard]::Clear() }
        }
    }
    Wait-Name 'Accept chart drawing'
    Invoke-Button 'Accept chart drawing'
    Wait-Name 'Remove chart drawing'
}
function Capture-Chart([string]$label,[int]$expected) {
    $chart=Wait-ForProbe 'actual synthetic financial chart' {
        Find-Control 'Candlestick and volume chart for SIM.CN.FUTURE.001' $kind::Image
    }
    $watch=[Diagnostics.Stopwatch]::StartNew()
    do {
        $b=$chart.Current.BoundingRectangle
        $image=[ChartNativeCapture]::Plot($handle,$b.Left,$b.Top,$b.Width,$b.Height)
        try {
            $rings=@([ChartNativeCapture]::Rings($image))
            if ($rings.Count -eq $expected) {
                if ($expected -eq 2 -and (!([ChartNativeCapture]::Candle($image,$rings[0],$false)) -or
                    !([ChartNativeCapture]::Candle($image,$rings[1],$true)))) { throw 'Expected candle ink at both authored close positions.' }
                $image.Save((Join-Path $OutputDirectory "$label.png"),[Drawing.Imaging.ImageFormat]::Png)
                Write-Host "$label actual chart $($image.Width)x$($image.Height), rings=$($rings -join ' | ')"
                if ($expected -eq 2) {
                    return @{x=$b.Left; y=$b.Top; width=$b.Width; height=$b.Height; rings=$rings}
                }
                return
            }
        } finally { $image.Dispose() }
        Start-Sleep -Milliseconds 100
    } while ($watch.ElapsedMilliseconds -lt 6000)
    throw "Unexpected visible anchor count ($($rings.Count)) at $label."
}
function Hover-Bar($obs,[int]$index) {
    $r=$obs.rings[$index]
    [ChartNativeCapture]::Hover($handle,[int]($obs.x+$r.Left+$r.Width/2),[int]($obs.y+$r.Top+$r.Height/2))
    $stamp=@('2024-01-02T13:00:00.000Z','2024-01-02T13:01:00.000Z')[$index]
    $close=@('3499.2','3499.8')[$index]
    $null=Wait-ForProbe "real chart crosshair $stamp and close $close" {
        $texts=@($window.FindAll($scope,$any) | ForEach-Object { $_.Current.Name })
        if (($texts -join ' ').Contains($stamp) -and ($texts -join ' ').Replace(',','').Contains("$close CNY")) { return $true }
    }
}
try {
    [ChartNativeCapture]::Resize($handle,1120,760)
    Open-ChartFile
    $wide=Capture-Chart 'aligned' 2
    Hover-Bar $wide 1
    [ChartNativeCapture]::Hover($handle,[int]($wide.x+$wide.width-10),[int]($wide.y+28))
    $null=Wait-ForProbe 'price scale clears crosshair timestamp' {
        $texts=@($window.FindAll($scope,$any) | ForEach-Object { $_.Current.Name })
        -not ($texts -join ' ').Contains('2024-01-02T13:01:00.000Z')
    }
    [ChartNativeCapture]::Resize($handle,900,650)
    Start-Sleep -Milliseconds 600
    $narrow=Capture-Chart 'resized' 2
    if ([Math]::Abs($wide.width-$narrow.width) -lt 80) { throw 'Actual native plot width did not change.' }
    Hover-Bar $narrow 0
    [ChartNativeCapture]::Hover($handle,[int]($narrow.x+$narrow.width-12),[int]($narrow.y+28))
    Invoke-Button 'Zoom in'
    Start-Sleep -Milliseconds 500
    $null=Capture-Chart 'clipped' 0
    Invoke-Button 'Fit chart'
    Start-Sleep -Milliseconds 500
    $fit=Capture-Chart 'refit' 2
    Hover-Bar $fit 1
    Invoke-Button '5m'
    $null=Wait-ForProbe 'timeframe removes the chart drawing and program picker' {
        -not (Find-Control 'Remove chart drawing' $null) -and -not (Find-ChartButton 'Open chart drawing program') -and
        -not (Find-ChartButton 'Choose File')
    }
    $null=Capture-Chart 'switched' 0
} catch {
    $labels=@($window.FindAll($scope,$any) | Where-Object { $_.Current.Name -and $_.Current.ControlType -ne $kind::ListItem } |
        ForEach-Object { "$($_.Current.ControlType.ProgrammaticName) $($_.Current.Name)" })
    Write-Output ($labels -join "`n")
    throw
}
