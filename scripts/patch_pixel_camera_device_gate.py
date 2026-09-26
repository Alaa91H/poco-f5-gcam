#!/usr/bin/env python3
"""Patch Pixel Camera's POCO F5 startup compatibility gates in a merged APK.

The patch is intentionally fail-closed. It locates the exact
"Device is not recognized or not supported" constructor guard in one
classes*.dex file, redirects that rejection block to the existing common
finalization path, and then hard-disables Tensor-only GXP/TPU/DarwiNN feature
queries in the klm class. Real-device runtime evidence is then used to keep
the native GCam InitParams provider from enabling almond TPU and Tomte grain
during startup. Because the real Snapdragon runtime still enters the native
DarwiNN/GXP delegate path, the exact same-version libgcastartup instructions
that select that delegate are changed to the library's existing CPU/TFLite
fallback. Model verification, PairIP, and unrelated feature gates are untouched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

MARKER = "Device is not recognized or not supported"
MARKER_BYTES = MARKER.encode("utf-8")
GCAM_CREATE_SYMBOL = "Gcam_Create"
ALMOND_TPU_SYMBOL = "InitParams_almond_use_tpu_set"
TOMTE_GRAIN_SYMBOL = "InitParams_finish_tomte_grain_enabled_set"
GCAM_INIT_SYMBOLS = (
    GCAM_CREATE_SYMBOL.encode("utf-8"),
    ALMOND_TPU_SYMBOL.encode("utf-8"),
    TOMTE_GRAIN_SYMBOL.encode("utf-8"),
)
GCASTARTUP_LIBRARY = "lib/arm64-v8a/libgcastartup.so"
GCASTARTUP_11_0_073_SHA256 = (
    "34487551ea95b83b76ff41a742c83a6fb27f19ae07d3210139215313e3cacdbe"
)
TUNING_DIAGNOSTIC_STRINGS = (
    "Unknown device code",
    "Failed to get tuning for device code",
    "Device has not been calibrated for HDR+",
    "Unsupported sensor ID. Using tuning defaults.",
    "Using tuning defaults.",
)
# Exact AArch64 instructions for Pixel Camera 11.0.073.972752740.32.
# These patches select existing fallback behavior only. They do not spoof a
# Pixel model, bypass model verification, or synthesize camera tuning data.
GXP_CPU_PATCHES: tuple[tuple[str, int, bytes, bytes], ...] = (
    (
        "force_cpu_tflite_interpreter",
        0x352B5D0,
        bytes.fromhex("41060054"),
        bytes.fromhex("1f2003d5"),
    ),
    (
        "force_non_tpu_delegate_type",
        0x31DB540,
        bytes.fromhex("0a05881a"),
        bytes.fromhex("ea03082a"),
    ),
    (
        "bypass_darwinn_assertion_branch",
        0x352B6A0,
        bytes.fromhex("41180054"),
        bytes.fromhex("1f2003d5"),
    ),
)

# libgcam stores an allow-uncalibrated byte at [x22 + 0x2c8]. For an unknown
# device code, Pixel Camera 11.0.073 loads that byte into w9 and executes:
#
#   0x692453c  ldrb w9, [x22, #0x2c8]
#   0x6924548  cbz  w9, 0x6924c20
#
# The branch target is the existing "Unknown device code ... Aborting" block.
# Falling through enters the existing "Treating as uncalibrated" path. NOPing
# only this conditional therefore reuses Google's own uncalibrated fallback
# without inventing tuning data or impersonating another device.
TUNING_FALLBACK_PATCHES: tuple[tuple[str, int, bytes, bytes], ...] = (
    (
        "allow_unknown_device_uncalibrated_fallback",
        0x6924548,
        bytes.fromhex("c9360034"),
        bytes.fromhex("1f2003d5"),
    ),
)

GCASTARTUP_NATIVE_PATCHES = GXP_CPU_PATCHES + TUNING_FALLBACK_PATCHES
DEX_NAME_RE = re.compile(r"^classes(?:\d+)?\.dex$")
LABEL_RE = re.compile(r"^\s*:(?P<label>[A-Za-z0-9_.$-]+)\s*$")
BRANCH_RE_TEMPLATE = r"^\s*if-[^\s]+\s+.+,\s*:(?P<label>{label})\s*$"


class PatchError(RuntimeError):
    pass


def _sign_extend(value: int, bits: int) -> int:
    sign = 1 << (bits - 1)
    return (value ^ sign) - sign


def _elf64_load_segments(data: bytes) -> list[dict[str, int]]:
    """Return ELF64 PT_LOAD segments needed for file-offset/VA translation."""

    if len(data) < 64 or data[:4] != b"\x7fELF":
        raise PatchError("libgcastartup is not an ELF file")
    if data[4] != 2 or data[5] != 1:
        raise PatchError("libgcastartup must be little-endian ELF64")

    e_phoff = struct.unpack_from("<Q", data, 32)[0]
    e_phentsize = struct.unpack_from("<H", data, 54)[0]
    e_phnum = struct.unpack_from("<H", data, 56)[0]
    if e_phentsize < 56:
        raise PatchError(f"unexpected ELF64 program-header size: {e_phentsize}")

    segments: list[dict[str, int]] = []
    for index in range(e_phnum):
        off = e_phoff + index * e_phentsize
        if off + 56 > len(data):
            raise PatchError("ELF64 program-header table is truncated")
        (
            p_type,
            p_flags,
            p_offset,
            p_vaddr,
            _p_paddr,
            p_filesz,
            p_memsz,
            _p_align,
        ) = struct.unpack_from("<IIQQQQQQ", data, off)
        if p_type != 1:
            continue
        if p_offset + p_filesz > len(data):
            raise PatchError("ELF64 PT_LOAD segment exceeds file size")
        segments.append(
            {
                "flags": p_flags,
                "offset": p_offset,
                "vaddr": p_vaddr,
                "filesz": p_filesz,
                "memsz": p_memsz,
            }
        )

    if not segments:
        raise PatchError("ELF64 contains no PT_LOAD segments")
    return segments


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


def _instruction_vaddr(
    offset: int,
    segments: list[dict[str, int]],
) -> int | None:
    return _file_offset_to_vaddr(offset, segments)


def _decode_aarch64_branch(word: int, pc: int) -> str | None:
    if word == 0xD503201F:
        return "nop"
    if (word & 0xFFFFFC1F) == 0xD65F0000:
        return "ret"

    if (word & 0x7C000000) == 0x14000000:
        imm26 = _sign_extend(word & 0x03FFFFFF, 26) << 2
        mnemonic = "bl" if (word & 0x80000000) else "b"
        return f"{mnemonic} 0x{pc + imm26:x}"

    if (word & 0xFF000010) == 0x54000000:
        imm19 = _sign_extend((word >> 5) & 0x7FFFF, 19) << 2
        cond = word & 0xF
        return f"b.cond[{cond}] 0x{pc + imm19:x}"

    if (word & 0x7E000000) == 0x34000000:
        imm19 = _sign_extend((word >> 5) & 0x7FFFF, 19) << 2
        mnemonic = "cbnz" if ((word >> 24) & 1) else "cbz"
        return f"{mnemonic} 0x{pc + imm19:x}"

    if (word & 0x7E000000) == 0x36000000:
        imm14 = _sign_extend((word >> 5) & 0x3FFF, 14) << 2
        mnemonic = "tbnz" if ((word >> 24) & 1) else "tbz"
        return f"{mnemonic} 0x{pc + imm14:x}"

    return None


def _aarch64_string_xrefs(
    data: bytes,
    target_vaddr: int,
    segments: list[dict[str, int]],
) -> list[int]:
    """Find common ADR/ADRP+ADD references to one in-binary string VA."""

    refs: set[int] = set()
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
                imm = _sign_extend((immhi << 2) | immlo, 21)
                if pc + imm == target_vaddr:
                    refs.add(off)
                continue

            # ADRP Xd, page(label), followed shortly by ADD Xd/Xn,#lo12.
            if (word & 0x9F000000) != 0x90000000:
                continue
            rd = word & 0x1F
            immlo = (word >> 29) & 0x3
            immhi = (word >> 5) & 0x7FFFF
            page_delta = _sign_extend((immhi << 2) | immlo, 21) << 12
            page = (pc & ~0xFFF) + page_delta

            for step in range(1, 6):
                add_off = off + step * 4
                if add_off + 4 > end:
                    break
                add = struct.unpack_from("<I", data, add_off)[0]
                if (add & 0xFF000000) != 0x91000000:
                    continue
                rn = (add >> 5) & 0x1F
                if rn != rd:
                    continue
                imm12 = (add >> 10) & 0xFFF
                shift = (add >> 22) & 0x1
                address = page + (imm12 << (12 if shift else 0))
                if address == target_vaddr:
                    refs.add(off)
                    refs.add(add_off)
                    break

    return sorted(refs)


def _instruction_window(
    data: bytes,
    center_offset: int,
    segments: list[dict[str, int]],
    *,
    radius: int = 0x80,
) -> list[dict[str, str]]:
    start = max(0, center_offset - radius)
    end = min(len(data), center_offset + radius + 4)
    start += (-start) % 4
    rows: list[dict[str, str]] = []
    for off in range(start, end - 3, 4):
        pc = _instruction_vaddr(off, segments)
        if pc is None:
            continue
        word = struct.unpack_from("<I", data, off)[0]
        decoded = _decode_aarch64_branch(word, pc)
        rows.append(
            {
                "file_offset": f"0x{off:x}",
                "vaddr": f"0x{pc:x}",
                "word_le_hex": data[off : off + 4].hex(),
                "control_flow": decoded or "",
            }
        )
    return rows


def analyze_libgcam_tuning(data: bytes) -> dict[str, Any]:
    """Collect fail-safe static evidence for the current libgcam tuning failure.

    The POCO F5 report proves Gcam_Create aborts because libgcam rejects the
    Xiaomi/marble device-code tuning key. This function does not modify tuning.
    It records target strings and AArch64 references so a later patch can be
    based on the actual same-version control flow rather than guessed offsets.
    """

    segments = _elf64_load_segments(data)
    strings: dict[str, Any] = {}

    for marker in TUNING_DIAGNOSTIC_STRINGS:
        needle = marker.encode("utf-8")
        offsets: list[int] = []
        cursor = 0
        while True:
            found = data.find(needle, cursor)
            if found < 0:
                break
            offsets.append(found)
            cursor = found + 1

        occurrences: list[dict[str, Any]] = []
        for offset in offsets:
            vaddr = _file_offset_to_vaddr(offset, segments)
            xrefs = (
                _aarch64_string_xrefs(data, vaddr, segments)
                if vaddr is not None
                else []
            )
            occurrences.append(
                {
                    "file_offset": f"0x{offset:x}",
                    "vaddr": f"0x{vaddr:x}" if vaddr is not None else None,
                    "xref_offsets": [f"0x{item:x}" for item in xrefs],
                    "xref_windows": [
                        {
                            "xref_offset": f"0x{xref:x}",
                            "instructions": _instruction_window(
                                data,
                                xref,
                                segments,
                            ),
                        }
                        for xref in xrefs[:8]
                    ],
                }
            )

        strings[marker] = {
            "count": len(offsets),
            "occurrences": occurrences,
        }

    return {
        "status": "diagnostic_only",
        "library_size": len(data),
        "strings": strings,
        "tuning_bypass_performed": False,
        "profile_spoof_performed": False,
    }


def patch_native_bytes(
    data: bytes,
    *,
    patches: tuple[tuple[str, int, bytes, bytes], ...] = GCASTARTUP_NATIVE_PATCHES,
) -> tuple[bytes, list[dict[str, Any]]]:
    """Apply exact-offset native patches with strict byte verification."""

    mutable = bytearray(data)
    report: list[dict[str, Any]] = []

    for name, offset, expected, replacement in patches:
        end = offset + len(expected)
        if len(expected) != len(replacement):
            raise PatchError(f"{name}: expected/replacement length mismatch")
        if end > len(mutable):
            raise PatchError(
                f"{name}: offset 0x{offset:x} exceeds native library size "
                f"{len(mutable)}"
            )

        actual = bytes(mutable[offset:end])
        if actual == replacement:
            status = "already_patched"
        elif actual == expected:
            mutable[offset:end] = replacement
            status = "patched"
        else:
            raise PatchError(
                f"{name}: native bytes at 0x{offset:x} changed; "
                f"expected {expected.hex()}, found {actual.hex()}"
            )

        report.append(
            {
                "name": name,
                "offset": f"0x{offset:x}",
                "expected_hex": expected.hex(),
                "replacement_hex": replacement.hex(),
                "status": status,
            }
        )

    return bytes(mutable), report


def _verify_uncalibrated_tuning_branch(data: bytes) -> dict[str, Any]:
    """Verify the exact unknown-device branch before enabling fallback."""

    digest = hashlib.sha256(data).hexdigest()
    if digest != GCASTARTUP_11_0_073_SHA256:
        raise PatchError(
            "libgcastartup SHA-256 changed; refusing version-specific tuning "
            f"fallback patch: expected {GCASTARTUP_11_0_073_SHA256}, found {digest}"
        )

    offset = TUNING_FALLBACK_PATCHES[0][1]
    expected = TUNING_FALLBACK_PATCHES[0][2]
    actual = data[offset : offset + len(expected)]
    if actual != expected:
        raise PatchError(
            "unknown-device uncalibrated branch bytes changed at "
            f"0x{offset:x}: expected {expected.hex()}, found {actual.hex()}"
        )

    word = struct.unpack_from("<I", data, offset)[0]
    decoded = _decode_aarch64_branch(word, offset)
    if decoded != "cbz 0x6924c20":
        raise PatchError(
            "unexpected unknown-device tuning branch semantics at "
            f"0x{offset:x}: {decoded!r}"
        )

    load_offset = 0x692453C
    expected_load = bytes.fromhex("c9224b39")
    actual_load = data[load_offset : load_offset + 4]
    if actual_load != expected_load:
        raise PatchError(
            "allow-uncalibrated flag load changed at "
            f"0x{load_offset:x}: expected {expected_load.hex()}, "
            f"found {actual_load.hex()}"
        )

    return {
        "library_sha256": digest,
        "flag_load_offset": f"0x{load_offset:x}",
        "flag_load_hex": actual_load.hex(),
        "branch_offset": f"0x{offset:x}",
        "branch_hex": actual.hex(),
        "branch": decoded,
        "fallback_target": "existing uncalibrated tuning path",
    }


def patch_gcastartup_cpu_fallback(
    archive: zipfile.ZipFile,
    root: Path,
) -> tuple[str, Path, dict[str, Any]]:
    """Patch verified accelerator and unknown-device fallback branches."""

    names = [name for name in archive.namelist() if name == GCASTARTUP_LIBRARY]
    if len(names) != 1:
        raise PatchError(
            f"expected exactly one {GCASTARTUP_LIBRARY}; found {len(names)}"
        )

    original = archive.read(GCASTARTUP_LIBRARY)
    tuning_diagnostics = analyze_libgcam_tuning(original)
    tuning_fallback_verification = _verify_uncalibrated_tuning_branch(original)
    patched, instruction_report = patch_native_bytes(original)
    output = root / "libgcastartup-cpu-fallback.so"
    output.write_bytes(patched)

    return GCASTARTUP_LIBRARY, output, {
        "status": "patched",
        "library": GCASTARTUP_LIBRARY,
        "original_size": len(original),
        "patched_size": len(patched),
        "instructions": instruction_report,
        "tuning_diagnostics": tuning_diagnostics,
        "tuning_fallback_verification": tuning_fallback_verification,
        "model_verification_bypass_performed": False,
        "tuning_bypass_performed": False,
        "tuning_profile_spoof_performed": False,
        "uncalibrated_tuning_fallback_enabled": True,
    }


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


def find_gcam_init_dex(apk: Path) -> str:
    """Locate the dex that owns Pixel Camera's native GCam InitParams builder."""

    with zipfile.ZipFile(apk, "r") as archive:
        matches: list[str] = []
        for info in archive.infolist():
            if not DEX_NAME_RE.fullmatch(info.filename):
                continue
            data = archive.read(info)
            if all(symbol in data for symbol in GCAM_INIT_SYMBOLS):
                matches.append(info.filename)

    if len(matches) != 1:
        rendered = ", ".join(matches) or "none"
        raise PatchError(
            "expected GCam_Create/almond TPU/Tomte grain symbols in exactly one "
            f"classes*.dex; found {rendered}"
        )
    return matches[0]


