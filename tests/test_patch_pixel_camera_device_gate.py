import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts import patch_pixel_camera_device_gate as patcher


SAMPLE = r'''.method public constructor <init>(Luyv;Luyu;Lqxe;Lacku;Lklk;)V
    .locals 9

    invoke-virtual {p1}, Luyv;->p()Z

    move-result p1

    if-eqz p1, :cond_18

    :goto_2
    sget-object p1, Lkjl;->s:Lkiy;
    const/4 v2, -0x1
    invoke-static {v2}, Ljava/lang/Integer;->valueOf(I)Ljava/lang/Integer;
    move-result-object p2
    invoke-virtual {p0, p1, p2}, Lklm;->e(Lkiy;Ljava/lang/Integer;)V
    return-void

    :cond_18
    new-instance p0, Ljava/lang/UnsupportedOperationException;

    const-string p1, "Device is not recognized or not supported"

    invoke-direct {p0, p1}, Ljava/lang/UnsupportedOperationException;-><init>(Ljava/lang/String;)V

    throw p0
.end method
'''


FLAG_QUERY_SAMPLE = r'''.class public final Lklm;
.super Ljava/lang/Object;

.method public final q(Lkiz;)Z
    .locals 3

    iget-object v0, p0, Lklm;->b:Ljava/util/Map;
    invoke-interface {v0, p1}, Ljava/util/Map;->get(Ljava/lang/Object;)Ljava/lang/Object;
    move-result-object v0
    check-cast v0, Lovu;
    iget-object v0, v0, Lovu;->a:Ljava/lang/Object;
    check-cast v0, Ljava/lang/Boolean;
    invoke-static {v0}, Lklk;->e(Ljava/lang/Boolean;)Z
    move-result v0
    return v0
.end method

.method public final x(Lkiz;)Z
    .locals 3

    iget-object p0, p0, Lklm;->b:Ljava/util/Map;
    invoke-interface {p0, p1}, Ljava/util/Map;->get(Ljava/lang/Object;)Ljava/lang/Object;
    move-result-object p0
    check-cast p0, Lovu;
    iget-object p0, p0, Lovu;->a:Ljava/lang/Object;
    check-cast p0, Ljava/lang/Boolean;
    invoke-static {p0}, Lklk;->e(Ljava/lang/Boolean;)Z
    move-result p0
    return p0
.end method
'''



FLAG_QUERY_REGISTERS_SAMPLE = FLAG_QUERY_SAMPLE.replace(".locals 3", ".registers 4")


GCAM_INIT_SAMPLE = r'''.class public final Lmjy;
.super Ljava/lang/Object;

.method public final synthetic a()Ljava/lang/Object;
    .locals 37

    :cond_29
    if-eqz v27, :cond_2a

    iget-wide v2, v1, Lcom/google/googlex/gcam/InitParams;->a:J

    invoke-static {v2, v3, v1, v12}, Lcom/google/googlex/gcam/GcamModuleJNI;->InitParams_almond_use_tpu_set(JLcom/google/googlex/gcam/InitParams;Z)V

    :cond_2a
    invoke-virtual {v2}, Ljava/lang/Boolean;->booleanValue()Z

    move-result v2

    if-eqz v2, :cond_2c

    iget-wide v2, v1, Lcom/google/googlex/gcam/InitParams;->a:J

    invoke-static {v2, v3, v1, v12}, Lcom/google/googlex/gcam/GcamModuleJNI;->InitParams_finish_tomte_grain_enabled_set(JLcom/google/googlex/gcam/InitParams;Z)V

    :cond_2c
    iget-wide v2, v1, Lcom/google/googlex/gcam/InitParams;->a:J

    invoke-static/range {v14 .. v19}, Lcom/google/googlex/gcam/GcamModuleJNI;->Gcam_Create(JLcom/google/googlex/gcam/InitParams;JLcom/google/googlex/gcam/StaticMetadataVector;)J

    move-result-wide v0

    return-object v5
.end method
'''


