import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "variant-compatibility-checker"))
sys.path.insert(0, str(ROOT / "ozon-uploader"))
sys.path.insert(0, str(ROOT / "ozon-adapter"))

from variant_compatibility_checker import (  # noqa: E402
    build_grouping_result,
    build_variant_decision,
    load_variant_rule,
    validate_grouping_result,
    validate_variant_decision,
)
from ozon_uploader.service import build_import_items  # noqa: E402


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


@unittest.skipUnless((ROOT / "products/P000011/input/source.json").is_file(), "optional runtime product fixture is not installed")
class CapacityVariantMappingTest(unittest.TestCase):
    def setUp(self):
        self.product = ROOT / "products/P000011"
        self.source = load(self.product / "input/source.json")
        self.category = load(self.product / "output/ozon-category.json")
        self.draft = load(self.product / "output/ozon-draft.json")
        self.config = load(self.product / "output/ozon-upload-config.json")
        self.raw = load(self.product / "input/raw-snapshot.json")

    def build(self):
        rule = load_variant_rule(
            ROOT / "ozon-adapter/metadata/ozon-rules-2026-07-10",
            self.category["category_id"],
            self.category["type_id"],
        )
        decision = build_variant_decision(
            "P000011",
            self.source,
            self.category["category_id"],
            self.category["type_id"],
            rule,
            "ozon-rules-2026-07-10",
        )
        grouping = build_grouping_result(
            "P000011", self.source, decision, self.draft, self.config, self.raw
        )
        return decision, grouping

    def test_jin_sku_labels_map_to_official_ozon_volume_aspect(self):
        decision, grouping = self.build()
        self.assertEqual(validate_variant_decision(decision), [])
        self.assertEqual(validate_grouping_result(grouping), [])
        self.assertTrue(decision["platform_can_merge"])
        self.assertEqual(decision["difference_type"], "Объем, мл")
        self.assertEqual(
            decision["detected_difference_fields"][0]["mapped_variant_fields"],
            [{"attribute_id": 6788, "attribute_name": "Объем, мл"}],
        )
        self.assertEqual(grouping["variant_mapping_status"], "MAPPED")
        self.assertEqual(grouping["upload_strategy"], "single_card_variants")
        self.assertTrue(grouping["upload_allowed"])

        by_sku = {item["sku_id"]: item for item in grouping["variants"]}
        smaller = by_sku["5872460434733"]["variant_attribute_values"][0]
        larger = by_sku["5872460434730"]["variant_attribute_values"][0]
        self.assertEqual((smaller["value"], smaller["dictionary_value_id"]), ("12500 мл", 971392619))
        self.assertEqual((larger["value"], larger["dictionary_value_id"]), ("25000 мл", 970824500))
        self.assertTrue(smaller["estimated"] and larger["estimated"])

    def test_each_offer_receives_its_own_volume_dictionary_value_without_stock(self):
        _, grouping = self.build()
        items = build_import_items(
            self.draft,
            self.config,
            ["https://images.example.test/shared.png"],
            variant_grouping=grouping,
            variant_main_image_urls={
                "5872460434730": "https://images.example.test/40jin.png",
                "5872460434733": "https://images.example.test/20jin.png",
            },
        )
        by_offer = {item["offer_id"]: item for item in items}
        expected = {
            "P000011-5872460434730": ("25000 мл", 970824500),
            "P000011-5872460434733": ("12500 мл", 971392619),
        }
        for offer_id, (value, dictionary_id) in expected.items():
            attribute = next(item for item in by_offer[offer_id]["attributes"] if item["id"] == 6788)
            self.assertEqual(attribute["values"], [{"dictionary_value_id": dictionary_id, "value": value}])
            self.assertEqual(by_offer[offer_id]["images"][1:], ["https://images.example.test/shared.png"])
            self.assertNotIn("stock", by_offer[offer_id])
            self.assertNotIn("warehouse_id", by_offer[offer_id])
        self.assertEqual(by_offer["P000011-5872460434730"]["primary_image"], "https://images.example.test/40jin.png")
        self.assertEqual(by_offer["P000011-5872460434733"]["primary_image"], "https://images.example.test/20jin.png")


if __name__ == "__main__":
    unittest.main()
