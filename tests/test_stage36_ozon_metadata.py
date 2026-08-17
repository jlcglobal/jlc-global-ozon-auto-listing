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

if __name__ == "__main__":
    unittest.main()
