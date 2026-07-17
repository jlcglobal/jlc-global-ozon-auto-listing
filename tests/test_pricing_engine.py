import ast
import json
import os
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pricing-engine"))

from pricing_engine.pricing_calculator import calculate_base_price  # noqa: E402
from pricing_engine.dimension_estimator import estimate_product_dimensions  # noqa: E402
from pricing_engine.service import build_pricing_package  # noqa: E402
from pricing_engine.weight_estimator import estimate_weight  # noqa: E402
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

    @unittest.skipUnless((ROOT / "products/P000011/input/source.json").is_file(), "optional runtime product fixture is not installed")
    def test_product_and_package_estimates_are_separate_and_strictly_ordered(self):
        package = build_pricing_package(
            ROOT / "products/P000011", "2026-07-11T00:00:00+00:00"
        )
        cost = package["cost-analysis.json"]
        self.assertTrue(cost["measurement_hierarchy"]["valid"])
        self.assertGreater(cost["package_weight"]["value"], cost["product_weight"]["value"])
        for key in ("length", "width", "height"):
            self.assertGreater(cost["package_dimensions"][key], cost["product_dimensions"][key])
        self.assertEqual(cost["weight"], cost["package_weight"])
        self.assertEqual(cost["dimensions"], cost["package_dimensions"])

    @unittest.skipUnless((ROOT / "products/P000011/input/source.json").is_file(), "optional runtime product fixture is not installed")
    def test_shipping_uses_package_measurements(self):
        package = build_pricing_package(
            ROOT / "products/P000011", "2026-07-11T00:00:00+00:00"
        )
        cost = package["cost-analysis.json"]
        shipping_weight = package["pricing-result.json"]["sku_pricing"][0]["shipping"]["weight"]
        self.assertEqual(shipping_weight["actual_weight_g"], cost["package_weight"]["value"])
        self.assertNotEqual(shipping_weight["actual_weight_g"], cost["product_weight"]["value"])

    def test_only_rets_sheet_supplies_shipping_rates(self):
        rules = load_rets_rules(ROOT / "pricing-engine/shipping_rules.xlsx")
        self.assertEqual(rules["worksheet"], "RETS")
        self.assertEqual(rules["exchange_rate_rub_per_cny"], 12)
        self.assertEqual(len(rules["route_costs"]), 6)
        self.assertEqual(rules["route_costs"]["RETS Extra Small Economy"], {
            "base_fee_cny": 3.12,
            "rate_per_kg_cny": 26.0,
        })

    @unittest.skipUnless((ROOT / "products/P000004/input/source.json").is_file(), "optional runtime product fixture is not installed")
    @unittest.skipUnless(os.environ.get("CAF_RUN_LEGACY_FIXTURES") == "1", "legacy runtime fixture suite is isolated from active tests")
    def test_real_product_pricing_uses_required_fees_and_no_api(self):
        package = build_pricing_package(
            ROOT / "products/P000004", "2026-07-11T00:00:00+00:00"
        )
        result = package["pricing-result.json"]
        self.assertEqual(result["shipping_rules"]["worksheet"], "RETS")
        self.assertEqual(result["commission"], {
            "value": 0.18,
            "source": "default_unknown_category",
        })
        first = result["sku_pricing"][0]
        self.assertEqual(first["logistics_commission_rate"], 0.02)
        self.assertEqual(first["acquiring_fee_rate"], 0.02)
        self.assertEqual(first["withdrawal_fee_rate"], 0.012)
        self.assertEqual(first["packing_fee_cny"], 2.0)
        self.assertEqual(first["shipping"]["route_name"], "RETS Extra Small Economy")
        self.assertEqual(first["selling_price_cny"], 58.0)

        forbidden = {"openai", "requests", "httpx", "aiohttp"}
        imports = set()
        for path in (ROOT / "pricing-engine/pricing_engine").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.add(node.module.split(".")[0])
        self.assertTrue(imports.isdisjoint(forbidden))


if __name__ == "__main__":
    unittest.main()
