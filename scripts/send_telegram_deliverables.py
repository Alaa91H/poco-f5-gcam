#!/usr/bin/env python3
"""Send every flavour in a deliverables manifest through Telegram.

This helper is used only against the local Telegram Bot API server started by
the GitHub Actions workflow. It validates the manifest file size and SHA-256
before uploading, sends one document per flavour, and verifies the response
metadata. It never prints the bot token.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

DEFAULT_MAX_BYTES = 2000 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 1800


class DeliveryError(RuntimeError):
    pass


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeliveryError(f"could not read deliverables manifest {path}: {exc}") from exc
    flavors = data.get("flavors")
    if not isinstance(flavors, list) or not flavors:
        raise DeliveryError("deliverables manifest has no flavours")
    return data


def validate_entry(entry: dict[str, Any], max_bytes: int) -> Path:
    try:
        apk = Path(entry["apk"]).resolve()
        expected_sha256 = str(entry["sha256"])
        expected_size = int(entry["size_bytes"])
        caption = str(entry["delivery_caption"])
    except (KeyError, TypeError, ValueError) as exc:
        raise DeliveryError(f"invalid deliverables entry: {entry!r}") from exc

    if not apk.is_file():
        raise DeliveryError(f"deliverable does not exist: {apk}")
    actual_size = apk.stat().st_size
    if actual_size != expected_size:
        raise DeliveryError(
            f"deliverable size changed for {apk.name}: "
            f"expected {expected_size}, got {actual_size}"
        )
    if actual_size > max_bytes:
        raise DeliveryError(
            f"deliverable {apk.name} exceeds the Telegram limit: "
            f"{actual_size} > {max_bytes}"
        )
    actual_sha256 = sha256_path(apk)
    if actual_sha256 != expected_sha256:
        raise DeliveryError(
            f"deliverable checksum changed for {apk.name}: "
            f"expected {expected_sha256}, got {actual_sha256}"
        )
    if len(caption) > 1024:
        raise DeliveryError(
            f"caption for {apk.name} exceeds Telegram's 1024-character limit"
        )
    return apk


def send_document(
    *,
    api_url: str,
    bot_token: str,
    chat_id: str,
    apk: Path,
    caption: str,
    timeout: int,
) -> dict[str, Any]:
    url = api_url.rstrip("/") + f"/bot{bot_token}/sendDocument"
    command = [
        "curl",
        "--silent",
        "--show-error",
        "--fail-with-body",
        "--retry",
        "3",
        "--retry-all-errors",
        "--max-time",
        str(timeout),
        "--request",
        "POST",
        "--url",
        url,
        "--form",
        f"chat_id={chat_id}",
        "--form",
        f"document=@{apk};type=application/vnd.android.package-archive",
        "--form-string",
        f"caption={caption}",
    ]
    completed = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        # Never echo the request URL: it contains the bot token.
        raise DeliveryError(
            f"Telegram upload failed for {apk.name} "
            f"(curl exit {completed.returncode}): {completed.stderr[-2000:]}"
        )
    try:
        response = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise DeliveryError(
            f"Telegram returned invalid JSON for {apk.name}: "
            f"{completed.stdout[-2000:]}"
        ) from exc
    if response.get("ok") is not True:
        raise DeliveryError(f"Telegram rejected {apk.name}: {response}")

    document = response.get("result", {}).get("document") or {}
    delivered_size = document.get("file_size")
    delivered_name = document.get("file_name")
    if delivered_size is not None and int(delivered_size) != apk.stat().st_size:
        raise DeliveryError(
            f"Telegram size mismatch for {apk.name}: "
            f"expected {apk.stat().st_size}, got {delivered_size}"
        )
    if delivered_name and delivered_name != apk.name:
        raise DeliveryError(
            f"Telegram name mismatch: expected {apk.name}, got {delivered_name}"
        )
    return document


def deliver_manifest(
    manifest_path: Path,
    *,
    api_url: str,
    bot_token: str,
    chat_id: str,
    max_bytes: int = DEFAULT_MAX_BYTES,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    manifest = load_manifest(manifest_path)
    delivered: list[dict[str, Any]] = []
    for entry in manifest["flavors"]:
        apk = validate_entry(entry, max_bytes)
        document = send_document(
            api_url=api_url,
            bot_token=bot_token,
            chat_id=chat_id,
            apk=apk,
            caption=str(entry["delivery_caption"]),
            timeout=timeout,
        )
        delivered.append(
            {
                "id": entry.get("id"),
                "apk": os.fspath(apk),
                "file_name": document.get("file_name") or apk.name,
                "file_size": document.get("file_size") or apk.stat().st_size,
            }
        )
        print(f"Telegram accepted: {apk.name} ({apk.stat().st_size} bytes)")
    return delivered


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--api-url", default="http://127.0.0.1:8081")
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args()

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token:
        print("error: TELEGRAM_BOT_TOKEN is empty", file=sys.stderr)
        return 1
    if not chat_id:
        print("error: TELEGRAM_CHAT_ID is empty", file=sys.stderr)
        return 1

    try:
        delivered = deliver_manifest(
            args.manifest,
            api_url=args.api_url,
            bot_token=token,
            chat_id=chat_id,
            max_bytes=args.max_bytes,
            timeout=args.timeout,
        )
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps({"delivered": delivered}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
