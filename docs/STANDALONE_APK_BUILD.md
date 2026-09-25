# Standalone POCO F5 Pixel Camera APK

The build pipeline can transform the selected APKMirror Pixel Camera bundle into
one standalone APK for the POCO F5 / Android 17 target.

## Pipeline

1. Download the newest compatible APK/APKM and verify the published SHA-256.
2. Verify every original APK/split signature against the approved Google signer.
3. Download the pinned APKEditor release and verify its SHA-256.
4. Merge the split bundle into one standalone APK without dropping feature
   splits.
5. Remove obsolete split/signature metadata created by the bundle container.
6. Align native libraries for 16 KiB page-size devices with Android `zipalign`.
7. Sign the transformed APK with the project signing key.
8. Verify the final signature, ZIP alignment, package name, SDK level, and ABI.
9. Write a JSON provenance report and upload the APK/report as workflow
   artifacts.
10. When Telegram credentials are configured, deliver the final APK through the
    local Bot API path.

The policy is stored in `device/marble/standalone-apk-policy.json` and the
builder is `scripts/build_standalone_pixel_camera.py`.

## Signing

Any merge or binary modification invalidates Google's original APK signature.
The final APK therefore uses a project-owned key.

For stable upgradeable builds configure these repository secrets:

- `GCMOD_KEYSTORE_B64`
- `GCMOD_KEY_ALIAS`
- `GCMOD_KEYSTORE_PASSWORD`
- `GCMOD_KEY_PASSWORD`

`GCMOD_KEYSTORE_B64` is the base64-encoded binary keystore. The workflow never
commits the keystore or passwords.

Pull-request validation may use an ephemeral CI key when those secrets are
absent. Scheduled and manually dispatched production builds fail closed unless
all four stable signing secrets are configured, preventing non-upgradeable APKs
from being distributed as production outputs.

## Safety and compatibility rules

The build is fail-closed when the original Google signature gate fails, the
pinned APKEditor checksum changes, the final package name changes unexpectedly,
the output contains a non-target native ABI, signature verification fails, or
16 KiB ZIP alignment verification fails.

The pipeline does **not** remove PairIP, bypass application protection, or
automatically delete feature splits. Those operations are deliberately excluded
from the standalone policy.

The final package keeps `com.google.android.GoogleCamera`. Because the project
signature differs from Google's signature, it cannot update an already
installed Google-signed package with the same application ID.

## CI production hardening

Feature branches are validated through pull-request workflows instead of running
duplicate push and pull-request jobs. Large standalone APK artifacts use a
14-day retention period. Telegram single-file delivery fails early if the APK
exceeds 2 GiB.

The Telegram Local Bot API source is pinned to
`e3e9dd8e5b3d7ab8537cd5a10dc31d5ffa8f82d1` so the delivery toolchain is
reproducible and its compiled binary cache remains stable until the pin is
intentionally updated.
