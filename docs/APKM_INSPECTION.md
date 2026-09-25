# APKM inspection and integrity gate

The project treats downloaded Pixel Camera APK/APKM files as immutable signed artifacts. It does not rewrite, strip, resign, zipalign, or otherwise patch Google-owned binaries.

## Goals

The inspection pipeline answers four questions before any on-device testing:

1. What consumes space inside the base APK and each split?
2. Which ABIs and native libraries are present?
3. Are every base/split APK signature valid and signed by an approved Google certificate?
4. Are there PairIP indicators that should be recorded as a compatibility constraint?

The tool is `scripts/inspect_apkm.py` and the device policy is `device/marble/apkm-audit-policy.json`.

## Read-only size bill of materials

Run:

    python .\scripts\inspect_apkm.py .\downloads\pixel-camera\PixelCamera.apkm --output .\reports\pixel-camera-audit.json

The report includes package SHA-256, APK/split count, largest APKs by size, compressed and uncompressed bytes by category, DEX/assets/resources/native payloads, ABIs, PairIP indicators, optional manifest metadata via `apkanalyzer`, and optional certificate verification via `apksigner`.

## Signature gate

GitHub Actions verifies every APK inside the downloaded bundle with `apksigner verify --verbose --print-certs`.

The gate fails if any APK signature does not verify, base/split APKs do not share one signer, or the signer is not present in `device/marble/apkm-audit-policy.json`.

The pinned signer is deliberately fail-closed. A legitimate upstream key change must be reviewed explicitly rather than silently accepted.

## PairIP

PairIP is **detected, not removed**. Detection checks archive paths and DEX payloads for PairIP indicators and records affected APKs in the report.

The default policy is `report-only`, because removing application protection would require modifying and resigning the proprietary APK and would destroy the original Google signature identity.

## Split-size optimization policy

No feature split is removed automatically.

The first stage is measurement only. A future device-targeted split planner may mark a split removable only after its manifest/dependency relationship is known, it is not required by another installed split, POCO F5 A/B testing shows no loss of a working feature, installation succeeds using the unmodified Google-signed APK set, and regression tests still pass.

This preserves the project's rule: reduce transferred/installed payload only through safe split selection, never by deleting content from signed APKs.
