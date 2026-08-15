import asyncio
import importlib.util
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from jsonschema import Draft202012Validator

from scripts.pipeline_runtime import PIPELINE_STEPS, complete_step, create_batch, load_json
from scripts.pipeline_runtime import queue_product
from scripts.production_input_guard import write_source_manifest
from scripts.run_batch import run_one_step, run_registered_process
from scripts.ozon_ecommerce_designer_contract import validate_design


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "collector/local-ingest/app.py"
SPEC = importlib.util.spec_from_file_location("collection_inbox_app", APP_PATH)
inbox_app = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inbox_app)


class FakeRequest:
    async def json(self):
        return {"confirm_product_id": "P000001"}


class PayloadRequest:
    def __init__(self, payload: dict):
        self.payload = payload

    async def json(self):
        return self.payload


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def make_product(root: Path, number: int, sku_count: int, status: str = "COLLECTED") -> Path:
    product_id = f"P{number:06d}"
    collection_id = f"COL-TEST-{number:08d}"
    product_dir = root / "products" / product_id
    write_json(product_dir / "input/source.json", {
        "product_id": product_id,
        "collection_id": collection_id,
        "source_kind": "workbench_collection",
        "source_path": f"products/{product_id}/input/source.json",
        "title_cn": f"真实结构商品{number}",
        "source_url": f"https://detail.1688.com/offer/{number}.html",
        "captured_at": "2026-07-11T10:00:00+08:00",
        "collected_at": "2026-07-11T10:00:00+08:00",
        "raw_capture_file": f"products/{product_id}/input/raw-snapshot.json",
        "main_images": [],
        "detail_images": [],
        "skus": [{"sku_id": f"{number}-{index}"} for index in range(sku_count)],
    })
    write_json(product_dir / "input/raw-snapshot.json", {
        "product_id": product_id,
        "collection_id": collection_id,
        "source_kind": "workbench_collection",
    })
    write_json(product_dir / "input/category-selection.json", {
        "product_id": product_id,
        "collection_id": collection_id,
        "category_id": 17027905,
        "type_id": 92014,
        "category_path": ["测试类目"],
        "selection_status": "confirmed",
    })
    write_json(product_dir / "status.json", {
        "status": status,
        "current_step": "collect_source",
        "progress": 100,
        "started_at": "2026-07-11T10:00:00+08:00",
        "completed_at": "2026-07-11T10:01:00+08:00",
        "error_code": "unknown",
        "error_message": "unknown",
        "warnings": [],
        "history": [],
        "steps": [],
        "ozon": {"errors": []},
    })
    image = product_dir / "input/main-images/main-001.jpg"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"image")
    write_source_manifest(product_dir)
    return product_dir


