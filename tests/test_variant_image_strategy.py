import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from scripts.image_planner import build_image_plan
from scripts.style_selector import ROOT, load_json

sys.path.insert(0, str(ROOT / "ozon-uploader"))
sys.path.insert(0, str(ROOT / "ozon-adapter"))
from ozon_uploader.service import build_import_items  # noqa: E402


class VariantImageStrategyTests(unittest.TestCase):
    def build_plan(self, difference_kind: str, source_field: str, values: list[str]):
        with tempfile.TemporaryDirectory() as directory:
            product_dir = Path(directory) / "products/P888888"
            (product_dir / "output").mkdir(parents=True)
            (product_dir / "output/product-positioning.json").write_text(
                (ROOT / "products/P000004/output/product-positioning.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            decision = {
                "detected_difference_fields": [{
                    "source_field": source_field,
                    "source_values": values,
                    "difference_kind": difference_kind,
                    "mapped_variant_fields": [{"attribute_id": 1, "attribute_name": "fixture"}],
                    "compatible": True,
                }],
            }
            (product_dir / "output/variant-decision.json").write_text(json.dumps(decision), encoding="utf-8")
            source = {
                "title_cn": "测试商品",
                "main_images": [], "detail_images": [],
                "skus": [
                    {
                        "sku_id": f"sku-{index}", "sku_name": value, "selection_order": index,
                        "local_image_path": f"products/P888888/input/sku-images/sku-{index}.jpg",
                        "variant_local_image_path": f"products/P888888/input/sku-images/sku-{index}.jpg",
                        "sku_image_missing": False,
                        "option_values": [{"name_cn": source_field, "value_cn": value}],
                    }
                    for index, value in enumerate(values, start=1)
                ],
            }
            plan = build_image_plan(
                product_dir,
                source,
                load_json(ROOT / "products/P000004/output/product-analysis.json"),
                load_json(ROOT / "products/P000004/output/style-profile.json"),
                started_at="2026-07-12T00:00:00+08:00",
            )
            return copy.deepcopy(plan)

    def test_color_size_and_quantity_only_multiply_main_images(self):
        cases = [
            ("color", "颜色", ["绿色", "卡其色"]),
            ("size_or_measurement", "尺寸", ["小号", "大号"]),
            ("configuration", "数量", ["2个装", "4个装"]),
        ]
        schema = load_json(ROOT / "templates/image-plan.schema.json")
        for kind, field, values in cases:
            with self.subTest(kind=kind):
                plan = self.build_plan(kind, field, values)
                self.assertEqual(len(plan["main_images"]), 2)
                self.assertEqual(len(plan["detail_images"]), 6)
                self.assertEqual(len(plan["disclaimer_images"]), 1)
                self.assertEqual(plan["variant_image_strategy"]["mode"], "sku_specific_main_shared_details")
                self.assertTrue(all(item["variant_scope"] == "sku" for item in plan["main_images"]))
                self.assertTrue(all(item["shared_across_variants"] for item in plan["detail_images"]))
                self.assertTrue(all(item["shared_across_variants"] for item in plan["disclaimer_images"]))
                self.assertEqual(
                    [item["reference_images"][0] for item in plan["main_images"]],
                    [
                        "products/P888888/input/sku-images/sku-1.jpg",
                        "products/P888888/input/sku-images/sku-2.jpg",
                    ],
                )
                self.assertEqual(list(Draft202012Validator(schema).iter_errors(plan)), [])

    def test_each_offer_receives_own_main_and_shared_images(self):
        draft = {
            "description_category_id": 100,
            "type_id": 200,
            "title": "Товар",
            "skus": [
                {"source_sku_id": "sku-1", "offer_id": "P-sku-1"},
                {"source_sku_id": "sku-2", "offer_id": "P-sku-2"},
            ],
        }
        config = {
            "sku_prices": [
                {"source_sku_id": "sku-1", "price": "100"},
                {"source_sku_id": "sku-2", "price": "110"},
            ],
            "sku_colors": [],
            "package_dimensions": {"length_mm": 10, "width_mm": 11, "height_mm": 12},
            "package_weight": {"value_g": 100},
            "brand": {"attribute_id": 1, "dictionary_value_id": 10, "value": "Нет бренда"},
            "model_name": {"attribute_id": 2, "value": "Model"},
            "type": {"attribute_id": 3, "dictionary_value_id": 30, "value": "Type"},
            "currency_code": "RUB", "vat": "0", "old_price": None,
        }
        grouping = {
            "variants": [],
            "mapping_requirements": {"difference_types": ["color"]},
        }
        items = build_import_items(
            draft,
            config,
            ["https://images/shared-1.png", "https://images/shared-2.png"],
            variant_grouping=grouping,
            variant_main_image_urls={
                "sku-1": "https://images/sku-1.png",
                "sku-2": "https://images/sku-2.png",
            },
        )
        self.assertEqual(items[0]["primary_image"], "https://images/sku-1.png")
        self.assertEqual(items[1]["primary_image"], "https://images/sku-2.png")
        self.assertNotIn("https://images/sku-2.png", items[0]["images"])
        self.assertNotIn("https://images/sku-1.png", items[1]["images"])
        self.assertEqual(items[0]["images"][1:], items[1]["images"][1:])
        self.assertEqual(items[0]["color_image"], "https://images/sku-1.png")


if __name__ == "__main__":
    unittest.main()
