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

**Phase 1 — Device baseline is in progress.**

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
2. Normalize exposed camera IDs and capabilities.
3. Validate physical lens mappings.
4. Add a dedicated Camera2 characteristics probe where `dumpsys` is incomplete.
5. Build a tested GCam compatibility matrix.
6. Begin reproducible image/video tuning profiles.

## Contributing

Keep changes device-focused, reproducible, and documented. When adding a configuration or patch, include the GCam base/mod version it targets and the exact behavior being fixed or improved.

## Disclaimer

Google Camera is a Google product. This is an independent community project and is not affiliated with or endorsed by Google, Xiaomi, or POCO.
