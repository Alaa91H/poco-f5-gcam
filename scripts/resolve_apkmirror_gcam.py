#!/usr/bin/env python3
"""Resolve the newest APKMirror Pixel Camera release compatible with POCO F5.

This script intentionally stores metadata only. Proprietary APK/APKM files remain
outside the repository and can be fetched separately by local tooling when needed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable

VERSION_RE = re.compile(r"^\\d+(?:\\.\\d+)+(?:[A-Za-z0-9._-]*)?$")
VARIANT_MARKER = "/variant-"
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_ATTEMPTS = 3


class AnchorCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._current_href: str | None = None
        self._current_text: list[str] = []
        self.anchors: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self._current_href = href
            self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._current_href is not None:
            self._current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._current_href is not None:
            text = " ".join("".join(self._current_text).split())
            self.anchors.append((self._current_href, text))
            self._current_href = None
            self._current_text = []


@dataclass(frozen=True)
class Candidate:
    version: str
    min_api: int
    architectures: tuple[str, ...]
    dpis: tuple[str, ...]
    release_url: str


def _as_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    return (str(value),)


def _extract_min_api(spec: dict[str, object]) -> int | None:
    values = _as_tuple(spec.get("minapi_slug"))
    for value in values:
        match = re.search(r"(\\d+)", value)
        if match:
            return int(match.group(1))
    return None


def _parse_variant_href(href: str) -> dict[str, object] | None:
    path = urllib.parse.urlsplit(href).path
    if VARIANT_MARKER not in path:
        return None
    payload = path.split(VARIANT_MARKER, 1)[1].rstrip("/")
    try:
        decoded = urllib.parse.unquote(payload)
        parsed = json.loads(decoded)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\\d+", version))


def discover_candidates(html: str, product_url: str) -> list[Candidate]:
    parser = AnchorCollector()
    parser.feed(html)
    anchors = parser.anchors
    candidates: list[Candidate] = []

    variant_indexes = [
        index for index, (href, _) in enumerate(anchors) if VARIANT_MARKER in href
    ]

    for offset, index in enumerate(variant_indexes):
        href, _ = anchors[index]
        spec = _parse_variant_href(href)
        if not spec:
            continue

        min_api = _extract_min_api(spec)
        if min_api is None:
            continue

        end = variant_indexes[offset + 1] if offset + 1 < len(variant_indexes) else len(anchors)
        release: tuple[str, str] | None = None

        for release_href, release_text in anchors[index + 1 : end]:
            normalized_text = release_text.strip()
            if (
                VERSION_RE.fullmatch(normalized_text)
                and "/apk/google-inc/camera/" in release_href
                and "-release/" in release_href
            ):
                release = (
                    normalized_text,
                    urllib.parse.urljoin(product_url, release_href),
                )
                break

        if release is None:
            continue

        version, release_url = release
        candidates.append(
            Candidate(
                version=version,
                min_api=min_api,
                architectures=_as_tuple(spec.get("arches_slug")),
                dpis=_as_tuple(spec.get("dpis_slug")),
                release_url=release_url,
            )
        )

    return candidates


def choose_candidate(policy: dict[str, object], candidates: Iterable[Candidate]) -> Candidate:
    compatibility = policy["compatibility"]
    if not isinstance(compatibility, dict):
        raise ValueError("policy.compatibility must be an object")

    allowed_arches = set(_as_tuple(compatibility.get("architectures")))
    allowed_dpis = set(_as_tuple(compatibility.get("dpis")))
    max_min_api = int(compatibility["max_min_api"])

    compatible = [
        candidate
        for candidate in candidates
        if candidate.min_api <= max_min_api
        and bool(allowed_arches.intersection(candidate.architectures))
        and bool(allowed_dpis.intersection(candidate.dpis))
    ]

    if not compatible:
        raise RuntimeError(
            "APKMirror returned no Pixel Camera variant compatible with "
            f"API <= {max_min_api}, arches={sorted(allowed_arches)}, "
            f"dpis={sorted(allowed_dpis)}"
        )

    return max(compatible, key=lambda item: (_version_key(item.version), item.min_api))


def fetch_html(url: str, attempts: int = DEFAULT_ATTEMPTS) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 "
                "poco-f5-gcam-upstream-resolver/1.0"
            ),
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.8",
        },
    )

    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace")
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(1.5 * (attempt + 1))

    raise RuntimeError(f"Unable to fetch {url}: {last_error}") from last_error


def build_lock(policy: dict[str, object], candidate: Candidate) -> dict[str, object]:
    device = policy["device"]
    android = policy["android"]
    compatibility = policy["compatibility"]
    source = policy["source"]

    if not all(isinstance(item, dict) for item in (device, android, compatibility, source)):
        raise ValueError("device, android, compatibility and source must be objects")

    min_android = candidate.min_api - 20 if candidate.min_api >= 21 else candidate.min_api

    return {
        "schema_version": 1,
        "source": {
            "provider": source["provider"],
            "product_url": source["product_url"],
        },
        "target": {
            "device": f"{device['name']} ({device['codename']})",
            "rom": device["rom"],
            "android_version": android["version"],
            "api_level": android["api_level"],
            "architectures": list(_as_tuple(compatibility.get("architectures"))),
            "dpis": list(_as_tuple(compatibility.get("dpis"))),
        },
        "selected": {
            "version": candidate.version,
            "min_api": candidate.min_api,
            "min_android": min_android,
            "architectures": list(candidate.architectures),
            "dpis": list(candidate.dpis),
            "release_url": candidate.release_url,
        },
        "runtime_validation": "pending-on-device",
        "resolved_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
    }


def _stable_identity(lock: dict[str, object]) -> tuple[object, ...]:
    selected = lock.get("selected")
    target = lock.get("target")
    if not isinstance(selected, dict) or not isinstance(target, dict):
        return ()
    return (
        selected.get("version"),
        selected.get("min_api"),
        tuple(selected.get("architectures", [])),
        tuple(selected.get("dpis", [])),
        selected.get("release_url"),
        target.get("api_level"),
        tuple(target.get("architectures", [])),
        tuple(target.get("dpis", [])),
    )


def resolve(policy_path: Path, output_path: Path, source_html: Path | None = None) -> bool:
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    source = policy["source"]
    if not isinstance(source, dict):
        raise ValueError("policy.source must be an object")

    product_url = str(source["product_url"])
    html = source_html.read_text(encoding="utf-8") if source_html else fetch_html(product_url)

    candidates = discover_candidates(html, product_url)
    if not candidates:
        raise RuntimeError(
            "No APKMirror Pixel Camera variants were parsed. "
            "The upstream page structure may have changed."
        )

    candidate = choose_candidate(policy, candidates)
    new_lock = build_lock(policy, candidate)

    if output_path.exists():
        current_lock = json.loads(output_path.read_text(encoding="utf-8"))
        if _stable_identity(current_lock) == _stable_identity(new_lock):
            print(
                f"Pixel Camera {candidate.version} remains the newest compatible release "
                f"(min API {candidate.min_api})."
            )
            return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(new_lock, indent=2, ensure_ascii=False) + "\\n",
        encoding="utf-8",
    )
    print(
        f"Selected Pixel Camera {candidate.version} "
        f"(min API {candidate.min_api}) -> {candidate.release_url}"
    )
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--policy",
        type=Path,
        default=Path("device/marble/upstream-policy.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("device/marble/upstream/pixel-camera.json"),
    )
    parser.add_argument(
        "--source-html",
        type=Path,
        help="Read an APKMirror HTML fixture instead of accessing the network.",
    )
    args = parser.parse_args()

    try:
        resolve(args.policy, args.output, args.source_html)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
