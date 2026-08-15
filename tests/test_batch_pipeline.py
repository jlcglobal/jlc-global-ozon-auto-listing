import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from pipeline_runtime import (  # noqa: E402
    MAX_SELECTED_SKUS_PER_PRODUCT,
    PHASE_A_STEPS,
    PHASE_B_STEPS,
    collected_products,
    maybe_create_operator_question,
    queue_product,
)
from run_batch import (  # noqa: E402
    finalize_batch,
    image_slot_stall_seconds,
    image_slot_prompt,
    mark_manual_upload_ready,
    result_row,
    route_image_qc_failures_back_to_image_plan,
    route_upload_image_precheck_back_to_image_plan,
    run_local_step,
    run_single_image_slot,
    transition_to_processing,
    validate_image_slot_result,
    validate_ozon_tags,
)
from production_input_guard import write_source_manifest  # noqa: E402
from tests.test_ozon_ecommerce_designer_contract import (  # noqa: E402
    build_design as build_ecommerce_design,
    make_product as make_ecommerce_design_product,
)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def write_png_header(path: Path, width: int, height: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II5B", width, height, 8, 2, 0, 0, 0)
    )


def make_product(root: Path, number: int, sku_count: int) -> Path:
    product_id = f"P{number:06d}"
    product_dir = root / "products" / product_id
    write_json(product_dir / "input/source.json", {
        "product_id": product_id,
        "collection_id": f"COL-PIPELINE-{number:08d}",
        "source_kind": "workbench_collection",
        "source_path": f"products/{product_id}/input/source.json",
        "source_url": f"https://detail.1688.com/offer/{number}.html",
        "collected_at": "2026-07-16T12:00:00+08:00",
        "captured_at": "2026-07-16T12:00:00+08:00",
        "raw_capture_file": f"products/{product_id}/input/raw-snapshot.json",
        "main_images": [], "detail_images": [],
        "skus": [{"sku_id": str(index)} for index in range(sku_count)],
    })
    write_json(product_dir / "input/raw-snapshot.json", {
        "product_id": product_id, "source_kind": "workbench_collection",
    })
    write_json(product_dir / "input/category-selection.json", {
        "product_id": product_id, "category_id": 1, "type_id": 2,
    })
    write_json(
        product_dir / "status.json",
        {
            "status": "COLLECTED",
            "completed_steps": ["collect_source"],
            "pending_steps": [],
            "history": [],
        },
    )
    write_source_manifest(product_dir)
    return product_dir


