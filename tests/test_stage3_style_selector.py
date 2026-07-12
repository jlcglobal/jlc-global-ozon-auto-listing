import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.image_generator_contract import build_prompt_packet
from scripts.image_planner import build_image_plan
from scripts.style_selector import (
    ROOT,
    load_json,
    select_style_profile,
)
from scripts.validate_product import validate_product, validate_schema, validate_style_integrity


P3 = ROOT / "products" / "P000003"
P4 = ROOT / "products" / "P000004"


def kitchen_fixture():
    source = {
        "title_cn": "厨房台面多层调料置物架餐具收纳架",
        "product_attributes": [],
        "main_images": [],
        "detail_images": [],
        "skus": [],
    }
    analysis = {
        "product_type": "厨房收纳置物架",
        "category": "厨房用品 / 厨房收纳",
        "target_customer": ["希望保持厨房台面整洁的家庭用户"],
        "usage_scenarios": ["厨房台面", "橱柜内收纳"],
        "selling_points": [
            {"text": "用于整理调料和餐具，减少台面杂乱。", "evidence": ["fixture"]},
            {"text": "围绕整洁、收纳和节省空间的购买需求。", "evidence": ["fixture"]},
        ],
        "competitive_advantages": [],
        "facts": {
            "title_cn": "厨房台面多层调料置物架餐具收纳架",
            "category_cn": "厨房收纳",
            "dimensions": "unknown",
            "weight": "unknown",
            "load_capacity": "unknown",
        },
        "unknowns": [],
    }
    return source, analysis


