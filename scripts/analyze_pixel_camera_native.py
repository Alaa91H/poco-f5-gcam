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
import struct
import sys
import zipfile
from pathlib import Path
from typing import Any

try:
    from scripts.patch_pixel_camera_device_gate import (
        GCASTARTUP_LIBRARY,
        PatchError,
        _elf64_load_segments,
        _instruction_window,
        _verify_uncalibrated_tuning_branch,
        analyze_libgcam_tuning,
    )
except ModuleNotFoundError:
    from patch_pixel_camera_device_gate import (
        GCASTARTUP_LIBRARY,
        PatchError,
        _elf64_load_segments,
        _instruction_window,
        _verify_uncalibrated_tuning_branch,
        analyze_libgcam_tuning,
    )


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()



def _file_offset_to_vaddr(
    offset: int,
    segments: list[dict[str, int]],
) -> int | None:
    for segment in segments:
        start = segment["offset"]
        end = start + segment["filesz"]
        if start <= offset < end:
            return segment["vaddr"] + (offset - start)
    return None


def _vaddr_to_file_offset(
    vaddr: int,
    segments: list[dict[str, int]],
) -> int | None:
    for segment in segments:
        start = segment["vaddr"]
        end = start + segment["filesz"]
        if start <= vaddr < end:
            return segment["offset"] + (vaddr - start)
    return None


def _c_string_context(
    data: bytes,
    offset: int,
    radius: int = 384,
) -> tuple[int, str]:
    lower = max(0, offset - radius)
    upper = min(len(data), offset + radius)
    start = data.rfind(b"\x00", lower, offset)
    start = lower if start < 0 else start + 1
    end = data.find(b"\x00", offset, upper)
    end = upper if end < 0 else end
    return start, data[start:end].decode("utf-8", errors="replace")


def _raw_pointer_slots(
    data: bytes,
    target_vaddr: int,
    segments: list[dict[str, int]],
) -> list[dict[str, Any]]:
    needle = struct.pack("<Q", target_vaddr)
    slots: list[dict[str, Any]] = []
    cursor = 0
    while True:
        found = data.find(needle, cursor)
        if found < 0:
            break
        slot_vaddr = _file_offset_to_vaddr(found, segments)
        if slot_vaddr is not None:
            slots.append(
                {
                    "kind": "raw_u64",
                    "file_offset": f"0x{found:x}",
                    "vaddr": f"0x{slot_vaddr:x}",
                }
            )
        cursor = found + 1
    return slots


def _rela_slots(
    data: bytes,
    target_vaddr: int,
    segments: list[dict[str, int]],
) -> list[dict[str, Any]]:
    """Find ELF64 SHT_RELA entries whose addend points at a tuning string."""

    if len(data) < 64:
        return []

    e_shoff = struct.unpack_from("<Q", data, 40)[0]
    e_shentsize = struct.unpack_from("<H", data, 58)[0]
    e_shnum = struct.unpack_from("<H", data, 60)[0]
    if not e_shoff or not e_shnum or e_shentsize < 64:
        return []

    slots: list[dict[str, Any]] = []
    for index in range(e_shnum):
        shoff = e_shoff + index * e_shentsize
        if shoff + 64 > len(data):
            break
        (
            _sh_name,
            sh_type,
            _sh_flags,
            _sh_addr,
            sh_offset,
            sh_size,
            _sh_link,
            _sh_info,
            _sh_addralign,
            sh_entsize,
        ) = struct.unpack_from("<IIQQQQIIQQ", data, shoff)
        if sh_type != 4:  # SHT_RELA
            continue
        entry_size = sh_entsize or 24
        if entry_size < 24 or sh_offset + sh_size > len(data):
            continue
        for rel_off in range(sh_offset, sh_offset + sh_size, entry_size):
            if rel_off + 24 > len(data):
                break
            r_offset, r_info, r_addend = struct.unpack_from("<QQq", data, rel_off)
            if r_addend != target_vaddr:
                continue
            slot_file_offset = _vaddr_to_file_offset(r_offset, segments)
            slots.append(
                {
                    "kind": "elf_rela",
                    "file_offset": (
                        f"0x{slot_file_offset:x}"
                        if slot_file_offset is not None
                        else None
                    ),
                    "vaddr": f"0x{r_offset:x}",
                    "relocation_type": r_info & 0xFFFFFFFF,
                    "symbol_index": r_info >> 32,
                    "rela_file_offset": f"0x{rel_off:x}",
                }
            )
    return slots


