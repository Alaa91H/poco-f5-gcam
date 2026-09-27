[CmdletBinding()]
param(
    [string]$OutputDirectory,
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
        throw ("adb " + ($Arguments -join " ") + " failed with exit code " +
            $exitCode + [Environment]::NewLine + $text)
    }

    return [pscustomobject]@{
        ExitCode = $exitCode
        Text = $text.Trim()
    }
}

function Invoke-PythonJson {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ScriptPath,
        [Parameter(Mandatory = $true)]
        [string]$LibraryPath,
        [Parameter(Mandatory = $true)]
        [string]$ReportPath
    )

    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        & $pythonCommand.Source $ScriptPath $LibraryPath --output $ReportPath --pretty | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Python ELF analyzer failed with exit code $LASTEXITCODE."
        }
        return
    }

    $pyCommand = Get-Command py -ErrorAction SilentlyContinue
    if ($pyCommand) {
        & $pyCommand.Source -3 $ScriptPath $LibraryPath --output $ReportPath --pretty | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Python ELF analyzer failed with exit code $LASTEXITCODE."
        }
        return
    }

    throw "Python 3 was not found in PATH. Install Python 3 or enable the Windows py launcher."
}

if (-not (Get-Command adb -ErrorAction SilentlyContinue)) {
    throw "adb was not found in PATH. Install Android SDK Platform-Tools first."
}

$state = (Invoke-AdbText -Arguments @("get-state")).Text
if ($state -ne "device") {
    throw "ADB device is not ready. Current state: $state"
}

$deviceCodename = (Invoke-AdbText -Arguments @(
    "shell", "getprop", "ro.product.device"
)).Text.Trim()

$allowedDevices = @("marble", "marblein")
if ($allowedDevices -notcontains $deviceCodename) {
    $message = "Connected device codename '$deviceCodename' is not marble/marblein."
    if ($Strict) {
        throw $message
    }
    Write-Warning $message
}

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$analyzer = Join-Path $scriptRoot "analyze-marble-cameraimpl.py"
if (-not (Test-Path -LiteralPath $analyzer)) {
    throw "Missing analyzer: $analyzer"
}

$fileStamp = (Get-Date).ToUniversalTime().ToString("yyyyMMdd-HHmmss")
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $PWD "device/marble/runtime/cameraimpl/$fileStamp"
}

$outputRoot = [System.IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null

$remoteLibraries = @(
    "/system_ext/lib64/libcameraimpl.so",
    "/system_ext/lib/libcameraimpl.so"
)

$results = New-Object System.Collections.Generic.List[object]

foreach ($remotePath in $remoteLibraries) {
    $probe = Invoke-AdbText -Arguments @("shell", "ls", "-l", $remotePath) -AllowFailure
    if ($probe.ExitCode -ne 0) {
        $results.Add([ordered]@{
            remotePath = $remotePath
            present = $false
            pullSucceeded = $false
            analyzerSucceeded = $false
            error = $probe.Text
        })
        continue
    }

    $archName = if ($remotePath -like "*/lib64/*") { "lib64" } else { "lib" }
    $localLibrary = Join-Path $outputRoot "$archName-libcameraimpl.so"
    $analysisPath = Join-Path $outputRoot "$archName-libcameraimpl-analysis.json"

    $pull = Invoke-AdbText -Arguments @("pull", $remotePath, $localLibrary) -AllowFailure
    if ($pull.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $localLibrary)) {
        $results.Add([ordered]@{
            remotePath = $remotePath
            present = $true
            pullSucceeded = $false
            analyzerSucceeded = $false
            error = $pull.Text
        })
        continue
    }

    try {
        Invoke-PythonJson -ScriptPath $analyzer -LibraryPath $localLibrary -ReportPath $analysisPath
        $analysis = Get-Content -LiteralPath $analysisPath -Raw -Encoding UTF8 | ConvertFrom-Json

        $coverage = $analysis.symbols.expectedHookCoverage
        $results.Add([ordered]@{
            remotePath = $remotePath
            present = $true
            pullSucceeded = $true
            analyzerSucceeded = $true
            localPath = $localLibrary
            analysisPath = $analysisPath
            sha256 = $analysis.file.sha256
            sizeBytes = $analysis.file.sizeBytes
            elfClass = $analysis.elf.class
            machine = $analysis.elf.machine
            soname = $analysis.dynamic.soname
            neededLibraries = @($analysis.dynamic.needed)
            candidateExports = @($analysis.symbols.candidateExports)
            expectedHookCoverage = $coverage
            integrationAssessment = $analysis.integrationAssessment
            interestingStrings = @($analysis.strings.interesting)
        })
    }
    catch {
        $results.Add([ordered]@{
            remotePath = $remotePath
            present = $true
            pullSucceeded = $true
            analyzerSucceeded = $false
            localPath = $localLibrary
            analysisPath = $analysisPath
            error = $_.Exception.Message
        })
    }
}

