# Marble libcameraimpl ABI inspection

This phase verifies the actual Xiaomi camera-session ABI shipped on the connected
POCO F5 before any `frameworks/av` hook is authored.

The goal is to avoid copying mangled symbols, object layouts, or call sequences
from another Xiaomi generation.

## What the inspector does

`scripts/inspect-marble-libcameraimpl.ps1`:

1. Verifies that ADB can see the device.
2. Checks the connected codename against `marble` / `marblein`.
3. Probes:
   - `/system_ext/lib64/libcameraimpl.so`
   - `/system_ext/lib/libcameraimpl.so`
4. Pulls only the libraries that exist.
5. Runs `scripts/analyze-marble-cameraimpl.py`.
6. Produces an ABI report without requiring `readelf`, `nm`, an Android NDK,
   or third-party Python packages.

The Python analyzer reads the ELF structures directly and reports:

- ELF class and machine type
- SHA-256 and file size
- SONAME
- `DT_NEEDED` dependencies
- exported dynamic symbols
- Xiaomi camera-session hook candidates
- coverage for the logical methods used by the known Xiaomi session lifecycle
- interesting strings containing Xiaomi session tags, MiVi, CameraImpl,
  CameraX, role, or third-party camera references

## Run on Windows 11

From the repository root:

```powershell
adb devices
powershell -ExecutionPolicy Bypass -File .\scripts\inspect-marble-libcameraimpl.ps1 -Strict
```

The default output directory is:

```text
device/marble/runtime/cameraimpl/YYYYMMDD-HHMMSS/
```

The main file to share for review is:

```text
cameraimpl-summary.json
```

The pulled `.so` files are ignored by Git and must not be committed.

## Admission rule for a native session hook

Do not add a Marble camera-session hook merely because another Xiaomi device
exports similarly named functions.

Before implementation, validate the 64-bit library report and record the exact
mangled symbol for every method that will be called. The minimum lifecycle to
investigate is:

```text
create
hookModuleInit
setClientPackageName
initializeDeviceInfo
updateSessionParams
createCustomDefaultRequest
executeSceneIdentify
detachSceneIdentify
```

`notifyRequestSubmit` and `notifyCancelRequest` should be treated as optional
until the Marble ABI and the original call sites demonstrate that they are
required.

If one of the logical methods is absent, stop and inspect the Marble-specific
exports instead of substituting a symbol or signature from Nezha or another
device.

## Next step

After a real `cameraimpl-summary.json` is captured, compare its exact exports
and dependencies against the framework ABI used by the ROM. Only then should a
Marble-specific adapter be prepared for `frameworks/av`.