def _aarch64_refs_to_targets(
    data: bytes,
    targets: set[int],
    segments: list[dict[str, int]],
    *,
    lookahead: int = 20,
) -> dict[int, list[int]]:
    """Resolve direct and ADRP-based references to a small set of addresses."""

    refs: dict[int, set[int]] = {target: set() for target in targets}
    if not targets:
        return {}

    target_pages = {target & ~0xFFF for target in targets}

    def sign_extend(value: int, bits: int) -> int:
        sign = 1 << (bits - 1)
        return (value ^ sign) - sign

    for segment in segments:
        if not (segment["flags"] & 0x1):
            continue
        start = segment["offset"]
        end = start + segment["filesz"]
        start += (-start) % 4

        for off in range(start, max(start, end - 3), 4):
            word = struct.unpack_from("<I", data, off)[0]
            pc = segment["vaddr"] + (off - segment["offset"])

            # ADR Xd, label.
            if (word & 0x9F000000) == 0x10000000:
                immlo = (word >> 29) & 0x3
                immhi = (word >> 5) & 0x7FFFF
                address = pc + sign_extend((immhi << 2) | immlo, 21)
                if address in refs:
                    refs[address].add(off)
                continue

            # LDR literal; useful when the target is a pointer slot.
            if (word & 0x3B000000) == 0x18000000:
                imm19 = sign_extend((word >> 5) & 0x7FFFF, 19) << 2
                address = pc + imm19
                if address in refs:
                    refs[address].add(off)
                continue

            # ADRP Xd, page(label).
            if (word & 0x9F000000) != 0x90000000:
                continue
            rd = word & 0x1F
            immlo = (word >> 29) & 0x3
            immhi = (word >> 5) & 0x7FFFF
            page_delta = sign_extend((immhi << 2) | immlo, 21) << 12
            page = (pc & ~0xFFF) + page_delta
            if page not in target_pages:
                continue

            for step in range(1, lookahead + 1):
                use_off = off + step * 4
                if use_off + 4 > end:
                    break
                use = struct.unpack_from("<I", data, use_off)[0]

                # ADD (immediate): address materialization.
                if (use & 0x7F000000) == 0x11000000:
                    rn = (use >> 5) & 0x1F
                    if rn == rd:
                        imm12 = (use >> 10) & 0xFFF
                        shift = (use >> 22) & 0x1
                        address = page + (imm12 << (12 if shift else 0))
                        if address in refs:
                            refs[address].update((off, use_off))

                # Load/store register (unsigned immediate), including LDR Xn.
                if (use & 0x3B000000) == 0x39000000:
                    rn = (use >> 5) & 0x1F
                    if rn == rd:
                        imm12 = (use >> 10) & 0xFFF
                        size = (use >> 30) & 0x3
                        address = page + (imm12 << size)
                        if address in refs:
                            refs[address].update((off, use_off))

    return {target: sorted(offsets) for target, offsets in refs.items()}


