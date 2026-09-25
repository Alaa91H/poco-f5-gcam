import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import resolve_apkmirror_gcam as resolver


def variant(spec: str, version: str, slug: str) -> str:
    return (
        f'<a href="/apk/google-inc/camera/variant-{spec}/">variant</a>'
        f'<span>Latest:</span>'
        f'<a href="/apk/google-inc/camera/{slug}-release/">{version}</a>'
    )


class ResolverTests(unittest.TestCase):
    def setUp(self):
        self.policy = {
            "compatibility": {
                "architectures": ["arm64-v8a"],
                "dpis": ["nodpi"],
                "max_min_api": 37,
            }
        }

    def test_selects_highest_version_that_is_compatible(self):
        html = "".join(
            [
                variant(
                    "%7B%22arches_slug%22%3A%5B%22arm64-v8a%22%5D%2C"
                    "%22dpis_slug%22%3A%5B%22nodpi%22%5D%2C"
                    "%22minapi_slug%22%3A%22minapi-36%22%7D",
                    "10.4.117.936816638.14",
                    "pixel-camera-10-4-117-936816638-14",
                ),
                variant(
                    "%7B%22arches_slug%22%3A%5B%22arm64-v8a%22%5D%2C"
                    "%22dpis_slug%22%3A%5B%22nodpi%22%5D%2C"
                    "%22minapi_slug%22%3A%22minapi-37%22%7D",
                    "11.0.073.972752740.32",
                    "pixel-camera-11-0-073-972752740-32",
                ),
                variant(
                    "%7B%22arches_slug%22%3A%5B%22arm64-v8a%22%5D%2C"
                    "%22dpis_slug%22%3A%5B%22nodpi%22%5D%2C"
                    "%22minapi_slug%22%3A%22minapi-38%22%7D",
                    "12.0.001.1",
                    "pixel-camera-12-0-001-1",
                ),
            ]
        )

        candidates = resolver.discover_candidates(
            html, "https://www.apkmirror.com/apk/google-inc/camera/"
        )
        selected = resolver.choose_candidate(self.policy, candidates)

        self.assertEqual(selected.version, "11.0.073.972752740.32")
        self.assertEqual(selected.min_api, 37)
        self.assertIn("arm64-v8a", selected.architectures)

    def test_rejects_wrong_architecture_even_if_version_is_newer(self):
        html = "".join(
            [
                variant(
                    "%7B%22arches_slug%22%3A%5B%22armeabi-v7a%22%5D%2C"
                    "%22dpis_slug%22%3A%5B%22nodpi%22%5D%2C"
                    "%22minapi_slug%22%3A%22minapi-37%22%7D",
                    "99.0.0.1",
                    "pixel-camera-99-0-0-1",
                ),
                variant(
                    "%7B%22arches_slug%22%3A%5B%22arm64-v8a%22%5D%2C"
                    "%22dpis_slug%22%3A%5B%22nodpi%22%5D%2C"
                    "%22minapi_slug%22%3A%22minapi-37%22%7D",
                    "11.0.073.972752740.32",
                    "pixel-camera-11-0-073-972752740-32",
                ),
            ]
        )

        selected = resolver.choose_candidate(
            self.policy,
            resolver.discover_candidates(
                html, "https://www.apkmirror.com/apk/google-inc/camera/"
            ),
        )

        self.assertEqual(selected.version, "11.0.073.972752740.32")

    def test_tracks_ordered_fallback_candidates_with_limit(self):
        self.policy["selection"] = {"candidate_limit": 2}
        html = "".join(
            [
                variant(
                    "%7B%22arches_slug%22%3A%5B%22arm64-v8a%22%5D%2C"
                    "%22dpis_slug%22%3A%5B%22nodpi%22%5D%2C"
                    "%22minapi_slug%22%3A%22minapi-37%22%7D",
                    "11.0.3",
                    "pixel-camera-11-0-3",
                ),
                variant(
                    "%7B%22arches_slug%22%3A%5B%22arm64-v8a%22%5D%2C"
                    "%22dpis_slug%22%3A%5B%22nodpi%22%5D%2C"
                    "%22minapi_slug%22%3A%22minapi-37%22%7D",
                    "11.0.2",
                    "pixel-camera-11-0-2",
                ),
                variant(
                    "%7B%22arches_slug%22%3A%5B%22arm64-v8a%22%5D%2C"
                    "%22dpis_slug%22%3A%5B%22nodpi%22%5D%2C"
                    "%22minapi_slug%22%3A%22minapi-37%22%7D",
                    "11.0.1",
                    "pixel-camera-11-0-1",
                ),
            ]
        )

        ordered = resolver.compatible_candidates(
            self.policy,
            resolver.discover_candidates(
                html, "https://www.apkmirror.com/apk/google-inc/camera/"
            ),
        )

        self.assertEqual([item.version for item in ordered], ["11.0.3", "11.0.2"])

    def test_parses_human_readable_variant_when_json_href_is_unavailable(self):
        html = (
            '<a href="/apk/google-inc/camera/android-17-variant/">'
            '(arm64-v8a) (nodpi) (Android 17+)</a>'
            '<a href="/apk/google-inc/camera/'
            'pixel-camera-11-0-073-972752740-32-release/">'
            'Latest: Pixel Camera 11.0.073.972752740.32</a>'
        )

        candidates = resolver.discover_candidates(
            html, "https://www.apkmirror.com/apk/google-inc/camera/"
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].version, "11.0.073.972752740.32")
        self.assertEqual(candidates[0].min_api, 37)
        self.assertEqual(candidates[0].architectures, ("arm64-v8a",))
        self.assertEqual(candidates[0].dpis, ("nodpi",))

    def test_direct_variant_page_parser_collects_fallback_releases(self):
        html = "".join(
            [
                '<a href="/apk/google-inc/camera/'
                'pixel-camera-11-0-073-972752740-32-release/'
                'pixel-camera-11-0-073-972752740-32-android-apk-download/">'
                'Pixel Camera 11.0.073.972752740.32 '
                '(arm64-v8a) (nodpi) (Android 17+)</a>',
                '<a href="/apk/google-inc/camera/'
                'pixel-camera-11-0-073-967434166-31-release/'
                'pixel-camera-11-0-073-967434166-31-android-apk-download/">'
                'Pixel Camera 11.0.073.967434166.31 '
                '(arm64-v8a) (nodpi) (Android 17+)</a>',
            ]
        )

        candidates = resolver.discover_variant_page_candidates(
            html,
            "https://www.apkmirror.com/apk/google-inc/camera/",
            "arm64-v8a",
            "nodpi",
            37,
        )

        self.assertEqual(
            [item.version for item in candidates],
            [
                "11.0.073.972752740.32",
                "11.0.073.967434166.31",
            ],
        )
        self.assertTrue(
            candidates[0].release_url.endswith(
                "pixel-camera-11-0-073-972752740-32-release/"
            )
        )

    def test_build_variant_url_round_trips_target_metadata(self):
        url = resolver.build_variant_url(
            "https://www.apkmirror.com/apk/google-inc/camera/",
            "arm64-v8a",
            "nodpi",
            37,
        )
        spec = resolver._parse_variant_href(url)

        self.assertIsNotNone(spec)
        self.assertEqual(spec["arches_slug"], ["arm64-v8a"])
        self.assertEqual(spec["dpis_slug"], ["nodpi"])
        self.assertEqual(spec["minapi_slug"], "minapi-37")

    def test_fails_closed_when_no_compatible_variant_exists(self):
        html = variant(
            "%7B%22arches_slug%22%3A%5B%22arm64-v8a%22%5D%2C"
            "%22dpis_slug%22%3A%5B%22nodpi%22%5D%2C"
            "%22minapi_slug%22%3A%22minapi-38%22%7D",
            "12.0.001.1",
            "pixel-camera-12-0-001-1",
        )
        candidates = resolver.discover_candidates(
            html, "https://www.apkmirror.com/apk/google-inc/camera/"
        )

        with self.assertRaises(RuntimeError):
            resolver.choose_candidate(self.policy, candidates)


if __name__ == "__main__":
    unittest.main()
