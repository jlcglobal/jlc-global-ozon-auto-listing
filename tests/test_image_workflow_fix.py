import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator
from PIL import Image

import scripts.image_source_preflight as image_source_preflight
from scripts.image_planner import (
    ROOT,
    build_image_plan,
    build_image_strategy_enhancement,
    enhance_image_prompt_with_strategy,
    load_json,
    revision_prompt_addendum,
    russian_image_text,
    usable_reference_images,
    variant_main_specs,
)
from scripts.image_generator_contract import build_prompt_packet
from scripts.ozon_ecommerce_designer_contract import materialize
from scripts.image_slot_scheduler import checked_slot_is_current, receipt_slot_is_current, requested_slot_names
from scripts.image_source_preflight import source_image_candidates, source_image_key
from scripts.image_text_overlay import overlay
from scripts.production_input_guard import write_source_manifest
from scripts.run_batch import codex_exec_command, codex_worker_env
from scripts.sku_image_bindings import save_sku_image_binding
from tests.test_ozon_ecommerce_designer_contract import build_design, make_product


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class ImageWorkflowFixTest(unittest.TestCase):
    def test_main_image_receipt_does_not_require_subjective_visual_acceptance(self):
        with tempfile.TemporaryDirectory() as tmp:
            product_dir = Path(tmp) / "products" / "P777777"
            output = product_dir / "output/generated-images/variant-main/main.png"
            output.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (900, 1200), "white").save(output)
            import scripts.image_slot_scheduler as scheduler

            digest = scheduler.file_sha256(output)
            receipt = {
                "product_id": "P777777",
                "slot": "main-sku-1",
                "output_path": "products/P777777/output/generated-images/variant-main/main.png",
                "status": "PASS",
                "attempt": 1,
                "sha256": digest,
                "dimensions": {"width": 900, "height": 1200},
                "hard_failures": [],
                "checked_at": "2026-08-13T00:00:00+00:00",
                "generation_source": "built_in_image_tool",
                "designer_prompt_followed": True,
                "local_script_generation": False,
            }
            write_json(product_dir / "output/image-slot-results/main-sku-1.json", receipt)

            self.assertTrue(receipt_slot_is_current(product_dir, "main-sku-1", output))

            receipt["visual_acceptance"] = {
                "status": "PASS",
                "checks": {
                    "product_visually_dominant": True,
                    "text_integrated_not_poster": True,
                    "title_not_dominating_product": True,
                    "main_three_second_click": True,
                },
                "failures": [],
            }
            write_json(product_dir / "output/image-slot-results/main-sku-1.json", receipt)

            self.assertTrue(receipt_slot_is_current(product_dir, "main-sku-1", output))
            self.assertTrue(checked_slot_is_current(receipt, output))

            receipt["visual_acceptance"]["checks"]["product_visually_dominant"] = False
            write_json(product_dir / "output/image-slot-results/main-sku-1.json", receipt)

            self.assertFalse(receipt_slot_is_current(product_dir, "main-sku-1", output))

    def test_detail_image_receipt_does_not_require_visual_acceptance(self):
        with tempfile.TemporaryDirectory() as tmp:
            product_dir = Path(tmp) / "products" / "P777778"
            output = product_dir / "output/generated-images/detail/detail-001.png"
            output.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (900, 1200), "white").save(output)
            import scripts.image_slot_scheduler as scheduler

            receipt = {
                "product_id": "P777778",
                "slot": "detail-001",
                "output_path": "products/P777778/output/generated-images/detail/detail-001.png",
                "status": "PASS",
                "attempt": 1,
                "sha256": scheduler.file_sha256(output),
                "dimensions": {"width": 900, "height": 1200},
                "hard_failures": [],
                "checked_at": "2026-08-13T00:00:00+00:00",
                "generation_source": "built_in_image_tool",
                "designer_prompt_followed": True,
                "local_script_generation": False,
            }
            write_json(product_dir / "output/image-slot-results/detail-001.json", receipt)

            self.assertTrue(receipt_slot_is_current(product_dir, "detail-001", output))

    def test_image_design_revision_prompt_only_targets_failed_slot(self):
        revision = {
            "product_id": "P000001",
            "failed_slots": ["detail-001"],
            "critical_failures": ["product_pixel_lock_failed"],
            "slot_issues": {
                "detail-001": [{
                    "code": "product_pixel_lock_failed",
                    "message": "商品结构被改成另一款",
                }],
            },
            "reason": "Image QC failed",
        }

        failed = revision_prompt_addendum(revision, "detail-001")
        passed = revision_prompt_addendum(revision, "detail-002")

        self.assertIn("Image designer revision for this failed slot only", failed)
        self.assertIn("product_pixel_lock_failed", failed)
        self.assertIn("Preserve the product fact lock", failed)
        self.assertIn("do not change title, description, tags, attributes", failed)
        self.assertEqual(passed, "")

    def test_runtime_no_longer_forces_background_only_or_external_branding_skill(self):
        runner = (ROOT / "scripts/run_batch.py").read_text(encoding="utf-8")
        skill = (ROOT / ".agents/skills/image-generator/SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("内置生图只生成不含商品", runner)
        self.assertNotIn("必须用locked_product_compositor.py", runner)
        self.assertNotIn("并以只读建议模式调用$ecommerce-branding", runner)
        self.assertNotIn("$ecommerce-branding", skill)
        self.assertNotIn("短边不足600px", runner)
        self.assertIn("below 600 pixels", skill)
        self.assertIn("compose_from_real_images", skill)
        self.assertIn("edit_real_image", skill)

    def test_unattended_child_skips_questions_and_unrelated_mcp(self):
        with tempfile.TemporaryDirectory() as directory:
            fake_codex = Path(directory) / "codex"
            fake_codex.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake_codex.chmod(0o755)
            settings = {
                "codex_command": str(fake_codex), "codex_reasoning_effort_by_step": {"default": "high"},
            }
            command = codex_exec_command(settings, "image_generation", "禁止向用户提问")
            self.assertIn("--ephemeral", command)
            self.assertIn("chronicle", command)
            self.assertIn("mcp_servers={}", command)
            self.assertEqual(command[-1], "禁止向用户提问")
        runner = (ROOT / "scripts/run_batch.py").read_text(encoding="utf-8")
        self.assertIn("只读image-plan该slot和product-fact-lock", runner)
        self.assertIn("禁止提问", runner)
        self.assertIn("参考图只用当前商品input", runner)
        self.assertIn("不分析、不改计划、不碰其他图位", runner)

    def test_design_worker_does_not_enable_live_web_search(self):
        with tempfile.TemporaryDirectory() as directory:
            fake_codex = Path(directory) / "codex"
            fake_codex.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake_codex.chmod(0o755)
            settings = {
                "codex_command": str(fake_codex), "codex_reasoning_effort_by_step": {"default": "high"},
            }
            command = codex_exec_command(settings, "ecommerce_design", "只处理当前商品")
            self.assertNotIn('web_search="live"', command)

    def test_codex_worker_uses_project_python_for_image_helpers(self):
        with patch.dict(os.environ, {"APP_MODE": "development"}):
            env = codex_worker_env({"app_mode": "development"})
        self.assertEqual(env["CAF_PYTHON_BIN"], str(ROOT / ".venv/bin/python"))
        self.assertEqual(env["PATH"].split(":", 1)[0], str(ROOT / ".venv/bin"))
        # 2026-08-14：russian_copy 已收口为确定性本地步骤（run_local_step 直接以
        # 项目 python 运行子进程），run_batch 源码中不再出现 $CAF_PYTHON_BIN 引用；
        # 委托路径的 env 契约由上面两行直接验证。

    def test_1688_thumbnail_prefers_original_resolution_url(self):
        thumbnail = "https://cbu01.alicdn.com/img/ibank/item.jpg_sum.jpg"
        self.assertEqual(source_image_candidates(thumbnail)[0], "https://cbu01.alicdn.com/img/ibank/item.jpg")
        self.assertEqual(source_image_candidates(thumbnail)[-1], thumbnail)
        self.assertEqual(
            source_image_key("https://cbu01.alicdn.com/img/ibank/O1CN01ABC_!!1-0-cib.jpg_sum.jpg"),
            source_image_key("https://cbu01.alicdn.com/img/ibank/O1CN01ABC_!!1-0-cib.jpg_.webp"),
        )

    def test_single_sku_low_res_thumbnail_can_use_matching_detail_image(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product_dir = root / "products/P777780"
            sku_path = product_dir / "input/sku-images/sku-001.jpg"
            detail_path = product_dir / "input/detail-images/detail-001.webp"
            sku_path.parent.mkdir(parents=True)
            detail_path.parent.mkdir(parents=True)
            Image.new("RGB", (400, 400), "gray").save(sku_path)
            Image.new("RGB", (900, 900), "white").save(detail_path)
            source = {
                "product_id": "P777780",
                "collection_id": "COL-P777780",
                "source_kind": "workbench_collection",
                "source_path": "products/P777780/input/source.json",
                "source_url": "https://detail.1688.com/offer/test.html",
                "title_cn": "单SKU商品",
                "collected_at": "2026-07-24T10:00:00+08:00",
                "main_images": [],
                "detail_images": [{
                    "id": "detail-001",
                    "original_url": "https://cbu01.alicdn.com/img/ibank/O1CN01SAME_!!1-0-cib.jpg_.webp",
                    "local_path": "products/P777780/input/detail-images/detail-001.webp",
                    "download_status": "downloaded",
                }],
                "skus": [{
                    "sku_id": "sku-1",
                    "sku_name": "单规格",
                    "local_image_path": "products/P777780/input/sku-images/sku-001.jpg",
                    "variant_local_image_path": "products/P777780/input/sku-images/sku-001.jpg",
                    "image_url": "https://cbu01.alicdn.com/img/ibank/O1CN01SAME_!!1-0-cib.jpg_sum.jpg",
                    "variant_image_url": "https://cbu01.alicdn.com/img/ibank/O1CN01SAME_!!1-0-cib.jpg_sum.jpg",
                    "sku_image_missing": False,
                    "selection_order": 1,
                }],
            }
            write_json(product_dir / "input/source.json", source)
            write_json(product_dir / "input/raw-snapshot.json", {"sku_raw_data": []})

            preflight = image_source_preflight.build_preflight(product_dir, allow_download=False)

            self.assertEqual(preflight["status"], "PASS")
            check = preflight["sku_references"][0]
            self.assertEqual(check["preferred_reference_path"], "products/P777780/input/detail-images/detail-001.webp")
            self.assertEqual(check["reference_override"]["decision"], "auto_single_sku_gallery_reference")
            self.assertEqual(check["reference_override"]["match_kind"], "sku_url_exact")
            self.assertEqual(check["reference_override"]["source_type"], "detail_gallery_reference")

    def test_single_sku_gallery_fallback_excludes_unselected_sku_image(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product_dir = root / "products/P777781"
            sku_path = product_dir / "input/sku-images/sku-001.jpg"
            wrong_main = product_dir / "input/main-images/main-001.webp"
            good_main = product_dir / "input/main-images/main-002.webp"
            sku_path.parent.mkdir(parents=True)
            wrong_main.parent.mkdir(parents=True)
            Image.new("RGB", (400, 400), "gray").save(sku_path)
            Image.new("RGB", (1200, 1200), "pink").save(wrong_main)
            Image.new("RGB", (900, 900), "white").save(good_main)
            selected_url = "https://cbu01.alicdn.com/img/ibank/O1CN01SELECTED_!!1-0-cib.jpg_sum.jpg"
            unselected_url = "https://cbu01.alicdn.com/img/ibank/O1CN01UNSELECTED_!!1-0-cib.jpg_sum.jpg"
            source = {
                "product_id": "P777781",
                "collection_id": "COL-P777781",
                "source_kind": "workbench_collection",
                "source_path": "products/P777781/input/source.json",
                "source_url": "https://detail.1688.com/offer/test.html",
                "title_cn": "单SKU商品",
                "collected_at": "2026-07-24T10:00:00+08:00",
                "main_images": [
                    {
                        "id": "main-001",
                        "original_url": "https://cbu01.alicdn.com/img/ibank/O1CN01UNSELECTED_!!1-0-cib.jpg_.webp",
                        "local_path": "products/P777781/input/main-images/main-001.webp",
                        "download_status": "downloaded",
                    },
                    {
                        "id": "main-002",
                        "original_url": "https://cbu01.alicdn.com/img/ibank/O1CN01OTHER_!!1-0-cib.jpg_.webp",
                        "local_path": "products/P777781/input/main-images/main-002.webp",
                        "download_status": "downloaded",
                    },
                ],
                "detail_images": [],
                "skus": [{
                    "sku_id": "selected-sku",
                    "sku_name": "大号腌菜罐",
                    "local_image_path": "products/P777781/input/sku-images/sku-001.jpg",
                    "variant_local_image_path": "products/P777781/input/sku-images/sku-001.jpg",
                    "image_url": selected_url,
                    "variant_image_url": selected_url,
                    "sku_image_missing": False,
                    "selection_order": 1,
                }],
            }
            write_json(product_dir / "input/source.json", source)
            write_json(product_dir / "input/raw-snapshot.json", {
                "sku_raw_data": [
                    {"sku_id": "selected-sku", "sku_name": "大号腌菜罐", "image_url": selected_url},
                    {"sku_id": "unselected-sku", "sku_name": "粉色樱花碟", "image_url": unselected_url},
                ],
            })

            preflight = image_source_preflight.build_preflight(product_dir, allow_download=False)

            self.assertEqual(preflight["status"], "PASS")
            check = preflight["sku_references"][0]
            self.assertEqual(check["preferred_reference_path"], "products/P777781/input/main-images/main-002.webp")
            self.assertEqual(check["reference_override"]["decision"], "auto_single_sku_gallery_reference")
            self.assertEqual(check["reference_override"]["match_kind"], "single_sku_current_product_gallery")

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

    def test_user_bound_main_gallery_image_feeds_preflight_and_image_plan_references(self):
        with tempfile.TemporaryDirectory() as directory:
            product_dir = Path(directory) / "products/P777779"
            main_path = product_dir / "input/main-images/main-001.png"
            main_path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (900, 900), "white").save(main_path)
            target = {
                "sku_id": "single-sku",
                "sku_name": "琥珀色 单规格",
                "local_image_path": "unknown",
                "variant_local_image_path": "unknown",
                "image_url": "unknown",
                "variant_image_url": "unknown",
                "sku_image_missing": True,
                "selection_order": 1,
                "option_values": [{"name_cn": "颜色", "value_cn": "琥珀色"}],
            }
            source = {
                "product_id": "P777779",
                "collection_id": "COL-P777779",
                "source_kind": "workbench_collection",
                "source_path": "products/P777779/input/source.json",
                "source_url": "https://detail.1688.com/offer/test.html",
                "title_cn": "单SKU商品",
                "collected_at": "2026-07-19T10:00:00+08:00",
                "main_images": [{
                    "local_path": "products/P777779/input/main-images/main-001.png",
                    "source_url": "https://cbu01.alicdn.com/main-001.png",
                }],
                "detail_images": [],
                "skus": [target],
            }
            write_json(product_dir / "input/source.json", source)
            write_source_manifest(product_dir)
            save_sku_image_binding(
                product_dir,
                "single-sku",
                "products/P777779/input/main-images/main-001.png",
                bound_by="tester",
            )

            preflight = image_source_preflight.build_preflight(product_dir, allow_download=False)
            self.assertEqual(preflight["status"], "PASS")
            sku_ref = preflight["sku_references"][0]
            self.assertEqual(sku_ref["preferred_reference_path"], "products/P777779/input/main-images/main-001.png")
            self.assertEqual(sku_ref["reference_override"]["decision"], "user_bound_reference_image")
            references = usable_reference_images(source, preflight)
            self.assertEqual([item["path"] for item in references], ["products/P777779/input/main-images/main-001.png"])

    def test_low_resolution_current_product_image_is_subject_reference_not_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product_dir, skus = make_product(root, "P888891", 1)
            low_reference = product_dir / "input/main-images/main-low.png"
            Image.new("RGB", (426, 426), "red").save(low_reference)
            reference_path = "products/P888891/input/main-images/main-low.png"
            source = load_json(product_dir / "input/source.json")
            sku_id = skus[0]["sku_id"]
            source["main_images"] = [{
                "id": "main-low",
                "local_path": reference_path,
                "original_url": "https://cbu01.alicdn.com/img/ibank/main-low.png",
                "download_status": "downloaded",
            }]
            source["skus"][0].update({
                "local_image_path": "unknown",
                "variant_local_image_path": "unknown",
                "image_url": "unknown",
                "variant_image_url": "unknown",
                "sku_image_missing": True,
                "option_values": [{"name_cn": "颜色", "value_cn": "红色"}],
            })
            write_json(product_dir / "input/source.json", source)
            write_source_manifest(product_dir)
            save_sku_image_binding(product_dir, sku_id, reference_path, bound_by="tester")

            preflight = image_source_preflight.build_preflight(product_dir, allow_download=False)

            self.assertEqual(preflight["status"], "PASS")
            self.assertEqual(preflight["blocked_sku_ids"], [])
            sku_ref = preflight["sku_references"][0]
            self.assertEqual(sku_ref["status"], "ready_with_warning")
            self.assertTrue(sku_ref["subject_reference_only"])
            self.assertFalse(sku_ref["proof_ready"])
            self.assertEqual(sku_ref["preferred_reference_path"], reference_path)
            self.assertIn("产品主体参考", sku_ref["reason"])
            references = usable_reference_images(source, preflight)
            self.assertIn(reference_path, [item["path"] for item in references if item["usable"]])
            specs, _ = variant_main_specs(product_dir, source, preflight)
            self.assertTrue(specs[0]["reference_ready"])

            design_skus = [dict(source["skus"][0], local_image_path=reference_path)]
            design = build_design(product_dir, design_skus)
            write_json(product_dir / "output/ozon-ecommerce-design.json", design)
            materialize(product_dir, design)
            plan = build_image_plan(product_dir, source, {}, {}, started_at="2026-07-24T00:00:00+08:00")

            self.assertEqual(len(plan["main_images"]), 1)
            self.assertEqual(len(plan["detail_images"]), 8)
            self.assertEqual(plan["main_images"][0]["status"], "planned")
            self.assertEqual(plan["main_images"][0]["operation"], "generate_from_reference")
            self.assertEqual(plan["main_images"][0]["reference_product_images"], [reference_path])
            self.assertFalse(
                any(item["operation"] == "needs_human_input" for item in plan["main_images"] + plan["detail_images"])
            )
            schema = load_json(ROOT / "templates/image-plan.schema.json")
            self.assertEqual(list(Draft202012Validator(schema).iter_errors(plan)), [])

    def test_forced_same_appearance_override_replaces_wrong_bound_sku_image(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product_dir = root / "products/P777778"
            wrong_path = product_dir / "input/sku-images/wrong-1500-new.jpg"
            source_path = product_dir / "input/sku-images/source-1500-old.jpg"
            wrong_path.parent.mkdir(parents=True)
            Image.new("RGB", (800, 800), "red").save(wrong_path)
            Image.new("RGB", (800, 800), "green").save(source_path)
            target = {
                "sku_id": "1000ml",
                "sku_name": "黄油搅拌器1000ml;透明",
                "purchase_price": 122,
                "image_path": "products/P777778/input/sku-images/wrong-1500-new.jpg",
                "selection_order": 1,
                "option_values": [{"name_cn": "规格", "value_cn": "黄油搅拌器1000ml"}],
            }
            source_sku = {
                "sku_id": "1500-old",
                "sku_name": "黄油搅拌器老款1500ml;透明",
                "purchase_price": 126,
                "image_path": "products/P777778/input/sku-images/source-1500-old.jpg",
                "selection_order": 2,
                "option_values": [{"name_cn": "规格", "value_cn": "黄油搅拌器老款1500ml"}],
            }
            write_json(product_dir / "input/source.json", {
                "product_id": "P777778",
                "collection_id": "COL-P777778",
                "main_images": [],
                "detail_images": [],
                "skus": [target, source_sku],
            })
            write_json(product_dir / "input/manual-confirmation.json", {
                "sku_image_reference_overrides": {"1000ml": {
                    "source_sku_id": "1500-old",
                    "decision": "user_confirmed_same_appearance",
                    "scope": "reference_image_only",
                    "force_reference_override": True,
                    "source_capacity_ml": 1500,
                    "target_capacity_ml": 1000,
                    "scale_ratio": 0.6667,
                    "must_preserve_target_sku_facts": True,
                }},
            })
            with patch.object(image_source_preflight, "ROOT", root):
                preflight = image_source_preflight.build_preflight(product_dir, allow_download=False)

            first = preflight["sku_references"][0]
            self.assertEqual(first["preferred_reference_path"], "products/P777778/input/sku-images/source-1500-old.jpg")
            self.assertEqual(first["reference_override"]["source_sku_id"], "1500-old")
            self.assertEqual(first["reference_override"]["target_sku_name"], "黄油搅拌器1000ml;透明")

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
            extra_main = product_dir / "input/main-images/main-extra.png"
            extra_main.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (900, 900), "blue").save(extra_main)
            source["main_images"] = [{
                "id": "main-extra",
                "local_path": "products/P888888/input/main-images/main-extra.png",
                "original_url": "https://cbu01.alicdn.com/img/ibank/main-extra.png",
                "download_status": "downloaded",
            }]
            write_json(product_dir / "input/source.json", source)
            write_source_manifest(product_dir)
            design = build_design(product_dir, skus)
            design["detail_images"][0]["prompt"] += (
                " Use only these current product real references: "
                "products/P888888/input/sku-images/sku-1.png."
            )
            design["detail_images"][1]["must_preserve"] = [
                "gold metal frame", "colored beads", "transparent rhinestones",
            ]
            design["detail_images"][1]["russian_text"] = [
                "Цветные бусины", "Стразы по контуру", "Металлическая рамка",
            ]
            design["detail_images"][1]["art_direction"]["scene"] = (
                "macro fragment of visible beads, rhinestones and metal frame"
            )
            design["detail_images"][1]["art_direction"]["composition"] = (
                "true close-up macro proof shot; crop into the visible details"
            )
            design["detail_images"][2]["art_direction"]["scene"] = (
                "A visible adult Russian lifestyle model demonstrates the product in a source-grounded use scene for this one slot only"
            )
            write_json(product_dir / "output/ozon-ecommerce-design.json", design)
            materialize(product_dir, design)
            write_json(product_dir / "output/image-source-preflight.json", {
                "status": "PASS",
                "blocked_sku_ids": [],
                "sku_references": [
                    {
                        "source_sku_id": "sku-1",
                        "original_reference_path": "products/P888888/input/sku-images/sku-1.png",
                        "preferred_reference_path": "products/P888888/input/main-images/main-extra.png",
                        "status": "ready",
                        "reference_override": {
                            "decision": "auto_single_sku_gallery_reference",
                        },
                    },
                    {
                        "source_sku_id": "sku-2",
                        "original_reference_path": "products/P888888/input/sku-images/sku-2.png",
                        "preferred_reference_path": "products/P888888/input/sku-images/sku-2.png",
                        "status": "ready",
                    },
                ],
            })
            write_json(product_dir / "output/variant-decision.json", {"detected_difference_fields": [{
                "source_field": "规格", "difference_kind": "size_or_measurement",
            }]})
            plan = build_image_plan(product_dir, source, {}, {}, started_at="2026-07-13T00:00:00+08:00")
            self.assertEqual({item["operation"] for item in plan["main_images"]}, {"generate_from_reference"})
            self.assertEqual({item["operation"] for item in plan["detail_images"]}, {"generate_from_reference"})
            comparison = [item for item in plan["detail_images"] if item["layout_type"] == "sku_comparison"]
            self.assertTrue(comparison)
            for item in comparison:
                self.assertEqual(item["operation"], "generate_from_reference")
            lifestyle = [item for item in plan["detail_images"] if item["operation"] == "generate_from_reference"]
            self.assertTrue(lifestyle)
            self.assertEqual(len(plan["main_images"]), 2)
            self.assertEqual(len(plan["detail_images"]), 8)
            for field in ("image_positioning", "main_image_goal", "visual_style", "need_model", "avoid_style"):
                self.assertIn(field, plan)
                self.assertIn(field, plan["main_images"][0])
                self.assertIn(field, plan["detail_images"][0])
            self.assertIn("Text whitelist: only these Russian strings", plan["main_images"][0]["prompt"])
            self.assertIn('"JLC GLOBAL"', plan["main_images"][0]["prompt"])
            # 2026-08-15: the designer's full slot prompt is now preserved (no 320-char truncation),
            # so the fixture's own "Render these exact lines once" wording legitimately survives.
            self.assertIn("Render these exact lines once", plan["main_images"][0]["prompt"])
            self.assertIn("no large title block", plan["main_images"][0]["prompt"])
            self.assertIn("Text whitelist: only these Russian strings", plan["main_images"][0]["prompt"])
            self.assertIn("SKU title:", plan["main_images"][0]["prompt"])
            self.assertNotIn("Image sales strategy enhancement", plan["main_images"][0]["prompt"])
            all_slots = plan["main_images"] + plan["detail_images"]
            for item in all_slots:
                self.assertIn("Fact lock", item["prompt"])
                self.assertIn("JLC GLOBAL", item["prompt"])
                self.assertIn("Text whitelist", item["prompt"])
                self.assertIn("preserve exact product/SKU structure", item["prompt"])
                self.assertNotIn("Image sales direction:", item["prompt"])
                self.assertNotIn("Required shared-detail roles", item["prompt"])
                self.assertLess(len(item["prompt"]), 5000)
                self.assertFalse(
                    {"capacity_badge", "benefit_section", "icon_chips"} & set(item["overlay_modules"]),
                    item["overlay_modules"],
                )
            for item in plan["detail_images"]:
                self.assertNotIn("Main image realism: camera-shot product photography look", item["prompt"])
            model_slots = [item["slot"] for item in all_slots if item["need_model"]]
            self.assertEqual(len(model_slots), 1)
            self.assertEqual(model_slots, [design["detail_images"][2]["slot"]])
            self.assertIn("product parameter/specification image", plan["detail_images"][1]["prompt"])
            self.assertIn("use a true close-up", plan["detail_images"][1]["prompt"])
            self.assertIn("transparent crystal border", plan["detail_images"][1]["prompt"])
            self.assertIn("product-specific real-use or scale scene", plan["detail_images"][2]["prompt"])
            self.assertNotIn("required Russian model image", plan["detail_images"][2]["prompt"])
            self.assertNotIn("hand-only/body-fragment-only/object-only is not acceptable", plan["detail_images"][2]["prompt"])
            self.assertNotIn("final required product-based disclaimer image", plan["detail_images"][-1]["prompt"])
            self.assertFalse(plan["generator_contract"]["product_pixel_lock_required"])
            self.assertEqual(plan["generator_contract"]["deterministic_image_types"], ["comparison", "size_spec"])
            self.assertEqual(plan["generator_contract"]["advisory_skills_required"], [])
            self.assertLessEqual(max(len(item["reference_images"]) for item in plan["detail_images"]), 5)
            self.assertTrue(
                any(
                    "products/P888888/input/main-images/main-extra.png" in item["reference_images"]
                    for item in plan["detail_images"]
                )
            )
            self.assertIn("products/P888888/input/main-images/main-extra.png", plan["detail_images"][0]["reference_images"])
            self.assertNotIn("products/P888888/input/sku-images/sku-1.png", plan["detail_images"][0]["reference_images"])
            self.assertIn("products/P888888/input/main-images/main-extra.png", plan["detail_images"][0]["prompt"])
            self.assertNotIn("products/P888888/input/sku-images/sku-1.png", plan["detail_images"][0]["prompt"])
            schema = load_json(ROOT / "templates/image-plan.schema.json")
            self.assertEqual(list(Draft202012Validator(schema).iter_errors(plan)), [])
            write_json(product_dir / "output/image-plan.json", plan)
            packet = build_prompt_packet(product_dir, plan["main_images"][0]["slot"])
            self.assertEqual(packet["image_sales_strategy"]["image_positioning"], plan["image_positioning"])
            self.assertEqual(packet["image_intent"]["visual_style"], plan["main_images"][0]["visual_style"])
            self.assertTrue(packet["slot_prompt"].startswith("Create a source-grounded 3:4 Ozon"))
            self.assertNotIn("Image sales strategy enhancement", packet["slot_prompt"])
            self.assertIn("image-to-image", packet["instruction"])
            self.assertIn("never a detached headline block", packet["instruction"])
            self.assertEqual(packet["generation_contract"]["exact_russian_text"], ["JLC GLOBAL"])
            self.assertTrue(packet["generation_contract"]["product_body_topology_lock_required"])
            self.assertIn("changed structure, size proportion, specification, quantity or set composition", packet["generation_contract"]["quality_gate"])

    def test_required_shared_detail_roles_do_not_add_a_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            product_dir, skus = make_product(Path(directory), "P888889", 2)
            source = load_json(product_dir / "input/source.json")
            design = build_design(product_dir, skus)
            write_json(product_dir / "output/ozon-ecommerce-design.json", design)
            materialize(product_dir, design)

            plan = build_image_plan(product_dir, source, {}, {}, started_at="2026-07-13T00:00:00+08:00")

            self.assertEqual(len(plan["main_images"]), 2)
            self.assertEqual(len(plan["detail_images"]), 8)
            self.assertEqual([item["slot"] for item in plan["detail_images"] if item["need_model"]], [])
            self.assertIn("product parameter/specification image", plan["detail_images"][1]["prompt"])
            self.assertNotIn("required Russian model image", plan["detail_images"][2]["prompt"])
            self.assertNotIn("final required product-based disclaimer image", plan["detail_images"][-1]["prompt"])
            self.assertFalse(
                any(item["operation"] == "needs_human_input" for item in plan["main_images"] + plan["detail_images"])
            )
            schema = load_json(ROOT / "templates/image-plan.schema.json")
            self.assertEqual(list(Draft202012Validator(schema).iter_errors(plan)), [])

    def test_visual_reference_analysis_guides_prompts_without_changing_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            product_dir, skus = make_product(Path(directory), "P888887", 2)
            source = load_json(product_dir / "input/source.json")
            design = build_design(product_dir, skus)
            write_json(product_dir / "output/ozon-ecommerce-design.json", design)
            materialize(product_dir, design)
            visual_reference = {
                "schema_version": "1.0.0",
                "product_id": "P888887",
                "source_kind": "ozon_reference_images",
                "provider": {
                    "name": "spgoodman/florence2-visionapi",
                    "mode": "local_florence2_visionapi",
                    "endpoint": "http://127.0.0.1:54880/process_image",
                },
                "reference_images": [{
                    "path": "manual/ozon-reference.jpg",
                    "caption": "close-up handheld seller photo on a desk with screen light and shallow depth of field",
                    "role": "ozon_competitor_reference",
                }],
                "real_photo_style": {
                    "camera_feel": "real phone seller photo",
                    "lighting": "mixed indoor light and screen glow",
                    "background": "desk with blurred monitor background",
                    "depth_of_field": "shallow depth of field",
                    "texture": "visible material texture and lens softness",
                    "imperfections": "minor handheld framing and mild noise",
                },
                "shot_recipes": [{
                    "shot_type": "macro close-up",
                    "composition": "near product detail on real desk surface",
                    "purpose": "prove material realism",
                    "avoid": ["watermark", "store name"],
                }],
                "negative_style": ["watermark", "store name", "AI-polished poster look"],
                "fact_policy": {
                    "reference_is_not_product_fact": True,
                    "forbidden_fact_sources": ["competitor brand", "competitor packaging"],
                    "allowed_usage": ["camera feel", "lighting style"],
                },
                "processing": {
                    "step": "visual_reference_analysis",
                    "status": "completed",
                    "generated_at": "2026-07-31T00:00:00+08:00",
                    "error": None,
                },
            }
            write_json(product_dir / "output/visual-reference-analysis.json", visual_reference)

            visual_schema = load_json(ROOT / "templates/visual-reference-analysis.schema.json")
            self.assertEqual(list(Draft202012Validator(visual_schema).iter_errors(visual_reference)), [])

            plan = build_image_plan(product_dir, source, {}, {}, started_at="2026-07-31T00:00:00+08:00")
            self.assertEqual(len(plan["main_images"]), 2)
            self.assertEqual(len(plan["detail_images"]), 8)
            self.assertIn("products/P888887/output/visual-reference-analysis.json", plan["source_refs"])
            for item in plan["main_images"] + plan["detail_images"]:
                self.assertIn("Ozon real-photo reference guidance", item["prompt"])
                self.assertIn("real phone seller photo", item["prompt"])
                self.assertIn("never copy their watermark", item["prompt"])
                self.assertIn("product facts still come only from the current product", item["prompt"])
            schema = load_json(ROOT / "templates/image-plan.schema.json")
            self.assertEqual(list(Draft202012Validator(schema).iter_errors(plan)), [])

    def test_image_strategy_enhancement_is_image_only_and_generic(self):
        design = {
            "visual_system": {
                "style_name": "clean technical marketplace look",
                "value_impression": "show product value through clear proof",
                "palette_logic": "use product colors and verified use context",
                "scene_logic": "each image answers a buyer question",
                "typography_logic": "readable Russian hierarchy",
                "anti_template_rule": "avoid generic category templates",
            },
            "buyer_profile": {"purchase_motivation": "quickly understand whether this product solves the need"},
            "ecommerce_strategy": {"visual_positioning": "sell through use-case clarity"},
            "main_images": [],
            "detail_images": [],
        }
        strategy = build_image_strategy_enhancement(
            design,
            {"core_sales_angle": "source-backed benefit", "purchase_motivation": "simple choice"},
            {"title_ru": "Тестовый товар", "sku_variants": [{"name": "SKU A"}]},
        )
        self.assertEqual(set(strategy), {
            "image_positioning", "main_image_goal", "visual_style", "need_model", "avoid_style",
            "visual_fact_anchor",
        })
        self.assertFalse(strategy["need_model"])
        prompt = enhance_image_prompt_with_strategy("Base image prompt.\nForbidden: weak catalog layout.", strategy)
        self.assertTrue(prompt.startswith("Base image prompt."))
        self.assertIn("Fact lock: preserve exact product/SKU structure, color, proportions, size/capacity, quantity and confirmed accessories", prompt)
        self.assertIn("no large title block, empty text panel, template badge or poster text-pasting", prompt)
        self.assertIn('Text whitelist: only these Russian strings, once if used: "JLC GLOBAL"', prompt)
        self.assertIn("Reference props are not included accessories unless confirmed", prompt)
        self.assertLess(len(prompt), 1800)
        self.assertNotIn("Image sales strategy enhancement", prompt)
        main_prompt = enhance_image_prompt_with_strategy(
            "Base image prompt.",
            {**strategy, "is_main_image": True},
        )
        self.assertIn("Fact lock: preserve exact product/SKU structure", main_prompt)
        contaminated = build_image_strategy_enhancement(
            design,
            {
                "core_sales_angle": "通过清晰俄文大标题完成电商表达",
                "purchase_motivation": "不要出现中文策略",
            },
            {"title_ru": "Тестовый товар", "sku_variants": [{"name": "中文SKU"}]},
        )
        contaminated_prompt = enhance_image_prompt_with_strategy(
            "Create a marketing poster with a huge headline, capacity badge and three-card benefit row.",
            contaminated,
        )
        self.assertFalse(re.search(r"[\u3400-\u9fff]", contaminated_prompt), contaminated_prompt)
        self.assertNotIn("huge headline", contaminated_prompt.casefold())
        self.assertNotIn("capacity badge", contaminated_prompt.casefold())
        self.assertNotIn("three-card benefit row", contaminated_prompt.casefold())

        model_prompt = enhance_image_prompt_with_strategy(
            "Base image prompt.\nForbidden: weak catalog layout.",
            {**strategy, "need_model": True},
        )
        self.assertIn("Optional real-use scale scene", model_prompt)
        self.assertIn("visible adult may appear only when the slot's product evidence and buyer question support it", model_prompt)
        self.assertIn("do not force a model", model_prompt)
        self.assertNotIn("hand-only/body-fragment-only/object-only is not acceptable", model_prompt)
        self.assertNotIn("Human/model/lifestyle elements are allowed", model_prompt)


if __name__ == "__main__":
    unittest.main()
