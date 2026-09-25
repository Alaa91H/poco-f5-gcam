[CmdletBinding()]
param(
    [string]$Serial,
    [string]$ApkPath = "tools/lens-verifier/app/build/outputs/apk/debug/app-debug.apk",
    [string]$OutputRoot = "device/marble/lens-mapping/captures",
    [switch]$Build,
    [int]$TimeoutSeconds = 600
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Package = "dev.alaa.pocof5.lensverifier"
$Activity = ".MainActivity"
$ExportRelativePath = "files/lens-mapping.json"

function Fail([string]$Message) {
    Write-Error $Message
    exit 1
}

if (-not (Get-Command adb -ErrorAction SilentlyContinue)) {
    Fail "adb was not found. Install Android SDK Platform-Tools and add adb to PATH."
}

if ($Build) {
    if (-not (Get-Command gradle -ErrorAction SilentlyContinue)) {
        Fail "Gradle was not found. Install Gradle or use the APK artifact produced by GitHub Actions."
    }

    Write-Host "Building lens verifier..."
    & gradle -p "tools/lens-verifier" ":app:assembleDebug"
    if ($LASTEXITCODE -ne 0) {
        Fail "Lens verifier build failed."
    }
}

if (-not (Test-Path -LiteralPath $ApkPath)) {
    Fail "Lens verifier APK not found at '$ApkPath'. Build it with -Build or download the lens-verifier-debug artifact from GitHub Actions."
}

$deviceLines = @(
    & adb devices |
        Select-Object -Skip 1 |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_ -match "\sdevice$" }
)

if ($Serial) {
    $matched = $deviceLines | Where-Object {
        $_ -match "^$([regex]::Escape($Serial))\s+device$"
    }
    if (-not $matched) {
        Fail "The requested device '$Serial' is not connected and authorized."
    }
}
else {
    if ($deviceLines.Count -eq 0) {
        Fail "No authorized Android device found."
    }
    if ($deviceLines.Count -gt 1) {
        Fail "More than one Android device is connected. Re-run with -Serial <adb-serial>."
    }
    $Serial = ($deviceLines[0] -split "\s+")[0]
}

$adbPrefix = @("-s", $Serial)

Write-Host "Installing dedicated lens verifier..."
& adb @adbPrefix uninstall $Package 2>$null | Out-Null
& adb @adbPrefix install $ApkPath
if ($LASTEXITCODE -ne 0) {
    Fail "Lens verifier APK installation failed."
}

& adb @adbPrefix shell run-as $Package rm -f $ExportRelativePath 2>$null | Out-Null

Write-Host ""
Write-Host "Launching lens verifier on the phone..."
Write-Host "For each Camera ID:"
Write-Host "  1. Cover the physical lenses one at a time."
Write-Host "  2. Confirm which lens feeds the live preview."
Write-Host "  3. Tap Main / Ultrawide / Macro / Front / Other."
Write-Host "  4. Move to the next Camera ID."
Write-Host "When finished, tap 'Export verified mapping'."
Write-Host ""

& adb @adbPrefix shell am start -W -n "$Package/$Activity" | Out-Null
if ($LASTEXITCODE -ne 0) {
    Fail "Failed to launch the lens verifier."
}

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$ready = $false

do {
    Start-Sleep -Seconds 1
    & adb @adbPrefix shell run-as $Package test -f $ExportRelativePath 2>$null
    if ($LASTEXITCODE -eq 0) {
        $ready = $true
        break
    }
} while ((Get-Date) -lt $deadline)

if (-not $ready) {
    Fail "Timed out waiting for the exported lens mapping after $TimeoutSeconds seconds. Run the script again and press 'Export verified mapping' in the app."
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$outDir = Join-Path $OutputRoot $timestamp
New-Item -ItemType Directory -Path $outDir -Force | Out-Null

$outFile = Join-Path $outDir "lens-mapping.json"
$mappingLines = & adb @adbPrefix exec-out run-as $Package cat $ExportRelativePath 2>&1
if ($LASTEXITCODE -ne 0) {
    Fail "Failed to export lens-mapping.json via run-as."
}

$mappingText = ($mappingLines -join [Environment]::NewLine)

try {
    $parsed = $mappingText | ConvertFrom-Json
}
catch {
    Fail "The exported lens mapping is not valid JSON: $($_.Exception.Message)"
}

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText(
    $outFile,
    $mappingText + [Environment]::NewLine,
    $utf8NoBom
)

$verified = @(
    $parsed.mappings |
        Where-Object {
            $null -ne $_.verifiedRole -and
            -not [string]::IsNullOrWhiteSpace([string]$_.verifiedRole)
        }
)

$readme = @"
# POCO F5 lens mapping capture

- Model: $($parsed.device.model)
- Device codename: $($parsed.device.device)
- Android: $($parsed.device.androidRelease)
- Capture time: $((Get-Date).ToString("o"))
- Camera IDs reported: $(@($parsed.cameraIds).Count)
- Camera IDs manually labeled: $($verified.Count)
- Verification method: live Camera2 preview + physical lens occlusion
- ADB serial: intentionally not stored

Only manually observed roles should be treated as verified.
"@

[System.IO.File]::WriteAllText(
    (Join-Path $outDir "README.md"),
    $readme,
    $utf8NoBom
)

$hashLines = Get-ChildItem -Path $outDir -File |
    Where-Object { $_.Name -ne "SHA256SUMS.txt" } |
    Sort-Object Name |
    ForEach-Object {
        $hash = (Get-FileHash -Path $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        "$hash  $($_.Name)"
    }

[System.IO.File]::WriteAllText(
    (Join-Path $outDir "SHA256SUMS.txt"),
    ($hashLines -join [Environment]::NewLine) + [Environment]::NewLine,
    $utf8NoBom
)

Write-Host ""
Write-Host "Lens mapping exported successfully:"
Write-Host $outFile