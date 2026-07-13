import copy
import unittest

from scripts.marketplace_content_generator import (
    ROOT,
    RULES_PATH,
    SCHEMAS,
    build_package,
    load_json,
    validate_content_input,
    validate_package,
)
from scripts.validate_product import validate_product, validate_schema


PRODUCT_IDS = ("P000004", "P000005", "P000003")


@unittest.skipUnless((ROOT / "products/P000004/input/source.json").is_file(), "optional runtime product fixture is not installed")
class Stage35MarketplaceContentTest(unittest.TestCase):
    def test_rules_and_all_new_schemas_parse(self):
        rules = load_json(RULES_PATH)
        self.assertFalse(rules["upload_gate"]["stage35_upload_allowed"])
        self.assertEqual(len(rules["attribute_fields"]), 10)
        for schema_path in SCHEMAS.values():
            self.assertIsInstance(load_json(schema_path), dict)

    def test_three_real_packages_match_all_schemas(self):
        for product_id in PRODUCT_IDS:
            product_dir = ROOT / "products" / product_id
            self.assertEqual(validate_package(product_dir), [], product_id)
            for filename, schema_path in SCHEMAS.items():
                self.assertEqual(
                    validate_schema(product_dir / "output" / filename, schema_path),
                    [],
                    f"{product_id}/{filename}",
                )

    def test_content_is_distinct_by_product_positioning(self):
        titles = {
            product_id: load_json(ROOT / "products" / product_id / "output/title-ru.json")["title_ru"]
            for product_id in PRODUCT_IDS
        }
        self.assertIn("Весы для багажа", titles["P000004"])
        self.assertIn("Электрическая точилка", titles["P000005"])
        self.assertIn("Искусственный газон", titles["P000003"])
        self.assertEqual(len(set(titles.values())), 3)

    def test_selected_skus_and_source_fields_are_preserved_exactly(self):
        for product_id in PRODUCT_IDS:
            product_dir = ROOT / "products" / product_id
            source = load_json(product_dir / "input/source.json")
            draft = load_json(product_dir / "output/ozon-draft.json")
            uploaded = load_json(product_dir / "status.json")["status"] == "UPLOADED"
            source_skus = {str(item["sku_id"]): item for item in source["skus"]}
            draft_skus = {str(item["source_sku_id"]): item for item in draft["skus"]}
            self.assertEqual(set(source_skus), set(draft_skus))
            for sku_id, source_sku in source_skus.items():
                draft_sku = draft_skus[sku_id]
                self.assertEqual(draft_sku["source_sku_name"], source_sku["sku_name"])
                self.assertEqual(draft_sku["option_values"], source_sku["option_values"])
                self.assertEqual(draft_sku["purchase_price_cny"], source_sku["purchase_price"])
                self.assertEqual(draft_sku["purchase_price_source"], source_sku["price_source"])
                self.assertEqual(draft_sku["source_image_url"], source_sku["image_url"])
                self.assertEqual(draft_sku["local_image_path"], source_sku["local_image_path"])
                self.assertEqual(draft_sku["sku_image_missing"], source_sku["sku_image_missing"])
                if not uploaded:
                    self.assertIsNotNone(draft_sku["sale_price_rub"])
                self.assertIsNone(draft_sku["stock"])

    def test_every_stage35_draft_is_blocked_from_upload(self):
        for product_id in PRODUCT_IDS:
            product_dir = ROOT / "products" / product_id
            draft = load_json(product_dir / "output/ozon-draft.json")
            uploaded = load_json(product_dir / "status.json")["status"] == "UPLOADED"
            self.assertFalse(draft["upload_allowed"])
            self.assertEqual(draft["preflight"]["status"], "failed")
            if draft["category"]["metadata_source"] == "ozon_seller_api":
                self.assertIsInstance(draft["description_category_id"], int)
                self.assertIsInstance(draft["type_id"], int)
            else:
                self.assertEqual(draft["description_category_id"], "unknown")
                self.assertEqual(draft["type_id"], "unknown")
            if not uploaded:
                self.assertIsNotNone(draft["price"]["price"])
            self.assertEqual(draft["price"]["currency_code"], "CNY")
            self.assertIsNone(draft["stock"]["quantity"])

    def test_unknown_attributes_remain_unknown(self):
        for product_id in PRODUCT_IDS:
            attributes = load_json(ROOT / "products" / product_id / "output/attributes.json")
            by_key = {item["field_key"]: item for item in attributes["attributes"]}
            for key in ("brand", "material", "dimensions", "weight", "load_capacity", "certifications", "package_quantity"):
                self.assertEqual(by_key[key]["value"], "unknown", f"{product_id}/{key}")
                self.assertEqual(by_key[key]["status"], "unknown", f"{product_id}/{key}")
                self.assertEqual(by_key[key]["ozon_attribute_id"], "unknown")

    def test_forbidden_unverified_claim_is_rejected(self):
        product_dir = ROOT / "products/P000005"
        content = copy.deepcopy(load_json(product_dir / "logs/marketplace-content-input.json"))
        content["title_ru"] += " Германия"
        with self.assertRaisesRegex(ValueError, "forbidden or unsupported claims"):
            validate_content_input(content, "P000005", load_json(RULES_PATH))

    def test_build_is_reproducible_and_calculates_sale_price_separately(self):
        product_dir = ROOT / "products/P000005"
        content = load_json(product_dir / "logs/marketplace-content-input.json")
        package = build_package(product_dir, content, checked_at="2026-07-11T00:00:00+08:00")
        draft = package["ozon-draft.json"]
        self.assertEqual([item["purchase_price_cny"] for item in draft["skus"]], [18, 20, 19])
        self.assertEqual([item["sale_price"] for item in draft["skus"]], ["163.0", "167.0", "165.0"])
        self.assertEqual([item["sale_price_rub"] for item in draft["skus"]], ["1956.0", "2004.0", "1980.0"])
        self.assertFalse(draft["upload_allowed"])

    def test_existing_full_product_and_stage1_example_still_validate(self):
        self.assertEqual(validate_product(ROOT / "products/P000004"), [])
        self.assertEqual(validate_product(ROOT / "products/P000001"), [])


if __name__ == "__main__":
    unittest.main()
