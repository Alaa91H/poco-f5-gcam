# POCO F5 Device Baseline Workflow

The first development milestone is to establish a reproducible camera baseline for the POCO F5 (`marble`) before tuning GCam.

## Why this comes first

GCam behavior depends on the camera HAL, ROM, Android version, camera IDs, vendor properties, available codecs, and how auxiliary cameras are exposed. Guessing those values makes configs fragile.

The baseline must therefore be captured from the actual target device.

## Windows 11 workflow

### 1. Prepare the phone

1. Enable **Developer options**.
2. Enable **USB debugging**.
3. Connect the POCO F5 by USB.
4. Accept the computer's RSA debugging prompt on the phone.

Root is **not required** for the baseline collector.

### 2. Prepare ADB

Install Android SDK Platform-Tools and ensure `adb.exe` is available in `PATH`.

Verify:

```powershell
adb version
adb devices
```

The device should appear with the state `device`, not `unauthorized` or `offline`.

### 3. Run the collector

From the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\collect-camera-baseline.ps1
```

If more than one Android device is connected:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\collect-camera-baseline.ps1 -Serial <adb-serial>
```

The serial is used only to select the connected target. The collector intentionally does not save it in the generated baseline.

### 4. Optional camera log

Only use log collection when reproducing an actual camera failure:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\collect-camera-baseline.ps1 -IncludeCameraLog
```

Logcat can contain package/runtime information. Review `camera-logcat.txt` before publishing it.

## Generated data

A timestamped directory is created under:

```text
device/marble/baseline/captures/YYYYMMDD-HHMMSS/
```

It contains, where supported:

- device and ROM metadata
- `dumpsys media.camera`
- camera proxy state
- camera-related vendor properties
- relevant Android feature declarations
- installed camera/GCam package names
- media codec service information
- optional filtered camera logcat
- SHA-256 checksums

## Privacy review

The scripts intentionally avoid storing the ADB serial and filter obvious identifier-like camera properties. That is not a guarantee that every vendor ROM exposes no sensitive information in service dumps.

Before committing a capture:

1. Open every generated text file.
2. Search for your name, email, phone number, serial number, IMEI, local paths, Wi-Fi details, or account data.
3. Remove anything unrelated to camera compatibility.
4. Do not commit logcat unless it is necessary to diagnose a reproducible problem.

## Acceptance criteria

A baseline is ready for analysis when all of the following are true:

- Device identifies as POCO F5 / `marble`.
- Android/ROM/build information is recorded.
- `dumpsys media.camera` was captured successfully.
- Camera-related properties and platform features were captured.
- Media codec data is available for video capability analysis.
- The capture has been manually reviewed before publication.

## Next milestone

After receiving the first clean baseline capture, the project will parse it into a normalized camera capability matrix and then add a dedicated Camera2 probe for characteristics that vendor `dumpsys` does not expose reliably.