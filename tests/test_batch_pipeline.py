import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from pipeline_runtime import (  # noqa: E402
    MAX_SELECTED_SKUS_PER_PRODUCT,
    PHASE_A_STEPS,
    PHASE_B_STEPS,
    collected_products,
    queue_product,
)
from run_batch import finalize_batch, mark_manual_upload_ready  # noqa: E402
from production_input_guard import write_source_manifest  # noqa: E402


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


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
    def test_feasibility_phase_completes_before_content_and_images(self):
        self.assertEqual(PHASE_A_STEPS, [
            "validate_source", "product_analysis", "category_match", "variant_rules",
            "measurements", "offer_exists_check", "upload_feasibility",
        ])
        self.assertEqual(PHASE_B_STEPS[0], "product_positioning")
        self.assertLess(PHASE_B_STEPS.index("marketplace_content"), PHASE_B_STEPS.index("field_completion"))
        self.assertLess(PHASE_B_STEPS.index("field_completion"), PHASE_B_STEPS.index("image_plan"))
        self.assertLess(PHASE_B_STEPS.index("image_plan"), PHASE_B_STEPS.index("image_generation"))
        self.assertLess(PHASE_B_STEPS.index("image_qc"), PHASE_B_STEPS.index("final_upload_check"))
        self.assertLess(PHASE_B_STEPS.index("final_upload_check"), PHASE_B_STEPS.index("ozon_upload"))
        self.assertNotIn("ozon_status", PHASE_B_STEPS)
    def test_batch_product_count_is_not_limited_to_ten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for number in range(1, 13):
                make_product(root, number, 2)
            self.assertEqual(len(collected_products(root)), 12)

    def test_each_product_accepts_up_to_ten_selected_skus(self):
        with tempfile.TemporaryDirectory() as directory:
            product_dir = make_product(Path(directory), 1, MAX_SELECTED_SKUS_PER_PRODUCT)
            status = queue_product(product_dir, "B-TEST")
            self.assertEqual(status["status"], "QUEUED")
            self.assertTrue(status["task_authorized"])

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
                    "product_positioning", "ecommerce_design", "russian_copy", "marketplace_content", "field_completion",
                    "style_selector", "image_plan",
                ],
            })
            write_json(status_path, status)
            queued = queue_product(product_dir, "B-RESUME")
            self.assertEqual(queued["status"], "QUEUED")
            self.assertEqual(queued["next_action"], "image_generation")
            self.assertEqual(queued["batch_id"], "B-RESUME")

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
                    "style_selector", "image_plan", "image_generation", "image_qc",
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
                "product_id": "P000001", "status": "OZON_READY",
                "current_step": "final_upload_check", "progress": 94,
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
                "status": "FAILED_HARD_BLOCKER", "current_step": "ozon_upload",
                "error_code": "PIPELINE_HARD_BLOCKER", "error_message": "old upload error",
                "failed_step": "ozon_upload", "task_authorized": True,
                "api_write_count": 0, "ozon": {"upload_status": "failed", "errors": ["old"]},
            })
            marked = mark_manual_upload_ready(product_dir, status)
            self.assertEqual(marked["status"], "WAITING_MANUAL_REVIEW")
            self.assertEqual(marked["current_step"], "final_upload_check")
            self.assertEqual(marked["error_code"], "unknown")
            self.assertEqual(marked["error_message"], "unknown")
            self.assertEqual(marked["failed_step"], "unknown")
            self.assertFalse(marked["task_authorized"])
            self.assertEqual(marked["ozon"]["upload_status"], "not_started")
            self.assertEqual(marked["ozon"]["errors"], [])
            self.assertIn("ozon_upload", marked["pending_steps"])

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
                "status": "FAILED_HARD_BLOCKER", "progress": 42,
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
