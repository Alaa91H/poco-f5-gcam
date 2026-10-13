import unittest
from unittest.mock import patch
from scripts import patch_pixel_camera_device_gate as p


class NativePatchProfilesTests(unittest.TestCase):
    def test_new_library_selects_relocated_verified_branches(self):
        with patch.object(p.hashlib, 'sha256') as digest:
            digest.return_value.hexdigest.return_value = '142f699b735372c2b7dbf6f950bf9163c5cb5d9ccf9903592ca28673c2c6f434'
            profile = p.native_patch_profile(b'fixture')
        self.assertEqual(profile['tuning_offset'], 0x69C38EC)
        self.assertEqual(profile['tuning_target'], 'cbz 0x69c3b80')
        self.assertEqual(profile['aion_offset'], 0x68C1A58)
        self.assertEqual(len(profile['patches']), 5)

    def test_unknown_library_is_not_patched_using_old_offsets(self):
        with self.assertRaisesRegex(p.PatchError, 'SHA-256 changed'):
            p.native_patch_profile(b'unsupported library')
