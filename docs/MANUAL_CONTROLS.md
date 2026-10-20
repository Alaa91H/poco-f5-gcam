# Manual controls profile

The `pixel-manual-a17` and `pixel-manual-a13` flavours add a clean-room
`manual_controls` profile to the reviewed Pixel Camera 11.1 compatibility build.
They are still the same Google package
(`com.google.android.GoogleCamera`) and use the same project signing key as the
normal flavours.

## What the profile enables

Pixel Camera 11.1 already contains the Ark manual-control UI. Its device
profiles disable those flags on unrecognised hardware. The profile makes five
existing flags return true on `marble`/`marblein`:

- `camera.ark_enabled`
- `camera.ark_focus_available`
- `camera.ark_shutter_speed_available`
- `camera.ark_ISO_available`
- `camera.ark_lens_selector_available`

The visible result, when the POCO F5 camera HAL exposes the required Camera2
keys, is manual focus, shutter-speed, ISO, and lens selection alongside the
normal Pixel Camera UI. The selector uses the app's existing camera
enumeration; the compatibility profile intentionally keeps camera IDs 3-6
excluded until real-device evidence supports exposing them.

## Implementation

`scripts/patch_pixel_camera_manual_controls.py` renames the reviewed
`Lksf;->q(Lkps;)Z` and `Lksf;->x(Lkps;)Z` methods to `q$pocoBase` and
`x$pocoBase`, then adds small wrappers with the same signatures. The wrappers
call `Lpoco/ManualFeatures;->isEnabled(Lkps;)Z` and return true for the five
exact flag names. Everything else falls through to the original method body,
including the existing non-Tensor guards.

The helper is gated at runtime to `android.os.Build.DEVICE` values starting
with `marble`. On any other device it returns false, preserving the upstream
behaviour. The build fails closed if the reviewed method layout, flag set, or
class descriptor changes.

The profile adds:

- no third-party DEX, native libraries, resources, or signing identity;
- no advertising SDK or ad unit;
- no developer branding, links, or network endpoints;
- no new permissions;
- no PairIP change.

It does **not** implement third-party lib-patcher or XML-config features. Those
are separate application behaviours and are outside this clean-room profile.

## Runtime validation

The feature depends on the POCO F5 Camera2/HAL paths for manual focus, shutter
speed, ISO, and logical/physical lens selection. Successful compilation and
installation do not prove those paths work.

Run the strict smoke test on a real POCO F5 for each Android version you intend
to use:

```powershell
pwsh ./scripts/test-pixel-camera-runtime.ps1 -Strict
```

Record the resulting report. Until that passes, the manual-controls flavours
remain experimental and runtime-unvalidated.
