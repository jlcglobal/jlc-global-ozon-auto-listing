import json
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator
from PIL import Image

from scripts.image_planner import build_image_plan, russian_image_text
from scripts.image_slot_scheduler import requested_slot_names
from scripts.image_source_preflight import source_image_candidates
from scripts.image_text_overlay import overlay
from scripts.run_batch import codex_exec_command
from scripts.style_selector import ROOT, load_json


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class ImageWorkflowFixTest(unittest.TestCase):
    def test_runtime_no_longer_forces_background_only_or_external_branding_skill(self):
        runner = (ROOT / "scripts/run_batch.py").read_text(encoding="utf-8")
        skill = (ROOT / ".agents/skills/image-generator/SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("内置生图只生成不含商品", runner)
        self.assertNotIn("必须用locked_product_compositor.py", runner)
        self.assertNotIn("并以只读建议模式调用$ecommerce-branding", runner)
        self.assertNotIn("$ecommerce-branding", skill)
        self.assertIn("短边不足600px", runner)
        self.assertIn("compose_from_real_images", skill)
        self.assertIn("edit_real_image", skill)

    def test_unattended_child_skips_questions_and_unrelated_mcp(self):
        settings = {
            "codex_command": "/tmp/codex", "codex_reasoning_effort_by_step": {"default": "high"},
        }
        command = codex_exec_command(settings, "image_generation", "禁止向用户提问")
        self.assertIn("--ephemeral", command)
        self.assertIn("chronicle", command)
        self.assertIn("mcp_servers={}", command)
        self.assertEqual(command[-1], "禁止向用户提问")
        runner = (ROOT / "scripts/run_batch.py").read_text(encoding="utf-8")
        self.assertIn("已授权的无人值守批次", runner)
        self.assertIn("禁止向用户提问", runner)
        self.assertIn("转换为以项目根目录开头的绝对路径", runner)

    def test_1688_thumbnail_prefers_original_resolution_url(self):
        thumbnail = "https://cbu01.alicdn.com/img/ibank/item.jpg_sum.jpg"
        self.assertEqual(source_image_candidates(thumbnail)[0], "https://cbu01.alicdn.com/img/ibank/item.jpg")
        self.assertEqual(source_image_candidates(thumbnail)[-1], thumbnail)

    def test_detailed_retry_records_are_normalized(self):
        self.assertEqual(
            requested_slot_names([{"slot": "main-001", "reason": "bad"}, "detail-001"]),
            {"main-001", "detail-001"},
        )

    def test_russian_copy_is_reused_and_overlay_keeps_canvas(self):
        copy = {
            "short_title": "Контейнер для круп",
            "selling_points": [{"text_ru": "Порядок на кухне"}],
        }
        self.assertEqual(russian_image_text("benefit", copy, None), ["Порядок на кухне"])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "image.png"
            Image.new("RGB", (900, 1200), "white").save(path)
            overlay(path, path, "Порядок на кухне")
            with Image.open(path) as image:
                self.assertEqual(image.size, (900, 1200))

    def test_plan_routes_exact_and_lifestyle_images_differently(self):
        with tempfile.TemporaryDirectory() as directory:
            product_dir = Path(directory) / "products/P888888"
            output = product_dir / "output"
            output.mkdir(parents=True)
            structure = ["main", "benefit", "problem_solution", "scene", "feature", "detail", "usage", "size_spec", "comparison"]
            style = {
                "style_family": "kitchen_warm_home", "image_set_structure": structure,
                "classification_status": "selected", "usage_scene": ["厨房收纳"],
                "composition_style": "真实厨房场景", "text_style": "简洁俄文",
                "tone": "warm", "color_direction": ["beige"],
                "target_user": ["家庭用户"], "purchase_motivation": ["收纳"],
                "image_strategy": ["真实参考"],
                "generator_constraints": {
                    "forbidden_visual_signals": [], "required_visual_signals": [],
                    "truthfulness_guardrails": [],
                },
            }
            source = {
                "title_cn": "米桶", "main_images": [], "detail_images": [],
                "skus": [
                    {
                        "sku_id": "sku-1", "sku_name": "20斤", "selection_order": 1,
                        "local_image_path": "products/P888888/input/sku-images/tiny-1.jpg",
                        "variant_local_image_path": "products/P888888/input/sku-images/tiny-1.jpg",
                        "sku_image_missing": False,
                        "option_values": [{"name_cn": "规格", "value_cn": "20斤"}],
                    },
                    {
                        "sku_id": "sku-2", "sku_name": "40斤", "selection_order": 2,
                        "local_image_path": "products/P888888/input/sku-images/tiny-2.jpg",
                        "variant_local_image_path": "products/P888888/input/sku-images/tiny-2.jpg",
                        "sku_image_missing": False,
                        "option_values": [{"name_cn": "规格", "value_cn": "40斤"}],
                    },
                ],
            }
            write_json(output / "variant-decision.json", {"detected_difference_fields": [{
                "source_field": "规格", "difference_kind": "size_or_measurement",
            }]})
            write_json(output / "image-source-preflight.json", {
                "status": "PASS", "blocked_sku_ids": [], "sku_references": [
                    {"source_sku_id": "sku-1", "status": "ready", "preferred_reference_path": "products/P888888/input/sku-images/source-upgrades/sku-1.jpg"},
                    {"source_sku_id": "sku-2", "status": "ready", "preferred_reference_path": "products/P888888/input/sku-images/source-upgrades/sku-2.jpg"},
                ],
            })
            write_json(output / "product-positioning.json", {
                "purchase_motivation": "厨房收纳", "core_sales_angle": "整洁收纳",
                "competitive_advantage": "unknown", "customer_pain_points": ["unknown"],
                "recommended_visual_direction": "真实厨房", "target_customer": "家庭用户",
            })
            write_json(output / "cost-analysis.json", {"product_dimensions": {
                "length": 20, "width": 20, "height": 30, "unit": "cm",
                "estimated": True, "confidence": 70, "source": "estimated", "source_ref": "manual",
            }})
            plan = build_image_plan(product_dir, source, {}, style, started_at="2026-07-13T00:00:00+08:00")
            self.assertEqual({item["operation"] for item in plan["main_images"]}, {"edit_real_image"})
            exact = [item for item in plan["detail_images"] if item["image_type"] in {"comparison", "size_spec"}]
            self.assertTrue(exact)
            self.assertEqual({item["operation"] for item in exact}, {"compose_from_real_images"})
            self.assertFalse(plan["generator_contract"]["product_pixel_lock_required"])
            self.assertEqual(plan["generator_contract"]["advisory_skills_required"], [])
            self.assertLessEqual(max(len(item["reference_images"]) for item in plan["detail_images"]), 5)
            schema = load_json(ROOT / "templates/image-plan.schema.json")
            self.assertEqual(list(Draft202012Validator(schema).iter_errors(plan)), [])


if __name__ == "__main__":
    unittest.main()