def _enrich_tuning_analysis(
    data: bytes,
    tuning: dict[str, Any],
) -> dict[str, Any]:
    """Add indirect ELF/data references that the direct string scanner misses."""

    segments = _elf64_load_segments(data)
    occurrence_targets: dict[int, dict[str, Any]] = {}
    occurrence_extra_targets: dict[int, set[int]] = {}
    slot_targets: set[int] = set()

    for entry in tuning["strings"].values():
        for occurrence in entry.get("occurrences") or []:
            target_vaddr = int(occurrence["vaddr"], 16)
            occurrence_targets[target_vaddr] = occurrence
            file_offset = int(occurrence["file_offset"], 16)
            c_string_start, c_string_context = _c_string_context(
                data,
                file_offset,
            )
            occurrence["c_string_context"] = c_string_context
            occurrence["c_string_start_file_offset"] = f"0x{c_string_start:x}"
            c_string_start_vaddr = _file_offset_to_vaddr(
                c_string_start,
                segments,
            )
            extra_targets: set[int] = set()
            if c_string_start_vaddr is not None:
                occurrence["c_string_start_vaddr"] = (
                    f"0x{c_string_start_vaddr:x}"
                )
                extra_targets.add(c_string_start_vaddr)
            occurrence_extra_targets[target_vaddr] = extra_targets

            slot_source_targets = {target_vaddr} | extra_targets
            slots: list[dict[str, Any]] = []
            for slot_source in slot_source_targets:
                slots.extend(_raw_pointer_slots(data, slot_source, segments))
                slots.extend(_rela_slots(data, slot_source, segments))

            unique: dict[int, dict[str, Any]] = {}
            for slot in slots:
                slot_vaddr = int(slot["vaddr"], 16)
                unique.setdefault(slot_vaddr, slot)
                slot_targets.add(slot_vaddr)
            occurrence["pointer_slots"] = list(unique.values())

    direct_targets = set(occurrence_targets)
    for targets in occurrence_extra_targets.values():
        direct_targets.update(targets)
    all_targets = direct_targets | slot_targets
    refs = _aarch64_refs_to_targets(data, all_targets, segments)

    for target_vaddr, occurrence in occurrence_targets.items():
        resolved = set(occurrence.get("xref_offsets") or [])
        direct_source_targets = {target_vaddr} | occurrence_extra_targets.get(
            target_vaddr,
            set(),
        )
        direct_target_refs: list[dict[str, Any]] = []
        for direct_target in sorted(direct_source_targets):
            offsets = refs.get(direct_target, [])
            if offsets:
                direct_target_refs.append(
                    {
                        "target_vaddr": f"0x{direct_target:x}",
                        "xref_offsets": [f"0x{off:x}" for off in offsets],
                    }
                )
            resolved.update(f"0x{off:x}" for off in offsets)
        occurrence["resolved_direct_target_xrefs"] = direct_target_refs

        slot_refs: list[dict[str, Any]] = []
        for slot in occurrence.get("pointer_slots") or []:
            slot_vaddr = int(slot["vaddr"], 16)
            offsets = refs.get(slot_vaddr, [])
            if not offsets:
                continue
            rendered = [f"0x{off:x}" for off in offsets]
            resolved.update(rendered)
            slot_refs.append(
                {
                    "slot_vaddr": slot["vaddr"],
                    "xref_offsets": rendered,
                }
            )

        occurrence["indirect_slot_xrefs"] = slot_refs
        occurrence["resolved_xref_offsets"] = sorted(
            resolved,
            key=lambda item: int(item, 16),
        )
        occurrence["resolved_xref_windows"] = [
            {
                "xref_offset": item,
                "instructions": _instruction_window(
                    data,
                    int(item, 16),
                    segments,
                    radius=0x60,
                ),
            }
            for item in occurrence["resolved_xref_offsets"][:12]
        ]

    tuning["indirect_reference_analysis"] = True
    return tuning


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
    tuning = _enrich_tuning_analysis(
        library,
        analyze_libgcam_tuning(library),
    )
    return {
        "schema_version": 2,
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
        "tuning": tuning,
        "uncalibrated_fallback_patch_verification": (
            _verify_uncalibrated_tuning_branch(library)
        ),
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
