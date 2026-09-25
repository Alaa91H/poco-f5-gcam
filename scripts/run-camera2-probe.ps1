[CmdletBinding()]
param(
    [string]$Serial,
    [string]$ApkPath = "tools/camera2-probe/app/build/outputs/apk/debug/app-debug.apk",
    [string]$OutputRoot = "device/marble/camera2/captures",
    [switch]$Build,
    [int]$TimeoutSeconds = 30
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Package = "dev.alaa.pocof5.camera2probe"
$Activity = ".MainActivity"
$ReportRelativePath = "files/camera2-report.json"

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

    Write-Host "Building Camera2 probe..."
    & gradle -p "tools/camera2-probe" ":app:assembleDebug"
    if ($LASTEXITCODE -ne 0) {
        Fail "Camera2 probe build failed."
    }
}

if (-not (Test-Path -LiteralPath $ApkPath)) {
    Fail "Probe APK not found at '$ApkPath'. Build it with -Build or download the camera2-probe-debug artifact from GitHub Actions."
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
        Fail "No authorized Android device found."
    }
    if ($deviceLines.Count -gt 1) {
        Fail "More than one Android device is connected. Re-run with -Serial <adb-serial>."
    }
    $Serial = ($deviceLines[0] -split "\s+")[0]
}

$adbPrefix = @("-s", $Serial)

Write-Host "Installing Camera2 probe..."
& adb @adbPrefix install -r $ApkPath
if ($LASTEXITCODE -ne 0) {
    Fail "APK installation failed."
}

Write-Host "Launching probe and generating report..."
& adb @adbPrefix shell am force-stop $Package | Out-Null
& adb @adbPrefix shell am start -W -n "$Package/$Activity" --ez autoGenerate true | Out-Null
if ($LASTEXITCODE -ne 0) {
    Fail "Failed to launch the Camera2 probe."
}

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$ready = $false
do {
    Start-Sleep -Milliseconds 500
    & adb @adbPrefix shell run-as $Package test -f $ReportRelativePath 2>$null
    if ($LASTEXITCODE -eq 0) {
        $ready = $true
        break
    }
} while ((Get-Date) -lt $deadline)

if (-not $ready) {
    Fail "Timed out waiting for the Camera2 report after $TimeoutSeconds seconds. Open the app on the phone and check its status."
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$outDir = Join-Path $OutputRoot $timestamp
New-Item -ItemType Directory -Path $outDir -Force | Out-Null
$outFile = Join-Path $outDir "camera2-report.json"

$reportLines = & adb @adbPrefix exec-out run-as $Package cat $ReportRelativePath 2>&1
if ($LASTEXITCODE -ne 0) {
    Fail "Failed to export the Camera2 report via run-as."
}
$reportText = ($reportLines -join [Environment]::NewLine)

try {
    $parsed = $reportText | ConvertFrom-Json
}
catch {
    Fail "The exported report is not valid JSON: $($_.Exception.Message)"
}

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($outFile, $reportText + [Environment]::NewLine, $utf8NoBom)

$deviceCode = [string]$parsed.device.device
$model = [string]$parsed.device.model
if ($deviceCode -and $deviceCode -ne "marble") {
    Write-Warning "The connected device reports codename '$deviceCode' instead of 'marble'."
}

$readme = @"
# Camera2 probe capture

- Model: $model
- Device codename: $deviceCode
- Capture time: $((Get-Date).ToString("o"))
- Probe package: $Package
- ADB serial: intentionally not stored

The JSON report contains camera characteristics and device/build metadata only. Review it before publishing.
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
Write-Host "Camera2 report exported successfully:"
Write-Host $outFile
Write-Host ""
Write-Host "Next:"
Write-Host "powershell -ExecutionPolicy Bypass -File .\scripts\summarize-camera2-report.ps1 -ReportPath `"$outFile`""