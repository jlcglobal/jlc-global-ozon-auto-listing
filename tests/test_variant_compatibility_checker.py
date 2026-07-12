import copy
import json
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
        self.assertFalse(grouping["upload_allowed"])
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

    def test_p000005_has_fixed_separate_card_policy_in_status(self):
        status = json.loads((ROOT / "products/P000005/status.json").read_text(encoding="utf-8"))
        grouping = status["platform_grouping"]
        self.assertEqual(grouping["internal_product_group_id"], "P000005")
        self.assertEqual(grouping["source_sku_count"], 3)
        self.assertEqual(grouping["ozon_card_count"], 3)
        self.assertFalse(grouping["platform_can_merge"])
        self.assertEqual(grouping["upload_strategy"], "separate_cards")


if __name__ == "__main__":
    unittest.main()
