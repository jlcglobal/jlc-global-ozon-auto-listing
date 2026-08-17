import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from scripts.image_planner import ROOT, build_image_plan, load_json
from scripts.ozon_ecommerce_designer_contract import materialize
from scripts.production_input_guard import write_source_manifest
from tests.test_ozon_ecommerce_designer_contract import build_design, make_product

sys.path.insert(0, str(ROOT / "ozon-uploader"))
sys.path.insert(0, str(ROOT / "ozon-adapter"))
from ozon_uploader.service import build_import_items  # noqa: E402


class VariantImageStrategyTests(unittest.TestCase):
    def build_plan(self, difference_kind: str, source_field: str, values: list[str]):
        with tempfile.TemporaryDirectory() as directory:
            product_dir, _ = make_product(Path(directory), "P888888", len(values))
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
            source_path = product_dir / "input/source.json"
            source = json.loads(source_path.read_text(encoding="utf-8"))
            for index, (sku, value) in enumerate(zip(source["skus"], values), start=1):
                sku.update({
                    "sku_name": value,
                    "selection_order": index,
                    "variant_local_image_path": sku["local_image_path"],
                    "sku_image_missing": False,
                    "option_values": [{"name_cn": source_field, "value_cn": value}],
                })
            source_path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
            write_source_manifest(product_dir)
            design = build_design(product_dir, source["skus"])
            (product_dir / "output/ozon-ecommerce-design.json").write_text(
                json.dumps(design, ensure_ascii=False), encoding="utf-8"
            )
            materialize(product_dir, design)
            plan = build_image_plan(
                product_dir,
                source,
                {"buyer_objections": [], "confirmed_facts": [], "unknown_fields": []},
                {},
                started_at="2026-07-12T00:00:00+08:00",
            )
            return copy.deepcopy(plan)

    @unittest.skipUnless((ROOT / "products/P000004/input/source.json").is_file(), "optional runtime product fixture is not installed")
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
                self.assertEqual(len(plan["detail_images"]), 8)
                self.assertEqual(len(plan["disclaimer_images"]), 0)
                self.assertEqual(plan["variant_image_strategy"]["mode"], "sku_specific_main_shared_details")
                self.assertTrue(all(item["variant_scope"] == "sku" for item in plan["main_images"]))
                self.assertTrue(all(item["shared_across_variants"] for item in plan["detail_images"]))
                self.assertTrue(all(item["shared_across_variants"] for item in plan["disclaimer_images"]))
                self.assertEqual(
                    [item["reference_images"][0] for item in plan["main_images"]],
                    [
                        "products/P888888/input/sku-images/sku-1.png",
                        "products/P888888/input/sku-images/sku-2.png",
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

    def test_separate_cards_keep_own_images_and_share_one_product_model_value(self):
        draft = {
            "description_category_id": 100,
            "type_id": 200,
            "title": "Портативный миксер",
            "skus": [
                {"source_sku_id": "sku-350", "offer_id": "P-sku-350", "display_name_ru": "350 мл"},
                {"source_sku_id": "sku-400", "offer_id": "P-sku-400", "display_name_ru": "400 мл"},
            ],
        }
        config = {
            "sku_prices": [
                {"source_sku_id": "sku-350", "price": "100"},
                {"source_sku_id": "sku-400", "price": "110"},
            ],
            "sku_colors": [],
            "package_dimensions": {"length_mm": 10, "width_mm": 11, "height_mm": 12},
            "package_weight": {"value_g": 100},
            "brand": {"attribute_id": 1, "dictionary_value_id": 10, "value": "Нет бренда"},
            "model_name": {"attribute_id": 2, "value": "Mixer P1"},
            "type": {"attribute_id": 3, "dictionary_value_id": 30, "value": "Type"},
            "currency_code": "RUB", "vat": "0", "old_price": None,
        }
        grouping = {
            "variant_mapping_status": "SEPARATE_CARDS_REQUIRED",
            "upload_strategy": "separate_cards",
            "platform_can_merge": False,
            "variants": [],
            "mapping_requirements": {"difference_types": ["size_or_measurement"]},
        }
        items = build_import_items(
            draft,
            config,
            ["https://images/shared.png"],
            variant_grouping=grouping,
            variant_main_image_urls={
                "sku-350": "https://images/350.png",
                "sku-400": "https://images/400.png",
            },
        )
        self.assertEqual([item["primary_image"] for item in items], ["https://images/350.png", "https://images/400.png"])
        self.assertEqual([item["images"][1:] for item in items], [["https://images/shared.png"], ["https://images/shared.png"]])
        self.assertEqual({item["name"] for item in items}, {"Type, 350 мл", "Type, 400 мл"})
        model_values = {
            next(attribute for attribute in item["attributes"] if attribute["id"] == 2)["values"][0]["value"]
            for item in items
        }
        self.assertEqual(model_values, {"Mixer P1 sku-350", "Mixer P1 sku-400"})
        self.assertTrue(all("stock" not in item and "warehouse_id" not in item for item in items))


if __name__ == "__main__":
    unittest.main()
