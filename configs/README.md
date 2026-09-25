# GCam Configurations

Configuration files are grouped by use case and must identify the exact GCam mod/base version they target.

## Suggested groups

- `balanced/` — everyday photography
- `photo/` — still-image quality experiments
- `night/` — low-light and Night Sight tuning
- `video/` — video-focused profiles

## Required metadata

Each committed config should document:

- GCam mod name
- Exact app version/build
- Package name when relevant
- Tested Android/ROM build
- Supported lenses
- Known limitations
- Date tested
- Short explanation of changed parameters

Avoid presenting one configuration as universally compatible unless it has been tested across those variants.
