[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string[]]$ReportPath,

    [string]$OutputPath = "device/marble/compatibility/PACKAGE_EXPOSURE_MATRIX.md"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($ReportPath.Count -lt 2) {
    Write-Error "Provide at least two Camera2 report paths to compare package exposure."
    exit 1
}

$reports = New-Object System.Collections.Generic.List[object]

foreach ($path in $ReportPath) {
    if (-not (Test-Path -LiteralPath $path)) {
        Write-Error "Report not found: $path"
        exit 1
    }

    $data = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
    $package = [string]$data.packageName

    if ([string]::IsNullOrWhiteSpace($package)) {
        $package = "<legacy-report-without-packageName>"
    }

    $ids = @($data.cameraIdList | ForEach-Object { [string]$_ } | Sort-Object -Unique)

    $reports.Add([pscustomobject]@{
        Path = $path
        Package = $package
        Ids = $ids
        Data = $data
    })
}

$baseline = $reports[0]
$allIds = @(
    $reports |
        ForEach-Object { $_.Ids } |
        Sort-Object -Unique
)

$outDir = Split-Path -Parent $OutputPath
if ($outDir) {
    New-Item -ItemType Directory -Path $outDir -Force | Out-Null
}

function EscapeCell([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return "—"
    }
    return ($Value -replace "\|", "\|")
}

$lines = New-Object System.Collections.Generic.List[string]
$lines.Add("# POCO F5 Package-specific Camera Exposure Matrix")
$lines.Add("")
$lines.Add("> This compares which Camera2 IDs are exposed to different Android application IDs on the same device/ROM. It does not by itself prove that a specific GCam build can successfully capture from every exposed ID.")
$lines.Add("")
$lines.Add("## Summary")
$lines.Add("")
$lines.Add("| Package | Camera count | Camera IDs | Extra IDs vs baseline | Missing IDs vs baseline |")
$lines.Add("| --- | ---: | --- | --- | --- |")

$baseIds = @($baseline.Ids)

foreach ($report in $reports) {
    $extra = @($report.Ids | Where-Object { $_ -notin $baseIds })
    $missing = @($baseIds | Where-Object { $_ -notin $report.Ids })

    $lines.Add(
        "| $(EscapeCell $report.Package) | $($report.Ids.Count) | " +
        "$(EscapeCell ($report.Ids -join ', ')) | " +
        "$(EscapeCell ($extra -join ', ')) | " +
        "$(EscapeCell ($missing -join ', ')) |"
    )
}

$lines.Add("")
$lines.Add("Baseline package: $($baseline.Package)")
$lines.Add("")
$lines.Add("## Camera ID visibility")
$lines.Add("")

$header = "| Camera ID |"
$separator = "| --- |"
foreach ($report in $reports) {
    $header += " $(EscapeCell $report.Package) |"
    $separator += " --- |"
}
$lines.Add($header)
$lines.Add($separator)

foreach ($id in $allIds) {
    $row = "| $(EscapeCell $id) |"
    foreach ($report in $reports) {
        $row += if ($id -in $report.Ids) { " Yes |" } else { " No |" }
    }
    $lines.Add($row)
}

$lines.Add("")
$lines.Add("## Evidence")
$lines.Add("")
for ($i = 0; $i -lt $reports.Count; $i++) {
    $report = $reports[$i]
    $lines.Add("- $($report.Package): $($report.Path)")
}

$lines.Add("")
$lines.Add("## Interpretation rule")
$lines.Add("")
$lines.Add("Treat a package-specific extra Camera ID as an auxiliary-camera exposure candidate. Confirm the physical lens using the Lens Verifier or an equivalent live-preview test before assigning Main / Ultrawide / Macro labels.")

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines($OutputPath, $lines, $utf8NoBom)

Write-Host "Package exposure matrix generated:"
Write-Host $OutputPath