[CmdletBinding()]
param(
    [string]$Serial,
    [string]$OutputRoot = "device/marble/baseline/captures",
    [switch]$IncludeCameraLog
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Fail([string]$Message) {
    Write-Error $Message
    exit 1
}

if (-not (Get-Command adb -ErrorAction SilentlyContinue)) {
    Fail "adb was not found. Install Android SDK Platform-Tools and add adb to PATH."
}

$deviceLines = @(
    & adb devices |
        Select-Object -Skip 1 |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_ -match "\sdevice$" }
)

if ($Serial) {
    $matched = $deviceLines | Where-Object { $_ -match "^$([regex]::Escape($Serial))\s+device$" }
    if (-not $matched) {
        Fail "The requested device '$Serial' is not connected and authorized."
    }
}
else {
    if ($deviceLines.Count -eq 0) {
        Fail "No authorized Android device found. Enable USB debugging, connect the POCO F5, and accept the RSA prompt."
    }
    if ($deviceLines.Count -gt 1) {
        Fail "More than one Android device is connected. Re-run with -Serial <adb-serial>."
    }
    $Serial = ($deviceLines[0] -split "\s+")[0]
}

function Invoke-AdbText {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    $prefix = @()
    if ($Serial) {
        $prefix = @("-s", $Serial)
    }

    $result = & adb @prefix @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "adb command failed: adb $($Arguments -join ' ')`n$($result -join [Environment]::NewLine)"
    }

    return ($result -join [Environment]::NewLine)
}

function Write-Utf8NoBom {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [AllowEmptyString()][string]$Content
    )

    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $utf8NoBom)
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$outDir = Join-Path $OutputRoot $timestamp
New-Item -ItemType Directory -Path $outDir -Force | Out-Null

Write-Host "Collecting POCO F5 camera baseline..."
Write-Host "Output: $outDir"

$safeProps = [ordered]@{
    "ro.product.manufacturer"          = ""
    "ro.product.brand"                 = ""
    "ro.product.model"                 = ""
    "ro.product.device"                = ""
    "ro.product.name"                  = ""
    "ro.build.version.release"         = ""
    "ro.build.version.sdk"             = ""
    "ro.build.version.security_patch"  = ""
    "ro.build.fingerprint"             = ""
    "ro.build.id"                      = ""
    "ro.build.type"                    = ""
    "ro.build.tags"                    = ""
    "ro.boot.hardware"                 = ""
    "ro.hardware"                      = ""
    "ro.soc.manufacturer"              = ""
    "ro.soc.model"                     = ""
    "ro.vendor.build.fingerprint"      = ""
    "ro.vendor.build.security_patch"   = ""
    "ro.vendor.camera.aux.packagelist" = ""
}

foreach ($key in @($safeProps.Keys)) {
    try {
        $safeProps[$key] = (Invoke-AdbText @("shell", "getprop", $key)).Trim()
    }
    catch {
        $safeProps[$key] = "<unavailable>"
    }
}

$metadata = New-Object System.Collections.Generic.List[string]
$metadata.Add("# POCO F5 camera baseline metadata")
$metadata.Add("captured_at_local=$((Get-Date).ToString("o"))")
$metadata.Add("collector=collect-camera-baseline.ps1")
$metadata.Add("collector_version=1")
foreach ($entry in $safeProps.GetEnumerator()) {
    $metadata.Add("$($entry.Key)=$($entry.Value)")
}
Write-Utf8NoBom (Join-Path $outDir "device-metadata.txt") ($metadata -join [Environment]::NewLine)

try {
    $cameraDump = Invoke-AdbText @("shell", "dumpsys", "media.camera")
    Write-Utf8NoBom (Join-Path $outDir "dumpsys-media-camera.txt") $cameraDump
}
catch {
    Write-Utf8NoBom (Join-Path $outDir "dumpsys-media-camera.ERROR.txt") $_.Exception.Message
}

