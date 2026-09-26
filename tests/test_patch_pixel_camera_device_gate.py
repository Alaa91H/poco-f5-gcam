import struct
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


KEEPALIVE_SAMPLE = r'''.class public final Lcom/google/android/apps/camera/keepalive/KeepAliveBroadcastReceiver;
.super Landroid/content/BroadcastReceiver;

.method public final onReceive(Landroid/content/Context;Landroid/content/Intent;)V
    .locals 2

    new-instance v0, Landroid/content/Intent;
    const-class v1, Lcom/google/android/apps/camera/prewarm/NoOpPrewarmService;
    invoke-direct {v0, p1, v1}, Landroid/content/Intent;-><init>(Landroid/content/Context;Ljava/lang/Class;)V
    invoke-virtual {p1, v0}, Landroid/content/Context;->startService(Landroid/content/Intent;)Landroid/content/ComponentName;
    return-void
.end method
'''


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
    sget-object v0, Luve;->b:Luve;

    aput-object v0, v13, v12

    sget-object v0, Luve;->a:Luve;

    aput-object v0, v13, p0

    aget-object v0, v13, v12

    invoke-static {v5}, Lcom/google/googlex/gcam/hdrplus/NativeMetadataConverter;->C(Luus;)Lcom/google/googlex/gcam/StaticMetadata;

    move-result-object v7

    move-object/from16 v24, v5

    invoke-virtual {v14, v7}, Lcom/google/googlex/gcam/StaticMetadataVector;->c(Lcom/google/googlex/gcam/StaticMetadata;)V

    move-object/from16 v5, v24

    check-cast v5, Luur;

    iget-object v5, v5, Luur;->b:Lyfm;

    invoke-interface {v5}, Ljava/util/Set;->iterator()Ljava/util/Iterator;

    iget-wide v2, v1, Lcom/google/googlex/gcam/InitParams;->a:J

    invoke-static/range {v14 .. v19}, Lcom/google/googlex/gcam/GcamModuleJNI;->Gcam_Create(JLcom/google/googlex/gcam/InitParams;JLcom/google/googlex/gcam/StaticMetadataVector;)J

    move-result-wide v0

    cmp-long v2, v0, v20

    if-nez v2, :cond_644

    const/4 v5, 0x0

    goto :goto_649

    :cond_644
    new-instance v5, Lcom/google/googlex/gcam/Gcam;

    invoke-direct {v5, v0, v1}, Lcom/google/googlex/gcam/Gcam;-><init>(J)V

    :goto_649
    invoke-virtual {v5}, Lcom/google/googlex/gcam/Gcam;->g()Z

    move-result v0

    if-eqz v0, :cond_656

    invoke-interface/range {v22 .. v22}, Lulx;->g()V

    invoke-virtual {v5}, Ljava/lang/Object;->getClass()Ljava/lang/Class;

    return-object v5

    :cond_656
    new-instance v0, Ljava/lang/IllegalArgumentException;

    invoke-direct {v0}, Ljava/lang/IllegalArgumentException;-><init>()V

    throw v0
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

    def test_disables_android17_keepalive_background_service(self):
        patched, metadata = patcher.patch_keepalive_receiver_smali_text(
            KEEPALIVE_SAMPLE
        )

        self.assertIn(
            "# POCO F5 / Android 17: skip Pixel keepalive background service.",
            patched,
        )
        self.assertIn("return-void", patched)
        self.assertNotIn("->startService(", patched)
        self.assertEqual(
            metadata["status"],
            "disabled_pixel_keepalive_receiver",
        )

    def test_keepalive_patch_fails_closed_without_start_service(self):
        changed = KEEPALIVE_SAMPLE.replace(
            "->startService(Landroid/content/Intent;)Landroid/content/ComponentName;",
            "->stopService(Landroid/content/Intent;)Z",
        )
        with self.assertRaisesRegex(patcher.PatchError, "startService"):
            patcher.patch_keepalive_receiver_smali_text(changed)

    def test_keepalive_tree_requires_exact_receiver_class(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "KeepAliveBroadcastReceiver.smali"
            path.write_text(KEEPALIVE_SAMPLE, encoding="utf-8")

            metadata = patcher.find_and_patch_keepalive_receiver_smali_tree(root)

            self.assertEqual(
                metadata["smali_path"],
                "KeepAliveBroadcastReceiver.smali",
            )
            self.assertNotIn(
                "->startService(",
                path.read_text(encoding="utf-8"),
            )

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
        self.assertIn(
            "# POCO F5: logical camera must not reuse a physical GCam sensor ID.",
            patched,
        )
        self.assertIn(
            "sget-object v5, Lzoi;->s:Lzoi;",
            patched,
        )
        self.assertIn(
            "sget-object v5, Lzoi;->v:Lzoi;",
            patched,
        )
        self.assertIn(
            "Lcom/google/googlex/gcam/StaticMetadata;->u(Lzoi;)V",
            patched,
        )
        self.assertIn(
            "if-eqz v0, :cond_656",
            patched,
        )
        self.assertEqual(
            metadata["sensor_id_uniqueness"]["status"],
            "remapped_top_level_logical_entries",
        )
        self.assertTrue(
            metadata["sensor_id_uniqueness"]["native_check_preserved"],
        )
        self.assertEqual(
            metadata["sensor_id_uniqueness"]["native_check"],
            "Gcam_AllSensorIdsUnique",
        )

    def test_gcam_init_patch_fails_closed_when_sensor_id_guard_shape_changes(self):
        changed = GCAM_INIT_SAMPLE.replace(
            "invoke-virtual {v5}, Lcom/google/googlex/gcam/Gcam;->g()Z",
            "invoke-virtual {v5}, Lcom/google/googlex/gcam/Gcam;->f()Z",
        )
        with self.assertRaisesRegex(
            patcher.PatchError,
            "sensor-ID uniqueness check",
        ):
            patcher.patch_gcam_init_smali_text(changed)

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

    def test_libgcam_tuning_diagnostics_find_adrp_add_string_xref(self):
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

        report = patcher.analyze_libgcam_tuning(bytes(blob))
        unknown = report["strings"][patcher.TUNING_DIAGNOSTIC_STRINGS[0]]

        self.assertEqual(unknown["count"], 1)
        self.assertEqual(
            unknown["occurrences"][0]["xref_offsets"],
            ["0x1000", "0x1004"],
        )
        self.assertFalse(report["tuning_bypass_performed"])
        self.assertFalse(report["profile_spoof_performed"])

    def test_libgcam_tuning_diagnostics_reject_non_elf(self):
        with self.assertRaisesRegex(patcher.PatchError, "not an ELF"):
            patcher.analyze_libgcam_tuning(b"not-an-elf")

    def test_aarch64_control_flow_decoder_reports_branch_targets(self):
        self.assertEqual(
            patcher._decode_aarch64_branch(0x14000002, 0x1000),
            "b 0x1008",
        )
        self.assertEqual(
            patcher._decode_aarch64_branch(0xD503201F, 0x1000),
            "nop",
        )
        self.assertEqual(
            patcher._decode_aarch64_branch(0x370000B3, 0x682E610),
            "tbnz 0x682e624",
        )
        self.assertEqual(
            patcher.AION_FALLBACK_PATCHES[0][1],
            0x682E610,
        )
        self.assertEqual(
            patcher.AION_FALLBACK_PATCHES[0][2],
            bytes.fromhex("b3000037"),
        )

    def test_native_cpu_fallback_patches_exact_verified_bytes(self):
        patches = (
            (
                "first",
                2,
                bytes.fromhex("aabb"),
                bytes.fromhex("1122"),
            ),
            (
                "second",
                6,
                bytes.fromhex("ccdd"),
                bytes.fromhex("3344"),
            ),
        )
        original = bytes.fromhex("0000aabb0000ccdd0000")
        patched, metadata = patcher.patch_native_bytes(original, patches=patches)

        self.assertEqual(patched, bytes.fromhex("00001122000033440000"))
        self.assertEqual([item["status"] for item in metadata], ["patched", "patched"])

    def test_native_cpu_fallback_is_idempotent(self):
        patches = (
            (
                "first",
                1,
                bytes.fromhex("aabb"),
                bytes.fromhex("1122"),
            ),
        )
        original = bytes.fromhex("00112200")
        patched, metadata = patcher.patch_native_bytes(original, patches=patches)

        self.assertEqual(patched, original)
        self.assertEqual(metadata[0]["status"], "already_patched")

    def test_native_cpu_fallback_fails_closed_on_unknown_bytes(self):
        patches = (
            (
                "first",
                1,
                bytes.fromhex("aabb"),
                bytes.fromhex("1122"),
            ),
        )
        with self.assertRaisesRegex(patcher.PatchError, "native bytes"):
            patcher.patch_native_bytes(bytes.fromhex("00ffff00"), patches=patches)

    def test_native_cpu_fallback_fails_closed_on_short_library(self):
        patches = (
            (
                "first",
                8,
                bytes.fromhex("aabb"),
                bytes.fromhex("1122"),
            ),
        )
        with self.assertRaisesRegex(patcher.PatchError, "exceeds native library size"):
            patcher.patch_native_bytes(b"short", patches=patches)

    def test_finds_keepalive_receiver_dex(self):
        with tempfile.TemporaryDirectory() as temp:
            apk = Path(temp) / "camera.apk"
            with zipfile.ZipFile(apk, "w") as archive:
                archive.writestr("classes.dex", b"ordinary")
                archive.writestr(
                    "classes4.dex",
                    b"dex\n035\x00" + patcher.KEEPALIVE_RECEIVER_BYTES,
                )
            self.assertEqual(
                patcher.find_keepalive_receiver_dex(apk),
                "classes4.dex",
            )

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
