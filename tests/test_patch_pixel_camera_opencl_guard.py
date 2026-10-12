import hashlib
import unittest
from unittest.mock import patch

from scripts import patch_pixel_camera_opencl_guard as guard


class OpenClGuardTests(unittest.TestCase):
    def setUp(self):
        self.original = b'header' + guard.ORIGINAL + b'trailer'
        self.offset = patch.object(guard, 'OFFSET', 6)
        self.digest = patch.object(
            guard, 'SOURCE_SHA256', hashlib.sha256(self.original).hexdigest()
        )
        self.offset.start()
        self.digest.start()
        self.addCleanup(self.offset.stop)
        self.addCleanup(self.digest.stop)

    def test_changes_only_the_verified_wrapper(self):
        result = guard.patch_library(self.original)
        self.assertEqual(len(result), len(self.original))
        self.assertEqual(result, b'header' + guard.REPLACEMENT + b'trailer')

    def test_rejects_unrelated_library_changes(self):
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            guard.patch_library(self.original[:-1] + b'!')

    def test_rejects_wrong_instructions_even_with_matching_hash(self):
        changed = self.original[:6] + b'\x00' + self.original[7:]
        with patch.object(guard, 'SOURCE_SHA256', hashlib.sha256(changed).hexdigest()):
            with self.assertRaisesRegex(ValueError, 'instructions'):
                guard.patch_library(changed)

    def test_rejects_double_application(self):
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            guard.patch_library(guard.patch_library(self.original))
