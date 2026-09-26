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
6. injects narrow guards into `klm.q(...)` and `klm.x(...)` so POCO F5 never selects feature names containing `use_tpu`, `darwinn`, or `edgetpu`, and disables the `camera.lasagna*` Tensor/GXP motion path;
7. keeps the startup GCam InitParams provider from enabling `almond_use_tpu` and Tomte grain;
8. verifies three exact AArch64 instruction sequences in `lib/arm64-v8a/libgcastartup.so` and redirects only the GXP/DarwiNN delegate-selection path to the library's existing CPU/TFLite fallback;
9. does **not** bypass TFLite/model verification or PairIP;
10. rebuilds the modified DEX files with checksum-pinned smali;
11. aligns native libraries for 16 KiB page-size devices;
12. replaces Google's signing identity with the project signing key;
13. verifies package name, SDK, ABI, APK signature, and ZIP alignment.

The first compatibility patch removed the explicit unsupported-device startup
exception. Real-device testing then progressed far enough to connect to
CameraService, but the Snapdragon POCO F5 logged repeated `libgxp.so` /
DarwiNN initialization failures and later dereferenced a null native `Gcam`
object. Java-side feature and InitParams guards were not sufficient: a later
real-device run still entered the native `libgxp.so` path and returned a null
GCam object. The current build therefore also applies an exact-version,
fail-closed native patch that selects the existing CPU/TFLite delegate path
instead of the unavailable GXP/DarwiNN path.

A bare `dlopen failed` line is kept as runtime diagnostics but is no longer
treated as a fatal crash by itself; the strict smoke test still fails on real
Java/native fatal exceptions, process death, or launcher failure.

These checks still prove package structure and targeted bytecode
transformations only. They do **not** prove the rest of Pixel Camera's runtime
behavior on a non-Pixel camera HAL.

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
5. on an explicit manual standalone build, when Telegram credentials are
   available, deliver only the generated compatibility-patched standalone APK;
   scheduled upstream refreshes do not automatically distribute the modified
   APK.

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

PairIP remains untouched. Compatibility changes are limited to concrete
POCO F5 runtime failures: the unsupported-device constructor gate, Tensor-only
feature/InitParams selection, and three exact same-version native
GXP/DarwiNN delegate-selection instructions. Every patch fails closed when the
expected bytecode or machine-code shape changes. The pipeline does not bypass
model verification, remove PairIP, or delete feature modules.
