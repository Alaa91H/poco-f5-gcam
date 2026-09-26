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


OFE_SAMPLE = r'''.class public final Lofe;
.super Ljava/lang/Object;

.field public final a:I

.method public final synthetic a()Ljava/lang/Object;
    .registers 21

    sget-object v0, Ltdn;->a:Landroid/hardware/camera2/CaptureRequest$Key;

    invoke-static {v0}, Lj$/util/Optional;->ofNullable(Ljava/lang/Object;)Lj$/util/Optional;

    move-result-object v10

    sget-object v0, Ltdn;->b:Landroid/hardware/camera2/CaptureRequest$Key;

    invoke-static {v0}, Lj$/util/Optional;->of(Ljava/lang/Object;)Lj$/util/Optional;

    move-result-object v11

    new-instance v1, Lofp;

    return-object v1
.end method
'''


MTA_SAMPLE = r'''.class public final Lmta;
.super Ljava/lang/Object;

.method public final synthetic a()Ljava/lang/Object;
    .registers 17

    invoke-virtual {v0, v1}, Lklm;->q(Lkiz;)Z

    move-result v0

    if-eqz v0, :cond_2d9

    sget-object v0, Ltdi;->a:Landroid/hardware/camera2/CaptureRequest$Key;

    invoke-static {v4}, Ljava/lang/Integer;->valueOf(I)Ljava/lang/Integer;

    move-result-object v1

    new-instance v2, Lupd;

    invoke-direct {v2, v0, v1}, Lupd;-><init>(Landroid/hardware/camera2/CaptureRequest$Key;Ljava/lang/Object;)V

    new-instance v0, Lyjh;

    invoke-direct {v0, v2}, Lyjh;-><init>(Ljava/lang/Object;)V

    goto :goto_2db

    :cond_2d9
    sget-object v0, Lyiu;->a:Lyiu;

    :goto_2db
    return-object v0
.end method
'''


ODR_SAMPLE = r'''.class public final Lodr;
.super Ljava/lang/Object;

.field public final a:Ljava/lang/Object;
.field public final b:I

.method public final a(Ljava/lang/Object;)V
    .registers 7

    iget v0, p0, Lodr;->b:I

    check-cast p1, Lpqg;

    iget v0, p1, Lpqg;->a:I

    sget-object v1, Ltdn;->a:Landroid/hardware/camera2/CaptureRequest$Key;

    invoke-static {v0}, Ljava/lang/Integer;->valueOf(I)Ljava/lang/Integer;

    move-result-object v0

    new-instance v2, Lupd;

    invoke-direct {v2, v1, v0}, Lupd;-><init>(Landroid/hardware/camera2/CaptureRequest$Key;Ljava/lang/Object;)V

    iget-object p1, p1, Lpqg;->b:Lyeh;

    sget-object v0, Ltdn;->b:Landroid/hardware/camera2/CaptureRequest$Key;

    invoke-static {p1}, Laaaq;->aK(Ljava/util/Collection;)[F

    move-result-object p1

    new-instance v1, Lupd;

    invoke-direct {v1, v0, p1}, Lupd;-><init>(Landroid/hardware/camera2/CaptureRequest$Key;Ljava/lang/Object;)V

    invoke-static {v2, v1}, Lyfm;->I(Ljava/lang/Object;Ljava/lang/Object;)Lyfm;

    move-result-object p1

    iget-object p0, p0, Lodr;->a:Ljava/lang/Object;

    invoke-interface {p0, p1}, Luoi;->t(Ljava/util/Set;)V

    return-void
.end method
'''


RP_SAMPLE = r'''.class public final Lrp;
.super Ljava/lang/Object;

.method public final e(Lve;)Z
    .registers 21

    iget-object v5, v6, Lve;->g:Ljava/util/Map;

    invoke-interface {v5}, Ljava/util/Map;->entrySet()Ljava/util/Set;

    move-result-object v5

    invoke-interface {v5}, Ljava/util/Set;->iterator()Ljava/util/Iterator;

    move-result-object v5

    invoke-interface {v5}, Ljava/util/Iterator;->next()Ljava/lang/Object;

    move-result-object v6

    check-cast v6, Ljava/util/Map$Entry;

    invoke-interface {v6}, Ljava/util/Map$Entry;->getKey()Ljava/lang/Object;

    move-result-object v13

    move-object v14, v13

    check-cast v14, Landroid/hardware/camera2/CaptureRequest$Key;

    invoke-virtual {v14}, Landroid/hardware/camera2/CaptureRequest$Key;->getName()Ljava/lang/String;

    move-result-object v14

    invoke-interface {v9, v14}, Ljava/util/List;->contains(Ljava/lang/Object;)Z

    move-result v14

    if-eqz v14, :cond_123

    invoke-static {v0, v13, v6}, Lvz;->x(Landroid/hardware/camera2/CaptureRequest$Builder;Ljava/lang/Object;Ljava/lang/Object;)V

    :cond_123
    return v14
.end method
'''


