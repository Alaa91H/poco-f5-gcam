import unittest

from scripts import patch_image_reader_diagnostics as diagnostic


SAMPLE = """.class public final Luvx;
.super Ljava/lang/Object;
.method public final a(Luzq;)Luzt;
    .registers 8

    iget v0, p0, Luvx;->b:I
    if-eqz v0, :fallback
    new-instance p0, Landroid/media/ImageReader$Builder;
    iget v0, p1, Luzq;->a:I
    iget v1, p1, Luzq;->b:I
    invoke-direct {p0, v0, v1}, Landroid/media/ImageReader$Builder;-><init>(II)V
    return-object p1
    :fallback
    return-object p1
.end method
"""


class ImageReaderDiagnosticTests(unittest.TestCase):
    def test_logs_before_builder_and_preserves_original_instructions(self):
        result = diagnostic.patch_smali(SAMPLE)
        insertion = result.index('const-string v0, "GCamImageReaderConfig"')
        original = result.index('iget v0, p0, Luvx;->b:I')
        self.assertLess(insertion, original)
        self.assertIn('invoke-virtual {p1}, Luzq;->toString()', result)
        self.assertEqual(result.replace(diagnostic.LOG_BLOCK, '', 1), SAMPLE)

    def test_rejects_changed_register_layout(self):
        with self.assertRaises(ValueError):
            diagnostic.patch_smali(SAMPLE.replace('.registers 8', '.registers 9'))

    def test_rejects_another_class_or_method(self):
        for text in (SAMPLE.replace('Luvx;', 'Lother;'),
                     SAMPLE.replace('a(Luzq;)Luzt;', 'a(Lother;)Luzt;')):
            with self.assertRaises(ValueError):
                diagnostic.patch_smali(text)

    def test_rejects_duplicate_instrumentation(self):
        with self.assertRaises(ValueError):
            diagnostic.patch_smali(diagnostic.patch_smali(SAMPLE))


if __name__ == '__main__':
    unittest.main()
