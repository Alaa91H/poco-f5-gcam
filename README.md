# POCO F5 GCam

## Build and Telegram delivery

The single workflow, `.github/workflows/build.yml`, validates the project and
builds the modified Pixel Camera APK when relevant changes reach `main`. After
a successful APK build it sends that signed APK to the configured Telegram chat.
Manual runs on `main` also build and send by default; clear
`build_experimental_standalone` for an upstream-only run. Pull requests validate
changes without Telegram delivery. Scheduled runs check upstream availability.

Required repository secrets: `GCMOD_KEYSTORE_B64`, `GCMOD_KEY_ALIAS`,
`GCMOD_KEYSTORE_PASSWORD`, `GCMOD_KEY_PASSWORD`, `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_CHAT_ID`, `TELEGRAM_API_ID`, and `TELEGRAM_API_HASH`.

These are experimental compatibility builds. Successful CI and Telegram delivery
do not prove camera functionality on the phone. See
[the latest runtime findings](docs/RUNTIME_2026-09-27.md) for the remaining issues.

Device-focused compatibility, configuration, testing, and optimization project for Google Camera mods on the **POCO F5 5G (marble)**.

## Goals

- Improve GCam compatibility and stability on POCO F5.
- Maintain reproducible device-specific configurations.
- Document Camera2 capabilities and auxiliary-camera behavior.
- Tune photo processing, HDR, Night Sight, AWB, noise reduction, sharpness, and video behavior.
- Track regressions and test changes systematically across supported GCam mods.

## Repository scope

This repository is intended for:

- Device configuration files
- Compatibility notes
- Patches and patch documentation
- Test profiles and reproducible test procedures
- Helper scripts and tooling

It does **not** include or redistribute proprietary Google Camera APKs, Google-owned binaries, or other copyrighted vendor components.

## Planned structure

```text
.
├── configs/
│   ├── balanced/
│   ├── night/
│   ├── photo/
│   └── video/
├── device/
│   └── marble/
├── docs/
├── patches/
│   ├── auxiliary-cameras/
│   ├── camera2/
│   ├── hdr/
│   └── video/
├── scripts/
└── tests/
```

## Development status

**Phase 1 — Device baseline and Camera2 capability tooling are available. Phase 2 — physical lens mapping and package-specific exposure testing is now implemented.**

The repository now includes a Windows 11 ADB collector that captures camera-service, vendor-property, Android-feature, and media-codec information from the target POCO F5 without intentionally storing the ADB serial.

Hardware capabilities, sensor IDs, supported GCam bases, and tuning profiles will be documented only after validation on a real target device.

## Quick start: collect the first POCO F5 baseline

Requirements:

- POCO F5 with USB debugging enabled
- Android SDK Platform-Tools / `adb`
- Windows PowerShell

From the repository root:

```powershell
adb devices
powershell -ExecutionPolicy Bypass -File .\scripts\collect-camera-baseline.ps1
```

The generated capture is written under:

```text
device/marble/baseline/captures/YYYYMMDD-HHMMSS/
```

Review the generated files before publishing them. See [docs/DEVICE_BASELINE.md](docs/DEVICE_BASELINE.md) for the full procedure and privacy checklist.

## Development roadmap

See [docs/ROADMAP.md](docs/ROADMAP.md).

The immediate sequence is:

1. Capture and review a real POCO F5 baseline.
2. Run the Camera2 Probe and generate the capability matrix.
3. Validate physical lens mappings with the live Lens Verifier.
4. Compare package-specific Camera2 exposure where auxiliary cameras are restricted.
5. Fill the exact-build GCam compatibility matrix.
6. Begin reproducible image/video tuning profiles only after the compatible base is established.

## Contributing

Keep changes device-focused, reproducible, and documented. When adding a configuration or patch, include the GCam base/mod version it targets and the exact behavior being fixed or improved.

## Disclaimer

Google Camera is a Google product. This is an independent community project and is not affiliated with or endorsed by Google, Xiaomi, or POCO.


## Lens mapping and GCam compatibility

The repository includes an interactive Lens Verifier that opens each exposed Camera ID and lets the tester identify the physical lens by live preview and lens occlusion.

On Windows 11:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-lens-verifier.ps1 -Build
```

For package-specific auxiliary-camera exposure tests, the Camera2 Probe can also be built with a custom Android application ID. See [docs/LENS_MAPPING_AND_COMPATIBILITY.md](docs/LENS_MAPPING_AND_COMPATIBILITY.md).


## Latest compatible Pixel Camera

The project automatically selects the numerically newest APKMirror Pixel Camera
release matching the POCO F5 target:

- Android 17 / API 37
- `arm64-v8a`
- `nodpi`

There is no runtime promotion gate or last-known-good fallback. The selected
release is simply the newest release whose published variant metadata matches
the target policy.

Download it locally:

```powershell
python .\scripts\resolve_apkmirror_gcam.py
python .\scripts\download_latest_pixel_camera.py
```

The binary is saved under `downloads/pixel-camera/` and is ignored by Git.
GitHub Actions checks for a newer compatible release daily and downloads it when
the selected version changes. A manual workflow run always downloads the current
latest compatible release.

See [docs/PIXEL_CAMERA_UPSTREAM.md](docs/PIXEL_CAMERA_UPSTREAM.md).

## Pixel Camera installation and runtime validation

The preferred installation path now preserves the original Google-signed split
APKs. This avoids changing the dynamic-feature layout or application signing
identity before runtime compatibility is proven on the real POCO F5.

On Windows 11 with ADB:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-pixel-camera-splits.ps1 -PackagePath .\PixelCamera.apkm
powershell -ExecutionPolicy Bypass -File .\scripts\test-pixel-camera-runtime.ps1 -Strict
```

The runtime test launches Pixel Camera, waits for the process to remain alive,
checks the resumed activity, and captures package-associated fatal crash evidence
from logcat into a JSON report.

The merged single-APK path is still available for controlled experiments, but it
is marked **experimental / runtime-unvalidated**, is not automatically delivered,
and must not be promoted until the real-device smoke test passes on POCO F5
(Android 17 / API 37).

See [docs/STANDALONE_APK_BUILD.md](docs/STANDALONE_APK_BUILD.md).

## Xiaomi camera-session ABI inspection

Before adding any Marble-specific `frameworks/av` camera-session hook, inspect
the actual Xiaomi `libcameraimpl.so` shipped on the connected POCO F5:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\inspect-marble-libcameraimpl.ps1 -Strict
```

This produces an ELF/API report without requiring `readelf`, `nm`, an NDK,
or third-party Python packages. See
[docs/MARBLE_CAMERAIMPL_ABI.md](docs/MARBLE_CAMERAIMPL_ABI.md).
\n
