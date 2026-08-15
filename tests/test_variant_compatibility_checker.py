import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "variant-compatibility-checker"))

from variant_compatibility_checker import (  # noqa: E402
    build_grouping_result,
    build_variant_decision,
    build_platform_grouping_result,
    validate_grouping_result,
    validate_variant_decision,
)


def source_with_values(name, values):
    return {
        "skus": [
            {
                "sku_id": str(index),
                "sku_name": value,
                "option_values": [{"name_cn": name, "value_cn": value}],
            }
            for index, value in enumerate(values, start=1)
        ]
    }


class VariantCompatibilityCheckerTests(unittest.TestCase):
    def setUp(self):
        self.color_rule = {
            "categoryId": "1",
            "typeId": "2",
            "rule_data_complete": True,
            "source": "official_test_metadata",
            "attributes": [
                {"attributeId": "10096", "nameRu": "Цвет товара", "required": False, "isAspect": True, "values": []},
                {"attributeId": "10097", "nameRu": "Название цвета", "required": False, "isAspect": True, "values": []},
            ],
        }

    def decision(self, source, rule=None):
        return build_variant_decision(
            "P000001", source, 1, 2, rule or self.color_rule, "test-rules"
        )

    def test_color_difference_merges_when_color_is_allowed(self):
        result = self.decision(source_with_values("颜色", ["黑色", "白色"]))
        self.assertTrue(result["can_merge"])
        self.assertEqual(result["detected_difference_fields"][0]["difference_kind"], "color")
        self.assertEqual(validate_variant_decision(result), [])

    def test_color_plus_same_size_is_still_a_color_variant(self):
        result = self.decision(source_with_values(
            "规格1",
            ["绿色>M#2.0cm宽*120cm总长", "卡其色>M#2.0cm宽*120cm总长"],
        ))
        self.assertTrue(result["can_merge"])
        self.assertEqual(result["detected_difference_fields"][0]["difference_kind"], "color")

    def test_color_variant_payload_values_are_russian_only(self):
        source = source_with_values("颜色", ["卡其色1.9L", "黑色1.9L", "军绿色1.9L"])
        source.update({
            "source_url": "https://detail.1688.com/offer/123456.html",
            "captured_at": "2026-07-18T00:00:00Z",
        })
        decision = self.decision(source)
        grouping = build_grouping_result("P000009", source, decision)
        values = [
            attribute["value"]
            for variant in grouping["variants"]
            for attribute in variant["variant_attribute_values"]
            if attribute["attribute_id"] == 10097
        ]
        self.assertEqual(values, ["хаки", "черный", "зеленый"])
        self.assertTrue(all(not any(ch.isdigit() for ch in value) for value in values))

    def test_color_plus_different_phone_models_is_not_color_only(self):
        result = self.decision(source_with_values(
            "规格1",
            ["银色>iPhone17Pro", "银色>iPhone13", "黑色>iPhone14ProMax"],
        ))
        difference = result["detected_difference_fields"][0]
        self.assertEqual(difference["difference_kind"], "model_or_style")
        self.assertFalse(result["can_merge"])
        self.assertEqual(difference["mapped_variant_fields"], [])

    def test_color_only_category_keeps_group_but_requires_mapping(self):
        result = self.decision(source_with_values("规格", ["2个磨片", "4个磨片", "6个磨片"]))
        self.assertFalse(result["can_merge"])
        self.assertFalse(result["mapping_supported"])
        self.assertEqual(result["detected_difference_fields"][0]["mapped_variant_fields"], [])
        grouping = build_grouping_result(
            "P000001",
            {
                **source_with_values("规格", ["2个磨片", "4个磨片", "6个磨片"]),
                "source_url": "https://detail.1688.com/offer/123456.html?offerId=123456",
                "captured_at": "2026-07-10T00:00:00Z",
            },
            result,
        )
        self.assertTrue(grouping["must_merge"])
        self.assertTrue(grouping["internal_product_group"])
        self.assertFalse(grouping["platform_can_merge"])
        self.assertEqual(grouping["upload_strategy"], "separate_cards")
        self.assertEqual(grouping["product_group_count"], 1)
        self.assertEqual(grouping["variant_count"], 3)
        self.assertEqual(grouping["variant_mapping_status"], "SEPARATE_CARDS_REQUIRED")
        self.assertTrue(grouping["upload_allowed"])
        self.assertIsNone(grouping["mapping_requirements"]["missing_rule"])
        self.assertEqual(validate_grouping_result(grouping), [])

    def test_configuration_does_not_merge_when_attribute_is_not_an_aspect(self):
        rule = copy.deepcopy(self.color_rule)
        rule["attributes"].append({
            "attributeId": "4384", "nameRu": "Комплектация", "required": False,
            "isAspect": False, "values": []
        })
        result = self.decision(
            source_with_values("规格", ["2个磨片", "4个磨片", "6个磨片"]), rule
        )
        self.assertFalse(result["can_merge"])
        self.assertFalse(result["platform_can_merge"])
        self.assertTrue(result["internal_product_group"])
        self.assertFalse(result["mapping_supported"])
        self.assertEqual(result["confidence"], 100)
        self.assertEqual(result["difference_type"], "configuration")
        self.assertEqual(result["detected_difference_fields"][0]["mapped_variant_fields"], [])
        self.assertNotIn(
            10096,
            [field["attribute_id"] for field in result["detected_difference_fields"][0]["mapped_variant_fields"]],
        )

    def test_missing_is_aspect_never_defaults_to_merge_allowed(self):
        rule = {"categoryId": "1", "typeId": "2", "source": "legacy", "attributes": [
            {"attributeId": "10096", "nameRu": "Цвет товара", "required": False}
        ]}
        result = self.decision(source_with_values("颜色", ["黑色", "白色"]), rule)
        self.assertFalse(result["platform_can_merge"])
        self.assertTrue(result["variant_rule_data_incomplete"])
        self.assertEqual(result["allowed_variant_fields"], [])

    def test_unknown_difference_is_not_forced_into_a_variant(self):
        result = self.decision(source_with_values("规格", ["甲", "乙"]))
        self.assertFalse(result["can_merge"])
        self.assertFalse(result["mapping_supported"])
        self.assertEqual(result["detected_difference_fields"][0]["difference_kind"], "unknown")

    def test_per_sku_dimensions_do_not_become_duplicate_volume_aspects(self):
        source = {
            "skus": [
                {
                    "sku_id": "3l", "sku_name": "3L",
                    "option_values": [
                        {"name_cn": "容量", "value_cn": "3L"},
                        {"name_cn": "外部尺寸", "value_cn": "14.5×14.5×12cm"},
                    ],
                },
                {
                    "sku_id": "5l", "sku_name": "5L",
                    "option_values": [
                        {"name_cn": "容量", "value_cn": "5L"},
                        {"name_cn": "外部尺寸", "value_cn": "29×15.5×12cm"},
                    ],
                },
            ],
        }
        rule = {
            "categoryId": "1", "typeId": "2", "rule_data_complete": True,
            "source": "official_test_metadata",
            "attributes": [{
                "attributeId": "6788", "nameRu": "Объем, мл",
                "required": False, "isAspect": True, "values": [],
            }],
        }
        decision = self.decision(source, rule)
        grouping = build_grouping_result("P000001", source, decision)
        self.assertTrue(decision["platform_can_merge"])
        self.assertEqual(
            [item["source_field"] for item in decision["detected_difference_fields"]],
            ["容量"],
        )
        self.assertTrue(any("per-SKU specification" in item for item in decision["warnings"]))
        self.assertTrue(all(
            len(item["variant_attribute_values"]) == 1
            and item["variant_attribute_values"][0]["attribute_id"] == 6788
            for item in grouping["variants"]
        ))

    def test_compound_capacity_and_color_requires_both_aspects(self):
        source = source_with_values("规格1", [
            "500毫升（透明）",
            "500毫升（白色）",
            "300毫升（透明）",
            "200毫升（白色）",
            "300毫升（白色）",
            "200毫升（透明）",
        ])
        source.update({
            "source_url": "https://detail.1688.com/offer/123456.html",
            "captured_at": "2026-08-12T00:00:00Z",
        })
        rule = {
            "categoryId": "1",
            "typeId": "2",
            "rule_data_complete": True,
            "source": "official_test_metadata",
            "attributes": [
                {
                    "attributeId": "10096",
                    "nameRu": "Цвет товара",
                    "required": False,
                    "isAspect": True,
                    "values": [],
                },
                {
                    "attributeId": "6378",
                    "nameRu": "Объем, л",
                    "required": False,
                    "isAspect": True,
                    "values": [],
                },
            ],
        }

        decision = self.decision(source, rule)
        grouping = build_grouping_result("P000130", source, decision)

        self.assertTrue(decision["platform_can_merge"])
        self.assertEqual(
            [item["difference_kind"] for item in decision["detected_difference_fields"]],
            ["color", "size_or_measurement"],
        )
        self.assertEqual(
            [field["attribute_id"] for field in grouping["variant_attributes"]],
            [10096, 6378],
        )
        tuples = []
        for variant in grouping["variants"]:
            self.assertEqual(
                [item["attribute_id"] for item in variant["variant_attribute_values"]],
                [10096, 6378],
            )
            tuples.append(tuple(item["value"] for item in variant["variant_attribute_values"]))
        self.assertEqual(len(tuples), len(set(tuples)))

    def test_compound_capacity_and_color_separates_when_capacity_is_not_aspect(self):
        source = source_with_values("规格1", [
            "500毫升（透明）",
            "500毫升（白色）",
            "300毫升（透明）",
            "200毫升（白色）",
            "300毫升（白色）",
            "200毫升（透明）",
        ])
        source.update({
            "source_url": "https://detail.1688.com/offer/123456.html",
            "captured_at": "2026-08-12T00:00:00Z",
        })

        decision = self.decision(source, self.color_rule)
        grouping = build_grouping_result("P000130", source, decision)

        self.assertFalse(decision["platform_can_merge"])
        self.assertEqual(
            [item["difference_kind"] for item in decision["detected_difference_fields"]],
            ["color", "size_or_measurement"],
        )
        self.assertEqual(grouping["variant_mapping_status"], "SEPARATE_CARDS_REQUIRED")
        self.assertEqual(grouping["upload_strategy"], "separate_cards")
        self.assertTrue(grouping["upload_allowed"])

    def test_single_sku_is_one_non_variant_product_group(self):
        result = self.decision(source_with_values("颜色", ["黑色"]))
        self.assertFalse(result["can_merge"])
        grouping = build_grouping_result(
            "P000001",
            {
                **source_with_values("颜色", ["黑色"]),
                "source_url": "https://detail.1688.com/offer/123456.html",
                "captured_at": "2026-07-10T00:00:00Z",
            },
            result,
        )
        self.assertEqual(grouping["variant_mapping_status"], "NOT_REQUIRED")
        self.assertFalse(grouping["must_merge"])
        self.assertEqual(grouping["variant_count"], 1)

    def test_configuration_skus_remain_one_internal_group_but_use_separate_ozon_cards(self):
        result = self.decision(source_with_values("规格", ["2个磨片", "4个磨片", "6个磨片"]))
        platform = build_platform_grouping_result(result)
        self.assertEqual(platform["internal_group_count"], 1)
        self.assertEqual(platform["platform_card_count"], 3)
        self.assertFalse(platform["platform_can_merge"])
        self.assertEqual(platform["upload_strategy"], "separate_cards")

    def test_explicit_liters_win_over_weight_and_load_wording(self):
        source = source_with_values("规格1", [
            "10升特厚（390克）实装≥10斤油(1个装）",
            "25升特厚自重2.8斤实装≥50斤油（1个装）",
            "5升特厚（220克）实装≥10斤油（1个装）",
            "2.5升特厚实装5.5斤水（1个装）",
        ])
        source.update({
            "source_url": "https://detail.1688.com/offer/123456.html",
            "captured_at": "2026-07-16T00:00:00Z",
        })
        rule = {
            "categoryId": "1",
            "typeId": "2",
            "rule_data_complete": True,
            "source": "official_test_metadata",
            "attributes": [{
                "attributeId": "6378",
                "nameRu": "Объем, л",
                "required": False,
                "isAspect": True,
                "values": [],
            }],
        }
        decision = self.decision(source, rule)
        grouping = build_grouping_result("P000006", source, decision)
        values = [
            variant["variant_attribute_values"][0]
            for variant in grouping["variants"]
        ]
        self.assertEqual([item["value"] for item in values], ["10", "25", "5", "2.5"])
        self.assertTrue(all(item["estimated"] is False for item in values))
        self.assertTrue(all(item["dictionary_value_id"] is None for item in values))

if __name__ == "__main__":
    unittest.main()
