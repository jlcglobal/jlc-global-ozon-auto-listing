import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.image_slot_scheduler import pending_slots
from scripts.ozon_metadata_prewarm import prewarm_category_tree
from scripts.pipeline_observability import (
    shared_analysis_cache_restore,
    shared_analysis_cache_store,
    shared_analysis_input_hash,
)
from scripts.run_batch import BatchSafeStopRequested, complete_embedded_image_qc, run_registered_process


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class FakeOzonReadClient:
    def __init__(self):
        self.calls = 0

    def get_category_tree(self):
        self.calls += 1
        return {"result": [{"description_category_id": 1, "type_id": 2}]}


class PipelineSpeedOptimizationTests(unittest.TestCase):
    def test_safe_stop_interrupts_active_child_process_group(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            product = root / "products/P000001"
            product.mkdir(parents=True)
            (product / "status.json").write_text(
                json.dumps({"batch_id": "B-STOP", "current_step": "image_generation"}),
                encoding="utf-8",
            )
            worker = root / "worker.json"
            log = root / "child.log"
            with (
                patch("scripts.run_batch.product_worker_path", return_value=worker),
                patch("scripts.run_batch.safe_stop_requested", return_value=True),
                log.open("w", encoding="utf-8") as output,
            ):
                with self.assertRaises(BatchSafeStopRequested):
                    run_registered_process(
                        [sys.executable, "-c", "import time; time.sleep(30)"],
                        product,
                        output,
                        30,
                        completion_poll_seconds=0.05,
                    )
            self.assertFalse(worker.exists())

    def test_image_slots_schedule_all_mains_before_details(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory) / "products/P100001"
            items = []
            for index in range(8):
                output = Path(directory) / f"image-{index}.png"
                items.append({
                    "slot": f"slot-{index}", "image_type": "main" if index == 0 else "detail",
                    "status": "planned", "output_path": str(output),
                })
            write_json(product / "output/image-plan.json", {
                "main_images": items[:1], "detail_images": items[1:7],
                "disclaimer_images": items[7:],
            })
            result = pending_slots(product, 3)
            self.assertEqual(result["concurrency"], 3)
            self.assertTrue(result["main_images_first"])
            self.assertEqual([len(wave) for wave in result["waves"]], [1, 3, 3, 1])
            self.assertEqual(result["waves"][0][0]["image_type"], "main")

    def test_completed_retry_request_does_not_hide_other_missing_slots(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory) / "products/P100001"
            completed_output = Path(directory) / "completed.png"
            completed_output.write_bytes(b"image")
            write_json(product / "output/product-lock/retry-slot.json", {"audit": {"status": "pass"}})
            write_json(product / "output/image-regeneration-request.json", {"failed_slots": ["retry-slot"]})
            write_json(product / "output/image-plan.json", {
                "main_images": [{
                    "slot": "retry-slot", "image_type": "main", "status": "planned",
                    "output_path": str(completed_output),
                }],
                "detail_images": [{
                    "slot": "missing-slot", "image_type": "detail", "status": "planned",
                    "output_path": str(Path(directory) / "missing.png"),
                }],
                "disclaimer_images": [],
            })
            result = pending_slots(product, 3)
            self.assertEqual(result["pending_slot_count"], 1)
            self.assertEqual(result["waves"][0][0]["slot"], "missing-slot")

    def test_shared_analysis_cache_uses_source_and_image_fingerprints(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "products/P100001"
            second = root / "products/P100002"
            source = {
                "source_url": "https://detail.1688.com/offer/1.html",
                "title_cn": "测试商品",
                "product_attributes": [{"name_cn": "材质", "value_cn": "硅胶"}],
                "skus": [{"sku_id": "sku-1", "sku_name": "绿色", "option_values": []}],
            }
            for product in (first, second):
                write_json(product / "input/source.json", source)
                image = product / "input/main-images/main.jpg"
                image.parent.mkdir(parents=True, exist_ok=True)
                image.write_bytes(b"same-real-image")
            write_json(first / "output/product-analysis.json", {
                "product_id": "P100001",
                "source_refs": ["products/P100001/input/source.json"],
                "facts": {"title_cn": "测试商品"},
            })
            with patch("scripts.pipeline_observability.ROOT", root):
                first_key = shared_analysis_input_hash(first)
                second_key = shared_analysis_input_hash(second)
                self.assertEqual(first_key, second_key)
                shared_analysis_cache_store(first, first_key)
                self.assertTrue(shared_analysis_cache_restore(second, second_key))
            restored = json.loads((second / "output/product-analysis.json").read_text())
            self.assertEqual(restored["product_id"], "P100002")
            self.assertEqual(restored["source_refs"], ["products/P100002/input/source.json"])

    def test_ozon_prewarm_uses_read_only_tree_and_reuses_fresh_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = FakeOzonReadClient()
            settings = {"shop_name": "test", "ozon_metadata_cache_hours": 24}
            first = prewarm_category_tree(settings, root=root, client=client)
            second = prewarm_category_tree(settings, root=root, client=client)
            self.assertEqual(first["status"], "prewarmed")
            self.assertEqual(second["status"], "cache_fresh")
            self.assertEqual(client.calls, 1)

    def test_embedded_qc_completes_without_second_codex_task(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory) / "products/P100001"
            write_json(product / "output/image-qc-report.json", {"decision": "pass"})
            write_json(product / "output/image-regeneration-request.json", {"failed_slots": ["detail-001"]})
            write_json(product / "input/source.json", {"product_id": "P100001"})
            write_json(product / "input/raw-snapshot.json", {"product_id": "P100001"})
            with (
                patch("scripts.run_batch.run_local_step", return_value=True) as local_step,
                patch("scripts.run_batch.complete_step") as checkpoint,
                patch("scripts.run_batch.cache_store") as cache,
            ):
                result = complete_embedded_image_qc(
                    product, {"merge_image_generation_and_qc": True}, product / "log.txt"
            )
            self.assertTrue(result)
            self.assertFalse((product / "output/image-regeneration-request.json").exists())
            local_step.assert_called_once()
            checkpoint.assert_called_once_with(product, "image_qc")
            cache.assert_called_once()


if __name__ == "__main__":
    unittest.main()
