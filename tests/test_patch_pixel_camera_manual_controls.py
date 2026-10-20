import unittest

from scripts.patch_pixel_camera_device_gate import PatchError
from scripts.patch_pixel_camera_manual_controls import (
    HELPER,
    MANUAL_FLAGS,
    patch_smali,
)

SAMPLE = """\
.class public final Lksf;
.super Ljava/lang/Object;
.source "PG"

.method public final q(Lkps;)Z
    .registers 2

    const/4 v0, 0x0
    return v0
.end method

.method public final x(Lkps;)Z
    .registers 2

    const/4 v0, 0x1
    return v0
.end method
"""


class ManualControlsPatchTests(unittest.TestCase):
    def test_wraps_both_reviewed_queries_and_adds_helper(self):
        patched, report = patch_smali(SAMPLE)

        self.assertIn(".method public final q$pocoBase(Lkps;)Z", patched)
        self.assertIn(".method public final x$pocoBase(Lkps;)Z", patched)
        self.assertEqual(
            patched.count("Lpoco/ManualFeatures;->isEnabled(Lkps;)Z"),
            2,
        )
        self.assertEqual(report["profile"], "manual_controls")
        self.assertEqual(report["flags"], list(MANUAL_FLAGS))
        self.assertEqual(
            report["methods"],
            ["Lksf;->q(Lkps;)Z", "Lksf;->x(Lkps;)Z"],
        )
        self.assertFalse(report["third_party_code_added"])
        self.assertFalse(report["ads_added"])
        self.assertFalse(report["developer_branding_added"])

    def test_helper_contains_exact_flags_and_marble_gate(self):
        for flag in MANUAL_FLAGS:
            self.assertIn(flag, HELPER)
        self.assertIn('const-string v1, "marble"', HELPER)
        self.assertIn("startsWith(Ljava/lang/String;)Z", HELPER)
        self.assertNotIn("BSG", HELPER)
        self.assertNotIn("bsg", HELPER)
        self.assertNotIn("http", HELPER)

    def test_missing_query_fails_closed(self):
        source = SAMPLE.replace(
            ".method public final x(Lkps;)Z",
            ".method public final y(Lkps;)Z",
        )
        with self.assertRaisesRegex(PatchError, "x\\(Lkps;\\)Z"):
            patch_smali(source)

    def test_duplicate_helper_fails_closed(self):
        source = SAMPLE + "\n# Lpoco/ManualFeatures;\n"
        with self.assertRaisesRegex(PatchError, "already present"):
            patch_smali(source)


if __name__ == "__main__":
    unittest.main()