try {
    $proxyDump = Invoke-AdbText @("shell", "dumpsys", "media.camera.proxy")
    Write-Utf8NoBom (Join-Path $outDir "dumpsys-media-camera-proxy.txt") $proxyDump
}
catch {
    Write-Utf8NoBom (Join-Path $outDir "dumpsys-media-camera-proxy.ERROR.txt") $_.Exception.Message
}

try {
    $allProps = Invoke-AdbText @("shell", "getprop")
    $cameraProps = @(
        $allProps -split "\r?\n" |
            Where-Object {
                $_ -match "(?i)camera" -and
                $_ -notmatch "(?i)(serial|imei|meid|subscriber|account)"
            } |
            Sort-Object -Unique
    )
    Write-Utf8NoBom (Join-Path $outDir "camera-properties.txt") ($cameraProps -join [Environment]::NewLine)
}
catch {
    Write-Utf8NoBom (Join-Path $outDir "camera-properties.ERROR.txt") $_.Exception.Message
}

try {
    $features = Invoke-AdbText @("shell", "pm", "list", "features")
    $cameraFeatures = @(
        $features -split "\r?\n" |
            Where-Object { $_ -match "(?i)(camera|opengles|vulkan|sensor)" } |
            Sort-Object -Unique
    )
    Write-Utf8NoBom (Join-Path $outDir "camera-related-features.txt") ($cameraFeatures -join [Environment]::NewLine)
}
catch {
    Write-Utf8NoBom (Join-Path $outDir "camera-related-features.ERROR.txt") $_.Exception.Message
}

try {
    $packages = Invoke-AdbText @("shell", "pm", "list", "packages")
    $cameraPackages = @(
        $packages -split "\r?\n" |
            Where-Object { $_ -match "(?i)(camera|gcam|pixelcam|snapcam|lmc|agc)" } |
            Sort-Object -Unique
    )
    Write-Utf8NoBom (Join-Path $outDir "camera-packages.txt") ($cameraPackages -join [Environment]::NewLine)
}
catch {
    Write-Utf8NoBom (Join-Path $outDir "camera-packages.ERROR.txt") $_.Exception.Message
}

try {
    $codecDump = Invoke-AdbText @("shell", "dumpsys", "media.codec")
    Write-Utf8NoBom (Join-Path $outDir "dumpsys-media-codec.txt") $codecDump
}
catch {
    Write-Utf8NoBom (Join-Path $outDir "dumpsys-media-codec.ERROR.txt") $_.Exception.Message
}

if ($IncludeCameraLog) {
    Write-Warning "Camera log collection is enabled. Review the file before sharing because logcat can contain app/package/runtime information."
    try {
        $logcat = Invoke-AdbText @("logcat", "-d", "-v", "threadtime")
        $filtered = @(
            $logcat -split "\r?\n" |
                Where-Object { $_ -match "(?i)(camera|cameraserver|camera2|camx|chi|qcamera|gcam)" }
        )
        Write-Utf8NoBom (Join-Path $outDir "camera-logcat.txt") ($filtered -join [Environment]::NewLine)
    }
    catch {
        Write-Utf8NoBom (Join-Path $outDir "camera-logcat.ERROR.txt") $_.Exception.Message
    }
}

$readme = @"
# Baseline capture

This directory was generated by scripts/collect-camera-baseline.ps1.

- Capture time: $((Get-Date).ToString("o"))
- Device serial: intentionally not stored
- Logcat included: $($IncludeCameraLog.IsPresent)

Before committing or sharing these files, review them for any information you do not want to publish.
"@
Write-Utf8NoBom (Join-Path $outDir "README.md") $readme

$hashLines = Get-ChildItem -Path $outDir -File |
    Where-Object { $_.Name -ne "SHA256SUMS.txt" } |
    Sort-Object Name |
    ForEach-Object {
        $hash = (Get-FileHash -Path $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        "$hash  $($_.Name)"
    }
Write-Utf8NoBom (Join-Path $outDir "SHA256SUMS.txt") ($hashLines -join [Environment]::NewLine)

Write-Host ""
Write-Host "Baseline capture complete."
Write-Host "Review the generated files, then commit the capture directory if it is safe to publish."