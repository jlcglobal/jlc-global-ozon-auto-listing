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
from scripts.image_planner import ROOT
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


if __name__ == "__main__":
    unittest.main()
