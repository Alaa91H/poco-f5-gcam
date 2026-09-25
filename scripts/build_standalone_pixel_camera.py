#!/usr/bin/env python3
"""Build a standalone, installable POCO F5 Pixel Camera APK from APK/APKM/APKS/XAPK.

The pipeline intentionally preserves application payloads and does not bypass PairIP
or other application protections. Split bundles are merged with a pinned APKEditor
binary, then the result is page-aligned, re-signed, verified, and described in a
machine-readable report.

Because any transformation invalidates Google's original signature, the output is
signed with a project/user key. It therefore cannot update an installation that is
signed by Google's key.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

try:
    from scripts.patch_pixel_camera_device_gate import patch_apk as patch_device_gate
except ModuleNotFoundError:
    from patch_pixel_camera_device_gate import patch_apk as patch_device_gate

SUPPORTED_SUFFIXES = {".apk", ".apkm", ".apks", ".xapk", ".zip"}
PACKAGE_RE = re.compile(r"package:\s+name='([^']+)'")
VERSION_CODE_RE = re.compile(r"versionCode='([^']+)'")
VERSION_NAME_RE = re.compile(r"versionName='([^']+)'")
SDK_RE = re.compile(r"sdkVersion:'([^']+)'")
TARGET_SDK_RE = re.compile(r"targetSdkVersion:'([^']+)'")
CERT_SHA256_RE = re.compile(
    r"Signer #\d+ certificate SHA-256 digest:\s*([0-9a-f:]{64,95})",
    re.IGNORECASE,
)


class BuildError(RuntimeError):
    pass


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(command: list[str], *, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    if completed.returncode != 0:
        rendered = " ".join(command[:4])
        raise BuildError(
            f"command failed ({completed.returncode}): {rendered}\n"
            f"{completed.stdout[-6000:]}"
        )
    return completed.stdout


def resolve_tool(value: str | None, name: str) -> str:
    if value:
        candidate = Path(value)
        if candidate.is_file():
            return os.fspath(candidate)
        found = shutil.which(value)
        if found:
            return found
        raise BuildError(f"{name} not found: {value}")
    found = shutil.which(name)
    if not found:
        raise BuildError(f"{name} was not found on PATH")
    return found


def ensure_android_zip(path: Path) -> None:
    if not path.is_file():
        raise BuildError(f"package does not exist: {path}")
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise BuildError(f"unsupported package suffix: {path.suffix}")
    if not zipfile.is_zipfile(path):
        raise BuildError(f"package is not a valid ZIP-based Android package: {path}")


def apk_abis(path: Path) -> list[str]:
    abis: set[str] = set()
    with zipfile.ZipFile(path, "r") as archive:
        for name in archive.namelist():
            parts = name.split("/")
            if len(parts) >= 3 and parts[0] == "lib" and parts[-1].endswith(".so"):
                abis.add(parts[1])
    return sorted(abis)


def aapt_badging(path: Path, aapt2: str) -> dict[str, Any]:
    output = run([aapt2, "dump", "badging", os.fspath(path)])
    package_match = PACKAGE_RE.search(output)
    if not package_match:
        raise BuildError("aapt2 could not determine package name from output APK")
    return {
        "package_name": package_match.group(1),
        "version_code": _match_or_none(VERSION_CODE_RE, output),
        "version_name": _match_or_none(VERSION_NAME_RE, output),
        "min_sdk": _match_or_none(SDK_RE, output),
        "target_sdk": _match_or_none(TARGET_SDK_RE, output),
        "raw_tail": "\n".join(output.splitlines()[-40:]),
    }


def _match_or_none(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group(1) if match else None


def signing_certificates(verify_output: str) -> list[str]:
    result: list[str] = []
    for match in CERT_SHA256_RE.finditer(verify_output):
        value = match.group(1).replace(":", "").lower()
        if value not in result:
            result.append(value)
    return result


def output_name(version: str) -> str:
    safe = re.sub(r"[^0-9A-Za-z._-]+", "-", version).strip("-") or "unknown"
    return f"PixelCamera-{safe}-POCO-F5-Android17.apk"


def build_standalone(
    source: Path,
    output: Path,
    *,
    apkeditor: Path,
    baksmali: Path,
    smali: Path,
    java: str,
    zipalign: str,
    apksigner: str,
    aapt2: str,
    keystore: Path,
    key_alias: str,
    ks_pass_env: str,
    key_pass_env: str,
    expected_package: str,
    expected_abi: str,
    expected_min_sdk: int,
    report_path: Path | None,
) -> dict[str, Any]:
    source = source.resolve()
    output = output.resolve()
    apkeditor = apkeditor.resolve()
    baksmali = baksmali.resolve()
    smali = smali.resolve()
    keystore = keystore.resolve()

    ensure_android_zip(source)
    if not apkeditor.is_file():
        raise BuildError(f"APKEditor jar not found: {apkeditor}")
    if not keystore.is_file():
        raise BuildError(f"signing keystore not found: {keystore}")
    if not os.environ.get(ks_pass_env):
        raise BuildError(f"signing password environment variable is empty: {ks_pass_env}")
    if not os.environ.get(key_pass_env):
        raise BuildError(f"key password environment variable is empty: {key_pass_env}")

    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="poco-f5-standalone-") as temp:
        root = Path(temp)
        merged = root / "merged.apk"
        patched = root / "device-gate-patched.apk"
        aligned = root / "aligned.apk"
        signed = root / "signed.apk"

        merge_output = run(
            [
                java,
                "-Xmx5g",
                "-jar",
                os.fspath(apkeditor),
                "m",
                "-i",
                os.fspath(source),
                "-o",
                os.fspath(merged),
                "-clean-meta",
                "-validate-modules",
                "-f",
            ]
        )
        ensure_android_zip(merged)

        device_gate_patch = patch_device_gate(
            merged,
            patched,
            baksmali=baksmali,
            smali=smali,
            java=java,
        )
        ensure_android_zip(patched)

        run(
            [
                zipalign,
                "-P",
                "16",
                "-f",
                "-v",
                "4",
                os.fspath(patched),
                os.fspath(aligned),
            ]
        )

        sign_env = os.environ.copy()
        run(
            [
                apksigner,
                "sign",
                "--ks",
                os.fspath(keystore),
                "--ks-key-alias",
                key_alias,
                "--ks-pass",
                f"env:{ks_pass_env}",
                "--key-pass",
                f"env:{key_pass_env}",
                "--out",
                os.fspath(signed),
                os.fspath(aligned),
            ],
            env=sign_env,
        )

        verify_output = run(
            [
                apksigner,
                "verify",
                "--verbose",
                "--print-certs",
                os.fspath(signed),
            ]
        )
        run([zipalign, "-c", "-P", "16", "-v", "4", os.fspath(signed)])

        metadata = aapt_badging(signed, aapt2)
        if metadata["package_name"] != expected_package:
            raise BuildError(
                "unexpected output package: "
                f"{metadata['package_name']} (expected {expected_package})"
            )

        if metadata["min_sdk"] is not None:
            try:
                min_sdk = int(metadata["min_sdk"])
            except ValueError as exc:
                raise BuildError(f"non-numeric minSdkVersion: {metadata['min_sdk']}") from exc
            if min_sdk > expected_min_sdk:
                raise BuildError(
                    f"output minSdkVersion {min_sdk} exceeds target API {expected_min_sdk}"
                )

        abis = apk_abis(signed)
        foreign_abis = [abi for abi in abis if abi != expected_abi]
        if foreign_abis:
            raise BuildError(
                f"output contains non-target native ABIs: {', '.join(foreign_abis)}"
            )
        if abis and expected_abi not in abis:
            raise BuildError(f"output does not contain expected ABI {expected_abi}")

        shutil.copy2(signed, output)

    report = {
        "schema_version": 1,
        "source": {
            "path": os.fspath(source),
            "size_bytes": source.stat().st_size,
            "sha256": sha256_path(source),
            "suffix": source.suffix.lower(),
        },
        "output": {
            "path": os.fspath(output),
            "size_bytes": output.stat().st_size,
            "sha256": sha256_path(output),
            "package_name": metadata["package_name"],
            "version_code": metadata["version_code"],
            "version_name": metadata["version_name"],
            "min_sdk": metadata["min_sdk"],
            "target_sdk": metadata["target_sdk"],
            "abis": abis,
            "signer_sha256": signing_certificates(verify_output),
            "zipalign_page_size": 16384,
        },
        "transformations": [
            "merge_split_bundle_to_standalone_apk",
            "clean_obsolete_split_signature_metadata",
            "redirect_unsupported_device_gate_to_common_finalization",
            "zipalign_16k_native_libraries",
            "resign_with_project_key",
        ],
        "security": {
            "original_google_signature_preserved": False,
            "unsupported_device_gate_patch_performed": True,
            "pairip_bypass_performed": False,
            "feature_splits_removed": False,
        },
        "compatibility_patch": device_gate_patch,
        "runtime_validation": {
            "status": "not_run",
            "required_for_distribution": True,
            "target_device": "POCO F5 (marble/marblein)",
            "target_api": 37,
        },
        "distribution": {
            "status": "experimental-unvalidated",
            "automatic_delivery_allowed": False,
        },
        "tooling": {
            "apkeditor_output_tail": "\n".join(merge_output.splitlines()[-40:]),
        },
    }

    if report_path:
        report_path = report_path.resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--apkeditor", required=True, type=Path)
    parser.add_argument("--baksmali", required=True, type=Path)
    parser.add_argument("--smali", required=True, type=Path)
    parser.add_argument("--java", default="java")
    parser.add_argument("--zipalign")
    parser.add_argument("--apksigner")
    parser.add_argument("--aapt2")
    parser.add_argument("--keystore", required=True, type=Path)
    parser.add_argument("--key-alias", required=True)
    parser.add_argument("--ks-pass-env", default="GCMOD_KEYSTORE_PASSWORD")
    parser.add_argument("--key-pass-env", default="GCMOD_KEY_PASSWORD")
    parser.add_argument(
        "--expected-package",
        default="com.google.android.GoogleCamera",
    )
    parser.add_argument("--expected-abi", default="arm64-v8a")
    parser.add_argument("--expected-min-sdk", type=int, default=37)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        report = build_standalone(
            args.input,
            args.output,
            apkeditor=args.apkeditor,
            baksmali=args.baksmali,
            smali=args.smali,
            java=resolve_tool(args.java, "java"),
            zipalign=resolve_tool(args.zipalign, "zipalign"),
            apksigner=resolve_tool(args.apksigner, "apksigner"),
            aapt2=resolve_tool(args.aapt2, "aapt2"),
            keystore=args.keystore,
            key_alias=args.key_alias,
            ks_pass_env=args.ks_pass_env,
            key_pass_env=args.key_pass_env,
            expected_package=args.expected_package,
            expected_abi=args.expected_abi,
            expected_min_sdk=args.expected_min_sdk,
            report_path=args.report,
        )
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(f"Built: {report['output']['path']}")
            print(f"SHA-256: {report['output']['sha256']}")
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
