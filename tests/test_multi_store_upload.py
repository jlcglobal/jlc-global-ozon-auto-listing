import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.multi_store_upload import default_runner, execute_selected_stores, refresh_pending_stores
from scripts.store_publications import load_publications, select_stores


def write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def make_product(root: Path) -> Path:
    product = root / "products/P000001"
    write(product / "input/source.json", {
        "product_id": "P000001", "title_cn": "测试商品",
        "skus": [{"sku_id": "sku-a", "sku_name": "白色", "purchase_price": 10}],
    })
    write(product / "status.json", {
        "product_id": "P000001", "status": "OZON_READY", "completed_steps": ["collect_source", "field_completion"],
        "pending_steps": ["ozon_upload"], "next_action": "ozon_upload", "steps": [], "history": [],
        "task_authorized": True, "api_write_count": 0, "ozon": {},
    })
    write(product / "output/pricing-result.json", {
        "sku_pricing": [{"sku_id": "sku-a", "selling_price_cny": 50, "selling_price_rub": 600}],
    })
    write(product / "output/ozon-upload-config.json", {
        "shop_name": "default", "sku_prices": [{"source_sku_id": "sku-a", "price": "50.00"}],
    })
    return product


class MultiStoreUploadTest(unittest.TestCase):
    def test_default_runner_surfaces_prewrite_failure_from_store_log(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            isolated = root / "runtime/products/P000001"
            write(isolated / "status.json", {
                "status": "OZON_READY", "api_write_count": 0,
                "error_message": "unknown", "ozon": {},
            })

            def failed_run(*_args, **kwargs):
                kwargs["stdout"].write("FAILED\n- Persistent image channel did not become ready within 60 seconds\n")
                kwargs["stdout"].flush()
                return type("Completed", (), {"returncode": 2})()

            with patch("scripts.multi_store_upload.subprocess.run", side_effect=failed_run):
                outcome = default_runner(root, isolated, "store-a")
            self.assertEqual(outcome["returncode"], 2)
            self.assertEqual(
                outcome["status"]["error_message"],
                "Persistent image channel did not become ready within 60 seconds",
            )

    def test_store_results_are_isolated_and_pending_store_is_never_resent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = make_product(root)
            select_stores(product, ["store-a", "store-b"], ["store-a", "store-b"])
            calls = []

            def first_runner(_root, isolated, store_id):
                calls.append(store_id)
                if store_id == "store-a":
                    status = {"status": "PENDING_REMOTE", "api_write_count": 1, "ozon": {}}
                    result = {"task_id": "task-a", "action": "create", "items": [{"source_sku_id": "sku-a", "offer_id": "offer-a"}]}
                    idempotency = {"payload_hash": "hash-a"}
                else:
                    status = {"status": "FAILED_HARD_BLOCKER", "api_write_count": 0, "error_message": "definite pre-write failure", "ozon": {}}
                    result = {}
                    idempotency = {}
                write(isolated / "status.json", status)
                write(isolated / "output/ozon-result.json", result)
                return {"returncode": 0 if store_id == "store-a" else 1, "status": status, "result": result, "idempotency": idempotency}

            result = execute_selected_stores(root, product, runner=first_runner)
            self.assertEqual(calls, ["store-a", "store-b"])
            self.assertEqual(result["inventory_api_calls"], 0)
            stores = load_publications(product)["stores"]
            self.assertEqual(stores["store-a"]["status"], "PENDING_REMOTE")
            self.assertEqual(stores["store-a"]["sku_publications"][0]["task_id"], "task-a")
            self.assertEqual(stores["store-a"]["sku_publications"][0]["payload_hash"], "hash-a")
            self.assertEqual(stores["store-b"]["status"], "FAILED")

            def recovery_runner(_root, isolated, store_id):
                self.assertEqual(store_id, "store-a")
                status = {"status": "UPLOADED", "api_write_count": 1, "ozon": {}}
                result = {"task_id": "task-a", "status": "created", "items": [{"source_sku_id": "sku-a", "offer_id": "offer-a", "product_id": "product-a"}]}
                write(isolated / "status.json", status)
                write(isolated / "output/ozon-result.json", result)
                return {"returncode": 0, "status": status, "result": result}

            refreshed = refresh_pending_stores(root, product, runner=recovery_runner)
            self.assertEqual(refreshed["write_api_calls"], 0)
            self.assertEqual(refreshed["inventory_api_calls"], 0)
            self.assertEqual(load_publications(product)["stores"]["store-a"]["status"], "SUCCESS")

            retry_calls = []

            def retry_runner(_root, isolated, store_id):
                retry_calls.append(store_id)
                status = {"status": "UPLOADED", "api_write_count": 1, "ozon": {}}
                result = {"task_id": "task-b", "action": "create", "items": [{"source_sku_id": "sku-a", "offer_id": "offer-b", "product_id": "product-b"}]}
                write(isolated / "status.json", status)
                write(isolated / "output/ozon-result.json", result)
                return {"returncode": 0, "status": status, "result": result}

            execute_selected_stores(root, product, only_store_ids=["store-b"], runner=retry_runner)
            self.assertEqual(retry_calls, ["store-b"])
            stores = load_publications(product)["stores"]
            self.assertEqual(stores["store-b"]["status"], "SUCCESS")
            self.assertEqual(stores["store-b"]["sku_publications"][0]["ozon_product_id"], "product-b")
            execute_selected_stores(root, product, only_store_ids=["store-a"], runner=retry_runner)
            self.assertEqual(retry_calls, ["store-b"])

    def test_store_specific_cny_price_is_written_only_to_isolated_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = make_product(root)
            select_stores(
                product, ["store-a"], ["store-a"],
                {"store-a": {"sku_prices_cny": {"sku-a": 66}, "sku_prices": {"sku-a": 790}}},
            )
            seen = {}

            def runner(_root, isolated, store_id):
                seen.update(json.loads((isolated / "output/ozon-upload-config.json").read_text()))
                return {"returncode": 1, "status": {"status": "FAILED_HARD_BLOCKER", "api_write_count": 0}, "result": {}}

            execute_selected_stores(root, product, runner=runner)
            self.assertEqual(seen["shop_name"], "store-a")
            self.assertEqual(seen["sku_prices"][0]["price"], "66.00")
            master = json.loads((product / "output/ozon-upload-config.json").read_text())
            self.assertEqual(master["shop_name"], "default")
            self.assertEqual(master["sku_prices"][0]["price"], "50.00")


if __name__ == "__main__":
    unittest.main()
