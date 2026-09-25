# Pixel Camera Runtime Compatibility Gate

This stage prevents a newly discovered Pixel Camera release from replacing a
known-working POCO F5 runtime merely because its APK metadata is newer.

The project now separates two states:

1. **Discovery candidate** — newest APKMirror release matching Android 17 / API 37,
   arm64-v8a and nodpi metadata.
2. **Last-known-good runtime** — a version that actually passed the POCO F5
   on-device gate.

The daily upstream refresh may update the first state. It must never update the
second state.

## Files

- `device/marble/upstream/pixel-camera.json`
  - generated discovery lock
  - newest candidate plus ordered older compatible candidates
- `device/marble/upstream/pixel-camera-approved.json`
  - runtime approval state
  - effective / last-known-good version
  - rejected versions
  - next fallback candidate
- `device/marble/upstream-policy.json`
  - Android 17 / API 37 compatibility policy
  - required promotion checks
- `scripts/validate-pixel-camera.ps1`
  - Windows 11 + ADB device gate
- `scripts/pixel_camera_gate.py`
  - deterministic promotion / rejection / fallback state machine

## Promotion policy

The current strict policy requires all of the following:

- APK installation succeeds
- expected package is installed
- installed `versionName` matches the candidate
- launcher activity resolves
- process stays alive after launch
- no package-associated fatal crash marker is detected
- main-camera preview is visually confirmed
- main-camera capture is visually confirmed
- front-camera preview is visually confirmed

The last three checks are explicit switches because Android cannot reliably prove
that a visually correct preview/capture occurred without device-specific UI
automation.

## Run on POCO F5

Use an APK or APKMirror APKM bundle obtained locally for the candidate version.
Proprietary APK/APKM files are not committed to this repository.

From Windows PowerShell:

```powershell
git pull

powershell -ExecutionPolicy Bypass -File .\scripts\validate-pixel-camera.ps1 `
  -ApkPath "C:\path\to\PixelCamera.apkm" `
  -ConfirmMainPreview `
  -ConfirmMainCapture `
  -ConfirmFrontPreview
```

Only pass the confirmation switches after personally verifying those camera
functions during the same validation run.

The script:

1. selects the current discovery candidate unless `-CandidateVersion` is given
2. installs a single APK with `adb install` or an APKM split bundle with `adb install-multiple`
3. verifies package and version
4. launches the application
5. checks process stability and crash markers
6. records the manual camera confirmations
7. writes a JSON validation report
8. invokes the promotion gate automatically

## Successful candidate

When every required check passes:

```text
candidate
   ↓
PROMOTED
   ↓
last-known-good
   ↓
effective runtime
```

The approved-state file is updated to the passing version.

## Failed update with an existing known-good version

When a newer candidate fails:

```text
new candidate ── FAIL
                  │
                  ├── record rejection
                  ├── keep last-known-good effective
                  └── expose next older candidate
```

A failed discovery candidate therefore cannot replace the working runtime.

## First validation with no known-good version

If the first candidate fails, the state becomes `no-approved-runtime` and the
gate points to the next older compatible candidate. Test it explicitly:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\validate-pixel-camera.ps1 `
  -ApkPath "C:\path\to\older-candidate.apk" `
  -CandidateVersion "VERSION_FROM_NEXT_CANDIDATE" `
  -ConfirmMainPreview `
  -ConfirmMainCapture `
  -ConfirmFrontPreview
```

## Downgrades

The validation script never uninstalls Pixel Camera automatically.

Testing an older fallback may require Android to accept a downgrade. This is
disabled by default. To request ADB's downgrade mode explicitly:

```powershell
-AllowDowngrade
```

If Android still rejects the downgrade, the script records the install failure
instead of deleting an existing application or its data.

## Inspect current state

```powershell
python .\scripts\pixel_camera_gate.py status
```

This reports:

- newest discovery candidate
- effective runtime
- last-known-good version
- rejected versions
- next fallback candidate

## Safety invariant

Automated upstream discovery is allowed to modify only
`pixel-camera.json`.

It must never modify `pixel-camera-approved.json`. Runtime promotion requires
on-device evidence from the POCO F5.


## APKMirror bundle support

Current Pixel Camera releases may be distributed as APKMirror bundles rather
than one standalone APK. The validator supports both:

- `.apk` — installed with `adb install -r`
- `.apkm` — extracted to a temporary directory and installed with
  `adb install-multiple -r`

The APKM archive is checked for unsafe extraction paths before extraction. The
temporary directory is removed immediately after the install attempt.

The repository ignores both APK and APKM binaries by default.
