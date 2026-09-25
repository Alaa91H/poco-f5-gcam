# Camera2 Probe

The Camera2 probe is a small open-source Android utility built specifically for this repository. It reads the public Camera2 characteristics exposed to a normal application and exports them as structured JSON.

It does not contain Google Camera code and does not modify the device.

## What it captures

For every exposed Camera ID:

- lens facing
- Camera2 hardware support level
- available capabilities, including RAW and manual sensor support
- logical/physical camera relationships
- focal lengths and apertures
- sensor active/pixel array sizes
- physical sensor size
- exposure and ISO ranges
- focus characteristics
- OIS and video stabilization modes
- AE FPS ranges
- max digital zoom
- input/output stream formats and resolutions
- JPEG, YUV, RAW and private stream sizes
- Android 17 RAW14 stream exposure
- optional sustained YUV_420_888 runtime delivery per exposed Camera ID
- min frame and stall durations
- constrained high-speed video sizes/FPS ranges
- all CameraCharacteristics keys exposed by the framework, including vendor-tag keys that are visible to the app

It also records non-personal build context such as model, device codename, Android version, build fingerprint and security patch.

## Permissions

The static Camera2 characteristics report does **not** require opening a camera. The app declares `CAMERA` only for the separate optional YUV runtime probe. No storage, network, or root permission is used.

The runtime probe requests/grants camera permission only when explicitly launched. It does not save image pixels: frames are acquired only long enough to verify sustained `YUV_420_888` delivery and record technical metadata such as frame count, timestamps and plane strides.

## Build

### GitHub Actions

The repository workflow builds the debug APK automatically. Download the `camera2-probe-debug` artifact from a successful workflow run.

### Local build

Requirements:

- JDK 17
- Android SDK Platform 37.0 (Android 17 / API 37)
- Android Build Tools 37.0.0
- Gradle 9.6.0

From the repository root:

```powershell
gradle -p .\tools\camera2-probe :app:assembleDebug
```

The APK will be created at:

```text
tools/camera2-probe/app/build/outputs/apk/debug/app-debug.apk
```

## Run and export on Windows 11

With the POCO F5 connected and USB debugging authorized:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-camera2-probe.ps1
```

If you have a downloaded APK artifact in another location:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-camera2-probe.ps1 -ApkPath "C:\path\to\app-debug.apk"
```

To build locally before installation:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-camera2-probe.ps1 -Build
```

A timestamped capture is written under:

```text
device/marble/camera2/captures/YYYYMMDD-HHMMSS/
```

To also run the active sustained-YUV test across every exposed Camera ID:

```powershell
powershell -ExecutionPolicy Bypass -File .\\scripts\\run-camera2-probe.ps1 -YuvRuntime
```

When enabled, the same capture directory also contains `yuv-runtime-report.json`. The script deletes old in-app reports before each run so a preserved `install -r` data directory cannot be mistaken for fresh evidence.

The ADB serial is intentionally not stored.

## Generate the capability matrix

After export:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\summarize-camera2-report.ps1 `
  -ReportPath ".\device\marble\camera2\captures\YYYYMMDD-HHMMSS\camera2-report.json"
```

This creates `CAPABILITY_MATRIX.md` beside the JSON report.

## Important limitation

Camera2 reports what the current ROM/HAL exposes to the probe package. Some Xiaomi/POCO builds restrict auxiliary cameras by package allowlist, and a GCam mod may see a different set of cameras depending on its package name.

For that reason, the capability matrix is evidence for the base Camera2 exposure, not the final GCam compatibility result. The optional YUV runtime report strengthens that evidence by proving actual frame delivery for directly exposed Camera IDs, but physical cameras hidden behind a logical ID still require a physical-output session test in a later stage. Package-specific auxiliary-camera tests remain a separate compatibility dimension.