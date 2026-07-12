import copy
import tempfile
import unittest
from pathlib import Path

from scripts.image_planner import build_image_plan
from scripts.image_qc import inspect_images, load_json
from scripts.style_selector import ROOT


class LockedProductImageTest(unittest.TestCase):
    def setUp(self):
        self.product_dir = ROOT / "products/P000014"
        self.plan = build_image_plan(
            self.product_dir,
            load_json(self.product_dir / "input/source.json"),
            load_json(self.product_dir / "output/product-analysis.json"),
            load_json(self.product_dir / "output/style-profile.json"),
            started_at="2026-07-12T00:00:00+08:00",
        )

    def test_new_plan_requires_locked_product_composition(self):
        self.assertTrue(self.plan["generator_contract"]["product_pixel_lock_required"])
        self.assertEqual(
            self.plan["generator_contract"]["composition_tool"],
            "scripts/locked_product_compositor.py",
        )
        operations = {
            item["operation"]
            for key in ("main_images", "detail_images", "disclaimer_images")
            for item in self.plan[key]
            if item["status"] == "planned"
        }
        self.assertEqual(operations, {"locked_product_composite"})
        self.assertTrue(self.plan["generator_contract"]["raw_1688_image_direct_upload_forbidden"])
        self.assertTrue(self.plan["generator_contract"]["final_chinese_text_forbidden"])
        self.assertEqual(
            self.plan["generator_contract"]["advisory_skills_required"],
            ["ecommerce-branding"],
        )
        self.assertTrue(self.plan["generator_contract"]["project_rules_take_precedence"])
        self.assertEqual(self.plan["generator_contract"]["image_slot_concurrency"], 3)
        self.assertTrue(self.plan["generator_contract"]["image_qc_same_execution"])
        self.assertTrue(all(
            "reject_all_chinese_text" in item["source_text_policy"]
            for key in ("main_images", "detail_images", "disclaimer_images")
            for item in self.plan[key]
        ))

    def test_external_ecommerce_skill_is_advisory_only(self):
        skill = (ROOT / ".agents/skills/image-generator/SKILL.md").read_text(encoding="utf-8")
        runner = (ROOT / "scripts/run_batch.py").read_text(encoding="utf-8")
        self.assertIn("$ecommerce-branding", skill)
        self.assertIn("$ecommerce-branding", runner)
        self.assertNotIn("$shopify-product-photography-guide", skill)
        self.assertNotIn("$shopify-product-photography-guide", runner)
        self.assertIn("advisory-only", skill)
        self.assertIn("always take precedence", skill)

    def test_chinese_text_is_a_critical_image_failure(self):
        rules = load_json(ROOT / "rules/image_qc_rules.json")
        self.assertIn("chinese_text_present", rules["critical_failures"])

    def test_qc_rejects_new_plan_without_lock_manifest(self):
        plan = copy.deepcopy(self.plan)
        rules = load_json(ROOT / "rules/image_qc_rules.json")
        main_slot = plan["main_images"][0]["slot"]
        with tempfile.TemporaryDirectory() as temporary_product_dir:
            _, _, issues, failures = inspect_images(
                Path(temporary_product_dir), plan, [main_slot], rules
            )
        self.assertIn("product_pixel_lock_missing", failures)
        self.assertTrue(any(item["code"] == "product_pixel_lock_missing" for item in issues))


if __name__ == "__main__":
    unittest.main()
