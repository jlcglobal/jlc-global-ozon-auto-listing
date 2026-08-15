import ast
import json
import os
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pricing-engine"))

from pricing_engine.pricing_calculator import calculate_base_price  # noqa: E402
from pricing_engine.dimension_estimator import estimate_package_dimensions, estimate_product_dimensions  # noqa: E402
from pricing_engine.service import build_pricing_package, _price_sku, _purchase_cost  # noqa: E402
from pricing_engine.source_measurements import source_sku_measurements  # noqa: E402
from pricing_engine.weight_estimator import (  # noqa: E402
    estimate_package_weight,
    estimate_sku_weights_from_dimensions,
    estimate_weight,
)
from pricing_engine.xlsx_rets import load_rets_rules  # noqa: E402


class PricingEngineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = json.loads((ROOT / "pricing-engine/pricing_rules.json").read_text(encoding="utf-8"))

    def test_case_one_base_price_is_45(self):
        self.assertEqual(calculate_base_price(10, 20, 0, 0.5), 45.0)

    def test_abnormal_water_cup_weight_is_corrected_and_original_is_preserved(self):
        source = {"title_cn": "家用水杯", "skus": []}
        result = estimate_weight(
            source,
            {"product_type": "水杯", "category": "厨房用品", "facts": {"weight": {"value_g": 20000}}},
            self.rules["measurement_profiles"],
        )
        self.assertEqual(result["source"], "estimated")
        self.assertTrue(result["estimated"])
        self.assertEqual(result["validation"]["status"], "corrected")
        self.assertEqual(result["validation"]["original_value"], 20000)
        self.assertEqual(result["value"], 500)

    def test_missing_weight_uses_estimated_source(self):
        source = {"title_cn": "家用水杯", "skus": []}
        result = estimate_weight(source, {"product_type": "水杯", "category": "厨房用品"}, self.rules["measurement_profiles"])
        self.assertEqual(result["source"], "estimated")
        self.assertTrue(result["estimated"])
        self.assertEqual(result["confidence"], 70)

    def test_drain_cover_does_not_use_one_kilogram_generic_default(self):
        source = {"title_cn": "地漏防臭器硅胶垫排水口密封盖", "skus": []}
        result = estimate_weight(
            source,
            {"product_type": "排水口覆盖垫", "category": "卫浴用品"},
            self.rules["measurement_profiles"],
        )
        self.assertEqual(result["profile"], "drain_cover")
        self.assertEqual(result["value"], 150)
        self.assertTrue(result["estimated"])

    def test_5g_wifi_is_not_parsed_as_five_grams(self):
        source = {
            "title_cn": "双镜头户外监控摄像机",
            "skus": [{
                "sku_name": "5G-WiFi A款",
                "option_values": [{"value_cn": "5G-WiFi A款-欧规电源"}],
            }],
        }
        result = estimate_weight(
            source,
            {"product_type": "双镜头户外网络监控摄像机", "category": "安防监控"},
            self.rules["measurement_profiles"],
        )
        self.assertEqual(result["profile"], "surveillance_camera")
        self.assertEqual(result["value"], 1200)
        self.assertEqual(result["source"], "estimated")
        self.assertTrue(result["estimated"])

    def test_memory_capacity_is_not_parsed_as_weight(self):
        source = {
            "title_cn": "双镜头户外监控摄像机",
            "skus": [{
                "sku_name": "含128GB内存卡",
                "option_values": [{"value_cn": "UPS电源（含128GB内存卡）"}],
            }],
        }
        result = estimate_weight(
            source,
            {"product_type": "双镜头户外网络监控摄像机", "category": "安防监控"},
            self.rules["measurement_profiles"],
        )
        self.assertEqual(result["value"], 1200)
        self.assertEqual(result["source"], "estimated")
        self.assertEqual(result["validation"]["original_value"], 1200)

    def test_real_sku_weight_still_parses(self):
        source = {
            "title_cn": "双镜头户外监控摄像机",
            "skus": [{"sku_name": "含电源 1.5kg", "option_values": []}],
        }
        result = estimate_weight(
            source,
            {"product_type": "双镜头户外网络监控摄像机", "category": "安防监控"},
            self.rules["measurement_profiles"],
        )
        self.assertEqual(result["value"], 1500)
        self.assertEqual(result["source"], "sku_specification")

    def test_fractional_ai_weight_estimates_become_integer_grams(self):
        product = estimate_weight(
            {"title_cn": "便携式榨汁机", "skus": []},
            {"product_type": "便携式榨汁机", "category": "厨房小电", "facts": {"weight": {"value_g": 846.4}}},
            self.rules["measurement_profiles"],
        )
        self.assertEqual(product["value"], 847)
        self.assertIsInstance(product["value"], int)
        package = estimate_package_weight(
            {"package_weight": {"value_g": 901.2}},
            product,
            self.rules["package_estimation"],
        )
        self.assertEqual(package["value"], 1147)

    def test_package_weight_is_product_weight_plus_fixed_allowance(self):
        product = {
            "value": 1150,
            "unit": "g",
            "source": "1688",
            "source_ref": "source.product_attributes.sku_measurement_table.weight",
            "confidence": 100,
            "estimated": False,
            "profile": "default",
        }
        package = estimate_package_weight(
            {"package_weight": {"value_g": 2050}},
            product,
            self.rules["package_estimation"],
        )
        self.assertEqual(package["value"], 1450)
        self.assertEqual(package["validation"]["original_value"], 2050)
        self.assertEqual(package["source_ref"], "pricing_rules.package_estimation")

    def test_shared_cny_price_range_ignores_stock_and_footnotes(self):
        source = {
            "price_information": {
                "currency": "CNY",
                "price_ranges": [
                    {"min_quantity": None, "price_cny": 26, "raw_text": "价格¥26.00"},
                    {"min_quantity": None, "price_cny": 99500, "raw_text": "库存99500个"},
                    {"min_quantity": None, "price_cny": 1, "raw_text": "价格比较说明：（1）活动前价格"},
                ],
                "raw_text": "价格¥26.00 | 库存99500个",
            }
        }
        result = _purchase_cost(
            {"sku_id": "sku-1", "sku_name": "海湾橡树色1.18L", "purchase_price": None},
            source,
            {},
        )
        self.assertEqual(result["value_cny"], 26.0)
        self.assertEqual(result["source"], "price_range_conservative")
        self.assertEqual(result["source_ref"], "source.price_information.price_ranges.valid_cny.max")

    def test_low_price_item_uses_route_value_floor_instead_of_stopping(self):
        workbook = load_rets_rules(ROOT / "pricing-engine/shipping_rules.xlsx")
        pricing, profit = _price_sku(
            {
                "sku_id": "sku-1",
                "sku_name": "保温杯 1.18L",
                "purchase_cost": {
                    "value_cny": 26.0,
                    "source": "price_range_conservative",
                    "source_ref": "source.price_information.price_ranges.valid_cny.max",
                    "confidence": 70,
                },
            },
            {
                "package_weight": {"value": 575},
                "weight": {"value": 575},
                "package_dimensions": {
                    "length": 13.0,
                    "width": 13.0,
                    "height": 26.25,
                    "unit": "cm",
                },
                "dimensions": {
                    "length": 13.0,
                    "width": 13.0,
                    "height": 26.25,
                    "unit": "cm",
                },
            },
            {},
            {"category": "unknown"},
            self.rules,
            workbook,
            {"value": 0.18, "source": "default_unknown_category"},
        )
        self.assertNotIn("no_eligible_rets_route", pricing["errors"])
        self.assertEqual(pricing["shipping"]["route_name"], "RETS Small Economy")
        self.assertGreater(pricing["selling_price_rub"], 1500)
        self.assertLessEqual(pricing["selling_price_rub"] - 1500, 100)
        self.assertIsNotNone(profit["selling_price_cny"])

    def test_large_route_value_gap_does_not_force_premium_floor(self):
        workbook = load_rets_rules(ROOT / "pricing-engine/shipping_rules.xlsx")
        pricing, _profit = _price_sku(
            {
                "sku_id": "p000029-like",
                "sku_name": "香料架样本",
                "purchase_cost": {
                    "value_cny": 33.5,
                    "source": "test_sample",
                    "source_ref": "test",
                    "confidence": 100,
                },
            },
            {
                "package_weight": {"value": 2730},
                "weight": {"value": 2730},
                "package_dimensions": {
                    "length": 28.88,
                    "width": 28.35,
                    "height": 14.2,
                    "unit": "cm",
                },
                "dimensions": {
                    "length": 28.88,
                    "width": 28.35,
                    "height": 14.2,
                    "unit": "cm",
                },
            },
            {},
            {"category": "unknown"},
            self.rules,
            workbook,
            {"value": 0.18, "source": "default_unknown_category"},
        )

        self.assertNotEqual(pricing["shipping"]["route_name"], "RETS Premium Small Economy")
        self.assertEqual(pricing["shipping"]["route_name"], "RETS Big Standard")
        self.assertLess(pricing["selling_price_rub"], 7000)
        self.assertNotEqual(pricing["selling_price_rub"], 7008)

    def test_human_approved_per_sku_dimensions_use_largest_variant_for_shipping(self):
        result = estimate_product_dimensions(
            {"title_cn": "冰箱收纳盒", "skus": [{}, {}, {}]},
            {
                "product_type": "冰箱收纳盒",
                "facts": {
                    "dimensions": {
                        "provenance": "estimated_human_approved",
                        "by_sku_cm": {
                            "3l": {"length": 14.5, "width": 14.5, "height": 12},
                            "5l": {"length": 29, "width": 15.5, "height": 12},
                            "6l": {"length": 29, "width": 15.5, "height": 15.5},
                        },
                    },
                },
            },
            self.rules["measurement_profiles"],
        )
        self.assertEqual(
            (result["length"], result["width"], result["height"]),
            (29.0, 15.5, 15.5),
        )
        self.assertEqual(result["profile"], "manual_confirmation")
        self.assertEqual(result["confidence"], 95)

    def test_1688_dimension_attribute_with_unit_per_value_is_used(self):
        result = estimate_product_dimensions(
            {
                "title_cn": "折叠冰包",
                "skus": [],
                "product_attributes": [{
                    "name_cn": "规格(长*宽*高)",
                    "value_cn": "32cm*31cm*58cm",
                    "source": "dom_product_attribute_table",
                }],
            },
            {"product_type": "折叠冰包", "facts": {}},
            self.rules["measurement_profiles"],
        )
        self.assertEqual(
            (result["length"], result["width"], result["height"]),
            (32.0, 31.0, 58.0),
        )
        self.assertEqual(result["source"], "1688")
        self.assertFalse(result["estimated"])

    def test_1688_sku_measurement_table_supplies_dimensions_and_weight_before_ai_estimates(self):
        source = {
            "product_id": "P000111",
            "title_cn": "方形油桶50L60L70L80L",
            "skus": [
                {"sku_id": "s50", "sku_name": "加厚型50L不锈钢油桶", "option_values": []},
                {"sku_id": "s80", "sku_name": "加厚型80L不锈钢油桶", "option_values": []},
            ],
            "product_attributes": [
                {
                    "name_cn": "SKU尺寸-加厚型50L不锈钢油桶",
                    "value_cn": "47.50cm × 30cm × 47cm",
                    "source_text": "加厚型50L不锈钢油桶47.503047669755500",
                },
                {
                    "name_cn": "SKU尺寸-加厚型80L不锈钢油桶",
                    "value_cn": "49.50cm × 35cm × 56cm",
                    "source_text": "加厚型80L不锈钢油桶49.503556970207400",
                },
            ],
        }
        measurements = source_sku_measurements(source)
        self.assertEqual(measurements["s50"]["weight_g"], 5500)
        self.assertEqual(measurements["s80"]["weight_g"], 7400)

        dimensions = estimate_product_dimensions(
            source,
            {"product_type": "油桶", "category": "户外用品", "facts": {"weight": {"value_g": 1150}}},
            self.rules["measurement_profiles"],
        )
        weight = estimate_weight(
            source,
            {"product_type": "油桶", "category": "户外用品", "facts": {"weight": {"value_g": 1150}}},
            self.rules["measurement_profiles"],
        )

        self.assertEqual((dimensions["length"], dimensions["width"], dimensions["height"]), (49.5, 35.0, 56.0))
        self.assertEqual(dimensions["source"], "1688")
        self.assertFalse(dimensions["estimated"])
        self.assertEqual(weight["value"], 7400)
        self.assertEqual(weight["source"], "1688")
        self.assertFalse(weight["estimated"])

        package = estimate_package_weight({}, weight, self.rules["package_estimation"])
        self.assertEqual(package["value"], 7700)

    def test_unitless_sku_dimension_labels_are_centimetre_estimates(self):
        source = {
            "product_id": "P-storage",
            "title_cn": "透明收纳盒",
            "product_attributes": [],
            "skus": [
                {"sku_id": "small", "sku_name": "【27*22*18】无隔板", "option_values": []},
                {"sku_id": "large", "sku_name": "【45×33×33】1个隔板", "option_values": []},
                {"sku_id": "not-dimensions", "sku_name": "型号2026*8*3", "option_values": []},
            ],
        }

        measurements = source_sku_measurements(source)

        self.assertEqual(
            (measurements["small"]["length"], measurements["small"]["width"], measurements["small"]["height"]),
            (27.0, 22.0, 18.0),
        )
        self.assertTrue(measurements["small"]["estimated"])
        self.assertEqual(measurements["small"]["confidence"], 82)
        self.assertNotIn("not-dimensions", measurements)

    def test_relative_sku_weight_estimate_uses_size_and_divider(self):
        estimates = estimate_sku_weights_from_dimensions([
            {
                "sku_id": "small",
                "label": "27*22*18 无隔板",
                "dimensions_mm": {"length_mm": 270, "width_mm": 220, "height_mm": 180},
            },
            {
                "sku_id": "large-no-divider",
                "label": "45*33*33 无隔板",
                "dimensions_mm": {"length_mm": 450, "width_mm": 330, "height_mm": 330},
            },
            {
                "sku_id": "large-divider",
                "label": "45*33*33 1个隔板",
                "dimensions_mm": {"length_mm": 450, "width_mm": 330, "height_mm": 330},
            },
        ], 1000)

        self.assertLess(estimates["small"], estimates["large-no-divider"])
        self.assertLess(estimates["large-no-divider"], estimates["large-divider"])
        self.assertEqual(estimates["large-divider"], 1000)

    def test_luggage_sku_nominal_size_prevents_generic_small_item_estimate(self):
        source = {
            "product_id": "P000076-like",
            "title_cn": "新款折叠拉杆箱20寸24寸行李箱出差旅游商务轻便可折叠旅行箱",
            "skus": [
                {
                    "sku_id": "deep-blue-24",
                    "sku_name": "深蓝色>24寸",
                    "option_values": [
                        {"name_cn": "颜色", "value_cn": "深蓝色"},
                        {"name_cn": "尺寸", "value_cn": "24寸"},
                    ],
                },
                {
                    "sku_id": "pink-24",
                    "sku_name": "浅粉色>24寸",
                    "option_values": [
                        {"name_cn": "颜色", "value_cn": "浅粉色"},
                        {"name_cn": "尺寸", "value_cn": "24寸"},
                    ],
                },
            ],
            "product_attributes": [],
        }

        measurements = source_sku_measurements(source)
        self.assertEqual(
            (measurements["deep-blue-24"]["length"], measurements["deep-blue-24"]["width"], measurements["deep-blue-24"]["height"]),
            (46.0, 11.0, 66.0),
        )
        self.assertEqual(measurements["deep-blue-24"]["weight_g"], 3300)
        self.assertTrue(measurements["deep-blue-24"]["estimated"])

        dimensions = estimate_product_dimensions(
            source,
            {"product_type": "折叠拉杆箱", "category": "箱包", "facts": {}},
            self.rules["measurement_profiles"],
        )
        weight = estimate_weight(
            source,
            {"product_type": "折叠拉杆箱", "category": "箱包", "facts": {}},
            self.rules["measurement_profiles"],
        )
        package_dimensions = estimate_package_dimensions(source, dimensions, self.rules["package_estimation"])
        package_weight = estimate_package_weight(source, weight, self.rules["package_estimation"])

        self.assertEqual((dimensions["length"], dimensions["width"], dimensions["height"]), (46.0, 11.0, 66.0))
        self.assertEqual(dimensions["source_ref"], "source.skus.nominal_luggage_size_estimate")
        self.assertTrue(dimensions["estimated"])
        self.assertEqual(weight["value"], 3300)
        self.assertEqual(weight["source_ref"], "source.skus.nominal_luggage_size_estimate.weight")
        self.assertTrue(weight["estimated"])
        self.assertGreater(package_dimensions["length"], dimensions["length"])
        self.assertGreater(package_dimensions["width"], dimensions["width"])
        self.assertGreater(package_dimensions["height"], dimensions["height"])
        self.assertGreater(package_weight["value"], weight["value"])

    def test_bad_luggage_measurement_conflicting_with_size_token_is_not_trusted(self):
        source = {
            "title_cn": "24寸拉杆箱",
            "skus": [{"sku_id": "sku-24", "sku_name": "黑色 24寸", "option_values": []}],
            "product_attributes": [{
                "name_cn": "SKU尺寸-黑色 24寸",
                "value_cn": "30cm × 20cm × 15cm",
                "source_text": "黑色 24寸 30 20 15 1150",
            }],
        }

        measurements = source_sku_measurements(source)

        self.assertEqual(
            (measurements["sku-24"]["length"], measurements["sku-24"]["width"], measurements["sku-24"]["height"]),
            (46.0, 26.0, 66.0),
        )
        self.assertTrue(measurements["sku-24"]["estimated"])

    def test_only_rets_sheet_supplies_shipping_rates(self):
        rules = load_rets_rules(ROOT / "pricing-engine/shipping_rules.xlsx")
        self.assertEqual(rules["worksheet"], "RETS")
        self.assertEqual(rules["exchange_rate_rub_per_cny"], 12)
        self.assertEqual(len(rules["route_costs"]), 6)
        self.assertEqual(rules["route_costs"]["RETS Extra Small Economy"], {
            "base_fee_cny": 3.12,
            "rate_per_kg_cny": 26.0,
        })

if __name__ == "__main__":
    unittest.main()
