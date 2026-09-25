# POCO F5 camera environment audit

`scripts/audit-marble-camera-environment.ps1` performs a read-only ADB audit of the POCO F5 camera environment before blaming a GCam build for a HAL or vendor issue.

## What it checks

- device codename (`marble` / `marblein`)
- Android release, SDK and security patch
- Evolution X camera properties used for HAL3, OIS and gyro integration
- a compact `dumpsys media.camera` summary
- recent Camera provider, CamX/CHI, FastRPC, QNN/CDSP and native loader diagnostics

The script never sets a property, restarts the camera provider, roots the device, or modifies vendor files.

## Run on Windows 11

```powershell
adb devices
powershell -ExecutionPolicy Bypass -File .\scripts\audit-marble-camera-environment.ps1
```

The default report is written under:

```text
device/marble/runtime/audits/YYYYMMDD-HHMMSS-camera-environment.json
```

Use `-Strict` only when you want a failing exit code for an unexpected device codename or an Evolution X baseline property mismatch.

## Why this matters

A GCam symptom can originate below the app layer. OIS properties, Camera HAL exposure, CamX/CHI libraries, FastRPC and QNN/CDSP failures can all change preview, Night Sight, capture or video behavior. The audit preserves evidence so app-level tuning can be separated from ROM/vendor regressions.
