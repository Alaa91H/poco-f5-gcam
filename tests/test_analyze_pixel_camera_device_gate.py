import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts import analyze_pixel_camera_device_gate as analyzer


class DeviceGateAnalyzerTests(unittest.TestCase):
    def test_method_bounds_extracts_complete_method(self):
        lines = [
            ".class public Lx;",
            ".method public constructor <init>()V",
            "    .locals 1",
            '    const-string v0, "Device is not recognized or not supported"',
            "    throw v0",
            ".end method",
        ]
        self.assertEqual(analyzer.method_bounds(lines, 3), (1, 5))

    def test_analyze_decoded_finds_exact_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            smali = root / "smali_classes2" / "klm.smali"
            smali.parent.mkdir(parents=True)
            smali.write_text(
                "\n".join(
                    [
                        ".class public final Lklm;",
                        ".method public constructor <init>()V",
                        "    .locals 2",
                        '    const-string v0, \"Device is not recognized or not supported\"',
                        "    new-instance v1, Ljava/lang/UnsupportedOperationException;",
                        "    throw v1",
                        ".end method",
                    ]
                ),
                encoding="utf-8",
            )
            report = analyzer.analyze_decoded(root)
            self.assertEqual(report["match_count"], 1)
            self.assertEqual(report["matches"][0]["file"], "smali_classes2/klm.smali")
            self.assertIn("UnsupportedOperationException", report["matches"][0]["method_smali"])

    def test_extract_base_apk_from_bundle(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bundle = root / "camera.apkm"
            with zipfile.ZipFile(bundle, "w") as archive:
                archive.writestr("base.apk", b"base-bytes")
                archive.writestr("split_feature.apk", b"split-bytes")
            output_dir = root / "out"
            output_dir.mkdir()
            base = analyzer.extract_base_apk(bundle, output_dir)
            self.assertEqual(base.read_bytes(), b"base-bytes")


if __name__ == "__main__":
    unittest.main()
