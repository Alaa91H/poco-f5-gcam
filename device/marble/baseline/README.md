# POCO F5 Baseline Captures

This directory stores reviewed diagnostic captures from real POCO F5 (`marble`) devices.

## Rules

- Never commit an unreviewed dump.
- Never commit device serials, IMEI/MEID, account information, or private logs.
- Identify the ROM/build and Android version in each capture.
- Keep raw evidence separate from interpreted capability tables.
- Do not rename a camera ID based only on assumptions; validate the physical lens mapping first.

## Capture

On Windows 11, run from the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\collect-camera-baseline.ps1
```

See [Device Baseline Workflow](../../../docs/DEVICE_BASELINE.md) for the complete procedure.