import copy
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ozon-adapter"))

from ozon_adapter import OzonConfig, OzonConfigurationError, OzonReadOnlyClient  # noqa: E402
from ozon_adapter.service import (  # noqa: E402
    SCHEMAS,
    _canonical_allowed_value,
    _value_variants,
    build_live_metadata_package,
    fetch_and_write_product_metadata,
    flatten_category_tree,
    load_json,
    rank_categories,
    validate_near_synonym_compatibility,
    validate_live_metadata_package,
)
from scripts.validate_product import validate_schema  # noqa: E402


PRODUCTS = {
    "P000004": (41001, 51001, "Весы для багажа"),
    "P000005": (41002, 51002, "Электрические точилки для ножей"),
    "P000003": (41003, 51003, "Искусственный газон"),
}


class FakeOzonTransport:
    def __init__(self):
        self.calls = []

    def __call__(self, endpoint, payload):
        self.calls.append((endpoint, copy.deepcopy(payload)))
        if endpoint == OzonReadOnlyClient.CATEGORY_TREE_ENDPOINT:
            return {
                "result": [{
                    "description_category_id": 40000,
                    "category_name": "Товары",
                    "children": [
                        {
                            "description_category_id": category_id,
                            "category_name": f"Раздел {category_id}",
                            "disabled": False,
                            "children": [{
                                "type_id": type_id,
                                "type_name": category_name,
                                "disabled": False,
                            }],
                        }
                        for category_id, type_id, category_name in PRODUCTS.values()
                    ],
                }]
            }
        if endpoint == OzonReadOnlyClient.CATEGORY_ATTRIBUTES_ENDPOINT:
            category_id = payload["description_category_id"]
            return {
                "result": [
                    {
                        "id": category_id + 1,
                        "name": "Тип",
                        "is_required": True,
                        "type": "String",
                        "dictionary_id": 70000,
                    },
                    {"id": category_id + 2, "name": "Назначение", "is_required": False, "type": "String"},
                    {
                        "id": category_id + 3,
                        "name": "Бренд",
                        "is_required": True,
                        "type": "Dictionary",
                        "dictionary_id": 70001,
                    },
                    {"id": category_id + 4, "name": "Материал", "is_required": True, "type": "String"},
                ]
            }
        if endpoint == OzonReadOnlyClient.ATTRIBUTE_VALUES_ENDPOINT:
            category_id = payload["description_category_id"]
            if payload["attribute_id"] == category_id + 1:
                category_name = next(
                    item[2] for item in PRODUCTS.values() if item[0] == category_id
                )
                return {"result": [{"id": 80000, "value": category_name}], "has_next": False}
            return {"result": [{"id": 80001, "value": "Нет бренда"}], "has_next": False}
        raise AssertionError(f"Unexpected endpoint: {endpoint}")


def client(transport):
    return OzonReadOnlyClient(
        OzonConfig(client_id="test-client", api_key="test-secret"),
        transport=transport,
    )


