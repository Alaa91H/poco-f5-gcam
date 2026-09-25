#!/usr/bin/env python3
"""Build a conservative split-dependency plan from an APKM inspection report.

This tool never edits an APKM and never labels a split as safe to remove.
Its strongest output is "requires-device-validation": a candidate for controlled
POCO F5 A/B testing using the original Google-signed APK files.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


def build_plan(report: dict[str, Any]) -> dict[str, Any]:
    apks = report.get("apks")
    if not isinstance(apks, list) or not apks:
        raise ValueError("inspection report contains no APK entries")

    entries: list[dict[str, Any]] = []
    by_split: dict[str, dict[str, Any]] = {}
    used_by: dict[str, list[str]] = defaultdict(list)
    config_children: dict[str, list[str]] = defaultdict(list)
    manifest_complete = True

    for apk in apks:
        manifest = apk.get("manifest") or {}
        manifest_ok = (
            manifest.get("available") is True
            and not manifest.get("error")
        )
        if not manifest_ok:
            manifest_complete = False

        split = manifest.get("split")
        uses_splits = sorted(set(manifest.get("uses_splits") or []))
        config_for = manifest.get("config_for_split")
        entry = {
            "apk_name": apk.get("name"),
            "file_size_bytes": int(apk.get("file_size_bytes") or 0),
            "manifest_available": manifest_ok,
            "package": manifest.get("package"),
            "split": split,
            "uses_splits": uses_splits,
            "config_for_split": config_for,
            "is_feature_split": manifest.get("is_feature_split"),
        }
        entries.append(entry)

        if split:
            if split in by_split:
                raise ValueError(f"duplicate split name in report: {split}")
            by_split[split] = entry

        for dependency in uses_splits:
            used_by[dependency].append(split or "<base>")
        if split and config_for:
            config_children[config_for].append(split)

    known_splits = set(by_split)
    missing_dependencies: list[dict[str, str]] = []
    for entry in entries:
        owner = entry["split"] or "<base>"
        for dependency in entry["uses_splits"]:
            if dependency not in known_splits:
                missing_dependencies.append({
                    "from": owner,
                    "missing": dependency,
                    "kind": "uses-split",
                })
        config_for = entry["config_for_split"]
        if config_for and config_for not in known_splits:
            missing_dependencies.append({
                "from": owner,
                "missing": config_for,
                "kind": "configForSplit",
            })

    validation_pool_bytes = 0
    status_counts: dict[str, int] = defaultdict(int)
    planned: list[dict[str, Any]] = []

    for entry in entries:
        split = entry["split"]
        status: str
        reason: str

        if not entry["manifest_available"]:
            status = "unknown-manifest"
            reason = "Manifest metadata could not be decoded; keep until inspected."
        elif not split:
            status = "keep-base"
            reason = "Base APK is required."
        elif entry["config_for_split"]:
            status = "keep-with-parent"
            reason = (
                "Configuration split is structurally attached to "
                f"{entry['config_for_split']}."
            )
        elif used_by.get(split):
            status = "keep-dependency"
            reason = (
                "Another installed split declares a dependency on this split: "
                + ", ".join(sorted(used_by[split]))
            )
        else:
            status = "requires-device-validation"
            reason = (
                "No structural dependency found in decoded manifests. "
                "This is not proof that the feature is optional on POCO F5."
            )
            validation_pool_bytes += entry["file_size_bytes"]

        status_counts[status] += 1
        planned.append({
            **entry,
            "status": status,
            "reason": reason,
            "used_by": sorted(used_by.get(split, [])) if split else [],
            "configuration_children": sorted(config_children.get(split, []))
            if split
            else [],
        })

    validation_candidates = sorted(
        (
            {
                "split": item["split"],
                "apk_name": item["apk_name"],
                "file_size_bytes": item["file_size_bytes"],
                "configuration_children": item["configuration_children"],
            }
            for item in planned
            if item["status"] == "requires-device-validation"
        ),
        key=lambda item: item["file_size_bytes"],
        reverse=True,
    )

    return {
        "schema_version": 1,
        "mode": "report-only",
        "source_package_sha256": (report.get("input") or {}).get("sha256"),
        "manifest_metadata_complete": manifest_complete,
        "missing_dependency_count": len(missing_dependencies),
        "missing_dependencies": missing_dependencies,
        "status_counts": dict(sorted(status_counts.items())),
        "validation_candidate_bytes": validation_pool_bytes,
        "warning": (
            "validation_candidate_bytes is not safe savings. No split may be "
            "removed until on-device A/B testing proves that all working POCO F5 "
            "features remain available with the original Google-signed APK set."
        ),
        "validation_candidates": validation_candidates,
        "splits": sorted(
            planned,
            key=lambda item: (
                item["status"],
                -(item["file_size_bytes"]),
                item["apk_name"] or "",
            ),
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("audit_report", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    try:
        report = json.loads(args.audit_report.read_text(encoding="utf-8"))
        plan = build_plan(report)
        payload = json.dumps(plan, indent=2, ensure_ascii=False) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(payload, encoding="utf-8")
        print(payload, end="")

        if not plan["manifest_metadata_complete"]:
            print(
                "warning: one or more APK manifests were unavailable; "
                "the plan remains conservative",
                file=sys.stderr,
            )
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
