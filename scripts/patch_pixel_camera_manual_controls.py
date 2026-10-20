"""Enable Pixel Camera's existing manual controls and lens selector on marble.

The 11.1 build ships the Ark manual-control UI (focus, shutter speed, ISO, and
lens selection), but its device profiles disable those flags on unrecognised
hardware. This module wraps the two reviewed boolean feature-query methods,
``Lksf;->q(Lkps;)Z`` and ``Lksf;->x(Lkps;)Z``, with a small helper that returns
true for exactly five Ark flags when ``Build.DEVICE`` starts with ``marble``.

The helper is a clean-room patch of flags already present in the app. It does
not import third-party code, resources, signatures, ads, or network endpoints.
Unknown layouts fail closed.
"""

from __future__ import annotations

import re
from typing import Any

try:
    from scripts import patch_pixel_camera_device_gate as p
except ModuleNotFoundError:
    import patch_pixel_camera_device_gate as p

FEATURE_PROFILE = "manual_controls"
MANUAL_FLAGS = (
    "camera.ark_enabled",
    "camera.ark_focus_available",
    "camera.ark_shutter_speed_available",
    "camera.ark_ISO_available",
    "camera.ark_lens_selector_available",
)
QUERY_METHODS = ("q", "x")
TARGET_DESCRIPTOR = "Lksf;"
TARGET_CLASS_RE = re.compile(r"^\.class\s+.*\s+Lksf;\s*$", re.MULTILINE)

HELPER = """\
.class public final Lpoco/ManualFeatures;
.super Ljava/lang/Object;
.source "PG"

.method private constructor <init>()V
    .registers 1

    invoke-direct {p0}, Ljava/lang/Object;-><init>()V

    return-void
.end method

.method public static isEnabled(Lkps;)Z
    .locals 4

    if-eqz p0, :cond_false

    sget-object v0, Landroid/os/Build;->DEVICE:Ljava/lang/String;
    const-string v1, "marble"
    invoke-virtual {v0, v1}, Ljava/lang/String;->startsWith(Ljava/lang/String;)Z
    move-result v2
    if-nez v2, :cond_false

    iget-object v0, p0, Lkpq;->a:Ljava/lang/String;
    if-eqz v0, :cond_false

    const-string v1, "camera.ark_enabled"
    invoke-virtual {v0, v1}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z
    move-result v2
    if-eqz v2, :cond_next_1
    const/4 v0, 0x1
    return v0

    :cond_next_1
    const-string v1, "camera.ark_focus_available"
    invoke-virtual {v0, v1}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z
    move-result v2
    if-eqz v2, :cond_next_2
    const/4 v0, 0x1
    return v0

    :cond_next_2
    const-string v1, "camera.ark_shutter_speed_available"
    invoke-virtual {v0, v1}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z
    move-result v2
    if-eqz v2, :cond_next_3
    const/4 v0, 0x1
    return v0

    :cond_next_3
    const-string v1, "camera.ark_ISO_available"
    invoke-virtual {v0, v1}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z
    move-result v2
    if-eqz v2, :cond_next_4
    const/4 v0, 0x1
    return v0

    :cond_next_4
    const-string v1, "camera.ark_lens_selector_available"
    invoke-virtual {v0, v1}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z
    move-result v2
    if-eqz v2, :cond_false
    const/4 v0, 0x1
    return v0

    :cond_false
    const/4 v0, 0x0
    return v0
.end method
"""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise p.PatchError(message)


def _wrapper(method: str) -> str:
    return f"""\
.method public final {method}(Lkps;)Z
    .registers 3

    invoke-static {{p1}}, Lpoco/ManualFeatures;->isEnabled(Lkps;)Z
    move-result v0

    if-eqz v0, :poco_manual_base_{method}

    const/4 v0, 0x1
    return v0

    :poco_manual_base_{method}
    invoke-virtual {{p0, p1}}, Lksf;->{method}$pocoBase(Lkps;)Z
    move-result v0
    return v0
.end method
"""


def patch_smali(text: str) -> tuple[str, dict[str, Any]]:
    """Wrap the reviewed ``ksf`` feature queries with the manual-controls gate."""

    _require(TARGET_CLASS_RE.search(text) is not None, "manual-controls owner changed")
    _require("Lpoco/ManualFeatures;" not in text, "manual-controls helper already present")

    patched = text
    methods: list[str] = []
    for method in QUERY_METHODS:
        signature = f".method public final {method}(Lkps;)Z"
        _require(
            patched.count(signature) == 1,
            f"expected exactly one {method}(Lkps;)Z feature query",
        )
        renamed = f".method public final {method}$pocoBase(Lkps;)Z"
        _require(renamed not in patched, f"{method}$pocoBase already present")
        patched = patched.replace(signature, renamed, 1)
        methods.append(f"Lksf;->{method}(Lkps;)Z")

    if not patched.endswith("\n"):
        patched += "\n"
    patched += "\n" + "".join(_wrapper(method) for method in QUERY_METHODS)

    _require(
        patched.count("Lpoco/ManualFeatures;->isEnabled(Lkps;)Z") == len(QUERY_METHODS),
        "manual-controls wrapper count changed",
    )
    _require(
        all(flag in HELPER for flag in MANUAL_FLAGS),
        "manual-controls helper flag set changed",
    )

    return patched, {
        "profile": FEATURE_PROFILE,
        "flags": list(MANUAL_FLAGS),
        "methods": methods,
        "device_gate": "android.os.Build.DEVICE starts with marble",
        "third_party_code_added": False,
        "ads_added": False,
        "developer_branding_added": False,
        "runtime_validation": "not_run",
    }