def _find_skip_branch_for_call(
    lines: list[str],
    *,
    method_start: int,
    method_end: int,
    call_index: int,
    description: str,
) -> tuple[int, str]:
    """Find the local feature-enable conditional that skips a JNI setter call."""

    candidates: list[tuple[int, str, int]] = []
    for i in range(call_index - 1, max(method_start, call_index - 18), -1):
        match = re.match(
            r"^\s*if-[^\s]+\s+.+,\s*:(?P<label>[A-Za-z0-9_.$-]+)\s*$",
            lines[i],
        )
        if not match:
            continue
        label = match.group("label")
        label_indexes = [
            j
            for j in range(call_index + 1, min(method_end + 1, call_index + 18))
            if lines[j].strip() == f":{label}"
        ]
        if len(label_indexes) == 1:
            candidates.append((i, label, label_indexes[0]))

    if len(candidates) != 1:
        rendered = ", ".join(
            f"line {index + 1} -> :{label}" for index, label, _ in candidates
        ) or "none"
        raise PatchError(
            f"expected one local skip branch for {description}; found {rendered}"
        )

    branch_index, label, label_index = candidates[0]
    between = lines[branch_index + 1 : label_index]
    if any(LABEL_RE.match(line) for line in between):
        raise PatchError(
            f"{description} skip branch crosses another basic-block label"
        )
    if not (branch_index < call_index < label_index):
        raise PatchError(f"{description} call is not inside the expected guarded block")

    return branch_index, label


