[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ApkPath,

    [string]$CandidateVersion,
    [string]$Serial,
    [string]$PolicyPath = "device/marble/upstream-policy.json",
    [string]$LockPath = "device/marble/upstream/pixel-camera.json",
    [string]$ApprovedPath = "device/marble/upstream/pixel-camera-approved.json",
    [string]$OutputRoot = "device/marble/upstream/validation",
    [int]$StabilitySeconds = 8,

    [switch]$AllowDowngrade,
    [switch]$ConfirmMainPreview,
    [switch]$ConfirmMainCapture,
    [switch]$ConfirmFrontPreview
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Fail([string]$Message) {
    Write-Error $Message
    exit 1
}

function New-Check([string]$Status, [string]$Detail) {
    return [ordered]@{
        status = $Status
        detail = $Detail
    }
}

function Find-Python {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        return @("py", "-3")
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        return @("python")
    }
    return $null
}

function Invoke-PixelCameraInstall {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string[]]$AdbPrefix,

        [switch]$AllowDowngrade
    )

    $resolvedPath = (Resolve-Path -LiteralPath $Path).Path
    $extension = [System.IO.Path]::GetExtension($resolvedPath).ToLowerInvariant()

    $installFlags = @("-r")
    if ($AllowDowngrade) {
        $installFlags += "-d"
    }

    if ($extension -eq ".apk") {
        $args = @("install") + $installFlags + @($resolvedPath)
        $output = & adb @AdbPrefix @args 2>&1
        $exitCode = $LASTEXITCODE

        return [pscustomobject]@{
            Mode = "single-apk"
            PayloadCount = 1
            ExitCode = $exitCode
            Output = @($output)
        }
    }

    if ($extension -ne ".apkm") {
        throw "Unsupported package format '$extension'. Use a .apk or APKMirror .apkm bundle."
    }

    $tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
        "poco-f5-gcam-apkm-" + [guid]::NewGuid().ToString("N")
    )
    $extractRoot = Join-Path $tempRoot "extracted"

    New-Item -ItemType Directory -Path $extractRoot -Force | Out-Null

    try {
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [System.IO.Compression.ZipFile]::ExtractToDirectory(
            $resolvedPath,
            $extractRoot
        )

        $splitApks = @(
            Get-ChildItem -LiteralPath $extractRoot -Recurse -File -Filter "*.apk" |
                Sort-Object FullName
        )

        if ($splitApks.Count -eq 0) {
            throw "The APKM bundle did not contain any APK payloads."
        }

        $apkPaths = @($splitApks | ForEach-Object { $_.FullName })
        $args = @("install-multiple") + $installFlags + $apkPaths
        $output = & adb @AdbPrefix @args 2>&1
        $exitCode = $LASTEXITCODE

        return [pscustomobject]@{
            Mode = "apkm-install-multiple"
            PayloadCount = $apkPaths.Count
            ExitCode = $exitCode
            Output = @($output)
        }
    }
    finally {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

if (-not (Get-Command adb -ErrorAction SilentlyContinue)) {
    Fail "adb was not found. Install Android SDK Platform-Tools and add adb to PATH."
}
if (-not (Test-Path -LiteralPath $ApkPath)) {
    Fail "Package file not found: $ApkPath"
}
if (-not (Test-Path -LiteralPath $PolicyPath)) {
    Fail "Policy not found: $PolicyPath"
}
if (-not (Test-Path -LiteralPath $LockPath)) {
    Fail "Discovery lock not found: $LockPath"
}

$policy = Get-Content -LiteralPath $PolicyPath -Raw -Encoding UTF8 | ConvertFrom-Json
$lock = Get-Content -LiteralPath $LockPath -Raw -Encoding UTF8 | ConvertFrom-Json
$expectedPackage = [string]$policy.compatibility.package_name

$candidates = @()
if ($null -ne $lock.candidates) {
    $candidates = @($lock.candidates)
}
elseif ($null -ne $lock.selected) {
    $candidates = @($lock.selected)
}

if ($candidates.Count -eq 0) {
    Fail "The discovery lock contains no candidates."
}

if (-not $CandidateVersion) {
    if ($null -ne $lock.candidate -and $lock.candidate.version) {
        $CandidateVersion = [string]$lock.candidate.version
    }
    else {
        $CandidateVersion = [string]$candidates[0].version
    }
}

$candidate = $candidates | Where-Object {
    [string]$_.version -eq $CandidateVersion
} | Select-Object -First 1

if ($null -eq $candidate) {
    Fail "Candidate version '$CandidateVersion' is not tracked in $LockPath."
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
$checks = [ordered]@{}

$manufacturer = (& adb @adbPrefix shell getprop ro.product.manufacturer 2>$null | Out-String).Trim()
$model = (& adb @adbPrefix shell getprop ro.product.model 2>$null | Out-String).Trim()
$device = (& adb @adbPrefix shell getprop ro.product.device 2>$null | Out-String).Trim()
$android = (& adb @adbPrefix shell getprop ro.build.version.release 2>$null | Out-String).Trim()
$sdk = (& adb @adbPrefix shell getprop ro.build.version.sdk 2>$null | Out-String).Trim()

Write-Host "Validating Pixel Camera candidate $CandidateVersion"
Write-Host "Device: $manufacturer $model ($device), Android $android / API $sdk"
Write-Host "Expected package: $expectedPackage"
Write-Host ""

try {
    $installResult = Invoke-PixelCameraInstall `
        -Path $ApkPath `
        -AdbPrefix $adbPrefix `
        -AllowDowngrade:$AllowDowngrade
}
catch {
    $installResult = [pscustomobject]@{
        Mode = "package-prepare-failed"
        PayloadCount = 0
        ExitCode = 1
        Output = @($_.Exception.Message)
    }
}

$installOutput = @($installResult.Output)
$installOk = ($installResult.ExitCode -eq 0) -and (
    ($installOutput -join "`n") -match "(?i)success"
)

if ($installOk) {
    $checks["install"] = New-Check "pass" (
        "ADB installation succeeded via $($installResult.Mode) " +
        "with $($installResult.PayloadCount) APK payload(s)."
    )
}
else {
    $checks["install"] = New-Check "fail" (
        (($installOutput -join " ") -replace "\s+", " ").Trim()
    )
}

$packageList = & adb @adbPrefix shell pm list packages $expectedPackage 2>&1
$packagePresent = ($LASTEXITCODE -eq 0) -and (($packageList -join "`n") -match [regex]::Escape($expectedPackage))

if ($packagePresent) {
    $checks["package-match"] = New-Check "pass" "Expected package is installed."
}
else {
    $checks["package-match"] = New-Check "fail" "Expected package '$expectedPackage' was not found after installation."
}

$installedVersion = ""
if ($packagePresent) {
    $packageDump = (& adb @adbPrefix shell dumpsys package $expectedPackage 2>&1 | Out-String)
    $versionMatch = [regex]::Match($packageDump, "(?m)^\s*versionName=(.+?)\s*$")
    if ($versionMatch.Success) {
        $installedVersion = $versionMatch.Groups[1].Value.Trim()
    }
}

if ($installedVersion -eq $CandidateVersion) {
    $checks["version-match"] = New-Check "pass" "Installed versionName matches candidate."
}
elseif ([string]::IsNullOrWhiteSpace($installedVersion)) {
    $checks["version-match"] = New-Check "fail" "Unable to read installed versionName."
}
else {
    $checks["version-match"] = New-Check "fail" "Installed versionName '$installedVersion' does not match '$CandidateVersion'."
}

if ($packagePresent) {
    & adb @adbPrefix shell pm grant $expectedPackage android.permission.CAMERA 2>$null | Out-Null
}

& adb @adbPrefix logcat -c 2>$null | Out-Null

$resolvedActivity = (& adb @adbPrefix shell cmd package resolve-activity --brief $expectedPackage 2>&1 | Out-String).Trim()
$launchable = (-not [string]::IsNullOrWhiteSpace($resolvedActivity)) -and
              ($resolvedActivity -notmatch "(?i)no activity found|unable")

if ($launchable) {
    $checks["launchable"] = New-Check "pass" "Resolved launcher activity: $resolvedActivity"
}
else {
    $checks["launchable"] = New-Check "fail" "No launcher activity could be resolved."
}

$launchOutput = ""
if ($launchable) {
    $launchOutput = (& adb @adbPrefix shell monkey -p $expectedPackage -c android.intent.category.LAUNCHER 1 2>&1 | Out-String).Trim()
}

if ($launchable -and $LASTEXITCODE -eq 0) {
    Start-Sleep -Seconds $StabilitySeconds
    $pidText = (& adb @adbPrefix shell pidof $expectedPackage 2>$null | Out-String).Trim()
    if ($pidText -match "^\d+(?:\s+\d+)*$") {
        $checks["process-stable"] = New-Check "pass" "Process remained alive for at least $StabilitySeconds seconds."
    }
    else {
        $checks["process-stable"] = New-Check "fail" "Package process was not alive after $StabilitySeconds seconds."
    }
}
else {
    $checks["process-stable"] = New-Check "fail" "Application could not be launched."
}

$logcat = (& adb @adbPrefix logcat -d -v brief 2>&1 | Out-String)
$packageMentioned = $logcat -match [regex]::Escape($expectedPackage)
$fatalMentioned = $logcat -match "(?i)FATAL EXCEPTION|Fatal signal|am_crash"

if ($packageMentioned -and $fatalMentioned) {
    $checks["no-fatal-crash"] = New-Check "fail" "Crash markers were detected in logcat after launch."
}
else {
    $checks["no-fatal-crash"] = New-Check "pass" "No package-associated fatal crash marker was detected after launch."
}

if ($ConfirmMainPreview) {
    $checks["main-preview-confirmed"] = New-Check "pass" "User confirmed live main-camera preview."
}
else {
    $checks["main-preview-confirmed"] = New-Check "fail" "Not confirmed. Re-run with -ConfirmMainPreview after visually verifying preview."
}

if ($ConfirmMainCapture) {
    $checks["main-capture-confirmed"] = New-Check "pass" "User confirmed a successful main-camera capture."
}
else {
    $checks["main-capture-confirmed"] = New-Check "fail" "Not confirmed. Re-run with -ConfirmMainCapture after taking and reviewing a photo."
}

if ($ConfirmFrontPreview) {
    $checks["front-preview-confirmed"] = New-Check "pass" "User confirmed front-camera preview."
}
else {
    $checks["front-preview-confirmed"] = New-Check "fail" "Not confirmed. Re-run with -ConfirmFrontPreview after verifying the front camera."
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null
$resultPath = Join-Path $OutputRoot "$timestamp-$CandidateVersion.json"

$result = [ordered]@{
    schema_version = 1
    candidate_version = $CandidateVersion
    package_name = $expectedPackage
    installed_version = $installedVersion
    install_mode = [string]$installResult.Mode
    install_payload_count = [int]$installResult.PayloadCount
    candidate_release_url = [string]$candidate.release_url
    device = [ordered]@{
        manufacturer = $manufacturer
        model = $model
        codename = $device
        android_version = $android
        api_level = $sdk
    }
    checks = $checks
    validated_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
}

$json = $result | ConvertTo-Json -Depth 8
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText(
    $resultPath,
    $json + [Environment]::NewLine,
    $utf8NoBom
)

Write-Host ""
Write-Host "Validation result:"
Write-Host $resultPath

$python = Find-Python
if ($null -eq $python) {
    Write-Warning "Python was not found. The validation result was saved but was not evaluated for promotion."
    Write-Host "Run later:"
    Write-Host "python scripts/pixel_camera_gate.py evaluate --result `"$resultPath`""
    exit 0
}

Write-Host ""
Write-Host "Evaluating promotion gate..."

if ($python[0] -eq "py") {
    & py -3 scripts/pixel_camera_gate.py evaluate `
        --policy $PolicyPath `
        --lock $LockPath `
        --result $resultPath `
        --approved $ApprovedPath
}
else {
    & python scripts/pixel_camera_gate.py evaluate `
        --policy $PolicyPath `
        --lock $LockPath `
        --result $resultPath `
        --approved $ApprovedPath
}

$gateExit = $LASTEXITCODE
if ($gateExit -eq 0) {
    Write-Host ""
    Write-Host "Candidate promoted to last-known-good."
    exit 0
}
elseif ($gateExit -eq 2) {
    Write-Warning "Candidate was not promoted. Existing last-known-good remains active if one exists."
    exit 2
}
else {
    Fail "Promotion gate evaluation failed."
}