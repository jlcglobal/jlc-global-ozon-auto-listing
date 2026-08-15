import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "ozon-uploader"))
sys.path.insert(0, str(ROOT / "ozon-adapter"))
from product_deletion import mark_deletion_requested, purge_local_product  # noqa: E402
from ozon_uploader import service as uploader_service  # noqa: E402

APP_PATH = ROOT / "collector/local-ingest/app.py"
SPEC = importlib.util.spec_from_file_location("deletion_workbench_app", APP_PATH)
workbench = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(workbench)


def write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def make_product(root: Path, product_id: str, status: str = "COLLECTED", submitted: bool = False) -> Path:
    product = root / "products" / product_id
    write(product / "input/source.json", {
        "product_id": product_id, "title_cn": "待删除测试商品",
        "source_url": f"https://detail.1688.com/offer/{product_id[1:]}.html",
        "skus": [{"sku_id": "sku-1"}, {"sku_id": "sku-2"}],
        "main_images": [{"local_path": f"products/{product_id}/input/main-images/main.jpg"}],
    })
    (product / "input/main-images").mkdir(parents=True, exist_ok=True)
    (product / "input/main-images/main.jpg").write_bytes(b"image")
    write(product / "output/workbench-draft.json", {"selected_shop": "shop-a", "version": 2})
    write(product / "output/workbench-versions.json", {"product_id": product_id, "versions": [{"version": 1}]})
    write(product / "output/image-qc-report.json", {"decision": "reject", "issues": []})
    write(product / "output/ozon-upload-payload.json", {"product_id": product_id, "items": []})
    write(product / "status.json", {
        "product_id": product_id, "status": status, "current_step": "image_generation",
        "batch_id": "B-TEST", "api_write_count": 1 if submitted else 0,
        "ozon": {
            "task_id": "123" if submitted else "unknown",
            "offer_id": f"{product_id}-sku-1" if submitted else "unknown",
            "product_id": "456" if submitted else "unknown",
            "shop_name": "shop-b" if submitted else "unknown",
        },
    })
    if submitted:
        write(product / "output/ozon-result.json", {
            "task_id": 123, "shop_name": "shop-b",
            "items": [{"offer_id": f"{product_id}-sku-1", "product_id": 456}],
        })
    return product


class FakeRequest:
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


class PermanentProductDeletionTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def delete(self, product_id="P000101"):
        return purge_local_product(self.root, product_id)

    def test_01_delete_unprocessed_product(self):
        make_product(self.root, "P000101")
        result = self.delete()
        self.assertEqual(result["status"], "deleted")
        self.assertFalse((self.root / "products/P000101").exists())

    def test_02_delete_processing_product_stops_only_its_worker(self):
        make_product(self.root, "P000101", "PROCESSING")
        make_product(self.root, "P000102", "PROCESSING")
        write(self.root / "logs/product-workers/P000101.json", {"pid": 111})
        write(self.root / "logs/product-workers/P000102.json", {"pid": 222})
        with patch("product_deletion._terminate_pid", return_value=True) as terminate:
            result = self.delete()
        terminate.assert_called_once_with(111)
        self.assertEqual(result["stopped_product_pids"], [111])
        self.assertTrue((self.root / "products/P000102").is_dir())

    def test_03_delete_during_image_generation_stops_image_worker(self):
        product = make_product(self.root, "P000101", "PROCESSING")
        write(product / "output/image-channel-state.json", {"status": "running", "worker_pid": 333})
        with patch("product_deletion._terminate_pid", return_value=True):
            result = self.delete()
        self.assertIn(333, result["stopped_product_pids"])

    def test_04_delete_one_product_from_batch_keeps_other_product(self):
        make_product(self.root, "P000101", "QUEUED")
        make_product(self.root, "P000102", "QUEUED")
        write(self.root / "batches/B-TEST/batch.json", {
            "batch_id": "B-TEST", "product_count": 2,
            "products": [{"product_id": "P000101", "status": "QUEUED"}, {"product_id": "P000102", "status": "QUEUED"}],
        })
        self.delete()
        batch = json.loads((self.root / "batches/B-TEST/batch.json").read_text())
        self.assertEqual([item["product_id"] for item in batch["products"]], ["P000102"])
        self.assertEqual(batch["product_count"], 1)

    def test_05_delete_pending_remote_removes_remote_queue_only_locally(self):
        make_product(self.root, "P000101", "PENDING_REMOTE", submitted=True)
        write(self.root / "remote-pending-queue.json", {"items": [{"product_id": "P000101", "task_id": "123"}]})
        result = self.delete()
        self.assertEqual(json.loads((self.root / "remote-pending-queue.json").read_text())["items"], [])
        self.assertEqual(result["ozon_delete_api_calls"], 0)

    def test_06_delete_product_with_remote_product_id(self):
        make_product(self.root, "P000101", "IMPORTED", submitted=True)
        preview_root = self.root / "products/P000101"
        with patch.object(workbench, "ROOT", self.root), patch.object(workbench, "PRODUCTS_DIR", self.root / "products"):
            preview = workbench.workbench_delete_preview("P000101")
        self.assertTrue(preview["submitted_to_ozon"])
        self.assertEqual(preview["remote_ids"]["product_ids"], ["456"])
        self.assertTrue(preview_root.is_dir())

    def test_07_multi_shop_preview_and_delete_do_not_remove_shop_registry(self):
        make_product(self.root, "P000101", "PENDING_REMOTE", submitted=True)
        write(self.root / "ozon-adapter/shops.json", {"shops": [{"name": "shop-a"}, {"name": "shop-b"}]})
        with patch.object(workbench, "ROOT", self.root), patch.object(workbench, "PRODUCTS_DIR", self.root / "products"):
            preview = workbench.workbench_delete_preview("P000101")
        self.assertEqual(preview["associated_shops"], ["shop-a", "shop-b"])
        self.delete()
        self.assertTrue((self.root / "ozon-adapter/shops.json").is_file())

    def test_08_stale_async_result_cannot_recreate_deleted_product(self):
        product = make_product(self.root, "P000101", "PROCESSING")
        self.delete()
        self.assertFalse(product.exists())
        with self.assertRaises(uploader_service.UploadGateError):
            uploader_service.write_json_atomic(product / "output/stale-result.json", {"product_id": "P000101"})
        self.assertFalse(product.exists())

    def test_09_deleting_one_product_does_not_change_another(self):
        make_product(self.root, "P000101")
        other = make_product(self.root, "P000102")
        before = (other / "status.json").read_bytes()
        self.delete()
        self.assertEqual((other / "status.json").read_bytes(), before)

    def test_10_images_cache_queues_indexes_and_old_archive_have_no_residue(self):
        make_product(self.root, "P000101")
        write(self.root / "image-channel-queue.json", {"items": [{"product_id": "P000101"}]})
        write(self.root / "cache/image-recognition/cache.json", {"product_id": "P000101", "result": {}})
        old = self.root / "logs/deleted-products/20260712-P000101"
        write(old / "status.json", {"product_id": "P000101"})
        (self.root / "logs/batch-runner.log").parent.mkdir(parents=True, exist_ok=True)
        (self.root / "logs/batch-runner.log").write_text("P000101 old log\nP000102 keep\n", encoding="utf-8")
        self.delete()
        self.assertFalse((self.root / "cache/image-recognition/cache.json").exists())
        self.assertFalse(old.exists())
        self.assertNotIn("P000101", (self.root / "logs/batch-runner.log").read_text())

    def test_11_delete_never_calls_ozon_write_or_delete_api(self):
        make_product(self.root, "P000101", "PENDING_REMOTE", submitted=True)
        result = self.delete()
        self.assertEqual(result["ozon_write_api_calls"], 0)
        self.assertEqual(result["ozon_delete_api_calls"], 0)

    async def test_12_delete_never_calls_inventory_api_and_requires_confirmation(self):
        make_product(self.root, "P000101")
        with patch.object(workbench, "ROOT", self.root), patch.object(workbench, "PRODUCTS_DIR", self.root / "products"):
            with self.assertRaises(workbench.HTTPException) as raised:
                await workbench.permanently_delete_workbench_product("P000101", FakeRequest({}))
            result = await workbench.permanently_delete_workbench_product(
                "P000101", FakeRequest({"permanent": True, "confirm_product_id": "P000101"})
            )
        self.assertEqual(raised.exception.status_code, 422)
        self.assertEqual(result["inventory_api_calls"], 0)

    def test_13_new_collection_never_reuses_a_deleted_product_id(self):
        make_product(self.root, "P000101")
        mark_deletion_requested(self.root, "P000102")
        with patch.object(workbench, "ROOT", self.root), patch.object(workbench, "PRODUCTS_DIR", self.root / "products"):
            product_id = workbench.create_product_id()
        self.assertEqual(product_id, "P000103")


if __name__ == "__main__":
    unittest.main()