class BatchPipelineLimitsTest(unittest.TestCase):
    def test_ozon_tags_allow_less_than_thirty_or_empty_after_safety_filtering(self):
        with tempfile.TemporaryDirectory() as directory:
            product_dir = make_product(Path(directory), 1, 1)
            write_json(product_dir / "output/ozon-tags.json", {
                "tags": ["#держатель", "#органайзер", "#ванная"],
            })
            validate_ozon_tags(product_dir)
            write_json(product_dir / "output/ozon-tags.json", {"tags": []})
            validate_ozon_tags(product_dir)

    def test_feasibility_phase_completes_before_content_and_images(self):
        self.assertEqual(PHASE_A_STEPS, [
            "validate_source", "product_analysis", "category_match", "variant_rules",
            "measurements", "offer_exists_check", "upload_feasibility",
        ])
        self.assertEqual(PHASE_B_STEPS[0], "product_positioning")
        self.assertLess(PHASE_B_STEPS.index("ecommerce_design"), PHASE_B_STEPS.index("russian_copy"))
        self.assertLess(PHASE_B_STEPS.index("russian_copy"), PHASE_B_STEPS.index("field_completion"))
        self.assertLess(PHASE_B_STEPS.index("field_completion"), PHASE_B_STEPS.index("image_plan"))
        self.assertLess(PHASE_B_STEPS.index("image_plan"), PHASE_B_STEPS.index("image_generation"))
        self.assertLess(PHASE_B_STEPS.index("image_qc"), PHASE_B_STEPS.index("ozon_upload"))
        self.assertNotIn("ozon_status", PHASE_B_STEPS)

    def test_ecommerce_design_prepares_attribute_input_before_codex_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            product_dir = make_product(Path(directory), 1, 1)
            write_json(product_dir / "output/ozon-category-attributes.json", {
                "category_id": 1,
                "type_id": 2,
                "attributes": [{
                    "attribute_id": 4383,
                    "attribute_name": "Вес товара, г",
                    "type": "Decimal",
                    "required": False,
                    "is_aspect": True,
                    "allowed_values": [],
                }],
            })
            log_path = product_dir / "logs/full-pipeline.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)

            completed = run_local_step(
                product_dir,
                "ecommerce_design",
                {"step_timeout_seconds": 30},
                log_path,
            )

            self.assertFalse(completed)
            fill_input = json.loads((product_dir / "output/attribute-fill-input.json").read_text())
            compact = json.loads((product_dir / "output/attribute-fill-input.compact.json").read_text())
            self.assertEqual(fill_input["category_id"], 1)
            self.assertEqual(fill_input["type_id"], 2)
            self.assertEqual(fill_input["input_hash"], compact["input_hash"])
            self.assertEqual(fill_input["ozon_attributes"][0]["attribute_id"], 4383)
            status = json.loads((product_dir / "status.json").read_text())
            self.assertNotIn("ecommerce_design", status.get("completed_steps") or [])

    def test_russian_copy_materializes_locally_without_codex_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            product_dir, skus = make_ecommerce_design_product(Path(directory), "P000936", 2)
            design = build_ecommerce_design(product_dir, skus)
            write_json(product_dir / "output/ozon-ecommerce-design.json", design)
            write_json(product_dir / "status.json", {
                "status": "PROCESSING",
                "completed_steps": [
                    "collect_source",
                    *PHASE_A_STEPS,
                    "product_positioning",
                    "ecommerce_design",
                ],
                "pending_steps": ["russian_copy", "field_completion"],
                "next_action": "russian_copy",
                "history": [],
            })
            log_path = product_dir / "logs/full-pipeline.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)

            completed = run_local_step(
                product_dir,
                "russian_copy",
                {"step_timeout_seconds": 30},
                log_path,
            )

            self.assertTrue(completed)
            for relative in (
                "output/copy-ru.json",
                "output/title-ru.json",
                "output/description-ru.json",
                "output/keyword-research-ru.json",
            ):
                self.assertTrue((product_dir / relative).is_file(), relative)
            status = json.loads((product_dir / "status.json").read_text())
            self.assertIn("russian_copy", status.get("completed_steps") or [])
            self.assertEqual(status.get("next_action"), "field_completion")

    def test_batch_product_count_is_not_limited_to_ten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for number in range(1, 13):
                make_product(root, number, 2)
            self.assertEqual(len(collected_products(root)), 12)

    def test_image_qc_revise_without_critical_failure_does_not_pause_product(self):
        with tempfile.TemporaryDirectory() as directory:
            product_dir = make_product(Path(directory), 1, 1)
            log_path = product_dir / "logs/full-pipeline.log"
            write_json(product_dir / "output/image-regeneration-request.json", {"old": True})

            def fake_run_checked(command, *_args, **_kwargs):
                if "ozon-field-completion/cli.py" in command:
                    write_json(product_dir / "output/ozon-draft.json", {"skus": [{"source_sku_id": "0"}]})
                    write_json(product_dir / "output/rich-content.json", {"items": []})
                    write_json(product_dir / "output/ozon-tags.json", {"tags": []})
                    write_json(product_dir / "output/ozon-attributes-final.json", {"attributes": []})
                    write_json(product_dir / "output/ozon-upload-config.json", {"shop_name": "default"})
                else:
                    write_json(product_dir / "output/image-qc-report.json", {
                        "decision": "revise",
                        "critical_failures": [],
                        "issues": [{"severity": "warning", "image_slots": ["detail-001"]}],
                        "images_checked": [{"slot": "detail-001"}],
                    })

            with patch("run_batch.run_checked", side_effect=fake_run_checked):
                completed = run_local_step(
                    product_dir,
                    "image_qc",
                    {"step_timeout_seconds": 30},
                    log_path,
                )

            self.assertTrue(completed)
            self.assertFalse((product_dir / "output/image-regeneration-request.json").exists())
            status = json.loads((product_dir / "status.json").read_text())
            self.assertNotEqual(status.get("status"), "NEEDS_ATTENTION")

    def test_image_qc_hard_failure_routes_back_to_prompt_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            product_dir = make_product(Path(directory), 1, 1)
            status_path = product_dir / "status.json"
            status = json.loads(status_path.read_text())
            status.update({
                "status": "IMAGES_GENERATED",
                "current_step": "image_qc",
                "next_action": "image_qc",
                "completed_steps": [
                    "collect_source", *PHASE_A_STEPS, "product_positioning",
                    "ecommerce_design", "russian_copy", "field_completion",
                    "image_plan", "image_generation",
                ],
                "pending_steps": ["image_qc", "ozon_upload"],
                "retry_count_by_step": {"image_generation": 1, "image_qc": 1},
                "history": [],
            })
            write_json(status_path, status)
            write_json(product_dir / "output/pipeline-cache.json", {
                "steps": {
                    "image_plan": {"ok": True},
                    "image_generation": {"ok": True},
                    "image_qc": {"ok": True},
                },
            })
            report = {
                "critical_failures": ["product_pixel_lock_failed"],
                "issues": [{
                    "code": "product_pixel_lock_failed",
                    "severity": "critical",
                    "message": "商品结构被改成另一款",
                    "image_slots": ["detail-001"],
                }],
                "images_checked": [{"slot": "detail-001"}],
            }

            repaired = route_image_qc_failures_back_to_image_plan(
                product_dir,
                status,
                report,
                "Image QC failed",
            )

            self.assertEqual(repaired["status"], "PROCESSING")
            self.assertEqual(repaired["current_step"], "image_plan")
            self.assertEqual(repaired["next_action"], "image_plan")
            self.assertFalse(repaired["attention_required"])
            self.assertNotIn("image_plan", repaired["completed_steps"])
            self.assertNotIn("image_generation", repaired["completed_steps"])
            request = json.loads((product_dir / "output/image-design-revision-request.json").read_text(encoding="utf-8"))
            self.assertEqual(request["failed_slots"], ["detail-001"])
            self.assertIn("product_pixel_lock_failed", request["critical_failures"])
            self.assertIn("商品结构被改成另一款", request["slot_issues"]["detail-001"][0]["message"])
            regeneration = json.loads((product_dir / "output/image-regeneration-request.json").read_text(encoding="utf-8"))
            self.assertEqual(regeneration["requested_slots"], ["detail-001"])
            self.assertTrue(regeneration["preserve_passed_images"])
            self.assertEqual(repaired["image_qc_revision_count"], 1)
            self.assertEqual(repaired["image_qc_revision_limit"], 10)
            self.assertEqual(repaired["retry_count_by_step"]["image_generation"], 1)
            cache = json.loads((product_dir / "output/pipeline-cache.json").read_text())
            self.assertNotIn("image_plan", cache["steps"])
            self.assertNotIn("image_generation", cache["steps"])
            self.assertNotIn("image_qc", cache["steps"])

            repaired["image_qc_revision_count"] = repaired["image_qc_revision_limit"]
            stopped = route_image_qc_failures_back_to_image_plan(
                product_dir,
                repaired,
                report,
                "Image QC still failed",
            )
            self.assertEqual(stopped["status"], "NEEDS_ATTENTION")
            self.assertEqual(stopped["error_code"], "IMAGE_QC_REVISION_LIMIT")
            self.assertFalse((product_dir / "output/image-regeneration-request.json").exists())

    def test_image_slot_prompt_delegates_visuals_to_source_plan_and_stays_short(self):
        with tempfile.TemporaryDirectory() as directory:
            product_dir = make_product(Path(directory), 1, 1)
            slot_item = {
                "slot": "detail-001",
                "output_path": "products/P000001/output/generated-images/detail/detail-001.png",
                "russian_text": ["Порядок на кухне"],
                "overlay_plan": [{
                    "background_style": "solid",
                    "background_color": "#111827",
                    "box": [0.05, 0.05, 0.62, 0.085],
                }],
            }

            prompt = image_slot_prompt(product_dir, slot_item, 1)

            self.assertIn("只读image-plan该slot", prompt)
            self.assertIn("结构、颜色、规格、配件不变", prompt)
            self.assertIn("信息图必须解释真实产品证明", prompt)
            self.assertIn("白名单俄文", prompt)
            self.assertNotIn("premium practical Ozon ecommerce style", prompt)
            self.assertNotIn("空白占位框", prompt)
            self.assertLess(len(prompt), 900)
            self.assertIn('generation_source="built_in_image_tool"', prompt)
            self.assertIn("designer_prompt_followed=true", prompt)
            self.assertIn("local_script_generation=false", prompt)
            self.assertIn("参考图只用当前商品input", prompt)

    def test_image_slot_stall_is_bounded_by_parent_retry_contract(self):
        self.assertEqual(image_slot_stall_seconds({}), 300)
        self.assertEqual(image_slot_stall_seconds({"image_slot_stall_seconds": 1200}), 600)
        self.assertEqual(image_slot_stall_seconds({"image_slot_stall_seconds": 60}), 180)

    def test_image_generator_skill_forbids_in_child_correction_loops(self):
        skill = (ROOT / ".agents/skills/image-generator/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Use at most one built-in image generation/edit call per slot invocation", skill)
        self.assertIn("write a FAIL receipt", skill)
        self.assertIn("The parent batch runner owns targeted retries", skill)

    def test_image_slot_result_rejects_local_script_or_legacy_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            product_dir = make_product(Path(directory), 1, 1)
            slot_item = {
                "slot": "detail-001",
                "output_path": "products/P000001/output/generated-images/detail/detail-001.png",
            }
            output = product_dir / "output/generated-images/detail/detail-001.png"
            write_png_header(output, 900, 1200)
            import run_batch
            digest = run_batch.image_file_sha256(output)
            write_json(product_dir / "output/image-slot-results/detail-001.json", {
                "product_id": product_dir.name,
                "slot": "detail-001",
                "output_path": slot_item["output_path"],
                "status": "PASS",
                "attempt": 1,
                "sha256": digest,
                "dimensions": {"width": 900, "height": 1200},
                "hard_failures": [],
                "checked_at": "2026-07-23T00:00:00+08:00",
            })

            result = validate_image_slot_result(product_dir, slot_item, 1)

            self.assertEqual(result["status"], "failed")
            self.assertIn("内置生图工具", result["error"])

    def test_image_slot_result_accepts_exact_absolute_output_path(self):
        with tempfile.TemporaryDirectory() as directory:
            product_dir = make_product(Path(directory), 1, 1)
            slot_item = {
                "slot": "detail-001",
                "output_path": "products/P000001/output/generated-images/detail/detail-001.png",
            }
            output = product_dir / "output/generated-images/detail/detail-001.png"
            write_png_header(output, 900, 1200)
            import run_batch
            digest = run_batch.image_file_sha256(output)
            write_json(product_dir / "output/image-slot-results/detail-001.json", {
                "product_id": product_dir.name,
                "slot": "detail-001",
                "output_path": str(output.resolve()),
                "status": "PASS",
                "attempt": 1,
                "sha256": digest,
                "dimensions": {"width": 900, "height": 1200},
                "hard_failures": [],
                "checked_at": "2026-08-05T00:00:00+08:00",
                "generation_source": "built_in_image_tool",
                "designer_prompt_followed": True,
                "local_script_generation": False,
            })

            result = validate_image_slot_result(product_dir, slot_item, 1)

            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["output_path"], slot_item["output_path"])

    def test_image_slot_worker_reuses_valid_pass_receipt_before_paid_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            product_dir = make_product(Path(directory), 1, 1)
            slot_item = {
                "slot": "detail-001",
                "output_path": "products/P000001/output/generated-images/detail/detail-001.png",
            }
            output = product_dir / "output/generated-images/detail/detail-001.png"
            write_png_header(output, 900, 1200)
            import run_batch
            digest = run_batch.image_file_sha256(output)
            write_json(product_dir / "output/image-slot-results/detail-001.json", {
                "product_id": product_dir.name,
                "slot": "detail-001",
                "output_path": str(output.resolve()),
                "status": "PASS",
                "attempt": 1,
                "sha256": digest,
                "dimensions": {"width": 900, "height": 1200},
                "hard_failures": [],
                "checked_at": "2026-08-05T00:00:00+08:00",
                "generation_source": "built_in_image_tool",
                "designer_prompt_followed": True,
                "local_script_generation": False,
            })

            with patch("run_batch.codex_exec_command", side_effect=AssertionError("must not regenerate")):
                result = run_single_image_slot(product_dir, {}, slot_item, 1)

            self.assertEqual(result["status"], "passed")
            self.assertTrue(result["reused_checkpoint"])

    def test_each_product_accepts_up_to_ten_selected_skus(self):
        with tempfile.TemporaryDirectory() as directory:
            product_dir = make_product(Path(directory), 1, MAX_SELECTED_SKUS_PER_PRODUCT)
            status = queue_product(product_dir, "B-TEST")
            self.assertEqual(status["status"], "QUEUED")
            self.assertTrue(status["task_authorized"])

    def test_authorized_batch_never_creates_an_operator_question(self):
        with tempfile.TemporaryDirectory() as directory:
            product_dir = make_product(Path(directory), 1, 1)
            queue_product(product_dir, "B-AUTO-ACCEPT")
            result = maybe_create_operator_question(
                product_dir,
                "image_plan",
                "SKU对应关系无法确认，参考图缺少",
            )
            self.assertIsNone(result)
            self.assertFalse((product_dir / "input/pending-question.json").exists())

    def test_stopped_product_is_requeued_from_its_saved_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            product_dir = make_product(Path(directory), 1, 2)
            status_path = product_dir / "status.json"
            status = json.loads(status_path.read_text())
            status.update({
                "status": "STOPPED",
                "current_step": "image_generation",
                "completed_steps": [
                    "collect_source", "validate_source", "product_analysis", "category_match",
                    "variant_rules", "measurements", "offer_exists_check", "upload_feasibility",
                    "product_positioning", "ecommerce_design", "russian_copy", "field_completion",
                    "image_plan",
                ],
            })
            write_json(status_path, status)
            queued = queue_product(product_dir, "B-RESUME")
            self.assertEqual(queued["status"], "QUEUED")
            self.assertEqual(queued["next_action"], "image_generation")
            self.assertEqual(queued["batch_id"], "B-RESUME")

    def test_manual_continue_clears_old_retry_limit_and_resumes_failed_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            product_dir = make_product(Path(directory), 1, 2)
            status_path = product_dir / "status.json"
            status = json.loads(status_path.read_text())
            status.update({
                "status": "NEEDS_ATTENTION",
                "current_step": "ecommerce_design",
                "failed_step": "ecommerce_design",
                "next_action": "retry_failed_step",
                "task_authorized": True,
                "retry_count_by_step": {"ecommerce_design": 2},
                "image_slot_retry_count_by_slot": {"detail-001": 8},
                "error_code": "PIPELINE_NEEDS_ATTENTION",
                "error_message": "RuntimeError: old timeout",
                "human_message": "旧错误不应继续显示",
                "attention_required": True,
                "warnings": ["旧版内容兼容失败"],
                "completed_steps": [
                    "collect_source", "validate_source", "product_analysis", "category_match",
                    "variant_rules", "measurements", "offer_exists_check", "upload_feasibility",
                    "product_positioning", "ecommerce_design", "russian_copy",
                ],
            })
            write_json(status_path, status)
            write_json(product_dir / "output/image-regeneration-request.json", {
                "failed_slots": ["detail-001"],
                "attempt": 8,
            })

            queued = queue_product(product_dir, "B-CONTINUE-AFTER-FIX")

            self.assertEqual(queued["status"], "QUEUED")
            self.assertEqual(queued["next_action"], "field_completion")
            self.assertEqual(queued["batch_id"], "B-CONTINUE-AFTER-FIX")
            self.assertEqual(queued["retry_count_by_step"], {})
            self.assertEqual(queued["image_slot_retry_count_by_slot"], {})
            self.assertFalse((product_dir / "output/image-regeneration-request.json").exists())
            self.assertEqual(queued["warnings"], [])
            self.assertEqual(queued["error_code"], "unknown")
            self.assertEqual(queued["error_message"], "unknown")
            self.assertIsNone(queued["human_message"])
            self.assertFalse(queued["attention_required"])
            self.assertIn("field_completion", queued["pending_steps"])

    def test_product_with_eleven_selected_skus_cannot_enter_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            product_dir = make_product(Path(directory), 1, MAX_SELECTED_SKUS_PER_PRODUCT + 1)
            with self.assertRaisesRegex(ValueError, "selected SKU count must be between 1 and 10"):
                queue_product(product_dir, "B-TEST")

    def test_old_image_first_checkpoint_restarts_before_image_planning(self):
        with tempfile.TemporaryDirectory() as directory:
            product_dir = make_product(Path(directory), 1, 2)
            status_path = product_dir / "status.json"
            status = json.loads(status_path.read_text())
            status.update({
                "status": "STOPPED", "api_write_count": 0,
                "completed_steps": [
                    "collect_source", *PHASE_A_STEPS, "product_positioning", "russian_copy",
                    "image_plan", "image_generation", "image_qc",
                ],
            })
            write_json(status_path, status)
            queued = queue_product(product_dir, "B-MIGRATE")
            self.assertEqual(queued["next_action"], "ecommerce_design")
            self.assertNotIn("image_plan", queued["completed_steps"])
            self.assertNotIn("image_generation", queued["completed_steps"])

    def test_manual_mode_stops_with_explicit_upload_state_not_completed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product_dir = make_product(root, 1, 2)
            status_path = product_dir / "status.json"
            status = json.loads(status_path.read_text())
            status.update({
                "product_id": "P000001", "status": "WAITING_MANUAL_REVIEW",
                "current_step": "image_qc", "progress": 94,
                "next_action": "ozon_upload", "api_write_count": 0,
                "ozon": {"upload_status": "not_started", "errors": []},
            })
            write_json(status_path, status)
            marked = mark_manual_upload_ready(product_dir, status)
            self.assertEqual(marked["next_action"], "manual_ozon_upload")
            self.assertEqual(marked["progress"], 95)
            self.assertEqual(marked["completed_at"], "unknown")
            self.assertFalse(marked["task_authorized"])
            self.assertEqual(marked["error_message"], "unknown")

            batch = {
                "batch_id": "B-MANUAL", "status": "RUNNING",
                "started_at": "2026-07-14T00:00:00+00:00",
                "auto_upload": False,
                "products": [{"product_id": "P000001"}],
            }
            report = finalize_batch(root, batch)
            saved = json.loads((root / "batches/B-MANUAL/batch.json").read_text())
            self.assertEqual(report["status"], "AWAITING_MANUAL_UPLOAD")
            self.assertEqual(saved["status"], "AWAITING_MANUAL_UPLOAD")
            self.assertEqual(report["waiting_manual_review_count"], 1)
            self.assertEqual(report["success_count"], 0)
            self.assertEqual(saved["progress"], 95)
            self.assertEqual(report["api_write_count"], 0)
            self.assertFalse(report["inventory_api_called"])

    def test_manual_recovery_clears_stale_failure_before_new_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            product_dir = make_product(Path(directory), 1, 1)
            status = json.loads((product_dir / "status.json").read_text())
            status.update({
                "status": "NEEDS_ATTENTION", "current_step": "ozon_upload",
                "error_code": "PIPELINE_NEEDS_ATTENTION", "error_message": "old upload error",
                "failed_step": "ozon_upload", "task_authorized": True,
                "api_write_count": 0, "ozon": {"upload_status": "failed", "errors": ["old"]},
            })
            marked = mark_manual_upload_ready(product_dir, status)
            self.assertEqual(marked["status"], "WAITING_MANUAL_REVIEW")
            self.assertEqual(marked["current_step"], "manual_ozon_upload")
            self.assertEqual(marked["error_code"], "unknown")
            self.assertEqual(marked["error_message"], "unknown")
            self.assertEqual(marked["failed_step"], "unknown")
            self.assertFalse(marked["task_authorized"])
            self.assertEqual(marked["ozon"]["upload_status"], "not_started")
            self.assertEqual(marked["ozon"]["errors"], [])
            self.assertIn("ozon_upload", marked["pending_steps"])

    def test_upload_image_precheck_rewinds_to_image_plan_without_manual_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            product_dir = make_product(Path(directory), 1, 1)
            status_path = product_dir / "status.json"
            status = json.loads(status_path.read_text())
            status.update({
                "status": "NEEDS_ATTENTION",
                "current_step": "ozon_upload",
                "failed_step": "ozon_upload",
                "next_action": "retry_failed_step",
                "task_authorized": True,
                "api_write_count": 0,
                "error_code": "PIPELINE_NEEDS_ATTENTION",
                "error_message": "RuntimeError: 主SKU颜色图仍不满足上传条件，已在调用Ozon前停止",
                "human_message": "旧错误不应继续显示",
                "attention_required": True,
                "retry_count_by_step": {"ozon_upload": 1, "image_generation": 1},
                "completed_steps": [
                    "collect_source", *PHASE_A_STEPS, "product_positioning",
                    "ecommerce_design", "russian_copy", "field_completion",
                    "image_plan", "image_generation", "image_qc",
                ],
            })
            write_json(status_path, status)
            write_json(product_dir / "output/pipeline-cache.json", {
                "steps": {
                    "field_completion": {"ok": True},
                    "image_plan": {"ok": True},
                    "image_generation": {"ok": True},
                    "image_qc": {"ok": True},
                    "ozon_upload": {"ok": True},
                },
            })
            write_json(product_dir / "output/image-regeneration-request.json", {
                "failed_slots": ["main-1"],
            })

            repaired = route_upload_image_precheck_back_to_image_plan(
                product_dir,
                status,
                "RuntimeError: 主SKU颜色图仍不满足上传条件，已在调用Ozon前停止",
            )

            self.assertEqual(repaired["status"], "PROCESSING")
            self.assertEqual(repaired["current_step"], "image_plan")
            self.assertEqual(repaired["next_action"], "image_plan")
            self.assertEqual(repaired["failed_step"], "unknown")
            self.assertFalse(repaired["attention_required"])
            self.assertEqual(repaired["api_write_count"], 0)
            self.assertNotIn("image_plan", repaired["completed_steps"])
            self.assertNotIn("image_generation", repaired["completed_steps"])
            self.assertNotIn("image_qc", repaired["completed_steps"])
            self.assertIn("field_completion", repaired["completed_steps"])
            self.assertIn("image_plan", repaired["pending_steps"])
            self.assertNotIn("ozon_upload", repaired["retry_count_by_step"])
            self.assertNotIn("image_generation", repaired["retry_count_by_step"])
            self.assertFalse((product_dir / "output/image-regeneration-request.json").exists())
            cache = json.loads((product_dir / "output/pipeline-cache.json").read_text())
            self.assertIn("field_completion", cache["steps"])
            self.assertNotIn("image_plan", cache["steps"])
            self.assertNotIn("image_generation", cache["steps"])
            self.assertNotIn("image_qc", cache["steps"])
            self.assertNotIn("ozon_upload", cache["steps"])

    def test_confirmed_manual_upload_transitions_out_of_terminal_review_state(self):
        with tempfile.TemporaryDirectory() as directory:
            product_dir = make_product(Path(directory), 1, 1)
            status_path = product_dir / "status.json"
            status = json.loads(status_path.read_text())
            status.update({
                "status": "WAITING_MANUAL_REVIEW",
                "current_step": "manual_ozon_upload",
                "next_action": "ozon_upload",
                "progress": 95,
                "task_authorized": True,
                "api_write_count": 0,
                "upload_priority_state": "queued",
                "ozon": {"upload_status": "not_started", "errors": []},
            })
            write_json(status_path, status)

            processing = transition_to_processing(product_dir)

            self.assertEqual(processing["status"], "PROCESSING")
            self.assertEqual(processing["current_step"], "ozon_upload")
            self.assertEqual(processing["next_action"], "ozon_upload")
            self.assertEqual(processing["upload_priority_state"], "running")
            self.assertTrue(processing["task_authorized"])

    def test_failed_store_image_repair_transitions_even_after_first_api_write(self):
        with tempfile.TemporaryDirectory() as directory:
            product_dir = make_product(Path(directory), 1, 1)
            status_path = product_dir / "status.json"
            status = json.loads(status_path.read_text())
            status.update({
                "status": "WAITING_MANUAL_REVIEW",
                "current_step": "field_completion",
                "next_action": "ozon_upload",
                "progress": 95,
                "task_authorized": True,
                "api_write_count": 1,
                "target_store_ids_for_run": ["shop-a"],
                "ozon": {"upload_status": "failed", "errors": [{"reason": "all_image_failed"}]},
            })
            write_json(status_path, status)

            processing = transition_to_processing(product_dir)

            self.assertEqual(processing["status"], "PROCESSING")
            self.assertEqual(processing["current_step"], "ozon_upload")
            self.assertEqual(processing["target_store_ids_for_run"], ["shop-a"])

    def test_upload_validation_reads_canonical_publications_without_legacy_json(self):
        with tempfile.TemporaryDirectory() as directory:
            product_dir = make_product(Path(directory), 1, 1)
            log_path = product_dir / "logs/ozon-upload.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            canonical = {
                "stores": {
                    "store-a": {
                        "selected": True,
                        "status": "HANDED_OFF_TO_OZON",
                    },
                },
            }
            valid_tags = [
                "#канистра", "#топливо", "#металл", "#сталь", "#гараж", "#мастерская",
                "#техника", "#емкость", "#хранение", "#переноска", "#ручка", "#крышка",
                "#автотовары", "#поездка", "#дача", "#запас", "#закрытая", "#квадратная",
                "#прочная", "#большая", "#удобная", "#практичная", "#покупка", "#товар",
                "#дом", "#работа", "#сервис", "#резерв", "#комплект", "#выбор",
            ]
            write_json(product_dir / "output/title-ru.json", {"title_ru": "Канистра металлическая"})
            write_json(product_dir / "output/description-ru.json", {"description_ru": "Описание"})
            write_json(product_dir / "output/keywords-ru.json", {"primary_keywords": ["канистра"], "secondary_keywords": []})
            write_json(product_dir / "output/ozon-draft.json", {"skus": [{"source_sku_id": "0"}]})

            def fake_run_checked(command, *_args, **_kwargs):
                if "ozon-field-completion/cli.py" in command:
                    write_json(product_dir / "output/ozon-upload-config.json", {"shop_name": "default"})
                    write_json(product_dir / "output/ozon-tags.json", {"tags": valid_tags})
                    write_json(product_dir / "output/ozon-attributes-final.json", {"attributes": []})
                    write_json(product_dir / "output/rich-content.json", {"items": []})

            with patch.dict(os.environ, {"APP_MODE": "production"}), \
                 patch("run_batch.run_checked", side_effect=fake_run_checked) as run_checked, \
                 patch("run_batch.load_publications", return_value=canonical):
                completed = run_local_step(
                    product_dir,
                    "ozon_upload",
                    {"app_mode": "production"},
                    log_path,
                )

            self.assertTrue(completed)
            self.assertEqual(run_checked.call_count, 2)
            self.assertIn("ozon-field-completion/cli.py", run_checked.call_args_list[0].args[0])
            self.assertIn("scripts/multi_store_upload.py", run_checked.call_args_list[1].args[0])
            self.assertFalse((product_dir / "output/store-publications.json").exists())

    def test_ozon_upload_refreshes_ecommerce_projection_when_visible_copy_has_chinese(self):
        with tempfile.TemporaryDirectory() as directory:
            product_dir = make_product(Path(directory), 1, 1)
            log_path = product_dir / "logs/ozon-upload.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            canonical = {
                "stores": {
                    "store-a": {
                        "selected": True,
                        "status": "HANDED_OFF_TO_OZON",
                    },
                },
            }
            valid_tags = ["#канистра", "#топливо", "#гараж"]
            write_json(product_dir / "output/ozon-ecommerce-design.json", {"product_id": product_dir.name})
            write_json(product_dir / "output/title-ru.json", {"title_ru": "Канистра металлическая"})
            write_json(product_dir / "output/description-ru.json", {
                "description_ru": "Материал в текущей采集资料 не подтвержден."
            })
            write_json(product_dir / "output/copy-ru.json", {
                "title_ru": "Канистра металлическая",
                "description_ru": "Материал в текущей采集资料 не подтвержден.",
            })
            write_json(product_dir / "output/keywords-ru.json", {"primary_keywords": ["канистра"], "secondary_keywords": []})
            write_json(product_dir / "output/ozon-tags.json", {"tags": valid_tags})
            write_json(product_dir / "output/rich-content.json", {"items": []})
            write_json(product_dir / "output/ozon-upload-config.json", {"shop_name": "default"})
            write_json(product_dir / "output/ozon-draft.json", {
                "title": "Канистра металлическая",
                "description": "Материал в текущей采集资料 не подтвержден.",
                "keywords": ["канистра"],
                "skus": [{"source_sku_id": "0"}],
            })

            def fake_run_checked(command, *_args, **_kwargs):
                if "scripts/ozon_ecommerce_designer_contract.py" in command:
                    write_json(product_dir / "output/description-ru.json", {
                        "description_ru": "Материал в текущих данных не подтвержден."
                    })
                    write_json(product_dir / "output/copy-ru.json", {
                        "title_ru": "Канистра металлическая",
                        "description_ru": "Материал в текущих данных не подтвержден.",
                    })
                    draft = json.loads((product_dir / "output/ozon-draft.json").read_text())
                    draft["description"] = "Материал в текущих данных не подтвержден."
                    write_json(product_dir / "output/ozon-draft.json", draft)
                if "ozon-field-completion/cli.py" in command:
                    write_json(product_dir / "output/ozon-upload-config.json", {"shop_name": "default"})
                    write_json(product_dir / "output/ozon-tags.json", {"tags": valid_tags})
                    write_json(product_dir / "output/ozon-attributes-final.json", {"attributes": []})
                    write_json(product_dir / "output/rich-content.json", {"items": []})

            with patch.dict(os.environ, {"APP_MODE": "production"}), \
                 patch("run_batch.run_checked", side_effect=fake_run_checked) as run_checked, \
                 patch("run_batch.load_publications", return_value=canonical):
                completed = run_local_step(
                    product_dir,
                    "ozon_upload",
                    {"app_mode": "production"},
                    log_path,
                )

            self.assertTrue(completed)
            commands = [call.args[0] for call in run_checked.call_args_list]
            self.assertIn("scripts/ozon_ecommerce_designer_contract.py", commands[0])
            self.assertIn("--repair-buyer-copy", commands[0])
            self.assertIn("ozon-field-completion/cli.py", commands[1])
            self.assertIn("scripts/multi_store_upload.py", commands[2])

    def test_batch_result_uses_per_store_handoff_when_root_result_is_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            product_dir = make_product(Path(directory), 1, 1)
            status = json.loads((product_dir / "status.json").read_text())
            status.update({
                "status": "HANDED_OFF_TO_OZON", "api_write_count": 1,
                "ozon": {"errors": []},
            })
            write_json(product_dir / "status.json", status)
            write_json(product_dir / "output/store-publications.json", {
                "stores": {
                    "store-a": {
                        "selected": True, "status": "HANDED_OFF_TO_OZON",
                        "api_write_count": 1,
                        "sku_publications": [{
                            "sku_id": "0", "action": "CREATE",
                            "offer_id": "OFFER-A", "task_id": "TASK-A",
                        }],
                    },
                },
            })

            row = result_row(product_dir)

            self.assertEqual(row["status"], "HANDED_OFF_TO_OZON")
            self.assertEqual(row["upload_action"], "CREATE")
            self.assertEqual(row["offer_ids"], ["OFFER-A"])
            self.assertEqual(row["task_ids"], ["TASK-A"])
            self.assertEqual(row["api_write_count"], 1)

    def test_manual_multi_product_batch_aggregates_waiting_and_failed_without_false_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            waiting = make_product(root, 1, 1)
            failed = make_product(root, 2, 1)
            waiting_status = json.loads((waiting / "status.json").read_text())
            waiting_status.update({
                "status": "WAITING_MANUAL_REVIEW", "progress": 95,
                "api_write_count": 0, "ozon": {"upload_status": "not_started"},
            })
            failed_status = json.loads((failed / "status.json").read_text())
            failed_status.update({
                "status": "NEEDS_ATTENTION", "progress": 42,
                "api_write_count": 0, "error_message": "图片损坏",
                "ozon": {"upload_status": "not_started"},
            })
            write_json(waiting / "status.json", waiting_status)
            write_json(failed / "status.json", failed_status)
            batch = {
                "batch_id": "B-MIXED-MANUAL", "status": "RUNNING",
                "started_at": "2026-07-16T00:00:00+00:00", "auto_upload": False,
                "products": [{"product_id": waiting.name}, {"product_id": failed.name}],
            }
            report = finalize_batch(root, batch)
            self.assertEqual(report["status"], "AWAITING_MANUAL_UPLOAD")
            self.assertEqual(report["waiting_manual_review_count"], 1)
            self.assertEqual(report["failed_count"], 1)
            self.assertEqual(report["success_count"], 0)
            self.assertEqual(report["submitted_count"], 0)


if __name__ == "__main__":
    unittest.main()
