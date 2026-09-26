[CmdletBinding()]
param(
    [string]$Serial,
    [string]$ApkPath = "tools/camera2-probe/app/build/outputs/apk/debug/app-debug.apk",
    [string]$OutputRoot = "device/marble/camera2/captures",
    [string]$Package = "dev.alaa.pocof5.camera2probe",
    [switch]$Build,
    [switch]$YuvRuntime,
    [int]$TimeoutSeconds = 120,
    [int]$YuvTimeoutSeconds = 180
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Activity = ".MainActivity"
$YuvActivity = ".YuvProbeActivity"
$ExpectedProbeVersion = "0.3.1"
$ReportRelativePath = "files/camera2-report.json"
$StatusRelativePath = "files/camera2-probe-status.json"
$ErrorRelativePath = "files/camera2-probe-error.txt"
$YuvReportRelativePath = "files/yuv-runtime-report.json"

function Fail([string]$Message) {
    Write-Error $Message
    exit 1
}

function Invoke-Adb {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,
        [switch]$AllowFailure,
        [switch]$StdoutOnly
    )

    $previousErrorActionPreference = $ErrorActionPreference
    try {
        # Windows PowerShell 5 promotes native stderr to NativeCommandError when
        # ErrorActionPreference is Stop. adb legitimately emits status/warning
        # text on stderr, so never let the PowerShell wrapper abort the probe.
        $ErrorActionPreference = "Continue"
        if ($StdoutOnly) {
            # Keep stderr out of JSON payloads exported via exec-out.
            $output = & adb @Arguments
        }
        else {
            $output = & adb @Arguments 2>&1
        }
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }

    $text = ($output | ForEach-Object { "$_" }) -join [Environment]::NewLine
    if ($exitCode -ne 0 -and -not $AllowFailure) {
        throw (
            "adb " + ($Arguments -join " ") +
            " failed with exit code " + $exitCode +
            [Environment]::NewLine + $text
        )
    }

    return [pscustomobject]@{
        ExitCode = $exitCode
        Text = $text.Trim()
    }
}

if (-not (Get-Command adb -ErrorAction SilentlyContinue)) {
    Fail "adb was not found. Install Android SDK Platform-Tools and add adb to PATH."
}

if ($Build) {
    if (-not (Get-Command gradle -ErrorAction SilentlyContinue)) {
        Fail "Gradle was not found. Install Gradle or use the APK artifact produced by GitHub Actions."
    }

    Write-Host "Building Camera2 probe..."
    & gradle -p "tools/camera2-probe" ":app:assembleDebug" "-PprobeApplicationId=$Package"
    if ($LASTEXITCODE -ne 0) {
        Fail "Camera2 probe build failed."
    }
}

if (-not (Test-Path -LiteralPath $ApkPath)) {
    Fail "Probe APK not found at '$ApkPath'. Build it with -Build or download the camera2-probe-debug artifact from GitHub Actions."
}