def patch_gcam_init_smali_text(text: str) -> tuple[str, dict[str, Any]]:
    """Keep startup GCam initialization off Pixel Tensor-only accelerator paths.

    The real POCO F5 runtime reaches CameraService but Pixel Camera 11 then tries
    to initialize libgxp.so and Gcam_Create returns a null native object. Two
    independent InitParams inputs bypass klm.q()/x(): almond_use_tpu is fed by a
    provider boolean, and Tomte grain is fed by an Optional. Redirecting their
    existing enable branches to their existing continuation labels preserves
    the default false values without inventing a Pixel device profile.
    """

    lines = text.splitlines()
    symbol_indexes: dict[str, list[int]] = {
        symbol: [i for i, line in enumerate(lines) if symbol in line]
        for symbol in (
            GCAM_CREATE_SYMBOL,
            ALMOND_TPU_SYMBOL,
            TOMTE_GRAIN_SYMBOL,
        )
    }
    for symbol, indexes in symbol_indexes.items():
        if len(indexes) != 1:
            raise PatchError(
                f"expected exactly one {symbol} occurrence in GCam init smali; "
                f"found {len(indexes)}"
            )

    almond_index = symbol_indexes[ALMOND_TPU_SYMBOL][0]
    tomte_index = symbol_indexes[TOMTE_GRAIN_SYMBOL][0]
    gcam_create_index = symbol_indexes[GCAM_CREATE_SYMBOL][0]

    method_start, method_end = _method_bounds(lines, gcam_create_index)
    if not (
        method_start < almond_index < method_end
        and method_start < tomte_index < method_end
    ):
        raise PatchError(
            "GCam_Create, almond TPU, and Tomte grain setters are not in one method"
        )

    method_header = lines[method_start].strip()
    if " synthetic a()Ljava/lang/Object;" not in method_header:
        raise PatchError(
            "GCam InitParams symbols moved out of the expected synthetic provider method"
        )

    almond_branch, almond_label = _find_skip_branch_for_call(
        lines,
        method_start=method_start,
        method_end=method_end,
        call_index=almond_index,
        description="InitParams_almond_use_tpu_set",
    )
    tomte_branch, tomte_label = _find_skip_branch_for_call(
        lines,
        method_start=method_start,
        method_end=method_end,
        call_index=tomte_index,
        description="InitParams_finish_tomte_grain_enabled_set",
    )

    for branch_index, label in sorted(
        [(almond_branch, almond_label), (tomte_branch, tomte_label)],
        reverse=True,
    ):
        indent = re.match(r"^(\s*)", lines[branch_index]).group(1)
        lines[branch_index] = f"{indent}goto/32 :{label}"

    patched = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    return patched, {
        "status": "patched",
        "method": method_header,
        "almond_use_tpu": {
            "status": "forced_default_false",
            "continuation_label": almond_label,
        },
        "finish_tomte_grain": {
            "status": "forced_default_false",
            "continuation_label": tomte_label,
        },
    }


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


