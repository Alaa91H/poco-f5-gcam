import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import pixel_camera_gate as gate


def candidate(version: str) -> dict:
    return {
        "version": version,
        "min_api": 37,
        "min_android": 17,
        "architectures": ["arm64-v8a"],
        "dpis": ["nodpi"],
        "release_url": f"https://example.test/{version}",
    }


class PixelCameraGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.policy = self.root / "policy.json"
        self.lock = self.root / "lock.json"
        self.approved = self.root / "approved.json"
        self.result = self.root / "result.json"

        self.policy.write_text(
            json.dumps(
                {
                    "compatibility": {
                        "package_name": "com.google.android.GoogleCamera"
                    },
                    "validation": {
                        "required_checks": [
                            "install",
                            "launchable",
                            "main-preview-confirmed",
                        ]
                    },
                }
            ),
            encoding="utf-8",
        )
        self.lock.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "candidate": candidate("11.0.3"),
                    "candidates": [
                        candidate("11.0.3"),
                        candidate("11.0.2"),
                        candidate("11.0.1"),
                    ],
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def write_result(self, version: str, statuses: dict[str, str]):
        checks = {
            name: {"status": status, "detail": name}
            for name, status in statuses.items()
        }
        self.result.write_text(
            json.dumps(
                {
                    "candidate_version": version,
                    "package_name": "com.google.android.GoogleCamera",
                    "checks": checks,
                    "validated_at": "2026-09-25T12:00:00Z",
                }
            ),
            encoding="utf-8",
        )

    def test_pass_promotes_candidate_and_keeps_next_candidate(self):
        self.write_result(
            "11.0.3",
            {
                "install": "pass",
                "launchable": "pass",
                "main-preview-confirmed": "pass",
            },
        )

        promoted, state = gate.evaluate(
            self.policy, self.lock, self.result, self.approved
        )

        self.assertTrue(promoted)
        self.assertEqual(state["status"], "approved")
        self.assertEqual(state["effective"]["version"], "11.0.3")
        self.assertEqual(state["last_known_good"]["version"], "11.0.3")
        self.assertEqual(state["next_candidate"]["version"], "11.0.2")

    def test_failed_latest_without_known_good_selects_older_candidate(self):
        self.write_result(
            "11.0.3",
            {
                "install": "pass",
                "launchable": "fail",
                "main-preview-confirmed": "fail",
            },
        )

        promoted, state = gate.evaluate(
            self.policy, self.lock, self.result, self.approved
        )

        self.assertFalse(promoted)
        self.assertEqual(state["status"], "no-approved-runtime")
        self.assertIsNone(state["effective"])
        self.assertEqual(state["next_candidate"]["version"], "11.0.2")
        self.assertEqual(
            state["last_gate_failure"]["failed_checks"],
            ["launchable", "main-preview-confirmed"],
        )

    def test_failed_update_preserves_last_known_good(self):
        self.approved.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "approved",
                    "effective": candidate("11.0.2"),
                    "last_known_good": candidate("11.0.2"),
                    "rejected": [],
                }
            ),
            encoding="utf-8",
        )
        self.write_result(
            "11.0.3",
            {
                "install": "pass",
                "launchable": "fail",
                "main-preview-confirmed": "pass",
            },
        )

        promoted, state = gate.evaluate(
            self.policy, self.lock, self.result, self.approved
        )

        self.assertFalse(promoted)
        self.assertEqual(state["status"], "fallback-active")
        self.assertEqual(state["effective"]["version"], "11.0.2")
        self.assertEqual(state["last_known_good"]["version"], "11.0.2")
        self.assertEqual(state["next_candidate"]["version"], "11.0.1")

    def test_missing_required_check_rejects_candidate(self):
        self.write_result(
            "11.0.3",
            {
                "install": "pass",
                "launchable": "pass",
            },
        )

        promoted, state = gate.evaluate(
            self.policy, self.lock, self.result, self.approved
        )

        self.assertFalse(promoted)
        self.assertIn(
            "main-preview-confirmed",
            state["last_gate_failure"]["failed_checks"],
        )

    def test_package_mismatch_is_rejected_as_invalid_evidence(self):
        self.result.write_text(
            json.dumps(
                {
                    "candidate_version": "11.0.3",
                    "package_name": "wrong.package",
                    "checks": {},
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaises(ValueError):
            gate.evaluate(self.policy, self.lock, self.result, self.approved)


if __name__ == "__main__":
    unittest.main()
