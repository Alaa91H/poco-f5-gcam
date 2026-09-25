#!/usr/bin/env python3
"""Inspect APK/APKM/XAPK packages without modifying their signed contents.

The inspector is intentionally read-only. It produces a bill of materials for
base/split APKs, native ABIs, compressed/uncompressed payload sizes, optional
manifest metadata through apkanalyzer, optional signature verification through
apksigner, and PairIP presence indicators for compatibility diagnostics.

It never rewrites, resigns, strips, aligns, or otherwise mutates Google APKs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import struct
import sys
import tempfile
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

APK_SUFFIXES = {".apk"}
BUNDLE_SUFFIXES = {".apkm", ".xapk", ".zip"}
DEX_RE = re.compile(r"(^|/)classes(?:\d+)?\.dex$", re.IGNORECASE)
CERT_SHA256_RE = re.compile(
    r"Signer #\d+ certificate SHA-256 digest:\s*([0-9a-f:]{64,95})",
    re.IGNORECASE,
)
MANIFEST_ATTR_RE = re.compile(r'\b(package|split)="([^"]*)"')
USES_SPLIT_RE = re.compile(
    r'<uses-split\b[^>]*android:name="([^"]+)"',
    re.IGNORECASE,
)
CONFIG_FOR_SPLIT_RE = re.compile(r'\bandroid:configForSplit="([^"]+)"')
FEATURE_SPLIT_RE = re.compile(r'\bandroid:isFeatureSplit="([^"]+)"')


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _category(name: str) -> str:
    normalized = name.lstrip("/")
    if DEX_RE.search(normalized):
        return "dex"
    if normalized.startswith("lib/"):
        return "native"
    if normalized.startswith("assets/"):
        return "assets"
    if normalized.startswith("res/") or normalized == "resources.arsc":
        return "resources"
    if normalized.startswith("META-INF/") or normalized == "AndroidManifest.xml":
        return "metadata"
    return "other"


def _inspect_elf_load_alignment(data: bytes) -> dict[str, Any]:
    if len(data) < 64 or data[:4] != b"\x7fELF":
        return {
            "available": False,
            "load_alignments": [],
            "min_load_alignment": None,
            "compatible_16kb": None,
        }

    elf_class = data[4]
    endian_id = data[5]
    if endian_id == 1:
        endian = "<"
    elif endian_id == 2:
        endian = ">"
    else:
        return {
            "available": False,
            "error": f"unknown ELF endianness {endian_id}",
            "load_alignments": [],
            "min_load_alignment": None,
            "compatible_16kb": None,
        }

    try:
        if elf_class == 1:
            phoff = struct.unpack_from(endian + "I", data, 28)[0]
            phentsize = struct.unpack_from(endian + "H", data, 42)[0]
            phnum = struct.unpack_from(endian + "H", data, 44)[0]
            align_offset = 28
            elf_bits = 32
            align_format = "I"
        elif elf_class == 2:
            phoff = struct.unpack_from(endian + "Q", data, 32)[0]
            phentsize = struct.unpack_from(endian + "H", data, 54)[0]
            phnum = struct.unpack_from(endian + "H", data, 56)[0]
            align_offset = 48
            elf_bits = 64
            align_format = "Q"
        else:
            return {
                "available": False,
                "error": f"unknown ELF class {elf_class}",
                "load_alignments": [],
                "min_load_alignment": None,
                "compatible_16kb": None,
            }

        alignments: list[int] = []
        for index in range(phnum):
            entry = phoff + index * phentsize
            if entry + phentsize > len(data):
                raise ValueError("program header extends beyond ELF payload")
            p_type = struct.unpack_from(endian + "I", data, entry)[0]
            if p_type != 1:
                continue
            alignment = struct.unpack_from(
                endian + align_format,
                data,
                entry + align_offset,
            )[0]
            alignments.append(int(alignment))

        minimum = min(alignments) if alignments else None
        compatible = (
            all(alignment >= 16384 for alignment in alignments)
            if alignments
            else None
        )
        return {
            "available": True,
            "elf_class_bits": elf_bits,
            "load_alignments": alignments,
            "min_load_alignment": minimum,
            "compatible_16kb": compatible,
        }
    except (struct.error, ValueError) as exc:
        return {
            "available": False,
            "error": str(exc),
            "load_alignments": [],
            "min_load_alignment": None,
            "compatible_16kb": None,
        }


def verify_zip_alignment(apk_path: Path, zipalign: str | None) -> dict[str, Any]:
    if not zipalign:
        return {
            "available": False,
            "verified_16kb": None,
        }

    code, output = _run_text(
        [zipalign, "-c", "-P", "16", "-v", "4", str(apk_path)]
    )
    return {
        "available": True,
        "verified_16kb": code == 0,
        "output_tail": "\n".join(output.strip().splitlines()[-20:]),
    }


def _stream_contains_ascii(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    needle: bytes,
) -> bool:
    needle = needle.lower()
    overlap = max(len(needle) - 1, 0)
    tail = b""
    with archive.open(info, "r") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                return False
            haystack = (tail + chunk).lower()
            if needle in haystack:
                return True
            tail = haystack[-overlap:] if overlap else b""


def _pairip_indicators(
    archive: zipfile.ZipFile,
    infos: Iterable[zipfile.ZipInfo],
) -> list[str]:
    indicators: list[str] = []
    for info in infos:
        lowered = info.filename.lower()
        if "pairip" in lowered:
            indicators.append(f"path:{info.filename}")
        if DEX_RE.search(info.filename):
            try:
                if _stream_contains_ascii(archive, info, b"pairip"):
                    indicators.append(f"dex:{info.filename}")
            except (OSError, RuntimeError, zipfile.BadZipFile):
                pass
    return sorted(set(indicators))


def _run_text(command: list[str]) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        return 127, str(exc)
    return completed.returncode, completed.stdout


def inspect_manifest(apk_path: Path, apkanalyzer: str | None) -> dict[str, Any]:
    if not apkanalyzer:
        return {
            "available": False,
            "package": None,
            "split": None,
            "uses_splits": [],
        }

    code, output = _run_text([apkanalyzer, "manifest", "print", str(apk_path)])
    if code != 0:
        return {
            "available": True,
            "error": output.strip(),
            "package": None,
            "split": None,
            "uses_splits": [],
        }

    attrs = dict(MANIFEST_ATTR_RE.findall(output))
    return {
        "available": True,
        "package": attrs.get("package"),
        "split": attrs.get("split") or None,
        "uses_splits": sorted(set(USES_SPLIT_RE.findall(output))),
    }


def verify_signature(apk_path: Path, apksigner: str | None) -> dict[str, Any]:
    if not apksigner:
        return {
            "available": False,
            "verified": None,
            "signer_sha256": [],
        }

    code, output = _run_text(
        [apksigner, "verify", "--verbose", "--print-certs", str(apk_path)]
    )
    signers = []
    for match in CERT_SHA256_RE.finditer(output):
        normalized = match.group(1).replace(":", "").lower()
        if normalized not in signers:
            signers.append(normalized)

    return {
        "available": True,
        "verified": code == 0,
        "signer_sha256": signers,
        "output_tail": "\n".join(output.strip().splitlines()[-20:]),
    }


def inspect_apk(
    apk_path: Path,
    logical_name: str,
    apksigner: str | None,
    apkanalyzer: str | None,
) -> dict[str, Any]:
    if not zipfile.is_zipfile(apk_path):
        raise ValueError(f"{logical_name} is not a valid APK ZIP payload")

    categories: dict[str, dict[str, int]] = defaultdict(
        lambda: {"compressed_bytes": 0, "uncompressed_bytes": 0, "files": 0}
    )
    abis: set[str] = set()
    native_libraries: list[str] = []

    with zipfile.ZipFile(apk_path, "r") as archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        for info in infos:
            category = _category(info.filename)
            categories[category]["compressed_bytes"] += info.compress_size
            categories[category]["uncompressed_bytes"] += info.file_size
            categories[category]["files"] += 1

            parts = info.filename.split("/")
            if len(parts) >= 3 and parts[0] == "lib" and parts[-1].endswith(".so"):
                abis.add(parts[1])
                native_libraries.append(info.filename)

        pairip = _pairip_indicators(archive, infos)

        compressed_payload = sum(info.compress_size for info in infos)
        uncompressed_payload = sum(info.file_size for info in infos)

    return {
        "name": logical_name,
        "file_size_bytes": apk_path.stat().st_size,
        "sha256": sha256_path(apk_path),
        "zip_payload": {
            "compressed_bytes": compressed_payload,
            "uncompressed_bytes": uncompressed_payload,
            "file_count": sum(item["files"] for item in categories.values()),
        },
        "categories": dict(sorted(categories.items())),
        "abis": sorted(abis),
        "native_library_count": len(native_libraries),
        "pairip": {
            "detected": bool(pairip),
            "indicators": pairip,
        },
        "manifest": inspect_manifest(apk_path, apkanalyzer),
        "signature": verify_signature(apk_path, apksigner),
    }


def _extract_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    destination: Path,
) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with archive.open(info, "r") as source, destination.open("wb") as target:
        shutil.copyfileobj(source, target, length=1024 * 1024)
    return destination


def _bundle_apk_infos(path: Path) -> list[zipfile.ZipInfo]:
    with zipfile.ZipFile(path, "r") as archive:
        return [
            info
            for info in archive.infolist()
            if not info.is_dir() and Path(info.filename).suffix.lower() in APK_SUFFIXES
        ]


def _signature_summary(
    apks: list[dict[str, Any]],
    allowed_signers: set[str],
) -> dict[str, Any]:
    checked = [
        apk for apk in apks if apk.get("signature", {}).get("available") is True
    ]
    if not checked:
        return {
            "checked": False,
            "all_verified": None,
            "consistent_signer": None,
            "observed_signers": [],
            "allowed_signer_match": None if not allowed_signers else False,
        }

    all_verified = all(
        apk.get("signature", {}).get("verified") is True for apk in checked
    )
    observed: set[str] = set()
    for apk in checked:
        observed.update(apk.get("signature", {}).get("signer_sha256", []))

    consistent = all_verified and len(observed) == 1
    allowed_match: bool | None
    if allowed_signers:
        allowed_match = bool(observed) and observed.issubset(allowed_signers)
    else:
        allowed_match = None

    return {
        "checked": True,
        "all_verified": all_verified,
        "consistent_signer": consistent,
        "observed_signers": sorted(observed),
        "allowed_signer_match": allowed_match,
    }


def inspect_package(
    package_path: Path,
    apksigner: str | None = None,
    apkanalyzer: str | None = None,
    allowed_signers: set[str] | None = None,
) -> dict[str, Any]:
    package_path = package_path.resolve()
    if not package_path.is_file():
        raise FileNotFoundError(package_path)
    if not zipfile.is_zipfile(package_path):
        raise ValueError(f"{package_path} is not a ZIP-based Android package")

    allowed_signers = {item.lower().replace(":", "") for item in (allowed_signers or set())}

    suffix = package_path.suffix.lower()
    apk_results: list[dict[str, Any]] = []
    bundle_metadata: dict[str, Any] = {}

    if suffix == ".apk":
        apk_results.append(
            inspect_apk(
                package_path,
                package_path.name,
                apksigner,
                apkanalyzer,
            )
        )
        kind = "apk"
    else:
        kind = "bundle"
        with zipfile.ZipFile(package_path, "r") as bundle:
            infos = [
                info
                for info in bundle.infolist()
                if not info.is_dir()
            ]
            apk_infos = [
                info
                for info in infos
                if Path(info.filename).suffix.lower() in APK_SUFFIXES
            ]
            if not apk_infos:
                raise ValueError(
                    f"{package_path.name} contains no APK members"
                )

            bundle_metadata = {
                "member_count": len(infos),
                "apk_member_count": len(apk_infos),
                "apk_members_compressed_bytes": sum(
                    info.compress_size for info in apk_infos
                ),
                "apk_members_uncompressed_bytes": sum(
                    info.file_size for info in apk_infos
                ),
            }

            with tempfile.TemporaryDirectory(prefix="poco-f5-apkm-") as temp_dir:
                root = Path(temp_dir)
                for index, info in enumerate(
                    sorted(apk_infos, key=lambda item: item.filename)
                ):
                    safe_name = f"{index:03d}-{Path(info.filename).name}"
                    extracted = _extract_member(bundle, info, root / safe_name)
                    apk_results.append(
                        inspect_apk(
                            extracted,
                            info.filename,
                            apksigner,
                            apkanalyzer,
                        )
                    )

    aggregate_categories: dict[str, dict[str, int]] = defaultdict(
        lambda: {"compressed_bytes": 0, "uncompressed_bytes": 0, "files": 0}
    )
    for apk in apk_results:
        for category, values in apk["categories"].items():
            for key in ("compressed_bytes", "uncompressed_bytes", "files"):
                aggregate_categories[category][key] += int(values[key])

    signature = _signature_summary(apk_results, allowed_signers)
    pairip_apks = [apk["name"] for apk in apk_results if apk["pairip"]["detected"]]

    report = {
        "schema_version": 1,
        "input": {
            "path": os.fspath(package_path),
            "kind": kind,
            "file_size_bytes": package_path.stat().st_size,
            "sha256": sha256_path(package_path),
        },
        "bundle": bundle_metadata,
        "summary": {
            "apk_count": len(apk_results),
            "aggregate_categories": dict(sorted(aggregate_categories.items())),
            "top_apks_by_size": [
                {"name": item["name"], "file_size_bytes": item["file_size_bytes"]}
                for item in sorted(
                    apk_results,
                    key=lambda item: item["file_size_bytes"],
                    reverse=True,
                )[:15]
            ],
            "pairip_detected": bool(pairip_apks),
            "pairip_apks": pairip_apks,
            "signature": signature,
        },
        "apks": apk_results,
    }
    return report


def _resolve_tool(explicit: str | None, executable: str) -> str | None:
    if explicit:
        candidate = Path(explicit)
        if candidate.is_file():
            return str(candidate)
        resolved = shutil.which(explicit)
        if resolved:
            return resolved
        raise FileNotFoundError(f"{executable} not found: {explicit}")
    return shutil.which(executable)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path, help="APK/APKM/XAPK file to inspect")
    parser.add_argument("--output", type=Path, help="Write JSON report to this path")
    parser.add_argument(
        "--apksigner",
        help="Path/name of apksigner. Auto-detected from PATH when omitted.",
    )
    parser.add_argument(
        "--apkanalyzer",
        help="Path/name of apkanalyzer. Auto-detected from PATH when omitted.",
    )
    parser.add_argument(
        "--allowed-signer-sha256",
        action="append",
        default=[],
        help="Allowed APK certificate SHA-256. May be passed more than once.",
    )
    parser.add_argument(
        "--require-signature-verification",
        action="store_true",
        help="Fail when signatures cannot be verified, differ across splits, or are not allowed.",
    )
    parser.add_argument(
        "--fail-on-pairip",
        action="store_true",
        help="Fail if PairIP indicators are detected. Detection does not modify the APK.",
    )
    args = parser.parse_args()

    try:
        apksigner = _resolve_tool(args.apksigner, "apksigner")
        apkanalyzer = _resolve_tool(args.apkanalyzer, "apkanalyzer")
        allowed = {
            item.lower().replace(":", "")
            for item in args.allowed_signer_sha256
            if item.strip()
        }

        report = inspect_package(
            args.package,
            apksigner=apksigner,
            apkanalyzer=apkanalyzer,
            allowed_signers=allowed,
        )

        payload = json.dumps(report, indent=2, ensure_ascii=False)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(payload + "\n", encoding="utf-8")
        print(payload)

        if args.fail_on_pairip and report["summary"]["pairip_detected"]:
            print("error: PairIP indicators detected", file=sys.stderr)
            return 3

        if args.require_signature_verification:
            signature = report["summary"]["signature"]
            if (
                signature["checked"] is not True
                or signature["all_verified"] is not True
                or signature["consistent_signer"] is not True
                or (
                    allowed
                    and signature["allowed_signer_match"] is not True
                )
            ):
                print(
                    "error: package signature verification gate failed",
                    file=sys.stderr,
                )
                return 4

        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
