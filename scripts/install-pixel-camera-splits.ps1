[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$PackagePath,

    [string]$PackageName = "com.google.android.GoogleCamera",

    [switch]$AllowDowngrade,

    [switch]$UninstallExisting
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

if (-not (Get-Command adb -ErrorAction SilentlyContinue)) {
    throw "adb was not found in PATH. Install Android SDK Platform-Tools first."
}

$resolvedPackage = [System.IO.Path]::GetFullPath($PackagePath)
if (-not (Test-Path -LiteralPath $resolvedPackage -PathType Leaf)) {
    throw "Package file does not exist: $resolvedPackage"
}

$state = (Invoke-Adb -Arguments @("get-state")).Text
if ($state -ne "device") {
    throw "ADB device is not ready. Current state: $state"
}

$device = (Invoke-Adb -Arguments @("shell", "getprop", "ro.product.device")).Text.Trim()
$sdk = (Invoke-Adb -Arguments @("shell", "getprop", "ro.build.version.sdk")).Text.Trim()
$allowedDevices = @("marble", "marblein")

if ($allowedDevices -notcontains $device) {
    throw "Connected device '$device' is not a POCO F5 target (marble/marblein)."
}

if ($sdk -ne "37") {
    Write-Warning "Connected device reports Android API $sdk; this package selection targets API 37."
}

if ($UninstallExisting) {
    $uninstall = Invoke-Adb -Arguments @("uninstall", $PackageName) -AllowFailure
    if ($uninstall.ExitCode -ne 0 -and $uninstall.Text -notmatch "Unknown package") {
        throw "Failed to uninstall existing $PackageName installation: $($uninstall.Text)"
    }
}

$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("poco-f5-pixel-camera-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null

try {
    $extension = [System.IO.Path]::GetExtension($resolvedPackage).ToLowerInvariant()
    $apkFiles = New-Object System.Collections.Generic.List[string]

    if ($extension -eq ".apk") {
        $apkFiles.Add($resolvedPackage)
    }
    elseif ($extension -in @(".apkm", ".apks", ".xapk", ".zip")) {
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $archive = [System.IO.Compression.ZipFile]::OpenRead($resolvedPackage)
        try {
            $index = 0
            foreach ($entry in $archive.Entries) {
                if ([System.IO.Path]::GetExtension($entry.Name).ToLowerInvariant() -ne ".apk") {
                    continue
                }

                $index++
                $safeName = "{0:D3}-{1}" -f $index, $entry.Name
                $destination = Join-Path $tempRoot $safeName
                [System.IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $destination, $true)
                $apkFiles.Add($destination)
            }
        }
        finally {
            $archive.Dispose()
        }
    }
    else {
        throw "Unsupported package type '$extension'. Expected .apk, .apkm, .apks, .xapk, or .zip."
    }

    if ($apkFiles.Count -eq 0) {
        throw "No APK files were found inside $resolvedPackage."
    }

    Write-Host "Target device: $device (API $sdk)"
    Write-Host "APK files to install: $($apkFiles.Count)"
    Write-Host "Original split APK bytes are preserved; no APK is merged or re-signed."

    $arguments = New-Object System.Collections.Generic.List[string]
    if ($apkFiles.Count -eq 1) {
        $arguments.Add("install")
    }
    else {
        $arguments.Add("install-multiple")
    }
    $arguments.Add("-r")
    if ($AllowDowngrade) {
        $arguments.Add("-d")
    }
    foreach ($apk in $apkFiles) {
        $arguments.Add($apk)
    }

    $install = Invoke-Adb -Arguments $arguments.ToArray() -AllowFailure
    if ($install.ExitCode -ne 0) {
        if ($install.Text -match "INSTALL_FAILED_UPDATE_INCOMPATIBLE") {
            throw ("Android rejected the update because the installed package uses a different signing certificate." +
                [Environment]::NewLine +
                "This commonly happens when the experimental merged APK was installed previously." +
                [Environment]::NewLine +
                "Uninstall that build first, then rerun this script with -UninstallExisting." +
                [Environment]::NewLine +
                "ADB output:" + [Environment]::NewLine + $install.Text)
        }
        throw ("Split installation failed:" + [Environment]::NewLine + $install.Text)
    }

    Write-Host $install.Text

    $paths = Invoke-Adb -Arguments @("shell", "pm", "path", $PackageName) -AllowFailure
    if ($paths.ExitCode -ne 0 -or $paths.Text -notmatch "^package:") {
        throw "Installation returned success but Android cannot resolve $PackageName."
    }

    $installedCount = @($paths.Text -split "\r?\n" | Where-Object { $_ -match "^package:" }).Count
    Write-Host "Installed package: $PackageName"
    Write-Host "Installed APK paths reported by Android: $installedCount"
    Write-Host ""
    Write-Host "Next: run scripts\test-pixel-camera-runtime.ps1 to verify launch/runtime stability."
}
finally {
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
