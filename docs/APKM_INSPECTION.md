# APKM inspection and integrity gate

The inspection stage treats downloaded Pixel Camera APK/APKM files as immutable signed artifacts. It never rewrites, strips, resigns, or otherwise mutates the Google-signed inputs. A separate post-verification standalone-build stage may transform a verified bundle into a project-signed APK; see [STANDALONE_APK_BUILD.md](STANDALONE_APK_BUILD.md).

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


## Native 16 KB compatibility audit

The inspector now reads ELF program headers for every native `.so` and records
the alignment of each `PT_LOAD` segment. Libraries whose load segments are
below 16 KiB are reported as incompatible candidates for investigation.

When `zipalign` is available, every base/split APK is also checked with:

```text
zipalign -c -P 16 -v 4
```

Both checks are diagnostic for the original Google-signed inputs. The later
standalone-build stage operates only after this audit passes and writes a new,
project-signed output instead of mutating the audited source files.

## Conservative split dependency planner

After an APKM audit, run:

```powershell
python .\scripts\plan_device_splits.py .\reports\pixel-camera-audit.json --output .\reports\pixel-camera-split-plan.json
```

The planner uses decoded manifest metadata such as `split`, `uses-split`,
and `configForSplit` to classify APKs as:

- `keep-base`
- `keep-dependency`
- `keep-with-parent`
- `unknown-manifest`
- `requires-device-validation`

`requires-device-validation` deliberately does **not** mean safe to remove.
It means only that no structural dependency was found in the decoded manifests
and the split can be prioritized for controlled POCO F5 A/B testing.

The reported `validation_candidate_bytes` is a test pool, not a savings claim.
No automatic split stripping is implemented.

GitHub Actions generates both the APKM audit and split-plan JSON during a
manual download, or on a scheduled run when the selected upstream version
changes.
