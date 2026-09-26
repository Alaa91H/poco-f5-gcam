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

$diagnosticPattern = "(?i)(" + (($fatalMarkers | ForEach-Object { [regex]::Escape($_) }) -join "|") + "|" + [regex]::Escape($PackageName) + "|CameraProvider|CameraService|CamX|CHI|QNN|CDSP|Gcam_Create|GxpCapi|gxp_host_late_binding|libgxp|DarwiNN|Tomte|almond|Unknown device code|Failed to get tuning|uncalibrated|Using tuning defaults|KeepAliveBroadcastReceiver|BackgroundServiceStartNotAllowedException|lib_aion_buffer|aion_context|AION|Gcam_AllSensorIdsUnique|GCamSensorIds|mjy\\.a\\(PG:\\d+\\)|sensor-ID uniqueness)"
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
            $_ -match "(?i)(Caused by:|NullPointerException|IllegalStateException|IllegalArgumentException|SecurityException|UnsatisfiedLinkError|ClassNotFoundException|NoClassDefFoundError|Resources(\$|\.)NotFoundException|dlopen failed|GxpCapi_|Gcam_Create|gxp_host_late_binding|libgxp|DarwiNN|Tomte|almond|KeepAliveBroadcastReceiver|BackgroundServiceStartNotAllowedException|lib_aion_buffer|aion_context|AION|Gcam_AllSensorIdsUnique|GCamSensorIds|GCamTopCameraId|GCamPhysicalCameraId|mjy\\.a\\(PG:\\d+\\))"
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
            $_ -match '(?i)(mjy\.a\(PG:\d+\)|Gcam_AllSensorIdsUnique)'
        } |
        Select-Object -Last 50
)
$sensorIdUniquenessCrashObserved = (
    $logcat -match '(?is)java\.lang\.IllegalArgumentException.*?\bat\s+mjy\.a\(PG:\d+\)'
)

$cameraSourceTopLines = @(
    $logLines |
        Where-Object {
            $_ -match '(?i)GCamTopCameraId'
        } |
        Select-Object -Last 100
)
$cameraSourceTopIds = New-Object System.Collections.Generic.List[string]
foreach ($line in $cameraSourceTopLines) {
    if ($line -match 'GCamTopCameraId\s*:\s*(?<camera>\S+)') {
        $cameraSourceTopIds.Add($Matches["camera"])
    }
}

$cameraSourcePhysicalLines = @(
    $logLines |
        Where-Object {
            $_ -match '(?i)GCamPhysicalCameraId'
        } |
        Select-Object -Last 100
)
$cameraSourcePhysicalIds = New-Object System.Collections.Generic.List[string]
foreach ($line in $cameraSourcePhysicalLines) {
    if ($line -match 'GCamPhysicalCameraId\s*:\s*(?<camera>\S+)') {
        $cameraSourcePhysicalIds.Add($Matches["camera"])
    }
}

$sensorVectorLines = @(
    $logLines |
        Where-Object {
            $_ -match '(?i)GCamSensorIds'
        } |
        Select-Object -Last 100
)
$sensorVectorIds = New-Object System.Collections.Generic.List[string]
foreach ($line in $sensorVectorLines) {
    if ($line -match 'GCamSensorIds\s*:\s*(?<sensor>\S+)') {
        $sensorVectorIds.Add($Matches["sensor"])
    }
}
$sensorVectorDuplicateIds = @(
    $sensorVectorIds |
        Group-Object |
        Where-Object { $_.Count -gt 1 } |
        ForEach-Object {
            [ordered]@{
                sensor = $_.Name
                count = $_.Count
            }
        }
)

