[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ReportPath,

    [string]$OutputPath
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if (-not (Test-Path -LiteralPath $ReportPath)) {
    Write-Error "Report not found: $ReportPath"
    exit 1
}

$report = Get-Content -LiteralPath $ReportPath -Raw -Encoding UTF8 | ConvertFrom-Json

if (-not $OutputPath) {
    $OutputPath = Join-Path (Split-Path -Parent $ReportPath) "CAPABILITY_MATRIX.md"
}

function Cell($Value) {
    if ($null -eq $Value) { return "—" }
    $text = [string]$Value
    if ([string]::IsNullOrWhiteSpace($text)) { return "—" }
    return ($text -replace "\|", "\|")
}

function NamedValue($Object) {
    if ($null -eq $Object) { return "—" }
    if ($Object.name) { return [string]$Object.name }
    if ($null -ne $Object.value) { return [string]$Object.value }
    return "—"
}

function JoinArray($Value) {
    if ($null -eq $Value) { return "—" }
    $items = @($Value | ForEach-Object { [string]$_ })
    if ($items.Count -eq 0) { return "—" }
    return ($items -join ", ")
}

function FormatFocalLengths($Value) {
    if ($null -eq $Value) { return "—" }
    $items = @($Value | ForEach-Object {
        try { "{0:0.###} mm" -f [double]$_ }
        catch { [string]$_ }
    })
    if ($items.Count -eq 0) { return "—" }
    return ($items -join ", ")
}

function CapabilityNames($Capabilities) {
    if ($null -eq $Capabilities) { return "—" }
    $names = @($Capabilities | ForEach-Object {
        if ($_.name) { [string]$_.name } else { [string]$_.value }
    })
    if ($names.Count -eq 0) { return "—" }
    return ($names -join ", ")
}

function HasCapability($Capabilities, [string]$Name) {
    foreach ($cap in @($Capabilities)) {
        if ([string]$cap.name -eq $Name) { return $true }
    }
    return $false
}

function MaxJpegSize($Camera) {
    foreach ($fmt in @($Camera.streamConfiguration.outputsByFormat)) {
        if ([string]$fmt.format.name -eq "JPEG") {
            $sizes = @($fmt.sizes)
            if ($sizes.Count -gt 0) {
                $s = $sizes[0]
                return "$($s.width)x$($s.height) ($($s.megapixels) MP)"
            }
        }
    }
    return "—"
}

function MaxHighSpeed($Camera) {
    $max = 0
    foreach ($entry in @($Camera.streamConfiguration.highSpeedVideo)) {
        foreach ($range in @($entry.fpsRanges)) {
            if ($null -ne $range.upper) {
                $upper = [int]$range.upper
                if ($upper -gt $max) { $max = $upper }
            }
        }
    }
    if ($max -eq 0) { return "—" }
    return "$max fps"
}

$lines = New-Object System.Collections.Generic.List[string]
$lines.Add("# POCO F5 Camera2 Capability Matrix")
$lines.Add("")
$lines.Add("> Generated automatically from the Camera2 probe report. Physical lens labels must still be verified on the real device.")
$lines.Add("")
$lines.Add("## Build context")
$lines.Add("")
$lines.Add("| Field | Value |")
$lines.Add("| --- | --- |")
$lines.Add("| Manufacturer | $(Cell $report.device.manufacturer) |")
$lines.Add("| Brand | $(Cell $report.device.brand) |")
$lines.Add("| Model | $(Cell $report.device.model) |")
$lines.Add("| Device | $(Cell $report.device.device) |")
$lines.Add("| Android | $(Cell $report.device.androidRelease) (API $(Cell $report.device.sdkInt)) |")
$lines.Add("| Security patch | $(Cell $report.device.securityPatch) |")
$lines.Add("| Build fingerprint | $(Cell $report.device.fingerprint) |")
$lines.Add("| Probe generated at | $(Cell $report.generatedAtUtc) |")
$lines.Add("")

$lines.Add("## Camera IDs")
$lines.Add("")
$lines.Add("| ID | Facing | Hardware level | Physical IDs | Focal length(s) | RAW | Manual sensor | Max JPEG | High-speed |")
$lines.Add("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")

foreach ($camera in @($report.cameras)) {
    $n = $camera.normalized
    $physical = JoinArray $n.physicalCameraIds
    $focal = FormatFocalLengths $n.focalLengthsMm
    $raw = if (HasCapability $n.capabilities "RAW") { "Yes" } else { "No" }
    $manual = if (HasCapability $n.capabilities "MANUAL_SENSOR") { "Yes" } else { "No" }
    $jpeg = MaxJpegSize $camera
    $highSpeed = MaxHighSpeed $camera

    $lines.Add(
        "| $(Cell $camera.id) | $(Cell (NamedValue $n.lensFacing)) | $(Cell (NamedValue $n.hardwareLevel)) | " +
        "$(Cell $physical) | $(Cell $focal) | $raw | $manual | $(Cell $jpeg) | $(Cell $highSpeed) |"
    )
}

$lines.Add("")
$lines.Add("## Detailed normalized characteristics")
$lines.Add("")

foreach ($camera in @($report.cameras)) {
    $n = $camera.normalized
    $lines.Add("### Camera ID $(Cell $camera.id)")
    $lines.Add("")
    $lines.Add("- Facing: **$(Cell (NamedValue $n.lensFacing))**")
    $lines.Add("- Hardware level: **$(Cell (NamedValue $n.hardwareLevel))**")
    $lines.Add("- Physical camera IDs: **$(Cell (JoinArray $n.physicalCameraIds))**")
    $lines.Add("- Focal lengths: **$(Cell (FormatFocalLengths $n.focalLengthsMm))**")
    $lines.Add("- Apertures: **$(Cell (JoinArray $n.apertures))**")
    $lines.Add("- Capabilities: $(Cell (CapabilityNames $n.capabilities))")
    $lines.Add("- Sensor orientation: $(Cell $n.sensorOrientation)")
    $lines.Add("- Max digital zoom: $(Cell $n.maxDigitalZoom)")
    $lines.Add("- Max JPEG: $(Cell (MaxJpegSize $camera))")
    $lines.Add("- Max constrained high-speed FPS: $(Cell (MaxHighSpeed $camera))")
    $lines.Add("")
}

$lines.Add("## Lens mapping verification")
$lines.Add("")
$lines.Add("Do not infer Main / Ultrawide / Macro / Front solely from Camera ID numbers. Verify each rear camera by opening it through a test client or by comparing focal length, field of view, physical IDs, and actual covered-lens behavior.")
$lines.Add("")
$lines.Add("## Evidence")
$lines.Add("")
$lines.Add("- Source JSON: $(Split-Path -Leaf $ReportPath)")
$lines.Add("- Schema version: $(Cell $report.schemaVersion)")
$lines.Add("- All raw CameraCharacteristics remain available in the JSON under allCharacteristics.")

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines($OutputPath, $lines, $utf8NoBom)

Write-Host "Capability matrix generated:"
Write-Host $OutputPath