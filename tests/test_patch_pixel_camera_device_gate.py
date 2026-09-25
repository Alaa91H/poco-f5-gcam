import tempfile
import unittest
from pathlib import Path

from scripts import patch_pixel_camera_device_gate as patcher


class PixelCameraDeviceGatePatchTests(unittest.TestCase):
    def test_patch_smali_replaces_only_guard_branch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "klm.smali"
            target.write_text(
                "\n".join(
                    [
                        ".class public final Lklm;",
                        patcher.TARGET_METHOD,
                        "    .locals 2",
                        "    invoke-virtual {p1}, Luyv;->p()Z",
                        "    move-result p1",
                        f"    {patcher.TARGET_BRANCH}",
                        "    :goto_2",
                        "    return-void",
                        "    :cond_18",
                        "    new-instance p0, Ljava/lang/UnsupportedOperationException;",
                        f'    const-string p1, \"{patcher.DEVICE_GATE_TEXT}\"',
                        "    invoke-direct {p0, p1}, Ljava/lang/UnsupportedOperationException;-><init>(Ljava/lang/String;)V",
                        "    throw p0",
                        ".end method",
                    ]
                ),
                encoding="utf-8",
            )
            report = patcher.patch_smali(root)
            patched = target.read_text(encoding="utf-8")
            self.assertIn("    nop", patched)
            self.assertNotIn(patcher.TARGET_BRANCH, patched)
            self.assertIn(patcher.DEVICE_GATE_TEXT, patched)
            self.assertEqual(report["smali_file"], "klm.smali")

    def test_patch_smali_fails_closed_on_changed_branch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "klm.smali"
            target.write_text(
                "\n".join(
                    [
                        ".class public final Lklm;",
                        patcher.TARGET_METHOD,
                        "    .locals 2",
                        "    if-eqz p1, :cond_99",
                        "    return-void",
                        f'    const-string p1, \"{patcher.DEVICE_GATE_TEXT}\"',
                        ".end method",
                    ]
                ),
                encoding="utf-8",
            )
            with self.assertRaises(patcher.PatchError):
                patcher.patch_smali(root)

    def test_signer_digest_parser_normalizes(self):
        output = (
            "Signer #1 certificate SHA-256 digest: "
            "AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:"
            "AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99\n"
        )
        self.assertEqual(
            patcher.signer_digests(output),
            ["aabbccddeeff00112233445566778899aabbccddeeff00112233445566778899"],
        )


if __name__ == "__main__":
    unittest.main()
