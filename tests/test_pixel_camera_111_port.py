import unittest
from scripts.pixel_camera_111_port import patch_optional, patch_missing_key, patch_odr, normalize_patch
from tests.test_patch_pixel_camera_device_gate import OFE_SAMPLE, MTA_SAMPLE, ODR_SAMPLE
from scripts.patch_pixel_camera_device_gate import PatchError

class Pixel111PortTests(unittest.TestCase):
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
