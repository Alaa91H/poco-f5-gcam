#!/usr/bin/env python3
"""Download the newest metadata-compatible Pixel Camera release from APKMirror.

The resolver lock decides which release is compatible. This script follows the
corresponding APKMirror release/variant/download pages and stores the resulting
APK/APKM outside Git tracking.

No Google-owned binary is committed by this repository.
"""

from __future__ import annotations

import argparse
import hashlib
import html as html_lib
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_ATTEMPTS = 3
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/153.0.0.0 Safari/537.36 "
    "poco-f5-gcam-downloader/1.0"
)

DIRECT_HOST_RE = re.compile(r"^downloadr\d+\.apkmirror\.com$", re.IGNORECASE)
DOWNLOAD_LABEL_RE = re.compile(r"Download\s+(?:APK\s+Bundle|APK)", re.IGNORECASE)
SHA256_RE = re.compile(r"\b([0-9a-f]{64})\b", re.IGNORECASE)
FILENAME_RE = re.compile(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', re.IGNORECASE)


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


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class DownloadTarget:
    version: str
    release_url: str
    variant_url: str
    trigger_url: str
    direct_url: str | None
    expected_sha256: str | None


def _headers(referer: str | None = None) -> dict[str, str]:
    result = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/octet-stream;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
    }
    if referer:
        result["Referer"] = referer
    return result


def _build_opener(
    cookie_jar: http.cookiejar.CookieJar | None = None,
    no_redirect: bool = False,
) -> urllib.request.OpenerDirector:
    jar = cookie_jar or http.cookiejar.CookieJar()
    handlers: list[urllib.request.BaseHandler] = [
        urllib.request.HTTPCookieProcessor(jar)
    ]
    if no_redirect:
        handlers.append(NoRedirect())
    return urllib.request.build_opener(*handlers)