class PixelCameraDeviceGatePatchTests(unittest.TestCase):
    def test_redirects_unsupported_device_throw_to_common_finalization(self):
        patched, metadata = patcher.patch_smali_text(SAMPLE)

        self.assertNotIn(patcher.MARKER, patched)
        self.assertIn(":cond_18\n\n    goto/32 :goto_2", patched)
        self.assertEqual(metadata["unsupported_label"], "cond_18")
        self.assertEqual(metadata["finalization_label"], "goto_2")
        self.assertIn("constructor <init>", metadata["method"])

    def test_fails_closed_without_exact_marker(self):
        with self.assertRaisesRegex(patcher.PatchError, "expected one marker"):
            patcher.patch_smali_text(SAMPLE.replace(patcher.MARKER, "different"))

    def test_fails_closed_if_marker_moves_outside_constructor(self):
        changed = SAMPLE.replace(" constructor <init>", " public unsupportedCheck")
        with self.assertRaisesRegex(patcher.PatchError, "not inside a constructor"):
            patcher.patch_smali_text(changed)

    def test_fails_closed_if_throw_shape_changes(self):
        changed = SAMPLE.replace("throw p0", "return-void")
        with self.assertRaisesRegex(patcher.PatchError, "no throw"):
            patcher.patch_smali_text(changed)

    def test_injects_nontensor_guards_into_boolean_flag_queries(self):
        patched, metadata = patcher.patch_nontensor_flag_queries(FLAG_QUERY_SAMPLE)

        self.assertIn('const-string v1, "camera.lasagna"', patched)
        self.assertIn('const-string v1, "use_tpu"', patched)
        self.assertIn('const-string v1, "darwinn"', patched)
        self.assertIn('const-string v1, "edgetpu"', patched)
        self.assertIn(":poco_nontensor_false_q", patched)
        self.assertIn(":poco_nontensor_orig_q", patched)
        self.assertIn(":poco_nontensor_false_x", patched)
        self.assertIn(":poco_nontensor_orig_x", patched)
        self.assertEqual(
            metadata["forced_false_patterns"],
            ["camera.lasagna*", "*use_tpu*", "*darwinn*", "*edgetpu*"],
        )

    def test_nontensor_guards_accept_baksmali_registers_directive(self):
        patched, metadata = patcher.patch_nontensor_flag_queries(
            FLAG_QUERY_REGISTERS_SAMPLE
        )

        self.assertIn(":poco_nontensor_false_q", patched)
        self.assertIn(":poco_nontensor_false_x", patched)
        self.assertEqual(
            metadata["methods"]["q"]["register_declaration"],
            ".registers 4",
        )
        self.assertEqual(
            metadata["methods"]["x"]["register_declaration"],
            ".registers 4",
        )

    def test_nontensor_guard_expands_minimal_registers(self):
        changed = FLAG_QUERY_REGISTERS_SAMPLE.replace(
            ".registers 4",
            ".registers 2",
        )
        patched, metadata = patcher.patch_nontensor_flag_queries(changed)

        self.assertIn(".registers 4", patched)
        self.assertEqual(
            metadata["methods"]["q"]["original_register_declaration"],
            ".registers 2",
        )
        self.assertEqual(
            metadata["methods"]["x"]["original_register_declaration"],
            ".registers 2",
        )
        self.assertTrue(metadata["methods"]["q"]["registers_expanded"])
        self.assertTrue(metadata["methods"]["x"]["registers_expanded"])

    def test_nontensor_guard_expands_minimal_locals(self):
        changed = FLAG_QUERY_SAMPLE.replace(
            ".locals 3",
            ".locals 0",
        )
        patched, metadata = patcher.patch_nontensor_flag_queries(changed)

        self.assertIn(".locals 2", patched)
        self.assertEqual(
            metadata["methods"]["q"]["original_register_declaration"],
            ".locals 0",
        )
        self.assertEqual(
            metadata["methods"]["x"]["original_register_declaration"],
            ".locals 0",
        )
        self.assertTrue(metadata["methods"]["q"]["registers_expanded"])
        self.assertTrue(metadata["methods"]["x"]["registers_expanded"])

    def test_nontensor_guard_preserves_original_method_fallthrough(self):
        patched, _ = patcher.patch_nontensor_flag_queries(FLAG_QUERY_SAMPLE)

        self.assertIn(
            ":poco_nontensor_orig_q\n\n"
            "    iget-object v0, p0, Lklm;->b:Ljava/util/Map;",
            patched,
        )
        self.assertIn(
            ":poco_nontensor_orig_x\n\n"
            "    iget-object p0, p0, Lklm;->b:Ljava/util/Map;",
            patched,
        )

    def test_nontensor_guard_fails_closed_when_query_shape_changes(self):
        changed = FLAG_QUERY_SAMPLE.replace(
            ".method public final x(Lkiz;)Z",
            ".method public final renamed(Lkiz;)Z",
        )
        with self.assertRaisesRegex(patcher.PatchError, "exactly one"):
            patcher.patch_nontensor_flag_queries(changed)

    def test_disables_native_tensor_startup_initparams(self):
        patched, metadata = patcher.patch_gcam_init_smali_text(GCAM_INIT_SAMPLE)

        self.assertIn("goto/32 :cond_2a", patched)
        self.assertIn("goto/32 :cond_2c", patched)
        self.assertIn(patcher.ALMOND_TPU_SYMBOL, patched)
        self.assertIn(patcher.TOMTE_GRAIN_SYMBOL, patched)
        self.assertEqual(
            metadata["almond_use_tpu"]["status"],
            "forced_default_false",
        )
        self.assertEqual(
            metadata["finish_tomte_grain"]["status"],
            "forced_default_false",
        )

    def test_gcam_init_patch_fails_closed_without_unique_symbols(self):
        changed = GCAM_INIT_SAMPLE.replace(
            patcher.ALMOND_TPU_SYMBOL,
            "DifferentSetter",
        )
        with self.assertRaisesRegex(patcher.PatchError, "exactly one"):
            patcher.patch_gcam_init_smali_text(changed)

    def test_gcam_init_patch_rejects_cross_block_branch(self):
        changed = GCAM_INIT_SAMPLE.replace(
            "    iget-wide v2, v1, Lcom/google/googlex/gcam/InitParams;->a:J\n\n"
            "    invoke-static {v2, v3, v1, v12}, "
            "Lcom/google/googlex/gcam/GcamModuleJNI;->InitParams_almond_use_tpu_set",
            "    :unexpected_label\n"
            "    iget-wide v2, v1, Lcom/google/googlex/gcam/InitParams;->a:J\n\n"
            "    invoke-static {v2, v3, v1, v12}, "
            "Lcom/google/googlex/gcam/GcamModuleJNI;->InitParams_almond_use_tpu_set",
        )
        with self.assertRaisesRegex(patcher.PatchError, "crosses another"):
            patcher.patch_gcam_init_smali_text(changed)

    def test_smali_tree_ignores_gcam_module_jni_declarations(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "mjy.smali").write_text(GCAM_INIT_SAMPLE, encoding="utf-8")
            (root / "com").mkdir()
            jni = root / "com" / "GcamModuleJNI.smali"
            jni.write_text(
                """.class public final Lcom/google/googlex/gcam/GcamModuleJNI;
.super Ljava/lang/Object;

.method public static native InitParams_almond_use_tpu_set(JLcom/google/googlex/gcam/InitParams;Z)V
.end method

.method public static native InitParams_finish_tomte_grain_enabled_set(JLcom/google/googlex/gcam/InitParams;Z)V
.end method

.method public static native Gcam_Create(JLcom/google/googlex/gcam/InitParams;JLcom/google/googlex/gcam/StaticMetadataVector;)J
.end method
""",
                encoding="utf-8",
            )

            metadata = patcher.find_and_patch_gcam_init_smali_tree(root)

            self.assertEqual(metadata["smali_path"], "mjy.smali")
            patched = (root / "mjy.smali").read_text(encoding="utf-8")
            self.assertIn("goto/32 :cond_2a", patched)
            self.assertIn("goto/32 :cond_2c", patched)

    def test_finds_gcam_init_dex_by_exact_symbol_set(self):
        with tempfile.TemporaryDirectory() as temp:
            apk = Path(temp) / "camera.apk"
            with zipfile.ZipFile(apk, "w") as archive:
                archive.writestr("classes.dex", b"ordinary")
                archive.writestr(
                    "classes3.dex",
                    b"\x00".join(patcher.GCAM_INIT_SYMBOLS),
                )
            self.assertEqual(patcher.find_gcam_init_dex(apk), "classes3.dex")

    def test_finds_exactly_one_target_dex(self):
        with tempfile.TemporaryDirectory() as temp:
            apk = Path(temp) / "camera.apk"
            with zipfile.ZipFile(apk, "w") as archive:
                archive.writestr("classes.dex", b"dex\n035\x00ordinary")
                archive.writestr(
                    "classes2.dex",
                    b"dex\n035\x00" + patcher.MARKER_BYTES + b"\x00tail",
                )
            self.assertEqual(patcher.find_target_dex(apk), "classes2.dex")

    def test_rejects_duplicate_target_dex_markers(self):
        with tempfile.TemporaryDirectory() as temp:
            apk = Path(temp) / "camera.apk"
            with zipfile.ZipFile(apk, "w") as archive:
                archive.writestr("classes.dex", patcher.MARKER_BYTES)
                archive.writestr("classes2.dex", patcher.MARKER_BYTES)
            with self.assertRaisesRegex(patcher.PatchError, "exactly one classes"):
                patcher.find_target_dex(apk)


if __name__ == "__main__":
    unittest.main()
