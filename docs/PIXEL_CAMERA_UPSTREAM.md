# Pixel Camera upstream tracking

The repository tracks the newest **APKMirror Pixel Camera** releases whose published
variant metadata matches the POCO F5 target policy. Discovery and runtime approval
are deliberately separate: a newer APKMirror candidate cannot replace the
last-known-good POCO F5 runtime until the on-device promotion gate passes.

## Current target

- Device: POCO F5 5G (`marble`)
- ROM: Evolution X
- Android: 17 / API 37
- ABI: `arm64-v8a`
- DPI class: `nodpi`
- Package family: `com.google.android.GoogleCamera`

The policy lives in `device/marble/upstream-policy.json`. The currently selected
upstream release is stored in `device/marble/upstream/pixel-camera.json`.

## Automatic refresh

`.github/workflows/refresh-pixel-camera-upstream.yml` runs every day and can also
be started manually. It:

1. Runs parser/selection unit tests.
2. Reads the Pixel Camera product page on APKMirror.
3. Enumerates published variant families.
4. Rejects variants whose minimum API is newer than API 37.
5. Rejects non-`arm64-v8a` and non-`nodpi` variants.
6. Sorts compatible releases newest-first and retains an ordered fallback set.
7. Updates the discovery lock only when the candidate set actually changes.
8. Leaves the last-known-good runtime state untouched.

If APKMirror changes its page structure and no safe match can be parsed, the
resolver exits with an error instead of silently selecting an unverified build.

## Important compatibility distinction

"Compatible" here means **upstream package metadata compatibility**: Android
minimum API, CPU architecture, and DPI. It does not claim that Google's stock
Pixel Camera will run correctly on a POCO F5. Google may gate Pixel-only
features or the entire app based on device-specific behavior.

For this project, the selected release is the upstream base candidate. Runtime
camera, lens switching, HDR, Night Sight, video, stabilization, and auxiliary
camera behavior must still pass the project's POCO F5 compatibility tests before
being marked validated.

## Manual resolution

From the repository root:

```bash
python scripts/resolve_apkmirror_gcam.py
```

The repository intentionally does not commit proprietary APK/APKM files.


## Runtime approval and fallback

The discovery lock is not the runtime approval source.

- `device/marble/upstream/pixel-camera.json` tracks metadata-compatible candidates.
- `device/marble/upstream/pixel-camera-approved.json` tracks the effective
  last-known-good runtime.

Use the Windows on-device gate before promoting a candidate:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\validate-pixel-camera.ps1 `
  -ApkPath "C:\path\to\PixelCamera.apk" `
  -ConfirmMainPreview `
  -ConfirmMainCapture `
  -ConfirmFrontPreview
```

If the candidate fails, the gate records the rejection, keeps the previous
last-known-good version effective, and exposes the next older compatible
candidate. See [Runtime Compatibility Gate](RUNTIME_COMPATIBILITY_GATE.md).