UG_SAMPLE = r'''.class public final Lug;
.super Ljava/lang/Object;

.method public final a(Ljava/lang/String;IJLsz;Lsb;Ladel;)Ljava/lang/Object;
    .registers 28

    move-object/from16 v0, p0

    move-object/from16 v1, p1

    move-object/from16 v2, p7

    instance-of v3, v2, Ltz;

    if-eqz v3, :cond_19

    :cond_4e
    invoke-static {v2}, Laaax;->ac(Ljava/lang/Object;)V

    iget-object v2, v0, Lug;->i:Ladvz;

    iput-object v1, v3, Ltz;->g:Ljava/lang/String;

    move-object v10, v1

    :goto_8f
    new-instance v9, Lrr;

    invoke-direct/range {v9 .. v19}, Lrr;-><init>(Ljava/lang/String;Lpi;IJLjom;Lsz;Lufk;Ldan;Lsb;)V

    new-instance v1, Luf;

    invoke-direct {v1, v0, v10, v9, v8}, Luf;-><init>(Lug;Ljava/lang/String;Lrr;Ladel;)V

    return-object v4
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

    iget-object v7, v5, Luuv;->a:Ljava/lang/String;

    if-eqz v7, :cond_131

    invoke-interface {v2, v5}, Luut;->a(Luuv;)Luus;

    move-result-object v5

    invoke-static {v5}, Lcom/google/googlex/gcam/hdrplus/NativeMetadataConverter;->C(Luus;)Lcom/google/googlex/gcam/StaticMetadata;

    move-result-object v7

    move-object/from16 v24, v5

    iget-object v5, v1, Landroid/content/pm/PackageInfo;->packageName:Ljava/lang/String;

    invoke-virtual {v7, v5}, Lcom/google/googlex/gcam/StaticMetadata;->q(Ljava/lang/String;)V

    iget-object v5, v1, Landroid/content/pm/PackageInfo;->versionName:Ljava/lang/String;

    invoke-virtual {v7, v5}, Lcom/google/googlex/gcam/StaticMetadata;->r(Ljava/lang/String;)V

    invoke-virtual {v7}, Lcom/google/googlex/gcam/StaticMetadata;->g()Lzoi;

    invoke-virtual {v14, v7}, Lcom/google/googlex/gcam/StaticMetadataVector;->c(Lcom/google/googlex/gcam/StaticMetadata;)V

    move-object/from16 v5, v24

    check-cast v5, Luur;

    iget-object v5, v5, Luur;->b:Lyfm;

    invoke-interface {v5}, Ljava/util/Set;->iterator()Ljava/util/Iterator;

    invoke-interface {v15, v7}, Ljava/util/List;->get(I)Ljava/lang/Object;

    move-result-object v0

    check-cast v0, Luuv;

    invoke-interface {v2, v0}, Luut;->a(Luuv;)Luus;

    move-result-object v0

    invoke-static {v0}, Lmjt;->b(Luus;)Z

    move-result v18

    if-nez v18, :cond_160

    :cond_160
    invoke-static {v0}, Lcom/google/googlex/gcam/hdrplus/NativeMetadataConverter;->C(Luus;)Lcom/google/googlex/gcam/StaticMetadata;

    move-result-object v2

    invoke-virtual {v14, v2}, Lcom/google/googlex/gcam/StaticMetadataVector;->c(Lcom/google/googlex/gcam/StaticMetadata;)V

    iget-wide v2, v1, Lcom/google/googlex/gcam/InitParams;->a:J

    iget-wide v4, v14, Lcom/google/googlex/gcam/StaticMetadataVector;->a:J

    move-object/from16 v16, v1

    move-wide/from16 v17, v4

    move-object/from16 v19, v14

    move-wide v14, v2

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

    def test_allows_absent_onecamera_request_key(self):
        patched, metadata = patcher.patch_onecamera_optional_key_smali_text(
            OFE_SAMPLE
        )

        self.assertIn(
            "sget-object v0, Ltdn;->b:"
            "Landroid/hardware/camera2/CaptureRequest$Key;",
            patched,
        )
        self.assertNotIn(
            "sget-object v0, Ltdn;->b:"
            "Landroid/hardware/camera2/CaptureRequest$Key;\n\n"
            "    invoke-static {v0}, Lj$/util/Optional;->"
            "of(Ljava/lang/Object;)Lj$/util/Optional;",
            patched,
        )
        self.assertIn(
            "sget-object v0, Ltdn;->b:"
            "Landroid/hardware/camera2/CaptureRequest$Key;\n\n"
            "    invoke-static {v0}, Lj$/util/Optional;->"
            "ofNullable(Ljava/lang/Object;)Lj$/util/Optional;",
            patched,
        )
        self.assertEqual(
            metadata["status"],
            "allow_absent_ldtn_b_capture_request_key",
        )
        self.assertEqual(metadata["key"], "Ltdn.b")
        self.assertEqual(metadata["old_wrapper"], "Optional.of")
        self.assertEqual(metadata["new_wrapper"], "Optional.ofNullable")

    def test_onecamera_optional_patch_fails_closed_if_wrapper_changes(self):
        changed = OFE_SAMPLE.replace(
            "invoke-static {v0}, Lj$/util/Optional;->"
            "of(Ljava/lang/Object;)Lj$/util/Optional;",
            "invoke-static {v0}, Lj$/util/Optional;->"
            "empty()Lj$/util/Optional;",
        )
        with self.assertRaisesRegex(
            patcher.PatchError,
            "no longer wrapped by the expected Optional.of",
        ):
            patcher.patch_onecamera_optional_key_smali_text(changed)

    def test_onecamera_optional_patch_fails_closed_without_nullable_sibling(self):
        changed = OFE_SAMPLE.replace(
            "ofNullable(Ljava/lang/Object;)Lj$/util/Optional;",
            "of(Ljava/lang/Object;)Lj$/util/Optional;",
            1,
        )
        with self.assertRaisesRegex(
            patcher.PatchError,
            "Ltdn.a no longer uses Optional.ofNullable",
        ):
            patcher.patch_onecamera_optional_key_smali_text(changed)

    def test_allows_absent_mta_vendor_request_key(self):
        patched, metadata = patcher.patch_onecamera_missing_request_key_smali_text(
            MTA_SAMPLE
        )

        self.assertIn(
            "sget-object v0, Ltdi;->a:"
            "Landroid/hardware/camera2/CaptureRequest$Key;\n\n"
            "    # POCO F5: optional vendor CaptureRequest key is absent.\n"
            "    if-eqz v0, :cond_2d9",
            patched,
        )
        self.assertEqual(
            metadata["status"],
            "allow_absent_ldti_a_capture_request_key",
        )
        self.assertEqual(metadata["key"], "Ltdi.a")
        self.assertEqual(metadata["fallback_label"], "cond_2d9")

    def test_mta_vendor_request_key_patch_fails_closed_if_key_moves(self):
        changed = MTA_SAMPLE.replace("Ltdi;->a:", "Ltdi;->b:")
        with self.assertRaisesRegex(
            patcher.PatchError,
            "exactly one Ltdi.a CaptureRequest key",
        ):
            patcher.patch_onecamera_missing_request_key_smali_text(changed)

    def test_odr_omits_absent_ldtn_b_request_entry(self):
        patched, metadata = (
            patcher.patch_onecamera_odr_missing_request_key_smali_text(
                ODR_SAMPLE
            )
        )

        self.assertIn(
            "sget-object v1, Ltdn;->a:"
            "Landroid/hardware/camera2/CaptureRequest$Key;\n\n"
            "    # POCO F5: Ltdn.a is optional on non-Pixel camera HALs.\n"
            "    if-eqz v1, :poco_odr_ldtn_a_absent",
            patched,
        )
        self.assertIn(
            "sget-object v0, Ltdn;->b:"
            "Landroid/hardware/camera2/CaptureRequest$Key;\n\n"
            "    # POCO F5: Ltdn.b is an optional Pixel-only request key.\n"
            "    if-eqz v0, :poco_odr_ldtn_b_absent",
            patched,
        )
        self.assertIn(
            ":poco_odr_ldtn_a_absent\n\n    return-void",
            patched,
        )
        self.assertIn(
            "invoke-static {v2}, "
            "Ljava/util/Collections;->singleton(Ljava/lang/Object;)Ljava/util/Set;",
            patched,
        )
        self.assertIn(":poco_odr_request_set_ready", patched)
        self.assertEqual(
            metadata["status"],
            "omit_absent_ldtn_b_request_entry",
        )
        self.assertEqual(metadata["key"], "Ltdn.b")
        self.assertEqual(metadata["preserved_key"], "Ltdn.a")
        self.assertEqual(metadata["additional_nullable_key"], "Ltdn.a")

    def test_odr_missing_request_key_patch_fails_closed_if_key_moves(self):
        changed = ODR_SAMPLE.replace("Ltdn;->b:", "Ltdn;->c:")
        with self.assertRaisesRegex(
            patcher.PatchError,
            "Ltdn.a/Ltdn.b request-entry shape changed",
        ):
            patcher.patch_onecamera_odr_missing_request_key_smali_text(
                changed
            )

    def test_omits_unsupported_stabilization_session_key_and_logs_rest(self):
        patched, metadata = (
            patcher.patch_onecamera_session_parameter_logging_smali_text(
                RP_SAMPLE
            )
        )

        self.assertIn(
            "sget-object v15, "
            "Landroid/hardware/camera2/CaptureRequest;->"
            "CONTROL_VIDEO_STABILIZATION_MODE:"
            "Landroid/hardware/camera2/CaptureRequest$Key;",
            patched,
        )
        self.assertIn(
            "if-eq v13, v15, :goto_123",
            patched,
        )
        self.assertIn(
            'const-string v15, "GCamSessionParamKey"',
            patched,
        )
        self.assertIn(
            "invoke-static {v15, v14}, "
            "Landroid/util/Log;->e(Ljava/lang/String;Ljava/lang/String;)I",
            patched,
        )
        self.assertGreater(
            patched.index('const-string v15, "GCamSessionParamKey"'),
            patched.index("if-eqz v14, :cond_123"),
        )
        self.assertLess(
            patched.index('const-string v15, "GCamSessionParamKey"'),
            patched.index(
                "invoke-static {v0, v13, v6}, "
                "Lvz;->x(Landroid/hardware/camera2/CaptureRequest$Builder;"
                "Ljava/lang/Object;Ljava/lang/Object;)V"
            ),
        )
        self.assertIn(
            "invoke-virtual {v13}, "
            "Landroid/hardware/camera2/CaptureRequest$Key;->getName()"
            "Ljava/lang/String;",
            patched,
        )
        self.assertEqual(metadata["status"], "diagnostic_logging")
        self.assertEqual(metadata["class"], "Lrp;")
        self.assertEqual(metadata["tag"], "GCamSessionParamKey")
        self.assertEqual(metadata["scratch_register"], "v15")
        self.assertEqual(
            metadata["scratch_liveness"],
            "verified unused after key-name lookup",
        )
        self.assertTrue(metadata["logs_only_applied_session_parameters"])
        self.assertIn("Lpi.d()", metadata["source"])
        self.assertTrue(metadata["behavior_changed"])
        self.assertEqual(
            metadata["skipped_session_key"],
            "android.control.videoStabilizationMode",
        )
        self.assertEqual(metadata["skip_scope"], "session_parameters_only")
        self.assertEqual(
            metadata["preserved_session_keys"],
            ["android.control.aeTargetFpsRange"],
        )

    def test_session_parameter_logging_fails_closed_if_scratch_register_moves(self):
        changed = RP_SAMPLE.replace(
            ":cond_123\n"
            "    return v14",
            ":cond_123\n"
            "    const/4 v15, 0x0\n\n"
            "    return v14",
        )
        with self.assertRaisesRegex(
            patcher.PatchError,
            "diagnostic scratch register v15",
        ):
            patcher.patch_onecamera_session_parameter_logging_smali_text(
                changed
            )

    def test_redirects_xiaomi_logical_rear_before_callback_creation(self):
        patched, metadata = patcher.patch_onecamera_open_camera_fallback_smali_text(
            UG_SAMPLE
        )

        self.assertIn('const-string v9, "4"', patched)
        self.assertIn('const-string v1, "0"', patched)
        self.assertIn(":poco_physical_rear_camera_ready", patched)
        self.assertLess(
            patched.index(":poco_physical_rear_camera_ready"),
            patched.index("iput-object v1, v3, Ltz;->g:Ljava/lang/String;"),
        )
        self.assertLess(
            patched.index(":poco_physical_rear_camera_ready"),
            patched.index("Lrr;-><init>"),
        )
        self.assertEqual(
            metadata["status"],
            "redirect_xiaomi_logical_rear_to_physical_rear",
        )
        self.assertEqual(metadata["requested_camera_id"], "4")
        self.assertEqual(metadata["fallback_camera_id"], "0")
        self.assertTrue(metadata["callback_expected_id_remapped"])
        self.assertTrue(metadata["metadata_lookup_id_remapped"])
        self.assertTrue(metadata["other_camera_ids_unchanged"])

    def test_rear_open_fallback_fails_closed_if_camera_flow_changes(self):
        changed = UG_SAMPLE.replace(
            "move-object/from16 v1, p1",
            "move-object/from16 v5, p1",
        )
        with self.assertRaisesRegex(
            patcher.PatchError,
            "initial camera ID copy",
        ):
            patcher.patch_onecamera_open_camera_fallback_smali_text(changed)

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
            "filtered_xiaomi_logical_and_duplicate_aliases",
        )
        self.assertEqual(
            metadata["sensor_id_uniqueness"][
                "duplicate_alias_camera_ids_excluded_from_gcam_vector"
            ],
            ["3", "5", "6"],
        )
        self.assertEqual(
            metadata["sensor_id_uniqueness"][
                "unsupported_logical_camera_ids_excluded_from_gcam_vector"
            ],
            ["4"],
        )
        self.assertEqual(
            metadata["sensor_id_uniqueness"][
                "excluded_camera_ids_from_gcam_vector"
            ],
            ["3", "4", "5", "6"],
        )
        self.assertEqual(
            metadata["sensor_id_uniqueness"]["expected_kept_camera_ids"],
            ["0", "2", "1"],
        )
        self.assertTrue(
            metadata["sensor_id_uniqueness"]["camera2_enumeration_unchanged"],
        )
        self.assertIn(":poco_top_level_alias_done", patched)
        self.assertIn('const-string v25, "3"', patched)
        self.assertIn('const-string v25, "4"', patched)
        self.assertIn('const-string v25, "5"', patched)
        self.assertIn('const-string v25, "6"', patched)
        self.assertIn('const-string v25, "GCamMappedCameraId"', patched)
        self.assertIn('const-string v25, "GCamMappedSensorId"', patched)
        self.assertEqual(
            metadata["sensor_id_uniqueness"]["pre_filter_mapping_diagnostics"][
                "camera_id_tag"
            ],
            "GCamMappedCameraId",
        )
        self.assertEqual(
            metadata["sensor_id_uniqueness"]["pre_filter_mapping_diagnostics"][
                "sensor_id_tag"
            ],
            "GCamMappedSensorId",
        )
        self.assertFalse(
            metadata["sensor_id_uniqueness"]["pre_filter_mapping_diagnostics"][
                "behavior_changed"
            ],
        )
        self.assertTrue(
            metadata["sensor_id_uniqueness"]["native_check_preserved"],
        )
        self.assertEqual(
            metadata["sensor_id_uniqueness"]["native_check"],
            "Gcam_AllSensorIdsUnique",
        )
        self.assertIn('const-string v24, "GCamTopCameraId"', patched)
        self.assertIn('const-string v25, "GCamPhysicalCameraId"', patched)
        self.assertIn(
            "invoke-static/range {v24 .. v25}, Landroid/util/Log;->e",
            patched,
        )
        self.assertIn("move-object/from16 v24, v3", patched)
        self.assertIn("iget-object v3, v0, Luuv;->a:Ljava/lang/String;", patched)
        self.assertIn("move-object/from16 v3, v24", patched)
        self.assertIn(
            "invoke-static/range {v25 .. v26}, Landroid/util/Log;->e",
            patched,
        )
        self.assertEqual(
            metadata["camera_source_diagnostics"]["status"],
            "logged_android_camera_ids",
        )
        self.assertFalse(
            metadata["camera_source_diagnostics"]["behavior_changed"],
        )
        self.assertIn('const-string v3, "GCamSensorIds"', patched)
        self.assertIn(":poco_sensor_diag_loop", patched)
        self.assertEqual(
            metadata["sensor_vector_diagnostics"]["status"],
            "logged_static_metadata_sensor_ids",
        )
        self.assertFalse(
            metadata["sensor_vector_diagnostics"]["behavior_changed"],
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
