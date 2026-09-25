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

Initial repository bootstrap. Hardware capabilities, sensor IDs, supported GCam bases, and tuning profiles will be documented only after validation on the target device.

## Contributing

Keep changes device-focused, reproducible, and documented. When adding a configuration or patch, include the GCam base/mod version it targets and the exact behavior being fixed or improved.

## Disclaimer

Google Camera is a Google product. This is an independent community project and is not affiliated with or endorsed by Google, Xiaomi, or POCO.
