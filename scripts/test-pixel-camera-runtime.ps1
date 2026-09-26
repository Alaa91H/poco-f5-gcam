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

    $previousErrorActionPreference = $ErrorActionPreference
    try {
        # Windows PowerShell 5 surfaces native stderr as NativeCommandError when
        # ErrorActionPreference is Stop. adb/monkey legitimately writes status
        # lines to stderr even when the native exit code is zero, so capture both
        # streams without allowing PowerShell's wrapper error to terminate us.
        $ErrorActionPreference = "Continue"
        $output = & adb @Arguments 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }

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

# Wake the display before launching. Android 17 can classify a receiver-started
# service as background work when the device is sleeping, which can obscure the
# real camera startup result with BackgroundServiceStartNotAllowedException.
Invoke-Adb -Arguments @("shell", "input", "keyevent", "KEYCODE_WAKEUP") -AllowFailure | Out-Null
Start-Sleep -Milliseconds 500

Invoke-Adb -Arguments @("shell", "am", "force-stop", $PackageName) -AllowFailure | Out-Null
Invoke-Adb -Arguments @("logcat", "-c") -AllowFailure | Out-Null

$launchStartedAt = (Get-Date).ToUniversalTime()
$launch = Invoke-Adb -Arguments @(
    "shell", "monkey",
    "-p", $PackageName,
    "-c", "android.intent.category.LAUNCHER",
    "1"
) -AllowFailure

$launchPidSamples = New-Object System.Collections.Generic.List[string]
$firstObservedPid = ""
for ($attempt = 0; $attempt -lt 12; $attempt++) {
    $samplePid = (Invoke-Adb -Arguments @("shell", "pidof", $PackageName) -AllowFailure).Text.Trim()
    if (-not [string]::IsNullOrWhiteSpace($samplePid)) {
        $launchPidSamples.Add($samplePid)
        if ([string]::IsNullOrWhiteSpace($firstObservedPid)) {
            $firstObservedPid = $samplePid
        }
    }
    Start-Sleep -Milliseconds 100
}

Start-Sleep -Milliseconds 800
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

$diagnosticPattern = "(?i)(" + (($fatalMarkers | ForEach-Object { [regex]::Escape($_) }) -join "|") + "|" + [regex]::Escape($PackageName) + "|CameraProvider|CameraService|CamX|CHI|QNN|CDSP|Gcam_Create|GxpCapi|gxp_host_late_binding|libgxp|DarwiNN|Tomte|almond|Unknown device code|Failed to get tuning|uncalibrated|Using tuning defaults|KeepAliveBroadcastReceiver|BackgroundServiceStartNotAllowedException|lib_aion_buffer|aion_context|AION|Gcam_AllSensorIdsUnique|mjy\\.a\\(PG:1626\\)|sensor-ID uniqueness)"
$diagnosticLines = @(
    $logLines |
        Where-Object { $_ -match $diagnosticPattern } |
        Select-Object -Last 1200
)

$fatalEvidence = New-Object System.Collections.Generic.List[string]
for ($i = 0; $i -lt $logLines.Count; $i++) {
    $line = $logLines[$i]
    # A bare native "dlopen failed" line is diagnostic, not necessarily fatal.
    # Pixel Camera probes optional Pixel-only libraries such as libgxp.so on
    # non-Tensor hardware; only a real Java/native fatal marker should fail a
    # run by itself. Loader failures remain in diagnosticLines/rootCauseLines.
    $isFatalMarker = $line -match "(?i)(FATAL EXCEPTION|Fatal signal|Abort message|UnsatisfiedLinkError|NoClassDefFoundError|ClassNotFoundException|Resources(\$|\.)NotFoundException|VerifyError|IncompatibleClassChangeError)"
    if (-not $isFatalMarker) {
        continue
    }

    $start = [Math]::Max(0, $i - 4)
    # Keep enough lines to include nested "Caused by:" chains. The previous
    # 12-line tail often stopped before the actual provider root cause.
    $end = [Math]::Min($logLines.Count - 1, $i + 80)
    $context = ($logLines[$start..$end] -join [Environment]::NewLine)

    if ($context -match [regex]::Escape($PackageName)) {
        $fatalEvidence.Add($context)
    }
}

