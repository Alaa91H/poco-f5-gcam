import unittest

from scripts import patch_pixel_camera_device_gate as patcher
from tests.test_patch_pixel_camera_device_gate import GCAM_INIT_SAMPLE
from tests.test_patch_pixel_camera_device_gate import motion_stabilizer_fixture
from tests.test_patch_pixel_camera_device_gate import query_fixture, execute_query


class GcamInitObfuscationTests(unittest.TestCase):
    def test_flag_guard_rejects_renamed_key_without_a_name_field(self):
        text = query_fixture().replace('Lkiz;', 'Lkps;')
        with self.assertRaisesRegex(patcher.PatchError, 'unique boolean flag name field'):
            patcher.patch_nontensor_flag_queries(text)

    def test_flag_guard_rejects_different_query_key_types(self):
        text = query_fixture().replace('x(Lkiz;)', 'x(Lkps;)')
        with self.assertRaisesRegex(patcher.PatchError, 'different key types'):
            patcher.patch_nontensor_flag_queries(text)

    def test_flag_guard_uses_actual_key_type_and_name_owner(self):
        text = query_fixture().replace('Lklm;', 'Lksf;').replace('Lkiz;', 'Lkps;')
        text = text.replace('    const/4 v0, 0x1',
                            '    iget-object v0, p1, Lkpq;->a:Ljava/lang/String;\n'
                            '    const/4 v0, 0x1')
        patched, report = patcher.patch_nontensor_flag_queries(text)
        self.assertNotIn('Lkix;', patched)
        self.assertNotIn('Lkiz;', patched)
        self.assertEqual(report['key_type'], 'Lkps;')
        self.assertEqual(report['name_field'], 'Lkpq;->a:Ljava/lang/String;')
        canonical = patched.replace('Lkps;', 'Lkiz;').replace('Lkpq;', 'Lkix;')
        for method in ('q', 'x'):
            self.assertFalse(execute_query(canonical, method, 'camera.micro_prestab'))
            self.assertTrue(execute_query(canonical, method, 'camera.enable_micro'))

    def test_motion_guard_accepts_renamed_profile_and_wrapper(self):
        text = (motion_stabilizer_fixture().replace('Llio;', 'Llrm;')
                .replace('Lrdw;', 'Lrop;').replace('->j()Lnbv;', '->p()Lnkt;')
                .replace('Lthi;', 'Ltta;'))
        patched, _ = patcher.patch_motion_stabilizer_startup_smali_text(text)
        self.assertIn('return-void', patched)
        self.assertIn('Lrop;->p()Lnkt;', patched)
        self.assertNotIn('Llio;', patched)

    def test_interface_metadata_accessors_replace_old_concrete_fields(self):
        text = GCAM_INIT_SAMPLE.replace('Luve;', 'Lvhd;').replace('Luus;', 'Lvgr;')
        text = text.replace(
            '    move-object/from16 v5, v24\n\n    check-cast v5, Luur;\n\n'
            '    iget-object v5, v5, Luur;->b:Lyfm;',
            '    invoke-interface/range {v24 .. v24}, Lvgr;->G()Ljava/util/Set;\n\n'
            '    move-result-object v5',
        )
        patched, _ = patcher.patch_gcam_init_smali_text(
            text, metadata_interface_text='.method public abstract k()Luuv;\n.end method'
        )
        self.assertIn('Lvgr;->G()Ljava/util/Set;', patched)
        self.assertIn('Lvgr;->k()Luuv;', patched)
        self.assertNotIn('Luur;', patched)

    def test_accepts_renamed_roles_and_converter_with_same_data_flow(self):
        names = {'Luve;': 'Lvhd;', 'Luut;': 'Lvgs;', 'Luuv;': 'Lvgu;',
                 'Luus;': 'Lvgr;', 'Luur;': 'Lvgq;', 'Lyfm;': 'LaSet;',
                 'Lzoi;': 'Laaaj;'}
        text = GCAM_INIT_SAMPLE
        for old, new in names.items():
            text = text.replace(old, new)
        text = text.replace('NativeMetadataConverter;->C(', 'NativeMetadataConverter;->A(')
        text = text.replace(':cond_131', ':cond_12f')
        patched, _ = patcher.patch_gcam_init_smali_text(text)
        self.assertIn('Laaaj;->s:Laaaj;', patched)
        self.assertIn('Laaaj;->v:Laaaj;', patched)
        self.assertIn('Lvgq;->b:LaSet;', patched)
        self.assertIn('NativeMetadataConverter;->A(Lvgr;)', patched)
        self.assertIn('if-eqz v7, :cond_12f', patched)
        for old in names:
            self.assertNotIn(old, patched)
