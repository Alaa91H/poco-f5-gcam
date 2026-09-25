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
- min frame and stall durations
- constrained high-speed video sizes/FPS ranges
- all CameraCharacteristics keys exposed by the framework, including vendor-tag keys that are visible to the app

It also records non-personal build context such as model, device codename, Android version, build fingerprint and security patch.

## Permissions

The probe deliberately requests **no Camera permission**, storage permission, network permission, or root access.

Reading CameraCharacteristics does not require opening the camera device. The generated JSON is stored in the app's private debug data directory and exported through ADB `run-as`.

## Build

### GitHub Actions

The repository workflow builds the debug APK automatically. Download the `camera2-probe-debug` artifact from a successful workflow run.

### Local build

Requirements:

- JDK 17
- Android SDK Platform 35
- Android Build Tools 35.0.0
- Gradle 8.9

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

For that reason, the capability matrix is evidence for the base Camera2 exposure, not the final GCam compatibility result. Package-specific auxiliary-camera tests are a later stage.