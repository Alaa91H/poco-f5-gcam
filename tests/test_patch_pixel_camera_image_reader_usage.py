import unittest

from scripts import patch_pixel_camera_image_reader_usage as usage


SOURCE = """.class public final Luvx;
.super Ljava/lang/Object;
.method public final a(Luzq;)Luzt;
    .registers 8
    :cond_1c
    iget-wide v2, p1, Luzq;->e:J

    const-wide/16 v4, -0x1

    cmp-long v0, v2, v4
.end method
"""


class ImageReaderUsageTests(unittest.TestCase):
    def test_preserves_builder_and_configuration_outside_guard(self):
        patched = usage.patch_smali(SOURCE)
        self.assertEqual(patched.replace(usage.GUARD, '', 1), SOURCE)
        self.assertEqual(patched.count('p1, Luzq;->e:J'), 1)
        self.assertNotIn('iput', patched)

    def test_rejects_changed_class_registers_and_usage_field(self):
        for changed in (SOURCE.replace('Luvx;', 'Lother;'),
                        SOURCE.replace('.registers 8', '.registers 9'),
                        SOURCE.replace('Luzq;->e:J', 'Luzq;->f:J')):
            with self.assertRaises(ValueError):
                usage.patch_smali(changed)

    def test_cannot_apply_twice(self):
        with self.assertRaises(ValueError):
            usage.patch_smali(usage.patch_smali(SOURCE))


if __name__ == '__main__':
    unittest.main()
