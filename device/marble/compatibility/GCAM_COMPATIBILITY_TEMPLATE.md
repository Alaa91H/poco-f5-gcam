# POCO F5 GCam Compatibility Matrix

> Status values: `PASS`, `PARTIAL`, `FAIL`, `NOT_TESTED`, `NOT_EXPOSED`.

Every row must identify the exact GCam build and package name. Do not copy results between package variants unless they were tested independently.

| Mod / build | Package | Main photo | Ultrawide | Macro | Front | HDR+ | Night Sight | Portrait | 4K video | 60 FPS | EIS | Slow motion | RAW | Stability | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TBD | TBD | NOT_TESTED | NOT_TESTED | NOT_TESTED | NOT_TESTED | NOT_TESTED | NOT_TESTED | NOT_TESTED | NOT_TESTED | NOT_TESTED | NOT_TESTED | NOT_TESTED | NOT_TESTED | NOT_TESTED | TBD |

## Evidence requirements

For every tested build, record:

- exact APK/mod build name and version
- exact Android package name
- ROM/build fingerprint
- Camera2 report captured for that package when auxiliary-camera exposure differs
- verified physical Camera ID mapping
- crash/reproduction notes
- test date

## Stability rule

A feature should be marked `PASS` only after repeated use without a reproducible crash or corrupted output. A feature that opens but fails intermittently is `PARTIAL`, not `PASS`.