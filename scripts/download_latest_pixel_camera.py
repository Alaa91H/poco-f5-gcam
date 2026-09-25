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
PACKAGE_URL_RE = re.compile(r"\.(?:apk|apkm|xapk)(?:$|[?#])", re.IGNORECASE)
IMAGE_URL_RE = re.compile(
    r"\.(?:png|jpe?g|webp|gif|svg)(?:$|[?#])",
    re.IGNORECASE,
)
DOWNLOAD_LABEL_RE = re.compile(r"(?:Download|click\s+here)", re.IGNORECASE)
WAIT_SECONDS_RE = re.compile(
    r"wait\s+(\d+)\s+(?:more\s+)?sec",
    re.IGNORECASE,
)
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


class DownloadHintCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hints: list[tuple[str, str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {name.lower(): value for name, value in attrs if value}
        for name, value in attrs_dict.items():
            lowered = value.lower()
            if (
                name in {"href", "src", "action", "content", "data-url", "data-href"}
                or name.startswith("data-")
            ) and any(
                token in lowered
                for token in ("download", "key=", "token", ".apk", ".apkm", "mirror")
            ):
                self.hints.append((tag.lower(), name, value))


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


def _download_hints(raw_html: str, base_url: str) -> list[str]:
    collector = DownloadHintCollector()
    collector.feed(raw_html)

    hints: list[str] = []
    for tag, name, value in collector.hints:
        normalized = value.strip()
        if name in {"href", "src", "action", "data-url", "data-href"}:
            try:
                normalized = urllib.parse.urljoin(base_url, normalized)
            except Exception:
                pass
        hints.append(f"{tag}.{name}={normalized}")

    script_patterns = [
        r"(?:window\.)?location(?:\.href)?\s*=\s*['\"]([^'\"]+)['\"]",
        r"setTimeout\s*\([^)]{0,500}\)",
        r"https?://downloadr\d+\.apkmirror\.com/[^\"'<>\\\s]+",
        r"[^\"'<>\\\s]+\.(?:apk|apkm|xapk)(?:\?[^\"'<>\\\s]*)?",
    ]
    for pattern in script_patterns:
        for match in re.finditer(pattern, raw_html, re.I | re.S):
            value = match.group(1) if match.lastindex else match.group(0)
            value = html_lib.unescape(" ".join(value.split()))
            hints.append(f"script={value[:500]}")

    deduped: list[str] = []
    seen: set[str] = set()
    for hint in hints:
        if hint not in seen:
            seen.add(hint)
            deduped.append(hint)
        if len(deduped) >= 40:
            break
    return deduped


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


def _countdown_seconds(raw_html: str) -> int | None:
    match = WAIT_SECONDS_RE.search(_visible_text(raw_html))
    if not match:
        return None
    return max(0, min(int(match.group(1)), 30))


def _is_download_handler_url(url: str) -> bool:
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    return (
        (host == "apkmirror.com" or host.endswith(".apkmirror.com"))
        and parsed.path.lower().endswith("/wp-content/themes/apkmirror/download.php")
        and "id=" in parsed.query.lower()
        and "key=" in parsed.query.lower()
    )


def _followup_download_url(raw_html: str, base_url: str) -> str | None:
    candidates: list[tuple[int, str]] = []
    for href, text in _anchors(raw_html):
        normalized = " ".join(text.split()).lower()
        absolute = urllib.parse.urljoin(base_url, href)
        parsed = urllib.parse.urlsplit(absolute)

        if parsed.scheme not in ("http", "https"):
            continue

        host = (parsed.hostname or "").lower()
        if not (host == "apkmirror.com" or host.endswith(".apkmirror.com")):
            continue

        if IMAGE_URL_RE.search(parsed.path):
            continue

        is_download_handler = _is_download_handler_url(absolute)

        if (
            not is_download_handler
            and "click here" not in normalized
            and "download" not in normalized
        ):
            continue

        score = 0
        if is_download_handler:
            score += 250
        if "click here" in normalized:
            score += 100
        if "/download/" in parsed.path:
            score += 40
        if "key=" in parsed.query.lower():
            score += 20
        if DIRECT_HOST_RE.fullmatch(parsed.hostname or ""):
            score += 80

        candidates.append((score, absolute))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _direct_url_from_html(html: str, base_url: str) -> str | None:
    candidates: list[tuple[int, str]] = []

    for href, text in _anchors(html):
        absolute = urllib.parse.urljoin(base_url, href)
        parsed = urllib.parse.urlsplit(absolute)
        host = parsed.hostname or ""
        if not DIRECT_HOST_RE.fullmatch(host):
            continue

        if IMAGE_URL_RE.search(parsed.path):
            continue

        score = 0
        if PACKAGE_URL_RE.search(absolute):
            score += 100
        if DOWNLOAD_LABEL_RE.search(text):
            score += 40
        if "key=" in parsed.query.lower():
            score += 20

        if score >= 40:
            candidates.append((score, absolute))

    if candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    match = re.search(
        r"https?://downloadr\d+\.apkmirror\.com/"
        r"[^\"'<>\\\s]+\.(?:apk|apkm|xapk)"
        r"(?:\?[^\"'<>\\\s]*)?",
        html,
        re.IGNORECASE,
    )
    if match:
        return match.group(0).replace("&amp;", "&")
    return None


def _resolve_trigger_once(
    cookie_jar: http.cookiejar.CookieJar,
    trigger_url: str,
    referer: str,
) -> tuple[str | None, str | None]:
    no_redirect_opener = _build_opener(cookie_jar, no_redirect=True)
    request = urllib.request.Request(trigger_url, headers=_headers(referer))

    try:
        with no_redirect_opener.open(
            request, timeout=DEFAULT_TIMEOUT_SECONDS
        ) as response:
            content_type = response.headers.get_content_type()
            if content_type != "text/html":
                return response.geturl(), None

            page = response.read().decode(
                response.headers.get_content_charset() or "utf-8",
                errors="replace",
            )
            return _direct_url_from_html(page, trigger_url), page
    except urllib.error.HTTPError as exc:
        if exc.code not in (301, 302, 303, 307, 308):
            raise

        location = exc.headers.get("Location")
        if not location:
            return None, None

        absolute = urllib.parse.urljoin(trigger_url, location)
        host = urllib.parse.urlsplit(absolute).hostname or ""
        if DIRECT_HOST_RE.fullmatch(host):
            return absolute, None

        page_opener = _build_opener(cookie_jar)
        try:
            page = _read_html(page_opener, absolute, trigger_url)
        except Exception:
            return None, None
        return _direct_url_from_html(page, absolute), page


def resolve_direct_url(
    cookie_jar: http.cookiejar.CookieJar,
    trigger_url: str,
    variant_url: str,
) -> str | None:
    current_url = trigger_url
    referer = variant_url
    seen: set[str] = set()
    waited: set[str] = set()
    last_page: str | None = None

    for _ in range(5):
        if current_url in seen:
            break
        seen.add(current_url)

        if _is_download_handler_url(current_url):
            return current_url

        direct_url, page = _resolve_trigger_once(
            cookie_jar, current_url, referer
        )
        if direct_url:
            parsed = urllib.parse.urlsplit(direct_url)
            if PACKAGE_URL_RE.search(direct_url) or DIRECT_HOST_RE.fullmatch(
                parsed.hostname or ""
            ):
                return direct_url

        if not page:
            break

        last_page = page
        followup = _followup_download_url(page, current_url)
        if followup and followup not in seen:
            if _is_download_handler_url(followup):
                return followup
            referer = current_url
            current_url = followup
            continue

        wait_seconds = _countdown_seconds(page)
        if wait_seconds is not None and current_url not in waited:
            waited.add(current_url)
            time.sleep(wait_seconds + 1)
            seen.discard(current_url)
            continue

        break

    if last_page:
        summary = _visible_text(last_page)[:700]
        if summary:
            print(
                "APKMirror trigger diagnostics: "
                + summary.replace("\n", " "),
                file=sys.stderr,
            )

        hints = _download_hints(last_page, current_url)
        if hints:
            print("APKMirror download hints:", file=sys.stderr)
            for hint in hints:
                print(f"  {hint}", file=sys.stderr)

    return None


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


def _filename_from_headers(
    headers,
    fallback_version: str,
    prefer_bundle: bool = False,
) -> str:
    disposition = headers.get("Content-Disposition") or ""
    match = FILENAME_RE.search(disposition)
    if match:
        name = urllib.parse.unquote(match.group(1)).strip()
        if name:
            return os.path.basename(name)

    content_type = headers.get_content_type()
    extension = (
        ".apkm"
        if prefer_bundle or "zip" in content_type or "octet-stream" in content_type
        else ".apk"
    )
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

        filename = _filename_from_headers(
            response.headers,
            target.version,
            prefer_bundle=expected_sha256 is not None,
        )
        destination = output_dir / filename
        temp = destination.with_suffix(destination.suffix + ".part")

        total = 0
        digest = hashlib.sha256()
        first_bytes = b""
        with temp.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                if not first_bytes:
                    first_bytes = chunk[:512]
                handle.write(chunk)
                digest.update(chunk)
                total += len(chunk)

        temp.replace(destination)

    if not first_bytes.startswith(b"PK"):
        preview = first_bytes.decode("utf-8", errors="replace")
        hex_prefix = first_bytes[:32].hex()
        destination.unlink(missing_ok=True)
        raise RuntimeError(
            "Downloaded response is not an APK/APKM ZIP payload: "
            f"size={total}, first32_hex={hex_prefix}, "
            f"preview={' '.join(preview.split())[:300]}"
        )

    if total < 1024 * 1024:
        destination.unlink(missing_ok=True)
        raise RuntimeError(
            f"Downloaded ZIP payload is unexpectedly small ({total} bytes)."
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
