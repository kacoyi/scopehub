# Invoked inside the existing private-profile probe after verifying its native window owner.
param([Parameter(Mandatory=$true)][string]$OutputDirectory)
Add-Type -AssemblyName System.Drawing
Add-Type -Path (Join-Path $PSScriptRoot 'DrawingWindowCapture.cs') -ReferencedAssemblies System.Drawing
$null = [DrawingWindowCapture]::SetProcessDPIAware()
$handle = $app.MainWindowHandle
$fixtureDir = Join-Path $PSScriptRoot '../fixtures/workbench'
$program = [IO.File]::ReadAllText((Join-Path $fixtureDir 'volume-pennants.js')).Replace("`r`n","`n")
$revision = [IO.File]::ReadAllText((Join-Path $fixtureDir 'volume-pennants-revised.js')).Replace("`r`n","`n")
$frames = @()

function Drawing-Editor {
    $field = Wait-ForProbe 'Program source' { Find-Control 'Program source' ([Windows.Automation.ControlType]::Edit) }
    $field.GetCurrentPattern([Windows.Automation.ValuePattern]::Pattern)
}
function Preview-Program([string]$code) {
    (Drawing-Editor).SetValue($code)
    Invoke-Button 'Preview draft'
    Wait-Name 'Draft preview'
    Invoke-Button 'Accept view'
    Wait-Name 'Accepted view'
}
function Save-Plot([string]$name, [bool]$expanded) {
    $svg = Wait-ForProbe 'price overlay image' { Find-Control 'Price overlay' ([Windows.Automation.ControlType]::Image) }
    $path = Join-Path $OutputDirectory $name
    # Rendering and accessibility updates can land on different frames.
    $watch = [Diagnostics.Stopwatch]::StartNew()
    do {
        $bounds = $svg.Current.BoundingRectangle
        $image = [DrawingWindowCapture]::Plot($handle,$bounds.Left,$bounds.Top,$bounds.Width,$bounds.Height)
        try {
            $measured = [DrawingWindowCapture]::Poles($image,$expanded)
            $image.Save($path,[Drawing.Imaging.ImageFormat]::Png)
            return @{ name=$name; width=$image.Width; height=$image.Height; pole_centers_and_bottoms=$measured
                sha256=(Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant() }
        } catch {
            if ($watch.Elapsed.TotalSeconds -gt 6) { throw }
        } finally { $image.Dispose() }
        Start-Sleep -Milliseconds 150
    } while ($true)
}

Invoke-Button 'Open blank AI workbench'
Invoke-Button 'Drawing studio'
Wait-Name 'Blank canvas'
Preview-Program $program
[DrawingWindowCapture]::Resize($handle,1120,760)
Start-Sleep -Milliseconds 300
$frames += Save-Plot 'fit.png' $false
[DrawingWindowCapture]::Resize($handle,900,650)
Invoke-Button 'Expand viewport'
Wait-Name 'Fit frozen bars'
$frames += Save-Plot 'expanded.png' $true
if ($frames[0].width -lt $frames[1].width + 50) { throw 'Resizing did not change the painted plot.' }
Invoke-Button 'Fit frozen bars'
Wait-Name 'Expand viewport'
Preview-Program $revision
$frames += Save-Plot 'revised.png' $false
(Drawing-Editor).SetValue("// future overlay`nconst incomplete =")
Invoke-Button 'Preview draft'
Wait-Name 'The drawing failed validation or exceeded its limits.'
Invoke-Button 'Restore accepted view'
Wait-ForProbe 'restored drawing source' { (Drawing-Editor).Current.Value.Replace("`r`n","`n").Trim() -ceq $revision.Trim() } | Out-Null
$frames += Save-Plot 'restored.png' $false
if ($frames[2].sha256 -ne $frames[3].sha256) { throw 'Restoration changed the accepted plot pixels.' }
Invoke-Button 'Undo accepted edit'
Wait-ForProbe 'original drawing source' { (Drawing-Editor).Current.Value.Replace("`r`n","`n").Trim() -ceq $program.Trim() } | Out-Null
Invoke-Button 'Back to chart'
Wait-Name 'Open blank AI workbench'
@{ fixture='3a1b0032b57711c9563131caf34c8cfda9d91bbf5ef5e9dbc7a4f0f48ab390d1'
   capture='Owned WebView2 client window, cropped to its Price overlay UIA bounds'
   frames=$frames } | ConvertTo-Json -Depth 5 -Compress | Write-Output