class CollectionInboxTest(unittest.TestCase):
    def test_completed_artifact_stops_long_running_child_immediately(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product_dir = root / "products/P000321"
            product_dir.mkdir(parents=True)
            marker = root / "artifact-ready.txt"
            worker = root / "worker.json"
            command = [
                sys.executable,
                "-c",
                f"from pathlib import Path; import time; Path({str(marker)!r}).write_text('ready'); time.sleep(10)",
            ]
            started = time.monotonic()
            with (root / "child.log").open("w", encoding="utf-8") as output, patch(
                "scripts.run_batch.product_worker_path", return_value=worker
            ):
                process = run_registered_process(
                    command,
                    product_dir,
                    output,
                    timeout_seconds=5,
                    completion_check=marker.is_file,
                    completion_poll_seconds=0.05,
                )
            self.assertLess(time.monotonic() - started, 2)
            self.assertTrue(process.artifact_completed_early)
            self.assertFalse(worker.exists())

    def test_task_status_overlays_live_product_step_without_waiting_for_batch_sync(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product_dir = root / "products/P000001"
            write_json(product_dir / "status.json", {
                "status": "PROCESSING",
                "current_step": "product_analysis",
                "progress": 12,
                "started_at": "2026-07-12T10:00:00+08:00",
                "completed_at": "unknown",
                "warnings": [],
                "error_message": "unknown",
            })
            batch = {
                "products": [{"product_id": "P000001", "status": "PROCESSING", "current_step": "validate_source"}],
                "progress": 6,
            }
            live = inbox_app.overlay_live_batch_status(batch, root / "products")
            self.assertEqual(live["products"][0]["current_step"], "product_analysis")
            self.assertEqual(live["products"][0]["progress"], 12)
            self.assertEqual(live["progress"], 12)

    def test_live_batch_counts_unfinished_products(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            statuses = {
                "P000001": {"status": "HANDED_OFF_TO_OZON", "current_step": "ozon_upload", "progress": 100},
                "P000002": {"status": "STOPPED", "current_step": "image_generation", "progress": 85},
                "P000003": {"status": "CONTENT_GENERATED", "current_step": "image_plan", "progress": 81},
            }
            for product_id, status in statuses.items():
                write_json(root / "products" / product_id / "status.json", {
                    **status,
                    "warnings": [],
                    "error_message": "unknown",
                })
            batch = {
                "status": "COMPLETED",
                "product_count": 3,
                "success_count": 3,
                "failed_count": 0,
                "products": [{"product_id": product_id, "status": "HANDED_OFF_TO_OZON"} for product_id in statuses],
            }

            live = inbox_app.overlay_live_batch_status(batch, root / "products")

            self.assertEqual(live["success_count"], 0)
            self.assertEqual(live["failed_count"], 0)
            self.assertEqual(live["pending_remote_count"], 1)
            self.assertEqual(live["incomplete_count"], 2)
            self.assertEqual(live["status"], "INCOMPLETE")
            self.assertEqual(live["display_status"], "未完成")

    def test_live_batch_labels_remote_handoff_as_waiting_for_ozon(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for product_id in ("P000001", "P000002"):
                write_json(root / "products" / product_id / "status.json", {
                    "status": "PENDING_REMOTE",
                    "current_step": "ozon_upload",
                    "progress": 99,
                    "warnings": [],
                    "error_message": "unknown",
                })
            batch = {
                "products": [
                    {"product_id": "P000001", "status": "PENDING_REMOTE"},
                    {"product_id": "P000002", "status": "PENDING_REMOTE"},
                ],
            }

            live = inbox_app.overlay_live_batch_status(batch, root / "products")

            self.assertEqual(live["pending_remote_count"], 2)
            self.assertEqual(live["incomplete_count"], 0)
            self.assertEqual(live["status"], "INCOMPLETE")
            self.assertEqual(live["display_status"], "等待Ozon结果")

    def test_task_status_last_result_uses_live_product_status(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product_dir = root / "products/P000001"
            write_json(product_dir / "status.json", {
                "status": "CONTENT_GENERATED",
                "current_step": "field_completion",
                "progress": 75,
                "started_at": "2026-07-12T10:00:00+08:00",
                "completed_at": "unknown",
                "warnings": [],
                "error_message": "unknown",
            })
            batch_id = "B-STALE"
            stale_result = {
                "batch_id": batch_id,
                "status": "COMPLETED_WITH_ERRORS",
                "product_count": 1,
                "success_count": 0,
                "failed_count": 1,
                "products": [{
                    "product_id": "P000001",
                    "status": "NEEDS_ATTENTION",
                    "current_step": "field_completion",
                    "warnings": ["old failure"],
                }],
            }
            write_json(root / "batch-result.json", stale_result)
            write_json(root / "batches" / batch_id / "batch.json", stale_result)
            with (
                patch.object(inbox_app, "ROOT", root),
                patch.object(inbox_app, "PRODUCTS_DIR", root / "products"),
                patch.object(inbox_app, "CURRENT_BATCH_PATH", root / "logs/current-batch.json"),
                patch.object(inbox_app, "BATCH_PID_PATH", root / "logs/batch-runner.pid"),
            ):
                status = inbox_app.get_batch_status()

        result = status["last_result"]
        self.assertEqual(result["products"][0]["status"], "CONTENT_GENERATED")
        self.assertEqual(result["failed_count"], 0)
        self.assertEqual(result["incomplete_count"], 1)
        self.assertEqual(result["status"], "INCOMPLETE")

    def test_product_analysis_artifact_fast_path_advances_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product_dir = make_product(root, 1, 1)
            queue_product(product_dir, "B-FAST")
            complete_step(product_dir, "validate_source")
            analysis = {
                "schema_version": "1.0.0",
                "product_id": "P000001",
                "source_refs": ["products/P000001/input/source.json"],
                "product_type": "测试商品",
                "category": "测试类目",
                "facts": {
                    "title_cn": "真实结构商品1",
                    "category_cn": "测试类目",
                    "brand": "Нет бренда",
                    "materials": ["unknown"],
                    "dimensions": "unknown",
                    "weight": "unknown",
                    "load_capacity": "unknown",
                    "certifications": ["unknown"],
                    "functions": ["unknown"],
                    "package_quantity": {"value": 1},
                    "accessories": ["unknown"],
                    "skus": [{
                        "sku_id": "1-0", "name_cn": "测试SKU", "properties": {},
                        "price_cny": None, "image_refs": [],
                    }],
                },
                "selling_points": [{"text": "真实商品", "evidence": ["input/source.json"]}],
                "inferences": [],
                "unknowns": [{"field": "material", "reason": "来源未提供", "needed_from_human": False}],
                "risks": [],
                "recommendation": {"decision": "continue", "reason": "来源可追溯"},
                "processing": {
                    "step": "product_analysis", "status": "completed",
                    "started_at": "2026-07-12T10:00:00+08:00",
                    "finished_at": "2026-07-12T10:00:01+08:00", "error": "unknown",
                },
            }

            captured = {}

            def create_artifact(*args, **kwargs):
                captured["prompt"] = args[0][-1]
                write_json(product_dir / "output/product-analysis.json", analysis)
                return SimpleNamespace(returncode=-15, artifact_completed_early=True)

            settings = {
                "step_retry_limit": 1,
                "codex_command": "/usr/bin/true",
                "artifact_poll_interval_seconds": 0.05,
            }
            with patch("scripts.run_batch.run_local_step", return_value=False), patch(
                "scripts.run_batch.run_registered_process", side_effect=create_artifact
            ), patch("scripts.run_batch.shared_analysis_cache_store"):
                result = run_one_step(product_dir, settings)
            self.assertEqual(result["outcome"], "completed_from_artifact")
            status = load_json(product_dir / "status.json")
            self.assertIn("product_analysis", status["completed_steps"])
            self.assertEqual(status["next_action"], "category_match")
            # product_analysis 已收口为确定性本地步骤，不再构造 Codex 委托 prompt。
            self.assertEqual(captured["prompt"], "")
            self.assertNotIn("image_generation必须", captured["prompt"])

    def test_russian_copy_artifact_fast_path_advances_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product_dir = make_product(root, 1, 1)
            queue_product(product_dir, "B-COPY-FAST")
            for step in (
                "validate_source", "product_analysis", "category_match", "variant_rules",
                "measurements", "offer_exists_check", "upload_feasibility", "product_positioning",
                "ecommerce_design",
            ):
                complete_step(product_dir, step)

            captured = {}

            def create_artifacts(*args, **kwargs):
                captured["prompt"] = args[0][-1]
                for name in (
                    "copy-ru.json", "ozon-ecommerce-design.json", "keyword-research-ru.json",
                ):
                    write_json(product_dir / "output" / name, {"ready": True})
                return SimpleNamespace(returncode=-15, artifact_completed_early=True)

            settings = {
                "step_retry_limit": 1,
                "codex_command": "/usr/bin/true",
                "artifact_poll_interval_seconds": 0.05,
            }
            with patch("scripts.run_batch.run_local_step", return_value=False), patch(
                "scripts.run_batch.run_registered_process", side_effect=create_artifacts
            ), patch("scripts.run_batch.russian_copy_output_is_complete", return_value=True):
                result = run_one_step(product_dir, settings)
            self.assertEqual(result["outcome"], "completed_from_artifact")
            status = load_json(product_dir / "status.json")
            self.assertIn("russian_copy", status["completed_steps"])
            self.assertEqual(status["next_action"], "field_completion")
            self.assertNotIn("old_content_step", status["pending_steps"])
            # russian_copy 已收口为确定性本地步骤，不再构造 Codex 委托 prompt。
            self.assertEqual(captured["prompt"], "")
            self.assertNotIn("image_generation必须", captured["prompt"])

    def test_attribute_input_hash_mismatch_does_not_rewind_valid_decisions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product_dir = make_product(root, 1, 1)
            write_json(product_dir / "output/attribute-fill-input.json", {
                "input_hash": "new-hash",
                "ozon_attributes": [
                    {"attribute_id": 1, "attribute_name": "Required", "required": True, "allowed_values": []},
                ],
            })
            design = {
                "product_id": "P000001",
                "collection_id": "COL-TEST-00000001",
                "source_kind": "workbench_collection",
                "source_refs": ["products/P000001/input/source.json"],
                "product_understanding": {},
                "listing": {"selling_points": [], "keywords": {}, "hashtags": []},
                "sku_plan": [{"sku_id": "1-0"}],
                "attribute_decisions": {
                    "input_hash": "old-hash",
                    "common_attributes": [
                        {"attribute_id": 1, "decision_status": "filled", "raw_semantic_value": "ok", "ozon_value": "ok"}
                    ],
                    "attributes_by_sku": {},
                },
                "main_images": [],
                "detail_images": [],
                "processing": {},
                "decision_trace": {"steps": [], "compliance_status": "PASS", "violations": []},
            }
            with patch("scripts.ozon_ecommerce_designer_contract.Draft202012Validator") as validator, \
                    patch("scripts.ozon_ecommerce_designer_contract.validate_formal_product_input"), \
                    patch("scripts.ozon_ecommerce_designer_contract.selected_skus", return_value=[{"sku_id": "1-0"}]), \
                    patch("scripts.ozon_ecommerce_designer_contract.DECISION_STEP_ORDER", []), \
                    patch("scripts.ozon_ecommerce_designer_contract.validate_current_product_trace_ref"), \
                    patch("scripts.ozon_ecommerce_designer_contract.validate_hashtag_set", return_value=True), \
                    patch("scripts.ozon_ecommerce_designer_contract.load_sku_image_bindings", return_value={}), \
                    patch("scripts.ozon_ecommerce_designer_contract._allowed_sku_reference", return_value=None), \
                    patch("scripts.ozon_ecommerce_designer_contract.attribute_decision_errors", wraps=validate_design.__globals__["attribute_decision_errors"]):
                validator.return_value.iter_errors.return_value = []
                errors = validate_design(product_dir, design)
            self.assertNotIn("attribute decisions were made for a stale attribute-fill-input hash", errors)
            self.assertIn(
                "attribute decision input_hash differs from the current attribute-fill-input; validating concrete attribute ids and dictionary values instead",
                design["processing"]["validation_warnings"],
            )

    def test_inbox_has_no_product_count_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for number in range(1, 16):
                make_product(root, number, 2)
            with patch.object(inbox_app, "ROOT", root), patch.object(inbox_app, "PRODUCTS_DIR", root / "products"):
                result = inbox_app.list_inbox_products()
            self.assertEqual(result["product_count"], 15)
            self.assertEqual(result["pending_product_count"], 15)
            self.assertEqual(result["pending_sku_count"], 30)
            self.assertEqual(result["max_selected_skus_per_product"], 10)

    def test_batch_freezes_product_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_product(root, 1, 1)
            make_product(root, 2, 3)
            batch = create_batch(root)
            make_product(root, 3, 2)
            persisted = load_json(root / "batches" / batch["batch_id"] / "batch.json")
            self.assertEqual(persisted["product_count"], 2)
            self.assertEqual(persisted["sku_count"], 4)
            self.assertEqual({item["product_id"] for item in persisted["products"]}, {"P000001", "P000002"})

    def test_create_batch_rejects_empty_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "批次没有可处理商品"):
                create_batch(root)

    def test_legacy_failed_status_queues_for_continue(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product_dir = make_product(root, 9, 2, status="NEEDS_ATTENTION")
            status = load_json(product_dir / "status.json")
            status.update({
                "api_write_count": 0,
                "completed_steps": ["collect_source", "validate_source", "product_analysis"],
                "pending_steps": ["category_match", "variant_rules"],
                "next_action": "retry_failed_step",
                "failed_step": "category_match",
            })
            write_json(product_dir / "status.json", status)
            batch = create_batch(root, ["P000009"])
            queued = queue_product(product_dir, batch["batch_id"])
            self.assertEqual(queued["status"], "QUEUED")
            self.assertEqual(queued["next_action"], "category_match")

    def test_controlled_single_product_batch_can_resume_a_nonterminal_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product_dir = make_product(root, 9, 2, status="IMAGES_GENERATED")
            status = load_json(product_dir / "status.json")
            status["completed_steps"] = ["collect_source", "validate_source"]
            write_json(product_dir / "status.json", status)
            batch = create_batch(root, ["P000009"])
            self.assertEqual(batch["product_count"], 1)
            self.assertEqual(batch["products"][0]["product_id"], "P000009")

    def test_controlled_single_product_batch_can_resume_prewrite_hard_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product_dir = make_product(root, 9, 2, status="NEEDS_ATTENTION")
            status = load_json(product_dir / "status.json")
            status["api_write_count"] = 0
            write_json(product_dir / "status.json", status)
            batch = create_batch(root, ["P000009"])
            self.assertEqual(batch["product_count"], 1)

    def test_waiting_manual_review_can_enter_explicit_upload_batch_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product_dir = make_product(root, 9, 2, status="WAITING_MANUAL_REVIEW")
            status = load_json(product_dir / "status.json")
            status.update({
                "api_write_count": 0,
                "ozon": {"upload_status": "not_started"},
                "completed_steps": ["collect_source", *PIPELINE_STEPS[:-1]],
                "pending_steps": ["ozon_upload"],
                "next_action": "manual_ozon_upload",
            })
            write_json(product_dir / "status.json", status)
            batch = create_batch(
                root, ["P000009"], target_store_ids=["shop-a"], auto_upload=True,
            )
            queued = queue_product(product_dir, batch["batch_id"])

            self.assertTrue(batch["auto_upload"])
            self.assertEqual(batch["product_count"], 1)
            self.assertEqual(queued["status"], "QUEUED")
            self.assertEqual(queued["next_action"], "ozon_upload")
            self.assertTrue(queued["task_authorized"])

            with self.assertRaisesRegex(ValueError, "already terminal"):
                create_batch(root, ["P000009"], target_store_ids=["shop-a"], auto_upload=False)

    def test_waiting_manual_review_upload_batch_does_not_depend_on_cached_image_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product_dir = make_product(root, 9, 2, status="WAITING_MANUAL_REVIEW")
            status = load_json(product_dir / "status.json")
            status.update({"api_write_count": 0, "ozon": {"upload_status": "not_started"}})
            write_json(product_dir / "status.json", status)
            batch = create_batch(root, ["P000009"], target_store_ids=["shop-a"], auto_upload=True)

            self.assertTrue(batch["auto_upload"])
            self.assertEqual(batch["product_count"], 1)
            self.assertEqual(batch["products"][0]["product_id"], "P000009")

    def test_failed_remote_store_retry_can_enter_explicit_upload_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product_dir = make_product(root, 9, 2, status="NEEDS_ATTENTION")
            status = load_json(product_dir / "status.json")
            status.update({
                "api_write_count": 1,
                "ozon": {"upload_status": "failed"},
                "next_action": "ozon_upload",
                "target_store_ids_for_run": ["shop-a"],
                "task_authorized": True,
            })
            write_json(product_dir / "status.json", status)

            with self.assertRaisesRegex(ValueError, "already terminal"):
                create_batch(root, ["P000009"], target_store_ids=["shop-a"], auto_upload=True)

            batch = create_batch(
                root, ["P000009"], target_store_ids=["shop-a"],
                auto_upload=True, allow_terminal_store_retry=True,
            )

            self.assertEqual(batch["product_count"], 1)
            self.assertEqual(batch["products"][0]["target_store_ids"], ["shop-a"])

    def test_workbench_batch_create_treats_waiting_manual_review_as_confirmed_upload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product_dir = make_product(root, 14, 2, status="WAITING_MANUAL_REVIEW")
            status = load_json(product_dir / "status.json")
            status.update({
                "api_write_count": 0,
                "ozon": {"upload_status": "not_started"},
                "completed_steps": ["collect_source", *PIPELINE_STEPS[:-1]],
                "pending_steps": ["ozon_upload"],
                "next_action": "manual_ozon_upload",
            })
            write_json(product_dir / "status.json", status)
            queue_path = root / "logs/workbench-run-queue.json"
            current_path = root / "logs/current-batch.json"
            pid_path = root / "logs/batch-runner.pid"
            safe_stop_path = root / "logs/safe-stop-request.json"

            with (
                patch.object(inbox_app, "ROOT", root),
                patch.object(inbox_app, "PRODUCTS_DIR", root / "products"),
                patch.object(inbox_app, "WORKBENCH_RUN_QUEUE_PATH", queue_path),
                patch.object(inbox_app, "CURRENT_BATCH_PATH", current_path),
                patch.object(inbox_app, "BATCH_PID_PATH", pid_path),
                patch.object(inbox_app, "SAFE_STOP_REQUEST_PATH", safe_stop_path),
                patch.object(inbox_app, "validate_target_stores", return_value=["shop-a"]),
                patch.object(inbox_app, "connected_store_ids", return_value=["shop-a"]),
                patch.object(inbox_app, "workbench_settings", return_value={"auto_mode_enabled": False}),
                patch.object(inbox_app, "running_batch_pid", return_value=None),
                patch.object(inbox_app, "launch_batch_process", side_effect=lambda batch: {"pid": 123, "batch_id": batch["batch_id"]}),
                patch.object(inbox_app, "ensure_batch_dispatcher"),
            ):
                result = asyncio.run(inbox_app.create_workbench_batch(PayloadRequest({
                    "product_ids": ["P000014"],
                    "store_ids": ["shop-a"],
                })))

            updated = load_json(product_dir / "status.json")
            saved_batch = load_json(next((root / "batches").glob("B-*/batch.json")))
            self.assertTrue(result["auto_upload"])
            self.assertTrue(result["priority_upload"])
            self.assertEqual(updated["next_action"], "ozon_upload")
            self.assertTrue(updated["task_authorized"])
            self.assertEqual(saved_batch["execution_priority"], "manual_upload")

    def test_workbench_batch_create_rejects_mixed_upload_ready_and_unfinished_products(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            waiting = make_product(root, 14, 2, status="WAITING_MANUAL_REVIEW")
            status = load_json(waiting / "status.json")
            status.update({"api_write_count": 0, "ozon": {"upload_status": "not_started"}})
            write_json(waiting / "status.json", status)
            make_product(root, 15, 2, status="COLLECTED")

            with (
                patch.object(inbox_app, "ROOT", root),
                patch.object(inbox_app, "PRODUCTS_DIR", root / "products"),
                patch.object(inbox_app, "validate_target_stores", return_value=["shop-a"]),
                patch.object(inbox_app, "workbench_settings", return_value={"auto_mode_enabled": False}),
                patch.object(inbox_app, "running_batch_pid", return_value=None),
            ):
                with self.assertRaisesRegex(Exception, "不能和还在生成阶段"):
                    asyncio.run(inbox_app.create_workbench_batch(PayloadRequest({
                        "product_ids": ["P000014", "P000015"],
                        "store_ids": ["shop-a"],
                    })))

    def test_only_latest_capture_version_enters_current_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = make_product(root, 1, 1)
            new = make_product(root, 2, 2)
            old_source = load_json(old / "input/source.json")
            new_source = load_json(new / "input/source.json")
            new_source["source_url"] = old_source["source_url"]
            write_json(new / "input/source.json", new_source)
            write_source_manifest(new)
            batch = create_batch(root)
            self.assertEqual(batch["product_count"], 1)
            self.assertEqual(batch["products"][0]["product_id"], "P000002")

    def test_run_endpoint_starts_frozen_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_product(root, 1, 1)
            make_product(root, 2, 2)
            fake_process = SimpleNamespace(pid=43210)
            with (
                patch.object(inbox_app, "ROOT", root),
                patch.object(inbox_app, "PRODUCTS_DIR", root / "products"),
                patch.object(inbox_app, "BATCH_PID_PATH", root / "logs/batch-runner.pid"),
                patch.object(inbox_app, "BATCH_LOG_PATH", root / "logs/batch-runner.log"),
                patch.object(inbox_app, "CURRENT_BATCH_PATH", root / "logs/current-batch.json"),
                patch.object(inbox_app, "running_batch_pid", return_value=None),
                patch.object(inbox_app, "saved_target_store_candidates", return_value=["shop-a"]),
                patch.object(inbox_app, "validate_target_stores", side_effect=lambda stores: list(stores)),
                patch.object(inbox_app.subprocess, "Popen", return_value=fake_process) as popen,
            ):
                result = inbox_app.run_collected_tasks()
            self.assertEqual(result["status"], "started")
            self.assertEqual(result["queued_products"], 2)
            self.assertEqual(result["queued_skus"], 3)
            command = popen.call_args.args[0]
            self.assertIn("--batch-id", command)
            self.assertIn(result["batch_id"], command)
            persisted = load_json(root / "batches" / result["batch_id"] / "batch.json")
            self.assertEqual(persisted["product_count"], 2)
            self.assertTrue(persisted["auto_upload"])
            self.assertEqual(persisted["review_mode"], "automatic")
            self.assertEqual(persisted["target_store_ids"], ["shop-a"])

    def test_batch_and_result_schemas_are_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_product(root, 1, 10)
            batch = create_batch(root)
            schema = load_json(ROOT / "templates/batch.schema.json")
            self.assertEqual(list(Draft202012Validator(schema).iter_errors(batch)), [])

    def test_delete_permanently_removes_product_instead_of_archiving_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_product(root, 1, 1)
            with patch.object(inbox_app, "ROOT", root), patch.object(inbox_app, "PRODUCTS_DIR", root / "products"):
                result = asyncio.run(inbox_app.delete_inbox_product("P000001", FakeRequest()))
            self.assertEqual(result["status"], "deleted")
            self.assertFalse((root / "products/P000001").exists())
            self.assertFalse((root / "logs/deleted-products").exists())
            self.assertEqual(result["ozon_write_api_calls"], 0)

    def test_pipeline_concurrency_is_bounded(self):
        settings = load_json(ROOT / "config/pipeline-settings.json")
        self.assertEqual(settings["analysis_concurrency"], 3)
        self.assertEqual(settings["category_concurrency"], 3)
        self.assertEqual(settings["pricing_concurrency"], 5)
        self.assertEqual(settings["copy_concurrency"], 3)
        self.assertEqual(settings["image_generation_concurrency"], 1)
        self.assertEqual(settings["image_slot_concurrency"], 3)
        self.assertTrue(settings["merge_image_generation_and_qc"])
        self.assertEqual(settings["image_qc_concurrency"], 2)
        self.assertEqual(settings["ozon_write_concurrency"], 1)
        self.assertEqual(settings["max_selected_skus_per_product"], 10)
        self.assertEqual(settings["app_mode"], "development")
        self.assertEqual(settings["timeouts_seconds"]["image_generation"], 600)
        self.assertEqual(settings["timeouts_seconds"]["image_qc"], 600)

    def test_failed_step_retries_once_then_isolated_hard_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product_dir = make_product(root, 1, 1)
            queue_product(product_dir, "B-TEST")
            settings = {
                "step_retry_limit": 1,
                "codex_command": "/Applications/ChatGPT.app/Contents/Resources/codex",
            }
            with patch("scripts.run_batch.run_local_step", return_value=False), patch(
                "scripts.run_batch.run_registered_process", return_value=SimpleNamespace(returncode=1)
            ):
                first = run_one_step(product_dir, settings)
                second = run_one_step(product_dir, settings)
            self.assertEqual(first["outcome"], "retry")
            self.assertIn(second["outcome"], {"failed", "error"})
            status = load_json(product_dir / "status.json")
            self.assertEqual(status["status"], "NEEDS_ATTENTION")
            self.assertEqual(status["retry_count_by_step"]["validate_source"], 2)

    def test_prewrite_ozon_upload_timeout_keeps_running_without_manual_continue(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product_dir = make_product(root, 1, 1)
            status_path = product_dir / "status.json"
            status = queue_product(product_dir, "B-UPLOAD")
            completed = [
                step for step in PIPELINE_STEPS
                if step != "ozon_upload"
            ]
            status.update({
                "status": "IMAGES_GENERATED",
                "current_step": "ozon_upload",
                "next_action": "ozon_upload",
                "completed_steps": completed,
                "pending_steps": ["ozon_upload"],
                "api_write_count": 0,
                "ozon": {"upload_status": "not_started", "task_id": "unknown", "errors": []},
            })
            write_json(status_path, status)

            def timeout_upload(*_args, **_kwargs):
                raise RuntimeError("Step timed out after 120s: multi_store_upload.py")

            with patch("scripts.run_batch.run_local_step", side_effect=timeout_upload):
                result = run_one_step(product_dir, {
                    "step_retry_limit": 1,
                    "codex_command": "/Applications/ChatGPT.app/Contents/Resources/codex",
                    "timeouts_seconds": {"ozon_api": 120},
                })

            saved = load_json(status_path)
            self.assertEqual(result["outcome"], "retry")
            self.assertTrue(result["auto_resume"])
            self.assertEqual(saved["status"], "IMAGES_GENERATED")
            self.assertEqual(saved["next_action"], "ozon_upload")
            self.assertEqual(saved["failed_step"], "unknown")
            self.assertEqual(saved["api_write_count"], 0)
            self.assertFalse(saved["attention_required"])

    def test_active_status_schema_excludes_manual_review_states(self):
        schema = load_json(ROOT / "templates/status.schema.json")
        statuses = set(schema["properties"]["status"]["enum"])
        self.assertTrue({
            "COLLECTED", "QUEUED", "PROCESSING", "CATEGORY_MATCHED", "CONTENT_GENERATED",
            "IMAGES_GENERATED", "PRICED", "WAITING_MANUAL_REVIEW", "UPLOADING", "UPLOADED",
            "OZON_MODERATION", "NEEDS_ATTENTION",
        }.issubset(statuses))
        self.assertTrue({"REJECTED"}.isdisjoint(statuses))

    def test_workbench_is_the_only_product_inbox_ui(self):
        static_dir = ROOT / "collector/local-ingest/static"
        self.assertFalse((static_dir / "inbox.html").exists())
        self.assertFalse((static_dir / "inbox.css").exists())
        self.assertFalse((static_dir / "inbox.js").exists())
        html = (static_dir / "workbench.html").read_text(encoding="utf-8")
        script = (static_dir / "workbench.js").read_text(encoding="utf-8")
        self.assertIn("全部商品", script)
        self.assertIn("共享工作台", html)
        self.assertIn("运行可处理商品", script)
        self.assertIn("彻底删除", script)


if __name__ == "__main__":
    unittest.main()