def _inject_nontensor_guard(
    text: str,
    *,
    signature: str,
    suffix: str,
) -> tuple[str, dict[str, str]]:
    """Inject a narrow early-return guard into a klm boolean feature query.

    Pixel Camera's generic configuration can still expose Tensor-only paths
    after the unsupported-device constructor rejection is bypassed. On POCO F5
    those paths attempt to load libgxp.so / DarwiNN and can leave Gcam_Create
    without a usable native object. The guard only forces false for:
      * camera.lasagna* (motion pipelines that require Google GXP/EdgeTPU)
      * any flag name containing use_tpu
      * any flag name containing darwinn
      * any flag name containing edgetpu

    Everything else falls through to the original method body.
    """

    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() == signature]
    if len(starts) != 1:
        raise PatchError(
            f"expected exactly one {signature} method in klm smali; found {len(starts)}"
        )
    start = starts[0]

    end = start + 1
    while end < len(lines) and lines[end].strip() != ".end method":
        end += 1
    if end >= len(lines):
        raise PatchError(f"unterminated method: {signature}")

    register_index = None
    register_mode = None
    register_count = None
    for i in range(start + 1, end):
        locals_match = re.match(r"^\s*\.locals\s+(\d+)\s*$", lines[i])
        registers_match = re.match(r"^\s*\.registers\s+(\d+)\s*$", lines[i])
        if locals_match:
            register_index = i
            register_mode = "locals"
            register_count = int(locals_match.group(1))
            break
        if registers_match:
            register_index = i
            register_mode = "registers"
            register_count = int(registers_match.group(1))
            break

    if register_index is None or register_mode is None or register_count is None:
        raise PatchError(
            f"{signature} does not use an expected .locals or .registers declaration"
        )

    # q/x are instance methods with one object parameter: p0 + p1 consume two
    # parameter registers. The injected guard needs v0 and v1. baksmali may
    # emit a minimal ".registers 2" method with no local registers at all.
    # Expanding the register declaration is safe here: p0/p1 are symbolic
    # parameter aliases and continue to refer to the high parameter registers.
    original_register_count = register_count
    if register_mode == "locals" and register_count < 2:
        register_count = 2
        indent = re.match(r"^(\s*)", lines[register_index]).group(1)
        lines[register_index] = f"{indent}.locals {register_count}"
    elif register_mode == "registers" and register_count < 4:
        register_count = 4
        indent = re.match(r"^(\s*)", lines[register_index]).group(1)
        lines[register_index] = f"{indent}.registers {register_count}"

    false_label = f"poco_nontensor_false_{suffix}"
    original_label = f"poco_nontensor_orig_{suffix}"
    method_text = "\n".join(lines[start : end + 1])
    if f":{false_label}" in method_text or f":{original_label}" in method_text:
        raise PatchError(f"{signature} already contains POCO non-Tensor guard labels")

    guard = [
        "",
        "    # POCO F5 / Snapdragon compatibility: skip Tensor-only accelerators.",
        f"    if-eqz p1, :{original_label}",
        "",
        "    iget-object v0, p1, Lkix;->a:Ljava/lang/String;",
        "",
        f"    if-eqz v0, :{original_label}",
        "",
        '    const-string v1, "camera.lasagna"',
        "",
        "    invoke-virtual {v0, v1}, Ljava/lang/String;->startsWith(Ljava/lang/String;)Z",
        "",
        "    move-result v1",
        "",
        f"    if-nez v1, :{false_label}",
        "",
        '    const-string v1, "use_tpu"',
        "",
        "    invoke-virtual {v0, v1}, Ljava/lang/String;->contains(Ljava/lang/CharSequence;)Z",
        "",
        "    move-result v1",
        "",
        f"    if-nez v1, :{false_label}",
        "",
        '    const-string v1, "darwinn"',
        "",
        "    invoke-virtual {v0, v1}, Ljava/lang/String;->contains(Ljava/lang/CharSequence;)Z",
        "",
        "    move-result v1",
        "",
        f"    if-nez v1, :{false_label}",
        "",
        '    const-string v1, "edgetpu"',
        "",
        "    invoke-virtual {v0, v1}, Ljava/lang/String;->contains(Ljava/lang/CharSequence;)Z",
        "",
        "    move-result v1",
        "",
        f"    if-eqz v1, :{original_label}",
        "",
        f"    :{false_label}",
        "    const/4 v0, 0x0",
        "",
        "    return v0",
        "",
        f"    :{original_label}",
    ]

    patched_lines = lines[: register_index + 1] + guard + lines[register_index + 1 :]
    patched = "\n".join(patched_lines) + ("\n" if text.endswith("\n") else "")

    return patched, {
        "method": signature,
        "false_label": false_label,
        "original_label": original_label,
        "register_declaration": f".{register_mode} {register_count}",
        "original_register_declaration": (
            f".{register_mode} {original_register_count}"
        ),
        "registers_expanded": register_count != original_register_count,
    }


