import json
import tempfile
import unittest
from pathlib import Path

from scripts.pipeline_observability import cache_hit, cache_store, input_hash
from scripts.run_batch import batch_performance, product_step_timeout


ROOT = Path(__file__).resolve().parents[1]


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class PipelineOptimizationTest(unittest.TestCase):
    def test_cache_rejects_tampered_output(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory) / "P100001"
            write_json(product / "input/source.json", {"product_id": "P100001"})
            write_json(product / "input/raw-snapshot.json", {"product_id": "P100001"})
            write_json(product / "output/product-analysis.json", {"value": 1})
            key = input_hash(product, "product_analysis")
            cache_store(product, "product_analysis", key)
            self.assertTrue(cache_hit(product, "product_analysis", key))
            write_json(product / "output/product-analysis.json", {"value": 2})
            self.assertFalse(cache_hit(product, "product_analysis", key))

    def test_batch_performance_uses_only_current_batch_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_json(root / "products/P100001/output/performance-report.json", {
                "total_seconds": 103,
                "steps": [
                    {"batch_id": "B-OLD", "step": "image_generation", "duration_seconds": 100, "cache_hit": False},
                    {"batch_id": "B-NEW", "step": "product_analysis", "duration_seconds": 3, "cache_hit": False},
                ],
            })
            report = batch_performance([{"product_id": "P100001"}], root, "B-NEW")
            self.assertEqual(report["average_product_seconds"], 3)
            self.assertEqual(report["slowest_step"], "product_analysis")

    def test_image_generation_window_is_capped_and_resumable(self):
        settings = json.loads((ROOT / "config/pipeline-settings.json").read_text())
        timeout = product_step_timeout(ROOT / "products/P000013", settings, "image_generation")
        self.assertLessEqual(timeout, settings["image_generation_run_max_seconds"])
        self.assertEqual(settings["codex_concurrency"], 2)

    def test_marketplace_content_does_not_recalculate_pricing(self):
        source = (ROOT / "scripts/marketplace_content_generator.py").read_text(encoding="utf-8")
        self.assertNotIn("build_pricing_package", source)
        self.assertIn('"pricing-result.json"', source)


if __name__ == "__main__":
    unittest.main()
