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

from scripts.pipeline_runtime import complete_step, create_batch, load_json
from scripts.pipeline_runtime import queue_product
from scripts.run_batch import run_one_step, run_registered_process


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "collector/local-ingest/app.py"
SPEC = importlib.util.spec_from_file_location("collection_inbox_app", APP_PATH)
inbox_app = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inbox_app)


class FakeRequest:
    async def json(self):
        return {"confirm_product_id": "P000001"}


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def make_product(root: Path, number: int, sku_count: int, status: str = "COLLECTED") -> Path:
    product_id = f"P{number:06d}"
    product_dir = root / "products" / product_id
    write_json(product_dir / "input/source.json", {
        "product_id": product_id,
        "title_cn": f"真实结构商品{number}",
        "source_url": f"https://detail.1688.com/offer/{number}.html",
        "captured_at": "2026-07-11T10:00:00+08:00",
        "skus": [{"sku_id": f"{number}-{index}"} for index in range(sku_count)],
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
            self.assertIn("禁止展开读取rules_snapshot", captured["prompt"])
            self.assertNotIn("image_generation必须", captured["prompt"])

    def test_russian_copy_artifact_fast_path_advances_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product_dir = make_product(root, 1, 1)
            queue_product(product_dir, "B-COPY-FAST")
            for step in (
                "validate_source", "product_analysis", "category_match", "variant_rules",
                "measurements", "offer_exists_check", "upload_feasibility", "product_positioning",
            ):
                complete_step(product_dir, step)

            captured = {}

            def create_artifacts(*args, **kwargs):
                captured["prompt"] = args[0][-1]
                for name in (
                    "copy-ru.json", "marketplace-content-input.json", "keyword-research-ru.json",
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
            self.assertEqual(status["next_action"], "style_selector")
            self.assertIn("只完成russian_copy", captured["prompt"])
            self.assertNotIn("image_generation必须", captured["prompt"])

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
            product_dir = make_product(root, 9, 2, status="FAILED_HARD_BLOCKER")
            status = load_json(product_dir / "status.json")
            status["api_write_count"] = 0
            write_json(product_dir / "status.json", status)
            batch = create_batch(root, ["P000009"])
            self.assertEqual(batch["product_count"], 1)

    def test_only_latest_capture_version_enters_current_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = make_product(root, 1, 1)
            new = make_product(root, 2, 2)
            old_source = load_json(old / "input/source.json")
            new_source = load_json(new / "input/source.json")
            new_source["source_url"] = old_source["source_url"]
            write_json(new / "input/source.json", new_source)
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
        self.assertEqual(settings["app_mode"], "production")
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
            self.assertEqual(status["status"], "FAILED_HARD_BLOCKER")
            self.assertEqual(status["retry_count_by_step"]["validate_source"], 2)

    def test_active_status_schema_excludes_manual_review_states(self):
        schema = load_json(ROOT / "templates/status.schema.json")
        statuses = set(schema["properties"]["status"]["enum"])
        self.assertTrue({
            "COLLECTED", "QUEUED", "PROCESSING", "CATEGORY_MATCHED", "CONTENT_GENERATED",
            "IMAGES_GENERATED", "PRICED", "OZON_READY", "UPLOADING", "UPLOADED",
            "OZON_MODERATION", "FAILED_HARD_BLOCKER",
        }.issubset(statuses))
        self.assertTrue({"WAITING_REVIEW", "APPROVED", "REJECTED"}.isdisjoint(statuses))

    def test_workbench_is_the_only_product_inbox_ui(self):
        static_dir = ROOT / "collector/local-ingest/static"
        self.assertFalse((static_dir / "inbox.html").exists())
        self.assertFalse((static_dir / "inbox.css").exists())
        self.assertFalse((static_dir / "inbox.js").exists())
        html = (static_dir / "workbench.html").read_text(encoding="utf-8")
        script = (static_dir / "workbench.js").read_text(encoding="utf-8")
        self.assertIn("我的采集箱", html)
        self.assertIn("运行可处理商品", script)
        self.assertIn("彻底删除", script)


if __name__ == "__main__":
    unittest.main()
