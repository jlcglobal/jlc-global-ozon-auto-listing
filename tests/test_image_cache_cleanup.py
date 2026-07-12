import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.image_cache_cleanup import cleanup_images


NOW = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)


def write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def image(path: Path, size: int = 16) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


class ImageCacheCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "products").mkdir()
        self.settings = {
            "image_cleanup_enabled": True,
            "image_cleanup_interval_hours": 24,
            "image_retention_days": 10,
        }

    def tearDown(self):
        self.temp.cleanup()

    def product(self, product_id: str, status: str, age_days: int, media=False) -> Path:
        product = self.root / "products" / product_id
        at = (NOW - timedelta(days=age_days)).isoformat()
        write(product / "input/source.json", {"product_id": product_id, "captured_at": at})
        write(product / "status.json", {
            "status": status, "last_run_at": at,
            "completed_steps": ["image_generation", "image_qc", "marketplace_content"],
            "pending_steps": [],
        })
        image(product / "input/main-images/main.jpg", 10)
        image(product / "input/sku-images/sku.jpg", 11)
        image(product / "output/generated-images/main.png", 12)
        image(product / "output/generated-backgrounds/main.png", 13)
        write(product / "output/product-analysis.json", {"keep": True})
        if media:
            write(product / "output/ozon-image-transfer.json", {
                "status": "MEDIA_CONFIRMED", "checked_at": at,
            })
        return product

    def test_unuploaded_images_are_removed_after_ten_days_but_records_remain(self):
        product = self.product("P000001", "COLLECTED", 11)
        result = cleanup_images(self.root, self.settings, current_time=NOW, force=True)
        self.assertEqual(result["deleted_product_count"], 1)
        self.assertFalse((product / "input/main-images").exists())
        self.assertFalse((product / "output/generated-images").exists())
        self.assertTrue((product / "input/source.json").is_file())
        self.assertTrue((product / "output/product-analysis.json").is_file())

    def test_images_younger_than_ten_days_are_kept(self):
        product = self.product("P000002", "COLLECTED", 9)
        cleanup_images(self.root, self.settings, current_time=NOW, force=True)
        self.assertTrue((product / "input/main-images/main.jpg").is_file())

    def test_remote_pending_images_are_always_protected(self):
        product = self.product("P000003", "PENDING_REMOTE", 40)
        cleanup_images(self.root, self.settings, current_time=NOW, force=True)
        self.assertTrue((product / "output/generated-images/main.png").is_file())

    def test_active_image_channel_protects_product(self):
        product = self.product("P000004", "UPLOADED", 40, media=True)
        write(self.root / "image-channel-queue.json", {
            "items": [{"product_id": "P000004", "status": "WAITING_OZON_CDN"}],
        })
        cleanup_images(self.root, self.settings, current_time=NOW, force=True)
        self.assertTrue((product / "output/generated-images/main.png").is_file())

    def test_success_requires_media_confirmation_before_countdown(self):
        product = self.product("P000005", "UPLOADED", 40, media=False)
        cleanup_images(self.root, self.settings, current_time=NOW, force=True)
        self.assertTrue((product / "output/generated-images/main.png").is_file())

    def test_success_is_deleted_ten_days_after_media_confirmation(self):
        product = self.product("P000006", "UPLOADED", 11, media=True)
        cleanup_images(self.root, self.settings, current_time=NOW, force=True)
        self.assertFalse((product / "output/generated-images").exists())
        self.assertTrue((product / "output/image-cleanup-report.json").is_file())

    def test_failed_product_retry_is_invalidated_after_cleanup(self):
        product = self.product("P000007", "FAILED_HARD_BLOCKER", 11)
        write(product / "output/pipeline-cache.json", {
            "steps": {"image_generation": {}, "image_qc": {}, "product_analysis": {}},
        })
        cleanup_images(self.root, self.settings, current_time=NOW, force=True)
        status = json.loads((product / "status.json").read_text())
        cache = json.loads((product / "output/pipeline-cache.json").read_text())
        self.assertNotIn("image_generation", status["completed_steps"])
        self.assertTrue(status["images_require_regeneration_on_retry"])
        self.assertNotIn("image_generation", cache["steps"])
        self.assertIn("product_analysis", cache["steps"])

    def test_cleanup_is_run_at_most_once_per_interval(self):
        self.product("P000008", "COLLECTED", 11)
        cleanup_images(self.root, self.settings, current_time=NOW, force=True)
        result = cleanup_images(self.root, self.settings, current_time=NOW + timedelta(hours=1))
        self.assertEqual(result["status"], "not_due")


if __name__ == "__main__":
    unittest.main()
