import unittest

from scripts.select_pixel_camera_build_source import select_release


class BuildSourceTests(unittest.TestCase):
    def test_standalone_uses_supported_release_when_latest_advances(self):
        latest = {'selected': {'version': '11.1.040.982810059.19'}}
        supported = {'selected': {'version': '11.0.073.972752740.32'}}
        self.assertEqual(select_release(latest, supported, True), supported)

    def test_upstream_audit_still_uses_latest_release(self):
        latest = {'selected': {'version': '11.1.040.982810059.19'}}
        self.assertEqual(select_release(latest, {}, False), latest)

    def test_unknown_patch_version_fails_before_download(self):
        unknown = {'selected': {'version': '11.1.040.982810059.19'}}
        with self.assertRaisesRegex(ValueError, 'unsupported'):
            select_release(unknown, unknown, True)
