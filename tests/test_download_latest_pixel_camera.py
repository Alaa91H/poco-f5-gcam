import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import download_latest_pixel_camera as downloader


class DownloaderParsingTests(unittest.TestCase):
    def test_selects_variant_download_page_for_selected_version(self):
        html = (
            '<a href="/apk/google-inc/camera/'
            'pixel-camera-11-0-073-972752740-32-release/'
            'pixel-camera-11-0-073-972752740-32-android-apk-download/">'
            '11.0.073.972752740.32</a>'
        )
        url = downloader.choose_variant_url(
            html,
            "https://www.apkmirror.com/apk/google-inc/camera/"
            "pixel-camera-11-0-073-972752740-32-release/",
            "11.0.073.972752740.32",
        )
        self.assertTrue(url.endswith("pixel-camera-11-0-073-972752740-32-android-apk-download/"))

    def test_selects_download_trigger(self):
        html = (
            '<a href="/apk/google-inc/camera/release/'
            'variant/download/?key=abc123">'
            'Download APK Bundle Base APK and 22 splits, 1.00 GB</a>'
        )
        url = downloader.choose_download_trigger(
            html,
            "https://www.apkmirror.com/apk/google-inc/camera/release/variant/",
        )
        self.assertIn("/download/?key=abc123", url)

    def test_extracts_bundle_sha256_from_visible_text(self):
        sha = "cc8f75c106bfa4a9b488ebee3859397410c33cd7a59f091d11b06cfc3c73d18f"
        html = (
            "<h4>APK bundle file hashes</h4>"
            "<div>MD5: deadbeef</div>"
            "<div>SHA-1: 012345</div>"
            f"<div>SHA-256: {sha}</div>"
        )
        self.assertEqual(downloader.extract_bundle_sha256(html), sha)

    def test_direct_url_parser_accepts_apkmirror_package_link(self):
        html = (
            '<a href="https://downloadr2.apkmirror.com/wp-content/uploads/'
            'camera-screenshot.png">image</a>'
            '<a href="https://downloadr2.apkmirror.com/wp-content/uploads/'
            'PixelCamera.apkm?key=abc">click here to download</a>'
        )
        url = downloader._direct_url_from_html(
            html,
            "https://www.apkmirror.com/download/",
        )
        self.assertEqual(
            url,
            "https://downloadr2.apkmirror.com/wp-content/uploads/"
            "PixelCamera.apkm?key=abc",
        )

    def test_direct_url_parser_ignores_screenshot_only(self):
        html = (
            '<a href="https://downloadr2.apkmirror.com/wp-content/uploads/'
            'camera.png">download</a>'
        )
        self.assertIsNone(
            downloader._direct_url_from_html(
                html, "https://www.apkmirror.com/download/"
            )
        )

    def test_extracts_apkmirror_wait_countdown(self):
        html = "<div>Whoa there! You'll have to wait 15 more sec.</div>"
        self.assertEqual(downloader._countdown_seconds(html), 15)

    def test_direct_url_parser_rejects_unrelated_host(self):
        html = '<a href="https://example.com/file.apkm">download</a>'
        self.assertIsNone(
            downloader._direct_url_from_html(
                html, "https://www.apkmirror.com/download/"
            )
        )


if __name__ == "__main__":
    unittest.main()