$deviceResult = Invoke-Adb -Arguments @("devices")
$deviceLines = @(
    $deviceResult.Text -split "\r?\n" |
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

Write-Host "Installing Camera2 probe as package $Package..."
# A clean install prevents a stale probe package from masking an accidentally
# supplied app-debug.apk that belongs to a different Android application.
Invoke-Adb -Arguments @($adbPrefix + @("uninstall", $Package)) -AllowFailure | Out-Null
$install = Invoke-Adb -Arguments @($adbPrefix + @("install", "-r", $ApkPath)) -AllowFailure
if ($install.ExitCode -ne 0) {
    Fail ("APK installation failed." + [Environment]::NewLine + $install.Text)
}
if (-not [string]::IsNullOrWhiteSpace($install.Text)) {
    Write-Host $install.Text
}

$packageCheck = Invoke-Adb -Arguments @(
    $adbPrefix + @("shell", "pm", "path", $Package)
) -AllowFailure
if ($packageCheck.ExitCode -ne 0 -or
        [string]::IsNullOrWhiteSpace($packageCheck.Text) -or
        $packageCheck.Text -notmatch "^package:") {
    Fail (
        "The supplied APK installed successfully, but it is not package '$Package'." +
        [Environment]::NewLine +
        "Use the camera2-probe-debug artifact, not another app-debug.apk."
    )
}

$installedPackageDump = Invoke-Adb -Arguments @(
    $adbPrefix + @("shell", "dumpsys", "package", $Package)
) -AllowFailure
$versionMatch = [regex]::Match(
    $installedPackageDump.Text,
    "(?m)^\s*versionName=([^\s]+)"
)
if (-not $versionMatch.Success -or
        $versionMatch.Groups[1].Value -ne $ExpectedProbeVersion) {
    $actualVersion = if ($versionMatch.Success) {
        $versionMatch.Groups[1].Value
    }
    else {
        "<unknown>"
    }
    Fail (
        "Camera2 probe version mismatch." +
        [Environment]::NewLine +
        "Expected: $ExpectedProbeVersion" +
        [Environment]::NewLine +
        "Installed: $actualVersion" +
        [Environment]::NewLine +
        "Download the latest camera2-probe-debug artifact before retrying."
    )
}

Write-Host "Camera2 probe version: $ExpectedProbeVersion"
Write-Host "Launching probe and generating report..."
Invoke-Adb -Arguments @($adbPrefix + @("shell", "am", "force-stop", $Package)) -AllowFailure | Out-Null
Invoke-Adb -Arguments @(
    $adbPrefix + @(
        "shell", "run-as", $Package, "rm", "-f",
        $ReportRelativePath, $StatusRelativePath, $ErrorRelativePath
    )
) -AllowFailure | Out-Null
$launch = Invoke-Adb -Arguments @(
    $adbPrefix + @(
        "shell", "am", "start", "-W",
        "-n", "$Package/$Activity",
        "--ez", "autoGenerate", "true"
    )
) -AllowFailure
if ($launch.ExitCode -ne 0) {
    Fail ("Failed to launch the Camera2 probe." + [Environment]::NewLine + $launch.Text)
}

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$ready = $false
do {
    Start-Sleep -Milliseconds 500
    $readyCheck = Invoke-Adb -Arguments @(
        $adbPrefix + @("shell", "run-as", $Package, "test", "-f", $ReportRelativePath)
    ) -AllowFailure
    if ($readyCheck.ExitCode -eq 0) {
        $ready = $true
        break
    }
} while ((Get-Date) -lt $deadline)

if (-not $ready) {
    $failureTimestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $failureDir = Join-Path $OutputRoot ("failed-" + $failureTimestamp)
    New-Item -ItemType Directory -Path $failureDir -Force | Out-Null
    $utf8NoBomFailure = New-Object System.Text.UTF8Encoding($false)

    $statusExport = Invoke-Adb -Arguments @(
        $adbPrefix + @("exec-out", "run-as", $Package, "cat", $StatusRelativePath)
    ) -AllowFailure -StdoutOnly
    if ($statusExport.ExitCode -eq 0 -and
            -not [string]::IsNullOrWhiteSpace($statusExport.Text)) {
        [System.IO.File]::WriteAllText(
            (Join-Path $failureDir "camera2-probe-status.json"),
            $statusExport.Text + [Environment]::NewLine,
            $utf8NoBomFailure
        )
    }

    $errorExport = Invoke-Adb -Arguments @(
        $adbPrefix + @("exec-out", "run-as", $Package, "cat", $ErrorRelativePath)
    ) -AllowFailure -StdoutOnly
    if ($errorExport.ExitCode -eq 0 -and
            -not [string]::IsNullOrWhiteSpace($errorExport.Text)) {
        [System.IO.File]::WriteAllText(
            (Join-Path $failureDir "camera2-probe-error.txt"),
            $errorExport.Text + [Environment]::NewLine,
            $utf8NoBomFailure
        )
    }

    $packageDump = Invoke-Adb -Arguments @(
        $adbPrefix + @("shell", "dumpsys", "package", $Package)
    ) -AllowFailure
    [System.IO.File]::WriteAllText(
        (Join-Path $failureDir "package-dump.txt"),
        $packageDump.Text + [Environment]::NewLine,
        $utf8NoBomFailure
    )

    $activityDump = Invoke-Adb -Arguments @(
        $adbPrefix + @("shell", "dumpsys", "activity", "activities")
    ) -AllowFailure
    $activityLines = @(
        $activityDump.Text -split "\r?\n" |
            Where-Object {
                $_ -match [regex]::Escape($Package) -or
                $_ -match "mResumedActivity|topResumedActivity"
            }
    )
    [System.IO.File]::WriteAllText(
        (Join-Path $failureDir "activity-dump.txt"),
        ($activityLines -join [Environment]::NewLine) + [Environment]::NewLine,
        $utf8NoBomFailure
    )

    $logcat = Invoke-Adb -Arguments @(
        $adbPrefix + @("logcat", "-d", "-v", "threadtime", "-t", "1200")
    ) -AllowFailure
    $logLines = @(
        $logcat.Text -split "\r?\n" |
            Where-Object {
                $_ -match [regex]::Escape($Package) -or
                $_ -match "AndroidRuntime|CameraManager|CameraService|camera2probe"
            }
    )
    [System.IO.File]::WriteAllText(
        (Join-Path $failureDir "logcat.txt"),
        ($logLines -join [Environment]::NewLine) + [Environment]::NewLine,
        $utf8NoBomFailure
    )

    Write-Host ""
    Write-Host "Camera2 probe diagnostics:"
    Write-Host $failureDir
    Fail (
        "Timed out waiting for the Camera2 report after $TimeoutSeconds seconds." +
        [Environment]::NewLine +
        "Diagnostic files were saved under '$failureDir'."
    )
}

$reportExport = Invoke-Adb -Arguments @(
    $adbPrefix + @("exec-out", "run-as", $Package, "cat", $ReportRelativePath)
) -AllowFailure -StdoutOnly
if ($reportExport.ExitCode -ne 0) {
    Fail (
        "Failed to export the Camera2 report via run-as." +
        [Environment]::NewLine + $reportExport.Text
    )
}
$reportText = $reportExport.Text

try {
    $parsed = $reportText | ConvertFrom-Json
}
catch {
    Fail (
        "The exported report is not valid JSON: " +
        $_.Exception.Message +
        [Environment]::NewLine +
        "Payload prefix: " +
        $reportText.Substring(0, [Math]::Min(500, $reportText.Length))
    )
}

# Create the capture directory only after the base report has been exported and
# parsed successfully. A failed export can no longer leave a misleading empty
# timestamp directory behind.
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$outDir = Join-Path $OutputRoot $timestamp
New-Item -ItemType Directory -Path $outDir -Force | Out-Null
$outFile = Join-Path $outDir "camera2-report.json"

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($outFile, $reportText + [Environment]::NewLine, $utf8NoBom)

if ($YuvRuntime) {
    Write-Host "Running sustained YUV_420_888 runtime probe..."

    $grant = Invoke-Adb -Arguments @(
        $adbPrefix + @("shell", "pm", "grant", $Package, "android.permission.CAMERA")
    ) -AllowFailure
    if ($grant.ExitCode -ne 0) {
        Fail (
            "Failed to grant CAMERA permission to the debug probe package." +
            [Environment]::NewLine + $grant.Text
        )
    }

    Invoke-Adb -Arguments @(
        $adbPrefix + @("shell", "run-as", $Package, "rm", "-f", $YuvReportRelativePath)
    ) -AllowFailure | Out-Null
    $yuvLaunch = Invoke-Adb -Arguments @(
        $adbPrefix + @(
            "shell", "am", "start", "-W",
            "-n", "$Package/$YuvActivity",
            "--ez", "autoGenerate", "true"
        )
    ) -AllowFailure
    if ($yuvLaunch.ExitCode -ne 0) {
        Fail (
            "Failed to launch the YUV runtime probe." +
            [Environment]::NewLine + $yuvLaunch.Text
        )
    }

    $yuvDeadline = (Get-Date).AddSeconds($YuvTimeoutSeconds)
    $yuvReady = $false
    do {
        Start-Sleep -Milliseconds 500
        $yuvReadyCheck = Invoke-Adb -Arguments @(
            $adbPrefix + @("shell", "run-as", $Package, "test", "-f", $YuvReportRelativePath)
        ) -AllowFailure
        if ($yuvReadyCheck.ExitCode -eq 0) {
            $yuvReady = $true
            break
        }
    } while ((Get-Date) -lt $yuvDeadline)

    if (-not $yuvReady) {
        Fail "Timed out waiting for the YUV runtime report after $YuvTimeoutSeconds seconds."
    }

    $yuvOutFile = Join-Path $outDir "yuv-runtime-report.json"
    $yuvExport = Invoke-Adb -Arguments @(
        $adbPrefix + @("exec-out", "run-as", $Package, "cat", $YuvReportRelativePath)
    ) -AllowFailure -StdoutOnly
    if ($yuvExport.ExitCode -ne 0) {
        Fail (
            "Failed to export the YUV runtime report via run-as." +
            [Environment]::NewLine + $yuvExport.Text
        )
    }
    $yuvText = $yuvExport.Text

    try {
        $yuvParsed = $yuvText | ConvertFrom-Json
    }
    catch {
        Fail "The exported YUV runtime report is not valid JSON: $($_.Exception.Message)"
    }

    [System.IO.File]::WriteAllText(
        $yuvOutFile,
        $yuvText + [Environment]::NewLine,
        $utf8NoBom
    )

    $successCount = @($yuvParsed.cameras | Where-Object { $_.success -eq $true }).Count
    $cameraCount = @($yuvParsed.cameras).Count
    Write-Host "YUV runtime probe: $successCount/$cameraCount exposed camera IDs delivered sustained YUV frames."
}
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
- YUV runtime probe requested: $YuvRuntime
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