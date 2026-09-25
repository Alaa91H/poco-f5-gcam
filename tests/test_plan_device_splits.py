import unittest

from scripts.plan_device_splits import build_plan


def apk(
    name,
    size,
    *,
    split=None,
    uses=None,
    config_for=None,
    manifest_available=True,
):
    return {
        "name": name,
        "file_size_bytes": size,
        "manifest": {
            "available": manifest_available,
            "package": "com.google.android.GoogleCamera",
            "split": split,
            "uses_splits": uses or [],
            "config_for_split": config_for,
            "is_feature_split": split is not None,
        },
    }


class SplitPlannerTests(unittest.TestCase):
    def test_builds_conservative_dependency_plan(self):
        report = {
            "input": {"sha256": "abc"},
            "apks": [
                apk("base.apk", 100),
                apk("feature-a.apk", 200, split="feature_a"),
                apk(
                    "feature-a-config.apk",
                    20,
                    split="config.arm64",
                    config_for="feature_a",
                ),
                apk(
                    "feature-b.apk",
                    300,
                    split="feature_b",
                    uses=["feature_a"],
                ),
                apk("leaf.apk", 400, split="leaf"),
            ],
        }

        plan = build_plan(report)
        by_split = {item["split"]: item for item in plan["splits"]}

        base = next(item for item in plan["splits"] if item["split"] is None)
        self.assertEqual(base["status"], "keep-base")
        self.assertEqual(by_split["feature_a"]["status"], "keep-dependency")
        self.assertEqual(
            by_split["config.arm64"]["status"],
            "keep-with-parent",
        )
        self.assertEqual(
            by_split["feature_b"]["status"],
            "requires-device-validation",
        )
        self.assertEqual(
            by_split["leaf"]["status"],
            "requires-device-validation",
        )
        self.assertEqual(plan["validation_candidate_bytes"], 700)
        self.assertEqual(plan["missing_dependency_count"], 0)
        self.assertTrue(plan["manifest_metadata_complete"])

    def test_missing_manifest_stays_unknown(self):
        report = {
            "input": {"sha256": "abc"},
            "apks": [
                apk("base.apk", 100),
                apk(
                    "mystery.apk",
                    500,
                    split=None,
                    manifest_available=False,
                ),
            ],
        }

        plan = build_plan(report)
        mystery = next(
            item for item in plan["splits"] if item["apk_name"] == "mystery.apk"
        )
        self.assertEqual(mystery["status"], "unknown-manifest")
        self.assertFalse(plan["manifest_metadata_complete"])
        self.assertEqual(plan["validation_candidate_bytes"], 0)

    def test_reports_missing_declared_dependency(self):
        report = {
            "input": {"sha256": "abc"},
            "apks": [
                apk("base.apk", 100),
                apk(
                    "feature.apk",
                    200,
                    split="feature",
                    uses=["missing_parent"],
                ),
            ],
        }

        plan = build_plan(report)
        self.assertEqual(plan["missing_dependency_count"], 1)
        self.assertEqual(
            plan["missing_dependencies"][0]["missing"],
            "missing_parent",
        )


if __name__ == "__main__":
    unittest.main()
