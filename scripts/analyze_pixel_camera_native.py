#!/usr/bin/env python3
"""Analyze Pixel Camera's native libgcam tuning paths without rebuilding the APK.

Accepts APK/APKM/APKS/XAPK/ZIP input. For split bundles it finds the one nested
APK containing lib/arm64-v8a/libgcastartup.so, extracts only that member in
memory, and reuses the fail-safe ELF/AArch64 diagnostic logic from the
compatibility patcher. No APK or native bytes are modified.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path
from typing import Any

try:
    from scripts.patch_pixel_camera_device_gate import (
        GCASTARTUP_LIBRARY,
        PatchError,
        analyze_libgcam_tuning,
    )
except ModuleNotFoundError:
    from patch_pixel_camera_device_gate import (
        GCASTARTUP_LIBRARY,
        PatchError,
        analyze_libgcam_tuning,
    )


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def find_native_library(package: Path) -> tuple[bytes, dict[str, Any]]:
    if not package.is_file() or not zipfile.is_zipfile(package):
        raise PatchError(f"input is not a valid ZIP-based Android package: {package}")

    matches: list[tuple[bytes, dict[str, Any]]] = []

    with zipfile.ZipFile(package, "r") as outer:
        direct = [name for name in outer.namelist() if name == GCASTARTUP_LIBRARY]
        for member in direct:
            data = outer.read(member)
            matches.append(
                (
                    data,
                    {
                        "container": package.name,
                        "nested_apk": None,
                        "member": member,
                    },
                )
            )

        for info in outer.infolist():
            if not info.filename.lower().endswith(".apk"):
                continue
            nested_bytes = outer.read(info)
            if not zipfile.is_zipfile(io.BytesIO(nested_bytes)):
                continue
            with zipfile.ZipFile(io.BytesIO(nested_bytes), "r") as nested:
                names = [
                    name
                    for name in nested.namelist()
                    if name == GCASTARTUP_LIBRARY
                ]
                for member in names:
                    data = nested.read(member)
                    matches.append(
                        (
                            data,
                            {
                                "container": package.name,
                                "nested_apk": info.filename,
                                "member": member,
                            },
                        )
                    )

    if len(matches) != 1:
        rendered = [
            {
                **location,
                "size_bytes": len(data),
                "sha256": sha256_bytes(data),
            }
            for data, location in matches
        ]
        raise PatchError(
            "expected exactly one libgcastartup.so in package/bundle; "
            f"found {len(matches)}: {json.dumps(rendered, sort_keys=True)}"
        )

    return matches[0]


def analyze_package(package: Path) -> dict[str, Any]:
    library, location = find_native_library(package)
    return {
        "schema_version": 1,
        "status": "diagnostic_only",
        "input": {
            "path": str(package.resolve()),
            "size_bytes": package.stat().st_size,
        },
        "library": {
            **location,
            "size_bytes": len(library),
            "sha256": sha256_bytes(library),
        },
        "tuning": analyze_libgcam_tuning(library),
        "modifications_performed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    try:
        report = analyze_package(args.input)
        rendered = json.dumps(report, indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
        print(rendered, end="")
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
