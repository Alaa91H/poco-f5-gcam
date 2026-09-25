#!/usr/bin/env python3
"""Promote or reject Pixel Camera runtime candidates for POCO F5.

The discovery lock may change daily. The approved state changes only after a
candidate passes the configured on-device compatibility gate.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _load(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _write(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _candidate_list(lock: dict[str, object]) -> list[dict[str, object]]:
    items = lock.get("candidates")
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]

    selected = lock.get("selected")
    return [selected] if isinstance(selected, dict) else []


def find_candidate(
    lock: dict[str, object], version: str
) -> dict[str, object]:
    for item in _candidate_list(lock):
        if str(item.get("version")) == version:
            return item
    raise ValueError(
        f"Candidate version {version!r} is not present in the current discovery lock"
    )


def required_checks(policy: dict[str, object]) -> list[str]:
    validation = policy.get("validation")
    if not isinstance(validation, dict):
        raise ValueError("policy.validation must be an object")
    checks = validation.get("required_checks")
    if not isinstance(checks, list) or not checks:
        raise ValueError("policy.validation.required_checks must be a non-empty list")
    return [str(item) for item in checks]


def failed_checks(
    policy: dict[str, object], result: dict[str, object]
) -> list[str]:
    checks = result.get("checks")
    if not isinstance(checks, dict):
        return required_checks(policy)

    failed: list[str] = []
    for name in required_checks(policy):
        entry = checks.get(name)
        if not isinstance(entry, dict) or entry.get("status") != "pass":
            failed.append(name)
    return failed


def _rejected_versions(approved: dict[str, object]) -> set[str]:
    rejected = approved.get("rejected")
    if not isinstance(rejected, list):
        return set()
    return {
        str(item.get("version"))
        for item in rejected
        if isinstance(item, dict) and item.get("version")
    }


def choose_next_candidate(
    lock: dict[str, object],
    approved: dict[str, object],
    just_failed_version: str | None = None,
) -> dict[str, object] | None:
    rejected = _rejected_versions(approved)
    if just_failed_version:
        rejected.add(just_failed_version)

    effective = approved.get("last_known_good")
    effective_version = (
        str(effective.get("version"))
        if isinstance(effective, dict) and effective.get("version")
        else None
    )

    for item in _candidate_list(lock):
        version = str(item.get("version", ""))
        if not version or version in rejected or version == effective_version:
            continue
        return item
    return None


def _upsert_rejection(
    approved: dict[str, object],
    candidate: dict[str, object],
    failed: list[str],
    result: dict[str, object],
) -> list[dict[str, object]]:
    current = approved.get("rejected")
    rejected = [
        item for item in current
        if isinstance(item, dict)
        and str(item.get("version")) != str(candidate.get("version"))
    ] if isinstance(current, list) else []

    rejected.append(
        {
            "version": candidate.get("version"),
            "release_url": candidate.get("release_url"),
            "failed_checks": failed,
            "validated_at": result.get("validated_at") or _now(),
        }
    )
    return rejected[-20:]


def evaluate(
    policy_path: Path,
    lock_path: Path,
    result_path: Path,
    approved_path: Path,
) -> tuple[bool, dict[str, object]]:
    policy = _load(policy_path)
    lock = _load(lock_path)
    result = _load(result_path)

    version = str(result.get("candidate_version", ""))
    if not version:
        raise ValueError("validation result is missing candidate_version")

    candidate = find_candidate(lock, version)

    expected_package = (
        policy.get("compatibility", {}).get("package_name")
        if isinstance(policy.get("compatibility"), dict)
        else None
    )
    if expected_package and result.get("package_name") != expected_package:
        raise ValueError(
            "validation result package_name does not match policy.compatibility.package_name"
        )

    approved = (
        _load(approved_path)
        if approved_path.exists()
        else {
            "schema_version": 1,
            "status": "unvalidated",
            "last_known_good": None,
            "rejected": [],
        }
    )

    failed = failed_checks(policy, result)
    timestamp = str(result.get("validated_at") or _now())

    if not failed:
        rejected = [
            item for item in approved.get("rejected", [])
            if isinstance(item, dict)
            and str(item.get("version")) != version
        ]
        new_state: dict[str, object] = {
            "schema_version": 1,
            "status": "approved",
            "effective": candidate,
            "last_known_good": candidate,
            "approved_at": timestamp,
            "validation_result": str(result_path),
            "rejected": rejected[-20:],
            "next_candidate": choose_next_candidate(lock, approved),
        }
        _write(approved_path, new_state)
        return True, new_state

    approved["schema_version"] = 1
    approved["rejected"] = _upsert_rejection(
        approved, candidate, failed, result
    )

    last_known_good = approved.get("last_known_good")
    if isinstance(last_known_good, dict):
        approved["status"] = "fallback-active"
        approved["effective"] = last_known_good
    else:
        approved["status"] = "no-approved-runtime"
        approved["effective"] = None

    approved["last_gate_failure"] = {
        "version": version,
        "failed_checks": failed,
        "validated_at": timestamp,
        "validation_result": str(result_path),
    }
    approved["next_candidate"] = choose_next_candidate(
        lock, approved, just_failed_version=version
    )
    _write(approved_path, approved)
    return False, approved


def status(lock_path: Path, approved_path: Path) -> dict[str, object]:
    lock = _load(lock_path)
    approved = (
        _load(approved_path)
        if approved_path.exists()
        else {
            "schema_version": 1,
            "status": "unvalidated",
            "last_known_good": None,
            "rejected": [],
        }
    )
    return {
        "candidate": lock.get("candidate") or lock.get("selected"),
        "effective": approved.get("effective"),
        "last_known_good": approved.get("last_known_good"),
        "status": approved.get("status"),
        "next_candidate": choose_next_candidate(lock, approved),
        "rejected": approved.get("rejected", []),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    evaluate_parser = sub.add_parser("evaluate")
    evaluate_parser.add_argument(
        "--policy",
        type=Path,
        default=Path("device/marble/upstream-policy.json"),
    )
    evaluate_parser.add_argument(
        "--lock",
        type=Path,
        default=Path("device/marble/upstream/pixel-camera.json"),
    )
    evaluate_parser.add_argument("--result", type=Path, required=True)
    evaluate_parser.add_argument(
        "--approved",
        type=Path,
        default=Path("device/marble/upstream/pixel-camera-approved.json"),
    )

    status_parser = sub.add_parser("status")
    status_parser.add_argument(
        "--lock",
        type=Path,
        default=Path("device/marble/upstream/pixel-camera.json"),
    )
    status_parser.add_argument(
        "--approved",
        type=Path,
        default=Path("device/marble/upstream/pixel-camera-approved.json"),
    )

    args = parser.parse_args()

    try:
        if args.command == "evaluate":
            promoted, state = evaluate(
                args.policy, args.lock, args.result, args.approved
            )
            if promoted:
                print(
                    "PROMOTED: "
                    f"{state['effective']['version']} is now last-known-good."
                )
                return 0

            failure = state.get("last_gate_failure", {})
            print(
                "REJECTED: "
                f"{failure.get('version')} failed "
                f"{', '.join(failure.get('failed_checks', []))}."
            )
            effective = state.get("effective")
            if isinstance(effective, dict):
                print(
                    f"FALLBACK: keeping {effective.get('version')} active."
                )
            next_candidate = state.get("next_candidate")
            if isinstance(next_candidate, dict):
                print(
                    "NEXT CANDIDATE: "
                    f"{next_candidate.get('version')}"
                )
            return 2

        print(json.dumps(status(args.lock, args.approved), indent=2))
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
