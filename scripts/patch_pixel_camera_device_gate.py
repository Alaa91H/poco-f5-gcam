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
KEEPALIVE_RECEIVER_DESCRIPTOR = (
    "Lcom/google/android/apps/camera/keepalive/KeepAliveBroadcastReceiver;"
)
KEEPALIVE_RECEIVER_BYTES = KEEPALIVE_RECEIVER_DESCRIPTOR.encode("utf-8")
ONECAMERA_PROVIDER_DESCRIPTOR = "Lofe;"
ONECAMERA_REQUEST_PROVIDER_DESCRIPTOR = "Lmta;"
ONECAMERA_OPEN_CAMERA_DESCRIPTOR = "Lug;"
ONECAMERA_ODR_DESCRIPTOR = "Lodr;"
# Runtime-verified on marble: Lodr is in base classes.dex.
KEEPALIVE_ON_RECEIVE_DESCRIPTOR = (
    "onReceive(Landroid/content/Context;Landroid/content/Intent;)V"
)
GCASTARTUP_11_0_073_SHA256 = (
    "34487551ea95b83b76ff41a742c83a6fb27f19ae07d3210139215313e3cacdbe"
)
TUNING_DIAGNOSTIC_STRINGS = (
    "Unknown device code",
    "Failed to get tuning for device code",
    "Device has not been calibrated for HDR+",
    "Unsupported sensor ID. Using tuning defaults.",
    "Using tuning defaults.",
    "lib_aion_buffer.so",
    "aion_context.cc",
    "Check failed: valid_",
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

# AION's context constructor already has a non-fatal failure path. After the
# lazy loader returns an error (for example when lib_aion_buffer.so is absent),
# it stores valid_=false and returns unless bit 0 of the caller-provided strict
# flag is set:
#
#   0x682e600  cmp   w0, #0
#   0x682e604  cset  w8, eq
#   0x682e608  strb  w8, [x20]      ; valid_ = (status == 0)
#   0x682e60c  cbz   w0, 0x682e614 ; success -> return
#   0x682e610  tbnz  w19,#0,0x682e624  ; strict failure -> CHECK(valid_)
#   0x682e614  ...                  ; existing non-fatal return path
#
# The POCO F5 runtime proves lib_aion_buffer.so is unavailable and the strict
# branch terminates the process. NOPing only that TBNZ preserves valid_=false
# and reuses the library's existing non-fatal return path.
AION_FALLBACK_PATCHES: tuple[tuple[str, int, bytes, bytes], ...] = (
    (
        "allow_missing_aion_buffer_nonfatal_fallback",
        0x682E610,
        bytes.fromhex("b3000037"),
        bytes.fromhex("1f2003d5"),
    ),
)

GCASTARTUP_NATIVE_PATCHES = (
    GXP_CPU_PATCHES + TUNING_FALLBACK_PATCHES + AION_FALLBACK_PATCHES
)
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


def _verify_aion_nonfatal_branch(data: bytes) -> dict[str, Any]:
    """Verify AION's existing invalid-context non-fatal return path."""

    digest = hashlib.sha256(data).hexdigest()
    if digest != GCASTARTUP_11_0_073_SHA256:
        raise PatchError(
            "libgcastartup SHA-256 changed; refusing version-specific AION "
            f"fallback patch: expected {GCASTARTUP_11_0_073_SHA256}, found {digest}"
        )

    sequence_offset = 0x682E600
    expected_sequence = bytes.fromhex(
        "1f000071"  # cmp w0, #0
        "e8179f1a"  # cset w8, eq
        "88020039"  # strb w8, [x20]
        "40000034"  # cbz w0, 0x682e614
        "b3000037"  # tbnz w19, #0, 0x682e624
    )
    actual_sequence = data[
        sequence_offset : sequence_offset + len(expected_sequence)
    ]
    if actual_sequence != expected_sequence:
        raise PatchError(
            "AION failure-control sequence changed at "
            f"0x{sequence_offset:x}: expected {expected_sequence.hex()}, "
            f"found {actual_sequence.hex()}"
        )

    branch_offset = AION_FALLBACK_PATCHES[0][1]
    branch_word = struct.unpack_from("<I", data, branch_offset)[0]
    decoded = _decode_aarch64_branch(branch_word, branch_offset)
    if decoded != "tbnz 0x682e624":
        raise PatchError(
            "unexpected AION strict-failure branch semantics at "
            f"0x{branch_offset:x}: {decoded!r}"
        )

    return {
        "library_sha256": digest,
        "sequence_offset": f"0x{sequence_offset:x}",
        "sequence_hex": actual_sequence.hex(),
        "strict_branch_offset": f"0x{branch_offset:x}",
        "strict_branch_hex": data[branch_offset : branch_offset + 4].hex(),
        "strict_branch": decoded,
        "fallback_target": "existing invalid-AION nonfatal return path",
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
    aion_fallback_verification = _verify_aion_nonfatal_branch(original)
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
        "aion_fallback_verification": aion_fallback_verification,
        "model_verification_bypass_performed": False,
        "tuning_bypass_performed": False,
        "tuning_profile_spoof_performed": False,
        "uncalibrated_tuning_fallback_enabled": True,
        "aion_missing_library_nonfatal_fallback_enabled": True,
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


def find_keepalive_receiver_dex(apk: Path) -> str:
    """Locate the dex that contains the Pixel-only keepalive receiver."""

    with zipfile.ZipFile(apk, "r") as archive:
        matches: list[tuple[str, int]] = []
        for info in archive.infolist():
            if not DEX_NAME_RE.fullmatch(info.filename):
                continue
            data = archive.read(info)
            count = data.count(KEEPALIVE_RECEIVER_BYTES)
            if count:
                matches.append((info.filename, count))

    if len(matches) != 1:
        rendered = ", ".join(f"{name}:{count}" for name, count in matches) or "none"
        raise PatchError(
            "expected KeepAliveBroadcastReceiver in exactly one classes*.dex; "
            f"found {rendered}"
        )
    return matches[0][0]


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


def _patch_logical_camera_sensor_ids(
    lines: list[str],
    *,
    method_start: int,
    method_end: int,
) -> tuple[list[str], dict[str, Any]]:
    """Assign GCam logical sensor IDs to top-level multi-camera entries.

    Pixel Camera first adds each top-level camera to StaticMetadataVector, then
    appends its physical camera IDs. On POCO F5 the Xiaomi converter can label
    the top-level logical camera with the same physical GCam sensor ID as one of
    its children. Native Gcam_AllSensorIdsUnique() then rejects the vector.

    Luur.b is the top-level camera's physical-ID set. Only entries with a
    non-empty set are remapped. The enclosing Luve array is verified as
    BACK-first / FRONT-second, so v12 selects kRearLogical or kFrontLogical.
    Physical cameras and synthetic binned/max-resolution entries remain intact.

    Runtime Camera2 probing on marble confirms that exposed IDs 0, 1, 2 and 3
    can deliver sustained YUV frames, while logical IDs 4/5 are not reliable
    application-facing capture endpoints. Keep the mapping diagnostics active
    so camera 3 can be classified before changing the retained-ID policy.
    """

    method_text = "\n".join(lines[method_start : method_end + 1])

    # Keep global checks only for camera-specific tokens that are expected to
    # be unique in this provider method. Generic register operations such as
    # move-result-object v7 can legitimately occur many times elsewhere in the
    # same large synthetic method, so those are verified locally around the
    # unique metadata converter call below.
    unique_camera_shape = (
        "sget-object v0, Luve;->b:Luve;",
        "aput-object v0, v13, v12",
        "sget-object v0, Luve;->a:Luve;",
        "aput-object v0, v13, p0",
        "aget-object v0, v13, v12",
    )
    for token in unique_camera_shape:
        count = method_text.count(token)
        if count != 1:
            raise PatchError(
                "top-level logical-camera metadata shape changed; expected one "
                f"{token!r}, found {count}"
            )

    converter_indexes = [
        i
        for i in range(method_start, method_end)
        if (
            "Lcom/google/googlex/gcam/hdrplus/NativeMetadataConverter;->"
            "C(Luus;)Lcom/google/googlex/gcam/StaticMetadata;" in lines[i]
            and "{v5}" in lines[i]
        )
    ]
    if len(converter_indexes) != 1:
        raise PatchError(
            "expected exactly one top-level NativeMetadataConverter.C(v5) call; "
            f"found {len(converter_indexes)}"
        )
    converter_index = converter_indexes[0]

    def _previous_code_line(index: int) -> int:
        cursor = index - 1
        while cursor >= method_start:
            stripped = lines[cursor].strip()
            if stripped and not stripped.startswith("#"):
                return cursor
            cursor -= 1
        raise PatchError("metadata converter has no preceding code instruction")

    def _next_code_line_local(index: int) -> int:
        cursor = index + 1
        while cursor < method_end:
            stripped = lines[cursor].strip()
            if stripped and not stripped.startswith("#"):
                return cursor
            cursor += 1
        raise PatchError("metadata converter sequence ended unexpectedly")

    result_index = _next_code_line_local(converter_index)
    if lines[result_index].strip() != "move-result-object v7":
        raise PatchError(
            "top-level metadata converter is no longer followed by "
            "'move-result-object v7'"
        )

    saved_source_index = _next_code_line_local(result_index)
    if lines[saved_source_index].strip() != "move-object/from16 v24, v5":
        raise PatchError(
            "top-level metadata source is no longer preserved after conversion"
        )

    add_call = (
        "invoke-virtual {v14, v7}, "
        "Lcom/google/googlex/gcam/StaticMetadataVector;->"
        "c(Lcom/google/googlex/gcam/StaticMetadata;)V"
    )
    add_indexes = [
        i
        for i in range(saved_source_index + 1, min(method_end, saved_source_index + 40))
        if lines[i].strip() == add_call
    ]
    if len(add_indexes) != 1:
        raise PatchError(
            "top-level metadata result is no longer paired with exactly one "
            "StaticMetadataVector add in its local block"
        )
    add_index = add_indexes[0]

    # Pixel Camera stamps package/version metadata and reads the converted
    # sensor ID before adding the top-level metadata. Verify those operations
    # in order without requiring them to be adjacent.
    expected_pre_add_flow = (
        "invoke-virtual {v7, v5}, Lcom/google/googlex/gcam/StaticMetadata;->q(Ljava/lang/String;)V",
        "invoke-virtual {v7, v5}, Lcom/google/googlex/gcam/StaticMetadata;->r(Ljava/lang/String;)V",
        "invoke-virtual {v7}, Lcom/google/googlex/gcam/StaticMetadata;->g()Lzoi;",
    )
    cursor = saved_source_index + 1
    for token in expected_pre_add_flow:
        matches = [
            i
            for i in range(cursor, add_index)
            if lines[i].strip() == token
        ]
        if len(matches) != 1:
            raise PatchError(
                "top-level metadata pre-add flow changed; expected one "
                f"{token!r}, found {len(matches)}"
            )
        cursor = matches[0] + 1

    physical_set_indexes = [
        i
        for i in range(add_index + 1, min(method_end, add_index + 48))
        if lines[i].strip() == "iget-object v5, v5, Luur;->b:Lyfm;"
    ]
    if len(physical_set_indexes) != 1:
        raise PatchError(
            "top-level metadata add is no longer followed by exactly one "
            "physical-ID set in its local block"
        )
    physical_set_index = physical_set_indexes[0]
    post_add_window = "\n".join(
        lines[add_index + 1 : min(method_end, physical_set_index + 10)]
    )
    for token in (
        "move-object/from16 v5, v24",
        "check-cast v5, Luur;",
        "iget-object v5, v5, Luur;->b:Lyfm;",
        "invoke-interface {v5}, Ljava/util/Set;->iterator()Ljava/util/Iterator;",
    ):
        count = post_add_window.count(token)
        if count != 1:
            raise PatchError(
                "top-level metadata physical-ID flow changed; expected one "
                f"{token!r}, found {count}"
            )

    labels = (
        "poco_top_level_front_logical",
        "poco_top_level_set_logical",
        "poco_top_level_logical_done",
        "poco_top_level_alias_done",
    )
    if any(f":{label}" in method_text for label in labels):
        raise PatchError("logical camera sensor-ID patch labels already exist")

    guard = [
        "    # POCO F5: logical camera must not reuse a physical GCam sensor ID.",
        "    move-object/from16 v5, v24",
        "",
        "    check-cast v5, Luur;",
        "",
        "    iget-object v5, v5, Luur;->b:Lyfm;",
        "",
        "    invoke-interface {v5}, Ljava/util/Set;->isEmpty()Z",
        "",
        "    move-result v5",
        "",
        "    if-nez v5, :poco_top_level_logical_done",
        "",
        "    if-nez v12, :poco_top_level_front_logical",
        "",
        "    sget-object v5, Lzoi;->s:Lzoi;",
        "",
        "    goto :poco_top_level_set_logical",
        "",
        "    :poco_top_level_front_logical",
        "    sget-object v5, Lzoi;->v:Lzoi;",
        "",
        "    :poco_top_level_set_logical",
        "    invoke-virtual {v7, v5}, "
        "Lcom/google/googlex/gcam/StaticMetadata;->u(Lzoi;)V",
        "",
        "    :poco_top_level_logical_done",
        "",
        "    # POCO F5: exclude Camera2 aliases that collapse onto an existing",
        "    # GCam sensor enum. Keep Android/Camera2 enumeration unchanged;",
        "    # only omit the duplicate metadata entry from the native GCam vector.",
        "    move-object/from16 v5, v24",
        "",
        "    check-cast v5, Luur;",
        "",
        "    iget-object v5, v5, Luur;->a:Luuv;",
        "",
        "    iget-object v5, v5, Luuv;->a:Ljava/lang/String;",
        "",
        "    # Diagnostic only: record the GCam sensor enum assigned to each",
        "    # top-level Camera2 ID before compatibility filtering.",
        "    move-object/from16 v26, v5",
        "",
        '    const-string v25, "GCamMappedCameraId"',
        "",
        "    invoke-static/range {v25 .. v26}, Landroid/util/Log;->e(Ljava/lang/String;Ljava/lang/String;)I",
        "",
        "    invoke-virtual {v7}, Lcom/google/googlex/gcam/StaticMetadata;->g()Lzoi;",
        "",
        "    move-result-object v5",
        "",
        "    invoke-virtual {v5}, Lzoi;->toString()Ljava/lang/String;",
        "",
        "    move-result-object v5",
        "",
        "    move-object/from16 v26, v5",
        "",
        '    const-string v25, "GCamMappedSensorId"',
        "",
        "    invoke-static/range {v25 .. v26}, Landroid/util/Log;->e(Ljava/lang/String;Ljava/lang/String;)I",
        "",
        "    # Recover the Camera2 ID after diagnostic logging.",
        "    move-object/from16 v5, v24",
        "",
        "    check-cast v5, Luur;",
        "",
        "    iget-object v5, v5, Luur;->a:Luuv;",
        "",
        "    iget-object v5, v5, Luuv;->a:Ljava/lang/String;",
        "",
        "    move-object/from16 v26, v5",
        "",
        '    const-string v25, "3"',
        "",
        "    invoke-virtual/range {v25 .. v26}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z",
        "",
        "    move-result v5",
        "",
        "    if-nez v5, :poco_top_level_alias_done",
        "",
        "    # Camera2 ID 4 is Xiaomi's logical rear camera. Real-device logs",
        "    # show its MultiCameraSAT graph cannot initialize for this app",
        "    # (logical camera type 7 / invalid logical camera ID). Keep it",
        "    # enumerated in Camera2 but omit it from the native GCam vector.",
        '    const-string v25, "4"',
        "",
        "    invoke-virtual/range {v25 .. v26}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z",
        "",
        "    move-result v5",
        "",
        "    if-nez v5, :poco_top_level_alias_done",
        "",
        '    const-string v25, "5"',
        "",
        "    invoke-virtual/range {v25 .. v26}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z",
        "",
        "    move-result v5",
        "",
        "    if-nez v5, :poco_top_level_alias_done",
        "",
        '    const-string v25, "6"',
        "",
        "    invoke-virtual/range {v25 .. v26}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z",
        "",
        "    move-result v5",
        "",
        "    if-nez v5, :poco_top_level_alias_done",
        "",
    ]
    patched = (
        lines[:add_index]
        + guard
        + [
            lines[add_index],
            "",
            "    :poco_top_level_alias_done",
        ]
        + lines[add_index + 1 :]
    )
    return patched, {
        "status": "filtered_xiaomi_logical_and_duplicate_aliases",
        "predicate": "non_empty_physical_camera_id_set",
        "back_sensor_id": "kRearLogical (5)",
        "front_sensor_id": "kFrontLogical (3)",
        "duplicate_alias_camera_ids_excluded_from_gcam_vector": ["3", "5", "6"],
        "unsupported_logical_camera_ids_excluded_from_gcam_vector": ["4"],
        "excluded_camera_ids_from_gcam_vector": ["3", "4", "5", "6"],
        "camera2_enumeration_unchanged": True,
        "expected_kept_camera_ids": ["0", "2", "1"],
        "physical_entries_preserved": True,
        "pre_filter_mapping_diagnostics": {
            "camera_id_tag": "GCamMappedCameraId",
            "sensor_id_tag": "GCamMappedSensorId",
            "behavior_changed": False,
        },
    }


def _inject_camera_source_runtime_diagnostics(
    lines: list[str],
) -> tuple[list[str], dict[str, Any]]:
    """Log Android Camera2 IDs in the same order metadata enters the GCam vector.

    Top-level camera IDs are logged while Pixel Camera iterates Luut.h(Luve).
    Physical IDs are logged later when the deduplicated Luuv list is converted.
    The final GCamSensorIds dump therefore lets real-device reports correlate
    Xiaomi camera IDs with the generated GCam sensor enums without changing
    camera selection or sensor metadata.
    """

    method_text = "\n".join(lines)
    for tag in ("GCamTopCameraId", "GCamPhysicalCameraId"):
        if tag in method_text:
            raise PatchError(f"camera-source diagnostic tag already exists: {tag}")

    top_id_get = "iget-object v7, v5, Luuv;->a:Ljava/lang/String;"
    top_indexes = [i for i, line in enumerate(lines) if line.strip() == top_id_get]
    if len(top_indexes) != 1:
        raise PatchError(
            "expected exactly one top-level Luuv camera-ID read; "
            f"found {len(top_indexes)}"
        )
    top_index = top_indexes[0]

    top_null_index = top_index + 1
    while top_null_index < len(lines) and not lines[top_null_index].strip():
        top_null_index += 1
    if top_null_index >= len(lines) or lines[top_null_index].strip() != "if-eqz v7, :cond_131":
        raise PatchError("top-level Luuv camera-ID null guard changed")

    top_call_index = top_null_index + 1
    while top_call_index < len(lines) and not lines[top_call_index].strip():
        top_call_index += 1
    if (
        top_call_index >= len(lines)
        or lines[top_call_index].strip()
        != "invoke-interface {v2, v5}, Luut;->a(Luuv;)Luus;"
    ):
        raise PatchError("top-level Luuv metadata lookup changed")

    top_block = [
        "",
        '    const-string v24, "GCamTopCameraId"',
        "",
        "    move-object/from16 v25, v7",
        "",
        "    invoke-static/range {v24 .. v25}, Landroid/util/Log;->e(Ljava/lang/String;Ljava/lang/String;)I",
        "",
    ]
    lines = lines[:top_call_index] + top_block + lines[top_call_index:]

    physical_cast = "check-cast v0, Luuv;"
    physical_indexes = [i for i, line in enumerate(lines) if line.strip() == physical_cast]
    if len(physical_indexes) != 1:
        raise PatchError(
            "expected exactly one physical Luuv conversion loop; "
            f"found {len(physical_indexes)}"
        )
    physical_index = physical_indexes[0]

    physical_call_index = physical_index + 1
    while physical_call_index < len(lines) and not lines[physical_call_index].strip():
        physical_call_index += 1
    if (
        physical_call_index >= len(lines)
        or lines[physical_call_index].strip()
        != "invoke-interface {v2, v0}, Luut;->a(Luuv;)Luus;"
    ):
        raise PatchError("physical Luuv metadata lookup changed")

    physical_block = [
        "",
        "    # iget-object uses 4-bit registers; preserve a low temp explicitly.",
        "    move-object/from16 v24, v3",
        "",
        "    iget-object v3, v0, Luuv;->a:Ljava/lang/String;",
        "",
        "    move-object/from16 v26, v3",
        "",
        "    move-object/from16 v3, v24",
        "",
        '    const-string v25, "GCamPhysicalCameraId"',
        "",
        "    invoke-static/range {v25 .. v26}, Landroid/util/Log;->e(Ljava/lang/String;Ljava/lang/String;)I",
        "",
    ]
    lines = lines[:physical_call_index] + physical_block + lines[physical_call_index:]

    return lines, {
        "status": "logged_android_camera_ids",
        "top_level_tag": "GCamTopCameraId",
        "physical_tag": "GCamPhysicalCameraId",
        "ordering": "top_level_then_physical_matches_StaticMetadataVector",
        "behavior_changed": False,
    }


def _inject_sensor_vector_runtime_diagnostics(
    lines: list[str],
) -> tuple[list[str], dict[str, Any]]:
    """Log every StaticMetadata sensor enum immediately before Gcam_Create.

    This is intentionally diagnostic-only. It does not mutate sensor IDs or
    bypass Gcam_AllSensorIdsUnique. Real-device logs can then identify the exact
    duplicate enum(s) produced by the Xiaomi camera topology before applying a
    narrower compatibility fix.
    """

    create_indexes = [
        i
        for i, line in enumerate(lines)
        if GCAM_CREATE_SYMBOL in line and "invoke-static" in line
    ]
    if len(create_indexes) != 1:
        raise PatchError(
            "expected exactly one Gcam_Create before sensor-vector diagnostics; "
            f"found {len(create_indexes)}"
        )
    create_index = create_indexes[0]
    method_start, method_end = _method_bounds(lines, create_index)

    labels = ("poco_sensor_diag_loop", "poco_sensor_diag_done")
    method_text = "\n".join(lines[method_start : method_end + 1])
    if any(f":{label}" in method_text for label in labels):
        raise PatchError("sensor-vector diagnostic labels already exist")

    code_indexes: list[int] = []
    cursor = create_index - 1
    while cursor >= method_start and len(code_indexes) < 6:
        stripped = lines[cursor].strip()
        if stripped and not stripped.startswith("#"):
            code_indexes.append(cursor)
        cursor -= 1
    code_indexes.reverse()

    expected_prefix = (
        "iget-wide v2, v1, Lcom/google/googlex/gcam/InitParams;->a:J",
        "iget-wide v4, v14, Lcom/google/googlex/gcam/StaticMetadataVector;->a:J",
        "move-object/from16 v16, v1",
        "move-wide/from16 v17, v4",
        "move-object/from16 v19, v14",
        "move-wide v14, v2",
    )
    actual_prefix = tuple(lines[i].strip() for i in code_indexes)
    if actual_prefix != expected_prefix:
        raise PatchError(
            "Gcam_Create pre-call register flow changed; expected "
            f"{expected_prefix!r}, got {actual_prefix!r}"
        )

    insert_index = code_indexes[0]
    diagnostic_block = [
        "    # POCO F5 diagnostic: log StaticMetadata sensor IDs before native create.",
        "    const/4 v0, 0x0",
        "",
        "    :poco_sensor_diag_loop",
        "    invoke-virtual {v14}, Lcom/google/googlex/gcam/StaticMetadataVector;->a()J",
        "",
        "    move-result-wide v2",
        "",
        "    int-to-long v4, v0",
        "",
        "    cmp-long v6, v4, v2",
        "",
        "    if-gez v6, :poco_sensor_diag_done",
        "",
        "    invoke-virtual {v14, v0}, Lcom/google/googlex/gcam/StaticMetadataVector;->b(I)Lcom/google/googlex/gcam/StaticMetadata;",
        "",
        "    move-result-object v2",
        "",
        "    invoke-virtual {v2}, Lcom/google/googlex/gcam/StaticMetadata;->g()Lzoi;",
        "",
        "    move-result-object v2",
        "",
        "    invoke-virtual {v2}, Lzoi;->toString()Ljava/lang/String;",
        "",
        "    move-result-object v2",
        "",
        '    const-string v3, "GCamSensorIds"',
        "",
        "    invoke-static {v3, v2}, Landroid/util/Log;->e(Ljava/lang/String;Ljava/lang/String;)I",
        "",
        "    add-int/lit8 v0, v0, 0x1",
        "",
        "    goto :poco_sensor_diag_loop",
        "",
        "    :poco_sensor_diag_done",
        "",
    ]

    patched = lines[:insert_index] + diagnostic_block + lines[insert_index:]
    return patched, {
        "status": "logged_static_metadata_sensor_ids",
        "tag": "GCamSensorIds",
        "location": "immediately_before_Gcam_Create",
        "behavior_changed": False,
    }


def patch_gcam_init_smali_text(text: str) -> tuple[str, dict[str, Any]]:
    """Keep startup off unsupported Pixel accelerator paths.

    Real-device evidence shows the POCO F5 reaches CameraService and creates a
    non-null native Gcam object after the GXP/AION fallbacks. Almond TPU and
    Tomte grain remain disabled through their existing continuation paths.
    Unrelated image processors are preserved while the Xiaomi sensor-ID mapping
    is handled at the caller-local uniqueness guard below.
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

    lines, logical_mapping_metadata = _patch_logical_camera_sensor_ids(
        lines,
        method_start=method_start,
        method_end=method_end,
    )
    lines, camera_source_diagnostics = _inject_camera_source_runtime_diagnostics(lines)
    lines, sensor_vector_diagnostics = _inject_sensor_vector_runtime_diagnostics(lines)

    # Preserve Pixel Camera's native uniqueness check. The compatibility fix
    # changes only the misidentified logical entries before Gcam_Create.
    create_indexes = [
        i
        for i, line in enumerate(lines)
        if GCAM_CREATE_SYMBOL in line and "invoke-static" in line
    ]
    if len(create_indexes) != 1:
        raise PatchError(
            "expected exactly one Gcam_Create after InitParams patching; "
            f"found {len(create_indexes)}"
        )
    method_start, method_end = _method_bounds(lines, create_indexes[0])

    ready_calls = [
        i
        for i in range(method_start, method_end)
        if "Lcom/google/googlex/gcam/Gcam;->g()Z" in lines[i]
    ]
    if len(ready_calls) != 1:
        raise PatchError(
            "expected exactly one Gcam.g() sensor-ID uniqueness check; "
            f"found {len(ready_calls)}"
        )
    ready_call = ready_calls[0]

    def _next_code_line(index: int) -> int:
        cursor = index + 1
        while cursor < method_end:
            stripped = lines[cursor].strip()
            if stripped and not stripped.startswith("#"):
                return cursor
            cursor += 1
        raise PatchError("Gcam.g() sensor-ID check ended unexpectedly")

    move_index = _next_code_line(ready_call)
    move_match = re.match(
        r"^\s*move-result\s+(?P<reg>[vp]\d+)\s*$",
        lines[move_index],
    )
    if not move_match:
        raise PatchError("Gcam.g() is not followed by move-result")
    ready_reg = move_match.group("reg")

    branch_index = _next_code_line(move_index)
    branch_match = re.match(
        rf"^\s*if-eqz\s+{re.escape(ready_reg)},\s*:(?P<label>[A-Za-z0-9_.$-]+)\s*$",
        lines[branch_index],
    )
    if not branch_match:
        raise PatchError(
            "Gcam sensor-ID result is not followed by the expected if-eqz"
        )
    failure_label = branch_match.group("label")

    failure_indexes = [
        i
        for i in range(branch_index + 1, method_end)
        if lines[i].strip() == f":{failure_label}"
    ]
    if len(failure_indexes) != 1:
        raise PatchError(
            "expected exactly one Gcam sensor-ID failure label; "
            f"found {len(failure_indexes)}"
        )
    failure_index = failure_indexes[0]
    success_block = "\n".join(lines[branch_index + 1 : failure_index])
    if "return-object" not in success_block:
        raise PatchError(
            "Gcam sensor-ID success path no longer returns the Gcam object"
        )
    failure_block = "\n".join(lines[failure_index : method_end])
    if failure_block.count("Ljava/lang/IllegalArgumentException;") != 2:
        raise PatchError(
            "Gcam sensor-ID failure block no longer has exactly one "
            "IllegalArgumentException construction"
        )
    if "throw " not in failure_block:
        raise PatchError(
            "Gcam sensor-ID failure block no longer throws the exception"
        )

    sensor_id_metadata = {
        **logical_mapping_metadata,
        "native_check": "Gcam_AllSensorIdsUnique",
        "native_check_preserved": True,
        "failure_label": failure_label,
        "failure_behavior": "IllegalArgumentException if duplicates remain",
    }

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
        "sensor_id_uniqueness": sensor_id_metadata,
        "camera_source_diagnostics": camera_source_diagnostics,
        "sensor_vector_diagnostics": sensor_vector_diagnostics,
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


def patch_onecamera_optional_key_smali_text(
    text: str,
) -> tuple[str, dict[str, Any]]:
    """Allow the Pixel-only Ltdn.b request key to be absent on POCO F5.

    Real-device Android 17 evidence shows OneCamera crashes in ofe.a(PG:413)
    because Ltdn.b is null while this provider wraps it with Optional.of().
    The immediately preceding sibling Ltdn.a already uses ofNullable(), which
    defines the intended absent-key representation for this provider. Change
    only the exact Ltdn.b site and fail closed if its shape moves.
    """

    lines = text.splitlines()
    class_matches = [
        i
        for i, line in enumerate(lines)
        if re.match(
            r"^\.class\s+.*" + re.escape(ONECAMERA_PROVIDER_DESCRIPTOR) + r"\s*$",
            line,
        )
    ]
    if len(class_matches) != 1:
        raise PatchError(
            "expected exactly one OneCamera provider class Lofe;; "
            f"found {len(class_matches)}"
        )

    method_starts = [
        i
        for i, line in enumerate(lines)
        if line.strip() == ".method public final synthetic a()Ljava/lang/Object;"
    ]
    if len(method_starts) != 1:
        raise PatchError(
            "expected exactly one ofe synthetic provider a() method; "
            f"found {len(method_starts)}"
        )
    method_start = method_starts[0]
    method_end = method_start + 1
    while method_end < len(lines) and lines[method_end].strip() != ".end method":
        method_end += 1
    if method_end >= len(lines):
        raise PatchError("ofe.a() is unterminated")

    first_key_line = (
        "sget-object v1, Ltdn;->a:"
        "Landroid/hardware/camera2/CaptureRequest$Key;"
    )
    first_key_indexes = [
        i for i in range(method_start, method_end)
        if lines[i].strip() == first_key_line
    ]
    if len(first_key_indexes) != 1:
        raise PatchError(
            "expected exactly one Ltdn.a request key in Lodr.a(Object); "
            f"found {len(first_key_indexes)}"
        )
    first_key_index = first_key_indexes[0]

    key_line = (
        "sget-object v0, Ltdn;->b:"
        "Landroid/hardware/camera2/CaptureRequest$Key;"
    )
    key_indexes = [
        i
        for i in range(method_start, method_end)
        if lines[i].strip() == key_line
    ]
    if len(key_indexes) != 1:
        raise PatchError(
            "expected exactly one Ltdn.b CaptureRequest key in ofe.a(); "
            f"found {len(key_indexes)}"
        )
    key_index = key_indexes[0]

    cursor = key_index + 1
    while cursor < method_end and not lines[cursor].strip():
        cursor += 1
    expected_of = (
        "invoke-static {v0}, Lj$/util/Optional;->"
        "of(Ljava/lang/Object;)Lj$/util/Optional;"
    )
    if cursor >= method_end or lines[cursor].strip() != expected_of:
        actual = lines[cursor].strip() if cursor < method_end else "<end>"
        raise PatchError(
            "Ltdn.b is no longer wrapped by the expected Optional.of call; "
            f"found {actual!r}"
        )

    # Verify the neighboring Ltdn.a key still uses Google's nullable-safe form.
    sibling_line = (
        "sget-object v0, Ltdn;->a:"
        "Landroid/hardware/camera2/CaptureRequest$Key;"
    )
    sibling_indexes = [
        i
        for i in range(max(method_start, key_index - 12), key_index)
        if lines[i].strip() == sibling_line
    ]
    if len(sibling_indexes) != 1:
        raise PatchError(
            "expected the neighboring Ltdn.a key immediately before Ltdn.b"
        )
    sibling_cursor = sibling_indexes[0] + 1
    while sibling_cursor < key_index and not lines[sibling_cursor].strip():
        sibling_cursor += 1
    expected_nullable = (
        "invoke-static {v0}, Lj$/util/Optional;->"
        "ofNullable(Ljava/lang/Object;)Lj$/util/Optional;"
    )
    if (
        sibling_cursor >= key_index
        or lines[sibling_cursor].strip() != expected_nullable
    ):
        raise PatchError(
            "neighboring Ltdn.a no longer uses Optional.ofNullable"
        )

    indent = re.match(r"^(\s*)", lines[cursor]).group(1)
    lines[cursor] = indent + expected_nullable
    patched = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    return patched, {
        "status": "allow_absent_ldtn_b_capture_request_key",
        "class": ONECAMERA_PROVIDER_DESCRIPTOR,
        "method": lines[method_start].strip(),
        "key": "Ltdn.b",
        "old_wrapper": "Optional.of",
        "new_wrapper": "Optional.ofNullable",
        "behavior": "represent absent Pixel-only request key as Optional.empty",
    }


def find_and_patch_onecamera_optional_key_smali_tree(
    root: Path,
) -> dict[str, Any]:
    matches: list[Path] = []
    class_line_re = re.compile(
        r"^\.class\s+.*" + re.escape(ONECAMERA_PROVIDER_DESCRIPTOR) + r"\s*$",
        re.MULTILINE,
    )
    for path in root.rglob("*.smali"):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if class_line_re.search(text):
            matches.append(path)

    if len(matches) != 1:
        rendered = ", ".join(os.fspath(p.relative_to(root)) for p in matches) or "none"
        raise PatchError(
            "expected OneCamera provider Lofe; in exactly one smali file; "
            f"found {rendered}"
        )

    target = matches[0]
    original = target.read_text(encoding="utf-8")
    patched, metadata = patch_onecamera_optional_key_smali_text(original)
    target.write_text(patched, encoding="utf-8")
    metadata["smali_path"] = os.fspath(target.relative_to(root))
    return metadata



def patch_onecamera_missing_request_key_smali_text(
    text: str,
) -> tuple[str, dict[str, Any]]:
    """Reuse OneCamera's empty-request path when Ltdi.a is absent.

    POCO F5 runtime evidence reaches mta.a(PG:720), where the enabled feature
    constructs Lupd(Ltdi.a, Integer(1)). Ltdi.a is null on this device, and the
    Lupd constructor immediately dereferences the key. The same provider
    already has an empty collection branch for the feature-disabled case.
    Branch to that existing empty path only when Ltdi.a is null.
    """

    lines = text.splitlines()
    class_matches = [
        i for i, line in enumerate(lines)
        if re.match(
            r"^\.class\s+.*" + re.escape(ONECAMERA_REQUEST_PROVIDER_DESCRIPTOR) + r"\s*$",
            line,
        )
    ]
    if len(class_matches) != 1:
        raise PatchError(
            "expected exactly one OneCamera request provider class Lmta;; "
            f"found {len(class_matches)}"
        )

    method_starts = [
        i for i, line in enumerate(lines)
        if line.strip() == ".method public final synthetic a()Ljava/lang/Object;"
    ]
    if len(method_starts) != 1:
        raise PatchError(
            "expected exactly one mta synthetic provider a() method; "
            f"found {len(method_starts)}"
        )
    method_start = method_starts[0]
    method_end = method_start + 1
    while method_end < len(lines) and lines[method_end].strip() != ".end method":
        method_end += 1
    if method_end >= len(lines):
        raise PatchError("mta.a() is unterminated")

    key_line = (
        "sget-object v0, Ltdi;->a:"
        "Landroid/hardware/camera2/CaptureRequest$Key;"
    )
    key_indexes = [
        i for i in range(method_start, method_end)
        if lines[i].strip() == key_line
    ]
    if len(key_indexes) != 1:
        raise PatchError(
            "expected exactly one Ltdi.a CaptureRequest key in mta.a(); "
            f"found {len(key_indexes)}"
        )
    key_index = key_indexes[0]

    # The feature-disabled branch immediately before the key already returns
    # Google's canonical empty collection. Reuse exactly that label.
    branch_indexes = []
    branch_label = None
    for i in range(max(method_start, key_index - 12), key_index):
        match = re.match(
            r"^\s*if-eqz\s+v0,\s*:(?P<label>[A-Za-z0-9_.$-]+)\s*$",
            lines[i],
        )
        if match:
            branch_indexes.append(i)
            branch_label = match.group("label")
    if len(branch_indexes) != 1 or branch_label is None:
        raise PatchError(
            "expected exactly one feature-disabled if-eqz branch before Ltdi.a"
        )

    label_indexes = [
        i for i in range(key_index + 1, min(method_end, key_index + 32))
        if lines[i].strip() == f":{branch_label}"
    ]
    if len(label_indexes) != 1:
        raise PatchError(
            "existing empty-request branch for Ltdi.a moved or disappeared"
        )
    label_index = label_indexes[0]

    local_window = "\n".join(lines[key_index:label_index + 8])
    for token in (
        "invoke-static {v4}, Ljava/lang/Integer;->valueOf(I)Ljava/lang/Integer;",
        "new-instance v2, Lupd;",
        "invoke-direct {v2, v0, v1}, "
        "Lupd;-><init>(Landroid/hardware/camera2/CaptureRequest$Key;Ljava/lang/Object;)V",
        "new-instance v0, Lyjh;",
        "sget-object v0, Lyiu;->a:Lyiu;",
    ):
        if local_window.count(token) != 1:
            raise PatchError(
                "mta Ltdi.a request-provider shape changed; expected one "
                f"{token!r}"
            )

    indent = re.match(r"^(\s*)", lines[key_index]).group(1)
    guard = [
        "",
        f"{indent}# POCO F5: optional vendor CaptureRequest key is absent.",
        f"{indent}if-eqz v0, :{branch_label}",
    ]
    patched_lines = lines[: key_index + 1] + guard + lines[key_index + 1 :]
    patched = "\n".join(patched_lines) + ("\n" if text.endswith("\n") else "")
    return patched, {
        "status": "allow_absent_ldti_a_capture_request_key",
        "class": ONECAMERA_REQUEST_PROVIDER_DESCRIPTOR,
        "method": lines[method_start].strip(),
        "key": "Ltdi.a",
        "fallback_label": branch_label,
        "behavior": "reuse existing empty request-key collection when vendor key is absent",
    }


def find_and_patch_onecamera_missing_request_key_smali_tree(
    root: Path,
) -> dict[str, Any]:
    matches: list[Path] = []
    class_line_re = re.compile(
        r"^\.class\s+.*"
        + re.escape(ONECAMERA_REQUEST_PROVIDER_DESCRIPTOR)
        + r"\s*$",
        re.MULTILINE,
    )
    for path in root.rglob("*.smali"):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if class_line_re.search(text):
            matches.append(path)

    if len(matches) != 1:
        rendered = ", ".join(os.fspath(p.relative_to(root)) for p in matches) or "none"
        raise PatchError(
            "expected OneCamera request provider Lmta; in exactly one smali file; "
            f"found {rendered}"
        )

    target = matches[0]
    patched, metadata = patch_onecamera_missing_request_key_smali_text(
        target.read_text(encoding="utf-8")
    )
    target.write_text(patched, encoding="utf-8")
    metadata["smali_path"] = os.fspath(target.relative_to(root))
    return metadata



def patch_onecamera_odr_missing_request_key_smali_text(
    text: str,
) -> tuple[str, dict[str, Any]]:
    """Omit only the absent Ltdn.b request entry in Lodr.a(Object).

    Real-device Android 17 evidence identifies odr.a(PG:24) as the remaining
    direct caller of Lupd.<init> with a null request key. The same Pixel Camera
    build already proves Ltdn.b is absent on marble while Ltdn.a is usable.

    Keep the Ltdn.a request entry unchanged. If Ltdn.b is null, reuse a normal
    singleton Set containing the first entry instead of constructing Lupd with
    the absent key. This remains caller-local and does not weaken Lupd's
    non-null contract globally.
    """

    lines = text.splitlines()
    class_matches = [
        i for i, line in enumerate(lines)
        if re.match(
            r"^\.class\s+.*" + re.escape(ONECAMERA_ODR_DESCRIPTOR) + r"\s*$",
            line,
        )
    ]
    if len(class_matches) != 1:
        raise PatchError(
            "expected exactly one OneCamera request-entry class Lodr;; "
            f"found {len(class_matches)}"
        )

    signature = ".method public final a(Ljava/lang/Object;)V"
    method_starts = [
        i for i, line in enumerate(lines)
        if line.strip() == signature
    ]
    if len(method_starts) != 1:
        raise PatchError(
            "expected exactly one Lodr.a(Object) method; "
            f"found {len(method_starts)}"
        )
    method_start = method_starts[0]
    method_end = method_start + 1
    while method_end < len(lines) and lines[method_end].strip() != ".end method":
        method_end += 1
    if method_end >= len(lines):
        raise PatchError("Lodr.a(Object) is unterminated")

    method_text = "\n".join(lines[method_start : method_end + 1])
    expected_tokens = (
        "sget-object v1, Ltdn;->a:Landroid/hardware/camera2/CaptureRequest$Key;",
        "invoke-direct {v2, v1, v0}, "
        "Lupd;-><init>(Landroid/hardware/camera2/CaptureRequest$Key;Ljava/lang/Object;)V",
        "sget-object v0, Ltdn;->b:Landroid/hardware/camera2/CaptureRequest$Key;",
        "invoke-direct {v1, v0, p1}, "
        "Lupd;-><init>(Landroid/hardware/camera2/CaptureRequest$Key;Ljava/lang/Object;)V",
        "invoke-static {v2, v1}, "
        "Lyfm;->I(Ljava/lang/Object;Ljava/lang/Object;)Lyfm;",
        "invoke-interface {p0, p1}, Luoi;->t(Ljava/util/Set;)V",
    )
    for token in expected_tokens:
        if method_text.count(token) != 1:
            raise PatchError(
                "Lodr Ltdn.a/Ltdn.b request-entry shape changed; expected one "
                f"{token!r}"
            )

    key_line = (
        "sget-object v0, Ltdn;->b:"
        "Landroid/hardware/camera2/CaptureRequest$Key;"
    )
    key_indexes = [
        i for i in range(method_start, method_end)
        if lines[i].strip() == key_line
    ]
    if len(key_indexes) != 1:
        raise PatchError(
            "expected exactly one Ltdn.b request key in Lodr.a(Object); "
            f"found {len(key_indexes)}"
        )
    key_index = key_indexes[0]

    pair_call = (
        "invoke-static {v2, v1}, "
        "Lyfm;->I(Ljava/lang/Object;Ljava/lang/Object;)Lyfm;"
    )
    pair_indexes = [
        i for i in range(key_index + 1, method_end)
        if lines[i].strip() == pair_call
    ]
    if len(pair_indexes) != 1:
        raise PatchError(
            "expected exactly one two-entry request-set builder after Ltdn.b"
        )
    pair_index = pair_indexes[0]

    move_result_index = pair_index + 1
    while move_result_index < method_end and not lines[move_result_index].strip():
        move_result_index += 1
    if (
        move_result_index >= method_end
        or lines[move_result_index].strip() != "move-result-object p1"
    ):
        raise PatchError(
            "Lodr two-entry request-set result no longer lands in p1"
        )

    label_first_absent = "poco_odr_ldtn_a_absent"
    label_absent = "poco_odr_ldtn_b_absent"
    label_ready = "poco_odr_request_set_ready"
    for label in (label_first_absent, label_absent, label_ready):
        if any(
            line.strip() == f":{label}"
            for line in lines[method_start:method_end]
        ):
            raise PatchError(f"Lodr compatibility label already exists: {label}")

    first_key_indent = re.match(r"^(\s*)", lines[first_key_index]).group(1)
    first_key_guard = [
        "",
        f"{first_key_indent}# POCO F5: Ltdn.a is optional on non-Pixel camera HALs.",
        f"{first_key_indent}if-eqz v1, :{label_first_absent}",
    ]
    lines = (
        lines[: first_key_index + 1]
        + first_key_guard
        + lines[first_key_index + 1 :]
    )
    key_index += len(first_key_guard)
    pair_index += len(first_key_guard)
    move_result_index += len(first_key_guard)

    key_indent = re.match(r"^(\s*)", lines[key_index]).group(1)
    key_guard = [
        "",
        f"{key_indent}# POCO F5: Ltdn.b is an optional Pixel-only request key.",
        f"{key_indent}if-eqz v0, :{label_absent}",
    ]
    lines = lines[: key_index + 1] + key_guard + lines[key_index + 1 :]

    pair_index += len(key_guard)
    move_result_index += len(key_guard)
    result_indent = re.match(r"^(\s*)", lines[move_result_index]).group(1)
    fallback = [
        "",
        f"{result_indent}goto :{label_ready}",
        "",
        f"{result_indent}:{label_absent}",
        "",
        f"{result_indent}invoke-static {{v2}}, "
        "Ljava/util/Collections;->singleton(Ljava/lang/Object;)Ljava/util/Set;",
        "",
        f"{result_indent}move-result-object p1",
        "",
        f"{result_indent}goto :{label_ready}",
        "",
        f"{result_indent}:{label_first_absent}",
        "",
        f"{result_indent}invoke-static {{}}, "
        "Ljava/util/Collections;->emptySet()Ljava/util/Set;",
        "",
        f"{result_indent}move-result-object p1",
        "",
        f"{result_indent}:{label_ready}",
    ]
    lines = lines[: move_result_index + 1] + fallback + lines[move_result_index + 1 :]

    patched = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    return patched, {
        "status": "omit_absent_ldtn_b_request_entry",
        "class": ONECAMERA_ODR_DESCRIPTOR,
        "method": signature,
        "key": "Ltdn.b",
        "preserved_key": "Ltdn.a",
        "additional_nullable_key": "Ltdn.a",
        "fallback": "singleton(Ltdn.a) when only Ltdn.b is absent; emptySet when Ltdn.a is absent",
        "behavior": (
            "omit only the absent Pixel-only Ltdn.b request entry while "
            "preserving the supported Ltdn.a request"
        ),
    }


def find_and_patch_onecamera_odr_missing_request_key_smali_tree(
    root: Path,
) -> dict[str, Any]:
    matches: list[Path] = []
    class_line_re = re.compile(
        r"^\.class\s+.*" + re.escape(ONECAMERA_ODR_DESCRIPTOR) + r"\s*$",
        re.MULTILINE,
    )
    for path in root.rglob("*.smali"):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if class_line_re.search(text):
            matches.append(path)

    if len(matches) != 1:
        rendered = ", ".join(os.fspath(p.relative_to(root)) for p in matches) or "none"
        raise PatchError(
            "expected OneCamera request-entry class Lodr; in exactly one smali file; "
            f"found {rendered}"
        )

    target = matches[0]
    patched, metadata = patch_onecamera_odr_missing_request_key_smali_text(
        target.read_text(encoding="utf-8")
    )
    target.write_text(patched, encoding="utf-8")
    metadata["smali_path"] = os.fspath(target.relative_to(root))
    return metadata


def patch_onecamera_open_camera_fallback_smali_text(
    text: str,
) -> tuple[str, dict[str, Any]]:
    """Remap Xiaomi logical rear ID 4 before OneCamera creates its callback.

    Real-device evidence shows POCO F5 camera ID 4 is Xiaomi's logical rear
    MultiCameraSAT endpoint and cannot initialize reliably for Pixel Camera.
    The physical rear camera is ID 0.

    The remap must happen in Lug.a(), before the camera ID is stored in the
    coroutine state and before Lrr (CameraDevice.StateCallback) is constructed.
    Patching only CameraManager.openCamera is too late: Lrr keeps the original
    ID and its onOpened() invariant rejects CameraDevice.getId() == "0" while
    expecting "4". Remapping v1 here keeps metadata lookup, callback state, and
    the eventual openCamera request consistent.
    """

    lines = text.splitlines()
    class_matches = [
        i for i, line in enumerate(lines)
        if re.match(
            r"^\.class\s+.*" + re.escape(ONECAMERA_OPEN_CAMERA_DESCRIPTOR) + r"\s*$",
            line,
        )
    ]
    if len(class_matches) != 1:
        raise PatchError(
            "expected exactly one OneCamera camera-open coordinator Lug;; "
            f"found {len(class_matches)}"
        )

    signature = (
        ".method public final "
        "a(Ljava/lang/String;IJLsz;Lsb;Ladel;)Ljava/lang/Object;"
    )
    method_starts = [
        i for i, line in enumerate(lines)
        if line.strip() == signature
    ]
    if len(method_starts) != 1:
        raise PatchError(
            "expected exactly one Lug.a(String,...) camera-open coroutine; "
            f"found {len(method_starts)}"
        )
    method_start = method_starts[0]
    method_end = method_start + 1
    while method_end < len(lines) and lines[method_end].strip() != ".end method":
        method_end += 1
    if method_end >= len(lines):
        raise PatchError("Lug.a() is unterminated")

    method_text = "\n".join(lines[method_start : method_end + 1])
    for token in (
        "iput-object v1, v3, Ltz;->g:Ljava/lang/String;",
        "invoke-direct/range {v9 .. v19}, "
        "Lrr;-><init>(Ljava/lang/String;Lpi;IJLjom;Lsz;Lufk;Ldan;Lsb;)V",
        "invoke-direct {v1, v0, v10, v9, v8}, "
        "Luf;-><init>(Lug;Ljava/lang/String;Lrr;Ladel;)V",
    ):
        if method_text.count(token) != 1:
            raise PatchError(
                "Lug camera-ID/callback flow changed; expected exactly one "
                f"{token!r}"
            )

    camera_move = "move-object/from16 v1, p1"
    camera_move_indexes = [
        i for i in range(method_start, min(method_end, method_start + 20))
        if lines[i].strip() == camera_move
    ]
    if len(camera_move_indexes) != 1:
        raise PatchError(
            "expected initial camera ID copy move-object/from16 v1, p1 in Lug.a(); "
            f"found {len(camera_move_indexes)}"
        )
    camera_move_index = camera_move_indexes[0]

    # Verify the exact neighboring register flow so this remains fail-closed.
    previous_code = []
    cursor = camera_move_index - 1
    while cursor > method_start and len(previous_code) < 1:
        stripped = lines[cursor].strip()
        if stripped and not stripped.startswith("#"):
            previous_code.append(stripped)
        cursor -= 1
    next_code = []
    cursor = camera_move_index + 1
    while cursor < method_end and len(next_code) < 1:
        stripped = lines[cursor].strip()
        if stripped and not stripped.startswith("#"):
            next_code.append(stripped)
        cursor += 1
    if previous_code != ["move-object/from16 v0, p0"]:
        raise PatchError("Lug camera ID predecessor register flow changed")
    if next_code != ["move-object/from16 v2, p7"]:
        raise PatchError("Lug camera ID successor register flow changed")

    label = "poco_physical_rear_camera_ready"
    if any(line.strip() == f":{label}" for line in lines[method_start:method_end]):
        raise PatchError("physical rear camera fallback label already exists")

    indent = re.match(r"^(\s*)", lines[camera_move_index]).group(1)
    guard = [
        "",
        f"{indent}# POCO F5: normalize Xiaomi logical rear ID before callback creation.",
        f'{indent}const-string v9, "4"',
        "",
        f"{indent}invoke-virtual {{v9, v1}}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z",
        "",
        f"{indent}move-result v9",
        "",
        f"{indent}if-eqz v9, :{label}",
        "",
        f'{indent}const-string v1, "0"',
        "",
        f"{indent}:{label}",
    ]
    patched_lines = (
        lines[: camera_move_index + 1]
        + guard
        + lines[camera_move_index + 1 :]
    )
    patched = "\n".join(patched_lines) + ("\n" if text.endswith("\n") else "")
    return patched, {
        "status": "redirect_xiaomi_logical_rear_to_physical_rear",
        "class": ONECAMERA_OPEN_CAMERA_DESCRIPTOR,
        "method": signature,
        "requested_camera_id": "4",
        "fallback_camera_id": "0",
        "scope": "before_coroutine_state_and_CameraDevice_StateCallback_creation",
        "callback_expected_id_remapped": True,
        "metadata_lookup_id_remapped": True,
        "other_camera_ids_unchanged": True,
    }


def find_and_patch_onecamera_open_camera_fallback_smali_tree(
    root: Path,
) -> dict[str, Any]:
    matches: list[Path] = []
    class_line_re = re.compile(
        r"^\.class\s+.*"
        + re.escape(ONECAMERA_OPEN_CAMERA_DESCRIPTOR)
        + r"\s*$",
        re.MULTILINE,
    )
    for path in root.rglob("*.smali"):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if class_line_re.search(text):
            matches.append(path)

    if len(matches) != 1:
        rendered = ", ".join(os.fspath(p.relative_to(root)) for p in matches) or "none"
        raise PatchError(
            "expected Lug; in exactly one smali file; "
            f"found {rendered}"
        )

    target = matches[0]
    patched, metadata = patch_onecamera_open_camera_fallback_smali_text(
        target.read_text(encoding="utf-8")
    )
    target.write_text(patched, encoding="utf-8")
    metadata["smali_path"] = os.fspath(target.relative_to(root))
    return metadata


def patch_keepalive_receiver_smali_text(text: str) -> tuple[str, dict[str, Any]]:
    """No-op Pixel Camera's keepalive broadcast receiver on POCO F5.

    Android 17 rejects the receiver's background startService() call and throws
    BackgroundServiceStartNotAllowedException before the camera activity can
    stay alive. The receiver only drives Pixel keepalive/prewarm behavior, so
    this compatibility patch leaves the receiver class registered but makes its
    onReceive(Context, Intent) implementation return immediately.
    """

    lines = text.splitlines()
    method_indexes = [
        i
        for i, line in enumerate(lines)
        if line.lstrip().startswith(".method ")
        and KEEPALIVE_ON_RECEIVE_DESCRIPTOR in line
    ]
    if len(method_indexes) != 1:
        raise PatchError(
            "expected exactly one KeepAliveBroadcastReceiver.onReceive method; "
            f"found {len(method_indexes)}"
        )

    method_start = method_indexes[0]
    method_end = method_start + 1
    while method_end < len(lines) and lines[method_end].strip() != ".end method":
        method_end += 1
    if method_end >= len(lines):
        raise PatchError("KeepAliveBroadcastReceiver.onReceive is unterminated")

    body = "\n".join(lines[method_start : method_end + 1])
    if "->startService(Landroid/content/Intent;)Landroid/content/ComponentName;" not in body:
        raise PatchError(
            "KeepAliveBroadcastReceiver.onReceive no longer contains the expected "
            "background startService call"
        )

    register_indexes = [
        i
        for i in range(method_start + 1, method_end)
        if re.match(r"^\s*\.(?:locals|registers)\s+\d+\s*$", lines[i])
    ]
    if len(register_indexes) != 1:
        raise PatchError(
            "expected exactly one register declaration in "
            "KeepAliveBroadcastReceiver.onReceive"
        )

    register_index = register_indexes[0]
    indent = re.match(r"^(\s*)", lines[register_index]).group(1)
    patched_lines = (
        lines[: register_index + 1]
        + [
            "",
            f"{indent}# POCO F5 / Android 17: skip Pixel keepalive background service.",
            f"{indent}return-void",
        ]
        + lines[method_end:]
    )
    patched = "\n".join(patched_lines) + ("\n" if text.endswith("\n") else "")
    return patched, {
        "status": "disabled_pixel_keepalive_receiver",
        "method": lines[method_start].strip(),
        "reason": "Android 17 forbids this background startService path",
    }


def find_and_patch_keepalive_receiver_smali_tree(root: Path) -> dict[str, Any]:
    matches: list[Path] = []
    class_line_re = re.compile(
        r"^\.class\s+.*"
        + re.escape(KEEPALIVE_RECEIVER_DESCRIPTOR)
        + r"\s*$",
        re.MULTILINE,
    )
    for path in root.rglob("*.smali"):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if class_line_re.search(text):
            matches.append(path)

    if len(matches) != 1:
        rendered = ", ".join(os.fspath(p.relative_to(root)) for p in matches) or "none"
        raise PatchError(
            "expected KeepAliveBroadcastReceiver class in exactly one smali file; "
            f"found {rendered}"
        )

    target = matches[0]
    original = target.read_text(encoding="utf-8")
    patched, metadata = patch_keepalive_receiver_smali_text(original)
    target.write_text(patched, encoding="utf-8")
    metadata["smali_path"] = os.fspath(target.relative_to(root))
    return metadata


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
    keepalive_dex = find_keepalive_receiver_dex(source)

    with tempfile.TemporaryDirectory(prefix="poco-f5-device-gate-") as temp:
        root = Path(temp)
        replacements: dict[str, Path] = {}
        dex_reports: dict[str, dict[str, Any]] = {}
        command_tails: dict[str, dict[str, str]] = {}

        with zipfile.ZipFile(source, "r") as archive:
            for dex_name in sorted({target_dex, gcam_init_dex, keepalive_dex}):
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
                    report_for_dex["onecamera_optional"] = (
                        find_and_patch_onecamera_optional_key_smali_tree(smali_dir)
                    )
                    report_for_dex["onecamera_missing_request_key"] = (
                        find_and_patch_onecamera_missing_request_key_smali_tree(smali_dir)
                    )
                    report_for_dex["onecamera_open_camera_fallback"] = (
                        find_and_patch_onecamera_open_camera_fallback_smali_tree(smali_dir)
                    )
                    report_for_dex["onecamera_odr_missing_request_key"] = (
                        find_and_patch_onecamera_odr_missing_request_key_smali_tree(smali_dir)
                    )
                if dex_name == keepalive_dex:
                    report_for_dex["keepalive"] = (
                        find_and_patch_keepalive_receiver_smali_tree(smali_dir)
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
                    tail = "\n".join(assemble_output.splitlines()[-80:])
                    raise PatchError(
                        f"smali did not produce rebuilt dex: {dex_name}\n{tail}"
                    )
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
    onecamera_optional_metadata = dex_reports[gcam_init_dex]["onecamera_optional"]
    onecamera_missing_request_key_metadata = dex_reports[gcam_init_dex][
        "onecamera_missing_request_key"
    ]
    onecamera_open_camera_fallback_metadata = dex_reports[gcam_init_dex][
        "onecamera_open_camera_fallback"
    ]
    onecamera_odr_missing_request_key_metadata = dex_reports[gcam_init_dex][
        "onecamera_odr_missing_request_key"
    ]
    keepalive_metadata = dex_reports[keepalive_dex]["keepalive"]

    return {
        "status": "patched",
        "marker": MARKER,
        "target_dex": target_dex,
        **device_metadata,
        "gcam_init_patch": {
            "target_dex": gcam_init_dex,
            **gcam_metadata,
        },
        "onecamera_optional_key_patch": {
            "target_dex": gcam_init_dex,
            **onecamera_optional_metadata,
        },
        "onecamera_missing_request_key_patch": {
            "target_dex": gcam_init_dex,
            **onecamera_missing_request_key_metadata,
        },
        "onecamera_open_camera_fallback_patch": {
            "target_dex": gcam_init_dex,
            **onecamera_open_camera_fallback_metadata,
        },
        "onecamera_odr_missing_request_key_patch": {
            "target_dex": gcam_init_dex,
            **onecamera_odr_missing_request_key_metadata,
        },
        "keepalive_receiver_patch": {
            "target_dex": keepalive_dex,
            **keepalive_metadata,
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
