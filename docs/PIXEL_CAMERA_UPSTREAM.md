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