# Correlate the Android Camera2 sources with the final GCam sensor vector per
# native-create thread. Source IDs are emitted while the vector is built, and
# GCamSensorIds is emitted immediately before Gcam_Create on that same thread.
$cameraSensorEventLines = @(
    $logLines |
        Where-Object {
            $_ -match '(?i)(GCamTopCameraId|GCamPhysicalCameraId|GCamSensorIds)'
        } |
        Select-Object -Last 300
)
$cameraSensorThreadGroups = [ordered]@{}
foreach ($line in $cameraSensorEventLines) {
    if ($line -notmatch '^\S+\s+\S+\s+(?<pid>\d+)\s+(?<tid>\d+)\s+\S+\s+(?<tag>GCamTopCameraId|GCamPhysicalCameraId|GCamSensorIds)\s*:\s*(?<value>\S+)') {
        continue
    }

    $threadKey = "$($Matches["pid"]):$($Matches["tid"])"
    if (-not $cameraSensorThreadGroups.Contains($threadKey)) {
        $cameraSensorThreadGroups[$threadKey] = [ordered]@{
            pid = $Matches["pid"]
            tid = $Matches["tid"]
            sources = New-Object System.Collections.Generic.List[object]
            sensors = New-Object System.Collections.Generic.List[string]
        }
    }

    $group = $cameraSensorThreadGroups[$threadKey]
    if ($Matches["tag"] -eq "GCamSensorIds") {
        $group.sensors.Add($Matches["value"])
    }
    else {
        $group.sources.Add([ordered]@{
            sourceType = if ($Matches["tag"] -eq "GCamTopCameraId") { "top_level" } else { "physical" }
            cameraId = $Matches["value"]
        })
    }
}

$sensorSourceMappings = New-Object System.Collections.Generic.List[object]
$sensorSourceMappingThreads = New-Object System.Collections.Generic.List[object]
$sensorVectorDuplicateIdsPerThread = New-Object System.Collections.Generic.List[object]
foreach ($entry in $cameraSensorThreadGroups.GetEnumerator()) {
    $group = $entry.Value
    $pairedCount = [Math]::Min($group.sources.Count, $group.sensors.Count)
    $complete = (
        $group.sources.Count -gt 0 -and
        $group.sources.Count -eq $group.sensors.Count
    )

    if ($complete) {
        for ($i = 0; $i -lt $pairedCount; $i++) {
            $sensorSourceMappings.Add([ordered]@{
                pid = $group.pid
                tid = $group.tid
                index = $i
                sourceType = $group.sources[$i].sourceType
                cameraId = $group.sources[$i].cameraId
                sensor = $group.sensors[$i]
            })
        }
    }

    $sensorSourceMappingThreads.Add([ordered]@{
        pid = $group.pid
        tid = $group.tid
        sourceCount = $group.sources.Count
        sensorCount = $group.sensors.Count
        complete = $complete
    })

    $group.sensors.ToArray() |
        Group-Object |
        Where-Object { $_.Count -gt 1 } |
        ForEach-Object {
            $sensorVectorDuplicateIdsPerThread.Add([ordered]@{
                pid = $group.pid
                tid = $group.tid
                sensor = $_.Name
                count = $_.Count
            })
        }
}

$cameraServiceConnectLines = @(
    $logLines |
        Where-Object {
            $_ -match '(?i)CameraService::connect call .*camera ID'
        } |
        Select-Object -Last 100
)
$cameraServiceConnectedIds = @(
    $cameraServiceConnectLines |
        ForEach-Object {
            if ($_ -match '(?i)camera ID\s+([^\s\)]+)') {
                $Matches[1]
            }
        } |
        Select-Object -Unique
)

$xiaomiMultiCameraGraphFailureLines = @(
    $logLines |
        Where-Object {
            $_ -match '(?i)(Cannot map logical camera type|Invalid logical camera id|MultiCameraSAT.*(failed|failure)|CreateUsecaseObject failed|Failed to initialize Multicamera|Feature graph manager initialization failed)'
        } |
        Select-Object -Last 150
)
$xiaomiMultiCameraGraphFailureObserved = $xiaomiMultiCameraGraphFailureLines.Count -gt 0

