import unittest

from scripts.attribute_fill_input import (
    build_category_attribute_plan,
    build_compact_attribute_fill_input,
)


class AttributeFillInputCompactionTest(unittest.TestCase):
    def _base_value(self, attributes):
        return {
            "schema_version": "1.0.0",
            "product_id": "P123456",
            "collection_id": "C123456",
            "source_kind": "workbench_collection",
            "selected_skus": [
                {"sku_id": "SKU-1", "name": "черный держатель"},
            ],
            "sku_rows": [
                {"sku_id": "SKU-1", "color": {"canonical_value": "черный"}},
            ],
            "merged_facts": {
                "brand": {"canonical_value": "Нет бренда"},
                "material": {"canonical_value": "碳钢"},
                "origin_country": {"canonical_value": "中国"},
            },
            "measurements": {},
            "ozon_attributes": attributes,
            "dependencies": {"builder_version": "test"},
            "input_hash": "hash-123",
        }

    def test_huge_brand_dictionary_keeps_only_project_brand_values_without_changing_hash(self):
        brand_values = [
            {"value": "Нет бренда", "dictionary_value_id": 1},
            {"value": "JLC GLOBAL", "dictionary_value_id": 2},
            {"value": "зеленый", "dictionary_value_id": 3},
            {"value": "UNKNOWN", "dictionary_value_id": 4},
        ]
        brand_values.extend(
            {"value": f"BrandName{i}", "dictionary_value_id": i + 2}
            for i in range(1500)
        )
        value = self._base_value(
            [
                {
                    "attribute_id": 85,
                    "attribute_name": "Бренд",
                    "type": "String",
                    "required": True,
                    "is_aspect": False,
                    "allowed_values": brand_values,
                }
            ]
        )

        compact = build_compact_attribute_fill_input(value)

        self.assertEqual(compact["input_hash"], value["input_hash"])
        self.assertEqual(len(value["ozon_attributes"][0]["allowed_values"]), 1504)
        compact_attr = compact["ozon_attributes"][0]
        self.assertTrue(compact_attr["allowed_values_compacted"])
        self.assertEqual(
            compact_attr["allowed_values"],
            [
                {"value": "Нет бренда", "dictionary_value_id": 1},
                {"value": "JLC GLOBAL", "dictionary_value_id": 2},
            ],
        )
        self.assertEqual(compact["compact_input"]["full_allowed_values_count"], 1504)

    def test_small_dictionary_is_not_compacted(self):
        value = self._base_value(
            [
                {
                    "attribute_id": 10096,
                    "attribute_name": "Цвет товара",
                    "type": "String",
                    "required": False,
                    "is_aspect": True,
                    "allowed_values": [
                        {"value": "черный", "dictionary_value_id": 10},
                        {"value": "белый", "dictionary_value_id": 11},
                    ],
                }
            ]
        )

        compact = build_compact_attribute_fill_input(value)

        compact_attr = compact["ozon_attributes"][0]
        self.assertFalse(compact_attr["allowed_values_compacted"])
        self.assertEqual(compact_attr["allowed_values"], value["ozon_attributes"][0]["allowed_values"])
        self.assertEqual(compact["compact_input"]["compact_allowed_values_count"], 2)

    def test_category_attribute_plan_classifies_required_aspect_dictionary_and_numeric_fields(self):
        plan = build_category_attribute_plan([
            {
                "attribute_id": 85,
                "attribute_name": "Бренд",
                "type": "String",
                "required": True,
                "is_aspect": False,
                "allowed_values": [{"value": "Нет бренда", "dictionary_value_id": 1}],
            },
            {
                "attribute_id": 10097,
                "attribute_name": "Название цвета",
                "type": "String",
                "required": True,
                "is_aspect": True,
                "allowed_values": [],
            },
            {
                "attribute_id": 4497,
                "attribute_name": "Вес с упаковкой, г",
                "type": "Integer",
                "required": False,
                "is_aspect": True,
                "allowed_values": [],
            },
        ])

        by_id = {item["attribute_id"]: item for item in plan["items"]}
        self.assertEqual(plan["required_attribute_ids"], [85, 10097])
        self.assertEqual(plan["aspect_attribute_ids"], [10097, 4497])
        self.assertEqual(plan["dictionary_attribute_ids"], [85])
        self.assertEqual(plan["numeric_attribute_ids"], [4497])
        self.assertEqual(by_id[85]["fill_scope"], "common")
        self.assertEqual(by_id[85]["recommended_handling"], "match_current_ozon_dictionary_value")
        self.assertEqual(by_id[10097]["fill_scope"], "sku")
        self.assertEqual(by_id[10097]["physical_dimension"], "color")
        self.assertEqual(by_id[4497]["value_kind"], "integer")
        self.assertEqual(by_id[4497]["physical_dimension"], "weight")

    def test_compact_input_preserves_category_attribute_plan(self):
        value = self._base_value([{
            "attribute_id": 4497,
            "attribute_name": "Вес с упаковкой, г",
            "type": "Integer",
            "required": False,
            "is_aspect": True,
            "allowed_values": [],
        }])
        value["category_attribute_plan"] = build_category_attribute_plan(value["ozon_attributes"])

        compact = build_compact_attribute_fill_input(value)

        self.assertEqual(
            compact["category_attribute_plan"],
            value["category_attribute_plan"],
        )
        self.assertEqual(compact["category_attribute_plan"]["aspect_attribute_ids"], [4497])


if __name__ == "__main__":
    unittest.main()
