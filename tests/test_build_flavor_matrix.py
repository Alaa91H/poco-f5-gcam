import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import build_flavor_matrix as matrix

REPO = Path(__file__).resolve().parents[1]


class FlavorMatrixTests(unittest.TestCase):
    def test_policy_has_four_pixel_flavors(self):
        flavors = matrix.load_flavor_policy(
            REPO / "device/marble/build-flavors.json"
        )
        self.assertEqual(
            [flavor["id"] for flavor in flavors],
            [
                "pixel-a17",
                "pixel-a13",
                "pixel-manual-a17",
                "pixel-manual-a13",
            ],
        )
        self.assertEqual(
            [flavor["kind"] for flavor in flavors],
            ["pixel_camera_patched"] * 4,
        )
        self.assertEqual(
            [flavor.get("feature_profile") for flavor in flavors],
            [None, None, "manual_controls", "manual_controls"],
        )

    def test_selected_flavors_preserves_requested_order(self):
        flavors = matrix.load_flavor_policy(
            REPO / "device/marble/build-flavors.json"
        )
        selected = matrix.selected_flavors(
            flavors, ["pixel-manual-a13", "pixel-a17"]
        )
        self.assertEqual(
            [flavor["id"] for flavor in selected],
            ["pixel-manual-a13", "pixel-a17"],
        )

    def test_unknown_flavor_fails(self):
        flavors = matrix.load_flavor_policy(
            REPO / "device/marble/build-flavors.json"
        )
        with self.assertRaisesRegex(matrix.FlavorError, "unknown flavour"):
            matrix.selected_flavors(flavors, ["missing"])

    def test_output_templates_are_distinct(self):
        flavors = matrix.load_flavor_policy(
            REPO / "device/marble/build-flavors.json"
        )
        names = {
            matrix.render_output_name(
                flavor["output_template"],
                version="11.1.040.982810059.19",
                android_label=flavor["android_label"],
            )
            for flavor in flavors
        }
        self.assertEqual(
            names,
            {
                "PixelCamera-11.1.040.982810059.19-POCO-F5-Android17plus.apk",
                "PixelCamera-11.1.040.982810059.19-POCO-F5-Android13plus.apk",
                "PixelCamera-11.1.040.982810059.19-POCO-F5-Android17plus-Manual.apk",
                "PixelCamera-11.1.040.982810059.19-POCO-F5-Android13plus-Manual.apk",
            },
        )

    def test_delivery_captions_are_single_line_and_bounded(self):
        normal = {
            "kind": "pixel_camera_patched",
            "label": "Pixel Camera 11.1 - Android 13+",
            "feature_profile": None,
            "version_name": "11.1.040.982810059.19",
            "min_sdk": 33,
            "size_bytes": 123,
            "sha256": "a" * 64,
            "package_name": "com.google.android.GoogleCamera",
        }
        caption = matrix.delivery_caption(normal, "repository-keystore")
        self.assertNotIn("\n", caption)
        self.assertIn("Android: 13+ / API 33", caption)
        self.assertLessEqual(len(caption), 1024)

        manual = {
            **normal,
            "label": "Pixel Camera 11.1 - Android 13+ (manual controls)",
            "feature_profile": "manual_controls",
        }
        manual_caption = matrix.delivery_caption(manual, "repository-keystore")
        self.assertNotIn("\n", manual_caption)
        self.assertIn("Manual focus/shutter/ISO and lens selector", manual_caption)
        self.assertIn("no third-party code or ads", manual_caption)
        self.assertLessEqual(len(manual_caption), 1024)

    def test_policy_rejects_unknown_feature_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "flavors.json"
            path.write_text(
                json.dumps(
                    {
                        "flavors": [
                            {
                                "id": "bad",
                                "kind": "pixel_camera_patched",
                                "label": "bad",
                                "manifest_min_sdk": 37,
                                "expected_min_sdk": 37,
                                "output_template": "bad.apk",
                                "feature_profile": "unknown",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                matrix.FlavorError, "unsupported feature_profile"
            ):
                matrix.load_flavor_policy(path)

    def _fake_report(self, min_sdk: int = 33) -> dict:
        return {
            "output": {
                "path": "unused",
                "size_bytes": 123,
                "sha256": "b" * 64,
                "package_name": "com.google.android.GoogleCamera",
                "version_code": "1",
                "version_name": "11.1.040.982810059.19",
                "min_sdk": min_sdk,
                "target_sdk": 37,
            }
        }

    def test_matrix_rejects_changed_target_sdk(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.apkm"
            source.write_bytes(b"source")
            keystore = root / "signing.jks"
            keystore.write_bytes(b"keystore")
            upstream = root / "pixel-camera.json"
            upstream.write_text(
                json.dumps({"selected": {"version": "11.1.040.982810059.19"}}),
                encoding="utf-8",
            )
            flavor = {
                "id": "pixel-a13",
                "kind": "pixel_camera_patched",
                "label": "Pixel Camera 11.1 - Android 13+",
                "manifest_min_sdk": 33,
                "expected_min_sdk": 33,
                "output_template": "camera-{version}.apk",
                "feature_profile": None,
            }
            report = self._fake_report(min_sdk=33)
            report["output"]["target_sdk"] = 36
            with mock.patch.object(
                matrix,
                "load_flavor_policy",
                return_value=[flavor],
            ), mock.patch.object(
                matrix,
                "build_standalone",
                return_value=report,
            ), mock.patch.object(
                matrix,
                "_require_tool",
                side_effect=lambda value, name: value or name,
            ):
                with self.assertRaisesRegex(matrix.FlavorError, "targetSdkVersion"):
                    matrix.build_flavor_matrix(
                        flavor_policy_path=root / "unused.json",
                        upstream_path=upstream,
                        flavor_ids=["pixel-a13"],
                        pixel_source=source,
                        output_dir=root / "out",
                        apkeditor="apkeditor.jar",
                        baksmali="baksmali.jar",
                        smali="smali.jar",
                        java="java",
                        zipalign="zipalign",
                        apksigner="apksigner",
                        aapt2="aapt2",
                        keystore=keystore,
                        key_alias="alias",
                        ks_pass_env="KS",
                        key_pass_env="KP",
                    )

    def test_matrix_calls_pixel_builder_for_all_four_flavors(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.apkm"
            source.write_bytes(b"source")
            keystore = root / "signing.jks"
            keystore.write_bytes(b"keystore")
            upstream = root / "pixel-camera.json"
            upstream.write_text(
                json.dumps(
                    {"selected": {"version": "11.1.040.982810059.19"}}
                ),
                encoding="utf-8",
            )
            output_dir = root / "out"

            with mock.patch.object(
                matrix,
                "build_standalone",
                side_effect=lambda *args, **kwargs: self._fake_report(
                    min_sdk=kwargs["expected_min_sdk"]
                ),
            ) as build_mock, mock.patch.object(
                matrix,
                "_require_tool",
                side_effect=lambda value, name: value or name,
            ):
                manifest = matrix.build_flavor_matrix(
                    flavor_policy_path=REPO / "device/marble/build-flavors.json",
                    upstream_path=upstream,
                    flavor_ids=[],
                    pixel_source=source,
                    output_dir=output_dir,
                    apkeditor="apkeditor.jar",
                    baksmali="baksmali.jar",
                    smali="smali.jar",
                    java="java",
                    zipalign="zipalign",
                    apksigner="apksigner",
                    aapt2="aapt2",
                    keystore=keystore,
                    key_alias="alias",
                    ks_pass_env="KS",
                    key_pass_env="KP",
                )

        self.assertEqual(build_mock.call_count, 4)
        calls = [call.kwargs for call in build_mock.call_args_list]
        self.assertEqual(
            [call["manifest_min_sdk"] for call in calls],
            [37, 33, 37, 33],
        )
        self.assertEqual(
            [call["expected_min_sdk"] for call in calls],
            [37, 33, 37, 33],
        )
        self.assertEqual(
            [call["feature_profile"] for call in calls],
            [None, None, "manual_controls", "manual_controls"],
        )
        self.assertEqual(
            [entry["id"] for entry in manifest["flavors"]],
            [
                "pixel-a17",
                "pixel-a13",
                "pixel-manual-a17",
                "pixel-manual-a13",
            ],
        )
        self.assertTrue(
            all(
                entry["signing_mode"] == "repository-keystore"
                for entry in manifest["flavors"]
            )
        )
        self.assertTrue(
            all(entry["delivery_caption"] for entry in manifest["flavors"])
        )


if __name__ == "__main__":
    unittest.main()
