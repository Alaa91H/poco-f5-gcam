import unittest
from scripts import patch_pixel_camera_enumeration as enumeration


class EnumerationPatchTests(unittest.TestCase):
    def test_rewrites_only_enumeration_invocations(self):
        source = '.class public final Lsy;\n' + enumeration.ORIGINAL.format(reg='v0') + '\n    move-result-object v0\n'
        result = enumeration.patch_smali(source, 'Lsy;', 1)
        self.assertEqual(result.replace(enumeration.REPLACEMENT.format(reg='v0'),
                                        enumeration.ORIGINAL.format(reg='v0')), source)

    def test_layout_drift_and_reapplication_fail_closed(self):
        source = '.class public final Lsy;\n' + enumeration.ORIGINAL.format(reg='v0')
        for text, owner, count in [(source, 'Lkwp;', 1), (source, 'Lsy;', 2),
                                    (source.replace('invoke-virtual', 'invoke-interface'), 'Lsy;', 1)]:
            with self.assertRaises(ValueError):
                enumeration.patch_smali(text, owner, count)
        with self.assertRaises(ValueError):
            enumeration.patch_smali(enumeration.patch_smali(source, 'Lsy;', 1), 'Lsy;', 1)
