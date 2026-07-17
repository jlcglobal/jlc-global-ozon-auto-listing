import copy
import os
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from scripts.image_qc import (
    RULES_PATH,
    build_report,
    decision_for,
    detect_empty_placeholder_panel,
    load_json,
    validate_report,
)
from scripts.style_selector import ROOT
from scripts.validate_product import validate_product, validate_schema


P4 = ROOT / "products" / "P000004"


class EmptyPlaceholderPanelDetectionTest(unittest.TestCase):
    def test_blank_panel_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "blank-panel.png"
            image = Image.new("RGB", (1086, 1448), (220, 220, 220))
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle((80, 1050, 1000, 1300), radius=30, fill=(20, 45, 35), outline=(220, 180, 30), width=5)
            image.save(path)
            evidence = detect_empty_placeholder_panel(path)
            self.assertIsNotNone(evidence)
            self.assertGreater(evidence["width"], 0.7)
            self.assertLess(evidence["interior_stddev"], 18)

    def test_product_scene_is_not_treated_as_blank_panel(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scene.png"
            image = Image.new("RGB", (1086, 1448), (220, 220, 220))
            draw = ImageDraw.Draw(image)
            draw.rectangle((300, 650, 750, 1300), fill=(40, 50, 120))
            draw.ellipse((200, 200, 800, 800), fill=(120, 180, 220))
            image.save(path)
            self.assertIsNone(detect_empty_placeholder_panel(path))


@unittest.skipUnless(os.environ.get("CAF_RUN_LEGACY_FIXTURES") == "1", "legacy runtime fixture suite is isolated from active tests")
class Stage34ImageQCTest(unittest.TestCase):
    def test_weights_and_criteria_total_one_hundred(self):
        rules = load_json(RULES_PATH)
        self.assertEqual(sum(rules["weights"].values()), 100)
        for dimension, weight in rules["weights"].items():
            self.assertEqual(sum(rules["criteria"][dimension].values()), weight)

    def test_decision_thresholds_are_deterministic(self):
        rules = load_json(RULES_PATH)
        self.assertEqual(decision_for(90, [], rules), "pass")
        self.assertEqual(decision_for(89, [], rules), "revise")
        self.assertEqual(decision_for(75, [], rules), "revise")
        self.assertEqual(decision_for(74, [], rules), "reject")
        self.assertEqual(decision_for(100, ["product_identity_changed"], rules), "reject")

    @unittest.skipUnless((P4 / "logs/image-qc-assessment.json").is_file(), "legacy image QC assessment is not installed")
    def test_p000004_report_is_reproducible_and_technical_checks_pass(self):
        assessment = load_json(P4 / "logs/image-qc-assessment.json")
        report = build_report(P4, assessment, checked_at="2026-07-10T23:50:00+08:00")
        self.assertEqual(report["score"], 96)
        self.assertEqual(report["decision"], "pass")
        self.assertFalse(report["regenerate_needed"])
        self.assertEqual(len(report["images_checked"]), 4)
        self.assertTrue(all(item["status"] == "pass" for item in report["technical_checks"]))
        self.assertTrue(all(item["aspect_ratio"] == "3:4" for item in report["technical_checks"]))
        self.assertTrue(all((item["width"], item["height"]) == (1086, 1448) for item in report["technical_checks"]))

    @unittest.skipUnless((P4 / "logs/image-qc-assessment.json").is_file(), "legacy image QC assessment is not installed")
    def test_critical_visual_failure_overrides_high_score(self):
        assessment = load_json(P4 / "logs/image-qc-assessment.json")
        assessment["critical_failures"] = ["product_structure_changed"]
        report = build_report(P4, assessment, checked_at="2026-07-10T23:50:00+08:00")
        self.assertEqual(report["score"], 96)
        self.assertEqual(report["decision"], "reject")
        self.assertTrue(report["regenerate_needed"])

    def test_real_report_matches_schema_and_score_integrity(self):
        report_path = P4 / "output/image-qc-report.json"
        self.assertEqual(validate_schema(report_path, ROOT / "templates/image-qc-report.schema.json"), [])
        self.assertEqual(validate_report(load_json(report_path)), [])
        self.assertEqual(validate_product(P4), [])

    def test_tampered_decision_is_rejected(self):
        report = copy.deepcopy(load_json(P4 / "output/image-qc-report.json"))
        report["decision"] = "revise"
        errors = validate_report(report)
        self.assertTrue(any("decision" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
