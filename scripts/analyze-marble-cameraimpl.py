#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
from pathlib import Path

SHT_SYMTAB = 2
SHT_DYNAMIC = 6
SHT_DYNSYM = 11
DT_NULL = 0
DT_NEEDED = 1
DT_SONAME = 14
STB_GLOBAL = 1
STB_WEAK = 2
STT_FUNC = 2
SHN_UNDEF = 0

CANDIDATE_PATTERNS = [
    re.compile(p, re.I)
    for p in (
        r"(^|::|_)create($|[^a-z])",
        r"hookModuleInit",
        r"splitClientPackageActivityName",
        r"setClientActivityName",
        r"setClientPackageName",
        r"parseCustomizedJsonData",
        r"initializeDeviceInfo",
        r"updateSessionParams",
        r"createCustomDefaultRequest",
        r"executeSceneIdentify",
        r"detachSceneIdentify",
        r"notifyRequestSubmit",
        r"notifyCancelRequest",
        r"destroy",
        r"getCustomBestSize",
        r"isPrivilegedClient",
    )
]

INTERESTING_STRING_PATTERNS = [
    re.compile(p, re.I)
    for p in (
        r"com\\.xiaomi\\.sessionparams",
        r"MiStreamUsecase",
        r"clientName",
        r"activityName",
        r"CameraImpl",
        r"SceneIdentify",
        r"thirdparty",
        r"mivi",
        r"camera.*role",
        r"camerax",
    )
]


def cstr(blob: bytes, offset: int) -> str:
    if offset < 0 or offset >= len(blob):
        return ""
    end = blob.find(b"\\0", offset)
    if end < 0:
        end = len(blob)
    return blob[offset:end].decode("utf-8", errors="replace")


def printable_strings(data: bytes, minimum: int = 5):
    out = []
    start = None
    for i, b in enumerate(data):
        if 32 <= b <= 126:
            if start is None:
                start = i
        else:
            if start is not None and i - start >= minimum:
                out.append(data[start:i].decode("ascii", errors="replace"))
            start = None
    if start is not None and len(data) - start >= minimum:
        out.append(data[start:].decode("ascii", errors="replace"))
    return out


