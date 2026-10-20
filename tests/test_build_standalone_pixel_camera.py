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
            "PixelCamera-11.0.073.972752740.32-POCO-F5-Android13plus.apk",
        )
        self.assertEqual(
            builder.output_name("11 beta / test"),
            "PixelCamera-11-beta-test-POCO-F5-Android13plus.apk",
        )
        self.assertEqual(
            builder.output_name("11.0.073.972752740.32", min_sdk=37),
            "PixelCamera-11.0.073.972752740.32-POCO-F5-Android17plus.apk",
        )

    def test_feature_profile_validation_accepts_manual_controls_only(self):
        self.assertIsNone(builder.validate_feature_profile(None))
        self.assertEqual(
            builder.validate_feature_profile("manual_controls"),
            "manual_controls",
        )
        with self.assertRaisesRegex(builder.BuildError, "unsupported feature profile"):
            builder.validate_feature_profile("unknown")

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

    def test_signing_certificate_parser_accepts_v3_signer_prefix(self):
        output = (
            "V3.0 Signer: certificate SHA-256 digest: "
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

    def test_manifest_patch_declares_optional_opencl_libraries(self):
        source = """<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.google.android.GoogleCamera">
    <application android:label="Pixel Camera" />
</manifest>
"""
        patched, metadata = builder.patch_manifest_xml_text(source)

        root = builder.ET.fromstring(patched)
        application = root.find("application")
        self.assertIsNotNone(application)
        declarations = {
            element.get(builder.ANDROID_NAME): element.get(builder.ANDROID_REQUIRED)
            for element in application.findall("uses-native-library")
        }
        self.assertEqual(
            declarations,
            {
                "libOpenCL.so": "false",
                "libOpenCL-car.so": "false",
                "libOpenCL-pixel.so": "false",
            },
        )
        self.assertEqual(
            metadata["libraries"],
            list(builder.OPENCL_NATIVE_LIBRARIES),
        )
        self.assertFalse(metadata["required"])
        self.assertFalse(metadata["allow_native_heap_pointer_tagging_changed"])

    def test_manifest_patch_makes_existing_opencl_declaration_optional(self):
        source = """<manifest xmlns:android="http://schemas.android.com/apk/res/android">
    <application>
        <uses-native-library android:name="libOpenCL.so" android:required="true" />
    </application>
</manifest>
"""
        patched, metadata = builder.patch_manifest_xml_text(source)
        root = builder.ET.fromstring(patched)
        application = root.find("application")
        declarations = [
            element
            for element in application.findall("uses-native-library")
            if element.get(builder.ANDROID_NAME) == "libOpenCL.so"
        ]
        self.assertEqual(len(declarations), 1)
        self.assertEqual(declarations[0].get(builder.ANDROID_REQUIRED), "false")
        self.assertEqual(metadata["forced_optional"], ["libOpenCL.so"])

    def test_manifest_patch_fails_closed_on_duplicate_opencl_declaration(self):
        source = """<manifest xmlns:android="http://schemas.android.com/apk/res/android">
    <application>
        <uses-native-library android:name="libOpenCL.so" android:required="false" />
        <uses-native-library android:name="libOpenCL.so" android:required="false" />
    </application>
</manifest>
"""
        with self.assertRaisesRegex(builder.BuildError, "duplicate OpenCL"):
            builder.patch_manifest_xml_text(source)

    def test_manifest_patch_fails_closed_without_single_application(self):
        with self.assertRaisesRegex(builder.BuildError, "exactly one <application>"):
            builder.patch_manifest_xml_text(
                '<manifest xmlns:android="http://schemas.android.com/apk/res/android" />'
            )

    def test_manifest_patch_uses_direct_arsclib_helper_without_xml_decode(self):
        helper_output = "\n".join(
            [
                "status=patched",
                "added=libOpenCL.so,libOpenCL-car.so,libOpenCL-pixel.so",
                "forced_optional=",
                "required=false",
                "allow_native_heap_pointer_tagging_changed=false",
                "min_sdk_before=37",
                "min_sdk_after=33",
                "target_sdk_before=37",
                "target_sdk_after=37",
                "min_sdk_requested=33",
            ]
        )
        with mock.patch.object(
            builder.shutil,
            "which",
            return_value="/usr/bin/javac",
        ), mock.patch.object(
            builder,
            "run",
            side_effect=["", helper_output],
        ) as run_mock, mock.patch.object(
            builder,
            "ensure_android_zip",
        ):
            result = builder.patch_manifest_with_apkeditor(
                Path("input.apk"),
                Path("output.apk"),
                apkeditor=Path("APKEditor.jar"),
                java="/usr/bin/java",
                min_sdk=33,
            )

        self.assertEqual(result["implementation"], "arsclib_binary_manifest")
        self.assertEqual(
            result["added"],
            list(builder.OPENCL_NATIVE_LIBRARIES),
        )
        self.assertEqual(result["min_sdk_before"], 37)
        self.assertEqual(result["min_sdk_after"], 33)
        self.assertEqual(result["target_sdk_after"], 37)
        commands = [call.args[0] for call in run_mock.call_args_list]
        rendered = [" ".join(map(str, command)) for command in commands]
        self.assertTrue(any("OpenClManifestPatcher.java" in item for item in rendered))
        self.assertTrue(any("OpenClManifestPatcher" in item for item in rendered))
        self.assertTrue(any(item.endswith(" 33") for item in rendered))
        self.assertFalse(any(" -jar " in (" " + item + " ") for item in rendered))
        self.assertFalse(any(" -t xml " in (" " + item + " ") for item in rendered))

    def test_manifest_sdk_helper_rejects_missing_persisted_value(self):
        helper_output = "\n".join(
            [
                "status=patched",
                "added=",
                "forced_optional=",
                "required=false",
                "allow_native_heap_pointer_tagging_changed=false",
                "min_sdk_before=37",
                "min_sdk_after=37",
                "target_sdk_before=37",
                "target_sdk_after=37",
                "min_sdk_requested=33",
            ]
        )
        with mock.patch.object(
            builder.shutil,
            "which",
            return_value="/usr/bin/javac",
        ), mock.patch.object(
            builder,
            "run",
            side_effect=["", helper_output],
        ), mock.patch.object(
            builder,
            "ensure_android_zip",
        ):
            with self.assertRaisesRegex(builder.BuildError, "minSdkVersion 33"):
                builder.patch_manifest_with_apkeditor(
                    Path("input.apk"),
                    Path("output.apk"),
                    apkeditor=Path("APKEditor.jar"),
                    java="/usr/bin/java",
                    min_sdk=33,
                )

    def test_final_manifest_verification_accepts_optional_opencl_entries(self):
        sample = """
E: manifest (line=1)
  E: application (line=2)
    E: uses-native-library (line=3)
      A: android:name(0x01010003)="libOpenCL.so" (Raw: "libOpenCL.so")
      A: android:required(0x0101028e)=(type 0x12)0x0
    E: uses-native-library (line=4)
      A: android:name(0x01010003)="libOpenCL-car.so" (Raw: "libOpenCL-car.so")
      A: android:required(0x0101028e)=(type 0x12)0x0
    E: uses-native-library (line=5)
      A: android:name(0x01010003)="libOpenCL-pixel.so" (Raw: "libOpenCL-pixel.so")
      A: android:required(0x0101028e)=(type 0x12)0x0
"""
        with mock.patch.object(builder, "run", return_value=sample):
            result = builder.verify_optional_opencl_manifest(
                Path("camera.apk"),
                "aapt2",
            )
        self.assertEqual(result["status"], "verified")
        self.assertEqual(
            result["libraries"],
            list(builder.OPENCL_NATIVE_LIBRARIES),
        )
        self.assertFalse(result["required"])

    def test_final_manifest_verification_handles_aapt2_boolean_literals(self):
        sample = '\n'.join(
            '    E: uses-native-library (line=3)\n'
            f'      A: android:name(0x01010003)="{name}"\n'
            '      A: http://schemas.android.com/apk/res/android:required(0x0101028e)=false'
            for name in builder.OPENCL_NATIVE_LIBRARIES
        )
        with mock.patch.object(builder, "run", return_value=sample):
            self.assertEqual(builder.verify_optional_opencl_manifest(
                Path("camera.apk"), "aapt2")["status"], "verified")
        for invalid in ('true', 'falsehood'):
            with mock.patch.object(builder, "run", return_value=sample.replace('=false', '=' + invalid)):
                with self.assertRaises(builder.BuildError):
                    builder.verify_optional_opencl_manifest(Path("camera.apk"), "aapt2")

    def test_final_manifest_verification_rejects_required_opencl_entry(self):
        sample = """
E: manifest (line=1)
  E: application (line=2)
    E: uses-native-library (line=3)
      A: android:name(0x01010003)="libOpenCL.so" (Raw: "libOpenCL.so")
      A: android:required(0x0101028e)=(type 0x12)0xffffffff
    E: uses-native-library (line=4)
      A: android:name(0x01010003)="libOpenCL-car.so" (Raw: "libOpenCL-car.so")
      A: android:required(0x0101028e)=(type 0x12)0x0
    E: uses-native-library (line=5)
      A: android:name(0x01010003)="libOpenCL-pixel.so" (Raw: "libOpenCL-pixel.so")
      A: android:required(0x0101028e)=(type 0x12)0x0
"""
        with mock.patch.object(builder, "run", return_value=sample):
            with self.assertRaisesRegex(builder.BuildError, "does not mark"):
                builder.verify_optional_opencl_manifest(
                    Path("camera.apk"),
                    "aapt2",
                )

    def test_final_manifest_sdk_verification_accepts_android_13_values(self):
        sample = """
E: manifest (line=1)
  E: uses-sdk (line=2)
    A: http://schemas.android.com/apk/res/android:minSdkVersion(0x0101020c)=(type 0x10)0x21
    A: http://schemas.android.com/apk/res/android:targetSdkVersion(0x01010270)=(type 0x10)0x25
"""
        with mock.patch.object(builder, "run", return_value=sample):
            result = builder.verify_manifest_sdk(
                Path("camera.apk"),
                "aapt2",
                expected_min_sdk=33,
            )
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["min_sdk"], 33)
        self.assertEqual(result["target_sdk"], 37)

    def test_final_manifest_sdk_verification_accepts_decimal_literals(self):
        sample = """
    A: android:minSdkVersion(0x0101020c)=33
    A: android:targetSdkVersion(0x01010270)="37"
"""
        with mock.patch.object(builder, "run", return_value=sample):
            result = builder.verify_manifest_sdk(
                Path("camera.apk"),
                "aapt2",
                expected_min_sdk=33,
            )
        self.assertEqual(result["min_sdk"], 33)
        self.assertEqual(result["target_sdk"], 37)

    def test_final_manifest_sdk_verification_rejects_wrong_min_sdk(self):
        sample = """
    A: android:minSdkVersion(0x0101020c)=(type 0x10)0x25
    A: android:targetSdkVersion(0x01010270)=(type 0x10)0x25
"""
        with mock.patch.object(builder, "run", return_value=sample):
            with self.assertRaisesRegex(builder.BuildError, "minSdkVersion is 37"):
                builder.verify_manifest_sdk(
                    Path("camera.apk"),
                    "aapt2",
                    expected_min_sdk=33,
                )

    def test_final_manifest_sdk_verification_rejects_missing_min_sdk(self):
        with mock.patch.object(builder, "run", return_value="E: manifest (line=1)"):
            with self.assertRaisesRegex(builder.BuildError, "no numeric minSdkVersion"):
                builder.verify_manifest_sdk(
                    Path("camera.apk"),
                    "aapt2",
                    expected_min_sdk=33,
                )

    def test_android_package_must_be_zip_based(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "broken.apkm"
            path.write_text("not a zip", encoding="utf-8")
            with self.assertRaises(builder.BuildError):
                builder.ensure_android_zip(path)


if __name__ == "__main__":
    unittest.main()
