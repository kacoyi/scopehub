# The caller owns this synthetic ScopeHub process, WebView2 data directory, and local app profile.
param([Parameter(Mandatory=$true)][string]$OutputDirectory)
Add-Type -AssemblyName System.Drawing,System.Windows.Forms
Add-Type -Path (Join-Path $PSScriptRoot 'ChartNativeCapture.cs') -ReferencedAssemblies System.Drawing
$null=[ChartNativeCapture]::SetProcessDPIAware()
$handle=$app.MainWindowHandle
$scope=[Windows.Automation.TreeScope]::Descendants
$any=[Windows.Automation.Condition]::TrueCondition
$kind=[Windows.Automation.ControlType]
$issues=[Collections.Generic.List[string]]::new()
$file=(Resolve-Path (Join-Path $PSScriptRoot '../fixtures/workbench/chart-price-anchors.js')).Path
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $file).Hash -ne 'ac20d9a0cbd4624a36913c75d08df015b2e26602357f62a4e71600df83ded47e') { throw 'Unexpected chart overlay program.' }

function Find-ChartButton([string]$prefix) {
    $condition=[Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::ControlTypeProperty,$kind::Button)
    foreach ($b in $window.FindAll($scope,$condition)) {
        if ($b.Current.IsEnabled -and ([string]$b.Current.Name).StartsWith($prefix)) { return $b }
    }
}
function Invoke-ChartButton([string]$name) {
    Write-Host "Activating chart control: $name"
    $null=Wait-ForProbe "live chart button $name" {
        $control=Find-Control $name $kind::Button
        if (!$control -or !$control.Current.IsEnabled) { return $false }
        try {
            $pattern=$null
            if ($control.TryGetCurrentPattern([Windows.Automation.InvokePattern]::Pattern,[ref]$pattern)) {
                $pattern.Invoke()
                return $true
            }
            $bounds=$control.Current.BoundingRectangle
            if ($bounds.IsEmpty -or $bounds.Width -le 0 -or $bounds.Height -le 0) { return $false }
            $x=[int]($bounds.X+$bounds.Width/2); $y=[int]($bounds.Y+$bounds.Height/2)
            [ChartNativeCapture]::Hover($handle,$x,$y)
            $atPoint=[Windows.Automation.AutomationElement]::FromPoint([Windows.Point]::new($x,$y))
            if ($atPoint.Current.Name -ne $name) { return $false }
            [ChartNativeCapture]::ClickOwnedControl($handle,$x,$y)
            return $true
        } catch [System.InvalidOperationException] { return $false }
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
    $null=Capture-Chart 'draft' 2
    Invoke-ChartButton 'Accept chart drawing'
    Wait-Name 'Remove chart drawing'
    Write-Host 'The native chart now offers Remove chart drawing.'
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
            if ($watch.ElapsedMilliseconds -gt 5500) {
                $image.Save((Join-Path $OutputDirectory 'transition.png'),[Drawing.Imaging.ImageFormat]::Png)
            }
            if ($rings.Count -eq $expected) {
                $image.Save((Join-Path $OutputDirectory "$label.png"),[Drawing.Imaging.ImageFormat]::Png)
                $client=[ChartNativeCapture]::ClientBitmap($handle)
                try { $client.Save((Join-Path $OutputDirectory "$label-client.png"),[Drawing.Imaging.ImageFormat]::Png) } finally { $client.Dispose() }
                Write-Host "$label owned bbox=($($b.Left),$($b.Top),$($b.Width),$($b.Height)) image=$($image.Width)x$($image.Height), rings=$($rings -join ' | ')"
                $desktop=$null
                try {
                    $desktop=[ChartNativeCapture]::ForegroundChart($handle,$b.Left,$b.Top,$b.Width,$b.Height)
                    $desktop.Save((Join-Path $OutputDirectory "$label-screen.png"),[Drawing.Imaging.ImageFormat]::Png)
                } catch { Write-Host "$label foreground capture unavailable: $($_.Exception.GetBaseException().Message)" }
                try {
                    if ($expected -eq 2) {
                        $verified=$false
                        foreach ($surface in @('PrintWindow','foreground')) {
                            $sample=if ($surface -eq 'PrintWindow') { $image } else { $desktop }
                            if (!$sample) { continue }
                            $observed=@([ChartNativeCapture]::Rings($sample))
                            Write-Host "$label $surface observed rings $($observed -join ' | ')"
                            if ($observed.Count -ne 2) { continue }
                            $aligned=$true
                            for ($i=0;$i -lt 2;$i++) {
                                $colors=[ChartNativeCapture]::CenterColors($sample,$observed[$i])
                                $closeY=[ChartNativeCapture]::CloseRow($sample,$observed[$i],$i -eq 1)
                                $ringY=$observed[$i].Top + ($observed[$i].Height-1)/2
                                $atClose=$closeY -ge 0 -and [Math]::Abs($closeY-$ringY) -le 2
                                Write-Host "$label $surface point $i ring center row=$ringY candle body close row=$closeY aligned=$atClose; 5x5 RGB=$($colors -join ',')"
                                $aligned=$aligned -and $atClose
                            }
                            if ($aligned) { $verified=$true }
                        }
                        if (!$verified) { $issues.Add("$label candle price has no matching pixel under both rings.") }
                        return @{x=$b.Left; y=$b.Top; width=$b.Width; height=$b.Height; rings=$rings}
                    }
                } finally { if ($desktop) { $desktop.Dispose() } }
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
    [ChartNativeCapture]::Resize($handle,1280,800)
    Wait-Name 'Close panel'
    Invoke-ChartButton 'Close panel'
    $null=Wait-ForProbe 'closed Watchlist before chart qualification' { !(Find-Control 'Close panel' $kind::Button) }
    Open-ChartFile
    $wide=Capture-Chart 'aligned' 2
    try { Hover-Bar $wide 1 } catch { $issues.Add("Wide hover: $($_.Exception.Message)") }
    [ChartNativeCapture]::Hover($handle,[int]($wide.x+$wide.width-10),[int]($wide.y+28))
    try { $null=Wait-ForProbe 'price scale clears crosshair timestamp' {
        $texts=@($window.FindAll($scope,$any) | ForEach-Object { $_.Current.Name })
        -not ($texts -join ' ').Contains('2024-01-02T13:01:00.000Z')
    }
    } catch { $issues.Add("Price scale: $($_.Exception.Message)") }
    [ChartNativeCapture]::Resize($handle,1100,740)
    Start-Sleep -Milliseconds 600
    $narrow=Capture-Chart 'resized' 2
    if ([Math]::Abs($wide.width-$narrow.width) -lt 80) { $issues.Add('Actual native plot width did not change.') }
    try { Hover-Bar $narrow 0 } catch { $issues.Add("Narrow hover: $($_.Exception.Message)") }
    [ChartNativeCapture]::Hover($handle,[int]($narrow.x+$narrow.width-12),[int]($narrow.y+28))
    Invoke-ChartButton 'Zoom in'
    Start-Sleep -Milliseconds 500
    try { $null=Capture-Chart 'clipped' 0 } catch { $issues.Add("Zoom clipping: $($_.Exception.Message)") }
    Invoke-ChartButton 'Fit chart'
    Start-Sleep -Milliseconds 500
    $fit=Capture-Chart 'refit' 2
    try { Hover-Bar $fit 1 } catch { $issues.Add("Refit hover: $($_.Exception.Message)") }
    Invoke-ChartButton '5m'
    $null=Wait-ForProbe 'timeframe removes the chart drawing and program picker' {
        -not (Find-Control 'Remove chart drawing' $null) -and -not (Find-ChartButton 'Open chart drawing program') -and
        -not (Find-ChartButton 'Choose File')
    }
    $null=Capture-Chart 'switched' 0
    if ($issues.Count) { throw ($issues -join ' / ') }
} catch {
    $labels=@($window.FindAll($scope,$any) | Where-Object { $_.Current.Name -and $_.Current.ControlType -ne $kind::ListItem } |
        ForEach-Object { "$($_.Current.ControlType.ProgrammaticName) $($_.Current.Name)" })
    Write-Output ($labels -join "`n")
    throw
}
