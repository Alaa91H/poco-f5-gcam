import unittest
from scripts.patch_pixel_camera_raw_session import patch_raw_session_smali, PatchError

FIXTURE = '''.class public final Lsb;
.super Ljava/lang/Object;
.field public final b:Ljava/lang/String;
.method public session()V
    .registers 20
    iget v15, v6, Lvr;->a:I
    move-object v13, v5
    move-object/from16 v5, v18
    invoke-direct/range {v0 .. v5}, Lsf;-><init>(Luh;Luf;Lvs;Ljvg;Landroid/os/Handler;)V
    new-instance v2, Landroid/hardware/camera2/params/SessionConfiguration;
    invoke-direct {v2, v15, v13, v9, v0}, Landroid/hardware/camera2/params/SessionConfiguration;-><init>(ILjava/util/List;Ljava/util/concurrent/Executor;Landroid/hardware/camera2/CameraCaptureSession$StateCallback;)V
    iget-object v0, v6, Lvr;->b:Ljava/util/List;
    return-void
.end method
'''

class RawSessionTests(unittest.TestCase):
    def test_preserves_constructor_and_guards_model_id_type_and_raw_outputs(self):
        patched, report = patch_raw_session_smali(FIXTURE)
        self.assertIn('invoke-direct {v2, v15, v13, v9, v0}', patched)
        self.assertIn('if-nez p0, :poco_raw_keep', patched)
        self.assertIn('->getConfiguredFormat()I', patched)
        self.assertIn('if-eqz v1, :poco_raw_keep', patched)
        self.assertEqual(report['raw_formats'], [32,37,38])
        self.assertEqual(report['camera_ids'], ['0','1'])
        self.assertNotIn('removeTarget', patched)

    def test_obfuscated_owner_is_resolved_without_old_descriptors(self):
        patched, report = patch_raw_session_smali(FIXTURE.replace('Lsb;', 'Lnewowner;').replace('b:Ljava/lang/String;', 'z:Ljava/lang/String;'))
        self.assertIn('Lnewowner;->z:Ljava/lang/String;', patched)
        self.assertNotIn('Lsb;', patched)

    def test_unreviewed_dataflow_and_duplicate_roles_fail_closed(self):
        for source in [FIXTURE.replace('move-object v13, v5','move-object v13, v8'), FIXTURE + FIXTURE,
                       FIXTURE.replace('.field public final b:Ljava/lang/String;', '.field public final b:Ljava/lang/String;\n.field public final c:Ljava/lang/String;')]:
            with self.subTest(source=source), self.assertRaises(PatchError):
                patch_raw_session_smali(source)

    def test_double_patch_is_rejected(self):
        patched, _ = patch_raw_session_smali(FIXTURE)
        with self.assertRaises(PatchError): patch_raw_session_smali(patched)

if __name__ == '__main__': unittest.main()
