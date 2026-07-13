import copy
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.image_generator_contract import build_prompt_packet
from scripts.product_positioning_agent import build_positioning_draft
from scripts.style_selector import ROOT, load_json
from scripts.validate_product import (
    validate_positioning_integrity,
    validate_product,
    validate_schema,
)


PRODUCT_IDS = ("P000004", "P000005", "P000003")


@unittest.skipUnless((ROOT / "products/P000001/input/source.json").is_file(), "optional runtime product fixture is not installed")
class Stage3ProductPositioningTest(unittest.TestCase):
    def test_real_positioning_files_match_schema_and_evidence_rules(self):
        for product_id in PRODUCT_IDS:
            product = ROOT / "products" / product_id
            self.assertEqual(
                validate_schema(
                    product / "output/product-positioning.json",
                    ROOT / "templates/product-positioning.schema.json",
                ),
                [],
            )
            self.assertEqual(validate_positioning_integrity(product), [])

    def test_three_products_have_distinct_sales_logic(self):
        values = {
            product_id: load_json(ROOT / "products" / product_id / "output/product-positioning.json")
            for product_id in PRODUCT_IDS
        }
        self.assertIn("出发前", values["P000004"]["core_sales_angle"])
        self.assertIn("厨房", values["P000005"]["core_sales_angle"])
        self.assertIn("户外铺装场景", values["P000003"]["core_sales_angle"])
        self.assertEqual(len({value["core_sales_angle"] for value in values.values()}), 3)

    def test_price_position_remains_unknown_without_market_evidence(self):
        for product_id in PRODUCT_IDS:
            positioning = load_json(ROOT / "products" / product_id / "output/product-positioning.json")
            self.assertEqual(positioning["recommended_price_position"], "unknown")

    def test_positioning_is_carried_into_style_and_image_plan(self):
        for product_id in PRODUCT_IDS:
            product = ROOT / "products" / product_id
            expected = f"products/{product_id}/output/product-positioning.json"
            style = load_json(product / "output/style-profile.json")
            plan = load_json(product / "output/image-plan.json")
            positioning = load_json(product / "output/product-positioning.json")
            self.assertEqual(style["positioning_ref"], expected)
            self.assertIn(expected, plan["source_refs"])
            self.assertEqual(plan["buyer_analysis"]["who_buys"], [positioning["target_customer"]])
            self.assertEqual(plan["buyer_analysis"]["main_pain_point"], positioning["customer_pain_points"][0])
            self.assertEqual(plan["buyer_analysis"]["strongest_selling_point"], positioning["core_sales_angle"])

    def test_generator_prompt_packet_contains_product_positioning(self):
        packet = build_prompt_packet(ROOT / "products/P000004", "main-001")
        self.assertIn("出发前", packet["product_positioning"]["core_sales_angle"])
        self.assertEqual(packet["product_positioning"]["emotional_trigger"], "让旅行准备更可控、更安心")

    def test_agent_draft_is_conservative_before_codex_refinement(self):
        product = ROOT / "products/P000005"
        draft = build_positioning_draft(product, load_json(product / "output/product-analysis.json"))
        self.assertEqual(draft["recommended_price_position"], "unknown")
        self.assertEqual(draft["customer_pain_points"], ["unknown"])
        self.assertEqual(draft["processing"]["status"], "in_progress")

    def test_missing_evidence_is_rejected(self):
        product = ROOT / "products/P000004"
        original = load_json(product / "output/product-positioning.json")
        changed = copy.deepcopy(original)
        changed["positioning_evidence"] = [
            item for item in changed["positioning_evidence"] if item["field"] != "core_sales_angle"
        ]

        def fake_load(path):
            if Path(path).name == "product-positioning.json":
                return changed
            return load_json(Path(path))

        with patch("scripts.validate_product.load_json", side_effect=fake_load):
            errors = validate_positioning_integrity(product)
        self.assertTrue(any("core_sales_angle requires positioning evidence" in error for error in errors))

    def test_existing_full_products_still_validate(self):
        self.assertEqual(validate_product(ROOT / "products/P000001"), [])
        self.assertEqual(validate_product(ROOT / "products/P000004"), [])


if __name__ == "__main__":
    unittest.main()
