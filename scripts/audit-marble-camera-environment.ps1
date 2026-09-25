[CmdletBinding()]
param(
    [string]$OutputPath,
    [switch]$Strict
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Invoke-AdbText {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,
        [switch]$AllowFailure
    )

    $output = & adb @Arguments 2>&1
    $exitCode = $LASTEXITCODE
    $text = ($output | ForEach-Object { "$_" }) -join [Environment]::NewLine

    if ($exitCode -ne 0 -and -not $AllowFailure) {
        $message = "adb " + ($Arguments -join " ") + " failed with exit code " + $exitCode + [Environment]::NewLine + $text
        throw $message
    }

    return [pscustomobject]@{
        ExitCode = $exitCode
        Text = $text.Trim()
    }
}

function Get-AdbProperty {
    param([Parameter(Mandatory = $true)][string]$Name)

    return (Invoke-AdbText -Arguments @("shell", "getprop", $Name)).Text.Trim()
}

if (-not (Get-Command adb -ErrorAction SilentlyContinue)) {
    throw "adb was not found in PATH. Install Android SDK Platform-Tools first."
}

$state = (Invoke-AdbText -Arguments @("get-state")).Text
if ($state -ne "device") {
    throw "ADB device is not ready. Current state: $state"
}

$timestamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$fileStamp = (Get-Date).ToUniversalTime().ToString("yyyyMMdd-HHmmss")

if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path $PWD "device/marble/runtime/audits/$fileStamp-camera-environment.json"
}

$expectedProperties = [ordered]@{
    "persist.camera.gyro.android" = "20"
    "persist.camera.HAL3.enabled" = "1"
    "persist.camera.is_type" = "4"
    "persist.camera.ois.enable" = "1"
    "persist.camera.ois.video" = "1"
    "persist.vendor.camera.ois.enable" = "1"
    "persist.vendor.camera.ois.video" = "1"
    "ro.product.mod_device" = "marble_global"
}

$propertyResults = [ordered]@{}
$mismatches = New-Object System.Collections.Generic.List[object]

foreach ($entry in $expectedProperties.GetEnumerator()) {
    $actual = Get-AdbProperty -Name $entry.Key
    $matches = $actual -eq $entry.Value

    $propertyResults[$entry.Key] = [ordered]@{
        actual = $actual
        expected = $entry.Value
        matchesExpectedEvolutionXBaseline = $matches
    }

    if (-not $matches) {
        $mismatches.Add([ordered]@{
            property = $entry.Key
            expected = $entry.Value
            actual = $actual
        })
    }
}

$deviceInfo = [ordered]@{
    manufacturer = Get-AdbProperty -Name "ro.product.manufacturer"
    brand = Get-AdbProperty -Name "ro.product.brand"
    model = Get-AdbProperty -Name "ro.product.model"
    device = Get-AdbProperty -Name "ro.product.device"
    product = Get-AdbProperty -Name "ro.product.name"
    androidRelease = Get-AdbProperty -Name "ro.build.version.release"
    sdk = Get-AdbProperty -Name "ro.build.version.sdk"
    securityPatch = Get-AdbProperty -Name "ro.build.version.security_patch"
    buildId = Get-AdbProperty -Name "ro.build.id"
}

$allowedDevices = @("marble", "marblein")
$deviceMatches = $allowedDevices -contains $deviceInfo.device

$cameraDumpResult = Invoke-AdbText -Arguments @("shell", "dumpsys", "media.camera") -AllowFailure
$cameraDump = $cameraDumpResult.Text
$cameraPatterns = @(
    "Camera ID",
    "Device version",
    "Facing",
    "Resource cost",
    "Conflicting",
    "API1",
    "API2",
    "torch",
    "CameraService"
)
$cameraSummary = @()
if (-not [string]::IsNullOrWhiteSpace($cameraDump)) {
    $cameraSummary = @(
        $cameraDump -split "\r?\n" |
            Select-String -Pattern $cameraPatterns -SimpleMatch |
            ForEach-Object { $_.Line.Trim() } |
            Select-Object -First 500
    )
}

$logcatResult = Invoke-AdbText -Arguments @(
    "shell", "logcat", "-d", "-v", "threadtime", "-t", "5000"
) -AllowFailure

$diagnosticRegex = "(?i)(camera\.provider|camx|chi\b|fastrpc|qnn|cdsp|unsatisfiedlinkerror|dlopen failed|camera.*fatal|camera.*error)"
$diagnosticLines = @()
if (-not [string]::IsNullOrWhiteSpace($logcatResult.Text)) {
    $diagnosticLines = @(
        $logcatResult.Text -split "\r?\n" |
            Where-Object { $_ -match $diagnosticRegex } |
            Select-Object -Last 1000
    )
}

$errorRegex = "(?i)(fatal|unsatisfiedlinkerror|dlopen failed|failed|error)"
$errorLines = @($diagnosticLines | Where-Object { $_ -match $errorRegex })

$report = [ordered]@{
    schemaVersion = 1
    generatedAtUtc = $timestamp
    mode = "read-only"
    device = $deviceInfo
    validation = [ordered]@{
        expectedDeviceCodenames = $allowedDevices
        deviceCodenameMatches = $deviceMatches
        evolutionXBaselinePropertiesMatch = $mismatches.Count -eq 0
        propertyMismatchCount = $mismatches.Count
        propertyMismatches = @($mismatches)
    }
    cameraProperties = $propertyResults
    cameraService = [ordered]@{
        available = $cameraDumpResult.ExitCode -eq 0
        summaryLines = $cameraSummary
    }
    diagnostics = [ordered]@{
        logcatCommandSucceeded = $logcatResult.ExitCode -eq 0
        matchingLineCount = $diagnosticLines.Count
        likelyErrorLineCount = $errorLines.Count
        lines = $diagnosticLines
    }
}

$outputFile = [System.IO.Path]::GetFullPath($OutputPath)
$outputDirectory = Split-Path -Parent $outputFile
if (-not [string]::IsNullOrWhiteSpace($outputDirectory)) {
    New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
}

$report | ConvertTo-Json -Depth 10 | Set-Content -Path $outputFile -Encoding UTF8

Write-Host "Camera environment audit written to:"
Write-Host $outputFile
Write-Host ""
Write-Host "Device codename: $($deviceInfo.device)"
Write-Host "Evolution X camera property mismatches: $($mismatches.Count)"
Write-Host "Camera diagnostic log lines: $($diagnosticLines.Count)"
Write-Host "Likely error lines: $($errorLines.Count)"

if ($Strict -and ((-not $deviceMatches) -or $mismatches.Count -gt 0)) {
    Write-Error "Strict camera environment audit failed."
    exit 2
}
