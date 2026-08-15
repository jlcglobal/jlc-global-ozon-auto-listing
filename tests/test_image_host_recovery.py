import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.run_batch as runner


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class ImageHostRecoveryTest(unittest.TestCase):
    def make_product(self, root: Path) -> Path:
        product = root / "products/P000101"
        write_json(product / "output/image-plan.json", {
            "generator_contract": {"product_pixel_lock_required": False},
            "main_images": [
                {"slot": "main-001", "output_path": "products/P000101/output/generated-images/main.png", "status": "pending", "failure_reason": "unknown"},
            ],
            "detail_images": [
                {"slot": "detail-001", "output_path": "products/P000101/output/generated-images/detail.png", "status": "pending", "failure_reason": "unknown"},
            ],
            "disclaimer_images": [],
        })
        write_json(product / "status.json", {
            "product_id": "P000101",
            "status": "PROCESSING",
            "current_step": "image_generation",
            "next_action": "image_generation",
            "completed_steps": ["collect_source", *runner.PIPELINE_STEPS[:runner.PIPELINE_STEPS.index("image_generation")]],
            "pending_steps": runner.PIPELINE_STEPS[runner.PIPELINE_STEPS.index("image_generation"):],
            "retry_count_by_step": {},
            "history": [],
            "steps": [],
            "warnings": [],
        })
        completed = product / "output/generated-images/main.png"
        completed.parent.mkdir(parents=True, exist_ok=True)
        completed.write_bytes(b"finished")
        return product

    def test_interruption_preserves_finished_slot_and_retries_only_missing_slot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = self.make_product(root)
            with patch.object(runner, "ROOT", root), patch.object(runner, "notify_mac"):
                result = runner.recover_interrupted_image_generation(
                    product, {"step_retry_limit": 1},
                    "生图连续5分钟没有新增图片", "image_generation_stalled",
                )
            self.assertEqual(result["outcome"], "retry")
            self.assertEqual(result["failed_slots"], ["detail-001"])
            plan = json.loads((product / "output/image-plan.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["main_images"][0]["status"], "generated")
            self.assertEqual(plan["detail_images"][0]["status"], "needs_review")
            request = json.loads((product / "output/image-regeneration-request.json").read_text(encoding="utf-8"))
            self.assertEqual(request["failed_slots"], ["detail-001"])
            self.assertTrue(request["preserve_passed_images"])

    def test_second_interruption_requeues_same_slot_without_attention(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = self.make_product(root)
            with patch.object(runner, "ROOT", root), patch.object(runner, "notify_mac") as notify:
                runner.recover_interrupted_image_generation(
                    product, {"step_retry_limit": 1}, "第一次卡住", "image_generation_stalled",
                )
                result = runner.recover_interrupted_image_generation(
                    product, {"step_retry_limit": 1}, "自动修复后仍卡住", "image_generation_stalled",
                )
            self.assertEqual(result["outcome"], "retry")
            status = json.loads((product / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["status"], "PROCESSING")
            self.assertEqual(status["host_recovery_state"], "recovering")
            self.assertEqual(status["image_slot_retry_count_by_slot"]["detail-001"], 2)
            notify.assert_not_called()

    def test_second_host_window_continues_when_new_images_were_completed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = self.make_product(root)
            with patch.object(runner, "ROOT", root), patch.object(runner, "notify_mac") as notify:
                first = runner.recover_interrupted_image_generation(
                    product, {"step_retry_limit": 1}, "第一次达到窗口", "image_generation_timeout",
                )
                self.assertEqual(first["failed_slots"], ["detail-001"])
                detail = product / "output/generated-images/detail.png"
                detail.write_bytes(b"finished detail")
                second = runner.recover_interrupted_image_generation(
                    product, {"step_retry_limit": 1}, "第二次达到窗口", "image_generation_timeout",
                )
            self.assertEqual(second["outcome"], "retry")
            self.assertEqual(second["failed_slots"], [])
            status = json.loads((product / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["retry_count_by_step"]["image_generation"], 0)
            self.assertEqual(status["image_slot_retry_count_by_slot"]["detail-001"], 1)
            self.assertNotEqual(status["status"], "NEEDS_ATTENTION")
            notify.assert_not_called()

    def test_service_waits_are_visible_without_consuming_image_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = self.make_product(root)
            with patch.object(runner, "ROOT", root):
                runner.update_image_plan_from_results(product, [{
                    "slot": "detail-001",
                    "status": "service_unavailable",
                    "attempt": 1,
                }])
            status = json.loads((product / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["image_slot_retry_count_by_slot"]["detail-001"], 0)
            self.assertEqual(status["image_slot_service_wait_count_by_slot"]["detail-001"], 1)

    def test_registered_image_worker_is_stopped_after_stall_window(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = root / "products/P000101"
            write_json(product / "status.json", {
                "product_id": "P000101", "status": "PROCESSING",
                "current_step": "image_generation", "next_action": "image_generation",
            })
            log_path = product / "logs/full-pipeline.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with patch.object(runner, "ROOT", root), log_path.open("a", encoding="utf-8") as output:
                with self.assertRaises(runner.ImageGenerationStalled):
                    runner.run_registered_process(
                        ["/bin/sleep", "5"], product, output,
                        timeout_seconds=5, completion_poll_seconds=0.05, stall_seconds=1,
                    )
            self.assertFalse((root / "logs/product-workers/P000101.json").exists())

    def test_explicit_new_batch_clears_stale_host_attention_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = self.make_product(root)
            status_path = product / "status.json"
            status = json.loads(status_path.read_text(encoding="utf-8"))
            status.update({
                "status": "NEEDS_ATTENTION",
                "host_recovery_state": "needs_attention",
                "host_recovery_reason": "old timeout",
            })
            write_json(status_path, status)
            write_json(product / "input/source.json", {
                "product_id": "P000101",
                "skus": [{"sku_id": "sku-1", "purchase_price": 10}],
            })
            runner.queue_product(product, "B-NEW")
            queued = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(queued["host_recovery_state"], "normal")
            self.assertEqual(queued["host_recovery_reason"], "unknown")


if __name__ == "__main__":
    unittest.main()
