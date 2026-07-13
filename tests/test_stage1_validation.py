import copy
import tempfile
import unittest
from pathlib import Path

from scripts.validate_product import (
    ROOT,
    can_start_upload,
    load_json,
    validate_product,
    validate_status_integrity,
    validate_source_truthfulness
)


PRODUCT_DIR = ROOT / "products" / "P000001"


@unittest.skipUnless((ROOT / "products/P000001/input/source.json").is_file(), "optional runtime product fixture is not installed")
class Stage1ValidationTest(unittest.TestCase):
    def test_example_product_directory_is_valid(self):
        self.assertEqual(validate_product(PRODUCT_DIR), [])

    def test_upload_gate_blocks_collected_product(self):
        status = load_json(PRODUCT_DIR / "status.json")
        self.assertFalse(can_start_upload(status))

    def test_upload_gate_allows_user_started_batch_at_ozon_ready(self):
        status = load_json(PRODUCT_DIR / "status.json")
        status["status"] = "OZON_READY"
        status["current_step"] = "ozon_preflight"
        status["task_authorized"] = True
        status["history"] = []
        self.assertTrue(can_start_upload(status))
        self.assertEqual(validate_status_integrity(status, PRODUCT_DIR), [])

    def test_failed_status_requires_concrete_reason(self):
        status = load_json(PRODUCT_DIR / "status.json")
        status["status"] = "FAILED_HARD_BLOCKER"
        status["current_step"] = "image_plan"
        status["steps"].append(
            {
                "name": "image_plan",
                "status": "failed",
                "started_at": "2026-07-10T00:00:00+08:00",
                "finished_at": "2026-07-10T00:01:00+08:00",
                "retry_count": 0,
                "retryable": True,
                "error": None
            }
        )
        errors = validate_status_integrity(status, PRODUCT_DIR)
        self.assertTrue(any("requires concrete reason" in error for error in errors))

    def test_source_rejects_analysis_only_fields(self):
        source = load_json(PRODUCT_DIR / "input" / "source.json")
        source["inferences"] = [{"field": "material", "value": "plastic"}]
        errors = validate_source_truthfulness(source, PRODUCT_DIR)
        self.assertTrue(any("analysis-only keys" in error for error in errors))

    def test_schema_validation_reports_missing_required_file(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_product = Path(tmp_dir) / "P000002"
            temp_product.mkdir()
            errors = validate_product(temp_product)
        self.assertTrue(any("missing directory input" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
