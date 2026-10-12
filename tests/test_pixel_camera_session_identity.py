import unittest
from tests.test_patch_pixel_camera_device_gate import RP_SAMPLE
from scripts import patch_pixel_camera_device_gate as patcher


class SessionIdentityRegressionTests(unittest.TestCase):
    def test_diagnostics_do_not_inject_stock_camera_identity(self):
        patched, metadata = patcher.patch_onecamera_session_parameter_logging_smali_text(RP_SAMPLE)
        self.assertNotIn('"com.android.camera"', patched)
        self.assertNotIn('"com.xiaomi.sessionparams.clientName"', patched)
        self.assertIn('"GCamSessionParamKey"', patched)
        self.assertIn('Lvz;->x(Landroid/hardware/camera2/CaptureRequest$Builder;', patched)
        self.assertFalse(metadata['behavior_changed'])