$logicalCameraMappingLines = @(
    $logLines |
        Where-Object {
            $_ -match '(?i)(Cannot map logical camera type|Invalid logical camera id|CreateUsecaseObject failed|Failed to initialize Multicamera)'
        } |
        Select-Object -Last 100
)
$logicalCameraMappingErrorsObserved = $logicalCameraMappingLines.Count -gt 0

$oneCameraOptionalNpeLines = @(
    $logLines |
        Where-Object {
            $_ -match '(?i)(Failed to start OneCamera|j\$\.util\.Optional\.of|ofe\.a\(PG:413\)|NullPointerException.*null object reference)'
        } |
        Select-Object -Last 100
)
$oneCameraOptionalNpeObserved = (
    $logcat -match '(?is)Failed to start OneCamera.*?Caused by:\s*java\.lang\.NullPointerException.*?j\$\.util\.Optional\.of.*?ofe\.a\(PG:413\)'
)

$googleAllowlistLines = @(
    $logLines |
        Where-Object {
            $_ -match '(?i)(GoogleCertificatesRslt: not allowed|Package not on allowlist|CBVerifier: Fail to register phenotypeflags)'
        } |
        Select-Object -Last 100
)
$googleAllowlistRejectionObserved = $googleAllowlistLines.Count -gt 0

$cameraStreamConfigurationFailureLines = @(
    $logLines |
        Where-Object {
            $_ -match '(?i)(Unsupported set of inputs/outputs provided|Failed to create capture session; configuration failed|configure_streams\(\).*max_buffers\s*:\s*0|Unable to configure stream .*Function not implemented|End CONFIG failed)'
        } |
        Select-Object -Last 100
)
$cameraStreamConfigurationFailureObserved = $cameraStreamConfigurationFailureLines.Count -gt 0

$oneCameraVendorRequestKeyNpeLines = @(
    $logLines |
        Where-Object {
            $_ -match '(?i)(upd\.<init>\(PG:3\)|mta\.a\(PG:720\)|Failed to start OneCamera \(retry disabled\)|NullPointerException.*null object reference)'
        } |
        Select-Object -Last 100
)
$oneCameraVendorRequestKeyNpeObserved = (
    $logcat -match '(?is)Failed to start OneCamera.*?Caused by:\s*java\.lang\.NullPointerException.*?upd\.<init>\(PG:3\).*?mta\.a\(PG:720\)'
)

