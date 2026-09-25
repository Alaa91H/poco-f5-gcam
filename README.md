# POCO F5 GCam

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

## Standalone POCO F5 APK build

After the original APK/APKM passes checksum and Google-signature verification,
the CI pipeline can produce one standalone APK for POCO F5 on Android 17. The
bundle is merged without automatically removing feature splits, aligned for
16 KiB native-library pages, re-signed with the project key, verified, uploaded
as a workflow artifact, and delivered to Telegram when the required credentials
are configured.

The original Google-signed input remains ephemeral and is never committed to the
repository. See [docs/STANDALONE_APK_BUILD.md](docs/STANDALONE_APK_BUILD.md).
