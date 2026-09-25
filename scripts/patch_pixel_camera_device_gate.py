#!/usr/bin/env python3
"""Patch Pixel Camera's unsupported-device constructor gate in a merged APK.

The patch is intentionally narrow and fail-closed. It looks for the exact
"Device is not recognized or not supported" guard in one classes*.dex file,
disassembles only that dex, and redirects the guard's throw block to the
constructor's existing common finalization path. No feature flags or PairIP
checks are modified.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

MARKER = "Device is not recognized or not supported"
MARKER_BYTES = MARKER.encode("utf-8")
DEX_NAME_RE = re.compile(r"^classes(?:\d+)?\.dex$")
LABEL_RE = re.compile(r"^\s*:(?P<label>[A-Za-z0-9_.$-]+)\s*$")
BRANCH_RE_TEMPLATE = r"^\s*if-[^\s]+\s+.+,\s*:(?P<label>{label})\s*$"


class PatchError(RuntimeError):
    pass


def run(command: list[str]) -> str:
    completed = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        rendered = " ".join(command[:5])
        raise PatchError(
            f"command failed ({completed.returncode}): {rendered}\n"
            f"{completed.stdout[-6000:]}"
        )
    return completed.stdout


def find_target_dex(apk: Path) -> str:
    with zipfile.ZipFile(apk, "r") as archive:
        matches: list[tuple[str, int]] = []
        for info in archive.infolist():
            if not DEX_NAME_RE.fullmatch(info.filename):
                continue
            data = archive.read(info)
            count = data.count(MARKER_BYTES)
            if count:
                matches.append((info.filename, count))

    if len(matches) != 1:
        rendered = ", ".join(f"{name}:{count}" for name, count in matches) or "none"
        raise PatchError(
            "expected the unsupported-device marker in exactly one classes*.dex; "
            f"found {rendered}"
        )
    if matches[0][1] != 1:
        raise PatchError(
            f"expected one marker occurrence in {matches[0][0]}; found {matches[0][1]}"
        )
    return matches[0][0]


def _method_bounds(lines: list[str], index: int) -> tuple[int, int]:
    start = index
    while start >= 0 and not lines[start].lstrip().startswith(".method "):
        start -= 1
    end = index
    while end < len(lines) and lines[end].strip() != ".end method":
        end += 1
    if start < 0 or end >= len(lines):
        raise PatchError("marker is not inside a complete smali method")
    return start, end


def patch_smali_text(text: str) -> tuple[str, dict[str, str]]:
    lines = text.splitlines()
    marker_indexes = [i for i, line in enumerate(lines) if MARKER in line]
    if len(marker_indexes) != 1:
        raise PatchError(f"expected one marker in smali file; found {len(marker_indexes)}")

    marker_index = marker_indexes[0]
    method_start, method_end = _method_bounds(lines, marker_index)
    method_header = lines[method_start].strip()
    if " constructor <init>(" not in method_header:
        raise PatchError("unsupported-device marker is not inside a constructor")

    unsupported_index = marker_index
    unsupported_label = None
    while unsupported_index > method_start:
        match = LABEL_RE.match(lines[unsupported_index])
        if match:
            unsupported_label = match.group("label")
            break
        unsupported_index -= 1
    if unsupported_label is None:
        raise PatchError("could not locate unsupported-device block label")

    throw_index = marker_index
    while throw_index < method_end and not lines[throw_index].lstrip().startswith("throw "):
        if throw_index > marker_index and LABEL_RE.match(lines[throw_index]):
            raise PatchError("unsupported-device block reaches another label before throw")
        throw_index += 1
    if throw_index >= method_end:
        raise PatchError("unsupported-device block has no throw instruction")

    block = "\n".join(lines[unsupported_index : throw_index + 1])
    required_tokens = [
        "Ljava/lang/UnsupportedOperationException;",
        f'\"{MARKER}\"',
        "throw ",
    ]
    if not all(token in block for token in required_tokens):
        raise PatchError("unsupported-device block does not match the expected guard shape")

    branch_re = re.compile(BRANCH_RE_TEMPLATE.format(label=re.escape(unsupported_label)))
    branch_indexes = [
        i
        for i in range(method_start, unsupported_index)
        if branch_re.match(lines[i])
    ]
    if len(branch_indexes) != 1:
        raise PatchError(
            "expected exactly one conditional branch into the unsupported-device block; "
            f"found {len(branch_indexes)}"
        )
    branch_index = branch_indexes[0]

    success_index = None
    success_label = None
    for i in range(branch_index + 1, unsupported_index):
        match = LABEL_RE.match(lines[i])
        if match:
            success_index = i
            success_label = match.group("label")
            break
    if success_index is None or success_label is None:
        raise PatchError("could not locate the constructor common finalization label")

    between = "\n".join(lines[success_index:unsupported_index])
    if "return-void" not in between:
        raise PatchError("candidate finalization path does not return from the constructor")

    indent = re.match(r"^(\s*)", lines[throw_index]).group(1)
    replacement = [
        lines[unsupported_index],
        "",
        f"{indent}goto/32 :{success_label}",
    ]
    patched_lines = lines[:unsupported_index] + replacement + lines[throw_index + 1 :]
    patched = "\n".join(patched_lines) + ("\n" if text.endswith("\n") else "")

    if MARKER in patched:
        raise PatchError("marker remained in patched smali source")

    return patched, {
        "method": method_header,
        "unsupported_label": unsupported_label,
        "finalization_label": success_label,
    }


def find_and_patch_smali_tree(root: Path) -> dict[str, str]:
    matches: list[Path] = []
    for path in root.rglob("*.smali"):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if MARKER in text:
            matches.append(path)

    if len(matches) != 1:
        rendered = ", ".join(os.fspath(p.relative_to(root)) for p in matches) or "none"
        raise PatchError(
            "expected unsupported-device marker in exactly one smali file; "
            f"found {rendered}"
        )

    target = matches[0]
    original = target.read_text(encoding="utf-8")
    patched, metadata = patch_smali_text(original)
    target.write_text(patched, encoding="utf-8")
    metadata["smali_path"] = os.fspath(target.relative_to(root))
    return metadata


def replace_zip_member(source: Path, output: Path, member: str, replacement: Path) -> None:
    if source.resolve() == output.resolve():
        raise PatchError("source and output APK paths must be different")
    output.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(source, "r") as src, zipfile.ZipFile(output, "w") as dst:
        dst.comment = src.comment
        found = False
        for info in src.infolist():
            if info.filename == member:
                found = True
                data = replacement.read_bytes()
            else:
                data = src.read(info)
            dst.writestr(info, data, compress_type=info.compress_type)
    if not found:
        raise PatchError(f"target dex disappeared from APK: {member}")


def patch_apk(
    source: Path,
    output: Path,
    *,
    baksmali: Path,
    smali: Path,
    java: str = "java",
) -> dict[str, Any]:
    source = source.resolve()
    output = output.resolve()
    baksmali = baksmali.resolve()
    smali = smali.resolve()

    if not source.is_file() or not zipfile.is_zipfile(source):
        raise PatchError(f"source is not a valid APK/ZIP: {source}")
    if not baksmali.is_file():
        raise PatchError(f"baksmali jar not found: {baksmali}")
    if not smali.is_file():
        raise PatchError(f"smali jar not found: {smali}")

    target_dex = find_target_dex(source)

    with tempfile.TemporaryDirectory(prefix="poco-f5-device-gate-") as temp:
        root = Path(temp)
        input_dex = root / target_dex
        smali_dir = root / "smali"
        rebuilt_dex = root / "patched.dex"

        with zipfile.ZipFile(source, "r") as archive:
            input_dex.write_bytes(archive.read(target_dex))

        disassemble_output = run(
            [java, "-jar", os.fspath(baksmali), "d", os.fspath(input_dex), "-o", os.fspath(smali_dir)]
        )
        metadata = find_and_patch_smali_tree(smali_dir)
        assemble_output = run(
            [java, "-jar", os.fspath(smali), "a", os.fspath(smali_dir), "-o", os.fspath(rebuilt_dex)]
        )
        if not rebuilt_dex.is_file() or rebuilt_dex.stat().st_size == 0:
            raise PatchError("smali did not produce a rebuilt dex")
        if MARKER_BYTES in rebuilt_dex.read_bytes():
            raise PatchError("unsupported-device marker still exists in rebuilt dex")

        replace_zip_member(source, output, target_dex, rebuilt_dex)

    with zipfile.ZipFile(output, "r") as archive:
        remaining = []
        for info in archive.infolist():
            if DEX_NAME_RE.fullmatch(info.filename) and MARKER_BYTES in archive.read(info):
                remaining.append(info.filename)
    if remaining:
        raise PatchError(
            "unsupported-device marker still exists in patched dex: "
            + ", ".join(remaining)
        )

    return {
        "status": "patched",
        "marker": MARKER,
        "target_dex": target_dex,
        **metadata,
        "baksmali_output_tail": "\n".join(disassemble_output.splitlines()[-20:]),
        "smali_output_tail": "\n".join(assemble_output.splitlines()[-20:]),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--baksmali", required=True, type=Path)
    parser.add_argument("--smali", required=True, type=Path)
    parser.add_argument("--java", default="java")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    try:
        report = patch_apk(
            args.input,
            args.output,
            baksmali=args.baksmali,
            smali=args.smali,
            java=args.java,
        )
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