$rootCauseLines = @(
    $logLines |
        Where-Object {
            $_ -match "(?i)(Caused by:|NullPointerException|IllegalStateException|IllegalArgumentException|SecurityException|UnsatisfiedLinkError|ClassNotFoundException|NoClassDefFoundError|Resources(\$|\.)NotFoundException|dlopen failed|GxpCapi_|Gcam_Create|gxp_host_late_binding|libgxp|DarwiNN|Tomte|almond|KeepAliveBroadcastReceiver|BackgroundServiceStartNotAllowedException|lib_aion_buffer|aion_context|AION|Gcam_AllSensorIdsUnique|mjy\\.a\\(PG:1626\\))"
        } |
        Select-Object -Last 200
)

$fatalProcessPids = @(
    $logLines |
        ForEach-Object {
            if ($_ -match ("Process:\s*" + [regex]::Escape($PackageName) + ",\s*PID:\s*(\d+)")) {
                $Matches[1]
            }
        } |
        Select-Object -Unique
)
$pidReplacementObserved = (
    -not [string]::IsNullOrWhiteSpace($firstObservedPid) -and
    -not [string]::IsNullOrWhiteSpace($finalPid) -and
    $firstObservedPid -ne $finalPid
)
$fatalPidDiffersFromFinalPid = @(
    $fatalProcessPids |
        Where-Object {
            -not [string]::IsNullOrWhiteSpace($finalPid) -and $_ -ne $finalPid
        }
).Count -gt 0

$tuningUncalibratedLines = @(
    $logLines |
        Where-Object {
            $_ -match '(?i)Unknown device code.*Treating as "uncalibrated"'
        } |
        Select-Object -Last 50
)

$tuningAbortLines = @(
    $logLines |
        Where-Object {
            $_ -match '(?i)(Unknown device code.*Aborting|Failed to get tuning for device code.*Aborting)'
        } |
        Select-Object -Last 50
)

$tuningDefaultsLines = @(
    $logLines |
        Where-Object {
            $_ -match '(?i)(Unsupported sensor ID.*Using tuning defaults|Using tuning defaults)'
        } |
        Select-Object -Last 50
)

$tuningFallbackObserved = $tuningUncalibratedLines.Count -gt 0
$tuningAbortObserved = $tuningAbortLines.Count -gt 0
$tuningDefaultsObserved = $tuningDefaultsLines.Count -gt 0
$tuningState = if ($tuningAbortObserved) {
    "abort_observed"
}
elseif ($tuningFallbackObserved) {
    "uncalibrated_fallback_observed"
}
elseif ($tuningDefaultsObserved) {
    "sensor_defaults_observed"
}
else {
    "not_observed"
}

$keepAliveBackgroundCrashLines = @(
    $logLines |
        Where-Object {
            $_ -match "(?i)(KeepAliveBroadcastReceiver|BackgroundServiceStartNotAllowedException)"
        } |
        Select-Object -Last 50
)
$aionMissingLibraryLines = @(
    $logLines |
        Where-Object {
            $_ -match '(?i)lib_aion_buffer\.so.*(not found|dlopen failed)'
        } |
        Select-Object -Last 50
)
$aionFatalCheckLines = @(
    $logLines |
        Where-Object {
            $_ -match '(?i)(aion_context\.cc.*Check failed: valid_|Check failed: valid_)'
        } |
        Select-Object -Last 50
)
$keepAliveBackgroundCrashObserved = $keepAliveBackgroundCrashLines.Count -gt 0
$aionMissingLibraryObserved = $aionMissingLibraryLines.Count -gt 0
$aionFatalCheckObserved = $aionFatalCheckLines.Count -gt 0