def _read_html(
    opener: urllib.request.OpenerDirector,
    url: str,
    referer: str | None = None,
    attempts: int = DEFAULT_ATTEMPTS,
) -> str:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers=_headers(referer))
            with opener.open(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace")
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Unable to fetch {url}: {last_error}") from last_error


def _anchors(html: str) -> list[tuple[str, str]]:
    parser = AnchorCollector()
    parser.feed(html)
    return parser.anchors


def _slug_version(version: str) -> str:
    return version.replace(".", "-")


def choose_variant_url(
    release_html: str,
    release_url: str,
    version: str,
) -> str:
    anchors = _anchors(release_html)
    preferred: list[str] = []
    generic: list[str] = []

    for href, text in anchors:
        absolute = urllib.parse.urljoin(release_url, href)
        path = urllib.parse.urlsplit(absolute).path
        if "-android-apk-download/" not in path:
            continue
        generic.append(absolute)
        if version in text or _slug_version(version) in path:
            preferred.append(absolute)

    matches = preferred or generic
    if not matches:
        raise RuntimeError(
            "Could not find APKMirror variant download page for the selected release."
        )

    return matches[0]


def choose_download_trigger(variant_html: str, variant_url: str) -> str:
    for href, text in _anchors(variant_html):
        absolute = urllib.parse.urljoin(variant_url, href)
        path = urllib.parse.urlsplit(absolute).path
        if DOWNLOAD_LABEL_RE.search(text) and "/download/" in path:
            return absolute

    raise RuntimeError(
        "Could not find the APKMirror download trigger for the selected variant."
    )


def _visible_text(raw_html: str) -> str:
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", raw_html, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(html_lib.unescape(text).split())


def extract_bundle_sha256(variant_html: str) -> str | None:
    text = _visible_text(variant_html)
    marker = re.search(
        r"APK\s+bundle\s+file\s+hashes(?P<body>.{0,1200})",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if marker:
        sha = SHA256_RE.search(marker.group("body"))
        if sha:
            return sha.group(1).lower()
    return None


def _direct_url_from_html(html: str, base_url: str) -> str | None:
    for href, _ in _anchors(html):
        absolute = urllib.parse.urljoin(base_url, href)
        host = urllib.parse.urlsplit(absolute).hostname or ""
        if DIRECT_HOST_RE.fullmatch(host):
            return absolute

    match = re.search(
        r"https?://downloadr\d+\.apkmirror\.com/[^\"'<>\\\s]+",
        html,
        re.IGNORECASE,
    )
    if match:
        return match.group(0).replace("&amp;", "&")
    return None


def resolve_direct_url(
    cookie_jar: http.cookiejar.CookieJar,
    trigger_url: str,
    variant_url: str,
) -> str | None:
    # Fresh APKMirror nonces may immediately redirect to the binary CDN.
    no_redirect_opener = _build_opener(cookie_jar, no_redirect=True)
    request = urllib.request.Request(trigger_url, headers=_headers(variant_url))

    try:
        with no_redirect_opener.open(
            request, timeout=DEFAULT_TIMEOUT_SECONDS
        ) as response:
            content_type = response.headers.get_content_type()
            if content_type != "text/html":
                return response.geturl()

            page = response.read().decode(
                response.headers.get_content_charset() or "utf-8",
                errors="replace",
            )
            return _direct_url_from_html(page, trigger_url)
    except urllib.error.HTTPError as exc:
        if exc.code not in (301, 302, 303, 307, 308):
            raise

        location = exc.headers.get("Location")
        if not location:
            return None

        absolute = urllib.parse.urljoin(trigger_url, location)
        host = urllib.parse.urlsplit(absolute).hostname or ""
        if DIRECT_HOST_RE.fullmatch(host):
            return absolute

        page_opener = _build_opener(cookie_jar)
        try:
            page = _read_html(page_opener, absolute, trigger_url)
        except Exception:
            return None
        return _direct_url_from_html(page, absolute)


def resolve_target(lock_path: Path) -> DownloadTarget:
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    selected = lock.get("selected") or lock.get("candidate")
    if not isinstance(selected, dict):
        raise ValueError("The upstream lock does not contain a selected release.")

    version = str(selected["version"])
    release_url = str(selected["release_url"])

    cookie_jar = http.cookiejar.CookieJar()
    opener = _build_opener(cookie_jar)
    release_html = _read_html(opener, release_url)
    variant_url = choose_variant_url(release_html, release_url, version)
    variant_html = _read_html(opener, variant_url, release_url)
    trigger_url = choose_download_trigger(variant_html, variant_url)
    expected_sha256 = extract_bundle_sha256(variant_html)
    direct_url = resolve_direct_url(cookie_jar, trigger_url, variant_url)

    return DownloadTarget(
        version=version,
        release_url=release_url,
        variant_url=variant_url,
        trigger_url=trigger_url,
        direct_url=direct_url,
        expected_sha256=expected_sha256,
    )


def _filename_from_headers(headers, fallback_version: str) -> str:
    disposition = headers.get("Content-Disposition") or ""
    match = FILENAME_RE.search(disposition)
    if match:
        name = urllib.parse.unquote(match.group(1)).strip()
        if name:
            return os.path.basename(name)

    content_type = headers.get_content_type()
    extension = ".apkm" if "zip" in content_type or "octet-stream" in content_type else ".apk"
    return f"PixelCamera-{fallback_version}{extension}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(
    target: DownloadTarget,
    output_dir: Path,
) -> tuple[Path, str, int]:
    # Resolve a fresh nonce and download the binary in the SAME cookie session.
    # APKMirror's download flow may reject a valid short-lived URL if the
    # request loses the session that created it.
    cookie_jar = http.cookiejar.CookieJar()
    opener = _build_opener(cookie_jar)

    _read_html(opener, target.release_url)
    variant_html = _read_html(opener, target.variant_url, target.release_url)
    trigger_url = choose_download_trigger(variant_html, target.variant_url)
    direct_url = resolve_direct_url(
        cookie_jar, trigger_url, target.variant_url
    )
    if not direct_url:
        raise RuntimeError(
            "APKMirror did not expose a direct binary URL. "
            "The site may have changed its download flow."
        )

    expected_sha256 = extract_bundle_sha256(variant_html) or target.expected_sha256

    output_dir.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        direct_url,
        headers={
            **_headers(trigger_url),
            "Accept": "application/octet-stream,*/*;q=0.8",
        },
    )

    with opener.open(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
        content_type = response.headers.get_content_type()
        final_url = response.geturl()

        if content_type in ("text/html", "text/plain"):
            preview = response.read(2048).decode("utf-8", errors="replace")
            raise RuntimeError(
                "APKMirror returned an HTML/text response instead of the "
                f"binary payload (content-type={content_type}, url={final_url}). "
                f"Response preview: {' '.join(preview.split())[:300]}"
            )

        filename = _filename_from_headers(response.headers, target.version)
        destination = output_dir / filename
        temp = destination.with_suffix(destination.suffix + ".part")

        total = 0
        digest = hashlib.sha256()
        with temp.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                digest.update(chunk)
                total += len(chunk)

        temp.replace(destination)

    if total < 1024 * 1024:
        destination.unlink(missing_ok=True)
        raise RuntimeError(
            f"Downloaded payload is unexpectedly small ({total} bytes)."
        )

    actual_sha256 = digest.hexdigest()
    if expected_sha256 and actual_sha256 != expected_sha256:
        destination.unlink(missing_ok=True)
        raise RuntimeError(
            "Downloaded file SHA-256 does not match APKMirror metadata: "
            f"expected {expected_sha256}, got {actual_sha256}"
        )

    return destination, actual_sha256, total


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--lock",
        type=Path,
        default=Path("device/marble/upstream/pixel-camera.json"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("downloads/pixel-camera"),
    )
    parser.add_argument(
        "--resolve-only",
        action="store_true",
        help="Resolve the live APKMirror download path without downloading the binary.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable result JSON.",
    )
    args = parser.parse_args()

    try:
        target = resolve_target(args.lock)

        if args.resolve_only:
            result = {
                "version": target.version,
                "release_url": target.release_url,
                "variant_url": target.variant_url,
                "trigger_url": target.trigger_url,
                "direct_url_resolved": bool(target.direct_url),
                "expected_sha256": target.expected_sha256,
            }
        else:
            path, sha256, size = download(target, args.output_dir)
            result = {
                "version": target.version,
                "path": str(path),
                "size_bytes": size,
                "sha256": sha256,
                "expected_sha256": target.expected_sha256,
                "verified_sha256": (
                    target.expected_sha256 is not None
                    and sha256 == target.expected_sha256
                ),
            }

        if args.json:
            print(json.dumps(result, indent=2))
        else:
            for key, value in result.items():
                print(f"{key}: {value}")
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
