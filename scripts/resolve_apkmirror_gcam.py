#!/usr/bin/env python3
"""Resolve compatible APKMirror Pixel Camera candidates for POCO F5.

Only metadata is stored. Proprietary APK/APKM files are never committed.
The newest metadata-compatible release becomes the *candidate*, not the
last-known-good runtime release.
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

VERSION_RE = re.compile(r"^\d+(?:\.\d+)+(?:[A-Za-z0-9._-]*)?$")
VARIANT_MARKER = "/variant-"
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_ATTEMPTS = 3

API_TO_ANDROID = {
    37: "17",
    36: "16",
    35: "15",
    34: "14",
    33: "13",
    32: "12L",
    31: "12",
    30: "11",
    29: "10",
    28: "9",
}

ANDROID_TO_API = {
    "17": 37,
    "16": 36,
    "15": 35,
    "14": 34,
    "13": 33,
    "12L": 32,
    "12": 31,
    "11": 30,
    "10": 29,
    "9.0": 28,
    "9": 28,
}

VERSION_SEARCH_RE = re.compile(
    r"(?<!\d)(\d+(?:\.\d+)+(?:[A-Za-z0-9._-]*)?)(?!\d)"
)
ARCH_TEXT_RE = re.compile(
    r"\b(arm64-v8a|armeabi-v7a|arm-v7a|x86_64|x86|noarch)\b",
    re.IGNORECASE,
)
DPI_TEXT_RE = re.compile(r"\b(nodpi|\d+(?:-\d+)?dpi)\b", re.IGNORECASE)
ANDROID_TEXT_RE = re.compile(
    r"Android\s+(12L|\d+(?:\.\d+)?)\+",
    re.IGNORECASE,
)


class AnchorCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._href: str | None = None
        self._text: list[str] = []
        self.anchors: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self._href = href
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            text = " ".join("".join(self._text).split())
            self.anchors.append((self._href, text))
            self._href = None
            self._text = []


@dataclass(frozen=True)
class Candidate:
    version: str
    min_api: int
    architectures: tuple[str, ...]
    dpis: tuple[str, ...]
    release_url: str

    def to_lock_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "min_api": self.min_api,
            "min_android": API_TO_ANDROID.get(self.min_api, f"API {self.min_api}"),
            "architectures": list(self.architectures),
            "dpis": list(self.dpis),
            "release_url": self.release_url,
        }


def _as_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    return (str(value),)


def _extract_min_api(spec: dict[str, object]) -> int | None:
    for value in _as_tuple(spec.get("minapi_slug")):
        match = re.search(r"(\d+)", value)
        if match:
            return int(match.group(1))
    return None


def _parse_variant_href(href: str) -> dict[str, object] | None:
    path = urllib.parse.urlsplit(href).path
    if VARIANT_MARKER not in path:
        return None
    payload = path.split(VARIANT_MARKER, 1)[1].rstrip("/")
    try:
        parsed = json.loads(urllib.parse.unquote(payload))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _parse_variant_text(text: str) -> dict[str, object] | None:
    arch_match = ARCH_TEXT_RE.search(text)
    dpi_match = DPI_TEXT_RE.search(text)
    android_match = ANDROID_TEXT_RE.search(text)
    if not (arch_match and dpi_match and android_match):
        return None

    android_version = android_match.group(1)
    min_api = ANDROID_TO_API.get(android_version)
    if min_api is None:
        return None

    return {
        "arches_slug": [arch_match.group(1).lower()],
        "dpis_slug": [dpi_match.group(1).lower()],
        "minapi_slug": f"minapi-{min_api}",
    }


def _parse_variant_anchor(href: str, text: str) -> dict[str, object] | None:
    return _parse_variant_href(href) or _parse_variant_text(text)


def _extract_release_version(text: str) -> str | None:
    normalized = " ".join(text.split())
    if VERSION_RE.fullmatch(normalized):
        return normalized
    match = VERSION_SEARCH_RE.search(normalized)
    return match.group(1) if match else None


def _normalize_release_url(product_url: str, href: str) -> str:
    absolute = urllib.parse.urljoin(product_url, href)
    parsed = urllib.parse.urlsplit(absolute)
    path = parsed.path
    marker = "-release/"
    if marker in path:
        path = path.split(marker, 1)[0] + marker
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, path, "", "")
    )


def _is_variant_anchor(href: str, text: str) -> bool:
    if VARIANT_MARKER in urllib.parse.urlsplit(href).path:
        return True
    return _parse_variant_text(text) is not None


def build_variant_url(
    product_url: str,
    architecture: str,
    dpi: str,
    min_api: int,
) -> str:
    payload = {
        "arches_slug": [architecture],
        "dpis_slug": [dpi],
        "minapi_slug": f"minapi-{min_api}",
    }
    encoded = urllib.parse.quote(
        json.dumps(payload, separators=(",", ":")),
        safe="",
    )
    return urllib.parse.urljoin(product_url, f"variant-{encoded}/")


def discover_variant_page_candidates(
    html: str,
    product_url: str,
    architecture: str,
    dpi: str,
    min_api: int,
) -> list[Candidate]:
    parser = AnchorCollector()
    parser.feed(html)
    result: list[Candidate] = []

    for href, text in parser.anchors:
        if (
            "/apk/google-inc/camera/" not in href
            or "-release/" not in href
        ):
            continue

        version = _extract_release_version(text)
        if version is None:
            continue

        result.append(
            Candidate(
                version=version,
                min_api=min_api,
                architectures=(architecture,),
                dpis=(dpi,),
                release_url=_normalize_release_url(product_url, href),
            )
        )

    deduped: dict[tuple[str, str], Candidate] = {}
    for item in result:
        deduped[(item.version, item.release_url)] = item
    return list(deduped.values())


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", version))


def discover_candidates(html: str, product_url: str) -> list[Candidate]:
    parser = AnchorCollector()
    parser.feed(html)
    anchors = parser.anchors
    result: list[Candidate] = []

    variant_indexes = [
        index
        for index, (href, text) in enumerate(anchors)
        if _is_variant_anchor(href, text)
    ]

    for offset, index in enumerate(variant_indexes):
        href, variant_text = anchors[index]
        spec = _parse_variant_anchor(href, variant_text)
        if not spec:
            continue

        min_api = _extract_min_api(spec)
        if min_api is None:
            continue

        end = (
            variant_indexes[offset + 1]
            if offset + 1 < len(variant_indexes)
            else len(anchors)
        )

        for release_href, release_text in anchors[index + 1 : end]:
            if (
                "/apk/google-inc/camera/" not in release_href
                or "-release/" not in release_href
            ):
                continue

            version = _extract_release_version(release_text)
            if version is None:
                continue

            result.append(
                Candidate(
                    version=version,
                    min_api=min_api,
                    architectures=_as_tuple(spec.get("arches_slug")),
                    dpis=_as_tuple(spec.get("dpis_slug")),
                    release_url=_normalize_release_url(product_url, release_href),
                )
            )
            break

    return result


def compatible_candidates(
    policy: dict[str, object], candidates: Iterable[Candidate]
) -> list[Candidate]:
    compatibility = policy["compatibility"]
    selection = policy.get("selection", {})
    if not isinstance(compatibility, dict):
        raise ValueError("policy.compatibility must be an object")
    if not isinstance(selection, dict):
        raise ValueError("policy.selection must be an object")

    allowed_arches = set(_as_tuple(compatibility.get("architectures")))
    allowed_dpis = set(_as_tuple(compatibility.get("dpis")))
    max_min_api = int(compatibility["max_min_api"])
    limit = int(selection.get("candidate_limit", 5))
    if limit < 1:
        raise ValueError("selection.candidate_limit must be >= 1")

    compatible = [
        item
        for item in candidates
        if item.min_api <= max_min_api
        and bool(allowed_arches.intersection(item.architectures))
        and bool(allowed_dpis.intersection(item.dpis))
    ]

    deduped: dict[tuple[str, str], Candidate] = {}
    for item in compatible:
        deduped[(item.version, item.release_url)] = item

    ordered = sorted(
        deduped.values(),
        key=lambda item: (_version_key(item.version), item.min_api),
        reverse=True,
    )
    if not ordered:
        raise RuntimeError(
            "APKMirror returned no Pixel Camera variant compatible with "
            f"API <= {max_min_api}, arches={sorted(allowed_arches)}, "
            f"dpis={sorted(allowed_dpis)}"
        )

    return ordered[:limit]


def choose_candidate(
    policy: dict[str, object], candidates: Iterable[Candidate]
) -> Candidate:
    return compatible_candidates(policy, candidates)[0]


def fetch_html(url: str, attempts: int = DEFAULT_ATTEMPTS) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 "
                "poco-f5-gcam-upstream-resolver/2.0"
            ),
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.8",
        },
    )

    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(
                request, timeout=DEFAULT_TIMEOUT_SECONDS
            ) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace")
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(1.5 * (attempt + 1))

    raise RuntimeError(f"Unable to fetch {url}: {last_error}") from last_error


def build_lock(
    policy: dict[str, object], candidates: list[Candidate]
) -> dict[str, object]:
    device = policy["device"]
    android = policy["android"]
    compatibility = policy["compatibility"]
    source = policy["source"]

    if not all(
        isinstance(item, dict)
        for item in (device, android, compatibility, source)
    ):
        raise ValueError(
            "device, android, compatibility and source must be objects"
        )

    candidate = candidates[0]
    return {
        "schema_version": 2,
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
        "candidate": candidate.to_lock_dict(),
        "candidates": [item.to_lock_dict() for item in candidates],
        "selected": candidate.to_lock_dict(),
        "runtime_validation": "pending-on-device",
        "promotion_state": "candidate-only",
        "resolved_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
    }


def _stable_identity(lock: dict[str, object]) -> tuple[object, ...]:
    target = lock.get("target")
    candidates = lock.get("candidates")
    if not isinstance(target, dict):
        return ()
    if not isinstance(candidates, list):
        selected = lock.get("selected")
        candidates = [selected] if isinstance(selected, dict) else []

    candidate_ids: list[tuple[object, ...]] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        candidate_ids.append(
            (
                item.get("version"),
                item.get("min_api"),
                tuple(item.get("architectures", [])),
                tuple(item.get("dpis", [])),
                item.get("release_url"),
            )
        )

    return (
        tuple(candidate_ids),
        target.get("api_level"),
        tuple(target.get("architectures", [])),
        tuple(target.get("dpis", [])),
    )


def resolve(
    policy_path: Path,
    output_path: Path,
    source_html: Path | None = None,
) -> bool:
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    source = policy["source"]
    if not isinstance(source, dict):
        raise ValueError("policy.source must be an object")

    product_url = str(source["product_url"])
    if source_html:
        html = source_html.read_text(encoding="utf-8")
        discovered = discover_candidates(html, product_url)
    else:
        html = fetch_html(product_url)
        discovered = discover_candidates(html, product_url)

        if not discovered:
            compatibility = policy.get("compatibility")
            if not isinstance(compatibility, dict):
                raise ValueError("policy.compatibility must be an object")

            allowed_arches = _as_tuple(compatibility.get("architectures"))
            allowed_dpis = _as_tuple(compatibility.get("dpis"))
            max_min_api = int(compatibility["max_min_api"])

            fallback_discovered: list[Candidate] = []
            for architecture in allowed_arches:
                for dpi in allowed_dpis:
                    variant_url = build_variant_url(
                        product_url,
                        architecture,
                        dpi,
                        max_min_api,
                    )
                    variant_html = fetch_html(variant_url)
                    fallback_discovered.extend(
                        discover_variant_page_candidates(
                            variant_html,
                            product_url,
                            architecture,
                            dpi,
                            max_min_api,
                        )
                    )
            discovered = fallback_discovered

    if not discovered:
        raise RuntimeError(
            "No APKMirror Pixel Camera variants were parsed from either "
            "the product page or the direct target-variant page. "
            "Failing closed instead of selecting an unverified release."
        )

    ordered = compatible_candidates(policy, discovered)
    new_lock = build_lock(policy, ordered)

    if output_path.exists():
        current_lock = json.loads(output_path.read_text(encoding="utf-8"))
        if _stable_identity(current_lock) == _stable_identity(new_lock):
            print(
                f"Pixel Camera {ordered[0].version} remains the newest "
                f"metadata-compatible candidate; {len(ordered)} fallback "
                "candidate(s) are tracked."
            )
            return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(new_lock, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        f"Candidate Pixel Camera {ordered[0].version} "
        f"(min API {ordered[0].min_api}); "
        f"tracking {len(ordered)} compatible candidate(s)."
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
