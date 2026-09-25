[CmdletBinding()]
param(
    [string]$PackageName = "com.google.android.GoogleCamera",

    [ValidateRange(3, 60)]
    [int]$WaitSeconds = 12,

    [string]$OutputPath,

    [switch]$Strict
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Invoke-Adb {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,
        [switch]$AllowFailure
    )

    $output = & adb @Arguments 2>&1
    $exitCode = $LASTEXITCODE
    $text = ($output | ForEach-Object { "$_" }) -join [Environment]::NewLine

    if ($exitCode -ne 0 -and -not $AllowFailure) {
        throw ("adb " + ($Arguments -join " ") + " failed with exit code " + $exitCode + [Environment]::NewLine + $text)
    }

    return [pscustomobject]@{
        ExitCode = $exitCode
        Text = $text.Trim()
    }
}

function Get-Prop {
    param([Parameter(Mandatory = $true)][string]$Name)
    return (Invoke-Adb -Arguments @("shell", "getprop", $Name)).Text.Trim()
}

if (-not (Get-Command adb -ErrorAction SilentlyContinue)) {
    throw "adb was not found in PATH. Install Android SDK Platform-Tools first."
}

$state = (Invoke-Adb -Arguments @("get-state")).Text
if ($state -ne "device") {
    throw "ADB device is not ready. Current state: $state"
}

$device = Get-Prop -Name "ro.product.device"
$model = Get-Prop -Name "ro.product.model"
$sdk = Get-Prop -Name "ro.build.version.sdk"
$buildId = Get-Prop -Name "ro.build.id"
$allowedDevices = @("marble", "marblein")
$deviceMatches = $allowedDevices -contains $device

$packagePaths = Invoke-Adb -Arguments @("shell", "pm", "path", $PackageName) -AllowFailure
if ($packagePaths.ExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($packagePaths.Text)) {
    throw "$PackageName is not installed on the connected device."
}

$installedApkPaths = @(
    $packagePaths.Text -split "\r?\n" |
        Where-Object { $_ -match "^package:" } |
        ForEach-Object { $_.Substring("package:".Length).Trim() }
)

if ($installedApkPaths.Count -eq 0) {
    throw "Android did not report any installed APK path for $PackageName."
}

Invoke-Adb -Arguments @("shell", "am", "force-stop", $PackageName) -AllowFailure | Out-Null
Invoke-Adb -Arguments @("logcat", "-c") -AllowFailure | Out-Null

$launchStartedAt = (Get-Date).ToUniversalTime()
$launch = Invoke-Adb -Arguments @(
    "shell", "monkey",
    "-p", $PackageName,
    "-c", "android.intent.category.LAUNCHER",
    "1"
) -AllowFailure

Start-Sleep -Seconds 2
$earlyPid = (Invoke-Adb -Arguments @("shell", "pidof", $PackageName) -AllowFailure).Text.Trim()

Start-Sleep -Seconds ([Math]::Max(1, $WaitSeconds - 2))
$finalPid = (Invoke-Adb -Arguments @("shell", "pidof", $PackageName) -AllowFailure).Text.Trim()

$activityDump = (Invoke-Adb -Arguments @("shell", "dumpsys", "activity", "activities") -AllowFailure).Text
$resumedLines = @(
    $activityDump -split "\r?\n" |
        Where-Object {
            $_ -match "(?i)(topResumedActivity|mResumedActivity)" -and
            $_ -match [regex]::Escape($PackageName)
        } |
        Select-Object -First 20
)

$logcat = (Invoke-Adb -Arguments @("logcat", "-d", "-v", "threadtime", "-t", "8000") -AllowFailure).Text
$logLines = @($logcat -split "\r?\n")

$fatalMarkers = @(
    "FATAL EXCEPTION",
    "UnsatisfiedLinkError",
    "NoClassDefFoundError",
    "ClassNotFoundException",
    'Resources$NotFoundException',
    "Resources.NotFoundException",
    "VerifyError",
    "IncompatibleClassChangeError",
    "SecurityException",
    "dlopen failed",
    "Fatal signal",
    "Abort message"
)

$diagnosticPattern = "(?i)(" + (($fatalMarkers | ForEach-Object { [regex]::Escape($_) }) -join "|") + "|" + [regex]::Escape($PackageName) + "|CameraProvider|CameraService|CamX|CHI|QNN|CDSP)"
$diagnosticLines = @(
    $logLines |
        Where-Object { $_ -match $diagnosticPattern } |
        Select-Object -Last 1200
)