$sensorIdUniquenessCrashLines = @(
    $logLines |
        Where-Object {
            $_ -match '(?i)(mjy\.a\(PG:1626\)|Gcam_AllSensorIdsUnique)'
        } |
        Select-Object -Last 50
)
$sensorIdUniquenessCrashObserved = (
    $logcat -match '(?is)java\.lang\.IllegalArgumentException.*?\bat\s+mjy\.a\(PG:1626\)'
)

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
        firstObservedPid = $firstObservedPid
        launchPidSamples = @($launchPidSamples)
        earlyPid = $earlyPid
        finalPid = $finalPid
        processAliveAfterWait = $processAlive
        pidReplacementObserved = $pidReplacementObserved
        fatalProcessPids = $fatalProcessPids
        fatalPidDiffersFromFinalPid = $fatalPidDiffersFromFinalPid
        topActivityMatchesPackage = $topActivityMatches
        resumedActivityLines = $resumedLines
    }
    tuningAnalysis = [ordered]@{
        state = $tuningState
        uncalibratedFallbackObserved = $tuningFallbackObserved
        abortObserved = $tuningAbortObserved
        sensorDefaultsObserved = $tuningDefaultsObserved
        uncalibratedLines = $tuningUncalibratedLines
        abortLines = $tuningAbortLines
        defaultsLines = $tuningDefaultsLines
    }
    compatibilityAnalysis = [ordered]@{
        keepAliveBackgroundCrashObserved = $keepAliveBackgroundCrashObserved
        keepAliveBackgroundCrashLines = $keepAliveBackgroundCrashLines
        aionMissingLibraryObserved = $aionMissingLibraryObserved
        aionMissingLibraryLines = $aionMissingLibraryLines
        aionFatalCheckObserved = $aionFatalCheckObserved
        aionFatalCheckLines = $aionFatalCheckLines
        sensorIdUniquenessCrashObserved = $sensorIdUniquenessCrashObserved
        sensorIdUniquenessCrashLines = $sensorIdUniquenessCrashLines
    }
    crashAnalysis = [ordered]@{
        fatalContextCount = $fatalEvidence.Count
        fatalContexts = @($fatalEvidence)
        rootCauseLineCount = $rootCauseLines.Count
        rootCauseLines = $rootCauseLines
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
Write-Host "First observed PID: $firstObservedPid"
Write-Host "PID replacement observed: $pidReplacementObserved"
Write-Host "Fatal PID differs from final PID: $fatalPidDiffersFromFinalPid"
Write-Host "Fatal contexts: $($fatalEvidence.Count)"
Write-Host "Root-cause lines: $($rootCauseLines.Count)"
Write-Host "GCam tuning state: $tuningState"
Write-Host "Uncalibrated fallback observed: $tuningFallbackObserved"
Write-Host "Tuning abort observed: $tuningAbortObserved"
Write-Host "KeepAlive background crash observed: $keepAliveBackgroundCrashObserved"
Write-Host "AION missing library observed: $aionMissingLibraryObserved"
Write-Host "AION fatal check observed: $aionFatalCheckObserved"
Write-Host "Sensor-ID uniqueness crash observed: $sensorIdUniquenessCrashObserved"
Write-Host "Runtime passed: $runtimePassed"

if (-not $runtimePassed) {
    Write-Warning $report.result.reason
    if ($fatalEvidence.Count -gt 0) {
        Write-Host ""
        Write-Host "First fatal context:"
        Write-Host $fatalEvidence[0]
    }
    if ($rootCauseLines.Count -gt 0) {
        Write-Host ""
        Write-Host "Root-cause lines:"
        $rootCauseLines | Select-Object -Last 40 | ForEach-Object { Write-Host $_ }
    }
}

if ($Strict -and -not $runtimePassed) {
    exit 2
}
