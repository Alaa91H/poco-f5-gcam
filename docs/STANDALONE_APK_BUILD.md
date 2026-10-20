# Pixel Camera packaging on POCO F5

Pixel Camera 11 is distributed as a base APK plus split/configuration/feature
APKs. The project preserves that original layout as the upstream baseline, but
the current 11.0.073 build reaches the launcher on POCO F5 and then is rejected
by Pixel Camera's explicit unsupported-device constructor guard.

## Four-flavour build

`device/marble/build-flavors.json` defines the four deliverables produced by
`scripts/build_flavor_matrix.py`:

| Flavour | Base | Manifest minSdk | Package | Notes |
|---|---|---:|---|---|
| `pixel-a17` | Pixel Camera 11.1 | 37 | `com.google.android.GoogleCamera` | Reviewed compatibility patches; no manifest override |
| `pixel-a13` | Pixel Camera 11.1 | 33 | `com.google.android.GoogleCamera` | Same patches; install-time minSdk override |
| `pixel-manual-a17` | Pixel Camera 11.1 | 37 | `com.google.android.GoogleCamera` | Compatibility patches plus clean-room manual focus, shutter, ISO, and lens selector |
| `pixel-manual-a13` | Pixel Camera 11.1 | 33 | `com.google.android.GoogleCamera` | Same manual profile with install-time minSdk override |

All four flavours share the Google package, so they replace each other. The
manual-controls profile is a Java-side, fail-closed wrapper around the existing
feature queries `Lksf;->q` and `Lksf;->x`. It enables only the five Ark flags
listed in [docs/MANUAL_CONTROLS.md](MANUAL_CONTROLS.md), is gated to
`Build.DEVICE` values starting with `marble`, and adds no third-party code,
resources, ads, or branding; the result is signed only with the project key.

The normal and manual flavours are built from the same selected Pixel Camera
11.1 source with the reviewed DEX/native compatibility patches. The manual
profile adds a small helper class and two wrapper methods; it does not implement
third-party lib-patcher or XML-config behaviour.

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
3. edits the binary manifest directly through the ARSCLib classes bundled
   in the pinned APKEditor JAR, without decoding the full resource tree;
   this includes setting `minSdkVersion` to the selected flavour value (37 or
   33), while leaving `targetSdkVersion=37` unchanged;
4. declares `libOpenCL.so`, `libOpenCL-car.so`, and `libOpenCL-pixel.so`
   as optional `<uses-native-library>` entries, then verifies those declarations
   again in the final compiled manifest;
5. locates the exact `Device is not recognized or not supported` constructor guard in one DEX;
6. disassembles only that DEX with checksum-pinned baksmali;
7. redirects the rejection block to Pixel Camera's existing common constructor finalization path and fails closed if the bytecode shape is not exactly recognized;
8. injects narrow guards into the 11.1 boolean feature queries `Lksf;->q(...)` and `Lksf;->x(...)` so POCO F5 never selects feature names containing `use_tpu`, `darwinn`, or `edgetpu`, and disables the `camera.lasagna*` Tensor/GXP motion path;
9. keeps the startup GCam InitParams provider from enabling `almond_use_tpu` and Tomte grain;
10. verifies three exact AArch64 instruction sequences in `lib/arm64-v8a/libgcastartup.so` and redirects only the GXP/DarwiNN delegate-selection path to the library's existing CPU/TFLite fallback;
11. does **not** bypass TFLite/model verification or PairIP;
12. rebuilds the modified DEX files with checksum-pinned smali;
13. deletes large intermediate APKs immediately after each stage to keep hosted builds within disk limits;
14. aligns native libraries for 16 KiB page-size devices;
15. replaces Google's signing identity with the project signing key;
16. verifies package name, ABI, APK signature, ZIP alignment, the final
    OpenCL manifest declarations, and that the compiled manifest reports the
    flavour's selected `minSdkVersion` (37 or 33) and an unchanged
    `targetSdkVersion="37"`;
17. for manual-controls flavours only, wraps `Lksf;->q` and `Lksf;->x` with a
    `Build.DEVICE`-gated helper that enables the five reviewed Ark flags, and
    fails closed if the method or flag layout changes.

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

## Android 13+ manifest compatibility

Upstream Pixel Camera 11.1 is published for Android 17 and declares
`minSdkVersion="37"`. The compatibility build lowers only that install-time
manifest value to API 33 (Android 13), so the APK can be installed on Android
13, 14, 15, and 16 as well as 17. `targetSdkVersion` remains 37; the build does
not pretend that Google supports the older platforms, and it does not change
runtime behaviour to match an older target SDK.

This is an **install compatibility** change, not proof of runtime
compatibility. Pixel Camera 11.1 may call Android 14-17 APIs that do not exist
on Android 13, and the APK has not been smoke-tested on Android 13-16. On a
device below Android 17, the app can install successfully and still crash or
lose features at runtime. A real-device run of
`scripts/test-pixel-camera-runtime.ps1 -Strict` on each target Android version
is required before treating the build as stable.

If the goal is a Google-supported base for Android 13, use the APKMirror
`(arm64-v8a) (nodpi) (Android 13+)` Pixel Camera variant (currently 8.8.x)
instead of forcing the Android 17 build down. That older base needs its own
reviewed compatibility patches because the DEX and native profiles in this
repository target 11.0/11.1.

These checks still prove package structure and targeted bytecode
transformations only. They do **not** prove the rest of Pixel Camera's runtime
behavior on a non-Pixel camera HAL.

The OpenCL manifest change is intentionally narrow. Android 12+ restricts
visibility of non-NDK native libraries for apps targeting API 31+, so the
compatibility build declares the known Android OpenCL library names as optional.
No OpenCL library is bundled or replaced, and installation does not require any
one of those names to exist. Native heap pointer tagging is left unchanged
because current POCO F5 runtime evidence does not identify a pointer-tagging
failure.

For that reason the merged output is now explicitly recorded as:

- `runtime_validation.status = not_run`;
- `runtime_validation.required_for_distribution = true`;
- `distribution.status = compatibility-patched-unvalidated`;
- `distribution.automatic_delivery_allowed = false`.

These report fields describe eligibility for stable distribution. The owner
has explicitly requested delivery of experimental builds to the configured
Telegram chat: relevant pushes to `main` and manual standalone builds on `main`
now build and send the modified APK. Manual runs default
`build_experimental_standalone` to true. The artifact name includes
`compatibility-patched-unvalidated`, and the Telegram caption states that device
validation is pending. Real-device validation remains mandatory before treating
the build as stable. Scheduled upstream refreshes do not send modified APKs.

## CI behavior

Scheduled or manually dispatched upstream refreshes continue to:

1. resolve the newest API-37/arm64 compatible Pixel Camera release;
2. download and checksum-verify the original package;
3. audit all APK signatures and the split dependency graph;
4. upload the original Google-signed package together with the audit report,
   split plan, and installation/runtime-test scripts;
5. build four Pixel Camera 11.1 flavours: Android 17+ and Android 13+, each
   with and without the clean-room manual-controls profile, and write
   `deliverables.json` with each APK's size and SHA-256;
6. on a standalone build triggered by a relevant push or manual run on `main`,
   deliver all four flavour APKs to Telegram through the Local Bot API; missing
   Telegram credentials fail the delivery step. Scheduled upstream refreshes do
   not distribute the modified APKs. All four remain experimental and
   runtime-unvalidated.

`.github/workflows/build.yml` is the only active workflow. Historical one-off
diagnostic workflows were removed; their source remains available in Git history.

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
