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


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def make_product(root: Path, number: int, sku_count: int) -> Path:
    product_dir = root / "products" / f"P{number:06d}"
    write_json(product_dir / "input/source.json", {"skus": [{"sku_id": str(index)} for index in range(sku_count)]})
    write_json(
        product_dir / "status.json",
        {
            "status": "COLLECTED",
            "completed_steps": ["collect_source"],
            "pending_steps": [],
            "history": [],
        },
    )
    return product_dir


class BatchPipelineLimitsTest(unittest.TestCase):
    def test_feasibility_phase_completes_before_content_and_images(self):
        self.assertEqual(PHASE_A_STEPS, [
            "validate_source", "product_analysis", "category_match", "variant_rules",
            "measurements", "offer_exists_check", "upload_feasibility",
        ])
        self.assertEqual(PHASE_B_STEPS[0], "product_positioning")
        self.assertGreater(PHASE_B_STEPS.index("image_generation"), 0)
        self.assertNotIn("final_upload_check", PHASE_B_STEPS)
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
                    "product_positioning", "russian_copy", "style_selector", "image_plan",
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


if __name__ == "__main__":
    unittest.main()
