import unittest
from scripts.pixel_camera_111_port import (patch_optional, patch_missing_key, patch_odr,
                                           patch_manual_awb_fallback,
                                           patch_opencl_merge_fallback,
                                           patch_video_mode_fallback, normalize_patch)
from tests.test_patch_pixel_camera_device_gate import OFE_SAMPLE, MTA_SAMPLE, ODR_SAMPLE
from scripts.patch_pixel_camera_device_gate import PatchError

class Pixel111PortTests(unittest.TestCase):
    def test_manual_awb_fallback_uses_standard_camera2_gains(self):
        source = '''.class public final Lidya;
.method public final a(Ljava/lang/Object;)V
    .registers 12
    :pswitch_50
    check-cast p1, Lqac;
    iget v0, p1, Lqac;->a:I
    sget-object v1, Ltpg;->a:Landroid/hardware/camera2/CaptureRequest$Key;
    if-eqz v1, :cond_6e
    invoke-static {v0}, Ljava/lang/Integer;->valueOf(I)Ljava/lang/Integer;
    move-result-object v0
    iget-object p1, p1, Lqac;->b:Lyqc;
    sget-object v0, Ltpg;->b:Landroid/hardware/camera2/CaptureRequest$Key;
    if-eqz v0, :cond_6e
    invoke-static {p1}, Lyzu;->bq(Ljava/util/Collection;)[F
    move-result-object p1
    invoke-static {v0, p1, p0}, Ljhq;->e(Landroid/hardware/camera2/CaptureRequest$Key;Ljava/lang/Object;Lvae;)V
    :cond_6e
    return-void
.end method
'''
        patched, report = patch_manual_awb_fallback(source)
        self.assertIn('CaptureRequest;->COLOR_CORRECTION_MODE', patched)
        self.assertIn('CaptureRequest;->COLOR_CORRECTION_GAINS', patched)
        self.assertIn('Ljava/lang/Float;->floatValue()F', patched)
        self.assertIn('Landroid/hardware/camera2/params/RggbChannelVector;-><init>(FFFF)V', patched)
        self.assertEqual(report['behavior'], 'use standard Camera2 AWB-off and RGGB gains when Pixel AWB keys are unavailable')

    def test_manual_awb_fallback_rejects_unreviewed_callback_shape(self):
        with self.assertRaises(PatchError):
            patch_manual_awb_fallback('.class Lbroken;')

    def test_opencl_merge_fallback_rewrites_only_reviewed_wrapper(self):
        import hashlib
        from unittest.mock import patch
        from scripts import pixel_camera_111_port as port

        original = b'prefix' + port.OPENCL_111_ORIGINAL + b'suffix'
        with patch.object(port, 'OPENCL_111_OFFSET', len(b'prefix')), \
                patch.object(port, 'OPENCL_111_SOURCE_SHA256', hashlib.sha256(original).hexdigest()):
            patched, report = patch_opencl_merge_fallback(original)
        self.assertEqual(len(patched), len(original))
        self.assertEqual(
            patched,
            b'prefix' + port.OPENCL_111_REPLACEMENT
            + port.OPENCL_111_ORIGINAL[len(port.OPENCL_111_REPLACEMENT):] + b'suffix',
        )
        self.assertEqual(report['behavior'], 'return CL_INVALID_OPERATION when optional OpenCL entry is absent')

    def test_opencl_merge_fallback_rejects_changed_source_or_wrapper(self):
        import hashlib
        from unittest.mock import patch
        from scripts import pixel_camera_111_port as port

        original = b'prefix' + port.OPENCL_111_ORIGINAL + b'suffix'
        with patch.object(port, 'OPENCL_111_OFFSET', len(b'prefix')), \
                patch.object(port, 'OPENCL_111_SOURCE_SHA256', '0' * 64):
            with self.assertRaisesRegex(PatchError, 'SHA-256'):
                patch_opencl_merge_fallback(original)
        changed = original[:6] + b'!' + original[7:]
        with patch.object(port, 'OPENCL_111_OFFSET', len(b'prefix')), \
                patch.object(port, 'OPENCL_111_SOURCE_SHA256', hashlib.sha256(changed).hexdigest()):
            with self.assertRaisesRegex(PatchError, 'wrapper instructions'):
                patch_opencl_merge_fallback(changed)

    def test_empty_filtered_video_sizes_fall_back_to_safe_30fps_1080p(self):
        source = '''.class public Ljbw;
.method public final a()Ljfz;
    invoke-interface {v4}, Lj$/util/stream/Stream;->findFirst()Lj$/util/Optional;
    move-result-object v4
    invoke-virtual {v4}, Lj$/util/Optional;->get()Ljava/lang/Object;
    move-result-object v4
    check-cast v4, Lusx;
    goto :goto_24b
    invoke-interface {v4}, Lj$/util/stream/Stream;->findFirst()Lj$/util/Optional;
    move-result-object v4
    invoke-virtual {v4}, Lj$/util/Optional;->get()Ljava/lang/Object;
    move-result-object v4
    check-cast v4, Lusx;
    goto :goto_24b
.end method
'''
        patched, report = patch_video_mode_fallback(source)
        self.assertEqual(patched.count('Optional;->isPresent()Z'), 2)
        self.assertEqual(patched.count('sget-object v3, Lusv;->c:Lusv;'), 2)
        self.assertEqual(patched.count('sget-object v4, Lusx;->i:Lusx;'), 2)
        self.assertEqual(patched.count('Optional;->get()Ljava/lang/Object;'), 2)
        self.assertEqual(report['empty_filter_fallback'], '30fps_1080p')

    def test_video_mode_fallback_fails_closed_if_upstream_shape_changes(self):
        source = '''.class public Ljbw;
.method public final a()Ljfz;
    invoke-interface {v4}, Lj$/util/stream/Stream;->findFirst()Lj$/util/Optional;
    move-result-object v4
    invoke-virtual {v4}, Lj$/util/Optional;->get()Ljava/lang/Object;
    move-result-object v4
    check-cast v4, Lusx;
    goto :goto_24b
.end method
'''
        with self.assertRaises(PatchError):
            patch_video_mode_fallback(source)

    def test_optional_vendor_key_keeps_111_types(self):
        source=OFE_SAMPLE.replace('Lofe;','Lopn;').replace('Ltdn;','Ltpg;')
        patched,_=patch_optional(source)
        self.assertIn('Optional;->ofNullable',patched)
        self.assertIn('Ltpg;',patched)
        self.assertNotIn('Ltdn;',patched)

    def test_missing_key_preserves_actual_value_register_and_empty_branch(self):
        source=MTA_SAMPLE
        for old,new in [('Lmta;','Lnbw;'),('Ltdi;','Ltpb;'),('Lupd;','Lvaz;'),('Lyjh;','Lyvd;'),('Lyiu;','Lyuq;')]: source=source.replace(old,new)
        source=source.replace('invoke-static {v4}, Ljava/lang/Integer;', 'invoke-static {v5}, Ljava/lang/Integer;')
        patched,_=patch_missing_key(source)
        self.assertIn('invoke-static {v5}, Ljava/lang/Integer;',patched)
        self.assertNotIn('invoke-static {v4}, Ljava/lang/Integer;',patched)
        self.assertIn('if-eqz v0,',patched)
        self.assertIn('Lyuq;->a:Lyuq;',patched)

    def test_awb_preserves_present_first_key_when_second_is_absent(self):
        source=ODR_SAMPLE
        for old,new in [('Lodr;','Lptg;'),('Ltdn;','Ltpg;'),('Lupd;','Lvaz;'),('Lyfm;','Lyrh;'),('Luoi;','Lvae;')]: source=source.replace(old,new)
        patched,_=patch_odr(source)
        self.assertIn('Collections;->singleton',patched)
        self.assertIn('Ltpg;->a:',patched)
        self.assertIn('Ltpg;->b:',patched)
        self.assertNotIn('Lodr;',patched)

    def test_alias_collision_is_rejected(self):
        with self.assertRaises(PatchError):
            normalize_patch('Lnew; Lold;', [('Lnew;','Lold;')], lambda s:(s,{}))

if __name__=='__main__': unittest.main()
