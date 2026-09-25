#!/usr/bin/env python3
"""Locate Pixel Camera's unsupported-device guard in the original base APK.

This tool is analysis-only: it never modifies APK bytes. It extracts base.apk
from an APK/APKM/APKS/XAPK package, decodes dex bytecode to smali with Apktool,
finds the exact unsupported-device message, and records the containing method
so a narrow compatibility patch can be reviewed before application.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

DEVICE_GATE_TEXT = "Device is not recognized or not supported"


class AnalysisError(RuntimeError):
    pass


def run(command: list[str]) -> str:
    proc = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        raise AnalysisError(
            f"command failed ({proc.returncode}): {' '.join(command)}\n{proc.stdout}"
        )
    return proc.stdout


def extract_base_apk(package_path: Path, work_dir: Path) -> Path:
    suffix = package_path.suffix.lower()
    if suffix == ".apk":
        target = work_dir / "base.apk"
        shutil.copy2(package_path, target)
        return target

    if suffix not in {".apkm", ".apks", ".xapk", ".zip"}:
        raise AnalysisError(f"unsupported package type: {suffix}")

    with zipfile.ZipFile(package_path) as archive:
        apk_entries = [name for name in archive.namelist() if name.lower().endswith(".apk")]
        if not apk_entries:
            raise AnalysisError("bundle contains no APK entries")

        exact = [name for name in apk_entries if Path(name).name.lower() == "base.apk"]
        if len(exact) != 1:
            raise AnalysisError(
                f"expected exactly one base.apk, found {len(exact)}; entries={apk_entries}"
            )

        target = work_dir / "base.apk"
        with archive.open(exact[0]) as src, target.open("wb") as dst:
            shutil.copyfileobj(src, dst)
        return target


def method_bounds(lines: list[str], hit_index: int) -> tuple[int, int]:
    start = hit_index
    while start >= 0 and not lines[start].lstrip().startswith(".method"):
        start -= 1
    if start < 0:
        raise AnalysisError("marker was found outside a smali method")

    end = hit_index
    while end < len(lines) and not lines[end].lstrip().startswith(".end method"):
        end += 1
    if end >= len(lines):
        raise AnalysisError("smali method containing marker is not terminated")
    return start, end


def analyze_decoded(decoded_dir: Path) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []

    for path in sorted(decoded_dir.rglob("*.smali")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if DEVICE_GATE_TEXT not in text:
            continue

        lines = text.splitlines()
        for index, line in enumerate(lines):
            if DEVICE_GATE_TEXT not in line:
                continue
            start, end = method_bounds(lines, index)
            method_lines = lines[start : end + 1]
            matches.append(
                {
                    "file": str(path.relative_to(decoded_dir)).replace("\\", "/"),
                    "marker_line": index + 1,
                    "method_start_line": start + 1,
                    "method_end_line": end + 1,
                    "method_header": lines[start].strip(),
                    "method_smali": "\n".join(method_lines),
                }
            )

    if not matches:
        raise AnalysisError(
            f"did not find exact device-gate text: {DEVICE_GATE_TEXT!r}"
        )

    return {
        "schema_version": 1,
        "mode": "analysis-only",
        "device_gate_text": DEVICE_GATE_TEXT,
        "match_count": len(matches),
        "matches": matches,
    }


def analyze_package(package_path: Path, apktool_jar: Path, output_path: Path) -> dict[str, Any]:
    if not package_path.is_file():
        raise AnalysisError(f"package does not exist: {package_path}")
    if not apktool_jar.is_file():
        raise AnalysisError(f"apktool jar does not exist: {apktool_jar}")

    with tempfile.TemporaryDirectory(prefix="pixel-camera-device-gate-") as temp:
        work = Path(temp)
        base_apk = extract_base_apk(package_path, work)
        decoded = work / "decoded"

        run(
            [
                "java",
                "-Xmx6g",
                "-jar",
                str(apktool_jar),
                "d",
                str(base_apk),
                "--no-res",
                "--force",
                "--output",
                str(decoded),
            ]
        )

        report = analyze_decoded(decoded)
        report["input"] = {
            "path": str(package_path),
            "base_apk_name": base_apk.name,
        }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    parser.add_argument("--apktool", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        report = analyze_package(args.package, args.apktool, args.output)
        if args.json:
            print(json.dumps(report, ensure_ascii=False))
        else:
            print(f"Found {report['match_count']} device-gate match(es).")
            for match in report["matches"]:
                print(
                    f"{match['file']}:{match['marker_line']} "
                    f"{match['method_header']}"
                )
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
