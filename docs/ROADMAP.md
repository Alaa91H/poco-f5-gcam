# Development Roadmap

## Phase 1 — Device baseline

- Record Android/ROM version used for testing.
- Capture Camera2 API capabilities for every exposed camera ID.
- Identify main, ultrawide, macro, and front camera mappings.
- Document resolutions, RAW support, stabilization modes, FPS ranges, and auxiliary-camera access.
- Establish a reproducible baseline before applying tuning.

## Phase 2 — Lens mapping and GCam compatibility matrix

First verify physical Camera ID mappings and package-specific auxiliary-camera exposure. Then track each tested GCam mod/base independently:

- Application launches and camera switching
- Main camera
- Auxiliary cameras
- HDR / HDR+ Enhanced
- Night Sight
- Portrait
- Video recording
- EIS/OIS behavior where exposed
- Slow motion
- Front camera
- RAW/DNG
- Known crashes or regressions

## Phase 3 — Image tuning

Create separate profiles for:

- Balanced daily use
- Maximum detail
- Low light / Night Sight
- Natural color
- Video

Evaluate changes independently for noise reduction, sharpening, tone mapping, AWB, exposure, saturation, and HDR behavior.

## Phase 4 — Stability and regression testing

- Define repeatable test scenes.
- Compare before/after samples.
- Track crashes and unsupported combinations.
- Keep configs tied to exact GCam mod/base versions.
- Avoid device-wide claims until behavior is validated.

## Phase 5 — Releases

Once a configuration is stable:

1. Freeze its supported GCam version.
2. Document required settings.
3. Add a changelog.
4. Publish the config and checksums.
5. Keep proprietary APKs outside this repository.