class Stage3StyleSelectorTest(unittest.TestCase):
    def test_style_library_has_all_required_families_and_structures(self):
        profiles = load_json(ROOT / "rules/style_profiles.json")["profiles"]
        image_rules = load_json(ROOT / "rules/image_structure_rules.json")
        structures = image_rules["structures"]
        expected = {
            "electronics_clean_tech",
            "outdoor_rugged_lifestyle",
            "kitchen_warm_home",
            "home_minimal_organized",
            "pet_friendly_lifestyle",
            "beauty_clean_premium",
            "baby_safe_soft",
            "auto_practical_performance",
        }
        self.assertTrue(expected.issubset(profiles))
        self.assertTrue(expected.issubset(structures))
        for style_family in expected:
            self.assertEqual(structures[style_family], ["main"])
        policy = image_rules["selection_policy"]
        self.assertEqual(policy["mode"], "product_specific")
        self.assertEqual(policy["shared_detail_min"], 6)
        self.assertEqual(policy["shared_detail_max"], 8)

    def test_real_electronic_scale_selects_clean_tech(self):
        profile = select_style_profile(
            P4,
            load_json(P4 / "input/source.json"),
            load_json(P4 / "output/product-analysis.json"),
            generated_at="2026-07-10T00:00:00+08:00",
        )
        self.assertEqual(profile["classification_status"], "selected")
        self.assertEqual(profile["style_family"], "electronics_clean_tech")
        self.assertGreaterEqual(profile["confidence"], 0.9)
        self.assertEqual(
            profile["image_set_structure"],
            ["main", "benefit", "problem_solution", "scene", "feature", "detail", "usage"],
        )
        self.assertIn("anti_template_rule", profile["creative_direction"])

    def test_real_artificial_turf_selects_outdoor(self):
        profile = select_style_profile(
            P3,
            load_json(P3 / "input/source.json"),
            load_json(P3 / "output/product-analysis.json"),
            generated_at="2026-07-10T00:00:00+08:00",
        )
        self.assertEqual(profile["classification_status"], "selected")
        self.assertEqual(profile["style_family"], "outdoor_rugged_lifestyle")
        self.assertGreaterEqual(profile["confidence"], 0.9)
        self.assertIn("scene", profile["image_set_structure"])
        self.assertIn("problem_solution", profile["image_set_structure"])
        self.assertIn("comparison", profile["image_set_structure"])

    def test_kitchen_signals_select_warm_home_not_generic_home(self):
        source, analysis = kitchen_fixture()
        profile = select_style_profile(
            ROOT / "products/P999999",
            source,
            analysis,
            generated_at="2026-07-10T00:00:00+08:00",
        )
        self.assertEqual(profile["classification_status"], "selected")
        self.assertEqual(profile["style_family"], "kitchen_warm_home")
        self.assertEqual(
            profile["image_set_structure"],
            ["main", "benefit", "scene", "problem_solution", "detail", "size_spec", "comparison", "disclaimer"],
        )

    def test_locked_chinese_category_selects_style_without_runtime_guessing(self):
        source, analysis = kitchen_fixture()
        analysis["product_type"] = "unknown"
        analysis["category"] = "unknown"
        analysis["facts"]["category_cn"] = "unknown"
        source["title_cn"] = "普通家用容器"
        profile = select_style_profile(
            ROOT / "products/P999999",
            source,
            analysis,
            category_selection={
                "category_name_zh": "食品储藏罐",
                "category_path_zh": ["家居园艺", "食品储藏", "食品储藏罐"],
            },
            generated_at="2026-07-12T00:00:00+08:00",
        )
        self.assertEqual(profile["classification_status"], "selected")
        self.assertEqual(profile["style_family"], "kitchen_warm_home")

    def test_image_planner_uses_selected_structure_and_blocks_unknown_specs(self):
        source = load_json(P4 / "input/source.json")
        analysis = load_json(P4 / "output/product-analysis.json")
        profile = load_json(P4 / "output/style-profile.json")
        plan = build_image_plan(P4, source, analysis, profile, started_at="2026-07-10T00:00:00+08:00")
        self.assertEqual(plan["style_family"], profile["style_family"])
        self.assertEqual(plan["image_set_structure"], profile["image_set_structure"])
        self.assertTrue(plan["generator_contract"]["must_follow_style_profile"])
        size = next(item for item in plan["detail_images"] if item["image_type"] == "size_spec")
        self.assertEqual(size["status"], "planned")
        self.assertEqual(analysis["facts"]["dimensions"]["source"], "source.package_dimensions")

    def test_image_planner_uses_estimated_product_dimensions_with_approximate_label(self):
        source, analysis = kitchen_fixture()
        source["main_images"] = [{
            "id": "main-001",
            "download_status": "downloaded",
            "local_path": "products/P999999/input/main-images/main-001.jpg",
            "original_url": "https://example.test/main-001.jpg",
        }]
        with tempfile.TemporaryDirectory() as directory:
            product_dir = Path(directory) / "products/P999999"
            (product_dir / "output").mkdir(parents=True)
            (product_dir / "output/cost-analysis.json").write_text(json.dumps({
                "product_dimensions": {
                    "length": 20, "width": 12.5, "height": 3, "unit": "cm",
                    "source": "estimated", "source_ref": "fixture.profile",
                    "confidence": 65, "estimated": True,
                },
                "package_dimensions": {
                    "length": 22, "width": 14.5, "height": 5, "unit": "cm",
                    "source": "estimated", "source_ref": "fixture.package",
                    "confidence": 65, "estimated": True,
                },
            }), encoding="utf-8")
            profile = select_style_profile(
                product_dir,
                source,
                analysis,
                generated_at="2026-07-12T00:00:00+08:00",
            )
            plan = build_image_plan(
                product_dir, source, analysis, profile,
                started_at="2026-07-12T00:00:00+08:00",
            )
            (product_dir / "output/style-profile.json").write_text(
                json.dumps(profile), encoding="utf-8"
            )
            (product_dir / "output/image-plan.json").write_text(
                json.dumps(plan), encoding="utf-8"
            )
            size_slot = next(
                item["slot"] for item in plan["detail_images"]
                if item["image_type"] == "size_spec"
            )
            packet = build_prompt_packet(product_dir, size_slot)

        size = next(item for item in plan["detail_images"] if item["image_type"] == "size_spec")
        self.assertEqual(size["status"], "planned")
        self.assertEqual(
            size["russian_text"],
            ["Примерные размеры (Д × Ш × В): 20 × 12.5 × 3 см"],
        )
        self.assertTrue(size["measurement_annotation"]["estimated"])
        self.assertEqual(
            size["measurement_annotation"]["source_field"],
            "cost-analysis.product_dimensions",
        )
        self.assertNotIn("22", size["russian_text"][0])
        self.assertEqual(
            packet["image_intent"]["measurement_annotation"]["source_field"],
            "cost-analysis.product_dimensions",
        )
        self.assertIn("Примерные размеры", packet["generation_contract"]["exact_russian_text"][0])

    def test_image_planner_blocks_size_slot_when_measurement_output_is_missing(self):
        source, analysis = kitchen_fixture()
        source["main_images"] = [{
            "id": "main-001", "download_status": "downloaded",
            "local_path": "products/P999999/input/main-images/main-001.jpg",
            "original_url": "https://example.test/main-001.jpg",
        }]
        profile = select_style_profile(
            ROOT / "products/P999999", source, analysis,
            generated_at="2026-07-12T00:00:00+08:00",
        )
        plan = build_image_plan(
            ROOT / "products/P999999", source, analysis, profile,
            started_at="2026-07-12T00:00:00+08:00",
        )
        size = next(item for item in plan["detail_images"] if item["image_type"] == "size_spec")
        self.assertEqual(size["status"], "needs_review")
        self.assertIn("禁止使用包装尺寸", size["failure_reason"])

    def test_generator_packet_is_style_bound_and_blocks_unverified_durability(self):
        packet = build_prompt_packet(P3, "main-001")
        self.assertEqual(packet["style_family"], "outdoor_rugged_lifestyle")
        self.assertEqual(packet["aspect_ratio"], "3:4")
        self.assertIn("真实户外环境", packet["required_visual_signals"])
        with self.assertRaisesRegex(ValueError, "真实尺寸|product-body dimensions"):
            build_prompt_packet(P3, "detail-005")

    def test_style_schemas_and_integrity_pass(self):
        self.assertEqual(
            validate_schema(P3 / "output/style-profile.json", ROOT / "templates/style-profile.schema.json"),
            [],
        )
        self.assertEqual(
            validate_schema(P3 / "output/image-plan.json", ROOT / "templates/image-plan.schema.json"),
            [],
        )
        self.assertEqual(
            validate_schema(P3 / "output/qc-report.json", ROOT / "templates/qc-report.schema.json"),
            [],
        )
        self.assertEqual(validate_product(P4), [])

    def test_style_mismatch_is_rejected_by_full_validator(self):
        original = load_json(P4 / "output/image-plan.json")
        changed = copy.deepcopy(original)
        changed["style_family"] = "kitchen_warm_home"

        def fake_load(path):
            if Path(path).name == "image-plan.json":
                return changed
            return load_json(Path(path))

        with patch("scripts.validate_product.load_json", side_effect=fake_load):
            errors = validate_style_integrity(P4)
        self.assertTrue(any("style_family does not match" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
