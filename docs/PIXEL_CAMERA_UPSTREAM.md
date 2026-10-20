# Pixel Camera upstream tracking

The repository selects and downloads the newest **APKMirror Pixel Camera**
release whose published variant metadata matches the POCO F5 target policy.

## Target

- Device: POCO F5 5G (`marble`)
- ROM: Evolution X
- Android: 17 / API 37
- ABI: `arm64-v8a`
- DPI: `nodpi`
- Package family: `com.google.android.GoogleCamera`

The policy is stored in:

```text
device/marble/upstream-policy.json
```

The selected release metadata is stored in:

```text
device/marble/upstream/pixel-camera.json
```

## Selection behavior

The resolver:

1. reads the current Pixel Camera releases from APKMirror
2. rejects releases requiring an API newer than API 37
3. rejects variants that do not match `arm64-v8a`
4. rejects variants that do not match `nodpi`
5. sorts compatible releases numerically
6. selects only the newest compatible release

If APKMirror changes its page structure and the metadata cannot be verified
safely, the resolver fails closed rather than selecting an unknown file.

## Download

Run from the repository root:

```powershell
python .\scripts\resolve_apkmirror_gcam.py
python .\scripts\download_latest_pixel_camera.py
```

The downloaded APK/APKM is written under:

```text
downloads/pixel-camera/
```

The downloader follows APKMirror's official download flow, including the
`download.php` handler, and verifies SHA-256 when APKMirror publishes a bundle
hash.

The proprietary Google binary is intentionally excluded from Git.

## GitHub Actions

`.github/workflows/refresh-pixel-camera-upstream.yml`:

- runs every day
- can be started manually
- runs resolver/downloader unit tests
- resolves the current newest compatible release
- verifies the live APKMirror download path
- on a scheduled run, downloads only when the selected version changes
- on a manual run, always downloads the current selected release
- commits only the metadata lock when the selected version changes

The downloaded binary exists only in the ephemeral runner unless a later build
step consumes it. It is not committed or published by this repository.

## Meaning of compatible

Here, **compatible** means the upstream package metadata matches Android 17 /
API 37, `arm64-v8a`, and `nodpi`.

This selection does not claim every Pixel-only camera feature will work on the
POCO F5. Device-specific camera compatibility remains a separate development
concern from choosing the upstream version.

## Older Android versions

The resolver intentionally keeps selecting the newest Android 17 / API 37
variant, because the reviewed 11.1 compatibility patches are tied to that exact
upstream build. Separately, the standalone build lowers the merged APK's
manifest `minSdkVersion` from 37 to 33 so the output can be installed on
Android 13 and newer. That is an install-time manifest change only; it does not
mean Google supports Pixel Camera 11.1 on those releases.

If a Google-supported base is required for Android 13, select the APKMirror
`(arm64-v8a) (nodpi) (Android 13+)` variant (currently Pixel Camera 8.8.x)
instead. That base is not covered by the 11.0/11.1 patch profiles in this
repository and would need its own reviewed compatibility work.

## Flavour matrix

The upstream lock still selects the newest Android 17 / API 37 build. The
four-flavour pipeline then produces:

- `pixel-a17`: the selected Pixel Camera build with the reviewed compatibility
  patches and the upstream `minSdkVersion=37`;
- `pixel-a13`: the same build with the reviewed patches and an install-time
  `minSdkVersion=33` manifest override;
- `pixel-manual-a17`: the Android 17+ build plus the clean-room
  `manual_controls` profile;
- `pixel-manual-a13`: the Android 13+ build plus the same manual profile.

All four flavours come from the same selected Pixel Camera 11.1 source and use
the same reviewed DEX/native patches. The manual profile only enables five
existing Ark feature flags and adds a small device-gated helper; it imports no
third-party code or signing identity. See
[docs/MANUAL_CONTROLS.md](MANUAL_CONTROLS.md).
