import ast
import os
import unittest

from scripts.ozon_metadata_matcher import (
    ATTRIBUTES_SCHEMA_PATH,
    CATEGORY_SCHEMA_PATH,
    DRAFT_SCHEMA_PATH,
    OZON_RULES_PATH,
    PROFILES_PATH,
    ROOT,
    build_metadata_package,
    load_json,
    validate_metadata_package,
)
from scripts.validate_product import validate_product, validate_schema


EXPECTED = {
    "P000004": {
        "category": "Весы для багажа",
        "mapped": 6,
        "missing": 6,
        "unknown": {"material", "max_load", "certifications"},
    },
    "P000005": {
        "category": "Электрические точилки для ножей",
        "mapped": 4,
        "missing": 9,
        "unknown": {"material", "dimensions", "weight", "power", "voltage", "certifications"},
    },
    "P000003": {
        "category": "Искусственный газон",
        "mapped": 4,
        "missing": 6,
        "unknown": {"material", "roll_dimensions", "weight", "certifications"},
    },
}


@unittest.skipUnless(os.environ.get("CAF_RUN_LEGACY_FIXTURES") == "1", "legacy runtime fixture suite is isolated from active tests")
class Stage36OzonMetadataTest(unittest.TestCase):
    def test_profiles_rules_and_schemas_parse(self):
        profiles = load_json(PROFILES_PATH)
        self.assertEqual(profiles["metadata_mode"], "offline_semantic_profiles")
        self.assertLess(profiles["confidence_cap_without_live_ozon_metadata"], 1)
        self.assertTrue(profiles["near_synonym_policy"]["require_attribute_compatibility"])
        self.assertEqual(
            profiles["near_synonym_policy"]["on_failure"],
            "block_product_without_upload",
        )
        self.assertTrue(OZON_RULES_PATH.is_file())
        for path in (CATEGORY_SCHEMA_PATH, ATTRIBUTES_SCHEMA_PATH, DRAFT_SCHEMA_PATH):
            self.assertIsInstance(load_json(path), dict)

    def test_three_real_metadata_packages_match_schemas(self):
        for product_id in EXPECTED:
            product_dir = ROOT / "products" / product_id
            self.assertEqual(validate_metadata_package(product_dir), [], product_id)
            for filename, schema_path in (
                ("ozon-category.json", CATEGORY_SCHEMA_PATH),
                ("ozon-attributes.json", ATTRIBUTES_SCHEMA_PATH),
                ("ozon-draft.json", DRAFT_SCHEMA_PATH),
            ):
                self.assertEqual(
                    validate_schema(product_dir / "output" / filename, schema_path),
                    [],
                    f"{product_id}/{filename}",
                )

    def test_category_recommendations_and_offline_confidence_cap(self):
        cap = load_json(PROFILES_PATH)["confidence_cap_without_live_ozon_metadata"]
        for product_id, expected in EXPECTED.items():
            category = build_metadata_package(ROOT / "products" / product_id)["ozon-category.json"]
            self.assertEqual(category["category_name"], expected["category"])
            self.assertEqual(category["category_id"], "unknown")
            self.assertLessEqual(category["confidence"], cap)
            self.assertEqual(category["metadata_source"], "offline_semantic_profiles")

    def test_surveillance_camera_uses_its_own_profile(self):
        package = build_metadata_package(ROOT / "products" / "P000014")
        category = package["ozon-category.json"]
        attributes = package["ozon-attributes.json"]
        self.assertEqual(category["category_name"], "Камера видеонаблюдения")
        self.assertGreaterEqual(category["confidence"], 0.7)
        keys = {item["field_key"] for item in attributes["required_attributes"]}
        self.assertIn("video_resolution", keys)
        self.assertIn("connection_type", keys)
        self.assertNotIn("roll_dimensions", keys)
        self.assertNotIn("max_load", keys)
        compatible = load_json(PROFILES_PATH)["profiles"]["surveillance_cameras"]["compatible_live_types"]
        self.assertEqual(compatible[0]["name_ru"], "Комплект охранной системы для дома")

    def test_attribute_counts_and_sensitive_unknowns(self):
        for product_id, expected in EXPECTED.items():
            attributes = build_metadata_package(ROOT / "products" / product_id)["ozon-attributes.json"]
            summary = attributes["summary"]
            self.assertEqual(summary["mapped_count"], expected["mapped"])
            self.assertEqual(summary["missing_count"], expected["missing"])
            self.assertEqual(summary["unknown_count"], expected["missing"])
            self.assertTrue(expected["unknown"].issubset(set(attributes["unknown_attributes"])))
            self.assertTrue(all(
                item["value"] == "unknown" for item in attributes["missing_attributes"]
            ))
            self.assertTrue(all(
                item["ozon_attribute_id"] == "unknown"
                for item in attributes["required_attributes"]
            ))

    def test_drafts_are_updated_but_upload_remains_blocked(self):
        for product_id, expected in EXPECTED.items():
            draft = build_metadata_package(ROOT / "products" / product_id)["ozon-draft.json"]
            self.assertEqual(draft["category"]["category_name"], expected["category"])
            self.assertEqual(draft["description_category_id"], "unknown")
            self.assertEqual(draft["type_id"], "unknown")
            self.assertTrue(all(item["attribute_id"] == "unknown" for item in draft["attributes"]))
            self.assertFalse(draft["upload_allowed"])
            self.assertEqual(draft["preflight"]["status"], "failed")
            self.assertGreater(len(draft["attribute_warnings"]), 0)

    def test_rebuild_is_deterministic(self):
        for product_id, expected in EXPECTED.items():
            package = build_metadata_package(ROOT / "products" / product_id)
            self.assertEqual(package["ozon-category.json"]["category_name"], expected["category"])
            self.assertFalse(package["ozon-draft.json"]["upload_allowed"])

    def test_matcher_has_no_network_or_ai_client_imports(self):
        path = ROOT / "scripts" / "ozon_metadata_matcher.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertTrue(imported.isdisjoint({"openai", "requests", "httpx", "urllib", "aiohttp"}))

    def test_unified_product_validator_includes_stage36_outputs(self):
        for product_id in ("P000004", "P000003"):
            self.assertEqual(validate_product(ROOT / "products" / product_id), [], product_id)


if __name__ == "__main__":
    unittest.main()
