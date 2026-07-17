import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator
from PIL import Image

import scripts.image_source_preflight as image_source_preflight
from scripts.image_planner import (
    build_image_plan,
    russian_image_text,
    usable_reference_images,
    variant_main_specs,
)
from scripts.ozon_ecommerce_designer_contract import materialize
from scripts.image_slot_scheduler import requested_slot_names
from scripts.image_source_preflight import source_image_candidates
from scripts.image_text_overlay import overlay
from scripts.run_batch import codex_exec_command, codex_worker_env
from scripts.style_selector import ROOT, load_json
from tests.test_ozon_ecommerce_designer_contract import build_design, make_product


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

    def test_codex_worker_uses_project_python_for_image_helpers(self):
        with patch.dict(os.environ, {"APP_MODE": "development"}):
            env = codex_worker_env({"app_mode": "development"})
        self.assertEqual(env["CAF_PYTHON_BIN"], str(ROOT / ".venv/bin/python"))
        self.assertEqual(env["PATH"].split(":", 1)[0], str(ROOT / ".venv/bin"))
        runner = (ROOT / "scripts/run_batch.py").read_text(encoding="utf-8")
        self.assertIn("$CAF_PYTHON_BIN", runner)

    def test_1688_thumbnail_prefers_original_resolution_url(self):
        thumbnail = "https://cbu01.alicdn.com/img/ibank/item.jpg_sum.jpg"
        self.assertEqual(source_image_candidates(thumbnail)[0], "https://cbu01.alicdn.com/img/ibank/item.jpg")
        self.assertEqual(source_image_candidates(thumbnail)[-1], thumbnail)

    def test_confirmed_same_appearance_sku_can_share_only_the_real_reference_image(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product_dir = root / "products/P777777"
            shared_path = product_dir / "input/sku-images/source.jpg"
            shared_path.parent.mkdir(parents=True)
            Image.new("RGB", (800, 800), "gray").save(shared_path)
            target = {
                "sku_id": "target-sku",
                "sku_name": "25升特厚2.8斤",
                "purchase_price": 24,
                "local_image_path": "unknown",
                "variant_local_image_path": "unknown",
                "image_url": "unknown",
                "variant_image_url": "unknown",
                "sku_image_missing": True,
                "selection_order": 1,
                "option_values": [{"name_cn": "规格", "value_cn": "25升特厚2.8斤"}],
            }
            write_json(product_dir / "input/source.json", {
                "product_id": "P777777", "main_images": [], "detail_images": [], "skus": [target],
            })
            write_json(product_dir / "input/raw-snapshot.json", {
                "sku_raw_data": [{
                    "sku_id": "source-sku", "sku_name": "25升加厚2.4斤",
                    "local_image_path": "products/P777777/input/sku-images/source.jpg",
                    "image_url": "https://cbu01.alicdn.com/source.jpg", "sku_image_missing": False,
                }],
            })
            write_json(product_dir / "input/manual-confirmation.json", {
                "sku_image_reference_overrides": {"target-sku": {
                    "source_sku_id": "source-sku",
                    "decision": "user_confirmed_same_appearance",
                    "scope": "reference_image_only",
                    "confirmed_at": "2026-07-16T04:15:42+08:00",
                    "must_preserve_target_sku_facts": True,
                }},
            })
            write_json(product_dir / "output/variant-decision.json", {
                "detected_difference_fields": [{
                    "source_field": "规格", "difference_kind": "size_or_measurement",
                }],
            })
            with patch.object(image_source_preflight, "ROOT", root):
                preflight = image_source_preflight.build_preflight(product_dir, allow_download=False)

            self.assertEqual(preflight["status"], "PASS")
            check = preflight["sku_references"][0]
            self.assertEqual(check["reference_override"]["source_sku_id"], "source-sku")
            self.assertTrue(check["reference_override"]["must_preserve_target_sku_facts"])
            self.assertEqual(check["preferred_reference_path"], "products/P777777/input/sku-images/source.jpg")
            references = usable_reference_images({"main_images": [], "detail_images": [], "skus": [target]}, preflight)
            self.assertEqual([item["path"] for item in references], ["products/P777777/input/sku-images/source.jpg"])
            specs, _ = variant_main_specs(product_dir, {"skus": [target]}, preflight)
            self.assertEqual(specs[0]["sku_name"], "25升特厚2.8斤")
            self.assertEqual(specs[0]["variant_value"], "25升特厚2.8斤")
            self.assertEqual(target["purchase_price"], 24)

    def test_unconfirmed_sku_image_sharing_remains_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product_dir = root / "products/P777778"
            write_json(product_dir / "input/source.json", {
                "product_id": "P777778", "main_images": [], "detail_images": [], "skus": [{
                    "sku_id": "target-sku", "sku_name": "missing", "local_image_path": "unknown",
                    "variant_local_image_path": "unknown", "image_url": "unknown",
                    "variant_image_url": "unknown", "sku_image_missing": True,
                }],
            })
            write_json(product_dir / "input/manual-confirmation.json", {
                "sku_image_reference_overrides": {"target-sku": {
                    "source_sku_id": "source-sku", "decision": "model_inferred_same_appearance",
                    "scope": "reference_image_only", "must_preserve_target_sku_facts": True,
                }},
            })
            with patch.object(image_source_preflight, "ROOT", root):
                preflight = image_source_preflight.build_preflight(product_dir, allow_download=False)
            self.assertEqual(preflight["status"], "BLOCKED")
            self.assertEqual(preflight["blocked_sku_ids"], ["target-sku"])

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
            overlay(path, path, "ПОРЯДОК НА КУХНЕ||УДОБНО КАЖДЫЙ ДЕНЬ", placement="top")
            with Image.open(path) as image:
                self.assertEqual(image.size, (900, 1200))

    def test_plan_routes_exact_and_lifestyle_images_differently(self):
        with tempfile.TemporaryDirectory() as directory:
            product_dir, skus = make_product(Path(directory), "P888888", 2)
            source = load_json(product_dir / "input/source.json")
            design = build_design(product_dir, skus)
            write_json(product_dir / "output/ozon-ecommerce-design.json", design)
            materialize(product_dir, design)
            style = {
                "style_family": "kitchen_warm_home", "image_set_structure": ["main"],
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
            write_json(product_dir / "output/variant-decision.json", {"detected_difference_fields": [{
                "source_field": "规格", "difference_kind": "size_or_measurement",
            }]})
            plan = build_image_plan(product_dir, source, {}, style, started_at="2026-07-13T00:00:00+08:00")
            self.assertEqual({item["operation"] for item in plan["main_images"]}, {"edit_real_image"})
            exact = [item for item in plan["detail_images"] if item["operation"] == "compose_from_real_images"]
            self.assertTrue(exact)
            self.assertEqual({item["operation"] for item in exact}, {"compose_from_real_images"})
            lifestyle = [item for item in plan["detail_images"] if item["operation"] == "edit_real_image"]
            self.assertTrue(lifestyle)
            self.assertEqual(len(plan["main_images"]), 2)
            self.assertEqual(len(plan["detail_images"]), 8)
            self.assertFalse(plan["generator_contract"]["product_pixel_lock_required"])
            self.assertEqual(plan["generator_contract"]["advisory_skills_required"], [])
            self.assertLessEqual(max(len(item["reference_images"]) for item in plan["detail_images"]), 5)
            schema = load_json(ROOT / "templates/image-plan.schema.json")
            self.assertEqual(list(Draft202012Validator(schema).iter_errors(plan)), [])


if __name__ == "__main__":
    unittest.main()
