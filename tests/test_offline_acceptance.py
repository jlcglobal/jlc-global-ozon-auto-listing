import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANUAL_INPUT = ROOT / "test-data/manual-input/P900002"
MANUAL_OUTPUT = ROOT / "test-data/manual-output/P900002"


class OfflineAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = subprocess.run(
            [sys.executable, "scripts/offline_acceptance.py"],
            cwd=ROOT, text=True, capture_output=True, check=True,
        )
        cls.result = json.loads(result.stdout)

    def test_fixture_is_physical_manual_test_only(self):
        self.assertEqual(self.result["test_identity"], "manual_test")
        self.assertFalse((ROOT / "products/P900002").exists())
        source = json.loads((MANUAL_INPUT / "source.json").read_text(encoding="utf-8"))
        self.assertEqual(source["source_kind"], "manual_test")
        self.assertTrue(source["source_path"].startswith("test-data/manual-input/P900002/"))

    def test_only_three_canonical_product_references(self):
        self.assertEqual(
            [item["path"] for item in self.result["sku_images"]],
            [
                "test-data/manual-input/P900002/sku-images/sku-3l.png",
                "test-data/manual-input/P900002/sku-images/sku-5l.png",
                "test-data/manual-input/P900002/sku-images/sku-6l.png",
            ],
        )
        self.assertEqual(self.result["user_provided_main_images"], 0)
        self.assertEqual(self.result["user_provided_detail_images"], 0)

    def test_old_generated_images_are_rejected_not_candidates(self):
        self.assertEqual(self.result["candidate_images"], 0)
        self.assertEqual(self.result["accepted_images"], 0)
        self.assertEqual(self.result["rejected_prior_outputs"], 11)
        self.assertEqual(
            self.result["quality_status"],
            "WAITING_FOR_REAL_CONNECTED_CODEX_PRODUCTION_TEST",
        )

    def test_fixture_tool_does_not_claim_production_or_call_ozon(self):
        self.assertEqual(self.result["production_stages_proven"], [])
        self.assertEqual(self.result["errors"], [])
        self.assertEqual(self.result["ozon_write_calls"], 0)
        self.assertEqual(self.result["ozon_read_calls"], 0)
        self.assertEqual(self.result["inventory_calls"], 0)


if __name__ == "__main__":
    unittest.main()