def parse_elf(path: Path):
    data = path.read_bytes()
    if data[:4] != b"\\x7fELF":
        raise ValueError("Not an ELF file")

    elf_class = data[4]
    endian_id = data[5]
    if elf_class not in (1, 2):
        raise ValueError(f"Unsupported ELF class: {elf_class}")
    if endian_id not in (1, 2):
        raise ValueError(f"Unsupported ELF endianness: {endian_id}")

    endian = "<" if endian_id == 1 else ">"
    is64 = elf_class == 2

    if is64:
        hdr_fmt = endian + "16sHHIQQQIHHHHHH"
        sh_fmt = endian + "IIQQQQIIQQ"
        sym_fmt = endian + "IBBHQQ"
        dyn_fmt = endian + "qQ"
    else:
        hdr_fmt = endian + "16sHHIIIIIHHHHHH"
        sh_fmt = endian + "IIIIIIIIII"
        sym_fmt = endian + "IIIBBH"
        dyn_fmt = endian + "iI"

    hdr = struct.unpack_from(hdr_fmt, data, 0)
    (_, e_type, e_machine, _e_version, _e_entry, _e_phoff, e_shoff,
     _e_flags, _e_ehsize, _e_phentsize, _e_phnum, e_shentsize,
     e_shnum, e_shstrndx) = hdr

    sh_calc = struct.calcsize(sh_fmt)
    if e_shentsize < sh_calc:
        raise ValueError("Section header entry is smaller than expected")

    raw_sections = []
    for idx in range(e_shnum):
        off = e_shoff + idx * e_shentsize
        values = struct.unpack_from(sh_fmt, data, off)
        (name, typ, flags, addr, offset, size, link, info, addralign, entsize) = values
        raw_sections.append({
            "index": idx,
            "name_off": name,
            "type": typ,
            "flags": flags,
            "addr": addr,
            "offset": offset,
            "size": size,
            "link": link,
            "info": info,
            "addralign": addralign,
            "entsize": entsize,
        })

    if e_shstrndx >= len(raw_sections):
        raise ValueError("Invalid section-name string table index")

    shstr = raw_sections[e_shstrndx]
    shstr_blob = data[shstr["offset"]:shstr["offset"] + shstr["size"]]
    for section in raw_sections:
        section["name"] = cstr(shstr_blob, section["name_off"])

    dyn_symbols = []
    undefined = []
    for section in raw_sections:
        if section["type"] not in (SHT_DYNSYM, SHT_SYMTAB) or section["entsize"] == 0:
            continue
        if section["link"] >= len(raw_sections):
            continue

        strsec = raw_sections[section["link"]]
        strtab = data[strsec["offset"]:strsec["offset"] + strsec["size"]]
        count = section["size"] // section["entsize"]

        for i in range(count):
            off = section["offset"] + i * section["entsize"]
            vals = struct.unpack_from(sym_fmt, data, off)
            if is64:
                st_name, st_info, st_other, st_shndx, st_value, st_size = vals
            else:
                st_name, st_value, st_size, st_info, st_other, st_shndx = vals

            name = cstr(strtab, st_name)
            if not name:
                continue

            binding = st_info >> 4
            symbol_type = st_info & 0xF
            item = {
                "name": name,
                "binding": binding,
                "type": symbol_type,
                "sectionIndex": st_shndx,
                "value": st_value,
                "size": st_size,
                "sourceSection": section["name"],
            }
            if section["type"] == SHT_DYNSYM:
                dyn_symbols.append(item)
            if st_shndx == SHN_UNDEF:
                undefined.append(item)

    needed = []
    soname = None
    for section in raw_sections:
        if section["type"] != SHT_DYNAMIC or section["entsize"] == 0:
            continue
        if section["link"] >= len(raw_sections):
            continue

        strsec = raw_sections[section["link"]]
        strtab = data[strsec["offset"]:strsec["offset"] + strsec["size"]]
        count = section["size"] // section["entsize"]

        for i in range(count):
            off = section["offset"] + i * section["entsize"]
            tag, value = struct.unpack_from(dyn_fmt, data, off)
            if tag == DT_NULL:
                break
            if tag == DT_NEEDED:
                needed.append(cstr(strtab, value))
            elif tag == DT_SONAME:
                soname = cstr(strtab, value)

    exported = [
        symbol for symbol in dyn_symbols
        if symbol["sectionIndex"] != SHN_UNDEF
        and symbol["binding"] in (STB_GLOBAL, STB_WEAK)
    ]
    exported_functions = [
        symbol for symbol in exported
        if symbol["type"] == STT_FUNC
    ]
    candidate_symbols = [
        symbol for symbol in exported
        if any(pattern.search(symbol["name"]) for pattern in CANDIDATE_PATTERNS)
    ]

    strings = printable_strings(data)
    interesting_strings = sorted({
        value for value in strings
        if any(pattern.search(value) for pattern in INTERESTING_STRING_PATTERNS)
    })

    expected_logical_names = [
        "create",
        "hookModuleInit",
        "splitClientPackageActivityName",
        "setClientActivityName",
        "setClientPackageName",
        "parseCustomizedJsonData",
        "initializeDeviceInfo",
        "updateSessionParams",
        "createCustomDefaultRequest",
        "executeSceneIdentify",
        "detachSceneIdentify",
        "notifyRequestSubmit",
        "notifyCancelRequest",
        "destroy",
        "getCustomBestSize",
        "isPrivilegedClient",
    ]

    coverage = {}
    for logical_name in expected_logical_names:
        coverage[logical_name] = [
            symbol["name"] for symbol in exported
            if logical_name in symbol["name"]
        ]

    def has_hook(name: str) -> bool:
        return bool(coverage.get(name))

    legacy_core = all(
        has_hook(name)
        for name in (
            "create",
            "setClientPackageName",
            "updateSessionParams",
            "createCustomDefaultRequest",
        )
    )
    modern_scene = legacy_core and all(
        has_hook(name)
        for name in (
            "executeSceneIdentify",
            "detachSceneIdentify",
        )
    )
    lifecycle_registration = all(
        has_hook(name)
        for name in (
            "splitClientPackageActivityName",
            "setClientActivityName",
            "parseCustomizedJsonData",
        )
    )

    if modern_scene:
        recommended_integration = "cameraimpl_scene_hook"
    elif legacy_core:
        recommended_integration = "camera_stub_legacy_core"
    else:
        recommended_integration = "no_direct_cameraimpl_hook"

    return {
        "schemaVersion": 1,
        "file": {
            "path": str(path),
            "sizeBytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        },
        "elf": {
            "class": "ELF64" if is64 else "ELF32",
            "endianness": "little" if endian_id == 1 else "big",
            "type": e_type,
            "machine": e_machine,
            "sectionCount": e_shnum,
            "sectionNames": [
                section["name"] for section in raw_sections
                if section["name"]
            ],
        },
        "dynamic": {
            "soname": soname,
            "needed": needed,
        },
        "integrationAssessment": {
            "legacyCameraStubCore": legacy_core,
            "modernSceneIdentification": modern_scene,
            "clientLifecycleRegistration": lifecycle_registration,
            "recommendedIntegration": recommended_integration,
            "warning": (
                "Symbol presence is ABI evidence only; method signatures and "
                "runtime linker/SELinux access still require device validation."
            ),
        },
        "symbols": {
            "dynamicCount": len(dyn_symbols),
            "exportedCount": len(exported),
            "exportedFunctionCount": len(exported_functions),
            "undefinedCount": len(undefined),
            "candidateExports": candidate_symbols,
            "expectedHookCoverage": coverage,
        },
        "strings": {
            "interesting": interesting_strings[:500],
            "interestingCount": len(interesting_strings),
        },
    }


def main():
    parser = argparse.ArgumentParser(
        description="Inspect Xiaomi libcameraimpl.so without external ELF tools."
    )
    parser.add_argument("library", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    report = parse_elf(args.library)
    payload = json.dumps(
        report,
        indent=2 if args.pretty else None,
        ensure_ascii=False,
    )

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")

    print(payload)


if __name__ == "__main__":
    main()