$successful = @($results | Where-Object { $_.analyzerSucceeded })
$lib64 = @($successful | Where-Object { $_.remotePath -eq "/system_ext/lib64/libcameraimpl.so" })

$summary = [ordered]@{
    schemaVersion = 1
    generatedAtUtc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    deviceCodename = $deviceCodename
    expectedDeviceCodenames = $allowedDevices
    deviceMatches = $allowedDevices -contains $deviceCodename
    librariesChecked = $remoteLibraries.Count
    librariesPresent = @($results | Where-Object { $_.present }).Count
    librariesAnalyzed = $successful.Count
    lib64Analyzed = $lib64.Count -gt 0
    results = @($results)
}

$summaryPath = Join-Path $outputRoot "cameraimpl-summary.json"
$summary | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $summaryPath -Encoding UTF8

Write-Host "Xiaomi camera implementation ABI inspection complete:"
Write-Host $summaryPath
Write-Host ""
Write-Host "Device: $deviceCodename"
Write-Host "Libraries present: $($summary.librariesPresent)"
Write-Host "Libraries analyzed: $($summary.librariesAnalyzed)"
Write-Host "64-bit factory library analyzed: $($summary.lib64Analyzed)"

foreach ($result in $successful) {
    Write-Host ""
    Write-Host "[$($result.remotePath)]"
    Write-Host "SHA-256: $($result.sha256)"
    Write-Host "ELF: $($result.elfClass), machine=$($result.machine)"

    $coverage = $result.expectedHookCoverage
    $assessment = $result.integrationAssessment
    if ($assessment) {
        Write-Host "Recommended integration: $($assessment.recommendedIntegration)"
        Write-Host "Legacy CameraStub core: $($assessment.legacyCameraStubCore)"
        Write-Host "Modern scene identification: $($assessment.modernSceneIdentification)"
        Write-Host "Client lifecycle registration: $($assessment.clientLifecycleRegistration)"
    }

    foreach ($name in @(
        "create",
        "hookModuleInit",
        "setClientPackageName",
        "initializeDeviceInfo",
        "updateSessionParams",
        "createCustomDefaultRequest",
        "executeSceneIdentify",
        "detachSceneIdentify",
        "notifyRequestSubmit",
        "notifyCancelRequest"
    )) {
        $matches = @($coverage.$name)
        $status = if ($matches.Count -gt 0) { "FOUND" } else { "missing" }
        Write-Host ("{0,-28} {1}" -f $name, $status)
    }
}

if ($Strict) {
    if (-not $summary.deviceMatches) {
        Write-Error "Strict libcameraimpl inspection failed: unexpected device codename."
        exit 2
    }
    if (-not $summary.lib64Analyzed) {
        Write-Error "Strict libcameraimpl inspection failed: 64-bit factory library was not analyzed."
        exit 3
    }
}
