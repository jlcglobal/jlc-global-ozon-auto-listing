import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "collector/local-ingest/app.py"
SPEC = importlib.util.spec_from_file_location("batch_confirmation_app", APP_PATH)
workbench = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(workbench)

sys.path.insert(0, str(ROOT / "pricing-engine"))
from pricing_engine.service import _apply_manual_confirmation, _apply_manual_measurements  # noqa: E402


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class FakeRequest:
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload

    async def body(self):
        return json.dumps(self.payload).encode("utf-8")


def make_product(root: Path, product_id: str = "P000101") -> Path:
    product = root / "products" / product_id
    main_path = product / "input/main-images/main.jpg"
    detail_path = product / "input/detail-images/size.jpg"
    sku_path = product / "input/sku-images/sku.jpg"
    for path in (main_path, detail_path, sku_path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"image")
    write_json(product / "input/source.json", {
        "product_id": product_id,
        "title_cn": "圆形透明密封保鲜盒",
        "source_url": "https://detail.1688.com/offer/101.html",
        "captured_at": "2026-07-13T08:00:00+08:00",
        "product_attributes": [{"name_cn": "材质", "value_cn": "PP塑料"}],
        "main_images": [{"local_path": str(main_path.relative_to(root))}],
        "detail_images": [{"local_path": str(detail_path.relative_to(root))}],
        "skus": [{
            "sku_id": "sku-280", "sku_name": "280ml", "purchase_price": 8.5,
            "local_image_path": str(sku_path.relative_to(root)),
            "option_values": [{"value_cn": "280ml"}],
        }],
    })
    write_json(product / "input/category-selection.json", {
        "category_id": 10, "type_id": 20,
        "category_path_zh": ["家居", "厨房用品", "食品储存容器"],
        "category_path": ["Дом", "Кухня", "Контейнеры"],
        "rules_snapshot_hash": "rules-1",
        "rules_snapshot": {
            "attributes": [{"id": 1}, {"id": 2}, {"id": 3}],
            "required_attribute_ids": [1, 2], "aspect_attribute_ids": [3],
        },
    })
    write_json(product / "status.json", {
        "product_id": product_id, "status": "COLLECTED", "current_step": "collect_source",
        "progress": 0, "steps": [], "warnings": [], "api_write_count": 0, "ozon": {},
    })
    return product


class BatchConfirmationTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.product = make_product(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def patches(self):
        return (
            patch.object(workbench, "ROOT", self.root),
            patch.object(workbench, "PRODUCTS_DIR", self.root / "products"),
            patch.object(workbench, "validate_target_stores", return_value=["shop-a"]),
            patch.object(workbench, "connected_store_ids", return_value=["shop-a"]),
            patch.object(workbench, "select_stores"),
            patch.object(workbench, "materialize_active_experience"),
        )

    async def test_manual_batch_waits_for_one_time_confirmation(self):
        contexts = self.patches()
        with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4], contexts[5], \
             patch.object(workbench, "workbench_settings", return_value={"auto_mode_enabled": False}), \
             patch.object(workbench, "launch_or_enqueue_batch") as launch:
            result = await workbench.create_workbench_batch(FakeRequest({
                "product_ids": ["P000101"], "store_ids": ["shop-a"],
            }))
            batch = json.loads(workbench.batch_path(self.root, result["batch_id"]).read_text())
        self.assertEqual(result["status"], "awaiting_confirmation")
        self.assertEqual(batch["status"], "AWAITING_CONFIRMATION")
        launch.assert_not_called()

    def test_confirmation_prefers_sku_image_and_exposes_local_reference(self):
        batch = workbench.create_batch(self.root, ["P000101"], ["shop-a"], auto_upload=False)
        batch["status"] = "AWAITING_CONFIRMATION"
        write_json(workbench.batch_path(self.root, batch["batch_id"]), batch)
        with patch.object(workbench, "ROOT", self.root), patch.object(workbench, "PRODUCTS_DIR", self.root / "products"):
            result = workbench.get_workbench_batch_confirmation(batch["batch_id"])
            source_response = workbench.workbench_source_image("P000101", "sku", 0)
        item = result["products"][0]
        self.assertIn("/source-images/sku/0", item["thumbnail_url"])
        self.assertIn("/source-images/detail/0", item["reference_images"][0]["url"])
        self.assertEqual(Path(source_response.path), (self.product / "input/sku-images/sku.jpg").resolve())
        self.assertEqual(item["fields"]["material"]["value"], "PP塑料")

    async def test_confirmation_saves_supplement_then_launches(self):
        batch = workbench.create_batch(self.root, ["P000101"], ["shop-a"], auto_upload=False)
        batch["status"] = "AWAITING_CONFIRMATION"
        batch["products"][0]["status"] = "AWAITING_CONFIRMATION"
        write_json(workbench.batch_path(self.root, batch["batch_id"]), batch)
        payload = {"products": [{
            "product_id": "P000101",
            "fields": {
                "product_dimensions": {"length": 10.5, "width": 10.5, "height": 6.5},
                "product_weight_g": 100,
                "package_dimensions": {"length": 11.5, "width": 11.5, "height": 7.5},
                "package_weight_g": 140,
                "material": "PP塑料",
            },
            "sku_prices": {"sku-280": 9.2},
        }]}
        with patch.object(workbench, "ROOT", self.root), patch.object(workbench, "PRODUCTS_DIR", self.root / "products"), \
             patch.object(workbench, "final_snapshot"), \
             patch.object(workbench, "launch_or_enqueue_batch", return_value={"status": "started", "pid": 123, "queue_position": 0}):
            result = await workbench.confirm_workbench_batch(batch["batch_id"], FakeRequest(payload))
        saved = json.loads((self.product / "input/manual-confirmation.json").read_text())
        updated_batch = json.loads(workbench.batch_path(self.root, batch["batch_id"]).read_text())
        self.assertEqual(result["status"], "started")
        self.assertEqual(saved["provenance"], "estimated_human_approved")
        self.assertEqual(saved["sku_purchase_prices_cny"]["sku-280"], 9.2)
        self.assertEqual(updated_batch["status"], "QUEUED")
        self.assertEqual(result["inventory_api_calls"], 0)
        self.assertEqual(result["write_api_calls"], 0)

    async def test_unstarted_confirmation_batch_can_be_cancelled_without_deleting_product(self):
        batch = workbench.create_batch(self.root, ["P000101"], ["shop-a"], auto_upload=False)
        batch["status"] = "AWAITING_CONFIRMATION"
        write_json(workbench.batch_path(self.root, batch["batch_id"]), batch)
        with patch.object(workbench, "ROOT", self.root), patch.object(workbench, "PRODUCTS_DIR", self.root / "products"), \
             patch.object(workbench, "running_batch_pid", return_value=None):
            result = await workbench.control_batch(FakeRequest({
                "action": "cancel_confirmation", "batch_id": batch["batch_id"],
            }))
        saved = json.loads(workbench.batch_path(self.root, batch["batch_id"]).read_text())
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(saved["status"], "CANCELLED")
        self.assertTrue(self.product.is_dir())
        self.assertEqual(result["write_api_calls"], 0)
        self.assertEqual(result["inventory_api_calls"], 0)

    def test_pricing_uses_approved_estimates_without_overwriting_source(self):
        source = {"skus": [{"sku_id": "sku-280", "purchase_price": 8.5}]}
        confirmation = {
            "sku_purchase_prices_cny": {"sku-280": 9.2},
            "fields": {
                "product_dimensions": {"length": 10.5, "width": 10.5, "height": 6.5},
                "product_weight": {"value_g": 100},
                "package_dimensions": {"length": 11.5, "width": 11.5, "height": 7.5},
                "package_weight": {"value_g": 140},
            },
        }
        _apply_manual_confirmation(source, confirmation)
        cost = {"product_id": "P000101", "source_refs": [], "warnings": ["Product weight is estimated and is not a 1688 confirmed fact."]}
        _apply_manual_measurements(cost, confirmation)
        self.assertEqual(source["skus"][0]["purchase_price"], 9.2)
        self.assertTrue(cost["measurement_hierarchy"]["valid"])
        self.assertEqual(cost["package_weight"]["source_ref"], "input/manual-confirmation.json")
        self.assertTrue(cost["package_weight"]["estimated"])


if __name__ == "__main__":
    unittest.main()
