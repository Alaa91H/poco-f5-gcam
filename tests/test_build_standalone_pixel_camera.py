import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from scripts import build_standalone_pixel_camera as builder


class StandaloneBuilderTests(unittest.TestCase):
    def test_output_name_is_stable_and_sanitized(self):
        self.assertEqual(
            builder.output_name("11.0.073.972752740.32"),
            "PixelCamera-11.0.073.972752740.32-POCO-F5-Android17.apk",
        )
        self.assertEqual(
            builder.output_name("11 beta / test"),
            "PixelCamera-11-beta-test-POCO-F5-Android17.apk",
        )

    def test_signing_certificate_parser_normalizes_digest(self):
        output = (
            "Signer #1 certificate SHA-256 digest: "
            "AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:"
            "AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99\n"
        )
        self.assertEqual(
            builder.signing_certificates(output),
            ["aabbccddeeff00112233445566778899aabbccddeeff00112233445566778899"],
        )

    def test_apk_abis_reads_native_library_directories(self):
        with tempfile.TemporaryDirectory() as temp:
            apk = Path(temp) / "sample.apk"
            with zipfile.ZipFile(apk, "w") as archive:
                archive.writestr("AndroidManifest.xml", b"manifest")
                archive.writestr("lib/arm64-v8a/libcamera.so", b"x")
                archive.writestr("lib/arm64-v8a/libhelper.so", b"x")
            self.assertEqual(builder.apk_abis(apk), ["arm64-v8a"])

    def test_aapt_badging_extracts_package_and_sdk(self):
        sample = "\n".join(
            [
                "package: name='com.google.android.GoogleCamera' versionCode='123' versionName='11.0'",
                "sdkVersion:'37'",
                "targetSdkVersion:'37'",
            ]
        )
        with mock.patch.object(builder, "run", return_value=sample):
            result = builder.aapt_badging(Path("camera.apk"), "aapt2")
        self.assertEqual(result["package_name"], "com.google.android.GoogleCamera")
        self.assertEqual(result["version_code"], "123")
        self.assertEqual(result["version_name"], "11.0")
        self.assertEqual(result["min_sdk"], "37")
        self.assertEqual(result["target_sdk"], "37")

    def test_android_package_must_be_zip_based(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "broken.apkm"
            path.write_text("not a zip", encoding="utf-8")
            with self.assertRaises(builder.BuildError):
                builder.ensure_android_zip(path)


if __name__ == "__main__":
    unittest.main()