$fatalEvidence = New-Object System.Collections.Generic.List[string]
for ($i = 0; $i -lt $logLines.Count; $i++) {
    $line = $logLines[$i]
    $isFatalMarker = $line -match "(?i)(FATAL EXCEPTION|Fatal signal|Abort message|UnsatisfiedLinkError|NoClassDefFoundError|ClassNotFoundException|Resources(\$|\.)NotFoundException|VerifyError|IncompatibleClassChangeError|dlopen failed)"
    if (-not $isFatalMarker) {
        continue
    }

    $start = [Math]::Max(0, $i - 4)
    $end = [Math]::Min($logLines.Count - 1, $i + 12)
    $context = ($logLines[$start..$end] -join [Environment]::NewLine)

    if ($context -match [regex]::Escape($PackageName)) {
        $fatalEvidence.Add($context)
    }
}

$processAlive = -not [string]::IsNullOrWhiteSpace($finalPid)
$launcherAccepted = $launch.ExitCode -eq 0 -and $launch.Text -notmatch "(?i)(No activities found|monkey aborted)"
$topActivityMatches = $resumedLines.Count -gt 0
$runtimePassed = $launcherAccepted -and $processAlive -and $fatalEvidence.Count -eq 0

if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMdd-HHmmss")
    $OutputPath = Join-Path $PWD "device/marble/runtime/$stamp-pixel-camera-runtime.json"
}

$report = [ordered]@{
    schemaVersion = 1
    generatedAtUtc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    packageName = $PackageName
    device = [ordered]@{
        codename = $device
        model = $model
        sdk = $sdk
        buildId = $buildId
        expectedCodenames = $allowedDevices
        codenameMatches = $deviceMatches
    }
    installation = [ordered]@{
        apkPathCount = $installedApkPaths.Count
        apkPaths = $installedApkPaths
        isSplitInstall = $installedApkPaths.Count -gt 1
    }
    launch = [ordered]@{
        startedAtUtc = $launchStartedAt.ToString("yyyy-MM-ddTHH:mm:ssZ")
        waitSeconds = $WaitSeconds
        commandExitCode = $launch.ExitCode
        commandOutput = $launch.Text
        launcherAccepted = $launcherAccepted
        earlyPid = $earlyPid
        finalPid = $finalPid
        processAliveAfterWait = $processAlive
        topActivityMatchesPackage = $topActivityMatches
        resumedActivityLines = $resumedLines
    }
    crashAnalysis = [ordered]@{
        fatalContextCount = $fatalEvidence.Count
        fatalContexts = @($fatalEvidence)
        diagnosticLineCount = $diagnosticLines.Count
        diagnosticLines = $diagnosticLines
    }
    result = [ordered]@{
        runtimePassed = $runtimePassed
        reason = if ($runtimePassed) {
            "Launcher accepted the app, its process remained alive, and no package-associated fatal crash signature was found."
        }
        elseif (-not $launcherAccepted) {
            "Android did not accept a launcher activity for the package."
        }
        elseif (-not $processAlive) {
            "The package process was not alive after the observation window."
        }
        else {
            "Package-associated fatal crash evidence was found in logcat."
        }
    }
}

$outputFile = [System.IO.Path]::GetFullPath($OutputPath)
$outputDirectory = Split-Path -Parent $outputFile
if (-not [string]::IsNullOrWhiteSpace($outputDirectory)) {
    New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
}

$report | ConvertTo-Json -Depth 12 | Set-Content -Path $outputFile -Encoding UTF8

Write-Host "Pixel Camera runtime report:"
Write-Host $outputFile
Write-Host ""
Write-Host "Installed APK paths: $($installedApkPaths.Count)"
Write-Host "Launcher accepted: $launcherAccepted"
Write-Host "Process alive after $WaitSeconds seconds: $processAlive"
Write-Host "Fatal contexts: $($fatalEvidence.Count)"
Write-Host "Runtime passed: $runtimePassed"

if (-not $runtimePassed) {
    Write-Warning $report.result.reason
    if ($fatalEvidence.Count -gt 0) {
        Write-Host ""
        Write-Host "First fatal context:"
        Write-Host $fatalEvidence[0]
    }
}

if ($Strict -and -not $runtimePassed) {
    exit 2
}
