# Lens Mapping and Package-specific Camera Exposure

This stage converts raw Camera2 data into verified physical lens mappings and then checks whether Android exposes different camera IDs to different package names.

## Part A — Physical lens verification

Numeric Camera IDs are not stable lens names. Never assume that Camera ID `0` is the main camera or that another fixed number is the ultrawide.

The repository includes a dedicated Lens Verifier application. It requests Camera permission only because it must display a live preview. It does not record photos, video, audio, location, or network traffic.

### Build and run on Windows 11

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-lens-verifier.ps1 -Build
```

Or use the `lens-verifier-debug` APK artifact produced by GitHub Actions:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-lens-verifier.ps1 `
  -ApkPath "C:\path\to\app-debug.apk"
```

On the phone:

1. Grant Camera permission.
2. For the selected Camera ID, physically cover one rear lens at a time.
3. Watch which covered lens blocks the live preview.
4. Assign `Main`, `Ultrawide`, `Macro`, `Front`, or `Other / duplicate`.
5. Repeat for every exposed ID.
6. Press **Export verified mapping**.

The script exports `lens-mapping.json` under:

```text
device/marble/lens-mapping/captures/YYYYMMDD-HHMMSS/
```

## Part B — Package-specific exposure

Some vendor camera HALs expose auxiliary cameras differently depending on the requesting Android package name.

The Camera2 Probe supports a custom application ID at build time.

Example:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-camera2-probe.ps1 `
  -Build `
  -Package "example.package.name"
```

Use only a package name you intentionally want to test. If that package is already installed on the phone, a differently signed diagnostic APK normally cannot replace it; do not uninstall an existing camera app merely to run this test unless you explicitly intend to remove that app.

Run the default probe first so it becomes the comparison baseline:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-camera2-probe.ps1 -Build
```

Then run one or more package-specific probes and compare the resulting JSON reports:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\compare-camera2-exposure.ps1 `
  -ReportPath `
    ".\device\marble\camera2\captures\BASELINE\camera2-report.json", `
    ".\device\marble\camera2\captures\PACKAGE_TEST\camera2-report.json"
```

The comparison is written to:

```text
device/marble/compatibility/PACKAGE_EXPOSURE_MATRIX.md
```

## Part C — GCam validation

After physical IDs and package exposure are known, test each GCam build independently using:

`device/marble/compatibility/GCAM_COMPATIBILITY_TEMPLATE.md`

Do not treat “Camera ID is exposed” as equivalent to “GCam fully supports the lens.” A build can see an ID and still fail during preview, capture, HDR processing, video configuration, or lens switching.