$processAlive = -not [string]::IsNullOrWhiteSpace($finalPid)
$launcherAccepted = $launch.ExitCode -eq 0 -and $launch.Text -notmatch "(?i)(No activities found|monkey aborted)"
$topActivityMatches = $resumedLines.Count -gt 0
$startupPassed = $launcherAccepted -and $processAlive -and $fatalEvidence.Count -eq 0
$cameraPipelineCompatibilityPassed = (
    $startupPassed -and
    -not $logicalCameraMappingErrorsObserved -and
    -not $xiaomiMultiCameraGraphFailureObserved -and
    -not $cameraStreamConfigurationFailureObserved
)
# Keep runtimePassed as the startup/crash gate for backward compatibility.
$runtimePassed = $startupPassed

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
        launchPidSamples = $launchPidSamples.ToArray()
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
        cameraSourceTopLines = $cameraSourceTopLines
        cameraSourceTopIds = $cameraSourceTopIds.ToArray()
        cameraSourcePhysicalLines = $cameraSourcePhysicalLines
        cameraSourcePhysicalIds = $cameraSourcePhysicalIds.ToArray()
        sensorVectorLines = $sensorVectorLines
        sensorVectorIds = $sensorVectorIds.ToArray()
        sensorVectorDuplicateIds = $sensorVectorDuplicateIds
        sensorVectorDuplicateIdsPerThread = $sensorVectorDuplicateIdsPerThread.ToArray()
        sensorSourceMappingThreads = $sensorSourceMappingThreads.ToArray()
        sensorSourceMappings = $sensorSourceMappings.ToArray()
        cameraServiceConnectLines = $cameraServiceConnectLines
        cameraServiceConnectedIds = $cameraServiceConnectedIds
        xiaomiMultiCameraGraphFailureObserved = $xiaomiMultiCameraGraphFailureObserved
        xiaomiMultiCameraGraphFailureLines = $xiaomiMultiCameraGraphFailureLines
        logicalCameraMappingErrorsObserved = $logicalCameraMappingErrorsObserved
        logicalCameraMappingLines = $logicalCameraMappingLines
        oneCameraOptionalNpeObserved = $oneCameraOptionalNpeObserved
        oneCameraOptionalNpeLines = $oneCameraOptionalNpeLines
        googleAllowlistRejectionObserved = $googleAllowlistRejectionObserved
        googleAllowlistLines = $googleAllowlistLines
        cameraStreamConfigurationFailureObserved = $cameraStreamConfigurationFailureObserved
        cameraStreamConfigurationFailureLines = $cameraStreamConfigurationFailureLines
        oneCameraVendorRequestKeyNpeObserved = $oneCameraVendorRequestKeyNpeObserved
        oneCameraVendorRequestKeyNpeLines = $oneCameraVendorRequestKeyNpeLines
    }
    crashAnalysis = [ordered]@{
        fatalContextCount = $fatalEvidence.Count
        fatalContexts = $fatalEvidence.ToArray()
        rootCauseLineCount = $rootCauseLines.Count
        rootCauseLines = $rootCauseLines
        diagnosticLineCount = $diagnosticLines.Count
        diagnosticLines = $diagnosticLines
    }
    result = [ordered]@{
        startupPassed = $startupPassed
        cameraPipelineCompatibilityPassed = $cameraPipelineCompatibilityPassed
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
Write-Host "Top-level Camera2 IDs: $($cameraSourceTopIds -join ', ')"
Write-Host "Physical Camera2 IDs: $($cameraSourcePhysicalIds -join ', ')"
Write-Host "Sensor vector IDs: $($sensorVectorIds -join ', ')"
if ($sensorVectorDuplicateIdsPerThread.Count -gt 0) {
    Write-Host "Duplicate sensor vector IDs per create thread:"
    $sensorVectorDuplicateIdsPerThread | ForEach-Object {
        Write-Host ("  PID " + $_.pid + " TID " + $_.tid + ": " + $_.sensor + " x" + $_.count)
    }
}
else {
    Write-Host "Duplicate sensor vector IDs per create thread: none observed"
}
if ($sensorSourceMappings.Count -gt 0) {
    Write-Host "Camera2 -> GCam sensor mappings:"
    $sensorSourceMappings | ForEach-Object {
        Write-Host ("  PID " + $_.pid + " TID " + $_.tid + " [" + $_.sourceType + "] camera " + $_.cameraId + " -> " + $_.sensor)
    }
}
else {
    Write-Host "Camera2 -> GCam sensor mappings: none observed"
}
Write-Host "CameraService connected IDs: $($cameraServiceConnectedIds -join ', ')"
Write-Host "Xiaomi multi-camera graph failure observed: $xiaomiMultiCameraGraphFailureObserved"
Write-Host "Logical camera mapping errors observed: $logicalCameraMappingErrorsObserved"
Write-Host "OneCamera Optional NPE observed: $oneCameraOptionalNpeObserved"
Write-Host "Google allowlist rejection observed: $googleAllowlistRejectionObserved"
Write-Host "Camera stream configuration failure observed: $cameraStreamConfigurationFailureObserved"
Write-Host "OneCamera vendor request-key NPE observed: $oneCameraVendorRequestKeyNpeObserved"
Write-Host "Startup passed: $startupPassed"
Write-Host "Camera pipeline compatibility passed: $cameraPipelineCompatibilityPassed"
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

if ($Strict -and -not $cameraPipelineCompatibilityPassed) {
    exit 2
}
