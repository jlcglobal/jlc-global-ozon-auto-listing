import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ozon-field-completion"))
sys.path.insert(0, str(ROOT / "ozon-uploader"))
sys.path.insert(0, str(ROOT / "ozon-adapter"))

from ozon_field_completion import build_package, validate_package  # noqa: E402
from ozon_field_completion.service import _auto_upload_config, build_color_variant_policy  # noqa: E402
from ozon_uploader.service import build_preflight, load_json  # noqa: E402


class OzonFieldCompletionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.product_dir = ROOT / "products/P000004"
        cls.package = build_package(cls.product_dir, write=False)

    def test_all_outputs_validate(self):
        validation = validate_package(self.package)
        self.assertTrue(all(not errors for errors in validation.values()), validation)

    def test_benchmark_separates_chinese_seller_ui_from_russian_buyer_content(self):
        data = load_json(ROOT / "rules/ozon_content_score_benchmarks.json")
        benchmark = data["benchmarks"][0]
        self.assertEqual(benchmark["seller_ui_locale"], "zh-CN")
        self.assertEqual(benchmark["buyer_content_locale"], "ru-RU")

    def test_exactly_30_unique_russian_hashtags(self):
        tags = self.package["ozon-tags.json"]
        self.assertEqual(tags["count"], 30)
        self.assertEqual(len(tags["tags"]), 30)
        self.assertEqual(len(set(tags["tags"])), 30)
        self.assertTrue(all(item.startswith("#") for item in tags["tags"]))
        self.assertFalse(any("hostweigh" in item.casefold() for item in tags["tags"]))

    def test_attributes_use_live_schema_and_preserve_unknowns(self):
        output = self.product_dir / "output"
        metadata = load_json(output / "ozon-category-attributes.json")
        final = self.package["ozon-attributes-final.json"]
        self.assertEqual(final["category_id"], metadata["category_id"])
        self.assertEqual(len(final["attributes"]), len(metadata["attributes"]))
        by_id = {item["attribute_id"]: item for item in final["attributes"]}
        self.assertEqual(by_id[4497]["value"], 150)
        self.assertEqual(by_id[4497]["source"], "AI_estimated")
        self.assertEqual(by_id[4382]["value"], "170 x 120 x 50")
        self.assertEqual(by_id[4383]["value"], 120)
        for unknown_id in (6056, 6057):
            self.assertEqual(by_id[unknown_id]["value"], "unknown")
            self.assertEqual(by_id[unknown_id]["source"], "unknown")
        self.assertEqual(final["required_summary"]["missing"], 0)

    def test_rich_content_is_json_not_html(self):
        rich = self.package["rich-content.json"]
        self.assertEqual(rich["status"], "ready")
        self.assertEqual(rich["content"]["version"], 0.3)
        parsed = json.loads(rich["serialized_json"])
        self.assertEqual(parsed, rich["content"])
        self.assertNotIn("<html", rich["serialized_json"].casefold())
        self.assertTrue(all(
            block["img"]["src"].startswith("https://ir.ozone.ru/")
            for widget in rich["content"]["content"]
            for block in widget["blocks"]
        ))

    def test_color_variants_are_never_randomly_filled(self):
        colors = self.package["color-variants.json"]
        by_sku = {item["sku_id"]: item for item in colors["variants"]}
        self.assertEqual(colors["summary"], {"total": 3, "mapped": 3, "missing": 0})
        self.assertEqual(by_sku["3993658310174"]["image"], "products/P000004/input/main-images/main-002.webp")
        self.assertEqual(by_sku["3993658310175"]["image"], "products/P000004/input/main-images/main-005.webp")
        self.assertEqual(by_sku["3993658310173"]["image"], "products/P000004/input/main-images/main-001.webp")
        self.assertEqual(by_sku["3993658310173"]["source"], "main_image_match")
        self.assertGreaterEqual(by_sku["3993658310173"]["confidence"], 0.9)

    def test_resolved_main_variant_allows_final_check(self):
        check = self.package["final-upload-check.json"]
        self.assertTrue(check["upload_allowed"])
        failed = {item["name"] for item in check["checks"] if not item["passed"]}
        self.assertEqual(failed, set())

    def test_main_missing_blocks_but_optional_missing_only_warns(self):
        source = load_json(self.product_dir / "input/source.json")
        colors = copy.deepcopy(self.package["color-variants.json"])
        colors["variants"][0].update({"status": "missing", "image": "missing", "source": "missing", "resolution_level": 4, "confidence": 0})
        blocked = build_color_variant_policy("P000004", source, colors)
        self.assertEqual(blocked["status"], "BLOCK")
        colors = copy.deepcopy(self.package["color-variants.json"])
        colors["variants"][1].update({"status": "missing", "image": "missing", "source": "missing", "resolution_level": 4, "confidence": 0})
        warning = build_color_variant_policy("P000004", source, colors)
        self.assertEqual(warning["status"], "WARNING")
        self.assertEqual(len(warning["warning_variants"]), 1)

    def test_uploader_preflight_includes_field_completion_gate(self):
        output = self.product_dir / "output"
        draft = load_json(output / "ozon-draft.json")
        status = load_json(self.product_dir / "status.json")
        config = load_json(output / "ozon-upload-config.json")
        metadata = load_json(output / "ozon-category-attributes.json")
        manifest = load_json(output / "ozon-images.json")
        preflight = build_preflight(
            self.product_dir, draft, status, config, metadata, manifest,
            "2026-07-11T00:00:00+00:00",
        )
        color_check = next(
            item for item in preflight["checks"]
            if item["name"] == "field_completion_color_variants"
        )
        self.assertTrue(color_check["passed"])

    def test_uploader_preflight_rejects_non_greater_package_measurements(self):
        output = self.product_dir / "output"
        config = load_json(output / "ozon-upload-config.json")
        config["package_dimensions"] = copy.deepcopy(config["product_dimensions"])
        preflight = build_preflight(
            self.product_dir,
            load_json(output / "ozon-draft.json"),
            load_json(self.product_dir / "status.json"),
            config,
            load_json(output / "ozon-category-attributes.json"),
            load_json(output / "ozon-images.json"),
            "2026-07-11T00:00:00+00:00",
        )
        hierarchy = next(item for item in preflight["checks"] if item["name"] == "measurement_hierarchy")
        self.assertFalse(hierarchy["passed"])
        self.assertFalse(preflight["upload_allowed"])

    def test_non_aspect_color_is_not_sent_for_luggage_scale_category(self):
        with tempfile.TemporaryDirectory() as directory:
            product_dir = Path(directory) / "P000004"
            shutil.copytree(self.product_dir, product_dir)
            (product_dir / "output/ozon-upload-config.json").unlink()
            package = build_package(product_dir, write=False)
            config_path = product_dir / "output/ozon-upload-config.json"
            self.assertFalse(config_path.exists())
            metadata = load_json(product_dir / "output/ozon-category-attributes.json")
            self.assertEqual(_auto_upload_config(product_dir, metadata)["sku_colors"], [])
            self.assertEqual(package["ozon-attributes-final.json"]["required_summary"]["missing"], 0)

    def test_drain_cover_tags_and_title_are_marketplace_safe(self):
        package = build_package(ROOT / "products/P000011", write=False)
        tags = package["ozon-tags.json"]["tags"]
        self.assertEqual(len(tags), 30)
        self.assertEqual(len(set(tags)), 30)
        self.assertTrue(all(tag.startswith("#") and len(tag) <= 30 for tag in tags))
        self.assertTrue(any("слив" in tag for tag in tags))
        attributes = package["ozon-attributes-final.json"]["attributes"]
        name = next(item for item in attributes if item["attribute_id"] == 4180)
        self.assertEqual(name["value"], "Крышка для слива светло-зелёная")
        material = next(item for item in attributes if item["attribute_id"] == 7346)
        color = next(item for item in attributes if item["attribute_id"] == 10096)
        color_name = next(item for item in attributes if item["attribute_id"] == 10097)
        self.assertEqual((material["value"], material["dictionary_value_id"]), ("Силикон", 971416954))
        self.assertEqual((color["value"], color["dictionary_value_id"]), ("светло-зеленый", 61589))
        self.assertEqual(color_name["value"], "светло-зеленый")
        self.assertEqual(material["source"], "1688")
        country = next(item for item in attributes if item["attribute_id"] == 4389)
        self.assertEqual((country["value"], country["dictionary_value_id"]), ("Китай", 90296))
        self.assertEqual(country["source"], "workspace_default")
        by_id = {item["attribute_id"]: item for item in attributes}
        self.assertEqual(by_id[4383]["value"], 150)
        self.assertEqual(by_id[4497]["value"], 180)
        self.assertEqual(by_id[9802]["value"], 200)
        self.assertEqual(by_id[6605]["value"], 200)
        self.assertEqual(by_id[6606]["value"], 20)
        self.assertTrue(all(by_id[item]["source"] == "AI_estimated" for item in (4383, 4497, 9802, 6605, 6606)))
        for attribute_id in (11650, 23249, 8962):
            quantity = next(item for item in attributes if item["attribute_id"] == attribute_id)
            self.assertEqual(quantity["value"], 1)
            self.assertEqual(quantity["source"], "workspace_default")
        coverage = package["attribute-coverage-report.json"]
        self.assertEqual(
            coverage["filled_attribute_count"] + coverage["omitted_unknown_count"],
            coverage["total_attribute_count"],
        )
        self.assertNotIn(4389, {
            item["attribute_id"] for item in coverage["omitted_attributes"]
        })

    def test_forbidden_facts_are_not_estimated(self):
        package = build_package(ROOT / "products/P000011", write=False)
        forbidden_terms = ("сертифик", "нагруз", "функц", "комплект", "материал")
        for item in package["ozon-attributes-final.json"]["attributes"]:
            if any(term in item["attribute_name"].casefold() for term in forbidden_terms):
                if item["source"] == "AI_estimated":
                    self.fail(f"Forbidden attribute was estimated: {item['attribute_name']}")

    def test_invalid_measurement_hierarchy_blocks_final_upload(self):
        with tempfile.TemporaryDirectory() as directory:
            product_dir = Path(directory) / "P000011"
            shutil.copytree(ROOT / "products/P000011", product_dir)
            cost_path = product_dir / "output/cost-analysis.json"
            cost = load_json(cost_path)
            cost["package_weight"]["value"] = cost["product_weight"]["value"]
            cost["weight"] = copy.deepcopy(cost["package_weight"])
            cost["measurement_hierarchy"]["valid"] = False
            cost_path.write_text(json.dumps(cost, ensure_ascii=False), encoding="utf-8")
            check = build_package(product_dir, write=False)["final-upload-check.json"]
            hierarchy = next(item for item in check["checks"] if item["name"] == "measurement_hierarchy")
            self.assertFalse(hierarchy["passed"])
            self.assertFalse(check["upload_allowed"])

    def test_category_remap_refreshes_stale_product_type_config(self):
        with tempfile.TemporaryDirectory() as directory:
            product_dir = Path(directory) / "P000011"
            shutil.copytree(ROOT / "products/P000011", product_dir)
            config_path = product_dir / "output/ozon-upload-config.json"
            config = load_json(config_path)
            config["type"] = {
                "attribute_id": 8229,
                "dictionary_value_id": 94635,
                "value": "Сифон сливной",
                "source": "stale_test_value",
            }
            config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
            build_package(product_dir, write=True)
            refreshed = load_json(config_path)
            self.assertEqual(refreshed["type"]["dictionary_value_id"], 92038)
            self.assertEqual(refreshed["type"]["value"], "Пробка для ванны")
            self.assertEqual(refreshed["sku_prices"], config["sku_prices"])

    def test_chinese_attribute_names_do_not_collapse_into_unrelated_page_text(self):
        with tempfile.TemporaryDirectory() as directory:
            product_dir = Path(directory) / "P000011"
            shutil.copytree(ROOT / "products/P000011", product_dir)
            source_path = product_dir / "input/source.json"
            source = load_json(source_path)
            source["product_attributes"].insert(0, {
                "name_cn": "原产地", "value_cn": "中国", "source": "structured_product_data",
            })
            source_path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
            package = build_package(product_dir, write=False)
            country = next(
                item for item in package["ozon-attributes-final.json"]["attributes"]
                if item["attribute_id"] == 4389
            )
            self.assertEqual(country["value"], "Китай")
            self.assertEqual(country["source"], "1688")
            self.assertEqual(country["evidence"], ["source.product_attributes.country"])


if __name__ == "__main__":
    unittest.main()