def patch_nontensor_flag_queries(text: str) -> tuple[str, dict[str, Any]]:
    patched, q_meta = _inject_nontensor_guard(
        text,
        signature=".method public final q(Lkiz;)Z",
        suffix="q",
    )
    patched, x_meta = _inject_nontensor_guard(
        patched,
        signature=".method public final x(Lkiz;)Z",
        suffix="x",
    )
    return patched, {
        "status": "patched",
        "forced_false_patterns": [
            "camera.lasagna*",
            "*use_tpu*",
            "*darwinn*",
            "*edgetpu*",
        ],
        "methods": {
            "q": q_meta,
            "x": x_meta,
        },
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
    patched, nontensor_metadata = patch_nontensor_flag_queries(patched)
    target.write_text(patched, encoding="utf-8")
    metadata["smali_path"] = os.fspath(target.relative_to(root))
    metadata["nontensor_flag_guards"] = nontensor_metadata
    return metadata


def _has_gcam_init_callsites(text: str) -> bool:
    """Return true only for the smali provider that *calls* the JNI setters.

    GcamModuleJNI.smali itself contains the same symbol names in native method
    declarations. Matching raw symbol strings therefore produces a false second
    candidate. Require invoke-static callsites for every startup symbol instead.
    """

    lines = text.splitlines()
    for symbol in (
        GCAM_CREATE_SYMBOL,
        ALMOND_TPU_SYMBOL,
        TOMTE_GRAIN_SYMBOL,
    ):
        if not any(
            "invoke-static" in line
            and f"Lcom/google/googlex/gcam/GcamModuleJNI;->{symbol}" in line
            for line in lines
        ):
            return False
    return True


def find_and_patch_gcam_init_smali_tree(root: Path) -> dict[str, Any]:
    matches: list[Path] = []
    for path in root.rglob("*.smali"):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if _has_gcam_init_callsites(text):
            matches.append(path)

    if len(matches) != 1:
        rendered = ", ".join(os.fspath(p.relative_to(root)) for p in matches) or "none"
        raise PatchError(
            "expected native GCam InitParams callsites in exactly one smali file; "
            f"found {rendered}"
        )

    target = matches[0]
    original = target.read_text(encoding="utf-8")
    patched, metadata = patch_gcam_init_smali_text(original)
    target.write_text(patched, encoding="utf-8")
    metadata["smali_path"] = os.fspath(target.relative_to(root))
    return metadata


def replace_zip_members(
    source: Path,
    output: Path,
    replacements: dict[str, Path],
) -> None:
    if source.resolve() == output.resolve():
        raise PatchError("source and output APK paths must be different")
    if not replacements:
        raise PatchError("no APK members were supplied for replacement")
    output.parent.mkdir(parents=True, exist_ok=True)

    found: set[str] = set()
    with zipfile.ZipFile(source, "r") as src, zipfile.ZipFile(output, "w") as dst:
        dst.comment = src.comment
        for info in src.infolist():
            replacement = replacements.get(info.filename)
            if replacement is not None:
                found.add(info.filename)
                data = replacement.read_bytes()
            else:
                data = src.read(info)
            dst.writestr(info, data, compress_type=info.compress_type)

    missing = sorted(set(replacements) - found)
    if missing:
        raise PatchError(
            "target dex disappeared from APK: " + ", ".join(missing)
        )


def replace_zip_member(source: Path, output: Path, member: str, replacement: Path) -> None:
    replace_zip_members(source, output, {member: replacement})


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
    gcam_init_dex = find_gcam_init_dex(source)

    with tempfile.TemporaryDirectory(prefix="poco-f5-device-gate-") as temp:
        root = Path(temp)
        replacements: dict[str, Path] = {}
        dex_reports: dict[str, dict[str, Any]] = {}
        command_tails: dict[str, dict[str, str]] = {}

        with zipfile.ZipFile(source, "r") as archive:
            for dex_name in sorted({target_dex, gcam_init_dex}):
                safe_name = dex_name.replace(".", "_")
                input_dex = root / dex_name
                smali_dir = root / f"smali-{safe_name}"
                rebuilt_dex = root / f"patched-{safe_name}"
                input_dex.write_bytes(archive.read(dex_name))

                disassemble_output = run(
                    [
                        java,
                        "-jar",
                        os.fspath(baksmali),
                        "d",
                        os.fspath(input_dex),
                        "-o",
                        os.fspath(smali_dir),
                    ]
                )

                report_for_dex: dict[str, Any] = {}
                if dex_name == target_dex:
                    report_for_dex["device_gate"] = find_and_patch_smali_tree(smali_dir)
                if dex_name == gcam_init_dex:
                    report_for_dex["gcam_init"] = find_and_patch_gcam_init_smali_tree(
                        smali_dir
                    )

                assemble_output = run(
                    [
                        java,
                        "-jar",
                        os.fspath(smali),
                        "a",
                        os.fspath(smali_dir),
                        "-o",
                        os.fspath(rebuilt_dex),
                    ]
                )
                if not rebuilt_dex.is_file() or rebuilt_dex.stat().st_size == 0:
                    raise PatchError(f"smali did not produce rebuilt dex: {dex_name}")
                if dex_name == target_dex and MARKER_BYTES in rebuilt_dex.read_bytes():
                    raise PatchError(
                        "unsupported-device marker still exists in rebuilt dex"
                    )

                replacements[dex_name] = rebuilt_dex
                dex_reports[dex_name] = report_for_dex
                command_tails[dex_name] = {
                    "baksmali_output_tail": "\n".join(
                        disassemble_output.splitlines()[-20:]
                    ),
                    "smali_output_tail": "\n".join(
                        assemble_output.splitlines()[-20:]
                    ),
                }

            native_name, native_path, native_gxp_report = patch_gcastartup_cpu_fallback(
                archive,
                root,
            )
            replacements[native_name] = native_path

        replace_zip_members(source, output, replacements)

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

    device_metadata = dex_reports[target_dex]["device_gate"]
    gcam_metadata = dex_reports[gcam_init_dex]["gcam_init"]

    return {
        "status": "patched",
        "marker": MARKER,
        "target_dex": target_dex,
        **device_metadata,
        "gcam_init_patch": {
            "target_dex": gcam_init_dex,
            **gcam_metadata,
        },
        "native_gxp_cpu_fallback": native_gxp_report,
        "dex_command_tails": command_tails,
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
