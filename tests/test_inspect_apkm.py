import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.inspect_apkm import inspect_package


def write_apk(path: Path, *, pairip: bool = False, abi: str = "arm64-v8a") -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("AndroidManifest.xml", b"binary-manifest-placeholder")
        dex = b"dex\n035\x00" + (b"com/pairip/LicenseClient" if pairip else b"camera")
        archive.writestr("classes.dex", dex)
        archive.writestr(f"lib/{abi}/libcamera_test.so", b"ELF" + b"\x00" * 128)
        archive.writestr("assets/model.bin", b"x" * 2048)
        archive.writestr("res/raw/config.bin", b"y" * 512)
        archive.writestr("resources.arsc", b"z" * 256)


class InspectApkmTests(unittest.TestCase):
    def test_single_apk_bill_of_materials(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "base.apk"
            write_apk(path)

            report = inspect_package(path)

            self.assertEqual(report["input"]["kind"], "apk")
            self.assertEqual(report["summary"]["apk_count"], 1)
            self.assertFalse(report["summary"]["pairip_detected"])
            apk = report["apks"][0]
            self.assertEqual(apk["abis"], ["arm64-v8a"])
            self.assertEqual(apk["native_library_count"], 1)
            self.assertGreater(apk["categories"]["assets"]["uncompressed_bytes"], 0)
            self.assertGreater(apk["categories"]["dex"]["uncompressed_bytes"], 0)
            self.assertEqual(
                report["input"]["sha256"],
                apk["sha256"],
            )

    def test_apkm_detects_splits_and_pairip_indicator(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base = root / "base.apk"
            feature = root / "feature.apk"
            write_apk(base)
            write_apk(feature, pairip=True)

            bundle = root / "camera.apkm"
            with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.write(base, "base.apk")
                archive.write(feature, "splits/feature.apk")
                archive.writestr("meta.sai_v1.json", json.dumps({"name": "test"}))

            report = inspect_package(bundle)

            self.assertEqual(report["input"]["kind"], "bundle")
            self.assertEqual(report["summary"]["apk_count"], 2)
            self.assertEqual(report["bundle"]["apk_member_count"], 2)
            self.assertTrue(report["summary"]["pairip_detected"])
            self.assertEqual(
                report["summary"]["pairip_apks"],
                ["splits/feature.apk"],
            )
            feature_report = next(
                item
                for item in report["apks"]
                if item["name"] == "splits/feature.apk"
            )
            self.assertTrue(feature_report["pairip"]["detected"])
            self.assertIn("dex:classes.dex", feature_report["pairip"]["indicators"])

    def test_bundle_rejects_archive_without_apks(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "empty.apkm"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("metadata.json", "{}")

            with self.assertRaisesRegex(ValueError, "contains no APK members"):
                inspect_package(path)


if __name__ == "__main__":
    unittest.main()
