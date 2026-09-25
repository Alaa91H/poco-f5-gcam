# Pixel Camera upstream tracking

The repository tracks the newest **APKMirror Pixel Camera** release whose published
variant metadata matches the POCO F5 target policy.

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
6. Selects the numerically newest remaining release.
7. Updates the lock file only when the selected release actually changes.

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
