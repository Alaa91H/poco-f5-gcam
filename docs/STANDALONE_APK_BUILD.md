# Pixel Camera packaging on POCO F5

Pixel Camera 11 is distributed as a base APK plus split/configuration/feature
APKs. The project preserves that original layout as the upstream baseline, but
the current 11.0.073 build reaches the launcher on POCO F5 and then is rejected
by Pixel Camera's explicit unsupported-device constructor guard.

## Baseline path: original Google-signed splits

The upstream APK/APKM is downloaded, checksum-verified, and every contained APK
is verified against the approved Google signing certificate. No APK bytes are
edited before installation. This is useful as a clean baseline, but it is not a
working POCO F5 runtime path for the current Pixel Camera 11.0.073 release because
the application rejects the device during provider startup.

On Windows 11 with Android Platform-Tools:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-pixel-camera-splits.ps1 -PackagePath .\PixelCamera.apkm
```

The installer:

1. Requires an attached `marble`/`marblein` device.
2. Extracts APK entries to a temporary directory without modifying them.
3. Uses `adb install-multiple -r` for split bundles.
4. Keeps Google's original signatures intact.
5. Verifies that Android resolves `com.google.android.GoogleCamera` after
   installation.
6. Removes temporary extracted files after installation.

If a previously installed experimental merged APK uses the project signing key,
Android will reject an in-place update with
`INSTALL_FAILED_UPDATE_INCOMPATIBLE`. In that case, uninstall that experimental
build first or rerun the installer with `-UninstallExisting`.

## Required runtime smoke test

A package is not considered runtime-compatible merely because it installs.

Run:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\test-pixel-camera-runtime.ps1 -Strict
```

The smoke test:

- validates the connected POCO F5 codename;
- verifies that Pixel Camera is installed;
- force-stops the package and clears logcat;
- launches the launcher activity;
- observes whether the process remains alive;
- checks whether the package owns the resumed activity;
- scans logcat for package-associated fatal exceptions, native loader failures,
  resource/class failures, verifier errors, fatal signals, and abort messages;
- writes a JSON report under `device/marble/runtime/`.

A strict run exits non-zero when the launcher fails, the process dies during the
observation window, or package-associated fatal crash evidence is detected.

## POCO F5 compatibility-patched single APK

The repository contains `scripts/build_standalone_pixel_camera.py` plus
`scripts/patch_pixel_camera_device_gate.py` for a controlled POCO F5
single-file compatibility build.

That path:

1. merges the split bundle with pinned APKEditor;
2. sanitizes split metadata during the merge;
3. locates the exact `Device is not recognized or not supported` constructor guard in one DEX;
4. disassembles only that DEX with checksum-pinned baksmali;
5. redirects the rejection block to Pixel Camera's existing common constructor finalization path and fails closed if the bytecode shape is not exactly recognized;
6. rebuilds the DEX with checksum-pinned smali;
7. aligns native libraries for 16 KiB page-size devices;
8. replaces Google's signing identity with the project signing key;
9. verifies package name, SDK, ABI, APK signature, and ZIP alignment.

The patch addresses the concrete startup crash observed on POCO F5, but those
checks still prove package structure and the targeted bytecode transformation
only. They do **not** prove the rest of Pixel Camera's runtime behavior on a
non-Pixel camera HAL.

For that reason the merged output is now explicitly recorded as:

- `runtime_validation.status = not_run`;
- `runtime_validation.required_for_distribution = true`;
- `distribution.status = compatibility-patched-unvalidated`;
- `distribution.automatic_delivery_allowed = false`.

GitHub Actions does not automatically distribute this modified APK during
scheduled upstream refreshes. A manual workflow run must explicitly enable
`build_experimental_standalone` to produce it, and the artifact name includes
`compatibility-patched-unvalidated`. Real-device validation remains mandatory
before treating the build as stable.

## CI behavior

Scheduled or manually dispatched upstream refreshes continue to:

1. resolve the newest API-37/arm64 compatible Pixel Camera release;
2. download and checksum-verify the original package;
3. audit all APK signatures and the split dependency graph;
4. upload the original Google-signed package together with the audit report,
   split plan, and installation/runtime-test scripts;
5. when Telegram credentials are available, deliver the original Google-signed
   bundle rather than the merged project-signed APK.

Pull requests run static/unit validation without downloading or redistributing
the proprietary upstream package.

## Signing and compatibility

The original split installation retains Google's certificate.

Any merged or otherwise modified APK cannot retain the original Google
signature. A stable repository signing key is still required for upgrade
compatibility between experimental project-signed builds, but a stable project
key does not make the merged package equivalent to Google's original runtime
layout.

## Protection policy

PairIP remains untouched. The compatibility patch is limited to the explicit
unsupported-device constructor exception shown by the POCO F5 runtime report.
The pipeline does not remove PairIP or delete feature modules.
