import io
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts import analyze_pixel_camera_native as analyzer
from scripts import patch_pixel_camera_device_gate as patcher


def minimal_elf_with_tuning_marker() -> bytes:
    size = 0x3000
    blob = bytearray(size)
    blob[:4] = b"\x7fELF"
    blob[4] = 2
    blob[5] = 1
    struct.pack_into("<Q", blob, 32, 64)
    struct.pack_into("<H", blob, 54, 56)
    struct.pack_into("<H", blob, 56, 1)
    struct.pack_into(
        "<IIQQQQQQ",
        blob,
        64,
        1,
        5,
        0,
        0,
        0,
        size,
        size,
        0x1000,
    )
    marker = patcher.TUNING_DIAGNOSTIC_STRINGS[0].encode("utf-8")
    blob[0x2000 : 0x2000 + len(marker)] = marker
    struct.pack_into("<I", blob, 0x1000, 0xB0000000)
    struct.pack_into("<I", blob, 0x1004, 0x91000000)

    # Simulate a pointer table entry to the string plus code referencing
    # that pointer slot through ADRP+ADD. Real Android ELF libraries often
    # reach logging strings through relocated data rather than direct ADRP+ADD.
    struct.pack_into("<Q", blob, 0x2200, 0x2000)
    struct.pack_into("<I", blob, 0x1010, 0xB0000001)
    struct.pack_into("<I", blob, 0x1014, 0x91080021)
    return bytes(blob)


class PixelCameraNativeAnalyzerTests(unittest.TestCase):
    def test_finds_library_inside_nested_split_apk(self):
        library = minimal_elf_with_tuning_marker()

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            split = io.BytesIO()
            with zipfile.ZipFile(split, "w") as apk:
                apk.writestr(patcher.GCASTARTUP_LIBRARY, library)

            bundle = root / "camera.apkm"
            with zipfile.ZipFile(bundle, "w") as outer:
                outer.writestr("base.apk", b"not-a-zip")
                outer.writestr("split_startup_jni_split_module.apk", split.getvalue())

            found, location = analyzer.find_native_library(bundle)

            self.assertEqual(found, library)
            self.assertEqual(
                location["nested_apk"],
                "split_startup_jni_split_module.apk",
            )
            self.assertEqual(location["member"], patcher.GCASTARTUP_LIBRARY)

    def test_analyze_package_is_read_only_and_reports_tuning_xref(self):
        library = minimal_elf_with_tuning_marker()

        with tempfile.TemporaryDirectory() as temp:
            apk_path = Path(temp) / "camera.apk"
            with zipfile.ZipFile(apk_path, "w") as apk:
                apk.writestr(patcher.GCASTARTUP_LIBRARY, library)

            report = analyzer.analyze_package(apk_path)

            self.assertFalse(report["modifications_performed"])
            self.assertEqual(report["status"], "diagnostic_only")
            unknown = report["tuning"]["strings"][
                patcher.TUNING_DIAGNOSTIC_STRINGS[0]
            ]
            self.assertEqual(unknown["count"], 1)
            occurrence = unknown["occurrences"][0]
            self.assertEqual(
                occurrence["xref_offsets"],
                ["0x1000", "0x1004"],
            )
            self.assertIn(
                {
                    "kind": "raw_u64",
                    "file_offset": "0x2200",
                    "vaddr": "0x2200",
                },
                occurrence["pointer_slots"],
            )
            self.assertEqual(
                occurrence["resolved_xref_offsets"],
                ["0x1000", "0x1004", "0x1010", "0x1014"],
            )

    def test_rejects_duplicate_native_library_matches(self):
        library = minimal_elf_with_tuning_marker()

        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp) / "camera.apkm"
            nested = io.BytesIO()
            with zipfile.ZipFile(nested, "w") as apk:
                apk.writestr(patcher.GCASTARTUP_LIBRARY, library)

            with zipfile.ZipFile(bundle, "w") as outer:
                outer.writestr(patcher.GCASTARTUP_LIBRARY, library)
                outer.writestr("split.apk", nested.getvalue())

            with self.assertRaisesRegex(patcher.PatchError, "exactly one"):
                analyzer.find_native_library(bundle)


if __name__ == "__main__":
    unittest.main()