class Stage41OzonReadOnlyTest(unittest.TestCase):
    def test_camera_near_synonym_requires_compatible_live_attributes(self):
        offline = {"category_name": "Камера видеонаблюдения"}
        selected = {
            "category_name": "Комплект охранной системы для дома",
            "match_status": "api_match_needs_review",
            "confidence": 0.8,
            "rationale": "candidate",
            "warnings": [],
            "evidence": [],
        }
        attributes = {
            "attributes": [
                {"attribute_name": name}
                for name in ("Тип связи", "Питание от", "Степень защиты", "Особенности")
            ]
        }
        validate_near_synonym_compatibility("P000014", offline, selected, attributes)
        self.assertEqual(selected["match_status"], "api_confirmed")
        self.assertGreaterEqual(selected["confidence"], 0.9)

    def test_camera_near_synonym_is_rejected_when_attributes_do_not_fit(self):
        offline = {"category_name": "Камера видеонаблюдения"}
        selected = {
            "category_name": "Комплект охранной системы для дома",
            "match_status": "api_match_needs_review",
            "confidence": 0.8,
            "rationale": "candidate",
            "warnings": [],
            "evidence": [],
        }
        with self.assertRaisesRegex(ValueError, "failed attribute compatibility"):
            validate_near_synonym_compatibility(
                "P000014",
                offline,
                selected,
                {"attributes": [{"attribute_name": "Бренд"}]},
            )

    def test_storage_bag_maps_to_storage_coffer_not_fashion_bag(self):
        categories = [
            {
                "category_id": 17027904,
                "category_name": "Сумка",
                "type_id": 970575517,
                "type_name": "Сумка",
                "disabled": False,
                "is_leaf": True,
                "path": ["Одежда", "Сумка"],
            },
            {
                "category_id": 17027937,
                "category_name": "Хранение вещей",
                "type_id": 95483,
                "type_name": "Кофр для хранения вещей",
                "disabled": False,
                "is_leaf": True,
                "path": ["Дом и сад", "Хранение вещей", "Кофр для хранения вещей"],
            },
        ]
        ranked = rank_categories({
            "category_name": "Сумки и органайзеры для хранения",
            "alternatives": [{"category_name": "Кофр для хранения вещей"}],
        }, categories)
        self.assertEqual(ranked[0][0], 1.10)
        self.assertEqual(ranked[0][1]["type_id"], 95483)

    def test_pet_leash_plural_maps_to_real_ozon_leash_type(self):
        categories = [{
            "category_id": 17028668,
            "category_name": "Аксессуар для прогулки и дрессировки",
            "type_id": 95226,
            "type_name": "Поводок",
            "disabled": False,
            "is_leaf": True,
            "path": ["Товары для животных", "Аксессуар для прогулки и дрессировки", "Поводок"],
        }]
        ranked = rank_categories({
            "category_name": "Поводки для собак",
            "alternatives": [],
        }, categories)
        self.assertEqual(ranked[0][0], 1.10)
        self.assertEqual(ranked[0][1]["type_id"], 95226)

    def test_configuration_requires_both_environment_values(self):
        with self.assertRaisesRegex(OzonConfigurationError, "OZON_CLIENT_ID"):
            OzonConfig.from_env({})
        config = OzonConfig.from_env({"OZON_CLIENT_ID": "123", "OZON_API_KEY": "secret"})
        self.assertNotIn("secret", repr(config))
        self.assertEqual(config.headers()["Client-Id"], "123")

    def test_named_shop_uses_separate_environment_variables(self):
        registry_path = ROOT / "ozon-adapter/shops.json"
        registry = load_json(registry_path)
        self.assertEqual(registry["default_read_shop"], "default")
        shop = registry["shops"][0]
        self.assertNotIn("client_id", shop)
        self.assertNotIn("api_key", shop)
        config = OzonConfig.from_shop(
            "default",
            registry_path,
            {
                "OZON_DEFAULT_CLIENT_ID": "123",
                "OZON_DEFAULT_API_KEY": "secret",
            },
        )
        self.assertEqual(config.shop_name, "default")
        self.assertNotIn("secret", repr(config))

    def test_unknown_shop_is_rejected(self):
        with self.assertRaisesRegex(OzonConfigurationError, "Unknown Ozon shop"):
            OzonConfig.from_shop("missing-shop", ROOT / "ozon-adapter/shops.json", {})

    def test_client_has_only_three_readonly_endpoints(self):
        self.assertEqual(len(OzonReadOnlyClient.ALLOWED_ENDPOINTS), 3)
        transport = FakeOzonTransport()
        readonly = client(transport)
        with self.assertRaisesRegex(ValueError, "read-only allowlist"):
            readonly._post_json("/v3/product/import", {})
        self.assertEqual(transport.calls, [])

    def test_transient_readonly_connection_error_is_retried(self):
        calls = []

        def flaky_transport(endpoint, payload):
            calls.append((endpoint, payload))
            if len(calls) < 3:
                raise urllib.error.URLError("temporary SSL disconnect")
            return {"result": []}

        with patch("ozon_adapter.client.time.sleep") as sleep:
            response = client(flaky_transport).get_category_tree()

        self.assertEqual(response, {"result": []})
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleep.call_count, 2)

    def test_tree_is_flattened_with_parent_id(self):
        transport = FakeOzonTransport()
        response = transport(OzonReadOnlyClient.CATEGORY_TREE_ENDPOINT, {})
        categories = flatten_category_tree(response)
        parent = next(item for item in categories if item["category_id"] == 40000)
        child = next(item for item in categories if item["category_id"] == 41001 and item["type_id"] == 51001)
        self.assertIsNone(parent["parent_id"])
        self.assertEqual(child["parent_id"], 40000)
        self.assertEqual(child["path"], ["Товары", "Раздел 41001", "Весы для багажа"])

    def test_chinese_material_uses_synonym_before_normalization(self):
        self.assertIn("нержавеющая сталь", _value_variants("不锈钢"))
        self.assertEqual(
            _canonical_allowed_value(
                "不锈钢",
                [{"id": 1, "value": "Нержавеющая сталь"}],
            ),
            "Нержавеющая сталь",
        )

    def test_empty_dictionary_candidate_does_not_crash(self):
        self.assertEqual(
            _canonical_allowed_value(
                "来源：未提供",
                [{"id": 1, "value": "Пластик"}],
            ),
            "unknown",
        )

    def test_project_source_contains_no_ozon_write_endpoint(self):
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "ozon-adapter").rglob("*.py")
        )
        for forbidden in (
            "/v3/product/import",
            "/v1/product/import/pictures",
            "/v2/products/stocks",
            "/v4/product/info/stocks",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
