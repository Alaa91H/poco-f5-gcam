#!/usr/bin/env python3
"""Patch Pixel Camera's explicit unsupported-device guard for POCO F5 testing.

The patch is intentionally narrow:
- preserve the original split layout;
- modify only classes2.dex in base.apk;
- replace the single conditional branch that jumps to the known
  UnsupportedOperationException block with a nop;
- zipalign and re-sign every APK with one project/test certificate so Android
  accepts the split set as one package.

This does not remove PairIP or other application protections.
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

DEVICE_GATE_TEXT = "Device is not recognized or not supported"
TARGET_CLASS = "klm.smali"
TARGET_METHOD = ".method public constructor <init>(Luyv;Luyu;Lqxe;Lacku;Lklk;)V"
REPLACEMENT_BRANCH = "nop"
SIGNER_RE = re.compile(r"Signer #\d+ certificate SHA-256 digest:\s*([0-9A-Fa-f:]+)")


class PatchError(RuntimeError):
    pass


def run(command: list[str], *, env: dict[str, str] | None = None) -> str:
    proc = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=env,
    )
    if proc.returncode != 0:
        raise PatchError(
            f"command failed ({proc.returncode}): {' '.join(command)}\n{proc.stdout}"
        )
    return proc.stdout


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_bundle(package_path: Path, output_dir: Path) -> list[Path]:
    suffix = package_path.suffix.lower()
    if suffix == ".apk":
        target = output_dir / "base.apk"
        shutil.copy2(package_path, target)
        return [target]

    if suffix not in {".apkm", ".apks", ".xapk", ".zip"}:
        raise PatchError(f"unsupported package type: {suffix}")

    apk_paths: list[Path] = []
    with zipfile.ZipFile(package_path) as archive:
        for entry in archive.infolist():
            if not entry.filename.lower().endswith(".apk"):
                continue
            name = Path(entry.filename).name
            if not name:
                continue
            target = output_dir / name
            if target.exists():
                raise PatchError(f"duplicate APK filename in bundle: {name}")
            with archive.open(entry) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            apk_paths.append(target)

    if not apk_paths:
        raise PatchError("bundle contains no APK entries")
    bases = [path for path in apk_paths if path.name.lower() == "base.apk"]
    if len(bases) != 1:
        raise PatchError(f"expected exactly one base.apk, found {len(bases)}")
    return sorted(apk_paths, key=lambda path: (path.name.lower() != "base.apk", path.name))


def replace_zip_entry(source_apk: Path, output_apk: Path, entry_name: str, replacement: Path) -> None:
    found = False
    with zipfile.ZipFile(source_apk, "r") as src, zipfile.ZipFile(output_apk, "w", allowZip64=True) as dst:
        for info in src.infolist():
            if info.filename.upper().startswith("META-INF/") and (
                info.filename.upper().endswith(".RSA")
                or info.filename.upper().endswith(".DSA")
                or info.filename.upper().endswith(".EC")
                or info.filename.upper().endswith(".SF")
                or info.filename.upper().endswith("MANIFEST.MF")
            ):
                continue

            data = replacement.read_bytes() if info.filename == entry_name else src.read(info.filename)
            if info.filename == entry_name:
                found = True

            cloned = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            cloned.compress_type = info.compress_type
            cloned.comment = info.comment
            cloned.extra = info.extra
            cloned.internal_attr = info.internal_attr
            cloned.external_attr = info.external_attr
            cloned.create_system = info.create_system
            cloned.flag_bits = info.flag_bits
            dst.writestr(cloned, data)

    if not found:
        raise PatchError(f"{entry_name} was not found in {source_apk.name}")


def patch_smali(smali_root: Path) -> dict[str, Any]:
    candidates = list(smali_root.rglob(TARGET_CLASS))
    if len(candidates) != 1:
        raise PatchError(f"expected one {TARGET_CLASS}, found {len(candidates)}")

    path = candidates[0]
    text = path.read_text(encoding="utf-8")
    if DEVICE_GATE_TEXT not in text:
        raise PatchError("target class no longer contains the known device-gate text")

    method_start = text.find(TARGET_METHOD)
    if method_start < 0:
        raise PatchError("known constructor signature was not found")
    method_end = text.find(".end method", method_start)
    if method_end < 0:
        raise PatchError("known constructor is not terminated")
    method_end += len(".end method")
    method = text[method_start:method_end]

    if method.count(DEVICE_GATE_TEXT) != 1:
        raise PatchError("expected exactly one unsupported-device marker in target constructor")

    marker_index = method.index(DEVICE_GATE_TEXT)

    # baksmali is free to renumber :cond_* labels, so discover the throw label
    # from the exception block itself instead of pinning a numeric label.
    before_marker = method[:marker_index]
    label_matches = list(
        re.finditer(
            r"(?m)^\s*(:[A-Za-z0-9_]+)\s*$"
            r"(?:(?!^\s*:[A-Za-z0-9_]+\s*$).)*?"
            r"^\s*new-instance\s+\w+,\s+Ljava/lang/UnsupportedOperationException;\s*$"
            r"(?:(?!^\s*:[A-Za-z0-9_]+\s*$).)*?$",
            before_marker,
            re.DOTALL,
        )
    )
    if not label_matches:
        raise PatchError("could not locate unsupported-device exception label")
    throw_label = label_matches[-1].group(1)

    # Locate the recognition call and the conditional branch that targets the
    # discovered exception label. This binds the patch to semantics rather than
    # label numbering.
    guard_re = re.compile(
        r"(?m)^(?P<indent>\s*)invoke-virtual\s+\{p1\},\s+Luyv;->p\(\)Z\s*$"
        r"\n\s*move-result\s+p1\s*$"
        r"\n(?P<branch_indent>\s*)if-eqz\s+p1,\s+"
        + re.escape(throw_label)
        + r"\s*$"
    )
    guards = list(guard_re.finditer(method[:marker_index]))
    if len(guards) != 1:
        raise PatchError(
            f"expected one device-recognition branch to {throw_label}, found {len(guards)}"
        )

    guard = guards[0]
    original_instruction = f"if-eqz p1, {throw_label}"
    branch_line_re = re.compile(
        r"(?m)^(?P<indent>\s*)if-eqz\s+p1,\s+"
        + re.escape(throw_label)
        + r"\s*$"
    )
    patched_method, count = branch_line_re.subn(
        lambda match: match.group("indent") + REPLACEMENT_BRANCH,
        method,
        count=1,
    )
    if count != 1:
        raise PatchError("failed to replace the recognized device-gate branch")

    patched_text = text[:method_start] + patched_method + text[method_end:]
    path.write_text(patched_text, encoding="utf-8")

    return {
        "smali_file": str(path.relative_to(smali_root)).replace("\\", "/"),
        "method": TARGET_METHOD,
        "throw_label": throw_label,
        "original_instruction": original_instruction,
        "patched_instruction": REPLACEMENT_BRANCH,
        "device_gate_text": DEVICE_GATE_TEXT,
    }


def signer_digests(apksigner_output: str) -> list[str]:
    result = []
    for match in SIGNER_RE.findall(apksigner_output):
        digest = match.replace(":", "").lower()
        if digest not in result:
            result.append(digest)
    return result


def sign_apk(
    input_apk: Path,
    output_apk: Path,
    *,
    zipalign: Path,
    apksigner: Path,
    keystore: Path,
    key_alias: str,
    ks_pass_env: str,
    key_pass_env: str,
    work_dir: Path,
) -> str:
    aligned = work_dir / f"{input_apk.stem}.aligned.apk"
    run([str(zipalign), "-f", "-P", "16", "4", str(input_apk), str(aligned)])

    env = os.environ.copy()
    if ks_pass_env not in env or key_pass_env not in env:
        raise PatchError("signing password environment variables are not set")

    run(
        [
            str(apksigner),
            "sign",
            "--ks",
            str(keystore),
            "--ks-key-alias",
            key_alias,
            "--ks-pass",
            f"env:{ks_pass_env}",
            "--key-pass",
            f"env:{key_pass_env}",
            "--out",
            str(output_apk),
            str(aligned),
        ],
        env=env,
    )
    verify = run([str(apksigner), "verify", "--verbose", "--print-certs", str(output_apk)])
    digests = signer_digests(verify)
    if len(digests) != 1:
        raise PatchError(f"expected exactly one signer for {output_apk.name}, got {digests}")
    return digests[0]


def build_patched_bundle(
    package_path: Path,
    output_path: Path,
    *,
    baksmali_jar: Path,
    smali_jar: Path,
    zipalign: Path,
    apksigner: Path,
    keystore: Path,
    key_alias: str,
    ks_pass_env: str,
    key_pass_env: str,
    report_path: Path,
) -> dict[str, Any]:
    for required in (package_path, baksmali_jar, smali_jar, zipalign, apksigner, keystore):
        if not required.is_file():
            raise PatchError(f"required file does not exist: {required}")

    source_sha256 = sha256_file(package_path)

    with tempfile.TemporaryDirectory(prefix="pixel-camera-patch-") as temp:
        work = Path(temp)
        extracted = work / "apks"
        extracted.mkdir()
        apk_paths = extract_bundle(package_path, extracted)
        base = next(path for path in apk_paths if path.name.lower() == "base.apk")

        dex = work / "classes2.dex"
        with zipfile.ZipFile(base) as archive:
            try:
                dex.write_bytes(archive.read("classes2.dex"))
            except KeyError as exc:
                raise PatchError("base.apk does not contain classes2.dex") from exc

        smali_root = work / "smali_classes2"
        run(
            [
                "java",
                "-Xmx4g",
                "-jar",
                str(baksmali_jar),
                "disassemble",
                str(dex),
                "--output",
                str(smali_root),
            ]
        )
        patch = patch_smali(smali_root)

        rebuilt_dex = work / "classes2.patched.dex"
        run(
            [
                "java",
                "-Xmx4g",
                "-jar",
                str(smali_jar),
                "assemble",
                str(smali_root),
                "--output",
                str(rebuilt_dex),
            ]
        )

        patched_base = work / "base.patched-unsigned.apk"
        replace_zip_entry(base, patched_base, "classes2.dex", rebuilt_dex)

        signed_dir = work / "signed"
        signed_dir.mkdir()
        signer: str | None = None
        signed_apks: list[Path] = []

        for source in apk_paths:
            input_for_signing = patched_base if source.name.lower() == "base.apk" else source
            signed = signed_dir / source.name
            current_signer = sign_apk(
                input_for_signing,
                signed,
                zipalign=zipalign,
                apksigner=apksigner,
                keystore=keystore,
                key_alias=key_alias,
                ks_pass_env=ks_pass_env,
                key_pass_env=key_pass_env,
                work_dir=work,
            )
            if signer is None:
                signer = current_signer
            elif current_signer != signer:
                raise PatchError(
                    f"split signer mismatch: expected {signer}, got {current_signer} for {source.name}"
                )
            signed_apks.append(signed)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            for signed in signed_apks:
                archive.write(signed, signed.name)

        report = {
            "schema_version": 1,
            "source": {
                "path": str(package_path),
                "sha256": source_sha256,
                "apk_count": len(apk_paths),
            },
            "output": {
                "path": str(output_path),
                "sha256": sha256_file(output_path),
                "apk_count": len(signed_apks),
                "signer_sha256": signer,
            },
            "patch": {
                **patch,
                "modified_apks": ["base.apk"],
                "modified_dex_entries": ["classes2.dex"],
                "split_layout_preserved": True,
                "device_compatibility_gate_patched": True,
                "pairip_bypass_performed": False,
            },
            "runtime_validation": {
                "status": "required",
                "target": "POCO F5 (marble/marblein), Android 17 / API 37",
                "script": "scripts/test-pixel-camera-runtime.ps1",
            },
        }

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--baksmali", type=Path, required=True)
    parser.add_argument("--smali", type=Path, required=True)
    parser.add_argument("--zipalign", type=Path, required=True)
    parser.add_argument("--apksigner", type=Path, required=True)
    parser.add_argument("--keystore", type=Path, required=True)
    parser.add_argument("--key-alias", required=True)
    parser.add_argument("--ks-pass-env", default="GCMOD_KEYSTORE_PASSWORD")
    parser.add_argument("--key-pass-env", default="GCMOD_KEY_PASSWORD")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        report = build_patched_bundle(
            args.package,
            args.output,
            baksmali_jar=args.baksmali,
            smali_jar=args.smali,
            zipalign=args.zipalign,
            apksigner=args.apksigner,
            keystore=args.keystore,
            key_alias=args.key_alias,
            ks_pass_env=args.ks_pass_env,
            key_pass_env=args.key_pass_env,
            report_path=args.report,
        )
        if args.json:
            print(json.dumps(report))
        else:
            print(f"Patched split bundle: {args.output}")
            print(f"Signer SHA-256: {report['output']['signer_sha256']}")
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
