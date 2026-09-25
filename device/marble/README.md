# POCO F5 (marble) Device Profile

This directory contains validated, device-specific camera information for the POCO F5.

## Data to collect

- Android version and ROM/build
- Camera HAL version
- Camera2 hardware level per camera ID
- Logical/physical camera relationships
- Available focal lengths
- Sensor active-array sizes
- Supported output resolutions
- RAW capability
- Manual sensor controls
- Exposure ranges
- ISO ranges
- FPS ranges
- Stabilization modes
- Auxiliary camera access behavior

## Validation rule

Do not hard-code sensor names or camera-ID mappings here until they have been verified from the target device. ROMs and camera providers can expose auxiliary cameras differently.

## Suggested capture methods

Keep raw diagnostic output in a dedicated subdirectory and remove serial numbers, account information, filesystem paths, or other personal data before committing.
