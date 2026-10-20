#!/usr/bin/env python3
"""Build every POCO F5 Pixel Camera flavour in one deterministic pass.

The committed flavour policy defines four Pixel Camera 11.1 builds:

* ``pixel-a17`` and ``pixel-a13`` use only the reviewed compatibility patches.
* ``pixel-manual-a17`` and ``pixel-manual-a13`` additionally apply the
  clean-room ``manual_controls`` profile, which enables the app's existing
  focus, shutter, ISO, and lens-selector UI on marble.

Every output is signed with the project key. No third-party APK is rebuilt,
re-signed, or delivered.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

try:
    from scripts.build_standalone_pixel_camera import (
        BuildError,
        build_standalone,
    )
except ModuleNotFoundError:
    from build_standalone_pixel_camera import (
        BuildError,
        build_standalone,
    )

DEFAULT_FLAVORS = Path("device/marble/build-flavors.json")
DEFAULT_UPSTREAM = Path("device/marble/upstream/pixel-camera.json")
SUPPORTED_FEATURE_PROFILES = {None, "manual_controls"}
API_TO_ANDROID_LABEL = {
    30: "11",
    31: "12",
    32: "12L",
    33: "13",
    34: "14",
    35: "15",
    36: "16",
    37: "17",
}


class FlavorError(RuntimeError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FlavorError(f"could not read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise FlavorError(f"{path} must contain a JSON object")
    return data


def load_flavor_policy(path: Path) -> list[dict[str, Any]]:
    data = load_json(path)
    flavors = data.get("flavors")
    if not isinstance(flavors, list) or not flavors:
        raise FlavorError(f"{path} has no flavours")
    seen: set[str] = set()
    for flavor in flavors:
        if not isinstance(flavor, dict):
            raise FlavorError("flavour entries must be objects")
        flavor_id = flavor.get("id")
        if not isinstance(flavor_id, str) or not flavor_id:
            raise FlavorError("every flavour needs a non-empty id")
        if flavor_id in seen:
            raise FlavorError(f"duplicate flavour id: {flavor_id}")
        seen.add(flavor_id)

        if flavor.get("kind") != "pixel_camera_patched":
            raise FlavorError(
                f"unsupported flavour kind for {flavor_id}: "
                f"{flavor.get('kind')}"
            )
        if not isinstance(flavor.get("label"), str) or not flavor["label"]:
            raise FlavorError(f"flavour {flavor_id} has no label")
        for field in ("manifest_min_sdk", "expected_min_sdk", "output_template"):
            if field not in flavor:
                raise FlavorError(f"pixel flavour {flavor_id} is missing {field}")
        if not isinstance(flavor["manifest_min_sdk"], int):
            raise FlavorError(f"flavour {flavor_id} has a non-integer manifest_min_sdk")
        if not isinstance(flavor["expected_min_sdk"], int):
            raise FlavorError(f"flavour {flavor_id} has a non-integer expected_min_sdk")
        if flavor["manifest_min_sdk"] != flavor["expected_min_sdk"]:
            raise FlavorError(
                f"pixel flavour {flavor_id} must use the same manifest and "
                "expected minSdkVersion"
            )
        if not isinstance(flavor["output_template"], str):
            raise FlavorError(f"flavour {flavor_id} has a non-string output_template")
        if flavor.get("feature_profile") not in SUPPORTED_FEATURE_PROFILES:
            raise FlavorError(
                f"flavour {flavor_id} has an unsupported feature_profile: "
                f"{flavor.get('feature_profile')!r}"
            )
    return flavors


def selected_flavors(
    flavors: list[dict[str, Any]],
    requested: list[str],
) -> list[dict[str, Any]]:
    by_id = {flavor["id"]: flavor for flavor in flavors}
    if not requested:
        return flavors
    missing = [flavor_id for flavor_id in requested if flavor_id not in by_id]
    if missing:
        raise FlavorError("unknown flavour id(s): " + ", ".join(missing))
    return [by_id[flavor_id] for flavor_id in requested]


def upstream_version(path: Path) -> str:
    data = load_json(path)
    try:
        version = data["selected"]["version"]
    except (KeyError, TypeError) as exc:
        raise FlavorError(f"{path} has no selected.version") from exc
    if not isinstance(version, str) or not version:
        raise FlavorError(f"{path} has an invalid selected.version")
    return version


def render_output_name(template: str, *, version: str, android_label: str) -> str:
    try:
        rendered = template.format(version=version, android_label=android_label)
    except (KeyError, ValueError) as exc:
        raise FlavorError(f"invalid output template {template!r}: {exc}") from exc
    if "/" in rendered or "\\" in rendered or rendered in {".", ".."}:
        raise FlavorError(f"unsafe output name: {rendered!r}")
    return rendered


def android_label(min_sdk: int) -> str:
    return API_TO_ANDROID_LABEL.get(min_sdk, f"API{min_sdk}")


def delivery_caption(entry: dict[str, Any], signing_mode: str) -> str:
    parts = [
        "POCO F5 camera flavour",
        str(entry["label"]),
        "Experimental: device validation pending",
        f"Version: {entry['version_name']}",
        f"Android: {android_label(int(entry['min_sdk']))}+ / API {entry['min_sdk']}",
        "ABI: arm64-v8a",
        f"Size: {entry['size_bytes']} bytes",
        f"SHA-256: {entry['sha256']}",
        f"Signing: {signing_mode}",
        f"Package: {entry['package_name']}",
        "Install: single APK",
    ]
    if entry.get("feature_profile") == "manual_controls":
        parts.append(
            "Manual focus/shutter/ISO and lens selector; clean-room patch; "
            "no third-party code or ads"
        )
    return " | ".join(parts)


def _require_tool(path: str | None, name: str) -> str:
    if path:
        candidate = Path(path)
        if candidate.is_file():
            return os.fspath(candidate)
        found = shutil.which(path)
        if found:
            return found
        raise FlavorError(f"{name} not found: {path}")
    found = shutil.which(name)
    if not found:
        raise FlavorError(f"{name} was not found on PATH")
    return found


def build_flavor_matrix(
    *,
    flavor_policy_path: Path,
    upstream_path: Path,
    flavor_ids: list[str],
    pixel_source: Path | None,
    output_dir: Path,
    apkeditor: str | None,
    baksmali: str | None,
    smali: str | None,
    java: str,
    zipalign: str | None,
    apksigner: str | None,
    aapt2: str | None,
    keystore: Path | None,
    key_alias: str | None,
    ks_pass_env: str,
    key_pass_env: str,
    signing_mode: str | None = None,
) -> dict[str, Any]:
    flavors = selected_flavors(
        load_flavor_policy(flavor_policy_path),
        flavor_ids,
    )
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if pixel_source is None:
        raise FlavorError("--pixel-source is required")
    pixel_source = pixel_source.resolve()
    if not pixel_source.is_file():
        raise FlavorError(f"Pixel Camera source does not exist: {pixel_source}")
    if not apkeditor or not baksmali or not smali:
        raise FlavorError(
            "Pixel Camera flavours require --apkeditor, --baksmali, and --smali"
        )
    if keystore is None or not keystore.is_file():
        raise FlavorError("Pixel Camera flavours require a signing keystore")
    if not key_alias:
        raise FlavorError("Pixel Camera flavours require --key-alias")
    if not aapt2 or not apksigner or not zipalign:
        raise FlavorError(
            "Pixel Camera flavours require --aapt2, --apksigner, and --zipalign"
        )

    version = upstream_version(upstream_path)
    mode = signing_mode or os.environ.get("GCMOD_SIGNING_MODE", "repository-keystore")
    entries: list[dict[str, Any]] = []
    seen_outputs: set[Path] = set()

    for flavor in flavors:
        flavor_id = flavor["id"]
        feature_profile = flavor.get("feature_profile")
        android = str(flavor.get("android_label") or flavor["manifest_min_sdk"])
        filename = render_output_name(
            str(flavor["output_template"]),
            version=version,
            android_label=android,
        )
        apk_path = output_dir / filename
        report_path = output_dir / (apk_path.stem + ".build.json")
        if apk_path in seen_outputs:
            raise FlavorError(f"duplicate output path: {apk_path}")
        seen_outputs.add(apk_path)

        try:
            report = build_standalone(
                pixel_source,
                apk_path,
                apkeditor=Path(apkeditor),
                baksmali=Path(baksmali),
                smali=Path(smali),
                java=_require_tool(java, "java"),
                zipalign=_require_tool(zipalign, "zipalign"),
                apksigner=_require_tool(apksigner, "apksigner"),
                aapt2=_require_tool(aapt2, "aapt2"),
                keystore=keystore,
                key_alias=key_alias,
                ks_pass_env=ks_pass_env,
                key_pass_env=key_pass_env,
                expected_package="com.google.android.GoogleCamera",
                expected_abi="arm64-v8a",
                expected_min_sdk=int(flavor["expected_min_sdk"]),
                manifest_min_sdk=int(flavor["manifest_min_sdk"]),
                report_path=report_path,
                flavor_id=flavor_id,
                flavor_label=str(flavor["label"]),
                feature_profile=feature_profile,
            )
        except BuildError as exc:
            raise FlavorError(f"failed to build {flavor_id}: {exc}") from exc

        output = report["output"]
        if output["min_sdk"] != int(flavor["expected_min_sdk"]):
            raise FlavorError(
                f"{flavor_id} reported minSdk {output['min_sdk']}, "
                f"expected {flavor['expected_min_sdk']}"
            )
        if output["target_sdk"] != 37:
            raise FlavorError(
                f"{flavor_id} changed targetSdkVersion to {output['target_sdk']}; "
                "the Pixel Camera 11.1 policy requires 37"
            )
        entry = {
            "id": flavor_id,
            "kind": "pixel_camera_patched",
            "label": str(flavor["label"]),
            "feature_profile": feature_profile,
            "apk": os.fspath(apk_path.resolve()),
            "report": os.fspath(report_path.resolve()),
            "sha256": output["sha256"],
            "size_bytes": output["size_bytes"],
            "package_name": output["package_name"],
            "version_code": output["version_code"],
            "version_name": output["version_name"],
            "min_sdk": output["min_sdk"],
            "target_sdk": output["target_sdk"],
            "runtime_validation": "not_run",
            "signing_mode": mode,
        }
        entry["delivery_caption"] = delivery_caption(entry, mode)
        entries.append(entry)

    return {
        "schema_version": 1,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "pixel_camera_version": version,
        "flavors": entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--flavor-policy", type=Path, default=DEFAULT_FLAVORS)
    parser.add_argument("--upstream", type=Path, default=DEFAULT_UPSTREAM)
    parser.add_argument("--flavor", action="append", default=[])
    parser.add_argument("--pixel-source", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--apkeditor")
    parser.add_argument("--baksmali")
    parser.add_argument("--smali")
    parser.add_argument("--java", default="java")
    parser.add_argument("--zipalign")
    parser.add_argument("--apksigner")
    parser.add_argument("--aapt2")
    parser.add_argument("--keystore", type=Path)
    parser.add_argument("--key-alias")
    parser.add_argument("--ks-pass-env", default="GCMOD_KEYSTORE_PASSWORD")
    parser.add_argument("--key-pass-env", default="GCMOD_KEY_PASSWORD")
    parser.add_argument("--signing-mode")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        manifest = build_flavor_matrix(
            flavor_policy_path=args.flavor_policy,
            upstream_path=args.upstream,
            flavor_ids=args.flavor,
            pixel_source=args.pixel_source,
            output_dir=args.output_dir,
            apkeditor=args.apkeditor,
            baksmali=args.baksmali,
            smali=args.smali,
            java=args.java,
            zipalign=args.zipalign,
            apksigner=args.apksigner,
            aapt2=args.aapt2,
            keystore=args.keystore,
            key_alias=args.key_alias,
            ks_pass_env=args.ks_pass_env,
            key_pass_env=args.key_pass_env,
            signing_mode=args.signing_mode,
        )
        if args.manifest:
            manifest_path = args.manifest.resolve()
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(
                json.dumps(manifest, indent=2) + "\n",
                encoding="utf-8",
            )
        if args.json:
            print(json.dumps(manifest, indent=2))
        else:
            for entry in manifest["flavors"]:
                print(f"{entry['id']}: {entry['apk']}")
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
